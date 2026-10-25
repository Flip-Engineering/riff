#!/usr/bin/env python3
"""Build a per-instrument Ardour MIDI session from ABC + voice map.

Scope: this is a probe adapter, not a general ABC importer.  The timing map is
explicit (arrangement-timing.json); note events come from abc2midi 5.07 and
are normalized from abc2midi's first-event tick 1 to tick 0.  The generated
session is the old 3001 template shape; `patch_tempo_range.py` applies the
new-format tempo map and musical session/region extents after the first
load/save upgrade.
"""
import argparse, copy, json, re, subprocess, sys, xml.etree.ElementTree as ET
from pathlib import Path

def vlq(n):
    buf = [n & 0x7f]; n >>= 7
    while n:
        buf.append(0x80 | (n & 0x7f)); n >>= 7
    return bytes(reversed(buf))

def meta(typ, payload):
    return bytes([0xff, typ]) + vlq(len(payload)) + payload

def write_smf(path, events, division=480):
    events = sorted(events, key=lambda e: e[0])
    data = bytearray(); last = 0
    for tick, msg in events:
        data += vlq(tick - last); last = tick; data += msg
    data += vlq(0) + b'\xff\x2f\x00'
    Path(path).write_bytes(b'MThd' + (6).to_bytes(4, 'big') + (1).to_bytes(2, 'big')
                           + (1).to_bytes(2, 'big') + division.to_bytes(2, 'big')
                           + b'MTrk' + len(data).to_bytes(4, 'big') + data)

def parse_abc(path):
    lines = Path(path).read_text().splitlines()
    idx = next(i for i, l in enumerate(lines) if l.startswith('V:'))
    header = '\n'.join(lines[:idx])
    seen, bodies, cur, mode = {}, {}, None, 'decl'
    for l in lines[idx:]:
        if l.startswith('V:'):
            name = l[2:].split()[0]
            if name not in seen:
                seen[name] = True; cur = name; mode = 'decl'
            else:
                bodies.setdefault(name, []).append(l); cur = name; mode = 'body'
        elif mode == 'body':
            bodies[cur].append(l)
    return header, bodies

def strip_chords(line):
    return re.sub(r'"[^"]*"', '', line)

def groups_from_map(voice_map):
    groups = {}
    for voice, spec in voice_map['voices'].items():
        inst = spec['instrument']
        g = groups.setdefault(inst, {'voices': [], 'programs': []})
        g['voices'].append(voice); g['programs'].append(int(spec['program_raw']))
    out = []
    for inst, g in groups.items():
        names = g['voices']
        prefix = names[0]
        if len(names) > 1:
            n = 0
            while n < min(map(len, names)) and len({x[n] for x in names}) == 1:
                n += 1
            if n >= 3:
                prefix = names[0][:n]
        out.append({'instrument': inst, 'track': prefix, 'voices': names,
                    'programs': g['programs']})
    return out

def summarize(summary_tool, midi):
    r = subprocess.run([sys.executable, str(summary_tool), str(midi)], text=True,
                       capture_output=True)
    if r.returncode:
        raise SystemExit(r.stderr or r.stdout)
    return json.loads(r.stdout)

def build_midi(name, programs, split_abc, out_mid, abc2midi, summary_tool):
    tmp = out_mid.with_suffix('.tmp.mid')
    r = subprocess.run([str(abc2midi), str(split_abc), '-o', str(tmp), '-NGUI'],
                       text=True, capture_output=True)
    if r.returncode:
        raise SystemExit(r.stderr or r.stdout)
    j = summarize(summary_tool, tmp)
    tmp.unlink(missing_ok=True)
    notes, chans = [], set()
    for ti, t in enumerate(j['tracks']):
        if ti == 0:
            continue
        for n in t['notes']:
            notes.append((max(0, n['start'] - 1), n['dur'], n['pitch'], n['vel'], n['ch']))
            chans.add(n['ch'])
    if len(chans) != len(programs):
        raise SystemExit(f'{name}: channels {sorted(chans)} != programs {programs}')
    chan_prog = dict(zip(sorted(chans), programs))
    ev = []
    # This tempo/meter map is metadata for the source; the session tempo map
    # is authoritative.  It matches arrangement-timing.json.
    for tick, us in [(0, 600000), (7680, 833333), (12960, 600000)]:
        ev.append((tick, meta(0x51, us.to_bytes(3, 'big'))))
    for tick, num, denpow in [(0, 4, 2), (11520, 3, 2), (12960, 4, 2)]:
        ev.append((tick, meta(0x58, bytes([num, denpow, 24, 8]))))
    ev.append((0, meta(0x03, name.encode())))
    for ch, p in chan_prog.items():
        ev.append((0, bytes([0xc0 | ch, p])))
    for start, dur, pitch, vel, ch in notes:
        ev.append((start, bytes([0x90 | ch, pitch, vel])))
        ev.append((start + dur, bytes([0x80 | ch, pitch, 0])))
    write_smf(out_mid, ev)

