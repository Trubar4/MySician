"""Songs the player has not started, and a mark only a hand can remove.

*"Alle Songs, die ich noch nie gespielt habe, sollen ein Neu haben. Es geht
nur weg, wenn ich es von Hand entferne."*

Both halves pull against each other: it has to be true of every
never-played song without anyone marking them, and it has to SURVIVE the
song being played. So the stored entry is an override and its absence is a
question -- and a song is pinned the moment it is opened, which is the only
reason playing it cannot take the mark away.
"""

import pygame
import pytest

from pickhero.config import Config
from pickhero.progress import ProgressTracker, SongRecord
from pickhero.ui.menu import MenuScreen


def _key(key, mod=0, unicode=""):
    return pygame.event.Event(pygame.KEYDOWN, key=key, unicode=unicode, mod=mod)


@pytest.fixture
def songs(tmp_path):
    for name in ("alpha.gp5", "bravo.gp5", "charlie.gp5"):
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


class _Played(ProgressTracker):
    """A history in which the named songs have been played."""

    def __init__(self, *stems):
        self._stems = set(stems)

    def get_best(self, song_key):
        if song_key in self._stems:
            return SongRecord(attempts=3, best_accuracy=80.0)
        return None


@pytest.fixture
def menu(songs):
    return MenuScreen(songs, config=Config(), progress=_Played("bravo"))


class TestWhatCountsAsNew:

    def test_a_song_never_played_is_new_with_nothing_stored(self):
        """No migration and no scan that invents state: the answer is the
        question asked of the history."""
        c = Config()
        assert c.song_new == {}
        assert c.is_new("alpha", played=False) is True

    def test_a_song_that_has_been_played_is_not(self):
        assert Config().is_new("bravo", played=True) is False

    def test_the_whole_folder_is_new_on_the_first_run(self, songs):
        menu = MenuScreen(songs, config=Config(), progress=_Played())
        assert all(menu._is_new(p) for p in menu._files)

    def test_a_played_song_is_not(self, menu, songs):
        by_name = {p.stem: p for p in menu._files}
        assert menu._is_new(by_name["alpha"]) is True
        assert menu._is_new(by_name["bravo"]) is False


class TestItGoesAwayOnlyByHand:

    def test_playing_it_does_not_take_the_mark_away(self):
        """The whole point, and the reason a stored entry exists at all.
        Opening the song pins it; the history then changes underneath and
        the mark stays."""
        c = Config()
        assert c.pin_new("alpha", played=False) is True
        assert c.is_new("alpha", played=True) is True

    def test_the_pin_writes_nothing_for_a_song_already_played(self):
        """"Not new" needs no pin -- a played song can never become
        unplayed -- and writing it would put an entry beside every song in
        the folder to say what was already known."""
        c = Config()
        assert c.pin_new("bravo", played=True) is False
        assert c.song_new == {}

    def test_the_pin_never_overrules_the_player(self):
        c = Config()
        c.set_new("alpha", False)
        assert c.pin_new("alpha", played=False) is False
        assert c.is_new("alpha", played=False) is False

    def test_ctrl_n_takes_the_mark_off(self, menu):
        menu._selected = [p.stem for p in menu._display_files].index("alpha")
        menu.handle_event(_key(pygame.K_n, mod=pygame.KMOD_CTRL))
        assert menu._is_new(menu._selected_path()) is False

    def test_ctrl_shift_n_puts_it_back_on_a_played_song(self, menu):
        """Which the derived rule alone could never do, and is the reason
        the entry is stored rather than computed."""
        menu._selected = [p.stem for p in menu._display_files].index("bravo")
        menu.handle_event(_key(pygame.K_n,
                               mod=pygame.KMOD_CTRL | pygame.KMOD_SHIFT))
        assert menu._is_new(menu._selected_path()) is True

    def test_pressing_the_same_key_twice_is_harmless(self, menu):
        menu._selected = 0
        menu.handle_event(_key(pygame.K_n, mod=pygame.KMOD_CTRL))
        menu.handle_event(_key(pygame.K_n, mod=pygame.KMOD_CTRL))
        assert menu._is_new(menu._selected_path()) is False


