"""Exercise the exact composite preflight without a runner or Apple build."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ACTION = Path(__file__).resolve().parents[1] / ".github/actions/require-apple-runner/action.yml"
# The action has one final shell step. Execute its actual body, not a reimplementation.
PREFLIGHT = textwrap.dedent(ACTION.read_text().split("      run: |\n", 1)[1])


class AppleRunnerRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tools = self.root / "bin"
        self.tools.mkdir()
        for tool, body in {
            "uname": 'case "$1" in -s) echo "${FIXTURE_OS:-Darwin}" ;; -m) echo "${FIXTURE_ARCH:-arm64}" ;; *) exit 1 ;; esac',
            "sw_vers": 'echo 15.7',
        }.items():
            path = self.tools / tool
            path.write_text("#!/bin/sh\n" + body + "\n")
            path.chmod(0o755)
        self.summary, self.environment = self.root / "summary", self.root / "environment"
        self.env = {**os.environ, "PATH": str(self.tools) + os.pathsep + os.environ["PATH"],
                    "APPLE_RUNNER_LABEL": "fixture-operator-supplied-label",
                    "APPLE_RUNNER_ENVIRONMENT": "self-hosted", "RUNNER_NAME": "fixture-remote-apple",
                    "RUNNER_TEMP": str(self.root / "temp"), "GITHUB_STEP_SUMMARY": str(self.summary),
                    "GITHUB_ENV": str(self.environment)}

    def execute(self, **changes):
        return subprocess.run(["bash", "-c", PREFLIGHT], env={**self.env, **changes},
                              text=True, capture_output=True)

    def assert_blocked(self, changes, message):
        result = self.execute(**changes)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stdout)
        self.assertIn("Apple validation blocked (#31)", self.summary.read_text())
        self.assertIn("No Apple build, test, desktop acceptance or Metal inference qualification ran", self.summary.read_text())
        self.assertFalse(self.environment.exists())

    def test_unconfigured_route_reports_exact_root_owned_request(self):
        self.assert_blocked({"APPLE_RUNNER_LABEL": ""}, "Set RIFF_APPLE_RUNNER_LABEL only after")
        self.assertIn("registered remote Apple runner", self.summary.read_text())

    def test_hosted_execution_is_rejected_even_if_a_custom_label_is_configured(self):
        self.assert_blocked({"APPLE_RUNNER_ENVIRONMENT": "github-hosted"}, "hosted execution is excluded")

    def test_wrong_actual_platform_and_unidentified_runner_are_rejected(self):
        for changes in ({"FIXTURE_OS": "Linux"}, {"FIXTURE_ARCH": "x86_64"}, {"RUNNER_NAME": ""}):
            with self.subTest(changes=changes):
                self.assert_blocked(changes, "::error::")

    def test_verified_fixture_route_sets_job_paths_without_claiming_inference(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fixture-remote-apple", result.stdout)
        self.assertIn("does not qualify Metal inference", result.stdout)
        settings = dict(line.split("=", 1) for line in self.environment.read_text().splitlines())
        self.assertEqual(settings, {"TMPDIR": self.env["RUNNER_TEMP"],
            "MIX_HOME": self.env["RUNNER_TEMP"] + "/mix", "HEX_HOME": self.env["RUNNER_TEMP"] + "/hex",
            "ImageOS": "macos15"})
        self.assertFalse(self.summary.exists())


if __name__ == "__main__":
    unittest.main()
