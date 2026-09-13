"""Actual queue/control/filesystem recovery with a non-neural CLI fixture."""
import hashlib
import http.client
import json
import shutil
import struct
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import test_score_integration as score
import run
from acoustic_artifacts import AcousticArtifacts
from model_admission import resource_inputs, SchedulerUnavailable
from review_recipe import FIELDS, recommended_generation, response_schema
from studio_core import validate_recipe
from studio import StudioServer


ENGINE = r'''
import hashlib, json, shutil, struct, sys, time, wave
from pathlib import Path
args = sys.argv[1:]
if "--help" in args:
    print("feature.yue2.score_tokens=1\nformat.yue2.score_tokens=riff.yue2.score-tokens.v1\nformat.yue2.prefix=riff.yue2.prefix.v1")
    print("feature.yue2.acoustic_checkpoint=1\nfeature.yue2.acoustic_decode=1\nformat.yue2.acoustic=riff.yue2.acoustic.v1")
    print("ode_method <midpoint|ab2>")
    raise SystemExit(0)
options = {}
for index, word in enumerate(args):
    if word == "--request-option":
        key, value = args[index + 1].split("=", 1)
        options[key] = value
output = Path(args[args.index("--out") + 1])
model = Path(args[args.index("--model") + 1])
spec = json.loads((model / "fixture.json").read_text())
output.with_suffix(".request.json").write_text(json.dumps({"argv": args, "options": options}))
if "acoustic_latents_file" in options:
    assert not any(key in options for key in ("score_tokens_file", "score_tokens_out", "semantic_codes_file", "semantic_codes_out", "abc", "style", "cot"))
    assert "--lyrics" not in args and "--seed" not in args
    assert not any(word.startswith("yue2.model_gguf=") for word in args)
    data = Path(options["acoustic_latents_file"]).read_bytes()
else:
    if "score_tokens_out" in options:
        Path(options["score_tokens_out"]).write_text('{"tokens":[],"truncated":false}')
    if "semantic_codes_out" in options:
        codes = Path(options["semantic_codes_out"])
        codes.write_bytes(struct.pack("<i", 7) * 3)
        codes.with_suffix(codes.suffix + ".json").write_text('{"frames":3,"truncated":false}')
    data = bytearray((model / "checkpoint-fixture.bin").read_bytes())
    struct.pack_into("<Q", data, 112, int(args[args.index("--seed") + 1]))
    struct.pack_into("<Q", data, 120, int(options["num_inference_steps"]))
    struct.pack_into("<I", data, 128, 2 if options["ode_method"] == "ab2" else 1)
    struct.pack_into("<f", data, 152, float(options["cfg_scale"]))
    for offset, path in ((192, model / "fixture-model.gguf"), (224, model / "fixture-vae.gguf"), (448, Path(spec["binary"]))):
        data[offset:offset+32] = hashlib.sha256(path.read_bytes()).digest()
    for offset, name in ((256, "yue2-model-config.json"), (288, "yue2-vae-config.json"), (320, "yue2-qwen.tiktoken"), (352, "yue2-generation-config.json")):
        data[offset:offset+32] = hashlib.sha256((model / "sidecars" / name).read_bytes()).digest()
    data[416:448] = hashlib.sha256(Path(options["semantic_codes_out"]).read_bytes()).digest()
    if spec.get("different_steps"): struct.pack_into("<Q", data, 120, 99)
    if spec.get("different_codes"): data[416:448] = hashlib.sha256(b"Other semantic codes").digest()
    data[480:512] = hashlib.sha256(data[:480]).digest()
    if "acoustic_latents_out" in options:
        Path(options["acoustic_latents_out"]).write_bytes(data[:700] if spec.get("partial_acoustic") else data)
if spec.get("fail_after_synthesis"):
    raise SystemExit(9)
if spec.get("wait_after_synthesis"):
    output.with_suffix(".waiting").touch()
    while True: time.sleep(.05)
if options.get("acoustic_only") != "true":
    frames = struct.unpack_from("<Q", data, 32)[0]
    with wave.open(str(output), "wb") as audio:
        audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes((frames * 1920 - 64) * 4))
'''


