# DAW runner script set (daw.compose / daw.render)

**Author:** riff-daw-deepseek-20261025
**Slice:** B / dispatch `5da095ce-2a33-4fdd-af6a-126bd8dfbe5b`
**Status:** design draft for the implementation plan. No shared files edited; no live activation.
**Grounding:** the validated probe chain in `probes/ardour/` and `docs/daw-integration/ardour-musical-workflow.md` §3, generalized to the adapter contract. The historically named `ARDOUR_PROBE_*` env interface is **kept verbatim** for compatibility; “PROBE” is a historical prefix, not a claim that the runner is a probe.

---

## 1. Division of responsibility

- **Riff / `daw.compose` adapter (existing `build_session.py`, to be widened by the contract):** validates the canonical session input, materializes per-voice ABC/SMF, writes `tracks.txt`/`tracks.json` and `timing.json`, emits `daw.*` markers, and owns the job receipt.
- **Runner (this document):** a thin, deterministic shell/Python driver that invokes Ardour CLI/Lua stages, enforces fail-closed checks, and never decides musical content.
- **Ardour:** loads/saves sessions, holds the tempo map and tracks, renders the master bus, and produces solo-stem snapshots. It is never driven through the GTK GUI.

No stage parses ABC timing. The explicit timing manifest is authoritative (contract §1); abc2midi is only the event source, and `build_session.py` already normalizes its first-event tick `1` to `0`.

Job-kind profiles over the same stage table:

- `daw.compose`: stages `prepare → build → upgrade → instrument → tempo_range → reopen → mix → verify → receipt` (0,1,2,3,4,5,6,9,11). Deliverable is the session snapshot plus mix and timeline receipt; stems are optional.
- `daw.render`: stages `prepare → build → upgrade → instrument → tempo_range → reopen → mix → stems → stem_export → verify → receipt` (0–9,11). Deliverable is mix + per-track stem WAVs + reconstruction receipt.
- `daw.edit`/edit continuation: only when the score changes with **identical topology/timing/programs**; uses stage 10 then `reopen(v1-final→v2-final)` + mix/stems/verify. Anything else is a full rebuild from stage 1.

The runner never invokes `bin/ardour9` (the GUI wrapper). It uses `bin/ardour9-lua` for session/Lua stages, `bin/ardour9-export` for master/stem renders, and `bin/ardour9-new_session` only for the Q2 template smoke.

---

## 2. Environment interface

### 2.1 Preserved probe variables (do not rename in this phase)

| variable | required | meaning |
|---|---|---|
| `ARDOUR_PROBE_SESSION_DIR` | yes | absolute path of the Ardour session directory |
| `ARDOUR_PROBE_SNAPSHOT_IN` | most stages | snapshot name **without** `.ardour` (e.g. `v1-final`) |
| `ARDOUR_PROBE_SNAPSHOT_OUT` | save stages | snapshot name to write |
| `ARDOUR_PROBE_LOG` | yes | per-stage log path; parent must exist |
| `ARDOUR_PROBE_STEM_PREFIX` | stems | snapshot prefix, e.g. `stem-` → `stem-1`, `stem-2`, … |

### 2.2 Additive runner variables

| variable | default | meaning |
|---|---|---|
| `ARDOUR_PROBE_PLUGIN_URI` | `http://gareus.org/oss/lv2/gmsynth` | instrument LV2 URI; the only validated value today |
| `ARDOUR_PROBE_TRACKS_FILE` | unset | newline-delimited expected track names, in session order; when set, Lua stages assert exact count/order/name |
| `ARDOUR_PROBE_SEQUENCE_FILE` | unset | JSON manifest of the job (`riff.daw.ardour-run.v1`); read-only, used for receipt identity |
| `ARDOUR_PREFIX` | none | install root from the pinned build (§ preflight) |
| `ARDOUR_DLL_PATH` | `$ARDOUR_PREFIX/lib/ardour9` | must be set; Ardour exits if absent (`libs/ardour/filesystem_paths.cc:193-207`) |
| `ARDOUR_DATA_PATH` | `$ARDOUR_PREFIX/share/ardour9` | data tree |
| `ARDOUR_CONFIG_PATH` | `$JOB_STATE/ardour-config` | writable per-job config; never the service HOME |
| `HOME` / `XDG_*` | `$JOB_STATE/...` | owned per job; no shared user config |
| `LV2_PATH` | optional | extra bundles; `ARDOUR_DLL_PATH/LV2` is already scanned (`libs/ardour/search_paths.cc:125-131`, `libs/ardour/lv2_plugin.cc:3785-3805`) |
| `JACK_NO_START_SERVER` | `1` | dummy backend only; no JACK server |
| `LC_ALL` / `TZ` | `C` / `UTC` | deterministic logs |
| `DAW_STAGE` | unset | set by the driver; Lua scripts echo it in the log |
| `DAW_WORK_DIR` | unset | private work dir; all `*.part` files live here |

