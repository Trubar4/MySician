"""The ladder: slow, twice clean, faster.

*"Ja, fang mit (a) an"* — to the offer that `error_nests` says a passage
keeps going wrong and `N` loops it, and what is missing is the SESSION over
the top: Rocksmith's riff repeater, built out of the loop, the practice
speed and the wait-mode landing this app already had.

The arithmetic is tested on counts, with no matcher and no screen, the way
`runs.error_nests` is tested on strings. What cannot be tested that way is
that the loop turn really drives it, so the last class runs the real
`update()`.
"""

import pygame
import pytest

from pickhero import drill as drill_mod
from pickhero import runs
from pickhero.config import Config
from pickhero.drill import CLEAN, CLEAN_PASSES, LADDER, NOTHING, WRONG, Drill
from pickhero.matcher import MatchType, NoteMatcher
from pickhero.tabs.timeline import MeasureInfo, NoteEvent, SongMetadata, Timeline
from pickhero.ui.scrolling import PlayingScreen


def _drill():
    return Drill(start_ms=0.0, end_ms=4000.0, where="bars 3-5")


def _clean(d, times=1):
    for _ in range(times):
        d.record(wrong=0, judged=8)


class TestTheLadder:
    def test_it_starts_slow(self):
        assert _drill().tempo == LADDER[0] == 0.70

    def test_one_clean_pass_is_not_enough(self):
        d = _drill()
        assert d.record(wrong=0, judged=8) == CLEAN
        assert d.tempo == LADDER[0], "one clean pass of three bars is luck"

    def test_two_clean_passes_step_up(self):
        d = _drill()
        _clean(d, CLEAN_PASSES)
        assert d.tempo == LADDER[1]

    def test_the_whole_way_up(self):
        d = _drill()
        for step in range(len(LADDER)):
            assert d.tempo == LADDER[step]
            _clean(d, CLEAN_PASSES)
        assert d.finished
        assert LADDER[-1] == 1.0, "a ladder that stops below full speed " \
                                  "leaves the passage unplayed at its own speed"

    def test_a_mistake_repeats_the_step(self):
        """*"Nur wiederholen, nie zurück"* — his own choice from three."""
        d = _drill()
        _clean(d)
        assert d.record(wrong=1, judged=8) == WRONG
        assert d.tempo == LADDER[0]
        assert d.clean == 0, "the counter restarts, the speed does not"

    def test_it_never_steps_back(self):
        d = _drill()
        _clean(d, CLEAN_PASSES)
        for _ in range(10):
            d.record(wrong=3, judged=8)
        assert d.tempo == LADDER[1]

    def test_a_pass_nobody_played_is_not_clean(self):
        """Audio off, or a guitar not plugged in. Crediting that would walk
        the ladder to 100 % without a note being heard — the presumption of
        innocence pointed the other way."""
        d = _drill()
        assert d.record(wrong=0, judged=0) == NOTHING
        assert d.clean == 0 and d.tempo == LADDER[0]

    def test_a_finished_drill_records_nothing_more(self):
        d = _drill()
        for _ in range(len(LADDER)):
            _clean(d, CLEAN_PASSES)
        before = d.passes
        assert d.record(wrong=0, judged=8) == NOTHING
        assert d.passes == before

    def test_the_line_says_where_what_speed_and_how_far(self):
        d = _drill()
        _clean(d)
        line = d.line()
        assert "bars 3-5" in line and "70 %" in line
        assert f"1 of {CLEAN_PASSES}" in line


class TestWhatCountsAsAMistake:
    """One rule in the project for it: `runs.is_error`."""

    def test_a_miss_is(self):
        assert drill_mod.count("hhmh") == (1, 4)

    def test_a_close_is_not(self):
        """The right note played off the beat is what the timing percentage
        answers for; counting it here would paint most of a run red."""
        assert drill_mod.count("hcch") == (0, 4)

    def test_a_drained_miss_is_not_either(self):
        """`M` is the app saying it could not tell, which is not evidence of
        a mistake — and it still counts as judged, because something was
        credited there."""
        assert drill_mod.count("hhMh") == (0, 4)

    def test_a_note_never_judged_is_not_counted_at_all(self):
        assert drill_mod.count("hh..") == (0, 2)

    def test_nothing_judged(self):
        assert drill_mod.count("....") == (0, 0)

    def test_the_rule_is_the_one_runs_uses(self):
        for char in (runs.HIT, runs.CLOSE, runs.MISS, runs.NOTHING,
                     "H", "C", "M"):
            wrong, _ = drill_mod.count(char)
            assert bool(wrong) == runs.is_error(char)


