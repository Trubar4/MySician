"""How much of the recording survives being read back at another rate?

Half of a pitch shift is a resample, and for a long time this app's was
linear interpolation -- which is not a resampler. The fault is invisible in
every obvious check: the length is right, the pitch is right to a hundredth
of a semitone, and the file plays. What is wrong is everything between the
notes, and the only way to see it is to compare against a resample that is
exact.

That reference is a spectral one: truncating a segment's spectrum IS the
brick wall a resampler approximates, so an FFT answers the question outright
-- but only for a RATIONAL ratio, since the mapping it implies is
`in_n / out_n`. So each segment is cut to exactly that length and the
resampler is handed the same rational ratio. Comparing against a reference
on a different grid measures the drift between the two grids, which is how
the first version of this measurement read 21 dB where the honest answer is
27 to 30.

    python tools/check_resampler.py [recording.mp3 ...]

Two numbers per reading, because they are different faults. Over the FULL
band, and over everything BELOW 18 kHz: a windowed sinc rolls off across the
top of its band by construction, and that is a kernel's transition rather
than noise -- where linear's error is the same either way, which is what
says it is noise. The band table underneath says what arrives that was
never played: at -2 semitones linear puts +17.9 dB into 16-20 kHz, which is
everything above the new Nyquist folded back down.

Exits non-zero if the shipped resampler ever reads worse than
`FLOOR_FULL_DB` over the full band or `FLOOR_CLEAR_DB` below 18 kHz, or if
linear ever beats it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.audio import timestretch                        # noqa: E402

#: What the shipped kernel measures on real music, with room for a mix this
#: has not been run on. Fitted figures are in `timestretch.TAPS`.
#:
#: **The full-band floor is loose because that figure is about the SONG.**
#: Measured on three of the player's own recordings, below 18 kHz they all
#: read 88 to 106 dB -- and over the full band Californication reads 53.7
#: at +2 semitones where Bon Jovi reads 70.8, because it is the brighter
#: master and so has more sitting in the top two kilohertz for the kernel's
#: transition to roll off. A floor fitted to the better of those two would
#: fail on a bright mix for being bright.
FLOOR_FULL_DB = 50.0
FLOOR_CLEAR_DB = 85.0

OUT_N = 1 << 18
TRIM = 4096
AT_SECONDS = (30, 60, 120, 180, 240)
BANDS = ((0, 1000), (1000, 4000), (4000, 8000), (8000, 12000),
         (12000, 16000), (16000, 20000))


def linear(samples: np.ndarray, ratio: float, out_n: int) -> np.ndarray:
    """What used to be in `_resample`, kept as the control."""
    index = np.arange(max(0, out_n), dtype=np.float64) * ratio
    low = np.clip(index.astype(np.int64), 0, max(0, len(samples) - 2))
    frac = (index - low).astype(np.float32).reshape(-1, 1)
    return (samples[low] * (1.0 - frac)
            + samples[low + 1] * frac).astype(np.float32)


def ideal(seg: np.ndarray, out_n: int) -> np.ndarray:
    """The exact band-limited resample of `seg` into `out_n` frames."""
    out = np.empty((out_n, seg.shape[1]))
    for channel in range(seg.shape[1]):
        spectrum = np.fft.rfft(seg[:, channel].astype(np.float64))
        keep = spectrum[:out_n // 2 + 1].copy()
        if out_n % 2 == 0:
            keep[-1] = keep[-1].real
        out[:, channel] = np.fft.irfft(keep, n=out_n) * (out_n / len(seg))
    return out


def snr_db(got: np.ndarray, ref: np.ndarray, rate: int,
           below: float | None = None) -> float:
    got = got[TRIM:-TRIM].astype(np.float64)
    ref = ref[TRIM:-TRIM]
    if below is not None:
        n = len(got)
        mask = (np.fft.rfftfreq(n, 1 / rate) < below).reshape(-1, 1)
        got = np.fft.irfft(np.fft.rfft(got, axis=0) * mask, n=n, axis=0)
        ref = np.fft.irfft(np.fft.rfft(ref, axis=0) * mask, n=n, axis=0)
    return 10 * np.log10(np.sum(ref ** 2) / np.sum((got - ref) ** 2))


def band_table(got: np.ndarray, ref: np.ndarray, rate: int
               ) -> list[tuple[int, int, float]]:
    """Energy per band, got against ref, in dB."""
    g = got[TRIM:-TRIM, 0]
    r = ref[TRIM:-TRIM, 0]
    n = 1 << int(np.floor(np.log2(len(g))))
    window = np.hanning(n)
    gm = np.abs(np.fft.rfft(g[:n] * window))
    rm = np.abs(np.fft.rfft(r[:n] * window))
    freqs = np.fft.rfftfreq(n, 1 / rate)
    rows = []
    for low, high in BANDS:
        inside = (freqs >= low) & (freqs < high)
        rows.append((low, high, 10 * np.log10(
            max(np.sum(gm[inside] ** 2), 1e-30)
            / max(np.sum(rm[inside] ** 2), 1e-30))))
    return rows


def _decode(path: Path) -> tuple[np.ndarray, int]:
    import os
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    import pygame
    if not pygame.mixer.get_init():
        pygame.mixer.pre_init(44100, -16, 2, 2048)
        pygame.mixer.init()
    return timestretch._decode(path)


def main(paths: list[str]) -> int:
    songs = [Path(p) for p in paths] or sorted(Path("songs").glob("*.mp3"))[:1]
    worst_full = worst_clear = 1e9
    bad = False
    for song in songs:
        samples, rate = _decode(song)
        print(f"\n{song.name}   {len(samples) / rate / 60:.1f} min"
              f" at {rate} Hz")
        for semitones in (2, -2):
            want = 2.0 ** (semitones / 12.0)
            in_n = int(round(OUT_N * want))
            ratio = in_n / OUT_N          # rational, so the FFT is the answer
            print(f"  {semitones:+d} semitones            full band   "
                  f"below 18 kHz")
            for at in AT_SECONDS:
                start = rate * at
                if start + in_n > len(samples):
                    continue
                seg = samples[start:start + in_n]
                ref = ideal(seg, OUT_N)
                got = timestretch._resample(seg, ratio, OUT_N)
                was = linear(seg, ratio, OUT_N)
                full, clear = (snr_db(got, ref, rate),
                               snr_db(got, ref, rate, 18_000))
                worst_full = min(worst_full, full)
                worst_clear = min(worst_clear, clear)
                lin = snr_db(was, ref, rate)
                if lin >= full:
                    bad = True
                print(f"    at {at:4d} s   shipped {full:6.1f} dB /"
                      f" {clear:6.1f} dB    linear {lin:6.1f} dB")
            seg = samples[rate * AT_SECONDS[1]:rate * AT_SECONDS[1] + in_n]
            if len(seg) == in_n:
                ref = ideal(seg, OUT_N)
                rows = zip(band_table(linear(seg, ratio, OUT_N), ref, rate),
                           band_table(timestretch._resample(seg, ratio, OUT_N),
                                      ref, rate))
                print("     band          linear     shipped")
                for (low, high, dl), (_, _, ds) in rows:
                    print(f"     {low // 1000:2d}-{high // 1000:2d} kHz   "
                          f"{dl:+8.2f} dB  {ds:+8.2f} dB")
    print(f"\nworst: {worst_full:.1f} dB full band, {worst_clear:.1f} dB "
          f"below 18 kHz   (floors {FLOOR_FULL_DB:.0f}"
          f" / {FLOOR_CLEAR_DB:.0f})")
    if worst_full < FLOOR_FULL_DB or worst_clear < FLOOR_CLEAR_DB:
        print("FAIL: the resampler reads worse than it was fitted at")
        return 1
    if bad:
        print("FAIL: linear interpolation beat it somewhere")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
