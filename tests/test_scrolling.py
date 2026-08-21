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

    def test_notes_keep_full_size_while_the_window_can_afford_it(self):
        """The window gives way first. Only once shrinking it further would
        leave too little warning to read a fret number does the note size
        move at all."""
        sizes = {round(self._screen(spacing_ms=sp)._head_px, 3)
                 for sp in (1000.0, 600.0, 280.0)}
        assert len(sizes) == 1

    def test_a_song_too_dense_to_read_buys_time_with_note_size(self):
        """At full size a dense tab gave 1.5 s of warning at 683 px/s -- a
        note crossing the screen faster than it can be read, never mind
        fingered. Head size is the only currency available for that."""
        from pickhero.ui.scrolling import READABLE_WINDOW_MS
        roomy = self._screen(spacing_ms=1000.0)
        dense = self._screen(spacing_ms=90.0)
        assert dense._head_px < roomy._head_px
        assert dense._visible_window_ms > 1500.0

    def test_the_notes_never_shrink_past_a_readable_fret_number(self):
        """Two digits still have to fit, or the trade buys nothing."""
        from pickhero.ui.scrolling import MIN_HEAD_PX
        for spacing in (90.0, 40.0, 10.0):
            assert self._screen(spacing_ms=spacing)._head_px >= MIN_HEAD_PX

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


class TestPalmMuteMarking:
    """"PM" goes on the note that OPENS a run, the way paper tab writes it.

    A muted metal riff flags every note it contains. A badge over each of them
    is a row of discs covering the music it is supposed to describe, so the
    label goes on the first note and the choked note bodies carry it onward.
    """

    def _n(self, ts, string=6, palm_mute=False):
        return NoteEvent(timestamp_ms=ts, duration_ms=200.0, midi_note=40,
                         string=string, fret=0, palm_mute=palm_mute)

    def test_only_the_first_note_of_a_run_is_marked(self):
        notes = [self._n(ts, palm_mute=True) for ts in (0.0, 300.0, 600.0)]
        starts = PlayingScreen._palm_mute_run_starts(notes)
        assert starts == {(0.0, 6)}

    def test_an_unmuted_note_ends_the_run(self):
        notes = [self._n(0.0, palm_mute=True), self._n(300.0),
                 self._n(600.0, palm_mute=True)]
        starts = PlayingScreen._palm_mute_run_starts(notes)
        assert starts == {(0.0, 6), (600.0, 6)}

    def test_a_long_silence_starts_a_new_run(self):
        """The badge has to come back when the riff does, a chorus later."""
        notes = [self._n(0.0, palm_mute=True), self._n(60_000.0, palm_mute=True)]
        starts = PlayingScreen._palm_mute_run_starts(notes)
        assert starts == {(0.0, 6), (60_000.0, 6)}

    def test_a_muted_chord_is_marked_once_on_its_lowest_string(self):
        """Palm muting is the picking hand resting on the strings: it applies
        to the whole stroke, so three stacked badges only crowd the lanes."""
        notes = [self._n(0.0, string=s, palm_mute=True) for s in (6, 5, 4)]
        starts = PlayingScreen._palm_mute_run_starts(notes)
        assert starts == {(0.0, 6)}

    def test_nothing_is_marked_without_a_palm_mute(self):
        assert PlayingScreen._palm_mute_run_starts(
            [self._n(0.0), self._n(300.0)]
        ) == set()

    def test_a_dead_stroke_does_not_break_the_run(self):
        """Chug, chug, muted stroke, chug is the commonest metal rhythm there
        is. The picking hand never leaves the strings, so the run continues --
        counted as a break, it re-badges every second note of the riff."""
        notes = [
            self._n(0.0, palm_mute=True),
            NoteEvent(timestamp_ms=300.0, duration_ms=200.0, midi_note=40,
                      string=6, fret=0, dead=True),
            self._n(600.0, palm_mute=True),
        ]
        starts = PlayingScreen._palm_mute_run_starts(notes)
        assert starts == {(0.0, 6)}


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
        "a": "A: audio", "b": "B: backing", "c": "X/C", "d": "D: run log",
        "f": "F: frets",
        "g": "G: hit window", "h": "H: help", "i": "I/O", "j": "J: strings",
        "k": "K: sync", "l": "L: weakest", "m": "N/M", "n": "N/M",
        "o": "I/O", "p": "P: toggle", "t": "T: theme", "u": "U: audio track",
        "v": "V: chords",
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


