"""Build per-instrument Ardour sessions from ABC + voice map + timing manifest.

This is the generalized adapter core for the Ardour composition/render
workflow (#78 phase 2), extracted from the validated probe
``probes/ardour/build_session.py``.  Scope and invariants are the adapter
contract in ``docs/daw-integration/ardour-adapter-contract.md``:

- The timing manifest is explicit; nothing is inferred from ABC text.
- The project stores performance events (SMF), not notation.  Quoted chord
  symbols are stripped from the performance copy.  No lossless ABC claim.
- The voice map is authoritative for grouping, channels and programs.
- All admission checks run before any file side effect (``ContractError``).

The generated session keeps the 3001 template shape of the probe route; the
musical tempo/meter map and session/region extents are installed afterwards
by ``patch_tempo_range.py`` once the pinned runtime's first load/save has
upgraded the session (route A).  A native-7004 pinned clean template (route
B in the contract) removes that migration step; the session-shaping code here
is the same for both routes.
"""
import copy
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


class ContractError(ValueError):
    """The composition input violates the adapter contract; nothing was written."""


# -- SMF primitives -----------------------------------------------------------

def vlq(n):
    buf = [n & 0x7f]
    n >>= 7
    while n:
        buf.append(0x80 | (n & 0x7f))
        n >>= 7
    return bytes(reversed(buf))


def meta(typ, payload):
    return bytes([0xff, typ]) + vlq(len(payload)) + payload


def write_smf(path, events, division=480):
    """Write one format-1 single-track SMF from [(tick, bytes)] events."""
    events = sorted(events, key=lambda e: e[0])
    data = bytearray()
    last = 0
    for tick, msg in events:
        data += vlq(tick - last)
        last = tick
        data += msg
    data += vlq(0) + b'\xff\x2f\x00'
    Path(path).write_bytes(b'MThd' + (6).to_bytes(4, 'big') + (1).to_bytes(2, 'big')
                           + (1).to_bytes(2, 'big') + division.to_bytes(2, 'big')
                           + b'MTrk' + len(data).to_bytes(4, 'big') + data)


# -- ABC and voice map --------------------------------------------------------

def split_abc_voices(text):
    """Split a multi-voice ABC document at ``V:`` declarations.

    Returns (header, bodies): the shared header text and {voice: [lines]}.
    Same splitting rule as the validated probe: first ``V:`` block opens the
    declaration, a repeated ``V:`` starts that voice's body, and body lines
    continue until the next new ``V:``.
    """
    lines = text.splitlines()
    idx = next((i for i, l in enumerate(lines) if l.startswith('V:')), None)
    if idx is None:
        raise ContractError("ABC has no V: voice declarations.")
    header = '\n'.join(lines[:idx])
    seen, bodies, cur, mode = {}, {}, None, 'decl'
    for l in lines[idx:]:
        if l.startswith('V:'):
            name = l[2:].split()[0]
            if name not in seen:
                seen[name] = True
                cur = name
                mode = 'decl'
            else:
                bodies.setdefault(name, []).append(l)
                cur = name
                mode = 'body'
        elif mode == 'body':
            bodies[cur].append(l)
    for name in seen:
        if not bodies.get(name):
            raise ContractError(f"Voice {name} has no body lines.")
    return header, bodies


def strip_chords(line):
    """Quoted chord symbols are annotations; strip them from the performance copy."""
    return re.sub(r'"[^"]*"', '', line)


def group_tracks(voice_map):
    """Group voices by instrument: one track, one instrument instance per group.

    Track name: the group's longest common voice-ID prefix when >= 3 chars
    (PianoRH+PianoLH -> Piano), else the first voice ID.  Returns one entry per
    group: {track, voices, programs} with programs in voice order.
    """
    groups = {}
    for voice, spec in voice_map['voices'].items():
        program = spec.get('program_raw')
        if not isinstance(program, int) or not 0 <= program <= 127:
            raise ContractError(f"{voice}: program_raw must be an integer 0-127.")
        if not spec.get('instrument'):
            raise ContractError(f"{voice}: instrument must be non-empty.")
        g = groups.setdefault(spec['instrument'], {'voices': [], 'programs': []})
        g['voices'].append(voice)
        g['programs'].append(program)
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
    names = [g['track'] for g in out]
    if len(names) != len(set(names)):
        raise ContractError(f"Track names are not unique: {names}")
    return out


def check_voice_sets(bodies, voice_map):
    """ABC voices and map voices must match exactly."""
    abc_voices, map_voices = set(bodies), set(voice_map['voices'])
    if abc_voices != map_voices:
        raise ContractError(
            f"Voice sets differ: only in ABC {sorted(abc_voices - map_voices)}, "
            f"only in map {sorted(map_voices - abc_voices)}.")


# -- timing manifest ----------------------------------------------------------

