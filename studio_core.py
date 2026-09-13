"""Riff's local library and single-owner music generation queue."""
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
            "performance_source": "", **origin, **holds, **provenance}


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

    def capture_artifacts(self, job_id, recipe):
        """Capture completed native stages, including after an interrupted render."""
        recipe = dict(recipe)
        output = self.outputs / "riff" / (job_id + ".wav")
        plan_path = self.library_path(output.with_suffix(".plan.json"))
        # read_plan also writes a decoded score beside the native token file.
        self.library_path(plan_path.with_suffix(".abc"))
        plan = read_plan(plan_path, Path(engine.platform_runtime.settings()["model_root"]) / "sidecars/yue2-qwen.tiktoken")
        if plan:
            recipe["symbolic_plan"] = plan
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

    def recover_jobs(self):
        with self.db() as db:
            interrupted = list(db.execute("SELECT id,recipe FROM jobs WHERE status IN ('running','cancelling')"))
        for row in interrupted:
            recipe = json.loads(row["recipe"])
            try:
                recipe = self.capture_artifacts(row["id"], recipe)
                message = "Rendering was interrupted. Your performance is ready to finish." if recipe.get("performance") else "This take was interrupted. Your draft is saved."
            except (ValueError, KeyError, OSError, TypeError):
                # Partial or invalid files are preserved, never offered as a
                # completed performance or allowed to stop studio startup.
                recipe.pop("performance", None)
                message = "This take was interrupted. Your draft is saved."
            with self.db() as db:
                db.execute("UPDATE jobs SET status='interrupted',recipe=?,error=?,finished=? WHERE id=?",
                           (json.dumps(recipe), message, time.time(), row["id"]))

    def prepare_performance(self, recipe):
        source_id = recipe.get("performance_source")
        if not source_id:
            return recipe
        self.performance_path(source_id)
        source = self.performance_record(source_id)[0]["recipe"]
        recipe = dict(recipe)
        # A generated score must accompany reused codes. The semantic stage is
        # skipped, so it cannot reconstruct an omitted composition this time.
        if recipe["cot"] != "off" and not recipe["abc"]:
            recipe["abc"] = source.get("abc") or (source.get("symbolic_plan") or {}).get("abc", "")
            if not recipe["abc"]:
                raise ValueError("The saved performance needs its score. Supply a score or choose Direct generation.")
        recipe["max_seconds"] = source["performance"]["frames"] / TOKEN_RATE
        return validate_recipe(recipe)

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
            job["performance_available"] = bool(saved) and job["status"] in ("performed", "done", "cancelled", "interrupted", "failed")
            job["performance_seconds"] = saved.get("frames", 0) / TOKEN_RATE
        tracks = []
        for row in rows:
            recipe, audio = json.loads(row["recipe"]), json.loads(row["audio"])
            tracks.append({"id": row["id"], "title": row["title"], "created": row["created"],
                           "favorite": bool(row["favorite"]), "archived": bool(row["archived"]),
                           "style": recipe["style"], "seed": recipe["seed"], "duration": audio["duration"],
                           "mode": recipe.get("mode", "lyrics"), "performance_available": bool(recipe.get("performance"))})
        with self.db() as db:
            plans = [{"id": row["id"], "title": row["title"], "finished": row["finished"], "recipe": json.loads(row["recipe"])}
                     for row in db.execute("SELECT id,title,finished,recipe FROM jobs WHERE status='planned' ORDER BY finished DESC")]
        return {"tracks": tracks, "jobs": jobs, "presets": presets, "plans": plans}


