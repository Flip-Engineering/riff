# YuE2 LoRA validation

Checked October 19, 2026. This record accompanies the native adapter backport,
import library, studio controls and [training handoff](yue2-lora-research.md).
It separates software validation and a short real-model CPU smoke from musical
quality, GPU acceptance and training evidence.

## Completed checks

| Boundary | Evidence |
| --- | --- |
| Pinned native source | The existing `fbe3eedbf6c504e45189e2cdcf1b257740a28863` runtime plus all 14 patches applied and built as a CPU production CLI; patch bytes and SHA-256 verified against `sources.json` |
| Native arithmetic and loading | 44 passing cases with real CPU ggml tensors and a tiny generated Q4 GGUF; native/PEFT/Comfy mappings, BF16/F16/F32 inputs, six upload storage modes, fused shapes, independent strengths, full replacements versus dense differences, invalid files, repeated loads and exact zero bypass |
| Full Python regression | 282 tests, successful with 3 platform/runtime skips in the initial complete run; pinned CMake available for native format checks |
| Previously unavailable artifact integration | All 24 score/acoustic integration tests then passed with `RIFF_REQUIRE_SCORE_INTEGRATION=1`; the remaining platform exclusion is macOS Keychain on Linux |
| Elixir contracts | 15 resource-estimator, 8 admission and 5 native-capability tests passed; production CLI capabilities verified against the exact binary SHA; formatting passed |
| Adapter backend | 35 focused tests included in that suite: import scaling, combined files, corruption, references, real-layout headers, pagination/filters/cache, checksum failure, cancellation/resume and revision updates |
| CLI and queue | 25 focused tests included in that suite: complete writer/producer recipes, saved sound reuse, immutable receipts, zero on an older engine, independent strengths, and changed files before/after execution |
| Browser | Nine LoRA scenarios using the real local HTTP/import/queue path and explicit Hub/model fixtures; desktop and 390px phone layouts, plus the adapter modal at 320px |
| Existing studio behavior | State-refresh, writer, review, saved-acoustic and acoustic-comparison browser suites passed with the extended recipe contract |
| Training preparation | Seven tests included in the Python suite: sidecars, separate deterministic splits, duplicate detection, licenses, configuration and evidence validation |
| Release packaging | Curated archive includes the adapter modules, UI, native patch, training helper and public evidence; verified installation, real HTTP startup, fallback and persistent workspace all passed |

The browser checks cover search, sort and compatibility/stage filters, safely
rendered model cards, same-origin demo playback, actual local imports, paired
installation, metadata edits, explicit trigger insertion, independent AR/NAR
strengths, queue receipts, draft restoration, update installation beside an old
revision, references-aware deletion, unavailable entries, disabled Use and Hub
outages. They also cover Unicode PEFT configuration headers, pinned relative
model-card links and recovery from a download task lost after a server restart.
They keep the original CSP and make no browser-side Hub requests.
The 320px check is scoped to the adapter modal; the existing page header/player
have a separate minimum-width issue at that width.

The native harness and its limits are documented in
[`tests/native_lora/README.md`](../tests/native_lora/README.md). Native CI runs
the CPU numerical harness in CPU and Metal builds. CUDA remains a compilation
gate; a CPU harness inside a Metal build is not Metal inference evidence.

## Actual public file import

