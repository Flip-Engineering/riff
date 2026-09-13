defmodule Riff.Runtime.SchedulerPort do
  @moduledoc """
  A private JSON-line entry point for the current studio's process bridge.

  The packaged ERTS release runs this via `eval`; the graphical installer is
  not started. EOF ends the process, so no global daemon or service is needed.
  """

  alias Riff.Runtime.Admission

  def main do
    :io.setopts(:standard_io, encoding: :unicode)
    loop(Admission.new())
  end

  defp loop(state) do
    case IO.read(:stdio, :line) do
      :eof ->
        :ok

      {:error, _} ->
        :ok

      line ->
        {next, response} = dispatch(state, line)
        IO.puts(:stdio, Jason.encode!(response))
        loop(next)
    end
  end

  defp dispatch(state, line) do
    case Jason.decode(line) do
      {:ok, %{"op" => "estimate", "inputs" => inputs}} when is_map(inputs) ->
        {state,
         %{"state" => "estimated", "requirement" => Riff.Runtime.Resources.estimate(inputs)}}

      {:ok, message} when is_map(message) ->
        Admission.step(state, message)

      _ ->
        {state, %{"state" => "error", "error" => "Expected a JSON scheduling request."}}
    end
  rescue
    _ -> {state, %{"state" => "error", "error" => "Invalid scheduling observation."}}
  end
end
