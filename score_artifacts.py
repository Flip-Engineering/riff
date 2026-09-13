"""Transitional library adapter; score integrity and file operations run in Elixir."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

import run as engine
from symbolic import read_plan
from model_admission import SchedulerNotInstalled, SchedulerUnavailable


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _directory(base, *parts):
    current = Path(base)
    for ancestor in (current, *current.parents):
        if not stat.S_ISDIR(ancestor.lstat().st_mode):
            raise ValueError("The score storage directory is linked or unavailable.")
    for part in parts:
        if not part or part in (".", "..") or "/" in part or "\\" in part:
            raise ValueError("The score storage path is invalid.")
        parent = current
        current = current / part
        current.mkdir(exist_ok=True)
        if not stat.S_ISDIR(current.lstat().st_mode):
            raise ValueError("The score storage directory is linked or unavailable.")
        _sync_directory(parent)
    return current


def _write_once(path, content):
    """Publish a fully synced launch/snapshot file without replacing evidence."""
    _directory(path.parent)
    if path.exists() or path.is_symlink():
        if not stat.S_ISREG(path.lstat().st_mode) or path.read_bytes() != content:
            raise ValueError("The saved generation inputs have changed.")
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
        _sync_directory(path.parent)
        return
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    with temporary.open("xb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        if not stat.S_ISREG(path.lstat().st_mode) or path.read_bytes() != content:
            raise ValueError("The saved generation inputs have changed.")
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
    _sync_directory(path.parent)
    temporary.unlink()  # Only this invocation's successfully published scratch link.
    _sync_directory(path.parent)


class ScoreArtifacts:
    def __init__(self, store, control):
        self.store, self.control = store, control
        self.root = _directory(store.outputs, "riff", "scores")
        self.vocabularies = _directory(self.root, ".vocabularies")
        self.jobs = _directory(store.data_root, "jobs")
        with store.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS score_artifacts (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, title TEXT NOT NULL, created REAL NOT NULL)")

    def enabled(self):
        return engine.engine_capabilities()["exact_score_replay"] and self.control.supports_artifacts()

    def _job_directory(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("The generation reference is invalid.")
        return _directory(self.jobs, job_id)

    def input_path(self, job_id):
        return self._job_directory(job_id) / "inputs" / "score.json"

    def _call(self, action, cancelled=None, **arguments):
        try:
            return self.control.artifact(action, {"root": str(self.root), **arguments}, cancelled=cancelled)
        except SchedulerNotInstalled:
            raise ValueError("The saved-score runtime is not installed. Install the desktop app or build the source control runtime.") from None

    def resolve(self, reference, contract=None, cancelled=None, recorded=False):
        with self.store.db() as db:
            row = db.execute("SELECT * FROM score_artifacts WHERE id=?", (reference,)).fetchone()
        if row is None:
            raise ValueError("This saved score is not in the library. Choose a score or use editable notation.")
        result = self._call("resolve", artifact_id=reference, cancelled=cancelled,
                            contract=None if recorded else contract or engine.score_contract())
        return result

    def recipe(self, recipe, cancelled=None):
        recipe = dict(recipe)
        recipe.pop("score_input", None)
        if recipe.get("score_source"):
            result = self.resolve(recipe["score_source"], cancelled=cancelled)
            recipe["score_input"] = {"id": result["id"], **result["descriptor"]["score"]}
        return recipe

    def launch(self, job_id, recipe, cancelled=None):
        """Bind finalized inputs before the model child; recovery uses these bytes."""
        if not self.enabled():
            if recipe.get("score_source"):
                engine.score_contract()  # Explain only the unavailable operation.
                raise ValueError("The saved-score runtime is not installed. Install the desktop app or build the source control runtime.")
            return recipe
        recipe = self.recipe(recipe, cancelled=cancelled)
        recipe.pop("launch_receipt_sha256", None)
        settings = engine.platform_runtime.settings()
        contract = engine.score_contract(settings)
        vocabulary = Path(settings["model_root"]) / "sidecars/yue2-qwen.tiktoken"
        vocabulary_bytes = vocabulary.read_bytes()
        if hashlib.sha256(vocabulary_bytes).hexdigest() != contract["tokenizer_sha256"]:
            raise ValueError("The selected tokenizer changed before generation.")
        _write_once(self.vocabularies / (contract["tokenizer_sha256"] + ".tiktoken"), vocabulary_bytes)
        directory = self._job_directory(job_id)
        if recipe.get("score_source"):
            self._call("prepare", artifact_id=recipe["score_source"], contract=contract, cancelled=cancelled,
                       input_directory=str(_directory(directory, "inputs")))
        components = {}
        for name in ("yue2-model-config.json", "yue2-generation-config.json", "yue2-vae-config.json"):
            path = Path(settings["model_root"]) / "sidecars" / name
            components[name] = engine.file_digest(path)
        # Weight files may be user supplied. Record their actual file identity;
        # never invent a capture-time digest or rehash gigabytes per queue call.
        weights = {}
        for name in (settings["model_file"], settings["vae_file"]):
            info = (Path(settings["model_root"]) / name).stat()
            weights[name] = {"bytes": info.st_size, "mtime_ns": info.st_mtime_ns,
                             "inode": info.st_ino, "device": info.st_dev}
        launch = {"format_version": 1, "job_id": job_id, "contract": contract,
                  "engine_sha256": engine.file_digest(settings["binary"]), "sidecars": components,
                  "weight_file_identity": weights, "recipe": recipe,
                  "recipe_sha256": hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()}
        encoded = json.dumps(launch, sort_keys=True).encode()
        _write_once(directory / "launch.json", encoded)
        recipe["launch_receipt_sha256"] = hashlib.sha256(encoded).hexdigest()
        return recipe

    def capture(self, job_id, recipe, cancelled=None):
        receipt = self._job_directory(job_id) / "launch.json"
        if not receipt.exists():
            return None
        if not stat.S_ISREG(receipt.lstat().st_mode):
            raise ValueError("The generation's score receipt is linked or unavailable.")
        expected = recipe.get("launch_receipt_sha256")
        if not expected:
            return None
        if not self.control.supports_artifacts():
            return None  # Pre-desktop source installs retain their editable ABC path.
        if engine.file_digest(receipt) != expected:
            raise ValueError("The generation's score receipt has changed.")
        launch = json.loads(receipt.read_text())
        if launch.get("job_id") != job_id or launch.get("format_version") != 1:
            raise ValueError("The generation's score receipt is invalid.")
        contract = launch["contract"]
        output_root = self.store.outputs / "riff"
        raw = output_root / (job_id + ".plan.json")
        if raw.exists() or raw.is_symlink():
            result = self._call("capture", source_root=str(output_root), source_path=str(raw), cancelled=cancelled,
                                contract=contract, provenance={"kind": "native_capture", "launch": launch,
                                "parent_score": launch["recipe"].get("score_source", "")})
            with self.store.db() as db:
                db.execute("INSERT OR IGNORE INTO score_artifacts VALUES (?,?,?,?)",
                           (result["id"], job_id, recipe["title"], time.time()))
        elif launch["recipe"].get("score_source"):
            # Saved-performance rendering returns before a new score file is
            # emitted. Carry the verified used score; do not call it generated.
            result = self.resolve(launch["recipe"]["score_source"], contract, cancelled=cancelled)
        else:
            return None
        plan = self._display(result)
        return {**plan, "artifact_id": result["id"],
                "stage": "captured" if raw.exists() else "reused", "provenance": "captured"}

    def _display(self, result):
        # A readable view is optional. A failed decode must never discard a
        # verified token artifact or invalidate an otherwise complete stage.
        contract = result["descriptor"]["contract"]
        vocabulary = self.vocabularies / (contract["tokenizer_sha256"] + ".tiktoken")
        plan = {**result["descriptor"]["score"], "abc": ""}
        try:
            if engine.file_digest(vocabulary) != contract["tokenizer_sha256"]:
                raise ValueError("The saved score's tokenizer snapshot has changed.")
            decoded = read_plan(result["path"], vocabulary, write=False)
            if decoded is None:
                raise ValueError("The score's notation is unavailable.")
            plan.update(decoded)
        except (ValueError, OSError, KeyError, TypeError):
            plan["display_error"] = "The notation view is unavailable. The saved score can still be reused."
        return plan

    def describe(self, reference, cancelled=None):
        result = self.resolve(reference, recorded=True, cancelled=cancelled)
        try:
            compatible = result["descriptor"]["contract"] == engine.score_contract()
        except (ValueError, OSError, KeyError, TypeError):
            compatible = False
        plan = self._display(result)
        with self.store.db() as db:
            row = db.execute("SELECT title,job_id FROM score_artifacts WHERE id=?", (reference,)).fetchone()
        return {"id": reference, "artifact_id": reference, "title": row["title"], "source_job_id": row["job_id"],
                **result["descriptor"]["score"], **plan, "cot": result["descriptor"]["provenance"]["launch"]["recipe"]["cot"],
                "provenance": "captured", "compatible": compatible}

    def available(self, source, cancelled=None):
        references = [source.get("score_source"), (source.get("symbolic_plan") or {}).get("artifact_id")]
        arrangement = source.get("arrangement") or {}
        recipes = arrangement.get("source_recipes") or {}
        for recipe in recipes.values() if isinstance(recipes, dict) else recipes:
            if isinstance(recipe, dict):
                references.extend([recipe.get("score_source"), (recipe.get("symbolic_plan") or {}).get("artifact_id")])
        available = []
        for reference in dict.fromkeys(value for value in references if value):
            try:
                described = self.describe(reference, cancelled=cancelled)
                if described["compatible"]:
                    available.append(described)
            except (ValueError, OSError, KeyError, SchedulerUnavailable):
                continue
        return available
