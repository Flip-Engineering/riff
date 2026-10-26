"""Focused checks for the daw.compose / daw.render operation integration.

Runs against a real StudioServer with a hermetic pinned-runtime record
(hash-verified stubs): the shared queue, admission, receipts and artifact
recording are exercised end to end without Ardour, a model, or a native
build.  The fake ardour9-lua runs the REAL maintained Lua scripts from
scripts/daw/ under a real liblua interpreter with a stub Ardour API derived
from the actual session file, so the runner is checked against the committed
log protocol rather than a mirror of its own strings.  Path-plumbing evidence
only — actual Ardour operation is qualified separately on the pinned runtime.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capabilities import describe
from studio import StudioServer
from studio_core import Generator
from test_studio import StudioFixture, wait_until

JSON = {"Content-Type": "application/json", "X-Riff-Request": "1"}

ABC = """X:1
T:Two voices
M:4/4
L:1/4
Q:1/4=120
K:C
V:Lead
V:Bass
V:Lead
C D E F |]
V:Bass
C,2 G,2 |]
"""

VOICE_MAP = {"voices": {"Lead": {"instrument": "lead", "program_raw": 80},
                        "Bass": {"instrument": "bass", "program_raw": 33}}}
TIMING = {"quarter_note_ticks": 480, "end_quarters": 8,
          "tempos": [{"bbt": "1|1|0", "quarters": 0, "bpm": 120}],
          "meters": [{"bbt": "1|1|0", "quarters": 0, "divisions_per_bar": 4}]}

TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Session version="3001" name="tpl" sample-rate="48000">
  <Sources>
    <Source name="MIDI 1-1.mid" type="midi" flags="Writable,CanRename" id="129" origin=""/>
  </Sources>
  <Locations>
    <Location name="session" start="0" end="1"/>
    <Location name="Loop" start="0" end="1"/>
    <Location name="Punch" start="0" end="1"/>
  </Locations>
  <Routes>
    <Route id="100" name="MIDI 1" default-type="midi" order-keys="editor=1:signal=1">
      <IO name="MIDI 1"><Port name="MIDI 1/in"/></IO>
      <Processor type="main-outs" name="MIDI 1" output="MIDI 1"/>
      <Diskstream name="MIDI 1" playlist="MIDI 1.1" id="101"/>
    </Route>
  </Routes>
  <Playlists>
    <Playlist id="102" name="MIDI 1.1" orig-track-id="100">
      <Region name="MIDI 1-1" position="0" length="1" length-beats="1.000000"
              start-beats="0" bbt-position="1|1|0" source-0="129" master-source-0="129" id="103"/>
    </Playlist>
  </Playlists>
  <TempoMap>
    <Tempo start="1|1|0" beats-per-minute="120.000000" note-type="4.000000" movable="no"/>
    <Meter start="1|1|0" note-type="4.000000" divisions-per-bar="4.000000" movable="no"/>
  </TempoMap>
  <Extra><UI/></Extra>
</Session>
"""

