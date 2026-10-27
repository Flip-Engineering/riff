"""The stem reconstruction guard: divergent solo stems never publish (#78).

Dependency-free; no Ardour runtime is executed.  WAVs are synthesized in a
temporary directory: the guard mirrors probes/ardour/verify_stems.py and the
qualification contract §5.4 D (stems sum to the mix within 1 LSB).
"""
import array
from pathlib import Path
import tempfile
import unittest
import wave

import daw_runner


def write_wav(path, samples, width=2, channels=2, rate=48000):
    """samples: flat interleaved iterable of ints (16-bit unless width=1)."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        if width == 2:
            handle.writeframes(array.array("h", samples).tobytes())
        else:
            handle.writeframes(bytes(samples))


class StemReconstruction(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_exact_sum_passes(self):
        flute = [100, -100, 200, -200] * 64
        bass = [30, -30, 60, -60] * 64
        mix = [f + b for f, b in zip(flute, bass)]
        write_wav(self.root / "mix.wav", mix)
        write_wav(self.root / "stem-1.wav", flute)
        write_wav(self.root / "stem-2.wav", bass)
        daw_runner._verify_stem_reconstruction(self.root / "mix.wav",
                                               [self.root / "stem-1.wav", self.root / "stem-2.wav"])

    def test_one_lsb_boundary_passes(self):
        stem = [100, -100] * 128
        mix = [s + 1 for s in stem]  # uniformly 1 LSB over the sum
        write_wav(self.root / "mix.wav", mix)
        write_wav(self.root / "stem-1.wav", stem)
        daw_runner._verify_stem_reconstruction(self.root / "mix.wav", [self.root / "stem-1.wav"])

    def test_divergent_stem_refused(self):
        # A dropped/late event as observed in qualification: one stem loses
        # content while the mix keeps it.
        flute_good = [0, 0, 10828, -10828] * 64
        flute_bad = [0, 0, 8000, -8000] * 64
        mix = flute_good
        write_wav(self.root / "mix.wav", mix)
        write_wav(self.root / "stem-1.wav", flute_bad)
        with self.assertRaises(daw_runner.StageError) as raised:
            daw_runner._verify_stem_reconstruction(self.root / "mix.wav", [self.root / "stem-1.wav"])
        self.assertEqual(raised.exception.stage, "evidence")

    def test_length_mismatch_refused(self):
        write_wav(self.root / "mix.wav", [10, -10] * 128)
        write_wav(self.root / "stem-1.wav", [10, -10] * 64)
        with self.assertRaises(daw_runner.StageError) as raised:
            daw_runner._verify_stem_reconstruction(self.root / "mix.wav", [self.root / "stem-1.wav"])
        self.assertEqual(raised.exception.stage, "evidence")

    def test_non_16bit_refused(self):
        write_wav(self.root / "mix.wav", [128, 128] * 64, width=1)
        write_wav(self.root / "stem-1.wav", [128, 128] * 64, width=1)
        with self.assertRaises(daw_runner.StageError) as raised:
            daw_runner._verify_stem_reconstruction(self.root / "mix.wav", [self.root / "stem-1.wav"])
        self.assertEqual(raised.exception.stage, "evidence")


if __name__ == "__main__":
    unittest.main()
