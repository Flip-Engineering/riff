# Ardour composition/render implementation plan (#78, phase 2)

Status: draft contract for root review, 2026-10-26. Ardour is the SELECTED
implementation target (operator d6c922c9). This plan binds the first bounded
slices; it is not a broad build-out authorization. Research evidence lives in
`docs/daw-integration/` and `docs/evidence/`; this document cites it without
re-arguing the selection.

## 1. Objective and non-goals

Add a score-driven per-instrument composition/edit/render workflow to Riff,
running on Ardour, that:

- preserves the original ABC verbatim and records realization differences
  explicitly (converter, tick normalization, channel/program assignment),
- carries tempo/meter through an explicit manifest, never inferred from ABC,
- produces editable, reopenable Ardour sessions as first-class artifacts,
- supports shared instrument routing across voices (e.g. piano RH+LH on one
  track/instrument) and distinct per-track timbres (validated GM program
  evidence: 73/33/0 controlled swap),
- renders mix and per-track stems through the validated headless path.

Non-goals for these slices: no change to the YuE2 path, the musician's
instructions/material/native work, or existing operation semantics; no
parallel engine UI/API; no mandatory creative fields; no implicit timbre
assumptions; no lossless-ABC claims; no live activation during active
generation; no direct native build; no bundling/distribution work before the
GPLv2+/bank obligations from `docs/evidence/ardour-bundle-licensing-20261025.json`
are incorporated and reviewed.

## 2. Where the workflow plugs in

The existing surface (`docs/agent-operations.md`) is the contract: one queue,
one operation record, one receipt model. The Ardour workflow rides on it.
Registration points are pinned from the merged source (feature base
`4e0f9ee`; ensemble architecture map verified file:line):

- New operation names on `Operations.submit` (`operations.py:516`), e.g.
  `daw.compose` (build session + instrument + tempo/range + reopen) and
  `daw.render` (mix + stems), plus `daw.edit` (v1→v2 same-topology source
  replacement). Registered in `catalog()` (`operations.py:952-1020`) as
  additive post() entries; `SURFACE_VERSION` stays 1 (additive rule at
  `operations.py:962-963`). Same idempotency-key, Conflict/409,
  parent/family and receipt semantics as `study.create`/`take.develop`.
- `families.py:23-24` `ROLES` maps operation name → family role; the new
  names must be added there or family linkage is not role-tagged.
- `studio.py:533-568` `invoke_operation` name→handler table wires the
  routes; `capabilities.py:28-29` merges `operations.catalog` +
  `families.catalog` automatically (no edit unless adding a legacy alias).
- Jobs run on the existing `Generator` queue (`studio_core.py:1384`,
  `execute` stage machine at `studio_core.py:1415-1646`) as a new job kind
  beside `native` and `acoustic_decode` (kind selection at
  `studio_core.py:1455-1461`): an Ardour branch runs the §3 tool chain
  instead of `native_command` (`studio_core.py:898-918`). The warm-engine
  path must return no plan for the DAW kind (`warm_plan`,
  `studio_core.py:920`). Admission (`model_admission.py:472-475`,
  kind-keyed `resource_inputs`) gains a CPU/file-only `daw` reservation
  class with its Elixir-side estimate clause — no GPU residency.
- Receipts keep the existing shape (`operations.py:176-208`,
  `_stages:210-238`); the DAW stages are additive (session, instrument,
  render, stems), NOT forced into the closed `render_mode` enum or the
  YuE2-token `symbolic_plan` semantics. Errors keep the
  `<data>`/`<library>`/`<riff>` path redaction (`clean_text`,
  `operations.py:126-134`).
- A NEW reference kind for arrangements/sessions (proposal:
  `riff-arrangement-v1:<sha256>`): own regex beside
  `operations.py:25-26`, own provenance tuple, `resolve_references`
  branch (`operations.py:328-355`), and store table. `riff-score-v1`
  provenance is YuE2-token-specific (`SCORE_PROVENANCE`,
  `operations.py:62`) and `ScoreArtifacts` would reject foreign IDs; do
  not reuse it.
