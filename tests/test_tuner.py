"""A tuner is a pitch measured against a target. What it must get right is
what it refuses to say."""

import math

import pygame
import pytest

from pickhero.audio.note_utils import (
    NAMED_TUNINGS, STANDARD_TUNING, midi_to_freq,
)
from pickhero.config import Config
from pickhero.ui import tuner_menu
from pickhero.ui.tuner_menu import (
    CATCH_SEMITONES, IN_TUNE_CENTS, STEADY_MS, TunerMenuScreen,
    nearest_string,
)

DROP_B = {1: 61, 2: 56, 3: 52, 4: 47, 5: 42, 6: 35}


def _cents(midi, cents):
    return midi_to_freq(midi) * (2 ** (cents / 1200.0))


class TestPickingTheString:
    def test_every_open_string_names_itself(self):
        for string, midi in STANDARD_TUNING.items():
            found = nearest_string(midi_to_freq(midi), STANDARD_TUNING)
            assert found is not None
            assert found[0] == string
            assert abs(found[1]) < 0.01

    def test_a_flat_string_is_still_that_string(self):
        """D3 is string 4 -- strings are numbered from the high e down."""
        found = nearest_string(_cents(50, -40.0), STANDARD_TUNING)
        assert found is not None
        assert found[0] == 4 and found[1] == pytest.approx(-40.0, abs=0.5)

    def test_it_abstains_rather_than_guessing(self):
        """A pitch no string owns. A tuner that guesses sends the player the
        wrong way, and further out with every turn."""
        between = midi_to_freq(47)          # B2, three semitones from A2 and D3
        assert nearest_string(between, STANDARD_TUNING) is None

    def test_silence_names_nothing(self):
        assert nearest_string(0.0, STANDARD_TUNING) is None

    @pytest.mark.parametrize("name,tuning", NAMED_TUNINGS)
    def test_no_reading_is_owned_by_two_strings(self, name, tuning):
        """The window must never reach halfway to the neighbour. This is what
        caught DADGAD, whose G and A are a whole tone apart -- a fixed
        2-semitone window owned both."""
        for string, midi in tuning.items():
            for offset in (-0.99, -0.5, 0.0, 0.5, 0.99):
                found = nearest_string(_cents(midi, offset * 100), tuning)
                if found is not None:
                    assert found[0] == string, f"{name} string {string}"

    def test_drop_tunings_work_too(self):
        found = nearest_string(midi_to_freq(35), DROP_B)
        assert found is not None and found[0] == 6


class TestTheScreen:
    def _screen(self, monkeypatch):
        monkeypatch.setattr(TunerMenuScreen, "_start_capture", lambda self: None)
        screen = TunerMenuScreen(Config())
        screen._capture = None
        return screen

    def _hear(self, screen, freq, conf=0.9, times=1):
        class _Fake:
            def get_tuner_data(self, raw=False):
                assert raw, "a tuner must read the pitch before the calibration"
                return (freq, conf)
        screen._capture = _Fake()
        for _ in range(times):
            screen.update()

    def test_it_reads_the_pitch_before_the_calibration(self, monkeypatch):
        """_correct_octave_jump halves a frequency whose half lands near a
        calibrated string, and this player's stored calibration is itself an
        octave out. Wrong about the octave while tuning means they detune
        the guitar to match. The assertion is inside _hear."""
        screen = self._screen(monkeypatch)
        self._hear(screen, midi_to_freq(40))
        assert screen._active == 6

    def test_a_quiet_reading_is_ignored(self, monkeypatch):
        screen = self._screen(monkeypatch)
        self._hear(screen, midi_to_freq(40), conf=0.2)
        assert screen._active is None

    def test_in_tune_has_to_be_held(self, monkeypatch):
        """One frame inside the band is a string passing through the note on
        its way somewhere else."""
        screen = self._screen(monkeypatch)
        self._hear(screen, midi_to_freq(45))
        assert 5 not in screen._done
        import time as _t
        screen._steady_since[5] -= STEADY_MS + 1
        self._hear(screen, midi_to_freq(45))
        assert 5 in screen._done

    def test_going_out_again_takes_the_tick_back(self, monkeypatch):
        screen = self._screen(monkeypatch)
        self._hear(screen, midi_to_freq(45))
        screen._steady_since[5] -= STEADY_MS + 1
        self._hear(screen, midi_to_freq(45))
        assert 5 in screen._done
        self._hear(screen, _cents(45, 60.0), times=40)
        assert 5 not in screen._done

    def test_changing_the_tuning_drops_what_was_measured(self, monkeypatch):
        """Everything measured was measured against the old targets."""
        screen = self._screen(monkeypatch)
        self._hear(screen, midi_to_freq(40))
        assert screen._cents
        screen._choose_tuning(+1)
        assert not screen._cents and screen._active is None

    def test_the_tuning_list_wraps_both_ways(self, monkeypatch):
        screen = self._screen(monkeypatch)
        first = screen.tuning_name
        screen._choose_tuning(-1)
        assert screen.tuning_name != first
        screen._choose_tuning(+1)
        assert screen.tuning_name == first

    def test_escape_leaves(self, monkeypatch):
        screen = self._screen(monkeypatch)
        event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0)
        assert screen.handle_event(event) == "escape"

    @pytest.fixture(autouse=True)
    def _fonts(self):
        pygame.init()
        yield

    def test_it_draws_without_a_device(self, monkeypatch):
        screen = self._screen(monkeypatch)
        screen._error = "no device"
        surface = pygame.Surface((1280, 720))
        screen.render(surface)

    def test_it_draws_with_readings(self, monkeypatch):
        screen = self._screen(monkeypatch)
        self._hear(screen, _cents(40, -30.0))
        screen.render(pygame.Surface((1280, 720)))


