"""Exercise the actual publisher with a local Git tag and fake gh transport."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PUBLISHER = Path(__file__).resolve().parents[1] / "scripts/publish_source_release.sh"


class SourceReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "source"
        self.root.mkdir()
        (self.root / ".github").mkdir()
        (self.root / ".github/release-notes.md").write_text("Artist-facing changes.\n")
        (self.root / "VERSION").write_text("0.6.32\n")
        dist = self.root / "dist"
        dist.mkdir()
        (dist / "riff-v0.6.32.tar.gz").write_bytes(b"fixture source archive")
        (dist / "install.py").write_text("# fixture installer\n")
        (dist / "unrelated-desktop.payload.tar.gz").write_bytes(b"not a source asset")
        (dist / "SHA256SUMS").write_text("".join(
            hashlib.sha256((dist / name).read_bytes()).hexdigest() + "  " + name + "\n"
            for name in ["riff-v0.6.32.tar.gz", "install.py"]))
        self.git("init", "-q")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.git("tag", "-am", "fixture tag", "v0.6.32")
        self.source = self.git("rev-parse", "HEAD").strip()
        self.remote = Path(self.temporary.name) / "remote.git"
        self.git("init", "--bare", "-q", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "refs/tags/v0.6.32")
        tools = self.root / "tools"
        tools.mkdir()
        (tools / "uname").write_text("#!/bin/sh\ncase $1 in\n-s) echo \"${FIXTURE_OS:-Linux}\";;\n-m) echo \"${FIXTURE_ARCH:-x86_64}\";;\n*) exit 1;;\nesac\n")
        (tools / "gh").write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
notes = Path(args[args.index('--notes-file') + 1]).read_text()
Path(os.environ['FIXTURE_PUBLICATION']).write_text(json.dumps({'args': args, 'notes': notes}))
sys.exit(int(os.environ.get('FIXTURE_GH_EXIT', '0')))
""")
        for tool in tools.iterdir():
            tool.chmod(0o755)
        (self.root / "temporary files").mkdir()
        self.publication = self.root / "publication.json"
        self.environment = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": "Flip-Engineering/riff",
            "GITHUB_REF_TYPE": "tag", "GITHUB_REF": "refs/tags/v0.6.32",
            "GITHUB_REF_NAME": "v0.6.32", "GITHUB_SHA": self.source, "GITHUB_RUN_ID": "12345",
            "RUNNER_ENVIRONMENT": "self-hosted", "RUNNER_TEMP": str(self.root / "temporary files"),
            "FIXTURE_PUBLICATION": str(self.publication)}

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "user.name=Fixture",
            "-c", "user.email=fixture@example.invalid", *args], cwd=self.root, text=True)

    def execute(self, **changes):
        return subprocess.run(["bash", str(PUBLISHER)], cwd=self.root,
            env={**self.environment, **changes}, text=True, capture_output=True)

    def test_tagged_linux_source_has_exact_assets_and_honest_qualification_notes(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        sent = json.loads(self.publication.read_text())
        self.assertEqual(sent["args"][:6], ["release", "create", "v0.6.32",
            "dist/riff-v0.6.32.tar.gz", "dist/install.py", "dist/SHA256SUMS"])
        self.assertIn("--verify-tag", sent["args"])
        self.assertNotIn("--clobber", sent["args"])
        self.assertNotIn("dist/unrelated-desktop.payload.tar.gz", sent["args"])
        self.assertIn("Source archive qualified on self-hosted Linux x86-64", sent["notes"])
        self.assertIn("does not establish Apple Silicon/Metal, CUDA device inference, or desktop-package qualification", sent["notes"])
        self.assertIn(self.source, sent["notes"])
        self.assertIn("actions/runs/12345", sent["notes"])
        self.assertIn("Artist-facing changes.", sent["notes"])

    def test_wrong_event_repository_ref_source_or_runner_cannot_publish(self):
        cases = [{"GITHUB_EVENT_NAME": "workflow_dispatch"},
                 {"GITHUB_REPOSITORY": "another/repository"},
                 {"GITHUB_REF_TYPE": "branch"}, {"GITHUB_REF": "refs/heads/main"},
                 {"GITHUB_REF_NAME": "v0.6.31", "GITHUB_REF": "refs/tags/v0.6.31"},
                 {"GITHUB_SHA": "0" * 40}, {"RUNNER_ENVIRONMENT": "github-hosted"},
                 {"FIXTURE_OS": "Darwin"}, {"FIXTURE_ARCH": "aarch64"}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertNotEqual(self.execute(**changes).returncode, 0)
                self.assertFalse(self.publication.exists())

    def test_tag_must_exist_and_still_select_the_checked_out_source(self):
        self.git("tag", "-d", "v0.6.32")
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertFalse(self.publication.exists())
        (self.root / "changed").write_text("another commit")
        self.git("add", "changed")
        self.git("commit", "-qm", "changed fixture")
        self.git("tag", "v0.6.32", "HEAD~1")
        self.assertNotEqual(self.execute(GITHUB_SHA=self.git("rev-parse", "HEAD").strip()).returncode, 0)
        self.assertFalse(self.publication.exists())

    def test_changed_assets_fail_digest_checks_before_publication(self):
        (self.root / "dist/install.py").write_text("substituted installer")
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertFalse(self.publication.exists())

    def test_lightweight_remote_tag_selecting_the_same_source_is_accepted(self):
        self.git("push", "-q", "--force", "origin", self.source + ":refs/tags/v0.6.32")
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.publication.exists())

    def test_moved_remote_tag_is_rejected_even_when_local_tag_and_event_sha_agree(self):
        self.git("commit", "--allow-empty", "-qm", "new remote source")
        moved = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-am", "replacement annotated tag", "remote-replacement")
        self.git("checkout", "-q", "--detach", self.source)
        self.assertEqual(self.git("rev-parse", "refs/tags/v0.6.32^{commit}").strip(), self.source)
        for replacement in [moved, "refs/tags/remote-replacement"]:
            with self.subTest(replacement=replacement):
                self.git("push", "-q", "--force", "origin", replacement + ":refs/tags/v0.6.32")
                result = self.execute()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("remote version tag no longer selects", result.stderr)
                self.assertFalse(self.publication.exists())

    def test_remote_lookup_failure_and_missing_remote_tag_propagate(self):
        self.git("push", "-q", "origin", ":refs/tags/v0.6.32")
        self.assertEqual(self.execute().returncode, 2)  # ls-remote --exit-code: no match
        self.assertFalse(self.publication.exists())
        self.git("remote", "set-url", "origin", str(self.remote / "missing"))
        self.assertEqual(self.execute().returncode, 128)
        self.assertFalse(self.publication.exists())

    def test_publication_transport_failure_is_not_hidden_or_retried(self):
        result = self.execute(FIXTURE_GH_EXIT="42")
        self.assertEqual(result.returncode, 42)
        self.assertEqual(json.loads(self.publication.read_text())["args"][:2], ["release", "create"])


if __name__ == "__main__":
    unittest.main()
