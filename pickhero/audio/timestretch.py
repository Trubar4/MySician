"""Slow a recording down without dropping its pitch.

A backing track played at 80 % by resampling comes out four semitones flat,
which is worse than no backing track at all -- so below full speed the
recording used to be held silent and the player was pointed at the MIDI
backing instead. That is the wrong answer for the songs people actually
practise: a recording IS the song, and practising a solo slowly against a
click is not the same exercise.

So the file is stretched instead of resampled. WSOLA (waveform similarity
overlap-add) cuts the audio into overlapping windows and lays them back down
at a different spacing, sliding each one a little to the place where it best
continues the last -- which is what keeps the periods lined up and the pitch
where it was. It is the cheap, dependency-free member of the time-stretch
family: no FFT resynthesis, no phase vocoder, no ML, about eighty lines of
numpy, and it runs on the low-end machine this app is written for.

Two things it is honest about:

- **It is not transparent.** Stretched far, a strummed chord picks up a faint
  flutter, and drums smear before guitars do. At 80-90 % it is hard to hear;
  at 50 % it is audible and still perfectly usable to practise against.
- **It holds the whole song in memory while it works.** Eight minutes of
  stereo is about 170 MB as float32, and the stretched copy is longer again,
  so a long recording at 50 % briefly wants the better part of half a gigabyte.
  It survives on the machines this app targets; a longer file than that would
  need the stretch done in chunks, which the overlap makes real work.
- **It costs seconds, not milliseconds.** A four-minute song takes a few
  seconds to stretch, so the result is written to a cache file and reused for
  every later run at the same speed. The caller does the work off the game
  loop -- the app must not freeze on a tempo key, which is a fault this
  project has already shipped once.
"""

import hashlib
import wave
from pathlib import Path
from typing import Callable

import numpy as np

# ~46 ms at 44.1 kHz. Long enough to hold several periods of a low guitar
# string (82 Hz is 12 ms), short enough that a drum hit stays a drum hit.
WINDOW = 2048
# Fixed synthesis hop at half the window: a Hann window overlapped by half
# sums to a constant, so the output needs no normalisation pass.
HOP_OUT = WINDOW // 2
# How far a window may slide to find the place it best continues the last.
# This is the "waveform similarity" in WSOLA; without it the overlap-add
# cancels its own periods and the result sounds hollow.
SEARCH = 256
# Cached stretches to keep. One per (file, speed) the player has used; a
# handful of songs at a couple of speeds each, and old ones are not worth
# the disk.
MAX_CACHED = 24


class Cancelled(RuntimeError):
    """The stretch was abandoned because nobody wants it any more."""


def stretch(samples: np.ndarray, factor: float | Callable[[float], float],
            progress: Callable[[float], bool] | None = None) -> np.ndarray:
    """Make the audio `factor` times as long at the same pitch.

    `samples` is float32, mono `(n,)` or `(n, channels)`. A factor above 1
    makes it longer (slower playback), below 1 shorter.

    `factor` may also be a FUNCTION of the position in the original, as a
    fraction 0..1. A band that played without a click needs that: measured
    on the player's own song, the recording runs 6 ms per second slow for
    the first three minutes and 13 ms per second fast for the next eighty
    seconds, and one constant factor through both is worth nothing at all --
    a straight line through the two ends corrects by 0.0 % and leaves 1.07 s
    standing in the middle.

    `progress` is called with how far along this is, 0 to 1, and may return
    False to abandon the work -- which raises `Cancelled`. Both exist for the
    same reason: a whole song takes five to twenty seconds, and the player is
    sitting in silence for all of it. They deserve to see it moving, and
    stepping the tempo down three times should not build three copies before
    reaching the one they asked for.
    """
    if samples.ndim == 1:
        samples = samples[:, None]
    n, channels = samples.shape
    varying = callable(factor)
    if n < 4 * WINDOW:
        return samples.copy()
    if not varying and abs(factor - 1.0) < 1e-3:
        return samples.copy()
    at = factor if varying else (lambda _fraction: factor)
    biggest = (max(at(i / 32.0) for i in range(33)) if varying else factor)

    window = np.hanning(WINDOW + 1)[:-1].astype(np.float32)[:, None]
    # The similarity search compares one signal, not each channel separately:
    # sliding the two sides of a stereo image by different amounts would tear
    # it apart.
    mono = samples.mean(axis=1).astype(np.float32)

    # Allocated for the fastest stretch anywhere in the piece and trimmed to
    # what was actually written: with a factor that varies there is no single
    # length to compute up front, and guessing short would cut the song off.
    out_len = int(n * biggest) + WINDOW
    out = np.zeros((out_len, channels), dtype=np.float32)

    read = 0.0
    write = 0
    frames = 0
    previous = None
    while write + WINDOW <= out_len and int(read) + WINDOW <= n:
        # Reported every so often rather than every window: the callback
        # crosses a thread boundary and the answer cannot change that fast.
        frames += 1
        if progress is not None and frames % 64 == 0:
            if progress(write / out_len) is False:
                raise Cancelled()
        base = int(read)
        if previous is None:
            position = base
        else:
            # What would have come next if the previous window had simply
            # played on. The window nearest THAT is the one that continues it
            # without a phase jump.
            wanted = previous + HOP_OUT
            reference = mono[wanted:wanted + WINDOW]
            position = _best_offset(mono, reference, base, n)
        out[write:write + WINDOW] += samples[position:position + WINDOW] * window
        previous = position
        write += HOP_OUT
        read += HOP_OUT / max(1e-6, at(read / n))
    # The loop stops one window short of the end, so the output is trimmed to
    # where the input WOULD have taken it: whatever is left over, stretched
    # at the rate in force there. With a constant factor that is the old
    # `n * factor` exactly; with a varying one there is no closed form and
    # this is the same answer computed forward.
    tail = max(0.0, n - read) * at(min(1.0, read / n))
    return out[:max(1, min(out_len, int(round(write + tail))))]


