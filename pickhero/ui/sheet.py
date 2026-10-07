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

# How much WIDER than proportional a bar may be drawn so that its shortest
# note still clears MIN_GAP_HEADS. The floor used to be applied gap by gap,
# which equalises exactly the notes it is meant to keep apart: in bar 38 of
# the player's November Rain an eighth came out 65 px and a sixteenth 59 px
# -- 10 % apart for twice the duration -- so the playhead crossed the
# sixteenths 1.82x faster than the eighths beside them.
#
#   *"Es müssten aber manche Töne breiter sein. Niemand sagt, dass jeder
#    Takt gleich viel Platz in der Breite haben muss."*
#
# He is right, and the fix is his: stretch the whole BAR instead, so the
# gaps keep their exact ratios and the playhead runs at one speed through
# it. The stretch a note value needs is MIN_GAP_HEADS / QUARTER_HEADS x
# (crotchet / gap) and is independent of the head size -- 0.91 for an
# eighth (nothing to do), 1.82 for a sixteenth, 2.72 for a triplet
# sixteenth, 3.63 for a thirty-second.
#
# 2.0 covers the sixteenth with headroom and is measured, not chosen.
# Swept over 23 guitar tracks of 9 songs:
#
#   cap          1.82   1.9    2.0    2.2    2.5    2.72
#   bars still varying >5%     47     47     47     46     46     25
#   rows a single bar overflows  5      6      6     25     55     55
#
# A plateau from 1.82 to 2.0 and then a cliff: past 2.0 a bar wider than a
# whole line has to be SQUEEZED, heads touch and the row is `crowded` --
# which is a worse picture than a bar whose playhead speed varies, and it
# buys one bar in nineteen hundred rows. Total rows are identical at every
# cap, so the cap costs no page turns at all; what the stretch itself costs
# is +11 % of them (1686 -> 1879), which is the trade he asked for.
#
# It is also what keeps a degenerate bar drawable: two anchors a fraction
# of a millisecond apart would otherwise ask for a stretch of 10^13. Past
# the cap the gap is floored individually, exactly as every gap used to be,
# so two heads can still never overlap.
MAX_BAR_STRETCH = 2.0

# A bar this much wider than the row it is in cannot be helped by breaking
# the line -- it is a bar too dense to draw at this size, and it is squeezed
# rather than dropped. Reported, never silent: see `Row.crowded`.
CROWDED = 1.0


# --- How the sheet is STACKED on screen -------------------------------------
# The arithmetic above says where a note sits inside its row. These say how
# tall a row is and how many of them there is room for, and they live here
# rather than in the drawing because the answer decides the layout: the head
# size is what sets both how big a note is drawn AND how far apart two of
# them have to sit, so it is the one number that moves legibility and bars
# per row together.

# Two rows, the same count the page view settled on: the one under the hand
# and the one arriving. Everything past that was spending the room the HUD
# needs, and a reader is not using it.
ROWS_SHOWN = 2
# A string lane's height as a multiple of the head. Just over one, so heads
# on neighbouring strings never touch either -- the same reason MIN_GAP_HEADS
# is above one along the row.
LANE_HEADS = 1.18
# Room above a row for its bar numbers.
NUMBER_STRIP = 20
# And the taller strip a row gets when the chord names are on: they are read
# at a glance from a distance, the way the scrolling board draws them, so
# they need the height a bar number does not. It was 54, which let the name
# grow to 34 px -- bigger than anything else on the screen, and paid for out
# of the NOTES: 34 px of strip on each of two rows is 68 px of head.
CHORD_STRIP = 42
# Air between one row and the next, so the eye can tell them apart. Measured
# rather than felt: at 12 the rows are still plainly two, and the six saved
# go into the head, which is the thing being read.
ROW_GAP = 12
# How thick each string is drawn, relative to the thinnest, index 0 = high e.
# Not invented: these are the gauges of a light set -- .010 .013 .017 .026
# .036 .046 -- divided by the first. A guitarist reads the low E as a rope
# and the high e as a hair, and one weight for all six throws away the
# strongest cue there is for which lane is which.
STRING_GAUGES = (1.0, 1.3, 1.7, 2.6, 3.6, 4.6)
# What one gauge unit is worth in pixels, as a share of the lane height. Set
# so the high e is a line you can see and the low E is unmistakably a rope:
# on a 68 px lane that is 2 px against 9.
GAUGE_PER_LANE = 1 / 34


