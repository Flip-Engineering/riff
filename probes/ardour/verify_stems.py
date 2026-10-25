#!/usr/bin/env python3
"""Check that solo-render stems sum to the rendered mix within 1 LSB."""
import argparse, array, hashlib, math, struct, wave

def read(path):
    w = wave.open(path, 'rb')
    p = w.getparams()
    a = array.array('h'); a.frombytes(w.readframes(w.getnframes()))
    return p, a

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mix', required=True)
    ap.add_argument('--stem', action='append', required=True)
    ap.add_argument('--out')
    a = ap.parse_args()
    mp, mix = read(a.mix)
    stems = [read(s) for s in a.stem]
    if any(p[:3] != mp[:3] for p, _ in stems):
        raise SystemExit('stem format differs from mix')
    n = min([len(mix)] + [len(x) for _, x in stems])
    total = [0] * n
    for _, data in stems:
        for i in range(n): total[i] += data[i]
    diff = [total[i] - mix[i] for i in range(n)]
    rms = math.sqrt(sum(d * d for d in diff) / n)
    out = {
        'mix': a.mix, 'stems': a.stem, 'params': list(mp),
        'mix_sha256': hashlib.sha256(open(a.mix, 'rb').read()).hexdigest(),
        'stem_sha256': [hashlib.sha256(open(s, 'rb').read()).hexdigest() for s in a.stem],
        'mix_peak_linear': max(abs(x) for x in mix[:n]),
        'stem_sum_peak_linear': max(abs(x) for x in total),
        'difference_rms_linear': rms,
        'difference_peak_linear': max(abs(x) for x in diff),
        'within_1_lsb': max(abs(x) for x in diff) <= 1,
    }
    print(__import__('json').dumps(out, indent=1))
    if a.out: open(a.out, 'w').write(__import__('json').dumps(out, indent=1))

if __name__ == '__main__':
    main()
