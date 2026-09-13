Code.require_file("support.exs", __DIR__)
Code.require_file("../scripts/native_capabilities.exs", __DIR__)
alias Riff.Desktop.Build, as: B

{options, [], []} =
  OptionParser.parse(System.argv(),
    strict: [source: :string, app_source: :string, output: :string, jobs: :integer]
  )

source = options |> Keyword.fetch!(:source) |> Path.expand()
app = options |> Keyword.get(:app_source, Path.dirname(B.root())) |> Path.expand()
output = options |> Keyword.fetch!(:output) |> Path.expand()
if File.exists?(output), do: raise("Choose a fresh native build directory")
manifest = B.json_read(Path.join(app, "sources.json"))
revision = String.trim(B.run!("git", ["-C", source, "rev-parse", "HEAD"]))

unless revision == manifest["runtime_commit"],
  do: raise("Native source revision differs from sources.json")

patches = Path.wildcard(Path.join(app, "patches/*.patch")) |> Enum.sort()
fingerprint = B.engine_fingerprint!(app)

File.mkdir_p!(output)
index = Path.join(output, "source-proof.index")
environment = [{"GIT_INDEX_FILE", index}]
snapshot = Path.join(output, "source")
File.mkdir!(snapshot)

try do
  B.run!("git", ["-C", source, "read-tree", revision], env: environment)

  for patch <- patches,
      do: B.run!("git", ["-C", source, "apply", "--cached", patch], env: environment)

  # Compile exactly the verified index. Untracked files in a developer checkout
  # and its uncommitted edits remain untouched and cannot enter the build.
  B.run!("git", ["-C", source, "checkout-index", "--all", "--prefix=" <> snapshot <> "/"],
    env: environment
  )
after
  File.rm(index)
end

flags =
  "-ffile-prefix-map=#{snapshot}=audio.cpp -ffile-prefix-map=#{output}=riff-build -fdebug-prefix-map=#{snapshot}=audio.cpp -fdebug-prefix-map=#{output}=riff-build"

arguments = [
  "-S",
  snapshot,
  "-B",
  output,
  "-DCMAKE_BUILD_TYPE=Release",
  "-DCMAKE_C_FLAGS=#{flags}",
  "-DCMAKE_CXX_FLAGS=#{flags}",
  "-DCMAKE_OSX_DEPLOYMENT_TARGET=15.0",
  "-DAUDIOCPP_MODEL_SET=custom",
  "-DAUDIOCPP_MODELS=yue2",
  "-DAUDIOCPP_DEPLOYMENT_BUILD=ON",
  "-DENGINE_ENABLE_METAL=ON",
  "-DENGINE_ENABLE_CUDA=OFF",
  "-DENGINE_ENABLE_HIP=OFF",
  "-DENGINE_ENABLE_VULKAN=OFF",
  "-DENGINE_ENABLE_OPENMP=OFF",
  "-DGGML_OPENMP=OFF",
  "-DENGINE_ENABLE_NATIVE_CPU=OFF",
  "-DGGML_METAL_EMBED_LIBRARY=ON"
]

File.write!(
  Path.join(output, "configure.log"),
  B.run!("cmake", arguments, env: [{"GIT_CEILING_DIRECTORIES", Path.dirname(output)}])
)

File.write!(
  Path.join(output, "build.log"),
  B.run!("cmake", [
    "--build",
    output,
    "--parallel",
    Integer.to_string(Keyword.get(options, :jobs, 2)),
    "--target",
    "audiocpp_cli"
  ])
)

binary = Path.join(output, "bin/audiocpp_cli")
B.run!("/usr/bin/strip", ["-x", binary])
B.run!("/usr/bin/codesign", ["--force", "--sign", "-", binary])
B.inspect_macos!(Path.join(output, "bin"))

capabilities =
  Riff.Native.Capabilities.verify!(binary, Map.fetch!(manifest, "native_capabilities"), output)

B.json_write(Path.join(output, "engine.json"), %{
  "runtime_commit" => revision,
  "engine_fingerprint" => fingerprint,
  "sha256" => B.hash(binary),
  "bytes" => File.stat!(binary).size,
  "source_export" => "verified-index",
  "platform" => "macos-arm64",
  "backend" => "metal",
  "capabilities" => capabilities,
  "model_gpu_execution_validated" => false
})

IO.puts("Native payload ready: #{binary}")
