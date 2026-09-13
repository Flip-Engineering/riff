# Riff runtime

The reusable admission and memory-estimation policy lives in Elixir. The
installer compiles `lib/` into its ERTS release. Its private entry point is
`Riff.Runtime.SchedulerPort.main/0`, called with `eval`, `RELEASE_DISTRIBUTION=none`,
and newline-delimited JSON on stdin/stdout. EOF exits the process. It does not
start the graphical installer, an HTTP listener, or a global daemon.

`RIFF_RUNTIME_CONTROL` identifies the installed control executable. The current
Python studio uses `model_admission.py` as a transitional process bridge. Source
checkouts with already compiled scheduler and Jason modules can run the same
entry point through Mix without compiling or fetching dependencies; older source
installations without the control runtime retain serial model exclusion and
cancellable waiting. They do not claim memory-aware overlap. Human desktop
installations bundle the runtime and do not require a language installation.

The current studio integration overlaps one standalone local writer with its
ordered native music/score/performance queue when reservations fit. Each process
owns its cancellation and reservation. Multiple simultaneous native jobs and
independent writing drafts remain a separate dispatch/UI integration task; the
Elixir policy itself has no job-count ceiling.

## Admission contract

Each request has an operation ID, an estimated `host_peak`, optional `device`
UUID and `device_peak`, a fresh host/device snapshot, and measured usage by active
operation ID. Requests and polls reconsider waiting work. Release/cancel removes
only the named reservation. Host and CUDA memory are separate budgets. Metal is
charged to unified host memory. Missing host observations and host pressure defer
new work. Missing device observations permit only the existing lone-model path;
they never justify overlap. Requested generation settings stay unchanged.

OS available memory already excludes current residents. Admission therefore
reserves only the remaining growth of active models, plus each newly admitted
model's complete estimated peak. The queue scan is linear in active and waiting
work. A request waiting on a GPU does not block independent CPU work that fits.
A conservative estimate must not become a new solo-generation budget: with
normal host pressure and an available-memory observation, the oldest operation
can run on its own even when its estimate exceeds headroom. That admission
records `admission: solo`, its uncertainty, and exclusive ownership. Peers wait
until it releases. Warning/critical/unknown pressure still defers new work;
inputs are never truncated or silently changed.
After a policy crash, existing process ownership remains in the bridge; new work
waits until those owners release before the policy can restart safely.

Estimates use installed weight sizes and model configuration, effective
generation-sidecar context and planning budgets, explicit recipe overrides,
conditioning text, requested music duration, CFG branches, and decoder chunks.
Native CPU semantic KV uses four-byte elements; device KV and the local BF16
writer use two-byte elements. The writer prefill reservation follows the
installed `mlx_lm.generate_step` default of 2048 tokens. The pinned YuE2 native
planning fallback is 4096 tokens (`assets.cpp`); sidecar and recipe values take
precedence. These estimates reserve capacity and never truncate model inputs.

The uncertainty margin (`RIFF_MODEL_MEMORY_MARGIN`, default 0.2) covers allocator
and backend scratch variation; it is an estimate, not a memory guarantee. An
optional `RIFF_MEMORY_RESERVE_MB` adds explicit host headroom. The IPC timeout
(`RIFF_SCHEDULER_TIMEOUT`, default 30 seconds) concerns the small policy process,
not a writing or music budget. Current measurements sample macOS attributed
process footprint or Linux RSS and selected CUDA process usage. Linux cgroup
ancestor limits constrain host availability; available PSI full-stall readings
defer new work while memory stalls settle. The [kernel PSI contract](https://docs.kernel.org/accounting/psi.html)
and [cgroup hierarchy](https://docs.kernel.org/admin-guide/cgroup-v2.html) define these observations.
Real CUDA overlap remains unverified until
it is measured on that hardware.

## Focused validation

Run each Elixir test entry point with `elixir runtime/test/admission_test.exs`
and `elixir runtime/test/resources_test.exs`. Python bridge/observation cases are
in `tests/test_admission.py`; process ownership and studio cancellation cases are
in `tests/test_studio.py`. The latter use a controllable admission boundary, so
they do not load models or require Elixir on unrelated application test jobs.
Actual packaged-runtime and model-overlap receipts are kept in the private
research directory, separately from automated fixture proof.

## Saved scores

`Riff.Runtime.ScoreArtifact` validates and stores exact native planning tokens.
An immutable descriptor binds their bytes, tokenizer/format contract and launch
provenance. Capture and job-copy publication sync files and directories; retries
verify existing bytes. The Python library adapter records references and derives
readable notation from the captured tokenizer snapshot. This work shares the
existing control VM with admission.

`artifact_start`, `artifact_result` and `artifact_cancel` supervise file operations
without holding the scheduling request channel for their duration. Results are
consumed once; cancellation affects only its owned worker. Resolve accepts the
recorded contract for historical viewing, while prepare requires an explicit
compatible contract. Queue shutdown interrupts pending operations and retains
launch receipts for recovery. User cancellation can salvage a completed score.

Tests in `installer/test/score_artifact_test.exs` and `scheduler_port_test.exs`
cover publication, exact integer/key parsing and concurrent admission. The
Python-to-Elixir integration suite is `tests/test_score_integration.py`; control
CI runs it explicitly, while application jobs without Elixir report a skip.
