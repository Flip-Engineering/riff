import sys
import array
import http.client
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import wave
from unittest.mock import patch

from studio import StudioServer, byte_range, studio_identity
from studio_core import CONTEXT, Generator, Store, observe_generation, validate_recipe
from admission_fixture import FixtureAdmission


def fixture_audio(path):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(array.array("h", [1000, -1000] * 4800).tobytes())


def recipe(**updates):
    return dict({"title": "A test take", "lyrics": "[Verse]\nA little light across the bay.",
                 "style": "warm folk", "max_seconds": 10, "steps": 8, "seed": "42", "cot": "off", "abc": ""}, **updates)


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError("The expected process state did not arrive before the test deadline")


class RecipeTests(unittest.TestCase):
    def test_invalid_recipe_does_not_reach_engine(self):
        for values in ({"max_seconds": float("nan")}, {"max_seconds": 0},
                       {"seed": "-1"}, {"seed": str(2 ** 63)}, {"cot": "unknown"},
                       {"abc": "X:1\nK:C\nC D E"}, {"style": ["not text"]},
                       {"steps": 0}, {"steps": 8.5}, {"mode": "unknown"}, {"cfg_scale": -1},
                       {"seed": True}, {"seed": 1.5}, {"max_seconds": True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_recipe(recipe(**values))

    def test_score_and_seed_are_preserved(self):
        value = validate_recipe(recipe(cot="melody", abc="X:1\nK:C\nC D E", seed=str(2 ** 63 - 1)))
        self.assertEqual(value["seed"], str(2 ** 63 - 1))
        self.assertIn("C D E", value["abc"])

    def test_open_conditioning_and_custom_settings_pass_through(self):
        value = validate_recipe({"mode": "free", "steps": 19, "max_seconds": 400,
                                 "cfg_scale": 1.2, "temperature": 1.7})
        self.assertEqual((value["lyrics"], value["style"], value["steps"]), ("", "", 19))
        self.assertEqual(value["max_seconds"], 400)
        custom = "instrumental jazz with choir-like texture and irregular phrasing"
        value = validate_recipe(recipe(mode="instrumental", style=custom))
        self.assertEqual(value["style"], custom)

    def test_surprise_recipe_defers_ai_and_resolves_explicit_phrase_choice(self):
        ai = validate_recipe({"mode": "surprise", "brief": "A satellite misses the sea"})
        self.assertEqual(ai["lyrics_source"], "pending")
        self.assertEqual(ai["lyrics"], "")
        phrases = validate_recipe({"mode": "surprise", "idea_engine": "phrases", "seed": "123"})
        self.assertTrue(phrases["lyrics"])
        self.assertNotIn("[Verse]", phrases["lyrics"])


class ProgressTests(unittest.TestCase):
    def test_progress_is_measured_monotonic_and_local_to_its_stage(self):
        live = {"stage_index": 0}
        observe_generation(live, "yue2.ar.prefill.tokens 100\n")
        self.assertNotIn("stage_progress", live)
        observe_generation(live, "yue2.nar.init_ms 20\n")
        self.assertNotIn("stage_progress", live)
        observe_generation(live, "yue2.nar.progress 0.375\nyue2.nar.progress 0.5\n")
        self.assertEqual(live["stage_progress"], .5)
        observe_generation(live, "yue2.nar.progress -1\nyue2.nar.progress 2\nyue2.nar.progress 1e309\nyue2.nar.progress 0.25\n")
        self.assertEqual(live["stage_progress"], .5)
        observe_generation(live, "oobleck_audio_vae decode\n")
        self.assertEqual(live["stage"], "Rendering stereo audio")
        self.assertNotIn("stage_progress", live)


class StudioFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "data", self.root / "outputs")
        self.admission = FixtureAdmission()
        admission = patch("studio_core.ModelAdmission", return_value=self.admission)
        admission.start()
        self.addCleanup(admission.stop)
        self.audio = self.store.outputs / "take.wav"
        fixture_audio(self.audio)

    def tearDown(self):
        self.temp.cleanup()


class StoreTests(StudioFixture):
    def test_queue_migration_retains_original_timestamps_and_recipes(self):
        original = json.dumps(recipe())
        with self.store.db() as db:
            db.execute("DROP TABLE jobs")
            db.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY,title TEXT,created REAL,status TEXT,recipe TEXT,error TEXT,started REAL,finished REAL,track_id TEXT)")
            db.executemany("INSERT INTO jobs(id,title,created,status,recipe) VALUES(?,?,?,?,?)",
                           [("b", "Later", 20.5, "queued", original), ("a", "Earlier", 10.25, "queued", original)])
        restored = Store(self.root / "data", self.root / "outputs")
        with restored.db() as db:
            rows = db.execute("SELECT id,created,recipe,queue_position FROM jobs ORDER BY queue_position").fetchall()
        self.assertEqual([tuple(row) for row in rows], [("a", 10.25, original, 1), ("b", 20.5, original, 2)])

    def test_saved_sounds_crud_does_not_reseed_removed_defaults(self):
        sound = self.store.add_preset({"name": "A sound", "style": "piano"})
        self.store.update_preset(sound["id"], {"name": "Changed", "style": "cellos"})
        self.assertEqual(self.store.preset(sound["id"])["style"], "cellos")
        self.store.delete_preset(sound["id"])
        self.store.delete_preset("warm-current")
        reloaded = Store(self.root / "data", self.root / "outputs")
        with self.assertRaises(KeyError): reloaded.preset(sound["id"])
        with self.assertRaises(KeyError): reloaded.preset("warm-current")

    def test_notes_favorites_archive_and_recipe_survive_restart(self):
        track_id = self.store.add_track(self.audio, recipe(), {})
        self.store.update_track(track_id, {"favorite": True, "archived": True, "notes": "Keep the vocal", "title": "Kept"})
        reloaded = Store(self.root / "data", self.root / "outputs")
        track = reloaded.track(track_id)
        self.assertEqual((track["title"], track["notes"], track["favorite"], track["archived"]), ("Kept", "Keep the vocal", 1, 1))
        self.assertEqual(track["recipe"]["seed"], "42")
        self.assertEqual(track["audio"]["sample_rate"], 48000)
        self.assertTrue(any(track["audio"]["waveform"]))
        reloaded.update_track(track_id, {"archived": False})
        self.assertTrue(reloaded.audio_path(track_id).exists())

    def test_library_cannot_serve_external_files_or_symlinks(self):
        external = self.root / "external.wav"
        fixture_audio(external)
        linked = self.store.outputs / "linked.wav"
        linked.symlink_to(external)
        with self.assertRaises(ValueError):
            self.store.add_track(linked, recipe(), {})
        track_id = self.store.add_track(self.audio, recipe(), {})
        with self.store.db() as db:
            db.execute("UPDATE tracks SET file='../external.wav' WHERE id=?", (track_id,))
        with self.assertRaises(ValueError):
            self.store.audio_path(track_id)

    def test_existing_import_is_idempotent_and_skips_failed_jobs(self):
        metrics = {"exit_code": 0, "command": ["audiocpp_cli", "--lyrics", "Original lyrics", "--seed", "77", "--request-option", "style=Jazz", "--request-option", "semantic_max_tokens=500"]}
        self.audio.with_suffix(".metrics.json").write_text(json.dumps(metrics))
        failed = self.store.outputs / "failed.wav"
        fixture_audio(failed)
        failed.with_suffix(".metrics.json").write_text('{"exit_code":1}')
        self.store.import_existing()
        self.store.import_existing()
        tracks = self.store.snapshot()["tracks"]
        self.assertEqual(len(tracks), 1)
        self.assertEqual(self.store.track(tracks[0]["id"])["recipe"]["lyrics"], "Original lyrics")


