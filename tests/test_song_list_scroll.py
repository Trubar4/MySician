"""Sorting by hand, and the list actually moving.

*"Kann ich in der Songübersicht eine Sortierung haben - zuletzt hinzugefügt?
Ich hätte die Sortiervarianten gerne wie die Stimmungen zum mit der Maus
anklicken."*

*"Können wir rechts eine Scrollbar ergänzen? Mit dem Mausrad kann ich zwar
scrollen, aber nicht so, dass sich Songs außerhalb des Bildschirms nach oben
bewegen."*

He was exactly right about the second, and the reason is one missing call:
`_ensure_visible` ran in every KEY handler and not in the wheel's, so the
highlight walked down an unmoving list and off the bottom edge -- measured
at 60 songs as offset 0 after six notches, with the cursor on row 18 and
invisible. The comment sitting over that handler described a rule the code
never had.
"""

import os
import time

import pygame
import pytest

from pickhero.config import Config
from pickhero.tabs import song_index
from pickhero.tabs.song_index import SongIndex, SongInfo
from pickhero.ui.menu import SORT_LABELS, SORT_MODES, MenuScreen

SIZE = (1920, 1080)


@pytest.fixture(autouse=True)
def _display():
    pygame.init()
    pygame.display.set_mode((320, 240))
    yield
    pygame.display.quit()
    pygame.quit()


@pytest.fixture
def menu(tmp_path, monkeypatch):
    """Sixty songs, each written an hour before the one named after it, so
    "added" has an order that is not the alphabet."""
    monkeypatch.setattr(song_index, "index_file",
                        lambda: tmp_path / "index.json")
    monkeypatch.setattr(SongIndex, "scan_in_background",
                        lambda self, files: None)
    songs = tmp_path / "songs"
    songs.mkdir()
    screen = MenuScreen(songs, config=Config())
    now = time.time()
    for i in range(60):
        song = songs / f"Song {i:02d}.gp5"
        song.write_bytes(b"x")
        os.utime(song, (now - i * 3600, now - i * 3600))
        screen._index._record(song, SongInfo(1, ["E A D G B E"]))
    screen.scan_files()
    return screen


def _render(screen):
    surface = pygame.Surface(SIZE)
    screen.render(surface)
    return surface


def _wheel(screen, notches=-1):
    screen.handle_event(pygame.event.Event(pygame.MOUSEWHEEL,
                                           {"y": notches, "x": 0}))


def _click(screen, pos):
    screen.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                                           {"button": 1, "pos": pos}))


class TestTheWheelMovesTheList:
    def test_the_cursor_never_leaves_the_screen(self, menu):
        """The property, not the offset: it fails on the unfixed code at the
        seventh notch, which is the only thing that makes it worth having."""
        _render(menu)
        for notch in range(1, 20):
            _wheel(menu)
            _render(menu)
            assert (menu._scroll_offset <= menu._selected
                    < menu._scroll_offset + menu._visible_items), (
                f"after {notch} notches the highlight is off the screen")

    def test_songs_really_move_up(self, menu):
        _render(menu)
        first = menu._display_files[menu._scroll_offset]
        for _ in range(10):
            _wheel(menu)
            _render(menu)
        assert menu._display_files[menu._scroll_offset] != first

    def test_and_back_up_again(self, menu):
        _render(menu)
        for _ in range(10):
            _wheel(menu)
        _render(menu)
        for _ in range(20):
            _wheel(menu, notches=+1)
        _render(menu)
        assert (menu._scroll_offset <= menu._selected
                < menu._scroll_offset + menu._visible_items) and menu._selected == 0


