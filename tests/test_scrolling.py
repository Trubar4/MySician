"""Tests for scrolling display math.

Tests the pure computation functions in PlayingScreen without needing
a running PyGame display.
"""

import pygame
import pytest

from pickhero.config import Config
from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline
from pickhero.ui.scrolling import (
    MIN_NOTE_WIDTH_PX,
    PlayingScreen,
    format_time,
)


def _make_timeline(tempo: int = 120, notes: list[NoteEvent] | None = None) -> Timeline:
    """Create a timeline with given tempo and notes."""
    meta = SongMetadata(title="Test", artist="Tester", tempo=tempo)
    return Timeline(notes or [], meta)


class TestFormatTime:
    def test_zero(self):
        assert format_time(0) == "0:00"

    def test_one_minute(self):
        assert format_time(60_000) == "1:00"

    def test_seconds_padded(self):
        assert format_time(5_000) == "0:05"

    def test_mixed(self):
        assert format_time(125_000) == "2:05"

    def test_negative_clamps_to_zero(self):
        assert format_time(-1000) == "0:00"

    def test_fractional_truncates(self):
        assert format_time(61_999) == "1:01"


class TestNoteX:
    """Test PlayingScreen.note_x static method."""

    def test_note_at_playback_time_sits_on_hit_zone(self):
        hit_zone_x = 256.0
        pixels_per_ms = 0.5
        playback_ms = 1000.0
        x = PlayingScreen.note_x(1000.0, playback_ms, hit_zone_x, pixels_per_ms)
        assert x == pytest.approx(hit_zone_x)

    def test_future_note_is_right_of_hit_zone(self):
        hit_zone_x = 256.0
        pixels_per_ms = 0.5
        playback_ms = 1000.0
        x = PlayingScreen.note_x(2000.0, playback_ms, hit_zone_x, pixels_per_ms)
        assert x > hit_zone_x
        assert x == pytest.approx(256.0 + 1000.0 * 0.5)

    def test_past_note_is_left_of_hit_zone(self):
        hit_zone_x = 256.0
        pixels_per_ms = 0.5
        playback_ms = 1000.0
        x = PlayingScreen.note_x(500.0, playback_ms, hit_zone_x, pixels_per_ms)
        assert x < hit_zone_x

    def test_proportional_to_time_difference(self):
        hit_zone_x = 200.0
        pixels_per_ms = 1.0
        playback_ms = 0.0
        x1 = PlayingScreen.note_x(100.0, playback_ms, hit_zone_x, pixels_per_ms)
        x2 = PlayingScreen.note_x(200.0, playback_ms, hit_zone_x, pixels_per_ms)
        assert x2 - x1 == pytest.approx(100.0)


class TestNoteWidth:
    """Test PlayingScreen.note_width static method."""

    def test_enforces_minimum(self):
        w = PlayingScreen.note_width(1.0, 0.5)  # 0.5 px, below minimum
        assert w == MIN_NOTE_WIDTH_PX

    def test_long_note_exceeds_minimum(self):
        w = PlayingScreen.note_width(500.0, 0.5)  # 250 px
        assert w == pytest.approx(250.0)

    def test_zero_duration(self):
        w = PlayingScreen.note_width(0.0, 1.0)
        assert w == MIN_NOTE_WIDTH_PX


class TestPlayingScreenLayout:
    """Test layout computation with a mock surface."""

    class _MockSurface:
        def __init__(self, w: int, h: int):
            self._size = (w, h)

        def get_size(self):
            return self._size

    def test_layout_dimensions(self):
        timeline = _make_timeline(tempo=120)
        screen = PlayingScreen(timeline, visible_beats=4, hit_zone_fraction=0.20)
        surface = self._MockSurface(1280, 720)
        layout = screen._layout(surface)

        assert layout.screen_w == 1280
        assert layout.screen_h == 720
        assert layout.hit_zone_x == pytest.approx(1280 * 0.20)

        # Fixed 8-second visible window
        expected_window = 8000.0
        assert screen._visible_window_ms == pytest.approx(expected_window)

        usable = 1280 - layout.hit_zone_x
        assert layout.usable_width == pytest.approx(usable)
        assert layout.pixels_per_ms == pytest.approx(usable / expected_window)

    def test_layout_adapts_to_different_size(self):
        timeline = _make_timeline(tempo=120)
        screen = PlayingScreen(timeline)
        layout_small = screen._layout(self._MockSurface(800, 600))
        layout_large = screen._layout(self._MockSurface(1920, 1080))

        assert layout_large.usable_width > layout_small.usable_width
        assert layout_large.lane_height > layout_small.lane_height
        assert layout_large.pixels_per_ms > layout_small.pixels_per_ms


