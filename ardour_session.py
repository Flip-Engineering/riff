"""Build per-instrument Ardour sessions from ABC + voice map + timing manifest.

This is the generalized adapter core for the Ardour composition/render
workflow (#78 phase 2), extracted from the validated probe
``probes/ardour/build_session.py`` and corrected per root's Slice A review.
Scope and invariants are the adapter contract in
``docs/daw-integration/ardour-adapter-contract.md``:

- The timing manifest is explicit; nothing is inferred from ABC text.
- The project stores performance events (SMF), not notation.  Quoted chord
  symbols are stripped from the performance copy.  No lossless ABC claim.
- The voice map is authoritative for grouping, channels and programs.
- Every admission check runs before any file side effect; conversion work is
  staged in owned scratch and published only on success, never overwriting an
  existing output.

Only the 3001 template route is qualified (the probe route A).  A
native-7004 pinned clean template (route B in the contract) is a future,
unqualified route; this module fails closed on other template versions rather
than shaping them identically.
"""
import copy
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


class ContractError(ValueError):
    """The composition input violates the adapter contract; nothing was written."""


SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+")


def check_name(value, label):
    """A safe single path component: no traversal, separators or dot entries."""
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value) or value in (".", ".."):
        raise ContractError(f"{label} {value!r} is not a safe single name.")
    return value


# -- SMF primitives -----------------------------------------------------------

def vlq(n):
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise ContractError(f"VLQ value {n!r} is not a non-negative integer.")
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

    Track name: an explicit ``track`` given in the map is honored; all voices
    in one instrument group must agree on it or the map is refused.  Without
    an explicit name, the group's longest common voice-ID prefix when >= 3
    chars (PianoRH+PianoLH -> Piano), else the first voice ID.  Returns one
    entry per group: {track, voices, programs} with programs in voice order.
    """
    groups = {}
    for voice, spec in voice_map['voices'].items():
        check_name(voice, "voice")
        program = spec.get('program_raw')
        if isinstance(program, bool) or not isinstance(program, int) or not 0 <= program <= 127:
            raise ContractError(f"{voice}: program_raw must be an integer 0-127.")
        if not spec.get('instrument'):
            raise ContractError(f"{voice}: instrument must be non-empty.")
        g = groups.setdefault(spec['instrument'], {'voices': [], 'programs': [], 'tracks': set()})
        g['voices'].append(voice)
        g['programs'].append(program)
        if spec.get('track') is not None:
            g['tracks'].add(check_name(spec['track'], f"{voice}: track"))
    out = []
    for inst, g in groups.items():
        if len(g['tracks']) > 1:
            raise ContractError(
                f"Instrument {inst}: conflicting explicit track names {sorted(g['tracks'])}.")
        names = g['voices']
        if g['tracks']:
            prefix = g['tracks'].pop()
        else:
            prefix = names[0]
            if len(names) > 1:
                n = 0
                while n < min(map(len, names)) and len({x[n] for x in names}) == 1:
                    n += 1
                if n >= 3:
                    prefix = names[0][:n]
        out.append({'instrument': inst, 'track': check_name(prefix, f"{inst}: derived track"),
                    'voices': names, 'programs': g['programs']})
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
    if isinstance(tpq, bool) or not isinstance(tpq, int) or tpq <= 0:
        raise ContractError("quarter_note_ticks must be a positive integer.")
    if tpq != division:
        raise ContractError(f"quarter_note_ticks {tpq} != SMF division {division}; "
                            "resampling is out of contract.")
    tempos, meters = timing.get('tempos'), timing.get('meters')
    if not tempos or not meters:
        raise ContractError("timing must carry tempos and meters.")
    for kind, events in (('tempos', tempos), ('meters', meters)):
        quarters = [e.get('quarters') for e in events]
        if any(not isinstance(q, (int, float)) or isinstance(q, bool) for q in quarters):
            raise ContractError(f"timing.{kind} quarters must be numeric.")
        if quarters[0] != 0 or quarters != sorted(quarters) or len(set(quarters)) != len(quarters):
            raise ContractError(f"timing.{kind} must start at 0 and strictly increase.")
    end = timing.get('end_quarters')
    if isinstance(end, bool) or not isinstance(end, (int, float)) or not end > 0:
        raise ContractError("timing.end_quarters must be positive and finite.")
    if tempos[-1]['quarters'] > end or meters[-1]['quarters'] > end:
        raise ContractError("timing events must not start after end_quarters.")
    ev = []
    for t in tempos:
        bpm = float(t['bpm'])
        if not bpm > 0:
            raise ContractError("tempo bpm must be positive and finite.")
        us = round(60_000_000 / bpm)
        ev.append((int(t['quarters'] * tpq), meta(0x51, us.to_bytes(3, 'big'))))
    for m in meters:
        div = m.get('divisions_per_bar')
        if isinstance(div, bool) or not isinstance(div, (int, float)) or not float(div).is_integer() \
                or not 1 <= div <= 255:
            raise ContractError("meter divisions_per_bar must be an integer 1-255.")
        ev.append((int(m['quarters'] * tpq),
                   meta(0x58, bytes([int(div), 2, 24, 8]))))
    return ev


def end_tick(timing, division=480):
    """The manifest's exclusive end tick."""
    timing_to_meta(timing, division)
    return int(timing['end_quarters'] * timing.get('quarter_note_ticks', division))


