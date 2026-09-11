#!/usr/bin/python3
"""Download only Q4 YuE2, its F16 VAE, and sidecars with bounded RAM."""
import hashlib
import argparse
import json
from pathlib import Path
import time
import urllib.request

from paths import MODELS, ROOT as CODE_ROOT
ROOT = MODELS
REPO = "audio-cpp/Yue2-3B-GGUF"
FILES = [
    "yue2-3b-q4_0.gguf",
    "yue2-vae-f16.gguf",
    "sidecars/yue2-model-config.json",
    "sidecars/yue2-generation-config.json",
    "sidecars/yue2-qwen.tiktoken",
    "sidecars/yue2-vae-config.json",
]


def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    # The release pins are authoritative. An installed manifest is only a receipt.
    manifest = json.loads((CODE_ROOT / "sources.json").read_text())["model"]
    from setup_engine import model_directory
    ROOT = args.directory or model_directory(manifest)
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Pinned {manifest['repo']}@{manifest['revision']}", flush=True)
    for name, expected in manifest["files"].items():
        target = ROOT / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size == expected["bytes"]:
            digest = digest_file(target)
            if expected["sha256"] and digest == expected["sha256"]:
                print(f"Verified {name}", flush=True)
                continue
        partial = target.with_name(target.name + ".part")
        url = f"https://huggingface.co/{manifest['repo']}/resolve/{manifest['revision']}/{name}"
        digest = hashlib.sha256()
        received = partial.stat().st_size if partial.exists() else 0
        if received > expected["bytes"]:
            raise RuntimeError(f"Partial file is too large: {partial}")
        if received:
            with partial.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
        if received == expected["bytes"] and expected["sha256"]:
            if digest.hexdigest() != expected["sha256"]:
                raise RuntimeError(f"SHA-256 mismatch in completed partial file: {partial}")
            partial.replace(target)
            print(f"Verified {name}", flush=True)
            continue
        last_progress = time.monotonic()
        print(f"Downloading {name}: {expected['bytes'] / 1e6:.1f} MB, resuming at {received / 1e6:.1f} MB", flush=True)
        request = urllib.request.Request(url, headers={"Range": f"bytes={received}-"} if received else {})
        with urllib.request.urlopen(request, timeout=60) as response:
            if received and response.status != 206:
                raise RuntimeError("Server did not honor the download resume range")
            if received and not response.headers.get("Content-Range", "").startswith(f"bytes {received}-"):
                raise RuntimeError("Server returned an unexpected download range")
            with partial.open("ab" if received else "wb") as output:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    digest.update(block)
                    received += len(block)
                    if received > expected["bytes"]:
                        raise RuntimeError(f"Download exceeded the pinned size: {name}")
                    if time.monotonic() - last_progress >= 15:
                        print(f"  {received / expected['bytes']:.0%}", flush=True)
                        last_progress = time.monotonic()
        if received != expected["bytes"]:
            raise RuntimeError(f"Size mismatch: {name}")
        if expected["sha256"] and digest.hexdigest() != expected["sha256"]:
            raise RuntimeError(f"SHA-256 mismatch: {name}")
        expected["sha256"] = digest.hexdigest()
        partial.replace(target)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Verified {name}", flush=True)


if __name__ == "__main__":
    main()