All paths are absolute and must match `^[A-Za-z0-9._/@+-]+$` (no spaces, no newlines). Snapshot names and stem prefixes must match `^[A-Za-z0-9._-]+$`; reject anything else before invoking Ardour.

---

## 3. Stage table (the complete chain)

Prefix the whole chain with `nice -n 19 ionice -c3` and run sequentially. `P` = `$ARDOUR_PREFIX`, `S` = `$ARDOUR_PROBE_SESSION_DIR`.

| # | stage | tool / exact command | env in | produced artifacts | accept criteria | markers emitted |
|---|---|---|---|---|---|---|
| 0 | `prepare` | Riff adapter (Python) materializes input, copies template, writes `tracks.txt`, `timing.json`, empty log dirs | canonical session input | `$S/…` skeleton, `$JOB_STATE/manifest.json` | manifest hashes parse; template sha256 recorded; no absolute checkout prefixes | `daw.stage prepare`, `daw.progress 0.02` |
| 1 | `build` | `python3 probes/ardour/build_session.py --template … --abc … --voice-map … --timing … --out $S --session-name … --abc2midi <pinned> --summary-tool probes/verify/midi_summary.py` | v1 ABC, voice map, timing, abc2midi path | `$S/…ardour` (3001 shape), `$S/interchange/<name>/midifiles/<track>.mid`, split `*.abc` | exit 0; session XML parses; N MIDI files exist; event summary per track equals fixture; program bytes 73/33/0 at tick 0 | `daw.stage build`, `daw.progress 0.15`, `daw.artifact midi <track>.mid` |
| 2 | `upgrade` | `$P/bin/ardour9-lua probes/ardour/reopen_session.lua` with `SNAPSHOT_IN=<generated> SNAPSHOT_OUT=upgraded` | session dir, logs | `$S/upgraded.ardour` (format 7004) | file exists; contains `<Session version="7004"`; log has `save ok=true`; track count matches `tracks.txt` | `daw.stage upgrade`, `daw.progress 0.25` |
| 3 | `instrument` | `$P/bin/ardour9-lua probes/ardour/instrument_session.lua` with `SNAPSHOT_IN=upgraded SNAPSHOT_OUT=instrumented`, `PLUGIN_URI=…`, optional `TRACKS_FILE` | session dir, logs, plugin URI | `$S/instrumented.ardour` | per-track `plugin_isnil=false`, `add ok=true`, `plugins_after=plugins_before+1`; file exists; log has `save ok=true`; sentinel `.instrumented` absent before run | `daw.stage instrument`, `daw.progress` per track, `daw.check instrument ok=1` |
| 4 | `tempo_range` | `python3 probes/ardour/patch_tempo_range.py --session $S/instrumented.ardour --timing … --out $S/tempoed.ardour --sample-rate 48000` | timing manifest | `$S/tempoed.ardour` | exactly one TempoMap replaced; end `b59520`; Punch `b0..b1920`; all region lengths `b<end>@b0`; no legacy tempo XML remains | `daw.stage tempo_range`, `daw.progress 0.32` |
| 5 | `reopen` | `$P/bin/ardour9-lua probes/ardour/reopen_session.lua` with `SNAPSHOT_IN=tempoed SNAPSHOT_OUT=v1-final` | session dir, logs | `$S/v1-final.ardour` | file exists; version 7004; track count matches; tempo map has three tempos/two meters at expected ticks; log `save ok=true` | `daw.stage reopen`, `daw.progress 0.40`, `daw.artifact session v1-final.ardour` |
| 6 | `mix` | `$P/bin/ardour9-export -b 16 -s 48000 -o $OUT/mix.wav.part $S v1-final` then rename to `mix.wav` | session, snapshot | `mix.wav` | RIFF/WAVE; 48 kHz; 2 ch; 16-bit; duration `21.166667 s ± 1 sample`; peak > −60 dBFS; sha256 recorded | `daw.stage mix`, `daw.progress 0.55`, `daw.artifact mix mix.wav` |
| 7 | `stems` | `$P/bin/ardour9-lua probes/ardour/save_stems.lua` with `SNAPSHOT_IN=v1-final`, `STEM_PREFIX=stem-` | session, logs, prefix | `$S/stem-1.ardour … stem-N.ardour` | exactly N snapshots; each file exists; each log line `stem <i> <name> save=true`; tracks set back to unsolo | `daw.stage stems`, `daw.progress` per track, `daw.check stems ok=1` |
| 8 | `stem_export` | loop: `$P/bin/ardour9-export -b 16 -s 48000 -o $OUT/stem-<i>.wav.part $S stem-<i>` then rename | snapshots | `stem-<i>.wav` for i=1..N | each RIFF/WAVE, same duration as mix, peak > −60 dBFS; N files; no `.part` left | `daw.stage stem_export`, `daw.progress` per stem |
| 9 | `verify` | `python3 probes/ardour/verify_timeline.py …`; `python3 probes/ardour/verify_stems.py --mix mix.wav --stem stem-1.wav …`; optional `python3 probes/verify/wav_note_check.py …` | generated MIDI, session XML, WAVs | `receipts/timeline.json`, `receipts/stems.json`, `receipts/onsets.json` | timeline: exact program/tick/note equivalence; stems: N stems, sum peak = mix peak (or ±1 LSB), diff RMS ≤ 1.0; onsets recorded with 80 ms tolerance | `daw.stage verify`, `daw.progress 0.90`, `daw.check timeline ok=1`, `daw.check stems ok=1` |
| 10 | `edit` | `python3 probes/ardour/apply_edit.py --session-dir $S --snapshot v1-final --midi-dir $EDIT_MIDI --tracks-file tracks.txt --timing timing.json --receipt receipts/edit.json`; then run `reopen` with `SNAPSHOT_IN=v1-final SNAPSHOT_OUT=v2-final`, then mix/stems/verify | v2 MIDI + same timing | `$S/v2-final.ardour`, `mix-v2.wav`, stems optional | topology identical (track names/count/source names/region lengths); each source sha256 changed; v2 mix duration `21.166667 s`; v2 mix hash ≠ v1; exact events | `daw.stage edit`, `daw.progress 0.92` |
| 11 | `receipt` | Riff-side Python assembles `riff.daw.ardour-run.v1` | all receipts | `receipt.json` | all checks `ok=1`; no `.part`; artifact hashes present; status `complete` | `daw.stage receipt`, `daw.progress 1.0`, `daw.artifact receipt receipt.json` |

