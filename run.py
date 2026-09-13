#!/usr/bin/python3
"""Run one YuE2 generation, then release the model; no Python ML packages."""
import argparse
import ctypes
from datetime import datetime
import json
import hashlib
from functools import lru_cache
import math
from pathlib import Path
import resource
import subprocess
import time
import wave
from inspiration import inspire
from paths import ROOT, WORKSPACE, MODELS
import platform_support as platform_runtime

BINARY = Path(platform_runtime.settings()["binary"])
GIB = 1024 ** 3


class RusageInfoV4(ctypes.Structure):
    # macOS SDK sys/resource.h: rusage_info_v4, queried through libproc.
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "user_time system_time pkg_idle_wkups interrupt_wkups pageins "
            "wired_size resident_size phys_footprint proc_start_abstime "
            "proc_exit_abstime child_user_time child_system_time "
            "child_pkg_idle_wkups child_interrupt_wkups child_pageins "
            "child_elapsed_abstime diskio_bytesread diskio_byteswritten "
            "cpu_time_qos_default cpu_time_qos_maintenance cpu_time_qos_background "
            "cpu_time_qos_utility cpu_time_qos_legacy cpu_time_qos_user_initiated "
            "cpu_time_qos_user_interactive billed_system_time serviced_system_time "
            "logical_writes lifetime_max_phys_footprint instructions cycles "
            "billed_energy serviced_energy interval_max_phys_footprint runnable_time"
        ).split()
    ]


def sysctl_int(name):
    return int(subprocess.check_output(["/usr/sbin/sysctl", "-n", name], text=True))


def build_command(*, lyrics, style, max_seconds, steps, cot, seed, threads, output,
                  backend=None, abc="", mode="lyrics", cfg_scale=1., temperature=1., refinement=None, render_mode="music",
                  performance_file=None, semantic_only=False, solver="midpoint", score_file=None,
                  acoustic_out=None, acoustic_only=False):
    """Shared native invocation for the command line and Riff studio."""
    if solver not in ("midpoint", "ab2"):
        raise ValueError("Choose midpoint or ab2 for acoustic synthesis.")
    if score_file and (abc or cot == "off"):
        raise ValueError("Use a saved score with melody or full planning and an empty ABC input.")
    settings = platform_runtime.settings()
    config_path = Path(settings["model_root"]) / "sidecars/yue2-vae-config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    token_rate = config.get("sample_rate", 48000) / config.get("downsampling_ratio", 1920)
    token_limit = max(1, math.floor(max_seconds * token_rate))
    # An explicit instrumental request carries its own cue; Free play remains
    # unconditioned. Preserve any artist-supplied text, including custom cues.
    native_lyrics = "[Instrumental]" if mode == "instrumental" and not lyrics else lyrics
    command = [
        settings["binary"], "--task", "gen", "--family", "yue2", "--model", settings["model_root"],
        "--backend", backend or settings["backend"], "--device", str(settings["device"]),
        "--threads", str(threads), "--lyrics", native_lyrics,
        "--session-option", "yue2.model_gguf=" + settings["model_file"],
        "--session-option", "yue2.vae_gguf=" + settings["vae_file"],
        "--request-option", "cot=" + cot,
        "--request-option", f"cfg_scale={cfg_scale}",
        "--request-option", f"semantic_temperature={temperature}",
        "--request-option", f"semantic_max_tokens={token_limit}",
        "--request-option", f"semantic_min_tokens={min(200, token_limit)}",
        "--request-option", f"num_inference_steps={steps}",
        "--request-option", "ode_method=" + solver,
        "--seed", str(seed), "--out", str(output), "--log", "--metrics",
    ]
    if style:
        command += ["--request-option", "style=" + style]
    if abc:
        command += ["--request-option", "abc=" + abc]
    if cot != "off":
        command += ["--request-option", "score_tokens_out=" + str(Path(output).with_suffix(".plan.json"))]
    if render_mode == "plan":
        command += ["--request-option", "plan_only=true"]
    else:
        command += ["--request-option", "semantic_codes_out=" + str(Path(output).with_suffix(".codes.i32"))]
    if performance_file:
        command += ["--request-option", "semantic_codes_file=" + str(performance_file)]
    if score_file:
        command += ["--request-option", "score_tokens_file=" + str(score_file)]
    if semantic_only:
        command += ["--request-option", "semantic_only=true"]
    if acoustic_out:
        if not engine_capabilities(settings).get("acoustic_checkpoint"):
            raise ValueError("Update the selected engine to save sound synthesis.")
        command += ["--request-option", "acoustic_latents_out=" + str(acoustic_out)]
    if acoustic_only:
        if not acoustic_out:
            raise ValueError("Saving sound synthesis needs an output location.")
        command += ["--request-option", "acoustic_only=true"]
    from model_options import validate
    for key, value in validate(refinement or {}, max_seconds).items():
        command += ["--request-option", f"{key}={value}"]
    return command