class TestPlaybackClock:
    """Test play/pause/seek logic."""

    def test_starts_paused(self):
        screen = PlayingScreen(_make_timeline())
        assert not screen.is_playing()

    def test_toggle_play(self):
        screen = PlayingScreen(_make_timeline())
        screen.toggle_play()
        assert screen.is_playing()
        screen.toggle_play()
        assert not screen.is_playing()

    def test_seek_clamps_to_zero(self):
        screen = PlayingScreen(_make_timeline())
        screen.seek(-100)
        assert screen._playback_ms == 0.0

    def test_seek_clamps_to_duration(self):
        notes = [NoteEvent(1000, 500, 64, 1, 5)]
        timeline = _make_timeline(notes=notes)
        screen = PlayingScreen(timeline)
        screen.seek(999999)
        assert screen._playback_ms == timeline.duration_ms


class TestTempoFactor:
    """Test tempo factor (speed adjustment) logic."""

    def test_tempo_factor_default(self):
        screen = PlayingScreen(_make_timeline())
        assert screen._tempo_factor == 1.0

    def test_tempo_factor_clamps_low(self):
        screen = PlayingScreen(_make_timeline())
        screen.set_tempo_factor(0.3)
        assert screen._tempo_factor == 0.5

    def test_tempo_factor_clamps_high(self):
        screen = PlayingScreen(_make_timeline())
        screen.set_tempo_factor(1.5)
        assert screen._tempo_factor == 1.0

    def test_tempo_factor_rounds_to_nearest_005(self):
        screen = PlayingScreen(_make_timeline())
        screen.set_tempo_factor(0.73)
        assert screen._tempo_factor == pytest.approx(0.75)
        screen.set_tempo_factor(0.82)
        assert screen._tempo_factor == pytest.approx(0.80)
        screen.set_tempo_factor(0.68)
        assert screen._tempo_factor == pytest.approx(0.70)

    def test_tempo_factor_from_config(self):
        from pickhero.config import Config
        config = Config(tempo_factor=0.75)
        screen = PlayingScreen(_make_timeline(), config=config)
        assert screen._tempo_factor == pytest.approx(0.75)

    def test_tempo_factor_config_clamped_on_init(self):
        from pickhero.config import Config
        config = Config(tempo_factor=0.2)
        screen = PlayingScreen(_make_timeline(), config=config)
        assert screen._tempo_factor == 0.5


class TestLoopState:
    """Test section looping state management."""

    def test_default_state(self):
        screen = PlayingScreen(_make_timeline())
        assert screen._loop_start_ms is None
        assert screen._loop_end_ms is None
        assert screen._loop_enabled is False

    def test_setting_one_marker_does_not_enable(self):
        screen = PlayingScreen(_make_timeline())
        screen._set_loop_start(1000.0)
        assert screen._loop_start_ms == 1000.0
        assert screen._loop_end_ms is None
        assert screen._loop_enabled is False

    def test_setting_both_markers_auto_enables(self):
        screen = PlayingScreen(_make_timeline())
        screen._set_loop_start(1000.0)
        screen._set_loop_end(5000.0)
        assert screen._loop_start_ms == 1000.0
        assert screen._loop_end_ms == 5000.0
        assert screen._loop_enabled is True

    def test_auto_swap_when_start_after_end(self):
        screen = PlayingScreen(_make_timeline())
        screen._set_loop_end(2000.0)
        screen._set_loop_start(5000.0)
        assert screen._loop_start_ms == 2000.0
        assert screen._loop_end_ms == 5000.0
        assert screen._loop_enabled is True

    def test_auto_swap_when_end_before_start(self):
        screen = PlayingScreen(_make_timeline())
        screen._set_loop_start(5000.0)
        screen._set_loop_end(2000.0)
        assert screen._loop_start_ms == 2000.0
        assert screen._loop_end_ms == 5000.0
        assert screen._loop_enabled is True

    def test_toggle_off_keeps_markers(self):
        screen = PlayingScreen(_make_timeline())
        screen._set_loop_start(1000.0)
        screen._set_loop_end(5000.0)
        assert screen._loop_enabled is True
        screen._toggle_loop()
        assert screen._loop_enabled is False
        assert screen._loop_start_ms == 1000.0
        assert screen._loop_end_ms == 5000.0

    def test_toggle_off_again_clears_markers(self):
        screen = PlayingScreen(_make_timeline())
        screen._set_loop_start(1000.0)
        screen._set_loop_end(5000.0)
        screen._toggle_loop()   # disable
        screen._toggle_loop()   # clear
        assert screen._loop_start_ms is None
        assert screen._loop_end_ms is None
        assert screen._loop_enabled is False

    def test_zero_length_guard_snaps_end(self):
        """Loop shorter than one beat gets snapped to one beat."""
        screen = PlayingScreen(_make_timeline(tempo=120))
        # 120 BPM → 500ms per beat
        screen._set_loop_start(1000.0)
        screen._set_loop_end(1100.0)  # only 100ms apart
        assert screen._loop_end_ms == pytest.approx(1000.0 + 500.0)
        assert screen._loop_enabled is True

    def test_zero_length_guard_on_same_position(self):
        screen = PlayingScreen(_make_timeline(tempo=120))
        screen._set_loop_start(3000.0)
        screen._set_loop_end(3000.0)
        assert screen._loop_end_ms == pytest.approx(3000.0 + 500.0)

    def test_loop_hud_text_both_markers_enabled(self):
        screen = PlayingScreen(_make_timeline())
        screen._set_loop_start(15000.0)
        screen._set_loop_end(32000.0)
        text = screen._loop_hud_text()
        assert text == "LOOP 0:15 - 0:32"

    def test_loop_hud_text_both_markers_disabled(self):
        screen = PlayingScreen(_make_timeline())
        screen._set_loop_start(15000.0)
        screen._set_loop_end(32000.0)
        screen._toggle_loop()
        text = screen._loop_hud_text()
        assert text == "loop 0:15 - 0:32 (off)"

    def test_loop_hud_text_one_marker(self):
        screen = PlayingScreen(_make_timeline())
        screen._set_loop_start(15000.0)
        text = screen._loop_hud_text()
        assert text == "loop start: 0:15"

    def test_loop_hud_text_no_markers(self):
        screen = PlayingScreen(_make_timeline())
        assert screen._loop_hud_text() is None


