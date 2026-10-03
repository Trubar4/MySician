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

import time

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


def _turn_only(screen):
    """Drive the real loop turn: put the clock past the end and update."""
    screen._playing = True
    screen._playback_ms = screen._loop_end_ms + 1.0
    screen.update()


def _breathe_out(screen, frames=60):
    """Wait out the drill's breath the way real time does.

    It HOLDS the song at the top of the passage, so without this the next
    pass never starts -- and one frame is not enough, because the frame clock
    caps what a single frame may spend. Exactly what the app does.
    """
    for _ in range(frames):
        if screen._breath_s <= 0.0:
            return
        screen._last_tick = time.perf_counter() - 0.1
        screen.update()


def _turn(screen):
    """One whole turn of the loop, breath included."""
    _turn_only(screen)
    _breathe_out(screen)


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


class TestTheBreathAtTheLoopTurn:
    """*"Beim Üben mit Drill brauche ich 1,5 Sekunden Pause, wenn der Loop
    wieder auf Anfang springt."*

    The loop deliberately has no count-in, which is right for a loop being
    played through and wrong for a drill: the hand has to come off the last
    note of the passage and back to the first fret of it. So the picture
    stands still at the top for a moment, and nothing is scored there.
    """

    def _held(self):
        screen = _screen()
        screen.start_drill(0.0, 4000.0)
        _judge(screen, MatchType.HIT, 0, 4000.0)
        _turn_only(screen)
        return screen

    def test_the_turn_holds_the_song(self):
        screen = self._held()
        assert screen._breath_s == pytest.approx(drill_mod.BREATH_S)

    def test_the_picture_does_not_move_while_it_is_held(self):
        screen = self._held()
        screen._last_tick = time.perf_counter() - 0.1
        screen._playback_ms = 0.0
        screen.update()
        assert screen._playback_ms == 0.0
        assert screen._breath_s > 0.0

    def test_and_then_it_runs_again(self):
        screen = self._held()
        # Real seconds, and the frame clock caps what one frame may spend --
        # so it takes several, exactly as it does in the app.
        _breathe_out(screen)
        assert screen._breath_s == 0.0
        before = screen._playback_ms
        screen._last_tick = time.perf_counter() - 0.1
        screen.update()
        assert screen._playback_ms > before

    def test_nothing_is_scored_while_it_is_held(self):
        """A strike arriving during the breath is the hand moving, not the
        passage being played."""
        screen = self._held()
        screen._matcher.forget_from(0.0)
        screen._last_tick = time.perf_counter() - 0.1
        screen.update()
        assert all(screen._matcher.get_note_state(n) == MatchType.PENDING
                   for n in screen._timeline.notes
                   if n.timestamp_ms < 4000.0)

    def test_an_ordinary_loop_gets_no_breath(self):
        """The comment at the loop turn says "no count-in on loop" and means
        it: a bar repeating every few seconds must not stop every time."""
        screen = _screen()
        screen._set_loop_start(0.0)
        screen._set_loop_end(4000.0)
        screen._loop_enabled = True
        _turn_only(screen)
        assert screen._breath_s == 0.0

    def test_leaving_the_drill_lets_the_song_go(self):
        screen = self._held()
        screen._end_drill()
        assert screen._breath_s == 0.0

    def test_so_does_going_somewhere_else(self):
        screen = self._held()
        screen.seek(8000.0)
        assert screen._breath_s == 0.0


def _press(screen, key, mod=0, char=""):
    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod,
                                           unicode=char, scancode=0))


