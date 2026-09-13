Code.require_file("../../desktop/support.exs", __DIR__)

defmodule RiffInstaller.MacPackage do
  @moduledoc false

  def run do
    unless :os.type() == {:unix, :darwin}, do: Mix.raise("Build the macOS package on macOS.")
    unless Mix.env() == :prod, do: Mix.raise("Build this package with MIX_ENV=prod.")
    crypto = crypto_component!()
    inputs = build_inputs()
    Mix.Task.reenable("compile")
    Mix.Task.run("compile", ["--force"])
    Mix.Task.run("release", ["--overwrite"])
    root = File.cwd!()
    payload = Riff.Installer.Payload.load(System.get_env("RIFF_INSTALLER_BUNDLE_PAYLOAD"))
    version = if(payload, do: payload.manifest["version"], else: "0.1.0")
    stamp = DateTime.utc_now() |> Calendar.strftime("%Y%m%d-%H%M%S")

    output =
      System.get_env("RIFF_INSTALLER_PACKAGE_PATH") ||
        Path.join(root, "_build/macos/#{stamp}/Riff Setup.app")

    if File.exists?(output),
      do: Mix.raise("The package destination already exists; choose a fresh path.")

    contents = Path.join(output, "Contents")
    resources = Path.join(contents, "Resources")
    runtime = Path.join(resources, "runtime")
    File.mkdir_p!(Path.join(contents, "MacOS"))
    File.mkdir_p!(resources)
    File.cp_r!("_build/prod/rel/riff_installer", runtime)
    File.write!(Path.join(contents, "Info.plist"), plist(version))

    run!("rustc", [
      "--edition=2021",
      "-O",
      "-C",
      "strip=symbols",
      "--remap-path-prefix",
      root <> "=riff-installer",
      "native/macos_launcher.rs",
      "-o",
      Path.join(contents, "MacOS/RiffSetup")
    ])

    clean_beams(runtime, Path.dirname(root))
    copy_licenses(runtime, crypto)
    # The Mix launcher computes its runtime paths. This unused OTP helper embeds
    # the build host's installation path and is not an installer entry point.
    for path <- Path.wildcard(Path.join(runtime, "erts-*/bin/start")), do: File.rm!(path)
    # Keep the native dependency closure within the control runtime, which is
    # also installed as its own relocatable tree beside the studio runtimes.
    frameworks = Path.join(runtime, "native")
    File.mkdir_p!(frameworks)
    binaries = files(contents) |> Enum.filter(&mach_o?/1)
    bundled = bundle_libraries(binaries, frameworks, MapSet.new(), crypto)
    for binary <- bundled, do: run!("/usr/bin/codesign", ["--force", "--sign", "-", binary])

    for app <- Path.wildcard(Path.join(runtime, "lib/riff_installer-*/priv/desktop/Riff.app")),
        do: run!("/usr/bin/codesign", ["--force", "--sign", "-", app])

    unless build_inputs() == inputs,
      do: Mix.raise("Control sources changed during the build. Rebuild from one source snapshot.")

    File.write!(
      Path.join(runtime, "control-build.json"),
      Jason.encode!(
        %{
          format_version: 1,
          files: inputs,
          input_sha256:
            inputs
            |> :json.encode()
            |> IO.iodata_to_binary()
            |> then(&:crypto.hash(:sha256, &1))
            |> Base.encode16(case: :lower),
          elixir: System.version(),
          otp: System.otp_release(),
          crypto_component: crypto.receipt["component"]
        },
        pretty: true
      )
    )

    if payload do
      # Native payload bytes have already been signed and pinned by desktop/.
      # Copy them after rewriting the control closure; never mutate their hashes.
      Riff.Installer.Payload.copy_package!(payload, Path.join(resources, "payload"))
    end

    # Ad-hoc signing is solely for this local packaging proof, not Developer ID.
    run!("/usr/bin/codesign", ["--force", "--sign", "-", output])

    File.write!(
      Path.join(Path.dirname(output), "package-proof.json"),
      Jason.encode!(
        %{
          package: Path.basename(output),
          version: version,
          built_at: DateTime.utc_now(),
          runtime_bundled: true,
          signing: "ad-hoc local proof; not notarized or a public installer",
          application_integration:
            if(payload,
              do: "Verified desktop payload included; isolated installation acceptance required",
              else: "Control/runtime proof only; no desktop payload included"
            ),
          payload_version: payload && payload.manifest["version"],
          native_files: Enum.map(bundled, &Path.relative_to(&1, output))
        },
        pretty: true
      )
    )

    IO.puts("Local package proof: #{output}")
  end

  defp build_inputs do
    Riff.Desktop.Build.control_inputs(Path.expand(".."))
  end

  defp clean_beams(runtime, root) do
    for path <- Path.wildcard(Path.join(runtime, "lib/*/ebin/*.beam")) do
      {:ok, _module, chunks} = :beam_lib.all_chunks(String.to_charlist(path))

      rewritten =
        Enum.flat_map(chunks, fn
          {~c"Line", data} = chunk ->
            if :binary.match(data, root) == :nomatch, do: [chunk], else: []

          {~c"Attr", data} ->
            [
              {~c"Attr",
               data |> :erlang.binary_to_term() |> relocate(root) |> :erlang.term_to_binary()}
            ]

          {~c"LitT", data} ->
            [{~c"LitT", rewrite_literals(data, root)}]

          chunk ->
            [chunk]
        end)

      {:ok, binary} = :beam_lib.build_module(rewritten)
      File.write!(path, binary)
    end
  end

  defp crypto_component! do
    path =
      System.get_env("RIFF_INSTALLER_CRYPTO_RECEIPT") ||
        Mix.raise("Supply RIFF_INSTALLER_CRYPTO_RECEIPT from the pinned desktop OpenSSL build.")

    receipt = path |> File.read!() |> Jason.decode!()

    pinned =
      "../desktop/components.json" |> File.read!() |> Jason.decode!() |> Map.fetch!("openssl")

    unless receipt["format_version"] == 1 and receipt["component"] == pinned,
      do: Mix.raise("The crypto component does not match the pinned OpenSSL source.")

    root = path |> Path.expand() |> Path.dirname()

    files =
      Map.new(receipt["files"], fn item ->
        {item["path"], Riff.Installer.Payload.verified_source!(root, item)}
      end)

    unless map_size(files) == length(receipt["files"]) and files[receipt["library"]] != nil and
             is_list(receipt["licenses"]) and receipt["licenses"] != [] and
             Enum.all?(receipt["licenses"], &files[&1]),
           do: Mix.raise("The crypto component is missing its library or notices.")

    %{
      library: files[receipt["library"]],
      licenses: Enum.map(receipt["licenses"], &files[&1]),
      receipt: receipt
    }
  end

  defp copy_licenses(runtime, crypto) do
    destination = Path.join(runtime, "licenses")
    File.mkdir_p!(destination)
    copy_license_group(Path.expand(".."), "riff", destination)

    for source <- Path.wildcard("deps/*"),
        do: copy_license_group(source, Path.basename(source), destination)

    copy_license_group(
      license_root(:code.lib_dir(:elixir) |> List.to_string()),
      "elixir",
      destination
    )

    copy_license_group(
      license_root(:code.root_dir() |> List.to_string()),
      "erlang-otp",
      destination
    )

    for path <- crypto.licenses, do: File.cp!(path, Path.join(destination, Path.basename(path)))

    File.write!(
      Path.join(runtime, "crypto-build.json"),
      Jason.encode!(
        Map.take(
          crypto.receipt,
          ["format_version", "component", "library", "licenses", "files", "configure", "platform"]
        ),
        pretty: true
      )
    )

    rust = run!("rustc", ["--print", "sysroot"]) |> String.trim()
    copy_license_group(rust, "rust", destination)

    File.cp!(
      Path.join(rust, "share/doc/rustc/COPYRIGHT-library.html"),
      Path.join(destination, "rust-standard-library-COPYRIGHT.html")
    )
  end

  defp license_root(path) do
    if license_files(path) != [] do
      path
    else
      if path == Path.dirname(path),
        do: Mix.raise("The runtime's license notice could not be found.")

      license_root(Path.dirname(path))
    end
  end

  defp license_files(path) do
    case File.ls(path) do
      {:ok, names} ->
        names
        |> Enum.filter(
          &Regex.match?(~r/\A(LICENSE|LICENCE|COPYING|COPYRIGHT|NOTICE)([.-].*)?\z/i, &1)
        )
        |> Enum.map(&Path.join(path, &1))
        |> Enum.filter(&File.regular?/1)

      _ ->
        []
    end
  end

  defp copy_license_group(source, name, destination) do
    case license_files(source) do
      [] when name == "nimble_pool" ->
        # NimblePool publishes its copyright/license notice inside README.
        File.cp!(Path.join(source, "README.md"), Path.join(destination, "nimble_pool-NOTICE.md"))
        File.cp!("deps/telemetry/LICENSE", Path.join(destination, "nimble_pool-Apache-2.0.txt"))

      [] ->
        Mix.raise("No license notice found for #{name}.")

      files ->
        for path <- files,
            do: File.cp!(path, Path.join(destination, name <> "-" <> Path.basename(path)))
    end
  end

  defp rewrite_literals(<<size::32, data::binary>>, root) do
    data = if size == 0, do: data, else: :zlib.uncompress(data)
    <<count::32, literals::binary>> = data
    rewritten = <<count::32>> <> rewrite_terms(literals, root)
    # OTP 28 supports the original uncompressed form as well as zlib.
    if size == 0,
      do: <<0::32, rewritten::binary>>,
      else: <<byte_size(rewritten)::32, :zlib.compress(rewritten)::binary>>
  end

  defp rewrite_terms(<<>>, _root), do: <<>>

  defp rewrite_terms(<<size::32, term::binary-size(size), rest::binary>>, root) do
    encoded = term |> :erlang.binary_to_term() |> relocate(root) |> :erlang.term_to_binary()
    <<byte_size(encoded)::32, encoded::binary>> <> rewrite_terms(rest, root)
  end

  defp relocate(value, root) when is_binary(value),
    do: :binary.replace(value, root, "riff-installer", [:global])

  defp relocate([], _root), do: []

  defp relocate([head | tail] = value, root) do
    if List.ascii_printable?(value),
      do: value |> List.to_string() |> relocate(root) |> String.to_charlist(),
      else: [relocate(head, root) | relocate(tail, root)]
  end

  defp relocate(value, root) when is_tuple(value),
    do: value |> Tuple.to_list() |> Enum.map(&relocate(&1, root)) |> List.to_tuple()

  defp relocate(value, root) when is_map(value),
    do:
      Map.new(Map.to_list(value), fn {key, item} ->
        {relocate(key, root), relocate(item, root)}
      end)

  defp relocate(value, _root), do: value

  defp bundle_libraries([], _frameworks, seen, _crypto), do: MapSet.to_list(seen)

  defp bundle_libraries([binary | rest], frameworks, seen, crypto) do
    if MapSet.member?(seen, binary) do
      bundle_libraries(rest, frameworks, seen, crypto)
    else
      dependencies = dependencies(binary)

      extra =
        Enum.flat_map(dependencies, fn original ->
          cond do
            String.starts_with?(original, ["/usr/lib/", "/System/Library/"]) ->
              []

            Path.basename(original) == "libcrypto.3.dylib" ->
              copied = Path.join(frameworks, Path.basename(original))
              if not File.exists?(copied), do: File.cp!(crypto.library, copied)

              if binary != copied do
                local = "@loader_path/" <> relative(copied, Path.dirname(binary))
                run!("/usr/bin/install_name_tool", ["-change", original, local, binary])
                [copied]
              else
                []
              end

            String.starts_with?(original, "@loader_path/") ->
              resolved =
                original
                |> String.replace_prefix("@loader_path/", "")
                |> Path.expand(Path.dirname(binary))

              unless String.starts_with?(resolved, Path.dirname(frameworks) <> "/") and
                       File.regular?(resolved),
                     do:
                       Mix.raise(
                         "A native library points outside the control runtime: #{Path.basename(binary)}"
                       )

              []

            true ->
              Mix.raise(
                "Unresolved native library dependency in #{Path.basename(binary)}: #{original}"
              )
          end
        end)

      if String.ends_with?(binary, ".dylib"),
        do:
          run!("/usr/bin/install_name_tool", ["-id", "@rpath/" <> Path.basename(binary), binary])

      bundle_libraries(rest ++ extra, frameworks, MapSet.put(seen, binary), crypto)
    end
  end

  defp dependencies(path) do
    run!("/usr/bin/otool", ["-L", path])
    |> String.split("\n", trim: true)
    |> Enum.drop(1)
    |> Enum.map(&(String.trim(&1) |> String.split(" (compatibility", parts: 2) |> hd()))
  end

  defp relative(target, source) do
    left = Path.split(target)
    right = Path.split(source)
    common = Enum.zip(left, right) |> Enum.take_while(fn {a, b} -> a == b end) |> length()
    Path.join(List.duplicate("..", length(right) - common) ++ Enum.drop(left, common))
  end

  defp files(path) do
    path
    |> File.ls!()
    |> Enum.flat_map(fn name ->
      full = Path.join(path, name)

      case File.lstat!(full).type do
        :directory -> files(full)
        :regular -> [full]
        _ -> []
      end
    end)
  end

  defp mach_o?(path) do
    {:ok, file} = File.open(path, [:read, :binary, :raw])
    magic = :file.read(file, 4)
    File.close(file)

    magic in Enum.map(
      [0xFEEDFACE, 0xFEEDFACF, 0xCEFAEDFE, 0xCFFAEDFE, 0xCAFEBABE],
      &{:ok, <<&1::32>>}
    )
  end

  defp run!(command, args) do
    case System.cmd(command, args, stderr_to_stdout: true) do
      {output, 0} -> output
      {output, _} -> Mix.raise("#{Path.basename(command)} failed: #{output}")
    end
  end

  defp plist(version) do
    """
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0"><dict>
      <key>CFBundleName</key><string>Riff Setup</string>
      <key>CFBundleDisplayName</key><string>Riff Setup</string>
      <key>CFBundleIdentifier</key><string>org.flip-engineering.riff.setup</string>
      <key>CFBundleExecutable</key><string>RiffSetup</string>
      <key>CFBundlePackageType</key><string>APPL</string>
      <key>CFBundleVersion</key><string>#{version}</string>
      <key>CFBundleShortVersionString</key><string>#{version}</string>
      <key>LSUIElement</key><true/>
      <key>LSMinimumSystemVersion</key><string>15.0</string>
      <key>NSHighResolutionCapable</key><true/>
    </dict></plist>
    """
  end
end

RiffInstaller.MacPackage.run()
