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


def stretch(samples: np.ndarray, factor: float) -> np.ndarray:
    """Make the audio `factor` times as long at the same pitch.

    `samples` is float32, mono `(n,)` or `(n, channels)`. A factor above 1
    makes it longer (slower playback), below 1 shorter.
    """
    if samples.ndim == 1:
        samples = samples[:, None]
    n, channels = samples.shape
    if abs(factor - 1.0) < 1e-3 or n < 4 * WINDOW:
        return samples.copy()

    window = np.hanning(WINDOW + 1)[:-1].astype(np.float32)[:, None]
    # The similarity search compares one signal, not each channel separately:
    # sliding the two sides of a stereo image by different amounts would tear
    # it apart.
    mono = samples.mean(axis=1).astype(np.float32)

    out_len = int(n * factor) + WINDOW
    out = np.zeros((out_len, channels), dtype=np.float32)
    hop_in = HOP_OUT / factor

    read = 0.0
    write = 0
    previous = None
    while write + WINDOW <= out_len and int(read) + WINDOW <= n:
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
        read += hop_in
    return out[:max(1, int(n * factor))]


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


def cache_name(path: Path, tempo_factor: float) -> str:
    """A file name that changes when the recording or the speed does."""
    try:
        stat = path.stat()
        stamp = f"{stat.st_size}_{int(stat.st_mtime)}"
    except OSError:
        stamp = "0_0"
    key = hashlib.sha1(f"{path.resolve()}|{stamp}".encode()).hexdigest()[:12]
    return f"{path.stem[:32]}_{int(round(tempo_factor * 100)):03d}_{key}.wav"


def build(path: Path, tempo_factor: float, cache_dir: Path) -> Path:
    """Return a WAV of `path` slowed to `tempo_factor`, building it if needed.

    Blocking and slow by design -- seconds for a whole song. Call it off the
    game loop.
    """
    cache_dir = Path(cache_dir)
    target = cache_dir / cache_name(Path(path), tempo_factor)
    if target.exists():
        return target
    samples, rate = _decode(Path(path))
    stretched = stretch(samples, 1.0 / tempo_factor)
    del samples                                # a whole song, twice, is real
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Written beside the target and renamed, so a stretch interrupted halfway
    # never leaves a half-written file that later looks like a cache hit.
    partial = target.with_suffix(".part")
    _write_wav(partial, stretched, rate)
    partial.replace(target)
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
    if not pygame.mixer.get_init():
        pygame.mixer.init()
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
