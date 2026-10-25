#!/usr/bin/env python3
"""Qualify pinned YuE2 baseline/zero/nonzero LoRA inference on an installed backend.

Downloads only the pinned 117.5 MB CC BY-NC adapter through Riff's importer.
Base weights must already be installed; no model download or numeric parity claim.
"""
import argparse
import array
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
import threading
import time
import wave

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from loras import LoRALibrary
from platform_support import ProcessMemory
from setup_engine import fingerprint

ADAPTER = {
    "repo": "becausereasons/yue2-trbdr-folk-troubadour",
    "revision": "078b19e52baebaa71463ea94afac5ce13abfe29a",
    "file": "trbdr_broadside.safetensors", "bytes": 117500816,
    "sha256": "68e3aca03e7aa7ddf8a2ee5cf46ce68c2c18fc9fe432cc60b838b9f0e9b8171f",
    "license": "cc-by-nc-4.0", "purpose": "Noncommercial inference qualification only",
    "normalized_sha256": {
        "ar": "2e505269c924acb57fc2d43349d5699365398e86c42ac4f9bcb45673f9e84e75",
        "nar": "5149299f325cd50bc4f996a2c611e39cae96eddc5bbdcb4da020af927ab40b41",
    },
}
METADATA_ARENAS = ("model_weight_context", "vae_weight_context", "ar_prefill_graph_arena",
                   "ar_decode_graph_arena", "nar_graph_arena", "vae_graph_arena")


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def verify_models(directory, pinned):
    result = {}
    for name, expected in pinned["files"].items():
        path = directory / name
        if not path.is_file() or path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
            raise ValueError("Pinned model size/SHA-256 verification failed: " + name)
        result[name] = dict(expected)
    return {"repo": pinned["repo"], "revision": pinned["revision"], "files": result}


def verify_adapter(library, item):
    expected_source = {"type": "huggingface", "repo": ADAPTER["repo"], "revision": ADAPTER["revision"], "files": [ADAPTER["file"]]}
    if item["source"] != expected_source or item["license"] != ADAPTER["license"] or set(item["stages"]) != {"ar", "nar"}:
        raise ValueError("Installed adapter provenance/license differs from the qualification pin")
    if len(item["files"]) != 2:
        raise ValueError("Qualification needs the pinned combined AR/NAR adapter")
    for entry in item["files"]:
        if entry["sha256"] != ADAPTER["sha256"] or entry["bytes"] != ADAPTER["bytes"] or entry["engine_sha256"] != ADAPTER["normalized_sha256"][entry["stage"]]:
            raise ValueError("Installed adapter original/normalized hashes differ from the qualification pin")
    return library.resolve([{"id": item["id"], "ar_scale": 1, "nar_scale": 1}])


def import_adapter(library):
    for item in library.list():
        if not item.get("error") and item.get("source", {}).get("repo") == ADAPTER["repo"] and item["source"].get("revision") == ADAPTER["revision"]:
            verify_adapter(library, item)
            return item
    print(f"Importing {ADAPTER['file']}: {ADAPTER['bytes']} bytes, {ADAPTER['license']} (qualification only)", flush=True)
    # Pin metadata before Riff starts a download, then require our independently
    # pinned original and normalized hashes after its full import validation.
    model = library.hub.model(ADAPTER["repo"], ADAPTER["revision"])
    remote = next((entry for entry in model["siblings"] if entry["rfilename"] == ADAPTER["file"]), {})
    if remote.get("size") != ADAPTER["bytes"] or remote.get("lfs", {}).get("sha256") != ADAPTER["sha256"]:
        raise ValueError("Remote adapter size/hash differs from the qualification pin")
    if model.get("cardData", {}).get("license") != ADAPTER["license"]:
        raise ValueError("Remote adapter license differs from the qualification pin")
    task = library.hub_install({"repo": ADAPTER["repo"], "revision": ADAPTER["revision"], "files": [ADAPTER["file"]]})
    try:
        library.threads[task["id"]].join()
    except BaseException:
        library.events[task["id"]].set()
        library.threads[task["id"]].join()
        raise
    task = library.task(task["id"])
    if task["status"] != "completed":
        raise ValueError("Adapter import failed: " + str(task.get("error")))
    item = task["result"]
    verify_adapter(library, item)
    return item


