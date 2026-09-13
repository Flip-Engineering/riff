"""Composer inputs survive provider, draft and queued-generation boundaries."""
import hashlib
from io import BytesIO
import json
import unittest
from unittest.mock import patch

import model_options
import writer
from capabilities import describe
from review_recipe import FIELDS, generation_context, symbolic_context
from studio_core import Generator, validate_recipe
from test_studio import StudioFixture, wait_until
from test_reviews import FakeKeychain, Reviews, fake_listener


SCORE = 'X:1\nT:Folded melody\nM:7/8\nL:1/8\nQ:1/4=112\nK:Dm\n"Dm"D2 F A2 G2|"Bb"F2 E D4|'
SCORE_ID = "riff-score-v1:" + "a" * 64
SAVED_SCORE = {"id": SCORE_ID, "title": "Folded melody", "abc": SCORE, "cot": "full",
               "token_count": 91, "truncated": False, "sha256": "b" * 64, "bytes": 456,
               "source_job_id": "c" * 32, "provenance": {"kind": "composition"}, "compatible": True}


def source(**changes):
    return validate_recipe({"title": "Held title", "lyrics": "Keep these original words.", "style": "Oud and chamber choir",
        "cot": "full", "abc": SCORE, "seed": "58201", "steps": 11, "solver": "ab2", "max_seconds": 18,
        "cfg_scale": 1.4, "temperature": .85, "refinement": {**{key: value[2] for key, value in model_options.OPTIONS.items()},
        "semantic_min_tokens": 100, "abc_top_k": 41, "abc_temperature": .6}, **changes})


def response(**changes):
    take = source(**changes)
    return {"summary": "An asymmetrical chorus with a gentler ending.", "generation": {key: take[key] for key in FIELDS}}


