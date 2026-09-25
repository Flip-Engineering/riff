"""Warm engine protocol, lifecycle and studio integration, against a fake engine."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import platform_support
import run
import studio_core
from studio_core import Generator, Store
from warm_engine import WarmEngine, engine_command, job_from_command
from admission_fixture import FixtureAdmission

FAKE_ENGINE = """#!{python}
import array, json, os, sys, time, wave
from pathlib import Path
state = Path(os.environ["FAKE_ENGINE_STATE"])
(state / ("start-%d" % os.getpid())).write_text(json.dumps(sys.argv[1:]))
print("fake engine startup", flush=True)
print("@@riff-engine-ready riff.jobs.v1", flush=True)
for line in sys.stdin:
    if not line.strip():
        continue
    job = json.loads(line)
    ident, mode = job["id"], job["options"].get("style", "ok")
    with (state / "jobs.jsonl").open("a") as log:
        log.write(json.dumps({"pid": os.getpid(), **job}) + "\\n")
    print("@@riff-job-begin " + ident, flush=True)
    print("[TIMING] yue2.ar.init_ms 1 seed=%s lyrics=%s" % (job.get("seed"), job.get("lyrics")), flush=True)
    if mode == "crash":
        print("about to crash", flush=True)
        os._exit(7)
    if mode == "wait":
        (state / "waiting").write_text(ident)
        time.sleep(300)
    if mode == "error":
        print("audiocpp_cli failed: bad request", flush=True)
        print("@@riff-job-end %s status=error message=bad request" % ident, flush=True)
        continue
    if mode == "fatal":
        print("@@riff-job-end %s status=error fatal=1 message=Yue2 NAR graph compute failed" % ident, flush=True)
        sys.exit(3)
    if mode == "wrong-id":
        print("@@riff-job-end someone-else status=ok", flush=True)
        continue
    print("[TIMING] yue2.nar.progress 0.5", flush=True)
    print("[TIMING] framework.oobleck_audio_vae.decode.total_ms 1", flush=True)
    if job.get("out"):
        with wave.open(job["out"], "wb") as audio:
            audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            audio.writeframes(array.array("h", [1000, -1000] * 4800).tobytes())
    print("@@riff-job-end %s status=ok" % ident, flush=True)
