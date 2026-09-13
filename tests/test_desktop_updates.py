import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install
from maintenance import Maintenance
from studio_core import Generator, Store
from admission_fixture import FixtureAdmission


def payload_fixture(root, version="0.5.4"):
    payload = root / "fixture-payload"
    entries = {"python": "runtime/python/python", "studio": "app/studio.py", "launcher": "app/launcher.py",
               "engine": "runtime/engine/audiocpp_cli", "ffmpeg": "runtime/media/ffmpeg",
               "ffprobe": "runtime/media/ffprobe", "control": "runtime/control/bin/riff_installer"}
    values = {name: b"fixture executable\n" for name in entries.values()}
    values.update({"app/VERSION": version.encode(), "app/sources.json": b'{"model":{"revision":"new-model"}}'})
    records = []
    for relative, content in sorted(values.items()):
        path = payload / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        executable = relative in entries.values()
        path.chmod(0o755 if executable else 0o644)
        records.append({"path": relative, "bytes": len(content), "executable": executable,
                        "sha256": hashlib.sha256(content).hexdigest()})
    runtime = [record for record in records if record["path"].startswith("runtime/")]
    digest = hashlib.sha256(json.dumps(runtime, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest = {"format_version": 1, "version": version, "platform": "macos-arm64", "files": records,
                "entries": entries, "runtime_sha256": digest, "runtime_id": digest[:24]}
    (payload / "manifest.json").write_text(json.dumps(manifest))
    archive = root / f"riff-v{version}-macos-arm64.payload.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for path in sorted(payload.rglob("*")):
            if path.is_file():
                output.add(path, arcname=path.relative_to(payload).as_posix())
    release = {"version": version, "tag": "v" + version, "name": archive.name, "kind": "desktop",
               "platform": "macos-arm64", "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
               "bytes": archive.stat().st_size,
               "url": f"https://github.com/{install.REPOSITORY}/releases/download/v{version}/{archive.name}"}
    return payload, archive, release


def current_runtime(root, script=None):
    runtime_id = "a" * 24
    runtime = root / "runtimes" / runtime_id
    control = runtime / "control/bin/riff_installer"
    control.parent.mkdir(parents=True)
    if script is None:
        script = """import json, os, pathlib, sys
request = json.load(sys.stdin)
assert sys.argv[1:] == ['eval', 'Riff.Installer.Update.main()']
assert request['protocol'] == 1
root, payload = pathlib.Path(request['root']), pathlib.Path(request['payload'])
manifest = json.loads((payload/'manifest.json').read_text())
version, runtime_id = manifest['version'], manifest['runtime_id']
target = root/'releases'/version
if request['action'] == 'prepare':
    target.mkdir(parents=True, exist_ok=True)
    (target/'studio.py').write_text('pass')
elif request['action'] == 'activate':
    if (root/'fail-activation').exists():
        print(json.dumps({'protocol':1, 'type':'result', 'status':'error', 'message':'Fixture preflight failed'}), flush=True)
        sys.exit(1)
    old = json.loads((root/'current.json').read_text())['version']
    staged = root/'current.json.tmp'
    staged.write_text(json.dumps({'version':version, 'previous':old}))
    staged.replace(root/'current.json')
print(json.dumps({'protocol':1, 'type':'progress', 'stage':'downloading', 'downloaded_bytes':64, 'total_bytes':128}), flush=True)
print(json.dumps({'protocol':1, 'type':'result', 'status':'prepared' if request['action']=='prepare' else 'activated', 'version':version, 'runtime_id':runtime_id, 'path':str(target)}), flush=True)
"""
    control.write_text("#!" + sys.executable + "\n" + script)
    control.chmod(0o755)
    helper = runtime / "control/lock"
    helper.write_text("#!/bin/sh\nprintf 'BUSY\\n'\n")
    helper.chmod(0o755)
    (runtime / "media").mkdir()
    (runtime / ".payload-manifest.json").write_text(json.dumps({"runtime_id": runtime_id, "platform": "macos-arm64"}))
    (root / "runtime.json").write_text(json.dumps({"format_version": 1, "runtime_id": runtime_id,
        "control": "control/bin/riff_installer", "media": "media", "lock_helper": "control/lock"}))
    return install.desktop_runtime(root)


class Download(BytesIO):
    def __init__(self, content, url):
        super().__init__(content)
        self.url = url

    def geturl(self):
        return self.url


class DesktopUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="riff-desktop-update-test-")
        self.root = Path(self.temporary.name)
        self.install_root = self.root / "installed"
        self.install_root.mkdir()
        (self.install_root / "current.json").write_text('{"version":"0.5.3"}')
        self.library = self.install_root / "workspace/data"
        self.library.mkdir(parents=True)
        (self.library / "keep-song").write_text("existing recording")
        self.payload, self.archive, self.release = payload_fixture(self.root)
        self.maintenances, self.generators = [], []

    def tearDown(self):
        for maintenance in self.maintenances:
            maintenance.close()
        for generator in self.generators:
            generator.close()
        self.temporary.cleanup()

    def unchanged(self):
        self.assertEqual(json.loads((self.install_root / "current.json").read_text()), {"version": "0.5.3"})
        self.assertEqual((self.library / "keep-song").read_text(), "existing recording")

    def test_official_desktop_asset_is_required_without_source_fallback(self):
        source = {"name": "riff-v0.5.4.tar.gz", "digest": "sha256:" + "c" * 64, "size": 12,
                  "browser_download_url": f"https://github.com/{install.REPOSITORY}/releases/download/v0.5.4/riff-v0.5.4.tar.gz"}
        value = {"tag_name": "v0.5.4", "assets": [source]}
        with patch("install.urllib.request.urlopen", return_value=BytesIO(json.dumps(value).encode())):
            with self.assertRaisesRegex(ValueError, "compatible desktop update"):
                install.get_release("macos-arm64")
        value["assets"].append({"name": self.release["name"], "digest": "sha256:" + self.release["sha256"],
                                "size": self.release["bytes"], "browser_download_url": self.release["url"]})
        with patch("install.urllib.request.urlopen", return_value=BytesIO(json.dumps(value).encode())):
            selected = install.get_release("macos-arm64")
        self.assertEqual(selected["kind"], "desktop")
        self.assertEqual(selected["sha256"], self.release["sha256"])
        value["assets"][-1]["browser_download_url"] = "https://other.invalid/payload.tar.gz"
        with patch("install.urllib.request.urlopen", return_value=BytesIO(json.dumps(value).encode())):
            with self.assertRaisesRegex(ValueError, "outside"):
                install.get_release("macos-arm64")

    def test_verified_staging_is_reusable_without_changing_selection(self):
        target = install.stage_desktop_release(self.install_root, self.release, archive=self.archive)
        self.assertEqual(target.parent, self.install_root.resolve() / ".desktop-updates")
        self.unchanged()
        self.assertEqual(install.stage_desktop_release(self.install_root, self.release, archive=self.archive), target)
        (target / "runtime/engine/audiocpp_cli").write_text("changed after verification")
        self.assertEqual(install.stage_desktop_release(self.install_root, self.release, archive=self.archive), target)
        self.assertEqual((target / "runtime/engine/audiocpp_cli").read_bytes(), b"fixture executable\n")
        preserved = list(target.parent.glob(target.name + ".unverified-*"))
        self.assertEqual(len(preserved), 1)
        self.assertEqual((preserved[0] / "runtime/engine/audiocpp_cli").read_text(), "changed after verification")
        self.unchanged()

    def test_bad_checksum_and_cancelled_extraction_leave_current_app_untouched(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            install.stage_desktop_release(self.install_root, {**self.release, "sha256": "0" * 64}, archive=self.archive)
        with self.assertRaises(install.UpdateCancelled):
            install.stage_desktop_release(self.install_root, self.release, cancelled=lambda: True, archive=self.archive)
        self.assertEqual(list((self.install_root / ".desktop-updates").iterdir()), [])
        self.unchanged()

    def test_manifest_rejects_wrong_platform_extra_files_and_runtime_identity(self):
        with self.assertRaisesRegex(ValueError, "platform"):
            install.validate_desktop_payload(self.payload, {**self.release, "platform": "linux-x86_64"})
        (self.payload / "unlisted").write_text("not in manifest")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            install.validate_desktop_payload(self.payload, self.release)
        (self.payload / "unlisted").unlink()
        path = self.payload / "manifest.json"
        manifest = json.loads(path.read_text()); manifest["runtime_id"] = "0" * 24
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "identity"):
            install.validate_desktop_payload(self.payload, self.release)

    def test_archive_rejects_overwrites_links_traversal_and_duplicates(self):
        for names in (("../outside",), ("app/link",), ("app/file", "app/file"), ("runtime/../../outside",)):
            with self.subTest(names=names):
                archive = self.root / "unsafe.tar.gz"
                with tarfile.open(archive, "w:gz") as output:
                    for name in names:
                        member = tarfile.TarInfo(name)
                        if name == "app/link":
                            member.type, member.linkname = tarfile.SYMTYPE, "../../../outside"
                        output.addfile(member)
                target = self.root / "unpack"
                with self.assertRaises(ValueError):
                    install.extract_desktop_archive(archive, target)
                self.assertFalse(target.exists())
                self.assertFalse((self.root / "outside").exists())

    def test_runtime_receipt_cannot_escape_or_silently_select_source_update(self):
        runtime = current_runtime(self.install_root)
        self.assertEqual(runtime["platform"], "macos-arm64")
        receipt = self.install_root / "runtime.json"
        original = json.loads(receipt.read_text())
        receipt.write_text(json.dumps({**original, "control": "../../external"}))
        with self.assertRaises(ValueError):
            install.desktop_runtime(self.install_root)
        receipt.write_text(json.dumps({**original, "runtime_id": "../../elsewhere"}))
        with self.assertRaises(ValueError):
            install.desktop_runtime(self.install_root)

    def test_cached_manifest_is_bound_to_original_archive_before_activation(self):
        target = install.stage_desktop_release(self.install_root, self.release, archive=self.archive)
        manifest = json.loads((target / "manifest.json").read_text())
        changed = target / "app/studio.py"
        changed.write_bytes(b"different app bytes\n")
        record = next(record for record in manifest["files"] if record["path"] == "app/studio.py")
        record.update(bytes=changed.stat().st_size, sha256=hashlib.sha256(changed.read_bytes()).hexdigest())
        (target / "manifest.json").write_text(json.dumps(manifest))
        # Internal hashes alone are consistent; release custody must still fail.
        install.validate_desktop_payload(target, self.release)
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            install.validate_prepared_desktop(self.install_root, target, self.release)
        self.unchanged()

    def maintenance(self):
        store = Store(self.library, self.install_root / "workspace/outputs")
        generator = Generator(store, admission=FixtureAdmission(),
            command_builder=MagicMock(side_effect=AssertionError("Use a fixture command for native generation.")))
        self.generators.append(generator)
        app = self.root / "running-app"; app.mkdir(exist_ok=True)
        (app / "VERSION").write_text("0.5.3")
        (app / "sources.json").write_text('{"model":{"revision":"old-model"}}')
        with patch.dict(os.environ, {"RIFF_INSTALL_ROOT": str(self.install_root)}), patch("maintenance.DATA", self.library), patch("maintenance.ROOT", app), patch.object(Maintenance, "automatic"):
            value = Maintenance(generator, None)
        self.maintenances.append(value)
        return value, app

    def test_model_changing_desktop_update_prepares_then_activates_without_compilers(self):
        current_runtime(self.install_root)
        maintenance, app = self.maintenance()
        with patch("maintenance.DATA", self.library), patch("maintenance.ROOT", app), patch("install.get_release", return_value=self.release), patch("install.urllib.request.urlopen", return_value=Download(self.archive.read_bytes(), self.release["url"])), patch("setup_engine.prepare") as compile_engine:
            maintenance.task = {"status": "running", "action": "update"}
            maintenance.work("update", {})
            self.assertEqual(maintenance.task["status"], "done", maintenance.task)
            self.unchanged()
            self.assertEqual(maintenance.pending["kind"], "desktop")
            self.assertEqual(json.loads((self.library / "pending-update.json").read_text())["runtime_id"], maintenance.pending["runtime_id"])
            self.assertFalse(maintenance.generator.quiescing)
            self.assertEqual(maintenance.apply_pending(), {"status": "restarting"})
            self.assertEqual(json.loads((self.install_root / "current.json").read_text()), {"version": "0.5.4", "previous": "0.5.3"})
            compile_engine.assert_not_called()
            self.assertTrue(maintenance.generator.quiescing)
            with self.assertRaisesRegex(ValueError, "updating"):
                maintenance.generator.inspiration({"idea_engine": "phrases"})

    def test_missing_asset_and_activation_failure_preserve_retryable_state(self):
        current_runtime(self.install_root)
        maintenance, app = self.maintenance()
        with patch("maintenance.DATA", self.library), patch("maintenance.ROOT", app), patch("install.get_release", side_effect=ValueError("A compatible desktop update is not available yet.")), patch("setup_engine.prepare") as compile_engine:
            maintenance.task = {"status": "running", "action": "update"}
            maintenance.work("update", {})
            self.assertEqual(maintenance.task["status"], "failed")
            self.assertFalse(maintenance.generator.quiescing)
            self.unchanged()
            compile_engine.assert_not_called()
        with patch("maintenance.DATA", self.library), patch("install.urllib.request.urlopen", return_value=Download(self.archive.read_bytes(), self.release["url"])):
            maintenance.prepare_desktop(install.desktop_runtime(self.install_root), self.release)
            maintenance.task["status"] = "done"
            (self.install_root / "fail-activation").touch()
            saved = (self.library / "pending-update.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "preflight"):
                maintenance.apply_pending()
            self.assertEqual((self.library / "pending-update.json").read_bytes(), saved)
            self.assertFalse(maintenance.generator.quiescing)
            self.unchanged()

    def test_busy_or_racing_writer_prevents_any_activation(self):
        maintenance, _ = self.maintenance()
        maintenance.pending = {"version": "0.5.4", "path": str(self.install_root / "releases/0.5.4")}
        with maintenance.generator._writer_slot() as writer:
            self.assertTrue(maintenance.busy())
            # Even a stale idle snapshot cannot let activation overtake a
            # writer admitted under the real generator's lock.
            with patch("maintenance.DATA", self.library), patch.object(maintenance, "busy", return_value=False), patch("install.activate") as activate:
                with self.assertRaisesRegex(ValueError, "current work"):
                    maintenance.apply_pending()
                activate.assert_not_called()
            self.assertFalse(maintenance.generator.quiescing)
            self.assertFalse(writer["cancelled"].is_set())

    def test_preparation_closes_real_admission_and_reopens_on_every_outcome(self):
        maintenance, app = self.maintenance()
        generator = maintenance.generator
        for outcome in ("done", "failed", "cancelled"):
            with self.subTest(outcome=outcome):
                entered, finish = threading.Event(), threading.Event()
                def prepare(*_args, **_kwargs):
                    entered.set()
                    if not finish.wait(5):
                        raise AssertionError("The test did not release preparation.")
                    if outcome == "failed":
                        raise ValueError("Fixture preparation failed")
                    install.check_cancelled(maintenance.update_cancelled)
                with patch("maintenance.DATA", self.library), patch("maintenance.ROOT", app), patch("setup_engine.prepare", side_effect=prepare):
                    maintenance.start("setup", {"backend": "cpu"})
                    try:
                        self.assertTrue(entered.wait(5), maintenance.task)
                        self.assertTrue(generator.quiescing)
                        with self.assertRaisesRegex(ValueError, "updating"):
                            generator.submit({"mode": "free"})
                        with self.assertRaisesRegex(ValueError, "updating"):
                            generator.inspiration({"mode": "surprise"})
                        if outcome == "cancelled":
                            maintenance.cancel()
                    finally:
                        finish.set()
                        maintenance.thread.join(5)
                    self.assertFalse(maintenance.thread.is_alive())
                    self.assertEqual(maintenance.task["status"], outcome, maintenance.task)
                    self.assertFalse(generator.quiescing)
                    self.assertFalse(generator.has_active_work())
                    self.assertTrue(generator.inspiration({"idea_engine": "phrases"})["lyrics"])
                    self.unchanged()

    def test_preparation_publishes_completion_only_after_resuming_admission(self):
        maintenance, app = self.maintenance()
        generator = maintenance.generator
        resuming, finish = threading.Event(), threading.Event()
        resume = generator.resume
        def pause_resume():
            resuming.set()
            if not finish.wait(5):
                raise AssertionError("The test did not release admission.")
            resume()
        with patch("maintenance.DATA", self.library), patch("maintenance.ROOT", app), patch("setup_engine.prepare"), patch.object(generator, "resume", side_effect=pause_resume):
            maintenance.start("setup", {"backend": "cpu"})
            try:
                self.assertTrue(resuming.wait(5), maintenance.task)
                self.assertEqual(maintenance.task["status"], "running")
                self.assertTrue(generator.quiescing)
                acquired = maintenance.lock.acquire(blocking=False)
                if acquired:
                    maintenance.lock.release()
                self.assertFalse(acquired)
            finally:
                finish.set()
                maintenance.thread.join(5)
            self.assertFalse(maintenance.thread.is_alive())
            self.assertEqual(maintenance.task["status"], "done")
            self.assertFalse(generator.quiescing)

    def test_racing_waiting_writer_preserves_work_and_prevents_preparation(self):
        maintenance, app = self.maintenance()
        generator = maintenance.generator
        generator.admission.gates["writer"].clear()
        waiting, results = threading.Event(), []
        reserve = generator.admission.reserve
        def observe_wait(*args, **kwargs):
            original = kwargs["on_wait"]
            def on_wait(reason):
                original(reason)
                waiting.set()
            return reserve(*args, **{**kwargs, "on_wait": on_wait})
        with patch.object(generator, "writer_ready", return_value=True), patch.object(generator.admission, "reserve", side_effect=observe_wait):
            writer = threading.Thread(target=lambda: results.append(generator.inspiration({"mode": "surprise"})))
            writer.start()
            try:
                self.assertTrue(waiting.wait(5))
                session = generator.writer_session
                self.assertIsNone(generator.writer_process)
                with patch("maintenance.DATA", self.library), patch("maintenance.ROOT", app), patch.object(maintenance, "busy", return_value=False), patch("setup_engine.prepare") as prepare:
                    maintenance.start("setup", {"backend": "cpu"})
                    maintenance.thread.join(5)
                    self.assertFalse(maintenance.thread.is_alive())
                    self.assertEqual(maintenance.task["status"], "failed", maintenance.task)
                    self.assertIn("current work", maintenance.task["message"])
                    prepare.assert_not_called()
                self.assertIs(generator.writer_session, session)
                self.assertFalse(session["cancelled"].is_set())
                self.assertFalse(generator.quiescing)
                self.assertTrue(writer.is_alive())
            finally:
                if generator.writer_session:
                    generator.cancel_writing()
                writer.join(5)
            self.assertFalse(writer.is_alive())
            self.assertEqual(results, [{"cancelled": True}])
            self.unchanged()

    def test_busy_native_review_and_export_preserve_active_work(self):
        maintenance, _ = self.maintenance()
        generator = maintenance.generator
        generator.command_builder = lambda *_: [sys.executable, "-c", "import time; time.sleep(300)"]
        first = generator.submit({"mode": "free"})["id"]
        deadline = time.monotonic() + 5
        while generator.process is None and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNotNone(generator.process)
        process = generator.process
        second = generator.submit({"mode": "free"})["id"]
        try:
            for action in ("setup", "update"):
                with self.assertRaisesRegex(ValueError, "current takes"):
                    maintenance.start(action, {})
            self.assertIsNone(process.poll())
            self.assertFalse(generator.job_cancelled.is_set())
            self.assertEqual(generator.store.job(second)["status"], "queued")
            self.assertFalse(generator.quiescing)
        finally:
            generator.cancel(second)
            generator.cancel(first)
            generator.close()
            self.generators.remove(generator)
        for kind in ("review", "export"):
            with self.subTest(kind=kind):
                worker = MagicMock()
                worker.snapshot.return_value = [{"status": "running"}]
                worker.busy.return_value = True
                maintenance.reviews = worker if kind == "review" else None
                maintenance.video_exports = worker if kind == "export" else None
                with self.assertRaisesRegex(ValueError, "current takes"):
                    maintenance.start("update", {})
                worker.cancel.assert_not_called()
                worker.close.assert_not_called()

    def test_source_install_keeps_its_existing_update_path(self):
        maintenance, app = self.maintenance()
        target = self.install_root / "releases/0.5.4"
        target.mkdir(parents=True)
        (target / "sources.json").write_text('{"model":{"revision":"old-model"}}')
        (target / "studio.py").write_text("pass")
        release = {"version": "0.5.4", "sha256": "b" * 64}
        with patch("maintenance.DATA", self.library), patch("maintenance.ROOT", app), patch("install.get_release", return_value=release) as select, patch("install.stage_release", return_value=target) as stage, patch("setup_engine.fingerprint", return_value="same-engine"), patch("install.stage_desktop_release") as desktop:
            maintenance.task = {"status": "running", "action": "update"}
            maintenance.work("update", {})
            self.assertEqual(maintenance.task["status"], "done", maintenance.task)
            select.assert_called_once_with()
            stage.assert_called_once()
            desktop.assert_not_called()
            self.unchanged()
            self.assertEqual(maintenance.apply_pending(), {"status": "restarting"})
        self.assertEqual(json.loads((self.install_root / "current.json").read_text()), {"version": "0.5.4", "previous": "0.5.3"})

    def test_external_installer_requires_matching_gate_and_live_lock(self):
        runtime = current_runtime(self.install_root)
        pending = {"kind": "external-installer", "version": "0.5.4", "installer_id": "owned-transaction"}
        gate = self.install_root / ".installer-activation.json"
        gate.write_text(json.dumps(pending))
        self.assertTrue(install.external_installer_handoff(runtime, self.install_root, pending))
        self.assertFalse(install.external_installer_handoff(runtime, self.install_root, {**pending, "installer_id": "different"}))
        runtime["lock_helper"].write_text("#!/bin/sh\nprintf 'LOCKED\\nRELEASED\\n'\n")
        self.assertFalse(install.external_installer_handoff(runtime, self.install_root, pending))

    def test_cancellation_stops_only_owned_control_group(self):
        runtime = current_runtime(self.install_root, "import json,sys,time\njson.load(sys.stdin)\nprint(json.dumps({'protocol':1,'type':'progress','message':'Waiting fixture'}),flush=True)\ntime.sleep(300)\n")
        cancelled, started = threading.Event(), threading.Event()
        processes, failures = [], []
        def capture(process):
            if process:
                processes.append(process)
        def progress(_):
            started.set()
        def invoke():
            try:
                install.desktop_control(runtime, "prepare", self.install_root, self.payload, progress, cancelled.is_set, capture)
            except BaseException as error:
                failures.append(error)
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        worker = threading.Thread(target=invoke)
        worker.start()
        try:
            self.assertTrue(started.wait(5), "owned control did not start")
            cancelled.set()
            install.stop_desktop_control(processes[0])
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertIsInstance(failures[0], install.UpdateCancelled)
            self.assertIsNone(unrelated.poll())
            self.unchanged()
        finally:
            if processes and processes[0].poll() is None:
                install.stop_desktop_control(processes[0])
            unrelated.terminate(); unrelated.wait()
            worker.join(5)

    def test_cancellation_before_request_write_is_reported_as_cancelled(self):
        runtime = current_runtime(self.install_root)
        cancelled = threading.Event()
        def stop_before_write(process):
            if process:
                cancelled.set()
                install.stop_desktop_control(process)
        with self.assertRaises(install.UpdateCancelled):
            install.desktop_control(runtime, "prepare", self.install_root, self.payload,
                                    cancelled=cancelled.is_set, process_changed=stop_before_write)
        self.unchanged()

    def test_http_music_writer_review_and_export_admission_waits_for_restart_decision(self):
        from studio import StudioServer
        from studio_core import Store
        class ObservedLock:
            def __init__(self):
                self.inner, self.entered = threading.RLock(), threading.Event()
            def __enter__(self):
                self.entered.set()
                self.inner.acquire()
            def __exit__(self, *_):
                self.inner.release()
        lock = ObservedLock()
        store = Store(self.root / "http-data", self.root / "http-outputs")
        generator, reviews = MagicMock(), MagicMock()
        generator.inspiration.return_value = {"title": "should not start"}
        reviews.submit.return_value = {"id": "c" * 32}
        maintenance = SimpleNamespace(lock=lock)
        server = StudioServer(("127.0.0.1", 0), store, generator, reviews, maintenance)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = "http://127.0.0.1:" + str(server.server_port)
        paths = ["/api/generations", "/api/inspiration", "/api/composition/revise",
                 "/api/tracks/" + "b" * 32 + "/reviews", "/api/tracks/" + "b" * 32 + "/video-exports"]
        try:
            for path in paths:
                with self.subTest(path=path):
                    server.restart_requested = False
                    replies = []
                    def post():
                        request = urllib.request.Request(url + path, data=b"{}",
                            headers={"Content-Type": "application/json", "Origin": url, "X-Riff-Request": "1"})
                        try:
                            with urllib.request.urlopen(request, timeout=5) as response:
                                replies.append((response.status, json.load(response)))
                        except urllib.error.HTTPError as response:
                            with response:
                                replies.append((response.code, json.load(response)))
                    lock.entered.clear()
                    lock.inner.acquire()
                    request = threading.Thread(target=post)
                    request.start()
                    try:
                        self.assertTrue(lock.entered.wait(5), "creation route skipped the restart boundary")
                        server.restart_requested = True
                    finally:
                        lock.inner.release()
                    request.join(5)
                    self.assertFalse(request.is_alive())
                    self.assertEqual(replies[0][0], 400)
                    self.assertIn("restarting", replies[0][1]["error"])
            generator.submit.assert_not_called()
            generator.inspiration.assert_not_called()
            reviews.submit.assert_not_called()
            self.assertFalse(server.video_exports.busy())
        finally:
            server.shutdown()
            worker.join(5)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
