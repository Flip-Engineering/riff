Code.require_file("../../desktop/support.exs", __DIR__)
Code.require_file("../scripts/crypto_component.exs", __DIR__)

defmodule Riff.Installer.CryptoComponentTest do
  use ExUnit.Case, async: true
  alias Riff.Desktop.Build, as: B
  alias Riff.Installer.CryptoComponent, as: Crypto

  @moduletag skip: :os.type() != {:unix, :darwin}

  setup do
    root = Path.join(System.tmp_dir!(), "riff-crypto-abi-#{System.unique_integer([:positive])}")
    File.mkdir!(root)
    on_exit(fn -> File.rm_rf!(root) end)
    %{root: root, beam: Crypto.host!().beam}
  end

  test "verifies every import with absolute, rpath, and loader-relative install names", ctx do
    for {name, install_name} <- [
          {"absolute", "/unavailable/build-host/libcrypto.3.dylib"},
          {"rpath", "@rpath/libcrypto.3.dylib"},
          {"loader", "@loader_path/../libcrypto.3.dylib"}
        ] do
      library =
        library(ctx.root, name, install_name, "int riff_required_crypto(void) { return 1; }")

      nif =
        nif(
          ctx.root,
          name,
          library,
          "extern int riff_required_crypto(void); int nif_init(void) { return riff_required_crypto(); }"
        )

      result = Crypto.verify_nif!(nif, library, ctx.beam)
      assert result["required_symbols"] == ["_riff_required_crypto"]
      assert result["missing_symbols"] == []
    end
  end

  test "a replacement missing a real import cannot pass by intersecting exports", ctx do
    original =
      library(
        ctx.root,
        "original",
        "@rpath/libcrypto.3.dylib",
        "int riff_missing_crypto(void) { return 1; }"
      )

    replacement =
      library(
        ctx.root,
        "replacement",
        "@rpath/libcrypto.3.dylib",
        "int riff_other_crypto(void) { return 1; }"
      )

    nif =
      nif(
        ctx.root,
        "missing",
        original,
        "extern int riff_missing_crypto(void); int nif_init(void) { return riff_missing_crypto(); }"
      )

    assert_raise RuntimeError, ~r/ABI mismatch: _riff_missing_crypto/, fn ->
      Crypto.verify_nif!(nif, replacement, ctx.beam)
    end
  end

  test "embedded OpenSSL must be rebuilt rather than treating zero imports as success", ctx do
    source =
      "int OpenSSL_version(void) { return 1; } int EVP_MD_fetch(void) { return 1; } int SHA256(void) { return 1; }"

    nif = nif(ctx.root, "embedded", nil, source)
    assert Crypto.linkage!(nif) == "embedded"

    assert_raise RuntimeError, ~r/embeds OpenSSL/, fn ->
      Crypto.verify_nif!(nif, nif, ctx.beam)
    end
  end

  test "the NIF's ERTS imports must exist in the exact target runtime", ctx do
    library =
      library(
        ctx.root,
        "crypto",
        "@rpath/libcrypto.3.dylib",
        "int riff_crypto(void) { return 1; }"
      )

    nif =
      nif(
        ctx.root,
        "erts",
        library,
        "extern int riff_crypto(void); extern int enif_unavailable_in_runtime(void); int nif_init(void) { return riff_crypto() + enif_unavailable_in_runtime(); }"
      )

    assert_raise RuntimeError, ~r/unavailable ERTS APIs: _enif_unavailable_in_runtime/, fn ->
      Crypto.verify_nif!(nif, library, ctx.beam)
    end
  end

  test "unattributed non-ERTS imports and another OpenSSL major fail closed", ctx do
    library =
      library(
        ctx.root,
        "crypto",
        "@rpath/libcrypto.3.dylib",
        "int riff_crypto(void) { return 1; }"
      )

    nif =
      nif(
        ctx.root,
        "unknown",
        library,
        "extern int riff_crypto(void); extern int unknown_provider(void); int nif_init(void) { return riff_crypto() + unknown_provider(); }"
      )

    assert_raise RuntimeError, ~r/unattributed non-ERTS/, fn ->
      Crypto.verify_nif!(nif, library, ctx.beam)
    end

    old =
      library(
        ctx.root,
        "old",
        "@rpath/libcrypto.1.1.dylib",
        "int riff_crypto(void) { return 1; }"
      )

    nif =
      nif(
        ctx.root,
        "old",
        old,
        "extern int riff_crypto(void); int nif_init(void) { return riff_crypto(); }"
      )

    assert_raise RuntimeError, ~r/different OpenSSL ABI/, fn ->
      Crypto.verify_nif!(nif, library, ctx.beam)
    end
  end

  defp library(root, name, install_name, code) do
    source = Path.join(root, name <> "-library.c")
    target = Path.join(root, name <> "-libcrypto.3.dylib")
    File.write!(source, code)

    B.run!("/usr/bin/clang", [
      "-arch",
      "arm64",
      "-dynamiclib",
      "-install_name",
      install_name,
      source,
      "-o",
      target
    ])

    target
  end

  defp nif(root, name, library, code) do
    source = Path.join(root, name <> "-nif.c")
    target = Path.join(root, name <> "-crypto.so")
    File.write!(source, code)

    B.run!(
      "/usr/bin/clang",
      ["-arch", "arm64", "-bundle", "-undefined", "dynamic_lookup", source] ++
        List.wrap(library) ++ ["-o", target]
    )

    target
  end
end
