defmodule Riff.Runtime.ScoreArtifactTest do
  use ExUnit.Case, async: false
  alias Riff.Runtime.ScoreArtifact, as: Score

  @contract %{
    "format" => "riff.yue2.score-tokens.v1",
    "tokenizer_sha256" => String.duplicate("a", 64),
    "prefix_contract" => "riff.yue2.prefix.v1"
  }

  setup do
    # Keep this owned fixture under the canonical checkout; macOS /tmp and
    # /var aliases are deliberately not artifact-library descendants.
    root =
      Path.expand("../_build/score-artifact-test-#{System.unique_integer([:positive])}", __DIR__)

    File.mkdir_p!(root)
    on_exit(fn -> File.rm_rf!(root) end)
    %{root: root}
  end

  test "an empty score is present and a truncated score remains usable" do
    assert Score.parse!(~s({"tokens":[]})) == %{"token_count" => 0, "truncated" => false}

    assert Score.parse!(~s({"tokens":[-0,151642],"truncated":true})) == %{
             "token_count" => 2,
             "truncated" => true
           }
  end

  test "numeric JSON spellings and actual vocabulary bounds are enforced" do
    for value <- [
          "true",
          "0.0",
          "0e0",
          "-1",
          "151643",
          "9999999999999999999999999999999",
          "0.00000000000000000000000001"
        ] do
      assert_raise Score.Error, fn -> Score.parse!(~s({"tokens":[#{value}]})) end
    end

    for bytes <- [
          "",
          "[]",
          "{}",
          ~s({"tokens":false}),
          ~s({"tokens":[),
          ~s({"tokens":[],"truncated":0})
        ] do
      assert_raise Score.Error, fn -> Score.parse!(bytes) end
    end
  end

  test "duplicate decoded keys are rejected; metadata keys retain their full lengths" do
    for bytes <- [
          ~s({"tokens":[],"tokens":[1]}),
          ~S({"tokens":[],"to\u006bens":[1]}),
          ~s({"tokens":[],"extra":1,"extra":2})
        ] do
      assert_raise Score.Error, fn -> Score.parse!(bytes) end
    end

    assert_raise Score.Error, fn -> Score.parse!(~S({"tokens\u0000suffix":[]})) end

    assert Score.parse!(~S({"tokens":[],"tokens\u0000suffix":[-1],"truncated\u0000suffix":2})) ==
             %{"token_count" => 0, "truncated" => false}

    assert Score.parse!(~S({"tokens":[],"meta\u0000a":1,"meta\u0000b":2}))["token_count"] == 0
  end

  test "capture preserves exact source bytes and binds provenance without changing inputs", %{
    root: root
  } do
    bytes = " { \"tokens\" : [0, 22, 151642], \"truncated\": true, \"annotation\": \"한글\" }\n"

    origin = %{
      "kind" => "native_capture",
      "job_id" => "original",
      "cot" => "full",
      "model_sha256" => "old-model"
    }

    result = Score.capture!(root, bytes, @contract, origin)
    assert File.read!(result["path"]) == bytes
    assert result["descriptor"]["provenance"] == origin
    assert result["descriptor"]["score"]["token_count"] == 3
    assert Score.resolve!(root, result["id"], @contract) == result
    assert Score.capture!(root, bytes, @contract, origin) == result
    assert length(File.ls!(root)) == 1
  end

  test "new model or musical inputs do not become a tokenizer compatibility restriction", %{
    root: root
  } do
    result =
      Score.capture!(root, ~s({"tokens":[]}), @contract, %{"model" => "previous", "cot" => "full"})

    # Only tokenizer/format compatibility is supplied to resolution. The
    # caller may choose new weights, lyrics, guidance or a planning instruction.
    assert Score.resolve!(root, result["id"], @contract)["descriptor"]["score"]["token_count"] ==
             0

    incompatible = Map.put(@contract, "tokenizer_sha256", String.duplicate("b", 64))

    assert_raise Score.Error, ~r/different tokenizer/, fn ->
      Score.resolve!(root, result["id"], incompatible)
    end
  end

  test "changed raw bytes never get repaired or overwritten by another capture", %{root: root} do
    bytes = ~s({"tokens":[1]})
    result = Score.capture!(root, bytes, @contract, %{})
    File.write!(result["path"], ~s({"tokens":[2]}))
    assert_raise Score.Error, fn -> Score.resolve!(root, result["id"], @contract) end
    assert_raise Score.Error, fn -> Score.capture!(root, bytes, @contract, %{}) end
    assert File.read!(result["path"]) == ~s({"tokens":[2]})
  end

  test "descriptor identity is checked before interpreting its provenance", %{root: root} do
    result = Score.capture!(root, ~s({"tokens":[1]}), @contract, %{})
    path = Path.join(Path.dirname(result["path"]), "descriptor.json")
    File.write!(path, "{}")

    assert_raise Score.Error, ~r/descriptor has changed/, fn ->
      Score.resolve!(root, result["id"], @contract)
    end
  end

  test "job input is a separate preserved copy and never overwrites an existing file", %{
    root: root
  } do
    result = Score.capture!(root, ~s({"tokens":[],"truncated":true}), @contract, %{})
    inputs = Path.join(root, "job-inputs")
    File.mkdir!(inputs)
    prepared = Score.prepare!(root, result["id"], @contract, inputs)
    assert File.read!(prepared["input_path"]) == File.read!(result["path"])
    refute File.stat!(prepared["input_path"]).inode == File.stat!(result["path"]).inode
    original = File.read!(result["path"])
    assert Score.prepare!(root, result["id"], @contract, inputs) == prepared
    File.write!(prepared["input_path"], "changed by this job")
    assert_raise Score.Error, fn -> Score.prepare!(root, result["id"], @contract, inputs) end
    assert File.read!(prepared["input_path"]) == "changed by this job"
    assert File.read!(result["path"]) == original
  end

  test "artifact and input symlinks cannot redirect publication or reads", %{root: root} do
    result = Score.capture!(root, ~s({"tokens":[1]}), @contract, %{})
    inputs = Path.join(root, "linked-inputs")
    File.ln_s!(Path.dirname(result["path"]), inputs)
    assert_raise Score.Error, fn -> Score.prepare!(root, result["id"], @contract, inputs) end
    File.rename!(result["path"], result["path"] <> ".original")
    File.ln_s!(result["path"] <> ".original", result["path"])
    assert_raise Score.Error, fn -> Score.resolve!(root, result["id"], @contract) end
    assert File.read!(result["path"] <> ".original") == ~s({"tokens":[1]})
  end

  test "a hard-linked existing job target cannot be overwritten", %{root: root} do
    result = Score.capture!(root, ~s({"tokens":[1]}), @contract, %{})
    inputs = Path.join(root, "inputs")
    File.mkdir!(inputs)
    File.ln!(result["path"], Path.join(inputs, "score.json"))

    assert_raise Score.Error, ~r/aliases/, fn ->
      Score.prepare!(root, result["id"], @contract, inputs)
    end

    assert File.read!(result["path"]) == ~s({"tokens":[1]})
  end

  test "concurrent captures agree on one complete immutable reference", %{root: root} do
    results =
      1..4
      |> Task.async_stream(
        fn _ ->
          Score.capture!(root, ~s({"tokens":[22,17],"truncated":true}), @contract, %{
            "job" => "same"
          })
        end,
        max_concurrency: 4
      )
      |> Enum.map(fn {:ok, result} -> result end)

    assert length(Enum.uniq(results)) == 1
    [result] = Enum.uniq(results)
    assert Score.resolve!(root, result["id"], @contract) == result
  end

  test "unpublished staging contents are preserved and never resolved as references", %{
    root: root
  } do
    staging = Path.join(root, ".staging-interrupted")
    File.mkdir!(staging)
    File.write!(Path.join(staging, "score.json"), ~s({"tokens":[))
    result = Score.capture!(root, ~s({"tokens":[]}), @contract, %{})

    assert Score.resolve!(root, result["id"], @contract)["descriptor"]["score"]["token_count"] ==
             0

    assert_raise Score.Error, fn ->
      Score.resolve!(root, "riff-score-v1:../.staging-interrupted", @contract)
    end

    assert File.read!(Path.join(staging, "score.json")) == ~s({"tokens":[)
  end

  test "file capture reads only owned regular descendants and preserves the original", %{
    root: root
  } do
    output = Path.join(root, "job")
    File.mkdir!(output)
    source = Path.join(output, "result.plan.json")
    bytes = ~s({"tokens":[],"truncated":true})
    File.write!(source, bytes)
    artifact = Score.capture_file!(root, output, source, @contract, %{"job" => "owned"})
    assert File.read!(artifact["path"]) == bytes
    assert File.read!(source) == bytes

    assert_raise Score.Error, fn ->
      Score.capture_file!(root, output, artifact["path"], @contract, %{})
    end

    assert_raise Score.Error, fn ->
      Score.capture_file!(root, output, output <> "/../job/result.plan.json", @contract, %{})
    end

    linked = Path.join(output, "linked.plan.json")
    File.ln_s!(source, linked)
    assert_raise Score.Error, fn -> Score.capture_file!(root, output, linked, @contract, %{}) end
    assert File.read!(source) == bytes
  end

  test "retry completes the rename and input-write durability boundaries", %{root: root} do
    bytes = ~s({"tokens":[22]})
    artifact = Score.capture!(root, bytes, @contract, %{})

    # The existing directory is the state visible after rename and before the
    # parent sync. Verify the successful retry includes the native sync call.
    capture_calls =
      sync_calls(fn -> assert Score.capture!(root, bytes, @contract, %{}) == artifact end)

    assert {Riff.Installer.Lock, :sync_directory!, [root]} in capture_calls

    inputs = Path.join(root, "interrupted-input")
    File.mkdir!(inputs)
    # Model a complete visible write whose originating call ended before fsync.
    File.write!(Path.join(inputs, "score.json"), bytes)
    prepare_calls = sync_calls(fn -> Score.prepare!(root, artifact["id"], @contract, inputs) end)
    assert Enum.any?(prepare_calls, fn {module, name, _} -> module == :file and name == :sync end)
    assert {Riff.Installer.Lock, :sync_directory!, [inputs]} in prepare_calls
    assert File.read!(Path.join(inputs, "score.json")) == bytes
    assert File.read!(artifact["path"]) == bytes
  end

  defp sync_calls(operation) do
    tracer = spawn(fn -> collect_sync_calls([]) end)
    :erlang.trace_pattern({:file, :sync, 1}, true, [])
    :erlang.trace_pattern({Riff.Installer.Lock, :sync_directory!, 1}, true, [])
    :erlang.trace(self(), true, [:call, {:tracer, tracer}])

    try do
      operation.()
      :erlang.trace(self(), false, [:call])
      barrier = :erlang.trace_delivered(self())
      assert_receive {:trace_delivered, _, ^barrier}
      send(tracer, {:finish, self()})
      assert_receive {:sync_calls, calls}
      calls
    after
      :erlang.trace(self(), false, [:call])
      :erlang.trace_pattern({:file, :sync, 1}, false, [])
      :erlang.trace_pattern({Riff.Installer.Lock, :sync_directory!, 1}, false, [])
      if Process.alive?(tracer), do: Process.exit(tracer, :kill)
    end
  end

  defp collect_sync_calls(calls) do
    receive do
      {:trace, _, :call, call} -> collect_sync_calls([call | calls])
      {:finish, observer} -> send(observer, {:sync_calls, Enum.reverse(calls)})
    end
  end
end