class TestItOpensOnTheSongsOwnTuning:
    """The song list shows every song's tuning on its row. Asking the player
    to dial it in again is asking them for something the app has."""

    def test_a_drop_d_song_opens_the_tuner_in_drop_d(self, monkeypatch):
        monkeypatch.setattr(tuner_menu.TunerMenuScreen, "_start_capture",
                            lambda self: None)
        screen = tuner_menu.TunerMenuScreen(Config(), "D A D G B E", "Song")
        assert screen.tuning_name == "Drop D"

    def test_a_tuning_nobody_named_opens_on_standard(self, monkeypatch):
        monkeypatch.setattr(tuner_menu.TunerMenuScreen, "_start_capture",
                            lambda self: None)
        screen = tuner_menu.TunerMenuScreen(Config(), "C G C G C E", "Song")
        assert screen.tuning_name == "Standard"

    def test_and_so_does_no_tuning_at_all(self, monkeypatch):
        monkeypatch.setattr(tuner_menu.TunerMenuScreen, "_start_capture",
                            lambda self: None)
        assert tuner_menu.TunerMenuScreen(Config()).tuning_name == "Standard"

    def test_picking_by_hand_stops_naming_the_song(self, monkeypatch):
        """The line would be describing something no longer true."""
        monkeypatch.setattr(tuner_menu.TunerMenuScreen, "_start_capture",
                            lambda self: None)
        screen = tuner_menu.TunerMenuScreen(Config(), "D A D G B E", "Song")
        assert screen._song == "Song"
        screen._choose_tuning(+1)
        assert screen._song == ""


class TestItSaysWhatToDo:
    """"-34 cents" asks the player to know that negative means flat, and that
    flat means turn the peg the tightening way. The thing they DO is the
    thing to say."""

    def _screen(self, monkeypatch):
        monkeypatch.setattr(tuner_menu.TunerMenuScreen, "_start_capture",
                            lambda self: None)
        return tuner_menu.TunerMenuScreen(Config())

    def test_nothing_heard_yet(self, monkeypatch):
        assert self._screen(monkeypatch).advice() == ("Play a string", "")

    def test_flat_means_tighten(self, monkeypatch):
        screen = self._screen(monkeypatch)
        screen._active = 6
        screen._cents[6] = -34.0
        action, note = screen.advice()
        assert "tighten" in action.lower() and note.startswith("E")

    def test_sharp_means_loosen(self, monkeypatch):
        screen = self._screen(monkeypatch)
        screen._active = 6
        screen._cents[6] = +34.0
        assert "loosen" in screen.advice()[0].lower()

    def test_inside_the_band_but_not_held_yet(self, monkeypatch):
        screen = self._screen(monkeypatch)
        screen._active = 6
        screen._cents[6] = 2.0
        assert screen.advice()[0] == "Hold it…"

    def test_and_held_is_in_tune(self, monkeypatch):
        screen = self._screen(monkeypatch)
        screen._active = 6
        screen._cents[6] = 2.0
        screen._done.add(6)
        assert screen.advice()[0] == "In tune"


