"""A tuner is a pitch measured against a target. What it must get right is
what it refuses to say."""

import math

import pygame
import pytest

from pickhero.audio.note_utils import (
    NAMED_TUNINGS, STANDARD_TUNING, midi_to_freq,
)
from pickhero.config import Config
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
