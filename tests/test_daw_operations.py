"""Focused checks for the daw.compose / daw.render operation integration.

Runs against a real StudioServer with a hermetic fake Ardour runtime (stub
ardour9-lua / ardour9-export / abc2midi scripts): the shared queue, admission,
receipts and artifact recording are exercised end to end without Ardour, a
model, or a native build.  Path-plumbing evidence only — actual Ardour
operation is qualified separately on the pinned runtime.
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
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capabilities import describe
from operations import catalog
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

FAKE_LUA = r"""#!/usr/bin/env python3
import os, re, shutil, sys
from pathlib import Path
script = Path(sys.argv[1]).name
sdir = Path(os.environ["ARDOUR_PROBE_SESSION_DIR"])
snap_in = os.environ.get("ARDOUR_PROBE_SNAPSHOT_IN")
snap_out = os.environ.get("ARDOUR_PROBE_SNAPSHOT_OUT")
log = Path(os.environ["ARDOUR_PROBE_LOG"])

def upgrade(text):
    # Emulate the 3001 -> 7004 migration: b-coordinate locations/regions and a
    # superclock TempoMap, so patch_tempo_range.py sees the real new shape.
    text = text.replace('version="3001"', 'version="7004"')
    def block(match):
        inner = match.group(0)
        tempos = re.findall(r"<Tempo [^>]*/>", inner)
        meters = re.findall(r"<Meter [^>]*/>", inner)
        return ('\n  <TempoMap superclocks-per-second="282240000">\n    <Tempos>\n'
                + "".join("      " + t + "\n" for t in tempos)
                + '    </Tempos>\n    <Meters>\n'
                + "".join("      " + m + "\n" for m in meters)
                + '    </Meters>\n    <MusicTimes/>\n  </TempoMap>')
    text = re.sub(r"<TempoMap>.*?</TempoMap>", block, text, flags=re.S)
    text = re.sub(r'start="(\d+)"', lambda m: 'start="b' + m.group(1) + '"', text)
    text = re.sub(r'end="(\d+)"', lambda m: 'end="b' + m.group(1) + '"', text)
    text = re.sub(r'length="(\d+)"', lambda m: 'length="b' + m.group(1) + '@b0"', text)
    return text

if snap_out:
    text = upgrade((sdir / (snap_in + ".ardour")).read_text())
    (sdir / (snap_out + ".ardour")).write_text(text)
    log.write_text("save ok=True\n")
elif script == "save_stems.lua":
    for index in (1, 2):
        shutil.copyfile(sdir / (snap_in + ".ardour"), sdir / (f"stem-{index}.ardour"))
    log.write_text("stem 1 Lead save=True\nstem 2 Bass save=True\nsave ok=True\n")
else:
    log.write_text("ok\n")
"""

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
        record = {"version": "9.8", "commit": "059c95883d8158b03f632056ba36e5c7de91ef28",
                  "kind": "source-build", "prefix": str(bin_dir), "abc2midi": str(abc2midi),
                  "template": str(template),
                  "sha256": {name: _sha(bin_dir / name)
                             for name in ("ardour9-lua", "ardour9-export", "ardour9-new_session")}}
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
        status, render = self.post("daw.render", {"title": "Two voices", "arrangement_source": reference,
                                                  "idempotency_key": "r1"})
        self.assertEqual(status, 201)
        wait_until(lambda: self.store.job(render["job_id"])["status"] == "done")
        receipt = Operations(self.store, self.generator).receipt(render["operation_id"])
        self.assertEqual(receipt["stages"]["session"]["state"], "reused")
        self.assertEqual(receipt["stages"]["session"]["from"], reference)
        self.assertEqual(receipt["stages"]["render"]["state"], "done")
        self.assertEqual(receipt["stages"]["stems"]["state"], "done")
        self.assertEqual(len(receipt["outputs"]["stem_manifest"]), 2)
        self.assertTrue(receipt["outputs"]["track_id"])
        self.assertNotIn(str(self.root), json.dumps(receipt))

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

    def test_idempotency_replays_the_same_operation(self):
        _, first = self.compose("same-key")
        status, second = self.compose("same-key")
        self.assertEqual(status, 200)
        self.assertTrue(second["replayed"])
        self.assertEqual(first["operation_id"], second["operation_id"])
        wait_until(lambda: self.store.job(first["job_id"])["status"] == "done")

    def test_render_requires_an_existing_arrangement(self):
        status, body = self.post("daw.render", {"title": "X",
                                                "arrangement_source": "riff-arrangement-v1:" + "0" * 64})
        self.assertEqual(status, 400)
        self.assertIn("unavailable", body["error"])

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


if __name__ == "__main__":
    unittest.main()
