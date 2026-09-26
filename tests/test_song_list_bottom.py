"""The bottom of the song list, stacked on measured heights.

*"Unten überlappt der Text im Screenshot."* Three things shared the bottom
edge on fixed offsets -- the device line at -36, the scoring hint at -20, and
a footer that had just been taught to WRAP. 16 px of step for an 18 px font,
and a list whose row count was the constant `VISIBLE_ITEMS`, so on a window
short enough the songs were drawn straight through all of it.

The same fault the playing screen's footer, its sync panel and its completion
overlay have each been fixed for once already.
"""

import pathlib
import pygame
import pytest

from pickhero.config import Config
from pickhero.ui.menu import MenuScreen, VISIBLE_ITEMS, _get_font


@pytest.fixture
def songs(tmp_path):
    for i in range(40):
        (tmp_path / f"Song {i:02d}.gp5").write_bytes(b"x")
    return tmp_path


SIZES = [(1280, 720), (1352, 776), (1600, 900), (1920, 1080), (1024, 600)]


class TestTheListNeverReachesTheBottomBlock:
    @pytest.mark.parametrize("size", SIZES)
    def test_at_every_window_size(self, songs, size):
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            screen = MenuScreen(songs, config=Config())
            surface = pygame.Surface(size)
            screen.render(surface)
            w, h = size
            hint_font = _get_font("arial", 16)
            block_top = h - screen._bottom_height(w, hint_font)
            # The last row, plus the whole "more" line drawn under it --
            # not the 4 px of leading it used to be checked with, which let
            # the arrow itself overlap while every row was clear.
            last_row = (screen._list_top
                        + screen._visible_items * screen._item_h
                        + 4 + hint_font.get_height())
            assert last_row <= block_top, (
                f"{w}x{h}: the list runs {last_row - block_top} px into the "
                f"lines along the bottom")
        finally:
            pygame.display.quit()
            pygame.quit()

    def test_a_tall_window_still_shows_the_full_list(self, songs):
        """The room is a CEILING that was missing, not a new limit: a window
        with space for eighteen rows still gets eighteen."""
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            screen = MenuScreen(songs, config=Config())
            screen.render(pygame.Surface((1920, 1080)))
            assert screen._visible_items == VISIBLE_ITEMS
        finally:
            pygame.display.quit()
            pygame.quit()

    def test_a_short_window_shows_fewer_rather_than_overlapping(self, songs):
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            screen = MenuScreen(songs, config=Config())
            screen.render(pygame.Surface((1280, 700)))
            assert screen._visible_items < VISIBLE_ITEMS
        finally:
            pygame.display.quit()
            pygame.quit()


class TestTheTwoLinesAboveTheFooterDoNotSitOnEachOther:
    def test_the_step_is_the_font_height_not_a_constant(self, songs):
        """They were 16 px apart with an 18 px font, so they were printed
        through each other before the footer even wrapped."""
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            hint_font = _get_font("arial", 16)
            screen = MenuScreen(songs, config=Config())
            screen.render(pygame.Surface((1280, 720)))
            # Two lines, each its own height plus a gap, above the footer.
            block = screen._bottom_height(1280, hint_font)
            floor = 36 + hint_font.get_height() + 2 + 2 * (hint_font.get_height() + 4)
            assert block >= floor
        finally:
            pygame.display.quit()
            pygame.quit()


class TestOneStringForTheFooter:
    def test_the_measurement_and_the_drawing_read_the_same_text(self, songs):
        """`_hint_text` is the one source. Two of them would let the room be
        computed for a footer other than the one drawn."""
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            screen = MenuScreen(songs, config=Config())
            screen.render(pygame.Surface((1280, 720)))
            assert screen._last_hint == screen._hint_text()
        finally:
            pygame.display.quit()
            pygame.quit()

    def test_the_undo_key_is_written_down_where_the_delete_key_is(self, songs):
        screen = MenuScreen(songs, config=Config())
        assert "Ctrl+Z" in screen._hint_text()
        screen._search_active = True
        assert "Ctrl+Z" in screen._hint_text()