def parse_timings(text):
    timings = {}
    for line in text.splitlines():
        match = re.search(r"\[TIMING[^\]]*\]\s+(\S+)\s+(\S+)\s*$", line)
        if not match:
            continue
        name, raw = match.groups()
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            raise ValueError("Invalid native timing: " + line)
        timings.setdefault(name, []).append(value)
    if not timings:
        raise ValueError("Native process emitted no [TIMING] records")
    return timings


def device_memory(text, backend):
    # The pinned ggml Metal allocator logs MTLDevice.currentAllocatedSize in
    # rounded MiB at allocation events. Do not sum individual buffer sizes.
    if backend == "metal":
        values = [float(value) for value in re.findall(
            r"ggml_metal_log_allocated_size: allocated buffer, size\s*=\s*[\d.]+ MiB,\s*\(\s*([\d.]+)", text)]
        return {"peak_bytes": round(max(values) * 1024 ** 2) if values else None,
                "status": "observed_at_logged_allocations" if values else "unavailable",
                "current_allocated_mib_observations": values,
                "note": "Metal currentAllocatedSize at logged allocation events, rounded MiB; not continuous. "
                        "Unified allocations also contribute to process footprint. Missing logs do not mean zero."}
    return {"peak_bytes": None, "status": "not_applicable" if backend == "cpu" else "unavailable",
            "note": "CPU has no separate device allocation; other backend device telemetry is not collected here."}


def command_for(binary, models, backend, recipe, output, scales=None, stage_paths=None):
    command = [str(binary), "--task", "gen", "--family", "yue2", "--model", str(models), "--backend", backend,
               "--threads", str(recipe["threads"]), "--seed", str(recipe["seed"]), "--lyrics", recipe["lyrics"],
               "--out", str(output.with_suffix(".wav")), "--log", "--metrics"]
    options = {"yue2.model_gguf": "yue2-3b-q4_0.gguf", "yue2.vae_gguf": "yue2-vae-f16.gguf"}
    options.update({"yue2." + key + "_mb": value for key, value in recipe.get("metadata_arenas_mib", {}).items()})
    if scales is not None:
        for stage in ("ar", "nar"):
            # Deliberately send paths even at scale zero: application-level
            # filtering must not turn the native zero-bypass test into baseline.
            options[f"yue2.{stage}_lora"] = str(stage_paths[stage + "_lora"])
            options[f"yue2.{stage}_lora_scale"] = scales[stage]
    for key, value in options.items():
        command += ["--session-option", f"{key}={value}"]
    requests = {"cot": "off", "cfg_scale": 1, "semantic_temperature": 1, "style": recipe["style"],
                "semantic_min_tokens": recipe["semantic_tokens"], "semantic_max_tokens": recipe["semantic_tokens"],
                "num_inference_steps": recipe["steps"], "ode_method": "midpoint",
                "semantic_codes_out": str(output.with_suffix(".codes.i32")),
                "acoustic_latents_out": str(output.with_suffix(".acoustic"))}
    for key, value in requests.items():
        command += ["--request-option", f"{key}={value}"]
    return command


