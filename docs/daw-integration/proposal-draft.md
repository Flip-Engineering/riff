# DAW selection and integration proposal (#78)

Ensemble riff-daw-integration-20261025: conductor riff-daw-conductor-kimi-20261025,
workers riff-daw-deepseek-20261025 and riff-daw-glm-20261025.
Status: FOR PARENT/ROOT REVIEW. No detailed architecture, no implementation
authorization is claimed; this is the evidence-backed comparison, selection
recommendation and integration-hypothesis recommendation the assignment
asked for. All receipts are reproducible; docs cited live on branch
`research/riff-daw-integration-20261025`.

Evidence base: `riff-architecture.md` (Riff source facts),
`daw-candidates.md` (GLM official-source matrix + LMMS receipts),
`abc-semantics.md` (DeepSeek 10-case fidelity matrix),
`ardour-headless-probe.md` + `ardour-two-track-probe.md` (DeepSeek),
`ardour-template-swap-probe.md` (conductor), `tracktion-engine-note.md`
(DeepSeek), `headless-import-correction.md` (DeepSeek),
`comparison-framework.md` (conductor), `docs/evidence/*.json` receipts,
Baton knowledge records by all three seats.

## 1. Recommendation

**Integrate LMMS (pinned v1.3.0-alpha.2) as the first DAW render backend,
under the batch-render hypothesis (H1), with a pinned server-side abc2midi
conversion stage. Retain Ardour as the production-depth alternative and
Tracktion Engine as the engine-component alternative, both with documented
open gates.**

Why LMMS first, on evidence:

1. It is the only candidate with a COMPLETE receipt chain for the product
   aim today: hand-written (agent-authored) 2-track `.mmp` XML → headless
   mix render (`render`, ~1 s wall, no display) → per-track stems
   (`rendertracks`, distinct per-voice content) → deterministic output
   (three renders byte-identical, sha256-verified by two seats
   independently). (GLM probe; conductor cross-check.)
2. Official macOS arm64 builds exist (macOS 11+/M1+, 1.3.0-alpha.2 release
   table), matching Riff's only desktop payload target; Linux x86_64
   probed on this host.
3. GPLv2 license; distribution as a separate process with pinned sources
   follows Riff's existing ffmpeg/x264/lame compliance pattern
   (obligation review still required — see §5).
4. The project format is agent-writable XML with a text-level `dump`
   round-trip receipt (11 notes byte-identical after write→MMPZ→dump).

Why not the others first (all reversible, evidence in the cited docs):

- **Ardour 9.8**: strongest arrangement/mixing/automation depth; headless
  render PROVEN (DeepSeek: MIDI+gmsynth→WAV via `ardour9-export` CLI and
  in-process `SimpleExport`, non-silent, sha256-pinned); construction gates
  partially proven (DeepSeek two-track template generation; conductor
  template-swap). But no general headless session generator, no per-track
  stem export evidence, no session reopen receipt, and Lua scripting is
  self-described upstream as "prototyping stage, interfaces may change".
  Keep as the upgrade path for mixing depth once its generator gate closes.
- **Tracktion Engine 3.2**: the strongest PROGRAMMATIC authoring surface
  (first-class MIDI API, GUI-independent `Renderer`, ProjectManager
  persistence), but it is an engine, not a DAW: Riff would build and own a
  thin headless helper (new C++20 component, instrument choices, packaging,
  signing), and distribution raises GPLv3 + separate JUCE license questions
  (verbatim obligations in `tracktion-engine-note.md`). No runtime probe.
  This is the "build a service" alternative — presented for review, per the
  user's instruction that integrating an existing DAW is the task, not
  building one.
- **Zrythm**: scripting officially disabled/deprecated; no CLI export. Fails
  agent-control today; re-evaluate when libpeas scripting lands.
- **Qtractor**: JACK+ALSA mandatory — Linux-only; remains a possibility only
  as a Linux-side render service (not the current product shape).
- **MuseScore 4**: notation bridge adjunct (CLI converter mode verified from
  the handbook; ABC import is plugin-based). Not a mixing/automation DAW.
