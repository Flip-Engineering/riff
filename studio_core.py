"""Riff's library and resource-aware, independently cancellable model work."""
import array
from contextlib import contextmanager
import ctypes
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
import wave
import sys

import run as engine
from inspiration import inspire, seed_number
from paths import ROOT, WORKSPACE
import model_options
from model_admission import AdmissionCancelled, ModelAdmission, SchedulerUnavailable
from symbolic import read_plan

CONTEXT = model_options.CONTEXT
TOKEN_RATE = model_options.TOKEN_RATE
PRESETS = [
    ("warm-current", "Warm current", "English, intimate indie folk, fingerpicked acoustic guitar, brushed drums, warm bass, clear close lead vocal, a gentle uplifting chorus", "#8b9f83"),
    ("blue-hour", "Blue hour", "English, atmospheric alternative R&B, Rhodes piano, deep rounded bass, restrained breakbeat, breathy soulful vocal, late-night city mood", "#738fd4"),
    ("velvet-circuit", "Velvet circuit", "English, dreamy synth pop, soft analog arpeggios, shimmering guitars, warm drum machine, airy lead vocal, bittersweet and spacious", "#a093bc"),
    ("reed-room", "Reed room", "English, jazz folk, mellow clarinet, upright bass, light brushed drums, nylon-string guitar, conversational lead vocal, intimate live-room sound", "#ba986b"),
]


def observe_generation(live, lines):
    for marker, label, index in [
        ("yue2.ar.", "Finding the melody", 1),
        ("yue2.nar.", "Shaping the performance", 2),
        ("oobleck_audio_vae", "Rendering stereo audio", 3),
    ]:
        if marker in lines and index >= live["stage_index"]:
            live.update(stage=label, stage_index=index)
    if live["stage_index"] != 2:
        live.pop("stage_progress", None)
        return
    for value in re.findall(r"yue2\.nar\.progress ([0-9.eE+-]+)(?:\s|$)", lines):
        try:
            progress = float(value)
        except ValueError:
            continue
        if math.isfinite(progress) and 0 <= progress <= 1:
            live["stage_progress"] = max(live.get("stage_progress", 0), progress)


def validate_recipe(payload):
    if not isinstance(payload, dict):
        raise ValueError("Send a recording object.")
    def text(name, default=""):
        value = payload.get(name, default)
        if not isinstance(value, str):
            raise ValueError(f"{name.replace('_', ' ').capitalize()} must be text.")
        return value.strip()
    title = text("title")
    lyrics, style, abc = text("lyrics"), text("style"), text("abc")
    mode = text("mode", "lyrics")
    if mode not in ("free", "instrumental", "lyrics", "surprise"):
        raise ValueError("Choose free play, instrumental, my lyrics, or surprise song.")
    try:
        if isinstance(payload.get("max_seconds"), bool) or type(payload.get("steps", 8)) not in (int, str):
            raise ValueError()
        if payload.get("seed") not in (None, "") and type(payload["seed"]) not in (int, str):
            raise ValueError()
        seconds = float(payload.get("max_seconds", 30))
        steps = int(payload.get("steps", 8))
        seed = seed_number(payload.get("seed"))
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Duration, quality, and seed must be valid numbers.")
    if not math.isfinite(seconds) or seconds < 1 / TOKEN_RATE:
        raise ValueError(f"Choose a positive duration of at least {1 / TOKEN_RATE:g} seconds.")
    if steps < 1:
        raise ValueError("Generation needs at least one solver step.")
    if not 0 <= seed < 2 ** 63:
        raise ValueError("Seed must be a whole number from 0 through 9223372036854775807.")
    idea = inspire({"seed": str(seed), "mode": mode, "theme": payload.get("theme", "anywhere"),
                    "energy": payload.get("energy"), "texture": payload.get("texture")})
    source = text("lyrics_source", "provided")
    idea_engine = text("idea_engine", "ai")
    if idea_engine not in ("ai", "phrases", "openrouter"):
        raise ValueError("Choose the AI writer, OpenRouter writer, or phrase shuffler.")
    if mode == "surprise" and not lyrics:
        lyrics, source = (idea["lyrics"], "generated") if idea_engine == "phrases" else ("", "pending")
    elif not lyrics:
        source = "none"
    title_auto = not title or payload.get("title_auto") is True
    title = title or ("A new song" if source == "pending" else idea["title"])
    if mode == "instrumental" and not style:
        style = "Instrumental music, no vocals"
    if mode == "surprise" and idea_engine == "phrases" and not style:
        style = idea["style"]
    cot = text("cot", "off")
    if cot not in ("off", "melody", "full"):
        raise ValueError("Choose direct, melody, or melody and chords planning.")
    if abc and cot == "off":
        raise ValueError("Select melody or melody and chords to use an ABC score.")
    score_source = text("score_source")
    if score_source and not re.fullmatch(r"riff-score-v1:[a-f0-9]{64}", score_source):
        raise ValueError("The saved score reference is invalid.")
    if score_source and (abc or cot == "off"):
        raise ValueError("Use the saved score with melody or melody and chords, or edit its notation.")
    render_mode = text("render_mode", "music")
    if render_mode not in ("music", "plan", "performance") or (render_mode == "plan" and cot == "off"):
        raise ValueError("Choose melody or melody and chords to compose a score.")
    solver = text("solver", "midpoint")
    if solver not in ("midpoint", "ab2"):
        raise ValueError("Choose the midpoint or multistep acoustic solver.")
    def number(name, default, lower, upper):
        raw = payload.get(name, default)
        if isinstance(raw, bool):
            raise ValueError(f"{name} must be a number.")
        try: value = float(raw)
        except (ValueError, TypeError): raise ValueError(f"{name} must be a number.")
        if not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError(f"{name} must be between {lower} and {upper}, as required by the runtime.")
        return value
    tokens = payload.get("writer_tokens", 768)
    if type(tokens) is not int or tokens < 1:
        raise ValueError("Writing token budget must be a positive whole number.")
    origin = {}
    for name in ("parent_track_id", "review_id", "performance_source"):
        value = text(name)
        if value:
            if not re.fullmatch(r"[a-f0-9]{32}", value):
                raise ValueError("The previous take or review reference is invalid.")
            origin[name] = value
    if origin.get("performance_source") and render_mode == "plan":
        raise ValueError("Choose a fresh composition to create a score, or music to render the saved performance.")
    holds = {}
    for name in ("hold_words", "hold_sound"):
        value = payload.get(name, False)
        if type(value) is not bool:
            raise ValueError("Choose whether to hold the words or sound.")
        holds[name] = value
    provenance = {name: text(name) for name in ("writer_model", "writer_summary") if payload.get(name)}
    return {"title": title, "title_auto": title_auto, "lyrics": lyrics, "style": style, "abc": abc, "mode": mode, "lyrics_source": source,
            "idea_engine": idea_engine, "brief": text("brief"), "writer_tokens": tokens,
            "cfg_scale": number("cfg_scale", 1., 0., 20.), "temperature": number("temperature", 1., 0., 5.),
            "energy": idea["energy"] if payload.get("energy") is not None else None,
            "texture": idea["texture"] if payload.get("texture") is not None else None,
            "theme": payload.get("theme", "anywhere"),
            "max_seconds": seconds, "steps": steps, "solver": solver, "cot": cot, "seed": str(seed),
            "refinement": model_options.validate(payload.get("refinement", {}), seconds), "render_mode": render_mode,
            "performance_source": "", "score_source": score_source, **origin, **holds, **provenance}


