Code.require_file("support.exs", __DIR__)
alias Riff.Desktop.Build, as: B

{options, [], []} = OptionParser.parse(System.argv(), strict: [output: :string])
root = options |> Keyword.fetch!(:output) |> Path.expand()
if File.exists?(root), do: raise("Choose a fresh Python compatibility build directory")
File.mkdir_p!(root)
source = B.unpack!(B.fetch!("python"), Path.join(root, "source"))
python = Path.join(root, "python")
B.copy_tree!(Path.join(source, "python"), python)
File.rm_rf!(source)
pins = B.json_read(Path.join(B.root(), "writer-wheels-macos-arm64.json"))
requirements = Path.join(Path.dirname(B.root()), "requirements-writer.lock")

unless B.hash(requirements) == pins["requirements_sha256"],
  do: raise("Refresh and verify binary wheel pins for the changed writer lock")

cache = Path.join(B.root(), ".cache/writer-wheels")
File.mkdir_p!(cache)

wheels =
  for item <- pins["files"] do
    target = Path.join(cache, item["filename"])

    unless File.exists?(target) do
      partial = target <> ".download"

      B.run!("curl", [
        "--fail",
        "--location",
        "--silent",
        "--show-error",
        "--proto",
        "=https",
        "--output",
        partial,
        item["url"]
      ])

      B.verify!(partial, item["bytes"], item["sha256"])
      File.rename!(partial, target)
    end

    B.verify!(target, item["bytes"], item["sha256"])
  end

executable = Path.join(python, "bin/python3.14")

environment = [
  {"PYTHONPATH", nil},
  {"PYTHONHOME", nil},
  {"PYTHONDONTWRITEBYTECODE", "1"},
  {"SSL_CERT_FILE", "/etc/ssl/cert.pem"}
]

log =
  B.run!(
    executable,
    [
      "-I",
      "-B",
      "-m",
      "pip",
      "--isolated",
      "install",
      "--disable-pip-version-check",
      "--no-index",
      "--no-deps",
      "--no-compile" | wheels
    ],
    env: environment
  )

File.write!(Path.join(root, "install.log"), log)
B.run!(executable, ["-I", "-B", "-m", "pip", "check"], env: environment)
# Package the interpreter and importable modules. Generated console commands
# contain build-host shebangs and are not part of Riff's application interface.
for name <- File.ls!(Path.join(python, "bin")), name not in ["python", "python3", "python3.14"] do
  File.rm!(Path.join([python, "bin", name]))
end

# Local-wheel provenance has build-host file URLs. The verified public wheel
# manifest replaces that optional installation metadata in the distributed app.
for metadata <- Path.wildcard(Path.join(python, "lib/python3.14/site-packages/*.dist-info")) do
  direct = Path.join(metadata, "direct_url.json")
  if File.exists?(direct), do: File.rm!(direct)
  record = Path.join(metadata, "RECORD")

  if File.exists?(record) do
    relative = Path.basename(metadata) <> "/direct_url.json,"

    lines =
      File.read!(record)
      |> String.split("\n")
      |> Enum.reject(&String.starts_with?(&1, [relative, "../../../bin/", "../../bin/"]))

    File.write!(record, Enum.join(lines, "\n"))
  end
end

imports =
  B.run!(
    executable,
    [
      "-I",
      "-B",
      "-c",
      "import json,mlx.core as mx,mlx_lm,numpy,transformers; print(json.dumps(dict(mlx=mx.__version__,numpy=numpy.__version__,transformers=transformers.__version__)))"
    ],
    env: environment
  )

File.write!(Path.join(root, "imports.json"), imports)
B.inspect_macos!(python, verified_vendor: true)

receipt = %{
  "python" => B.components()["python"],
  "writer_wheels" => pins,
  "files" => B.records(python),
  "local_writer_imports" => :json.decode(imports)
}

B.json_write(Path.join(root, "python.json"), receipt)
IO.puts("Portable application and local-writer runtime ready: #{python}")
