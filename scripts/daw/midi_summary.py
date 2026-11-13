#!/usr/bin/env python3
"""Minimal SMF parser used for Riff ABC->MIDI fidelity receipts (no external deps)."""
import json, sys

def read_vlq(b, i):
    v = 0
    while True:
        c = b[i]; i += 1
        v = (v << 7) | (c & 0x7f)
        if not c & 0x80:
            return v, i

def parse(path):
    b = open(path, 'rb').read()
    assert b[:4] == b'MThd', 'not SMF'
    hlen = int.from_bytes(b[4:8], 'big')
    fmt = int.from_bytes(b[8:10], 'big'); ntrks = int.from_bytes(b[10:12], 'big'); div = int.from_bytes(b[12:14], 'big')
    pos = 8 + hlen
    tracks = []
    while pos + 8 <= len(b) and len(tracks) < ntrks:
        assert b[pos:pos+4] == b'MTrk', (pos, b[pos:pos+4])
        tlen = int.from_bytes(b[pos+4:pos+8], 'big'); i = pos + 8; end = i + tlen
        tick = 0; status = None; ev = []
        while i < end:
            delta, i = read_vlq(b, i); tick += delta
            c = b[i]
            if c == 0xff:
                i += 1; typ = b[i]; i += 1; ln, i = read_vlq(b, i); data = b[i:i+ln]; i += ln
                ev.append({'tick': tick, 'type': 'meta', 'meta': typ, 'data_hex': data.hex(),
                           'text': data.decode('utf-8', 'replace') if typ in (1,2,3,4,5,6,7,8,9,10,11,12,13,14,15) else None})
                if typ == 0x2f: break
            elif c in (0xf0, 0xf7):
                i += 1; ln, i = read_vlq(b, i); data = b[i:i+ln]; i += ln
                ev.append({'tick': tick, 'type': 'sysex', 'data_hex': data.hex()})
            else:
                if c & 0x80:
                    status = c; i += 1
                assert status is not None, 'running status with none'
                kind = status & 0xf0; chan = status & 0x0f
                if kind in (0xc0, 0xd0):
                    d1 = b[i]; i += 1
                    ev.append({'tick': tick, 'type': 'program' if kind == 0xc0 else 'pressure', 'ch': chan, 'v': d1})
                else:
                    d1 = b[i]; d2 = b[i+1]; i += 2
                    if kind == 0x90:
                        ev.append({'tick': tick, 'type': 'on' if d2 else 'off', 'ch': chan, 'pitch': d1, 'vel': d2})
                    elif kind == 0x80:
                        ev.append({'tick': tick, 'type': 'off', 'ch': chan, 'pitch': d1, 'vel': d2})
                    elif kind == 0xb0:
                        ev.append({'tick': tick, 'type': 'cc', 'ch': chan, 'cc': d1, 'v': d2})
                    elif kind == 0xe0:
                        ev.append({'tick': tick, 'type': 'bend', 'ch': chan, 'v': (d2 << 7) | d1})
                    elif kind == 0xa0:
                        ev.append({'tick': tick, 'type': 'aftertouch', 'ch': chan, 'pitch': d1, 'v': d2})
        tracks.append(ev); pos = end
    return {'format': fmt, 'division': div, 'tracks': tracks}

def summarize(path):
    m = parse(path); out = {'file': path, 'format': m['format'], 'division': m['division'], 'track_count': len(m['tracks']), 'tracks': []}
    for ti, evs in enumerate(m['tracks']):
        notes = {}; active = {}; nlist = []
        programs = []; tempos = []; timesigs = []; keysigs = []; names = []; texts = []; lyrics = []; markers = []; ccs = []; bends = []
        for e in evs:
            t = e['tick']
            if e['type'] == 'on':
                active.setdefault((e['ch'], e['pitch']), []).append((t, e['vel']))
            elif e['type'] == 'off':
                st = active.get((e['ch'], e['pitch']))
                if st:
                    s, v = st.pop(0)
                    nlist.append({'start': s, 'dur': t - s, 'pitch': e['pitch'], 'vel': v, 'ch': e['ch']})
            elif e['type'] == 'program': programs.append({'tick': t, 'ch': e['ch'], 'program': e['v']})
            elif e['type'] == 'meta':
                mt = e['meta']
                if mt == 0x51:
                    us = int.from_bytes(bytes.fromhex(e['data_hex']), 'big'); tempos.append({'tick': t, 'us_per_qn': us, 'bpm': round(60_000_000/us, 3)})
                elif mt == 0x58:
                    d = bytes.fromhex(e['data_hex']); timesigs.append({'tick': t, 'num': d[0], 'den_pow2': d[1]})
                elif mt == 0x59:
                    d = bytes.fromhex(e['data_hex']); keysigs.append({'tick': t, 'sharps': d[0] if d[0] < 128 else d[0]-256, 'minor': d[1]})
                elif mt == 0x03: names.append({'tick': t, 'text': e['text']})
                elif mt in (1,2): texts.append({'tick': t, 'meta': mt, 'text': e['text']})
                elif mt == 0x05: lyrics.append({'tick': t, 'text': e['text']})
                elif mt == 0x06: markers.append({'tick': t, 'text': e['text']})
            elif e['type'] == 'cc': ccs.append({'tick': t, 'ch': e['ch'], 'cc': e['cc'], 'v': e['v']})
            elif e['type'] == 'bend': bends.append({'tick': t, 'ch': e['ch'], 'v': e['v']})
        nlist.sort(key=lambda n: (n['start'], n['pitch']))
        out['tracks'].append({'track': ti, 'name': names[0]['text'] if names else None, 'name_events': names,
            'channels': sorted({n['ch'] for n in nlist} | {p['ch'] for p in programs}), 'programs': programs,
            'tempos': tempos, 'time_sigs': timesigs, 'key_sigs': keysigs, 'texts': texts[:12], 'lyrics': lyrics[:12],
            'markers': markers[:12], 'cc': ccs[:20], 'pitch_bends': bends[:12], 'note_count': len(nlist),
            'notes': nlist[:40]})
    return out

if __name__ == '__main__':
    for p in sys.argv[1:]:
        print(json.dumps(summarize(p), indent=1))
