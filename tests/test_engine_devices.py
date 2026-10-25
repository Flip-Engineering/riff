"""Device eligibility without models or native compilation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import engine_devices
import platform_support
import run
import setup_engine
from scripts.check_metal_device import qualification


FIXTURES = Path(__file__).parent / "fixtures/metal"
CAPABLE = (FIXTURES / "capable.log").read_text()
PARAVIRTUAL = (FIXTURES / "paravirtual.log").read_text()


class MetalDeviceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binary = self.root / "engine"
        self.binary.write_text("owned binary fixture")
        self.settings = {"binary": str(self.binary), "backend": "metal", "device": 0,
                         "model_root": str(self.root / "models"), "model_file": "model.gguf",
                         "vae_file": "vae.gguf", "threads": 1}
        engine_devices._device_output.cache_clear()

    def test_captured_paravirtual_and_representative_capable_facts(self):
        supported = engine_devices.metal_device_capability(CAPABLE)
        self.assertEqual(supported["status"], "supported")
        self.assertTrue(supported["device"]["simdgroup_matrix_mul"])
        unsupported = engine_devices.metal_device_capability(PARAVIRTUAL)
        self.assertEqual(unsupported["status"], "unsupported")
        self.assertFalse(unsupported["device"]["simdgroup_reduction"])
        self.assertIn("MTLGPUFamilyApple5", unsupported["device"]["families"][0])
        self.assertIn("RMS_NORM", unsupported["error"])
        self.assertIn("--backend cpu", unsupported["error"])
        # Decisions use capability facts, never device names or memory sizes.
        renamed = PARAVIRTUAL.replace("Apple Paravirtual device", "Apple M99").replace("5010.80", "999999.99")
        self.assertEqual(engine_devices.metal_device_capability(renamed)["status"], "unsupported")

    def test_selected_device_does_not_inherit_another_devices_support(self):
        output = CAPABLE + PARAVIRTUAL.replace("MTL:0", "MTL:1").replace("MTL0", "MTL1")
        self.assertEqual(engine_devices.metal_device_capability(output, 0)["status"], "supported")
        self.assertEqual(engine_devices.metal_device_capability(output, 1)["status"], "unsupported")
        self.assertEqual(engine_devices.metal_device_capability(output, 2)["status"], "unverified")
        self.assertEqual(engine_devices.metal_device_capability('MTL:0 "GPU" [GPU]')["status"], "unverified")

    def test_probe_combines_stderr_and_stdout_caches_and_rechecks_changed_binary(self):
        listing, diagnostics = PARAVIRTUAL.split("ggml_metal_device_init:", 1)
        result = subprocess.CompletedProcess([], 0, listing, "ggml_metal_device_init:" + diagnostics)
        with patch.object(engine_devices.subprocess, "run", return_value=result) as probe:
            self.assertEqual(engine_devices.probe_metal_device(self.binary)["status"], "unsupported")
            engine_devices.probe_metal_device(self.binary)
            self.assertEqual(probe.call_count, 1)
            self.binary.write_text("changed owned binary fixture")
            engine_devices.probe_metal_device(self.binary)
            self.assertEqual(probe.call_count, 2)
        self.assertEqual(probe.call_args.args[0], [str(self.binary), "--list-devices"])

    def test_failed_and_timed_out_probes_are_unverified_not_unsupported(self):
        for result in (subprocess.CompletedProcess([], 1, PARAVIRTUAL, "failure"),
                       subprocess.TimeoutExpired("engine", 90)):
            engine_devices._device_output.cache_clear()
            kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
            with patch.object(engine_devices.subprocess, "run", **kwargs):
                capability = engine_devices.probe_metal_device(self.binary)
            self.assertEqual(capability["status"], "unverified")
            self.assertEqual(qualification(capability)["status"], "failed")

    def test_capabilities_readiness_and_both_runtime_guards_offer_cpu(self):
        with patch.object(engine_devices.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, PARAVIRTUAL, "")), \
                patch.object(run, "_engine_capabilities", return_value={"exact_score_replay": True}), \
                patch.object(platform_support, "settings", return_value=self.settings):
            capability = run.engine_capabilities(self.settings)
            self.assertEqual(capability["recommended_backend"], "cpu")
            self.assertEqual(capability["device"]["status"], "unsupported")
            ready = platform_support.readiness()
            self.assertFalse(ready["ready"])
            self.assertEqual(ready["recommended_backend"], "cpu")
            for command in (lambda: run.build_command(lyrics="", style="", max_seconds=1, steps=2,
                cot="off", seed=1, threads=1, output=self.root / "out.wav"),
                lambda: run.build_decode_command(self.root / "audio.bin", self.root / "out.wav")):
                with self.assertRaisesRegex(ValueError, "simdgroup reduction is unavailable"):
                    command()
            # An explicit CPU render remains possible using the same executable.
            command = run.build_command(lyrics="", style="", max_seconds=1, steps=2, cot="off",
                seed=1, threads=1, output=self.root / "out.wav", backend="cpu")
            self.assertEqual(command[command.index("--backend") + 1], "cpu")

    def test_cpu_readiness_does_not_probe_metal(self):
        required = ["model.gguf", "vae.gguf", "sidecars/yue2-model-config.json", "sidecars/yue2-generation-config.json",
                    "sidecars/yue2-vae-config.json", "sidecars/yue2-qwen.tiktoken"]
        for name in required:
            target = self.root / "models" / name
            target.parent.mkdir(exist_ok=True, parents=True)
            target.touch()
        with patch.object(platform_support, "settings", return_value={**self.settings, "backend": "cpu"}), \
                patch.object(run, "_engine_capabilities", return_value={}), \
                patch.object(engine_devices, "probe_metal_device") as probe:
            self.assertTrue(platform_support.readiness()["ready"])
            probe.assert_not_called()

    def test_missing_metal_binary_reports_error_and_cpu_advice(self):
        self.binary.unlink()
        capability = run.engine_capabilities(self.settings)
        self.assertFalse(capability["exact_score_replay"])
        self.assertEqual(capability["recommended_backend"], "cpu")
        self.assertIn("Cannot verify the Metal engine", capability["error"])
        self.assertIn("--backend cpu", capability["error"])

    def test_setup_rejects_metal_before_activation_or_model_fetch_and_cpu_can_activate(self):
        (self.root / "sources.json").write_text(json.dumps({"runtime_commit": "fixture", "runtime_repo": "fixture", "local_patches": {}}))
        commands = []
        def execute(command):
            commands.append(command)
            if "--build" in command:
                binary = Path(command[command.index("--build") + 1]) / "bin/audiocpp_cli"
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.touch()
        with patch.object(setup_engine, "WORKSPACE", self.root), \
                patch.object(setup_engine, "fingerprint", return_value="fixture"), \
                patch.object(setup_engine, "prerequisites", return_value=[]), \
                patch.object(setup_engine, "apply_pinned_patches"), \
                patch.object(setup_engine.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "fixture", "")), \
                patch.object(engine_devices, "probe_metal_device", return_value=engine_devices.metal_device_capability(PARAVIRTUAL)), \
                patch.object(platform_support, "configure") as activate:
            with self.assertRaisesRegex(ValueError, "--backend cpu"):
                setup_engine.prepare("metal", run=execute, source=self.root)
            activate.assert_not_called()
            self.assertFalse(any("fetch-models.py" in str(part) for command in commands for part in command))
            value = setup_engine.prepare("cpu", run=execute, source=self.root, download_models=False)
            self.assertEqual(value["backend"], "cpu")
            activate.assert_called_once_with(value)

    def test_workflow_probe_records_skip_or_failure_without_claiming_inference(self):
        # Real subprocess boundary, no model or Metal framework required.
        script = Path(__file__).resolve().parents[1] / "scripts/check_metal_device.py"
        for name, output, engine_code, code, status in (("unsupported", PARAVIRTUAL, 0, 0, "not-qualified"),
                                          ("capable", CAPABLE, 0, 0, "eligible"),
                                          ("unknown", "available_devices=0", 0, 1, "failed"),
                                          ("probe-failure", PARAVIRTUAL, 2, 1, "failed")):
            self.binary.write_text("#!" + sys.executable + "\nimport sys\nassert sys.argv[1:] == ['--list-devices']\nprint(" + repr(output) + ")\nsys.exit(" + str(engine_code) + ")\n")
            self.binary.chmod(0o755)
            destination = self.root / name
            summary, github_output = self.root / (name + ".md"), self.root / (name + ".txt")
            result = subprocess.run([sys.executable, str(script), "--binary", str(self.binary), "--output", str(destination)],
                text=True, capture_output=True, env={**os.environ, "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_OUTPUT": str(github_output)})
            self.assertEqual(result.returncode, code, result.stderr)
            receipt = json.loads((destination / "metal-eligibility.json").read_text())
            self.assertEqual(receipt["status"], status)
            self.assertFalse(receipt["qualified"])
            self.assertFalse(receipt["inference_run"])
            self.assertIn("eligible=" + str(name == "capable").lower(), github_output.read_text())
            if name == "unsupported":
                self.assertIn("no LoRA inference qualification", summary.read_text())
                self.assertIn("simdgroup_reduction", summary.read_text())


if __name__ == "__main__":
    unittest.main()
