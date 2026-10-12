"""Coming back from a pause: a few onsets back, a silent second, best gilt.

*"Nach Pause beim wieder Anlaufen 3 Anschlaege zurueck und 1 Sekunde
einzaehlen. Wenn ich die Noten neu spiele ueberschreibt es sie und sonst
gilt der alte Wert."* — with *bester gilt* as the overwrite rule.

Two halves, tested apart. `_rewind_for_pause` is arithmetic over the
timeline and is driven directly; the verdict floor lives in the matcher and
is driven through `_record_match`, which is the one place a verdict is
written. The last class runs the real `toggle_play` and the real `update()`,
because what the first two cannot see is whether anything calls them.

The control is in the same class rather than in the old code, and
deliberately: `floor_until_ms` did not exist before, so "it fails on the
unfixed tree" cannot even be collected. What CAN be shown is that omitting
the floor is the old behaviour --
`test_without_a_floor_nothing_changes` is that run, and the rewound notes
go red in it. The two together are the before and after.
"""

import time

import pygame
import pytest

from pickhero.config import Config
from pickhero.matcher import MatchType, NoteMatcher
from pickhero.tabs.timeline import MeasureInfo, NoteEvent, SongMetadata, Timeline
from pickhero.ui.scrolling import (
    PAUSE_COUNT_IN_S,
    PAUSE_REWIND_LEAD_MS,
    PAUSE_REWIND_ONSETS,
    PlayingScreen,
)

BAR_MS = 2000.0


def _song(bars=8, strings_per_beat=1):
    """A bar of four onsets, `strings_per_beat` written notes on each."""
    notes, measures = [], []
    for bar in range(bars):
        measures.append(MeasureInfo(index=bar, start_ms=bar * BAR_MS,
                                    end_ms=(bar + 1) * BAR_MS))
        for beat in range(4):
            for s in range(strings_per_beat):
                notes.append(NoteEvent(
                    timestamp_ms=bar * BAR_MS + beat * 500.0,
                    duration_ms=400.0, midi_note=40 + s, string=6 - s,
                    fret=7, measure=bar))
    return Timeline(notes, SongMetadata(title="t", tempo=120),
                    measures=measures)


@pytest.fixture(autouse=True)
def _display():
    pygame.init()
    pygame.display.set_mode((640, 480))
    yield
    pygame.display.quit()
    pygame.quit()


def _screen(**kw):
    screen = PlayingScreen(_song(**kw), config=Config())
    screen._audio_enabled = False
    screen._matcher = NoteMatcher(screen._timeline)
    return screen


def _onsets(screen):
    return sorted({n.timestamp_ms for n in screen._timeline.notes})


def _record(screen, kind, first=0.0, last=None):
    """Put a verdict on notes the way the matcher really does.

    `_set_state` would leave the counters describing a run that never
    happened, and half of what the floor has to keep honest IS the counters.
    """
    for note in screen._timeline.notes:
        if first <= note.timestamp_ms < (last if last is not None else 1e12):
            screen._matcher._record_match(note, kind)


class TestThreeStrokesBack:
    def test_it_lands_a_lead_before_the_third_last_onset(self):
        screen = _screen()
        screen._playback_ms = 4000.0
        was = screen._rewind_for_pause()
        assert was == 4000.0
        behind = [o for o in _onsets(screen) if o < 4000.0]
        assert screen._playback_ms == pytest.approx(
            behind[-PAUSE_REWIND_ONSETS] - PAUSE_REWIND_LEAD_MS)

    def test_the_first_note_is_not_due_the_instant_it_starts(self):
        """Landing ON the onset would have the note due before the picture
        has moved a pixel, and a strike during the hold is DROPPED."""
        screen = _screen()
        screen._playback_ms = 4000.0
        screen._rewind_for_pause()
        nxt = min(o for o in _onsets(screen) if o >= screen._playback_ms)
        assert nxt - screen._playback_ms >= 150.0, \
            "no room for the hit window after the countdown"

    def test_three_STROKES_not_three_written_notes(self):
        """A power chord is three written notes on one stroke. Counting
        notes would rewind a third of a second and call it three."""
        one = _screen(strings_per_beat=1)
        three = _screen(strings_per_beat=3)
        for s in (one, three):
            s._playback_ms = 4000.0
            s._rewind_for_pause()
        assert one._playback_ms == three._playback_ms

    def test_the_count_in_has_nothing_to_rewind_into(self):
        screen = _screen()
        screen._playback_ms = -1200.0
        assert screen._rewind_for_pause() is None
        assert screen._playback_ms == -1200.0

    def test_too_few_onsets_behind_it(self):
        screen = _screen()
        screen._playback_ms = 600.0          # two onsets behind
        assert screen._rewind_for_pause() is None
        assert screen._playback_ms == 600.0

    def test_a_chosen_position_is_left_alone(self):
        """`take_passage` lands a marked passage paused at the loop start.
        Rewinding out of it on the next SPACE is a key fighting the key
        before it."""
        screen = _screen()
        screen._playback_ms = 4000.0
        screen.seek(4000.0)
        assert screen._rewind_for_pause() is None
        assert screen._playback_ms == 4000.0

    def test_and_the_pause_after_that_one_does_rewind(self):
        screen = _screen()
        screen.seek(4000.0)
        assert screen._rewind_for_pause() is None
        assert screen._rewind_for_pause() == 4000.0, \
            "the flag outlived the resume it was for"

    def test_it_never_leaves_a_live_loop(self):
        screen = _screen()
        screen._loop_start_ms = 3900.0
        screen._loop_end_ms = 6000.0
        screen._loop_enabled = True
        screen._playback_ms = 4000.0
        screen._rewind_for_pause()
        assert screen._playback_ms >= 3900.0, \
            "the loop would drag it back on the next frame anyway"

    def test_it_never_moves_forward(self):
        screen = _screen()
        for at in (700.0, 1100.0, 2600.0, 4000.0, 9000.0):
            screen._playback_ms = at
            screen._position_chosen = False
            screen._rewind_for_pause()
            assert screen._playback_ms <= at

    def test_it_is_counted(self):
        screen = _screen()
        screen._playback_ms = 4000.0
        screen._rewind_for_pause()
        screen._position_chosen = False
        screen._playback_ms = 6000.0
        screen._rewind_for_pause()
        assert screen._pause_rewinds == 2


