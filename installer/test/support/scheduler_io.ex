defmodule Riff.Installer.TestSchedulerIO do
  @moduledoc false

  # An Erlang IO device keeps lifecycle tests deterministic: reads can remain
  # pending, while real SchedulerPort replies and task completions proceed.
  def start(observer), do: spawn(fn -> loop(observer, nil, []) end)
  def feed(io, input), do: send(io, {:feed, input})

  defp loop(observer, reader, inputs) do
    receive do
      {:feed, value} ->
        if reader do
          {from, ref} = reader
          send(from, {:io_reply, ref, value})
          loop(observer, nil, inputs)
        else
          loop(observer, nil, inputs ++ [value])
        end

      {:io_request, from, ref, {:get_line, _, _}} ->
        case inputs do
          [] ->
            loop(observer, {from, ref}, [])

          [value | rest] ->
            send(from, {:io_reply, ref, value})
            loop(observer, nil, rest)
        end

      {:io_request, from, ref, {:setopts, _}} ->
        send(from, {:io_reply, ref, :ok})
        loop(observer, reader, inputs)

      {:io_request, from, ref, {:put_chars, _, chars}} ->
        send(observer, {:scheduler_output, self(), IO.iodata_to_binary(chars)})
        send(from, {:io_reply, ref, :ok})
        loop(observer, reader, inputs)

      {:io_request, from, ref, {:put_chars, _, module, function, arguments}} ->
        send(
          observer,
          {:scheduler_output, self(), apply(module, function, arguments) |> IO.iodata_to_binary()}
        )

        send(from, {:io_reply, ref, :ok})
        loop(observer, reader, inputs)
    end
  end
end
