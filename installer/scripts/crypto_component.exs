defmodule Riff.Installer.CryptoComponent do
  @moduledoc false
  alias Riff.Desktop.Build, as: B

  @nif_names ["crypto.so", "crypto_callback.so", "otp_test_engine.so"]

  def nif_names, do: @nif_names

  def host!(otp_root \\ List.to_string(:code.root_dir())) do
    pin = B.components()["otp_crypto"]
    major = pin["version"] |> String.split(".") |> hd()
    version = File.read!(Path.join([otp_root, "releases", major, "OTP_VERSION"])) |> String.trim()
    application = Path.join(otp_root, "lib/crypto-#{pin["crypto_version"]}")

    {:ok, [{:application, :crypto, properties}]} =
      :file.consult(String.to_charlist(Path.join(application, "ebin/crypto.app")))

    unless version == pin["version"] and
             List.to_string(Keyword.fetch!(properties, :vsn)) == pin["crypto_version"],
           do: raise("Build the control runtime with the exact pinned OTP and crypto versions")

    erts = Path.join(otp_root, "erts-#{pin["erts_version"]}")
    headers = Path.join(erts, "include")
    beam = Path.join(erts, "bin/beam.smp")
    nif = Path.join(application, "priv/lib/crypto.so")
    for path <- [beam, nif, Path.join(headers, "erl_nif.h")], do: File.stat!(path)
    %{root: otp_root, pin: pin, application: application, headers: headers, beam: beam, nif: nif}
  end

  # Darwin's symbol table carries the provider ordinal even when a library's
  # install name is relative or its original build-host path no longer exists.
  # Never intersect with the old library's exports: that could omit missing APIs.
  def verify_nif!(nif, library, beam) do
    imported = imports!(nif)
    [dependency] = crypto_dependencies!(nif)
    required = imported |> Enum.filter(&crypto_provider?(&1.provider)) |> Enum.map(& &1.symbol)

    if required == [],
      do: raise("Crypto NIF has no dynamic OpenSSL imports; rebuild the pinned NIF")

    missing = MapSet.difference(MapSet.new(required), symbols!(library, "-gjU")) |> Enum.sort()
    if missing != [], do: raise("Crypto NIF ABI mismatch: #{Enum.join(missing, ", ")}")

    runtime =
      imported
      |> Enum.filter(&(&1.provider in ["executable", "dynamically looked up"]))
      |> Enum.map(& &1.symbol)

    unless Enum.all?(runtime, &String.starts_with?(&1, "_enif_")),
      do: raise("Crypto NIF contains an unattributed non-ERTS import")

    missing_runtime =
      MapSet.difference(MapSet.new(runtime), symbols!(beam, "-gjU")) |> Enum.sort()

    if missing_runtime != [],
      do: raise("Crypto NIF requires unavailable ERTS APIs: #{Enum.join(missing_runtime, ", ")}")

    %{
      "required_symbols" => Enum.sort(required),
      "engine_symbol_count" => Enum.count(required, &String.starts_with?(&1, "_ENGINE_")),
      "required_erts_symbols" => Enum.sort(runtime),
      "missing_symbols" => missing,
      "crypto_dependency" => Path.basename(dependency)
    }
  end

  def linkage!(nif) do
    case crypto_dependencies(nif) do
      [_] ->
        "dynamic"

      [] ->
        exports = symbols!(nif, "-gjU")

        if Enum.all?(
             ["_OpenSSL_version", "_EVP_MD_fetch", "_SHA256"],
             &MapSet.member?(exports, &1)
           ),
           do: "embedded",
           else: raise("The installed crypto NIF has an unrecognized OpenSSL linkage")

      _ ->
        raise("The installed crypto NIF links multiple OpenSSL libraries")
    end
  end

  def imports!(nif) do
    listing = B.run!("/usr/bin/nm", ["-arch", "arm64", "-mu", nif])

    imported =
      for line <- String.split(listing, "\n", trim: true) do
        case Regex.run(
               ~r/\A\s*\(undefined\)\s+(?:weak\s+)?external\s+(\S+)\s+\((?:from )?([^)]*)\)\s*\z/,
               line
             ) do
          [_, symbol, provider] -> %{symbol: symbol, provider: provider}
          _ -> raise("Cannot identify a native import's provider in #{Path.basename(nif)}")
        end
      end

    unless MapSet.new(Enum.map(imported, & &1.symbol)) == symbols!(nif, "-ju"),
      do: raise("Native import inspection did not account for every undefined symbol")

    imported
  end

  defp symbols!(path, flags) do
    B.run!("/usr/bin/nm", ["-arch", "arm64", flags, path])
    |> String.split("\n", trim: true)
    |> Enum.map(&String.trim/1)
    |> MapSet.new()
  end

  defp crypto_provider?(provider),
    do: Regex.match?(~r/\Alibcrypto(?:\.3)?(?:\.dylib)?\z/, provider)

  defp crypto_dependencies(nif) do
    B.run!("/usr/bin/otool", ["-L", nif])
    |> String.split("\n", trim: true)
    |> Enum.drop(1)
    |> Enum.map(&(String.trim(&1) |> String.split(" (", parts: 2) |> hd()))
    |> Enum.filter(&(Path.basename(&1) |> String.starts_with?("libcrypto")))
  end

  defp crypto_dependencies!(nif) do
    case crypto_dependencies(nif) do
      [dependency] ->
        unless Path.basename(dependency) == "libcrypto.3.dylib",
          do: raise("Crypto NIF requires a different OpenSSL ABI; rebuild the pinned NIF")

        [dependency]

      [] ->
        raise("Crypto NIF embeds OpenSSL or has no crypto library; rebuild the pinned NIF")

      _ ->
        raise("Crypto NIF links multiple OpenSSL libraries")
    end
  end

  def build_nifs!(root, crypto, openssl_source, environment, options \\ []) do
    host = host!(Keyword.get(options, :otp_root, List.to_string(:code.root_dir())))
    source_root = Path.join(root, "otp-source")
    File.mkdir!(source_root)
    prefix = "otp_src_#{host.pin["version"]}"
    archive = B.fetch!("otp_crypto")

    notices =
      B.run!("/usr/bin/tar", ["-tf", archive])
      |> String.split("\n", trim: true)
      |> Enum.filter(fn path ->
        String.starts_with?(path, prefix <> "/") and not String.ends_with?(path, "/") and
          (String.starts_with?(path, prefix <> "/LICENSES/") or
             Regex.match?(
               ~r/\A(LICENSE|LICENCE|COPYING|COPYRIGHT|NOTICE)([.-].*)?\z/i,
               Path.basename(path)
             ))
      end)

    members =
      Enum.map(["lib/crypto/c_src", "lib/crypto/vsn.mk", "OTP_VERSION"], &Path.join(prefix, &1))

    B.run!("/usr/bin/tar", ["-xf", archive, "-C", source_root] ++ members ++ notices)
    source = Path.join([source_root, prefix, "lib/crypto/c_src"])

    unless String.trim(File.read!(Path.join([source_root, prefix, "OTP_VERSION"]))) ==
             host.pin["version"],
           do: raise("Crypto source version does not match its pin")

    makefile = File.read!(Path.join(source, "Makefile.in"))
    [_, objects] = Regex.run(~r/^CRYPTO_OBJS =([\s\S]*?)^CALLBACK_OBJS =/m, makefile)
    pattern = ~r/\$\(OBJDIR\)\/([a-z][a-z0-9_]*)\$\(TYPEMARKER\)\.o/
    sources = for [_, name] <- Regex.scan(pattern, objects), do: Path.join(source, name <> ".c")
    leftover = Regex.replace(pattern, objects, "") |> String.replace("\\", "") |> String.trim()

    unless leftover == "" and sources != [] and Enum.uniq(sources) == sources and
             Enum.all?(sources, &File.regular?/1),
           do: raise("The pinned OTP crypto build's source list is not understood")

    source_records = B.records(source)
    header_records = B.records(host.headers)
    destination = Path.join(crypto, "nif")
    File.mkdir!(destination)
    library = Path.join(crypto, "libcrypto.3.dylib")

    # Match configure.ac's declaration/link test. Without this feature define,
    # OTP still builds but crypto:hash_equals/2 raises notsup at runtime.
    probe = Path.join(root, "crypto-memcmp-check.c")
    probe_binary = Path.join(root, "crypto-memcmp-check")

    File.write!(probe, """
    #include <openssl/crypto.h>
    int main(void) {
      int (*compare)(const void *, const void *, size_t) = CRYPTO_memcmp;
      return compare("same", "same", 4) != 0 || compare("same", "else", 4) == 0;
    }
    """)

    B.run!(
      "/usr/bin/clang",
      [
        "-arch",
        "arm64",
        "-mmacosx-version-min=15.0",
        "-Werror=implicit-function-declaration",
        "-I",
        Path.join(openssl_source, "include"),
        probe,
        library,
        "-Wl,-rpath,@loader_path/crypto",
        "-o",
        probe_binary
      ],
      env: environment
    )

    B.run!(probe_binary, [], env: environment)

    flags = [
      "-O2",
      "-fPIC",
      "-fno-common",
      "-bundle",
      "-undefined",
      "dynamic_lookup",
      "-arch",
      "arm64",
      "-mmacosx-version-min=15.0",
      "-DHAVE_DYNAMIC_CRYPTO_LIB",
      "-DHAVE_OPENSSL_CRYPTO_MEMCMP",
      "-DDISABLE_EVP_DH=0",
      "-DDISABLE_EVP_HMAC=0",
      "-Wno-deprecated-declarations",
      "-I",
      source,
      "-I",
      host.headers,
      "-I",
      Path.join(openssl_source, "include"),
      "-ffile-prefix-map=#{root}=riff-crypto",
      "-fdebug-prefix-map=#{root}=riff-crypto",
      "-ffile-prefix-map=#{host.root}=riff-otp",
      "-fdebug-prefix-map=#{host.root}=riff-otp",
      "-ffile-prefix-map=#{openssl_source}=riff-openssl",
      "-fdebug-prefix-map=#{openssl_source}=riff-openssl"
    ]

    log =
      for {name, units, libraries} <- [
            {"crypto.so", sources, [library]},
            {"crypto_callback.so", [Path.join(source, "crypto_callback.c")], []},
            {"otp_test_engine.so", [Path.join(source, "otp_test_engine.c")], [library]}
          ] do
        output = Path.join(destination, name)

        result =
          B.run!("/usr/bin/clang", flags ++ units ++ libraries ++ ["-o", output],
            env: environment
          )

        if libraries != [],
          do:
            B.run!("/usr/bin/install_name_tool", [
              "-change",
              "@rpath/libcrypto.3.dylib",
              "@loader_path/../libcrypto.3.dylib",
              output
            ])

        B.run!("/usr/bin/strip", ["-x", output])
        B.run!("/usr/bin/codesign", ["--force", "--sign", "-", output])
        result
      end

    File.write!(Path.join(root, "crypto-nif-build.log"), log)

    unless B.records(source) == source_records and B.records(host.headers) == header_records,
      do: raise("Crypto build inputs changed during compilation")

    B.inspect_macos!(crypto)
    verification = verify_nif!(Path.join(destination, "crypto.so"), library, host.beam)

    for notice <- notices do
      target = Path.join([crypto, "licenses/erlang-otp", Path.relative_to(notice, prefix)])
      File.mkdir_p!(Path.dirname(target))
      File.cp!(Path.join(source_root, notice), target)
    end

    %{
      "component" => host.pin,
      "application" => "crypto",
      "application_version" => host.pin["crypto_version"],
      "files" => Enum.map(@nif_names, &Path.join("crypto/nif", &1)),
      "source_files" => source_records,
      "erts_headers" => header_records,
      "configure_features" => %{
        "HAVE_DYNAMIC_CRYPTO_LIB" => true,
        "HAVE_OPENSSL_CRYPTO_MEMCMP" => true,
        "FIPS_SUPPORT" => false,
        "DISABLE_EVP_DH" => 0,
        "DISABLE_EVP_HMAC" => 0
      },
      "original_nif_sha256" => B.hash(host.nif),
      "original_linkage" => linkage!(host.nif),
      "verification" => verification
    }
  end
end
