"""The versioned agent operation surface: identity, idempotency, receipts and restart."""
import http.client
import json
from pathlib import Path
import sqlite3
import sys
import threading
import unittest
from unittest.mock import patch

from capabilities import describe
from operations import Operations
from studio import StudioServer
from studio_core import Generator, Store
from test_studio import StudioFixture, recipe, wait_until

JSON = {"Content-Type": "application/json", "X-Riff-Request": "1"}


def conforms(value, schema, where="$"):
    """Check the JSON Schema subset the surface advertises."""
    kinds = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None), "integer": int, "number": (int, float)}
    if "anyOf" in schema:
        errors = []
        for option in schema["anyOf"]:
            try:
                return conforms(value, option, where)
            except AssertionError as error:
                errors.append(str(error))
        raise AssertionError(f"{where} matches no alternative: {errors}")
    wanted = schema.get("type")
    if wanted:
        names = wanted if isinstance(wanted, list) else [wanted]
        if not any(isinstance(value, kinds[name]) and not (name != "boolean" and isinstance(value, bool) and name in ("integer", "number"))
                   for name in names):
            raise AssertionError(f"{where} is {type(value).__name__}, expected {wanted}")
    if "enum" in schema and value not in schema["enum"]:
        raise AssertionError(f"{where}={value!r} not in {schema['enum']}")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                raise AssertionError(f"{where}.{name} is missing")
        for name, child in schema.get("properties", {}).items():
            if name in value:
                conforms(value[name], child, f"{where}.{name}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            conforms(item, schema["items"], f"{where}[{index}]")


class OperationFixture(StudioFixture):
    def setUp(self):
        super().setUp()
        self.gate = self.root / "gate"
        self.generator = self.make_generator()
        self.server = StudioServer(("127.0.0.1", 0), self.store, self.generator)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        ready = patch("studio.platform_support.readiness", return_value={"ready": True})
        ready.start()
        self.addCleanup(ready.stop)

    def make_generator(self):
        return Generator(self.store, self.command)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.generator.close()
        self.thread.join()
        super().tearDown()

    def command(self, settings, output):
        # Every take waits for the gate file, so tests control what is queued and what finishes.
        script = """
import array, sys, time, wave
from pathlib import Path
gate, target = map(Path, sys.argv[1:])
while not gate.exists():
    time.sleep(.02)
with wave.open(str(target), 'wb') as wav:
    wav.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
    wav.writeframes(array.array('h', [1000, -1000] * 4800).tobytes())
"""
        return [sys.executable, "-u", "-c", script, str(self.gate), str(output)]

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        data = None if body is None else json.dumps(body)
        connection.request(method, path, data, {**(JSON if method != "GET" else {}), **(headers or {})})
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, json.loads(raw) if raw else None

    def study(self, **body):
        return self.request("POST", "/api/v1/operations/study.create", {"recipe": recipe(), **body})

    def jobs(self):
        with self.store.db() as db:
            return [row["id"] for row in db.execute("SELECT id FROM jobs ORDER BY created,id")]

    def finish(self, job_id):
        self.gate.write_text("open")
        wait_until(lambda: self.store.job(job_id)["status"] == "done")


class SurfaceTests(OperationFixture):
    def test_capabilities_advertise_versioned_operations_with_schemas(self):
        status, discovery = self.request("GET", "/api/capabilities")
        self.assertEqual(status, 200)
        surface = discovery["agent_surface"]
        self.assertEqual(surface["version"], 1)
        self.assertEqual(surface["compatibility"]["base_path"], "/api/v1/operations")
        self.assertEqual(surface["compatibility"]["requests"]["header"], {"X-Riff-Request": "1"})
        self.assertIn("riff_version", surface["compatibility"])
        for name in ("list", "get", "recipe.validate", "study.create", "operation.cancel"):
            entry = surface["operations"][name]
            self.assertEqual(entry["since"], 1)
            self.assertIn("output_schema", entry)
            self.assertTrue(entry["path"].startswith("/api/v1/operations"))
            if entry["method"] == "POST":
                self.assertIn("input_schema", entry)
        self.assertEqual(json.dumps(surface, sort_keys=True), json.dumps(describe()["agent_surface"], sort_keys=True))
        # The existing operations stay for current clients.
        self.assertEqual(discovery["operations"]["generate"]["path"], "/api/generations")

    def test_submission_and_receipt_match_their_advertised_schemas(self):
        operations = describe()["agent_surface"]["operations"]
        status, submission = self.study()
        self.assertEqual(status, 201, submission)
        conforms(submission, operations["study.create"]["output_schema"])
        status, receipt = self.request("GET", f"/api/v1/operations/{submission['operation_id']}")
        self.assertEqual(status, 200)
        conforms(receipt, operations["get"]["output_schema"])
        conforms(self.request("POST", "/api/v1/operations/recipe.validate", {"recipe": recipe()})[1],
                 operations["recipe.validate"]["output_schema"])
        conforms(self.request("POST", "/api/v1/operations/operation.cancel", {"operation_id": submission["operation_id"]})[1],
                 operations["operation.cancel"]["output_schema"])

    def test_unknown_inputs_and_operations_are_rejected(self):
        self.assertEqual(self.request("POST", "/api/v1/operations/study.create", {"recipe": recipe(), "extra": 1})[0], 400)
        self.assertEqual(self.request("POST", "/api/v1/operations/study.create", {})[0], 400)
        self.assertEqual(self.request("POST", "/api/v1/operations/nothing.here", {})[0], 404)
        self.assertEqual(self.request("GET", "/api/v1/operations/" + "0" * 32)[0], 404)
        self.assertEqual(self.request("POST", "/api/v1/operations/study.create", {"recipe": recipe()}, {"X-Riff-Request": ""})[0], 403)


class IdentityTests(OperationFixture):
    def test_identity_is_returned_with_the_queued_job_before_work_starts(self):
        status, submission = self.study()
        self.assertEqual(status, 201)
        # The worker may claim the job before the response is read, so "queued" is not deterministic here
        # (QueuedContractTests pins it with a worker that never claims). What is deterministic: the
        # identity, and that nothing has been produced because the engine is held at the gate.
        self.assertIn(submission["status"], ("queued", "running"))
        self.assertFalse(submission["replayed"])
        self.assertIsNone(submission["parent"])
        for name in ("operation_id", "job_id", "family_id"):
            self.assertRegex(submission[name], "^[a-f0-9]{32}$")
        row = self.store.job(submission["job_id"])
        self.assertIn(row["status"], ("queued", "running"))
        self.assertIsNone(row["track_id"])
        self.assertFalse(submission["receipt"]["terminal"])
        self.assertEqual(submission["receipt"]["outputs"]["track_id"], "")
        self.assertFalse(self.gate.exists())
        by_job = self.request("GET", f"/api/v1/operations/{submission['job_id']}")[1]
        self.assertEqual(by_job["operation"]["id"], submission["operation_id"])

    def test_receipt_binds_the_submitted_recipe_effective_defaults_and_seed(self):
        sent = {"title": "Bare", "lyrics": "[Verse]\nWords", "style": "folk"}
        status, submission = self.study(recipe=sent)
        receipt = submission["receipt"]
        self.assertEqual(receipt["request"], {"recipe": sent})
        effective = receipt["effective_recipe"]
        self.assertEqual((effective["max_seconds"], effective["steps"], effective["solver"]), (30.0, 8, "midpoint"))
        self.assertEqual(receipt["seed"], effective["seed"])
        self.assertEqual(self.store.job(submission["job_id"])["recipe"]["seed"], receipt["seed"])
        self.assertEqual(receipt["runtime"]["riff"], (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip())
        self.assertEqual(receipt["stages"]["plan"], {"state": "not_applicable"})
        self.assertEqual(receipt["stages"]["audio"], {"state": "pending"})

    def test_family_and_parent_follow_recorded_links(self):
        first = self.study()[1]
        self.finish(first["job_id"])
        take = first["job_id"]
        status, child = self.study(recipe=recipe(parent_track_id=take, seed="7"))
        self.assertEqual(status, 201, child)
        self.assertEqual(child["parent"], {"kind": "track", "id": take})
        self.assertEqual(child["family_id"], first["family_id"])
        status, explicit = self.study(recipe=recipe(seed="8"), family_id=first["family_id"],
                                      parent={"kind": "operation", "id": first["operation_id"]})
        self.assertEqual((status, explicit["family_id"]), (201, first["family_id"]))
        other = self.study(recipe=recipe(seed="9"))[1]
        self.assertNotEqual(other["family_id"], first["family_id"])
        self.assertEqual(self.request("POST", "/api/v1/operations/study.create",
                                      {"recipe": recipe(), "family_id": "f" * 32})[0], 404)
        self.assertEqual(self.request("POST", "/api/v1/operations/study.create",
                                      {"recipe": recipe(), "parent": {"kind": "track", "id": "e" * 32}})[0], 404)
        self.assertEqual(self.request("POST", "/api/v1/operations/study.create",
                                      {"recipe": recipe(), "parent": {"kind": "song", "id": take}})[0], 400)
        listed = self.request("GET", f"/api/v1/operations?family={first['family_id']}")[1]["operations"]
        self.assertEqual({item["id"] for item in listed}, {first["operation_id"], child["operation_id"], explicit["operation_id"]})
        by_parent = self.request("GET", f"/api/v1/operations?parent={take}")[1]["operations"]
        self.assertEqual([item["id"] for item in by_parent], [child["operation_id"]])
        done = self.request("GET", "/api/v1/operations?status=done")[1]["operations"]
        self.assertEqual([item["id"] for item in done], [first["operation_id"]])

    def test_receipt_reports_outputs_and_stages_after_the_take_finishes(self):
        submission = self.study()[1]
        self.finish(submission["job_id"])
        receipt = self.request("GET", f"/api/v1/operations/{submission['operation_id']}")[1]
        self.assertTrue(receipt["terminal"])
        self.assertEqual(receipt["outputs"]["track_id"], submission["job_id"])
        self.assertEqual(receipt["stages"]["audio"], {"state": "done"})
        self.assertEqual(receipt["errors"], {})


class IdempotencyTests(OperationFixture):
    def test_repeated_key_returns_the_original_operation_without_queueing_again(self):
        first = self.study(idempotency_key="draft-1")[1]
        status, again = self.study(idempotency_key="draft-1")
        self.assertEqual(status, 200)
        self.assertTrue(again["replayed"])
        self.assertEqual((again["operation_id"], again["job_id"], again["family_id"]),
                         (first["operation_id"], first["job_id"], first["family_id"]))
        self.assertEqual(self.jobs(), [first["job_id"]])
        self.assertNotEqual(self.study()[1]["operation_id"], first["operation_id"])

    def test_same_key_with_different_input_conflicts_and_queues_nothing(self):
        first = self.study(idempotency_key="k")[1]
        status, error = self.study(idempotency_key="k", recipe=recipe(seed="99"))
        self.assertEqual(status, 409)
        self.assertIn("different request", error["error"])
        self.assertEqual(self.jobs(), [first["job_id"]])

    def test_keys_are_scoped_to_the_operation_name(self):
        studio = self.study(idempotency_key="shared")[1]
        status, legacy = self.request("POST", "/api/generations", recipe(), {"Idempotency-Key": "shared"})
        self.assertEqual(status, 201)
        self.assertNotEqual(legacy["operation_id"], studio["operation_id"])

    def test_invalid_keys_are_refused(self):
        for key in ("", "  ", "line\nbreak", 5):
            with self.subTest(key=key):
                self.assertEqual(self.study(idempotency_key=key)[0], 400)
        self.assertEqual(self.jobs(), [])

    def test_concurrent_requests_with_one_key_create_one_job(self):
        results = []
        def send():
            results.append(self.study(idempotency_key="race")[1])
        threads = [threading.Thread(target=send) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual({item["operation_id"] for item in results}, {results[0]["operation_id"]})
        self.assertEqual(sorted(item["replayed"] for item in results), [False] + [True] * 7)

    def test_studio_route_keeps_its_response_and_gains_identity_and_replay(self):
        status, queued = self.request("POST", "/api/generations", recipe(), {"Idempotency-Key": "form-1"})
        self.assertEqual(status, 201)
        self.assertEqual(set(queued) >= {"id", "recipe", "status", "operation_id", "family_id"}, True)
        self.assertIn(queued["status"], ("queued", "running"))
        again = self.request("POST", "/api/generations", recipe(), {"Idempotency-Key": "form-1"})
        self.assertEqual((again[0], again[1]["id"], again[1]["replayed"]), (200, queued["id"], True))
        self.assertEqual(self.request("POST", "/api/generations", recipe(seed="3"), {"Idempotency-Key": "form-1"})[0], 409)
        status, plan = self.request("POST", "/api/plans", recipe(cot="melody"))
        self.assertEqual(status, 201)
        self.assertEqual(plan["recipe"]["render_mode"], "plan")
        self.assertEqual(self.request("GET", f"/api/v1/operations/{plan['operation_id']}")[1]["operation"]["name"], "generate")

    def test_studio_and_agent_routes_queue_identical_jobs(self):
        studio = self.request("POST", "/api/generations", recipe(seed="5"))[1]
        agent = self.study(recipe=recipe(seed="5"))[1]
        self.assertEqual(self.store.job(studio["id"])["recipe"], self.store.job(agent["job_id"])["recipe"])


class AtomicityTests(OperationFixture):
    def operation_rows(self):
        with self.store.db() as db:
            return db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]

    def test_failure_before_the_transaction_leaves_nothing(self):
        with patch.object(Operations, "resolve_references", side_effect=ValueError("reference changed")):
            self.assertEqual(self.study()[0], 400)
        self.assertEqual((self.jobs(), self.operation_rows()), ([], 0))

    def test_failure_inside_the_transaction_rolls_back_the_job_and_the_operation(self):
        """The job row is inserted first; a failure after it must take the job back out."""
        original = Operations.adapter_references
        seen = []
        def fail_after_the_job_exists(recipe):
            with self.store.db() as db:
                seen.append(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
            raise ValueError("recording failed")
        with patch.object(Operations, "adapter_references", staticmethod(fail_after_the_job_exists)):
            self.assertEqual(self.study()[0], 400)
        # Another connection cannot see the uncommitted job, and nothing remained after the rollback.
        self.assertEqual(seen, [0])
        self.assertEqual((self.jobs(), self.operation_rows()), ([], 0))
        self.assertEqual(self.study()[0], 201)
        self.assertEqual((len(self.jobs()), self.operation_rows()), (1, 1))
        self.assertIs(Operations.adapter_references, original)

    def test_a_unique_key_collision_inside_the_transaction_leaves_one_job(self):
        first = self.study(idempotency_key="same")[1]
        original, calls = Operations._existing, []
        def miss_once(operations, *arguments):
            calls.append(arguments)
            return None if len(calls) == 1 else original(operations, *arguments)
        # The pre-check misses, as it would for a request racing the first; the insert path must catch it.
        with patch.object(Operations, "_existing", miss_once):
            status, result = self.study(idempotency_key="same")
        self.assertEqual(status, 200)
        self.assertEqual(result["operation_id"], first["operation_id"])
        self.assertEqual((self.jobs(), self.operation_rows()), ([first["job_id"]], 1))

    def test_invalid_recipe_queues_nothing_and_records_nothing(self):
        self.assertEqual(self.study(recipe=recipe(steps=0))[0], 400)
        self.assertEqual(self.jobs(), [])


class RestartTests(OperationFixture):
    def test_receipt_is_complete_after_the_studio_is_reopened(self):
        submission = self.study(idempotency_key="before-restart")[1]
        self.finish(submission["job_id"])
        before = self.request("GET", f"/api/v1/operations/{submission['operation_id']}")[1]
        self.server.shutdown(); self.server.server_close(); self.generator.close()
        reopened = Store(self.root / "data", self.root / "outputs")
        after = Operations(reopened, None).receipt(submission["operation_id"])
        self.assertEqual(after, before)
        self.assertEqual(after["request"], {"recipe": recipe()})
        # Restore the objects tearDown expects.
        self.generator = Generator(self.store, self.command)
        self.server = StudioServer(("127.0.0.1", 0), self.store, self.generator)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        again = self.study(idempotency_key="before-restart")[1]
        self.assertTrue(again["replayed"])
        self.assertEqual(again["operation_id"], submission["operation_id"])


class ReadTests(OperationFixture):
    def make_performance(self):
        codes = self.audio.with_suffix(".codes.i32")
        codes.write_bytes(bytes(range(40)))
        import hashlib
        saved = {"frames": 10, "truncated": False, "sha256": hashlib.sha256(codes.read_bytes()).hexdigest()}
        return self.store.add_track(self.audio, recipe(title="Source", performance=saved), {}), saved

    def test_receipts_and_references_carry_no_local_paths(self):
        submission = self.study()[1]
        self.finish(submission["job_id"])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET error=? WHERE id=?",
                       (f"Could not read {self.store.outputs}/riff/x.wav and {self.store.data_root}/y.log", submission["job_id"]))
        receipt = self.request("GET", f"/api/v1/operations/{submission['operation_id']}")[1]
        text = json.dumps(receipt)
        self.assertNotIn(str(self.root), text)
        self.assertEqual(receipt["errors"]["job"], "Could not read <library>/riff/x.wav and <data>/y.log")

    def test_receipt_for_a_job_made_before_operations_marks_what_it_lacks(self):
        from studio_core import validate_recipe
        stored = validate_recipe(recipe(seed="77"))
        with self.store.db() as db:
            db.execute("INSERT INTO jobs (id,title,created,status,recipe,queue_position) VALUES (?,?,?,?,?,?)",
                       ("a" * 32, "Older", 1.0, "interrupted", json.dumps(stored), 1))
        status, receipt = self.request("GET", f"/api/v1/operations/{'a' * 32}")
        self.assertEqual(status, 200)
        conforms(receipt, describe()["agent_surface"]["operations"]["get"]["output_schema"])
        self.assertFalse(receipt["recorded"])
        self.assertEqual(receipt["unrecorded"], ["operation", "request", "effective_recipe", "references", "runtime"])
        self.assertIsNone(receipt["operation"]["id"])
        self.assertIsNone(receipt["request"])
        self.assertEqual((receipt["seed"], receipt["job"]["status"]), ("77", "interrupted"))
        self.assertEqual(self.request("GET", "/api/v1/operations/" + "b" * 32)[0], 404)
        self.assertEqual(self.request("GET", "/api/v1/operations")[1]["operations"], [])

    def test_listing_pages_with_a_cursor_and_a_client_chosen_size(self):
        made = [self.study(recipe=recipe(seed=str(n)))[1]["operation_id"] for n in range(5)]
        everything = self.request("GET", "/api/v1/operations")[1]
        self.assertEqual([item["id"] for item in everything["operations"]], made[::-1])
        self.assertIsNone(everything["next_cursor"])
        seen, cursor = [], None
        while True:
            query = "limit=2" + (f"&cursor={cursor}" if cursor else "")
            page = self.request("GET", "/api/v1/operations?" + query)[1]
            self.assertLessEqual(len(page["operations"]), 2)
            seen += [item["id"] for item in page["operations"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(seen, made[::-1])
        exact = self.request("GET", "/api/v1/operations?limit=5")[1]
        self.assertEqual((len(exact["operations"]), exact["next_cursor"]), (5, None))
        for bad in ("limit=0", "limit=x", "cursor=!!", "limit=2&cursor=" + "e30"):
            with self.subTest(bad=bad):
                self.assertEqual(self.request("GET", "/api/v1/operations?" + bad)[0], 400)

    def test_references_are_verified_by_the_server_with_captured_provenance(self):
        track, saved = self.make_performance()
        resolve = lambda body: self.request("POST", "/api/v1/operations/references.resolve", body)
        status, result = resolve({"performance_source": track, "parent_track_id": track,
                                  "score_source": "riff-score-v1:" + "c" * 64, "acoustic_source": "riff-acoustic-v1:" + "d" * 64})
        self.assertEqual(status, 200, result)
        by_field = {item["field"]: item for item in result["references"]}
        self.assertEqual(by_field["performance_source"]["status"], "verified")
        self.assertEqual(by_field["performance_source"]["provenance"], saved)
        self.assertEqual(by_field["parent_track_id"]["provenance"], {"title": "Source", "archived": False})
        self.assertEqual(by_field["score_source"]["status"], "unavailable")
        self.assertEqual(by_field["acoustic_source"]["status"], "unavailable")
        self.assertTrue(by_field["score_source"]["error"])
        conforms(result, describe()["agent_surface"]["operations"]["references.resolve"]["output_schema"])
        self.assertEqual(resolve({"performance_source": "9" * 32})[1]["references"][0]["status"], "unavailable")
        offered = resolve({"subject": track})[1]
        self.assertEqual([(item["field"], item["status"]) for item in offered["references"]], [("performance_source", "verified")])
        self.assertEqual(resolve({"score_source": "riff-score-v1:" + "c" * 64, "other": 1})[0], 400)
        self.assertEqual(resolve({"subject": "8" * 32})[0], 404)

    def test_submission_records_resolved_references_and_parent(self):
        track, saved = self.make_performance()
        status, submission = self.study(recipe=recipe(performance_source=track, seed="3"))
        self.assertEqual(status, 201, submission)
        self.assertEqual(submission["parent"], {"kind": "track", "id": track})
        references = {item["field"]: item for item in submission["receipt"]["references"]}
        self.assertEqual(references["performance_source"]["provenance"], {"source": "track", **saved})
        self.assertEqual(submission["receipt"]["stages"]["performance"], {"state": "reused", "from": track})

    def test_compare_separates_shared_inputs_from_differences(self):
        first = self.study(recipe=recipe(seed="1", steps=8))[1]
        second = self.study(recipe=recipe(seed="2", steps=16))[1]
        self.finish(first["job_id"])
        compare = lambda subjects: self.request("POST", "/api/v1/operations/inputs.compare", {"subjects": subjects})
        status, result = compare([first["operation_id"], second["job_id"]])
        self.assertEqual(status, 200, result)
        conforms(result, describe()["agent_surface"]["operations"]["inputs.compare"]["output_schema"])
        self.assertEqual({item["field"] for item in result["differences"]}, {"seed", "steps"})
        seed = next(item for item in result["differences"] if item["field"] == "seed")
        self.assertEqual([(v["subject"], v["value"]) for v in seed["values"]],
                         [(first["operation_id"], "1"), (second["job_id"], "2")])
        self.assertIn("style", {item["field"] for item in result["shared"]})
        self.assertEqual([item["family_id"] for item in result["subjects"]], [first["family_id"], second["family_id"]])
        self.assertEqual(compare([first["operation_id"]])[0], 400)
        self.assertEqual(compare([first["operation_id"], "7" * 32])[0], 404)
        track, _ = self.make_performance()
        self.assertEqual(compare([first["operation_id"], track])[0], 200)

    def test_compare_uses_the_same_differences_as_lineage(self):
        from studio_core import recipe_changes
        before, after = recipe(seed="1"), recipe(seed="2", style="x")
        self.assertEqual({item["field"] for item in recipe_changes(before, after)}, {"seed", "style"})
        self.assertEqual(recipe_changes(before, dict(before)), [])


class ValidateAndCancelTests(OperationFixture):
    def test_validate_reports_the_effective_recipe_and_queues_nothing(self):
        status, result = self.request("POST", "/api/v1/operations/recipe.validate", {"recipe": recipe(seed="12")})
        self.assertEqual(status, 200)
        self.assertTrue(result["valid"])
        self.assertEqual(result["effective_recipe"]["seed"], "12")
        status, result = self.request("POST", "/api/v1/operations/recipe.validate", {"recipe": recipe(steps=0)})
        self.assertEqual(status, 200)
        self.assertFalse(result["valid"])
        self.assertIn("solver step", result["error"])
        self.assertEqual(self.jobs(), [])
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 0)

    def test_cancel_by_operation_id_stops_a_queued_take_and_keeps_the_receipt(self):
        submission = self.study()[1]
        status, cancelled = self.request("POST", "/api/v1/operations/operation.cancel", {"operation_id": submission["operation_id"]})
        self.assertEqual(status, 200, cancelled)
        self.assertIn(cancelled["status"], ("cancelled", "cancelling"))
        wait_until(lambda: self.request("GET", f"/api/v1/operations/{submission['operation_id']}")[1]["terminal"])
        receipt = self.request("GET", f"/api/v1/operations/{submission['operation_id']}")[1]
        self.assertEqual(receipt["job"]["status"], "cancelled")
        self.assertEqual(receipt["request"], {"recipe": recipe()})
        self.assertEqual(self.request("POST", "/api/v1/operations/operation.cancel", {"operation_id": submission["operation_id"]})[0], 400)
        self.assertEqual(self.request("POST", "/api/v1/operations/operation.cancel", {"operation_id": "d" * 32})[0], 404)


class IdleGenerator(Generator):
    """Queues work but never runs it, so a test can set a job's state and stage artifacts itself."""
    def work(self):
        self.stop.wait()


class Scores:
    """Saved-score service double: knows the references a test registers."""
    def __init__(self, real):
        self.real, self.known = real, set()

    def describe(self, reference, cancelled=None):
        if reference not in self.known:
            raise ValueError("This saved score is not in the library.")
        return {"id": reference, "title": "plan", "source_job_id": "s" * 32, "sha256": reference[-64:], "bytes": 10,
                "token_count": 4, "truncated": False, "cot": "melody", "compatible": True}

    def recipe(self, recipe, cancelled=None):
        recipe = dict(recipe)
        recipe.pop("score_input", None)
        if recipe.get("score_source"):
            found = self.describe(recipe["score_source"])
            recipe["score_input"] = {key: found[key] for key in ("id", "sha256", "bytes", "token_count", "truncated")}
        return recipe

    def __getattr__(self, name):
        return getattr(self.real, name)


class Sounds:
    def __init__(self, real):
        self.real, self.known = real, set()

    def describe(self, reference, cancelled=None):
        if reference not in self.known:
            raise ValueError("This saved sound is not in the library.")
        return {"id": reference, "title": "sound", "source_job_id": "s" * 32, "sample_rate": 48000, "channels": 2,
                "duration": 1.0, "seed": "42", "steps": 8, "solver": "midpoint", "hashes": {}, "compatible": True}

    def recipe(self, payload, cancelled=None):
        from studio_core import validate_recipe
        found = self.describe(payload["acoustic_source"])
        return {**validate_recipe(recipe(title=payload.get("title", "Finished"))), "acoustic_source": found["id"],
                "acoustic": {"artifact_id": found["id"]}, "decoder": payload.get("decoder", {})}

    def __getattr__(self, name):
        return getattr(self.real, name)


SCORE = "riff-score-v1:" + "a" * 64
SOUND = "riff-acoustic-v1:" + "b" * 64


class DerivedFixture(OperationFixture):
    def make_generator(self):
        generator = IdleGenerator(self.store, self.command)
        self.scores, self.sounds = Scores(self.store.artifacts), Sounds(self.store.acoustics)
        self.scores.known.add(SCORE)
        self.sounds.known.add(SOUND)
        self.store.artifacts, self.store.acoustics = self.scores, self.sounds
        return generator

    def source(self, status="done", stages=(), **updates):
        """A queued operation moved to ``status`` with the named captured stages."""
        submission = self.study(recipe=recipe(**{"cot": "melody", "seed": "1234", "max_seconds": 10, **updates}))
        self.assertEqual(submission[0], 201, submission[1])
        submission = submission[1]
        job_id, captured = submission["job_id"], self.store.job(submission["job_id"])["recipe"]
        if "score" in stages:
            captured["symbolic_plan"] = {"artifact_id": SCORE, "sha256": "a" * 64, "token_count": 4, "truncated": False, "abc": "X:1"}
        if "performance" in stages:
            (self.store.outputs / "riff").mkdir(exist_ok=True)
            codes = self.store.outputs / "riff" / f"{job_id}.codes.i32"
            codes.write_bytes(bytes(40))
            captured["performance"] = {"frames": 10, "truncated": False, "sha256": __import__("hashlib").sha256(bytes(40)).hexdigest()}
        if "sound" in stages:
            captured["acoustic"] = {"artifact_id": SOUND}
        track = ""
        if status == "done":
            from test_studio import fixture_audio
            (self.store.outputs / "riff").mkdir(exist_ok=True)
            fixture_audio(self.store.outputs / "riff" / f"{job_id}.wav")
            track = self.store.add_track(self.store.outputs / "riff" / f"{job_id}.wav", captured, {}, job_id)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status=?,recipe=?,track_id=?,finished=?,error=? WHERE id=?",
                       (status, json.dumps(captured), track or None, 2.0, "It stopped." if status == "failed" else "", job_id))
        return submission

    def derive(self, operation, **body):
        return self.request("POST", f"/api/v1/operations/{operation}", body)


class DevelopTests(DerivedFixture):
    def test_develop_keeps_the_seed_and_verified_score_and_records_lineage(self):
        study = self.source(stages=("score",))
        status, result = self.derive("take.develop", source=study["operation_id"], changes={"max_seconds": 60, "steps": 16})
        self.assertEqual(status, 201, result)
        conforms(result, describe()["agent_surface"]["operations"]["take.develop"]["output_schema"])
        effective = result["receipt"]["effective_recipe"]
        self.assertEqual((effective["seed"], effective["score_source"], effective["abc"]), ("1234", SCORE, ""))
        self.assertEqual((effective["max_seconds"], effective["steps"]), (60.0, 16))
        self.assertEqual(effective["parent_track_id"], study["job_id"])
        self.assertEqual(effective["title"], "A test take — developed")
        self.assertEqual(result["family_id"], study["family_id"])
        self.assertEqual(result["parent"], {"kind": "operation", "id": study["operation_id"]})
        derivation = result["receipt"]["derivation"]
        self.assertEqual(derivation["retained"], {"seed": True, "score": True, "performance": False})
        self.assertEqual(derivation["from"]["operation_id"], study["operation_id"])
        self.assertEqual({item["field"] for item in derivation["changed"]} >= {"max_seconds", "steps", "score_source"}, True)
        self.assertEqual(result["receipt"]["references"][0]["provenance"]["sha256"], "a" * 64)
        self.assertEqual(result["receipt"]["stages"]["plan"], {"state": "reused", "from": SCORE})

    def test_defaults_that_cannot_be_kept_are_noted_and_explicit_requests_fail(self):
        direct = self.source(cot="off")
        status, result = self.derive("take.develop", source=direct["job_id"])
        self.assertEqual(status, 201, result)
        self.assertEqual(result["receipt"]["derivation"]["retained"]["score"], False)
        self.assertTrue(result["receipt"]["derivation"]["notes"])
        self.assertEqual(self.derive("take.develop", source=direct["job_id"], retain=["score"])[0], 400)
        unscored = self.source()
        self.assertEqual(self.derive("take.develop", source=unscored["job_id"], retain=["score"])[0], 400)
        self.assertEqual(self.derive("take.develop", source=unscored["job_id"], retain=["seed"])[0], 201)
        self.assertEqual(self.derive("take.develop", source=unscored["job_id"], retain=["nonsense"])[0], 400)

    def test_retained_performance_fixes_the_duration_and_is_verified(self):
        take = self.source(stages=("score", "performance"))
        status, result = self.derive("take.develop", source=take["job_id"], retain=["performance"])
        self.assertEqual(status, 201, result)
        effective = result["receipt"]["effective_recipe"]
        self.assertEqual((effective["performance_source"], effective["max_seconds"]), (take["job_id"], 10 / 25))
        self.assertEqual(result["receipt"]["derivation"]["retained"]["performance"], True)
        self.assertEqual(self.derive("take.develop", source=take["job_id"], retain=["performance"], changes={"max_seconds": 60})[0], 400)
        (self.store.outputs / "riff" / f"{take['job_id']}.codes.i32").write_bytes(b"changed")
        self.assertEqual(self.derive("take.develop", source=take["job_id"], retain=["performance"])[0], 400)

    def test_changes_cannot_name_references_or_unknown_fields(self):
        take = self.source(stages=("score",))
        for changes in ({"score_source": SCORE}, {"performance_source": take["job_id"]}, {"parent_track_id": take["job_id"]},
                        {"typo_field": 1}, "text"):
            with self.subTest(changes=changes):
                self.assertEqual(self.derive("take.develop", source=take["job_id"], changes=changes)[0], 400)
        self.assertEqual(self.derive("take.develop", source=take["job_id"], changes={"abc": "X:1\nK:C\nC D E"})[0], 201)
        edited = self.store.job(self.jobs()[-1])["recipe"]
        self.assertEqual(edited["score_source"], "")
        self.assertIn("C D E", edited["abc"])

    def test_draft_mode_queues_nothing_and_reports_validation(self):
        take = self.source(stages=("score",))
        before = self.jobs()
        status, draft = self.derive("take.develop", source=take["job_id"], enqueue=False, changes={"steps": 0})
        self.assertEqual(status, 200, draft)
        conforms(draft, describe()["agent_surface"]["operations"]["take.develop"]["output_schema"])
        self.assertEqual(draft["draft"]["score_source"], SCORE)
        self.assertFalse(draft["validation"]["valid"])
        ok = self.derive("take.develop", source=take["job_id"], enqueue=False)[1]
        self.assertTrue(ok["validation"]["valid"])
        self.assertEqual(self.jobs(), before)

    def test_develop_is_idempotent(self):
        take = self.source(stages=("score",))
        first = self.derive("take.develop", source=take["job_id"], idempotency_key="grow-1")[1]
        again = self.derive("take.develop", source=take["job_id"], idempotency_key="grow-1")
        self.assertEqual((again[0], again[1]["replayed"], again[1]["operation_id"]), (200, True, first["operation_id"]))
        self.assertEqual(self.derive("take.develop", source=take["job_id"], idempotency_key="grow-1", changes={"steps": 9})[0], 409)

    def test_a_take_with_no_operation_record_can_still_be_developed(self):
        from studio_core import validate_recipe
        stored = validate_recipe(recipe(seed="88"))
        take = self.store.add_track(self.audio, stored, {})
        status, result = self.derive("take.develop", source=take, retain=["seed"])
        self.assertEqual(status, 201, result)
        self.assertEqual(result["parent"], {"kind": "track", "id": take})
        self.assertEqual(result["receipt"]["effective_recipe"]["seed"], "88")


class ReplayTests(DerivedFixture):
    def test_replay_score_and_performance_are_strict(self):
        scored = self.source(stages=("score", "performance"))
        status, result = self.derive("take.replay", source=scored["job_id"], replay="score")
        self.assertEqual(status, 201, result)
        self.assertEqual(result["receipt"]["effective_recipe"]["score_source"], SCORE)
        self.assertEqual(result["receipt"]["derivation"]["mode"], "take.replay:score")
        status, result = self.derive("take.replay", source=scored["job_id"], replay="performance")
        self.assertEqual(status, 201, result)
        self.assertEqual(result["receipt"]["effective_recipe"]["performance_source"], scored["job_id"])
        self.assertEqual(result["receipt"]["effective_recipe"]["seed"], "1234")
        plain = self.source()
        for replay in ("score", "performance", "sound"):
            with self.subTest(replay=replay):
                self.assertEqual(self.derive("take.replay", source=plain["job_id"], replay=replay)[0], 400)
        self.assertEqual(self.derive("take.replay", source=scored["job_id"])[0], 400)
        self.assertEqual(self.derive("take.replay", source=scored["job_id"], replay="score", retain=["seed"])[0], 400)

    def test_replay_sound_queues_what_the_studios_finish_audio_button_posts(self):
        """Finish audio posts {acoustic_source, decoder, title?} to /api/generations; replay is the same take."""
        take = self.source(stages=("sound",))
        studio = self.request("POST", "/api/generations", {"acoustic_source": SOUND, "decoder": {"core_frames": 5}, "title": "Finished"})[1]
        agent = self.derive("take.replay", source=take["job_id"], replay="sound",
                            changes={"decoder": {"core_frames": 5}, "title": "Finished"})[1]
        self.assertEqual(studio["recipe"], agent["receipt"]["effective_recipe"])
        draft = self.derive("take.replay", source=take["job_id"], replay="sound", enqueue=False,
                            changes={"decoder": {"core_frames": 5}, "title": "Finished"})[1]
        self.assertEqual(draft["draft"], {"acoustic_source": SOUND, "decoder": {"core_frames": 5}, "title": "Finished"})

    def test_replay_sound_finishes_the_saved_sound_with_decoder_controls(self):
        take = self.source(stages=("sound",))
        status, result = self.derive("take.replay", source=take["job_id"], replay="sound", changes={"decoder": {"core_frames": 5}})
        self.assertEqual(status, 201, result)
        effective = result["receipt"]["effective_recipe"]
        self.assertEqual((effective["acoustic_source"], effective["decoder"]), (SOUND, {"core_frames": 5}))
        self.assertEqual(result["receipt"]["derivation"]["retained"], {"sound": True})
        self.assertEqual(self.derive("take.replay", source=take["job_id"], replay="sound", changes={"steps": 3})[0], 400)
        self.assertEqual(self.derive("take.replay", source=take["job_id"], replay="sound", retain=["seed"])[0], 400)


class SelectionTests(DerivedFixture):
    def test_selection_is_durable_append_only_and_idempotent(self):
        first, second = self.source(), self.source()
        status, chosen = self.derive("take.select", take=first["job_id"], rationale="Warmer sound", idempotency_key="pick-1")
        self.assertEqual(status, 201, chosen)
        conforms(chosen, describe()["agent_surface"]["operations"]["take.select"]["output_schema"])
        self.assertEqual((chosen["family_id"], chosen["selection"]["rationale"], chosen["current"]), (first["family_id"], "Warmer sound", True))
        again = self.derive("take.select", take=first["job_id"], rationale="Warmer sound", idempotency_key="pick-1")
        self.assertEqual((again[0], again[1]["replayed"], again[1]["selection_id"]), (200, True, chosen["selection_id"]))
        self.assertEqual(self.derive("take.select", take=first["job_id"], rationale="Other", idempotency_key="pick-1")[0], 409)
        # A second choice is appended; the first remains in the history.
        later = self.derive("take.select", take=first["job_id"], rationale="Still the one")[1]
        self.assertEqual([item["id"] for item in later["history"]], [later["selection_id"], chosen["selection_id"]])
        self.assertEqual(self.store.job(first["job_id"])["status"], "done")
        self.assertEqual(self.jobs(), [first["job_id"], second["job_id"]])

    def test_only_completed_takes_in_a_family_can_be_selected(self):
        running = self.source(status="queued")
        self.assertEqual(self.derive("take.select", take=running["job_id"])[0], 400)
        self.assertEqual(self.derive("take.select", take="9" * 32)[0], 404)
        done = self.source()
        other = self.source()
        self.assertEqual(self.derive("take.select", take=done["job_id"], family_id=other["family_id"])[0], 400)
        self.assertEqual(self.derive("take.select", take=done["job_id"], family_id="f" * 32)[0], 404)
        self.assertEqual(self.derive("take.select", take=done["job_id"], rationale=5)[0], 400)
        older = self.store.add_track(self.audio, recipe(), {})
        self.assertEqual(self.derive("take.select", take=older)[0], 400)
        self.assertEqual(self.derive("take.select", take=older, family_id=done["family_id"])[0], 201)

    def test_develop_records_the_selection_it_came_from(self):
        take = self.source(stages=("score",))
        other = self.source()
        self.assertIsNone(self.derive("take.develop", source=take["job_id"])[1]["receipt"]["derivation"]["selection"])
        chosen = self.derive("take.select", take=take["job_id"], rationale="Best phrasing")[1]
        implicit = self.derive("take.develop", source=take["job_id"])[1]
        self.assertEqual(implicit["receipt"]["derivation"]["selection"], chosen["selection_id"])
        explicit = self.derive("take.develop", source=take["job_id"], selection=chosen["selection_id"])
        self.assertEqual(explicit[1]["receipt"]["derivation"]["selection"], chosen["selection_id"])
        self.assertEqual(self.derive("take.develop", source=other["job_id"], selection=chosen["selection_id"])[0], 400)
        self.assertEqual(self.derive("take.develop", source=take["job_id"], selection="1" * 32)[0], 404)


class ResumeTests(DerivedFixture):
    def resume(self, operation_id, **body):
        return self.derive("operation.resume", operation_id=operation_id, **body)

    def test_resume_continues_from_the_last_captured_stage_with_the_same_seed(self):
        cases = (("failed", (), {}), ("interrupted", ("score",), {"score_source": SCORE}),
                 ("cancelled", ("score", "performance"), {"performance_source": "job"}))
        for status, stages, expected in cases:
            with self.subTest(status=status):
                ended = self.source(status=status, stages=stages)
                code, result = self.resume(ended["operation_id"])
                self.assertEqual(code, 201, result)
                conforms(result, describe()["agent_surface"]["operations"]["operation.resume"]["output_schema"])
                effective = result["receipt"]["effective_recipe"]
                self.assertEqual(effective["seed"], "1234")
                self.assertEqual(effective["title"], "A test take")
                for field, value in expected.items():
                    self.assertEqual(effective[field], ended["job_id"] if value == "job" else value)
                self.assertEqual(result["receipt"]["operation"]["resumes"], ended["operation_id"])
                self.assertEqual(result["family_id"], ended["family_id"])
                self.assertEqual(result["status"], "queued")
                self.assertEqual(result["receipt"]["derivation"]["mode"], "operation.resume")

    def test_resume_finishes_a_captured_sound(self):
        ended = self.source(status="interrupted", stages=("score", "performance", "sound"))
        code, result = self.resume(ended["operation_id"])
        self.assertEqual(code, 201, result)
        self.assertEqual(result["receipt"]["effective_recipe"]["acoustic_source"], SOUND)
        self.assertEqual(result["receipt"]["derivation"]["retained"]["sound"], True)

    def test_resume_is_explicit_and_only_for_ended_unfinished_work(self):
        cancelled = self.source(status="cancelled")
        self.assertEqual(self.store.job(cancelled["job_id"])["status"], "cancelled")
        # Nothing resumes by itself: no operation links to it until resume is called.
        self.assertEqual(self.request("GET", "/api/v1/operations")[1]["operations"][0]["resumes"], None)
        self.assertEqual(self.resume(self.source(status="queued")["operation_id"])[0], 400)
        self.assertEqual(self.resume(self.source(status="done")["operation_id"])[0], 400)
        self.assertEqual(self.resume("5" * 32)[0], 404)
        first = self.resume(cancelled["operation_id"])
        self.assertEqual(first[0], 201)
        status, conflict = self.resume(cancelled["operation_id"])
        self.assertEqual(status, 409)
        self.assertIn(first[1]["operation_id"], conflict["error"])

    def test_resume_is_idempotent_and_does_not_repeat_a_captured_requested_stage(self):
        ended = self.source(status="failed")
        first = self.resume(ended["operation_id"], idempotency_key="again")[1]
        again = self.resume(ended["operation_id"], idempotency_key="again")
        self.assertEqual((again[0], again[1]["replayed"], again[1]["operation_id"]), (200, True, first["operation_id"]))
        planned = self.source(status="failed", stages=("score",), render_mode="plan")
        status, error = self.resume(planned["operation_id"])
        self.assertEqual(status, 400)
        self.assertIn("already captured", error["error"])

    def test_retry_route_is_the_same_explicit_resume(self):
        ended = self.source(status="failed")
        status, body = self.request("POST", f"/api/jobs/{ended['job_id']}/retry", {})
        self.assertEqual(status, 201, body)
        self.assertEqual(body["status"], "queued")
        receipt = self.request("GET", f"/api/v1/operations/{body['operation_id']}")[1]
        self.assertEqual(receipt["operation"]["resumes"], ended["operation_id"])
        self.assertEqual(self.request("POST", f"/api/jobs/{self.source()['job_id']}/retry", {})[0], 400)
        self.assertEqual(self.request("POST", f"/api/jobs/{'4' * 32}/retry", {})[0], 404)

    def test_a_job_with_no_operation_record_can_be_resumed(self):
        from studio_core import validate_recipe
        with self.store.db() as db:
            db.execute("INSERT INTO jobs (id,title,created,status,recipe,queue_position,error) VALUES (?,?,?,?,?,?,?)",
                       ("c" * 32, "Older", 1.0, "interrupted", json.dumps(validate_recipe(recipe(seed="66"))), 1, "stopped"))
        status, result = self.resume("c" * 32)
        self.assertEqual(status, 201, result)
        self.assertEqual((result["receipt"]["effective_recipe"]["seed"], result["receipt"]["operation"]["resumes"]), ("66", "c" * 32))


class QueuedContractTests(DerivedFixture):
    """Identity comes back from the call that queues the job, before any work: pinned with a worker that never claims."""
    def test_the_response_the_row_and_the_receipt_all_say_queued_with_nothing_produced(self):
        status, submission = self.study()
        self.assertEqual(status, 201)
        self.assertEqual(submission["status"], "queued")
        row = self.store.job(submission["job_id"])
        self.assertEqual((row["status"], row["track_id"], row["started"]), ("queued", None, None))
        receipt = submission["receipt"]
        self.assertEqual((receipt["job"]["status"], receipt["terminal"]), ("queued", False))
        self.assertEqual(receipt["outputs"], {"track_id": "", "score_source": "", "performance_source": "", "acoustic_source": ""})
        self.assertEqual(self.request("GET", f"/api/v1/operations/{submission['operation_id']}")[1]["job"]["status"], "queued")
        status, legacy = self.request("POST", "/api/generations", recipe(seed="4"))
        self.assertEqual((status, legacy["status"]), (201, "queued"))


class PublicationTests(DerivedFixture):
    def lora(self):
        from test_loras import adapter_bytes
        source = self.root / "a.safetensors"
        source.write_bytes(adapter_bytes())
        self.addCleanup(self.store.loras.close)
        adapter = self.store.loras.import_local([source], metadata={"name": "Folk study", "license": "CC0"})
        capabilities = patch("run.engine_capabilities", return_value={"lora": True, "warm_engine": False, "exact_score_replay": True,
                                                                      "acoustic_checkpoint": True, "score_format": "f", "prefix_contract": "p"})
        capabilities.start()
        self.addCleanup(capabilities.stop)
        return adapter

    def test_receipts_carry_no_installation_paths_in_any_reference(self):
        adapter = self.lora()
        take = self.source(stages=("score", "performance", "sound"))
        status, submission = self.study(recipe=recipe(
            cot="melody", score_source=SCORE, loras=[{"id": adapter["id"], "strength": .5}]))
        self.assertEqual(status, 201, submission)
        status, performance = self.derive("take.develop", source=take["job_id"], retain=["performance"])
        self.assertEqual(status, 201, performance)
        status, sound = self.derive("take.replay", source=take["job_id"], replay="sound")
        self.assertEqual(status, 201, sound)
        fields = {item["field"] for result in (submission, performance, sound) for item in result["receipt"]["references"]}
        self.assertEqual(fields, {"score_source", "loras", "performance_source", "acoustic_source", "parent_track_id"})
        kinds = {item["kind"] for item in submission["receipt"]["references"]}
        self.assertIn("adapter", kinds)
        listing = self.request("GET", "/api/v1/operations")[1]
        resolved = self.request("POST", "/api/v1/operations/references.resolve", {"subject": take["job_id"]})[1]
        for label, value in (("submission", submission), ("performance", performance), ("sound", sound),
                             ("receipt", self.request("GET", f"/api/v1/operations/{submission['operation_id']}")[1]),
                             ("list", listing), ("resolve", resolved)):
            text = json.dumps(value)
            for root in (self.store.data_root, self.store.outputs, Path(__file__).resolve().parents[1], self.root):
                with self.subTest(label=label, root=str(root)):
                    self.assertNotIn(str(root), text)

    def test_recipes_and_user_text_are_never_rewritten(self):
        text = f"[Verse]\nWalking {self.store.outputs}/riff and {self.store.data_root} at dawn"
        body = recipe(lyrics=text, style=f"folk, field recording from {self.store.outputs}")
        status, submission = self.study(recipe=body)
        self.assertEqual(status, 201, submission)
        receipt = submission["receipt"]
        self.assertEqual(receipt["request"], {"recipe": body})
        self.assertEqual(receipt["effective_recipe"]["lyrics"], text)
        self.assertEqual(receipt["result_recipe"]["lyrics"], text)
        self.assertEqual(self.store.job(submission["job_id"])["recipe"]["lyrics"], text)
        status, draft = self.derive("take.develop", source=submission["operation_id"], enqueue=False)
        self.assertEqual(draft["draft"]["lyrics"], text)
        compared = self.request("POST", "/api/v1/operations/inputs.compare", {"subjects": [
            submission["operation_id"], self.study(recipe=recipe(lyrics=text, seed="2"))[1]["operation_id"]]})[1]
        self.assertIn(text, [item["value"] for item in compared["shared"] if item["field"] == "lyrics"])
        # Only error messages, which are free text about this installation, are made path-free.
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?",
                       (f"Could not read {self.store.outputs}/riff/x.wav", submission["job_id"]))
        failed = self.request("GET", f"/api/v1/operations/{submission['operation_id']}")[1]
        self.assertEqual(failed["errors"]["job"], "Could not read <library>/riff/x.wav")
        self.assertEqual(failed["effective_recipe"]["lyrics"], text)

    def test_studio_routes_return_the_recipe_the_generator_returned(self):
        text = f"[Verse]\n{self.store.outputs} stays as written"
        status, queued = self.request("POST", "/api/generations", recipe(lyrics=text, seed="31"))
        self.assertEqual(status, 201)
        self.assertEqual(queued["recipe"], self.store.job(queued["id"])["recipe"])
        self.assertEqual(queued["recipe"]["lyrics"], text)
        status, plan = self.request("POST", "/api/plans", recipe(cot="melody", seed="32"))
        self.assertEqual(plan["recipe"], self.store.job(plan["id"])["recipe"])
        again = self.request("POST", "/api/generations", recipe(lyrics=text, seed="31"), {"Idempotency-Key": "k"})[1]
        replay = self.request("POST", "/api/generations", recipe(lyrics=text, seed="31"), {"Idempotency-Key": "k"})[1]
        self.assertEqual(replay["recipe"], again["recipe"])

    def test_status_filter_rejects_unknown_statuses(self):
        self.study()
        self.assertEqual(self.request("GET", "/api/v1/operations?status=queued")[0], 200)
        self.assertEqual(self.request("GET", "/api/v1/operations?status=quued")[0], 400)


class ConcurrencyAndHardeningTests(DerivedFixture):
    def race(self, send, count=6):
        barrier, results = threading.Barrier(count), []
        def go():
            barrier.wait()
            results.append(send())
        threads = [threading.Thread(target=go) for _ in range(count)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        return results

    def test_concurrent_resumes_without_a_key_queue_exactly_one_job(self):
        ended = self.source(status="failed")
        before = len(self.jobs())
        results = self.race(lambda: self.derive("operation.resume", operation_id=ended["operation_id"]))
        self.assertEqual(sorted(status for status, _ in results), [201] + [409] * 5)
        self.assertEqual(len(self.jobs()), before + 1)
        for status, body in results:
            if status == 409:
                self.assertIn("already resuming", body["error"])

    def test_concurrent_retries_without_a_key_queue_exactly_one_job(self):
        ended = self.source(status="failed")
        before = len(self.jobs())
        results = self.race(lambda: self.request("POST", f"/api/jobs/{ended['job_id']}/retry", {}))
        self.assertEqual(sorted(status for status, _ in results), [201] + [409] * 5)
        self.assertEqual(len(self.jobs()), before + 1)

    def test_a_resume_of_a_resume_is_still_allowed_once_the_first_has_ended(self):
        ended = self.source(status="failed")
        first = self.derive("operation.resume", operation_id=ended["operation_id"])[1]
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='failed' WHERE id=?", (first["job_id"],))
        self.assertEqual(self.derive("operation.resume", operation_id=ended["operation_id"])[0], 201)

    def test_reference_fields_are_type_checked_and_answer_400(self):
        resolve = lambda body: self.request("POST", "/api/v1/operations/references.resolve", body)
        for field in ("subject", "score_source", "performance_source", "acoustic_source", "parent_track_id"):
            for value in ([], {}, 5, True, None, [1], {"a": 1}, "", "not-a-reference", 1.5):
                with self.subTest(field=field, value=value):
                    status, body = resolve({field: value})
                    self.assertEqual(status, 400, body)
                    self.assertIn("error", body)
        # The server still answers afterwards.
        self.assertEqual(resolve({"subject": self.source()["job_id"]})[0], 200)

    def test_every_operation_refuses_values_of_the_wrong_type_without_dropping_the_connection(self):
        take = self.source(stages=("score",))["job_id"]
        bad = ([], {}, 5, True, [1], {"a": 1})
        cases = []
        for value in bad:
            cases += [("operation.cancel", {"operation_id": value}), ("operation.resume", {"operation_id": value}),
                      ("take.select", {"take": value}), ("take.develop", {"source": value}),
                      ("take.replay", {"source": value, "replay": "score"}), ("inputs.compare", {"subjects": [value, take]}),
                      ("study.create", {"recipe": recipe(), "family_id": value}),
                      ("study.create", {"recipe": recipe(), "parent": {"kind": "track", "id": value}}),
                      ("take.develop", {"source": take, "selection": value}),
                      ("take.select", {"take": take, "family_id": value})]
        for name, body in cases:
            with self.subTest(name=name, body=body):
                status, answer = self.derive(name, **body)
                self.assertIn(status, (400, 404), answer)
                self.assertIn("error", answer)
        self.assertEqual(self.request("GET", "/api/v1/operations?family=%5B1%5D")[0], 400)

    def test_a_sqlite_binding_error_from_request_input_is_a_400_not_a_dropped_connection(self):
        with patch.object(Operations, "compare", side_effect=sqlite3.ProgrammingError("Error binding parameter 1")):
            status, body = self.derive("inputs.compare", subjects=["a" * 32, "b" * 32])
        self.assertEqual(status, 400)
        self.assertIn("value the studio could not use", body["error"])
        with patch.object(Operations, "receipt", side_effect=sqlite3.InterfaceError("bad")):
            self.assertEqual(self.request("GET", "/api/v1/operations/" + "c" * 32)[0], 400)

    def test_an_unexpected_error_is_a_500_with_a_message_and_the_server_keeps_serving(self):
        with patch.object(Operations, "validate", side_effect=RuntimeError("boom")):
            status, body = self.derive("recipe.validate", recipe=recipe())
        self.assertEqual(status, 500)
        self.assertEqual(body["error"], "The studio could not complete this request.")
        with patch.object(Operations, "list", side_effect=RuntimeError("boom")):
            self.assertEqual(self.request("GET", "/api/v1/operations")[0], 500)
        self.assertEqual(self.request("GET", "/api/v1/operations")[0], 200)


class FakeMaintenance:
    def __init__(self, task):
        self.lock, self.task = threading.RLock(), task


class AdmissionRefusalTests(DerivedFixture):
    """Work refused for the Studio's routes is refused identically on the agent routes."""
    def refusals(self):
        return {"restart": ("Riff is restarting. Your draft is saved.", lambda: setattr(self.server, "restart_requested", True),
                            lambda: setattr(self.server, "restart_requested", False)),
                "setup": ("Setup is still running. Your draft is saved.",
                          lambda: setattr(self.server, "maintenance", FakeMaintenance({"status": "running", "action": "setup"})),
                          lambda: setattr(self.server, "maintenance", None)),
                "engine": ("Open Studio settings to finish setting up the music engine.", lambda: None, lambda: None)}

    def routes(self, sources):
        develop, replay, ended = sources
        return {"studio generations": lambda: self.request("POST", "/api/generations", recipe(seed="5")),
                "studio plans": lambda: self.request("POST", "/api/plans", recipe(cot="melody", seed="5")),
                "study.create": lambda: self.derive("study.create", recipe=recipe(seed="6")),
                "take.develop": lambda: self.derive("take.develop", source=develop),
                "take.replay": lambda: self.derive("take.replay", source=replay, replay="performance"),
                "operation.resume": lambda: self.derive("operation.resume", operation_id=ended),
                "retry route": lambda: self.request("POST", f"/api/jobs/{ended}/retry", {})}

    def test_refusals_are_the_same_on_every_route(self):
        sources = (self.source(stages=("score",))["job_id"], self.source(stages=("performance",))["job_id"],
                   self.source(status="failed")["operation_id"])
        routes = self.routes(sources)
        before = self.jobs()
        for name, (message, apply, undo) in self.refusals().items():
            for label, send in routes.items():
                with self.subTest(refusal=name, route=label):
                    apply()
                    try:
                        if name == "engine":
                            with patch("studio.platform_support.readiness", return_value={"ready": False}):
                                status, body = send()
                        else:
                            status, body = send()
                    finally:
                        undo()
                    self.assertEqual((status, body["error"]), (400, message))
        self.assertEqual(self.jobs(), before)
        # With the refusal lifted the same routes queue work again.
        self.assertEqual(routes["study.create"]()[0], 201)

    def test_finishing_a_saved_sound_needs_no_engine_but_still_respects_restart_and_setup(self):
        take = self.source(stages=("sound",))["job_id"]
        with patch("studio.platform_support.readiness", return_value={"ready": False}):
            self.assertEqual(self.derive("take.replay", source=take, replay="sound")[0], 201)
        self.server.restart_requested = True
        try:
            status, body = self.derive("take.replay", source=take, replay="sound")
        finally:
            self.server.restart_requested = False
        self.assertEqual((status, body["error"]), (400, "Riff is restarting. Your draft is saved."))


if __name__ == "__main__":
    unittest.main()
