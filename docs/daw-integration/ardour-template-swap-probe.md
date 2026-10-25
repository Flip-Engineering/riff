# Ardour template-swap construction probe (#78)

Author: riff-daw-conductor-kimi-20261025. Status: reversible feasibility
evidence, 2026-10-25. Complements DeepSeek's ardour-headless-probe.md (which
proved headless render of an existing session and found the headless MIDI
IMPORT gate). This probe tests the cheapest construction pattern for the
import gate: a pinned template session whose MIDI SOURCE FILE is replaced by
agent-authored content, then rendered headless. No selection is made.

## Probe

1. Two-voice ABC (`.probe-tmp/kimi/perinst.abc`: V:Flute `%%MIDI program 73`,
   V:Bass `%%MIDI program 33`, Q:1/4=100, 12 beats) → SMF via abc2midi 5.07
   `-NGUI` (293 bytes, format 1, 3 tracks; conductor + ch0 prog 73 + ch1
   prog 33 — per-channel programs correct).
2. Copied DeepSeek's `region-session` (the Ardour `1region` fixture +
   `gmsynthprobe` snapshot that already carries a gmsynth LV2 on MIDI track
   "MIDI 1") to `.probe-tmp/kimi/swap-session`; deleted `analysis/` cache;
   REPLACED `interchange/1region/midifiles/MIDI 1-1.mid` with the probe SMF
   (same filename, so session XML source/region references hold).
3. Rendered headless:
   `ardour9-export -o swap-session/export/swapped.wav swap-session gmsynthprobe`
   (official 9.8.241 demo build, extracted no-root; HOME isolated;
   nice -n 19, ionice -c3). Exit 0, 2,237,944 bytes, with benign-looking
   `DummyMidiBuffer: it's too late for this event` warnings (dummy backend
   MIDI timing; render still produced the notes).

## Receipt

`swapped.wav`: 48 kHz stereo 16-bit, 11.656 s, peak -4.43 dBFS
(NONZERO — content present). Coarse per-0.5s dominant-frequency scan:

- Multiple distinct note onsets (the fixture source was one long note — so
  the swapped content, not the fixture, rendered).
- Both registers present: ~131 Hz / ~195 Hz (bass line, C3/G3 region) and
  ~525 Hz / ~665 Hz / ~780 Hz / ~1050 Hz (flute line, C5–C6 region).
- Note activity ends ≈7 s, consistent with 12 beats at the session tempo,
  not with the fixture's original timing.

## What this proves and what it does not

PROVEN: arbitrary agent-authored multi-voice MIDI, produced by the pinned
abc2midi converter, can be injected into an Ardour session by file swap and
rendered to audible audio headlessly with per-channel GM programs honored by
one gmsynth instance. The template-swap construction is a viable project-
construction fallback for the Ardour import gate.

NOT PROVEN / next gates:
- N-track construction: this probe used ONE MIDI track reading a multi-track
  SMF (both voices through one gmsynth). True per-instrument tracks (N MIDI
  tracks, each its own plugin, each its own stem) need a template with N
  tracks each referencing its own single-voice SMF — same pattern, unprobed.
- Stem export: `ardour9-export` is master-bus mixdown; per-track stems via
  Lua `SimpleExport` are master-bus too; per-route stem export evidence is
  still missing.
- Octave/convention check: observed flute dominant ≈ C4/C5 region — exact
  octave assignment of ABC `c` through abc2midi→SMF→Ardour→gmsynth needs a
  note-level comparison (the fidelity matrix's pitch rows), done from the
  summary JSONs rather than this audio-level scan.
- Fresh-session generation (no fixture XML at all) and Lua `MidiModel`
  note authoring remain alternative construction paths, unprobed.
- Demo build caveat (DeepSeek's note): CLI tools ran normally; production
  claims should re-run on a pinned source build.
- Region length metadata (`b44758@b42374` beats) exceeded the swapped
  source content; the export range covered the content, but a production
  adapter must write consistent region lengths rather than inherit them.

Commands and artifacts: `.probe-tmp/kimi/` (perinst.abc, perinst.mid,
swap-session/, export.log). Related: `ardour-headless-probe.md`,
`abc-semantics.md`, `daw-candidates.md`.
