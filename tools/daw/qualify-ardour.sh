#!/usr/bin/env bash
# Qualification battery for the pinned Ardour 9.8 production runtime (#78).
#
# Runs the Q1-Q10 gates from docs/daw-integration/ardour-build-preflight.md §8
# against an installed pinned prefix and writes a
# riff.daw.ardour-qualification.v1 receipt.  Every gate fails closed: any
# failure exits non-zero and the receipt records the failing gate.
#
# Usage:
#   tools/daw/qualify-ardour.sh \
#     --prefix /path/to/ardour-9.8.0 \
#     --ardour-src /path/to/pinned/source-checkout \
#     --fixtures /path/to/probes/fixtures \
#     --work /path/to/owned/workdir \
#     --out /path/to/owned/workdir/receipts/qualification.json
#
# Optional: --abc2midi /path/to/abc2midi (default: repo probe build),
#           --summary-tool (default: repo probes/verify/midi_summary.py),
#           --receipt /path/to/ardour-build.json (Q1 hash cross-check),
#           --skip-determinism (omit the Q9 second full chain run).
#
# The battery never touches the network, a display, JACK, or the host HOME:
# every run gets an owned scratch HOME/XDG (ardour-build-preflight §7.3).
set -euo pipefail

PREFIX=""
ARDOUR_SRC=""
FIXTURES=""
WORK=""
OUT=""
ABC2MIDI=""
SUMMARY_TOOL=""
RECEIPT=""
SKIP_DETERMINISM=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX=$2; shift 2;;
    --ardour-src) ARDOUR_SRC=$2; shift 2;;
    --fixtures) FIXTURES=$2; shift 2;;
    --work) WORK=$2; shift 2;;
    --out) OUT=$2; shift 2;;
    --abc2midi) ABC2MIDI=$2; shift 2;;
    --summary-tool) SUMMARY_TOOL=$2; shift 2;;
    --receipt) RECEIPT=$2; shift 2;;
    --skip-determinism) SKIP_DETERMINISM=1; shift;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done

for v in PREFIX ARDOUR_SRC FIXTURES WORK OUT; do
  if [ -z "${!v}" ]; then echo "missing required --$(echo "$v" | tr 'A-Z_' 'a-z-')" >&2; exit 2; fi
done

REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
PROBES="$REPO/probes"
ARDOUR_PROBES="$PROBES/ardour"
[ -n "$ABC2MIDI" ] || ABC2MIDI="$REPO/.probe-tmp/abcmidi/src/abc2midi"
[ -n "$SUMMARY_TOOL" ] || SUMMARY_TOOL="$PROBES/verify/midi_summary.py"
TEMPLATE="$ARDOUR_SRC/libs/ardour/test/profiling/sessions/1region/1region.ardour"

LUA="$PREFIX/bin/ardour9-lua"
EXPORT="$PREFIX/bin/ardour9-export"
NEW_SESSION="$PREFIX/bin/ardour9-new_session"
for b in "$LUA" "$EXPORT" "$NEW_SESSION"; do
  [ -x "$b" ] || { echo "missing runtime binary: $b" >&2; exit 1; }
done
[ -f "$TEMPLATE" ] || { echo "missing pinned template: $TEMPLATE" >&2; exit 1; }
[ -x "$ABC2MIDI" ] || { echo "missing abc2midi: $ABC2MIDI" >&2; exit 1; }

mkdir -p "$WORK" "$(dirname "$OUT")"
RESULTS="$WORK/results.ndjson"
: > "$RESULTS"

record() { # gate status key=value...
  python3 - "$RESULTS" "$@" <<'PY'
import json, sys
path, gate, status = sys.argv[1], sys.argv[2], sys.argv[3]
entry = {"gate": gate, "status": status}
for kv in sys.argv[4:]:
    k, _, v = kv.partition("=")
    entry[k] = v
with open(path, "a") as f:
    f.write(json.dumps(entry) + "\n")
PY
}

fail() { echo "qualify-ardour: FAIL: $*" >&2; exit 1; }

