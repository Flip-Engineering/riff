# DAW candidate landscape from official sources (#78)

Author: riff-daw-glm-20261025. Status: first full draft, 2026-10-25. Companion to
[docs/daw-integration/riff-architecture.md](riff-architecture.md) (conductor).
Method: official upstream pages/repos only, versions and commits recorded per
claim; every claim carries its source link. `[unverified]` marks items still
needing a source-level receipt; `[probe]` marks items backed by a reproducible
run in this checkout (receipts under `.probe-tmp/`, gitignored). This document
does not select a DAW; it constrains the joint selection proposal.

## 1. Version evidence table (accessed 2026-10-25)

| Candidate | Current version | Evidence |
| --- | --- | --- |
| Ardour | 9.8 (2026-08-23) | https://ardour.org/whatsnew.html |
| LMMS | v1.3.0-alpha.2 (commit `4e677cb`, verified sig); stable v1.2.2 | https://github.com/LMMS/lmms/releases/tag/v1.3.0-alpha.2 |
| Zrythm | v1.0.0 stable; v2.0.0-alpha.7 dev | https://github.com/zrythm/zrythm/releases |
| Qtractor | 1.6.4 (2026-09-06) | https://qtractor.org/ |
| Carla | 2.5.8 (Ubuntu noble); main branch | https://github.com/falkTX/Carla |
| MuseScore | 4.x (CLI/export claims `[unverified]` here) | https://musescore.org |
| Stargate | rolling (stargatedaw org; 882 stars) | https://github.com/stargatedaw/stargate |
| BespokeSynth | nightly channel | https://github.com/BespokeSynth/BespokeSynth |
| abcMIDI | 5.07 (2026-10-14), commit `6582eb8` (2026-10-20), built locally | https://github.com/sshlien/abcmidi (canonical: https://ifdo.ca/~seymour/runabc/) |
| Tracktion Engine | 3.2.0 (2025-05-15) | https://github.com/Tracktion/tracktion_engine/releases/tag/v3.2.0 — see [docs/daw-integration/tracktion-engine-note.md](tracktion-engine-note.md) (DeepSeek) |
## 2. Candidate assessments

### 2.1 Ardour 9.8

