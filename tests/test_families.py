"""Named study families: creation, shared inputs, per-take differences, selection and development."""
import json
import unittest
from unittest.mock import patch

from capabilities import describe
from operations import Operations
from test_operations import DerivedFixture, SCORE, conforms
from test_studio import recipe

BASE = {"title": "Night Orchard", "lyrics": "[Verse]\nLamps along the orchard wall.", "style": "slow folk, warm room",
        "cot": "melody", "max_seconds": 12, "steps": 8}


class FamilyFixture(DerivedFixture):
    def create(self, **body):
        body.setdefault("name", "Night Orchard studies")
        body.setdefault("takes", [{"seed": "11"}, {"seed": "22"}])
        return self.request("POST", "/api/v1/operations/family.create", body)

    def family(self, family_id):
        return self.request("GET", f"/api/v1/families/{family_id}")

    def schema(self, name):
        return describe()["agent_surface"]["operations"][name]["output_schema"]

    def complete(self, job_id, stages=()):
        """Move a queued job to done with a take, as the worker would."""
        from test_studio import fixture_audio
        captured = self.store.job(job_id)["recipe"]
        if "score" in stages:
            captured["symbolic_plan"] = {"artifact_id": SCORE, "sha256": "a" * 64, "token_count": 4, "truncated": False, "abc": "X:1"}
        (self.store.outputs / "riff").mkdir(exist_ok=True)
        fixture_audio(self.store.outputs / "riff" / f"{job_id}.wav")
        track = self.store.add_track(self.store.outputs / "riff" / f"{job_id}.wav", captured, {}, job_id)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='done',recipe=?,track_id=?,finished=2.0 WHERE id=?", (json.dumps(captured), track, job_id))


