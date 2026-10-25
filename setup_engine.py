#!/usr/bin/env python3
"""Prepare pinned audio.cpp for Metal, NVIDIA CUDA, or CPU."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from paths import ROOT, WORKSPACE, MODELS
import platform_support


def cmake_executable(run, version):
    cmake = shutil.which("cmake")
    if cmake:
        return cmake
    tools_env = WORKSPACE / "tools"
    local = tools_env / "bin/cmake"
    if not local.is_file():
        interpreter = tools_env / "bin/python"
        if not interpreter.exists():
            run([sys.executable, "-m", "venv", str(tools_env)])
        run([str(interpreter), "-m", "pip", "install", "cmake==" + version])
    return str(local)


def fingerprint(source=ROOT, sha_backend=None, sha_identity=None):
    manifest = json.loads((Path(source) / "sources.json").read_text())
    digest = hashlib.sha256(manifest["runtime_commit"].encode())
    for path in sorted((Path(source) / "patches").glob("*.patch")):
        digest.update(path.read_bytes())
    if sha_backend is not None:
        digest.update(b"\0sha256_backend=" + sha_backend.encode())
    for key in ("libcrypto_soname", "openssl_version_major"):
        if sha_identity and key in sha_identity:
            digest.update(b"\0" + key.encode() + b"=" + sha_identity[key].encode())
    return digest.hexdigest()[:16]


def model_directory(manifest):
    receipt = MODELS / "manifest.json"
    if not receipt.exists() or json.loads(receipt.read_text()) == manifest:
        return MODELS
    identity = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]
    return MODELS / "sets" / identity


def prerequisites(backend):
    missing = []
    if not shutil.which("git"):
        missing.append({"name": "Git", "command": "xcode-select --install" if platform.system() == "Darwin" else "sudo apt install git"})
    if not (shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")):
        missing.append({"name": "C++ compiler", "command": "xcode-select --install" if platform.system() == "Darwin" else "sudo apt install build-essential"})
    if backend == "metal" and not (platform.system() == "Darwin" and platform.machine() == "arm64"):
        missing.append({"name": "Apple Silicon for Metal", "command": "Choose NVIDIA CUDA or CPU on this system."})
    if backend == "cuda" and not shutil.which("nvcc"):
        missing.append({"name": "NVIDIA CUDA Toolkit", "command": "Install the NVIDIA CUDA Toolkit, then add its bin directory to PATH."})
    return missing


def cmake_command(source, build, backend, cuda_arch=None, portable_sha=False):
    command = ["-S", str(source), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
               "-DAUDIOCPP_MODEL_SET=custom", "-DAUDIOCPP_MODELS=yue2", "-DAUDIOCPP_DEPLOYMENT_BUILD=ON",
               "-DENGINE_ENABLE_METAL=" + ("ON" if backend == "metal" else "OFF"),
               "-DENGINE_ENABLE_CUDA=" + ("ON" if backend == "cuda" else "OFF"),
               "-DENGINE_ENABLE_HIP=OFF", "-DENGINE_ENABLE_VULKAN=OFF",
               "-DENGINE_ENABLE_OPENMP=OFF", "-DGGML_OPENMP=OFF", "-DENGINE_ENABLE_NATIVE_CPU=OFF",
               "-DRIFF_CHECKPOINT_SYSTEM_SHA=" + ("OFF" if portable_sha else "ON")]
    if backend == "cuda":
        command.append("-DCMAKE_CUDA_ARCHITECTURES=" + (cuda_arch or "native"))
    return command


def apply_pinned_patches(checkout, source, manifest, run):
    """Validate complete patch states without touching the checkout's real index.

    Later patches can change earlier patches' context, so reversing an individual
    patch is not a valid test of an already prepared engine. A temporary index
    describes each complete prefix, including the final tree. Only a matching
    prefix may be resumed; other tracked edits are left for their owner.
    """
    patches = sorted((Path(source) / "patches").glob("*.patch"))
    for patch in patches:
        expected = manifest["local_patches"]["patches/" + patch.name]["sha256"]
        if hashlib.sha256(patch.read_bytes()).hexdigest() != expected:
            raise ValueError("The runtime patch checksum does not match sources.json.")
    with tempfile.TemporaryDirectory(prefix="riff-engine-index-") as temporary:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(temporary) / "index")}

        def git(*args):
            return subprocess.run(["git", "-C", str(checkout), *args], env=env, capture_output=True)

        def matches():
            result = git("diff", "--no-ext-diff", "--quiet")
            if result.returncode not in (0, 1):
                raise ValueError("The engine source could not be verified.")
            return result.returncode == 0

        git("read-tree", manifest["runtime_commit"]).check_returncode()
        completed = 0 if matches() else None
        for position, patch in enumerate(patches, 1):
            git("apply", "--cached", str(patch.resolve())).check_returncode()
            if matches():
                completed = position
        if completed is None:
            raise ValueError("The managed engine has local source changes. Keep them and choose a separate engine path.")
        for patch in patches[completed:]:
            run(["git", "-C", str(checkout), "apply", str(patch.resolve())])
        if not matches():
            raise ValueError("The engine source changed during setup. Its local changes have been preserved.")


def prepare(backend, run=None, source=ROOT, download_models=True, jobs=None, cuda_arch=None, activate=True, probe=True,
            configure_only=False, portable_sha=False):
    source = Path(source)
    manifest = json.loads((source / "sources.json").read_text())
    if backend not in ("metal", "cuda", "cpu"):
        raise ValueError("Choose Metal, NVIDIA CUDA, or CPU.")
    missing = prerequisites(backend)
    if missing:
        raise ValueError("; ".join(item["name"] + ": " + item["command"] for item in missing))
    if jobs is not None and (type(jobs) is not int or jobs < 1):
        raise ValueError("Build jobs must be a positive whole number.")
    if run is None:
        def run(command, cwd=None):
            print(" ".join(map(str, command)), flush=True)
            subprocess.run(command, cwd=cwd, check=True)
    cmake = cmake_executable(run, manifest["cmake"])
    sha_backend, sha_identity = None, None
    if "patches/yue2-weight-system-sha.patch" in manifest["local_patches"]:
        from scripts.checkpoint_sha_identity import discover
        sha_identity = discover(portable_sha, source, cmake=cmake)
        sha_backend = sha_identity["sha256_backend"]
        if sha_backend == "portable":
            print("System crypto is unavailable or disabled; using portable SHA-256.", flush=True)
    build_root = WORKSPACE / "engines" / (fingerprint(source, sha_backend, sha_identity) + "-" + backend)
    checkout, build = build_root / "audio.cpp", build_root / "build"
    build_root.mkdir(parents=True, exist_ok=True)
    if not (checkout / ".git").exists():
        run(["git", "init", str(checkout)])
        run(["git", "-C", str(checkout), "remote", "add", "origin", manifest["runtime_repo"]])
    revision = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], capture_output=True, text=True)
    if revision.returncode or revision.stdout.strip() != manifest["runtime_commit"]:
        if revision.returncode == 0:
            raise ValueError("The managed engine checkout changed. Select a separate engine path instead of overwriting it.")
        run(["git", "-C", str(checkout), "fetch", "--depth", "1", "origin", manifest["runtime_commit"]])
        run(["git", "-C", str(checkout), "checkout", "--detach", "FETCH_HEAD"])
    apply_pinned_patches(checkout, source, manifest, run)
    configuration = [cmake, *cmake_command(checkout, build, backend, cuda_arch, portable_sha)]
    run(configuration)
    if configure_only:
        return {"backend": backend, "source_directory": str(checkout), "build_directory": str(build),
                "cmake": cmake, "configuration": configuration}
    # Leave half the CPUs available to the studio and desktop; adjustable by the user.
    build_jobs = jobs or max(1, (platform_support.default_threads() + 1) // 2)
    run([cmake, "--build", str(build), "--parallel", str(build_jobs), "--target", "audiocpp_cli"])
    executable = build / "bin/audiocpp_cli"
    if not executable.is_file():
        raise ValueError("The build did not produce an audio.cpp executable.")
    if sha_identity is not None:
        from scripts.checkpoint_sha_identity import write_runtime_receipt
        write_runtime_receipt(executable, build, sha_identity)
    if probe:
        run([str(executable), "--list-devices"])
    value = {"backend": backend, "binary": str(executable)}
    if download_models:
        model_root = model_directory(manifest["model"])
        run([sys.executable, str(source / "fetch-models.py"), "--directory", str(model_root)])
        value.update(model_root=str(model_root), model_file="yue2-3b-q4_0.gguf", vae_file="yue2-vae-f16.gguf")
    if activate:
        platform_support.configure(value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("metal", "cuda", "cpu"), default=platform_support.default_backend())
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--compile-only", action="store_true", help="Compile without models, device probing, or activation (for CI)")
    parser.add_argument("--configure-only", action="store_true", help="Verify source and configure CMake without compiling or activating")
    parser.add_argument("--portable-sha", action="store_true", help="Build with portable SHA-256 instead of system crypto discovery")
    parser.add_argument("--jobs", type=int)
    parser.add_argument("--cuda-arch", help="CMake CUDA architectures; default native")
    args = parser.parse_args()
    configured = prepare(args.backend, download_models=not (args.build_only or args.compile_only), jobs=args.jobs,
                         cuda_arch=args.cuda_arch, probe=not args.compile_only, activate=not args.compile_only,
                         configure_only=args.configure_only, portable_sha=args.portable_sha)
    if args.configure_only:
        print(json.dumps(configured, sort_keys=True))


if __name__ == "__main__":
    main()
