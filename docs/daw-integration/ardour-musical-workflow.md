# Ardour 9.8 musical workflow probe (#78)

Author: riff-daw-deepseek-20261025. Status: bounded evidence for root review,
2026-10-25. This is a probe, not an integration commitment. It uses the shared
`probes/fixtures/arrangement-v{1,2}.abc` fixture and an **explicit adapter
contract**; it does not infer ABC timing.

## 1. Question

Can the Ardour template path carry a real four-voice arrangement — three
instruments, with PianoRH + PianoLH sharing one instrument/program 0 — through
generation, save/reopen, a musical edit, and headless mix + solo-stem renders,
with the rendered timeline traceable to abc2midi's SMF events?

This is the follow-up to `ardour-two-track-probe.md`. That probe proved a
two-track hand-patched template and solo-render stems; this one uses the
shared musical fixture, distinct gmsynth programs, tempo/meter conductor
events, reopening, and a v1→v2 score edit.

## 2. Fixture and timing adapter

- ABC: `probes/fixtures/arrangement-v1.abc` (v2 is the edited score).
- Voice map: `probes/fixtures/voice-map.json`. Four voices → three tracks:
  - Flute → program 73 (raw MIDI; GM flute)
  - Bass → program 33 (raw; GM acoustic bass)
  - PianoRH + PianoLH → **one** Piano track, program 0, two MIDI channels
- Timing: `probes/fixtures/arrangement-timing.json` is the explicit adapter
  contract, not an ABC parser output. It carries the expected quarter-note
  positions:

  | event | BBT | quarters | 480-tpq tick |
  | --- | --- | ---: | ---: |
  | bar 5 tempo 100→72 | 5\|1\|0 | 16 | 7680 |
  | bar 7 meter 4/4→3/4 | 7\|1\|0 | 24 | 11520 |
  | bar 8 tempo 72→100 + meter 3/4→4/4 | 8\|1\|0 | 27 | 12960 |
  | end | — | 31 | 14880 |

- Conversion: abc2midi 5.07 (source build, commit `6582eb8`) produces each
  voice's performance events. The adapter merges the selected voices per
  instrument into one SMF track. abc2midi's first event is at tick 1; the
  generator normalizes it to tick 0. Tempo/meter metadata in the source SMF
  matches the timing manifest.

## 3. Method

Committed scripts (all authored by this seat unless stated):

- `probes/ardour/build_session.py` — splits voices by instrument, runs
  abc2midi, merges notes per track, injects tick-0 program changes, and emits
  the 3001 template-shaped session directory.
- `probes/ardour/instrument_session.lua` — loads the generated session, adds
  the bundled `gmsynth` LV2 to every MIDI track, saves a snapshot.
- `probes/ardour/patch_tempo_range.py` — after Ardour's first load/save
  upgrade to session format 7004, installs the new-format tempo/meter map and
  the musical session/region extent (31 quarters; b59520).
- `probes/ardour/reopen_session.lua` — explicit load/save and reopen receipt;
  used once to upgrade the 3001 template to 7004 **before** instrumenting,
  and again after the tempo/range patch to save `v1-final`.
- `probes/ardour/save_stems.lua` — saves one solo snapshot per track.
- `probes/ardour/verify_timeline.py` — compares generated MIDI to abc2midi
  and checks the session tempo/meter/range structure.
- `probes/ardour/verify_stems.py` — sums stems and compares to the mix.
- `probes/verify/wav_note_check.py` (conductor/ensemble shared checker) —
  audio onset check against the abc2midi-derived expected timeline.

Representative commands (paths below are the local gitignored probe dirs):

```sh
# 1. generate three per-instrument MIDI sources and a 3001 session
python3 probes/ardour/build_session.py \
  --template .probe-tmp/deepseek/upstream/ardour/1region.ardour \
  --abc probes/fixtures/arrangement-v1.abc \
  --voice-map probes/fixtures/voice-map.json \
  --timing probes/fixtures/arrangement-timing.json \
  --out .probe-tmp/deepseek/ardour-workflow/v1-build \
  --session-name arrangement-v1 \
  --abc2midi .probe-tmp/abcmidi/src/abc2midi

# 2. upgrade-only load/save, then add gmsynth, then patch tempo/range,
#    then reopen/save.  Do the 3001->7004 migration before instrumenting;
#    instrumenting the 3001 template first yields a different (valid) panner
#    migration and therefore a different mix hash.
ARDOUR_PROBE_SESSION_DIR=.../v1 ARDOUR_PROBE_SNAPSHOT_IN=arrangement-v1 \
ARDOUR_PROBE_SNAPSHOT_OUT=upgraded ARDOUR_PROBE_LOG=.../upgrade.out \
  ardour9-lua probes/ardour/reopen_session.lua
ARDOUR_PROBE_SESSION_DIR=.../v1 ARDOUR_PROBE_SNAPSHOT_IN=upgraded \
ARDOUR_PROBE_SNAPSHOT_OUT=v1-instrumented ARDOUR_PROBE_LOG=.../instrument.out \
  ardour9-lua probes/ardour/instrument_session.lua
python3 probes/ardour/patch_tempo_range.py \
  --session .../v1/v1-instrumented.ardour \
  --timing probes/fixtures/arrangement-timing.json \
  --out .../v1/v1-tempoed.ardour
ARDOUR_PROBE_SESSION_DIR=.../v1 ARDOUR_PROBE_SNAPSHOT_IN=v1-tempoed \
ARDOUR_PROBE_SNAPSHOT_OUT=v1-final ARDOUR_PROBE_LOG=.../reopen.out \
  ardour9-lua probes/ardour/reopen_session.lua

# 3. render mix and solo stems
ardour9-export -o .../v1/export/v1-final.wav .../v1 v1-final
ARDOUR_PROBE_SESSION_DIR=.../v1 ARDOUR_PROBE_SNAPSHOT_IN=v1-final \
ARDOUR_PROBE_STEM_PREFIX=v1-stem- ARDOUR_PROBE_LOG=.../stems.out \
  ardour9-lua probes/ardour/save_stems.lua
for s in v1-stem-1 v1-stem-2 v1-stem-3; do
  ardour9-export -o .../v1/export/$s.wav .../v1 $s
done
```

