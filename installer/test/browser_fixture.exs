alias Riff.Installer.{Session, TestFixture}
contents = :crypto.strong_rand_bytes(524_288)
names = ["music.gguf", "decoder.gguf", "sidecars/config.json"]
{_files, base_url} = TestFixture.start!(contents: contents, mode: :stream, delay: 2)

destination =
  Path.expand(
    "_build/browser-fixture-" <> Base.url_encode64(:crypto.strong_rand_bytes(8), padding: false)
  )

{:ok, session} =
  Session.start_link(
    name: Riff.Installer.BrowserFixture,
    manifest: TestFixture.manifest(contents, names),
    destination: destination,
    base_url: base_url,
    allow_local_http: true
  )

{:ok, server} =
  Bandit.start_link(
    plug: {Riff.Installer.Router, session: session},
    ip: {127, 0, 0, 1},
    port: 0,
    startup_log: false
  )

{:ok, {_, port}} = ThousandIsland.listener_info(server)
url = "http://127.0.0.1:#{port}"
Session.set_endpoint(url, session)
IO.puts(Jason.encode!(%{url: url, asset_ids: names, destination: destination}))
Process.sleep(:infinity)
