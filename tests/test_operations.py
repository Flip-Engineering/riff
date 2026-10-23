"""The versioned agent operation surface: identity, idempotency, receipts and restart."""
import http.client
import json
from pathlib import Path
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
        self.generator = Generator(self.store, self.command)
        self.server = StudioServer(("127.0.0.1", 0), self.store, self.generator)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        ready = patch("studio.platform_support.readiness", return_value={"ready": True})
        ready.start()
        self.addCleanup(ready.stop)

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
        self.assertEqual(submission["status"], "queued")
        self.assertFalse(submission["replayed"])
        self.assertIsNone(submission["parent"])
        for name in ("operation_id", "job_id", "family_id"):
            self.assertRegex(submission[name], "^[a-f0-9]{32}$")
        self.assertEqual(self.store.job(submission["job_id"])["status"], "queued")
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
        self.assertEqual(queued["status"], "queued")
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
    def test_failure_while_recording_leaves_no_job(self):
        with patch.object(Operations, "resolve_references", side_effect=ValueError("reference changed")):
            self.assertEqual(self.study()[0], 400)
        self.assertEqual(self.jobs(), [])
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 0)

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


if __name__ == "__main__":
    unittest.main()
