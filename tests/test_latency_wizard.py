"""Play along with a click, and the offset sets itself.

*"Warum machen wir dafuer nicht einen konfig Button. Ich spiele Noten, wenn
ich sie sehe und du machst den Wert selbst."*

The second half is the feature. The first half is corrected, because it
would have made the measurement worthless: reacting to something you SEE
takes 150-250 ms, so timing an answer to a shown note fits the offset to a
reaction time and lands further out than the hit window it is meant to
centre. Playing ALONG with a steady pulse is anticipatory.

Nothing in the wizard judges its own measurement -- the clicks are a tab,
the real matcher scores it, and `timing_report` already separates latency
from the playing. These tests are about the three things that ARE new: the
tempo the repeated pitch forces, the string the detector can actually read,
and that the times scored are the ones the clicks really went out at.
"""

import random

import pygame
import pytest

from pickhero import latency
from pickhero.audio.detector import DetectedNote
from pickhero.audio.input import TimestampedNote
from pickhero.audio.note_utils import midi_to_freq, midi_to_name
from pickhero.config import Config
from pickhero.matcher import LATENCY_SEARCH_MS
from pickhero.ui.latency_menu import LatencyMenuScreen

STANDARD = {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40}
DROP_C = {1: 62, 2: 57, 3: 53, 4: 48, 5: 43, 6: 36}


def strike_at(ms, midi):
    return TimestampedNote(
        note=DetectedNote(midi_note=midi, frequency=midi_to_freq(midi),
                          confidence=1.0, name=midi_to_name(midi),
                          is_onset=True),
        timestamp_ms=float(ms), sample_pos=int(ms * 44.1))


# ── the tempo is a constraint, not a comfort ───────────────────────────────

class TestWhyItIsSlow:

    def test_one_click_can_never_be_mistaken_for_its_neighbour(self):
        """Every click is the SAME pitch, so the beat has to be longer than
        the window the candidate search looks in -- or the matcher refuses
        the sample as ambiguous, correctly, and the wizard collects nothing.
        """
        assert latency.spacing_clears_search(latency.CLICK_BPM, 150.0)

    def test_it_holds_at_the_widest_hit_window_the_app_offers(self):
        assert latency.spacing_clears_search(latency.CLICK_BPM, 200.0)

    def test_and_a_faster_tempo_really_would_break_it(self):
        """The control. Without this the class passes on any tempo at all."""
        assert not latency.spacing_clears_search(120.0, 150.0)
        assert latency.beat_ms(120.0) < LATENCY_SEARCH_MS + 150.0

    def test_sixteen_scored_clicks_clear_the_report_s_own_minimum(self):
        from pickhero.ui.latency_menu import MIN_SAMPLES
        assert latency.SCORED_CLICKS >= MIN_SAMPLES


# ── which string, and never the one that folds ─────────────────────────────

class TestWhichStringItAsksFor:

    def test_it_is_never_the_low_E(self):
        """Measured on the tuner's own reference takes: `yinfast` returns a
        THIRD of its pitch on 301 of 301 confident readings of an open low
        E. A folded reading is a subharmonic, `_times_its_own_strike`
        refuses it, and the wizard would look like a broken detector."""
        for tuning in (STANDARD, DROP_C):
            string, pitch = latency.calibration_string(tuning)
            assert string != 6
            assert pitch >= latency.LOWEST_SAFE_MIDI

    def test_it_is_a_middle_string_where_there_is_one(self):
        assert latency.calibration_string(STANDARD) == (4, 50)
        assert latency.calibration_string(DROP_C) == (4, 48)

    def test_a_guitar_where_everything_folds_takes_the_highest(self):
        """Not the preferred one: it is the only string with a chance of
        reading straight at all."""
        basement = {s: 30 + s for s in range(1, 7)}
        string, pitch = latency.calibration_string(basement)
        assert pitch == max(basement.values())

    def test_no_tuning_at_all_still_answers(self):
        assert latency.calibration_string({}) == (4, 50)

    def test_the_player_can_override_it(self):
        screen = LatencyMenuScreen(Config(), STANDARD)
        screen.pick_string(2)
        assert (screen._string, screen._midi) == (2, 59)

    def test_a_string_this_guitar_has_not_got_is_refused(self):
        screen = LatencyMenuScreen(Config(), {4: 50})
        before = (screen._string, screen._midi)
        screen.pick_string(6)
        assert (screen._string, screen._midi) == before


# ── the tab the matcher scores ─────────────────────────────────────────────