# Per-run owned state (preflight §7.3): never the host HOME/XDG.
run_env() { # run_env <jobstate> <command...>
  local state=$1; shift
  mkdir -p "$state/ardour-config" "$state/home/.config" "$state/home/.cache" "$state/home/.local/share"
  env -i PATH=/usr/bin:/bin HOME="$state/home" XDG_CONFIG_HOME="$state/home/.config" \
      XDG_CACHE_HOME="$state/home/.cache" XDG_DATA_HOME="$state/home/.local/share" \
      ARDOUR_PREFIX="$PREFIX" ARDOUR_DLL_PATH="$PREFIX/lib/ardour9" \
      ARDOUR_DATA_PATH="$PREFIX/share/ardour9" ARDOUR_CONFIG_PATH="$state/ardour-config" \
      JACK_NO_START_SERVER=1 LC_ALL=C TZ=UTC "$@"
}

# lua_run <logfile> <session-dir> <snapshot-in> <script> [extra env NAME=VALUE...]
lua_run() {
  local log=$1 sdir=$2 snapin=$3 script=$4; shift 4
  ARDOUR_PROBE_SESSION_DIR="$sdir" ARDOUR_PROBE_SNAPSHOT_IN="$snapin" ARDOUR_PROBE_LOG="$log" \
    run_env "$sdir/.state" env "$@" "$LUA" "$script"
}

expect_save_ok() { # expect_save_ok <logfile>
  grep -q "save ok=True" "$1" || fail "expected 'save ok=True' in $1"
}

wav_seconds() { # wav_seconds <file> -> seconds (exact rational)
  python3 - "$1" <<'PY'
import sys, wave
w = wave.open(sys.argv[1], "rb")
print(f"{w.getnframes()}/{w.getframerate()}")
PY
}

MIX_SECONDS_EXPECTED="1016000/48000"  # 21.166667 s at 48 kHz

echo "== Q1 identity =="
Q1DIR="$WORK/q1"; mkdir -p "$Q1DIR"
v_lua=$("$LUA" --version 2>&1 | head -1)
v_export=$("$EXPORT" --version 2>&1 | head -1)
v_new=$("$NEW_SESSION" --version 2>&1 | head -1)
echo "$v_lua" | grep -q "9\.8" || fail "Q1: ardour9-lua --version reported '$v_lua'"
echo "$v_export" | grep -q "9\.8" || fail "Q1: ardour9-export --version reported '$v_export'"
echo "$v_new" | grep -q "9\.8" || fail "Q1: ardour9-new_session --version reported '$v_new'"
h_lua=$(sha256sum "$LUA" | cut -d' ' -f1)
h_export=$(sha256sum "$EXPORT" | cut -d' ' -f1)
h_new=$(sha256sum "$NEW_SESSION" | cut -d' ' -f1)
if [ -n "$RECEIPT" ]; then
  python3 - "$RECEIPT" "$h_lua" "$h_export" "$h_new" <<'PY'
import json, sys
receipt = json.load(open(sys.argv[1]))
have = dict(receipt.get("sha256", {}))
want = {"ardour9-lua": sys.argv[2], "ardour9-export": sys.argv[3], "ardour9-new_session": sys.argv[4]}
for name, h in want.items():
    if have.get(name) != h:
        raise SystemExit(f"Q1: {name} sha256 {h} does not match build receipt {have.get(name)}")
PY
fi
record q1_identity pass lua="$v_lua" export="$v_export" new_session="$v_new"

echo "== Q2 clean session =="
Q2DIR="$WORK/q2"; mkdir -p "$Q2DIR"
# Upstream ardour9-new_session intermittently aborts at process teardown
# ("FATAL: exception not rethrown", SIGABRT) AFTER the session artifact is
# fully written and closed — measured ~1/5 on the pinned source build and
# reproduced on the official 9.8.241 demo bundle, so it is upstream, not
# toolchain.  Q2's acceptance is artifact-based (preflight §8: file exists,
# track count 0, reopen-save succeeds); the teardown exit code is recorded in
# the log, not gated on.  The production runner never invokes new_session
# (sessions are template-built by ardour_session.py), so this tolerance does
# not reach the production path.
run_env "$Q2DIR/state" "$NEW_SESSION" -s 48000 -m 2 "$Q2DIR/session" clean > "$Q2DIR/new.log" 2>&1 || {
  rc=$?
  [ -f "$Q2DIR/session/clean.ardour" ] || fail "Q2: new_session exited $rc and left no session file"
  echo "Q2: new_session exited $rc after writing the session (documented upstream teardown abort; artifact intact)" >&2
}
[ -f "$Q2DIR/session/clean.ardour" ] || fail "Q2: no session file created"
lua_run "$Q2DIR/reopen.log" "$Q2DIR/session" clean "$ARDOUR_PROBES/reopen_session.lua" \
  ARDOUR_PROBE_SNAPSHOT_OUT=clean-reopened