class TestBestGilt:
    """Replayed better it counts, replayed worse or not at all it does not."""

    def _rewound(self, kind=MatchType.HIT, at=4000.0):
        screen = _screen()
        _record(screen, kind, 0.0, at)
        screen._playback_ms = at
        was = screen._rewind_for_pause()
        screen._matcher.forget_from(screen._playback_ms, floor_until_ms=was)
        return screen, was

    def _rewound_notes(self, screen, was):
        return [n for n in screen._timeline.notes
                if screen._playback_ms <= n.timestamp_ms < was]

    def test_a_note_not_replayed_keeps_its_verdict(self):
        screen, was = self._rewound()
        notes = self._rewound_notes(screen, was)
        assert notes, "the fixture rewound over nothing"
        screen._matcher.process_detected_notes([], was + 5000.0)
        for n in notes:
            assert screen._matcher.get_note_state(n) is MatchType.HIT

    def test_replayed_better_it_is_overwritten(self):
        screen, was = self._rewound(kind=MatchType.MISS)
        notes = self._rewound_notes(screen, was)
        for n in notes:
            screen._matcher._record_match(n, MatchType.HIT)
            assert screen._matcher.get_note_state(n) is MatchType.HIT

    def test_replayed_worse_the_old_value_stands(self):
        screen, was = self._rewound()
        notes = self._rewound_notes(screen, was)
        for n in notes:
            screen._matcher._record_match(n, MatchType.CLOSE)
            assert screen._matcher.get_note_state(n) is MatchType.HIT, \
                "coming back in sloppily must not cost a note already right"

    def test_the_counters_say_the_same_as_the_states(self):
        screen, was = self._rewound()
        m = screen._matcher
        for n in self._rewound_notes(screen, was):
            m._record_match(n, MatchType.MISS)
        states = [m.get_note_state(n) for n in screen._timeline.notes]
        assert m.hits == sum(1 for s in states if s is MatchType.HIT)
        assert m.misses == sum(1 for s in states if s is MatchType.MISS)
        assert m.close == sum(1 for s in states if s is MatchType.CLOSE)

    def test_the_floor_stops_at_the_pause(self):
        """Beyond that point nothing has been played in this pass, so the
        ordinary rule applies and a note that is not played goes red."""
        screen = _screen()
        _record(screen, MatchType.HIT)             # the WHOLE song green
        screen._playback_ms = 4000.0
        was = screen._rewind_for_pause()
        screen._matcher.forget_from(screen._playback_ms, floor_until_ms=was)
        screen._matcher.process_detected_notes([], 20000.0)
        ahead = [n for n in screen._timeline.notes if n.timestamp_ms > was]
        assert ahead
        assert all(screen._matcher.get_note_state(n) is MatchType.MISS
                   for n in ahead)

    def test_a_plain_forget_from_clears_it(self):
        """A loop turn and a tempo change pass no floor, and they must not
        inherit one -- or a drill could never score a pass worse than the
        one before it."""
        screen, was = self._rewound()
        screen._matcher.forget_from(0.0)
        notes = self._rewound_notes(screen, was)
        for n in notes:
            screen._matcher._record_match(n, MatchType.MISS)
            assert screen._matcher.get_note_state(n) is MatchType.MISS

    def test_without_a_floor_nothing_changes(self):
        """The control: this is the behaviour every other replay has."""
        screen = _screen()
        _record(screen, MatchType.HIT, 0.0, 4000.0)
        screen._playback_ms = 2000.0
        screen._matcher.forget_from(2000.0)
        screen._matcher.process_detected_notes([], 20000.0)
        replayed = [n for n in screen._timeline.notes
                    if 2000.0 <= n.timestamp_ms < 4000.0]
        assert all(screen._matcher.get_note_state(n) is MatchType.MISS
                   for n in replayed)


