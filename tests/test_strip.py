"""The strip along the bottom: the arithmetic, without a screen."""

import pytest

from pickhero.tabs.timeline import NoteEvent
from pickhero.ui import strip


def note(ms: float, string: int = 1, fret: int = 0, midi: int = 64):
    return NoteEvent(timestamp_ms=ms, midi_note=midi, string=string,
                     fret=fret, duration_ms=200.0)


# -- the three numbers -----------------------------------------------------

def test_the_split_falls_out_of_what_the_matcher_already_counts():
    # CLOSE has always meant "the right note, played off the beat", so
    # nothing new is measured: 6 on time, 2 late, 2 missed.
    overall, timing, right = strip.split_percentages(
        {"hits": 6, "close": 2, "misses": 2, "total": 10})
    assert overall == pytest.approx(60.0)      # right note, on time
    assert right == pytest.approx(80.0)        # right note at all
    assert timing == pytest.approx(75.0)       # of those, how many landed


def test_nothing_played_is_not_everything_missed():
    # None, never 0.0. A zero is a claim about playing that never happened,
    # and it is what a strip would show for the first bar of every song.
    assert strip.split_percentages(
        {"hits": 0, "close": 0, "misses": 0, "total": 0}) == (None, None, None)


def test_timing_abstains_when_no_note_was_right():
    overall, timing, right = strip.split_percentages(
        {"hits": 0, "close": 0, "misses": 5, "total": 5})
    assert (overall, right) == (0.0, 0.0)
    assert timing is None       # there is no right note to have been late


# -- the mapping, which the drawing and the mouse must agree about ---------

def test_a_click_lands_where_the_marker_was():
    # The whole reason this is one implementation: the marker is put at
    # x_for_ms and the mouse reads ms_for_x back, and a disagreement means a
    # click a bar away from where it was aimed.
    duration, width = 240_000.0, 600
    for ms in (0.0, 1.0, 12_345.0, 120_000.0, 239_999.0):
        x = strip.x_for_ms(ms, duration, width)
        assert strip.ms_for_x(x, duration, width) == pytest.approx(ms, abs=1.0)


def test_a_drag_that_leaves_the_strip_still_scrubs():
    # The mouse does not stop at an edge the hand cannot feel, so the answer
    # is clamped rather than refused.
    assert strip.ms_for_x(-400, 60_000.0, 600) == 0.0
    assert strip.ms_for_x(9_000, 60_000.0, 600) == 60_000.0


def test_a_song_with_no_length_names_no_pixel():
    assert strip.x_for_ms(1000.0, 0.0, 600) == 0.0
    assert strip.ms_for_x(300, 60_000.0, 0) == 0.0


# -- the miniature ---------------------------------------------------------

def test_the_low_e_is_at_the_bottom():
    # The same way up as the board and the sheet. A miniature that flipped it
    # would ask the player to turn the picture over between two glances.
    rows = [strip.row_y(s, 24) for s in range(1, 7)]
    assert rows == sorted(rows)
    assert rows[0] < 24 / 2 < rows[5]


def test_every_pixel_is_named_once():
    # A four-minute song puts dozens of notes on one pixel of one row, and
    # the base surface is built by drawing every one of them.
    notes = [note(ms, string=1) for ms in range(0, 1000, 5)]
    dots = strip.note_dots(notes, 240_000.0, 600, 24)
    assert len(dots) < len(notes)
    assert len({(x, y) for x, y, _ in dots}) == len(dots)


def test_density_survives_the_squeeze():
    # What the miniature is FOR: a solo looks like a solo and a held chord
    # looks like a gap, without reading anything.
    dense = strip.note_dots([note(ms) for ms in range(0, 20_000, 100)],
                            240_000.0, 600, 24)
    sparse = strip.note_dots([note(ms) for ms in range(0, 20_000, 2000)],
                             240_000.0, 600, 24)
    assert len(dense) > len(sparse)


def test_a_note_lands_on_its_own_string_and_its_own_moment():
    n = note(120_000.0, string=6)
    x, y = strip.dot(n, 240_000.0, 600, 24)
    assert x == 300
    assert y == int(strip.row_y(6, 24))


def test_the_band_is_a_constant():
    # Deliberately, and this is the assertion that says so. The music's
    # bottom margin is taken from it and the board's note height from what is
    # left -- and the footer already says "N s ahead", which comes out of that
    # note height. A height derived from what the strip DRAWS would close that
    # circle, which is the 44.4 -> 44.5 px loop this project caught once.
    assert isinstance(strip.STRIP_HEIGHT, int)
    assert strip.STRIP_BAND == strip.STRIP_HEIGHT + strip.STRIP_GAP


# =========================================================================
# And the half that reaches the screen. The arithmetic above is worth
# nothing if nobody calls it -- "a feature that cannot be seen working is
# indistinguishable from one that does not work" is this project's most
# expensive lesson, paid four times.
# =========================================================================