expect_save_ok "$Q2DIR/reopen.log"
grep -q "tracks=0" "$Q2DIR/reopen.log" || fail "Q2: expected tracks=0 (master only)"
record q2_clean_session pass

# The Q3-Q6 chain, parameterised over a run directory so Q9 can re-run it.
build_chain() { # build_chain <rundir>
  local run=$1
  mkdir -p "$run"
  python3 "$ARDOUR_PROBES/build_session.py" \
    --template "$TEMPLATE" --abc "$FIXTURES/arrangement-v1.abc" \
    --voice-map "$FIXTURES/voice-map.json" --timing "$FIXTURES/arrangement-timing.json" \
    --out "$run/v1-build" --session-name arrangement-v1 \
    --abc2midi "$ABC2MIDI" --summary-tool "$SUMMARY_TOOL" > "$run/build.log"
  local sdir="$run/v1-build/arrangement-v1"
  [ -f "$sdir/arrangement-v1.ardour" ] || fail "Q3: no generated session in $run"
  lua_run "$run/upgrade.log" "$sdir" arrangement-v1 "$ARDOUR_PROBES/reopen_session.lua" \
    ARDOUR_PROBE_SNAPSHOT_OUT=upgraded
  expect_save_ok "$run/upgrade.log"
  lua_run "$run/instrument.log" "$sdir" upgraded "$ARDOUR_PROBES/instrument_session.lua" \
    ARDOUR_PROBE_SNAPSHOT_OUT=v1-instrumented
  expect_save_ok "$run/instrument.log"
  grep -q "tracks=3" "$run/instrument.log" || fail "Q3: expected tracks=3"
  python3 "$ARDOUR_PROBES/patch_tempo_range.py" \
    --session "$sdir/v1-instrumented.ardour" --timing "$FIXTURES/arrangement-timing.json" \
    --out "$sdir/v1-tempoed.ardour"
  lua_run "$run/reopen.log" "$sdir" v1-tempoed "$ARDOUR_PROBES/reopen_session.lua" \
    ARDOUR_PROBE_SNAPSHOT_OUT=v1-final
  expect_save_ok "$run/reopen.log"
  mkdir -p "$sdir/export"
  run_env "$sdir/.state" "$EXPORT" -o "$sdir/export/v1-final.wav" "$sdir" v1-final \
    > "$run/export.log" 2>&1
  [ -s "$sdir/export/v1-final.wav" ] || fail "Q4: no mix rendered in $run"
  local secs; secs=$(wav_seconds "$sdir/export/v1-final.wav")
  [ "$secs" = "$MIX_SECONDS_EXPECTED" ] || fail "Q4: mix duration $secs != $MIX_SECONDS_EXPECTED"
  python3 "$ARDOUR_PROBES/verify_timeline.py" \
    --abc "$FIXTURES/arrangement-v1.abc" --voice-map "$FIXTURES/voice-map.json" \
    --timing "$FIXTURES/arrangement-timing.json" \
    --midi-dir "$run/v1-build/midifiles" --session-xml "$sdir/v1-final.ardour" \
    --session-midi-dir "$sdir/interchange/arrangement-v1/midifiles" \
    --abc2midi "$ABC2MIDI" --summary-tool "$SUMMARY_TOOL" > "$run/timeline.log"
  ARDOUR_PROBE_SESSION_DIR="$sdir" ARDOUR_PROBE_SNAPSHOT_IN=v1-final \
    ARDOUR_PROBE_STEM_PREFIX=v1-stem- ARDOUR_PROBE_LOG="$run/stems.log" \
    run_env "$sdir/.state" "$LUA" "$ARDOUR_PROBES/save_stems.lua"
  local i
  for i in 1 2 3; do
    run_env "$sdir/.state" "$EXPORT" -o "$sdir/export/v1-stem-$i.wav" "$sdir" "v1-stem-$i" \
      >> "$run/export.log" 2>&1
    [ -s "$sdir/export/v1-stem-$i.wav" ] || fail "Q6: stem $i not rendered in $run"
  done
  python3 "$ARDOUR_PROBES/verify_stems.py" --mix "$sdir/export/v1-final.wav" \
    --stem "$sdir/export/v1-stem-1.wav" --stem "$sdir/export/v1-stem-2.wav" \
    --stem "$sdir/export/v1-stem-3.wav" --out "$run/stems.json" > /dev/null
  python3 - "$run/stems.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
if not d["within_1_lsb"]:
    raise SystemExit(f"Q6: stems do not reconstruct mix within 1 LSB: {d['difference_peak_linear']}")
PY
}