class TestNamingTheStringYourself:
    """*"Damit ich beim Stimmen die Saite wählen kann, falls die falsche
    erkannt wird."*

    The bigger half of this is the case he did not name: a string too far
    out for ANY target to own it gets no answer at all from
    `nearest_string`, which is exactly when a tuner is most needed. Naming
    the string is the player saying which one it is, so the catch window
    stops applying.
    """

    def _tuner(self):
        from pickhero.config import Config
        from pickhero.ui.tuner_menu import TunerMenuScreen
        screen = TunerMenuScreen.__new__(TunerMenuScreen)
        screen._config = Config()
        screen._capture = None
        screen._error = ""
        screen._tuning_index = 0
        screen._song = ""
        screen._cents = {}
        screen._steady_since = {}
        screen._done = set()
        screen._active = None
        screen._locked = None
        screen._last_heard = 0.0
        return screen

    def _press(self, screen, key):
        import pygame
        return screen.handle_event(pygame.event.Event(pygame.KEYDOWN,
                                                      key=key, mod=0))

    def test_six_is_the_low_e_the_way_a_guitarist_counts(self):
        import pygame
        from pickhero.audio.note_utils import midi_to_name
        screen = self._tuner()
        self._press(screen, pygame.K_6)
        assert screen._locked == 6
        assert midi_to_name(screen.tuning[6]).startswith("E")

    def test_and_one_is_the_high_one(self):
        import pygame
        from pickhero.audio.note_utils import midi_to_name
        screen = self._tuner()
        self._press(screen, pygame.K_1)
        assert midi_to_name(screen.tuning[1]).startswith("E")
        assert screen.tuning[1] > screen.tuning[6]

    def test_the_same_key_lets_go_again(self):
        """The player who pressed 5 to get away from a wrong guess presses 5
        again to stop, without finding a second key for it."""
        import pygame
        screen = self._tuner()
        self._press(screen, pygame.K_5)
        self._press(screen, pygame.K_5)
        assert screen._locked is None

    def test_another_number_moves_the_lock(self):
        import pygame
        screen = self._tuner()
        self._press(screen, pygame.K_5)
        self._press(screen, pygame.K_3)
        assert screen._locked == 3

    def test_a_string_far_too_flat_is_still_read(self):
        """The case that matters most. Half a tone flat on the low E is 100
        cents out, which `nearest_string` on Standard would still catch --
        but four hundred cents is a string nothing owns, and that is a
        string this tuner used to be silent about."""
        from pickhero.audio.note_utils import midi_to_freq
        from pickhero.ui.tuner_menu import nearest_string
        screen = self._tuner()
        way_out = midi_to_freq(screen.tuning[6]) * 2 ** (-400 / 1200)
        assert nearest_string(way_out, screen.tuning) is None
        import pygame
        self._press(screen, pygame.K_6)
        string, cents = screen._reading(way_out)
        assert string == 6
        assert round(cents) == -400

    def test_an_octave_error_is_still_refused(self):
        """A reading an octave up is the detector being wrong, not the
        string being wrong."""
        from pickhero.audio.note_utils import midi_to_freq
        import pygame
        screen = self._tuner()
        self._press(screen, pygame.K_6)
        assert screen._reading(midi_to_freq(screen.tuning[6]) * 2) is None

    def test_a_neighbouring_string_no_longer_steals_the_reading(self):
        """What he actually asked for: the wrong string being recognised."""
        from pickhero.audio.note_utils import midi_to_freq
        import pygame
        screen = self._tuner()
        a_string = midi_to_freq(screen.tuning[5])
        assert nearest_string_of(screen, a_string) == 5
        self._press(screen, pygame.K_6)
        assert screen._reading(a_string)[0] == 6

    def test_without_a_lock_nothing_changed(self):
        from pickhero.audio.note_utils import midi_to_freq
        screen = self._tuner()
        assert screen._reading(midi_to_freq(screen.tuning[4]))[0] == 4

    def test_letting_go_drops_that_string_s_reading(self):
        """It was measured against a target that is no longer the
        question."""
        import pygame
        screen = self._tuner()
        self._press(screen, pygame.K_2)
        screen._cents[2] = -300.0
        screen._done.add(2)
        self._press(screen, pygame.K_2)
        assert 2 not in screen._cents and 2 not in screen._done

    def test_r_lets_go_too(self):
        import pygame
        screen = self._tuner()
        self._press(screen, pygame.K_6)
        self._press(screen, pygame.K_r)
        assert screen._locked is None

    def test_it_says_which_string_it_is_waiting_for(self):
        import pygame
        screen = self._tuner()
        self._press(screen, pygame.K_4)
        action, note = screen.advice()
        assert action == "Play string 4" and note

    def test_the_keys_are_written_on_the_screen(self):
        """A tuner is read with a guitar in both hands."""
        import inspect
        from pickhero.ui import tuner_menu
        source = inspect.getsource(tuner_menu)
        assert "1-6: pick the string" in source
        assert "str(string)" in source, "the number under each pip"

    def test_a_number_no_tuning_has_is_ignored(self):
        import pygame
        screen = self._tuner()
        screen._locked = 3
        screen._lock(9)
        assert screen._locked == 3


def nearest_string_of(screen, freq):
    from pickhero.ui.tuner_menu import nearest_string
    found = nearest_string(freq, screen.tuning)
    return found[0] if found else None
