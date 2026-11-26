"""A take records the device the engine itself reported, never the configured backend."""
import json
import tempfile
import unittest
from pathlib import Path

from engine_devices import reported_device
from studio_core import Generator, Store

CUDA_LOG = """ggml_cuda_init: found 2 CUDA devices (Total VRAM: 65024 MiB):
  Device 0: NVIDIA A100-SXM4-40GB, compute capability 8.0, VMM: yes, VRAM: 40326 MiB
  Device 1: Tesla V100-SXM2-32GB, compute capability 7.0, VMM: yes, VRAM: 32494 MiB
"""


class ReportedDeviceTest(unittest.TestCase):
    def test_the_cuda_device_of_the_run_is_the_one_the_engine_named(self):
        self.assertEqual(reported_device(CUDA_LOG, "cuda", 1),
                         {"backend": "cuda", "device": "Tesla V100-SXM2-32GB", "compute_capability": "7.0"})

    def test_a_run_without_a_device_line_is_not_reported(self):
        self.assertEqual(reported_device("generation done\n", "cuda", 0)["backend"], "not reported")
        self.assertEqual(reported_device(CUDA_LOG, "cpu", 0)["backend"], "not reported")

    def test_metal_names_its_gpu(self):
        line = "ggml_metal_device_init: GPU name:   MTL0 (Apple M2 Max)\n"
        self.assertEqual(reported_device(line, "metal", 0)["device"], "Apple M2 Max")

    def test_the_take_operation_records_the_device(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "data", root / "outputs")
            with store.db() as db:
                db.execute("INSERT INTO operations (id,name,surface_version,request_digest,created,job_id,family_id,"
                           "request,effective_recipe,refs,runtime) VALUES ('op1','take.render',1,'d',1,'job1','f1','{}','{}','[]',?)",
                           (json.dumps({"riff_version": "0.6.36"}),))
            log = root / "job1.log"
            log.write_text(CUDA_LOG)
            generator = Generator.__new__(Generator)
            generator.store = store
            device = generator.reported_device(["audiocpp_cli", "--backend", "cuda", "--device", "0"], log, False)
            generator.record_engine_device("job1", device)
            with store.db() as db:
                runtime = json.loads(db.execute("SELECT runtime FROM operations WHERE id='op1'").fetchone()[0])
            self.assertEqual(runtime["engine_device"]["device"], "NVIDIA A100-SXM4-40GB")
            self.assertEqual(runtime["riff_version"], "0.6.36")


if __name__ == "__main__":
    unittest.main()