`N` is the number of MIDI tracks in `tracks.txt`; the validated fixture has `N=3` (Flute, Bass, Piano; PianoRH+PianoLH share one track).

---

## 4. Per-stage contracts

### 4.1 `build` (existing Python adapter; widen input, do not fork the algorithm)

Keep `build_session.py` as the only ABC→session builder. It must receive:
`--template` (contract §1 template provenance), `--abc`, `--voice-map`, `--timing`, `--out`, `--session-name`, `--abc2midi`, `--summary-tool`.
It already groups voices by `instrument` (shared Piano = one track/two channels), injects per-channel program changes at tick 0, writes 480-tpq SMFs, and emits the 3001-shape session. Add, before it writes anything:
1. assert `--abc2midi` resolves and its `--version`/hash is the pinned 5.07 receipt;
2. assert `--template` sha256 is in the allowed template set (never a mutable path);
3. assert `timing.end_quarters * 1920 == 59520` for the 31-quarter fixture shape (or general `end_quarters*1920`);
4. write `tracks.txt` in session order and `tracks.json` with `{track, voices, programs, midi_file, sha256}`.

`build` has no Lua; it fails closed on nonzero exit and on any missing expected SMF (don’t rely on exit status alone).

### 4.2 `upgrade` / `reopen` — generalized `reopen_session.lua`

```lua
-- Env: ARDOUR_PROBE_SESSION_DIR, ARDOUR_PROBE_SNAPSHOT_IN, ARDOUR_PROBE_SNAPSHOT_OUT,
--      ARDOUR_PROBE_LOG, optional ARDOUR_PROBE_TRACKS_FILE
local function req(n) local v=os.getenv(n); assert(v and v~="", "missing "..n); return v end
local sdir  = req("ARDOUR_PROBE_SESSION_DIR")
local snap  = req("ARDOUR_PROBE_SNAPSHOT_IN")
local out   = req("ARDOUR_PROBE_SNAPSHOT_OUT")
local logp  = req("ARDOUR_PROBE_LOG")
assert(out:match("^[%w%._%-]+$"), "unsafe snapshot name")
local f = assert(io.open(logp, "w"))
local function log(x) f:write(tostring(x).."\n"); f:flush() end
local function mark(x) print(x); io.stdout:flush() end

local s, err = load_session(sdir, snap)
log("load session="..tostring(s).." err="..tostring(err))
if not s then mark("daw.error reopen load_failed"); f:close(); quit() end
local tracks = {}
for t in s:get_tracks():iter() do tracks[#tracks+1] = t end
log("tracks="..#tracks)
local tf = os.getenv("ARDOUR_PROBE_TRACKS_FILE")
if tf and tf ~= "" then
  local i = 0
  for line in io.lines(tf) do
    if line ~= "" then
      i = i + 1
      assert(tracks[i], "missing track "..i)
      assert(tostring(tracks[i]:name()) == line,
             "track "..i.." name "..tostring(tracks[i]:name()).." ~= "..line)
      log("track "..i.." name="..line)
    end
  end
  assert(i == #tracks, "track count "..#tracks.." ~= expected "..i)
end
local ok, e = pcall(function() return s:save_state(out) end)
log("save ok="..tostring(ok).." result="..tostring(e))
if not ok then mark("daw.error reopen save_failed"); f:close(); close_session(); quit() end
local xh = io.open(sdir.."/"..out..".ardour", "r")
if not xh then mark("daw.error reopen snapshot_missing"); f:close(); close_session(); quit() end
xh:close()
mark("daw.stage "..(os.getenv("DAW_STAGE") or "reopen"))
f:close(); close_session(); quit()
```

