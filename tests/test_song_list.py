"""Reloading the song list without restarting the app (F5).

A file copied into the songs folder while the app is open was invisible
until it was restarted. Rescanning is the easy half; what these pin down is
everything around it -- the search that must survive, the cursor that must
stay on the same SONG rather than the same row, and the fact that it says
what it found.
"""

import pygame
import pytest

from pickhero.config import Config
from pickhero.ui.menu import MenuScreen


def _key(key):
    return pygame.event.Event(pygame.KEYDOWN, key=key, unicode="", mod=0)


def _typed(char):
    return pygame.event.Event(pygame.KEYDOWN, key=ord(char), unicode=char, mod=0)


@pytest.fixture
def songs(tmp_path):
    for name in ("alpha.gp5", "bravo.gp5", "charlie.gp5"):
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


@pytest.fixture
def menu(songs):
    return MenuScreen(songs, config=Config())


class TestReloading:
    def test_a_file_added_while_the_app_runs_appears(self, menu, songs):
        (songs / "delta.gp5").write_bytes(b"x")
        menu.reload_files()
        assert any(p.name == "delta.gp5" for p in menu._files)

    def test_a_file_removed_disappears(self, menu, songs):
        (songs / "bravo.gp5").unlink()
        menu.reload_files()
        assert not any(p.name == "bravo.gp5" for p in menu._files)

    def test_it_says_what_it_found(self, menu, songs):
        (songs / "delta.gp5").write_bytes(b"x")
        note = menu.reload_files()
        assert "1 new" in note and "4 songs" in note

    def test_it_says_so_when_nothing_changed(self, menu):
        """A refresh that looks exactly like no refresh cannot be told apart
        from a dead key."""
        assert "nothing changed" in menu.reload_files()

    def test_a_deleted_file_is_counted_too(self, menu, songs):
        (songs / "alpha.gp5").unlink()
        assert "1 gone" in menu.reload_files()


class TestWhatMustSurviveIt:
    def test_the_search_is_kept(self, menu, songs):
        """Dropping a song in while hunting for one and coming back to an
        unfiltered list means typing it all again -- and the new file is very
        likely the one being searched for."""
        menu.handle_event(_key(pygame.K_f))
        for ch in "bra":
            menu.handle_event(_typed(ch))
        assert menu._search_text == "bra"
        (songs / "bravado.gp5").write_bytes(b"x")
        menu.reload_files()
        assert menu._search_text == "bra"
        assert {p.name for p in menu._display_files} == {"bravo.gp5", "bravado.gp5"}

    def test_the_cursor_stays_on_the_same_song_not_the_same_row(self, menu,
                                                                songs):
        """The list is sorted, so a file added above the cursor moves every
        row below it."""
        menu._selected = 2                       # charlie.gp5
        assert menu._display_files[menu._selected].name == "charlie.gp5"
        (songs / "aaa.gp5").write_bytes(b"x")    # sorts to the top
        menu.reload_files()
        assert menu._display_files[menu._selected].name == "charlie.gp5"

    def test_a_cursor_on_a_deleted_song_lands_somewhere_real(self, menu, songs):
        menu._selected = 2
        (songs / "charlie.gp5").unlink()
        menu.reload_files()
        assert 0 <= menu._selected < len(menu._display_files)

    def test_an_empty_folder_does_not_crash_the_cursor(self, menu, songs):
        for f in songs.glob("*.gp5"):
            f.unlink()
        menu.reload_files()
        assert menu._selected == 0 and menu._display_files == []


class TestTheKey:
    def test_f5_reloads(self, menu, songs):
        (songs / "delta.gp5").write_bytes(b"x")
        menu.handle_event(_key(pygame.K_F5))
        assert any(p.name == "delta.gp5" for p in menu._files)

    def test_f5_does_not_select_or_leave(self, menu):
        assert menu.handle_event(_key(pygame.K_F5)) is None

    def test_it_works_while_searching_too(self, menu, songs):
        """The search box takes every letter, which is why this is F5."""
        menu.handle_event(_key(pygame.K_f))
        menu.handle_event(_typed("b"))
        (songs / "bravado.gp5").write_bytes(b"x")
        menu.handle_event(_key(pygame.K_F5))
        assert menu._search_active is True
        assert any(p.name == "bravado.gp5" for p in menu._display_files)

    def test_the_note_gives_way_to_the_next_key(self, menu):
        """It is for what just happened, not for the rest of the session."""
        menu.handle_event(_key(pygame.K_F5))
        assert menu._reload_note
        menu.handle_event(_key(pygame.K_DOWN))
        assert menu._reload_note == ""


