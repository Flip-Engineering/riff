"""Validate and normalize YuE2-3B adapters without loading executable checkpoints.

Only safetensors is accepted. Inspection can use a bounded remote header; import
additionally checks every value and writes a deterministic native stage file.
The dimensions describe the published m-a-p/YuE2-3B checkpoint, also repackaged
by Comfy-Org/YuE2. YuE1 stage-2 adapters have a different architecture.
"""
import array
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import sys


MAX_HEADER_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 ** 3
MAX_TENSORS = 4096
MAX_STAGE_F32_BYTES = 2 * 1024 ** 3
BASE_MODEL = "m-a-p/YuE2-3B"
DTYPES = {"F32": 4, "F16": 2, "BF16": 2}
PROJECTIONS = {
    "self_attn.q_proj": (2048, 2048),
    "self_attn.k_proj": (1024, 2048),
    "self_attn.v_proj": (1024, 2048),
    "self_attn.o_proj": (2048, 2048),
    "self_attn.qkv_proj": (4096, 2048),
    "mlp.gate_proj": (6144, 2048),
    "mlp.up_proj": (6144, 2048),
    "mlp.down_proj": (2048, 6144),
    "mlp.gate_up_proj": (12288, 2048),
}
FULL_SHAPES = {"vae2llm.weight": (2048, 64), "vae2llm.bias": (2048,),
               "llm2vae.weight": (64, 2048), "llm2vae.bias": (64,)}
SUFFIXES = ((".lora_A.default.weight", "a", "peft"),
            (".lora_B.default.weight", "b", "peft"),
            (".lora_A.weight", "a", "peft"), (".lora_B.weight", "b", "peft"),
            (".lora_down.weight", "a", "comfyui"), (".lora_up.weight", "b", "comfyui"),
            (".lora_A", "a", "native"), (".lora_B", "b", "native"),
            (".alpha", "alpha", None))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate safetensors or configuration key: " + key)
        result[key] = value
    return result


