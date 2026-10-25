"""Named study families on top of the shared operation record.

A family is the ``family_id`` operations already carry. This module adds the name, the shared inputs
the caller declared, a place for provenance, and the read model: which inputs the studies share and
what each take changes. Nothing is inferred from musical similarity, no recipe is edited, and no
number of takes is fixed.
"""
import base64
import json
import sqlite3
import time
import uuid

from operations import Conflict, SUBMISSION_SCHEMA, digest, identity, idempotency_key, recipe_changes

SCHEMA = """
    CREATE TABLE IF NOT EXISTS families (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
        shared TEXT NOT NULL DEFAULT '{}', context TEXT NOT NULL DEFAULT '{}',
        idempotency_key TEXT, request_digest TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS families_key ON families(idempotency_key) WHERE idempotency_key IS NOT NULL;
"""
ROLES = {"study.create": "study", "generate": "study", "take.develop": "develop", "take.replay": "replay",
         "operation.resume": "resume"}


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text.")
    return value.strip()


def object_value(value, label):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    json.dumps(value, allow_nan=False)
    return value


class Families:
    def __init__(self, operations):
        self.operations, self.store = operations, operations.store

    # -- writes ---------------------------------------------------------------

    def create(self, payload):
        """Create a named family and queue one study per take; repeating the request finishes what is missing."""
        unknown = set(payload) - {"name", "shared", "takes", "context", "idempotency_key"}
        if unknown:
            raise ValueError("Unknown input: " + ", ".join(sorted(unknown)))
        key = idempotency_key(payload.get("idempotency_key"))
        name = text(payload.get("name"), "The family name")
        shared, context = object_value(payload.get("shared"), "shared"), object_value(payload.get("context"), "context")
        takes = payload.get("takes")
        if not isinstance(takes, list) or not takes:
            raise ValueError("Send at least one study in 'takes'.")
        if any(not isinstance(take, dict) for take in takes):
            raise ValueError("Each take must be an object of recipe fields.")
        recipes = [{**shared, **take} for take in takes]
        fingerprint = digest("family.create", {field: payload[field] for field in payload if field != "idempotency_key"})
        family_id, replayed = self._claim(key, fingerprint)
        creating = family_id is None
        if creating:
            for position, recipe in enumerate(recipes, 1):
                checked = self.operations.validate(recipe)
                if not checked["valid"]:
                    raise ValueError(f"Study {position}: {checked['error']}")
            family_id, now = uuid.uuid4().hex, time.time()

        def record(db):
            db.execute("INSERT INTO families (id,name,created,updated,shared,context,idempotency_key,request_digest)"
                       " VALUES (?,?,?,?,?,?,?,?)", (family_id, name, now, now, json.dumps(shared), json.dumps(context), key, fingerprint))
        submissions = []
        # Each study has its own key, derived from the family, so a repeated request queues only the missing ones.
        for position, recipe in enumerate(recipes):
            request = {"recipe": recipe, "family_id": family_id}
            try:
                # The family is saved in the same transaction as its first accepted study, so a refused
                # first study (restart pending, engine not set up, setup running) leaves no family behind.
                submissions.append(self.operations.submit("study.create", request, recipe, key=f"family:{family_id}:{position}",
                                                          family_id=family_id, also=record if creating and position == 0 else None))
            except sqlite3.IntegrityError:
                # Another request with this key created the family between our check and our insert.
                family_id, replayed = self._claim(key, fingerprint)
                if family_id is None:
                    raise
                creating = False
                request = {"recipe": recipe, "family_id": family_id}
                submissions.append(self.operations.submit("study.create", request, recipe, key=f"family:{family_id}:{position}", family_id=family_id))
            except (ValueError, KeyError) as error:
                if position:
                    # Some studies are already queued. Repeating the request finishes them only when it carries
                    # the same key; without one it would start a second family, so name this one instead.
                    message = error.args[0] if error.args else str(error)
                    have = f"The family has {position} of {len(recipes)} studies"
                    advice = ("repeat the same request to add the rest." if key else
                              f"it is family {family_id}; read it with GET /api/v1/families/{family_id} and add the rest with "
                              "study.create and that family_id. Repeating this request would start a second family.")
                    raise type(error)(f"Study {position + 1} was not queued ({message}). {have}; {advice}") from None
                raise
        return {"family_id": family_id, "replayed": replayed, "submissions": submissions, "family": self.get(family_id)}

    def _claim(self, key, fingerprint):
        if not key:
            return None, False
        with self.store.db() as db:
            row = db.execute("SELECT id,request_digest FROM families WHERE idempotency_key=?", (key,)).fetchone()
        if row is None:
            return None, False
        if row["request_digest"] != fingerprint:
            raise Conflict("This idempotency key was already used for a different request. "
                           f"Recorded request digest {row['request_digest']}, this request {fingerprint}. Nothing was queued.")
        return row["id"], True

    def rename(self, payload):
        """Name a family, including one started implicitly, or rename it. Only the name and its update time change."""
        unknown = set(payload) - {"family_id", "name"}
        if unknown:
            raise ValueError("Unknown input: " + ", ".join(sorted(unknown)) + ". A family's context is provenance and is set when it is created.")
        family_id = identity(payload.get("family_id"), "Family")
        name = text(payload.get("name"), "The family name")
        now = time.time()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT 1 FROM families WHERE id=?", (family_id,)).fetchone()
            if row is None:
                if not db.execute("SELECT 1 FROM operations WHERE family_id=? LIMIT 1", (family_id,)).fetchone():
                    raise KeyError("Family not found.")
                first = db.execute("SELECT MIN(created) FROM operations WHERE family_id=?", (family_id,)).fetchone()[0]
                db.execute("INSERT INTO families (id,name,created,updated) VALUES (?,?,?,?)", (family_id, name, first, now))
            else:
                db.execute("UPDATE families SET name=?,updated=? WHERE id=?", (name, now, family_id))
        return self.get(family_id)

    # -- reads ----------------------------------------------------------------

    def get(self, family_id):
        identity(family_id, "Family")
        with self.store.db() as db:
            row = db.execute("SELECT * FROM families WHERE id=?", (family_id,)).fetchone()
            operations = [r["id"] for r in db.execute("SELECT id FROM operations WHERE family_id=? ORDER BY created,id", (family_id,))]
            history = [dict(r) for r in db.execute("SELECT * FROM selections WHERE family_id=? ORDER BY created DESC,id", (family_id,))]
        if row is None and not operations:
            raise KeyError("Family not found.")
        takes, recipes = [], []
        for operation_id in operations:
            receipt = self.operations.receipt(operation_id)
            operation, job = receipt["operation"], receipt["job"]
            takes.append({"operation_id": operation["id"], "job_id": job["id"], "track_id": receipt["outputs"]["track_id"],
                          "name": operation["name"], "role": ROLES.get(operation["name"], "other"), "status": job["status"],
                          "title": receipt["result_recipe"].get("title") or receipt["effective_recipe"].get("title", ""),
                          "created": operation["created"], "parent": operation["parent"], "resumes": operation["resumes"],
                          "seed": receipt["seed"], "stages": {stage: item["state"] for stage, item in receipt["stages"].items()},
                          "outputs": receipt["outputs"], "derivation": receipt["derivation"],
                          "settings": {key: receipt["result_recipe"][key] for key in
                                       ("max_seconds", "steps", "solver", "cot", "style", "lyrics", "cfg_scale", "temperature")
                                       if key in receipt["result_recipe"]}})
            recipes.append(receipt["result_recipe"])
        by_id = {}
        for index, take in enumerate(takes):
            for reference in (take["operation_id"], take["job_id"], take["track_id"]):
                if reference:
                    by_id[reference] = index
        # The studies are what a family compares. Without any, the takes that start it are.
        studies = [index for index, take in enumerate(takes) if take["role"] == "study"]
        compared = studies or [index for index, take in enumerate(takes) if (take["parent"] or {}).get("id") not in by_id]
        common, differing = self.operations.split_inputs([self.operations.input_view(recipes[index]) for index in compared])
        differences = dict(zip(compared, differing))
        for index, take in enumerate(takes):
            # A study is shown against the other studies, a take grown from another against the take it continues.
            take["diff"] = differences[index] if take["role"] == "study" else self._against_parent(take, recipes[index], recipes, by_id)
            take["children"] = []
        for index, take in enumerate(takes):
            parent = by_id.get((take["parent"] or {}).get("id"))
            if parent is not None and parent != index:
                takes[parent]["children"].append(take["operation_id"])
        current = history[0] if history else None
        for take in takes:
            take["selected"] = bool(current) and current["take_job_id"] in (take["job_id"], take["track_id"])
        created = row["created"] if row else min(take["created"] for take in takes)
        item = lambda r: {"id": r["id"], "take_job_id": r["take_job_id"], "operation_id": r["operation_id"],
                          "rationale": r["rationale"], "created": r["created"]}
        return {"family_id": family_id, "named": row is not None, "name": row["name"] if row else None, "created": created,
                "updated": row["updated"] if row else created, "context": json.loads(row["context"]) if row else {},
                "declared_shared": json.loads(row["shared"]) if row else {}, "common": common, "takes": takes,
                "selection": {"current": item(current) if current else None, "history": [item(r) for r in history]}}

    def _against_parent(self, take, recipe, recipes, by_id):
        reference = (take["parent"] or {}).get("id")
        if reference in by_id:
            before = recipes[by_id[reference]]
        else:
            try:
                before = self.operations._subject(reference)["recipe"] if reference else None
            except (KeyError, ValueError):
                before = None
        if before is None:
            return None
        return [{"field": item["field"], "label": item["label"], "value": item["after"], "recorded": item["after_recorded"],
                 "before": item["before"], "before_recorded": item["before_recorded"]}
                for item in recipe_changes(before, recipe)]

    def list(self, named=None, limit=None, cursor=None):
        """Families, most recently active first. Without a limit every family is returned."""
        if named not in (None, "true", "false"):
            raise ValueError("named must be true or false.")
        with self.store.db() as db:
            rows = {r["id"]: dict(r) for r in db.execute(
                "SELECT family_id AS id, COUNT(*) AS takes, MIN(created) AS first, MAX(created) AS latest "
                "FROM operations GROUP BY family_id")}
            names = {r["id"]: dict(r) for r in db.execute("SELECT id,name,created,updated FROM families")}
            newest = {r["family_id"]: r["status"] for r in db.execute(
                "SELECT o.family_id, j.status FROM operations o JOIN jobs j ON j.id=o.job_id "
                "WHERE o.created=(SELECT MAX(created) FROM operations WHERE family_id=o.family_id)")}
            chosen = {r["family_id"]: r["take_job_id"] for r in db.execute(
                "SELECT s.family_id, s.take_job_id FROM selections s "
                "WHERE s.created=(SELECT MAX(created) FROM selections WHERE family_id=s.family_id)")}
        for identifier, row in names.items():
            rows.setdefault(identifier, {"id": identifier, "takes": 0, "first": row["created"], "latest": row["updated"]})
        items = []
        for identifier, row in rows.items():
            if named is not None and (identifier in names) != (named == "true"):
                continue
            items.append({"family_id": identifier, "named": identifier in names, "name": names[identifier]["name"] if identifier in names else None,
                          "takes": row["takes"], "created": row["first"], "latest": row["latest"],
                          "latest_status": newest.get(identifier), "selected_take": chosen.get(identifier)})
        items.sort(key=lambda item: (-item["latest"], item["family_id"]))
        if cursor is not None:
            try:
                latest, last = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii") + b"=" * (-len(cursor) % 4)))
                latest = float(latest)
                identity(last, "Cursor")
            except (ValueError, TypeError, UnicodeError):
                raise ValueError("The cursor is invalid. Use next_cursor from an earlier page.") from None
            items = [item for item in items if (-item["latest"], item["family_id"]) > (-latest, last)]
        more = False
        if limit is not None:
            try:
                limit = int(limit)
            except (ValueError, TypeError):
                raise ValueError("The page size must be a whole number.") from None
            if limit < 1:
                raise ValueError("The page size must be at least one.")
            more, items = len(items) > limit, items[:limit]
        token = (base64.urlsafe_b64encode(json.dumps([items[-1]["latest"], items[-1]["family_id"]]).encode()).decode().rstrip("=")
                 if more else None)
        return {"families": items, "next_cursor": token}


