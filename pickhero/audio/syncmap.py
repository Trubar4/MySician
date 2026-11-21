"""Where the tab and the recording agree, and what happens between those places.

An offset says where a recording STARTS. It cannot say how fast it runs, and a
fixed grid at 132 BPM against a band that played to no click walks away by
seconds over a song. A single rate fixes that only while the rate is constant,
and measured on the player's own song it is not: -10.4, -5.2, -7.0, -17.7 and
-14.0 ms per second across five sections.

So this is a LIST of places where the two are known to agree, and the
correction between two of them is the straight line joining them. Nothing is
modelled; every point is a piece of the truth, and the line between two of
them is the least that can be claimed. Integrated over that measured drift
curve, the worst error over four minutes:

    one offset (what the offset keys alone can do)   1313 ms
    one offset and one rate                           241 ms
    3 sync points                                     107 ms
    5 sync points                                      90 ms
    9 sync points                                      27 ms
    17 sync points                                      7 ms

which is the whole argument for points rather than a formula, and the reason
the other tools that solve this -- Go PlayAlong, Guitar Pro 8's audio track,
alphaTab's sync points -- all ask for a handful of places rather than a number.

**The correction is applied to the TAB, never to the recording.** Warping a
recording means seeking it, and a seek is audible: at 17 ms/s the sync would
break every five seconds for the rest of the song, which is the stutter this
app already had. The picture can be pulled by a millisecond a frame and nobody
sees it. Correct the cheap side.
"""

from __future__ import annotations

from bisect import bisect_right

# How far a segment's slope may imply the recording runs from the tab. The
# same bounds the stored rate uses: a real mismatch is about a percent, and
# anything past this is two points set too close together rather than a
# recording that really runs at that speed.
MIN_RATE = 0.9
MAX_RATE = 1.1

# How far outside the real breakpoints the sentinels sit -- an hour, which no
# song reaches, so nothing real is ever read off the segment beyond them. See
# `__init__`: they exist so that the map's extrapolation and the correction's
# held value can both be true at the ends of ONE interpolation.
SENTINEL_MS = 3_600_000.0


