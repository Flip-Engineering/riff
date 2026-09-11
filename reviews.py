"""Persistent producer notes and a cancellable OpenRouter review worker."""
import json
import os
import sys
from pathlib import Path
import subprocess
import signal
import tempfile
import threading
import time
import urllib.request
import uuid

from keychain import Keychain
from studio_core import ROOT


class Reviews:
    def __init__(self, store, keychain=None, command_builder=None):
        self.store = store
        self.keychain = keychain or Keychain()
        self.command_builder = command_builder or (lambda output: [sys.executable, str(ROOT / "review_client.py"), str(output)])
        self.lock = threading.RLock()
        self.wake, self.stop = threading.Event(), threading.Event()
        self.process = None
        self.models_cache = None
        with store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY, track_id TEXT NOT NULL, created REAL NOT NULL,
                    finished REAL, status TEXT NOT NULL, model TEXT NOT NULL, focus TEXT NOT NULL,
                    keep_lyrics INTEGER NOT NULL, source_recipe TEXT NOT NULL,
                    notes TEXT DEFAULT '', summary TEXT DEFAULT '', revision TEXT DEFAULT '{}',
                    usage TEXT DEFAULT '{}', error TEXT DEFAULT '');
                CREATE INDEX IF NOT EXISTS reviews_track ON reviews(track_id,created);
            """)
            db.execute("UPDATE reviews SET status='interrupted',error='The studio closed during this review.',finished=? WHERE status IN ('running','cancelling')", (time.time(),))
        self.worker = threading.Thread(target=self.work, daemon=True)
        self.worker.start()
        self.wake.set()

    def settings(self):
        with self.store.db() as db:
            row = db.execute("SELECT value FROM library_meta WHERE key='review_settings'").fetchone()
        return json.loads(row[0]) if row else {"enabled": False, "model": "google/gemini-3.8-flash"}

    def connection(self):
        return {**self.settings(), "has_key": self.keychain.exists(), "key_storage": getattr(self.keychain, "storage_name", "Credential store"), "key_storage_available": getattr(self.keychain, "available", True)}

    def configure(self, payload):
        with self.lock:
            return self._configure(payload)

    def _configure(self, payload):
        current = self.settings()
        enabled, model = payload.get("enabled", current["enabled"]), payload.get("model", current["model"])
        if type(enabled) is not bool or not isinstance(model, str) or not model.strip():
            raise ValueError("Choose a review model and whether reviews are enabled.")
        if "api_key" in payload:
            self.keychain.set(payload["api_key"])
        if enabled and not self.keychain.exists():
            raise ValueError("Save an OpenRouter key to enable reviews.")
        with self.store.db() as db:
            db.execute("INSERT OR REPLACE INTO library_meta(key,value) VALUES('review_settings',?)",
                       (json.dumps({"enabled": enabled, "model": model.strip()}),))
            if not enabled:
                db.execute("UPDATE reviews SET status='cancelled',finished=? WHERE status='queued'", (time.time(),))
        return self.connection()

    def remove_key(self):
        with self.lock:
            self.configure({"enabled": False})
            for review in self.snapshot():
                if review["status"] in ("running", "cancelling"):
                    self.cancel(review["id"])
            self.keychain.delete()
            return self.connection()

    def models(self, refresh=False):
        if self.models_cache is None or refresh:
            with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as response:
                catalog = json.load(response)
            self.models_cache = [{"id": model["id"], "name": model["name"]}
                                 for model in catalog["data"]
                                 if "audio" in model.get("architecture", {}).get("input_modalities", [])
                                 and "text" in model.get("architecture", {}).get("output_modalities", [])]
        return self.models_cache

    @staticmethod
    def unpack(row):
        item = dict(row)
        for name in ("source_recipe", "revision", "usage"):
            item[name] = json.loads(item[name])
        item["keep_lyrics"] = bool(item["keep_lyrics"])
        return item

    def list(self, track_id=None):
        with self.store.db() as db:
            rows = db.execute("SELECT * FROM reviews" + (" WHERE track_id=?" if track_id else "") +
                              " ORDER BY created DESC", (track_id,) if track_id else ()).fetchall()
        return [self.unpack(row) for row in rows]

    def snapshot(self):
        with self.store.db() as db:
            return [dict(row) for row in db.execute("SELECT id,track_id,status,created FROM reviews ORDER BY created DESC")]

    def get(self, review_id):
        with self.store.db() as db:
            row = db.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
        if not row:
            raise KeyError("Review not found.")
        return self.unpack(row)

    def submit(self, track_id, payload):
        with self.lock:
            return self._submit(track_id, payload)

    def _submit(self, track_id, payload):
        settings = self.connection()
        if not settings["enabled"]:
            raise ValueError("Enable OpenRouter reviews in review settings.")
        if not settings["has_key"]:
            raise ValueError("Add your OpenRouter key in review settings.")
        focus, keep = payload.get("focus", ""), payload.get("keep_lyrics", True)
        if not isinstance(focus, str) or type(keep) is not bool:
            raise ValueError("Add a review focus and choose whether to keep the lyrics.")
        track = self.store.track(track_id)
        self.store.audio_path(track_id)
        review_id = uuid.uuid4().hex
        with self.store.db() as db:
            db.execute("INSERT INTO reviews(id,track_id,created,status,model,focus,keep_lyrics,source_recipe) VALUES(?,?,?,?,?,?,?,?)",
                       (review_id, track_id, time.time(), "queued", settings["model"], focus.strip(), int(keep), json.dumps(track["recipe"])))
        self.wake.set()
        return self.get(review_id)

    def cancel(self, review_id):
        with self.lock, self.store.db() as db:
            row = db.execute("SELECT status FROM reviews WHERE id=?", (review_id,)).fetchone()
            if not row:
                raise KeyError("Review not found.")
            if row[0] == "queued":
                db.execute("UPDATE reviews SET status='cancelled',finished=? WHERE id=?", (time.time(), review_id))
            elif row[0] in ("running", "cancelling"):
                db.execute("UPDATE reviews SET status='cancelling' WHERE id=?", (review_id,))
                self.terminate()
        return self.get(review_id)

    def terminate(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def work(self):
        while not self.stop.is_set():
            with self.lock, self.store.db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM reviews WHERE status='queued' ORDER BY created,id LIMIT 1").fetchone()
                if row:
                    db.execute("UPDATE reviews SET status='running' WHERE id=?", (row["id"],))
            if row:
                self.execute(self.unpack(row))
            else:
                self.wake.wait()
                self.wake.clear()

    def execute(self, review):
        result, error, secret = None, "", ""
        try:
            secret = self.keychain.get()
            track = self.store.track(review["track_id"])
            settings = {"model": review["model"], "focus": review["focus"], "keep_lyrics": review["keep_lyrics"],
                        "recipe": review["source_recipe"], "audio": str(self.store.audio_path(review["track_id"])),
                        "duration": track["audio"]["duration"],
                        "api_key": secret}
            with tempfile.TemporaryDirectory(prefix="review-", dir=self.store.data_root) as folder:
                output = Path(folder) / "result.json"
                with self.lock:
                    if self.stop.is_set() or self.get(review["id"])["status"] == "cancelling":
                        return
                    self.process = subprocess.Popen(self.command_builder(output), stdin=subprocess.PIPE,
                                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                    text=True, start_new_session=True)
                    process = self.process
                process.communicate(json.dumps(settings))
                if output.is_file():
                    result = json.loads(output.read_text().replace(settings["api_key"], "[redacted]"))
                    error = result.get("error", "")
                elif process.returncode:
                    error = "The listening request stopped before finishing. Try the review again."
                else:
                    error = "The listening request finished without notes. Try another model."
                if result and not error and not isinstance(result.get("notes"), str):
                    error = "The model returned notes the studio could not read. Try the review again."
                if result and not isinstance(result.get("revision"), dict):
                    result["revision"] = {}
        except Exception as exc:
            error = str(exc).replace(secret, "[redacted]") if secret else str(exc)
        finally:
            with self.lock:
                if self.process and self.process.poll() is None:
                    self.terminate()
                    self.process.wait()
                self.process = None
                with self.store.db() as db:
                    status = db.execute("SELECT status FROM reviews WHERE id=?", (review["id"],)).fetchone()[0]
                    status = "interrupted" if self.stop.is_set() else "cancelled" if status == "cancelling" else "failed" if error or not result else "done"
                    if status == "done":
                        revision = {key: value for key, value in result.get("revision", {}).items()
                                    if key in ("title", "style", "lyrics", "abc") and isinstance(value, str)}
                        if review["keep_lyrics"]:
                            revision.pop("lyrics", None)
                        db.execute("UPDATE reviews SET status=?,finished=?,notes=?,summary=?,revision=?,usage=? WHERE id=?",
                                   (status, time.time(), result["notes"], str(result.get("summary", "")),
                                    json.dumps(revision), json.dumps(result.get("usage", {})), review["id"]))
                    else:
                        db.execute("UPDATE reviews SET status=?,finished=?,error=? WHERE id=?",
                                   (status, time.time(), error if status == "failed" else "", review["id"]))

    def close(self):
        self.stop.set()
        self.wake.set()
        with self.lock:
            self.terminate()
        self.worker.join()
