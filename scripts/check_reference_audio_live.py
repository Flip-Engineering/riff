#!/usr/bin/env python3
"""One live request: does the review model accept the take and two references as three audio parts?

This makes exactly one OpenRouter request and is the only part of #16 that tests do not cover.
It costs one short review. Use a key from OPENROUTER_API_KEY, never an argument.

    OPENROUTER_API_KEY=... python3 scripts/check_reference_audio_live.py [--model google/gemini-3.8-flash]
"""
import argparse
import array
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from review_client import listen
from review_recipe import FIELDS, GENERATED_TAKE
from studio_core import validate_recipe


def tone(path, hertz, seconds=3, rate=16000):
    samples = array.array("h", (int(6000 * math.sin(2 * math.pi * hertz * i / rate)) for i in range(int(seconds * rate))))
    with wave.open(str(path), "wb") as clip:
        clip.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        clip.writeframes(samples.tobytes())
    return path


def check(model, key):
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        take = tone(folder / "take.wav", 220)
        references = [{"id": str(number) * 32, "sha256": "0" * 64, "label": label, "link": "", "note": note,
                       "duration": 3.0, "bytes": 0, "format": "wav", "path": str(tone(folder / f"{label}.wav", hertz))}
                      for number, (label, note, hertz) in enumerate(
                          (("low drone", "A steady low tone", 110), ("bright tone", "A higher, brighter tone", 880)), 1)]
        recipe = validate_recipe({"title": "Live check", "lyrics": "", "style": "A single sustained tone",
                                  "mode": "instrumental", "max_seconds": 3, "steps": 8, "seed": "1", "cot": "off", "abc": ""})
        result = listen({"recipe": recipe, "keep_lyrics": True, "focus": "Describe each audio briefly.", "model": model,
                         "api_key": key, "audio": str(take), "duration": 3.0, "references": references})
    sources = {item.get("source") for item in result.get("observations") or []}
    expected = {GENERATED_TAKE, *(item["id"] for item in references)}
    print(json.dumps({"model": result["model"], "sent_audio": result["sent_audio"], "observation_sources": sorted(sources),
                      "every_audio_answered": sources == expected, "recipe_fields": sorted(result["generation"]) == sorted(FIELDS),
                      "usage": result["usage"]}, indent=2))
    return sources == expected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="google/gemini-3.8-flash")
    options = parser.parse_args()
    if not os.environ.get("OPENROUTER_API_KEY"):
        sys.exit("Set OPENROUTER_API_KEY. This script makes one paid request.")
    sys.exit(0 if check(options.model, os.environ["OPENROUTER_API_KEY"]) else 1)
