# Run only through a bundled release eval after reviewing the fixture descriptor.
# No managed Riff path or production launchd label is accepted here.
alias Riff.Installer.{Manifest, Payload, Session}

descriptor =
  System.fetch_env!("RIFF_INSTALLER_FIXTURE_REQUEST") |> File.read!() |> Jason.decode!()

root = Path.expand(Map.fetch!(descriptor, "root"))
label = Map.fetch!(descriptor, "service_label")
port = Map.fetch!(descriptor, "studio_port")
default_root = Path.join(System.user_home!(), "Library/Application Support/Riff")

unless root != default_root and String.starts_with?(label, "org.flip-engineering.riff.fixture.") and
         is_integer(port) and port in 1..65_535 and port != 7878,
       do: raise("Only a private fixture root, label and port are accepted")

if File.exists?(root),
  do: raise("Use a fresh fixture root; it must not already contain user data")

payload = Payload.load(Map.fetch!(descriptor, "payload"))
manifest = Payload.model_manifest(payload)
writer = Payload.writer_set(payload, root, [])

seed = fn pins, source, destination ->
  for asset <- Manifest.assets(pins) do
    existing = Path.join(source, asset.id)

    unless File.lstat!(existing).type == :regular,
      do: raise("Fixture seed assets must be regular files")

    target = Riff.Installer.Download.safe_target!(destination, asset.id)

    case File.ln(existing, target) do
      :ok -> :ok
      {:error, :exdev} -> File.cp!(existing, target)
      other -> raise("Fixture seed could not be prepared: #{inspect(other)}")
    end
  end
end

seed.(manifest, Map.fetch!(descriptor, "models_source"), Payload.model_directory(root, manifest))
if writer, do: seed.(writer.manifest, Map.fetch!(descriptor, "writer_source"), writer.directory)

{:ok, _} = Application.ensure_all_started(:bandit)
{:ok, _} = Application.ensure_all_started(:finch)
{:ok, _} = Application.ensure_all_started(:jason)

{:ok, _http} =
  Finch.start_link(
    name: Riff.Installer.HTTP,
    pools: %{default: [size: 1, count: 1, protocols: [:http1]]}
  )

{:ok, _tasks} = Task.Supervisor.start_link(name: Riff.Installer.Tasks)

{:ok, session} =
  Session.start_link(
    name: nil,
    install_root: root,
    payload: payload.directory,
    service_label: label,
    launch_agent_path: Path.join(root, "fixture-launch-agent.plist"),
    studio_port: port,
    desktop_app: true,
    desktop_app_path: Path.join(root, "Applications/Riff.app"),
    finish_after_open: true
  )

{:ok, server} =
  Bandit.start_link(
    plug: {Riff.Installer.Router, session: session},
    ip: {127, 0, 0, 1},
    port: 0,
    startup_log: false
  )

{:ok, {_, gui_port}} = ThousandIsland.listener_info(server)
url = "http://127.0.0.1:#{gui_port}"
Session.set_endpoint(url, session)

IO.puts(
  Jason.encode!(%{
    url: url,
    root: root,
    service_label: label,
    studio_port: port,
    app: Path.join(root, "Applications/Riff.app"),
    asset_ids: Enum.map(Session.status(session).assets, & &1.id)
  })
)

Process.sleep(:infinity)
