# LoRA library API

Riff's local library accepts the published **YuE2-3B** architecture, including the
Comfy-Org repackaging. AR adapters affect composition; NAR adapters affect acoustic
rendering. The library stores original safetensors, normalized native stage files
and an immutable receipt. Python uses only the standard library.

## Supported file layouts

| Export | Accepted tensors | Scaling |
| --- | --- | --- |
| Native YuE2 | `layers.N.self_attn.*.lora_A/B`, `layers.N.mlp.*.lora_A/B`; NAR uses `nar_self_attn` / `nar_mlp` | Unit `B @ A` unless explicit alpha or finite `lora_scale` metadata specifies otherwise |
| ComfyUI | `text_encoders.model.layers.*` for AR; `diffusion_model.model.layers.*` for NAR; `lora_down.weight` / `lora_up.weight` | Alpha divided by rank when supplied; otherwise unit `B @ A` |
| AI Toolkit YuE2 transformer export | The same AR/NAR namespaces with `lora_A.weight` / `lora_B.weight`; can combine both stages in one file | Unit `B @ A` for the verified AI Toolkit producer convention below |
| PEFT LoRA | `lora_A.weight` / `lora_B.weight`, including `default.weight` spelling | Requires `adapter_config.json` or explicit alpha; literal rank/alpha module patterns (including original full-module aliases) and RS-LoRA configuration are supported |
| NAR full projections | `vae2llm.weight`, `.bias`, `llm2vae.weight`, `.bias` | Full replacements at any nonzero NAR strength |
| ComfyUI NAR projection differences | `diffusion_model.vae2llm.diff`, `.diff_b`, `diffusion_model.llm2vae.diff`, `.diff_b` | Additive differences multiplied by NAR strength |

Ordinary attention projections are Q, K, V and O. MLP projections are gate, up and
down. Fused `qkv_proj` and `gate_up_proj` exports retain their factors; the native
engine binds their output slices to the respective model projections. Combined
AR/NAR files are normalized into separate stage files while retaining one original.
A fused projection and one of its component projections cannot coexist in a stage.

