"""The screen that says what everything is set to.

Its reason for existing is not that it can change settings -- keys already
could -- but that a setting nobody remembers changing is the one that
explains a bad session. A fret filter left switched on once made whole songs
unplayable with nothing on screen to say so, so what is tested hardest here
is that a changed setting is VISIBLE.
"""

import pygame
import pytest

from pickhero.config import Config
from pickhero.ui.settings_menu import STRING_ORDER, SettingsMenuScreen


def _key(code):
    return pygame.event.Event(pygame.KEYDOWN, {"key": code, "unicode": ""})


def _screen(config=None):
    return SettingsMenuScreen(config or Config())


def _row_index(screen, key):
    return next(i for i, row in enumerate(screen._rows) if row.key == key)


def _select(screen, key):
    screen._selected = _row_index(screen, key)
    return screen._rows[screen._selected]


class TestItShowsWhatIsSet:
    def test_every_row_can_say_its_value(self):
        screen = _screen()
        for row in screen._rows:
            assert isinstance(row.value(), str) and row.value()

    def test_a_fret_limit_is_called_out_as_changed(self):
        """The incident this screen exists for: a filter left on, invisible."""
        config = Config()
        config.max_fret = 5
        screen = _screen(config)
        assert "Fret limit" in [row.label for row in screen.changed_rows()]

    def test_a_muted_string_is_called_out_as_changed(self):
        config = Config()
        config.active_strings[0] = False
        screen = _screen(config)
        assert "Strings played" in [row.label for row in screen.changed_rows()]

    def test_a_fresh_config_has_nothing_to_call_out(self):
        """A screen that always claims something is off teaches the player to
        ignore it."""
        assert _screen().changed_rows() == []

    def test_every_row_explains_itself(self):
        """In terms of the guitar, not of the code. A setting whose effect on
        the score is invisible needs a sentence, or it will be left where it
        is out of fear."""
        for row in _screen()._rows:
            assert row.note.strip()


class TestChangingThings:
    def test_left_and_right_move_the_value(self):
        screen = _screen()
        _select(screen, "window")
        before = screen._config.timing_window_ms
        screen.handle_event(_key(pygame.K_RIGHT))
        assert screen._config.timing_window_ms > before
        screen.handle_event(_key(pygame.K_LEFT))
        assert screen._config.timing_window_ms == before

    def test_a_change_is_saved_at_once(self, tmp_path):
        """There is no OK button, so there must be nothing left to press."""
        import pickhero.config as config_module
        screen = _screen()
        _select(screen, "gate")
        screen.handle_event(_key(pygame.K_LEFT))
        assert config_module.CONFIG_FILE.exists()
        stored = Config.load()
        assert stored.audio.noise_gate_db == screen._config.audio.noise_gate_db

    def test_a_value_cannot_be_pushed_past_its_limit(self):
        screen = _screen()
        _select(screen, "fret")
        for _ in range(60):
            screen.handle_event(_key(pygame.K_LEFT))
        assert screen._config.max_fret >= 1

    def test_r_puts_one_row_back_to_standard(self):
        config = Config()
        config.max_fret = 3
        screen = _screen(config)
        _select(screen, "fret")
        screen.handle_event(_key(pygame.K_r))
        assert screen._config.max_fret == Config().max_fret

    def test_r_leaves_the_other_rows_alone(self):
        """Reset is per row: losing the audio device for having noticed a
        wrong fret limit is a punishment, not a fix."""
        config = Config()
        config.max_fret = 3
        config.audio.device_index = 7
        screen = _screen(config)
        _select(screen, "fret")
        screen.handle_event(_key(pygame.K_r))
        assert screen._config.audio.device_index == 7

    def test_enter_works_a_toggle_without_knowing_it_is_one(self):
        screen = _screen()
        _select(screen, "wait")
        screen.handle_event(_key(pygame.K_RETURN))
        assert screen._config.wait_mode is True


class TestTheStringsRow:
    """Six settings in one row, and the one place an off-by-one would mute the
    wrong string and look like a detection fault."""

    def test_the_cursor_starts_on_the_low_e(self):
        screen = _screen()
        _select(screen, "strings")
        assert STRING_ORDER[screen._string_cursor] == 5

    def test_enter_mutes_the_string_the_cursor_points_at(self):
        screen = _screen()
        _select(screen, "strings")
        screen.handle_event(_key(pygame.K_RIGHT))       # low E -> A
        screen.handle_event(_key(pygame.K_RETURN))
        # The A string is GP string 5, which is index 4.
        assert screen._config.active_strings[4] is False
        assert screen._config.active_strings[5] is True

    def test_the_row_reads_low_string_first(self):
        screen = _screen()
        row = _select(screen, "strings")
        assert row.value().replace("[", "").replace("]", "").split() == \
            ["E", "A", "D", "G", "B", "e"]

    def test_a_muted_string_is_drawn_as_muted(self):
        config = Config()
        config.active_strings[5] = False               # the low E
        screen = _screen(config)
        row = _select(screen, "strings")
        assert "·" in row.value()

    def test_all_six_cannot_be_muted(self):
        """A song with nothing in it reads as a broken app, not a setting."""
        screen = _screen()
        _select(screen, "strings")
        for _ in range(6):
            screen.handle_event(_key(pygame.K_RETURN))
            screen.handle_event(_key(pygame.K_RIGHT))
        assert any(screen._config.active_strings)


