"""The CLI must not silently replace an exact-score request on an older engine."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = ["feature.yue2.score_tokens=1", "format.yue2.score_tokens=riff.yue2.score-tokens.v1",
                "format.yue2.prefix=riff.yue2.prefix.v1"]


@unittest.skipIf(os.name == "nt", "The tiny executable fixture uses a POSIX shell")
class RunScoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="riff-score-cli-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "data").mkdir()
        (self.root / "models/sidecars").mkdir(parents=True)
        (self.root / "models/sidecars/yue2-qwen.tiktoken").write_text("QQ== 0\n")
        self.score = self.root / "saved.json"
        self.score.write_bytes(b'{"tokens":[], "truncated":false}')
        self.abc = self.root / "empty.abc"
        self.abc.write_text("")
        self.output = self.root / "result.wav"
        implementation = self.root / "fixture.py"
        implementation.write_text('''import json,sys
from pathlib import Path
root=Path(__file__).parent
args=sys.argv[1:]
if "--help" in args:
    print((root/"metadata.txt").read_text())
else:
    (root/"spawned.json").write_text(json.dumps(args))
    options=dict(args[i+1].split("=",1) for i,v in enumerate(args) if v=="--request-option")
    Path(options["score_tokens_out"]).write_bytes(Path(options["score_tokens_file"]).read_bytes())
''')
        binary = self.root / "engine"
        binary.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -B " + shlex.quote(str(implementation)) + ' "$@"\n')
        binary.chmod(0o755)
        (self.root / "data/engine.json").write_text(json.dumps({"backend": "cpu", "binary": str(binary), "threads": 1}))

    def invoke(self, metadata, *extra):
        (self.root / "metadata.txt").write_text("\n".join(metadata))
        return subprocess.run([sys.executable, "-B", str(ROOT / "run.py"), "--free", "--cot", "full",
                               "--plan-only", "--score-tokens", str(self.score), "--out", str(self.output), *extra],
                              env={**os.environ, "RIFF_HOME": str(self.root)}, capture_output=True, text=True, timeout=15)

    def test_old_partial_or_incompatible_engine_never_starts_generation(self):
        for metadata in ([], CAPABILITIES[:2], [*CAPABILITIES[:2], "format.yue2.prefix=other"]):
            with self.subTest(metadata=metadata):
                result = self.invoke(metadata)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("reuse saved scores", result.stderr)
                self.assertFalse((self.root / "spawned.json").exists())
                self.assertFalse(self.output.with_suffix(".plan.json").exists())

    def test_supported_engine_receives_exact_file_and_retains_empty_score(self):
        result = self.invoke(CAPABILITIES)
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads((self.root / "spawned.json").read_text())
        self.assertIn("score_tokens_file=" + str(self.score), argv)
        self.assertEqual(self.output.with_suffix(".plan.json").read_bytes(), self.score.read_bytes())

    def test_explicit_empty_abc_is_still_an_ambiguous_second_input(self):
        result = self.invoke(CAPABILITIES, "--abc", str(self.abc), "--dry-run")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("no --abc", result.stderr)
        self.assertFalse((self.root / "spawned.json").exists())

    def test_dry_run_remains_inspectable_without_engine_replay_support(self):
        result = self.invoke([], "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("score_tokens_file=" + str(self.score), json.loads(result.stdout))
        self.assertFalse((self.root / "spawned.json").exists())
