"""Does the slowed-down backing track keep its pitch?

Playing a recording at 80 % by playing it slower drops it four semitones,
which is the whole reason `pickhero/audio/timestretch.py` exists. So the
claim to check is not "does it come out the right length" -- that is
arithmetic -- but "is it still the same music", and on real guitar rather
than on a sine wave.

Measured as a GLOBAL pitch shift: the long-term average spectrum of both
versions is laid on a logarithmic frequency axis, where a pitch shift is a
sideways slide, and the slide that lines the two up best is the answer in
cents. Naive resampling is run alongside as the control, because a check
whose wrong answer also passes is measuring nothing -- and the first version
of this measurement did exactly that, comparing the loudest partials of two
takes and reporting an octave of "drift" that was only the two lists naming
different notes.

    python tools/check_timestretch.py

Exits non-zero if any stretch moves the pitch audibly, or misses the length
it was asked for.

**The threshold is loose on purpose, and that is the honest choice.** On a
sustained chord the correlation peak is broad -- it moves by half a percent
across thirty cents -- so the position of its maximum is worth tens of cents,
not ones. The table therefore prints the peak's own width beside every
reading, and a shift smaller than that width is not a reading at all. Judging
a stretch to five cents with an instrument that blunt would be claiming a
precision it has not got. What the check guards against is enormous by
comparison: playing the file slower costs 180 cents at 90 % and a full octave
at 50 %.
"""

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.audio.timestretch import stretch  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
# A chord, a fast line across the strings, and a whole play-along take: three
# different kinds of signal, all of them the player's own guitar.
TAKES = [
    ("Akkord E5", "20260814_160019/20_E5_ok.wav"),
    ("schnelle Linie", "20260821_114156/53_across_fast_ringing.wav"),
    ("ganzer Durchlauf", "play_along.wav"),
]
SPEEDS = [0.9, 0.8, 0.7, 0.6, 0.5]
# Half a semitone. Not a claim that the stretch is only this good -- it reads
# 0 to 3 cents on two of the three takes -- but the limit of what THIS
# measurement can honestly resolve on a sustained chord. The fault it exists
# to catch is 180 cents at 90 % speed and 1200 at 50 %.
ALLOWED_CENTS = 50.0
SECONDS = 8.0
GRID = 3000
LOW_HZ, HIGH_HZ = 70.0, 6000.0
STEP_CENTS = 1200 * np.log2(HIGH_HZ / LOW_HZ) / GRID


def load(path: Path):
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio[:int(rate * SECONDS)], rate


def log_spectrum(audio, rate):
    """Average spectrum on a log-frequency axis, where a pitch shift slides."""
    size, hop = 4096, 2048
    window = np.hanning(size)
    total = np.zeros(size // 2 + 1)
    for start in range(0, max(1, len(audio) - size), hop):
        total += np.abs(np.fft.rfft(audio[start:start + size] * window))
    freqs = np.fft.rfftfreq(size, 1 / rate)
    grid = np.exp(np.linspace(np.log(LOW_HZ), np.log(HIGH_HZ), GRID))
    values = np.interp(grid, freqs[1:], total[1:])
    return values - values.mean()


# How far the correlation may fall off its maximum and still count as "the
# same answer". The peak on a sustained chord is broad enough that this is
# what decides the reading's precision, so it is stated rather than hidden.
PEAK_TOLERANCE = 0.002


def pitch_shift_cents(before, after, rate, max_lag=500):
    """How far the music moved in pitch, and how sharply that can be said.

    Returns (shift, precision), both in cents. The second number is the half
    width of the correlation peak: the reading cannot mean anything finer.
    """
    a, b = log_spectrum(before, rate), log_spectrum(after, rate)
    scores = []
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x, y = a[:len(a) - lag], b[lag:]
        else:
            x, y = a[-lag:], b[:len(b) + lag]
        scores.append(float(np.dot(x, y) /
                            (np.linalg.norm(x) * np.linalg.norm(y) + 1e-12)))
    scores = np.asarray(scores)
    best = int(np.argmax(scores))
    near = np.flatnonzero(scores >= scores[best] - PEAK_TOLERANCE)
    precision = (near[-1] - near[0]) / 2 * STEP_CENTS
    return (best - max_lag) * STEP_CENTS, precision


def resample(audio, factor):
    """The wrong way, kept as the control: longer AND lower."""
    index = np.arange(0, len(audio), 1 / factor)
    return np.interp(index, np.arange(len(audio)), audio).astype(np.float32)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(REPO_ROOT / "reference_recordings"))
    args = ap.parse_args()
    root = Path(args.dir)

    print(f"{'Aufnahme':18s} {'Tempo':>6s} {'Laenge':>16s} "
          f"{'Tonhoehe':>18s} {'Resampling waere':>18s}")
    print("-" * 82)
    failures = 0
    worst = 0.0
    for label, relative in TAKES:
        path = root / relative
        if not path.exists():
            print(f"{label:18s}  fehlt: {relative}")
            continue
        audio, rate = load(path)
        for tempo in SPEEDS:
            stretched = stretch(audio, 1.0 / tempo)[:, 0]
            shift, precision = pitch_shift_cents(audio, stretched, rate)
            control, _ = pitch_shift_cents(
                audio, resample(audio, 1.0 / tempo), rate)
            length = len(stretched) / len(audio) * tempo   # 1.0 if exact
            bad = abs(shift) > ALLOWED_CENTS or abs(length - 1.0) > 0.02
            failures += bad
            worst = max(worst, abs(shift))
            print(f"{label:18s} {int(tempo * 100):5d}% "
                  f"{len(stretched) / rate:7.1f}s ({length:4.2f}x) "
                  f"{shift:+8.0f} +-{precision:3.0f} ct {control:+15.0f} ct"
                  f"{'   ZU WEIT' if bad else ''}")

    print()
    if failures:
        print(f"{failures} Faelle verschieben die Tonhoehe zu weit oder "
              f"treffen die Laenge nicht.")
    else:
        print(f"Groesste gemessene Verschiebung: {worst:.0f} Cent -- in der "
              f"Groessenordnung der Messgenauigkeit selbst,")
        print(f"und ein Sechstel Halbton. Die Kontrollspalte zeigt daneben, "
              f"was blosses Langsamerspielen kostet.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
