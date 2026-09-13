defmodule Riff.Native.Capabilities do
  @moduledoc "Build-time inspection of the native engine, without a backend or model weights."

  def fixture!(root) do
    model = Path.join(root, "metadata-model")
    sidecars = Path.join(model, "sidecars")
    File.mkdir_p!(sidecars)

    config = %{
      "hidden_size" => 1,
      "num_hidden_layers" => 1,
      "num_attention_heads" => 1,
      "num_key_value_heads" => 1,
      "head_dim" => 1,
      "intermediate_size" => 1,
      "vocab_size" => 1,
      "max_position_embeddings" => 1
    }

    File.write!(Path.join(sidecars, "yue2-model-config.json"), :json.encode(config))
    File.write!(Path.join(sidecars, "yue2-vae-config.json"), "{}\n")
    # Inspection requires an asset path, but never reads a tokenizer or GGUF.
    File.write!(Path.join(sidecars, "yue2-qwen.tiktoken"), "metadata fixture; not a tokenizer\n")
    model
  end

  def decoder_fixture!(root) do
    model = Path.join(root, "metadata-vae-only")
    sidecars = Path.join(model, "sidecars")
    File.mkdir_p!(sidecars)
    File.write!(Path.join(sidecars, "yue2-vae-config.json"), "{}\n")
    model
  end

  def parse!(text, expected) when is_map(expected) and map_size(expected) > 0 do
    entries =
      text
      |> String.split("\n")
      |> Enum.flat_map(fn line ->
        case String.split(line, "=", parts: 2) do
          [key, value] when is_map_key(expected, key) -> [{key, value}]
          _ -> []
        end
      end)

    if length(entries) != map_size(expected) or Map.new(entries) != expected,
      do: raise("Native capability metadata is missing, duplicated or incompatible")

    expected
  end

  def verify!(binary, expected, root) do
    model = fixture!(root)
    acoustic? = expected["feature.yue2.acoustic_decode"] == "1"

    fixtures =
      [{"native", model}] ++
        if(acoustic?, do: [{"native-vae-only", decoder_fixture!(root)}], else: [])

    for {label, model} <- fixtures, mode <- ["help", "inspect"] do
      arguments = ["--family", "yue2", "--model", model, "--" <> mode]
      {text, status} = System.cmd(binary, arguments, stderr_to_stdout: true)
      File.write!(Path.join(root, "#{label}-#{mode}.log"), text)
      unless status == 0, do: raise("Native #{mode} failed with status #{status}")
      parse!(text, expected)

      if mode == "help" and not String.contains?(text, "score_tokens_file"),
        do: raise("Native help does not expose score_tokens_file")

      if acoustic? and mode == "help" do
        for option <-
              ~w(acoustic_latents_out acoustic_only acoustic_latents_file acoustic_decode_core_frames acoustic_decode_halo_frames) do
          unless String.contains?(text, option),
            do: raise("Native help does not expose #{option}")
        end
      end

      if mode == "inspect" and not String.contains?(text, "\nweights=0\n"),
        do: raise("Capability inspection unexpectedly discovered model weights")

      if label == "native-vae-only" and mode == "inspect" and
           not String.contains?(text, "\nconfigs=1\n"),
         do: raise("Decoder discovery unexpectedly required main-model metadata")
    end

    expected
  end
end
