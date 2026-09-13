defmodule Riff.Runtime.AcousticArtifactTest do
  use ExUnit.Case, async: true
  alias Riff.Runtime.{AcousticArtifact, AcousticCheckpoint}

  setup_all do
    root =
      Path.join(
        System.tmp_dir!(),
        "riff-copy-launcher-" <> Base.encode16(:crypto.strong_rand_bytes(12))
      )

    File.mkdir!(root)
    on_exit(fn -> File.rm_rf!(root) end)
    launcher = Path.join(root, "limited-copy")

    {output, status} =
      System.cmd(
        System.find_executable("cc") || flunk("The native copy fixture needs a C compiler."),
        [
          "-std=c11",
          "-Wall",
          "-Wextra",
          "-Werror",
          Path.join(__DIR__, "fixtures/acoustic/limited_copy.c"),
          "-o",
          launcher
        ],
        stderr_to_stdout: true
      )

    assert status == 0, "Could not compile the native copy fixture:\n#{output}"
    %{copy_launcher: launcher}
  end

  setup do
    temporary = if File.dir?("/private/tmp"), do: "/private/tmp", else: System.tmp_dir!()

    root =
      Path.join(
        temporary,
        "riff-acoustic-artifact-" <> Base.encode16(:crypto.strong_rand_bytes(12))
      )

    for name <- ~w(library engine input), do: File.mkdir_p!(Path.join(root, name))
    source = Path.join(root, "engine/completed.yac")
    File.cp!(Path.join(__DIR__, "fixtures/acoustic/checkpoint-v1.bin"), source)
    metadata = AcousticCheckpoint.inspect!(root, source)

    decoder =
      metadata
      |> Map.take(~w(sample_rate channels latent_dim encoder_latent_dim downsampling_ratio))
      |> Map.put("sha256", metadata["hashes"]["decoder"])

    on_exit(fn -> File.rm_rf!(root) end)

    %{
      root: root,
      source: source,
      library: Path.join(root, "library"),
      input: Path.join(root, "input"),
      metadata: metadata,
      decoder: decoder
    }
  end

  test "native bytes survive capture, repeated capture and independent queued inputs", c do
    saved = AcousticArtifact.capture_file!(c.library, c.root, c.source, %{"job_id" => "original"})

    assert saved ==
             AcousticArtifact.capture_file!(c.library, c.root, c.source, %{"job_id" => "original"})

    assert saved["descriptor"]["acoustic"] == c.metadata
    assert saved["descriptor"]["acoustic"]["seed"] == "9223372036854775807"
    assert saved == AcousticArtifact.resolve!(c.library, saved["id"], c.decoder)
    prepared = AcousticArtifact.prepare!(c.library, saved["id"], c.decoder, c.input)
    assert prepared == AcousticArtifact.prepare!(c.library, saved["id"], c.decoder, c.input)
    paths = [c.source, saved["path"], prepared["input_path"]]
    assert paths |> Enum.map(&File.read!/1) |> Enum.uniq() |> length() == 1
    assert paths |> Enum.map(&File.stat!(&1).inode) |> Enum.uniq() |> length() == 3
    File.write!(c.source, "engine output changed later")
    assert AcousticArtifact.resolve!(c.library, saved["id"], c.decoder) == saved
    assert AcousticCheckpoint.inspect!(c.input, prepared["input_path"]) == c.metadata
  end

  test "incomplete capture cannot publish a reference or replace the source", c do
    bytes = File.read!(c.source)
    File.write!(c.source, binary_part(bytes, 0, 600))

    assert_raise AcousticCheckpoint.Error, fn ->
      AcousticArtifact.capture_file!(c.library, c.root, c.source, %{})
    end

    assert File.ls!(c.library) == []
    assert byte_size(File.read!(c.source)) == 600

    assert_raise AcousticCheckpoint.Error, fn ->
      AcousticArtifact.capture_file!(c.library, c.input, c.source, %{})
    end
  end

  test "changed saved data and descriptors fail without repair or replacement", c do
    saved = AcousticArtifact.capture_file!(c.library, c.root, c.source, %{})
    original = File.read!(saved["path"])
    File.write!(saved["path"], "damaged checkpoint")

    assert_raise AcousticCheckpoint.Error, fn ->
      AcousticArtifact.resolve!(c.library, saved["id"])
    end

    assert_raise AcousticCheckpoint.Error, fn ->
      AcousticArtifact.capture_file!(c.library, c.root, c.source, %{})
    end

    assert File.read!(saved["path"]) == "damaged checkpoint"
    File.write!(saved["path"], original)
    descriptor = Path.join(Path.dirname(saved["path"]), "descriptor.json")
    File.write!(descriptor, "changed descriptor")

    assert_raise AcousticArtifact.Error, fn ->
      AcousticArtifact.resolve!(c.library, saved["id"])
    end

    assert File.read!(descriptor) == "changed descriptor"
  end

  test "historical inspection is separate from compatible decoder admission", c do
    saved = AcousticArtifact.capture_file!(c.library, c.root, c.source, %{"seed" => "historical"})
    assert AcousticArtifact.resolve!(c.library, saved["id"]) == saved

    for wrong <- [
          Map.put(c.decoder, "sha256", String.duplicate("0", 64)),
          Map.put(c.decoder, "latent_dim", 1)
        ] do
      assert_raise AcousticArtifact.Error, fn ->
        AcousticArtifact.prepare!(c.library, saved["id"], wrong, c.input)
      end
    end

    assert File.ls!(c.input) == []

    assert_raise AcousticArtifact.Error, fn ->
      AcousticArtifact.resolve!(c.library, "riff-acoustic-v1:../elsewhere")
    end
  end

  test "changed or aliased queued inputs are not silently overwritten", c do
    saved = AcousticArtifact.capture_file!(c.library, c.root, c.source, %{})
    input = Path.join(c.input, "acoustic.yac")
    File.write!(input, "unfinished input")

    assert_raise AcousticCheckpoint.Error, fn ->
      AcousticArtifact.prepare!(c.library, saved["id"], c.decoder, c.input)
    end

    assert File.read!(input) == "unfinished input"
    File.rm!(input)
    File.ln!(saved["path"], input)

    assert_raise AcousticArtifact.Error, fn ->
      AcousticArtifact.prepare!(c.library, saved["id"], c.decoder, c.input)
    end

    assert File.stat!(input).inode == File.stat!(saved["path"]).inode
    File.rm!(input)
    File.ln_s!(saved["path"], input)

    assert_raise AcousticCheckpoint.Error, fn ->
      AcousticArtifact.prepare!(c.library, saved["id"], c.decoder, c.input)
    end

    assert File.lstat!(input).type == :symlink
  end

  test "a killed partial copy has no final name and can retry without sweeping older staging",
       c do
    saved = AcousticArtifact.capture_file!(c.library, c.root, c.source, %{})
    bytes = File.read!(c.source)
    earlier = Path.join(c.input, ".acoustic-earlier")
    File.write!(earlier, "another invocation")

    {output, status} =
      limited_copy(
        c.copy_launcher,
        :prepare!,
        [c.library, saved["id"], c.decoder, c.input],
        :kill
      )

    assert output =~ "COPY_STARTED\n", "Copy child did not start (#{status}):\n#{output}"
    assert [_, signal] = Regex.run(~r/COPY_LIMIT mode=kill bytes=1024 signal=(\d+)/, output)

    assert status == 128 + String.to_integer(signal),
           "Copy child was not killed by SIGXFSZ:\n#{output}"

    refute File.exists?(Path.join(c.input, "acoustic.yac"))
    assert [partial_name] = File.ls!(c.input) -- [Path.basename(earlier)], output
    assert String.starts_with?(partial_name, ".acoustic-")
    partial = Path.join(c.input, partial_name)
    partial_bytes = File.read!(partial)
    assert byte_size(partial_bytes) == 1024 and byte_size(partial_bytes) < byte_size(bytes)

    prepared = AcousticArtifact.prepare!(c.library, saved["id"], c.decoder, c.input)
    assert File.read!(prepared["input_path"]) == bytes
    assert File.read!(c.source) == bytes
    assert File.read!(saved["path"]) == bytes
    assert File.read!(partial) == partial_bytes
    assert File.read!(earlier) == "another invocation"

    assert Enum.sort(File.ls!(c.input)) ==
             Enum.sort(["acoustic.yac", partial_name, Path.basename(earlier)])
  end

  test "a caught partial copy error reclaims only its own sibling and a retry succeeds", c do
    saved = AcousticArtifact.capture_file!(c.library, c.root, c.source, %{})
    bytes = File.read!(c.source)
    earlier = Path.join(c.input, ".acoustic-earlier")
    File.write!(earlier, "another invocation")

    {output, status} =
      limited_copy(
        c.copy_launcher,
        :prepare!,
        [c.library, saved["id"], c.decoder, c.input],
        :error
      )

    assert output =~ "COPY_STARTED\n", "Copy child did not start (#{status}):\n#{output}"
    assert status == 1, output
    assert output =~ "File.Error"
    assert output =~ "copy sound"
    assert output =~ "file too large"
    assert File.ls!(c.input) == [Path.basename(earlier)]
    prepared = AcousticArtifact.prepare!(c.library, saved["id"], c.decoder, c.input)
    assert File.read!(prepared["input_path"]) == bytes
    assert File.read!(saved["path"]) == bytes
    assert File.read!(c.source) == bytes
    assert File.read!(earlier) == "another invocation"
  end

  test "a caught capture error removes its stage while preserving earlier stages and source", c do
    bytes = File.read!(c.source)
    earlier = Path.join(c.library, ".staging-earlier")
    File.mkdir!(earlier)
    File.write!(Path.join(earlier, "keep"), "another invocation")

    {output, status} =
      limited_copy(c.copy_launcher, :capture_file!, [c.library, c.root, c.source, %{}], :error)

    assert output =~ "COPY_STARTED\n", "Copy child did not start (#{status}):\n#{output}"
    assert status == 1, output
    assert output =~ "File.Error"
    assert output =~ "copy sound"
    assert output =~ "file too large"
    assert File.ls!(c.library) == [Path.basename(earlier)]
    saved = AcousticArtifact.capture_file!(c.library, c.root, c.source, %{})
    assert File.read!(saved["path"]) == bytes
    assert File.read!(c.source) == bytes
    assert File.read!(Path.join(earlier, "keep")) == "another invocation"

    assert Enum.sort(File.ls!(c.library)) ==
             Enum.sort([Path.basename(earlier), Path.basename(Path.dirname(saved["path"]))])
  end

  test "concurrent captures and preparations verify the winner and reclaim unpublished copies",
       c do
    earlier = Path.join(c.library, ".staging-earlier")
    File.mkdir!(earlier)
    File.write!(Path.join(earlier, "keep"), "another invocation")

    captures =
      simultaneously(fn -> AcousticArtifact.capture_file!(c.library, c.root, c.source, %{}) end)

    [saved] = Enum.uniq(captures)

    assert Enum.sort(File.ls!(c.library)) ==
             Enum.sort([Path.basename(earlier), Path.basename(Path.dirname(saved["path"]))])

    assert File.read!(Path.join(earlier, "keep")) == "another invocation"

    inputs =
      simultaneously(fn ->
        AcousticArtifact.prepare!(c.library, saved["id"], c.decoder, c.input)
      end)

    [prepared] = Enum.uniq(inputs)
    assert File.ls!(c.input) == ["acoustic.yac"]
    assert File.read!(prepared["input_path"]) == File.read!(c.source)
    assert File.stat!(prepared["input_path"]).inode != File.stat!(saved["path"]).inode
  end

  defp simultaneously(operation) do
    tasks = for _ <- 1..8, do: Task.async(fn -> receive do: (:go -> operation.()) end)
    for task <- tasks, do: send(task.pid, :go)
    Enum.map(tasks, &Task.await(&1, 10_000))
  end

  defp limited_copy(launcher, operation, arguments, mode) do
    command =
      "IO.puts(\"COPY_STARTED\"); " <>
        "apply(Riff.Runtime.AcousticArtifact, #{inspect(operation)}, #{inspect(arguments, limit: :infinity)})"

    # Start with the problematic ignored disposition deliberately. The native
    # launcher resets it after shell startup and fixes the limit at 1,024 bytes.
    # Linux BeamAsm normally grows a memfd with ftruncate for dual-mapped code,
    # which also hits RLIMIT_FSIZE before this tiny copy can start. Select its
    # documented anonymous single mapping only in this owned test child. Keep
    # optional profiler files disabled; the production VM keeps its defaults.
    jit_flags = if :os.type() == {:unix, :linux}, do: " +JMsingle true +JPperf false", else: ""

    System.cmd(
      "/bin/sh",
      [
        "-c",
        "trap '' XFSZ; exec \"$@\"",
        "riff-acoustic-copy-fixture",
        launcher,
        Atom.to_string(mode),
        System.find_executable("elixir"),
        "-pa",
        Application.app_dir(:riff_installer, "ebin"),
        "-pa",
        Application.app_dir(:jason, "ebin"),
        "-e",
        command
      ],
      stderr_to_stdout: true,
      env: [
        {"ERL_FLAGS", "+S 1:1 +A 1" <> jit_flags},
        {"ERL_AFLAGS", nil},
        {"ERL_ZFLAGS", nil},
        {"ELIXIR_ERL_OPTIONS", nil},
        {"ERL_CRASH_DUMP", "/dev/null"}
      ]
    )
  end
end
