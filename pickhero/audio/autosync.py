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

from bisect import bisect_left
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
# A jump between two NEIGHBOURING windows that no drift can explain. They sit
# STEP_S apart and the fastest drift allowed is MAX_DRIFT_RATE, so drift can
# move them by at most 0.3 s; a second is three times that. Above it the tab
# and the recording are not the same piece of music at that moment -- a
# repeat one of them does not play, a bar the transcriber added, or a window
# that matched the wrong chorus. Measured on the player's own files: Godsmack
# jumps 13.1 s between 1:06 and 1:36, and What's Up swings +9.9, -34.4, -6.2,
# +21.1 across five windows.
BREAK_S = 1.0
# What a stretch between two breaks has to be before a line is fitted to it.
# Below this it is noise pretending to be a section: What's Up produces
# twenty "sections" of one window each, and a map built from those is worse
# than no map at all, because it looks measured.
MIN_SECTION_WINDOWS = 4
MIN_SECTION_S = 30.0
# What share of a song's windows has to survive before the answer is worth
# storing. Fitted on the three songs to hand and nothing more: Bon Jovi keeps
# 32 of 42 (76 %), Godsmack 29 of 47 (62 %), and What's Up -- four chords
# repeated for four minutes, where the windows match +9.9, -34.4, -6.2 and
# +21.1 s -- keeps 5 of 41 (12 %). A third is the gap between them. Below it
# the map is not thin, it is WRONG: What's Up's came out at -3.02 % drift and
# 5.1 s of correction, and it looked exactly as measured as the good ones.
MIN_USABLE_SHARE = 1.0 / 3.0
# How far the tab and the recording may differ in LENGTH before they are not
# the same transcription at all. Measured on the player's own files: his old
# Thunder tab is 6:21 at 156 BPM in 248 bars against a 4:40 recording -- 36 %
# apart -- and his new one is 4:40 in 91 bars at 78 BPM, matching to a tenth
# of a second. No offset, rate or map can bridge the first, and a whole
# session was spent on its sync before anyone compared the two numbers. Ten
# per cent is far outside the 5 % the drift model allows, so nothing this
# rejects could have been fitted anyway.
MAX_LENGTH_MISMATCH = 0.10
# How far a window may sit from the constant before it is not agreeing with a
# made per-bar map. Wider than the map's own error -- measured at 80 to 92 ms
# median against the player's Thunder recording -- and far narrower than a
# wrong chorus, which is tens of seconds.
BAR_MAP_AGREE_S = 3.0


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


def breaks_in(kept: Sequence[tuple[float, float]]
              ) -> list[tuple[float, float]]:
    """(when, by how much) the readings jump, in seconds.

    Not an outlier test. An outlier is one window that disagrees with the
    rest; a BREAK is the whole curve moving and staying moved, and the two
    need opposite treatment -- an outlier is dropped, a break is a place to
    stop fitting and start again.

    Told apart by what drift could do: neighbours are STEP_S apart and drift
    is bounded, so anything past that budget is not the recording running at
    a different speed, it is the two of them being different music.
    """
    out = []
    for (at, lag), (next_at, next_lag) in zip(kept, kept[1:]):
        budget = max(BREAK_S, MAX_DRIFT_RATE * (next_at - at))
        if abs(next_lag - lag) > budget:
            out.append((next_at, next_lag - lag))
    return out


def sections(kept: Sequence[tuple[float, float]]
             ) -> list[list[tuple[float, float]]]:
    """The readings split where they jump, longest run first in time order."""
    if not kept:
        return []
    at_break = {at for at, _ in breaks_in(kept)}
    out: list[list[tuple[float, float]]] = [[]]
    for reading in kept:
        if reading[0] in at_break and out[-1]:
            out.append([])
        out[-1].append(reading)
    return [part for part in out if part]


def big_enough(part: Sequence[tuple[float, float]]) -> bool:
    """Whether a section is worth fitting a line to."""
    return (len(part) >= MIN_SECTION_WINDOWS
            and part[-1][0] - part[0][0] >= MIN_SECTION_S)