The Python driver then asserts (not the Lua script alone): file exists, `bytes > 0`, XML parses, `<Session version="7004"`, expected tempo/meter ticks for the fixture, and the log contains `save ok=true`. Accepting `save_state`’s return value alone is not sufficient (it returns `nil` on success).

### 4.3 `instrument` — generalized `instrument_session.lua`

```lua
-- Env: ARDOUR_PROBE_SESSION_DIR, ARDOUR_PROBE_SNAPSHOT_IN, ARDOUR_PROBE_SNAPSHOT_OUT,
--      ARDOUR_PROBE_LOG, ARDOUR_PROBE_PLUGIN_URI (default gmsynth),
--      optional ARDOUR_PROBE_TRACKS_FILE
local function req(n) local v=os.getenv(n); assert(v and v~="", "missing "..n); return v end
local sdir = req("ARDOUR_PROBE_SESSION_DIR")
local snap = req("ARDOUR_PROBE_SNAPSHOT_IN")
local out  = req("ARDOUR_PROBE_SNAPSHOT_OUT")
local logp = req("ARDOUR_PROBE_LOG")
local uri  = os.getenv("ARDOUR_PROBE_PLUGIN_URI") or "http://gareus.org/oss/lv2/gmsynth"
assert(out:match("^[%w%._%-]+$"), "unsafe snapshot name")
local f = assert(io.open(logp, "w"))
local function log(x) f:write(tostring(x).."\n"); f:flush() end
local function mark(x) print(x); io.stdout:flush() end
local function proc_count(t)
  for i = 0, 63 do
    local ok, p = pcall(function() return t:nth_processor(i) end)
    if not ok or p == nil then return i end
  end
  error("too many processors")
end
local s, err = load_session(sdir, snap)
log("load session="..tostring(s).." err="..tostring(err))
if not s then mark("daw.error instrument load_failed"); f:close(); quit() end
local tracks = {}
for t in s:get_tracks():iter() do tracks[#tracks+1] = t end
log("tracks="..#tracks)
local tf = os.getenv("ARDOUR_PROBE_TRACKS_FILE")
if tf and tf ~= "" then
  local i = 0
  for line in io.lines(tf) do
    if line ~= "" then
      i = i + 1
      assert(tracks[i] and tostring(tracks[i]:name()) == line,
             "track "..i.." mismatch: "..tostring(tracks[i] and tracks[i]:name()).." ~= "..line)
    end
  end
  assert(i == #tracks, "track count mismatch")
end
mark("daw.stage instrument")
for i, t in ipairs(tracks) do
  local tname = tostring(t:name())
  local before = proc_count(t)
  local p = ARDOUR.LuaAPI.new_plugin(s, uri, ARDOUR.PluginType.LV2, "")
  log("track="..i.." name="..tname..
      " plugin_isnil="..tostring(p:isnil()))
  if p:isnil() then mark("daw.error instrument plugin_load_failed"); f:close(); close_session(); quit() end
  local ok, e = pcall(function() t:add_processor_by_index(p, 0, nil, true) end)
  log("add ok="..tostring(ok).." err="..tostring(e))
  if not ok then mark("daw.error instrument add_failed"); f:close(); close_session(); quit() end
  local after = proc_count(t)
  log("plugins before="..before.." after="..after)
  if after ~= before + 1 then mark("daw.error instrument processor_count"); f:close(); close_session(); quit() end
  mark(string.format("daw.progress %.3f", (i - 1) / #tracks * 0.1 + 0.30))
end
local ok, e = pcall(function() return s:save_state(out) end)
log("save ok="..tostring(ok).." result="..tostring(e))
if not ok then mark("daw.error instrument save_failed"); f:close(); close_session(); quit() end
local xh = io.open(sdir.."/"..out..".ardour", "r")
if not xh then mark("daw.error instrument snapshot_missing"); f:close(); close_session(); quit() end
xh:close()
mark("daw.check instrument ok=1")
f:close(); close_session(); quit()
```

