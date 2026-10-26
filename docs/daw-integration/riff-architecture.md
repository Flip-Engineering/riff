# Riff architecture trace for DAW integration (#78)

Author: riff-daw-conductor-kimi-20261025. Status: in progress; sections cite
file:line in this checkout (branch `research/riff-daw-integration-20261025`,
base `c4c2e23`). Every claim is source-verified unless marked [INFERENCE].
This document is evidence for the selection/integration proposal; it does not
select a DAW.

## 1. System shape

- Python studio: HTTP API + queue + artifact recording (`studio.py`,
  `studio_core.py`, `operations.py`, `capabilities.py`).
- Native engine: YuE2-3B GGML inference via audio.cpp, pinned commit
  `fbe3eedbf6c504e45189e2cdcf1b257740a28863` plus 16 local patches
  (`sources.json`, `patches/`). One-shot CLI per take, or opt-in resident
  "warm engine" job protocol `riff.jobs.v1` (HANDOFF.md).
- Elixir runtime (`runtime/lib`): admission/memory policy and
  `Riff.Runtime.ScoreArtifact` store; Python bridges via
  `model_admission.py` (runtime/README.md).
- Web UI (`web/`): score workspace on vendored abcjs_basic 6.7.0
  (`web/vendor/abcjs-basic-min.js:1`, MIT, `NOTICE.md:14`).

## 2. Agent surface (merged PR #44 contract)

`capabilities.py:describe()` returns the machine-readable recipe schema and
operation catalog. Agent-relevant operations (capabilities.py:52-77):

- `POST /api/plans` — score-only composition (`cot=melody|full`).
- `POST /api/generations` — full render; also `finish_audio` with
  `acoustic_source`.
- `POST /api/composition/revise` — `abc, cot, brief` + recipe inputs.
- `POST /api/inspiration` — writer ("brief", `idea_engine=ai|phrases|openrouter`).
- `GET /api/scores/{riff-score-v1:<sha256>}` — owned score inspection.
- `GET /api/tracks/{id}/lineage` — recorded ancestry graph; "No ancestry is
  inferred from seed, title or musical similarity" (capabilities.py:52-53).
- Queue: `GET /api/jobs/{id}`, `cancel`, `move` (up/down/first).

