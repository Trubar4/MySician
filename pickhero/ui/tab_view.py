"""The song as engraved tablature, with a playhead.

The scrolling display answers "what do I play next"; this answers "where am
I in the piece". verovio does the engraving (`tabs/musicxml.py` is the
bridge), and everything here is about turning its page into something the
app can draw on and point at.

Three things this gets right on purpose:

- **Time comes from our timeline, never from the engraver.** verovio's
  timemap is unreliable on a tablature staff -- see `tabs/musicxml.py` --
  and it was never the authority anyway. Each note carries an id of ours, so
  a note's moment is ours and its position is verovio's.
- **The positions are in the SVG's own coordinates, not pixels.** A page
  whose viewBox is 24000 units wide rasterises to whatever pygame felt like,
  and a mapping that ignores that is off by a factor of twenty-five while
  looking plausible. Everything is normalised to 0..1 first.
- **Engraving is done once per song and zoom, never per frame.** A whole
  song is 0.2 s of work; sixty of those a second is not a display.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pygame

from pickhero.tabs.musicxml import note_positions, to_musicxml
from pickhero.tabs.timeline import Timeline

# Zoom is the PAGE WIDTH, not verovio's `scale`. The first version stepped
# the scale, which made a bigger bitmap of the identical layout -- once it
# was blitted to the window nothing whatsoever changed on screen, which is
# the "feature that cannot be seen working" fault this project keeps
# shipping. Measured on a real song: the page width moves the layout from
# 2.1 to 10.4 bars per line, which is what a zoom is FOR. Narrower page,
# fewer bars, bigger notes.
ZOOM_STEPS = (4200, 3200, 2400, 2000, 1600, 1200)
DEFAULT_ZOOM = 2                       # index into ZOOM_STEPS

# Everything is scaled to the window afterwards, so this only decides how
# tall a page is relative to its width.
PAGE_HEIGHT = 3200
ENGRAVING_SCALE = 40


@dataclass
class TabPage:
    """One engraved page, ready to blit."""
    number: int
    surface: pygame.Surface
    # note id -> position as a fraction of the page, 0..1 in both axes. Kept
    # unitless so the page can be scaled to any window without the positions
    # having to be re-read.
    spots: dict[str, tuple[float, float]] = field(default_factory=dict)


def _view_box(svg: str) -> tuple[float, float]:
    match = re.search(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"', svg)
    if not match:
        return 0.0, 0.0
    return float(match.group(1)), float(match.group(2))


def engrave(timeline: Timeline, page_width: int = ZOOM_STEPS[DEFAULT_ZOOM],
            ) -> list[TabPage]:
    """Render the whole song. Raises if verovio is not usable."""
    import verovio

    toolkit = verovio.toolkit()
    toolkit.setOptions({
        "scale": ENGRAVING_SCALE,
        "pageWidth": page_width,
        "pageHeight": PAGE_HEIGHT,
        "adjustPageHeight": False,
        "footer": "none",
        "header": "none",
        "breaks": "auto",
    })
    if not toolkit.loadData(to_musicxml(timeline)):
        raise RuntimeError("the engraver could not read the exported score")

    pages: list[TabPage] = []
    for number in range(1, toolkit.getPageCount() + 1):
        svg = toolkit.renderToSVG(number)
        width, height = _view_box(svg)
        surface = pygame.image.load(io.BytesIO(svg.encode()), "page.svg")
        spots = {}
        if width > 0 and height > 0:
            for note_id, (x, y) in note_positions(svg).items():
                spots[note_id] = (x / width, y / height)
        pages.append(TabPage(number=number, surface=surface, spots=spots))
    return pages


class TabEngraving:
    """Where every note of a song was drawn, and where the playhead goes.

    Built once per song and zoom. `at_ms` is the only thing called per frame
    and it is a bisect over a list that never changes.
    """

    def __init__(self, timeline: Timeline, zoom_index: int = DEFAULT_ZOOM):
        self.timeline = timeline
        self.zoom_index = max(0, min(len(ZOOM_STEPS) - 1, zoom_index))
        self.pages = engrave(timeline, ZOOM_STEPS[self.zoom_index])
        # (time, page index, x, y) for every note that was found on a page,
        # in playing order. A note the engraver did not draw is simply absent
        # rather than guessed at.
        self.spots: list[tuple[float, int, float, float]] = []
        for position, note in enumerate(timeline.notes):
            key = f"n{position}"
            for index, page in enumerate(self.pages):
                if key in page.spots:
                    x, y = page.spots[key]
                    self.spots.append((note.timestamp_ms, index, x, y))
                    break
        self.spots.sort()

    @property
    def zoom(self) -> int:
        return ZOOM_STEPS[self.zoom_index]

    def found(self) -> tuple[int, int]:
        """(notes placed, notes written) -- a number that has to be checked.

        An engraving that silently loses a fifth of the song still looks
        like a tab, and the playhead would simply skip those bars.
        """
        return len(self.spots), len(self.timeline.notes)

    def at_ms(self, ms: float) -> tuple[int, float, float] | None:
        """(page, x, y) as fractions, for the playhead at this moment.

        Interpolated between the notes either side while they sit on the same
        line of the same page, and snapped to the next note otherwise: a
        playhead sliding diagonally across a line break is worse than one
        that steps.
        """
        if not self.spots:
            return None
        low, high = 0, len(self.spots)
        while low < high:
            middle = (low + high) // 2
            if self.spots[middle][0] <= ms:
                low = middle + 1
            else:
                high = middle
        if low == 0:
            _, page, x, y = self.spots[0]
            return page, x, y
        before = self.spots[low - 1]
        if low >= len(self.spots):
            return before[1], before[2], before[3]
        after = self.spots[low]
        same_line = (before[1] == after[1]
                     and abs(before[3] - after[3]) < 1e-6
                     and after[2] >= before[2])
        if not same_line or after[0] <= before[0]:
            return before[1], before[2], before[3]
        share = (ms - before[0]) / (after[0] - before[0])
        return before[1], before[2] + (after[2] - before[2]) * share, before[3]