- Stems: `Store.add_track` (`studio_core.py:675`) binds exactly one WAV
  per track row; N-stem output needs a manifest/N-reference contract
  addition (mix take + stem artifacts linked, not N fake tracks).
- Progress: `observe_generation` markers (`studio_core.py:44-59`) are
  hard-coded `yue2.*`; the DAW runner needs its own marker/stage
  reporting (additive, same live-status shape).
- Lineage: rendered takes link their source score/session through the
  ordinary `parent`/`family_id` mechanism; `inputs.compare` and the
  lineage view read recorded differences, never inferred ones.

## 3. Data contracts

The full adapter contract, authored by riff-daw-deepseek-20261025 and folded
from its probe receipts, is `docs/daw-integration/ardour-adapter-contract.md`:
the canonical `riff.ardour.session_input.v1` input object, voice-map and
timing-manifest rules, template provenance decision (pinned clean 7004
template recommended; 1region fixture kept as regression baseline only),
admission checks, the exact 8-stage operation chain with commands,
environment contract (owned per-job HOME/XDG, `JACK_NO_START_SERVER=1`),
edit semantics (same-topology source replacement vs rebuild-triggering
changes), the runtime qualification recommendation (pinned source build of
9.8 `059c9588`; demo nightly is probe fixture only), and the explicit
non-goals/uncertainty list. The summary below is subordinate to it.

### 3.1 Composition input (agent-facing)

```jsonc
{
  "abc": "X:1\n...",                    // original ABC, stored verbatim
  "voice_map": {                        // voices -> tracks/instruments
    "Flute": {"track": "Flute", "channel": 0, "program": 73},
    "Bass":  {"track": "Bass",  "channel": 0, "program": 33},
    "PianoRH": {"track": "Piano", "channel": 0, "program": 0},
    "PianoLH": {"track": "Piano", "channel": 1, "program": 0}
  },
  "timing": {                           // explicit manifest; see probes/fixtures/arrangement-timing.json
    "tpq": 480, "events": [ {"tick": 7680, "tempo": 72}, {"tick": 11520, "meter": [3,4]} ],
    "end_tick": 14880
  },
  "instruments": {                      // per-track plugin selection
    "default": {"plugin": "http://gareus.org/oss/lv2/gmsynth", "bank": "GeneralUser_LV2.sf2"}
  },
  "title": "..."
}
```

- ABC tempo/meter/key markings are checked AGAINST the manifest; a mismatch
  is a recorded warning or an explicit error, never a silent rewrite.
- No creative field is required; `timing` is the only structural requirement
  beyond `abc`+`voice_map`, and a default flat tempo/meter manifest can be
  derived only when the caller asks for it explicitly.

### 3.2 Realization record (per voice, per render)

Recorded with the session artifact: converter + version (abc2midi 5.07
`6582eb8` source build), tick normalization (abc2midi first-event tick 1 →
0), channel/program assignment from the voice map, and the generated SMF
hash. This is the explicit realization-difference evidence; the original ABC
is preserved alongside.

### 3.3 Session and render artifacts

- The generated Ardour session directory is an owned artifact with a source
  ID and sha-manifest; reopen is part of the contract (validated:
  `reopen_session.lua` receipt).
- Renders: mix WAV + one solo-stem WAV per track, each an artifact with
  lineage to the session. Validated evidence: stems sum to the mix within
  1 LSB (`docs/daw-integration/ardour-musical-workflow.md` §4.3).
- Export sample rate/format are explicit in the request (evidence: CLI export
  used the session's 48 kHz; `SimpleExport` preset defaulted to 44.1 kHz —
  an integration must choose, not inherit).

## 4. Instrument provisioning

- Default bank: Ardour-bundled `gmsynth.lv2` + `GeneralUser_LV2.sf2`, program
  changes injected per track at tick 0. Source inventory/hash and the
  controlled program-swap evidence are retained
  (`ardour-bundle-licensing-20261025.json`, workflow §4.4).
