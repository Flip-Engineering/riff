defmodule Riff.Installer.RouterTest do
  use ExUnit.Case, async: false
  import Plug.Test
  import Plug.Conn
  alias Riff.Installer.{Router, Session, TestFixture}

  setup do
    directory =
      Path.join(
        System.tmp_dir!(),
        "riff-installer-router-" <>
          Base.url_encode64(:crypto.strong_rand_bytes(12), padding: false)
      )

    session =
      start_supervised!(
        {Session, name: nil, manifest: TestFixture.manifest("model"), destination: directory}
      )

    Session.set_endpoint("http://127.0.0.1:51000", session)
    on_exit(fn -> File.rm_rf!(directory) end)
    %{session: session, directory: directory, token: Session.token(session)}
  end

  test "serves the GUI with a private per-launch token and restrictive browser headers",
       context do
    conn = conn(:get, "http://127.0.0.1:51000/") |> Router.call(session: context.session)
    assert conn.status == 200
    assert conn.resp_body =~ context.token
    refute conn.resp_body =~ "RIFF_INSTALLER_TOKEN"
    assert get_resp_header(conn, "cache-control") == ["no-store"]
    assert [policy] = get_resp_header(conn, "content-security-policy")
    assert policy =~ "frame-ancestors 'none'"
    assert policy =~ "connect-src 'self'"
  end

  test "requires the launch token for both status and every mutation", context do
    for {method, path} <- [
          {:get, "status"},
          {:post, "install"},
          {:post, "cancel"},
          {:post, "retry"},
          {:post, "open"}
        ] do
      conn =
        conn(method, "http://127.0.0.1:51000/api/" <> path)
        |> Router.call(session: context.session)

      assert conn.status == 403
    end

    refute File.exists?(context.directory)

    conn =
      conn(:get, "http://127.0.0.1:51000/api/status")
      |> put_req_header("x-riff-installer-token", context.token)
      |> Router.call(session: context.session)

    assert conn.status == 200
    assert Jason.decode!(conn.resp_body)["state"] == "idle"
    refute conn.resp_body =~ context.token
  end

  test "rejects foreign origins, DNS rebinding hosts, and remote connections even with a token",
       context do
    base =
      conn(:post, "http://127.0.0.1:51000/api/install")
      |> put_req_header("x-riff-installer-token", context.token)

    assert (base
            |> put_req_header("origin", "https://elsewhere.example")
            |> Router.call(session: context.session)).status == 403

    assert (base
            |> put_req_header("sec-fetch-site", "cross-site")
            |> Router.call(session: context.session)).status == 403

    assert Router.call(%{base | host: "attacker.example"}, session: context.session).status == 403

    assert Router.call(%{base | remote_ip: {100, 64, 0, 1}}, session: context.session).status ==
             403

    refute File.exists?(context.directory)
  end
end
