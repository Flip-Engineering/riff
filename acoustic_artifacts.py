"""Transitional studio adapter; acoustic validation and storage run in Elixir."""
import copy
import hashlib
import json
import re
import stat
import struct
import time

import run as engine
from model_admission import SchedulerNotInstalled, SchedulerUnavailable
from score_artifacts import _directory, _write_once


UPSTREAM = ("lyrics", "style", "abc", "score_source", "mode", "cot", "max_seconds",
            "steps", "solver", "cfg_scale", "temperature", "seed", "refinement", "performance_source")


class AcousticArtifacts:
    def __init__(self, store, control):
        self.store, self.control = store, control
        self.root = _directory(store.outputs, "riff", "acoustics")
        self.jobs = _directory(store.data_root, "jobs")
        with store.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS acoustic_artifacts (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, title TEXT NOT NULL, created REAL NOT NULL)")

    def enabled(self):
        return engine.engine_capabilities().get("acoustic_checkpoint", False) and self.control.supports_artifacts("acoustic")

    def _job_directory(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("The generation reference is invalid.")
        return _directory(self.jobs, job_id)

    def input_path(self, job_id):
        return self._job_directory(job_id) / "inputs" / "acoustic.yac"

    def _call(self, action, cancelled=None, **arguments):
        try:
            return self.control.artifact(action, {"kind": "acoustic", "root": str(self.root), **arguments}, cancelled=cancelled)
        except SchedulerNotInstalled:
            raise ValueError("Update Riff to use saved sound synthesis.") from None

    def resolve(self, reference, recorded=False, cancelled=None):
        if not isinstance(reference, str) or not re.fullmatch(r"riff-acoustic-v1:[a-f0-9]{64}", reference):
            raise ValueError("The saved sound reference is invalid.")
        with self.store.db() as db:
            row = db.execute("SELECT id FROM acoustic_artifacts WHERE id=?", (reference,)).fetchone()
        if row is None:
            raise ValueError("This saved sound is not in the library.")
        return self._call("resolve", artifact_id=reference, contract=None if recorded else engine.decoder_contract(), cancelled=cancelled)

    def recipe(self, payload, cancelled=None):
        """A decoder take retains the actual upstream inputs and completed stages."""
        if not self.enabled():
            raise ValueError("Update Riff and the selected engine to finish this saved sound.")
        result = self.resolve(payload["acoustic_source"], cancelled=cancelled)
        provenance = result["descriptor"]["provenance"]
        original = provenance["launch"]["recipe"]
        for key in UPSTREAM:
            if key in payload and payload[key] != original.get(key, "" if key.endswith("_source") else None):
                raise ValueError("Saved sound already contains its music. Choose a new performance to change the composition or synthesis.")
        if payload.get("render_mode", "music") != "music":
            raise ValueError("Choose audio to finish saved sound synthesis.")
        recipe = copy.deepcopy(original)
        for key in ("launch_receipt_sha256", "acoustic_launch_sha256", "artifact_errors", "score_input", "stage_capture_pending"):
            recipe.pop(key, None)
        recipe.update(copy.deepcopy(provenance.get("stages", {})))
        recipe.update(acoustic_source=result["id"], acoustic=self._metadata(result),
                      decoder=engine.decoder_options(payload.get("decoder", {})), render_mode="music",
                      parent_track_id=provenance["launch"]["job_id"])
        if "title" in payload:
            if not isinstance(payload["title"], str):
                raise ValueError("Title must be text.")
            recipe["title"] = payload["title"].strip() or original["title"]
        if payload.get("review_id"):
            if not isinstance(payload["review_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", payload["review_id"]):
                raise ValueError("The review reference is invalid.")
            recipe["review_id"] = payload["review_id"]
        return recipe

    def launch(self, job_id, recipe, cancelled=None):
        recipe = dict(recipe)
        if recipe.get("acoustic_source"):
            # Revalidate and copy before every child, including a queued retry.
            recipe = self.recipe(recipe, cancelled=cancelled)
            decoder = engine.decoder_contract()
            self._call("prepare", artifact_id=recipe["acoustic_source"], contract=decoder,
                       input_directory=str(_directory(self._job_directory(job_id), "inputs")), cancelled=cancelled)
            launch = {"format_version": 1, "job_id": job_id, "operation": "decode", "decoder": decoder,
                      "recipe": recipe, "engine_sha256": engine.file_digest(engine.platform_runtime.settings()["binary"])}
        else:
            if recipe.get("render_mode") not in ("music", "sound") or not self.enabled():
                if recipe.get("render_mode") == "sound":
                    raise ValueError("Update Riff and the selected engine to save sound synthesis.")
                return recipe
            settings = engine.platform_runtime.settings()
            from pathlib import Path
            model = Path(settings["model_root"])
            sidecar_names = {"model_config": "yue2-model-config.json", "decoder_config": "yue2-vae-config.json",
                             "tokenizer": "yue2-qwen.tiktoken", "generation_config": "yue2-generation-config.json"}
            sidecars = {}
            for key, name in sidecar_names.items():
                path = model / "sidecars" / name
                sidecars[key] = (hashlib.sha256(b"").hexdigest() if key == "generation_config" and not path.exists()
                                 else engine.file_digest(path))
            launch = {"format_version": 1, "job_id": job_id, "operation": "synthesize", "recipe": recipe,
                      "engine_sha256": engine.file_digest(settings["binary"]),
                      "main_sha256": engine.file_digest(model / settings["model_file"]),
                      "decoder": engine.decoder_contract(settings), "sidecars": sidecars}
        encoded = json.dumps(launch, sort_keys=True).encode()
        _write_once(self._job_directory(job_id) / "acoustic-launch.json", encoded)
        recipe["acoustic_launch_sha256"] = hashlib.sha256(encoded).hexdigest()
        return recipe

    def capture(self, job_id, recipe, cancelled=None):
        expected = recipe.get("acoustic_launch_sha256")
        if not expected:
            return None
        receipt = self._job_directory(job_id) / "acoustic-launch.json"
        if not stat.S_ISREG(receipt.lstat().st_mode) or engine.file_digest(receipt) != expected:
            raise ValueError("The saved sound's generation inputs have changed.")
        launch = json.loads(receipt.read_text())
        if launch.get("job_id") != job_id or launch.get("format_version") != 1:
            raise ValueError("The saved sound's generation inputs are invalid.")
        if launch["operation"] == "decode":
            return self._metadata(self.resolve(launch["recipe"]["acoustic_source"], recorded=True, cancelled=cancelled))
        output_root = self.store.outputs / "riff"
        raw = output_root / (job_id + ".yac")
        if not raw.exists() and not raw.is_symlink():
            return None
        result = self._call("capture", source_root=str(output_root), source_path=str(raw), cancelled=cancelled,
                            provenance={"launch": launch, "stages": {key: recipe[key] for key in ("symbolic_plan", "performance") if key in recipe}})
        metadata = result["descriptor"]["acoustic"]
        # The native container binds the files actually used, not just their names.
        inputs = launch["recipe"]
        guidance = struct.unpack("<f", struct.pack("<f", inputs["cfg_scale"]))[0]
        if (metadata["hashes"]["decoder"] != launch["decoder"]["sha256"] or
                metadata["hashes"]["model"] != launch["main_sha256"] or
                metadata["hashes"]["producer_binary"] != launch["engine_sha256"] or
                any(metadata["hashes"][key] != digest for key, digest in launch["sidecars"].items()) or
                metadata["seed"] != str(inputs["seed"]) or metadata["steps"] != inputs["steps"] or
                metadata["solver"] != inputs["solver"] or metadata["guidance"] != guidance or
                metadata["hashes"]["semantic_codes"] != (recipe.get("performance") or {}).get("sha256")):
            raise ValueError("The completed sound does not match its generation inputs.")
        with self.store.db() as db:
            db.execute("INSERT OR IGNORE INTO acoustic_artifacts VALUES (?,?,?,?)",
                       (result["id"], job_id, launch["recipe"]["title"], time.time()))
        return self._metadata(result)

    @staticmethod
    def _metadata(result):
        return {"artifact_id": result["id"], **result["descriptor"]["acoustic"]}

    def describe(self, reference, cancelled=None):
        result = self.resolve(reference, recorded=True, cancelled=cancelled)
        metadata = self._metadata(result)
        try:
            decoder = engine.decoder_contract()
            compatible = (metadata["hashes"]["decoder"] == decoder["sha256"] and
                          all(metadata[key] == decoder[key] for key in ("sample_rate", "channels", "latent_dim", "encoder_latent_dim", "downsampling_ratio")))
            compatible = compatible and self.control.supports_artifacts("acoustic")
        except (ValueError, OSError, KeyError, SchedulerUnavailable):
            compatible = False
        launch = result["descriptor"]["provenance"]["launch"]
        return {"id": reference, **metadata, "title": launch["recipe"]["title"], "source_job_id": launch["job_id"],
                "compatible": compatible, "inputs": copy.deepcopy(launch["recipe"]),
                "decoder_controls": engine.decoder_schema()}

    def available(self, source, cancelled=None):
        references = [source.get("acoustic_source"), (source.get("acoustic") or {}).get("artifact_id")]
        result = []
        for reference in dict.fromkeys(value for value in references if value):
            try:
                item = self.describe(reference, cancelled=cancelled)
                if item["compatible"]:
                    result.append(item)
            except (ValueError, OSError, KeyError, SchedulerUnavailable):
                continue
        return result