Notes:
- The plugin is inserted at index `0` (pre-fader instrument position), matching the validated probe.
- The **only** validated URI is gmsynth. Do not silently fall back to `a-fluidsynth`; if the payload is missing, fail with `plugin_load_failed`.
- No SF2 path is set through Lua today; the validated bundle keeps `GeneralUser_LV2.sf2` beside `gmsynth.so`. If a future `ARDOUR_PROBE_SF2` is added, it must be set as explicit plugin state and receipted; do not infer it.
- Re-running `instrument` on an already instrumented snapshot must be rejected by the driver sentinel (`$JOB_STATE/.instrumented`), not by guessing plugin names.

### 4.4 `tempo_range` — extend `patch_tempo_range.py`

Keep the current algorithm (it replaced the legacy tempo XML with `<TempoMap><Tempos>/<Meters>` and set locations/extents). Add:
- `--expect-end-quarters` (default from timing) and assert `end_q*1920 == end_b`;
- assert exactly one `<TempoMap>`, three tempo entries, two meter entries for the fixture;
- assert `Punch` start `<` end (session abort otherwise);
- emit `daw.stage tempo_range` and `daw.progress 0.32`;
- write a receipt with the resulting sha256 and the tempo/meter tick table.

### 4.5 `mix` — CLI export, not Lua

```sh
"$ARDOUR_PREFIX/bin/ardour9-export" -b 16 -s 48000 \
  -o "$OUT/mix.wav.part" "$ARDOUR_PROBE_SESSION_DIR" v1-final
mv "$OUT/mix.wav.part" "$OUT/mix.wav"
```

`ardour9-export` exports the **session range from the master bus** (`session_utils/export.cc:222-239`; default 16-bit at session rate, `-b`/`-s` override). It is not a per-route stem API; stems are the solo-snapshot path below.

Accept: `RIFF`/`WAVE`, 48 kHz, 2 channels, 16-bit, duration `21.166667 s ± 1 sample`, peak > −60 dBFS, sha256 recorded. Never promote a `.part` file.

### 4.6 `stems` — generalized `save_stems.lua`

Use the validated solo-snapshot loop, with two hardenings: assert `#tracks == N` from `tracks.txt`, and check each snapshot file exists after `save_state` (do not trust the return value).

```lua
-- Env: ARDOUR_PROBE_SESSION_DIR, ARDOUR_PROBE_SNAPSHOT_IN, ARDOUR_PROBE_STEM_PREFIX,
--      ARDOUR_PROBE_LOG, optional ARDOUR_PROBE_TRACKS_FILE
local function req(n) local v=os.getenv(n); assert(v and v~="", "missing "..n); return v end
local sdir = req("ARDOUR_PROBE_SESSION_DIR")
local snap = req("ARDOUR_PROBE_SNAPSHOT_IN")
local prefix = req("ARDOUR_PROBE_STEM_PREFIX")
local logp = req("ARDOUR_PROBE_LOG")
assert(prefix:match("^[%w%._%-]+$"), "unsafe stem prefix")
local f = assert(io.open(logp, "w"))
local function log(x) f:write(tostring(x).."\n"); f:flush() end
local function mark(x) print(x); io.stdout:flush() end
local s = load_session(sdir, snap)
log("load session="..tostring(s))
if not s then mark("daw.error stems load_failed"); f:close(); quit() end
local tracks = {}
for t in s:get_tracks():iter() do tracks[#tracks+1] = t end
log("tracks="..#tracks)
mark("daw.stage stems")
for i, t in ipairs(tracks) do
  for j, o in ipairs(tracks) do
    o:solo_control():set_value(j == i and 1.0 or 0.0, PBD.GroupControlDisposition.NoGroup)
  end
  local name = prefix..tostring(i)
  local ok, e = pcall(function() return s:save_state(name) end)
  local xh = io.open(sdir.."/"..name..".ardour", "r")
  local exists = xh ~= nil
  if xh then xh:close() end
  local tname = tostring(t:name())
  log("stem "..i.." "..tname..
      " save="..tostring(ok).." exists="..tostring(exists).." result="..tostring(e))
  if not ok or not exists then mark("daw.error stems save_failed"); f:close(); close_session(); quit() end
  mark(string.format("daw.progress %.3f", 0.60 + i / #tracks * 0.10))
end
for _, o in ipairs(tracks) do
  o:solo_control():set_value(0.0, PBD.GroupControlDisposition.NoGroup)
end
mark("daw.check stems ok=1")
f:close(); close_session(); quit()
```

