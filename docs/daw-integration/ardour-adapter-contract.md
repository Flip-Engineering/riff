# Ardour adapter contract — score-driven per-instrument composition, edit and render

**Draft v0.1 for the implementation plan.** Author: riff-daw-deepseek-20261025.
Base: main @ `4e0f9eefaf9ee86f9901b75263574940af49034d` (PR80 merged).
This is a contract draft folded from the probe receipts; it is not authorization
to implement beyond the plan and not a redistribution decision.

**Evidence provenance, load-bearing:**
- All runtime receipts are from the official **9.8.241 DEMO NIGHTLY** Linux
  x86_64 build (`Ardour_x86_64-9.8.241-demo`, package sha1
  `0007d1664fe92c0d982c8c5e5bfea1b07dc1a98d`).
- Source citations are tag `9.8`: annotated tag object
  `059c95883d8158b03f632056ba36e5c7de91ef28`, which peels to commit
  `22ed8656c2533e325322ff11831448e5123e0d4b` (check out the commit; see
  `ardour-build-preflight.md` §1).
- Demo-nightly behavior must not be quoted as release or source-build behavior.
  The production runtime gate is §5.

## 0. Boundary and invariants

The contract covers: explicit symbolic arrangement input -> generated Ardour
session -> instrumented project -> rendered mix and per-track solo-render
stems -> same-topology score edit -> re-render. It does not cover ABC inference,
notation preservation, GUI operation, headless MIDI import, arbitrary SF2/bank
selection, or bundle redistribution.

Invariants:

- **I1 — manifest, not inference.** Session tempo/meter/bar structure comes
  only from the explicit timing manifest. The adapter never infers timing from
  ABC text. Inline `[Q:...]`/`[M:...]` directives are not the source of truth.
- **I2 — performance events, not notation.** The project stores SMF events.
  ABC notation, key, articulations, dynamics markings and chord symbols are not
  preserved as notation; quoted chord symbols are stripped from the performance
  copy. Do not claim lossless ABC conversion.
- **I3 — voice map, not `%%MIDI` labels.** The voice->track/instrument map is
  authoritative for grouping and programs. GM program labels/numbers are not
  timbre claims; only the controlled program-swap receipt is timbre evidence.
- **I4 — one instrument instance per track.** Distinct voices that share a map
  instrument collapse to one MIDI track and one instrument instance with
  per-channel program changes.
- **I5 — fail closed.** The Lua tools can exit 0 after a failed `load_session`
  (they log and quit). A receipt is valid only if the expected snapshot/files
  exist non-zero **and** the logs contain the expected success lines; exit
  status alone is insufficient.
- **I6 — deterministic names and hashes.** Snapshot names, exports and receipts
  are deterministic per job; all artifacts are sha256-recorded.

## 1. Session construction input contract

### 1.1 Canonical input object

`riff.ardour.session_input.v1` (JSON). Production inputs are immutable job
artifacts; the adapter validates before side effects.

```json
{
  "schema": "riff.ardour.session_input.v1",
  "session": {
    "name": "arrangement-v1",
    "sample_rate": 48000,
    "quarter_note_ticks": 480
  },
  "abc": {
    "mode": "voice_parts",
    "header": "X:1\nM:4/4\nL:1/8\nQ:1/4=100\nK:G",
    "voices": [
      {"voice_id": "Flute", "abc": "z8 | ...", "sha256": "..."},
      {"voice_id": "Bass",  "abc": "!p!\"G\"G,2 B,2 D2 G2 | ...", "sha256": "..."}
    ]
  },
  "voice_map": {
    "voices": {
      "Flute":  {"track": "Flute", "instrument": "flute",         "program_raw": 73},
      "Bass":   {"track": "Bass",  "instrument": "acoustic_bass", "program_raw": 33},
      "PianoRH":{"track": "Piano", "instrument": "acoustic_grand","program_raw": 0},
      "PianoLH":{"track": "Piano", "instrument": "acoustic_grand","program_raw": 0}
    }
  },
  "timing": {
    "end_quarters": 31,
    "tempos": [
      {"bbt": "1|1|0", "quarters": 0,  "bpm": 100},
      {"bbt": "5|1|0", "quarters": 16, "bpm": 72},
      {"bbt": "8|1|0", "quarters": 27, "bpm": 100}
    ],
    "meters": [
      {"bbt": "1|1|0", "quarters": 0,  "divisions_per_bar": 4},
      {"bbt": "7|1|0", "quarters": 24, "divisions_per_bar": 3},
      {"bbt": "8|1|0", "quarters": 27, "divisions_per_bar": 4}
    ]
  },
  "template": {
    "id": "ardour-9.8-clean-7004",
    "path": "templates/ardour-9.8-clean-7004.ardour",
    "sha256": "<recorded>",
    "session_format": 7004,
    "built_by": {"ardour_version": "9.8", "commit": "22ed8656c2533e325322ff11831448e5123e0d4b",
                 "tag_object": "059c95883d8158b03f632056ba36e5c7de91ef28", "build_kind": "full-source"}
  }
}
```