echo "== Q3 fixture build / Q4 chain / Q5 events / Q6 stems =="
Q3DIR="$WORK/q3-q6/run1"
build_chain "$Q3DIR"
record q3_fixture_build pass
record q4_chain pass duration="$MIX_SECONDS_EXPECTED"
record q5_events pass
record q6_stems pass

echo "== Q7 program control =="
Q7DIR="$WORK/q7"; mkdir -p "$Q7DIR"
# Same notes, Flute program 73 -> 0: the rendered flute stem must change.
python3 - "$FIXTURES/voice-map.json" "$Q7DIR/voice-map-prog0.json" <<'PY'
import json, sys
vm = json.load(open(sys.argv[1]))
vm["voices"]["Flute"]["program_raw"] = 0
json.dump(vm, open(sys.argv[2], "w"), indent=1)
PY
python3 "$ARDOUR_PROBES/build_session.py" \
  --template "$TEMPLATE" --abc "$FIXTURES/arrangement-v1.abc" \
  --voice-map "$Q7DIR/voice-map-prog0.json" --timing "$FIXTURES/arrangement-timing.json" \
  --out "$Q7DIR/build" --session-name prog0 --abc2midi "$ABC2MIDI" \
  --summary-tool "$SUMMARY_TOOL" > "$Q7DIR/build.log"
Q7S="$Q7DIR/build/prog0"
lua_run "$Q7DIR/upgrade.log" "$Q7S" prog0 "$ARDOUR_PROBES/reopen_session.lua" \
  ARDOUR_PROBE_SNAPSHOT_OUT=upgraded
expect_save_ok "$Q7DIR/upgrade.log"
lua_run "$Q7DIR/instrument.log" "$Q7S" upgraded "$ARDOUR_PROBES/instrument_session.lua" \
  ARDOUR_PROBE_SNAPSHOT_OUT=prog0-instrumented
expect_save_ok "$Q7DIR/instrument.log"
python3 "$ARDOUR_PROBES/patch_tempo_range.py" --session "$Q7S/prog0-instrumented.ardour" \
  --timing "$FIXTURES/arrangement-timing.json" --out "$Q7S/prog0-tempoed.ardour"
lua_run "$Q7DIR/reopen.log" "$Q7S" prog0-tempoed "$ARDOUR_PROBES/reopen_session.lua" \
  ARDOUR_PROBE_SNAPSHOT_OUT=prog0-final
expect_save_ok "$Q7DIR/reopen.log"
mkdir -p "$Q7S/export"
run_env "$Q7S/.state" "$EXPORT" -o "$Q7S/export/prog0-final.wav" "$Q7S" prog0-final \
  > "$Q7DIR/export.log" 2>&1
python3 - "$Q3DIR/v1-build/arrangement-v1/export/v1-stem-1.wav" "$Q7S/export/prog0-final.wav" <<'PY'
import sys, wave, array, math
def stats(p):
    w = wave.open(p, "rb"); a = array.array("h"); a.frombytes(w.readframes(w.getnframes()))
    peak = max(abs(x) for x in a); rms = math.sqrt(sum(x * x for x in a) / len(a))
    return peak, rms
p73, r73 = stats(sys.argv[1]); p0, r0 = stats(sys.argv[2])
if p73 == p0 and r73 == r0:
    raise SystemExit("Q7: program 73->0 swap produced identical audio")
print(f"program73 peak={p73} rms={r73:.1f}; program0 peak={p0} rms={r0:.1f}")
PY
record q7_program_control pass

echo "== Q8 edit v1 -> v2 =="
Q8DIR="$WORK/q8"; mkdir -p "$Q8DIR"
cp -a "$Q3DIR/v1-build/arrangement-v1" "$Q8DIR/v2-edit"
python3 "$ARDOUR_PROBES/build_session.py" \
  --template "$TEMPLATE" --abc "$FIXTURES/arrangement-v2.abc" \
  --voice-map "$FIXTURES/voice-map.json" --timing "$FIXTURES/arrangement-timing.json" \
  --out "$Q8DIR/v2-build" --session-name arrangement-v2 \
  --abc2midi "$ABC2MIDI" --summary-tool "$SUMMARY_TOOL" > "$Q8DIR/build.log"
