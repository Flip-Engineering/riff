"""Exercise prepared and interrupted engine updates with real overlapping patches."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

from setup_engine import apply_pinned_patches


class EnginePatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.checkout = self.root / "engine"
        self.checkout.mkdir()
        self.source = self.root / "release"
        (self.source / "patches").mkdir(parents=True)
        self.git("init", "--quiet")
        self.file = self.checkout / "engine.cpp"
        self.file.write_text("header\noriginal\nfooter\n")
        (self.checkout / "other.cpp").write_text("unchanged\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@riff.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Pinned source")
        self.manifest = {"runtime_commit": self.git("rev-parse", "HEAD").stdout.decode().strip(), "local_patches": {}}
        for name, content in (("01-first.patch", "header\nfirst\nfooter\n"),
                              ("02-overlap.patch", "header changed\nsecond\nfooter\n")):
            self.file.write_text(content)
            path = self.source / "patches" / name
            path.write_bytes(self.git("diff").stdout)
            self.manifest["local_patches"]["patches/" + name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            self.git("add", "engine.cpp")
        self.git("reset", "--hard", "HEAD")  # This isolated fixture is owned by the test.
        self.commands = []

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.checkout), *args], check=True, capture_output=True)

    def execute(self, command):
        self.commands.append(command)
        subprocess.run(command, check=True, capture_output=True)

    def apply(self, run=None):
        apply_pinned_patches(self.checkout, self.source, self.manifest, run or self.execute)

    def test_complete_overlapping_patch_set_is_idempotent_and_preserves_real_index(self):
        index = (self.checkout / ".git/index").read_bytes()
        untracked = self.checkout / "notes.txt"
        untracked.write_text("Keep this local work.")
        self.apply()
        self.assertEqual(self.file.read_text(), "header changed\nsecond\nfooter\n")
        isolated_reverse = subprocess.run(["git", "-C", str(self.checkout), "apply", "--reverse", "--check",
                                          str(self.source / "patches/01-first.patch")], capture_output=True)
        self.assertNotEqual(isolated_reverse.returncode, 0)
        self.commands.clear()
        self.apply()
        self.assertEqual(self.commands, [])
        self.assertEqual((self.checkout / ".git/index").read_bytes(), index)
        self.assertEqual(untracked.read_text(), "Keep this local work.")

    def test_interrupted_valid_prefix_resumes_only_remaining_patch(self):
        def interrupted(command):
            self.execute(command)
            raise OSError("Interrupted after writing the first patch")
        with self.assertRaises(OSError):
            self.apply(interrupted)
        self.assertEqual(self.file.read_text(), "header\nfirst\nfooter\n")
        self.commands.clear()
        self.apply()
        self.assertEqual(len(self.commands), 1)
        self.assertTrue(self.commands[0][-1].endswith("02-overlap.patch"))
        self.assertEqual(self.file.read_text(), "header changed\nsecond\nfooter\n")

    def test_unrelated_tracked_edits_are_preserved_before_any_patch_is_applied(self):
        other = self.checkout / "other.cpp"
        other.write_text("Local experiment\n")
        with self.assertRaisesRegex(ValueError, "local source changes"):
            self.apply()
        self.assertEqual(self.commands, [])
        self.assertEqual(other.read_text(), "Local experiment\n")
        self.assertEqual(self.file.read_text(), "header\noriginal\nfooter\n")

    def test_edit_to_completed_engine_is_preserved(self):
        self.apply()
        self.file.write_text(self.file.read_text() + "A local experiment\n")
        self.commands.clear()
        with self.assertRaisesRegex(ValueError, "local source changes"):
            self.apply()
        self.assertEqual(self.commands, [])
        self.assertTrue(self.file.read_text().endswith("A local experiment\n"))

    def test_later_checksum_failure_leaves_even_first_patch_unapplied(self):
        with (self.source / "patches/02-overlap.patch").open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.apply()
        self.assertEqual(self.commands, [])
        self.assertEqual(self.file.read_text(), "header\noriginal\nfooter\n")


if __name__ == "__main__":
    unittest.main()
