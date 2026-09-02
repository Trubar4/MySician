"""Find the places where the tab and the recording agree, by listening.

Setting sync points by hand works and is what the other tools ask for -- Go
PlayAlong says two to five is usually enough -- but five points per song,
placed by ear, is a chore before every song rather than after the first. The
measurement that finds them already existed here as a diagnostic
(`tools/check_song_sync.py`); this is the same measurement with its answer
handed to the app instead of printed.

**Chroma, not onsets.** Note attacks do not survive a dense mix: matching them
put 133 of 343 strikes on the grid and the best offset jumped between -39 s and
+37 s at constant confidence. Comparing pitch-class energy instead gives a
smooth curve with residuals under 0.4 s.

**A pop song rhymes with itself**, so a lag search always finds something. A
window whose best lag beats its nearest rival by too little is dropped and
counted rather than averaged into a trend it would poison; and what survives
is fitted robustly, so an outlier that gets through cannot move the answer.
Nothing pretends to identify an outlier in advance.

**The points that come out are all measured.** The curve is thinned by
dropping the ones the straight line between their neighbours already predicts
to within `SIMPLIFY_MS` -- so what is stored is the fewest MEASURED places
that reproduce the measurement, not a model of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

from pickhero.tabs.timeline import Timeline

# The analysis frame. 8192 at 44.1 kHz is 186 ms -- long enough to resolve a
# bass note's pitch class, short enough that a chord change is not smeared.
FRAME = 8192
HOP = 2048
# Windows this long are compared against the recording: long enough to carry a
# phrase, short enough that a real drift shows as a slope rather than a blur.
WINDOW_S = 20.0
STEP_S = 6.0
# How far the recording may be from where the tab says. Eight minutes would be
# the offset range the app allows, but a search that wide over a song that
# rhymes with itself is mostly a way of finding the wrong chorus; the manual
# offset is there for a tab that covers only the solo.
MAX_LAG_S = 40.0
# How far from the best lag a rival has to be to count as a rival at all.
RUNNER_UP_GUARD_S = 5.0
# A window whose best lag beats its rival by less than this is the song
# rhyming with itself. Fitted on the player's own files, where the good
# windows clear 0.03 and the three that matched the wrong chorus do not.
MIN_MARGIN = 0.012
# A window sitting further than this from the robust line is not a reading.
OUTLIER_S = 3.0
# A point the line between its neighbours already predicts this well is not
# worth storing. 25 ms is a quarter of the 100 ms where picture and sound
# stop reading as one event.
SIMPLIFY_MS = 25.0
# Below this many usable windows there is no curve, only noise.
MIN_WINDOWS = 3


def decode(path: str | Path, samplerate: int = 44100) -> tuple[np.ndarray, int]:
    """The whole recording as mono float32, through SDL.

    The same decoder the app plays it with, so a file this can read is a file
    the app can play and the other way round.
    """
    import pygame
    started = not pygame.mixer.get_init()
    if started:
        import os
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        pygame.mixer.init(frequency=samplerate, size=-16, channels=2)
    rate = pygame.mixer.get_init()[0]
    try:
        arr = pygame.sndarray.array(
            pygame.mixer.Sound(str(path))).astype(np.float32) / 32768.0
    finally:
        if started:
            pygame.mixer.quit()
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    return arr, rate


def _class_matrix(samplerate: int) -> np.ndarray:
    """(12, bins): which pitch class each FFT bin belongs to.

    A matrix rather than twelve masked sums per frame -- the same arithmetic,
    and it is what makes this fast enough to run while the player waits.
    """
    freqs = np.fft.rfftfreq(FRAME, 1.0 / samplerate)
    midi = np.full(len(freqs), -1.0)
    audible = freqs > 25
    midi[audible] = np.round(12 * np.log2(freqs[audible] / 440.0) + 69)
    # E1 to E7: below is rumble, above is mostly cymbals and air.
    klass = np.where((midi >= 28) & (midi <= 100), midi % 12, -1)
    out = np.zeros((12, len(freqs)), dtype=np.float32)
    for k in range(12):
        out[k, klass == k] = 1.0
    return out


def chroma_of_audio(samples: np.ndarray, samplerate: int,
                    progress: Callable[[float], bool] | None = None,
                    ) -> tuple[np.ndarray, float]:
    """Pitch-class energy over time, and how many rows there are per second."""
    frames = max(0, (len(samples) - FRAME) // HOP)
    matrix = _class_matrix(samplerate)
    window = np.hanning(FRAME).astype(np.float32)
    out = np.zeros((frames, 12), dtype=np.float32)
    # In blocks, so one rfft call covers a second of audio rather than a frame
    # of it, and so the progress line moves.
    block = 256
    for start in range(0, frames, block):
        stop = min(frames, start + block)
        rows = np.lib.stride_tricks.sliding_window_view(
            samples, FRAME)[start * HOP:stop * HOP:HOP]
        mag = np.abs(np.fft.rfft(rows * window, axis=1))
        out[start:stop] = mag @ matrix.T
        if progress is not None and progress(stop / max(1, frames)) is False:
            raise Cancelled()
    out /= (out.sum(axis=1, keepdims=True) + 1e-9)
    return out, samplerate / HOP


# An analysis frame covers FRAME samples but is indexed by its FIRST one, so
# the recording's chroma reports every event half a frame early -- 93 ms at
# 44.1 kHz, against the 100 ms this whole feature exists to get under. The
# tab's chroma has no such window, so the two sides do not describe the same
# stretch of time and the answer comes out biased. Measured on a synthesised
# recording with NO drift at all -- a control that must read zero -- the bias
# was +50 ms with a scatter of only 10 ms: a shift, not noise.
FRAME_CENTRE_ROWS = FRAME / (2.0 * HOP)


def chroma_of_timeline(timeline: Timeline, fps: float) -> np.ndarray:
    """The tab as the same twelve bins.

    Every written note, at the moment the app itself will play it -- so this
    is measured against the app's own clock rather than against a re-reading
    of the file.
    """
    notes = [(n.timestamp_ms / 1000.0, max(n.duration_ms / 1000.0, 0.15),
              n.midi_note) for n in timeline.notes]
    if not notes:
        return np.zeros((0, 12), dtype=np.float32)
    span = max(t + d for t, d, _ in notes)
    out = np.zeros((int(span * fps) + 50, 12), dtype=np.float32)
    for start, duration, midi in notes:
        a = int(start * fps)
        b = min(len(out), int((start + duration) * fps))
        if 0 <= a < b:
            out[a:b, midi % 12] += 1.0
    # Smeared over the same span the recording's analysis window covers, so
    # both sides answer for the same stretch of time. Without it the sharp
    # tab and the smeared recording line up at whatever the music's own
    # envelope happens to favour.
    span = max(1, int(round(FRAME / HOP)))
    if span > 1:
        window = np.hanning(span + 2)[1:-1].astype(np.float32)
        window /= window.sum()
        padded = np.pad(out, ((span, span), (0, 0)), mode="edge")
        out = np.stack([np.convolve(padded[:, k], window, mode="same")
                        for k in range(12)], axis=1)[span:-span]
    out /= (out.sum(axis=1, keepdims=True) + 1e-9)
    return out


def chroma_of_tab_file(path: str | Path, fps: float) -> np.ndarray:
    """Every pitched track of the file, as one set of bins.

    A recording is the whole band, so aligning one guitar track against it
    throws away most of what is there to match. Percussion is left out: a
    drum kit has no pitch classes, only noise spread over all twelve.
    """
    from pickhero.tabs.loader import list_tracks, load_gp_file

    merged: list[Timeline] = []
    for info in list_tracks(Path(path)):
        if info.get("is_percussion"):
            continue
        try:
            merged.append(load_gp_file(Path(path), track_index=info["index"]))
        except Exception:                       # a track that will not parse
            continue
    if not merged:
        return np.zeros((0, 12), dtype=np.float32)
    notes = [note for timeline in merged for note in timeline.notes]
    return chroma_of_timeline(
        Timeline(notes, merged[0].metadata, measures=merged[0].measures), fps)


def _norm(a: np.ndarray) -> np.ndarray:
    a = a - a.mean(axis=0, keepdims=True)
    return a / (np.linalg.norm(a, axis=0, keepdims=True) + 1e-9)


def _refine(lags: np.ndarray, scores: np.ndarray, best: int) -> float:
    """The peak between two frames, by a parabola through three scores.

    Without this the answer can only ever be a whole frame -- 46 ms at
    44.1 kHz, and 186 ms at the rate a stretched copy is analysed. Measured
    on a synthesised recording with a known drift, that quantisation alone
    left a 228 ms staircase, which is twice the 100 ms this whole feature
    exists to get under. The correlation peak is smooth, so its top is worth
    asking for.
    """
    if best <= 0 or best >= len(scores) - 1:
        return float(lags[best])
    a, b, c = float(scores[best - 1]), float(scores[best]), float(scores[best + 1])
    bottom = a - 2.0 * b + c
    if bottom >= 0:                    # not a peak; take the sample itself
        return float(lags[best])
    shift = 0.5 * (a - c) / bottom
    if abs(shift) > 1.0:
        return float(lags[best])
    step = float(lags[best + 1] - lags[best])
    return float(lags[best]) + shift * step


class Cancelled(RuntimeError):
    """The measurement was abandoned because nobody wants it any more."""


def drift_curve(tab: np.ndarray, rec: np.ndarray, fps: float,
                progress: Callable[[float], bool] | None = None,
                ) -> list[tuple[float, float, float]]:
    """(tab seconds, lag seconds, margin) per window.

    The lag is where in the RECORDING this window of the tab was found, so a
    positive lag means the recording says it later than the tab does. The
    margin is how far the winner beat its nearest rival, which is the only
    thing that separates a reading from the song rhyming with itself.
    """
    width, step = int(WINDOW_S * fps), int(STEP_S * fps)
    max_lag, guard = int(MAX_LAG_S * fps), int(RUNNER_UP_GUARD_S * fps)
    starts = list(range(0, max(0, len(tab) - width), step))
    rows: list[tuple[float, float, float]] = []
    for done, start in enumerate(starts):
        seg = _norm(tab[start:start + width])
        lags, scores = [], []
        for lag in range(-max_lag, max_lag):
            at = start + lag
            if at < 0 or at + width > len(rec):
                continue
            lags.append(lag)
            scores.append(float((seg * _norm(rec[at:at + width])).sum()) / 12)
        if not scores:
            continue
        scores_a, lags_a = np.array(scores), np.array(lags)
        best = int(np.argmax(scores_a))
        far = np.abs(lags_a - lags_a[best]) > guard
        runner = float(scores_a[far].max()) if far.any() else -1.0
        rows.append((start / fps,
                     (_refine(lags_a, scores_a, best)
                      + FRAME_CENTRE_ROWS) / fps,
                     float(scores_a[best]) - runner))
        if progress is not None and progress((done + 1) / len(starts)) is False:
            raise Cancelled()
    return rows


def _robust_line(xs: Sequence[float], ys: Sequence[float]
                 ) -> tuple[float, float]:
    """Slope and intercept by the median of pairwise slopes.

    Least squares would let one window that matched the wrong chorus drag the
    whole line; this cannot be moved by fewer than half of them.
    """
    slopes = [(ys[j] - ys[i]) / (xs[j] - xs[i])
              for i in range(len(xs)) for j in range(i + 1, len(xs))
              if xs[j] - xs[i] > 1e-9]
    if not slopes:
        return 0.0, float(np.median(ys))
    slope = float(np.median(slopes))
    return slope, float(np.median([y - slope * x for x, y in zip(xs, ys)]))


def usable_rows(rows: Iterable[tuple[float, float, float]]
                ) -> list[tuple[float, float]]:
    """Drop the windows that are not readings, and then the outliers.

    Two filters and they answer different questions: the margin says the
    window could not tell one chorus from another, and the residual says this
    one disagrees with all the others. Neither is a threshold on the
    correlation itself, which would have to be fitted per song and would then
    be measuring the song.
    """
    kept = [(at, lag) for at, lag, margin in rows if margin >= MIN_MARGIN]
    if len(kept) < MIN_WINDOWS:
        return []
    slope, intercept = _robust_line([a for a, _ in kept], [b for _, b in kept])
    return [(at, lag) for at, lag in kept
            if abs(lag - (slope * at + intercept)) <= OUTLIER_S]


def simplify(points: list[tuple[float, float]],
             tolerance_ms: float = SIMPLIFY_MS) -> list[tuple[float, float]]:
    """Drop the points the line between their neighbours already predicts.

    Douglas-Peucker, so what is kept is a subset of what was MEASURED --
    the curve is thinned, never smoothed into something nobody observed.
    """
    if len(points) < 3:
        return list(points)
    first, last = points[0], points[-1]
    run = last[0] - first[0]
    worst, index = -1.0, 0
    for i in range(1, len(points) - 1):
        at, value = points[i]
        predicted = (first[1] if run <= 0 else
                     first[1] + (at - first[0]) * (last[1] - first[1]) / run)
        gap = abs(value - predicted)
        if gap > worst:
            worst, index = gap, i
    if worst <= tolerance_ms:
        return [first, last]
    return (simplify(points[:index + 1], tolerance_ms)[:-1]
            + simplify(points[index:], tolerance_ms))


def points_from_rows(rows: Iterable[tuple[float, float, float]],
                     tolerance_ms: float = SIMPLIFY_MS
                     ) -> list[tuple[float, float]]:
    """The whole reduction: rows of the curve to sync points the app stores.

    A point is `(song ms, offset ms)`, and the offset is the NEGATIVE of the
    lag: a recording that says this passage later than the tab does has to be
    played from earlier in the file, which is what a negative offset means
    here.
    """
    kept = usable_rows(rows)
    if not kept:
        return []
    # The window's own middle, not its start: the lag was measured over the
    # whole 20 s and attributing it to the first frame would put every point
    # ten seconds early.
    middle = [(at + WINDOW_S / 2.0, lag) for at, lag in kept]
    thinned = simplify([(a * 1000.0, -b * 1000.0) for a, b in middle],
                       tolerance_ms)
    return [(round(a, 1), round(b, 1)) for a, b in thinned]


def find_points(timeline: Timeline | str | Path, audio_path: str | Path,
                progress: Callable[[float, str], bool] | None = None,
                tolerance_ms: float = SIMPLIFY_MS
                ) -> tuple[list[tuple[float, float]],
                           list[tuple[float, float, float]]]:
    """Measure this recording against this tab. Returns (points, rows).

    The rows come back as well as the points, because "how many windows were
    usable" is the difference between a song this could not read and a song
    that is already in sync.
    """
    def stage(share: float, base: float, span: float, what: str):
        if progress is None:
            return None
        return lambda fraction: progress(base + span * fraction, what)

    samples, rate = decode(audio_path)
    if progress is not None and progress(0.05, "listening") is False:
        raise Cancelled()
    rec, fps = chroma_of_audio(samples, rate, stage(1, 0.05, 0.45, "listening"))
    if isinstance(timeline, Timeline):
        tab = chroma_of_timeline(timeline, fps)
    else:
        tab = chroma_of_tab_file(timeline, fps)
    if len(tab) == 0 or len(rec) == 0:
        return [], []
    rows = drift_curve(tab, rec, fps, stage(1, 0.5, 0.5, "comparing"))
    return points_from_rows(rows, tolerance_ms), rows
