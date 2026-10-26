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

Build under test: official **9.8.241 DEMO NIGHTLY** binary
(`Ardour_x86_64-9.8.241-demo`), not the 9.8 release source tag — source
claims in this file and in `ardour-headless-probe.md` cite tag `9.8`; the
runtime receipts come from the demo build and must not be quoted as release
behavior without that distinction.

`swapped.wav`: 48 kHz stereo 16-bit, 11.656 s, peak -4.43 dBFS
(NONZERO — content present). The export log contains repeated
**`DummyMidiBuffer: it's too late for this event` warnings** (dummy backend
MIDI timing); the render still completed, but these warnings are retained
here as a correctness caveat, not explained away.

Coarse per-0.5s dominant-frequency scan (a numerical content check, NOT a
listening assessment):

- Multiple distinct note onsets (the fixture source was one long note — so
  the swapped content, not the fixture, rendered). [inference from content
  difference, not a controlled comparison]
- Energy present in both a low band (~131 Hz / ~195 Hz) and a higher band
  (~525–1050 Hz). This is SUGGESTIVE that both voices contributed to the
  master mix; it is NOT isolated proof that both intended voices or their
  intended instruments rendered correctly — a single mixed master cannot
  separate them. Establishing that needs a controlled receipt (e.g.
  mute/solo renders or per-track stems) — retained as a qualification item.
- Note activity ends ≈7 s, consistent with 12 beats at the session tempo,
  not with the fixture's original timing. [inference]

## What this proves and what it does not

PROVEN: arbitrary agent-authored multi-voice MIDI, produced by the pinned
abc2midi converter, was injected into a template session by file swap and a
nonzero master render was produced headlessly by the 9.8.241 demo build.
Content scan is consistent with the swapped material playing (multiple
onsets, low+high band energy). Per-channel GM program handling by the
single gmsynth instance is PLAUSIBLE from the mix but NOT isolated —
unproven until a controlled per-voice receipt exists.

NOT PROVEN / qualification items for the proposal (not authorization to
implement before review):
- N-track construction (N MIDI tracks, each its own plugin and stem).
- Controlled voice isolation (mute/solo or stems) proving each intended
  voice/instrument renders.
- Stem export: `ardour9-export` and Lua `SimpleExport` are master-bus;
  per-route stem export evidence is missing.
- Generalized session GENERATOR (no fixture XML inheritance), session
  REOPEN round-trip of generated sessions, and EXPLICIT per-track program
  assignment in generated sessions.
- Octave/convention check: exact octave assignment of ABC `c` through
  abc2midi→SMF→Ardour→gmsynth needs a note-level comparison.
- Demo-vs-release: receipts are from the demo nightly; release-tag behavior
  and a pinned source build remain qualification work.

Commands and artifacts: `.probe-tmp/kimi/` (perinst.abc, perinst.mid,
swap-session/, export.log). Related: `ardour-headless-probe.md`,
`abc-semantics.md`, `daw-candidates.md`.
