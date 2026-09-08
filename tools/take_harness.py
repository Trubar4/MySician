"""Push a recorded take through the real audio path, the way the app does.

Four check tools had a copy of this each, and all four had the same fault --
which is the "four readers of one plan" this project has already paid for
elsewhere. One implementation now, so a fix reaches all of them.

**Strikes and windows have to be interleaved, not batched.** The app drains
both queues every frame; a tool that pushes a whole take through and then
hands the matcher all the windows at once is not running the same code. Two
bounded structures make that difference real, and both drop the OLDEST entry:

- `AudioCapture.strike_queue` holds `MAX_QUEUED_WINDOWS` (16). Collecting
  once at the end keeps only the last sixteen strikes of the take.
- `NoteMatcher._pending_rescues` holds 32. Feeding every strike before any
  window means a 134-strike take arrives with 70 holds and only the last 32
  can still be answered.

Measured on the arpeggio take, changing nothing but the order the same events
were handed over: **46.0 % of the written notes credited batched, 61.5 %
interleaved.** Every arpeggio figure this project recorded before that was
measured through the batched version and understates the rescue.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from pickhero.audio.input import RING_SECONDS, AudioCapture, _AudioRing
from pickhero.config import Config

HOP = 512


def read_mono(path: Path) -> np.ndarray:
    """A take as mono float32, whatever it was recorded with."""
    with wave.open(str(path)) as handle:
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


def events(path: Path, sample_rate: int, config: Config | None = None
           ) -> list[tuple[str, object]]:
    """Every strike and window the audio thread produced, in ARRIVAL order.

    `[("strike", TimestampedNote), ("window", StrikeWindow), ...]` -- the same
    sequence, in the same order, the app's game loop would have seen.
    """
    audio = read_mono(Path(path))
    cap = AudioCapture(config if config is not None else Config())
    cap._sample_rate = sample_rate
    cap.detector.sample_rate = sample_rate
    cap.detector.reset()
    cap._onset_collector.reset()
    cap._ring = _AudioRing(int(sample_rate * RING_SECONDS))

    out: list[tuple[str, object]] = []

    def drain() -> None:
        for strike in cap.get_notes():
            if strike.note.is_onset:
                out.append(("strike", strike))
        for window in cap.get_strike_windows():
            out.append(("window", window))

    for i in range(0, len(audio) - HOP + 1, HOP):
        cap._audio_callback(audio[i:i + HOP].reshape(-1, 1), HOP, None, None)
        drain()
    drain()
    return out


def feed(matcher, take: list[tuple[str, object]], offset_ms: float = 0.0,
         tempo: float = 1.0) -> None:
    """Hand the take to the matcher in the order it happened.

    `offset_ms` is where the song starts inside the recording and `tempo` is
    the speed it was played at -- both found by the alignment, neither
    guessed. The matcher MUTATES the timestamps it is given, so a take that
    is scored twice must be captured twice.
    """
    for kind, item in take:
        item.timestamp_ms *= tempo
        if kind == "strike":
            matcher.process_detected_notes(
                [item], item.timestamp_ms - offset_ms)
        else:
            matcher.process_strike_windows([item])


def strikes_of(take: list[tuple[str, object]]) -> list:
    return [item for kind, item in take if kind == "strike"]


def windows_of(take: list[tuple[str, object]]) -> list:
    return [item for kind, item in take if kind == "window"]
