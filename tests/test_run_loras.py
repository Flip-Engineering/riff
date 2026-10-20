"""CLI adapter choices reach session options with verified, recorded identity."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from loras import LoRALibrary
from test_loras import adapter_bytes


ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = ["feature.yue2.lora=1", "format.yue2.lora=riff.yue2.lora.v1"]


@unittest.skipIf(os.name == "nt", "The tiny executable fixture uses a POSIX shell")
class RunLoRATests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="riff-lora-cli-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "data").mkdir()
        (self.root / "models/sidecars").mkdir(parents=True)
        self.library = LoRALibrary(self.root / "data/loras")
        self.addCleanup(self.library.close)
        self.adapters = {}
        for stage, default in (("ar", .75), ("nar", 1.25)):
            file = self.root / (stage + ".safetensors")
            file.write_bytes(adapter_bytes(stage))
            self.adapters[stage] = self.library.import_local([file], metadata={
                "name": stage.upper() + " fixture", "default_strength": default, "license": "CC0"})
        self.output = self.root / "result.wav"
        implementation = self.root / "fixture.py"
        implementation.write_text('''import json,sys,wave
from pathlib import Path
root=Path(__file__).parent
args=sys.argv[1:]
control=json.loads((root/"mutate.json").read_text()) if (root/"mutate.json").exists() else {}
def mutate(when):
    if control.get("when")==when:
        with Path(control["path"]).open("ab") as output:
            output.write(b"changed")
if "--help" in args:
    print((root/"metadata.txt").read_text())
    mutate("help")
else:
    (root/"spawned.json").write_text(json.dumps(args))
    output=Path(args[args.index("--out")+1])
    with wave.open(str(output),"wb") as audio:
        audio.setparams((2,2,48000,0,"NONE","not compressed"))
        audio.writeframes(bytes(480*4))
    mutate("render")
''')
        binary = self.root / "engine"
        binary.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -B "
                          + shlex.quote(str(implementation)) + ' "$@"\n')
        binary.chmod(0o755)
        (self.root / "data/engine.json").write_text(json.dumps({
            "backend": "cpu", "binary": str(binary), "threads": 1}))

    def choices(self, *stages):
        return [argument for stage in stages for argument in ("--lora", self.adapters[stage]["id"])]

    def invoke(self, *extra, metadata=CAPABILITIES, dry_run=True):
        (self.root / "metadata.txt").write_text("\n".join(metadata))
        return subprocess.run([sys.executable, "-B", str(ROOT / "run.py"), "--free", "--cot", "off",
            "--max-seconds", "1", "--steps", "1", "--out", str(self.output), *extra,
            *(["--dry-run"] if dry_run else [])], env={**os.environ, "RIFF_HOME": str(self.root)},
            capture_output=True, text=True, timeout=15)

    def command(self, *extra, metadata=CAPABILITIES):
        result = self.invoke(*extra, metadata=metadata)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "spawned.json").exists())
        return json.loads(result.stdout)

    @staticmethod
    def session_options(command):
        return dict(command[index + 1].split("=", 1) for index, item in enumerate(command)
                    if item == "--session-option")

    def test_independent_stage_strengths_override_overall_and_keep_session_scope(self):
        command = self.command(*self.choices("nar", "ar"), "--lora-strength", ".6",
                               "--ar-lora-strength", "-.2", "--nar-lora-strength", "1.4")
        options = self.session_options(command)
        for stage, scale in (("ar", -.2), ("nar", 1.4)):
            expected = self.library.root / self.adapters[stage]["id"] / (stage + ".safetensors")
            self.assertEqual(options["yue2." + stage + "_lora"], str(expected))
            self.assertEqual(float(options["yue2." + stage + "_lora_scale"]), scale)
        self.assertFalse(any("lora" in command[index + 1].split("=", 1)[0] for index, item in enumerate(command)
                             if item == "--request-option"))

    def test_each_adapter_default_and_explicit_overall_strength_are_honored(self):
        for arguments, expected in (([], {"ar": .75, "nar": 1.25}),
                                    (["--lora-strength", ".3"], {"ar": .3, "nar": .3})):
            with self.subTest(arguments=arguments):
                options = self.session_options(self.command(*self.choices("ar", "nar"), *arguments))
                for stage, scale in expected.items():
                    self.assertEqual(float(options["yue2." + stage + "_lora_scale"]), scale)

    def test_zero_disables_one_stage_and_all_zero_bypasses_older_engine(self):
        options = self.session_options(self.command(*self.choices("ar", "nar"),
            "--lora-strength", ".4", "--ar-lora-strength", "0"))
        self.assertNotIn("yue2.ar_lora", options)
        self.assertNotIn("yue2.ar_lora_scale", options)
        self.assertEqual(float(options["yue2.nar_lora_scale"]), .4)
        for arguments in (["--lora-strength", "0"],
                          ["--ar-lora-strength", "0", "--nar-lora-strength", "0"]):
            with self.subTest(arguments=arguments):
                options = self.session_options(self.command(*self.choices("ar", "nar"), *arguments, metadata=[]))
                self.assertFalse(any("lora" in option for option in options))

    def test_unused_strength_flags_are_errors(self):
        for flag in ("--lora-strength", "--ar-lora-strength", "--nar-lora-strength"):
            with self.subTest(flag=flag):
                result = self.invoke(flag, "0")
                self.assertEqual(result.returncode, 2)
                self.assertIn("require at least one --lora", result.stderr)
        for stage, missing in (("ar", "nar"), ("nar", "ar")):
            for value in ("0", ".5"):
                with self.subTest(stage=stage, value=value):
                    result = self.invoke(*self.choices(stage), "--" + missing + "-lora-strength", value)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("requires a selected " + missing.upper() + " adapter", result.stderr)
        self.assertFalse((self.root / "spawned.json").exists())

    def test_invalid_or_duplicate_ids_and_nonfinite_strengths_never_launch(self):
        for identity in ("f" * 64, "../outside", "too-short"):
            with self.subTest(identity=identity):
                result = self.invoke("--lora", identity)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)
        result = self.invoke(*self.choices("ar", "ar"), "--lora-strength", "0")
        self.assertEqual(result.returncode, 2)
        self.assertIn("only once", result.stderr)
        for value in ("nan", "inf", "-inf", "1e39"):
            for flag in ("--lora-strength", "--ar-lora-strength"):
                with self.subTest(value=value, flag=flag):
                    result = self.invoke(*self.choices("ar"), flag + "=" + value)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("finite", result.stderr)
        self.assertFalse((self.root / "spawned.json").exists())

    def test_active_adapters_require_complete_compatible_engine_capability(self):
        for metadata in ([], CAPABILITIES[:1], [CAPABILITIES[0], "format.yue2.lora=other"]):
            with self.subTest(metadata=metadata):
                result = self.invoke(*self.choices("ar"), metadata=metadata)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Update the music engine", result.stderr)
        self.assertFalse((self.root / "spawned.json").exists())

    def test_metrics_record_exact_adapter_receipts_and_effective_strengths(self):
        result = self.invoke(*self.choices("ar", "nar"), "--lora-strength", ".6",
                             "--ar-lora-strength", ".2", "--nar-lora-strength", "-.4", dry_run=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        metrics = json.loads(self.output.with_suffix(".metrics.json").read_text())
        selection = [{"id": self.adapters[stage]["id"], "strength": .6, stage + "_scale": scale}
                     for stage, scale in (("ar", .2), ("nar", -.4))]
        self.assertEqual(metrics["lora_provenance"], self.library.resolve(selection)["loras"])
        self.assertEqual(metrics["loras"], [{"id": item["id"], **{key: value for key, value in item.items()
                         if key.endswith("_scale")}} for item in selection])
        self.assertEqual(metrics["command"][1:], json.loads((self.root / "spawned.json").read_text()))
        self.assertEqual(metrics["exit_code"], 0)
        self.assertNotIn("lora_error", metrics)

    def test_changed_files_are_rejected_before_generation_launch(self):
        path = self.library.root / self.adapters["ar"]["id"] / "ar.safetensors"
        (self.root / "mutate.json").write_text(json.dumps({"path": str(path), "when": "help"}))
        result = self.invoke(*self.choices("ar"), dry_run=False)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("changed or is corrupt", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.root / "spawned.json").exists())
        self.assertFalse(self.output.exists())

    def test_postrender_hash_failure_marks_metrics_failed_without_repinning(self):
        original = self.library.resolve([{"id": self.adapters["ar"]["id"]}])["loras"]
        path = self.library.root / self.adapters["ar"]["id"] / "ar.safetensors"
        (self.root / "mutate.json").write_text(json.dumps({"path": str(path), "when": "render"}))
        result = self.invoke(*self.choices("ar"), dry_run=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        metrics = json.loads(self.output.with_suffix(".metrics.json").read_text())
        self.assertEqual(metrics["exit_code"], 1)
        self.assertIn("changed or is corrupt", metrics["lora_error"])
        self.assertEqual(metrics["lora_provenance"], original)
        self.assertTrue(self.output.exists())


if __name__ == "__main__":
    unittest.main()
