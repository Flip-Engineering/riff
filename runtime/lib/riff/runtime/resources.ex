defmodule Riff.Runtime.Resources do
  @moduledoc """
  Architecture-based peak reservations for YuE2 and its local writer.

  These are conservative estimates, not measured promises. Weight bytes come
  from the installed files; KV growth comes from the actual layer/head layout,
  token budget and CFG branches. Native decoding uses its configured VAE chunk
  size. A configurable uncertainty margin covers allocator and backend scratch
  space until a measured peak demonstrates that more headroom is necessary.

  Acoustic replay is a separate operation. Its inputs are verified checkpoint
  metadata and the selected decoder controls; no text/model configuration is
  needed. The decoder estimate follows the native Oobleck tensor shapes and
  allows unfused convolution work. It is deliberately not fitted to one short
  sampled physical-footprint result or presented as a measured device peak.
  """

  # This exact GGUF has 347 F16 source tensors. The native store expands the
  # bias/snake vectors to F32. Sidecar dtype is not a GGUF storage declaration.
  @f16_vae "d4f4a05d8f291ae820cd1e43609da3fa91b56465810091a2b08c3350b751719d"
  @oobleck_channels [64, 64, 128, 256, 512, 1024, 2048]
  @oobleck_strides [2, 2, 4, 4, 5, 6]
  # ggml_new_graph_custom(..., 524288, false): nodes, leaves, use counts,
  # hash keys and bitset. Allocator/tensor metadata is covered by the margin,
  # not by charging the entire no_alloc virtual arena as resident memory.
  @graph_metadata 524_288 * 16 + 1_048_583 * 12 + div(1_048_583 + 63, 64) * 8

  def estimate(%{"kind" => "acoustic_decode"} = input) do
    acoustic = input["acoustic"] || %{}
    decoder = input["decoder"] || %{}
    frames = dimension!(acoustic["frames"])
    latent = dimension!(acoustic["latent_dim"])
    channels = dimension!(acoustic["channels"])
    encoder_latent = dimension!(acoustic["encoder_latent_dim"])
    ratio = dimension!(acoustic["downsampling_ratio"])
    core = dimension!(decoder["core_frames"] || acoustic["decode_core_frames"])
    halo = dimension!(decoder["halo_frames"] || acoustic["decode_halo_frames"], 0)
    source_bytes = dimension!(input["vae_bytes"])
    storage = decoder["storage"] || acoustic["vae_storage"]
    unless storage in 0..6, do: raise(ArgumentError, "Invalid decoder storage.")
    if frames * ratio <= 64, do: raise(ArgumentError, "Invalid decoder output dimensions.")

    # Yue2Pipeline::ensure_vae copies audio/latent dimensions but currently uses
    # Oobleck's native channel/stride defaults, even if a sidecar contains other
    # c_mults/strides. Do not invent support for a different native topology.
    layout = decoder_layout(encoder_latent, latent, channels)

    known_f16 =
      get_in(acoustic, ["hashes", "decoder"]) == @f16_vae and
        source_bytes == 265_218_656 and latent == 64 and encoder_latent == 128 and
        channels == 2

    # All supported quantized row blocks are wider than these 1..12-wide
    # convolution kernels; BackendWeightStore therefore materializes them F32.
    width = if storage in [2, 3] or (storage == 0 and known_f16), do: 2, else: 4
    weights = layout.convolutions * width + layout.vectors * 4
    weights = if storage == 0 and not known_f16, do: max(weights, source_bytes), else: weights
    full_latents = frames * latent * 4
    full_audio = max(frames * ratio - 64, native_sample_frames(frames)) * channels * 4

    tiles = tile_candidates(frames, core, halo)
    largest_tile = Enum.max(Enum.map(tiles, &elem(&1, 0)))

    graph = fn size -> decoder_graph(size, latent, channels, width, layout.transposes) end

    {graph_peak, graph_instances} =
      tiles
      |> Enum.map(fn {size, previous} ->
        if previous != nil and previous != size,
          do: {graph.(size) + graph.(previous), 2},
          else: {graph.(size), 1}
      end)
      |> Enum.max()

    # decode() retains planar and interleaved float audio during conversion;
    # the tiled caller retains the complete destination as well. The complete
    # frame-major latent tensor stays alive while each planar tile is packed.
    tile_latents = largest_tile * latent * 4
    tile_audio = native_sample_frames(largest_tile) * channels * 4 * 2
    buffers = full_latents + full_audio + tile_latents + tile_audio

    # Loading and decoding are successive stages. Direct F16 upload touches at
    # most the largest source tensor plus vector conversion. Precision changes
    # need raw/F32/conversion buffers. Unknown sources also allow all derived
    # weight-normalized F32 values pending upload; they remain usable, with a
    # conservative estimate instead of a guessed native-precision discount.
    upload =
      cond do
        known_f16 and storage in [0, 2] -> layout.largest * 2 + layout.vectors * 6
        known_f16 -> layout.largest * 10 + layout.vectors * 6
        true -> source_bytes + (layout.convolutions + layout.vectors) * 4 + layout.largest * 8
      end

    working = max(full_latents + upload, buffers + graph_peak)
    margin = max(0, input["margin"] || 0.2)
    peak = ceil((weights + working) * (1 + margin))
    cuda = input["backend"] == "cuda"

    %{
      "host_peak" => peak,
      "device_peak" => if(cuda, do: peak, else: 0),
      "device" => if(cuda, do: input["device"], else: nil),
      "basis" => %{
        "operation" => "acoustic_decode",
        "weights" => weights,
        "kv" => 0,
        "nar_scratch" => 0,
        "tokens" => 0,
        "branches" => 0,
        "cache_element_bytes" => 0,
        "working_tensors" => working,
        "uncertainty_margin" => margin,
        "decoder" => %{
          "source_bytes" => source_bytes,
          "storage" => storage,
          "convolution_element_bytes" => width,
          "known_f16_source" => known_f16,
          "frames" => frames,
          "tile_frames" => largest_tile,
          "core_frames" => core,
          "halo_frames" => halo,
          "full_latents" => full_latents,
          "full_audio" => full_audio,
          "tile_buffers" => tile_latents + tile_audio,
          "graph_peak" => graph_peak,
          "graph_instances" => graph_instances,
          "upload_workspace" => upload,
          "bound" => "native_oobleck_unfused",
          "measurement" => "unverified"
        }
      }
    }
  end

  def estimate(input) do
    config = input["config"] || %{}
    layers = config["num_hidden_layers"] || 28
    heads = config["num_key_value_heads"] || 8
    dimension = config["head_dim"] || 128
    hidden = config["hidden_size"] || heads * dimension
    context = input["context"] || config["max_position_embeddings"] || 24_576
    writer = input["kind"] == "writer"
    branches = if not writer and (input["cfg_scale"] || 1.0) != 1.0, do: 2, else: 1

    generated =
      cond do
        writer -> input["writer_tokens"] || 768
        input["render_mode"] == "plan" -> 0
        true -> ceil((input["seconds"] || 0) * (input["token_rate"] || 25))
      end

    planning = if input["planning"] == true, do: input["planning_tokens"] || context, else: 0
    # UTF-8 bytes bound byte-level text tokens without loading a second model.
    # Supplied token counts can tighten the estimate when a tokenizer provides
    # them; estimates never reduce the artist's requested context or output.
    prefix = input["prefix_tokens"] || input["text_bytes"] || 0
    tokens = min(context, max(1, prefix + planning + generated))
    # Native CPU semantic KV is F32. Device KV and the BF16 MLX writer use
    # two-byte elements. An explicit backend capability can refine this.
    element_bytes =
      input["cache_element_bytes"] || if(not writer and input["backend"] == "cpu", do: 4, else: 2)

    kv = tokens * layers * heads * dimension * 2 * element_bytes * branches
    # mlx_lm.generate_step uses a 2048-token prefill chunk. Native prefill can
    # span all conditioning tokens; long scores need scratch space as well as KV.
    frames =
      if writer,
        do: min(tokens, input["prefill_chunk"] || 2048),
        else: min(context, max(1, max(prefix + planning, generated)))

    # Simultaneously live residual, Q/K/V, gate/up and projection work arrays.
    tensors = frames * hidden * 4 * 12 * branches

    vae =
      if input["render_mode"] in ["plan", "performance"] or writer,
        do: 0,
        else: input["vae_bytes"] || 0

    decoder_frames = if vae > 0, do: min(generated, input["vae_chunk"] || generated), else: 0
    decode = decoder_frames * (input["sample_stride"] || 1920) * 2 * 4 * 4
    weights = input["model_bytes"] || 0
    margin = max(0, input["margin"] || 0.2)
    peak = ceil((weights + vae + kv + tensors + decode) * (1 + margin))
    cuda = input["backend"] == "cuda"

    %{
      "host_peak" => peak,
      "device_peak" => if(cuda, do: peak, else: 0),
      "device" => if(cuda, do: input["device"], else: nil),
      "basis" => %{
        "weights" => weights + vae,
        "kv" => kv,
        "working_tensors" => tensors + decode,
        "tokens" => tokens,
        "branches" => branches,
        "cache_element_bytes" => element_bytes,
        "uncertainty_margin" => margin
      }
    }
  end

  defp dimension!(value, minimum \\ 1)

  defp dimension!(value, minimum)
       when is_integer(value) and value >= minimum,
       do: value

  defp dimension!(_, _), do: raise(ArgumentError, "Invalid decoder dimensions.")

  defp decoder_layout(encoder_latent, latent, channels) do
    # Both encoder and decoder are bound by load_weights(), although only the
    # decoder graph runs. Each residual unit has 8*C*C convolution elements,
    # four snake vectors and two biases; each block has three residual units.
    initial = %{
      convolutions: 64 * channels * 14 + encoder_latent * 2048 * 3 + 2048 * latent * 7,
      vectors: 64 + encoder_latent + 2048 + 2 * 2048 + 2 * 64,
      transposes: 0,
      largest: max(64 * channels * 7, max(encoder_latent * 2048 * 3, 2048 * latent * 7))
    }

    Enum.zip([@oobleck_channels, tl(@oobleck_channels), @oobleck_strides])
    |> Enum.reduce(initial, fn {lower, upper, stride}, acc ->
      transpose = lower * upper * 2 * stride

      %{
        convolutions: acc.convolutions + 2 * (24 * lower * lower + transpose),
        vectors: acc.vectors + 36 * lower + 3 * lower + 3 * upper,
        transposes: acc.transposes + transpose,
        largest: max(acc.largest, max(transpose, 7 * lower * lower))
      }
    end)
  end

  defp decoder_graph(frames, latent, channels, width, transpose_elements) do
    initial = {frames, 2048 * frames, 7 * latent * frames * width}

    {output_frames, features, columns} =
      Enum.zip([@oobleck_channels, tl(@oobleck_channels), @oobleck_strides])
      |> Enum.reverse()
      |> Enum.reduce(initial, fn {lower, _upper, stride}, {count, features, columns} ->
        expanded = count * stride + stride
        cropped = expanded - 2 * div(stride + 1, 2)
        feature_peak = max(features, lower * expanded)

        column_peak =
          max(columns, max(2 * stride * lower * count * 4, 7 * lower * cropped * width))

        {cropped, feature_peak, column_peak}
      end)

    # Four F32 feature arrays bound residual/skip + snake/bias intermediates.
    # Regular conv im2col uses the effective weight dtype. Device transposed
    # conv uses F32 columns and a contiguous weight permutation, plus a cast
    # for 16-bit weights. Count all such weight views, allowing DFS retention.
    views = transpose_elements * 4 * if(width == 2, do: 2, else: 1)
    pinned = frames * latent * 4 + output_frames * channels * 4
    @graph_metadata + views + max(features, output_frames * channels) * 4 * 4 + columns + pinned
  end

  defp native_sample_frames(frames) do
    Enum.reduce(Enum.reverse(@oobleck_strides), frames, fn stride, count ->
      count * stride + stride - 2 * div(stride + 1, 2)
    end)
  end

  defp tile_candidates(frames, core, halo) do
    count = div(frames + core - 1, core)

    # Tile length is piecewise linear with two clamp points. Its maximum and
    # every adjacent-shape transition are bounded at those points/endpoints;
    # evaluate their neighbours rather than loop over the duration of a song.
    indices = [0, count - 1, div(halo, core), div(max(0, frames - halo), core) - 1]

    indices
    |> Enum.flat_map(fn index -> Enum.map(-2..2, &(index + &1)) end)
    |> Enum.filter(&(&1 >= 0 and &1 < count))
    |> Enum.uniq()
    |> Enum.map(fn index ->
      size = tile_size(index, frames, core, halo)
      previous = if index > 0, do: tile_size(index - 1, frames, core, halo)
      {size, previous}
    end)
  end

  defp tile_size(index, frames, core, halo) do
    min(frames, min(frames, (index + 1) * core) + halo) - max(0, index * core - halo)
  end
end
