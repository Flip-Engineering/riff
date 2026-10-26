"""Isolated owner-service and fake HTTP provider; never paid listening or generation."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from keychain import SecretService, secret_service_environment
from reviews import Reviews
from studio import StudioServer
from studio_core import validate_recipe
from review_recipe import FIELDS
from scripts import review_master
from test_reviews import FakeKeychain
import test_studio
from test_studio import StudioFixture, recipe, wait_until


HEADERS = {"Content-Type": "application/json", "X-Riff-Request": "1"}


class MasterHttpTests(StudioFixture):
    request = test_studio.HttpTests.request

    def setUp(self):
        super().setUp()
        self.track_id = self.store.add_track(self.audio, recipe(), {})
        self.original = self.store.track(self.track_id), self.audio.read_bytes()
        self.calls, self.response_code = [], 200
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        take = validate_recipe(recipe(seed="42", style="Reviewed arrangement"))
        self.response = {"id": "provider-receipt", "model": "fixture/actual-model", "usage": {"cost": .01},
            "complete_provider_metadata": {"nested": "fixture-secret"},
            "choices": [{"message": {"content": json.dumps({"notes": "0:00 Edited entrance. fixture-secret",
                "summary": "Keep the artist's edit.", "generation": {key: take[key] for key in FIELDS}})}, "finish_reason": "stop"}]}
        fixture = self
        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                fixture.calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                fixture.entered.set()
                fixture.release.wait(10)
                body = json.dumps(fixture.response).encode()
                self.send_response(fixture.response_code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *args):
                pass
        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        self.provider_thread = threading.Thread(target=self.provider.serve_forever, daemon=True)
        self.provider_thread.start()
        provider_url = f"http://127.0.0.1:{self.provider.server_port}"
        def command(output):
            # A real worker subprocess and HTTP request, with only media/provider doubles.
            script = '''
import base64,hashlib,json,sys
from pathlib import Path
import review_client as client
settings=json.load(sys.stdin)
original=client.urllib.request.urlopen
def provider(request,**kwargs):
 assert request.full_url == 'https://openrouter.ai/api/v1/chat/completions'
 request.full_url=sys.argv[2]
 return original(request,**kwargs)
client.urllib.request.urlopen=provider
client.shutil.which=lambda name: '/fixture/ffmpeg'
def encode(source,*args):
 data=b'fixture-mp3:'+Path(source).read_bytes()
 return {'data':base64.b64encode(data).decode(),'format':'mp3',
         'sent':{'format':'mp3','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}}
client.encode_mp3=encode
try: result=client.listen(settings)
except Exception as error:
 result={'error':str(error)}
 if isinstance(error,client.ProviderResponseError):
  result.update(provider_response=error.response,sent_audio=error.sent)
Path(sys.argv[1]).write_text(json.dumps(result).replace(settings['api_key'],'[redacted]'))
'''
            return [sys.executable, "-c", script, str(output), provider_url]
        self.keys = FakeKeychain()
        self.reviews = Reviews(self.store, self.keys, command)
        self.reviews.configure({"enabled": True, "api_key": "fixture-secret", "model": "fixture/default"})
        self.probe = patch.object(self.reviews, "_probe", return_value=("wav", 1.25))
        self.probe.start()
        self.server = StudioServer(("127.0.0.1", 0), self.store, None, self.reviews)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.master_bytes = b"artist edited primary master; distinct from generated audio"
        self.sha = hashlib.sha256(self.master_bytes).hexdigest()
        self.payload = {"request_id": "artist.edit.001", "master": {"sha256": self.sha,
            "bytes": len(self.master_bytes), "duration": 1.25}, "source_recipe": recipe(),
            "edit_context": {"timeline": [{"source": "artist take", "cut": [2, 4]}], "instruction": "Retain this transition."},
            "source_track_id": self.track_id, "model": "fixture/artist-selected", "focus": "Judge this edit", "keep_lyrics": True}

    def tearDown(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.reviews.close()
        self.probe.stop()
        self.provider.shutdown()
        self.provider.server_close()
        self.provider_thread.join()
        super().tearDown()

    def upload(self, headers=None, data=None, sha=None):
        return self.request("POST", "/api/review/masters?sha256=" + (sha or self.sha),
            self.master_bytes if data is None else data,
            {"Content-Type": "application/octet-stream", "X-Riff-Request": "1", **(headers or {})})

    def submit(self, payload=None):
        status, _, body = self.request("POST", "/api/reviews", json.dumps(payload or self.payload), HEADERS)
        self.assertEqual(status, 201, body)
        return json.loads(body)

    def finish(self, review):
        wait_until(lambda: self.reviews.get(review["id"])["status"] in ("done", "failed", "cancelled"))
        return self.reviews.get(review["id"])

    def test_primary_master_exact_transport_context_receipts_and_original_preservation(self):
        status, _, body = self.upload()
        self.assertEqual(status, 201, body)
        self.assertEqual(json.loads(body), {**self.payload["master"], "format": "wav"})
        result = self.finish(self.submit())
        self.assertEqual(result["status"], "done", result["error"])
        self.assertEqual(result["source_recipe"], self.payload["source_recipe"])
        self.assertEqual(result["edit_context"], self.payload["edit_context"])
        self.assertEqual(result["model"], "fixture/artist-selected")
        self.assertEqual(result["references"], [])
        self.assertEqual(result["track_id"], "")
        self.assertEqual(result["source_track_id"], self.track_id)
        self.assertEqual(self.reviews.list(self.track_id), [])
        self.assertEqual(self.reviews.references()["references"], [])
        self.assertEqual(len(self.calls), 1)
        sent = b"fixture-mp3:" + self.master_bytes
        parts = self.calls[0]["messages"][0]["content"]
        self.assertEqual(base64.b64decode(parts[1]["input_audio"]["data"]), sent)
        self.assertEqual(len(parts), 2)
        self.assertIn("PRIMARY EDITED MASTER", parts[0]["text"])
        self.assertIn("Retain this transition.", parts[0]["text"])
        self.assertIn("Judge this edit", parts[0]["text"])
        self.assertEqual(self.calls[0]["model"], self.payload["model"])
        self.assertEqual(result["sent_audio"], [{"source": "edited_master", "format": "mp3", "bytes": len(sent),
            "sha256": hashlib.sha256(sent).hexdigest()}])
        self.assertEqual(result["result"]["provider_response"]["complete_provider_metadata"], {"nested": "[redacted]"})
        self.assertEqual(result["result"]["response_id"], "provider-receipt")
        self.assertNotIn("fixture-secret", json.dumps(result))
        self.assertEqual((self.store.track(self.track_id), self.audio.read_bytes()), self.original)
        self.assertNotIn(str(self.store.data_root), parts[0]["text"])

    def test_idempotent_concurrent_retry_completed_replay_conflict_and_persistence(self):
        self.upload()
        ids = []
        threads = [threading.Thread(target=lambda: ids.append(self.submit()["id"])) for _ in range(5)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 1)
        self.finish({"id": ids[0]})
        self.assertEqual(self.submit()["id"], ids[0])
        for field, changed in (("focus", "another focus"), ("model", "another/model"), ("keep_lyrics", False),
                               ("edit_context", {"new": "cut"}), ("source_recipe", recipe(seed="43"))):
            status, _, _ = self.request("POST", "/api/reviews", json.dumps({**self.payload, field: changed}), HEADERS)
            self.assertEqual(status, 400)
        self.reviews.close()
        restored = Reviews(self.store, self.keys)
        try:
            self.assertEqual(restored.submit_master(self.payload)["id"], ids[0])
        finally:
            restored.close()
        self.assertEqual(len(self.calls), 1)

    def test_queue_cancellation_and_failed_requests_never_automatically_resend(self):
        self.upload()
        self.release.clear()
        first = self.submit()
        self.assertTrue(self.entered.wait(5))
        queued = self.submit({**self.payload, "request_id": "artist.edit.002"})
        self.assertEqual(queued["status"], "queued")
        status, _, body = self.request("POST", f"/api/reviews/{queued['id']}/cancel", "{}", HEADERS)
        self.assertEqual((status, json.loads(body)["status"]), (200, "cancelled"))
        self.request("POST", f"/api/reviews/{first['id']}/cancel", "{}", HEADERS)
        self.assertEqual(self.finish(first)["status"], "cancelled")
        self.release.set()
        self.assertEqual(self.submit()["status"], "cancelled")
        self.assertEqual(len(self.calls), 1)
        self.response_code, self.response = 402, {"error": {"message": "fixture-secret declined"}}
        failed = self.finish(self.submit({**self.payload, "request_id": "artist.edit.003"}))
        self.assertEqual(failed["status"], "failed")
        self.assertIn("[redacted]", failed["error"])
        self.assertEqual(failed["result"]["provider_response"]["http_status"], 402)
        self.assertNotIn("fixture-secret", json.dumps(failed))
        self.submit({**self.payload, "request_id": "artist.edit.003"})
        self.assertEqual(len(self.calls), 2)

    def test_missing_changed_invalid_inputs_and_session_failure_precede_provider_contact(self):
        self.assertEqual(self.request("POST", "/api/reviews", json.dumps(self.payload), HEADERS)[0], 400)
        self.assertEqual(self.upload(data=b"changed")[0], 400)
        self.assertEqual(self.upload(data=b"")[0], 400)
        self.upload()
        for patch_payload in ({"audio": "/etc/hostname"}, {"master": {"path": "/etc/hostname"}},
                              {"request_id": ""}, {"keep_lyrics": "true"}, {"master": {**self.payload["master"], "duration": 9}}):
            self.assertEqual(self.request("POST", "/api/reviews", json.dumps({**self.payload, **patch_payload}), HEADERS)[0], 400)
        with self.reviews.lock:
            queued = self.reviews.submit_master(self.payload)
            self.reviews.master_path(self.sha).write_bytes(b"corrupted after queue admission")
        failed = self.finish(queued)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("missing or changed", failed["error"])
        self.upload()
        with patch.object(self.keys, "get", side_effect=ValueError("Owner keyring session unavailable")):
            failed = self.finish(self.submit({**self.payload, "request_id": "session-error"}))
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.calls, [])

    def test_safe_request_checks_and_standalone_master_without_library_import(self):
        self.assertEqual(self.upload(headers={"X-Riff-Request": "0"})[0], 403)
        self.assertEqual(self.upload(headers={"Origin": "https://unrelated.invalid"})[0], 403)
        self.assertEqual(self.upload(headers={"Content-Type": "text/plain"})[0], 403)
        self.assertEqual(self.request("POST", "/api/reviews", json.dumps(self.payload), {"Content-Type": "application/json"})[0], 403)
        self.upload()
        result = self.finish(self.submit({**self.payload, "source_track_id": "", "keep_lyrics": False}))
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["track_id"], "")
        self.assertFalse(result["keep_lyrics"])
        self.assertEqual(self.store.track(self.track_id), self.original[0])

    def test_partial_provider_result_is_complete_redacted_and_not_retried(self):
        self.upload()
        self.response["choices"][0]["finish_reason"] = "length"
        failed = self.finish(self.submit())
        self.assertEqual(failed["status"], "failed")
        self.assertIn("cut off", failed["error"])
        self.assertEqual(failed["result"]["provider_response"]["choices"][0]["finish_reason"], "length")
        self.assertEqual(failed["sent_audio"][0]["source"], "edited_master")
        self.assertNotIn("fixture-secret", json.dumps(failed))
        self.assertEqual(self.submit()["id"], failed["id"])
        self.assertEqual(len(self.calls), 1)

    def test_maintained_helper_local_prepare_and_retry_retains_complete_result(self):
        audio = self.root / "edited.wav"
        audio.write_bytes(self.master_bytes)
        spec = {**self.payload, "master": str(audio), "master_sha256": self.sha,
                "master_bytes": len(self.master_bytes), "duration": 1.25}
        manifest, output = self.root / "request.json", self.root / "receipt.json"
        manifest.write_text(json.dumps(spec))
        args = ["--request", str(manifest), "--request-id", "helper.artist.001", "--studio",
                f"http://127.0.0.1:{self.server.server_port}", "--output", str(output)]
        review_master.main(args)
        self.assertEqual(self.calls, [])
        review_master.main(args + ["--submit", "--wait"])
        saved = json.loads(output.read_text())
        self.assertEqual(saved["review"]["status"], "done")
        review_master.main(args + ["--submit", "--wait"])
        self.assertEqual(json.loads(output.read_text())["review"], saved["review"])
        self.assertEqual(len(self.calls), 1)
        manifest.write_text(json.dumps({**spec, "focus": "changed"}))
        with self.assertRaisesRegex(ValueError, "existing receipt"):
            review_master.main(args + ["--submit"])


class OwnerSessionTests(unittest.TestCase):
    def metadata(self, path):
        path = str(path)
        return SimpleNamespace(st_uid=999 if "wrong-owner" in path else 2026,
            st_mode=(stat.S_IFSOCK | 0o600) if path.endswith("bus") else (stat.S_IFDIR | 0o700))

    def test_missing_values_derive_from_actual_effective_uid_and_only_child_environment_changes(self):
        with patch.dict(os.environ, {}, clear=True), patch("os.geteuid", return_value=2026), \
                patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir="/actual/account")), \
                patch("keychain.Path.stat", autospec=True, side_effect=self.metadata), \
                patch("keychain.shutil.which", return_value="/fixture/secret-tool"), \
                patch("keychain.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="fake-key", stderr="")) as run:
            self.assertEqual(SecretService().get(), "fake-key")
            env = run.call_args.kwargs["env"]
            self.assertEqual(env["HOME"], "/actual/account")
            self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/2026")
            self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/2026/bus")
            self.assertNotIn("HOME", os.environ)

    def test_explicit_owned_nonstandard_session_is_preserved_wrong_owner_refused(self):
        explicit = {"HOME": "/custom/home", "XDG_RUNTIME_DIR": "/custom/runtime",
                    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/custom/session-bus"}
        with patch("os.geteuid", return_value=2026), patch("keychain.Path.stat", autospec=True, side_effect=self.metadata):
            with patch.dict(os.environ, explicit, clear=True):
                self.assertEqual(secret_service_environment(), explicit)
            for field, value in (("HOME", "/wrong-owner/home"), ("XDG_RUNTIME_DIR", "/wrong-owner/runtime"),
                                 ("DBUS_SESSION_BUS_ADDRESS", "unix:path=/wrong-owner/bus")):
                with patch.dict(os.environ, {**explicit, field: value}, clear=True), \
                        patch("keychain.shutil.which", return_value="/fixture/secret-tool"), \
                        patch("keychain.subprocess.run") as run, self.assertRaisesRegex(ValueError, "actual process account"):
                    SecretService().get()
                run.assert_not_called()
            with patch.dict(os.environ, {"HOME": "/custom/home", "DBUS_SESSION_BUS_ADDRESS": "unix:abstract=custom-owner-session"}, clear=True):
                self.assertEqual(secret_service_environment()["DBUS_SESSION_BUS_ADDRESS"], "unix:abstract=custom-owner-session")
        with patch.dict(os.environ, {}, clear=True), patch("os.geteuid", return_value=2026), \
                patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir="/actual/account")), \
                patch("keychain.Path.stat", side_effect=FileNotFoundError()), self.assertRaisesRegex(ValueError, "session path is unavailable"):
            secret_service_environment()
        with patch.dict(os.environ, explicit, clear=True), patch("os.geteuid", return_value=2026), \
                patch("keychain.Path.stat", autospec=True,
                      side_effect=lambda path: SimpleNamespace(st_uid=2026, st_mode=stat.S_IFDIR | 0o777)), \
                self.assertRaisesRegex(ValueError, "actual process account"):
            secret_service_environment()


if __name__ == "__main__":
    unittest.main()
