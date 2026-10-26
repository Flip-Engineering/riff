# Ardour qualification gap and decision register (#78, dispatch 696afc8c)

Author: riff-daw-glm-20261025, 2026-10-26. Read-only review of the preserved
receipts at `f068888` plus a provisioning-path hardening fix (same commit).
Ground truth: `docs/evidence/ardour-qualification-20261026/*.json`,
`docs/evidence/generaluser-sf2-verification-20261026.json`,
`docs/daw-integration/ardour-build-preflight.md` (Q1–Q10 definitions,
:375-386), `daw_runtime.py`/`daw_runner.py`/`ardour_session.py`/
`daw_operations.py`, and the conductor-dispatched summary of Kimi's
seat-scoped knowledge records (9047f327, ba371930, 75efbde9, 28b9a2c0,
f74ea704/71ded2da — not directly readable from this seat; summarized facts
are used as reported, not independently re-verified).

## 1. Gate matrix (definitions: ardour-build-preflight.md:375-386)

| Gate | State | Evidence |
| --- | --- | --- |
| Q1 identity | **PASS** | `ardour-9.8-qualification.json` gates.Q1_identity ok (3 wrapper sha256 matches, version exit 0); knowledge 9047f327 |
| Q2 clean session | **PASS with note** | gates.Q2_clean_session ok; intermittent upstream SIGABRT at teardown recorded (artifact complete); 75efbde9 attributes it upstream |
| Q3 fixture build | **PASS** (conductor record 2026-10-26) | `q3-q4-named-gates-20261026.json`: 3 sources/tracks, programs 73/33/0, hashes recorded | (was: OPEN as a named gate) | no Q3 entry exists in the preserved receipt; the Q5/Q6 event and stem evidence presupposes the Q3 fixture, so the capability is demonstrated transitively, but Q3's own "source+session hashes recorded" result is not recorded |
| Q4 chain | **PASS** (conductor record 2026-10-26) | `q3-q4-named-gates-20261026.json`: snapshots hashed, save ok=true, duration exact | (was: OPEN as a named gate; chain demonstrated) | the Q4 upgrade→instrument→tempo→reopen→export sequence ran inside Q5/Q6 evidence and in both e2e runs (compose stages session/instrument done; render stages reused/done/done), but Q4's own criteria line (every snapshot exists; `save ok=true`; duration ±1 sample) has no recorded gate result |
| Q5 events | **PASS** | knowledge 9047f327 (Flute 12 / Bass 30 / Piano 37 exact; tempo/meter ticks 0/7680/12960; end b59520) |
| Q6 stems | **PASS** | knowledge 9047f327 (sum peak 17877 = mix peak; diff RMS ≤ 1.0) |
| Q7 program control | **PASS** (conductor run 2026-10-26) | `q7-program-control-20261026.json`: identical 12 Flute events, program 73→0, stem peak/RMS/centroid shift, other stems byte-identical |
| Q8 edit | **PASS** (conductor run 2026-10-26) | `q8-edit-20261026.json`: v2 re-render, same topology/duration/counts, all stem hashes + mix hash change |
| Q9 determinism | **PASS** | knowledge ba371930 (bit-identical re-run) |
| Q10 negative | **PASS** | gates.Q10_negative ok |

E2E operation-surface evidence (beyond the Q battery): `e2e-run-1.json` and
`e2e-run-2.json` record real `daw.compose`/`daw.render` operations with
riff-arrangement-v1 references, the four-stage receipt shape (session,
instrument, render, stems) and the reuse semantics — in two independent
stores.

## 2. Open items and decisions required

1. **Q3/Q4 named-gate results** — record the per-gate results (hashes,
   snapshot list, duration tolerance) in the qualification schema, or amend
   the preflight to derive them from Q5/Q6 + e2e receipts explicitly.
   Decision needed: backfill from retained artifacts vs re-run (a re-run is
   NOT authorized by this document; the ~31 min build is already evidenced).
2. **Q7 program-control run** — smallest missing musical gate. The fixture
   (73→0 swap on identical note events, compare peak/RMS/centroid to the
   recorded receipt, no timbre claim) is fully specified in the preflight.
3. **Q8 edit run** — v2 same-topology replacement on a project copy; the
   v1/v2 fixtures and the LMMS-side equivalent receipt already exist.
4. **Production runtime provisioning route** — `daw_runtime.py:30` admits
   `source-build` and `reviewed-full` kinds; the qualified runtime is a
   source build. Decision: which kind ships, and who signs the runtime
   record. The demo bundle is never a production runtime (`daw_runtime.py`
   :90-92) and was used only for SF2 measurement.
