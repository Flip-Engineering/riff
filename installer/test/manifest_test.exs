defmodule Riff.Installer.ManifestTest do
  use ExUnit.Case, async: true
  alias Riff.Installer.Manifest

  test "release pins include every required model and sidecar from the application sources" do
    sources = File.read!(Path.expand("../../sources.json", __DIR__)) |> Jason.decode!()
    assert Manifest.pinned() == sources["model"]
    assets = Manifest.assets()
    assert length(assets) == map_size(sources["model"]["files"])
    assert Enum.all?(assets, &String.contains?(&1.url, sources["model"]["revision"]))
  end

  test "invalid pins and paths cannot become downloads" do
    manifest = Manifest.pinned()
    assert_raise ArgumentError, fn -> Manifest.assets(%{manifest | "revision" => "main"}) end

    for path <- [
          "/absolute",
          "../relative",
          "sidecars/../../escape",
          "sidecars\\escape",
          "sidecars//empty"
        ] do
      assert_raise ArgumentError, fn -> Manifest.validate_path!(path) end
    end
  end
end
