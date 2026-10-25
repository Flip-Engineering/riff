"""Compile-only CI builds select their actual binary for CPU-side harnesses."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/configure_native_checks.py"


class NativeCheckSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.receipt = {"schema": "riff.native-cache-build.v1", "backend": "metal",
                        "build_exit_status": 0, "native_fingerprint": "1234567890abcdef"}
        self.binary = self.workspace / "engines/1234567890abcdef-metal/build/bin/audiocpp_cli"
        self.binary.parent.mkdir(parents=True)
        self.binary.write_text("Not an executable: setup must not launch the engine or compile anything.")
        self.saved = self.workspace / "data/engine.json"
        self.saved.parent.mkdir()
        self.saved.write_text(json.dumps({"backend": "metal", "device": 7, "binary": "/old/engine", "threads": 2}))
        self.receipt_path = self.workspace / "native-cache-receipts/build.json"
        self.receipt_path.parent.mkdir()

    def configure(self):
        self.receipt_path.write_text(json.dumps(self.receipt))
        return subprocess.run([sys.executable, "-B", str(SCRIPT)], text=True, capture_output=True,
                              env={**os.environ, "RIFF_HOME": str(self.workspace)})

    def test_compile_only_metal_build_selects_cpu_harnesses_against_receipt_binary(self):
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = json.loads(self.saved.read_text())
        self.assertEqual(settings["backend"], "cpu")
        self.assertEqual(settings["device"], 0)
        self.assertEqual(settings["threads"], 2)
        self.assertEqual(Path(settings["binary"]), self.binary)
        self.assertIn("no Metal inference qualification", result.stdout)

    def test_failed_build_invalid_fingerprint_and_missing_binary_preserve_old_selection(self):
        before = self.saved.read_bytes()
        for key, value in (("build_exit_status", 1), ("native_fingerprint", "../escape")):
            original = self.receipt[key]
            self.receipt[key] = value
            self.assertNotEqual(self.configure().returncode, 0)
            self.assertEqual(self.saved.read_bytes(), before)
            self.receipt[key] = original
        self.binary.unlink()
        self.assertNotEqual(self.configure().returncode, 0)
        self.assertEqual(self.saved.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