BAR_MS = 2000.0


def _song(bars=8):
    notes, measures = [], []
    for bar in range(bars):
        measures.append(MeasureInfo(index=bar, start_ms=bar * BAR_MS,
                                    end_ms=(bar + 1) * BAR_MS))
        for beat in range(4):
            notes.append(NoteEvent(
                timestamp_ms=bar * BAR_MS + beat * 500.0, duration_ms=400.0,
                midi_note=40 + beat, string=6 - beat, fret=beat, measure=bar))
    return Timeline(notes, SongMetadata(title="t", tempo=120),
                    measures=measures)


@pytest.fixture(autouse=True)
def _display():
    pygame.init()
    pygame.display.set_mode((320, 240))
    yield
    pygame.display.quit()
    pygame.quit()


def _screen():
    """A playing screen with a matcher, without opening a device.

    `_start_audio` is what builds one in the app, and it opens the input --
    which this machine has none of. The matcher is the same object either
    way; what is under test is the loop turn reading it.
    """
    screen = PlayingScreen(_song(), config=Config())
    screen._audio_enabled = True
    screen._matcher = NoteMatcher(screen._timeline)
    return screen


def _judge(screen, kind, first=0, last=None):
    """Put a verdict on the notes of the drilled stretch."""
    for note in screen._timeline.notes:
        if first <= note.timestamp_ms < (last if last is not None else 1e9):
            screen._matcher._set_state(note, kind)


def _turn(screen):
    """Drive the real loop turn: put the clock past the end and update."""
    screen._playing = True
    screen._playback_ms = screen._loop_end_ms + 1.0
    screen.update()


