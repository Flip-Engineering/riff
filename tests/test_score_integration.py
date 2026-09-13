"""Real application/Elixir score boundary; a tiny CLI fixture never loads a model."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

import run
from model_admission import ModelAdmission, SchedulerNotInstalled
from score_artifacts import ScoreArtifacts
from studio_core import Generator, Store, validate_recipe


ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"done", "planned", "performed", "failed", "cancelled", "interrupted"}
NOTATION = "X:1\nK:C\nC4|\n"

# This executable consumes the application's real argv. Its own request log
# proves the score-file option survived the queue/command boundary. There is
# no model implementation, tokenizer substitute in the artifact service, or
# assertion of neural parity here.
ENGINE = r'''
import json, shutil, struct, sys, wave
from pathlib import Path
args = sys.argv[1:]
if "--help" in args:
    print("feature.yue2.score_tokens=1")
    print("format.yue2.score_tokens=riff.yue2.score-tokens.v1")
    print("format.yue2.prefix=riff.yue2.prefix.v1")
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
if "score_tokens_file" in options:
    assert "abc" not in options
    raw = Path(options["score_tokens_file"]).read_bytes()
else:
    raw = json.dumps({"tokens": spec.get("tokens", []), "truncated": spec.get("truncated", False)}).encode()
if "score_tokens_out" in options and "semantic_codes_file" not in options:
    Path(options["score_tokens_out"]).write_bytes(b'{"tokens":[' if spec.get("partial_score") else raw)
if "semantic_codes_out" in options:
    codes = Path(options["semantic_codes_out"])
    if "semantic_codes_file" in options:
        shutil.copyfile(options["semantic_codes_file"], codes)
    else:
        codes.write_bytes(struct.pack("<i", 7) * (1 if spec.get("partial_performance") else 4))
    codes.with_suffix(codes.suffix + ".json").write_text(json.dumps({"frames": 4, "truncated": True}))
if options.get("plan_only") != "true" and options.get("semantic_only") != "true":
    with wave.open(str(output), "wb") as audio:
        audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(1920 * 4 * 4))
'''


class ScoreIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = shutil.which("elixir")
        if not executable:
            if os.environ.get("RIFF_REQUIRE_SCORE_INTEGRATION") == "1":
                raise AssertionError("The control CI job must provide Elixir and run mix check first.")
            raise unittest.SkipTest("Application-only environment has no Elixir runtime")
        library = ROOT / "installer/_build/test/lib"
        required = [library / "riff_installer/ebin/Elixir.Riff.Runtime.ScoreArtifact.beam",
                    library / "riff_installer/ebin/Elixir.Riff.Runtime.SchedulerPort.beam",
                    library / "jason/ebin/Elixir.Jason.beam"]
        if any(not path.is_file() for path in required):
            if os.environ.get("RIFF_REQUIRE_SCORE_INTEGRATION") == "1":
                raise AssertionError("Run mix check in installer before this real control integration suite.")
            raise unittest.SkipTest("Application-only checkout has no compiled score control modules")
        cls.control = [executable]
        for path in sorted(library.glob("*/ebin")):
            cls.control += ["-pa", str(path)]
        cls.control += ["-e", "Riff.Runtime.SchedulerPort.main()"]

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="riff-score-integration-")
        self.root = Path(self.temporary.name).resolve()
        self.models = self.root / "models"
        (self.models / "sidecars").mkdir(parents=True)
        self.vocabulary = self.models / "sidecars/yue2-qwen.tiktoken"
        self.vocabulary.write_bytes(base64.b64encode(NOTATION.encode()) + b" 0\n" + base64.b64encode(b"\xff\x00") + b" 1\n")
        for name, content in {
            "yue2-model-config.json": {"num_hidden_layers": 1, "num_key_value_heads": 1, "head_dim": 8},
            "yue2-generation-config.json": {"context": 24576},
            "yue2-vae-config.json": {"sample_rate": 48000, "downsampling_ratio": 1920},
        }.items():
            (self.models / "sidecars" / name).write_text(json.dumps(content))
        for name in ("fixture-model.gguf", "fixture-vae.gguf"):
            (self.models / name).write_bytes(b"Fixture identity only; this is not a model.")
        self.set_fixture(tokens=[])
        implementation = self.root / "fixture_engine.py"
        implementation.write_text(ENGINE)
        self.binary = self.root / "fixture-engine"
        self.binary.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -B " + shlex.quote(str(implementation)) + ' "$@"\n')
        self.binary.chmod(0o755)
        self.settings = {"backend": "cpu", "threads": 1, "device": 0,
                         "binary": str(self.binary), "model_root": str(self.models),
                         "model_file": "fixture-model.gguf", "vae_file": "fixture-vae.gguf"}
        self.settings_patch = patch("run.platform_runtime.settings", side_effect=lambda: dict(self.settings))
        self.settings_patch.start()
        self.store = Store(self.root / "data", self.root / "outputs")
        self.generator = None
        self.admission = None

    def tearDown(self):
        # Release only this fixture's deliberate barrier before cleanup even
        # when an assertion failed, so an implementation bug cannot hang tests.
        (self.root / "release-score-worker").touch()
        if self.generator:
            self.generator.close()
        elif self.admission:
            self.admission.close()
        self.settings_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def recipe(**updates):
        return {"title": "An integration fixture", "lyrics": "", "style": "",
                "mode": "free", "cot": "full", "abc": "", "seed": "17",
                "steps": 2, "max_seconds": 1, "render_mode": "plan", **updates}

    def set_fixture(self, **value):
        (self.models / "fixture.json").write_text(json.dumps(value))

    def new_control(self, command=None):
        class Observation:
            def snapshot(self):
                return {"host": {"available": 16 * 1024 ** 3, "pressure": "normal"}, "devices": {}}
        self.admission = ModelAdmission(self.store.data_root, observer=Observation(), command=command or self.control)
        return self.admission

    def start_generator(self, command=None):
        self.generator = Generator(self.store, admission=self.admission or self.new_control(command))
        return self.generator

    def wait_for(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail("The owned fixture did not reach its expected state before the test deadline")

    def finish(self, job, expected):
        self.wait_for(lambda: self.store.job(job["id"])["status"] in TERMINAL)
        result = self.store.job(job["id"])
        self.assertEqual(result["status"], expected, result["error"])
        return result

    def test_empty_score_launch_receipt_and_real_command_remain_exact(self):
        generator = self.start_generator()
        job = self.finish(generator.submit(self.recipe()), "planned")
        recipe = job["recipe"]
        self.assertEqual(recipe["symbolic_plan"]["token_count"], 0)
        self.assertEqual(recipe["symbolic_plan"]["abc"], "")
        self.assertTrue(recipe["symbolic_plan"]["artifact_id"])
        launch_path = self.store.data_root / "jobs" / job["id"] / "launch.json"
        self.assertEqual(hashlib.sha256(launch_path.read_bytes()).hexdigest(), recipe["launch_receipt_sha256"])
        launch = json.loads(launch_path.read_text())
        self.assertEqual(launch["job_id"], job["id"])
        self.assertEqual(launch["recipe"]["score_source"], "")
        self.assertEqual(launch["recipe_sha256"], hashlib.sha256(json.dumps(launch["recipe"], sort_keys=True).encode()).hexdigest())
        self.assertEqual(launch["contract"]["tokenizer_sha256"], hashlib.sha256(self.vocabulary.read_bytes()).hexdigest())
        self.assertEqual(launch["engine_sha256"], hashlib.sha256(self.binary.read_bytes()).hexdigest())
        self.assertIn("fixture-model.gguf", launch["weight_file_identity"])
        raw = self.store.outputs / "riff" / (job["id"] + ".plan.json")
        artifact = self.store.artifacts.resolve(recipe["symbolic_plan"]["artifact_id"])
        self.assertEqual(Path(artifact["path"]).read_bytes(), raw.read_bytes())
        self.assertNotEqual(Path(artifact["path"]).stat().st_ino, raw.stat().st_ino)
        self.assertEqual(self.store.snapshot()["tracks"], [])

    def seed_interrupted(self, **shape):
        if not self.store.artifacts:
            self.store.artifacts = ScoreArtifacts(self.store, self.admission or self.new_control())
        job_id = uuid.uuid4().hex
        recipe = self.store.artifacts.launch(job_id, validate_recipe(self.recipe(render_mode="performance")))
        with self.store.db() as db:
            db.execute("INSERT INTO jobs(id,title,created,status,recipe) VALUES(?,?,?,?,?)",
                       (job_id, "Interrupted fixture", time.time(), "running", json.dumps(recipe)))
        self.set_fixture(tokens=[0], truncated=True, **shape)
        output = self.store.outputs / "riff" / (job_id + ".wav")
        command = run.build_command(lyrics="", style="", max_seconds=1, steps=2, cot="full", seed=17,
                                    threads=1, output=output, render_mode="performance", semantic_only=True)
        subprocess.run(command, check=True, capture_output=True, timeout=5)
        return job_id, recipe

    def test_recovery_uses_launch_snapshot_and_keeps_complete_stages_independent(self):
        complete, _ = self.seed_interrupted()
        bad_score, _ = self.seed_interrupted(partial_score=True)
        bad_performance, _ = self.seed_interrupted(partial_performance=True)
        malformed = self.store.outputs / "riff" / (bad_score + ".plan.json")
        before = malformed.read_bytes()
        self.vocabulary.write_bytes(base64.b64encode(b"WRONG CURRENT TOKENIZER") + b" 0\n")
        self.start_generator()
        recovered = {identity: self.store.job(identity) for identity in (complete, bad_score, bad_performance)}
        self.assertEqual({row["status"] for row in recovered.values()}, {"interrupted"})
        self.assertEqual(recovered[complete]["recipe"]["symbolic_plan"]["abc"], NOTATION.strip())
        self.assertTrue(recovered[complete]["recipe"]["performance"])
        self.assertTrue(recovered[bad_score]["recipe"]["performance"])
        self.assertIn("score", recovered[bad_score]["recipe"]["artifact_errors"])
        self.assertTrue(recovered[bad_performance]["recipe"]["symbolic_plan"]["artifact_id"])
        self.assertIn("performance", recovered[bad_performance]["recipe"]["artifact_errors"])
        self.assertEqual(malformed.read_bytes(), before)

    def test_changed_launch_receipt_is_rejected_without_losing_saved_performance(self):
        identity, recipe = self.seed_interrupted()
        receipt = self.store.data_root / "jobs" / identity / "launch.json"
        changed = json.loads(receipt.read_text())
        changed["recipe"]["lyrics"] = "These were not the launched words"
        receipt.write_text(json.dumps(changed))
        before = receipt.read_bytes()
        recovered = self.store.capture_artifacts(identity, recipe)
        self.assertNotIn("symbolic_plan", recovered)
        self.assertIn("receipt has changed", recovered["artifact_errors"]["score"])
        self.assertTrue(recovered["performance"])
        self.assertEqual(receipt.read_bytes(), before)

    def test_saved_performance_reuses_input_score_without_claiming_new_plan_output(self):
        generator = self.start_generator()
        source = self.finish(generator.submit(self.recipe(render_mode="performance")), "performed")
        reference = source["recipe"]["symbolic_plan"]["artifact_id"]
        reused = self.finish(generator.submit(self.recipe(render_mode="music", performance_source=source["id"],
                                                          style="قصيدة · 서로의 목소리", cot="melody", steps=7)), "done")
        recipe = reused["recipe"]
        self.assertEqual(recipe["score_source"], reference)
        self.assertEqual(recipe["symbolic_plan"]["artifact_id"], reference)
        self.assertEqual(recipe["symbolic_plan"]["stage"], "reused")
        self.assertEqual((recipe["cot"], recipe["steps"]), ("melody", 7))
        self.assertFalse((self.store.outputs / "riff" / (reused["id"] + ".plan.json")).exists())
        options = json.loads((self.store.outputs / "riff" / (reused["id"] + ".request.json")).read_text())["options"]
        self.assertIn("score_tokens_file", options)
        self.assertNotIn("abc", options)
        self.assertIn("semantic_codes_file", options)
        self.assertEqual(self.store.performance_path(source["id"]).read_bytes(), self.store.performance_path(reused["id"]).read_bytes())

    def test_submit_and_claim_validate_the_same_owned_reference(self):
        generator = self.start_generator()
        source = self.finish(generator.submit(self.recipe()), "planned")
        reference = source["recipe"]["symbolic_plan"]["artifact_id"]
        with self.assertRaises(ValueError):
            generator.submit(self.recipe(score_source="riff-score-v1:" + "f" * 64))
        artifact = self.store.artifacts.resolve(reference)
        with generator.lock:
            queued = generator.submit(self.recipe(score_source=reference, style="A different interpretation", seed="29"))
            Path(artifact["path"]).write_bytes(b'{"tokens":[1]}')
        failed = self.finish(queued, "failed")
        self.assertIn("changed", failed["error"])
        self.assertFalse((self.store.outputs / "riff" / (queued["id"] + ".request.json")).exists())
        self.assertEqual(Path(artifact["path"]).read_bytes(), b'{"tokens":[1]}')

    def test_historical_description_uses_recorded_contract_without_enabling_incompatible_replay(self):
        self.set_fixture(tokens=[0])
        generator = self.start_generator()
        job = self.finish(generator.submit(self.recipe()), "planned")
        reference = job["recipe"]["symbolic_plan"]["artifact_id"]
        self.vocabulary.write_bytes(base64.b64encode(b"Another tokenizer") + b" 0\n")
        described = self.store.artifacts.describe(reference)
        self.assertEqual(described["abc"], NOTATION.strip())
        self.assertFalse(described["compatible"])
        with self.assertRaises(ValueError):
            generator.submit(self.recipe(score_source=reference))
        self.settings["binary"] = str(self.root / "unavailable-engine")
        self.assertEqual(self.store.artifacts.describe(reference)["abc"], NOTATION.strip())

    def test_valid_raw_score_survives_a_display_snapshot_problem(self):
        identity, recipe = self.seed_interrupted()
        launch = json.loads((self.store.data_root / "jobs" / identity / "launch.json").read_text())
        vocabulary = self.store.artifacts.vocabularies / (launch["contract"]["tokenizer_sha256"] + ".tiktoken")
        vocabulary.write_bytes(b"corrupted display snapshot")
        for problem in ("changed", "missing"):
            with self.subTest(snapshot=problem):
                if problem == "missing":
                    vocabulary.rename(vocabulary.with_suffix(".retained"))
                result = self.store.capture_artifacts(identity, recipe)
                plan = result["symbolic_plan"]
                self.assertTrue(plan["artifact_id"])
                self.assertEqual(plan["token_count"], 1)
                self.assertTrue(plan["display_error"])
                self.assertNotIn(str(self.root), json.dumps(plan))
                self.assertTrue(result["performance"])
                exact = self.store.artifacts.resolve(plan["artifact_id"])
                self.assertEqual(json.loads(Path(exact["path"]).read_text())["tokens"], [0])

    def test_source_only_generation_works_without_the_score_control_runtime(self):
        # Retain one previously captured score, as an older source installation
        # can have library data even when its optional control runtime is absent.
        identity, recipe = self.seed_interrupted()
        captured = self.store.capture_artifacts(identity, recipe)
        reference = captured["symbolic_plan"]["artifact_id"]
        self.admission.close()
        self.admission = self.new_control()
        with patch.object(self.admission, "_command", side_effect=SchedulerNotInstalled("Source fixture has no control runtime")):
            generator = self.start_generator()
            job = self.finish(generator.submit(self.recipe(render_mode="music")), "done")
            self.assertTrue(self.admission.serial_fallback)
            self.assertIsNone(self.admission.process)
            self.assertEqual(job["recipe"]["symbolic_plan"]["abc"], NOTATION.strip())
            self.assertNotIn("artifact_id", job["recipe"]["symbolic_plan"])
            self.assertTrue(job["track_id"])
            self.assertFalse((self.store.data_root / "jobs" / job["id"] / "launch.json").exists())
            with self.assertRaisesRegex(ValueError, "saved-score runtime is not installed"):
                generator.submit(self.recipe(score_source=reference))

    def test_writer_and_producer_context_keep_input_and_output_score_choices_separate(self):
        import review_recipe
        import writer
        generator = self.start_generator()
        original = self.finish(generator.submit(self.recipe()), "planned")
        reference = original["recipe"]["symbolic_plan"]["artifact_id"]
        current = self.recipe(score_source=reference, parent_track_id=original["id"], style="An open-ended revision")
        settings = generator.writing_settings(current)
        self.assertEqual(settings["score_source"], reference)
        self.assertEqual(settings["abc"], "")
        self.assertEqual(settings["reference"]["inputs"]["score_source"], "")
        self.assertEqual({item["id"] for item in settings["available_scores"]}, {reference})
        context = writer.writing_context(settings)
        self.assertEqual(context["inputs"]["score_source"], reference)
        self.assertEqual(context["symbolic"]["available_scores"][0]["id"], reference)
        source = {**original["recipe"], "available_scores": settings["available_scores"]}
        proposed = {name: validate_recipe(current)[name] for name in review_recipe.FIELDS}
        proposed.update(lyrics="새로운 말 / كلمات جديدة", steps=13, cot="melody")
        validated = review_recipe.validate_generation(proposed, source)
        self.assertEqual((validated["score_source"], validated["abc"], validated["steps"]), (reference, "", 13))
        self.assertEqual(validated["lyrics"], proposed["lyrics"])

    def test_cancel_during_score_prepare_reaches_the_owned_elixir_worker(self):
        ready, release = self.root / "score-worker-ready", self.root / "release-score-worker"
        # Only this test's prepare task pauses. The real module runs all other
        # operations, and production wire clients cannot select this callback.
        pause = json.dumps(str(ready))
        resume = json.dumps(str(release))
        entry = '''
runner = fn request ->
  if request["action"] == "prepare" do
    File.write!(READY, "waiting")
    wait = fn again ->
      if File.exists?(RELEASE), do: :ok, else: (Process.sleep(10); again.(again))
    end
    wait.(wait)
  end
  case request["action"] do
    "capture" -> Riff.Runtime.ScoreArtifact.capture_file!(request["root"], request["source_root"], request["source_path"], request["contract"], request["provenance"])
    "resolve" -> Riff.Runtime.ScoreArtifact.resolve!(request["root"], request["artifact_id"], request["contract"])
    "prepare" -> Riff.Runtime.ScoreArtifact.prepare!(request["root"], request["artifact_id"], request["contract"], request["input_directory"])
  end
end
Riff.Runtime.SchedulerPort.main(artifact_runner: runner)
'''.replace("READY", pause).replace("RELEASE", resume)
        generator = self.start_generator(self.control[:-1] + [entry])
        source = self.finish(generator.submit(self.recipe()), "planned")
        job = generator.submit(self.recipe(score_source=source["recipe"]["symbolic_plan"]["artifact_id"]))
        self.wait_for(ready.exists)
        pid = self.admission.process.pid
        generator.cancel(job["id"])
        try:
            self.wait_for(lambda: self.store.job(job["id"])["status"] == "cancelled", timeout=2)
            self.assertEqual(self.admission.process.pid, pid)
            self.assertEqual(self.admission._exchange({"op": "status"})["active"], [])
            self.assertFalse((self.store.outputs / "riff" / (job["id"] + ".request.json")).exists())
        finally:
            release.touch()


if __name__ == "__main__":
    unittest.main()
