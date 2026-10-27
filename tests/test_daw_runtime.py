"""The pinned Ardour runtime hash gate: symlink refusal is atomic with hashing.

Dependency-free; no Ardour runtime is executed. The store is a namespace with
the one attribute daw_runtime uses (data_root).
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

import daw_runtime


def digest(data):
    return hashlib.sha256(data).hexdigest()


BIN = {"ardour9-lua": b"lua-bytes", "ardour9-export": b"export-bytes",
       "ardour9-new_session": b"newsession-bytes"}


class FakeStore:
    def __init__(self, root):
        self.data_root = Path(root)


class RuntimeGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = FakeStore(self.root)
        self.prefix = self.root / "prefix" / "bin"
        self.prefix.mkdir(parents=True)
        self.template = self.root / "template" / "1region.ardour"
        self.template.parent.mkdir(parents=True)
        self.template.write_bytes(b"session-template")
        self.bank = self.root / "GeneralUser_LV2.sf2"
        self.bank.write_bytes(b"bank-bytes")
        self.record = {
            "version": daw_runtime.APPROVED_VERSION,
            "commit": daw_runtime.APPROVED_COMMIT,
            "kind": "source-build",
            "prefix": str(self.prefix),
            "abc2midi": str(self.root / "abc2midi"),
            "template": str(self.template),
            "converter": daw_runtime.APPROVED_CONVERTER,
            "instrument": {"uri": daw_runtime.GMSYNTH_URI,
                           "sf2_path": str(self.bank),
                           "sf2_sha256": digest(b"bank-bytes")},
            "sha256": {name: digest(data) for name, data in BIN.items()},
            "tools": {"abc2midi": digest(b"abc2midi-bytes"),
                      "template": digest(b"session-template")},
        }
        for name, data in BIN.items():
            (self.prefix / name).write_bytes(data)
            os.chmod(self.prefix / name, 0o755)
        (self.root / "abc2midi").write_bytes(b"abc2midi-bytes")
        os.chmod(self.root / "abc2midi", 0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def write_record(self):
        (self.store.data_root / daw_runtime.RECORD).write_text(json.dumps(self.record))

    def test_valid_record_probes(self):
        self.write_record()
        self.assertEqual(daw_runtime.probe(self.store), self.record)
        self.assertEqual(daw_runtime.require(self.store), self.record)

    def test_absent_record_is_none(self):
        self.assertIsNone(daw_runtime.probe(self.store))

    def test_symlinked_binary_is_refused_even_with_matching_content(self):
        """The gate refuses the link itself, not the link's target bytes."""
        real = self.prefix / "ardour9-lua"
        data = real.read_bytes()
        real.unlink()
        target = self.root / "matched.bin"
        target.write_bytes(data)
        os.symlink(target, real)
        self.write_record()
        with self.assertRaises(daw_runtime.RuntimeUnavailable) as caught:
            daw_runtime.probe(self.store)
        self.assertIn("symlink", str(caught.exception))

    def test_content_swap_is_refused(self):
        (self.prefix / "ardour9-export").write_bytes(b"tampered")
        self.write_record()
        with self.assertRaises(daw_runtime.RuntimeUnavailable) as caught:
            daw_runtime.probe(self.store)
        self.assertIn("no longer", str(caught.exception))

    def test_missing_binary_is_refused(self):
        (self.prefix / "ardour9-new_session").unlink()
        self.write_record()
        with self.assertRaises(daw_runtime.RuntimeUnavailable) as caught:
            daw_runtime.probe(self.store)
        self.assertIn("missing", str(caught.exception))

    def test_non_regular_file_is_refused(self):
        fifo = self.prefix / "ardour9-lua"
        fifo.unlink()
        os.mkfifo(fifo)
        self.write_record()
        with self.assertRaises(daw_runtime.RuntimeUnavailable) as caught:
            daw_runtime.probe(self.store)
        self.assertIn("regular file", str(caught.exception))

    def test_symlinked_record_is_refused(self):
        real = self.store.data_root / "real-record.json"
        self.write_record_at(real)
        (self.store.data_root / daw_runtime.RECORD).symlink_to(real)
        with self.assertRaises(daw_runtime.RuntimeUnavailable) as caught:
            daw_runtime.probe(self.store)
        self.assertIn("record is a symlink", str(caught.exception))

    def test_unapproved_version_is_refused(self):
        self.record["version"] = "9.7"
        self.write_record()
        with self.assertRaises(daw_runtime.RuntimeUnavailable):
            daw_runtime.probe(self.store)

    def test_demo_kind_is_refused(self):
        self.record["kind"] = "demo"
        self.write_record()
        with self.assertRaises(daw_runtime.RuntimeUnavailable) as caught:
            daw_runtime.probe(self.store)
        self.assertIn("demo", str(caught.exception))

    def test_wrong_sf2_is_refused(self):
        self.record["instrument"]["sf2_sha256"] = "0" * 64
        self.write_record()
        with self.assertRaises(daw_runtime.RuntimeUnavailable):
            daw_runtime.probe(self.store)

    def test_changed_bank_is_refused(self):
        """A bank swapped after provisioning fails the byte verification."""
        self.write_record()
        self.bank.write_bytes(b"tampered-bank")
        with self.assertRaises(daw_runtime.RuntimeUnavailable) as caught:
            daw_runtime.probe(self.store)
        self.assertIn("no longer", str(caught.exception))

    def test_missing_bank_path_is_refused(self):
        del self.record["instrument"]["sf2_path"]
        self.write_record()
        with self.assertRaises(daw_runtime.RuntimeUnavailable):
            daw_runtime.probe(self.store)

    def test_symlinked_bank_is_refused(self):
        target = self.root / "real-bank.sf2"
        target.write_bytes(b"bank-bytes")
        self.bank.unlink()
        os.symlink(target, self.bank)
        self.write_record()
        with self.assertRaises(daw_runtime.RuntimeUnavailable) as caught:
            daw_runtime.probe(self.store)
        self.assertIn("symlink", str(caught.exception))

    def write_record_at(self, path):
        path.write_text(json.dumps(self.record))


if __name__ == "__main__":
    unittest.main()
