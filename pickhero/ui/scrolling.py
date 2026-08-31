"""Scrolling note display for the playing screen.

Renders 6 string lanes with notes scrolling right-to-left, synchronized
to a playback clock. Optionally captures audio and shows hit/miss feedback.
"""

from __future__ import annotations

import functools
import math
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import pygame

from pickhero.audio.midi_playback import BackingTrack, MidiPlayer
from pickhero.audio.mp3_playback import Mp3Player, pick_audio_file
from pickhero.audio import timestretch
from pickhero import config as config_module
from pickhero.config import (MAX_GATE_DB, MAX_LATENCY_OFFSET_MS,
                             MAX_MP3_RATE, MIN_GATE_DB, MIN_MP3_RATE, Config)
from pickhero.matcher import (FINE_MS, STRING_MIN_SAMPLES, MatchType,
                              NoteMatcher)
from pickhero.audio import output
from pickhero import practice_log
from pickhero.progress import ProgressTracker
from pickhero.tabs.chords import name_chord
from pickhero.tabs.timeline import NoteEvent, Timeline
from pickhero.audio.note_utils import (
    freq_to_cents_deviation, is_standard_tuning, midi_to_name, tuning_name,
    tuning_notes,
)
from pickhero.ui.colors import (
    OPEN_STRING_COLOR,
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
STRING_THICKNESS = (1, 2, 3, 4, 5, 6)

# The three lowest are wound and read as brass rather than steel. It is the
# cue that lets the low half of the board be told apart without reading
# anything, which is the whole point of drawing a fretboard instead of rows.
WOUND_STRINGS = 3
WOUND_TINT = (196, 158, 92)
PLAIN_TINT = (208, 212, 220)

# Bar line. Barely above the board and slightly COOLER than it, which is what
# makes it read as a line on the wood rather than as an object of its own.
# It was drawn as a lit nickel-silver wire and that was too loud: the eye went
# to it instead of to the notes, which is the opposite of what a landmark is
# for. A landmark is noticed when looked for and not otherwise.
BAR_LINE_COLOR = (52, 54, 66)

# No two bar lines closer together than this. A fast song puts bars a few
# pixels apart and the board turns into a picket fence behind the notes, so
# past this every second bar is drawn, then every fourth.
MIN_BAR_LINE_GAP_PX = 90

# How far the hit line stands proud of the board, top and bottom. Flush with
# the edge it is one more vertical among the fret wires; past it, it is the
# thing the board scrolls through.
HIT_LINE_OVERHANG_PX = 14

# Gap left between a sustain and the next note, as a fraction of note height.
# A capsule is drawn from the head's left edge to one radius before the next
# note's centre, so back-to-back notes abut instead of merging into a ribbon —
# without this, a run of eighths renders as one unbroken bar.
SUSTAIN_GAP_FRACTION = 0.18

# -- Technique marks -------------------------------------------------------
# Bends, slides and legato are drawn the way Yousician draws them: a white
# line inside the note showing what the pitch does, and a small dark disc
# above the note's leading edge naming the technique. Both stay within the
# note's own lane, which a six-lane layout requires -- a curve arcing out of
# the lane reads as a note on the neighbouring string.
TECHNIQUE_WIDTH_PX = 4
BADGE_RADIUS_HEADS = 0.3
BADGE_LIFT_HEADS = 0.75   # as a fraction of the badge radius
# Where inside the note the bend curve starts and how deep it goes, as
# fractions of the head.
BEND_BASE_FRACTION = 0.42       # below centre, so a rise has room
BEND_DEPTH_FRACTION = 0.42
BEND_INSET_FRACTION = 0.5       # keeps the curve off the rounded ends
BEND_MIN_WIDTH_HEADS = 0.9      # a bend on a staccato note still needs room
BEND_DEPTH_HEADS = 0.62
# A "full" bend in guitar notation is a whole step, i.e. two semitones. The
# drawn depth is measured against that, so 1/2 looks half as deep.
FULL_BEND_SEMITONES = 2.0
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
# The hammer-on / pull-off arc bows up between the two fret numbers, the way
# tab notation ties them.
LEGATO_ARC_HEADS = 0.34
LEGATO_BASE_FRACTION = 0.3
LEGATO_ARC_STEPS = 12

# -- Muting ----------------------------------------------------------------
# A palm-muted note is choked short of whatever length the tab wrote for it,
# so drawing its full sustain promises a ring that will not happen. Capped at
# this many heads instead: long enough to tell a chug from a dead note, short
# enough that a muted riff reads as the stubs it sounds like.
PALM_MUTE_MAX_HEADS = 1.3
# A palm-muted run is marked once, at its start, the way paper tab writes
# "P.M." and dashes it onward -- a disc over every note of a muted riff hides
# the music behind its own labelling. A silence longer than this starts a new
# run, so the badge comes back when the riff does.
PALM_MUTE_RUN_GAP_MS = 1200.0

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
# Look-ahead a player needs to read a fret number and get a finger there. Below
# this the display shrinks its notes to buy more time rather than scrolling
# faster; a dense tab otherwise arrives at several hundred pixels a second.
READABLE_WINDOW_MS = 4000.0
# The smallest fret digit worth calling readable, in pixels of type.
#
# This is the number the whole size question is really about. Measured on the
# app as it stood: a ONE-digit fret was drawn at 42 px and a TWO-digit one at
# 27 px -- 64 % of it -- because the head was 33 px wide and 49 px tall, and
# a number is wider than it is tall. "11 or 12?" in a fast solo is that 27 px.
#
# So the head's WIDTH is now sized for the widest label the song contains,
# not for a single digit. It costs look-ahead, and that is the honest trade:
# a number you cannot read is not worth the second of warning it bought.
MIN_FRET_DIGIT_PX = 34.0

# Smallest note head to shrink to, for a song whose frets are all one digit.
MIN_HEAD_PX = 26.0

# Turning a wanted digit size into the head width that produces it, given
# _fret_font's own arithmetic (width / digits / 0.55 * 0.9).
def _head_px_for_digits(digit_px: float, digits: int) -> float:
    return digit_px * 0.55 * max(1, digits) / 0.9

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

# How far the MIDI backing can be shifted against the notes, and its steps.
# Ten seconds is far more than a synth and a sound card need -- that is tens of
# milliseconds -- but the player asked for it, and the reason holds: the tab
# and the backing do not always start on the same beat, and a range chosen
# from what the hardware needs is a range chosen from the wrong thing.
MAX_BACKING_OFFSET_MS = 10_000.0
BACKING_OFFSET_STEP_MS = 10.0
# Ten seconds at 10 ms a press is a thousand presses, so the wide range needs
# a wide step to go with it.
BACKING_OFFSET_COARSE_MS = 1000.0

# The recording gets its own, far wider range. The MIDI backing is generated
# from the same timeline as the notes, so it only ever needs the tens of
# milliseconds a synth and a sound card add. A recording is a different piece
# of music that happens to contain the same song: it can have a count-in, an
# intro, a spoken word, or several seconds of studio silence before the first
# beat, and none of that is knowable in advance. Half a second was not enough
# to reach the first note of a real track.
# Eight minutes, because a tab is not always the whole song: a GP file holding
# only the solo has to be lined up against a recording that plays four minutes
# of music before it. Thirty seconds reached the first beat of a track and
# nothing further in.
MAX_MP3_OFFSET_MS = 480_000.0
# Three steps, because no single one serves all three jobs: 10 ms is what a
# sync is judged in, a second is what an intro is worth, and reaching four
# minutes at a second a press is four minutes of pressing.
MP3_OFFSET_STEP_MS = 10.0
MP3_OFFSET_COARSE_MS = 1000.0
MP3_OFFSET_JUMP_MS = 10_000.0

# When to stop believing the recording is following the song. A decoder that
# cannot seek into a file accepts play(start=...) without complaint and starts
# from the top anyway, so the only evidence is the gap that will not close --
# and "the backing ignores the arrow keys" is otherwise indistinguishable from
# "you were paused", which is a whole round trip to find out.
MP3_STUCK_DRIFT_MS = 250.0
# Long enough that the sync has had at least one correction attempt at it.
MP3_STUCK_FOR_MS = 3000.0

# Input level advice. A level at or below this has not been measured yet --
# the meter reads -120 dB before any audio arrives.
SIGNAL_UNKNOWN_DB = -119.0
# An RMS this high over a 512-sample hop means the peaks are already against
# the ceiling, and a clipped waveform has no period for YIN to find.
CLIPPING_DB = -8.0
# How loud the loudest hop has to be for the detector to keep its grip.
# Measured, not guessed: the player's own play-along take was attenuated in
# steps and read back through the real detector, which gives the level at
# which pitch accuracy starts to rot. In the same units the HUD shows (RMS
# over one 512-sample hop):
#
#   loudest hop   -20   -32   -38   -44   -50   -56 dB
#   heard right    96    96    91    83    52     9 %
#
# So the knee sits around -38 and the collapse below -44. Note what fails
# first: strikes keep arriving, they just carry the WRONG PITCH -- which is
# why "few strikes" is the wrong thing to look for, and why the completion
# screen counts strikes heard next to notes landed.
QUIET_PEAK_DB = -40.0
# How far the loudest playing must clear the gate before the gate itself is
# the thing eating the notes. A strike decays fast, so most of a note sits
# well below its own peak.
QUIET_MARGIN_DB = 12.0
# How far the quietest moment must stay UNDER the gate before background hum
# starts firing onsets of its own.
NOISE_MARGIN_DB = 6.0


def gate_band(peak: float, floor: float) -> tuple[float, float]:
    """The window a noise gate may sit in, as (lowest, highest).

    Above the room by NOISE_MARGIN_DB so hum does not fire onsets of its own,
    and below the playing by QUIET_MARGIN_DB so a decaying note survives --
    capped by `MAX_GATE_DB`, which is the level at which the DETECTOR gives
    up and therefore the point past which gating wins nothing.

    The band can be EMPTY (lowest > highest) and that is a real state, not an
    error: a hot, compressed signal has less than NOISE_MARGIN_DB +
    QUIET_MARGIN_DB of range to put a gate in. It has to be a state the
    advice can express, because for one cycle it was not -- the two pieces of
    advice named keys that undo each other, and with no gate able to satisfy
    both, the panel asked for X, then C, then X for ever. Which is what the
    player saw, and they pressed C until the gate reached the old ceiling and
    the clean half of the song stopped being heard.
    """
    return floor + NOISE_MARGIN_DB, min(peak - QUIET_MARGIN_DB, MAX_GATE_DB)


def suggested_gate_db(peak: float, floor: float) -> float:
    """A gate inside the band, on the 5 dB grid the X and C keys move in.

    As LOW in the band as still clears the room: the two failures are not
    each other's equals. A gate under the room costs spurious onsets, which
    the confidence filter and the matcher's candidate search already throw
    away; a gate over the playing costs the strikes themselves, and a strike
    that never arrives cannot be recovered by anything downstream.
    """
    lowest, highest = gate_band(peak, floor)
    target = math.ceil(lowest / 5.0) * 5.0
    if target > highest:
        target = math.floor(highest / 5.0) * 5.0
    return max(MIN_GATE_DB, min(MAX_GATE_DB, target))
# Per frame, so one loud accident does not fix the advice in place for the
# rest of the song.
LEVEL_DECAY_DB = 0.05

# The room is what the microphone hears while the song is NOT running, which
# is the only moment it can be read: a low percentile of a take that is being
# PLAYED is not the room. Measured across one session's reference takes, the
# 2nd percentile ranged from -35 dB on a dense passage with no gaps to -94 dB
# on a sparse one, against a recorded room of -73 -- so a percentile says how
# busy the playing was, not how quiet the room is.
#
# A median over the most recent readings, so a session that changes (a fan, a
# different guitar) is followed and one frame of the guitar being put down is
# not. At 60 frames a second the minimum is about a second and a half.
ROOM_WINDOW = 300
ROOM_SAMPLES = 90

# Auto-sync confidence. Scatter does not invalidate the median — a player is
# simply not a metronome — it only means more strikes are needed before the
# median is trustworthy. Refuse outright only when the scatter is so wide that
# no systematic offset is visible in it at all.
AUTO_SYNC_MIN_SAMPLES = 8
# The spread thresholds that used to live here are gone on purpose. They were
# a second opinion on the samples the timing report already judges, and a
# second opinion is only ever a chance for the two to disagree. Both K and the
# HUD line now ask the report.


# 60 FPS is a 16.7 ms budget for everything a frame does.
FRAME_BUDGET_MS = 1000.0 / 60.0

# The longest a single frame may move the song. Fifteen frames' worth: beyond
# that nothing was drawn and nothing was heard, so charging the song for it
# only teleports the picture.
MAX_FRAME_STALL_S = 0.25

# How far apart the two sync points must be. The offset is dialled in 10 ms
# steps, so one keypress over a short span is a large speed error: over 30 s
# it is 0.03 %, against the ~1 % the correction is for; over 5 s it would be
# 0.2 %. The start and the end of the song are what this wants.
MIN_SYNC_SPAN_MS = 30_000.0

# How long seeks have to stop arriving before the recording follows them.
# A held arrow key repeats every 40 ms and every repeat used to be a
# play(start=), which decodes the file up to that point -- 25 of them a
# second on the frame's own thread. The FIRST seek of a burst is still
# immediate, so a single press and a loop turn are as sharp as they were;
# only a run of them is collapsed into one.
MP3_SEEK_SETTLE_S = 0.15

# What Ctrl + arrow moves. Half a minute is a section of a song -- a verse, a
# chorus -- which is the unit somebody skipping through one actually thinks in.
SEEK_SECTION_MS = 30_000.0
# Pressing back from just after a bar line must reach the PREVIOUS bar, not
# stand still on the one just crossed.
BAR_SNAP_MARGIN_MS = 30.0


class _CachedFont:
    """A font that keeps the text surfaces it has already drawn.

    Measured on the playing screen: one frame rasterises **62 text surfaces**
    and that was **79 % of the whole frame** -- against 8 % for drawing the
    notes. The footer alone was 66 %, and the footer is the list of keyboard
    shortcuts, which never changes at all. Almost none of the rest changes
    either: the song title, the tempo, the tuning, the hit window. Only the
    clock does, once a second.

    So the surface is kept and blitted again. Wrapping the font rather than
    every call site means the ~180 `font.render(...)` calls in this file are
    untouched, and anything added later gets the cache without knowing.

    The cache is cleared wholesale when it grows past `MAX_ENTRIES` rather
    than evicted one at a time: the only text that really varies is the clock,
    a re-render costs a fraction of a millisecond, and an LRU here would be
    bookkeeping to save nothing.
    """

    MAX_ENTRIES = 256

    __slots__ = ("_font", "_cache")

    def __init__(self, font: pygame.font.Font) -> None:
        self._font = font
        self._cache: dict = {}

    def render(self, text, antialias=True, color=(255, 255, 255),
               background=None):
        if background is not None:
            # Rare, and a second key dimension for nothing.
            return self._font.render(text, antialias, color, background)
        key = (text, bool(antialias), tuple(color))
        surface = self._cache.get(key)
        if surface is None:
            if len(self._cache) >= self.MAX_ENTRIES:
                self._cache.clear()
            surface = self._font.render(text, antialias, color)
            self._cache[key] = surface
        return surface

    def __getattr__(self, name):
        # size(), get_height(), get_linesize() and the rest, unchanged.
        return getattr(self._font, name)


@functools.lru_cache(maxsize=96)
def _get_font(name: str, size: int, bold: bool = False) -> "_CachedFont":
    """Try to load a system font with fallbacks.

    Cached because SysFont is a font-file lookup every call, and the playing
    screen asks for the same handful of fonts on every frame -- and because
    the cache of drawn text lives on the object it returns, so handing back a
    fresh one each time would throw that away.
    """
    for family in (name, "Courier New", "monospace"):
        font = pygame.font.SysFont(family, size, bold=bold)
        if font:
            return _CachedFont(font)
    return _CachedFont(pygame.font.Font(None, size))


def clear_font_cache() -> None:
    """Drop every cached font and every surface drawn with one.

    Both belong to ONE pygame session. A Font kept across `pygame.quit()` is a
    dangling pointer and rendering with it segfaults -- verified, not assumed,
    and it is why this function exists rather than being left to chance. The
    app calls it when it starts a session; the test suite calls it between
    tests, several of which run an init/quit cycle of their own.
    """
    _get_font.cache_clear()


def format_time(ms: float) -> str:
    """Format milliseconds as M:SS."""
    total_seconds = max(0, int(ms / 1000))
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"


def _clock_text(ms: float) -> str:
    """A song position as a player reads it off a transport."""
    total = max(0, int(ms // 1000))
    return f"{total // 60}:{total % 60:02d}"


def _offset_text(ms: float) -> str:
    """A backing offset in the unit it is actually judged in.

    Milliseconds while it is a sync, seconds while it is an intro, and minutes
    and seconds once the tab is only the solo of a longer recording -- where
    "-192.00 s" is a number nobody can check against a player's time display.
    """
    sign = "-" if ms < 0 else "+"
    size = abs(ms)
    if size < 1000:
        return f"{sign}{int(size)} ms"
    if size < 60_000:
        return f"{sign}{size / 1000:.2f} s"
    return f"{sign}{int(size // 60_000)}:{size % 60_000 / 1000:04.1f} min"


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
                 guide_track: BackingTrack | None = None,
                 progress_tracker: ProgressTracker | None = None,
                 song_key: str = ""):
        self._timeline = timeline
        self._visible_beats = visible_beats
        self._hit_zone_fraction = hit_zone_fraction
        self._config = config or Config()

        # Practice speed belongs to the song, not to the app. A song never
        # slowed down opens at full speed, whatever the last one needed.
        # The parameter, not self._song_key: that is not assigned until fifty
        # lines further down, and reading it here quietly gave every song the
        # speed of no song at all.
        getter = getattr(self._config, "tempo_factor_for", None)
        self._tempo_factor = (getter(song_key) if getter
                              else max(0.5, min(1.0, self._config.tempo_factor)))

        # Where the audio clock and the song clock were last agreed to be the
        # same moment. A strike is stamped in recorded time, which runs at
        # real speed; the song runs at a fraction of it. Multiplying one by
        # the other is only correct from a common origin, so changing the
        # practice speed has to move that origin -- otherwise every strike
        # after the change is mis-stamped by (elapsed x change), which grows
        # for the rest of the song and cannot be corrected by K.
        self._audio_anchor_ms: float = 0.0       # audio clock at the anchor
        self._audio_anchor_song_ms: float = 0.0  # song clock at the anchor

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
        self._head_h_px: float | None = None
        self._fret_digits: int = 2
        # Where the "PM" badges go. Computed from the whole song, not from the
        # notes currently on screen: a run crossing the edge of the view would
        # otherwise be re-labelled every time its first note scrolled off.
        self._palm_mute_starts = self._palm_mute_run_starts(self._timeline.notes)

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
        # A ring of recent frame times, for the run log. Bounded: a long
        # session must not turn a diagnostic into a memory leak.
        # (when, name) for every chord change. Built once per song.
        self._chord_names: list[tuple[float, str]] = []
        self._frame_ms: list[float] = []
        # A seek collapsed because more were still arriving, and when the
        # last one did. See _seek_mp3.
        # The file chooser blocks for seconds, so it is opened one frame
        # AFTER the key, once the note saying so has been drawn.
        self._mp3_dialog_due = False
        self._mp3_dialog_armed = False
        self._mp3_pending_seek_ms: float | None = None
        self._mp3_last_seek_at = float("-inf")
        self._loop_start_ms: float | None = None
        self._loop_end_ms: float | None = None
        self._loop_enabled: bool = False

        # Progress tracking
        self._progress_tracker = progress_tracker
        # The practice diary: real seconds with the song running, and every
        # strike the microphone heard. Both survive a seek, a loop and a
        # tempo change, which the matcher's own counters do not -- it is reset
        # by all three.
        self._session_started = practice_log.now_iso()
        self._session_seconds = 0.0
        self._session_strikes = 0
        self._session_written = False
        self._song_key = song_key
        self._song_completed = False
        self._is_new_best = False
        self._recommendations: list[str] = []

        # MIDI backing track
        self._midi_player: MidiPlayer | None = None
        # The written part of the track being PLAYED, as a guide to hear.
        # A separate player rather than a second toggle on the same one,
        # because the two answer different questions -- "what does the band
        # play" and "what am I supposed to play" -- and a player learning a
        # solo wants the second without the first. The MIDI output is shared,
        # so this costs no extra device.
        self._guide_player: MidiPlayer | None = None
        self._guide_muted = True
        self._backing_muted = not self._config.backing_track_enabled
        # Off unless asked for: the whole point of the app is that the player
        # produces this part, and starting with it playing would teach the
        # wrong thing on the first run.
        self._guide_muted = not getattr(self._config, "guide_track_enabled", False)
        # A recording playing alongside the MIDI backing, not instead of it.
        # Hearing both at once is how the recording gets lined up against the
        # click, which is the only way its encoder padding can be found.
        self._mp3_player: Mp3Player | None = None
        self._mp3_muted = not getattr(self._config, "mp3_backing_enabled", True)
        self._mp3_note: str = ""
        # When the recording first fell behind, or None while it is keeping up.
        self._mp3_stuck_since_ms: float | None = None
        # Practice speed below full needs a stretched copy of the recording,
        # which takes seconds to make. It is built on a thread and swapped in
        # when it is ready: the app must not freeze on a tempo key.
        self._mp3_stretch_thread: threading.Thread | None = None
        self._mp3_stretch_wanted: float | None = None
        # (speed, the recording it was made from, the stretched file). The
        # recording is part of the key because picking a new file mid-session
        # must not inherit the last one's stretch.
        self._mp3_stretch_done: tuple[float, str, str] | None = None
        self._mp3_stretch_failed: tuple[float, str, str] | None = None
        # How far the build has got, 0 to 1. A whole song is five to twenty
        # seconds of work and the player hears nothing for all of it -- "one
        # moment" with nothing moving is indistinguishable from broken.
        self._mp3_stretch_progress = 0.0
        # The build the loaded source was made for, so a rate change is seen
        # even though it leaves time_scale untouched.
        self._mp3_loaded_build: float | None = None
        # The first of the two places the player lines the recording up at,
        # as (song ms, offset ms). Not remembered across songs: it describes
        # one act of syncing, not a setting.
        self._sync_anchor: tuple[float, float] | None = None
        self._sync_key_held: bool = False
        # What the last sync did, kept on screen rather than announced once:
        # the player is navigating a four-minute song between the two points
        # and an expiring note is gone long before the second one is set.
        self._sync_lines: list[str] = []
        if backing_track is not None and len(backing_track) > 0:
            self._init_midi_player(backing_track)
        if guide_track is not None and len(guide_track) > 0:
            self._init_guide_player(guide_track)
        self._load_mp3_for_song()

        # Difficulty filter
        self._max_fret: int = self._config.max_fret
        self._active_strings: list[bool] = list(self._config.active_strings)

        # Signal level meter
        self._signal_db: float = -120.0
        self._signal_db_smooth: float = -120.0
        # Loudest and quietest level heard recently, so the HUD can say what
        # to do about the input rather than only what it is.
        self._signal_peak_db: float = SIGNAL_UNKNOWN_DB
        self._signal_floor_db: float = 0.0
        # What the room sounds like, and the loudest thing this run has heard.
        # The peak above decays on purpose; this one does not, because a gate
        # is judged against the whole run rather than the last two seconds.
        self._room_samples: deque[float] = deque(maxlen=ROOM_WINDOW)
        self._loudest_db: float = SIGNAL_UNKNOWN_DB
        self._auto_gate: bool = bool(getattr(self._config.audio, "auto_gate", True))
        # Every level seen while the song ran, for the run log.
        self._level_samples: list[float] = []

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
        self._show_timing: bool = False
        self._timing_export_note: str = ""
        self._run_log_note: str = ""
        # What just happened, for the few seconds it is still news. The run
        # log's own note was drawn ONLY on the completion screen, so pressing
        # D in the middle of a song wrote the file and said nothing at all --
        # which is what the player reported as "D does nothing". A feature
        # that cannot be seen working is indistinguishable from one that does
        # not work, and this project has now shipped that fault four times.
        self._status_note: str = ""
        self._status_note_until: float = 0.0
        # What the picture's clock did against real time -- see the advance
        # in update(). Not reset by a seek or a loop: the question is what
        # the machine did over the whole sitting.
        self._clock_real_ms: float = 0.0
        self._clock_song_ms: float = 0.0
        self._clock_stalls: int = 0
        # Leaving a song reaches stop_audio more than once -- the app tears
        # the screen down and the state change calls it again -- and the
        # second call came after the recording was closed, so it wrote a
        # SECOND log missing every mp3 line. Two files a second apart, one
        # of them worse, is the run log arguing with itself.
        self._run_log_written: bool = False
        # Whether K has already been applied in this run. A residual after a
        # sync means something different to the player than an unmeasured one:
        # the first says "press K", the second says "press it again".
        self._sync_applied: bool = False

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
            # Only start audio capture when past count-in. If the stream is
            # already open -- which after a pause it now is -- re-anchoring is
            # the whole of what resuming needs, and it does not touch the
            # hardware. See _resume_audio.
            if self._audio_enabled and self._playback_ms >= 0:
                self._resume_audio()
            elif self._audio_enabled:
                # The count-in is starting. Open the input now so the room is
                # heard before the first note -- the automatic gate has no
                # other window, and this is the longest clean one a run gets.
                self._start_capture_only()
            if self._playback_ms >= 0:
                for player in self._midi_all():
                    player.seek(self._backing_ms(self._playback_ms))
            if self._mp3_player is not None and self._playback_ms >= 0:
                if self._mp3_plays() and self._mp3_pending_seek_ms is None:
                    if self._mp3_player.suspended:
                        self._mp3_player.set_suspended(False)
                    else:
                        self._seek_mp3(self._mp3_ms(self._playback_ms))
            # LAST, not first: opening a device or starting a recording can
            # take a moment, and the clock must start when the song does. Set
            # before them, that moment is charged to the song and the picture
            # jumps forward by it on the very next frame.
            self._last_tick = time.perf_counter()
        else:
            self._last_tick = None
            # The input device stays OPEN. Closing it here and opening it
            # again on resume is a real device open on Windows -- the same
            # thing that made every arrow key freeze the app for seconds, and
            # the space bar was still doing it twice per pause. Strikes that
            # arrive while the song stands still are dropped on resume.
            for player in self._midi_all():
                player.pause()
            if self._mp3_player is not None:
                self._mp3_player.set_suspended(True)

    def _seek_target_ms(self, event: pygame.event.Event, direction: int) -> float:
        """Where one arrow key press lands, by modifier.

        A beat is the right step for placing a loop marker and useless for
        reaching the chorus of a four-minute song: at 273 ms a beat that is
        nine hundred presses, and with key repeat at 40 ms it is half a minute
        of holding the key while the picture scrolls past. So the same ladder
        the backing-track offset already uses -- plain, Shift, Ctrl -- with
        each step chosen from what it is FOR: a beat to place a loop, a BAR to
        walk a phrase, half a minute to reach a section.

        Shift snaps to the bar line rather than adding a fixed number of
        beats, so it stays on the bars through a time-signature change and
        lands where the tab is drawn rather than near it.
        """
        now = self._playback_ms
        if event.mod & pygame.KMOD_CTRL:
            return now + direction * SEEK_SECTION_MS
        if event.mod & pygame.KMOD_SHIFT:
            starts = [m.start_ms for m in self._timeline.measures]
            if starts:
                # A margin, so pressing back from just after a bar line goes
                # to the PREVIOUS bar instead of standing still.
                if direction > 0:
                    later = [t for t in starts if t > now + BAR_SNAP_MARGIN_MS]
                    if later:
                        return later[0]
                else:
                    earlier = [t for t in starts if t < now - BAR_SNAP_MARGIN_MS]
                    if earlier:
                        return earlier[-1]
                    return 0.0
        return now + direction * self._ms_per_beat

    def position_ms(self) -> float:
        """Where the song is, in song milliseconds.

        Negative during the count-in, which callers that carry the position
        across a reload have to clamp rather than reproduce.
        """
        return self._playback_ms

    def seek(self, ms: float) -> None:
        """Seek to an absolute position in ms, clamped to [0, duration]."""
        self._playback_ms = max(0.0, min(ms, self._timeline.duration_ms))
        # Reaching the last bar put the completion screen up and nothing took
        # it down again, so an arrow key moved the song under a picture that
        # went on showing the score -- the player had to leave and start over
        # to hear the last bars a second time, which is exactly when a
        # recording is being synced. Moving off the end is leaving the
        # completion screen; the run it scored has already been written.
        if self._song_completed and self._playback_ms < self._timeline.duration_ms:
            self._song_completed = False
        if self._matcher:
            self._matcher.reset()
        self._feedback.reset()
        for player in self._midi_all():
            player.seek(self._backing_ms(self._playback_ms))
        if self._mp3_player is not None:
            self._seek_mp3(self._mp3_ms(self._playback_ms)
                           if self._mp3_plays() else -1.0)
        # The audio clock has to be told the song moved -- but NOT by closing
        # and reopening the input device, which is what this used to do. On
        # Windows that is a real device open, and doing it on every arrow key
        # and every loop turn froze the app for seconds at a time. Re-anchoring
        # is the same correction without touching the hardware.
        if self._audio_enabled and self._playing:
            self._reanchor_audio_clock()

    def is_playing(self) -> bool:
        return self._playing

    def set_tempo_factor(self, factor: float) -> None:
        """Set tempo scaling factor, clamped to [0.5, 1.0] and rounded to nearest 0.05."""
        factor = max(0.5, min(1.0, factor))
        factor = round(factor * 20) / 20  # round to nearest 0.05
        self._tempo_factor = factor
        # Both: the per-song value is what this song opens at next time, and
        # the plain one is what tools outside the app read to find out what
        # speed a take was played at.
        self._config.tempo_factor = factor
        setter = getattr(self._config, "set_tempo_factor_for", None)
        if setter is not None:
            setter(self._song_key, factor)
        self._config.save()
        # The song clock now runs at a different fraction of the audio clock,
        # so the point where the two were last equal has to be moved to now.
        self._reanchor_audio_clock()
        # The recording needs a copy stretched for the new speed. It stays
        # silent until that copy is there rather than playing on at a speed
        # the song has left.
        self._update_mp3()
        if self._matcher:
            self._matcher.reset()
        self._feedback.reset()

    def _reanchor_audio_clock(self) -> None:
        """Agree audio time and song time on the present moment.

        Strikes already waiting in the queue were stamped before the change
        and would be read with the new factor, so they are dropped: a handful
        of strikes at the moment the speed is touched, against every strike
        afterwards landing where it was played.
        """
        if self._audio_capture is None or self._matcher is None:
            return
        self._audio_capture.get_notes()
        self._audio_capture.get_strike_windows()
        self._audio_anchor_ms = self._audio_capture.elapsed_ms()
        self._audio_anchor_song_ms = self._playback_ms
        self._matcher.audio_offset_ms = (
            self._audio_anchor_song_ms
            - self._audio_anchor_ms * self._tempo_factor
            + self._sync_offset_song_ms()
        )

    def set_noise_gate_db(self, db: float) -> None:
        """Set the noise gate, clamped to the useful range and rounded.

        The clamp lives here and nowhere else, so every route to the gate --
        the X and C keys, the settings screen, a saved file -- lands in the
        same range. The ceiling used to be -20 dB, which is 30 dB inside the
        band where the gate deletes the quiet half of a song; see MAX_GATE_DB.
        """
        db = max(MIN_GATE_DB, min(MAX_GATE_DB, round(db)))
        self._noise_gate_db = db
        self._config.audio.noise_gate_db = db
        if self._audio_capture is not None:
            self._audio_capture.set_noise_gate_db(db)
        self._config.save()

    def update(self) -> None:
        """Advance playback clock by real elapsed time."""
        if self._mp3_dialog_armed:
            # The note has been on screen for a frame; now we may block.
            self._mp3_dialog_due = False
            self._mp3_dialog_armed = False
            self._open_mp3_dialog()

        # Update signal level meter and tuner even when paused (so user can verify signal)
        if self._audio_capture is not None:
            raw_db = self._audio_capture.get_signal_db()
            self._signal_db = raw_db
            self._signal_db_smooth = self._signal_db_smooth * 0.7 + raw_db * 0.3
            self._track_levels(raw_db)
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
            # The input device stays open through a pause now, so whatever it
            # hears has to be thrown away here. Both queues are unbounded and
            # a strike window holds 341 ms of audio: a long pause with the
            # guitar in hand would otherwise fill memory with sound belonging
            # to no moment in the song.
            if self._audio_capture is not None:
                self._audio_capture.get_notes()
                self._audio_capture.get_strike_windows()
            # Still worth a look: a stretched copy that lands while the song
            # is paused has to be swapped in, and the line that says how far
            # along it is has to keep moving. Pausing does not stop the work,
            # and _update_mp3 keeps the recording itself silent.
            self._update_mp3()
            return

        now = time.perf_counter()
        prev_ms = self._playback_ms
        if self._last_tick is not None:
            # A frame that took longer than this is a machine that stalled --
            # a decoder, a device open, the operating system. Advancing the
            # song by the whole of it scrolls a bar of music past uncredited
            # and lands the picture somewhere the player never saw, which is
            # the "it stands still and then jumps" they reported. Losing the
            # time is the cheaper of the two: the recording is pulled back
            # into line by the ordinary sync a frame later.
            raw_elapsed = now - self._last_tick
            real_elapsed = min(raw_elapsed, MAX_FRAME_STALL_S)
            # The picture's own clock, measured against the wall it is
            # supposed to be keeping. A player reporting the notes falling
            # behind the sound is reporting exactly this ratio, and without it
            # the app cannot be told apart from the recording running away.
            # Uncapped on one side, what was actually spent on the other, so
            # the difference is the time the cap discarded.
            self._clock_real_ms += raw_elapsed * 1000.0
            self._clock_song_ms += real_elapsed * 1000.0
            if raw_elapsed > MAX_FRAME_STALL_S:
                self._clock_stalls += 1
            self._playback_ms += real_elapsed * 1000.0 * self._tempo_factor
            # REAL seconds, not song time: at 70 % speed the song is shorter
            # than the time you spent on it, and it is the time you spent that
            # a practice diary is about.
            self._session_seconds += real_elapsed
        self._last_tick = now

        # Wait mode: freeze if there are pending notes the player hasn't hit yet
        if (self._wait_mode and self._audio_enabled
                and self._playback_ms >= 0 and self._matcher is not None):
            if self._matcher.has_pending_notes_at(self._playback_ms):
                self._playback_ms = prev_ms
                self._last_tick = now
                self._wait_mode_frozen = True
                for player in self._midi_all():
                    player.pause()
            elif self._wait_mode_frozen:
                self._wait_mode_frozen = False
                for player in self._midi_all():
                    player.seek(self._backing_ms(self._playback_ms))

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
                for player in self._midi_all():
                    player.seek(0)

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
            # Pinned timestamps carry no latency information -- nor any
            # information about how long a bend was held, since every reading
            # would claim the same millisecond.
            self._matcher.record_timing_samples = not self._wait_mode_frozen
            self._matcher.record_contour = not self._wait_mode_frozen
            self._session_strikes += sum(1 for d in detected if d.note.is_onset)
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
        if self._playback_ms >= 0:
            for player in self._midi_all():
                player.update(self._backing_ms(self._playback_ms))
        self._update_mp3()

        # Loop check — jump back to start marker when reaching end marker
        # (no count-in on loop)
        if (self._loop_enabled and self._loop_end_ms is not None
                and self._loop_start_ms is not None
                and self._playback_ms >= self._loop_end_ms):
            for player in self._midi_all():
                player.pause()
            self._playback_ms = self._loop_start_ms
            self._last_tick = time.perf_counter()
            if self._matcher:
                self._matcher.reset()
            self._feedback.reset()
            for player in self._midi_all():
                player.seek(self._backing_ms(self._loop_start_ms))
            if self._mp3_player is not None and self._mp3_plays():
                self._mp3_player.seek(self._mp3_ms(self._loop_start_ms))
            if self._audio_enabled and self._playing:
                self._reanchor_audio_clock()
            return

        if self._playback_ms >= self._timeline.duration_ms:
            self._playback_ms = self._timeline.duration_ms
            self._playing = False
            self._last_tick = None
            for player in self._midi_all():
                player.pause()
            if self._mp3_player is not None:
                self._mp3_player.pause()
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
                    # Written whether or not anything scored: a run that
                    # scored nothing is the one most worth reading.
                    self._export_run_log()
                elif not self._audio_enabled:
                    # Auto-scroll (passive) completion
                    self._weakest_sections = []
                    self._song_completed = True

    def handle_event(self, event: pygame.event.Event):
        """Handle input.

        Returns 'menu' to go back, ('select_track', index) when a track was
        picked, else None.
        """
        if event.type == pygame.KEYUP:
            # Key repeat is 300 ms then 40 ms, and a KEYDOWN from a repeat is
            # indistinguishable from a real press. For most keys that is what
            # repeat is FOR; for Shift+S it is a disaster, because the two
            # presses mean different things -- the player's second point was
            # taken, the rate was set, and the repeat 40 ms later opened a new
            # point 1 on top of it. Which is exactly what they saw. Requiring
            # the key to come up is exact where a timeout would be a guess.
            if event.key == pygame.K_s:
                self._sync_key_held = False
            return None
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
            self.seek(self._seek_target_ms(event, -1))
        elif event.key == pygame.K_RIGHT:
            self.seek(self._seek_target_ms(event, +1))
        elif event.key == pygame.K_HOME:
            self.seek(0)
        elif event.key == pygame.K_a:
            if event.mod & pygame.KMOD_SHIFT:
                self._reopen_output()
            else:
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
        elif event.key == pygame.K_s and event.mod & pygame.KMOD_SHIFT:
            if self._sync_key_held:
                return None                    # a repeat, not a second press
            self._sync_key_held = True
            if event.mod & pygame.KMOD_CTRL:
                self._clear_sync_rate()
            else:
                self._set_sync_point()
        elif event.key == pygame.K_b:
            if event.mod & pygame.KMOD_SHIFT:
                self._toggle_guide_track()
            else:
                self._toggle_backing()
        elif event.key == pygame.K_x:
            self._take_gate_by_hand()
            self.set_noise_gate_db(self._noise_gate_db - 5)
        elif event.key == pygame.K_c:
            # C is the key that walked this player's gate to the old ceiling,
            # five decibels at a time, on advice the app kept repeating --
            # see gate_band. set_noise_gate_db is what bounds it.
            self._take_gate_by_hand()
            self.set_noise_gate_db(self._noise_gate_db + 5)
        elif event.key == pygame.K_t:
            self._cycle_theme()
        elif event.key == pygame.K_f:
            self._cycle_fret_limit()
        elif event.key == pygame.K_d:
            self._export_run_log()
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
        elif event.key in (pygame.K_n, pygame.K_m):
            self._nudge_backing(1 if event.key == pygame.K_m else -1, event.mod)
        elif event.key == pygame.K_u:
            if event.mod & pygame.KMOD_SHIFT:
                self._choose_mp3_backing()
            else:
                self._toggle_mp3_backing()
        elif event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
            self._adjust_scroll_factor(SCROLL_FACTOR_STEP)
        elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self._adjust_scroll_factor(-SCROLL_FACTOR_STEP)
        elif event.key == pygame.K_l:
            self._loop_weakest_section()
        elif event.key == pygame.K_h:
            self._show_help = not self._show_help
        elif event.key == pygame.K_y:
            if event.mod & pygame.KMOD_SHIFT:
                self._export_timing_samples()
            else:
                self._show_timing = not self._show_timing
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
        self._draw_chord_names(surface, layout)
        self._draw_hud(surface, layout)

        if self._show_timing:
            self._draw_timing_overlay(surface, layout)
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

    def _fret_font(self, radius: float, half_h: float | None = None,
                   digits: int = 2) -> pygame.font.Font:
        """Font sized to the room the head actually has, cached.

        Both dimensions, not just the radius. A head squeezed narrow by a
        dense song is still full height, and a number sized on the width
        alone throws that height away -- on a real 135 BPM song that is the
        difference between a 14 px digit and a 20 px one, in a note that was
        already hard to read.

        Cached because building a font per note per frame is far too slow.
        """
        by_width = radius * 2 * 0.9 / max(1, digits) / 0.55
        by_height = (half_h * 2 * 0.86) if half_h else radius * 1.1
        size = max(9, int(min(by_width, by_height)))
        font = self._fret_fonts.get(size)
        if font is None:
            # Bold. The digit sits on a saturated colour with a dark outline
            # around it, and a thin stroke is the first thing to disappear at
            # speed -- which is exactly when the fret number matters most.
            font = _get_font("consolas", size, True)
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

    def _min_head_px(self, layout: _Layout) -> float:
        """How narrow a head may get before the fret number stops reading.

        A two-digit fret needs roughly twice the width of a one-digit one to
        show the same size of type, and the head was squeezed to a single
        digit's worth for every song. Never wider than the lane: past that the
        head would be wider than tall for no gain, and the height is free.
        """
        wanted = _head_px_for_digits(MIN_FRET_DIGIT_PX, self._fret_digits)
        return min(max(MIN_HEAD_PX, wanted), layout.note_h)

    def _recompute_scroll_speed(self, layout: _Layout | None = None) -> None:
        """Pick this song's one scroll speed and one note size.

        Both are set per song rather than per frame: a speed that moves while
        the song plays makes every note on screen visibly stretch and squeeze,
        and a size that varies note by note is the same problem in miniature.

        Speed comes first. A tab can only be read so fast no matter how dense
        the music is, so once the tightest passage would push past that limit
        the notes shrink toward the smallest head that still shows a two-digit
        fret, instead of the tab scrolling faster and faster. That paragraph
        described the intent for a while before the code did it: heads stayed
        full size and dense songs simply scrolled quicker, down to 1.5 s of
        warning.
        """
        layout = layout or self._last_layout
        if layout is None or layout.usable_width <= 0:
            return

        # FIRST, because the head width is sized for it below. Every fret
        # number in the song is sized for the widest one in it, so they are
        # all the same size: sizing each to its own label makes a lone "5"
        # tower over the "15" beside it, which reads as emphasis the music
        # never asked for.
        frets = [len(str(n.fret)) for n in self._timeline.notes
                 if self._note_passes_filter(n)]
        self._fret_digits = max(frets) if frets else 2

        head = layout.note_h
        spacing = self._spacing_percentile(SPACING_PERCENTILE)

        # The window follows from the note size: however much time fits on
        # screen once every note has its room is how much gets shown.
        window = BASE_VISIBLE_WINDOW_MS
        if spacing and spacing > 0:
            per_head = head * (1.0 + SUSTAIN_GAP_FRACTION)
            window = spacing * layout.usable_width / per_head

            # Full-size notes on a dense song buy so little look-ahead that
            # the fret numbers arrive unreadable -- canon.gp5 came out at
            # 1.5 s of warning and 683 px/s, which is a note crossing the
            # screen faster than it can be read, never mind fingered. Trading
            # head size for time is the only currency available, and it is a
            # trade the display is allowed to make: what it must never do is
            # resize notes WHILE scrolling, and this is decided once per song.
            # The floor is the smallest head a two-digit fret still fits in.
            if window < READABLE_WINDOW_MS:
                needed = (spacing * layout.usable_width
                          / (READABLE_WINDOW_MS * (1.0 + SUSTAIN_GAP_FRACTION)))
                head = max(self._min_head_px(layout), min(head, needed))
                window = (spacing * layout.usable_width
                          / (head * (1.0 + SUSTAIN_GAP_FRACTION)))

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
        # The head is squeezed HORIZONTALLY, by how close the notes sit in
        # time. Nothing squeezes it vertically -- the lane is as tall as it
        # ever was -- so a shrunken head leaves half its lane empty for
        # nothing. Measured on a real song at 135 BPM with sixteenths: the
        # head is at its 26 px floor inside a 56 px lane, 53 % of the height
        # unused. Keeping the full height costs no look-ahead at all, because
        # look-ahead is bought and sold in width.
        self._head_h_px = max(head, layout.note_h)
        self._chord_names = self._build_chord_names()
        self._scroll_speed_signature = self._filter_signature()

    def _build_chord_names(self) -> list[tuple[float, str]]:
        """(when, name) for every chord CHANGE in the song.

        At the change, not on every beat: a name repeated over eight bars of
        the same chord is eight bars of noise, and the thing worth seeing is
        the moment the hand has to move.

        Built once per song. Naming a chord is cheap but it is not free, and
        this display has been bitten twice by work that looked cheap until it
        ran once a frame.
        """
        by_time: dict[float, list[int]] = {}
        for note in self._timeline.notes:
            if self._note_passes_filter(note) and not note.dead:
                by_time.setdefault(note.timestamp_ms, []).append(note.midi_note)
        out: list[tuple[float, str]] = []
        last = None
        for when in sorted(by_time):
            name = name_chord(by_time[when])
            if name is None or name == last:
                continue
            out.append((when, name))
            last = name
        return out

    def _draw_chord_names(self, surface: pygame.Surface,
                          layout: _Layout) -> None:
        """The chord name above the board, where the chord changes."""
        if not self._chord_names:
            return
        t = get_theme()
        font = _get_font("arial", int(layout.lane_height * 0.62), True)
        view_start = self._playback_ms - LEFT_MARGIN_MS
        view_end = self._playback_ms + self._visible_window_ms + RIGHT_MARGIN_MS
        y = int(layout.lane_top) - HIT_LINE_OVERHANG_PX - font.get_height() - 4
        for when, name in self._chord_names:
            if when < view_start or when > view_end:
                continue
            x = int(self.note_x(when, self._playback_ms,
                                layout.hit_zone_x, layout.pixels_per_ms))
            if x < -80 or x > layout.screen_w:
                continue
            # Outlined, like every other white mark on this screen: it sits
            # over whatever the background happens to be at that moment.
            shadow = font.render(name, True, (0, 0, 0))
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                surface.blit(shadow, (x + dx, y + dy))
            surface.blit(font.render(name, True, t.hud_text), (x, y))

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

    def _nudge_backing(self, direction: int, mods: int) -> None:
        """N and M, with the modifier deciding which backing and how far.

        Both backings live on one pair of keys because the hands are on the
        guitar and a second pair would not be found. The order matters:
        Ctrl+Shift has to be tested before Ctrl, or the wider step can never
        be reached.
        """
        if mods & pygame.KMOD_CTRL and mods & pygame.KMOD_SHIFT:
            self._adjust_mp3_offset(direction * MP3_OFFSET_JUMP_MS)
        elif mods & pygame.KMOD_CTRL:
            self._adjust_mp3_offset(direction * MP3_OFFSET_COARSE_MS)
        elif mods & pygame.KMOD_SHIFT:
            self._adjust_mp3_offset(direction * MP3_OFFSET_STEP_MS)
        elif mods & pygame.KMOD_ALT:
            self._adjust_backing_offset(direction * BACKING_OFFSET_COARSE_MS)
        else:
            self._adjust_backing_offset(direction * BACKING_OFFSET_STEP_MS)

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
        for player in self._midi_all():
            player.seek(self._backing_ms(self._playback_ms))

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
    def _palm_mute_run_starts(notes) -> set[tuple[float, int]]:
        """Keys of the notes that OPEN a palm-muted run.

        One badge per run, not per note: a muted metal riff flags every note
        it contains, and a disc over each of them buries the music under its
        own labelling. Paper tab writes "P.M." once and dashes it onward for
        exactly that reason -- here the choked note bodies carry the run on
        from the badge.

        Marked on the lowest string of the stroke that starts the run. Palm
        muting is the picking hand resting on the strings, so it applies to
        the whole stroke rather than to one string of it, and one badge says
        that where three stacked ones only crowd the lanes.
        """
        by_string: dict[int, list] = {}
        for note in notes:
            by_string.setdefault(note.string, []).append(note)

        opening: dict[float, int] = {}
        for group in by_string.values():
            group.sort(key=lambda n: n.timestamp_ms)
            last_muted_ms: float | None = None
            for note in group:
                if note.dead:
                    # A dead stroke in the middle of a chug riff does not lift
                    # the picking hand off the strings, so it does not end the
                    # run -- treating it as a break re-badged every second note
                    # of the commonest metal rhythm there is.
                    continue
                if not note.palm_mute:
                    last_muted_ms = None
                    continue
                if (last_muted_ms is None
                        or note.timestamp_ms - last_muted_ms > PALM_MUTE_RUN_GAP_MS):
                    # Lowest string = highest number, and a chord's strings
                    # share one timestamp.
                    opening[note.timestamp_ms] = max(
                        opening.get(note.timestamp_ms, 0), note.string
                    )
                last_muted_ms = note.timestamp_ms
        return {(ts, string) for ts, string in opening.items()}

    @staticmethod
    def bend_label(semitones: float) -> str:
        """Bend depth in WHOLE steps: ½, 1, 1½, 2 ...

        Guitar notation counts steps, not semitones -- one semitone is a half
        bend, two is a whole one. Paper tab writes that whole bend as 'full',
        but it goes in a badge the size of a fingertip, and Yousician writes
        the number there for the same reason. ½ and 1 fit; 'full' does not.
        """
        halves = int(round(semitones))
        if halves <= 0:
            return ""
        whole, rest = divmod(halves, 2)
        if not whole:
            return "½"
        return f"{whole}½" if rest else str(whole)

    @staticmethod
    def _bend_points(
        note: NoteEvent, x: float, cy: float, width: float, height: float,
    ) -> list[tuple[float, float]]:
        """Screen points of the bend curve, left to right.

        `height` is how far a FULL bend (two semitones) rises above `cy`, so
        a half bend really does look half as deep. A deeper bend than that is
        squeezed to fit rather than drawn outside the note.

        Each written point is joined to the next by a smoothstep rather than a
        straight line: a bend is a continuous pull, and a polyline with visible
        kinks reads as a staircase of separate pitches.
        """
        curve = list(note.bend)
        # GP files routinely omit the starting point at (0, 0); without it the
        # curve begins in mid-air instead of at the fretted pitch.
        if curve and curve[0][0] > 0.0:
            curve.insert(0, (0.0, 0.0))
        if len(curve) < 2:
            return []
        deepest = max((v for _, v in curve), default=0.0)
        if deepest <= 0:
            return []
        rise = height / FULL_BEND_SEMITONES
        if deepest * rise > height:
            rise = height / deepest

        points: list[tuple[float, float]] = []
        for (p0, v0), (p1, v1) in zip(curve, curve[1:]):
            for step in range(BEND_CURVE_STEPS):
                f = step / BEND_CURVE_STEPS
                eased = f * f * (3 - 2 * f)
                pos = p0 + (p1 - p0) * f
                val = v0 + (v1 - v0) * eased
                points.append((x + pos * width, cy - val * rise))
        last_pos, last_val = curve[-1]
        points.append((x + last_pos * width, cy - last_val * rise))
        return points

    def _draw_technique_line(
        self, surface: pygame.Surface, points: list[tuple[float, float]],
        width: int, dim: bool,
    ) -> None:
        """A white technique line with a dark shadow under it.

        The shadow is not decoration. A white curve on a white-ish or yellow
        note is invisible, which is exactly what happened to the bend line on
        the amber string -- and a technique you cannot see is one you will not
        play.
        """
        pts = [(int(px), int(py)) for px, py in points]
        if len(pts) < 2:
            return
        t = get_theme()
        shadow = [(px + 1, py + 2) for px, py in pts]
        pygame.draw.lines(surface, t.note_border, False, shadow, width + 2)
        line = (190, 190, 200) if dim else (255, 255, 255)
        pygame.draw.lines(surface, line, False, pts, width)

    @staticmethod
    def _badge_y(cy: float, head: float, half_h: float | None = None) -> float:
        """Badge centre: clear of the note's top edge, not on the fret number.

        The top edge is the head's HEIGHT, which on a dense song is larger
        than its width -- measuring from the width would park the badge
        inside the note.
        """
        top = half_h if half_h is not None else head / 2
        return cy - top - head * BADGE_RADIUS_HEADS * BADGE_LIFT_HEADS

    def _draw_badge(
        self, surface: pygame.Surface, label: str, cx: float, cy: float,
        head: float, color: tuple[int, int, int], dim: bool,
    ) -> None:
        """The little dark disc naming a technique, as Yousician marks them.

        Sits above the note's leading edge so the fret number underneath stays
        whole, and takes its colour from the string so it still reads as
        belonging to that note.
        """
        radius = max(8, int(head * BADGE_RADIUS_HEADS))
        fill = dimmed(color, 0.25 if dim else 0.45)
        t = get_theme()
        pygame.draw.circle(surface, t.note_border, (int(cx), int(cy)), radius + 1)
        pygame.draw.circle(surface, fill, (int(cx), int(cy)), radius)
        ink = (170, 170, 180) if dim else (255, 255, 255)
        # Shrink to fit rather than spill: "SL" and "1½" are wider than "H",
        # and a label hanging over the edge of its disc looks like a mistake.
        for size in range(max(9, int(radius * 1.25)), 6, -1):
            font = _get_font("arial", size)
            text = font.render(label, True, ink)
            if text.get_width() <= radius * 1.7:
                break
        surface.blit(text, (int(cx) - text.get_width() // 2,
                            int(cy) - text.get_height() // 2))

    def _draw_bend(
        self, surface: pygame.Surface, note: NoteEvent, x: float, cy: float,
        head: float, capsule_w: float, color: tuple[int, int, int], dim: bool,
    ) -> None:
        """The bend curve, drawn INSIDE the note, plus a badge saying how far.

        Inside rather than above, which is how Yousician draws it and what
        this six-lane layout actually allows: an arc rising out of the note
        reaches into the neighbouring string's lane, where it reads as a note
        over there. Kept within the note body it cannot be misread, and the
        depth is carried by the badge -- 1/2, full, 1 1/2 -- which is the
        number a player looks for anyway.
        """
        radius = head / 2
        body = max(capsule_w, head)
        # Starts past the fret number rather than under it: the digit is what
        # tells you where to put the finger, and a line through it wins an
        # argument it should not be having.
        start = x + head
        width = max(body - head - radius * BEND_INSET_FRACTION,
                    head * BEND_MIN_WIDTH_HEADS)
        points = self._bend_points(
            note, start, cy + radius * BEND_BASE_FRACTION,
            width, head * BEND_DEPTH_HEADS,
        )
        if len(points) < 2:
            return
        self._draw_technique_line(surface, points, TECHNIQUE_WIDTH_PX, dim)

        label = self.bend_label(note.bend_semitones)
        if label:
            self._draw_badge(surface, label, x + head, self._badge_y(cy, head),
                             head, color, dim)

    def _draw_slide(
        self, surface: pygame.Surface, note: NoteEvent, x: float, cy: float,
        head: float, capsule_w: float, target: NoteEvent | None,
        target_x: float | None, color: tuple[int, int, int], dim: bool,
    ) -> None:
        """A slanted connector to where the finger is going, plus an SL badge.

        The target of a slide sits on the SAME string, so the lane cannot show
        direction the way a staff would. The connector is slanted within the
        lane instead: rising to the right means sliding up the neck. It spans
        the GAP between the two heads rather than their full separation --
        across a long gap the slant would flatten out to nothing, and the
        direction is the whole point of drawing it.
        """
        radius = head / 2
        slant = radius * SLIDE_SLANT_FRACTION

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
            self._draw_technique_line(
                surface, [(start_x, cy + rise), (end_x, cy - rise)],
                TECHNIQUE_WIDTH_PX, dim,
            )
            self._draw_badge(surface, "SL", x + head,
                             self._badge_y(cy, head), head, color, dim)
            return

        stub = head * SLIDE_STUB_HEADS
        if note.slide_out:
            start_x = x + max(capsule_w, head)
            rise = -slant if note.slide_out > 0 else slant
            self._draw_technique_line(
                surface, [(start_x, cy), (start_x + stub, cy + rise * 2)],
                TECHNIQUE_WIDTH_PX, dim,
            )
        if note.slide_in:
            rise = slant if note.slide_in > 0 else -slant
            self._draw_technique_line(
                surface, [(x - stub, cy + rise * 2), (x + radius, cy)],
                TECHNIQUE_WIDTH_PX, dim,
            )

    def _draw_legato(
        self, surface: pygame.Surface, note: NoteEvent, x: float, cy: float,
        head: float, capsule_w: float, target: NoteEvent,
        target_x: float, color: tuple[int, int, int], dim: bool,
    ) -> None:
        """The hammer-on / pull-off arc, with an H or P badge over the target.

        Which one it is follows from the frets: onto a higher fret is a
        hammer-on, back to a lower one is a pull-off. The arc bows upward
        between the two fret numbers, the way tab notation ties them.
        """
        radius = head / 2
        # Between the two fret numbers, not across them: the arc ties the
        # notes together, it does not have to cover them to say so.
        start_x = x + head
        end_x = target_x + radius * 0.4
        span = end_x - start_x
        if span < 4:
            return

        lift = min(head * LEGATO_ARC_HEADS, span * 0.35)
        base = cy + radius * LEGATO_BASE_FRACTION
        points = []
        for step in range(LEGATO_ARC_STEPS + 1):
            f = step / LEGATO_ARC_STEPS
            points.append((start_x + span * f, base - lift * (4 * f * (1 - f))))
        self._draw_technique_line(surface, points, TECHNIQUE_WIDTH_PX - 1, dim)

        label = "H" if target.fret > note.fret else "P"
        self._draw_badge(surface, label, end_x, self._badge_y(cy, head),
                         head, color, dim)

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
        # Fret wires FIRST, so the strings lie over them the way they do on a
        # guitar. They are the landmarks the eye was missing: without them the
        # notes float in an empty band and the only way to know where you are
        # is to read the number, which is the thing that is hard to read.
        self._draw_frets(surface, layout, board_h)

        # The strings themselves, down the middle of each lane. Thicker AND
        # warmer toward the low E: one weight and one colour throws away the
        # strongest cue for which lane is which.
        for i in range(6):
            y = int(layout.lane_top + (i + 0.5) * layout.lane_height)
            tint = WOUND_TINT if i >= 6 - WOUND_STRINGS else PLAIN_TINT
            width = STRING_THICKNESS[i]
            # A wound string is drawn as a dark core with a lighter highlight
            # on top, which is what makes it read as round rather than as a
            # thick line.
            pygame.draw.line(surface, dimmed(tint, 0.45), (0, y),
                             (layout.screen_w, y), width)
            pygame.draw.line(surface, tint, (0, y - max(0, width // 3)),
                             (layout.screen_w, y - max(0, width // 3)),
                             max(1, width // 2))
        # Edges of the board, deliberately DARKER than the strings. Drawn in
        # the string colour they read as a seventh and a zeroth string.
        edge_color = dimmed(t.lane_line, 0.45)
        for edge_y in (layout.lane_top, layout.lane_top + board_h):
            pygame.draw.line(
                surface, edge_color,
                (0, int(edge_y)), (layout.screen_w, int(edge_y)), 2,
            )

    def _draw_frets(self, surface: pygame.Surface, layout: _Layout,
                    board_h: float) -> None:
        """The bar lines, drawn as fret wires across the board.

        A real fretboard's wires do not move; these do, because the board is
        what scrolls. What they give is the same thing: somewhere for the eye
        to rest between notes, and a sense of where in the bar you are without
        reading anything.

        The BAR is what gets a wire. Every beat would be a picket fence behind
        the notes, and the bar is the unit a player counts in anyway.
        """
        measures = self._timeline.measures
        if not measures:
            return
        t = get_theme()
        view_start = self._playback_ms - LEFT_MARGIN_MS
        view_end = self._playback_ms + self._visible_window_ms + RIGHT_MARGIN_MS
        # On a light board the same near-invisible line really is invisible,
        # so there it is darkened instead of lightened.
        wire = (BAR_LINE_COLOR if sum(t.lane_bg_even) < 300
                else dimmed(t.lane_line, 0.75))
        top, bottom = int(layout.lane_top), int(layout.lane_top + board_h)

        # Thin them out rather than drawing a picket fence. Every second bar,
        # then every fourth: halving keeps the lines on real bar boundaries,
        # where a fixed pixel spacing would drift off the beat and stop
        # meaning anything.
        step = 1
        if len(measures) > 1:
            spacing = ((measures[1].start_ms - measures[0].start_ms)
                       * layout.pixels_per_ms)
            while spacing > 0 and spacing * step < MIN_BAR_LINE_GAP_PX:
                step *= 2

        for index, measure in enumerate(measures):
            if index % step:
                continue
            if measure.start_ms < view_start or measure.start_ms > view_end:
                continue
            x = int(self.note_x(measure.start_ms, self._playback_ms,
                                layout.hit_zone_x, layout.pixels_per_ms))
            if x < -4 or x > layout.screen_w + 4:
                continue
            pygame.draw.line(surface, wire, (x, top), (x, bottom), 1)

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

        # It stands PROUD of the board, top and bottom. Ending flush with the
        # edge, the line is one more vertical among the fret wires; running
        # past it, it reads as the thing the board scrolls through -- and the
        # overhang is visible even where a long note covers the line itself.
        pygame.draw.line(surface, t.hit_zone,
                         (x, top - HIT_LINE_OVERHANG_PX),
                         (x, bottom + HIT_LINE_OVERHANG_PX), 3)

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
            # Half-height, which is the lane's business rather than the
            # music's. Equal to the radius on a song roomy enough to keep
            # full-size heads, so those stay round.
            half_h = (self._head_h_px if self._head_h_px is not None
                      else layout.note_h) / 2
            visual_gap = head * (SLIDE_GAP_FRACTION if note.slide_to_next
                                 else SUSTAIN_GAP_FRACTION)

            # A sustain still stops short of its neighbour, so a long tab
            # duration cannot run over the next note
            gap_ms = neighbour_gap.get((note.timestamp_ms, note.string))
            gap_px = (gap_ms * layout.pixels_per_ms
                      if gap_ms is not None else float("inf"))
            body = self.sustain_width(note.duration_ms, layout.pixels_per_ms)
            capsule_w = min(body, gap_px) - visual_gap

            # Muted notes do not ring for the length the tab wrote. A dead
            # note is a click with no sustain at all, and a palm-muted one is
            # choked; drawing either at full length promises a ring that never
            # comes, and reading a chug as a held note is how a muted riff
            # ends up played wrong.
            if note.dead:
                capsule_w = min(capsule_w, 2 * radius)
            elif note.palm_mute:
                capsule_w = min(capsule_w, head * PALM_MUTE_MAX_HEADS)

            # Skip notes fully off-screen
            if x + max(capsule_w, 2 * radius) < 0 or x > layout.screen_w:
                continue

            # Centre of the string lane this note sits on
            cy = layout.lane_top + (note.string - 0.5) * layout.lane_height

            # Color: feedback color if matched, dimmed if past the hit zone
            # Grey for an open string: the lane already says which string it
            # is, so the colour can say the thing the position cannot.
            base_color = (OPEN_STRING_COLOR if note.fret == 0 and not note.dead
                          else STRING_COLORS.get(note.string, (180, 180, 180)))
            # A note is not OVER because the clock passed it. It is over when
            # the matcher has finished with it -- and the matcher cannot have
            # finished that soon: the strike is still inside the hit window
            # and the late window beyond it, and a chord verdict trails its
            # strike by ~380 ms by design. Dimming on the clock drew the whole
            # width of the window as "already missed", which is the dark flash
            # before the green the player reported as distracting. It was not
            # a glitch; it was the app showing a state it had no business
            # showing.
            if self._audio_enabled and self._matcher is not None:
                past_hit_zone = (self._matcher.get_note_state(note)
                                 is not MatchType.PENDING)
            else:
                # Nothing is coming to decide it, so the clock is the only
                # answer there is.
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
            if capsule_w > 2 * radius:
                rect = pygame.Rect(
                    int(x), int(cy - half_h), int(capsule_w), int(2 * half_h),
                )
                corner = int(min(radius, half_h))
                pygame.draw.rect(surface, color, rect, border_radius=corner)
                pygame.draw.rect(surface, t.note_border, rect, width=2,
                                 border_radius=corner)
            else:
                oval = pygame.Rect(
                    int(x), int(cy - half_h), int(2 * radius), int(2 * half_h),
                )
                pygame.draw.ellipse(surface, color, oval)
                pygame.draw.ellipse(surface, t.note_border, oval, 2)

            # Technique marks go OVER the head. They live inside the note now
            # rather than arcing out of the lane, so drawing them underneath
            # would simply hide them behind the note they describe.
            following = None
            target_x = None
            if (note.slide_to_next or note.hammer_to_next
                    or note.slide_in or note.slide_out):
                following = next_on_string.get((note.timestamp_ms, note.string))
                if following is not None:
                    target_x = self.note_x(
                        following.timestamp_ms, self._playback_ms,
                        layout.hit_zone_x, layout.pixels_per_ms,
                    )
            if note.slide_to_next or note.slide_in or note.slide_out:
                self._draw_slide(surface, note, x, cy, head, capsule_w,
                                 following, target_x, base_color, past_hit_zone)
            if note.hammer_to_next and following is not None and target_x is not None:
                self._draw_legato(surface, note, x, cy, head, capsule_w,
                                  following, target_x, base_color, past_hit_zone)
            if note.bend:
                self._draw_bend(surface, note, x, cy, head, capsule_w,
                                base_color, past_hit_zone)

            # "PM" over the note that opens a muted run, unless that note is
            # already wearing a technique badge -- two discs in one place read
            # as neither, and which pitch the note does is the more urgent of
            # the two.
            has_technique_badge = bool(
                note.bend or note.slide_to_next or note.slide_in or note.slide_out
            )
            if ((note.timestamp_ms, note.string) in self._palm_mute_starts
                    and not has_technique_badge):
                self._draw_badge(surface, "PM", x + head,
                                 self._badge_y(cy, head, half_h), head,
                                 base_color, past_hit_zone)

            # Fret number centred in the head, sized to the head it sits in —
            # a fixed size spills out of the shrunken heads of a fast run.
            # A dead note shows the X the tab shows: its fret says where the
            # hand goes, not which note comes out, and printing the digit
            # invites the player to actually fret it.
            fret_font = self._fret_font(radius, half_h, self._fret_digits)
            fret_label = "X" if note.dead else str(note.fret)
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
    FRAME_SAMPLES = 3600          # a minute of frames at 60 Hz
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
        if self._guide_player is None:
            guide_state = "—"
        else:
            guide_state = "off" if self._guide_muted else "ON"
        if not self._wait_mode:
            wait_state = "off" if self._audio_enabled else "—"
        else:
            wait_state = "WAIT" if self._wait_mode_frozen else "ON"

        transport = (
            f"{state}  |  SPACE: play/pause  "
            f"|  LEFT/RIGHT: beat (Shift: bar, Ctrl: 30s)  "
            f"|  HOME: restart  |  PgDn/PgUp: tempo  |  A: audio {audio_state}  "
            f"|  B: backing {backing_state}  |  Shift+B: my part {guide_state}  "
            f"|  W: wait {wait_state}  "
            f"|  I/O: loop {loop_state}  |  P: toggle  |  ESC: menu"
        )
        tools = (
            "+/-: speed  |  G: hit window  |  K: sync (Shift+K: reset)  "
            "|  ,/.: sync +/-10ms  |  N/M: backing sync  |  X/C: gate  "
            "|  U: audio track (Shift+U: pick, Shift/Ctrl/Alt+N/M: sync)  "
            "|  Shift+S: audio speed from 2 points (Ctrl+Shift+S: clear)  "
            "|  Shift+A: reopen audio output (if the sound goes bad)  "
            "|  TAB: track  |  V: chords  |  J: strings  |  F: frets  "
            "|  F1-F6: mute string  |  L: weakest part  |  T: theme  "
            "|  Y: timing report  |  D: run log  |  H: help"
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

        # Top-right column. Every line is stacked on the measured height of
        # the one above it. Fixed pixel offsets put the noise gate on top of
        # the hit count, because the block above draws two lines and the
        # spacing had been counted for one.
        current = format_time(self._playback_ms)
        total = format_time(self._timeline.duration_ms)
        time_text = f"{current} / {total}"
        time_surf = time_font.render(time_text, True, t.hud_text)
        surface.blit(time_surf, (w - time_surf.get_width() - 12, 12))
        right_y = 12 + time_surf.get_height() + 2

        if self._audio_enabled and self._matcher is not None:
            stats = self._matcher.get_statistics()
            if stats["total"] > 0:
                right_y = self._feedback.draw_stats(
                    surface, stats, hint_font, w - 12, right_y)

        line_h = hint_font.get_height() + 4
        if self._audio_enabled:
            gate_surf = hint_font.render(
                f"Gate: {int(self._noise_gate_db)} dB"
                + (" (auto)" if self._auto_gate else " (X/C)"),
                True, t.hud_accent)
            surface.blit(gate_surf, (w - gate_surf.get_width() - 12, right_y))
            right_y += line_h
            if self._audio_capture is not None:
                self._draw_signal_meter(surface, hint_font, w, right_y)
                right_y += line_h
                self._draw_tuner(surface, hint_font, w, right_y)
                right_y += line_h
        elif self._audio_capture is not None:
            # Audio off but capture exists — still show meter and tuner
            self._draw_signal_meter(surface, hint_font, w, right_y)
            right_y += line_h
            self._draw_tuner(surface, hint_font, w, right_y)
            right_y += line_h

        # What to do about the level, when there is something to do. Silent
        # otherwise: a permanent "everything is fine" is a line nobody reads,
        # and the one time it changes nobody notices either.
        if self._audio_capture is not None:
            advice = self._level_advice()
            if advice:
                advice_surf = hint_font.render(advice, True, t.feedback_close)
                surface.blit(advice_surf,
                             (w - advice_surf.get_width() - 12, right_y))

        # Where the recording sync has got to. NOT a note that expires: the
        # player spends a minute seeking from the first point to the second,
        # and a message that is gone by then leaves them guessing which press
        # the next Shift+S will be -- which is what they reported.
        note_y = h - 74
        for line in self._sync_lines:
            line_surf = hint_font.render(line, True, t.hud_accent)
            surface.blit(line_surf,
                         (w // 2 - line_surf.get_width() // 2, note_y))
            note_y += 18
        note_y = h - 74 - 18 * len(self._sync_lines)

        # What just happened, over the footer, while it is still news.
        note = self._status_note_text()
        if note:
            note_surf = hint_font.render(note, True, t.hud_accent)
            surface.blit(note_surf,
                         (w // 2 - note_surf.get_width() // 2, note_y))

        # Bottom-center: play state + controls
        self._blit_footer_lines(surface, layout, self._footer_lines(), t.hud_text)
        if self._mp3_dialog_due:
            # Drawn this frame, so the next update may block on the chooser.
            self._mp3_dialog_armed = True

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

        # Tuning HUD — always shown, because a tab in Drop C played on a
        # standard-tuned guitar is wrong on every single note, and nothing
        # else on screen says so: the notes scroll by looking perfectly
        # ordinary while every one of them scores red. Highlighted when it
        # differs from standard, since that is the case that needs an action
        # from the player (and, after retuning, a fresh calibration).
        tuning = meta.tuning
        standard = is_standard_tuning(tuning)
        notes = tuning_notes(tuning)
        if notes:
            name = tuning_name(tuning)
            label = f"Tuning: {name or 'custom'} — {' '.join(notes)}"
            if not standard:
                label += "   ← retune"
            tune_surf = hint_font.render(
                label, True, t.hud_text if standard else t.feedback_close)
            surface.blit(tune_surf, (12, info_y))
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

        # Recorded backing HUD. Its own line, because it is a second thing
        # that can be off: a recording lined up against the click is what the
        # player is listening for while both are sounding, and a silent one
        # has to say WHY it is silent or it reads as broken.
        mp3_text = self._mp3_hud_text()
        if mp3_text:
            mp3_surf = hint_font.render(mp3_text, True, t.hud_accent)
            surface.blit(mp3_surf, (12, info_y))
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

        # Dropped audio HUD — silent while there is nothing to report, loud
        # when there is. A machine that loses buffers loses notes at random,
        # which looks exactly like bad detection or bad playing and is neither;
        # without a number on screen there is no way to tell the three apart.
        if (self._audio_enabled and self._audio_capture is not None
                and getattr(self._audio_capture, "dropped_buffers", 0)):
            drops = self._audio_capture.dropped_buffers
            drop_surf = hint_font.render(
                f"Audio dropouts: {drops}  — close other programs",
                True, t.feedback_miss)
            surface.blit(drop_surf, (12, info_y))
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
                verdict = self._sync_advice()
                sync_text = (f"Sync: {int(offset):+d} ms  |  strikes {int(abs(err)):d} ms "
                             f"{direction}{spread_text} {verdict}")
                sync_color = t.hud_accent if abs(err) > 20 else t.hud_text
            else:
                # Shown even at zero with nothing measured yet. It used to
                # vanish in exactly that state, which is the state Shift+K
                # produces -- so the one key whose whole job is to put the
                # offset back to zero looked like it had done nothing at all.
                sync_text = f"Sync: {int(offset):+d} ms  {self._sync_advice()}"
                sync_color = t.hud_text
            if sync_text:
                sync_surf = hint_font.render(sync_text, True, sync_color)
                surface.blit(sync_surf, (12, info_y))

    def _playing_median_db(self) -> float | None:
        """The level while the guitar is sounding, or None with too little.

        The same reading the run log prints: the median of the hops within
        30 dB of the loudest, which is what separates the playing from the
        gaps between notes. Compared against the ROOM, it says whether the
        input can hear the instrument at all.
        """
        if len(self._level_samples) < ROOM_SAMPLES:
            return None
        loudest = max(self._level_samples)
        playing = [db for db in self._level_samples if db > loudest - 30.0]
        return statistics.median(playing) if playing else None

    def _level_advice(self) -> str:
        """What to do about the input level, or "" when nothing needs doing.

        "Gate: -65 dB" is a number, not an instruction. A player whose signal
        is too weak sees notes come back as the wrong note and has no way to
        know it is the level rather than their playing -- and the level is
        measurable, so it should not be guesswork.

        Judged on what has been HEARD over the last few seconds, not on the
        instant level: a guitar note decays, and a single quiet frame between
        strikes says nothing about the input.
        """
        # A song that is not running has nothing to measure. Saying anything
        # from a peak that has been decaying since the last note is worse than
        # saying nothing -- it sends the player after a fault that is not
        # there, which is exactly what it did on the completion screen.
        if not self._playing:
            return ""
        peak = self._signal_peak_db
        floor = self._signal_floor_db
        gate = self._noise_gate_db
        if peak <= SIGNAL_UNKNOWN_DB:
            return ""
        if peak >= CLIPPING_DB:
            return "Too loud — turn the interface down (it distorts the pitch)"
        if peak < QUIET_PEAK_DB:
            # The detector's own limit, not the gate's. Below this the strikes
            # keep coming and their pitch goes wrong, which reads as bad
            # playing and is not.
            return ("Input too quiet for reliable pitch — turn the interface "
                    "up (notes will come back as the wrong note)")
        # A room louder than any gate may exclude is not a level problem and
        # no key on this screen can fix it -- it is the wrong input. Measured
        # on the run that produced this rule: the internal microphone array of
        # a laptop, picked up as Windows' default recording device, read a
        # room of -37.3 dB against a playing median of -37.2 -- a tenth of a
        # decibel apart, the input sounding the same whether the guitar was
        # played or not, and 24 of its 25 strikes carrying no pitch at all.
        # The two rules above were both silent: the peak was -9.2 dB, neither
        # clipping nor quiet.
        #
        # The quantity is the DISTANCE between the room and the playing, and
        # the first version of this rule used the gate ceiling as a proxy for
        # it -- which convicted the player's Focusrite on the very next run:
        # room -50.4 dB against a playing median of -29.6, a healthy 21 dB
        # apart, told to check the device. A room merely above the gate
        # ceiling is a loud room, and a loud room with a real instrument in
        # front of the microphone is not this fault.
        #
        # Measured: the laptop microphone array read -37.3 room against -37.2
        # playing -- 0.1 dB. The Focusrite reads 20.8 dB. QUIET_MARGIN_DB is
        # the same 12 dB the gate band already uses for "clear of the room",
        # and it separates the two by eight decibels either way.
        room = self.room_db()
        playing = self._playing_median_db()
        if (room is not None and playing is not None
                and playing - room < QUIET_MARGIN_DB):
            return ("Input is hearing the room, not the guitar — wrong "
                    "device? Pick your interface with D in the song list")
        if self._auto_gate:
            # The gate is not the player's job any more. What is left here is
            # the interface's gain, which no gate can fix and only a hand on
            # the knob can -- the two cases above.
            return ""
        # One direction at a time, and only ever toward the band. X fires
        # while the gate is above it and C only while a real band exists to
        # raise the gate INTO, so following the advice always terminates --
        # the property the test asserts, because the wording is not the thing
        # that was broken.
        lowest, highest = gate_band(peak, floor)
        target = suggested_gate_db(peak, floor)
        if gate > highest:
            return (f"Gate {gate:.0f} dB is eating your notes — "
                    f"press X down to {target:.0f} dB")
        if lowest <= highest and gate < lowest:
            return (f"Background noise reaches the gate — "
                    f"press C up to {target:.0f} dB")
        return ""

    def _track_levels(self, db: float) -> None:
        """Keep the loudest and quietest recent level, for _level_advice.

        Decays back toward the present so a single loud accident does not
        silence the advice for the rest of the song. Only while the song is
        running: silence between takes is not a reading.
        """
        if db <= SIGNAL_UNKNOWN_DB:
            return
        if not self._playing or self._playback_ms < 0:
            # Not a reading of the playing -- a reading of the room, which is
            # what the gate has to clear and what nothing else can measure.
            # The count-in counts as room: the song is not running and the
            # player is not meant to be playing yet, which makes it the
            # longest clean window a run ever offers.
            self._room_samples.append(db)
            return
        self._signal_peak_db = max(db, self._signal_peak_db - LEVEL_DECAY_DB)
        self._signal_floor_db = min(db, self._signal_floor_db + LEVEL_DECAY_DB)
        self._loudest_db = max(self._loudest_db, db)
        self._auto_gate_while_playing()
        # Kept for the run log, which is where a level problem is proved
        # rather than suspected. Bounded so a long session cannot grow it
        # without limit.
        if len(self._level_samples) < 40_000:
            self._level_samples.append(db)

    def room_db(self) -> float | None:
        """What the room measures, or None while too little has been heard."""
        if len(self._room_samples) < ROOM_SAMPLES:
            return None
        return statistics.median(self._room_samples)

    def _take_gate_by_hand(self) -> None:
        """Touching X or C switches the automatic off, and says so.

        Otherwise the next song would silently undo the adjustment that was
        just made by hand, and a setting that will not stay set is worse than
        one that was never offered. It goes back on from the settings screen.
        """
        if not self._auto_gate:
            return
        self._auto_gate = False
        self._config.audio.auto_gate = False
        self._config.save()
        self._say("Gate von Hand — Automatik aus (O zum Zurueckschalten)")

    def _auto_gate_from_room(self) -> None:
        """Set the gate from the room, when a song starts.

        Derived every time rather than accumulated: a gate that only ever
        walks in one direction ends up wherever the last session left it, and
        the value that suits this interface at this gain is not something the
        player can judge by ear. Nothing is lost by putting it low -- swept
        over four real play-along takes, every gate from -80 dB up to the
        knee reads exactly the same number of notes, and a fully processed hop
        costs 2 % of its 11.6 ms, so there is no work to be saved either.
        """
        if not self._auto_gate:
            return
        room = self.room_db()
        if room is None:
            return
        wanted = min(room + NOISE_MARGIN_DB, MAX_GATE_DB)
        if round(wanted) != round(self._noise_gate_db):
            self.set_noise_gate_db(wanted)

    def _auto_gate_while_playing(self) -> None:
        """Lower a gate that is sitting inside the playing. Never raise one.

        The safety net for a room measured while the player happened to be
        noodling, or a gain turned down mid-session. It is one-sided because
        the two mistakes are not equals: a gate under the room costs spurious
        onsets, which the confidence filter and the candidate search already
        throw away, while a gate over the playing costs the strikes
        themselves, and a strike that never arrives cannot be recovered by
        anything downstream.

        `_loudest_db` only rises, so the level it demands only rises with it:
        once satisfied this can never fire again, and it cannot oscillate the
        way the ADVICE it replaces did.
        """
        if not self._auto_gate or self._loudest_db < QUIET_PEAK_DB:
            # Too quiet to judge a gate against -- that is the interface's
            # gain, which _level_advice names and no gate can fix.
            return
        highest = min(self._loudest_db - QUIET_MARGIN_DB, MAX_GATE_DB)
        if self._noise_gate_db > highest:
            self.set_noise_gate_db(highest)
            self._say(f"Gate automatisch auf {self._noise_gate_db:.0f} dB gesenkt")

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

            # How many strikes were HEARD at all, next to how many scored.
            # Without it a low percentage says only that something is wrong;
            # with it, it says which thing. Far fewer strikes than notes is
            # the microphone path; as many strikes as notes and a low score
            # is the matching, and they are fixed in different places.
            heard_surf = hint_font.render(self._heard_line(), True, t.hud_text)
            surface.blit(heard_surf,
                         (w // 2 - heard_surf.get_width() // 2, center_y + 92))

            # And what the SCORE rests on. One strike credits a whole chord,
            # so a number that mixes "heard" with "credited to the strum"
            # cannot answer "was I really that good".
            credit = self._credit_line()
            if credit:
                credit_surf = hint_font.render(credit, True, t.hud_text)
                surface.blit(credit_surf,
                             (w // 2 - credit_surf.get_width() // 2,
                              center_y + 110))

            # "New Best!" indicator
            if self._is_new_best:
                best_surf = stat_font.render("New Best!", True, (255, 220, 50))
                surface.blit(best_surf, (w // 2 - best_surf.get_width() // 2, center_y + 132))

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

            # Where the run log went. A file written silently is a file
            # nobody sends, and this one is the whole point of writing it.
            if self._run_log_note:
                log_surf = hint_font.render(self._run_log_note, True, t.hud_text)
                surface.blit(log_surf,
                             (w // 2 - log_surf.get_width() // 2, hint_y + 26))
        else:
            # Auto-scroll completion — no stats
            hint_text = "SPACE to replay  |  ESC to menu"
            hint_surf = hint_font.render(hint_text, True, t.hud_text)
            surface.blit(hint_surf, (w // 2 - hint_surf.get_width() // 2, center_y + 70))

    def _heard_line(self) -> str:
        """What the ear did, said apart from what the scoring did."""
        if self._matcher is None:
            return ""
        trace = self._matcher.strike_trace
        strikes = [t for t in trace if t.outcome != "string_taken_back"]
        credited = sum(1 for t in strikes
                       if t.outcome in ("hit", "close", "dead", "chord"))
        taken_back = self._matcher.chord_strings_corrected
        line = (f"{len(strikes)} strikes heard, {credited} of them landed on "
                f"a written note")
        if taken_back:
            line += f"; {taken_back} strings taken back by the string check"
        return line

    def _credit_line(self) -> str:
        """What the score rests on, said apart from the score itself.

        A six-string chord is credited from ONE strike: the strum is heard,
        the fretting of the other five is not. The chord verifier is what
        polices that, and it can only convict a string whose partials are not
        masked by a lower one -- which in an open chord is most of them. So a
        percentage that mixes the two cannot answer "was I really that good",
        and a player who feels the score is too kind is reading something
        real. This says how much of it was actually heard.
        """
        if self._matcher is None:
            return ""
        proved = self._matcher.notes_proved
        strum = self._matcher.notes_by_strum
        if proved + strum == 0:
            return ""
        line = f"{proved} of them were heard as themselves"
        if strum:
            line += f", {strum} credited to a strum that was heard"
        rescued = self._matcher.rescued_notes
        if rescued:
            line += f" ({rescued} confirmed from the audio)"
        return line

    # -- Timing report (Y) --

    TIMING_VERDICTS = {
        "fine": ("Your timing is fine.",
                 "Nothing here needs fixing. The rest is the music."),
        "latency": ("Most of your error is LATENCY.",
                    "Every strike is late by about the same amount, which one "
                    "offset removes. Press K."),
        "mixed": ("You have BOTH latency and scatter.",
                  "Press K to take out the constant part; what is left is "
                  "spread, and that needs slower practice, not a setting."),
        "scatter": ("Most of your error is SCATTER.",
                    "Your strikes disagree with each other, so no offset can "
                    "fix it. Slow the song down (PgDn) or widen the hit "
                    "window (G) while you learn the part."),
        "per_string": ("Your strings register at DIFFERENT delays.",
                       "That is neither latency nor playing, and one global "
                       "offset cannot remove it. See the per-string list."),
    }

    def _draw_timing_overlay(self, surface: pygame.Surface, layout: _Layout) -> None:
        """Show WHICH timing problem this is, not just that there is one.

        A median and a spread are two numbers; the shape of the distribution
        is the diagnosis. One narrow hill away from zero is latency and K
        removes it. One wide hill over zero is the playing. Two hills, or a
        split between strings, is something structural that neither fixes.
        """
        t = get_theme()
        w, h = layout.screen_w, layout.screen_h
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 248))
        surface.blit(overlay, (0, 0))

        title_font = _get_font("arial", 26)
        body_font = _get_font("arial", 16)
        small_font = _get_font("arial", 13)
        cx = w // 2

        surface.blit(title_font.render("Timing report", True, t.hud_accent),
                     (cx - title_font.size("Timing report")[0] // 2, 14))

        report = self._matcher.timing_report() if self._matcher is not None else None
        if report is None:
            lines = [
                "Not enough measurements yet.",
                "",
                "Play a while with audio on (A), then press Y again.",
                "Strikes are only measured where exactly one tab note can",
                "explain them, so a riff repeating one pitch contributes",
                "nothing until the offset is close enough to be unambiguous.",
            ]
            y = h // 2 - len(lines) * 11
            for line in lines:
                surf = body_font.render(line, True, t.hud_text)
                surface.blit(surf, (cx - surf.get_width() // 2, y))
                y += 22
            self._draw_timing_footer(surface, layout, small_font)
            return

        headline, advice = self.TIMING_VERDICTS[report["verdict"]]
        colour = t.feedback_hit if report["verdict"] == "fine" else t.feedback_close
        surf = body_font.render(headline, True, colour)
        surface.blit(surf, (cx - surf.get_width() // 2, 50))
        surf = small_font.render(advice, True, t.hud_text)
        surface.blit(surf, (cx - surf.get_width() // 2, 72))

        self._draw_timing_histogram(surface, report, 60, 108, w - 120, 200)
        self._draw_timing_numbers(surface, report, 60, 356, body_font, small_font)
        self._draw_timing_strings(surface, report, cx + 100, 356, body_font, small_font)
        self._draw_timing_footer(surface, layout, small_font)

    def _draw_timing_footer(self, surface, layout, font) -> None:
        t = get_theme()
        note = self._timing_export_note or "Y to close   |   Shift+Y to save the measurements to a file"
        surf = font.render(note, True, t.hud_accent)
        surface.blit(surf, (layout.screen_w // 2 - surf.get_width() // 2,
                            layout.screen_h - 26))

    def _draw_timing_histogram(self, surface, report, x, y, width, height) -> None:
        """Bars over the error axis, with zero and the median marked.

        Drawn against the ACTUAL range of the samples rather than a fixed
        axis, because the interesting cases differ by an order of magnitude:
        a well-synced player sits inside +-40 ms, an unsynced one is a
        hundred milliseconds away and would be a single bar at the edge.
        """
        t = get_theme()
        bars = report["histogram"]
        if not bars:
            return
        font = _get_font("arial", 12)
        peak = max(count for _, count in bars) or 1
        step = max(2.0, width / max(1, len(bars)))
        baseline = y + height

        for i, (low, count) in enumerate(bars):
            bx = x + i * step
            bh = (count / peak) * (height - 18)
            late = low >= 0
            colour = t.feedback_miss if late else t.hud_accent
            pygame.draw.rect(surface, colour,
                             (int(bx), int(baseline - bh), max(1, int(step - 2)), int(bh)))

        pygame.draw.line(surface, t.hud_text, (x, baseline), (x + width, baseline), 1)

        bin_ms = self._matcher.timing_bin_ms()
        lows = [low for low, _ in bars]
        axis_lo, axis_hi = lows[0], lows[-1] + bin_ms

        def position(value_ms: float) -> int | None:
            if not axis_lo <= value_ms <= axis_hi:
                return None
            frac = (value_ms - axis_lo) / max(1e-6, axis_hi - axis_lo)
            return int(x + frac * (len(bars) * step))

        def mark(value_ms: float, colour, label: str, row: int) -> None:
            mx = position(value_ms)
            if mx is None:
                return
            pygame.draw.line(surface, colour, (mx, y), (mx, baseline + 5), 2)
            surf = font.render(label, True, colour)
            surface.blit(surf, (mx - surf.get_width() // 2, y - 15 - row * 15))

        # A well-synced player has both marks in nearly the same place, and
        # their labels then print on top of each other -- exactly the case
        # where the picture is supposed to be reassuring.
        zero_x, median_x = position(0.0), position(report["median_ms"])
        crowded = (zero_x is not None and median_x is not None
                   and abs(zero_x - median_x) < 110)
        mark(0.0, t.hud_text, "on the beat", 0)
        mark(report["median_ms"], t.feedback_close,
             f"your middle {report['median_ms']:+.0f} ms", 1 if crowded else 0)

        left = font.render(f"{axis_lo:+.0f} ms (early)", True, t.hud_text)
        right = font.render(f"{axis_hi:+.0f} ms (late)", True, t.hud_text)
        surface.blit(left, (x, baseline + 8))
        surface.blit(right, (x + width - right.get_width(), baseline + 8))

    def _draw_timing_numbers(self, surface, report, x, y, font, small) -> None:
        t = get_theme()
        surface.blit(font.render("What the numbers say", True, t.hud_accent), (x, y))
        y += 26
        rows = [
            (f"{report['count']} strikes measured",
             f"{report['ambiguous']} more could not be told apart from a neighbour"),
            (f"Middle error {report['median_ms']:+.0f} ms",
             "positive = you register late, so you feel forced to play early"),
            (f"Scatter +/-{report['spread_ms']:.0f} ms",
             "how far a typical strike sits from your own middle"),
            (f"Typical error {report['mean_error_ms']:.0f} ms",
             f"would drop to {report['residual_ms']:.0f} ms if the middle were "
             f"compensated (K)"),
            (f"K removes {100 * report['explained_fraction']:.0f}% of it",
             "the rest is scatter, which no offset can touch"),
        ]
        for headline, detail in rows:
            surface.blit(font.render(headline, True, t.hud_text), (x, y))
            y += 19
            surface.blit(small.render(detail, True, dimmed(t.hud_text, 0.75)), (x + 12, y))
            y += 22

    def _draw_timing_strings(self, surface, report, x, y, font, small) -> None:
        t = get_theme()
        surface.blit(font.render("Per string", True, t.hud_accent), (x, y))
        y += 26
        by_string = report["by_string"]
        if not by_string:
            surface.blit(small.render("no measurements yet", True, t.hud_text), (x, y))
            return

        names = {1: "high E", 2: "B", 3: "G", 4: "D", 5: "A", 6: "low E"}
        for string, (median, count) in by_string.items():
            colour = STRING_COLORS.get(string, (180, 180, 180))
            pygame.draw.rect(surface, colour, (x, y + 4, 12, 12), border_radius=2)
            thin = count < STRING_MIN_SAMPLES
            ink = dimmed(t.hud_text, 0.6) if thin else t.hud_text
            label = f"{names.get(string, string):>6}  {median:+6.0f} ms   ({count})"
            surface.blit(font.render(label, True, ink), (x + 20, y))
            y += 22

        y += 6
        gap = report["string_gap_ms"]
        if gap > 0:
            if report["string_gap_real"]:
                note = f"Spread between strings: {gap:.0f} ms — more than chance"
                colour = t.feedback_close
            else:
                note = f"Spread between strings: {gap:.0f} ms — within chance"
                colour = dimmed(t.hud_text, 0.8)
            surface.blit(small.render(note, True, colour), (x, y))
            y += 18
        surface.blit(small.render(
            f"(a string needs {STRING_MIN_SAMPLES} strikes to count, and the gap "
            "has to beat the scatter)", True, dimmed(t.hud_text, 0.7)), (x, y))

    def _export_timing_samples(self) -> None:
        """Write the raw measurements next to the settings, as CSV.

        The report answers the common questions; a file answers the ones
        nobody thought to ask yet, and can be looked at away from the app.
        """
        if self._matcher is None or not self._matcher.timing_samples:
            self._timing_export_note = "Nothing measured yet — play a while first."
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        song = "".join(c if c.isalnum() else "_" for c in (self._song_key or "song"))[:40]
        # Read through the module, not a name bound at import: the test suite
        # redirects the config directory, and a name captured at import time
        # would sail past that straight into the user's real home folder.
        directory = config_module.CONFIG_DIR
        path = directory / f"timing_{song}_{stamp}.csv"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("delta_ms,string,midi_note,note_ms\n")
                for s in self._matcher.timing_samples:
                    fh.write(f"{s.delta_ms:.1f},{s.string},{s.midi_note},{s.note_ms:.1f}\n")
        except OSError as exc:
            self._timing_export_note = f"Could not write the file: {exc}"
            return
        self._timing_export_note = f"Saved {len(self._matcher.timing_samples)} measurements to {path}"

    def record_frame_ms(self, ms: float) -> None:
        """One frame's work, for the run log.

        "Slow and stuttering" is a feeling, and a feeling cannot say whether
        the display is behind, the machine is throttling, or the audio thread
        is stalling -- the same problem the score had before the log named
        strikes and notes separately. So the frames are counted here and the
        header reports the median and the worst tenth.

        Only while the song is running: a frame spent on the settings screen
        or a paused picture says nothing about whether the app can keep up.
        """
        if not self._playing:
            return
        self._frame_ms.append(ms)
        if len(self._frame_ms) > self.FRAME_SAMPLES:
            del self._frame_ms[:len(self._frame_ms) - self.FRAME_SAMPLES]

    def _frame_line(self, fh) -> None:
        """Median and worst-tenth frame, and how many frames were late.

        60 FPS is a 16.7 ms budget. A median well under it with a fat tail is
        something arriving in bursts; a median over it is the drawing itself,
        and those are fixed in different places.
        """
        frames = sorted(self._frame_ms)
        if not frames:
            fh.write("frame_ms\t(nothing measured)\n")
            return
        late = sum(1 for ms in frames if ms > FRAME_BUDGET_MS)
        fh.write(f"frame_ms_median\t{frames[len(frames) // 2]:.1f}\n")
        fh.write(f"frame_ms_worst_tenth\t{frames[int(len(frames) * 0.9)]:.1f}\n")
        fh.write(f"frame_ms_worst\t{frames[-1]:.1f}\n")
        fh.write(f"frames_over_budget_percent\t{100 * late / len(frames):.0f}\n")
        fh.write(f"frames_measured\t{len(frames)}\n")

    STATUS_NOTE_SECONDS = 8.0

    def _say(self, text: str) -> None:
        """Put one line on screen for a few seconds.

        For what the live HUD cannot say by itself -- a file that was just
        written, a gate that just moved. It expires rather than being cleared
        by hand, because a status message that outlives its situation is the
        other half of the same fault.
        """
        self._status_note = text
        self._status_note_until = time.monotonic() + self.STATUS_NOTE_SECONDS

    def _status_note_text(self) -> str:
        """The note, while it is still news."""
        if self._status_note and time.monotonic() < self._status_note_until:
            return self._status_note
        return ""

    def _export_run_log(self) -> None:
        """Write everything the audio path did this run, as one text file.

        A percentage cannot be debugged. The same take that scored 35 % in
        the app scored 97 % when the identical detector and matcher were run
        over the recording offline, and nothing on screen could say which of
        the two dozen steps in between lost the notes -- whether they were
        never heard, heard as something else, heard at the wrong moment, or
        heard and then taken back by the string check. This file says which,
        strike by strike, so the next question is asked of evidence.
        """
        if self._matcher is None:
            self._run_log_note = "Nothing to write — audio was off (A)."
            self._say(self._run_log_note)
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        song = "".join(c if c.isalnum() else "_" for c in (self._song_key or "song"))[:40]
        # Read through the module, not a name bound at import: the test suite
        # redirects the config directory.
        directory = config_module.CONFIG_DIR
        path = directory / f"run_{song}_{stamp}.txt"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                self._write_run_log(fh)
        except OSError as exc:
            self._run_log_note = f"Could not write the file: {exc}"
            self._say(self._run_log_note)
            return
        done = self._song_completed or self._playback_ms >= self._timeline.duration_ms
        where = "" if done else f" — up to {self._playback_ms / 1000:.0f} s"
        self._run_log_written = True
        self._run_log_note = f"Run written to {path}{where}"
        self._say(self._run_log_note)

    def _write_run_log(self, fh) -> None:
        """The body of the run log. Split out so a test can read it back."""
        matcher = self._matcher
        stats = matcher.get_statistics()
        capture = self._audio_capture
        ac = self._config.audio
        fh.write("# MySician run log\n")
        fh.write(f"song\t{self._song_key}\n")
        fh.write(f"notes_written\t{len(self._timeline.notes)}\n")
        # How far the run actually got. D can be pressed at any moment, and a
        # log stopped a third of the way in has two thirds of its notes still
        # PENDING -- which reads as a catastrophic score to anybody who
        # divides hits by notes_written. A number is only readable next to
        # what it is a number of.
        pending = sum(1 for note in self._timeline.notes
                      if matcher.get_note_state(note) is MatchType.PENDING)
        fh.write(f"notes_reached\t{len(self._timeline.notes) - pending}\n")
        fh.write(f"notes_not_reached\t{pending}\n")
        fh.write(f"reached_ms\t{self._playback_ms:.0f}\n")
        fh.write(f"song_ms\t{self._timeline.duration_ms:.0f}\n")
        fh.write(f"played_to_the_end\t{bool(self._song_completed)}\n")
        if self._loop_enabled and self._loop_start_ms is not None:
            fh.write(f"loop\t{self._loop_start_ms:.0f}-"
                     f"{'' if self._loop_end_ms is None else f'{self._loop_end_ms:.0f}'}"
                     f" ms (the same bars were played over and over)\n")
        fh.write(f"tempo_percent\t{int(self._tempo_factor * 100)}\n")
        fh.write(f"hit_window_ms\t{self._config.timing_window_ms:.0f}\n")
        fh.write(f"sync_offset_ms\t{self._config.audio_latency_offset_ms:.0f}\n")
        fh.write(f"audio_offset_ms\t{matcher.audio_offset_ms:.1f}\n")
        fh.write(f"late_window_ms\t{matcher.late_window_ms:.0f}\n")
        fh.write(f"audio_anchor_ms\t{self._audio_anchor_ms:.1f}\n")
        fh.write(f"audio_anchor_song_ms\t{self._audio_anchor_song_ms:.1f}\n")
        fh.write(f"sample_rate\t{getattr(capture, '_sample_rate', ac.sample_rate)}\n")
        describe = getattr(capture, "describe_device", None)
        fh.write(f"input_device\t{describe() if describe else '(unknown)'}\n")
        fh.write(f"dropped_buffers\t{getattr(capture, 'dropped_buffers', 0)}\n")
        # The OUTPUT, which this log never mentioned. A run where the sound
        # went wrong and a run where it did not are otherwise identical here.
        fh.write(f"output_device\t{output.describe()}\n")
        fh.write(f"noise_gate_db\t{ac.noise_gate_db:.0f}"
                 f"\t{'auto' if self._auto_gate else 'von Hand'}\n")
        # The input level, in the same units the HUD shows (RMS of one hop).
        # A weak input does not lose strikes, it corrupts their PITCH -- which
        # looks exactly like bad playing from the score alone. Measured on the
        # player's own take: the loudest hop above -38 dB reads 91-96 %, at
        # -44 dB it is 83 %, at -50 dB 52 %. So these three numbers settle in
        # one reading what would otherwise be a round trip of guessing.
        levels = sorted(self._level_samples)
        if levels:
            loudest = levels[-1]
            playing = [db for db in levels if db > loudest - 30.0]
            median = playing[len(playing) // 2] if playing else loudest
            under = sum(1 for db in levels if db < ac.noise_gate_db)
            fh.write(f"level_loudest_db\t{loudest:.1f}\n")
            fh.write(f"level_median_playing_db\t{median:.1f}\n")
            fh.write(f"level_under_gate_percent\t{100 * under / len(levels):.0f}\n")
            # The room is measured while the song is NOT running, never as a
            # low percentile of the playing: across one session's reference
            # takes that percentile ran from -35 dB on a dense passage to
            # -94 dB on a sparse one against a recorded room of -73, so it
            # reports how busy the playing was. Without it there is no honest
            # gate to suggest, and none is printed.
            room = self.room_db()
            if room is None:
                fh.write("level_room_db\t(nicht gemessen)\n")
            else:
                low, high = gate_band(loudest, room)
                fh.write(f"level_room_db\t{room:.1f}\n")
                # A percentage of discarded audio is only readable next to
                # the value that would not have discarded it -- the same rule
                # as "up to 40 s" beside a half-finished run.
                fh.write(f"gate_suggested_db"
                         f"\t{suggested_gate_db(loudest, room):.0f}"
                         f"\t(band {low:.0f} to {high:.0f}"
                         f"{', empty' if low > high else ''})\n")
                # The verdict, not two numbers eight lines apart. A room that
                # no permitted gate can clear is the wrong INPUT, and the run
                # that produced this rule read a room of -37.3 against a
                # playing median of -37.2 -- the input sounding the same
                # whether the guitar was played or not.
                if median - room < QUIET_MARGIN_DB:
                    fh.write(f"input_hears_the_room\tyes"
                             f"\t(room {room:.1f} dB vs playing "
                             f"{median:.1f} dB — check the device)\n")
        else:
            fh.write("level_loudest_db\t(nothing measured)\n")
        fh.write(f"confidence_threshold\t{ac.confidence_threshold}\n")
        fh.write(f"onset_threshold\t{ac.onset_threshold}\n")
        fh.write(f"calibrated\t{bool(getattr(self._config, 'calibration', None))}\n")
        fh.write(f"chord_verify\t{getattr(self._config, 'chord_verify', True)}\n")
        fh.write(f"bend_check\t{getattr(self._config, 'bend_check', True)}\n")
        fh.write(f"chord_partial_credit\t{self._chord_partial_credit}\n")
        fh.write(f"max_fret\t{self._config.max_fret}\n")
        fh.write(f"active_strings\t{self._config.active_strings}\n")
        fh.write(f"wait_mode\t{self._wait_mode}\n")
        fh.write(f"hits\t{stats['hits']}\n")
        fh.write(f"close\t{stats['close']}\n")
        fh.write(f"misses\t{stats['misses']}\n")
        # What the score rests on. A six-string chord is credited from one
        # strike, so hits alone cannot say how much was actually heard.
        # Where the score comes from, split by how many strings the tab
        # writes at that instant. A chord is credited from one strike, so a
        # single number cannot say whether 80 % was played or strummed: on
        # the run that raised the question, single notes read 20 % and
        # four-string chords 94 %.
        from collections import Counter
        per_onset = Counter(n.timestamp_ms for n in self._timeline.notes)
        sizes: dict[int, list[int]] = {}
        for note in self._timeline.notes:
            state = matcher.get_note_state(note)
            if state is MatchType.PENDING:
                continue
            row = sizes.setdefault(per_onset[note.timestamp_ms], [0, 0])
            row[1] += 1
            if state in (MatchType.HIT, MatchType.CLOSE):
                row[0] += 1
        for size in sorted(sizes):
            green, total = sizes[size]
            fh.write(f"chord_of_{size}\t{green}/{total}"
                     f"\t{100 * green / total:.0f}%\n")
        fh.write(f"notes_heard_as_themselves\t{matcher.notes_proved}\n")
        fh.write(f"notes_credited_to_a_strum\t{matcher.notes_by_strum}\n")
        fh.write(f"strings_taken_back\t{matcher.chord_strings_corrected}\n")
        fh.write(f"chord_windows_judged\t{matcher.chord_verifications}\n")
        fh.write(f"rescued_notes\t{matcher.rescued_notes}\n")
        fh.write(f"bends_judged\t{matcher.bends_judged}\n")
        fh.write(f"bends_short\t{matcher.bends_short}\n")
        # The recording's own sync, so "it feels out" becomes a number. The
        # stretch itself keeps time to 2 ms a minute (tools/check_timestretch),
        # so anything felt here is drift or latency, not the tempo.
        player = self._mp3_player
        if player is not None:
            fh.write(f"mp3_worst_drift_ms\t{player.worst_drift_ms:.0f}\n")
            fh.write(f"mp3_resyncs\t{player.resyncs}\n")
            fh.write(f"mp3_worst_seek_ms\t"
                     f"{getattr(player, 'worst_seek_ms', 0.0):.0f}\n")
            fh.write(f"mp3_time_scale\t{player.time_scale:.3f}\n")
        fh.write(f"mp3_rate\t{self._mp3_rate():.4f}"
                 f"\t({(1 / self._mp3_rate() - 1) * 100:+.2f} % gedehnt)\n")
        self._frame_line(fh)
        if self._clock_real_ms > 0:
            ratio = self._clock_song_ms / self._clock_real_ms
            fh.write(f"clock_real_s\t{self._clock_real_ms / 1000:.1f}\n")
            fh.write(f"clock_song_s\t{self._clock_song_ms / 1000:.1f}\n")
            fh.write(f"clock_ratio\t{ratio:.4f}\n")
            fh.write(f"clock_lost_ms\t"
                     f"{self._clock_real_ms - self._clock_song_ms:.0f}\n")
            fh.write(f"clock_stalls\t{self._clock_stalls}\n")
        fh.write(f"timing_samples\t{len(matcher.timing_samples)}\n")
        fh.write(f"timing_ambiguous\t{matcher.timing_ambiguous}\n")

        fh.write("\n# every strike the audio thread produced\n")
        fh.write("strike_ms\tadjusted_ms\tplayback_ms\tmidi\tconf"
                 "\tunpitched\tsubharm\toutcome\tnote_ms\tsemitones\n")
        for t in matcher.strike_trace:
            fh.write(
                f"{t.strike_ms:.1f}\t{t.adjusted_ms:.1f}\t{t.playback_ms:.1f}"
                f"\t{t.midi_note}\t{t.confidence:.2f}\t{int(t.unpitched)}"
                f"\t{int(t.subharmonic)}\t{t.outcome}"
                f"\t{'' if t.note_ms is None else f'{t.note_ms:.1f}'}"
                f"\t{'' if t.semitones is None else t.semitones}\n")

        fh.write("\n# every written note and how it ended up\n")
        fh.write("note_ms\tstring\tmidi\tverdict\n")
        for note in sorted(self._timeline.notes,
                           key=lambda n: (n.timestamp_ms, -n.string)):
            fh.write(f"{note.timestamp_ms:.1f}\t{note.string}\t{note.midi_note}"
                     f"\t{matcher.get_note_state(note).value}\n")

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

        block("Techniques (badge above the note says which)", [
            "\u00bd  1  1\u00bd   BEND. Fret the note, then push the string until",
            "     the pitch rises. \u00bd is one fret, 1 is two. The white",
            "     curve inside the note draws the same thing.",
            "SL   SLIDE. Strike only the first note and slide into the",
            "     second. The bar between them rises to the right for",
            "     up the neck. A short stub is a slide off into nothing.",
            "H    HAMMER-ON. Strike the first note, then hammer the",
            "     finger down for the second without striking again.",
            "P    PULL-OFF. The same in reverse, lifting the finger.",
            "PM   PALM MUTE starts here and runs on until the notes",
            "     stop being drawn as short stubs. Rest the picking",
            "     hand on the strings; the pitch stays the written one.",
            "X    DEAD NOTE. Damp the string with the fretting hand and",
            "     strike it: a click, no pitch. Counts as played as long",
            "     as you strike it in time — there is no pitch to check.",
            "",
            "H, P and SL notes are not struck, so they score with the",
            "note they came from. Bends are scored leniently: the pitch",
            "has to land in the right region, not on the target exactly.",
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
            "Y: timing report — which timing problem you actually have",
            "Shift+Y: save the raw measurements as a CSV file",
            "D: save a full run log (what every strike did)",
            "U: recorded backing on/off, Shift+U picks the file",
            "Shift+N / Shift+M: shift the recording by 10 ms",
            "Ctrl+N / Ctrl+M: by a second   Ctrl+Shift: by ten seconds",
            "  (reaches 8 minutes, for a tab that is only the solo)",
            "Shift+S: line the recording up at two places and let the app",
            "  work out its speed — the offset says where it starts, this",
            "  says how fast it runs. Ctrl+Shift+S puts it back.",
            "+/-: scroll faster / slower     G: hit window",
            "N/M: MIDI backing earlier / later   Alt+N/M: by a second",
            "TAB: choose track     H: this help     ESC: song list",
            "On the song list, O opens the settings screen — everything that",
            "is set once, with anything away from standard marked.",
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

    def _sync_offset_song_ms(self) -> float:
        """The latency compensation, in SONG milliseconds.

        `audio_latency_offset_ms` is a delay of the real world -- the sound
        card's buffer plus aubio's analysis window, both a fixed number of
        SAMPLES and both entirely indifferent to the practice speed. A strike
        is stamped in recorded time and then scaled into song time, so the
        compensation has to be scaled with it.

        It was not, and slow practice paid for it. Measured on a 70 % run with
        a -220 ms offset: every strike landed 114 ms before its note, 66 of
        which is this -- a third of the 200 ms hit window, spent before the
        player has played anything. At 50 % it would be 110 ms, over half.
        Slowing a song down is what you do when a passage is too hard, and it
        was quietly making the scoring harder.
        """
        return self._config.audio_latency_offset_ms * self._tempo_factor

    def _late_window_ms(self) -> float:
        """Grace period for late-arriving strike notes.

        Base 150 ms covers the onset collector delay; a compensated input
        latency delays the strike's real-world arrival by the same amount
        on top, so misses must be marked correspondingly later.
        """
        return 150.0 + max(0.0, -self._sync_offset_song_ms())

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
            # The stored value is real time; the matcher works in song time.
            self._matcher.audio_offset_ms += delta_ms * self._tempo_factor
            self._matcher.late_window_ms = self._late_window_ms()
            # Old measurements no longer reflect the new offset
            self._matcher.reset_timing_samples()

    def _sync_advice(self) -> str:
        """What the HUD says about K, decided the way K itself decides.

        Both read the same report and act on the same verdict, so the line can
        never offer a key that then does nothing -- which is what it did while
        the HUD kept its own spread thresholds and K had moved on to the
        report's. A player who presses an advertised key and sees no change
        learns to distrust the whole panel, not just that line.
        """
        if self._matcher is None:
            return ""
        report = self._matcher.timing_report(AUTO_SYNC_MIN_SAMPLES)
        if report is None:
            return "— play on, still measuring"
        if report["verdict"] == "scatter":
            return "— too scattered to sync"
        if report["verdict"] == "per_string":
            return "— strings differ, no one offset fixes it"
        if report["verdict"] == "fine":
            return "— synced" if self._sync_applied else "— nothing to sync"
        # latency or mixed: K can take the constant part off.
        if not self._sync_applied:
            return "— K to auto-sync"
        # One press removes the median it could see at the time. Whatever is
        # left shows up in the samples taken since, and saying so is the
        # difference between a tool that converges and one the player abandons
        # halfway, thinking it did all it could.
        return f"— {int(abs(report['median_ms'])):d} ms still left, K again"

    def _auto_sync_timing(self) -> None:
        """Cancel out the measured input latency (K key).

        Applies exactly what the timing report calls latency, and refuses
        everything else. The report already decides whether a median is an
        effect or a coincidence, and whether one offset could fix it at all,
        so deciding it a second time here by a looser rule can only produce
        the two answers disagreeing -- which is what happened: a measurement
        the report called scattered (a spread of +-75 ms, taken over notes
        carrying bends and slides) still passed this check because it had
        enough samples, and set an offset out of noise that then sat in the
        config for days, silently swallowing a third of the real latency.

        Whatever remains after a press is measurable in the samples that
        follow, because _adjust_latency_offset clears the old ones -- so K
        pressed again converges rather than double-counting.
        """
        if self._matcher is None:
            return
        report = self._matcher.timing_report(AUTO_SYNC_MIN_SAMPLES)
        if report is None:
            return
        # "mixed" is latency with loose playing on top: the offset still
        # removes the constant part, which is exactly what K is for. The rest
        # -- scatter, a per-string split, or a median inside its own noise --
        # is not something one offset can fix, and applying one anyway is a
        # guess dressed up as a measurement.
        if report["verdict"] not in ("latency", "mixed"):
            return
        # The report's median is song milliseconds; the offset is stored in
        # real ones, so that a speed change does not invalidate it.
        self._adjust_latency_offset(-report["median_ms"] / self._tempo_factor)
        self._sync_applied = True

    def _reset_latency_offset(self) -> None:
        """Put latency compensation back to zero (Shift+K).

        The way out when the offset no longer resembles anything real and
        every fresh measurement is taken against the wrong note.
        """
        self._adjust_latency_offset(-self._config.audio_latency_offset_ms)
        self._sync_applied = False

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

    def _resume_audio(self) -> None:
        """Carry on capturing after a pause, without reopening anything.

        A pause used to stop the stream and a resume used to open a new one,
        which on Windows is a real device open and cost seconds every time the
        space bar was pressed -- exactly the fault "Seeking Must Not Reopen
        The Input Device" fixed for the arrow keys and never for the pause.

        It also threw the matcher away, so a run log lost every strike before
        the pause. Re-anchoring keeps both: the clock agrees with the song
        again and the strikes stamped while the picture stood still are
        dropped, because they belong to no moment in the song.
        """
        self._auto_gate_from_room()
        if self._audio_capture is None or self._matcher is None:
            self._start_audio()
            return
        if not getattr(self._audio_capture, "is_running", lambda: False)():
            self._start_audio()
            return
        self._reanchor_audio_clock()

    def _start_audio(self) -> None:
        """Start audio capture and create matcher."""
        try:
            from pickhero.audio.input import AudioCapture
            if self._audio_capture is None:
                self._audio_capture = AudioCapture(self._config)
            # start() builds a NEW stream and a new ring every time. Called on
            # a capture already running -- which is what happens when the
            # signal meter was switched on before the count-in -- the old
            # stream is never closed and goes on writing into the same ring,
            # so the sample counter advances at twice real time and every
            # strike after that is stamped further into the future.
            if getattr(self._audio_capture, "is_running", lambda: False)():
                # The stream has been open since the count-in began, which is
                # the whole point: the room can only be measured while the
                # song is not running, and before this it was opened here --
                # after the count-in -- so there was never anything to
                # measure and the automatic gate had nothing to go on.
                # Reusing it also saves a device open, which on Windows is
                # the freeze this project has now paid for three times.
                self._audio_capture.get_notes()
                self._audio_capture.get_strike_windows()
                self._audio_anchor_ms = self._audio_capture.elapsed_ms()
            else:
                self._audio_capture.stop()
                self._audio_capture.start()
                # A fresh stream restarts the sample counter, so the two
                # clocks agree here by construction.
                self._audio_anchor_ms = 0.0
            # The count-in has just been listened to; that is the room.
            self._auto_gate_from_room()
            self._audio_anchor_song_ms = self._playback_ms
            self._matcher = NoteMatcher(
                self._timeline,
                timing_window_ms=self._config.timing_window_ms,
                audio_offset_ms=(self._audio_anchor_song_ms
                                 - self._audio_anchor_ms * self._tempo_factor
                                 + self._sync_offset_song_ms()),
                chord_threshold_ms=self._config.chord_threshold_ms,
                note_filter=self._note_passes_filter if self._is_filter_active() else None,
                chord_partial_credit=self._chord_partial_credit,
                late_window_ms=self._late_window_ms(),
                chord_verifier=self._make_chord_verifier(),
                bend_check=getattr(self._config, "bend_check", True),
            )
            self._feedback.reset()
        except Exception as e:
            print(f"Audio start failed: {e}")
            self._audio_enabled = False

    def _start_capture_only(self) -> None:
        """Open the input without a matcher, to listen to the room.

        This is what makes the automatic gate possible: the room is what the
        microphone hears while the song is NOT running, and until the stream
        is open there is nothing to hear. Also what the signal meter needs.
        """
        try:
            from pickhero.audio.input import AudioCapture
            if self._audio_capture is None:
                self._audio_capture = AudioCapture(self._config)
            if getattr(self._audio_capture, "is_running", lambda: False)():
                return
            # start() builds a new stream and a new ring every time; called on
            # one already running, the old stream keeps writing into the same
            # ring and the sample counter advances at twice real time.
            self._audio_capture.stop()
            self._audio_capture.start()
        except Exception as e:
            print(f"Audio capture start failed: {e}")
            self._audio_enabled = False

    def _stop_audio(self) -> None:
        """Stop audio capture."""
        if self._audio_capture is not None:
            self._audio_capture.stop()

    def close_session(self) -> bool:
        """Write this sitting to the practice diary. Once, whenever it ends.

        Called when the player leaves the song and when the app shuts down --
        both, because either can be the end of a session and neither happens
        reliably. Idempotent for the same reason: leaving after finishing a
        song reaches this twice.
        """
        if self._session_written or not self._song_key:
            return False
        self._session_written = True
        stats = (self._matcher.get_statistics()
                 if (self._matcher is not None and self._song_completed) else None)
        session = practice_log.Session(
            started=self._session_started,
            song=self._song_key,
            seconds=round(self._session_seconds, 1),
            strikes=self._session_strikes,
            tempo_percent=int(round(self._tempo_factor * 100)),
            notes_hit=stats["hits"] if stats else None,
            notes_written=stats["total"] if stats else None,
            accuracy=(round(stats["accuracy_percent"], 1)
                      if stats and stats.get("total") else None),
        )
        try:
            return practice_log.append(session)
        except OSError:
            # A diary that cannot be written must not take the app down with
            # it; the playing is what matters and it has already happened.
            return False

    def stop_audio(self) -> None:
        """Public method to stop audio (called on state transitions)."""
        # Leaving the song is the end of the run, and until now it wrote
        # nothing: only reaching the last bar did. A four-minute song is
        # almost never played to its end while something is being diagnosed,
        # so the one run worth reading was the one that produced no file. It
        # says how far it got -- that is what notes_reached is for.
        if (self._matcher is not None and not self._song_completed
                and not self._run_log_written):
            self._export_run_log()
        self.close_session()
        self._stop_audio()
        self._audio_enabled = False
        for player in self._midi_all():
            player.close()
        self._midi_player = None
        self._guide_player = None
        if self._mp3_player is not None:
            self._mp3_player.close()
            self._mp3_player = None

    # -- MIDI backing track --

    # -- Recorded backing track (MP3) --

    def _load_mp3_for_song(self) -> None:
        """Open the recording this song was given, if it still exists."""
        self._mp3_player = None
        path = self._mp3_path()
        if not path:
            return
        player = Mp3Player(path)
        if player.open():
            self._mp3_player = player
            # The file as it was made: build tempo 1.0. Without this the
            # source never counts as fitting, and the app rebuilds a copy of
            # a song that needed none.
            self._mp3_loaded_build = 1.0
            self._mp3_note = ""
        else:
            # Named rather than swallowed: a file that has been moved or
            # renamed otherwise looks exactly like a feature that does not
            # work, and the player would go looking in the wrong place.
            self._mp3_note = player.error or "Could not open the backing track"

    def _mp3_path(self) -> str:
        getter = getattr(self._config, "mp3_path_for", None)
        return getter(self._song_key) if getter else ""

    def _mp3_offset(self) -> float:
        getter = getattr(self._config, "mp3_offset_for", None)
        return getter(self._song_key) if getter else 0.0

    def _mp3_ms(self, playback_ms: float) -> float:
        """Song position as the recording should hear it.

        A positive offset makes the recording sound LATER, so it is
        subtracted -- the same convention as the MIDI backing.
        """
        return playback_ms - self._mp3_offset()

    def _mp3_plays(self) -> bool:
        """Whether the recording may sound at all right now.

        **Including whether the song is running at all.** Every caller here
        reaches `Mp3Player.seek`, and seeking STARTS playback -- so without
        this, nudging the offset on a paused song set the recording playing
        against a picture standing still, which is exactly the state the
        offset is meant to be judged in. Pausing has to mean silence for both
        backings or neither.

        And whether the file loaded is the one this practice speed needs. Below
        full speed that is a stretched copy, which takes seconds to build --
        until it is there the recording stays silent rather than playing on at
        the wrong speed, which would put it a bar out within seconds.
        """
        return (self._mp3_player is not None
                and self._mp3_player.ready
                and not self._mp3_muted
                and self._playing
                and self._mp3_source_fits())

    def _seek_mp3(self, target_ms: float) -> None:
        """Move the recording, collapsing a burst of seeks into one.

        Seeking really means `play(start=)`, which decodes the file up to that
        point. One is fine; twenty-five a second -- which is what a held arrow
        key produces -- is a stuttering picture and a stuttering sound, on the
        frame's own thread.

        The first seek of a burst still happens at once, so a single press and
        a loop turn are unchanged. Inside the window the recording is held
        silent instead, because playing on from where it was is worse than
        nothing while the song is being scrubbed.
        """
        if self._mp3_player is None:
            return
        now = time.perf_counter()
        if now - self._mp3_last_seek_at >= MP3_SEEK_SETTLE_S:
            self._mp3_last_seek_at = now
            self._mp3_pending_seek_ms = None
            self._mp3_player.seek(target_ms)
            return
        self._mp3_pending_seek_ms = target_ms
        self._mp3_player.set_suspended(True)

    def _apply_pending_mp3_seek(self) -> bool:
        """Carry out a seek that was collapsed, once they have stopped."""
        if self._mp3_pending_seek_ms is None:
            return False
        now = time.perf_counter()
        if now - self._mp3_last_seek_at < MP3_SEEK_SETTLE_S:
            return True                    # still moving; stay silent
        target = self._mp3_pending_seek_ms
        self._mp3_pending_seek_ms = None
        self._mp3_last_seek_at = now
        self._mp3_player.seek(target)
        return False

    def _mp3_paused_only(self) -> bool:
        """True when the song standing still is the ONLY reason for silence."""
        return (not self._playing
                and self._mp3_player is not None
                and self._mp3_player.ready
                and not self._mp3_muted
                and self._mp3_source_fits())

    def _mp3_scale(self) -> float:
        """File milliseconds per song millisecond at the current speed.

        Set by the practice speed ALONE, and the speed correction below does
        not belong in it: the file plays at real time, so this is what makes
        one real second advance the song by `tempo` seconds. A correction put
        here would change how fast the song scrolls, which is the one thing
        it must not do. It goes into the length of the BUILT copy instead.
        """
        return 1.0 / self._tempo_factor if self._tempo_factor > 0 else 1.0

    def _mp3_rate(self) -> float:
        """How fast this recording runs against the tab; 1.0 is untouched."""
        getter = getattr(self._config, "mp3_rate_for", None)
        return getter(self._song_key) if getter else 1.0

    def _mp3_build_tempo(self) -> float:
        """What `timestretch.build` has to be asked for.

        It returns a copy `1 / tempo` times as long, and two independent
        things want a say in that length: the practice speed, and how far the
        recording runs away from the tab. At full speed with no correction
        this is 1.0 and the original file is used untouched.
        """
        return self._tempo_factor * self._mp3_rate()

    def _mp3_source_fits(self) -> bool:
        """Whether the loaded file plays this song at this speed AND rate.

        The scale alone cannot answer it: a rate correction changes the file
        while leaving the scale exactly where it was, so a check on the scale
        would report a fit and the correction would never be built.
        """
        if self._mp3_player is None:
            return False
        if abs(self._mp3_player.time_scale - self._mp3_scale()) >= 1e-6:
            return False
        if self._mp3_loaded_build is None:
            # A source this screen did not load itself. All that is known is
            # what the player reports, which is the scale -- enough while no
            # correction is wanted, and never enough once one is.
            return abs(self._mp3_rate() - 1.0) < 1e-6
        return abs(self._mp3_loaded_build - self._mp3_build_tempo()) < 1e-6

    def _ensure_mp3_source(self) -> None:
        """Load the file this speed needs, building it if it does not exist.

        At full speed that is the recording itself. Below it, a copy stretched
        by `audio/timestretch.py` -- longer, same pitch, so a solo can be
        practised slowly against the real thing instead of against a click.
        The build takes seconds on a whole song, so it runs on a thread and is
        swapped in when it lands; the recording is silent until then and the
        HUD says why. Every result is cached, so the same song at the same
        speed is instant ever after.
        """
        if self._mp3_player is None or self._mp3_muted or self._mp3_source_fits():
            return
        wanted = self._mp3_build_tempo()
        # The same threshold `stretch` itself gives up at: below it the build
        # returns the audio unchanged, so spending five seconds on a copy of
        # the original would be work bought with nothing.
        if abs(wanted - 1.0) < 1e-3:
            self._mp3_player.set_source(self._mp3_path(), self._mp3_scale())
            self._mp3_loaded_build = wanted
            return
        if self._mp3_stretch_matches(self._mp3_stretch_done, wanted):
            self._mp3_player.set_source(self._mp3_stretch_done[2],
                                        self._mp3_scale())
            self._mp3_loaded_build = wanted
            return
        if self._mp3_stretch_matches(self._mp3_stretch_failed, wanted):
            return                             # already said so on screen
        if self._mp3_stretch_wanted is not None:
            return                             # a build is already running
        self._start_mp3_stretch(wanted)

    def _mp3_stretch_matches(self, entry, tempo: float) -> bool:
        """Whether a finished build belongs to this recording at this speed."""
        return bool(entry) and entry[0] == tempo and entry[1] == self._mp3_path()

    def _start_mp3_stretch(self, tempo: float) -> None:
        """Build the stretched copy off the game loop."""
        path = self._mp3_path()
        if not path:
            return
        self._mp3_stretch_wanted = tempo
        self._mp3_stretch_progress = 0.0
        cache_dir = config_module.CONFIG_DIR / "stretched"

        def report(fraction: float) -> bool:
            self._mp3_stretch_progress = fraction
            # Stepping the tempo down three times should not build three
            # copies before reaching the one that was asked for.
            return abs(self._mp3_build_tempo() - tempo) < 1e-6

        def work() -> None:
            try:
                built = timestretch.build(Path(path), tempo, cache_dir, report)
                self._mp3_stretch_done = (tempo, path, str(built))
            except timestretch.Cancelled:
                pass                          # the speed moved on; not a fault
            except Exception as exc:
                # Not every format SDL can stream can also be decoded into
                # memory. Named rather than swallowed: "convert it" is a thing
                # the player can act on, silence is not.
                self._mp3_stretch_failed = (
                    tempo, path,
                    f"cannot be slowed down ({type(exc).__name__}) — "
                    f"convert it to OGG or WAV")
            finally:
                self._mp3_stretch_wanted = None

        self._mp3_stretch_thread = threading.Thread(target=work, daemon=True)
        self._mp3_stretch_thread.start()

    def _update_mp3(self) -> None:
        """Keep the recording where the song is, or silent if it may not play."""
        if self._mp3_player is None:
            return
        self._ensure_mp3_source()
        if self._apply_pending_mp3_seek():
            return                         # a seek is still being scrubbed
        if not self._mp3_plays():
            # WHY it may not sound decides what happens to it. A paused song
            # is held where it is; anything else -- muted, a different file
            # wanted, not ready -- really stops. Stopping for a pause is what
            # made the space bar cost a re-decode each way, and this runs
            # every frame, so it would undo the hold on the very next one.
            if self._mp3_paused_only():
                self._mp3_player.set_suspended(True)
            else:
                self._mp3_player.pause()
            self._mp3_stuck_since_ms = None
            return
        if self._mp3_player.suspended:
            self._mp3_player.set_suspended(False)
        target = self._mp3_ms(self._playback_ms)
        self._mp3_player.update(target)
        self._track_mp3_drift(target)

    def _track_mp3_drift(self, target_ms: float) -> None:
        """Notice a recording that has stopped following the song."""
        if abs(self._mp3_player.drift_ms(target_ms)) <= MP3_STUCK_DRIFT_MS:
            self._mp3_stuck_since_ms = None
        elif self._mp3_stuck_since_ms is None:
            self._mp3_stuck_since_ms = self._playback_ms

    def _mp3_is_stuck(self) -> bool:
        """True once the gap has stayed open long enough to mean something."""
        if self._mp3_stuck_since_ms is None:
            return False
        return self._playback_ms - self._mp3_stuck_since_ms > MP3_STUCK_FOR_MS

    def _toggle_mp3_backing(self) -> None:
        """Turn the recorded backing on or off (key: U). Independent of B."""
        self._mp3_muted = not self._mp3_muted
        self._config.mp3_backing_enabled = not self._mp3_muted
        self._config.save()
        if self._mp3_player is not None:
            self._mp3_player.set_muted(self._mp3_muted)
        if not self._mp3_muted and not self._mp3_path():
            self._mp3_note = "No backing track chosen yet — Shift+U to pick one"

    def _choose_mp3_backing(self) -> None:
        """Ask for the file chooser -- next frame, not this one.

        The dialog is the operating system's, and on Windows the first one
        takes seconds to appear. Opened straight from the key press, nothing
        is drawn in between: the app simply stops, which is indistinguishable
        from a dead key. So the note goes up first and the dialog opens once
        it has actually been on screen.
        """
        if not self._song_key:
            self._mp3_note = "No song loaded"
            return
        self._mp3_note = "Opening the file chooser..."
        self._mp3_dialog_due = True
        self._mp3_dialog_armed = False

    def _open_mp3_dialog(self) -> None:
        """Actually show the chooser. Blocks until the player answers."""
        current = self._mp3_path()
        start_dir = str(Path(current).parent) if current else self._config.songs_dir
        try:
            chosen = pick_audio_file(start_dir)
        finally:
            self._mp3_note = ""
            # Every key repeat that arrived while the dialog held the app is
            # still in the queue, and each one would open it again. That is
            # exactly what happened: the chooser came back over and over and
            # had to be cancelled each time. Key repeat is 40 ms, so seconds
            # of a blocked frame are dozens of them.
            try:
                pygame.event.clear(pygame.KEYDOWN)
                pygame.event.clear(pygame.KEYUP)
            except Exception:
                pass
        if not chosen:
            # Cancelled, or no tkinter on this machine. The two look the same
            # from here and neither is an error worth shouting about.
            return
        setter = getattr(self._config, "set_mp3_path_for", None)
        if setter is not None:
            setter(self._song_key, chosen)
            self._config.save()
        self._load_mp3_for_song()
        if self._mp3_player is not None:
            self._mp3_muted = False
            self._config.mp3_backing_enabled = True
            self._config.save()
            # No message on success. The ordinary line already names the file
            # AND the offset, and a note set here would sit on top of it for
            # the rest of the session -- which is exactly what hid the offset
            # from the one key that exists to change it.
            self._mp3_note = ""

    def _clear_mp3_note(self) -> None:
        """Drop a status message once it has been overtaken by events.

        A note outranks the ordinary line, so one left lying around silently
        replaces the live reading with old news.
        """
        self._mp3_note = ""

    def _reopen_output(self) -> None:
        """Close and reopen the audio output, keeping the song where it is.

        The player reported the sound turning flattering and quiet mid-
        session -- staying that way for every song afterwards, with the MIDI
        backing and no recording at all, while other applications were fine,
        until the app was restarted. That is a piece of state this process
        holds, and until now the only way to drop it was to lose the sitting.

        It is also the experiment that says WHERE. If the sound comes back,
        the fault is the mixer this reopens. If it does not, it is the shared
        Windows device and no key in this app can reach it.
        """
        if not output.reopen():
            self._say("Audio output could not be reopened")
            return
        # The mixer forgot the file along with everything else.
        self._mp3_loaded_build = None
        if self._mp3_player is not None:
            self._mp3_player.close()
            self._load_mp3_for_song()
            self._ensure_mp3_source()
            if self._mp3_plays():
                self._mp3_player.seek(self._mp3_ms(self._playback_ms))
        self._say(f"Audio output reopened — {output.describe()}")

    def _set_sync_point(self) -> None:
        """Two places lined up by hand become a speed for the whole song.

        The offset can only say WHERE the recording starts. A tab is a fixed
        grid and a band is not, so a downloaded tab and a recording of the
        real performance walk apart -- 1.09 % on the song this was built for,
        2.7 s over four minutes, which no single offset can follow. Given the
        offset that is right at two places, the line between them is the
        speed, and that the app can play.

        It is a repair and not a cure, and it has to be offered as one: on
        that song the local rate runs from 0.5 to 1.8 % section by section,
        because nobody played to a click. The best single correction leaves
        about 400 ms standing, against 2700 without it.
        """
        if self._mp3_player is None:
            self._say("No backing track — Shift+U to pick one")
            return
        here = (self._playback_ms, self._mp3_offset())
        if self._sync_anchor is None:
            self._sync_anchor = here
            self._sync_lines = [
                f"SYNC 1 of 2 — point 1: {_clock_text(here[0])} at "
                f"{_offset_text(here[1])}",
                "now play to near the END, line the recording up with "
                "Shift+N/M, then Shift+S again",
            ]
            return
        s1, o1 = self._sync_anchor
        s2, o2 = here
        self._sync_lines = [
            f"SYNC — point 1: {_clock_text(s1)} at {_offset_text(o1)}"
            f"   point 2: {_clock_text(s2)} at {_offset_text(o2)}",
        ]
        if abs(s2 - s1) < MIN_SYNC_SPAN_MS:
            # The offset is dialled in 10 ms steps, so a short span turns
            # one keypress into a large speed error. Kept rather than
            # dropped: the player only has to move further away.
            self._sync_lines.append(
                f"too close together — the two points must be at least "
                f"{MIN_SYNC_SPAN_MS / 1000:.0f} s apart. Point 1 is kept; "
                f"go further away and press Shift+S there.")
            return
        old_rate = self._mp3_rate()
        rate = old_rate * (1.0 - (o2 - o1) / (s2 - s1))
        if not (MIN_MP3_RATE <= rate <= MAX_MP3_RATE):
            self._sync_anchor = None
            self._sync_lines.append(
                f"that would play the recording at {100 / rate:.0f} % of its "
                f"speed — the two points cannot both be right. Start again "
                f"with Shift+S.")
            return
        setter = getattr(self._config, "set_mp3_rate_for", None)
        if setter is None:
            return
        setter(self._song_key, rate)
        # The offset has to move with it, or fixing the drift throws away the
        # alignment the player just demonstrated at the first point.
        self._set_mp3_offset(s1 - (s1 - o1) * old_rate / rate)
        self._sync_anchor = None
        self._config.save()
        self._sync_lines.append(
            f"recording stretched by {(1.0 / rate - 1.0) * 100:+.2f} % — "
            f"kept for this song. Ctrl+Shift+S puts it back.")

    def _clear_sync_rate(self) -> None:
        """Back to the recording as it was made."""
        self._sync_anchor = None
        self._sync_lines = []
        setter = getattr(self._config, "set_mp3_rate_for", None)
        if setter is not None:
            setter(self._song_key, 1.0)
            self._config.save()
        self._say("Recording speed correction cleared")

    def _set_mp3_offset(self, offset_ms: float) -> None:
        """Store the recording's offset and move the recording to match."""
        new = max(-MAX_MP3_OFFSET_MS, min(MAX_MP3_OFFSET_MS, offset_ms))
        setter = getattr(self._config, "set_mp3_offset_for", None)
        if setter is None or not self._song_key:
            return
        setter(self._song_key, new)
        self._config.save()
        # Whatever the note said, the number is the news now.
        self._clear_mp3_note()
        if self._mp3_player is not None and self._mp3_plays():
            self._mp3_player.seek(self._mp3_ms(self._playback_ms))

    def _adjust_mp3_offset(self, delta_ms: float) -> None:
        """Shift the recording against the notes (Shift+N earlier, Shift+M later).

        Its own offset, not the MIDI one: an MP3 decoder emits encoder padding
        before the music and how much depends on the encoder that made the
        file, so nothing about the MIDI backing predicts it.

        On a paused song this only stores the number. Moving the recording
        would mean starting it, and a recording playing under a frozen picture
        tells the player nothing about whether the two line up -- which is the
        one question the key exists to answer. The value is dialled in while
        the song runs, and the HUD shows it either way.
        """
        if not self._song_key:
            return
        self._set_mp3_offset(self._mp3_offset() + delta_ms)

    def _mp3_hud_text(self) -> str:
        """What the HUD says about the recording, or "" when there is nothing."""
        if self._mp3_player is not None and self._mp3_player.error:
            # A failure while playing outranks whatever was said when the file
            # was chosen -- that message is now stale news.
            return f"Audio: {self._mp3_player.error}"
        if self._mp3_note:
            return self._mp3_note
        if self._mp3_player is None:
            # Not nothing. A song with no recording showed no line at all, so
            # the key that assigns one was invisible and U looked as though it
            # had been removed -- which is what the player reported. A feature
            # that silently does nothing cannot be told from a broken one.
            return "Audio: no backing track — Shift+U to pick one"
        name = Path(self._mp3_path()).name
        if self._mp3_muted:
            return f"Audio: off (U) — {name}"
        if self._mp3_is_stuck():
            return (f"Audio: not following the song — this file cannot be "
                    f"seeked into; try OGG or WAV — {name}")
        if self._mp3_stretch_matches(self._mp3_stretch_failed,
                                     self._mp3_build_tempo()):
            return f"Audio: {self._mp3_stretch_failed[2]} — {name}"
        if not self._mp3_source_fits():
            # Two different reasons for the same wait, and a line saying
            # "fitting to 100 % speed" would name neither.
            if abs(self._tempo_factor - 1.0) < 1e-6:
                what = f"to this tab ({(1 / self._mp3_rate() - 1) * 100:+.2f} %)"
            else:
                what = f"to {int(self._tempo_factor * 100)} % speed"
            return (f"Audio: fitting {what} "
                    f"— {self._mp3_stretch_progress:.0%} — {name}")
        rate = self._mp3_rate()
        speed = ("" if abs(rate - 1.0) < 1e-6
                 else f"  [{(1 / rate - 1) * 100:+.2f} % Shift+S] ")
        if self._sync_anchor is not None:
            speed = "  [sync point 1 set — line up near the end, Shift+S] "
        return (f"Audio: {_offset_text(self._mp3_offset())}{speed} "
                f"(Shift+N/M ±10ms, Ctrl ±1s, Ctrl+Shift ±10s) — {name}")

    def _midi_all(self) -> list:
        """Both MIDI players, in the order they were made.

        Everything that moves the song -- a seek, a pause, a loop turn, a
        tempo change -- has to reach BOTH or they drift apart, and a guide
        that is a bar out is worse than no guide. Going through one list is
        what stops a new transport call being added to only one of them.
        """
        return [p for p in (self._midi_player, self._guide_player) if p is not None]

    def _init_guide_player(self, guide_track: BackingTrack) -> None:
        """The written part of the track being played, as something to hear."""
        try:
            player = MidiPlayer(guide_track)
            if player.open():
                player.set_muted(self._guide_muted)
                self._guide_player = player
            else:
                player.close()
        except Exception as exc:
            print(f"Guide track unavailable: {exc}")

    def _toggle_guide_track(self) -> None:
        """Hear the part you are meant to play, or stop hearing it (Shift+B)."""
        if self._guide_player is None:
            return
        self._guide_muted = not self._guide_muted
        self._guide_player.set_muted(self._guide_muted)
        self._config.guide_track_enabled = not self._guide_muted
        self._config.save()
        # It is seeked rather than simply unmuted: the cursor advanced while
        # it was silent, so unmuting alone would carry on from wherever the
        # song happens to be -- which is right -- but a mute leaves notes
        # hanging, and pause() is how they are let go.
        if self._guide_muted:
            self._guide_player.pause()
        else:
            self._guide_player.seek(self._backing_ms(self._playback_ms))

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
