import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from model_admission import AdmissionCancelled, ModelAdmission, SchedulerNotInstalled, resource_inputs
import platform_support
from studio_core import Generator


PORT_FIXTURE = r'''
import json,sys
sys.stdout.write('fixture ready\n');sys.stdout.flush()
for line in sys.stdin:
    message=json.loads(line)
    if message['op']=='estimate': result={'state':'estimated','requirement':{'host_peak':1024}}
    elif message['op']=='request': result={'state':'waiting' if message['requirement'].get('wait') else 'admitted','reason':'Waiting for memory'}
    else: result={'state':'released','received':message}
    text=json.dumps(result,ensure_ascii=False)+'\n'
    sys.stdout.write(text[:len(text)//2]);sys.stdout.flush()
    sys.stdout.write(text[len(text)//2:]);sys.stdout.flush()
'''


class Observation:
    def snapshot(self):
        return {"host": {"available": 8192, "pressure": "normal"}, "devices": {}, "device_processes": {}}


def arrived(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Expected admission state did not arrive")


class AdmissionBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.admission = ModelAdmission(self.root, observer=Observation(), command=[sys.executable, "-u", "-c", PORT_FIXTURE])
        self.addCleanup(self.admission.close)

    def test_private_port_handles_fragmented_unicode_and_exits_at_eof(self):
        result = self.admission._exchange({"op": "echo", "value": "هنا · 여기"})
        self.assertEqual(result["received"]["value"], "هنا · 여기")
        self.assertEqual(self.admission.estimate({}), {"host_peak": 1024})
        process = self.admission.process
        self.admission.close()
        self.assertEqual(process.returncode, 0)
        self.assertIn("fixture ready", (self.root / "scheduler.log").read_text())

    def test_crashed_policy_keeps_active_ownership_then_recovers_automatically(self):
        first_stop = threading.Event()
        self.admission.acquire("music", {"host_peak": 4096}, first_stop)
        original = self.admission.process
        original.terminate()
        original.wait()
        waiting, admitted, cancel = threading.Event(), threading.Event(), threading.Event()
        def write():
            self.admission.acquire("writer", {"host_peak": 1024}, cancel, lambda reason: waiting.set())
            admitted.set()
        thread = threading.Thread(target=write)
        thread.start()
        try:
            self.assertTrue(waiting.wait(5))
            self.assertFalse(admitted.is_set())
            self.assertTrue(self.admission.entries["music"]["admitted"])
            self.assertIs(self.admission.process, original)
            self.admission.release("music")
            self.assertTrue(admitted.wait(5))
            self.assertNotEqual(self.admission.process.pid, original.pid)
        finally:
            cancel.set()
            thread.join(5)
            self.admission.release("writer")

    def test_waiting_cancellation_removes_only_its_reservation(self):
        self.admission.acquire("music", {"host_peak": 4096}, threading.Event())
        waiting, cancel = threading.Event(), threading.Event()
        result = []
        def write():
            try:
                self.admission.acquire("writer", {"host_peak": 1024, "wait": True}, cancel, lambda reason: waiting.set())
            except AdmissionCancelled:
                result.append("cancelled")
        thread = threading.Thread(target=write)
        thread.start()
        self.assertTrue(waiting.wait(5))
        cancel.set()
        thread.join(5)
        self.assertEqual(result, ["cancelled"])
        self.assertEqual(list(self.admission.entries), ["music"])
        self.admission.release("music")

    def test_older_source_install_retains_serial_waiting_without_runtime_prerequisite(self):
        legacy = ModelAdmission(self.root / "legacy", observer=Observation())
        self.addCleanup(legacy.close)
        waiting, cancel, admitted = threading.Event(), threading.Event(), threading.Event()
        with patch.dict(os.environ, {}, clear=True), patch("model_admission.ROOT", self.root), patch("model_admission.WORKSPACE", self.root), patch("model_admission.shutil.which", return_value=None):
            first = legacy.reserve("music", {}, "native", threading.Event())
            self.assertEqual(first["policy"], "serial_compatibility")
            def write():
                legacy.reserve("writer", {}, "writer", cancel, lambda reason: waiting.set())
                admitted.set()
            thread = threading.Thread(target=write)
            thread.start()
            try:
                self.assertTrue(waiting.wait(5))
                self.assertFalse(admitted.is_set())
                self.assertIsNone(legacy.process)
                legacy.release("music")
                self.assertTrue(admitted.wait(5))
            finally:
                cancel.set()
                thread.join(5)
                legacy.release("writer")

    def test_explicit_bundled_runtime_and_writer_paths_take_priority(self):
        control = self.root / "control"
        control.touch()
        python = self.root / "python"
        python.touch()
        model = self.root / "writer"
        model.mkdir()
        (model / "model.safetensors").write_bytes(b"test")
        (model / "config.json").write_text('{"max_position_embeddings":2048}')
        with patch.dict(os.environ, {"RIFF_RUNTIME_CONTROL": str(control), "RIFF_WRITER_PYTHON": str(python), "RIFF_WRITER_MODEL": str(model)}):
            real = ModelAdmission(self.root, observer=Observation())
            self.assertEqual(real._command()[0][0], str(control))
            self.assertTrue(Generator.writer_ready())
            self.assertEqual(Generator.writer_command("out")[0], str(python))
            self.assertIn("-B", Generator.writer_command("out"))
            self.assertEqual(platform_support.writer_settings()["model"], model)
            inputs = resource_inputs({"cot": "off"}, "writer", Observation(), {})
            self.assertEqual(inputs["model_bytes"], 4)
            self.assertEqual(inputs["context"], 2048)
            real.close()

    def test_source_archive_with_system_mix_does_not_build_or_fetch_at_admission(self):
        project = self.root / "installer"
        project.mkdir()
        (project / "mix.exs").write_text("# source archive fixture")
        with patch.dict(os.environ, {}, clear=True), patch("model_admission.ROOT", self.root), patch("model_admission.WORKSPACE", self.root), patch("model_admission.shutil.which", return_value="/tools/mix"):
            scheduler = ModelAdmission(self.root, observer=Observation())
            with self.assertRaises(SchedulerNotInstalled):
                scheduler._command()
            for beam in ("riff_installer/ebin/Elixir.Riff.Runtime.SchedulerPort.beam", "jason/ebin/Elixir.Jason.beam"):
                destination = project / "_build/dev/lib" / beam
                destination.parent.mkdir(parents=True)
                destination.touch()
            command, directory = scheduler._command()
            self.assertEqual(directory, project)
            self.assertIn("--no-compile", command)
            self.assertIn("--no-deps-check", command)
            scheduler.close()


class ResourceObservationTests(unittest.TestCase):
    def test_cuda_visibility_selects_uuid_and_keeps_device_process_usage_separate(self):
        cards = "0, GPU-a, 8192, 4096\n1, GPU-b, 24576, 16384\n"
        processes = "42, GPU-a, 1024\n42, GPU-b, 2048\n77, GPU-b, [N/A]\n"
        with patch("platform_support.shutil.which", return_value="/test/nvidia-smi"), patch("platform_support.subprocess.check_output", side_effect=[cards, processes]), patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "1,0"}):
            devices, usage, identity = platform_support._cuda_memory(0)
        self.assertEqual(identity, "GPU-b")
        self.assertEqual(devices["GPU-b"]["available"], 16384 * 1024 ** 2)
        self.assertEqual(usage["GPU-a"]["42"], 1024 * 1024 ** 2)
        self.assertEqual(usage["GPU-b"]["42"], 2048 * 1024 ** 2)
        self.assertNotIn("77", usage["GPU-b"])

    def test_linux_container_budget_limits_host_available_memory(self):
        files = {"/proc/meminfo": "MemTotal: 16000 kB\nMemAvailable: 12000 kB\n",
                 "/proc/self/cgroup": "0::/riff/child\n", "/sys/fs/cgroup/riff/memory.max": "6000000",
                 "/sys/fs/cgroup/riff/memory.current": "4000000", "/sys/fs/cgroup/riff/child/memory.max": "max",
                 "/sys/fs/cgroup/riff/child/memory.current": "1000000", "/proc/pressure/memory": "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"}
        def read(path, *a, **kw):
            if str(path) not in files: raise FileNotFoundError(path)
            return files[str(path)]
        with patch.object(Path, "read_text", read):
            value = platform_support._linux_memory()
            self.assertEqual(value["pressure"], "normal")
            files["/sys/fs/cgroup/riff/memory.pressure"] = "full avg10=0.08 avg60=0.02 avg300=0.00 total=150000\n"
            self.assertEqual(platform_support._linux_memory()["pressure"], "warning")
            files["/proc/self/cgroup"] = "2:memory:/riff\n"
            files["/sys/fs/cgroup/memory/riff/memory.limit_in_bytes"] = "5000000"
            files["/sys/fs/cgroup/memory/riff/memory.usage_in_bytes"] = "4000000"
            self.assertEqual(platform_support._linux_memory()["available"], 1000000)
        self.assertEqual(value["total"], 6000000)
        self.assertEqual(value["available"], 2000000)

    def test_unknown_host_observation_does_not_invent_available_memory(self):
        with patch("platform_support.platform.system", return_value="Darwin"), patch("platform_support._mac_memory", side_effect=OSError), patch.dict(os.environ, {"RIFF_MEMORY_RESERVE_MB": "256"}):
            value = platform_support.MemoryObserver(backend="metal").snapshot()
        self.assertEqual(value["host"], {"pressure": "unknown", "reserve": 256 * 1024 ** 2})