class CreateTests(FamilyFixture):
    def test_a_named_family_queues_one_study_per_take_with_shared_inputs_declared_once(self):
        status, result = self.create(shared=BASE, takes=[{"seed": "11"}, {"seed": "22", "max_seconds": 20}, {"seed": "33", "solver": "ab2"}],
                                     context={"brief": "an orchard at night"})
        self.assertEqual(status, 201, result)
        conforms(result, describe()["agent_surface"]["operations"]["family.create"]["output_schema"])
        self.assertFalse(result["replayed"])
        self.assertEqual(len(result["submissions"]), 3)
        self.assertEqual({item["family_id"] for item in result["submissions"]}, {result["family_id"]})
        self.assertTrue(all(item["status"] == "queued" for item in result["submissions"]))
        family = result["family"]
        self.assertEqual((family["named"], family["name"], family["context"]), (True, "Night Orchard studies", {"brief": "an orchard at night"}))
        self.assertEqual(family["declared_shared"], BASE)
        self.assertEqual(len(self.jobs()), 3)
        for submission, seed in zip(result["submissions"], ("11", "22", "33")):
            self.assertEqual(submission["receipt"]["effective_recipe"]["seed"], seed)
            self.assertEqual(submission["receipt"]["effective_recipe"]["style"], BASE["style"])

    def test_one_study_or_many_is_the_callers_choice(self):
        for count in (1, 2, 5):
            with self.subTest(count=count):
                status, result = self.create(name=f"Family of {count}", takes=[{"seed": str(n)} for n in range(count)], shared=BASE)
                self.assertEqual((status, len(result["submissions"])), (201, count))
        self.assertEqual(self.create(takes=[])[0], 400)
        self.assertEqual(self.create(takes="x")[0], 400)
        self.assertEqual(self.create(takes=["x"])[0], 400)

    def test_invalid_input_queues_nothing(self):
        before = self.jobs()
        for body in ({"name": ""}, {"name": 5}, {"shared": []}, {"context": "x"}, {"extra": 1},
                     {"takes": [{"seed": "1"}, {"steps": 0}]}):
            with self.subTest(body=body):
                self.assertEqual(self.create(**{"shared": BASE, **body})[0], 400)
        self.assertEqual(self.jobs(), before)
        self.assertEqual(self.request("GET", "/api/v1/families")[1]["families"], [])
        self.assertIn("Study 2", self.create(shared=BASE, takes=[{"seed": "1"}, {"steps": 0}])[1]["error"])

    def test_repeating_a_request_returns_the_family_and_queues_nothing_new(self):
        first = self.create(shared=BASE, idempotency_key="start")[1]
        status, again = self.create(shared=BASE, idempotency_key="start")
        self.assertEqual(status, 200)
        self.assertTrue(again["replayed"])
        self.assertEqual(again["family_id"], first["family_id"])
        self.assertEqual([item["operation_id"] for item in again["submissions"]], [item["operation_id"] for item in first["submissions"]])
        self.assertEqual(len(self.jobs()), 2)
        self.assertEqual(self.create(shared=BASE, idempotency_key="start", takes=[{"seed": "99"}])[0], 409)
        self.assertEqual(len(self.jobs()), 2)

    def test_a_request_that_failed_part_way_finishes_when_repeated(self):
        original, calls = Operations.submit, []
        def fail_second(operations, *arguments, **keywords):
            calls.append(arguments[0])
            if len(calls) == 2:
                raise ValueError("Setup is still running. Your draft is saved.")
            return original(operations, *arguments, **keywords)
        with patch.object(Operations, "submit", fail_second):
            status, error = self.create(shared=BASE, takes=[{"seed": "1"}, {"seed": "2"}, {"seed": "3"}], idempotency_key="resume-me")
        self.assertEqual(status, 400)
        self.assertEqual(len(self.jobs()), 1)
        status, result = self.create(shared=BASE, takes=[{"seed": "1"}, {"seed": "2"}, {"seed": "3"}], idempotency_key="resume-me")
        self.assertEqual((status, result["replayed"], len(result["submissions"])), (200, True, 3))
        self.assertEqual(len(self.jobs()), 3)
        self.assertEqual([item["replayed"] for item in result["submissions"]], [True, False, False])
        self.assertEqual(len(self.request("GET", "/api/v1/families")[1]["families"]), 1)

    def test_context_is_provenance_set_at_creation_and_untouched_by_rename_or_new_studies(self):
        family = self.create(shared=BASE, context={"brief": "an orchard at night"})[1]
        status, error = self.request("POST", "/api/v1/operations/family.rename", {"family_id": family["family_id"], "name": "x", "context": {}})
        self.assertEqual(status, 400)
        self.assertIn("provenance", error["error"])
        renamed = self.request("POST", "/api/v1/operations/family.rename", {"family_id": family["family_id"], "name": "Renamed"})[1]
        self.assertEqual((renamed["context"], renamed["declared_shared"]), ({"brief": "an orchard at night"}, BASE))
        self.assertEqual(renamed["name"], "Renamed")

    def test_declared_shared_inputs_are_never_merged_into_a_study_added_later(self):
        family = self.create(shared=BASE)[1]
        status, added = self.study(recipe={"title": "Bare", "seed": "9"}, family_id=family["family_id"])
        self.assertEqual(status, 201)
        effective = added["receipt"]["effective_recipe"]
        self.assertEqual((effective["style"], effective["lyrics"]), ("", ""))
        state = self.family(family["family_id"])[1]
        self.assertEqual(state["declared_shared"], BASE)
        self.assertNotIn("style", {item["field"] for item in state["common"]})

    def test_a_repeat_must_be_the_same_request_so_reordering_takes_is_refused(self):
        self.create(shared=BASE, takes=[{"seed": "1"}, {"seed": "2"}], idempotency_key="order")
        self.assertEqual(self.create(shared=BASE, takes=[{"seed": "2"}, {"seed": "1"}], idempotency_key="order")[0], 409)
        self.assertEqual(len(self.jobs()), 2)

    def test_studies_can_be_added_to_a_family_later(self):
        family = self.create(shared=BASE)[1]
        status, added = self.study(recipe={**BASE, "seed": "44"}, family_id=family["family_id"])
        self.assertEqual((status, added["family_id"]), (201, family["family_id"]))
        self.assertEqual(len(self.family(family["family_id"])[1]["takes"]), 3)


