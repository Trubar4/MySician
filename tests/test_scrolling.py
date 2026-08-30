"""Tests for scrolling display math.

Tests the pure computation functions in PlayingScreen without needing
a running PyGame display.
"""

import pygame
import pytest

from pickhero.config import MAX_GATE_DB, MIN_GATE_DB, Config
from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)
from pickhero.ui.scrolling import (
    MIN_NOTE_WIDTH_PX,
    ROOM_SAMPLES,
    PlayingScreen,
    format_time,
    gate_band,
    suggested_gate_db,
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

    def test_the_speed_belongs_to_the_song(self):
        """The solo being learned at 70 % is still at 70 % tomorrow."""
        from pickhero.config import Config
        config = Config()
        config.set_tempo_factor_for("solo", 0.75)
        screen = PlayingScreen(_make_timeline(), config=config, song_key="solo")
        assert screen._tempo_factor == pytest.approx(0.75)

    def test_another_song_opens_at_full_speed(self):
        """A song that never needed slowing must not inherit what the last
        one needed -- which is what a single global speed did."""
        from pickhero.config import Config
        config = Config()
        config.set_tempo_factor_for("solo", 0.6)
        screen = PlayingScreen(_make_timeline(), config=config, song_key="other")
        assert screen._tempo_factor == 1.0

    def test_a_song_never_slowed_opens_at_full_speed(self):
        from pickhero.config import Config
        screen = PlayingScreen(_make_timeline(), config=Config(), song_key="new")
        assert screen._tempo_factor == 1.0

    def test_changing_the_speed_stores_it_for_this_song(self):
        from pickhero.config import Config
        config = Config()
        screen = PlayingScreen(_make_timeline(), config=config, song_key="solo")
        screen.set_tempo_factor(0.7)
        assert config.tempo_factor_for("solo") == pytest.approx(0.7)

    def test_full_speed_is_not_stored(self):
        """Storing 1.0 fills the file with entries that say nothing, and full
        speed is what a song opens at anyway."""
        from pickhero.config import Config
        config = Config()
        screen = PlayingScreen(_make_timeline(), config=config, song_key="solo")
        screen.set_tempo_factor(0.7)
        screen.set_tempo_factor(1.0)
        assert "solo" not in config.song_tempo_factors

    def test_a_stored_speed_out_of_range_is_ignored(self):
        from pickhero.config import Config
        config = Config()
        config.song_tempo_factors["solo"] = 0.2
        screen = PlayingScreen(_make_timeline(), config=config, song_key="solo")
        assert screen._tempo_factor == 1.0

    def test_tools_can_still_read_the_speed_in_use(self):
        """record_reference writes it into a take's manifest, and an analysis
        that does not know the speed reads a stretched take against the wrong
        grid -- which cost a whole session once."""
        from pickhero.config import Config
        config = Config()
        screen = PlayingScreen(_make_timeline(), config=config, song_key="solo")
        screen.set_tempo_factor(0.65)
        assert config.tempo_factor == pytest.approx(0.65)


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

    def _screen_with(self, deltas, tempo=1.0):
        from pickhero.matcher import NoteMatcher, TimingSample
        from pickhero.tabs.timeline import SongMetadata, Timeline
        screen = PlayingScreen(_make_timeline(), config=Config())
        # Before the samples: changing the speed resets them, on purpose.
        screen.set_tempo_factor(tempo)
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

    def test_what_it_stores_is_real_time_not_song_time(self):
        """The samples are song milliseconds and the setting is real ones.
        Stored unconverted, an offset calibrated at 70 % would be a seventh
        too small the moment the song went back to full speed -- and the
        player has no way to see that, because the number on screen looks
        exactly the same either way."""
        screen = self._screen_with([118, 122, 120, 125, 119, 121, 123, 120,
                                    124, 119], tempo=0.5)
        screen._auto_sync_timing()
        # 120 ms of song at half speed is 240 ms of the world.
        assert self._offset(screen) == pytest.approx(-240, abs=8)

    def test_and_that_is_what_the_matcher_then_gets_back(self):
        """Round trip: measure at one speed, and the strikes land on the beat
        at that same speed rather than being corrected twice."""
        screen = self._screen_with([118, 122, 120, 125, 119, 121, 123, 120,
                                    124, 119], tempo=0.5)
        screen._auto_sync_timing()
        assert screen._sync_offset_song_ms() == pytest.approx(-120, abs=4)

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
        """And it is scaled, because it is a delay of the real world.

        The sound card's buffer and aubio's analysis window are a fixed
        number of SAMPLES; neither knows the song has been slowed down. A
        strike is stamped in recorded time and scaled into song time, so its
        compensation is scaled with it. Applied unscaled it over-corrects by
        (1 - tempo) of itself -- 66 ms of a 200 ms hit window on the player's
        70 % run, spent before they had played anything.
        """
        screen = self._screen(elapsed_ms=10_000.0, playback_ms=10_000.0)
        screen._config.audio_latency_offset_ms = -60.0
        screen.set_tempo_factor(0.75)
        assert self._song_position(screen, 10_000.0) == pytest.approx(9_955.0)

    def test_full_speed_is_untouched_by_the_scaling(self):
        """Every offset ever calibrated was calibrated at some speed, and at
        full speed the two times are the same thing."""
        screen = self._screen(elapsed_ms=10_000.0, playback_ms=10_000.0)
        screen._config.audio_latency_offset_ms = -60.0
        screen.set_tempo_factor(1.0)
        assert self._song_position(screen, 10_000.0) == pytest.approx(9_940.0)

    def test_the_offset_the_player_set_is_what_stays_in_the_config(self):
        """Scaling happens on the way to the matcher, not in the setting. A
        value that changed itself when you slowed the song down could never
        be judged by feel, and K would fight it every run."""
        screen = self._screen(elapsed_ms=1000.0, playback_ms=1000.0)
        screen._config.audio_latency_offset_ms = -60.0
        screen.set_tempo_factor(0.5)
        assert screen._config.audio_latency_offset_ms == pytest.approx(-60.0)
        assert screen._sync_offset_song_ms() == pytest.approx(-30.0)

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
                    "sync_offset_ms", "strings_taken_back", "notes_written",
                    "rescued_notes", "input_device"):
            assert f"{key}\t" in text

    def test_it_names_the_input_it_was_listening_to(self):
        """A log reporting a silent stream without saying WHICH device was
        silent cannot tell a wrong device from a blocked one -- and on a
        machine listing the same interface under MME, DirectSound and WASAPI
        that is the whole question."""
        screen = self._played_screen()
        class Capture:
            def describe_device(self):
                return "Focusrite USB — index 7, 2 of 2 channel(s) at 48000 Hz"
        screen._audio_capture = Capture()
        assert "Focusrite USB" in self._log_text(screen)

    def test_writing_it_reports_where_it_went(self, tmp_path):
        screen = self._played_screen()
        screen._export_run_log()
        assert "run_test_gp5_" in screen._run_log_note

    def test_it_says_so_when_there_is_nothing_to_write(self):
        screen = PlayingScreen(_make_timeline(), config=Config())
        screen._export_run_log()
        assert "audio was off" in screen._run_log_note


