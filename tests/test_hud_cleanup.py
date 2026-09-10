"""What stays on the screen while you are trying to read music off it.

A line earns its place by saying something that CHANGES and that nothing
else on screen says. Measured against the old HUD on the player's own
screenshot: eight lines down the left, a caption across the middle and two
rows of twenty-three shortcuts along the bottom, of which he read four.
"""

import pygame
import pytest

from pickhero.audio.note_utils import NAMED_TUNINGS
from pickhero.config import Config
from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)
from pickhero.ui import scrolling
from pickhero.ui.scrolling import PlayingScreen


@pytest.fixture(autouse=True)
def _display():
    pygame.init()
    pygame.display.set_mode((1280, 800))
    yield
    pygame.display.quit()


def _named(name):
    return dict(next(shape for label, shape in NAMED_TUNINGS if label == name))


def _song(tuning=None, bars=8):
    notes, measures = [], []
    for bar in range(bars):
        measures.append(MeasureInfo(index=bar, start_ms=bar * 2000.0,
                                    end_ms=(bar + 1) * 2000.0))
        for beat in range(4):
            notes.append(NoteEvent(timestamp_ms=bar * 2000.0 + beat * 500.0,
                                   duration_ms=400.0, midi_note=40 + beat,
                                   string=6 - beat, fret=beat, measure=bar))
    meta = SongMetadata(title="t", tempo=120)
    meta.tuning = tuning if tuning is not None else _named("Standard")
    return Timeline(notes, meta, measures=measures)


def _screen(tuning=None, transpose=0):
    screen = PlayingScreen(_song(tuning), config=Config())
    screen._transpose = transpose
    return screen


class TestTheTuningsAreOneLine:
    """"Alle Varianten nebeneinander. Nie mehr als 5. Aktive Variante in
    Blau. Original-Variante mit *."

    A tab in Drop C played on a standard-tuned guitar is wrong on every
    single note and nothing else on screen says so -- the notes scroll by
    looking ordinary while every one of them scores red.
    """

    def _played(self, screen):
        return [n for n, role in screen.tuning_segments()
                if role in ("played", "both")]

    def _written(self, screen):
        return [n for n, role in screen.tuning_segments()
                if role in ("written", "both")]

    def test_never_more_than_five(self):
        for transpose in range(0, -6, -1):
            screen = _screen(_named("C Standard"), transpose)
            assert len(screen.tuning_segments()) <= scrolling.TUNINGS_SHOWN

    def test_the_one_being_played_is_always_in_it(self):
        """It is marked in blue, so leaving it out is a blue mark on
        nothing."""
        for transpose in range(0, -6, -1):
            screen = _screen(_named("C Standard"), transpose)
            assert len(self._played(screen)) == 1, f"at {transpose}"

    def test_and_so_is_the_one_it_was_written_in(self):
        for transpose in range(0, -6, -1):
            screen = _screen(_named("C Standard"), transpose)
            assert len(self._written(screen)) == 1, f"at {transpose}"

    def test_at_most_one_step_below_them(self):
        """Further down is a floppy string and a fret that buzzes, not a
        choice anybody would make."""
        screen = _screen(_named("C Standard"), -4)
        order, here = screen._tuning_order()
        shown = [n for n, _ in screen.tuning_segments()]
        written_at = next(i for i, (_, k) in enumerate(order) if k == 0)
        below = min(here, written_at) - (len(order) - len(shown))
        assert below <= scrolling.TUNINGS_BELOW + 1

    def test_the_top_of_the_list_is_the_standard_shaped_one(self):
        screen = _screen(_named("C Standard"), -4)
        assert screen.tuning_segments()[-1][0] == "EADGBE"

    def test_the_notes_are_the_notes_of_that_tuning(self):
        screen = _screen(_named("C Standard"), -4)
        assert ("CFA#D#GC", "played") in screen.tuning_segments()

    def test_a_song_at_its_written_tuning_marks_one_entry_twice(self):
        screen = _screen(_named("Standard"), 0)
        roles = dict(screen.tuning_segments())
        assert roles["EADGBE"] == "both"

    def test_a_song_with_nothing_to_step_to_says_nothing(self):
        screen = PlayingScreen(_song(tuning={}), config=Config())
        assert screen.tuning_segments() == []


