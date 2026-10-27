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
import uuid

from paths import ROOT, WORKSPACE, MODELS
import platform_support


RESIDENT_PROTOCOL = "riff.resident.v1"


class ResidentUnavailable(ValueError):
    pass


class ResidentBindingUnavailable(ResidentUnavailable):
    pass


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
        self.residents = {}
        self.resident_version = None
        self.on_change = None

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
        self.resident_version = None

    def supports_artifacts(self, kind="score"):
        """Read-only availability check; source-only installs can retain legacy scores."""
        try:
            self._command()
        except SchedulerNotInstalled:
            return False
        if kind != "score":
            try:
                response = self._exchange({"op": "capabilities"})
                return response.get("artifacts", {}).get(kind) == "riff.yue2.acoustic.v1"
            except (ValueError, SchedulerUnavailable):
                return False
        return True

    def supports_residents(self):
        """Negotiate once per policy process; absence means legacy behavior."""
        with self.lock:
            if self.serial_fallback:
                return False
            try:
                self._ensure()
            except SchedulerNotInstalled:
                return False
            if self.resident_version is None:
                try:
                    version = self._exchange({"op": "capabilities"}).get("resident")
                except ValueError:
                    version = None  # Schedulers predating capability discovery.
                self.resident_version = RESIDENT_PROTOCOL if version == RESIDENT_PROTOCOL else False
            return self.resident_version == RESIDENT_PROTOCOL

    def _snapshot(self, fresh=False):
        observe = getattr(self.observer, "fresh_snapshot", self.observer.snapshot) if fresh else self.observer.snapshot
        return observe()

    def _resident_measurement(self, requirement, process, snapshot):
        if process.poll() is not None:
            return None, "The resident engine has stopped"
        host, _ = self.monitor.read(process.pid)
        if not isinstance(host, int) or host <= 0:
            return None, "Resident process memory is unavailable; using legacy warm admission without lease credit"
        device = 0
        if requirement.get("device_peak", 0) > 0:
            processes = snapshot.get("device_processes", {}).get(requirement["device"], {})
            device = processes.get(str(process.pid))
            if not isinstance(device, int) or device < 0:
                return None, "Resident device memory is unavailable; using legacy warm admission without lease credit"
        return {"host": host, "device": device}, ""

    def _resident_message(self, action, identity, **arguments):
        result = self._exchange({"op": "resident", "action": action, "id": identity,
                                 "protocol": RESIDENT_PROTOCOL, **arguments})
        if result.get("protocol") != RESIDENT_PROTOCOL:
            raise ValueError("The scheduler returned an unsupported resident lease protocol")
        expected = {"update": ("resident",), "fit": ("resident_fit",),
                    "release": ("released",), "release_if_idle": ("released", "busy")}
        if result.get("state") not in expected[action]:
            raise ValueError("The scheduler returned an invalid resident lease result")
        return result

    def _sync_residents(self, snapshot):
        errors = {}
        if not self.residents or not self.supports_residents():
            return errors
        for identity, lease in list(self.residents.items()):
            owners = [entry for entry in self.entries.values() if entry.get("resident") == identity]
            bound = any(entry.get("admitted") for entry in owners)
            if not bound:
                usage, reason = self._resident_measurement(lease["requirement"], lease["process"], snapshot)
                if usage is None:
                    errors[identity] = reason
                    if owners:
                        continue
                    self._resident_message("release", identity)
                    self.residents.pop(identity)
                    continue
                lease["usage"] = usage
            self._resident_message("update", identity, requirement=lease["requirement"], usage=lease["usage"])
        return errors

    def _usage(self, snapshot):
        usage = {identity: lease["usage"] for identity, lease in self.residents.items()}
        for key, entry in self.entries.items():
            process = entry.get("process")
            if process and process.poll() is None:
                resident, _ = self.monitor.read(process.pid)
                devices = snapshot.get("device_processes", {}).get(entry["requirement"].get("device"), {})
                usage[key] = {"host": resident, "device": devices.get(str(process.pid), 0)}
        return usage

    def resident_plan(self, identity, recipe, kind, cancelled, on_wait=None, resident=None):
        requirement = self.estimate(resource_inputs(recipe, kind, self.observer), cancelled, on_wait)
        with self.lock:
            snapshot = self._snapshot(fresh=True)
            if not self.supports_residents():
                raise ResidentUnavailable("The scheduler no longer supports resident leases; using legacy admission")
            errors = self._sync_residents(snapshot)
            if resident is not None and (resident in errors or resident not in self.residents):
                raise ResidentUnavailable(errors.get(resident, "Resident measurements changed; the take needs a fresh reservation"))
            arguments = {"requirement": requirement, "snapshot": snapshot, "usage": self._usage(snapshot)}
            if resident is not None:
                arguments["resident"] = resident
            decision = self._resident_message("fit", identity, **arguments)
            if (decision.get("state") != "resident_fit" or decision.get("protocol") != RESIDENT_PROTOCOL
                    or type(decision.get("keep")) is not bool or not isinstance(decision.get("figures"), dict)):
                raise ValueError("The scheduler returned an invalid resident fit decision")
            return requirement, decision

    def retain_resident(self, identity, process, resident=None):
        """Publish an idle engine's measured baseline, never a guessed footprint."""
        with self.lock:
            if not self.supports_residents():
                return None, "The scheduler does not support resident leases"
            requirement = self.entries[identity]["estimate"]
            snapshot = self._snapshot(fresh=True)
            usage, reason = self._resident_measurement(requirement, process, snapshot)
            if usage is None:
                return None, reason
            resident = resident or "resident:" + uuid.uuid4().hex
            self._resident_message("update", resident, requirement=requirement, usage=usage)
            self.residents[resident] = {"requirement": requirement, "usage": usage, "process": process}
            return {"id": resident, "protocol": RESIDENT_PROTOCOL, "usage": usage}, ""

    def release_resident(self, identity):
        if identity is None:
            return
        with self.lock:
            if identity in self.residents and self.process and self.process.poll() is None and self.resident_version:
                try:
                    self._resident_message("release", identity)
                except SchedulerUnavailable:
                    pass
            self.residents.pop(identity, None)

    def release_resident_if_idle(self, identity, retire):
        """Retire under admission's lock; the caller waits for process exit outside it.

        ``retire`` must only mark the engine unavailable for reuse, never wait.
        """
        with self.lock:
            if self.entries:
                return False
            if self.serial_fallback:
                idle = True
            elif self.supports_residents() and identity is not None:
                self._sync_residents(self._snapshot(fresh=True))
                result = self._resident_message("release_if_idle", identity)
                idle = result.get("state") == "released"
            else:
                status = self._exchange({"op": "status"})
                idle = status.get("state") == "ready" and status.get("active") == [] and status.get("waiting") == []
            if not idle:
                return False
            self.residents.pop(identity, None)
            retire()
            return True

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
                        error = result.get("error", "Invalid model scheduling request")
                        if error in ("The resident lease is no longer registered.",
                                     "The resident lease uses a different device."):
                            raise ResidentBindingUnavailable(error)
                        raise ValueError(error)
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

    def artifact(self, action, arguments, cancelled=None, on_wait=None):
        """Run file work in the existing control VM, without holding admission's lock while waiting."""
        if action not in ("capture", "resolve", "prepare") or not isinstance(arguments, dict):
            raise ValueError("Choose a supported saved-score operation.")
        cancelled = cancelled if cancelled is not None else threading.Event()
        identity = uuid.uuid4().hex
        terminal = False
        try:
            if cancelled.is_set() or self.closed:
                raise AdmissionCancelled()
            result = self._exchange({**arguments, "op": "artifact_start", "id": identity, "action": action})
            if result.get("state") != "started" or result.get("id") != identity:
                raise SchedulerUnavailable("The score operation could not start. Its existing files are kept.")
            while True:
                if cancelled.is_set() or self.closed:
                    raise AdmissionCancelled()
                result = self._exchange({"op": "artifact_result", "id": identity})
                if result.get("id") != identity:
                    raise SchedulerUnavailable("The score operation was interrupted. Try it again.")
                state = result.get("state")
                if state in ("complete", "failed"):
                    terminal = True  # The port consumes this operation's terminal result.
                    if state == "failed":
                        raise ValueError(result.get("error", "The saved score could not be prepared."))
                    if not isinstance(result.get("result"), dict):
                        raise SchedulerUnavailable("The score operation returned an invalid result.")
                    return result["result"]
                if state != "working":
                    raise SchedulerUnavailable("The score operation was interrupted. Try it again.")
                if on_wait:
                    on_wait()
                # File IO and fsync happen in the port's owned worker. Other
                # threads can estimate/admit/release model work during this wait.
                cancelled.wait(.05)
        finally:
            if not terminal:
                with self.lock:
                    if not self.closed and self.process and self.process.poll() is None:
                        try:
                            self._exchange({"op": "artifact_cancel", "id": identity})
                        except (SchedulerUnavailable, AdmissionCancelled, ValueError):
                            pass

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

    def acquire(self, identity, requirement, cancelled, on_wait=None, resident=None):
        with self.lock:
            if identity in self.entries:
                raise ValueError("This model operation already owns a reservation.")
            self.entries[identity] = {"requirement": requirement, "estimate": requirement, "admitted": False, "process": None, "resident": resident}
        try:
            while not cancelled.is_set():
                snapshot = self.observer.snapshot()
                with self.lock:
                    try:
                        errors = self._sync_residents(snapshot)
                        if resident is not None and (resident in errors or resident not in self.residents or not self.supports_residents()):
                            raise ResidentUnavailable(errors.get(resident, "Resident lease ended while waiting; a cold reservation is required"))
                        payload = {"op": "request", "id": identity, "requirement": requirement,
                                   "snapshot": snapshot, "usage": self._usage(snapshot)}
                        if resident is not None:
                            payload.update(resident=resident, resident_protocol=RESIDENT_PROTOCOL)
                        result = self._exchange(payload)
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
        if entry and self.on_change:
            self.on_change()

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


