defmodule Riff.Installer.TestFixture do
  import Plug.Conn

  def init(options), do: options

  def call(conn, options) do
    owner = Keyword.fetch!(options, :owner)
    contents = Keyword.fetch!(options, :contents)
    send(owner, {:request, conn.request_path, get_req_header(conn, "range")})
    mode = Keyword.get(options, :mode, :range)
    [range | _] = get_req_header(conn, "range") ++ [nil]

    offset =
      if range,
        do:
          range
          |> String.trim_leading("bytes=")
          |> String.trim_trailing("-")
          |> String.to_integer(),
        else: 0

    cond do
      mode == :redirect and not String.ends_with?(conn.request_path, "/actual") ->
        conn |> put_resp_header("location", "/actual") |> send_resp(302, "redirect")

      mode == :loop ->
        conn |> put_resp_header("location", conn.request_path) |> send_resp(302, "loop")

      mode == :bad_range and offset > 0 ->
        conn
        |> put_resp_header(
          "content-range",
          "bytes 0-#{byte_size(contents) - 1}/#{byte_size(contents)}"
        )
        |> send_resp(206, contents)

      mode == :ignore_range ->
        send_resp(conn, 200, contents)

      mode == :stream ->
        stream(
          conn,
          contents,
          offset,
          Keyword.get(options, :chunk_size, 16_384),
          Keyword.get(options, :delay, 0),
          owner
        )

      offset > 0 ->
        conn
        |> put_resp_header(
          "content-range",
          "bytes #{offset}-#{byte_size(contents) - 1}/#{byte_size(contents)}"
        )
        |> send_resp(206, binary_part(contents, offset, byte_size(contents) - offset))

      true ->
        send_resp(conn, 200, contents)
    end
  end

  defp stream(conn, contents, offset, chunk_size, delay, owner) do
    conn =
      if offset > 0,
        do:
          put_resp_header(
            conn,
            "content-range",
            "bytes #{offset}-#{byte_size(contents) - 1}/#{byte_size(contents)}"
          ),
        else: conn

    conn = send_chunked(conn, if(offset > 0, do: 206, else: 200))
    chunks(conn, contents, offset, chunk_size, delay, owner)
  end

  defp chunks(conn, contents, offset, _chunk_size, _delay, _owner)
       when offset == byte_size(contents),
       do: conn

  defp chunks(conn, contents, offset, chunk_size, delay, owner) do
    length = min(chunk_size, byte_size(contents) - offset)
    if delay > 0, do: Process.sleep(delay)

    case chunk(conn, binary_part(contents, offset, length)) do
      {:ok, conn} ->
        send(owner, {:chunk, offset + length})
        chunks(conn, contents, offset + length, chunk_size, delay, owner)

      {:error, _} ->
        conn
    end
  end

  def start!(options) do
    {:ok, server} =
      Bandit.start_link(
        plug: {__MODULE__, Keyword.put(options, :owner, self())},
        ip: {127, 0, 0, 1},
        port: 0,
        startup_log: false
      )

    Process.unlink(server)
    {:ok, {_, port}} = ThousandIsland.listener_info(server)
    {server, "http://127.0.0.1:#{port}"}
  end

  def asset(contents, base_url, id \\ "model.gguf") do
    %{
      id: id,
      label: "Test model",
      bytes: byte_size(contents),
      sha256: sha(contents),
      url: base_url <> "/" <> id
    }
  end

  def manifest(contents, names \\ ["model.gguf"]) do
    %{
      "repo" => "test/model",
      "revision" => String.duplicate("a", 40),
      "files" =>
        Map.new(names, &{&1, %{"bytes" => byte_size(contents), "sha256" => sha(contents)}})
    }
  end

  def sha(data), do: :crypto.hash(:sha256, data) |> Base.encode16(case: :lower)
end
