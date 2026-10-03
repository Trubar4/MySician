"""A chord as a GRIP, drawn the way every songbook draws one.

Six fret numbers spread down six lanes say which notes to play and nothing
about the shape the hand has to make. A diagram says the shape at a glance,
which is why the reference app the player reads without thinking keeps two of
them in the corner: the chord being played, and the one coming next.

**It lies the way the board does**: strings across, low E at the BOTTOM,
frets left to right from the nut. A songbook prints the grid upright with
the low string on the left, and mixing the two orientations in one app means
rotating the picture in your head between one glance and the next. The NAME
stays at the top, where a card is read from.

What is drawn is what the tab WROTE -- see `tabs/chord_shapes.py` for what
the files actually carry. In particular there are no finger colours here: two
of the player's three songs give no fingering at all and the third gives one
for 41 of 120 positions, and a picture that is richer on one song than the
next teaches nothing.
"""

from __future__ import annotations

import pygame

from pickhero.tabs.chord_shapes import FRETS_SHOWN, ChordShape
from pickhero.ui.colors import STRING_COLORS, get_theme

# The grid. Six string lines across, five fret spaces down.
STRINGS = 6
# Room above the grid for the name, and below it for the fret number. Every
# one of these is at the card's FULL size and is scaled with it -- see
# `_chrome`. They were absolute, so making the card smaller took the whole
# reduction out of the grid: at 15 % off the card the dots lost 24 %.
NAME_H = 22
FOOT_H = 14
PAD = 10
# An open string is a ring above the nut, a silent one a cross -- the two
# marks every diagram uses, and the only way to tell "play it open" from
# "do not play it" without writing a word.
MARK_R = 4
# Room to the LEFT of the nut for those marks, now that the strings lie
# across rather than down.
MARK_ROOM = 18
# What `card_size(1.0)` draws, which is what every length above is a part of.
FULL_H = 178


def card_size(scale: float = 1.0) -> tuple[int, int]:
    """How big one diagram card is. One place, so the caller can lay out.

    Bigger than it was, and the same for both cards. At 150x132 the dots sat
    about 18 px apart, which is a diagram you have to lean in for while
    playing -- and the "next" card was smaller still, so the grip you have to
    PREPARE was the harder of the two to read.
    """
    return int(round(240 * scale)), int(round(178 * scale))


def _chrome(rect: pygame.Rect) -> tuple[int, int, int, int]:
    """(name, foot, pad, mark room) at the size this card is drawn.

    Read off the card's own height, because `card_size` is the one place
    that knows what a card is -- and a second copy of the scale is how the
    drawing and the layout come to disagree about where the grid starts.
    """
    scale = max(0.4, rect.height / FULL_H)
    return (max(14, int(round(NAME_H * scale))),
            max(10, int(round(FOOT_H * scale))),
            max(4, int(round(PAD * scale))),
            max(10, int(round(MARK_ROOM * scale))))


def header(rect: pygame.Rect, labelled: bool) -> tuple[int, int, int]:
    """(y of the label, y of the name, y the grid starts at).

    One implementation, because the drawing and `grid_rect` were two: the
    header was `NAME_H + 12` and a 22 px name RENDERS about 27 px tall, so
    the grid began inside the name and the top string's own cross was drawn
    through it -- visible on the player's screenshot at full size, and worse
    at any smaller one.
    """
    scale = max(0.4, rect.height / FULL_H)
    label_h = int(round(14 * scale)) if labelled else 0
    y = rect.top + int(round(4 * scale))
    return y, y + label_h, y + label_h + int(round(NAME_H * 1.25 * scale))


def grid_rect(rect: pygame.Rect, labelled: bool) -> pygame.Rect:
    """Where the fret grid sits inside a card.

    One implementation, so the drawing and anything asking where a string
    landed cannot disagree.
    """
    _, _, foot_h, pad, mark_room = (0, *_chrome(rect))
    top = header(rect, labelled)[2]
    return pygame.Rect(
        rect.left + pad + mark_room,
        top,
        max(1, rect.right - pad - (rect.left + pad + mark_room)),
        max(1, rect.bottom - foot_h - 4 - top),
    )


def leftmost_finger(shape: ChordShape) -> tuple[int, int] | None:
    """(column 1..FRETS_SHOWN, the fret it really is) of the first finger.

    *"Fuege Bundnummer zum linkesten Finger hinzu."* The number used to sit
    at the NUT and only appear when the shape was up the neck, which answers
    "where does this diagram begin" -- and the question a player holding the
    grip has is "which fret is that dot on". Those are the same number only
    in the first column.
    """
    best = None
    for _string, fret in shape.frets:
        row = fret - shape.base_fret
        if 1 <= row <= FRETS_SHOWN and (best is None or row < best[0]):
            best = (row, fret)
    return best


