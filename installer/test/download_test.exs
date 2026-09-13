defmodule Riff.Installer.DownloadTest do
  use ExUnit.Case, async: false
  alias Riff.Installer.{Download, TestFixture}

  setup do
    directory =
      Path.join(
        System.tmp_dir!(),
        "riff-installer-test-" <> Base.url_encode64(:crypto.strong_rand_bytes(12), padding: false)
      )

    File.mkdir_p!(directory)
    on_exit(fn -> File.rm_rf!(directory) end)
    %{directory: directory}
  end

  test "downloads, verifies, then reuses existing files without a request", %{
    directory: directory
  } do
    contents = :crypto.strong_rand_bytes(600_000)
    {server, url} = TestFixture.start!(contents: contents)
    on_exit(fn -> if Process.alive?(server), do: Supervisor.stop(server) end)
    asset = TestFixture.asset(contents, url)

    assert :downloaded ==
             Download.ensure(asset, directory, fn _, _ -> :ok end, allow_local_http: true)

    assert File.read!(Path.join(directory, asset.id)) == contents
    assert_received {:request, "/model.gguf", []}

    assert :reused ==
             Download.ensure(asset, directory, fn _, _ -> :ok end, allow_local_http: true)

    refute_received {:request, _, _}
  end

  test "resumes a saved prefix, checks Content-Range and hashes the entire result", %{
    directory: directory
  } do
    contents = :crypto.strong_rand_bytes(500_000)
    {server, url} = TestFixture.start!(contents: contents)
    on_exit(fn -> if Process.alive?(server), do: Supervisor.stop(server) end)
    asset = TestFixture.asset(contents, url, "sidecars/tokens.bin")
    partial = Download.safe_target!(directory, asset.id) <> ".part"
    File.write!(partial, binary_part(contents, 0, 125_000))

    assert :downloaded ==
             Download.ensure(asset, directory, fn _, _ -> :ok end, allow_local_http: true)

    assert_received {:request, "/sidecars/tokens.bin", ["bytes=125000-"]}
    assert File.read!(Path.join(directory, asset.id)) == contents
    refute File.exists?(partial)
  end

  test "follows HTTPS-style redirects without including redirect bodies", %{directory: directory} do
    contents = "model data"
    {server, url} = TestFixture.start!(contents: contents, mode: :redirect)
    on_exit(fn -> if Process.alive?(server), do: Supervisor.stop(server) end)
    asset = TestFixture.asset(contents, url)

    assert :downloaded ==
             Download.ensure(asset, directory, fn _, _ -> :ok end, allow_local_http: true)

    assert File.read!(Path.join(directory, asset.id)) == contents
    assert_received {:request, "/actual", []}
  end

  test "restarts when Range is ignored, preserving the old partial", %{directory: directory} do
    contents = "verified music model"
    {server, url} = TestFixture.start!(contents: contents, mode: :ignore_range)
    on_exit(fn -> if Process.alive?(server), do: Supervisor.stop(server) end)
    asset = TestFixture.asset(contents, url)
    File.write!(Path.join(directory, asset.id <> ".part"), "verified")

    assert :downloaded ==
             Download.ensure(asset, directory, fn _, _ -> :ok end, allow_local_http: true)

    assert File.read!(Path.join(directory, asset.id)) == contents
    assert [preserved] = Path.wildcard(Path.join(directory, "*.unverified.*"))
    assert File.read!(preserved) == "verified"
  end

  test "rejects wrong resumed ranges before appending", %{directory: directory} do
    contents = "verified music model"
    {server, url} = TestFixture.start!(contents: contents, mode: :bad_range)
    on_exit(fn -> if Process.alive?(server), do: Supervisor.stop(server) end)
    asset = TestFixture.asset(contents, url)
    partial = Path.join(directory, asset.id <> ".part")
    File.write!(partial, "verified")

    assert_raise Download.Error, ~r/wrong file range/, fn ->
      Download.ensure(asset, directory, fn _, _ -> :ok end, allow_local_http: true)
    end

    assert File.read!(partial) == "verified"
  end

  test "rejects checksum mismatch, preserves evidence, and can retry cleanly", %{
    directory: directory
  } do
    contents = "good model"
    {server, url} = TestFixture.start!(contents: "bad  model")
    on_exit(fn -> if Process.alive?(server), do: Supervisor.stop(server) end)
    asset = TestFixture.asset(contents, url)

    assert_raise Download.Error, ~r/did not pass verification/, fn ->
      Download.ensure(asset, directory, fn _, _ -> :ok end, allow_local_http: true)
    end

    refute File.exists?(Path.join(directory, asset.id))
    assert [preserved] = Path.wildcard(Path.join(directory, "*.unverified.*"))
    assert File.read!(preserved) == "bad  model"
    {other, good_url} = TestFixture.start!(contents: contents)
    on_exit(fn -> if Process.alive?(other), do: Supervisor.stop(other) end)

    assert :downloaded ==
             Download.ensure(
               %{asset | url: good_url <> "/model.gguf"},
               directory,
               fn _, _ -> :ok end,
               allow_local_http: true
             )
  end

  test "preserves invalid final files and verifies a complete partial without network", %{
    directory: directory
  } do
    contents = "new verified model"
    asset = TestFixture.asset(contents, "https://example.invalid")
    File.write!(Path.join(directory, asset.id), "old user file")
    File.write!(Path.join(directory, asset.id <> ".part"), contents)
    assert :downloaded == Download.ensure(asset, directory, fn _, _ -> :ok end)
    assert File.read!(Path.join(directory, asset.id)) == contents
    assert [preserved] = Path.wildcard(Path.join(directory, "*.unverified.*"))
    assert File.read!(preserved) == "old user file"
  end

  test "rejects traversal and symlink destinations without touching linked data", %{
    directory: directory
  } do
    asset = TestFixture.asset("data", "https://example.invalid")

    assert_raise ArgumentError, fn ->
      Download.ensure(%{asset | id: "../escape"}, directory, fn _, _ -> :ok end)
    end

    outside = Path.join(directory, "original")
    File.write!(outside, "keep me")
    File.ln_s!(outside, Path.join(directory, "model.gguf.part"))

    assert_raise Download.Error, ~r/not a regular file/, fn ->
      Download.ensure(asset, directory, fn _, _ -> :ok end)
    end

    assert File.read!(outside) == "keep me"
    File.mkdir!(Path.join(directory, "safe"))
    File.ln_s!(Path.join(directory, "safe"), Path.join(directory, "sidecars"))

    assert_raise Download.Error, ~r/not a regular directory/, fn ->
      Download.ensure(%{asset | id: "sidecars/token"}, directory, fn _, _ -> :ok end)
    end
  end

  test "detects redirect loops and rejects unsecured production URLs", %{directory: directory} do
    {server, url} = TestFixture.start!(contents: "data", mode: :loop)
    on_exit(fn -> if Process.alive?(server), do: Supervisor.stop(server) end)
    asset = TestFixture.asset("data", url)

    assert_raise Download.Error, ~r/secure source/, fn ->
      Download.ensure(asset, directory, fn _, _ -> :ok end)
    end

    assert_raise Download.Error, ~r/redirect loop/, fn ->
      Download.ensure(asset, directory, fn _, _ -> :ok end, allow_local_http: true)
    end
  end
end
