"""A prebuilt engine asset of this release activates without a compile, and only when it is intact."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

import setup_engine

ROOT = Path(setup_engine.__file__).resolve().parent


class PrebuiltEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # A fresh install has no workspace yet; activation makes it.
        self.workspace = Path(self.temporary.name) / "workspace"
        for patch in (mock.patch.object(setup_engine, "WORKSPACE", self.workspace),
                      mock.patch.object(setup_engine.platform, "system", return_value="Linux"),
                      mock.patch.object(setup_engine.platform, "machine", return_value="x86_64")):
            patch.start()
            self.addCleanup(patch.stop)
        self.version = (ROOT / "VERSION").read_text().strip()

    def asset(self, backend="cuda", version=None, engine=None, files=None, receipt_files=None, extra=None):
        files = files or {"audiocpp_cli": b"#!/bin/sh\necho engine\n"}
        receipt = {"schema": "riff.prebuilt-engine.v1", "riff_version": version or self.version, "backend": backend,
                   "platform": "linux-x64", "engine": engine or setup_engine.fingerprint(ROOT) + "-" + backend,
                   "files": receipt_files or {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for name, data in {**files, "engine.json": json.dumps(receipt).encode(), **(extra or {})}.items():
                item = tarfile.TarInfo(name if name.startswith(("/", "..")) else "engine/" + name)
                item.size = len(data)
                tar.addfile(item, io.BytesIO(data))
        path = Path(self.temporary.name) / "asset.tar.gz"
        path.write_bytes(buffer.getvalue())
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def prepare(self, path, digest, backend="cuda"):
        return setup_engine.prepare_prebuilt(backend, path, digest, run=mock.Mock(),
                                             download_models=False, activate=False, probe=False)

    def test_an_intact_asset_of_this_release_becomes_the_engine(self):
        path, digest = self.asset()
        value = self.prepare(path, digest)
        binary = Path(value["binary"])
        self.assertEqual(value["backend"], "cuda")
        self.assertEqual(binary.parent.name, "prebuilt-" + setup_engine.fingerprint(ROOT) + "-cuda")
        self.assertTrue(binary.is_relative_to(self.workspace / "engines"))
        self.assertEqual(binary.read_bytes(), b"#!/bin/sh\necho engine\n")

    def test_the_asset_must_match_its_sha256_before_it_is_opened(self):
        path, _ = self.asset()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.prepare(path, "0" * 64)

    def test_an_asset_of_another_release_or_backend_is_refused(self):
        path, digest = self.asset(version="0.0.1")
        with self.assertRaisesRegex(ValueError, "another release"):
            self.prepare(path, digest)
        path, digest = self.asset(backend="cpu")
        with self.assertRaisesRegex(ValueError, "another release"):
            self.prepare(path, digest)

    def test_an_engine_from_other_native_sources_is_refused(self):
        path, digest = self.asset(engine="0123456789abcdef-cuda")
        with self.assertRaisesRegex(ValueError, "other native sources"):
            self.prepare(path, digest)

    def test_a_changed_file_is_refused(self):
        path, digest = self.asset(receipt_files={"audiocpp_cli": "0" * 64})
        with self.assertRaisesRegex(ValueError, "does not match its receipt"):
            self.prepare(path, digest)

    def test_an_entry_outside_the_engine_directory_is_refused(self):
        path, digest = self.asset(extra={"../escape": b"x"})
        with self.assertRaisesRegex(ValueError, "unexpected entry"):
            self.prepare(path, digest)
        self.assertFalse((Path(self.temporary.name) / "escape").exists())


if __name__ == "__main__":
    unittest.main()
