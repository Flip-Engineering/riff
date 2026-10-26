# ABC → MIDI fidelity for DAW integration (#78)

Author: riff-daw-deepseek-20261025. Status: evidence for the joint selection
proposal, 2026-10-25. This document changes no Riff contract and selects no
DAW. It answers the DeepSeek fidelity axis agreed with the conductor and GLM:
what survives when Riff's ABC reaches a DAW, and which parts need an explicit
representation instead of a "lossless conversion" claim.

## 1. Why this axis is decisive

Riff's current production path is a single YuE2 performance. The new task is
per-instrument composition and production in a DAW. That means ABC is no
longer only conditioning text for one generator: it must become tracks,
instruments, note events, tempo/meter maps, mix state and audio. The
conversion boundary is therefore part of the product contract, not a UI
export convenience.

Two actual converters were exercised:

- **abcjs 6.7.0**, the exact bundle Riff ships and calls in the browser:
  `web/score.js:283-286` calls
  `ABCJS.synth.getMidiFile(source.value, {midiOutputType:"binary"})`.
  Bundle `web/vendor/abcjs-basic-min.js`, sha256
  `b0cde4bc52bb33949181683a245005fff8a024a8c1f07ec6ce3222cd4bd72e51`,
  signature `abcjs-basic v6.7.0`; package pin `package.json:19`, NOTICE
  `NOTICE.md:14`.
- **abc2midi 5.07 (2026-10-14)** from the abcMIDI source tree built in
  `.probe-tmp/abcmidi`, the converter used as the candidate/import evidence
  layer by GLM (upstream `https://github.com/sshlien/abcmidi`; GLM's probe
  records commit `6582eb8`, VERSION `October 14 2026`).

Riff has **no server-side ABC parser**. `abc` is free text
(`studio_core.py:74`); the only semantic rules are that supplied ABC needs
`cot=melody|full` (`studio_core.py:113-114`) and is exclusive with a saved
score (`studio_core.py:115-119`). The browser's abcjs is the sole parser today.
Any DAW operation must add a pinned conversion stage with its own provenance;
it cannot silently reuse the UI export and call it authoritative.

## 2. Method and corpus

Eighteen small ABC cases plus a two-voice smoke probe were converted with
both tools and parsed with a dependency-free SMF parser. The command shape was:

```sh
# from the shared checkout
node .probe-tmp/deepseek/abc2js.js cases/CASE.abc out/CASE.abcjs.mid
.probe-tmp/abcmidi/src/abc2midi cases/CASE.abc -o out/CASE.abc2midi.mid
python3 .probe-tmp/deepseek/midi_summary.py out/CASE.abc2midi.mid > out/CASE.abc2midi.summary.json
python3 .probe-tmp/deepseek/midi_summary.py out/CASE.abcjs.mid  > out/CASE.abcjs.summary.json
```

The harness files and full event summaries remain in the gitignored
`.probe-tmp/deepseek/abc/` on the shared host; the compact receipts are in
`docs/evidence/abc-semantics-20261025.json`. Cases:

| Case | Constructs exercised |
| --- | --- |
| A | `M:`, `L:`, `Q:`, `K:`, two `V:` voices, `%%MIDI program 73/33` |
| B | inline `[M:3/4]`, `[K:G]`, `[Q:1/4=60]` |
| B1/B2/B3 | inline meter only / key only / tempo only |
| C | `|:` repeat with `[1` and `[2` endings |
| D | `!pp! !p! !mp! !mf! !f! !ff! !accent! !staccato!` |
| E | `!staccato! !tenuto! !accent! !marcato!`, slur `(G2 A2)`, tie `C4- C4`, grace `{g}` |
| F | `(3` triplet, `(5` quintuplet, `(7` septuplet |
| G | chord symbols `"Am" "F" "G" "C"` plus `w:` lyrics |
| H | voice labels `Bass`, `Gtr`, `Arp` with no `%%MIDI program` |
| I | `%%MIDI channel 10` percussion |
| J | three voices with explicit `%%MIDI channel 1/2/3` and programs 40/41/42 |
| K | unequal voice lengths (2 bars vs 1 bar) |

All files are 4/4 except A (3/4) and B's inline section; all use
`L:1/8` except G (1/4). Both converters were run on the same bytes.

Case K is the multi-voice edge check: abc2midi warns that the second voice
is shorter, but still emits its two notes; abcjs also emits two notes. Neither
pads the short voice to a common length, so DAW import gets tracks of unequal
extent. This is a layout decision for the bridge, not a preservation guarantee.

## 3. Fidelity matrix

Classes: **preserved** = recoverable from SMF; **converted** = represented by
an explicit MIDI mechanism with a documented approximation; **expanded** =
written structure is flattened and no longer recoverable from SMF; **dropped**
= absent from SMF. "Preserved" does not mean the DAW reconstructs ABC notation.

