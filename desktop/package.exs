Code.require_file("support.exs", __DIR__)
alias Riff.Desktop.Build, as: B

{options, [], []} =
  OptionParser.parse(System.argv(),
    strict: [
      application_archive: :string,
      application_sha256: :string,
      application_commit: :string,
      engine: :string,
      engine_source: :string,
      engine_receipt: :string,
      media_receipt: :string,
      python: :string,
      python_receipt: :string,
      media: :string,
      control: :string,
      output: :string
    ]
  )

required = fn key -> Keyword.fetch!(options, key) |> Path.expand() end
archive = required.(:application_archive)
digest = Keyword.fetch!(options, :application_sha256)
unless B.hash(archive) == digest, do: raise("Application archive verification failed")
output = required.(:output)
if File.exists?(output), do: raise("Choose a fresh payload destination")
File.mkdir_p!(output)
app = B.unpack!(archive, Path.join(output, "app"))
# Official source archives have no links; inspect before accepting the source tree.
B.regular_files(app)
version = File.read!(Path.join(app, "VERSION")) |> String.trim()
unless Regex.match?(~r/^\d+\.\d+\.\d+$/, version), do: raise("Invalid application version")
engine_receipt = B.json_read(required.(:engine_receipt))
media_receipt = B.json_read(required.(:media_receipt))
python_receipt = B.json_read(required.(:python_receipt))
fingerprint = B.engine_fingerprint!(app)
control_receipt = B.verify_control!(app, required.(:control))

unless engine_receipt["runtime_commit"] ==
         B.json_read(Path.join(app, "sources.json"))["runtime_commit"] and
         engine_receipt["engine_fingerprint"] == fingerprint and
         engine_receipt["source_export"] == "verified-index" and
         engine_receipt["platform"] == "macos-arm64" and engine_receipt["backend"] == "metal",
       do: raise("Native build receipt does not match this application's complete pinned source")

B.verify!(required.(:engine), engine_receipt["bytes"], engine_receipt["sha256"])

unless media_receipt["components"] == Map.take(B.components(), ["ffmpeg", "lame", "x264"]),
  do: raise("Media build source differs from pinned components")

unless B.records(required.(:media)) == media_receipt["files"],
  do: raise("Media tools differ from their build receipt")

unless python_receipt["python"] == B.components()["python"] and
         python_receipt["writer_wheels"] ==
           B.json_read(Path.join(B.root(), "writer-wheels-macos-arm64.json")) and
         is_map(python_receipt["local_writer_imports"]),
       do: raise("Portable writer runtime does not match its pinned components")

unless B.records(required.(:python)) == python_receipt["files"],
  do: raise("Portable Python differs from its build receipt")

runtime = Path.join(output, "runtime")
File.mkdir!(runtime)
B.copy_tree!(required.(:python), Path.join(runtime, "python"))
engine = Path.join(runtime, "engine")
File.mkdir!(engine)
File.cp!(required.(:engine), Path.join(engine, "audiocpp_cli"))
File.chmod!(Path.join(engine, "audiocpp_cli"), 0o755)
B.copy_tree!(required.(:media), Path.join(runtime, "media"))
B.copy_tree!(required.(:control), Path.join(runtime, "control"))
licenses = Path.join(runtime, "licenses")
File.mkdir!(licenses)
engine_source = required.(:engine_source)

for relative <- [
      "LICENSE",
      "external/ggml/LICENSE",
      "external/libyaml/License",
      "external/cJSON/LICENSE",
      "external/cpp-httplib/LICENSE",
      "external/sentencepiece/LICENSE",
      "external/sentencepiece/third_party/absl/LICENSE",
      "external/sentencepiece/third_party/darts_clone/LICENSE",
      "external/sentencepiece/third_party/esaxx/LICENSE",
      "external/sentencepiece/third_party/protobuf-lite/LICENSE"
    ] do
  target = Path.join(licenses, "audio.cpp/" <> relative)
  File.mkdir_p!(Path.dirname(target))
  File.cp!(Path.join(engine_source, relative), target)
end

