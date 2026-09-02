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


class SyncMap:
    """song time <-> recording time, through the places they agree.

    A point is `(song_ms, offset_ms)` with the app's usual sign: a POSITIVE
    offset makes the recording sound later, so the recording is at
    `song_ms - offset_ms`.
    """

    def __init__(self, points: list[tuple[float, float]] | None = None,
                 base_offset_ms: float = 0.0):
        # With no points at all the single stored offset is the whole answer,
        # which is what every song starts as and what most songs stay as.
        self.base_offset_ms = float(base_offset_ms)
        self.points = sorted((float(a), float(b)) for a, b in (points or []))
        self._songs = [p[0] for p in self.points]
        self._offsets = [p[1] for p in self.points]
        # The recording position of each point, which is what the inverse is
        # interpolated over. Increasing as long as no segment's offset slope
        # reaches 1, and _slope() is clamped so none can.
        self._recordings = [s - o for s, o in self.points]

    def __len__(self) -> int:
        return len(self.points)

    @property
    def empty(self) -> bool:
        return not self.points

    # -- the two directions ------------------------------------------------

    def offset_at(self, song_ms: float) -> float:
        """How far the recording is shifted against the tab, here."""
        if not self.points:
            return self.base_offset_ms
        if len(self.points) == 1:
            return self._offsets[0]
        return self._interp(song_ms, self._songs, self._offsets,
                            slope_of_offset=True)

    def recording_at(self, song_ms: float) -> float:
        """Where in the recording this moment of the song is."""
        return song_ms - self.offset_at(song_ms)

    def song_at(self, recording_ms: float) -> float:
        """Where in the song this moment of the recording is.

        The inverse, and it is exact rather than iterated: the recording is a
        piecewise-linear function of song time, so its inverse is piecewise
        linear over the recording positions of the same points.
        """
        if not self.points:
            return recording_ms + self.base_offset_ms
        if len(self.points) == 1:
            return recording_ms + self._offsets[0]
        return self._interp(recording_ms, self._recordings, self._songs,
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
        """How fast the recording runs against the tab, one per segment."""
        return tuple(
            1.0 - self._slope(self._songs, self._offsets, i, True)
            for i in range(len(self.points) - 1))

    def worst_correction_ms(self) -> float:
        """The largest offset the map applies. A size for "how far out"."""
        if not self.points:
            return abs(self.base_offset_ms)
        return max(abs(o) for o in self._offsets)
