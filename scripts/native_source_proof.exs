defmodule Riff.Native.SourceProof do
  @moduledoc "Verify every exported native input against the exact pinned Git index."
  # Compare against a separate Git index, never the developer's real index.
  # This also works with the verified source export used by desktop builds.
  def verify_tree!(repository, index_env, source) do
    {entries, 0} =
      System.cmd("git", ["-C", repository, "ls-files", "--stage", "-z"], env: index_env)

    for entry <- String.split(entries, <<0>>, trim: true) do
      [metadata, relative] = String.split(entry, "\t", parts: 2)
      [mode, expected, "0"] = String.split(metadata, " ")
      path = Path.join(source, relative)

      digest =
        case {mode, File.lstat!(path).type} do
          {"120000", :symlink} ->
            body = File.read_link!(path)
            :crypto.hash(:sha, ["blob #{byte_size(body)}", <<0>>, body])

          {mode, :regular} when mode in ["100644", "100755"] ->
            state =
              :crypto.hash_init(:sha)
              |> :crypto.hash_update(["blob #{File.stat!(path).size}", <<0>>])

            File.stream!(path, 1_048_576)
            |> Enum.reduce(state, &:crypto.hash_update(&2, &1))
            |> :crypto.hash_final()

          _ ->
            raise("Unexpected source entry: #{relative}")
        end

      matches = Base.encode16(digest, case: :lower) == expected

      # checkout-index follows the pinned attributes: Windows .cmd/.bat files
      # contain CRLF even though Git stores their blobs with LF. Allow only that
      # explicitly declared, complete checkout transformation.
      matches =
        matches or
          case System.cmd(
                 "git",
                 ["-C", repository, "check-attr", "--cached", "-z", "eol", "--", relative],
                 env: index_env
               ) do
            {attributes, 0}
            when attributes == relative <> <<0>> <> "eol" <> <<0>> <> "crlf" <> <<0>> ->
              body = File.read!(path)
              normalized = :binary.replace(body, "\r\n", "\n", [:global])
              canonical = :binary.replace(normalized, "\n", "\r\n", [:global])

              canonical == body and
                Base.encode16(
                  :crypto.hash(:sha, ["blob #{byte_size(normalized)}", <<0>>, normalized]),
                  case: :lower
                ) == expected

            _ ->
              false
          end

      unless matches,
        do: raise("Source differs from the pinned patch set: #{relative}")
    end
  end
end