FAKE_LUA = r'''#!/usr/bin/env python3
# Fake ardour9-lua for Slice B tests: runs the REAL maintained Lua scripts
# (scripts/daw/*.lua) under a real liblua interpreter with a stub Ardour API
# whose session shape comes from the actual session file.  This is a host
# stub exercising the committed protocol, not a mirror of runner strings.
import ctypes, ctypes.util, sys
from pathlib import Path

STUB = r"""
local function readfile(p)
  local f = io.open(p); if not f then return nil end
  local t = f:read("a"); f:close(); return t
end

local function migrate(text)
  text = text:gsub('version="3001"', 'version="7004"')
  if not text:find('superclocks%-per%-second') then
    text = text:gsub('<TempoMap>(.-)</TempoMap>', function(inner)
      local out = {'  <TempoMap superclocks-per-second="282240000">', '    <Tempos>'}
      for t in inner:gmatch('<Tempo [^>]*/>') do out[#out+1] = '      ' .. t end
      out[#out+1] = '    </Tempos>'
      out[#out+1] = '    <Meters>'
      for m in inner:gmatch('<Meter [^>]*/>') do out[#out+1] = '      ' .. m end
      out[#out+1] = '    </Meters>'
      out[#out+1] = '    <MusicTimes/>'
      out[#out+1] = '  </TempoMap>'
      return table.concat(out, '\n')
    end)
  end
  text = text:gsub('start="(%d+)"', 'start="b%1"')
  text = text:gsub('end="(%d+)"', 'end="b%1"')
  text = text:gsub('length="(%d+)"', 'length="b%1@b0"')
  return text
end

local session = nil
local session_dir, session_name = nil, nil

local function new_track(name)
  return {
    name = function() return name end,
    tname = function() return name end,
    add_processor_by_index = function(p, i, a, b) return true end,
    solo_control = function() return { set_value = function() end } end,
  }
end

function load_session(dir, name)
  local text = readfile(dir .. "/" .. name .. ".ardour")
  if not text then return nil, "no session" end
  local tracks = {}
  for block in text:gmatch("<Route%s(.-)>") do
    if block:find('default%-type="midi"') then
      local n = block:match('name="([^"]+)"')
      if n then tracks[#tracks+1] = new_track(n) end
    end
  end
  session_dir, session_name = dir, name
  local list = { n = #tracks }
  list.iter = function()
    local i = 0
    return function() i = i + 1; if i <= #tracks then return tracks[i] end end
  end
  session = {
    name = function() return name end,
    get_tracks = function() return list end,
    save_state = function(_, out)
      local body = migrate(readfile(dir .. "/" .. name .. ".ardour"))
      local f = io.open(dir .. "/" .. out .. ".ardour", "w")
      f:write(body); f:close()
      return 0
    end,
  }
  return session
end

function close_session() end
function quit() end

ARDOUR = {
  LuaAPI = { new_plugin = function() return { isnil = function() return false end } end },
  PluginType = { LV2 = "LV2" },
}
PBD = { GroupControlDisposition = { NoGroup = "NoGroup" } }
"""


def main():
    library = ctypes.util.find_library("lua5.4") or ctypes.util.find_library("lua5.3")
    if not library:
        raise SystemExit("liblua5.4/5.3 is required for the script host stub")
    lua = ctypes.CDLL(library)
    lua.luaL_newstate.restype = ctypes.c_void_p
    for fn in ("luaL_openlibs", "lua_close"):
        getattr(lua, fn).restype = None
        getattr(lua, fn).argtypes = [ctypes.c_void_p]
    lua.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lua.luaL_loadstring.restype = ctypes.c_int
    lua.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_ssize_t, ctypes.c_void_p]
    lua.lua_pcallk.restype = ctypes.c_int
    lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    lua.lua_tolstring.restype = ctypes.c_char_p
    state = lua.luaL_newstate()
    lua.luaL_openlibs(state)
    for chunk in (STUB, Path(sys.argv[1]).read_text()):
        status = lua.luaL_loadstring(state, chunk.encode())
        if status == 0:
            status = lua.lua_pcallk(state, 0, -1, 0, 0, None)
        if status != 0:
            print(lua.lua_tolstring(state, -1, None).decode("utf-8", "replace"), file=sys.stderr)
            lua.lua_close(state)
            raise SystemExit(1)
    lua.lua_close(state)


main()
'''

FAKE_EXPORT = """#!/usr/bin/env python3
import array, sys, wave
target = sys.argv[sys.argv.index("-o") + 1]
with wave.open(target, "wb") as wav:
    wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
    wav.writeframes(array.array("h", [1000, -1000] * 4800).tobytes())
"""

FAKE_ABC2MIDI = """#!/usr/bin/env python3
import sys
out = sys.argv[sys.argv.index("-o") + 1]
def vlq(n):
    b = [n & 0x7f]; n >>= 7
    while n: b.append(0x80 | (n & 0x7f)); n >>= 7
    return bytes(reversed(b))
conductor = b"\\x00" + bytes([0xff, 0x51, 3]) + (500000).to_bytes(3, "big") + b"\\x00\\xff\\x2f\\x00"
notes = b"\\x00" + bytes([0x90, 60, 96]) + vlq(480) + bytes([0x80, 60, 0]) + b"\\x00\\xff\\x2f\\x00"
data = (b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + (2).to_bytes(2, "big")
        + (480).to_bytes(2, "big")
        + b"MTrk" + len(conductor).to_bytes(4, "big") + conductor
        + b"MTrk" + len(notes).to_bytes(4, "big") + notes)
open(out, "wb").write(data)
"""