- Additional SF2 via ACE FluidSynth/LV2 is allowed only behind an explicit
  instrument map entry plus a license/provenance check recorded in the
  receipt. Not in the first slices.
- Known caveats stay visible: instrument/backend/plugin recall warnings from
  the probe notes; Dummy-backend timing caveat (a real backend or source
  build is required before sample-accuracy claims); a GM program number is
  not a timbre-quality claim.

## 5. Runtime qualification

The production runtime must be a full, non-demo Ardour. Options for root:

1. **Pinned source build** of Ardour 9.8 (`059c95883d8158b03f632056ba36e5c7de91ef28`,
   evidence-verified) through the managed build queue (`setup_engine.py`
   pattern; tests/test_managed_build_queue.py is the existing harness).
   Pros: full license-clean feature set, no demo timer, matches the
   native-build admission model. Cons: heavy; must be queued and coordinated
   with root, never a manual shared-host change.
2. **Reviewed full binary route** (official non-demo build): faster to
   provision; needs root's explicit review of provenance and update policy.

The 9.8.241 DEMO nightly is a probe fixture only and must not be silently
shipped or used for production qualification.

## 6. Language/architecture placement

`docs/architecture-direction.md` holds: Elixir owns library/queue boundaries
long-term, Rust for native integration, and the existing Python must not gain
new permanent requirements. The validated session-adapter logic is Python
(`probes/ardour/build_session.py`, `patch_tempo_range.py`). Decision for
root, with a concrete recommendation:

- First slices keep the adapter as a bounded internal tool under the feature
  branch (clearly marked, with the Elixir/Rust replacement path named), so
  the workflow contracts are proven against real sessions before any port.
- The port target is the adapter's narrow core (ABC→SMF per voice, SMF merge,
  session-XML emit, tempo/range patch), not the whole probe tree.

## 7. Bounded slices

1. **Slice A (this PR): contract + adapter core.** This document; the
   session-construction adapter extracted as a reviewed module
   (`probes/ardour/build_session.py` logic, generalized: arbitrary voice map,
   timing manifest, instrument map; no fixture-only assumptions), with
   focused unit checks against the retained fixtures. No server wiring.
   Reversible.
2. **Slice B: operation registration.** `score.render` on the shared queue
   with the new job kind, admission class, receipts, artifact recording, and
   a stub-host/fake-runner test path. Requires Slice A plus the ensemble's
   registration map.
3. **Slice C: edit/reopen + stems.** `score.edit`, stem artifacts,
   verification hooks (`verify_timeline.py`, `verify_stems.py`,
   `wav_note_check.py` as library checks).
4. **Slice D: runtime qualification.** Root-coordinated pinned runtime
   (build queue or reviewed binary), then real-Ardour end-to-end validation.

Each slice lands as a focused draft PR with exact head/tree/tests and
remaining qualifications, published through the same-series identity helper;
on helper refusal, retain redacted evidence and route to root (no grant or
identity changes, no fallback write API).

## 8. Compatibility contracts (must not regress)

- YuE2 generation path, musician instructions/material/native work: untouched.
- Studio/agent parity (`tests/agent-parity.mjs`, `tests/test_operations.py`)
  stays green; new operations follow the same rules these tests pin.
- Queue/progress/cancel/retry/idempotency/lineage semantics identical to the
  existing operations; `operation.resume` resumes from the last captured
  Ardour stage.
- Historical evidence, docs and probe receipts are preserved verbatim.

## 9. Open decisions for root

1. Runtime route (§5): pinned source build vs reviewed full binary.
2. Adapter language path (§6): bounded Python tool now, Elixir/Rust port target.
3. Operation naming and stage vocabulary (Slice B) — proposal: `score.render`,
   `score.edit`; stages `session`, `instrument`, `render`, `stems`.
4. Instrument scope beyond the GM bank (§4) and its license gates.
