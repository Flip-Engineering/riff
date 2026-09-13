defmodule Riff.Desktop.Build do
  @moduledoc "Build-time packaging helpers. Nothing here is required on an artist's computer."
  import Bitwise
  @root Path.expand(__DIR__)
  def root, do: @root
  def components, do: json_read(Path.join(@root, "components.json"))
  def json_read(path), do: path |> File.read!() |> :json.decode()
  def json_write(path, value), do: File.write!(path, [:json.encode(value), "\n"])

  def run!(command, arguments, options \\ []) do
    case System.cmd(command, arguments, Keyword.merge([stderr_to_stdout: true], options)) do
      {output, 0} -> output
      {output, status} -> raise "#{Path.basename(command)} failed (#{status}):\n#{output}"
    end
  end

  def hash(path) do
    path
    |> File.stream!(1_048_576)
    |> Enum.reduce(:crypto.hash_init(:sha256), &:crypto.hash_update(&2, &1))
    |> :crypto.hash_final()
    |> Base.encode16(case: :lower)
  end

  def verify!(path, bytes, digest) do
    unless File.regular?(path) and File.stat!(path).size == bytes and hash(path) == digest,
      do: raise("Component verification failed: #{Path.basename(path)}")

    path
  end

  def fetch!(name) do
    item = Map.fetch!(components(), name)
    directory = Path.join(@root, ".cache")
    File.mkdir_p!(directory)
    path = Path.join(directory, item["archive"])

    unless File.exists?(path) do
      temporary = path <> ".download-" <> Integer.to_string(System.unique_integer([:positive]))
      # curl is a build-host utility. The delivered Elixir installer downloads its own models.
      run!("curl", [
        "--fail",
        "--location",
        "--silent",
        "--show-error",
        "--proto",
        "=https",
        "--output",
        temporary,
        item["url"]
      ])

      verify!(temporary, item["bytes"], item["sha256"])
      File.rename!(temporary, path)
    end

    verify!(path, item["bytes"], item["sha256"])
  end

  def unpack!(archive, target) do
    if File.exists?(target), do: raise("Use a fresh extraction destination: #{target}")
    File.mkdir_p!(target)
    run!("tar", ["-xf", archive, "-C", target])
    target
  end

  def regular_files(root), do: files(root, root)

  defp files(directory, root) do
    directory
    |> File.ls!()
    |> Enum.sort()
    |> Enum.flat_map(fn name ->
      path = Path.join(directory, name)

      case File.lstat!(path).type do
        :directory -> files(path, root)
        :regular -> [Path.relative_to(path, root)]
        _ -> raise "Payload must contain only regular files: #{Path.relative_to(path, root)}"
      end
    end)
  end

  def copy_tree!(source, target),
    do: copy_entry!(Path.expand(source), target, Path.expand(source), MapSet.new())

  defp copy_entry!(source, target, root, visited) do
    if MapSet.member?(visited, source), do: raise("Cyclic component link")
    visited = MapSet.put(visited, source)

    case File.lstat!(source).type do
      :directory ->
        File.mkdir_p!(target)

        for name <- File.ls!(source),
            do: copy_entry!(Path.join(source, name), Path.join(target, name), root, visited)

      :regular ->
        File.mkdir_p!(Path.dirname(target))
        File.cp!(source, target)
        File.chmod!(target, if((File.stat!(source).mode &&& 0o111) != 0, do: 0o755, else: 0o644))

      :symlink ->
        link = File.read_link!(source)
        resolved = Path.expand(link, Path.dirname(source))

        unless resolved == root or String.starts_with?(resolved, root <> "/"),
          do: raise("Component link leaves its archive")

        copy_entry!(resolved, target, root, visited)

      _ ->
        raise "Unsupported component file"
    end
  end

  def records(root) do
    for relative <- regular_files(root) do
      path = Path.join(root, relative)
      stat = File.stat!(path)

      %{
        "path" => relative,
        "bytes" => stat.size,
        "sha256" => hash(path),
        "executable" => (stat.mode &&& 0o111) != 0
      }
    end
  end

  def archive!(root, destination, epoch) do
    if File.exists?(destination), do: raise("Release archive already exists")
    paths = regular_files(root)
    temporary = destination <> ".building"
    if File.exists?(temporary), do: raise("Archive staging path already exists")
    File.mkdir_p!(Path.dirname(destination))
    {:ok, tar} = :erl_tar.open(String.to_charlist(temporary), [:write, :compressed])

    try do
      for relative <- paths do
        :ok =
          :erl_tar.add(
            tar,
            String.to_charlist(Path.join(root, relative)),
            String.to_charlist(relative),
            chunks: 1_048_576,
            uid: 0,
            gid: 0,
            atime: epoch,
            mtime: epoch,
            ctime: epoch
          )
      end
    after
      :ok = :erl_tar.close(tar)
    end

    File.rename!(temporary, destination)
    destination
  end

  def native_files(root) do
    Enum.filter(regular_files(root), fn path ->
      {:ok, file} = File.open(Path.join(root, path), [:read, :binary])
      prefix = IO.binread(file, 4)
      File.close(file)

      prefix in [
        <<0xFE, 0xED, 0xFA, 0xCE>>,
        <<0xFE, 0xED, 0xFA, 0xCF>>,
        <<0xCE, 0xFA, 0xED, 0xFE>>,
        <<0xCF, 0xFA, 0xED, 0xFE>>,
        <<0xCA, 0xFE, 0xBA, 0xBE>>
      ]
    end)
  end

  def inspect_macos!(root, options \\ []) do
    root = Path.expand(root)

    for relative <- native_files(root) do
      path = Path.join(root, relative)
      listing = run!("/usr/bin/otool", ["-L", path])
      # otool -L includes a dylib's own install name. It is an identity,
      # not an additional file that the dynamic loader must locate.
      identities =
        run!("/usr/bin/otool", ["-D", path])
        |> String.split("\n", trim: true)
        |> Enum.drop(1)
        |> Enum.map(&String.trim/1)

      dependencies = listing |> String.split("\n", trim: true) |> Enum.drop(1)
      load_commands = run!("/usr/bin/otool", ["-l", path])

      rpaths =
        Regex.scan(~r/cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset/s, load_commands)
        |> Enum.map(&Enum.at(&1, 1))

      executable? = String.contains?(run!("/usr/bin/otool", ["-hv", path]), "EXECUTE")

      for line <- dependencies do
        dependency = line |> String.trim() |> String.split(" (compatibility", parts: 2) |> hd()

        unless dependency in identities do
          verify_dependency!(root, path, dependency, rpaths, executable?)
        end
      end

      # Compiler source paths can expose contributors even when symbols are stripped.
      strings = run!("/usr/bin/strings", [path])

      vendor? =
        Keyword.get(options, :verified_vendor, false) or
          Enum.any?(
            Keyword.get(options, :verified_vendor_directories, []),
            &String.starts_with?(relative, &1 <> "/")
          )

      # Verified upstream wheels retain their public CI source locations. Keep
      # their bytes intact; our workspace/home paths must never be introduced.
      private_strings =
        if vendor?,
          do:
            strings
            |> String.replace("/Users/runner/", "/vendor-build/")
            |> String.replace("/home/runner/", "/vendor-build/"),
          else: strings

      if String.contains?(strings, Path.dirname(@root)) or
           String.contains?(private_strings, ["/Users/", "/home/runner/", "/opt/homebrew/Cellar/"]),
         do: raise("Build-host paths remain in #{relative}; rebuild with source-path mapping")
    end

    :ok
  end

  def verify_dependency!(root, binary, dependency, rpaths, executable? \\ false) do
    root = Path.expand(root)
    owner = Path.dirname(Path.expand(binary))

    expand = fn value ->
      cond do
        value == "@loader_path" ->
          owner

        String.starts_with?(value, "@loader_path/") ->
          Path.expand(String.replace_prefix(value, "@loader_path/", ""), owner)

        executable? and String.starts_with?(value, "@executable_path/") ->
          Path.expand(String.replace_prefix(value, "@executable_path/", ""), owner)

        true ->
          nil
      end
    end

    cond do
      String.starts_with?(dependency, ["/System/Library/", "/usr/lib/"]) ->
        :ok

      true ->
        candidates =
          if String.starts_with?(dependency, "@rpath/") do
            suffix = String.replace_prefix(dependency, "@rpath/", "")

            for base <- rpaths,
                directory = expand.(base),
                directory != nil,
                do: Path.expand(suffix, directory)
          else
            [expand.(dependency)]
          end

        unless Enum.any?(candidates, fn path ->
                 path != nil and String.starts_with?(path, root <> "/") and File.regular?(path)
               end),
               do:
                 raise(
                   "Missing or external native dependency in #{Path.relative_to(binary, root)}: #{dependency}"
                 )

        :ok
    end
  end

  def engine_fingerprint!(app) do
    manifest = json_read(Path.join(app, "sources.json"))
    expected = Map.keys(manifest["local_patches"]) |> Enum.sort()

    present =
      Path.wildcard(Path.join(app, "patches/*.patch"))
      |> Enum.map(&Path.relative_to(&1, app))
      |> Enum.sort()

    unless present == expected, do: raise("Native patch set differs from sources.json")

    hash =
      Enum.reduce(
        expected,
        :crypto.hash_init(:sha256) |> :crypto.hash_update(manifest["runtime_commit"]),
        fn path, hash ->
          pin = manifest["local_patches"][path]
          file = verify!(Path.join(app, path), pin["bytes"], pin["sha256"])
          :crypto.hash_update(hash, File.read!(file))
        end
      )

    hash |> :crypto.hash_final() |> Base.encode16(case: :lower) |> String.slice(0, 16)
  end

  def control_inputs(app) do
    required = [
      "installer/mix.exs",
      "installer/mix.lock",
      "sources.json",
      "desktop/components.json",
      "desktop/build_openssl.exs",
      "desktop/support.exs"
    ]

    patterns = [
      "installer/lib/**/*.ex",
      "installer/config/*.exs",
      "installer/rel/**/*",
      "installer/native/*.rs",
      "installer/priv/static/*",
      "installer/scripts/*.exs",
      "runtime/lib/**/*.ex"
    ]

    files =
      Enum.map(required, &Path.join(app, &1)) ++
        Enum.flat_map(
          patterns,
          &(Path.wildcard(Path.join(app, &1)) |> Enum.filter(fn path -> File.regular?(path) end))
        )

    files
    |> Enum.uniq()
    |> Enum.sort()
    |> Enum.map(fn path ->
      unless File.lstat!(path).type == :regular,
        do: raise("Control source must be a regular file")

      %{
        "path" => Path.relative_to(path, app),
        "bytes" => File.stat!(path).size,
        "sha256" => hash(path)
      }
    end)
  end

  def verify_control!(app, control) do
    receipt = json_read(Path.join(control, "control-build.json"))
    expected = control_inputs(app)

    digest =
      expected
      |> :json.encode()
      |> IO.iodata_to_binary()
      |> then(&:crypto.hash(:sha256, &1))
      |> Base.encode16(case: :lower)

    unless receipt["format_version"] == 1 and receipt["files"] == expected and
             receipt["input_sha256"] == digest and
             receipt["crypto_component"] ==
               json_read(Path.join(app, "desktop/components.json"))["openssl"],
           do:
             raise(
               "Control runtime differs from this application's source; rebuild the control release"
             )

    receipt
  end
end
