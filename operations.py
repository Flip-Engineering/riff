"""Versioned operations shared by the Studio and external agents.

Every queued take, whether it comes from the Studio form or from an agent,
passes through ``Operations.submit``. That call validates the recipe, resolves
its references on the server and inserts the job and its operation record in
one transaction. The record keeps what was submitted and what the server
resolved at that moment. Live state (status, stages, outputs) is read from the
job row, so a receipt is complete after a restart.
"""
import base64
import copy
import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import nullcontext

import run as engine
from paths import ROOT

SURFACE_VERSION = 1
IDENTITY = re.compile(r"[a-f0-9]{32}")
TERMINAL = ("done", "planned", "performed", "synthesized", "cancelled", "interrupted", "failed")
SCHEMA = """
    CREATE TABLE IF NOT EXISTS operations (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, surface_version INTEGER NOT NULL,
        idempotency_key TEXT, request_digest TEXT NOT NULL, created REAL NOT NULL,
        job_id TEXT NOT NULL UNIQUE, family_id TEXT NOT NULL, parent TEXT, resumes TEXT,
        request TEXT NOT NULL, effective_recipe TEXT NOT NULL, refs TEXT NOT NULL, runtime TEXT NOT NULL,
        derivation TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS operations_key ON operations(name, idempotency_key)
        WHERE idempotency_key IS NOT NULL;
    CREATE INDEX IF NOT EXISTS operations_family ON operations(family_id, created);
    CREATE TABLE IF NOT EXISTS selections (
        id TEXT PRIMARY KEY, family_id TEXT NOT NULL, take_job_id TEXT NOT NULL, operation_id TEXT,
        rationale TEXT NOT NULL, created REAL NOT NULL, idempotency_key TEXT, request_digest TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS selections_key ON selections(idempotency_key) WHERE idempotency_key IS NOT NULL;
    CREATE INDEX IF NOT EXISTS selections_family ON selections(family_id, created);
"""


INPUT_FIELDS = (("seed", "Seed"), ("lyrics", "Words"), ("style", "Direction"), ("mode", "Creation mode"),
                ("cot", "Planning"), ("max_seconds", "Length"), ("steps", "Detail"), ("solver", "Synthesis method"),
                ("cfg_scale", "Guidance"), ("temperature", "Variation"), ("abc", "Written input"),
                ("score_source", "Saved score"), ("performance_source", "Saved performance"),
                ("acoustic_source", "Saved synthesis"), ("decoder", "Audio refinement"),
                ("refinement", "Sampling controls"), ("brief", "Brief"), ("energy", "Energy"), ("texture", "Texture"))


def recipe_changes(before, after):
    """Recorded input differences. A field that was never recorded is not the same as its default."""
    return [{"field": field, "label": label, "before": before.get(field), "after": after.get(field),
             "before_recorded": field in before, "after_recorded": field in after}
            for field, label in INPUT_FIELDS
            if (field in before, before.get(field)) != (field in after, after.get(field))]


CARRIED = ("title", "lyrics", "style", "mode", "lyrics_source", "idea_engine", "brief", "writer_tokens", "cfg_scale",
           "temperature", "energy", "texture", "theme", "max_seconds", "steps", "solver", "cot", "refinement", "loras",
           "hold_words", "hold_sound", "writer_model", "writer_summary")
CHANGEABLE = CARRIED + ("abc", "seed", "render_mode")
COMPARED_RESULTS = (("composed_score", "Composed score"), ("performance_sha256", "Performance"))


class Conflict(ValueError):
    """The request cannot be applied to the current state; the HTTP layer answers 409."""