def merged_sections(parts: Sequence[Sequence[tuple[float, float]]]
                    ) -> list[list[tuple[float, float]]]:
    """Fold each section too small to fit into the big one beside it.

    **A break is the curve moving and STAYING moved.** A single window that
    jumps and comes straight back is an outlier, and splitting there strands
    the good readings after it in a section too small to fit -- so they are
    thrown away with it.

    Measured on the player's own file: Bon Jovi's outro has five such jumps
    of about 14 s, each one window wide. Splitting at all of them cost 12
    readings and a minute and a half of coverage, and every one of the bad
    ones is dropped by the section's own spike filter anyway. Godsmack's
    real breaks survive this, because they are followed by four windows and
    thirty seconds that agree with each other.
    """
    out: list[list[tuple[float, float]]] = []
    for part in parts:
        if big_enough(part) or not out:
            out.append(list(part))
        else:
            out[-1] += list(part)
    # A run of small sections before the first big one has nothing to fold
    # into, so it is kept and judged on its own -- which usually drops it.
    if out and not big_enough(out[0]) and len(out) > 1:
        out[1] = out[0] + out[1]
        out.pop(0)
    return out


def usable_rows(rows: Iterable[tuple[float, float, float]]
                ) -> list[tuple[float, float]]:
    """Drop the windows that are not readings, and then the outliers.

    Three filters now, and they answer three different questions.

    - The **margin** says the window could not tell one chorus from another.
    - The **breaks** say the two stopped being the same piece of music here.
      This one is new, and it is what the whole thing was getting wrong: a
      single line was fitted to the entire song, so a tab that repeats a
      section the record does not made every window after the repeat look
      like an outlier. Measured on the player's own files: Godsmack kept 11
      of 47 windows and covered 0:00-1:26 of 4:56, with the remaining three
      and a half minutes extrapolated from a line fitted to the first.
    - The **residual**, WITHIN a section, says this one window disagrees with
      the others around it. Scaled to the scatter that section shows rather
      than fixed, because a fixed one has to be set for the worst case it
      must catch and is then blind to a spike of one second on a song whose
      windows agree to fifty milliseconds.

    A section too small to fit is dropped whole rather than fitted: four
    windows over thirty seconds is the least that can say anything, and
    below it a "section" is one reading calling itself a trend.
    """
    kept = [(at, lag) for at, lag, margin in rows if margin >= MIN_MARGIN]
    if len(kept) < MIN_WINDOWS:
        return []
    out: list[tuple[float, float]] = []
    for part in merged_sections(sections(kept)):
        if not big_enough(part):
            continue
        slope, intercept = _robust_line([a for a, _ in part],
                                        [b for _, b in part])
        residuals = [lag - (slope * at + intercept) for at, lag in part]
        tolerance = spike_tolerance(residuals)
        out += [(at, lag) for (at, lag), gap in zip(part, residuals)
                if abs(gap) <= tolerance]
    return out


def read_report(rows: Sequence[tuple[float, float, float]]) -> dict:
    """What the listening could and could not read, as numbers.

    Separate from the points because the app has to be able to SAY what
    happened. "28 of 51 windows usable" is a number nobody can act on; "the
    tab and the recording part company at 1:36" is a place to put a point.
    """
    by_margin = [(at, lag) for at, lag, margin in rows if margin >= MIN_MARGIN]
    parts = merged_sections(sections(by_margin))
    used = [part for part in parts if big_enough(part)]
    kept = usable_rows(rows)
    dropped = [(part[0][0], part[-1][0] + WINDOW_S)
               for part in parts if not big_enough(part)]
    windows = len(rows)
    share = len(kept) / windows if windows else 0.0
    return {
        "readable": bool(used) and share >= MIN_USABLE_SHARE,
        "share": share,
        "windows": len(rows),
        "ambiguous": len(rows) - len(by_margin),
        # From the readings the MAP is built on, not from the raw sections.
        # A section can be big enough to fit and still contain a wrong match
        # at its edge -- Thunder's first 36 s hold six readings, two at +1.2 s
        # and the rest at -23.5 s -- and comparing raw endpoints then reported
        # a 24.9 s break that the stored points do not have. A break the map
        # does not contain is a line that lies.
        "breaks": breaks_in(kept),
        "sections": len(parts),
        "sections_used": len(used),
        "unreadable": dropped,
        "usable": len(kept),
        "covered": ((kept[0][0], kept[-1][0] + WINDOW_S) if kept else None),
        "song_s": (rows[-1][0] + WINDOW_S) if rows else 0.0,
    }


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


