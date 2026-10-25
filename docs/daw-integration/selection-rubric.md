# DAW selection rubric and integration shapes (#78)

Author: riff-daw-conductor-kimi-20261025. Status: DRAFT framework for the
joint proposal. Candidate evidence lives in
`docs/daw-integration/daw-candidates.md` (GLM) and the ABC fidelity axis
(offered to DeepSeek). This document defines the decision structure only;
no candidate is scored yet.

## 1. Decision criteria (derived from Riff evidence, see riff-architecture.md)

Hard gates (failure excludes the candidate as the primary target):

- G1 License: open-source license that permits redistribution as a bundled
  separate process under the existing ffmpeg/x264/lame compliance pattern
  (pinned source archives + license texts in payload). GPL/LGPL acceptable;
  copyleft linking into the studio process is not. (riff-architecture.md §6)
- G2 Platform: builds and runs on macOS 15+ arm64 (desktop payload target)
  and Linux x86_64 (source-checkout/CI/research host). (desktop/README.md)
- G3 Headless: project creation/edit/render achievable without a display,
  via a documented CLI, scripting interface, or remote-control protocol with
  official support. ("programmatic/headless operation" is a #78 axis)
- G4 Audio export: offline (faster-than-realtime not required, but batch)
  export of a full mix to WAV; per-track/stem export strongly preferred.
- G5 Project persistence: a durable on-disk project format that can be
  created programmatically and reopened without loss of the composed
  arrangement.

Weighted qualities (scored 0–3 each, evidence-required):

- W1 Per-instrument MIDI/score handling: multi-track MIDI import with
  per-track instrument assignment (the core product aim: planned ABC per
  instrument → per-instrument tracks).
- W2 Instrument hosting: LV2/VST3/CLAP plugin instruments, SF2/SFZ
  soundfonts, or bundled GM-quality instruments usable headlessly.
- W3 Mixing: per-track gain/pan, sends/buses, effects hosting.
- W4 Automation: parameter automation representable in the project format
  and settable programmatically.
- W5 Scripting depth: full project graph editable via script (not only
  render-from-existing-project).
- W6 Project health: release cadence, active maintenance, packaging
  availability (source build feasibility for pinning).
- W7 Resource envelope: offline render RAM/CPU profile compatible with
  measured admission (must not assume a GPU; must run under nice/idle).
- W8 MusicXML/ABC import paths (bonus; MIDI is the minimum viable bridge).

## 2. Integration shapes (candidate-independent)

Shape A — Batch renderer (separate process, ffmpeg-style boundary):
Riff produces per-instrument MIDI (abcjs `getMidiFile` today, or a pinned
server-side converter), plus a generated/parameterized project file;
a queued job invokes the DAW headless to load project + render stems/mix.
Admission: new `resource_inputs` kind + Elixir `Resources.estimate` clause;
cold reserve per job. Lowest coupling; matches the existing
one-shot `audiocpp_cli` per-take pattern. Process startup cost per render.

Shape B — Resident DAW session (warm-engine pattern):
A long-lived headless DAW process (scripting socket / JSON-lines job
protocol) registered as a `riff.resident.v1` lease with MEASURED idle
baseline; jobs bind to the lease and get weight credit. Faster iteration,
plugin scan cost paid once; requires the DAW's headless protocol to be
stable and crash-contained, and the lease retirement semantics of
HANDOFF.md (release-if-idle, engine-changed invalidation).

Shape C — Library embedding:
Link DAW engine code into a Riff-controlled binary. REJECTED for
GPL-licensed candidates (linking boundary conflict with the MIT app);
possible only for permissively licensed engines. Not the default
assumption for any candidate.

Initial lean [INFERENCE, to be tested by candidate evidence]: Shape A first
(batch render is the minimal honest contract), Shape B only if a candidate
proves a stable headless session protocol and the admission lease evidence
justifies it.

## 3. Evidence the proposal still needs

- Per-candidate: G1–G5 gate evidence and W1–W8 scores with official
  doc/source links + versions (GLM's matrix).
- ABC→MIDI fidelity: what survives conversion (voices, timing, pitch,
  key/meter/tempo, repeats, articulations, dynamics, instrument intentions)
  per actual converter, with probe receipts (GLM probes; DeepSeek axis if
  accepted).
- Probe receipts: at minimum one headless project-create + render receipt
  for the leading candidate before the proposal is submitted (reversible,
  bounded, .probe-tmp).
- Staged validation plan: unit-level (converter invariants), integration
  (operation submit→render→receipt), and real-render receipts, per #44/#47
  precedent that prior contracts shipped without real runs — this proposal
  must state how the DAW path gets real receipts.

## 4. Honesty boundaries (from #17/#78, restated for the proposal)

- An ABC voice label is compositional intention; in the DAW path it becomes
  an explicit MIDI/track assignment — audibly verifiable, unlike YuE2
  conditioning. The proposal must keep that distinction in UX copy and
  receipts.
- No lossless-ABC claims: the fidelity matrix names what is preserved,
  converted, approximated, or dropped per converter.
- Existing YuE2 generation, musician session, and workflows unchanged; the
  DAW path is additive (new operations), per #14 additive/reversible rule.