| ABC construct | abc2midi 5.07 | abcjs 6.7.0 | Class |
| --- | --- | --- | --- |
| Pitch / note on/off / rests | Note events; rests are gaps | Note events; rests are gaps | preserved as performance data, not notation |
| Note durations and tuplets | Tick durations; `(3` 160, `(5` 96, `(7` 67 ticks | Tick durations; `(3` 160, `(5` 96, `(7` 69 ticks (rounding) | converted with quantisation |
| Header `M:`, `K:`, `Q:` | Time-sig, key-sig and tempo meta at tick 0 | Same at tick 0 | preserved as MIDI meta |
| Inline `[M:]`, `[K:]`, `[Q:]` | Changes emitted at the correct tick (B: 3/4, G, 60 BPM at 1920/1921) | No inline meta; key affects pitch, but **K/Q cases double subsequent note durations** (B2/B3) | converted by abc2midi; approximated/buggy in abcjs |
| Default tempo when `Q:` absent | 120 BPM | 180 BPM | converter default; not ABC semantics |
| `V:` voice label | Separate format-1 track and channel; label is not a track name | Separate track; label is not a track name; no name events | converted to track/channel, name dropped |
| `%%MIDI program N` | Program change on the voice's own channel (A: 73/33 on ch0/1; J: 40/41/42 on ch0/1/2) | Program changes all on **channel 0** (A: 33 on ch0; J: 41/42 on ch0), while notes are on ch1/ch2 | abc2midi converted; abcjs **misroutes** programs |
| `%%MIDI channel N` | Notes on requested channel (I: ch9; J: ch0/1/2) | Notes on requested channel (I: ch9; J: ch0/1/2) | preserved for notes; program routing still wrong |
| Voice label without program (H) | No program events at all; no GM inference | Program 0 on ch0 for every voice; no GM inference | **instrument intention is dropped** |
| Chord symbols `"Am"` | Generates a separate gchord track with bass + triad notes, channels 1/2 | Generates a separate chord track with root+triad, channel 1 | **converted into unrequested notes**, not metadata |
| `w:` lyrics | Text meta events in the note track (`/Hel`, `lo`, ...) unless `-lyrics`/`-STFW` used | No lyric/text events at all | dropped by abcjs; non-standard text by abc2midi |
| `|:` repeats and `[1`/`[2` endings | Expanded to 16 note events (C) | Expanded to the same 16 note events | **expanded**; repeat/ending structure absent |
| `!pp!` ... `!ff!` | Velocities 45/50/65/80/105/110 | Same velocity table (D) | converted to velocity, not continuous dynamics |
| `!accent!` | Velocity 110 | Velocity 127 | approximation; differs by converter |
| `!staccato!` / `.` | `!staccato!` ignored with a warning; `.D2` shortens duration (probe) | `!staccato!` leaves duration unchanged | approximation / dropped |
| `!tenuto!`, `!marcato!` | Ignored with warnings (E) | No effect observed | dropped |
| Slur `(G2 A2)` | No timing/velocity effect | No effect observed | dropped as performance semantics |
| Tie `C4- C4` | Merged into one 1920-tick note | Merged into one 1920-tick note | converted (notation tie structure lost) |
| Grace `{g}A4` | Grace 59 ticks + main 899 ticks | Grace 480 ticks + main 480 ticks | approximation; converters disagree |
| Track names | First track gets the tune title; voice names not used | No track-name events | dropped/partial |
| Start times | Notes start at tick 1; durations end one tick early | Notes start at tick 0 | converter artefact (1/480 quarter in A) |
| Velocity stress | Adds metrical stress even without dynamics (e.g. 105/80/95) | Adds its own velocity pattern (e.g. 105/85/95) | converter artefact |
| Controller setup | No initial CCs | CC121=0, CC64=0, CC91=48, CC10=64, CC7=100 at tick 0 | converter artefact; may override DAW defaults |
| SMF layout | Format 0 for one voice; format 1 for multiple | Always format 1 with a meta track + one track per voice | converted |

### 3.1 The abcjs instrument-routing defect is material

Case A is two voices with `%%MIDI program 73` and `%%MIDI program 33`.
abcjs emitted:

- track 1: notes on channel 0, program 73 on channel 0;
- track 2: notes on channel 1, **program 33 on channel 0**.

Case J repeats the pattern with three explicit channels: programs 40, 41, 42
were all emitted on channel 0; notes were on channels 0, 1 and 2. In MIDI
semantics, channel 1/2 notes therefore have no program of their own; a GM
player hears whatever channel 0 last selected. A DAW that assigns an
instrument per imported track may mask this, and a DAW that honors embedded
program changes will not. Either way, the current Riff MIDI export is not a
trustworthy per-instrument bridge without a converter-side fix and a test.

The same defect lets chord-track and percussion programs leak onto channel 0
while their notes are elsewhere (G, I). This is exactly the kind of silent
timbre mismatch #17 warns against.

### 3.2 Inline field timing in abcjs 6.7.0

Isolating the fields shows the effect:

- B1 `[M:3/4]` only: note durations remain correct; the 3/4 meta is not
  emitted.
- B2 `[K:G]` only: the F is correctly turned into F#, but every note after
  the field has twice the expected duration (G 480 instead of 240 ticks).
