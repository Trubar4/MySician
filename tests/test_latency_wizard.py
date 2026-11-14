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


def unpitched_at(ms):
    """A strike loud enough to be real that produced no pitch at all."""
    return TimestampedNote(
        note=DetectedNote(midi_note=0, frequency=0.0, confidence=0.0,
                          name="", is_onset=True, unpitched=True),
        timestamp_ms=float(ms), sample_pos=int(ms * 44.1))


def subharmonic_at(ms, midi):
    """A reading folded up from below the guitar's range."""
    return TimestampedNote(
        note=DetectedNote(midi_note=midi, frequency=midi_to_freq(midi),
                          confidence=1.0, name=midi_to_name(midi),
                          is_onset=True, subharmonic=True),
        timestamp_ms=float(ms), sample_pos=int(ms * 44.1))


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
             tuning=STANDARD, played=None):
        """`fired_late=None` means the ragged pattern above.

        `played` is the pitch the GUITAR sounded, which need not be anything
        the tuning holds -- that is the whole point of reading it back.
        """
        config = Config()
        config.save = lambda: None                     # type: ignore[method-assign]
        config.audio_latency_offset_ms = 0.0
        screen = LatencyMenuScreen(config, tuning)
        screen._began_ms = 0.0
        random.seed(seed)
        screen._fired = [
            c.at_ms + (self._ragged(i) if fired_late is None else fired_late)
            for i, c in enumerate(screen._schedule)]
        pitch = screen._midi if played is None else int(played)
        screen._strikes = [
            strike_at(at + delay_ms + random.gauss(0.0, jitter_ms), pitch)
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


# ── the tuning dependency, which was a real bug ────────────────────────────

class TestItReadsThePitchItHeard:
    """*"Warum muss ich die Gitarre extra drop c tunen, um das timing zu
    syncen?"* He did not: the wizard took the string-4 pitch out of the SONG's
    tuning and wrote the tab at it, so a standard-tuned guitar against a Drop
    C song was asked for MIDI 48, played 50, and nothing matched.
    """

    def test_a_standard_guitar_measures_against_a_drop_c_song(self):
        stored, lines = TestWhatItMeasures()._run(
            +60.0, tuning=DROP_C, played=50)
        assert stored == pytest.approx(-60.0, abs=6.0)

    def test_and_it_says_which_pitch_it_timed_against(self):
        _stored, lines = TestWhatItMeasures()._run(
            +60.0, tuning=DROP_C, played=50)
        assert any("D3" in line for line in lines)

    def test_any_string_at_all_gives_the_same_answer(self):
        """Measured over the guitar's whole range: the pitch is irrelevant to
        WHEN a strike arrived, which is the whole reason this works."""
        answers = [TestWhatItMeasures()._run(+60.0, played=midi)[0]
                   for midi in (40, 45, 48, 50, 55, 59, 64)]
        assert all(a == pytest.approx(answers[0], abs=0.1) for a in answers)

    def test_the_commonest_reading_wins_not_the_first(self):
        votes = [strike_at(0, 61)] + [strike_at(i, 50) for i in range(9)]
        assert latency.pitch_played(votes) == 50

    def test_an_unpitched_strike_does_not_vote(self):
        """It carries no pitch at all -- `midi_note` means nothing there."""
        notes = [strike_at(i, 50) for i in range(3)]
        notes += [unpitched_at(i) for i in range(40)]
        assert latency.pitch_played(notes) == 50

    def test_nor_does_a_subharmonic(self):
        """It names a chord sounding in the room, not one string -- the same
        reason the matcher never scores one."""
        notes = [strike_at(i, 50) for i in range(3)]
        notes += [subharmonic_at(i, 24) for i in range(40)]
        assert latency.pitch_played(notes) == 50

    def test_nothing_readable_measures_nothing(self):
        assert latency.pitch_played([unpitched_at(0)]) is None
        assert latency.pitch_played([]) is None

    def test_a_tie_goes_to_the_lower_pitch(self):
        """A tie is the detector undecided, and the harmonic is the wrong
        one of the two."""
        assert latency.pitch_played([strike_at(0, 50), strike_at(1, 62)]) == 50

    def test_the_string_keys_only_move_the_hint(self):
        screen = LatencyMenuScreen(Config(), STANDARD)
        screen.pick_string(2)
        assert "string 2" in screen.prompt()


# ── the click goes out the way the backing does ────────────────────────────

class TestTheClickCarriesTheBackingOffset:
    """At song time T the MIDI backing sounds the note written at T - B, so
    the player hears it B late and his strike lands B late with it. An offset
    measured without B is wrong by exactly B the moment he plays along.
    """

    def _with(self, backing, hand=60.0, jitter=3.0):
        config = Config()
        config.save = lambda: None                     # type: ignore[method-assign]
        config.audio_latency_offset_ms = 0.0
        config.backing_offset_ms = backing
        screen = LatencyMenuScreen(config, STANDARD)
        screen._began_ms = 0.0
        random.seed(5)
        # The click really goes out `backing` late, and he plays with what he
        # hears rather than with the beat it was due on.
        screen._fired = [c.at_ms + backing for c in screen._schedule]
        screen._strikes = [strike_at(at + hand + random.gauss(0.0, jitter), 50)
                           for at in screen._fired]
        screen._finish()
        return config.audio_latency_offset_ms

    def test_a_delayed_backing_is_part_of_the_answer(self):
        assert self._with(+80.0) == pytest.approx(-140.0, abs=6.0)

    def test_and_so_is_one_that_runs_early(self):
        """-20 rather than -40: at -40 the total is 20 ms, which sits exactly
        on `FINE_MS` and is refused as not an effect. The first version of
        this test chose that value and read the refusal as a fault."""
        assert self._with(-20.0) == pytest.approx(-40.0, abs=6.0)

    def test_a_backing_offset_can_take_the_total_under_the_floor(self):
        """And then nothing is stored, which is the report's rule and not
        this screen's: a median inside its own noise is not an effect, so a
        hand 60 ms late behind a backing 40 ms early has nothing to remove."""
        assert self._with(-40.0) == 0.0

    def test_no_backing_offset_changes_nothing(self):
        """The control: this is what the screen did before, so a song whose
        backing needs no offset must measure exactly as it always did."""
        assert self._with(0.0) == pytest.approx(-60.0, abs=6.0)

    def test_a_backing_that_cancels_the_hand_stores_nothing(self):
        """Not a special case -- it is the arithmetic. With the backing
        already 60 ms early and the hand 60 ms late there is nothing left for
        an offset to remove, and the report refuses a median in its noise."""
        assert self._with(-60.0) == 0.0

    def test_the_frame_s_own_jitter_still_cancels_on_top_of_it(self):
        """Both at once, which is the only arrangement that is right about
        both: the deliberate delay stays in the median and the unpredictable
        lateness drops out."""
        config = Config()
        config.save = lambda: None                     # type: ignore[method-assign]
        config.audio_latency_offset_ms = 0.0
        config.backing_offset_ms = 50.0
        screen = LatencyMenuScreen(config, STANDARD)
        screen._began_ms = 0.0
        random.seed(5)
        screen._fired = [c.at_ms + 50.0 + TestWhatItMeasures._ragged(i)
                         for i, c in enumerate(screen._schedule)]
        screen._strikes = [strike_at(at + random.gauss(0.0, 3.0), 50)
                           for at in screen._fired]
        screen._finish()
        # The hand is exactly on the click it hears, so only the 50 ms of
        # deliberate delay is left.
        assert config.audio_latency_offset_ms == pytest.approx(-50.0, abs=8.0)

    def test_the_global_value_is_used_and_never_a_song_s_own(self):
        """What this screen stores is one number per setup, so it has to be
        measured against the backing delay that is also per setup."""
        config = Config()
        config.backing_offset_ms = 30.0
        config.song_backing_offsets = {"some song": 400.0}
        screen = LatencyMenuScreen(config, STANDARD)
        assert screen._backing_offset() == 30.0

    def test_it_is_said_on_screen_rather_than_applied_in_silence(self):
        lines = latency.verdict_text(
            {"verdict": "latency", "median_ms": 140.0, "spread_ms": 4.0,
             "count": 16}, -140.0, 46.0, heard=50, backing_offset_ms=80.0)
        assert any("80 ms" in line and "backing" in line for line in lines)

    def test_and_nothing_is_said_when_there_is_no_offset(self):
        lines = latency.verdict_text(
            {"verdict": "latency", "median_ms": 60.0, "spread_ms": 4.0,
             "count": 16}, -60.0, 46.0, heard=50, backing_offset_ms=0.0)
        assert not any("backing" in line for line in lines)


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
