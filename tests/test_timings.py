"""Per-stage timings, warm hits, queue waits and memory in a take's metrics.

The log fixtures in tests/fixtures/timings follow the exact line format and key names in the pinned
audio.cpp source (``timing_log_scalar`` in src/framework/debug/trace.cpp, and the keys in
src/models/yue2/pipeline.cpp, ar_runtime.cpp, nar_runtime.cpp and session.cpp). They are written to
match that source, not captured from a model run; no YuE2 weights are installed on the test host.
Their numbers are illustrative.
"""
import json
from pathlib import Path
import sys
import time
import unittest

from studio_core import Generator, Store
from timings import STAGE_KEYS, parse_timing_lines, summarize, warm_record
from test_studio import StudioFixture, recipe, wait_until
from unittest.mock import patch

from admission_fixture import FixtureAdmission
from test_warm_engine import WarmFixture
from warm_engine import job_from_command

FIXTURES = Path(__file__).parent / "fixtures" / "timings"


def lines(name):
    return (FIXTURES / name).read_text().splitlines()


class ParserTests(unittest.TestCase):
    def test_cold_take_reports_every_stage_and_the_weight_load(self):
        summary = summarize(lines("cold-take.log"))
        self.assertEqual(summary["stages"], {"plan": 2.2505, "performance": 14.90025, "sound": 6.12, "audio": 1.98075})
        self.assertAlmostEqual(summary["load_seconds"], (8470.2 + 3879 + 1349.1) / 1000)
        self.assertEqual(summary["engine_session_seconds"], 25.715)
        # Non-timing lines, trace lines and a non-numeric timing value are ignored.
        self.assertEqual(summary["parsed_lines"], 11)

    def test_warm_take_has_no_load_lines_and_stages_are_not_invented(self):
        summary = summarize(lines("warm-take.log"))
        self.assertNotIn("load_seconds", summary)
        self.assertEqual(set(summary["stages"]), set(STAGE_KEYS))
        self.assertEqual(summary["engine_session_seconds"], 9.725)

    def test_a_stage_that_was_not_logged_is_absent_not_zero(self):
        summary = summarize(lines("decode-only.log"))
        self.assertEqual(summary["stages"], {"audio": 2.8755})
        self.assertEqual(summary["load_seconds"], 1.21)

    def test_a_log_with_no_timing_lines_gives_an_empty_summary(self):
        self.assertEqual(summarize(lines("no-timing.log")), {"parsed_lines": 0, "stages": {}})
        self.assertEqual(summarize([]), {"parsed_lines": 0, "stages": {}})

    def test_both_line_forms_and_hostile_values_are_handled(self):
        parsed, count = parse_timing_lines([
            "[TIMING ts=20261024-100000] yue2.plan_ms 10", "[TIMING] yue2.plan_ms 20.5", "[TIMING ts=x] yue2.plan_ms nan",
            "[TIMING ts=x] yue2.plan_ms inf", "[TIMING ts=x] yue2.plan_ms", "TIMING yue2.plan_ms 5",
            "prefix text [TIMING ts=1] yue2.nar_ms 7"])
        self.assertEqual(parsed, {"yue2.plan_ms": [10.0, 20.5], "yue2.nar_ms": [7.0]})
        self.assertEqual(count, 3)

    def test_repeated_stage_lines_add_up_and_the_session_total_is_the_last(self):
        summary = summarize(["[TIMING] yue2.nar_ms 1000", "[TIMING] yue2.nar_ms 500",
                             "[TIMING] session.wall_ms 4000", "[TIMING] session.wall_ms 9000"])
        self.assertEqual(summary["stages"], {"sound": 1.5})
        self.assertEqual(summary["engine_session_seconds"], 9.0)

    def test_warm_hit_needs_a_warm_engine_timing_lines_and_no_load(self):
        cold, warm = summarize(lines("cold-take.log")), summarize(lines("warm-take.log"))
        self.assertEqual(warm_record("warm", cold, 1.5), {"engine": "warm", "hit": False, "prepare_seconds": 1.5})
        self.assertEqual(warm_record("warm", warm), {"engine": "warm", "hit": True})
        self.assertEqual(warm_record("warm", summarize([]))["hit"], None)
        self.assertEqual(warm_record("process", warm), {"engine": "process", "hit": False})