def timing_to_meta(timing, division=480):
    """Derive the SMF tempo/meter meta events from the explicit manifest.

    The manifest is the authoritative timeline; these meta events are source
    metadata carried into the track SMF and cross-checked by
    ``verify_timeline.py``.  Quarter positions convert with the manifest's
    ``quarter_note_ticks``; meter denominators are quarter notes
    (dd = 2 in SMF time-signature encoding).
    """
    tpq = timing.get('quarter_note_ticks', division)
    if tpq != division:
        raise ContractError(f"quarter_note_ticks {tpq} != SMF division {division}; "
                            "resampling is out of contract.")
    tempos, meters = timing.get('tempos'), timing.get('meters')
    if not tempos or not meters:
        raise ContractError("timing must carry tempos and meters.")
    for kind, events in (('tempos', tempos), ('meters', meters)):
        quarters = [e['quarters'] for e in events]
        if quarters[0] != 0 or quarters != sorted(quarters) or len(set(quarters)) != len(quarters):
            raise ContractError(f"timing.{kind} must start at 0 and strictly increase.")
    end = timing.get('end_quarters')
    if not isinstance(end, (int, float)) or end <= 0:
        raise ContractError("timing.end_quarters must be positive.")
    if tempos[-1]['quarters'] > end or meters[-1]['quarters'] > end:
        raise ContractError("timing events must not start after end_quarters.")
    ev = []
    for t in tempos:
        us = round(60_000_000 / float(t['bpm']))
        ev.append((int(t['quarters'] * tpq), meta(0x51, us.to_bytes(3, 'big'))))
    for m in meters:
        ev.append((int(m['quarters'] * tpq),
                   meta(0x58, bytes([int(m['divisions_per_bar']), 2, 24, 8]))))
    return ev


def extent_samples(timing, sample_rate=48000):
    """3001-template region/loop extent placeholder, in samples.

    Derived by integrating the manifest's tempo map over ``end_quarters``.
    ``patch_tempo_range.py`` replaces this with the musical extent after the
    session upgrade, so this value never reaches a receipt on its own.
    """
    end = float(timing['end_quarters'])
    seconds = 0.0
    tempos = timing['tempos']
    for i, t in enumerate(tempos):
        start = float(t['quarters'])
        stop = float(tempos[i + 1]['quarters']) if i + 1 < len(tempos) else end
        seconds += (stop - start) * 60.0 / float(t['bpm'])
    return round(seconds * sample_rate)


# -- per-track conversion -----------------------------------------------------

def abc2midi_notes(split_abc, out_mid, abc2midi, summary_tool):
    """Convert one track's split ABC to note events with the pinned abc2midi.

    Returns [(start, dur, pitch, vel, ch)] with starts normalized from
    abc2midi's first-event tick 1 to tick 0, in abc2midi channel order.
    """
    tmp = Path(out_mid).with_suffix('.tmp.mid')
    r = subprocess.run([str(abc2midi), str(split_abc), '-o', str(tmp), '-NGUI'],
                       text=True, capture_output=True)
    if r.returncode:
        raise ContractError(f"abc2midi failed for {split_abc}: {r.stderr or r.stdout}")
    r = subprocess.run([sys.executable, str(summary_tool), str(tmp)], text=True,
                       capture_output=True)
    tmp.unlink(missing_ok=True)
    if r.returncode:
        raise ContractError(f"midi summary failed for {split_abc}: {r.stderr or r.stdout}")
    j = json.loads(r.stdout)
    notes = []
    for ti, t in enumerate(j['tracks']):
        if ti == 0:
            continue
        for n in t['notes']:
            notes.append((max(0, n['start'] - 1), n['dur'], n['pitch'], n['vel'], n['ch']))
    return notes


def build_track_smf(path, track, notes, timing, division=480):
    """Write one track's SMF: manifest meta, tick-0 program changes, notes.

    ``track`` is a group from ``group_tracks``; ``notes`` are in abc2midi
    channel order.  Channels are assigned to the group's programs in sorted
    channel order, one program change per channel at tick 0.
    """
    chans = sorted({n[4] for n in notes})
    if len(chans) != len(track['programs']):
        raise ContractError(
            f"{track['track']}: channels {chans} != programs {track['programs']}")
    if not notes:
        raise ContractError(f"{track['track']}: no performance events.")
    ev = timing_to_meta(timing, division)
    ev.append((0, meta(0x03, track['track'].encode())))
    for ch, p in zip(chans, track['programs']):
        ev.append((0, bytes([0xc0 | ch, p])))
    for start, dur, pitch, vel, ch in notes:
        ev.append((start, bytes([0x90 | ch, pitch, vel])))
        ev.append((start + dur, bytes([0x80 | ch, pitch, 0])))
    write_smf(path, ev, division)


# -- session shaping (3001 template, route A) ---------------------------------

