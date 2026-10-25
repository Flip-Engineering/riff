"""Offline contracts for real-model qualification; no weights or Hub requests."""
import hashlib
import importlib.util
import math
from pathlib import Path
import struct
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_lora_inference", ROOT / "scripts/check_lora_inference.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class InferenceQualificationTests(unittest.TestCase):
    def test_zero_strength_reaches_native_loader_with_both_verified_paths(self):
        recipe = {"threads": 2, "seed": 1234, "lyrics": "[Instrumental]", "style": "trbdr", "semantic_tokens": 25, "steps": 2}
        paths = {"ar_lora": "/verified/ar.safetensors", "nar_lora": "/verified/nar.safetensors"}
        zero = check.command_for(Path("engine"), Path("models"), "metal", recipe, Path("zero"), {"ar": 0, "nar": 0}, paths)
        baseline = check.command_for(Path("engine"), Path("models"), "metal", recipe, Path("base"))
        for stage in ("ar", "nar"):
            self.assertIn(f"yue2.{stage}_lora={paths[stage + '_lora']}", zero)
            self.assertIn(f"yue2.{stage}_lora_scale=0", zero)
            self.assertFalse(any(f"yue2.{stage}_lora" in value for value in baseline))
        self.assertEqual(zero[zero.index("--backend") + 1], "metal")
        self.assertNotIn("--jobs", zero)

    def test_pinned_weights_reject_same_size_corruption_and_truncation(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            path = directory / "weight.gguf"
            path.write_bytes(b"real bytes")
            pins = {"repo": "test", "revision": "a" * 40,
                    "files": {path.name: {"bytes": path.stat().st_size, "sha256": check.digest(path)}}}
            self.assertEqual(check.verify_models(directory, pins)["files"], pins["files"])
            for content in (b"fake bytes", b"short"):
                path.write_bytes(content)
                with self.assertRaisesRegex(ValueError, "verification failed"):
                    check.verify_models(directory, pins)

    def test_remote_pin_mismatch_prevents_install(self):
        class Hub:
            def model(self, repo, revision):
                return {"siblings": [{"rfilename": check.ADAPTER["file"], "size": 1}], "cardData": {"license": "cc-by-nc-4.0"}}
        class Library:
            hub = Hub()
            def list(self): return []
            def hub_install(self, payload): raise AssertionError("A mismatched pin must never start a download")
        with self.assertRaisesRegex(ValueError, "Remote adapter size/hash"):
            check.import_adapter(Library())

    def test_native_timing_samples_remain_separate_and_invalid_values_fail(self):
        raw = "[TIMING ts=20261024-000000] yue2.ar.compute_ms 3\n[TIMING] yue2.ar.compute_ms 5\n"
        self.assertEqual(check.parse_timings(raw), {"yue2.ar.compute_ms": [3, 5]})
        for value in ("nan", "inf", "-1"):
            with self.assertRaises(ValueError):
                check.parse_timings("[TIMING] yue2.ar.compute_ms " + value)
        with self.assertRaisesRegex(ValueError, "no.*TIMING"):
            check.parse_timings("No telemetry")

    def test_metal_memory_uses_current_allocated_size_not_buffer_sum(self):
        logs = ("ggml_metal_log_allocated_size: allocated buffer, size = 32.00 MiB, (16.00 / 6000.00)\n"
                "ggml_metal_log_allocated_size: allocated buffer, size = 8.00 MiB, (20.00 / 6000.00)\n")
        self.assertEqual(check.device_memory(logs, "metal")["peak_bytes"], 20 * 1024 ** 2)
        self.assertIsNone(check.device_memory("", "metal")["peak_bytes"])
        self.assertEqual(check.device_memory("", "cpu")["status"], "not_applicable")

    def test_equal_semantics_cannot_hide_a_broken_zero_nar_or_repeat(self):
        base = {key: "baseline" for key in ("pcm_sha256", "semantic_sha256", "acoustic_payload_sha256")}
        adapted = {key: "adapted" for key in base}
        runs = [{"outputs": dict(x)} for x in (base, base, adapted, adapted)]
        self.assertTrue(all(check.compare_runs(runs).values()))
        runs[1]["outputs"]["pcm_sha256"] = "broken zero NAR"
        self.assertFalse(check.compare_runs(runs)["zero_exact_pcm_sha256"])
        runs[3]["outputs"]["semantic_sha256"] = "nondeterministic repeat"
        self.assertFalse(check.compare_runs(runs)["adapted_repeat_exact_semantic_sha256"])

    def test_checkpoint_provenance_and_finiteness_are_checked(self):
        with tempfile.TemporaryDirectory() as name:
            prefix = Path(name) / "take"
            with wave.open(str(prefix.with_suffix(".wav")), "wb") as stream:
                stream.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                stream.writeframes(struct.pack("<hh", 100, -100))
            prefix.with_suffix(".codes.i32").write_bytes(struct.pack("<i", 7))
            models = {"files": {"yue2-3b-q4_0.gguf": {"sha256": "11" * 32}, "yue2-vae-f16.gguf": {"sha256": "22" * 32}}}
            def checkpoint(value, producer="33" * 32):
                header = bytearray(512)
                header[:8] = b"RIFFYAC1"
                struct.pack_into("<5I", header, 8, 1, 512, 1, 1, 1)
                payload = struct.pack("<f", value)
                struct.pack_into("<3Q", header, 32, 1, 1, 4)
                for offset, digest in ((192, "11" * 32), (224, "22" * 32), (448, producer)):
                    header[offset:offset + 32] = bytes.fromhex(digest)
                header[160:192] = hashlib.sha256(payload).digest()
                header[480:512] = hashlib.sha256(header[:480]).digest()
                prefix.with_suffix(".acoustic").write_bytes(header + payload)
            checkpoint(.25)
            self.assertEqual(check.inspect_outputs(prefix, models, "33" * 32, 1)["semantic_tokens"], 1)
            checkpoint(math.nan)
            with self.assertRaisesRegex(ValueError, "nonfinite"):
                check.inspect_outputs(prefix, models, "33" * 32, 1)
            checkpoint(.25, "44" * 32)
            with self.assertRaisesRegex(ValueError, "provenance differs"):
                check.inspect_outputs(prefix, models, "33" * 32, 1)

    def test_public_receipt_paths_are_normalized_longest_first(self):
        value = {"command": ["/private/checkout/engine", "/private/checkout/out/take.wav"]}
        self.assertEqual(check.normalize_paths(value, {Path('/private/checkout'): '<checkout>', Path('/private/checkout/out'): '<output>'}),
                         {"command": ["<checkout>/engine", "<output>/take.wav"]})


if __name__ == "__main__":
    unittest.main()
