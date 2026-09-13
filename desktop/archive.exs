Code.require_file("support.exs", __DIR__)
alias Riff.Desktop.Build, as: B

{options, [], []} =
  OptionParser.parse(System.argv(), strict: [payload: :string, output: :string, epoch: :integer])

payload = options |> Keyword.fetch!(:payload) |> Path.expand()
manifest = B.json_read(Path.join(payload, "manifest.json"))

unless manifest["format_version"] == 1 and Regex.match?(~r/^\d+\.\d+\.\d+$/, manifest["version"]) and
         manifest["platform"] == "macos-arm64",
       do: raise("Unsupported desktop manifest")

expected = manifest["files"]
actual = B.records(payload) |> Enum.reject(&(&1["path"] == "manifest.json"))
unless expected == actual, do: raise("Payload files differ from their verified manifest")
output = options |> Keyword.fetch!(:output) |> Path.expand()
archive = Path.join(output, "riff-v#{manifest["version"]}-#{manifest["platform"]}.payload.tar.gz")
B.archive!(payload, archive, Keyword.fetch!(options, :epoch))

B.json_write(archive <> ".json", %{
  "version" => manifest["version"],
  "platform" => manifest["platform"],
  "asset" => Path.basename(archive),
  "bytes" => File.stat!(archive).size,
  "sha256" => B.hash(archive),
  "runtime_id" => manifest["runtime_id"]
})

IO.puts("Desktop update asset: #{archive}")