class TestAutoSync:
    """K applies exactly what the timing report calls latency, and no more.

    Deciding it a second time here by a looser rule only produces the two
    answers disagreeing. That really happened: a measurement taken over notes
    carrying bends and slides scattered by +-75 ms, passed the old check
    because it had enough samples, and left an offset built out of noise
    sitting in the config for days.
    """

    def _screen_with(self, deltas):
        from pickhero.matcher import NoteMatcher, TimingSample
        from pickhero.tabs.timeline import SongMetadata, Timeline
        screen = PlayingScreen(_make_timeline(), config=Config())
        matcher = NoteMatcher(Timeline([], SongMetadata(title="x", tempo=100)))
        for i, d in enumerate(deltas):
            matcher.timing_errors_ms.append(d)
            matcher.timing_samples.append(
                TimingSample(delta_ms=d, string=6, midi_note=40,
                             note_ms=1000.0 * i)
            )
        screen._matcher = matcher
        return screen

    def _offset(self, screen):
        return screen._config.audio_latency_offset_ms

    def test_plain_latency_is_removed(self):
        screen = self._screen_with([118, 122, 120, 125, 119, 121, 123, 120,
                                    124, 119])
        screen._auto_sync_timing()
        assert self._offset(screen) == pytest.approx(-120, abs=4)

    def test_scattered_timing_is_refused(self):
        """No single offset fixes strikes that disagree with each other, and
        applying one anyway is a guess dressed up as a measurement."""
        screen = self._screen_with([-90, 80, -70, 95, -85, 75, -60, 88,
                                    -95, 70, 82, -78])
        screen._auto_sync_timing()
        assert self._offset(screen) == 0.0

    def test_timing_already_fine_is_left_alone(self):
        screen = self._screen_with([2, -3, 1, 4, -2, 0, 3, -1, 2, -4])
        screen._auto_sync_timing()
        assert self._offset(screen) == 0.0

    def test_too_few_samples_does_nothing(self):
        screen = self._screen_with([120, 118, 122])
        screen._auto_sync_timing()
        assert self._offset(screen) == 0.0

    def test_applying_clears_the_measurements_it_used(self):
        """Otherwise a second press would count the same error twice instead
        of measuring what is left."""
        screen = self._screen_with([120] * 10)
        screen._auto_sync_timing()
        assert screen._matcher.timing_samples == []

    def test_a_press_is_remembered_so_a_residual_can_be_named(self):
        screen = self._screen_with([120] * 10)
        assert not screen._sync_applied
        screen._auto_sync_timing()
        assert screen._sync_applied

    def test_resetting_the_sync_forgets_that_too(self):
        screen = self._screen_with([120] * 10)
        screen._auto_sync_timing()
        screen._reset_latency_offset()
        assert not screen._sync_applied
        assert self._offset(screen) == 0.0