def catalog(recipe_schema):
    """Describe the family operations for /api/capabilities."""
    base = "/api/v1/operations"
    shared_schema = {"type": "object", "description": "Recipe fields common to every take; each take is {...shared, ...take}."}
    selection = {"type": ["object", "null"], "properties": {"current": {"type": ["object", "null"]}, "history": {"type": "array"}}}
    field = {"type": "object", "required": ["field", "label", "value", "recorded"]}
    family = {
        "type": "object", "required": ["family_id", "named", "name", "created", "context", "declared_shared", "common", "takes", "selection"],
        "properties": {
            "named": {"type": "boolean"}, "name": {"type": ["string", "null"]},
            "declared_shared": {"type": "object", "description": "The shared recipe fields the caller declared when it created the "
                                "family. Provenance only: it is never merged into a take added later."},
            "common": {"type": "array", "items": {"type": "object", "required": ["field", "label", "value"]},
                       "description": "Recorded inputs every study in the family actually has in common, computed from the takes."},
            "takes": {"type": "array", "items": {"type": "object", "required": [
                "operation_id", "job_id", "track_id", "name", "role", "status", "title", "parent", "seed", "stages", "outputs",
                "settings", "diff", "children", "selected"], "properties": {
                    "role": {"type": "string", "enum": ["study", "develop", "replay", "resume", "other"]},
                    "diff": {"anyOf": [{"type": "array", "items": field}, {"type": "null"}],
                             "description": "For a study, the recorded inputs where it differs from the other studies. For a take "
                                            "grown from another, its changes against the take it continues (null if that take is unavailable)."}}}},
            "selection": selection}}
    return {
        "family.list": {"method": "GET", "path": "/api/v1/families", "since": 1, "idempotent": True,
                        "summary": "Families, most recently active first. Query: named (true|false), limit (page size, none by "
                                   "default) and cursor (next_cursor from the previous page).",
                        "output_schema": {"type": "object", "required": ["families", "next_cursor"]}},
        "family.get": {"method": "GET", "path": "/api/v1/families/{family_id}", "since": 1, "idempotent": True,
                       "summary": "A family's name, shared inputs, takes with their per-take differences, and selection history.",
                       "output_schema": family},
        "family.create": {"method": "POST", "path": f"{base}/family.create", "since": 1, "idempotent": True,
                          "summary": "Create a named family and queue one study for each recipe in takes. Every take is validated "
                                     "before any is queued. Repeating the request with the same idempotency key queues only the studies "
                                     "still missing.",
                          "input_schema": {"type": "object", "required": ["name", "takes"], "additionalProperties": False, "properties": {
                              "name": {"type": "string"}, "shared": shared_schema, "context": {"type": "object", "description":
                              "Provenance to keep with the family, for example the brief it started from."},
                              "takes": {"type": "array", "items": recipe_schema, "description": "One or more study recipes."},
                              "idempotency_key": {"type": "string"}}},
                          "output_schema": {"type": "object", "required": ["family_id", "replayed", "submissions", "family"],
                                            "properties": {"submissions": {"type": "array", "items": SUBMISSION_SCHEMA}, "family": family}}},
        "family.rename": {"method": "POST", "path": f"{base}/family.rename", "since": 1, "idempotent": True,
                          "summary": "Name a family, including one started implicitly by an earlier study, or rename it. Changes only the name; "
                                     "context is provenance and stays as it was created.",
                          "input_schema": {"type": "object", "required": ["family_id", "name"], "additionalProperties": False, "properties": {
                              "family_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"}, "name": {"type": "string"}}},
                          "output_schema": family},
    }