def string_widths(lane_h: float) -> tuple[int, ...]:
    """Pixel thickness for the six strings, high e first."""
    unit = lane_h * GAUGE_PER_LANE
    return tuple(max(1, int(round(g * unit))) for g in STRING_GAUGES)


# How a note's LENGTH is drawn, mirroring the scrolling board's rules. The
# numbers live here as well as there because this module may not import the
# drawing (the drawing imports this one), and `tests/test_sheet.py` asserts
# the two sets are equal -- so they cannot drift apart in silence.
#
# A gap so back-to-back notes abut instead of merging into one ribbon; a
# bigger one when a slide has to fit a connector into it; and a cap on a
# palm-muted note, which is choked short of whatever the tab wrote. A dead
# note is a click with no sustain at all.
SUSTAIN_GAP = 0.18
SLIDE_GAP = 0.85
PALM_MUTE_MAX_HEADS = 1.3

# Never smaller than this, whatever the window does: below it the fret number
# inside the head stops being a number.
MIN_HEAD_PX = 22.0

# What +/- does in this view. Bigger heads need more room along the row, so
# fewer bars fit on one -- which is the trade the player asked to be able to
# hold himself: more readable notes, or more music in sight at once.
# DOWNWARD only, and the view opens at the top of the range. The size that
# makes two rows exactly fill the room is already the biggest a note can be
# drawn without losing the row that shows what is coming -- measured on the
# player's own machines: a 58 px head, against 26 to 44 on the scrolling
# board. Two steps up were built and tried and reported useless
# ("Vergrößern macht keinen Sinn"), because they buy nothing the eye wanted
# and cost the second row; they are gone rather than left in as a way to
# make the view worse. What is left is the direction that does something:
# smaller notes, more music on a row.
ZOOM_STEPS = (0.50, 0.60, 0.70, 0.85, 1.00)
ZOOM_DEFAULT = len(ZOOM_STEPS) - 1


def row_height(head_px: float, strip: float = NUMBER_STRIP) -> float:
    """How tall one row of music is, its top strip included."""
    return 6 * LANE_HEADS * head_px + strip


def head_for_room(room: float, rows: int = ROWS_SHOWN,
                  strip: float = NUMBER_STRIP) -> float:
    """The head size at which `rows` rows exactly fill the space there is.

    The starting point, not a cap: this is what makes two rows fit on any
    window without anybody typing a pixel count, and ZOOM_STEPS moves off it
    in both directions.
    """
    each = (room - ROW_GAP * (rows - 1)) / max(1, rows)
    return max(MIN_HEAD_PX, (each - strip) / (6 * LANE_HEADS))


