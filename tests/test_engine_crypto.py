"""Managed native engines notice system-library changes before launch."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import platform_support
import run
import setup_engine
from scripts.checkpoint_sha_identity import (
    REPAIR_MESSAGE, verify_runtime, write_runtime_receipt,
)


class CryptoLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binary = self.root / "engine"
        self.binary.write_bytes(b"owned engine fixture")
        self.library = self.root / "libcrypto-fixture.so"
        self.library.write_bytes(b"library-v1")
        self.identity = {"sha256_backend": "openssl-evp",
                         "libcrypto_sha256": hashlib.sha256(self.library.read_bytes()).hexdigest(),
                         "openssl_headers_sha256": "0" * 64}
        self.build = self.root / "build"
        self.build.mkdir()
        (self.build / "CMakeCache.txt").write_text(
            "RIFF_CHECKPOINT_SHA_BACKEND:INTERNAL=openssl-evp\nOPENSSL_CRYPTO_LIBRARY:FILEPATH=" + str(self.library) + "\n")
        write_runtime_receipt(self.binary, self.build, self.identity)
        self.settings = {"binary": str(self.binary), "model_root": str(self.root / "models"),
                         "model_file": "model.gguf", "vae_file": "vae.gguf", "backend": "cpu",
                         "device": 0, "threads": 1}

    def test_library_bytes_bind_engine_fingerprint_and_missing_crypto_selects_another(self):
        first = setup_engine.fingerprint(sha_backend="openssl-evp", sha_identity=self.identity)
        changed = {**self.identity, "libcrypto_sha256": hashlib.sha256(b"library-v2").hexdigest()}
        self.assertNotEqual(first, setup_engine.fingerprint(sha_backend="openssl-evp", sha_identity=changed))
        self.assertNotEqual(first, setup_engine.fingerprint(sha_backend="portable", sha_identity={"sha256_backend": "portable"}))

    def test_same_size_restored_mtime_change_is_detected_after_successful_verification(self):
        verify_runtime(self.binary)
        before = self.library.stat()
        self.library.write_bytes(b"library-v2")
        os.utime(self.library, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "Download and set up"):
            verify_runtime(self.binary)

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

    def test_library_change_during_build_cannot_publish_a_new_receipt(self):
        original = self.binary.with_name("riff-sha256.json").read_bytes()
        self.library.write_bytes(b"library-v2")
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
