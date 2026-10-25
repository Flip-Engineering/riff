# Warm engine handoff (branch `codex/warm-engine`, based on 4bc4de0 / Riff 0.6.17)

## What changed

**Native (audio.cpp patch `patches/yue2-warm-engine.patch`)**

- New session option `yue2.keep_resident` (default `false`). With `false`, the
  pipeline behaves exactly as before: AR and NAR runtimes are destroyed after
  NAR synthesis in every request. With `true`, AR/NAR (and the VAE, which was
  already cached) stay constructed, so their weights are uploaded once per
  process. Each request still starts and ends with no graphs, KV caches or AR
  prefix snapshots (`release_runtime_graphs()` before and after the request,
  including on exceptions), so request N+1 behaves like a fresh process.
  RNG and NAR noise were already seeded per call.
- `audiocpp_cli --jobs -`: after building the session once, reads one JSON
  job per stdin line (`build_request_from_json` fields plus `id`, `out`,
  `metrics`). Output protocol `riff.jobs.v1`:
  `@@riff-engine-ready riff.jobs.v1`, then per job
  `@@riff-job-begin <id>` ... normal log/TIMING/metrics lines ...
  `@@riff-job-end <id> status=ok` or
  `@@riff-job-end <id> status=error [fatal=1] message=...`.
  A failed job does not stop the process, except errors containing
  `compute failed`, which end the job with `fatal=1` and exit status 3.
  EOF on stdin exits with status 0. Command-line `--request-option` values are
  defaults for every job.
- The engine advertises `feature.yue2.keep_resident=1` and
  `format.yue2.jobs=riff.jobs.v1` in `--help`/`--inspect`; both are added to
  `native_capabilities` in `sources.json`, so desktop builds verify them.
- The pipeline-level `release_runtime_graphs()` now releases NAR before AR,
  because the NAR graph borrows the AR prefix snapshot. Nothing on the default
  path calls it.

**Patch name.** The request named `yue2-keep-resident.patch`, but every
consumer (`setup_engine.py`, `desktop/build_engine.exs`, the native check
scripts) applies patches in sorted filename order, and that name sorts before
`yue2-score-replay.patch`. The patch is named `yue2-warm-engine.patch` so it
applies last. `scripts/check_native_score_replay.exs` requires explicit review
of patches after score replay; the new patch is added to its list, with a
comment explaining that the default path is unchanged.

**Riff (Python)**

- `warm_engine.py`: `job_from_command` turns the existing per-take command from
  `run.build_command` into (session arguments, JSON job). Anything it does not
  fully recognise returns `None` and uses the per-take process. `WarmEngine`
  starts `audiocpp_cli ... --session-option yue2.keep_resident=true --log --metrics --jobs -`,
  writes each job, and copies the output between the markers into that take's
  `data/<job>.log`. `WarmJob` provides the Popen methods the studio uses
  (`pid`, `poll`, `wait`, `terminate`; there is no `kill`).
- Restart rules: the engine restarts when its command (binary, model root,
  model/VAE files, backend, device, threads, session options) or the stat
  identity of the binary, GGUF files or sidecars changes; after a crash; after
  `fatal=1`; after an unexpected marker; and after a cancel. Cancel sends
  SIGTERM and waits. An idle engine is stopped by closing stdin; if it has not
  exited after `RIFF_WARM_ENGINE_STOP_SECONDS` (default 30) it gets SIGTERM.
  SIGKILL is never sent.
- `studio_core.Generator.execute`: when `warm_engine` is enabled and the
  selected binary advertises job mode, the take runs in the warm engine;
  otherwise the per-take process path is unchanged. Turning the setting off
  stops the idle engine before the next take. Metrics gain
  `"engine": "warm"|"process"`, and `command` still records the equivalent
  per-take command. For warm takes the memory peak is the sampled footprint
  during the take (the process lifetime peak covers earlier takes).
- `platform_support.settings()/configure()`: boolean `warm_engine`, default
  `false`. Other settings validate as before, and unknown keys are still rejected.
- `run.engine_capabilities()` reports `warm_engine`.

## Mode coverage in warm mode

