defmodule Riff.Installer.RustNotices do
  @moduledoc false

  # rustc's minimal rustup component includes these notices under share/doc/rust.
  # Homebrew places the same generated notices under share/doc/rustc instead.
  # Preserve their relative paths, including the accompanying REUSE texts.
  def copy!(sysroot, destination) do
    sysroot = Path.expand(sysroot)

    layout =
      Enum.find(["share/doc/rust", "share/doc/rustc"], fn relative ->
        File.exists?(Path.join([sysroot, relative, "COPYRIGHT-library.html"]))
      end) || raise("Rust standard-library copyright notices were not found in this toolchain.")

    for relative <- [
          "COPYRIGHT.html",
          "COPYRIGHT-library.html",
          "licenses/Apache-2.0.txt",
          "licenses/MIT.txt"
        ] do
      regular!(sysroot, Path.join(layout, relative))
    end

    paths =
      notice_files(sysroot, "") ++
        notice_files(sysroot, layout) ++
        license_tree!(sysroot, Path.join(layout, "licenses"))

    files =
      paths
      |> Enum.uniq()
      |> Enum.sort()
      |> Enum.map(fn relative ->
        source = regular!(sysroot, relative)
        bytes = File.read!(source)

        %{
          "path" => relative,
          "bytes" => byte_size(bytes),
          "sha256" => hash(bytes)
        }
      end)

    for item <- files do
      target = Path.join(destination, item["path"])
      File.mkdir_p!(Path.dirname(target))
      File.cp!(regular!(sysroot, item["path"]), target)

      unless File.stat!(target).size == item["bytes"] and
               hash(File.read!(target)) == item["sha256"],
             do: raise("Rust notices changed while preparing the package.")
    end

    %{"format_version" => 1, "layout" => layout, "files" => files}
  end

  defp notice_files(root, relative) do
    path = Path.join(root, relative)

    File.ls!(path)
    |> Enum.filter(
      &Regex.match?(~r/\A(LICENSE|LICENCE|COPYING|COPYRIGHT|NOTICE)([.-].*)?\z/i, &1)
    )
    |> Enum.map(&Path.join(relative, &1))
  end

  defp license_tree!(root, relative) do
    path = owned!(root, relative)

    case File.lstat!(path).type do
      :directory ->
        File.ls!(path)
        |> Enum.sort()
        |> Enum.flat_map(&license_tree!(root, Path.join(relative, &1)))

      :regular ->
        [relative]

      _ ->
        raise "Rust license notices must be regular files."
    end
  end

  defp regular!(root, relative) do
    path = owned!(root, relative)

    unless File.lstat!(path).type == :regular and File.stat!(path).size > 0,
      do: raise("Rust notice is missing or empty: #{relative}")

    path
  end

  defp owned!(root, relative) do
    unless Path.type(relative) == :relative and
             Enum.all?(Path.split(relative), &(&1 not in [".", "..", ""])) do
      raise "Invalid Rust notice path."
    end

    Enum.reduce(Path.split(relative), root, fn part, parent ->
      path = Path.join(parent, part)

      case File.lstat(path) do
        {:ok, %{type: :symlink}} -> raise "Rust license notices must not follow symbolic links."
        {:ok, _} -> path
        {:error, _} -> raise "Rust notice is missing: #{relative}"
      end
    end)
  end

  defp hash(bytes), do: :crypto.hash(:sha256, bytes) |> Base.encode16(case: :lower)
end
