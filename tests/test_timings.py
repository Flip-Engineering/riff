"""Per-stage timings, warm hits, queue waits and memory in a take's metrics.

``cold-take.log`` and ``warm-take.log`` in tests/fixtures/timings are real engine logs, captured on
2026-10-23 from the pinned audio.cpp CPU engine (fingerprint a97282a0cd11f431) with the pinned YuE2 3B Q4
model, a 6 s study at 4 steps and 4 threads: the first take in a fresh process, and the second take in a
warm engine. Only two path prefixes were replaced, with ``<models>`` and ``<outputs>``. ``decode-only.log``
and ``no-timing.log`` are written by hand to the same line format (no decoder-only run was captured), and
their numbers are illustrative.
"""
import json
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc
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
    def test_real_cold_take_reports_every_stage_the_load_and_the_pin(self):
        summary = summarize(lines("cold-take.log"))
        self.assertEqual(summary["stages"], {"plan": 2.766671647, "performance": 40.64737152,
                                             "sound": 108.166921413, "audio": 32.57509579})
        # AR weights, AR generation weights, NAR weights and VAE weights; the AR prefix line is inside the first.
        self.assertAlmostEqual(summary["load_seconds"], (3405.964867 + 392.00195 + 2144.093341 + 823.56612) / 1000)
        self.assertEqual(summary["engine_session_seconds"], 217.522476581)
        self.assertEqual(summary["acoustic_pin_seconds"], 32.312149477)
        self.assertNotIn("resident_reuse", summary)
        self.assertEqual(summary["parsed_lines"], 112)

    def test_real_warm_take_has_no_load_and_says_it_reused_the_resident_runtime(self):
        summary = summarize(lines("warm-take.log"))
        self.assertNotIn("load_seconds", summary)
        self.assertIs(summary["resident_reuse"], True)
        self.assertEqual(set(summary["stages"]), set(STAGE_KEYS))
        self.assertEqual(summary["stages"]["plan"], 0.029082308)
        self.assertEqual(summary["engine_session_seconds"], 168.200637345)
        self.assertEqual(summary["parsed_lines"], 97)

    def test_the_stages_and_the_pin_account_for_the_engine_session(self):
        for name in ("cold-take.log", "warm-take.log"):
            with self.subTest(name=name):
                summary = summarize(lines(name))
                accounted = sum(summary["stages"].values()) + summary["acoustic_pin_seconds"] + summary.get("load_seconds", 0)
                # Within a few seconds of the engine's own session total (the rest is untimed glue).
                self.assertLess(abs(summary["engine_session_seconds"] - accounted), 12)

    def test_a_stage_that_was_not_logged_is_absent_not_zero(self):
        summary = summarize(lines("decode-only.log"))
        self.assertEqual(summary["stages"], {"audio": 2.8755})
        self.assertEqual(summary["load_seconds"], 0.8235)

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

    def test_the_vae_init_line_is_not_a_load_cost(self):
        self.assertNotIn("load_seconds", summarize(["[TIMING] yue2.vae.init_ms 0.004", "[TIMING] yue2.plan_ms 1"]))

    def test_large_log_summary_does_not_retain_samples_or_distinct_unknown_names(self):
        samples = 100_000
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.log"
            with path.open("w") as log:
                log.write("[TIMING] yue2.ar.resident_reuse 1\n[TIMING] session.wall_ms 1000\n")
                for index in range(samples):
                    log.write(f"[TIMING] unknown.{index} 1\n[TIMING] yue2.nar_ms 1\n")
                log.write("[TIMING] session.wall_ms 9000\n[TIMING] yue2.ar.weights_upload_ms 0\n")
            tracemalloc.start()
            try:
                with path.open() as log:
                    summary = summarize(log)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertEqual(summary, {"parsed_lines": samples * 2 + 4, "stages": {"sound": samples / 1000},
                                      "engine_session_seconds": 9.0, "resident_reuse": True, "load_seconds": 0.0})
            self.assertFalse(warm_record("warm", summary)["hit"])
            # Even a fraction of this multi-megabyte log would exceed the stream's working memory.
            self.assertLess(peak, path.stat().st_size // 32)

    def test_public_parser_still_returns_all_samples_in_order(self):
        parsed, count = parse_timing_lines(iter(["[TIMING] unknown 1", "[TIMING] yue2.nar_ms 2",
                                                "[TIMING] unknown 3", "[TIMING] unknown nan"]))
        self.assertEqual((parsed, count), ({"unknown": [1.0, 3.0], "yue2.nar_ms": [2.0]}, 3))

    def test_warm_hit_needs_a_warm_engine_reuse_line_and_no_load(self):
        cold, warm = summarize(lines("cold-take.log")), summarize(lines("warm-take.log"))
        self.assertEqual(warm_record("warm", cold, 1.5), {"engine": "warm", "hit": False, "prepare_seconds": 1.5})
        self.assertEqual(warm_record("warm", warm), {"engine": "warm", "hit": True})
        self.assertEqual(warm_record("warm", summarize([]))["hit"], None)
        self.assertEqual(warm_record("warm", summarize(["[TIMING] yue2.plan_ms 1"]))["hit"], False)
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
        read_text = Path.read_text
        def without_whole_log_reads(path, *args, **kwargs):
            if path.suffix == ".log":
                raise AssertionError("Take metrics must stream the engine log.")
            return read_text(path, *args, **kwargs)
        with patch.object(Path, "read_text", without_whole_log_reads):
            job_id = generator.submit(recipe())["id"]
            wait_until(lambda: self.store.job(job_id)["status"] in ("done", "failed"))
            self.assertEqual(self.store.job(job_id)["status"], "done")
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
        self.assertIn("prepare_seconds", one["warm"])
        self.assertIn("prepare_seconds", two["warm"])


if __name__ == "__main__":
    unittest.main()