def rows_that_fit(room: float, head_px: float,
                  strip: float = NUMBER_STRIP) -> int:
    """How many whole rows the space holds at this head size.

    At least one. Zoomed all the way in a row is taller than half the window
    and the second one is a sliver at the bottom -- which is honest about
    what was traded rather than hiding the row that no longer fits.
    """
    pitch = row_height(head_px, strip) + ROW_GAP
    if pitch <= 0:
        return 1
    # The half-pixel is not slack, it is arithmetic: head_for_room divides
    # the room and this multiplies it back, and a row that came out
    # 491.0000000000001 px tall counted as not fitting a window sized for
    # exactly two of it.
    return max(1, int((room + ROW_GAP + 0.5) // pitch))


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

    def ms_at(self, x: float) -> float:
        """Which moment sits at this x -- the inverse of `x_at`.

        Both directions read the SAME anchors, so a click lands exactly
        where the playhead would have stood for that moment. Two separate
        mappings would disagree by however much the layout squeezed a bar,
        which is most of a row on anything dense.
        """
        if x <= self.xs[0]:
            return self.times[0]
        if x >= self.xs[-1]:
            return self.times[-1]
        i = max(0, bisect_right(self.xs, x) - 1)
        i = min(i, len(self.xs) - 2)
        span = self.xs[i + 1] - self.xs[i]
        if span <= 0:
            return self.times[i]
        share = (x - self.xs[i]) / span
        return self.times[i] + (self.times[i + 1] - self.times[i]) * share

    def moment_at(self, x: float) -> float:
        """The moment the player POINTED AT: the nearest anchor to this x.

        A raw `ms_at` is a position, and a position inside a note head is
        already a hundred milliseconds past the note -- clicking a note would
        land just after it, which is the one place it is no use. The anchors
        are the moments this row gave room to (every onset, and the bar's own
        edges), so the nearest one is the thing that was aimed at.

        Nearest in X rather than in time, because that is what the eye
        judged: on a squeezed bar two anchors far apart in time sit a few
        pixels apart, and the pixels are what the hand aimed with.
        """
        if x <= self.xs[0]:
            return self.times[0]
        if x >= self.xs[-1]:
            return self.times[-1]
        i = max(0, bisect_right(self.xs, x) - 1)
        i = min(i, len(self.xs) - 2)
        near = i if x - self.xs[i] <= self.xs[i + 1] - x else i + 1
        return self.times[near]

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

    Strictly proportional to time, with the WHOLE BAR stretched until its
    shortest gap clears `min_gap`. Keeping the ratios exact is the point: a
    bar is the stretch of music the playhead crosses between two bar lines,
    and a floor applied gap by gap makes it cross one note faster than the
    next -- which is the thing the player was reading off the screen.

    The stretch is bounded by `MAX_BAR_STRETCH`, and what it cannot reach is
    floored one gap at a time as before, so two heads never overlap however
    pathological the bar.
    """
    wanted = [(b - a) * per_ms for a, b in zip(anchors, anchors[1:])]
    if not wanted:
        return wanted
    smallest = min(wanted)
    stretch = 1.0
    if 0.0 < smallest < min_gap:
        stretch = min(MAX_BAR_STRETCH, min_gap / smallest)
    return [max(w * stretch, min_gap) for w in wanted]


def quarter_ms(bar: MeasureInfo, fallback_ms: float) -> float:
    """How long a crotchet lasts IN THIS BAR.

    A tempo per song is the wrong unit for an engraving, and the song that
    found it says so with no room for argument: Guns N' Roses' "November
    Rain" is written at a header tempo of 70 and PLAYS at twelve tempos
    between 70 and 91. Spacing was `(gap in ms) x QUARTER_HEADS / 857 ms`
    for the whole piece, so a quarter was 2.6 heads only where the music
    really ran at 70 -- and an eighth came out

        1.30 heads at 70 BPM     1.20 at 76     **1.17 at 78**     1.00 at 91

    against a floor of `MIN_GAP_HEADS` = 1.18. **The eighth note straddles
    the floor at 77 BPM**, so bar 42 drew its eighths proportionally and bar
    34 -- the same written rhythm, two bars of music away -- floored them to
    the width of a sixteenth. Which is exactly what the player reported:
    *"kürzere Noten und Pausen und längere Noten können dieselbe Breite
    haben und der Progress-Bar wandert schneller oder langsamer"*, in bars
    34-45 of that song.

    Read per bar, a note VALUE is the same width wherever it is written, the
    floor bites only on sixteenths and faster -- which is what it is for --
    and `CLAUDE.md`'s own rule about GP files finally reaches this module:
    *"Tempo changes: GP files can have tempo changes per measure. Track
    cumulative time, don't assume constant BPM."*

    The bar's own length divided by the crotchets in it, so a 3/4 or a 6/8
    bar is read as the music written in it rather than as a quarter of
    whatever it lasts. A bar with no length or no signature falls back to the
    header tempo, which is what the whole song used to use.
    """
    beats = bar.beats or 4
    beat_type = bar.beat_type or 4
    quarters = beats * 4.0 / beat_type if beat_type else 4.0
    length = bar.end_ms - bar.start_ms
    if length <= 0 or quarters <= 0:
        return fallback_ms
    return length / quarters


# What share of a song's bars has to be narrow enough to share a line with
# the bar after it. See `head_width_for`: the head is as TALL as the screen
# allows and as WIDE as the music allows, and this says how much of the
# music gets a vote.
#
# It is a fitted number and there is no plateau to hide behind. Swept over
# 17 guitar tracks of 7 songs at the player's own 1920 window:
#
#   percentile      0.80   0.85   **0.90**   0.95   1.00
#   head width      47-58  47-58  **45-58**  41-58  30-58
#   rows holding one bar   198    173  **125**     80      6
#
# 1.00 means the song's single densest bar sets the head for the whole
# piece, which is the pathology `MAX_BAR_STRETCH` already exists to refuse
# one level down. 0.90 keeps the full-size head on 8 of those 17 tracks and
# narrows the dense ones by a fifth.
PAIR_PERCENTILE = 0.90


def bar_units(timeline: Timeline, passes: object = None) -> list[float]:
    """Each bar's width at a head of ONE pixel, in bar order.

    Every width in `lay_out` is linear in the head, so this is the whole
    song measured once in a unit the head can then be chosen against --
    which is what makes `head_width_for` arithmetic rather than a search.
    """
    measures = list(timeline.measures)
    notes = [n for n in timeline.notes if passes is None or passes(n)]
    if not measures:
        return [1.0]
    written_quarter = 60_000.0 / max(1, timeline.metadata.tempo)
    out = []
    for bar in measures:
        anchors = _bar_anchors(bar, notes)
        per_ms = QUARTER_HEADS / quarter_ms(bar, written_quarter)
        out.append(sum(_widths(anchors, per_ms, MIN_GAP_HEADS)))
    return out


def head_width_for(units: list[float], line: float, tall: float) -> float:
    """How WIDE a head may be drawn, given how tall the screen lets it be.

    *"Nun ist ab und zu nur noch ein Takt in einer ganzen Zeile, obwohl die
    Koepfe schmaler gemacht werden koennten und 2 Takte Platz haetten."*

    He is right, and the arithmetic says there is no other lever. With the
    gaps of a bar in exact proportion, the bar's width is

        (bar duration / its shortest gap) x MIN_GAP_HEADS heads

    -- so a 4/4 bar is 16 x 1.18 heads the moment it contains ONE sixteenth,
    whether it holds two notes or sixteen. At a 58 px head that is 1095 px
    and two of them do not fit a 1786 px line. Nothing between "stretch the
    bar" and "do not" exists: a partial stretch leaves the short gaps on the
    floor, so the bar is just as wide AND the playhead speed varies again
    (swept: capping the bar at half a line left 320 one-bar rows and brought
    475 of the 504 uneven bars back).

    The head is the lever, and it does not have to be square. The scrolling
    board already squeezes a head sideways while it keeps the lane's full
    height; here the two questions are simply asked of different things:
    the VERTICAL room says how tall a head can be, and the music says how
    wide it may be before two bars stop fitting a line.

    Measured over 17 guitar tracks: rows holding a single bar 362 -> 125,
    and the whole song comes out in 1215 rows against 1418 -- fewer page
    turns than before the proportional spacing was built at all.
    """
    if not units or line <= 0:
        return tall
    pairs = sorted(a + b for a, b in zip(units, units[1:])) or [max(units)]
    want = pairs[min(len(pairs) - 1,
                     int(PAIR_PERCENTILE * (len(pairs) - 1)))]
    if want <= 0:
        return tall
    return max(MIN_HEAD_PX, min(tall, line / want))


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

    # Only the FALLBACK, for a bar that cannot say how long a crotchet is in
    # it. Every bar that can is read on its own -- see `quarter_ms`.
    written_quarter = 60_000.0 / max(1, timeline.metadata.tempo)
    per_quarter = QUARTER_HEADS * head_px
    min_gap = MIN_GAP_HEADS * head_px

    plans = []
    for bar in measures:
        anchors = _bar_anchors(bar, notes)
        per_ms = per_quarter / quarter_ms(bar, written_quarter)
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
    on_string: dict[int, list[float]] = {}
    for note in notes:
        on_string.setdefault(note.string, []).append(note.timestamp_ms)

    placed = []
    for note in mine:
        x = row.x_at(note.timestamp_ms)
        ends = row.x_at(min(note.timestamp_ms + note.duration_ms, row.end_ms))
        others = on_string[note.string]
        after = bisect_right(others, note.timestamp_ms)
        # The next note on this string, wherever it is. Clamped by x_at to
        # the row's right edge when it belongs to the next row, which is
        # what a note held over a line break should look like.
        limit = (row.x_at(others[after]) if after < len(others) else width)
        # "let ring" does not lengthen the written value -- a let-ring eighth
        # is still an eighth -- it says the string is never damped, so the
        # note sounds until something else is played on it. That is exactly
        # the neighbour, which is already the cap for every other note.
        if note.let_ring:
            ends = limit
        gap = head_px * (SLIDE_GAP if note.slide_to_next else SUSTAIN_GAP)
        body = min(ends, limit) - x - gap
        # A muted note does not ring for the length the tab wrote. A dead
        # note is a click with no sustain at all, and a palm-muted one is
        # choked; drawing either at full length promises a ring that never
        # comes, and reading a chug as a held note is how a muted riff ends
        # up played wrong.
        if note.dead:
            body = min(body, head_px)
        elif note.palm_mute:
            body = min(body, head_px * PALM_MUTE_MAX_HEADS)
        placed.append(Placed(note=note, x=x, width=max(head_px, body)))
    return tuple(placed)


def row_at(rows: list[Row], ms: float) -> int:
    """Which row this moment is in, clamped to the ones that exist."""
    for row in rows:
        if row.holds(ms):
            return row.index
    return 0 if not rows or ms < rows[0].start_ms else rows[-1].index
