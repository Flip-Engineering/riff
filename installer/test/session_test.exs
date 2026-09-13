defmodule Riff.Installer.SessionTest do
  use ExUnit.Case, async: false
  alias Riff.Installer.{Session, TestFixture}

  setup do
    directory =
      Path.join(
        System.tmp_dir!(),
        "riff-installer-session-" <>
          Base.url_encode64(:crypto.strong_rand_bytes(12), padding: false)
      )

    File.mkdir_p!(directory)
    on_exit(fn -> File.rm_rf!(directory) end)
    %{directory: directory}
  end

  test "cancel closes an active download promptly and Continue resumes all required assets", %{
    directory: directory
  } do
    contents = :crypto.strong_rand_bytes(2_097_152)
    {server, url} = TestFixture.start!(contents: contents, mode: :stream, delay: 3)
    on_exit(fn -> Supervisor.stop(server) end)
    manifest = TestFixture.manifest(contents, ["model.gguf", "sidecars/config.json"])

    session =
      start_supervised!(
        {Session,
         name: nil,
         manifest: manifest,
         destination: directory,
         base_url: url,
         allow_local_http: true}
      )

    assert Session.status(session).progress.total_files == 2
    assert Session.status(session).state == :idle
    assert {:ok, %{state: :installing}} = Session.install(session)
    assert {:error, :busy, _} = Session.install(session)
    assert_receive {:chunk, _}, 2000
    wait_for(session, &(&1.progress.downloaded_bytes > 0))
    started = System.monotonic_time(:millisecond)
    assert {:ok, %{state: :cancelling}} = Session.cancel(session)
    assert wait_for(session, &(&1.state == :cancelled)).state == :cancelled
    assert System.monotonic_time(:millisecond) - started < 1000
    assert %{size: saved} = File.stat!(Path.join(directory, "model.gguf.part"))
    assert saved > 0 and saved < byte_size(contents)
    refute File.exists?(Path.join(directory, "manifest.json"))
    assert {:ok, %{state: :installing}} = Session.install(session)
    assert wait_for(session, &(&1.state not in [:installing, :cancelling])).state == :prepared
    assert_receive {:request, _, [range]}, 1000
    assert range == "bytes=#{saved}-"
    assert File.read!(Path.join(directory, "model.gguf")) == contents
    assert File.read!(Path.join(directory, "sidecars/config.json")) == contents
    assert Jason.decode!(File.read!(Path.join(directory, "manifest.json"))) == manifest
    status = Session.status(session)
    assert status.progress.completed_files == 2
    assert status.progress.downloaded_bytes == status.progress.total_bytes
    refute status.app.available
    assert {:error, :app_unavailable, _} = Session.open(session)
  end

  test "a second installer cannot write the same destination and can continue after cancellation",
       %{directory: directory} do
    contents = :crypto.strong_rand_bytes(2_097_152)
    {server, url} = TestFixture.start!(contents: contents, mode: :stream, delay: 3)
    on_exit(fn -> Supervisor.stop(server) end)

    options = [
      name: nil,
      manifest: TestFixture.manifest(contents),
      destination: directory,
      base_url: url,
      allow_local_http: true
    ]

    first = start_supervised!(Supervisor.child_spec({Session, options}, id: :first))
    second = start_supervised!(Supervisor.child_spec({Session, options}, id: :second))
    assert {:ok, %{state: :installing}} = Session.install(first)
    assert {:ok, %{state: :error, error: message}} = Session.install(second)
    assert message =~ "Another Riff setup window"
    Session.cancel(first)
    wait_for(first, &(&1.state == :cancelled))
    assert {:ok, %{state: :installing}} = Session.install(second)
    assert wait_for(second, &(&1.state not in [:installing, :cancelling])).state == :prepared
    assert File.read!(Path.join(directory, "model.gguf")) == contents
  end

  test "only a verified app integration permits ready and Open", %{directory: directory} do
    contents = "test model"
    {server, url} = TestFixture.start!(contents: contents)
    on_exit(fn -> Supervisor.stop(server) end)
    owner = self()
    check = fn _ -> {:ok, %{version: "test-version", url: "http://127.0.0.1:7878"}} end

    opener = fn app ->
      send(owner, {:opened, app.version})
      :ok
    end

    session =
      start_supervised!(
        {Session,
         name: nil,
         manifest: TestFixture.manifest(contents),
         destination: directory,
         base_url: url,
         allow_local_http: true,
         app_check: check,
         app_opener: opener}
      )

    assert {:error, :app_unavailable, _} = Session.open(session)
    Session.install(session)
    assert wait_for(session, &(&1.state not in [:installing, :cancelling])).state == :ready
    assert {:ok, %{opened: true}} = Session.open(session)
    assert_received {:opened, "test-version"}
  end

  defp wait_for(server, predicate),
    do: wait_for(server, predicate, System.monotonic_time(:millisecond) + 10_000)

  defp wait_for(server, predicate, deadline) do
    status = Session.status(server)

    if predicate.(status) do
      status
    else
      assert System.monotonic_time(:millisecond) < deadline,
             "setup did not reach expected state: #{inspect(status)}"

      Process.sleep(5)
      wait_for(server, predicate, deadline)
    end
  end
end
