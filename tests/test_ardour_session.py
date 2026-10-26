"""Focused checks for the Ardour session adapter core (ardour_session.py).

These qualify the adapter's contracts with the committed fixtures and a
synthetic 3001-shaped template.  They do not run Ardour, abc2midi, or a
render; per-track conversion is injected.  Actual Ardour operation is
qualified separately on the pinned runtime (see
docs/daw-integration/ardour-adapter-contract.md §5.4).
"""
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ardour_session as adapter

FIXTURES = ROOT / "probes" / "fixtures"
TIMING = json.loads((FIXTURES / "arrangement-timing.json").read_text())
VOICE_MAP = json.loads((FIXTURES / "voice-map.json").read_text())
ABC_TEXT = (FIXTURES / "arrangement-v1.abc").read_text()
SUMMARY_TOOL = ROOT / "probes" / "verify" / "midi_summary.py"

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

# The channels a real abc2midi run assigns per voice, in voice order; the
# injected converter returns one note per (channel, pitch) at tick 0.
FAKE_NOTES = {
    "Flute": [(0, 480, 72, 96, 0)],
    "Bass": [(0, 480, 40, 96, 0)],
    "Piano": [(0, 480, 60, 96, 0), (0, 480, 64, 96, 1)],
    # Flute's explicit-track alias in the named-map tests.
    "Woodwind": [(0, 480, 72, 96, 0)],
}


def fake_convert(split_abc, out_mid):
    track = Path(split_abc).stem
    return FAKE_NOTES[track]


class VoiceSplitting(unittest.TestCase):
    def test_fixture_splits_into_four_voices_with_shared_header(self):
        header, bodies = adapter.split_abc_voices(ABC_TEXT)
        self.assertEqual(sorted(bodies), ["Bass", "Flute", "PianoLH", "PianoRH"])
        for field in ("X:", "M:", "L:", "Q:", "K:"):
            self.assertIn(field, header)

    def test_voice_without_body_is_refused(self):
        with self.assertRaises(adapter.ContractError):
            adapter.split_abc_voices("X:1\nV:A\nV:A\nV:B\n")

    def test_chord_symbols_stripped_from_performance_copy(self):
        self.assertEqual(adapter.strip_chords('!p!"G"G2 "D7"D2'), '!p!G2 D2')


class Grouping(unittest.TestCase):
    def test_shared_instrument_collapses_to_one_track(self):
        groups = {g["track"]: g for g in adapter.group_tracks(VOICE_MAP)}
        self.assertEqual(sorted(groups), ["Bass", "Flute", "Piano"])
        self.assertEqual(groups["Piano"]["voices"], ["PianoRH", "PianoLH"])
        self.assertEqual(groups["Piano"]["programs"], [0, 0])
        self.assertEqual(groups["Flute"]["programs"], [73])
        self.assertEqual(groups["Bass"]["programs"], [33])

    def test_program_out_of_range_refused(self):
        bad = json.loads(json.dumps(VOICE_MAP))
        bad["voices"]["Flute"]["program_raw"] = 128
        with self.assertRaises(adapter.ContractError):
            adapter.group_tracks(bad)

    def test_duplicate_derived_track_names_refused(self):
        bad = {"voices": {"FluteA": {"instrument": "i", "program_raw": 0},
                          "FluteB": {"instrument": "i", "program_raw": 0},
                          "Flute": {"instrument": "j", "program_raw": 0}}}
        with self.assertRaises(adapter.ContractError):
            adapter.group_tracks(bad)


class TimingManifest(unittest.TestCase):
    def test_meta_events_reproduce_the_validated_probe_events(self):
        # The probe hard-coded exactly these events; derivation must agree.
        events = adapter.timing_to_meta(TIMING)
        tempos = [e for e in events if e[1][1] == 0x51]
        meters = [e for e in events if e[1][1] == 0x58]
        self.assertEqual([t[0] for t in tempos], [0, 7680, 12960])
        self.assertEqual([int.from_bytes(t[1][3:], "big") for t in tempos],
                         [600000, 833333, 600000])
        self.assertEqual([m[0] for m in meters], [0, 11520, 12960])
        self.assertEqual([m[1][3] for m in meters], [4, 3, 4])

    def test_unsorted_or_offset_events_refused(self):
        bad = json.loads(json.dumps(TIMING))
        bad["tempos"] = [bad["tempos"][1], bad["tempos"][0]] + bad["tempos"][2:]
        with self.assertRaises(adapter.ContractError):
            adapter.timing_to_meta(bad)
        bad = json.loads(json.dumps(TIMING))
        bad["tempos"][0]["quarters"] = 1
        with self.assertRaises(adapter.ContractError):
            adapter.timing_to_meta(bad)

    def test_extent_integrates_the_tempo_map(self):
        # 16 quarters @100 + 11 @72 (the bar-7 3/4 span) + 4 @100
        # = 9.6 + 9.1667 + 2.4 = 21.1667 s = 1016000 samples at 48 kHz,
        # matching the validated probe's placeholder.
        # patch_tempo_range.py installs the musical extent after the 7004
        # upgrade; this placeholder only has to span the content.
        self.assertEqual(adapter.extent_samples(TIMING), 1016000)