B.json_write(Path.join(licenses, "components.json"), B.components())
B.json_write(Path.join(licenses, "writer-wheels.json"), python_receipt["writer_wheels"])
File.cp!(Path.join(B.root(), "build_media.exs"), Path.join(licenses, "build_media.exs"))
File.cp!(Path.join(B.root(), "support.exs"), Path.join(licenses, "support.exs"))

for name <- ["ffmpeg", "x264", "lame"] do
  component = B.components()[name]
  File.cp!(B.fetch!(name), Path.join(licenses, component["archive"]))
end

File.write!(Path.join(licenses, "README.txt"), """
The Python runtime is a temporary compatibility component while Riff migrates to Elixir.
Python's license texts and third-party notices are included in the python directory.
FFmpeg is built with GPL components x264 and LGPL LAME. Corresponding pinned source
archives and exact build scripts are included here; inspect each archive's COPYING
and LICENSE files for its terms. No nonfree FFmpeg components are included.
The native audio.cpp and ggml engine source revision and Riff patches are recorded
in app/sources.json and app/patches. Their source and notices must accompany releases.
The bundled control release retains the Elixir/OTP dependency licenses.
""")

B.inspect_macos!(runtime, verified_vendor_directories: ["python"])
python = Path.join(runtime, "python/bin/python3.14")

environment = [
  {"PATH", Path.join(runtime, "media") <> ":/usr/bin:/bin"},
  {"PYTHONNOUSERSITE", "1"},
  {"PYTHONDONTWRITEBYTECODE", "1"},
  {"PYTHONPATH", nil},
  {"PYTHONHOME", nil},
  {"SSL_CERT_FILE", "/etc/ssl/cert.pem"}
]

B.run!(python, ["-s", "-B", Path.join(app, "studio.py"), "--check"], env: environment)

B.run!(
  Path.join(runtime, "control/bin/riff_installer"),
  [
    "eval",
    "{:ok, _} = Application.ensure_all_started(:crypto); true = byte_size(:crypto.hash(:sha256, \"riff\")) == 32; true = Code.ensure_loaded?(Riff.Runtime.SchedulerPort)"
  ],
  env: environment
)

for tool <- ["ffmpeg", "ffprobe"],
    do: B.run!(Path.join(runtime, "media/" <> tool), ["-version"], env: environment)

files = B.records(output)

runtime_digest =
  files
  |> Enum.filter(&String.starts_with?(&1["path"], "runtime/"))
  |> :json.encode()
  |> IO.iodata_to_binary()
  |> then(&:crypto.hash(:sha256, &1))
  |> Base.encode16(case: :lower)

B.json_write(Path.join(output, "manifest.json"), %{
  "format_version" => 1,
  "version" => version,
  "platform" => "macos-arm64",
  "features" => %{"local_writer" => true},
  "files" => files,
  "runtime_id" => String.slice(runtime_digest, 0, 24),
  "runtime_sha256" => runtime_digest,
  "entries" => %{
    "python" => "runtime/python/bin/python3.14",
    "studio" => "app/studio.py",
    "launcher" => "app/launcher.py",
    "engine" => "runtime/engine/audiocpp_cli",
    "ffmpeg" => "runtime/media/ffmpeg",
    "ffprobe" => "runtime/media/ffprobe",
    "control" => "runtime/control/bin/riff_installer"
  },
  "environment" => %{
    "SSL_CERT_FILE" => "/etc/ssl/cert.pem",
    "PYTHONNOUSERSITE" => "1",
    "PYTHONDONTWRITEBYTECODE" => "1"
  },
  "provenance" => %{
    "application_commit" => Keyword.fetch!(options, :application_commit),
    "application_archive_sha256" => digest,
    "runtime_commit" => B.json_read(Path.join(app, "sources.json"))["runtime_commit"],
    "engine_fingerprint" => fingerprint,
    "engine_build" => engine_receipt,
    "control_build" => control_receipt,
    "media_build" => media_receipt,
    "python_components" =>
      Map.take(python_receipt, ["python", "writer_wheels", "local_writer_imports"]),
    "components" => B.components()
  }
})

IO.puts("Verified desktop payload: #{output}")