- **Carla**: instrument/plugin hosting component (OSC remote, SF2/SFZ,
  LV2/VST3/AU…); a complement to whichever DAW is chosen, not a DAW.
- Stargate (~20-month stale releases), BespokeSynth (live-patch focus, no
  documented headless render): deprioritized.

## 2. Comparison summary (axes from the #78 authorization)

| Axis (C1–C12) | LMMS 1.3.0-alpha.2 | Ardour 9.8 | Tracktion 3.2 |
| --- | --- | --- | --- |
| License/distribution | GPLv2; separate-process precedent applies | GPLv2+ + plugin clarification | GPLv3/commercial + JUCE license |
| Headless operation | render/rendertracks QCoreApplication-only `[probe]`; dump/upgrade need display | `ardour9-lua` + `ardour9-export` `[probe]` | `Renderer` GUI-independent (source) |
| Project construction | agent-written XML `[probe]` + dump round-trip | template-swap + 2-track template `[probe]`; general generator unproven | C++ API by design (source); unprobed |
| Per-instrument MIDI | per-track patterns in XML; MIDI import is GUI-only (correction) | SMF sources per track `[probe]`; Lua import binding absent | first-class MIDI API (source) |
| Instrument hosting | bundled synths + SF2/GIG players; LV2/Carla on 1.3; no bundled GM soundfont | LV2 (VST3/CLAP per platform); bundled gmsynth+SF2 in official build | VST3/AU via JUCE build; none bundled |
| Arrangement/editing | Song editor model in XML | production-grade | engine-level Edit model |
| Mixing | FX mixer, per-track vol/pan | full buses/sends/mature mixing | via plugins/API |
| Automation | automation clips/controllers in XML | mature automation | AutomatableParameter API |
| Persistence/reopen | .mmp/.mmpz; alpha one-way-upgrade caveat; reopen receipt via dump round-trip | .ardour XML; reopen receipt missing | ProjectManager/Edit (source) |
| Audio export | mix + per-track stems, wav/flac/ogg/mp3, ≤192 kHz, 32-bit float `[probe]` | master-bus export `[probe]`; stems unproven | renderToFile incl. track selection (source) |
| Platforms | macOS arm64 (1.3 alpha), Linux, Windows | macOS arm64 official, Linux, Windows | macOS/Linux/Windows/mobile |
| Health | 1.3 alpha churn risk; active | active, mature | v3.2.0 2025-05; active |
| Resource profile | ~1 s short-render, CPU-only `[probe]` | demo-build CPU render `[probe]` | unmeasured |

Legend: `[probe]` = runtime receipt this ensemble; otherwise official
source/doc evidence; full citations in `daw-candidates.md` and probe docs.

## 3. ABC semantics: what crosses the bridge (DeepSeek's matrix, condensed)

Authoritative matrix: `abc-semantics.md` (10 cases × 2 converters, event-level
receipts). Contract-level consequences:

- Preserved: pitch/duration/rests as performance data; header M:/K:/Q: metas;
  tuplets (quantized); voice→track+channel mapping (abc2midi channel-correct;
  abcjs has a program-on-channel-0 defect and inline-field duration doubling —
  abcjs remains the browser audition path, NOT the bridge).
- Converted with documented approximation: dynamics→velocities; ties merged;
  grace timing differs per converter; staccato.
- Expanded/dropped: repeats flattened; slurs/tenuto/marcato dropped;
  voice names dropped; lyrics dropped (abcjs) or non-standard text
  (abc2midi); chord symbols synthesize unrequested accompaniment unless
  suppressed (abc2midi `-NGUI`).
- Convention pins the bridge must state: GM program numbering (raw 0-127 vs
  1-indexed names — `abcguide.txt:774`); default tempo when Q: absent
  (abc2midi 120 vs abcjs 180); one ABC doc with one `V:` per instrument as
  the composition format (matches YuE2 Vocal/Ins and Riff's multi-voice
  workspace).
