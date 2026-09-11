"""Backend discovery and lightweight platform-specific process measurements."""
import ctypes
from functools import lru_cache
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

from paths import DATA, MODELS, RUNTIME


@lru_cache(maxsize=1)
def default_backend():
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "metal"
    if shutil.which("nvidia-smi"):
        try:
            if subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=10).returncode == 0:
                return "cuda"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return "cpu"


@lru_cache(maxsize=1)
def default_threads():
    if platform.system() == "Darwin":
        try:
            return int(subprocess.check_output(["/usr/sbin/sysctl", "-n", "hw.perflevel0.physicalcpu"], stderr=subprocess.DEVNULL))
        except (OSError, ValueError, subprocess.CalledProcessError):
            pass
    return os.cpu_count() or 1


def settings():
    path = DATA / "engine.json"
    saved = json.loads(path.read_text()) if path.exists() else {}
    backend = saved.get("backend") or default_backend()
    legacy = RUNTIME / "build/macos-yue2/bin/audiocpp_cli"
    default_binary = legacy if backend == "metal" and legacy.is_file() else RUNTIME / f"build/{backend}-yue2/bin/audiocpp_cli"
    return {"backend": backend, "threads": saved.get("threads", default_threads()),
            "device": saved.get("device", 0), "binary": saved.get("binary", str(default_binary)),
            "model_root": saved.get("model_root", str(MODELS)),
            "model_file": saved.get("model_file", "yue2-3b-q4_0.gguf"),
            "vae_file": saved.get("vae_file", "yue2-vae-f16.gguf")}


def configure(payload):
    current = settings()
    if set(payload) - set(current):
        raise ValueError("Unknown engine setting.")
    current.update(payload)
    if current["backend"] not in ("metal", "cuda", "cpu"):
        raise ValueError("Choose Metal, NVIDIA CUDA, or CPU.")
    for key, minimum in (("threads", 1), ("device", 0)):
        if type(current[key]) is not int or current[key] < minimum:
            raise ValueError(f"{key.capitalize()} must be a whole number of at least {minimum}.")
    for key in ("binary", "model_root", "model_file", "vae_file"):
        if not isinstance(current[key], str) or not current[key].strip():
            raise ValueError(f"Provide {key.replace('_', ' ')}.")
        current[key] = current[key].strip()
    for key in ("model_file", "vae_file"):
        path = Path(current[key])
        if path.is_absolute() or ".." in path.parts or path.suffix != ".gguf":
            raise ValueError("Model files must be GGUF paths inside the model folder.")
    DATA.mkdir(parents=True, exist_ok=True)
    temporary = DATA / "engine.json.tmp"
    temporary.write_text(json.dumps(current, indent=2) + "\n")
    temporary.replace(DATA / "engine.json")
    return current


def readiness():
    value = settings()
    model_root = Path(value["model_root"])
    required = [value["model_file"], value["vae_file"], "sidecars/yue2-model-config.json",
                "sidecars/yue2-generation-config.json", "sidecars/yue2-vae-config.json", "sidecars/yue2-qwen.tiktoken"]
    missing = [name for name in required if not (model_root / name).is_file()]
    return {**value, "ready": Path(value["binary"]).is_file() and not missing,
            "binary_ready": Path(value["binary"]).is_file(), "missing_models": missing,
            "platform": platform.system(), "architecture": platform.machine()}


class ProcessMemory:
    def __init__(self):
        self.libproc = None
        if platform.system() == "Darwin":
            from run import RusageInfoV4
            self.usage_type = RusageInfoV4
            self.libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
            self.libproc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(RusageInfoV4)]
            self.libproc.proc_pid_rusage.restype = ctypes.c_int

    def read(self, pid):
        if self.libproc:
            usage = self.usage_type()
            if self.libproc.proc_pid_rusage(pid, 4, ctypes.byref(usage)) == 0:
                return usage.phys_footprint, max(usage.phys_footprint, usage.lifetime_max_phys_footprint)
        elif platform.system() == "Linux":
            try:
                fields = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines() if ":" in line)
                return int(fields.get("VmRSS", "0 kB").split()[0]) * 1024, int(fields.get("VmHWM", "0 kB").split()[0]) * 1024
            except (OSError, ValueError):
                pass
        return 0, 0

    @property
    def note(self):
        return ("Process footprint including attributed Metal allocations; sampled peak may miss the final interval."
                if self.libproc else "Process resident memory sampled from /proc; GPU memory is not included.")