def bar_lag(song_ms: float, bar_starts: Sequence[float],
            bar_times: Sequence[float]) -> float:
    """Where a made map says this moment is, in seconds, before the constant.

    Piecewise linear between bar lines, which is what a per-bar map IS: the
    map knows where bar 34 begins and says nothing about the middle of it.
    """
    if not bar_starts or not bar_times:
        return 0.0
    last = min(len(bar_starts), len(bar_times)) - 1
    if last < 1:
        return float(bar_times[0]) - song_ms / 1000.0
    at = song_ms / 1000.0
    i = max(0, min(last - 1, bisect_left(bar_starts, song_ms) - 1))
    run = bar_starts[i + 1] - bar_starts[i]
    share = 0.0 if run <= 0 else (song_ms - bar_starts[i]) / run
    where = bar_times[i] + (bar_times[i + 1] - bar_times[i]) * share
    return where - at


def align_to_bar_times(timeline: Timeline, audio_path: str | Path,
                       bar_times: Sequence[float],
                       progress: Callable[[float, str], bool] | None = None,
                       tolerance_ms: float = SIMPLIFY_MS) -> dict:
    """Fit a made per-bar map to the recording the player actually has.

    The points are times in a VIDEO. If that video and this file are the same
    master they differ by ONE constant, so the whole job is to find a single
    number -- and the way the windows agree about it is a free check on
    whether the map belongs to this recording at all.

    Done by warping the tab through the map and then measuring what is left,
    which reuses the listening rather than inventing a second way to compare
    two things.
    """
    bar_starts = [m.start_ms for m in timeline.measures]
    report: dict = {
        "source": "songsterr", "bars": len(bar_times),
        "measures": len(bar_starts), "points": [],
        "readable": False, "windows": 0, "usable": 0, "ambiguous": 0,
        "breaks": [], "covered": None, "song_s": 0.0, "wrong_length": False,
        "sections": 0, "sections_used": 0, "unreadable": [], "share": 0.0,
    }
    if len(bar_times) < 2 or len(bar_starts) < 2:
        return report
    # A map with a different number of bars is a map of a different tab --
    # the repeats expanded differently, or a revision that moved on. Said
    # rather than stretched over, because stretching it would be silent and
    # wrong everywhere after the first difference.
    if len(bar_times) != len(bar_starts):
        report["wrong_bars"] = True
        return report
    report["wrong_bars"] = False

    warped = replace_times(timeline, bar_starts, bar_times)
    samples, rate = decode(audio_path)
    if progress is not None and progress(0.05, "listening") is False:
        raise Cancelled()
    rec, fps = chroma_of_audio(
        samples, rate,
        (lambda f: progress(0.05 + 0.45 * f, "listening"))
        if progress else None)
    tab = chroma_of_timeline(warped, fps)
    if len(tab) == 0 or len(rec) == 0:
        return report
    rows = drift_curve(tab, rec, fps,
                       (lambda f: progress(0.5 + 0.5 * f, "comparing"))
                       if progress else None)
    good = [(at, lag) for at, lag, margin in rows if margin >= MIN_MARGIN]
    report["windows"] = len(rows)
    report["ambiguous"] = len(rows) - len(good)
    report["song_s"] = (rows[-1][0] + WINDOW_S) if rows else 0.0
    if len(good) < MIN_WINDOWS:
        return report

    # The constant every window should agree on, and how many actually do.
    lags = sorted(lag for _, lag in good)
    constant = lags[len(lags) // 2]
    agreed = [(at, lag) for at, lag in good
              if abs(lag - constant) <= BAR_MAP_AGREE_S]
    report["usable"] = len(agreed)
    report["share"] = len(agreed) / max(1, len(rows))
    report["constant_s"] = constant
    report["scatter_ms"] = (
        1000.0 * float(np.median([abs(lag - constant) for _, lag in agreed]))
        if agreed else 0.0)
    report["readable"] = (len(agreed) >= MIN_WINDOWS
                          and report["share"] >= MIN_USABLE_SHARE)
    if agreed:
        report["covered"] = (agreed[0][0], agreed[-1][0] + WINDOW_S)
    if not report["readable"]:
        return report

    # The stored points are the MAP's own bar lines, corrected by the one
    # constant -- so what is kept is Songsterr's measurement, not a smoothing
    # of ours. Thinned the same way, because a bar the line between its
    # neighbours already predicts is not worth a point.
    # A point is (song ms, offset ms) with the app's usual sign: the
    # recording is at song_ms - offset_ms. The map puts bar i at `time` in
    # the VIDEO and the video is at `time + constant` in this file, so the
    # offset is the song's own bar line minus that. Getting the constant's
    # sign wrong here reads as a map that is twice the constant out --
    # measured at 324 ms against readings the report claimed 76 ms of.
    raw = [(float(start), start - (time + constant) * 1000.0)
           for start, time in zip(bar_starts, bar_times)]
    report["points"] = [(round(a, 1), round(b, 1))
                        for a, b in simplify(raw, tolerance_ms)]
    return report


def replace_times(timeline: Timeline, bar_starts: Sequence[float],
                  bar_times: Sequence[float]) -> Timeline:
    """The same music with every note moved to where the map says it is."""
    from pickhero.tabs.timeline import MeasureInfo, NoteEvent

    def moved(ms: float) -> float:
        return ms + bar_lag(ms, bar_starts, bar_times) * 1000.0

    notes = []
    for note in timeline.notes:
        when = moved(note.timestamp_ms)
        ends = moved(note.timestamp_ms + note.duration_ms)
        notes.append(NoteEvent(
            timestamp_ms=when, duration_ms=max(20.0, ends - when),
            midi_note=note.midi_note, string=note.string, fret=note.fret,
            measure=note.measure))
    last = bar_times[-1] + (bar_times[-1] - bar_times[-2]
                            if len(bar_times) > 1 else 3.0)
    measures = [
        MeasureInfo(index=i, start_ms=bar_times[i] * 1000.0,
                    end_ms=(bar_times[i + 1] if i + 1 < len(bar_times)
                            else last) * 1000.0)
        for i in range(len(bar_times))]
    return Timeline(notes, timeline.metadata, measures=measures)


def measure(timeline: Timeline | str | Path, audio_path: str | Path,
            progress: Callable[[float, str], bool] | None = None,
            tolerance_ms: float = SIMPLIFY_MS
            ) -> tuple[list[tuple[float, float]],
                       list[tuple[float, float, float]],
                       tuple[float, float]]:
    """Measure this recording against this tab.

    Returns (points, rows, (tab seconds, recording seconds)). The rows come
    back as well as the points, because "how many windows were usable" is the
    difference between a song this could not read and a song that is already
    in sync -- and the lengths, because two files of different lengths are
    not the same transcription and no map can bridge that.
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
    lengths = (len(tab) / fps, len(samples) / rate)
    if len(tab) == 0 or len(rec) == 0:
        return [], [], lengths
    rows = drift_curve(tab, rec, fps, stage(1, 0.5, 0.5, "comparing"))
    return points_from_rows(rows, tolerance_ms), rows, lengths


def find_points(timeline: Timeline | str | Path, audio_path: str | Path,
                progress: Callable[[float, str], bool] | None = None,
                tolerance_ms: float = SIMPLIFY_MS
                ) -> tuple[list[tuple[float, float]],
                           list[tuple[float, float, float]]]:
    """Just the points and the rows, for the tools and the tests."""
    points, rows, _ = measure(timeline, audio_path, progress, tolerance_ms)
    return points, rows


def find(timeline: Timeline | str | Path, audio_path: str | Path,
         progress: Callable[[float, str], bool] | None = None,
         tolerance_ms: float = SIMPLIFY_MS) -> dict:
    """The whole answer in one place: the points, and what to say about them.

    The app needs both, and it needs them to agree -- a report derived from a
    second measurement could disagree with the map that was stored.
    """
    points, rows, (tab_s, rec_s) = measure(
        timeline, audio_path, progress, tolerance_ms)
    report = read_report(rows)
    report["tab_s"] = tab_s
    report["recording_s"] = rec_s
    longer = max(tab_s, rec_s, 1e-9)
    report["length_gap"] = abs(tab_s - rec_s) / longer
    # A tab of a different length is not a sync problem and no map can fix
    # it. Said before anything else, because it is the only finding here that
    # tells the player to go and get a different file.
    if report["length_gap"] > MAX_LENGTH_MISMATCH:
        report["readable"] = False
        report["wrong_length"] = True
    else:
        report["wrong_length"] = False
    report["points"] = points if report["readable"] else []
    return report