class ProcessTakeTests(StudioFixture):
    def command(self, settings, output):
        script = """
import array, sys, time, wave
print("[TIMING ts=20261024-100000] yue2.ar.weights_upload_ms 4000", flush=True)
print("[TIMING ts=20261024-100001] yue2.plan_ms 1000", flush=True)
print("[TIMING ts=20261024-100002] yue2.semantic_ms 2000", flush=True)
print("[TIMING ts=20261024-100003] yue2.nar_ms 3000", flush=True)
print("[TIMING ts=20261024-100004] yue2.vae_decode_ms 500", flush=True)
time.sleep(.1)
with wave.open(sys.argv[1], "wb") as wav:
    wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
    wav.writeframes(array.array("h", [1000, -1000] * 4800).tobytes())
"""
        return [sys.executable, "-u", "-c", script, str(output)]

    def test_a_finished_take_records_timings_queue_and_memory_in_both_places(self):
        generator = Generator(self.store, self.command)
        self.addCleanup(generator.close)
        job_id = generator.submit(recipe())["id"]
        wait_until(lambda: self.store.job(job_id)["status"] == "done")
        saved = json.loads((self.store.outputs / "riff" / f"{job_id}.metrics.json").read_text())
        stored = self.store.track(job_id)["metrics"]
        for metrics in (saved, stored):
            self.assertEqual(metrics["timings"]["stages"], {"plan": 1.0, "performance": 2.0, "sound": 3.0, "audio": 0.5})
            self.assertEqual(metrics["timings"]["load_seconds"], 4.0)
            self.assertEqual(metrics["warm"], {"engine": "process", "hit": False})
            self.assertGreaterEqual(metrics["queue"]["waited_seconds"], 0)
            self.assertGreaterEqual(metrics["queue"]["admission_wait_seconds"], 0)
            self.assertEqual(metrics["memory"]["observed_peak_bytes"], metrics["peak_process_memory_bytes"])
            # Existing fields are unchanged.
            self.assertEqual((metrics["engine"], metrics["exit_code"]), ("process", 0))

    def test_waits_for_admission_are_measured_with_their_last_reason(self):
        generator = Generator(self.store, self.command)
        self.addCleanup(generator.close)
        self.admission.gates["native"].clear()
        job_id = generator.submit(recipe())["id"]
        wait_until(lambda: (generator.status() or {}).get("stage") == "Waiting for memory")
        time.sleep(.3)
        self.admission.gates["native"].set()
        wait_until(lambda: self.store.job(job_id)["status"] == "done")
        queue = self.store.track(job_id)["metrics"]["queue"]
        self.assertGreaterEqual(queue["admission_wait_seconds"], .3)
        self.assertEqual(queue["last_wait_reason"], "Waiting for memory")

    def test_a_failed_take_still_writes_what_was_measured(self):
        def failing(settings, output):
            return [sys.executable, "-c", "print('[TIMING] yue2.plan_ms 250', flush=True); raise SystemExit(5)"]
        generator = Generator(self.store, failing)
        self.addCleanup(generator.close)
        job_id = generator.submit(recipe())["id"]
        wait_until(lambda: self.store.job(job_id)["status"] == "failed")
        saved = json.loads((self.store.outputs / "riff" / f"{job_id}.metrics.json").read_text())
        self.assertEqual(saved["timings"]["stages"], {"plan": 0.25})
        self.assertEqual(saved["exit_code"], 5)


class WarmTakeTests(WarmFixture):
    def setUp(self):
        super().setUp()
        self.store = Store(self.root / "data", self.root / "outputs")
        admission = patch("studio_core.ModelAdmission", return_value=FixtureAdmission())
        admission.start()
        self.addCleanup(admission.stop)
        warm = patch.object(Generator, "warm_plan", staticmethod(lambda command: (True, job_from_command(command))))
        warm.start()
        self.addCleanup(warm.stop)
        self.generator = Generator(self.store, lambda recipe, output: self.command(recipe["style"], output))
        self.addCleanup(self.generator.close)

    def status(self, job_id):
        return self.store.job(job_id)["status"]

    def submit(self, style):
        return self.generator.submit({"title": "Warm", "lyrics": "A line", "style": style, "max_seconds": 10,
                                      "steps": 8, "seed": "42", "cot": "off", "abc": ""})["id"]

    def test_the_first_take_loads_weights_and_the_next_reuses_them(self):
        first = self.submit("ok")
        wait_until(lambda: self.status(first) == "done")
        second = self.submit("ok")
        wait_until(lambda: self.status(second) == "done")
        one, two = (self.store.track(job)["metrics"] for job in (first, second))
        self.assertEqual(one["warm"]["engine"], "warm")
        self.assertIs(one["warm"]["hit"], False)
        self.assertEqual(one["timings"]["load_seconds"], 0.25)
        self.assertEqual(two["warm"]["engine"], "warm")
        self.assertIs(two["warm"]["hit"], True)
        self.assertNotIn("load_seconds", two["timings"])
        self.assertEqual(two["timings"]["stages"], {"sound": 3.0, "audio": 0.5})
        self.assertIn("prepare_seconds", two["warm"])
        self.assertLess(two["warm"]["prepare_seconds"], one["warm"]["prepare_seconds"])


if __name__ == "__main__":
    unittest.main()
