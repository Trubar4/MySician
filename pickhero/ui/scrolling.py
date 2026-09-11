"""Scrolling note display for the playing screen.

Renders 6 string lanes with notes scrolling right-to-left, synchronized
to a playback clock. Optionally captures audio and shows hit/miss feedback.
"""

from __future__ import annotations

import bisect
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
from pickhero.audio import midi_playback
from pickhero.audio import output
from pickhero.audio.syncmap import SyncMap
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
from pickhero.ui import sheet

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
# How strongly a chord block tints the board under its notes. A tint and not
# a fill: the notes are the thing being read, and a solid slab under them
# would fight them for attention.
CHORD_BLOCK_ALPHA = 54
# Between the two grip cards.
CHORD_CARD_GAP = 10
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
# A press has to buy something a player can SEE. Measured over the guitar
# tracks of the four songs to hand, stepping the factor by 0.1 moves the
# window by 7-17 % while the trade is live -- and by 1.7 % at 0.7x -> 0.6x,
# where the head has already reached its floor and there is nothing left to
# spend. That step stored a new number, redrew nothing anybody could see, and
# was followed by a refusal at the next press, which is the "key that looks
# broken" this display has already been fixed for once. Anything from 2 to 7 %
# separates the live steps from the dead one.
SCROLL_FACTOR_MIN_GAIN = 0.05

# A DISCRETE SETTING IS NOT A SCRUB. `pygame.key.set_repeat(300, 40)` is one
# global setting for every key in the app, and 40 ms is 25 steps a second --
# which is right for an arrow key walking through a song and far too fast for
# a setting with eleven positions. Measured: the practice speed runs 50 % to
# 100 % in 5 % steps, so 700 ms of holding crosses the ENTIRE range, and the
# scroll factor's 22 positions take 1.14 s. On top of that, a frame that
# stalls drains every repeat that arrived during it in one go, so one press
# can land at the far end of the range -- which is exactly what the player
# reported: a short press showing 95 % for a moment and then 50 %.
#
# 150 ms a step walks the whole speed range in 1.5 s, which reads as a
# deliberate movement, and it is nine times a frame, so a burst drained in one
# frame applies once. The first press of a key is never delayed -- a key that
# feels dead is the fault this display has already been fixed for twice -- so
# only REPEATS are gated, and coming off the key clears the gate outright.
STEP_KEY_REPEAT_S = 0.15

# A rest is worth a key only when it is longer than the music writes rests for.
# Measured over every track of the four songs to hand: a guitar track's inner
# rests are either 4-6 s -- two bars, part of the music, and the player counts
# through them -- or 12 s and up, which is a section they do not play, with
# nothing at all in between. Bass and vocal tracks run to 44 and 100 s. So
# anything from 7 to 12 s picks out exactly the same rests on this material:
# the constant sits on a plateau rather than on a knife edge.
GAP_MIN_MS = 8000.0
# Landing ON the next note leaves no time to get the hand there. Three seconds
# is a bar and a half at 100 BPM -- long enough to read the fret and place the
# fingers, short enough not to be a second rest.
GAP_LEAD_IN_MS = 3000.0

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

# How far a gap between two pictures may sit from the usual one before it
# counts as uneven. A fifth is well past what the eye forgives on a moving
# note and well clear of the millisecond or so a timer costs to read.
FRAME_EVEN_FRACTION = 0.2

# The longest a single frame may move the song. Fifteen frames' worth: beyond
# that nothing was drawn and nothing was heard, so charging the song for it
# only teleports the picture.
MAX_FRAME_STALL_S = 0.25

# When a recording is playing, IT is the clock and the picture is pulled to
# it. Two numbers decide how that pull feels.
#
# Past this the two describe different moments -- a seek, a loop turn, a
# recording that has just started -- and the picture jumps rather than
# creeping there over half a minute.
SYNC_SNAP_MS = 1500.0
# Otherwise the picture may be corrected by at most this fraction of the time
# that really passed, so it is never seen to jump. The mismatch being chased
# is about 1 %, so five times that is plenty of authority, and pygame's
# get_pos() moves in buffer-sized steps -- the slew limit is what smooths
# those into a scroll nobody can see move.
SYNC_PULL_FRACTION = 0.05

# How far apart the two sync points must be. The offset is dialled in 10 ms
# steps, so one keypress over a short span is a large speed error: over 30 s
# it is 0.03 %, against the ~1 % the correction is for; over 5 s it would be
# 0.2 %. The start and the end of the song are what this wants.
MIN_SYNC_SPAN_MS = 30_000.0

# How many rows of music the page view shows at once, and how wide its
# playhead is drawn. Two rows because that is what a reader uses -- the one
# under the hand and the one arriving -- and everything past it was spending
# the room the HUD needed. Measured on a real engraving a row is 0.107 of
# the page against a staff band of 0.039, so a row is mostly the air above
# it and the count has to be in rows rather than in staves.
TAB_SYSTEMS_SHOWN = 2
TAB_PLAYHEAD_PX = 5
# How long the page takes to slide up by a row, and the distance past which
# a move is not a page turn at all. Seeking across a song must not crawl.
TAB_GLIDE_S = 0.25
TAB_GLIDE_SNAP_PX = 1200.0
# Air between the last line of the HUD and the top of the music. The margin
# itself is measured -- see _hud_top_used.
TAB_TOP_GAP = 26

# The three ways this screen can draw one song. Not three screens: the clock,
# the keys, the matcher and the offsets are the same in all of them, and this
# project has already paid for four readers of one plan.
VIEWS = ("standard", "hybrid", "tab")
# What the footer calls each one. Short, because it sits in a line of twelve
# entries and the long form is in the settings screen and the help.
VIEW_SHORT = {"standard": "Standard", "hybrid": "Hybrid", "tab": "Tab page"}
# What each sync source is called on screen. The words say what will HAPPEN
# when Ctrl+S is pressed, not what the setting is named in the file.
SYNC_SOURCE_WORDS = {
    "auto": "listen, then Songsterr if that fails",
    "listen": "listen only",
    "songsterr": "Songsterr's bar map only",
    "hand": "by hand only — Ctrl+S does nothing",
}
# How many tunings the one-line strip offers, and how far either side of the
# one being played. See tuning_segments for why it is one down and three up.
TUNINGS_SHOWN = 5
TUNINGS_BELOW = 1
TUNINGS_ABOVE = 3
VIEW_NAMES = {
    "standard": "Standard — the board scrolls",
    "hybrid": "Hybrid — the sheet holds still",
    "tab": "Tab page — engraved",
}
# Room either side of the sheet, so the first and last note of a row are not
# against the window edge.
SHEET_SIDE_PAD = 24
# A move longer than this many rows is a seek, not a page turn, and arrives
# rather than sliding. In ROWS because a row is whatever the head size makes
# it -- see _slide_sheet.
SHEET_SNAP_ROWS = 3.0
# How dark a string's core is against its highlight, on the sheet.
SHEET_STRING_CORE = 0.62
# The grip cards, a tenth smaller than they were drawn on the scrolling
# board. They sit in the corner the music now reaches into, and a tenth is
# what the player asked for after seeing them over the top string.
CHORD_CARD_SCALE = 0.9
# Clear space a chord name needs after it on the sheet before the next one
# may be drawn. Below this the two read as one word.
SHEET_NAME_GAP = 10
# How much silence at the END of a tab is worth explaining. Below a few
# seconds it is the last chord ringing out; above it the export was padded
# to the end of the sheet and the clock stops matching the recording.
SILENT_TAIL_MS = 5000.0

# How a note's verdict is shown on an engraved page. PENDING is absent on
# purpose: a dot under every note not yet reached would bury the music under
# its own labelling, the same reason a palm mute is badged once per run.
_TAB_VERDICT_COLOURS = {
    MatchType.HIT: "feedback_hit",
    MatchType.CLOSE: "feedback_close",
    MatchType.MISS: "feedback_miss",
}

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


# Note heads, drawn once and blitted after that. A frame of a real song makes
# 48 rounded-rect calls -- 24 notes, fill and border -- and measured on the
# player's own song 46 of the 48 are the SAME size, because one head size is
# chosen for the whole song. Rounding is what costs: 24 heads drawn as rounded
# rects is 0.519 ms and the same 24 blitted is 0.056 ms.
#
# Cleared wholesale at the cap, the way the font cache is: the only thing that
# really varies is a colour mid-animation, redrawing one head is a fraction of
# a millisecond, and an LRU here would be bookkeeping to save nothing.
_HEAD_CACHE: dict = {}
_HEAD_CACHE_MAX = 256


_BLOCK_CACHE: dict = {}
_BLOCK_CACHE_MAX = 64


def _chord_block_surface(width: int, height: int, colour) -> pygame.Surface:
    """One chord block, tinted and outlined, ready to blit.

    Cached for the same reason the note heads are: an SRCALPHA surface per
    block per frame cost 2.0 ms of a 16.7 ms budget on a real song, which is
    an eighth of the frame spent allocating pictures that repeat. A song
    chooses one head size, so the blocks come in very few sizes too.
    """
    key = (width, height, tuple(colour))
    got = _BLOCK_CACHE.get(key)
    if got is not None:
        return got
    if len(_BLOCK_CACHE) >= _BLOCK_CACHE_MAX:
        _BLOCK_CACHE.clear()
    block = pygame.Surface((width, height), pygame.SRCALPHA)
    pygame.draw.rect(block, (*colour, CHORD_BLOCK_ALPHA),
                     block.get_rect(), border_radius=8)
    pygame.draw.rect(block, colour, block.get_rect(), 2, border_radius=8)
    _BLOCK_CACHE[key] = block
    return block


def _segments_width(font, segments) -> int:
    """How wide a row of (text, colour) segments comes out."""
    return sum(font.size(text)[0] for text, _ in segments)


def _wrap_segments(font, segments, width: int, sep: str = "  |  "):
    """Break (text, colour) segments into rows that fit, at the separators.

    The separator belongs to no entry, so it is emitted as its own segment in
    the ordinary text colour -- a bar drawn in the colour of the entry beside
    it reads as part of that entry.
    """
    rows, row, used = [], [], 0
    bar = (sep, "hud_text")
    for segment in segments:
        piece = ([bar, segment] if row else [segment])
        wanted = _segments_width(font, piece)
        if row and used + wanted > width:
            rows.append(row)
            row, used, piece = [], 0, [segment]
            wanted = _segments_width(font, piece)
        row += piece
        used += wanted
    if row:
        rows.append(row)
    return rows or [[]]


def _glide_step(target: float, value: float, frm: float, to: float,
                at: float, now: float,
                snap_px: float) -> tuple[float, float, float, float]:
    """One frame of a slide towards `target`. Returns (value, from, to, at).

    Pure arithmetic so both views that slide can share it and neither can
    drift from the other -- and so it can be tested by winding `now` rather
    than by waiting a quarter of a second per assertion.
    """
    if target != to:
        far = abs(target - to) > snap_px
        frm = target if far else value
        to = target
        at = now
    share = (now - at) / TAB_GLIDE_S
    if share >= 1.0:
        return to, frm, to, at
    eased = share * share * (3.0 - 2.0 * share)
    return frm + (to - frm) * eased, frm, to, at


def _head_surface(width: int, height: int, colour, border) -> pygame.Surface:
    """One note head, ready to blit. Same shape for every note.

    Transparent outside the rounded corners, so the board and the fret wires
    show through them exactly as they did when this was drawn in place.
    """
    key = (width, height, tuple(colour), tuple(border))
    got = _HEAD_CACHE.get(key)
    if got is not None:
        return got
    if len(_HEAD_CACHE) >= _HEAD_CACHE_MAX:
        _HEAD_CACHE.clear()
    head = pygame.Surface((width, height), pygame.SRCALPHA)
    try:
        head = head.convert_alpha()
    except pygame.error:
        pass                            # no display yet; the surface still works
    rect = pygame.Rect(0, 0, width, height)
    corner = height // 2
    pygame.draw.rect(head, colour, rect, border_radius=corner)
    pygame.draw.rect(head, border, rect, width=2, border_radius=corner)
    _HEAD_CACHE[key] = head
    return head


def clear_font_cache() -> None:
    """Drop every cached font and every surface drawn with one.

    Both belong to ONE pygame session. A Font kept across `pygame.quit()` is a
    dangling pointer and rendering with it segfaults -- verified, not assumed,
    and it is why this function exists rather than being left to chance. The
    app calls it when it starts a session; the test suite calls it between
    tests, several of which run an init/quit cycle of their own.

    The note heads go with them: a Surface outlives `pygame.quit()` no better
    than a Font does, and they are drawn with the theme's colours, which a
    theme change moves.
    """
    _get_font.cache_clear()
    _HEAD_CACHE.clear()
    _BLOCK_CACHE.clear()


def shift_held(event) -> bool:
    """Was SHIFT down for this key press -- however the keyboard says so.

    Three signals, because one was not enough on the player's machine: a key
    arrived carrying a capital letter with no shift bit in `event.mod` at
    all, and the shortcut fell through to its unshifted twin. The event's own
    modifiers are the normal answer, the live keyboard state catches a stale
    one, and the CHARACTER catches the rest -- a capital letter is what was
    typed, whatever the layout did to say it.

    `pygame.key.get_mods()` needs the video system and raises without it, so
    it is guarded: a key handler that can raise takes the app down with it.
    """
    if getattr(event, "mod", 0) & pygame.KMOD_SHIFT:
        return True
    try:
        if pygame.key.get_mods() & pygame.KMOD_SHIFT:
            return True
    except pygame.error:
        pass
    letter = getattr(event, "unicode", "") or ""
    return len(letter) == 1 and letter.isalpha() and letter.isupper()


def _wrap_on_bars(line: str, font, width: int) -> list[str]:
    """Break one footer line into as many as it takes to fit `width`.

    At the "|" the entries already carry, so a shortcut is never split down
    the middle. A single entry wider than the screen is left alone -- there
    is nothing to be done about it here, and shortening the text is a
    decision for whoever wrote it.
    """
    if font.size(line)[0] <= width:
        return [line]
    out: list[str] = []
    current = ""
    for part in line.split("|"):
        candidate = part if not current else f"{current}|{part}"
        if current and font.size(candidate)[0] > width:
            out.append(current.strip())
            current = part
        else:
            current = candidate
    if current.strip():
        out.append(current.strip())
    return out or [line]


def format_time(ms: float) -> str:
    """Format milliseconds as M:SS."""
    total_seconds = max(0, int(ms / 1000))
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"


def _engraver_state() -> str:
    """Whether verovio is present AND able to engrave, in one line.

    Asked on a THREAD, because that is where the app engraves and verovio's
    resource path is thread-local: a check run on the main thread reports
    `ready` for a build whose pages all come back blank.
    """
    answer: list[str] = []
    thread = threading.Thread(target=lambda: answer.append(_engraver_check()),
                              daemon=True)
    thread.start()
    thread.join(60)
    return answer[0] if answer else "present but it never answered"


def _ink_pixels(surface: pygame.Surface) -> int:
    """How many pixels on this page are actually dark.

    A page that "loaded" is not a page that was drawn -- SDL's SVG loader
    managed 20 pixels of 1.2 million, and every check this project had
    passed on it.
    """
    import numpy as np
    from pygame import surfarray
    return int((surfarray.array3d(surface).mean(axis=2) < 100).sum())


def _engraver_check() -> str:
    """The check itself. Never call this on the main thread -- see above."""
    from pickhero.ui.tab_view import rasterise
    try:
        import verovio
    except Exception as exc:
        return f"absent ({type(exc).__name__})"
    try:
        verovio.setDefaultResourcePath(
            str(Path(verovio.__file__).parent / "data"))
        toolkit = verovio.toolkit()
        ok = toolkit.loadData(
            '<?xml version="1.0"?><score-partwise version="3.1">'
            '<part-list><score-part id="P1"><part-name>G</part-name>'
            '</score-part></part-list><part id="P1"><measure number="1">'
            "<attributes><divisions>1</divisions><time><beats>4</beats>"
            "<beat-type>4</beat-type></time></attributes>"
            "<note><rest/><duration>4</duration><type>whole</type></note>"
            "</measure></part></score-partwise>")
    except Exception as exc:
        return f"present but broken ({type(exc).__name__})"
    if not ok:
        return "present but its data files are missing"
    # The rasteriser is the other half and it is a separate package: the page
    # is engraved by verovio and drawn by resvg, and either can be absent.
    # Counted through the app's own path -- resvg to PNG, pygame to a
    # surface -- so this answers the question the tab view actually asks.
    try:
        surface = rasterise(toolkit.renderToSVG(1), 200)
        ink = _ink_pixels(surface)
    except Exception as exc:
        return f"no rasteriser ({type(exc).__name__})"
    return "ready" if ink else "the rasteriser drew nothing"


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


def _technique_flags(note) -> str:
    """What the tab asks for at this note, in one field.

    One letter each, so a whole song's techniques fit in a column: b bend,
    s slide, h hammer-on or pull-off, d dead note, p palm mute, l let ring.
    A dash where the tab asks for nothing, because an empty cell in a
    tab-separated table is a column that has gone missing.
    """
    flags = ""
    if note.bend:
        flags += "b"
    if note.slide_to_next or note.slide_in or note.slide_out:
        flags += "s"
    if note.hammer_to_next:
        flags += "h"
    if note.dead:
        flags += "d"
    if note.palm_mute:
        flags += "p"
    if getattr(note, "let_ring", False):
        flags += "l"
    return flags or "-"


