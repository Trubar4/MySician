"""Calibrate per-string chord verification against the reference recordings.

Answers, for one struck chord, not just "did this string sound" but "did it
sound on the RIGHT fret" -- by scoring competing pitch hypotheses against the
partials that no other expected note of the chord can produce.

Thresholds below were fitted on reference_recordings/20260814_160019 (clean
DI, Focusrite, 48 kHz, standard tuning ~19 cents flat). On that set the rule
catches 7/7 deliberate one-fret errors and produces 0 false alarms across 33
confidently judged strings; 22 further strings are octave/fifth duplicates of
a lower string and get no verdict at all, which is the honest answer -- their
partials are a strict subset of a string that is already sounding.

    python tools/analyze_reference.py reference_recordings/<stamp>
"""

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

# --- analysis window -------------------------------------------------------
N_WIN = 16384          # 341 ms at 48 kHz; long enough to resolve a semitone
SKIP_MS = 40           # let the attack transient pass before analysing
PAD = 2                # zero-padding factor, for finer peak interpolation

# --- partial matching ------------------------------------------------------
CENTS_WIN = 45         # absorbs this guitar being ~19 cents flat, plus
                       # inharmonicity, without reaching the neighbouring
                       # semitone (100 cents away)
SEP_CENTS = 60         # two partials closer than this count as shared
N_HARM = 14
MIN_HZ = 150.0         # below this the FFT grid is too coarse to trust

# --- decision thresholds ---------------------------------------------------
PRESENT_DB = -32.0     # genuine detections landed at -3..-30 dB; noise-driven
                       # false alarms at -36..-39 dB
MARGIN_DB = 8.0        # winner must lead the runner-up by this much; at 5 dB
                       # a correctly played low E was still mis-called once
INTRUDER_DB = -25.0    # to flag a wrong note on a string whose expected note
                       # is itself masked, the intruder must be this loud

NOTE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def nm(m):
    return f"{NOTE[m % 12]}{m // 12 - 1}"


def midi_to_freq(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


def load_wav(path):
    with wave.open(str(path)) as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64) / 32768.0
    return (raw.reshape(-1, ch).mean(axis=1) if ch > 1 else raw), sr


def onsets(x, sr, rel_db=14.0, min_gap_s=0.15):
    """Strike times from the energy envelope, thresholded relative to this
    take's own peak so quiet takes are not missed."""
    hop, win = 256, 1024
    n = (len(x) - win) // hop
    if n <= 0:
        return []
    env = np.array([np.sqrt(np.mean(x[i * hop:i * hop + win] ** 2)) for i in range(n)])
    db = 20 * np.log10(env + 1e-12)
    thr = db.max() - rel_db
    out, last = [], -1e9
    for i in range(2, len(db)):
        t = i * hop / sr
        if db[i] > thr and db[i] - db[i - 2] > 3.0 and t - last > min_gap_s:
            out.append(t)
            last = t
    return out


def spectrum(frame, sr):
    w = np.hanning(len(frame))
    S = np.abs(np.fft.rfft(frame * w, n=len(frame) * PAD))
    f = np.fft.rfftfreq(len(frame) * PAD, 1 / sr)
    return f, S


def window_at(x, t_s, sr):
    o = int((t_s + SKIP_MS / 1000) * sr)
    seg = x[o:o + N_WIN]
    return np.pad(seg, (0, max(0, N_WIN - len(seg))))


def peak_near(f, S, target, cents=CENTS_WIN):
    lo, hi = target * 2 ** (-cents / 1200), target * 2 ** (cents / 1200)
    idx = np.where((f >= lo) & (f <= hi))[0]
    return float(S[idx].max()) if len(idx) else 0.0


def partial_freqs(m, sr, n_harm=N_HARM):
    f0 = midi_to_freq(m)
    return [f0 * h for h in range(1, n_harm + 1) if f0 * h < sr * 0.45]


def score(f, S, cand, others, sr, n_best=3):
    """Evidence for `cand`, in dB below the frame peak, using only partials
    that no note in `others` produces. None = fully masked, undecidable."""
    theirs = [pf for o in others for pf in partial_freqs(o, sr, 60)]
    peak = S.max() + 1e-12
    vals = []
    for fh in partial_freqs(cand, sr):
        if fh < MIN_HZ:
            continue
        if any(abs(1200 * np.log2(fh / tf)) < SEP_CENTS for tf in theirs):
            continue
        vals.append(20 * np.log10(peak_near(f, S, fh) / peak + 1e-12))
    if not vals:
        return None
    vals.sort(reverse=True)
    return float(np.mean(vals[:n_best]))


def judge(x, sr, target, others, span=2):
    """Which pitch did this string play? None when no fair verdict is possible.

    Presumption of innocence: a string is only called wrong on positive
    evidence of a wrong pitch, never on absence of evidence for the right one.
    """
    per, target_testable = [], None
    for t0 in onsets(x, sr):
        fr = window_at(x, t0, sr)
        if np.abs(fr).max() < 1e-4:
            continue
        f, S = spectrum(fr, sr)
        if target_testable is None:
            target_testable = score(f, S, target, others, sr) is not None
        row = {c: s for c in range(target - span, target + span + 1)
               if (s := score(f, S, c, others, sr)) is not None}
        if row:
            per.append(row)
    if not per:
        return None

    cands = sorted({c for r in per for c in r})
    avg = {c: float(np.mean([r[c] for r in per if c in r])) for c in cands}
    rank = sorted(avg.items(), key=lambda kv: -kv[1])
    best = rank[0]
    margin = best[1] - (rank[1][1] if len(rank) > 1 else -120.0)

    if not target_testable:
        # expected note is an octave/fifth of a lower string: it can never be
        # confirmed, but a foreign pitch still shows up loud and clear
        if best[0] != target and best[1] > INTRUDER_DB and margin > MARGIN_DB:
            return dict(winner=best[0], level=best[1], margin=margin, via="intruder")
        return None
    if best[1] <= PRESENT_DB or margin <= MARGIN_DB:
        return None
    return dict(winner=best[0], level=best[1], margin=margin, via="direct")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", help="reference_recordings/<stamp>")
    args = ap.parse_args()

    d = Path(args.session)
    manifest = json.load(open(d / "manifest.json", encoding="utf-8"))

    print(f"{manifest['device']}  {manifest['samplerate']} Hz\n")
    print(f"{'take':22s} {'string':>6s} {'played':>7s} {'expected':>9s} "
          f"{'level':>8s} {'margin':>8s}  verdict")
    print("-" * 88)

    judged = false_alarms = skipped = 0
    for t in manifest["takes"]:
        if not t["expected_midi"] or len(t["expected_midi"]) != 1:
            continue
        chord = t["expected_midi"][0]
        if len(chord) < 2:
            continue
        x, sr = load_wav(d / t["file"])
        for m in chord:
            r = judge(x, sr, m, [o for o in chord if o != m])
            if r is None:
                skipped += 1
                continue
            judged += 1
            ok = r["winner"] == m
            if ok:
                verdict = "correct"
            else:
                false_alarms += 1
                verdict = "FALSE ALARM"
            print(f"{t['id']:22s} {nm(m):>6s} {nm(r['winner']):>7s} {nm(m):>9s} "
                  f"{r['level']:7.1f}dB {r['margin']:+7.1f}dB  {verdict}")

    print("-" * 88)
    print(f"judged {judged}   false alarms {false_alarms}   "
          f"not decidable {skipped}")
    print("\n(The manifest records what was actually played, so every mismatch\n"
          " above is a false alarm. Whether deliberate errors are CAUGHT is the\n"
          " separate question the calibration notes answer.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
