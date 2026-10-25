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

## Metal runner eligibility (October 25, 2026)

[Run 40199464372](https://github.com/Flip-Engineering/riff/actions/runs/40199464372)
on c074c8a enumerated an Apple Paravirtual device with simdgroup reduction and
matrix multiplication both false. Baseline inference aborted on `RMS_NORM`
before any adapter run. This runner did not qualify Metal LoRA inference.

Setup, readiness and generation now check the selected device's simdgroup
reduction diagnostic from the pinned engine's model-free `--list-devices`
output. Missing or changed diagnostics fail closed. This checks the minimum
known YuE2 requirement; it does not establish support for every graph operation.
Unsupported devices offer CPU setup. The qualification workflow records a
`not-qualified` eligibility receipt and explicitly skips weights and inference
on confirmed unsupported hardware. Probe/build errors and inference failures on
eligible devices remain failures.

The paravirtual test fixture is captured from that run; the true-flag M-series
fixture is representative, not physical-device evidence. A physical Apple GPU
run remains required for actual Metal inference qualification. No new native
build or model inference was run for this guard.

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

## Repeatable CPU and hosted Metal inference qualification

`scripts/check_lora_inference.py` runs four cold native sessions against an
installed engine and the exact model/VAE/sidecars in `sources.json`: baseline,
AR 0 / NAR 0, AR 0.75 / NAR 0.5, and an adapted repeat. All use seed 1234,
25 semantic tokens, two midpoint steps, guidance 1, planning off and the same
instrumental TRBDR conditioning. These defaults describe a short experiment;
they do not cap other requested token or step counts.

The helper downloads only the qualification adapter: TRBDR at the revision above,
117,500,816 bytes, CC BY-NC 4.0. It uses `LoRALibrary.hub_install`, verifies the
pinned original and both normalized stage hashes, and re-verifies the library
before and after each adapted session. Existing compatible imports can be reused
with `--adapter-library`. No pickle files or model downloads are involved in the
helper. Base weights must already be installed and are only read; every pinned
file is SHA-256 checked before inference and again afterwards.

The zero-strength command deliberately passes both verified adapter paths and
zero scales to the native engine. It does not rely on the application filtering
zero-strength adapters away. PCM samples, semantic codes and acoustic payloads
must match baseline exactly. Nonzero outputs must differ and their repeat must
match exactly; native logs must confirm nonempty AR and NAR projection bindings.
The helper also checks semantic count, PCM format, acoustic checksums/provenance
and finite latents. A failed process or comparison leaves `receipt.json` marked
failed with completed measurements and local logs available.

Example from a checkout with a queue-built CPU engine and read-only pinned weights:

```sh
scripts/run_native_build_queued.sh cpu --build-only --jobs 2
python3 scripts/check_lora_inference.py --backend cpu --models fixtures/yue2 \
  --output test-results/lora-cpu --threads 2 \
  --metadata-arena model_weight_context=32 --metadata-arena vae_weight_context=32 \
  --metadata-arena ar_prefill_graph_arena=64 --metadata-arena ar_decode_graph_arena=32 \
  --metadata-arena nar_graph_arena=64 --metadata-arena vae_graph_arena=64
```

Set `TMPDIR` to an owned directory on the intended storage before local builds
and tests. `--binary` and `--build-receipt` select another installed engine and
its successful native build receipt. The engine key, binary/build receipt hashes,
runtime/patch pins, model hashes, adapter source/revision/license, exact recipe,
commands and output identities are written to the inference receipt. Recipe
metadata arena options use the earlier one-second smoke's explicit capacities;
they are adjustable native allocations, not duration, RSS or timeout guards.
Larger recipes may require different native capacities. The helper imposes no
wall-time or RSS abort threshold.

Every native `[TIMING]` sample is retained. AR stage elapsed times divided by
actual semantic count are reported separately; initialization/upload keys are
amortized costs, not steady-state decode speed. Native stages nest and must not
be added together. Process memory records each child's `wait4` peak RSS and a
sampled process-memory series. On macOS the latter is attributed physical
footprint, including attributed Metal allocations. When the pinned Metal allocator
emits `currentAllocatedSize` observations, the receipt records their maximum;
these rounded allocation-event samples are not continuous device-memory tracing.
Unavailable device observations are null rather than zero. Shared-host load,
page cache, initialization and sampling remain part of these cold observations.

### Manual hosted Metal run

`.github/workflows/metal-lora-inference.yml` is `workflow_dispatch` only and uses
`runs-on: macos-14`, the [hosted Apple Silicon runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
It verifies an exact 40-character source SHA and actual Darwin/arm64 identity,
builds through the same cached Metal build driver as Apple Silicon validation,
runs the native LoRA checks, then probes the device without model weights.
Confirmed unsupported hardware produces an explicit `not-qualified` receipt and
summary with device facts; weight download and inference steps are skipped, and
no LoRA inference qualification is claimed. Missing diagnostics or a failed
device probe fail the job. An eligible device still needs actual inference:
the workflow downloads pinned base weights with `fetch-models.py`, executes the
same helper on Metal, and retains any inference failure as a failed job. Normal
CI never downloads these model weights. An eligible manual job downloads about
2.93 GB of base/VAE/sidecars and the separate 117.5 MB adapter; its purpose is
qualification only.

After review and after the workflow exists on the default branch, the root can
run the following with the reviewed full commit substituted. GitHub
[requires the dispatch workflow on the default branch](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onworkflow_dispatch).
The PR supplies the exact dispatch command for its head.

```sh
gh workflow run metal-lora-inference.yml --repo Flip-Engineering/riff --ref main \
  --field source_sha=FULL_REVIEWED_40_CHARACTER_COMMIT
```

The job uploads build and inference receipts, hardware/toolchain identity and
logs on success or failure, excluding model weights, imported adapters and generated
audio. No hosted Metal inference result is claimed until that dispatched job's
receipt is reviewed. CUDA execution is a separate farm qualification and is not
part of this workflow.

### BF16 and graph-level reference parity: not done

No BF16-first or graph-level parity experiment was attempted here. A follow-up
should compare a fixed input to one AR and one NAR projection using the same
quantized base and adapter factors, separating the implemented BF16-rounded
load-time merge from `W_dequant*x + scale*B*(A*x)`. Comparing against the original
BF16 training checkpoint additionally requires separately approved, pinned
weights and its own error measurements; exact parity must not be inferred from
repeatable audio or zero bypass. Long/warm inference, other adapter ranks and
musical quality also remain separate acceptance work.

### October 24 CPU receipt

The [reviewed CPU receipt](evidence/yue2-lora-inference-cpu-2026-10-24.json)
records an actual run of helper commit `51105e980ec7e045edde5a9c16b7caa12c96b4c8`
with the pinned model/VAE/sidecars and imported TRBDR pair. The CPU engine was
built through `scripts/run_native_build_queued.sh cpu --build-only --jobs 2`;
its key is `a97282a0cd11f431-cpu`, and the successful build receipt is embedded.
The same binary also passed all 44 native loader cases; eight offline helper
checks and manual workflow/shell checks passed. The host was shared Linux,
AMD EPYC 7551, with two native threads. The Python importer/build are outside
the per-child inference memory measurements.

All four native sessions exited successfully, each producing 25 semantic tokens,
25×64 finite acoustic values and 47,936 stereo PCM16 frames at 48 kHz
(0.998667 s). Baseline and explicit-path zero-strength runs have identical WAV,
PCM, semantic and acoustic-payload hashes. The adapted run changed each of those
outputs and its repeat reproduced them exactly. Both nonzero roles logged
196 bound projections. No musical quality judgment follows from this short take.

| Condition | Whole process s | AR weight upload s | NAR weight upload s | AR decode ms/output token | Peak native RSS GB (`wait4`) |
| --- | ---: | ---: | ---: | ---: | ---: |
| No adapter | 64.596 | 3.581 | 1.539 | 136.243 | 2.804 |
| AR 0 / NAR 0, paths present | 66.050 | 2.289 | 1.317 | 138.186 | 2.804 |
| AR 0.75 / NAR 0.5 | 160.670 | 50.832 | 40.578 | 214.530 | 3.148 |
| Same adapted take, repeat | 135.894 | 40.808 | 38.525 | 140.209 | 3.150 |

The large cold-session increase is concentrated in weight upload/merge, as the
retained stage arrays show. These are contended observations, not a controlled
steady-state performance comparison; the two adapted decode observations alone
range from 140 to 215 ms/token. The two measured adapted peaks are about 0.34 GB
above baseline, not a general memory bound. Native RSS and the sampled `/proc`
high-water observation are both retained and can differ slightly; device memory
is not applicable on this CPU run. Complete original receipts, memory series,
logs and generated outputs are retained locally under the referenced
`test-results/` paths and are not in Git. No hosted Metal run, CUDA run,
BF16-reference comparison or graph-level reference comparison was performed.
