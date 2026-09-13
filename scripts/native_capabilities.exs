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

    for mode <- ["help", "inspect"] do
      arguments = ["--family", "yue2", "--model", model, "--" <> mode]
      {text, status} = System.cmd(binary, arguments, stderr_to_stdout: true)
      File.write!(Path.join(root, "native-#{mode}.log"), text)
      unless status == 0, do: raise("Native #{mode} failed with status #{status}")
      parse!(text, expected)

      if mode == "help" and not String.contains?(text, "score_tokens_file"),
        do: raise("Native help does not expose score_tokens_file")

      if mode == "inspect" and not String.contains?(text, "\nweights=0\n"),
        do: raise("Capability inspection unexpectedly discovered model weights")
    end

    expected
  end
end
