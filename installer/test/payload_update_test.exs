defmodule Riff.Installer.PayloadUpdateTest do
  use ExUnit.Case, async: false
  alias Riff.Installer.{Download, Lock, Payload, TestFixture, Update}

  setup do
    directory =
      Path.join(
        System.tmp_dir!(),
        "riff-payload-" <> Base.url_encode64(:crypto.strong_rand_bytes(12), padding: false)
      )

    File.mkdir_p!(directory)
    on_exit(fn -> File.rm_rf!(directory) end)
    contents = "a small verified music model"
    {server, url} = TestFixture.start!(contents: contents)
    on_exit(fn -> Supervisor.stop(server) end)
    package = fixture(directory, contents)
    root = Path.join(directory, "installation")
    options = [preflight: false, allow_test_platform: true, base_url: url, allow_local_http: true]
    request = %{"protocol" => 1, "action" => "prepare", "payload" => package, "root" => root}
    %{directory: directory, package: package, root: root, request: request, options: options}
  end

  test "preparation verifies package and required models, reuses them, and leaves selection and library alone",
       context do
    library = Path.join(context.root, "workspace/library/my-song.wav")
    File.mkdir_p!(Path.dirname(library))
    File.write!(library, "recording")
    result = Update.execute(context.request, &send(self(), {:progress, &1}), context.options)
    assert result.status == "prepared"
    assert File.read!(library) == "recording"
    refute File.exists?(Path.join(context.root, "current.json"))
    refute File.exists?(Path.join(context.root, "runtime.json"))
    assert File.regular?(Path.join(result.path, ".engine.json"))
    assert_received {:progress, %{protocol: 1, stage: :verified}}
    assert_received {:request, _, []}
    flush_requests()
    assert Update.execute(context.request, fn _ -> :ok end, context.options).status == "prepared"
    refute_received {:request, _, _}
  end

  test "activation publishes only launch metadata after preparation and preserves previous source and engine settings",
       context do
    Update.execute(context.request, fn _ -> :ok end, context.options)
    data = Path.join(context.root, "workspace/data")
    File.mkdir_p!(data)
    File.write!(Path.join(data, "engine.json"), "existing engine settings")

    Payload.write_json(Path.join(context.root, "current.json"), %{
      version: "0.4.0",
      previous: "0.3.0"
    })

    result =
      Update.execute(
        %{context.request | "action" => "activate"},
        fn _ -> :ok end,
        context.options
      )

    assert result.status == "activated"

    assert Jason.decode!(File.read!(Path.join(context.root, "current.json"))) == %{
             "version" => "0.5.0",
             "previous" => "0.4.0",
             "previous_runtime" => nil
           }

    runtime = Jason.decode!(File.read!(Path.join(context.root, "runtime.json")))
    assert runtime["runtime_id"] == result.runtime_id
    assert runtime["environment"]["SSL_CERT_FILE"] == "/etc/ssl/cert.pem"
    refute File.exists?(Path.join(context.root, ".installer-activation.json"))
    assert File.read!(Path.join(data, "engine.json")) == "existing engine settings"
    assert File.read!(Path.join(context.root, "launcher.py")) == "launcher source"
  end

  test "changed prepared models refuse activation without downloading or changing the current version",
       context do
    Update.execute(context.request, fn _ -> :ok end, context.options)
    flush_requests()
    File.write!(Path.join(context.root, "workspace/models/yue2-3b-q4_0.gguf"), "changed")

    assert_raise Download.Error, ~r/prepared music models have changed/, fn ->
      Update.execute(
        %{context.request | "action" => "activate"},
        fn _ -> :ok end,
        context.options
      )
    end

    refute File.exists?(Path.join(context.root, "current.json"))
    refute_received {:request, _, _}
  end

  test "model sources must match the package before they can choose downloads", context do
    File.write!(
      Path.join(context.package, "app/sources.json"),
      Jason.encode!(%{model: TestFixture.manifest("untrusted")})
    )

    assert_raise Download.Error, ~r/model sources did not pass verification/, fn ->
      Update.execute(context.request, fn _ -> :ok end, context.options)
    end

    refute_received {:request, _, _}
  end

  test "runtime identity covers all runtime records", context do
    manifest_path = Path.join(context.package, "manifest.json")
    manifest = Jason.decode!(File.read!(manifest_path))
    Payload.write_json(manifest_path, Map.put(manifest, "runtime_id", String.duplicate("b", 24)))

    assert_raise Download.Error, ~r/runtime identity does not match/, fn ->
      Payload.load(context.package)
    end
  end

  test "linked installation ancestors cannot redirect payload writes", context do
    outside = Path.join(context.directory, "outside")
    File.mkdir_p!(outside)
    File.mkdir_p!(context.root)
    File.ln_s!(outside, Path.join(context.root, "releases"))

    assert_raise Download.Error, ~r/not a regular directory/, fn ->
      Update.execute(context.request, fn _ -> :ok end, context.options)
    end

    assert File.ls!(outside) == []
    refute File.exists?(Path.join(context.root, "current.json"))
  end

  test "a corrupt payload is never selected and can be retried after replacing the package",
       context do
    source = Path.join(context.package, "runtime/media/ffmpeg")
    original = File.read!(source)
    File.write!(source, "corrupt media")

    assert_raise Download.Error, ~r/did not pass verification/, fn ->
      Update.execute(context.request, fn _ -> :ok end, context.options)
    end

    refute File.exists?(Path.join(context.root, "current.json"))
    File.write!(source, original)
    assert Update.execute(context.request, fn _ -> :ok end, context.options).status == "prepared"
  end

  test "headless updates use the same root lock as GUI activation", context do
    {:ok, lock} = Lock.acquire(context.root)

    try do
      assert_raise Download.Error, ~r/Another Riff setup window/, fn ->
        Update.execute(context.request, fn _ -> :ok end, context.options)
      end
    after
      Lock.release(lock)
    end
  end

  test "a packaged local writer includes every pinned writer asset and passes owned paths to the launcher",
       context do
    manifest_path = Path.join(context.package, "manifest.json")
    manifest = Jason.decode!(File.read!(manifest_path))
    Payload.write_json(manifest_path, Map.put(manifest, "features", %{"local_writer" => true}))
    result = Update.execute(context.request, fn _ -> :ok end, context.options)
    writer = Path.join(context.root, "workspace/models/lyric-writer")
    assert File.regular?(Path.join(writer, "model.safetensors"))
    assert File.regular?(Path.join(writer, "config.json"))
    assert File.regular?(Path.join(writer, "manifest.json"))
    assert result.status == "prepared"
    Update.execute(%{context.request | "action" => "activate"}, fn _ -> :ok end, context.options)
    receipt = Path.join(context.root, "runtime.json") |> File.read!() |> Jason.decode!()
    assert receipt["writer_model"] == "workspace/models/lyric-writer"
    refute File.exists?(Path.join(context.root, "workspace/.writer-venv"))
    File.write!(Path.join(writer, "config.json"), "damaged")

    assert_raise Download.Error, ~r/prepared music models have changed/, fn ->
      Update.execute(
        %{context.request | "action" => "activate"},
        fn _ -> :ok end,
        context.options
      )
    end
  end

  defp fixture(directory, model) do
    package = Path.join(directory, "package")

    manifest =
      TestFixture.manifest(model, [
        "yue2-3b-q4_0.gguf",
        "yue2-vae-f16.gguf",
        "sidecars/config.json"
      ])

    entries = %{
      "python" => "runtime/python/bin/python3",
      "studio" => "app/studio.py",
      "launcher" => "app/launcher.py",
      "engine" => "runtime/engine/audiocpp_cli",
      "ffmpeg" => "runtime/media/ffmpeg",
      "ffprobe" => "runtime/media/ffprobe",
      "control" => "runtime/control/bin/riff_installer"
    }

    files =
      Map.new(entries, fn {key, path} -> {path, key <> " source"} end)
      |> Map.put(
        "app/sources.json",
        Jason.encode!(%{
          model: manifest,
          writer: TestFixture.manifest(model, ["model.safetensors", "config.json"])
        })
      )
      |> Map.put(
        "runtime/control/lib/riff_installer-0.1.0/priv/native/riff-file-lock",
        "lock helper"
      )
      |> Enum.sort()
      |> Enum.map(fn {path, content} ->
        target = Path.join(package, path)
        File.mkdir_p!(Path.dirname(target))
        File.write!(target, content)

        %{
          "path" => path,
          "bytes" => byte_size(content),
          "sha256" => TestFixture.sha(content),
          "executable" => String.starts_with?(path, "runtime/")
        }
      end)

    digest =
      files
      |> Enum.filter(&String.starts_with?(&1["path"], "runtime/"))
      |> :json.encode()
      |> IO.iodata_to_binary()
      |> TestFixture.sha()

    Payload.write_json(Path.join(package, "manifest.json"), %{
      "format_version" => 1,
      "version" => "0.5.0",
      "platform" => "fixture",
      "runtime_id" => String.slice(digest, 0, 24),
      "runtime_sha256" => digest,
      "files" => files,
      "entries" => entries,
      "environment" => %{"SSL_CERT_FILE" => "/etc/ssl/cert.pem"}
    })

    package
  end

  defp flush_requests do
    receive do
      {:request, _, _} -> flush_requests()
    after
      0 -> :ok
    end
  end
end
