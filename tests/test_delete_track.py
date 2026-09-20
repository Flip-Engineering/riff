import hashlib
import json
import shutil
from unittest.mock import patch

from test_studio import StudioFixture, recipe, Store


class DeleteTrackTests(StudioFixture):
    def test_restart_restores_wav_when_deletion_did_not_commit(self):
        track = self.store.add_track(self.audio, recipe(), {})
        staged = self.audio.with_name("." + track + ".deleting")
        self.audio.rename(staged)
        reopened = Store(self.store.data_root, self.store.outputs)
        self.assertTrue(reopened.audio_path(track).exists())
        self.assertFalse(staged.exists())

    def test_delete_preserves_children_and_breaks_the_link(self):
        codes = self.audio.with_suffix(".codes.i32")
        codes.write_bytes(b"\x07\0\0\0" * 250)
        performance = {"frames": 250, "sha256": hashlib.sha256(codes.read_bytes()).hexdigest()}
        parent = self.store.add_track(self.audio, recipe(performance=performance), {})
        child_audio = self.store.outputs / "child.wav"
        shutil.copyfile(self.audio, child_audio)
        child = self.store.add_track(child_audio, recipe(parent_track_id=parent, performance_source=parent), {})
        with self.store.db() as db:
            db.execute("INSERT INTO jobs(id,title,created,status,recipe) VALUES(?,?,?,?,?)",
                       ("a" * 32, "Queued child", 0, "queued", json.dumps(recipe(parent_track_id=parent, performance_source=parent))))
        expected_audio = child_audio.read_bytes()
        self.assertEqual(self.store.delete_track(parent)["deleted"], parent)
        self.assertFalse(self.audio.exists())
        with self.assertRaises(KeyError): self.store.track(parent)
        child_recipe = self.store.track(child)["recipe"]
        queued_recipe = self.store.job("a" * 32)["recipe"]
        self.assertEqual(child_recipe["parent_track_id"], "")
        self.assertEqual(queued_recipe["parent_track_id"], "")
        self.assertNotEqual(child_recipe["performance_source"], parent)
        self.assertNotEqual(child_recipe["performance_source"], queued_recipe["performance_source"])
        codes.unlink()  # Neither child may depend on the parent's original file.
        for saved in (child_recipe, queued_recipe):
            self.assertEqual(self.store.performance_path(saved["performance_source"]).read_bytes(), b"\x07\0\0\0" * 250)
            self.assertEqual(self.store.prepare_performance(saved)["max_seconds"], 10)
        self.assertEqual(self.store.performance_path(parent).read_bytes(), b"\x07\0\0\0" * 250,
                         "In-flight work holding the old ID uses the child's copy")
        self.assertEqual(child_audio.read_bytes(), expected_audio)
        self.assertEqual(self.store.lineage(child)["edges"], [])
        self.store.import_existing()
        self.assertNotIn(parent, {track["id"] for track in self.store.snapshot()["tracks"]})

    def test_delete_missing_audio_and_archived_recording(self):
        track = self.store.add_track(self.audio, recipe(), {})
        self.store.update_track(track, {"archived": True})
        self.audio.unlink()
        self.store.delete_track(track)
        self.assertEqual(self.store.snapshot()["tracks"], [])
        with self.assertRaises(KeyError): self.store.delete_track(track)

    def test_outside_path_cannot_delete_external_file(self):
        track = self.store.add_track(self.audio, recipe(), {})
        external = self.root / "external.wav"
        external.write_bytes(b"keep me")
        with self.store.db() as db:
            db.execute("UPDATE tracks SET file='../external.wav' WHERE id=?", (track,))
        with self.assertRaises(ValueError): self.store.delete_track(track)
        self.assertEqual(external.read_bytes(), b"keep me")
        self.assertEqual(self.store.track(track)["id"], track)

    def test_copy_failure_keeps_parent_and_child_unchanged(self):
        codes = self.audio.with_suffix(".codes.i32")
        codes.write_bytes(b"\0" * 4)
        parent = self.store.add_track(self.audio, recipe(performance={"frames": 1, "sha256": hashlib.sha256(codes.read_bytes()).hexdigest()}), {})
        other = self.store.outputs / "child.wav"
        shutil.copyfile(self.audio, other)
        child = self.store.add_track(other, recipe(parent_track_id=parent, performance_source=parent), {})
        with patch("studio_core.shutil.copyfile", side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.store.delete_track(parent)
        self.assertTrue(self.audio.exists())
        self.assertEqual(self.store.track(child)["recipe"]["parent_track_id"], parent)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM detached_performances").fetchone()[0], 0)

    def test_shared_audio_is_not_removed(self):
        parent = self.store.add_track(self.audio, recipe(), {})
        alias = self.store.outputs / "alias.wav"
        alias.symlink_to(self.audio)
        with self.store.db() as db:
            db.execute("INSERT INTO tracks SELECT ?,?,title,created,favorite,archived,notes,recipe,audio,metrics FROM tracks WHERE id=?",
                       ("a" * 32, "alias.wav", parent))
        self.store.delete_track(parent)
        self.assertTrue(self.store.audio_path("a" * 32).exists())
        self.store.import_existing()
        self.assertEqual(len(self.store.snapshot()["tracks"]), 1)