def inspect_outputs(output, models, binary_sha256, requested_tokens):
    wav, codes, acoustic = (output.with_suffix(suffix) for suffix in (".wav", ".codes.i32", ".acoustic"))
    raw = codes.read_bytes()
    if len(raw) != requested_tokens * 4:
        raise ValueError("Native output does not contain the requested semantic token count")
    tokens = struct.unpack("<" + "i" * requested_tokens, raw)
    if any(not 0 <= token < 32768 for token in tokens):
        raise ValueError("Native semantic output contains invalid codec IDs")
    with wave.open(str(wav), "rb") as stream:
        frames, rate, channels, width = stream.getnframes(), stream.getframerate(), stream.getnchannels(), stream.getsampwidth()
        if frames <= 0 or rate != 48000 or channels != 2 or width != 2 or stream.getcomptype() != "NONE":
            raise ValueError("Native output is not nonempty stereo 48 kHz PCM16")
        pcm = stream.readframes(frames)
    if not any(pcm):
        raise ValueError("Native audio is entirely zero")
    content = acoustic.read_bytes()
    header, payload = content[:512], content[512:]
    if len(header) != 512 or header[:8] != b"RIFFYAC1" or struct.unpack_from("<5I", header, 8) != (1, 512, 1, 1, 1):
        raise ValueError("Native acoustic checkpoint is incomplete or incompatible")
    count, dimension, size = struct.unpack_from("<3Q", header, 32)
    if count != requested_tokens or dimension <= 0 or size != count * dimension * 4 or len(payload) != size:
        raise ValueError("Native acoustic checkpoint shape/length differs")
    for offset, expected in ((192, models["files"]["yue2-3b-q4_0.gguf"]["sha256"]),
                             (224, models["files"]["yue2-vae-f16.gguf"]["sha256"]), (448, binary_sha256)):
        if header[offset:offset + 32].hex() != expected:
            raise ValueError("Native acoustic checkpoint provenance differs")
    if hashlib.sha256(header[:480]).digest() != header[480:512] or hashlib.sha256(payload).digest() != header[160:192]:
        raise ValueError("Native acoustic checkpoint checksum differs")
    floats = array.array("f", payload)
    if sys.byteorder != "little":
        floats.byteswap()
    if any(not math.isfinite(value) for value in floats):
        raise ValueError("Native acoustic checkpoint contains nonfinite values")
    return {"wav_sha256": digest(wav), "pcm_sha256": hashlib.sha256(pcm).hexdigest(), "audio_frames": frames,
            "audio_seconds": frames / rate, "sample_rate": rate, "channels": channels,
            "semantic_sha256": digest(codes), "semantic_tokens": count,
            "acoustic_sha256": digest(acoustic), "acoustic_payload_sha256": hashlib.sha256(payload).hexdigest(),
            "acoustic_frames": count, "acoustic_dimensions": dimension, "nonfinite_acoustic_values": 0}


def execute(command, output, backend):
    monitor = ProcessMemory()
    done = threading.Event()
    samples, errors = [], []
    started = time.monotonic()
    with output.with_suffix(".log").open("w") as log:
        child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                 env={**os.environ, "OMP_NUM_THREADS": command[command.index("--threads") + 1],
                                      "OPENBLAS_NUM_THREADS": command[command.index("--threads") + 1]})
        def sample():
            while not done.is_set():
                try:
                    current, peak = monitor.read(child.pid)
                    if current or peak:
                        samples.append({"seconds": time.monotonic() - started, "current_bytes": current, "peak_bytes": peak})
                except Exception as error:
                    errors.append(str(error))
                done.wait(.5)
        observer = threading.Thread(target=sample, daemon=True)
        observer.start()
        usage = None
        try:
            if hasattr(os, "wait4"):
                _, status, usage = os.wait4(child.pid, 0)
                child.returncode = os.waitstatus_to_exitcode(status)
            else:
                child.wait()
        except BaseException:
            child.terminate()
            child.wait()
            raise
        finally:
            done.set()
            observer.join()
    rss = usage.ru_maxrss * (1 if platform.system() == "Darwin" else 1024) if usage else None
    text = output.with_suffix(".log").read_text(errors="replace")
    return {"exit_code": child.returncode, "wall_seconds": time.monotonic() - started,
            "peak_rss_bytes": rss, "rss_source": "Per-child wait4/rusage" if usage else "Unavailable on this platform",
            "sampled_peak_process_memory_bytes": max((s["peak_bytes"] for s in samples), default=None),
            "process_memory_note": monitor.note, "memory_samples": samples, "memory_sampling_errors": errors,
            "device_memory": device_memory(text, backend),
            "native_memory_lines": [line for line in text.splitlines() if re.search(r"memory|buffer.*(?:MiB|MB|bytes)", line, re.I)],
            "timing_lines": [line for line in text.splitlines() if "[TIMING" in line], "command": command}


def compare_runs(runs):
    base, zero, adapted, repeat = (run["outputs"] for run in runs)
    checks = {}
    for key in ("pcm_sha256", "semantic_sha256", "acoustic_payload_sha256"):
        checks["zero_exact_" + key] = base[key] == zero[key]
        checks["adapted_repeat_exact_" + key] = adapted[key] == repeat[key]
    checks["nonzero_audio_differs"] = adapted["pcm_sha256"] != base["pcm_sha256"]
    checks["nonzero_semantics_differ"] = adapted["semantic_sha256"] != base["semantic_sha256"]
    checks["nonzero_acoustics_differ"] = adapted["acoustic_payload_sha256"] != base["acoustic_payload_sha256"]
    return checks


