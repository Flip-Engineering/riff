Code.require_file("../scripts/native_capabilities.exs", __DIR__)
ExUnit.start()

defmodule Riff.Native.CapabilitiesTest do
  use ExUnit.Case, async: true
  alias Riff.Native.Capabilities, as: C

  @expected %{
    "feature.yue2.score_tokens" => "1",
    "format.yue2.score_tokens" => "riff.yue2.score-tokens.v1",
    "format.yue2.prefix" => "riff.yue2.prefix.v1"
  }
  @valid Enum.map_join(@expected, "\n", fn {key, value} -> "#{key}=#{value}" end)

  test "accepts model-owned metadata alongside ordinary and future help fields" do
    assert C.parse!("family=yue2\nfeature.future=1\n" <> @valid <> "\n", @expected) == @expected
  end

  test "rejects an old engine, a renamed key, and an incompatible format" do
    for text <- [
          "family=yue2\n",
          String.replace(@valid, "feature.yue2.score_tokens", "feature.yue2.score_tokens_extra"),
          String.replace(@valid, "riff.yue2.prefix.v1", "riff.yue2.prefix.v2")
        ] do
      assert_raise RuntimeError, ~r/metadata/, fn -> C.parse!(text, @expected) end
    end
  end

  test "rejects duplicate or conflicting capability lines" do
    for value <- ["1", "0"] do
      assert_raise RuntimeError, ~r/duplicated/, fn ->
        C.parse!(@valid <> "\nfeature.yue2.score_tokens=#{value}\n", @expected)
      end
    end
  end

  test "source manifest declares the exact supported score and prefix contracts" do
    manifest = Path.join(__DIR__, "../sources.json") |> File.read!() |> :json.decode()
    assert Map.take(manifest["native_capabilities"], Map.keys(@expected)) == @expected
  end
end
