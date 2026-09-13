Code.require_file("../support.exs", __DIR__)
ExUnit.start()

defmodule Riff.Desktop.BuildTest do
  use ExUnit.Case, async: true
  alias Riff.Desktop.Build, as: B

  setup do
    root = Path.join(System.tmp_dir!(), "riff-payload-test-#{System.unique_integer([:positive])}")
    File.mkdir!(root)
    on_exit(fn -> File.rm_rf!(root) end)
    {:ok, root: root}
  end

  test "dereferences owned runtime links and records executable content", %{root: root} do
    source = Path.join(root, "source")
    File.mkdir_p!(Path.join(source, "bin"))
    File.write!(Path.join(source, "bin/python3.14"), "runtime fixture")
    File.chmod!(Path.join(source, "bin/python3.14"), 0o755)
    File.ln_s!("python3.14", Path.join(source, "bin/python3"))
    target = Path.join(root, "payload")
    B.copy_tree!(source, target)
    assert File.lstat!(Path.join(target, "bin/python3")).type == :regular
    assert [a, b] = B.records(target)
    assert a["sha256"] == b["sha256"]
    assert a["executable"] and b["executable"]
    assert a["bytes"] == 15
  end

  test "rejects links escaping the component", %{root: root} do
    source = Path.join(root, "source")
    File.mkdir!(source)
    File.write!(Path.join(root, "unrelated"), "preserve")
    File.ln_s!("../unrelated", Path.join(source, "escape"))

    assert_raise RuntimeError, ~r/leaves its archive/, fn ->
      B.copy_tree!(source, Path.join(root, "payload"))
    end

    assert File.read!(Path.join(root, "unrelated")) == "preserve"
  end

  test "rejects link cycles", %{root: root} do
    source = Path.join(root, "source")
    File.mkdir!(source)
    File.ln_s!("b", Path.join(source, "a"))
    File.ln_s!("a", Path.join(source, "b"))

    assert_raise RuntimeError, ~r/Cyclic/, fn ->
      B.copy_tree!(source, Path.join(root, "payload"))
    end
  end

  test "manifest cannot silently include links", %{root: root} do
    File.write!(Path.join(root, "file"), "original")
    File.ln_s!("file", Path.join(root, "link"))
    assert_raise RuntimeError, ~r/only regular files/, fn -> B.records(root) end
  end

  test "checksum and exact size are both required", %{root: root} do
    path = Path.join(root, "component")
    File.write!(path, "original")
    digest = B.hash(path)
    assert B.verify!(path, 8, digest) == path
    assert_raise RuntimeError, ~r/verification failed/, fn -> B.verify!(path, 7, digest) end
    File.write!(path, "modified")
    assert_raise RuntimeError, ~r/verification failed/, fn -> B.verify!(path, 8, digest) end
  end

  test "archives use exact relative names and neutral owner metadata", %{root: root} do
    source = Path.join(root, "payload")
    File.mkdir_p!(Path.join(source, "app"))
    File.write!(Path.join(source, "app/VERSION"), "0.0.1\n")
    first = Path.join(root, "first.tar.gz")
    second = Path.join(root, "second.tar.gz")
    B.archive!(source, first, 1_700_000_000)
    B.archive!(source, second, 1_700_000_000)
    assert B.hash(first) == B.hash(second)
    assert {:ok, [~c"app/VERSION"]} == :erl_tar.table(String.to_charlist(first), [:compressed])
    raw = first |> File.read!() |> :zlib.gunzip()
    assert binary_part(raw, 265, 64) == :binary.copy(<<0>>, 64)
    assert binary_part(raw, 108, 16) == "0000000\0" <> "0000000\0"

    assert_raise RuntimeError, ~r/already exists/, fn ->
      B.archive!(source, first, 1_700_000_000)
    end
  end

  test "loader-relative links must resolve within the copied payload", %{root: root} do
    binary = Path.join(root, "control/lib/crypto.so")
    File.mkdir_p!(Path.dirname(binary))
    File.mkdir_p!(Path.join(root, "control/native"))
    File.write!(Path.join(root, "control/native/libcrypto.dylib"), "library")
    assert :ok == B.verify_dependency!(root, binary, "@loader_path/../native/libcrypto.dylib", [])

    assert_raise RuntimeError, ~r/Missing or external/, fn ->
      B.verify_dependency!(
        root,
        binary,
        "@loader_path/../../../../Frameworks/libcrypto.dylib",
        []
      )
    end

    assert_raise RuntimeError, ~r/Missing or external/, fn ->
      B.verify_dependency!(root, binary, "@loader_path/missing.dylib", [])
    end
  end

  test "rpath dependencies require a real bundled file", %{root: root} do
    binary = Path.join(root, "lib/extensions/tk.so")
    File.mkdir_p!(Path.dirname(binary))
    File.write!(Path.join(root, "lib/libtk.dylib"), "library")
    assert :ok == B.verify_dependency!(root, binary, "@rpath/libtk.dylib", ["@loader_path/.."])

    assert_raise RuntimeError, ~r/Missing or external/, fn ->
      B.verify_dependency!(root, binary, "@rpath/libtk.dylib", ["/opt/homebrew/lib"])
    end
  end

  test "exact upstream ERTS provenance follows its bytes into the combined payload", %{root: root} do
    {app, control, item, provenance} = otp_fixture(root)
    mapping = B.otp_public_source_files!(app, control, provenance, "control")
    assert mapping == %{("control/" <> item["path"]) => item}

    File.write!(Path.join(control, item["path"]), "different bytes")

    assert_raise RuntimeError, ~r/verification failed/, fn ->
      B.otp_public_source_files!(app, control, provenance, "control")
    end
  end

  test "mutable output metadata cannot grant an upstream exception to different bytes", %{
    root: root
  } do
    {app, control, item, provenance} = otp_fixture(root)
    path = Path.join(control, item["path"])
    File.write!(path, "another compiler")

    forged = [
      %{"path" => item["path"], "bytes" => File.stat!(path).size, "sha256" => B.hash(path)}
    ]

    provenance = Map.merge(provenance, %{"input_files" => forged, "packaged_files" => forged})

    assert_raise RuntimeError, ~r/differs from the pinned upstream archive/, fn ->
      B.otp_public_source_files!(app, control, provenance)
    end

    assert %{} ==
             B.otp_public_source_files!(
               app,
               control,
               Map.put(provenance, "upstream_component", nil)
             )
  end

  @tag skip: :os.type() != {:unix, :darwin}
  test "pinned public compiler paths do not exempt another home path or loader dependency", %{
    root: root
  } do
    source = Path.join(root, "native.c")
    binary = Path.join(root, "native")
    prefix = "/Users/runner/work/otp_builds/otp_builds/tmp/otp_builds/otp-OTP-28.0.2-src/"

    File.write!(
      source,
      "const char *path = \"#{prefix}erts/config\"; int main(void) { return path[0]; }"
    )

    B.run!("/usr/bin/clang", [source, "-o", binary])

    item = %{
      "bytes" => File.stat!(binary).size,
      "sha256" => B.hash(binary),
      "public_source_prefixes" => [prefix]
    }

    assert :ok == B.inspect_macos!(root, verified_source_files: %{"native" => item})
    assert_raise RuntimeError, ~r/Build-host paths remain/, fn -> B.inspect_macos!(root) end

    assert_raise RuntimeError, ~r/verification failed/, fn ->
      B.inspect_macos!(root,
        verified_source_files: %{"native" => Map.put(item, "sha256", String.duplicate("0", 64))}
      )
    end

    File.write!(
      source,
      "const char *path = \"#{prefix}erts/config /Users/private-user/source\"; int main(void) { return path[0]; }"
    )

    B.run!("/usr/bin/clang", [source, "-o", binary])
    item = Map.merge(item, %{"bytes" => File.stat!(binary).size, "sha256" => B.hash(binary)})

    assert_raise RuntimeError, ~r/Build-host paths remain/, fn ->
      B.inspect_macos!(root, verified_source_files: %{"native" => item})
    end

    assert_raise RuntimeError, ~r/Missing or external native dependency/, fn ->
      B.verify_dependency!(root, binary, prefix <> "libcrypto.3.dylib", [])
    end
  end

  defp otp_fixture(root) do
    app = Path.join(root, "app")
    control = Path.join(root, "runtime/control")
    relative = "erts-16.0.2/bin/beam.smp"
    path = Path.join(control, relative)
    File.mkdir_p!(Path.dirname(path))
    File.write!(path, "exact upstream bytes")

    item = %{
      "path" => relative,
      "bytes" => File.stat!(path).size,
      "sha256" => B.hash(path),
      "public_source_prefixes" => [
        "/Users/runner/work/otp_builds/otp_builds/tmp/otp_builds/otp-OTP-28.0.2-src/"
      ]
    }

    component = %{"version" => "28.0.2", "archive" => "verified-upstream-archive"}
    File.mkdir_p!(Path.join(app, "desktop"))

    B.json_write(Path.join(app, "desktop/components.json"), %{
      "otp_runtime" => Map.put(component, "native_files", [item])
    })

    records = [Map.take(item, ["path", "bytes", "sha256"])]

    provenance = %{
      "format_version" => 1,
      "erts_version" => "16.0.2",
      "input_files" => records,
      "packaged_files" => records,
      "upstream_component" => component
    }

    {app, control, item, provenance}
  end

  test "missing and additional native patches cannot produce a claimed fingerprint", %{root: root} do
    File.mkdir!(Path.join(root, "patches"))
    patch = Path.join(root, "patches/one.patch")
    File.write!(patch, "patch")
    pin = %{"bytes" => 5, "sha256" => B.hash(patch)}

    B.json_write(Path.join(root, "sources.json"), %{
      "runtime_commit" => String.duplicate("a", 40),
      "local_patches" => %{"patches/one.patch" => pin}
    })

    assert byte_size(B.engine_fingerprint!(root)) == 16
    File.rename!(patch, patch <> ".saved")
    assert_raise RuntimeError, ~r/patch set differs/, fn -> B.engine_fingerprint!(root) end
    File.rename!(patch <> ".saved", patch)
    File.write!(Path.join(root, "patches/extra.patch"), "patch")
    assert_raise RuntimeError, ~r/patch set differs/, fn -> B.engine_fingerprint!(root) end
  end

  test "a changed scheduler cannot be paired with a stale control runtime", %{root: root} do
    for name <- [
          "installer/mix.exs",
          "installer/mix.lock",
          "sources.json",
          "desktop/build_openssl.exs",
          "desktop/support.exs",
          "runtime/lib/policy.ex"
        ] do
      path = Path.join(root, name)
      File.mkdir_p!(Path.dirname(path))
      File.write!(path, "source fixture")
    end

    component = %{"version" => "fixture"}
    B.json_write(Path.join(root, "desktop/components.json"), %{"openssl" => component})
    control = Path.join(root, "control")
    File.mkdir!(control)
    inputs = B.control_inputs(root)

    digest =
      inputs
      |> :json.encode()
      |> IO.iodata_to_binary()
      |> then(&:crypto.hash(:sha256, &1))
      |> Base.encode16(case: :lower)

    receipt = %{
      "format_version" => 1,
      "files" => inputs,
      "input_sha256" => digest,
      "crypto_component" => component
    }

    B.json_write(Path.join(control, "control-build.json"), receipt)
    assert B.verify_control!(root, control) == receipt

    for changed <- ["runtime/lib/policy.ex", "desktop/build_openssl.exs"] do
      File.write!(Path.join(root, changed), "corrected source")

      assert_raise RuntimeError, ~r/Control runtime differs/, fn ->
        B.verify_control!(root, control)
      end

      File.write!(Path.join(root, changed), "source fixture")
    end
  end
end
