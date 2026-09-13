defmodule Riff.Installer.OtpRuntime do
  @moduledoc false
  alias Riff.Desktop.Build, as: B

  def capture!(root, version, pin \\ B.components()["otp_runtime"]) do
    directory = "erts-#{version}"

    inputs =
      Path.join(root, directory)
      |> B.native_files()
      |> Enum.map(&record(root, Path.join(directory, &1)))
      |> Enum.sort_by(& &1["path"])

    if inputs == [], do: raise("The selected ERTS runtime has no native files")

    official =
      Enum.filter(pin["native_files"], &String.starts_with?(&1["path"], directory <> "/"))

    official =
      Enum.map(official, &Map.take(&1, ["path", "bytes", "sha256"])) |> Enum.sort_by(& &1["path"])

    %{
      "format_version" => 1,
      "erts_version" => version,
      "input_files" => inputs,
      "upstream_component" => if(inputs == official, do: Map.delete(pin, "native_files"))
    }
  end

  # Mix.copy_erts intentionally retains existing same-version files. A release
  # assembled into a fresh package directory must match the selected toolchain.
  def verify_copied!(runtime, receipt) do
    for item <- receipt["input_files"] do
      B.verify!(Path.join(runtime, item["path"]), item["bytes"], item["sha256"])
    end

    :ok
  end

  def preserve_upstream!(runtime, receipt, pin \\ B.components()["otp_runtime"]) do
    if receipt["upstream_component"] do
      for item <- pin["native_files"],
          path = Path.join(runtime, item["path"]),
          File.regular?(path),
          File.stat!(path).size == item["bytes"],
          B.hash(path) == item["sha256"] do
        B.run!("/usr/bin/codesign", ["--verify", "--strict", path])
        path
      end
      |> MapSet.new()
    else
      MapSet.new()
    end
  end

  def finish!(runtime, receipt) do
    Map.put(
      receipt,
      "packaged_files",
      Enum.map(receipt["input_files"], &record(runtime, &1["path"]))
    )
  end

  defp record(root, relative) do
    path = Path.join(root, relative)
    unless File.lstat!(path).type == :regular, do: raise("ERTS input must be a regular file")
    %{"path" => relative, "bytes" => File.stat!(path).size, "sha256" => B.hash(path)}
  end
end
