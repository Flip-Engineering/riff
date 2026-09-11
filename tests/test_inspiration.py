import unittest
from inspiration import inspire
from run import build_command


class InspirationTests(unittest.TestCase):
    def test_seed_reproduces_idea_and_different_seeds_vary_it(self):
        first = inspire({"seed": "42", "theme": "water"})
        self.assertEqual(first, inspire({"seed": "42", "theme": "water"}))
        self.assertNotEqual(first["lyrics"], inspire({"seed": "123", "theme": "water"})["lyrics"])
        self.assertEqual(first["theme"], "water")

    def test_no_lyrics_required_for_instrumental_ideas(self):
        value = inspire({"mode": "instrumental", "seed": "1"})
        self.assertEqual(value["lyrics"], "")
        self.assertIn("no vocals", value["style"])

    def test_compass_values_and_seeds_are_validated(self):
        for payload in [{"seed": True}, {"energy": float("nan")}, {"texture": 2}, {"theme": []}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError): inspire(payload)

    def test_native_command_preserves_empty_and_arbitrary_conditioning(self):
        command = build_command(lyrics="", style="", max_seconds=10, steps=19, cot="off", seed=42,
                                threads=4, output="example.wav", mode="free", cfg_scale=1.2, temperature=1.7)
        self.assertEqual(command[command.index("--lyrics") + 1], "")
        self.assertFalse(any(value.startswith("style=") for value in command))
        self.assertIn("num_inference_steps=19", command)
        self.assertIn("cfg_scale=1.2", command)
        self.assertIn("semantic_temperature=1.7", command)
