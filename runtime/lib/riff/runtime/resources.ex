defmodule Riff.Runtime.Resources do
  @moduledoc """
  Architecture-based peak reservations for YuE2 and its local writer.

  These are conservative estimates, not measured promises. Weight bytes come
  from the installed files; KV growth comes from the actual layer/head layout,
  token budget and CFG branches. Native decoding uses its configured VAE chunk
  size. A configurable uncertainty margin covers allocator and backend scratch
  space until a measured peak demonstrates that more headroom is necessary.
  """

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
end