- The ABC text stays authoritative; MIDI is a one-way performance
  realization; DAW-edited material re-enters YuE2 only as text ABC
  (`cot=melody|full`) — token-exact `riff-score-v1` identity cannot survive
  a DAW round-trip (GLM), so DAW arrangements need their own lineage kind.

## 4. Integration hypothesis recommended for review (H1, batch)

Not architecture — the shape the ensemble recommends detailing IF approved:

1. Agent composes per-instrument ABC (existing compose/write operations; the
   multi-voice score workspace already isolates voices).
2. A pinned server-side converter stage (abc2midi 5.07, sources.json
   pattern) validates (fail-closed: abc2midi aborts on parse errors) and
   emits one SMF per instrument + a conversion receipt (ABC sha256,
   converter version/options, voice→instrument map).
3. A project generator writes `.mmp` from a version-pinned template with
   per-track instruments and validates by load-and-render (never by
   parse-success — GLM's schema-landmine receipt).
4. A queued job runs `lmms render`/`rendertracks` (cold process, measured
   admission; a new `resource_inputs` kind per the architecture trace),
   records mix + stems as owned artifacts with provenance, and files the
   receipt under the existing operation contract (PR #44 shape).
5. YuE2 paths unchanged; the DAW path is additive (#14); the musician
   session is untouched.

Alternatives retained: H2 resident session (lease pattern exists; LMMS's
fast cold start weakens the case; Ardour/Carla would benefit more);
H3 library embedding (Tracktion) as the engine alternative with its license
review.

## 5. Qualification items before implementation (validation plan)

1. LMMS: GM instrument path (Sf2Player + a license-reviewed pinned
   soundfont — LMMS ships none; vs per-track synth presets); macOS headless
   render (unverified — AppImage probe was Linux); .mmp reopen round-trip
   beyond dump; stem-vs-master signal semantics (stems did not sample-sum
   to the mix in cross-check — document pre/post-master behavior); render
   level sanity (probe content clipped at full scale).
2. Ardour: general session generator; per-track stems; reopen; controlled
   mute/solo voice-isolation receipt (current two-track evidence is
   master-mix level); demo-nightly vs release-tag re-verification;
   DummyMidiBuffer warning investigation.
3. Converter: exact octave/pitch mapping through the full chain; malformed
   input policy; the abcjs defects stay browser-side unless fixed upstream.
4. Contract: N-artifact (stems) provenance shape, SURFACE_VERSION policy
   for receipt additions (GLM §4), new operation naming, admission
   estimates for render jobs.
5. License review: GPL obligations per distribution approach for the
   selected candidate(s), including payload source-archive pattern
   (ffmpeg precedent); Tracktion/JUCE only if H3 is pursued.
6. Real-content validation: a composed multi-instrument piece end-to-end
   with listening review (producer pipeline), plus #17 honesty wording in
   UX/receipts (intention vs assignment vs verified timbre).

## 6. Risks and honesty boundaries

- LMMS 1.3 is alpha (one-way project upgrades; schema churn): pin the exact
  release, validate per pinned version, treat the project format as
  LMMS-version-locked.
- abcjs stays the browser audition path; its program-routing and
  inline-field defects are recorded, not silently relied on.
- No lossless-ABC claims anywhere: the fidelity matrix defines preserved /
  converted / approximated / dropped.
- No stacking on unmerged #47/#75; no changes to existing YuE2 generation;
  no deployment; musician session preserved.
- No macOS run was possible from this host: every macOS claim is
  official-page evidence, not a receipt.

## 7. Ensemble review status

- Conductor (kimi): author of synthesis; architecture trace; abcjs and
  template-swap probes.
- GLM: candidate landscape, LMMS receipts, corrections accepted and applied;
  reviewed conductor docs (3 corrections landed).
- DeepSeek: fidelity matrix, Ardour probes, LMMS import correction,
  Tracktion note; reviewed conductor probe wording ("two-track template path
  proven; general generator unproven" applied).
- Dissent: none recorded. DeepSeek's note that no final selection was
  justified was written BEFORE the LMMS chain receipt and its own two-track
  receipt landed; this proposal is the ensemble's joint answer to that bar.
