"""Adapter tests use tiny architecture-correct factors, never model weights."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

import lora_format
from loras import Cancelled, HuggingFace, LoRALibrary, hf_url, hub_path, repository, resolve_url, revision


COMMIT = "a" * 40
REPO = "musician/yue2-test-lora"


def adapter_bytes(stage="ar", style="native", fused=False, combined=False, value=.125,
                  full=None, alpha=None, config_metadata=None, rank=1):
    tensors = {}
    stages = ("ar", "nar") if combined else (stage,)
    for role in stages:
        if style == "comfyui":
            prefix = ("text_encoders." if role == "ar" else "diffusion_model.") + "model.layers.0.self_attn."
            a_suffix, b_suffix = ".lora_down.weight", ".lora_up.weight"
        elif style == "peft":
            prefix = "base_model.model.model.layers.0." + ("nar_" if role == "nar" else "") + "self_attn."
            a_suffix, b_suffix = ".lora_A.weight", ".lora_B.weight"
        else:
            prefix = "layers.0." + ("nar_" if role == "nar" else "") + "self_attn."
            a_suffix, b_suffix = ".lora_A", ".lora_B"
        target = prefix + ("qkv_proj" if fused else "q_proj")
        tensors[target + a_suffix] = ("F32", [rank, 2048], struct.pack("<f", value) * (rank * 2048))
        tensors[target + b_suffix] = ("F32", [4096 if fused else 2048, rank],
                                     struct.pack("<f", value) * ((4096 if fused else 2048) * rank))
        if alpha is not None:
            tensors[target + ".alpha"] = ("F32", [], struct.pack("<f", alpha))
    if full:
        prefix = "diffusion_model." if style == "comfyui" else ""
        if full == "delta":
            name, shape = prefix + "vae2llm.diff_b", [2048]
        else:
            name, shape = prefix + "vae2llm.bias", [2048]
        tensors[name] = ("F32", shape, b"\0\0\0\0" * 2048)
    return pack_tensors(tensors, config_metadata)


def pack_tensors(tensors, metadata=None):
    header = {"__metadata__": metadata or {"base_model": "m-a-p/YuE2-3B"}}
    position, chunks = 0, []
    for name, (dtype, shape, data) in sorted(tensors.items()):
        header[name] = {"dtype": dtype, "shape": shape, "data_offsets": [position, position + len(data)]}
        position += len(data)
        chunks.append(data)
    raw = json.dumps(header).encode()
    raw += b" " * (-len(raw) % 8)
    return struct.pack("<Q", len(raw)) + raw + b"".join(chunks)


def header_of(data):
    length = struct.unpack("<Q", data[:8])[0]
    return json.loads(data[8:8 + length])


class Response(BytesIO):
    def __init__(self, data, url, status=200, headers=None):
        super().__init__(data)
        self.status, self.url, self.headers = status, url, headers or {}

    def geturl(self):
        return self.url


class FakeHub(HuggingFace):
    def __init__(self, files, ignore_range=False):
        self.files = files
        self.ignore_range = ignore_range
        self.requests = []
        self.commit = COMMIT

    def model(self, repo, ref="main"):
        if repo != REPO:
            raise ValueError("Unknown model")
        commit = self.commit if ref == "main" else ref
        return {"id": repo, "sha": commit, "tags": ["lora", "folk"], "cardData": {"license": "apache-2.0"},
                "siblings": [{"rfilename": path, "size": len(data), "lfs": {"size": len(data),
                              "sha256": hashlib.sha256(data).hexdigest()}} for path, data in self.files.items()]}

    def open(self, url, headers=None):
        hf_url(url)
        self.requests.append((url, headers or {}))
        path = unquote(urlsplit(url).path.split("/resolve/", 1)[1].split("/", 1)[1])
        data = self.files[path]
        byte_range = (headers or {}).get("Range")
        if byte_range and not self.ignore_range:
            left, right = byte_range[6:].split("-")
            start, end = int(left), int(right) if right else len(data) - 1
            return Response(data[start:end + 1], url, 206,
                            {"Content-Range": f"bytes {start}-{end}/{len(data)}"})
        return Response(data, url)


class LoRAFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = LoRALibrary(self.root / "library")

    def tearDown(self):
        self.library.close()
        self.temporary.cleanup()

    def adapter(self, name="adapter.safetensors", **kwargs):
        path = self.root / name
        path.write_bytes(adapter_bytes(**kwargs))
        return path


class FormatTests(LoRAFixture):
    def test_native_and_comfyui_role_names_normalize(self):
        for role in ("ar", "nar"):
            for style in ("native", "comfyui"):
                with self.subTest(role=role, style=style):
                    source = self.adapter(style + role + ".safetensors", stage=role, style=style, fused=True)
                    destination = self.root / (style + role + "-engine.safetensors")
                    info = lora_format.normalize(source, destination)
                    header, offset = lora_format.read_header(destination)
                    prefix = "layers.0." + ("nar_" if role == "nar" else "") + "self_attn.qkv_proj"
                    self.assertIn(prefix + ".lora_A", header)
                    self.assertIn(prefix + ".lora_B", header)
                    self.assertEqual(info["stage"], role)
                    alpha = header[prefix + ".alpha"]
                    data = destination.read_bytes()[offset + alpha["data_offsets"][0]:]
                    self.assertEqual(struct.unpack("<f", data[:4])[0], 1.)

    def test_combined_file_is_split_into_two_native_files(self):
        source = self.adapter(style="comfyui", combined=True, full="delta")
        installed = self.library.import_local([source])
        self.assertEqual(installed["stages"], ["ar", "nar"])
        self.assertEqual(len({f["original_file"] for f in installed["files"]}), 1)
        for file in installed["files"]:
            header, _ = lora_format.read_header(self.library.root / installed["id"] / file["engine_file"])
            info = lora_format.inspect_header(header)
            self.assertEqual(info["stage"], file["stage"])
        self.assertEqual(installed["files"][1]["projection_mode"], ["delta"])

    def test_full_deltas_are_not_silently_changed_to_replacements(self):
        for mode in ("delta", "replacement"):
            source = self.adapter(mode + ".safetensors", stage="nar", style="comfyui", full=mode)
            result = self.library.import_local([source])
            file = result["files"][0]
            header, _ = lora_format.read_header(self.library.root / result["id"] / file["engine_file"])
            self.assertIn("vae2llm." + ("diff_b" if mode == "delta" else "bias"), header)
            self.assertEqual(file["projection_mode"], [mode])

    def test_missing_pairs_wrong_dimensions_unknown_targets_and_conflicting_fusions_rejected(self):
        baseline = header_of(adapter_bytes())
        bads = []
        missing = deepcopy(baseline)
        del missing["layers.0.self_attn.q_proj.lora_B"]
        bads.append(missing)
        wrong = deepcopy(baseline)
        wrong["layers.0.self_attn.q_proj.lora_A"]["shape"] = [2, 1024]
        bads.append(wrong)
        unknown = {k.replace("q_proj", "bad_proj"): v for k, v in baseline.items()}
        bads.append(unknown)
        layer = {k.replace("layers.0.", "layers.28."): v for k, v in baseline.items()}
        bads.append(layer)
        for header in bads:
            with self.subTest(header=list(header)), self.assertRaises(ValueError):
                lora_format.inspect_header(header)
        tensors = {}
        for target, out in (("q_proj", 2048), ("qkv_proj", 4096)):
            tensors["layers.0.self_attn." + target + ".lora_A"] = ("F32", [1, 2048], b"\0" * 8192)
            tensors["layers.0.self_attn." + target + ".lora_B"] = ("F32", [out, 1], b"\0" * (out * 4))
        with self.assertRaisesRegex(ValueError, "fused"):
            lora_format.inspect_header(header_of(pack_tensors(tensors)))

    def test_header_byte_layout_and_pickle_and_duplicate_json_are_rejected(self):
        source = self.adapter()
        original = source.read_bytes()
        for data in (original[:-4], original + b"\0", struct.pack("<Q", 2 ** 63) + b"{}"):
            source.write_bytes(data)
            with self.assertRaises(ValueError):
                lora_format.read_header(source)
        pickle = self.root / "unsafe.pt"
        pickle.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "Pickle"):
            lora_format.read_header(pickle)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            lora_format.json_object(b'{"x":1,"x":2}')
        source.write_bytes(original)
        header, _ = lora_format.read_header(source)
        header["layers.0.self_attn.q_proj.lora_B"]["data_offsets"] = [0, 8192]
        with self.assertRaisesRegex(ValueError, "overlapping"):
            lora_format.validate_header(header)

    def test_nonfinite_factors_and_alpha_never_install(self):
        for kwargs in ({"value": float("nan")}, {"value": float("inf")}, {"alpha": float("nan")}):
            source = self.adapter(**kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, "NaN|finite"):
                self.library.import_local([source])
        self.assertEqual(self.library.list(), [])

    def test_f16_and_bf16_nonfinite_values_are_detected(self):
        for dtype, finite, nonfinite in (("F16", 0x3C00, 0x7C00), ("BF16", 0x3F80, 0x7F80)):
            lora_format._finite_payload(struct.pack("<H", finite), dtype, "fixture")
            with self.subTest(dtype=dtype), self.assertRaisesRegex(ValueError, "infinity"):
                lora_format._finite_payload(struct.pack("<H", nonfinite), dtype, "fixture")

    def test_fused_admission_includes_native_a_factor_copies(self):
        header = header_of(adapter_bytes(fused=True, rank=2))
        info = lora_format.inspect_header(header)
        raw = sum(structure["shape"][0] * structure["shape"][1] * 4
                  for name, structure in header.items() if name != "__metadata__")
        self.assertEqual(info["adapter_bytes"], raw + 2 * 2 * 2048 * 4)

    def test_peft_config_scaling_is_folded_into_explicit_alpha(self):
        source = self.adapter(style="peft", rank=2)
        with self.assertRaisesRegex(ValueError, "adapter_config"):
            self.library.import_local([source])
        (self.root / "adapter_config.json").write_text(json.dumps({"peft_type": "LORA", "r": 2, "lora_alpha": 8}))
        result = self.library.import_local([source])
        file = result["files"][0]
        self.assertIsNotNone(file["config_sha256"])
        header, offset = lora_format.read_header(self.library.root / result["id"] / file["engine_file"])
        key = "layers.0.self_attn.q_proj.alpha"
        data = (self.library.root / result["id"] / file["engine_file"]).read_bytes()
        start = offset + header[key]["data_offsets"][0]
        self.assertEqual(struct.unpack("<f", data[start:start + 4])[0], 8.)

    def test_other_base_models_and_unsupported_peft_algorithms_rejected(self):
        source = self.adapter(config_metadata={"base_model": "m-a-p/YuE-s2-1B-general"})
        with self.assertRaisesRegex(ValueError, "different base"):
            self.library.import_local([source])
        header = header_of(adapter_bytes(style="peft"))
        for option in ("use_dora", "fan_in_fan_out", "use_qalora", "lora_bias"):
            with self.subTest(option=option), self.assertRaisesRegex(ValueError, "Unsupported"):
                lora_format.inspect_header(header, {"r": 1, "lora_alpha": 1, option: True})

    def test_ai_toolkit_yue2_export_uses_verified_unit_scale_without_peft_config(self):
        data = adapter_bytes(style="comfyui", combined=True)
        original = header_of(data)
        size = struct.unpack("<Q", data[:8])[0]
        payload = data[8 + size:]
        tensors = {}
        for name, descriptor in original.items():
            if name == "__metadata__":
                continue
            key = name.replace(".lora_down.weight", ".lora_A.weight").replace(".lora_up.weight", ".lora_B.weight")
            start, end = descriptor["data_offsets"]
            tensors[key] = (descriptor["dtype"], descriptor["shape"], payload[start:end])
        metadata = {"ss_base_model_version": "yue2", "software": json.dumps({"name": "ai-toolkit",
                    "repo": "https://github.com/ostris/ai-toolkit", "version": "0.13.16"})}
        source = self.root / "toolkit.safetensors"
        source.write_bytes(pack_tensors(tensors, metadata))
        result = self.library.import_local([source])
        self.assertEqual(result["stages"], ["ar", "nar"])
        for file in result["files"]:
            path = self.library.root / result["id"] / file["engine_file"]
            header, offset = lora_format.read_header(path)
            for name, tensor in header.items():
                if name.endswith(".alpha"):
                    start = offset + tensor["data_offsets"][0]
                    self.assertEqual(struct.unpack("<f", path.read_bytes()[start:start + 4])[0], 1.)
        source.write_bytes(pack_tensors(tensors, {"ss_base_model_version": "different-model"}))
        with self.assertRaisesRegex(ValueError, "adapter_config"):
            self.library.import_local([source])

    def test_malformed_configuration_values_fail_as_validation_errors(self):
        header = header_of(adapter_bytes(style="peft"))
        for config in ({"base_model_name_or_path": []}, {"r": True}, {"lora_alpha": []}, {"use_rslora": "false"},
                       {"lora_alpha": 10 ** 1000},
                       {"rank_pattern": {"q_proj": {}}}, {"rank_pattern": {"q.*": 2}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                lora_format.inspect_header(header, config)

    def test_peft_patterns_match_original_full_module_aliases(self):
        header = header_of(adapter_bytes(style="peft", rank=2))
        target = "model.layers.0.self_attn.q_proj"
        info = lora_format.inspect_header(header, {"r": 1, "lora_alpha": 1,
                         "rank_pattern": {target: 2}, "alpha_pattern": {target: 3}})
        group = info["groups"]["layers.0.self_attn.q_proj"]
        self.assertEqual(group["effective_alpha"], 3.)
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            lora_format.inspect_header(header, {"r": 2,
                "alpha_pattern": {target: 3, "q_proj": 4}})
        with self.assertRaisesRegex(ValueError, "rank"):
            lora_format.inspect_header(header, {"r": 2, "rank_pattern": {target: 4}})

    def test_native_expansion_cap_is_checked_before_compatible_or_import(self):
        a_size, b_size = 65536 * 2048 * 2, 12288 * 65536 * 2
        header = {"layers.0.mlp.gate_up_proj.lora_A": {"dtype": "BF16", "shape": [65536, 2048],
                    "data_offsets": [0, a_size]},
                  "layers.0.mlp.gate_up_proj.lora_B": {"dtype": "BF16", "shape": [12288, 65536],
                    "data_offsets": [a_size, a_size + b_size]}}
        with self.assertRaisesRegex(ValueError, "2 GiB"):
            lora_format.inspect_header(header)


class LibraryTests(LoRAFixture):
    def test_revision_is_content_addressed_and_metadata_edits_preserve_provenance(self):
        source = self.adapter()
        first = self.library.import_local([source], {"name": "A folk sound", "default_strength": .8})
        again = self.library.import_local([source])
        self.assertEqual(first["id"], again["id"])
        resolved = self.library.resolve([{"id": first["id"]}])
        self.library.update(first["id"], {"name": "New label", "default_strength": 2, "notes": "Use for folk.", "license": "User annotation"})
        restored = self.library.resolve_saved(resolved["loras"])
        self.assertEqual(restored["ar_lora_scale"], .8)
        self.assertEqual(restored["loras"][0]["name"], "A folk sound")
        self.assertEqual(restored["loras"][0]["license"], "")
        self.assertGreater(restored["memory_bytes"], 0)

    def test_resolution_rechecks_original_and_engine_and_manifest_content(self):
        for what in ("original_file", "engine_file", "manifest"):
            with self.subTest(what=what):
                isolated = LoRALibrary(self.root / what)
                item = isolated.import_local([self.adapter()])
                provenance = isolated.resolve([{"id": item["id"], "strength": 1}])["loras"]
                directory = isolated.root / item["id"]
                if what == "manifest":
                    path = directory / "manifest.json"
                    changed = json.loads(path.read_text())
                    changed["files"][0]["adapter_bytes"] = 0
                    path.write_text(json.dumps(changed))
                else:
                    path = directory / item["files"][0][what]
                    data = path.read_bytes()
                    path.write_bytes(data[:-4] + b"\0\0\0\0")
                with self.assertRaisesRegex(ValueError, "changed|corrupt"):
                    isolated.resolve_saved(provenance)

    def test_strengths_are_independent_and_zero_roles_can_be_mixed(self):
        a = self.library.import_local([self.adapter("a.safetensors", combined=True, style="comfyui")])
        b = self.library.import_local([self.adapter("b.safetensors", combined=True, style="comfyui", value=.25)])
        result = self.library.resolve([{"id": a["id"], "ar_scale": .6, "nar_scale": 0},
                                       {"id": b["id"], "ar_scale": 0, "nar_scale": -.2}])
        self.assertEqual(result["ar_lora_scale"], .6)
        self.assertEqual(result["nar_lora_scale"], -.2)
        self.assertIn(a["id"], result["ar_lora"])
        self.assertIn(b["id"], result["nar_lora"])
        self.assertEqual(self.library.resolve_saved(result["loras"]), result)
        with self.assertRaisesRegex(ValueError, "stacking"):
            self.library.resolve([{"id": a["id"]}, {"id": b["id"]}])
        zero = self.library.resolve([{"id": a["id"], "strength": 0}])
        self.assertNotIn("ar_lora", zero)
        self.assertEqual(zero["memory_bytes"], 0)

    def test_client_paths_and_unknown_controls_never_resolve(self):
        item = self.library.import_local([self.adapter()])
        for selection in ([{"id": "../../outside"}], [{"id": item["id"], "path": "/tmp/unsafe"}],
                          [{"id": item["id"], "strength": True}], [{"id": item["id"], "strength": float("nan")}],
                          [{"id": item["id"], "strength": 10 ** 1000}],
                          [{"id": item["id"], "nar_scale": 1}]):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                self.library.resolve(selection)

    def test_saved_hash_source_or_stage_changes_are_rejected(self):
        item = self.library.import_local([self.adapter()])
        saved = self.library.resolve([{"id": item["id"]}])["loras"]
        for key in ("source", "files", "stages"):
            edited = deepcopy(saved)
            if key == "source":
                edited[0][key] = {"type": "local", "files": ["elsewhere"]}
            elif key == "files":
                edited[0][key][0]["sha256"] = "0" * 64
            else:
                edited[0][key] = []
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.library.resolve_saved(edited)

    def test_saved_base_model_is_explicit_verified_and_legacy_receipts_are_enriched(self):
        item = self.library.import_local([self.adapter()])
        saved = self.library.resolve([{"id": item["id"]}])["loras"]
        self.assertEqual(saved[0]["base_model"], "m-a-p/YuE2-3B")
        changed = deepcopy(saved)
        changed[0]["base_model"] = "m-a-p/YuE-s2-1B-general"
        with self.assertRaisesRegex(ValueError, "base model"):
            self.library.resolve_saved(changed)
        legacy = deepcopy(saved)
        del legacy[0]["base_model"]
        enriched = self.library.resolve_saved(legacy)
        self.assertEqual(enriched["loras"], saved)
        self.assertNotIn("base_model", legacy[0])

    def test_delete_blocks_active_jobs_and_warns_for_saved_recordings(self):
        item = self.library.import_local([self.adapter()])
        self.library.references = lambda _: [{"kind": "job", "status": "queued", "id": "take1"}]
        with self.assertRaisesRegex(ValueError, "queued"):
            self.library.delete(item["id"], confirm=True)
        self.library.references = lambda _: [{"kind": "track", "id": "song1"}]
        warning = self.library.delete(item["id"])
        self.assertTrue(warning["confirmation_required"])
        self.assertTrue(self.library.delete(item["id"], confirm=True)["deleted"])
        self.assertEqual(self.library.list(), [])

    def test_symlink_source_and_installed_engine_are_rejected(self):
        source = self.adapter()
        link = self.root / "link.safetensors"
        link.symlink_to(source)
        with self.assertRaisesRegex(ValueError, "link"):
            self.library.import_local([link])
        item = self.library.import_local([source])
        engine = self.library.root / item["id"] / item["files"][0]["engine_file"]
        engine.unlink()
        engine.symlink_to(source)
        with self.assertRaisesRegex(ValueError, "link"):
            self.library.resolve([{"id": item["id"]}])


class HubTests(LoRAFixture):
    def test_url_paths_and_revisions_reject_ssrf_traversal_and_credentials(self):
        self.assertEqual(repository("https://huggingface.co/musician/yue2-test-lora"), REPO)
        self.assertEqual(revision(COMMIT, pinned=True), COMMIT)
        for value in ("http://huggingface.co/x/y", "https://user@huggingface.co/x/y", "https://huggingface.co.evil/x/y",
                      "https://huggingface.co/x/y?token=secret", "../model", "x/../y"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                repository(value)
        for value in ("../../secret", "file%2F..%2Fsecret", "a\\b", "/etc/passwd", "https://example.com/a"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                hub_path(value)
        for value in ("http://huggingface.co/x", "https://localhost/", "https://169.254.169.254/",
                      "https://huggingface.co.evil/", "https://huggingface.co:8443/a", "https://me@huggingface.co/a"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                hf_url(value)

    def test_header_inspection_is_bounded_and_falls_back_when_range_is_ignored(self):
        data = adapter_bytes()
        for ignores in (True, False):
            hub = FakeHub({"adapter.safetensors": data}, ignore_range=ignores)
            header = hub.header(REPO, COMMIT, "adapter.safetensors", len(data))
            self.assertEqual(lora_format.inspect_header(header)["stage"], "ar")
            self.assertEqual(len(hub.requests), 2)
            self.assertEqual(hub.requests[0][1]["Range"], "bytes=0-7")

    def test_detail_marks_compatibility_and_pins_readme_and_demos(self):
        self.library.hub = FakeHub({"adapter.safetensors": adapter_bytes(), "old-yue1.safetensors": b"not safe",
                                   "README.md": b"# A folk voice\n", "demo.mp3": b"demo"})
        detail = self.library.hub_detail(REPO)
        self.assertEqual(detail["revision"], COMMIT)
        self.assertTrue(detail["files"][0]["compatible"])
        self.assertFalse(detail["files"][1]["compatible"])
        self.assertIn("A folk voice", detail["readme"])
        self.assertEqual(detail["demos"][0]["mime"], "audio/mpeg")

    def test_detail_lists_unsafe_checkpoint_files_without_opening_them_and_pairs_prompts(self):
        self.library.hub = FakeHub({"adapter.safetensors": adapter_bytes(), "trainer.pt": b"executable pickle",
                                   "checkpoint.bin": b"executable pickle", "demos/folk.mp3": b"demo",
                                   "prompts/folk.txt": b"Soft acoustic guitar, warm lead vocal.",
                                   "prompts/other.txt": b"A different take"})
        detail = self.library.hub_detail(REPO)
        rejected = [file for file in detail["files"] if not file["compatible"]]
        self.assertEqual({file["path"] for file in rejected}, {"trainer.pt", "checkpoint.bin"})
        self.assertTrue(all(file["compatibility"] == "unsupported" for file in rejected))
        self.assertEqual(detail["demos"][0]["prompt"], {"path": "prompts/folk.txt",
                            "text": "Soft acoustic guitar, warm lead vocal."})
        requests = [url for url, _ in self.library.hub.requests]
        self.assertFalse(any(url.endswith(("trainer.pt", "checkpoint.bin", "other.txt")) for url in requests))
        self.assertTrue(all(COMMIT in url for url in requests))

    def test_hub_detail_cache_expires_and_callers_cannot_mutate_its_contents(self):
        self.library.hub = FakeHub({"adapter.safetensors": adapter_bytes(), "README.md": b"Original text"})
        with patch("loras.time.monotonic", return_value=100):
            first = self.library.hub_detail(REPO)
        requests = len(self.library.hub.requests)
        first["readme"] = "Caller changed its copy"
        with patch("loras.time.monotonic", return_value=101):
            second = self.library.hub_detail(REPO)
        self.assertEqual(second["readme"], "Original text")
        self.assertEqual(len(self.library.hub.requests), requests)
        with patch("loras.time.monotonic", return_value=401):
            self.library.hub_detail(REPO)
        self.assertGreater(len(self.library.hub.requests), requests)
        disabled = LoRALibrary(self.root / "disabled", hub=self.library.hub, cache_ttl=0)
        before = len(self.library.hub.requests)
        disabled.hub_detail(REPO)
        middle = len(self.library.hub.requests)
        disabled.hub_detail(REPO)
        self.assertEqual(middle - before, len(self.library.hub.requests) - middle)
        self.assertGreater(middle, before)

    def test_download_is_resumable_and_wrong_checksums_never_promote(self):
        data = b"x" * (1024 * 1024 + 20)
        hub = FakeHub({"adapter.safetensors": data})
        self.library.hub = hub
        info = {"path": "adapter.safetensors", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        destination = self.library.root / "download.safetensors"
        destination.parent.mkdir()
        stop = threading.Event()
        def cancel():
            if stop.is_set():
                raise Cancelled("cancel")
        with self.assertRaises(Cancelled):
            self.library._download(REPO, COMMIT, info, destination, cancel,
                                   lambda size: stop.set())
        partial = destination.with_suffix(".safetensors.part")
        self.assertEqual(partial.stat().st_size, 1024 * 1024)
        self.library._download(REPO, COMMIT, info, destination)
        self.assertEqual(hub.requests[-1][1]["Range"], "bytes=1048576-")
        self.assertEqual(destination.read_bytes(), data)
        destination.unlink()
        info["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.library._download(REPO, COMMIT, info, destination)
        self.assertFalse(destination.exists())
        self.assertFalse(partial.exists())

    def test_resume_restarts_safely_when_server_ignores_range(self):
        data = b"abcdef"
        self.library.hub = FakeHub({"adapter.safetensors": data}, ignore_range=True)
        destination = self.library.root / "download.safetensors"
        destination.parent.mkdir()
        destination.with_suffix(".safetensors.part").write_bytes(b"abc")
        info = {"path": "adapter.safetensors", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        self.library._download(REPO, COMMIT, info, destination)
        self.assertEqual(destination.read_bytes(), data)

    def test_background_install_completes_and_update_keeps_old_revision(self):
        self.library.hub = FakeHub({"adapter.safetensors": adapter_bytes(style="comfyui", combined=True)})
        def install(commit):
            task = self.library.hub_install({"repo": REPO, "revision": commit, "files": ["adapter.safetensors"]})
            deadline = time.monotonic() + 10
            while task["status"] not in ("completed", "failed", "cancelled") and time.monotonic() < deadline:
                time.sleep(.01)
                task = self.library.task(task["id"])
            self.assertEqual(task["status"], "completed", task)
            return task["result"]
        old = install(COMMIT)
        self.assertEqual(old["source"]["revision"], COMMIT)
        self.library.hub.commit = "b" * 40
        update = self.library.check_updates(old["id"])
        self.assertTrue(update["available"])
        new = install("b" * 40)
        self.assertNotEqual(old["id"], new["id"])
        self.assertEqual(len(self.library.list()), 2)
        self.assertEqual(self.library.get(old["id"])["source"]["revision"], COMMIT)

    def test_search_merges_metadata_and_text_discovery_and_supports_sort_and_paging(self):
        hub = HuggingFace()
        queries = []
        def json_response(url):
            params = parse_qs(urlsplit(url).query)
            queries.append(params)
            if "filter" in params:
                return [{"id": "artist/Folk", "tags": ["lora", "folk"], "likes": 3, "downloads": 2}], {}
            return [{"id": "artist/Folk", "tags": ["lora", "folk"], "likes": 3, "downloads": 2},
                    {"id": "artist/YuE2-jazz-lora", "tags": ["jazz"], "likes": 10, "downloads": 8}], {}
        hub.json = json_response
        self.assertEqual(hub.search(limit=1, sort="likes")["models"][0]["repo"], "artist/YuE2-jazz-lora")
        self.assertEqual(hub.search(limit=1, page=1, sort="likes")["models"][0]["repo"], "artist/Folk")
        self.assertEqual(hub.search(genre="folk")["models"][0]["repo"], "artist/Folk")
        self.assertTrue(any("filter" in q for q in queries))
        self.assertTrue(any("search" in q for q in queries))

    def test_search_keeps_paging_until_filtered_matches_and_has_no_page_cap(self):
        hub = HuggingFace()
        calls = []
        def json_response(url):
            calls.append(url)
            if "cursor=next" in url:
                return [{"id": "artist/rare-lora", "tags": ["folk"], "trendingScore": 5}], {}
            return [{"id": "artist/other-lora", "tags": ["jazz"]} for _ in range(30)], {
                "Link": '<https://huggingface.co/api/models?cursor=next>; rel="next"'}
        hub.json = json_response
        result = hub.search(genre="folk", sort="trendingScore", limit=1)
        self.assertEqual(result["models"][0]["repo"], "artist/rare-lora")
        self.assertEqual(result["models"][0]["trendingScore"], 5)
        self.assertTrue(any("cursor=next" in url for url in calls))
        self.assertEqual(hub.search(page=5000, limit=5000)["models"], [])

    def test_invalid_install_json_is_rejected_before_a_thread_starts(self):
        for files in ({}, [None], [[]], [{"path": "model.safetensors"}], ["../escape"]):
            with self.subTest(files=files), self.assertRaises(ValueError):
                self.library.hub_install({"repo": REPO, "revision": COMMIT, "files": files})
        self.assertFalse(self.library.tasks)

    def test_library_stage_and_license_filters_paginate_after_inspection(self):
        class SearchHub:
            def __init__(self):
                self.search_calls, self.inspections = [], []
                self.rows = [{"repo": f"artist/other-lora-{i}", "license": "mit", "compatibility": "inspect-required"}
                             for i in range(25)]
                self.rows.extend({"repo": f"artist/{name}", "license": "apache-2.0", "compatibility": "inspect-required"}
                                 for name in ("nar-lora", "ar-lora", "both-lora", "pickle-lora"))

            def search(self, query, limit, sort, genre, page):
                self.search_calls.append(page)
                return {"models": deepcopy(self.rows[page * limit:(page + 1) * limit]),
                        "has_more": len(self.rows) > (page + 1) * limit, "page": page, "limit": limit}

            def model(self, repo, ref):
                name = "adapter.pt" if repo.endswith("pickle-lora") else "adapter.safetensors"
                return {"sha": COMMIT, "siblings": [{"rfilename": name}]}

            def header(self, repo, ref, path, size):
                self.inspections.append(repo)
                return header_of(adapter_bytes(stage="nar" if repo.endswith("nar-lora") else "ar",
                                  style="comfyui", combined=repo.endswith("both-lora")))
        hub = SearchHub()
        self.library.hub = hub
        # A license-only filter can use model metadata without tensor requests.
        licensed = self.library.hub_search(license="apache-2.0", limit=1, page=1)
        self.assertEqual(licensed["models"][0]["repo"], "artist/ar-lora")
        self.assertEqual(hub.inspections, [])
        result = self.library.hub_search(license="apache-2.0", stage="ar", compatibility="compatible", limit=1, page=1)
        self.assertEqual(result["models"][0]["repo"], "artist/both-lora")
        self.assertEqual(result["models"][0]["stages"], ["ar", "nar"])
        self.assertEqual(result["models"][0]["compatibility"], "compatible")
        self.assertEqual(result["models"][0]["revision"], COMMIT)
        self.assertFalse(result["has_more"])
        self.assertTrue(all("other-lora" not in repo for repo in hub.inspections))
        rejected = self.library.hub_search(license="apache-2.0", compatibility="unsupported")
        self.assertEqual(rejected["models"][0]["repo"], "artist/pickle-lora")
        self.assertEqual(rejected["models"][0]["stages"], [])
        paired = self.library.hub_search(license="apache-2.0", stage="ar+nar")
        self.assertEqual(paired["models"][0]["repo"], "artist/both-lora")

    def test_media_uses_the_pinned_repo_file_and_checks_digest(self):
        self.library.hub = FakeHub({"demo.mp3": b"audio"})
        path, mime = self.library.media(REPO, COMMIT, "demo.mp3")
        self.assertEqual(path.read_bytes(), b"audio")
        self.assertEqual(mime, "audio/mpeg")
        path.write_bytes(b"wrong")
        repaired, _ = self.library.media(REPO, COMMIT, "demo.mp3")
        self.assertEqual(repaired.read_bytes(), b"audio")
        with self.assertRaises(ValueError):
            self.library.media(REPO, "main", "demo.mp3")


if __name__ == "__main__":
    unittest.main()
