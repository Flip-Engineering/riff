"""One persistent music engine that keeps YuE2 weights on the GPU between takes.

The default path starts a new audiocpp_cli for every take, and every take uploads
the AR, NAR and VAE weights again. Warm mode starts ``audiocpp_cli --jobs -`` once
per engine configuration with ``yue2.keep_resident=true`` and sends each take as
one JSON line on stdin. The engine prints each take's normal log between
``@@riff-job-begin <id>`` and ``@@riff-job-end <id> status=...`` markers. That
output is copied into the take's own log file, so progress and metrics parsing
stay the same as for a per-take process.

Only generation commands use the warm engine. ``job_from_command`` returns None
for any command it does not fully understand (for example acoustic decoding,
which uses a decoder-only session), and the caller runs that take in its own
process as before.

Cancelling a take sends SIGTERM to the engine and waits for it to exit. The next
take starts a new engine. This module never sends SIGKILL.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid

PROTOCOL = "riff.jobs.v1"
READY = "@@riff-engine-ready "
BEGIN = "@@riff-job-begin "
END = "@@riff-job-end "

SESSION_FLAGS = ("--task", "--family", "--model", "--backend", "--device", "--threads")
JOB_FLAGS = {"--lyrics": "lyrics", "--seed": "seed", "--out": "out"}
PROCESS_FLAGS = {"--log", "--metrics"}
SIDECARS = ("sidecars/yue2-model-config.json", "sidecars/yue2-generation-config.json",
            "sidecars/yue2-vae-config.json", "sidecars/yue2-qwen.tiktoken")


def job_from_command(command):
    """Split a per-take generation command into (session, job), or return None.

    ``session`` holds the process-level arguments that must match the running
    engine. ``job`` is the JSON object that audiocpp_cli's job reader accepts
    (build_request_from_json fields plus ``out``). The seed stays a string so
    values above 2**53 keep their exact value.
    """
    if not command:
        return None
    arguments = [str(value) for value in command[1:]]
    session = {"binary": str(command[0]), "session_options": []}
    job = {"options": {}}
    index = 0
    while index < len(arguments):
        flag = arguments[index]
        if flag in PROCESS_FLAGS:
            index += 1
            continue
        if index + 1 >= len(arguments):
            return None
        value = arguments[index + 1]
        index += 2
        if flag in SESSION_FLAGS:
            if flag in session:
                return None
            session[flag] = value
        elif flag == "--session-option":
            session["session_options"].append(value)
        elif flag == "--request-option":
            key, separator, item = value.partition("=")
            if not separator or not key or key in job["options"]:
                return None
            job["options"][key] = item
        elif flag in JOB_FLAGS and JOB_FLAGS[flag] not in job:
            job[JOB_FLAGS[flag]] = value
        else:
            return None
    options = session["session_options"]
    if (session.get("--task") != "gen" or session.get("--family") != "yue2" or "--model" not in session
            or not any(option.startswith("yue2.model_gguf=") for option in options)
            or any(option.startswith("yue2.keep_resident=") for option in options)
            or "acoustic_latents_file" in job["options"]):
        return None
    return session, job


def engine_command(session):
    command = [session["binary"]]
    for flag in SESSION_FLAGS:
        if flag in session:
            command += [flag, session[flag]]
    for option in session["session_options"]:
        command += ["--session-option", option]
    return command + ["--session-option", "yue2.keep_resident=true", "--log", "--metrics", "--jobs", "-"]


def _identity(path):
    try:
        info = Path(path).stat()
    except OSError:
        return None
    return (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def engine_key(session):
    """The engine restarts when its arguments or any file it has loaded change."""
    root = Path(session["--model"])
    files = [Path(session["binary"])] + [root / name for name in SIDECARS]
    for option in session["session_options"]:
        key, _, value = option.partition("=")
        if key in ("yue2.model_gguf", "yue2.vae_gguf"):
            files.append(root / value)
    return tuple(engine_command(session)), tuple(_identity(path) for path in files)


def _stop_grace():
    # An idle engine exits as soon as stdin closes; this only bounds how long an
    # unresponsive engine may take before it receives SIGTERM.
    value = float(os.environ.get("RIFF_WARM_ENGINE_STOP_SECONDS", "30"))
    if value <= 0:
        raise ValueError("RIFF_WARM_ENGINE_STOP_SECONDS must be positive.")
    return value


class WarmJob:
    """A take running in the warm engine, with the Popen methods the studio uses.

    ``pid`` is the engine's process ID, for memory sampling and admission.
    ``terminate`` cancels the take by stopping the engine. There is no ``kill``.
    """
    shared_process = True

    def __init__(self, engine, process, job_id, log):
        self.engine, self.process, self.id, self.log = engine, process, job_id, log
        self.pid = process.pid
        self.returncode = None
        self.message = ""
        self.begun = False
        self.done = threading.Event()
        self.lock = threading.Lock()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise subprocess.TimeoutExpired("audiocpp_cli --jobs", timeout)
        return self.returncode

    def terminate(self):
        if self.returncode is None:
            self.engine.terminate(self.process)

    def write(self, text):
        with self.lock:
            if not self.log.closed:
                self.log.write(text)
                self.log.flush()

    def finish(self, returncode, message=""):
        with self.lock:
            if self.returncode is not None:
                return False
            self.returncode, self.message = returncode, message
            self.log.close()
        self.done.set()
        return True


class WarmEngine:
    def __init__(self, log_path, cwd=None):
        self.log_path = Path(log_path)
        self.cwd = cwd
        self.lock = threading.Lock()
        self.process = None
        self.reader = None
        self.key = None
        self.current = None
        self.ready = False
        self.retiring = False

    def _note(self, text):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as log:
            log.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.rstrip("\n") + "\n")

    def running(self):
        with self.lock:
            return self.process is not None and self.process.poll() is None

    def prepare(self, session):
        """Start or reuse an engine for ``session``; restart it if anything changed.

        Called before the take is published as running, so a restart does not
        hold the studio's lock.
        """
        key = engine_key(session)
        with self.lock:
            if self.current is not None and self.current.returncode is None:
                raise RuntimeError("The warm engine is still running another take.")
            stale = self.process is not None and (self.process.poll() is not None or self.retiring or self.key != key)
        if stale:
            self.stop()
        with self.lock:
            if self.process is None:
                command = engine_command(session)
                self._note("starting: " + " ".join(command))
                process = subprocess.Popen(command, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, bufsize=0)
                self.process, self.key, self.ready, self.retiring = process, key, False, False
                self.reader = threading.Thread(target=self._read, args=(process,), name="riff-warm-engine", daemon=True)
                self.reader.start()
                self._note(f"started pid {process.pid}")

    def submit(self, job, log_path):
        """Send one take to the prepared engine; returns a WarmJob."""
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None or self.retiring:
                raise RuntimeError("The warm engine is not running. Prepare it before submitting a take.")
            if self.current is not None and self.current.returncode is None:
                raise RuntimeError("The warm engine is still running another take.")
            handle = WarmJob(self, process, uuid.uuid4().hex, Path(log_path).open("a"))
            self.current = handle
        try:
            process.stdin.write((json.dumps(dict(job, id=handle.id, metrics=True)) + "\n").encode())
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            # The reader finishes the take with the engine's exit status at EOF.
            self.retiring = True
        return handle

    def terminate(self, process=None):
        """Cancel: SIGTERM the engine (never SIGKILL). The next take restarts it."""
        with self.lock:
            process = process or self.process
            if process is None or process is not self.process:
                return
            self.retiring = True
        if process.poll() is None:
            self._note(f"stopping pid {process.pid} with SIGTERM")
            process.send_signal(signal.SIGTERM)

    def stop(self):
        """Stop the engine: close stdin, wait, then SIGTERM and wait. Never SIGKILL."""
        with self.lock:
            process, reader = self.process, self.reader
            if process is None:
                return
            self.retiring = True
        if process.poll() is None:
            try:
                process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=_stop_grace())
            except subprocess.TimeoutExpired:
                self._note(f"pid {process.pid} did not exit after stdin closed; sending SIGTERM")
                process.send_signal(signal.SIGTERM)
                process.wait()
        else:
            process.wait()
        if reader is not None:
            reader.join()
        for stream in (process.stdin, process.stdout):
            try:
                stream.close()
            except OSError:
                pass
        with self.lock:
            if self.process is process:
                self.process, self.reader, self.key, self.ready = None, None, None, False
                self.retiring = False

    def close(self):
        self.stop()

    def _read(self, process):
        """Copy engine output into the current take's log and detect its end."""
        stream = process.stdout
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", "replace")
            line = text.rstrip("\r\n")
            job = self.current if self.current is not None and self.current.process is process else None
            if job is not None and job.returncode is not None:
                job = None
            begin, end = line.find(BEGIN), line.find(END)
            if line.startswith(READY):
                self.ready = line[len(READY):].strip() == PROTOCOL
                if not self.ready:
                    self._note("unsupported job protocol: " + line)
                    self.terminate(process)
                continue
            if begin >= 0:
                identity = line[begin + len(BEGIN):].strip()
                if job is not None and identity == job.id and not job.begun:
                    if begin:
                        job.write(line[:begin] + "\n")
                    job.begun = True
                else:
                    self._protocol_error(process, job, line)
                continue
            if end >= 0:
                identity, _, fields = line[end + len(END):].partition(" ")
                if job is None or identity != job.id or not job.begun:
                    self._protocol_error(process, job, line)
                    continue
                if end:
                    job.write(line[:end] + "\n")
                status, _, rest = fields.partition(" ")
                message = rest.split("message=", 1)[1] if "message=" in rest else ""
                if " fatal=1" in " " + rest.split("message=", 1)[0]:
                    self.retiring = True
                job.finish(0 if status == "status=ok" else 1, message)
                continue
            if job is not None:
                job.write(text if text.endswith("\n") else text + "\n")
                if not job.begun:
                    self._note(line)
            else:
                self._note(line)
        returncode = process.wait()
        self._note(f"pid {process.pid} exited with status {returncode}")
        with self.lock:
            if self.process is process:
                self.retiring = True
        job = self.current
        if job is not None and job.process is process:
            job.finish(returncode or 1, "The music engine stopped before finishing.")

    def _protocol_error(self, process, job, line):
        self._note("unexpected job marker: " + line)
        if job is not None:
            job.write("warm engine protocol error: " + line + "\n")
            job.finish(1, "The warm engine returned an unexpected result.")
        self.terminate(process)