import pygame

from pickhero.config import Config
from pickhero.matcher import MatchType, NoteMatcher
from pickhero.tabs.timeline import MeasureInfo, SongMetadata, Timeline
from pickhero.ui.scrolling import PlayingScreen

BAR_MS = 2000.0


@pytest.fixture
def display():
    pygame.init()
    pygame.display.set_mode((1280, 800))
    yield
    pygame.display.quit()


def _song(bars=40, per_bar=4):
    notes, measures = [], []
    for bar in range(bars):
        measures.append(MeasureInfo(index=bar, start_ms=bar * BAR_MS,
                                    end_ms=(bar + 1) * BAR_MS))
        for beat in range(per_bar):
            notes.append(note(bar * BAR_MS + beat * (BAR_MS / per_bar),
                              string=(beat % 6) + 1, fret=beat,
                              midi=40 + beat))
    return Timeline(notes, SongMetadata(title="t", tempo=120),
                    measures=measures)


def _screen(song=None):
    return PlayingScreen(song or _song(), config=Config())


def _surface():
    return pygame.Surface((1280, 800))


class TestItIsReallyOnTheScreen:

    def test_the_strip_sits_above_the_keys_and_inside_the_window(self, display):
        # Stacked on the MEASURED top of the footer, because the footer wraps.
        # A band at a fixed height is the fault this screen has already been
        # fixed for at the sync panel and at the completion overlay.
        screen, surface = _screen(), _surface()
        screen.render(surface)
        layout = screen._layout(surface)
        rect = screen._strip_rect(layout.screen_w, layout.screen_h)
        _, _, _, footer_top = screen._footer_geometry(layout.screen_w,
                                                      layout.screen_h)
        assert rect.bottom <= footer_top
        assert rect.top > 0 and rect.bottom <= layout.screen_h

    def test_the_music_does_not_reach_into_it(self, display):
        # Both the board and the page. Text and dots over a staff is the
        # fault this file has written up twice.
        for view in ("standard", "hybrid"):
            screen, surface = _screen(), _surface()
            screen._view = view
            screen.render(surface)
            layout = screen._layout(surface)
            rect = screen._strip_rect(layout.screen_w, layout.screen_h)
            if view == "standard":
                music_bottom = layout.lane_top + 6 * layout.lane_height
            else:
                top, room = screen._tab_room(layout)
                music_bottom = top + room
            assert music_bottom <= rect.top, view

    def test_something_is_actually_drawn_down_there(self, display):
        screen, surface = _screen(), _surface()
        screen.render(surface)
        layout = screen._layout(surface)
        mini = screen._strip_mini_rect(layout.screen_w, layout.screen_h)
        band = {tuple(surface.get_at((x, y))[:3])
                for x in range(mini.x + 1, mini.right - 1, 3)
                for y in range(mini.y + 1, mini.bottom - 1, 3)}
        # Its own ground, the dots on it, and the marker: a band of one
        # colour is a band nothing was drawn into.
        assert len(band) > 2, f"the strip drew nothing: {band}"


class TestTheMiniatureHoldsStill:

    def test_it_does_not_move_when_the_first_note_is_judged(self, display):
        # The song would slide sideways once, a bar into every run, and take
        # a drag in progress with it. Found by a verdict landing on the wrong
        # pixel, which is exactly what it would have looked like on screen.
        song = _song(bars=12)
        screen, surface = _screen(song), _surface()
        screen._matcher = NoteMatcher(song)
        screen._audio_enabled = True
        screen.render(surface)
        before = screen._strip_mini_rect(1280, 800)
        screen._matcher._set_state(song.notes[0], MatchType.HIT)
        screen._matcher.hits = 1
        screen.render(surface)
        assert screen._strip_mini_rect(1280, 800) == before


class TestTheNumbersFit:

    def test_the_widest_label_is_not_drawn_over(self, display):
        # The first value was fitted by eye and the miniature covered the
        # word "Notes". Same fault as the footer that was wider than the
        # screen, and the sync panel that grew into the keys.
        from pickhero.ui.scrolling import _get_font
        big, small = _get_font("consolas", 26), _get_font("arial", 12)
        widest = max(small.size(f"100%  {label}")[0]
                     for label in ("Timing", "Right Notes"))
        assert big.size("100%")[0] + 10 + widest <= strip.STRIP_NUMBERS_W


