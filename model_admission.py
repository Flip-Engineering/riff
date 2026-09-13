"""Transitional process bridge to Riff's packaged Elixir resource scheduler."""
import ast
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import threading
import time

from paths import ROOT, WORKSPACE, MODELS
import platform_support


class AdmissionCancelled(Exception):
    pass


class SchedulerUnavailable(RuntimeError):
    pass


class SchedulerNotInstalled(RuntimeError):
    pass


class ModelAdmission:
    def __init__(self, data_root, observer=None, command=None):
        self.data_root = Path(data_root)
        self.observer = observer or platform_support.MemoryObserver()
        self.command = command
        self.monitor = platform_support.ProcessMemory()
        self.lock = threading.RLock()
        self.process = None
        self.entries = {}
        self.buffer = b""
        self.log = None
        self.closed = False
        self.serial_fallback = False

    @staticmethod
    def _timeout():
        value = float(os.environ.get("RIFF_SCHEDULER_TIMEOUT", "30"))
        if value <= 0:
            raise ValueError("RIFF_SCHEDULER_TIMEOUT must be positive.")
        return value

    def _dispose(self):
        if not self.process:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=self._timeout())
            except subprocess.TimeoutExpired:
                # This is our small CPU-only policy process, never a GPU/model
                # process or another application's service.
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            if stream and not stream.closed:
                stream.close()
        self.process = None

    def _command(self):
        if self.command:
            return list(self.command), ROOT
        configured = os.environ.get("RIFF_RUNTIME_CONTROL")
        if configured and not Path(configured).is_file():
            raise RuntimeError("The Riff runtime is missing its resource scheduler. Repair the app installation.")
        candidates = ([Path(configured)] if configured else []) + [ROOT / ".control/bin/riff_installer", WORKSPACE / ".control/bin/riff_installer"]
        for candidate in candidates:
            if candidate.is_file():
                return [str(candidate), "eval", "Riff.Runtime.SchedulerPort.main()"], ROOT
        # This fallback is for source checkouts. Human installs carry ERTS and
        # the scheduler executable in their managed runtime payload.
        mix = shutil.which("mix")
        project = ROOT / "installer"
        beams = [project / "_build/dev/lib/riff_installer/ebin/Elixir.Riff.Runtime.SchedulerPort.beam",
                 project / "_build/dev/lib/jason/ebin/Elixir.Jason.beam"]
        if mix and (project / "mix.exs").is_file() and all(path.is_file() for path in beams):
            return [mix, "run", "--no-compile", "--no-deps-check", "--no-start", "-e", "Riff.Runtime.SchedulerPort.main()"], project
        raise SchedulerNotInstalled("This source installation uses serial model scheduling.")

    def _ensure(self):
        if self.closed:
            raise AdmissionCancelled()
        if self.process and self.process.poll() is None:
            return
        if any(entry.get("admitted") for entry in self.entries.values()):
            raise SchedulerUnavailable("Waiting for active work before recovering the resource scheduler")
        self._dispose()
        command, directory = self._command()
        self.data_root.mkdir(parents=True, exist_ok=True)
        if self.log:
            self.log.close()
        self.log = (self.data_root / "scheduler.log").open("ab")
        self.process = subprocess.Popen(command, cwd=directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.log, bufsize=0, env={**os.environ, "RELEASE_DISTRIBUTION": "none"})
        self.buffer = b""

    def _exchange(self, payload):
        with self.lock:
            self._ensure()
            try:
                self.process.stdin.write(json.dumps(payload).encode() + b"\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._dispose()
                raise SchedulerUnavailable("Waiting for the resource scheduler to recover") from exc
            # This is a recoverable IPC timeout, not a generation budget.
            deadline = time.monotonic() + self._timeout()
            while True:
                if b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)
                    try:
                        result = json.loads(line)
                    except ValueError:
                        self.log.write(line + b"\n")
                        continue
                    if result.get("state") == "error":
                        raise ValueError(result.get("error", "Invalid model scheduling request"))
                    return result
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._dispose()
                    raise SchedulerUnavailable("Waiting for the resource scheduler to recover")
                if select.select([self.process.stdout], [], [], remaining)[0]:
                    chunk = os.read(self.process.stdout.fileno(), 65536)
                    if not chunk:
                        self._dispose()
                        raise SchedulerUnavailable("Waiting for the resource scheduler to recover")
                    self.buffer += chunk

    def estimate(self, inputs, cancelled=None, on_wait=None):
        while True:
            if cancelled and cancelled.is_set():
                raise AdmissionCancelled()
            try:
                return self._exchange({"op": "estimate", "inputs": inputs})["requirement"]
            except SchedulerUnavailable as exc:
                if cancelled is None:
                    raise
                if on_wait:
                    on_wait(str(exc))
                cancelled.wait(.5)

    def reserve(self, identity, recipe, kind, cancelled, on_wait=None, writer_settings=None):
        if not self.serial_fallback:
            try:
                self._command()
            except SchedulerNotInstalled:
                self.serial_fallback = True
        if self.serial_fallback:
            return self._reserve_serial(identity, cancelled, on_wait)
        requirement = self.estimate(resource_inputs(recipe, kind, self.observer, writer_settings), cancelled, on_wait)
        return self.acquire(identity, requirement, cancelled, on_wait)

    def _reserve_serial(self, identity, cancelled, on_wait):
        # Compatibility for pre-desktop source updates, which did not carry an
        # ERTS runtime. Preserve their existing exclusion until a bundled
        # scheduler is installed; do not pretend to have measured admission.
        requirement = {"policy": "serial_compatibility"}
        with self.lock:
            if identity in self.entries:
                raise ValueError("This model operation already owns a reservation.")
            self.entries[identity] = {"requirement": requirement, "admitted": False, "process": None}
        try:
            while not cancelled.is_set():
                with self.lock:
                    if self.closed:
                        raise AdmissionCancelled()
                    if next(iter(self.entries)) == identity:
                        self.entries[identity]["admitted"] = True
                        return requirement
                if on_wait:
                    on_wait("Waiting for the current model")
                cancelled.wait(.5)
            raise AdmissionCancelled()
        except BaseException:
            self.release(identity)
            raise

    def acquire(self, identity, requirement, cancelled, on_wait=None):
        with self.lock:
            if identity in self.entries:
                raise ValueError("This model operation already owns a reservation.")
            self.entries[identity] = {"requirement": requirement, "admitted": False, "process": None}
        try:
            while not cancelled.is_set():
                snapshot = self.observer.snapshot()
                with self.lock:
                    usage = {}
                    for key, entry in self.entries.items():
                        process = entry.get("process")
                        if process and process.poll() is None:
                            resident, _ = self.monitor.read(process.pid)
                            devices = snapshot.get("device_processes", {}).get(entry["requirement"].get("device"), {})
                            usage[key] = {"host": resident, "device": devices.get(str(process.pid), 0)}
                    try:
                        result = self._exchange({"op": "request", "id": identity, "requirement": requirement,
                                                 "snapshot": snapshot, "usage": usage})
                    except SchedulerUnavailable as exc:
                        result = {"state": "waiting", "reason": str(exc)}
                    if result["state"] == "admitted":
                        self.entries[identity]["admitted"] = True
                        self.entries[identity]["requirement"] = result.get("requirement", requirement)
                        if cancelled.is_set():
                            break
                        return self.entries[identity]["requirement"]
                if on_wait:
                    on_wait(result.get("reason", "Waiting for memory"))
                cancelled.wait(.5)
            raise AdmissionCancelled()
        except BaseException:
            self.release(identity)
            raise

    def attach(self, identity, process):
        with self.lock:
            self.entries[identity]["process"] = process

    def release(self, identity):
        with self.lock:
            entry = self.entries.pop(identity, None)
            if entry and self.process and self.process.poll() is None:
                try:
                    self._exchange({"op": "release", "id": identity})
                except SchedulerUnavailable:
                    # Active work retains its local ownership record. A fresh
                    # policy process starts only after those owners release.
                    pass

    def close(self):
        with self.lock:
            self.closed = True
            if self.process:
                if self.process.stdin:
                    self.process.stdin.close()
                if self.process.poll() is None:
                    try:
                        self.process.wait(timeout=self._timeout())
                    except subprocess.TimeoutExpired:
                        pass
                self._dispose()
            if self.log:
                self.log.close()


