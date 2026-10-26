#!/usr/bin/env python3
"""Focused path checks; requires Node and a Lua 5.3/5.4 shared library, not Ardour.

The Lua scripts run in a real interpreter with stub session/export objects. This
qualifies input plumbing, not Ardour, plugin loading, rendering or musical output.
"""
import ctypes
import ctypes.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
INPUTS = {
    "hi.lua": ["ARDOUR_PROBE_LOG"],
    "load.lua": ["ARDOUR_PROBE_LOG", "ARDOUR_PROBE_SESSION_DIR", "ARDOUR_PROBE_SNAPSHOT_IN"],
    "probe.lua": ["ARDOUR_PROBE_SESSION_DIR", "ARDOUR_PROBE_SNAPSHOT_IN"],
    "gmsynth.lua": ["ARDOUR_PROBE_LOG", "ARDOUR_PROBE_SESSION_DIR", "ARDOUR_PROBE_SNAPSHOT_IN"],
    "savegmsynth.lua": ["ARDOUR_PROBE_LOG", "ARDOUR_PROBE_SESSION_DIR",
                       "ARDOUR_PROBE_SNAPSHOT_IN", "ARDOUR_PROBE_SNAPSHOT_OUT"],
}
JAVASCRIPT = ["abc2js.js", "abcjs_midi_to_file.js"]


def quoted(value):
    return json.dumps(str(value), ensure_ascii=False)


def run_lua(source):
    library = ctypes.util.find_library("lua5.4") or ctypes.util.find_library("lua5.3")
    if not library:
        raise RuntimeError("Install a Lua 5.3/5.4 shared library to run these checks")
    lua = ctypes.CDLL(library)
    lua.luaL_newstate.restype = ctypes.c_void_p
    lua.luaL_openlibs.argtypes = [ctypes.c_void_p]
    lua.luaL_openlibs.restype = None
    lua.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lua.luaL_loadstring.restype = ctypes.c_int
    lua.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_ssize_t, ctypes.c_void_p]
    lua.lua_pcallk.restype = ctypes.c_int
    lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    lua.lua_tolstring.restype = ctypes.c_char_p
    lua.lua_close.argtypes = [ctypes.c_void_p]
    lua.lua_close.restype = None
    state = lua.luaL_newstate()
    if not state:
        raise RuntimeError("Cannot allocate Lua state")
    try:
        lua.luaL_openlibs(state)
        status = lua.luaL_loadstring(state, source.encode("utf-8"))
        if status == 0:
            status = lua.lua_pcallk(state, 0, -1, 0, 0, None)
        if status != 0:
            raise AssertionError(lua.lua_tolstring(state, -1, None).decode("utf-8", "replace"))
    finally:
        lua.lua_close(state)


def host(environment):
    values = ",".join("[" + quoted(key) + "]=" + quoted(value)
                      for key, value in environment.items())
    return "local inputs={" + values + "}\nos.getenv=function(name) return inputs[name] end\n" + """
local loads, closes, quits, saves, folders = {}, 0, 0, {}, {}
local session = {}
function session:name() return "stub" end
function session:get_tracks() return {iter=function() return function() return nil end end} end
function session:save_state(snapshot) saves[#saves+1] = snapshot or false; return true end
function session:simple_export()
  return {set_name=function() end, set_folder=function(_, folder) folders[#folders+1]=folder end,
          set_range=function() end, check_outputs=function() return true end,
          run_export=function() return true end}
end
function load_session(directory, snapshot)
  loads[#loads+1]={directory, snapshot}; return session
end
function close_session() closes=closes+1 end
function quit() quits=quits+1 end
"""


class PortabilityChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="riff probe paths ")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.environment = {
            "ARDOUR_PROBE_LOG": str(self.directory / "probe output.log"),
            "ARDOUR_PROBE_SESSION_DIR": str(self.directory / "session with spaces"),
            "ARDOUR_PROBE_SNAPSHOT_IN": "input snapshot",
            "ARDOUR_PROBE_SNAPSHOT_OUT": "saved snapshot",
        }

    def test_lua_paths_with_spaces_reach_host_unchanged(self):
        for name in INPUTS:
            with self.subTest(script=name):
                log = Path(self.environment["ARDOUR_PROBE_LOG"])
                log.unlink(missing_ok=True)
                checks = ""
                if name == "hi.lua":
                    checks += "assert(#loads == 0 and quits == 0)\n"
                else:
                    checks += "assert(#loads == 1 and closes == 1 and quits == 1)\n"
                    checks += "assert(loads[1][1] == inputs.ARDOUR_PROBE_SESSION_DIR)\n"
                    checks += "assert(loads[1][2] == inputs.ARDOUR_PROBE_SNAPSHOT_IN)\n"
                if name == "savegmsynth.lua":
                    checks += "assert(saves[1] == inputs.ARDOUR_PROBE_SNAPSHOT_OUT)\n"
                if name == "gmsynth.lua":
                    checks += "assert(saves[1] == false)\n"
                    checks += "assert(folders[1] == inputs.ARDOUR_PROBE_SESSION_DIR .. '/export')\n"
                run_lua(host(self.environment) + "dofile(" + quoted(ROOT / "probes/ardour" / name) + ")\n" + checks)
                if "ARDOUR_PROBE_LOG" in INPUTS[name]:
                    self.assertGreater(log.stat().st_size, 0)

    def test_lua_missing_or_empty_input_precedes_log_and_session_writes(self):
        for name, required in INPUTS.items():
            for key in required:
                for value in [None, ""]:
                    with self.subTest(script=name, missing=key, value=value):
                        environment = dict(self.environment)
                        if value is None:
                            del environment[key]
                        else:
                            environment[key] = value
                        run_lua(host(environment) + "local ok, err=pcall(dofile," + quoted(ROOT / "probes/ardour" / name) + ")\n"
                                + "assert(not ok and tostring(err):find(" + quoted("Set " + key) + ", 1, true))\n"
                                + "assert(#loads == 0 and #saves == 0 and #folders == 0)\n")
                        self.assertFalse(Path(self.environment["ARDOUR_PROBE_LOG"]).exists())

    def test_lua_log_open_failure_precedes_session_load(self):
        for name, required in INPUTS.items():
            if "ARDOUR_PROBE_LOG" not in required:
                continue
            with self.subTest(script=name):
                environment = dict(self.environment, ARDOUR_PROBE_LOG=str(self.directory / "missing parent" / "log"))
                run_lua(host(environment) + "local ok=pcall(dofile," + quoted(ROOT / "probes/ardour" / name) + ")\n"
                        + "assert(not ok and #loads == 0 and #saves == 0 and #folders == 0)\n")

    def test_abcjs_resolves_vendor_from_script_and_accepts_spaced_paths(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required")
        source = self.directory / "score input.abc"
        output = self.directory / "score output.mid"
        shutil.copyfile(ROOT / "probes/fixtures/arrangement-v1.abc", source)
        for script in JAVASCRIPT:
            with self.subTest(script=script):
                output.unlink(missing_ok=True)
                result = subprocess.run([node, str(ROOT / "probes/verify" / script), str(source), str(output)],
                                        cwd=self.directory, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(output.read_bytes().startswith(b"MThd"))

    def test_abcjs_missing_arguments_explain_usage_without_writing(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required")
        for script in JAVASCRIPT:
            for arguments in [[], ["missing input.abc"], ["a", "b", "extra"]]:
                with self.subTest(script=script, arguments=arguments):
                    result = subprocess.run([node, str(ROOT / "probes/verify" / script), *arguments],
                                            cwd=self.directory, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Usage:", result.stderr)
                    self.assertEqual(list(self.directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