def build(template, abc, voice_map_path, outdir, session_name, abc2midi, summary_tool, timing):
    voice_map = json.loads(Path(voice_map_path).read_text())
    header, bodies = parse_abc(abc)
    groups = groups_from_map(voice_map)
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    mididir = outdir / 'interchange' / session_name / 'midifiles'
    mididir.mkdir(parents=True, exist_ok=True)
    for g in groups:
        split = outdir / f"{g['track']}.abc"
        text = [header]
        for v in g['voices']:
            text.append('V:' + v)
            text.extend(strip_chords(x) for x in bodies[v])
        split.write_text('\n'.join(text) + '\n')
        build_midi(g['track'], g['programs'], split, mididir / f"{g['track']}.mid",
                   abc2midi, summary_tool)
    tree = ET.parse(template); root = tree.getroot()
    root.set('name', session_name); root.set('sample-rate', '48000')
    sources = root.find('Sources'); sources.clear()
    next_id = 5000; src_ids = {}
    for g in groups:
        sid = str(next_id); next_id += 1; src_ids[g['track']] = sid
        ET.SubElement(sources, 'Source', {'name': g['track'] + '.mid', 'type': 'midi',
                      'flags': 'Writable,CanRename', 'id': sid, 'origin': ''})
    for loc in root.find('Locations'):
        if loc.get('name') in ('session', 'Loop'):
            loc.set('start', '0'); loc.set('end', '1016000')
        elif loc.get('name') == 'Punch':
            loc.set('start', '0'); loc.set('end', '1')
    routes = root.find('Routes')
    midi_tpl = next((copy.deepcopy(r) for r in routes if r.get('default-type') == 'midi'), None)
    if midi_tpl is None:
        raise SystemExit('template has no MIDI route')
    for r in list(routes):
        if r.get('default-type') == 'midi': routes.remove(r)
    base_playlists = ET.parse(template).getroot().find('Playlists')
    pl_tpl = copy.deepcopy(list(base_playlists)[0])
    playlists = root.find('Playlists'); playlists.clear()
    for i, g in enumerate(groups, 1):
        r = copy.deepcopy(midi_tpl)
        for e in r.iter():
            if 'id' in e.attrib:
                e.set('id', str(next_id)); next_id += 1
        rid = r.get('id'); name = g['track']
        r.set('name', name); r.set('order-keys', f'editor={i}:signal={i}')
        for io in r.findall('IO'):
            io.set('name', name)
            for p in io.findall('Port'):
                p.set('name', name + '/' + p.get('name', '').split('/', 1)[-1])
        for proc in r.findall('Processor'):
            if proc.get('type') == 'main-outs':
                proc.set('name', name); proc.set('output', name)
        ds = r.find('Diskstream')
        if ds is not None:
            ds.set('name', name); ds.set('playlist', name + '.1')
        routes.append(r)
        pl = copy.deepcopy(pl_tpl)
        for e in pl.iter():
            if 'id' in e.attrib:
                e.set('id', str(next_id)); next_id += 1
        pl.set('name', name + '.1'); pl.set('orig-track-id', rid)
        reg = pl.find('Region')
        reg.set('name', name + '-1'); reg.set('position', '0'); reg.set('length', '1016000')
        reg.set('length-beats', '31.000000'); reg.set('start-beats', '0')
        reg.set('bbt-position', '1|1|0'); reg.set('source-0', src_ids[name])
        reg.set('master-source-0', src_ids[name])
        playlists.append(pl)
    tm = root.find('TempoMap'); tm.clear()
    if timing:
        for p in timing['tempos']:
            ET.SubElement(tm, 'Tempo', {'start': p['bbt'], 'beats-per-minute': f"{p['bpm']:.6f}",
                          'note-type': '4.000000', 'movable': 'no'})
        for p in timing['meters']:
            ET.SubElement(tm, 'Meter', {'start': p['bbt'], 'note-type': '4.000000',
                          'divisions-per-bar': f"{p['divisions_per_bar']:.6f}", 'movable': 'no'})
    ex = root.find('Extra')
    if ex is not None:
        for child in list(ex): ex.remove(child)
    tree.write(outdir / f'{session_name}.ardour', encoding='UTF-8', xml_declaration=True)
    print(outdir / f'{session_name}.ardour')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--template', required=True)
    ap.add_argument('--abc', required=True)
    ap.add_argument('--voice-map', required=True)
    ap.add_argument('--timing', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--session-name', required=True)
    ap.add_argument('--abc2midi', required=True)
    ap.add_argument('--summary-tool', default='probes/verify/midi_summary.py')
    a = ap.parse_args()
    # Copy timing beside the output so nested calls can find it without
    # hard-coding the fixture path.
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    timing = json.loads(Path(a.timing).read_text())
    (out / 'arrangement-timing.json').write_text(Path(a.timing).read_text())
    build(a.template, a.abc, a.voice_map, out, a.session_name, a.abc2midi,
          Path(a.summary_tool), timing)

if __name__ == '__main__':
    main()
