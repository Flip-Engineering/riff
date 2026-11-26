"""Importing another workspace's library adds its takes and overwrites nothing."""
import hashlib
import json
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path

from library_import import import_library
from studio_core import Store


def track(db, track_id, file):
    db.execute("INSERT INTO tracks (id, file, title, created, recipe, audio, metrics) VALUES (?, ?, ?, 1, '{}', '{}', '{}')",
               (track_id, file, track_id))


def workspace(root, takes, files):
    store = Store(root / "data", root / "outputs")
    with store.db() as db:
        for take in takes:
            track(db, take, take + ".wav")
    for name, data in files.items():
        (root / "outputs" / name).write_bytes(data)
    return store


def pack(root, archive):
    with tarfile.open(archive, "w") as tar:
        for part in ("data", "outputs"):
            for path in sorted((root / part).rglob("*")):
                if path.is_file() and not path.name.endswith(("-wal", "-shm")):
                    if path.suffix == ".sqlite3":
                        copy = archive.parent / ("backup-" + path.name)
                        source, target = sqlite3.connect(path), sqlite3.connect(copy)
                        source.backup(target)
                        target.close(); source.close()
                        tar.add(copy, arcname=f"workspace/{part}/{path.relative_to(root / part)}")
                    else:
                        tar.add(path, arcname=f"workspace/{part}/{path.relative_to(root / part)}")
    return hashlib.sha256(archive.read_bytes()).hexdigest()


class LibraryImportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        base = Path(self.dir.name)
        self.old, self.new = base / "old", base / "new"
        workspace(self.old, ["a1", "b2"], {"a1.wav": b"old a1", "b2.wav": b"old b2"})
        self.store = workspace(self.new, ["own", "a1"], {"own.wav": b"mine", "a1.wav": b"the studio's a1"})
        self.archive = base / "old.tar"
        self.digest = pack(self.old, self.archive)

    def ids(self):
        with self.store.db() as db:
            return sorted(row[0] for row in db.execute("SELECT id FROM tracks"))

    def revision(self):
        with self.store.db() as db:
            return int(db.execute("SELECT value FROM library_meta WHERE key='catalog_revision'").fetchone()[0])

    def test_the_takes_join_and_nothing_of_the_studio_changes(self):
        before = self.revision()
        report = import_library(self.archive, self.digest, "local:old", workspace=self.new)
        self.assertEqual(self.ids(), ["a1", "b2", "own"])
        self.assertEqual((self.new / "outputs/b2.wav").read_bytes(), b"old b2")
        self.assertEqual((self.new / "outputs/a1.wav").read_bytes(), b"the studio's a1")
        self.assertEqual((self.new / "outputs/own.wav").read_bytes(), b"mine")
        self.assertEqual((report["rows_added"], report["files_added"], report["files_kept"]), (1, 1, 1))
        # The studio's own triggers counted the import, so a running studio shows it.
        self.assertGreater(self.revision(), before)

    def test_a_second_import_from_the_same_source_does_nothing(self):
        import_library(self.archive, self.digest, "local:old", workspace=self.new)
        again = import_library(self.archive, self.digest, "local:old", workspace=self.new)
        self.assertEqual(again, {"state": "imported", "already": True, "source": "local:old"})
        self.assertEqual(self.ids(), ["a1", "b2", "own"])

    def test_a_wrong_digest_imports_nothing(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            import_library(self.archive, "0" * 64, "local:old", workspace=self.new)
        self.assertEqual(self.ids(), ["a1", "own"])
        self.assertFalse(list(self.new.glob("imported-from-*")))

    def test_an_empty_studio_gets_the_whole_library(self):
        empty = Path(self.dir.name) / "empty"
        report = import_library(self.archive, self.digest, "local:old", workspace=empty)
        db = sqlite3.connect(empty / "data/library.sqlite3")
        self.assertEqual(sorted(row[0] for row in db.execute("SELECT id FROM tracks")), ["a1", "b2"])
        self.assertEqual(json.loads((empty / "imported-from-local_old.json").read_text())["sha256"], self.digest)
        self.assertEqual(report["files_added"], 3)


if __name__ == "__main__":
    unittest.main()
