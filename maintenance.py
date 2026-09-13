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
        self.video_exports = None
        self.lock = threading.RLock()
        self.task = {"status": "idle", "message": ""}
        self.thread, self.process = None, None
        self.stopping = threading.Event()
        self.cancelled = threading.Event()
        self.desktop_process = False
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
        reviews = self.reviews.snapshot() if self.reviews else []
        return bool(self.generator.has_active_work() or (self.video_exports and self.video_exports.busy()) or any(r["status"] in ("queued", "running", "cancelling") for r in reviews))

    def snapshot(self):
        with self.lock:
            engine = platform_support.readiness()
            desktop = bool(self.install_root and ((self.install_root / "runtime.json").exists() or (self.install_root / "runtime.json").is_symlink()))
            return {"version": self.version, "engine": engine, "prerequisites": [] if desktop else setup_engine.prerequisites(engine["backend"]),
                    "desktop": desktop,
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
            self.cancelled.clear()
            self.task = {"status": "running", "action": action, "message": "Starting"}
            self.thread = threading.Thread(target=self.work, args=(action, payload), daemon=True)
            self.thread.start()
            return dict(self.task)

    def work(self, action, payload):
        quiesced, status = False, "failed"
        try:
            if action == "check":
                self.release = None
            if action != "check":
                with self.lock:
                    # A writer or queued take may arrive after start's idle
                    # check. Quiesce closes admission under the same lock used
                    # by the generator to claim that work, without stopping it.
                    if self.busy() or not self.generator.quiesce():
                        raise ValueError("Finish or cancel the current work before changing the engine.")
                    quiesced = True
            runtime = install.desktop_runtime(self.install_root)
            if action == "setup":
                if runtime:
                    raise ValueError("Open Riff Setup to repair the bundled music engine.")
                setup_engine.prepare(payload.get("backend", platform_support.settings()["backend"]), run=self.run,
                                     jobs=payload.get("jobs"), cuda_arch=payload.get("cuda_arch"))
                self.progress("Ready to make music")
            elif action == "check":
                try:
                    self.release = install.get_release(runtime["platform"], newer_than=self.version) if runtime else install.get_release()
                except install.DesktopUpdatePending:
                    self.progress("An update is being prepared.")
                else:
                    if self.release and install.version_tuple(self.release["version"]) <= install.version_tuple(self.version):
                        self.release = None
                    self.progress("An update is available" if self.release else "Riff is up to date")
            elif action == "update":
                if not self.install_root:
                    raise ValueError("Use the Riff installer to enable managed updates. Source checkouts stay under Git control.")
                release = install.get_release(runtime["platform"]) if runtime else install.get_release()
                if install.version_tuple(release["version"]) <= install.version_tuple(self.version):
                    self.progress("Riff is up to date")
                elif runtime:
                    self.prepare_desktop(runtime, release)
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
            status = "done"
        except install.UpdateCancelled as exc:
            status = "cancelled"
            self.progress(str(exc))
        except Exception as exc:
            self.progress(str(exc))
        finally:
            with self.lock:
                # Reopen before publishing completion. Otherwise restart can
                # quiesce successfully and then be undone by this worker's
                # late resume. Activation owns its separate idle boundary.
                if quiesced:
                    self.generator.resume()
                self.task["status"] = status

    def update_cancelled(self):
        return self.stopping.is_set() or self.cancelled.is_set()

    def control_process(self, process):
        with self.lock:
            self.process = process
            self.desktop_process = process is not None
            if process and self.update_cancelled() and self.task.get("action") != "activate":
                install.stop_desktop_control(process)

    def run_desktop(self, runtime, action, payload):
        with (DATA / "setup.log").open("a") as log:
            return install.desktop_control(runtime, action, self.install_root, payload,
                                           self.progress, self.update_cancelled if action == "prepare" else None,
                                           self.control_process, log)

    def prepare_desktop(self, runtime, release):
        payload = install.stage_desktop_release(self.install_root, release, self.progress, self.update_cancelled)
        result = self.run_desktop(runtime, "prepare", payload)
        manifest = json.loads((payload / "manifest.json").read_text())
        if result["version"] != release["version"] or result["runtime_id"] != manifest["runtime_id"]:
            raise ValueError("The prepared desktop update does not match this release.")
        install.check_cancelled(self.update_cancelled)
        pending = {"kind": "desktop", "version": result["version"], "path": result["path"],
                   "runtime_id": result["runtime_id"], "payload": str(payload), "release": release}
        temporary = DATA / "pending-update.json.tmp"
        temporary.write_text(json.dumps(pending) + "\n")
        temporary.replace(DATA / "pending-update.json")
        self.pending = pending
        self.progress("Update ready. Restart Riff to use it.")

    def cancel(self):
        with self.lock:
            if self.task.get("action") == "activate" and self.task["status"] == "running":
                raise ValueError("Riff is finishing its restart.")
            if self.task["status"] != "running":
                return dict(self.task)
            self.cancelled.set()
            self.task["message"] = "Stopping update preparation"
            process, desktop = self.process, self.desktop_process
        if process and process.poll() is None:
            if desktop:
                install.stop_desktop_control(process)
            else:
                process.terminate()
        with self.lock:
            return dict(self.task)

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
        if not self.generator.quiesce():
            raise ValueError("Finish or cancel the current work before restarting.")
        self.task = {"status": "running", "action": "activate", "message": "Opening the updated studio"}
        try:
            runtime = install.desktop_runtime(self.install_root)
            if pending.get("kind") == "desktop":
                if not runtime:
                    raise ValueError("The desktop runtime is unavailable. Reopen Riff Setup.")
                payload = Path(pending["payload"])
                manifest = install.validate_prepared_desktop(self.install_root, payload, pending["release"])
                if manifest["version"] != pending["version"] or manifest["runtime_id"] != pending["runtime_id"]:
                    raise ValueError("The prepared desktop identity changed. Prepare the update again.")
                result = self.run_desktop(runtime, "activate", payload)
                if result["version"] != pending["version"] or result["runtime_id"] != pending["runtime_id"]:
                    raise ValueError("The desktop activation receipt does not match the prepared update.")
            else:
                if runtime:
                    if not install.external_installer_handoff(runtime, self.install_root, pending):
                        raise ValueError("Prepare this update again with the desktop installer. Your current Riff is kept.")
                install.activate(self.install_root, Path(pending["path"]))
            self.task.update(status="done", message="Restarting Riff")
            return {"status": "restarting"}
        except Exception as exc:
            self.generator.resume()
            self.task.update(status="failed", message=str(exc))
            raise

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
        self.cancel()
        if self.thread:
            self.thread.join(timeout=30)