## 4. Result

### 4.1 Session structure and tempo map

`v1-final.ardour` has exactly three MIDI tracks — `Flute`, `Bass`, `Piano` —
one `General MIDI Synth` per track, three MIDI sources and three playlists.
Piano is one track containing both voices: its source has channels 0 and 1,
both with program 0; no second piano instrument/track was created.

The saved session tempo map is exactly the adapter contract:

```
Tempo bbt=1|1|0 quarters=0   sclock=0           npm=100
Tempo bbt=5|1|0 quarters=16  sclock=2709504000  npm=72
Tempo bbt=8|1|0 quarters=27  sclock=5296704000  npm=100
Meter bbt=1|1|0 quarters=0   sclock=0           divisions-per-bar=4
Meter bbt=7|1|0 quarters=24  sclock=4591104000  divisions-per-bar=3
Meter bbt=8|1|0 quarters=27  sclock=5296704000  divisions-per-bar=4
```

Session range is `b0` → `b59520` (31 quarters), not a sample count. The mix
render is exactly **21.166667 s** at 48 kHz, matching 31 quarters under the
100/72/100 map and the expected bar-5/bar-8 ticks.

The reopen receipt logs `track 1 Flute`, `track 2 Bass`, `track 3 Piano`,
`tracks=3`, and `save ok=true`; the session is not only load-then-export.

### 4.2 Event-level equivalence to abc2midi

`verify_timeline.py` passes for both v1 and v2:

| track | notes (abc2midi / generated) | note events after +1-tick normalization | programs |
| --- | ---: | --- | --- |
| Flute | 12 / 12 | exact | ch 0 → 73 |
| Bass | 30 / 30 | exact | ch 0 → 33 |
| Piano | 37 / 37 | exact | ch 0 → 0, ch 1 → 0 |

"Exact" compares `(start_tick, duration, pitch, velocity)` multisets for the
selected voices. It also verifies that the same events survive Ardour's
load/save rewrite. This is performance-event preservation, not notation
preservation: the ABC text/notation structure is not stored in the SMF.

### 4.3 Rendered mix and solo stems

All files are 48 kHz, stereo, `pcm_s16le`, 21.166667 s, 4,064,044 bytes.

| file | peak dBFS | RMS dBFS | sha256 |
| --- | ---: | ---: | --- |
| `v1-final.wav` mix | -5.262841 | -18.931317 | `4175c184a15cc678d7656cad52797641641a3609e653e4fbe7dcf9f060c2babe` |
| `v1-stem-1.wav` Flute | -9.617769 | -19.977136 | `ff36b4f8397a77cc0983042ee2d1c464b1f348ea12e09d1afcbdc69f2a56cc18` |
| `v1-stem-2.wav` Bass | -19.802653 | -34.170883 | `407b548d2e253384ebad3c23eb862b0dbf811d88e204f403b0b34aa3d9d41245` |
| `v1-stem-3.wav` Piano | -11.056612 | -26.872109 | `c5be98a95386299db3b58fde967ac9e68b4db6184c80ce6f80aceda4bfd85b57` |

`verify_stems.py` samples-sums the three stems and compares them with the
mix: sum peak 17877 = mix peak 17877; difference peak **1 LSB**, difference
RMS 0.554 LSB. The three solo renders are therefore a complete per-track
decomposition of this master, within 16-bit rounding. This is solo-render
stem evidence, not the GUI stem-export dialog or raw pre-fader stems.

### 4.4 Program/timbre control

The three tracks carry program changes 73 (Flute), 33 (Bass), 0 (Piano),
injected at tick 0. A controlled check changed only the Flute source's
program change from 73 to 0, keeping every note event identical, and rendered
the same solo track again:

| variant | peak dBFS | RMS dBFS | spectral centroid* |
| --- | ---: | ---: | ---: |
| program 73 (flute) | -9.617769 | -19.977136 | 715.8 Hz |
| program 0 (piano) | -12.748644 | -32.565055 | 652.6 Hz |

