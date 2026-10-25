# Ardour multi-track template generation + render probe (#78)

Author: riff-daw-deepseek-20261025. Status: reversible feasibility evidence,
2026-10-25. This addresses the conductor's remaining "Ardour session
XML/template generation" gate; it does not select a DAW or commit an
integration architecture.

It is also the next gate named in the conductor's
`ardour-template-swap-probe.md`: that probe injected a multi-track SMF into
**one** MIDI track, while this one creates **two** MIDI tracks, each with its
own source and gmsynth, and verifies that both contribute.

## 1. Question

GLM's LMMS work is probing whether a project can be written and rendered
headlessly; the Ardour side of the same decisive gate is whether an
ABC-derived, per-voice project can be **generated** and then rendered without
the Editor. The earlier Ardour receipt proved headless Lua control and
headless `ardour9-export`, but used a single source-tree test track.

This probe asks: can a two-track session with distinct MIDI sources be built
from a static template by deterministic XML edits, instrumented, saved, and
rendered headlessly with both tracks present in the master?

## 2. Method

Base template: `libs/ardour/test/profiling/sessions/1region/1region.ardour`
(session version 3001, one MIDI track, one playlist/region, one MIDI source).
The probe:

1. Copied the template and its `MIDI 1-1.mid` source.
2. Added a second ABC-derived SMF: `E G B d |`, `M:4/4`, `L:1/4`,
   `Q:1/4=120`, converted with abc2midi to E4/G4/B4/D5.
3. Duplicated the MIDI `<Route>`, `<Playlist>` and `<Source>` XML blocks with
   unique IDs, names and playlist link; added the second source file
   `interchange/1region/midifiles/MIDI 2-1.mid`.
4. Loaded the generated session with `ardour9-lua`, added the bundled
   `gmsynth` LV2 instrument to **both** MIDI tracks, and saved snapshot `two`.
5. Rendered with the shipped CLI exporter:
   `ardour9-export -o two-session/export/two-gmsynth.wav two-session two`.

The generated `two.ardour` contains three routes (master + MIDI 1 + MIDI 2),
two MIDI sources and two MIDI playlists. The Lua run added gmsynth to both
tracks and reported `tracks=2`; `save_state("two")` returned 0.

## 3. Result

| Property | Value |
| --- | --- |
| File | `.probe-tmp/deepseek/ardour-probe/two-session/export/two-gmsynth.wav` |
| Format | WAV, `pcm_s16le`, 48 kHz, stereo |
| Duration | 11.655729 s |
| Peak | **-8.661645 dBFS** |
| RMS | **-29.183008 dBFS** |
| sha256 | `b5b0657c92876c6bef8485bb15d9ae8fd93aad240e4e6f23fa16436f0f6aa6bb` |
| Tracks in session | master + MIDI 1 + MIDI 2 |
| Sources/playlists | 2 / 2 |
| MIDI notes | 4 (MIDI 1) + 4 (MIDI 2) = 8 |

**Both tracks contribute:** comparing the two-track master against the
one-track four-note export (same first track and same gmsynth), the sample
difference is nonzero — RMS difference **811**, peak difference **7625**,
samples not identical. The two-track master RMS is 1,138 vs 799 for the
one-track file (linear 16-bit RMS units). This is a mix-level contribution
check, not a stem isolation.

The export log again showed `DummyMidiBuffer: it's too late for this event`
warnings (one per track per event group). They do not prevent nonzero audio,
but this receipt is not sample-accurate scheduling evidence.

### 3.1 Per-route stems by solo rendering

To address the conductor's second follow-up — per-route stem evidence — I
loaded the saved `two` snapshot and used the Lua `solo_control()` on each MIDI
track:

```lua
tracks[1]:solo_control():set_value(1.0, PBD.GroupControlDisposition.NoGroup)
tracks[2]:solo_control():set_value(0.0, PBD.GroupControlDisposition.NoGroup)
s:save_state("stem1")
-- then swap the solo values and save "stem2"
```

Each snapshot was rendered with `ardour9-export`:

| Stem | Peak | RMS | sha256 | Notes |
| --- | --- | --- | --- | --- |
| stem1 | -13.130774 dBFS | -32.252030 dBFS | `2b24541a…` | exactly the same bytes as the one-track four-note export |
| stem2 | -12.662798 dBFS | -32.126983 dBFS | `a274c970…` | distinct nonzero track 2 content |

Both are 48 kHz stereo 16-bit, 11.655729 s and live at
`.probe-tmp/deepseek/ardour-probe/two-session/export/stem1.wav` and
`stem2.wav`. Adding stem1 and stem2 sample-wise
equals the two-track master within **1 LSB** (difference RMS 0, peak 1), so the
two solo renders are a complete per-track decomposition of the master.