def analyze_audio(path):
    with wave.open(str(path), "rb") as audio:
        frames, channels, width, rate = audio.getnframes(), audio.getnchannels(), audio.getsampwidth(), audio.getframerate()
        if not frames or not channels or not rate:
            raise ValueError("The generated audio is empty.")
        # 160 peaks for a 480px waveform, streamed without decoding the full song.
        bins = min(160, frames)
        peaks = []
        for i in range(bins):
            begin, end = i * frames // bins, (i + 1) * frames // bins
            raw = audio.readframes(end - begin)
            if width == 1:
                peak = max((abs(value - 128) for value in raw), default=0)
            elif width in (2, 4):
                values = array.array("h" if width == 2 else "i", raw)
                if sys.byteorder != "little":
                    values.byteswap()
                peak = max(abs(min(values, default=0)), abs(max(values, default=0)))
            else:
                peak = max((abs(int.from_bytes(raw[j:j + width], "little", signed=True)) for j in range(0, len(raw), width)), default=0)
            peaks.append(round(peak / (2 ** (8 * width - 1)), 5))
    return {"duration": frames / rate, "sample_rate": rate, "channels": channels, "waveform": peaks}


def recipe_from_metrics(metrics):
    command = metrics.get("command", [])
    opts = {}
    for index, value in enumerate(command[:-1]):
        if value in ("--lyrics", "--seed"):
            opts[value[2:]] = command[index + 1]
        elif value == "--request-option" and "=" in command[index + 1]:
            key, val = command[index + 1].split("=", 1)
            opts[key] = val
    instrumental = opts.get("lyrics", "").strip() == "[Instrumental]"
    return {"title": "Across the bay", "lyrics": "" if instrumental else opts.get("lyrics", ""), "style": opts.get("style", ""),
            "mode": "instrumental" if instrumental else "lyrics",
            "seed": str(opts.get("seed", "831001")), "steps": int(opts.get("num_inference_steps", 8)),
            "solver": opts.get("ode_method", "midpoint"),
            "cot": opts.get("cot", "off"), "max_seconds": int(opts.get("semantic_max_tokens", 750)) / TOKEN_RATE,
            "abc": opts.get("abc", "")}


