ExUnit.start()
Code.require_file("../lib/riff/runtime/resources.ex", __DIR__)

defmodule Riff.Runtime.ResourcesTest do
  use ExUnit.Case, async: true
  alias Riff.Runtime.Resources

  defp acoustic(frames \\ 200) do
    %{
      "kind" => "acoustic_decode",
      "backend" => "metal",
      "vae_bytes" => 265_218_656,
      "vae_config" => %{"dtype" => "float32"},
      "acoustic" => %{
        "frames" => frames,
        "latent_dim" => 64,
        "encoder_latent_dim" => 128,
        "channels" => 2,
        "downsampling_ratio" => 1920,
        "decode_core_frames" => 1024,
        "decode_halo_frames" => 16,
        "vae_storage" => 0,
        "hashes" => %{
          "decoder" => "d4f4a05d8f291ae820cd1e43609da3fa91b56465810091a2b08c3350b751719d"
        }
      }
    }
  end

  test "longer scores, longer music and CFG reserve the later growth" do
    inputs = %{
      "kind" => "native",
      "model_bytes" => 2_600_000_000,
      "vae_bytes" => 260_000_000,
      "seconds" => 30,
      "text_bytes" => 1000,
      "backend" => "metal",
      "vae_chunk" => 1024
    }

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
    input = %{
      "kind" => "writer",
      "model_bytes" => 300_000_000,
      "vae_bytes" => 260_000_000,
      "text_bytes" => 5000,
      "writer_tokens" => 768,
      "config" => %{"max_position_embeddings" => 4096}
    }

    writer = Resources.estimate(input)
    assert writer["basis"]["tokens"] == 4096
    assert writer["basis"]["weights"] == 300_000_000

    plan =
      Resources.estimate(
        Map.merge(input, %{"kind" => "native", "render_mode" => "plan", "planning" => true})
      )

    assert plan["basis"]["weights"] == 300_000_000

    assert Resources.estimate(Map.put(input, "render_mode", "plan"))["host_peak"] ==
             writer["host_peak"]
  end

  test "planning-only ignores music duration and honors effective planning context" do
    input = %{
      "kind" => "native",
      "render_mode" => "plan",
      "planning" => true,
      "planning_tokens" => 12_000,
      "text_bytes" => 100,
      "seconds" => 200,
      "model_bytes" => 300_000_000
    }

    plan = Resources.estimate(input)
    assert plan["basis"]["tokens"] == 12_100
    assert plan == Resources.estimate(Map.put(input, "seconds", 1))
    assert Resources.estimate(Map.put(input, "context", 4096))["basis"]["tokens"] == 4096
  end

  test "verified acoustic replay needs no main model and accounts for the observed short decode" do
    result = Resources.estimate(acoustic())
    basis = result["basis"]
    decoder = basis["decoder"]

    # Independent GGUF inventory: 132,482,816 convolution elements and 98,432
    # scalar-vector elements. All source tensors are F16, while native loading
    # expands the vectors to F32 and retains both halves of the VAE.
    assert basis["weights"] == 265_359_360
    assert decoder["full_latents"] == 51_200
    assert decoder["full_audio"] == 3_071_488
    assert decoder["tile_frames"] == 200
    assert decoder["graph_instances"] == 1
    assert decoder["known_f16_source"]
    assert result["host_peak"] > 337_216_568
    assert result["device_peak"] == 0
    assert basis["kv"] == 0
    assert basis["nar_scratch"] == 0
    assert basis["tokens"] == 0
    assert decoder["measurement"] == "unverified"
  end

  test "historical synthesis and unrelated main model inputs cannot inflate a decoder reservation" do
    input = acoustic()

    irrelevant = %{
      "model_bytes" => 100_000_000_000,
      "config" => %{"num_hidden_layers" => 1000, "max_position_embeddings" => 1_000_000},
      "context" => 1_000_000,
      "text_bytes" => 1_000_000,
      "prefix_tokens" => 1_000_000,
      "planning" => true,
      "planning_tokens" => 1_000_000,
      "cfg_scale" => 2.0,
      "seconds" => 100_000,
      "render_mode" => "plan"
    }

    altered = Map.merge(input, irrelevant)

    altered =
      update_in(altered["acoustic"], fn metadata ->
        Map.merge(metadata, %{"context" => 1_000_000, "steps" => 1000, "guidance" => 2.0})
      end)

    assert Resources.estimate(input) == Resources.estimate(altered)
  end

  test "full latent and float audio storage grows after tile work has reached its bound" do
    shorter = Resources.estimate(acoustic(4096))
    longer = Resources.estimate(acoustic(8192))
    short = shorter["basis"]["decoder"]
    long = longer["basis"]["decoder"]

    assert long["tile_frames"] == short["tile_frames"]
    assert long["graph_peak"] == short["graph_peak"]
    assert long["tile_buffers"] == short["tile_buffers"]
    assert long["full_latents"] == short["full_latents"] * 2
    assert long["full_audio"] - short["full_audio"] == 4096 * 1920 * 2 * 4
    assert longer["host_peak"] > shorter["host_peak"]

    assert Resources.estimate(acoustic(100))["host_peak"] <
             Resources.estimate(acoustic(200))["host_peak"]
  end

  test "core and halo controls change actual tile work without changing the saved performance" do
    input = acoustic(4096)

    small =
      Resources.estimate(Map.put(input, "decoder", %{"core_frames" => 64, "halo_frames" => 0}))

    core =
      Resources.estimate(Map.put(input, "decoder", %{"core_frames" => 128, "halo_frames" => 0}))

    halo =
      Resources.estimate(Map.put(input, "decoder", %{"core_frames" => 128, "halo_frames" => 16}))

    assert small["host_peak"] < core["host_peak"]
    assert core["host_peak"] < halo["host_peak"]
    assert small["basis"]["decoder"]["full_audio"] == halo["basis"]["decoder"]["full_audio"]
    assert small["basis"]["decoder"]["graph_instances"] == 1
    assert halo["basis"]["decoder"]["graph_instances"] == 2
    assert halo["basis"]["decoder"]["tile_frames"] == 160
  end

  test "precision expansion follows the native store, including quantized kernel fallback" do
    input = acoustic()
    native = Resources.estimate(input)
    f16 = Resources.estimate(Map.put(input, "decoder", %{"storage" => 2}))
    f32 = Resources.estimate(Map.put(input, "decoder", %{"storage" => 1}))

    assert f16["host_peak"] == native["host_peak"]
    assert f32["basis"]["weights"] == 530_324_992
    assert f32["host_peak"] > f16["host_peak"]

    for storage <- [4, 5, 6] do
      quantized = Resources.estimate(Map.put(input, "decoder", %{"storage" => storage}))
      assert quantized["host_peak"] == f32["host_peak"]
      assert quantized["basis"]["decoder"]["convolution_element_bytes"] == 4
    end

    unknown = put_in(input, ["acoustic", "hashes", "decoder"], String.duplicate("a", 64))
    unknown = Resources.estimate(unknown)
    refute unknown["basis"]["decoder"]["known_f16_source"]
    assert unknown["basis"]["weights"] >= f32["basis"]["weights"]
    assert unknown["host_peak"] >= f32["host_peak"]
  end

  test "explicit native storage and zero halo override captured nonzero controls" do
    input = put_in(acoustic(), ["acoustic", "vae_storage"], 1)
    input = Map.put(input, "decoder", %{"storage" => 0, "core_frames" => 32, "halo_frames" => 0})
    decoder = Resources.estimate(input)["basis"]["decoder"]
    assert decoder["storage"] == 0
    assert decoder["convolution_element_bytes"] == 2
    assert decoder["core_frames"] == 32
    assert decoder["halo_frames"] == 0
    assert decoder["tile_frames"] == 32
  end

  test "constant-size tile boundary evaluation matches complete native tile schedules" do
    for frames <- [3, 15, 16, 17, 64], core <- [3, 8, 16], halo <- [0, 1, 7, 20, 128] do
      input =
        Map.put(acoustic(frames), "decoder", %{"core_frames" => core, "halo_frames" => halo})

      actual = Resources.estimate(input)["basis"]["decoder"]

      # Enumerate the native caller's complete sequence here, independently of
      # the estimator's constant-size breakpoint selection.
      sizes =
        Stream.iterate(0, &(&1 + core))
        |> Enum.take_while(&(&1 < frames))
        |> Enum.map(fn start ->
          stop = min(start + core, frames)
          right = min(stop + halo, frames)
          left = max(0, start - halo)
          right - left
        end)

      graphs =
        Map.new(Enum.uniq(sizes), fn size ->
          single = Map.put(acoustic(size), "decoder", %{"core_frames" => size})
          {size, Resources.estimate(single)["basis"]["decoder"]["graph_peak"]}
        end)

      expected =
        [nil | sizes]
        |> Enum.chunk_every(2, 1, :discard)
        |> Enum.map(fn [previous, current] ->
          if previous == nil or previous == current,
            do: graphs[current],
            else: graphs[previous] + graphs[current]
        end)
        |> Enum.max()

      assert actual["tile_frames"] == Enum.max(sizes)
      assert actual["graph_peak"] == expected
    end
  end

  test "CUDA keeps an explicit unmeasured device reservation and margin never removes work" do
    input =
      Map.merge(acoustic(), %{"backend" => "cuda", "device" => "GPU-fixture", "margin" => 0})

    raw = Resources.estimate(input)
    padded = Resources.estimate(Map.put(input, "margin", 0.2))
    assert raw["device"] == "GPU-fixture"
    assert raw["device_peak"] == raw["host_peak"]
    assert padded["host_peak"] == ceil(raw["host_peak"] * 1.2)
    assert raw["basis"]["decoder"]["measurement"] == "unverified"
  end

  test "missing or invalid operation metadata cannot silently reserve only a default small decoder" do
    assert_raise ArgumentError, fn -> Resources.estimate(%{"kind" => "acoustic_decode"}) end

    for {key, value} <- [{"storage", 7}, {"core_frames", 0}, {"halo_frames", -1}] do
      assert_raise ArgumentError, fn ->
        Resources.estimate(Map.put(acoustic(), "decoder", %{key => value}))
      end
    end
  end
end