def string_rows(grid: pygame.Rect) -> list[tuple[int, float]]:
    """(GP string number, y) for the six strings, LOW E AT THE BOTTOM.

    The same way round as the scrolling board, where string 6 sits on the
    lowest lane -- a diagram that puts it at the top asks the player to flip
    the picture in their head between one glance and the next.
    """
    step = grid.height / (STRINGS - 1)
    return [(6 - i, grid.top + (STRINGS - 1 - i) * step)
            for i in range(STRINGS)]


def draw_diagram(surface: pygame.Surface, rect: pygame.Rect,
                 shape: ChordShape, *, label: str = "",
                 dim: bool = False) -> None:
    """Draw one grip into `rect`, lying the way the board does.

    Strings run ACROSS with the low E at the bottom and the frets left to
    right from the nut, which is how this app draws a tab everywhere else.
    A songbook prints the grid the other way up, and mixing the two means
    rotating the picture in your head between one glance and the next.

    The NAME stays at the top, where a card is read from.

    `label` goes above the name ("now", "next") -- a card with no label is a
    card whose meaning depends on where it happens to sit, and the two are
    only a few pixels apart.
    """
    t = get_theme()
    panel = t.lane_bg_even if not dim else t.lane_bg_odd
    pygame.draw.rect(surface, panel, rect, border_radius=6)
    pygame.draw.rect(surface, t.lane_line, rect, 1, border_radius=6)

    from pickhero.ui.scrolling import _get_font
    name_h, foot_h, pad, mark_room = _chrome(rect)
    name_font = _get_font("arial", max(14, name_h))
    small_font = _get_font("arial", max(9, int(round(12 * rect.height
                                                     / FULL_H))))
    foot_font = _get_font("arial", max(11, foot_h), bold=True)

    label_y, name_y, _ = header(rect, bool(label))
    if label:
        surface.blit(small_font.render(label, True, t.hud_text),
                     (rect.left + pad, label_y))
    name = name_font.render(shape.name, True,
                            t.hud_accent if not dim else t.hud_text)
    surface.blit(name, (rect.left + pad, name_y))

    grid = grid_rect(rect, bool(label))
    if grid.width < 20 or grid.height < 20:
        return
    step_x = grid.width / FRETS_SHOWN

    # The nut, where the shape starts at it. A grid that opens up the neck
    # draws no nut, because there is none there -- and either way the fret
    # the first finger is on is written under that finger, below.
    if not shape.base_fret:
        pygame.draw.line(surface, t.hud_text, (grid.left, grid.top),
                         (grid.left, grid.bottom), 3)
    first = leftmost_finger(shape)
    if first is not None:
        row, fret = first
        text = foot_font.render(str(fret), True, t.hud_text)
        cx = int(grid.left + (row - 0.5) * step_x)
        surface.blit(text, (cx - text.get_width() // 2, grid.bottom + 2))

    for i in range(FRETS_SHOWN + 1):
        x = int(grid.left + i * step_x)
        pygame.draw.line(surface, t.lane_line, (x, grid.top),
                         (x, grid.bottom), 1)
    for string, sy in string_rows(grid):
        yy = int(sy)
        pygame.draw.line(surface, t.lane_line, (grid.left, yy),
                         (grid.right, yy), 1)
        colour = STRING_COLORS.get(string, t.hud_text)
        if dim:
            colour = tuple(c // 2 for c in colour)
        fret = shape.fret_on(string)
        mark_x = grid.left - MARK_ROOM // 2
        if fret is None:
            # Not written here: a cross before the nut.
            pygame.draw.line(surface, t.hud_text,
                             (mark_x - MARK_R, yy - MARK_R),
                             (mark_x + MARK_R, yy + MARK_R), 2)
            pygame.draw.line(surface, t.hud_text,
                             (mark_x - MARK_R, yy + MARK_R),
                             (mark_x + MARK_R, yy - MARK_R), 2)
        elif fret == shape.base_fret:
            # Open (or at the diagram's own base): a ring before the nut.
            pygame.draw.circle(surface, colour, (mark_x, yy), MARK_R + 1, 2)
        else:
            row = fret - shape.base_fret
            if 1 <= row <= FRETS_SHOWN:
                cx = int(grid.left + (row - 0.5) * step_x)
                pygame.draw.circle(surface, colour, (cx, yy),
                                   max(4, int(min(step_x, grid.height /
                                                  (STRINGS - 1)) * 0.34)))