class PlayingScreen:
    """Scrolling tab display with playback clock and optional audio matching."""

    def __init__(self, timeline: Timeline, visible_beats: int = 4,
                 hit_zone_fraction: float = 0.20, config: Config | None = None,
                 backing_track: BackingTrack | None = None,
                 guide_track: BackingTrack | None = None,
                 progress_tracker: ProgressTracker | None = None,
                 song_key: str = "", song_path: str = "",
                 transpose: int = 0):
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
        # The gaps BETWEEN pictures, which is a different question from how
        # long one took to draw. See `record_frame_shown`.
        self._frame_intervals: list[float] = []
        self._frame_shown_at: float | None = None
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
        # Where the tab came from. Only the auto-sync wants it,
        # and only because a recording is the whole band.
        self._song_path = song_path
        # How far this song is being PLAYED from how it is written. The
        # timeline handed in has already been shifted, so this is only kept
        # to work back to the written tuning and to say so on screen.
        self._transpose = int(transpose)
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
        self._mp3_stretch_done: tuple[float, str, str, float] | None = None
        self._mp3_stretch_failed: tuple[float, str, str, float] | None = None
        # How far the build has got, 0 to 1. A whole song is five to twenty
        # seconds of work and the player hears nothing for all of it -- "one
        # moment" with nothing moving is indistinguishable from broken.
        self._mp3_stretch_progress = 0.0
        # The build the loaded source was made for, so a rate change is seen
        # even though it leaves time_scale untouched.
        self._mp3_loaded_build: float | None = None
        # Which transpose the loaded copy was built for. The scale cannot
        # answer it -- a pitch shift leaves the length exactly alone, which
        # is the whole point of it.
        self._mp3_loaded_source_transpose: int = 0
        # The first of the two places the player lines the recording up at,
        # as (song ms, offset ms). Not remembered across songs: it describes
        # one act of syncing, not a setting.
        self._sync_anchor: tuple[float, float] | None = None
        self._sync_key_held: bool = False
        # What the last sync did, kept on screen rather than announced once:
        # the player is navigating a four-minute song between the two points
        # and an expiring note is gone long before the second one is set.
        self._sync_lines: list[str] = []
        # How far the picture ever had to be pulled to stay with the
        # recording, and whether the recording ever led at all. A percentage
        # cannot be debugged and neither can a sync: these two say whether
        # the map was doing anything and how much was left over.
        self._worst_sync_pull_ms: float = 0.0
        self._mp3_led: bool = False
        # How much the player spooled. Not reset by a loop or a song end:
        # the question is what this sitting did.
        self._seeks: int = 0
        # How often the picture had to JUMP to stay with the recording. A
        # pull nobody can see is the design; a jump is a fault, and without a
        # count it is only ever a report.
        self._mp3_snaps: int = 0
        # Finding the sync points by listening. On a thread: a four-minute
        # song is a couple of seconds of arithmetic, and seconds in the game
        # loop is a frozen app -- a bill this project has paid three times.
        self._auto_sync_thread: threading.Thread | None = None
        self._auto_sync_progress: tuple[float, str] = (0.0, "")
        self._auto_sync_result: tuple | None = None
        # The engraved page view. Asked for on one frame and started on the
        # next, so the "engraving..." line is really on screen before the
        # work begins -- and the work itself runs on a thread, because
        # rasterising a whole song is seconds and seconds in the game loop
        # is a frozen app.
        # Which of the three views is up. A string rather than a pair of
        # flags: two booleans have four states and only three of them mean
        # anything, and the fourth is the bug that gets shipped.
        self._view: str = (getattr(config, "default_view", "standard")
                           if getattr(config, "default_view", "standard")
                           in VIEWS else "standard")
        # The chord extension: the two grip cards AND the blocks that say
        # which notes are one chord. ONE switch, because it is one idea --
        # "show me the chords" -- and two keys for two halves of an answer
        # is how a panel ends up with settings nobody can find. Off by
        # default: it is an extension to the normal view, not the view.
        self._chord_mode: bool = bool(getattr(config, "chord_view", False))
        self._chord_shapes: list = []
        # (rest starts, next note) for every stretch of the song with
        # nothing to play on THIS track. Built once per song, because it
        # is a walk over every note and this display has been bitten
        # twice by work that looked cheap until it ran once a frame.
        self._rests: list[tuple[float, float]] = []
        self._tab_engraving = None
        self._tab_due: bool = self._view == "tab"
        self._tab_error: str = ""
        self._tab_zoom: int = 2
        self._tab_thread: threading.Thread | None = None
        self._tab_progress: float = 0.0
        # (engraving or "error", the zoom it was built for). Handed from the
        # thread to the main loop, which is the only place a pygame surface
        # may be converted for the display.
        self._tab_ready: tuple = (None, -1)
        # Where the page is scrolled to. Held between frames on purpose: the
        # page moves when the music leaves the screen, not continuously.
        # Where the page is, where it is sliding to, and when it started.
        # A float because a quarter of a second at 60 Hz is fifteen steps and
        # rounding each of them to a whole pixel is a stutter of its own.
        self._tab_scroll: float = 0.0
        self._tab_glide_from: float = 0.0
        self._tab_glide_to: float = 0.0
        self._tab_glide_at: float = 0.0
        # The sheet view: the rows as laid out, what they were laid out
        # FOR, and where the paper has slid to. Laid out once per song and
        # size rather than per frame -- it walks every bar of the song, which
        # is exactly the loop this display has had to move out of a frame
        # three times.
        self._sheet_rows: list = []
        self._sheet_next: dict = {}
        self._sheet_key: tuple = ()
        self._sheet_zoom: int = sheet.ZOOM_DEFAULT
        self._sheet_scroll: float = 0.0
        self._sheet_glide_from: float = 0.0
        self._sheet_glide_to: float = 0.0
        self._sheet_glide_at: float = 0.0
        # Whether the sync panel is open. Everything about lining sound up
        # against the notes lives in it, and none of it is needed while
        # playing -- which is what the screen is for.
        self._show_sync: bool = False
        # Which page of how many the tab view last drew, so the footer can
        # say it without the drawing having to reach the footer. The page
        # number comes from the clock and not from the layout, so unlike
        # bars-per-row it cannot feed back into the room it is measured in.
        self._tab_pages: tuple[int, int] = (0, 0)
        if backing_track is not None and len(backing_track) > 0:
            self._init_midi_player(backing_track)
        if guide_track is not None and len(guide_track) > 0:
            self._init_guide_player(guide_track)
        self._load_mp3_for_song()
        # A song opened with sync points already measured has to SHOW them.
        # They were stored and used all along -- the panel simply started
        # empty every time, so the player had no way to tell a synced song
        # from one nobody had touched. That is the "a feature that cannot be
        # seen working" fault, applied to the most expensive setting there is.
        if self._mp3_anchors():
            self._describe_sync()

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
        # Which stepping key is being held, and when it last acted.
        self._step_key: int | None = None
        self._step_key_at: float = 0.0
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
        if shift_held(event):
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
        # Counted, because the player has now identified seeking as what the
        # stutter follows -- and a run log that cannot say how much spooling
        # a run contained cannot correlate anything with it.
        self._seeks += 1
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

        if self._tab_due:
            # Drawn last frame, so the "engraving..." note is really on
            # screen. Before the paused branch below, because a paused song
            # is exactly when the page view gets opened.
            self._build_tab_engraving()
        if self._auto_sync_result is not None:
            self._take_auto_sync()
        if self._tab_ready[0] is not None:
            # The thread is done. Taking it here rather than there is not
            # tidiness: a surface must be convert()ed on the thread that
            # owns the display, or every blit re-converts it.
            self._take_tab_engraving()

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
            self._follow_recording(real_elapsed)
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
            # Coming off a stepping key makes the next press instant again,
            # so two deliberate presses in quick succession both count. Exact
            # where the timer alone would be a guess -- the same reason the
            # sync key above waits for the key to come up.
            if event.key == self._step_key:
                self._step_key = None
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
            if shift_held(event):
                self._reopen_output()
            else:
                self._toggle_audio()
        elif event.key == pygame.K_PAGEDOWN:
            if self._step_key_ready(event.key):
                self.set_tempo_factor(self._tempo_factor - 0.05)
        elif event.key == pygame.K_PAGEUP:
            if self._step_key_ready(event.key):
                self.set_tempo_factor(self._tempo_factor + 0.05)
        elif event.key == pygame.K_i:
            self._set_loop_start(self._playback_ms)
        elif event.key == pygame.K_o:
            self._set_loop_end(self._playback_ms)
        elif event.key == pygame.K_p:
            self._toggle_loop()
        elif event.key == pygame.K_r:
            return self._next_tuning(-1 if shift_held(event)
                                     else +1)
        elif (event.key == pygame.K_s and event.mod & pygame.KMOD_ALT
                and not shift_held(event)):
            # Tested before the bare S below, which would otherwise swallow
            # it: an elif chain is read in order, and Alt is neither Ctrl nor
            # Shift.
            self._cycle_sync_source()
        elif (event.key == pygame.K_s and event.mod & pygame.KMOD_CTRL
                and not shift_held(event)):
            self._start_auto_sync()
        elif (event.key == pygame.K_s and not shift_held(event)
                and not event.mod & pygame.KMOD_CTRL):
            self._toggle_sync_block()
        elif event.key == pygame.K_s and shift_held(event):
            if self._sync_key_held:
                return None                    # a repeat, not a second press
            self._sync_key_held = True
            if event.mod & pygame.KMOD_CTRL:
                self._clear_sync_rate()
            else:
                self._set_sync_point()
        elif event.key == pygame.K_b:
            if shift_held(event):
                self._toggle_guide_track()
            else:
                self._toggle_backing()
        elif event.key == pygame.K_x:
            self._take_gate_by_hand()
            self.set_noise_gate_db(self._noise_gate_db - 5)
        elif event.key == pygame.K_c and shift_held(event):
            # Tested BEFORE the plain C below, which raises the noise gate:
            # an `elif` chain is read in order, so a shifted key placed after
            # its unshifted twin is never reached at all.
            self._chord_mode = not self._chord_mode
            if hasattr(self._config, "chord_view"):
                self._config.chord_view = self._chord_mode
                self._config.save()
            self._say("Chord view on — grips and blocks"
                      if self._chord_mode else "Chord view off")
        elif event.key == pygame.K_c:
            # C is the key that walked this player's gate to the old ceiling,
            # five decibels at a time, on advice the app kept repeating --
            # see gate_band. set_noise_gate_db is what bounds it.
            self._take_gate_by_hand()
            self.set_noise_gate_db(self._noise_gate_db + 5)
        elif event.key == pygame.K_t:
            if shift_held(event):
                self._cycle_view()
            else:
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
        elif event.key == pygame.K_z:
            if shift_held(event):
                self._toggle_steady_pace()
            else:
                self._toggle_vsync()
        elif event.key == pygame.K_e:
            self._skip_rest()
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
            if event.mod & pygame.KMOD_CTRL:
                self._paste_songsterr_link()
            elif shift_held(event):
                self._choose_mp3_backing()
            else:
                self._toggle_mp3_backing()
        elif event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
            # Same key, the meaning of the view it is pressed in: on a page
            # there is no scroll speed to set and zoom is what it wants.
            if self._step_key_ready(event.key):
                if self._tab_mode:
                    self._zoom_tab(+1)
                elif self._view == "hybrid":
                    self._size_sheet(+1)
                else:
                    self._adjust_scroll_factor(SCROLL_FACTOR_STEP)
        elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            if self._step_key_ready(event.key):
                if self._tab_mode:
                    self._zoom_tab(-1)
                elif self._view == "hybrid":
                    self._size_sheet(-1)
                else:
                    self._adjust_scroll_factor(-SCROLL_FACTOR_STEP)
        elif event.key == pygame.K_l:
            self._loop_weakest_section()
        elif event.key == pygame.K_h:
            self._show_help = not self._show_help
        elif event.key == pygame.K_y:
            if shift_held(event):
                self._export_timing_samples()
            else:
                self._show_timing = not self._show_timing
        elif event.key == pygame.K_w:
            self._toggle_wait_mode()
        elif event.key == pygame.K_k:
            if shift_held(event):
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
        if self._tab_mode:
            # The same song, the same clock, the same keys -- only drawn as
            # a page instead of a scrolling board. A second SCREEN would be
            # a second copy of the transport, the offsets and the loop, and
            # this project has already paid for four readers of one plan.
            self._draw_tab_page(surface, layout)
            # _draw_hud already puts the score up when the song is over.
            # Calling the overlay here as well drew it unconditionally --
            # it takes no decision of its own -- so the page view showed
            # nothing but the end of the song, every time.
            self._draw_hud(surface, layout)
            if self._show_help:
                self._draw_help_overlay(surface, layout)
            return
        if self._view == "hybrid":
            # The same notes the board draws, in the same colours, on a page
            # that does not move. Nothing here is a second copy of anything:
            # the chord cards, the HUD and the help are the ones the board
            # uses, because they never depended on the scrolling.
            self._draw_sheet(surface, layout)
            self._draw_chord_cards(surface, layout)
            self._draw_hud(surface, layout)
            if self._show_timing:
                self._draw_timing_overlay(surface, layout)
            if self._show_help:
                self._draw_help_overlay(surface, layout)
            if self._track_menu_open:
                self._draw_track_menu(surface)
            return
        self._draw_lanes(surface, layout)
        self._draw_loop_region(surface, layout)
        self._draw_hit_zone(surface, layout)
        # UNDER the notes: the block says "these belong together and this is
        # what it is called", the heads keep saying which string went right.
        self._draw_chord_blocks(surface, layout)
        self._draw_notes(surface, layout)
        self._draw_chord_names(surface, layout)
        self._draw_chord_cards(surface, layout)
        self._draw_hud(surface, layout)

        if self._show_timing:
            self._draw_timing_overlay(surface, layout)
        if self._show_help:
            self._draw_help_overlay(surface, layout)
        if self._track_menu_open:
            self._draw_track_menu(surface)

    # -- Pure math helpers (testable without display) --

    @property
    def _tab_mode(self) -> bool:
        """Kept as a name because half this file asks the question that way.

        There are three views now, and "the page or not the page" is still a
        real question for the keys that mean something different on paper.
        """
        return self._view == "tab"

    @_tab_mode.setter
    def _tab_mode(self, on: bool) -> None:
        self._set_view("tab" if on else "standard")

    def _cycle_view(self) -> None:
        """Shift+T walks all three, in the order they cost the eye.

        The scrolling board, the sheet that holds still, the engraved page.
        One key rather than three: a view is chosen by looking at it, so what
        it needs is a way to keep pressing until the right one is up.
        """
        self._set_view(VIEWS[(VIEWS.index(self._view) + 1) % len(VIEWS)])

    def _toggle_tab_mode(self) -> None:
        """Straight to the engraved page and straight back."""
        self._set_view("standard" if self._view == "tab" else "tab")

    def _set_view(self, view: str) -> None:
        self._view = view
        if view == "tab" and self._tab_engraving is None and not self._tab_error:
            # Not built here: the note below has to reach the screen first.
            self._tab_due = True
            self._say("Engraving the tab…")
        else:
            self._say(VIEW_NAMES[view])

    def _build_tab_engraving(self) -> None:
        """The one slow thing, on a thread.

        Three pages of a real song is 3.1 s -- the ENGRAVING is fast (0.2 s)
        and the rasterising is not, because SDL cannot draw verovio's output
        and resvg has to. Three seconds in the game loop is a frozen app,
        which this project has already shipped twice.
        """
        self._tab_due = False
        if self._tab_thread is not None and self._tab_thread.is_alive():
            return
        zoom = self._tab_zoom
        width = self._last_layout.screen_w if self._last_layout else 1280

        def report(fraction: float) -> bool:
            self._tab_progress = fraction
            # Stepping the zoom twice should not build the pages of the one
            # nobody wants any more.
            return self._tab_zoom == zoom

        def work() -> None:
            from pickhero.ui.tab_view import Cancelled, TabEngraving
            try:
                built = TabEngraving(self._timeline, zoom, width, report)
            except Cancelled:
                return                        # the zoom moved on; not a fault
            except Exception as exc:
                # Named rather than swallowed: without the engraver or its
                # rasteriser this is the only place the player finds out.
                self._tab_error = f"{type(exc).__name__}: {exc}"
                self._tab_ready = ("error", zoom)
                return
            self._tab_ready = (built, zoom)

        self._tab_progress = 0.0
        self._tab_thread = threading.Thread(target=work, daemon=True)
        self._tab_thread.start()

    def _take_tab_engraving(self) -> None:
        """Swap in a finished engraving, on the main thread.

        The pages are scaled and CONVERTED here rather than on the thread:
        convert() needs the display, and a surface converted anywhere else is
        one the display converts again on every blit.
        """
        ready, zoom = self._tab_ready
        self._tab_ready = (None, -1)
        if zoom != self._tab_zoom:
            return                            # built for a zoom nobody wants
        if ready == "error":
            self._tab_engraving = None
            self._say(f"The tab view needs the engraver — {self._tab_error}")
            return
        self._tab_engraving = ready
        if self._last_layout is not None:
            from pickhero.ui.tab_view import fit
            for page in ready.pages:
                fit(page, self._last_layout.screen_w)
        placed, written = ready.found()
        if placed < written:
            self._say(f"Engraved {placed} of {written} notes")

    def _zoom_tab(self, step: int) -> None:
        from pickhero.ui.tab_view import ZOOM_STEPS
        wanted = max(0, min(len(ZOOM_STEPS) - 1, self._tab_zoom + step))
        if wanted == self._tab_zoom:
            self._say("Already at the "
                      f"{'closest' if step > 0 else 'widest'} zoom")
            return
        self._tab_zoom = wanted
        self._tab_engraving = None
        self._tab_due = True
        self._say(f"Zoom {wanted + 1} of {len(ZOOM_STEPS)} — engraving…")

    def written_tuning(self) -> dict[int, int]:
        """The tuning the tab was WRITTEN in, whatever it is played in."""
        return {s: v - self._transpose
                for s, v in self._timeline.metadata.tuning.items()}

    def tuning_choices(self) -> list[tuple[str, int]]:
        """The tunings this song can be played in without moving a fret."""
        from pickhero.audio.note_utils import reachable_tunings
        return reachable_tunings(self.written_tuning())

    def _tuning_order(self) -> tuple[list[tuple[str, int]], int]:
        """The tunings this song steps through, lowest first, and where we
        are in them. One helper, so the line that ADVERTISES the key and the
        key itself can never name different tunings."""
        order = sorted(self.tuning_choices(), key=lambda pair: pair[1])
        here = next((i for i, (_, shift) in enumerate(order)
                     if shift == self._transpose), None)
        if here is None:
            here = next((i for i, (_, shift) in enumerate(order)
                         if shift == 0), 0)
        return order, here

    def tuning_step_label(self) -> str:
        """What R and Shift+R will do next, named rather than discovered.

        A key that walks a list nobody can see is a key you press to find out
        where it went -- and this one reloads the song and rebuilds the
        stretched recording, so finding out costs seconds. An end of the list
        says so instead of naming a tuning.
        """
        order, here = self._tuning_order()
        if len(order) < 2:
            return ""
        up = order[here + 1][0] if here + 1 < len(order) else "—"
        down = order[here - 1][0] if here > 0 else "—"
        return f"R \u2192 {up}    Shift+R \u2192 {down}"

    def _next_tuning(self, step: int):
        """Play the same shapes on a differently tuned guitar (R).

        A player thinks in tunings, not in semitones, so this steps through
        the tunings the song can actually be played in -- the ones a uniform
        shift away, which are the ones where every fret number still holds.
        A tuning of a different SHAPE cannot be reached this way at all, and
        for such a song there is nothing to step through.

        It does NOT wrap. Ordered by pitch, the two ends are five semitones
        apart, so wrapping turns one press at the top of the list into a jump
        to the bottom of it -- a whole recording rebuilt for a tuning nobody
        asked for. R means higher and Shift+R means lower, all the way, and
        the end of the list is a sentence rather than a surprise.
        """
        order, here = self._tuning_order()
        if len(order) < 2:
            self._say("This tuning cannot be swapped without moving the frets")
            return None
        wanted = here + step
        if not 0 <= wanted < len(order):
            self._say(f"Already the {'highest' if step > 0 else 'lowest'} "
                      f"tuning this song can be played in ({order[here][0]})")
            return None
        name, shift = order[wanted]
        if shift == self._transpose:
            return None
        self._say(f"Playing in {name}"
                  + (f" — the recording moves {shift:+d} semitones with you"
                     if shift else " — as written"))
        return ("transpose", shift)

    def _tab_offset_for(self, page, row: int, page_h: float,
                        view_h: float) -> int:
        """Where to scroll the page so the row being played is the TOP one.

        The rule used to be "hold still while the current row is anywhere on
        screen, then move" -- which sounds right and did the opposite of what
        it was for. With a window two rows tall it showed rows in PAIRS: the
        playhead was in the top row for one row and in the bottom row for the
        next, so half the song was played with no sight of what was coming.
        Measured on Thunder before this was changed: OBEN, unten, OBEN, unten,
        every row, all the way down the page.

        The row IS the state now. The offset follows from which row is being
        played, so it holds by itself while the playhead crosses a row and
        steps exactly one row when it leaves -- and the row after the one in
        the hand is always the one underneath it.
        """
        top, _ = page.row_window(row, TAB_SYSTEMS_SHOWN)
        limit = max(0.0, page_h - view_h)
        target = max(0.0, min(limit, top * page_h))
        return int(self._glide_to(target))

    def _glide_to(self, target: float) -> float:
        """The page sliding up to `target`, rather than arriving there.

        A step is the cheapest thing to draw and the hardest thing to
        follow: the eye has no idea whether the page went up by one row or
        three, so it has to re-find the playhead every time. A quarter of a
        second of movement carries the eye with it and costs one page turn's
        worth of motion every four seconds -- which is not the continuous
        scrolling this view exists to avoid.

        Eased at both ends (`3t^2 - 2t^3`), because a slide that starts and
        stops abruptly reads as a jump with extra steps.

        A LONG move is not a page turn and is not glided: seeking half a
        song would otherwise crawl across the page for a quarter of a second
        while the music is already somewhere else.
        """
        (self._tab_scroll, self._tab_glide_from, self._tab_glide_to,
         self._tab_glide_at) = _glide_step(
            target, self._tab_scroll, self._tab_glide_from,
            self._tab_glide_to, self._tab_glide_at, time.monotonic(),
            TAB_GLIDE_SNAP_PX)
        return self._tab_scroll

    def _slide_sheet(self, target: float, snap_px: float) -> float:
        """The same slide, for the sheet -- and its own four numbers.

        Two views glide at once (switching between them must not make the
        other jump), so the state cannot be shared. The ARITHMETIC is, which
        is the half that could disagree.

        The snap distance is passed in rather than fixed: a row of the sheet
        is whatever the head size makes it, so "a move too long to be a page
        turn" has to be measured in rows, not in pixels.
        """
        (self._sheet_scroll, self._sheet_glide_from, self._sheet_glide_to,
         self._sheet_glide_at) = _glide_step(
            target, self._sheet_scroll, self._sheet_glide_from,
            self._sheet_glide_to, self._sheet_glide_at, time.monotonic(),
            snap_px)
        return self._sheet_scroll

    # -- The hybrid view: the board's notes on a page that holds still ----

    def _sheet_strip(self) -> float:
        """How tall a row's top strip is: bar numbers, or names as well.

        One answer, because the size of the head is derived from it and the
        drawing places the names in it -- two readings of this would put the
        chord names half over the top string.
        """
        return (sheet.CHORD_STRIP if self._chord_mode and self._chord_names
                else sheet.NUMBER_STRIP)

    def _sheet_head_px(self, room: int) -> float:
        """How big a note head is drawn here, and so how much room it needs.

        One number decides both, which is the point: on the scrolling board
        the only way to part two notes was to make everything move faster,
        and here it is the head size and nothing else. Starting from the room
        there actually is, so two rows fit on any window before anybody
        touches +/-.
        """
        return max(sheet.MIN_HEAD_PX,
                   sheet.head_for_room(float(room), strip=self._sheet_strip())
                   * sheet.ZOOM_STEPS[self._sheet_zoom])

    def _sheet_layout(self, width: int, head_px: float) -> list:
        """The song as rows, laid out once per song, size and filter.

        Never per frame: this walks every bar of the song. A loop that looks
        cheap until it runs sixty times a second is the fault this display
        has had to fix three times.
        """
        key = (width, round(head_px, 1), self._filter_signature(),
               id(self._timeline))
        if key != self._sheet_key:
            notes = [n for n in self._timeline.notes
                     if self._note_passes_filter(n)]
            self._sheet_rows = sheet.lay_out(
                self._timeline, float(width), head_px,
                passes=self._note_passes_filter)
            # Where each slide, hammer-on and pull-off is GOING. Over the
            # whole song rather than the row, because the note a technique
            # points at is regularly the first one of the next row -- and
            # built here rather than per frame, because it walks every note.
            self._sheet_next = self._next_on_string(notes)
            self._sheet_key = key
        return self._sheet_rows

    def _size_sheet(self, step: int) -> None:
        """+/- in the hybrid view: bigger notes, fewer bars in sight."""
        wanted = max(0, min(len(sheet.ZOOM_STEPS) - 1,
                            self._sheet_zoom + step))
        if wanted == self._sheet_zoom:
            self._say("Sheet notes — that is as "
                      + ("big" if step > 0 else "small") + " as they go")
            return
        self._sheet_zoom = wanted
        # Laid out again at the new size, on the next frame that draws.
        self._sheet_key = ()
        self._say(f"Sheet notes {sheet.ZOOM_STEPS[wanted]:.2f}x — "
                  "bigger notes mean fewer bars on a row")

    def _draw_sheet(self, surface: pygame.Surface, layout: _Layout) -> None:
        """Two rows of music that do not move, and a playhead that does.

        The whole reason this view exists: with no hit line, a note's x owes
        nothing to the clock, so it can have the room it needs to be read at
        no cost in speed -- because there is no speed. What moves is the
        playhead, quickly through a sparse bar and slowly through a dense
        one, exactly as an engraved score has always been read.
        """
        t = get_theme()
        w, _ = surface.get_size()
        top, room = self._tab_room(layout)
        head = self._sheet_head_px(room)
        content_w = max(1, w - 2 * SHEET_SIDE_PAD)
        rows = self._sheet_layout(content_w, head)
        if not rows:
            return

        current = sheet.row_at(rows, self._playback_ms)
        strip = self._sheet_strip()
        pitch = sheet.row_height(head, strip) + sheet.ROW_GAP
        # The row being played is the TOP one, so the row after it is always
        # underneath -- the page view had to learn this the hard way, where
        # "hold while it is anywhere on screen" showed rows in pairs and half
        # the song was played with no sight of what was coming.
        scroll = self._slide_sheet(current * pitch,
                                      SHEET_SNAP_ROWS * pitch)

        was = surface.get_clip()
        surface.set_clip(pygame.Rect(0, top, w, room))
        showing = sheet.rows_that_fit(room, head, self._sheet_strip()) + 1
        for index in range(current, min(len(rows), current + showing)):
            self._draw_sheet_row(surface, rows[index], SHEET_SIDE_PAD,
                                 top + index * pitch - scroll, head,
                                 content_w, index == current, strip)
        surface.set_clip(was)

        # Nothing is written across the top of the music. What the view is
        # and what +/- does to it are in the footer, beside the keys that do
        # them; a caption over the staff is a caption you read once.
        #
        # A crowded row is the exception, because it is the one thing the
        # layout could not do and the player would otherwise blame on his
        # eyes: some heads in this bar really are touching.
        if any(rows[i].crowded
               for i in range(current, min(len(rows), current + showing))):
            warn = _get_font("arial", 16).render(
                "this bar is too dense to part at this size — press -",
                True, t.feedback_close)
            surface.blit(warn, (w // 2 - warn.get_width() // 2, int(top) - 22))

    def _draw_sheet_row(self, surface: pygame.Surface, row, x0: int,
                        y: float, head: float, content_w: int,
                        active: bool, strip: float = sheet.NUMBER_STRIP
                        ) -> None:
        """One line of music: lanes, bar lines and numbers, notes, playhead."""
        t = get_theme()
        lane_h = sheet.LANE_HEADS * head
        lanes_top = y + strip
        band_h = 6 * lane_h

        # ONE board, not six bands. Alternating lanes are what made the old
        # scrolling display read as a table of rows rather than a fretboard,
        # and drawing them here brought that fault back AND left the strings
        # out altogether -- the player saw the bands and no strings at all.
        pygame.draw.rect(surface, t.lane_bg_even,
                         (x0, int(lanes_top), content_w, int(band_h)))
        # The strings, down the middle of each lane, thicker AND warmer
        # towards the low E -- the same two cues the scrolling board uses,
        # and the reason a lane can be told apart without reading anything.
        # A wound string is a dark core with a lighter highlight on top,
        # which is what makes it read as round rather than as a thick line.
        for i, width in enumerate(sheet.string_widths(lane_h)):
            sy = int(lanes_top + (i + 0.5) * lane_h)
            tint = WOUND_TINT if i >= 6 - WOUND_STRINGS else PLAIN_TINT
            # Brighter than the scrolling board's, and deliberately so. There
            # the lanes are half as tall and full of moving notes, so a dim
            # string is enough; here a row is taller, emptier and still, and
            # the strings carry the picture between one note and the next.
            # Measured off the first screenshot of this view: the top three
            # came out at (93, 95, 99) on a (26, 23, 22) board.
            pygame.draw.line(surface, dimmed(tint, SHEET_STRING_CORE),
                             (x0, sy), (x0 + content_w, sy), width)
            lift = max(1, width // 3)
            pygame.draw.line(surface, tint, (x0, sy - lift),
                             (x0 + content_w, sy - lift), lift)
        # Edges deliberately darker than the strings: drawn in the string
        # colour they read as a seventh string and a zeroth one.
        edge = dimmed(t.lane_line, 0.45)
        for edge_y in (lanes_top, lanes_top + band_h):
            pygame.draw.line(surface, edge, (x0, int(edge_y)),
                             (x0 + content_w, int(edge_y)), 2)
        self._draw_sheet_loop(surface, row, x0, lanes_top, band_h, content_w)

        # Bar lines and their numbers. A sheet with no bar numbers is a sheet
        # you cannot talk about -- "the run in bar 34" is how a player finds
        # the passage again, and how a loop gets set.
        number_font = _get_font("arial", 13)
        for step, bar_x in enumerate(row.bar_lines):
            x = int(x0 + bar_x)
            pygame.draw.line(surface, t.lane_line, (x, int(lanes_top)),
                             (x, int(lanes_top + band_h)), 1)
            number = number_font.render(str(row.first_bar + step + 1), True,
                                        t.hud_text)
            surface.blit(number, (x + 3, int(y + 2)))
        pygame.draw.line(surface, t.lane_line, (x0 + content_w, int(lanes_top)),
                         (x0 + content_w, int(lanes_top + band_h)), 1)

        if self._chord_mode:
            self._draw_sheet_chords(surface, row, x0, y, lanes_top, lane_h)

        # Every head first, every number second -- the same two passes the
        # board needs, and for the same reason: a head drawn after its
        # neighbour's number covers it, and in a fast run that is every
        # number but the last.
        marks = []
        for placed in row.notes:
            note = placed.note
            base = (OPEN_STRING_COLOR if note.fret == 0 and not note.dead
                    else STRING_COLORS.get(note.string, (180, 180, 180)))
            colour = self._sheet_note_colour(note, base)
            cy = lanes_top + (note.string - 0.5) * lane_h
            surface.blit(
                _head_surface(max(1, int(placed.width)), max(1, int(head)),
                              colour, t.note_border),
                (int(x0 + placed.x), int(cy - head / 2)))
            marks.append((note, x0 + placed.x, cy, placed.width, base))

        # Every marking OVER every head, the same two passes the board needs
        # and for the same reason: a head drawn after its neighbour's mark
        # covers it, and in a fast run that is every mark but the last.
        radius = head / 2
        for note, x, cy, width, base in marks:
            following = self._sheet_next.get((note.timestamp_ms, note.string))
            target_x = None
            if following is not None:
                # x_at clamps to the row, so a note that belongs to the next
                # row lands on this row's right edge -- which is what a
                # technique running off the end of a line should look like.
                target_x = x0 + row.x_at(following.timestamp_ms)
            # Nothing on the sheet is dimmed, marks included: the row behind
            # the playhead is the record of the run, and a badge that fades
            # once it is played takes half that record away.
            if note.slide_to_next or note.slide_in or note.slide_out:
                self._draw_slide(surface, note, x, cy, head, width,
                                 following, target_x, base, False)
            if (note.hammer_to_next and following is not None
                    and target_x is not None):
                self._draw_legato(surface, note, x, cy, head, width,
                                  following, target_x, base, False)
            if note.bend:
                self._draw_bend(surface, note, x, cy, head, width, base, False)
            # "PM" over the note that opens a muted run, unless that note is
            # already wearing a technique badge -- two discs in one place
            # read as neither, and which pitch the note does is the more
            # urgent of the two.
            badged = bool(note.bend or note.slide_to_next or note.slide_in
                          or note.slide_out)
            if ((note.timestamp_ms, note.string) in self._palm_mute_starts
                    and not badged):
                self._draw_badge(surface, "PM", x + head,
                                 self._badge_y(cy, head, head / 2), head,
                                 base, False)

        fret_font = self._fret_font(radius, radius, self._fret_digits)
        for note, x, cy, _width, _base in marks:
            text = "X" if note.dead else str(note.fret)
            drawn = fret_font.render(text, True, t.note_text)
            if drawn.get_width() > 2 * radius:
                continue
            tx = int(x + radius) - drawn.get_width() // 2
            ty = int(cy) - drawn.get_height() // 2
            outline = fret_font.render(text, True, (0, 0, 0))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                surface.blit(outline, (tx + dx, ty + dy))
            surface.blit(drawn, (tx, ty))

        if active:
            # Its own colour and the full height of the row, the two things
            # the page view had to be told twice: a thin mark takes hunting
            # for, and one run down both rows says nothing about which row
            # the hand is on.
            x = int(x0 + row.x_at(self._playback_ms))
            pygame.draw.line(surface, t.tab_playhead, (x, int(y)),
                             (x, int(lanes_top + band_h)), TAB_PLAYHEAD_PX)

    def _sheet_note_colour(self, note, base: tuple[int, int, int]):
        """A note's colour on the sheet. Nothing here is dimmed.

        The scrolling board dims a note the moment it is done with, because
        a note behind the hit line is in the way of the ones still coming.
        A sheet has no such moment: the row behind the playhead is the
        RECORD of how the run just went, and it is the one thing this view
        offers that a scrolling one never can. "Lass sie einfach in der
        Farbe der Bewertung stehen ohne abdunkeln."

        So a judged note wears its verdict at full strength and keeps it,
        and an unjudged one stays its own string's colour whether the
        playhead has passed it or not -- the playhead already says where
        the music is, and greying half the row to say it again cost the
        colours that mean something.
        """
        if self._audio_enabled and self._matcher is not None:
            name = _TAB_VERDICT_COLOURS.get(self._matcher.get_note_state(note))
            if name is not None:
                return getattr(get_theme(), name)
        return base

    def sheet_chord_names(self, row, x0: int, width_of) -> list[tuple[int, str]]:
        """(x, label) for the chord names one row shows, left to right.

        Three rules, and each of them came from looking at the thing:

        - **Only where the chord CHANGES**, from the list built once per song
          -- the same list the scrolling board draws from, so the two views
          can never name a chord differently. The first build named every
          group, which on a song that strums sixteenths is twenty-four names
          across one row, each over the note heads.
        - **Plus the chord in force at the row's left edge.** A row whose
          chord started on the row above would otherwise sit in front of you
          for four seconds saying nothing. The scrolling board never needed
          this, because there the change itself scrolls past.
        - **Never one that would land on the one before it.** Measured on the
          player's own screenshot: three changes inside a bar came out as
          "DadA/EF#", which is worth less than one name.

        `width_of` measures a label, so the rule can be tested without a font
        and the drawing cannot use different widths from the decision.
        """
        at: dict[int, list] = {}
        for placed in row.notes:
            at.setdefault(int(round(placed.note.timestamp_ms)),
                          []).append(placed)
        moments = sorted(at)
        if not moments:
            return []
        changes = {int(round(when)): label for when, label in self._chord_names
                   if row.start_ms <= when < row.end_ms}
        if not any(when <= moments[0] for when in changes):
            carried = [label for when, label in self._chord_names
                       if when < row.start_ms]
            if carried:
                changes[moments[0]] = carried[-1]

        out: list[tuple[int, str]] = []
        written_to = 0
        for when in moments:
            label = changes.get(when)
            if label is None or len(at[when]) < 2:
                continue
            x = int(x0 + min(p.x for p in at[when]))
            if x < written_to:
                continue
            written_to = x + width_of(label) + SHEET_NAME_GAP
            out.append((x, label))
        return out

    def _draw_sheet_loop(self, surface: pygame.Surface, row, x0: int,
                         lanes_top: float, band_h: float,
                         content_w: int) -> None:
        """The looped stretch, shaded across the rows it covers.

        A loop silently repeating eight bars is the fret-filter trap in
        another costume: nothing else on the sheet would say why the
        playhead keeps going back. Drawn OVER the board and under the notes,
        so it reads as ground rather than as something played.
        """
        start, end = self._loop_start_ms, self._loop_end_ms
        if start is None and end is None:
            return
        t = get_theme()
        marker = (t.loop_marker if self._loop_enabled
                  else t.loop_marker_disabled)
        region = (t.loop_region if self._loop_enabled
                  else t.loop_region_disabled)
        if start is not None and end is not None:
            # Only the part of it that is on THIS row. x_at clamps, so a loop
            # that starts before the row shades from its left edge and one
            # that ends after it shades to the right -- which is what a
            # stretch running across a line break looks like.
            if end > row.start_ms and start < row.end_ms:
                left = int(x0 + row.x_at(start))
                right = int(x0 + row.x_at(end))
                if right > left:
                    band = pygame.Surface((right - left, int(band_h)),
                                          pygame.SRCALPHA)
                    band.fill(region)
                    surface.blit(band, (left, int(lanes_top)))
        for when in (start, end):
            if when is None or not row.holds(when):
                continue
            x = int(x0 + row.x_at(when))
            pygame.draw.line(surface, marker, (x, int(lanes_top)),
                             (x, int(lanes_top + band_h)), 3)

    def _draw_sheet_chords(self, surface: pygame.Surface, row, x0: int,
                           y: float, lanes_top: float,
                           lane_h: float) -> None:
        """The chord blocks and their names, on the sheet (Shift+C).

        The grip CARDS need nothing from this view -- they are drawn by the
        board's own method, because "which grip is the hand on" never
        depended on the scrolling. Only the block and the name have to be
        told where the notes ended up.
        """
        from pickhero.tabs.chord_shapes import shape_of
        t = get_theme()
        strip = lanes_top - y
        # As big as the staff allows, but never taller than the strip it is
        # drawn in -- the bar number lives at the top of that strip, and a
        # name sized off the lane alone sat straight on top of it.
        name_font = _get_font(
            "arial", max(14, min(int(lane_h * 0.62), int(strip) - 20)), True)

        groups: dict[int, list] = {}
        for placed in row.notes:
            groups.setdefault(int(round(placed.note.timestamp_ms)),
                              []).append(placed)
        for when in sorted(groups):
            group = groups[when]
            if len(group) < 2:
                continue
            if shape_of([p.note for p in group]) is None:
                continue
            strings = [p.note.string for p in group]
            left = min(p.x for p in group)
            right = max(p.x + p.width for p in group)
            rect = pygame.Rect(
                int(x0 + left), int(lanes_top + (min(strings) - 1) * lane_h),
                max(1, int(right - left)),
                max(1, int((max(strings) - min(strings) + 1) * lane_h)))
            surface.blit(
                _chord_block_surface(rect.width, rect.height,
                                     self._chord_block_colour(
                                         [p.note for p in group])),
                rect.topleft)

        # Sat on the floor of the strip, just above the staff, so the bar
        # number keeps the ceiling. Outlined like every other pale mark on
        # this screen: it sits over whatever happens to be behind it.
        for nx, label in self.sheet_chord_names(
                row, x0, lambda text: name_font.size(text)[0]):
            drawn = name_font.render(label, True, t.hud_text)
            shadow = name_font.render(label, True, (0, 0, 0))
            ny = int(lanes_top) - drawn.get_height() - 2
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                surface.blit(shadow, (nx + dx, ny + dy))
            surface.blit(drawn, (nx, ny))

    def _draw_tab_page(self, surface: pygame.Surface, layout: _Layout) -> None:
        """The engraved page, the playhead, and how each note went."""
        from pickhero.ui.tab_view import fit
        t = get_theme()
        font = _get_font("arial", 18)
        w, h = surface.get_size()
        if self._tab_engraving is None:
            # The percentage is not decoration: this is seconds of work with
            # nothing on screen, which is indistinguishable from a dead key.
            message = (self._tab_error and
                       f"The tab view needs the engraver — {self._tab_error}"
                       or f"Engraving the tab… {self._tab_progress * 100:.0f} %")
            text = font.render(message, True, t.hud_text)
            surface.blit(text, (w // 2 - text.get_width() // 2, h // 2))
            return

        engraving = self._tab_engraving
        spot = engraving.at_ms(self._playback_ms)
        page = engraving.pages[spot[0] if spot else 0]
        # The page is as wide as the window allows, and scrolls vertically so
        # the playhead stays in view rather than the player hunting for it.
        top_margin, room = self._tab_room(self._layout(surface))
        fitted = fit(page, w)
        page_h = fitted.get_height()
        # TWO rows of music, not as many as fit. The page filled the window,
        # and the HUD -- which is text with no ground of its own -- was then
        # printed straight over the staff, which is what the player's
        # screenshot showed. Two rows is what a reader is actually using:
        # the one being played and the one coming. Everything else was paying
        # for itself in the only currency the corners had left.
        # The ROW the playhead is in, never the note's own height. A note's y
        # on a tab staff is the string it is written on, so following that
        # moved the page up and down by the string spacing on every note of
        # an arpeggio -- a centimetre, once a second, which is what the
        # player reported.
        row = page.system_index(spot[2] if spot else 0.0)
        _, window_h = page.row_window(row, TAB_SYSTEMS_SHOWN)
        view_h = max(1, min(room, int(window_h * page_h)))
        # Centred in what is left, so the space it gives back is shared
        # between the block at the top and the lines along the bottom rather
        # than all landing in one place.
        view_top = top_margin + (room - view_h) // 2
        offset = self._tab_offset_for(page, row, page_h, view_h)
        surface.blit(fitted, (0, view_top), (0, offset, w, view_h))

        if spot is not None:
            x = int(spot[1] * fitted.get_width())
            # The height of the ROW being played, not of the whole picture.
            # Fitted to the staff alone it was a short mark that took
            # hunting for; run down both rows it said nothing about which of
            # them the hand is on, which is the one thing a page view has to
            # answer and a scrolling one never has to ask.
            #
            # Its own colour, not the hit zone's. The scrolling board is
            # dark and takes a white line; this page is PAPER, and white on
            # paper is the one thing on screen that cannot be found. Blue
            # reads on the paper and on the dark surround either side of it.
            row_top, row_h = page.row_window(row, 1)
            y0 = int(row_top * page_h) - offset + view_top
            y1 = int((row_top + row_h) * page_h) - offset + view_top
            pygame.draw.line(surface, t.tab_playhead,
                             (x, max(view_top, y0)),
                             (x, min(view_top + view_h, y1)),
                             TAB_PLAYHEAD_PX)

        # How each note went, as a dot under its fret number. The page is a
        # picture and cannot be re-coloured, so the verdict is drawn ON it.
        if self._matcher is not None and page.placed:
            # Only the notes on screen. A page holds most of a song, and
            # walking all of them every frame cost 12.4 ms against a 16.7 ms
            # budget -- the loop-over-the-whole-song fault, for the fifth
            # time. page.placed is sorted by y, so this is two bisects.
            first = bisect.bisect_left(page.placed, (offset / page_h,))
            last = bisect.bisect_right(
                page.placed, ((offset + view_h) / page_h, 2.0, 1 << 30))
            notes = self._timeline.notes
            for y, x, position in page.placed[first:last]:
                colour = _TAB_VERDICT_COLOURS.get(
                    self._matcher.get_note_state(notes[position]))
                if colour is None:
                    continue
                pygame.draw.circle(
                    surface, getattr(t, colour),
                    (int(x * fitted.get_width()),
                     int(y * page_h) - offset + view_top + 13), 4)

        # No caption over the staff. Which page of how many rides in the
        # footer's +/- entry, beside the key that turns them.
        self._tab_pages = (page.number, len(engraving.pages))

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

    def _min_head_px(self, layout: _Layout, by_hand: bool = False) -> float:
        """How narrow a head may get before the fret number stops reading.

        A two-digit fret needs roughly twice the width of a one-digit one to
        show the same size of type, and the head was squeezed to a single
        digit's worth for every song. Never wider than the lane: past that the
        head would be wider than tall for no gain, and the height is free.

        `by_hand` is the floor for a slowdown the PLAYER asked for, and it is
        the older, harder one. `MIN_FRET_DIGIT_PX` was fitted for reading a
        number that is crossing the screen at 430 px/s -- the whole point of
        the "eleven or twelve" measurement -- and applying it to a tab the
        player is deliberately slowing down asks the wrong question. Measured:
        with the fast floor, every song containing a two-digit fret could not
        be slowed AT ALL, because its head was already sitting on it. That is
        most rock songs, and it is what the player was hitting.
        """
        if by_hand:
            return min(MIN_HEAD_PX, layout.note_h)
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

        # Largest window in which every note still gets its full size.
        fit_window = window
        window = max(MIN_VISIBLE_WINDOW_MS, min(BASE_VISIBLE_WINDOW_MS, window))
        window = window / self._scroll_factor()
        window = max(MIN_VISIBLE_WINDOW_MS, window)

        # Slowing the tab down costs note size, and for a while this refused
        # to spend it: the window was clamped back to `fit_window`, so every
        # factor below 1.0 did NOTHING. Measured on three real songs, 0.4,
        # 0.6, 0.8 and 1.0 gave the identical window and the identical
        # pixels per second, with the head sitting at 44 px against a 26 px
        # floor -- room to spend that was simply never spent. Which is what
        # the player reported: "it sticks at 1 and ignores smaller".
        #
        # The rule it was protecting is real but narrower than it was read:
        # notes must not change size WHILE SCROLLING. This is decided once,
        # on a keypress, which is the same moment the automatic already
        # resizes them. And the app deciding to shrink notes is a different
        # thing from the player asking for it.
        # Only for a slowdown the player ASKED for. Without the factor test
        # this also fires when MIN_VISIBLE_WINDOW_MS lifts the window above
        # what the notes can fill -- a song so dense its notes must overlap --
        # and then recomputed the window straight back down through the floor,
        # to 167 ms. The suite caught it; the floor is not decoration.
        if (self._scroll_factor() < 1.0 and window > fit_window
                and spacing and spacing > 0):
            head = (spacing * layout.usable_width
                    / (window * (1.0 + SUSTAIN_GAP_FRACTION)))
            floor = self._min_head_px(layout, by_hand=True)
            if head < floor:
                # The end of the trade: past here a two-digit fret stops
                # reading, and an unreadable slow tab is worth nothing.
                head = floor
                window = (spacing * layout.usable_width
                          / (head * (1.0 + SUSTAIN_GAP_FRACTION)))
            window = max(MIN_VISIBLE_WINDOW_MS, window)

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
        from pickhero.tabs.chord_shapes import changes_in
        self._chord_shapes = changes_in(self._timeline)
        self._rests = self._build_rests()
        self._scroll_speed_signature = self._filter_signature()

    def _build_rests(self) -> list[tuple[float, float]]:
        """(when the rest starts, when the next note is) for every long rest.

        Measured by the END of the notes before it, not by their onset. A note
        held for eight seconds is nothing to PLAY, so counting from the onset
        would find more rests -- and skipping over one would skip a note that
        is still sounding and still being scored, which costs the player the
        note. Measured on the four songs to hand, that is the only difference
        the two definitions make: three rests, every one of them a held note.

        The outro is deliberately NOT in here. It is the biggest hole in the
        material -- 52 s on one song's lead guitar, 37 s on its rhythm track --
        and there is no next note to skip to, so it is announced (see
        `_rest_hud_text`) and never offered as a jump.
        """
        notes = sorted(self._timeline.notes, key=lambda n: n.timestamp_ms)
        rests: list[tuple[float, float]] = []
        sounding_to = 0.0
        for note in notes:
            if note.timestamp_ms - sounding_to >= GAP_MIN_MS:
                rests.append((sounding_to, note.timestamp_ms))
            sounding_to = max(sounding_to, note.end_ms)
        return rests

    def _last_note_end_ms(self) -> float:
        """When the last written note stops sounding, 0.0 for an empty track."""
        return max((n.end_ms for n in self._timeline.notes), default=0.0)

    def _rest_at(self, ms: float) -> tuple[float, float] | None:
        """The long rest the given moment sits inside, if any."""
        for start, until in self._rests:
            if start <= ms < until:
                return (start, until)
        return None

    def _next_rest_after(self, ms: float) -> tuple[float, float] | None:
        """The first long rest whose landing point is still ahead of `ms`.

        A rest already within the lead-in is not worth jumping into: the jump
        would be backwards, or a fraction of a second forwards, and either is
        worse than doing nothing.
        """
        for start, until in self._rests:
            if until - GAP_LEAD_IN_MS > ms + 1.0:
                return (start, until)
        return None

    def _skip_rest(self) -> None:
        """Jump to shortly before the next note, over a long rest.

        The whole transport moves with it -- `seek` carries the MIDI backing,
        the recording and the audio clock's anchor -- because a picture that
        jumps while the recording plays on is the sync fault this project has
        already paid for several times over.
        """
        if not self._rests:
            if self._playback_ms >= self._last_note_end_ms() > 0:
                self._say("Nothing left to play on this track")
            else:
                self._say("No long rest in this track")
            return
        rest = self._next_rest_after(self._playback_ms)
        if rest is None:
            if self._playback_ms >= self._last_note_end_ms() > 0:
                self._say("Nothing left to play on this track")
            else:
                self._say("No rest ahead to skip")
            return
        start, until = rest
        target = until - GAP_LEAD_IN_MS
        # A loop is a decision about where the song is allowed to be, and it
        # outranks this: jumping out of one would be undone by the loop itself
        # on the very next frame, which is a key that looks broken.
        if (self._loop_enabled and self._loop_end_ms is not None
                and target > self._loop_end_ms):
            self._say("Loop is on — the next rest is outside it (I/O)")
            return
        skipped = target - max(self._playback_ms, start)
        self.seek(target)
        if skipped >= 1000.0:
            self._say(f"Skipped {skipped / 1000.0:.0f} s of rest")
        else:
            self._say("Jumped to the next note")

    def _rest_hud_text(self) -> str | None:
        """What to say while the player has nothing to play, or None.

        Only while they are actually sitting in the hole. Announcing a rest
        before it arrives is noise on a line that is read at a glance, and the
        moment it is worth reading is the moment nothing is happening.
        """
        if self._playback_ms < 0 or not self._timeline.notes:
            return None
        rest = self._rest_at(self._playback_ms)
        if rest is not None:
            left = (rest[1] - self._playback_ms) / 1000.0
            if left <= GAP_LEAD_IN_MS / 1000.0:
                return None
            return f"Rest: {left:.0f} s to the next note — E skips ahead"
        last_end = self._last_note_end_ms()
        if last_end > 0 and self._playback_ms >= last_end:
            left = (self._timeline.duration_ms - self._playback_ms) / 1000.0
            if left < 1.0:
                return None
            return f"Nothing left to play — {left:.0f} s of song to run"
        return None

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

    def _chord_blocks_in_view(self, layout: _Layout):
        """(x, width, top, bottom, shape, notes) for each chord on screen.

        Grouped from the VISIBLE notes only, which is a couple of dozen, so
        this stays a per-frame cost that does not grow with the song -- the
        loop this project has had to move out of a frame three times.
        """
        view_start = self._playback_ms - LEFT_MARGIN_MS
        view_end = (self._playback_ms + self._visible_window_ms
                    + RIGHT_MARGIN_MS)
        notes = [n for n in self._timeline.get_notes_in_range(
            view_start, view_end) if self._note_passes_filter(n)]
        at: dict[int, list] = {}
        for note in notes:
            at.setdefault(int(round(note.timestamp_ms)), []).append(note)

        from pickhero.tabs.chord_shapes import shape_of

        out = []
        for when in sorted(at):
            group = at[when]
            if len(group) < 2:
                continue
            shape = shape_of(group)
            if shape is None:
                continue
            x = self.note_x(float(when), self._playback_ms,
                            layout.hit_zone_x, layout.pixels_per_ms)
            head = self._head_px if self._head_px is not None else layout.note_h
            width = max(2 * (head / 2), min(
                self.sustain_width(max(n.duration_ms for n in group),
                                   layout.pixels_per_ms),
                head * 4))
            if x + width < 0 or x > layout.screen_w:
                continue
            strings = [n.string for n in group]
            top = layout.lane_top + (min(strings) - 1) * layout.lane_height
            bottom = layout.lane_top + max(strings) * layout.lane_height
            out.append((x, width, top, bottom, shape, group))
        return out

    def _chord_block_colour(self, notes) -> tuple[int, int, int]:
        """What a whole chord's worth of verdicts looks like as one colour.

        The block cannot show six answers, so it shows the WORST of them --
        a chord with one string wrong is not a chord that went well. The
        per-string detail is not lost: the heads are drawn on top of this and
        keep their own colours, which is the whole reason the block goes
        underneath rather than instead.
        """
        t = get_theme()
        if not self._audio_enabled or self._matcher is None:
            return t.lane_line
        states = [self._matcher.get_note_state(n) for n in notes]
        if any(s is MatchType.PENDING for s in states):
            return t.lane_line
        if any(s is MatchType.MISS for s in states):
            return t.feedback_miss
        if any(s is MatchType.CLOSE for s in states):
            return t.feedback_close
        return t.feedback_hit

    def _draw_chord_blocks(self, surface: pygame.Surface,
                           layout: _Layout) -> None:
        """Draw each chord as ONE object with its name on it (Shift+C).

        Six fret numbers spread down six lanes are not a shape. A block that
        spans the strings says "this is one grip" before a single number has
        been read, which is what the reference app's big coloured slabs do.

        It is a tint, not a fill: the notes are the thing being read and a
        solid slab under them would fight them for attention. The name sits
        at the block's LEADING edge, because that is the moment the hand has
        to be ready -- the same reason a note's leading edge is its time.
        """
        if not self._chord_mode:
            return
        blocks = self._chord_blocks_in_view(layout)
        if not blocks:
            return
        t = get_theme()
        font = _get_font("arial", 22)
        for x, width, top, bottom, shape, notes in blocks:
            colour = self._chord_block_colour(notes)
            rect = pygame.Rect(int(x), int(top), max(1, int(width)),
                               max(1, int(bottom - top)))
            surface.blit(_chord_block_surface(rect.width, rect.height, colour),
                         rect.topleft)
            label = font.render(shape.name, True, t.note_text)
            shadow = font.render(shape.name, True, (0, 0, 0))
            # A white name over an amber string is invisible, which is the
            # same reason every technique line here carries a shadow.
            lx, ly = rect.left + 6, rect.top - label.get_height() - 2
            if ly < layout.lane_top:
                ly = rect.top + 4
            surface.blit(shadow, (lx + 1, ly + 1))
            surface.blit(label, (lx, ly))

    def _chord_now_and_next(self):
        """The grip being played and the one after it.

        The grip being PLAYED, not the nearest one: a chord is held until the
        next one starts, so the card must stay up for as long as the hand is
        on it. Bisect rather than a scan -- this runs sixty times a second
        over a list that grows with the song, which is exactly the loop this
        project has had to move out of a frame three times already.
        """
        shapes = self._chord_shapes
        if not shapes:
            return None, None
        i = bisect.bisect_right([w for w, _ in shapes], self._playback_ms) - 1
        now = shapes[i][1] if i >= 0 else None
        nxt = shapes[i + 1][1] if i + 1 < len(shapes) else None
        # Before the first chord there is nothing being played, so the first
        # one is what is COMING -- which is the more useful of the two while
        # the count-in runs.
        if now is None:
            return None, shapes[0][1]
        return now, nxt

    def _hud_left_x(self) -> int:
        """Where the top-left text column starts.

        Past the chord cards when they are up: they occupy the same corner,
        and the text is the half that can move. Drawn over each other neither
        can be read, which is what the player's screenshot showed.
        """
        if not self._chord_mode or not self._chord_shapes:
            return 12
        from pickhero.ui.chord_view import card_size
        return 12 + 2 * (card_size(CHORD_CARD_SCALE)[0] + CHORD_CARD_GAP) + 8

    def _draw_chord_cards(self, surface: pygame.Surface,
                          layout: _Layout) -> None:
        """The grip now and the grip next, top left (Shift+C).

        Silent on a song with no chords in it -- a panel that is always there
        and usually empty is a panel nobody looks at.
        """
        if not self._chord_mode or not self._chord_shapes:
            return
        from pickhero.ui.chord_view import card_size, draw_diagram

        now, nxt = self._chord_now_and_next()
        if now is None and nxt is None:
            return
        # Both the SAME size. The second card was smaller to say "this one
        # is next", and the label already says that -- what the smaller one
        # actually did was make the grip you have to prepare the harder of
        # the two to read.
        w, h = card_size(CHORD_CARD_SCALE)
        x, y = 12, 6
        if now is not None:
            draw_diagram(surface, pygame.Rect(x, y, w, h), now, label="now")
        x += w + CHORD_CARD_GAP
        if nxt is not None:
            draw_diagram(surface, pygame.Rect(x, y, w, h), nxt, label="next",
                         dim=True)

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
        """Speed the tab up or down by hand (+ / -), and remember it.

        A press that changes nothing is put back rather than stored. The
        factor used to walk all the way to 0.4 while the picture stood still,
        so the number on screen described a setting the display was not
        honouring -- and the key looked broken because it was.
        """
        lo, hi = SCROLL_FACTOR_RANGE
        current = self._scroll_factor()
        wanted = max(lo, min(hi, round(current + delta, 2)))
        before_window = self._visible_window_ms
        if wanted == current:
            self._say(f"Already at the {'slowest' if delta < 0 else 'fastest'}"
                      f" setting ({current:.1f}x)")
            return
        self._config.scroll_speed_factor = wanted
        self._recompute_scroll_speed()
        gained = abs(self._visible_window_ms - before_window)
        if gained < max(1.0, before_window * SCROLL_FACTOR_MIN_GAIN):
            self._config.scroll_speed_factor = current
            self._recompute_scroll_speed()
            if delta < 0:
                self._say("The notes are already as small as they may get — "
                          "this song cannot scroll slower and stay readable")
            else:
                self._say("The notes are already as big as the lane allows")
            return
        self._config.save()
        self._say(f"Tab speed {wanted:.1f}x — {self._head_px:.0f} px notes, "
                  f"{self._visible_window_ms / 1000:.1f} s ahead")

    def _filter_signature(self) -> tuple:
        """What the scroll speed depends on, so it is only redone when needed."""
        return (self._max_fret, tuple(self._active_strings))

    @staticmethod
    def _neighbour_gaps(notes: list[NoteEvent]) -> dict[tuple[float, int], float]:
        """Time to the NEXT note on the same string, in ms.

        This is what limits how long a note may be drawn: notes only collide
        within their own lane, and a note can only ever run into the one that
        follows it. It used to take the smaller of the gaps before and after,
        which is a note being shortened by something that had already
        finished -- and it is why a note held across two beats was still drawn
        for one. Measured on the player's own tab: a tied note of 1562 ms,
        490 px of sustain, cut to 98 px by an eighth note that came BEFORE it.
        Every long note following a quick one on its string was drawn short.
        """
        by_string: dict[int, list[float]] = {}
        for note in notes:
            by_string.setdefault(note.string, []).append(note.timestamp_ms)

        gaps: dict[tuple[float, int], float] = {}
        for string, stamps in by_string.items():
            unique = sorted(set(stamps))
            for first, following in zip(unique, unique[1:]):
                gaps[(first, string)] = following - first
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

        # Every head first, every marking second. A fast run puts the next
        # onset closer than a head is wide, and one loop drawing head-then-
        # number per note let the following head land on top of the number
        # that was already there -- so in exactly the passage the player has
        # to read fastest, every fret number but the last was half covered.
        # Two passes cost one list and change no geometry at all.
        marks: list[tuple] = []

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
            if note.let_ring:
                # "let ring" does not lengthen the written value -- a
                # let-ring eighth is still an eighth -- it says the string is
                # never damped, so the note sounds until something else is
                # played on it. That is exactly the neighbour gap, which is
                # already the cap for every other note.
                #
                # And if nothing ever is, it rings to the END OF THE SONG,
                # which is a length. The gap is None for the last note on a
                # string, and taking that as the cap made the body infinite:
                # every song whose last note on any string is let-ring
                # crashed the frame on `int(inf)`.
                body = gap_px if gap_ms is not None else max(
                    0.0, (self._timeline.duration_ms - note.timestamp_ms)
                    * layout.pixels_per_ms)
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

            # One shape for every note: a rounded rectangle, as round as its
            # HEIGHT allows. A short note is then a circle and a sustained
            # one a capsule, and the curvature is identical -- which is the
            # point. The note's LEADING EDGE is at its own time, so the
            # moment to play is when the start of the shape reaches the hit
            # line, not its middle, which put the cue half a note late.
            #
            # The corner used to be `min(radius, half_h)` -- half the head's
            # WIDTH. Since the head is squeezed sideways to buy look-ahead
            # while keeping the lane's height, that made every wide note less
            # rounded than the short ones beside it, and a long note read as
            # a box. The curvature is the height's business and nothing
            # else's; pygame clamps it to half the shorter side by itself, so
            # a head narrower than it is tall stays a capsule rather than
            # growing corners.
            draw_w = max(1, int(max(capsule_w, 2 * radius)))
            draw_h = max(1, int(2 * half_h))
            surface.blit(_head_surface(draw_w, draw_h, color, t.note_border),
                         (int(x), int(cy - half_h)))
            marks.append((note, x, cy, head, radius, half_h, capsule_w,
                          base_color, past_hit_zone))

        # Technique marks and the fret number go OVER every head, not only
        # over their own. They live inside the note rather than arcing out of
        # the lane, so drawing them underneath would hide them behind the
        # note they describe -- and drawing them before the NEXT note's head
        # hid them just as surely.
        for (note, x, cy, head, radius, half_h, capsule_w,
             base_color, past_hit_zone) in marks:
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

    # -- What the HUD says, as data, so it can be tested without a screen --

    def tuning_segments(self) -> list[tuple[str, str]]:
        """The tunings worth offering, as (notes, role) low to high.

        Role is "played", "written", "both" or "other", and the drawing turns
        that into a colour and a star. One line, six letters a tuning, so the
        whole question "what is my guitar in, what was this written in, and
        what else can I play it as" is answered by looking rather than by
        pressing R to find out -- which reloads the song and costs seconds.

        The window is bounded by what a guitarist would actually do:

        - **At most one step DOWN**, because further down is a floppy string
          and a fret that buzzes, not a choice.
        - **Up to three steps UP**, ending at the standard-shaped tuning
          (EADGBE, or DADGBE for a song written in a drop shape) -- the top
          of the reachable list is that tuning by construction.
        - **Never more than five**, and the two that must survive the trim
          are the one being PLAYED and the one it was WRITTEN in. Anything
          else is a suggestion; those two are facts.
        """
        order, here = self._tuning_order()
        if not order:
            return []
        written = next((i for i, (_, shift) in enumerate(order) if shift == 0),
                       here)
        low, high = min(here, written), max(here, written)
        start = max(0, low - TUNINGS_BELOW)
        end = min(len(order) - 1, max(here + TUNINGS_ABOVE, high))
        while end - start + 1 > TUNINGS_SHOWN:
            # Trim the suggestions first, from whichever end has one to
            # spare; only if both facts cannot fit does anything real go.
            if start < low:
                start += 1
            elif end > high:
                end -= 1
            elif written > here:
                end -= 1
            else:
                start += 1

        base = self.written_tuning()
        out = []
        for i in range(start, end + 1):
            shift = order[i][1]
            notes = tuning_notes({s: v + shift for s, v in base.items()})
            if i == here and i == written:
                role = "both"
            elif i == here:
                role = "played"
            elif i == written:
                role = "written"
            else:
                role = "other"
            out.append(("".join(notes), role))
        return out

    def _latency_line(self) -> tuple[str, str] | None:
        """The strike-timing offset (K), as (text, theme colour name).

        Shown even at zero with nothing measured yet: it used to vanish in
        exactly that state, which is the state Shift+K produces -- so the one
        key whose whole job is to put the offset back to zero looked like it
        had done nothing at all.
        """
        offset = self._config.audio_latency_offset_ms
        if self._audio_enabled and self._matcher is not None:
            err = self._matcher.median_timing_error_ms()
            if err is not None:
                # Spread separates the two timing problems: a big error with
                # a small spread is latency and K fixes it; a big spread
                # means the strikes disagree with each other and no offset
                # can help.
                spread = self._matcher.timing_spread_ms()
                spread_text = f"  ±{int(spread):d} ms" if spread is not None else ""
                return (f"Sync: {int(offset):+d} ms  |  strikes "
                        f"{int(abs(err)):d} ms {'late' if err > 0 else 'early'}"
                        f"{spread_text} {self._sync_advice()}",
                        "hud_accent" if abs(err) > 20 else "hud_text")
        return (f"Sync: {int(offset):+d} ms  {self._sync_advice()}", "hud_text")

    def sync_block_lines(self) -> list[tuple[str, str]]:
        """Everything about lining sound up against the notes, in one place.

        Six lines of it were spread down the left column and across the
        bottom, permanently, on a screen whose whole point is reading music.
        None of it is needed while playing and all of it is needed while
        syncing, which is what a panel behind a key is for.
        """
        out: list[tuple[str, str]] = []
        backing = self._backing_offset()
        if abs(backing) > 0.5:
            out.append((f"Backing: {int(backing):+d} ms (N/M)", "hud_accent"))
        mp3 = self._mp3_hud_text()
        if mp3:
            out.append((mp3, "hud_accent"))
        tail = self._silent_tail()
        if tail is not None:
            # The number the player keeps comparing against YouTube. A tab
            # padded out to the end of the sheet runs minutes past its last
            # note, and the clock says so with no explanation -- "Die
            # Songdauer passt noch immer nicht zusammen. Hab ich noch immer
            # das falsche GP?"
            last, bars = tail
            out.append((f"SYNC   this tab runs to "
                        f"{format_time(self._timeline.duration_ms)} but its "
                        f"last note is at {format_time(last)} — {bars} empty "
                        f"bars at the end", "hud_text"))
        out.append((f"SYNC   source: {SYNC_SOURCE_WORDS[self._sync_source()]}"
                    + (f"   |   Songsterr {self._songsterr_id()} stored"
                       if self._songsterr_id() else
                       "   |   no Songsterr link (Ctrl+U pastes one)")
                    + "   |   Alt+S changes it", "hud_accent"))
        latency = self._latency_line()
        if latency:
            out.append(latency)
        if self._auto_sync_line():
            out.append((self._auto_sync_line(), "hud_accent"))
        else:
            out += [(line, "hud_accent") for line in self._sync_lines]
            beyond = self._beyond_sync_line()
            if beyond:
                out.append((beyond, "feedback_close"))
        return out

    def _toggle_sync_block(self) -> None:
        """S: show what is going on with the sound, or put it away again."""
        self._show_sync = not self._show_sync
        self._say("Sync panel open (S)" if self._show_sync
                  else "Sync panel closed (S)")

    def _left_notes(self) -> list[tuple[str, str]]:
        """The short lines under the tuning: only what is NOT the default.

        Every one of these is a setting that changes how the song scores and
        that nothing else on screen would mention -- the fret filter left on
        by accident cost this project a session. A permanent line saying
        everything is normal is a line nobody reads.
        """
        out: list[tuple[str, str]] = []
        filter_text = self._filter_hud_text()
        if filter_text:
            out.append((filter_text, "hud_accent"))
        if self._chord_partial_credit != self._config._default_chord_partial_credit:
            out.append(("Chords: strict" if self._chord_partial_credit
                        else "Chords: easy", "hud_accent"))
        if not getattr(self._config, "chord_verify", True):
            out.append(("Strings: off (J)", "hud_accent"))
        # Loud when it happens, absent otherwise. A machine that loses
        # buffers loses notes at random, which looks exactly like bad
        # detection or bad playing and is neither.
        if (self._audio_enabled and self._audio_capture is not None
                and getattr(self._audio_capture, "dropped_buffers", 0)):
            out.append((f"Audio dropouts: {self._audio_capture.dropped_buffers}"
                        "  — close other programs", "feedback_miss"))
        return out

    def footer_segments(self) -> list[tuple[str, str]]:
        """The one line of keys that stays on screen, as (text, colour name).

        Everything else moved into H. Twenty-three shortcuts along the bottom
        of a screen you are trying to read music off are not a help system,
        they are wallpaper -- and the ones that matter while playing are the
        ones whose STATE you need to see, which is why each of these carries
        its value and lights up when it is not at rest.
        """
        meta = self._timeline.metadata
        pct = int(self._tempo_factor * 100)
        on = "hud_accent"
        off = "hud_text"

        # What the size BOUGHT, not just what it is set to. On the sheet that
        # is bars on a row, on the page which page of how many, on the board
        # the seconds of song in sight -- the number actually being traded,
        # in the entry for the key that trades it. All three used to be
        # captions across the top of the music, which is read once.
        if self._view == "hybrid":
            # Bars-per-row is NOT written here, tempting as it is. It comes
            # out of the layout, the layout is sized from the room left after
            # this footer, and a longer footer can need a second line -- so
            # the number would change the room it was measured in. Caught by
            # the test that says the sheet is laid out once: it was laid out
            # twice, at 44.4 px and then 44.5. The bar numbers are on the
            # screen anyway, which is where a player would count them.
            size = f"{sheet.ZOOM_STEPS[self._sheet_zoom]:.2f}x"
            sized = self._sheet_zoom != sheet.ZOOM_DEFAULT
        elif self._view == "tab":
            size = f"zoom {self._tab_zoom + 1}"
            if self._tab_pages[1]:
                size += f", page {self._tab_pages[0]}/{self._tab_pages[1]}"
            sized = self._tab_zoom != 2
        else:
            size = (f"{self._scroll_factor():.1f}x, "
                    f"{self._visible_window_ms / 1000.0:.1f} s ahead")
            sized = abs(self._scroll_factor() - 1.0) > 0.01

        def state(player, muted) -> str:
            if player is None:
                return "—"
            return "off" if muted else "on"

        backing = state(self._midi_player, self._backing_muted)
        guide = state(self._guide_player, self._guide_muted)
        # A dash where there is no file, the same as the other two: it is the
        # answer to "why does pressing it do nothing", and U looking removed
        # is a thing this player has already reported once.
        mp3 = ("U: MP3 —" if self._mp3_player is None
               else f"U: MP3 {'off' if self._mp3_muted else 'on'}")
        window = int(self._config.timing_window_ms)
        # The sync panel is shut, so anything urgent inside it has to reach
        # the outside somehow. One coloured word is the whole signal.
        sync_colour = ("feedback_close" if self._beyond_sync_line()
                       else on if self._show_sync else off)
        return [
            ("SPACE: play/pause", on if self._playing else off),
            (f"PgDn/PgUp: Tempo {meta.tempo} BPM ({pct}%)",
             on if pct != 100 else off),
            (f"A: Audio {'on' if self._audio_enabled else 'off'}",
             on if self._audio_enabled else off),
            (f"B: Backing {backing}", on if backing == "on" else off),
            (f"Shift+B: My Backing {guide}", on if guide == "on" else off),
            (mp3, on if mp3.endswith("on") else off),
            (f"+/- Size ({size})", on if sized else off),
            (f"G: {window} ms",
             on if window != int(config_module.Config().timing_window_ms)
             else off),
            (f"Shift+C: Chords {'on' if self._chord_mode else 'off'}",
             on if self._chord_mode else off),
            (f"Shift+T: View {VIEW_SHORT[self._view]}", off),
            ("E: Skip", on if self._rest_hud_text() else off),
            ("S: Sync", sync_colour),
            ("H: help", on if self._show_help else off),
        ]

    @staticmethod
    def _fit_line(font, text: str, width: int, colour) -> pygame.Surface:
        """Render a line, shrinking the middle away until it fits.

        Seventeen sync points do not fit across a window, and a line drawn
        wider than the screen is centred so BOTH ends are cut -- the first
        point and the last, which are the two that matter most.
        """
        surface = font.render(text, True, colour)
        if surface.get_width() <= width or len(text) < 8:
            return surface
        keep = len(text)
        while keep > 8:
            keep -= max(1, keep // 20)
            half = keep // 2
            shortened = text[:half] + " … " + text[-(keep - half):]
            surface = font.render(shortened, True, colour)
            if surface.get_width() <= width:
                break
        return surface

    def _footer_block(self, layout: _Layout, segments=None):
        """The footer's lines, its spacing, and where the block starts.

        Measured rather than assumed, and shared, because two callers need
        the same answer at different moments: the footer draws it, and the
        sheet and page views have to know how much room is left BEFORE they
        lay a row out. A constant was tried and the page ran into the keys.

        Segments rather than a string, because each entry carries its own
        colour: an entry lights up when the thing it names is not at rest,
        and that is the whole reason the line is worth the space it takes.
        """
        segments = (self.footer_segments() if segments is None else segments)
        w = layout.screen_w
        for size in self.FOOTER_FONT_SIZES:
            font = _get_font("arial", size)
            rows = _wrap_segments(font, segments, w - 16)
            if len(rows) == 1:
                break
        line_h = font.get_height() + 2
        return font, rows, line_h, layout.screen_h - 4 - line_h * len(rows)

    def _blit_footer_lines(self, surface: pygame.Surface,
                           layout: _Layout) -> int:
        """Centre the footer, shrinking and WRAPPING until it fits.

        Shrinking alone was never enough: a line drawn wider than the screen
        is centred, which cuts BOTH ends -- measured on the player's own
        screenshot, the first entry and the last were simply not there, and
        a shortcut nobody can see is a shortcut nobody has.

        Returns the y the block STARTS at, because whatever stacks above it
        has to know where it ends.
        """
        t = get_theme()
        font, rows, line_h, top = self._footer_block(layout)
        y = top
        for row in rows:
            width = _segments_width(font, row)
            x = layout.screen_w // 2 - width // 2
            for text, colour in row:
                drawn = font.render(text, True, getattr(t, colour))
                surface.blit(drawn, (x, y))
                x += drawn.get_width()
            y += line_h
        return top

    def _tab_room(self, layout: _Layout) -> tuple[int, int]:
        """(top, height) of the space the page may use, clear of the text.

        The HUD is text with no ground of its own, so anything drawn under it
        is simply lost -- which is what the player's screenshot showed. The
        block above is a known number of lines; the one below is the footer
        plus whatever sync lines are standing, and both are measured here
        rather than guessed at.
        """
        _, _, _, footer_top = self._footer_block(layout)
        # The sync panel is NOT counted, deliberately. Counting it made the
        # music RE-FLOW when S was pressed: less room, a smaller head, more
        # bars on a row, different line breaks -- the page turned into a
        # different page while you were looking at it. It is an overlay
        # instead, drawn over the bottom of the sheet, which is the part you
        # are not reading while you are lining a recording up.
        #
        # What IS counted is the one-line status note above it, and a gap.
        bottom = footer_top - 18 - 12
        top = self._hud_top_used() + TAB_TOP_GAP
        return top, max(1, bottom - top)

    def _hud_top_used(self) -> int:
        """How far down the text at the top of the screen reaches.

        Measured, not a constant. It WAS a constant, fitted to a HUD with
        eight lines down the left and a caption across the middle; with
        three lines left and nothing in the middle it left 90 px of empty
        window above the music and took it off the bottom of the sheet. The
        block is now between two and six lines depending on what is not at
        its default, which is exactly what a constant cannot follow.
        """
        # Title, then the small lines: track, tunings, and whatever is off
        # its default. The right-hand column is shorter than this whenever
        # the left one is drawn at all.
        lines = 1 if self._timeline.metadata.track_name else 0
        lines += 1 if self.tuning_segments() else 0
        lines += len(self._left_notes())
        used = 38 + 16 * lines
        # The grip cards are in this corner too, and they are 160 px tall
        # against three lines of text. On the scrolling board they sat above
        # a lane band that starts halfway down the window; here the music
        # reaches into the corner, and the first thing the player saw was a
        # diagram over his top string.
        if self._chord_mode and self._chord_shapes:
            from pickhero.ui.chord_view import card_size
            used = max(used, 6 + card_size(CHORD_CARD_SCALE)[1])
        return used

    def _draw_hud(self, surface: pygame.Surface, layout: _Layout) -> None:
        """Everything around the music.

        Rebuilt to a rule the player wrote after living with the old one: a
        line earns its place by saying something that CHANGES and that
        nothing else on screen says. Six lines of sync arithmetic, a hit
        window, a scroll speed and a frame-pacing note were permanently on a
        screen whose whole job is to be read while both hands are busy. They
        are behind S, behind H, or gone.
        """
        t = get_theme()
        title_font = _get_font("arial", 20)
        big_font = _get_font("consolas", 20)
        hint_font = _get_font("arial", 14)

        meta = self._timeline.metadata
        w, h = layout.screen_w, layout.screen_h

        # Count-in overlay — large centred beat countdown.
        if self._playback_ms < 0 and self._count_in_ms > 0:
            beats = int(-self._playback_ms / self._ms_per_beat) + 1
            beats = min(beats, self._config.count_in_beats)
            countdown = _get_font("arial", 120).render(
                str(beats), True, t.hud_accent)
            surface.blit(countdown, (w // 2 - countdown.get_width() // 2,
                                     h // 2 - countdown.get_height() // 2))

        if self._song_completed:
            self._draw_completion_overlay(surface, layout)

        # -- Top left: what this is, and what the guitar has to be in ------
        # The column starts wherever the chord cards end: both want this
        # corner and the text is the half that can move.
        left = self._hud_left_x()
        title = meta.title or "Untitled"
        if meta.artist:
            title = f"{meta.artist} — {title}"
        surface.blit(title_font.render(title, True, t.hud_text), (left, 12))
        y = 38
        if meta.track_name:
            surface.blit(hint_font.render(f"Track: {meta.track_name}", True,
                                          t.hud_text), (left, y))
            y += 16
        y = self._blit_tuning_strip(surface, hint_font, left, y)
        for text, colour in self._left_notes():
            surface.blit(hint_font.render(text, True, getattr(t, colour)),
                         (left, y))
            y += 16

        # -- Top centre: nothing, unless something is not normal -----------
        # The tempo moved into the footer, where the key that changes it is.
        # A loop, though, silently repeats a section of the song and no other
        # line would mention it -- the fret-filter trap in another costume.
        loop_info = self._loop_hud_text()
        if loop_info and self._loop_enabled:
            loop_surf = hint_font.render(loop_info, True, t.hud_accent)
            surface.blit(loop_surf, (w // 2 - loop_surf.get_width() // 2, 12))

        # -- Top right: the two numbers you glance at while playing --------
        # Stacked on the MEASURED height of the line above, never on a fixed
        # offset: the block used to draw two lines where one was counted for,
        # and the noise gate landed on top of the hit count.
        time_text = (f"{format_time(self._playback_ms)} / "
                     f"{format_time(self._timeline.duration_ms)}")
        time_surf = big_font.render(time_text, True, t.hud_text)
        surface.blit(time_surf, (w - time_surf.get_width() - 12, 12))
        right_y = 12 + time_surf.get_height() + 2

        # As big as the clock, and for the same reason: it is the other
        # number worth reading from across the room. The H/C/M breakdown it
        # used to carry went with the rest of the arithmetic -- the
        # percentage is the answer, and Y gives the whole report.
        if self._audio_enabled and self._matcher is not None:
            stats = self._matcher.get_statistics()
            if stats["total"] > 0:
                accuracy = stats["accuracy_percent"]
                acc_surf = big_font.render(
                    f"{accuracy:.0f}%", True,
                    t.feedback_hit if accuracy >= 80 else t.hud_text)
                surface.blit(acc_surf, (w - acc_surf.get_width() - 12, right_y))
                right_y += acc_surf.get_height() + 2
        if self._audio_enabled and self._feedback.streak >= 3:
            # Beside the other number that says how it is going, rather than
            # across the top of the music where nothing else is now. Drawn
            # here rather than through draw_streak because that one centres
            # on the x it is given and this column is right-aligned.
            streak = hint_font.render(f"{self._feedback.streak}x streak",
                                      True, t.feedback_streak)
            surface.blit(streak, (w - streak.get_width() - 12, right_y))
            right_y += streak.get_height() + 4

        line_h = hint_font.get_height() + 4
        if self._audio_enabled:
            gate = hint_font.render(
                f"Gate: {int(self._noise_gate_db)} dB"
                + (" (auto)" if self._auto_gate else " (X/C)"),
                True, t.hud_accent)
            surface.blit(gate, (w - gate.get_width() - 12, right_y))
            right_y += line_h
        if self._audio_capture is not None:
            self._draw_signal_meter(surface, hint_font, w, right_y)
            right_y += line_h
            self._draw_tuner(surface, hint_font, w, right_y)
            right_y += line_h
            # What to DO about the level, when there is something to do.
            # Silent otherwise: a permanent "everything is fine" is a line
            # nobody reads, and the one time it changes nobody notices.
            advice = self._level_advice()
            if advice:
                drawn = hint_font.render(advice, True, t.feedback_close)
                surface.blit(drawn, (w - drawn.get_width() - 12, right_y))

        # -- Bottom: the keys, then the sync panel, then the status note ---
        # Drawn in that order because each stacks on the one below it. The
        # sync panel used to start at a fixed height and grow downward into
        # the keys, which is exactly what the player saw overlapping.
        footer_top = self._blit_footer_lines(surface, layout)
        note_y = self._blit_sync_block(surface, layout, hint_font, footer_top)

        note = self._status_note_text()
        if note:
            drawn = hint_font.render(note, True, t.hud_accent)
            surface.blit(drawn, (w // 2 - drawn.get_width() // 2, note_y))
        if self._mp3_dialog_due:
            # Drawn this frame, so the next update may block on the chooser.
            self._mp3_dialog_armed = True

    def _blit_tuning_strip(self, surface: pygame.Surface, font, left: int,
                           y: int) -> int:
        """The tunings on one line: played in blue, written with a star.

        A tab in Drop C played on a standard-tuned guitar is wrong on every
        single note and nothing else on screen says so -- the notes scroll by
        looking perfectly ordinary while every one of them scores red. The
        old line said it in words and took two rows to do it; this says it in
        six letters a tuning and answers "what else could I play this as" in
        the same glance, which used to cost a press of R and a reload.
        """
        t = get_theme()
        segments = self.tuning_segments()
        if not segments:
            return y
        x = left
        label = font.render("Tuning:  ", True, t.hud_text)
        surface.blit(label, (x, y))
        x += label.get_width()
        for notes, role in segments:
            text = notes + ("*" if role in ("written", "both") else "")
            colour = (t.hud_accent if role in ("played", "both")
                      else t.hud_text)
            drawn = font.render(text + "   ", True, colour)
            surface.blit(drawn, (x, y))
            x += drawn.get_width()
        return y + 16

    def _blit_sync_block(self, surface: pygame.Surface, layout: _Layout,
                         font, footer_top: int) -> int:
        """The sync panel above the footer (S), and where the room above it
        starts. Closed, it costs nothing and takes no room."""
        if not self._show_sync:
            return footer_top - 6 - 18
        t = get_theme()
        lines = self.sync_block_lines()
        w = layout.screen_w
        y = footer_top - 6 - 18 * len(lines)
        top = y
        # A ground of its own, because it is drawn OVER the music rather than
        # in room taken from it -- see _tab_room. Text with no ground on top
        # of a staff is the fault this file has already written up twice.
        panel = pygame.Surface((w, 18 * len(lines) + 8), pygame.SRCALPHA)
        panel.fill((*t.bg, 232))
        surface.blit(panel, (0, top - 4))
        for text, colour in lines:
            drawn = self._fit_line(font, text, w - 16, getattr(t, colour))
            surface.blit(drawn, (w // 2 - drawn.get_width() // 2, y))
            y += 18
        return top - 18

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
        """Draw the song completion results overlay.

        Unconditional on purpose -- every caller decides whether the song is
        over. Forgetting that check is what made the page view show nothing
        but the score, so the callers are the thing to look at when this
        appears somewhere it should not.
        """
        t = get_theme()
        w, h = layout.screen_w, layout.screen_h

        # Semi-transparent dark overlay
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        surface.blit(overlay, (0, 0))

        header_font = _get_font("arial", 48)
        stat_font = _get_font("consolas", 28)
        hint_font = _get_font("arial", 18)

        # Every line is STACKED on the measured height of the one above it,
        # never placed at an offset of its own. Fixed offsets are how "New
        # Best!" at +132 in the big font came to be drawn through the weakest
        # section at +140 in the small one -- the two were laid out on the
        # assumption that the other was absent. Same rule as the footer and
        # the sync panel: the block grows, it does not collide.
        lines: list[tuple[pygame.Surface, int]] = []

        def say(surface_line, gap: int = 4) -> None:
            lines.append((surface_line, gap))

        say(header_font.render("Song Complete!", True, t.hud_accent), 14)

        if self._audio_enabled and self._matcher is not None:
            # Accuracy stats
            stats = self._matcher.get_statistics()
            accuracy_text = (
                f"Accuracy: {stats['accuracy_percent']:.1f}%  "
                f"({stats['hits']}/{stats['total']})"
            )
            say(stat_font.render(accuracy_text, True, t.hud_text), 10)

            # How many strikes were HEARD at all, next to how many scored.
            # Without it a low percentage says only that something is wrong;
            # with it, it says which thing. Far fewer strikes than notes is
            # the microphone path; as many strikes as notes and a low score
            # is the matching, and they are fixed in different places.
            say(hint_font.render(self._heard_line(), True, t.hud_text))

            # And what the SCORE rests on. One strike credits a whole chord,
            # so a number that mixes "heard" with "credited to the strum"
            # cannot answer "was I really that good".
            credit = self._credit_line()
            if credit:
                say(hint_font.render(credit, True, t.hud_text))

            if self._is_new_best:
                say(stat_font.render("New Best!", True, (255, 220, 50)), 10)

            weak = getattr(self, "_weakest_sections", [])
            if weak:
                section = weak[0]
                weak_text = (
                    f"Weakest: bars {section[0]+1}-{section[1]+1} "
                    f"({section[2]:.0f}%) -- press L to loop"
                )
                say(hint_font.render(weak_text, True, t.feedback_close), 10)

            for rec in self._recommendations:
                say(hint_font.render(rec, True, t.hud_accent), 6)

            say(hint_font.render(
                "SPACE to replay  |  L to loop weak section  |  ESC to menu",
                True, t.hud_text), 8)

            # Where the run log went. A file written silently is a file
            # nobody sends, and this one is the whole point of writing it.
            if self._run_log_note:
                say(hint_font.render(self._run_log_note, True, t.hud_text))
        else:
            say(hint_font.render("SPACE to replay  |  ESC to menu",
                                 True, t.hud_text), 14)

        # Centred on the screen as a BLOCK, so a run with recommendations and
        # one without are both readable rather than one of them hanging off
        # the bottom edge.
        total = sum(line.get_height() + gap for line, gap in lines)
        y = max(12, (h - total) // 2)
        for line, gap in lines:
            surface.blit(line, (w // 2 - line.get_width() // 2, y))
            y += line.get_height() + gap

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

    def forget_frame_measurements(self) -> None:
        """Throw away the frame history, because it is about to stop being
        about one thing.

        `_frame_ms` and `_frame_intervals` are rolling windows of the last
        FRAME_SAMPLES frames -- a minute at 60 Hz. Flipping the pacing in
        the middle of that leaves the log averaging a minute that was half
        one mode and half the other, which cannot show a difference however
        large the difference is. The player pressed the switch repeatedly,
        read "no difference", and was right about the number and wrong about
        the world: the number could not have said anything else.

        So a change of pacing starts the measurement again, and
        `frame_intervals_measured` then doubles as how long the mode being
        reported has actually been running.
        """
        self._frame_ms.clear()
        self._frame_intervals.clear()
        self._frame_shown_at = None

    def record_frame_shown(self, at_s: float) -> None:
        """When a picture actually went out, so the GAPS can be counted.

        `record_frame_ms` answers "can this machine keep up" -- it times the
        work before the wait that pads a frame out. It cannot answer the
        other question the player asks, which is whether the pictures arrive
        EVENLY: a machine drawing in 4 ms of a 16.7 ms budget can still hand
        them over raggedly, and a note that hesitates and then jumps double
        reads as juddering however much headroom the log reports.

        **What this cannot see, and it matters.** Without vsync `flip`
        returns before the panel has shown anything, so a frame the DISPLAY
        held twice is invisible from in here. This measures the app's own
        cadence and nothing else -- which is exactly what makes it worth
        having: a ragged cadence is the app's fault and fixable without
        vsync, while a dead-even one says the remaining judder is the beat
        between the app's timer and a panel running at some other rate, and
        only vsync answers that.

        A gap across a pause is not an interval, so the timestamp is dropped
        whenever the song is not running rather than charged to the next
        frame as a stutter that never happened.
        """
        previous = self._frame_shown_at
        self._frame_shown_at = at_s if self._playing else None
        if previous is None or not self._playing:
            return
        self._frame_intervals.append((at_s - previous) * 1000.0)
        if len(self._frame_intervals) > self.FRAME_SAMPLES:
            del self._frame_intervals[
                :len(self._frame_intervals) - self.FRAME_SAMPLES]

    def _interval_line(self, fh) -> None:
        """How evenly the pictures arrived, against their own usual gap.

        Measured against the MEDIAN interval rather than against 16.7 ms: the
        question is whether this app hands frames over at a steady rate, and
        a steady 17.4 ms is a different report from an average 16.7 that is
        really 16.7 and 33.3 in turns. The second one is what juddering is.
        """
        intervals = sorted(self._frame_intervals)
        if not intervals:
            fh.write("frame_interval_ms\t(nothing measured)\n")
            return
        median = intervals[len(intervals) // 2]
        uneven = sum(1 for ms in intervals
                     if abs(ms - median) > median * FRAME_EVEN_FRACTION)
        fh.write(f"frame_interval_median\t{median:.2f}\n")
        fh.write(f"frame_interval_best_tenth\t{intervals[int(len(intervals) * 0.1)]:.2f}\n")
        fh.write(f"frame_interval_worst_tenth\t{intervals[int(len(intervals) * 0.9)]:.2f}\n")
        fh.write(f"frames_per_second_shown\t{1000.0 / median:.1f}\n"
                 if median > 0 else "frames_per_second_shown\t(no gap)\n")
        fh.write(f"frames_uneven_percent\t{100 * uneven / len(intervals):.0f}\n")
        fh.write(f"frame_intervals_measured\t{len(intervals)}\n")

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

    def _step_key_ready(self, key: int) -> bool:
        """Whether a stepping key may act now, or is a repeat arriving too fast.

        The first press of a key always acts. While it stays down, repeats are
        honoured at most every `STEP_KEY_REPEAT_S`, so holding it walks the
        setting at a readable pace instead of crossing the whole range in
        two thirds of a second -- and a burst of repeats drained after a
        stalled frame moves it by one step, not by ten.
        """
        now = time.monotonic()
        if key != self._step_key or now - self._step_key_at >= STEP_KEY_REPEAT_S:
            self._step_key = key
            self._step_key_at = now
            return True
        return False

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
        # WHICH BUILD wrote this. Three fixes in a row came back as "does
        # nothing" while their code was in the tree and under test, and each
        # was an older EXE -- without this line "is it fixed" and "did it
        # reach the machine" are the same question with no way to tell them
        # apart.
        from pickhero.build_info import build_stamp
        fh.write(f"build\t{build_stamp()}\n")
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
        dropped = getattr(capture, "dropped_buffers", 0)
        busy = getattr(capture, "dropped_while_busy", 0)
        # Said apart, because they mean opposite things. A dropout while a
        # background measurement is running costs nothing -- it does not use
        # the microphone and the player is not meant to be playing -- and the
        # same number during a run loses notes at random.
        fh.write(f"dropped_buffers\t{dropped}"
                 + (f"\t({busy} of them while measuring — those cost nothing)"
                    if busy else "") + "\n")
        # The OUTPUT, which this log never mentioned. A run where the sound
        # went wrong and a run where it did not are otherwise identical here.
        fh.write(f"output_device\t{output.describe()}\n")
        # The OTHER thing in this process that makes sound. A hum that
        # survives Shift+A is not the mixer, and a log naming only the mixer
        # cannot say that.
        fh.write(f"midi_output\t{midi_playback.output_name()}\n")
        # A stutter that clears when the song is PAUSED is a backlog, and
        # pausing is the one thing that drains these every frame without
        # doing anything else. If Shift+A does nothing while it stutters,
        # the main loop is not reading keys either -- which is a stalled
        # frame, not a bad mixer. These two numbers tell the two apart.
        fh.write(f"worst_note_backlog\t"
                 f"{getattr(capture, 'worst_note_backlog', 0)}\n")
        fh.write(f"worst_window_backlog\t"
                 f"{getattr(capture, 'worst_window_backlog', 0)}\n")
        # Whether the engraver is really in this build. It imports perfectly
        # happily without its 20 MB of data files and then renders an empty
        # page, and the EXE is the only place that question is settled -- so
        # the answer travels in the log rather than being assumed.
        fh.write(f"engraver\t{_engraver_state()}\n")
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
        # Where the rescues that did NOT happen were lost. Held but never
        # asked means the audio window never arrived (a strike too close to
        # the next one); asked but refused means the verifier could not find
        # the written note in the sound. Those are fixed in different places,
        # and a single "rescued 12" cannot tell them apart -- reconstructing
        # it by hand from the strike table is what the last report cost.
        held, asked = matcher.rescue_held, matcher.rescue_asked
        fh.write(f"rescue_held\t{held}\n")
        fh.write(f"rescue_no_window\t{max(0, held - asked)}\n")
        fh.write(f"rescue_asked\t{asked}\n")
        fh.write(f"rescue_already_credited\t{matcher.rescue_already_credited}\n")
        fh.write(f"rescue_refused\t{matcher.rescue_refused}\n")
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
        anchors = self._mp3_anchors()
        sync = self._sync_map()
        fh.write(f"mp3_sync_points\t{len(anchors)}\t"
                 + " ".join(f"{at / 1000:.0f}s:{off:+.0f}ms"
                            for at, off in anchors) + "\n")
        # One rate per gap between points. Two numbers pulling in OPPOSITE
        # directions is the whole reason this is a list: no single rate, and
        # no stretched copy of the recording, can express it.
        fh.write(f"mp3_sync_sections\t{len(sync.rates())}\t"
                 + " ".join(f"{(1 / rate - 1) * 100:+.2f}%"
                            for rate in sync.rates()) + "\n")
        # How far the picture had to be pulled to stay with the recording,
        # and how much of that the map was already doing. A large pull with
        # few points says where the next point belongs.
        # WHERE the points are, next to how many there are. Beyond the
        # outermost one the map extrapolates, and a song whose points stop
        # two thirds of the way through is a song whose last minute is a
        # guess -- which is exactly what "synced at the start, apart at the
        # end" looks like from the inside. Measured on the song that
        # prompted it: 5 of 41 windows readable, all of them before 2:48 of
        # a 4:03 song, and a fitted drift of 3 % where a real one is about 1.
        if anchors:
            covered = 100.0 * (anchors[-1][0] - anchors[0][0]) / max(
                1.0, self._timeline.duration_ms)
            fh.write(f"mp3_sync_covers\t{anchors[0][0] / 1000:.0f}"
                     f"-{anchors[-1][0] / 1000:.0f}s of "
                     f"{self._timeline.duration_ms / 1000:.0f}s"
                     f"\t{covered:.0f}%\n")
        fh.write(f"mp3_worst_pull_ms\t{self._worst_sync_pull_ms:.0f}\n")
        fh.write(f"mp3_snaps\t{self._mp3_snaps}\n")
        fh.write(f"mp3_leads\t{'yes' if self._mp3_led else 'no'}\n")
        fh.write(f"seeks\t{self._seeks}\n")
        self._frame_line(fh)
        self._interval_line(fh)
        # Which pacing this reading came from. Two logs that differ in the
        # one thing being tested are worth nothing if neither says which was
        # which -- and this session has already lost a day to exactly that.
        fh.write(f"vsync\t{self._config.display.vsync_outcome}\n")
        fh.write("pacing\t"
                 f"{'steady' if self._config.display.steady_pace else 'system timer'}\n")
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

        # Every written note and what became of it -- with the BAR it is in,
        # the fret and what the tab asked for there. Milliseconds locate a
        # note for a machine and for nobody else: "practise bar 36 to 45" is
        # something a player can act on and "practise at 78341 ms" is not.
        # The fret and the technique are here for the same reason -- a run of
        # bends and a stretch across four frets fail for different causes and
        # are practised differently -- and because they make the log readable
        # WITHOUT the tab file beside it, which is what lets a log be handed
        # to anybody who does not have the song.
        starts = [m.start_ms for m in self._timeline.measures]
        written_at: dict[int, int] = {}
        for note in self._timeline.notes:
            key = int(round(note.timestamp_ms))
            written_at[key] = written_at.get(key, 0) + 1
        fh.write("\n# every written note and how it ended up\n")
        fh.write("note_ms\tbar\tstring\tfret\tmidi\ttech\tchord\tverdict\n")
        for note in sorted(self._timeline.notes,
                           key=lambda n: (n.timestamp_ms, -n.string)):
            # A tab that parsed without measure info has no bars to name,
            # and a "0" there would read as one. Same rule as the technique
            # column: a dash says the answer is missing, a number says it is
            # this one.
            bar = (bisect.bisect_right(starts, note.timestamp_ms + 1e-6)
                   if starts else "-")
            fh.write(f"{note.timestamp_ms:.1f}\t{bar}\t{note.string}"
                     f"\t{note.fret}\t{note.midi_note}"
                     f"\t{_technique_flags(note)}"
                     f"\t{written_at.get(int(round(note.timestamp_ms)), 1)}"
                     f"\t{matcher.get_note_state(note).value}\n")

    def help_blocks(self) -> list[tuple[str, list, str]]:
        """The help page as data: (heading, items, text size).

        Data rather than drawing calls because this is now the ONE place
        every bound key is written down -- the footer carries the twelve
        worth watching while playing, and the rest live here. A test reads
        handle_event's own source against this, so adding a shortcut and
        forgetting to document it fails in the suite instead of shipping a
        key nobody can find.

        An item is one of three things: a line of text; a (colour, line)
        pair for a colour swatch; or a (label, value) pair, where the value
        is what that setting is RIGHT NOW. The last one is why this page is
        worth opening mid-song and not only once: "G: hit window" is a key,
        "±150 ms" is the answer to the question you opened the page with.
        """
        t = get_theme()
        meta = self._timeline.metadata
        return [
                ("Reading the Track", [
                "The number on each note is the fret to press (0 = open).",
                "A note's colour matches its row, so it names the string.",
                "Standard: the board scrolls right-to-left. Play as the",
                "  START of a note reaches the hit line, not its middle.",
                "  A note it is done with is dimmed out of the way.",
                "Hybrid: the sheet holds still and the playhead moves.",
                "  Nothing is dimmed — the row behind the playhead keeps",
                "  its verdict colours, so you can look back at the run.",
                "Tab page: the engraved score, a dot under each note.",
                ], "body"),

                ("The 6 Rows = 6 Guitar Strings", [
                (STRING_COLORS.get(s, (180, 180, 180)), label)
                for s, label in (
                    (1, "Row 1 (top)      = high E  (thinnest)"),
                    (2, "Row 2               = B"),
                    (3, "Row 3               = G"),
                    (4, "Row 4               = D"),
                    (5, "Row 5               = A"),
                    (6, "Row 6 (bottom) = low E  (thickest)"),
                )
                ], "body"),

                ("Techniques (badge above the note says which)", [
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
                ], "body"),

                ("Scoring (colours change after you play)", [
                (t.feedback_hit, "Green \u2014 you played the correct note"),
                (t.feedback_close, "Yellow \u2014 close, off by 1 semitone"),
                (t.feedback_miss, "Red \u2014 missed, or not played in time"),
                ], "body"),

            # Every key handle_event answers is here, because the footer is now
            # one line of the twelve worth watching WHILE playing. A key that is
            # bound and written down nowhere is a key nobody finds, and this
            # overlay is the only place left that can carry them all.
                ("Playing", [
                ("SPACE: play/pause", "playing" if self._playing else "paused"),
                "HOME: restart     ESC: song list",
                "LEFT/RIGHT: a beat   Shift: a bar   Ctrl: 30 seconds",
                ("PgDn/PgUp: practice speed, kept for this song",
                 f"{meta.tempo} BPM ({int(self._tempo_factor * 100)} %)"),
                ("A: audio on/off", "on" if self._audio_enabled else "off"),
                ("W: wait mode (holds for the right note)",
                 "on" if self._wait_mode else "off"),
                ("E: skip a long rest (jumps to 3 s before the next note)",
                 "a rest is here" if self._rest_hud_text() else "nothing to skip"),
                ("I/O: loop markers     P: loop on/off",
                 self._loop_hud_text() or "no loop set"),
                "L: loop the weakest part",
                ("TAB: choose track", meta.track_name or "—"),
                "H: this help",
                ], "small"),

                ("What you see", [
                ("Shift+T: the board, the hybrid sheet or the tab page",
                 VIEW_SHORT[self._view]),
                "+/-: note size on the sheet and the page.  On the board",
                "  it is a trade: + pushes the notes further apart and shows",
                "  less of the song, - buys look-ahead by moving them in.",
                ("Shift+C: chord view — grips and a block per chord",
                 "on" if self._chord_mode else "off"),
                ("V: chord scoring", "one string is enough"
                 if self._chord_partial_credit else "every string"),
                ("T: theme", self._config.theme),
                ("F: fret limit", f"up to fret {self._max_fret}"),
                # Low string first, the order a guitarist names them in and
                # the reverse of the index: active_strings[0] is the high e.
                ("F1-F6: mute a string", "".join(
                    "EADGBe"[5 - i] if self._active_strings[i] else "·"
                    for i in (5, 4, 3, 2, 1, 0))),
                ("J: per-string chord check",
                 "on" if getattr(self._config, "chord_verify", True) else "off"),
                ("R / Shift+R: play the same shapes in another tuning",
                 next((n for n, role in self.tuning_segments()
                       if role in ("played", "both")), "—")),
                ], "small"),

                ("Sound and scoring", [
                ("B: MIDI backing", "—" if self._midi_player is None
                 else "off" if self._backing_muted else "on"),
                ("Shift+B: your own part, as a guide",
                 "—" if self._guide_player is None
                 else "off" if self._guide_muted else "on"),
                ("Ctrl+U: paste a Songsterr link, so Ctrl+S can fall back",
                 f"song {self._songsterr_id()}" if self._songsterr_id()
                 else "none pasted"),
                ("U: recorded backing on/off     Shift+U: pick the file",
                 "—" if self._mp3_player is None
                 else "off" if self._mp3_muted else "on"),
                ("X/C: noise gate down / up",
                 f"{int(self._noise_gate_db)} dB"
                 + (" (auto)" if self._auto_gate else "")),
                ("G: hit window — how far off the beat still counts",
                 f"±{int(self._config.timing_window_ms)} ms"),
                ("K: measure your timing offset     Shift+K: back to 0",
                 f"{int(self._config.audio_latency_offset_ms):+d} ms"),
                ",/.: nudge that offset by 10 ms",
                "Shift+A: reopen the audio output, if the sound goes bad",
                "Y: timing report — which timing problem you actually have",
                "Shift+Y: save the raw measurements as a CSV",
                "D: save a full run log (what every strike did)",
                ("Z: vsync", getattr(self._config.display, "vsync_outcome",
                                     "off")),
                ("Shift+Z: steady frame pacing",
                 "on" if getattr(self._config.display, "steady_pace", False)
                 else "off"),
                ], "small"),

                ("S: lining the recording up", [
                ("S opens the sync panel at the bottom and closes it again",
                 "open" if self._show_sync else "shut"),
                "Everything below is in it, and none of it is on screen",
                "while it is shut — the entry turns yellow if it needs you.",
                ("N/M: MIDI backing earlier / later    Alt+N/M: by a second",
                 f"{int(self._backing_offset()):+d} ms"),
                "Shift+N / Shift+M: the recording, by 10 ms",
                "Ctrl+N / Ctrl+M: by a second    Ctrl+Shift: by ten seconds",
                "  (reaches 8 minutes, for a tab that is only the solo)",
                ("Alt+S: choose where this song\'s sync comes from",
                 SYNC_SOURCE_WORDS[self._sync_source()]),
                ("Ctrl+S: find the offsets by listening to the recording",
                 self._sync_span_label()),
                "Shift+S: line it up HERE and add a sync point.",
                "  Two points give one speed, three give two sections, and a",
                "  band that played without a click needs the sections.",
                "  Ctrl+Shift+S clears them all.",
                "On the song list, O opens the settings screen — everything",
                "that is set once, with anything away from standard marked.",
                ], "small"),
        ]

    def help_lines(self) -> list[str]:
        """Every line of the help page as plain text, values included.

        One reader for three item shapes, so the test that checks every
        bound key is written down cannot disagree with what is drawn.
        """
        out = []
        for _, items, _ in self.help_blocks():
            for item in items:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item[0], tuple):
                    out.append(item[1])                 # colour swatch
                else:
                    out.append(f"{item[0]}   {item[1]}")
        return out

    def _sync_span_label(self) -> str:
        """What the automatic pass has measured, in one phrase."""
        points = len(self._sync_map().points) if self._sync_map() else 0
        if not points:
            return "not measured"
        return f"{points} points"

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
        fonts = {"body": _get_font("arial", 15), "small": _get_font("arial", 13)}
        steps = {"body": 20, "small": 17}
        hint_font = fonts["small"]

        cx = w // 2
        title_surf = title_font.render("Help", True, t.hud_accent)
        surface.blit(title_surf, (cx - title_surf.get_width() // 2, 12))

        top, bottom = 56, h - 30
        col_w = (w - 60) // 3
        columns = [30, 30 + col_w + 15, 30 + 2 * (col_w + 15)]
        col = 0
        x, y = columns[0], top

        for title, items, size in self.help_blocks():
            font, step = fonts[size], steps[size]
            # Whole blocks move to the next column, never halves of one: a
            # heading stranded at the foot of a column with its list carrying
            # on at the top of the next reads as two unrelated things.
            needed = 24 + step * len(items) + 8
            if y + needed > bottom and col + 1 < len(columns):
                col += 1
                x, y = columns[col], top
            surface.blit(section_font.render(title, True, t.hud_accent), (x, y))
            y += 24
            # Values in a column of their own, at the width of the widest
            # label in THIS block: a value tacked straight onto the end of
            # each line makes a ragged edge nobody can scan down.
            labels = [i[0] for i in items
                      if isinstance(i, tuple) and isinstance(i[0], str)]
            value_x = (max(font.size(text)[0] for text in labels) + 16
                       if labels else 0)
            for item in items:
                if isinstance(item, tuple) and isinstance(item[0], tuple):
                    colour, label = item
                    pygame.draw.rect(surface, colour, (x, y + 3, 13, 13),
                                     border_radius=2)
                    surface.blit(font.render(label, True, t.hud_text),
                                 (x + 20, y))
                elif isinstance(item, tuple):
                    label, value = item
                    surface.blit(font.render(label, True, t.hud_text), (x, y))
                    surface.blit(font.render(value, True, t.hud_accent),
                                 (x + value_x, y))
                else:
                    surface.blit(font.render(item, True, t.hud_text), (x, y))
                y += step
            y += 8

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

    def _mp3_leads(self) -> bool:
        """Whether the recording is the clock and the picture follows it.

        It is, whenever it is really sounding. A recording has its own clock
        in the sound card and cannot be bent without a seek; the picture can
        be pulled by a fraction of a millisecond a frame and nobody sees it.
        So the correction goes on the cheap side -- which is also the only
        way a warped tab can be followed at all, since the alternative is
        seeking the audio every few seconds for the whole song.
        """
        player = self._mp3_player
        return bool(
            player is not None and player.ready and player.playing
            and not player.suspended and not self._mp3_muted
            and self._mp3_pending_seek_ms is None
            and self._playing and self._playback_ms >= 0)

    def _follow_recording(self, real_elapsed_s: float) -> None:
        """Pull the song clock towards where the recording actually is."""
        if not self._mp3_leads():
            return
        wanted = self._sync_map().song_at(self._mp3_player.position_ms())
        error = wanted - self._playback_ms
        if abs(error) < 1.0:
            return
        if abs(error) > SYNC_SNAP_MS:
            # Not drift: something moved. Creeping there would scroll half a
            # minute of music the player never asked for.
            #
            # Counted, because a snap is the picture JUMPING and the player
            # sees it. In normal playback it should never happen: past a
            # second and a half the map and the recording are describing
            # different moments, which is a map that was measured wrong.
            # On the song that found this, three snaps of up to 4.8 s.
            self._mp3_snaps += 1
            step = error
        else:
            room = real_elapsed_s * 1000.0 * SYNC_PULL_FRACTION
            step = max(-room, min(room, error))
        self._worst_sync_pull_ms = max(self._worst_sync_pull_ms, abs(error))
        self._mp3_led = True
        self._playback_ms = max(0.0, self._playback_ms + step)
        # The audio clock is anchored to song time, so moving song time moves
        # the anchor with it. Re-anchoring properly would throw away every
        # strike still queued, which is not something a frame may do.
        self._audio_anchor_song_ms += step
        if self._matcher is not None:
            self._matcher.audio_offset_ms += step

    def _sync_map(self) -> SyncMap:
        """Where this tab and this recording agree, and the lines between.

        Built fresh from the stored points every time it is asked for: they
        change while the player is placing them, and a map cached behind that
        is the kind of thing that answers yesterday's question.
        """
        return SyncMap(self._mp3_anchors(), self._mp3_offset())

    def _mp3_ms(self, playback_ms: float) -> float:
        """Song position as the recording should hear it.

        A positive offset makes the recording sound LATER, so it is
        subtracted -- the same convention as the MIDI backing. With sync
        points set, the offset is what the map says HERE rather than one
        number for the whole song.
        """
        return self._sync_map().recording_at(playback_ms)

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

    def _mp3_anchors(self) -> list[tuple[float, float]]:
        """The places the player lined this recording up, sorted."""
        getter = getattr(self._config, "mp3_anchors_for", None)
        return getter(self._song_key) if getter else []

    def _mp3_plan(self) -> tuple[tuple[float, float], ...]:
        """((fraction through the song, how fast the recording runs), ...).

        One entry per gap between anchors. Two anchors give one entry, which
        is the straight line the first version could express; three give two,
        which is the least that can follow a band. The rate between two
        anchors is `1 - (offset change) / (time between them)` -- the same
        arithmetic as before, applied per segment.
        """
        anchors = self._mp3_anchors()
        length = self._timeline.duration_ms or 1.0
        plan: list[tuple[float, float]] = []
        for (s1, o1), (s2, o2) in zip(anchors, anchors[1:]):
            if s2 - s1 < MIN_SYNC_SPAN_MS:
                continue
            rate = 1.0 - (o2 - o1) / (s2 - s1)
            if not (MIN_MP3_RATE <= rate <= MAX_MP3_RATE):
                continue
            plan.append((max(0.0, min(1.0, s1 / length)), rate))
        return tuple(plan)

    def _mp3_rate(self) -> float:
        """The FIRST segment's rate, which is all a single number can say.

        For the HUD and the run log only -- "how far off is this recording"
        wants one number. NOTHING is built from it any more: see below.
        """
        plan = self._mp3_plan()
        return plan[0][1] if plan else 1.0

    def _mp3_build_tempo(self) -> float:
        """What `timestretch.build` has to be asked for.

        The PRACTICE SPEED and nothing else. The sync correction used to be
        multiplied in here, and that was the wrong lever twice over: it
        rebuilt the whole file for a percent (seconds of work, silence until
        it landed, a cache entry per attempt), and it could only ever apply
        ONE rate to a recording whose rate varies by a factor of three across
        a song. The correction is a warp of the tab now -- free, instant, and
        as detailed as the number of sync points -- so putting it in the file
        as well would apply it twice. See audio/syncmap.py.
        """
        return self._tempo_factor

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
        if self._mp3_loaded_source_transpose != self._transpose:
            return False
        if self._mp3_loaded_build is None:
            # A source this screen did not load itself. All that is known is
            # what the player reports, which is the scale -- and since the
            # only thing that decides the file now is the practice speed,
            # that is the whole answer.
            return True
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
        #
        # AND the tuning, which this decided without for a while. The practice
        # speed is not the only thing that makes the recording a different
        # file: a song played in another tuning sounds a tone higher, so the
        # backing has to be shifted with it. At 100 % speed the test passed on
        # the speed alone, the original was loaded whatever the tuning, and
        # `_mp3_loaded_source_transpose` was then set to a shift that had NOT
        # been applied -- so the fit check agreed and it was never rebuilt.
        # The player heard the recording at its written pitch against a guitar
        # playing a tone above it, which is precisely what they reported.
        if abs(wanted - 1.0) < 1e-3 and not self._transpose:
            self._mp3_player.set_source(self._mp3_path(), self._mp3_scale())
            self._mp3_loaded_build = wanted
            self._mp3_loaded_source_transpose = self._transpose
            return
        if self._mp3_stretch_matches(self._mp3_stretch_done, wanted):
            self._mp3_player.set_source(self._mp3_stretch_done[2],
                                        self._mp3_scale())
            self._mp3_loaded_build = wanted
            self._mp3_loaded_source_transpose = self._transpose
            return
        if self._mp3_stretch_matches(self._mp3_stretch_failed, wanted):
            return                             # already said so on screen
        if self._mp3_stretch_wanted is not None:
            return                             # a build is already running
        self._start_mp3_stretch(wanted)

    def _mp3_stretch_matches(self, entry, tempo: float) -> bool:
        """Whether a build belongs to this recording, this speed AND this tuning.

        The tuning is the third of the three and was missing: a copy built at
        +2 was handed straight back for the written tuning, and the other way
        round, because the memo was keyed by speed and file only. The cache on
        disk had it right all along (`timestretch.cache_name` hashes the
        semitones); it was this one-entry memo in front of it that did not.
        """
        if not entry or entry[0] != tempo or entry[1] != self._mp3_path():
            return False
        return (entry[3] if len(entry) > 3 else 0) == self._transpose

    def _start_mp3_stretch(self, tempo: float) -> None:
        """Build the stretched copy off the game loop."""
        path = self._mp3_path()
        if not path:
            return
        self._mp3_stretch_wanted = tempo
        self._mp3_stretch_progress = 0.0
        # The recording moves with the player: a tab in Drop C played on a
        # Drop D guitar sounds a tone higher, so the backing has to. Pitch
        # shifting preserves the LENGTH exactly, so every sync point, the
        # offset and the whole map still describe this file.
        semitones = self._transpose
        # No sync plan any more: the practice speed is the only thing that
        # decides the file. The recording is left exactly as it was made and
        # the TAB is warped onto it. See _mp3_build_tempo.
        cache_dir = config_module.CONFIG_DIR / "stretched"

        def report(fraction: float) -> bool:
            self._mp3_stretch_progress = fraction
            # Stepping the tempo down three times should not build three
            # copies before reaching the one that was asked for.
            return abs(self._mp3_build_tempo() - tempo) < 1e-6

        def work() -> None:
            try:
                built = timestretch.build(Path(path), tempo, cache_dir,
                                          report, semitones=semitones)
                self._mp3_stretch_done = (tempo, path, str(built),
                                          semitones)
            except timestretch.Cancelled:
                pass                          # the speed moved on; not a fault
            except Exception as exc:
                # Not every format SDL can stream can also be decoded into
                # memory. Named rather than swallowed: "convert it" is a thing
                # the player can act on, silence is not.
                self._mp3_stretch_failed = (
                    tempo, path,
                    f"cannot be slowed down ({type(exc).__name__}) — "
                    f"convert it to OGG or WAV", semitones)
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
        # When the recording leads, nothing here may seek it -- the picture is
        # what gets pulled. See _follow_recording.
        self._mp3_player.update(target, correct=not self._mp3_leads())
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
        # Sync points and the offset belong to (song, RECORDING), not to the
        # song alone: another rip has another intro and another encoder
        # padding, so points measured against the old file put the new one
        # out by seconds while looking like a synced song. Dropped only when
        # the path really changes -- re-picking the same file after moving it
        # must not throw the player's work away.
        replaced = bool(self._mp3_path()) and chosen != self._mp3_path()
        setter = getattr(self._config, "set_mp3_path_for", None)
        if setter is not None:
            setter(self._song_key, chosen)
            self._config.save()
        if replaced:
            self._forget_sync_for_new_recording()
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
            self._sync_after_new_recording()

    def _sync_after_new_recording(self) -> None:
        """Line the new recording up, without being asked.

        *"Wenn ich nur noch das MP3 laden und mit sh+U im Song verknüpfen
        muss und die Songsterr Sync schon im Song ist, dann reicht das."*

        Picking a recording and then having to press Ctrl+S is one key that
        exists only because nothing connected the two. The recording is new,
        it has no sync, and there is exactly one thing to do with it -- so it
        happens.

        **Only when there is nothing there.** A song that already carries
        sync points carries the player's own work in them: Shift+N/M
        nudges, Shift+S pins, an anchor set by ear in the middle of a song
        that drifts. Re-picking the same file must never throw that away,
        and re-picking is exactly what somebody does after moving the file.
        `_forget_sync_for_new_recording` has already cleared the points when
        the FILE genuinely changed, so an empty list here means there is
        nothing of his to lose.

        Sync by hand is a choice, not a gap, so it is left alone.
        """
        if self._mp3_anchors():
            return
        if self._sync_source() == "hand":
            return
        self._start_auto_sync()

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

        It is also the experiment that says WHERE -- and for a whole cycle it
        could not be, because it reached only the MIXER. There are TWO things
        in this process that make sound: the mixer, which plays the recording,
        and the MIDI synth, which plays the backing. A hum that survives
        leaving the song and dies only when the app is closed is what a synth
        still holding something sounds like, and no amount of reopening the
        mixer can touch it. So this now silences both, and SAYS which ones it
        reached: "reopened, MIDI synth silenced" against "reopened, no MIDI
        output" is the difference between two diagnoses.
        """

        # Before the mixer, because it is the half that has never been tried.
        # Not through the players: one that was dropped without being closed
        # still has its notes sounding, and reaching the PORT is the point.
        for player in self._midi_all():
            player.pause()
        had_midi = midi_playback.panic()
        if not output.reopen():
            self._say("Audio output could not be reopened"
                      + (" — MIDI synth silenced" if had_midi else ""))
            return
        # The mixer forgot the file along with everything else.
        self._mp3_loaded_build = None
        if self._mp3_player is not None:
            self._mp3_player.close()
            self._load_mp3_for_song()
            self._ensure_mp3_source()
            if self._mp3_plays():
                self._mp3_player.seek(self._mp3_ms(self._playback_ms))
        for player in self._midi_all():
            player.seek(self._backing_ms(self._playback_ms))
        self._say(f"Audio output reopened — {output.describe()}"
                  + ("; MIDI synth silenced" if had_midi
                     else "; no MIDI output to silence"))

    def _forget_sync_for_new_recording(self) -> None:
        """A different file needs its own sync, and says so.

        Keeping the old points would be worse than having none: they were
        measured against another recording and would put this one out by
        seconds while the panel still read "17 points".
        """
        had = len(self._mp3_anchors())
        for name, value in (("set_mp3_anchors_for", []),
                            ("set_mp3_rate_for", 1.0),
                            ("set_mp3_offset_for", 0.0)):
            setter = getattr(self._config, name, None)
            if setter is not None:
                setter(self._song_key, value)
        self._config.save()
        self._sync_lines = []
        self._mp3_loaded_build = None
        self._say("A different recording — its sync starts fresh"
                  + (f" ({had} points dropped)" if had else "")
                  + ". Ctrl+S measures it.")

    def _start_auto_sync(self) -> None:
        """Find this recording's sync points by listening to it (Ctrl+S).

        Setting five points by hand is what the other tools ask for and it
        works; doing it before every song does not. The measurement that
        finds them is the one `tools/check_song_sync.py` already made -- the
        tab's pitch classes against the recording's, window by window --
        with its answer handed to the map instead of printed.
        """
        if self._auto_sync_thread is not None and self._auto_sync_thread.is_alive():
            self._say("Already listening to the recording…")
            return
        path = self._mp3_path()
        if not path:
            self._say("No backing track — Shift+U to pick one")
            return
        # The FILE where there is one, so every track of the band is
        # matched against the recording rather than the one guitar being
        # practised. A recording is the whole arrangement, and one track of
        # it is most of the evidence thrown away.
        tab = Path(self._song_path) if self._song_path else self._timeline
        song_id = self._songsterr_id()
        source = self._sync_source()
        if source == "hand":
            self._say("This song is set to sync by hand (O → Sync source). "
                      "Shift+N/M to line it up, Shift+S to pin it")
            return
        if source == "songsterr" and not song_id:
            self._say("This song is set to use Songsterr, but no link is "
                      "stored — copy one and press Ctrl+U")
            return

        def report(fraction: float, what: str) -> bool:
            self._auto_sync_progress = (fraction, what)
            return self._auto_sync_thread is not None

        def work() -> None:
            from pickhero.audio import autosync
            try:
                if source == "songsterr":
                    # Asked for by name (Alt+S). The listening is finer where
                    # it works -- 8 to 16 ms against 80 to 92 on the player's
                    # own recording -- but "where it works" is a judgement
                    # this makes about itself, and a player who can hear that
                    # it did not outranks it.
                    # Chosen by the player, so it is the only thing tried.
                    # Falling back to the listening here would be the app
                    # deciding again -- which is the thing that was reported.
                    found = self._ask_songsterr(song_id, path, report, None)
                    self._auto_sync_result = ("ok", found["points"], found)
                    return
                found = autosync.find(tab, path, report)
                if source == "auto" and not found["readable"] and song_id:
                    # The listening produced nothing. A made per-bar map does
                    # not care that a song repeats itself, which is the one
                    # thing that defeats a windowed search -- so it is the
                    # fallback, not the first answer. Measured on Thunder,
                    # on readings neither was fitted to: the listening is 8
                    # to 16 ms where it works and this is 80 to 92, so it is
                    # asked second and only when the first has nothing.
                    found = self._ask_songsterr(song_id, path, report, found)
                self._auto_sync_result = ("ok", found["points"], found)
            except Exception as exc:
                # Named rather than swallowed: "the file cannot be decoded"
                # is a thing the player can act on; silence is not.
                self._auto_sync_result = (
                    "error", f"{type(exc).__name__}: {exc}", None)

        self._auto_sync_progress = (0.0, "reading the recording")
        self._sync_lines = ["SYNC   listening to the recording…"]
        # Seconds of FFT on a worker thread is enough to starve the audio
        # callback on a laptop, and the player reports dropouts here. Marking
        # the capture does not prevent them -- only the machine can do that --
        # but it makes the run log able to say that these particular ones were
        # harmless, which "130 dropped buffers" on its own cannot.
        if self._audio_capture is not None:
            self._audio_capture.busy = True
        self._auto_sync_thread = threading.Thread(target=work, daemon=True)
        self._auto_sync_thread.start()

    def _take_auto_sync(self) -> None:
        """Store what the listening found, on the main thread."""
        result, self._auto_sync_result = self._auto_sync_result, None
        self._auto_sync_thread = None
        if self._audio_capture is not None:
            self._audio_capture.busy = False
        if result is None:
            return
        if result[0] == "error":
            self._sync_lines = [f"SYNC   could not read the recording — "
                                f"{result[1]}"]
            return
        _, points, report = result
        self._sync_lines = self._auto_sync_report_lines(points, report)
        if not report["readable"] or len(points) < 2:
            return
        setter = getattr(self._config, "set_mp3_anchors_for", None)
        if setter is None:
            return
        setter(self._song_key, points)
        rate_setter = getattr(self._config, "set_mp3_rate_for", None)
        if rate_setter is not None:
            rate_setter(self._song_key, 1.0)
        self._config.save()
        described = list(self._sync_lines)
        self._describe_sync()
        self._sync_lines = described + self._sync_lines

    def _silent_tail(self) -> tuple[float, int] | None:
        """(last note, empty bars after it), when a tab ends in silence.

        A Guitar Pro export is regularly padded out to the end of the sheet:
        What's Up is 80 bars of which the last ten carry nothing, so the
        clock reads 4:55 for four minutes and thirteen seconds of music. The
        player compared that against YouTube three times and concluded he had
        the wrong file.
        """
        notes = self._timeline.notes
        bars = self._timeline.measures
        if not notes or len(bars) < 2:
            return None
        last = max(n.timestamp_ms + n.duration_ms for n in notes)
        empty = sum(1 for bar in bars if bar.start_ms >= last)
        if empty < 1 or self._timeline.duration_ms - last < SILENT_TAIL_MS:
            return None
        return last, empty

    def _sync_source(self) -> str:
        """Which measurement this song's sync comes from.

        A setting rather than a judgement the app makes per run: "the
        listening decides whether the listening worked" is a circle, and the
        player who can hear the answer was left outside it.
        """
        getter = getattr(self._config, "sync_source_for", None)
        return getter(self._song_key) if getter else "auto"

    def _cycle_sync_source(self) -> None:
        """Alt+S: choose where this song's sync comes from.

        A CHOICE, not a one-off override. The first build had Alt+S run the
        bar map once and Ctrl+S go on judging for itself, and the player
        read that exactly right: "Ich habe das Gefühl es entscheidet noch
        immer selbst." One setting, four answers, and Ctrl+S obeys it.
        """
        setter = getattr(self._config, "set_sync_source_for", None)
        if setter is None:
            return
        order = list(getattr(self._config, "SYNC_SOURCES",
                             ("auto", "listen", "songsterr", "hand")))
        here = order.index(self._sync_source())
        chosen = order[(here + 1) % len(order)]
        setter(self._song_key, chosen)
        self._config.save()
        warn = ("" if chosen != "songsterr" or self._songsterr_id()
                else " — but no link is stored, press Ctrl+U")
        self._say(f"Sync source: {SYNC_SOURCE_WORDS[chosen]}{warn}")

    def _songsterr_id(self) -> int:
        getter = getattr(self._config, "songsterr_for", None)
        return getter(self._song_key) if getter else 0

    def _ask_songsterr(self, song_id: int, audio_path, report, failed) -> dict:
        """Fit Songsterr's own per-bar map to this recording.

        Kept whole rather than merged into the listening's answer: the two
        are different measurements and a map half from each would be neither.
        The one that could not read the song hands over completely.
        """
        from pickhero.audio import autosync
        from pickhero.tabs import songsterr
        try:
            bars, meta = self._songsterr_bar_times(song_id)
        except songsterr.NotFound as exc:
            # Marked as Songsterr's answer, not left wearing the listening's.
            # "could not read this recording" would send the player looking
            # for a better recording when the fault is a 404.
            out = dict(failed or {})
            out.update(source="songsterr", readable=False, points=[],
                       songsterr_error=str(exc))
            return out
        found = autosync.align_to_bar_times(
            self._whole_song_timeline(), audio_path, bars, report)
        found["songsterr_title"] = str(meta.get("title") or "")
        if failed is not None:
            found["listening"] = failed
        return found

    def _songsterr_bar_times(self, song_id: int):
        """(timelines, metadata) for this song, from disk if it is there.

        The download screen writes the whole reply beside the tab, so a song
        fetched in the app syncs on a machine with no network at all -- and
        pressing Ctrl+S twice does not ask Songsterr twice. The network is
        still there for a song whose link was pasted in by hand, and what it
        answers is written to the same place, so that song is offline from
        the second press on.

        Main video first either way: it is the recording the tab was written
        from, and after an in-app download it is also the recording on disk.
        """
        from pickhero.tabs import songsterr
        if self._song_path:
            cached = songsterr.bar_times_from_cache(self._song_path)
            if cached is not None:
                return cached
        meta = songsterr.fetch_meta(song_id)
        entries = songsterr.fetch_entries(song_id, int(meta["revisionId"]))
        bars = songsterr.preferred_bar_times(entries)
        if not bars:
            raise songsterr.NotFound(
                f"song {song_id} has video points but none usable")
        if self._song_path:
            try:
                songsterr.save_cache(self._song_path, song_id, meta, entries)
            except OSError:
                # A read-only songs folder is a slower song, not a broken
                # one. The map is in hand; only the keeping of it failed.
                pass
        return bars, meta

    def _whole_song_timeline(self):
        """Every pitched track of the file as one timeline.

        A recording is the whole band, and matching one guitar track against
        it throws most of the evidence away -- the same reason the listening
        reads the FILE rather than the track being practised.
        """
        from pickhero.tabs.timeline import Timeline
        if not self._song_path:
            return self._timeline
        from pickhero.tabs.loader import list_tracks, load_gp_file
        merged = []
        for info in list_tracks(Path(self._song_path)):
            if info.get("is_percussion"):
                continue
            try:
                merged.append(load_gp_file(Path(self._song_path),
                                           track_index=info["index"]))
            except Exception:
                continue
        if not merged:
            return self._timeline
        return Timeline([n for t in merged for n in t.notes],
                        merged[0].metadata, measures=merged[0].measures)

    @staticmethod
    def _clipboard_text() -> str:
        """Whatever is on the clipboard, or "" when there is no way to ask.

        The search box needs the same thing, so the tkinter lives in
        `ui/clipboard.py` now and this stays as the name the tests and the
        key handler already use.
        """
        from pickhero.ui.clipboard import clipboard_text
        return clipboard_text()

    def _paste_songsterr_link(self) -> None:
        """Ctrl+U: take a Songsterr link off the clipboard.

        The clipboard because the app has no text field and building one for
        a URL somebody has just copied out of their browser is a screen
        nobody wants. The same tkinter the file chooser already uses.
        """
        from pickhero.tabs import songsterr
        song_id = songsterr.song_id_of(self._clipboard_text())
        if not song_id:
            self._say("Copy a Songsterr link first, then press Ctrl+U")
            return
        setter = getattr(self._config, "set_songsterr_for", None)
        if setter is None:
            return
        setter(self._song_key, song_id)
        self._config.save()
        self._say(f"Songsterr {song_id} — Ctrl+S will use its bar map if the "
                  f"listening cannot read this song")

    def _auto_sync_report_lines(self, points, report) -> list[str]:
        from pickhero.audio import autosync
        """What the listening found, in words a player can act on.

        "28 of 51 windows usable" is a number nobody can do anything with.
        Where the two stopped being the same piece of music is a PLACE to put
        a point, and whether the reading is worth storing at all is the one
        thing the old line never said -- it stored a map either way and the
        player found out four minutes later.
        """
        # Routed FIRST, not after the failure branches. A Songsterr answer
        # that failed used to come out wearing the listening's words -- "4 of
        # 40 windows agreed" when the truth was a 404 -- which sends the
        # player looking for a better recording over a broken link.
        if report.get("source") == "songsterr":
            return self._songsterr_report_lines(points, report)
        if report.get("wrong_length"):
            tempo = autosync.written_tempo_gap(
                self._timeline, report.get("recording_s", 0.0))
            if tempo is not None and abs(tempo["ratio"] - 1.0) > 0.02:
                # The tab's bars are all one length, so its tempo is the one
                # thing wrong with it -- and the right tempo is a number to
                # act on, where "16 % apart" is not. No offset can repair a
                # rate, which is why one sync point fixes the start and the
                # two walk apart again immediately after it.
                # NAMED AS A GUESS, because the length can be off for two
                # reasons and this cannot tell them apart. What's Up looked
                # like 16 % of tempo and turned out to be mostly structure:
                # Songsterr times 72 bars where the tab has 80, so eight
                # bars of it are not in the recording at all, and only about
                # 3 % is really the tempo. Saying "the tab is 16 % too slow"
                # would have sent the player to change a tempo that is very
                # nearly right.
                return [
                    f"SYNC   this tab is {format_time(report['tab_s'] * 1000)}"
                    f" and the recording is "
                    f"{format_time(report['recording_s'] * 1000)} — either "
                    f"the written {tempo['written']:.0f} BPM should be about "
                    f"{tempo['wanted']:.0f}, or the tab has "
                    f"{abs(tempo['ratio'] - 1) * 100:.0f} % of music the "
                    f"recording does not",
                    "SYNC   no offset or rate can repair either, so nothing "
                    "was stored. Songsterr's bar map can tell them apart "
                    "(Ctrl+U, then Alt+S)",
                ]
            # Said before anything else and in different words, because it is
            # the one finding here that means "go and get another file"
            # rather than "place a point". The player spent a session on
            # Thunder's sync with a tab a hundred seconds longer than the
            # recording, and nothing on screen ever compared the two numbers.
            return [
                f"SYNC   this tab is "
                f"{format_time(report['tab_s'] * 1000)} long and the "
                f"recording is {format_time(report['recording_s'] * 1000)} — "
                f"{report['length_gap'] * 100:.0f} % apart",
                "SYNC   they are not the same transcription, and no sync can "
                "bridge that. Nothing was stored — try another tab of this "
                "song",
            ]
        if not report["readable"] or len(points) < 2:
            why = (f"{report['usable']} of {report['windows']} windows agreed"
                   + (f", {report['ambiguous']} could not tell one chorus "
                      f"from another" if report["ambiguous"] else ""))
            return [
                f"SYNC   could not read this recording against this tab — "
                f"{why}",
                "SYNC   nothing was stored. Shift+N/M to line it up by hand, "
                "Shift+S to pin it there",
            ]
        lines = []
        covered = report["covered"]
        span = (f", {format_time(covered[0] * 1000)}–"
                f"{format_time(covered[1] * 1000)} of "
                f"{format_time(report['song_s'] * 1000)} covered"
                if covered else "")
        lines.append(
            f"SYNC   found by listening — {report['usable']} of "
            f"{report['windows']} windows{span}")
        if report["breaks"]:
            where = "  ".join(f"{format_time(at * 1000)} ({by:+.1f} s)"
                              for at, by in report["breaks"][:4])
            lines.append(
                f"SYNC   the tab and the recording part company at {where}"
                " — check those places by ear")
        return lines

    def _songsterr_report_lines(self, points, report) -> list[str]:
        """What Songsterr's bar map did, said as its own answer.

        Never dressed up as the listening: it is a different measurement,
        five to ten times coarser where the listening works, and the player
        has to know which one is under his song.
        """
        if report.get("songsterr_error"):
            return [f"SYNC   Songsterr had nothing for this song — "
                    f"{report['songsterr_error']}",
                    "SYNC   nothing was stored. Shift+N/M to line it up by "
                    "hand, Shift+S to pin it there"]
        if report.get("wrong_bars"):
            offered = report.get("offered") or [report.get("bars", 0)]
            counts = " or ".join(str(n) for n in sorted(set(offered),
                                                        reverse=True))
            return [
                f"SYNC   Songsterr times {counts} bars and this tab has "
                f"{report['measures']} — your tab is a different revision of "
                f"it, or the repeats are written out differently",
                "SYNC   nothing was stored. Download the tab from Songsterr "
                "again, or line it up by hand with Shift+N/M and Shift+S"]
        if not report["readable"] or len(points) < 2:
            return [
                f"SYNC   Songsterr's bar map does not fit this recording — "
                f"{report['usable']} of {report['windows']} windows agreed "
                f"with it",
                "SYNC   nothing was stored. Is this the same recording the "
                "tab was made from?"]
        return [
            f"SYNC   from Songsterr's {report['bars']} bar times, lined up "
            f"here at {report['constant_s']:+.2f} s — {report['usable']} of "
            f"{report['windows']} windows agree to "
            f"{report['scatter_ms']:.0f} ms",
            "SYNC   coarser than listening (80–90 ms against 10) but it does "
            "not care that a song repeats itself. Shift+S refines it",
        ]

    def _auto_sync_line(self) -> str:
        """What the panel says while the listening is running."""
        if self._auto_sync_thread is None:
            return ""
        fraction, what = self._auto_sync_progress
        return f"SYNC   {what}… {fraction * 100:.0f} %"

    def _set_sync_point(self) -> None:
        """Remember that the recording is right HERE, and rebuild from all
        of them.

        The offset can only say where the recording starts, and for a while
        this took exactly two points -- which is a straight line. The player's
        own measurements say that is not enough: their offset runs -260 ms at
        the start, -1330 ms at 2:57 and back to -260 ms at 4:18, so the best
        straight line corrects by nothing at all and leaves 1.07 s standing in
        the middle. A band that played without a click does not follow a line,
        and every point they set is one more piece of it that can be followed.

        It is a repair and not a cure, and it has to be offered as one: the
        correction is a step at each point, not a curve, because the anchors
        are the only places the truth is known.
        """
        if self._mp3_player is None:
            self._say("No backing track — Shift+U to pick one")
            return
        # The offset IN FORCE here, not the stored one. On a song that has
        # never been synced they are the same number. On one that has, the
        # stored offset is a nudge sitting on top of the map -- and saving
        # the nudge alone wrote a point unrelated to anything being heard:
        # a song whose map reads +11.0 s in the solo got a point saying +0,
        # which does not mend a drifting tail, it destroys a working map.
        here = (self._playback_ms, self._sync_map().offset_at(self._playback_ms))
        points = [p for p in self._mp3_anchors()
                  if abs(p[0] - here[0]) >= MIN_SYNC_SPAN_MS]
        dropped = len(self._mp3_anchors()) - len(points)
        points.append(here)
        points.sort()
        setter = getattr(self._config, "set_mp3_anchors_for", None)
        if setter is None:
            return
        setter(self._song_key, points)
        # The nudge has been spent: it is inside the point now, and left
        # standing it would be applied a second time to the whole song --
        # including the parts the player had already got right.
        self._set_mp3_offset(0.0)
        self._config.save()
        self._describe_sync(replaced=dropped)
        self._mp3_loaded_build = None          # the plan changed

    def _toggle_vsync(self) -> None:
        """Hand the pictures to the panel on its beat, or back to the timer.

        A switch and not a decision, because the measurement says both
        halves are real and neither is free. The app hands over 60.0 pictures
        a second into a panel Windows calls 59, so one is periodically shown
        twice whatever we do about our own timing -- and vsync is the only
        thing that ends that. It also costs: SCALED fixes the drawing size,
        so the window letterboxes instead of relaying out, and a driver may
        refuse it altogether.

        Which of those matters more cannot be argued from here. It is one
        key so the same passage can be played both ways with `D` pressed
        after each, and the run log says which mode a reading came from.
        """
        dc = self._config.display
        dc.vsync = not dc.vsync
        self._config.save()
        self.forget_frame_measurements()
        self._say("Vsync on — the panel sets the pace (Z)" if dc.vsync
                  else "Vsync off — a software timer sets the pace (Z)")

    def _pacing_line(self) -> str:
        """What is timing the pictures, in the words the run log uses.

        The same two words as `pacing` and `vsync` in the log, deliberately:
        a screen that says one thing and a file that says another is how
        three measurements were taken of a switch nobody had turned on.
        """
        dc = self._config.display
        pace = "steady" if dc.steady_pace else "system timer"
        return (f"Pace: {pace} (Shift+Z)   |   vsync: {dc.vsync_outcome} (Z)")

    def _pacing_unusual(self) -> bool:
        """Whether either setting is away from its default, so it stands out.

        The default is the thing that has been measured for weeks; anything
        else is an experiment running, and an experiment the player has
        forgotten is running is worse than no experiment.

        Drawn in the streak colour rather than the HUD accent, because the
        accent IS the colour most of this panel is already in -- the player
        asked what "highlighted" was supposed to mean and answered the
        question at the same time: "the line is always blue".
        """
        dc = self._config.display
        return bool(dc.steady_pace or dc.vsync)

    def _toggle_steady_pace(self) -> None:
        """Wait for each frame precisely, or let the system time it.

        The other half of the pacing question, and the half that does not
        need the driver's permission: vsync is refused on this player's
        machines, and the jitter it would have cured is measurable without
        it -- 13.8 to 19.5 ms gaps against a 16.7 ms frame.

        A switch because it costs a busy wait. Only the last two
        milliseconds of each frame, about a tenth of one core, but a laptop
        on a battery is entitled to the choice.
        """
        dc = self._config.display
        dc.steady_pace = not dc.steady_pace
        self._config.save()
        self.forget_frame_measurements()
        self._say("Steady pace on — each frame is timed exactly (Shift+Z)"
                  if dc.steady_pace
                  else "Steady pace off — the system times the frames (Shift+Z)")

    def _beyond_sync_line(self) -> str:
        """Said only where the playhead is outside the measured span.

        The map extrapolates past its outermost point from the last
        segment's slope, which is worth having and is still a guess. How big
        a guess cannot be modelled honestly, so the size offered is the drift
        the song ALREADY showed where somebody was listening: a recording
        that wandered that far under measurement can wander that far again
        where nobody measured.

        Nothing is said inside the span, and nothing on a song with no points
        at all -- a single stored offset makes no claim to have been measured
        anywhere, so there is no edge to fall off.
        """
        covers = self._sync_map().covers()
        if covers is None:
            return ""
        first, last = covers
        if first <= self._playback_ms <= last:
            return ""
        side = "before" if self._playback_ms < first else "past"
        drift = self._sync_map().drift_seen_ms()
        return (f"SYNC   {side} the measured part "
                f"({_clock_text(first)}–{_clock_text(last)}) — the recording "
                f"is guessed here, and it drifted {drift:.0f} ms where it was "
                f"measured. Shift+S pins it.")

    def _describe_sync(self, replaced: int = 0) -> None:
        """The sync points and what they add up to, kept on screen.

        Not a note that expires: the player spends minutes moving between
        points, and a message that is gone by then leaves them guessing which
        press the next one will be.
        """
        points = self._mp3_anchors()
        rates = self._sync_map().rates()
        lines = [
            "  ".join(f"{_clock_text(at)} {_offset_text(off)}"
                      for at, off in points)
        ]
        if len(points) < 2:
            lines.append("SYNC 1 of 2 — now go somewhere else in the song, "
                         "line the recording up with Shift+N/M, Shift+S again")
        else:
            spans = "  ".join(f"{(1 / rate - 1) * 100:+.2f} %"
                              for rate in rates) or "nothing usable"
            # Where the points actually ARE. Beyond the outermost one the map
            # extrapolates, and a song whose points all sit in the first
            # minute is a song whose last four are a guess -- which the
            # panel has to say rather than leave to be discovered.
            covered = (f"measured {_clock_text(points[0][0])}"
                       f"–{_clock_text(points[-1][0])} of "
                       f"{_clock_text(self._timeline.duration_ms)}")
            lines.append(
                f"SYNC {len(points)} points, {len(rates)} section(s): "
                f"{spans}   ({covered})")
            lines.append("SYNC Shift+S adds one, Ctrl+Shift+S clears, "
                         "Ctrl+S measures again")
        if replaced:
            lines.append(f"replaced {replaced} point(s) closer than "
                         f"{MIN_SYNC_SPAN_MS / 1000:.0f} s to this one")
        self._sync_lines = ["SYNC   " + lines[0]] + lines[1:]

    def _clear_sync_rate(self) -> None:
        """Back to the recording as it was made."""
        self._sync_anchor = None
        self._sync_lines = []
        self._mp3_loaded_build = None
        for name, value in (("set_mp3_anchors_for", []),
                            ("set_mp3_rate_for", 1.0)):
            setter = getattr(self._config, name, None)
            if setter is not None:
                setter(self._song_key, value)
        self._config.save()
        self._say("Recording sync points cleared")

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