AI Toolkit's YuE2 export is identified by both `ss_base_model_version=yue2` and its
`software` JSON identifying `ai-toolkit` from `https://github.com/ostris/ai-toolkit`,
together with the expected YuE2 namespaces. The inspected producer
[sets transformer alpha equal to rank](https://github.com/ostris/ai-toolkit/blob/04733873cf1da8060fce01316ec45daedbd4a918/toolkit/lora_special.py),
[omits alpha while saving PEFT factors](https://github.com/ostris/ai-toolkit/blob/04733873cf1da8060fce01316ec45daedbd4a918/toolkit/network_mixins.py),
and rewrites YuE2's AR/NAR namespaces. Generic PEFT exports without scaling metadata
remain ambiguous and are rejected. Producer metadata establishes a format convention;
it is not an authentication mechanism or an endorsement of a model's creator.

Normalized files have `riff_format=yue2-lora-v1`, native stage names, and explicit
F32 alpha tensors. Factor and full-projection bytes retain their original F32, F16
or BF16 values. Projection differences remain differences, not replacements.

The validator checks the YuE2-3B dimensions (28 layers, hidden size 2,048, MLP size
6,144, query size 2,048, key/value size 1,024, acoustic latent size 64), rank pairing,
tensor offsets and exact payload length. It rejects unknown modules, unknown
algorithms such as DoRA/LoKr, invalid or contradictory stage metadata, nonfinite
values, unsupported dtypes, symlinks, and executable checkpoint formats. It never
unpickles `.pt`, `.bin` or other checkpoint objects. Parser resource limits are 16 MiB
for a safetensors header, 4,096 tensors, and 8 GiB for an adapter file. These are
input-validation limits, not model-memory guarantees. Header compatibility also
checks the native loader's 2 GiB limit on expanded F32 data per normalized stage,
including alpha tensors emitted by normalization.

## HTTP endpoints

All routes use the studio's existing same-origin request checks.

| Method and route | Request | Response |
| --- | --- | --- |
| `GET /api/loras` | — | `{loras: [manifest], enabled: boolean}` |
| `GET /api/loras/{id}` | — | Manifest with current references |
| `POST /api/loras/import` | Streamed `application/octet-stream` safetensors upload; filename and metadata use the studio upload controls | Installed manifest |
| `PATCH /api/loras/{id}` | Any editable metadata fields below | Updated manifest |
| `DELETE /api/loras/{id}` | `{confirm: false}` or `{confirm: true}` | Deleted receipt, or a references warning requiring confirmation |
| `GET /api/loras/hub` | `q`, `genre`, `license`, `stage`, `compatibility`, `sort`, `page`, `limit` query parameters | `{models, page, limit, has_more, query, genre, sort}` plus selected filters |
| `GET /api/loras/hub/detail` | `repo`, optional `revision` (defaults to `main`) | Revision-pinned repository details, compatible files, README text and demo files |
| `GET /api/loras/hub/media` | `repo`, pinned `revision`, repository `file` | Verified same-origin audio/video with byte-range playback |
| `POST /api/loras` | Hub installation request below | Download task with HTTP 202 |
| `GET /api/loras/tasks/{task_id}` | — | Download progress and terminal result |
| `POST /api/loras/tasks/{task_id}/cancel` | `{}` | Current task; cancellation is cooperative |
| `GET /api/loras/{id}/updates` | — | Current vs latest revision; never modifies an installed adapter |

Search merges base-model adapter/finetune metadata queries with a YuE2 text search,
deduplicates repositories, and applies text/tag and genre terms. It follows Hub
pagination until enough matching models are found or the results are exhausted.
Sort values are `downloads`, `likes`, `trendingScore`, and `lastModified` (also
`last_modified`). `page` is zero-based; callers choose any positive `limit`.
Transport requests use at most 100 models per Hub page, without a fixed result-page
ceiling in Riff.

The Python method `hub_search` accepts the same fields with `query` in place of
`q`. Optional `license` matches declared license identifiers. `stage` accepts `ar`,
`nar`, or `ar+nar` (`both` and `paired` are aliases). `compatibility` accepts
`compatible`, `unsupported`, or `unverified`; `header-compatible` is an alias for
`compatible`. Stage and compatibility filters inspect repository details before
applying pagination. Their result rows include checked `stages`, aggregate
`compatibility`, and the pinned `revision`. License-only filtering uses metadata
and default browsing retains `compatibility: "inspect-required"` without a header
request for every result. Connection/access errors remain unverified.

Hub search and detail responses use a short-lived, process-local cache. Its default
lifetime is 300 seconds; set `RIFF_LORA_CACHE_SECONDS` or pass
`LoRALibrary(..., cache_ttl=seconds)` to change it. Zero disables caching. Constructor
options `cache_entries` and `cache_bytes` bound the cache (defaults: 64 entries and
32 MiB). Cache responses are copied before returning, so callers cannot modify
stored results. Explicit update checks bypass the cache for the latest revision.

File details use two bounded HTTP range requests to inspect safetensors headers.
`compatible: true` and `compatibility: "header-compatible"` indicate that module
names and shapes fit the supported architecture. They do not claim payload integrity
or listening quality; the complete download is verified before installation.
Unsupported files include a `reason`. A combined file has `stage: "ar+nar"` and
`stages: ["ar", "nar"]`.

Repository `.pt`, `.pth`, `.bin`, `.ckpt`, `.pkl`, `.pickle` and `.gguf` files appear
as unsupported alternatives with a reason; their bodies are never opened or fetched
by inspection or installation. This keeps unsupported training checkpoints visible
without interpreting executable objects.

Adapter metadata remains available in each inspected file's `metadata` object.
In particular, a producer's `pair_with` entry names a declared companion; it does
not identify a dependency that Riff has installed or verified. For example, the
published `nar_lora_joint_v9.bf16.safetensors` declares
`pair_with: tokenizer_head_joint_v9`. Its module dimensions can be compatible
while its intended producer workflow also uses a trained tokenizer/head. Display
that pairing alongside compatibility, and consult the pinned model card before
assuming a standalone adapter reproduces the producer's results. Riff neither
fetches nor switches companion models automatically.

Model-card content is returned as plain text. The studio must render that text safely;
it must not execute embedded HTML, scripts or repository instructions. Demo media is
limited to published audio/video files within the selected pinned repository revision.
When a demo has one same-stem text file under `prompts/`, its record includes
`prompt: {path, text}`. Prompt retrieval is revision-pinned and limited to 64 KiB per
file; ambiguous matches are not silently paired. Overlarge or inaccessible prompt
files carry an error instead of content.

### Hub installation

```json
{
  "repo": "creator/yue2-adapter",
  "revision": "0123456789abcdef0123456789abcdef01234567",
  "files": ["adapter.safetensors"],
  "name": "Folk sound",
  "default_strength": 0.8,
  "tags": ["folk"],
  "trigger_words": ["fingerpicked guitar"]
}
```

The revision must be a full 40-character commit SHA returned by the details endpoint.
Select either a combined export, one stage, or one file for each stage. Equivalent
native and ComfyUI exports for the same stage are alternatives. The installer also
downloads the colocated PEFT configuration when needed. Configuration bytes receive
their own checksum and are included in the installed identity.

Downloads use a single serialized worker per library. A task has:

```json
{
  "id": "32-character task identifier",
  "status": "downloading",
  "downloaded": 1048576,
  "total": 70301896,
  "error": null,
  "result": null
}
```

Status progresses through `queued`, `inspecting`, `downloading`, `validating` and
`completed`, or ends in `failed` / `cancelled`. A completed task's `result` is the
installed manifest. Task records live in the running studio process; partial files
survive a restart and are reused by a new installation request for the same revision.
Cancellation is checked between bounded reads, hash chunks and validation chunks;
an outstanding HTTP request has a 30-second timeout.

File bodies stream to `.part` files. Resume requests validate the returned byte
range; servers that ignore Range cause a clean restart. Exact byte length and
Hub-published SHA-256 (or Git blob SHA-1 for ordinary small repository files) must
match before promotion. Failed checksum checks remove the corrupt partial. Final
installation is an atomic directory rename after every selected file passes.
Hugging Face HTTPS URLs and every redirect are restricted to its `huggingface.co`
and `hf.co` domains. The browser currently supports public, anonymous repositories.

## Manifest and execution receipts

Installed IDs are 64-character SHA-256 content identities covering schema, base
model, stages, source, original and engine filenames/hashes/sizes, configuration
hashes, inspected targets, and memory estimates. Changing editable metadata does
not change the ID. Source provenance records the repository, pinned revision and
selected files; local uploads retain their original filenames. The manifest's
`files` list has one descriptor per normalized stage, including:

```json
{
  "stage": "ar",
  "original_name": "adapter.safetensors",
  "original_file": "original-0.safetensors",
  "sha256": "SHA-256 of original safetensors",
  "bytes": 123456,
  "engine_file": "ar.safetensors",
  "engine_sha256": "SHA-256 of normalized engine file",
  "engine_bytes": 123789,
  "targets": ["layers.0.self_attn.q_proj"],
  "rank": [32],
  "format": "native",
  "projection_mode": [],
  "config_file": null,
  "config_sha256": null
}
```

Editable fields are `name`, `notes`, `license`, `tags`, `trigger_words`, and
`default_strength`. `license` records a creator's stated license or the user's
annotation; Riff does not infer rights from model compatibility. Trigger words are
stored as suggestions for the artist and do not secretly alter generation inputs.

Generation recipes select library IDs:

```json
{
  "loras": [
    {"id": "64-character library ID", "ar_scale": 0.8, "nar_scale": 0.6}
  ]
}
```

`strength` is a shared default; stage-specific `ar_scale` / `nar_scale` override it.
Numbers must be finite and representable as F32; negative strengths are allowed.
Zero fully disables that stage. Up to two entries are supported, with at most one
nonzero adapter per stage. Two paired adapters can therefore contribute opposite
stages by setting their unused strengths to zero. General stacking is unsupported.

`LoRALibrary.resolve()` validates selections, verifies original/configuration/engine
SHA-256 values, and returns authoritative `loras` receipts plus absolute native
stage paths and their strengths. Queued takes save those receipts in
`lora_provenance`. `resolve_saved()` checks the same IDs, source, hashes, stages and
captured strengths before execution. Receipts explicitly record `base_model`;
conflicting values fail validation. Earlier receipts that omit this field are
enriched from their verified content identity, which already covers the base model.
Missing or changed adapters fail clearly;
execution never silently falls back to the base model. Recorded names and license
annotations remain in the historical receipt even after library metadata changes.

The returned `adapter_bytes`, `merge_scratch_bytes` and `memory_bytes` estimate the
host increment for expanded F32 factors and conservative merge scratch. They are
admission estimates, not measured peak-RSS or quality claims. A zero-strength stage
contributes no active adapter memory.

Deletion consults references supplied by the recording store. Queued, running and
cancelling jobs block deletion. References from completed takes require explicit
confirmation; audio and historical receipts remain intact. An update installs beside
the previous revision so that old takes retain their exact dependencies.

## Verification

Run `python3 -m unittest discover -s tests -p test_loras.py -v`. Fixtures cover
normalization, fused targets, combined exports, current AI Toolkit conventions,
PEFT scaling, projection deltas vs replacements, malformed files, nonfinite data,
corruption and provenance edits, independent stage strengths, references-aware
deletion, safe Hub URLs, range fallback/resume, checksum failure, task completion,
revision updates, filtering/pagination, and verified demo media. The tests use small
synthetic adapters and a fake Hub; they do not need GPUs, model weights or accounts.
