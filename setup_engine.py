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

from paths import ROOT, WORKSPACE, MODELS
import platform_support


def fingerprint(source=ROOT):
    manifest = json.loads((Path(source) / "sources.json").read_text())
    digest = hashlib.sha256(manifest["runtime_commit"].encode())
    for path in sorted((Path(source) / "patches").glob("*.patch")):
        digest.update(path.read_bytes())
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


def cmake_command(source, build, backend, cuda_arch=None):
    command = ["-S", str(source), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
               "-DAUDIOCPP_MODEL_SET=custom", "-DAUDIOCPP_MODELS=yue2", "-DAUDIOCPP_DEPLOYMENT_BUILD=ON",
               "-DENGINE_ENABLE_METAL=" + ("ON" if backend == "metal" else "OFF"),
               "-DENGINE_ENABLE_CUDA=" + ("ON" if backend == "cuda" else "OFF"),
               "-DENGINE_ENABLE_HIP=OFF", "-DENGINE_ENABLE_VULKAN=OFF",
               "-DENGINE_ENABLE_OPENMP=OFF", "-DGGML_OPENMP=OFF", "-DENGINE_ENABLE_NATIVE_CPU=OFF"]
    if backend == "cuda":
        command.append("-DCMAKE_CUDA_ARCHITECTURES=" + (cuda_arch or "native"))
    return command


def prepare(backend, run=None, source=ROOT, download_models=True, jobs=None, cuda_arch=None, activate=True, probe=True):
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
    build_root = WORKSPACE / "engines" / (fingerprint(source) + "-" + backend)
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
    for patch in sorted((source / "patches").glob("*.patch")):
        expected = manifest["local_patches"]["patches/" + patch.name]["sha256"]
        if hashlib.sha256(patch.read_bytes()).hexdigest() != expected:
            raise ValueError("The runtime patch checksum does not match sources.json.")
        check = subprocess.run(["git", "-C", str(checkout), "apply", "--check", str(patch)], capture_output=True)
        if check.returncode == 0:
            run(["git", "-C", str(checkout), "apply", str(patch)])
        elif subprocess.run(["git", "-C", str(checkout), "apply", "--reverse", "--check", str(patch)], capture_output=True).returncode:
            raise ValueError("The engine source does not match its pinned patches.")
    cmake = shutil.which("cmake")
    if not cmake:
        tools_env = WORKSPACE / "tools"
        interpreter = tools_env / "bin/python"
        if not interpreter.exists():
            run([sys.executable, "-m", "venv", str(tools_env)])
        run([str(interpreter), "-m", "pip", "install", "cmake==" + manifest["cmake"]])
        cmake = str(tools_env / "bin/cmake")
    run([cmake, *cmake_command(checkout, build, backend, cuda_arch)])
    # Leave half the CPUs available to the studio and desktop; adjustable by the user.
    build_jobs = jobs or max(1, (platform_support.default_threads() + 1) // 2)
    run([cmake, "--build", str(build), "--parallel", str(build_jobs), "--target", "audiocpp_cli"])
    executable = build / "bin/audiocpp_cli"
    if not executable.is_file():
        raise ValueError("The build did not produce an audio.cpp executable.")
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
    parser.add_argument("--jobs", type=int)
    parser.add_argument("--cuda-arch", help="CMake CUDA architectures; default native")
    args = parser.parse_args()
    prepare(args.backend, download_models=not (args.build_only or args.compile_only), jobs=args.jobs,
            cuda_arch=args.cuda_arch, probe=not args.compile_only, activate=not args.compile_only)


if __name__ == "__main__":
    main()
