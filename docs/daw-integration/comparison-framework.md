# DAW comparison questions and candidate-independent integration hypotheses (#78)

Author: riff-daw-conductor-kimi-20261025. Status: DRAFT framework for the
joint proposal. Nothing here selects a candidate, a contract, or a topology.
The Riff-side source facts live in `docs/daw-integration/riff-architecture.md`;
candidate evidence lives in `docs/daw-integration/daw-candidates.md` (GLM).

## 1. Comparison questions (from the #78 authorization, verbatim axes)

For each candidate, answer with official source/doc links and versions:

- C1 Licensing and distribution: what license(s) cover the code and bundled
  assets; what obligations follow for each possible distribution approach
  (separate process, linked library, source build pinned in
  sources.json/components.json); are there official statements on
  embedding/server use. Open-source is a user-stated requirement; the
  obligations of specific licenses (e.g. GPL family) are a question for
  official-license evidence, not an assumed disqualifier of any approach.
- C2 Programmatic and headless operation: what officially supported
  interfaces exist for creating, editing, and rendering projects without a
  display (CLI, scripting languages, sockets/OSC/IPC, library APIs)? Which
  are versioned/documented?
- C3 Per-instrument score and MIDI handling: multi-track MIDI import/export,
  track↔instrument assignment, and any notation/MusicXML paths.
- C4 Sound generation and instrument hosting: bundled instruments,
  soundfont (SF2/SFZ) support, plugin standards (LV2/VST3/CLAP/LADSPA) and
  their headless usability.
- C5 Arrangement and editing: the project data model (tracks, clips,
  regions, tempo/meter maps) and whether it is fully scriptable.
- C6 Mixing: buses, sends, per-track gain/pan/effects.
- C7 Automation: parameter automation, its representation, and programmatic
  access.
- C8 Project persistence and reopening: the on-disk format, its
  documentation/stability, and round-trip behavior for programmatically
  created projects.
- C9 Audio export: mixdown and stem export, formats, offline rendering.
- C10 Platforms: which OS/architectures are officially supported and built;
  how that intersects Riff's actual shipping target (macOS-arm64 desktop
  today; Linux source checkouts) is a comparison input, not an eligibility
  rule.
- C11 Project health: release cadence, maintenance activity, build-system
  feasibility for sha256-pinned sourcing.
- C12 Resource profile: typical RAM/CPU of headless rendering, so admission
  can be measured honestly (#15 envelope discipline).

## 2. Candidate-independent integration hypotheses (NOT selected)

These are possibilities to evaluate per candidate against the source facts
in riff-architecture.md; the joint proposal will recommend one only after
the comparison lands.

- H1 Batch renderer: Riff emits per-instrument MIDI plus a project file; a
  queued job invokes the candidate headless to render. Reuses the existing
  one-shot process pattern; per-render startup cost.
- H2 Resident session: a long-lived headless candidate process under a
  measured `riff.resident.v1` lease; amortizes startup/plugin scans;
  requires a stable headless protocol and crash containment.
- H3 Library embedding: link candidate engine code into a Riff-controlled
  binary. Raises license-specific obligations (e.g. GPL-family) and
  packaging questions that need official-license evidence per candidate;
  not assumed viable or non-viable in advance.

## 3. Evidence the proposal still needs

- Per-candidate answers to C1–C12 with official links/versions.
- ABC→MIDI fidelity matrix per actual converter (abcjs probe receipts
  recorded in Baton knowledge by conductor; abc2midi/MuseScore paths from
  GLM; deeper semantics axis offered to DeepSeek): preserved vs converted
  vs approximated vs dropped for voices, timing, pitch, key/meter/tempo,
  repeats, articulations, dynamics, instrument intentions.
- At least one headless create+render receipt for any candidate the
  proposal recommends (reversible probe, .probe-tmp, bounded).
- A staged validation plan with real-render receipts (#78 asks for actual
  experiments and reproducible receipts; prior contract PRs shipped without
  real model runs and this work should do better).

## 4. Honesty boundaries (from #17/#78)

- ABC voice labels are compositional intention; in any DAW path an
  instrument assignment becomes explicit and audibly verifiable — UX copy
  and receipts must keep that distinction.
- No lossless-ABC claims; the fidelity matrix names what each converter
  preserves, converts, approximates or drops.
- Existing YuE2 generation and the musician session stay operational; any
  DAW path is additive per #14.