class TestPausingIsNotStoppingEverything:
    """The space bar cost a device open and an MP3 re-decode, each way.

    Reported as: pausing or seeking with a backing recording freezes the
    picture for up to three seconds and the sound stutters coming back. Three
    separate faults, all of them here.
    """

    def _screen(self, mp3=None):
        # A song long enough to have somewhere to be, and started past the
        # count-in: at position 0 the space bar counts in instead of playing.
        notes = [NoteEvent(timestamp_ms=60_000.0, duration_ms=400.0,
                           midi_note=40, string=6, fret=0, measure=0)]
        screen = PlayingScreen(_make_timeline(notes=notes), config=Config())
        screen._mp3_player = mp3
        screen._playback_ms = 30_000.0
        return screen

    class _FakeMp3:
        ready = True
        time_scale = 1.0
        suspended = False
        def __init__(self):
            self.stopped = 0
            self.seeks = 0
            self.suspends = []
        def pause(self):
            self.stopped += 1
            self.suspended = False
        def seek(self, ms):
            self.seeks += 1
            self.suspended = False
        def set_suspended(self, value):
            if value != self.suspended:
                self.suspends.append(value)
            self.suspended = value
        def update(self, ms): pass
        def drift_ms(self, ms): return 0.0

    def test_pausing_holds_the_recording_instead_of_stopping_it(self):
        """Stopping means play(start=) to come back, which decodes the file up
        to that point -- seconds, four minutes in, on a thin laptop."""
        mp3 = self._FakeMp3()
        screen = self._screen(mp3)
        screen.toggle_play()                 # play
        screen.toggle_play()                 # pause
        assert mp3.suspends[-1] is True
        assert mp3.stopped == 0

    def test_resuming_lifts_the_hold_rather_than_seeking_again(self):
        mp3 = self._FakeMp3()
        screen = self._screen(mp3)
        screen.toggle_play()
        seeks_after_start = mp3.seeks
        screen.toggle_play()                 # pause
        screen.toggle_play()                 # resume
        assert mp3.suspends[-1] is False
        assert mp3.seeks == seeks_after_start
        assert mp3.stopped == 0

    def test_the_frame_loop_does_not_undo_the_hold(self):
        """_update_mp3 runs every frame while paused too, and stopping there
        would cancel the suspension on the very next one."""
        mp3 = self._FakeMp3()
        screen = self._screen(mp3)
        screen.toggle_play()
        screen.toggle_play()                 # paused
        for _ in range(5):
            screen._update_mp3()
        assert mp3.stopped == 0
        assert mp3.suspended is True

    def test_muting_really_stops_it(self):
        """Only a PAUSE is a hold. Muting has to be silence, not a pause that
        resumes the moment the song does."""
        mp3 = self._FakeMp3()
        screen = self._screen(mp3)
        screen._mp3_muted = True
        screen._update_mp3()
        assert mp3.stopped >= 1

    def test_pausing_leaves_the_input_device_open(self):
        """Closing and reopening it is a real device open on Windows -- the
        same fault that made every arrow key freeze the app for seconds."""
        screen = self._screen()
        stops = []
        class Capture:
            def is_running(self): return True
            def stop(self): stops.append(1)
            def elapsed_ms(self): return 0.0
            def get_notes(self): return []
            def get_strike_windows(self): return []
        from pickhero.matcher import NoteMatcher
        screen._audio_enabled = True
        screen._audio_capture = Capture()
        screen._matcher = NoteMatcher(_make_timeline())
        screen._playing = True
        screen.toggle_play()                 # pause
        assert stops == []

    def test_resuming_reanchors_instead_of_reopening(self):
        screen = self._screen()
        starts = []
        class Capture:
            def is_running(self): return True
            def stop(self): starts.append("stop")
            def start(self): starts.append("start")
            def elapsed_ms(self): return 4000.0
            def get_notes(self): return []
            def get_strike_windows(self): return []
        from pickhero.matcher import NoteMatcher
        screen._audio_enabled = True
        screen._audio_capture = Capture()
        screen._matcher = NoteMatcher(_make_timeline())
        screen.toggle_play()                 # play
        assert starts == []
        assert screen._audio_anchor_ms == pytest.approx(4000.0)

    def test_a_closed_device_is_still_opened(self):
        """Re-anchoring only works on a stream that is actually there."""
        screen = self._screen()
        opened = []
        class Capture:
            def is_running(self): return False
            def stop(self): pass
            def start(self): opened.append(1)
            def elapsed_ms(self): return 0.0
            def get_notes(self): return []
            def get_strike_windows(self): return []
        from pickhero.matcher import NoteMatcher
        screen._audio_enabled = True
        screen._audio_capture = Capture()
        screen._matcher = NoteMatcher(_make_timeline())
        screen.toggle_play()
        assert opened == [1]

    def test_the_clock_starts_after_the_slow_work_not_before(self):
        """Set first, whatever the device and the decoder take is charged to
        the song, and the picture jumps forward by it on the next frame."""
        screen = self._screen()
        started_at = screen._playback_ms
        screen.toggle_play()
        assert screen._last_tick is not None
        # Nothing has been drawn yet, so no song time may have passed.
        screen.update()
        assert screen._playback_ms - started_at < 200.0


class TestScrubbingDoesNotDecodeTwentyFiveTimesASecond:
    """A held arrow key repeats every 40 ms, and every repeat was a
    play(start=) -- which decodes the file up to that point, on the frame's
    own thread. One press must still be immediate: a loop turn is a seek too,
    and delaying that would start the recording late every time round."""

    def _screen(self):
        notes = [NoteEvent(timestamp_ms=60_000.0, duration_ms=400.0,
                           midi_note=40, string=6, fret=0, measure=0)]
        screen = PlayingScreen(_make_timeline(notes=notes), config=Config())
        screen._mp3_player = TestPausingIsNotStoppingEverything._FakeMp3()
        screen._playback_ms = 30_000.0
        screen._playing = True
        return screen

    def test_one_seek_reaches_the_recording_at_once(self):
        screen = self._screen()
        screen.seek(31_000.0)
        assert screen._mp3_player.seeks == 1

    def test_a_burst_of_seeks_becomes_one(self):
        screen = self._screen()
        for i in range(20):
            screen.seek(31_000.0 + i * 100)
        assert screen._mp3_player.seeks == 1
        assert screen._mp3_pending_seek_ms is not None

    def test_the_last_position_is_the_one_that_lands(self):
        """Scrubbing forward and stopping must not leave the recording at the
        first position of the burst."""
        import time as _time
        screen = self._screen()
        for i in range(5):
            screen.seek(31_000.0 + i * 1000)
        screen._mp3_last_seek_at = _time.perf_counter() - 1.0   # they stopped
        screen._update_mp3()
        assert screen._mp3_pending_seek_ms is None
        assert screen._mp3_player.seeks == 2

    def test_the_recording_is_silent_while_it_is_being_scrubbed(self):
        """Playing on from where it was is worse than nothing here."""
        screen = self._screen()
        for i in range(5):
            screen.seek(31_000.0 + i * 100)
        assert screen._mp3_player.suspended is True