def file_identity(path):
    info = Path(path).stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@lru_cache(maxsize=32)
def _file_digest(path, identity):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if file_identity(path) != identity:
        raise ValueError("A selected model file changed while its identity was being read.")
    return digest.hexdigest()


def file_digest(path):
    path = str(Path(path).resolve())
    return _file_digest(path, file_identity(path))


@lru_cache(maxsize=16)
def _engine_capabilities(binary, model_root, identity):
    try:
        result = subprocess.run([binary, "--family", "yue2", "--model", model_root, "--help"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return {"exact_score_replay": False}
    metadata = {}
    if result.returncode == 0 and file_identity(binary) == identity:
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.strip().split("=", 1)
                if key.startswith(("feature.yue2.", "format.yue2.")):
                    metadata[key] = value
    exact = (metadata.get("feature.yue2.score_tokens") == "1" and
             metadata.get("format.yue2.score_tokens") == "riff.yue2.score-tokens.v1" and
             metadata.get("format.yue2.prefix") == "riff.yue2.prefix.v1")
    acoustic = (metadata.get("feature.yue2.acoustic_checkpoint") == "1" and
                metadata.get("feature.yue2.acoustic_decode") == "1" and
                metadata.get("format.yue2.acoustic") == "riff.yue2.acoustic.v1")
    return {"exact_score_replay": exact, "acoustic_checkpoint": acoustic, "score_format": metadata.get("format.yue2.score_tokens"),
            "prefix_contract": metadata.get("format.yue2.prefix")}


def engine_capabilities(settings=None):
    settings = settings or platform_runtime.settings()
    try:
        binary = str(Path(settings["binary"]).resolve())
        return dict(_engine_capabilities(binary, str(Path(settings["model_root"]).resolve()), file_identity(binary)))
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"exact_score_replay": False}


def score_contract(settings=None):
    settings = settings or platform_runtime.settings()
    capability = engine_capabilities(settings)
    if not capability["exact_score_replay"]:
        raise ValueError("Update the selected music engine to reuse saved scores. Your score is kept.")
    vocabulary = Path(settings["model_root"]) / "sidecars/yue2-qwen.tiktoken"
    return {"format": capability["score_format"], "prefix_contract": capability["prefix_contract"],
            "tokenizer_sha256": file_digest(vocabulary)}


def decoder_contract(settings=None):
    settings = settings or platform_runtime.settings()
    if not engine_capabilities(settings).get("acoustic_checkpoint"):
        raise ValueError("Update the selected engine to finish saved sound synthesis.")
    model = Path(settings["model_root"])
    config = json.loads((model / "sidecars/yue2-vae-config.json").read_text())
    # Match the pinned native VAE defaults. Main-model files are not consulted.
    values = {"sample_rate": config.get("sample_rate", 48000),
              "channels": config.get("audio_channels", 2), "latent_dim": config.get("latent_dim", 64),
              "encoder_latent_dim": 128,
              "downsampling_ratio": config.get("downsampling_ratio", 1920)}
    if any(type(value) is not int or not 0 < value <= 2**63-1 for value in values.values()):
        raise ValueError("The selected audio decoder has invalid dimensions.")
    if any(values[key] > 2**31-1 for key in ("sample_rate", "channels")):
        raise ValueError("The selected audio decoder has invalid dimensions.")
    return {"sha256": file_digest(model / settings["vae_file"]), **values}


def decoder_schema():
    return {"type": "object", "additionalProperties": False, "properties": {
        "core_frames": {"type": "integer", "minimum": 1, "maximum": 2**63-1,
                        "description": "Audio frames per decoder tile. Omit to retain the captured value."},
        "halo_frames": {"type": "integer", "minimum": 0, "maximum": 2**63-1,
                        "description": "Neighboring frames around each tile. Omit to retain the captured value."},
        "storage": {"type": "integer", "enum": list(range(7)),
                    "description": "Weight storage: 0 native, 1 F32, 2 F16, 3 BF16, 4 Q8_0, 5 Q4_0, 6 Q4_K. Omit to retain captured storage; small kernels can remain F32."}}}


def decoder_options(value):
    if not isinstance(value, dict) or not set(value) <= {"core_frames", "halo_frames", "storage"}:
        raise ValueError("Choose supported audio decoder controls.")
    for key, item in value.items():
        lower, upper = (0, 6) if key == "storage" else (1 if key == "core_frames" else 0, 2**63-1)
        if type(item) is not int or not lower <= item <= upper:
            raise ValueError("Decoder controls must be whole numbers within the supported range.")
    return dict(value)


def build_decode_command(acoustic_file, output, decoder=None):
    settings = platform_runtime.settings()
    decoder_contract(settings)
    values = decoder_options({} if decoder is None else decoder)
    command = [settings["binary"], "--task", "gen", "--family", "yue2", "--model", settings["model_root"],
               "--backend", settings["backend"], "--device", str(settings["device"]),
               "--threads", str(settings["threads"]), "--session-option", "yue2.vae_gguf=" + settings["vae_file"],
               "--request-option", "acoustic_latents_file=" + str(acoustic_file),
               "--out", str(output), "--log", "--metrics"]
    for key in ("core_frames", "halo_frames"):
        if key in values:
            command += ["--request-option", f"acoustic_decode_{key}={values[key]}"]
    if "storage" in values:
        storage = ("native", "f32", "f16", "bf16", "q8_0", "q4_0", "q4_k")[values["storage"]]
        command += ["--session-option", "yue2.vae_weight_type=" + storage]
    return command


def require_solver(solver, binary):
    """An older/custom engine must not silently ignore an explicit AB2 request."""
    if solver == "ab2":
        result = subprocess.run([str(binary), "--family", "yue2", "--model", platform_runtime.settings()["model_root"], "--help"],
                                capture_output=True, text=True, timeout=10)
        if result.returncode or "ode_method" not in result.stdout or "ab2" not in result.stdout:
            raise ValueError("Update the music engine in Studio settings to use multistep synthesis.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lyrics", type=Path, default=ROOT / "lyrics.txt", help="UTF-8 lyrics file")
    parser.add_argument("--style", help="Sound description; an instrumental/surprise run can choose one")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--free", action="store_true", help="Generate with optional style and no supplied lyrics")
    modes.add_argument("--instrumental", action="store_true", help="Experimental instrumental; no lyrics required")
    modes.add_argument("--surprise", action="store_true", help="Generate original phrase-bank lyrics and a musical direction locally")
    parser.add_argument("--max-seconds", type=float, default=30, help="Preview duration ceiling (default 30); may cut off a song")
    parser.add_argument("--steps", type=int, default=32, help="Acoustic solver steps (32 upstream default; 8 for a quick preview)")
    parser.add_argument("--solver", choices=("midpoint", "ab2"), default="midpoint", help="Acoustic integration method; ab2 reuses previous velocity estimates for fewer model passes")
    parser.add_argument("--guidance", type=float, default=1., help="Guidance scale, 0–20; 1 keeps a single generation branch")
    parser.add_argument("--temperature", type=float, default=1., help="Semantic sampling temperature, 0–5")
    parser.add_argument("--cot", choices=("off", "melody", "full"), default="off")
    parser.add_argument("--abc", type=Path, help="Optional ABC score for melody/full planning")
    parser.add_argument("--plan-only", action="store_true", help="Save a symbolic score without rendering music; requires --cot melody or full")
    parser.add_argument("--performance", type=Path, help="Reuse saved YuE2 .codes.i32 performance codes; keep the source score as conditioning")
    parser.add_argument("--performance-only", action="store_true", help="Save performance codes without synthesizing audio")
    parser.add_argument("--score-tokens", type=Path, help="Replay a native saved score token file")
    parser.add_argument("--seed", type=int, default=831001)
    parser.add_argument("--threads", type=int, default=platform_runtime.settings()["threads"])
    parser.add_argument("--backend", choices=("metal", "cuda", "cpu"), default=platform_runtime.settings()["backend"])
    parser.add_argument("--refinement", type=Path, help="JSON object of YuE2 sampling controls")
    parser.add_argument("--out", type=Path, help="Output WAV; defaults to a new file in outputs/")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.max_seconds) or args.max_seconds <= 0 or args.steps < 1 or args.threads < 1:
        parser.error("duration, steps, and threads must be positive")
    if not 0 <= args.seed < 2 ** 63:
        parser.error("seed must be in [0, 2^63)")
    if not 0 <= args.guidance <= 20 or not 0 <= args.temperature <= 5:
        parser.error("guidance must be in [0, 20] and temperature in [0, 5], as required by the runtime")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output = (args.out or WORKSPACE / "outputs" / f"song-{stamp}.wav").resolve()
    if output.exists():
        parser.error(f"output already exists: {output}")
    mode = "free" if args.free else "instrumental" if args.instrumental else "surprise" if args.surprise else "lyrics"
    idea = inspire({"seed": str(args.seed), "mode": mode})
    lyrics = "" if args.free or args.instrumental else idea["lyrics"] if args.surprise else args.lyrics.read_text(encoding="utf-8").strip()
    style = args.style if args.style is not None else "" if args.free else "Instrumental music, no vocals" if args.instrumental else idea["style"] if args.surprise else "English, warm indie folk, acoustic guitar, gentle drums, clear lead vocal"
    abc = args.abc.read_text(encoding="utf-8").strip() if args.abc else ""
    if abc and args.cot == "off":
        parser.error("an ABC score requires --cot melody or --cot full")
    if args.score_tokens and (args.abc or args.cot == "off"):
        parser.error("--score-tokens requires melody/full planning and no --abc")
    if args.plan_only and args.cot == "off":
        parser.error("--plan-only requires --cot melody or --cot full")
    if args.plan_only and (args.performance or args.performance_only):
        parser.error("score-only composition and performance rendering are separate operations")
    command = build_command(lyrics=lyrics, style=style, max_seconds=args.max_seconds,
                            steps=args.steps, cot=args.cot, seed=args.seed, threads=args.threads,
                            output=output, backend=args.backend, abc=abc, mode=mode,
                            cfg_scale=args.guidance, temperature=args.temperature,
                            refinement=json.loads(args.refinement.read_text()) if args.refinement else {},
                            render_mode="plan" if args.plan_only else "music",
                            performance_file=args.performance, semantic_only=args.performance_only, solver=args.solver,
                            score_file=args.score_tokens.resolve() if args.score_tokens else None)
    if args.dry_run:
        print(json.dumps(command, indent=2))
        return
    if args.score_tokens:
        try:
            score_contract()
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    if not args.plan_only and not args.performance_only:
        require_solver(args.solver, command[0])
    if not BINARY.is_file():
        parser.error("build the executable with ./build.sh first")
    output.parent.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "logs").mkdir(exist_ok=True)
    log_path = WORKSPACE / "logs" / f"run-{stamp}.log"
    metrics_path = output.with_suffix(".metrics.json")
    monitor = platform_runtime.ProcessMemory()
    peak_footprint = 0
    started = time.monotonic()
    next_update = started
    print(f"Generating up to {args.max_seconds:g}s using Q4 + F16 VAE on {args.backend}.\nLog: {log_path}", flush=True)
    with log_path.open("w") as log:
        proc = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            while proc.poll() is None:
                footprint, peak = monitor.read(proc.pid)
                peak_footprint = max(peak_footprint, peak)
                if time.monotonic() >= next_update:
                    memory = f", process memory {footprint / GIB:.2f} GiB, peak {peak_footprint / GIB:.2f} GiB" if footprint else ""
                    print(f"{time.monotonic() - started:.0f}s elapsed{memory}", flush=True)
                    next_update = time.monotonic() + 15
                time.sleep(0.5)
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait()
            raise
    elapsed = time.monotonic() - started
    metrics = {
        "command": command,
        "exit_code": proc.returncode,
        "wall_seconds": elapsed,
        "peak_process_memory_bytes": peak_footprint,
        "memory_note": monitor.note,
        "log": str(log_path),
    }
    if monitor.libproc:
        metrics["macos_lifetime_peak_footprint_bytes_observed"] = peak_footprint
    if proc.returncode == 0 and args.cot != "off":
        from symbolic import read_plan
        plan = read_plan(output.with_suffix(".plan.json"), Path(platform_runtime.settings()["model_root"]) / "sidecars/yue2-qwen.tiktoken")
        if plan:
            metrics["symbolic_plan"] = plan
    if proc.returncode == 0 and output.is_file():
        with wave.open(str(output), "rb") as audio:
            metrics["audio_seconds"] = audio.getnframes() / audio.getframerate()
            metrics["sample_rate"] = audio.getframerate()
            metrics["channels"] = audio.getnchannels()
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n")
    if proc.returncode != 0:
        print("\n".join(log_path.read_text(errors="replace").splitlines()[-25:]))
        raise SystemExit(proc.returncode)
    saved = output.with_suffix(".codes.i32") if args.performance_only else output.with_suffix(".plan.json") if args.plan_only else output
    print(f"Saved {saved}\nElapsed: {elapsed:.1f}s; observed peak footprint: {peak_footprint / GIB:.2f} GiB\nMetrics: {metrics_path}")


if __name__ == "__main__":
    main()
