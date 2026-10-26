# DeepSeek review of the joint proposal (`d6bbf98`)

Author: riff-daw-deepseek-20261025. Date: 2026-10-25.
Verdict: **SIGN-ON to LMMS-first as the first render backend under H1, with
two factual corrections and explicit gates.** I do not object to the
selection; I object to two specific claims in the current text that overstate
or understate the receipts.

## 1. Ardour "no per-track stem export evidence" is wrong

The proposal's §1/§2 table/§5.2 say Ardour has no per-track stem evidence.
The committed probe at `fa78626` already includes a per-route stem section:
`docs/daw-integration/ardour-two-track-probe.md` §3.1 uses Lua
`solo_control()` to solo each of the two MIDI tracks, saves `stem1`/`stem2`,
and renders each with `ardour9-export`:

- stem1: -13.130774 dBFS peak, sha256 `2b24541a…` — byte-identical to the
  one-track four-note export.
- stem2: -12.662798 dBFS peak, sha256 `a274c970…` — distinct nonzero track 2.
- stem1 + stem2 equals the two-track master within 1 LSB (difference RMS 0,
  peak 1).

The current working tree extends the same probe to four tracks and four
stems (uncommitted at review time; four-track master peak -4.55 dBFS, four
distinct stems reconstructing the master within 2 LSB). Even without the
extension, the committed two-track stem receipt is enough to correct the
proposal.

Required edits:

- §1 bullet "no per-track stem export evidence" → "solo-render per-track
  stems proven; GUI/raw/batch stem export unproven."
- §2 table Audio export row for Ardour → "master export + solo-render
  per-track stems `[probe]`; GUI/raw/batch stems unproven."
- §5.2 "per-track stems" → "raw/pre-fader/GUI/batch stems."

This does not automatically change the recommendation: Ardour still lacks a
general session generator, has heavier startup and a self-described
"prototyping stage" Lua surface. LMMS's native `rendertracks` and simpler
`.mmp` still justify first-backend status. It does change the comparison and
the upgrade-path wording.

## 2. LMMS "COMPLETE receipt chain for the product aim today" overstates

The LMMS receipts are strong and I accept them:

- `render`/`rendertracks` are QCoreApplication-only (`src/core/main.cpp`,
  v1.3.0-alpha.2); the AppImage rendered a hand-written 2-track `.mmp`
  headlessly, produced distinct per-track stems, and three renders are
  byte-identical (`out.wav`, `det1.wav`, `det2.wav` all sha256
  `d63f77e9…90fe08`).
- `write → MMPZ → dump` preserves 11 notes; the `.mmp` format is
  agent-writable XML; official macOS arm64 builds exist.
- The `--import` correction is properly applied: it is GUI-only and does not
  save a project.

But "complete receipt chain for the product aim today" is too broad. What is
receipted is **hand-written `.mmp` → headless mix + stems**, not:

1. **ABC → MIDI → `.mmp` → render** end-to-end with explicit per-instrument
   mapping. The abc2midi and `.mmp` receipts are separate components; the
   actual product bridge is unprobed.
2. **macOS arm64 headless render**. Official builds exist, but no runtime
   receipt on the product's desktop target.
3. **Instrument/timbre path**. LMMS ships no GM soundfont; the probe used
   TripleOscillator tracks, not per-instrument GM/SF2 mapping, and no timbre
   was audition-verified.
4. **Stem-vs-mix semantics**. I reproduced the mismatch: mix peak is clipped
   at 32767, stem sum peak is 32768, sample difference RMS 7775. Stems are
   not a simple master decomposition in this probe; pre/post-master behavior
   must be documented.
5. **Project generator**. Hand-written `.mmp` has a silent-layout landmine
   (`Track::loadTrack` clip placement) and must be validated by
   load-and-render, not parse success.

Required edit: change §1's "COMPLETE receipt chain for the product aim today"
to "complete headless project→mix+stems chain for a hand-written `.mmp`."
Keep the end-to-end conversion, macOS headless, instrument mapping, stem
semantics, clipping/level, generator validation and alpha version-lock as
explicit gates (most already appear in §5; they must not be buried under a
"complete" headline).

With that narrowed claim, **LMMS-first is evidence-sufficient for a bounded
first backend**: it is the only candidate with a working headless
project→render→native-stems path, a writable project format, and deterministic
output. The product chain remains to be demonstrated.

## 3. ABC matrix references are stale; substance is supported

The proposal says "10 cases × 2 converters"; the committed matrix has
**18 cases plus a two-voice smoke probe** (19 receipts), including B1/B2/B3
inline isolation, E1 grace/tie, G2–G4 chord/lyric isolation, K unequal
voices, and the two-voice probe. Required edit: say "18 cases + probe" (or
just "19 receipts").

The substantive ABC claims in §3 are supported by my receipts:

- abcjs program events all land on channel 0 while notes may be on other
  channels; inline `[K:]`/`[Q:]` can double subsequent durations; abcjs drops
  lyrics; both converters synthesize a chord track from chord symbols.
- abc2midi preserves header and inline M/K/Q metas, per-voice channels and
  programs, expands repeats, maps dynamics to velocity, and has the documented
  articulation/grace approximations.
- Voice labels are not instruments; GM numbering needs the raw-0–127 vs
  1-indexed convention pinned.

One citation is outside my receipt: `abcguide.txt:774` for the numbering
convention. My receipt proves the raw-byte behavior, not that line. Keep it
only with a direct source link or drop the line reference.

## 4. Tracktion framing: no objection

`tracktion-engine-note.md` supports the proposal's framing: Tracktion Engine
3.2 is an engine/library, not an end-user DAW; it has the strongest
programmatic MIDI/Renderer/ProjectManager surface; it is GPLv3/commercial
plus a JUCE licence obligation; no runtime probe; no bundled instruments/GM
soundfont. The proposal correctly leaves H3 as a "build a service"
alternative for review, not the selected first backend.

## 5. Sign-on statement

I sign on to **LMMS-first under H1**, conditional on the two corrections above
and on these gates being explicit in the root-facing proposal:

1. End-to-end ABC→MIDI→`.mmp`→render/stems receipt with an explicit
   voice→instrument map and a pinned abc2midi/converter version.
2. macOS arm64 headless render receipt, not just official build evidence.
3. LMMS instrument/timbre decision (SF2/soundfont licence-reviewed, or
   per-track synth presets) with listening verification.
4. Stem-vs-mix semantics and level/clipping documentation.
5. Version-locked `.mmp` generator validated by load-and-render, with the
   alpha one-way-upgrade caveat.
6. GPLv2 separate-process licence review and pinned source-archive packaging.

I do not sign on to the current "complete product-aim chain" wording until
items 1–2 are receipted. Subject to those edits, the proposal is a fair,
evidence-backed selection recommendation for root review.

## 6. Receipts used in this review

- LMMS determinism: `out.wav`/`det1.wav`/`det2.wav` all sha256
  `d63f77e9e5511a51a2b5d4ebc43f7f345b48bad49c053644960fb23aba90fe08`.
- LMMS stem-vs-mix: mix peak 32767, stem-sum peak 32768, difference RMS 7775
  (my `audioop` check on the receipts under `.probe-tmp/lmms/`).
- Ardour stems: committed `fa78626`, `ardour-two-track-probe.md` §3.1;
  working-tree four-track extension adds N=4 with stems within 2 LSB.
- ABC matrix: `docs/daw-integration/abc-semantics.md` and
  `docs/evidence/abc-semantics-20261025.json`.
- Tracktion: `docs/daw-integration/tracktion-engine-note.md`.