FAKE_NEW_SESSION = "#!/bin/sh\nexit 0\n"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class DawFixture(StudioFixture):
    """A studio with a hermetic pinned-runtime record (hash-verified stubs)."""

    def setUp(self):
        super().setUp()
        bin_dir = self.root / "runtime"
        bin_dir.mkdir()
        (bin_dir / "ardour9-lua").write_text(FAKE_LUA)
        (bin_dir / "ardour9-export").write_text(FAKE_EXPORT)
        (bin_dir / "ardour9-new_session").write_text(FAKE_NEW_SESSION)
        abc2midi = bin_dir / "abc2midi"
        abc2midi.write_text(FAKE_ABC2MIDI)
        template = self.root / "template.ardour"
        template.write_text(TEMPLATE)
        for binary in bin_dir.iterdir():
            binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
        record = {"version": "9.8", "commit": "22ed8656c2533e325322ff11831448e5123e0d4b",
                  "kind": "source-build", "prefix": str(bin_dir),
                  "abc2midi": str(abc2midi), "template": str(template),
                  "instrument": {"uri": "http://gareus.org/oss/lv2/gmsynth",
                                 "sf2_sha256": "c278464b823daf9c52106c0957f752817da0e52964817ff682fe3a8d2f8446ce"},
                  "sha256": {name: _sha(bin_dir / name)
                             for name in ("ardour9-lua", "ardour9-export", "ardour9-new_session")},
                  "tools": {"abc2midi": _sha(abc2midi), "template": _sha(template)}}
        (self.store.data_root / "ardour-runtime.json").write_text(json.dumps(record))
        self.generator = Generator(self.store, self.command)
        self.server = StudioServer(("127.0.0.1", 0), self.store, self.generator)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def command(self, settings, output):  # daw jobs never reach the YuE2 command builder
        raise AssertionError("daw work must not route into the YuE2 command builder")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.generator.close()
        self.thread.join()
        super().tearDown()

    def post(self, name, payload, expect=None):
        connection = http.client.HTTPConnection(*self.server.server_address)
        connection.request("POST", f"/api/v1/operations/{name}", json.dumps(payload), JSON)
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        if expect is not None:
            self.assertEqual(response.status, expect, body)
        return response.status, body

    def compose(self, key="k1"):
        return self.post("daw.compose", {"title": "Two voices", "abc": ABC, "voice_map": VOICE_MAP,
                                         "timing": TIMING, "idempotency_key": key})


class CatalogTests(unittest.TestCase):
    def test_catalog_registers_both_operations_additively(self):
        surface = describe()["agent_surface"]
        self.assertEqual(surface["version"], 1)
        for name in ("daw.compose", "daw.render"):
            entry = surface["operations"][name]
            self.assertEqual(entry["method"], "POST")
            self.assertTrue(entry["idempotent"])
            self.assertEqual(entry["path"], f"/api/v1/operations/{name}")