"""


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("The expected engine state did not arrive before the test deadline")


class WarmFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.models = self.root / "models"
        (self.models / "sidecars").mkdir(parents=True)
        for name in ("m.gguf", "v.gguf"):
            (self.models / name).write_bytes(b"weights")
        self.binary = self.root / "audiocpp_cli"
        self.binary.write_text(FAKE_ENGINE.replace("{python}", sys.executable))
        self.binary.chmod(0o755)
        environment = patch.dict(os.environ, {"FAKE_ENGINE_STATE": str(self.state)})
        environment.start()
        self.addCleanup(environment.stop)
        # The engine must never be sent SIGKILL.
        kill = patch.object(subprocess.Popen, "kill", side_effect=AssertionError("SIGKILL sent to the engine"))
        kill.start()
        self.addCleanup(kill.stop)

    def command(self, style="ok", output=None, threads="1", seed="42"):
        command = [str(self.binary), "--task", "gen", "--family", "yue2", "--model", str(self.models),
                   "--backend", "cpu", "--device", "0", "--threads", threads, "--lyrics", "[Verse]\nA line",
                   "--session-option", "yue2.model_gguf=m.gguf", "--session-option", "yue2.vae_gguf=v.gguf",
                   "--request-option", "cot=off", "--request-option", "style=" + style,
                   "--seed", seed, "--out", str(output or self.root / "take.wav"), "--log", "--metrics"]
        return command

    def starts(self):
        return sorted(self.state.glob("start-*"))


class CommandTranslationTests(WarmFixture):
    def test_generation_command_becomes_session_and_job(self):
        session, job = job_from_command(self.command(seed=str(2 ** 63 - 1)))
        self.assertEqual(session["--threads"], "1")
        self.assertEqual(session["session_options"], ["yue2.model_gguf=m.gguf", "yue2.vae_gguf=v.gguf"])
        self.assertEqual(job, {"options": {"cot": "off", "style": "ok"}, "lyrics": "[Verse]\nA line",
                               "seed": str(2 ** 63 - 1), "out": str(self.root / "take.wav")})
        command = engine_command(session)
        self.assertEqual(command[-6:], ["--session-option", "yue2.keep_resident=true", "--log", "--metrics", "--jobs", "-"])
        self.assertNotIn("--lyrics", command)

    def test_real_build_command_round_trips(self):
        (self.models / "sidecars/yue2-vae-config.json").write_text("{}")
        settings = {"binary": str(self.binary), "model_root": str(self.models), "backend": "cpu", "device": 0,
                    "threads": 2, "model_file": "m.gguf", "vae_file": "v.gguf", "warm_engine": True}
        with patch.object(run.platform_runtime, "settings", return_value=settings):
            command = run.build_command(lyrics="words", style="folk", max_seconds=10, steps=8, cot="melody", seed=7,
                                        threads=2, output=self.root / "a.wav", abc="X:1\nK:C\nC", cfg_scale=1.5,
                                        refinement={}, render_mode="music", solver="ab2")
        session, job = job_from_command(command)
        self.assertEqual(job["options"]["abc"], "X:1\nK:C\nC")
        self.assertEqual(job["options"]["score_tokens_out"], str(self.root / "a.plan.json"))
        self.assertEqual(job["options"]["semantic_codes_out"], str(self.root / "a.codes.i32"))
        self.assertEqual(job["options"]["ode_method"], "ab2")
        self.assertEqual((job["seed"], job["lyrics"]), ("7", "words"))

    def test_unsupported_commands_keep_the_process_path(self):
        decode = [str(self.binary), "--task", "gen", "--family", "yue2", "--model", str(self.models), "--backend", "cpu",
                  "--device", "0", "--threads", "1", "--session-option", "yue2.vae_gguf=v.gguf",
                  "--request-option", "acoustic_latents_file=/x.yac", "--out", "/x.wav", "--log", "--metrics"]
        self.assertIsNone(job_from_command(decode))
        self.assertIsNone(job_from_command(self.command() + ["--text", "unknown flag"]))
        self.assertIsNone(job_from_command(self.command() + ["--request-option", "style=twice"]))
        self.assertIsNone(job_from_command(self.command() + ["--session-option", "yue2.keep_resident=false"]))
        self.assertIsNone(job_from_command([sys.executable, "-c", "print(1)"]))
        self.assertIsNone(job_from_command([]))


class WarmEngineTests(WarmFixture):
    def setUp(self):
        super().setUp()
        self.engine = WarmEngine(self.root / "warm-engine.log")
        self.addCleanup(self.engine.close)

    def take(self, style="ok", **kwargs):
        session, job = job_from_command(self.command(style, **kwargs))
        self.engine.prepare(session)
        log = self.root / f"job-{len(list(self.root.glob('job-*.log')))}.log"
        log.write_text("")
        return self.engine.submit(job, log), log

    def test_consecutive_takes_reuse_one_engine_and_logs_are_separated(self):
        first, first_log = self.take(seed="1")
        self.assertEqual(first.wait(10), 0)
        second, second_log = self.take(seed="2")
        self.assertEqual(second.wait(10), 0)
        self.assertEqual(first.pid, second.pid)
        self.assertEqual(len(self.starts()), 1)
        arguments = json.loads(self.starts()[0].read_text())
        self.assertIn("yue2.keep_resident=true", arguments)
        self.assertEqual(arguments[-2:], ["--jobs", "-"])
        first_text, second_text = first_log.read_text(), second_log.read_text()
        self.assertIn("seed=1", first_text)
        self.assertNotIn("seed=2", first_text)
        self.assertIn("seed=2", second_text)
        self.assertIn("yue2.nar.progress 0.5", second_text)
        self.assertNotIn("@@riff", first_text + second_text)
        # Engine start-up output belongs to the take that started it, and to the engine log.
        self.assertIn("fake engine startup", first_text)
        self.assertNotIn("fake engine startup", second_text)
        self.assertIn("fake engine startup", (self.root / "warm-engine.log").read_text())
        jobs = [json.loads(line) for line in (self.state / "jobs.jsonl").read_text().splitlines()]
        self.assertEqual([job["seed"] for job in jobs], ["1", "2"])
        self.assertTrue(all(job["metrics"] is True and job["id"] for job in jobs))

    def test_job_error_fails_only_that_take(self):
        failed, log = self.take("error")
        self.assertEqual(failed.wait(10), 1)
        self.assertEqual(failed.message, "bad request")
        self.assertIn("audiocpp_cli failed: bad request", log.read_text())
        following, _ = self.take()
        self.assertEqual(following.wait(10), 0)
        self.assertEqual(failed.pid, following.pid)

    def test_crash_mid_take_fails_it_and_the_next_take_restarts_the_engine(self):
        crashed, log = self.take("crash")
        self.assertEqual(crashed.wait(10), 7)
        self.assertIn("about to crash", log.read_text())
        following, _ = self.take()
        self.assertEqual(following.wait(10), 0)
        self.assertNotEqual(crashed.pid, following.pid)
        self.assertEqual(len(self.starts()), 2)

    def test_cancel_sends_sigterm_and_the_next_take_restarts_the_engine(self):
        waiting, _ = self.take("wait")
        wait_until(lambda: (self.state / "waiting").exists())
        waiting.terminate()
        self.assertEqual(waiting.wait(10), -signal.SIGTERM)
        self.assertFalse(hasattr(waiting, "kill"))
        following, _ = self.take()
        self.assertEqual(following.wait(10), 0)
        self.assertNotEqual(waiting.pid, following.pid)

    def test_backend_fatal_error_retires_the_engine(self):
        fatal, _ = self.take("fatal")
        self.assertEqual(fatal.wait(10), 1)
        following, _ = self.take()
        self.assertEqual(following.wait(10), 0)
        self.assertNotEqual(fatal.pid, following.pid)

    def test_unexpected_marker_fails_the_take_and_stops_the_engine(self):
        broken, log = self.take("wrong-id")
        self.assertEqual(broken.wait(10), 1)
        self.assertIn("protocol error", log.read_text())
        following, _ = self.take()
        self.assertEqual(following.wait(10), 0)
        self.assertNotEqual(broken.pid, following.pid)

    def test_setting_or_model_file_change_restarts_the_engine(self):
        first, _ = self.take()
        first.wait(10)
        changed_threads, _ = self.take(threads="2")
        changed_threads.wait(10)
        self.assertNotEqual(first.pid, changed_threads.pid)
        same, _ = self.take(threads="2")
        same.wait(10)
        self.assertEqual(changed_threads.pid, same.pid)
        model = self.models / "m.gguf"
        model.write_bytes(b"new weights")
        os.utime(model, ns=(time.time_ns(), time.time_ns() + 10 ** 9))
        replaced, _ = self.take(threads="2")
        self.assertEqual(replaced.wait(10), 0)
        self.assertNotEqual(same.pid, replaced.pid)
        self.assertEqual(len(self.starts()), 3)

    def test_idle_engine_stops_on_eof_without_signals(self):
        take, _ = self.take()
        take.wait(10)
        process = self.engine.process
        with patch.object(subprocess.Popen, "send_signal", side_effect=AssertionError("idle stop should use EOF")):
            self.engine.stop()
        self.assertEqual(process.returncode, 0)
        self.assertFalse(self.engine.running())


class GeneratorWarmTests(WarmFixture):
    def setUp(self):
        super().setUp()
        self.store = Store(self.root / "data", self.root / "outputs")
        self.admission = FixtureAdmission()
        admission = patch("studio_core.ModelAdmission", return_value=self.admission)
        admission.start()
        self.addCleanup(admission.stop)
        warm = patch.object(Generator, "warm_plan", staticmethod(lambda command: (True, job_from_command(command))))
        warm.start()
        self.addCleanup(warm.stop)
        self.generator = Generator(self.store, lambda recipe, output: self.command(recipe["style"], output))
        self.addCleanup(self.generator.close)

    def status(self, job_id):
        with self.store.db() as db:
            return db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0]

    def submit(self, style):
        return self.generator.submit({"title": "Warm", "lyrics": "A line", "style": style, "max_seconds": 10,
                                      "steps": 8, "seed": "42", "cot": "off", "abc": ""})["id"]

    def test_takes_share_the_engine_and_keep_logs_metrics_and_tracks(self):
        first = self.submit("ok")
        wait_until(lambda: self.status(first) == "done")
        second = self.submit("ok")
        wait_until(lambda: self.status(second) == "done")
        self.assertEqual(len(self.starts()), 1)
        log = (self.store.data_root / f"{second}.log").read_text()
        self.assertIn("yue2.nar.progress 0.5", log)
        metrics = self.store.track(second)["metrics"]
        self.assertEqual((metrics["engine"], metrics["exit_code"]), ("warm", 0))
        self.assertEqual(metrics["command"][0], str(self.binary))
        self.assertEqual(len(self.store.snapshot()["tracks"]), 2)

    def test_engine_error_marks_the_take_failed_and_the_queue_continues(self):
        failed = self.submit("error")
        wait_until(lambda: self.status(failed) == "failed")
        following = self.submit("ok")
        wait_until(lambda: self.status(following) == "done")
        self.assertEqual(len(self.starts()), 1)

    def test_cancel_stops_the_engine_and_the_next_take_restarts_it(self):
        active = self.submit("wait")
        wait_until(lambda: (self.state / "waiting").exists())
        self.assertEqual(self.generator.cancel(active), "cancelling")
        wait_until(lambda: self.status(active) == "cancelled")
        following = self.submit("ok")
        wait_until(lambda: self.status(following) == "done")
        self.assertEqual(len(self.starts()), 2)

    def test_disabling_warm_mode_stops_the_idle_engine(self):
        first = self.submit("ok")
        wait_until(lambda: self.status(first) == "done")
        self.assertTrue(self.generator.warm.running())
        fallback = [sys.executable, "-c", "import sys, shutil; shutil.copy(sys.argv[1], sys.argv[2])",
                    str(self.store.outputs / "riff" / f"{first}.wav")]
        self.generator.command_builder = lambda recipe, output: fallback + [str(output)]
        with patch.object(Generator, "warm_plan", staticmethod(lambda command: (False, None))):
            second = self.submit("ok")
            wait_until(lambda: self.status(second) == "done")
        self.assertFalse(self.generator.warm.running())
        self.assertEqual(self.store.track(second)["metrics"]["engine"], "process")


class WarmSettingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        data = patch.object(platform_support, "DATA", Path(self.temp.name))
        data.start()
        self.addCleanup(data.stop)

    def test_warm_engine_defaults_off_is_validated_and_persists(self):
        self.assertIs(platform_support.settings()["warm_engine"], False)
        for value in ("true", 1, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Warm engine"):
                platform_support.configure({"warm_engine": value})
        platform_support.configure({"warm_engine": True, "threads": 3})
        self.assertIs(platform_support.settings()["warm_engine"], True)
        # Saving other engine fields keeps the setting; unknown keys are still rejected.
        platform_support.configure({"backend": "cpu"})
        self.assertIs(platform_support.settings()["warm_engine"], True)
        with self.assertRaisesRegex(ValueError, "Unknown engine setting"):
            platform_support.configure({"warm": True})

    def test_capability_requires_job_mode_metadata(self):
        root = Path(self.temp.name)
        for name, lines in (("new", "feature.yue2.keep_resident=1\nformat.yue2.jobs=riff.jobs.v1\n"), ("old", "")):
            binary = root / name
            binary.write_text(f"#!{sys.executable}\nprint('''{lines}feature.yue2.score_tokens=1''')\n")
            binary.chmod(0o755)
            capability = run.engine_capabilities({"binary": str(binary), "model_root": str(root)})
            self.assertEqual(capability["warm_engine"], name == "new")


if __name__ == "__main__":
    unittest.main()
