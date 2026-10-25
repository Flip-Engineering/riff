#!/usr/bin/env python3
"""Verify generated MIDI events and session timing against abc2midi + timing."""
import argparse, json, subprocess, sys, tempfile, xml.etree.ElementTree as ET
from pathlib import Path

def summarize(tool, midi):
    r = subprocess.run([sys.executable, str(tool), str(midi)], text=True, capture_output=True)
    if r.returncode:
        raise SystemExit(r.stderr or r.stdout)
    return json.loads(r.stdout)

def voice_order(abc):
    lines = Path(abc).read_text().splitlines()
    idx = next(i for i, l in enumerate(lines) if l.startswith('V:'))
    out, seen = [], set()
    for l in lines[idx:]:
        if l.startswith('V:'):
            name = l[2:].split()[0]
            if name not in seen:
                seen.add(name); out.append(name)
    return out

def group_map(voice_map, order):
    groups = {}
    for voice, spec in voice_map['voices'].items():
        g = groups.setdefault(spec['instrument'], {'voices': [], 'programs': []})
        g['voices'].append(voice); g['programs'].append(int(spec['program_raw']))
    out = []
    for inst, g in groups.items():
        names = g['voices']; track = names[0]
        if len(names) > 1:
            n = 0
            while n < min(map(len, names)) and len({x[n] for x in names}) == 1:
                n += 1
            if n >= 3: track = names[0][:n]
        out.append({'instrument': inst, 'track': track, 'voices': names,
                    'programs': g['programs'], 'track_indexes': [1 + order.index(v) for v in names]})
    return out

def notes_of(summary, indexes=None):
    out = []
    for i, t in enumerate(summary['tracks']):
        if indexes is not None and i not in indexes:
            continue
        for n in t['notes']:
            out.append((n['start'], n['dur'], n['pitch'], n['vel']))
    return sorted(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--abc', required=True)
    ap.add_argument('--voice-map', required=True)
    ap.add_argument('--timing', required=True)
    ap.add_argument('--midi-dir', required=True)
    ap.add_argument('--session-xml', required=True)
    ap.add_argument('--abc2midi', required=True)
    ap.add_argument('--summary-tool', default='probes/verify/midi_summary.py')
    ap.add_argument('--session-midi-dir')
    a = ap.parse_args()
    timing = json.loads(Path(a.timing).read_text())
    voicemap = json.loads(Path(a.voice_map).read_text())
    order = voice_order(a.abc)
    groups = group_map(voicemap, order)
    with tempfile.TemporaryDirectory() as td:
        full = Path(td) / 'full.mid'
        r = subprocess.run([a.abc2midi, a.abc, '-o', str(full), '-NGUI'], text=True,
                           capture_output=True)
        if r.returncode:
            raise SystemExit(r.stderr or r.stdout)
        full_summary = summarize(a.summary_tool, full)
    report = {'midi': {}, 'session': {}, 'pass': True}
    for g in groups:
        expected = notes_of(full_summary, g['track_indexes'])
        clean = summarize(a.summary_tool, Path(a.midi_dir) / (g['track'] + '.mid'))
        generated = notes_of(clean)
        # abc2midi's first event sits at tick 1; the generator normalizes it
        # to tick 0, so adding one tick back must reproduce abc2midi exactly.
        normalized = sorted((s + 1, d, p, v) for s, d, p, v in generated)
        ok = normalized == expected
        progs = sorted({(x['ch'], x['program']) for t in clean['tracks'] for x in t['programs']})
        exp_progs = sorted(set(zip(sorted({x['ch'] for t in clean['tracks'] for x in t['notes']}),
                                   g['programs'])))
        report['midi'][g['track']] = {
            'expected_notes': len(expected), 'generated_notes': len(generated),
            'notes_equal_after_minus_one_tick_norm': ok,
            'normalization': 'abc2midi start tick 1 -> generated tick 0; expected = generated_start + 1',
            'program_events': progs, 'expected_programs': exp_progs,
            'program_ok': progs == exp_progs,
        }
        if not ok or progs != exp_progs:
            report['pass'] = False
        if a.session_midi_dir:
            rewritten = summarize(a.summary_tool, Path(a.session_midi_dir) / (g['track'] + '.mid'))
            report['midi'][g['track']]['notes_survive_session_save'] = notes_of(rewritten) == generated
            if not report['midi'][g['track']]['notes_survive_session_save']:
                report['pass'] = False
    root = ET.parse(a.session_xml).getroot()
    routes = [r for r in root.find('Routes') if r.get('default-type') == 'midi']
    names = [r.get('name') for r in routes]
    expected_names = [g['track'] for g in groups]
    gm = {r.get('name'): sum(1 for p in r.iter('Processor')
          if p.get('type') == 'lv2' and p.get('unique-id') == 'http://gareus.org/oss/lv2/gmsynth')
          for r in routes}
    tm = root.find('TempoMap')
    tempos = [(x.get('bbt'), x.get('quarters'), x.get('npm')) for x in tm.findall('./Tempos/Tempo')]
    meters = [(x.get('bbt'), x.get('quarters'), x.get('divisions-per-bar')) for x in tm.findall('./Meters/Meter')]
    exp_tempos = [(str(p['bbt']), f"{p['quarters']}:0", str(p['bpm'])) for p in timing['tempos']]
    exp_meters = [(str(p['bbt']), f"{p['quarters']}:0", str(p['divisions_per_bar'])) for p in timing['meters']]
    loc = {x.get('name'): (x.get('start'), x.get('end')) for x in root.find('Locations')}
    regions = [(r.get('length'), r.get('start')) for r in root.iter('Region')]
    session_ok = (names == expected_names and all(v == 1 for v in gm.values())
                  and tempos == exp_tempos and meters == exp_meters
                  and loc.get('session') == ('b0', 'b%d' % (timing['end_quarters'] * 1920))
                  and all(x == ('b%d@b0' % (timing['end_quarters'] * 1920), 'b0') for x in regions))
    report['session'] = {
        'midi_routes': names, 'expected_routes': expected_names,
        'gmsynth_per_route': gm,
        'tempos': tempos, 'expected_tempos': exp_tempos,
        'meters': meters, 'expected_meters': exp_meters,
        'session_range': loc.get('session'), 'expected_session_range': ('b0', 'b%d' % (timing['end_quarters'] * 1920)),
        'region_lengths': regions, 'structure_ok': session_ok,
    }
    report['pass'] = report['pass'] and session_ok
    print(json.dumps(report, indent=1))

if __name__ == '__main__':
    main()