def _shift(key):
    return pygame.event.Event(pygame.KEYDOWN, key=key, unicode="",
                              mod=pygame.KMOD_LSHIFT)


class TestFavourites:
    """M marks, Shift+M shows only the marked ones."""

    def test_m_stars_the_selected_song(self, menu):
        song = menu._display_files[menu._selected]
        menu.handle_event(_key(pygame.K_m))
        assert menu._config.is_favourite(song.stem)

    def test_m_again_takes_the_star_off(self, menu):
        song = menu._display_files[menu._selected]
        menu.handle_event(_key(pygame.K_m))
        menu.handle_event(_key(pygame.K_m))
        assert not menu._config.is_favourite(song.stem)

    def test_it_says_which_song_it_starred(self, menu):
        """A key that looks dead is a key nobody presses twice."""
        menu.handle_event(_key(pygame.K_m))
        assert "Favourite" in menu._reload_note

    def test_the_star_survives_the_session(self, menu):
        menu.handle_event(_key(pygame.K_m))
        assert menu._config.favourites          # written into the config

    def test_typing_m_into_the_search_does_not_star_anything(self, menu):
        """The search box owns every letter the moment it is open."""
        menu.handle_event(_key(pygame.K_f))
        menu.handle_event(_typed("m"))
        assert menu._config.favourites == []
        assert menu._search_text == "m"


class TestTheFavouritesFilter:
    def _star(self, menu, *stems):
        for stem in stems:
            menu._config.set_favourite(stem, True)
        menu._apply_filter()

    def test_shift_m_shows_only_the_starred_ones(self, menu):
        self._star(menu, "alpha", "charlie")
        menu.handle_event(_shift(pygame.K_m))
        assert {p.stem for p in menu._display_files} == {"alpha", "charlie"}

    def test_shift_m_again_shows_them_all(self, menu):
        self._star(menu, "alpha")
        menu.handle_event(_shift(pygame.K_m))
        menu.handle_event(_shift(pygame.K_m))
        assert len(menu._display_files) == 3

    def test_it_refuses_when_nothing_is_starred(self, menu):
        """A filter that empties the list looks exactly like a list that has
        lost its songs."""
        menu.handle_event(_shift(pygame.K_m))
        assert menu._favourites_only is False
        assert len(menu._display_files) == 3
        assert "No favourites" in menu._reload_note

    def test_the_search_and_the_star_filter_both_apply(self, menu):
        self._star(menu, "alpha", "bravo")
        menu.handle_event(_shift(pygame.K_m))
        menu.handle_event(_key(pygame.K_f))
        for ch in "alp":
            menu.handle_event(_typed(ch))
        assert [p.stem for p in menu._display_files] == ["alpha"]

    def test_unstarring_the_song_you_are_on_keeps_the_cursor_somewhere_real(
            self, menu):
        self._star(menu, "alpha", "bravo")
        menu.handle_event(_shift(pygame.K_m))
        menu.handle_event(_key(pygame.K_m))          # unstar the selected one
        assert 0 <= menu._selected < max(1, len(menu._display_files))
        assert len(menu._display_files) == 1

    def test_the_cursor_stays_on_its_song_when_the_filter_goes_on(self, menu):
        self._star(menu, "bravo", "charlie")
        menu._selected = [p.stem for p in menu._display_files].index("charlie")
        menu.handle_event(_shift(pygame.K_m))
        assert menu._display_files[menu._selected].stem == "charlie"

    def test_a_reload_does_not_lose_the_filter(self, menu, songs):
        self._star(menu, "alpha")
        menu.handle_event(_shift(pygame.K_m))
        (songs / "delta.gp5").write_bytes(b"x")
        menu.reload_files()
        assert menu._favourites_only is True
        assert [p.stem for p in menu._display_files] == ["alpha"]