def normalize_paths(value, roots):
    if isinstance(value, str):
        for path, name in sorted(roots.items(), key=lambda item: -len(str(item[0]))):
            value = value.replace(str(path), name)
        return value
    if isinstance(value, list):
        return [normalize_paths(item, roots) for item in value]
    if isinstance(value, dict):
        return {key: normalize_paths(item, roots) for key, item in value.items()}
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("cpu", "metal", "cuda"), required=True)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New output directory; weights remain uncommitted")
    parser.add_argument("--adapter-library", type=Path)
    parser.add_argument("--build-receipt", type=Path, default=ROOT / "native-cache-receipts/build.json")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--semantic-tokens", type=int, default=25)
    parser.add_argument("--steps", type=int, default=2)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--ar-strength", type=float, default=.75)
    parser.add_argument("--nar-strength", type=float, default=.5)
    parser.add_argument("--metadata-arena", action="append", default=[], metavar="NAME=MIB",
                        help="Explicit native metadata arena for this recipe; repeat to override runtime defaults")
    args = parser.parse_args()
    if any(n <= 0 for n in (args.threads, args.semantic_tokens, args.steps)) or not 0 <= args.seed < 2 ** 63:
        parser.error("Use positive threads/token/step counts and a runtime-compatible seed")
    if any(not math.isfinite(n) or n == 0 for n in (args.ar_strength, args.nar_strength)):
        parser.error("Qualification requires finite nonzero AR and NAR strengths")
    arenas = {}
    for option in args.metadata_arena:
        key, separator, value = option.partition("=")
        if not separator or key not in METADATA_ARENAS or not value.isdecimal() or int(value) <= 0 or key in arenas:
            parser.error("Use each supported metadata arena name once with a positive MiB value")
        arenas[key] = int(value)
    if args.binary is None:
        from platform_support import settings
        args.binary = Path(settings()["binary"])
    binary, models, output = args.binary.resolve(), args.models.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    library = LoRALibrary(args.adapter_library.resolve() if args.adapter_library else output / "adapter-library")
    recipe = {"seed": args.seed, "threads": args.threads, "semantic_tokens": args.semantic_tokens, "steps": args.steps,
              "lyrics": "[Instrumental]", "style": "trbdr, acoustic folk, guitar, instrumental", "cot": "off", "guidance": 1,
              "temperature": 1, "solver": "midpoint", "metadata_arenas_mib": arenas}
    roots = {binary: "<engine>", models: "<models>", output: "<output>", library.root.resolve(): "<adapter-library>", ROOT: "<checkout>"}
    receipt = {"schema": "riff.lora-inference.v1", "status": "running", "backend": args.backend, "recipe": recipe,
               "adapter_pin": ADAPTER, "runs": [], "limits": [
                   "Cold standalone sessions; source page cache and shared host load are not controlled.",
                   "One short fixed recipe is a pipeline smoke, not listening quality or full-song performance.",
                   "No BF16-first or graph-level reference parity is established.",
                   "Device memory is unavailable unless identified explicitly; missing is not zero."]}
    def save():
        (output / "receipt.json").write_text(json.dumps(normalize_paths(receipt, roots), indent=2) + "\n")
    save()
    try:
        manifest = json.loads((ROOT / "sources.json").read_text())
        receipt["source_sha"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        receipt["script_sha256"] = digest(Path(__file__))
        build_receipt = json.loads(args.build_receipt.read_text())
        sha_backend = build_receipt.get("sha256_backend")
        if sha_backend not in ("portable", "commoncrypto", "openssl-evp"):
            raise ValueError("Build receipt has no supported SHA-256 backend")
        native_key = fingerprint(sha_backend=sha_backend, sha_identity=build_receipt.get("sha256_identity"))
        receipt["engine"] = {"binary_sha256": digest(binary), "key": native_key + "-" + args.backend,
                             "sha256_backend": sha_backend,
                             "runtime_commit": manifest["runtime_commit"], "patches": manifest["local_patches"]}
        if build_receipt.get("backend") != args.backend or build_receipt.get("build_exit_status") != 0 or build_receipt.get("native_fingerprint") != native_key:
            raise ValueError("Build receipt does not match the requested pinned native engine/backend")
        receipt["engine"]["build_receipt"] = build_receipt
        receipt["engine"]["build_receipt_sha256"] = digest(args.build_receipt)
        receipt["models"] = verify_models(models, manifest["model"])
        receipt["environment"] = {"system": platform.system(), "release": platform.release(), "architecture": platform.machine(),
                                  "load_average_start": os.getloadavg() if hasattr(os, "getloadavg") else None}
        devices = subprocess.run([str(binary), "--list-devices"], text=True, capture_output=True, check=True)
        (output / "devices.log").write_text(devices.stdout + devices.stderr)
        receipt["engine"]["devices"] = devices.stdout + devices.stderr
        registry = {"cpu": "CPU", "metal": "MTL", "cuda": "CUDA"}[args.backend]
        if not re.search(r"^" + registry + r":\d+", receipt["engine"]["devices"], re.MULTILINE):
            raise ValueError("Requested backend has no enumerated native device: " + args.backend)
        version = subprocess.run([str(binary), "--version"], text=True, capture_output=True, check=True)
        receipt["engine"]["version"] = version.stdout + version.stderr
        item = import_adapter(library)
        stage_paths = verify_adapter(library, item)
        receipt["adapter"] = {"id": item["id"], "source": item["source"], "license": item["license"],
            "files": [{**{key: entry[key] for key in ("stage", "original_name", "sha256", "bytes", "engine_sha256",
                "engine_bytes", "rank", "format", "adapter_bytes", "merge_scratch_bytes")},
                "target_count": len(entry["targets"])} for entry in item["files"]]}
        cases = [("baseline", None), ("zero", {"ar": 0, "nar": 0}),
                 ("adapted", {"ar": args.ar_strength, "nar": args.nar_strength}),
                 ("adapted-repeat", {"ar": args.ar_strength, "nar": args.nar_strength})]
        for name, scales in cases:
            verify_models(models, manifest["model"])
            selected = library.resolve([{"id": item["id"], "ar_scale": scales["ar"], "nar_scale": scales["nar"]}]) if scales else None
            prefix = output / name
            command = command_for(binary, models, args.backend, recipe, prefix, scales, stage_paths)
            run = {"name": name, "strengths": scales, "adapter_provenance": selected["loras"] if selected else []}
            receipt["runs"].append(run)
            save()
            print(f"Rendering {name} on {args.backend}, {args.threads} threads", flush=True)
            run.update(execute(command, prefix, args.backend))
            save()
            if run["exit_code"]:
                raise ValueError(f"Native {name} failed; see {name}.log")
            if digest(binary) != receipt["engine"]["binary_sha256"]:
                raise ValueError("Engine changed during inference")
            if selected:
                library.resolve_saved(selected["loras"])
            text = prefix.with_suffix(".log").read_text(errors="replace")
            run["timings"] = parse_timings(text)
            if scales and any(scales.values()):
                for stage in ("ar", "nar"):
                    if not re.search(r"yue2\." + stage + r"_lora:.*projections=[1-9]\d*", text):
                        raise ValueError("Native adapter binding was not confirmed for " + stage)
            run["outputs"] = inspect_outputs(prefix, receipt["models"], receipt["engine"]["binary_sha256"], args.semantic_tokens)
            run["ar_elapsed_ms_per_output_token"] = {key: sum(values) / run["outputs"]["semantic_tokens"]
                for key, values in run["timings"].items() if key.startswith("yue2.ar.") and key.endswith("_ms")}
            run["per_token_note"] = "AR elapsed stage totals divided by completed semantic tokens; load/prefill keys are amortized, not steady-state decode cost. Stages overlap/nest and must not be summed."
            save()
        receipt["checks"] = compare_runs(receipt["runs"])
        if not all(receipt["checks"].values()):
            raise ValueError("Inference equality/difference checks failed: " + json.dumps(receipt["checks"]))
        verify_models(models, manifest["model"])
        receipt["models_reverified_after_inference"] = True
        receipt["status"] = "passed"
    except BaseException as error:
        receipt["status"] = "failed"
        receipt["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        save()
    print(json.dumps({"status": receipt["status"], "receipt": str(output / "receipt.json"), "checks": receipt["checks"]}), flush=True)


if __name__ == "__main__":
    main()