Provenance/reference types (operations.py:7-9,40-42): 32-hex track/job IDs,
`riff-score-v1:<sha256>` score references, `riff-acoustic-v1:<sha256>` saved
sounds; receipts bind request, effective recipe, resolved references, seed and
per-stage status; receipts carry no filesystem paths (PR #44). Idempotency
keys replay with `replayed:true`; same key + different input = 409.

`SURFACE_VERSION = 1` (operations.py); runtime capability advertisement keys
`RUNTIME_CAPABILITIES = (exact_score_replay, acoustic_checkpoint, warm_engine,
lora, score_format, prefix_contract)`.

## 3. ABC layer (what a DAW must interoperate with)

- Sole ABC parser: vendored abcjs_basic 6.7.0 in the browser. Python performs
  NO ABC syntax validation: `abc` is free text (`studio_core.py:74`); the only
  semantic rules are `abc` requires `cot=melody|full`
  (`studio_core.py:113-114`, `run.py:318-320`) and mutual exclusion with
  `score_source` (`studio_core.py:115-119`).
- Dialect: `X:T:M:L:Q:K:` headers, optional `V:` voices, chord symbols
  (`"Dm"`); YuE2's emitted convention is a `Vocal` melody voice, an `Ins`
  melody voice and chord symbols (docs/yue2-capability-audit.md:64-71).
  Arbitrary voice names (`Bass`, `Gtr`, `Arp`) are conditioning text only —
  they carry no instrument, stem or timbre guarantee into YuE2 audio.
- ABC→MIDI exists today, client-side only:
  `ABCJS.synth.getMidiFile(abc, {midiOutputType:"binary"})`
  (`web/score.js:283-286`), derived from ABC text, never from audio
  (docs/capabilities.md:95-96).
- Score identity is token-level, not text-level: saved scores capture native
  token IDs + tokenizer provenance (`symbolic.py`, `runtime/README.md` "Saved
  scores"); editing notation detaches the saved score
  (`web/score.js:171-176`). Re-encoding visible ABC does not reproduce the
  original tokens.
- Size ceilings relevant to arrangement length: `abc_min_tokens=32`,
  `abc_max_tokens=4096` defaults (`model_options.py:18-19`), model context
  24576 tokens (`model_options.py:5`); admission reserves
  `planning_tokens = abc_max_tokens` (`model_admission.py:522`).

## 4. Resource admission (what a DAW render process must respect)

- Admission is measured, not guessed: requests carry estimated
  `host_peak`/`device_peak` plus fresh host/device snapshots; missing host
  observations defer work; host and CUDA budgets are separate; Metal charges
  unified memory (runtime/README.md "Admission contract").
- Resident processes: scheduler advertises `resident: riff.resident.v1`;
  versioned leases record measured idle bytes; lease credit is capped by
  measured residency; failed fits fall back to full cold admission
  (HANDOFF.md "Resident lease admission (#15 step 3)").
- A DAW render backend (headless process, plugin host) would integrate exactly
  here: per-job admission with honest peak estimates, or a resident lease for
  a persistent session — same pattern as `yue2.keep_resident` warm engine.

## 5. Governance constraints on any proposal (from tracker evidence)

- #78: proposal-first gate — evidence-backed comparison/selection/integration
  proposal before detailed architecture or substantial implementation;
  reversible feasibility probes allowed.
- #17: instrument-label honesty — never conflate compositional intention,
  MIDI program assignment, and verified generated timbre; #78 re-opens the
  deferral but keeps this evidentiary bar.
- #44: DAW-facing agent actions must ride the same versioned operation/queue/
  receipt contract (idempotency, transactional records, no path leakage).
- #47 merged (main `0230da64`); #75 merged by root (`0634fa36`): both are
  now merged evidence; this branch took no dependency on either.
- #14/#15: additive and reversible; measured admission; serial fallback.
- Musician session and existing YuE2 workflows remain operational; no
  deployment in this scope.

## 6. Source pinning, packaging, licensing

- Pinning pattern (`sources.json`): repo+commit(+branch), per-file
  `{bytes, sha256}`, `local_patches` with `{bytes, sha256}`, advertised
  `native_capabilities` verified post-build (`desktop/build_engine.exs:93-106`).
- Desktop payload is macOS-arm64/Metal only today (`desktop/README.md`);
  source checkouts build Metal/CUDA/CPU (`setup_engine.py`). A DAW component
  must build for macOS 15+ arm64 to ship, or remain source-checkout-only
  until a CUDA desktop package exists.
- Payload assembly (`desktop/package.exs`) cross-verifies component receipts,
  emits per-file sha256 manifest. New Python deps ship only as sha256-pinned
  wheels, `--no-index --no-deps` (`desktop/build_python.exs`,
  `desktop/writer-wheels-macos-arm64.json`).
- GPL/LGPL precedent: ffmpeg is built with GPL x264 + LGPL LAME and shipped
  as SEPARATE binaries with pinned source archives + build scripts + license
  README in the payload (`desktop/package.exs:101-121`, `NOTICE.md`). A
  copyleft DAW used as a separate headless process fits this pattern;
  linking copyleft code into `audiocpp_cli` or the studio would not.
- App code MIT (`LICENSE`); YuE2 weights CC BY-NC 4.0, fetched by installer,
  never bundled (`NOTICE.md`).
- New resident process requirements: a new `Resources.estimate` clause
  (`runtime/lib/riff/runtime/resources.ex`), measured-only resident baseline
  (`model_admission.py` retain_resident refuses guesses), protocol
  `riff.resident.v1`, requirement `{host_peak>=0, device_peak>=0, device
  required if device_peak>0}` (`runtime/lib/riff/runtime/admission.ex:350-370`).

## 7. Candidate integration seams (source facts, no design selected)

These are where the current codebase would mechanically accommodate a new
operation; they are observations for later evaluation, not a proposed
`daw.render` contract. IF a future proposal adds an operation, the code
locations involved would be:

1. **Operation name + dispatch**: dotted names match the existing route
   pattern (`/api/v1/operations/{name}`, `studio.py:43`,
   `invoke_operation` `studio.py:488-518`).
2. **Catalog entry**: JSON input/output schemas in `operations.catalog()`
   (`operations.py:861-976`); `RECEIPT_SCHEMA`/`SUBMISSION_SCHEMA` reusable.
3. **Validation**: `validate_recipe` (`studio_core.py:62-175`) or a parallel
   validator; `render_mode` is a closed enum (`music|plan|performance|sound`,
   `studio_core.py:118-120`) and receipts hard-code stages
   plan/performance/sound/audio (`operations.py:210-238`, receipt schema
   properties `operations.py:855-857`) — a DAW
   render either maps onto these or extends both.
4. **Job→process mapping**: `Generator.execute` kind branch
   (`studio_core.py:1415-1421`) + command builder (`native_command`
   `studio_core.py:896-916` → `run.build_command` `run.py:40-124`). A DAW
   render would add a kind + builder for the chosen binary; spawn, cancellation,
   progress markers, and artifact capture are reused.
5. **Provenance**: new artifact reference kinds extend
   `resolve_references` (`operations.py:343-368`) with a fixed-key pick-list
   (pattern: `SCORE_PROVENANCE` operations.py:56-61), a reference regex
   (pattern: `SCORE_REFERENCE` operations.py:22-23), `INPUT_FIELDS`,
   `CARRIED`/`CHANGEABLE`, and `RUNTIME_CAPABILITIES` as applicable.
6. **Admission**: a `resource_inputs` kind branch (`model_admission.py:472-`)
   feeding a new Elixir `Resources.estimate` clause; resident DAW processes
   use measured `riff.resident.v1` leases (HANDOFF.md).

Reused verbatim: `Operations.submit` idempotency + transactional
operation+job insert (`operations.py:498-534`), queue with cancel/move,
resume guard, receipt derivation, catalog discovery.

## 8. Job lifecycle (verified)

Submit (`operations.py:498-534`): key validation → canonical digest → replay
check → `admit` gate → `generator.prepare` (`studio_core.py:1276-1280`) →
`resolve_references` → runtime identity → `enqueue` (single transaction,
`queue_position = MAX+1`, `studio_core.py:1282-1296`). Worker: single daemon
thread claims oldest queued job under `BEGIN IMMEDIATE`
(`studio_core.py:1351-1380`); `execute` reserves via `_reserve_take`
(cold reserve or resident lease, `studio_core.py:969-1028`), spawns cold
process or warm-engine job (`studio_core.py:1463-1472`), attaches PID to
admission, parses progress markers, captures artifacts before status write.
Terminal statuses: done/planned/performed/synthesized/cancelled/interrupted/
failed (`operations.py:25`).
