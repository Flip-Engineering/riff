"""Managed native engines notice system-library changes before launch."""
import struct
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import platform_support
import run
import setup_engine
from scripts.checkpoint_sha_identity import (
    REPAIR_MESSAGE, crypto_identity, library_soname, verify_runtime, write_runtime_receipt,
)


def elf_library(soname="libcrypto.so.3", patch=b"patch-1", wide=True, endian="<"):
    """Owned ELF metadata fixture; never loads code or modifies system libraries."""
    def pack(fmt, *values):
        return struct.pack(endian + fmt, *values)
    header_size, program_size, dynamic_size = (64, 56, 16) if wide else (52, 32, 8)
    dynamic_offset = header_size + 2 * program_size
    string_offset = dynamic_offset + 3 * dynamic_size
    strings = b"\0" + soname.encode() + b"\0" + patch
    size = string_offset + len(strings)
    ident = b"\x7fELF" + bytes((2 if wide else 1, 1 if endian == "<" else 2, 1)) + b"\0" * 9
    header = pack("HHIQQQIHHHHHH" if wide else "HHIIIIIHHHHHH",
                  3, 62, 1, 0, header_size, 0, 0, header_size, program_size, 2, 0, 0, 0)
    if wide:
        segments = pack("IIQQQQQQ", 1, 4, 0, 0, 0, size, size, 1)
        segments += pack("IIQQQQQQ", 2, 4, dynamic_offset, dynamic_offset, 0, 3 * dynamic_size, 3 * dynamic_size, 1)
    else:
        segments = pack("IIIIIIII", 1, 0, 0, 0, size, size, 4, 1)
        segments += pack("IIIIIIII", 2, dynamic_offset, dynamic_offset, 0, 3 * dynamic_size, 3 * dynamic_size, 4, 1)
    dynamic = b"".join(pack("qQ" if wide else "iI", *row) for row in ((5, string_offset), (14, 1), (0, 0)))
    return ident + header + segments + dynamic + strings


class CryptoLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binary = self.root / "engine"
        self.binary.write_bytes(b"owned engine fixture")
        self.library = self.root / "libcrypto.so.3"
        self.library.write_bytes(elf_library())
        headers = self.root / "openssl"
        headers.mkdir()
        self.header = headers / "opensslv.h"
        self.header.write_text("#define OPENSSL_VERSION_MAJOR 3\n#define OPENSSL_VERSION_PATCH 1\n")
        self.identity = {"sha256_backend": "openssl-evp",
                         "libcrypto_soname": "libcrypto.so.3",
                         "openssl_version_major": "3"}
        self.build = self.root / "build"
        self.build.mkdir()
        (self.build / "CMakeCache.txt").write_text(
            "RIFF_CHECKPOINT_SHA_BACKEND:INTERNAL=openssl-evp\nOPENSSL_CRYPTO_LIBRARY:FILEPATH=" + str(self.library) + "\nOPENSSL_INCLUDE_DIR:PATH=" + str(self.root) + "\n")
        write_runtime_receipt(self.binary, self.build, self.identity)
        self.settings = {"binary": str(self.binary), "model_root": str(self.root / "models"),
                         "model_file": "model.gguf", "vae_file": "vae.gguf", "backend": "cpu",
                         "device": 0, "threads": 1}

    def test_abi_binds_engine_fingerprint_and_portable_selects_another(self):
        first = setup_engine.fingerprint(sha_backend="openssl-evp", sha_identity=self.identity)
        for key, value in (("libcrypto_soname", "libcrypto.so.4"), ("openssl_version_major", "4")):
            changed = {**self.identity, key: value}
            self.assertNotEqual(first, setup_engine.fingerprint(sha_backend="openssl-evp", sha_identity=changed))
        self.assertNotEqual(first, setup_engine.fingerprint(sha_backend="portable", sha_identity={"sha256_backend": "portable"}))

    def test_patch_update_keeps_fingerprint_and_cached_engine_ready(self):
        before = crypto_identity(self.entries())
        self.library.write_bytes(elf_library(patch=b"different security patch bytes"))
        self.header.write_text("#define OPENSSL_VERSION_MAJOR 3\n#define OPENSSL_VERSION_PATCH 2\n")
        after = crypto_identity(self.entries())
        self.assertEqual(before, after)
        self.assertEqual(setup_engine.fingerprint(sha_backend="openssl-evp", sha_identity=before),
                         setup_engine.fingerprint(sha_backend="openssl-evp", sha_identity=after))
        with patch.object(run, "_engine_capabilities", return_value={"exact_score_replay": True}):
            self.assertTrue(run.engine_capabilities(self.settings)["exact_score_replay"])
        verify_runtime(self.binary)

    def entries(self):
        return {"RIFF_CHECKPOINT_SHA_BACKEND": "openssl-evp", "OPENSSL_CRYPTO_LIBRARY": str(self.library),
                "OPENSSL_INCLUDE_DIR": str(self.root)}

    def test_incompatible_soname_replacement_makes_engine_unavailable(self):
        self.library.write_bytes(elf_library(soname="libcrypto.so.4"))
        with self.assertRaisesRegex(ValueError, "different major version"):
            verify_runtime(self.binary)

    def test_soname_alias_survives_patch_file_replacement(self):
        self.library.unlink()
        old = self.root / "libcrypto.so.3.0.1"
        old.write_bytes(elf_library())
        self.library.symlink_to(old)
        write_runtime_receipt(self.binary, self.build, self.identity)
        new = self.root / "libcrypto.so.3.0.2"
        new.write_bytes(elf_library(patch=b"security update"))
        self.library.unlink()
        self.library.symlink_to(new)
        old.unlink()
        verify_runtime(self.binary)

    def test_elf_soname_reader_supports_both_classes_and_byte_orders(self):
        for wide in (True, False):
            for endian in ("<", ">"):
                self.library.write_bytes(elf_library(wide=wide, endian=endian))
                self.assertEqual(library_soname(self.library), "libcrypto.so.3")
        self.library.write_bytes(b"not ELF")
        with self.assertRaises(ValueError):
            library_soname(self.library)

    def test_missing_library_blocks_cached_capabilities_and_command_before_launch(self):
        with patch.object(run, "_engine_capabilities", return_value={"exact_score_replay": True}) as probe:
            self.assertTrue(run.engine_capabilities(self.settings)["exact_score_replay"])
            self.library.unlink()
            self.assertEqual(run.engine_capabilities(self.settings)["error"], REPAIR_MESSAGE)
            self.assertEqual(probe.call_count, 1)
        with patch.object(platform_support, "settings", return_value=self.settings):
            self.assertEqual(platform_support.readiness()["error"], REPAIR_MESSAGE)
            self.assertFalse(platform_support.readiness()["ready"])
            with self.assertRaisesRegex(ValueError, "Download and set up"):
                run.build_command(lyrics="", style="", max_seconds=1, steps=2, cot="off",
                                  seed=1, threads=1, output=self.root / "out.wav")

    def test_portable_rebuild_receipt_recovers_without_any_libcrypto(self):
        self.library.unlink()
        (self.build / "CMakeCache.txt").write_text("RIFF_CHECKPOINT_SHA_BACKEND:INTERNAL=portable\n")
        write_runtime_receipt(self.binary, self.build, {"sha256_backend": "portable"})
        verify_runtime(self.binary)
        with patch.object(run, "_engine_capabilities", return_value={"exact_score_replay": True}):
            self.assertNotIn("error", run.engine_capabilities(self.settings))

    def test_abi_selection_change_during_build_cannot_publish_a_new_receipt(self):
        original = self.binary.with_name("riff-sha256.json").read_bytes()
        for change in ("header-major", "soname", "missing", "backend"):
            with self.subTest(change=change):
                self.header.write_text("#define OPENSSL_VERSION_MAJOR " + ("4" if change == "header-major" else "3") + "\n")
                self.library.write_bytes(elf_library(soname="libcrypto.so.4" if change == "soname" else "libcrypto.so.3"))
                if change == "missing":
                    self.library.unlink()
                if change == "backend":
                    (self.build / "CMakeCache.txt").write_text("RIFF_CHECKPOINT_SHA_BACKEND:INTERNAL=portable\n")
                with self.assertRaisesRegex(ValueError, "changed during the build"):
                    write_runtime_receipt(self.binary, self.build, self.identity)
                self.assertEqual(self.binary.with_name("riff-sha256.json").read_bytes(), original)

    def test_binary_alias_uses_the_managed_receipt(self):
        alias = self.root / "engine-alias"
        alias.symlink_to(self.binary)
        self.library.unlink()
        with self.assertRaisesRegex(ValueError, "Download and set up"):
            verify_runtime(alias)

    def test_older_or_custom_engine_without_receipt_is_unchanged(self):
        self.binary.with_name("riff-sha256.json").unlink()
        self.library.unlink()
        verify_runtime(self.binary)