### 4.7 `stem_export`

For each `i` in `1..N`:

```sh
"$ARDOUR_PREFIX/bin/ardour9-export" -b 16 -s 48000 \
  -o "$OUT/stem-$i.wav.part" "$ARDOUR_PROBE_SESSION_DIR" "stem-$i"
mv "$OUT/stem-$i.wav.part" "$OUT/stem-$i.wav"
```

Accept per stem: RIFF/WAVE, same sample rate/format/duration as mix, peak > −60 dBFS. Accept at set level: exactly N files, and `verify_stems.py` passes.

### 4.8 `edit` — same-topology source replacement (`apply_edit.py`)

This is the validated v1→v2 route: copy the **same project directory**, replace each source MIDI file with the v2 abc2midi output, then reopen/save and re-render. Do not rebuild a fresh session when only notes change.

`apply_edit.py` inputs: `--session-dir`, `--snapshot v1-final`, `--midi-dir` (new files named `<track>.mid`), `--tracks-file`, `--timing`, `--receipt`. It must, before writing:
1. parse `<Session version="7004">`, collect route names and `Diskstream`/source names;
2. assert route names and count exactly equal `tracks.txt` (same topology);
3. assert each new MIDI filename maps 1:1 to an existing `<Source name="<track>.mid">`;
4. assert region `length-beats` equals `timing.end_quarters` and the session tempo map hash equals the v1 receipt;
5. replace files atomically (`copy to .part`, `os.replace`), remove only cache dirs (`analysis/`, `*.peak`), never touch the session XML;
6. write `receipts/edit.json` with old/new sha256 per track.

Then run stage 5 (`reopen`, snapshot `v2-final`), stage 6 (mix `mix-v2.wav`), optionally stems, and stage 9 verification. If topology, timing, or programs differ, **do not edit**: trigger a full rebuild from stage 1 and record the reason.

The validated v1→v2 result is the acceptance reference: source hashes change for all three tracks; duration stays `21.166667 s`; v2 mix sha256 differs from v1; exact events remain Flute 12 / Bass 30 / Piano 37 with programs 73/33/0.

### 4.9 `verify` — reuse the committed checkers

```sh
python3 probes/ardour/verify_timeline.py \
  --abc <v1|v2>.abc --voice-map <voice-map>.json --timing <timing>.json \
  --midi-dir "$S/interchange/<name>/midifiles" \
  --session-xml "$S/v1-final.ardour" --abc2midi <pinned abc2midi> \
  --summary-tool probes/verify/midi_summary.py > receipts/timeline.json

python3 probes/ardour/verify_stems.py \
  --mix "$OUT/mix.wav" --stem "$OUT/stem-1.wav" --stem "$OUT/stem-2.wav" --stem "$OUT/stem-3.wav" \
  --out receipts/stems.json
```

`verify_timeline.py` asserts exact abc2midi equivalence after the +1-tick normalization and the tempo/meter/range structure; `verify_stems.py` sums stems against the mix. `wav_note_check.py` is optional, per-stem exact / mix suggestive, 80 ms tolerance; record its output as a detector receipt, not as an event-equivalence claim.

---

## 5. Fail-closed checks (invariant I5: never exit status alone)

Every stage runs through a Python driver function `run_stage()` that applies the checks below. A stage passes only when **all** applicable checks pass; any failure writes `daw.error <stage> <code>`, marks the job failed, and promotes nothing.

| code | check | exact assertion |
|---|---|---|
| C1 | exit status | process return code is the expected 0 (necessary, not sufficient) |
| C2 | expected log lines | log matches required regexes (e.g. `^save ok=true$`, `^add ok=true$`, `^tracks=3$`); forbidden patterns (`^Error:`, `failed to load`, `cannot open`) fail |
| C3 | artifact exists | every expected path exists, `bytes > 0`, and is not a `.part` |
| C4 | artifact type | session XML parses with `<Session version="7004">`; WAV starts `RIFF`/`WAVE`; JSON parses; sha256 recorded |
| C5 | musical structure | track count/names equal `tracks.txt`; tempo entries `0/7680/12960`; meters `0/11520/12960`; session end `b59520`; region length `b59520@b0` |
| C6 | event equivalence | `verify_timeline.py` passes: Flute 12 / Bass 30 / Piano 37, programs 73/33/0, no extra/missing events |
| C7 | audio non-silence | `peak > -60 dBFS`; duration within ±1 sample of `21.166667 s`; sample rate 48 kHz; 2 ch; 16-bit |
| C8 | stem reconstruction | N stems exist; each nonzero; `sum_peak == mix_peak` (or ±1 LSB); `difference_rms <= 1.0` |
| C9 | reopen | reopened snapshot exists and re-renders to the same duration; log has `save ok=true` |
| C10 | negative controls | missing SF2 / bad snapshot / wrong track count each fail with a non-zero status and no promoted artifact |

