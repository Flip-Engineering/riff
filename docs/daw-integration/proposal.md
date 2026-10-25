# DAW selection and integration proposal (#78)

Ensemble riff-daw-integration-20261025: conductor riff-daw-conductor-kimi-20261025,
workers riff-daw-deepseek-20261025 and riff-daw-glm-20261025.
Status: JOINT PROPOSAL, ensemble-signed, FOR PARENT/ROOT REVIEW — updated
2026-10-25 with the root-authorized musical-workflow probe round
(recommendation PROVISIONAL; Ardour remains in active competition). No detailed architecture, no implementation
authorization is claimed; this is the evidence-backed comparison, selection
recommendation and integration-hypothesis recommendation the assignment
asked for. All receipts are reproducible; docs cited live on branch
`research/riff-daw-integration-20261025`.

Evidence base: `riff-architecture.md` (Riff source facts),
`daw-candidates.md` (GLM official-source matrix + LMMS receipts + fair
comparison + licensing inventory),
`abc-semantics.md` (DeepSeek 18-case + smoke-probe fidelity matrix),
`ardour-headless-probe.md` + `ardour-two-track-probe.md` +
`ardour-musical-workflow.md` (DeepSeek),
`ardour-template-swap-probe.md` (conductor), `tracktion-engine-note.md`
(DeepSeek), `headless-import-correction.md` (DeepSeek),
`deepseek-review.md` + `deepseek-lmms-challenge.md` (DeepSeek),
`ardour-bundle-licensing.md` (DeepSeek),
`comparison-framework.md` (conductor), `docs/evidence/*.json` receipts,
`probes/` (committed scripts + shared fixtures), Baton knowledge records.

## 1. Recommendation (PROVISIONAL — root review continues; bounded musical
comparison authorized and running)

**The ensemble's evidence favors LMMS (pinned v1.3.0-alpha.2) as the first
DAW render backend, under the batch-render hypothesis (H1), with a pinned
server-side abc2midi conversion stage — while Ardour remains a competitive
choice and Tracktion Engine remains the engine-component alternative. This
is a preference, not a decisive production-quality advantage, and not a
selected production implementation.**

Fair-comparison correction (root review): BOTH LMMS and Ardour need a
project-generation adapter. LMMS's receipts are handwritten-XML driven;
its MIDI import is GUI-dependent. Ardour already has GENERATED two- and
four-track projects with individual MIDI sources/instruments, headless
save/render, and solo stems reconstructing the master within 1-2 LSB.
Deferring Ardour for lacking a general generator would apply a standard
LMMS has not met either. LMMS's demonstrated advantages are simpler project
construction, native per-track export (`rendertracks`), and fast startup on
the small fixture — these support the preference; they do not close the
comparison. The root-authorized musical workflow probes (both DAWs, shared
arrangement fixture) are designed to supply the missing production-quality
evidence before any selection is final.

Why LMMS first, on evidence:

1. It is the only candidate with a receipted headless PROJECT→MIX+STEMS
   chain for a hand-written `.mmp`: agent-authored 4-voice XML (generated
   by `probes/verify/mmp_generator.py` from the shared ABC fixture) →
   headless mix render (~1 s wall, no display) → per-track stems
   (`rendertracks`, distinct per-voice content) → deterministic output:
   re-run byte-identical (GLM render+re-run + conductor's two invocations,
   one sha256). Qualifiers on "complete": the render carries a
   deterministic song-end pad (24.166 s render vs ~19.97 s content);
   stems pass the master-volume stage and are NOT a sample-sum
   decomposition of the mix (track clipping + nonlinear master taper —
   GLM's routing resolution; Ardour's solo-render stems reconstruct its
   mix within 1 LSB, the complementary stronger receipt); and the
   ABC→project generator is fixture-shaped, not general.
   The end-to-end ABC→MIDI→`.mmp` product bridge, macOS headless,
   instrument/timbre path, and stem-vs-mix semantics are NOT yet receipted —
   they are explicit gates in §5, not buried under this claim.
2. Official macOS arm64 builds exist (macOS 11+/M1+, 1.3.0-alpha.2 release
   table), matching Riff's only desktop payload target; Linux x86_64
   probed on this host.
3. GPLv2 license. Separate-process execution is NOT blanket clearance:
   obligations follow the ACTUAL distributed artifacts (program sources,
   bundled instruments/plugins/samples/soundfonts), so per-candidate
   licensing inventories of the distributed bundles are probe items
   (GLM: LMMS AppImage; DeepSeek: Ardour bundle incl. gmsynth and its SF2)
   with obligation review to follow (§5).
4. The project format is agent-writable XML with a text-level `dump`
   reparse/round-trip receipt (11 notes byte-identical after
   write→MMPZ→dump; this is a reparse of the generated project, NOT a
   reopen-and-edit receipt).

