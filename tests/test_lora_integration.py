"""Exercise real adapter storage through the studio queue and HTTP boundary."""
import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from admission_fixture import FixtureAdmission
from test_loras import adapter_bytes
from test_studio import fixture_audio, recipe, wait_until
from review_recipe import FIELDS, response_schema, validate_generation
from studio import StudioServer
from studio_core import Generator, Store, validate_recipe
import run
from warm_engine import engine_key, job_from_command
from writer import written_generation


CAPS = {"lora": True, "warm_engine": False, "exact_score_replay": False, "acoustic_checkpoint": False}


class LoRAFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="riff-lora-integration-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "data", self.root / "outputs")
        self.addCleanup(self.store.loras.close)
        self.source = self.root / "a.safetensors"
        self.source.write_bytes(adapter_bytes())
        self.adapter = self.store.loras.import_local([self.source], metadata={"name": "Folk study", "license": "CC0"})
        self.capabilities = patch("run.engine_capabilities", return_value=CAPS)
        self.capabilities.start()
        self.addCleanup(self.capabilities.stop)

    def selected(self, **updates):
        return recipe(loras=[{"id": self.adapter["id"], "strength": .65}], **updates)


class LoRAIntegration(LoRAFixture):

    def test_invalid_selection_is_rejected_before_path_resolution(self):
        for value in ({}, [{"id": "../outside"}], [{"id": self.adapter["id"], "strength": float("nan")}],
                      [{"id": self.adapter["id"], "strength": 10 ** 1000}],
                      [{"id": self.adapter["id"], "strength": True}], [{"id": self.adapter["id"], "path": "/tmp/file"}]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_recipe(recipe(loras=value))

    def test_malformed_receipts_fail_before_library_resolution(self):
        pinned = self.store.prepare_loras(validate_recipe(self.selected()), bind=True)
        receipt = pinned["lora_provenance"][0]
        malformed = [None, {}, ["invalid"], [{**receipt, "id": []}], [{**receipt, "id": None}],
                     [{**receipt, "id": "../outside"}], [{**receipt, "stages": 1}],
                     [{**receipt, "stages": []}], [{**receipt, "stages": ["ar", "ar"]}],
                     [{**receipt, "stages": ["ar", []]}], [receipt, receipt], [receipt] * 3]
        with patch.object(self.store.loras, "resolve_saved", side_effect=AssertionError("Malformed receipt reached the library")):
            for value in malformed:
                payload = {**pinned, "lora_provenance": value}
                for validate in (validate_recipe, self.store.prepare_loras):
                    with self.subTest(value=value, validate=validate.__name__), self.assertRaisesRegex(ValueError, "saved LoRA"):
                        validate(payload)

    def test_queued_writer_preserves_pinned_adapters_and_strengths(self):
        pinned = self.store.prepare_loras(validate_recipe(self.selected()), bind=True)
        before = copy.deepcopy(pinned)
        for loras in ([], [{"id": self.adapter["id"], "ar_scale": -.2}]):
            for partial in (False, True):
                proposal = {key: pinned[key] for key in FIELDS}
                proposal.update(loras=loras, lyrics="[Verse]\nFresh words for the queued take.")
                if partial:
                    proposal = {key: proposal[key] for key in ("loras", "lyrics")}
                with self.subTest(loras=loras, partial=partial):
                    generated = written_generation({"generation": proposal},
                        {**pinned, "queued_generation": True}, partial=partial)
                    self.assertEqual(generated["loras"], pinned["loras"])
                    self.assertEqual(generated["lora_provenance"], pinned["lora_provenance"])
                    self.assertIn("Fresh words", generated["lyrics"])
                    # Exercise the actual writer-result merge at the queue boundary.
                    executable = self.store.prepare_loras(validate_recipe({**pinned, **generated}))
                    self.assertEqual(executable["lora_provenance"], pinned["lora_provenance"])
        self.assertEqual(pinned, before)

    def test_standalone_sound_holds_keep_adapters_but_sound_writing_can_change_them(self):
        pinned = self.store.prepare_loras(validate_recipe(self.selected()), bind=True)
        for loras in ([], [{"id": self.adapter["id"], "ar_scale": .2}]):
            proposal = {"generation": {"loras": loras, "lyrics": "[Verse]\nNew held-sound words."}}
            for control in ({"hold_sound": True}, {"write_scope": "words"}):
                with self.subTest(loras=loras, control=control):
                    generated = written_generation(proposal, {**pinned, **control}, partial=True)
                    self.assertEqual(generated["loras"], pinned["loras"])
                    self.assertEqual(generated["lora_provenance"], pinned["lora_provenance"])
                    self.assertIn("New held-sound words", generated["lyrics"])
            generated = written_generation(proposal, {**pinned, "write_scope": "sound", "hold_sound": True}, partial=True)
            self.assertEqual(generated["loras"], loras)
            self.assertEqual(generated["lyrics"], pinned["lyrics"])
            # An explicit standalone sound change becomes a new take.
            self.assertEqual(self.store.prepare_loras(generated, bind=True)["loras"], loras)

    def test_offered_acoustic_keeps_its_own_receipt_after_adapter_deletion(self):
        captured = self.store.prepare_loras(validate_recipe(self.selected()), bind=True)
        nar_file = self.root / "nar.safetensors"
        nar_file.write_bytes(adapter_bytes("nar"))
        nar = self.store.loras.import_local([nar_file], metadata={"name": "Current draft sound"})
        other_draft = self.store.prepare_loras(validate_recipe(recipe(loras=[{"id": nar["id"]}])), bind=True)
        reference = "riff-acoustic-v1:" + "f" * 64
        sound = {"id": reference, "title": "Captured style", "compatible": True, "inputs": captured}
        proposal = {key: captured[key] for key in FIELDS}
        proposal.update(acoustic_source=reference, title="A new decode", decoder={"core_frames": 32})
        self.store.loras.delete(self.adapter["id"])
        for draft in (validate_recipe(recipe()), other_draft):
            source = {**draft, "available_acoustics": [sound]}
            expected_ids = {self.adapter["id"], *[item["id"] for item in draft["loras"]]}
            schema = response_schema(source, False)["properties"]["generation"]["properties"]["loras"]
            with self.subTest(draft=draft["loras"]), patch.object(self.store.loras, "resolve_saved",
                    side_effect=AssertionError("Decoding a captured sound must not load adapters")):
                self.assertEqual(set(schema["items"]["properties"]["id"]["enum"]), expected_ids)
                self.assertEqual(schema["maxItems"], len(expected_ids))
                generated = validate_generation(proposal, source)
                self.assertEqual(generated["loras"], captured["loras"])
                self.assertEqual(generated["lora_provenance"], captured["lora_provenance"])
                self.assertEqual(self.store.prepare_loras(generated), generated)

    def test_offered_acoustic_adapter_permission_is_limited_to_its_exact_saved_inputs(self):
        captured = self.store.prepare_loras(validate_recipe(self.selected()), bind=True)
        reference = "riff-acoustic-v1:" + "f" * 64
        source = {**validate_recipe(recipe()), "available_acoustics": [
            {"id": reference, "compatible": True, "inputs": captured}]}
        proposal = {key: captured[key] for key in FIELDS}
        with self.assertRaisesRegex(ValueError, "LoRA.*not supplied"):
            validate_generation(proposal, source)
        for loras in ([], [{"id": self.adapter["id"], "ar_scale": .1}],
                      [{"id": "e" * 64, "ar_scale": .65}]):
            with self.subTest(loras=loras), self.assertRaisesRegex(ValueError, "changed its saved musical inputs"):
                validate_generation({**proposal, "acoustic_source": reference, "loras": loras}, source)

    def test_pin_survives_normalization_defaults_and_metadata_edits(self):
        pinned = self.store.prepare_loras(validate_recipe(self.selected()), bind=True)
        receipt = copy.deepcopy(pinned["lora_provenance"])
        self.store.loras.update(self.adapter["id"], {"name": "A renamed sound", "default_strength": .1, "license": "An edited label"})
        replay = self.store.prepare_loras(validate_recipe(pinned))
        self.assertEqual(replay["lora_provenance"], receipt)
        self.assertEqual(replay["loras"][0]["ar_scale"], .65)
        self.assertGreater(replay["lora_memory_bytes"], 0)
        options = self.store.loras.resolve_saved(replay["lora_provenance"])
        self.assertNotIn("nar_lora", options)

    def test_new_take_can_change_strength_but_execution_cannot(self):
        pinned = self.store.prepare_loras(validate_recipe(self.selected()), bind=True)
        changed = copy.deepcopy(pinned)
        changed["loras"][0]["ar_scale"] = -.2
        with self.assertRaisesRegex(ValueError, "strength has changed"):
            self.store.prepare_loras(changed)
        revised = self.store.prepare_loras(changed, bind=True)
        self.assertEqual(revised["lora_provenance"][0]["ar_scale"], -.2)
        self.assertEqual(revised["lora_provenance"][0]["files"], pinned["lora_provenance"][0]["files"])

    def test_zero_strength_and_decoder_do_not_load_adapter_weights(self):
        selected = self.selected()
        selected["loras"][0]["strength"] = 0
        pinned = self.store.prepare_loras(validate_recipe(selected), bind=True)
        options = self.store.loras.resolve_saved(pinned["lora_provenance"])
        self.assertEqual(options["memory_bytes"], 0)
        self.assertNotIn("ar_lora", options)
        # A completed acoustic checkpoint is sufficient to finish a recording.
        self.store.loras.delete(self.adapter["id"])
        pinned["acoustic_source"] = "riff-acoustic-v1:" + "f" * 64
        self.assertEqual(self.store.prepare_loras(pinned), pinned)

    def test_old_engine_refuses_selection_and_plain_generation_stays_available(self):
        with patch("run.engine_capabilities", return_value={}):
            with self.assertRaisesRegex(ValueError, "Update the music engine"):
                self.store.prepare_loras(validate_recipe(self.selected()), bind=True)
            self.assertEqual(self.store.prepare_loras(validate_recipe(recipe()), bind=True)["loras"], [])

    def test_zero_recorded_selection_bypasses_old_engine_but_still_verifies_identity(self):
        selected = self.selected()
        selected["loras"][0]["strength"] = 0
        with patch("run.engine_capabilities", return_value={}):
            pinned = self.store.prepare_loras(validate_recipe(selected), bind=True)
            self.assertEqual(pinned["lora_memory_bytes"], 0)
            self.assertEqual(pinned["loras"][0]["ar_scale"], 0)
            self.assertEqual(self.store.prepare_loras(validate_recipe(pinned)), pinned)
            changed = copy.deepcopy(pinned)
            changed["lora_provenance"][0]["files"][0]["sha256"] = "f" * 64
            with self.assertRaisesRegex(ValueError, "provenance differs"):
                self.store.prepare_loras(changed)
            directory = self.store.loras.root / self.adapter["id"]
            with (directory / "ar.safetensors").open("ab") as output:
                output.write(b"changed")
            with self.assertRaisesRegex(ValueError, "changed or is corrupt"):
                self.store.prepare_loras(pinned)

    def test_native_arguments_and_warm_identity_include_adapter_files(self):
        pinned = self.store.prepare_loras(validate_recipe(self.selected()), bind=True)
        options = self.store.loras.resolve_saved(pinned["lora_provenance"])
        command = run.build_command(lyrics="", style="folk", max_seconds=2, steps=2, cot="off", seed=42,
                                    threads=1, output=self.root / "out.wav", lora_options=options)
        self.assertIn("yue2.ar_lora=" + options["ar_lora"], command)
        self.assertIn("yue2.ar_lora_scale=0.65", command)
        session, job = job_from_command(command)
        self.assertFalse(any("lora" in key for key in job["options"]))
        first = engine_key(session)
        with Path(options["ar_lora"]).open("ab") as output:
            output.write(b"changed")
        self.assertNotEqual(first, engine_key(session))
        with self.assertRaises(ValueError):
            self.store.loras.resolve_saved(pinned["lora_provenance"])

    def test_queue_rechecks_after_waiting_and_retry_rejects_changed_weights(self):
        admission = FixtureAdmission()
        admission.gates["native"].clear()
        spawned = []
        audio = self.root / "take.wav"
        fixture_audio(audio)
        def command(queued, output):
            spawned.append(queued)
            return [sys.executable, "-c", "import shutil,sys;shutil.copyfile(sys.argv[1],sys.argv[2])", str(audio), str(output)]
        generator = Generator(self.store, command_builder=command, admission=admission)
        self.addCleanup(generator.close)
        queued = generator.submit(self.selected())
        wait_until(lambda: bool(admission.entries))
        with self.assertRaisesRegex(ValueError, "queued or running"):
            self.store.loras.delete(self.adapter["id"], confirm=True)
        file = self.store.loras.resolve_saved(queued["recipe"]["lora_provenance"])["ar_lora"]
        with Path(file).open("r+b") as stream:
            stream.seek(-1, 2); stream.write(b"\xff")
        admission.gates["native"].set()
        wait_until(lambda: self.store.job(queued["id"])["status"] == "failed")
        self.assertFalse(spawned)
        self.assertIn("changed", self.store.job(queued["id"])["error"])
        with self.assertRaisesRegex(ValueError, "changed"):
            generator.submit(queued["recipe"])


class LoRAHTTP(LoRAFixture):
    def setUp(self):
        super().setUp()
        self.generator = Generator(self.store, admission=FixtureAdmission())
        self.addCleanup(self.generator.close)
        self.server = StudioServer(("127.0.0.1", 0), self.store, self.generator)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, path, method="GET", data=None, raw=False):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        headers = {"X-Riff-Request": "1"}
        if data is not None:
            headers["Content-Type"] = "application/octet-stream" if raw else "application/json"
        body = data if raw or data is None else json.dumps(data)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.headers), json.loads(response.read())
        connection.close()
        return result

    def test_raw_import_route_and_private_path_rejection(self):
        status, headers, item = self.request("/api/loras/import?name=sound.safetensors", "POST", adapter_bytes("nar"), raw=True)
        self.assertEqual(status, 201, item)
        self.assertEqual(item["stages"], ["nar"])
        self.assertEqual(item["files"][0]["original_name"], "sound.safetensors")
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        code, _, error = self.request("/api/loras/import?name=../../outside.safetensors", "POST", adapter_bytes(), raw=True)
        self.assertEqual(code, 400, error)
        code, _, error = self.request("/api/loras/import?name=model.pt", "POST", b"pickle", raw=True)
        self.assertEqual(code, 400, error)

    def test_list_edit_inspect_delete_and_capability_schema(self):
        status, _, result = self.request("/api/loras")
        self.assertEqual(status, 200)
        self.assertTrue(result["enabled"])
        id = self.adapter["id"]
        self.assertEqual(result["loras"][0]["id"], id)
        self.assertEqual(self.request(f"/api/loras/{id}", "PATCH", {"name": "New name"})[2]["name"], "New name")
        self.assertEqual(self.request(f"/api/loras/{id}")[2]["references"], [])
        self.assertFalse(self.request(f"/api/loras/{id}/updates")[2]["available"])
        schema = self.request("/api/capabilities")[2]
        self.assertIn("loras", schema["recipe_schema"]["properties"])
        self.assertIn("lora_install", schema["operations"])
        self.assertTrue(self.request(f"/api/loras/{id}", "DELETE", {})[2]["deleted"])

    def test_malformed_receipt_returns_json_error_without_queueing(self):
        for receipt in ([{"id": [], "stages": ["ar"]}], [{"id": self.adapter["id"], "stages": 1}]):
            with self.subTest(receipt=receipt), patch("studio.platform_support.readiness", return_value={"ready": True}):
                code, _, error = self.request("/api/generations", "POST", self.selected(lora_provenance=receipt))
                self.assertEqual(code, 400, error)
                self.assertIn("saved LoRA", error["error"])
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