def _farm_device(observation, configured):
    """The shared farm device an admission names (#93): the freest
    visible card from the snapshot when one was observed, else the
    configured card's identity, else its indexed fallback."""
    return (observation.get("farm_device") or observation.get("device_identity")
            or f"cuda:{configured['device']}")


def resource_inputs(recipe, kind, observer, writer_settings=None):
    """Collect installed facts; estimation and admission live in Elixir."""
    configured = platform_support.settings()
    if kind == "acoustic_decode":
        model_root = Path(configured["model_root"])
        observation = observer.snapshot()
        return {"kind": kind, "acoustic": recipe["acoustic"],
                "vae_config": json.loads((model_root / "sidecars/yue2-vae-config.json").read_text()),
                "vae_bytes": (model_root / configured["vae_file"]).stat().st_size,
                "decoder": recipe.get("decoder", {}), "backend": configured["backend"],
                "device": _farm_device(observation, configured),
                "margin": float(os.environ.get("RIFF_MODEL_MEMORY_MARGIN", "0.2"))}
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
            "adapter_bytes": 0 if writer else recipe.get("lora_memory_bytes", 0),
            "vae_bytes": vae_file.stat().st_size if not writer and vae_file.is_file() else 0,
            "backend": backend, "device": _farm_device(observation, configured),
            "text_bytes": len(text.encode()), "seconds": recipe.get("max_seconds", 0),
            "writer_tokens": recipe.get("writer_tokens", 768), "cfg_scale": recipe.get("cfg_scale", 1.0),
            "planning": not writer and recipe.get("cot", "off") != "off" and not recipe.get("abc", "").strip() and not recipe.get("score_source"),
            **({"prefix_tokens": len(text.encode()) + recipe["score_input"]["token_count"]}
               if not writer and recipe.get("score_source") and recipe.get("score_input") else {}),
            # 4096 is the pinned native fallback in assets.cpp; installed
            # generation sidecars and explicit artist overrides take priority.
            "planning_tokens": recipe.get("refinement", {}).get("abc_max_tokens", generation.get("abc", {}).get("max_tokens", 4096)),
            "context": generation.get("context", config.get("max_position_embeddings", 24576)),
            "render_mode": recipe.get("render_mode", "music"),
            "token_rate": vae.get("sample_rate", 48000) / vae.get("downsampling_ratio", 1920),
            "sample_stride": vae.get("downsampling_ratio", 1920), "vae_chunk": vae.get("decode_core_frames", 1024),
            "margin": float(os.environ.get("RIFF_MODEL_MEMORY_MARGIN", "0.2"))}