class Store:
    def __init__(self, data_root, outputs):
        self.data_root, self.outputs = Path(data_root).resolve(), Path(outputs).resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.outputs.mkdir(parents=True, exist_ok=True)
        self.path = self.data_root / "library.sqlite3"
        self.artifacts = None
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tracks (
                    id TEXT PRIMARY KEY, file TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
                    created REAL NOT NULL, favorite INTEGER DEFAULT 0, archived INTEGER DEFAULT 0,
                    notes TEXT DEFAULT '', recipe TEXT NOT NULL, audio TEXT NOT NULL, metrics TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL,
                    status TEXT NOT NULL, recipe TEXT NOT NULL, error TEXT DEFAULT '',
                    started REAL, finished REAL, track_id TEXT, queue_position INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS presets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, style TEXT NOT NULL, color TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS library_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created);
            """)
            db.execute("BEGIN IMMEDIATE")
            if "queue_position" not in {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN queue_position INTEGER NOT NULL DEFAULT 0")
                rows = db.execute("SELECT id FROM jobs ORDER BY created,id").fetchall()
                db.executemany("UPDATE jobs SET queue_position=? WHERE id=?",
                               ((position, row["id"]) for position, row in enumerate(rows, 1)))
            if not db.execute("SELECT 1 FROM library_meta WHERE key='sounds_seeded'").fetchone():
                db.executemany("INSERT OR IGNORE INTO presets VALUES (?,?,?,?)", PRESETS)
                db.execute("INSERT INTO library_meta VALUES('sounds_seeded','1')")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def track(self, track_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
        if row is None:
            raise KeyError("Recording not found.")
        track = dict(row)
        for name in ("recipe", "audio", "metrics"):
            track[name] = json.loads(track[name])
        return track

    def audio_path(self, track_id):
        path = (self.outputs / self.track(track_id)["file"]).resolve()
        try:
            path.relative_to(self.outputs)
        except ValueError:
            raise ValueError("Recording path is outside the library.")
        if not path.is_file():
            raise KeyError("The audio file is missing from the library folder.")
        return path

    def job(self, job_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("Generation not found.")
        job = dict(row)
        job["recipe"] = json.loads(job["recipe"])
        return job

    def library_path(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.outputs):
            raise ValueError("Performance data is outside the library.")
        return path

    def performance_record(self, source_id):
        try:
            record = self.track(source_id)
            path = self.outputs / record["file"]
        except KeyError:
            record = self.job(source_id)
            if record["status"] not in ("performed", "done", "cancelled", "interrupted", "failed"):
                raise ValueError("This performance is still being created.")
            path = self.outputs / "riff" / (record["id"] + ".wav")
        return record, self.library_path(path.with_suffix(".codes.i32"))

    def performance_path(self, source_id):
        record, path = self.performance_record(source_id)
        saved = record["recipe"].get("performance") or {}
        if not saved or not path.is_file():
            raise ValueError("This take has no saved performance. Generate a new performance to capture one.")
        frames = saved.get("frames")
        if type(frames) is not int or frames < 1 or path.stat().st_size != frames * 4 or hashlib.sha256(path.read_bytes()).hexdigest() != saved.get("sha256"):
            raise ValueError("The saved performance has changed. Choose another take.")
        return path

    def capture_artifacts(self, job_id, recipe, cancelled=None):
        """Capture completed native stages, including after an interrupted render."""
        recipe = dict(recipe)
        output = self.outputs / "riff" / (job_id + ".wav")
        errors = dict(recipe.get("artifact_errors") or {})
        try:
            plan = self.artifacts.capture(job_id, recipe, cancelled=cancelled) if self.artifacts else None
            if plan is None:
                plan_path = self.library_path(output.with_suffix(".plan.json"))
                self.library_path(plan_path.with_suffix(".abc"))
                plan = read_plan(plan_path, Path(engine.platform_runtime.settings()["model_root"]) / "sidecars/yue2-qwen.tiktoken")
            if plan is not None:
                recipe["symbolic_plan"] = plan
            errors.pop("score", None)
        except (ValueError, KeyError, OSError, TypeError, SchedulerUnavailable) as exc:
            recipe.pop("symbolic_plan", None)
            errors["score"] = str(exc)
        try:
            recipe = self._capture_performance(job_id, recipe)
            errors.pop("performance", None)
        except (ValueError, KeyError, OSError, TypeError) as exc:
            recipe.pop("performance", None)
            errors["performance"] = str(exc)
        if errors:
            recipe["artifact_errors"] = errors
        else:
            recipe.pop("artifact_errors", None)
        return recipe

    def _capture_performance(self, job_id, recipe):
        output = self.outputs / "riff" / (job_id + ".wav")
        codes = self.library_path(output.with_suffix(".codes.i32"))
        metadata = self.library_path(codes.with_suffix(codes.suffix + ".json"))
        if codes.is_file() and metadata.is_file():
            saved = json.loads(metadata.read_text())
            if type(saved.get("frames")) is not int or saved["frames"] < 1 or codes.stat().st_size != saved["frames"] * 4:
                raise ValueError("The engine returned incomplete performance data.")
            performance = {"frames": saved["frames"], "truncated": bool(saved.get("truncated")),
                           "sha256": hashlib.sha256(codes.read_bytes()).hexdigest()}
            if recipe.get("performance") and recipe["performance"]["sha256"] != performance["sha256"]:
                raise ValueError("The saved performance has changed.")
            if recipe.get("performance_source"):
                self.performance_path(recipe["performance_source"])
                source, _ = self.performance_record(recipe["performance_source"])
                performance["truncated"] = bool(source["recipe"]["performance"].get("truncated"))
            recipe["performance"] = performance
        return recipe

    def recover_jobs(self, cancelled=None):
        with self.db() as db:
            interrupted = list(db.execute("SELECT id,recipe FROM jobs WHERE status IN ('running','cancelling')"))
        for row in interrupted:
            recipe = json.loads(row["recipe"])
            try:
                recipe = self.capture_artifacts(row["id"], recipe, cancelled=cancelled)
                message = "Rendering was interrupted. Your performance is ready to finish." if recipe.get("performance") else "This take was interrupted. Your draft is saved."
            except (ValueError, KeyError, OSError, TypeError):
                # Partial or invalid files are preserved, never offered as a
                # completed performance or allowed to stop studio startup.
                recipe.pop("performance", None)
                message = "This take was interrupted. Your draft is saved."
            with self.db() as db:
                db.execute("UPDATE jobs SET status='interrupted',recipe=?,error=?,finished=? WHERE id=?",
                           (json.dumps(recipe), message, time.time(), row["id"]))

    def score_recipe(self, recipe, cancelled=None):
        if self.artifacts:
            return self.artifacts.recipe(recipe, cancelled=cancelled)
        if recipe.get("score_source"):
            raise ValueError("The saved-score service is not available. Reopen Riff to use this score.")
        return recipe

    def available_scores(self, recipe, cancelled=None):
        return self.artifacts.available(recipe, cancelled=cancelled) if self.artifacts else []

    def prepare_performance(self, recipe, cancelled=None):
        source_id = recipe.get("performance_source")
        if not source_id:
            return self.score_recipe(recipe, cancelled=cancelled)
        self.performance_path(source_id)
        source = self.performance_record(source_id)[0]["recipe"]
        recipe = dict(recipe)
        # A generated score must accompany reused codes. The semantic stage is
        # skipped, so it cannot reconstruct an omitted composition this time.
        if recipe["cot"] != "off" and not recipe["abc"] and not recipe.get("score_source"):
            saved_score = (source.get("symbolic_plan") or {}).get("artifact_id") or source.get("score_source")
            if saved_score:
                recipe["score_source"] = saved_score
            else:
                recipe["abc"] = source.get("abc") or (source.get("symbolic_plan") or {}).get("abc", "")
                if not recipe["abc"]:
                    raise ValueError("The saved performance needs its score. Supply a score or choose Direct generation.")
        recipe["max_seconds"] = source["performance"]["frames"] / TOKEN_RATE
        return self.score_recipe(validate_recipe(recipe), cancelled=cancelled)

    def add_track(self, file, recipe, metrics, track_id=None):
        path = Path(file).resolve()
        relative = str(path.relative_to(self.outputs))
        audio = analyze_audio(path)
        track_id = track_id or uuid.uuid4().hex
        with self.db() as db:
            db.execute("INSERT INTO tracks (id,file,title,created,recipe,audio,metrics) VALUES (?,?,?,?,?,?,?)",
                       (track_id, relative, recipe["title"], time.time(), json.dumps(recipe), json.dumps(audio), json.dumps(metrics)))
        return track_id

    def import_existing(self):
        with self.db() as db:
            known = {row[0] for row in db.execute("SELECT file FROM tracks")}
        # Files generated by the studio are inserted by the worker; import only
        # top-level CLI outputs so interrupted jobs cannot become fake successes.
        for path in sorted(self.outputs.glob("*.wav")):
            if path.name in known or not path.is_file() or not path.resolve().is_relative_to(self.outputs):
                continue
            metrics_file = path.with_suffix(".metrics.json")
            try:
                metrics = json.loads(metrics_file.read_text()) if metrics_file.exists() else {}
                if metrics.get("exit_code", 0) != 0:
                    continue
                recipe = recipe_from_metrics(metrics)
                if path.name != "first-preview.wav":
                    recipe["title"] = path.stem.replace("-", " ").replace("_", " ").capitalize()
                self.add_track(path, recipe, metrics)
            except (ValueError, OSError, wave.Error):
                continue

    def update_track(self, track_id, changes):
        self.track(track_id)
        allowed = {"title", "notes", "favorite", "archived"}
        if not isinstance(changes, dict) or not changes or not set(changes) <= allowed:
            raise ValueError("Only title, notes, favorite, and archive state can be changed.")
        values = {}
        for name, value in changes.items():
            if name in ("favorite", "archived"):
                if not isinstance(value, bool):
                    raise ValueError("Favorite and archive state must be true or false.")
            elif not isinstance(value, str):
                raise ValueError("Title and notes must be text.")
            elif name == "title" and not value.strip():
                raise ValueError("Give the recording a title.")
            values[name] = value.strip() if isinstance(value, str) else int(value)
        with self.db() as db:
            assignments = ",".join(name + "=?" for name in values)
            db.execute(f"UPDATE tracks SET {assignments} WHERE id=?", (*values.values(), track_id))
        return self.track(track_id)

    def add_preset(self, payload):
        name, style = payload.get("name"), payload.get("style")
        if not isinstance(name, str) or not name.strip() or not isinstance(style, str) or not style.strip():
            raise ValueError("Give the sound a name and a description.")
        preset = {"id": uuid.uuid4().hex, "name": name.strip(), "style": style.strip(), "color": "#8298ba"}
        with self.db() as db:
            db.execute("INSERT INTO presets VALUES (?,?,?,?)", tuple(preset.values()))
        return preset

    def preset(self, preset_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM presets WHERE id=?", (preset_id,)).fetchone()
        if not row:
            raise KeyError("Saved sound not found.")
        return dict(row)

    def update_preset(self, preset_id, payload):
        self.preset(preset_id)
        name, style = payload.get("name"), payload.get("style")
        if not isinstance(name, str) or not name.strip() or not isinstance(style, str) or not style.strip():
            raise ValueError("Give the sound a name and a description.")
        with self.db() as db:
            db.execute("UPDATE presets SET name=?,style=? WHERE id=?", (name.strip(), style.strip(), preset_id))
        return self.preset(preset_id)

    def delete_preset(self, preset_id):
        self.preset(preset_id)
        with self.db() as db:
            db.execute("DELETE FROM presets WHERE id=?", (preset_id,))

    def snapshot(self):
        with self.db() as db:
            rows = db.execute("SELECT * FROM tracks ORDER BY created DESC, id DESC").fetchall()
            jobs = [dict(row) for row in db.execute("SELECT id,title,created,status,error,started,finished,track_id,queue_position,recipe FROM jobs ORDER BY created, id")]
            presets = [dict(row) for row in db.execute("SELECT * FROM presets ORDER BY rowid")]
        for job in jobs:
            recipe = json.loads(job.pop("recipe"))
            saved = recipe.get("performance") or {}
            job["score_available"] = bool((recipe.get("symbolic_plan") or {}).get("artifact_id"))
            job["performance_available"] = bool(saved) and job["status"] in ("performed", "done", "cancelled", "interrupted", "failed")
            job["performance_seconds"] = saved.get("frames", 0) / TOKEN_RATE
        tracks = []
        for row in rows:
            recipe, audio = json.loads(row["recipe"]), json.loads(row["audio"])
            tracks.append({"id": row["id"], "title": row["title"], "created": row["created"],
                           "favorite": bool(row["favorite"]), "archived": bool(row["archived"]),
                           "style": recipe["style"], "seed": recipe["seed"], "duration": audio["duration"],
                           "mode": recipe.get("mode", "lyrics"), "score_available": bool((recipe.get("symbolic_plan") or {}).get("artifact_id")), "performance_available": bool(recipe.get("performance"))})
        with self.db() as db:
            plans = [{"id": row["id"], "title": row["title"], "finished": row["finished"], "recipe": json.loads(row["recipe"])}
                     for row in db.execute("SELECT id,title,finished,recipe FROM jobs WHERE status='planned' ORDER BY finished DESC")]
        return {"tracks": tracks, "jobs": jobs, "presets": presets, "plans": plans}


class Generator:
    def __init__(self, store, command_builder=None, admission=None):
        self.store = store
        self.command_builder = command_builder or self.native_command
        self.wake, self.stop = threading.Event(), threading.Event()
        self.lock = threading.RLock()
        self.process = None
        self.writer_process = None
        self.writer_job_id = None
        self.writer_session = None
        self.writer_gate = threading.Lock()
        self.active_job_id = None
        self.job_cancelled = None
        self.quiescing = False
        self.live = None
        self.monitor = engine.platform_runtime.ProcessMemory()
        self.admission = admission if admission is not None else ModelAdmission(store.data_root)
        from score_artifacts import ScoreArtifacts
        store.artifacts = ScoreArtifacts(store, self.admission)
        store.recover_jobs(cancelled=self.stop)
        self.thread = threading.Thread(target=self.work, name="riff-generator", daemon=True)
        self.thread.start()

    def native_command(self, recipe, output):
        if recipe.get("render_mode", "music") == "music":
            engine.require_solver(recipe.get("solver", "midpoint"), engine.platform_runtime.settings()["binary"])
        return engine.build_command(lyrics=recipe["lyrics"], style=recipe["style"], max_seconds=recipe["max_seconds"],
                                    steps=recipe["steps"], cot=recipe["cot"], seed=int(recipe["seed"]),
                                    threads=engine.platform_runtime.settings()["threads"], output=output, abc=recipe["abc"],
                                    mode=recipe.get("mode", "lyrics"), cfg_scale=recipe.get("cfg_scale", 1.),
                                    temperature=recipe.get("temperature", 1.), refinement=recipe.get("refinement", {}),
                                    render_mode=recipe.get("render_mode", "music"),
                                    solver=recipe.get("solver", "midpoint"),
                                    semantic_only=recipe.get("render_mode") == "performance",
                                    performance_file=self.store.performance_path(recipe["performance_source"]) if recipe.get("performance_source") else None,
                                    score_file=self.store.artifacts.input_path(Path(output).stem) if recipe.get("score_source") else None)

    @staticmethod
    def writer_ready():
        settings = engine.platform_runtime.writer_settings()
        return settings["python"].is_file() and (settings["model"] / "model.safetensors").is_file()

    def inspiration(self, payload):
        with self.lock:
            self._accepting()
        if payload.get("idea_engine", "ai") == "phrases":
            return inspire(payload)
        try:
            with self._writer_slot(nonblocking=True) as session:
                return self.write_idea(payload, session=session)
        except AdmissionCancelled:
            return {"cancelled": True}

    def _accepting(self):
        if self.stop.is_set():
            raise AdmissionCancelled()
        if self.quiescing:
            raise ValueError("Riff is updating. Your draft is kept.")

    def has_active_work(self):
        with self.lock:
            if self.active_job_id or self.writer_gate.locked():
                return True
            with self.store.db() as db:
                return bool(db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','cancelling') LIMIT 1").fetchone())

    def quiesce(self):
        """Atomically close admission for activation, without interrupting work."""
        with self.lock:
            if self.has_active_work():
                return False
            self.quiescing = True
            return True

    def resume(self):
        with self.lock:
            self.quiescing = False
        self.wake.set()

    @contextmanager
    def _writer_slot(self, job_id=None, started=None, nonblocking=False):
        cancelled = self.job_cancelled if job_id else threading.Event()
        while True:
            with self.lock:
                self._accepting()
                if cancelled.is_set():
                    raise AdmissionCancelled()
                if self.writer_gate.acquire(blocking=False):
                    session = {"id": f"writer:{uuid.uuid4().hex}", "job_id": job_id,
                               "cancelled": cancelled, "started": started or time.monotonic(),
                               "stage": "Preparing the writer", "footprint": 0, "peak_footprint": 0}
                    self.writer_session = session
                    self.writer_job_id = job_id
                    break
                if nonblocking:
                    raise ValueError("A writing request is already running. Finish or stop it before starting another.")
                if self.live and self.live["id"] == job_id:
                    self.live.update(stage="Waiting for the writer", elapsed=time.monotonic() - started)
            cancelled.wait(.2)
        try:
            yield session
        finally:
            with self.lock:
                if self.writer_session is session:
                    self.writer_session = None
                    self.writer_job_id = None
                self.writer_gate.release()

    @staticmethod
    def writer_command(output):
        return [str(engine.platform_runtime.writer_settings()["python"]), "-B", str(ROOT / "writer.py"), "--output", str(output)]

    def cancel_writing(self):
        with self.lock:
            session = self.writer_session
            if not session or session["job_id"]:
                raise ValueError("There is no standalone writing session to stop.")
            session["cancelled"].set()
            session["stage"] = "Stopping the writer"
            if self.writer_process and self.writer_process.poll() is None:
                self.writer_process.terminate()
        return {"status": "stopping"}

    def writing_status(self):
        with self.lock:
            if not self.writer_session:
                return None
            session = self.writer_session
            return {key: session[key] for key in ("id", "job_id", "stage", "footprint", "peak_footprint")} | {
                "elapsed": time.monotonic() - session["started"]}

    def writing_settings(self, payload, cancelled=None):
        # Validate the settings without imposing a particular story, genre, or form.
        seed = seed_number(payload.get("seed"))
        checked = self.store.score_recipe(validate_recipe({**payload, "seed": str(seed)}), cancelled=cancelled)
        # Do not turn an unspecified theme or compass position into a constraint.
        settings = {key: value for key, value in checked.items() if value is not None}
        scope = payload.get("write_scope", "all")
        if scope not in ("all", "words", "sound"):
            raise ValueError("Choose a new direction, words, or sound.")
        settings["write_scope"] = scope
        from review_recipe import generation_context, symbolic_context
        reference_id = checked.get("performance_source") or checked.get("parent_track_id")
        if reference_id:
            try:
                reference = self.store.track(reference_id)
            except KeyError:
                reference = self.store.job(reference_id)
            source = reference["recipe"]
            settings["reference"] = {"id": reference_id, "inputs": generation_context(source),
                                     "symbolic": symbolic_context(source), "notes": reference.get("notes", "")}
            if source.get("performance"):
                try:
                    self.store.performance_path(reference_id)
                except (ValueError, OSError):
                    if checked.get("performance_source"):
                        raise
                    # A fresh variation can still use the recording's score
                    # and notes when its optional native codes are unavailable.
                    settings["reference"]["performance_available"] = False
                else:
                    settings["performance_track_id"] = reference_id
                    settings["reference"]["performance_available"] = True
                    settings["reference"]["performance"] = source["performance"]
            with self.store.db() as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reviews'").fetchone():
                    # A chosen review is precise context; otherwise use the most
                    # recent completed listening notes for this source recording.
                    review_id = checked.get("review_id")
                    clause = "AND id=?" if review_id else ""
                    params = (reference_id, review_id) if review_id else (reference_id,)
                    review = db.execute("SELECT id,model,focus,notes,summary,generation FROM reviews "
                        "WHERE track_id=? AND status='done' " + clause + " ORDER BY created DESC LIMIT 1", params).fetchone()
                    if review:
                        settings["reference"]["review"] = {key: review[key] for key in ("id", "model", "focus", "notes", "summary")}
                        settings["reference"]["review"]["generation"] = generation_context(json.loads(review["generation"]))
        offered = {value["id"]: value for value in self.store.available_scores(checked, cancelled=cancelled)}
        if reference_id:
            offered.update({value["id"]: value for value in self.store.available_scores(source, cancelled=cancelled)})
        settings["available_scores"] = list(offered.values())
        return settings

    def write_idea(self, payload, job_id=None, started=None, session=None):
        cloud = payload.get("idea_engine") == "openrouter"
        if not cloud and not self.writer_ready():
            raise ValueError("The local AI writer is not installed. Choose another writer in Ideas from.")
        settings = self.writing_settings(payload, cancelled=session["cancelled"])
        settings["queued_generation"] = bool(job_id)
        if payload.get("task") == "score":
            if settings.get("score_source"):
                score = next((value for value in settings["available_scores"] if value["id"] == settings["score_source"]), None)
                if score is None or score.get("display_error"):
                    raise ValueError("This saved score's notation is unavailable for editing.")
                settings["abc"] = score["abc"]
            if not cloud or not settings["abc"].strip() or not settings["brief"].strip():
                raise ValueError("Open a score and describe the change you want.")
            settings["task"] = "score"
        if cloud:
            from keychain import Keychain
            with self.store.db() as db:
                saved = db.execute("SELECT value FROM library_meta WHERE key='review_settings'").fetchone()
            settings["model"] = json.loads(saved[0])["model"] if saved else "google/gemini-3.8-flash"
            settings["api_key"] = Keychain().get()
        started = session["started"]
        cancelled = session["cancelled"]
        identity = session["id"]
        proc, reserved = None, False

        def waiting(reason):
            with self.lock:
                session["stage"] = reason
                if job_id and self.live and self.live["id"] == job_id:
                    self.live.update(stage=reason, elapsed=time.monotonic() - started)

        try:
            if not cloud:
                session["reservation"] = self.admission.reserve(identity, settings, "writer", cancelled,
                                                                 on_wait=waiting, writer_settings=settings)
                reserved = True
            with tempfile.TemporaryDirectory(prefix="writing-", dir=self.store.data_root) as temporary:
                output, log_path = Path(temporary) / "idea.json", Path(temporary) / "writer.log"
                with log_path.open("w") as log, self.lock:
                    if self.stop.is_set() or cancelled.is_set():
                        raise AdmissionCancelled()
                    session["stage"] = "Writing a new song idea"
                    if job_id and self.live and self.live["id"] == job_id:
                        self.live.update(stage=session["stage"], stage_index=0)
                    proc = subprocess.Popen(
                        ([sys.executable, "-B", str(ROOT / "writer.py"), "--output", str(output)] if cloud else self.writer_command(output)),
                        cwd=ROOT, stdin=subprocess.PIPE, stdout=log, stderr=log)
                    self.writer_process = proc
                    if reserved:
                        self.admission.attach(identity, proc)
                proc.stdin.write(json.dumps(settings).encode())
                proc.stdin.close()
                while proc.poll() is None:
                    footprint, memory_peak = self.monitor.read(proc.pid)
                    with self.lock:
                        session.update(footprint=footprint, peak_footprint=max(session["peak_footprint"], memory_peak))
                        if job_id and self.live and self.live["id"] == job_id:
                            self.live.update(elapsed=time.monotonic() - started, footprint=footprint,
                                             peak_footprint=max(self.live["peak_footprint"], memory_peak))
                        if (self.stop.is_set() or cancelled.is_set()) and proc.poll() is None:
                            proc.terminate()
                    time.sleep(.2)
                proc.wait()
                if cancelled.is_set() or self.stop.is_set():
                    raise AdmissionCancelled()
                if proc.returncode or not output.is_file():
                    message = log_path.read_text().strip().splitlines()
                    raise ValueError(message[-1] if message else "The local writer stopped. Try another idea.")
                result = json.loads(output.read_text())
                if payload.get("task") != "score":
                    from writer import written_generation
                    generation = written_generation(result, settings, partial=not cloud)
                    generation = self.store.prepare_performance(generation)
                    generation.update(writer_model=result.get("writer_model", "local"),
                                      writer_summary=result.get("summary", result.get("writer_summary", "")))
                    result = {**result, **generation, "generation": generation}
                if cancelled.is_set() or self.stop.is_set():
                    raise AdmissionCancelled()
                return result
        finally:
            if proc:
                if not proc.stdin.closed:
                    try: proc.stdin.close()
                    except OSError: pass
                if proc.poll() is None:
                    proc.terminate()
                proc.wait()
                with self.lock:
                    if self.writer_process is proc:
                        self.writer_process = None
            if reserved:
                self.admission.release(identity)

    def submit(self, payload):
        recipe = self.store.prepare_performance(validate_recipe(payload))
        job_id = uuid.uuid4().hex
        with self.lock:
            self._accepting()
            with self.store.db() as db:
                db.execute("BEGIN IMMEDIATE")
                position = db.execute("SELECT COALESCE(MAX(queue_position),0)+1 FROM jobs").fetchone()[0]
                db.execute("INSERT INTO jobs (id,title,created,status,recipe,queue_position) VALUES (?,?,?,'queued',?,?)",
                           (job_id, recipe["title"], time.time(), json.dumps(recipe), position))
        self.wake.set()
        return {"id": job_id, "recipe": recipe, "status": "queued"}

    def move_queued(self, job_id, direction):
        if direction not in ("up", "down", "first"):
            raise ValueError("Choose an earlier or later place in the queue.")
        with self.store.db() as db:
            # Claiming and reordering share the same database write lock. A
            # take that started since the last browser refresh stays active.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Take not found.")
            if row["status"] != "queued":
                raise ValueError("Only waiting takes can move in the queue.")
            ids = [row[0] for row in db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY queue_position,created,id")]
            current = ids.index(job_id)
            target = 0 if direction == "first" else max(0, current - 1) if direction == "up" else min(len(ids) - 1, current + 1)
            ids.insert(target, ids.pop(current))
            db.executemany("UPDATE jobs SET queue_position=? WHERE id=? AND status='queued'",
                           ((position, item) for position, item in enumerate(ids, 1)))
        self.wake.set()
        return {"status": "queued", "position": target + 1, "order": ids}

    def cancel(self, job_id):
        with self.lock:
            with self.store.db() as db:
                row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
                if row is None:
                    raise KeyError("Take not found.")
                if row["status"] == "queued":
                    db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE id=?", (time.time(), job_id))
                    status = "cancelled"
                elif row["status"] in ("running", "cancelling"):
                    if self.active_job_id == job_id and self.process and self.process.poll() is not None:
                        raise ValueError("This take has finished and is being saved.")
                    db.execute("UPDATE jobs SET status='cancelling' WHERE id=?", (job_id,))
                    status = "cancelling"
                    if self.active_job_id == job_id and self.job_cancelled:
                        self.job_cancelled.set()
                    if self.active_job_id == job_id and self.process and self.process.poll() is None:
                        self.process.terminate()
                    if self.writer_job_id == job_id and self.writer_process and self.writer_process.poll() is None:
                        self.writer_process.terminate()
                else:
                    raise ValueError("This take has already finished.")
        self.wake.set()
        return status

    def status(self):
        with self.lock:
            return dict(self.live) if self.live else None

    def work(self):
        while not self.stop.is_set():
            row = None
            try:
                with self.lock:
                    if not self.quiescing and not self.stop.is_set():
                        with self.store.db() as db:
                            db.execute("BEGIN IMMEDIATE")
                            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY queue_position,created,id LIMIT 1").fetchone()
                            if row:
                                db.execute("UPDATE jobs SET status='running',started=? WHERE id=?", (time.time(), row["id"]))
                                self.active_job_id = row["id"]
                                self.job_cancelled = threading.Event()
                                self.live = {"id": row["id"], "title": row["title"], "stage": "Preparing the studio",
                                             "stage_index": 0, "elapsed": 0, "footprint": 0, "peak_footprint": 0}
            except sqlite3.OperationalError:
                # Keep the worker alive while storage is temporarily unavailable.
                # Shutdown interrupts the same polling interval used by rendering.
                self.stop.wait(.5)
                continue
            if row:
                self.execute(dict(row))
                continue
            self.wake.wait()
            self.wake.clear()

    def execute(self, job):
        recipe, job_id = json.loads(job["recipe"]), job["id"]
        directory = self.store.outputs / "riff"
        directory.mkdir(exist_ok=True)
        output = directory / f"{job_id}.wav"
        log_path = self.store.data_root / f"{job_id}.log"
        started, peak = time.monotonic(), 0
        returncode, failure, track_id, planned, performed = None, "", None, False, False
        command = []
        proc, reserved, reservation = None, False, None
        cancelled = self.job_cancelled
        identity = f"native:{job_id}"

        def waiting(reason):
            with self.lock:
                if self.live and self.live["id"] == job_id:
                    self.live.update(stage=reason, elapsed=time.monotonic() - started)

        try:
            if recipe.get("lyrics_source") == "pending":
                with self.store.db() as db:
                    if db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "cancelling":
                        return
                with self._writer_slot(job_id, started) as session:
                    idea = self.write_idea(recipe, job_id, started, session=session)
                peak = self.live.get("peak_footprint", 0) if self.live else 0
                recipe = validate_recipe({**recipe, **idea["generation"],
                                          "title": idea["title"] if recipe.get("title_auto") else recipe["title"],
                                          "lyrics_source": "ai"})
                recipe["writer_model"] = idea.get("writer_model", "local")
                with self.store.db() as db:
                    db.execute("UPDATE jobs SET title=?,recipe=? WHERE id=?", (recipe["title"], json.dumps(recipe), job_id))
            recipe = self.store.prepare_performance(recipe, cancelled=cancelled)
            reservation = self.admission.reserve(identity, recipe, "native", cancelled, on_wait=waiting)
            reserved = True
            recipe = self.store.artifacts.launch(job_id, recipe, cancelled=cancelled)
            with self.store.db() as db:
                db.execute("UPDATE jobs SET recipe=? WHERE id=?", (json.dumps(recipe), job_id))
            command = self.command_builder(recipe, output)
            with log_path.open("w") as log, self.lock:
                if cancelled.is_set() or self.stop.is_set():
                    return
                self.live = {"id": job_id, "title": recipe["title"], "stage": "Preparing the studio", "stage_index": 0,
                             "elapsed": 0, "footprint": 0, "peak_footprint": 0}
                proc = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                self.process = proc
                self.admission.attach(identity, proc)
            with log_path.open() as log:
                pending = ""
                while proc.poll() is None:
                    pending += log.read()
                    lines, separator, pending = pending.rpartition("\n")
                    if not separator:
                        lines = ""
                    footprint, memory_peak = self.monitor.read(proc.pid)
                    peak = max(peak, memory_peak)
                    with self.lock:
                        observe_generation(self.live, lines)
                        self.live.update(elapsed=time.monotonic() - started,
                                         footprint=footprint, peak_footprint=peak)
                        if (self.stop.is_set() or cancelled.is_set()) and proc.poll() is None:
                            proc.terminate()
                    time.sleep(0.5)
            returncode = proc.wait()
            metrics = {"wall_seconds": time.monotonic() - started, "exit_code": returncode,
                       "command": command, "peak_process_memory_bytes": peak, "memory_note": self.monitor.note,
                       "memory_reservation": reservation}
            if self.monitor.libproc:
                metrics["macos_lifetime_peak_footprint_bytes_observed"] = peak
            output.with_suffix(".metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
            # Keep completed stages after user cancellation; application shutdown
            # can interrupt capture and recover it from the launch receipt later.
            recipe = self.store.capture_artifacts(job_id, recipe, cancelled=self.stop)
            plan = recipe.get("symbolic_plan")
            with self.store.db() as db:
                db.execute("UPDATE jobs SET recipe=? WHERE id=?", (json.dumps(recipe), job_id))
            with self.store.db() as db:
                cancelling = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "cancelling"
            if not cancelling and not self.stop.is_set():
                if returncode:
                    failure = "The music engine stopped before finishing. Your draft is saved; check the generation log for details."
                elif recipe.get("render_mode") == "plan":
                    planned = isinstance(plan, dict)
                    if not planned:
                        failure = (recipe.get("artifact_errors") or {}).get("score") or "No score was returned. Update the music engine in Studio settings and try again."
                elif recipe.get("render_mode") == "performance":
                    performed = bool(recipe.get("performance"))
                    if not performed:
                        failure = "No performance was returned. Check the generation log for details."
                elif not output.is_file():
                    failure = "The engine finished without an audio file. Try a new seed."
                else:
                    track_id = self.store.add_track(output, recipe, metrics, job_id)
        except AdmissionCancelled:
            pass
        except Exception as exc:
            failure = f"The take could not finish: {exc}"
        finally:
            # Reap only this operation's child. The standalone writer may be
            # running independently and owns its own cancellation and lease.
            if proc:
                if proc.poll() is None:
                    proc.terminate()
                proc.wait()
            with self.lock:
                if self.process is proc:
                    self.process = None
            if reserved:
                self.admission.release(identity)
            while True:
                try:
                    with self.store.db() as db:
                        row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
                        status = "done" if track_id else "performed" if performed else "planned" if planned else "interrupted" if self.stop.is_set() else (
                            "cancelled" if row[0] == "cancelling" else "failed" if failure or not track_id else "done")
                        if status == "cancelled":
                            failure = ""
                        db.execute("UPDATE jobs SET status=?,error=?,finished=?,track_id=? WHERE id=?",
                                   (status, failure, time.time(), track_id, job_id))
                    break
                except sqlite3.OperationalError:
                    with self.lock:
                        if self.live:
                            self.live.update(stage="Waiting for storage", stage_progress=None, footprint=0)
                    if self.stop.wait(.5):
                        break
            with self.lock:
                if self.active_job_id == job_id:
                    self.live = None
                    self.active_job_id = None
                    self.job_cancelled = None

    def close(self):
        self.stop.set()
        self.wake.set()
        with self.lock:
            writer = self.writer_process
            if self.job_cancelled:
                self.job_cancelled.set()
            if self.writer_session:
                self.writer_session["cancelled"].set()
            if self.process and self.process.poll() is None:
                self.process.terminate()
            if self.writer_process and self.writer_process.poll() is None:
                self.writer_process.terminate()
        if writer:
            writer.wait()
        self.thread.join()
        # A standalone HTTP writer can still be unwinding a waiting admission.
        # Its slot is released only after its owned child and lease are reaped.
        with self.writer_gate:
            self.admission.close()