class TestSpooling:
    """The mouse, which this screen had never taken before.

    `handle_event` returned None for every event that was not a key, so a
    pointer never reached the playing screen at all.
    """

    def _mini(self, screen, surface):
        screen.render(surface)
        layout = screen._layout(surface)
        return screen._strip_mini_rect(layout.screen_w, layout.screen_h)

    def test_a_click_jumps_there(self, display):
        screen, surface = _screen(), _surface()
        mini = self._mini(screen, surface)
        x = mini.x + int(mini.width * 0.75)
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1, pos=(x, mini.centery)))
        want = screen._timeline.duration_ms * 0.75
        assert screen._playback_ms == pytest.approx(want, abs=BAR_MS / 2)

    def test_a_click_outside_the_miniature_moves_nothing(self, display):
        screen, surface = _screen(), _surface()
        mini = self._mini(screen, surface)
        screen._playback_ms = 5_000.0
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1, pos=(mini.centerx, 10)))
        assert screen._playback_ms == 5_000.0

    def test_a_drag_moves_the_marker_and_not_the_song(self, display):
        # This is the property that matters. Every seek decodes the recording
        # up to that point, and a dragged mouse fires an event a frame -- the
        # same 25 a second that made a held arrow key stutter for seconds.
        screen, surface = _screen(), _surface()
        mini = self._mini(screen, surface)
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1, pos=(mini.x + 5, mini.centery)))
        landed = screen._playback_ms
        for frac in (0.3, 0.5, 0.8):
            screen.handle_event(pygame.event.Event(
                pygame.MOUSEMOTION,
                pos=(mini.x + int(mini.width * frac), mini.centery)))
            assert screen._playback_ms == landed, "a drag seeked"
            assert screen._strip_preview_ms is not None
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONUP, button=1,
            pos=(mini.x + int(mini.width * 0.8), mini.centery)))
        want = screen._timeline.duration_ms * 0.8
        assert screen._playback_ms == pytest.approx(want, abs=BAR_MS / 2)
        assert screen._strip_preview_ms is None

    def test_a_drag_that_leaves_the_strip_goes_on_scrubbing(self, display):
        screen, surface = _screen(), _surface()
        mini = self._mini(screen, surface)
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1,
            pos=(mini.centerx, mini.centery)))
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEMOTION, pos=(mini.right + 400, mini.centery + 90)))
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONUP, button=1,
            pos=(mini.right + 400, mini.centery + 90)))
        assert screen._playback_ms == pytest.approx(
            screen._timeline.duration_ms, abs=1.0)


class TestNothingHereGrowsWithTheSong:

    def test_the_miniature_is_built_once_and_not_once_a_frame(self, display):
        # It walks every note in the song. That is the loop the page view had
        # to move out of a frame, at 12.4 ms against a 16.7 ms budget.
        screen, surface = _screen(_song(bars=200)), _surface()
        built = []
        real = PlayingScreen._strip_base_surface

        def watched(self, mini):
            hit = self._strip_base is not None and self._strip_base_key == (
                mini.width, mini.height, id(self._timeline),
                self._filter_signature())
            if not hit:
                built.append(mini)
            return real(self, mini)

        PlayingScreen._strip_base_surface = watched
        try:
            for i in range(20):
                screen._playback_ms = i * 500.0
                screen.render(surface)
        finally:
            PlayingScreen._strip_base_surface = real
        assert len(built) == 1, f"rebuilt {len(built)} times"

    def test_a_jump_is_stepped_over_rather_than_walked(self, display):
        # A seek puts every note back to PENDING and the sweep then marks
        # everything behind the playhead missed -- so repainting from the
        # start would draw a run nobody played, as well as walking the song.
        song = _song(bars=200)
        screen, surface = _screen(song), _surface()
        screen._matcher = NoteMatcher(song)
        screen._audio_enabled = True
        screen.render(surface)
        asked = []
        real = screen._matcher.get_note_state
        screen._matcher.get_note_state = (
            lambda n: (asked.append(n), real(n))[1])
        screen._playback_ms = 300_000.0
        screen.render(surface)
        assert len(asked) < len(song.notes) / 8


class TestTheVerdictsLandOnTheStrip:

    def test_a_judged_note_changes_colour_where_it_sits(self, display):
        from pickhero.ui.colors import get_theme
        song = _song(bars=12)
        screen, surface = _screen(song), _surface()
        screen._matcher = NoteMatcher(song)
        screen._audio_enabled = True
        screen.render(surface)
        layout = screen._layout(surface)
        mini = screen._strip_mini_rect(layout.screen_w, layout.screen_h)

        target = song.notes[0]
        screen._matcher._set_state(target, MatchType.HIT)
        screen._matcher.hits = 1
        # Played through rather than jumped to: a jump is deliberately
        # stepped over, because the stretch skipped was never played.
        for ms in range(0, 5001, 400):
            screen._playback_ms = float(ms)
            screen.render(surface)

        x, y = strip.dot(target, song.duration_ms, mini.width, mini.height)
        got = surface.get_at((mini.x + x + 1, mini.y + y))[:3]
        assert got == get_theme().feedback_hit
