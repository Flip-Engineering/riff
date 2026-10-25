ExUnit.start()
Code.require_file("../lib/riff/runtime/admission.ex", __DIR__)
Code.require_file("../lib/riff/runtime/resources.ex", __DIR__)

defmodule Riff.Runtime.ResidentTest do
  use ExUnit.Case, async: true
  alias Riff.Runtime.Admission
  @protocol "riff.resident.v1"

  defp snapshot(host, device \\ 0, pressure \\ "normal", reserve \\ 0),
    do: %{
      "host" => %{"available" => host, "reserve" => reserve, "pressure" => pressure},
      "devices" => %{"gpu" => %{"available" => device}}
    }

  defp requirement(host \\ 1_000, device \\ 0),
    do: %{
      "host_peak" => host,
      "device_peak" => device,
      "device" => if(device > 0, do: "gpu"),
      "basis" => %{"weights" => 600}
    }

  defp resident(state, device \\ 0) do
    Admission.step(state, %{
      "op" => "resident",
      "action" => "update",
      "protocol" => @protocol,
      "id" => "engine",
      "requirement" => requirement(1_000, device),
      "usage" => %{"host" => 600, "device" => if(device > 0, do: 600, else: 0)}
    })
  end

  defp request(state, id, requirement, snapshot, extra \\ %{}),
    do:
      Admission.step(
        state,
        Map.merge(
          %{"op" => "request", "id" => id, "requirement" => requirement, "snapshot" => snapshot},
          extra
        )
      )

  defp fit(state, memory, extra \\ %{}),
    do:
      Admission.step(
        state,
        Map.merge(
          %{
            "op" => "resident",
            "action" => "fit",
            "protocol" => @protocol,
            "id" => "next",
            "requirement" => requirement(),
            "snapshot" => memory
          },
          extra
        )
      )

  test "resident charge is measured, and a named warm request removes weight double counting" do
    {state, registered} = resident(Admission.new())
    assert registered["requirement"]["host_peak"] == 600
    {state, _} = request(state, "writer", %{"host_peak" => 100}, snapshot(600))
    {state, cold} = request(state, "cold", requirement(), snapshot(600))
    assert cold["state"] == "waiting"

    {_, warm} =
      request(state, "warm", requirement(), snapshot(600), %{
        "resident" => "engine",
        "resident_protocol" => @protocol
      })

    assert warm["state"] == "admitted"
    assert warm["requirement"]["host_peak"] == 400
    assert warm["requirement"]["resident"]["usage"]["host"] == 600
  end

  test "other work sees only remaining working growth, not the same PID credited twice" do
    {state, _} = resident(Admission.new())

    {state, _} =
      request(state, "warm", requirement(), snapshot(600), %{
        "resident" => "engine",
        "resident_protocol" => @protocol
      })

    usage = %{"warm" => %{"host" => 700, "device" => 0}}

    {state, blocked} =
      request(state, "writer", %{"host_peak" => 350}, snapshot(600), %{"usage" => usage})

    assert blocked["state"] == "waiting"

    {_, small} =
      request(state, "small", %{"host_peak" => 300}, snapshot(600), %{"usage" => usage})

    assert small["state"] == "admitted"
  end

  test "a warm charge never falls below measured incremental working usage" do
    {state, _} = resident(Admission.new())

    {state, _} =
      request(state, "warm", requirement(), snapshot(600), %{
        "resident" => "engine",
        "resident_protocol" => @protocol
      })

    {_, warm} =
      Admission.step(state, %{
        "op" => "poll",
        "id" => "warm",
        "snapshot" => snapshot(300),
        "usage" => %{"warm" => %{"host" => 1_300, "device" => 0}}
      })

    assert warm["requirement"]["host_peak"] == 700
  end

  test "weight credit is limited by measured residency and the lease's weight basis" do
    {state, _} = resident(Admission.new())

    {_, job} =
      request(state, "warm", put_in(requirement()["basis"]["weights"], 900), snapshot(1_000), %{
        "resident" => "engine",
        "resident_protocol" => @protocol
      })

    assert job["requirement"]["resident"]["host_weights"] == 600
  end

  test "fit compares actual memory waiters with projected retained bytes and the user reserve" do
    {state, _} = request(Admission.new(), "active", %{"host_peak" => 1}, snapshot(100))
    {state, _} = request(state, "other", %{"host_peak" => 400}, snapshot(100))
    {_, no} = fit(state, snapshot(1_000, 0, "normal", 100))
    refute no["keep"]
    assert no["figures"]["host_available"] == 1000
    assert no["figures"]["reserve_bytes"] == 100
    assert no["figures"]["host_available_after_active_growth"] == 899
    assert no["figures"]["largest_waiting_host"] == 400
    assert no["figures"]["additional_resident"]["host"] == 600
    assert no["figures"]["host_available_after_resident"] == 299
    {_, yes} = fit(state, snapshot(1_201, 0, "normal", 100))
    assert yes["keep"]
  end

  test "already resident usage is already absent from available memory" do
    {state, _} = resident(Admission.new())
    {state, _} = request(state, "active", %{"host_peak" => 1}, snapshot(100))
    {state, _} = request(state, "other", %{"host_peak" => 400}, snapshot(100))
    {_, yes} = fit(state, snapshot(601, 0, "normal", 100), %{"resident" => "engine"})
    assert yes["keep"]
    assert yes["figures"]["additional_resident"]["host"] == 0
    assert yes["figures"]["host_available_after_resident"] == 500
  end

  test "no hypothetical peer or count gate is added to the fit check" do
    {_, yes} = fit(Admission.new(), snapshot(600))
    assert yes["keep"]
    assert yes["figures"]["largest_waiting_host"] == 0
    assert yes["figures"]["waiting_jobs"] == 0
  end

  test "pressure must be normal and observations known" do
    for pressure <- ["warning", "critical", "unknown", nil] do
      {_, no} = fit(Admission.new(), snapshot(10_000, 0, pressure))
      refute no["keep"]
    end

    {_, no} = fit(Admission.new(), %{"host" => %{"pressure" => "normal"}})
    refute no["keep"]
  end

  test "CUDA residency and reserve are checked independently of host headroom" do
    {state, _} = resident(Admission.new(), 1_000)
    {state, _} = request(state, "active", %{"host_peak" => 1}, snapshot(100))

    {state, _} =
      request(
        state,
        "gpu-waiter",
        %{"host_peak" => 100, "device_peak" => 600, "device" => "gpu"},
        snapshot(100)
      )

    {_, yes} =
      fit(state, snapshot(1_000, 700, "normal", 100), %{
        "requirement" => requirement(1_000, 1_000),
        "resident" => "engine"
      })

    assert yes["keep"]

    assert yes["figures"]["device_reserve_bytes"] == %{"gpu" => 100}
    assert yes["figures"]["device_pressure"] == %{"gpu" => "normal"}

    {_, pressure} =
      fit(
        state,
        put_in(snapshot(1_000, 700, "normal", 100), ["devices", "gpu", "pressure"], "warning"),
        %{
          "requirement" => requirement(1_000, 1_000),
          "resident" => "engine"
        }
      )

    refute pressure["keep"]
    assert pressure["figures"]["device_pressure"] == %{"gpu" => "warning"}

    {_, no} =
      fit(state, snapshot(1_000, 699, "normal", 100), %{
        "requirement" => requirement(1_000, 1_000),
        "resident" => "engine"
      })

    refute no["keep"]

    {_, no} =
      fit(state, %{"host" => %{"available" => 10_000, "pressure" => "normal"}}, %{
        "requirement" => requirement(1_000, 1_000),
        "resident" => "engine"
      })

    refute no["keep"]
  end

  test "Metal uses the one host pool reported by Resources, not a second device charge" do
    estimate =
      Riff.Runtime.Resources.estimate(%{
        "backend" => "metal",
        "model_bytes" => 600,
        "render_mode" => "plan",
        "margin" => 0,
        "context" => 1,
        "config" => %{
          "num_hidden_layers" => 1,
          "num_key_value_heads" => 1,
          "head_dim" => 1,
          "hidden_size" => 1
        }
      })

    assert estimate["device_peak"] == 0
    assert estimate["device"] == nil

    {state, _} =
      Admission.step(Admission.new(), %{
        "op" => "resident",
        "action" => "update",
        "protocol" => @protocol,
        "id" => "engine",
        "requirement" => estimate,
        "usage" => %{"host" => 600, "device" => 0}
      })

    memory = %{
      "host" => %{"available" => estimate["host_peak"] - 600, "pressure" => "normal"},
      "devices" => %{}
    }

    {state, job} =
      request(state, "warm", estimate, memory, %{
        "resident" => "engine",
        "resident_protocol" => @protocol
      })

    assert job["requirement"]["host_peak"] == estimate["host_peak"] - 600
    assert job["requirement"]["device_peak"] == 0

    {_, yes} =
      fit(state, memory, %{"id" => "warm", "requirement" => estimate, "resident" => "engine"})

    assert yes["keep"]
    assert yes["figures"]["device_available_after_resident"] == %{}
  end

  test "release_if_idle checks every scheduler job and is ordered against admission" do
    {state, _} = resident(Admission.new())
    {state, _} = request(state, "writer", %{"host_peak" => 100}, snapshot(1_000))

    release = %{
      "op" => "resident",
      "action" => "release_if_idle",
      "protocol" => @protocol,
      "id" => "engine"
    }

    {state, busy} = Admission.step(state, release)
    assert busy["state"] == "busy"
    assert Map.has_key?(state.residents, "engine")
    {state, _} = Admission.step(state, %{"op" => "release", "id" => "writer"})
    {state, released} = Admission.step(state, release)
    assert released["state"] == "released"

    {_, stale} =
      request(state, "warm", requirement(), snapshot(1_000), %{
        "resident" => "engine",
        "resident_protocol" => @protocol
      })

    assert stale["state"] == "error"
  end

  test "waiting scheduler jobs also prevent release when the generator queue is empty" do
    {state, _} = resident(Admission.new())
    {state, _} = request(state, "writer", %{"host_peak" => 100}, snapshot(1_000, 0, "warning"))

    {_, busy} =
      Admission.step(state, %{
        "op" => "resident",
        "action" => "release_if_idle",
        "protocol" => @protocol,
        "id" => "engine"
      })

    assert busy["state"] == "busy"
    assert busy["waiting"] == ["writer"]
  end

  test "lease versions and identities fail clearly rather than silently losing credit" do
    {state, _} = resident(Admission.new())

    for fields <- [
          %{"resident" => "engine"},
          %{"resident" => "engine", "resident_protocol" => "riff.resident.v2"},
          %{"resident" => "missing", "resident_protocol" => @protocol}
        ] do
      {_, response} = request(state, "warm", requirement(), snapshot(1_000), fields)
      assert response["state"] == "error"
    end

    {_, response} =
      Admission.step(state, %{"op" => "resident", "action" => "release", "id" => "engine"})

    assert response["error"] =~ "protocol"
  end

  test "cancel drops a job binding, then the engine lease can be released idempotently" do
    {state, _} = resident(Admission.new())

    {state, _} =
      request(state, "warm", requirement(), snapshot(1_000), %{
        "resident" => "engine",
        "resident_protocol" => @protocol
      })

    release = %{
      "op" => "resident",
      "action" => "release",
      "protocol" => @protocol,
      "id" => "engine"
    }

    {_, error} = Admission.step(state, release)
    assert error["state"] == "error"
    {state, _} = Admission.step(state, %{"op" => "cancel", "id" => "warm"})
    {state, _} = Admission.step(state, release)
    {state, _} = Admission.step(state, release)
    assert state.residents == %{}
    assert state.bindings == %{}
  end
end