class TestSyncAdviceMatchesWhatKDoes:
    """The HUD line and the key must never disagree.

    A line that offers K while K refuses teaches the player that the panel
    lies -- and it is not a hypothetical: the HUD kept its own spread
    thresholds for a while after K had moved to the report's verdict.
    """

    def _screen_with(self, deltas):
        from pickhero.matcher import NoteMatcher, TimingSample
        from pickhero.tabs.timeline import SongMetadata, Timeline
        screen = PlayingScreen(_make_timeline(), config=Config())
        matcher = NoteMatcher(Timeline([], SongMetadata(title="x", tempo=100)))
        for i, d in enumerate(deltas):
            matcher.timing_errors_ms.append(d)
            matcher.timing_samples.append(
                TimingSample(delta_ms=d, string=6, midi_note=40,
                             note_ms=1000.0 * i)
            )
        screen._matcher = matcher
        return screen

    CASES = [
        ("plain latency", [118, 122, 120, 125, 119, 121, 123, 120, 124, 119]),
        ("scattered", [-90, 80, -70, 95, -85, 75, -60, 88, -95, 70, 82, -78]),
        ("already fine", [2, -3, 1, 4, -2, 0, 3, -1, 2, -4]),
        ("too few", [120, 118, 122]),
    ]

    @pytest.mark.parametrize("label,deltas", CASES)
    def test_the_line_offers_k_exactly_when_k_would_act(self, label, deltas):
        screen = self._screen_with(deltas)
        advice = screen._sync_advice()
        offers_k = "K to auto-sync" in advice or "K again" in advice

        before = screen._config.audio_latency_offset_ms
        screen._auto_sync_timing()
        acted = screen._config.audio_latency_offset_ms != before

        assert offers_k == acted, f"{label}: line said {advice!r}, K acted={acted}"

    def test_a_residual_is_named_rather_than_re_offered_as_a_first_sync(self):
        screen = self._screen_with([120] * 10)
        screen._auto_sync_timing()
        # Fresh samples showing what the press did not take.
        screen._matcher.reset_timing_samples()
        for i, d in enumerate([49] * 10):
            screen._matcher.timing_errors_ms.append(d)
            from pickhero.matcher import TimingSample
            screen._matcher.timing_samples.append(
                TimingSample(delta_ms=d, string=6, midi_note=40, note_ms=1000.0 * i)
            )
        assert "still left, K again" in screen._sync_advice()

    def test_nothing_is_offered_before_there_is_anything_to_measure(self):
        assert "still measuring" in self._screen_with([]).\
            _sync_advice()


class TestSyncLineIsAlwaysThere:
    """Shift+K must visibly do something.

    The line used to be hidden whenever the offset was zero and nothing had
    been measured -- which is precisely the state Shift+K creates. The one key
    whose whole job is to put the offset back to zero therefore looked like it
    had done nothing, and the player pressed it again and again.
    """

    def _screen(self):
        from pickhero.matcher import NoteMatcher
        from pickhero.tabs.timeline import SongMetadata, Timeline
        screen = PlayingScreen(_make_timeline(), config=Config())
        screen._matcher = NoteMatcher(
            Timeline([], SongMetadata(title="x", tempo=100))
        )
        screen._audio_enabled = True
        return screen

    def test_advice_exists_with_nothing_measured_yet(self):
        assert self._screen()._sync_advice() != ""

    def test_resetting_to_zero_leaves_something_to_show(self):
        screen = self._screen()
        screen._adjust_latency_offset(-135.0)
        assert screen._config.audio_latency_offset_ms == -135.0
        screen._reset_latency_offset()
        assert screen._config.audio_latency_offset_ms == 0.0
        # The state Shift+K produces still has a line to render.
        assert screen._sync_advice() != ""


class TestAudioClockAnchor:
    """Changing the practice speed must not move the strikes.

    A strike is stamped in recorded time, which runs at real speed; the song
    runs at a fraction of it. The product of the two is only a song position
    when both are counted from the same moment, so touching the speed has to
    move that moment -- otherwise every strike after the change is displaced
    by (elapsed x change), which grows for the rest of the song and no sync
    offset can take it back.
    """

    class _FakeCapture:
        def __init__(self, elapsed_ms: float):
            self._elapsed = elapsed_ms
            self.drained = 0

        def elapsed_ms(self) -> float:
            return self._elapsed

        def get_notes(self):
            self.drained += 1
            return []

        def get_strike_windows(self):
            return []

    def _screen(self, *, elapsed_ms, playback_ms, tempo=1.0):
        from pickhero.matcher import NoteMatcher
        config = Config()
        config.tempo_factor = tempo
        screen = PlayingScreen(_make_timeline(), config=config)
        screen._matcher = NoteMatcher(_make_timeline())
        screen._audio_capture = self._FakeCapture(elapsed_ms)
        screen._playback_ms = playback_ms
        screen._audio_anchor_ms = 0.0
        screen._audio_anchor_song_ms = 0.0
        screen._matcher.audio_offset_ms = 0.0
        return screen

    def _song_position(self, screen, strike_ms):
        """Where the app decides a strike stamped at strike_ms happened."""
        return strike_ms * screen._tempo_factor + screen._matcher.audio_offset_ms

    def test_a_strike_keeps_its_place_across_a_speed_change(self):
        # 20 s of audio have gone by at full speed, so the song is at 20 s.
        screen = self._screen(elapsed_ms=20_000.0, playback_ms=20_000.0)
        screen.set_tempo_factor(0.8)
        # The very next strike is stamped where the audio clock stands now,
        # and must still read as the song position the player can see.
        assert self._song_position(screen, 20_000.0) == pytest.approx(20_000.0)

    def test_later_strikes_advance_at_the_new_speed(self):
        screen = self._screen(elapsed_ms=20_000.0, playback_ms=20_000.0)
        screen.set_tempo_factor(0.5)
        # One further second of playing is half a second of song.
        assert self._song_position(screen, 21_000.0) == pytest.approx(20_500.0)

    def test_the_sync_offset_survives_the_change(self):
        screen = self._screen(elapsed_ms=10_000.0, playback_ms=10_000.0)
        screen._config.audio_latency_offset_ms = -60.0
        screen.set_tempo_factor(0.75)
        assert self._song_position(screen, 10_000.0) == pytest.approx(9_940.0)

    def test_strikes_stamped_before_the_change_are_dropped(self):
        """They were stamped under the old speed and would be read under the
        new one, which puts them somewhere they never were."""
        screen = self._screen(elapsed_ms=10_000.0, playback_ms=10_000.0)
        screen.set_tempo_factor(0.9)
        assert screen._audio_capture.drained == 1