def _best_offset(mono: np.ndarray, reference: np.ndarray,
                 base: int, n: int) -> int:
    """Where near `base` the audio best continues `reference`.

    Cross-correlation by FFT rather than directly: a four-minute song is some
    thirteen thousand windows and five hundred candidate shifts each, which
    walked directly is billions of multiplications and turns a few seconds of
    work into a minute of it.
    """
    length = len(reference)
    if length < WINDOW // 2:
        return base
    low = max(0, base - SEARCH)
    high = min(n - WINDOW, base + SEARCH)
    if high <= low:
        return max(0, min(base, n - WINDOW))
    segment = mono[low:high + length]
    size = 1 << int(np.ceil(np.log2(len(segment) + length)))
    correlation = np.fft.irfft(
        np.fft.rfft(segment, size) * np.conj(np.fft.rfft(reference, size)),
        size)[:high - low + 1]
    # Divided by the local loudness, or every window in a quiet passage snaps
    # to the loudest thing within reach and the stretch stutters on it.
    energy = np.concatenate(([0.0], np.cumsum(segment.astype(np.float64) ** 2)))
    local = energy[length:length + len(correlation)] - energy[:len(correlation)]
    return low + int(np.argmax(correlation / np.sqrt(local + 1e-9)))


def rate_curve(plan: tuple[tuple[float, float], ...]
               ) -> Callable[[float], float] | None:
    """A stretch factor as a function of where we are in the recording.

    `plan` is ((fraction through the song, how fast the recording runs
    there), ...) sorted by fraction. Between two entries the rate is the
    earlier one -- a step rather than a ramp, because the anchors the player
    set are the only places the truth is known and interpolating between
    them invents a curve nobody measured.
    """
    if not plan:
        return None

    def at(fraction: float) -> float:
        rate = plan[0][1]
        for where, value in plan:
            if fraction < where:
                break
            rate = value
        return 1.0 / max(1e-6, rate)

    return at


def pitch_shift(samples: np.ndarray, semitones: float,
                progress: Callable[[float], bool] | None = None
                ) -> np.ndarray:
    """The same recording a few semitones higher or lower, same LENGTH.

    A tab written in Drop C is played on a Drop D guitar with the same fret
    numbers, sounding a tone higher -- so the recording has to move with the
    player, not the other way round.

    Stretch by the pitch ratio, then read the result back at that ratio: the
    two length changes cancel exactly, which matters more than it sounds.
    An unchanged length means every sync point, every offset and the whole
    sync map still describe this file. Nothing has to be measured again.

    The shift itself is arithmetic and is exact. Measured against a sine of
    known pitch: +2, -2, +1 and +5 semitones all land within 0.13 cents,
    which is a thousandth of a semitone. What it costs is not accuracy but
    the WSOLA artefacts the practice speed already has, at a far smaller
    factor -- a tone is 1.12, where 50 % speed is 2.0.
    """
    if not semitones:
        return samples
    ratio = 2.0 ** (semitones / 12.0)
    return _resample(stretch(samples, ratio, progress), ratio, len(samples))


def _resample(samples: np.ndarray, ratio: float, out_n: int) -> np.ndarray:
    """Read `samples` back at `ratio` times the rate, into `out_n` frames.

    Length and pitch both move, which is what makes it the other half of a
    pitch shift: the stretch put the length back where a resample takes it.
    """
    index = np.arange(max(0, out_n), dtype=np.float64) * ratio
    low = np.clip(index.astype(np.int64), 0, max(0, len(samples) - 2))
    frac = (index - low).astype(np.float32).reshape(-1, 1)
    return (samples[low] * (1.0 - frac)
            + samples[low + 1] * frac).astype(np.float32)


