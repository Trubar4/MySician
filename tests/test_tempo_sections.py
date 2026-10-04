"""A run played at two speeds is two runs as far as reading it goes.

*"Habe in der Mitte das Tempo von 90 % auf 100 % geaendert. Koennen wir das
irgendwie darstellen in der Auswertung?"* -- and on that run the 90 % half
was also the half where the sound broke up, so one percentage over both
answers neither question. Same rule as strikes-heard beside notes-credited:
two causes that look identical on one number have to be counted apart.
"""

import pygame
import pytest

from pickhero.config import Config
from pickhero.matcher import MatchType, NoteMatcher
from pickhero.played import by_speed
from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)
from pickhero.ui import scrolling, strip
from pickhero.ui.scrolling import PlayingScreen, _Layout


def _song(bars=8, per_bar=4, bar_ms=2000.0):
    notes = []
    measures = []
    for bar in range(bars):
        start = bar * bar_ms
        measures.append(MeasureInfo(index=bar, start_ms=start,
                                    end_ms=start + bar_ms))
        for i in range(per_bar):
            notes.append(NoteEvent(
                timestamp_ms=start + i * (bar_ms / per_bar),
                duration_ms=200.0, midi_note=40 + i, string=6 - i % 6,
                fret=i, measure=bar))
    return Timeline(notes, SongMetadata(title="t", tempo=120),
                    measures=measures)


def _screen(song=None, judged=False):
    """A screen with a matcher, because a run with no verdicts has nothing
    to split -- and a test that skips on that measures nothing."""
    pygame.init()
    pygame.display.set_mode((1280, 720))
    config = Config()
    config.save = lambda: None
    song = song or _song()
    screen = PlayingScreen(song, config=config, song_key="t")
    screen._matcher = NoteMatcher(song)
    if judged:
        _judge(screen)
    return screen


def _judge(screen):
    """Every note resolved, half of them right.

    Done AFTER a tempo change rather than before: changing the speed spends
    the verdicts from there on (`forget_from`), which is the right rule and
    means a test that judges first leaves the second section empty -- and an
    empty section is dropped, so the split would read as no split at all.
    """
    for i, note in enumerate(screen._timeline.notes):
        screen._matcher._set_state(
            note, MatchType.HIT if i % 2 else MatchType.MISS)


class TestTheArithmetic:
    """`by_speed` is plain values and no timeline, so the rule is testable
    without a screen and cannot grow a second opinion about a verdict."""

    def _notes(self):
        # (ms, reached, hit, close)
        return [(0.0, True, False, False), (1000.0, True, False, False),
                (5000.0, True, True, False), (6000.0, True, True, False),
                (9000.0, False, False, False)]

    def test_one_speed_is_one_section(self):
        out = by_speed(self._notes(), [(0.0, 0.9)], [0.0, 2000.0, 4000.0])
        assert len(out) == 1
        assert (out[0].hits, out[0].total) == (2, 4)

    def test_a_change_splits_it_where_it_happened(self):
        out = by_speed(self._notes(), [(0.0, 0.9), (4000.0, 1.0)],
                       [0.0, 2000.0, 4000.0, 6000.0])
        assert [s.speed_text for s in out] == ["90 %", "100 %"]
        assert [(s.hits, s.total) for s in out] == [(0, 2), (2, 2)]
        assert out[0].percent == 0.0 and out[1].percent == 100.0

    def test_a_note_nobody_reached_is_in_no_section(self):
        out = by_speed(self._notes(), [(0.0, 1.0)], [0.0])
        assert out[0].total == 4, "the unreached note is not counted"

    def test_a_section_nobody_reached_is_dropped(self):
        """Nothing played is not everything missed -- the rule
        `Played.percent` already follows, one level up."""
        out = by_speed(self._notes(), [(0.0, 0.9), (8000.0, 1.0)], [0.0])
        assert [s.speed_text for s in out] == ["90 %"]

    def test_it_names_the_bars(self):
        """A range is something you can check against the music; a bare
        percentage is not -- the rule the played-part line already follows."""
        out = by_speed(self._notes(), [(0.0, 0.9), (4000.0, 1.0)],
                       [0.0, 2000.0, 4000.0, 6000.0])
        assert out[0].bars_text() == "bar 1"
        assert (out[1].first_bar, out[1].last_bar) == (3, 4)
        assert out[1].bars_text() == "bars 3-4"


