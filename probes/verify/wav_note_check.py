#!/usr/bin/env python3
"""wav_note_check.py — dependency-free event-level check of a rendered WAV
against an expected note timeline (from midi_summary.py JSON).

Purpose (#78 root review): give both DAW probes the SAME audio receipt check:
onset times and dominant pitches of a rendered mix or stem, compared against
the expected events from the converter's SMF summary.

This is an EVENT-LEVEL comparison (timing/pitch of energy onsets). It is not
a listening assessment and cannot judge timbre.

Usage:
  python3 wav_note_check.py RENDER.wav EXPECTED_SUMMARY.json [--tolerance-ms 80]

EXPECTED_SUMMARY.json: output of midi_summary.py — uses track notes[]
(start/dur in ticks) + division + tempo map to compute expected note onsets
in seconds (mono-pitch check per onset window).

Output: JSON to stdout — per-onset detected time/dominant frequency, match
table against expected events within tolerance, and summary counts.
"""
import array, json, math, struct, sys


def read_wav(path):
    d = open(path, "rb").read()
    assert d[:4] == b"RIFF" and d[8:12] == b"WAVE", "not a WAV"
    i = d.index(b"fmt ")
    fmt_len = struct.unpack("<I", d[i + 4 : i + 8])[0]
    audio_format, channels, sr = struct.unpack("<HHI", d[i + 8 : i + 16])
    j = d.index(b"data")
    size = struct.unpack("<I", d[j + 4 : j + 8])[0]
    if audio_format == 1 and (d[i + 22 : i + 24] == b"\x10\x00"):
        a = array.array("h")
        a.frombytes(d[j + 8 : j + 8 + size])
    else:
        raise SystemExit("only 16-bit PCM supported")
    mono = a[::channels]
    return mono, sr


def envelope_onsets(mono, sr, win_ms=100, thresh_ratio=0.02):
    """Onsets = moments where the dominant frequency changes class or energy
    rises sharply. Handles legato content where energy never drops out.
    Polyphonic mixes read the loudest voice — use on stems for per-voice
    checks; mix-level output is suggestive only."""
    win = max(1, int(sr * win_ms / 1000))
    frames = []
    peak_e = 0.0
    for s in range(0, len(mono) - win, win):
        seg = mono[s : s + win]
        e = sum(v * v for v in seg) / win
        frames.append((s / sr, e, seg))
        peak_e = max(peak_e, e)
    if peak_e == 0:
        return [], [], win
    thresh = peak_e * thresh_ratio
    onsets = []
    prev_f = None
    prev_e = 0.0
    for t, e, seg in frames:
        if e < thresh:
            prev_f, prev_e = None, 0.0
            continue
        f = dom_freq(seg, sr)
        new_note = prev_f is None or abs(f - prev_f) / max(prev_f, 1) > 0.06
        energy_jump = prev_e > 0 and e > prev_e * 4
        if new_note or energy_jump:
            onsets.append((t, f))
            prev_f = f
        prev_e = e
    env = [e for _, e, _ in frames]
    return onsets, env, win


def dom_freq(seg, sr, lo=40.0, hi=3000.0):
    best_f, best_m = 0.0, 0.0
    f = lo
    step = 4
    while f < hi:
        w = 2 * math.pi * f / sr
        cw = sw = 0.0
        for j in range(0, len(seg) - step, step):
            cw += seg[j] * math.cos(w * j)
            sw += seg[j] * math.sin(w * j)
        m = cw * cw + sw * sw
        if m > best_m:
            best_m, best_f = m, f
        f *= 1.01
    return best_f


def expected_events(summary):
    """Flatten midi_summary tracks into (onset_seconds, midi_pitch) list."""
    div = summary["division"]
    # gather global tempo map (tick -> us/qn) from any track
    tempos = []
    for t in summary["tracks"]:
        for tp in t.get("tempos", []):
            tempos.append((tp["tick"], tp["us_per_qn"]))
    tempos.sort()
    if not tempos:
        tempos = [(0, 500000)]

    def tick_to_sec(tick):
        sec = 0.0
        prev_t, prev_us = 0, tempos[0][1]
        for t, us in tempos:
            if t > tick:
                break
            sec += (t - prev_t) * prev_us / 1e6 / div
            prev_t, prev_us = t, us
        sec += (tick - prev_t) * prev_us / 1e6 / div
        return sec

    events = []
    for t in summary["tracks"]:
        for n in t.get("notes", []):
            events.append((tick_to_sec(n["start"]), n["pitch"], t["track"]))
    events.sort()
    return events


def main():
    wav, summary_path = sys.argv[1], sys.argv[2]
    tol = 0.08
    if "--tolerance-ms" in sys.argv:
        tol = float(sys.argv[sys.argv.index("--tolerance-ms") + 1]) / 1000
    flat_bpm = None
    if "--bpm" in sys.argv:
        flat_bpm = float(sys.argv[sys.argv.index("--bpm") + 1])
    mono, sr = read_wav(wav)
    summary = json.load(open(summary_path))
    if flat_bpm:
        # Replace the SMF tempo map: DAW sessions often govern their own tempo.
        for t in summary["tracks"]:
            t["tempos"] = []
        summary["tracks"][0]["tempos"] = [{"tick": 0, "us_per_qn": int(60_000_000 / flat_bpm), "bpm": flat_bpm}]
    expected = expected_events(summary)
    onsets, env, win = envelope_onsets(mono, sr)
    # onsets already carry (time, dominant_freq) from the detector
    observed = [{"t": round(t, 3), "dom_hz": round(f, 1)} for t, f in onsets]
    # match expected onsets to observed onsets (time only)
    matched = 0
    unmatched = []
    used = set()
    for t_exp, pitch, trk in expected:
        best = None
        for k, ob in enumerate(observed):
            if k in used:
                continue
            if abs(ob["t"] - t_exp) <= tol:
                if best is None or abs(ob["t"] - t_exp) < abs(best[1]["t"] - t_exp):
                    best = (k, ob)
        if best:
            used.add(best[0])
            matched += 1
        else:
            unmatched.append({"t": round(t_exp, 3), "pitch": pitch, "track": trk})
    out = {
        "wav": wav,
        "sr": sr,
        "duration_s": round(len(mono) / sr, 3),
        "expected_events": len(expected),
        "observed_onsets": len(observed),
        "matched_onsets_within_tolerance": matched,
        "tolerance_s": tol,
        "unmatched_expected": unmatched[:40],
        "observed": observed[:80],
    }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