class TestFretboardLayout:
    """The display is a compact fretboard band, not six full-height rows."""

    class _MockSurface:
        def __init__(self, w, h):
            self._size = (w, h)

        def get_size(self):
            return self._size

    def test_band_is_compact_not_full_height(self):
        from pickhero.ui.scrolling import LANE_BOTTOM_MARGIN, LANE_TOP_MARGIN
        screen = PlayingScreen(_make_timeline(tempo=120))
        layout = screen._layout(self._MockSurface(1280, 720))
        available = 720 - LANE_TOP_MARGIN - LANE_BOTTOM_MARGIN
        assert 6 * layout.lane_height < available * 0.75

    def test_band_is_centred_between_the_margins(self):
        from pickhero.ui.scrolling import LANE_BOTTOM_MARGIN, LANE_TOP_MARGIN
        screen = PlayingScreen(_make_timeline(tempo=120))
        layout = screen._layout(self._MockSurface(1280, 720))
        gap_above = layout.lane_top - LANE_TOP_MARGIN
        gap_below = (720 - LANE_BOTTOM_MARGIN) - (layout.lane_top + 6 * layout.lane_height)
        assert gap_above == pytest.approx(gap_below, abs=1.0)

    def test_band_still_scales_with_window_size(self):
        screen = PlayingScreen(_make_timeline(tempo=120))
        small = screen._layout(self._MockSurface(800, 600))
        large = screen._layout(self._MockSurface(1920, 1080))
        assert large.lane_height > small.lane_height

    def test_lane_top_never_above_the_margin(self):
        from pickhero.ui.scrolling import LANE_TOP_MARGIN
        screen = PlayingScreen(_make_timeline(tempo=120))
        # a window too short for six lanes must not push the band off-screen
        layout = screen._layout(self._MockSurface(1280, 200))
        assert layout.lane_top >= LANE_TOP_MARGIN


class TestSustainWidth:
    """Short notes draw as circles, sustained ones as capsules."""

    def test_no_minimum_so_short_notes_stay_circles(self):
        assert PlayingScreen.sustain_width(1.0, 0.5) == pytest.approx(0.5)

    def test_zero_duration_has_no_body(self):
        assert PlayingScreen.sustain_width(0.0, 1.0) == 0.0

    def test_negative_duration_clamps_to_zero(self):
        assert PlayingScreen.sustain_width(-5.0, 1.0) == 0.0

    def test_proportional_to_duration(self):
        assert PlayingScreen.sustain_width(500.0, 0.5) == pytest.approx(250.0)

    def test_differs_from_note_width_which_pads_to_a_minimum(self):
        short = 1.0
        assert PlayingScreen.sustain_width(short, 0.5) < PlayingScreen.note_width(short, 0.5)