`abc.mode = "multi_voice_file"` may also be accepted with one `path`/`sha256`;
the adapter must deterministically split at `V:` declarations exactly as the
validated probe does (`parse_abc` in `probes/ardour/build_session.py`). The
fixture (`probes/fixtures/arrangement-v1.abc`) is this convenience form; the
canonical production form is `voice_parts`.

### 1.2 Per-voice ABC rules

- Each voice has exactly one body; the shared header carries `X:`, `M:`, `L:`,
  `Q:`, `K:`.
- The performance copy strips quoted chord symbols (`"G"`, `"C"`, `"Em"`, ...);
  these are annotations, not performed events. The probe does this with
  `strip_chords`.
- Inline `%%MIDI program` and `%%MIDI` directives are **not** the program
  channel; `voice_map.program_raw` wins. Fixture inline programs must agree with
  the map; disagreement is a contract violation once the input validator exists.
  The current probe ignores inline programs and injects the map programs at
  tick 0.
- The adapter does not retain ABC text in the project. Event preservation is
  verified against the pinned abc2midi output (§3).

### 1.3 Voice -> track/instrument map rules

- `instrument` is the grouping key: all voices with the same `instrument` are
  one track, one instrument instance, one playlist/source.
- `track` is the track name. Production inputs SHOULD set it explicitly. When
  absent, derive exactly as the probe: longest common prefix of the grouped
  voice IDs if >= 3 characters, else the first voice ID. The fixture omits
  `track`; `PianoRH` + `PianoLH` derive `Piano`.