class TestRunLog:
    """The file that says what the audio path actually did."""

    def _played_screen(self):
        from pickhero.audio.detector import DetectedNote
        from pickhero.audio.input import TimestampedNote
        from pickhero.matcher import NoteMatcher
        notes = [
            NoteEvent(timestamp_ms=1000.0, midi_note=40, string=6, fret=0,
                      duration_ms=500.0, measure=0),
            NoteEvent(timestamp_ms=2000.0, midi_note=45, string=5, fret=0,
                      duration_ms=500.0, measure=0),
        ]
        timeline = _make_timeline(notes=notes)
        screen = PlayingScreen(timeline, config=Config())
        screen._song_key = "test.gp5"
        screen._matcher = NoteMatcher(timeline, timing_window_ms=150.0)
        struck = TimestampedNote(
            note=DetectedNote(40, 82.4, 0.95, "E2", True), timestamp_ms=1010.0)
        screen._matcher.process_detected_notes([struck], 1010.0)
        return screen

    def _log_text(self, screen) -> str:
        import io
        buffer = io.StringIO()
        screen._write_run_log(buffer)
        return buffer.getvalue()

    def test_it_names_the_practice_speed(self):
        screen = self._played_screen()
        screen._tempo_factor = 0.8
        assert "tempo_percent\t80" in self._log_text(screen)

    def test_every_strike_gets_a_line(self):
        text = self._log_text(self._played_screen())
        strikes = [line for line in text.splitlines() if "\thit\t" in line]
        assert len(strikes) == 1

    def test_a_strike_says_which_note_it_was_credited_to(self):
        text = self._log_text(self._played_screen())
        line = next(l for l in text.splitlines() if "\thit\t" in l)
        assert line.split("\t")[8] == "1000.0"

    def test_every_written_note_gets_a_verdict(self):
        text = self._log_text(self._played_screen())
        table = text.split("# every written note and how it ended up")[1]
        rows = [r for r in table.splitlines() if r and not r.startswith("note_ms")]
        assert len(rows) == 2

    def test_a_note_never_struck_reads_as_pending_not_as_hit(self):
        text = self._log_text(self._played_screen())
        table = text.split("# every written note and how it ended up")[1]
        assert "2000.0\t5\t45\tpending" in table

    def test_the_counts_that_explain_a_bad_score_are_in_the_header(self):
        text = self._log_text(self._played_screen())
        for key in ("dropped_buffers", "sample_rate", "hit_window_ms",
                    "sync_offset_ms", "strings_taken_back", "notes_written"):
            assert f"{key}\t" in text

    def test_writing_it_reports_where_it_went(self, tmp_path):
        screen = self._played_screen()
        screen._export_run_log()
        assert "run_test_gp5_" in screen._run_log_note

    def test_it_says_so_when_there_is_nothing_to_write(self):
        screen = PlayingScreen(_make_timeline(), config=Config())
        screen._export_run_log()
        assert "audio was off" in screen._run_log_note


