defmodule Riff.Installer.Manifest do
  @moduledoc "Validated, release-pinned model assets. The source pins are embedded in the OTP release."

  @source_path Path.expand("../../../../sources.json", __DIR__)
  @external_resource @source_path
  @pinned @source_path |> File.read!() |> Jason.decode!() |> Map.fetch!("model")

  def pinned, do: @pinned

  def assets(manifest \\ @pinned, options \\ []) do
    base_url = Keyword.get(options, :base_url, "https://huggingface.co")
    repo = Map.fetch!(manifest, "repo")
    revision = Map.fetch!(manifest, "revision")

    unless Regex.match?(~r/\A[^\/\s]+\/[^\/\s]+\z/, repo) and
             Regex.match?(~r/\A[0-9a-f]{40}\z/, revision) do
      raise ArgumentError, "The model source must name a repository and an immutable revision."
    end

    files = Map.fetch!(manifest, "files")

    unless is_map(files) and map_size(files) > 0,
      do: raise(ArgumentError, "No model assets are pinned.")

    files
    |> Enum.sort_by(fn {name, _} -> {not String.ends_with?(name, ".gguf"), name} end)
    |> Enum.map(fn {name, expected} ->
      validate_path!(name)
      bytes = Map.fetch!(expected, "bytes")
      sha256 = Map.fetch!(expected, "sha256")

      unless is_integer(bytes) and bytes > 0 and is_binary(sha256) and
               Regex.match?(~r/\A[0-9a-f]{64}\z/, sha256) do
        raise ArgumentError, "Every model asset needs a positive size and a SHA-256 checksum."
      end

      %{
        id: name,
        label: label(name),
        bytes: bytes,
        sha256: sha256,
        url: "#{base_url}/#{repo}/resolve/#{revision}/#{name}"
      }
    end)
  end

  def validate_path!(path) when is_binary(path) do
    segments = String.split(path, "/")

    unless Path.type(path) == :relative and
             Enum.all?(
               segments,
               &(&1 not in ["", ".", ".."] and not String.contains?(&1, ["\\", "\0", ":"]))
             ) do
      raise ArgumentError, "A model asset has an unsafe path."
    end

    path
  end

  def validate_path!(_), do: raise(ArgumentError, "A model asset path must be text.")

  defp label("yue2-3b-q4_0.gguf"), do: "YuE2 music model"
  defp label("yue2-vae-f16.gguf"), do: "Audio decoder"
  defp label("sidecars/yue2-qwen.tiktoken"), do: "Music vocabulary"
  defp label("sidecars/" <> name), do: String.replace_suffix(name, ".json", "")
  defp label(name), do: name
end
