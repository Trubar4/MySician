"""A chord as a GRIP, drawn the way every songbook draws one.

Six fret numbers spread down six lanes say which notes to play and nothing
about the shape the hand has to make. A diagram says the shape at a glance,
which is why the reference app the player reads without thinking keeps two of
them in the corner: the chord being played, and the one coming next.

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
# Room above the grid for the name, and below it for the fret numbers.
NAME_H = 22
FOOT_H = 12
PAD = 10
# An open string is a ring above the nut, a silent one a cross -- the two
# marks every diagram uses, and the only way to tell "play it open" from
# "do not play it" without writing a word.
MARK_R = 4


def card_size(scale: float = 1.0) -> tuple[int, int]:
    """How big one diagram card is. One place, so the caller can lay out."""
    return int(round(150 * scale)), int(round(132 * scale))


def draw_diagram(surface: pygame.Surface, rect: pygame.Rect,
                 shape: ChordShape, *, label: str = "",
                 dim: bool = False) -> None:
    """Draw one grip into `rect`.

    `label` goes above the name ("now", "next") -- a card with no label is a
    card whose meaning depends on where it happens to sit, and the two are
    only a few pixels apart.
    """
    t = get_theme()
    panel = t.lane_bg_even if not dim else t.lane_bg_odd
    pygame.draw.rect(surface, panel, rect, border_radius=6)
    pygame.draw.rect(surface, t.lane_line, rect, 1, border_radius=6)

    from pickhero.ui.scrolling import _get_font
    name_font = _get_font("arial", 19 if not dim else 16)
    small_font = _get_font("arial", 11)

    y = rect.top + 4
    if label:
        tag = small_font.render(label, True, t.hud_text)
        surface.blit(tag, (rect.left + PAD, y))
        y += tag.get_height() - 2
    name = name_font.render(shape.name, True,
                            t.hud_accent if not dim else t.hud_text)
    surface.blit(name, (rect.left + PAD, y))

    grid_top = rect.top + NAME_H + (12 if label else 0)
    grid_left = rect.left + PAD + 14        # room for the o/x marks
    grid_right = rect.right - PAD
    grid_bottom = rect.bottom - FOOT_H - 4
    if grid_bottom - grid_top < 20 or grid_right - grid_left < 20:
        return
    step_x = (grid_right - grid_left) / (STRINGS - 1)
    step_y = (grid_bottom - grid_top) / FRETS_SHOWN

    # The nut, or the fret number when the shape sits up the neck. An
    # open-position grid with a dot on the twelfth fret is not a diagram.
    if shape.base_fret:
        base = small_font.render(f"{shape.base_fret + 1}", True, t.hud_text)
        surface.blit(base, (rect.left + 2, int(grid_top) - 2))
    else:
        pygame.draw.line(surface, t.hud_text,
                         (grid_left, grid_top), (grid_right, grid_top), 3)

    for i in range(FRETS_SHOWN + 1):
        yy = int(grid_top + i * step_y)
        pygame.draw.line(surface, t.lane_line, (grid_left, yy),
                         (grid_right, yy), 1)
    # Strings are drawn LOW STRING FIRST, left to right -- the order a
    # guitarist reads a diagram and the reverse of the string numbers.
    for i, (string, fret) in enumerate(shape.rows()):
        xx = int(grid_left + i * step_x)
        pygame.draw.line(surface, t.lane_line, (xx, int(grid_top)),
                         (xx, int(grid_bottom)), 1)
        colour = STRING_COLORS.get(string, t.hud_text)
        if dim:
            colour = tuple(c // 2 for c in colour)
        if fret is None:
            # Not written here: a cross above the nut.
            cy = int(grid_top) - 8
            pygame.draw.line(surface, t.hud_text, (xx - MARK_R, cy - MARK_R),
                             (xx + MARK_R, cy + MARK_R), 2)
            pygame.draw.line(surface, t.hud_text, (xx - MARK_R, cy + MARK_R),
                             (xx + MARK_R, cy - MARK_R), 2)
        elif fret == shape.base_fret:
            # Open (or at the diagram's own base): a ring above the nut.
            pygame.draw.circle(surface, colour, (xx, int(grid_top) - 8),
                               MARK_R + 1, 2)
        else:
            row = fret - shape.base_fret
            if 1 <= row <= FRETS_SHOWN:
                cy = int(grid_top + (row - 0.5) * step_y)
                pygame.draw.circle(surface, colour, (xx, cy),
                                   max(4, int(step_y * 0.34)))
