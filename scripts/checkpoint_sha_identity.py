#!/usr/bin/env python3
"""Bind optional system SHA discovery to the homelab queue's artifact key.

Configuration only: this does not build the native engine. Use the exact pinned
CMake module, the caller's compiler environment, and the same portable override.
"""
import argparse
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PATCH = "patches/yue2-weight-system-sha.patch"
MODULE = "cmake/riff_checkpoint_sha.cmake"
REPAIR_MESSAGE = ("The engine's system crypto library changed or is unavailable. "
                  "Choose Download and set up in Settings to rebuild for this system.")


def file_identity(path):
    value = Path(path).stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


@lru_cache(maxsize=16)
def library_digest(path, identity):
    if file_identity(path) != identity:
        raise ValueError(REPAIR_MESSAGE)
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if file_identity(path) != identity:
        raise ValueError(REPAIR_MESSAGE)
    return digest.hexdigest()


def runtime_receipt_path(binary):
    return Path(binary).resolve().with_name("riff-sha256.json")


def verify_runtime(binary):
    """Guard managed engines before cached capability lookup or a new launch.

    Unmanaged/older engines have no receipt. Recordings and models are untouched.
    A library change invalidates this managed engine, rather than running it and
    exposing a dynamic-linker error. Rebuilding remains an explicit setup action.
    """
    path = runtime_receipt_path(binary)
    if not path.exists():
        return
    try:
        receipt = json.loads(path.read_text())
        if receipt["sha256_backend"] != "openssl-evp":
            return
        library = Path(receipt["library_path"]).resolve(strict=True)
        if library_digest(str(library), file_identity(library)) != receipt["libcrypto_sha256"]:
            raise ValueError(REPAIR_MESSAGE)
    except (OSError, KeyError, ValueError) as error:
        raise ValueError(REPAIR_MESSAGE) from error


def write_runtime_receipt(binary, build, expected):
    receipt = dict(expected)
    entries = {}
    for line in (Path(build) / "CMakeCache.txt").read_text().splitlines():
        if "=" in line and ":" in line.split("=", 1)[0]:
            key, value = line.split("=", 1)
            entries[key.split(":", 1)[0]] = value
    if entries.get("RIFF_CHECKPOINT_SHA_BACKEND") != receipt["sha256_backend"]:
        raise ValueError("System crypto selection changed during the build; run setup again.")
    if receipt["sha256_backend"] == "openssl-evp":
        try:
            library = Path(entries["OPENSSL_CRYPTO_LIBRARY"]).resolve(strict=True)
        except (OSError, KeyError) as error:
            raise ValueError("System libcrypto changed during the build; run setup again.") from error
        if library_digest(str(library), file_identity(library)) != receipt["libcrypto_sha256"]:
            raise ValueError("System libcrypto changed during the build; run setup again.")
        receipt["library_path"] = str(library)
    path = runtime_receipt_path(binary)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    temporary.replace(path)


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