class WriterContractTests(unittest.TestCase):
    def test_unavailable_notation_is_distinct_from_empty_score_and_incompatible_ids_are_not_offered(self):
        unreadable = {**SAVED_SCORE, "abc": "", "display_error": "The saved score's notation could not be displayed."}
        legacy = {**SAVED_SCORE, "id": "riff-score-v1:" + "b" * 64}
        legacy.pop("compatible")
        incompatible = {**SAVED_SCORE, "id": "riff-score-v1:" + "c" * 64, "compatible": False}
        payload = {**source(), "available_scores": [unreadable, legacy, incompatible]}
        self.assertEqual(symbolic_context(payload)["available_scores"], [unreadable, legacy])
        schema = writer.writing_schema(payload)["properties"]["generation"]
        self.assertEqual(schema["properties"]["score_source"]["enum"], ["", SCORE_ID, legacy["id"]])
        with self.assertRaisesRegex(ValueError, "score.*not supplied"):
            writer.written_generation(response(abc="", score_source=incompatible["id"]), payload)

    def test_provider_can_keep_an_offered_exact_score_with_complete_editable_context(self):
        payload = {**source(), "available_scores": [{**SAVED_SCORE, "path": "/private/score.json"}],
                   "idea_engine": "openrouter", "model": "google/gemini-3.8-flash", "api_key": "test-writer-secret"}
        proposed = response(abc="", score_source=SCORE_ID, steps=17)
        reply = {"choices": [{"message": {"content": json.dumps(proposed)}}]}
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(reply).encode())) as send:
            actual = writer.write(payload)
        body = json.loads(send.call_args.args[0].data)
        schema = body["response_format"]["json_schema"]["schema"]["properties"]["generation"]
        context = json.loads(body["messages"][1]["content"])
        self.assertEqual(schema["properties"]["score_source"]["enum"], ["", SCORE_ID])
        self.assertIn("score_source", schema["required"])
        self.assertEqual(context["symbolic"]["available_scores"], [SAVED_SCORE])
        self.assertNotIn("/private/", json.dumps(context))
        self.assertEqual(actual["generation"], proposed["generation"])

    def test_local_score_changes_replace_the_alternative_and_omission_keeps_attachment(self):
        attached = {**source(abc="", score_source=SCORE_ID), "available_scores": [SAVED_SCORE]}
        cases = [({"abc": SCORE}, SCORE, ""), ({"abc": ""}, "", ""),
                 ({"steps": 17}, "", SCORE_ID), ({"score_source": ""}, "", "")]
        for proposal, abc, score_id in cases:
            with self.subTest(proposal=proposal):
                result = writer.written_generation({"generation": proposal}, attached, partial=True)
                self.assertEqual((result["abc"], result["score_source"]), (abc, score_id))
        retained = writer.written_generation({"generation": {"score_source": SCORE_ID}},
                                            {**source(), "available_scores": [SAVED_SCORE]}, partial=True)
        self.assertEqual((retained["abc"], retained["score_source"]), ("", SCORE_ID))
        with self.assertRaises(ValueError):
            writer.written_generation({"generation": {"abc": SCORE, "score_source": SCORE_ID}}, attached, partial=True)

    def test_unoffered_score_cannot_be_authorized_by_the_current_input_or_reference(self):
        proposed = response(abc="", score_source=SCORE_ID)
        for payload in (source(), source(abc="", score_source=SCORE_ID),
                        {**source(), "reference": {"available_scores": [SAVED_SCORE]}},
                        {**source(), "available_scores": ["wrong-shape", {"id": "/private/score.json"}]}):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "score.*not supplied"):
                writer.written_generation(proposed, payload)

    def test_capability_schema_accepts_owned_score_ids_without_limiting_to_one_request(self):
        import re
        capabilities = describe()
        field = capabilities["recipe_schema"]["properties"]["score_source"]
        self.assertNotIn("enum", field)
        self.assertIsNotNone(re.fullmatch(field["pattern"], SCORE_ID))
        self.assertIsNone(re.fullmatch(field["pattern"], "/private/score.json"))
        self.assertIn("score_source", capabilities["score"]["inputs"])
        self.assertEqual(capabilities["operations"]["score"]["path"], "/api/scores/{score_source}")
        self.assertEqual(capabilities["operations"]["score"]["method"], "GET")

    def test_cloud_receives_all_controls_and_returns_runnable_score_and_settings(self):
        payload = {**source(), "idea_engine": "openrouter", "model": "google/gemini-3.8-flash", "api_key": "test-writer-secret",
                   "symbolic_plan": {"abc": SCORE, "token_count": 91, "truncated": False}, "brief": "Develop the harmony"}
        proposed = response(steps=23, cfg_scale=1.9, temperature=.7, seed="7001", abc=SCORE.replace('"Bb"', '"Gm"'))
        reply = {"choices": [{"message": {"content": json.dumps(proposed)}}]}
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(reply).encode())) as send:
            result = writer.write(payload)
        body = json.loads(send.call_args.args[0].data)
        context = json.loads(body["messages"][1]["content"])
        self.assertEqual(context["inputs"], generation_context(payload))
        self.assertEqual(context["symbolic"], symbolic_context(payload))
        self.assertEqual(body["response_format"]["json_schema"]["schema"]["properties"]["generation"]["properties"]["refinement"]["properties"].keys(), model_options.OPTIONS.keys())
        self.assertEqual(result["generation"], proposed["generation"])
        self.assertEqual(validate_recipe(result)["abc"], proposed["generation"]["abc"])
        self.assertNotIn(payload["api_key"], json.dumps(context))
        self.assertNotIn(payload["api_key"], json.dumps(result))

    def test_explicit_holds_preserve_words_or_sound_without_dropping_other_controls(self):
        proposed = response(lyrics="Fresh words", style="New sound", steps=31)
        kept = writer.written_generation(proposed, {**source(), "hold_words": True, "hold_sound": True})
        self.assertEqual(kept["lyrics"], source()["lyrics"])
        self.assertEqual(kept["style"], source()["style"])
        self.assertEqual(kept["steps"], 31)
        words = writer.written_generation(proposed, {**source(), "write_scope": "words", "hold_words": True})
        self.assertEqual(words["lyrics"], "Fresh words")
        self.assertEqual(words["style"], source()["style"])
        sound = writer.written_generation(proposed, {**source(), "write_scope": "sound", "hold_sound": True})
        self.assertEqual(sound["style"], "New sound")
        self.assertEqual(sound["lyrics"], source()["lyrics"])

    def test_local_partial_recipe_retains_every_proposed_supported_input(self):
        proposed = {"summary": "Local composition", "generation": {"abc": SCORE, "steps": 27, "solver": "midpoint",
            "refinement": {"abc_temperature": .4, "semantic_top_p": .72}}}
        actual = writer.written_generation(proposed, source(), partial=True)
        for key, value in proposed["generation"].items(): self.assertEqual(actual[key], value)
        self.assertEqual(actual["lyrics"], source()["lyrics"])
        with self.assertRaisesRegex(ValueError, "complete generation"):
            writer.written_generation(proposed, source())

    def test_invalid_controls_and_unknown_performance_never_become_a_draft(self):
        for field, value in (("steps", 0), ("cfg_scale", 21), ("cot", "off"),
                             ("performance_source", "a" * 32), ("refinement", {"imaginary_control": 4})):
            proposed = response(); proposed["generation"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                writer.written_generation(proposed, source())

    def test_queued_writing_keeps_output_scope_while_standalone_can_propose_a_new_one(self):
        proposed = response(max_seconds=120, render_mode="plan", title="New title", steps=23)
        queued = writer.written_generation(proposed, {**source(), "queued_generation": True})
        self.assertEqual((queued["max_seconds"], queued["render_mode"], queued["title"]), (18, "music", "Held title"))
        self.assertEqual(queued["steps"], 23)
        standalone = writer.written_generation(proposed, source())
        self.assertEqual((standalone["max_seconds"], standalone["render_mode"]), (120, "plan"))


class WriterContextTests(StudioFixture):
    def test_fresh_variation_keeps_score_when_optional_performance_is_missing(self):
        recipe = source()
        recipe["performance"] = {"frames": 300, "sha256": "0" * 64, "truncated": False}
        track_id = self.store.add_track(self.audio, recipe, {})
        generator = Generator(self.store)
        try:
            context = generator.writing_settings({**source(), "parent_track_id": track_id})
            self.assertEqual(context["reference"]["symbolic"]["supplied_abc"], SCORE)
            self.assertFalse(context["reference"]["performance_available"])
            self.assertNotIn("performance_track_id", context)
            with self.assertRaisesRegex(ValueError, "no saved performance"):
                generator.writing_settings({**source(), "performance_source": track_id})
        finally:
            generator.close()

    def test_variation_resolves_actual_library_scores_performance_and_review_notes(self):
        recipe = source()
        codes = self.audio.with_suffix(".codes.i32"); codes.write_bytes(bytes([7, 0, 0, 0]) * 300)
        recipe["symbolic_plan"] = {"abc": SCORE, "token_count": 91, "truncated": False}
        recipe["performance"] = {"frames": 300, "sha256": hashlib.sha256(codes.read_bytes()).hexdigest(), "truncated": False}
        track_id = self.store.add_track(self.audio, recipe, {})
        self.store.update_track(track_id, {"notes": "Keep the dry plucked opening; change the later entrance."})
        keys = FakeKeychain(); reviews = Reviews(self.store, keys, fake_listener)
        generator = Generator(self.store)
        try:
            reviews.configure({"enabled": True, "api_key": "test-secret", "model": "fixture/reviewer"})
            review = reviews.submit(track_id, {"focus": "Develop the ending"})
            wait_until(lambda: reviews.get(review["id"])["status"] == "done")
            context = generator.writing_settings({**source(), "parent_track_id": track_id})
            self.assertEqual(context["reference"]["symbolic"]["generated_abc"], SCORE)
            self.assertEqual(context["reference"]["inputs"]["refinement"], recipe["refinement"])
            self.assertIn("dry plucked opening", context["reference"]["notes"])
            self.assertIn("Strong entrance", context["reference"]["review"]["notes"])
            self.assertEqual(context["performance_track_id"], track_id)
            self.assertNotIn(str(self.root), json.dumps(context))
            self.assertNotIn("test-secret", json.dumps(context))
        finally:
            generator.close(); reviews.close()


if __name__ == "__main__": unittest.main()