Driver behaviour:
- log files are opened in append mode across sub-steps, with one `--- stage: <name> ---` separator; the check reads only the current stage’s slice;
- `DummyMidiBuffer: it's too late for this event` is a **known warning** from the dummy backend; do not fail on it, count it, and require the count to be stable across the same input (±small jitter). Record it in the receipt as `known_warnings.dummy_midi_buffer`.
- never accept `save_state`’s `nil` return as success; always check the resulting file and the `save ok=true` line.

---

## 6. Progress markers for Riff live-status

### 6.1 Problem

`studio_core.observe_generation()` is currently hard-coded to YuE2 markers (`yue2.ar.`, `yue2.nar.`, `yue2.nar.progress`; `studio_core.py:42-60`). DAW jobs emit none of these, so live status would sit on “Preparing the studio” until completion.

### 6.2 Proposed line protocol (trivially parseable)

One marker per line, space-separated, no JSON, no quoting, no spaces inside values:

```
daw.stage <stage>
daw.progress <float 0..1>
daw.artifact <kind> <relative-path>
daw.check <name> ok=<0|1>
daw.error <stage> <code>
```

- `<stage>` ∈ `prepare|build|upgrade|instrument|tempo_range|reopen|mix|stems|stem_export|verify|edit|receipt`.
- `<kind>` ∈ `session|midi|mix|stem|receipt|log`.
- `<relative-path>` restricted to `^[A-Za-z0-9._/-]+$` (no spaces); it is relative to the job output root.
- `daw.check` is emitted only after the corresponding C-check passes (`ok=1`) or fails (`ok=0`); `daw.error` is always followed by a non-zero exit.
- Stdout must be flushed after each line (`io.stdout:flush()` in Lua; `print(..., flush=True)` in Python).

### 6.3 Emission points

| stage | where emitted |
|---|---|
| `prepare` | Riff-side driver before spawning anything; `daw.progress 0.02` |
| `build` | `build_session.py` start/after each track SMF; `daw.artifact midi <track>.mid` |
| `upgrade` | before invoking Lua; Lua echoes `daw.stage <DAW_STAGE>`; `daw.progress 0.25` after reopen check |
| `instrument` | Lua prints `daw.stage instrument` once, then `daw.progress` per track (`0.30..0.40`), then `daw.check instrument ok=1` |
| `tempo_range` | Python patch prints `daw.stage tempo_range`, `daw.progress 0.42` |
| `reopen` | Lua prints stage; driver prints `daw.progress 0.45` after checks |
| `mix` | shell driver before/after export; `daw.progress 0.55`, `daw.artifact mix mix.wav` |
| `stems` | Lua stage + per-track progress (`0.60..0.70`), `daw.check stems ok=1` |
| `stem_export` | driver per stem; `daw.progress 0.70..0.85`, `daw.artifact stem stem-<i>.wav` |
| `verify` | driver prints `daw.check timeline ok=1`, `daw.check stems ok=1`, `daw.progress 0.90` |
| `edit` | `apply_edit.py` + repeat stages; `daw.stage edit`, `daw.progress 0.92` |
| `receipt` | driver prints `daw.stage receipt`, `daw.progress 1.0`, `daw.artifact receipt receipt.json` |

### 6.4 Parser change (minimal, additive)

Keep the existing `yue2.*` rules untouched. Add:

```python
DAW_STAGES = {
    "prepare": ("Preparing the session", 1),
    "build": ("Building the arrangement", 1),
    "upgrade": ("Opening the session", 2),
    "instrument": ("Assigning instruments", 2),
    "tempo_range": ("Mapping tempo and bars", 2),
    "reopen": ("Verifying the session", 2),
    "mix": ("Rendering the mix", 3),
    "stems": ("Rendering instrument stems", 3),
    "stem_export": ("Rendering instrument stems", 3),
    "verify": ("Checking the render", 3),
    "edit": ("Applying the edit", 3),
    "receipt": ("Finalizing", 4),
}
# in observe_generation(live, lines):
for name, (label, index) in DAW_STAGES.items():
    if ("daw.stage " + name) in lines and index >= live["stage_index"]:
        live.update(stage=label, stage_index=index)
for value in re.findall(r"^daw\.progress ([0-9]*\.?[0-9]+)$", lines, re.M):
    p = float(value)
    if math.isfinite(p) and 0 <= p <= 1:
        live["stage_progress"] = max(live.get("stage_progress", 0), p)
```