class TestNeighbourGaps:
    """Notes may not take more room than they have before their neighbour."""

    def _n(self, ts, string, dur=500.0):
        return NoteEvent(timestamp_ms=ts, duration_ms=dur, midi_note=40,
                         string=string, fret=0)

    def test_lone_note_has_no_neighbour(self):
        gaps = PlayingScreen._neighbour_gaps([self._n(0.0, 6)])
        assert gaps.get((0.0, 6)) is None

    def test_gap_is_to_the_nearest_neighbour_on_the_same_string(self):
        notes = [self._n(0.0, 6), self._n(200.0, 6), self._n(1000.0, 6)]
        gaps = PlayingScreen._neighbour_gaps(notes)
        assert gaps[(200.0, 6)] == pytest.approx(200.0)   # backwards, not 800
        assert gaps[(0.0, 6)] == pytest.approx(200.0)
        assert gaps[(1000.0, 6)] == pytest.approx(800.0)

    def test_other_strings_do_not_constrain(self):
        """Different lanes never collide, so they must not shrink each other."""
        notes = [self._n(0.0, 6), self._n(10.0, 5), self._n(900.0, 6)]
        gaps = PlayingScreen._neighbour_gaps(notes)
        assert gaps[(0.0, 6)] == pytest.approx(900.0)
        assert gaps.get((10.0, 5)) is None

    def test_chord_notes_at_one_instant_are_not_neighbours(self):
        """A chord strikes several strings at once; that is not congestion."""
        notes = [self._n(0.0, 6), self._n(0.0, 5), self._n(0.0, 4)]
        assert PlayingScreen._neighbour_gaps(notes) == {}

    def test_duplicate_timestamps_on_one_string_collapse(self):
        notes = [self._n(0.0, 6), self._n(0.0, 6), self._n(300.0, 6)]
        gaps = PlayingScreen._neighbour_gaps(notes)
        assert gaps[(0.0, 6)] == pytest.approx(300.0)


