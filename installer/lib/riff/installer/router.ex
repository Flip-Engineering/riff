defmodule Riff.Installer.Router do
  @moduledoc false
  import Plug.Conn
  alias Riff.Installer.Session

  def init(options), do: options

  def call(conn, options) do
    session = Keyword.get(options, :session, Session)

    conn =
      conn
      |> put_resp_header("cache-control", "no-store")
      |> put_resp_header("x-content-type-options", "nosniff")
      |> put_resp_header("referrer-policy", "no-referrer")
      |> put_resp_header(
        "content-security-policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
      )

    cond do
      conn.remote_ip != {127, 0, 0, 1} or conn.host not in ["127.0.0.1", "localhost"] ->
        json(conn, 403, %{error: "Setup is available from this device only."})

      String.starts_with?(conn.request_path, "/api/") ->
        if authorized?(conn, session),
          do: api(conn, session),
          else: json(conn, 403, %{error: "Reopen the setup window to continue."})

      conn.method in ["GET", "HEAD"] and conn.request_path in ["/", "/index.html"] ->
        index(conn, session, options)

      conn.method in ["GET", "HEAD"] ->
        directory =
          Keyword.get(options, :static, Application.app_dir(:riff_installer, "priv/static"))

        conn = Plug.Static.call(conn, Plug.Static.init(at: "/", from: directory, index: false))
        if conn.halted, do: conn, else: send_resp(conn, 404, "Not found")

      true ->
        send_resp(conn, 405, "Method not allowed")
    end
  end

  defp authorized?(conn, session) do
    token = get_req_header(conn, "x-riff-installer-token")
    origin = get_req_header(conn, "origin")
    endpoint = Session.endpoint(session) || "http://#{conn.host}:#{conn.port}"
    site = get_req_header(conn, "sec-fetch-site")

    origin in [[], [endpoint]] and site not in [["cross-site"]] and
      case token do
        [provided] -> Plug.Crypto.secure_compare(provided, Session.token(session))
        _ -> false
      end
  end

  defp api(%{method: "GET", request_path: "/api/status"} = conn, session),
    do: json(conn, 200, Session.status(session))

  defp api(%{method: "POST", request_path: path} = conn, session)
       when path in ["/api/install", "/api/retry"],
       do: result(conn, Session.install(session))

  defp api(%{method: "POST", request_path: "/api/cancel"} = conn, session),
    do: result(conn, Session.cancel(session))

  defp api(%{method: "POST", request_path: "/api/open"} = conn, session),
    do: result(conn, Session.open(session))

  defp api(conn, _session), do: json(conn, 404, %{error: "This setup action is unavailable."})

  defp result(conn, {:ok, state}), do: json(conn, 200, state)

  defp result(conn, {:error, :busy, state}),
    do: json(conn, 409, Map.put(state, :error, "Setup is already running."))

  defp result(conn, {:error, :app_unavailable, state}),
    do:
      json(
        conn,
        409,
        Map.put(state, :error, "The Riff application package is not ready to open yet.")
      )

  defp index(conn, session, options) do
    directory = Keyword.get(options, :static, Application.app_dir(:riff_installer, "priv/static"))

    case File.read(Path.join(directory, "index.html")) do
      {:ok, html} ->
        conn
        |> put_resp_content_type("text/html")
        |> send_resp(200, String.replace(html, "RIFF_INSTALLER_TOKEN", Session.token(session)))

      _ ->
        send_resp(conn, 503, "The graphical setup assets are missing from this package.")
    end
  end

  defp json(conn, code, body),
    do: conn |> put_resp_content_type("application/json") |> send_resp(code, Jason.encode!(body))
end
