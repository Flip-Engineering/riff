"""Training preparation isolates held-out audio and never claims a trained model."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import wave

from scripts import prepare_lora_training as training


class TrainingPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "songs"
        self.dataset.mkdir()
        for number in range(5):
            audio = self.dataset / f"song{number}.wav"
            with wave.open(str(audio), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(8000)
                stream.writeframes(bytes([number, 0]) * 64)
            audio.with_suffix(".txt").write_text("acoustic guitar, clear voice", encoding="utf-8")
            audio.with_suffix(".lyrics.txt").write_text(
                "[Verse 1]\nA song for this test\n[Chorus]\nLet the melody rest", encoding="utf-8")

    def prepare(self, name="prepared", **kwargs):
        return training.prepare(self.dataset, self.root / name, name="test_style",
                                dataset_license="Test fixture recordings created for this test", **kwargs)

    def test_distinct_splits_only_train_is_configured_and_default_is_bounded(self):
        result = self.prepare()
        self.assertEqual((result["train_songs"], result["heldout_songs"]), (4, 1))
        self.assertEqual(result["steps"], 80)
        self.assertFalse(result["trained"])
        self.assertFalse(result["dependency_lock_complete"])
        folder = self.root / "prepared"
        process = json.loads((folder / "train.json").read_text())["config"]["process"][0]
        self.assertEqual(process["datasets"][0]["folder_path"], str(folder / "train"))
        self.assertEqual(process["datasets"][0]["caption_dropout_rate"], 0)
        self.assertFalse(process["model"]["model_kwargs"]["merge_nar_lora"])
        self.assertTrue(process["train"]["disable_sampling"])
        self.assertEqual(training.verify(folder)["heldout"], 1)
        evaluation = json.loads((folder / "evaluation.json").read_text())
        self.assertFalse(evaluation["completed"])
        self.assertEqual(set(evaluation["variants"]), {"base", "ar_only", "nar_only", "paired"})
        self.assertNotEqual(evaluation["prompts"][0]["lyrics"], evaluation["prompts"][1]["lyrics"])

    def test_split_identity_is_stable_when_source_files_are_renamed(self):
        before = self.prepare("before")
        for path in sorted(self.dataset.iterdir()):
            path.rename(path.with_name("renamed_" + path.name))
        after = self.prepare("after")
        self.assertEqual({item["sha256"]: item["split"] for item in before["files"]},
                         {item["sha256"]: item["split"] for item in after["files"]})

    def test_duplicate_recordings_fail_even_under_different_names(self):
        shutil.copyfile(self.dataset / "song0.wav", self.dataset / "song1.wav")
        with self.assertRaisesRegex(ValueError, "Duplicate audio"):
            self.prepare()
        self.assertFalse((self.root / "prepared").exists())

    def test_config_or_caption_mutation_and_extra_audio_are_detected(self):
        manifest = self.prepare()
        folder = self.root / "prepared"
        heldout = next(item for item in manifest["files"] if item["split"] == "heldout")
        leaked = folder / "train" / "accidental_copy.wav"
        shutil.copyfile(folder / heldout["path"], leaked)
        with self.assertRaisesRegex(ValueError, "Unrecorded audio"):
            training.verify(folder)
        leaked.unlink()
        caption = folder / heldout["caption_path"]
        original = caption.read_bytes()
        caption.write_text("changed")
        with self.assertRaisesRegex(ValueError, "Prepared dataset changed"):
            training.verify(folder)
        caption.write_bytes(original)
        (folder / "train.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "Prepared file changed"):
            training.verify(folder)

    def test_missing_sidecars_and_input_output_overlap_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            training.prepare(self.dataset, self.dataset / "prepared", name="test", dataset_license="owned")
        (self.dataset / "song0.lyrics.txt").unlink()
        with self.assertRaisesRegex(ValueError, "sidecar"):
            self.prepare()
        self.assertFalse((self.root / "prepared").exists())

    def test_native_combined_caption_supported_and_missing_assets_reported(self):
        for path in self.dataset.glob("*.lyrics.txt"):
            path.unlink()
        for path in self.dataset.glob("*.txt"):
            path.write_text("[Tags]\nsoft piano\n[Lyrics]\n[instrumental]\n")
        self.prepare()
        with self.assertRaisesRegex(ValueError, "Missing or changed pinned training asset"):
            training.verify(self.root / "prepared", verify_assets=True)
        self.assertFalse((self.root / "prepared" / "assets").exists())

    def test_dataset_permission_and_valid_numeric_parameters_are_required(self):
        with self.assertRaisesRegex(ValueError, "license or permission"):
            training.prepare(self.dataset, self.root / "prepared", name="test", dataset_license="")
        for kwargs in ({"heldout_fraction": float("nan")}, {"steps": 0}, {"save_every": 0}, {"seed": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.prepare(**kwargs)


if __name__ == "__main__":
    unittest.main()