class TestScrollSpeed:
    """One speed per song, chosen so notes never have to shrink or resize."""

    class _MockSurface:
        def __init__(self, w, h):
            self._size = (w, h)

        def get_size(self):
            return self._size

    def _screen(self, spacing_ms, count=20, tempo=120):
        notes = [
            NoteEvent(timestamp_ms=i * spacing_ms, duration_ms=spacing_ms * 0.8,
                      midi_note=40, string=6, fret=0)
            for i in range(count)
        ]
        screen = PlayingScreen(Timeline(notes, SongMetadata(tempo=tempo)))
        layout = screen._layout(self._MockSurface(1280, 720))
        screen._last_layout = layout
        screen._recompute_scroll_speed(layout)
        return screen

    def test_sparse_song_keeps_the_base_window(self):
        from pickhero.ui.scrolling import BASE_VISIBLE_WINDOW_MS
        screen = self._screen(spacing_ms=1000.0)
        assert screen._visible_window_ms == pytest.approx(BASE_VISIBLE_WINDOW_MS)

    def test_notes_are_the_same_size_whatever_the_song(self):
        """The window gives way first, so notes keep their size across songs
        of very different density."""
        sizes = {round(self._screen(spacing_ms=sp)._head_px, 3)
                 for sp in (1000.0, 280.0, 125.0)}
        assert len(sizes) == 1

    def test_the_trim_never_changes_note_size(self):
        """Notes changing size is the one thing this display must not do, so
        the trim moves the speed and nothing else."""
        screen = self._screen(spacing_ms=600.0)
        sizes = set()
        for factor in (0.4, 0.7, 1.0, 1.5, 2.5):
            screen._config.scroll_speed_factor = factor
            screen._recompute_scroll_speed()
            sizes.add(round(screen._head_px, 3))
        assert len(sizes) == 1

    def test_speeding_up_always_gives_notes_more_room(self):
        screen = self._screen(spacing_ms=600.0)
        before = screen._visible_window_ms
        screen._adjust_scroll_factor(1.0)
        assert screen._visible_window_ms < before

    def test_slowing_down_stops_where_notes_would_have_to_shrink(self):
        """Rather than overlapping or shrinking, the trim simply refuses."""
        screen = self._screen(spacing_ms=600.0)
        screen._adjust_scroll_factor(-0.6)
        layout = screen._layout(self._MockSurface(1280, 720))
        assert 600.0 * layout.pixels_per_ms >= screen._head_px

    def test_very_dense_song_finally_scrolls_faster(self):
        from pickhero.ui.scrolling import BASE_VISIBLE_WINDOW_MS
        screen = self._screen(spacing_ms=100.0)
        assert screen._visible_window_ms < BASE_VISIBLE_WINDOW_MS

    def test_notes_never_overlap_at_the_chosen_size(self):
        screen = self._screen(spacing_ms=125.0)
        layout = screen._layout(self._MockSurface(1280, 720))
        assert 125.0 * layout.pixels_per_ms >= screen._head_px

    def test_head_fills_most_of_its_lane(self):
        """Notes are meant to be seen at a glance, not squinted at."""
        screen = self._screen(spacing_ms=500.0)
        layout = screen._layout(self._MockSurface(1280, 720))
        assert screen._head_px > layout.lane_height * 0.7

    def test_denser_song_shows_less_time_at_once(self):
        sparse = self._screen(spacing_ms=1000.0)
        dense = self._screen(spacing_ms=125.0)
        assert dense._visible_window_ms < sparse._visible_window_ms

    def test_a_few_freak_close_notes_do_not_set_the_pace(self):
        """One grace-note pair must not shrink the window for a whole song."""
        notes = [NoteEvent(timestamp_ms=i * 500.0, duration_ms=400.0,
                           midi_note=40, string=6, fret=0) for i in range(40)]
        notes.append(NoteEvent(timestamp_ms=10_020.0, duration_ms=50.0,
                               midi_note=40, string=6, fret=0))
        screen = PlayingScreen(Timeline(notes, SongMetadata(tempo=120)))
        screen._recompute_scroll_speed(screen._layout(self._MockSurface(1280, 720)))
        clean = self._screen(spacing_ms=500.0)
        assert screen._visible_window_ms == pytest.approx(clean._visible_window_ms)

    def test_never_scrolls_faster_than_the_floor(self):
        from pickhero.ui.scrolling import MIN_VISIBLE_WINDOW_MS
        screen = self._screen(spacing_ms=5.0)
        assert screen._visible_window_ms >= MIN_VISIBLE_WINDOW_MS - 0.01

    def test_speed_is_constant_while_the_song_plays(self):
        """Notes must never visibly stretch or squeeze mid-song."""
        screen = self._screen(spacing_ms=125.0, count=200)
        before = screen._visible_window_ms
        for pos in (0.0, 4000.0, 12000.0, 24000.0):
            screen._playback_ms = pos
            screen.update()
        assert screen._visible_window_ms == pytest.approx(before)

    def test_one_fast_passage_sets_the_pace_for_the_whole_song(self):
        """A song is paced by its tightest bar, so nothing resizes later on."""
        notes = [NoteEvent(timestamp_ms=i * 1000.0, duration_ms=500.0,
                           midi_note=40, string=6, fret=0) for i in range(8)]
        notes += [NoteEvent(timestamp_ms=20000.0 + i * 100.0, duration_ms=90.0,
                            midi_note=40, string=6, fret=0) for i in range(8)]
        screen = PlayingScreen(Timeline(notes, SongMetadata(tempo=120)))
        layout = screen._layout(self._MockSurface(1280, 720))
        screen._recompute_scroll_speed(layout)
        ppm = screen._layout(self._MockSurface(1280, 720)).pixels_per_ms
        assert 100.0 * ppm >= screen._head_px

    def test_manual_trim_changes_the_speed(self):
        screen = self._screen(spacing_ms=1000.0)
        before = screen._visible_window_ms
        screen._adjust_scroll_factor(-0.4)     # slower
        assert screen._visible_window_ms > before
        screen._adjust_scroll_factor(0.8)      # faster than the default
        assert screen._visible_window_ms < before

    def test_manual_trim_is_bounded(self):
        from pickhero.ui.scrolling import SCROLL_FACTOR_RANGE
        screen = self._screen(spacing_ms=1000.0)
        for _ in range(50):
            screen._adjust_scroll_factor(1.0)
        assert screen._scroll_factor() == pytest.approx(SCROLL_FACTOR_RANGE[1])
        for _ in range(100):
            screen._adjust_scroll_factor(-1.0)
        assert screen._scroll_factor() == pytest.approx(SCROLL_FACTOR_RANGE[0])

    def test_no_layout_yet_is_harmless(self):
        screen = PlayingScreen(_make_timeline(tempo=120))
        screen._last_layout = None
        screen._recompute_scroll_speed()   # must not raise

    def test_song_without_notes_keeps_the_base_window(self):
        from pickhero.ui.scrolling import BASE_VISIBLE_WINDOW_MS
        screen = PlayingScreen(Timeline([], SongMetadata(tempo=120)))
        screen._recompute_scroll_speed(screen._layout(self._MockSurface(1280, 720)))
        assert screen._visible_window_ms == pytest.approx(BASE_VISIBLE_WINDOW_MS)