Why not the others first (all reversible, evidence in the cited docs):

- **Ardour 9.8**: strongest arrangement/mixing/automation depth. BUILD
  PROVENANCE, kept distinct: source citations are release **tag 9.8**
  (commit `059c9588`); ALL runtime receipts are from the official
  **9.8.241 DEMO NIGHTLY** Linux build — demo-nightly behavior must not be
  quoted as release-9.8 behavior. New musical-workflow receipt
  (`ardour-musical-workflow.md`, hashes conductor-verified): generated
  3-track session from the shared fixture (PianoRH+PianoLH on ONE Piano
  track, one gmsynth instance, two MIDI channels — a true shared-instrument
  receipt), exact abc2midi event preservation (12/30/37 notes through
  load/save), explicit tempo+ meter map matching the contract, save →
  REOPEN → render cycle receipted, v1→v2 edit on the same project topology,
  solo stems reconstructing the mix within 1 LSB, and a controlled
  program-swap timbre check (73↔0 changes rendered audio). Still open: a
  general ABC→session generator (timing came from an explicit manifest),
  distinct SF2 BANKS (programs proven, banks not), raw/pre-fader/GUI/batch
  stem export, source-built/macOS receipts, and license clearance
  (gmsynth is GPL-family; GeneralUser GS has a custom license with a
  sample-provenance caveat — `ardour-bundle-licensing.md`).
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
| License/distribution | GPLv2 (LICENSE.txt); obligations follow actual distributed artifacts — inventory probe assigned | GPLv2+ + plugin clarification; bundle inventory probe assigned | GPLv3/commercial + JUCE license |
| Headless operation | render/rendertracks QCoreApplication-only `[probe]`; dump/upgrade need display | `ardour9-lua` + `ardour9-export` `[probe]` | `Renderer` GUI-independent (source) |
| Project construction | generated 4-track .mmp from shared ABC fixture via `mmp_generator.py` `[probe]` + dump reparse round-trip; fixture-shaped, not general | generated 3-track session from same fixture via `build_session.py` + explicit timing manifest `[probe]`; fixture-shaped, not general | C++ API by design (source); unprobed |
| Per-instrument MIDI | per-track patterns in XML; MIDI import is GUI-only (correction); two matched OPL2 tracks model the shared piano (NOT one session instrument) | SMF sources per track `[probe]`; Lua import binding absent; PianoRH+LH share ONE track/instrument with two channels `[probe]` | first-class MIDI API (source) |
| Instrument hosting | bundled synths + SF2/GIG players; LV2/Carla on 1.3; no bundled GM soundfont | LV2 (VST3/CLAP per platform); bundled gmsynth+SF2 in official build | VST3/AU via JUCE build; none bundled |
| Arrangement/editing | Song editor model in XML | production-grade | engine-level Edit model |
| Mixing | FX mixer, per-track vol/pan | full buses/sends/mature mixing | via plugins/API |
| Automation | automation clips/controllers in XML | mature automation | AutomatableParameter API |
| Persistence/reopen | .mmp/.mmpz; alpha one-way-upgrade caveat; dump reparse `[probe]` + reload-cycle reopen evidence `[probe]` | .ardour XML; save→instrument→patch→reopen→render cycle receipted `[probe]` | ProjectManager/Edit (source) |
| Tempo/meter maps | tempo 100→72→100 carried via AutomationTrack and audibly verified `[probe]`; meter map NOT representable (project-global; absolute ticks place events correctly) | explicit tempo+meter map saved in session, matching contract (q16/q24/q27) `[probe]` | engine-level (source) |
| Audio export | WAV mix + per-track stems `[probe]` (stems pass master stage, NOT sample-sum decomposition; deterministic song-end pad); flac/ogg/mp3, 44.1–192 kHz, 32-bit float documented (man page, not probed; mp3 needs lame in the pinned build) | master-bus export `[probe]`; solo-render per-track stems reconstruct mix within 1 LSB `[probe]`; raw/GUI/batch stems unproven | renderToFile incl. track selection (source) |
| Platforms | macOS arm64 (1.3 alpha), Linux, Windows | macOS arm64 official, Linux, Windows | macOS/Linux/Windows/mobile |
| Health | 1.3 alpha churn risk; active | active, mature | v3.2.0 2025-05; active |
| Resource profile | ~1 s short-render, CPU-only `[probe]` | demo-build CPU render `[probe]` | unmeasured |

Legend: `[probe]` = runtime receipt this ensemble; otherwise official
source/doc evidence; full citations in `daw-candidates.md` and probe docs.

## 3. ABC semantics: what crosses the bridge (DeepSeek's matrix, condensed)

Authoritative matrix: `abc-semantics.md` (18 cases + a two-voice smoke
probe = 19 receipts across abcjs 6.7.0 and abc2midi 5.07, event-level JSON).
Contract-level consequences:

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
  1-indexed names — receipt in `daw-candidates.md` §3 citing
  `abcguide.txt:774`); default tempo when Q: absent
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

