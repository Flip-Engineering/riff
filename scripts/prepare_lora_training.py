#!/usr/bin/env python3
"""Prepare and verify a local YuE2 AI Toolkit dataset; never train or download."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
import tempfile


FORMAT = "riff.yue2.training-preparation.v1"
TRAINER_COMMIT = "04733873cf1da8060fce01316ec45daedbd4a918"
AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus"}
ASSETS = {
    "checkpoint": {
        "repo": "Comfy-Org/YuE2", "revision": "2f76ca75e6ee094169de899cc7fc99d6887e2196",
        "file": "checkpoints/yue2_3b_int8_convrot.safetensors", "bytes": 3960938800,
        "sha256": "96fe199377309001ed8cd26a944baeee8cc31a20ba7c36d1d3c0a7e1f4149db6",
    },
    "semantic_head": {
        "repo": "Mothersuperior/yue2-mothersuperior-realaudio-tokenizer-v4",
        "revision": "e2e63d859f3af879baf1b4d4e9f22d1eeda6fde5",
        "file": "tokenizer_head_joint_v9.safetensors", "bytes": 171278808,
        "sha256": "06440f25605c6c12c4e517ef033d7b70b8d9e961b3b06aa3848b1f8028fd7eb8",
    },
    "sheetsage_checkpoint": {
        "repo": "Comfy-Org/YuE2", "revision": "2f76ca75e6ee094169de899cc7fc99d6887e2196",
        "file": "audio_encoders/sheetsage2_bf16.safetensors", "bytes": 1386868122,
        "sha256": "5fd960ce3df281e3f3a889d174584d88f96247711480cf96377b12d7e8b6adc5",
    },
}


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _text(path):
    if not path.is_file():
        raise ValueError(f"Missing caption or lyrics sidecar: {path}")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError(f"Sidecar is larger than 1 MiB: {path}")
    return path.read_text(encoding="utf-8-sig").strip()


def _inside(root, path):
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Dataset file leaves its selected folder: {path}")
    return resolved


def collect(dataset):
    """Validate text structure and exact-file identity, without decoding audio."""
    root = Path(dataset).resolve()
    if not root.is_dir():
        raise ValueError("Dataset must be an existing directory")
    songs, hashes, stems = [], {}, set()
    for audio in sorted(root.rglob("*")):
        relative = audio.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if audio.suffix.lower() not in AUDIO_EXTENSIONS or not audio.is_file():
            continue
        _inside(root, audio)
        if audio.stat().st_size == 0:
            raise ValueError(f"Empty audio file: {relative}")
        stem = str(relative.with_suffix(""))
        if stem in stems:
            raise ValueError(f"Multiple audio encodings share one sidecar: {stem}")
        stems.add(stem)
        caption = audio.with_suffix(".txt")
        _inside(root, caption)
        raw_caption = _text(caption)
        sources = [caption]
        parts = re.split(r"(?im)^\s*\[Lyrics\]\s*$", raw_caption)
        if len(parts) == 2:
            style, lyrics = (part.strip() for part in parts)
        elif len(parts) == 1:
            lyric_file = audio.with_suffix(".lyrics.txt")
            _inside(root, lyric_file)
            style, lyrics = raw_caption, _text(lyric_file)
            sources.append(lyric_file)
        else:
            raise ValueError(f"Expected one [Lyrics] header: {relative}")
        style = re.sub(r"(?i)^\[Tags\]\s*", "", style).strip()
        if not style or not lyrics or "\x00" in style + lyrics:
            raise ValueError(f"Nonempty style and full lyrics/section cues required: {relative}")
        if not re.search(r"(?m)^\s*\[[^]\n]+\]\s*$", lyrics):
            raise ValueError(f"Lyrics need section headers, or [instrumental]: {relative}")
        sha = digest(audio)
        if sha in hashes:
            raise ValueError(f"Duplicate audio content: {relative} and {hashes[sha]}")
        hashes[sha] = str(relative)
        songs.append({
            "source": str(relative), "audio": audio, "sha256": sha,
            "bytes": audio.stat().st_size, "style": style, "lyrics": lyrics,
            "sidecars": [{"source": str(p.relative_to(root)), "sha256": digest(p)} for p in sources],
        })
    if len(songs) < 3:
        raise ValueError("At least three distinct songs are required for train and held-out splits")
    return songs


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def training_config(name, destination, steps, save_every):
    asset = lambda role: str(destination / "assets" / ASSETS[role]["file"])
    return {"job": "extension", "config": {"name": name, "process": [{
        "type": "sd_trainer", "training_folder": str(destination / "output"), "device": "cuda:0",
        "network": {"type": "lora", "linear": 32, "linear_alpha": 32, "transformer_only": True},
        "save": {"dtype": "bf16", "save_every": save_every,
                 "max_step_saves_to_keep": max(4, math.ceil(steps / save_every)), "push_to_hub": False},
        "datasets": [{"folder_path": str(destination / "train"), "caption_ext": "txt",
                      "caption_dropout_rate": 0, "shuffle_tokens": False,
                      "cache_latents_to_disk": True, "resolution": [512]}],
        "train": {"batch_size": 1, "steps": steps, "gradient_accumulation_steps": 1,
                  "train_unet": True, "train_text_encoder": False, "gradient_checkpointing": True,
                  "noise_scheduler": "flowmatch", "timestep_type": "sigmoid", "optimizer": "adamw8bit",
                  "lr": 5e-5, "dtype": "bf16", "disable_sampling": True},
        "model": {"name_or_path": asset("checkpoint"), "arch": "yue2", "quantize": True,
                  "qtype": "convrot8", "model_kwargs": {
                      "cot": "full", "abc_dropout": 0.5, "ar_loss_weight": 1.0,
                      "ar_lr_multiplier": 0.6, "ar_kl_weight": 0.2,
                      "ar_max_tokens": 0, "train_window_frames": 1500,
                      "ar_cuda_graphs": False, "merge_nar_lora": False,
                      "semantic_head_path": asset("semantic_head"),
                      "sheetsage_path": asset("sheetsage_checkpoint"),
                      "sample_ar_repetition_penalty": 1.2,
                  }},
    }]}}


def prepare(dataset, output, *, name, dataset_license, seed=42, heldout_fraction=0.2,
            steps=None, save_every=100):
    root, destination = Path(dataset).resolve(), Path(output).resolve()
    if destination.exists() or destination.is_relative_to(root):
        raise ValueError("Output must be a new directory outside the source dataset")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
        raise ValueError("Name must contain 1–80 letters, digits, underscores or hyphens")
    if not isinstance(dataset_license, str) or not dataset_license.strip():
        raise ValueError("Record the actual dataset license or permission; there is no default")
    if not math.isfinite(heldout_fraction) or not 0 < heldout_fraction < 1:
        raise ValueError("Held-out fraction must be between zero and one")
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2**32 - 1:
        raise ValueError("Seed must be an integer between 0 and 4294967295")
    if not isinstance(save_every, int) or isinstance(save_every, bool) or save_every < 1:
        raise ValueError("Save interval must be a positive integer")
    songs = collect(root)
    ordered = sorted(songs, key=lambda song: hashlib.sha256(f"{seed}:{song['sha256']}".encode()).hexdigest())
    count = min(len(songs) - 2, max(1, math.ceil(len(songs) * heldout_fraction)))
    heldout = {song["sha256"] for song in ordered[:count]}
    train_count = len(songs) - count
    # A short first sweep, not an estimated optimum or a quality guarantee.
    steps = steps if steps is not None else min(2000, train_count * 20)
    if not isinstance(steps, int) or isinstance(steps, bool) or steps < 1:
        raise ValueError("Steps must be a positive integer")
    save_every = min(save_every, steps)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".riff-training-", dir=destination.parent))
    try:
        records = []
        for song in songs:
            split = "heldout" if song["sha256"] in heldout else "train"
            folder = staging / split
            folder.mkdir(exist_ok=True)
            stem = "song-" + song["sha256"]
            audio_path = folder / (stem + song["audio"].suffix.lower())
            caption_path = folder / (stem + ".txt")
            shutil.copyfile(song["audio"], audio_path)
            if digest(audio_path) != song["sha256"]:
                raise ValueError(f"Audio changed while preparing: {song['source']}")
            caption_path.write_text(f"{song['style']}\n[Lyrics]\n{song['lyrics']}\n", encoding="utf-8")
            records.append({key: value for key, value in song.items() if key not in {"audio", "style", "lyrics"}} | {
                "split": split, "path": str(audio_path.relative_to(staging)),
                "caption_path": str(caption_path.relative_to(staging)), "caption_sha256": digest(caption_path),
            })
        config = training_config(name, destination, steps, save_every)
        reference = ordered[0]
        evaluation = {"format": "riff.yue2.lora-evaluation-plan.v1", "completed": False,
            "seed": seed, "cot": "full", "max_seconds": 120, "steps": 32,
            "variants": {"base": {"ar": 0, "nar": 0}, "ar_only": {"ar": 1, "nar": 0},
                         "nar_only": {"ar": 0, "nar": 1}, "paired": {"ar": 1, "nar": 1}},
            "prompts": [
                {"id": "heldout", "source_audio_sha256": reference["sha256"],
                 "style": reference["style"], "lyrics": reference["lyrics"]},
                {"id": "new_lyrics", "style": reference["style"],
                 "lyrics": "[Verse 1]\nThe morning opens every door\nA quiet rhythm finds the shore\n"
                           "[Chorus]\nWe carry light along the way\nAnd make a song of this new day"},
            ],
            "review_fields": ["lyric_accuracy", "voice_and_style", "structure_and_ending",
                              "high_frequency_artifacts", "training_song_replay", "notes"],
            "receipt_required": ["adapter_sha256", "base_sha256", "vae_sha256", "runtime_commit",
                                 "runtime_patch_hashes", "actual_recipe", "output_audio_sha256", "reviewer"],
        }
        _write_json(staging / "train.json", config)
        _write_json(staging / "evaluation.json", evaluation)
        manifest = {"format": FORMAT, "status": "prepared_only", "trained": False,
            "name": name, "output_root": str(destination), "dataset_license": dataset_license.strip(),
            "seed": seed, "heldout_fraction": heldout_fraction, "train_songs": train_count,
            "heldout_songs": count, "steps": steps, "nominal_passes_per_song": steps / train_count,
            "audio_decoding_validated": False, "duplicate_detection": "exact file SHA-256 only",
            "trainer": {"repo": "https://github.com/ostris/ai-toolkit", "commit": TRAINER_COMMIT},
            "base_model": "m-a-p/YuE2-3B", "premerged_nar_companion": None,
            "assets": ASSETS, "dependency_lock_complete": False,
            "remote_code_revisions_to_record": {
                "m-a-p/MERT-v2-FullSong": "7001f7657276238629e61c7746ce70735e0604c0",
                "m-a-p/SheetSage2": "b83c3d79a6d98bcffaf8f9ed112c90aa887887bc",
            },
            "files": records, "config_sha256": digest(staging / "train.json"),
            "evaluation_sha256": digest(staging / "evaluation.json"),
        }
        _write_json(staging / "manifest.json", manifest)
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging)
        raise
    return manifest


def verify(output, *, verify_assets=False):
    root = Path(output).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT or manifest.get("trained") is not False:
        raise ValueError("Expected an untrained Riff preparation manifest")
    if manifest.get("output_root") != str(root):
        raise ValueError("Prepared directory moved; regenerate its absolute training paths")
    for filename, key in (("train.json", "config_sha256"), ("evaluation.json", "evaluation_sha256")):
        if digest(_inside(root, root / filename)) != manifest[key]:
            raise ValueError(f"Prepared file changed: {filename}")
    identities, expected_audio, counts = set(), set(), {"train": 0, "heldout": 0}
    for song in manifest["files"]:
        split = song["split"]
        if split not in counts or Path(song["path"]).parts[0] != split:
            raise ValueError("Invalid prepared split")
        if Path(song["caption_path"]).parts[0] != split:
            raise ValueError("Caption crosses the train/held-out split")
        if song["sha256"] in identities:
            raise ValueError("Duplicate audio identity crosses or repeats in a split")
        identities.add(song["sha256"])
        expected_audio.add(str(_inside(root, root / song["path"])))
        counts[split] += 1
        for path_key, hash_key in (("path", "sha256"), ("caption_path", "caption_sha256")):
            if digest(_inside(root, root / song[path_key])) != song[hash_key]:
                raise ValueError(f"Prepared dataset changed: {song[path_key]}")
    if counts != {"train": manifest["train_songs"], "heldout": manifest["heldout_songs"]}:
        raise ValueError("Prepared split counts differ")
    actual_audio = {str(path.resolve()) for split in counts for path in (root / split).rglob("*")
                    if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS}
    if actual_audio != expected_audio:
        raise ValueError("Unrecorded audio was added to a prepared split")
    config = json.loads((root / "train.json").read_text(encoding="utf-8"))
    datasets = config["config"]["process"][0]["datasets"]
    if len(datasets) != 1 or Path(datasets[0]["folder_path"]).resolve() != root / "train":
        raise ValueError("Training config must reference only the prepared train folder")
    if verify_assets:
        for role, asset in ASSETS.items():
            path = _inside(root, root / "assets" / asset["file"])
            if not path.is_file() or path.stat().st_size != asset["bytes"] or digest(path) != asset["sha256"]:
                raise ValueError(f"Missing or changed pinned training asset: {role}: {path}")
    return {"format": FORMAT, "valid": True, "trained": False, **counts,
            "assets_verified": verify_assets, "dependency_lock_complete": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("prepare", help="Copy validated songs into distinct train/heldout folders")
    create.add_argument("dataset", type=Path)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--dataset-license", required=True, help="Actual license or permission for these recordings")
    create.add_argument("--seed", type=int, default=42)
    create.add_argument("--heldout-fraction", type=float, default=0.2)
    create.add_argument("--steps", type=int)
    create.add_argument("--save-every", type=int, default=100)
    check = commands.add_parser("verify", help="Check snapshots/configuration, optionally pinned model files")
    check.add_argument("output", type=Path)
    check.add_argument("--verify-assets", action="store_true")
    args = vars(parser.parse_args())
    command = args.pop("command")
    try:
        result = prepare(**args) if command == "prepare" else verify(**args)
        print(json.dumps({key: value for key, value in result.items() if key != "files"}, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