class TestTheScrollbar:
    def test_it_is_there_when_the_list_is_longer_than_the_screen(self, menu):
        _render(menu)
        assert menu._scroll_track is not None
        assert menu._scroll_thumb is not None

    def test_it_stays_clear_of_the_song_names(self, menu):
        _render(menu)
        assert menu._scroll_track.x >= menu._list_left + menu._list_width

    def test_a_short_list_has_none(self, menu):
        """A bar that is always there and usually full-length is a
        decoration; one that appears when there is something off screen is
        an answer."""
        menu._search_text = "Song 0"
        menu._apply_filter()
        _render(menu)
        assert len(menu._display_files) < menu._visible_items
        assert menu._scroll_track is None

    def test_the_thumb_says_where_you_are(self, menu):
        _render(menu)
        top = menu._scroll_thumb.y
        for _ in range(12):
            _wheel(menu)
        _render(menu)
        assert menu._scroll_thumb.y > top

    def test_dragging_it_moves_the_view_and_not_the_cursor(self, menu):
        """The opposite of the wheel, and deliberately: a notch is a way of
        browsing WITH the cursor, dragging the bar is a way of looking
        somewhere else."""
        _render(menu)
        thumb, track = menu._scroll_thumb, menu._scroll_track
        _click(menu, (thumb.centerx, thumb.y + 3))
        menu.handle_event(pygame.event.Event(
            pygame.MOUSEMOTION, {"pos": (thumb.centerx, track.bottom + 400)}))
        _render(menu)
        assert menu._selected == 0
        assert menu._scroll_offset == len(menu._display_files) - menu._visible_items

    def test_and_one_arrow_key_brings_the_view_back(self, menu):
        _render(menu)
        thumb, track = menu._scroll_thumb, menu._scroll_track
        _click(menu, (thumb.centerx, thumb.y + 3))
        menu.handle_event(pygame.event.Event(
            pygame.MOUSEMOTION, {"pos": (thumb.centerx, track.bottom + 400)}))
        menu.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP,
                                             {"button": 1, "pos": (0, 0)}))
        _render(menu)
        menu.handle_event(pygame.event.Event(pygame.KEYDOWN,
                                             key=pygame.K_DOWN, mod=0,
                                             unicode=""))
        _render(menu)
        assert (menu._scroll_offset <= menu._selected
                < menu._scroll_offset + menu._visible_items)

    def test_letting_go_stops_the_drag(self, menu):
        _render(menu)
        thumb = menu._scroll_thumb
        _click(menu, (thumb.centerx, thumb.y + 3))
        menu.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP,
                                             {"button": 1, "pos": (0, 0)}))
        before = menu._scroll_offset
        menu.handle_event(pygame.event.Event(pygame.MOUSEMOTION,
                                             {"pos": (thumb.centerx, 5000)}))
        assert menu._scroll_offset == before

    def test_clicking_the_track_pages(self, menu):
        _render(menu)
        track, thumb = menu._scroll_track, menu._scroll_thumb
        _click(menu, (track.centerx, thumb.bottom + 10))
        _render(menu)
        assert menu._scroll_offset == menu._visible_items

    def test_a_click_on_the_bar_never_moves_the_cursor(self, menu):
        _render(menu)
        track, thumb = menu._scroll_track, menu._scroll_thumb
        menu._selected = 7
        _click(menu, (track.centerx, thumb.bottom + 10))
        assert menu._selected == 7

    def test_a_filter_cannot_strand_the_view_past_the_end(self, menu):
        _render(menu)
        thumb, track = menu._scroll_thumb, menu._scroll_track
        _click(menu, (thumb.centerx, thumb.y + 3))
        menu.handle_event(pygame.event.Event(
            pygame.MOUSEMOTION, {"pos": (thumb.centerx, track.bottom + 400)}))
        menu._search_text = "Song 1"
        menu._apply_filter()
        _render(menu)
        assert menu._scroll_offset + menu._visible_items <= max(
            menu._visible_items, len(menu._display_files))