class TestTheSyncPanelIsBehindS:
    """"Links oben nichts mehr, aber unten ein Block zum Ein- und
    Ausblenden." None of it is needed while playing and all of it is needed
    while syncing, which is what a key is for."""

    def test_it_starts_shut(self):
        assert _screen()._show_sync is False

    def test_s_opens_it_and_s_shuts_it(self):
        screen = _screen()
        for expected in (True, False, True):
            screen.handle_event(pygame.event.Event(
                pygame.KEYDOWN, key=pygame.K_s, mod=0))
            assert screen._show_sync is expected

    def test_shift_s_still_pins_a_sync_point(self):
        """The panel key must not have eaten the one it advertises."""
        screen = _screen()
        called = []
        screen._set_sync_point = lambda: called.append(1)
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_s, mod=pygame.KMOD_LSHIFT))
        assert called and screen._show_sync is False

    def test_ctrl_s_still_starts_the_automatic_pass(self):
        screen = _screen()
        called = []
        screen._start_auto_sync = lambda: called.append(1)
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_s, mod=pygame.KMOD_LCTRL))
        assert called and screen._show_sync is False

    def test_everything_the_player_listed_is_in_it(self):
        screen = _screen()
        screen._config.backing_offset_ms = -370.0
        screen._sync_lines = ["SYNC   0:10 -257 ms"]
        text = "  ".join(t for t, _ in screen.sync_block_lines())
        assert "Backing: -370 ms (N/M)" in text
        assert "SYNC   0:10 -257 ms" in text
        assert "Sync:" in text          # the strike-timing offset (K)

    def test_and_none_of_it_is_on_the_screen_while_it_is_shut(self):
        screen = _screen()
        screen._sync_lines = ["SYNC   0:10 -257 ms"]
        assert "SYNC   0:10 -257 ms" not in self._drawn(screen)

    def test_but_all_of_it_is_when_it_is_open(self):
        screen = _screen()
        screen._sync_lines = ["SYNC   0:10 -257 ms"]
        screen._show_sync = True
        assert "SYNC   0:10 -257 ms" in self._drawn(screen)

    def _drawn(self, screen) -> str:
        seen = []
        real = scrolling._get_font

        class Watched:
            def __init__(self, font):
                self._font = font

            def render(self, text, *a, **k):
                seen.append(text)
                return self._font.render(text, *a, **k)

            def __getattr__(self, name):
                return getattr(self._font, name)

        scrolling._get_font = lambda *a, **k: Watched(real(*a, **k))
        try:
            screen.render(pygame.Surface((1280, 800)))
        finally:
            scrolling._get_font = real
        return "  ".join(seen)

    def test_the_music_does_not_reflow_when_it_opens(self):
        """Counting the panel's lines as room taken made the sheet re-break
        its lines the moment S was pressed: a smaller head, more bars on a
        row, and a different page while you were looking at it."""
        screen = _screen()
        screen._view = "hybrid"
        surface = pygame.Surface((1280, 800))
        screen.render(surface)
        before = screen._sheet_key
        screen._show_sync = True
        screen.render(surface)
        assert screen._sheet_key == before

    def test_a_warning_inside_it_reaches_the_outside(self):
        """The one line built last session says the recording is guessed
        where you are standing. Shut in a panel it would be invisible at
        exactly the moment it is the only thing that explains the picture."""
        screen = _screen()
        screen._beyond_sync_line = lambda: "SYNC  before the measured part"
        colour = dict(screen.footer_segments())["S: Sync"]
        assert colour == "feedback_close"


class TestWhatIsGoneFromTheLeftColumn:
    """"Hit Windows und Scroll ausblenden. VSYNC-Zeile ausblenden." Each of
    them is a number that is set once and then never looked at again, on a
    screen whose whole job is to be read while both hands are busy."""

    def _drawn(self, screen) -> str:
        return TestTheSyncPanelIsBehindS()._drawn(screen)

    def test_the_hit_window_the_scroll_line_and_the_pacing_are_gone(self):
        screen = _screen()
        text = self._drawn(screen)
        for gone in ("Hit window:", "Scroll:", "Pace:", "vsync:"):
            assert gone not in text, f"{gone} is still on screen"

    def test_and_so_is_the_caption_over_the_music(self):
        screen = _screen()
        screen._view = "hybrid"
        text = self._drawn(screen)
        assert "bars a row" not in text
        assert "BPM" not in text.split("PgDn")[0], "the tempo is still centred"

    def test_the_dropout_warning_stays(self):
        """"Nur Audio dropouts." A machine losing buffers loses notes at
        random, which looks exactly like bad detection or bad playing."""
        screen = _screen()
        screen._audio_enabled = True
        screen._audio_capture = type("C", (), {"dropped_buffers": 12})()
        assert any("Audio dropouts: 12" in t
                   for t, _ in screen._left_notes())

    def test_the_fret_filter_still_says_it_is_on(self):
        """The invisible setting that cost this project a session."""
        screen = _screen()
        screen._max_fret = 5
        assert screen._left_notes(), "a fret limit is silent again"


class TestTheTopOfTheScreenIsMeasured:
    """The music used to start at a fixed 150 px, fitted to a HUD with eight
    lines down the left. With three it left 90 px of empty window above the
    staff and took it off the bottom row."""

    def test_a_shorter_column_gives_the_music_more_room(self):
        screen = _screen()
        tall = screen._hud_top_used()
        screen._max_fret = 5                      # one more line
        assert screen._hud_top_used() > tall

    def test_the_music_starts_below_the_text_not_on_it(self):
        screen = _screen()
        surface = pygame.Surface((1280, 800))
        layout = screen._layout(surface)
        top, _ = screen._tab_room(layout)
        assert top > screen._hud_top_used()
