Code.require_file("support.exs", __DIR__)
alias Riff.Desktop.Build, as: B

unless :os.type() == {:unix, :darwin} and String.trim(B.run!("uname", ["-m"])) == "arm64",
  do: raise("Build the macOS arm64 crypto runtime on an Apple Silicon Mac")

{options, [], []} = OptionParser.parse(System.argv(), strict: [jobs: :integer, output: :string])
jobs = Keyword.get(options, :jobs, 2)
unless jobs > 0, do: raise("Build jobs must be positive")
root = options |> Keyword.fetch!(:output) |> Path.expand()
if File.exists?(root), do: raise("Choose a fresh OpenSSL build directory")
File.mkdir_p!(root)
component = B.components()["openssl"]
source_parent = B.unpack!(B.fetch!("openssl"), Path.join(root, "source"))
[directory] = File.ls!(source_parent)
source = Path.join(source_parent, directory)
target = "darwin64-arm64"
prefix = "/riff/runtime/openssl"
flags = "-O2 -ffile-prefix-map=#{root}=riff-openssl -fdebug-prefix-map=#{root}=riff-openssl"

environment = [
  {"PATH", "/usr/bin:/bin:/usr/sbin:/sbin"},
  {"CC", "/usr/bin/clang"},
  {"AR", "/usr/bin/ar"},
  {"RANLIB", "/usr/bin/ranlib"},
  {"CFLAGS", flags},
  {"CPPFLAGS", nil},
  {"LDFLAGS", nil},
  {"CPATH", nil},
  {"C_INCLUDE_PATH", nil},
  {"LIBRARY_PATH", nil},
  {"SDKROOT", String.trim(B.run!("/usr/bin/xcrun", ["--show-sdk-path"]))},
  {"MACOSX_DEPLOYMENT_TARGET", "15.0"},
  {"SOURCE_DATE_EPOCH", "0"},
  {"RIFF_OPENSSL_BUILD_ROOT", root},
  {"OPENSSL_CONF", "/dev/null"},
  {"OPENSSL_MODULES", nil},
  {"OPENSSL_ENGINES", nil}
]

# OTP's crypto NIF imports ENGINE_* symbols even when Riff never uses engines.
# Keep that ABI while building no loadable engine/provider modules and disabling
# automatic config loading. A no-engine library is not a compatible replacement.
arguments = [
  target,
  "shared",
  "no-module",
  "no-dynamic-engine",
  "no-autoload-config",
  "--prefix=#{prefix}",
  "--openssldir=#{prefix}/ssl",
  "--libdir=lib"
]

IO.puts("Configuring pinned OpenSSL #{component["version"]}")
log = B.run!("/usr/bin/perl", ["Configure" | arguments], cd: source, env: environment)
File.write!(Path.join(root, "configure.log"), log)

# The upstream build-info generator embeds the actual compiler command in a
# runtime diagnostic string. Prefix maps protect __FILE__; normalize that one
# metadata string too, without changing compiler options or crypto source.
generator = Path.join(source, "util/mkbuildinf.pl")
needle = "my $cflags = join(' ', @ARGV);"
generator_source = File.read!(generator)
unless length(String.split(generator_source, needle)) == 2, do: raise("Build metadata changed")

File.write!(
  generator,
  String.replace(
    generator_source,
    needle,
    needle <>
      "\n$cflags =~ s/\\Q$ENV{RIFF_OPENSSL_BUILD_ROOT}\\E/riff-openssl/g " <>
      "if defined $ENV{RIFF_OPENSSL_BUILD_ROOT};"
  )
)

IO.puts("Building private shared crypto runtime")
log = B.run!("/usr/bin/make", ["-j#{jobs}", "build_sw"], cd: source, env: environment)
File.write!(Path.join(root, "build.log"), log)
crypto = Path.join(root, "crypto")
File.mkdir!(crypto)
library = Path.join(crypto, "libcrypto.3.dylib")
File.cp!(Path.join(source, "libcrypto.3.dylib"), library)
File.chmod!(library, 0o755)
B.run!("/usr/bin/install_name_tool", ["-id", "@rpath/libcrypto.3.dylib", library])
B.run!("/usr/bin/strip", ["-x", library])
B.run!("/usr/bin/codesign", ["--force", "--sign", "-", library])
B.inspect_macos!(crypto)
unless String.trim(B.run!("/usr/bin/lipo", ["-archs", library])) == "arm64",
  do: raise("Crypto runtime has an unexpected architecture")

