defmodule Riff.Installer.DesktopRestartTest do
  use ExUnit.Case, async: false
  alias Riff.Installer.{Desktop, Download}

  test "accepted restart survives real HTTP disconnects and resets until connection refusal" do
    {url, listener} = listener([:close, :serving, :reset, :refuse])
    owner = self()

    assert :ok =
             Desktop.wait_until_closed(
               url,
               fn stage, message -> send(owner, {:progress, stage, message}) end,
               shutdown_timeout: 5_000
             )

    assert_received {:response, ^listener, :close}
    assert_received {:response, ^listener, :serving}
    assert_received {:response, ^listener, :reset}
    assert_received {:listener_closed, ^listener}
    assert_received {:progress, :starting, "Finishing the current session"}
  end

  test "a listener that keeps dropping HTTP connections is never considered stopped" do
    {url, listener} = listener(:keep_closing)

    assert_raise Download.Error, ~r/did not finish closing its previous session/, fn ->
      Desktop.wait_until_closed(url, fn _, _ -> :ok end, shutdown_timeout: 300)
    end

    assert Process.alive?(listener)
    refute_received {:listener_closed, ^listener}
  end

  test "a request timeout during accepted shutdown is retried until the listener closes" do
    {url, listener} = listener([:timeout, :refuse])

    assert :ok =
             Desktop.wait_until_closed(
               url,
               fn _, _ -> send(listener, :finish_timeout) end,
               shutdown_timeout: 5_000
             )

    assert_received {:response, ^listener, :timeout}
    assert_received {:listener_closed, ^listener}
  end

  test "a permanent HTTP refusal is an error, not shutdown confirmation" do
    {url, listener} = listener(:unauthorized)

    assert_raise Download.Error, ~r/could not confirm that its previous session ended/, fn ->
      Desktop.wait_until_closed(url, fn _, _ -> :ok end, shutdown_timeout: 5_000)
    end

    assert_received {:response, ^listener, :unauthorized}
    assert Process.alive?(listener)
    refute_received {:listener_closed, ^listener}
  end

  defp listener(modes) do
    owner = self()
    {:ok, socket} = :gen_tcp.listen(0, [:binary, active: false, ip: {127, 0, 0, 1}])
    {:ok, {_, port}} = :inet.sockname(socket)

    process =
      spawn(fn ->
        receive do
          :start -> serve(socket, modes, owner)
        end
      end)

    :ok = :gen_tcp.controlling_process(socket, process)
    send(process, :start)
    on_exit(fn -> if Process.alive?(process), do: Process.exit(process, :shutdown) end)
    {"http://127.0.0.1:#{port}", process}
  end

  defp serve(socket, [:refuse], owner) do
    :ok = :gen_tcp.close(socket)
    send(owner, {:listener_closed, self()})
  end

  defp serve(socket, modes, owner) do
    {:ok, client} = :gen_tcp.accept(socket)
    {:ok, _request} = :gen_tcp.recv(client, 0, 5_000)
    {mode, remaining} = if is_list(modes), do: {hd(modes), tl(modes)}, else: {modes, modes}

    case mode do
      :serving ->
        respond(client, "200 OK", %{version: "previous", engine: %{ready: true}})

      :unauthorized ->
        respond(client, "403 Forbidden", %{error: "not authorized"})

      :reset ->
        :ok = :inet.setopts(client, linger: {true, 0})

      :timeout ->
        receive do
          :finish_timeout -> :ok
        end

      _ ->
        :ok
    end

    :gen_tcp.close(client)
    send(owner, {:response, self(), mode})
    serve(socket, remaining, owner)
  end

  defp respond(client, status, value) do
    body = Jason.encode!(value)

    :gen_tcp.send(
      client,
      "HTTP/1.1 #{status}\r\nContent-Type: application/json\r\nContent-Length: #{byte_size(body)}\r\nConnection: close\r\n\r\n#{body}"
    )
  end
end