class GeneratorTests(StudioFixture):
    def setUp(self):
        super().setUp()
        self.generator = None

    def tearDown(self):
        if self.generator:
            self.generator.close()
        super().tearDown()

    def fake_command(self, settings, output):
        script = """
import array, os, sys, time, wave
from pathlib import Path
mode, target, marker = sys.argv[1:]
if mode == 'fail': sys.exit(23)
if mode == 'wait':
    import signal
    def finish(sig, frame):
        Path(marker).unlink(missing_ok=True)
        sys.exit(0)
    signal.signal(signal.SIGTERM, finish)
fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
os.close(fd)
if mode == 'wait':
    time.sleep(300)
time.sleep(.1)
with wave.open(target, 'wb') as wav:
    wav.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
    wav.writeframes(array.array('h', [1000, -1000] * 4800).tobytes())
Path(marker).unlink()
"""
        return [sys.executable, "-u", "-c", script, settings["style"], str(output), str(self.root / "exclusive-worker")]

    def status(self, job_id):
        with self.store.db() as db:
            return db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0]

    def test_queue_reordering_preserves_active_take_and_timestamps_then_runs_in_order(self):
        performed = []
        def command(settings, output):
            performed.append(settings["title"])
            return self.fake_command(settings, output)
        self.generator = Generator(self.store, command)
        active = self.generator.submit(recipe(title="Active", style="wait"))["id"]
        wait_until(lambda: (self.root / "exclusive-worker").exists())
        pid = self.generator.process.pid
        first, second, third = [self.generator.submit(recipe(title=name))["id"] for name in ("First", "Second", "Third")]
        created = {job["id"]: job["created"] for job in self.store.snapshot()["jobs"]}
        self.assertEqual(self.generator.move_queued(third, "first")["order"], [third, first, second])
        self.assertEqual(self.generator.move_queued(third, "down")["order"], [first, third, second])
        self.assertEqual(self.generator.move_queued(second, "up")["order"], [first, second, third])
        self.generator.move_queued(third, "first")
        with self.assertRaises(ValueError): self.generator.move_queued(active, "up")
        self.assertEqual(self.generator.process.pid, pid)
        restored = Store(self.root / "data", self.root / "outputs").snapshot()["jobs"]
        self.assertEqual({job["id"]: job["created"] for job in restored}, created)
        self.assertEqual([job["id"] for job in sorted(restored, key=lambda j: j["queue_position"]) if job["status"] == "queued"], [third, first, second])
        fourth = self.generator.submit(recipe(title="Fourth"))["id"]
        self.generator.cancel(active)
        wait_until(lambda: self.status(fourth) == "done")
        self.assertEqual(performed, ["Active", "Third", "First", "Second", "Fourth"])

    def test_temporary_storage_failure_does_not_kill_worker_or_repeat_generation(self):
        self.generator = Generator(self.store, self.fake_command)
        active = self.generator.submit(recipe(style="wait"))["id"]
        wait_until(lambda: (self.root / "exclusive-worker").exists())
        next_job = self.generator.submit(recipe())["id"]
        original_db = self.store.db
        storage_available = threading.Event()
        @contextmanager
        def unreliable_db():
            if threading.current_thread() is self.generator.thread and not storage_available.is_set():
                raise sqlite3.OperationalError("database or disk is full")
            with original_db() as db: yield db
        with patch.object(self.store, "db", unreliable_db):
            self.generator.cancel(active)
            wait_until(lambda: (self.generator.status() or {}).get("stage") == "Waiting for storage")
            self.assertTrue(self.generator.thread.is_alive())
            self.assertIsNone(self.generator.process)
            storage_available.set()
            wait_until(lambda: self.status(next_job) == "done")
        self.assertEqual(self.status(active), "cancelled")
        self.assertEqual(len(self.store.snapshot()["tracks"]), 1)
        self.assertTrue(self.generator.thread.is_alive())

    def fake_writer(self, output):
        script = """
import json, os, signal, sys, time
from pathlib import Path
output, marker = map(Path, sys.argv[1:])
settings = json.load(sys.stdin)
os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
def stop(sig, frame):
    marker.unlink(missing_ok=True)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
if settings.get('brief') == 'wait': time.sleep(300)
result = {'title': 'From the writer', 'lyrics': 'Two moons talk over tea.', 'style': 'warm folk', 'writer_model': 'test'}
if settings.get('brief') == 'full recipe fixture':
    from review_recipe import FIELDS
    result['generation'] = {key: settings[key] for key in FIELDS}
    result['generation'].update(title=result['title'], lyrics=result['lyrics'], style='Prepared piano and oud',
        cot='full', abc='X:1\\nM:7/8\\nL:1/8\\nK:Dm\\nD2 F A2 G2|', steps=23, solver='ab2',
        cfg_scale=1.7, temperature=.8, refinement={'abc_temperature':.4, 'semantic_top_p':.72})
output.write_text(json.dumps(result))
marker.unlink()
"""
        return [sys.executable, "-u", "-c", script, str(output), str(self.root / "writer-worker")]

    def writing_generator(self):
        self.generator = Generator(self.store, self.fake_command)
        self.generator.writer_command = self.fake_writer
        self.generator.writer_ready = lambda: True
        return self.generator

    def test_ai_draft_is_saved_and_writer_exits_before_music_starts(self):
        generator = self.writing_generator()
        job = generator.submit({"mode": "surprise", "seed": "42"})["id"]
        wait_until(lambda: self.status(job) == "done")
        track = self.store.track(job)
        self.assertEqual(track["title"], "From the writer")
        self.assertEqual(track["recipe"]["lyrics"], "Two moons talk over tea.")
        self.assertEqual(track["recipe"]["lyrics_source"], "ai")
        self.assertEqual(track["recipe"]["writer_model"], "test")
        self.assertIsNone(generator.writer_process)
        self.assertFalse((self.root / "exclusive-worker").exists())

    def test_automatic_writer_applies_its_complete_recipe_before_generation(self):
        generator = self.writing_generator()
        captured = []
        def command(recipe, output):
            captured.append(recipe)
            return self.fake_command(recipe, output)
        generator.command_builder = command
        job = generator.submit({"mode": "surprise", "brief": "full recipe fixture", "seed": "42", "max_seconds": 12})["id"]
        wait_until(lambda: self.status(job) == "done")
        actual = self.store.track(job)["recipe"]
        for key, expected in {"style": "Prepared piano and oud", "cot": "full", "steps": 23, "solver": "ab2",
                              "cfg_scale": 1.7, "temperature": .8, "max_seconds": 12,
                              "refinement": {"abc_temperature": .4, "semantic_top_p": .72}}.items():
            self.assertEqual(actual[key], expected)
            self.assertEqual(captured[0][key], expected)
        self.assertIn("M:7/8", captured[0]["abc"])
        self.assertEqual(actual["writer_model"], "test")

    def test_cancel_during_automatic_writing_reaps_writer_and_queue_continues(self):
        generator = self.writing_generator()
        first = generator.submit({"mode": "surprise", "brief": "wait"})["id"]
        wait_until(lambda: (self.root / "writer-worker").exists())
        pid = generator.writer_process.pid
        second = generator.submit(recipe())["id"]
        generator.cancel(first)
        wait_until(lambda: self.status(second) == "done")
        self.assertEqual(self.status(first), "cancelled")
        self.assertEqual(subprocess.run(["/bin/ps", "-p", str(pid)], capture_output=True).returncode, 1)

    def test_standalone_writer_and_music_overlap_with_independent_cancellation(self):
        generator = self.writing_generator()
        results = []
        def write():
            results.append(generator.inspiration({"mode": "surprise", "brief": "wait"}))
        worker = threading.Thread(target=write)
        worker.start()
        try:
            wait_until(lambda: (self.root / "writer-worker").exists())
            pid = generator.writer_process.pid
            job = generator.submit(recipe(style="wait"))["id"]
            wait_until(lambda: (self.root / "exclusive-worker").exists())
            music = generator.process
            with self.assertRaises(ValueError): generator.inspiration({"mode": "surprise"})
            self.assertIsNone(music.poll())
            self.assertFalse(generator.quiesce())
            # Stopping the song must not stop the independent writer.
            generator.cancel(job)
            wait_until(lambda: self.status(job) == "cancelled")
            self.assertIsNone(generator.writer_process.poll())
            second = generator.submit(recipe(style="wait"))["id"]
            wait_until(lambda: generator.process and generator.process.poll() is None)
            second_process = generator.process
            generator.cancel_writing()
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
            self.assertIsNone(second_process.poll())
            generator.cancel(second)
            wait_until(lambda: self.status(second) == "cancelled")
            self.assertEqual(results, [{"cancelled": True}])
            self.assertEqual(subprocess.run(["/bin/ps", "-p", str(pid)], capture_output=True).returncode, 1)
        finally:
            if generator.writer_process: generator.cancel_writing()
            worker.join(timeout=10)

    def test_waiting_writer_can_stop_before_launch_and_resumes_when_memory_returns(self):
        generator = self.writing_generator()
        self.admission.gates["writer"].clear()
        results = []
        worker = threading.Thread(target=lambda: results.append(generator.inspiration({"mode": "surprise"})))
        worker.start()
        wait_until(lambda: (generator.writing_status() or {}).get("stage") == "Waiting for memory")
        self.assertIsNone(generator.writer_process)
        self.assertTrue(generator.has_active_work())
        self.assertFalse(generator.quiesce())
        generator.cancel_writing()
        worker.join(timeout=10)
        self.assertEqual(results, [{"cancelled": True}])
        self.assertFalse(self.admission.entries)
        worker = threading.Thread(target=lambda: results.append(generator.inspiration({"mode": "surprise"})))
        worker.start()
        try:
            wait_until(lambda: (generator.writing_status() or {}).get("stage") == "Waiting for memory")
            self.admission.gates["writer"].set()
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results[-1]["lyrics"], "Two moons talk over tea.")
            self.assertTrue(generator.quiesce())
            with self.assertRaisesRegex(ValueError, "Riff is updating"):
                generator.inspiration({"mode": "surprise"})
            with self.assertRaisesRegex(ValueError, "Riff is updating"):
                generator.submit(recipe())
            generator.resume()
            job = generator.submit(recipe())["id"]
            wait_until(lambda: self.status(job) == "done")
        finally:
            if generator.writing_status(): generator.cancel_writing()
            worker.join(timeout=10)

    def test_waiting_native_cancellation_keeps_writer_and_next_job_retries_admission(self):
        generator = self.writing_generator()
        self.admission.gates["native"].clear()
        results = []
        worker = threading.Thread(target=lambda: results.append(generator.inspiration({"mode": "surprise", "brief": "wait"})))
        worker.start()
        try:
            wait_until(lambda: generator.writer_process is not None)
            first = generator.submit(recipe())["id"]
            wait_until(lambda: (generator.status() or {}).get("stage") == "Waiting for memory")
            generator.cancel(first)
            wait_until(lambda: self.status(first) == "cancelled")
            self.assertIsNone(generator.writer_process.poll())
            self.assertIsNone(generator.process)
            second = generator.submit(recipe())["id"]
            wait_until(lambda: (generator.status() or {}).get("stage") == "Waiting for memory")
            self.admission.gates["native"].set()
            wait_until(lambda: self.status(second) == "done")
            self.assertIsNone(generator.writer_process.poll())
        finally:
            if generator.writing_status(): generator.cancel_writing()
            worker.join(timeout=10)

    def test_automatic_writer_waiting_for_standalone_writer_can_be_cancelled(self):
        generator = self.writing_generator()
        worker = threading.Thread(target=lambda: generator.inspiration({"mode": "surprise", "brief": "wait"}))
        worker.start()
        try:
            wait_until(lambda: generator.writer_process is not None)
            job = generator.submit({"mode": "surprise"})["id"]
            wait_until(lambda: (generator.status() or {}).get("stage") == "Waiting for the writer")
            generator.cancel(job)
            wait_until(lambda: self.status(job) == "cancelled")
            self.assertIsNone(generator.writer_process.poll())
        finally:
            if generator.writing_status(): generator.cancel_writing()
            worker.join(timeout=10)

    def test_cancellation_reaps_child_and_next_queued_take_finishes(self):
        self.generator = Generator(self.store, self.fake_command)
        first = self.generator.submit(recipe(style="wait"))["id"]
        wait_until(lambda: (self.root / "exclusive-worker").exists())
        pid = self.generator.process.pid
        second = self.generator.submit(recipe(title="Second"))["id"]
        third = self.generator.submit(recipe(title="Do not run"))["id"]
        self.assertEqual(self.generator.cancel(third), "cancelled")
        self.assertEqual(self.status(third), "cancelled")
        self.assertEqual(self.generator.cancel(first), "cancelling")
        wait_until(lambda: self.status(second) == "done")
        self.assertEqual(self.status(first), "cancelled")
        self.assertEqual(subprocess.run(["/bin/ps", "-p", str(pid)], capture_output=True).returncode, 1)
        self.assertEqual(len(self.store.snapshot()["tracks"]), 1)
        self.assertEqual(self.store.snapshot()["tracks"][0]["title"], "Second")

    def test_failure_preserves_recipe_and_worker_continues(self):
        self.generator = Generator(self.store, self.fake_command)
        failed = self.generator.submit(recipe(style="fail"))["id"]
        success = self.generator.submit(recipe())["id"]
        wait_until(lambda: self.status(success) == "done")
        self.assertEqual(self.status(failed), "failed")
        with self.store.db() as db:
            row = db.execute("SELECT recipe,error FROM jobs WHERE id=?", (failed,)).fetchone()
        self.assertEqual(json.loads(row["recipe"])["seed"], "42")
        self.assertTrue(row["error"])

    def test_restart_recovers_interrupted_take_without_importing_partial_audio(self):
        with self.store.db() as db:
            db.execute("INSERT INTO jobs(id,title,created,status,recipe) VALUES('old','Old',0,'running',?)", (json.dumps(recipe()),))
        self.generator = Generator(self.store, self.fake_command)
        self.assertEqual(self.status("old"), "interrupted")
        self.assertEqual(self.store.snapshot()["tracks"], [])