def json_object(raw):
    def invalid(value):
        raise ValueError("Non-finite JSON value: " + value)
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=invalid)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Invalid safetensors or configuration JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def regular_file(path):
    if not isinstance(path, (str, os.PathLike)):
        raise ValueError("Choose a regular safetensors file.")
    path = Path(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError("Adapter file is missing: " + path.name) from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ValueError("Choose a regular safetensors file, not a link or directory.")
    return path, info.st_size


def validate_header(header, data_bytes=None):
    """Check shape/offset integrity, with optional known total payload size."""
    if not isinstance(header, dict) or not header or len(header) > MAX_TENSORS + 1:
        raise ValueError("Adapter has an empty or oversized tensor header.")
    metadata = header.get("__metadata__", {})
    if not isinstance(metadata, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                              for k, v in metadata.items()):
        raise ValueError("Safetensors metadata must contain strings.")
    intervals = []
    for name, tensor in header.items():
        if name == "__metadata__":
            continue
        if not isinstance(name, str) or not name or len(name) > 512 or not isinstance(tensor, dict):
            raise ValueError("Adapter tensor name or descriptor is invalid.")
        if set(tensor) != {"dtype", "shape", "data_offsets"}:
            raise ValueError("Unexpected safetensors tensor fields: " + name)
        dtype, shape, offsets = tensor["dtype"], tensor["shape"], tensor["data_offsets"]
        if not isinstance(dtype, str) or dtype not in DTYPES:
            raise ValueError("Only F32, F16 and BF16 adapter tensors are supported: " + name)
        if (not isinstance(shape, list) or len(shape) > 2
                or any(type(n) is not int or n <= 0 or n > 65536 for n in shape)):
            raise ValueError("Adapter tensors must be nonempty matrices, vectors or scalars: " + name)
        if (not isinstance(offsets, list) or len(offsets) != 2
                or any(type(n) is not int or not 0 <= n <= MAX_FILE_BYTES for n in offsets)):
            raise ValueError("Invalid safetensors data offsets: " + name)
        start, end = offsets
        if end - start != math.prod(shape) * DTYPES[dtype]:
            raise ValueError("Tensor byte length does not match its shape: " + name)
        intervals.append((start, end, name))
    if not intervals:
        raise ValueError("Adapter contains no tensors.")
    end = 0
    for start, stop, name in sorted(intervals):
        if start != end:
            raise ValueError("Safetensors has overlapping tensors or unused data: " + name)
        end = stop
    if data_bytes is not None and end != data_bytes:
        raise ValueError("Safetensors payload is truncated or contains trailing data.")
    return end


def read_header(path):
    path, size = regular_file(path)
    if path.suffix.lower() != ".safetensors":
        raise ValueError("Import a .safetensors adapter. Pickle, .pt and .bin checkpoints are not opened.")
    if size < 10 or size > MAX_FILE_BYTES:
        raise ValueError("Adapter file has an invalid or unsupported size.")
    with path.open("rb") as handle:
        raw = handle.read(8)
        if len(raw) != 8:
            raise ValueError("Safetensors file was truncated during inspection.")
        length = struct.unpack("<Q", raw)[0]
        if not 2 <= length <= MAX_HEADER_BYTES or 8 + length > size:
            raise ValueError("Safetensors header length is invalid.")
        header = json_object(handle.read(length))
    validate_header(header, size - 8 - length)
    return header, 8 + length


def _target(name):
    """Return native module spelling plus a role implied by its namespace."""
    stage = None
    if name.startswith("text_encoders."):
        stage, name = "ar", name[len("text_encoders."):]
    elif name.startswith("diffusion_model."):
        stage, name = "nar", name[len("diffusion_model."):]
    if name.startswith("base_model.model."):
        name = name[len("base_model.model."):]
    if name.startswith("model."):
        name = name[len("model."):]
    match = re.fullmatch(r"layers\.(\d+)\.(nar_)?(self_attn|mlp)\.([a-z_]+)", name)
    if match:
        layer, nar, block, projection = match.groups()
        if int(layer) >= 28 or str(int(layer)) != layer:
            raise ValueError("Adapter layer is outside the YuE2-3B architecture: " + name)
        module = block + "." + projection
        if module not in PROJECTIONS:
            raise ValueError("Unsupported YuE2 adapter target: " + name)
        if nar and stage == "ar":
            raise ValueError("Adapter target contradicts its AR namespace: " + name)
        stage = "nar" if nar else (stage or "ar")
        native = "layers." + layer + "." + ("nar_" if stage == "nar" else "") + module
        return native, stage, PROJECTIONS[module]
    raise ValueError("Unsupported YuE2 adapter target: " + name)


def _full_target(name):
    for prefix in ("diffusion_model.", "base_model.model.", "model."):
        if name.startswith(prefix):
            name = name[len(prefix):]
    kind = "replacement"
    if name.endswith(".diff_b"):
        base, kind = name[:-7] + ".bias", "delta"
    elif name.endswith(".diff"):
        base, kind = name[:-5] + ".weight", "delta"
    else:
        base = name
    if base not in FULL_SHAPES:
        raise ValueError("Unsupported YuE2 adapter tensor: " + name)
    return name, base, kind, FULL_SHAPES[base]


def _config_value(config, field, target, fallback, aliases=()):
    """PEFT patterns are exact or suffix module names; regex execution is avoided."""
    pattern = config.get(field, {})
    if not isinstance(pattern, dict):
        raise ValueError("Adapter " + field + " must be an object.")
    found = []
    for key, value in pattern.items():
        if not re.fullmatch(r"[A-Za-z0-9_.]+", key):
            raise ValueError("Only literal module suffixes are supported in PEFT " + field + ".")
        numeric = _finite_number(value, "PEFT " + field)
        if any(candidate == key or candidate.endswith("." + key) for candidate in (target, *aliases)):
            found.append(numeric)
    if len(set(found)) > 1:
        raise ValueError("Ambiguous PEFT " + field + " for " + target)
    return found[0] if found else fallback


def _finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + " must be a finite number.")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(label + " must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError(label + " must be a finite number.")
    return number


def _fused_binding_bytes(groups):
    # Native binding retains one A factor per output projection. Fused B rows
    # partition without expansion, while QKV duplicates A twice and gate/up once.
    return sum(group["rank"] * group["shape"][1] * 4
               * (2 if target.endswith(".qkv_proj") else 1)
               for target, group in groups.items()
               if target.endswith((".qkv_proj", ".gate_up_proj")))


def _stage_f32_bytes(header, groups, full):
    return (sum((math.prod(header[g["a"]]["shape"]) + math.prod(header[g["b"]]["shape"]) + 1) * 4
                for g in groups.values())
            + sum(math.prod(header[f["source"]]["shape"]) * 4 for f in full))


def inspect_header(header, config=None):
    """Classify complete tensor metadata. This is not a payload integrity check."""
    payload_size = validate_header(header)
    metadata = header.get("__metadata__", {})
    base = metadata.get("base_model", "")
    if base and not any(marker in base.lower() for marker in ("m-a-p/yue2-3b", "comfy-org/yue2")):
        raise ValueError("Adapter declares a different base model: " + base)
    config = config or {}
    if not isinstance(config, dict):
        raise ValueError("PEFT adapter configuration must be an object.")
    for field in ("use_dora", "use_qalora", "fan_in_fan_out", "lora_bias", "use_rslora"):
        if field in config and type(config[field]) is not bool:
            raise ValueError("PEFT " + field + " must be true or false.")
    for field in ("use_dora", "use_qalora", "fan_in_fan_out", "lora_bias"):
        if config.get(field):
            raise ValueError("Unsupported PEFT adapter option: " + field)
    if config.get("peft_type", "LORA") != "LORA":
        raise ValueError("Only ordinary LoRA adapters are supported.")
    config_base = config.get("base_model_name_or_path", "")
    if not isinstance(config_base, str):
        raise ValueError("PEFT base model must be a string.")
    if config_base and not any(marker in config_base.lower() for marker in ("yue2-3b", "comfy-org/yue2")):
        raise ValueError("PEFT configuration declares a different base model.")
    groups, full, roles, formats = {}, [], set(), set()
    # AI Toolkit's YuE2 transformer export deliberately uses PEFT suffixes,
    # forces alpha=rank and omits alpha tensors. Require its producer metadata
    # and YuE2 namespace so generic PEFT files remain unambiguous.
    toolkit_export = False
    if metadata.get("ss_base_model_version") == "yue2" and "software" in metadata:
        try:
            software = json_object(metadata["software"])
            toolkit_export = software.get("name") == "ai-toolkit" and software.get("repo") == "https://github.com/ostris/ai-toolkit"
        except ValueError:
            pass
    for name, tensor in header.items():
        if name == "__metadata__":
            continue
        for suffix, part, style in SUFFIXES:
            if name.endswith(suffix):
                original_target = name[:-len(suffix)]
                target, role, shape = _target(original_target)
                group = groups.setdefault(target, {"target": target, "stage": role, "shape": shape})
                aliases = group.setdefault("aliases", [])
                if original_target not in aliases:
                    aliases.append(original_target)
                if part in group:
                    raise ValueError("Duplicate adapter factor: " + target)
                if style and group.get("format", style) != style:
                    raise ValueError("Mixed adapter formats for " + target)
                if style:
                    group["format"] = style
                    formats.add(style)
                group[part] = name
                roles.add(role)
                break
        else:
            native, target, kind, shape = _full_target(name)
            if tuple(tensor["shape"]) != shape:
                raise ValueError("Full projection shape differs from YuE2-3B: " + name)
            if any(item["target"] == target for item in full):
                raise ValueError("Duplicate full projection or replacement/delta conflict: " + target)
            full.append({"source": name, "native": native, "target": target, "kind": kind})
            roles.add("nar")
    if not roles:
        raise ValueError("Adapter contains no supported weight updates.")
    role = next(iter(roles)) if len(roles) == 1 else "ar+nar"
    declared = metadata.get("yue2_lora_branch")
    if declared and declared != role:
        raise ValueError("Adapter stage metadata contradicts its tensor targets.")
    for target, group in groups.items():
        if "a" not in group or "b" not in group:
            raise ValueError("Missing LoRA A/B pair: " + target)
        a, b = header[group["a"]]["shape"], header[group["b"]]["shape"]
        output, input_size = group["shape"]
        if len(a) != 2 or len(b) != 2 or a[0] != b[1] or a[1] != input_size or b[0] != output:
            raise ValueError("Adapter factor shapes do not match YuE2-3B: " + target)
        group["rank"] = a[0]
        if "alpha" in group and math.prod(header[group["alpha"]]["shape"]) != 1:
            raise ValueError("LoRA alpha must contain one finite scalar: " + target)
        alpha = float(a[0])
        if group["format"] == "peft":
            toolkit_unit = (toolkit_export and group["a"].startswith(("text_encoders.model.", "diffusion_model.model.")))
            if not config and "alpha" not in group and not toolkit_unit:
                raise ValueError("PEFT adapters need adapter_config.json or explicit alpha tensors.")
            rank = _config_value(config, "rank_pattern", target, config.get("r", a[0]), group["aliases"])
            if _finite_number(rank, "PEFT rank") != a[0]:
                raise ValueError("PEFT configured rank differs from adapter factors: " + target)
            alpha = _finite_number(_config_value(config, "alpha_pattern", target,
                                   config.get("lora_alpha", a[0]), group["aliases"]), "LoRA alpha")
            if config.get("use_rslora"):
                if "alpha" in group:
                    raise ValueError("RS-LoRA with explicit alpha tensors is not supported.")
                alpha *= math.sqrt(a[0])
        elif "lora_scale" in metadata:
            try:
                multiplier = float(metadata["lora_scale"])
            except (ValueError, TypeError) as exc:
                raise ValueError("Adapter lora_scale metadata is invalid.") from exc
            if not math.isfinite(multiplier):
                raise ValueError("Adapter lora_scale metadata must be finite.")
            alpha *= multiplier
        # Native and ComfyUI exports without alpha apply B @ A at unit strength.
        group["effective_alpha"] = alpha
    targets = set(groups)
    for target in targets:
        if target.endswith(".qkv_proj") and any(target[:-8] + part in targets for part in ("q_proj", "k_proj", "v_proj")):
            raise ValueError("Adapter mixes fused and separate Q/K/V targets.")
        if target.endswith(".gate_up_proj") and any(target[:-12] + part in targets for part in ("gate_proj", "up_proj")):
            raise ValueError("Adapter mixes fused and separate gate/up targets.")
    if not groups and not full:
        raise ValueError("Adapter contains no supported weight updates.")
    for stage in roles:
        selected_groups = {target: group for target, group in groups.items() if group["stage"] == stage}
        if _stage_f32_bytes(header, selected_groups, full if stage == "nar" else []) > MAX_STAGE_F32_BYTES:
            raise ValueError("Adapter exceeds the native engine's 2 GiB of expanded F32 data per stage.")
    expanded_bytes = sum(math.prod(t["shape"]) * 4 for name, t in header.items() if name != "__metadata__")
    expanded_bytes += _fused_binding_bytes(groups)
    largest = max([math.prod(g["shape"]) for g in groups.values()]
                  + [math.prod(FULL_SHAPES[f["target"]]) for f in full])
    return {"stage": role, "stages": sorted(roles), "format": "+".join(sorted(formats)) or "full-projections",
            "base_model": BASE_MODEL, "metadata": metadata, "groups": groups, "full": full,
            "targets": sorted([*groups, *(f["target"] for f in full)]),
            "rank": sorted({g["rank"] for g in groups.values()}), "payload_bytes": payload_size,
            "adapter_bytes": expanded_bytes, "merge_scratch_bytes": largest * 8,
            "projection_mode": sorted({f["kind"] for f in full})}


def _finite_payload(data, dtype, name):
    if dtype == "F32":
        values = array.array("f")
        values.frombytes(data)
        if sys.byteorder != "little":
            values.byteswap()
        valid = all(math.isfinite(n) for n in values)
    else:
        values = array.array("H")
        values.frombytes(data)
        if sys.byteorder != "little":
            values.byteswap()
        mask = 0x7F80 if dtype == "BF16" else 0x7C00
        valid = all(n & mask != mask for n in values)
    if not valid:
        raise ValueError("Adapter contains NaN or infinity: " + name)


def _scalar(data, dtype):
    if dtype == "BF16":
        return struct.unpack("<f", b"\0\0" + data)[0]
    return struct.unpack("<f" if dtype == "F32" else "<e", data)[0]


def normalize(source, destination, config=None, cancel=None, stage=None):
    """Validate all data and copy into the native spelling, preserving tensor bits."""
    source, destination = Path(source), Path(destination)
    header, offset = read_header(source)
    result = inspect_header(header, config)
    if stage is not None:
        if stage not in result["stages"]:
            raise ValueError("Adapter has no tensors for the selected stage.")
        groups = {k: v for k, v in result["groups"].items() if v["stage"] == stage}
        full = result["full"] if stage == "nar" else []
        source_names = {g[p] for g in groups.values() for p in ("a", "b", "alpha") if p in g}
        source_names.update(f["source"] for f in full)
        result.update({"stage": stage, "stages": [stage], "groups": groups, "full": full,
                       "targets": sorted([*groups, *(f["target"] for f in full)]),
                       "rank": sorted({g["rank"] for g in groups.values()}),
                       "adapter_bytes": sum(math.prod(header[n]["shape"]) * 4 for n in source_names)
                                        + _fused_binding_bytes(groups),
                       "merge_scratch_bytes": max([math.prod(g["shape"]) for g in groups.values()]
                            + [math.prod(FULL_SHAPES[f["target"]]) for f in full]) * 8,
                       "projection_mode": sorted({f["kind"] for f in full})})
    elif len(result["stages"]) != 1:
        raise ValueError("Choose a stage when normalizing a combined AR/NAR adapter.")
    tensors, alpha_sources = {}, {}
    for target, group in result["groups"].items():
        tensors[target + ".lora_A"] = group["a"]
        tensors[target + ".lora_B"] = group["b"]
        alpha_sources[target + ".alpha"] = group
    for item in result["full"]:
        tensors[item["native"]] = item["source"]
    normalized = {"__metadata__": {"format": "pt", "riff_format": "yue2-lora-v1",
                                   "base_model": BASE_MODEL, "yue2_lora_branch": result["stage"]}}
    position = 0
    for name in sorted([*tensors, *alpha_sources]):
        if name in alpha_sources:
            dtype, shape, count = "F32", [], 4
        else:
            original = header[tensors[name]]
            dtype, shape = original["dtype"], original["shape"]
            count = original["data_offsets"][1] - original["data_offsets"][0]
        normalized[name] = {"dtype": dtype, "shape": shape, "data_offsets": [position, position + count]}
        position += count
    raw_header = json.dumps(normalized, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    raw_header += b" " * (-len(raw_header) % 8)
    digest = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            def write(data):
                outgoing.write(data)
                digest.update(data)
            write(struct.pack("<Q", len(raw_header)))
            write(raw_header)
            for name in sorted([*tensors, *alpha_sources]):
                if cancel:
                    cancel()
                if name in alpha_sources:
                    group = alpha_sources[name]
                    alpha = group["effective_alpha"]
                    if "alpha" in group:
                        item = header[group["alpha"]]
                        incoming.seek(offset + item["data_offsets"][0])
                        data = incoming.read(DTYPES[item["dtype"]])
                        _finite_payload(data, item["dtype"], group["alpha"])
                        alpha = _scalar(data, item["dtype"])
                    try:
                        encoded = struct.pack("<f", alpha)
                    except (OverflowError, struct.error) as exc:
                        raise ValueError("LoRA alpha exceeds finite F32 range: " + name) from exc
                    if not math.isfinite(struct.unpack("<f", encoded)[0]):
                        raise ValueError("LoRA alpha exceeds finite F32 range: " + name)
                    write(encoded)
                    continue
                item = header[tensors[name]]
                start, end = item["data_offsets"]
                incoming.seek(offset + start)
                remaining = end - start
                while remaining:
                    if cancel:
                        cancel()
                    data = incoming.read(min(1024 * 1024, remaining))
                    if not data:
                        raise ValueError("Adapter changed or was truncated during import.")
                    _finite_payload(data, item["dtype"], name)
                    write(data)
                    remaining -= len(data)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    result["engine_sha256"] = digest.hexdigest()
    result["engine_bytes"] = destination.stat().st_size
    return result


def sha256_file(path, cancel=None):
    regular_file(path)
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            if cancel:
                cancel()
            digest.update(chunk)
    return digest.hexdigest()
