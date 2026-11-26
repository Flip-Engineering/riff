"""Importing another workspace's library adds every take of both and overwrites nothing."""
import hashlib
import json
import math
import sqlite3
import struct
import tarfile
import tempfile
import unittest
import wave
from pathlib import Path

from library_import import ImportConflict, import_library
from studio_core import Store


def tone(path, hz):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / 8000)))
                                 for i in range(4000)))


def studio(root, takes):
    """A library made by Riff's own Store, with takes recorded by its own add_track."""
    store = Store(root / "data", root / "outputs")
    ids = []
    for n, name in enumerate(takes):
        wav = root / "outputs" / f"{name}.wav"
        tone(wav, 220 + 40 * n)
        ids.append(store.add_track(wav, {"title": name}, {"exit_code": 0}))
    return store, ids


def pack(root, archive):
    with tarfile.open(archive, "w") as tar:
        for part in ("data", "outputs"):
            for path in sorted((root / part).rglob("*")):
                if not path.is_file() or path.name.endswith(("-wal", "-shm")):
                    continue
                name = f"workspace/{part}/{path.relative_to(root / part)}"
                if path.suffix == ".sqlite3":
                    copy = archive.parent / ("backup-" + path.name)
                    source, target = sqlite3.connect(path), sqlite3.connect(copy)
                    source.backup(target)
                    target.close(); source.close()
                    tar.add(copy, arcname=name)
                else:
                    tar.add(path, arcname=name)
    return hashlib.sha256(archive.read_bytes()).hexdigest()


class LibraryImportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        base = Path(self.dir.name)
        self.old, self.new = base / "old", base / "new"
        self.old_store, self.old_ids = studio(self.old, ["dawn", "dusk", "rain"])
        self.store, self.new_ids = studio(self.new, ["cuda-first"])
        self.archive = base / "old.tar"
        self.digest = pack(self.old, self.archive)

    def ids(self):
        with self.store.db() as db:
            return sorted(row[0] for row in db.execute("SELECT id FROM tracks"))

    def revision(self):
        with self.store.db() as db:
            return int(db.execute("SELECT value FROM library_meta WHERE key='catalog_revision'").fetchone()[0])

    def test_every_take_of_both_libraries_is_listed_and_plays(self):
        before = self.revision()
        report = import_library(self.archive, self.digest, "local:old", workspace=self.new)
        self.assertEqual(self.ids(), sorted(self.old_ids + self.new_ids))
        for track_id in self.old_ids + self.new_ids:
            audio = self.store.audio_path(track_id)
            with wave.open(str(audio)) as take:
                self.assertEqual(take.getnframes(), 4000)
        self.assertEqual(report["rows_added"], 3)
        # The studio's own triggers counted the import, so a running studio shows it.
        self.assertGreater(self.revision(), before)

    def test_a_second_import_from_the_same_source_does_nothing(self):
        import_library(self.archive, self.digest, "local:old", workspace=self.new)
        again = import_library(self.archive, self.digest, "local:old", workspace=self.new)
        self.assertEqual(again, {"state": "imported", "already": True, "source": "local:old"})
        self.assertEqual(len(self.ids()), 4)

    def test_a_held_key_with_other_content_stops_the_import_and_names_the_table(self):
        with self.store.db() as db:
            db.execute("INSERT INTO tracks (id,file,title,created,recipe,audio,metrics) "
                       "SELECT ?, 'elsewhere.wav', 'not the same take', created, recipe, audio, metrics FROM tracks LIMIT 1",
                       (self.old_ids[0],))
        with self.assertRaisesRegex(ImportConflict, "Table tracks"):
            import_library(self.archive, self.digest, "local:old", workspace=self.new)
        self.assertEqual(len(self.ids()), 2, "nothing of the import was committed")
        self.assertFalse(list(self.new.glob("imported-from-*")))
        self.assertFalse((self.new / "outputs/dusk.wav").exists(), "no file is copied before the rows join")

    def test_a_take_file_the_studio_holds_under_another_id_stops_the_import(self):
        with self.store.db() as db:
            db.execute("UPDATE tracks SET file='dawn.wav'")
        with self.assertRaisesRegex(ImportConflict, "Table tracks: a row collides"):
            import_library(self.archive, self.digest, "local:old", workspace=self.new)
        self.assertEqual(self.ids(), self.new_ids)

    def test_a_wrong_digest_imports_nothing(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            import_library(self.archive, "0" * 64, "local:old", workspace=self.new)
        self.assertEqual(self.ids(), self.new_ids)

    def test_an_empty_studio_gets_the_whole_library(self):
        empty = Path(self.dir.name) / "empty"
        import_library(self.archive, self.digest, "local:old", workspace=empty)
        store = Store(empty / "data", empty / "outputs")
        with store.db() as db:
            self.assertEqual(sorted(row[0] for row in db.execute("SELECT id FROM tracks")), sorted(self.old_ids))
        self.assertEqual(json.loads((empty / "imported-from-local_old.json").read_text())["sha256"], self.digest)


if __name__ == "__main__":
    unittest.main()