class HttpTests(StudioFixture):
    def setUp(self):
        super().setUp()
        self.track_id = self.store.add_track(self.audio, recipe(), {})
        self.generator = Generator(self.store, lambda recipe, output: ["/usr/bin/false"])
        self.server = StudioServer(("127.0.0.1", 0), self.store, self.generator)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.generator.close()
        self.thread.join()
        super().tearDown()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request(method, path, body, headers or {})
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_seek_download_and_unsatisfiable_audio_ranges(self):
        path = f"/api/tracks/{self.track_id}/audio"
        status, headers, body = self.request("GET", path, headers={"Range": "bytes=10-99"})
        self.assertEqual(status, 206)
        self.assertEqual(body, self.audio.read_bytes()[10:100])
        self.assertEqual(headers["Content-Length"], "90")
        status, headers, body = self.request("HEAD", path)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(int(headers["Content-Length"]), self.audio.stat().st_size)
        self.assertEqual(self.request("GET", path, headers={"Range": "bytes=999999-"})[0], 416)
        self.assertEqual(self.request("GET", path, headers={"Range": "bytes=-8"})[2], self.audio.read_bytes()[-8:])

    def test_loaded_page_has_server_build_before_any_state_poll(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertNotIn(b"__RIFF_STUDIO_BUILD__", body)
        state = json.loads(self.request("GET", "/api/state")[2])
        self.assertIn(f'name="riff-studio-build" content="{state["studio"]["build"]}"'.encode(), body)
        self.assertEqual(self.request("HEAD", "/")[1]["Content-Length"], str(len(body)))
        self.assertEqual(self.request("HEAD", "/")[2], b"")
        self.assertEqual(self.request("GET", "/updates.js")[0], 200)

    def test_build_changes_for_asset_bytes_and_version_but_not_mtime(self):
        with tempfile.TemporaryDirectory() as folder:
            web = Path(folder)
            (web / "index.html").write_text("page")
            (web / "video.js").write_text("original encoder")
            original = studio_identity(web, "1.0.0")
            (web / "video.js").touch()
            self.assertEqual(studio_identity(web, "1.0.0"), original)
            (web / "video.js").write_text("updated encoder")
            self.assertNotEqual(studio_identity(web, "1.0.0")["build"], original["build"])
            (web / "video.js").write_text("original encoder")
            self.assertNotEqual(studio_identity(web, "1.0.1")["build"], original["build"])

    def test_storage_failure_returns_retryable_json_and_reading_recovers(self):
        with patch.object(self.store, "snapshot", side_effect=sqlite3.OperationalError("database or disk is full")):
            status, headers, body = self.request("GET", "/api/state")
            self.assertEqual(status, 503)
            self.assertIn("disk space", json.loads(body)["error"])
        self.assertEqual(self.request("GET", "/api/state")[0], 200)

    def test_cross_origin_and_host_spoofed_requests_cannot_mutate(self):
        payload = json.dumps({"favorite": True})
        headers = {"Content-Type": "application/json", "X-Riff-Request": "1", "Origin": "https://example.com"}
        self.assertEqual(self.request("PATCH", f"/api/tracks/{self.track_id}", payload, headers)[0], 403)
        self.assertFalse(self.store.track(self.track_id)["favorite"])
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/generations", json.dumps(recipe()), {"Content-Type": "application/json"})[0], 403)

    def test_unsafe_paths_and_invalid_json(self):
        self.assertEqual(self.request("GET", "/%2e%2e/run.py")[0], 404)
        headers = {"Content-Type": "application/json", "X-Riff-Request": "1"}
        self.assertEqual(self.request("POST", "/api/generations", "[]", headers)[0], 400)
        self.assertEqual(self.request("POST", "/api/generations", '{"lyrics":', headers)[0], 400)


if __name__ == "__main__":
    unittest.main()