*Centroid over a 0.4 s window at the sustained first Flute note (4.9 s),
Goertzel bins 100 Hz–5 kHz. Same notes, different program → different audio
and harmonic weighting. This shows gmsynth responds to per-track program
changes; it is not a claim that GM program 73 is a high-quality or real flute.

The bass stem is low-register and the piano stem contains both piano voices;
their distinct RMS/peak values are not by themselves a timbre claim. The
controlled program swap is the timbre evidence.

### 4.5 Edit v1 → v2 and reopen

The v2 edit was applied to a **copy of the same generated project**, not a
fresh session: the same `v1-final.ardour` snapshot was opened in the copied
directory, its three source MIDI files were replaced with the v2 abc2midi
output (same lengths/tempo map, edited pitches/chords), and the project was
reopened and saved as `v2-final`. Source hashes change for all three tracks;
the v2 mix is a different file:

| | v1 mix | v2 mix |
| --- | --- | --- |
| sha256 | `4175c184…` | `49dba1a4c9a6ebcdc5a9f776b1f9db22dfee3decd3be8097d159636a808594ad` |
| peak / RMS dBFS | -5.262841 / -18.931317 | -5.776736 / -18.751771 |
| duration | 21.166667 s | 21.166667 s |

v2's per-track MIDI also passes the same exact event check against abc2midi
v2 (12/30/37 notes; programs 73/33/0). This is a meaningful musical edit
receipt, not merely a second unrelated render.

### 4.6 Audio onset check

Using `probes/verify/wav_note_check.py` against the abc2midi-derived expected
events, with an 80 ms tolerance:

| render | expected events | matched | note |
| --- | ---: | ---: | --- |
| v1 Flute stem | 12 | 11 | one late-gesture note at 17.935 s not detected by the dominant-pitch detector |
| v1 Bass stem | 30 | **30** | all bass onsets matched |
| v1 Piano stem | 37 | 25 | polyphonic/chord onsets merge in the detector |
| v2 mix | 12/30/37 | 11/21/22 | mix-level, not stem-isolated; weaker comparison |

The structural boundaries land at the expected times: Flute/Bass/Piano
observed onsets include 4.8 s (bar 3), 9.6 s (bar 5 tempo change), 18.8 s
(bar 8, expected 18.7667 s), and the file length is exactly 21.1667 s. The
missing/mis-merged onsets are detector limitations on legato/polyphonic
material, not missing source events: §4.2 shows every source event is present
and the stems are nonzero and complete.

## 5. What this proves and does not prove

Proven for this fixture:

- A four-voice ABC arrangement can be represented as three Ardour MIDI tracks
  with PianoRH+PianoLH sharing one instrument/program, distinct per-track
  gmsynth programs, an explicit tempo/meter map, correct bar-5/bar-8
  positions, and exact abc2midi note-event preservation.
- The project can be saved, reopened, rendered as a mix and as three
  solo-render stems; stems reconstruct the mix within 1 LSB.
- A v1→v2 score edit can be applied to the same project topology and
  re-rendered.
- gmsynth honors the per-track MIDI program change (controlled 73↔0 test).

Not proven:

- A general ABC→Ardour generator. Timing is from an explicit manifest; the
  adapter does not infer meter/tempo/bar structure from arbitrary ABC.
- More than this 4-voice/3-track fixture, other voice-sharing topologies,
  arbitrary programs/banks, or instrument-quality claims.
- A supported headless MIDI import path (`Session::import_files`); this probe
  writes per-instrument SMFs and session XML from a template.
- Ardour GUI stem dialog, raw pre-fader stems, batch stem export without
  solo-state changes, or a production export-preset boundary.
- macOS/Arm64, source-built Ardour, or sample-accurate timing beyond the
  DummyMidiBuffer warnings and the ±80 ms audio onset tolerance.
- Licensing clearance: see `ardour-bundle-licensing.md`; gmsynth is GPL-family
  and GeneralUser GS is a custom-licensed SoundFont with a sample-provenance
  caveat.

## 6. Receipts

Gitignored local artifacts under
`.probe-tmp/deepseek/ardour-workflow/`:

- `v1/arrangement-v1.ardour` (generated 3001), `v1/v1-inst.ardour`
  (instrumented), `v1/v1-final.ardour` (patched + reopened)
- `v1/export/v1-final.wav`, `v1/export/v1-stem-{1,2,3}.wav`
- `v2-edit/v2-final.ardour`, `v2-edit/export/v2-final.wav`
- `progtest/export/prog0.wav`
- `v1/timeline-verify.json`, `v1/stems-verify.json`,
  `v1/{Flute,Bass,Piano}-onset.json`, `v2-edit/timeline-verify.json`
- `licensing-inventory.json`

Machine-readable summary: `docs/evidence/ardour-musical-workflow-20261025.json`.
Related: `ardour-headless-probe.md`, `ardour-two-track-probe.md`,
`abc-semantics.md`, `ardour-bundle-licensing.md`, issue #78.