class TrackSmf(unittest.TestCase):
    def test_smf_roundtrip_preserves_programs_and_notes(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "Piano.mid"
            group = {"track": "Piano", "voices": ["PianoRH", "PianoLH"], "programs": [0, 0]}
            adapter.build_track_smf(out, group, FAKE_NOTES["Piano"], TIMING)
            r = subprocess.run([sys.executable, str(SUMMARY_TOOL), str(out)],
                               text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            j = json.loads(r.stdout)
        track = j["tracks"][0]
        self.assertEqual(track["name"], "Piano")
        self.assertEqual(track["channels"], [0, 1])
        self.assertEqual(track["programs"], [{"tick": 0, "ch": 0, "program": 0},
                                             {"tick": 0, "ch": 1, "program": 0}])
        notes = [(n["start"], n["dur"], n["pitch"], n["ch"]) for n in track["notes"]]
        self.assertEqual(sorted(notes), [(0, 480, 60, 0), (0, 480, 64, 1)])
        self.assertEqual([t["tick"] for t in track["tempos"]], [0, 7680, 12960])
        self.assertEqual([(t["tick"], t["num"]) for t in track["time_sigs"]],
                         [(0, 4), (11520, 3), (12960, 4)])

    def test_channel_count_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as td:
            group = {"track": "Flute", "voices": ["Flute"], "programs": [73]}
            with self.assertRaises(adapter.ContractError):
                adapter.build_track_smf(Path(td) / "f.mid", group,
                                        [(0, 480, 72, 96, 0), (0, 480, 74, 96, 1)], TIMING)
            self.assertFalse((Path(td) / "f.mid").exists())

    def test_empty_track_refused(self):
        with tempfile.TemporaryDirectory() as td:
            group = {"track": "Flute", "voices": ["Flute"], "programs": [73]}
            with self.assertRaises(adapter.ContractError):
                adapter.build_track_smf(Path(td) / "f.mid", group, [], TIMING)


class SessionBuild(unittest.TestCase):
    def build(self, td, voice_map=VOICE_MAP, timing=TIMING, abc_text=ABC_TEXT):
        td = Path(td)
        template = td / "template.ardour"
        template.write_text(TEMPLATE)
        abc = td / "input.abc"
        abc.write_text(abc_text)
        return adapter.build_session(template, abc, voice_map, timing,
                                     td / "out", "arrangement-v1", fake_convert)

    def test_build_shapes_three_tracks_from_the_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            session_file = self.build(td)
            root = ET.parse(session_file).getroot()
            out = Path(td) / "out"
            sources = root.find("Sources")
            self.assertEqual([s.get("name") for s in sources],
                             ["Flute.mid", "Bass.mid", "Piano.mid"])
            routes = root.find("Routes")
            names = [r.get("name") for r in routes if r.get("default-type") == "midi"]
            self.assertEqual(names, ["Flute", "Bass", "Piano"])
            playlists = root.find("Playlists")
            self.assertEqual([p.get("name") for p in playlists],
                             ["Flute.1", "Bass.1", "Piano.1"])
            ids = [e.get("id") for e in root.iter() if "id" in e.attrib]
            self.assertEqual(len(ids), len(set(ids)), "ids must be unique")
            src_by_name = {s.get("name"): s.get("id") for s in sources}
            for p in playlists:
                region = p.find("Region")
                track = p.get("name")[:-2]
                self.assertEqual(region.get("source-0"), src_by_name[track + ".mid"])
                self.assertEqual(region.get("length-beats"), "31.000000")
                self.assertEqual(region.get("length"), "1016000")
            tempos = [(t.get("start"), t.get("beats-per-minute")) for t in root.find("TempoMap") if t.tag == "Tempo"]
            self.assertEqual(tempos, [("1|1|0", "100.000000"), ("5|1|0", "72.000000"),
                                      ("8|1|0", "100.000000")])
            meters = [(m.get("start"), m.get("divisions-per-bar")) for m in root.find("TempoMap") if m.tag == "Meter"]
            self.assertEqual(meters, [("1|1|0", "4.000000"), ("7|1|0", "3.000000"),
                                      ("8|1|0", "4.000000")])
            self.assertEqual(root.get("name"), "arrangement-v1")
            self.assertEqual(root.get("sample-rate"), "48000")
            for track in ("Flute", "Bass", "Piano"):
                self.assertTrue((out / "interchange" / "arrangement-v1" / "midifiles" / f"{track}.mid").exists())
                self.assertTrue((out / f"{track}.abc").exists())

    def test_contract_violations_precede_all_writes(self):
        with tempfile.TemporaryDirectory() as td:
            bad_map = json.loads(json.dumps(VOICE_MAP))
            del bad_map["voices"]["Bass"]
            with self.assertRaises(adapter.ContractError):
                self.build(td, voice_map=bad_map)
            self.assertFalse((Path(td) / "out").exists(), "no session dir after refusal")
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(adapter.ContractError):
                self.build(td, timing=json.loads(json.dumps(TIMING)) | {"end_quarters": 0})
            self.assertFalse((Path(td) / "out").exists())

    def test_template_without_midi_route_refused(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            template = td / "template.ardour"
            template.write_text(TEMPLATE.replace('default-type="midi"', 'default-type="audio"'))
            abc = td / "input.abc"
            abc.write_text(ABC_TEXT)
            with self.assertRaises(adapter.ContractError):
                adapter.build_session(template, abc, VOICE_MAP, TIMING,
                                      td / "out", "arrangement-v1", fake_convert)
            # Root's Slice A review reproduction: the refusal must precede any
            # write; no split ABC or MIDI files may exist.
            self.assertFalse((td / "out").exists(), "refusal must precede any output")

    def test_unsupported_template_version_refused_before_writes(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            template = td / "template.ardour"
            template.write_text(TEMPLATE.replace('version="3001"', 'version="7004"'))
            abc = td / "input.abc"
            abc.write_text(ABC_TEXT)
            with self.assertRaises(adapter.ContractError):
                adapter.build_session(template, abc, VOICE_MAP, TIMING,
                                      td / "out", "arrangement-v1", fake_convert)
            self.assertFalse((td / "out").exists())

    def test_second_track_failure_publishes_nothing(self):
        calls = []

        def failing_convert(split_abc, out_mid):
            track = Path(split_abc).stem
            calls.append(track)
            if track == "Bass":
                raise adapter.ContractError("converter failed on Bass")
            return FAKE_NOTES[track]

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            template = td / "template.ardour"
            template.write_text(TEMPLATE)
            abc = td / "input.abc"
            abc.write_text(ABC_TEXT)
            with self.assertRaises(adapter.ContractError):
                adapter.build_session(template, abc, VOICE_MAP, TIMING,
                                      td / "out", "arrangement-v1", failing_convert)
            self.assertEqual(calls[0], "Flute")
            self.assertFalse((td / "out").exists(),
                             "a later-track failure must not publish a partial session")

    def test_existing_output_is_preserved_and_refused(self):
        with tempfile.TemporaryDirectory() as td:
            first = self.build(td)
            before = first.read_bytes()
            with self.assertRaises(adapter.ContractError):
                self.build(td)
            self.assertEqual(first.read_bytes(), before)

    def test_explicit_track_names_honored_and_conflicts_refused(self):
        named = json.loads(json.dumps(VOICE_MAP))
        named["voices"]["Flute"]["track"] = "Woodwind"
        groups = {g["track"]: g for g in adapter.group_tracks(named)}
        self.assertIn("Woodwind", groups)
        self.assertNotIn("Flute", groups)
        with tempfile.TemporaryDirectory() as td:
            session_file = self.build(td, voice_map=named)
            root = ET.parse(session_file).getroot()
            self.assertIn("Woodwind", [r.get("name") for r in root.find("Routes")])
        conflict = json.loads(json.dumps(VOICE_MAP))
        conflict["voices"]["PianoRH"]["track"] = "Keys"
        conflict["voices"]["PianoLH"]["track"] = "Piano"
        with self.assertRaises(adapter.ContractError):
            adapter.group_tracks(conflict)

    def test_traversal_and_dot_names_refused(self):
        for bad_name in ("../escape", "..", ".", "a/b", "a\\b"):
            bad = json.loads(json.dumps(VOICE_MAP))
            bad["voices"][bad_name] = bad["voices"].pop("Flute")
            with self.assertRaises(adapter.ContractError, msg=bad_name):
                adapter.group_tracks(bad)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(adapter.ContractError):
                self._build_named(td, "..")

    def _build_named(self, td, session_name):
        td = Path(td)
        template = td / "template.ardour"
        template.write_text(TEMPLATE)
        abc = td / "input.abc"
        abc.write_text(ABC_TEXT)
        return adapter.build_session(template, abc, VOICE_MAP, TIMING,
                                     td / "out", session_name, fake_convert)

    def test_note_bounds_and_midi_ranges_enforced(self):
        group = {"track": "Flute", "voices": ["Flute"], "programs": [73]}
        cases = [
            (-1, 480, 72, 96, 0),      # negative start
            (0, 0, 72, 96, 0),         # zero duration
            (0, 480.5, 72, 96, 0),     # non-integral duration
            (0, 480, 128, 96, 0),      # pitch > 127
            (0, 480, 72, 0, 0),        # velocity 0
            (0, 480, 72, 96, 16),      # channel > 15
            (14880, 480, 72, 96, 0),   # ends past the declared end
        ]
        for note in cases:
            with self.subTest(note=note), tempfile.TemporaryDirectory() as td:
                out = Path(td) / "f.mid"
                with self.assertRaises(adapter.ContractError):
                    adapter.build_track_smf(out, group, [note], TIMING)
                self.assertFalse(out.exists())

    def test_vlq_rejects_negative_input(self):
        with self.assertRaises(adapter.ContractError):
            adapter.vlq(-1)


if __name__ == "__main__":
    unittest.main()
