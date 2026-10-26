# Ardour 9.8 headless create/control/export probe (#78)

Author: riff-daw-deepseek-20261025. Status: reversible feasibility evidence,
2026-10-25. This note supersedes the "no documented headless offline export"
open question in the candidate landscape; it does not select Ardour and does
not commit an integration architecture.

## 1. Why this probe matters

GLM's candidate review identified one decisive Ardour question: can an Ardour
session be rendered to an audio file without the Editor GUI? The answer is
**yes for an existing session**: Ardour 9.8 ships a command-line exporter,
`ardour9-export`, and a headless Lua interpreter, `ardour9-lua`
(`luasession`). I verified a real non-silent render from a session that had a
MIDI region and a general-MIDI instrument.

There is still a separate gate: **constructing/importing arbitrary MIDI into a
session headlessly is not exposed by the same Lua API**. The proven part and
the remaining gate are kept distinct below.

## 2. Source evidence

All paths are at Ardour release tag `9.8`, commit
`059c95883d8158b03f632056ba36e5c7de91ef28` (verified with
`git ls-remote --tags https://github.com/Ardour/ardour`), and were also
observed in the official 9.8.241 Linux demo build.

- **Command-line exporter**: `session_utils/export.cc` (in the source tree)
  is the `ardour9-export` utility. Its help says: "This tool exports the
  session-range of a given ardour-session to a wave file, using the
  master-bus outputs." It takes session directory + session/snapshot name,
  optional `--output`, `--bitdepth`, `--samplerate`, `--normalize`. The
  implementation uses `session->current_start_sample()` /
  `current_end_sample()` and the master bus's audio ports; there is no GUI
  call in the export path.
  https://github.com/Ardour/ardour/blob/9.8/session_utils/export.cc
- **Headless Lua**: `luasession/luasession.cc` is the standalone
  `ardour9-lua` tool. Its official manual section calls it the "Commandline
  Tool" that "Replaces the complete Editor GUI, direct access to libardour
  (no GUI) from the commandline", and warns that "the vast majority of complex
  functionality is provided by the Editor UI".
  https://github.com/Ardour/ardour/blob/9.8/luasession/luasession.cc
  https://manual.ardour.org/lua-scripting/
- **In-process export API**: `Session:simple_export()` is bound in
  `libs/ardour/luabindings.cc`; `SimpleExport` exposes `run_export`,
  `set_name`, `set_folder`, `set_range`, `set_preset`, `check_outputs`.
  The implementation is `libs/ardour/simple_export.cc`. This is an alternative
  to the CLI exporter and uses the current session/master bus.
  https://github.com/Ardour/ardour/blob/9.8/libs/ardour/luabindings.cc
  https://github.com/Ardour/ardour/blob/9.8/libs/ardour/simple_export.cc
