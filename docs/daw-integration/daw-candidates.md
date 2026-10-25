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
- Gap for Riff: **no documented headless offline export-to-file**. Bounce is
  in-session; a file-export path from `luasession` is `[unverified]` and is
  the single decisive experiment for Ardour's candidacy.
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
  a different file" — native per-track stems), `upgrade`, plus `--import`
  MIDI/Hydrogen; render options: wav/flac/ogg/mp3, samplerate 44100–192000,
  32-bit float, loop mode, oversampling
  (https://github.com/LMMS/lmms/blob/master/doc/lmms.1).
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
- Hard constraint: **JACK and ALSA are mandatory runtime infrastructure** —
  Linux-only. Eliminated for the macOS-arm64 desktop payload; conceivable
  only as a Linux-side render service, which the current product does not
  ship.

### 2.5 MuseScore 4 (adjunct, not the DAW)

- Role: notation conversion bridge and quick audition rendering, not a
  mixing/automation DAW.
- ABC import is **plugin-based** (bundled ABC Import plugin historically;
  third-party ABC_ImpEx uses wim.vree's `abc2xml`/`xml2abc`), not a native
  importer (https://musescore.org/project/abc-import,
  https://blechtrottel.net/en/abc_impex.html).
- CLI audio export: `[unverified]` here — handbook page URL not yet located;
  needs a source receipt before any claim.

### 2.6 Carla 2.5.8 (hosting component, not the DAW)

- License GPLv2-or-later (README, doc/GPL.txt). Hosts
  LADSPA/DSSI/LV2/VST2/VST3/AU + SF2/SFZ; **OSC remote control**; native
  audio drivers + JACK; itself embeddable as LV2/VST2 — LMMS master embeds it
  (https://github.com/falkTX/Carla, README).
- Relevance: candidate for a per-instrument hosting/render service under
  Riff's admission contract, independent of which DAW is selected.

### 2.7 Secondary candidates (surveyed, deprioritized)

- Stargate (stargatedaw/stargate): GPL-3.0, Python, pattern-based DAW with
  routing matrix and bundled plugins; PortAudio/PortMidi; headless story and
  release cadence `[unverified]`.
- BespokeSynth: GPL-3.0 modular live environment; VST/VST3/LV2 hosting,
  Python livecoding; live-patch oriented, no documented headless render; not
  an arranger DAW.

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
fault-tolerant; Riff's conversion stage must validate before converting.

Riff's existing converter is **client-side only**: abcjs
`getMidiFile(..., {midiOutputType:"binary"})` in `web/score.js:283-285`. A
server-side `daw.render` operation needs its own pinned converter stage
(abc2midi or equivalent) with converter provenance in the receipt — this maps
onto the sources.json pinning pattern (repo+commit+sha256).

## 4. Cross-checks against riff-architecture.md (conductor)

- §3 citations verified against source: `abc` is free text with no server-side
  syntax validation (`studio_core.py:74`); abc/cot coupling at
  `studio_core.py:113-114`; saved-score exclusivity at `studio_core.py:115-119`;
  render_mode enum at `studio_core.py:120-122`; receipt stages at
  `operations.py:209-238` (`_stages`). §1 says "15 local patches";
  `sources.json` lists 16 patch entries — please recount.
- §3 gap: the doc states MIDI export is client-side but not the consequence —
  any server-side DAW operation must add a conversion stage with its own
  provenance (see §3 above); and DAW-mutated material can only re-enter YuE2
  as text ABC under `cot=melody|full` — token-exact `riff-score-v1` replay
  identity cannot survive a DAW round-trip, so DAW arrangements need their own
  lineage/reference kind.
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
  eliminates Qtractor (JACK+ALSA mandatory, Linux-only) as the bundled DAW.
  Ardour ships official macOS arm64 builds; LMMS 1.3.0-alpha.2 ships official
  macOS arm64 builds (macOS 11+/M1+) while stable 1.2.2 predates Apple
  Silicon; Zrythm/Carla/MuseScore have macOS builds. abcMIDI is dependency-free
  C (built here on Linux in ~9 s).
- License weighting under the ffmpeg separate-binary precedent: Ardour
  (GPLv2+), LMMS (GPLv2), Carla (GPLv2+) all fit separate-process bundling
  with pinned sources; Zrythm adds AGPL §13 obligations on any modifications
  plus trademark terms; none may be linked into `audiocpp_cli` or the studio.

## 5. Open questions for the joint proposal

1. Ardour headless file export: does any documented/supported path write
   rendered audio files from `luasession` (export dialog automation, or
   bounce-to-file via Lua)? Decisive experiment; needs an Ardour build or
   official package on Linux to probe.
2. LMMS on macOS headless: does `lmms -r`/`-rendertracks` run without a GUI
   session on macOS (Qt platform plugin)? Linux behavior is documented; macOS
   behavior `[unverified]`.
3. abcjs-vs-abc2midi fidelity delta (DeepSeek's axis): the two converters do
   not agree on dynamics/articulation/instrument mapping; whichever Riff pins
   becomes the project's conversion truth.
4. Stem manifest contract shape (N artifacts per job) and SURFACE_VERSION
   policy for receipt-shape additions.
5. SoundFont/GM instrument availability on each platform for verified timbre
   claims — no voice label or program number may be presented as verified
   timbre (#17's honesty rule).