| Take type | Warm engine |
|---|---|
| Music render (cot off/melody/full, ABC input) | yes |
| Plan only (`plan_only`) | yes |
| Performance only (`semantic_only`) | yes |
| Sound synthesis only (`acoustic_only`, `acoustic_latents_out`) | yes |
| Saved score replay (`score_tokens_file`) | yes |
| Saved performance reuse (`semantic_codes_file`) | yes |
| Finishing a saved sound (acoustic decode, `acoustic_latents_file`) | no: decoder-only session; runs in its own process, warm engine stays loaded |

## How to enable

1. Rebuild the engine. The patch set changed, so the engine fingerprint
   changes (`5316782788d200aa` for this tree) and setup builds a new engine
   directory: `python3 setup_engine.py --backend cuda` (or Metal/CPU), or the
   in-app engine update. On the V100 host keep the usual
   `--cuda-arch` choice.
2. Add `"warm_engine": true` to `data/engine.json` (or POST
   `{"warm_engine": true}` to `/api/system/engine`). No UI control was added.
   The next take starts the warm engine; `data/warm-engine.log` records engine
   start, stop and exit.

## Known limitations

- Not run on CUDA or on the production host (no remote or GPU access in this
  task). All real runs were Metal on an Apple M4.
- Admission now supports versioned resident leases (see below). Older schedulers
  and unmeasurable backends retain legacy admission without lease credit.
- Only errors containing `compute failed` are treated as fatal. CUDA errors
  that abort inside ggml end the process, which Riff handles as a crash.
- File changes are detected by stat identity (device, inode, size, mtime), not
  by content hash.
- Engine start-up output (for example Metal/CUDA device init) appears in the
  log of the take that started the engine, and in `data/warm-engine.log`.
- An idle warm engine holds its weights: measured 2.86 GiB footprint on Metal
  with `yue2-3b-q4_0.gguf` (2.48 GiB) + `yue2-vae-f16.gguf` (0.25 GiB). On CUDA
  this is VRAM.

## Test results (observed in this worktree, 2026-09-25)

Python suite, `python3 -B -m unittest discover -s tests -v` (includes the 17
new tests in `tests/test_warm_engine.py`):

```
Ran 210 tests in 57.321s
OK (skipped=5)
```

The 5 skips: 3 `ConfigureOnlyTests` (no cmake on PATH) and 2 integration classes
("Application-only checkout has no compiled score control modules"). With the
pinned cmake on PATH, `tests/test_engine_setup.py`:

```
Ran 8 tests in 12.080s
OK
```