class TestHeardLine:
    """A low score has two completely different causes and one number.

    Notes never heard are a microphone problem; notes heard and not credited
    are a matching problem. The completion screen has to say which, or the
    next session is spent guessing again -- which is exactly what happened.
    """

    def _screen_with(self, outcomes, taken_back=0):
        from pickhero.matcher import NoteMatcher, StrikeTrace
        screen = PlayingScreen(_make_timeline(), config=Config())
        screen._matcher = NoteMatcher(_make_timeline())
        screen._matcher.strike_trace = [
            StrikeTrace(strike_ms=0.0, adjusted_ms=0.0, playback_ms=0.0,
                        midi_note=40, confidence=0.9, unpitched=False,
                        subharmonic=False, outcome=o, note_ms=None,
                        semitones=None)
            for o in outcomes
        ]
        screen._matcher.chord_strings_corrected = taken_back
        return screen

    def test_it_counts_the_strikes_that_were_heard(self):
        screen = self._screen_with(["hit", "hit", "unmatched"])
        assert "3 strikes heard" in screen._heard_line()

    def test_it_counts_the_strikes_that_landed_separately(self):
        screen = self._screen_with(["hit", "close", "unmatched"])
        assert "2 of them landed" in screen._heard_line()

    def test_a_taken_back_string_is_not_counted_as_a_strike(self):
        """It is the same strike being judged again, not another one."""
        screen = self._screen_with(["hit", "string_taken_back"], taken_back=1)
        assert "1 strikes heard" in screen._heard_line()

    def test_taken_back_strings_are_named_when_there_are_any(self):
        screen = self._screen_with(["hit"], taken_back=2)
        assert "2 strings taken back" in screen._heard_line()

    def test_nothing_taken_back_says_nothing_about_it(self):
        screen = self._screen_with(["hit"])
        assert "taken back" not in screen._heard_line()


class TestLevelAdvice:
    """"Gate: -65 dB" is a number, not an instruction.

    A player whose signal sits under the gate sees notes go unrecognised and
    has no way to know that a threshold, not their playing, is eating them.
    """

    def _screen(self, peak, floor, gate=-60.0):
        screen = PlayingScreen(_make_timeline(), config=Config())
        # A stopped song has nothing to measure, so the advice stays quiet;
        # every case here is about a song that is running.
        screen._playing = True
        screen._noise_gate_db = gate
        screen._signal_peak_db = peak
        screen._signal_floor_db = floor
        return screen

    def test_a_healthy_level_says_nothing(self):
        assert self._screen(peak=-20.0, floor=-75.0)._level_advice() == ""

    def test_nothing_is_claimed_before_anything_was_heard(self):
        assert self._screen(peak=-120.0, floor=0.0)._level_advice() == ""

    def test_a_signal_that_barely_clears_the_gate_says_so(self):
        advice = self._screen(peak=-35.0, floor=-75.0, gate=-40.0)._level_advice()
        assert "X" in advice and "louder" in advice

    def test_a_signal_too_weak_for_the_detector_says_so(self):
        """Measured rather than assumed: below about -40 dB the strikes keep
        arriving and their pitch rots, which reads as bad playing."""
        advice = self._screen(peak=-50.0, floor=-90.0, gate=-80.0)._level_advice()
        assert "too quiet" in advice and "wrong note" in advice

    def test_a_stopped_song_says_nothing(self):
        """The peak decays while nothing is played, so the completion screen
        would otherwise report a level fault that is not there."""
        screen = self._screen(peak=-50.0, floor=-90.0, gate=-80.0)
        screen._playing = False
        assert screen._level_advice() == ""

    def test_clipping_is_named_before_anything_else(self):
        """Distortion destroys the period YIN looks for, so it outranks a
        quiet-signal reading that the same run would also produce."""
        advice = self._screen(peak=-2.0, floor=-70.0)._level_advice()
        assert "Too loud" in advice

    def test_background_noise_reaching_the_gate_says_so(self):
        advice = self._screen(peak=-20.0, floor=-58.0)._level_advice()
        assert "C" in advice and "noise" in advice.lower()

    def test_raising_the_gate_can_silence_the_noise_advice(self):
        screen = self._screen(peak=-20.0, floor=-58.0)
        assert screen._level_advice() != ""
        screen._noise_gate_db = -45.0
        assert screen._level_advice() == ""

    def test_levels_are_only_tracked_while_the_song_runs(self):
        screen = self._screen(peak=-120.0, floor=0.0)
        screen._playing = False
        screen._track_levels(-20.0)
        assert screen._signal_peak_db == -120.0

    def test_the_peak_decays_so_one_loud_accident_does_not_stick(self):
        screen = self._screen(peak=-120.0, floor=0.0)
        screen._track_levels(-10.0)
        for _ in range(1000):
            screen._track_levels(-70.0)
        assert screen._signal_peak_db < -20.0

    def test_a_silent_frame_does_not_erase_the_peak_at_once(self):
        screen = self._screen(peak=-120.0, floor=0.0)
        screen._track_levels(-20.0)
        screen._track_levels(-70.0)
        assert screen._signal_peak_db == pytest.approx(-20.05)


