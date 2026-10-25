defmodule Riff.Runtime.AcousticCheckpointTest do
  use ExUnit.Case, async: true
  alias Riff.Runtime.AcousticCheckpoint, as: Checkpoint

  @fixture Path.expand("fixtures/acoustic/checkpoint-v1.bin", __DIR__)

  setup do
    root = Path.expand("../_build/acoustic-test-#{System.unique_integer([:positive])}", __DIR__)
    File.mkdir_p!(root)
    on_exit(fn -> File.rm_rf!(root) end)
    bytes = File.read!(@fixture)
    path = Path.join(root, "saved.acoustic")
    File.write!(path, bytes)
    %{root: root, path: path, bytes: bytes}
  end

  test "a native-written completed stage is inspected without returning its tensor", c do
    saved = Checkpoint.inspect!(c.root, c.path)
    assert saved["format"] == "riff.yue2.acoustic.v1"
    assert saved["frames"] == 3
    assert saved["latent_dim"] == 64
    assert saved["seed"] == "9223372036854775807"
    assert saved["solver"] == "ab2"
    assert saved["steps"] == 4
    assert saved["truncated"]
    assert_in_delta saved["guidance"], 1.1, 0.0000001
    assert saved["bytes"] == byte_size(c.bytes)
    assert saved["payload_bytes"] == saved["frames"] * saved["latent_dim"] * 4
    assert saved["sha256"] == hex(:crypto.hash(:sha256, c.bytes))
    assert saved["sample_frames"] == saved["frames"] * saved["downsampling_ratio"] - 64
    refute Map.has_key?(saved, "latents")
    refute Map.has_key?(saved, "payload")
  end

  test "removing a publisher's temporary alias during inspection preserves unchanged content",
       c do
    expected = Checkpoint.inspect!(c.root, c.path)
    alias_path = c.path <> ".publishing"
    File.ln!(c.path, alias_path)
    before = File.stat!(c.path, time: :posix)
    assert before.links == 2

    result =
      Checkpoint.inspect!(c.root, c.path, fn ->
        changed = unlink_after_ctime_tick!(c.path, alias_path, before.ctime)
        assert changed.ctime != before.ctime
        assert changed.links == 1

        assert {changed.major_device, changed.inode, changed.size, changed.mtime} ==
                 {before.major_device, before.inode, before.size, before.mtime}
      end)

    assert result == expected
    assert File.read!(c.path) == c.bytes
  end

  test "same-size payload corruption during inspection is rejected even with mtime restored", c do
    before = File.stat!(c.path, time: :posix)
    <<header::binary-size(512), first, rest::binary>> = c.bytes

    assert_raise Checkpoint.Error, "The saved sound data has changed.", fn ->
      Checkpoint.inspect!(c.root, c.path, fn ->
        File.write!(c.path, header <> <<Bitwise.bxor(first, 1)>> <> rest)
        File.touch!(c.path, before.mtime)
      end)
    end
  end

  test "replacing the path with identical bytes during inspection is still rejected", c do
    assert_raise Checkpoint.Error, "The saved sound changed during inspection.", fn ->
      Checkpoint.inspect!(c.root, c.path, fn ->
        File.rename!(c.path, c.path <> ".old")
        File.write!(c.path, c.bytes)
      end)
    end
  end

  test "header edits and truncation after opening remain invalid", c do
    <<first, rest::binary>> = c.bytes

    for {replacement, message} <- [
          {<<Bitwise.bxor(first, 1)>> <> rest, "The saved sound header has changed."},
          {binary_part(c.bytes, 0, byte_size(c.bytes) - 1),
           "The saved sound ended before its data was complete."}
        ] do
      File.write!(c.path, c.bytes)

      assert_raise Checkpoint.Error, message, fn ->
        Checkpoint.inspect!(c.root, c.path, fn -> File.write!(c.path, replacement) end)
      end
    end
  end

  defp unlink_after_ctime_tick!(path, alias_path, original_ctime) do
    # File.Stat's POSIX timestamps have one-second precision. Wait for that
    # observed bucket to change, rather than hoping the race crosses a tick.
    remaining = (original_ctime + 1) * 1000 - System.os_time(:millisecond)
    if remaining > 0, do: Process.sleep(remaining)
    File.rm!(alias_path)
    changed = File.stat!(path, time: :posix)

    if changed.ctime == original_ctime do
      # A filesystem's cached timestamp can lag the wall clock at the edge.
      File.ln!(path, alias_path)
      unlink_after_ctime_tick!(path, alias_path, original_ctime)
    else
      changed
    end
  end

  test "interrupted publication, trailing bytes and changed acoustic data are not reusable", c do
    for size <- [0, 8, 511, byte_size(c.bytes) - 1] do
      File.write!(c.path, binary_part(c.bytes, 0, size))
      assert_raise Checkpoint.Error, fn -> Checkpoint.inspect!(c.root, c.path) end
    end

    File.write!(c.path, c.bytes <> <<0>>)
    assert_raise Checkpoint.Error, fn -> Checkpoint.inspect!(c.root, c.path) end
    <<prefix::binary-size(512), first, rest::binary>> = c.bytes
    File.write!(c.path, prefix <> <<Bitwise.bxor(first, 1)>> <> rest)
    assert_raise Checkpoint.Error, fn -> Checkpoint.inspect!(c.root, c.path) end
  end

  test "authenticated but unsupported or unfinished headers are rejected", c do
    for {offset, replacement} <- [
          {8, <<2::little-32>>},
          {16, <<2::little-32>>},
          {20, <<2::little-32>>},
          {24, <<0::little-32>>},
          {28, <<2::little-32>>},
          {32, <<0::little-64>>},
          {40, <<0xFFFFFFFFFFFFFFFF::little-64>>},
          {48, <<0::little-64>>},
          {132, <<2::little-32>>},
          {136, <<0::little-64>>},
          {152, <<0x7FC00000::little-32>>},
          {156, <<1::little-32>>},
          {192, :binary.copy(<<0>>, 32)}
        ] do
      changed = c.bytes |> replace(offset, replacement) |> sign_header()
      File.write!(c.path, changed)
      assert_raise Checkpoint.Error, fn -> Checkpoint.inspect!(c.root, c.path) end
    end
  end

  test "valid hashes do not make nonfinite latent values usable", c do
    for value <- [0x7F800000, 0xFF800000, 0x7FC00000] do
      changed = replace(c.bytes, 512, <<value::little-32>>)
      <<_::binary-size(512), payload::binary>> = changed
      changed = changed |> replace(160, :crypto.hash(:sha256, payload)) |> sign_header()
      File.write!(c.path, changed)
      assert_raise Checkpoint.Error, fn -> Checkpoint.inspect!(c.root, c.path) end
    end
  end

  test "the payload length fits the native signed header decoder", c do
    header =
      binary_part(c.bytes, 0, 512)
      |> replace(32, <<Bitwise.bsl(1, 57)::little-64>>)
      |> replace(40, <<16::little-64>>)
      |> replace(48, <<Bitwise.bsl(1, 63)::little-64>>)
      |> replace(72, <<1::little-64>>)
      |> replace(136, <<Bitwise.bsl(1, 57)::little-64>>)
      |> sign_header()

    assert_raise Checkpoint.Error, fn -> Checkpoint.parse_header!(header) end
  end

  test "missing files and parents use the artifact error contract", c do
    File.rm!(c.path)
    assert_raise Checkpoint.Error, fn -> Checkpoint.inspect!(c.root, c.path) end

    assert_raise Checkpoint.Error, fn ->
      Checkpoint.inspect!(c.root, Path.join([c.root, "gone", "saved.acoustic"]))
    end
  end

  test "file and ancestor links, sibling roots and nonregular input stay outside the library",
       c do
    link = Path.join(c.root, "linked.acoustic")
    File.ln_s!(c.path, link)
    assert_raise Checkpoint.Error, fn -> Checkpoint.inspect!(c.root, link) end

    directory = Path.join(c.root, "linked-directory")
    File.ln_s!(c.root, directory)

    assert_raise Checkpoint.Error, fn ->
      Checkpoint.inspect!(c.root, Path.join(directory, "saved.acoustic"))
    end

    assert_raise Checkpoint.Error, fn -> Checkpoint.inspect!(c.root <> "-other", c.path) end
    folder = Path.join(c.root, "folder")
    File.mkdir!(folder)
    assert_raise Checkpoint.Error, fn -> Checkpoint.inspect!(c.root, folder) end
  end

  test "decoder compatibility allows historical and explicit refinement differences", c do
    saved = Checkpoint.inspect!(c.root, c.path)
    keys = ~w(sample_rate channels latent_dim encoder_latent_dim downsampling_ratio)
    decoder = saved |> Map.take(keys) |> Map.put("sha256", saved["hashes"]["decoder"])
    assert Checkpoint.compatible?(saved, decoder)

    revised =
      saved |> Map.put("seed", 0) |> Map.put("steps", 100) |> Map.put("decode_core_frames", 32)

    revised = put_in(revised, ["hashes", "producer_binary"], String.duplicate("a", 64))
    assert Checkpoint.compatible?(revised, decoder)

    for key <- keys do
      refute Checkpoint.compatible?(saved, Map.update!(decoder, key, &(&1 + 1)))
    end

    refute Checkpoint.compatible?(saved, %{decoder | "sha256" => String.duplicate("0", 64)})
    refute Checkpoint.compatible?(%{}, %{})
  end

  test "complete payload verification crosses multiple bounded reads", c do
    <<header::binary-size(512), payload::binary>> = c.bytes
    saved = Checkpoint.parse_header!(header)
    copies = div(65_536, byte_size(payload)) + 3

    digest =
      Enum.reduce(1..copies, :crypto.hash_init(:sha256), fn _, acc ->
        :crypto.hash_update(acc, payload)
      end)
      |> :crypto.hash_final()

    header =
      header
      |> replace(32, <<saved["frames"] * copies::little-64>>)
      |> replace(48, <<byte_size(payload) * copies::little-64>>)
      |> replace(136, <<saved["frames"] * copies::little-64>>)
      |> replace(160, digest)
      |> sign_header()

    File.open!(c.path, [:write, :binary], fn file ->
      IO.binwrite(file, header)
      for _ <- 1..copies, do: IO.binwrite(file, payload)
    end)

    inspected = Checkpoint.inspect!(c.root, c.path)
    assert inspected["frames"] == saved["frames"] * copies
    assert inspected["payload_bytes"] > 65_536
    assert inspected["hashes"]["payload"] == hex(digest)
  end

  defp replace(bytes, offset, replacement) do
    count = byte_size(replacement)
    <<prefix::binary-size(offset), _::binary-size(count), rest::binary>> = bytes
    prefix <> replacement <> rest
  end

  defp sign_header(<<body::binary-size(480), _::binary-size(32), payload::binary>>) do
    body <> :crypto.hash(:sha256, body) <> payload
  end

  defp hex(bytes), do: Base.encode16(bytes, case: :lower)
end
