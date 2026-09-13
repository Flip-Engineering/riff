defmodule Riff.Runtime.AcousticCheckpoint do
  @moduledoc """
  Streaming inspection of completed native YuE2 acoustic checkpoints.

  The native engine owns synthesis and decoding. This boundary verifies the
  saved container before the library offers a reusable stage, without loading
  its tensor into the application VM. Historical producer identities describe
  provenance; compatibility is a separate comparison with the selected VAE.
  """

  import Bitwise

  defmodule Error do
    defexception [:message]
  end

  @header_bytes 512
  @chunk_bytes 65_536
  @max_i64 0x7FFFFFFFFFFFFFFF
  @max_u64 0xFFFFFFFFFFFFFFFF
  @hash_names ~w(payload model decoder model_config decoder_config tokenizer generation_config conditioning semantic_codes producer_binary)

  @doc "Verify a regular owned file and return metadata, never the latent payload."
  def inspect!(root, path) when is_binary(root) and is_binary(path) do
    check!(Path.expand(root) == root, "The acoustic library path is not canonical.")

    check!(
      Path.expand(path) == path and String.starts_with?(path, root <> "/"),
      "The saved sound is outside its library."
    )

    directory!(Path.dirname(path))
    before = regular!(path)
    {:ok, file} = File.open(path, [:read, :binary, :raw])

    try do
      opened = opened_stat!(file)
      check!(identity(opened) == identity(before), "The saved sound changed while opening.")
      header = read!(file, @header_bytes)
      metadata = parse_header!(header)

      check!(
        before.size == @header_bytes + metadata["payload_bytes"],
        "The saved sound is incomplete or has extra data."
      )

      whole = :crypto.hash_update(:crypto.hash_init(:sha256), header)
      payload = :crypto.hash_init(:sha256)
      {whole, payload} = stream!(file, metadata["payload_bytes"], whole, payload)

      check!(
        hex(:crypto.hash_final(payload)) == metadata["hashes"]["payload"],
        "The saved sound data has changed."
      )

      check!(:file.read(file, 1) == :eof, "The saved sound has extra data.")

      check!(
        identity(opened_stat!(file)) == identity(opened) and
          identity(regular!(path)) == identity(before),
        "The saved sound changed during inspection."
      )

      sha = hex(:crypto.hash_final(whole))
      Map.merge(metadata, %{"sha256" => sha, "bytes" => before.size})
    after
      File.close(file)
    end
  end

  @doc "Parse the versioned native header; payload integrity is checked by inspect!/2."
  def parse_header!(header) when is_binary(header) do
    check!(byte_size(header) == @header_bytes, "The saved sound header is incomplete.")
    <<body::binary-size(480), expected::binary-size(32)>> = header
    check!(:crypto.hash(:sha256, body) == expected, "The saved sound header has changed.")

    <<magic::binary-size(8), version::little-32, size::little-32, dtype::little-32,
      layout::little-32, complete::little-32, truncated::little-32, frames::little-64,
      latent::little-64, payload_bytes::little-64, rate::little-64, channels::little-64,
      ratio::little-64, core::little-64, halo::little-64, encoder_latent::little-64,
      vae_storage::little-32, model_storage::little-32, seed::little-64, steps::little-64,
      solver::little-32, decoder_contract::little-32, semantic_frames::little-64,
      context::little-64, guidance_bits::little-32, reserved::little-32,
      hashes::binary-size(320)>> = body

    check!(
      magic == "RIFFYAC1" and version == 1 and size == @header_bytes and
        dtype == 1 and layout == 1 and complete == 1 and truncated in [0, 1] and
        decoder_contract == 1 and reserved == 0,
      "The saved sound format is unsupported or unfinished."
    )

    check!(
      Enum.all?(
        [frames, latent, rate, channels, ratio, core, encoder_latent, steps, context],
        &(&1 > 0 and &1 <= @max_i64)
      ) and halo <= @max_i64 and seed <= @max_i64 and
        rate <= 0x7FFFFFFF and channels <= 0x7FFFFFFF and
        frames * latent <= @max_i64 and payload_bytes == frames * latent * 4 and
        payload_bytes <= @max_u64 - @header_bytes and
        semantic_frames == frames and frames * ratio <= @max_i64 and
        frames * ratio > 64 and (frames * ratio - 64) * channels * 4 <= @max_u64 and
        vae_storage in 0..6 and model_storage in 0..6 and solver in [1, 2],
      "The saved sound has invalid dimensions or synthesis metadata."
    )

    check!(finite?(guidance_bits), "The saved sound has invalid guidance.")
    <<guidance::little-float-32>> = <<guidance_bits::little-32>>
    check!(guidance >= 0 and guidance <= 20, "The saved sound has invalid guidance.")

    hash_values = for <<hash::binary-size(32) <- hashes>>, do: hex(hash)
    named_hashes = Map.new(Enum.zip(@hash_names, hash_values))

    check!(
      Enum.all?(Map.drop(named_hashes, ["payload"]), fn {_, value} ->
        value != String.duplicate("0", 64)
      end),
      "The saved sound is missing its source identities."
    )

    %{
      "format" => "riff.yue2.acoustic.v1",
      "decoder_contract" => decoder_contract,
      "frames" => frames,
      "latent_dim" => latent,
      "payload_bytes" => payload_bytes,
      "sample_rate" => rate,
      "channels" => channels,
      "downsampling_ratio" => ratio,
      "encoder_latent_dim" => encoder_latent,
      "decode_core_frames" => core,
      "decode_halo_frames" => halo,
      "vae_storage" => vae_storage,
      "model_storage" => model_storage,
      # Preserve all 63 native seed bits through JSON and browser form controls.
      "seed" => Integer.to_string(seed),
      "steps" => steps,
      "solver" => if(solver == 1, do: "midpoint", else: "ab2"),
      "context" => context,
      "guidance" => guidance,
      "truncated" => truncated == 1,
      "sample_frames" => frames * ratio - 64,
      "duration" => (frames * ratio - 64) / rate,
      "hashes" => named_hashes
    }
  end

  @doc "Compare decoding compatibility independently of historical synthesis settings."
  def compatible?(metadata, decoder) when is_map(metadata) and is_map(decoder) do
    metadata["format"] == "riff.yue2.acoustic.v1" and metadata["decoder_contract"] == 1 and
      is_binary(decoder["sha256"]) and Regex.match?(~r/\A[a-f0-9]{64}\z/, decoder["sha256"]) and
      metadata["hashes"]["decoder"] == decoder["sha256"] and
      Enum.all?(
        ~w(sample_rate channels latent_dim encoder_latent_dim downsampling_ratio),
        &(is_integer(metadata[&1]) and metadata[&1] > 0 and metadata[&1] == decoder[&1])
      )
  end

  defp stream!(_file, 0, whole, payload), do: {whole, payload}

  defp stream!(file, remaining, whole, payload) do
    bytes = read!(file, min(@chunk_bytes, remaining))
    finite_payload!(bytes)

    stream!(
      file,
      remaining - byte_size(bytes),
      :crypto.hash_update(whole, bytes),
      :crypto.hash_update(payload, bytes)
    )
  end

  defp finite_payload!(<<>>), do: :ok

  defp finite_payload!(<<value::little-32, rest::binary>>) do
    check!(finite?(value), "The saved sound contains invalid acoustic values.")
    finite_payload!(rest)
  end

  defp finite?(bits), do: (bits &&& 0x7F800000) != 0x7F800000

  defp read!(file, size) do
    case :file.read(file, size) do
      {:ok, bytes} when byte_size(bytes) == size -> bytes
      _ -> raise Error, "The saved sound ended before its data was complete."
    end
  end

  defp regular!(path) do
    case File.lstat(path, time: :posix) do
      {:ok, %{type: :regular} = info} -> info
      _ -> raise Error, "The saved sound is linked or unavailable."
    end
  end

  defp directory!(path) do
    check!(File.lstat!(path).type == :directory, "The sound library directory is linked.")
    parent = Path.dirname(path)
    if parent != path, do: directory!(parent)
  end

  defp opened_stat!(file) do
    {:ok, info} = :file.read_file_info(file, time: :posix)
    File.Stat.from_record(info)
  end

  defp identity(info),
    do: {info.type, info.major_device, info.inode, info.size, info.mtime, info.ctime}

  defp hex(value), do: Base.encode16(value, case: :lower)
  defp check!(true, _), do: :ok
  defp check!(_, message), do: raise(Error, message)
end