class TestTopRightHudDoesNotOverlap:
    """The gate used to be drawn straight over the hit count.

    The block above it renders two lines and the spacing had been counted for
    one, which is the kind of thing a fixed pixel offset does the moment
    anything above it grows.
    """

    def test_draw_stats_reports_where_it_ended(self):
        pygame.init()
        pygame.display.set_mode((320, 240))
        from pickhero.ui.feedback import FeedbackRenderer
        font = pygame.font.SysFont("arial", 14)
        surface = pygame.Surface((320, 240))
        renderer = FeedbackRenderer()
        stats = {"hits": 57, "close": 3, "misses": 2, "total": 62,
                 "accuracy_percent": 91.9}
        end = renderer.draw_stats(surface, stats, font, 300, 36)
        assert end >= 36 + 2 * font.get_height()
        pygame.display.quit()


class TestSeekingDoesNotReopenTheDevice:
    """Seeking used to close and reopen the audio input device.

    On Windows that is a real device open, and it happened on every arrow key
    and every loop turn -- the player reported the app freezing for about ten
    seconds after a seek. The clock still has to learn that the song moved,
    but re-anchoring does that without touching the hardware.
    """

    class _FakeCapture:
        def __init__(self):
            self.starts = 0
            self.stops = 0

        def start(self):
            self.starts += 1

        def stop(self):
            self.stops += 1

        def elapsed_ms(self):
            return 20_000.0

        def get_notes(self):
            return []

        def get_strike_windows(self):
            return []

        def get_signal_db(self):
            return -40.0

        def get_tuner_data(self):
            return (0.0, 0.0)

    def _screen(self):
        from pickhero.matcher import NoteMatcher
        notes = [NoteEvent(timestamp_ms=t, duration_ms=200.0, midi_note=40,
                           string=6, fret=0, measure=0)
                 for t in (1000.0, 30000.0)]
        timeline = Timeline(notes, SongMetadata(title="T", tempo=100))
        screen = PlayingScreen(timeline, config=Config())
        screen._matcher = NoteMatcher(timeline)
        screen._audio_capture = self._FakeCapture()
        screen._audio_enabled = True
        screen._playing = True
        screen._playback_ms = 5000.0
        return screen

    def test_seeking_leaves_the_stream_alone(self):
        screen = self._screen()
        screen.seek(12000.0)
        assert screen._audio_capture.stops == 0
        assert screen._audio_capture.starts == 0

    def test_seeking_still_moves_the_audio_clock(self):
        """Leaving the device alone must not mean leaving the clock wrong."""
        screen = self._screen()
        screen.seek(12000.0)
        # A strike stamped at the capture's current position must read as the
        # song position the player can see.
        adjusted = 20_000.0 * screen._tempo_factor + screen._matcher.audio_offset_ms
        assert adjusted == pytest.approx(12000.0)

    def test_a_loop_turn_leaves_the_stream_alone(self):
        """Loops happen every few seconds -- this one mattered most."""
        screen = self._screen()
        screen._loop_enabled = True
        screen._loop_start_ms = 1000.0
        screen._loop_end_ms = 6000.0
        screen._last_tick = None
        screen._playback_ms = 6500.0
        screen.update()
        assert screen._audio_capture.stops == 0
        assert screen._audio_capture.starts == 0
        assert screen._playback_ms == 1000.0


