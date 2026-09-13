defmodule Riff.Runtime.SchedulerPortTest do
  use ExUnit.Case, async: true
  alias Riff.Installer.TestSchedulerIO, as: Device
  alias Riff.Runtime.SchedulerPort

  @contract %{
    "format" => "riff.yue2.score-tokens.v1",
    "prefix_contract" => "riff.yue2.prefix.v1",
    "tokenizer_sha256" => String.duplicate("a", 64)
  }

  defp start_port(options \\ []) do
    io = Device.start(self())
    {pid, ref} = spawn_monitor(fn -> SchedulerPort.main([io: io] ++ options) end)

    on_exit(fn ->
      if Process.alive?(pid) do
        monitor = Process.monitor(pid)
        Device.feed(io, :eof)

        receive do
          {:DOWN, ^monitor, :process, ^pid, _} -> :ok
        after
          1000 -> Process.exit(pid, :kill)
        end
      end

      if Process.alive?(io), do: Process.exit(io, :kill)
    end)

    %{pid: pid, monitor: ref, io: io}
  end

  defp call(port, message) do
    Device.feed(port.io, Jason.encode!(message) <> "\n")
    io = port.io
    assert_receive {:scheduler_output, ^io, line}, 1000
    Jason.decode!(line)
  end

  defp request(id) do
    %{
      "op" => "artifact_start",
      "id" => id,
      "action" => "resolve",
      "root" => "/fixture",
      "artifact_id" => "fixture",
      "contract" => @contract
    }
  end

  defp blocking_runner do
    observer = self()

    fn request ->
      send(observer, {:artifact_worker, request["id"], self()})

      receive do
        {:complete, value} -> value
        :fail -> raise "test-only worker failure"
      end
    end
  end

  defp admit(port, id) do
    response =
      call(port, %{
        "op" => "request",
        "id" => id,
        "requirement" => %{"host_peak" => 1},
        "snapshot" => %{"host" => %{"available" => 100, "pressure" => "normal"}}
      })

    assert response["state"] == "admitted"
  end

  defp await_result(port, id, deadline \\ nil) do
    deadline = deadline || System.monotonic_time(:millisecond) + 1000

    case call(port, %{"op" => "artifact_result", "id" => id}) do
      %{"state" => "working"} ->
        assert System.monotonic_time(:millisecond) < deadline
        await_result(port, id, deadline)

      result ->
        result
    end
  end

  test "blocked artifact work leaves existing admission and release responsive" do
    port = start_port(artifact_runner: blocking_runner())
    admit(port, "music")
    assert call(port, request("score")) == %{"state" => "started", "id" => "score"}
    assert_receive {:artifact_worker, "score", worker}
    assert call(port, %{"op" => "artifact_result", "id" => "score"})["state"] == "working"
    assert call(port, %{"op" => "status"})["active"] == ["music"]
    assert call(port, %{"op" => "release", "id" => "music"})["state"] == "released"
    assert call(port, %{"op" => "estimate", "inputs" => %{}})["state"] == "estimated"
    send(worker, {:complete, %{"saved" => true}})

    assert await_result(port, "score") == %{
             "state" => "complete",
             "id" => "score",
             "result" => %{"saved" => true}
           }

    assert call(port, %{"op" => "artifact_result", "id" => "score"})["state"] == "missing"
  end

  test "cancel owns only its worker and forgets pending and completed results" do
    port = start_port(artifact_runner: blocking_runner())

    other =
      spawn(fn ->
        receive do
          :stop -> :ok
        end
      end)

    on_exit(fn -> if Process.alive?(other), do: Process.exit(other, :kill) end)
    for id <- ["first", "second"], do: assert(call(port, request(id))["state"] == "started")
    assert_receive {:artifact_worker, "first", first}
    assert_receive {:artifact_worker, "second", second}
    first_ref = Process.monitor(first)
    assert call(port, %{"op" => "artifact_cancel", "id" => "first"})["state"] == "cancelled"
    assert_receive {:DOWN, ^first_ref, :process, ^first, _}
    assert Process.alive?(second) and Process.alive?(other)
    assert call(port, %{"op" => "artifact_result", "id" => "first"})["state"] == "missing"
    send(second, {:complete, %{"saved" => true}})
    second_ref = Process.monitor(second)
    assert_receive {:DOWN, ^second_ref, :process, ^second, _}
    assert call(port, %{"op" => "artifact_cancel", "id" => "second"})["state"] == "cancelled"
    assert call(port, %{"op" => "artifact_result", "id" => "second"})["state"] == "missing"
    assert call(port, %{"op" => "artifact_cancel", "id" => "second"})["state"] == "cancelled"
  end

  test "worker exceptions and unexpected exits do not discard model reservations" do
    port = start_port(artifact_runner: blocking_runner())
    admit(port, "music")

    for {id, method} <- [{"exception", :exception}, {"exit", :exit}] do
      assert call(port, request(id))["state"] == "started"
      assert_receive {:artifact_worker, ^id, worker}
      if method == :exception, do: send(worker, :fail), else: Process.exit(worker, :kill)
      assert await_result(port, id)["state"] == "failed"
      assert call(port, %{"op" => "artifact_result", "id" => id})["state"] == "missing"
      assert call(port, %{"op" => "status"})["active"] == ["music"]
    end

    assert call(port, %{"op" => "release", "id" => "music"})["state"] == "released"
  end

  test "duplicate identities and malformed actions preserve the original operation" do
    port = start_port(artifact_runner: blocking_runner())
    assert call(port, request("original"))["state"] == "started"
    assert_receive {:artifact_worker, "original", worker}
    assert call(port, request("original"))["state"] == "error"
    assert call(port, Map.put(request("bad"), "action", "shell"))["state"] == "error"
    assert call(port, Map.put(request("bad"), "contract", []))["state"] == "error"
    assert call(port, %{"op" => "artifact_result", "id" => "bad"})["state"] == "missing"
    assert Process.alive?(worker)
    send(worker, {:complete, %{}})
    assert await_result(port, "original")["state"] == "complete"
  end

  @tag capture_log: true
  test "EOF and owner crash stop only that port's tasks" do
    unrelated =
      spawn(fn ->
        receive do
          :stop -> :ok
        end
      end)

    on_exit(fn -> if Process.alive?(unrelated), do: Process.exit(unrelated, :kill) end)

    for ending <- [:eof, :crash] do
      port = start_port(artifact_runner: blocking_runner())
      assert call(port, request("owned"))["state"] == "started"
      assert_receive {:artifact_worker, "owned", worker}
      worker_ref = Process.monitor(worker)
      if ending == :eof, do: Device.feed(port.io, :eof), else: Process.exit(port.pid, :kill)
      pid = port.pid
      monitor = port.monitor
      assert_receive {:DOWN, ^monitor, :process, ^pid, reason}, 1000
      if ending == :eof, do: assert(reason == :normal)
      assert_receive {:DOWN, ^worker_ref, :process, ^worker, _}, 1000
      assert Process.alive?(unrelated)
    end
  end

  test "real file operations round-trip without raw token arrays on the wire" do
    root = Path.expand("../_build/score-port-#{System.unique_integer([:positive])}", __DIR__)
    for child <- ["artifacts", "outputs", "inputs"], do: File.mkdir_p!(Path.join(root, child))
    on_exit(fn -> File.rm_rf!(root) end)
    source = Path.join(root, "outputs/empty.plan.json")
    File.write!(source, ~s({"tokens":[],"truncated":true}))
    port = start_port()

    capture = %{
      "op" => "artifact_start",
      "id" => "capture",
      "action" => "capture",
      "root" => Path.join(root, "artifacts"),
      "contract" => @contract,
      "source_root" => Path.join(root, "outputs"),
      "source_path" => source,
      "provenance" => %{"job" => "test"}
    }

    assert call(port, capture)["state"] == "started"
    result = await_result(port, "capture")
    assert result["state"] == "complete"
    saved = result["result"]
    assert saved["descriptor"]["score"]["token_count"] == 0
    refute Map.has_key?(saved, "tokens")

    for action <- ["resolve", "prepare"] do
      message = %{
        "op" => "artifact_start",
        "id" => action,
        "action" => action,
        "root" => capture["root"],
        "contract" => @contract,
        "artifact_id" => saved["id"],
        "input_directory" => Path.join(root, "inputs")
      }

      assert call(port, message)["state"] == "started"
      assert await_result(port, action)["state"] == "complete"
    end

    assert File.read!(Path.join(root, "inputs/score.json")) == File.read!(source)

    historical = %{
      "op" => "artifact_start",
      "id" => "historical",
      "action" => "resolve",
      "root" => capture["root"],
      "contract" => nil,
      "artifact_id" => saved["id"]
    }

    assert call(port, historical)["state"] == "started"
    assert await_result(port, "historical")["result"]["descriptor"]["contract"] == @contract

    assert call(port, Map.delete(%{historical | "id" => "missing-contract"}, "contract"))["state"] ==
             "error"

    assert call(
             port,
             Map.merge(historical, %{
               "id" => "invalid-prepare",
               "action" => "prepare",
               "input_directory" => Path.join(root, "inputs")
             })
           )["state"] == "error"

    File.write!(source, ~s({"tokens":[true]}))
    assert call(port, %{capture | "id" => "invalid"})["state"] == "started"
    assert await_result(port, "invalid")["state"] == "failed"
    assert call(port, %{"op" => "status"})["state"] == "ready"
  end
end