class TestSteppingTheLadderByHand:
    """*"Wie kann ich entscheiden, dass naechste Tempostufe fuer mich jetzt
    passt?"* Until this, he could not: PgUp ENDS the drill by design, so
    there was no way to say "this is fine, move on" without killing it.
    """

    def _drilling(self):
        screen = _screen()
        screen.start_drill(0.0, 4000.0, "bars 1-2")
        return screen

    def test_ctrl_pgup_steps_up_and_keeps_drilling(self):
        screen = self._drilling()
        _press(screen, pygame.K_PAGEUP, pygame.KMOD_LCTRL)
        assert screen._drill is not None
        assert screen._drill.step == 1
        assert screen._tempo_factor == LADDER[1]

    def test_and_the_clean_passes_start_again(self):
        """A step the PLAYER chose credits nothing. `clean_runs` reads the
        same rule over the stored history to say how demanding two clean
        passes are, and a pass nobody played would make that number describe
        runs that never happened."""
        screen = self._drilling()
        _judge(screen, MatchType.HIT, 0, 4000.0)
        _turn(screen)
        assert screen._drill.clean == 1
        _press(screen, pygame.K_PAGEUP, pygame.KMOD_LCTRL)
        assert screen._drill.clean == 0
        assert screen._drill.passes == 1, "the pass that happened still counts"

    def test_ctrl_pgdn_goes_back_down(self):
        screen = self._drilling()
        _press(screen, pygame.K_PAGEUP, pygame.KMOD_LCTRL)
        _press(screen, pygame.K_PAGEDOWN, pygame.KMOD_LCTRL)
        assert screen._drill.step == 0
        assert screen._tempo_factor == LADDER[0]

    def test_the_top_of_the_ladder_says_so(self):
        screen = self._drilling()
        for _ in range(len(LADDER) + 3):
            _press(screen, pygame.K_PAGEUP, pygame.KMOD_LCTRL)
        assert screen._drill.step == len(LADDER) - 1
        assert "already at the top" in (screen._status_note or "")

    def test_the_plain_key_still_ends_the_drill(self):
        """Shift and Ctrl are tested FIRST, because an `if` chain is read in
        order -- and a hand on the bare speed key must still end it."""
        screen = self._drilling()
        _press(screen, pygame.K_PAGEUP)
        assert screen._drill is None

    def test_without_a_drill_it_is_the_ordinary_speed_key(self):
        """The guard is tested first and asks for a RUNNING drill, so with
        none the Ctrl press falls through to the key it was always."""
        screen = _screen()
        before = screen._tempo_factor
        _press(screen, pygame.K_PAGEDOWN, pygame.KMOD_LCTRL)
        assert screen._tempo_factor < before


class TestDrillingTheLoopYouSet:
    """*"Wie kann ich mit dem Zeiger wohin springen, um zu markieren?"* The
    keys for marking already existed -- arrows, then I and O. What was
    missing is the one that drills what they marked."""

    def test_shift_p_drills_the_loop(self):
        screen = _screen()
        screen._set_loop_start(2000.0)
        screen._set_loop_end(6000.0)
        _press(screen, pygame.K_p, pygame.KMOD_LSHIFT, "P")
        assert screen._drill is not None
        assert (screen._drill.start_ms, screen._drill.end_ms) == (2000.0, 6000.0)
        assert screen._tempo_factor == LADDER[0]

    def test_it_names_the_bars(self):
        screen = _screen()
        screen._set_loop_start(2000.0)
        screen._set_loop_end(6000.0)
        _press(screen, pygame.K_p, pygame.KMOD_LSHIFT, "P")
        assert screen._drill.where == "bars 2-3"

    def test_with_no_loop_it_says_so_and_drills_nothing(self):
        screen = _screen()
        _press(screen, pygame.K_p, pygame.KMOD_LSHIFT, "P")
        assert screen._drill is None
        assert "loop" in (screen._status_note or "").lower()

    def test_the_plain_key_still_toggles_the_loop(self):
        screen = _screen()
        screen._set_loop_start(2000.0)      # it refuses without both markers
        screen._set_loop_end(6000.0)
        was = screen._loop_enabled
        _press(screen, pygame.K_p)
        assert screen._loop_enabled != was
        assert screen._drill is None
