"""Backend discovery and lightweight platform-specific process measurements."""
import ctypes
from functools import lru_cache
import json
import os
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path

from paths import DATA, MODELS, RUNTIME, WORKSPACE


def writer_settings():
    return {"python": Path(os.environ.get("RIFF_WRITER_PYTHON", str(WORKSPACE / ".writer-venv/bin/python"))).expanduser(),
            "model": Path(os.environ.get("RIFF_WRITER_MODEL", str(MODELS / "lyric-writer"))).expanduser()}


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
    from run import engine_capabilities
    return {**value, "capabilities": engine_capabilities(value), "ready": Path(value["binary"]).is_file() and not missing,
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


def _mac_memory():
    libc = ctypes.CDLL(None)
    read = libc.sysctlbyname
    read.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    read.restype = ctypes.c_int
    def value(name):
        output, size = ctypes.c_uint64(), ctypes.c_size_t(8)
        if read(name.encode(), ctypes.byref(output), ctypes.byref(size), None, 0):
            raise OSError("Memory observation is unavailable")
        return output.value
    total = value("hw.memsize")
    # This is the kernel's available-page percentage used by memory_pressure
    # -Q, not merely free physical pages. Reclaimable/compressible capacity is
    # evaluated by the OS. Pressure is independently checked before admission.
    level = value("kern.memorystatus_level")
    if not 0 <= level <= 100:
        raise ValueError("Invalid memory observation")
    # The sysctl returns dispatch flags, not the internal kernel enum.
    pressure = {1: "normal", 2: "warning", 4: "critical"}.get(value("kern.memorystatus_vm_pressure_level"), "unknown")
    return {"total": total, "available": total * level // 100, "pressure": pressure,
            "source": "Darwin memorystatus available pages and pressure"}


def _linux_memory():
    fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
    total = int(fields["MemTotal"].split()[0]) * 1024
    available = int(fields["MemAvailable"].split()[0]) * 1024
    # A container's cgroup budget takes precedence over host-wide free RAM.
    groups = Path("/proc/self/cgroup").read_text().splitlines()
    cgroup_root = Path("/sys/fs/cgroup")
    scopes = {cgroup_root}
    for group in groups:
        parts = group.split(":", 2)
        if len(parts) == 3 and (parts[1] == "" or "memory" in parts[1].split(",")):
            scope_root = cgroup_root if parts[1] == "" else cgroup_root / "memory"
            scopes.add(scope_root)
            relative = Path(parts[2].lstrip("/"))
            # A namespaced container may expose its effective limit directly
            # at the cgroup mount. Ancestor limits also constrain descendants.
            if ".." not in relative.parts:
                base = scope_root / relative
                while base != scope_root:
                    scopes.add(base)
                    base = base.parent
    pressure_paths = {Path("/proc/pressure/memory")}
    for base in scopes:
        pressure_paths.add(base / "memory.pressure")
        for limit_file, usage_file in (("memory.max", "memory.current"), ("memory.limit_in_bytes", "memory.usage_in_bytes")):
            try:
                limit = (base / limit_file).read_text().strip()
                used = int((base / usage_file).read_text().strip())
                if limit != "max":
                    total = min(total, int(limit))
                    available = min(available, max(0, int(limit) - used))
            except (OSError, ValueError):
                pass
    full_stall = 0.0
    for path in pressure_paths:
        try:
            for line in path.read_text().splitlines():
                if line.startswith("full "):
                    fields = dict(field.split("=", 1) for field in line.split()[1:])
                    full_stall = max(full_stall, float(fields["avg10"]))
        except (OSError, ValueError, KeyError):
            pass
    # PSI 'full' means all non-idle tasks stalled on memory together. Let a
    # measured stall settle before admitting another model. On older kernels
    # without PSI, MemAvailable and the cgroup budget still bound admission.
    return {"total": total, "available": available, "pressure": "warning" if full_stall > 0 else "normal",
            "full_stall_percent": full_stall, "source": "Linux MemAvailable, cgroup budget and available PSI"}


def _cuda_memory(device):
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {}, {}, None
    flags = ["--format=csv,noheader,nounits"]
    output = subprocess.check_output([executable, "--query-gpu=index,uuid,memory.total,memory.free", *flags], text=True, timeout=5, stderr=subprocess.DEVNULL)
    cards = []
    for line in output.splitlines():
        index, identity, total, free = [field.strip() for field in line.split(",")]
        cards.append({"index": index, "id": identity, "total": int(total) * 1024 ** 2, "available": int(free) * 1024 ** 2})
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    identity = str(device)
    if visible is not None:
        choices = [value.strip() for value in visible.split(",") if value.strip()]
        if int(device) >= len(choices):
            return {}, {}, None
        identity = choices[int(device)]
    chosen = next((card for card in cards if card["index"] == identity or card["id"].startswith(identity)), None)
    if not chosen:
        return {}, {}, None
    processes = {}
    try:
        output = subprocess.check_output([executable, "--query-compute-apps=pid,gpu_uuid,used_memory", *flags], text=True, timeout=5, stderr=subprocess.DEVNULL)
        for line in output.splitlines():
            pid, gpu, used = [field.strip() for field in line.split(",")]
            if used.isdigit():
                processes.setdefault(gpu, {})[pid] = int(used) * 1024 ** 2
    except (OSError, ValueError, subprocess.SubprocessError):
        # Missing per-process counters preserve the full growth reservation.
        pass
    return {card["id"]: {key: card[key] for key in ("total", "available")} for card in cards}, processes, chosen["id"]


class MemoryObserver:
    """Observe host pressure and the selected CUDA device without changing it."""
    def __init__(self, backend=None, device=None):
        self.backend = backend
        self.device = device
        self.cuda_seen = backend == "cuda"
        self.lock = threading.Lock()
        self.last, self.observed = None, 0

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            # Reuse one observation within a polling interval across writers
            # and generation workers; no repeated nvidia-smi processes per job.
            if self.last is not None and now - self.observed < .5:
                return self.last
            try:
                host = _mac_memory() if platform.system() == "Darwin" else _linux_memory() if platform.system() == "Linux" else {}
            except (OSError, ValueError, KeyError):
                host = {"pressure": "unknown"}
            reserve = max(0, int(os.environ.get("RIFF_MEMORY_RESERVE_MB", "0"))) * 1024 ** 2
            host["reserve"] = reserve
            devices, processes, identity = {}, {}, None
            configured = settings()
            backend = self.backend or configured["backend"]
            device = configured["device"] if self.device is None else self.device
            self.cuda_seen = self.cuda_seen or backend == "cuda"
            if self.cuda_seen:
                try:
                    devices, processes, identity = _cuda_memory(device)
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
            self.last = {"host": host, "devices": devices, "device_processes": processes,
                         "device_identity": identity, "observed_at": time.time()}
            self.observed = now
            return self.last