- License: GPLv2-or-later with an explicit "Plugin Clarification" stating
  plugins of any license may load without derivative-work implications
  (COPYING, https://github.com/Ardour/ardour/blob/master/COPYING). Separate
  process bundling is ordinary GPL distribution; no network-service clause.
- Headless/programmatic: Lua scripting (Lua 5.3) in five contexts — Editor
  actions, hooks, session scripts, DSP scripts, and a dedicated
  **"Commandline Tool"**: "The standalone tool `luasession` allows one to
  access an Ardour session directly from the commandline. It can also be used
  as #! interpreter for scripted sessions" with a "None (Dummy)" backend
  (https://manual.ardour.org/lua-scripting/). The same manual warns: "Be aware
  that the vast majority of complex functionality is provided by the Editor
  UI" and calls scripting "fully functional, yet still in a prototyping stage"
  with "interfaces may change".
- Official script evidence of programmatic control: `add_audio_track.lua`
  (`Session:new_audio_track`), `_export_tracks.lua`
  (`Session:export_route_state` / `import_route_state` — route-state XML, not
  audio), `bounce_replace.lua` (`track:bounce_range(...)` — in-session bounce
  with processing returning a region)
  (https://github.com/Ardour/ardour/tree/master/share/scripts).
- Headless file export: resolved at source level since first draft — master
  binds `SimpleExport` into Lua (`run_export`, `set_folder`, `set_range`,
  `set_preset`, `check_outputs`; `Session::simple_export` factory,
  `libs/ardour/luabindings.cc` in master), and the Ubuntu package ships
  `ardour8-export` ("export an ardour session from the commandline ...
  using the master-bus outputs"). Peer runtime receipts (DeepSeek seat):
  non-silent MIDI+gmsynth renders via both `ardour9-export` CLI and
  in-process SimpleExport, sha256-recorded. My no-root receipt:
  `utils/ardour8-new_empty_session` created a session headlessly, but
  `ardour8-export` segfaulted with the Dummy backend in that extraction
  (environment-negative receipt, not an upstream verdict).
- Open gate: **headless MIDI import** — `Editor:do_import` is a GUI path;
  a template/generated-session route is unproven. This, not export, is
  Ardour's decisive comparison now (see DeepSeek's
  `docs/daw-integration/headless-import-correction.md`).
- MIDI/per-instrument: full SMF workflows, JACK MIDI, per-track instrument
  plugin hosting (LV2; VST/LADSPA per platform), mixing, automation, tempo
  maps; production-grade arrangement and editing (manual: Sessions & Tracks,
  MIDI, Mixing chapters).
- Platform: official macOS arm64 downloads exist (https://community.ardour.org/download).

### 2.2 LMMS (v1.3.0-alpha.2; stable 1.2.2)

- License: GPLv2 (LICENSE.txt contains GPL v2 text,
  https://github.com/LMMS/lmms/blob/master/LICENSE.txt).
- Headless render: the shipped man page documents actions `dump` (XML of a
  compressed MMPZ project), `render`, **`rendertracks`** ("Render each track to
  a different file" — native per-track stems), and `upgrade`; render options:
  wav/flac/ogg/mp3, samplerate 44100–192000, 32-bit float, loop mode,
  oversampling (https://github.com/LMMS/lmms/blob/master/doc/lmms.1).
  **Correction (peer source-verified, DeepSeek):** `--import` is a GUI path
  even with `-e` (it constructs the GUI `MainApplication`;
  `MidiImport.cpp` uses `QProgressDialog`/`mainWindow`) and does not save a
  project — my runtime probe hung exactly there under Xvfb. `render` /
  `rendertracks` are headless (`Engine::init(true)` with no GUI,
  `src/core/main.cpp` in the v1.3.0-alpha.2 tag).
- Runtime receipt `[probe]` (this checkout, no root, nice -n 19/ionice -c3):
  a hand-written 2-track `.mmp` (2× TripleOscillator, 11 notes, 4 bars,
  108 BPM) rendered headlessly by the official `lmms-1.3.0-alpha.2-linux-x86_64.AppImage`
  (sha256 `7580e832…` verified against the release page): mix
  `out.wav` (pcm_s16le 44.1 kHz stereo, 6.66 s) in ~1 s wall with no X
  display, plus `rendertracks` stems `1_Melody.wav` / `2_Bass.wav`
  (volumedetect mean ≈ −3.2 dB). sha256s: mix `16b64791…`, melody
  `f765c0ce…`, bass `36eb555e…`, source project `19a04c9e…`. Receipts under
  `.probe-tmp/lmms/` (gitignored).
- Schema-migration landmine for hand-written projects: v1.3.0-alpha.2's
  `Track::loadTrack` (`src/core/Track.cpp`) treats clip/pattern elements as
  children of `<track>` (siblings of `<instrumenttrack>`); nesting them
  inside `<instrumenttrack>` (the 1.2 layout) parses without error but
  yields zero clips and "project is empty, aborting!" — a silent-layout
  failure mode that any `.mmp` generator/validator must handle per pinned
  version.
- Project format: XML (`.mmp` / compressed `.mmpz`), agent-writable and
  diffable; `dump`/`upgrade` give text-level access without the GUI.
- Instruments/hosting: bundled synths (TripleOscillator, OpulenZ, Monstro,
  Watsyn...), SF2 (`Sf2Player`), GIG (`GigPlayer`), embedded ZynAddSubFX,
  VST2 via `Vestige`, and on master `Lv2Instrument`/`Lv2Effect` plus embedded
  Carla (`CarlaRack`/`CarlaPatchbay`)
  (https://github.com/LMMS/lmms/tree/master/plugins). Release notes for
  alpha.2 add "Native LinuxVST support" and list ARM64 builds as a major
  feature. **Version-sensitive:** LV2/Carla support lives on master/1.3; the
  1.2.2 stable line predates it.
- Arrangement/mixing/automation: Song Editor + Beat/Bassline patterns,
  automation clips and controllers, FX mixer with unlimited channels (README).
- Platform: official **macOS (arm64)** builds, macOS 11+/M1+, per the
  alpha.2 release system-requirements table; Linux arm64/x86_64; Windows.
  Caveats: pre-release disclaimer ("project files ... cannot be reopened in
  previous versions", "new features ... may be modified or removed"), 32-bit
  dropped.
- Honest limits: mixing/automation depth is below Ardour's; VST2 embedding on
  Linux historically needs Wine for Windows plugins (LinuxVST native support
  is new in 1.3); alpha-branch churn.

### 2.3 Zrythm (v1.0.0 stable; v2.0.0-alpha.7)

- License: AGPL-3.0-or-later **plus** trademark additional terms (Section 7):
  modified builds may not use the Zrythm name/logo without consent
  (LICENSES/LicenseRef-ZrythmLicense.txt,
  https://github.com/zrythm/zrythm/blob/master/LICENSES/LicenseRef-ZrythmLicense.txt).
  Separate-process use avoids linking obligations, but AGPL §13 source
  duties follow any Riff-side patches of Zrythm itself.
- Headless/programmatic: **scripting is disabled/deprecated** — "Zrythm used
  to offer scripting capabilities using GNU Guile. This functionality has
  been disabled and is considered deprecated. We will be migrating to a
  different framework for scripting - likely libpeas-based."
  (https://manual.zrythm.org/en/scripting/overview.html, v2.0.0-DEV manual).
  The CLI chapter documents only help/version/settings/audio-backend
  overrides; no render/export command
  (https://manual.zrythm.org/en/invoking-from-command-line/intro.html).
- Per-instrument/MIDI/hosting/automation: LV2-centered plugin hosting, MIDI
  tracks, automation; GTK4. Project format `.zproj`.
- Verdict for this task: weakest programmatic story of the serious candidates
  today; re-evaluate when the libpeas scripting and v2 stabilize.

### 2.4 Qtractor 1.6.4

- License GPL-2.0-or-later; feature-complete per its own page ("production/
  stable phase"), XML sessions, LADSPA/DSSI/VST2/VST3/LV2/CLAP hosting,
  automation, track export/mixdown, SMF 0/1, NSM support
  (https://qtractor.org/).
- Platform-fit evidence: **JACK and ALSA are mandatory runtime
  infrastructure** (Linux-only) — poor fit for the macOS-arm64 desktop
  payload, but it remains a comparison possibility as a Linux-side render
  service (the research host is Linux; the current product does not ship a
  Linux render service, so this is a distribution-shape question for the
  proposal, not an exclusion).

### 2.5 MuseScore 4 (adjunct, not the DAW)

- Role: notation conversion bridge and quick audition rendering, not a
  mixing/automation DAW.
- ABC import is **plugin-based** (bundled ABC Import plugin historically;
  third-party ABC_ImpEx uses wim.vree's `abc2xml`/`xml2abc`), not a native
  importer (https://musescore.org/project/abc-import,
  https://blechtrottel.net/en/abc_impex.html).
- CLI: verified against the official handbook — `-o/--export-to` "switches
  to 'converter mode' and avoids the graphical user interface"; output
  extensions include wav/flac/ogg/mp3/midi/musicxml; JSON batch jobs via
  `-j`; `--sound-profile` selects "MuseScore Basic" vs "Muse Sounds"
  (https://handbook.musescore.org/appendix/command-line-usage). Caveat: the
  page notes it was copied from the MuseScore 3 handbook — options should be
  smoke-tested on the pinned MuseScore 4 build before proposal claims.

### 2.6 Carla 2.5.8 (hosting component, not the DAW)

- License GPLv2-or-later (README, doc/GPL.txt). Hosts
  LADSPA/DSSI/LV2/VST2/VST3/AU + SF2/SFZ; **OSC remote control**; native
  audio drivers + JACK; itself embeddable as LV2/VST2 — LMMS master embeds it
  (https://github.com/falkTX/Carla, README).
- Relevance: candidate for a per-instrument hosting/render service under
  Riff's admission contract, independent of which DAW is selected.

### 2.7 Secondary candidates (surveyed, deprioritized)

- Stargate (stargatedaw/stargate): GPL-3.0, Python, pattern-based DAW with
  routing matrix and bundled plugins; PortAudio/PortMidi; latest release
  `release-24.02.2` (2024-02) per its releases feed — ~20 months stale as of
  2026-10, which scores it low on project health; headless story
  `[unverified]`.
- BespokeSynth: GPL-3.0 modular live environment; VST/VST3/LV2 hosting,
  Python livecoding; live-patch oriented, no documented headless render; not
  an arranger DAW.

### 2.8 Tracktion Engine (cross-reference)

DeepSeek's [tracktion-engine-note.md](tracktion-engine-note.md) covers the
engine candidate (library not app; dual GPLv3/commercial plus a separate
JUCE license requirement; GUI-independent `Renderer`; first-class MIDI API;
no bundled GM instrument found). Spot-checked by this seat:
README.md at v3.2.0 states "Tracktion Engine is covered by a
GPL/Commercial license" with "multiple commercial licensing tiers"
(https://github.com/Tracktion/tracktion_engine/blob/v3.2.0/README.md).
No runtime probe by this seat; license review remains a proposal item there.

## 3. ABC → MIDI conversion trace (probe)

Deep ABC-semantics fidelity is the DeepSeek seat's axis; this section records
the candidate-evidence layer only: one reproducible probe with abcMIDI, the
converter most DAW/import paths and Riff's own abcjs path would interact with.

Probe receipts (commands verbatim, artifacts in `.probe-tmp/abcmidi/`,
gitignored):

```
$ git clone --depth 1 https://github.com/sshlien/abcmidi   # 6582eb8, VERSION "October 14 2026"
$ ./configure && make -j4                                  # abc2midi built in ~9 s
$ ./abc2midi -ver  ->  5.07 October 14 2026 abc2midi
$ ./abc2midi probe.abc -o probe.mid                        # 2 voices, K:Dm, M:4/4, Q:1/4=108
$ ./src/mftext probe.mid > probe.mftext                    # event-level dump
```

Observed mappings (event receipts in `probe.mftext`):

| ABC construct | MIDI outcome | Class |
| --- | --- | --- |
| `K:Dm` | Key signature meta: sharp/flats=-1, minor=1 | preserved |
| `M:4/4` | Time signature meta 4/4 | preserved |
| `Q:1/4=108` | Tempo meta 555555 µs/quarter = 108 BPM | preserved |
| `V:Melody` / `V:Bass` | Separate format-1 tracks, separate channels | preserved (as tracks, NOT as timbres) |
| `%%MIDI program 73` / `33` | Program change chan=1 program=73 / chan=2 program=33 | explicit conversion; **0-indexed** GM numbers |
| `!p!` `!mf!` `!f!` | Note velocities (e.g. 60 vs default 80/95/105 accents) | approximation (velocity-mapped) |
| `.D2` staccato | Duration 160/480 ticks ≈ 33% of nominal | approximation (documented shortening) |
| `D>E` broken rhythm | Asymmetric 720/240-tick pair | preserved |
| `(3DEF` triplet | Three 160-tick notes in one 480-tick beat | preserved |
| `\|: ... :\|` | Expanded (played twice; 86 note-ons total) | expansion (repeat structure not kept as structure) |
| Voice length mismatch | Warnings; pad/truncate to common length | lossy edge |

Malformed input behavior: a syntax error (`D.2`) aborted note generation for
the remainder of that pass with a parse error — conversion is not
fault-tolerant; any conversion stage must validate before converting.

Program-number convention: `doc/abcguide.txt:774` documents `%%MIDI program
[c] n` with "**n (in the range 0-127)**" placed "in the track associated with
the voice previously declared" — abc2midi passes the raw 0-127 value while
General MIDI instrument *names* are 1-indexed (my `program=73` = GM name
#74 Flute, `program=33` = GM #34 Acoustic Bass). Riff's bridge must pin one
emission convention explicitly.

Channel correctness cross-check (vs DeepSeek's abcjs probe): abc2midi put
each voice's program change on its own note channel (chan=1/chan=2 above);
the abcjs path landed program changes on channel 0 in every track regardless
of the track's note channel. Channel behavior differs between the two
converters and must be part of the fidelity matrix.

Hypothesis (not selected architecture, per the coordinator-accepted
comparison framework): IF a server-side DAW render operation is adopted, it
needs a converter stage with its own provenance — abcjs runs in the browser
only (`web/` is static; Riff has no Node server component), so a pinned
server-side converter (abc2midi or equivalent) mapped onto the sources.json
pinning pattern is the leading candidate for that hypothesized stage.

Riff's existing converter is **client-side only**: abcjs
`getMidiFile(..., {midiOutputType:"binary"})` in `web/score.js:283-285`
runs in the browser; Riff today has no server-side ABC→MIDI path.

## 4. Cross-checks against riff-architecture.md (conductor)

- §3 citations verified against source: `abc` is free text with no server-side
  syntax validation (`studio_core.py:74`); abc/cot coupling at
  `studio_core.py:113-114`; saved-score exclusivity at `studio_core.py:115-119`;
  render_mode enum at `studio_core.py:120-122`; receipt stages at
  `operations.py:209-238` (`_stages`). §1 says "15 local patches";
  `sources.json` lists 16 patch entries — please recount.
- §3 gap: the doc states MIDI export is client-side but not the consequence —
  under the hypothesized server-side DAW operation, DAW-mutated material can
  only re-enter YuE2 as text ABC under `cot=melody|full` — token-exact
  `riff-score-v1` replay identity cannot survive a DAW round-trip, so DAW
  arrangements would need their own lineage/reference kind.
- §7.4 split needed: one-shot CLI engines (LMMS render, abc2midi) fit the
  cold-process builder; persistent engines (Ardour `luasession`, Carla+OSC)
  need the resident job-protocol path (`riff.jobs.v1` / measured
  `riff.resident.v1` leases) per §7.6 — two different integration shapes.
- §7.3/§7.5: per-instrument stems mean one job emits N audio artifacts; current
  provenance shapes bind one artifact per receipt (`SCORE_PROVENANCE`,
  `SOUND_PROVENANCE` single `artifact_id`, `operations.py:62-65`). A stem
  manifest reference (or N sub-references) is a contract addition, and adding
  receipt stages touches `RECEIPT_SCHEMA`/`SUBMISSION_SCHEMA` — the
  SURFACE_VERSION bump rule should be stated in the proposal.
- Platform weighting (conductor's question): macOS-arm64 desktop payload
  makes Qtractor a poor fit as the bundled DAW (JACK+ALSA mandatory,
  Linux-only), leaving a Linux render-service comparison open (§2.4).
  Ardour ships official macOS arm64 builds; LMMS 1.3.0-alpha.2 ships official
  macOS arm64 builds (macOS 11+/M1+) while stable 1.2.2 predates Apple
  Silicon; Zrythm/Carla/MuseScore have macOS builds. abcMIDI is dependency-free
  C (built here on Linux in ~9 s).
- License weighting under the ffmpeg separate-binary precedent, with the
  actual texts: Ardour COPYING is "GPL v2.0 or later" whose "Plugin
  Clarification" states only that dynamically-loaded third-party-API plugins
  ("VST, AudioUnits, LV2, LADSPA and CLAP") create no derivative relationship
  — it says nothing about embedding Ardour code elsewhere. LMMS LICENSE.txt
  is GPL v2 text with no linking exception: GPL §2 makes a combined work "a
  work based on the Program", and §2(b) requires licensing the whole under
  GPL when conveyed. Zrythm's LicenseRef-ZrythmLicense.txt is AGPL-3.0 where
  §13 states: "if you modify the Program, your modified version must
  prominently offer all users interacting with it remotely through a computer
  network ... an opportunity to receive the Corresponding Source of your
  version". Reading: separate-process bundling fits all three (ordinary
  conveyance + pinned sources per the ffmpeg pattern); linking copyleft DAW
  code into the MIT-licensed app or `audiocpp_cli` would pull the combined
  work under the copyleft, and any Zrythm patches carry §13 network-source
  duties. This is engineering-grade reading of the quoted texts, not legal
  advice; definitive interpretation is outside ensemble scope.

## 5. Open questions for the joint proposal

1. ~~Ardour headless file export~~ RESOLVED: `SimpleExport` Lua binding
   verified in source, `ardour8-export` CLI ships in the Ubuntu package, and
   DeepSeek produced non-silent runtime renders via both paths (sha256
   receipts). Ardour's remaining gate is **headless MIDI import**
   (`Editor:do_import` is GUI; template/generated-session path unproven).
   The decisive comparison is now which project format (.mmp XML vs Ardour
   session/template) can be generated and validated safely — the LMMS side
   has a passing receipt (§2.2); the Ardour side is open.
2. LMMS on macOS headless: does `lmms -r`/`-rendertracks` run without a GUI
   session on macOS (Qt platform plugin)? Linux behavior is documented; macOS
   behavior `[unverified]`.
3. abcjs-vs-abc2midi fidelity delta: CONFIRMED REAL — different
   channel-correctness for program changes (§3), chord-symbol
   auto-accompaniment track in abcjs vs voice pad/truncate in abc2midi,
   program-number convention documentation only on the abc2midi side
   (§3). DeepSeek's full matrix is the deeper layer; whichever converter
   Riff pins becomes the project's conversion truth.
4. Stem manifest contract shape (N artifacts per job) and SURFACE_VERSION
   policy for receipt-shape additions.
5. SoundFont/GM instrument availability on each platform for verified timbre
   claims — no voice label or program number may be presented as verified
   timbre (#17's honesty rule).
