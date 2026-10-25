# DAW selection and integration proposal — DRAFT (not yet submitted) (#78)

Ensemble riff-daw-integration-20261025: conductor kimi, workers deepseek
(ABC semantics axis, pending first turn) and GLM (candidate landscape).
This draft assembles the joint proposal for parent/root review. It is NOT
a selection until the receipts cited below land and all three seats sign on
or record dissent.

Evidence base: `riff-architecture.md` (Riff source facts),
`daw-candidates.md` (official-source candidate matrix), Baton knowledge
records by all seats, probe receipts in `.probe-tmp/`.

## 1. Recommendation [PENDING probe receipts]

Leading picture from current evidence (subject to change):

- **Ardour 9.8** — production-grade arrangement/mixing/automation; GPLv2+
  with plugin clarification; official macOS arm64 builds; Lua scripting +
  `luasession` command-line tool with Dummy backend; `SimpleExport` bound in
  Lua (master-bus file export; luabindings.cc:1848-1854) — headless
  create+render is source-evidenced, runtime receipt pending. Scripting
  manual warns "prototyping stage".
- **LMMS 1.3.0-alpha.2** — headless `render`/`rendertracks` (stems) are
  documented and use QCoreApplication; GPLv2; official macOS arm64 builds
  (alpha; one-way project-file upgrades; lighter mixing/automation).
  CORRECTION (DeepSeek, source-verified on main.cpp v1.2.2 and
  v1.3.0-alpha.2): `--import` is a GUI path (no coreOnly; MidiImport uses
  QProgressDialog) and does not save a project — the headless chain is
  generate-.mmp-XML → render, and the .mmp generation gate is unproven
  (GLM probing).
- **Carla** — not a DAW; candidate instrument-hosting component
  (LADSPA/DSSI/LV2/VST2/VST3/AU + SF2/SFZ, OSC remote), GPLv2+.
- Deferred: Zrythm (scripting disabled/deprecated upstream), Qtractor
  (JACK+ALSA mandatory, Linux-only), MuseScore 4 (notation bridge adjunct;
  CLI export unverified), Stargate/BespokeSynth (headless story unverified).

Open decision: single-DAW (Ardour OR LMMS) vs layered (LMMS documented-CLI
render path now + Ardour for deep arrangement/mixing maturity), possibly
with Carla hosting. The proposal will weigh runtime receipts, alpha-branch
risk, and packaging cost.

Decisive gate (both serious candidates share it): neither has a headless
MIDI/score IMPORT path — Ardour's Lua bindings do not expose
`Session::import_files` (the shipped example drives the GUI Editor), and
LMMS `--import` is GUI. The comparison that decides the proposal is which
project format can be GENERATED and validated safely headlessly: Ardour
session XML (+template/static-structure path) vs LMMS `.mmp` XML
(write→`dump` round-trip). Receipts so far prove headless RENDER on both
sides (Ardour: non-silent WAV verified; LMMS: documented, GLM probing).

## 2. Comparison summary [condensed from daw-candidates.md at proposal time]

Table over C1–C12 (comparison-framework.md) — to be generated from the
final candidate doc so the proposal and matrix cannot drift.

## 3. ABC semantics: what crosses the bridge [evidence-backed]

The authoritative fidelity matrix is `abc-midi-fidelity.md` (DeepSeek;
10-case corpus across abcjs 6.7.0 and abc2midi 5.07, event-level receipts in
`docs/evidence/abc-midi-fidelity-20261025.json`). Headline results:

- Preserved by both: pitch/duration/rests as performance data, M:/K:/Q:
  header metas, tuplets (minor rounding), voice→track+channel mapping.
- Converted with documented approximation: dynamics→velocities
  (45..110/127), ties merged, repeats expanded (structure lost), grace
  notes (converters disagree on timing), staccato.
- Dropped: slurs/tenuto/marcato (performance effect), voice names, track
  names; lyrics dropped by abcjs, non-standard text events by abc2midi.
- abcjs defects (material): program changes emitted on channel 0 for every
  track; inline `[K:]`/`[Q:]` DOUBLE subsequent note durations; no inline
  meta emitted. abcjs is the browser audition path, not a production bridge.
- Convention hazards: GM program numbering (raw byte vs 1-indexed lists);
  converter defaults differ (no `Q:` → abc2midi 120 BPM vs abcjs 180 BPM);
  both synthesize unrequested chord accompaniment tracks by default
  (abc2midi: `-NGUI` ignores chord indications — flag verified in the
  5.07 binary).
- Consequence: the bridge is a pinned server-side converter stage with a
  receipt (ABC sha256, converter version/options, voice→instrument map);
  ABC text stays authoritative; MIDI is a one-way performance realization.

## 4. Integration hypotheses under evaluation (not selected)

H1 batch renderer / H2 resident lease / H3 library embedding — see
comparison-framework.md §2. Code seams are documented as source facts in
riff-architecture.md §7. GLM's review notes: one-shot CLI engines fit the
cold-process builder, persistent engines the resident path; N stem
artifacts per job would extend the current single-artifact provenance
shapes (operations.py:56-62) — a contract addition the proposal must price.

Composition-format hypothesis (working assumption to test, not a decision):
one multi-voice ABC document with one `V:` per instrument (matches YuE2's
Vocal/Ins convention and the existing multi-voice score workspace, where
voices can already be isolated in preview), each voice mapped to a DAW
track with an explicit instrument assignment (`%%MIDI program` and/or a
voice→plugin manifest with the numbering convention pinned down). The
alternative — N separate single-voice ABC scores — buys independent token
budgets at the cost of a shared meter/tempo/key coordination step.

## 5. Validation plan [to be detailed]

- Unit: converter invariant tests (tempo/meter/key exact; voice→track
  fidelity; documented approximations asserted).
- Integration: operation submit→render→receipt on the test-engine pattern
  of #44; admission reserve/release for the new kind.
- Real receipts: headless create+render per recommended candidate on this
  Linux host; macOS-arm64 verification explicitly listed as deployment
  prerequisite (this host cannot run it).

## 6. Risks and honesty boundaries

- LMMS 1.3 alpha churn + one-way project upgrades (pin exact release,
  reopen tests) vs LMMS 1.2.2 stable lacking arm64 macOS builds.
- Ardour scripting "prototyping stage" upstream warning; stem export via
  Lua needs per-route evidence (SimpleExport is master-bus).
- Instrument-label honesty (#17): DAW-side assignments are explicit and
  audibly verifiable; receipts must record program/plugin assignments as
  intention+assignment, never as YuE2-verified timbre.
- No stacking on unmerged #47/#75; no change to existing YuE2 paths; the
  musician session is untouched.