class Generator:
    def __init__(self, store, command_builder=None):
        self.store = store
        self.command_builder = command_builder or self.native_command
        self.wake, self.stop = threading.Event(), threading.Event()
        self.lock = threading.RLock()
        self.process = None
        self.writer_process = None
        self.writer_job_id = None
        self.writer_cancelled = False
        self.model_gate = threading.Lock()
        self.writer_gate = threading.Lock()
        self.live = None
        self.monitor = engine.platform_runtime.ProcessMemory()
        store.recover_jobs()
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
                                    performance_file=self.store.performance_path(recipe["performance_source"]) if recipe.get("performance_source") else None)

    @staticmethod
    def writer_ready():
        return (WORKSPACE / ".writer-venv/bin/python").is_file() and (WORKSPACE / "models/lyric-writer/model.safetensors").is_file()

    def inspiration(self, payload):
        if payload.get("idea_engine", "ai") == "phrases":
            return inspire(payload)
        local = payload.get("idea_engine", "ai") != "openrouter"
        if local and not self.model_gate.acquire(blocking=False):
            raise ValueError("The local model is busy. Try the writer after this take, or choose the instant phrase shuffler.")
        try:
            if not self.writer_gate.acquire(blocking=False):
                raise ValueError("A writing request is already running. Finish or stop it before starting another.")
            try:
                return self.write_idea(payload)
            finally:
                self.writer_gate.release()
        finally:
            if local:
                self.model_gate.release()

    @staticmethod
    def writer_command(output):
        return [str(WORKSPACE / ".writer-venv/bin/python"), str(ROOT / "writer.py"), "--output", str(output)]

    def cancel_writing(self):
        with self.lock:
            if self.writer_job_id or not self.writer_process or self.writer_process.poll() is not None:
                raise ValueError("There is no standalone writing session to stop.")
            self.writer_cancelled = True
            self.writer_process.terminate()
        return {"status": "stopping"}

    def writing_settings(self, payload):
        # Validate the settings without imposing a particular story, genre, or form.
        seed = seed_number(payload.get("seed"))
        checked = validate_recipe({**payload, "seed": str(seed)})
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
        return settings

    def write_idea(self, payload, job_id=None, started=None):
        cloud = payload.get("idea_engine") == "openrouter"
        if not cloud and not self.writer_ready():
            raise ValueError("The local AI writer is not installed. Choose another writer in Ideas from.")
        settings = self.writing_settings(payload)
        settings["queued_generation"] = bool(job_id)
        if payload.get("task") == "score":
            if not cloud or not settings["abc"].strip() or not settings["brief"].strip():
                raise ValueError("Open a score and describe the change you want.")
            settings["task"] = "score"
        if cloud:
            from keychain import Keychain
            with self.store.db() as db:
                saved = db.execute("SELECT value FROM library_meta WHERE key='review_settings'").fetchone()
            settings["model"] = json.loads(saved[0])["model"] if saved else "google/gemini-3.8-flash"
            settings["api_key"] = Keychain().get()
        started = started or time.monotonic()
        with tempfile.TemporaryDirectory(prefix="writing-", dir=self.store.data_root) as temporary:
            output, log_path = Path(temporary) / "idea.json", Path(temporary) / "writer.log"
            with log_path.open("w") as log, self.lock:
                if self.stop.is_set():
                    raise ValueError("The studio is closing.")
                if job_id:
                    with self.store.db() as db:
                        if db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "cancelling":
                            raise ValueError("Writing was stopped.")
                    self.live = {"id": job_id, "title": settings["title"], "stage": "Writing a new song idea",
                                 "stage_index": 0, "elapsed": 0, "footprint": 0, "peak_footprint": 0}
                self.writer_cancelled = False
                self.writer_job_id = job_id
                self.writer_process = subprocess.Popen(
                    ([sys.executable, str(ROOT / "writer.py"), "--output", str(output)] if cloud else self.writer_command(output)),
                    cwd=ROOT, stdin=subprocess.PIPE, stdout=log, stderr=log)
                proc = self.writer_process
            try:
                proc.stdin.write(json.dumps(settings).encode())
                proc.stdin.close()
                while proc.poll() is None:
                    footprint, memory_peak = self.monitor.read(proc.pid)
                    with self.lock:
                        if job_id and self.live:
                            self.live.update(elapsed=time.monotonic() - started,
                                             footprint=footprint)
                            self.live["peak_footprint"] = max(self.live["peak_footprint"], memory_peak)
                        if self.stop.is_set() and proc.poll() is None:
                            proc.terminate()
                    time.sleep(.2)
                proc.wait()
                if not job_id and self.writer_cancelled:
                    return {"cancelled": True}
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
                return result
            finally:
                if not proc.stdin.closed:
                    try: proc.stdin.close()
                    except OSError: pass
                with self.lock:
                    if proc.poll() is None:
                        proc.terminate()
                    proc.wait()
                    self.writer_process = None
                    self.writer_job_id = None

    def submit(self, payload):
        recipe = self.store.prepare_performance(validate_recipe(payload))
        job_id = uuid.uuid4().hex
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
                    if self.process and self.process.poll() is not None:
                        raise ValueError("This take has finished and is being saved.")
                    db.execute("UPDATE jobs SET status='cancelling' WHERE id=?", (job_id,))
                    status = "cancelling"
                    if self.live and self.live["id"] == job_id and self.process and self.process.poll() is None:
                        self.process.terminate()
                    if self.live and self.live["id"] == job_id and self.writer_process and self.writer_process.poll() is None:
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
            try:
                with self.store.db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY queue_position,created,id LIMIT 1").fetchone()
                    if row:
                        db.execute("UPDATE jobs SET status='running',started=? WHERE id=?", (time.time(), row["id"]))
            except sqlite3.OperationalError:
                # Keep the worker alive while storage is temporarily unavailable.
                # Shutdown interrupts the same polling interval used by rendering.
                self.stop.wait(.5)
                continue
            if row:
                with self.model_gate:
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
        try:
            if recipe.get("lyrics_source") == "pending":
                with self.store.db() as db:
                    if db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "cancelling":
                        return
                with self.writer_gate:
                    idea = self.write_idea(recipe, job_id, started)
                peak = self.live.get("peak_footprint", 0) if self.live else 0
                recipe = validate_recipe({**recipe, **idea["generation"],
                                          "title": idea["title"] if recipe.get("title_auto") else recipe["title"],
                                          "lyrics_source": "ai"})
                recipe["writer_model"] = idea.get("writer_model", "local")
                with self.store.db() as db:
                    db.execute("UPDATE jobs SET title=?,recipe=? WHERE id=?", (recipe["title"], json.dumps(recipe), job_id))
            recipe = self.store.prepare_performance(recipe)
            command = self.command_builder(recipe, output)
            with log_path.open("w") as log, self.lock:
                with self.store.db() as db:
                    cancelled = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "cancelling"
                if cancelled or self.stop.is_set():
                    return
                self.live = {"id": job_id, "title": recipe["title"], "stage": "Preparing the studio", "stage_index": 0,
                             "elapsed": 0, "footprint": 0, "peak_footprint": 0}
                self.process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            proc = self.process
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
                        if self.stop.is_set() and proc.poll() is None:
                            proc.terminate()
                    time.sleep(0.5)
            returncode = proc.wait()
            metrics = {"wall_seconds": time.monotonic() - started, "exit_code": returncode,
                       "command": command, "peak_process_memory_bytes": peak, "memory_note": self.monitor.note}
            if self.monitor.libproc:
                metrics["macos_lifetime_peak_footprint_bytes_observed"] = peak
            output.with_suffix(".metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
            recipe = self.store.capture_artifacts(job_id, recipe)
            plan = recipe.get("symbolic_plan")
            with self.store.db() as db:
                db.execute("UPDATE jobs SET recipe=? WHERE id=?", (json.dumps(recipe), job_id))
            with self.store.db() as db:
                cancelling = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "cancelling"
            if not cancelling and not self.stop.is_set():
                if returncode:
                    failure = "The music engine stopped before finishing. Your draft is saved; check the generation log for details."
                elif recipe.get("render_mode") == "plan":
                    planned = bool(plan and plan["abc"])
                    if not planned:
                        failure = "No score was returned. Update the music engine in Studio settings and try again."
                elif recipe.get("render_mode") == "performance":
                    performed = bool(recipe.get("performance"))
                    if not performed:
                        failure = "No performance was returned. Check the generation log for details."
                elif not output.is_file():
                    failure = "The engine finished without an audio file. Try a new seed."
                else:
                    track_id = self.store.add_track(output, recipe, metrics, job_id)
        except Exception as exc:
            failure = f"The take could not finish: {exc}"
        finally:
            with self.lock:
                # Always reap the owned native child, including an exception in monitoring.
                if self.process and self.process.poll() is None:
                    self.process.terminate()
                    self.process.wait()
                self.process = None
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
                self.live = None

    def close(self):
        self.stop.set()
        self.wake.set()
        with self.lock:
            writer = self.writer_process
            if self.process and self.process.poll() is None:
                self.process.terminate()
            if self.writer_process and self.writer_process.poll() is None:
                self.writer_process.terminate()
        if writer:
            writer.wait()
        self.thread.join()
