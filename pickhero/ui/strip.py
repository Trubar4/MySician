"""The strip along the bottom: where you are, how it went, and a way to move.

Arithmetic only -- no drawing and no pygame -- so it is tested without a
screen, and because two callers need the same answers and must never disagree
about them: the drawing puts the marker somewhere, and the mouse reads a click
back into a moment of the song. One implementation of "x is this millisecond"
is what stops a click landing a bar away from where the marker was.
"""

from __future__ import annotations

STRINGS = 6

# CONSTANT, and that is the point rather than an oversight. The footer's
# height is MEASURED because it wraps; this one must not be, because the
# music's bottom margin is taken from it and the board's note size is taken
# from what is left. A height derived from what the strip draws would close
# that circle -- which is the feedback loop this project has already caught
# once, when bars-per-row went into the footer and the sheet's head size
# walked 44.4 -> 44.5 px and never settled.
STRIP_HEIGHT = 37
STRIP_GAP = 8
STRIP_BAND = STRIP_HEIGHT + STRIP_GAP
STRIP_SIDE_PAD = 12

# The numbers beside the miniature. Also a constant, for the same reason: the
# miniature's width is what maps a click to a millisecond, and a column that
# grew with the digits in it would move the whole song under the mouse the
# moment the score crossed from 9 % to 10 %.
#
# Measured rather than guessed, at the sizes the strip draws: "100%" is 64 px
# of consolas 26 and "100%  Right Notes" is 98 px of arial 12, which with the
# gaps is 186. The first value here was 168 and the miniature drew over the
# word "Notes" -- the fault this screen has been fixed for at the footer, at
# the sync panel and at the completion overlay. A test asserts the room.
STRIP_NUMBERS_W = 190

# The six rows do not fill the band; they sit in the middle of it. Two
# reasons, and the player named the first: *"die Saiten naeher zusammenruecken,
# das ist bei Yousician auch so"*. Spread over the whole height the rows read
# as six separate lists of dots; clustered they read as ONE object -- the song
# -- which is what a miniature is for. And the margin it leaves is where the
# playhead and the loop shading are legible instead of buried among the notes.
ROW_SPREAD = 0.66

# A dot is three pixels because two is a speck and four closes the gap between
# the rows: at 0.66 of a 37 px band the rows sit 4.1 px apart, so three leaves
# about a pixel of dark between them and the six colours still read as six.
DOT_PX = 3

# How long a verdict can still change after the note has gone past. The hit
# window, the late window beyond it, a chord verdict that trails its strike by
# ~380 ms, and a rescue that arrives after the note has already timed out --
# past this nothing can move, so the dot may be drawn once and kept.
SETTLE_MS = 1200.0

# A step the clock could not have taken by PLAYING. Past this the song did not
# advance, it jumped -- a seek, a loop turn, the count-in ending -- and the
# stretch skipped over was never played. Painting it would fill the strip with
# a run that never happened, because the matcher's sweep marks everything
# behind the playhead missed: the same trap the run log carries, and the
# reason `seeks` is in its header. So the strip steps over it too.
JUMP_MS = 2000.0


def split_percentages(stats: dict) -> tuple[float | None, float | None,
                                            float | None]:
    """The three numbers, out of what the matcher already counts.

    `MatchType.CLOSE` has always meant *the right note, played off the beat*,
    so the split the player drew falls straight out of the existing model and
    nothing has to be measured again:

    - the big one   ``hits / total``            the right note, on time
    - right notes   ``(hits + close) / total``  the right note at all
    - timing        ``hits / (hits + close)``   of those, how many landed

    Over what was REACHED, which is what ``get_statistics`` counts: a song
    abandoned at bar 9 reports the first nine bars. That is the honest half of
    it -- and the reason the number is read next to the clock, which says how
    far the run actually got.

    ``None`` where the denominator is empty, never ``0.0``. Nothing played is
    not the same as everything missed, and a zero would claim it was.
    """
    total = stats.get("total", 0)
    hits = stats.get("hits", 0)
    close = stats.get("close", 0)
    if total <= 0:
        return None, None, None
    right = hits + close
    return (hits / total * 100.0,
            hits / right * 100.0 if right > 0 else None,
            right / total * 100.0)


def x_for_ms(ms: float, duration_ms: float, width: int) -> float:
    """Where a moment of the song sits along a strip `width` pixels wide."""
    if duration_ms <= 0 or width <= 0:
        return 0.0
    return max(0.0, min(1.0, ms / duration_ms)) * width


def ms_for_x(x: float, duration_ms: float, width: int) -> float:
    """The moment a pixel names, clamped to the song.

    Clamped rather than refused, so a drag that leaves the strip -- which is
    most of them, because the mouse does not stop at an edge the hand cannot
    feel -- goes on scrubbing towards the end it was heading for.
    """
    if width <= 0:
        return 0.0
    return max(0.0, min(1.0, x / width)) * max(0.0, duration_ms)


def row_y(string: int, height: int) -> float:
    """The centre of a string's row, string 1 at the TOP.

    The same way up as the board and the sheet: string 6, the low E, is the
    bottom row. A miniature of the tab that flipped it would ask the player to
    turn the picture over in their head between one glance and the next.

    The six rows occupy `ROW_SPREAD` of the band, centred, rather than all of
    it -- see the constant.
    """
    band = height * ROW_SPREAD
    return (height - band) / 2.0 + (string - 0.5) * band / STRINGS


def dot(note, duration_ms: float, width: int, height: int) -> tuple[int, int]:
    """The pixel one note occupies, as (x, y)."""
    return (int(x_for_ms(note.timestamp_ms, duration_ms, width)),
            int(row_y(note.string, height)))


def note_dots(notes, duration_ms: float, width: int,
              height: int) -> list[tuple[int, int, int]]:
    """Every note as (x, y, string), each pixel named once.

    Deduplicated because a four-minute song at a thousand notes puts dozens of
    them on the same pixel of the same row, and the strip is built by drawing
    every one of them. What survives the squeeze is density, which is what the
    miniature is FOR: a solo looks like a solo and a held chord looks like a
    gap, without reading anything.
    """
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int, int]] = []
    for note in notes:
        x, y = dot(note, duration_ms, width, height)
        if (x, y) in seen:
            continue
        seen.add((x, y))
        out.append((x, y, note.string))
    return out
