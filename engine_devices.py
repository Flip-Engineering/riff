"""Model-free checks against the pinned ggml Metal device diagnostics.

The log format is a weaker contract than a native supports-op API. Fail closed
if it changes: a listed GPU alone does not establish RMS_NORM support. This is
the minimum known YuE2 requirement, not qualification of every graph operation.
"""
from functools import lru_cache
from pathlib import Path
import re
import subprocess


CPU_ADVICE = "Choose CPU in Settings and Download and set up (CLI: setup_engine.py --backend cpu)."


class DeviceProbeError(ValueError):
    def __init__(self, message, output):
        super().__init__(message)
        self.output = output


def metal_device_capability(output, device=0):
    devices = {}
    for match in re.finditer(r'^MTL:(\d+) "([^"]+)"', output, re.MULTILINE):
        devices[int(match[1])] = {"index": int(match[1]), "name": match[2], "families": []}
    current = None
    for line in output.splitlines():
        name = re.search(r"ggml_metal_device_init: GPU name:\s+MTL(\d+) \((.*)\)", line)
        if name:
            current = devices.get(int(name[1]))
        elif current is not None:
            family = re.search(r"ggml_metal_device_init: GPU family:\s+(.+)", line)
            flag = re.search(r"ggml_metal_device_init: (simdgroup reduction|simdgroup matrix mul\.)\s*=\s*(true|false)\b", line)
            if family:
                current["families"].append(family[1].strip())
            if flag:
                key = "simdgroup_reduction" if flag[1] == "simdgroup reduction" else "simdgroup_matrix_mul"
                current[key] = flag[2] == "true"
    facts = devices.get(device, {"index": device})
    reduction = facts.get("simdgroup_reduction")
    status = "supported" if reduction is True else "unsupported" if reduction is False else "unverified"
    result = {"status": status, "device": facts, "requirement": "simdgroup reduction for YuE2 RMS_NORM"}
    if status == "unsupported":
        result["error"] = (f"Metal device {device} ({facts['name']}) cannot run YuE2: "
                           "simdgroup reduction is unavailable (required for RMS_NORM). " + CPU_ADVICE)
    elif status == "unverified":
        result["error"] = f"Cannot verify YuE2 simdgroup reduction support for Metal device {device}. " + CPU_ADVICE
    return result


def _identity(binary):
    info = Path(binary).stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@lru_cache(maxsize=16)
def _device_output(binary, identity):
    # Embedded Metal libraries can take ~30 seconds to initialize on hosted VMs.
    result = subprocess.run([binary, "--list-devices"], capture_output=True, text=True, timeout=90)
    output = result.stdout + "\n" + result.stderr
    if result.returncode or _identity(binary) != identity:
        raise DeviceProbeError("The engine device probe failed or the executable changed.", output)
    return output


def probe_metal_device(binary, device=0):
    binary = str(Path(binary).resolve())
    try:
        output = _device_output(binary, _identity(binary))
        return {**metal_device_capability(output, device), "output": output}
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {"status": "unverified", "device": {"index": device},
                "output": error.output if isinstance(error, DeviceProbeError) else "",
                "error": "Cannot verify the Metal device: " + str(error) + " " + CPU_ADVICE}


def require_device(settings, backend=None):
    if (backend or settings["backend"]) == "metal":
        result = probe_metal_device(settings["binary"], settings.get("device", 0))
        if result.get("error"):
            raise ValueError(result["error"])
