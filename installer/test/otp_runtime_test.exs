Code.require_file("../../desktop/support.exs", __DIR__)
Code.require_file("../scripts/otp_runtime.exs", __DIR__)

defmodule Riff.Installer.OtpRuntimeTest do
  use ExUnit.Case, async: true
  alias Riff.Desktop.Build, as: B
  alias Riff.Installer.OtpRuntime

  setup do
    root = Path.join(System.tmp_dir!(), "riff-erts-input-#{System.unique_integer([:positive])}")
    source = Path.join(root, "selected")
    runtime = Path.join(root, "runtime")
    relative = "erts-16.0.2/bin/beam.smp"
    File.mkdir_p!(Path.dirname(Path.join(source, relative)))
    File.write!(Path.join(source, relative), <<0xCF, 0xFA, 0xED, 0xFE>> <> "selected runtime")
    File.cp_r!(source, runtime)
    on_exit(fn -> File.rm_rf!(root) end)
    item = %{"path" => relative, "bytes" => 20, "sha256" => B.hash(Path.join(source, relative))}

    %{
      root: root,
      source: source,
      runtime: runtime,
      relative: relative,
      pin: %{"native_files" => [item], "version" => "28.0.2"}
    }
  end

  test "fresh release records and verifies selected ERTS bytes", ctx do
    receipt = OtpRuntime.capture!(ctx.source, "16.0.2", ctx.pin)
    assert :ok == OtpRuntime.verify_copied!(ctx.runtime, receipt)
    assert receipt["upstream_component"] == %{"version" => "28.0.2"}
    assert OtpRuntime.finish!(ctx.runtime, receipt)["packaged_files"] == receipt["input_files"]
  end

  test "same-version cached ERTS from another build cannot pass", ctx do
    receipt = OtpRuntime.capture!(ctx.source, "16.0.2", ctx.pin)
    File.write!(Path.join(ctx.runtime, ctx.relative), "a different runtime with the same version")

    assert_raise RuntimeError, ~r/Component verification failed/, fn ->
      OtpRuntime.verify_copied!(ctx.runtime, receipt)
    end
  end

  test "another selected toolchain is not labeled as the official binary archive", ctx do
    File.write!(
      Path.join(ctx.source, ctx.relative),
      <<0xCF, 0xFA, 0xED, 0xFE>> <> "another runtime"
    )

    receipt = OtpRuntime.capture!(ctx.source, "16.0.2", ctx.pin)
    assert receipt["upstream_component"] == nil
  end
end
