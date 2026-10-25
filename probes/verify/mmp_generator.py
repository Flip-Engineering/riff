#!/usr/bin/env python3
"""Generate an LMMS .mmp project from per-voice SMFs + voice-map.json (#78).

Probe tool for the LMMS-first selection: converts abc2midi per-voice SMFs
into a v1.3-schema .mmp with contrasting built-in instruments, a discrete
tempo automation map (LMMS AutomationTrack -> Song tempo model), and a
global 4/4 meter (LMMS Song::setTimeSignature is project-global; per-segment
meter maps are not representable — evidence in docs/daw-integration/
daw-candidates.md). Emits notes at round(SMF_tick / 10) LMMS ticks
(480 tpq SMF -> 48 ticks/beat at 4/4).

Author: riff-daw-glm-20261025. Dependency-free (uses midi_summary.parse).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from midi_summary import parse  # noqa: E402

# Voice-name -> instrument settings. Three timbre engines, all bundled in
# LMMS (license-clean, deterministic XML): two TripleOscillator variants and
# an OPL2 FM voice for the shared keyboard instrument.
INSTRUMENTS = {
    "flute": {
        "plugin": "tripleoscillator",
        "basenote": 69,
        "attrs": {"wavetype0": "0", "wavetype1": "0", "wavetype2": "0",
                  "coarse0": "0", "coarse1": "0", "coarse2": "12",
                  "vol0": "200", "vol1": "100", "vol2": "50",
                  "modalgo1": "2", "modalgo2": "1", "modalgo3": "2"},
    },
    "acoustic_bass": {
        "plugin": "tripleoscillator",
        "basenote": 45,
        "attrs": {"wavetype0": "2", "wavetype1": "2", "wavetype2": "2",
                  "coarse0": "-12", "coarse1": "-24", "coarse2": "0",
                  "vol0": "200", "vol1": "120", "vol2": "60",
                  "modalgo1": "2", "modalgo2": "1", "modalgo3": "2"},
    },
    "acoustic_grand": {
        "plugin": "OPL2",  # OpulenZ-family FM engine; shared by PianoRH/LH
        "basenote": 60,
        "attrs": {"op1_waveform": "0", "op2_waveform": "1", "fm": "1",
                  "op1_mul": "1", "op2_mul": "2", "op1_lvl": "40",
                  "op2_lvl": "50", "feedback": "3", "op1_a": "8",
                  "op1_d": "3", "op1_s": "13", "op1_r": "15",
                  "op2_a": "9", "op2_d": "0", "op2_s": "15", "op2_r": "3"},
    },
}

TICK_DIVISOR = 10  # SMF 480 tpq -> LMMS 48 ticks/beat (4/4)
PATTERN_LEN = 1536  # 8 bars x 192 LMMS ticks (song end at bar 8)


def voice_notes(smf_path):
    """Notes of the voice track (channel 0) as (pos, len, key) LMMS ticks."""
    m = parse(smf_path)
    notes = []
    for trk in m["tracks"]:
        for n in trk.get("notes", []):
            if n.get("ch", 0) == 0:
                pos = round(n["start"] / TICK_DIVISOR)
                length = max(1, round(n["dur"] / TICK_DIVISOR))
                notes.append((pos, length, n["pitch"]))
    notes.sort()
    return notes, m


def tempo_map(smf_path):
    """[(lmms_tick, bpm)] from the SMF conductor track."""
    m = parse(smf_path)
    pts = [(round(t["tick"] / TICK_DIVISOR), t["bpm"])
           for t in m["tracks"][0].get("tempos", [])]
    return sorted(pts)


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def instrument_xml(inst_name):
    inst = INSTRUMENTS[inst_name]
    attrs = " ".join(f'{k}="{v}"' for k, v in inst["attrs"].items())
    return (f'          <instrument name="{inst["plugin"]}">\n'
            f'            <{inst["plugin"]} {attrs}/>\n'
            f'          </instrument>\n'
            f'          <eldata fres="0.5" ftype="0" fcut="14000" fwet="0"/>\n'
            f'          <chordcreator chord="0" chordrange="1" chord-enabled="0"/>\n'
            f'          <arpeggiator arptime="100" arprange="1" arptime_denominator="4"'
            f' syncmode="0" arpmode="0" arp-enabled="0" arp="0" arptime_numerator="4"'
            f' arpdir="0" arpgate="100"/>\n'
            f'          <midiport inputcontroller="0" fixedoutputvelocity="-1"'
            f' inputchannel="0" outputcontroller="0" writable="0" outputchannel="1"'
            f' fixedinputvelocity="-1" fixedoutputnote="-1" outputprogram="1"'
            f' basevelocity="63" readable="0"/>\n'
            f'          <fxchain numofeffects="0" enabled="0"/>')


def build(voices, tempo, out_path, mastervol=65, title="riff arrangement probe"):
    # voices: list of (track_name, inst_key, notes)
    tracks = []
    for i, (name, inst_key, notes) in enumerate(voices, start=1):
        inst = INSTRUMENTS[inst_key]
        note_xml = "\n".join(
            f'            <note key="{k - 48}" pos="{p}" len="{l}" vol="100"/>'
            for p, l, k in notes)
        tracks.append(f'''      <track muted="0" name="{esc(name)}" solo="0" type="0">
        <instrumenttrack pan="0" pitchrange="1" pitch="0" basenote="{inst['basenote']}" vol="100">
{instrument_xml(inst_key)}
        </instrumenttrack>
        <pattern name="{esc(name)}" pos="0" len="{PATTERN_LEN}" steps="192" muted="0">
{note_xml}
        </pattern>
      </track>''')

    tempo_pts = "\n".join(
        f'          <time pos="{p}" value="{v}" outValue="{v}" inTan="0"'
        f' outTan="0" lockedTan="0"/>' for p, v in tempo)
    bpm_id = 9001
    tracks.append(f'''      <track muted="0" name="Tempo map" solo="0" type="5">
        <automationtrack/>
        <automationpattern pos="0" len="{PATTERN_LEN}" name="Tempo" prog="0" tens="0" mute="0" off="0" autoresize="1">
          <object id="{bpm_id}"/>
{tempo_pts}
        </automationpattern>
      </track>''')

    xml = f'''<?xml version="1.0"?>
<!DOCTYPE lmms-project>
<lmms-project creator="mmp_generator" version="1.0" type="song" creatorversion="1.3.0-alpha.2">
  <head timesig_numerator="4" timesig_denominator="4" mastervol="{mastervol}" masterpitch="0">
    <bpm id="{bpm_id}" value="{tempo[0][1]}" scale_type="linear"/>
  </head>
  <song>
    <trackcontainer x="5" y="5" w="700" h="400" visible="1" type="song">
{chr(10).join(tracks)}
    </trackcontainer>
  </song>
</lmms-project>
'''
    return xml


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map", required=True, help="voice-map.json (instrument key resolution)")
    ap.add_argument("--voice", action="append", required=True,
                    help="TrackName=instrument_key=path.mid (repeat per voice)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mastervol", type=int, default=65)
    ap.add_argument("--title", default="riff arrangement probe")
    a = ap.parse_args()

    voices = []
    tempo = None
    for spec in a.voice:
        name, inst_key, path = spec.split("=", 2)
        notes, m = voice_notes(path)
        if tempo is None:
            tempo = tempo_map(path)
        voices.append((name, inst_key, notes))

    if a.out:
        out = build(voices, tempo, a.out, a.mastervol, a.title)
        print(f"wrote {out}: {len(voices)} voice tracks, tempo points {tempo}")


if __name__ == "__main__":
    main()