The live Hub client retrieved and fully validated
[`becausereasons/yue2-trbdr-folk-troubadour`](https://huggingface.co/becausereasons/yue2-trbdr-folk-troubadour)
at revision `078b19e52baebaa71463ea94afac5ce13abfe29a`:

| Artifact | Bytes / SHA-256 |
| --- | --- |
| `trbdr_broadside.safetensors` | 117,500,816 bytes; `68e3aca03e7aa7ddf8a2ee5cf46ce68c2c18fc9fe432cc60b838b9f0e9b8171f` |
| Normalized AR | `2e505269c924acb57fc2d43349d5699365398e86c42ac4f9bcb45673f9e84e75` |
| Normalized NAR | `5149299f325cd50bc4f996a2c611e39cae96eddc5bbdcb4da020af927ab40b41` |

Download, published size/SHA verification, finite-value scan, normalization,
stage splitting and saved-receipt verification completed in 15.791 seconds in
this environment. Each stage contains 112 adapter targets, rank-32 factors and
336 normalized tensors including explicit alpha scalars. Fused targets bind to
196 separate native projections per stage. The combined original is retained
once. This establishes the complete file path beyond header-only inspection.

## Real model CPU smoke

The production binary loaded the exact Q4 main model and F16 VAE from
`sources.json`, first without adapters and then with the imported TRBDR pair.
The binary SHA-256 was
`a73b1605147a55f108348832e994fb15d7be7f28b4c7762b17ca9b0fe77a3889`.

Both requests used two CPU threads, seed 1234, instrumental conditioning,
planning off, 25 semantic tokens, guidance 1 and two midpoint synthesis steps.
The fixed model context remained 24,576; actual AR cache allocation for this
short request was 58 tokens. The container had an 8 GiB memory limit.

| Observed result | Original model | AR 0.75 / NAR 0.5 |
| --- | ---: | ---: |
| Complete process wall time | 23.036 s | 64.594 s |
| Peak process RSS (`wait4` / Linux `rusage`) | 2,902,138,880 bytes (2.7028 GiB) | 3,292,590,080 bytes (3.0665 GiB) |
| Audio frames | 47,936 | 47,936 |
| Audio format | Stereo, 48 kHz PCM16 | Stereo, 48 kHz PCM16 |
| Duration | 0.998667 s | 0.998667 s |
| Acoustic checkpoint | 25 × 64 F32 values, all finite | 25 × 64 F32 values, all finite |
| Absolute PCM peak | 0.050964 | 0.035400 |
| Full-scale PCM samples | 0 | 0 |

Both runs exited successfully. The adapted run logged both adapters and their
196 bound projections per stage. Repeating the baseline produced identical
semantic and audio hashes. The adapted semantic and audio hashes differed.
No OOM or OOM-kill event occurred. The
[public receipt](evidence/yue2-lora-cpu-2026-10-19.json) records commands, model,
adapter and output identities, output checks and measurement scope.

These are whole-process observations from one short recipe, including model
loading and adapter merging. They are not steady-state per-token benchmarks,
general memory requirements, warmed-session measurements, or a prediction of
full-song latency. One second and two synthesis steps are sufficient for a
pipeline smoke, not a useful listening comparison of a folk-song adapter.

## Numerical and implementation decision

Issue #24 originally proposed graph-level `W*x + s*B*(A*x)` updates. Current
audio.cpp provides a native session-load merge path. Riff backports that path
over its existing runtime and adds missing ComfyUI NAR/dense-difference support
and validation, rather than introducing a second family of inference kernels.
The original GGUF bytes remain immutable. Adapters are applied from that base
when weights are loaded; changing adapters or strengths starts a new session.
Zero bypass preserves the original quantized bytes exactly.

The merge follows upstream's BF16 rounding policy: round the scaled delta to
BF16, add it to the dequantized base, round the result to BF16, then use the
requested inference storage. A Q4 base is therefore merged from its Q4 values
and requantized. This is not numerically identical to a graph-level low-rank
update or a merge into the original BF16 training checkpoint. The native tests
use an independent reference for the implemented rounding policy. They do not
claim parity with those other algorithms.

Admission includes F32 adapter expansion, extra retained A-factor copies for
fused bindings and bounded merge scratch before the runtime's uncertainty
margin. The native implementation retains one weight merge buffer and a row
of product scratch, without retaining another complete merged model on the
host. The estimate is conservative bookkeeping; the CPU RSS observation above
does not calibrate CUDA VRAM or Metal unified memory.

Upstream already contains the core session adapter path. The remaining
ComfyUI NAR mapping/difference fixes and parser checks are candidates for a
separate upstream contribution after porting them onto current main and
running that repository's tests. This Riff change does not open or claim an
upstream PR.

## Remaining acceptance work

- Real CUDA and Metal inference with the same public adapter and captured
  receipts; short and long requests, warm reuse and changed-strength restarts.
- BF16-reference and graph-level comparisons if deciding whether the Q4
  merge fidelity warrants another implementation. ComfyUI audio-reference
  comparisons remain separate from the verified tensor mapping.
- Matched, warmed per-stage/per-token timing across useful adapter ranks;
  full-song memory and listening evidence. The current cold CPU smoke does
  not close those performance questions.
- A new training run using licensed source recordings and the prepared
  handoff, with realized dependency/cache pins, checkpoint identity and the
  held-out baseline/AR-only/NAR-only/combined evaluation plan completed.

The helper writes a preparation manifest and an **uncompleted** evaluation
plan. No new Riff LoRA was trained in this work. Public metadata or a valid
header cannot prove a producer's exact base weights, the suitability of a
companion model, or perceptual quality.