5. **SF2 redistribution is NOT cleared** —
   `generaluser-sf2-verification-20261026.json` verifies identity (sha256
   `c278464b…` matches) and quotes the embedded license ("License v2.0
   (custom, not OSI)"); it explicitly states this is not a redistribution
   clearance. Legal review is a hard prerequisite for any payload that
   ships `GeneralUser_LV2.sf2`. `ardour-bundle-licensing.md` carries the
   bundle inventory; the AppImage/`-demo` bundle provenance (nightly.ardour.org
   demo run, sha1-verified) is measurement-only provenance.
6. **ABC subset formalization** — the adapter's accepted ABC is the probe
   subset (voices, `%%MIDI program`, inline `[Q:]`/`[M:]`, gchordoff); the
   fidelity matrix (`abc-semantics.md`) documents converter behavior, but no
   normative "accepted ABC subset" contract exists for `daw.compose` input
   validation. Decide: document the subset as the contract, or implement a
   subset checker that fails closed pre-conversion.
7. **Stem contract wording** — `daw_runner.py:246-252`: stems are per-TRACK
   snapshots (shared-instrument voices collapse to one stem), and the
   manifest names tracks, never voices. The receipt/UX language must keep
   saying tracks (see :249-252 docstring); confirm the proposal's
   "per-voice stems" wording is amended everywhere.
8. **DummyMidiBuffer warnings** — still uninvestigated (open from
   `ardour-two-track-probe.md`); non-blocking for render correctness
   receipts but recorded here so it is not forgotten.
9. **demo/nightly vs release-tag re-verification** — binaries qualified are
   from a source build of the 9.8 pin commit; if production ships
   `reviewed-full`, Q1 identity and the Q-battery must re-run against those
   exact binaries (hash-pinned, per Q1's method).
10. **Provisioning-path hardening (this commit)** — `daw_runtime.
    _hashed_file` rejected symlinks but then re-opened the path to hash it
    (check-then-use; a link swapped between check and hash would redirect
    the hash), and `probe()` parsed a symlinked runtime record. Fixed:
    `os.open(O_NOFOLLOW|O_NONBLOCK)` + `fstat` regular-file check + hash
    from the opened descriptor (atomic gate), symlinked record refused, and
    non-regular files (FIFO) can no longer hang the gate. Covered by
    `tests/test_daw_runtime.py` (10 cases, dependency-free, no Ardour
    runtime; suite time ~0.03 s).

## 3. Explicit non-claims

- No runtime acceptance beyond the cited receipts is claimed.
- No legal/SF2 redistribution clearance is claimed or implied; legal review
  is required and has not happened.
- Q3/Q4 are not claimed as passed; their outcomes are demonstrated
  transitively and recorded as open named gates.
- The music session, Ardour GUI, and long builds were not touched: no build
  was re-run (the evidenced ~31 min build stands).

## 4. Smallest next artifacts (this commit)

- `daw_runtime.py`: atomic symlink/hash gate (`O_NOFOLLOW` + `fstat`),
  symlinked-record refusal.
- `tests/test_daw_runtime.py`: 10 gate tests (new file).
- This document.

Decision owners: root (route/kind, legal), ensemble (Q7/Q8 runs when
authorized, ABC subset contract wording).

## 5. Addendum — Q7/Q8 conductor runs (2026-10-26, backlog conductor)

GLM continuation 2 was quota-blocked before running (OMP weekly/monthly
exhaustion to 2026-10-31), Kimi seats share the exhausted weekly pool, Codex
seats fail workspace-routing 401 in this context, and the DAW DeepSeek seat
failed to start; the conductor therefore executed Q7/Q8 directly through the
`daw.compose`/`daw.render` operation surface on the preserved pinned runtime
(no rebuild, no GUI, no music-session contact). Receipts:
`docs/evidence/ardour-qualification-20261026/q7-program-control-20261026.json`
and `q8-edit-20261026.json`. The fresh v1 baseline reproduced the preserved
mix/stem hashes exactly (determinism anchor). Baseline required 4 attempts
(3 known upstream teardown SIGABRTs after `save ok=true`); Q7/Q8 passed on
first attempts. Remaining open items are unchanged: Q3/Q4 named-gate
records, production provisioning route/kind decision, SF2 legal review,
ABC subset contract, stem wording.

## 6. Addendum 2 — Q3/Q4 named-gate records (2026-10-26, backlog conductor)

`docs/evidence/ardour-qualification-20261026/q3-q4-named-gates-20261026.json`
records Q3 and Q4 strictly from the retained artifacts of the fresh v1
operation-surface run (source/session/snapshot SHA-256s, SMF programs and
note counts, `save ok=true` stage logs, mix duration exact at 1,016,000
frames). With this, Q1–Q10 all have recorded PASS evidence; the unified
battery script emitting all gates in one invocation remains a tooling gap.
Open root decisions are unchanged (provisioning route/kind, SF2 legal,
ABC subset, stem wording).
