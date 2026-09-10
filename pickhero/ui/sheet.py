"""The song laid out as a SHEET: rows of bars that do not move.

The scrolling view has one rule it cannot escape. A note's x is its time
multiplied by a speed, because the note has to arrive at the hit line at the
moment it is played -- so the distance between two notes and the speed of
the picture are the same number. Measured on the player's own songs: at
1.0x, half the notes in Thunder's solo sit closer together than a note head
is wide, and the only way to part them is to make everything move faster,
which is what he reported as unreadable in the first place.

**A sheet has no hit line.** Nothing has to reach a fixed point at a fixed
moment, so x is free of time -- and the PLAYHEAD carries the time instead,
running quickly through a sparse bar and slowly through a dense one. That
one change dissolves the trade the scrolling view is built on: every note
can have the room it needs to be read, at no cost in speed, because there
is no speed.

The price is that the playhead does not move evenly, which is what every
engraved score has always done and what Songsterr does today -- read off
the player's own screenshot of it, where a bar of sixteenths is visibly
wider than the bar of crotchets beside it.

Nothing here draws. This is the arithmetic, so it can be tested without a
screen -- and because the two views that will use it (the tab page and the
hybrid) need the same answer at different moments.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace

from pickhero.tabs.timeline import MeasureInfo, NoteEvent, Timeline

# A crotchet's width when nothing crowds it, as a multiple of the note head.
# Spacing is proportional to time UNTIL a gap would put two heads on top of
# each other; from there the minimum takes over, which is why a run of
# sixteenths comes out evenly spaced rather than illegibly tight.
QUARTER_HEADS = 2.6

# The closest two heads on ONE string may sit, as a multiple of the head.
# Above 1.0 so they never touch; the space between them is what tells the
# eye there are two notes rather than one wide one.
MIN_GAP_HEADS = 1.18

# A bar this much wider than the row it is in cannot be helped by breaking
# the line -- it is a bar too dense to draw at this size, and it is squeezed
# rather than dropped. Reported, never silent: see `Row.crowded`.
CROWDED = 1.0


@dataclass(frozen=True)
class Placed:
    """One note, and where it sits in its row."""

    note: NoteEvent
    x: float
    width: float


@dataclass(frozen=True)
class Row:
    """One line of music: whole bars, laid out left to right.

    `x_at` is the inverse of the layout -- given a moment, where the
    playhead is -- and it is the only thing that runs per frame.
    """

    index: int
    first_bar: int
    last_bar: int
    start_ms: float
    end_ms: float
    notes: tuple[Placed, ...]
    bar_lines: tuple[float, ...]
    # The anchors the layout was built from: a time and the x it landed on.
    times: tuple[float, ...]
    xs: tuple[float, ...]
    # True when this row had to be squeezed past the minimum gap to fit,
    # which means some heads in it DO touch. Said out loud rather than left
    # to be discovered.
    crowded: bool

    def x_at(self, ms: float) -> float:
        """Where the playhead is at this moment, in pixels from the left.

        Piecewise linear between the anchors, which is what makes the
        playhead slow down where the notes are close together: the same
        milliseconds are drawn across more pixels there.
        """
        if ms <= self.times[0]:
            return self.xs[0]
        if ms >= self.times[-1]:
            return self.xs[-1]
        i = max(0, bisect_right(self.times, ms) - 1)
        i = min(i, len(self.times) - 2)
        span = self.times[i + 1] - self.times[i]
        if span <= 0:
            return self.xs[i]
        share = (ms - self.times[i]) / span
        return self.xs[i] + (self.xs[i + 1] - self.xs[i]) * share

    def holds(self, ms: float) -> bool:
        """Whether this moment belongs to this row."""
        return self.start_ms <= ms < self.end_ms


def _bar_anchors(bar: MeasureInfo, notes: list[NoteEvent]) -> list[float]:
    """The moments this bar has to give room to, in order.

    The bar's own edges are always anchors, so an empty bar still takes its
    proportional width and a bar line lands where the music says it does.
    """
    inside = {n.timestamp_ms for n in notes
              if bar.start_ms <= n.timestamp_ms < bar.end_ms}
    return sorted({bar.start_ms, bar.end_ms} | inside)


def _widths(anchors: list[float], per_ms: float, min_gap: float) -> list[float]:
    """How wide each gap in a bar wants to be.

    Proportional to time, but never narrower than two heads can sit apart.
    That single `max` is the whole idea: rhythm is visible wherever there is
    room for it, and legibility wins wherever there is not.
    """
    return [max((b - a) * per_ms, min_gap)
            for a, b in zip(anchors, anchors[1:])]


def lay_out(timeline: Timeline, width: float, head_px: float,
            passes: object = None) -> list[Row]:
    """Break the song into rows of bars and place every note in them.

    `passes` is an optional filter -- the same one the playing screen uses
    to hide notes above a fret limit -- because a note that is not drawn
    must not take room either, or a filtered song is laid out for notes
    nobody can see.
    """
    measures = list(timeline.measures)
    notes = [n for n in timeline.notes
             if passes is None or passes(n)]
    if not measures:
        measures = [MeasureInfo(index=0, start_ms=0.0,
                                end_ms=max(1.0, timeline.duration_ms))]

    quarter_ms = 60_000.0 / max(1, timeline.metadata.tempo)
    per_ms = (QUARTER_HEADS * head_px) / quarter_ms
    min_gap = MIN_GAP_HEADS * head_px

    plans = []
    for bar in measures:
        anchors = _bar_anchors(bar, notes)
        widths = _widths(anchors, per_ms, min_gap)
        plans.append((bar, anchors, widths, sum(widths)))

    rows: list[Row] = []
    batch: list[tuple] = []
    taken = 0.0
    for plan in plans:
        wanted = plan[3]
        if batch and taken + wanted > width:
            rows.append(_row(len(rows), batch, taken, width, head_px, notes))
            batch, taken = [], 0.0
        batch.append(plan)
        taken += wanted
    if batch:
        rows.append(_row(len(rows), batch, taken, width, head_px, notes))
    return rows


def _row(index: int, batch: list[tuple], taken: float, width: float,
         head_px: float, notes: list[NoteEvent]) -> Row:
    """Justify one row to the full width and place its notes.

    Stretched when the bars ask for less than the line has -- an engraver
    fills the line rather than leaving a ragged edge -- and squeezed only
    when a single bar is denser than a whole line can hold, which is the one
    case where heads are allowed to touch and the row says so.
    """
    scale = width / taken if taken > 0 else 1.0
    times: list[float] = []
    xs: list[float] = []
    bar_lines: list[float] = []
    x = 0.0
    for bar, anchors, widths, _ in batch:
        bar_lines.append(x)
        for anchor, w in zip(anchors, widths):
            times.append(anchor)
            xs.append(x)
            x += w * scale
    times.append(batch[-1][1][-1])
    xs.append(x)

    start_ms = batch[0][0].start_ms
    end_ms = batch[-1][0].end_ms
    row = Row(index=index, first_bar=batch[0][0].index,
              last_bar=batch[-1][0].index, start_ms=start_ms, end_ms=end_ms,
              notes=(), bar_lines=tuple(bar_lines), times=tuple(times),
              xs=tuple(xs), crowded=scale < CROWDED)
    return replace(row, notes=_place(row, notes, head_px, width))


def _place(row: Row, notes: list[NoteEvent], head_px: float,
           width: float) -> tuple[Placed, ...]:
    """Every note of this row, with the width it is drawn at.

    A sustain stops short of the next note on ITS OWN string, the same rule
    the scrolling view uses -- tab durations regularly overrun the note
    after them, and a held note drawn over the next one is two notes that
    read as one.
    """
    mine = [n for n in notes if row.start_ms <= n.timestamp_ms < row.end_ms]
    mine.sort(key=lambda n: (n.timestamp_ms, n.string))
    next_on_string: dict[int, list[float]] = {}
    for note in mine:
        next_on_string.setdefault(note.string, []).append(note.timestamp_ms)

    placed = []
    for note in mine:
        x = row.x_at(note.timestamp_ms)
        ends = row.x_at(min(note.timestamp_ms + note.duration_ms, row.end_ms))
        others = next_on_string[note.string]
        after = bisect_right(others, note.timestamp_ms)
        limit = (row.x_at(others[after]) if after < len(others) else width)
        body = min(ends, limit) - x
        placed.append(Placed(note=note, x=x, width=max(head_px, body)))
    return tuple(placed)


def row_at(rows: list[Row], ms: float) -> int:
    """Which row this moment is in, clamped to the ones that exist."""
    for row in rows:
        if row.holds(ms):
            return row.index
    return 0 if not rows or ms < rows[0].start_ms else rows[-1].index