- `program_raw` is the raw MIDI program byte 0–127 (GM names are 1-indexed;
  raw 73 = GM #74 Flute). Banks are out of scope for v1.
- Every voice in the ABC must appear exactly once in the map, and vice versa.
- A grouped track must yield one MIDI channel per voice, in abc2midi channel
  order; per-channel program changes are emitted at tick 0. Voices that produce
  no performance events are rejected unless the job explicitly opts into empty
  tracks (not supported by v1 evidence).

### 1.4 Timing manifest rules

- Unit is a quarter note; `quarter_note_ticks` is the SMF reference (480 in the
  fixture; abc2midi 5.07 output).
- `tempos`/`meters` are sorted strictly by `quarters`, first at 0; values are
  integers; `bpm` and `divisions_per_bar` numeric; `bbt` is a receipt label
  only and is cross-checked by `verify_timeline.py`.
- `end_quarters` bounds the arrangement; generated events may not exceed
  `end_quarters * quarter_note_ticks`. Fixture values: bar 5 tick 7680
  (quarter 16), bar 7 tick 11520 (quarter 24), bar 8 tick 12960 (quarter 27),
  end tick 14880 (quarter 31), end sample 1016000 at 48 kHz = 21.166667 s.
- Session extent is expressed musically, not in samples: the patcher sets the
  session/loop/region extent to `b59520` (31 quarters x 1920 units/quarter in
  the saved 7004 session) and Punch to `b0`–`b1920`.
- Bar-5/bar-8 tick positions and the 21.166667 s duration are acceptance
  criteria, not inferred values.

### 1.5 Template provenance decision

Options:

| option | provenance | pros | cons | verdict |
| --- | --- | --- | --- | --- |
| A. `1region` fixture | Ardour source-tree test asset `libs/ardour/test/profiling/sessions/1region/1region.ardour`, GPL, session **3001**, one MIDI track | exactly the route behind every receipt; no XML authoring; known 3001→7004 migration | upstream test fixture, not a controlled product asset; legacy tempo warnings; migration order changes panner state and therefore the mix hash (instrument-before-upgrade gave a different valid hash); fixture IDs/track shape | **regression baseline only** |
| B. pinned clean template (recommended) | created once by the pinned production build (empty 48 kHz session + minimal MIDI track/region via Lua or an upgraded clean session), frozen as a versioned asset with sha256 and a regeneration recipe, native **7004** | owned provenance; no upstream test content; native format, no 3001 migration; stable IDs/state; simpler license inventory; template hash check at admission | **not yet receipted**; creation recipe and exact XML need validation; `build_session.py` must be ported off its 3001 assumptions; regenerate/verify on Ardour upgrades | **production target; gate on §5.4 battery** |
| C. generated session XML per run | adapter emits full 7004 XML | no template asset; full ownership; no migration | hand-authoring 7004 is brittle; unimplemented/unverified; high maintenance and schema drift; panner/IO/processor state easy to get wrong | **defer; only if B fails** |

**Recommendation:** adopt **B (pinned clean 7004 template)** for production and
keep **A** as the regression baseline until B passes the same battery. Do not
ship A. The template is addressed by id + sha256 + `session_format`; the runner
re-hashes before build and fails on mismatch rather than silently regenerating.
Tradeoff summary: B costs a one-time template receipt and a `build_session.py`
port; it buys native-format stability and clean provenance, which A cannot
provide and C pays for in maintenance.

### 1.6 Admission checks (fail before side effects)

- Schema version exact; unknown top-level keys rejected.
- `session.name` safe (`[A-Za-z0-9._-]+`); `sample_rate` 48000; ticks 480.
- Voice set equality between ABC and map; one body each; grouped tracks
  non-empty; derived/explicit track names unique.
- `program_raw` integer 0–127; `instrument` non-empty.
- Timing: first event at 0; strictly increasing quarters; `end_quarters` > 0;
  last tempo/meter at or before end; generated events within end.
- Template sha256 matches; template `session_format` matches the patcher's
  expectation (7004 for the patched route).
- All job paths relative to the isolated job root; the `probes/ardour` scripts
  keep the `ARDOUR_PROBE_*` env interface and are not called with baked-in
  absolute checkout prefixes.

## 2. Instrument provisioning

### 2.1 Default bank on this homelab

- Instrument URI: `http://gareus.org/oss/lv2/gmsynth`.
- One `General MIDI Synth` instance per MIDI track, inserted at processor index
  0 (`instrument_session.lua`).
- Bundled artifact under the pinned Ardour tree:
  `lib/LV2/gmsynth.lv2/GeneralUser_LV2.sf2`, 32,322,864 bytes, sha256
  `c278464b823daf9c52106c0957f752817da0e52964817ff682fe3a8d2f8446ce`.
- Plugin artifacts: `gmsynth.so` sha256
  `0ff559f0d732b951c4edd08d549c0ab1dc700e7cd881a7adda191ddfe332d7bd`;
  `gmsynth.ttl` sha256
  `cea126a1a60d701aa268e51e49969e19f261ade1cbc44316d36525c312b65e4c`.
- The TTL declares GPL-family (`doap:license .../gpl`) without a version; treat
  as GPL-family pending legal review. The SF2 is GeneralUser GS 2.0.2,
  **License v2.0 (custom, not OSI)**; its embedded terms allow use in software
  projects but explicitly warn about sample provenance and commercial use.
  Redistribution clearance is **not** established (see §2.4).

### 2.2 Per-track GM program changes

- Program bytes are injected at tick 0 in each track's SMF source, from
  `voice_map.program_raw`; the fixture receipt is 73 (Flute), 33 (Bass), 0
  (Piano), with PianoRH and PianoLH on channels 0 and 1 of the **one** Piano
  track, both program 0.
- Validated controlled swap: changing only the Flute source program from 73 to
  0, keeping every note event identical, changed the solo render from peak
  -9.617769 / RMS -19.977136 dBFS (centroid 715.8 Hz) to peak -12.748644 /
  RMS -32.565055 dBFS (centroid 652.6 Hz); file sha256
  `75fcd42f7122fe6a1842bb092c74849576be33e60137ca49c926143b7d546a97`.
  This proves gmsynth responds to the per-track program change. It is **not**
  evidence that GM program 73 is a real or high-quality flute.

### 2.3 ACE FluidSynth / extra-SF2 extension

Adding `a-fluidsynth.lv2` or another SF2 requires, before any code path:

- an explicit map extension, per track:
  `{"engine": "gmsynth" | "a-fluidsynth", "soundfont_path": "...",
    "soundfont_sha256": "...", "bank": 0, "program_raw": 0}`;
- engine/bank/program must be explicit in the job input; no inference from ABC
  labels or GM names;
- a per-engine, per-bank controlled receipt: same notes, two programs, different
  audio (the 73/0 method above);
- evidence for bank/preset selection (CC0/CC32 or plugin state) — **currently
  unproven**; `a-fluidsynth.lv2` is GPL-family per its TTL but is not on the
  probe path;
- a license/provenance check for every added SF2: license text, author,
  sample-provenance statement, redistribution permission. Do not treat
  "separate process" as clearance.
- Default stays gmsynth + GeneralUser; extensions are opt-in and gated.

### 2.4 License gate

- gmsynth is GPL-family; GeneralUser GS License v2.0 is custom and its
  sample-provenance caveat is unresolved. If Riff ships/bundles an Ardour-based
  renderer, both are hard legal gates.
- Exclude `Harrison.lv2` (proprietary/license-gated; mixed directory, one
  `XT_GV` TTL is not enough) and do not redistribute the demo bundle.
- Safe short-term shape: depend on a separately installed Ardour + gmsynth +
  GeneralUser environment; do not re-package the demo bundle into Riff
  artifacts.

## 3. Operation chain

### 3.1 Stages and accept criteria

| # | stage | tool | input | output | accept |
| --- | --- | --- | --- | --- | --- |
| 1 | build | `probes/ardour/build_session.py` | ABC + voice map + timing + template + pinned abc2midi | `<job>/v1/arrangement-v1.ardour` + `interchange/arrangement-v1/midifiles/{Flute,Bass,Piano}.mid` | files exist; per-track channels == programs; SMF written |
| 2 | upgrade-load | `ardour9-lua probes/ardour/reopen_session.lua` | snapshot `arrangement-v1` | `upgraded.ardour` (7004) | log `err=nil`, `save ok=true`; snapshot non-zero |
| 3 | instrument | `ardour9-lua probes/ardour/instrument_session.lua` | `upgraded` | `v1-instrumented.ardour` | one gmsynth per track; `save ok=true` |
| 4 | tempo/range patch | `probes/ardour/patch_tempo_range.py` | `v1-instrumented.ardour` + timing | `v1-tempoed.ardour` | exactly one TempoMap replaced; session/loop/region extent patched; Punch valid |
| 5 | reopen/save | `ardour9-lua probes/ardour/reopen_session.lua` | `v1-tempoed` | `v1-final.ardour` | log `track 1 Flute`/`track 2 Bass`/`track 3 Piano`, `tracks=3`, `save ok=true` |
| 6 | mix export | `ardour9-export` | session dir + `v1-final` | `export/v1-final.wav` | 48 kHz stereo pcm_s16le; duration 21.166667 s on fixture |
| 7 | solo-stem snapshots | `ardour9-lua probes/ardour/save_stems.lua` | `v1-final` + prefix `v1-stem-` | snapshots `v1-stem-1..3` | one solo per track; `save ok=true` |
| 8 | per-snapshot export | `ardour9-export` | each `v1-stem-i` | `export/v1-stem-i.wav` | same format/duration; non-silent |

Template note: on the current validated route A the template is the 3001
`1region` fixture, so stage 2 is the required 3001→7004 migration. On route B
(native 7004 clean template) stage 2 is a load/save validation, not a
migration; the remaining stages are unchanged. `$TEMPLATE` below is the pinned
template selected by §1.5.

### 3.2 Environment contract

- Owned, per-job `HOME` and XDG dirs; nothing writes to the service user's real
  home or `/tmp` shared space:

```sh
mkdir -p "$JOB"/{home/.config,home/.cache,home/.local/share,home/.local/state,run,tmp,logs,export}
chmod 700 "$JOB/run"
HOME="$JOB/home" \
XDG_CONFIG_HOME="$JOB/home/.config" \
XDG_DATA_HOME="$JOB/home/.local/share" \
XDG_STATE_HOME="$JOB/home/.local/state" \
XDG_CACHE_HOME="$JOB/home/.cache" \
XDG_RUNTIME_DIR="$JOB/run" \
TMPDIR="$JOB/tmp" \
JACK_NO_START_SERVER=1 \
nice -n 19 ionice -c3 "$BIN/ardour9-lua" ...
```

- `JACK_NO_START_SERVER=1` is required (headless render must not autostart JACK).
- One heavy process at a time, sequential; no parallel stem renders.
- Resolve `ardour9-lua`, `ardour9-export`, `ardour9-new_session` from the pinned
  runtime path and record their `--version`; do not rely on PATH drift.
- Lua scripts keep the env interface:
  `ARDOUR_PROBE_SESSION_DIR`, `ARDOUR_PROBE_SNAPSHOT_IN`,
  `ARDOUR_PROBE_SNAPSHOT_OUT`, `ARDOUR_PROBE_LOG`, and for stems
  `ARDOUR_PROBE_STEM_PREFIX`. Asserts run before side effects; no absolute
  checkout prefixes.
- Fail-closed: check snapshot existence/size and log lines in addition to exit
  status (see I5).

### 3.3 Exact command sequence (fixture; paths are job-relative)

Job vars: `JOB` = isolated job root; `BIN` = pinned runtime bin dir containing
`ardour9-lua`/`ardour9-export`; `TEMPLATE` = pinned template from §1.5;
`ABC2MIDI` = pinned abc2midi 5.07 binary.

```sh
# 1. build the 3001-shaped session + per-track MIDI sources
python3 probes/ardour/build_session.py \
  --template "$TEMPLATE" \
  --abc probes/fixtures/arrangement-v1.abc \
  --voice-map probes/fixtures/voice-map.json \
  --timing probes/fixtures/arrangement-timing.json \
  --out "$JOB/v1" \
  --session-name arrangement-v1 \
  --abc2midi "$ABC2MIDI"

# 2. upgrade 3001 -> 7004 BEFORE instrumenting (order is load-bearing)
ARDOUR_PROBE_SESSION_DIR="$JOB/v1" ARDOUR_PROBE_SNAPSHOT_IN=arrangement-v1 \
ARDOUR_PROBE_SNAPSHOT_OUT=upgraded ARDOUR_PROBE_LOG="$JOB/logs/upgrade.out" \
  "$BIN/ardour9-lua" probes/ardour/reopen_session.lua

# 3. add one gmsynth per MIDI track
ARDOUR_PROBE_SESSION_DIR="$JOB/v1" ARDOUR_PROBE_SNAPSHOT_IN=upgraded \
ARDOUR_PROBE_SNAPSHOT_OUT=v1-instrumented ARDOUR_PROBE_LOG="$JOB/logs/instrument.out" \
  "$BIN/ardour9-lua" probes/ardour/instrument_session.lua

# 4. install the new-format tempo/meter map + musical extent
python3 probes/ardour/patch_tempo_range.py \
  --session "$JOB/v1/v1-instrumented.ardour" \
  --timing probes/fixtures/arrangement-timing.json \
  --out "$JOB/v1/v1-tempoed.ardour"

# 5. reopen/save the final v1 snapshot
ARDOUR_PROBE_SESSION_DIR="$JOB/v1" ARDOUR_PROBE_SNAPSHOT_IN=v1-tempoed \
ARDOUR_PROBE_SNAPSHOT_OUT=v1-final ARDOUR_PROBE_LOG="$JOB/logs/reopen.out" \
  "$BIN/ardour9-lua" probes/ardour/reopen_session.lua

# 6. mix export
"$BIN/ardour9-export" -o "$JOB/export/v1-final.wav" "$JOB/v1" v1-final

# 7. one solo snapshot per track
ARDOUR_PROBE_SESSION_DIR="$JOB/v1" ARDOUR_PROBE_SNAPSHOT_IN=v1-final \
ARDOUR_PROBE_STEM_PREFIX=v1-stem- ARDOUR_PROBE_LOG="$JOB/logs/stems.out" \
  "$BIN/ardour9-lua" probes/ardour/save_stems.lua

# 8. export each snapshot as a per-track stem
for i in 1 2 3; do
  "$BIN/ardour9-export" -o "$JOB/export/v1-stem-$i.wav" "$JOB/v1" "v1-stem-$i"
done
```

Verification (probe tools):

```sh
python3 probes/ardour/verify_timeline.py \
  --abc probes/fixtures/arrangement-v1.abc \
  --voice-map probes/fixtures/voice-map.json \
  --timing probes/fixtures/arrangement-timing.json \
  --midi-dir "$JOB/v1/interchange/arrangement-v1/midifiles" \
  --session-xml "$JOB/v1/v1-final.ardour" \
  --abc2midi "$ABC2MIDI" \
  --session-midi-dir "$JOB/v1/interchange/arrangement-v1/midifiles"

python3 probes/ardour/verify_stems.py \
  --mix "$JOB/export/v1-final.wav" \
  --stem "$JOB/export/v1-stem-1.wav" \
  --stem "$JOB/export/v1-stem-2.wav" \
  --stem "$JOB/export/v1-stem-3.wav" \
  --out "$JOB/v1/stems-verify.json"
```

### 3.4 Dummy-backend caveat

The headless probe runs use the None/Dummy audio path; export logs show
`DummyMidiBuffer: it's too late for this event`. The renders are non-silent and
the timeline is plausible, but this is **not** sample-accurate MIDI scheduling
evidence. Contract rules:

- Dummy-backend renders are valid for project/event/mix-existence receipts.
- Any sample-accuracy claim requires a real-backend or documented offline-backend
  receipt; audio onset checks currently use an 80 ms tolerance and are detector
  limited (polyphony).
- Do not launder the warning into silence: retain the log with the receipt.

## 4. Edit semantics (v1 -> v2)

### 4.1 Same-topology edit — the validated route

A score edit is applied to a **copy of the same generated project**, not a new
session, when topology is unchanged (same track count, same voice grouping,
same source filenames/IDs, same timing manifest):

1. `cp -a "$JOB/v1" "$JOB/v2-edit"` (copy includes `v1-final.ardour`).
2. Build v2 MIDI into a scratch dir with the same adapter and pinned abc2midi
   (same template/voice-map/timing flags as stage 1):
   `python3 probes/ardour/build_session.py --template "$TEMPLATE" --abc probes/fixtures/arrangement-v2.abc --voice-map probes/fixtures/voice-map.json --timing probes/fixtures/arrangement-timing.json --out "$JOB/v2-scratch" --session-name arrangement-v2 --abc2midi "$ABC2MIDI"`
3. Replace the performance files in the copied session, keeping the same
   filenames: copy `v2-scratch/interchange/arrangement-v2/midifiles/*.mid` over
   `v2-edit/interchange/arrangement-v1/midifiles/*.mid`.
4. `ardour9-lua reopen_session.lua` with `SNAPSHOT_IN=v1-final`,
   `SNAPSHOT_OUT=v2-final` in `$JOB/v2-edit`; export
   `export/v2-final.wav` from snapshot `v2-final`.

Allowed changes: pitches, durations, rests, chords inside the existing voices.
The edit is a **source replacement**; session XML, plugin state, tempo map and
extent remain. `%%MIDI`/instrument changes are not an edit here.

Receipt requirements (fixture values):

- source hashes change for all three tracks (v1 Flute
  `0f8a86f2…`, Bass `1c62cd1a…`, Piano `f2c534bd…`; v2 Flute `c8b3cc0f…`,
  Bass `e6cde510…`, Piano `d3d57015…`);
- session hash changes (`v1 126addd3…`, `v2 dfb1d25d…`) and the v2 mix is a
  different file (`v1 4175c184…`, `v2 49dba1a4…`);
- duration unchanged at 21.166667 s when timing is unchanged;
- `verify_timeline.py` passes for v2 (12/30/37 notes, programs 73/33/0, exact
  after the +1-tick normalization);
- hash comparisons are only meaningful within one pinned runtime/build.

### 4.2 Rebuild-triggering edits

Treat as a **new project revision**, not an in-place edit, when any of these
change: voice set/grouping/track names, instrument or program map, timing
manifest/tempo/meter/end, source count. Rebuild from the template and re-run the
full chain (§3), including the tempo/range patcher. Do not mutate the old
project's XML.

### 4.3 What "edit" does not mean

No notation editing, no MIDI import, no GUI edit, no automatic re-orchestration.
The receipt is a same-topology performance replacement with event-level
verification.

## 5. Runtime qualification

### 5.1 Current status

All receipts above use the **demo nightly 9.8.241** binary. The documented demo
restriction is a GUI silence period; the probe run was short and the CLI tools
showed no timer, but the demo build is **not** a production runtime and its
behavior must not be quoted as release behavior.

### 5.2 Recommendation

**Primary recommendation: pinned source build** of Ardour 9.8 at commit
`22ed8656c2533e325322ff11831448e5123e0d4b` (annotated tag object
`059c95883d8158b03f632056ba36e5c7de91ef28`, tag `9.8`) as the production
runtime.

Why: it is non-demo by construction; it is reproducible from the reviewed GPL
source; it pins exact behavior; it lets provisioning choose the plugin set
(exclude `Harrison.lv2`), library paths and backend; and it gives a clean
license/provenance story. Cost: dependency/build receipt, queued build time, and
a mandatory re-run of the battery on the built binary.

**Reviewed full binary route:** acceptable as a **phase-0 staging runtime** only
if root supplies a reviewed, hash-pinned, full (non-demo) binary. It must pass
the same battery (§5.4) before any behavior is claimed, and its provenance/hash
must be recorded. Do not run both runtimes in production; pick one. The full
binary does not remove the source-build decision for GA.

### 5.3 What a queued build / provisioning would need (route to root)

1. **Authorization:** queue slot for an Ardour 9.8 source build; named build
   owner and runtime owner; target install prefix under the homelab.
2. **Pinned source:** `git clone https://git.ardour.org/ardour/ardour.git`
   (full history and tags; GitHub release tarballs are unsupported upstream),
   checkout commit `22ed8656c2533e325322ff11831448e5123e0d4b` (tag object
   `059c95883d8158b03f632056ba36e5c7de91ef28`); record `git describe` (must
   print `9.8`) and the source archive hash.
3. **Dependency/build receipt:** upstream configure/build recipe and dependency
   list at that commit are **not yet receipted here**; author them in the queued
   job (toolchain versions, dev packages, configure flags). Do not infer the
   recipe from the demo bundle.
4. **Artifacts:** `ardour9`, `ardour9-lua`, `ardour9-export`,
   `ardour9-new_session`; sha256 each; record `--version`, `ldd`/rpath, and the
   applicable `COPYING`/notices.
5. **Plugin/SF2 provisioning:** build or provision `gmsynth.lv2` and
   `GeneralUser_LV2.sf2` at the hashes in §2.1; scan the LV2 path; confirm
   `Harrison.lv2` is absent; the SF2 redistribution gate (§2.4) stays open.
6. **Provisioning receipt:** owned HOME/XDG smoke run; first-run config;
   `JACK_NO_START_SERVER=1`; a fixture render that proves the binary is not
   demo-limited.
7. **Acceptance battery (§5.4)** on the production runtime, with hashes.
8. **Decision to root:** authorize the queued build + dependency receipt, or
   provide the reviewed full non-demo binary (sha256) for staging. Until one
   lands, production remains blocked, but the adapter contract and probe
   evidence are ready.

### 5.4 Acceptance battery on the production runtime

- A. Build/mapping: 4 voices -> 3 tracks; PianoRH+PianoLH one track, two
  channels, one program.
- B. Timing: 100→72→100 and 4/4→3/4→4/4 at bars 5/7/8; ticks 7680/11520/12960;
  end 14880; 21.166667 s at 48 kHz.
- C. Save/reopen: `save ok=true`; three tracks; tempo map/range exact.
- D. Mix + solo stems: stems sum to mix within 1 LSB (fixture: diff peak 1 LSB,
  RMS 0.554 LSB; sum peak 17877 = mix peak 17877).
- E. Edit: same-topology v1→v2 source replacement, reopened, re-rendered,
  event-checked.
- F. Program control: 73↔0 swap changes audio with identical notes.
- G. Fail-closed: corrupted template/snapshot/input fails before side effects
  and does not produce a receipt.

## 6. Non-goals and uncertainty (explicit)

- **No headless `Session::import_files` claim.** The Lua API exposes track
  creation/model edit/`SimpleExport`, but not `import_files`; the shipped import
  example needs the GUI `Editor`. Our route is generated SMF + template source
  replacement, not import.
- **No sample-accuracy claim under the Dummy backend.** `DummyMidiBuffer`
  warnings and the 80 ms onset tolerance remain; a real-backend receipt is
  queued.
- **No timbre-quality claim from program numbers or GM labels.** The 73/0 swap
  proves program response, not that GM 73 is a good flute.
- No general ABC→project adapter: timing is an explicit manifest; arbitrary ABC
  meter/tempo/bar inference is out of scope.
- No ABC notation/key/articulation/dynamics preservation; performance events
  only; chord symbols are stripped.
- No GUI stem dialog, raw pre-fader, or batch-stem-without-solo-state claim;
  only solo-render snapshots are proven.
- No arbitrary banks/SF2s; bank selection unproven; extra engines unproven.
- No source-build/macOS/Arm64/Windows behavior claim; runtime receipts are
  Linux x86_64 demo-nightly.
- No redistributable bundle/license clearance; gmsynth GPL-family and
  GeneralUser custom license/sample provenance remain gates; exclude Harrison.
- No claim that the clean-template route (§1.5 B) works until it passes §5.4;
  it is a recommendation, not a receipt.
- No claim that the 3001→7004 migration order is a stable public API; it is
  observed behavior of the pinned build.
- No claim that session format stays 7004 across future Ardour versions.

## 7. Traceability

| contract area | evidence |
| --- | --- |
| generation, voice grouping, programs, timing | `probes/ardour/build_session.py`; `probes/fixtures/{arrangement-v1.abc,arrangement-v2.abc,voice-map.json,arrangement-timing.json}` |
| session chain, tempo/range patch, reopen | `probes/ardour/{instrument_session.lua,reopen_session.lua,patch_tempo_range.py}`; `docs/daw-integration/ardour-musical-workflow.md` |
| event verification | `probes/ardour/verify_timeline.py`; `docs/evidence/ardour-musical-workflow-20261025.json` |
| stems | `probes/ardour/{save_stems.lua,verify_stems.py}`; stems §3.1/§4.3 of the workflow doc |
| template/source-swap feasibility | `docs/daw-integration/ardour-template-swap-probe.md` |
| demo runtime and import gate | `docs/daw-integration/ardour-headless-probe.md` |
| license inventory | `docs/daw-integration/ardour-bundle-licensing.md`; `docs/evidence/ardour-bundle-licensing-20261025.json`; `probes/ardour/licensing_inventory.py` |
| selection/provisional plan | `docs/daw-integration/proposal.md` |

Receipt pins: package sha1 `0007d166…`; source commit `059c958…`; abc2midi
5.07 (commit `6582eb8`) per the workflow doc; session formats 3001 -> 7004;
fixture duration 21.166667 s at 48 kHz.