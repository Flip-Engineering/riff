defmodule Riff.Installer.TransactionTest do
  use ExUnit.Case, async: false
  alias Riff.Installer.{Download, Payload, Transaction}

  setup do
    root =
      Path.join(
        System.tmp_dir!(),
        "riff-transaction-" <> Base.url_encode64(:crypto.strong_rand_bytes(12), padding: false)
      )

    File.mkdir_p!(root)
    on_exit(fn -> File.rm_rf!(root) end)

    originals = %{
      "current.json" => ~s({"version":"0.4.0"}),
      "runtime.json" => "old runtime",
      "launcher.py" => "old launcher"
    }

    for {name, contents} <- originals, do: File.write!(Path.join(root, name), contents)
    %{root: root, originals: originals}
  end

  test "a crash after any metadata write restores every previous byte before the gate opens", %{
    root: root,
    originals: originals
  } do
    # Exercise each interruption boundary, including current.json already new
    # while the durable transaction has not yet reached committed.
    for changed <- [
          ["runtime.json"],
          ["runtime.json", "launcher.py"],
          ["runtime.json", "launcher.py", "current.json"]
        ] do
      Transaction.begin!(root, %{version: "0.5.0"})
      for name <- changed, do: File.write!(Path.join(root, name), "new " <> name)
      assert File.exists?(Path.join(root, ".installer-activation.json"))
      Transaction.recover!(root)
      for {name, contents} <- originals, do: assert(File.read!(Path.join(root, name)) == contents)
      refute File.exists?(Path.join(root, ".installer-activation.json"))
    end
  end

  test "a durable committed journal keeps the new version even if gate removal was interrupted",
       %{root: root} do
    Transaction.begin!(root, %{version: "0.5.0"})
    File.write!(Path.join(root, "current.json"), "selected new version")
    gate_path = Path.join(root, ".installer-activation.json")

    gate =
      gate_path
      |> File.read!()
      |> Jason.decode!()
      |> put_in(["transaction", "phase"], "committed")

    Payload.write_json(gate_path, gate)
    Transaction.recover!(root)
    assert File.read!(Path.join(root, "current.json")) == "selected new version"
    refute File.exists?(gate_path)
  end

  test "fresh-install recovery removes only newly created metadata", %{root: root} do
    for name <- ["runtime.json", "current.json", "launcher.py"],
        do: File.rm!(Path.join(root, name))

    File.write!(Path.join(root, "my-recording.wav"), "original")
    Transaction.begin!(root, %{version: "0.5.0"})
    File.write!(Path.join(root, "runtime.json"), "new runtime")
    Transaction.recover!(root)
    refute File.exists?(Path.join(root, "runtime.json"))
    assert File.read!(Path.join(root, "my-recording.wav")) == "original"
  end

  test "backup paths cannot redirect recovery outside its own journal", %{root: root} do
    Transaction.begin!(root, %{version: "0.5.0"})
    gate_path = Path.join(root, ".installer-activation.json")

    gate =
      gate_path
      |> File.read!()
      |> Jason.decode!()
      |> put_in(["transaction", "backups", "runtime.json"], "../outside")

    Payload.write_json(gate_path, gate)

    assert_raise Download.Error, ~r/outside its recovery journal/, fn ->
      Transaction.recover!(root)
    end

    assert File.exists?(gate_path)
  end
end