class TestAStalledFrameDoesNotTeleportTheSong:
    """"It stands still and then jumps." A frame blocked for three seconds
    advanced the song by three seconds, scrolling a bar of music past
    uncredited and landing the picture somewhere the player never saw."""

    def _screen(self):
        # An empty timeline is zero milliseconds long and clamps every
        # position to 0, which would make this assert nothing at all.
        notes = [NoteEvent(timestamp_ms=60_000.0, duration_ms=400.0,
                           midi_note=40, string=6, fret=0, measure=0)]
        screen = PlayingScreen(_make_timeline(notes=notes), config=Config())
        screen._playing = True
        screen._playback_ms = 10_000.0
        return screen

    def test_a_long_frame_advances_the_song_by_at_most_the_cap(self):
        import time as _time
        from pickhero.ui.scrolling import MAX_FRAME_STALL_S
        screen = self._screen()
        screen._last_tick = _time.perf_counter() - 3.0      # a 3 s stall
        screen.update()
        assert screen._playback_ms <= 10_000.0 + MAX_FRAME_STALL_S * 1000.0 + 50

    def test_an_ordinary_frame_is_untouched(self):
        import time as _time
        screen = self._screen()
        screen._last_tick = _time.perf_counter() - 0.016
        screen.update()
        assert screen._playback_ms == pytest.approx(10_016.0, abs=12.0)


