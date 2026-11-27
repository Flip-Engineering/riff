#!/usr/bin/env python3
"""Patch an Ardour 9.8 saved session (version 7004) to the musical timing map.

Ardour migrates the old 3001 template tempo map on first load and writes a
new-format map.  This script replaces that map and the sample-based
session/region extents with the explicit musical positions in the timing
manifest.  The 'b' coordinate used by version-7004 locations is 1920 units per
quarter note in this probe; end b = end_quarters * 1920.
"""
import argparse, json, math, re
from pathlib import Path

SUPERCLOCK_PER_SECOND = 282240000

def sec_at(q, tempos):
    sec = 0.0
    for i, t in enumerate(tempos):
        q0, bpm = t['quarters'], t['bpm']
        if q <= q0:
            break
        q1 = tempos[i + 1]['quarters'] if i + 1 < len(tempos) else None
        end = q if q1 is None or q < q1 else q1
        sec += (end - q0) * 60.0 / bpm
        if q1 is None or q <= q1:
            break
    return sec

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--session', required=True)
    ap.add_argument('--timing', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--sample-rate', type=int, default=48000)
    a = ap.parse_args()
    timing = json.loads(Path(a.timing).read_text())
    spc = SUPERCLOCK_PER_SECOND / a.sample_rate
    tempos = sorted(timing['tempos'], key=lambda x: x['quarters'])
    meters = sorted(timing['meters'], key=lambda x: x['quarters'])
    end_q = timing['end_quarters']
    text = Path(a.session).read_text()
    lines = ['  <TempoMap superclocks-per-second="%d">' % SUPERCLOCK_PER_SECOND, '    <Tempos>']
    for t in tempos:
        sclock = int(round(sec_at(t['quarters'], tempos) * a.sample_rate * spc))
        lines.append('      <Tempo npm="%s" enpm="%s" note-type="4" type="Constant" '
                     'locked-to-meter="0" continuing="0" active="1" sclock="%d" '
                     'quarters="%d:0" bbt="%s" omega="0"/>' %
                     (t['bpm'], t['bpm'], sclock, t['quarters'], t['bbt']))
    lines += ['    </Tempos>', '    <Meters>']
    for m in meters:
        sclock = int(round(sec_at(m['quarters'], tempos) * a.sample_rate * spc))
        lines.append('      <Meter note-value="4" divisions-per-bar="%d" sclock="%d" '
                     'quarters="%d:0" bbt="%s"/>' %
                     (m['divisions_per_bar'], sclock, m['quarters'], m['bbt']))
    lines += ['    </Meters>', '    <MusicTimes/>', '  </TempoMap>']
    new_map = '\n'.join(lines)
    text, n = re.subn(r'  <TempoMap.*?</TempoMap>', new_map, text, flags=re.S)
    if n != 1:
        raise SystemExit('expected exactly one TempoMap')
    end_b = end_q * 1920
    # Session range and loop span the whole musical arrangement; punch is one
    # quarter (start<end) so Ardour does not reject the auto-punch location.
    for name, end in [('Loop', end_b), ('session', end_b), ('Punch', 1920)]:
        text, count = re.subn(r'name="%s" start="b[^"]+" end="b[^"]+"' % re.escape(name),
                              'name="%s" start="b0" end="b%d"' % (name, end), text)
        if count != 1:
            raise SystemExit('expected one Location named %s' % name)
    text, count = re.subn(r'length="b[^"]+@b[^"]+"',
                          'length="b%d@b0"' % end_b, text)
    if count < 1:
        raise SystemExit('no region lengths patched')
    Path(a.out).write_text(text)
    print(a.out)

if __name__ == '__main__':
    main()