def cache_name(path: Path, tempo_factor: float,
               plan: tuple[tuple[float, float], ...] = (),
               semitones: float = 0.0) -> str:
    """A file name that changes when the recording or the speed does."""
    try:
        stat = path.stat()
        stamp = f"{stat.st_size}_{int(stat.st_mtime)}"
    except OSError:
        stamp = "0_0"
    # The tempo goes in the HASH, not only in the readable part. That part
    # is rounded to whole percent, which was enough while the speed moved in
    # 5 % steps -- and silently wrong the moment a per-song speed correction
    # arrived: 0.9891 and 0.9932 both read "099" and would have shared one
    # file, so a second attempt at syncing a recording would quietly play
    # the first attempt's copy.
    shape = ";".join(f"{a:.4f}:{b:.6f}" for a, b in plan)
    key = hashlib.sha1(
        f"{path.resolve()}|{stamp}|{tempo_factor:.6f}|{shape}"
        f"|{semitones:+.3f}".encode()
    ).hexdigest()[:12]
    return (f"{path.stem[:32]}_{int(round(tempo_factor * 100)):03d}"
            f"{f'_{semitones:+.0f}st' if semitones else ''}_{key}.wav")


def build(path: Path, tempo_factor: float, cache_dir: Path,
          progress: Callable[[float], bool] | None = None,
          plan: tuple[tuple[float, float], ...] = (),
          semitones: float = 0.0) -> Path:
    """Return a WAV of `path` at this speed and pitch, building it if needed.

    Blocking and slow by design -- seconds for a whole song. Call it off the
    game loop, and pass `progress` so the player can see it moving.
    """
    cache_dir = Path(cache_dir)
    target = cache_dir / cache_name(Path(path), tempo_factor, plan, semitones)
    if target.exists():
        return target
    if progress is not None and progress(0.0) is False:
        raise Cancelled()
    samples, rate = _decode(Path(path))
    # ONE stretch for the speed and the pitch together, not one each. A pitch
    # shift is a stretch by the pitch ratio read back at that ratio, and a
    # resample is uniform -- so it leaves the position of every sample as a
    # FRACTION of the file exactly where it was, and the rate curve composes
    # with it unchanged. Measured on four minutes of audio: 80 % speed and
    # +2 semitones is 14.0 s done separately and 6.7 s done together, and it
    # is the better of the two on its own terms as well, since a sample only
    # passes through WSOLA once instead of twice.
    ratio = 2.0 ** (semitones / 12.0) if semitones else 1.0
    curve = rate_curve(plan)
    if curve is None:
        stretched = stretch(samples, ratio / tempo_factor, progress)
    else:
        stretched = stretch(
            samples, lambda p: ratio * curve(p) / tempo_factor, progress)
    if semitones:
        # Back down to the pitch that was asked for. The length it lands on
        # is the one the speed alone would have given, so every offset and
        # every sync point still describes this file.
        stretched = _resample(stretched, ratio,
                              int(round(len(stretched) / ratio)))
    del samples                                # a whole song, twice, is real
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Written beside the target and renamed, so a stretch interrupted halfway
    # never leaves a half-written file that later looks like a cache hit.
    partial = target.with_suffix(".part")
    try:
        _write_wav(partial, stretched, rate)
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    _prune(cache_dir)
    return target


def _decode(path: Path) -> tuple[np.ndarray, int]:
    """The whole file as float32 samples, at the mixer's own rate.

    Decoded through SDL_mixer, which is already loaded and already the thing
    that will play the result. Not every format it can STREAM it can also
    decode into memory, so this raises for a file that has to be converted
    first -- and the caller says so on screen rather than falling silent.
    """
    import pygame
    from pickhero.audio import output
    output.ensure_mixer()
    sound = pygame.mixer.Sound(str(path))
    raw = pygame.sndarray.array(sound)
    rate = pygame.mixer.get_init()[0]
    samples = np.asarray(raw, dtype=np.float32) / 32768.0
    if samples.ndim == 1:
        samples = samples[:, None]
    return samples, rate


def _write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    """In blocks, because a whole song converted in one go is copied whole.

    Five minutes of stereo is 130 MB as float32, and this app is written for
    machines that have not got several of those to spare.
    """
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(samples.shape[1])
        handle.setsampwidth(2)
        handle.setframerate(rate)
        for start in range(0, len(samples), rate * 10):
            block = np.clip(samples[start:start + rate * 10], -1.0, 1.0)
            handle.writeframes((block * 32767.0).astype("<i2").tobytes())


def _prune(cache_dir: Path) -> None:
    """Keep the cache from growing without limit."""
    files = sorted(cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    for stale in files[MAX_CACHED:]:
        try:
            stale.unlink()
        except OSError:
            pass