def extent_samples(timing, sample_rate=48000):
    """3001-template region/loop extent placeholder, in samples.

    Derived by integrating the manifest's tempo map over ``end_quarters``.
    ``patch_tempo_range.py`` replaces this with the musical extent after the
    session upgrade, so this value never reaches a receipt on its own.
    """
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ContractError("sample_rate must be a positive integer.")
    end = float(timing['end_quarters'])
    seconds = 0.0
    tempos = timing['tempos']
    for i, t in enumerate(tempos):
        start = float(t['quarters'])
        stop = float(tempos[i + 1]['quarters']) if i + 1 < len(tempos) else end
        seconds += (stop - start) * 60.0 / float(t['bpm'])
    return round(seconds * sample_rate)


# -- per-track conversion -----------------------------------------------------

def check_notes(notes, limit, label):
    """Note events: integral non-negative ticks, positive durations, MIDI ranges,
    and the declared manifest end.  Returns them unchanged."""
    checked = []
    for n in notes:
        try:
            start, dur, pitch, vel, ch = n
        except (TypeError, ValueError):
            raise ContractError(f"{label}: malformed note {n!r}.")
        for value, lo, hi, what in ((start, 0, None, "start tick"), (dur, 1, None, "duration"),
                                    (pitch, 0, 127, "pitch"), (vel, 1, 127, "velocity"),
                                    (ch, 0, 15, "channel")):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ContractError(f"{label}: {what} {value!r} is not an integer.")
            if value < lo or (hi is not None and value > hi):
                raise ContractError(f"{label}: {what} {value} is out of range.")
        if start + dur > limit:
            raise ContractError(
                f"{label}: note ending at {start + dur} exceeds the declared end {limit}.")
        checked.append((start, dur, pitch, vel, ch))
    return checked


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
    channel order, one program change per channel at tick 0.  Notes are
    bounds-checked against the manifest end before anything is written.
    """
    notes = check_notes(notes, end_tick(timing, division), track['track'])
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

def load_template(template):
    """Parse and validate the session template before any output is written.

    Only the 3001 template shape is qualified (probe route A); other session
    versions are refused rather than shaped identically.
    """
    try:
        tree = ET.parse(template)
    except ET.ParseError as error:
        raise ContractError(f"template is not parseable XML: {error}")
    root = tree.getroot()
    if root.tag != 'Session':
        raise ContractError(f"template root is {root.tag}, not Session.")
    version = root.get('version')
    if version != '3001':
        raise ContractError(
            f"template session version {version!r} is unsupported; only the 3001 route is qualified.")
    for section in ('Sources', 'Locations', 'Routes', 'Playlists', 'TempoMap'):
        if root.find(section) is None:
            raise ContractError(f"template has no {section} section.")
    if not any(r.get('default-type') == 'midi' for r in root.find('Routes')):
        raise ContractError('template has no MIDI route')
    if not list(root.find('Playlists')):
        raise ContractError('template has no playlist to shape from')
    return tree


def build_session(template, abc_path, voice_map, timing, outdir, session_name,
                  convert, sample_rate=48000):
    """Build the session directory: per-track SMF sources + shaped session XML.

    ``convert(split_abc, out_mid)`` supplies a track's note events (production:
    ``abc2midi_notes`` with the pinned binary; tests inject a fake).  Every
    contract check — ABC structure, voice-set equality, map, names, timing,
    template version/structure — runs before any file is written, and the
    per-track conversion is staged in owned scratch: a failing track cleans up
    and leaves no partial session, and an existing output is never overwritten.
    Returns the session file path.
    """
    template, outdir = Path(template), Path(outdir)
    # Admission first: parse and validate everything before any side effect.
    header, bodies = split_abc_voices(Path(abc_path).read_text())
    check_voice_sets(bodies, voice_map)
    groups = group_tracks(voice_map)
    timing_to_meta(timing)  # validate before side effects
    check_name(session_name, "session name")
    tree = load_template(template)
    session_file = outdir / f'{session_name}.ardour'
    if session_file.exists():
        raise ContractError(f"output session {session_file} already exists.")

    # Stage conversion in owned scratch; publish only on complete success.
    with tempfile.TemporaryDirectory(prefix="ardour-session-") as scratch:
        scratch = Path(scratch)
        mididir = scratch / 'interchange' / session_name / 'midifiles'
        mididir.mkdir(parents=True)
        for g in groups:
            split = scratch / f"{g['track']}.abc"
            text = [header]
            for v in g['voices']:
                text.append('V:' + v)
                text.extend(strip_chords(x) for x in bodies[v])
            split.write_text('\n'.join(text) + '\n')
            notes = convert(split, mididir / f"{g['track']}.mid")
            build_track_smf(mididir / f"{g['track']}.mid", g, notes, timing)

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
        tree.write(scratch / f'{session_name}.ardour', encoding='UTF-8', xml_declaration=True)

        # Publish the completed staging tree; the targets were proved absent.
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / 'interchange').mkdir(exist_ok=True)
        (outdir / 'interchange' / session_name).mkdir()
        import shutil
        shutil.move(str(mididir), str(outdir / 'interchange' / session_name / 'midifiles'))
        for g in groups:
            shutil.move(str(scratch / f"{g['track']}.abc"), str(outdir / f"{g['track']}.abc"))
        shutil.move(str(scratch / f'{session_name}.ardour'), str(session_file))
    return session_file