- B3 `[Q:1/4=60]` only: no tempo meta at the change, and durations after it
  are doubled in the same way.

The exact parser cause was not established, so the safe statement is
behavioural: **at the pinned version, inline key/tempo fields are not safe in
the MIDI export path**. abc2midi emits the expected 3/4, G and 60 BPM meta
events at the change point. A server-side converter avoids the defect; a
client-side export would need a regression test and probably an upstream fix.

### 3.3 What "instrument" means in ABC

There is no standard, timbre-guaranteeing instrument field in ordinary ABC.
`%%MIDI program` is an abc2midi/ABC-2.1 MIDI directive, not a property of a
voice label, and even it is only a program number. Case H shows the honest
behaviour: `V:Bass`, `V:Gtr`, `V:Arp` without programs produces no instrument
assignment (abc2midi) or a default program 0 on channel 0 (abcjs). A DAW
integration therefore needs an **explicit voice→instrument map**:

- a program/bank or plugin identifier per voice;
- a channel or track assignment chosen by the converter, not inferred from
  the voice name;
- a receipt that records the map and the converter version;
- an audition/verification step before claiming the timbre was realized.

The bundled GM synth is a useful default, but a program number is still a
suggestion until the DAW and its soundfont/plugin are known.

A second convention hazard is numbering. abc2midi writes the raw MIDI program
byte (0-127), while common General MIDI lists are 1-indexed (raw 73 is GM
program 74, flute). The probe used `%%MIDI program 73/33` and observed raw
program bytes 73/33. Any instrument manifest must say whether it stores the
raw byte or the published GM number; otherwise a per-instrument score can be
off by one before the DAW is even involved.

## 4. Consequences for a DAW integration

These are evidence-backed constraints, not a selected architecture:

1. **Do not use `ABCJS.synth.getMidiFile` as the production bridge** without
   fixing/validating program routing and inline fields. It is the current UI
   audition path, not a converter with a fidelity contract.
2. **Convert ABC to an explicit score/MIDI representation server-side**, with
   a pinned converter (abc2midi or a purpose-built one) and a receipt carrying
   the ABC sha256, converter version/options and instrument map. The existing
   `sources.json` pinning pattern is the natural precedent.
3. **Separate score semantics from performance realisation.** Repeats,
   articulations, dynamics, slurs, ties, grace and chord symbols must be
   recorded in a sidecar or score IR when they matter. A MIDI file cannot be
   read back as ABC; a DAW round-trip must not be described as lossless.
4. **Handle chord symbols explicitly.** Both converters invent accompaniment
   notes from chord symbols by default. If the DAW is to realise harmony as
   its own instrument, either supply an explicit chord track or disable the
   converter's generated chord track (abc2midi `-NGUI`); never let an
   unrequested backing part silently double the arrangement.
5. **Expect velocity, not dynamics.** Both converters turn dynamic marks into
   note velocities and add their own stress/velocity defaults. A DAW
   automation lane is a separate, explicit authoring action, not a recovered
   ABC dynamic.
6. **Plan for explicit tempo/meter maps.** abc2midi preserves inline changes;
   abcjs 6.7.0 does not. Any arrangement with mid-song changes must be
   validated at the event level.
7. **Keep the ABC text authoritative.** The exact ABC, its token/score
   identity where available, and the conversion receipt should travel with
   the DAW project. Editing MIDI/DAW state creates a new branch, not a new
   "lossless ABC".
8. **Stem identity is a separate contract.** One MIDI track per voice is a
   start, but stems are audio artifacts with their own routing and gain; a
   voice label or MIDI channel is not a stem guarantee. The receipt needs an
   explicit stem manifest if per-instrument audio is promised.

## 5. Limits and open questions

- The probe used tiny, synthetic ABC and a Linux x86_64 host; no macOS or
  Apple Silicon run.
- abcjs was exercised through the exact vendored bundle in Node, not through
  the Riff web UI's audio path; `getMidiFile` is the same function the UI
  calls, but browser-side DOM/audio state was not involved.
- abc2midi was used as a reference converter, not selected as Riff's
  production converter. The upstream commit is recorded by GLM; the local
  binary version is `5.07 October 14 2026`.
- Microtonal ABC, percussion mappings beyond channel 10, multiple tunes per
  file, malformed-input recovery and very large scores were not covered here.
  GLM separately observed that abc2midi aborts subsequent note generation
  after a syntax error; conversion must therefore validate and reject, not
  silently truncate.
- No claim is made that any DAW imports these files identically. The next
  gate is DAW-side import/round-trip evidence for the selected candidate.

## 6. Receipts

The compact JSON includes the case ABC, expected note counts, converter
summaries (tracks, channels, programs, tempo/time/key events, selected notes)
and the observations above. Full event dumps and the harness remain under
`.probe-tmp/deepseek/abc/` (gitignored) on the host that ran the probe.

Related evidence:
- Riff architecture: `docs/daw-integration/riff-architecture.md` §3.
- Candidate landscape: `docs/daw-integration/daw-candidates.md` §3.
- Issue: https://github.com/Flip-Engineering/riff/issues/78