This is **solo-render stem evidence**, not Ardour's GUI stem-export dialog or
a per-route `SimpleExport` API. It proves each MIDI track can be isolated,
rendered, and reconstructed; it does not prove raw pre-fader stems, batch
stem export, or stem export without changing session solo state.

### 3.2 Four-track follow-up

The same generator was run with **four** MIDI tracks and four single-voice
SMFs (`C D E F`, `G A B c`, `c B A G`, `E G B d`), each with its own gmsynth
instance. The generated `four.ardour` contains five routes (master + four MIDI
tracks), four sources and four playlists, with 16 notes total.

Master render:

| Property | Value |
| --- | --- |
| File | `four-session/export/four.wav` |
| Format | WAV, `pcm_s16le`, 48 kHz, stereo |
| Duration | 11.655729 s |
| Peak | **-4.547775 dBFS** |
| RMS | **-25.015218 dBFS** |
| sha256 | `d3e15a8e36853b37cb99f33c80e27077dc1792a6f0d3c395288b3c42ed7e7a9f` |

Per-track solo stems (same method as §3.1):

| Stem | Peak | RMS | sha256 |
| --- | --- | --- | --- |
| fstem1 | -13.130774 dBFS | -32.252030 dBFS | `2b24541a…` |
| fstem2 | -12.522700 dBFS | -31.157270 dBFS | `5bea3f4b…` |
| fstem3 | -12.008128 dBFS | -31.376740 dBFS | `e77b064a…` |
| fstem4 | -12.662798 dBFS | -32.126983 dBFS | `a274c970…` |

All four stems are distinct and nonzero. Adding fstem1…fstem4 sample-wise
reconstructs the four-track master within **2 LSB** (difference RMS 0, peak 2;
sum peak 19411 equals master peak 19411).

This strengthens the N-track result from N=2 to N=4 and gives per-track stem
receipts for all four tracks. It is still the same hand-patched template
pattern, with default gmsynth program 0 on every track; it does not validate
a production generator, distinct programs/banks, batch stem export, or macOS.

## 4. What this proves and does not prove

Proven:

- A static Ardour session template can be duplicated into a **two-track**
  session with distinct MIDI sources by deterministic XML edits.
- `ardour9-lua` can add an instrument to both generated tracks and save a
  snapshot headlessly.
- `ardour9-export` renders the generated session's master bus headlessly.
- The second generated track contributes audibly to the master.
- Per-track **solo-render stems** are proven for N=2 and N=4: each track can
  be soloed, rendered separately, and the stems reconstruct the master within
  **1 LSB (N=2)** / **2 LSB (N=4)**. This is not Ardour's GUI stem-export
  dialog, but the audio stems are real, distinct and complete.

Not proven:

- A general, version-pinned **session generator** for arbitrary voice counts,
  names, instrument maps, tempo/meter changes or region layouts.
- Headless **MIDI import** through a supported API; the probe edited a
  template and replaced source files, not `Session::import_files`.
- Ardour's **GUI stem-export dialog**, raw pre-fader stems, batch stem export,
  or stem export without changing session solo state.
- Distinct gmsynth programs/banks; both tracks use the default program 0.
- Reopening and editing the generated project after export; only load/save/
  render were exercised.
- macOS/Arm64, source-build reproducibility, or absence of Dummy-backend
  timing defects.

## 5. Implication

This materially strengthens the Ardour side of the project-generation gate:
the pattern "template XML + per-voice MIDI sources + Lua instruments + CLI
export" is feasible and produced a two-track audible master. The next step is
not another one-off: it is a deterministic generator that can be re-run from
a score manifest, plus validation for 4–8 voices, explicit program mappings,
reopening, macOS, and (separately) a supported batch-export path if GUI-free
raw stems are required. If that generator proves brittle against
Ardour session-format changes, the fallback is a first-class headless engine
(Tracktion Engine) rather than claiming Ardour headless authoring.

## 6. Receipts

Local (gitignored) under `.probe-tmp/deepseek/ardour-probe/`:

- `two/second.abc`, `two/second.mid`, `two/addgmsynth.lua`, `two/addgmsynth.out`
- `two-session/1region.ardour` (generated two-track session)
- `two-session/two.ardour` (saved snapshot)
- `two-session/export/two-gmsynth.wav`
- one-track comparison `four-session/export/four-gmsynth.wav`

Commands:

```sh
abc2midi two/second.abc -o two/second.mid
# deterministic duplicate of Route/Playlist/Source XML with new IDs
ardour9-lua two/addgmsynth.lua        # adds gmsynth to both tracks, saves "two"
ardour9-export -o two-session/export/two-gmsynth.wav two-session two
ffprobe -v error -show_entries format=duration,size:stream=codec_name,sample_rate,channels,bits_per_sample two-session/export/two-gmsynth.wav
```

Related: `docs/daw-integration/ardour-headless-probe.md`,
`docs/daw-integration/abc-semantics.md`, issue
https://github.com/Flip-Engineering/riff/issues/78.