def resource_inputs(recipe, kind, observer, writer_settings=None):
    """Collect installed facts; estimation and admission live in Elixir."""
    configured = platform_support.settings()
    writer = kind == "writer"
    model_root = platform_support.writer_settings()["model"] if writer else Path(configured["model_root"])
    config_path = model_root / "config.json" if writer else model_root / "sidecars/yue2-model-config.json"
    config = json.loads(config_path.read_text()) if config_path.is_file() else {}
    model_file = model_root / "model.safetensors" if writer else model_root / configured["model_file"]
    vae_file = model_root / configured["vae_file"]
    vae_config_path = model_root / "sidecars/yue2-vae-config.json"
    vae = json.loads(vae_config_path.read_text()) if vae_config_path.is_file() else {}
    generation_path = model_root / "sidecars/yue2-generation-config.json"
    generation = json.loads(generation_path.read_text()) if not writer and generation_path.is_file() else {}
    # Native request_text and special delimiters, from the pinned YuE2
    # pipeline/types contract; include these in the byte-level token bound.
    instructions = {"off": "Generate music with codec tokens from the given conditions.",
                    "melody": "Generate a melody-only ABC transcription without chord symbols, then generate music with codec tokens from the given conditions.",
                    "full": "Generate a chord-annotated ABC transcription, then generate music with codec tokens from the given conditions."}
    lyrics = recipe.get("lyrics", "") or ("[Instrumental]" if recipe.get("mode") == "instrumental" else "")
    text = instructions[recipe.get("cot", "off")] + "\n[Tags]\n" + recipe.get("style", "") + "\n[Lyrics]\n" + lyrics + "\n" + recipe.get("abc", "") + "    "
    if writer:
        import writer as composer
        text = json.dumps(composer.writing_context(writer_settings or recipe)) + json.dumps(composer.writing_schema(writer_settings or recipe))
        # Bound the writer's fixed prompt literals without importing MLX or
        # loading another tokenizer/model into the studio process.
        syntax = ast.parse(Path(composer.__file__).read_text())
        fixed = next(node for node in syntax.body if isinstance(node, ast.FunctionDef) and node.name == "write")
        text += "".join(node.value for node in ast.walk(fixed) if isinstance(node, ast.Constant) and isinstance(node.value, str))
    observation = observer.snapshot()
    backend = "metal" if writer and platform_support.platform.system() == "Darwin" else "cpu" if writer else configured["backend"]
    return {"kind": kind, "config": config, "model_bytes": model_file.stat().st_size if model_file.is_file() else 0,
            "vae_bytes": vae_file.stat().st_size if not writer and vae_file.is_file() else 0,
            "backend": backend, "device": observation.get("device_identity") or f"cuda:{configured['device']}",
            "text_bytes": len(text.encode()), "seconds": recipe.get("max_seconds", 0),
            "writer_tokens": recipe.get("writer_tokens", 768), "cfg_scale": recipe.get("cfg_scale", 1.0),
            "planning": not writer and recipe.get("cot", "off") != "off" and not recipe.get("abc", "").strip(),
            # 4096 is the pinned native fallback in assets.cpp; installed
            # generation sidecars and explicit artist overrides take priority.
            "planning_tokens": recipe.get("refinement", {}).get("abc_max_tokens", generation.get("abc", {}).get("max_tokens", 4096)),
            "context": generation.get("context", config.get("max_position_embeddings", 24576)),
            "render_mode": recipe.get("render_mode", "music"),
            "token_rate": vae.get("sample_rate", 48000) / vae.get("downsampling_ratio", 1920),
            "sample_stride": vae.get("downsampling_ratio", 1920), "vae_chunk": vae.get("decode_core_frames", 1024),
            "margin": float(os.environ.get("RIFF_MODEL_MEMORY_MARGIN", "0.2"))}