class TestTheSpaceBarReallyDoesIt:
    def _playing(self):
        screen = _screen()
        screen.toggle_play()
        screen._playback_ms = 4000.0
        screen._position_chosen = False
        return screen

    def test_pause_then_play_rewinds_and_holds(self):
        screen = self._playing()
        screen.toggle_play()                   # pause
        screen.toggle_play()                   # play
        assert screen._playback_ms < 4000.0
        assert screen._breath_s == pytest.approx(PAUSE_COUNT_IN_S)

    def test_the_hold_is_one_second_not_the_loop_breath(self):
        screen = self._playing()
        screen.toggle_play()
        screen.toggle_play()
        assert screen._breath_s == 1.0

    def test_the_song_really_stands_still_for_it(self):
        screen = self._playing()
        screen.toggle_play()
        screen.toggle_play()
        at = screen._playback_ms
        for _ in range(3):
            screen._last_tick = time.perf_counter() - 0.05
            screen.update()
        assert screen._playback_ms == at
        assert screen._breath_s > 0.0

    def test_and_then_it_runs_again(self):
        screen = self._playing()
        screen.toggle_play()
        screen.toggle_play()
        at = screen._playback_ms
        for _ in range(40):
            screen._last_tick = time.perf_counter() - 0.1
            screen.update()
            if screen._breath_s <= 0.0:
                break
        assert screen._breath_s == 0.0
        screen._last_tick = time.perf_counter() - 0.1
        screen.update()
        assert screen._playback_ms > at

    def test_the_verdicts_behind_it_survive_the_resume(self):
        screen = self._playing()
        _record(screen, MatchType.HIT, 0.0, 4000.0)
        before = screen._matcher.hits
        screen.toggle_play()
        screen.toggle_play()
        for _ in range(40):
            screen._last_tick = time.perf_counter() - 0.1
            screen.update()
            if screen._breath_s <= 0.0:
                break
        screen._matcher.process_detected_notes([], 20000.0)
        assert screen._matcher.hits == before, \
            "the rewind spent verdicts it was meant to give back"

    def test_the_countdown_reaches_the_screen(self):
        """A picture standing still with nothing saying why is what a frozen
        app looks like -- and the countdown used to be drawn only inside the
        drill's line, behind `_loop_enabled`."""
        screen = self._playing()
        surface = pygame.Surface((640, 480))
        screen._breath_s = 0.0
        screen.render(surface)
        quiet = pygame.image.tostring(surface, "RGB")
        screen._breath_s = PAUSE_COUNT_IN_S
        screen.render(surface)
        counting = pygame.image.tostring(surface, "RGB")
        assert quiet != counting, "nothing on screen says it is holding"

    def test_a_seek_while_paused_is_not_rewound(self):
        screen = self._playing()
        screen.toggle_play()
        screen.seek(5000.0)
        screen.toggle_play()
        assert screen._playback_ms == 5000.0
        assert screen._breath_s == 0.0, "no count-in for a spot you chose"

    def test_starting_the_song_is_not_a_resume(self):
        screen = _screen()
        screen.toggle_play()
        assert screen._pause_rewinds == 0
        assert screen._breath_s == 0.0


class TestTheChosenSpotDoesNotOutliveItsReason:
    """An arrow key while PLAYING is not a spot the player is standing on.

    The flag exists for the other case -- a passage landed paused, where the
    next SPACE must not rewind out of it. Left standing it switched the
    rewind off for the rest of the song after any seek.
    """

    def test_a_seek_while_playing_is_spent_by_the_song_running_on(self):
        screen = _screen()
        screen.toggle_play()
        screen.seek(3000.0)
        assert screen._position_chosen
        screen._last_tick = time.perf_counter() - 0.05
        screen.update()
        assert not screen._position_chosen

    def test_so_a_pause_a_minute_later_still_rewinds(self):
        screen = _screen()
        screen.toggle_play()
        screen.seek(3000.0)
        for _ in range(3):
            screen._last_tick = time.perf_counter() - 0.05
            screen.update()
        at = screen._playback_ms
        screen.toggle_play()
        screen.toggle_play()
        assert screen._playback_ms < at
        assert screen._breath_s == pytest.approx(PAUSE_COUNT_IN_S)

    def test_but_a_seek_while_PAUSED_is_still_honoured(self):
        screen = _screen()
        screen.toggle_play()
        screen._playback_ms = 4000.0
        screen.toggle_play()                   # pause
        screen.seek(5000.0)
        screen.toggle_play()                   # play
        assert screen._playback_ms == 5000.0
