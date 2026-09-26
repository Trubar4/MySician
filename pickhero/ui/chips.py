"""Where a row of clickable chips lands.

Arithmetic and nothing else -- no drawing, no pygame -- for the reason
``strip.py`` gives: the drawing and the MOUSE need the same answers and must
never disagree about them. One implementation of "this chip is here" is what
stops a click landing on the chip next to the one under the pointer.

Text widths come in as a callable rather than a font, so the decision is
tested without a screen and the drawing cannot measure differently from the
layout that placed things.
"""

from __future__ import annotations

from dataclasses import dataclass

#: How tall a chip is. The search box is 26 px and they sit in one band.
CHIP_H = 26

#: Between two chips on a row, and between two rows.
CHIP_GAP = 8
ROW_GAP = 6

#: Either side of the label. Enough that a chip reads as a button rather than
#: as a word with a line round it.
CHIP_PAD = 11


@dataclass(frozen=True)
class Chip:
    """One chip: what it stands for, what it says, and where it is."""

    value: str
    label: str
    x: int
    y: int
    w: int
    h: int = CHIP_H

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def hit(self, pos: tuple[int, int]) -> bool:
        """Whether a click at this point is on this chip."""
        x, y = pos
        return self.x <= x < self.right and self.y <= y < self.bottom


def lay_out(entries, width_of, left: int, top: int,
            right: int) -> list[Chip]:
    """Place (value, label) pairs left to right, wrapping at ``right``.

    ``right`` is exclusive -- the first x a chip may not reach. A chip wider
    than the whole room is given the whole room and drawn anyway: shortening
    the text is a decision for whoever wrote it, which is the same answer the
    footer gives a single entry wider than the screen.
    """
    room = max(1, right - left)
    out: list[Chip] = []
    x, y = left, top
    for value, label in entries:
        w = min(room, width_of(label) + 2 * CHIP_PAD)
        if x > left and x + w > right:
            x, y = left, y + CHIP_H + ROW_GAP
        out.append(Chip(value=value, label=label, x=x, y=y, w=w))
        x += w + CHIP_GAP
    return out


def height(chips: list[Chip], top: int) -> int:
    """How far down the strip reached -- ``top`` again when there is none."""
    return max((c.bottom for c in chips), default=top) - top
