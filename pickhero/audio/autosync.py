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
# The CEILING on how far a window may sit from the fitted line, not the
# threshold itself. It was fitted against a wrong-chorus match, which lands 14
# to 28 s away -- so it is three seconds wide, and blind to a spike of one.
OUTLIER_S = 3.0
# The threshold is the scatter the SONG shows, because that is the only thing
# that says what a disagreement is. Bad Omens' "Like a Villain": nine of its
# twelve stored points sit within 52 ms of the fitted line (MAD 44 ms) and
# three are spikes of 175, 561 and 1349 ms -- 4, 13 and 31 times that scatter,
# and every one of them comfortably inside three seconds. Each poisons the two
# sections around it: the map then implies +22 %, -7.3 % and +9.3 %, one of
# them clamped at MAX_RATE, and the picture jumps by up to 1.35 s. Dropping
# the three brings the map's worst error from 1352 ms to 52 ms. The factor has
# to keep 52 ms and drop 175 ms, so anything from 1.2 to 4.0 works and 3.0 is
# the middle of what was measured.
SPIKE_FACTOR = 3.0
# ...and never rejects a reading nobody could see. 100 ms is where picture and
# sound stop reading as one event, and it is what keeps a song whose windows
# agree almost exactly from throwing away the one that agrees least.
SPIKE_FLOOR_S = 0.1
# How fast a recording of the same performance may run against the tab. The
# measured mismatch on a real song is about 1 %; this is five times that, and
# still nowhere near what a wrong match implies. Without a bound, a staircase
# of windows that each matched the WRONG repeat of a riff -- Godsmack's Awake
# stepped -17.9 s to +10.9 s in plateaus 8 s apart -- fits a straight line at
# -12 % perfectly, so the outlier filter finds nothing to drop and every one
# of them is stored as a sync point.
MAX_DRIFT_RATE = 0.05
# Two windows closer together than this cannot say anything about a rate: the
# offset moves in fractions of a second and the noise is comparable.
MIN_SLOPE_SPAN_S = 30.0
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
    """Slope and intercept of the line most windows agree on.

    Two things, and the second is what a median alone cannot do.

    The SLOPE is the median of pairwise slopes over pairs far enough apart to
    mean anything, and only over slopes a recording can actually have. A
    median is robust to a minority of wrong points; it is not robust to a
    majority, and a song that repeats produces exactly that -- whole clusters
    of windows matching the wrong repeat, each cluster consistent with the
    next, adding up to a "drift" of 12 %.

    The OFFSET is then the one the most windows sit near, not the median of
    them. Where half the readings are wrong the median lands between the two
    answers and belongs to neither; the largest agreeing group is an answer
    somebody measured.
    """
    if not xs:
        return 0.0, 0.0
    slopes = [(ys[j] - ys[i]) / (xs[j] - xs[i])
              for i in range(len(xs)) for j in range(i + 1, len(xs))
              if xs[j] - xs[i] >= MIN_SLOPE_SPAN_S]
    usable = [s for s in slopes if abs(s) <= MAX_DRIFT_RATE]
    slope = float(np.median(usable)) if usable else 0.0
    # Consensus on the offset: try the line through each reading in turn and
    # keep the one the most readings fall in with.
    offsets = [y - slope * x for x, y in zip(xs, ys)]
    best, agreed = float(np.median(offsets)), -1
    for candidate in offsets:
        count = sum(1 for o in offsets if abs(o - candidate) <= OUTLIER_S)
        if count > agreed:
            best, agreed = candidate, count
    # Centre it on the group it found, so the line sits in the middle of the
    # agreeing readings rather than on whichever one was tried first.
    inliers = [o for o in offsets if abs(o - best) <= OUTLIER_S]
    return slope, float(np.median(inliers)) if inliers else best


def spike_tolerance(residuals: Sequence[float]) -> float:
    """How far a window may sit from the line before it is not a reading.

    The scatter the song itself shows, measured as the median absolute
    residual, so it asks "does this window disagree with the others" rather
    than "is it more than N seconds out" -- which is a question only a fixed
    threshold has to answer, and which cannot be answered once for every song.

    Bounded at both ends: never below what nobody could see, never above what
    a wrong-chorus match implies.
    """
    if not residuals:
        return OUTLIER_S
    mad = float(np.median(np.abs(np.asarray(residuals, dtype=float))))
    return min(OUTLIER_S, max(SPIKE_FLOOR_S, SPIKE_FACTOR * mad))


def usable_rows(rows: Iterable[tuple[float, float, float]]
                ) -> list[tuple[float, float]]:
    """Drop the windows that are not readings, and then the outliers.

    Two filters and they answer different questions: the margin says the
    window could not tell one chorus from another, and the residual says this
    one disagrees with all the others. Neither is a threshold on the
    correlation itself, which would have to be fitted per song and would then
    be measuring the song.

    The residual filter is scaled to the scatter the song shows rather than
    fixed, because a fixed one has to be set for the worst case it must catch
    -- a wrong chorus, tens of seconds away -- and is then blind to a spike of
    one second on a song whose windows agree to within fifty milliseconds.
    """
    kept = [(at, lag) for at, lag, margin in rows if margin >= MIN_MARGIN]
    if len(kept) < MIN_WINDOWS:
        return []
    slope, intercept = _robust_line([a for a, _ in kept], [b for _, b in kept])
    residuals = [lag - (slope * at + intercept) for at, lag in kept]
    tolerance = spike_tolerance(residuals)
    return [(at, lag) for (at, lag), gap in zip(kept, residuals)
            if abs(gap) <= tolerance]


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