class SyncMap:
    """song time <-> recording time, through the places they agree.

    A point is `(song_ms, offset_ms)` with the app's usual sign: a POSITIVE
    offset makes the recording sound later, so the recording is at
    `song_ms - offset_ms`.
    """

    def __init__(self, points: list[tuple[float, float]] | None = None,
                 base_offset_ms: float = 0.0,
                 corrections: list[tuple[float, float]] | None = None):
        # With no points at all the single stored offset is the whole answer,
        # which is what every song starts as and what most songs stay as.
        self.base_offset_ms = float(base_offset_ms)
        self.points = sorted((float(a), float(b)) for a, b in (points or []))
        self.corrections = sorted(
            (float(a), float(b)) for a, b in (corrections or []))
        self._map_songs = [p[0] for p in self.points]
        self._map_offsets = [p[1] for p in self.points]
        # ONE curve, evaluated at every breakpoint either layer has, because
        # both directions and the rate clamp have to see the same thing. Two
        # interpolations added together would be the same answer computed
        # twice, and the inverse could not then be exact over either of them.
        self._songs = sorted({*self._map_songs,
                              *(c[0] for c in self.corrections)})
        self._offsets = [self._map_offset(t) + self.correction_at(t)
                         for t in self._songs]
        # ... and two sentinels, because the two layers do different things
        # OUTSIDE themselves: the map extrapolates its last slope on purpose
        # (a recording drifting at the last point is still drifting after
        # it), and a correction is held (a slope fitted to two observed
        # residuals would invent drift the map already carries).
        #
        # One interpolation cannot express both at its ends -- it continues
        # whatever the outermost segment's slope happens to be, so a song
        # with corrections and no map extrapolated them and a correction
        # of 500 ms read as 1038 ms two minutes later. The sentinels carry
        # the TRUE value far outside, so the outer segments already have
        # the right slope and continuing it is right as well.
        self._ix, self._iy = list(self._songs), list(self._offsets)
        if self._ix:
            for edge in (self._ix[0] - SENTINEL_MS,
                         self._ix[-1] + SENTINEL_MS):
                self._ix.append(edge)
                self._iy.append(self._map_offset(edge)
                                + self.correction_at(edge))
            order = sorted(range(len(self._ix)), key=self._ix.__getitem__)
            self._ix = [self._ix[i] for i in order]
            self._iy = [self._iy[i] for i in order]
        # The recording position of each breakpoint, which is what the
        # inverse is interpolated over. Increasing as long as no segment's
        # offset slope reaches 1, and _slope() is clamped so none can.
        self._recordings = [t - o for t, o in zip(self._ix, self._iy)]

    def __len__(self) -> int:
        return len(self.points)

    @property
    def empty(self) -> bool:
        """Nothing but the nudge: neither measured nor corrected by hand."""
        return not self.points and not self.corrections

    # -- the two layers ----------------------------------------------------

    def _map_offset(self, song_ms: float) -> float:
        """What the MEASUREMENT says here, before anybody corrected it."""
        if not self.points:
            return 0.0
        if len(self.points) == 1:
            return self._map_offsets[0]
        return self._interp(song_ms, self._map_songs, self._map_offsets,
                            slope_of_offset=True)

    def correction_at(self, song_ms: float) -> float:
        """What the player added on top of the measurement here.

        A second layer rather than points in the same list, and the reason is
        a measurement: dropped into the anchor list, one correction only
        removes the measured points it CONTRADICTS -- and a measured point
        five seconds away differing by 350 ms implies 7 %, under the clamp,
        so it survives and drags the correction straight back to the map's
        value. On a bar map with a point every five seconds that made
        `Shift+S` a triangle five seconds wide:

            song 0:15  0 ms | 0:18  210 | 0:20  350 | 0:22  210 | 0:25  0

        The rule before that deleted every measured point within thirty
        seconds instead, which destroyed the map's shape where it was most
        likely to be right. Both are wrong, in opposite directions, and
        neither is what the player asked for -- *"es ist einfach zu viel
        Handarbeit"*. A correction belongs on top: the map keeps its shape
        and the hand only bends the residual.

        **Flat outside the outermost correction, where the map
        extrapolates.** A correction is something somebody OBSERVED, and a
        slope fitted to two observations of a residual would invent drift
        that the map underneath already carries. One correction is therefore
        a constant over the whole song, which is exactly what
        `base_offset_ms` is -- so this is a strict generalisation of it.
        """
        if not self.corrections:
            return 0.0
        if len(self.corrections) == 1:
            return self.corrections[0][1]
        xs = [c[0] for c in self.corrections]
        ys = [c[1] for c in self.corrections]
        if song_ms <= xs[0]:
            return ys[0]
        if song_ms >= xs[-1]:
            return ys[-1]
        i = max(0, min(len(xs) - 2, bisect_right(xs, song_ms) - 1))
        run = xs[i + 1] - xs[i]
        if run <= 0:
            return ys[i]
        return ys[i] + (song_ms - xs[i]) * (ys[i + 1] - ys[i]) / run

    # -- the two directions ------------------------------------------------

    def offset_at(self, song_ms: float) -> float:
        """How far the recording is shifted against the tab, here.

        The stored offset is added ON TOP of the points rather than replaced
        by them. It used to be ignored the moment a single point existed,
        which made the offset keys dead on every song that had been synced --
        the HUD went on showing a number the player could change while the
        sound did not move, and the one press that could rescue a drifting
        solo saved that dead number instead of the offset actually sounding.
        Read as a nudge on top, the keys work everywhere and a point set
        after nudging carries what was heard.
        """
        if not self._songs:
            return self.base_offset_ms
        if len(self._songs) == 1 and not self.points:
            # One correction and nothing measured: a constant, which is what
            # the single stored offset always was.
            return self._offsets[0] + self.base_offset_ms
        return self._interp(song_ms, self._ix, self._iy,
                            slope_of_offset=True) + self.base_offset_ms

    def recording_at(self, song_ms: float) -> float:
        """Where in the recording this moment of the song is."""
        return song_ms - self.offset_at(song_ms)

    def song_at(self, recording_ms: float) -> float:
        """Where in the song this moment of the recording is.

        The inverse, and it is exact rather than iterated: the recording is a
        piecewise-linear function of song time, so its inverse is piecewise
        linear over the recording positions of the same points.
        """
        if not self._songs:
            return recording_ms + self.base_offset_ms
        if len(self._songs) == 1 and not self.points:
            return recording_ms + self._offsets[0] + self.base_offset_ms
        # The points' recording positions were taken without the nudge, so a
        # recording moment is looked up where it would have been without it.
        return self._interp(recording_ms + self.base_offset_ms,
                            self._recordings, self._ix,
                            slope_of_offset=False)

    # -- how a segment is read ---------------------------------------------

    def _slope(self, xs: list[float], ys: list[float], i: int,
               slope_of_offset: bool) -> float:
        """The straight line between point i and i+1, bounded.

        Two points set close together imply a wild rate from a small mistake,
        so the slope is clamped to what a recording can really do. The bound
        is on the RATE the segment implies, in both directions, so the same
        rule holds whichever way the map is being read.
        """
        run = xs[i + 1] - xs[i]
        if run <= 0:
            return 0.0
        slope = (ys[i + 1] - ys[i]) / run
        if slope_of_offset:
            # offset per song ms: rate = 1 - slope
            return max(1.0 - MAX_RATE, min(1.0 - MIN_RATE, slope))
        # song ms per recording ms: rate = 1 / slope
        return max(1.0 / MAX_RATE, min(1.0 / MIN_RATE, slope))

    def _interp(self, x: float, xs: list[float], ys: list[float],
                slope_of_offset: bool) -> float:
        """Piecewise linear, extrapolated from the outermost segment.

        Extrapolation rather than a held value, because a recording that was
        drifting at the last point is still drifting after it -- and the
        slope is bounded, so it cannot run away. A point near the end of the
        song is still worth more than any extrapolation, which is what every
        tool that does this tells its users.
        """
        last = len(xs) - 1
        if x <= xs[0]:
            i = 0
        elif x >= xs[last]:
            i = last - 1
        else:
            i = bisect_right(xs, x) - 1
            i = max(0, min(last - 1, i))
        return ys[i] + (x - xs[i]) * self._slope(xs, ys, i, slope_of_offset)

    # -- what the screen says about it -------------------------------------

    def rates(self) -> tuple[float, ...]:
        """How fast the recording runs against the tab, one per segment.

        The TOTAL, because this answers what the app is applying -- the run
        log's `mp3_sync_sections` and the panel's per-section figures -- and
        a correction set by ear is evidence about the recording too: the
        player heard it drift. What stays measurement-only is `covers()` and
        `drift_seen_ms()`, which are explicitly about the measured span.
        """
        return tuple(
            1.0 - self._slope(self._songs, self._offsets, i, True)
            for i in range(len(self._songs) - 1))

    def covers(self) -> tuple[float, float] | None:
        """The span the points were actually measured over, or None.

        Outside it the map EXTRAPOLATES, which is a guess made from the last
        segment's slope -- worth having, and worth saying out loud, because a
        song whose points stop at three minutes has its last three placed by
        arithmetic rather than by anybody listening.
        """
        if len(self.points) < 2:
            return None
        return self._map_songs[0], self._map_songs[-1]

    def drift_seen_ms(self) -> float:
        """How far the offset moved across the part that WAS measured.

        The honest size of an extrapolation: a recording that wandered by
        this much where somebody was listening can wander by that much again
        where nobody was. A modelled bound would be a promise; this is a
        number the song has already produced.
        """
        if len(self.points) < 2:
            return 0.0
        return max(self._map_offsets) - min(self._map_offsets)

    def worst_correction_ms(self) -> float:
        """The largest offset the map applies. A size for "how far out"."""
        if not self._songs:
            return abs(self.base_offset_ms)
        return max(abs(o + self.base_offset_ms) for o in self._offsets)