class SharedAndDiffTests(FamilyFixture):
    def test_shared_inputs_and_each_studys_own_differences(self):
        family = self.create(shared=BASE, takes=[{"seed": "11"}, {"seed": "22", "max_seconds": 20, "solver": "ab2"}, {"seed": "33"}])[1]
        status, state = self.family(family["family_id"])
        self.assertEqual(status, 200)
        conforms(state, self.schema("family.get"))
        shared = {item["field"]: item["value"] for item in state["common"]}
        for field in ("style", "lyrics", "cot", "steps", "mode"):
            self.assertIn(field, shared)
        self.assertEqual(shared["style"], BASE["style"])
        for field in ("seed", "max_seconds", "solver"):
            self.assertNotIn(field, shared)
        diffs = [{item["field"]: item["value"] for item in take["diff"]} for take in state["takes"]]
        self.assertEqual(diffs[0], {"seed": "11", "max_seconds": 12.0, "solver": "midpoint"})
        self.assertEqual(diffs[1], {"seed": "22", "max_seconds": 20.0, "solver": "ab2"})
        self.assertEqual(diffs[2], {"seed": "33", "max_seconds": 12.0, "solver": "midpoint"})
        self.assertTrue(all(item["recorded"] for take in state["takes"] for item in take["diff"]))
        self.assertEqual([take["role"] for take in state["takes"]], ["study"] * 3)
        self.assertEqual(state["takes"][1]["settings"], {"max_seconds": 20.0, "steps": 8, "solver": "ab2", "cot": "melody", "style": BASE["style"],
                                                         "lyrics": BASE["lyrics"], "cfg_scale": 1.0, "temperature": 1.0})

    def test_a_single_study_family_shares_everything_and_differs_in_nothing(self):
        family = self.create(shared=BASE, takes=[{"seed": "5"}])[1]
        state = self.family(family["family_id"])[1]
        self.assertEqual(state["takes"][0]["diff"], [])
        self.assertIn("seed", {item["field"] for item in state["common"]})

    def test_a_score_a_study_composed_shows_as_a_difference(self):
        family = self.create(shared=BASE)[1]
        first, second = (item["job_id"] for item in family["submissions"])
        self.complete(first, stages=("score",))
        self.complete(second)
        takes = self.family(family["family_id"])[1]["takes"]
        composed = lambda take: next(item for item in take["diff"] if item["field"] == "composed_score")
        self.assertEqual((composed(takes[0])["value"], composed(takes[0])["recorded"]), (SCORE, True))
        self.assertEqual(composed(takes[1])["recorded"], False)
        self.assertEqual([take["status"] for take in takes], ["done", "done"])
        self.assertEqual(takes[0]["outputs"]["score_source"], SCORE)

    def test_unrecorded_values_are_not_treated_as_defaults(self):
        family = self.create(shared=BASE)[1]
        job = family["submissions"][0]["job_id"]
        stored = self.store.job(job)["recipe"]
        stored.pop("temperature")
        with self.store.db() as db:
            db.execute("UPDATE jobs SET recipe=? WHERE id=?", (json.dumps(stored), job))
        takes = self.family(family["family_id"])[1]["takes"]
        item = next(entry for entry in takes[0]["diff"] if entry["field"] == "temperature")
        self.assertEqual(item["recorded"], False)


class DevelopmentTests(FamilyFixture):
    def test_a_developed_take_joins_the_family_with_its_changes_against_its_parent(self):
        family = self.create(shared=BASE, takes=[{"seed": "11"}, {"seed": "22"}])[1]
        chosen = family["submissions"][1]
        self.complete(chosen["job_id"], stages=("score",))
        selected = self.request("POST", "/api/v1/operations/take.select", {"take": chosen["operation_id"], "rationale": "Better phrasing"})[1]
        status, develop = self.request("POST", "/api/v1/operations/take.develop",
                                       {"source": chosen["operation_id"], "changes": {"max_seconds": 90, "steps": 16}})
        self.assertEqual(status, 201, develop)
        self.assertEqual(develop["family_id"], family["family_id"])
        state = self.family(family["family_id"])[1]
        conforms(state, self.schema("family.get"))
        self.assertEqual([take["role"] for take in state["takes"]], ["study", "study", "develop"])
        grown = state["takes"][2]
        self.assertEqual(grown["parent"], {"kind": "operation", "id": chosen["operation_id"]})
        self.assertEqual(grown["derivation"]["selection"], selected["selection_id"])
        changed = {item["field"]: (item["before"], item["value"]) for item in grown["diff"]}
        self.assertEqual(changed["max_seconds"], (12.0, 90.0))
        self.assertEqual(changed["steps"], (8, 16))
        self.assertNotIn("seed", changed)
        self.assertEqual(state["takes"][1]["children"], [develop["operation_id"]])
        self.assertEqual(state["takes"][0]["children"], [])
        self.assertEqual([take["selected"] for take in state["takes"]], [False, True, False])
        # The studies' own differences are still measured between studies only.
        self.assertEqual({item["field"] for item in state["takes"][0]["diff"]}, {"seed", "composed_score"})
        self.assertEqual(self.store.job(chosen["job_id"])["recipe"]["max_seconds"], 12.0)

    def test_selection_history_is_kept_and_the_latest_is_current(self):
        family = self.create(shared=BASE)[1]
        first, second = family["submissions"]
        for item in (first, second):
            self.complete(item["job_id"])
        one = self.request("POST", "/api/v1/operations/take.select", {"take": first["operation_id"], "rationale": "warm"})[1]
        two = self.request("POST", "/api/v1/operations/take.select", {"take": second["operation_id"], "rationale": "clearer"})[1]
        selection = self.family(family["family_id"])[1]["selection"]
        self.assertEqual(selection["current"]["id"], two["selection_id"])
        self.assertEqual([item["id"] for item in selection["history"]], [two["selection_id"], one["selection_id"]])

    def test_a_take_continued_from_a_recording_with_no_record_is_shown_against_it(self):
        from studio_core import validate_recipe
        older = self.store.add_track(self.audio, validate_recipe(recipe(seed="9")), {})
        status, develop = self.request("POST", "/api/v1/operations/take.develop", {"source": older, "retain": ["seed"], "changes": {"steps": 12}})
        self.assertEqual(status, 201, develop)
        state = self.family(develop["family_id"])[1]
        self.assertEqual((state["named"], [take["role"] for take in state["takes"]]), (False, ["develop"]))
        self.assertEqual({item["field"] for item in state["takes"][0]["diff"]} >= {"steps"}, True)


