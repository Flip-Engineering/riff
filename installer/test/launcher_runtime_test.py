"""Exercise the legacy launcher bridge without touching a managed installation."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("riff_test_launcher", REPO / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class RuntimeBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="riff-launcher-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtimes" / ("a" * 24)
        self.receipt = {"format_version": 1, "runtime_id": "a" * 24,
                        "python": "python/bin/python3", "media": "media", "control": "control/bin/riff_installer",
                        "lock_helper": "control/lock", "environment": {"SSL_CERT_FILE": "/etc/ssl/cert.pem"}}
        for key in ("python", "control", "lock_helper"):
            path = self.runtime / self.receipt[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture")
        (self.runtime / "media").mkdir()
        shutil.copyfile(REPO / "installer/priv/native/riff-file-lock", self.runtime / "control/lock")
        (self.runtime / "control/lock").chmod(0o755)
        self.write_receipt()

    def write_receipt(self):
        (self.root / "runtime.json").write_text(json.dumps(self.receipt))

    def test_existing_source_install_has_no_runtime_requirement(self):
        (self.root / "runtime.json").unlink()
        self.assertIsNone(launcher.configured_runtime(self.root))

    def test_bundled_runtime_and_writer_paths_resolve_within_owned_roots(self):
        model = self.root / "workspace/models/lyric-writer"
        model.mkdir(parents=True)
        self.receipt["writer_model"] = "workspace/models/lyric-writer"
        self.write_receipt()
        runtime = launcher.configured_runtime(self.root)
        self.assertEqual(runtime["writer_model"], model.resolve())
        self.assertEqual(runtime["python"], (self.runtime / self.receipt["python"]).resolve())
        self.assertEqual(runtime["environment"], {"SSL_CERT_FILE": "/etc/ssl/cert.pem"})

    def test_malformed_receipts_and_path_escape_are_rejected(self):
        for field, invalid in [("runtime_id", None), ("runtime_id", 7), ("python", "../outside"),
                               ("python", "/usr/bin/python3"), ("python", ["python3"]),
                               ("environment", {"PYTHONPATH": "/outside"}), ("writer_model", "/outside")]:
            with self.subTest(field=field, invalid=invalid):
                original = dict(self.receipt)
                self.receipt[field] = invalid
                self.write_receipt()
                with self.assertRaises(ValueError):
                    launcher.configured_runtime(self.root)
                self.receipt = original

    def test_directory_symlink_is_rejected_even_when_it_points_to_an_existing_runtime(self):
        actual = self.root / "separate-runtime"
        self.runtime.rename(actual)
        self.runtime.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "linked path"):
            launcher.configured_runtime(self.root)

    def test_abandoned_activation_gate_recovers_under_the_native_lock(self):
        gate = self.root / ".installer-activation.json"
        gate.write_text("{}")
        launcher.wait_for_installer(self.root, launcher.configured_runtime(self.root))
        self.assertFalse(gate.exists())

    def test_live_installer_gate_stays_until_its_root_lock_is_released(self):
        runtime = launcher.configured_runtime(self.root)
        gate = self.root / ".installer-activation.json"
        gate.write_text("{}")
        with subprocess.Popen([str(runtime["lock_helper"]), str(self.root / ".riff-installer.lock")],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE) as helper:
            self.assertEqual(helper.stdout.readline().strip(), b"LOCKED")
            done = threading.Event()
            errors = []
            def wait():
                try:
                    launcher.wait_for_installer(self.root, runtime)
                except Exception as error:
                    errors.append(error)
                finally:
                    done.set()
            worker = threading.Thread(target=wait, daemon=True)
            worker.start()
            self.assertFalse(done.wait(0.15))
            self.assertTrue(gate.exists())
            helper.communicate(b"release")
            self.assertTrue(done.wait(3))
            worker.join()
            self.assertEqual(errors, [])
            self.assertFalse(gate.exists())

    def test_crashed_metadata_transaction_restores_every_original_file(self):
        originals = {"runtime.json": (self.root / "runtime.json").read_bytes(),
                     "launcher.py": b"previous launcher", "current.json": b'{"version":"0.4.0"}'}
        identity = "fixture-journal"
        backup_dir = self.root / ".activation" / identity
        backup_dir.mkdir(parents=True)
        for name, data in originals.items():
            (backup_dir / name).write_bytes(data)
            (self.root / name).write_bytes(b"new bytes")
        journal = {"transaction": {"protocol": 1, "phase": "prepared", "id": identity,
                     "backups": {name: f".activation/{identity}/{name}" for name in originals}}}
        (self.root / ".installer-activation.json").write_text(json.dumps(journal))
        # The actual launcher already loaded this validated helper before the
        # metadata write was interrupted; the recovery runs under its OS lock.
        launcher.wait_for_installer(self.root, {"lock_helper": self.runtime / "control/lock"})
        for name, data in originals.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertIsNotNone(launcher.configured_runtime(self.root))
        self.assertFalse((self.root / ".installer-activation.json").exists())


    def test_runtime_failure_restores_previous_python_control_writer_and_engine_together(self):
        old_value = dict(self.receipt)
        old_model = self.root / "workspace/models/writer-old"
        new_model = self.root / "workspace/models/writer-new"
        old_model.mkdir(parents=True)
        new_model.mkdir(parents=True)
        old_value["writer_model"] = "workspace/models/writer-old"
        new_runtime = self.root / "runtimes" / ("b" * 24)
        shutil.copytree(self.runtime, new_runtime)
        self.receipt.update(runtime_id="b" * 24, writer_model="workspace/models/writer-new")
        self.write_receipt()
        for version in ("0.4.0", "0.5.0"):
            app = self.root / "releases" / version
            app.mkdir(parents=True)
            (app / "studio.py").write_text("fixture")
        previous_app = self.root / "releases/0.4.0"
        (previous_app / ".runtime.json").write_text(json.dumps(old_value))
        (self.root / "current.json").write_text(json.dumps({"version": "0.5.0", "previous": "0.4.0", "previous_runtime": old_value}))
        (self.root / "launcher.py").write_text("stable launcher")
        data = self.root / "workspace/data"
        data.mkdir()
        (data / "engine.json").write_text(json.dumps({"binary": "new-engine"}))
        (data / "engine-activations.json").write_text(json.dumps({"0.5.0": {"before": {"binary": "old-engine"}}}))
        checks = []
        def preflight(python, app, environment):
            checks.append((python, app, environment))
            return app.name == "0.4.0"
        with mock.patch.object(launcher, "__file__", str(self.root / "launcher.py")), \
             mock.patch.object(launcher, "app_check", side_effect=preflight), \
             mock.patch.object(launcher.os, "execve", side_effect=SystemExit(0)) as execute:
            with self.assertRaises(SystemExit):
                launcher.main()
        self.assertEqual(checks[0][0], (new_runtime / self.receipt["python"]).resolve())
        self.assertEqual(checks[1][0], (self.runtime / old_value["python"]).resolve())
        self.assertEqual(checks[1][2]["RIFF_WRITER_MODEL"], str(old_model.resolve()))
        self.assertEqual(execute.call_args.args[0], str((self.runtime / old_value["python"]).resolve()))
        self.assertEqual(execute.call_args.args[2]["RIFF_RUNTIME_CONTROL"], str((self.runtime / old_value["control"]).resolve()))
        self.assertEqual(json.loads((self.root / "runtime.json").read_text()), old_value)
        self.assertEqual(json.loads((self.root / "current.json").read_text())["version"], "0.4.0")
        self.assertEqual(json.loads((data / "engine.json").read_text()), {"binary": "old-engine"})
        self.assertFalse((self.root / ".installer-activation.json").exists())

    def test_interrupted_engine_fallback_recovers_app_runtime_and_both_engine_files(self):
        old_value = dict(self.receipt)
        previous = self.root / "releases/0.4.0"
        previous.mkdir(parents=True)
        (self.root / "launcher.py").write_text("launcher")
        (self.root / "current.json").write_text(json.dumps({"version": "0.5.0", "previous": "0.4.0"}))
        data = self.root / "workspace/data"
        data.mkdir(parents=True)
        (data / "engine.json").write_text(json.dumps({"binary": "failed-new-engine"}))
        (data / "engine-activations.json").write_text(json.dumps({"0.5.0": {"before": {"binary": "previous-engine"}}}))
        originals = {name: (self.root / name).read_bytes() for name in
                     ("runtime.json", "launcher.py", "current.json", "workspace/data/engine.json", "workspace/data/engine-activations.json")}
        apply_engine = launcher.activate_engine
        def interrupted(*arguments):
            apply_engine(*arguments)
            raise SystemExit("simulated process termination after engine restoration")
        runtime = launcher.configured_runtime(self.root)
        with mock.patch.object(launcher, "activate_engine", side_effect=interrupted):
            with self.assertRaises(SystemExit):
                launcher.select_fallback(self.root, "0.5.0", "0.4.0", old_value, runtime)
        self.assertEqual(json.loads((data / "engine.json").read_text()), {"binary": "previous-engine"})
        self.assertTrue((self.root / ".installer-activation.json").exists())
        launcher.wait_for_installer(self.root, runtime)
        for name, contents in originals.items():
            self.assertEqual((self.root / name).read_bytes(), contents)
        self.assertFalse((self.root / ".installer-activation.json").exists())
        launcher.select_fallback(self.root, "0.5.0", "0.4.0", old_value, runtime)
        self.assertEqual(json.loads((self.root / "current.json").read_text())["version"], "0.4.0")
        self.assertEqual(json.loads((data / "engine.json").read_text()), {"binary": "previous-engine"})
        self.assertEqual(json.loads((data / "engine-activations.json").read_text()), {})


    def test_first_launch_interruption_preserves_the_actual_previous_engine_for_later_rollback(self):
        app = self.root / "releases/0.5.0"
        app.mkdir(parents=True)
        (app / ".engine.json").write_text(json.dumps({"binary": "new-engine"}))
        (self.root / "launcher.py").write_text("launcher")
        (self.root / "current.json").write_text(json.dumps({"version": "0.5.0", "previous": "0.4.0"}))
        data = self.root / "workspace/data"
        data.mkdir(parents=True)
        (data / "engine.json").write_text(json.dumps({"binary": "actual-old-engine"}))
        (data / "engine-activations.json").write_text("{}")
        runtime = launcher.configured_runtime(self.root)
        write = launcher.write_json_atomic
        def interrupted(path, value):
            if path.name == "engine-activations.json":
                raise SystemExit("simulated termination between engine writes")
            return write(path, value)
        with mock.patch.object(launcher, "write_json_atomic", side_effect=interrupted):
            with self.assertRaises(SystemExit):
                launcher.select_engine(self.root, app, runtime)
        self.assertEqual(json.loads((data / "engine.json").read_text()), {"binary": "new-engine"})
        self.assertEqual(json.loads((data / "engine-activations.json").read_text()), {})
        self.assertTrue((self.root / ".installer-activation.json").exists())
        launcher.wait_for_installer(self.root, runtime)
        self.assertEqual(json.loads((data / "engine.json").read_text()), {"binary": "actual-old-engine"})
        launcher.select_engine(self.root, app, runtime)
        receipt = json.loads((data / "engine-activations.json").read_text())
        self.assertEqual(receipt["0.5.0"]["before"], {"binary": "actual-old-engine"})
        launcher.select_fallback(self.root, "0.5.0", "0.4.0", self.receipt, runtime)
        self.assertEqual(json.loads((data / "engine.json").read_text()), {"binary": "actual-old-engine"})


    def test_recovery_refuses_a_replaced_workspace_ancestor(self):
        identity = "linked-workspace-fixture"
        backup = self.root / ".activation" / identity / "workspace/data/engine.json"
        backup.parent.mkdir(parents=True)
        backup.write_text("old owned settings")
        outside = self.root / "outside"
        (outside / "data").mkdir(parents=True)
        protected = outside / "data/engine.json"
        protected.write_text("unrelated settings")
        (self.root / "workspace").symlink_to(outside, target_is_directory=True)
        backups = {name: None for name in ("current.json", "launcher.py", "runtime.json", "workspace/data/engine-activations.json")}
        backups["workspace/data/engine.json"] = f".activation/{identity}/workspace/data/engine.json"
        gate = self.root / ".installer-activation.json"
        gate.write_text(json.dumps({"transaction": {"protocol": 1, "id": identity, "phase": "prepared", "backups": backups}}))
        with self.assertRaisesRegex(ValueError, "not an owned directory"):
            launcher.recover_activation(self.root)
        self.assertEqual(protected.read_text(), "unrelated settings")
        self.assertTrue(gate.exists())


if __name__ == "__main__":
    unittest.main()