- `daw.progress` is accepted for any DAW stage, not only stage index 2; this is the difference from the YuE2 rule.
- `daw.check`/`daw.artifact`/`daw.error` are parsed by the receipt collector, not by `observe_generation`; unknown markers are ignored.
- If the parser is not patched yet, the runner still writes all markers to the job log; the UI simply will not move. Nothing is lost.

---

## 7. Job receipt (`riff.daw.ardour-run.v1`) — required fields

```json
{
  "schema": "riff.daw.ardour-run.v1",
  "job_id": "<riff job id>",
  "kind": "daw.compose|daw.render",
  "status": "complete|failed",
  "pin": {"tag": "9.8", "tag_object": "059c9588...", "commit": "22ed8656c..."},
  "runtime": {"prefix": "/opt/riff/ardour-9.8.0", "ardour_version": "9.8.0"},
  "input": {"session_input_sha256": "...", "abc_sha256": "...", "voice_map_sha256": "...", "timing_sha256": "...", "template_sha256": "..."},
  "topology": {"tracks": 3, "track_names": ["Flute", "Bass", "Piano"], "programs": [73, 33, 0]},
  "stages": [{"name": "build", "ok": true, "log_sha256": "...", "duration_s": 0.0}],
  "artifacts": [{"kind": "mix", "path": "mix.wav", "sha256": "...", "bytes": 0, "duration_s": 21.166667, "peak_dbfs": -5.262841}],
  "checks": {"C1": true, "C2": true, "C6": true, "C8": true},
  "known_warnings": {"dummy_midi_buffer": 0},
  "limits": ["demo-vs-source", "dummy-backend timing", "no timbre-quality claim", "solo-render stems, not GUI stems"]
}
```

The receipt is assembled only after all C-checks; `status=failed` receipts are kept for diagnosis but never referenced as deliverables. Hash the log slice, not the whole concatenated log, so a rerun does not change an unrelated stage’s hash.

---

## 8. Atomicity, cancellation, cleanup

- Work in a private `$DAW_WORK_DIR`; publish to `$OUT` only after the stage’s checks pass.
- All exports write `<name>.part` and are renamed with `os.replace`/`mv` only after C4/C7 pass. Snapshots are written by Ardour directly (cannot be atomic); the driver checks existence and moves on, and a failed/cancelled job’s work dir is discarded, never published.
- On cancellation (`SIGTERM` from Riff), the driver forwards to the child process group, waits, then removes `$DAW_WORK_DIR`; it never sends `SIGKILL` itself. Partial `.part` files are removed.
- No stage writes to the shared checkout. `HOME`, `XDG_*`, and `ARDOUR_CONFIG_PATH` are per-job.
- The driver is idempotent per output root: rerunning a completed job must fail with “output exists” unless `--force-new-work` is passed; determinism is checked in a fresh work dir, not by overwriting.

---

## 9. Explicit non-goals / uncertainty

- **No headless `import_files` claim.** The validated construction path is a generated 3001-shape session upgraded to 7004, then instrumented; the headless MIDI-import API is still not a supported Riff operation.
- **No sample-accurate claim under the Dummy backend.** `DummyMidiBuffer: it's too late for this event` warnings are recorded, not explained away; event-level equivalence is at the MIDI/timeline level, audio onsets use an 80 ms detector tolerance.
- **No timbre-quality claim from GM program numbers.** The 73↔0 swap proves program-controlled timbre only; labels like “Flute” are intent, not verified instrument identity.
- **Stems are solo snapshots rendered through `ardour9-export`, not the GUI stem dialog**, and not raw/pre-fader or batch stems.
- **No GUI stage.** The GUI frontend is compiled (upstream has no `--no-gui` switch) but never launched.
- **Template provenance is a contract §1 decision.** This stage table is template-agnostic at stage 1; the currently validated path is the 3001 `1region` fixture. A clean 7004 template path requires its own build/reopen receipt before it can replace the fixture.
- **Instrument payload licensing is not cleared here.** gmsynth is an external payload (GPL-family, version unresolved; SF2 custom/not OSI); `a-fluidsynth` is the in-tree alternative and would need a fresh per-track program battery.
- **Program markers are a proposal.** Until `observe_generation` gains the `daw.*` branch, live status does not move; the log still contains the markers.

---
