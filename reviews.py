"""Persistent producer notes and a cancellable OpenRouter review worker."""
import hashlib
import json
import math
import os
import re
import shutil
import sys
from pathlib import Path
import subprocess
import signal
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit
import uuid

from keychain import Keychain
from studio_core import ROOT
from review_recipe import attribute_observations, recommended_generation, source_seed

# Defaults for the reference-clip settings in the review settings dialog and
# POST /api/review/settings. Each is the user's choice; none is a fixed gate.
REFERENCE_DEFAULTS = {"reference_max_clips": 3, "reference_max_seconds": 120, "reference_max_bytes": 25 * 1024 * 1024}
# ffprobe container names that carry audio the providers accept after Riff's MP3 conversion.
REFERENCE_FORMATS = (("wav", "wav"), ("mp3", "mp3"), ("flac", "flac"), ("ogg", "ogg"),
                     ("aiff", "aiff"), ("mp4", "m4a"))
REFERENCE_FIELDS = ("id", "sha256", "label", "link", "note", "duration", "bytes", "format")


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
            columns = {row["name"] for row in db.execute("PRAGMA table_info(reviews)")}
            if "generation" not in columns:
                db.execute("ALTER TABLE reviews ADD COLUMN generation TEXT NOT NULL DEFAULT '{}'")
            for name in ("attachments", "observations", "warnings"):
                if name not in columns:
                    db.execute(f"ALTER TABLE reviews ADD COLUMN {name} TEXT NOT NULL DEFAULT '[]'")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS review_references (
                    id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, label TEXT NOT NULL,
                    link TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
                    duration REAL NOT NULL, bytes INTEGER NOT NULL, format TEXT NOT NULL,
                    created REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending', review_id TEXT);
                CREATE INDEX IF NOT EXISTS review_references_hash ON review_references(sha256);
            """)
            db.execute("UPDATE reviews SET status='interrupted',error='The studio closed during this review.',finished=? WHERE status IN ('running','cancelling')", (time.time(),))
        self.worker = threading.Thread(target=self.work, daemon=True)
        self.worker.start()
        self.wake.set()

    def settings(self):
        with self.store.db() as db:
            row = db.execute("SELECT value FROM library_meta WHERE key='review_settings'").fetchone()
        return {**REFERENCE_DEFAULTS, "enabled": False, "model": "google/gemini-3.8-flash",
                **(json.loads(row[0]) if row else {})}

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
        limits = {}
        for key in REFERENCE_DEFAULTS:
            value = payload.get(key, current[key])
            whole = key != "reference_max_seconds"
            if (type(value) is not int and (whole or type(value) is not float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("Reference clip limits must be positive numbers; clip count and size are whole numbers.")
            limits[key] = value
        if "api_key" in payload:
            self.keychain.set(payload["api_key"])
        if enabled and not self.keychain.exists():
            raise ValueError("Save an OpenRouter key to enable reviews.")
        with self.store.db() as db:
            db.execute("INSERT OR REPLACE INTO library_meta(key,value) VALUES('review_settings',?)",
                       (json.dumps({"enabled": enabled, "model": model.strip(), **limits}),))
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
        for name in ("source_recipe", "revision", "generation", "usage", "observations", "warnings"):
            item[name] = json.loads(item[name])
        item["references"] = json.loads(item.pop("attachments"))
        # Historical recommendations may predate seed retention. Present an
        # editable take without rewriting the original saved review.
        seed = source_seed(item["source_recipe"])
        if isinstance(item["generation"], dict) and item["generation"].get("mode") and seed is not None:
            item["generation"] = {**item["generation"], "seed": seed}
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
        attached = payload.get("references", [])
        if (not isinstance(attached, list) or any(not isinstance(item, str) for item in attached)
                or len(set(attached)) != len(attached)):
            raise ValueError("Attach each reference clip once.")
        track = self.store.track(track_id)
        self.store.audio_path(track_id)
        source = dict(track["recipe"])
        source["available_scores"] = self.store.available_scores(source)
        source["available_acoustics"] = self.store.available_acoustics(source)
        if source.get("performance"):
            self.store.performance_path(track_id)
            source["performance_track_id"] = track_id
        review_id = uuid.uuid4().hex
        with self.store.db() as db:
            snapshot = []
            for reference_id in attached:
                row = db.execute("SELECT * FROM review_references WHERE id=? AND status='pending'", (reference_id,)).fetchone()
                if not row:
                    raise ValueError("A reference clip was removed or already belongs to a review. Attach it again.")
                snapshot.append(self._public(row))
            self._check_references(snapshot, settings)
            db.execute("INSERT INTO reviews(id,track_id,created,status,model,focus,keep_lyrics,source_recipe,attachments) VALUES(?,?,?,?,?,?,?,?,?)",
                       (review_id, track_id, time.time(), "queued", settings["model"], focus.strip(), int(keep), json.dumps(source),
                        json.dumps(snapshot)))
            db.executemany("UPDATE review_references SET status='submitted',review_id=? WHERE id=?",
                           [(review_id, item["id"]) for item in snapshot])
        self.wake.set()
        return self.get(review_id)

    @staticmethod
    def _public(row):
        return {key: row[key] for key in REFERENCE_FIELDS}

    def _check_references(self, items, settings):
        """Apply the configured clip limits to clips about to be sent."""
        if len(items) > settings["reference_max_clips"]:
            raise ValueError(f"Attach at most {settings['reference_max_clips']} reference clips, or raise the limit in review settings.")
        for item in items:
            if item["bytes"] > settings["reference_max_bytes"]:
                raise ValueError(f"The reference clip {item['label']!r} is larger than the review limit of "
                                 f"{settings['reference_max_bytes']} bytes. Raise the limit in review settings or use a shorter clip.")
            if item["duration"] > settings["reference_max_seconds"]:
                raise ValueError(f"The reference clip {item['label']!r} is longer than the review limit of "
                                 f"{settings['reference_max_seconds']:g} seconds. Raise the limit in review settings or use a shorter clip.")
            if not self.reference_path(item["sha256"]).is_file():
                raise ValueError(f"The reference clip {item['label']!r} is no longer stored. Attach it again.")

    def _stored_reference(self, item):
        """The kept file for a receipt entry, after checking that it is still the same clip."""
        path = self.reference_path(item["sha256"])
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        except OSError:
            digest = None
        if digest is None or digest.hexdigest() != item["sha256"]:
            raise ValueError(f"The reference clip {item['label']!r} is missing or has changed. Attach it again.")
        return path

    def reference_path(self, digest):
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Reference clip identity is invalid.")
        return self.store.data_root / "review-references" / digest

    def references(self):
        """Clips attached for the next review, oldest first. Local paths are never returned."""
        with self.store.db() as db:
            rows = db.execute("SELECT * FROM review_references WHERE status='pending' ORDER BY rowid").fetchall()
        return {"references": [self._public(row) for row in rows], "limits": {key: self.settings()[key] for key in REFERENCE_DEFAULTS}}

    @staticmethod
    def _text(payload, name, limit, default=""):
        value = payload.get(name, default)
        if not isinstance(value, str):
            raise ValueError(f"The reference {name} must be text.")
        value = value.strip()
        if len(value) > limit:
            raise ValueError(f"The reference {name} must be at most {limit} characters.")
        return value

    def _details(self, payload, current=None):
        current = dict(current or {})
        payload = {**{key: current[key] for key in ("label", "link", "note") if key in current}, **payload}
        label = self._text(payload, "label", 200)
        link = self._text(payload, "link", 2048)
        note = self._text(payload, "note", 2000)
        if not label:
            raise ValueError("Give the reference clip a label.")
        if link:
            parts = urlsplit(link)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError("A reference source link must be an http or https address. Put other notes in the note.")
        return label, link, note

    @staticmethod
    def _probe(path):
        if not shutil.which("ffprobe"):
            raise ValueError("Install FFmpeg with brew install ffmpeg to check reference clips.")
        try:
            result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_entries",
                                     "format=format_name,duration:stream=codec_type:stream_disposition=attached_pic",
                                     "-of", "json", str(path)], capture_output=True, check=True, timeout=60)
            probe = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            raise ValueError("Riff could not read this file as audio. Choose a WAV, MP3, FLAC, Ogg, AIFF or M4A clip.") from None
        names = str(probe.get("format", {}).get("format_name", "")).split(",")
        streams = probe.get("streams", [])
        audio_format = next((label for name, label in REFERENCE_FORMATS if name in names), None)
        video = [item for item in streams if item.get("codec_type") == "video" and not item.get("disposition", {}).get("attached_pic")]
        try:
            duration = float(probe["format"]["duration"])
        except (KeyError, TypeError, ValueError):
            duration = 0.0
        if (audio_format is None or video or not any(item.get("codec_type") == "audio" for item in streams)
                or not math.isfinite(duration) or duration <= 0):
            raise ValueError("This file is not a supported audio clip. Choose a WAV, MP3, FLAC, Ogg, AIFF or M4A clip.")
        return audio_format, duration

    def add_reference(self, source, metadata=None, name=""):
        """Keep an uploaded clip as an opaque, content-addressed attachment."""
        metadata = metadata or {}
        if not isinstance(metadata, dict):
            raise ValueError("Reference clip details must be an object.")
        settings = self.settings()
        source = Path(source)
        size = source.stat().st_size
        if size <= 0:
            raise ValueError("This reference clip is empty.")
        if size > settings["reference_max_bytes"]:
            raise ValueError(f"This clip is larger than the review limit of {settings['reference_max_bytes']} bytes. "
                             "Raise the limit in review settings or use a shorter clip.")
        label, link, note = self._details({"label": Path(name).stem, **metadata})
        audio_format, duration = self._probe(source)
        if duration > settings["reference_max_seconds"]:
            raise ValueError(f"This clip is longer than the review limit of {settings['reference_max_seconds']:g} seconds. "
                             "Raise the limit in review settings or use a shorter clip.")
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        sha = digest.hexdigest()
        with self.lock:
            with self.store.db() as db:
                count = db.execute("SELECT COUNT(*) FROM review_references WHERE status='pending'").fetchone()[0]
            if count >= settings["reference_max_clips"]:
                raise ValueError(f"Attach at most {settings['reference_max_clips']} reference clips, or raise the limit in review settings.")
            blob = self.reference_path(sha)
            if not blob.is_file():
                blob.parent.mkdir(parents=True, exist_ok=True)
                part = blob.with_name(blob.name + "." + uuid.uuid4().hex + ".part")
                try:
                    shutil.copyfile(source, part)
                    os.replace(part, blob)
                finally:
                    part.unlink(missing_ok=True)
            row = {"id": uuid.uuid4().hex, "sha256": sha, "label": label, "link": link, "note": note,
                   "duration": duration, "bytes": size, "format": audio_format}
            with self.store.db() as db:
                db.execute("INSERT INTO review_references(id,sha256,label,link,note,duration,bytes,format,created) VALUES(?,?,?,?,?,?,?,?,?)",
                           (*(row[key] for key in REFERENCE_FIELDS), time.time()))
            return row

    def _pending(self, db, reference_id):
        row = db.execute("SELECT * FROM review_references WHERE id=? AND status='pending'", (reference_id,)).fetchone()
        if not row:
            raise KeyError("Reference clip not found, or it already belongs to a review.")
        return row

    def update_reference(self, reference_id, payload):
        if not isinstance(payload, dict) or set(payload) - {"label", "link", "note"}:
            raise ValueError("Only a reference clip's label, source link and note can change.")
        with self.lock, self.store.db() as db:
            row = self._pending(db, reference_id)
            label, link, note = self._details(payload, row)
            db.execute("UPDATE review_references SET label=?,link=?,note=? WHERE id=?", (label, link, note, reference_id))
            return self._public({**dict(row), "label": label, "link": link, "note": note})

    def remove_reference(self, reference_id):
        with self.lock:
            with self.store.db() as db:
                row = self._pending(db, reference_id)
                db.execute("UPDATE review_references SET status='removed' WHERE id=?", (reference_id,))
            self._release(row["sha256"])
        return {"status": "removed", "id": reference_id}

    def _release(self, digest):
        """Delete an unreferenced clip file; clips named by a review receipt stay."""
        with self.store.db() as db:
            kept = (db.execute("SELECT 1 FROM review_references WHERE sha256=? AND status!='removed' LIMIT 1", (digest,)).fetchone()
                    or db.execute("SELECT 1 FROM reviews WHERE attachments LIKE ? LIMIT 1", ("%" + digest + "%",)).fetchone())
        if not kept:
            self.reference_path(digest).unlink(missing_ok=True)

    def reuse_references(self, review_id):
        """Attach copies of a review receipt's clips so they can be edited before another review."""
        if not isinstance(review_id, str) or not re.fullmatch(r"[a-f0-9]{32}", review_id):
            raise ValueError("Choose the review whose reference clips to attach again.")
        with self.lock:
            review = self.get(review_id)
            settings = self.settings()
            with self.store.db() as db:
                pending = db.execute("SELECT COUNT(*) FROM review_references WHERE status='pending'").fetchone()[0]
            if not review["references"]:
                raise ValueError("This review had no reference clips.")
            if pending + len(review["references"]) > settings["reference_max_clips"]:
                raise ValueError("Remove a reference clip first, or raise the clip limit in review settings.")
            self._check_references(review["references"], settings)
            created = []
            with self.store.db() as db:
                for item in review["references"]:
                    row = {**item, "id": uuid.uuid4().hex}
                    db.execute("INSERT INTO review_references(id,sha256,label,link,note,duration,bytes,format,created) VALUES(?,?,?,?,?,?,?,?,?)",
                               (*(row[key] for key in REFERENCE_FIELDS), time.time()))
                    created.append(row)
            return {"references": created}

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
            source = dict(review["source_recipe"])
            source["available_scores"] = self.store.available_scores(source, cancelled=self.stop)
            source["available_acoustics"] = self.store.available_acoustics(source, cancelled=self.stop)
            settings = {"model": review["model"], "focus": review["focus"], "keep_lyrics": review["keep_lyrics"],
                        "recipe": source, "audio": str(self.store.audio_path(review["track_id"])),
                        "duration": track["audio"]["duration"],
                        "api_key": secret}
            if review["references"]:
                # The client reads these local files; their paths never go to the model or the page.
                settings["references"] = [{**item, "path": str(self._stored_reference(item))} for item in review["references"]]
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
                    error = "The listening request finished without a recommendation. Review again to retry."
                if result and not error and not isinstance(result.get("notes"), str):
                    error = "The model returned notes the studio could not read. Try the review again."
                if result and not error:
                    result["generation"] = recommended_generation(
                        result.get("generation"), source, review["keep_lyrics"])
                    if review["references"]:
                        result["observations"], result["warnings"] = attribute_observations(
                            result.get("observations"), review["references"])
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
                        generation = result["generation"]
                        # Retain the old text suggestion for installations that roll back.
                        revision = {key: generation[key] for key in ("title", "style", "lyrics", "abc")
                                    if generation[key] != review["source_recipe"].get(key, "")}
                        if review["keep_lyrics"]:
                            revision.pop("lyrics", None)
                        db.execute("UPDATE reviews SET status=?,finished=?,notes=?,summary=?,revision=?,generation=?,usage=?,observations=?,warnings=? WHERE id=?",
                                   (status, time.time(), result["notes"], str(result.get("summary", "")),
                                    json.dumps(revision), json.dumps(generation), json.dumps(result.get("usage", {})),
                                    json.dumps(result.get("observations", [])), json.dumps(result.get("warnings", [])), review["id"]))
                    else:
                        db.execute("UPDATE reviews SET status=?,finished=?,error=? WHERE id=?",
                                   (status, time.time(), error if status == "failed" else "", review["id"]))

    def close(self):
        self.stop.set()
        self.wake.set()
        with self.lock:
            self.terminate()
        self.worker.join()
