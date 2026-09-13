defmodule Riff.Runtime.ScoreArtifact do
  @moduledoc """
  Immutable native score files. References identify exact descriptor bytes;
  notation is a display/editing view, never a substitute for saved token IDs.

  `root` is the resolved, owned score-artifact directory. Capture receives bytes
  already read from the job's owned output; the caller binds launch provenance.
  These operations do not start a runtime, inspect audio, or change a recipe.
  """

  @prefix "riff-score-v1:"
  @format "riff.yue2.score-tokens.v1"
  @token_limit 151_643

  defmodule Error do
    defexception [:message]
  end

  @doc "Validate exact JSON token types while retaining the original file bytes."
  def parse!(bytes) when is_binary(bytes) do
    case Jason.decode(bytes, objects: :ordered_objects) do
      {:ok, %Jason.OrderedObject{values: fields}} ->
        names = Enum.map(fields, &elem(&1, 0))

        check!(
          length(names) == MapSet.size(MapSet.new(names)),
          "The saved score has duplicate fields."
        )

        fields = Map.new(fields)
        tokens = fields["tokens"]
        truncated = Map.get(fields, "truncated", false)

        check!(
          is_list(tokens) and
            Enum.all?(tokens, &(is_integer(&1) and &1 >= 0 and &1 < @token_limit)),
          "The saved score contains invalid token IDs."
        )

        check!(is_boolean(truncated), "The saved score has an invalid completion flag.")
        %{"token_count" => length(tokens), "truncated" => truncated}

      _ ->
        fail!("The saved score is incomplete or invalid.")
    end
  end

  @doc "Publish a complete artifact, retaining incomplete staging files for recovery."
  def capture!(root, bytes, contract, provenance) when is_binary(bytes) and is_map(provenance) do
    root = directory!(root)
    contract = contract!(contract)
    summary = parse!(bytes)

    descriptor = %{
      "format_version" => 1,
      "score" => Map.merge(summary, %{"sha256" => digest(bytes), "bytes" => byte_size(bytes)}),
      "contract" => contract,
      "provenance" => provenance
    }

    encoded = Jason.encode!(descriptor)
    id = @prefix <> digest(encoded)
    final = artifact_path!(root, id)

    case File.lstat(final) do
      {:ok, %{type: :directory}} ->
        # Repeated capture is idempotent, but existing changed bytes are never
        # repaired silently or replaced by a new capture of the same reference.
        artifact = resolve!(root, id, contract)
        # Finish a prior rename-before-parent-sync interruption on retry too.
        Riff.Installer.Lock.sync_directory!(root)
        artifact

      {:error, :enoent} ->
        staging =
          Path.join(
            root,
            ".staging-" <> Base.encode16(:crypto.strong_rand_bytes(16), case: :lower)
          )

        File.mkdir!(staging)
        write_new!(Path.join(staging, "score.json"), bytes)
        write_new!(Path.join(staging, "descriptor.json"), encoded)
        Riff.Installer.Lock.sync_directory!(staging)

        case File.rename(staging, final) do
          :ok ->
            :ok

          {:error, reason} when reason in [:eexist, :enotempty] ->
            resolve!(root, id, contract)

          {:error, reason} ->
            raise File.Error, reason: reason, action: "publish score", path: final
        end

        Riff.Installer.Lock.sync_directory!(root)
        resolve!(root, id, contract)

      _ ->
        fail!("The saved score location is not an owned directory.")
    end
  end

  @doc "Capture only a regular file inside the caller's canonical owned output root."
  def capture_file!(root, source_root, source_path, contract, provenance) do
    source_root = directory!(source_root)

    check!(
      is_binary(source_path) and Path.expand(source_path) == source_path and
        String.starts_with?(source_path, source_root <> "/"),
      "The score output is outside its owned job directory."
    )

    directory!(Path.dirname(source_path))
    capture!(root, regular_bytes!(source_path), contract, provenance)
  end

  @doc "Verify descriptor and raw bytes against the selected tokenizer and prefix format."
  def resolve!(root, id, contract) do
    root = directory!(root)
    selected_contract = if is_nil(contract), do: nil, else: contract!(contract)
    directory = artifact_path!(root, id) |> directory!()
    encoded = regular_bytes!(Path.join(directory, "descriptor.json"))
    check!(@prefix <> digest(encoded) == id, "The saved score descriptor has changed.")
    descriptor = Jason.decode!(encoded)
    check!(descriptor["format_version"] == 1, "The saved score format is not supported.")
    recorded_contract = contract!(descriptor["contract"])

    check!(
      is_nil(selected_contract) or recorded_contract == selected_contract,
      "This score uses a different tokenizer or score format."
    )

    bytes = regular_bytes!(Path.join(directory, "score.json"))
    summary = parse!(bytes)
    expected = Map.merge(summary, %{"sha256" => digest(bytes), "bytes" => byte_size(bytes)})
    check!(descriptor["score"] == expected, "The saved score bytes have changed.")
    %{"id" => id, "descriptor" => descriptor, "path" => Path.join(directory, "score.json")}
  end

  @doc "Create or verify an independent input for an owned job; retries retain the same bytes."
  def prepare!(root, id, contract, input_directory) do
    contract = contract!(contract)
    artifact = resolve!(root, id, contract)
    input_directory = directory!(input_directory)
    target = Path.join(input_directory, "score.json")
    bytes = regular_bytes!(artifact["path"])
    # Recheck the bytes used for the copy, not only an earlier resolution read.
    check!(
      digest(bytes) == artifact["descriptor"]["score"]["sha256"],
      "The saved score changed before generation."
    )

    existing_input? =
      case File.open(target, [:write, :exclusive, :binary, :raw]) do
        {:ok, file} ->
          write_open!(file, bytes)
          false

        {:error, :eexist} ->
          true

        {:error, reason} ->
          raise File.Error, reason: reason, action: "prepare score", path: target
      end

    check!(
      digest(regular_bytes!(target)) == digest(bytes),
      "The generation's score copy has changed."
    )

    original = File.lstat!(artifact["path"])
    copied = File.lstat!(target)

    check!(
      original.inode != copied.inode or original.major_device != copied.major_device,
      "The generation input aliases its saved score."
    )

    # A retry after write-before-sync must persist the file, not only verify
    # its currently visible bytes. Directory sync alone cannot do that.
    if existing_input? do
      {:ok, file} = File.open(target, [:read, :binary, :raw])

      try do
        :ok = :file.sync(file)
      after
        File.close(file)
      end
    end

    Riff.Installer.Lock.sync_directory!(input_directory)
    Map.put(artifact, "input_path", target)
  end

  defp contract!(
         %{"format" => @format, "tokenizer_sha256" => sha, "prefix_contract" => prefix} = contract
       )
       when is_binary(sha) and is_binary(prefix) and byte_size(prefix) > 0 do
    check!(Regex.match?(~r/\A[0-9a-f]{64}\z/, sha), "The score tokenizer identity is invalid.")
    check!(map_size(contract) == 3, "The score contract contains unsupported fields.")
    contract
  end

  defp contract!(_), do: fail!("The score contract is incomplete or unsupported.")

  defp artifact_path!(root, @prefix <> hash) do
    check!(Regex.match?(~r/\A[0-9a-f]{64}\z/, hash), "The saved score reference is invalid.")
    Path.join(root, hash)
  end

  defp artifact_path!(_, _), do: fail!("The saved score reference is invalid.")

  # The caller supplies a canonical owned root. Check each existing component
  # rather than accepting a linked descendant or normalizing traversal away.
  defp directory!(path) when is_binary(path) do
    check!(
      Path.type(path) == :absolute and Path.expand(path) == path,
      "The score directory must be an absolute owned path."
    )

    [anchor | parts] = Path.split(path)
    check!(File.lstat!(anchor).type == :directory, "The score filesystem is not a directory.")

    Enum.reduce(parts, anchor, fn part, parent ->
      current = Path.join(parent, part)

      check!(
        File.lstat!(current).type == :directory,
        "A score directory is linked or is not a directory."
      )

      current
    end)
  end

  defp regular_bytes!(path) do
    before = File.lstat!(path)
    check!(before.type == :regular, "The saved score is not a regular file.")
    bytes = File.read!(path)
    after_read = File.lstat!(path)

    check!(
      after_read.type == :regular and before.inode == after_read.inode and
        before.major_device == after_read.major_device and before.size == byte_size(bytes) and
        after_read.size == before.size,
      "The saved score changed while it was being read."
    )

    bytes
  end

  defp write_new!(path, bytes) do
    {:ok, file} = File.open(path, [:write, :exclusive, :binary, :raw])
    write_open!(file, bytes)
  end

  defp write_open!(file, bytes) do
    try do
      :ok = IO.binwrite(file, bytes)
      :ok = :file.sync(file)
    after
      File.close(file)
    end
  end

  defp digest(bytes), do: :crypto.hash(:sha256, bytes) |> Base.encode16(case: :lower)
  defp check!(true, _message), do: :ok
  defp check!(_, message), do: fail!(message)
  defp fail!(message), do: raise(Error, message: message)
end