Reversible probes ALREADY PERFORMED are the receipts cited in §1–§3 (LMMS
project→mix+stems, determinism, dump round-trip; Ardour render, template
construction, solo-render stems; converter matrix). Everything below is the
LATER validation plan — not done, and not required before this proposal per
the operator's instruction.

0. General ABC→project adapter (DeepSeek gate 1, evolved): both candidates
   now have FIXTURE-SHAPED generators with exact event preservation on the
   shared arrangement; neither handles arbitrary ABC/timing without an
   explicit contract (LMMS: version-locked XML + project-global meter;
   Ardour: template + explicit timing manifest). A production adapter must
   define which ABC subset is accepted and validate by load-and-render.

1. LMMS: GM instrument path (Sf2Player + a license-reviewed pinned
   soundfont — the AppImage contains zero .sf2/.sf3 files, GLM-verified;
   vs per-track synth presets — the probe used bundled TripleOscillator/
   OPL2 timbres); macOS arm64 headless render (unverified —
   AppImage probe was Linux; this gate qualifies the BUNDLED-DESKTOP
   backend only — a homelab-hosted backend can be proven on Linux without
   it, per root review item 6); single-instrument routing receipt if
   session-level sharing is required (current LMMS shared-piano model is
   two matched tracks — DeepSeek challenge §2.1);
   stem-vs-mix signal semantics RESOLVED (GLM: track clipping + nonlinear
   master taper; rule: headroom per track, verify by event alignment never
   sample sum; no DAW defect claimed); render
   level sanity (early probe content clipped at full scale; fixed by
   headroom rule). Runtime-environment
   hygiene for the job runner (GLM review): headless render requires an
   owned HOME/XDG_CONFIG_HOME/XDG_RUNTIME_DIR and JACK_NO_START_SERVER=1
   (JACK autostart hangs without it); `dump`/`upgrade` additionally need a
   display (Xvfb) and MMPZ (Qt qCompress) input and crash on plain `.mmp` —
   the queued-job contract must encode these.
2. Ardour: general session generator (timing manifest currently explicit,
   not ABC-inferred); distinct SF2 banks (programs proven, banks not);
   raw/pre-fader/GUI/batch stem export;
   source-built and macOS receipts (all runtime evidence is demo-nightly);
   DummyMidiBuffer warning investigation; license clearance of the bundled
   instruments (gmsynth GPL-family; GeneralUser GS custom license with
   sample-provenance caveat — `ardour-bundle-licensing.md`).
   Voice ISOLATION evidence note:
   the conductor's initial template-swap observation was mix-level only;
   DeepSeek's solo-render stems (two/four-track, and three-stem musical
   workflow reconstructing the mix within 1 LSB) DO isolate
   voices — that controlled receipt exists; what remains unproven is the
   non-solo stem paths named above.
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
- No stacking on pending work: #47 is MERGED (main `0230da64`); #75 remains
  unmerged — no dependence on `family.*`. No changes to existing YuE2
  generation; no deployment; musician session preserved.
- No macOS run was possible from this host: every macOS claim is
  official-page evidence, not a receipt. Scoped per root review: macOS
  qualification gates the BUNDLED-DESKTOP backend; a homelab-hosted
  backend can be established without it.

## 7. Ensemble review status and disagreement synthesis

- Conductor (kimi): synthesis; architecture trace; abcjs probes;
  template-swap probe; shared arrangement fixture + verification tooling.
- GLM: candidate landscape; LMMS musical-workflow probe (generated 4-track
  .mmp from the shared fixture, reopen via reload cycle, v2 edit with
  12/12 edited-flute onset match); stem/mix difference ROOT-CAUSED (track
  clipping + nonlinear master taper; predictable-routing rule; no defect
  claimed); doc corrections applied; editing-interface fair comparison
  (§2.9); LMMS bundle licensing inventory (§2.10).
- DeepSeek: Ardour musical-workflow probe (generated 3-track session, exact
  event preservation, reopen+edit receipts, 1-LSB stem reconstruction,
  controlled program-swap timbre check); bundle licensing inventory
  (gmsynth GPL-family; GeneralUser GS custom license caveat); and a
  four-point evidence challenge to LMMS-first wording — all four accepted
  and applied here (shared-instrument distinction, meter-map asymmetry,
  render-pad + stem-qualifier wording, neither generator general).
- Disagreement resolution: the challenge shifted the comparison closer —
  Ardour gained reopen/edit/program and stem-reconstruction receipts;
  LMMS retains simpler construction, native rendertracks, fast cold start,
  and official macOS arm64 page-evidence. The selection stays provisional
  pending root review of this probe round; no dissent recorded.