class TestTheLoopTurnDrivesIt:
    def test_starting_one_sets_the_loop_and_the_speed(self):
        screen = _screen()
        screen.start_drill(0.0, 4000.0, "bars 1-2")
        assert screen._loop_enabled
        assert (screen._loop_start_ms, screen._loop_end_ms) == (0.0, 4000.0)
        assert screen._tempo_factor == LADDER[0]
        assert not screen._playing, "it lands with the hands free"

    def test_a_clean_pass_counts(self):
        screen = _screen()
        screen.start_drill(0.0, 4000.0)
        _judge(screen, MatchType.HIT, 0, 4000.0)
        _turn(screen)
        assert screen._drill.clean == 1
        assert screen._tempo_factor == LADDER[0]

    def test_two_of_them_change_the_speed_for_real(self):
        screen = _screen()
        screen.start_drill(0.0, 4000.0)
        for _ in range(CLEAN_PASSES):
            _judge(screen, MatchType.HIT, 0, 4000.0)
            _turn(screen)
        assert screen._drill.tempo == LADDER[1]
        assert screen._tempo_factor == LADDER[1]

    def test_a_missed_note_in_the_passage_is_a_mistake(self):
        screen = _screen()
        screen.start_drill(0.0, 4000.0)
        _judge(screen, MatchType.HIT, 0, 4000.0)
        _judge(screen, MatchType.MISS, 500.0, 1000.0)
        _turn(screen)
        assert screen._drill.clean == 0

    def test_a_mistake_OUTSIDE_the_passage_is_not(self):
        """The drill is about the bars it was given and nothing else."""
        screen = _screen()
        screen.start_drill(0.0, 4000.0)
        _judge(screen, MatchType.HIT, 0, 4000.0)
        _judge(screen, MatchType.MISS, 6000.0, 9000.0)
        _turn(screen)
        assert screen._drill.clean == 1

    def test_the_pass_is_scored_before_the_verdicts_are_spent(self):
        """A loop turn calls `forget_from`, which puts the passage back to
        PENDING. Scored after it, every pass would read as nothing heard --
        which is why `_drill_pass` sits where it does."""
        screen = _screen()
        screen.start_drill(0.0, 4000.0)
        _judge(screen, MatchType.HIT, 0, 4000.0)
        _turn(screen)
        assert screen._drill.clean == 1, "scored after forget_from"

    def test_finishing_puts_the_players_own_speed_back(self):
        screen = _screen()
        screen.set_tempo_factor(0.85)
        screen.start_drill(0.0, 4000.0)
        assert screen._tempo_factor == LADDER[0]
        for _ in range(len(LADDER) * CLEAN_PASSES):
            _judge(screen, MatchType.HIT, 0, 4000.0)
            _turn(screen)
        assert screen._drill is None
        assert screen._tempo_factor == 0.85

    def test_setting_the_speed_by_hand_ends_it(self):
        """An automatic that silently undoes what you just set by hand is
        worse than one that was never offered -- the rule the automatic gate
        already follows for X and C."""
        screen = _screen()
        screen.start_drill(0.0, 4000.0)
        screen.set_tempo_factor(0.95)
        assert screen._drill is None
        assert screen._tempo_factor == 0.95, "and the keypress still lands"

    def test_switching_the_loop_off_ends_it(self):
        screen = _screen()
        screen.set_tempo_factor(0.90)
        screen.start_drill(0.0, 4000.0)
        screen._toggle_loop()
        assert screen._drill is None
        assert screen._tempo_factor == 0.90

    def test_leaving_the_song_puts_the_speed_back(self):
        """`set_tempo_factor` STORES the speed per song, so a song left in
        the middle of a ladder would open at 70 % next time with nothing on
        screen to say why."""
        screen = _screen()
        screen.set_tempo_factor(1.0)
        screen.start_drill(0.0, 4000.0)
        screen.stop_audio()
        assert screen._tempo_factor == 1.0

    def test_a_second_drill_does_not_inherit_the_first_ones_step(self):
        screen = _screen()
        screen.set_tempo_factor(1.0)
        screen.start_drill(0.0, 4000.0)
        for _ in range(CLEAN_PASSES):
            _judge(screen, MatchType.HIT, 0, 4000.0)
            _turn(screen)
        assert screen._tempo_factor == LADDER[1]
        screen.start_drill(4000.0, 8000.0)
        assert screen._drill.restore_tempo == 1.0
        screen._toggle_loop()
        assert screen._tempo_factor == 1.0


class TestHowOftenThisPassageHasGoneClean:
    """*"Zwei saubere Durchgänge ist nicht gegen deine echte Historie
    geprüft."*  It is now -- not by moving the constant, but by running the
    drill's own rule over the evenings already on disk.
    """

    def test_it_counts_the_passes_with_no_mistake(self):
        assert drill_mod.clean_runs(["hhh", "hmh", "hhh"]) == (2, 3)

    def test_a_run_that_never_reached_it_does_not_vote(self):
        # The same floor "frequent errors" refuses below: a note nobody got
        # to is evidence of neither.
        assert drill_mod.clean_runs(["hhh", "...", "hmh"]) == (1, 2)

    def test_a_drained_verdict_is_still_a_judged_note(self):
        # A pass where everything was credited to a strum reached the bars,
        # and `is_error` says a drained miss is not a mistake to act on --
        # which is the rule the ladder itself uses, so they agree by sharing
        # it rather than by being written twice.
        assert drill_mod.clean_runs(["HHH"]) == (1, 1)

    def test_nothing_at_all_is_no_rate(self):
        assert drill_mod.clean_runs([]) == (0, 0)
        assert drill_mod.clean_runs(["....", "...."]) == (0, 0)

    def test_it_is_the_same_rule_the_ladder_judges_a_pass_by(self):
        # Whatever marks are put in, a passage counted clean here is exactly
        # one the drill would have accepted.
        for marks in ("hhh", "hmh", "hch", "hHh", "hMh", "...", "h.m", "cCc"):
            wrong, judged = drill_mod.count(marks)
            clean, reached = drill_mod.clean_runs([marks])
            assert reached == (1 if judged else 0)
            assert clean == (1 if judged and not wrong else 0)
