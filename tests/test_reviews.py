import sys
import json
import subprocess
import sys
import threading
import unittest
import uuid
from unittest.mock import patch

from keychain import Keychain
from review_client import listen
from review_recipe import FIELDS, recommended_generation
from studio_core import validate_recipe
from reviews import Reviews
from studio import StudioServer
import test_studio
from test_studio import StudioFixture, recipe, wait_until


class FakeKeychain:
    def __init__(self):
        self.value = None

    def exists(self):
        return self.value is not None

    def get(self):
        if self.value is None:
            raise ValueError("Add a review key.")
        return self.value

    def set(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Enter a key.")
        self.value = value.strip()

    def delete(self):
        self.value = None


def fake_listener(output):
    script = """
import json,sys,time
from pathlib import Path
s=json.load(sys.stdin)
if s['focus']=='wait': time.sleep(300)
if s['focus']=='fail': result={'error':'Provider rejected '+s['api_key']}
else:
 from studio_core import validate_recipe
 from review_recipe import FIELDS
 take=validate_recipe({**s['recipe'], 'title':'Another take',
   'style':'Congas and a whispered chorus', 'lyrics':s['recipe'].get('lyrics','') if s['keep_lyrics'] else 'Fresh words',
   'mode':'lyrics', 'steps':37, 'max_seconds':26, 'seed':'1729', 'cfg_scale':1.6,
   'temperature':0.85, 'cot':'full', 'abc':'X:1\\nM:4/4\\nL:1/8\\nQ:1/4=108\\nK:Dm\\nD2 F2 A2 G2|F2 E2 D4|',
   'refinement':{'semantic_top_p':0.82,'abc_temperature':0.65}})
 if s['focus']=='reuse': take['performance_source']=s['recipe']['performance_track_id']
 if s['focus']=='compose': take['render_mode']='plan'
 result={'notes':'0:00 Strong entrance. '+s['api_key'], 'summary':'Bring the crowd forward.',
         'generation':{key:take[key] for key in FIELDS},'usage':{'cost':.01}}
Path(sys.argv[1]).write_text(json.dumps(result))
"""
    return [sys.executable, "-c", script, str(output)]


class ReviewTests(StudioFixture):
    def setUp(self):
        super().setUp()
        self.track_id = self.store.add_track(self.audio, recipe(steps=19, max_seconds=420), {})
        self.keys = FakeKeychain()
        self.reviews = Reviews(self.store, self.keys, fake_listener)

    def tearDown(self):
        self.reviews.close()
        super().tearDown()

    def connect(self):
        return self.reviews.configure({"enabled": True, "api_key": "test-secret-value", "model": "custom/audio-model"})

    def test_settings_store_only_nonsecret_preferences_and_removal_disconnects(self):
        with self.assertRaises(ValueError):
            self.reviews.submit(self.track_id, {})
        with self.assertRaises(ValueError):
            self.reviews.configure({"enabled": True})
        connection = self.connect()
        self.assertEqual(connection["model"], "custom/audio-model")
        self.assertNotIn("api_key", connection)
        self.reviews.configure({"api_key": "replacement-secret"})
        self.assertEqual(self.keys.get(), "replacement-secret")
        self.reviews.configure({"model": "another/audio-model"})
        self.assertEqual(self.keys.get(), "replacement-secret")
        with self.store.db() as db:
            dump = "\n".join(db.iterdump())
        self.assertNotIn("secret", dump)
        self.assertEqual(self.reviews.remove_key()["has_key"], False)
        self.assertFalse(self.reviews.settings()["enabled"])

    def test_notes_keep_the_source_recipe_and_lyric_policy_without_leaking_credentials(self):
        self.connect()
        review = self.reviews.submit(self.track_id, {"keep_lyrics": True})
        wait_until(lambda: self.reviews.get(review["id"])["status"] == "done")
        result = self.reviews.get(review["id"])
        self.assertNotIn("lyrics", result["revision"])
        self.assertEqual(result["generation"]["steps"], 37)
        self.assertEqual(result["generation"]["max_seconds"], 26)
        self.assertEqual(result["generation"]["cfg_scale"], 1.6)
        self.assertEqual(result["generation"]["refinement"]["semantic_top_p"], 0.82)
        self.assertIn("K:Dm", result["generation"]["abc"])
        self.assertEqual(result["generation"]["lyrics"], recipe()["lyrics"])
        self.assertEqual(result["source_recipe"]["steps"], 19)
        self.assertEqual(result["source_recipe"]["max_seconds"], 420)
        self.assertEqual(result["source_recipe"]["lyrics"], recipe()["lyrics"])
        self.assertNotIn("test-secret-value", json.dumps(result))
        revision = self.reviews.submit(self.track_id, {"keep_lyrics": False})
        wait_until(lambda: self.reviews.get(revision["id"])["status"] == "done")
        self.assertEqual(self.reviews.get(revision["id"])["revision"]["lyrics"], "Fresh words")
        self.assertEqual(self.store.track(self.track_id)["recipe"]["lyrics"], recipe()["lyrics"])
        with self.store.db() as db:
            self.assertNotIn("test-secret-value", "\n".join(db.iterdump()))

    def test_cancellation_reaps_request_and_queue_continues(self):
        self.connect()
        first = self.reviews.submit(self.track_id, {"focus": "wait"})
        wait_until(lambda: self.reviews.process is not None)
        pid = self.reviews.process.pid
        second = self.reviews.submit(self.track_id, {})
        self.reviews.cancel(first["id"])
        wait_until(lambda: self.reviews.get(second["id"])["status"] == "done")
        self.assertEqual(self.reviews.get(first["id"])["status"], "cancelled")
        self.assertEqual(subprocess.run(["/bin/ps", "-p", str(pid)], capture_output=True).returncode, 1)

    def test_removing_key_stops_active_and_queued_reviews(self):
        self.connect()
        first = self.reviews.submit(self.track_id, {"focus": "wait"})
        wait_until(lambda: self.reviews.process is not None)
        second = self.reviews.submit(self.track_id, {})
        self.reviews.remove_key()
        wait_until(lambda: self.reviews.get(first["id"])["status"] == "cancelled")
        self.assertEqual(self.reviews.get(second["id"])["status"], "cancelled")
        self.assertFalse(self.keys.exists())

    def test_provider_failure_redacts_key_and_next_review_finishes(self):
        self.connect()
        failed = self.reviews.submit(self.track_id, {"focus": "fail"})
        next_review = self.reviews.submit(self.track_id, {})
        wait_until(lambda: self.reviews.get(next_review["id"])["status"] == "done")
        result = self.reviews.get(failed["id"])
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("test-secret-value", result["error"])

    def test_close_and_restart_preserve_notes_and_report_interruption(self):
        self.connect()
        done = self.reviews.submit(self.track_id, {})
        wait_until(lambda: self.reviews.get(done["id"])["status"] == "done")
        active = self.reviews.submit(self.track_id, {"focus": "wait"})
        wait_until(lambda: self.reviews.process is not None)
        self.reviews.close()
        self.reviews = Reviews(self.store, self.keys, fake_listener)
        self.assertEqual(self.reviews.get(active["id"])["status"], "interrupted")
        self.assertIn("Strong entrance", self.reviews.get(done["id"])["notes"])

    def test_model_catalog_filters_for_audio_without_restricting_manual_ids(self):
        from io import BytesIO
        catalog = {"data": [{"id": "hears/audio", "name": "Listener", "architecture": {
            "input_modalities": ["text", "audio"], "output_modalities": ["text"]}},
            {"id": "sees/images", "name": "Vision", "architecture": {
                "input_modalities": ["image"], "output_modalities": ["text"]}}]}
        with patch("reviews.urllib.request.urlopen", return_value=BytesIO(json.dumps(catalog).encode())):
            self.assertEqual(self.reviews.models(), [{"id": "hears/audio", "name": "Listener"}])
        self.assertEqual(self.reviews.configure({"model": "my/custom-audio-model"})["model"], "my/custom-audio-model")


class ReviewClientTests(unittest.TestCase):
    def source(self):
        return {**recipe(steps=19, max_seconds=28, cfg_scale=1.4, temperature=.9,
                         refinement={"semantic_top_p":.85}),
                "symbolic_plan":{"abc":"X:1\nM:4/4\nL:1/8\nK:Dm\nD2 F2 A4|",
                                 "truncated":False,"token_count":42}}

    def proposal(self, keep=True, **updates):
        take = validate_recipe(self.source())
        take.update(title="A new procession", style="Yemeni chant and Korean brass choir",
                    abc=self.source()["symbolic_plan"]["abc"], cot="full", steps=37,
                    lyrics=take["lyrics"] if keep else "새로운 노래", **updates)
        return {"notes":"0:04 The low brass disappears beneath the voices.",
                "summary":"Carry the choir with a clearer brass pulse.",
                "generation":{key:take[key] for key in FIELDS}}

    def request(self, response_text, keep=True, finish="stop", source=None):
        from io import BytesIO
        from pathlib import Path
        captured = {}
        def encode(command, **kwargs):
            self.assertNotIn("test-secret", " ".join(command))
            Path(command[-1]).write_bytes(b"mp3 fixture")
        def respond(request, **kwargs):
            captured.update(json.loads(request.data))
            self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
            return BytesIO(json.dumps({"choices":[{"message":{"content":response_text}, "finish_reason":finish}], "usage":{"cost":0}}).encode())
        settings = {"recipe":self.source() if source is None else source,"keep_lyrics":keep,"focus":"Make it stranger.",
                    "model":"google/gemini-3.8-flash","api_key":"test-secret","audio":"fixture.wav","duration":27.52}
        with patch("review_client.shutil.which",return_value="ffmpeg"), \
                patch("review_client.subprocess.run", side_effect=encode), \
                patch("review_client.urllib.request.urlopen", side_effect=respond):
            result = listen(settings)
        return result,captured

    def test_audio_prior_controls_and_native_plan_produce_a_runnable_take(self):
        result, request = self.request(json.dumps(self.proposal()))
        self.assertEqual(request["model"], "google/gemini-3.8-flash")
        self.assertTrue(request["provider"]["require_parameters"])
        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertTrue(request["response_format"]["json_schema"]["strict"])
        schema=request["response_format"]["json_schema"]["schema"]["properties"]["generation"]
        self.assertEqual(set(schema["required"]), set(FIELDS))
        content=request["messages"][0]["content"]
        self.assertEqual(content[1]["type"], "input_audio")
        self.assertEqual(content[1]["input_audio"]["format"], "mp3")
        for text in ('"steps": 19', '"cfg_scale": 1.4', '"semantic_top_p": 0.85', 'generated_abc', 'K:Dm', '27.52 seconds'):
            self.assertIn(text,content[0]["text"])
        self.assertEqual(validate_recipe(result["generation"])["steps"],37)
        self.assertEqual(result["generation"]["lyrics"], self.source()["lyrics"])

    def test_arranged_audio_sends_each_source_score_and_musical_edits(self):
        first = {**self.source(), "lyrics": "First call", "private_setting": "private-sentinel"}
        second = {**self.source(), "lyrics": "Second answer", "cfg_scale": 1.7,
                  "symbolic_plan": {"abc": "X:2\nK:Gm\nG2 B2 d4|", "truncated": True, "token_count": 31}}
        for recipes in ({"first": first, "second": second}, [first, second]):
            with self.subTest(storage=type(recipes).__name__):
                source = {**self.source(), "arrangement": {"sources": ["first", "second"],
                    "source_recipes": recipes, "source_scores": {"score-only": "X:3\nK:Dm\nD4|"},
                    "clips": [{"track_id": "second", "timeline_start": 12.5, "start": 2, "end": 9,
                               "speed": .95, "kind": "chorus", "file": "private-file-sentinel",
                               "filters": "private-processing-sentinel"}]}}
                result, request = self.request(json.dumps(self.proposal()), source=source)
                prompt = request["messages"][0]["content"][0]["text"]
                context = json.loads(prompt.split("SYMBOLIC REPRESENTATION\n", 1)[1])["arrangement"]
                by_id = {item["track_id"]: item for item in context["sources"]}
                self.assertEqual(by_id["first"]["inputs"]["lyrics"], "First call")
                self.assertEqual(by_id["second"]["inputs"]["cfg_scale"], 1.7)
                self.assertIn("K:Gm", by_id["second"]["symbolic"]["generated_abc"])
                self.assertTrue(by_id["second"]["symbolic"]["generated_plan_truncated"])
                self.assertIn("K:Dm", by_id["score-only"]["symbolic"]["source_abc"])
                self.assertEqual(context["clips"], [{"track_id": "second", "timeline_start": 12.5,
                    "start": 2, "end": 9, "speed": .95, "kind": "chorus"}])
                for private in ("private-sentinel", "private-file-sentinel", "private-processing-sentinel"):
                    self.assertNotIn(private, prompt)
                self.assertEqual(validate_recipe(result["generation"])["steps"], 37)

    def test_lyric_choice_is_explicit_and_custom_settings_are_retained(self):
        changed,_ = self.request(json.dumps(self.proposal(keep=False)),keep=False)
        self.assertEqual(changed["generation"]["lyrics"], "새로운 노래")
        value=self.proposal();value["generation"]["steps"]=999
        accepted,_=self.request(json.dumps(value))
        self.assertEqual(accepted["generation"]["steps"],999)
        kept,_ = self.request(json.dumps(self.proposal(keep=False)))
        self.assertEqual(kept["generation"]["lyrics"], self.source()["lyrics"])

    def test_lyric_lock_keeps_bilingual_words_and_notes_across_both_validation_steps(self):
        source = {**self.source(), "lyrics": "\n[Caller]\nمَنْ شَادَ الْمَوَانِئَ؟\n[Choir]\n우리가 세웠다!\n"}
        for returned_lyrics in ("", "Unrequested replacement", source["lyrics"].strip()):
            with self.subTest(returned_lyrics=returned_lyrics):
                proposal = self.proposal()
                proposal["generation"]["lyrics"] = returned_lyrics
                result, request = self.request(json.dumps(proposal), source=source)
                final = recommended_generation(result["generation"], source, True)
                self.assertEqual(final["lyrics"], source["lyrics"].strip())
                self.assertEqual(result["notes"], proposal["notes"])
                self.assertEqual(final["steps"], 37)
                schema = request["response_format"]["json_schema"]["schema"]["properties"]["generation"]
                self.assertNotIn("const", schema["properties"]["lyrics"])

    def test_incomplete_or_invalid_recommendations_cannot_become_takes(self):
        invalid=["Try more brass.", '```json\n{}\n```', json.dumps({"notes":"A note","summary":"An idea"})]
        for change in ({"cfg_scale":21},{"abc":"X:1\nK:Dm\nD2|","cot":"off"},{"steps":True}):
            value=self.proposal();value["generation"].update(change);invalid.append(json.dumps(value))
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(ValueError):self.request(value)
        with self.assertRaisesRegex(ValueError,"cut off"):
            self.request(json.dumps(self.proposal()),finish="length")


class ReviewHttpTests(StudioFixture):
    request = test_studio.HttpTests.request

    def setUp(self):
        super().setUp()
        self.track_id = self.store.add_track(self.audio, recipe(), {})
        self.keys = FakeKeychain()
        self.reviews = Reviews(self.store, self.keys, fake_listener)
        self.server = StudioServer(("127.0.0.1", 0), self.store, None, self.reviews)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.reviews.close()
        super().tearDown()

    def test_settings_and_reviews_are_same_origin_and_never_return_a_key(self):
        headers = {"Content-Type": "application/json", "X-Riff-Request": "1"}
        payload = json.dumps({"enabled": True, "api_key": "http-secret"})
        self.assertEqual(self.request("POST", "/api/review/settings", payload,
                                      dict(headers, Origin="https://foreign.example"))[0], 403)
        self.assertFalse(self.keys.exists())
        status, _, body = self.request("POST", "/api/review/settings", payload, headers)
        self.assertEqual(status, 200)
        self.assertNotIn(b"http-secret", body)
        self.assertNotIn(b"http-secret", self.request("GET", "/api/review/settings")[2])
        self.assertEqual(self.request("GET", "/api/review/settings", headers={"Host": "foreign.example"})[0], 403)
        status, _, body = self.request("POST", f"/api/tracks/{self.track_id}/reviews", "{}", headers)
        self.assertEqual(status, 201)
        review_id = json.loads(body)["id"]
        wait_until(lambda: self.reviews.get(review_id)["status"] == "done")
        result = self.request("GET", f"/api/reviews/{review_id}")[2]
        self.assertNotIn(b"http-secret", result)
        self.assertEqual(self.request("DELETE", "/api/review/key", "{}", headers)[0], 200)
        self.assertFalse(self.keys.exists())


@unittest.skipUnless(sys.platform == "darwin", "Requires macOS Keychain")
class KeychainIntegrationTests(unittest.TestCase):
    def test_native_keychain_roundtrip_replacement_and_removal(self):
        keys = Keychain(service="Riff test " + uuid.uuid4().hex)
        try:
            self.assertFalse(keys.exists())
            keys.set("temporary-test-credential")
            self.assertTrue(keys.exists())
            self.assertEqual(keys.get(), "temporary-test-credential")
            keys.set("replaced-test-credential")
            self.assertEqual(keys.get(), "replaced-test-credential")
            keys.delete()
            self.assertFalse(keys.exists())
        finally:
            keys.delete()