class TestTheClicksAsATab:

    def test_only_the_scored_clicks_are_written(self):
        """A lead-in click with a note on it would be counted as a miss, or
        worse, timed while the player was still finding the beat."""
        tab = latency.click_timeline(50, 4)
        assert len(tab.notes) == latency.SCORED_CLICKS
        first = latency.beat_ms() * (latency.LEAD_IN_CLICKS + 1)
        assert tab.notes[0].timestamp_ms == pytest.approx(first)

    def test_a_timeline_is_built_AT_the_times_given(self):
        """`NoteEvent` is frozen, so they cannot be shifted afterwards --
        the first version tried and raised."""
        times = [100.0, 900.0, 1700.0]
        tab = latency.timeline_at(times, 50, 4)
        assert [n.timestamp_ms for n in tab.notes] == times

    def test_every_note_is_plain_and_therefore_timeable(self):
        """A dead, bent, slid or hammered note is refused by
        `_times_its_own_strike`, so a click tab must carry none of them."""
        for note in latency.click_timeline(50, 4).notes:
            assert not note.dead and not note.bend
            assert not note.slide_to_next and not note.hammer_to_next
            assert not note.slide_in and not note.slide_out


# ── the measurement, end to end ────────────────────────────────────────────

class TestWhatItMeasures:

    @staticmethod
    def _ragged(i):
        """A frame loop that never settles. Measured: scored against the
        beats they were DUE on, clicks fired like this read +50.5 ms."""
        return (i * 37) % 100

    def _run(self, delay_ms, jitter_ms=4.0, fired_late=0.0, seed=7,
             tuning=STANDARD):
        """`fired_late=None` means the ragged pattern above."""
        config = Config()
        config.save = lambda: None                     # type: ignore[method-assign]
        config.audio_latency_offset_ms = 0.0
        screen = LatencyMenuScreen(config, tuning)
        screen._began_ms = 0.0
        random.seed(seed)
        screen._fired = [
            c.at_ms + (self._ragged(i) if fired_late is None else fired_late)
            for i, c in enumerate(screen._schedule)]
        screen._strikes = [
            strike_at(at + delay_ms + random.gauss(0.0, jitter_ms),
                      screen._midi)
            for at in screen._fired]
        screen._finish()
        return config.audio_latency_offset_ms, screen._lines

    def test_a_late_hand_gets_a_negative_offset(self):
        stored, _ = self._run(+60.0)
        assert stored == pytest.approx(-60.0, abs=6.0)

    def test_an_early_hand_gets_a_positive_one(self):
        stored, _ = self._run(-40.0)
        assert stored == pytest.approx(+40.0, abs=6.0)

    def test_nothing_to_remove_stores_nothing(self):
        """The control, and the right answer: the report refuses a median
        inside its own noise, and the wizard must not store one anyway."""
        stored, lines = self._run(0.0, jitter_ms=3.0)
        assert stored == 0.0
        assert any("Nothing was stored" in line for line in lines)

    def test_it_scores_the_times_the_clicks_REALLY_went_out(self):
        """A click is fired by the frame loop, so it goes out when a frame
        gets round to it rather than when it was due.

        How much that is worth was MEASURED rather than assumed, and most of
        it is nothing: scored against the beats they were due on, a uniform
        16 ms lateness reads +15.3 ms, one stalled frame of 200 ms reads
        +7.3, and three stalls of 120 ms read +8.2 -- all under `FINE_MS`
        (20), so the report calls them `fine` and the wizard stores nothing
        either way. The median absorbs them.

        What it does NOT absorb is a frame loop that is ragged all the way
        through: firing spread over 0-100 ms reads **+50.5 ms, verdict
        latency** -- a whole offset of pure measurement artefact, stored and
        then shifting every strike in every song. That is the case here.
        """
        stored, _ = self._run(0.0, jitter_ms=3.0, fired_late=None)
        assert stored == 0.0

    def test_and_the_nominal_beats_really_would_have_read_as_latency(self):
        """The control for the one above. Without it that test passes on the
        broken code -- which is exactly what the first version of it did,
        because it used a 16 ms lateness that sits under the report's own
        floor.
        """
        screen = LatencyMenuScreen(Config(), STANDARD)
        nominal = latency.timeline_at(
            [c.at_ms for c in screen._schedule if c.scored],
            screen._midi, screen._string)
        fired = [c.at_ms + self._ragged(i)
                 for i, c in enumerate(screen._schedule) if c.scored]
        errors = sorted(at - note.timestamp_ms
                        for at, note in zip(fired, nominal.notes))
        median = errors[len(errors) // 2]
        from pickhero.matcher import FINE_MS
        assert median > FINE_MS, f"{median} would not be called latency"

    def test_too_few_clicks_measures_nothing_rather_than_guessing(self):
        config = Config()
        config.save = lambda: None                     # type: ignore[method-assign]
        screen = LatencyMenuScreen(config, STANDARD)
        screen._began_ms = 0.0
        screen._schedule = latency.schedule(lead_in=0, scored=3)
        screen._fired = [c.at_ms for c in screen._schedule]
        screen._strikes = [strike_at(c.at_ms + 50.0, screen._midi)
                           for c in screen._schedule]
        screen._finish()
        assert config.audio_latency_offset_ms == 0.0
        assert any("Not enough" in line for line in screen._lines)

    def test_the_mixer_s_own_buffer_is_NAMED_and_not_subtracted(self):
        """The value is right for playing along to something heard through
        this mixer, which is the case it was calibrated for. Subtracting an
        output path nobody measured would be a guess with a decimal point."""
        lines = latency.verdict_text(
            {"verdict": "latency", "median_ms": 60.0, "spread_ms": 4.0,
             "count": 16}, -60.0, output_latency_ms=46.0)
        assert any("46 ms" in line for line in lines)
        assert any("-60 ms" in line for line in lines)

    def test_a_split_between_strings_is_refused_in_its_own_words(self):
        lines = latency.verdict_text(
            {"verdict": "per_string", "median_ms": 60.0, "spread_ms": 40.0,
             "count": 16}, None)
        assert any("Nothing was stored" in line for line in lines)
        assert any("strings disagree" in line for line in lines)

    def test_the_stored_value_is_clamped_to_what_the_config_allows(self):
        from pickhero.config import MAX_LATENCY_OFFSET_MS
        stored, _ = self._run(+2000.0, jitter_ms=2.0)
        assert abs(stored) <= MAX_LATENCY_OFFSET_MS


# ── and the screen itself ──────────────────────────────────────────────────

class TestTheScreen:

    @pytest.fixture(autouse=True)
    def _display(self):
        pygame.init()
        surface = pygame.display.set_mode((1280, 720))
        yield surface

    def _key(self, key):
        return pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode="")

    def test_escape_goes_back(self, _display):
        screen = LatencyMenuScreen(Config(), STANDARD)
        assert screen.handle_event(self._key(pygame.K_ESCAPE)) == "back"

    def test_every_state_draws_something(self, _display):
        from pickhero.ui.colors import get_theme
        screen = LatencyMenuScreen(Config(), STANDARD)
        screen._next_click = latency.LEAD_IN_CLICKS + 3
        for state, lines in (("ready", []), ("running", []),
                             ("error", []),
                             ("done", ["16 notes measured", "set to -60 ms"])):
            screen._state, screen._lines = state, lines
            _display.fill(get_theme().menu_bg)
            screen.render(_display)
            ink = sum(1 for x in range(0, 1280, 3) for y in range(0, 720, 3)
                      if _display.get_at((x, y))[:3] != get_theme().menu_bg)
            assert ink > 200, f"{state} drew {ink} pixels"

    def test_it_says_which_string_and_which_note(self, _display):
        screen = LatencyMenuScreen(Config(), DROP_C)
        assert "4" in screen.prompt() and "C3" in screen.prompt()

    def test_ctrl_c_can_copy_the_measurement(self, _display):
        screen = LatencyMenuScreen(Config(), STANDARD)
        screen._lines = ["16 notes measured, median +60 ms"]
        assert "16 notes measured" in screen.copy_text()

    def test_a_device_that_will_not_open_says_so_and_does_not_raise(
            self, _display, monkeypatch):
        from pickhero.ui import latency_menu

        class Dead:
            def __init__(self, *a, **k):
                pass

            def start(self):
                raise RuntimeError("no such device")

            def stop(self):
                pass

        monkeypatch.setattr(latency_menu, "AudioCapture", Dead)
        screen = LatencyMenuScreen(Config(), STANDARD)
        screen.handle_event(self._key(pygame.K_SPACE))
        assert screen._state == "error"
        screen.render(_display)


class TestTheAppReachesIt:

    def test_the_settings_row_opens_it(self):
        from pickhero.ui.settings_menu import SettingsMenuScreen
        screen = SettingsMenuScreen(Config())
        rows = {row.key: row for row in screen._rows}
        assert rows["latency"].opens == "latency"

    def test_and_left_right_still_sets_it_by_hand(self):
        """ENTER opens the wizard and the arrows keep the hand control --
        `opens` wins on ENTER only, so both fit on one row."""
        from pickhero.ui.settings_menu import SettingsMenuScreen
        config = Config()
        config.save = lambda: None                     # type: ignore[method-assign]
        screen = SettingsMenuScreen(config)
        rows = {row.key: row for row in screen._rows}
        before = config.audio_latency_offset_ms
        rows["latency"].adjust(1)
        assert config.audio_latency_offset_ms != before

    def test_the_app_has_a_place_to_put_the_screen(self):
        from pickhero.ui.app import App
        app = App(Config())
        assert app._latency_menu is None