class TestGettingAroundIt:
    def test_escape_goes_back(self):
        assert _screen().handle_event(_key(pygame.K_ESCAPE)) == "back"

    def test_enter_opens_the_device_screen(self):
        screen = _screen()
        _select(screen, "device")
        assert screen.handle_event(_key(pygame.K_RETURN)) == "device"

    def test_enter_opens_the_calibration_screen(self):
        screen = _screen()
        _select(screen, "calibrate")
        assert screen.handle_event(_key(pygame.K_RETURN)) == "calibration"

    def test_the_cursor_stays_inside_the_list(self):
        screen = _screen()
        for _ in range(len(screen._rows) + 5):
            screen.handle_event(_key(pygame.K_DOWN))
        assert screen._selected == len(screen._rows) - 1
        for _ in range(len(screen._rows) + 5):
            screen.handle_event(_key(pygame.K_UP))
        assert screen._selected == 0

    def test_a_long_list_scrolls_with_the_cursor(self):
        screen = _screen()
        for _ in range(len(screen._rows)):
            screen.handle_event(_key(pygame.K_DOWN))
        assert screen._scroll > 0


class TestItDraws:
    @pytest.fixture
    def fonts(self):
        pygame.init()
        yield
        pygame.quit()

    def test_it_renders(self, fonts):
        surface = pygame.Surface((1280, 720))
        _screen().render(surface)

    def test_it_renders_with_things_changed(self, fonts):
        config = Config()
        config.max_fret = 5
        config.active_strings[0] = False
        config.tempo_factor = 0.8
        screen = _screen(config)
        _select(screen, "strings")
        screen.render(pygame.Surface((1280, 720)))

    def test_it_renders_in_a_small_window(self, fonts):
        """Resizable window, and the settings list is the longest one there
        is."""
        _screen().render(pygame.Surface((640, 400)))


class TestTheChordViewIsFindable:
    """The player turned it on with Shift+C, could not find it in the
    settings screen, and reasonably concluded it did not exist. Anything set
    once and then living on invisibly belongs on that screen -- that is the
    whole reason it is there.
    """

    def _screen(self):
        from pickhero.config import Config
        from pickhero.ui.settings_menu import SettingsMenuScreen

        config = Config()
        return SettingsMenuScreen(config), config

    def test_there_is_a_row_for_it(self):
        screen, _ = self._screen()
        assert any(row.key == "chord_view" for row in screen._rows)

    def test_it_shows_what_it_is_set_to(self):
        screen, config = self._screen()
        row = next(r for r in screen._rows if r.key == "chord_view")
        assert row.value() == "off"
        config.chord_view = True
        assert row.value() == "on"

    def test_and_changing_it_there_changes_the_setting(self):
        screen, config = self._screen()
        row = next(r for r in screen._rows if r.key == "chord_view")
        row.adjust(1)
        assert config.chord_view is True

    def test_it_is_marked_when_it_is_not_standard(self):
        """A row that is not on its standard value is the one that explains
        a surprise, which is what the marking is for."""
        screen, config = self._screen()
        row = next(r for r in screen._rows if r.key == "chord_view")
        assert row.is_default()
        config.chord_view = True
        assert not row.is_default()

    def test_the_song_screen_starts_from_it_and_writes_it_back(self):
        import pygame
        from pickhero.config import Config
        from pickhero.tabs.timeline import (NoteEvent, SongMetadata, Timeline)
        from pickhero.ui.scrolling import PlayingScreen

        pygame.init()
        pygame.display.set_mode((1280, 720))
        config = Config()
        config.chord_view = True
        config.save = lambda: None
        timeline = Timeline(
            [NoteEvent(timestamp_ms=0.0, duration_ms=100.0, midi_note=40,
                       string=6, fret=0, measure=0)],
            SongMetadata(title="t", tempo=120))
        screen = PlayingScreen(timeline, config=config, song_key="t")
        assert screen._chord_mode
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_c, mod=pygame.KMOD_SHIFT,
            unicode="C"))
        assert not screen._chord_mode
        assert config.chord_view is False


def test_no_two_rows_share_a_key():
    """Two rows with one key is a lookup that silently returns the wrong
    setting. The chord VIEW row was added as "chords", which the chord
    SCORING row two places down already used."""
    from pickhero.config import Config
    from pickhero.ui.settings_menu import SettingsMenuScreen

    keys = [row.key for row in SettingsMenuScreen(Config())._rows]
    assert len(keys) == len(set(keys)), \
        [k for k in keys if keys.count(k) > 1]
