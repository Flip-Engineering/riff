#!/usr/bin/env python3
"""Verify and exercise the pinned native LoRA loader with tiny real CPU tensors.

No downloaded model weights, adapters, credentials or paid requests are needed.
Requires a completed CPU engine build (e.g. setup_engine.py --backend cpu --compile-only).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def run(command, *, cwd=None, env=None):
    result = subprocess.run(list(map(str, command)), cwd=cwd, env=env, text=True,
                            capture_output=True, check=True)
    return result.stdout + result.stderr


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def cache(build, key):
    for line in (build / "CMakeCache.txt").read_text().splitlines():
        if line.startswith(key + ":"):
            return line.split("=", 1)[1]
    raise ValueError("CMake cache does not expose " + key)


def flags(build):
    entries = {}
    for line in (build / "CMakeFiles/engine_model_yue2.dir/flags.make").read_text().splitlines():
        if " = " in line:
            key, value = line.split(" = ", 1)
            entries[key] = shlex.split(value)
    return entries["CXX_DEFINES"] + entries["CXX_INCLUDES"] + entries["CXX_FLAGS"]


def verify_source(source, manifest, temporary):
    patches = sorted((ROOT / "patches").glob("*.patch"))
    if [str(path.relative_to(ROOT)) for path in patches] != sorted(manifest["local_patches"]):
        raise ValueError("The native patch set differs from sources.json")
    environment = {**os.environ, "GIT_INDEX_FILE": str(temporary / "index")}
    run(["git", "-C", source, "read-tree", manifest["runtime_commit"]], env=environment)
    for path in patches:
        expected = manifest["local_patches"][str(path.relative_to(ROOT))]
        if path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
            raise ValueError("Native patch verification failed: " + path.name)
        run(["git", "-C", source, "apply", "--cached", path], env=environment)
    changes = run(["git", "-C", source, "diff", "--no-ext-diff", "--name-only"], env=environment)
    if changes.strip():
        raise ValueError("Native source differs from the pinned patch set: " + changes)
    return patches, environment


def metadata_fixture(directory):
    sidecars = directory / "sidecars"
    sidecars.mkdir(parents=True)
    config = {"hidden_size": 1, "num_hidden_layers": 1, "num_attention_heads": 1,
              "num_key_value_heads": 1, "head_dim": 1, "intermediate_size": 1,
              "vocab_size": 1, "max_position_embeddings": 1}
    (sidecars / "yue2-model-config.json").write_text(json.dumps(config))
    (sidecars / "yue2-vae-config.json").write_text("{}\n")
    (sidecars / "yue2-qwen.tiktoken").write_text("metadata fixture; not a tokenizer\n")


def verify_capabilities(binary, fixture, expected):
    for mode in ("help", "inspect"):
        output = run([binary, "--family", "yue2", "--model", fixture, "--" + mode])
        entries = [tuple(line.split("=", 1)) for line in output.splitlines()
                   if "=" in line and line.split("=", 1)[0] in expected]
        if len(entries) != len(expected) or dict(entries) != expected:
            raise ValueError("Native LoRA capability receipt is missing or incompatible")
        if mode == "help":
            for option in ("yue2.ar_lora", "yue2.ar_lora_scale", "yue2.nar_lora", "yue2.nar_lora_scale"):
                if option not in output:
                    raise ValueError("Native CLI does not expose session option: " + option)
    # The actual production CLI must reject mutable per-job adapter selection
    # before loading any GGUF; an old warm session must never silently ignore it.
    result = subprocess.run([str(binary), "--task", "gen", "--family", "yue2", "--model", str(fixture),
                             "--backend", "cpu", "--jobs", "-"], text=True,
                            input=json.dumps({"id": "adapter-scope", "options": {"yue2.ar_lora_scale": "0.5"}}) + "\n",
                            capture_output=True, check=True)
    output = result.stdout + result.stderr
    if "@@riff-job-end adapter-scope status=error" not in output or "LoRA paths and strengths are session options" not in output:
        raise ValueError("Warm engine did not reject request-scoped adapter mutation: " + output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, help="Completed audio.cpp build directory; defaults to configured engine")
    parser.add_argument("--output", type=Path, help="New evidence directory (defaults to a temporary directory)")
    args = parser.parse_args()
    if args.build is None:
        sys.path.insert(0, str(ROOT))
        import platform_support
        build = Path(platform_support.settings()["binary"]).parent.parent.resolve()
    else:
        build = args.build.resolve()
    output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="riff-native-lora-"))
    if args.output:
        output.mkdir(parents=True, exist_ok=False)
    source = Path(cache(build, "CMAKE_HOME_DIRECTORY"))
    manifest = json.loads((ROOT / "sources.json").read_text())
    with tempfile.TemporaryDirectory(prefix="riff-lora-index-") as name:
        patches, environment = verify_source(source, manifest, Path(name))
        binary = build / "bin/audiocpp_cli"
        fixture = output / "metadata-model"
        metadata_fixture(fixture)
        verify_capabilities(binary, fixture, manifest["native_capabilities"])
        compiler = cache(build, "CMAKE_CXX_COMPILER")
        production_sources = [source / "src/framework/assets/weight_adapter.cpp",
                              source / "src/framework/assets/weight_adapter_source.cpp",
                              source / "src/models/yue2/lora.cpp"]
        test_source = ROOT / "tests/native_lora/lora_test.cpp"
        link_args = shlex.split((build / "CMakeFiles/audiocpp_cli.dir/link.txt").read_text())
        linker, link_args = link_args[0], link_args[1:]
        position = link_args.index("-o")
        del link_args[position:position + 2]
        libraries = [value for value in link_args if not value.endswith(".o")]
        tracked = [binary, test_source, Path(__file__), ROOT / "sources.json", *patches, *production_sources,
                   source / "include/engine/framework/assets/weight_adapter.h", source / "include/engine/models/yue2/assets.h",
                   build / "CMakeCache.txt", build / "CMakeFiles/engine_model_yue2.dir/flags.make",
                   build / "CMakeFiles/audiocpp_cli.dir/link.txt"]
        tracked += [(build / value).resolve() for value in libraries if value.endswith((".a", ".so", ".dylib"))]
        before = {str(path): digest(path) for path in tracked}
        objects = []
        for number, path in enumerate([*production_sources, test_source]):
            target = output / f"check-{number}.o"
            log = run([compiler, *flags(build), "-c", path, "-o", target], cwd=build)
            (output / f"compile-{number}.log").write_text(log)
            objects.append(target)
        executable = output / "native-lora-test"
        (output / "link.log").write_text(run([linker, *objects, *libraries, "-o", executable], cwd=build))
        result = run([executable, output / "fixtures"], cwd=build)
        (output / "numerical.log").write_text(result)
        numerical = json.loads(next(line for line in reversed(result.splitlines()) if line.startswith("{\"passed\"")))
        if numerical["failed"] or numerical["passed"] != 44 or numerical["model_weights_loaded"]:
            raise ValueError("Native LoRA checks were incomplete")
        changes = run(["git", "-C", source, "diff", "--no-ext-diff", "--name-only"], env=environment)
        if changes.strip() or {str(path): digest(path) for path in tracked} != before:
            raise ValueError("A native source or test input changed during verification")
        receipt = {"runtime_commit": manifest["runtime_commit"], "numerical": numerical,
                   "capabilities": manifest["native_capabilities"], "production_binary_sha256": digest(binary),
                   "test_binary_sha256": digest(executable), "input_sha256": before,
                   "limits": "Synthetic CPU tensors only; no neural audio, listening test, GPU or full-model memory measurement."}
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps({"output": str(output), **numerical}, sort_keys=True))


if __name__ == "__main__":
    main()