class TestRunLogRecordsTheLevel:
    """A weak input does not lose strikes, it corrupts their pitch -- which is
    indistinguishable from bad playing unless the level is written down."""

    def _screen(self, levels):
        from pickhero.matcher import NoteMatcher
        screen = PlayingScreen(_make_timeline(), config=Config())
        screen._song_key = "song"
        screen._matcher = NoteMatcher(_make_timeline())
        screen._level_samples = list(levels)
        return screen

    def _log(self, screen):
        import io
        buffer = io.StringIO()
        screen._write_run_log(buffer)
        return buffer.getvalue()

    def test_the_loudest_hop_is_written(self):
        text = self._log(self._screen([-60.0, -30.0, -45.0]))
        assert "level_loudest_db\t-30.0" in text

    def test_the_share_under_the_gate_is_written(self):
        screen = self._screen([-70.0, -70.0, -30.0, -30.0])
        screen._config.audio.noise_gate_db = -60.0
        assert "level_under_gate_percent\t50" in self._log(screen)

    def test_a_run_with_no_audio_says_so_rather_than_inventing_a_number(self):
        assert "(nothing measured)" in self._log(self._screen([]))


class TestHeadUsesTheHeightItHas:
    """A head squeezed narrow by a dense song is still full height.

    Measured on the case the player reported -- a real song at 135 BPM whose
    sixteenths put the notes 111 ms apart: the head sits at its 26 px floor
    inside a 56 px lane, leaving 53 % of the height unused. Look-ahead is
    bought and sold in WIDTH, so keeping the height costs nothing at all.
    """

    def _screen(self, spacing_ms, fret=5):
        notes = [
            NoteEvent(timestamp_ms=i * spacing_ms, duration_ms=150.0,
                      midi_note=40 + (i % 6), string=6 - (i % 6), fret=fret,
                      measure=i // 8)
            for i in range(60)
        ]
        screen = PlayingScreen(_make_timeline(notes=notes), config=Config())
        surface = pygame.Surface((1277, 771))
        screen._recompute_scroll_speed(screen._layout(surface))
        return screen

    def test_a_dense_song_keeps_its_height(self):
        screen = self._screen(111.0)
        assert screen._head_px < screen._head_h_px

    def test_a_roomy_song_stays_round(self):
        screen = self._screen(600.0)
        assert screen._head_px == pytest.approx(screen._head_h_px)

    def test_the_height_costs_no_look_ahead(self):
        """The whole point: it is free. Width is the currency, not height."""
        screen = self._screen(111.0)
        window_with_height = screen._visible_window_ms
        screen._head_h_px = screen._head_px       # as it was before
        screen._recompute_scroll_speed(screen._layout(pygame.Surface((1277, 771))))
        assert screen._visible_window_ms == pytest.approx(window_with_height)

    def test_every_fret_number_is_the_same_size(self):
        """A lone 5 towering over the 15 beside it reads as emphasis the
        music never asked for."""
        notes = [
            NoteEvent(timestamp_ms=i * 400.0, duration_ms=150.0, midi_note=40,
                      string=6, fret=(5 if i % 2 else 15), measure=0)
            for i in range(20)
        ]
        screen = PlayingScreen(_make_timeline(notes=notes), config=Config())
        screen._recompute_scroll_speed(screen._layout(pygame.Surface((1277, 771))))
        assert screen._fret_digits == 2

    def test_the_number_fills_the_width_it_has(self):
        """Where the digits grew: the old rule was a fixed multiple of the
        radius and left room unused. The HEIGHT does not help a two-digit
        label -- at a 26 px head the width is what binds -- so the height's
        gain is the size of the note itself, not of the number."""
        pygame.init()
        pygame.display.set_mode((320, 240))
        screen = self._screen(111.0)
        font = screen._fret_font(13.0, 23.5, 2)
        assert font.get_height() > int(13.0 * 1.1)     # the old rule
        assert font.render("20", True, (0, 0, 0)).get_width() <= 26
        pygame.display.quit()

    def test_a_flat_head_is_limited_by_its_height(self):
        pygame.init()
        pygame.display.set_mode((320, 240))
        screen = self._screen(111.0)
        flat = screen._fret_font(40.0, 8.0, 2)
        assert flat.get_height() <= 16
        pygame.display.quit()
