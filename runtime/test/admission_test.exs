ExUnit.start()
Code.require_file("../lib/riff/runtime/admission.ex", __DIR__)

defmodule Riff.Runtime.AdmissionTest do
  use ExUnit.Case, async: true
  alias Riff.Runtime.Admission

  defp snapshot(host, gpu \\ 0, pressure \\ "normal"), do: %{"host" => %{"available" => host, "pressure" => pressure}, "devices" => %{"cuda:0" => %{"available" => gpu}}}
  defp request(state, id, host, memory, options \\ %{}) do
    Admission.step(state, %{"op" => "request", "id" => id, "requirement" => Map.merge(%{"host_peak" => host}, options), "snapshot" => memory})
  end

  test "music and writer overlap when measured headroom accommodates both peaks" do
    {state, first} = request(Admission.new(), "music", 4_000, snapshot(7_000))
    assert first["state"] == "admitted"
    {state, writer} = request(state, "writer", 1_000, snapshot(7_000))
    assert writer["state"] == "admitted"
    {_, another} = request(state, "another-music", 4_000, snapshot(7_000))
    assert another["state"] == "waiting"
  end

  test "an active model's later stage growth stays reserved without double counting current usage" do
    {state, _} = request(Admission.new(), "music", 4_000, snapshot(5_000))
    {state, response} = Admission.step(state, %{"op" => "request", "id" => "writer", "requirement" => %{"host_peak" => 1_000}, "snapshot" => snapshot(2_500), "usage" => %{"music" => %{"host" => 1_000}}})
    assert response["state"] == "waiting"
    {_, response} = Admission.step(state, %{"op" => "poll", "id" => "writer", "snapshot" => snapshot(2_500), "usage" => %{"music" => %{"host" => 3_000}}})
    assert response["state"] == "admitted"
  end

  test "CUDA memory and host memory are independent and device identity is respected" do
    {state, _} = request(Admission.new(), "existing-cpu", 100, snapshot(8_000, 2_000))
    {state, blocked} = request(state, "gpu", 500, snapshot(8_000, 2_000), %{"device" => "cuda:0", "device_peak" => 3_000})
    assert blocked["reason"] == "Waiting for graphics memory"
    {state, cpu} = request(state, "cpu", 1_000, snapshot(8_000, 2_000))
    assert cpu["state"] == "admitted"
    {_, gpu} = Admission.step(state, %{"op" => "poll", "id" => "gpu", "snapshot" => snapshot(8_000, 4_000)})
    assert gpu["state"] == "admitted"
  end

  test "pressure queues new work, then fresh observations admit it without a manual retry" do
    {state, response} = request(Admission.new(), "writer", 1_000, snapshot(9_000, 0, "warning"))
    assert response["state"] == "waiting"
    {_, response} = Admission.step(state, %{"op" => "poll", "id" => "writer", "snapshot" => snapshot(9_000)})
    assert response["state"] == "admitted"
  end

  test "cancel removes only its waiting or running reservation and repeated release is safe" do
    {state, _} = request(Admission.new(), "music", 4_000, snapshot(5_000))
    {state, _} = request(state, "writer", 2_000, snapshot(5_000))
    {state, cancelled} = Admission.step(state, %{"op" => "cancel", "id" => "writer"})
    assert cancelled["state"] == "cancelled"
    assert Map.has_key?(state.active, "music")
    assert state.waiting == []
    {state, _} = Admission.step(state, %{"op" => "release", "id" => "music"})
    {state, _} = Admission.step(state, %{"op" => "release", "id" => "music"})
    assert state.active == %{}
  end

  test "the resource budget admits any number of independently fitting jobs" do
    state = Enum.reduce(1..20, Admission.new(), fn id, state ->
      {state, response} = request(state, "job-#{id}", 100, snapshot(2_000))
      assert response["state"] == "admitted"
      state
    end)
    assert map_size(state.active) == 20
    {_, response} = request(state, "overflow", 1, snapshot(2_000))
    assert response["state"] == "waiting"
  end

  test "an uncertain large score can run alone without weakening peer admission" do
    {state, score} = request(Admission.new(), "score", 14_149_896_384, snapshot(10_479_720_202))
    assert score["state"] == "admitted"
    assert score["requirement"]["admission"] == "solo"
    assert score["requirement"]["uncertainty"] == "Waiting for memory"
    {state, writer} = request(state, "writer", 1_000, snapshot(16_000_000_000))
    assert writer["state"] == "waiting"
    assert writer["reason"] == "Waiting for the current model"
    {state, _} = Admission.step(state, %{"op" => "release", "id" => "score"})
    {_, writer} = Admission.step(state, %{"op" => "poll", "id" => "writer", "snapshot" => snapshot(16_000_000_000)})
    assert writer["state"] == "admitted"
  end

  test "solo fallback never bypasses pressure or missing host observations" do
    for pressure <- ["warning", "critical", "unknown"] do
      {_, response} = request(Admission.new(), "score", 14_000, snapshot(10_000, 0, pressure))
      assert response["state"] == "waiting"
    end
    {_, response} = request(Admission.new(), "score", 14_000, %{"host" => %{"pressure" => "normal"}})
    assert response["state"] == "waiting"
  end
end
