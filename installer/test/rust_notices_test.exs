Code.require_file("../scripts/rust_notices.exs", __DIR__)

defmodule Riff.Installer.RustNoticesTest do
  use ExUnit.Case, async: true
  alias Riff.Installer.RustNotices

  setup do
    root = Path.join(System.tmp_dir!(), "riff-rust-notices-#{System.unique_integer([:positive])}")
    File.mkdir!(root)
    on_exit(fn -> File.rm_rf!(root) end)
    %{root: root, sysroot: Path.join(root, "toolchain"), destination: Path.join(root, "notices")}
  end

  test "minimal rustup keeps generated notices and their license links without docs", ctx do
    files = toolchain(ctx.sysroot, "share/doc/rust")
    write(ctx.sysroot, "share/doc/rust/html/index.html", "unrelated developer documentation")
    receipt = RustNotices.copy!(ctx.sysroot, ctx.destination)

    assert receipt["layout"] == "share/doc/rust"
    assert Enum.map(receipt["files"], & &1["path"]) == Enum.sort(Map.keys(files))
    assert_preserved(ctx, files, receipt)
    refute File.exists?(Path.join(ctx.destination, "share/doc/rust/html"))
  end

  test "Homebrew retains its root notices and the generated standard-library notice tree", ctx do
    files =
      toolchain(ctx.sysroot, "share/doc/rustc")
      |> Map.merge(%{"LICENSE-MIT" => "root MIT", "LICENSE-APACHE" => "root Apache"})

    for {path, bytes} <- files, do: write(ctx.sysroot, path, bytes)
    receipt = RustNotices.copy!(ctx.sysroot, ctx.destination)
    assert receipt["layout"] == "share/doc/rustc"
    assert_preserved(ctx, files, receipt)
  end

  test "generic root licenses do not replace missing standard-library notices", ctx do
    write(ctx.sysroot, "LICENSE-MIT", "MIT")
    write(ctx.sysroot, "LICENSE-APACHE", "Apache")

    assert_raise RuntimeError, ~r/standard-library copyright notices were not found/, fn ->
      RustNotices.copy!(ctx.sysroot, ctx.destination)
    end

    refute File.exists?(ctx.destination)
  end

  test "missing or empty required license texts fail before copying", ctx do
    toolchain(ctx.sysroot, "share/doc/rust")
    path = Path.join(ctx.sysroot, "share/doc/rust/licenses/MIT.txt")
    File.rm!(path)

    assert_raise RuntimeError, ~r/Rust notice is missing/, fn ->
      RustNotices.copy!(ctx.sysroot, ctx.destination)
    end

    File.write!(path, "")

    assert_raise RuntimeError, ~r/missing or empty/, fn ->
      RustNotices.copy!(ctx.sysroot, ctx.destination)
    end

    refute File.exists?(ctx.destination)
  end

  test "license directory links cannot include unrelated host files", ctx do
    toolchain(ctx.sysroot, "share/doc/rust")
    outside = Path.join(ctx.root, "outside")
    File.mkdir!(outside)
    File.write!(Path.join(outside, "secret.txt"), "unrelated host data")
    File.ln_s!(outside, Path.join(ctx.sysroot, "share/doc/rust/licenses/extra"))

    assert_raise RuntimeError, ~r/must not follow symbolic links/, fn ->
      RustNotices.copy!(ctx.sysroot, ctx.destination)
    end

    refute File.exists?(ctx.destination)
  end

  defp toolchain(root, layout) do
    files = %{
      Path.join(layout, "COPYRIGHT.html") => "<a href=\"licenses/Apache-2.0.txt\">Apache</a>",
      Path.join(layout, "COPYRIGHT-library.html") => "<a href=\"licenses/MIT.txt\">MIT</a>",
      Path.join(layout, "licenses/Apache-2.0.txt") => "Apache license",
      Path.join(layout, "licenses/MIT.txt") => "MIT license",
      Path.join(layout, "licenses/LLVM-exception.txt") => "LLVM exception"
    }

    for {path, bytes} <- files, do: write(root, path, bytes)
    files
  end

  defp write(root, relative, bytes) do
    target = Path.join(root, relative)
    File.mkdir_p!(Path.dirname(target))
    File.write!(target, bytes)
  end

  defp assert_preserved(ctx, files, receipt) do
    assert length(receipt["files"]) == map_size(files)

    for item <- receipt["files"] do
      assert File.read!(Path.join(ctx.destination, item["path"])) == files[item["path"]]
      assert item["bytes"] == byte_size(files[item["path"]])

      assert item["sha256"] ==
               :crypto.hash(:sha256, files[item["path"]]) |> Base.encode16(case: :lower)

      refute String.contains?(item["path"], ctx.root)
    end
  end
end