class SubmitTests(DawFixture):
    def test_compose_queues_and_completes_on_the_pinned_runtime(self):
        status, body = self.compose()
        self.assertEqual(status, 201)
        self.assertFalse(body["replayed"])
        job_id = body["job_id"]
        wait_until(lambda: self.store.job(job_id)["status"] == "done")
        job = self.store.job(job_id)
        self.assertEqual(job["status"], "done")
        from operations import Operations
        receipt = Operations(self.store, self.generator).receipt(body["operation_id"])
        stages = receipt["stages"]
        self.assertEqual(stages["session"]["state"], "done")
        self.assertEqual(stages["instrument"]["state"], "done")
        self.assertEqual(stages["render"]["state"], "not_applicable")
        reference = receipt["outputs"]["arrangement_source"]
        self.assertTrue(reference.startswith("riff-arrangement-v1:"))
        self.assertNotIn(str(self.root), json.dumps(receipt))
        described = self.store.arrangements.describe(reference)
        self.assertEqual(described["composition"]["voice_map"], VOICE_MAP)

    def test_render_from_the_saved_arrangement(self):
        _, body = self.compose("k2")
        wait_until(lambda: self.store.job(body["job_id"])["status"] == "done")
        from operations import Operations
        reference = Operations(self.store, self.generator).receipt(body["operation_id"])["outputs"]["arrangement_source"]
        status, render = self.post("daw.render", {"arrangement_source": reference,
                                                  "idempotency_key": "r1"})
        self.assertEqual(status, 201)
        wait_until(lambda: self.store.job(render["job_id"])["status"] == "done")
        receipt = Operations(self.store, self.generator).receipt(render["operation_id"])
        self.assertEqual(receipt["stages"]["session"]["state"], "reused")
        self.assertEqual(receipt["stages"]["session"]["from"], reference)
        self.assertEqual(receipt["stages"]["render"]["state"], "done")
        self.assertEqual(receipt["stages"]["stems"]["state"], "done")
        self.assertEqual(len(receipt["outputs"]["stem_manifest"]), 2)
        self.assertEqual([s["track"] for s in receipt["outputs"]["stem_manifest"]],
                         ["Lead", "Bass"])
        self.assertTrue(receipt["outputs"]["track_id"])
        self.assertNotIn(str(self.root), json.dumps(receipt))
        # The public references path verifies the own kind.
        _, resolved = self.post("references.resolve", {"arrangement_source": reference})
        self.assertEqual(resolved["references"][0]["status"], "verified")
        self.assertEqual(resolved["references"][0]["kind"], "arrangement")

    def test_validation_errors_are_explicit(self):
        status, body = self.post("daw.compose", {"abc": ABC, "voice_map": VOICE_MAP, "timing": TIMING})
        self.assertEqual(status, 400)
        self.assertIn("title", body["error"])
        status, body = self.post("daw.compose", {"title": "X", "abc": ABC, "session_input": "riff-arrangement-v1:" + "0" * 64,
                                                 "voice_map": VOICE_MAP, "timing": TIMING})
        self.assertEqual(status, 400)
        self.assertIn("exactly one", body["error"])
        bad_map = {"voices": {"Lead": {"instrument": "lead", "program_raw": 128},
                              "Bass": {"instrument": "bass", "program_raw": 33}}}
        status, body = self.post("daw.compose", {"title": "X", "abc": ABC, "voice_map": bad_map, "timing": TIMING})
        self.assertEqual(status, 400)
        self.assertIn("program_raw", body["error"])

    def test_unsupported_selections_are_refused_before_queueing(self):
        status, body = self.post("daw.compose", {"title": "X", "abc": ABC, "voice_map": VOICE_MAP,
                                                 "timing": TIMING,
                                                 "instrument_map": {"default": {"plugin": "other", "bank": "other"}}})
        self.assertEqual(status, 400)
        self.assertIn("gmsynth", body["error"])
        _, body = self.compose("k3")
        wait_until(lambda: self.store.job(body["job_id"])["status"] == "done")
        from operations import Operations
        reference = Operations(self.store, self.generator).receipt(body["operation_id"])["outputs"]["arrangement_source"]
        status, body = self.post("daw.render", {"arrangement_source": reference, "format": "flac"})
        self.assertEqual(status, 400)
        self.assertIn("format", body["error"])

    def test_idempotency_replays_the_same_operation(self):
        _, first = self.compose("same-key")
        status, second = self.compose("same-key")
        self.assertEqual(status, 200)
        self.assertTrue(second["replayed"])
        self.assertEqual(first["operation_id"], second["operation_id"])
        wait_until(lambda: self.store.job(first["job_id"])["status"] == "done")

    def test_family_linkage(self):
        _, first = self.compose("fam1")
        family_id = first["family_id"]
        _, linked = self.post("daw.compose", {"title": "Two voices", "abc": ABC, "voice_map": VOICE_MAP,
                                              "timing": TIMING, "family_id": family_id,
                                              "idempotency_key": "fam2"})
        self.assertEqual(linked["family_id"], family_id)
        wait_until(lambda: self.store.job(first["job_id"])["status"] == "done")
        wait_until(lambda: self.store.job(linked["job_id"])["status"] == "done")


