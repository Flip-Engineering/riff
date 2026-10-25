#!/usr/bin/env python3
"""Bind optional system SHA discovery to the homelab queue's artifact key.

Configuration only: this does not build the native engine. Use the exact pinned
CMake module, the caller's compiler environment, and the same portable override.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PATCH = "patches/yue2-weight-system-sha.patch"
MODULE = "cmake/riff_checkpoint_sha.cmake"
REPAIR_MESSAGE = ("The engine's system crypto library is missing or has a different major version. "
                  "Choose Download and set up in Settings to rebuild for this system.")


def library_soname(path):
    """Read the ELF dynamic SONAME without loading code or requiring binutils."""
    with Path(path).open("rb") as source:
        def unpack(fmt, offset):
            source.seek(offset)
            return struct.unpack(endian + fmt, source.read(struct.calcsize(endian + fmt)))

        ident = source.read(16)
        if len(ident) != 16 or ident[:4] != b"\x7fELF" or ident[4] not in (1, 2) or ident[5] not in (1, 2):
            raise ValueError("System libcrypto is not a supported ELF shared library")
        endian = "<" if ident[5] == 1 else ">"
        wide = ident[4] == 2
        header = unpack("HHIQQQIHHHHHH" if wide else "HHIIIIIHHHHHH", 16)
        offset, stride, count = header[4], header[8], header[9]
        segments = []
        for index in range(count):
            row = unpack("IIQQQQQQ" if wide else "IIIIIIII", offset + index * stride)
            kind, start, address, size = (row[0], row[2], row[3], row[5]) if wide else (row[0], row[1], row[2], row[4])
            segments.append((kind, start, address, size))
        dynamic = next((item for item in segments if item[0] == 2), None)  # PT_DYNAMIC
        if dynamic is None:
            raise ValueError("System libcrypto has no dynamic metadata")
        tags = {}
        width = 16 if wide else 8
        for offset in range(dynamic[1], dynamic[1] + dynamic[3], width):
            tag, value = unpack("qQ" if wide else "iI", offset)
            if tag == 0:
                break
            tags[tag] = value
        if 5 not in tags or 14 not in tags:  # DT_STRTAB, DT_SONAME
            raise ValueError("System libcrypto has no SONAME")
        address = tags[5] + tags[14]
        for kind, start, base, size in segments:
            if kind == 1 and base <= address < base + size:  # PT_LOAD
                source.seek(start + address - base)
                value = bytearray()
                while True:
                    char = source.read(1)
                    if not char:
                        raise ValueError("Unterminated system libcrypto SONAME")
                    if char == b"\0":
                        name = value.decode("ascii")
                        if not name or Path(name).name != name:
                            raise ValueError("Invalid system libcrypto SONAME")
                        return name
                    value.extend(char)
        raise ValueError("System libcrypto SONAME is outside its load segments")


def cache_entries(build):
    entries = {}
    for line in (Path(build) / "CMakeCache.txt").read_text().splitlines():
        if "=" in line and ":" in line.split("=", 1)[0]:
            key, value = line.split("=", 1)
            entries[key.split(":", 1)[0]] = value
    return entries


def crypto_identity(entries):
    receipt = {"sha256_backend": entries["RIFF_CHECKPOINT_SHA_BACKEND"]}
    if receipt["sha256_backend"] == "openssl-evp":
        receipt["libcrypto_soname"] = library_soname(entries["OPENSSL_CRYPTO_LIBRARY"])
        header = (Path(entries["OPENSSL_INCLUDE_DIR"]) / "openssl/opensslv.h").read_text()
        major = re.search(r"^\s*#\s*define\s+OPENSSL_VERSION_MAJOR\s+(\d+)\b", header, re.M)
        if major:
            receipt["openssl_version_major"] = major[1]
        else:
            # OpenSSL before 3.0 exposes its major in OPENSSL_VERSION_NUMBER.
            number = re.search(r"^\s*#\s*define\s+OPENSSL_VERSION_NUMBER\s+0x([0-9a-fA-F]+)L?\b", header, re.M)
            if not number:
                raise ValueError("Cannot identify the system OpenSSL header major version")
            receipt["openssl_version_major"] = str(int(number[1], 16) >> 28)
    return receipt


def runtime_receipt_path(binary):
    return Path(binary).resolve().with_name("riff-sha256.json")


def verify_runtime(binary):
    """Guard managed engines before cached capability lookup or a new launch.

    Unmanaged/older engines have no receipt. Recordings and models are untouched.
    A missing SONAME or incompatible replacement requires explicit setup. Patch
    updates with the same ABI remain usable, without hashing the library bytes.
    """
    path = runtime_receipt_path(binary)
    if not path.exists():
        return
    try:
        receipt = json.loads(path.read_text())
        if receipt["sha256_backend"] != "openssl-evp":
            return
        if library_soname(receipt["library_path"]) != receipt["libcrypto_soname"]:
            raise ValueError(REPAIR_MESSAGE)
    except (OSError, KeyError, ValueError, struct.error) as error:
        raise ValueError(REPAIR_MESSAGE) from error


def write_runtime_receipt(binary, build, expected):
    receipt = dict(expected)
    entries = cache_entries(build)
    try:
        actual = crypto_identity(entries)
    except (OSError, KeyError, ValueError, struct.error) as error:
        raise ValueError("System crypto selection changed during the build; run setup again.") from error
    if actual != {key: receipt.get(key) for key in actual}:
        raise ValueError("System crypto selection changed during the build; run setup again.")
    if receipt["sha256_backend"] == "openssl-evp":
        # Keep the SONAME alias, not a patch-version file's resolved path. A distro
        # can replace libcrypto.so.3.0.x while preserving libcrypto.so.3.
        library = Path(entries["OPENSSL_CRYPTO_LIBRARY"]).absolute()
        receipt["library_path"] = str(library.parent / receipt["libcrypto_soname"])
        verify_library = library_soname(receipt["library_path"])
        if verify_library != receipt["libcrypto_soname"]:
            raise ValueError("System crypto selection changed during the build; run setup again.")
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
        cmake = shutil.which("cmake") or cmake_executable(provision, manifest["cmake"])
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
        return crypto_identity(cache_entries(root / "build"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable-sha", action="store_true")
    parser.add_argument("--cmake-cache", type=Path, help="Read ABI identity from an existing build directory")
    options, _ = parser.parse_known_args()
    identity = crypto_identity(cache_entries(options.cmake_cache)) if options.cmake_cache else discover(options.portable_sha)
    print(json.dumps(identity, sort_keys=True))
