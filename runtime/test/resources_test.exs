ExUnit.start()
Code.require_file("../lib/riff/runtime/resources.ex", __DIR__)

defmodule Riff.Runtime.ResourcesTest do
  use ExUnit.Case, async: true
  alias Riff.Runtime.Resources

  test "longer scores, longer music and CFG reserve the later growth" do
    inputs = %{"kind" => "native", "model_bytes" => 2_600_000_000, "vae_bytes" => 260_000_000, "seconds" => 30, "text_bytes" => 1000, "backend" => "metal", "vae_chunk" => 1024}
    short = Resources.estimate(inputs)
    long = Resources.estimate(Map.put(inputs, "seconds", 200))
    score = Resources.estimate(Map.put(inputs, "text_bytes", 10_000))
    cfg = Resources.estimate(Map.put(inputs, "cfg_scale", 1.5))
    assert long["host_peak"] > short["host_peak"]
    assert score["host_peak"] > short["host_peak"]
    assert cfg["basis"]["kv"] == short["basis"]["kv"] * 2
    assert short["device_peak"] == 0
    cuda = Resources.estimate(Map.merge(inputs, %{"backend" => "cuda", "device" => "GPU-test"}))
    assert cuda["device_peak"] > 0
    assert cuda["device"] == "GPU-test"
    cpu = Resources.estimate(Map.put(inputs, "backend", "cpu"))
    assert cpu["basis"]["kv"] == short["basis"]["kv"] * 2
  end

  test "writer and planning reservations use their model context and exclude the decoder" do
    input = %{"kind" => "writer", "model_bytes" => 300_000_000, "vae_bytes" => 260_000_000,
              "text_bytes" => 5000, "writer_tokens" => 768, "config" => %{"max_position_embeddings" => 4096}}
    writer = Resources.estimate(input)
    assert writer["basis"]["tokens"] == 4096
    assert writer["basis"]["weights"] == 300_000_000
    plan = Resources.estimate(Map.merge(input, %{"kind" => "native", "render_mode" => "plan", "planning" => true}))
    assert plan["basis"]["weights"] == 300_000_000
    assert Resources.estimate(Map.put(input, "render_mode", "plan"))["host_peak"] == writer["host_peak"]
  end

  test "planning-only ignores music duration and honors effective planning context" do
    input = %{"kind" => "native", "render_mode" => "plan", "planning" => true, "planning_tokens" => 12_000,
              "text_bytes" => 100, "seconds" => 200, "model_bytes" => 300_000_000}
    plan = Resources.estimate(input)
    assert plan["basis"]["tokens"] == 12_100
    assert plan == Resources.estimate(Map.put(input, "seconds", 1))
    assert Resources.estimate(Map.put(input, "context", 4096))["basis"]["tokens"] == 4096
  end
end