- **Headless session creation**: `session_utils/new_session.cc` and
  `session_utils/new_empty_session.cc` are `ardour9-new_session` /
  `ardour9-new_empty_session`. Help: create an empty session (optionally from
  a *static* template; "Interactive Lua init-scripts or dynamic templates are
  not supported"). I created an empty 48 kHz session with the CLI.
  https://github.com/Ardour/ardour/blob/9.8/session_utils/new_session.cc
- **Bundled GM instrument**: the official 9.8.241 Linux package contains
  `lib/LV2/gmsynth.lv2` ("General MIDI Synth", GPL) with a 31 MB
  `GeneralUser_LV2.sf2`, plus `a-fluidsynth.lv2` and the other bundled LV2
  effects. This is a default sound-generation path that does not require a
  separately purchased instrument.
- **License**: GPLv2-or-later with the plugin clarification, as recorded in
  `COPYING`; separate-process distribution matches Riff's existing ffmpeg
  precedent.
  https://github.com/Ardour/ardour/blob/9.8/COPYING

## 3. Probe

### 3.1 Package and environment

- Official **nightly demo** Linux x86_64 package:
  `https://nightly.ardour.org/d/A_Linux_x86_64_FREE/Ardour-9.8.241-demo-x86_64.run`
  (76.36 MiB), sha1 `0007d1664fe92c0d982c8c5e5bfea1b07dc1a98d`.
  The demo build's documented restriction is a 10-minute GUI silence period;
  the CLI tools are separate and showed no timer in this short probe. This is
  a **nightly demo**, not the official 9.8 release binary; the CLI tools
  themselves are present in the 9.8 source tree above.
- Extracted with the Makeself `--noexec` path; no install to `/opt`, no root.
- `ardour9-export --version` -> `ardour-utils version 9.8.241`;
  `ardour9-lua --version` -> `ardour-lua version 9.8.241`.
- Host: Linux x86_64, `nice -n 19` + `ionice -c3`, one heavy process at a
  time. No macOS/Apple Silicon run was performed.

### 3.2 Empty-session create/export

`ardour9-new_session -s 48000 ... Test3` created `Test3.ardour` and the
session directory tree. The session has no tracks and no session range, so I
added a 2-second `IsSessionRange` location to the XML and ran:

```sh
ardour9-export -o .../export/probe.wav .../session3 Test3
```

It wrote a valid 2.000 s, 48 kHz, 16-bit stereo WAV (384,044 bytes) and then
printed `FATAL: exception not rethrown` during teardown. The empty-session
teardown is therefore not clean; a session with content must be used for a
representative receipt.

### 3.3 Content render with the bundled GM synth

To avoid hand-authoring a whole session, I used the source-tree test session
`libs/ardour/test/profiling/sessions/1region/1region.ardour` (session version
3001: one MIDI track with a MIDI region and one MIDI source). I copied it and
its `MIDI 1-1.mid` source into a local session directory, then ran a Lua
script under `ardour9-lua`:

```lua
local s = load_session(SESSION_DIR, "1region")
for t in s:get_tracks():iter() do
  local p = ARDOUR.LuaAPI.new_plugin(s,
      "http://gareus.org/oss/lv2/gmsynth", ARDOUR.PluginType.LV2, "")
  t:add_processor_by_index(p, 0, nil, true)
end
s:save_state("gmsynthprobe")
close_session()
quit()
```

- `load_session` returned a session; `get_tracks` returned the `MIDI 1` track.
- The gmsynth plugin loaded; `add_processor_by_index` succeeded.
- `save_state("gmsynthprobe")` returned 0 and wrote a 28,015-byte snapshot.
- The script then exited 0; no abort on this content path.

Then the CLI exporter ran cleanly:

```sh
ardour9-export -o .../export/cli-gmsynth.wav .../region-session gmsynthprobe
```

Result (ffprobe/astats):

| Property | Value |
| --- | --- |
| format | WAV, `pcm_s16le`, 48 kHz, stereo |
| duration | 11.655729 s |
| bytes | 2,237,944 |
| peak | -22.249757 dBFS |
| RMS | -40.366711 dBFS |
| sha256 | `9775dab665bda0c70e1c637ae311efd7f391ccaee79c02d0d475d98b2affd3e4` |

The non-silent output proves that a MIDI region + gmsynth instrument + master
bus can be rendered with no Editor. The signal is quiet (one long test note,
no mix gain), which is fine for a routing/render receipt; it is not a musical
quality claim.

The in-process `SimpleExport` path also ran successfully from the same Lua
session: `set_folder`, `set_range(529680, 1089150)`, `check_outputs` -> true,
`run_export` -> true. It produced `gmsynth-probe.wav` (2,056,096 bytes,
44.1 kHz because the default `SimpleExport` preset is CD/44.1 kHz, 11.655624 s,
peak 0.000265 dBFS, RMS -18.149351 dBFS, sha256
`aa63e4c4aa75ae480463cbcc37b19ae405f8fb72fad18cf245ffab2870cad69a`).
The CLI exporter used the session rate (48 kHz); an integration must choose
the rate/format explicitly rather than inherit a preset.

### 3.4 Four-note follow-up (peer-requested)

The first content receipt used one long source-tree test note. The conductor
reviewed the earlier empty-session WAV (2.00 s, pure digital silence) and asked
for a bounded **2–4 note** render so the receipt proves actual instrument
output, not just a valid WAV header. I reran the same mechanism with a fresh
four-note MIDI source:

- ABC source: `C D E F |`, `M:4/4`, `L:1/4`, `Q:1/4=120`, converted with
  abc2midi to a 4-note SMF (`fa719487…`); parsed note events are
  C4/D4/E4/F4 at ticks 1/481/961/1441.
- Loaded the `1region` session template with the replacement MIDI source,
  added gmsynth through `ardour9-lua`, saved snapshot `fourprobe`, and ran
  `ardour9-export`.
- Output: 11.655729 s, 48 kHz, stereo, 16-bit WAV, sha256
  `2b24541ae07e5a5a4fbdf089abd3ad85d6443e4d5e9953ac40f124c8e32b9165`;
  peak **-13.13 dBFS**, RMS **-32.25 dBFS**, 559,475 samples.
  `silencedetect` sees signal until **2.315 s** and silence thereafter,
  consistent with four quarter notes at 120 BPM plus release.
- Export log also printed three `DummyMidiBuffer: it's too late for this
  event` warnings. The audio is non-silent and the note timing is plausible,
  but this is a Dummy-backend timing caveat; a source build/real backend
  must be used before claiming sample-accurate MIDI scheduling.

This is the requested nonzero-signal receipt. It still does not prove
arbitrary MIDI *import* by an agent: the MIDI content was placed by replacing
the session's source file, not imported through a headless Riff operation.

### 3.5 What was not proven

- **Headless MIDI import/authoring is still open.** The Lua bindings expose
  `Session:new_midi_track`, `Session:new_audio_track`,
  `Route:add_processor_by_index`, `MidiModel` diff commands and `SimpleExport`,
  but not `Session::import_files`. The shipped import example
  `share/scripts/s_import_files.lua` uses `Editor:do_import`, and `Editor` is
  the GUI. The candidate landscape's "MIDI import" path is therefore a GUI
  path. A static session template with an existing empty MIDI region, or a
  generated session file, would be needed for arbitrary per-instrument
  content. That template path was not validated here; it is the next
  feasibility gate, not a solved capability.
- `ardour9-export` is a master-bus mixdown of the session range; per-track
  stems were not tested. Riff's stem manifest requirement is separate.
- The gmsynth/SF2 assets need license review before redistribution, and GM
  program changes were not audited through gmsynth in this probe.
- The test session is an old source-tree fixture (legacy tempo warnings); it
  is evidence for the render mechanism, not a production template.
- No macOS/Arm64 run. The official nightly page lists Intel and ARM64 macOS
  builds; the candidate landscape already records that platform evidence.

## 4. Implication for selection

This probe removes the "Ardour cannot export headlessly" objection and adds a
concrete default instrument (gmsynth + SF2). It does **not** by itself make
Ardour the selection: the agent must be able to turn an ABC-derived
per-instrument score into a session. The remaining question is whether a
small, version-pinned session/template adapter (per-voice MIDI files +
instrument map + static session structure) can be validated and maintained
without brittle reverse engineering.

If that construction gate passes, Ardour is a strong complete-DAW choice:
GPLv2+, macOS arm64, mature editing/mixing/automation, bundled GM synth,
headless Lua control, and both CLI and Lua master export. If the gate fails,
the joint proposal should not claim Ardour is agent-authorable; the fallback
is a library-grade headless engine with a first-class MIDI/arrangement API
(see the candidate landscape for Tracktion Engine considerations), or a
narrow scoring/rendering service instead of a full DAW.

## 5. Receipts and commands

Local receipts (gitignored, on the shared host):
`.probe-tmp/deepseek/ardour-probe/` (session, Lua scripts, WAVs, logs) and
`.probe-tmp/deepseek/ardour-bin/` (extracted package).

Key commands:

```sh
# create empty session
ardour9-new_session -s 48000 SESSION_DIR Test3
# add gmsynth and save snapshot
ardour9-lua SAVE_SCRIPT.lua
# render session range to WAV
ardour9-export -o OUT.wav SESSION_DIR gmsynthprobe
# inspect
ffprobe -v error -show_entries format=duration,size:stream=codec_name,sample_rate,channels,bits_per_sample OUT.wav
ffmpeg -hide_banner -nostats -i OUT.wav -af astats=metadata=1:reset=0 -f null -
```

Source session and source MIDI:
- `https://github.com/Ardour/ardour/blob/9.8/libs/ardour/test/profiling/sessions/1region/1region.ardour`
- `https://github.com/Ardour/ardour/blob/9.8/libs/ardour/test/profiling/sessions/1region/interchange/1region/midifiles/MIDI%201-1.mid`

Related: `docs/daw-integration/daw-candidates.md` (candidate landscape),
`docs/daw-integration/abc-semantics.md` (conversion boundary), issue
https://github.com/Flip-Engineering/riff/issues/78.