class TestReadingTheFretNumber:
    """Reported as not being able to tell 11 from 12 in a fast solo.

    Measured on the app as it stood: a ONE-digit fret was drawn at 42 px and
    a TWO-digit one at 21 px in the same song -- half the size -- because the
    head was squeezed sideways to buy look-ahead and a number is wider than
    it is tall. The head's width is sized for the widest label in the song
    now, so only the case that was broken pays for it.
    """

    def _song(self, spacing_ms, frets):
        notes = []
        t = 0.0
        for i, fret in enumerate(frets * 12):
            notes.append(NoteEvent(timestamp_ms=t, duration_ms=spacing_ms * 0.8,
                                   midi_note=40 + fret, string=(i % 3) + 1,
                                   fret=fret, measure=i // 8))
            t += spacing_ms
        return _make_timeline(notes=notes)

    def _measure(self, spacing_ms, frets):
        pygame.init()
        surface = pygame.Surface((1400, 800))
        screen = PlayingScreen(self._song(spacing_ms, frets), config=Config())
        screen.render(surface)
        layout = screen._last_layout
        head = screen._head_px or layout.note_h
        half_h = (screen._head_h_px or layout.note_h) / 2
        font = screen._fret_font(head / 2, half_h, screen._fret_digits)
        return head, font.get_height(), screen._visible_window_ms

    def test_a_two_digit_fret_gets_a_head_wide_enough_for_it(self):
        head, digit, _ = self._measure(111.0, [10, 13, 12, 11, 15, 13])
        assert digit >= 30

    def test_a_song_of_single_digit_frets_is_left_alone(self):
        """It never had the problem, so it must not pay for the cure."""
        wide, _, window = self._measure(111.0, [3, 5, 2, 7, 0, 4])
        assert wide < 30
        assert window >= 3500.0

    def test_the_look_ahead_it_costs_is_bounded(self):
        """Trading time for size is allowed; trading away the warning is not."""
        from pickhero.ui.scrolling import MIN_VISIBLE_WINDOW_MS
        _, _, window = self._measure(111.0, [10, 13, 12, 11, 15, 13])
        assert window > MIN_VISIBLE_WINDOW_MS

    def test_a_roomy_song_is_unchanged(self):
        head, digit, window = self._measure(300.0, [10, 13, 12, 11, 15, 13])
        assert digit >= 38 and window > 5000.0

    def test_the_head_never_grows_past_its_lane(self):
        """Wider than tall buys nothing: the height is already free."""
        pygame.init()
        surface = pygame.Surface((1400, 800))
        screen = PlayingScreen(self._song(111.0, [10, 13, 12]), config=Config())
        screen.render(surface)
        assert screen._head_px <= screen._last_layout.note_h + 0.001

    def test_the_digits_are_bold(self):
        """A thin stroke is the first thing to go at speed, which is exactly
        when the fret number matters most."""
        pygame.init()
        pygame.display.set_mode((100, 100))
        from pickhero.ui.scrolling import _get_font
        plain = _get_font("consolas", 30, False)
        heavy = _get_font("consolas", 30, True)
        assert plain is not heavy
        assert heavy.size("12")[0] >= plain.size("12")[0]


class TestAnOpenStringLooksLikeOne:
    """The lane already says WHICH string, so the colour is free to say the
    thing the position cannot -- and "nothing to fret" is the most useful
    thing it can say."""

    def test_an_open_string_is_grey(self):
        from pickhero.ui.colors import OPEN_STRING_COLOR, STRING_COLORS
        assert OPEN_STRING_COLOR not in STRING_COLORS.values()

    def test_it_is_not_one_of_the_feedback_colours(self):
        """The two palettes must never be confusable -- green, yellow and red
        say how it went, not what to play."""
        from pickhero.ui.colors import OPEN_STRING_COLOR, get_theme
        pygame.init()
        theme = get_theme()
        for name in ("feedback_hit", "feedback_close", "feedback_miss"):
            assert getattr(theme, name) != OPEN_STRING_COLOR


class TestTheBoardTheNotesSitOn:
    """Without landmarks the notes float in an empty band and the only way to
    know where you are is to read the number -- which is the thing that is
    hard to read."""

    def _screen(self, bars=8):
        from pickhero.tabs.timeline import MeasureInfo
        notes = [NoteEvent(timestamp_ms=i * 500.0, duration_ms=200.0,
                           midi_note=40, string=(i % 6) + 1, fret=3,
                           measure=i // 4) for i in range(40)]
        measures = [MeasureInfo(index=i, start_ms=i * 2000.0,
                                end_ms=(i + 1) * 2000.0) for i in range(bars)]
        timeline = Timeline(notes, SongMetadata(title="x", tempo=120),
                            measures=measures)
        return PlayingScreen(timeline, config=Config())

    def test_the_bar_lines_are_drawn_as_fret_wires(self):
        pygame.init()
        surface = pygame.Surface((1400, 800))
        screen = self._screen()
        drawn = []
        real = pygame.draw.line
        import pickhero.ui.scrolling as scr
        screen.render(surface)          # a layout to work from
        screen._playback_ms = 1000.0
        try:
            pygame.draw.line = lambda s, c, a, b, w=1: drawn.append((a, b)) or None
            screen._draw_frets(surface, screen._last_layout,
                               6 * screen._last_layout.lane_height)
        finally:
            pygame.draw.line = real
        # Vertical lines: same x at both ends.
        assert drawn and all(a[0] == b[0] for a, b in drawn)

    def test_a_song_with_no_bars_draws_none_rather_than_guessing(self):
        pygame.init()
        surface = pygame.Surface((1400, 800))
        screen = PlayingScreen(_make_timeline(), config=Config())
        screen.render(surface)
        screen._draw_frets(surface, screen._last_layout, 300.0)   # must not raise

    def test_a_bar_line_is_barely_above_the_board(self):
        """A landmark is noticed when looked for and not otherwise. Drawn as a
        lit wire it pulled the eye off the notes, which is the opposite of
        what it is there for."""
        pygame.init()
        from pickhero.ui.colors import get_theme
        from pickhero.ui.scrolling import BAR_LINE_COLOR
        board = get_theme().lane_bg_even
        lift = sum(BAR_LINE_COLOR) - sum(board)
        assert 0 < lift < 120, "a bar line must whisper, not shout"
        assert BAR_LINE_COLOR != get_theme().hit_zone

    def test_bars_too_close_together_are_thinned_out(self):
        """A fast song puts bars a few pixels apart and the board turns into a
        picket fence behind the notes."""
        from pickhero.tabs.timeline import MeasureInfo
        from pickhero.ui.scrolling import MIN_BAR_LINE_GAP_PX
        pygame.init()
        surface = pygame.Surface((1400, 800))
        notes = [NoteEvent(timestamp_ms=i * 120.0, duration_ms=100.0,
                           midi_note=40, string=1, fret=3, measure=i)
                 for i in range(400)]
        measures = [MeasureInfo(index=i, start_ms=i * 120.0,
                                end_ms=(i + 1) * 120.0) for i in range(400)]
        screen = PlayingScreen(
            Timeline(notes, SongMetadata(title="x", tempo=200), measures=measures),
            config=Config())
        screen.render(surface)
        drawn = []
        real = pygame.draw.line
        try:
            pygame.draw.line = lambda s, c, a, b, w=1: drawn.append(a[0])
            screen._draw_frets(surface, screen._last_layout, 300.0)
        finally:
            pygame.draw.line = real
        gaps = [b - a for a, b in zip(sorted(drawn), sorted(drawn)[1:])]
        assert not gaps or min(gaps) >= MIN_BAR_LINE_GAP_PX * 0.5

    def test_a_roomy_song_keeps_every_bar(self):
        from pickhero.tabs.timeline import MeasureInfo
        pygame.init()
        surface = pygame.Surface((1400, 800))
        notes = [NoteEvent(timestamp_ms=i * 500.0, duration_ms=200.0,
                           midi_note=40, string=1, fret=3, measure=i // 4)
                 for i in range(40)]
        measures = [MeasureInfo(index=i, start_ms=i * 2000.0,
                                end_ms=(i + 1) * 2000.0) for i in range(10)]
        screen = PlayingScreen(
            Timeline(notes, SongMetadata(title="x", tempo=120), measures=measures),
            config=Config())
        screen.render(surface)
        drawn = []
        real = pygame.draw.line
        try:
            pygame.draw.line = lambda s, c, a, b, w=1: drawn.append(a[0])
            screen._draw_frets(surface, screen._last_layout, 300.0)
        finally:
            pygame.draw.line = real
        assert len(drawn) >= 2

    def test_the_low_strings_are_thicker_than_the_high_ones(self):
        """One weight throws away the strongest cue for which lane is which."""
        from pickhero.ui.scrolling import STRING_THICKNESS
        assert STRING_THICKNESS[0] < STRING_THICKNESS[-1]
        assert list(STRING_THICKNESS) == sorted(STRING_THICKNESS)

    def test_the_wound_strings_are_warm_and_the_plain_ones_are_not(self):
        from pickhero.ui.scrolling import PLAIN_TINT, WOUND_TINT
        assert WOUND_TINT[0] - WOUND_TINT[2] > 60      # brass: red over blue
        assert abs(PLAIN_TINT[0] - PLAIN_TINT[2]) < 20  # steel: neutral


class TestANoteIsNotOverBecauseTheClockPassedIt:
    """Reported as distracting: a note goes DARK for a moment and then turns
    green. It was not a glitch -- it was the whole width of the hit window
    being drawn as "already missed". The verdict cannot arrive any sooner:
    the strike is still inside the window (200 ms) and the late window
    (370 ms) beyond it, and a chord verdict trails its strike by ~380 ms by
    design.
    """

    def _screen(self):
        from pickhero.matcher import NoteMatcher
        notes = [NoteEvent(timestamp_ms=1000.0, duration_ms=400.0,
                           midi_note=40, string=6, fret=3, measure=0),
                 NoteEvent(timestamp_ms=9000.0, duration_ms=400.0,
                           midi_note=45, string=5, fret=3, measure=0)]
        timeline = _make_timeline(notes=notes)
        screen = PlayingScreen(timeline, config=Config())
        screen._audio_enabled = True
        screen._matcher = NoteMatcher(timeline, timing_window_ms=200.0)
        return screen, timeline.notes[0]

    def _colour_of(self, screen, note):
        """What _draw_notes would paint this note, by the same rules."""
        from pickhero.matcher import MatchType
        from pickhero.ui.colors import STRING_COLORS, dimmed
        base = STRING_COLORS[note.string]
        if screen._audio_enabled and screen._matcher is not None:
            over = (screen._matcher.get_note_state(note)
                    is not MatchType.PENDING)
        else:
            over = note.timestamp_ms < screen._playback_ms
        if screen._audio_enabled:
            return screen._feedback.get_note_color(
                note, base, screen._playback_ms, over)
        return dimmed(base) if over else base

    def test_a_note_inside_its_own_window_is_still_full_colour(self):
        from pickhero.ui.colors import STRING_COLORS
        screen, note = self._screen()
        screen._playback_ms = 1100.0        # 100 ms past it, window is 200
        assert self._colour_of(screen, note) == STRING_COLORS[note.string]

    def test_it_is_still_full_colour_deep_into_the_late_window(self):
        from pickhero.ui.colors import STRING_COLORS
        screen, note = self._screen()
        screen._playback_ms = 1400.0
        assert self._colour_of(screen, note) == STRING_COLORS[note.string]

    def test_once_the_matcher_calls_it_missed_it_turns_red(self):
        from pickhero.ui.colors import get_theme
        screen, note = self._screen()
        results = screen._matcher.process_detected_notes([], 3000.0)
        screen._feedback.add_results(results, 3000.0)
        screen._playback_ms = 3000.0
        assert self._colour_of(screen, note) == get_theme().feedback_miss

    def test_a_note_that_was_hit_turns_green_with_no_dark_step_before_it(self):
        from pickhero.audio.detector import DetectedNote
        from pickhero.audio.input import TimestampedNote
        from pickhero.ui.colors import STRING_COLORS, get_theme
        screen, note = self._screen()
        seen = []
        for ms in (1050.0, 1100.0, 1150.0):
            screen._playback_ms = ms
            seen.append(self._colour_of(screen, note))
        struck = TimestampedNote(
            note=DetectedNote(40, 82.4, 0.95, "E2", True), timestamp_ms=1160.0)
        results = screen._matcher.process_detected_notes([struck], 1160.0)
        screen._feedback.add_results(results, 1160.0)
        screen._playback_ms = 1160.0
        seen.append(self._colour_of(screen, note))
        # Full colour throughout, then green. Nothing dimmed in between.
        assert seen[:3] == [STRING_COLORS[note.string]] * 3
        assert seen[3] == get_theme().feedback_hit

    def test_with_audio_off_the_clock_is_still_the_answer(self):
        """Nothing is coming to decide it, so there is nothing to wait for."""
        from pickhero.ui.colors import STRING_COLORS, dimmed
        screen, note = self._screen()
        screen._audio_enabled = False
        screen._playback_ms = 1100.0
        assert self._colour_of(screen, note) == dimmed(STRING_COLORS[note.string])


class TestTheHitLineStandsProudOfTheBoard:
    def test_it_runs_past_the_board_top_and_bottom(self):
        """Flush with the edge it is one more vertical among the fret wires;
        past it, it is the thing the board scrolls through -- and the
        overhang stays visible where a long note covers the line itself."""
        pygame.init()
        surface = pygame.Surface((1400, 800))
        screen = PlayingScreen(_make_timeline(), config=Config())
        screen.render(surface)
        layout = screen._last_layout
        drawn = []
        real = pygame.draw.line
        try:
            pygame.draw.line = lambda s, c, a, b, w=1: drawn.append((a, b, w))
            screen._draw_hit_zone(surface, layout)
        finally:
            pygame.draw.line = real
        from pickhero.ui.scrolling import HIT_LINE_OVERHANG_PX
        (_, top), (_, bottom), _ = drawn[-1]
        assert top < layout.lane_top
        assert bottom > layout.lane_top + 6 * layout.lane_height
        assert HIT_LINE_OVERHANG_PX > 0


class TestTheBoardIsAnObjectOnABackground:
    def test_the_board_and_the_surround_are_told_apart(self):
        """They were ten points apart -- near-black on near-black -- so the
        board dissolved into the screen and the notes floated. BOTH themes:
        the light one had the identical problem the other way up."""
        from pickhero.ui.colors import DARK_THEME, LIGHT_THEME
        for theme in (DARK_THEME, LIGHT_THEME):
            distance = sum(abs(a - b) for a, b in zip(theme.bg, theme.lane_bg_even))
            assert distance > 40, theme.bg


class TestTheStringColoursStayApartFromTheFeedback:
    """Two palettes share the screen and must never be confusable: one says
    WHICH STRING, the other says HOW IT WENT."""

    def test_no_string_colour_is_near_a_feedback_colour(self):
        pygame.init()
        from pickhero.ui.colors import STRING_COLORS, get_theme
        theme = get_theme()
        feedback = (theme.feedback_hit, theme.feedback_close, theme.feedback_miss)
        for string, colour in STRING_COLORS.items():
            for fb in feedback:
                distance = sum(abs(a - b) for a, b in zip(colour, fb))
                assert distance > 120, f"string {string} is too close to {fb}"

    def test_neighbouring_lanes_do_not_share_a_hue(self):
        """The lane above is the one a note can be confused with."""
        import colorsys
        from pickhero.ui.colors import STRING_COLORS
        def hue(c):
            return colorsys.rgb_to_hsv(*[v / 255 for v in c])[0] * 360
        for s in range(1, 6):
            a, b = hue(STRING_COLORS[s]), hue(STRING_COLORS[s + 1])
            apart = min(abs(a - b), 360 - abs(a - b))
            assert apart > 25, f"strings {s} and {s + 1} share a hue"

    def test_every_string_colour_carries_white_text(self):
        from pickhero.ui.colors import STRING_COLORS
        for string, colour in STRING_COLORS.items():
            # Rec. 601 luma: the digit is white with a dark outline, so a
            # colour that is nearly white would lose the digit entirely.
            luma = 0.299 * colour[0] + 0.587 * colour[1] + 0.114 * colour[2]
            assert luma < 200, f"string {string} is too pale for white type"


class TestDrawingTheSameTextAgain:
    """Measured: one frame of the playing screen rasterised 62 text surfaces,
    and that was 79 % of the frame -- against 8 % for drawing the notes. The
    footer alone was 66 %, and the footer is the list of keyboard shortcuts,
    which never changes at all. Caching it took a frame from 15.2 ms to
    1.5 ms, and a dense song from unplayable to 2.7 ms."""

    def _font(self):
        pygame.init()
        from pickhero.ui.scrolling import _get_font
        return _get_font("arial", 14)

    def test_the_same_text_comes_back_as_the_same_surface(self):
        font = self._font()
        first = font.render("Tempo: 80 %", True, (255, 255, 255))
        assert font.render("Tempo: 80 %", True, (255, 255, 255)) is first

    def test_different_text_is_drawn_afresh(self):
        font = self._font()
        assert (font.render("1:04", True, (255, 255, 255))
                is not font.render("1:05", True, (255, 255, 255)))

    def test_the_colour_is_part_of_it(self):
        """Green and red say completely different things about a note."""
        font = self._font()
        assert (font.render("hit", True, (0, 255, 0))
                is not font.render("hit", True, (255, 0, 0)))

    def test_asking_twice_gives_the_same_font_object(self):
        """Otherwise every lookup would hand back an empty cache."""
        pygame.init()
        from pickhero.ui.scrolling import _get_font
        assert _get_font("arial", 14) is _get_font("arial", 14)

    def test_it_does_not_grow_without_end(self):
        """A clock ticking through a long song must not become a leak."""
        font = self._font()
        from pickhero.ui.scrolling import _CachedFont
        for i in range(_CachedFont.MAX_ENTRIES + 40):
            font.render(f"{i}", True, (255, 255, 255))
        assert len(font._cache) <= _CachedFont.MAX_ENTRIES

    def test_the_cache_can_be_dropped_with_the_pygame_session(self):
        """A font kept across pygame.quit() is a dangling pointer, and
        rendering with it segfaults -- verified, not assumed."""
        pygame.init()
        from pickhero.ui import scrolling
        first = scrolling._get_font("arial", 14)
        scrolling.clear_font_cache()
        assert scrolling._get_font("arial", 14) is not first

    def test_a_font_still_answers_the_questions_a_font_answers(self):
        """The wrapper delegates, so layout code is untouched by all this."""
        font = self._font()
        assert font.get_height() > 0
        assert font.size("abc")[0] > 0


class TestHowLongAFrameTook:
    """"Slow and stuttering" is a feeling, and a feeling cannot say whether
    the drawing is behind or something arrives in bursts."""

    def _screen(self):
        screen = PlayingScreen(_make_timeline(), config=Config())
        screen._playing = True
        return screen

    def test_frames_are_only_counted_while_the_song_runs(self):
        """A frame spent on a paused picture says nothing about keeping up."""
        screen = self._screen()
        screen._playing = False
        screen.record_frame_ms(50.0)
        assert screen._frame_ms == []

    def test_the_log_reports_the_median_and_the_tail(self):
        import io
        from pickhero.matcher import NoteMatcher
        screen = self._screen()
        screen._matcher = NoteMatcher(_make_timeline())
        for ms in [8.0] * 90 + [40.0] * 10:
            screen.record_frame_ms(ms)
        buffer = io.StringIO()
        screen._write_run_log(buffer)
        text = buffer.getvalue()
        assert "frame_ms_median\t8.0" in text
        assert "frame_ms_worst\t40.0" in text
        assert "frames_over_budget_percent\t10" in text

    def test_a_long_session_does_not_become_a_leak(self):
        screen = self._screen()
        for _ in range(PlayingScreen.FRAME_SAMPLES + 500):
            screen.record_frame_ms(8.0)
        assert len(screen._frame_ms) == PlayingScreen.FRAME_SAMPLES

    def test_nothing_measured_says_so_rather_than_dividing_by_zero(self):
        import io
        from pickhero.matcher import NoteMatcher
        screen = self._screen()
        screen._matcher = NoteMatcher(_make_timeline())
        buffer = io.StringIO()
        screen._write_run_log(buffer)
        assert "frame_ms\t(nothing measured)" in buffer.getvalue()


class TestALogFromHalfARun:
    """D can be pressed at any moment, and most of the time it will be.

    Two thirds of a song not yet reached leaves two thirds of the notes
    PENDING, and hits divided by notes_written then reads as a catastrophic
    score. A number is only readable next to what it is a number of, which is
    the same lesson the stated practice speed taught the analysis.
    """

    def _half_played(self):
        from pickhero.audio.detector import DetectedNote
        from pickhero.audio.input import TimestampedNote
        from pickhero.matcher import NoteMatcher
        notes = [NoteEvent(timestamp_ms=1000.0 * i, midi_note=40, string=6,
                           fret=0, duration_ms=400.0, measure=0)
                 for i in range(1, 11)]
        timeline = _make_timeline(notes=notes)
        screen = PlayingScreen(timeline, config=Config())
        screen._song_key = "half.gp5"
        screen._matcher = NoteMatcher(timeline, timing_window_ms=150.0)
        for i in range(1, 4):
            struck = TimestampedNote(
                note=DetectedNote(40, 82.4, 0.95, "E2", True),
                timestamp_ms=1000.0 * i + 10.0)
            screen._matcher.process_detected_notes([struck], 1000.0 * i + 10.0)
        screen._playback_ms = 3500.0
        return screen

    def _text(self, screen):
        import io
        buffer = io.StringIO()
        screen._write_run_log(buffer)
        return buffer.getvalue()

    def test_it_says_how_many_notes_the_run_actually_reached(self):
        text = self._text(self._half_played())
        assert "notes_written\t10" in text
        assert "notes_reached\t3" in text
        assert "notes_not_reached\t7" in text

    def test_it_says_where_the_playhead_stopped(self):
        text = self._text(self._half_played())
        assert "reached_ms\t3500" in text
        assert "played_to_the_end\tFalse" in text

    def test_a_finished_run_says_that_instead(self):
        screen = self._half_played()
        screen._song_completed = True
        assert "played_to_the_end\tTrue" in self._text(screen)

    def test_a_loop_is_named_because_the_bars_were_played_many_times(self):
        screen = self._half_played()
        screen._loop_enabled = True
        screen._loop_start_ms = 1000.0
        screen._loop_end_ms = 3000.0
        assert "loop\t1000-3000 ms" in self._text(screen)

    def test_no_loop_writes_no_loop_line(self):
        assert "\nloop\t" not in self._text(self._half_played())

    def test_the_screen_says_it_was_only_part_of_the_song(self):
        screen = self._half_played()
        screen._export_run_log()
        assert "up to 4 s" in screen._run_log_note


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
        # The advice is the MANUAL path: with the automatic gate on there is
        # no key to press, and the app does it. See TestTheAutomaticGate.
        screen._auto_gate = False
        return screen

    def test_a_healthy_level_says_nothing(self):
        assert self._screen(peak=-20.0, floor=-75.0)._level_advice() == ""

    def test_nothing_is_claimed_before_anything_was_heard(self):
        assert self._screen(peak=-120.0, floor=0.0)._level_advice() == ""

    def test_a_gate_above_the_playing_says_so(self):
        advice = self._screen(peak=-35.0, floor=-75.0, gate=-40.0)._level_advice()
        assert "X" in advice and "eating your notes" in advice

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
        screen._noise_gate_db = suggested_gate_db(-20.0, -58.0)
        assert screen._level_advice() == ""

    def test_it_names_the_value_to_reach(self):
        """Pressing a key an unknown number of times is not an instruction."""
        advice = self._screen(peak=-35.0, floor=-75.0, gate=-40.0)._level_advice()
        assert f"{suggested_gate_db(-35.0, -75.0):.0f} dB" in advice


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


class TestTheAdviceCannotContradictItself:
    """It could, and the player followed it in circles.

    "Barely above the gate -- press X" and "background noise reaches the gate
    -- press C" name keys that undo each other, and a gate satisfying both
    needed 18 dB between the loudest and quietest recent hop. Under that --
    which is most of a distorted rock song, since the tracked peak decays and
    the floor recovers between strikes -- no gate existed and the panel asked
    for X, then C, then X, for ever. The player pressed C until the gate hit
    the old -20 dB ceiling, where it discarded 40 % of the audio and deleted
    every quiet single note in the song.
    """

    def _advice(self, peak, floor, gate):
        screen = PlayingScreen.__new__(PlayingScreen)
        screen._playing = True
        screen._signal_peak_db = peak
        screen._signal_floor_db = floor
        screen._noise_gate_db = gate
        screen._auto_gate = False
        return screen._level_advice()

    def _follow(self, peak, floor, gate):
        """Press whatever key the advice names, until it stops naming one."""
        pressed = []
        for _ in range(60):
            advice = self._advice(peak, floor, gate)
            if "press X" in advice:
                gate = max(MIN_GATE_DB, gate - 5)
                pressed.append("X")
            elif "press C" in advice:
                gate = min(MAX_GATE_DB, gate + 5)
                pressed.append("C")
            else:
                return "".join(pressed), gate
        return "".join(pressed) + "...", gate

    @pytest.mark.parametrize("peak,floor", [
        (-10.0, -28.0),      # 18 dB of range: the boundary case
        (-14.0, -26.0),      # what a distorted rock signal really looks like
        (-18.0, -30.0),
        (-12.0, -24.0),
        (-30.0, -36.0),      # barely any range at all
    ])
    def test_following_it_always_stops(self, peak, floor):
        pressed, _ = self._follow(peak, floor, -20.0)
        assert not pressed.endswith("...")

    @pytest.mark.parametrize("start", [-90.0, -75.0, -60.0, -50.0])
    def test_and_never_reverses_direction(self, start):
        pressed, _ = self._follow(-14.0, -26.0, start)
        assert "XC" not in pressed and "CX" not in pressed

    def test_a_gate_no_band_can_hold_still_protects_the_notes(self):
        """When the signal has too little range for any gate to satisfy both,
        the two failures are not equals: a gate under the room costs spurious
        onsets that the confidence filter throws away, a gate over the playing
        costs the strikes themselves."""
        lowest, highest = gate_band(-14.0, -26.0)
        assert lowest > highest                      # no gate satisfies both
        _, settled = self._follow(-14.0, -26.0, -20.0)
        assert settled <= highest

    def test_the_ceiling_is_where_the_detector_gives_up(self):
        """There is nothing to be won by gating away audio that could still
        have been read: at a -44 dB loudest hop the pitch is right 83 % of
        the time."""
        assert gate_band(0.0, -120.0)[1] == MAX_GATE_DB

    def test_the_keys_cannot_walk_outside_the_useful_range(self):
        screen = PlayingScreen(_make_timeline(), config=Config())
        for _ in range(40):
            screen.handle_event(
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c, mod=0))
        assert screen._noise_gate_db == MAX_GATE_DB
        for _ in range(40):
            screen.handle_event(
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x, mod=0))
        assert screen._noise_gate_db == MIN_GATE_DB



class TestTheAutomaticGate:
    """The gate sets itself from the room, and only ever comes down.

    Swept over four real play-along takes (tools/sweep_noise_gate.py): every
    gate from -80 dB up to the knee reads exactly the same notes, and the knee
    itself moves 15 dB between takes -- -55 dB on one, -40 dB on another --
    because it follows the interface gain, which the player cannot see. So
    there is no optimum to hunt for, only a ceiling to stay under, and a
    ceiling that moves is exactly what a person cannot be asked to track.
    """

    def _screen(self, gate=-60.0, auto=True):
        config = Config()
        config.audio.noise_gate_db = gate
        config.audio.auto_gate = auto
        screen = PlayingScreen(_make_timeline(), config=config)
        screen._noise_gate_db = gate
        return screen

    def _hear_room(self, screen, db, frames=ROOM_SAMPLES):
        screen._playing = False
        for _ in range(frames):
            screen._track_levels(db)

    def test_the_room_is_what_it_hears_while_the_song_is_not_running(self):
        screen = self._screen()
        self._hear_room(screen, -70.0)
        assert screen.room_db() == pytest.approx(-70.0)

    def test_the_count_in_counts_as_room(self):
        """The song is not running and the player is not meant to be playing:
        the longest clean window a run ever offers."""
        screen = self._screen()
        screen._playing = True
        screen._playback_ms = -2000.0
        for _ in range(ROOM_SAMPLES):
            screen._track_levels(-72.0)
        assert screen.room_db() == pytest.approx(-72.0)

    def test_playing_is_never_mistaken_for_room(self):
        screen = self._screen()
        screen._playing = True
        screen._playback_ms = 100.0
        for _ in range(ROOM_SAMPLES * 2):
            screen._track_levels(-12.0)
        assert screen.room_db() is None

    def test_too_little_heard_is_no_answer(self):
        """Better no gate than one set from four frames of silence."""
        screen = self._screen()
        self._hear_room(screen, -70.0, frames=ROOM_SAMPLES - 1)
        assert screen.room_db() is None

    def test_a_quiet_room_gets_a_gate_under_it(self):
        screen = self._screen(gate=-30.0)
        self._hear_room(screen, -70.0)
        screen._auto_gate_from_room()
        assert screen._noise_gate_db == -64.0

    def test_a_loud_room_does_not_lift_the_gate_past_the_ceiling(self):
        """A gate over the playing costs the strikes themselves; room noise
        costs spurious onsets the confidence filter already throws away."""
        screen = self._screen()
        self._hear_room(screen, -20.0)
        screen._auto_gate_from_room()
        assert screen._noise_gate_db == MAX_GATE_DB

    def test_it_does_nothing_when_switched_off(self):
        screen = self._screen(gate=-30.0, auto=False)
        self._hear_room(screen, -70.0)
        screen._auto_gate_from_room()
        assert screen._noise_gate_db == -30.0

    def test_a_gate_inside_the_playing_is_lowered(self):
        screen = self._screen(gate=-30.0)
        screen._playing = True
        screen._playback_ms = 100.0
        screen._track_levels(-4.0)
        assert screen._noise_gate_db == MAX_GATE_DB

    def test_it_is_never_raised_while_a_song_runs(self):
        """Raising it mid-song can only delete strikes, and a strike that
        never arrives cannot be recovered by anything downstream."""
        screen = self._screen(gate=-75.0)
        screen._playing = True
        screen._playback_ms = 100.0
        for db in (-4.0, -20.0, -60.0, -8.0):
            screen._track_levels(db)
        assert screen._noise_gate_db == -75.0

    def test_it_settles_and_cannot_flap(self):
        """The loudest heard only rises, so the level it demands only rises:
        once satisfied this can never fire again."""
        screen = self._screen(gate=-30.0)
        screen._playing = True
        screen._playback_ms = 100.0
        seen = [screen._noise_gate_db]
        for db in (-4.0, -35.0, -12.0, -50.0, -6.0, -41.0):
            screen._track_levels(db)
            seen.append(screen._noise_gate_db)
        assert seen == sorted(seen, reverse=True)      # never goes back up
        assert len(set(seen)) == 2                     # one change, then still
        assert seen[1:] == [MAX_GATE_DB] * 6           # and it settled at once

    def test_a_signal_too_weak_to_judge_moves_nothing(self):
        """Below the detector's own limit the fault is the interface's gain,
        which _level_advice names and no gate can fix."""
        screen = self._screen(gate=-45.0)
        screen._playing = True
        screen._playback_ms = 100.0
        screen._track_levels(-55.0)
        assert screen._noise_gate_db == -45.0

    def test_pressing_a_key_takes_the_gate_by_hand(self):
        """An automatic that silently undoes the next song what you just set
        is worse than one that was never offered."""
        screen = self._screen()
        screen.handle_event(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x, mod=0))
        assert not screen._auto_gate
        assert not screen._config.audio.auto_gate

    def test_the_room_is_heard_before_the_first_note(self):
        """The input used to be opened when the count-in ENDED, so there was
        never a moment of room to measure and the automatic had nothing to go
        on -- a run log said "(nicht gemessen)" and the gate never moved."""
        screen = self._screen()
        screen._audio_enabled = True
        opened = []
        screen._start_capture_only = lambda: opened.append("open")
        screen._resume_audio = lambda: opened.append("resume")
        screen._playback_ms = 0.0
        screen._count_in_ms = 2400.0
        screen.toggle_play()
        assert screen._playback_ms < 0                  # counting in
        assert opened == ["open"]

    def test_and_the_stream_is_not_reopened_when_the_song_starts(self):
        """A device open on Windows is seconds; the count-in already has one
        and the clocks are agreed by anchoring instead of by restarting."""
        import inspect
        from pickhero.ui.scrolling import PlayingScreen as PS
        source = inspect.getsource(PS._start_audio)
        assert "is_running" in source
        assert source.index("is_running") < source.index("self._audio_capture.start()")

    def test_and_the_advice_says_nothing_while_it_is_automatic(self):
        """It would name a key the app is already pressing for you."""
        screen = self._screen()
        screen._playing = True
        screen._signal_peak_db = -20.0
        screen._signal_floor_db = -58.0
        assert screen._level_advice() == ""

    def test_but_the_gain_is_still_the_players_job(self):
        screen = self._screen()
        screen._playing = True
        screen._signal_peak_db = -2.0
        screen._signal_floor_db = -70.0
        assert "Too loud" in screen._level_advice()



class TestSeekingInSteps:
    """A beat places a loop marker; it does not reach the chorus.

    At 273 ms a beat, four minutes of song is nine hundred presses, and key
    repeat is 40 ms -- half a minute of holding the key while the picture
    scrolls past. So the same ladder the backing-track offset uses.
    """

    def _screen(self):
        notes = [NoteEvent(timestamp_ms=t, duration_ms=200.0, midi_note=40,
                           string=6, fret=0)
                 for t in range(0, 120_000, 500)]
        meta = SongMetadata(title="x", artist="y", tempo=120)
        measures = [MeasureInfo(index=i, start_ms=i * 2000.0,
                                end_ms=(i + 1) * 2000.0) for i in range(60)]
        timeline = Timeline(notes, meta, measures=measures)
        return PlayingScreen(timeline, config=Config())

    def _press(self, screen, key, mod=0):
        screen.handle_event(
            pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod))
        return screen.position_ms()

    def test_a_plain_arrow_still_moves_one_beat(self):
        screen = self._screen()
        screen.seek(30_000.0)
        assert self._press(screen, pygame.K_RIGHT) == pytest.approx(
            30_000.0 + screen._ms_per_beat)

    def test_shift_lands_on_the_bar_line(self):
        """Snapped, not a fixed number of beats: it stays on the bars through
        a time-signature change and lands where the tab is drawn."""
        screen = self._screen()
        screen.seek(30_500.0)
        assert self._press(screen, pygame.K_RIGHT, pygame.KMOD_SHIFT) == 32_000.0

    def test_and_back_reaches_the_bar_before_this_one(self):
        screen = self._screen()
        screen.seek(32_000.0)
        assert self._press(screen, pygame.K_LEFT, pygame.KMOD_SHIFT) == 30_000.0

    def test_shift_back_from_just_after_a_bar_line_does_not_stand_still(self):
        screen = self._screen()
        screen.seek(32_010.0)
        assert self._press(screen, pygame.K_LEFT, pygame.KMOD_SHIFT) == 30_000.0

    def test_ctrl_moves_half_a_minute(self):
        screen = self._screen()
        screen.seek(30_000.0)
        assert self._press(screen, pygame.K_RIGHT, pygame.KMOD_CTRL) == 60_000.0

    def test_it_cannot_walk_off_either_end(self):
        screen = self._screen()
        screen.seek(0.0)
        assert self._press(screen, pygame.K_LEFT, pygame.KMOD_CTRL) == 0.0
        screen.seek(screen._timeline.duration_ms)
        assert (self._press(screen, pygame.K_RIGHT, pygame.KMOD_CTRL)
                <= screen._timeline.duration_ms)

    def test_a_song_with_no_bars_still_seeks(self):
        """A tab that parsed without measure info must not make the key dead."""
        notes = [NoteEvent(timestamp_ms=0.0, duration_ms=20_000.0,
                           midi_note=40, string=6, fret=0)]
        screen = PlayingScreen(
            Timeline(notes, SongMetadata(title="x", tempo=120)),
            config=Config())
        screen.seek(1_000.0)
        moved = self._press(screen, pygame.K_RIGHT, pygame.KMOD_SHIFT)
        assert moved > 1_000.0

    def test_the_footer_names_all_three(self):
        """A key that is bound but undocumented is a key nobody finds."""
        footer = " ".join(self._screen()._footer_lines())
        assert "Shift: bar" in footer and "Ctrl: 30s" in footer


class TestChangingTrackKeepsThePlace:
    """The tracks of one file share a clock: bar 40 of the rhythm guitar is
    bar 40 of the lead. Somebody comparing two versions of a passage changes
    track precisely BECAUSE they are at that passage."""

    def test_the_screen_can_say_where_it_is(self):
        notes = [NoteEvent(timestamp_ms=t, duration_ms=200.0, midi_note=40,
                           string=6, fret=0) for t in range(0, 60_000, 500)]
        screen = PlayingScreen(
            Timeline(notes, SongMetadata(title="x", tempo=120)),
            config=Config())
        screen.seek(12_345.0)
        assert screen.position_ms() == pytest.approx(12_345.0)

    def test_the_app_carries_it_across_the_reload(self):
        import inspect
        from pickhero.ui.app import App
        source = inspect.getsource(App._handle_playing_event)
        assert "position_ms()" in source and "resume_at_ms" in source
        assert "resume_at_ms" in inspect.signature(App._load_song).parameters

    def test_and_clamps_it_to_a_shorter_track(self):
        import inspect
        from pickhero.ui.app import App
        source = inspect.getsource(App._load_song)
        assert "min(resume_at_ms" in source

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
