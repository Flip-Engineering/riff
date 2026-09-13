defmodule Riff.Runtime.SchedulerPort do
  @moduledoc """
  A private JSON-line entry point for the current studio's process bridge.

  Scheduling keeps its synchronous request/reply protocol. Artifact start,
  result and cancel requests use the same channel, with file work supervised
  separately inside this VM. A result is consumed when returned; cancellation
  and EOF release this port's workers and results without touching model work.
  """

  alias Riff.Runtime.{Admission, ScoreArtifact}

  def main(options \\ []) do
    io = Keyword.get(options, :io, :standard_io)
    runner = Keyword.get(options, :artifact_runner, &artifact_operation!/1)
    :io.setopts(io, encoding: :unicode)
    {:ok, supervisor} = Task.Supervisor.start_link()
    owner = self()
    reader = spawn_link(fn -> read_input(owner, io) end)

    state = %{
      admission: Admission.new(),
      artifacts: %{},
      monitors: %{},
      supervisor: supervisor,
      runner: runner
    }

    send(reader, :read)

    try do
      loop(state, reader, io)
    after
      Process.unlink(reader)
      if Process.alive?(reader), do: Process.exit(reader, :kill)
      if Process.alive?(supervisor), do: Supervisor.stop(supervisor, :normal, :infinity)
    end
  end

  # One outstanding read retains the existing request/reply exchange, while
  # the owner can collect task completion during idle stdin.
  defp read_input(owner, io) do
    receive do
      :read ->
        case IO.read(io, :line) do
          line when is_binary(line) ->
            send(owner, {:input, self(), line})
            read_input(owner, io)

          _ ->
            send(owner, {:input_end, self()})
        end
    end
  end

  defp loop(state, reader, io) do
    receive do
      {:input, ^reader, line} ->
        {next, response} = dispatch(state, line)
        IO.puts(io, Jason.encode!(response))
        send(reader, :read)
        loop(next, reader, io)

      {:input_end, ^reader} ->
        :ok

      {ref, response} when is_reference(ref) ->
        Process.demonitor(ref, [:flush])
        loop(complete(state, ref, response), reader, io)

      {:DOWN, ref, :process, _pid, _reason} ->
        response = %{
          "state" => "failed",
          "error" =>
            "The saved score operation ended before completing. Its files have been kept."
        }

        loop(complete(state, ref, response), reader, io)
    end
  end

  defp dispatch(state, line) do
    case Jason.decode(line) do
      {:ok, %{"op" => "estimate", "inputs" => inputs}} when is_map(inputs) ->
        {state,
         %{"state" => "estimated", "requirement" => Riff.Runtime.Resources.estimate(inputs)}}

      {:ok, %{"op" => "artifact_start"} = request} ->
        start_artifact(state, request)

      {:ok, %{"op" => "artifact_result", "id" => id}} when is_binary(id) ->
        artifact_result(state, id)

      {:ok, %{"op" => "artifact_cancel", "id" => id}} when is_binary(id) ->
        cancel_artifact(state, id)

      {:ok, message} when is_map(message) ->
        {admission, response} = Admission.step(state.admission, message)
        {%{state | admission: admission}, response}

      _ ->
        {state, %{"state" => "error", "error" => "Expected a JSON scheduling request."}}
    end
  rescue
    _ -> {state, %{"state" => "error", "error" => "Invalid scheduling observation."}}
  end

  defp start_artifact(state, request) do
    id = request["id"]

    cond do
      not valid_artifact_request?(request) ->
        {state, %{"state" => "error", "error" => "Invalid saved score operation."}}

      Map.has_key?(state.artifacts, id) ->
        {state, %{"state" => "error", "error" => "This saved score operation already exists."}}

      true ->
        runner = state.runner

        task =
          Task.Supervisor.async_nolink(
            state.supervisor,
            fn -> run_artifact(runner, request) end,
            shutdown: :brutal_kill
          )

        next = %{
          state
          | artifacts: Map.put(state.artifacts, id, {:running, task}),
            monitors: Map.put(state.monitors, task.ref, id)
        }

        {next, %{"state" => "started", "id" => id}}
    end
  end

  defp valid_artifact_request?(request) do
    contract? =
      is_map(request["contract"]) or
        (request["action"] == "resolve" and Map.has_key?(request, "contract") and
           is_nil(request["contract"]))

    base =
      is_binary(request["id"]) and request["id"] != "" and
        is_binary(request["root"]) and contract?

    base and
      case request["action"] do
        "capture" ->
          is_binary(request["source_root"]) and is_binary(request["source_path"]) and
            is_map(request["provenance"])

        "resolve" ->
          is_binary(request["artifact_id"])

        "prepare" ->
          is_binary(request["artifact_id"]) and is_binary(request["input_directory"])

        _ ->
          false
      end
  end

  defp run_artifact(runner, request) do
    %{"state" => "complete", "result" => runner.(request)}
  rescue
    error in ScoreArtifact.Error ->
      %{"state" => "failed", "error" => Exception.message(error)}

    _ ->
      %{
        "state" => "failed",
        "error" =>
          "The saved score could not be read or saved. Its existing files have been kept."
      }
  end

  defp artifact_operation!(%{"action" => "capture"} = request) do
    ScoreArtifact.capture_file!(
      request["root"],
      request["source_root"],
      request["source_path"],
      request["contract"],
      request["provenance"]
    )
  end

  defp artifact_operation!(%{"action" => "resolve"} = request) do
    ScoreArtifact.resolve!(request["root"], request["artifact_id"], request["contract"])
  end

  defp artifact_operation!(%{"action" => "prepare"} = request) do
    ScoreArtifact.prepare!(
      request["root"],
      request["artifact_id"],
      request["contract"],
      request["input_directory"]
    )
  end

  defp complete(state, ref, response) do
    case Map.pop(state.monitors, ref) do
      {nil, _} ->
        state

      {id, monitors} ->
        %{
          state
          | monitors: monitors,
            artifacts: Map.put(state.artifacts, id, {:complete, Map.put(response, "id", id)})
        }
    end
  end

  defp artifact_result(state, id) do
    case Map.get(state.artifacts, id) do
      nil ->
        {state, %{"state" => "missing", "id" => id}}

      {:running, _} ->
        {state, %{"state" => "working", "id" => id}}

      {:complete, response} ->
        {%{state | artifacts: Map.delete(state.artifacts, id)}, response}
    end
  end

  defp cancel_artifact(state, id) do
    monitors =
      case Map.get(state.artifacts, id) do
        {:running, task} ->
          Task.Supervisor.terminate_child(state.supervisor, task.pid)
          Process.demonitor(task.ref, [:flush])
          Map.delete(state.monitors, task.ref)

        _ ->
          state.monitors
      end

    {%{state | artifacts: Map.delete(state.artifacts, id), monitors: monitors},
     %{"state" => "cancelled", "id" => id}}
  end
end
