import tempfile
import unittest
from pathlib import Path

import setup_engine


class DriverMismatchTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.proc = self.dir / "version"
        self.proc.write_text("NVRM version: NVIDIA UNIX x86_64 Kernel Module  580.126.16  Thu Oct  2 2025\n")
        self.lib = self.dir / "lib"
        self.lib.mkdir()

    def mismatch(self):
        return setup_engine.nvidia_library_mismatch(self.proc, [self.lib])

    def test_the_computers_own_libraries_match(self):
        (self.lib / "libcuda.so.580.126.16").write_bytes(b"")
        self.assertIsNone(self.mismatch())

    def test_a_guest_copy_of_another_version_is_named(self):
        (self.lib / "libcuda.so.580.126.16").write_bytes(b"")
        (self.lib / "libcuda.so.580.178.04").write_bytes(b"")
        mismatch = self.mismatch()
        self.assertEqual(mismatch, ("580.126.16", ["580.178.04"]))
        message = setup_engine.mismatch_message(mismatch)
        self.assertIn("580.126.16", message)
        self.assertIn("580.178.04", message)
        self.assertIn("--no-install-recommends", message)

    def test_no_driver_module_means_no_claim(self):
        self.assertIsNone(setup_engine.nvidia_library_mismatch(self.dir / "absent", [self.lib]))


if __name__ == "__main__":
    unittest.main()