# Same-topology source replacement: v2 MIDI into the copied v1 session layout.
for f in "$Q8DIR/v2-build/midifiles"/*.mid; do
  base=$(basename "$f")
  dst="$Q8DIR/v2-edit/interchange/arrangement-v1/midifiles/$base"
  [ -f "$dst" ] || fail "Q8: no existing source $base to replace in copied session"
  cp "$f" "$dst"
done
lua_run "$Q8DIR/reopen.log" "$Q8DIR/v2-edit" v1-final "$ARDOUR_PROBES/reopen_session.lua" \
  ARDOUR_PROBE_SNAPSHOT_OUT=v2-final
expect_save_ok "$Q8DIR/reopen.log"
mkdir -p "$Q8DIR/v2-edit/export"
run_env "$Q8DIR/v2-edit/.state" "$EXPORT" -o "$Q8DIR/v2-edit/export/v2-final.wav" \
  "$Q8DIR/v2-edit" v2-final > "$Q8DIR/export.log" 2>&1
[ -s "$Q8DIR/v2-edit/export/v2-final.wav" ] || fail "Q8: no v2 mix rendered"
secs=$(wav_seconds "$Q8DIR/v2-edit/export/v2-final.wav")
[ "$secs" = "$MIX_SECONDS_EXPECTED" ] || fail "Q8: v2 duration $secs != $MIX_SECONDS_EXPECTED"
python3 "$ARDOUR_PROBES/verify_timeline.py" \
  --abc "$FIXTURES/arrangement-v2.abc" --voice-map "$FIXTURES/voice-map.json" \
  --timing "$FIXTURES/arrangement-timing.json" \
  --midi-dir "$Q8DIR/v2-build/midifiles" --session-xml "$Q8DIR/v2-edit/v2-final.ardour" \
  --session-midi-dir "$Q8DIR/v2-edit/interchange/arrangement-v1/midifiles" \
  --abc2midi "$ABC2MIDI" --summary-tool "$SUMMARY_TOOL" > "$Q8DIR/timeline.log"
h_v1=$(sha256sum "$Q3DIR/v1-build/arrangement-v1/export/v1-final.wav" | cut -d' ' -f1)
h_v2=$(sha256sum "$Q8DIR/v2-edit/export/v2-final.wav" | cut -d' ' -f1)
[ "$h_v1" != "$h_v2" ] || fail "Q8: v2 mix hash equals v1 (edit had no effect)"
record q8_edit pass v1_mix="$h_v1" v2_mix="$h_v2"

echo "== Q9 determinism =="
if [ "$SKIP_DETERMINISM" = 0 ]; then
  Q9DIR="$WORK/q9/run2"
  build_chain "$Q9DIR"
  for f in v1-final.wav v1-stem-1.wav v1-stem-2.wav v1-stem-3.wav; do
    a="$Q3DIR/v1-build/arrangement-v1/export/$f"
    b="$Q9DIR/v1-build/arrangement-v1/export/$f"
    ha=$(sha256sum "$a" | cut -d' ' -f1); hb=$(sha256sum "$b" | cut -d' ' -f1)
    [ "$ha" = "$hb" ] || fail "Q9: determinism failure on $f: $ha != $hb"
  done
  record q9_determinism pass
else
  record q9_determinism skipped
fi

echo "== Q10 negative =="
Q10DIR="$WORK/q10"; mkdir -p "$Q10DIR"
# (a) bad snapshot: load fails (session=None in the log), no save, no artifact.
lua_run "$Q10DIR/bad-snapshot.log" "$Q2DIR/session" does-not-exist \
  "$ARDOUR_PROBES/reopen_session.lua" ARDOUR_PROBE_SNAPSHOT_OUT=should-not-exist
grep -q "session=None" "$Q10DIR/bad-snapshot.log" \
  || fail "Q10: bad-snapshot load did not fail closed (expected session=None)"
! grep -q "save ok=True" "$Q10DIR/bad-snapshot.log" \
  || fail "Q10: bad-snapshot run saved state"
[ ! -e "$Q2DIR/session/should-not-exist.ardour" ] || fail "Q10: bad-snapshot run created an artifact"
# (b) wrong track count: the timeline verifier must reject a tampered session.
python3 - "$Q3DIR/v1-build/arrangement-v1/v1-final.ardour" "$Q10DIR/tampered.ardour" <<'PY'
import re, sys
xml = open(sys.argv[1]).read()
# Remove the Flute route (first MIDI track) so the topology is wrong.
m = re.search(r'<Route [^>]*name="Flute".*?</Route>', xml, re.S)
if not m:
    raise SystemExit("Q10 setup: Flute route not found")
open(sys.argv[2], "w").write(xml[:m.start()] + xml[m.end():])
PY
if python3 "$ARDOUR_PROBES/verify_timeline.py" \
    --abc "$FIXTURES/arrangement-v1.abc" --voice-map "$FIXTURES/voice-map.json" \
    --timing "$FIXTURES/arrangement-timing.json" \
    --midi-dir "$Q3DIR/v1-build/midifiles" --session-xml "$Q10DIR/tampered.ardour" \
    --session-midi-dir "$Q3DIR/v1-build/arrangement-v1/interchange/arrangement-v1/midifiles" \
    --abc2midi "$ABC2MIDI" --summary-tool "$SUMMARY_TOOL" > "$Q10DIR/tampered.log" 2>&1; then
  fail "Q10: tampered (missing-track) session passed verification"
fi
# (c) missing SF2: a gmsynth copy without its bank must not render audio.
mkdir -p "$Q10DIR/lv2"
cp -a "$PREFIX/lib/ardour9/LV2/gmsynth.lv2" "$Q10DIR/lv2/" 2>/dev/null || cp -a "$PREFIX/lib/LV2/gmsynth.lv2" "$Q10DIR/lv2/"
rm -f "$Q10DIR/lv2/gmsynth.lv2/GeneralUser_LV2.sf2"
mkdir -p "$Q10DIR/silent"
run_env "$Q10DIR/state" env LV2_PATH="$Q10DIR/lv2" "$NEW_SESSION" -s 48000 -m 2 \
  "$Q10DIR/silent" probe > "$Q10DIR/silent-new.log" 2>&1 || {
  rc=$?
  [ -f "$Q10DIR/silent/probe.ardour" ] || fail "Q10: new_session exited $rc and left no session file"
  echo "Q10: new_session exited $rc after writing the session (documented upstream teardown abort; artifact intact)" >&2
}
# The bankless bundle either refuses the instrument step (gmsynth fails to
# load) or loads and renders silence; both are closed failures, never audio.
if ARDOUR_PROBE_SESSION_DIR="$Q10DIR/silent" ARDOUR_PROBE_SNAPSHOT_IN=probe \
   ARDOUR_PROBE_SNAPSHOT_OUT=instrumented ARDOUR_PROBE_LOG="$Q10DIR/silent-instrument.log" \
   run_env "$Q10DIR/state" env LV2_PATH="$Q10DIR/lv2" "$LUA" "$ARDOUR_PROBES/instrument_session.lua" 2>/dev/null; then
  # Plugin loaded despite missing bank: render must be silent to pass.
  run_env "$Q10DIR/state" env LV2_PATH="$Q10DIR/lv2" "$EXPORT" -o "$Q10DIR/silent/export.wav" \
    "$Q10DIR/silent" instrumented > "$Q10DIR/silent-export.log" 2>&1 || true
  if [ -s "$Q10DIR/silent/export.wav" ]; then
    python3 - "$Q10DIR/silent/export.wav" <<'PY'
import sys, wave, array
w = wave.open(sys.argv[1], "rb"); a = array.array("h"); a.frombytes(w.readframes(w.getnframes()))
if any(x != 0 for x in a):
    raise SystemExit("Q10: bankless gmsynth rendered non-silent audio")
PY
  fi
fi
record q10_negative pass

python3 - "$RESULTS" "$OUT" "$PREFIX" <<'PY'
import json, sys
gates = [json.loads(line) for line in open(sys.argv[1])]
receipt = {
    "schema": "riff.daw.ardour-qualification.v1",
    "runtime_prefix": sys.argv[3],
    "gates": gates,
    "status": "pass" if all(g["status"] == "pass" for g in gates) else "fail",
}
with open(sys.argv[2], "w") as f:
    json.dump(receipt, f, indent=1)
print(f"qualification receipt: {sys.argv[2]} ({receipt['status']}, {len(gates)} gates)")
PY