licenses = Path.join(crypto, "licenses")
File.mkdir!(licenses)
File.cp!(Path.join(source, "LICENSE.txt"), Path.join(licenses, "openssl-LICENSE.txt"))
File.cp!(__ENV__.file, Path.join(crypto, "build_openssl.exs"))
B.json_write(Path.join(crypto, "component.json"), component)

# Verify the real NIF's required OpenSSL symbol set against the replacement,
# including the ENGINE ABI. The currently linked library is used only to
# identify its symbols; no host library is copied into the distributed tree.
nif = Path.join(List.to_string(:code.priv_dir(:crypto)), "lib/crypto.so")

[previous_library] =
  B.run!("/usr/bin/otool", ["-L", nif])
  |> String.split("\n", trim: true)
  |> Enum.drop(1)
  |> Enum.map(&(String.trim(&1) |> String.split(" (", parts: 2) |> hd()))
  |> Enum.filter(&(String.contains?(&1, "libcrypto") and File.regular?(&1)))

symbols = fn path, flags ->
  B.run!("/usr/bin/nm", [flags, path])
  |> String.split("\n", trim: true)
  |> Enum.map(&String.trim/1)
  |> MapSet.new()
end

required = MapSet.intersection(symbols.(nif, "-ju"), symbols.(previous_library, "-gjU"))
provided = symbols.(library, "-gjU")
missing = MapSet.difference(required, provided) |> Enum.sort()
if missing != [], do: raise("Crypto NIF ABI mismatch: #{Enum.join(missing, ", ")}")
if MapSet.size(required) == 0, do: raise("Crypto NIF symbol verification found no imports")

# These native checks run the freshly built tools with the freshly built
# libraries. Only libcrypto is delivered; Erlang implements the TLS protocol.
openssl = Path.join(source, "apps/openssl")
test_environment = [{"DYLD_LIBRARY_PATH", source} | environment]
version = B.run!(openssl, ["version", "-a"], env: test_environment)
unless String.contains?(version, "OpenSSL #{component["version"]}"),
  do: raise("The native check loaded a different OpenSSL version")

unless String.contains?(version, "OPENSSLDIR: \"#{prefix}/ssl\""),
  do: raise("The native check has an unexpected config directory")

if String.contains?(version, [root, "/opt/homebrew/"]),
  do: raise("The native diagnostic exposes build-host paths")

message = "riff private crypto check\n"
input = Path.join(root, "digest-input.txt")
File.write!(input, message)
expected_hash = :crypto.hash(:sha256, message) |> Base.encode16(case: :lower)
digest = B.run!(openssl, ["dgst", "-sha256", input], env: test_environment)
unless String.ends_with?(String.trim(digest), expected_hash), do: raise("Native SHA256 check failed")
random = B.run!(openssl, ["rand", "-hex", "32"], env: test_environment) |> String.trim()
unless Regex.match?(~r/\A[0-9a-f]{64}\z/, random), do: raise("Native entropy check failed")
providers = B.run!(openssl, ["list", "-providers"], env: test_environment)
File.write!(Path.join(root, "native-check.log"), version <> "\n" <> providers)

files =
  for record <- B.records(crypto),
      do: Map.update!(record, "path", &Path.join("crypto", &1))

B.json_write(Path.join(root, "openssl.json"), %{
  "format_version" => 1,
  "component" => component,
  "platform" => "macos-arm64",
  "library" => "crypto/libcrypto.3.dylib",
  "licenses" => ["crypto/licenses/openssl-LICENSE.txt"],
  "files" => files,
  "configure" => arguments,
  "compiler_flags" => String.replace(flags, root, "riff-openssl"),
  "minimum_macos" => "15.0",
  "metadata_path_mapping" => "Only the compiler diagnostic string is normalized by mkbuildinf.pl",
  "validation" => %{
    "macho_closure" => "system libraries only",
    "build_host_paths" => "absent",
    "native_version_sha256_entropy" => true,
    "crypto_nif_sha256" => B.hash(nif),
    "required_symbols" => Enum.sort(required),
    "engine_symbol_count" => Enum.count(required, &String.starts_with?(&1, "_ENGINE_")),
    "missing_symbols" => missing,
    "erts_crypto_tls" => "Separate relocated control proof required"
  }
})

IO.puts("Verified private crypto runtime: #{library}")
IO.puts("Crypto receipt: #{Path.join(root, "openssl.json")}")