def build_session(template, abc_path, voice_map, timing, outdir, session_name,
                  convert, sample_rate=48000):
    """Build the session directory: per-track SMF sources + shaped session XML.

    ``convert(split_abc, out_mid)`` supplies a track's note events (production:
    ``abc2midi_notes`` with the pinned binary; tests inject a fake).  Every
    contract check runs before any file is written.  Returns the session file
    path.
    """
    template, outdir = Path(template), Path(outdir)
    header, bodies = split_abc_voices(Path(abc_path).read_text())
    check_voice_sets(bodies, voice_map)
    groups = group_tracks(voice_map)
    timing_to_meta(timing)  # validate before side effects
    if not re.fullmatch(r'[A-Za-z0-9._-]+', session_name):
        raise ContractError(f"session name {session_name!r} is not safe.")

    outdir.mkdir(parents=True, exist_ok=True)
    mididir = outdir / 'interchange' / session_name / 'midifiles'
    mididir.mkdir(parents=True, exist_ok=True)
    for g in groups:
        split = outdir / f"{g['track']}.abc"
        text = [header]
        for v in g['voices']:
            text.append('V:' + v)
            text.extend(strip_chords(x) for x in bodies[v])
        split.write_text('\n'.join(text) + '\n')
        notes = convert(split, mididir / f"{g['track']}.mid")
        build_track_smf(mididir / f"{g['track']}.mid", g, notes, timing)

    tree = ET.parse(template)
    root = tree.getroot()
    root.set('name', session_name)
    root.set('sample-rate', str(sample_rate))
    sources = root.find('Sources')
    sources.clear()
    next_id, src_ids = 5000, {}
    for g in groups:
        sid = str(next_id)
        next_id += 1
        src_ids[g['track']] = sid
        ET.SubElement(sources, 'Source', {'name': g['track'] + '.mid', 'type': 'midi',
                      'flags': 'Writable,CanRename', 'id': sid, 'origin': ''})
    extent = str(extent_samples(timing, sample_rate))
    end_beats = f"{float(timing['end_quarters']):.6f}"
    for loc in root.find('Locations'):
        if loc.get('name') in ('session', 'Loop'):
            loc.set('start', '0')
            loc.set('end', extent)
        elif loc.get('name') == 'Punch':
            loc.set('start', '0')
            loc.set('end', '1')
    routes = root.find('Routes')
    midi_tpl = next((copy.deepcopy(r) for r in routes if r.get('default-type') == 'midi'), None)
    if midi_tpl is None:
        raise ContractError('template has no MIDI route')
    for r in list(routes):
        if r.get('default-type') == 'midi':
            routes.remove(r)
    base_playlists = ET.parse(template).getroot().find('Playlists')
    pl_tpl = copy.deepcopy(list(base_playlists)[0])
    playlists = root.find('Playlists')
    playlists.clear()
    for i, g in enumerate(groups, 1):
        r = copy.deepcopy(midi_tpl)
        for e in r.iter():
            if 'id' in e.attrib:
                e.set('id', str(next_id))
                next_id += 1
        rid = r.get('id')
        name = g['track']
        r.set('name', name)
        r.set('order-keys', f'editor={i}:signal={i}')
        for io in r.findall('IO'):
            io.set('name', name)
            for p in io.findall('Port'):
                p.set('name', name + '/' + p.get('name', '').split('/', 1)[-1])
        for proc in r.findall('Processor'):
            if proc.get('type') == 'main-outs':
                proc.set('name', name)
                proc.set('output', name)
        ds = r.find('Diskstream')
        if ds is not None:
            ds.set('name', name)
            ds.set('playlist', name + '.1')
        routes.append(r)
        pl = copy.deepcopy(pl_tpl)
        for e in pl.iter():
            if 'id' in e.attrib:
                e.set('id', str(next_id))
                next_id += 1
        pl.set('name', name + '.1')
        pl.set('orig-track-id', rid)
        reg = pl.find('Region')
        reg.set('name', name + '-1')
        reg.set('position', '0')
        reg.set('length', extent)
        reg.set('length-beats', end_beats)
        reg.set('start-beats', '0')
        reg.set('bbt-position', '1|1|0')
        reg.set('source-0', src_ids[name])
        reg.set('master-source-0', src_ids[name])
        playlists.append(pl)
    tm = root.find('TempoMap')
    tm.clear()
    for p in timing['tempos']:
        ET.SubElement(tm, 'Tempo', {'start': p['bbt'], 'beats-per-minute': f"{float(p['bpm']):.6f}",
                      'note-type': '4.000000', 'movable': 'no'})
    for p in timing['meters']:
        ET.SubElement(tm, 'Meter', {'start': p['bbt'], 'note-type': '4.000000',
                      'divisions-per-bar': f"{float(p['divisions_per_bar']):.6f}", 'movable': 'no'})
    ex = root.find('Extra')
    if ex is not None:
        for child in list(ex):
            ex.remove(child)
    session_file = outdir / f'{session_name}.ardour'
    tree.write(session_file, encoding='UTF-8', xml_declaration=True)
    return session_file