class TestSortingByClick:
    def test_every_order_has_a_chip(self, menu):
        _render(menu)
        assert [c.value for c in menu._sort_chips] == SORT_MODES
        assert [c.label for c in menu._sort_chips] == [SORT_LABELS[m]
                                                       for m in SORT_MODES]

    def test_clicking_one_sorts_by_it(self, menu):
        _render(menu)
        chip = next(c for c in menu._sort_chips if c.value == "name_za")
        _click(menu, (chip.x + 4, chip.y + 4))
        assert menu._sort_mode == "name_za"
        assert menu._display_files[0].stem == "Song 59"

    def test_a_click_on_a_chip_never_moves_the_cursor_as_well(self, menu):
        _render(menu)
        menu._selected = 5
        chip = menu._sort_chips[1]
        _click(menu, (chip.x + 4, chip.y + 4))
        assert menu._selected == 0, "the sort moved it, not a row click"

    def test_the_key_and_the_chips_agree(self, menu):
        """One setter for both, so the strip can never show an order the key
        does not produce -- the property `K` and its HUD line are held to."""
        _render(menu)
        seen = []
        for _ in range(len(SORT_MODES)):
            menu.handle_event(pygame.event.Event(pygame.KEYDOWN,
                                                 key=pygame.K_n, mod=0,
                                                 unicode="n"))
            seen.append(menu._sort_mode)
        _render(menu)
        assert sorted(seen) == sorted(SORT_MODES)
        assert sorted(c.value for c in menu._sort_chips) == sorted(SORT_MODES)

    def test_it_is_remembered(self, menu):
        _render(menu)
        chip = next(c for c in menu._sort_chips if c.value == "added")
        _click(menu, (chip.x + 4, chip.y + 4))
        assert menu._config.sort_mode == "added"


class TestAddedIsTheFilesOwnTime:
    def test_newest_first(self, menu):
        menu._set_sort_mode("added")
        assert [f.stem for f in menu._display_files[:3]] == [
            "Song 00", "Song 01", "Song 02"]
        assert menu._display_files[-1].stem == "Song 59"

    def test_it_is_not_the_alphabet(self, menu, tmp_path):
        """Touching the oldest file puts it at the top, which the name
        cannot do."""
        oldest = menu._songs_dir / "Song 59.gp5"
        os.utime(oldest, None)
        menu._set_sort_mode("added")
        assert menu._display_files[0].stem == "Song 59"

    def test_a_file_that_cannot_be_stat_ed_goes_last(self, menu):
        """Zero rather than an exception: an unreadable stat is not news,
        and a sort that raises is a song list that does not come up."""
        from pickhero.ui import menu as menu_module
        assert menu_module._file_mtime(menu._songs_dir / "nothing.gp5") == 0.0


class TestRecentIsWhenItWasLastPlayed:
    """*"Recent könnte last played sein und added wann hinzugefügt."*  Both
    right -- and the two really are different questions, which is why the
    strip carries them both.
    """

    def _played(self, menu, **when):
        from pickhero.progress import ProgressTracker, SongRecord
        tracker = ProgressTracker()
        for stem, stamp in when.items():
            tracker._data[stem.replace("_", " ")] = SongRecord(
                attempts=1, best_accuracy=50.0, last_played=stamp)
        menu._progress = tracker
        menu._set_sort_mode("last_played")
        return [f.stem for f in menu._display_files]

    def test_the_most_recently_played_comes_first(self, menu):
        order = self._played(menu,
                             Song_40="2026-09-01T10:00:00+00:00",
                             Song_10="2026-10-01T10:00:00+00:00")
        assert order[0] == "Song 10"
        assert order[1] == "Song 40"

    def test_a_song_nobody_has_played_sorts_LAST(self, menu):
        """It is not the most recent thing you did -- the same place an
        unplayed song takes under "Best %". Written the other way up every
        song you have never touched comes out at the top, which is what it
        did before this was looked at."""
        order = self._played(menu, Song_30="2026-09-01T10:00:00+00:00")
        assert order[0] == "Song 30"
        assert len(order) == 60

    def test_it_is_a_different_order_from_added(self, menu):
        """The two questions cannot be answered by one chip: the file's time
        says when it arrived in the folder, the record says when you last
        played it, and a song downloaded today and never opened sits at
        opposite ends of the two."""
        recent = self._played(menu, Song_59="2026-10-01T10:00:00+00:00")
        menu._set_sort_mode("added")
        added = [f.stem for f in menu._display_files]
        assert recent[0] == "Song 59"      # the oldest file, played today
        assert added[0] == "Song 00"       # the newest file, never played