class _Duplicate(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(name, request):
    return hashlib.sha256(canonical({"operation": name, "input": request}).encode("utf-8")).hexdigest()


def identity(value, label):
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise ValueError(f"{label} must be a 32-character lowercase hexadecimal ID.")
    return value


def idempotency_key(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("The idempotency key must be non-empty printable text.")
    return value


def runtime_identity():
    """Identify the application and engine that accepted the work."""
    try:
        capabilities = {key: value for key, value in engine.engine_capabilities().items()
                        if isinstance(value, (bool, int, str))}
    except Exception:
        capabilities = {}
    return {"riff": (ROOT / "VERSION").read_text().strip(), "engine_capabilities": capabilities}


class Operations:
    def __init__(self, store, generator, admit=None):
        self.store, self.generator = store, generator
        # The host supplies the checks that must hold while work is accepted (restart, setup, engine readiness).
        self.admit = admit or (lambda payload: nullcontext())

    # -- reads ---------------------------------------------------------------

    def public(self, value):
        """Replace this installation's directories in text; receipts carry IDs and provenance, not paths."""
        roots = sorted({str(self.store.data_root): "<data>", str(self.store.outputs): "<library>", str(ROOT): "<riff>"}.items(),
                       key=lambda item: -len(item[0]))
        def clean(item):
            if isinstance(item, str):
                for root, label in roots:
                    item = item.replace(root, label)
                return item
            if isinstance(item, dict):
                return {key: clean(child) for key, child in item.items()}
            if isinstance(item, list):
                return [clean(child) for child in item]
            return item
        return clean(value)

    def find(self, operation_id):
        """The operation row for an operation, job or take ID, or None for work recorded before operations existed."""
        with self.store.db() as db:
            return db.execute("SELECT * FROM operations WHERE id=? OR job_id=? ORDER BY id=? DESC LIMIT 1",
                              (operation_id, operation_id, operation_id)).fetchone()

    def require(self, operation_id):
        row = self.find(operation_id)
        if row is None:
            raise KeyError("Operation not found.")
        return row

    def receipt(self, operation_id):
        row = self.find(operation_id)
        if row is None:
            return self._legacy_receipt(operation_id)
        job = self.store.job(row["job_id"])
        effective = json.loads(row["effective_recipe"])
        return self._receipt(job, {
            "id": row["id"], "name": row["name"], "surface_version": row["surface_version"], "created": row["created"],
            "idempotency_key": row["idempotency_key"], "family_id": row["family_id"],
            "parent": json.loads(row["parent"]) if row["parent"] else None, "resumes": row["resumes"]},
            json.loads(row["request"]), effective, json.loads(row["refs"]), json.loads(row["runtime"]), [],
            json.loads(row["derivation"]) if row["derivation"] else None)

    def _legacy_receipt(self, job_id):
        """A job made before operation records existed: derived from the job row, with unrecorded fields marked."""
        try:
            job = self.store.job(job_id)
        except KeyError:
            raise KeyError("Operation not found.") from None
        recipe = job["recipe"]
        return self._receipt(job, {"id": None, "name": None, "surface_version": None, "created": job["created"],
                                   "idempotency_key": None, "family_id": None, "parent": self.parent_of(recipe),
                                   "resumes": None},
                             None, recipe, [], {}, ["operation", "request", "effective_recipe", "references", "runtime"], None)

    def _receipt(self, job, operation, request, effective, references, runtime, unrecorded, derivation):
        recipe = job["recipe"]
        terminal = job["status"] in TERMINAL
        errors = dict(recipe.get("artifact_errors") or {})
        if job["error"]:
            errors["job"] = job["error"]
        stages = self._stages(effective, recipe, job, terminal)
        plan = recipe.get("symbolic_plan") or {}
        return self.public({
            "recorded": not unrecorded, "unrecorded": unrecorded,
            "operation": operation,
            "job": {"id": job["id"], "status": job["status"], "error": job["error"], "created": job["created"],
                    "started": job["started"], "finished": job["finished"], "queue_position": job["queue_position"]},
            "request": request,
            "effective_recipe": effective,
            "references": references,
            "runtime": runtime,
            "derivation": derivation,
            "seed": effective["seed"],
            "symbolic_plan": {key: plan.get(key) for key in ("artifact_id", "sha256", "token_count", "truncated")} if plan else None,
            "stages": stages,
            "outputs": {"track_id": job["track_id"] or "",
                        "score_source": plan.get("artifact_id", ""),
                        "performance_source": job["id"] if stages["performance"]["state"] == "done" and terminal else "",
                        "acoustic_source": (recipe.get("acoustic") or {}).get("artifact_id", "") if terminal else ""},
            "errors": errors,
            "result_recipe": recipe,
            "terminal": terminal,
        })

    @staticmethod
    def _stages(effective, recipe, job, terminal):
        """Report each stage the take asked for, from facts the engine returned."""
        errors = recipe.get("artifact_errors") or {}
        mode = effective.get("render_mode", "music")
        acoustic_finish = bool(effective.get("acoustic_source"))

        def stage(wanted, done, failure, reused=""):
            if not wanted:
                return {"state": "not_applicable"}
            if reused:
                return {"state": "reused", "from": reused}
            if done:
                return {"state": "done"}
            if failure and terminal:
                return {"state": "failed", "error": failure}
            return {"state": "pending" if not terminal else "not_produced"}

        plan_wanted = effective.get("cot", "off") != "off" and not acoustic_finish
        reused_score = effective.get("score_source", "") if plan_wanted else ""
        performance_wanted = mode in ("music", "performance", "sound") and not acoustic_finish
        sound_wanted = mode in ("music", "sound") or acoustic_finish
        return {
            "plan": stage(plan_wanted, bool(recipe.get("symbolic_plan")), errors.get("score"), reused_score),
            "performance": stage(performance_wanted, bool(recipe.get("performance")), errors.get("performance"),
                                 effective.get("performance_source", "")),
            "sound": stage(sound_wanted, bool(recipe.get("acoustic")), errors.get("acoustic"),
                           effective.get("acoustic_source", "")),
            "audio": stage(mode == "music", bool(job["track_id"]), job["error"] if job["status"] == "failed" else ""),
        }

    def list(self, family=None, status=None, parent=None, since=None, limit=None, cursor=None):
        """Recorded operations, newest first. Without a limit every match is returned; with one, pass
        ``next_cursor`` back to continue."""
        clauses, values = [], []
        if family is not None:
            clauses.append("o.family_id=?"); values.append(identity(family, "Family"))
        if status is not None:
            clauses.append("j.status=?"); values.append(status)
        if parent is not None:
            clauses.append("json_extract(o.parent,'$.id')=?"); values.append(identity(parent, "Parent"))
        if since is not None:
            clauses.append("o.created>?"); values.append(float(since))
        if cursor is not None:
            try:
                created, last = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii") + b"=" * (-len(cursor) % 4)))
                created = float(created)
                identity(last, "Cursor")
            except (ValueError, TypeError, UnicodeError):
                raise ValueError("The cursor is invalid. Use next_cursor from an earlier page.") from None
            clauses.append("(o.created<? OR (o.created=? AND o.id>?))"); values += [created, created, last]
        query = ("SELECT o.id,o.name,o.created,o.family_id,o.parent,o.resumes,o.job_id,o.idempotency_key,"
                 "j.status,j.title,j.error,j.track_id FROM operations o JOIN jobs j ON j.id=o.job_id"
                 + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY o.created DESC,o.id")
        if limit is not None:
            try:
                limit = int(limit)
            except (ValueError, TypeError):
                raise ValueError("The page size must be a whole number.") from None
            if limit < 1:
                raise ValueError("The page size must be at least one.")
            query += " LIMIT ?"; values.append(limit + 1)
        with self.store.db() as db:
            rows = db.execute(query, values).fetchall()
        more = limit is not None and len(rows) > limit
        rows = rows[:limit] if limit is not None else rows
        items = [self.public({"id": r["id"], "name": r["name"], "created": r["created"], "family_id": r["family_id"],
                              "parent": json.loads(r["parent"]) if r["parent"] else None, "resumes": r["resumes"],
                              "job_id": r["job_id"], "idempotency_key": r["idempotency_key"], "status": r["status"],
                              "title": r["title"], "error": r["error"], "track_id": r["track_id"] or ""}) for r in rows]
        token = base64.urlsafe_b64encode(json.dumps([rows[-1]["created"], rows[-1]["id"]]).encode()).decode().rstrip("=") if more else None
        return {"operations": items, "next_cursor": token}

    # -- references ------------------------------------------------------------

    def parent_of(self, recipe, explicit=None):
        """Name the take this one continues, from the caller or the recipe's recorded links."""
        if explicit is not None:
            kind = explicit.get("kind") if isinstance(explicit, dict) else None
            if kind not in ("track", "job", "operation") or set(explicit) != {"kind", "id"}:
                raise ValueError("Parent must be {kind: track|job|operation, id}.")
            reference = identity(explicit["id"], "Parent ID")
            if kind == "track":
                self.store.track(reference)
            elif kind == "job":
                self.store.job(reference)
            else:
                self.require(reference)
            return {"kind": kind, "id": reference}
        for field in ("parent_track_id", "performance_source"):
            value = recipe.get(field)
            if value:
                with self.store.db() as db:
                    job = db.execute("SELECT 1 FROM jobs WHERE id=? AND track_id IS NULL", (value,)).fetchone()
                # A take shares its ID with the job that made it; a job with no take is still a job.
                return {"kind": "job" if job else "track", "id": value}
        return None

    def family_for(self, parent, family_id=None):
        if family_id is not None:
            identity(family_id, "Family")
            with self.store.db() as db:
                if not db.execute("SELECT 1 FROM operations WHERE family_id=? LIMIT 1", (family_id,)).fetchone():
                    raise KeyError("Family not found.")
            return family_id
        if parent:
            with self.store.db() as db:
                row = db.execute("SELECT family_id FROM operations WHERE id=? OR job_id=? LIMIT 1",
                                 (parent["id"], parent["id"])).fetchone()
            if row:
                return row["family_id"]
        return uuid.uuid4().hex

    def resolve_references(self, recipe):
        """Record what each reference resolved to when the server accepted it."""
        found = []
        if recipe.get("score_source"):
            found.append({"field": "score_source", "kind": "score", "id": recipe["score_source"],
                          "provenance": dict(recipe.get("score_input") or {})})
        if recipe.get("performance_source"):
            source, _ = self.store.performance_record(recipe["performance_source"])
            with self.store.db() as db:
                kind = ("track" if db.execute("SELECT 1 FROM tracks WHERE id=?", (recipe["performance_source"],)).fetchone()
                        else "detached" if db.execute("SELECT 1 FROM detached_performances WHERE id=?", (recipe["performance_source"],)).fetchone()
                        else "job")
            found.append({"field": "performance_source", "kind": "performance", "id": recipe["performance_source"],
                          "provenance": {"source": kind, **{key: source["recipe"].get("performance", {}).get(key)
                                                            for key in ("frames", "truncated", "sha256")}}})
        if recipe.get("acoustic_source"):
            found.append({"field": "acoustic_source", "kind": "sound", "id": recipe["acoustic_source"],
                          "provenance": dict(recipe.get("acoustic") or {})})
        for field in ("parent_track_id", "review_id"):
            if recipe.get(field):
                found.append({"field": field, "kind": "track" if field == "parent_track_id" else "review",
                              "id": recipe[field], "provenance": {}})
        for receipt in recipe.get("lora_provenance") or []:
            found.append({"field": "loras", "kind": "adapter", "id": receipt.get("id", ""), "provenance": dict(receipt)})
        return found

    def references(self, payload):
        """Verify references the way submission would, reporting each outcome instead of raising."""
        unknown = set(payload) - {"subject", "score_source", "performance_source", "acoustic_source", "parent_track_id"}
        if unknown:
            raise ValueError("Unknown input: " + ", ".join(sorted(unknown)))
        found = []
        if "subject" in payload:
            subject = self._subject(payload["subject"])
            recipe = subject["recipe"]
            for item in self.store.available_scores(recipe):
                found.append(self._score_reference(item))
            if recipe.get("performance"):
                found.append(self._performance_reference(subject["job_id"] or subject["id"]))
            for item in self.store.available_acoustics(recipe):
                found.append(self._sound_reference(item))
            return {"subject": subject["id"], "references": self.public(found)}
        for field, check in (("score_source", self._check_score), ("performance_source", self._performance_reference),
                             ("acoustic_source", self._check_sound), ("parent_track_id", self._check_parent)):
            if payload.get(field):
                found.append(check(identity(payload[field], field) if field in ("performance_source", "parent_track_id") else payload[field]))
        return {"subject": None, "references": self.public(found)}

    @staticmethod
    def _score_reference(item):
        keys = ("title", "source_job_id", "sha256", "bytes", "token_count", "truncated", "cot")
        return {"field": "score_source", "kind": "score", "id": item["id"],
                "status": "verified" if item.get("compatible") else "incompatible",
                "provenance": {key: item.get(key) for key in keys}}

    @staticmethod
    def _sound_reference(item):
        keys = ("title", "source_job_id", "sample_rate", "channels", "duration", "seed", "steps", "solver", "hashes")
        return {"field": "acoustic_source", "kind": "sound", "id": item["id"],
                "status": "verified" if item.get("compatible") else "incompatible",
                "provenance": {key: item.get(key) for key in keys if key in item}}

    def _check_score(self, reference):
        try:
            if not self.store.artifacts:
                raise KeyError("Score not found.")
            return self._score_reference(self.store.artifacts.describe(reference))
        except (ValueError, KeyError, OSError) as error:
            return {"field": "score_source", "kind": "score", "id": reference, "status": "unavailable",
                    "provenance": {}, "error": str(error.args[0])}

    def _check_sound(self, reference):
        try:
            if not self.store.acoustics:
                raise KeyError("Saved sound not found.")
            return self._sound_reference(self.store.acoustics.describe(reference))
        except (ValueError, KeyError, OSError) as error:
            return {"field": "acoustic_source", "kind": "sound", "id": reference, "status": "unavailable",
                    "provenance": {}, "error": str(error.args[0])}

    def _performance_reference(self, reference):
        try:
            path = self.store.performance_path(reference)
            source, _ = self.store.performance_record(reference)
            saved = source["recipe"]["performance"]
            return {"field": "performance_source", "kind": "performance", "id": reference, "status": "verified",
                    "provenance": {key: saved.get(key) for key in ("frames", "truncated", "sha256")}}
        except (ValueError, KeyError, OSError) as error:
            return {"field": "performance_source", "kind": "performance", "id": reference, "status": "unavailable",
                    "provenance": {}, "error": str(error.args[0] if isinstance(error, KeyError) else error)}

    def _check_parent(self, reference):
        try:
            track = self.store.track(reference)
            return {"field": "parent_track_id", "kind": "track", "id": reference, "status": "verified",
                    "provenance": {"title": track["title"], "archived": bool(track["archived"])}}
        except KeyError:
            return {"field": "parent_track_id", "kind": "track", "id": reference, "status": "unavailable",
                    "provenance": {}, "error": "Recording not found."}

    def _subject(self, reference):
        """A take, job or operation as a recorded recipe, for comparison and reference lookup."""
        identity(reference, "Subject")
        row = self.find(reference)
        key = row["job_id"] if row else reference
        try:
            job = self.store.job(key)
            return {"id": reference, "job_id": job["id"], "track_id": job["track_id"] or "", "status": job["status"],
                    "title": job["title"], "recipe": job["recipe"], "kind": "take" if job["track_id"] else "job",
                    "operation_id": row["id"] if row else None, "family_id": row["family_id"] if row else None}
        except KeyError:
            track = self.store.track(key)
            return {"id": reference, "job_id": None, "track_id": track["id"], "status": "done", "title": track["title"],
                    "recipe": track["recipe"], "kind": "take", "operation_id": None, "family_id": None}

    def compare(self, references):
        """Show which recorded inputs the subjects share and where they differ."""
        if not isinstance(references, list) or len(references) < 2:
            raise ValueError("Choose at least two takes, jobs or operations to compare.")
        subjects = [self._subject(reference) for reference in references]
        views = []
        for subject in subjects:
            recipe, view = subject["recipe"], dict(subject["recipe"])
            if (recipe.get("symbolic_plan") or {}).get("artifact_id"):
                view["composed_score"] = recipe["symbolic_plan"]["artifact_id"]
            if (recipe.get("performance") or {}).get("sha256"):
                view["performance_sha256"] = recipe["performance"]["sha256"]
            views.append(view)
        shared, differences = [], []
        for field, label in INPUT_FIELDS + COMPARED_RESULTS:
            observed = [(field in view, view.get(field)) for view in views]
            if all(item == observed[0] for item in observed):
                if observed[0][0]:
                    shared.append({"field": field, "label": label, "value": observed[0][1]})
            else:
                differences.append({"field": field, "label": label, "values": [
                    {"subject": subject["id"], "recorded": recorded, "value": value}
                    for subject, (recorded, value) in zip(subjects, observed)]})
        return self.public({
            "subjects": [{key: subject[key] for key in ("id", "job_id", "title", "kind", "operation_id", "family_id")}
                         for subject in subjects],
            "shared": shared, "differences": differences, "basis": "recorded_inputs_and_captured_stage_artifacts"})

    # -- writes ----------------------------------------------------------------

    def submit(self, name, request, payload, key=None, family_id=None, parent=None, resumes=None, derivation=None):
        """Queue one take and record its operation atomically; replay a repeated key."""
        key = idempotency_key(key)
        fingerprint = digest(name, request)
        if key and (existing := self._existing(name, key, fingerprint)):
            return existing
        operation_id = uuid.uuid4().hex

        def record(db, job_id, recipe):
            if key and db.execute("SELECT 1 FROM operations WHERE name=? AND idempotency_key=?", (name, key)).fetchone():
                raise _Duplicate()
            linked = self.parent_of(recipe, parent)
            family = self.family_for(linked, family_id)
            db.execute("INSERT INTO operations (id,name,surface_version,idempotency_key,request_digest,created,job_id,"
                       "family_id,parent,resumes,request,effective_recipe,refs,runtime,derivation) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (operation_id, name, SURFACE_VERSION, key, fingerprint, time.time(), job_id, family,
                        json.dumps(linked) if linked else None, resumes, json.dumps(request),
                        json.dumps(recipe), json.dumps(self.public(self.resolve_references(recipe))), json.dumps(runtime_identity()),
                        json.dumps(self.public(derivation)) if derivation else None))

        try:
            with self.admit(payload):
                self.generator.submit(payload, operation=record)
        except _Duplicate:
            return self._existing(name, key, fingerprint)
        return {**self.submission(operation_id), "replayed": False}

    def replayed(self, name, request, key):
        """The original submission when this key was already used for this request, else None."""
        key = idempotency_key(key)
        return self._existing(name, key, digest(name, request)) if key else None

    def _existing(self, name, key, fingerprint):
        with self.store.db() as db:
            row = db.execute("SELECT id,request_digest FROM operations WHERE name=? AND idempotency_key=?", (name, key)).fetchone()
        if row is None:
            return None
        if row["request_digest"] != fingerprint:
            raise Conflict("This idempotency key was already used for a different request. "
                           f"Recorded request digest {row['request_digest']}, this request {fingerprint}. Nothing was queued.")
        return {**self.submission(row["id"]), "replayed": True}

    def submission(self, operation_id):
        receipt = self.receipt(operation_id)
        operation = receipt["operation"]
        return {"operation_id": operation["id"], "job_id": receipt["job"]["id"], "family_id": operation["family_id"],
                "parent": operation["parent"], "status": receipt["job"]["status"], "receipt": receipt}

    def cancel(self, operation_id):
        row = self.require(operation_id)
        status = self.generator.cancel(row["job_id"])
        return {"operation_id": row["id"], "job_id": row["job_id"], "status": status, "receipt": self.receipt(row["id"])}

    def validate(self, payload):
        """Run the submit-time checks without queueing anything."""
        try:
            recipe = self.store.prepare_loras(self.generator.prepare(payload), bind=True)
            return {"valid": True, "effective_recipe": recipe, "references": self.resolve_references(recipe),
                    "parent": self.parent_of(recipe)}
        except (ValueError, KeyError) as error:
            return {"valid": False, "error": str(error.args[0]) if isinstance(error, KeyError) else str(error)}

    # -- derived takes -----------------------------------------------------------

    def _stage_reference(self, subject, stage):
        """The verified reference a stage of this take offers, or the reason it does not."""
        recipe = subject["recipe"]
        if stage == "score":
            reference = (recipe.get("symbolic_plan") or {}).get("artifact_id") or recipe.get("score_source")
            if not reference:
                return None, "This take has no composed score to retain."
            check = self._check_score(reference)
        elif stage == "performance":
            reference = subject["track_id"] or subject["job_id"]
            if not recipe.get("performance") or not reference:
                return None, "This take has no saved performance to retain."
            check = self._performance_reference(reference)
        else:
            reference = (recipe.get("acoustic") or {}).get("artifact_id") or recipe.get("acoustic_source")
            if not reference:
                return None, "This take has no saved sound to finish."
            check = self._check_sound(reference)
        if check["status"] != "verified":
            return None, f"The saved {stage} is {check['status']}" + (f": {check['error']}" if check.get("error") else ".")
        return reference, ""

    def _draft(self, subject, retain, strict, changes, suffix="", render_mode="music", continuing=False):
        """Build a recipe for a new take from a recorded one, retaining only what was asked for.

        ``retain`` names what carries over: ``seed``, ``score`` (the verified composed score) and
        ``performance`` (the verified saved phrasing). Everything the source recorded as an input is
        carried; ``changes`` then replace individual inputs. With ``strict`` an unavailable retention
        is an error; otherwise it is skipped and reported in ``notes``.
        """
        source, notes = subject["recipe"], []
        draft = {key: copy.deepcopy(source[key]) for key in CARRIED if key in source}
        if "lora_provenance" in source and "loras" not in changes:
            draft["lora_provenance"] = copy.deepcopy(source["lora_provenance"])
        base = source.get("title") or subject["title"]
        draft["title"] = f"{base}{suffix}"
        draft["render_mode"] = render_mode

        def skip(reason):
            if strict:
                raise ValueError(reason)
            notes.append(reason)
        cot = changes.get("cot", draft.get("cot", "off"))
        if "seed" in retain and "seed" in source:
            draft["seed"] = source["seed"]
        if "score" in retain:
            if cot == "off":
                skip("The planning mode is Direct, so there is no score to retain.")
            elif changes.get("abc"):
                skip("Edited notation replaces the saved score.")
            else:
                reference, reason = self._stage_reference(subject, "score")
                if reference:
                    draft["score_source"], draft["abc"] = reference, ""
                else:
                    skip(reason)
        if "score_source" not in draft and cot != "off" and "abc" not in changes:
            draft["abc"] = source.get("abc", "")
        if "performance" in retain:
            if "max_seconds" in changes:
                raise ValueError("A retained performance fixes the duration. Change max_seconds only without retaining the performance.")
            reference, reason = self._stage_reference(subject, "performance")
            if reference:
                draft["performance_source"] = reference
            else:
                skip(reason)
        if continuing:
            for key in ("parent_track_id", "review_id"):
                if source.get(key):
                    draft[key] = source[key]
        elif subject["track_id"]:
            draft["parent_track_id"] = subject["track_id"]
        draft.update(copy.deepcopy(changes))
        if "loras" in changes:
            draft.pop("lora_provenance", None)
        return draft, notes

    def _changes(self, value, allowed=None):
        if value is None:
            return {}
        allowed = CHANGEABLE if allowed is None else allowed
        if not isinstance(value, dict):
            raise ValueError("changes must be an object of recipe fields.")
        unknown = set(value) - set(allowed)
        if unknown:
            raise ValueError("These inputs cannot be changed here: " + ", ".join(sorted(unknown)) +
                             ". References are chosen with retain. Changeable: " + ", ".join(sorted(allowed)) + ".")
        return value

    def _retention(self, value, default):
        if value is None:
            return list(default), False
        if not isinstance(value, list) or any(item not in ("seed", "score", "performance") for item in value):
            raise ValueError("retain must be a list of seed, score and performance.")
        return list(dict.fromkeys(value)), True

    def _parent_link(self, subject):
        if subject["operation_id"]:
            return {"kind": "operation", "id": subject["operation_id"]}
        if subject["track_id"]:
            return {"kind": "track", "id": subject["track_id"]}
        return {"kind": "job", "id": subject["job_id"]}

    def _origin(self, subject):
        return {"id": subject["id"], "operation_id": subject["operation_id"], "job_id": subject["job_id"],
                "track_id": subject["track_id"], "family_id": subject["family_id"]}

    def _selection_for(self, subject, requested):
        """The selection a derived take comes from: the one named, or the take's latest in its family."""
        with self.store.db() as db:
            if requested is not None:
                row = db.execute("SELECT * FROM selections WHERE id=?", (identity(requested, "Selection"),)).fetchone()
                if row is None:
                    raise KeyError("Selection not found.")
                if row["take_job_id"] != self._take_key(subject):
                    raise ValueError("That selection is for a different take.")
                return row["id"]
            if not subject["family_id"]:
                return None
            row = db.execute("SELECT id,take_job_id FROM selections WHERE family_id=? ORDER BY created DESC,id LIMIT 1",
                             (subject["family_id"],)).fetchone()
        return row["id"] if row and row["take_job_id"] == self._take_key(subject) else None

    @staticmethod
    def _take_key(subject):
        return subject["job_id"] or subject["track_id"]

    def derive(self, name, payload, replay=None):
        """Replay or develop a recorded take. ``enqueue: false`` returns the draft and queues nothing."""
        allowed = {"source", "retain", "changes", "idempotency_key", "enqueue"} | ({"replay"} if name == "take.replay" else {"selection"})
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError("Unknown input: " + ", ".join(sorted(unknown)))
        if "source" not in payload:
            raise ValueError("Send the take, job or operation to derive from as 'source'.")
        enqueue = payload.get("enqueue", True)
        if type(enqueue) is not bool:
            raise ValueError("enqueue must be true or false.")
        request = {key: payload[key] for key in payload if key != "idempotency_key"}
        if enqueue and (original := self.replayed(name, request, payload.get("idempotency_key"))):
            return original
        subject = self._subject(payload["source"])
        if name == "take.replay":
            replay = payload.get("replay")
            if replay not in ("score", "performance", "sound"):
                raise ValueError("Choose what to replay: score, performance or sound.")
        if replay == "sound":
            if "retain" in payload:
                raise ValueError("Finishing a saved sound has nothing to retain; it reuses the sound itself.")
            changes = self._changes(payload.get("changes"), ("decoder", "title"))
            reference, reason = self._stage_reference(subject, "sound")
            if not reference:
                raise ValueError(reason)
            draft, retained, notes = {"acoustic_source": reference, **copy.deepcopy(changes)}, {"sound": True}, []
        else:
            changes = self._changes(payload.get("changes"))
            default = ["seed", "score"] if replay in (None, "score") else ["seed", "performance"]
            retain, explicit = self._retention(payload.get("retain"), default)
            if replay in ("score", "performance") and replay not in retain:
                raise ValueError(f"Replaying the {replay} retains it; remove it from retain or choose another replay.")
            suffix = " — replay" if replay else " — developed"
            draft, notes = self._draft(subject, retain, strict=bool(replay) or explicit, changes=changes, suffix=suffix)
            retained = {"seed": draft.get("seed") == subject["recipe"].get("seed") and "seed" in draft,
                        "score": bool(draft.get("score_source")), "performance": bool(draft.get("performance_source"))}
        derivation = {"from": self._origin(subject), "mode": name + (f":{replay}" if replay else ""), "retained": retained,
                      "notes": notes, "changed": recipe_changes(subject["recipe"], draft),
                      "selection": self._selection_for(subject, payload.get("selection")) if name == "take.develop" else None}
        parent = self._parent_link(subject)
        if not enqueue:
            return {"draft": self.public(draft), "derivation": self.public(derivation), "parent": parent,
                    "validation": self.validate(draft)}
        return self.submit(name, request, draft, key=payload.get("idempotency_key"), parent=parent, derivation=derivation)

    def resume(self, operation_id, key=None):
        """Queue a new operation that continues an ended one from its last captured stage.

        Resuming is always an explicit request: an operation the user cancelled stays cancelled until
        this is called, and the new operation links back to the one it continues.
        """
        if original := self.replayed("operation.resume", {"operation_id": operation_id}, key):
            return original
        row = self.find(operation_id)
        job = self.store.job(row["job_id"] if row else identity(operation_id, "Operation"))
        if job["status"] in ("queued", "running", "cancelling"):
            raise ValueError("This operation is still active.")
        if job["status"] in ("done", "planned", "performed", "synthesized"):
            raise ValueError("This operation finished. Its outputs are in the receipt.")
        subject = self._subject(job["id"])
        recipe, mode = job["recipe"], job["recipe"].get("render_mode", "music")
        with self.store.db() as db:
            active = db.execute("SELECT o.id FROM operations o JOIN jobs j ON j.id=o.job_id WHERE o.resumes=? "
                                "AND j.status IN ('queued','running','cancelling')", (row["id"] if row else job["id"],)).fetchone()
        if active:
            raise Conflict(f"Operation {active['id']} is already resuming this one.")
        captured = {stage: self._stage_reference(subject, stage)[0] for stage in ("sound", "performance", "score")}
        if mode == "music" and captured["sound"]:
            draft, retain, reused = {"acoustic_source": captured["sound"]}, None, ["sound"]
        else:
            requested = {"plan": "score", "performance": "performance", "sound": "sound"}.get(mode)
            if requested and captured[requested]:
                raise ValueError(f"The requested {requested} was already captured. Its ID is in the receipt outputs.")
            retain = ["seed"] + (["performance"] if mode == "music" and captured["performance"] else
                                 ["score"] if mode != "plan" and captured["score"] and recipe.get("cot", "off") != "off" else [])
            draft, _ = self._draft(subject, retain, strict=False, changes={}, render_mode=mode, continuing=True)
            reused = [item for item in retain if item != "seed"]
        derivation = {"from": self._origin(subject), "mode": "operation.resume", "retained": {"seed": retain is not None, **{
            stage: stage in reused for stage in ("sound", "score", "performance")}}, "notes": [], "changed": [], "selection": None}
        return self.submit("operation.resume", {"operation_id": operation_id}, draft, key=key,
                           family_id=row["family_id"] if row else None,
                           parent=json.loads(row["parent"]) if row and row["parent"] else None,
                           resumes=row["id"] if row else job["id"], derivation=derivation)

    # -- selections ---------------------------------------------------------------

    def select(self, payload):
        """Record that a take was chosen within its family. Append-only; queues nothing."""
        unknown = set(payload) - {"take", "rationale", "family_id", "idempotency_key"}
        if unknown:
            raise ValueError("Unknown input: " + ", ".join(sorted(unknown)))
        if "take" not in payload:
            raise ValueError("Send the take to select as 'take'.")
        rationale = payload.get("rationale", "")
        if not isinstance(rationale, str):
            raise ValueError("The rationale must be text.")
        key = idempotency_key(payload.get("idempotency_key"))
        fingerprint = digest("take.select", {key_: payload[key_] for key_ in payload if key_ != "idempotency_key"})
        if key and (existing := self._existing_selection(key, fingerprint)):
            return existing
        subject = self._subject(payload["take"])
        if subject["status"] not in ("done", "planned", "performed", "synthesized"):
            raise ValueError("Only a completed take can be selected.")
        family = subject["family_id"]
        if payload.get("family_id") is not None:
            wanted = self.family_for(None, payload["family_id"])
            if family and family != wanted:
                raise ValueError("This take belongs to a different family.")
            family = wanted
        if not family:
            raise ValueError("This take predates operation records, so it has no family. Pass family_id to select it in an existing family.")
        selection_id = uuid.uuid4().hex
        try:
            with self.store.db() as db:
                db.execute("INSERT INTO selections (id,family_id,take_job_id,operation_id,rationale,created,idempotency_key,request_digest)"
                           " VALUES (?,?,?,?,?,?,?,?)", (selection_id, family, self._take_key(subject),
                                                       subject["operation_id"], rationale, time.time(), key, fingerprint))
        except sqlite3.IntegrityError:
            if key and (existing := self._existing_selection(key, fingerprint)):
                return existing
            raise
        return {**self.selection(selection_id), "replayed": False}

    def _existing_selection(self, key, fingerprint):
        with self.store.db() as db:
            row = db.execute("SELECT id,request_digest FROM selections WHERE idempotency_key=?", (key,)).fetchone()
        if row is None:
            return None
        if row["request_digest"] != fingerprint:
            raise Conflict("This idempotency key was already used for a different request. "
                           f"Recorded request digest {row['request_digest']}, this request {fingerprint}. Nothing was recorded.")
        return {**self.selection(row["id"]), "replayed": True}

    def selection(self, selection_id):
        with self.store.db() as db:
            row = db.execute("SELECT * FROM selections WHERE id=?", (selection_id,)).fetchone()
            if row is None:
                raise KeyError("Selection not found.")
            history = db.execute("SELECT * FROM selections WHERE family_id=? ORDER BY created DESC,id", (row["family_id"],)).fetchall()
        item = lambda r: {"id": r["id"], "family_id": r["family_id"], "take_job_id": r["take_job_id"],
                          "operation_id": r["operation_id"], "rationale": r["rationale"], "created": r["created"]}
        return self.public({"selection_id": row["id"], "family_id": row["family_id"], "selection": item(row),
                            "history": [item(r) for r in history], "current": history[0]["id"] == row["id"]})


def _object(properties, required=(), description=""):
    schema = {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    if description:
        schema["description"] = description
    return schema


def _free_object(description=""):
    return {"type": "object", "description": description} if description else {"type": "object"}


PARENT_SCHEMA = _object({"kind": {"type": "string", "enum": ["track", "job", "operation"]},
                         "id": {"type": "string", "pattern": "^[a-f0-9]{32}$"}}, ("kind", "id"))
STAGE_SCHEMA = {"type": "object", "required": ["state"], "properties": {
    "state": {"type": "string", "enum": ["not_applicable", "pending", "reused", "done", "failed", "not_produced"]},
    "from": {"type": "string"}, "error": {"type": "string"}}}
RECEIPT_SCHEMA = {
    "type": "object",
    "required": ["recorded", "unrecorded", "operation", "job", "request", "effective_recipe", "references", "runtime",
                 "derivation", "seed", "stages",
                 "outputs", "errors", "result_recipe", "terminal"],
    "properties": {
        "recorded": {"type": "boolean", "description": "False for a job made before operation records existed. "
                                                         "unrecorded lists the fields derived from the job row instead."},
        "unrecorded": {"type": "array", "items": {"type": "string"}},
        "operation": {"type": "object", "required": ["id", "name", "surface_version", "created", "family_id"]},
        "job": {"type": "object", "required": ["id", "status"]},
        "request": {"type": ["object", "null"], "description": "The input exactly as submitted; null when unrecorded."},
        "effective_recipe": _free_object("The recipe after validation and defaults, as queued."),
        "references": {"type": "array", "items": {"type": "object", "required": ["field", "kind", "id", "provenance"]}},
        "runtime": _free_object("Riff version and engine capabilities when the work was accepted."),
        "derivation": {"type": ["object", "null"], "description": "For replay, develop and resume: the source take, what was "
                       "retained (seed, score, performance, sound), notes on what could not be, the input changes against "
                       "the source, and the selection it came from. Null for other operations."},
        "seed": {"type": "string"},
        "symbolic_plan": {"type": ["object", "null"]},
        "stages": {"type": "object", "required": ["plan", "performance", "sound", "audio"],
                   "properties": {name: STAGE_SCHEMA for name in ("plan", "performance", "sound", "audio")}},
        "outputs": {"type": "object", "required": ["track_id", "score_source", "performance_source", "acoustic_source"]},
        "errors": _free_object("Stage errors by name, and the job error as 'job'."),
        "result_recipe": _free_object("The job's recipe now, including captured stage artifacts."),
        "terminal": {"type": "boolean"}}}
SUBMISSION_SCHEMA = {
    "type": "object", "required": ["operation_id", "job_id", "family_id", "parent", "status", "receipt", "replayed"],
    "properties": {"operation_id": {"type": "string"}, "job_id": {"type": "string"}, "family_id": {"type": "string"},
                   "parent": {"anyOf": [PARENT_SCHEMA, {"type": "null"}]}, "status": {"type": "string"},
                   "replayed": {"type": "boolean"}, "receipt": RECEIPT_SCHEMA}}
DERIVE_INPUT = {"source": {"type": "string", "description": "A take, job or operation ID."},
                "retain": {"type": "array", "items": {"type": "string", "enum": ["seed", "score", "performance"]},
                           "description": "What carries over. Unavailable retentions are errors when named, notes when defaulted."},
                "changes": {"type": "object", "description": "Recipe inputs to replace: " + ", ".join(CHANGEABLE) + "."},
                "enqueue": {"type": "boolean", "description": "False returns the draft and its validation and queues nothing."},
                "idempotency_key": {"type": "string"}}
DERIVE_OUTPUT = {"anyOf": [{"type": "object", "required": ["operation_id", "job_id", "family_id", "status", "receipt", "replayed"]},
                           {"type": "object", "required": ["draft", "derivation", "parent", "validation"]}]}
SELECTION_OUTPUT = {"type": "object", "required": ["selection_id", "family_id", "selection", "history", "current", "replayed"]}
KEY_SCHEMA = {"type": "string", "description": "Repeating a key with the same input returns the original operation without "
                                                 "queueing again. The same key with different input is refused with 409."}


def catalog(recipe_schema, riff_version):
    """Describe the surface for /api/capabilities."""
    base = "/api/v1/operations"
    post = lambda name, summary, input_schema, output_schema, idempotent: {
        "method": "POST", "path": f"{base}/{name}", "since": 1, "idempotent": idempotent,
        "summary": summary, "input_schema": input_schema, "output_schema": output_schema}
    return {
        "version": SURFACE_VERSION,
        "compatibility": {"riff_version": riff_version, "base_path": base,
                          "requests": {"header": {"X-Riff-Request": "1"}, "content_type": "application/json"},
                          "versioning": "Additive changes keep the version. A breaking change is published under a new "
                                        "base path and a higher version while the earlier one keeps working."},
        "operations": {
            "list": {"method": "GET", "path": base, "since": 1, "idempotent": True,
                     "summary": "Recorded operations, newest first. Query: family, status, parent, since, limit (page size, "
                                "chosen by the caller; none by default) and cursor (next_cursor from the previous page).",
                     "output_schema": {"type": "object", "required": ["operations", "next_cursor"]}},
            "get": {"method": "GET", "path": f"{base}/{{operation_id}}", "since": 1, "idempotent": True,
                    "summary": "Poll one operation and read its receipt. Accepts an operation, job or take ID.",
                    "output_schema": RECEIPT_SCHEMA},
            "recipe.validate": post("recipe.validate", "Run the submit-time validation and reference resolution without queueing.",
                                    _object({"recipe": recipe_schema}, ("recipe",)),
                                    {"type": "object", "required": ["valid"]}, True),
            "study.create": post("study.create", "Queue a recipe as a study. Returns the operation, job, family and parent "
                                 "identity before any work runs.",
                                 _object({"idempotency_key": KEY_SCHEMA, "recipe": recipe_schema,
                                          "family_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                                          "parent": PARENT_SCHEMA}, ("recipe",)),
                                 SUBMISSION_SCHEMA, True),
            "references.resolve": post("references.resolve", "Verify score, performance, sound or parent references, or list "
                                       "the references a take, job or operation offers. Each result is verified, "
                                       "incompatible or unavailable, with captured provenance and no file paths.",
                                       _object({"subject": {"type": "string"}, "score_source": {"type": "string"},
                                                "performance_source": {"type": "string"}, "acoustic_source": {"type": "string"},
                                                "parent_track_id": {"type": "string"}}),
                                       {"type": "object", "required": ["references"], "properties": {"references": {
                                           "type": "array", "items": {"type": "object", "required": ["field", "kind", "id", "status", "provenance"],
                                                                      "properties": {"status": {"type": "string", "enum": ["verified", "incompatible", "unavailable"]}}}}}}, True),
            "inputs.compare": post("inputs.compare", "Compare the recorded inputs and captured score/performance of two or more "
                                   "takes, jobs or operations: what they share and where they differ.",
                                   _object({"subjects": {"type": "array", "items": {"type": "string"}}}, ("subjects",)),
                                   {"type": "object", "required": ["subjects", "shared", "differences"]}, True),
            "take.replay": post("take.replay", "Replay a take's exact score, saved performance or saved sound as a new take. "
                                "Retains the seed by default; a stage that cannot be replayed is an error. Records parent, "
                                "family and what was retained.",
                                _object({**DERIVE_INPUT, "replay": {"type": "string", "enum": ["score", "performance", "sound"]}},
                                        ("source", "replay")), DERIVE_OUTPUT, True),
            "take.develop": post("take.develop", "Develop a take into a new one, for example a longer take from a study. Keeps "
                                 "the seed and the verified score by default, with every recorded input editable through "
                                 "changes. Records parent, family, what was retained and the selection it came from. This is the "
                                 "promotion step for a chosen study.",
                                 _object({**DERIVE_INPUT, "selection": {"type": "string"}}, ("source",)), DERIVE_OUTPUT, True),
            "take.select": post("take.select", "Record a durable, append-only choice of a completed take within its family, with "
                                "a rationale. Queues nothing.",
                                _object({"take": {"type": "string"}, "rationale": {"type": "string"},
                                         "family_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                                         "idempotency_key": KEY_SCHEMA}, ("take",)), SELECTION_OUTPUT, True),
            "operation.resume": post("operation.resume", "Queue a new operation that continues an interrupted, failed or "
                                     "cancelled one from its last captured stage, with the same seed. Never happens "
                                     "implicitly.",
                                     _object({"operation_id": {"type": "string"}, "idempotency_key": KEY_SCHEMA},
                                             ("operation_id",)), SUBMISSION_SCHEMA, True),
            "operation.cancel": post("operation.cancel", "Cancel the operation's job. Completed stages stay in the receipt.",
                                     _object({"operation_id": {"type": "string"}}, ("operation_id",)),
                                     {"type": "object", "required": ["operation_id", "job_id", "status", "receipt"]}, True),
        },
    }