class TestBackingOffset:
    """What you hear and what you see are generated from one timeline but do
    not necessarily arrive together."""

    def _screen(self):
        return PlayingScreen(_make_timeline(tempo=120), config=Config())

    def test_no_offset_leaves_the_position_alone(self):
        screen = self._screen()
        assert screen._backing_ms(5000.0) == pytest.approx(5000.0)

    def test_positive_offset_delays_the_backing(self):
        screen = self._screen()
        screen._config.backing_offset_ms = 120.0
        assert screen._backing_ms(5000.0) == pytest.approx(4880.0)

    def test_negative_offset_pulls_the_backing_forward(self):
        screen = self._screen()
        screen._config.backing_offset_ms = -80.0
        assert screen._backing_ms(5000.0) == pytest.approx(5080.0)

    def test_adjustment_accumulates(self):
        screen = self._screen()
        screen._adjust_backing_offset(30.0)
        screen._adjust_backing_offset(20.0)
        assert screen._config.backing_offset_ms == pytest.approx(50.0)

    def test_adjustment_is_bounded_both_ways(self):
        from pickhero.ui.scrolling import MAX_BACKING_OFFSET_MS
        screen = self._screen()
        for _ in range(200):
            screen._adjust_backing_offset(50.0)
        assert screen._config.backing_offset_ms == pytest.approx(MAX_BACKING_OFFSET_MS)
        for _ in range(400):
            screen._adjust_backing_offset(-50.0)
        assert screen._config.backing_offset_ms == pytest.approx(-MAX_BACKING_OFFSET_MS)


class TestTimingWindowCycle:
    def test_cycles_through_the_presets_and_wraps(self):
        from pickhero.ui.scrolling import TIMING_WINDOW_PRESETS
        screen = PlayingScreen(_make_timeline(tempo=120), config=Config())
        seen = []
        for _ in range(len(TIMING_WINDOW_PRESETS) + 1):
            screen._cycle_timing_window()
            seen.append(screen._config.timing_window_ms)
        assert set(seen) == set(TIMING_WINDOW_PRESETS)
        assert seen[-1] == seen[len(TIMING_WINDOW_PRESETS) - 1 + 1 - len(TIMING_WINDOW_PRESETS)]


class TestTrackPicker:
    """Multi-track tabs used to give you whichever part they picked."""

    def _screen(self):
        screen = PlayingScreen(_make_timeline(tempo=120), config=Config())
        screen.set_track_options([(0, "1. Guitar"), (2, "3. Lead"), (4, "5. Bass")], 2)
        return screen

    def _key(self, key):
        import pygame
        return pygame.event.Event(pygame.KEYDOWN, key=key, mod=0)

    def test_starts_closed(self):
        assert not self._screen()._track_menu_open

    def test_cursor_starts_on_the_current_track(self):
        assert self._screen()._track_menu_cursor == 1

    def test_tab_opens_and_closes(self):
        import pygame
        screen = self._screen()
        screen.handle_event(self._key(pygame.K_TAB))
        assert screen._track_menu_open
        screen.handle_event(self._key(pygame.K_TAB))
        assert not screen._track_menu_open

    def test_a_single_track_has_nothing_to_pick(self):
        import pygame
        screen = PlayingScreen(_make_timeline(tempo=120), config=Config())
        screen.set_track_options([(0, "1. Guitar")], 0)
        screen.handle_event(self._key(pygame.K_TAB))
        assert not screen._track_menu_open

    def test_choosing_another_track_reports_it(self):
        import pygame
        screen = self._screen()
        screen.handle_event(self._key(pygame.K_TAB))
        screen.handle_event(self._key(pygame.K_DOWN))
        result = screen.handle_event(self._key(pygame.K_RETURN))
        assert result == ("select_track", 4)
        assert not screen._track_menu_open

    def test_choosing_the_current_track_reports_nothing(self):
        import pygame
        screen = self._screen()
        screen.handle_event(self._key(pygame.K_TAB))
        assert screen.handle_event(self._key(pygame.K_RETURN)) is None

    def test_selection_wraps(self):
        import pygame
        screen = self._screen()
        screen.handle_event(self._key(pygame.K_TAB))
        screen.handle_event(self._key(pygame.K_UP))
        screen.handle_event(self._key(pygame.K_UP))
        assert screen._track_menu_cursor == 2

    def test_the_picker_swallows_keys_meant_for_the_song(self):
        """Arrow keys must move the selection, not seek through the song."""
        import pygame
        screen = self._screen()
        before = screen._playback_ms
        screen.handle_event(self._key(pygame.K_TAB))
        screen.handle_event(self._key(pygame.K_RIGHT))
        assert screen._playback_ms == before

    def test_escape_closes_without_choosing(self):
        import pygame
        screen = self._screen()
        screen.handle_event(self._key(pygame.K_TAB))
        screen.handle_event(self._key(pygame.K_DOWN))
        assert screen.handle_event(self._key(pygame.K_ESCAPE)) is None
        assert not screen._track_menu_open