class UnavailableRuntimeTests(StudioFixture):
    """No runtime record: submission refuses, and a queued job fails truthfully."""

    def setUp(self):
        super().setUp()
        self.generator = Generator(self.store, self.command)
        self.server = StudioServer(("127.0.0.1", 0), self.store, self.generator)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def command(self, settings, output):
        raise AssertionError("daw work must not route into the YuE2 command builder")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.generator.close()
        self.thread.join()
        super().tearDown()

    def post(self, name, payload):
        connection = http.client.HTTPConnection(*self.server.server_address)
        connection.request("POST", f"/api/v1/operations/{name}", json.dumps(payload), JSON)
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    def test_submit_refuses_without_a_provisioned_runtime(self):
        status, body = self.post("daw.compose", {"title": "X", "abc": ABC, "voice_map": VOICE_MAP,
                                                 "timing": TIMING})
        self.assertEqual(status, 400)
        self.assertIn("No pinned Ardour runtime", body["error"])

    def test_a_queued_job_fails_truthfully_without_the_runtime(self):
        # Bypass the HTTP admission guard (e.g. runtime removed after submit):
        # the worker must still fail closed, with no artifacts and no track.
        recipe = {"title": "X", "daw": {"kind": "compose", "stages": {}, "abc": ABC,
                                        "voice_map": VOICE_MAP, "timing": TIMING}}
        queued = self.generator.enqueue(recipe)
        job_id = queued["id"]
        wait_until(lambda: self.store.job(job_id)["status"] == "failed")
        job = self.store.job(job_id)
        self.assertIn("No pinned Ardour runtime", job["error"])
        self.assertIsNone(job["track_id"])
        from operations import Operations
        receipt = Operations(self.store, self.generator).receipt(job_id)
        # Enqueued directly, without an operation record: the receipt is the
        # documented legacy shape, never a fabricated daw receipt.
        self.assertFalse(receipt["recorded"])
        self.assertIn("operation", receipt["unrecorded"])
        self.assertNotIn(str(self.root), json.dumps(receipt))

    def test_malformed_or_demo_or_unhashed_records_are_refused(self):
        import daw_runtime
        record_path = self.store.data_root / "ardour-runtime.json"
        # Malformed JSON
        record_path.write_text("not json")
        with self.assertRaises(daw_runtime.RuntimeUnavailable):
            daw_runtime.require(self.store)
        # Demo kind is never a production runtime, even hash-valid
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        (bin_dir / "tool").write_text("x")
        (bin_dir / "tool").chmod(0o755)
        template = self.root / "t.ardour"
        template.write_text("x")
        demo = {"version": "9.8", "commit": "22ed8656c2533e325322ff11831448e5123e0d4b",
                "kind": "demo", "prefix": str(bin_dir),
                "abc2midi": str(bin_dir / "tool"), "template": str(template),
                "instrument": {"uri": "http://gareus.org/oss/lv2/gmsynth",
                               "sf2_sha256": "c278464b823daf9c52106c0957f752817da0e52964817ff682fe3a8d2f8446ce"},
                "sha256": {n: _sha(bin_dir / "tool") for n in ("ardour9-lua", "ardour9-export", "ardour9-new_session")},
                "tools": {"abc2midi": _sha(bin_dir / "tool"), "template": _sha(template)}}
        record_path.write_text(json.dumps(demo))
        with self.assertRaises(daw_runtime.RuntimeUnavailable):
            daw_runtime.require(self.store)
        # Same record with a missing hash is refused, not verified
        del demo["sha256"]["ardour9-lua"]
        record_path.write_text(json.dumps(demo))
        with self.assertRaises(daw_runtime.RuntimeUnavailable):
            daw_runtime.require(self.store)


class CustodyTests(unittest.TestCase):
    """Bounded cancellation custody over this suite's own test processes."""

    def test_stubborn_child_group_is_killed_and_reports_cancelled(self):
        import daw_runner
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            stubborn = td / "stubborn.py"
            stubborn.write_text("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                                "time.sleep(120)\n")
            log = td / "child.log"
            cancelled = threading.Event()
            holder = {}
            outcome = {}

            def work():
                try:
                    daw_runner._run([sys.executable, str(stubborn)], os.environ.copy(), log,
                                    cancelled, attach=lambda p: holder.setdefault("p", p))
                except daw_runner.StageError as error:
                    outcome["stage"] = error.stage

            thread = threading.Thread(target=work)
            thread.start()
            deadline = time.monotonic() + 10
            while "p" not in holder and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIn("p", holder)
            process = holder["p"]
            cancelled.set()
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive(), "the run call must return after cancellation")
            self.assertEqual(outcome.get("stage"), "cancelled")
            self.assertIsNotNone(process.poll(), "the stubborn child must be dead")

    def test_cancellation_before_any_stage_publishes_nothing(self):
        import daw_runner
        import daw_runtime
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            from studio_core import Store
            store = Store(root / "data", root / "outputs")
            from arrangement_artifacts import ArrangementArtifacts
            store.arrangements = ArrangementArtifacts(store)
            cancelled = threading.Event()
            cancelled.set()
            recipe = {"title": "X", "daw": {"kind": "compose", "stages": {}, "abc": ABC,
                                            "voice_map": VOICE_MAP, "timing": TIMING,
                                            "session_name": "arrangement", "converter": "t"}}
            record = {"prefix": str(root)}  # never reached: cancellation precedes custody
            with self.assertRaises(daw_runner.StageError):
                daw_runner.compose(store, "a" * 32, recipe, record, cancelled)
            job_dir = store.data_root / "daw" / ("a" * 32)
            self.assertFalse((job_dir / "session").exists())


if __name__ == "__main__":
    unittest.main()