class NamingAndListingTests(FamilyFixture):
    def test_an_implicit_family_can_be_named_and_renamed(self):
        study = self.study()[1]
        state = self.family(study["family_id"])[1]
        self.assertEqual((state["named"], state["name"]), (False, None))
        status, named = self.request("POST", "/api/v1/operations/family.rename", {"family_id": study["family_id"], "name": "  First idea  "})
        self.assertEqual(status, 200, named)
        conforms(named, self.schema("family.rename"))
        self.assertEqual((named["named"], named["name"]), (True, "First idea"))
        renamed = self.request("POST", "/api/v1/operations/family.rename", {"family_id": study["family_id"], "name": "Second idea"})[1]
        self.assertEqual((renamed["name"], renamed["created"]), ("Second idea", named["created"]))
        self.assertEqual(len(self.request("GET", "/api/v1/families")[1]["families"]), 1)
        for body in ({"family_id": "7" * 32, "name": "x"}, ):
            self.assertEqual(self.request("POST", "/api/v1/operations/family.rename", body)[0], 404)
        for body in ({"family_id": study["family_id"], "name": ""}, {"family_id": "bad", "name": "x"},
                     {"family_id": study["family_id"], "name": "x", "other": 1}):
            self.assertEqual(self.request("POST", "/api/v1/operations/family.rename", body)[0], 400)
        self.assertEqual(self.family("8" * 32)[0], 404)

    def test_listing_pages_with_a_cursor_and_filters_named_families(self):
        implicit = self.study()[1]
        made = [self.create(name=f"Family {n}", shared=BASE, takes=[{"seed": str(n)}])[1]["family_id"] for n in range(4)]
        everything = self.request("GET", "/api/v1/families")[1]
        conforms(everything, self.schema("family.list"))
        self.assertEqual([item["family_id"] for item in everything["families"]], made[::-1] + [implicit["family_id"]])
        self.assertIsNone(everything["next_cursor"])
        seen, cursor = [], None
        while True:
            page = self.request("GET", "/api/v1/families?limit=2" + (f"&cursor={cursor}" if cursor else ""))[1]
            self.assertLessEqual(len(page["families"]), 2)
            seen += [item["family_id"] for item in page["families"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(seen, made[::-1] + [implicit["family_id"]])
        named = self.request("GET", "/api/v1/families?named=true")[1]["families"]
        self.assertEqual({item["family_id"] for item in named}, set(made))
        unnamed = self.request("GET", "/api/v1/families?named=false")[1]["families"]
        self.assertEqual([item["family_id"] for item in unnamed], [implicit["family_id"]])
        for bad in ("limit=0", "limit=x", "cursor=!!", "named=maybe"):
            with self.subTest(bad=bad):
                self.assertEqual(self.request("GET", "/api/v1/families?" + bad)[0], 400)

    def test_listing_reports_the_newest_status_and_the_selected_take(self):
        family = self.create(shared=BASE)[1]
        first = family["submissions"][0]
        self.complete(first["job_id"])
        self.request("POST", "/api/v1/operations/take.select", {"take": first["operation_id"]})
        item = self.request("GET", "/api/v1/families")[1]["families"][0]
        self.assertEqual((item["takes"], item["selected_take"], item["name"]), (2, first["job_id"], "Night Orchard studies"))
        self.assertEqual(item["latest_status"], "queued")

    def test_one_off_generation_still_works_and_starts_an_implicit_family(self):
        status, queued = self.request("POST", "/api/generations", recipe(seed="3"))
        self.assertEqual(status, 201)
        listed = self.request("GET", "/api/v1/families")[1]["families"]
        self.assertEqual([(item["family_id"], item["named"], item["takes"]) for item in listed], [(queued["family_id"], False, 1)])

    def test_capabilities_advertise_the_family_operations(self):
        operations = self.request("GET", "/api/capabilities")[1]["agent_surface"]["operations"]
        for name in ("family.create", "family.get", "family.list", "family.rename"):
            self.assertIn("output_schema", operations[name])
        self.assertEqual(operations["family.get"]["path"], "/api/v1/families/{family_id}")
        self.assertIn("input_schema", operations["family.create"])


if __name__ == "__main__":
    unittest.main()
