# DAW selection and integration proposal (#78)

Ensemble riff-daw-integration-20261025: conductor riff-daw-conductor-kimi-20261025,
workers riff-daw-deepseek-20261025 and riff-daw-glm-20261025.
Status: JOINT PROPOSAL, ensemble-signed, FOR PARENT/ROOT REVIEW. No detailed architecture, no implementation
authorization is claimed; this is the evidence-backed comparison, selection
recommendation and integration-hypothesis recommendation the assignment
asked for. All receipts are reproducible; docs cited live on branch
`research/riff-daw-integration-20261025`.

Evidence base: `riff-architecture.md` (Riff source facts),
`daw-candidates.md` (GLM official-source matrix + LMMS receipts),
`abc-semantics.md` (DeepSeek 18-case + smoke-probe fidelity matrix),
`ardour-headless-probe.md` + `ardour-two-track-probe.md` (DeepSeek),
`ardour-template-swap-probe.md` (conductor), `tracktion-engine-note.md`
(DeepSeek), `headless-import-correction.md` (DeepSeek),
`comparison-framework.md` (conductor), `docs/evidence/*.json` receipts,
Baton knowledge records by all three seats.

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

1. It is the only candidate with a complete headless PROJECT→MIX+STEMS
   chain receipted for a hand-written `.mmp`: agent-authored 2-track XML →
   headless mix render (`render`, ~1 s wall, no display) → per-track stems
   (`rendertracks`, distinct per-voice content) → deterministic output:
   re-run byte-identical — GLM's original render and its re-run, plus the
   conductor's two independent invocations, share one sha256
   (`d63f77e9…90fe08`). (GLM probe; conductor and DeepSeek cross-checks.)
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
  quoted as release-9.8 behavior. Headless
  render PROVEN (DeepSeek: MIDI+gmsynth→WAV via `ardour9-export` CLI and
  in-process `SimpleExport`, non-silent, sha256-pinned); construction gates
  partially proven (DeepSeek two-track template generation, extended to
  four tracks; conductor template-swap); per-track stems proven via the
  solo-render path (Lua `solo_control()` + per-snapshot export; two stems
  reconstruct the master within 1 LSB, four within 2 LSB — DeepSeek §3.1,
  hashes conductor-verified). Still open: a general headless session
  generator (the same class of adapter LMMS also needs — handwritten XML
  is not a generator either), raw/pre-fader/GUI/batch stem export, and a
  session reopen receipt. The editing-interface tradeoff is weighed in
  probe 5: Lua scripting is self-described upstream as "prototyping stage,
  interfaces may change", while LMMS's XML writing needs version-specific
  maintenance — neither disqualifies a pinned implementation. Ardour
  remains in active competition pending the shared musical-workflow probe.
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
| Project construction | agent-written XML `[probe]` + dump round-trip | template-swap + 2-track template `[probe]`; general generator unproven | C++ API by design (source); unprobed |
| Per-instrument MIDI | per-track patterns in XML; MIDI import is GUI-only (correction) | SMF sources per track `[probe]`; Lua import binding absent | first-class MIDI API (source) |
| Instrument hosting | bundled synths + SF2/GIG players; LV2/Carla on 1.3; no bundled GM soundfont | LV2 (VST3/CLAP per platform); bundled gmsynth+SF2 in official build | VST3/AU via JUCE build; none bundled |
| Arrangement/editing | Song editor model in XML | production-grade | engine-level Edit model |
| Mixing | FX mixer, per-track vol/pan | full buses/sends/mature mixing | via plugins/API |
| Automation | automation clips/controllers in XML | mature automation | AutomatableParameter API |
| Persistence/reopen | .mmp/.mmpz; alpha one-way-upgrade caveat; write→MMPZ→dump reparse/round-trip receipt `[probe]` (not a reopen+edit receipt) | .ardour XML; reopen receipt missing | ProjectManager/Edit (source) |
| Audio export | WAV mix + per-track stems `[probe]`; flac/ogg/mp3, 44.1–192 kHz, 32-bit float documented (man page, not probed; mp3 needs lame in the pinned build) | master-bus export `[probe]`; solo-render per-track stems `[probe]`; raw/GUI/batch stems unproven | renderToFile incl. track selection (source) |
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

0. End-to-end bridge receipt (DeepSeek gate 1): ABC→MIDI→`.mmp`→render/stems
   with an explicit voice→instrument map and the pinned converter — the
   product-chain demonstration the current receipts do not yet cover.

1. LMMS: GM instrument path (Sf2Player + a license-reviewed pinned
   soundfont — the AppImage contains zero .sf2/.sf3 files, GLM-verified;
   vs per-track synth presets); macOS arm64 headless render (unverified —
   AppImage probe was Linux; this gate qualifies the BUNDLED-DESKTOP
   backend only — a homelab-hosted backend can be proven on Linux without
   it, per root review item 6); .mmp reopen round-trip beyond dump;
   stem-vs-mix signal semantics (stems did not sample-sum to the mix in
   cross-check — DeepSeek reproduced: mix peak 32767, stem-sum peak 32768,
   difference RMS 7775; document pre/post-master behavior); render
   level sanity (probe content clipped at full scale). Runtime-environment
   hygiene for the job runner (GLM review): headless render requires an
   owned HOME/XDG_CONFIG_HOME/XDG_RUNTIME_DIR and JACK_NO_START_SERVER=1
   (JACK autostart hangs without it); `dump`/`upgrade` additionally need a
   display (Xvfb) and MMPZ (Qt qCompress) input and crash on plain `.mmp` —
   the queued-job contract must encode these.
2. Ardour: general session generator; raw/pre-fader/GUI/batch stem export;
   session reopen receipt; demo-nightly vs release-tag re-verification;
   DummyMidiBuffer warning investigation. Voice ISOLATION evidence note:
   the conductor's initial template-swap observation was mix-level only;
   DeepSeek's later solo-render stems (two- and four-track) DO isolate
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

## 7. Ensemble review status

- Conductor (kimi): author of synthesis; architecture trace; abcjs and
  template-swap probes.
- GLM: **SIGN-ON**, no evidence errors; 4 minor comments (see its report);
  independently re-confirmed the determinism receipt and verified the LMMS
  AppImage ships no soundfont.
- DeepSeek: **CONDITIONAL SIGN-ON** (deepseek-review.md): signs on to
  LMMS-first under H1 conditional on its two factual corrections (applied
  here: Ardour solo-render stem evidence restored; "complete chain" wording
  narrowed to hand-written-.mmp scope) and on §5 gates 0–6 staying explicit
  in the root-facing proposal.
- No dissenting selection position remains; the Ardour vs LMMS-first
  difference was resolved by evidence (project-generation receipts), not
  preference.