class TestTheKeys:

    def test_ctrl_n_works_while_the_filter_box_is_open(self, menu):
        """Ctrl and not Shift: Shift+N is how a capital N is typed, and a
        filter box that cannot spell "Nirvana" is not a filter box."""
        menu._search_active = True
        menu._selected = 0
        before = menu._selected_path()
        menu.handle_event(_key(pygame.K_n, mod=pygame.KMOD_CTRL))
        assert menu._is_new(before) is False
        assert menu._search_text == ""

    def test_a_plain_n_still_sorts(self, menu):
        """A shortcut that has been under the player's fingers for months
        is not worth taking for a mnemonic."""
        was = menu._sort_mode
        menu.handle_event(_key(pygame.K_n))
        assert menu._sort_mode != was
        assert menu._new_only is False

    def test_shift_n_shows_only_the_new_ones(self, menu):
        menu.handle_event(_key(pygame.K_n, mod=pygame.KMOD_SHIFT))
        assert menu._new_only is True
        assert [p.stem for p in menu._display_files] == ["alpha", "charlie"]
        menu.handle_event(_key(pygame.K_n, mod=pygame.KMOD_SHIFT))
        assert [p.stem for p in menu._display_files] == [
            "alpha", "bravo", "charlie"]

    def test_shift_n_is_read_before_the_plain_key(self, menu):
        """An `if` chain is read in order, and a shifted key placed after
        its unshifted twin is never reached -- which is how the chord view
        once shipped inert."""
        was = menu._sort_mode
        menu.handle_event(_key(pygame.K_n, mod=pygame.KMOD_SHIFT))
        assert menu._sort_mode == was

    def test_a_capital_n_with_no_shift_bit_still_filters(self, menu):
        """The player's own keyboard sends one. `shift_held` is what closes
        that class of fault, and the song list has to be able to reach it."""
        menu.handle_event(_key(pygame.K_n, mod=0, unicode="N"))
        assert menu._new_only is True

    def test_the_filter_refuses_to_empty_the_list(self, songs):
        """A filter that empties the list looks exactly like a list that
        has lost its songs."""
        menu = MenuScreen(songs, config=Config(),
                          progress=_Played("alpha", "bravo", "charlie"))
        menu.handle_event(_key(pygame.K_n, mod=pygame.KMOD_SHIFT))
        assert menu._new_only is False
        assert "new" in (menu._reload_note or "")


class TestItTravels:

    def test_the_mark_is_carried_by_the_sidecar(self, tmp_path):
        """A dict named song_something, so the four readers pick it up
        without anyone writing the name down."""
        from pickhero.tabs import sidecar
        c = Config()
        c.set_new("alpha", False)
        assert "song_new" in sidecar.song_fields(c)
        assert sidecar.collect("alpha", c)["settings"]["song_new"] is False

    def test_it_is_renamed_and_forgotten_with_the_song(self):
        c = Config()
        c.set_new("alpha", True)
        c.rename_song("alpha", "Alpha Band - Alpha")
        assert c.is_new("Alpha Band - Alpha", played=True) is True
        c.forget_song("Alpha Band - Alpha")
        assert c.song_new == {}


class TestTheMarkIsPinnedWhereEverySongGoesThrough:
    """A pin nothing calls is a feature that ships doing nothing, which is
    the fault this project has now shipped four times. So it is asserted
    against the real `_load_song` and not against the helper alone."""

    SONG = (__import__("pathlib").Path(__file__).resolve().parent
            / "fixtures" / "canon.gp5")

    @pytest.fixture
    def app(self):
        if not self.SONG.exists():
            pytest.skip("reference song missing")
        from pickhero.ui.app import App
        pygame.init()
        pygame.display.set_mode((640, 480))
        application = App(Config())
        application._menu = None
        return application

    def test_opening_a_new_song_pins_its_mark(self, app, monkeypatch):
        monkeypatch.setattr(app._config, "save", lambda: None)
        monkeypatch.setattr(app._progress, "get_best", lambda key: None)
        assert self.SONG.stem not in app._config.song_new
        app._load_song(self.SONG)
        assert app._config.song_new[self.SONG.stem] is True

    def test_opening_a_played_song_pins_nothing(self, app, monkeypatch):
        monkeypatch.setattr(app._config, "save", lambda: None)
        monkeypatch.setattr(app._progress, "get_best",
                            lambda key: SongRecord(attempts=2))
        app._load_song(self.SONG)
        assert app._config.song_new == {}

    def test_a_history_that_will_not_read_is_not_a_song_that_cannot_open(
            self, app, monkeypatch):
        def boom(key):
            raise OSError("no history here")
        monkeypatch.setattr(app._progress, "get_best", boom)
        app._load_song(self.SONG)
        assert app._playing_screen is not None


class TestTheKeysAreActuallyOnTheScreen:
    """The song list's hint line measured 2554 px before a single entry was
    added to it, on a 1920 window -- so both ends were simply not there,
    which is how a new shortcut looks exactly like one that never shipped.
    The playing screen was fixed for this; this screen never was."""

    def test_a_line_wider_than_the_window_is_wrapped_at_its_entries(self):
        from pickhero.ui.footer import wrap_on_bars

        class Font:
            def size(self, text):
                return (len(text) * 10, 14)

        line = "  |  ".join(f"key {n}: does a thing" for n in range(10))
        lines = wrap_on_bars(line, Font(), 600)
        assert len(lines) > 1
        assert all(Font().size(one)[0] <= 600 for one in lines)
        # Nothing is lost and no entry is split down the middle.
        assert [b.strip() for b in line.split("|")] == [
            b.strip() for one in lines for b in one.split("|")]

    def test_every_key_the_hint_names_is_drawn(self, menu):
        pygame.display.set_mode((1280, 720))
        surface = pygame.Surface((1280, 720))
        menu.render(surface)
        from pickhero.ui.footer import wrap_on_bars
        from pickhero.ui.scrolling import _get_font
        font = _get_font("arial", 14)
        hint = menu._last_hint
        assert "Ctrl+N" in hint and "Shift+N" in hint
        for one in wrap_on_bars(hint, font, 1280 - 24):
            assert font.size(one)[0] <= 1280 - 24
