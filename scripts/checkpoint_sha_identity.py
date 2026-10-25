#!/usr/bin/env python3
"""Bind optional system SHA discovery to the homelab queue's artifact key.

Configuration only: this does not build the native engine. Use the exact pinned
CMake module, the caller's compiler environment, and the same portable override.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PATCH = "patches/yue2-weight-system-sha.patch"
MODULE = "cmake/riff_checkpoint_sha.cmake"


def module_text(source=ROOT):
    source = Path(source)
    patch = (source / PATCH).read_bytes()
    pin = json.loads((source / "sources.json").read_text())["local_patches"][PATCH]
    if len(patch) != pin["bytes"] or hashlib.sha256(patch).hexdigest() != pin["sha256"]:
        raise ValueError("System SHA discovery patch differs from sources.json")
    section = patch.decode().split(f"diff --git a/{MODULE} b/{MODULE}\n", 1)[1].split("\ndiff --git ", 1)[0]
    if "new file mode" not in section:
        raise ValueError("Expected a complete new discovery module")
    return "\n".join(line[1:] for line in section.splitlines() if line.startswith("+") and not line.startswith("+++")) + "\n"


def discover(portable=False, source=ROOT, cmake=None):
    if cmake is None:
        from setup_engine import cmake_executable
        def provision(command):
            subprocess.run(command, capture_output=True, text=True, check=True)
        manifest = json.loads((Path(source) / "sources.json").read_text())
        cmake = cmake_executable(provision, manifest["cmake"])
    with tempfile.TemporaryDirectory(prefix="riff-sha-discovery-") as directory:
        root = Path(directory)
        (root / "sha.cmake").write_text(module_text(source))
        (root / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.18)\nproject(riff_sha_discovery CXX)\ninclude(sha.cmake)\n')
        args = [cmake, "-S", str(root), "-B", str(root / "build")]
        if portable:
            args.append("-DRIFF_CHECKPOINT_SYSTEM_SHA=OFF")
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("SHA discovery configuration failed: " + result.stdout + result.stderr)
        entries = {}
        for line in (root / "build/CMakeCache.txt").read_text().splitlines():
            if "=" in line and ":" in line.split("=", 1)[0]:
                key, value = line.split("=", 1)
                entries[key.split(":", 1)[0]] = value
        receipt = {"sha256_backend": entries["RIFF_CHECKPOINT_SHA_BACKEND"]}
        if receipt["sha256_backend"] == "openssl-evp":
            library = Path(entries["OPENSSL_CRYPTO_LIBRARY"])
            receipt["libcrypto_sha256"] = hashlib.sha256(library.read_bytes()).hexdigest()
            headers = Path(entries["OPENSSL_INCLUDE_DIR"]) / "openssl"
            digest = hashlib.sha256()
            for path in sorted(headers.glob("*.h")):
                digest.update(path.name.encode() + b"\0" + path.read_bytes())
            receipt["openssl_headers_sha256"] = digest.hexdigest()
        return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable-sha", action="store_true")
    options, _ = parser.parse_known_args()
    print(json.dumps(discover(options.portable_sha), sort_keys=True))