class TestTheScreenRecordsWhereTheSpeedChanged:

    def test_a_fresh_song_has_one_span(self):
        screen = _screen()
        assert screen._tempo_spans == [(0.0, screen._tempo_factor)]
        assert screen._tempo_sections() == []

    def test_a_change_mid_song_appends_one(self):
        screen = _screen()
        screen._playback_ms = 8000.0
        screen.set_tempo_factor(0.8)
        assert [round(at) for at, _ in screen._tempo_spans] == [0, 8000]
        assert screen._tempo_spans[-1][1] == pytest.approx(0.8)

    def test_two_presses_at_one_moment_are_one_span(self):
        """A drill walks its ladder at the loop start, and a player presses
        the key twice. Neither may grow the list without bound."""
        screen = _screen()
        screen._playback_ms = 8000.0
        screen.set_tempo_factor(0.8)
        screen.set_tempo_factor(0.75)
        assert len(screen._tempo_spans) == 2
        assert screen._tempo_spans[-1][1] == pytest.approx(0.75)

    def test_asking_for_the_speed_already_in_force_changes_nothing(self):
        screen = _screen()
        screen._playback_ms = 8000.0
        screen.set_tempo_factor(screen._tempo_factor)
        assert len(screen._tempo_spans) == 1

    def test_going_back_and_changing_spends_what_is_beyond(self):
        """The same shape as `forget_from`, and for the same reason: the
        verdicts beyond that point are spent too, so a span claiming a speed
        for music about to be replayed would describe a run nobody played."""
        screen = _screen()
        screen._playback_ms = 8000.0
        screen.set_tempo_factor(0.8)
        screen._playback_ms = 4000.0
        screen.set_tempo_factor(0.7)
        assert [round(at) for at, _ in screen._tempo_spans] == [0, 4000]

    def test_the_sections_are_scored_apart(self):
        screen = _screen(judged=True)
        screen._playback_ms = 8000.0
        screen.set_tempo_factor(0.8)
        _judge(screen)                    # the rest of the song, at 80 %
        sections = screen._tempo_sections()
        assert [s.speed_text for s in sections] == ["100 %", "80 %"]
        assert sections[0].last_bar <= sections[1].first_bar
        assert (sections[0].total + sections[1].total
                == len(screen._timeline.notes))

    def test_and_one_speed_throughout_says_nothing(self):
        """A single section is the number already on the screen above it."""
        screen = _screen(judged=True)
        assert screen._tempo_sections() == []


class TestTheStripSaysWhereItChanged:
    """The strip is where a run is read back, so the one thing that makes two
    halves of it incomparable belongs in the picture."""

    def _band(self, screen):
        surface = pygame.Surface((1280, 720))
        layout = screen._layout(surface)
        screen._draw_strip(surface, layout)
        mini = screen._strip_mini_rect(1280, 720)
        from pickhero.ui.colors import get_theme
        want = get_theme().feedback_streak[:3]
        return sum(1 for x in range(mini.x, mini.right)
                   for y in range(mini.y, mini.bottom)
                   if surface.get_at((x, y))[:3] == want)

    def test_nothing_is_marked_on_a_run_at_one_speed(self):
        screen = _screen()
        assert self._band(screen) == 0

    def test_and_a_change_draws_a_tick(self):
        screen = _screen()
        screen._playback_ms = 8000.0
        screen.set_tempo_factor(0.8)
        # Away from the tick, or the playhead is drawn over it and the
        # measurement is of the marker.
        screen._playback_ms = 2000.0
        assert self._band(screen) > 0


class TestTheCompletionScreenSaysIt:

    def _lines(self, screen):
        surface = pygame.display.set_mode((950, 620))
        layout = _Layout(screen_w=950, screen_h=620, lane_height=60.0,
                         note_h=40.0, hit_zone_x=150.0, usable_width=800.0,
                         pixels_per_ms=0.2, visible_window_ms=4000.0)
        drawn = []

        class Recorder:
            def __init__(self, target):
                self._target = target

            def blit(self, source, dest, *args, **kwargs):
                if (isinstance(dest, tuple) and source.get_width() < 950
                        and source.get_height() < 200):
                    drawn.append(dest)
                return self._target.blit(source, dest, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._target, name)

        screen._song_completed = True
        screen._draw_completion_overlay(Recorder(surface), layout)
        return drawn

    def test_a_run_at_one_speed_says_nothing_about_speeds(self):
        screen = _screen(judged=True)
        screen._song_completed = True
        before = len(self._lines(screen))
        assert screen._tempo_sections() == []
        assert before == len(self._lines(screen))

    def test_and_a_run_at_two_speeds_grows_by_the_sections(self):
        screen = _screen(judged=True)
        plain = len(self._lines(screen))
        screen._playback_ms = 8000.0
        screen.set_tempo_factor(0.8)
        _judge(screen)
        screen._playback_ms = 16000.0
        assert len(screen._tempo_sections()) == 2
        assert len(self._lines(screen)) >= plain + 3