class AcousticIntegrationTests(unittest.TestCase):
    # Reuse the real control harness, without rerunning its unrelated score cases.
    setUpClass = classmethod(score.ScoreIntegrationTests.setUpClass.__func__)
    tearDown = score.ScoreIntegrationTests.tearDown
    new_control = score.ScoreIntegrationTests.new_control
    start_generator = score.ScoreIntegrationTests.start_generator
    wait_for = score.ScoreIntegrationTests.wait_for

    def setUp(self):
        score.ScoreIntegrationTests.setUp(self)
        (self.root / "fixture_engine.py").write_text(ENGINE)
        shutil.copyfile(score.ROOT / "installer/test/fixtures/acoustic/checkpoint-v1.bin", self.models / "checkpoint-fixture.bin")
        self.set_fixture()

    def set_fixture(self, **value):
        (self.models / "fixture.json").write_text(json.dumps({"binary": str(self.root / "fixture-engine"), **value}))

    @staticmethod
    def recipe(**updates):
        return {"title": "A saved breath", "lyrics": "", "style": "Reflective strings",
                "mode": "free", "cot": "full", "seed": "9223372036854775807", "steps": 4,
                "max_seconds": .12, "render_mode": "music", **updates}

    def finish(self, job, expected):
        self.wait_for(lambda: self.store.job(job["id"])["status"] in score.TERMINAL | {"synthesized"})
        result = self.store.job(job["id"])
        self.assertEqual(result["status"], expected, result["error"])
        return result

    def remove_main_inputs(self):
        # Only fixture-owned files, not actual models or another job's input.
        for name in ("fixture-model.gguf", "sidecars/yue2-model-config.json", "sidecars/yue2-generation-config.json", "sidecars/yue2-qwen.tiktoken"):
            (self.models / name).unlink()

    def test_failed_synthesis_finishes_without_main_inputs_and_keeps_original_seed(self):
        self.set_fixture(fail_after_synthesis=True)
        generator = self.start_generator()
        original = self.finish(generator.submit(self.recipe()), "failed")
        saved = original["recipe"]["acoustic"]
        reference = saved["artifact_id"]
        self.assertEqual(saved["seed"], "9223372036854775807")
        raw_recipe = original["recipe"]
        original_bytes = (self.store.outputs / "riff" / (original["id"] + ".yac")).read_bytes()
        self.remove_main_inputs()
        self.set_fixture()
        with patch.object(generator, "write_idea", side_effect=AssertionError("Writer must not run")), \
             patch.object(self.store.artifacts, "launch", side_effect=AssertionError("Score launch must not run")):
            child = self.finish(generator.submit({"acoustic_source": reference, "decoder": {"halo_frames": 0, "storage": 0}, "title": "A finished breath"}), "done")
        self.assertEqual(self.store.job(original["id"])["recipe"], raw_recipe)
        self.assertEqual(self.store.job(original["id"])["status"], "failed")
        result = self.store.track(child["id"])
        self.assertEqual(result["recipe"]["seed"], "9223372036854775807")
        self.assertEqual(result["recipe"]["parent_track_id"], original["id"])
        self.assertEqual(result["recipe"]["symbolic_plan"], original["recipe"]["symbolic_plan"])
        self.assertEqual(result["recipe"]["performance"], original["recipe"]["performance"])
        self.assertEqual(result["recipe"]["acoustic"], saved)
        # A decoded recording still offers its original performance for another
        # producer review or fresh synthesis, without pretending to emit codes.
        performance = self.store.performance_path(child["id"])
        self.assertEqual(performance.name, original["id"] + ".codes.i32")
        self.assertEqual(hashlib.sha256(performance.read_bytes()).hexdigest(), result["recipe"]["performance"]["sha256"])
        inputs = self.store.acoustics.input_path(child["id"])
        self.assertEqual(inputs.read_bytes(), original_bytes)
        self.assertEqual(result["audio"]["duration"], (3 * 1920 - 64) / 48000)
        self.assertNotIn("latent", json.dumps(self.store.snapshot()))
        self.assertEqual(self.admission.entries, {})

    def test_sound_only_capture_reuse_contract_and_independent_job_input(self):
        generator = self.start_generator()
        original = self.finish(generator.submit(self.recipe(render_mode="sound")), "synthesized")
        self.assertIsNone(original["track_id"])
        reference = original["recipe"]["acoustic"]["artifact_id"]
        described = self.store.acoustics.describe(reference)
        self.assertTrue(described["compatible"])
        self.assertNotIn("path", described)
        self.assertEqual(self.store.snapshot()["jobs"][0]["acoustic_source"], reference)
        for change in ({"seed": "1"}, {"style": "Changed"}, {"steps": 12}, {"decoder": {"halo_frames": -1}}):
            with self.assertRaises(ValueError):
                generator.submit({"acoustic_source": reference, **change})
        child = self.finish(generator.submit({"acoustic_source": reference, "decoder": {"core_frames": 2}}), "done")
        final = self.store.acoustics.resolve(reference)
        self.assertNotEqual(Path(final["path"]).stat().st_ino, self.store.acoustics.input_path(child["id"]).stat().st_ino)
        self.assertEqual(self.store.job(original["id"])["status"], "synthesized")

    def test_incomplete_or_changed_acoustics_are_never_offered(self):
        self.set_fixture(partial_acoustic=True, fail_after_synthesis=True)
        generator = self.start_generator()
        failed = self.finish(generator.submit(self.recipe()), "failed")
        self.assertNotIn("acoustic", failed["recipe"])
        self.assertIn("acoustic", failed["recipe"]["artifact_errors"])
        self.assertEqual(self.store.snapshot()["jobs"][0]["acoustic_source"], "")
        self.set_fixture()
        complete = self.finish(generator.submit(self.recipe()), "done")
        reference = complete["recipe"]["acoustic"]["artifact_id"]
        data = self.store.acoustics.resolve(reference)
        Path(data["path"]).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            generator.submit({"acoustic_source": reference})
        self.assertEqual(Path(data["path"]).read_bytes(), b"changed")

    def test_cancel_after_synthesis_retains_recoverable_stage(self):
        self.set_fixture(wait_after_synthesis=True)
        generator = self.start_generator()
        job = generator.submit(self.recipe())
        self.wait_for(lambda: (self.store.outputs / "riff" / (job["id"] + ".waiting")).exists())
        generator.cancel(job["id"])
        cancelled = self.finish(job, "cancelled")
        self.assertTrue(cancelled["recipe"]["acoustic"]["artifact_id"])
        self.assertEqual(self.admission.entries, {})

    def test_application_shutdown_recovers_completed_native_stage_on_restart(self):
        self.set_fixture(wait_after_synthesis=True)
        generator = self.start_generator()
        job = generator.submit(self.recipe())
        self.wait_for(lambda: (self.store.outputs / "riff" / (job["id"] + ".waiting")).exists())
        generator.close()
        self.generator = None
        stopped = self.store.job(job["id"])
        self.assertEqual(stopped["status"], "interrupted")
        self.assertTrue(stopped["recipe"]["stage_capture_pending"])
        self.new_control()
        self.start_generator()
        recovered = self.store.job(job["id"])
        self.assertEqual(recovered["status"], "interrupted")
        self.assertTrue(recovered["recipe"]["acoustic"]["artifact_id"])
        self.assertNotIn("stage_capture_pending", recovered["recipe"])
        self.assertEqual(recovered["recipe"]["seed"], stopped["recipe"]["seed"])
        with patch.object(self.store.acoustics, "capture", side_effect=AssertionError("Completed recovery must not repeat")):
            self.store.recover_jobs()

    def test_producer_can_select_only_offered_sound_and_retain_its_inputs(self):
        generator = self.start_generator()
        original = self.finish(generator.submit(self.recipe()), "done")
        source = dict(original["recipe"])
        source["available_acoustics"] = self.store.available_acoustics(source)
        reference = source["acoustic"]["artifact_id"]
        value = {key: source[key] for key in FIELDS}
        value.update(acoustic_source=reference, decoder={"core_frames": 32}, title="A refined decoder")
        schema = response_schema(source, False, preserve_seed=True)
        self.assertIn(reference, schema["properties"]["generation"]["properties"]["acoustic_source"]["enum"])
        recommendation = recommended_generation(value, source, False)
        child = self.finish(generator.submit(recommendation), "done")
        self.assertEqual(child["recipe"]["seed"], original["recipe"]["seed"])
        with self.assertRaises(ValueError):
            recommended_generation({**value, "style": "New composition"}, source, False)
        with self.assertRaises(ValueError):
            recommended_generation({**value, "acoustic_source": "riff-acoustic-v1:" + "f" * 64}, source, False)

    def test_historical_inspection_survives_changed_decoder_but_reuse_does_not(self):
        generator = self.start_generator()
        original = self.finish(generator.submit(self.recipe()), "done")
        reference = original["recipe"]["acoustic"]["artifact_id"]
        (self.models / "fixture-vae.gguf").write_bytes(b"Different decoder")
        self.assertFalse(self.store.acoustics.describe(reference)["compatible"])
        with self.assertRaises(ValueError):
            generator.submit({"acoustic_source": reference})

    def test_http_finish_audio_needs_only_decoder_and_keeps_request_checks(self):
        generator = self.start_generator()
        original = self.finish(generator.submit(self.recipe()), "done")
        reference = original["recipe"]["acoustic"]["artifact_id"]
        self.remove_main_inputs()
        server = StudioServer(("127.0.0.1", 0), self.store, generator)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method, path, body=None, extra=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            headers = {"Content-Type": "application/json", "X-Riff-Request": "1", **(extra or {})}
            connection.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        try:
            with patch("studio.platform_support.readiness", return_value={"ready": False}):
                status, detail = request("GET", "/api/acoustics/" + reference)
                self.assertEqual(status, 200)
                self.assertTrue(detail["compatible"])
                self.assertNotIn(str(self.root), json.dumps(detail))
                status, queued = request("POST", "/api/generations", {"acoustic_source": reference})
                self.assertEqual(status, 201, queued)
                self.finish(queued, "done")
                self.assertEqual(request("POST", "/api/generations", self.recipe())[0], 400)
                self.assertEqual(request("POST", "/api/generations", {"acoustic_source": reference}, {"Origin": "https://unrelated.invalid"})[0], 403)
            _, discovery = request("GET", "/api/capabilities")
            self.assertEqual(discovery["operations"]["finish_audio"]["path"], "/api/generations")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_changed_saved_sound_while_queued_fails_before_child(self):
        generator = self.start_generator()
        original = self.finish(generator.submit(self.recipe()), "done")
        reference = original["recipe"]["acoustic"]["artifact_id"]
        result = self.store.acoustics.resolve(reference)
        with generator.lock:
            queued = generator.submit({"acoustic_source": reference})
            Path(result["path"]).write_bytes(b"Changed while waiting")
        self.finish(queued, "failed")
        self.assertFalse((self.store.outputs / "riff" / (queued["id"] + ".request.json")).exists())
        self.assertEqual(self.admission.entries, {})

    def test_same_seed_other_synthesis_is_not_assigned_this_recipe(self):
        generator = self.start_generator()
        for change in ("different_steps", "different_codes"):
            with self.subTest(change=change):
                self.set_fixture(**{change: True})
                job = self.finish(generator.submit(self.recipe(render_mode="sound")), "failed")
                self.assertNotIn("acoustic", job["recipe"])
                self.assertIn("does not match", job["recipe"]["artifact_errors"]["acoustic"])
                self.assertTrue((self.store.outputs / "riff" / (job["id"] + ".yac")).is_file())
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM acoustic_artifacts").fetchone()[0], 0)

    def test_transient_control_failure_retries_without_rewriting_completed_job(self):
        generator = self.start_generator()
        with patch.object(self.store.acoustics, "capture", side_effect=SchedulerUnavailable("Control restarting")):
            original = self.finish(generator.submit(self.recipe()), "done")
        self.assertTrue(original["recipe"]["stage_capture_pending"])
        self.assertNotIn("acoustic", original["recipe"])
        generator.close()
        self.generator = None
        self.new_control()
        self.start_generator()
        recovered = self.store.job(original["id"])
        self.assertTrue(recovered["recipe"]["acoustic"]["artifact_id"])
        self.assertNotIn("stage_capture_pending", recovered["recipe"])
        self.assertEqual({key: original[key] for key in ("status", "created", "started", "finished", "error", "track_id")},
                         {key: recovered[key] for key in ("status", "created", "started", "finished", "error", "track_id")})
        self.assertEqual(self.store.track(original["id"])["recipe"], recovered["recipe"])

    def test_producer_keep_lyrics_uses_preserved_words_before_sound_validation(self):
        generator = self.start_generator()
        original = self.finish(generator.submit(self.recipe(mode="lyrics", lyrics="The tide returns")), "done")
        source = dict(original["recipe"])
        source["available_acoustics"] = self.store.available_acoustics(source)
        value = {key: source[key] for key in FIELDS}
        value.update(acoustic_source=source["acoustic"]["artifact_id"], lyrics="")
        result = recommended_generation(value, source, True)
        self.assertEqual(result["lyrics"], "The tide returns")
        self.finish(generator.submit(result), "done")

    def test_decoded_take_can_supply_another_writer_and_fresh_performance(self):
        generator = self.start_generator()
        original = self.finish(generator.submit(self.recipe()), "done")
        decoded = self.finish(generator.submit({"acoustic_source": original["recipe"]["acoustic"]["artifact_id"]}), "done")
        context = generator.writing_settings({**self.recipe(), "parent_track_id": decoded["id"]})
        self.assertTrue(context["reference"]["performance_available"])
        self.assertEqual(context["performance_track_id"], decoded["id"])
        self.assertTrue(context["available_acoustics"])
        fresh = {**decoded["recipe"], "acoustic_source": "", "decoder": {}, "performance_source": decoded["id"], "steps": 7}
        rendered = self.finish(generator.submit(fresh), "done")
        self.assertEqual(rendered["recipe"]["steps"], 7)
        self.assertEqual(rendered["recipe"]["performance"]["sha256"], decoded["recipe"]["performance"]["sha256"])