Elixir: `elixir tests/native_capabilities_test.exs` -> `5 tests, 0 failures`;
`elixir desktop/test/build_test.exs` -> `13 tests, 0 failures`;
`elixir tests/native_cache_test.exs` with cmake on PATH -> `17 tests, 0 failures`
(without cmake on PATH it reports 9 failures, "CMake is required for native
cache tests").

Patch series: `setup_engine.apply_pinned_patches` on a fresh clone at
`fbe3eed` applied all 13 patches in order, ending with
`yue2-warm-engine.patch`; a second run applied nothing. The resulting tree
(`d3850357b5252fd3d080094c36302f3319029713`) is identical to the compiled one.

Native build: Metal, `-DAUDIOCPP_MODELS=yue2`, target `audiocpp_cli`,
`[100%] Built target audiocpp_cli`, no warnings in the changed files, build
directory 106 MB.

Native checks against that build:
`elixir scripts/check_native_score_replay.exs --build ... --source-repository ... --tokenizer ...`
-> `Native score replay checks passed`;
`elixir scripts/check_native_acoustic.exs ...` -> `Native acoustic checks passed`,
pipeline harness `{"passed":46,"failed":0,...}` (41 existing + 5 new
keep_resident cases).

Real Metal renders (3 s, 4 steps, cot off, q4_0 model), one cold per-take run
and one warm session with jobs seed 1234, seed 5678, seed 1234:

```
cold exit=0 seconds=26
warm exit=0 seconds=48
cold.wav == warm_a1.wav
cold.wav == warm_a2.wav
cold.wav != warm_b.wav (different seed, expected)
```

| | AR upload ms | NAR upload ms | VAE upload ms | session.wall_ms |
|---|---|---|---|---|
| cold process | 8470 | 3879 | 1349 | 25715 |
| warm job 1 | 7286 | 6109 | 2014 | 26896 |
| warm job 2 | none | none | none | 9725 |
| warm job 3 | none | none | none | 9976 |

Through the Python `WarmEngine` with real `run.build_command` commands (seed
2^63-1, twice): both status 0 on the same engine pid, second take with 0 AR/NAR
upload lines, no markers in take logs, identical WAV and `.codes.i32` output.
Idle engine after one job: 2.86 GiB footprint; exit status 0 on stdin EOF.

## Step 2 checkpoint (superseded by step 3 below)

- No CUDA build or run; not deployed; nothing pushed.
- No UI toggle for `warm_engine`.
- Browser (`tests/*.mjs`) and `installer/test` suites were not run; they do not
  exercise the changed code.

## Resident lease admission (#15 step 3)

The scheduler advertises `resident: riff.resident.v1`. New resident update, fit,
release and atomic release-if-idle messages carry that protocol version. A job
names its lease with `resident` and `resident_protocol`; mismatches and missing
leases are rejected. A lost or incompatible binding falls back to full cold
admission, which waits through temporary scheduler outages. Older schedulers,
including those predating capability discovery, and unknown advertised resident
versions receive no new operation or request field. Explicit lease versions are
still checked and rejected on mismatch.
No automatic study-set reuse is introduced for an older scheduler.

A lease records measured idle process bytes, not its estimated peak. A matching
take receives weight credit from the smaller of its own and the lease's
`basis.weights`, capped by measured residency. Its incremental charge cannot
fall below measured working usage. Shared PID usage is split once: the lease's
baseline and the job's growth above it. Both are already absent from OS/device
availability; only future growth is additionally reserved. The existing solo
path and pressure rules still govern ordinary jobs. Metal uses the single host
pool returned by `Resources`; CUDA host and device pools remain independent.

Before a warm take, a fresh fit decision compares availability after active
growth, the existing `RIFF_MEMORY_RESERVE_MB` and projected retained bytes with
the largest actual other queued model requirement, including waiters behind an
exclusive/solo model. An existing lease uses measured bytes; a new engine, or
an unmeasurable manual engine without lease credit, uses estimated weight bytes.
Pressure must be normal and required
host/device observations known. Failed fits stop the old lease before acquiring
a full cold reservation and run that take in its own process. Inputs/session
changes during admission discard the old credit before launch.

Defaults remain `warm_engine: false` and `warm_engine_release_when_empty: false`.
With warm mode enabled, the engine stays resident between manual takes. The new
release option checks both the local queue and all scheduler waiting/running
model work. Its scheduler check and retirement are serialized against new
admission and local submission. The engine is marked unavailable for reuse
before those locks are released; process shutdown then waits outside the locks,
so status, submit, cancel and other admission remain responsive. A new take has
no lease credit and needs a full reservation. Completion of other model work
wakes the idle worker. A temporary idle queue/storage error uses the worker's
existing retry rather than ending the worker.
Cancel of a running take retires its engine and lease; queued cancellation does
not. Saving engine settings wakes the worker to retire the old idle session.
Both toggles and the effective environment memory reserve are visible in Studio.

Take metrics retain `engine`, `memory_reservation`, `peak_process_memory_bytes`
and `memory_note`. `warm_reason` explains fallback or lost reuse; `warm_fit`
records raw availability, reserve, active-growth remainder, projected retention,
largest waiters and pressure; `resident` records the versioned measured lease.
`engine_reused` means the process/session was reused. It deliberately does not
claim weights were already loaded: #47's separate `warm.hit` remains derived
from native reuse and upload log lines. This change does not depend on #47 or
replace its timings, queue or memory summaries.

Model-free tests read real Linux `/proc` RSS for an owned fake engine and exercise
the Python bridge/lifetime with controlled policy replies. Synthetic Elixir
snapshots cover CUDA and unified Metal accounting. No new real-model benchmark,
CUDA run or Metal hardware run qualifies this lease policy. The existing macOS
process-footprint probe and CUDA per-process device source are reused; missing
measurements retain legacy manual reuse without guessed credit and report why.
