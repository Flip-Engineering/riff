defmodule Riff.Runtime.AcousticArtifact do
  @moduledoc """
  Owned, immutable references to completed acoustic synthesis.

  Copies remain independent of engine outputs and queued inputs. Inspection and
  copying stream through files; the library never materializes the latent tensor.
  A decoder contract is optional for historical inspection and required for use.
  """

  alias Riff.Runtime.AcousticCheckpoint, as: Checkpoint
  @prefix "riff-acoustic-v1:"

  defmodule Error do
    defexception [:message]
  end

  def capture_file!(root, source_root, source, provenance) when is_map(provenance) do
    root = directory!(root)
    metadata = Checkpoint.inspect!(source_root, source)
    descriptor = %{"format_version" => 1, "acoustic" => metadata, "provenance" => provenance}
    encoded = Jason.encode!(descriptor)
    id = @prefix <> digest(encoded)
    final = artifact_path!(root, id)

    case File.lstat(final) do
      {:ok, %{type: :directory}} ->
        result = resolve!(root, id)
        Riff.Installer.Lock.sync_directory!(root)
        result

      {:error, :enoent} ->
        staging =
          Path.join(
            root,
            ".staging-" <> Base.encode16(:crypto.strong_rand_bytes(16), case: :lower)
          )

        File.mkdir!(staging)
        owned = File.lstat!(staging)

        try do
          copy!(source, staging, Path.join(staging, "acoustic.yac"), metadata)
          write_new!(Path.join(staging, "descriptor.json"), encoded)
          Riff.Installer.Lock.sync_directory!(staging)

          case File.rename(staging, final) do
            :ok ->
              :ok

            {:error, reason} when reason in [:eexist, :enotempty] ->
              resolve!(root, id)

            {:error, reason} ->
              raise File.Error, reason: reason, action: "publish sound", path: final
          end

          Riff.Installer.Lock.sync_directory!(root)
          resolve!(root, id)
        after
          cleanup_owned!(root, staging, owned)
        end

      _ ->
        fail!("The saved sound location is linked or unavailable.")
    end
  end

  def resolve!(root, id, decoder \\ nil) do
    directory = root |> directory!() |> artifact_path!(id) |> directory!()
    path = Path.join(directory, "acoustic.yac")
    descriptor_path = Path.join(directory, "descriptor.json")
    regular!(descriptor_path)
    encoded = File.read!(descriptor_path)
    check!(@prefix <> digest(encoded) == id, "The saved sound descriptor has changed.")
    descriptor = Jason.decode!(encoded)
    check!(descriptor["format_version"] == 1, "The saved sound descriptor is unsupported.")
    metadata = Checkpoint.inspect!(root, path)
    check!(descriptor["acoustic"] == metadata, "The saved sound differs from its library record.")

    check!(
      is_nil(decoder) or Checkpoint.compatible?(metadata, decoder),
      "This saved sound needs its compatible audio decoder."
    )

    %{"id" => id, "path" => path, "descriptor" => descriptor}
  end

  def prepare!(root, id, decoder, input_directory) when is_map(decoder) do
    artifact = resolve!(root, id, decoder)
    input_directory = directory!(input_directory)
    input = Path.join(input_directory, "acoustic.yac")
    copy!(artifact["path"], input_directory, input, artifact["descriptor"]["acoustic"])
    Riff.Installer.Lock.sync_directory!(input_directory)
    Map.put(artifact, "input_path", input)
  end

  defp copy!(source, root, target, expected) do
    regular!(source)

    case File.lstat(target) do
      {:ok, _} ->
        verify_copy!(source, root, target, expected)

      {:error, :enoent} ->
        temporary =
          Path.join(
            root,
            ".acoustic-" <> Base.encode16(:crypto.strong_rand_bytes(16), case: :lower)
          )

        {:ok, output} = File.open(temporary, [:write, :exclusive, :binary, :raw])
        owned = regular!(temporary)

        try do
          try do
            copy_stream!(source, output, expected["bytes"])
          after
            File.close(output)
          end

          verify_copy!(source, root, temporary, expected)

          # link(2) publishes the fully written inode without replacing a
          # concurrent winner or reserving a partial final input name.
          case File.ln(temporary, target) do
            :ok ->
              check!(identity(regular!(target)) == identity(owned), "The sound copy has changed.")

            {:error, :eexist} ->
              verify_copy!(source, root, target, expected)

            {:error, reason} ->
              raise File.Error, reason: reason, action: "publish sound input", path: target
          end
        after
          cleanup_owned!(root, temporary, owned)
        end

      {:error, reason} ->
        raise File.Error, reason: reason, action: "prepare sound", path: target
    end
  end

  defp copy_stream!(source, output, expected_bytes) do
    {:ok, input} = File.open(source, [:read, :binary, :raw])

    try do
      case :file.copy(input, output) do
        {:ok, count} -> check!(count == expected_bytes, "The sound copy is incomplete.")
        {:error, reason} -> raise File.Error, reason: reason, action: "copy sound", path: source
      end
    after
      File.close(input)
    end
  end

  defp verify_copy!(source, root, target, expected) do
    check!(Checkpoint.inspect!(root, target) == expected, "The sound copy has changed.")
    original = regular!(source)
    copied = regular!(target)

    check!(
      {original.major_device, original.inode} != {copied.major_device, copied.inode},
      "The generation input aliases the saved sound."
    )

    # Retry after copy-before-directory-sync still persists the validated input.
    {:ok, file} = File.open(target, [:read, :binary, :raw])

    try do
      :ok = :file.sync(file)
    after
      File.close(file)
    end
  end

  defp cleanup_owned!(root, path, owned) do
    # A killed invocation may leave its own random sibling behind. A later
    # invocation neither reuses nor sweeps it; only this creator reclaims it.
    directory!(root)

    case File.lstat(path) do
      {:ok, current} ->
        if identity(current) == identity(owned) do
          case current.type do
            :directory -> File.rm_rf!(path)
            :regular -> File.rm!(path)
          end

          Riff.Installer.Lock.sync_directory!(root)
        end

      {:error, :enoent} ->
        :ok

      {:error, reason} ->
        raise File.Error, reason: reason, action: "reclaim sound staging", path: path
    end
  end

  defp identity(info), do: {info.type, info.major_device, info.inode}

  defp write_new!(path, bytes) do
    {:ok, file} = File.open(path, [:write, :exclusive, :binary, :raw])

    try do
      :ok = :file.write(file, bytes)
      :ok = :file.sync(file)
    after
      File.close(file)
    end
  end

  defp artifact_path!(root, @prefix <> hash) do
    check!(Regex.match?(~r/\A[a-f0-9]{64}\z/, hash), "The saved sound reference is invalid.")
    Path.join(root, hash)
  end

  defp artifact_path!(_, _), do: fail!("The saved sound reference is invalid.")

  defp directory!(path) do
    check!(Path.expand(path) == path, "The sound library path is not canonical.")

    case File.lstat(path) do
      {:ok, %{type: :directory}} -> :ok
      _ -> fail!("The sound library directory is linked or unavailable.")
    end

    parent = Path.dirname(path)
    if parent != path, do: directory!(parent)
    path
  end

  defp regular!(path) do
    case File.lstat(path) do
      {:ok, %{type: :regular} = info} -> info
      _ -> fail!("The saved sound is linked or unavailable.")
    end
  end

  defp digest(bytes), do: :crypto.hash(:sha256, bytes) |> Base.encode16(case: :lower)
  defp check!(true, _), do: :ok
  defp check!(_, message), do: fail!(message)
  defp fail!(message), do: raise(Error, message)
end