class TestBendDrawing:
    def test_label_counts_steps_not_semitones(self):
        # Guitar notation: one semitone is a half bend, two is a whole one.
        # Written as a number rather than 'full' so it fits the badge.
        assert PlayingScreen.bend_label(1) == "½"
        assert PlayingScreen.bend_label(2) == "1"
        assert PlayingScreen.bend_label(3) == "1½"
        assert PlayingScreen.bend_label(4) == "2"

    def test_no_label_without_a_bend(self):
        assert PlayingScreen.bend_label(0) == ""

    def _curve(self, bend, height=30.0):
        note = NoteEvent(timestamp_ms=0.0, duration_ms=500.0, midi_note=64,
                         string=1, fret=0, bend=bend)
        return PlayingScreen._bend_points(note, 100.0, 200.0, 80.0, height)

    def test_curve_starts_on_the_string_and_rises(self):
        points = self._curve(((0.0, 0.0), (0.5, 2.0), (1.0, 2.0)))
        assert points[0] == pytest.approx((100.0, 200.0))
        assert min(p[1] for p in points) < 200.0     # screen y grows downward

    def test_curve_spans_the_width_it_is_given(self):
        points = self._curve(((0.0, 0.0), (1.0, 2.0)))
        assert points[-1][0] == pytest.approx(180.0)

    def test_a_missing_start_point_is_supplied(self):
        """GP files routinely omit (0, 0); without it the arc floats."""
        points = self._curve(((0.5, 2.0), (1.0, 2.0)))
        assert points[0][1] == pytest.approx(200.0)

    def test_a_deeper_bend_rises_further(self):
        half = min(p[1] for p in self._curve(((0.0, 0.0), (1.0, 1.0))))
        full = min(p[1] for p in self._curve(((0.0, 0.0), (1.0, 2.0))))
        assert full < half

    def test_a_full_bend_reaches_the_depth_it_is_given(self):
        top = min(p[1] for p in self._curve(((0.0, 0.0), (1.0, 2.0)), 30.0))
        assert top == pytest.approx(170.0)

    def test_a_half_bend_is_drawn_half_as_deep(self):
        top = min(p[1] for p in self._curve(((0.0, 0.0), (1.0, 1.0)), 30.0))
        assert top == pytest.approx(185.0)

    def test_a_deeper_bend_is_squeezed_in_rather_than_drawn_outside(self):
        """The curve lives inside the note, so it cannot overflow it."""
        top = min(p[1] for p in self._curve(((0.0, 0.0), (1.0, 8.0)), 30.0))
        assert top == pytest.approx(170.0)


class TestSlideTargets:
    def test_finds_the_next_note_on_the_same_string(self):
        first = NoteEvent(timestamp_ms=0.0, duration_ms=100.0, midi_note=64,
                          string=1, fret=0, slide_to_next=True)
        second = NoteEvent(timestamp_ms=500.0, duration_ms=100.0, midi_note=69,
                           string=1, fret=5)
        other = NoteEvent(timestamp_ms=200.0, duration_ms=100.0, midi_note=59,
                          string=2, fret=0)
        found = PlayingScreen._next_on_string([first, other, second])
        assert found[(0.0, 1)] is second

    def test_a_chord_partner_is_not_a_slide_target(self):
        """Same string, same instant cannot be where a slide is going."""
        a = NoteEvent(timestamp_ms=0.0, duration_ms=100.0, midi_note=64,
                      string=1, fret=0)
        b = NoteEvent(timestamp_ms=0.0, duration_ms=100.0, midi_note=64,
                      string=1, fret=0)
        assert PlayingScreen._next_on_string([a, b]) == {}

    def test_the_last_note_has_nowhere_to_slide(self):
        only = NoteEvent(timestamp_ms=0.0, duration_ms=100.0, midi_note=64,
                         string=1, fret=0, slide_to_next=True)
        assert PlayingScreen._next_on_string([only]) == {}


