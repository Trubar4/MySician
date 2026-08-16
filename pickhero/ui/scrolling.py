"""Scrolling note display for the playing screen.

Renders 6 string lanes with notes scrolling right-to-left, synchronized
to a playback clock. Optionally captures audio and shows hit/miss feedback.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pygame

from pickhero.audio.midi_playback import BackingTrack, MidiPlayer
from pickhero.config import MAX_LATENCY_OFFSET_MS, Config
from pickhero.matcher import NoteMatcher
from pickhero.progress import ProgressTracker
from pickhero.tabs.timeline import NoteEvent, Timeline
from pickhero.audio.note_utils import freq_to_cents_deviation, midi_to_name
from pickhero.ui.colors import (
    STRING_COLORS,
    cycle_theme,
    dimmed,
    get_theme,
    lightened,
)
from pickhero.ui.feedback import FeedbackRenderer

# Layout constants
LANE_TOP_MARGIN = 80
LANE_BOTTOM_MARGIN = 40
MIN_NOTE_WIDTH_PX = 20
NOTE_HEIGHT_FRACTION = 0.85
NOTE_CORNER_RADIUS = 4

# The six lanes form a fretboard band rather than filling the window: a lane
# stretched to 100 px reads as a spreadsheet row, not a string. Capped as a
# fraction of window height so it still scales with the display.
MAX_LANE_HEIGHT_FRACTION = 0.072

# Wound strings are visibly thicker than plain ones; drawing them at one
# weight loses the strongest cue for which lane is which. Index 0 = high e.
STRING_THICKNESS = (1, 1, 2, 2, 3, 4)

# Gap left between a sustain and the next note, as a fraction of note height.
# A capsule is drawn from the head's left edge to one radius before the next
# note's centre, so back-to-back notes abut instead of merging into a ribbon —
# without this, a run of eighths renders as one unbroken bar.
SUSTAIN_GAP_FRACTION = 0.18

# -- Bends and slides ------------------------------------------------------
# How far a full bend (two semitones) lifts the curve, in lane heights. The
# arc has to leave its own lane to read as a rise at all, so it overlaps the
# lane above -- as it does in Yousician. Not a whole lane, though: landing
# exactly on the neighbouring string's centre makes the arc look like a note
# over there. Deeper bends are capped for the same reason.
BEND_RISE_LANES = 0.72
BEND_MAX_RISE_LANES = 1.15
# A bend on a staccato note still needs somewhere to draw the arc, as a
# multiple of head width.
BEND_MIN_WIDTH_HEADS = 1.8
# Segments drawn between two written bend points, to smooth the pull.
BEND_CURVE_STEPS = 8
# Slides slant within their own lane: the target is on the same string, so
# there is no other axis to show direction on. Fraction of the head radius.
SLIDE_SLANT_FRACTION = 0.7
# Longest connector drawn, in head widths. A slide across two bars would
# otherwise stretch its slant out until it reads as a horizontal line.
SLIDE_SPAN_HEADS = 2.2
SLIDE_WIDTH_PX = 5
# A sliding note gives up part of its sustain so the connector has somewhere
# to be. Back to back notes otherwise leave a gap of a few pixels, and a
# connector squeezed into that is invisible however it is drawn.
SLIDE_GAP_FRACTION = 0.85
# Length of the stub drawn for a slide that has no note at the other end,
# as a multiple of head width.
SLIDE_STUB_HEADS = 0.7

# Left margin for notes that already passed the hit zone (ms)
LEFT_MARGIN_MS = 2000
# Right margin for notes not yet visible (ms)
RIGHT_MARGIN_MS = 500

# Difficulty filter: fret limit cycle values
FRET_LIMITS = [24, 12, 7, 5, 3]

# Scroll pacing. A song scrolls at ONE speed throughout, fast enough that its
# tightest passage still has room for full-size notes. Notes therefore never
# change size or width while playing — a fast song simply flies past. Varying
# the speed during a song was the obvious idea and the wrong one: easing the
# window visibly stretched and squeezed every note on screen, which is exactly
# what a player notices and what Yousician never does.
BASE_VISIBLE_WINDOW_MS = 8000.0
# Bounds on the derived window. The lower one keeps a minimum of lookahead;
# the upper one stops a sparse song from crawling.
MIN_VISIBLE_WINDOW_MS = 1500.0

# The window is set from a low percentile of the note spacing rather than its
# minimum. Real tabs contain the odd near-simultaneous pair — grace notes,
# ties, sloppy transcription — and letting one of those decide the pacing
# shrinks every note in the song for the sake of two. Those few overlap
# slightly instead, which is the cheaper price by far.
SPACING_PERCENTILE = 10.0
# How far the manual speed control may go, and its step.
SCROLL_FACTOR_RANGE = (0.4, 2.5)
SCROLL_FACTOR_STEP = 0.1

# Hit-window presets cycled by G. Strikes scatter by more than the default
# window even on a metronomic exercise, so how strict this should be is a
# choice about how the app should feel, not a constant.
TIMING_WINDOW_PRESETS = (100.0, 150.0, 200.0, 250.0)

# How far the backing track can be shifted against the notes, and its step.
MAX_BACKING_OFFSET_MS = 400.0
BACKING_OFFSET_STEP_MS = 10.0

# Auto-sync confidence. Scatter does not invalidate the median — a player is
# simply not a metronome — it only means more strikes are needed before the
# median is trustworthy. Refuse outright only when the scatter is so wide that
# no systematic offset is visible in it at all.
AUTO_SYNC_MIN_SAMPLES = 8
AUTO_SYNC_WIDE_SPREAD_MS = 40.0
AUTO_SYNC_WIDE_MIN_SAMPLES = 24
AUTO_SYNC_HOPELESS_SPREAD_MS = 150.0


def _get_font(name: str, size: int) -> pygame.font.Font:
    """Try to load a system font with fallbacks."""
    for family in (name, "Courier New", "monospace"):
        font = pygame.font.SysFont(family, size)
        if font:
            return font
    return pygame.font.Font(None, size)


def format_time(ms: float) -> str:
    """Format milliseconds as M:SS."""
    total_seconds = max(0, int(ms / 1000))
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"


@dataclass
class _Layout:
    """Computed layout dimensions for current surface size."""

    screen_w: int
    screen_h: int
    lane_height: float
    note_h: float
    hit_zone_x: float
    usable_width: float
    pixels_per_ms: float
    visible_window_ms: float
    # Top of the fretboard band. Not the same as LANE_TOP_MARGIN: the band is
    # compact and centred in the area between the margins.
    lane_top: float = float(LANE_TOP_MARGIN)


class PlayingScreen:
    """Scrolling tab display with playback clock and optional audio matching."""

    def __init__(self, timeline: Timeline, visible_beats: int = 4,
                 hit_zone_fraction: float = 0.20, config: Config | None = None,
                 backing_track: BackingTrack | None = None,
                 progress_tracker: ProgressTracker | None = None,
                 song_key: str = ""):
        self._timeline = timeline
        self._visible_beats = visible_beats
        self._hit_zone_fraction = hit_zone_fraction
        self._config = config or Config()

        self._tempo_factor = max(0.5, min(1.0, self._config.tempo_factor))

        self._playback_ms: float = 0.0
        self._playing = False
        self._last_tick: float | None = None

        tempo = max(1, self._timeline.metadata.tempo)
        self._ms_per_beat = 60_000 / tempo
        self._visible_window_ms = BASE_VISIBLE_WINDOW_MS
        self._last_layout: _Layout | None = None
        self._scroll_speed_signature: tuple | None = None
        # One head size for the whole song, set with the scroll speed
        self._head_px: float | None = None

        # Count-in state
        count_in_beats = max(0, self._config.count_in_beats)
        self._count_in_ms = count_in_beats * self._ms_per_beat
        self._last_count_in_beat: int = -1

        # Audio matching
        self._audio_capture = None  # AudioCapture, created on demand
        self._matcher: NoteMatcher | None = None
        self._feedback = FeedbackRenderer()
        self._audio_enabled = True
        self._noise_gate_db: float = self._config.audio.noise_gate_db

        # Loop state
        self._loop_start_ms: float | None = None
        self._loop_end_ms: float | None = None
        self._loop_enabled: bool = False

        # Progress tracking
        self._progress_tracker = progress_tracker
        self._song_key = song_key
        self._song_completed = False
        self._is_new_best = False
        self._recommendations: list[str] = []

        # MIDI backing track
        self._midi_player: MidiPlayer | None = None
        self._backing_muted = not self._config.backing_track_enabled
        if backing_track is not None and len(backing_track) > 0:
            self._init_midi_player(backing_track)

        # Difficulty filter
        self._max_fret: int = self._config.max_fret
        self._active_strings: list[bool] = list(self._config.active_strings)

        # Signal level meter
        self._signal_db: float = -120.0
        self._signal_db_smooth: float = -120.0

        # Tuner display
        self._tuner_freq: float = 0.0
        self._tuner_confidence: float = 0.0
        self._tuner_freq_smooth: float = 0.0
        self._tuner_displayed_note: int = -1
        self._tuner_note_stable_frames: int = 0

        # Chord partial credit mode
        self._chord_partial_credit: bool = self._config.chord_partial_credit

        # Fret-number fonts, keyed by pixel size
        self._fret_fonts: dict[int, pygame.font.Font] = {}

        # Help overlay
        self._show_help: bool = False

        # Track picker: [(index, label)], filled in by the app on load
        self._track_options: list[tuple[int, str]] = []
        self._track_index: int | None = None
        self._track_menu_open: bool = False
        self._track_menu_cursor: int = 0

        # Wait mode
        self._wait_mode: bool = self._config.wait_mode
        self._wait_mode_frozen: bool = False

    def _note_passes_filter(self, note: NoteEvent) -> bool:
        """Check if a note passes the difficulty filter."""
        if note.fret > self._max_fret:
            return False
        if not self._active_strings[note.string - 1]:
            return False
        return True

    def _is_filter_active(self) -> bool:
        """Check if any difficulty filter is active."""
        return self._max_fret < 24 or not all(self._active_strings)

    def toggle_play(self) -> None:
        """Toggle play/pause. Restarts with count-in if at beginning or past end."""
        if self._playback_ms >= self._timeline.duration_ms and not self._playing:
            # Restart from beginning with count-in
            self._playback_ms = -self._count_in_ms if self._count_in_ms > 0 else 0.0
            self._last_count_in_beat = -1
            self._song_completed = False
            self._is_new_best = False
            self._weakest_sections = []
            self._recommendations = []
            if self._matcher:
                self._matcher.reset()
            self._feedback.reset()
        elif self._playback_ms == 0.0 and not self._playing and self._count_in_ms > 0:
            # Starting from the very beginning — add count-in
            self._playback_ms = -self._count_in_ms
            self._last_count_in_beat = -1
            self._song_completed = False
            self._is_new_best = False
            self._weakest_sections = []
            self._recommendations = []
        self._playing = not self._playing
        if self._playing:
            self._last_tick = time.perf_counter()
            # Only start audio capture when past count-in
            if self._audio_enabled and self._playback_ms >= 0:
                self._start_audio()
            if self._midi_player is not None:
                if self._playback_ms >= 0:
                    self._midi_player.seek(self._backing_ms(self._playback_ms))
        else:
            self._last_tick = None
            self._stop_audio()
            if self._midi_player is not None:
                self._midi_player.pause()

    def seek(self, ms: float) -> None:
        """Seek to an absolute position in ms, clamped to [0, duration]."""
        self._playback_ms = max(0.0, min(ms, self._timeline.duration_ms))
        if self._matcher:
            self._matcher.reset()
        self._feedback.reset()
        if self._midi_player is not None:
            self._midi_player.seek(self._backing_ms(self._playback_ms))
        # Restart audio with new offset if active
        if self._audio_enabled and self._playing:
            self._stop_audio()
            self._start_audio()

    def is_playing(self) -> bool:
        return self._playing

    def set_tempo_factor(self, factor: float) -> None:
        """Set tempo scaling factor, clamped to [0.5, 1.0] and rounded to nearest 0.05."""
        factor = max(0.5, min(1.0, factor))
        factor = round(factor * 20) / 20  # round to nearest 0.05
        self._tempo_factor = factor
        self._config.tempo_factor = factor
        if self._matcher:
            self._matcher.reset()
        self._feedback.reset()

    def set_noise_gate_db(self, db: float) -> None:
        """Set noise gate threshold, clamped to [-80, -20] and rounded to int."""
        db = max(-80, min(-20, round(db)))
        self._noise_gate_db = db
        self._config.audio.noise_gate_db = db
        if self._audio_capture is not None:
            self._audio_capture.set_noise_gate_db(db)
        self._config.save()

    def update(self) -> None:
        """Advance playback clock by real elapsed time."""
        # Update signal level meter and tuner even when paused (so user can verify signal)
        if self._audio_capture is not None:
            raw_db = self._audio_capture.get_signal_db()
            self._signal_db = raw_db
            self._signal_db_smooth = self._signal_db_smooth * 0.7 + raw_db * 0.3
            freq, conf = self._audio_capture.get_tuner_data()
            self._tuner_freq = freq
            self._tuner_confidence = conf
            if freq > 0 and conf > 0.5:
                # Frequency jump guard: ignore wild jumps (> 50% change)
                if (self._tuner_freq_smooth > 0
                        and abs(freq - self._tuner_freq_smooth) / self._tuner_freq_smooth > 0.5):
                    # Wild jump — use very low alpha to dampen
                    alpha = 0.02
                else:
                    # Adaptive EMA: high confidence → faster, low → slower
                    alpha = 0.10 if conf > 0.8 else 0.03
                if self._tuner_freq_smooth > 0:
                    self._tuner_freq_smooth = self._tuner_freq_smooth * (1 - alpha) + freq * alpha
                else:
                    self._tuner_freq_smooth = freq
                # Note hysteresis: only change displayed note after 8 stable frames (~130ms)
                from pickhero.audio.note_utils import freq_to_midi
                candidate_note = freq_to_midi(self._tuner_freq_smooth)
                if candidate_note != self._tuner_displayed_note:
                    self._tuner_note_stable_frames += 1
                    if self._tuner_note_stable_frames >= 8:
                        self._tuner_displayed_note = candidate_note
                        self._tuner_note_stable_frames = 0
                else:
                    self._tuner_note_stable_frames = 0
            else:
                # Slow decay instead of instant reset
                self._tuner_freq_smooth *= 0.92
                if self._tuner_freq_smooth < 20.0:
                    self._tuner_freq_smooth = 0.0
                    self._tuner_displayed_note = -1
                    self._tuner_note_stable_frames = 0

        if not self._playing:
            return

        now = time.perf_counter()
        prev_ms = self._playback_ms
        if self._last_tick is not None:
            elapsed_ms = (now - self._last_tick) * 1000.0 * self._tempo_factor
            self._playback_ms += elapsed_ms
        self._last_tick = now

        # Wait mode: freeze if there are pending notes the player hasn't hit yet
        if (self._wait_mode and self._audio_enabled
                and self._playback_ms >= 0 and self._matcher is not None):
            if self._matcher.has_pending_notes_at(self._playback_ms):
                self._playback_ms = prev_ms
                self._last_tick = now
                self._wait_mode_frozen = True
                if self._midi_player is not None and not self._backing_muted:
                    self._midi_player.pause()
            elif self._wait_mode_frozen:
                self._wait_mode_frozen = False
                if self._midi_player is not None and not self._backing_muted:
                    self._midi_player.seek(self._backing_ms(self._playback_ms))

        # Count-in: play metronome clicks and start audio/midi when crossing 0
        if prev_ms < 0:
            # Play count-in clicks at beat boundaries
            if self._count_in_ms > 0 and self._midi_player is not None:
                beat_index = int((self._count_in_ms + self._playback_ms) / self._ms_per_beat)
                if beat_index > self._last_count_in_beat:
                    self._midi_player.play_click(100)
                    self._last_count_in_beat = beat_index

            # Crossed from negative to non-negative — song starts
            if self._playback_ms >= 0:
                if self._audio_enabled:
                    self._start_audio()
                if self._midi_player is not None:
                    self._midi_player.seek(0)

        # Process audio matching (only during actual song, not count-in)
        if (self._playback_ms >= 0
                and self._audio_enabled
                and self._audio_capture is not None
                and self._matcher is not None):
            detected = self._audio_capture.get_notes()
            for d in detected:
                d.timestamp_ms *= self._tempo_factor
            # While frozen in wait mode, pin detected timestamps to the frozen
            # playback position so matching hits the notes at the hit zone,
            # not future notes that drift ahead as real time passes.
            if self._wait_mode_frozen and detected:
                pinned_ts = self._playback_ms - self._matcher.audio_offset_ms
                for d in detected:
                    d.timestamp_ms = pinned_ts
            # Pinned timestamps carry no latency information
            self._matcher.record_timing_samples = not self._wait_mode_frozen
            results = self._matcher.process_detected_notes(detected, self._playback_ms)
            # Per-string chord verdicts arrive ~380 ms after their strike, once
            # enough audio exists to tell a semitone apart. They can only
            # downgrade strings already credited by the pitch path above.
            results.extend(self._matcher.process_strike_windows(
                self._audio_capture.get_strike_windows()
            ))
            self._feedback.add_results(results, self._playback_ms)
            self._feedback.cleanup(self._playback_ms)

        # Advance MIDI backing track (only during actual song)
        if self._playback_ms >= 0 and self._midi_player is not None:
            self._midi_player.update(self._backing_ms(self._playback_ms))

        # Loop check — jump back to start marker when reaching end marker
        # (no count-in on loop)
        if (self._loop_enabled and self._loop_end_ms is not None
                and self._loop_start_ms is not None
                and self._playback_ms >= self._loop_end_ms):
            if self._midi_player is not None:
                self._midi_player.pause()
            self._playback_ms = self._loop_start_ms
            self._last_tick = time.perf_counter()
            if self._matcher:
                self._matcher.reset()
            self._feedback.reset()
            if self._midi_player is not None:
                self._midi_player.seek(self._backing_ms(self._loop_start_ms))
            if self._audio_enabled and self._playing:
                self._stop_audio()
                self._start_audio()
            return

        if self._playback_ms >= self._timeline.duration_ms:
            self._playback_ms = self._timeline.duration_ms
            self._playing = False
            self._last_tick = None
            if self._midi_player is not None:
                self._midi_player.pause()
            self._stop_audio()

            if not self._song_completed:
                if (self._audio_enabled
                        and self._matcher is not None
                        and self._progress_tracker is not None
                        and self._song_key):
                    # Audio-scored completion
                    stats = self._matcher.get_statistics()
                    if stats["total"] > 0:
                        weakest = self._matcher.get_weakest_sections()
                        self._is_new_best, self._recommendations = (
                            self._progress_tracker.record_detailed_result(
                                self._song_key, stats,
                                weakest, self._tempo_factor,
                            )
                        )
                        self._weakest_sections = weakest
                        self._song_completed = True
                elif not self._audio_enabled:
                    # Auto-scroll (passive) completion
                    self._weakest_sections = []
                    self._song_completed = True

    def handle_event(self, event: pygame.event.Event):
        """Handle input.

        Returns 'menu' to go back, ('select_track', index) when a track was
        picked, else None.
        """
        if event.type != pygame.KEYDOWN:
            return None

        # While the picker is open it owns the keyboard, so arrow keys move
        # the selection instead of seeking through the song
        if self._track_menu_open:
            return self._handle_track_menu_event(event)

        if event.key == pygame.K_SPACE:
            self.toggle_play()
        elif event.key == pygame.K_ESCAPE:
            self.stop_audio()
            return "menu"
        elif event.key == pygame.K_LEFT:
            self.seek(self._playback_ms - self._ms_per_beat)
        elif event.key == pygame.K_RIGHT:
            self.seek(self._playback_ms + self._ms_per_beat)
        elif event.key == pygame.K_HOME:
            self.seek(0)
        elif event.key == pygame.K_a:
            self._toggle_audio()
        elif event.key == pygame.K_PAGEDOWN:
            self.set_tempo_factor(self._tempo_factor - 0.05)
        elif event.key == pygame.K_PAGEUP:
            self.set_tempo_factor(self._tempo_factor + 0.05)
        elif event.key == pygame.K_i:
            self._set_loop_start(self._playback_ms)
        elif event.key == pygame.K_o:
            self._set_loop_end(self._playback_ms)
        elif event.key == pygame.K_p:
            self._toggle_loop()
        elif event.key == pygame.K_b:
            self._toggle_backing()
        elif event.key == pygame.K_x:
            self.set_noise_gate_db(self._noise_gate_db - 5)
        elif event.key == pygame.K_c:
            self.set_noise_gate_db(self._noise_gate_db + 5)
        elif event.key == pygame.K_t:
            self._cycle_theme()
        elif event.key == pygame.K_f:
            self._cycle_fret_limit()
        elif event.key == pygame.K_F1:
            self._toggle_string(1)
        elif event.key == pygame.K_F2:
            self._toggle_string(2)
        elif event.key == pygame.K_F3:
            self._toggle_string(3)
        elif event.key == pygame.K_F4:
            self._toggle_string(4)
        elif event.key == pygame.K_F5:
            self._toggle_string(5)
        elif event.key == pygame.K_F6:
            self._toggle_string(6)
        elif event.key == pygame.K_v:
            self._toggle_chord_mode()
        elif event.key == pygame.K_j:
            self._toggle_chord_verify()
        elif event.key == pygame.K_g:
            self._cycle_timing_window()
        elif event.key == pygame.K_TAB:
            self._open_track_menu()
        elif event.key == pygame.K_n:
            self._adjust_backing_offset(-BACKING_OFFSET_STEP_MS)
        elif event.key == pygame.K_m:
            self._adjust_backing_offset(BACKING_OFFSET_STEP_MS)
        elif event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
            self._adjust_scroll_factor(SCROLL_FACTOR_STEP)
        elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self._adjust_scroll_factor(-SCROLL_FACTOR_STEP)
        elif event.key == pygame.K_l:
            self._loop_weakest_section()
        elif event.key == pygame.K_h:
            self._show_help = not self._show_help
        elif event.key == pygame.K_w:
            self._toggle_wait_mode()
        elif event.key == pygame.K_k:
            if event.mod & pygame.KMOD_SHIFT:
                self._reset_latency_offset()
            else:
                self._auto_sync_timing()
        elif event.key == pygame.K_COMMA:
            self._adjust_latency_offset(-10.0)
        elif event.key == pygame.K_PERIOD:
            self._adjust_latency_offset(10.0)

        return None

    def render(self, surface: pygame.Surface) -> None:
        """Draw the full playing screen."""
        t = get_theme()
        layout = self._layout(surface)
        if (self._last_layout is None
                or self._scroll_speed_signature != self._filter_signature()):
            # Needs a layout to know note size and width, and changes the
            # window it was built from — so redo it rather than draw one
            # frame at the stale speed.
            self._recompute_scroll_speed(layout)
            layout = self._layout(surface)
        self._last_layout = layout

        surface.fill(t.bg)
        self._draw_lanes(surface, layout)
        self._draw_loop_region(surface, layout)
        self._draw_hit_zone(surface, layout)
        self._draw_notes(surface, layout)
        self._draw_hud(surface, layout)

        if self._show_help:
            self._draw_help_overlay(surface, layout)
        if self._track_menu_open:
            self._draw_track_menu(surface)

    # -- Pure math helpers (testable without display) --

    def _layout(self, surface: pygame.Surface) -> _Layout:
        """Compute layout from current surface dimensions."""
        w, h = surface.get_size()
        lane_area = h - LANE_TOP_MARGIN - LANE_BOTTOM_MARGIN
        # Compact fretboard band, centred in the available area, instead of
        # six lanes stretched over the whole window
        lane_height = min(lane_area / 6, h * MAX_LANE_HEIGHT_FRACTION)
        lane_top = LANE_TOP_MARGIN + max(0.0, lane_area - 6 * lane_height) / 2
        note_h = lane_height * NOTE_HEIGHT_FRACTION
        hit_zone_x = w * self._hit_zone_fraction
        usable_width = w - hit_zone_x
        pixels_per_ms = usable_width / self._visible_window_ms if self._visible_window_ms > 0 else 1.0
        return _Layout(
            screen_w=w,
            screen_h=h,
            lane_height=lane_height,
            note_h=note_h,
            hit_zone_x=hit_zone_x,
            usable_width=usable_width,
            pixels_per_ms=pixels_per_ms,
            visible_window_ms=self._visible_window_ms,
            lane_top=lane_top,
        )

    @staticmethod
    def note_x(note_timestamp_ms: float, playback_ms: float,
               hit_zone_x: float, pixels_per_ms: float) -> float:
        """Calculate the x position of a note."""
        return hit_zone_x + (note_timestamp_ms - playback_ms) * pixels_per_ms

    @staticmethod
    def note_width(duration_ms: float, pixels_per_ms: float) -> float:
        """Calculate note rectangle width, enforcing minimum."""
        return max(duration_ms * pixels_per_ms, MIN_NOTE_WIDTH_PX)

    def _fret_font(self, radius: float) -> pygame.font.Font:
        """Font sized to a note head, cached: building one per note per frame
        is far too slow, and heads now vary in size within a single frame."""
        size = max(9, int(radius * 1.1))
        font = self._fret_fonts.get(size)
        if font is None:
            font = _get_font("consolas", size)
            self._fret_fonts[size] = font
        return font

    def _tightest_spacing_ms(self, from_ms: float, to_ms: float) -> float | None:
        """Smallest gap between consecutive notes on any one string in a range.

        Only same-string gaps count: notes in different lanes never crowd
        each other, and a six-string chord is one strum, not congestion.
        """
        notes = [n for n in self._timeline.get_notes_in_range(from_ms, to_ms)
                 if self._note_passes_filter(n)]
        gaps = self._neighbour_gaps(notes)
        return min(gaps.values()) if gaps else None

    def _spacing_percentile(self, percentile: float) -> float | None:
        """How often something happens in this song, in ms, near its densest.

        Measured between distinct onset times across ALL strings, not within
        each string: an arpeggio rotating over three strings looks roomy per
        string while the screen is in fact busy, and it is the screen that
        has to stay readable. Notes struck together are one event, so a
        six-string chord counts once rather than as five gaps of zero.

        A percentile rather than the minimum, so a couple of freak-close
        notes cannot set the pacing for everything else.
        """
        onsets = sorted({n.timestamp_ms for n in self._timeline.notes
                         if self._note_passes_filter(n)})
        if len(onsets) < 2:
            return None
        gaps = sorted(b - a for a, b in zip(onsets, onsets[1:]))
        idx = min(len(gaps) - 1, int(len(gaps) * percentile / 100.0))
        return gaps[idx]

    def _recompute_scroll_speed(self, layout: _Layout | None = None) -> None:
        """Pick this song's one scroll speed and one note size.

        Both are set per song rather than per frame: a speed that moves while
        the song plays makes every note on screen visibly stretch and squeeze,
        and a size that varies note by note is the same problem in miniature.

        Speed comes first. A tab can only be read so fast no matter how dense
        the music is, so once the tightest passage would push past that limit
        the notes shrink toward the smallest head that still shows a two-digit
        fret, instead of the tab scrolling faster and faster.
        """
        layout = layout or self._last_layout
        if layout is None or layout.usable_width <= 0:
            return

        head = layout.note_h
        spacing = self._spacing_percentile(SPACING_PERCENTILE)

        # Notes are always full size. The window follows from that: however
        # much time fits on screen once every note has its room is how much
        # gets shown. A dense song therefore shows less of itself at once
        # rather than drawing itself smaller.
        window = BASE_VISIBLE_WINDOW_MS
        if spacing and spacing > 0:
            window = spacing * layout.usable_width / (head * (1.0 + SUSTAIN_GAP_FRACTION))

        # Largest window in which every note still gets its full size. Slowing
        # past it would fit more time on screen at the cost of note size, and
        # notes changing size is the one thing this display must not do -- so
        # the trim stops there instead. Speeding up is always allowed: it only
        # ever gives the notes more room.
        fit_window = window
        window = max(MIN_VISIBLE_WINDOW_MS, min(BASE_VISIBLE_WINDOW_MS, window))
        window = window / self._scroll_factor()
        window = max(MIN_VISIBLE_WINDOW_MS, min(fit_window, window))

        self._visible_window_ms = window
        self._head_px = head
        self._scroll_speed_signature = self._filter_signature()

    def _backing_ms(self, playback_ms: float) -> float:
        """Playback position as the backing track should hear it.

        A positive offset delays the backing, so it is subtracted from the
        position the player feeds it.
        """
        return playback_ms - self._backing_offset()

    def _backing_offset(self) -> float:
        """This song's offset, falling back to the global default."""
        getter = getattr(self._config, "backing_offset_for", None)
        if getter is None:
            return getattr(self._config, "backing_offset_ms", 0.0)
        return getter(self._song_key)

    def _adjust_backing_offset(self, delta_ms: float) -> None:
        """Shift the backing against the notes (N earlier, M later).

        Stored per song: how far the backing lags depends on how much the
        arrangement asks of the synth, so a value dialled in on one song is
        wrong on the next.
        """
        new = max(-MAX_BACKING_OFFSET_MS,
                  min(MAX_BACKING_OFFSET_MS, self._backing_offset() + delta_ms))
        setter = getattr(self._config, "set_backing_offset_for", None)
        if setter is not None:
            setter(self._song_key, new)
        else:
            self._config.backing_offset_ms = new
        self._config.save()
        if self._midi_player is not None:
            self._midi_player.seek(self._backing_ms(self._playback_ms))

    def set_track_options(self, options: list[tuple[int, str]],
                          current: int | None) -> None:
        """Tell the screen which tracks exist, so it can offer them."""
        self._track_options = list(options)
        self._track_index = current
        self._track_menu_cursor = next(
            (i for i, (idx, _) in enumerate(self._track_options) if idx == current), 0
        )

    def _open_track_menu(self) -> None:
        if len(self._track_options) > 1:
            self._track_menu_open = not self._track_menu_open

    def _handle_track_menu_event(self, event: pygame.event.Event):
        """Arrow keys and Enter while the picker is open. Returns a result or None."""
        if event.type != pygame.KEYDOWN:
            return None
        count = len(self._track_options)
        if event.key in (pygame.K_ESCAPE, pygame.K_TAB):
            self._track_menu_open = False
        elif event.key in (pygame.K_UP, pygame.K_LEFT):
            self._track_menu_cursor = (self._track_menu_cursor - 1) % count
        elif event.key in (pygame.K_DOWN, pygame.K_RIGHT):
            self._track_menu_cursor = (self._track_menu_cursor + 1) % count
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            self._track_menu_open = False
            chosen = self._track_options[self._track_menu_cursor][0]
            if chosen != self._track_index:
                return ("select_track", chosen)
        return None

    def _draw_track_menu(self, surface: pygame.Surface) -> None:
        t = get_theme()
        font = _get_font("arial", 18)
        title = _get_font("arial", 15)
        rows = [label for _, label in self._track_options]
        width = max([font.size(r)[0] for r in rows] + [240]) + 40
        row_h = 28
        height = row_h * len(rows) + 52
        x = int(surface.get_width() / 2 - width / 2)
        y = int(surface.get_height() / 2 - height / 2)

        pygame.draw.rect(surface, t.menu_bg, (x, y, width, height))
        pygame.draw.rect(surface, t.hud_accent, (x, y, width, height), 2)
        surface.blit(title.render("Track  (up/down, Enter, Esc)", True, t.hud_text),
                     (x + 16, y + 12))
        for i, (idx, label) in enumerate(self._track_options):
            row_y = y + 40 + i * row_h
            if i == self._track_menu_cursor:
                pygame.draw.rect(surface, t.menu_selected_bg,
                                 (x + 8, row_y - 2, width - 16, row_h))
            mark = "*" if idx == self._track_index else " "
            surface.blit(font.render(f"{mark} {label}", True, t.hud_text),
                         (x + 16, row_y))

    def _cycle_timing_window(self) -> None:
        """Step through how much timing slack a hit gets (G)."""
        current = self._config.timing_window_ms
        nearest = min(TIMING_WINDOW_PRESETS, key=lambda p: abs(p - current))
        idx = (TIMING_WINDOW_PRESETS.index(nearest) + 1) % len(TIMING_WINDOW_PRESETS)
        self._config.timing_window_ms = TIMING_WINDOW_PRESETS[idx]
        self._config.save()
        if self._matcher is not None:
            self._matcher.timing_window_ms = self._config.timing_window_ms

    def _scroll_factor(self) -> float:
        lo, hi = SCROLL_FACTOR_RANGE
        return max(lo, min(hi, getattr(self._config, "scroll_speed_factor", 1.0)))

    def _adjust_scroll_factor(self, delta: float) -> None:
        """Speed the tab up or down by hand (+ / -), and remember it."""
        lo, hi = SCROLL_FACTOR_RANGE
        current = self._scroll_factor()
        self._config.scroll_speed_factor = max(lo, min(hi, round(current + delta, 2)))
        self._config.save()
        self._recompute_scroll_speed()

    def _filter_signature(self) -> tuple:
        """What the scroll speed depends on, so it is only redone when needed."""
        return (self._max_fret, tuple(self._active_strings))

    @staticmethod
    def _neighbour_gaps(notes: list[NoteEvent]) -> dict[tuple[float, int], float]:
        """Time to the nearest neighbouring note on the same string, in ms.

        Notes only collide within their own lane, so this is what limits how
        much room a note may take. Missing key means nothing else is near.
        """
        by_string: dict[int, list[float]] = {}
        for note in notes:
            by_string.setdefault(note.string, []).append(note.timestamp_ms)

        gaps: dict[tuple[float, int], float] = {}
        for string, stamps in by_string.items():
            unique = sorted(set(stamps))
            for i, ts in enumerate(unique):
                before = ts - unique[i - 1] if i > 0 else None
                after = unique[i + 1] - ts if i + 1 < len(unique) else None
                candidates = [g for g in (before, after) if g is not None]
                if candidates:
                    gaps[(ts, string)] = min(candidates)
        return gaps

    @staticmethod
    def _next_on_string(notes: list[NoteEvent]) -> dict[tuple[float, int], NoteEvent]:
        """The following note on the same string, for drawing slides to it."""
        by_string: dict[int, list[NoteEvent]] = {}
        for note in notes:
            by_string.setdefault(note.string, []).append(note)

        out: dict[tuple[float, int], NoteEvent] = {}
        for string, group in by_string.items():
            group.sort(key=lambda n: n.timestamp_ms)
            for note, following in zip(group, group[1:]):
                if following.timestamp_ms > note.timestamp_ms:
                    out[(note.timestamp_ms, string)] = following
        return out

    @staticmethod
    def bend_label(semitones: float) -> str:
        """Bend depth the way tab notation writes it, in WHOLE steps.

        Guitar notation counts steps, not semitones: one semitone is a half
        bend, two is 'full'. Writing '1' where a player expects 'full' is the
        kind of small wrongness that makes a display feel untrustworthy.
        """
        halves = int(round(semitones))
        if halves <= 0:
            return ""
        if halves == 1:
            return "½"
        if halves == 2:
            return "full"
        whole, rest = divmod(halves, 2)
        return f"{whole}½" if rest else str(whole)

    @staticmethod
    def _bend_points(
        note: NoteEvent, x: float, cy: float, width: float, lane_height: float,
    ) -> list[tuple[float, float]]:
        """Screen points of the bend curve, left to right.

        Each written point is joined to the next by a smoothstep rather than a
        straight line: a bend is a continuous pull, and a polyline with visible
        kinks reads as a staircase of separate pitches.
        """
        rise_per_step = lane_height * BEND_RISE_LANES / 2.0
        deepest = max((v for _, v in note.bend), default=0.0)
        cap = lane_height * BEND_MAX_RISE_LANES
        if deepest * rise_per_step > cap:
            rise_per_step = cap / deepest
        curve = list(note.bend)
        # GP files routinely omit the starting point at (0, 0); without it the
        # curve begins in mid-air beside the note head.
        if curve and curve[0][0] > 0.0:
            curve.insert(0, (0.0, 0.0))
        if len(curve) < 2:
            return []

        points: list[tuple[float, float]] = []
        for (p0, v0), (p1, v1) in zip(curve, curve[1:]):
            for step in range(BEND_CURVE_STEPS):
                f = step / BEND_CURVE_STEPS
                eased = f * f * (3 - 2 * f)
                pos = p0 + (p1 - p0) * f
                val = v0 + (v1 - v0) * eased
                points.append((x + pos * width, cy - val * rise_per_step))
        last_pos, last_val = curve[-1]
        points.append((x + last_pos * width, cy - last_val * rise_per_step))
        return points

    def _draw_bend(
        self, surface: pygame.Surface, note: NoteEvent, x: float, cy: float,
        head: float, capsule_w: float, lane_height: float,
        color: tuple[int, int, int], dim: bool,
    ) -> None:
        """An arc rising off the note head, with the depth written at its top.

        Drawn OVER the head rather than under it: the arc is the instruction,
        and half of it disappeared behind the sustain when it went below. It
        starts at the head's right edge so it reads as leaving the note, and
        the fret number stays clear at the head's left.
        """
        start = x + head
        width = max(capsule_w - head, head * BEND_MIN_WIDTH_HEADS)
        points = self._bend_points(note, start, cy, width, lane_height)
        if len(points) < 2:
            return

        t = get_theme()
        line = dimmed(color) if dim else lightened(color)
        pygame.draw.lines(surface, line, False,
                          [(int(px), int(py)) for px, py in points], 3)

        top = min(points, key=lambda p: p[1])
        barb = max(3, int(head * 0.16))
        # Arrowhead only where the curve actually rises, so a release-only
        # curve (bend already held, coming back down) does not sprout one.
        if top[1] < cy - 2:
            tip = int(top[0]), int(top[1])
            pygame.draw.polygon(surface, line, [
                (tip[0], tip[1] - barb),
                (tip[0] - barb, tip[1] + barb),
                (tip[0] + barb, tip[1] + barb),
            ])

        label = self.bend_label(note.bend_semitones)
        if not label:
            return
        font = _get_font("arial", max(11, int(head * 0.42)))
        text = font.render(label, True, dimmed(t.hud_text) if dim else t.hud_text)
        # Beside the top of the arc, not above it: above collides with the
        # next string up as soon as the bend is a full step or more.
        surface.blit(text, (int(top[0]) + barb + 2,
                            int(top[1]) - text.get_height() // 2))

    def _draw_slide(
        self, surface: pygame.Surface, note: NoteEvent, x: float, cy: float,
        head: float, capsule_w: float, target: NoteEvent | None,
        target_x: float | None, color: tuple[int, int, int], dim: bool,
    ) -> None:
        """A slanted connector to where the finger is going.

        The target of a slide sits on the SAME string, so the lane cannot show
        direction the way a staff would. The connector is slanted within the
        lane instead: rising to the right means sliding up the neck. It spans
        the GAP between the two heads rather than their full separation --
        across a long gap the slant would flatten out to nothing, and the
        direction is the whole point of drawing it.
        """
        radius = head / 2
        slant = radius * SLIDE_SLANT_FRACTION
        line = dimmed(color) if dim else lightened(color)

        if note.slide_to_next and target is not None and target_x is not None:
            # Ends just inside the target head and starts at the end of this
            # note's own body, so the whole connector lands in the gap the
            # shortened sustain left for it.
            end_x = target_x + radius * 0.5
            start_x = max(x + max(capsule_w, head) - radius * 0.5,
                          end_x - head * SLIDE_SPAN_HEADS)
            if end_x - start_x < 2:
                return
            # Up the neck is the higher fret. Comparing frets rather than
            # pitch keeps it right on tabs that slide across a string change.
            rise = slant if target.fret > note.fret else -slant
            pygame.draw.line(surface, line, (int(start_x), int(cy + rise)),
                             (int(end_x), int(cy - rise)), SLIDE_WIDTH_PX)
            return

        stub = head * SLIDE_STUB_HEADS
        if note.slide_out:
            start_x = x + max(capsule_w, head)
            rise = -slant if note.slide_out > 0 else slant
            pygame.draw.line(surface, line, (int(start_x), int(cy)),
                             (int(start_x + stub), int(cy + rise * 2)),
                             SLIDE_WIDTH_PX)
        if note.slide_in:
            rise = slant if note.slide_in > 0 else -slant
            pygame.draw.line(surface, line, (int(x - stub), int(cy + rise * 2)),
                             (int(x + radius), int(cy)), SLIDE_WIDTH_PX)

    @staticmethod
    def sustain_width(duration_ms: float, pixels_per_ms: float) -> float:
        """Length of a note's sustain body, with no minimum.

        Unlike note_width this may be zero: a short note is drawn as a bare
        circle, and padding it to a minimum length would turn every note into
        a capsule and destroy the short/long distinction.
        """
        return max(0.0, duration_ms * pixels_per_ms)

    # -- Drawing --

    def _draw_lanes(self, surface: pygame.Surface, layout: _Layout) -> None:
        """Draw the fretboard band: one panel, six strings across it."""
        t = get_theme()
        board_h = 6 * layout.lane_height
        # One uniform board, not alternating bands: the banding is what made
        # the old display read as a table of rows instead of a fretboard.
        pygame.draw.rect(
            surface, t.lane_bg_even,
            (0, int(layout.lane_top), layout.screen_w, int(board_h)),
        )
        # The strings themselves, down the middle of each lane, thicker toward
        # the low E so the lanes are told apart at a glance
        string_color = lightened(t.lane_line, 0.35)
        for i in range(6):
            y = int(layout.lane_top + (i + 0.5) * layout.lane_height)
            pygame.draw.line(
                surface, string_color, (0, y), (layout.screen_w, y),
                STRING_THICKNESS[i],
            )
        # Edges of the board, deliberately DARKER than the strings. Drawn in
        # the string colour they read as a seventh and a zeroth string.
        edge_color = dimmed(t.lane_line, 0.45)
        for edge_y in (layout.lane_top, layout.lane_top + board_h):
            pygame.draw.line(
                surface, edge_color,
                (0, int(edge_y)), (layout.screen_w, int(edge_y)), 2,
            )

    def _draw_hit_zone(self, surface: pygame.Surface, layout: _Layout) -> None:
        """The line a note's LEADING edge has to reach, plus the slack around it.

        Drawing the tolerance band as well as the line answers the question
        every player asks first — how exactly do I have to hit it — without
        anyone having to explain the timing window.
        """
        t = get_theme()
        x = int(layout.hit_zone_x)
        top = int(layout.lane_top)
        bottom = int(layout.lane_top + 6 * layout.lane_height)
        height = bottom - top

        slack_px = self._config.timing_window_ms * layout.pixels_per_ms
        if slack_px >= 2:
            band = pygame.Surface((int(slack_px * 2), height), pygame.SRCALPHA)
            band.fill((*t.hit_zone, 28))
            surface.blit(band, (x - int(slack_px), top))

        pygame.draw.line(surface, t.hit_zone, (x, top), (x, bottom), 3)

    def _draw_notes(self, surface: pygame.Surface, layout: _Layout) -> None:
        t = get_theme()
        # Visible time range with margins for long notes
        view_start = self._playback_ms - LEFT_MARGIN_MS
        view_end = self._playback_ms + self._visible_window_ms + RIGHT_MARGIN_MS

        notes = self._timeline.get_notes_in_range(view_start, view_end)

        neighbour_gap = self._neighbour_gaps(notes)
        next_on_string = self._next_on_string(notes)

        for note in notes:
            # Difficulty filter: skip notes that fail
            if not self._note_passes_filter(note):
                continue

            x = self.note_x(
                note.timestamp_ms, self._playback_ms,
                layout.hit_zone_x, layout.pixels_per_ms,
            )
            # A note may never occupy more room than it has before its
            # neighbour on the same string. Tab durations regularly overlap
            # the next note, and dense passages put onsets closer together
            # than a full-size head is wide — both drew notes on top of
            # each other.
            # One head size for the whole song, chosen with the scroll speed.
            # Sizing note by note would make notes visibly change as they
            # scroll, which is the thing this display must never do.
            head = self._head_px if self._head_px is not None else layout.note_h
            radius = head / 2
            visual_gap = head * (SLIDE_GAP_FRACTION if note.slide_to_next
                                 else SUSTAIN_GAP_FRACTION)

            # A sustain still stops short of its neighbour, so a long tab
            # duration cannot run over the next note
            gap_ms = neighbour_gap.get((note.timestamp_ms, note.string))
            gap_px = (gap_ms * layout.pixels_per_ms
                      if gap_ms is not None else float("inf"))
            body = self.sustain_width(note.duration_ms, layout.pixels_per_ms)
            capsule_w = min(body, gap_px) - visual_gap

            # Skip notes fully off-screen
            if x + max(capsule_w, 2 * radius) < 0 or x > layout.screen_w:
                continue

            # Centre of the string lane this note sits on
            cy = layout.lane_top + (note.string - 0.5) * layout.lane_height

            # Color: feedback color if matched, dimmed if past the hit zone
            base_color = STRING_COLORS.get(note.string, (180, 180, 180))
            past_hit_zone = note.timestamp_ms < self._playback_ms
            if self._audio_enabled:
                color = self._feedback.get_note_color(
                    note, base_color, self._playback_ms, past_hit_zone,
                )
            else:
                color = dimmed(base_color) if past_hit_zone else base_color

            # A short note is a circle sitting on its string; a sustained one
            # stretches into a capsule. Either way the note's LEADING EDGE is
            # at its own time, so the moment to play is when the start of the
            # shape reaches the hit line -- not its middle, which put the cue
            # half a note late.
            # A slide connector goes UNDER the heads it runs between, so it
            # cannot land on top of the target's fret number.
            if note.slide_to_next or note.slide_in or note.slide_out:
                following = next_on_string.get((note.timestamp_ms, note.string))
                target_x = None
                if following is not None:
                    target_x = self.note_x(
                        following.timestamp_ms, self._playback_ms,
                        layout.hit_zone_x, layout.pixels_per_ms,
                    )
                self._draw_slide(surface, note, x, cy, head, capsule_w,
                                 following, target_x, base_color, past_hit_zone)

            if capsule_w > 2 * radius:
                rect = pygame.Rect(
                    int(x), int(cy - radius), int(capsule_w), int(2 * radius),
                )
                pygame.draw.rect(surface, color, rect, border_radius=int(radius))
                pygame.draw.rect(surface, t.note_border, rect, width=2,
                                 border_radius=int(radius))
            else:
                centre = (int(x + radius), int(cy))
                pygame.draw.circle(surface, color, centre, int(radius))
                pygame.draw.circle(surface, t.note_border, centre, int(radius), 2)

            # The bend arc goes OVER the head: it rises away from it, so it
            # hides nothing, and half of it vanished behind the sustain when
            # it was drawn underneath.
            if note.bend:
                self._draw_bend(surface, note, x, cy, head, capsule_w,
                                layout.lane_height, base_color, past_hit_zone)

            # Fret number centred in the head, sized to the head it sits in —
            # a fixed size spills out of the shrunken heads of a fast run
            fret_font = self._fret_font(radius)
            fret_label = str(note.fret)
            fret_text = fret_font.render(fret_label, True, t.note_text)
            if fret_text.get_width() <= 2 * radius:
                tx = int(x + radius) - fret_text.get_width() // 2
                ty = int(cy) - fret_text.get_height() // 2
                outline = fret_font.render(fret_label, True, (0, 0, 0))
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    surface.blit(outline, (tx + dx, ty + dy))
                surface.blit(fret_text, (tx, ty))

    # Sizes the footer will try, largest first. A shortcut nobody can read
    # because the line ran off the window is a shortcut nobody has.
    FOOTER_FONT_SIZES = (14, 13, 12, 11, 10)

    def _footer_lines(self) -> tuple[str, str]:
        """The two footer lines: what is happening, then the rest of the keys.

        Every key handle_event answers appears here, unconditionally. A key
        that is bound but undocumented is a key nobody finds, so the ones
        that need something loaded (the backing track, wait mode) are listed
        with a dash rather than hidden -- the dash is the answer to "why does
        pressing it do nothing".
        """
        if self._playback_ms < 0:
            state = "Count-in"
        elif self._playing:
            if self._wait_mode_frozen:
                state = "Waiting..."
            elif self._audio_enabled:
                state = "Playing"
            else:
                state = "Auto-scroll"
        else:
            state = "Paused"

        audio_state = "ON" if self._audio_enabled else "off"
        loop_state = "ON" if self._loop_enabled else "off"
        if self._midi_player is None:
            backing_state = "—"
        else:
            backing_state = "off" if self._backing_muted else "ON"
        if not self._wait_mode:
            wait_state = "off" if self._audio_enabled else "—"
        else:
            wait_state = "WAIT" if self._wait_mode_frozen else "ON"

        transport = (
            f"{state}  |  SPACE: play/pause  |  LEFT/RIGHT: seek  "
            f"|  HOME: restart  |  PgDn/PgUp: tempo  |  A: audio {audio_state}  "
            f"|  B: backing {backing_state}  |  W: wait {wait_state}  "
            f"|  I/O: loop {loop_state}  |  P: toggle  |  ESC: menu"
        )
        tools = (
            "+/-: speed  |  G: hit window  |  K: sync (Shift+K: reset)  "
            "|  ,/.: sync +/-10ms  |  N/M: backing sync  |  X/C: gate  "
            "|  TAB: track  |  V: chords  |  J: strings  |  F: frets  "
            "|  F1-F6: mute string  |  L: weakest part  |  T: theme  |  H: help"
        )
        return transport, tools

    def _blit_footer_lines(
        self, surface: pygame.Surface, layout: _Layout,
        lines: tuple[str, ...], color: tuple[int, int, int],
    ) -> None:
        """Centre the footer lines, shrinking until the widest one fits."""
        w = layout.screen_w
        for size in self.FOOTER_FONT_SIZES:
            font = _get_font("arial", size)
            rendered = [font.render(line, True, color) for line in lines]
            if max(s.get_width() for s in rendered) <= w - 16:
                break
        line_h = rendered[0].get_height() + 2
        y = layout.screen_h - 4 - line_h * len(rendered)
        for surf in rendered:
            surface.blit(surf, (w // 2 - surf.get_width() // 2, y))
            y += line_h

    def _draw_hud(self, surface: pygame.Surface, layout: _Layout) -> None:
        t = get_theme()
        title_font = _get_font("arial", 20)
        time_font = _get_font("consolas", 20)
        hint_font = _get_font("arial", 14)

        meta = self._timeline.metadata
        w = layout.screen_w
        h = layout.screen_h

        # Count-in overlay — large centered beat countdown
        if self._playback_ms < 0 and self._count_in_ms > 0:
            remaining_beats = int(-self._playback_ms / self._ms_per_beat) + 1
            remaining_beats = min(remaining_beats, self._config.count_in_beats)
            countdown_font = _get_font("arial", 120)
            countdown_surf = countdown_font.render(
                str(remaining_beats), True, t.hud_accent
            )
            surface.blit(
                countdown_surf,
                (w // 2 - countdown_surf.get_width() // 2,
                 h // 2 - countdown_surf.get_height() // 2),
            )

        # Song completion overlay
        if self._song_completed:
            self._draw_completion_overlay(surface, layout)

        # Top-left: title + artist
        title = meta.title or "Untitled"
        if meta.artist:
            title = f"{meta.artist} — {title}"
        title_surf = title_font.render(title, True, t.hud_text)
        surface.blit(title_surf, (12, 12))

        # Top-center: BPM with tempo percentage (and streak below it)
        pct = int(self._tempo_factor * 100)
        bpm_text = f"{meta.tempo} BPM ({pct}%)"
        bpm_surf = title_font.render(bpm_text, True, t.hud_accent)
        surface.blit(bpm_surf, (w // 2 - bpm_surf.get_width() // 2, 12))

        # Loop status below BPM
        loop_y = 36
        loop_info = self._loop_hud_text()
        if loop_info:
            loop_color = t.hud_accent if self._loop_enabled else t.hud_text
            loop_surf = hint_font.render(loop_info, True, loop_color)
            surface.blit(loop_surf, (w // 2 - loop_surf.get_width() // 2, loop_y))
            loop_y += 18

        if self._audio_enabled:
            self._feedback.draw_streak(surface, title_font, w // 2, loop_y)

        # Top-right: time
        current = format_time(self._playback_ms)
        total = format_time(self._timeline.duration_ms)
        time_text = f"{current} / {total}"
        time_surf = time_font.render(time_text, True, t.hud_text)
        surface.blit(time_surf, (w - time_surf.get_width() - 12, 12))

        # Top-right second line: accuracy stats
        stats_bottom_y = 36
        if self._audio_enabled and self._matcher is not None:
            stats = self._matcher.get_statistics()
            if stats["total"] > 0:
                self._feedback.draw_stats(surface, stats, hint_font, w - 12, 36)
                stats_bottom_y = 54

        # Top-right: noise gate + signal meter + tuner (below stats, when audio capture exists)
        if self._audio_enabled:
            gate_text = f"Gate: {int(self._noise_gate_db)} dB"
            gate_surf = hint_font.render(gate_text, True, t.hud_accent)
            surface.blit(gate_surf, (w - gate_surf.get_width() - 12, stats_bottom_y))
            if self._audio_capture is not None:
                self._draw_signal_meter(surface, hint_font, w, stats_bottom_y + 18)
                self._draw_tuner(surface, hint_font, w, stats_bottom_y + 36)
        elif self._audio_capture is not None:
            # Audio off but capture exists — still show meter and tuner
            self._draw_signal_meter(surface, hint_font, w, stats_bottom_y)
            self._draw_tuner(surface, hint_font, w, stats_bottom_y + 18)

        # Bottom-center: play state + controls
        self._blit_footer_lines(surface, layout, self._footer_lines(), t.hud_text)

        # Top-left second line: track name + filter info
        info_y = 38
        if meta.track_name:
            track_surf = hint_font.render(
                f"Track: {meta.track_name}", True, t.hud_text
            )
            surface.blit(track_surf, (12, info_y))
            info_y += 16

        # Difficulty filter HUD
        filter_text = self._filter_hud_text()
        if filter_text:
            filter_surf = hint_font.render(filter_text, True, t.hud_accent)
            surface.blit(filter_surf, (12, info_y))
            info_y += 16

        # Chord mode HUD
        if self._chord_partial_credit != self._config._default_chord_partial_credit:
            chord_text = "Chords: strict" if self._chord_partial_credit else "Chords: easy"
            chord_surf = hint_font.render(chord_text, True, t.hud_accent)
            surface.blit(chord_surf, (12, info_y))
            info_y += 16

        # Hit-window HUD — always shown, since it decides what counts as a hit
        window_surf = hint_font.render(
            f"Hit window: +/-{int(self._config.timing_window_ms)} ms (G)",
            True, t.hud_text)
        surface.blit(window_surf, (12, info_y))
        info_y += 16

        # Backing offset HUD — only when shifted, but then always visible,
        # since a backing that disagrees with the notes is the hardest fault
        # to diagnose by ear
        backing_off = self._backing_offset()
        if abs(backing_off) > 0.5:
            back_surf = hint_font.render(
                f"Backing: {int(backing_off):+d} ms (N/M)", True, t.hud_accent)
            surface.blit(back_surf, (12, info_y))
            info_y += 16

        # Scroll speed HUD — shows the seconds of song on screen, not just the
        # trim factor: turning the speed down stops once notes would have to
        # shrink, and a factor that changes nothing visible is a puzzle
        ahead = self._visible_window_ms / 1000.0
        trimmed = abs(self._scroll_factor() - 1.0) > 0.01
        speed_surf = hint_font.render(
            f"Scroll: {ahead:.1f} s ahead ({self._scroll_factor():.1f}x, +/-)",
            True, t.hud_accent if trimmed else t.hud_text)
        surface.blit(speed_surf, (12, info_y))
        info_y += 16

        # Per-string chord check HUD — only when switched off, so the default
        # costs no screen space but a disabled check is never a silent surprise
        if not getattr(self._config, "chord_verify", True):
            verify_surf = hint_font.render("Strings: off (J)", True, t.hud_accent)
            surface.blit(verify_surf, (12, info_y))
            info_y += 16

        # Latency sync HUD — show measured timing error once enough strikes
        # were scored, so the player knows K (auto-sync) has data to work with
        if self._audio_enabled and self._matcher is not None:
            offset = self._config.audio_latency_offset_ms
            err = self._matcher.median_timing_error_ms()
            if err is not None:
                direction = "late" if err > 0 else "early"
                # Spread separates the two timing problems: a big error with a
                # small spread is latency and K fixes it; a big spread means
                # the strikes disagree with each other and no offset can help
                spread = self._matcher.timing_spread_ms()
                spread_text = f"  ±{int(spread):d} ms" if spread is not None else ""
                verdict = ""
                if spread is not None:
                    samples = len(self._matcher.timing_errors_ms)
                    if spread > AUTO_SYNC_HOPELESS_SPREAD_MS:
                        verdict = "— too scattered to sync"
                    elif (spread > AUTO_SYNC_WIDE_SPREAD_MS
                            and samples < AUTO_SYNC_WIDE_MIN_SAMPLES):
                        verdict = f"— play on ({samples}/{AUTO_SYNC_WIDE_MIN_SAMPLES}) then K"
                    else:
                        verdict = "— K to auto-sync"
                sync_text = (f"Sync: {int(offset):+d} ms  |  strikes {int(abs(err)):d} ms "
                             f"{direction}{spread_text} {verdict}")
                sync_color = t.hud_accent if abs(err) > 20 else t.hud_text
            elif offset != 0:
                sync_text = f"Sync: {int(offset):+d} ms"
                sync_color = t.hud_text
            else:
                sync_text = None
            if sync_text:
                sync_surf = hint_font.render(sync_text, True, sync_color)
                surface.blit(sync_surf, (12, info_y))

    def _draw_signal_meter(self, surface: pygame.Surface, font: pygame.font.Font,
                           screen_w: int, y: int) -> None:
        """Draw a compact horizontal signal level meter with dB label."""
        t = get_theme()
        db = self._signal_db_smooth

        bar_w = 100
        bar_h = 8
        db_min = -80.0
        db_max = -10.0

        # dB label
        db_display = max(db_min, min(db_max, db))
        label = f"Signal: {int(db_display)} dB"
        label_surf = font.render(label, True, t.hud_text)
        label_x = screen_w - label_surf.get_width() - 12
        surface.blit(label_surf, (label_x, y))

        # Bar position: to the left of the label
        bar_x = label_x - bar_w - 8
        bar_y = y + label_surf.get_height() // 2 - bar_h // 2

        # Bar background
        pygame.draw.rect(surface, t.signal_cold, (bar_x, bar_y, bar_w, bar_h))

        # Fill proportion
        fill_frac = max(0.0, min(1.0, (db - db_min) / (db_max - db_min)))
        fill_w = int(fill_frac * bar_w)

        if fill_w > 0:
            if db >= -30:
                color = t.signal_hot
            elif db >= self._noise_gate_db:
                color = t.signal_warm
            else:
                color = t.signal_cold
            pygame.draw.rect(surface, color, (bar_x, bar_y, fill_w, bar_h))

        # Bar border
        pygame.draw.rect(surface, t.hud_text, (bar_x, bar_y, bar_w, bar_h), 1)

        # Noise gate tick mark
        gate_frac = max(0.0, min(1.0, (self._noise_gate_db - db_min) / (db_max - db_min)))
        gate_x = bar_x + int(gate_frac * bar_w)
        pygame.draw.line(surface, t.hud_accent, (gate_x, bar_y - 2), (gate_x, bar_y + bar_h + 2), 1)

    def _draw_tuner(self, surface: pygame.Surface, font: pygame.font.Font,
                    screen_w: int, y: int) -> None:
        """Draw a compact tuner display with cents bar and note name."""
        t = get_theme()

        bar_w = 100
        bar_h = 8

        freq = self._tuner_freq_smooth

        if freq <= 0 or self._tuner_displayed_note < 0:
            # No pitch — show placeholder
            label = "Tuner: ---"
            label_surf = font.render(label, True, t.hud_text)
            surface.blit(label_surf, (screen_w - label_surf.get_width() - 12, y))
            return

        # Use hysteresis-stabilized note for the label, smoothed freq for cents
        midi_note, cents = freq_to_cents_deviation(freq)
        if midi_note < 0:
            return

        note_name = midi_to_name(self._tuner_displayed_note)
        # Recompute cents relative to the displayed note for consistency
        from pickhero.audio.note_utils import midi_to_freq as _mtf
        target_freq = _mtf(self._tuner_displayed_note)
        if target_freq > 0:
            import math
            cents = 1200 * math.log2(freq / target_freq)

        # Choose color based on cents deviation
        abs_cents = abs(cents)
        if abs_cents < 5:
            fill_color = t.tuner_in_tune
        elif abs_cents < 15:
            fill_color = t.tuner_close
        else:
            fill_color = t.tuner_off

        # Note name + cents label
        sign = "+" if cents >= 0 else ""
        label = f"{note_name} {sign}{int(cents)}\u00A2"
        label_surf = font.render(label, True, fill_color)
        label_x = screen_w - label_surf.get_width() - 12
        surface.blit(label_surf, (label_x, y))

        # Bar position: to the left of the label
        bar_x = label_x - bar_w - 8
        bar_y = y + label_surf.get_height() // 2 - bar_h // 2

        # Bar background
        pygame.draw.rect(surface, t.signal_cold, (bar_x, bar_y, bar_w, bar_h))

        # Fill indicator: center = in-tune, left = flat, right = sharp
        center_x = bar_x + bar_w // 2
        fill_offset = int((cents / 50.0) * (bar_w // 2))
        fill_offset = max(-bar_w // 2, min(bar_w // 2, fill_offset))

        if fill_offset >= 0:
            pygame.draw.rect(surface, fill_color,
                             (center_x, bar_y, fill_offset, bar_h))
        else:
            pygame.draw.rect(surface, fill_color,
                             (center_x + fill_offset, bar_y, -fill_offset, bar_h))

        # Bar border
        pygame.draw.rect(surface, t.hud_text, (bar_x, bar_y, bar_w, bar_h), 1)

        # Center tick mark (in-tune reference)
        pygame.draw.line(surface, t.hud_text,
                         (center_x, bar_y - 2), (center_x, bar_y + bar_h + 2), 1)

    def _draw_completion_overlay(self, surface: pygame.Surface, layout: _Layout) -> None:
        """Draw the song completion results overlay."""
        t = get_theme()
        w, h = layout.screen_w, layout.screen_h

        # Semi-transparent dark overlay
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        surface.blit(overlay, (0, 0))

        header_font = _get_font("arial", 48)
        stat_font = _get_font("consolas", 28)
        hint_font = _get_font("arial", 18)

        center_y = h // 2 - 80

        # "Song Complete!" header
        header_surf = header_font.render("Song Complete!", True, t.hud_accent)
        surface.blit(header_surf, (w // 2 - header_surf.get_width() // 2, center_y))

        if self._audio_enabled and self._matcher is not None:
            # Accuracy stats
            stats = self._matcher.get_statistics()
            accuracy_text = (
                f"Accuracy: {stats['accuracy_percent']:.1f}%  "
                f"({stats['hits']}/{stats['total']})"
            )
            acc_surf = stat_font.render(accuracy_text, True, t.hud_text)
            surface.blit(acc_surf, (w // 2 - acc_surf.get_width() // 2, center_y + 60))

            # "New Best!" indicator
            if self._is_new_best:
                best_surf = stat_font.render("New Best!", True, (255, 220, 50))
                surface.blit(best_surf, (w // 2 - best_surf.get_width() // 2, center_y + 100))

            # Weakest sections
            weak = getattr(self, "_weakest_sections", [])
            if weak:
                section = weak[0]
                weak_text = (
                    f"Weakest: bars {section[0]+1}-{section[1]+1} "
                    f"({section[2]:.0f}%) -- press L to loop"
                )
                weak_surf = hint_font.render(weak_text, True, t.feedback_close)
                surface.blit(weak_surf, (w // 2 - weak_surf.get_width() // 2, center_y + 140))

            # Practice recommendations
            rec_y = center_y + 170
            for rec in self._recommendations:
                rec_surf = hint_font.render(rec, True, t.hud_accent)
                surface.blit(rec_surf, (w // 2 - rec_surf.get_width() // 2, rec_y))
                rec_y += 24

            # Controls hint
            hint_y = max(center_y + 180, rec_y + 10)
            hint_text = "SPACE to replay  |  L to loop weak section  |  ESC to menu"
            hint_surf = hint_font.render(hint_text, True, t.hud_text)
            surface.blit(hint_surf, (w // 2 - hint_surf.get_width() // 2, hint_y))
        else:
            # Auto-scroll completion — no stats
            hint_text = "SPACE to replay  |  ESC to menu"
            hint_surf = hint_font.render(hint_text, True, t.hud_text)
            surface.blit(hint_surf, (w // 2 - hint_surf.get_width() // 2, center_y + 70))

    def _draw_help_overlay(self, surface: pygame.Surface, layout: _Layout) -> None:
        """Explain the track, the note colours, the techniques and the keys.

        Two columns. There is more to say than fits down one side of a 720 px
        window, and a help page whose last section falls off the bottom edge
        is worse than no help page.
        """
        t = get_theme()
        w, h = layout.screen_w, layout.screen_h

        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 236))
        surface.blit(overlay, (0, 0))

        title_font = _get_font("arial", 26)
        section_font = _get_font("arial", 18)
        body_font = _get_font("arial", 15)
        hint_font = _get_font("arial", 13)

        cx = w // 2
        title_surf = title_font.render("Help", True, t.hud_accent)
        surface.blit(title_surf, (cx - title_surf.get_width() // 2, 12))

        top = 56
        bottom = h - 30
        col_w = (w - 60) // 2
        columns = [30, 30 + col_w + 20]
        col = 0
        x, y = columns[0], top

        def block(title: str, items, font=body_font, step=20) -> None:
            """One titled section, measured before anything is drawn.

            Whole blocks move to the next column, never halves of one: a
            heading stranded at the foot of a column with its list continuing
            at the top of the next reads as two unrelated things.

            An item is either a line of text, or (colour, line) for a swatch.
            """
            nonlocal col, x, y
            needed = 24 + step * len(items) + 8
            if y + needed > bottom and col + 1 < len(columns):
                col += 1
                x, y = columns[col], top

            surface.blit(section_font.render(title, True, t.hud_accent), (x, y))
            y += 24
            for item in items:
                if isinstance(item, tuple):
                    color, label = item
                    pygame.draw.rect(surface, color, (x, y + 3, 13, 13),
                                     border_radius=2)
                    surface.blit(font.render(label, True, t.hud_text), (x + 20, y))
                else:
                    surface.blit(font.render(item, True, t.hud_text), (x, y))
                y += step
            y += 8

        block("Reading the Track", [
            "Notes scroll right-to-left toward the hit zone (white line).",
            "The number on each note is the fret to press (0 = open).",
            "Play it as the START of the note reaches the line.",
            "Dimmed notes have already passed the hit zone.",
            "A note's colour matches its row, so it names the string.",
        ])

        block("The 6 Rows = 6 Guitar Strings", [
            (STRING_COLORS.get(s, (180, 180, 180)), label)
            for s, label in (
                (1, "Row 1 (top)      = high E  (thinnest)"),
                (2, "Row 2               = B"),
                (3, "Row 3               = G"),
                (4, "Row 4               = D"),
                (5, "Row 5               = A"),
                (6, "Row 6 (bottom) = low E  (thickest)"),
            )
        ])

        block("Bends and Slides", [
            "An arc curving up off a note is a BEND: fret it, then push",
            "the string until the pitch rises. The label says how far \u2014",
            "\u00bd is one fret, 'full' is two. The arc's height says the same.",
            "A slanted bar between two notes is a SLIDE: strike only the",
            "first and slide into the second. Rising to the right means",
            "up the neck. A short stub is a slide off into nothing.",
            "Both are scored leniently: the pitch has to land in the",
            "right region, not exactly on the target.",
        ])

        block("Scoring (colours change after you play)", [
            (t.feedback_hit, "Green \u2014 you played the correct note"),
            (t.feedback_close, "Yellow \u2014 close, off by 1 semitone"),
            (t.feedback_miss, "Red \u2014 missed, or not played in time"),
        ])

        block("Controls", [
            "SPACE: play/pause     LEFT/RIGHT: seek     HOME: restart",
            "A: toggle audio     PgDn/PgUp: tempo     X/C: noise gate",
            "B: backing track     T: theme     I/O: loop markers",
            "P: toggle loop     L: loop the weakest part",
            "F: fret limit     F1-F6: mute a string     V: chord mode",
            "W: wait mode (holds until you play the right note)",
            "J: per-string chord check (finds the wrong-fret string)",
            "K: auto-sync timing     ,/.: nudge sync by 10 ms",
            "Shift+K: reset sync to 0, if it has run away",
            "+/-: scroll faster / slower     G: hit window",
            "N/M: backing track earlier / later",
            "TAB: choose track     H: this help     ESC: song list",
        ], font=hint_font, step=17)

        close_surf = hint_font.render("Press H to close", True, t.hud_accent)
        surface.blit(close_surf, (cx - close_surf.get_width() // 2, h - 20))

    # -- Difficulty filter --

    def _cycle_fret_limit(self) -> None:
        """Cycle through fret limit options."""
        try:
            idx = FRET_LIMITS.index(self._max_fret)
            self._max_fret = FRET_LIMITS[(idx + 1) % len(FRET_LIMITS)]
        except ValueError:
            self._max_fret = FRET_LIMITS[0]
        self._config.max_fret = self._max_fret
        self._config.save()
        self._reset_matcher_for_filter()

    def _toggle_string(self, string: int) -> None:
        """Toggle a string on/off in the difficulty filter."""
        idx = string - 1
        self._active_strings[idx] = not self._active_strings[idx]
        # Don't allow all strings to be off
        if not any(self._active_strings):
            self._active_strings[idx] = True
            return
        self._config.active_strings = list(self._active_strings)
        self._config.save()
        self._reset_matcher_for_filter()

    def _reset_matcher_for_filter(self) -> None:
        """Reset matcher when filter changes mid-song."""
        if self._matcher:
            self._matcher.reset()
            self._matcher.note_filter = self._note_passes_filter
        self._feedback.reset()

    def _filter_hud_text(self) -> str | None:
        """Return difficulty filter text for HUD, or None if default."""
        parts = []
        if self._max_fret < 24:
            parts.append(f"Fret: 0-{self._max_fret}")
        if not all(self._active_strings):
            strs = " ".join(
                str(i + 1) if on else "_"
                for i, on in enumerate(self._active_strings)
            )
            parts.append(f"Strings: {strs}")
        return "  |  ".join(parts) if parts else None

    # -- Theme --

    def _cycle_theme(self) -> None:
        """Toggle between dark and light theme."""
        name = cycle_theme()
        self._config.theme = name
        self._config.save()

    # -- Chord mode --

    def _toggle_chord_mode(self) -> None:
        """Toggle chord partial credit on/off."""
        self._chord_partial_credit = not self._chord_partial_credit
        self._config.chord_partial_credit = self._chord_partial_credit
        self._config.save()
        if self._matcher:
            self._matcher.chord_partial_credit = self._chord_partial_credit

    # -- Wait mode --

    def _toggle_wait_mode(self) -> None:
        """Toggle wait mode on/off."""
        self._wait_mode = not self._wait_mode
        self._config.wait_mode = self._wait_mode
        self._config.save()
        if not self._wait_mode:
            self._wait_mode_frozen = False

    # -- Latency sync --

    def _late_window_ms(self) -> float:
        """Grace period for late-arriving strike notes.

        Base 150 ms covers the onset collector delay; a compensated input
        latency delays the strike's real-world arrival by the same amount
        on top, so misses must be marked correspondingly later.
        """
        return 150.0 + max(0.0, -self._config.audio_latency_offset_ms)

    def _make_chord_verifier(self):
        """Per-string chord verifier, or None when the setting is off."""
        if not getattr(self._config, "chord_verify", True):
            return None
        from pickhero.audio.chord_verify import ChordVerifier
        return ChordVerifier()

    def _toggle_chord_verify(self) -> None:
        """Turn per-string chord verification on or off (key: J)."""
        self._config.chord_verify = not getattr(self._config, "chord_verify", True)
        self._config.save()
        if self._matcher is not None:
            self._matcher.chord_verifier = self._make_chord_verifier()

    def _adjust_latency_offset(self, delta_ms: float) -> None:
        """Shift the audio latency compensation and persist it.

        Negative values register strikes earlier (use when you feel forced
        to play ahead of the music to score hits).
        """
        target = self._config.audio_latency_offset_ms + delta_ms
        clamped = max(-MAX_LATENCY_OFFSET_MS, min(MAX_LATENCY_OFFSET_MS, target))
        delta_ms = clamped - self._config.audio_latency_offset_ms
        self._config.audio_latency_offset_ms = clamped
        self._config.save()
        if self._matcher is not None:
            self._matcher.audio_offset_ms += delta_ms
            self._matcher.late_window_ms = self._late_window_ms()
            # Old measurements no longer reflect the new offset
            self._matcher.timing_errors_ms.clear()

    def _auto_sync_timing(self) -> None:
        """Cancel out the measured input latency (K key).

        Uses the median timing error of the strikes matched so far in this
        run; needs a handful of scored notes before it can do anything.
        Refuses when the strikes disagree too much among themselves — there
        is no single offset that fixes scattered timing, and applying one
        anyway is how the offset used to walk away from any sane value.
        """
        if self._matcher is None:
            return
        err = self._matcher.median_timing_error_ms(AUTO_SYNC_MIN_SAMPLES)
        if err is None:
            return
        spread = self._matcher.timing_spread_ms()
        samples = len(self._matcher.timing_errors_ms)
        if spread is not None:
            if spread > AUTO_SYNC_HOPELESS_SPREAD_MS:
                return
            if spread > AUTO_SYNC_WIDE_SPREAD_MS and samples < AUTO_SYNC_WIDE_MIN_SAMPLES:
                return
        self._adjust_latency_offset(-err)

    def _reset_latency_offset(self) -> None:
        """Put latency compensation back to zero (Shift+K).

        The way out when the offset no longer resembles anything real and
        every fresh measurement is taken against the wrong note.
        """
        self._adjust_latency_offset(-self._config.audio_latency_offset_ms)

    # -- Loop weakest section --

    def _loop_weakest_section(self) -> None:
        """Set loop to weakest section from completion screen."""
        weak = getattr(self, "_weakest_sections", [])
        if not weak or not self._song_completed:
            return
        section = weak[0]
        start_measure, end_measure = section[0], section[1]
        # Get measure time ranges from timeline
        measures = self._timeline.measures
        if not measures or start_measure >= len(measures):
            return
        start_ms = measures[start_measure].start_ms
        end_idx = min(end_measure + 1, len(measures) - 1)
        end_ms = measures[end_idx].end_ms if end_idx < len(measures) else self._timeline.duration_ms
        self._loop_start_ms = start_ms
        self._loop_end_ms = end_ms
        self._loop_enabled = True
        self._song_completed = False
        self._is_new_best = False
        self._weakest_sections = []
        self.seek(start_ms)

    # -- Loop control --

    def _set_loop_start(self, ms: float) -> None:
        """Set loop start marker. Auto-swap if after end, auto-enable when both set."""
        self._loop_start_ms = ms
        if self._loop_end_ms is not None and self._loop_start_ms > self._loop_end_ms:
            self._loop_start_ms, self._loop_end_ms = self._loop_end_ms, self._loop_start_ms
        self._enforce_min_loop()
        if self._loop_start_ms is not None and self._loop_end_ms is not None:
            self._loop_enabled = True

    def _set_loop_end(self, ms: float) -> None:
        """Set loop end marker. Auto-swap if before start, auto-enable when both set."""
        self._loop_end_ms = ms
        if self._loop_start_ms is not None and self._loop_end_ms < self._loop_start_ms:
            self._loop_start_ms, self._loop_end_ms = self._loop_end_ms, self._loop_start_ms
        self._enforce_min_loop()
        if self._loop_start_ms is not None and self._loop_end_ms is not None:
            self._loop_enabled = True

    def _enforce_min_loop(self) -> None:
        """Ensure loop region is at least one beat long."""
        if self._loop_start_ms is not None and self._loop_end_ms is not None:
            if self._loop_end_ms - self._loop_start_ms < self._ms_per_beat:
                self._loop_end_ms = self._loop_start_ms + self._ms_per_beat

    def _toggle_loop(self) -> None:
        """Toggle loop off (keep markers), then clear markers on second press."""
        if self._loop_enabled:
            self._loop_enabled = False
        elif self._loop_start_ms is not None or self._loop_end_ms is not None:
            self._loop_start_ms = None
            self._loop_end_ms = None
            self._loop_enabled = False
        # If everything is already None/False, do nothing

    def _loop_hud_text(self) -> str | None:
        """Return loop status text for HUD, or None if no markers."""
        if self._loop_start_ms is not None and self._loop_end_ms is not None:
            s = format_time(self._loop_start_ms)
            e = format_time(self._loop_end_ms)
            if self._loop_enabled:
                return f"LOOP {s} - {e}"
            return f"loop {s} - {e} (off)"
        if self._loop_start_ms is not None:
            return f"loop start: {format_time(self._loop_start_ms)}"
        if self._loop_end_ms is not None:
            return f"loop end: {format_time(self._loop_end_ms)}"
        return None

    def _draw_loop_region(self, surface: pygame.Surface, layout: _Layout) -> None:
        """Draw loop markers and shaded region between them."""
        if self._loop_start_ms is None and self._loop_end_ms is None:
            return

        t = get_theme()
        lane_top = int(layout.lane_top)
        lane_bottom = int(layout.lane_top + 6 * layout.lane_height)
        lane_h = lane_bottom - lane_top

        marker_color = t.loop_marker if self._loop_enabled else t.loop_marker_disabled
        region_color = t.loop_region if self._loop_enabled else t.loop_region_disabled

        # Draw shaded region between both markers
        if self._loop_start_ms is not None and self._loop_end_ms is not None:
            x_start = int(self.note_x(self._loop_start_ms, self._playback_ms,
                                      layout.hit_zone_x, layout.pixels_per_ms))
            x_end = int(self.note_x(self._loop_end_ms, self._playback_ms,
                                    layout.hit_zone_x, layout.pixels_per_ms))
            # Clamp to screen
            x_start = max(0, min(x_start, layout.screen_w))
            x_end = max(0, min(x_end, layout.screen_w))
            if x_end > x_start:
                overlay = pygame.Surface((x_end - x_start, lane_h), pygame.SRCALPHA)
                overlay.fill(region_color)
                surface.blit(overlay, (x_start, lane_top))

        # Draw start marker
        if self._loop_start_ms is not None:
            x = int(self.note_x(self._loop_start_ms, self._playback_ms,
                                layout.hit_zone_x, layout.pixels_per_ms))
            if 0 <= x <= layout.screen_w:
                pygame.draw.line(surface, marker_color, (x, lane_top), (x, lane_bottom), 2)
                # Right-pointing triangle at top
                pygame.draw.polygon(surface, marker_color, [
                    (x, lane_top), (x + 10, lane_top + 7), (x, lane_top + 14),
                ])

        # Draw end marker
        if self._loop_end_ms is not None:
            x = int(self.note_x(self._loop_end_ms, self._playback_ms,
                                layout.hit_zone_x, layout.pixels_per_ms))
            if 0 <= x <= layout.screen_w:
                pygame.draw.line(surface, marker_color, (x, lane_top), (x, lane_bottom), 2)
                # Left-pointing triangle at top
                pygame.draw.polygon(surface, marker_color, [
                    (x, lane_top), (x - 10, lane_top + 7), (x, lane_top + 14),
                ])

    # -- Audio control --

    def _toggle_audio(self) -> None:
        """Toggle audio capture on/off."""
        self._audio_enabled = not self._audio_enabled
        if self._audio_enabled:
            if self._playing:
                self._start_audio()
            else:
                # Start capture for signal monitoring even while paused
                self._start_capture_only()
        else:
            self._stop_audio()

    def _start_audio(self) -> None:
        """Start audio capture and create matcher."""
        try:
            from pickhero.audio.input import AudioCapture
            if self._audio_capture is None:
                self._audio_capture = AudioCapture(self._config)
            self._audio_capture.start()
            self._matcher = NoteMatcher(
                self._timeline,
                timing_window_ms=self._config.timing_window_ms,
                audio_offset_ms=self._playback_ms + self._config.audio_latency_offset_ms,
                chord_threshold_ms=self._config.chord_threshold_ms,
                note_filter=self._note_passes_filter if self._is_filter_active() else None,
                chord_partial_credit=self._chord_partial_credit,
                late_window_ms=self._late_window_ms(),
                chord_verifier=self._make_chord_verifier(),
            )
            self._feedback.reset()
        except Exception as e:
            print(f"Audio start failed: {e}")
            self._audio_enabled = False

    def _start_capture_only(self) -> None:
        """Start audio capture for signal monitoring (no matcher)."""
        try:
            from pickhero.audio.input import AudioCapture
            if self._audio_capture is None:
                self._audio_capture = AudioCapture(self._config)
            self._audio_capture.start()
        except Exception as e:
            print(f"Audio capture start failed: {e}")
            self._audio_enabled = False

    def _stop_audio(self) -> None:
        """Stop audio capture."""
        if self._audio_capture is not None:
            self._audio_capture.stop()

    def stop_audio(self) -> None:
        """Public method to stop audio (called on state transitions)."""
        self._stop_audio()
        self._audio_enabled = False
        if self._midi_player is not None:
            self._midi_player.close()
            self._midi_player = None

    # -- MIDI backing track --

    def _init_midi_player(self, backing_track: BackingTrack) -> None:
        """Create and open MidiPlayer. Silently continues if MIDI unavailable."""
        try:
            player = MidiPlayer(backing_track)
            if player.open():
                player.set_muted(self._backing_muted)
                self._midi_player = player
            else:
                player.close()
        except Exception as e:
            print(f"MIDI player init failed: {e}")

    def _toggle_backing(self) -> None:
        """Toggle backing track mute on/off."""
        if self._midi_player is None:
            return
        self._backing_muted = not self._backing_muted
        self._midi_player.set_muted(self._backing_muted)
