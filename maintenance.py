"""Background setup and verified updates, with explicit task and idle boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from paths import ROOT, DATA
import install
import platform_support
import setup_engine


class Maintenance:
    def __init__(self, generator, reviews):
        self.generator, self.reviews = generator, reviews
        self.lock = threading.RLock()
        self.task = {"status": "idle", "message": ""}
        self.thread, self.process = None, None
        self.stopping = threading.Event()
        self.install_root = Path(os.environ["RIFF_INSTALL_ROOT"]) if os.environ.get("RIFF_INSTALL_ROOT") else None
        self.release = None
        pending_path = DATA / "pending-update.json"
        self.pending = json.loads(pending_path.read_text()) if pending_path.exists() else None
        self.version = (ROOT / "VERSION").read_text().strip()
        if self.pending and self.pending.get("version") == self.version:
            self.pending = None
        self.preferences_path = DATA / "updates.json"
        self.preferences = json.loads(self.preferences_path.read_text()) if self.preferences_path.exists() else {
            "automatic_checks": True, "automatic_downloads": False, "check_interval_seconds": 86400}
        self.checker = threading.Thread(target=self.automatic, daemon=True)
        self.checker.start()

    def busy(self):
        with self.generator.store.db() as db:
            queued = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancelling')").fetchone()[0]
        reviews = self.reviews.snapshot() if self.reviews else []
        return bool(queued or self.generator.writer_gate.locked() or any(r["status"] in ("queued", "running", "cancelling") for r in reviews))

    def snapshot(self):
        with self.lock:
            engine = platform_support.readiness()
            return {"version": self.version, "engine": engine, "prerequisites": setup_engine.prerequisites(engine["backend"]),
                    "task": dict(self.task), "managed": bool(self.install_root), "preferences": dict(self.preferences),
                    "release": self.release, "pending": self.pending, "busy": self.busy()}

    def progress(self, message):
        with self.lock:
            self.task["message"] = message

    def run(self, command, cwd=None):
        if self.stopping.is_set():
            raise ValueError("Setup was stopped.")
        with self.lock:
            self.process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            process = self.process
        with (DATA / "setup.log").open("a") as log:
            try:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    self.progress(line.strip())
                if process.wait():
                    raise ValueError("Setup stopped: " + self.task.get("message", "Open the setup log for details."))
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait()
                process.stdout.close()
                with self.lock:
                    self.process = None

    def start(self, action, payload):
        with self.lock:
            if self.task["status"] == "running":
                raise ValueError("A setup or update task is already running.")
            if action != "check" and self.busy():
                raise ValueError("Finish or cancel the current takes and reviews before changing the engine.")
            self.task = {"status": "running", "action": action, "message": "Starting"}
            self.thread = threading.Thread(target=self.work, args=(action, payload), daemon=True)
            self.thread.start()
            return dict(self.task)

    def work(self, action, payload):
        gate = False
        try:
            if action != "check":
                gate = self.generator.model_gate.acquire(blocking=False)
                if not gate:
                    raise ValueError("The music engine is in use. Try again when it finishes.")
            if action == "setup":
                setup_engine.prepare(payload.get("backend", platform_support.settings()["backend"]), run=self.run,
                                     jobs=payload.get("jobs"), cuda_arch=payload.get("cuda_arch"))
                self.progress("Ready to make music")
            elif action == "check":
                self.release = install.get_release()
                if install.version_tuple(self.release["version"]) <= install.version_tuple(self.version):
                    self.release = None
                self.progress("An update is available" if self.release else "Riff is up to date")
            elif action == "update":
                if not self.install_root:
                    raise ValueError("Use the Riff installer to enable managed updates. Source checkouts stay under Git control.")
                release = install.get_release()
                if install.version_tuple(release["version"]) <= install.version_tuple(self.version):
                    self.progress("Riff is up to date")
                else:
                    target = install.stage_release(self.install_root, release, self.progress)
                    models_changed = json.loads((target / "sources.json").read_text())["model"] != json.loads((ROOT / "sources.json").read_text())["model"]
                    if setup_engine.fingerprint(target) != setup_engine.fingerprint() or models_changed:
                        # Build in a separate directory; the running engine remains usable until restart.
                        engine = setup_engine.prepare(platform_support.settings()["backend"], run=self.run, source=target,
                                                      activate=False, download_models=models_changed)
                        (target / ".engine.json").write_text(json.dumps(engine))
                    self.pending = {"version": release["version"], "path": str(target)}
                    (DATA / "pending-update.json").write_text(json.dumps(self.pending))
                    self.progress("Update ready. Restart Riff to use it.")
            else:
                raise ValueError("Unknown setup action.")
            with self.lock:
                self.task["status"] = "done"
        except Exception as exc:
            with self.lock:
                self.task.update(status="failed", message=str(exc))
        finally:
            if gate:
                self.generator.model_gate.release()

    def configure(self, payload):
        for key in ("automatic_checks", "automatic_downloads"):
            if key in payload and type(payload[key]) is not bool:
                raise ValueError("Update preferences must be on or off.")
        if "check_interval_seconds" in payload:
            value = payload["check_interval_seconds"]
            if type(value) is not int or value < 1:
                raise ValueError("The update check interval must be a positive number of seconds.")
        self.preferences.update({key: value for key, value in payload.items() if key in self.preferences})
        temporary = self.preferences_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.preferences) + "\n")
        temporary.replace(self.preferences_path)
        return self.snapshot()

    def apply_pending(self):
        if self.busy() or self.task["status"] == "running":
            raise ValueError("Finish or cancel the current work before restarting.")
        path = DATA / "pending-update.json"
        pending = self.pending or (json.loads(path.read_text()) if path.exists() else None)
        if not self.install_root or not pending:
            raise ValueError("There is no prepared update to install.")
        target = Path(pending["path"])
        install.activate(self.install_root, target)
        return {"status": "restarting"}

    def automatic(self):
        # One startup check, then a user-configurable interval. Downloads are opt-in.
        if not self.install_root:
            return
        while not self.stopping.wait(5):
            if self.preferences["automatic_checks"]:
                try:
                    self.start("check", {})
                    self.thread.join()
                    if self.release and self.preferences["automatic_downloads"] and not self.busy():
                        self.start("update", {})
                        self.thread.join()
                except ValueError:
                    pass
            if self.stopping.wait(self.preferences["check_interval_seconds"]):
                return

    def close(self):
        self.stopping.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
        if self.thread:
            self.thread.join(timeout=30)
