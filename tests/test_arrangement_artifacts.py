"""Focused checks for arrangement_artifacts.py (riff-arrangement-v1).

Store-backed on temporary roots; no Ardour runtime, no engine control plane.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio_core import Store
from arrangement_artifacts import ArrangementArtifacts, REFERENCE


def build_session_dir(root):
    session = root / "session"
    (session / "interchange" / "take" / "midifiles").mkdir(parents=True, exist_ok=True)
    (session / "take.ardour").write_text("<Session version='3001'/>")
    (session / "interchange" / "take" / "midifiles" / "Flute.mid").write_bytes(b"MThd flute")
    (session / "interchange" / "take" / "midifiles" / "Piano.mid").write_bytes(b"MThd piano")
    return session


class ArrangementArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="riff-arrangements-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = Store(root / "data", root / "outputs")
        self.artifacts = ArrangementArtifacts(self.store)
        self.job = "a" * 32

    def test_record_publishes_content_addressed_reference(self):
        result = self.artifacts.record(self.job, build_session_dir(Path(self.temp.name)),
                                       "Arrangement", "abc2midi 5.07 6582eb8")
        self.assertTrue(REFERENCE.fullmatch(result["reference"]))
        self.assertFalse(result["replayed"])
        self.assertEqual(result["tracks"], 2)
        self.assertEqual(result["converter"], "abc2midi 5.07 6582eb8")
        described = self.artifacts.describe(result["reference"])
        self.assertEqual(described["sha256"], result["sha256"])
        self.assertEqual(described["title"], "Arrangement")
        self.assertEqual(described["job_id"], self.job)
        self.assertNotIn("path", described)
        self.assertIn("take.ardour", described["files"])
        copied = self.artifacts.path(result["reference"])
        self.assertEqual((copied / "take.ardour").read_text(), "<Session version='3001'/>")

    def test_same_content_resolves_to_same_record(self):
        first = self.artifacts.record(self.job, build_session_dir(Path(self.temp.name)),
                                      "One", "abc2midi 5.07 6582eb8")
        again = self.artifacts.record("b" * 32, build_session_dir(Path(self.temp.name)),
                                      "Two", "abc2midi 5.07 6582eb8")
        self.assertEqual(first["reference"], again["reference"])
        self.assertTrue(again["replayed"])
        with self.store.db() as db:
            count = db.execute("SELECT COUNT(*) FROM arrangements").fetchone()[0]
        self.assertEqual(count, 1)

    def test_changed_content_is_a_new_reference(self):
        first = self.artifacts.record(self.job, build_session_dir(Path(self.temp.name)),
                                      "One", "abc2midi 5.07 6582eb8")
        changed = build_session_dir(Path(self.temp.name) / "other")
        (changed / "take.ardour").write_text("<Session version='3001' name='edited'/>")
        second = self.artifacts.record(self.job, changed, "Two", "abc2midi 5.07 6582eb8")
        self.assertNotEqual(first["reference"], second["reference"])

    def test_invalid_and_unknown_references_fail_closed(self):
        for bad in ("riff-arrangement-v1:xyz", "riff-score-v1:" + "a" * 64, "a" * 64, ""):
            with self.assertRaises(ValueError, msg=bad):
                self.artifacts.describe(bad)
        with self.assertRaises(KeyError):
            self.artifacts.describe("riff-arrangement-v1:" + "0" * 64)

    def test_non_directory_and_sessionless_inputs_refused(self):
        with self.assertRaises(ValueError):
            self.artifacts.record(self.job, Path(self.temp.name) / "missing", "X", "tool")
        empty = Path(self.temp.name) / "empty"
        empty.mkdir()
        with self.assertRaises(ValueError):
            self.artifacts.record(self.job, empty, "X", "tool")
        with self.store.db() as db:
            count = db.execute("SELECT COUNT(*) FROM arrangements").fetchone()[0]
        self.assertEqual(count, 0)

    def test_record_requires_a_job_id(self):
        with self.assertRaises(ValueError):
            self.artifacts.record("not-a-job", build_session_dir(Path(self.temp.name)), "X", "tool")

    def test_composition_is_bound_into_the_reference_identity(self):
        # Root's Slice B reproduction: identical session files built from two
        # different original ABCs must be distinct references, each retaining
        # its own inputs.
        one = self.artifacts.record(self.job, build_session_dir(Path(self.temp.name)),
                                    "One", "tool", composition={"abc": "original one"})
        two = self.artifacts.record(self.job, build_session_dir(Path(self.temp.name)),
                                    "Two", "tool", composition={"abc": "original two"})
        self.assertNotEqual(one["reference"], two["reference"])
        self.assertEqual(self.artifacts.describe(two["reference"])["composition"]["abc"],
                         "original two")
        self.assertEqual(self.artifacts.describe(one["reference"])["composition"]["abc"],
                         "original one")

    def test_retrieval_verifies_bytes_and_refuses_mutation(self):
        result = self.artifacts.record(self.job, build_session_dir(Path(self.temp.name)),
                                       "Arrangement", "tool", composition={"abc": "a"})
        stored = self.artifacts.path(result["reference"])
        (stored / "take.ardour").write_text("mutated")
        with self.assertRaises(ValueError):
            self.artifacts.describe(result["reference"])

    def test_symlink_entries_are_refused(self):
        session = build_session_dir(Path(self.temp.name))
        (session / "interchange" / "take" / "midifiles" / "link.mid").symlink_to("/etc/hostname")
        with self.assertRaises(ValueError):
            self.artifacts.record(self.job, session, "X", "tool")


if __name__ == "__main__":
    unittest.main()