class TestFooterCompleteness:
    """Every key the screen answers has to be written somewhere on it.

    This reads handle_event's own source rather than a hand-kept list, so
    adding a shortcut and forgetting to document it fails here instead of
    quietly shipping a key nobody can find.
    """

    # pygame constant suffix -> the text the footer must contain for it
    LABELS = {
        "SPACE": "SPACE", "ESCAPE": "ESC", "LEFT": "LEFT", "RIGHT": "RIGHT",
        "HOME": "HOME", "PAGEDOWN": "PgDn", "PAGEUP": "PgUp", "TAB": "TAB",
        "a": "A: audio", "b": "B: backing", "c": "X/C", "f": "F: frets",
        "g": "G: hit window", "h": "H: help", "i": "I/O", "j": "J: strings",
        "k": "K: sync", "l": "L: weakest", "m": "N/M", "n": "N/M",
        "o": "I/O", "p": "P: toggle", "t": "T: theme", "v": "V: chords",
        "w": "W: wait", "x": "X/C", "y": "Y: timing",
        "COMMA": ",/.", "PERIOD": ",/.",
        "PLUS": "+/-", "EQUALS": "+/-", "MINUS": "+/-",
        "KP_PLUS": "+/-", "KP_MINUS": "+/-",
        "F1": "F1-F6", "F2": "F1-F6", "F3": "F1-F6",
        "F4": "F1-F6", "F5": "F1-F6", "F6": "F1-F6",
    }

    def _keys_handled(self) -> set[str]:
        import inspect
        import re
        source = inspect.getsource(PlayingScreen.handle_event)
        return set(re.findall(r"pygame\.K_(\w+)", source))

    def test_every_handled_key_is_in_the_footer(self):
        screen = PlayingScreen(_make_timeline())
        footer = "  ".join(screen._footer_lines())
        missing = []
        for key in sorted(self._keys_handled()):
            assert key in self.LABELS, f"new key K_{key} has no footer label"
            if self.LABELS[key] not in footer:
                missing.append(key)
        assert missing == [], f"undocumented keys: {missing}"

    def test_footer_keeps_the_keys_when_nothing_is_loaded(self):
        """No backing track still means B is bound, so B stays listed."""
        screen = PlayingScreen(_make_timeline())
        assert screen._midi_player is None
        footer = "  ".join(screen._footer_lines())
        assert "B: backing" in footer and "W: wait" in footer

    def test_footer_reports_the_state_it_shows(self):
        screen = PlayingScreen(_make_timeline())
        assert "Paused" in screen._footer_lines()[0]
        screen.toggle_play()
        assert "Paused" not in screen._footer_lines()[0]


class TestTimingOverlay:
    def _screen_with_samples(self, deltas, tmp_path, monkeypatch):
        import pickhero.config as config_module
        from pickhero.matcher import NoteMatcher, TimingSample
        monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / ".pickhero")
        notes = [NoteEvent(timestamp_ms=1000.0, duration_ms=200.0,
                           midi_note=64, string=1, fret=0)]
        screen = PlayingScreen(_make_timeline(notes=notes))
        screen._matcher = NoteMatcher(_make_timeline(notes=notes))
        for d in deltas:
            screen._matcher.timing_errors_ms.append(d)
            screen._matcher.timing_samples.append(
                TimingSample(delta_ms=d, string=1, midi_note=64, note_ms=1000.0))
        return screen

    def test_y_opens_and_closes_the_report(self):
        screen = PlayingScreen(_make_timeline())
        event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_y, mod=0)
        screen.handle_event(event)
        assert screen._show_timing
        screen.handle_event(event)
        assert not screen._show_timing

    def test_shift_y_exports_instead_of_toggling(self, tmp_path, monkeypatch):
        screen = self._screen_with_samples([40.0, 45.0], tmp_path, monkeypatch)
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_y, mod=pygame.KMOD_SHIFT))
        assert not screen._show_timing
        written = list((tmp_path / ".pickhero").glob("timing_*.csv"))
        assert len(written) == 1
        lines = written[0].read_text().strip().split("\n")
        assert lines[0] == "delta_ms,string,midi_note,note_ms"
        assert len(lines) == 3

    def test_export_writes_where_the_config_lives_not_the_real_home(
        self, tmp_path, monkeypatch
    ):
        """A name bound at import time would sail past the test redirect."""
        screen = self._screen_with_samples([10.0], tmp_path, monkeypatch)
        screen._export_timing_samples()
        assert (tmp_path / ".pickhero").exists()
        assert "Saved" in screen._timing_export_note

    def test_export_says_so_when_there_is_nothing_to_export(self, tmp_path, monkeypatch):
        screen = self._screen_with_samples([], tmp_path, monkeypatch)
        screen._export_timing_samples()
        assert "Nothing measured" in screen._timing_export_note
        assert not (tmp_path / ".pickhero").exists()

    def test_every_verdict_has_something_to_say(self):
        """The overlay indexes this by verdict, so a new one must not KeyError."""
        from pickhero.matcher import NoteMatcher
        verdicts = {NoteMatcher._timing_verdict(m, s, g)
                    for m in (0.0, 5.0, 90.0)
                    for s in (5.0, 60.0)
                    for g in (False, True)}
        assert verdicts <= set(PlayingScreen.TIMING_VERDICTS)

    @pytest.fixture
    def fonts(self):
        """Rendering needs the font module; the maths tests do not."""
        pygame.init()
        yield
        pygame.quit()

    def test_report_renders_without_measurements(self, fonts):
        surface = pygame.Surface((1280, 720))
        screen = PlayingScreen(_make_timeline())
        screen._show_timing = True
        screen.render(surface)      # must not raise: the empty state is normal

    def test_report_renders_with_measurements(self, fonts, tmp_path, monkeypatch):
        surface = pygame.Surface((1280, 720))
        screen = self._screen_with_samples(
            [80.0 + i for i in range(20)], tmp_path, monkeypatch)
        screen._show_timing = True
        screen.render(surface)
