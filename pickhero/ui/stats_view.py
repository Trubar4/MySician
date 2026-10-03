"""Several runs of one song, side by side.

*"Ich haette gerne Vergleiche gemacht von mehreren Durchgaengen des selben
Songs ... eine Liste aller alten Durchgaenge mit den 3 Prozentwerten inkl.
Zeit und Datum ... ich muss zwei Durchgaenge anklicken koennen, die dann
verglichen werden koennen."*

A run is already a picture: the strip along the bottom draws one every
evening and throws it away at the end of the song. `runs.py` keeps it, and
this is the same drawing asked of a stored one -- `strip.dot` places the
dots here exactly as it does down there, so the miniature in the list and
the miniature under the song are the same object at two sizes and cannot
disagree about where a note sits.

**A MODE of the playing screen, not a screen of its own.** A second screen
would be a second copy of the transport, the loop and the clock, and this
project has already paid for four readers of one plan. The overlay owns the
keyboard while it is up and hands everything back when it is not.
"""

from __future__ import annotations

from dataclasses import dataclass

import pygame

from pickhero import drill as drill_mod
from pickhero import runs as runs_mod
from pickhero.ui import strip
from pickhero.ui.keys import shift_held
from pickhero.ui.colors import STRING_COLORS, get_theme, get_theme_name, unsure

#: One row of the list is one bar plus the air around it.
ROW_GAP = 6
ROW_H = strip.STRIP_HEIGHT + ROW_GAP

#: The text beside each bar: when it was played, at what speed, and the three
#: numbers. A constant for the same reason the strip's column is one -- the
#: bar starts where it ends, and a column that grew with the digits in it
#: would move the whole song sideways the moment a score crossed 9 %.
LABEL_W = 300

PANEL_PAD = 22

#: How big a bar may get in the comparison. The floor is the strip the player
#: already reads; the ceiling is *"die Groesse der Standardansicht"*, which
#: with both runs stacked is half of what is left of the window.
SIZE_STEPS = 6

#: How much of the song a zoom step shows: all of it, then a half, a quarter,
#: and so on. At the whole song a bar of a real one puts about a pixel between
#: two notes, which says where the playing went wrong and nothing about WHICH
#: note -- and "which" is the question a comparison is opened to answer.
ZOOM_STEPS = 6

#: What one press of an arrow moves, as a share of what is on screen. A
#: quarter is a step the eye can follow across a picture that did not scroll
#: -- half a screen jumps past things and a tenth is a key held down.
SCROLL_FRACTION = 0.25

#: A bar line is drawn once the bars are at least this far apart, and numbered
#: once there is room for the digits. Below it they are a picket fence behind
#: the notes, which is the fault the board's own bar lines were thinned for.
BAR_TICK_PX = 26
BAR_NUMBER_PX = 58

#: However much room there is, a dot past this reads as a blob rather than
#: as a note, and two of them a bar apart stop looking like two notes.
MAX_DOT_PX = 22

#: A dot at least this tall carries its fret number. *"Koennen wir in der
#: hoechsten Zoom-Stufe nicht sogar alles anzeigen mit Bundnummern?"* --
#: measured rather than guessed: at this height a two-digit label is 12 px
#: of type, six to a digit, which is the narrowest a digit can be and still
#: have a stroke to read. Below it the number would be ink where a dot says
#: more, so the dot stays a dot.
FRET_DIGIT_PX = 11

#: How much of the room between two notes on one string a label may take.
#: Under one, because a label that filled the gap would touch the next one.
LABEL_SHARE = 0.9

#: How many evenings the trend shows, newest at the TOP. *"Die letzten 12,
#: neueste oben."* Twelve is about a fortnight of practising one song, which
#: is the span over which "is this bar getting better" has an answer -- and
#: it is as many rows as fit on a screen at a height a date can be read at.
TREND_RUNS = 12

#: A trend row, between the smallest height that still reads as a band and
#: the biggest that is not simply air.
TREND_ROW_MIN = 9
TREND_ROW_MAX = 26
TREND_ROW_GAP = 2

#: The date, the speed and the score beside each row. A constant, for the
#: reason the list's own column is one: the grid starts where it ends, and a
#: column that grew with the digits in it would move every bar sideways.
TREND_LABEL_W = 200

#: A bar is numbered in the ruler once its column is at least this wide; below
#: it the numbers are thinned rather than overprinted, which is the rule the
#: board's bar lines and the comparison's already follow.
TREND_NUMBER_PX = 30

#: At most this many bar surfaces are kept. Two in the comparison, a screenful
#: in the list; past that the oldest go, because a bar is redrawn in a
#: millisecond and a cache of every size ever asked for is a leak.
MAX_BARS = 32


@dataclass
class Entry:
    """One line of the list: a run, and what to say about it."""

    run: runs_mod.Run
    title: str
    detail: str
    #: Whether this run's verdicts describe the song now on screen. One
    #: recorded against another track, or before the tab was edited, still
    #: HAPPENED and is still listed -- but drawing it against these notes
    #: would put every dot in the wrong place.
    fits: bool = True
    #: Why not, in words, when it does not fit. Said on the row AND by the
    #: key that refuses to pick it: two sentences for one reason is how one
    #: of them comes to blame the wrong thing, which is what "another track"
    #: did to every song whose note count had moved.
    why: str = ""

    @property
    def comparable(self) -> bool:
        return self.fits and bool(self.run.notes)


def format_ms(ms: float) -> str:
    """m:ss, the unit the clock on the playing screen is read in."""
    total = max(0, int(ms // 1000))
    return f"{total // 60}:{total % 60:02d}"


def _when(run: runs_mod.Run) -> str:
    moment = run.when()
    if moment is None:
        return ""
    try:
        return moment.astimezone().strftime("%d %b %H:%M")
    except (ValueError, OSError):
        return moment.strftime("%d %b %H:%M")


def _score(run: runs_mod.Run) -> float:
    overall, _, _ = strip.split_percentages(run.counts())
    return -1.0 if overall is None else overall


def build(history: list[runs_mod.Run], note_count: int, track: int,
          sort: str = "date") -> list[Entry]:
    """The rows of the list: the two synthetic runs, then the real ones.

    The synthetics come first and stay first whatever the sort, because they
    are not runs and sorting them among the evenings would say they were.
    They are built from the runs that FIT -- a run of another track cannot
    contribute a verdict about a note it never described.
    """
    fitting = [r for r in history if r.fits(note_count, track)]
    rows: list[Entry] = []
    for synthetic in (runs_mod.best_ever(fitting),
                      runs_mod.common_errors(fitting)):
        if synthetic is not None:
            rows.append(Entry(run=synthetic,
                              title=("Best ever" if synthetic.kind == "best"
                                     else "Frequent errors"),
                              detail=synthetic.label))
    real = list(history)
    if sort == "score":
        real.sort(key=_score, reverse=True)
    else:
        real.sort(key=lambda r: r.started, reverse=True)
    for run in real:
        fits = run.fits(note_count, track)
        detail = f"{run.tempo_percent} %"
        judged = run.counts()["total"]
        if run.note_count and judged < run.note_count * 0.98:
            # A percentage is only readable next to what it is a percentage
            # OF. Two runs at 68 % are not the same evening when one of them
            # covered the whole song and the other gave up in the third bar
            # -- and side by side in a list, nothing else says so.
            detail += (f"   ·   judged {judged * 100 // run.note_count} % "
                       "of the notes")
        if run.label:
            # The run in progress says so. It is not in the file yet -- that
            # happens when the song is left -- and a line that looked like
            # every other would have the player wondering why there are two
            # of tonight.
            detail += f"   — {run.label}"
        why = ""
        if run.track != track:
            why = "played on another track"
        elif run.note_count != note_count:
            # The COUNTS, not a guess at the cause. This song had its tab
            # re-downloaded, or was played as a merge, or -- the commonest
            # and the one that used to read as "another track" -- it was
            # played as the other reading of Q. A row that names the wrong
            # cause sends the player to the wrong key.
            why = (f"{run.note_count} notes then, {note_count} now — "
                   "the tab has changed since")
        if why:
            detail += f"   — {why}"
        rows.append(Entry(run=run, title=_when(run) or "a run",
                          detail=detail, fits=fits, why=why))
    return rows


def visible_rows(area_h: int) -> int:
    """How many rows fit in the room the panel has for them."""
    return max(1, int(area_h) // ROW_H)


def scrolled(cursor: int, first: int, visible: int) -> int:
    """Keep the cursor on screen, moving the window as little as possible."""
    first = max(0, min(first, cursor))
    if cursor >= first + visible:
        first = cursor - visible + 1
    return max(0, first)


def size_ladder(floor: int, ceiling: int, steps: int = SIZE_STEPS) -> list[int]:
    """The heights `+`/`-` walk, from the strip's size to the screen's.

    Evenly spaced rather than doubling, because the useful part of the range
    is the middle -- a bar four times the strip already shows every note
    apart, and the steps after that only buy air.
    """
    ceiling = max(floor, int(ceiling))
    if steps < 2 or ceiling == floor:
        return [floor]
    span = ceiling - floor
    return [floor + round(span * i / (steps - 1)) for i in range(steps)]


def trend_columns(bars: int, width: int) -> list[tuple[int, int]]:
    """Where each bar's column starts and how wide it is.

    Tiled from rounded fractions of the width rather than from a fixed column
    size, so the columns meet exactly and the last one ends on the right edge:
    a grid of 150 bars at an integer width leaves a ragged margin that reads
    as a stretch of song nobody played.

    At least one pixel each, so a very long song still draws every bar --
    blank where a bar was never judged, and blank is an answer.
    """
    bars = max(0, int(bars))
    width = max(0, int(width))
    if not bars or not width:
        return []
    edges = [round(i * width / bars) for i in range(bars + 1)]
    return [(edges[i], max(1, edges[i + 1] - edges[i])) for i in range(bars)]


def _mix(a, b, share: float) -> tuple[int, int, int]:
    return tuple(int(round(a[i] + (b[i] - a[i]) * share)) for i in range(3))


def heat(share: float) -> tuple[int, int, int]:
    """A cell's colour, from the share of its notes that were not missed.

    Through the palette's own three feedback colours -- red at nothing, the
    close yellow halfway, green at everything -- so a cell reads the same way
    a note does and nothing new has to be learnt. A RAMP rather than bands,
    because a band needs a threshold and there is nothing to fit one against:
    the run history starts the day it ships.
    """
    theme = get_theme()
    share = max(0.0, min(1.0, float(share)))
    if share <= 0.5:
        return _mix(theme.feedback_miss, theme.feedback_close, share * 2.0)
    return _mix(theme.feedback_close, theme.feedback_hit, (share - 0.5) * 2.0)


def trend_row_label(run) -> tuple[str, str]:
    """What to write beside one trend row: the speed it was played at, and
    how it scored.

    **The speed is the half the grid cannot show.** A bar goes green at 70 %
    exactly as it does at 100, so a column reading better upwards says
    nothing about whether the passage got easier or the player simply slowed
    it down -- and the drill moves the speed by itself, which makes that the
    commonest case rather than a rare one. The run has carried
    `tempo_percent` since the day it was stored; nothing new is measured.

    **Said only where it is news.** Full speed is what a song opens at, so a
    "100 %" on every row is the wallpaper this screen was cut down to remove.
    """
    overall, _, _ = strip.split_percentages(run.counts())
    pace = int(getattr(run, "tempo_percent", 100) or 100)
    return ("" if pace == 100 else f"{pace}%",
            "" if overall is None else f"{overall:.0f}%")


def trend_row_height(rows: int, room: int) -> int:
    """How tall a trend row may be, given the room and how many there are."""
    rows = max(1, int(rows))
    each = int(room) // rows - TREND_ROW_GAP
    return max(TREND_ROW_MIN, min(TREND_ROW_MAX, each))


def _display_surface(w: int, h: int) -> pygame.Surface:
    """A blank surface already in the display's pixel format.

    Measured on a 1852x520 bar, which is what one arrow press rebuilds twice,
    over thirty repetitions each: `Surface()` is 0.18 ms, `convert()` on top
    of it is **4.20 ms**, and asking for the display's format up front is
    0.13 ms. The cost was never the drawing, it was the pixel format -- this
    project's own lesson from the tab page, one step earlier in the same work.

    **What that is worth per FRAME is not claimed**, and the reason is this
    file's own rule. The whole frame after an arrow press reads 12.8-13.0 ms
    over three runs here against one earlier reading of 14.8, and nothing
    inside the run-to-run spread is a finding. What is measured is the
    operation, thirty times over, and it is thirty times cheaper.

    Without a display there is no format to ask for, so it falls back to the
    plain surface: it still draws, it is simply slower to blit.
    """
    try:
        display = pygame.display.get_surface()
    except pygame.error:
        display = None
    if display is not None:
        return pygame.Surface((w, h), 0, display)
    return pygame.Surface((w, h))


class StatsOverlay:
    """The list of runs, and two of them stacked.

    Holds the playing screen rather than copying anything out of it: the
    timeline, the loop, the clock and the seek are all the screen's, and a
    comparison that had its own would be a second answer to where the song is.
    """

    def __init__(self, screen, font) -> None:
        self._screen = screen
        self._font = font
        self.open = False
        self.mode = "list"           # "list", "compare" or "trend"
        self.cursor = 0
        self.first = 0
        self.sort = "date"
        self.selected: list[int] = []
        self.size = 1                # index into the ladder, in compare mode
        # How much of the song the comparison shows, and from where. Zero is
        # the whole song, which is where it opens -- a comparison answers
        # "where did it go wrong" before it answers "which note", and the
        # first question needs the whole picture.
        self.zoom = 0
        self.view_from_ms = 0.0
        self._history: list[runs_mod.Run] = []
        self._entries: list[Entry] = []
        self._bars: dict[tuple, pygame.Surface] = {}
        self._positions: dict[tuple[int, int], list[tuple[int, int]]] = {}
        self._rows: list[tuple[int, pygame.Rect]] = []
        self._bar_rects: list[tuple[int, pygame.Rect]] = []
        self._drag_from: float | None = None
        self._drag_to: float | None = None
        # Where a run went wrong, and which of those the player is standing
        # in. Read off the run under the cursor, once, and kept until that
        # run changes -- the loop walks every note of the song.
        self._nests: list[tuple[int, int, int]] = []
        self._nest_of = ""
        self._nest_at = -1
        self._nest_note = ""
        self._note_bars: tuple[list[int], list] | None = None
        # The trend: one grid surface, built when the runs or the room change
        # and blitted after that. Rows times bars is a couple of thousand
        # fills, and this display has had to move a loop out of the frame
        # three times already.
        self._trend_grid: pygame.Surface | None = None
        self._trend_key: tuple | None = None
        self._trend_rows: list[tuple[int, pygame.Rect]] = []
        self._trend_cols: list[tuple[int, int]] = []
        self._trend_grid_rect: pygame.Rect | None = None
        self._trend_from_bar: int | None = None
        self._trend_to_bar: int | None = None

    # -- opening and closing ------------------------------------------------

    def toggle(self) -> None:
        if self.open:
            self.close()
        else:
            self.show()

    def show(self) -> None:
        """Read the history off the disk and put it up.

        Read on OPENING rather than kept up to date, because the file is
        written when a song is left and nothing can change it while one is
        being played. One read per press, never per frame.
        """
        self.open = True
        self.mode = "list"
        self.selected = []
        self.cursor = self.first = 0
        self._bars.clear()
        self._trend_grid = None
        self._trend_key = None
        self._nest_of = ""
        self._nest_at = -1
        self._nest_note = ""
        self._history = []
        path = getattr(self._screen, "_song_path", "")
        if path:
            try:
                self._history = runs_mod.load(path)
            except Exception:
                self._history = []
        self._rebuild()
        # The run in progress is the one the player just played, and it is
        # not in the file yet -- it is written when the song is left. Showing
        # it means the list answers "how did that go" straight away instead
        # of a song later.
        here = self._this_run()
        if here is not None:
            self._history.append(here)
            self._rebuild()

    def close(self) -> None:
        self.open = False
        self._drag_from = self._drag_to = None

    def _song(self):
        """The song these verdicts are about: the one as WRITTEN.

        Not `_timeline`, which on the easier reading (Q) holds a subset of
        it. A run is one character per written note whichever reading was
        played, so every geometry here -- the note count, the bars, the dots
        -- has to be the written song or a run of the other reading would be
        drawn against the wrong notes. One reader, because the drawing and
        the mouse must not disagree about where a note is.
        """
        return getattr(self._screen, "_written_timeline", None) or \
            self._song()

    def _this_run(self):
        """The run in progress, or None when the history already holds it.

        `unbanked_run` is the screen's own answer, so the list cannot offer a
        row `_write_run` has already stored -- which is what put the banked
        pass and the live one side by side, identical.
        """
        try:
            run = self._screen.unbanked_run()
        except Exception:
            return None
        if run is None:
            return None
        run.label = "this run, not saved yet"
        return run

    # -- what part of the song is on screen ---------------------------------

    def window(self) -> tuple[float, float]:
        """The stretch of song the bars show, as (from, to) in ms.

        Clamped to the song at both ends rather than allowed to run past it:
        a bar showing empty space beyond the last note would read as a stretch
        that was never played.
        """
        duration = max(1.0, self._song().duration_ms)
        span = duration / (2 ** max(0, self.zoom))
        start = max(0.0, min(self.view_from_ms, duration - span))
        return start, start + span

    def set_zoom(self, step: int) -> None:
        """Zoom, keeping the middle of the picture where it is.

        Zooming towards the left edge would walk the thing being looked at
        off the screen, and the player would have to scroll back to it after
        every press.
        """
        step = max(0, min(step, ZOOM_STEPS - 1))
        if step == self.zoom:
            return
        start, end = self.window()
        middle = (start + end) / 2.0
        self.zoom = step
        duration = max(1.0, self._song().duration_ms)
        span = duration / (2 ** self.zoom)
        self.view_from_ms = middle - span / 2.0
        self.view_from_ms = max(0.0, min(self.view_from_ms, duration - span))

    def scroll(self, direction: int) -> None:
        """Move along the song by a share of what is on screen."""
        start, end = self.window()
        self.view_from_ms = start + direction * (end - start) * SCROLL_FRACTION

    def _rebuild(self) -> None:
        # The track the SCREEN would record a run under, asked of the screen
        # -- `_track_index` is the primary of a merge and a merged part is
        # not that track's notes, so reading it here was a second answer to
        # the question `run_track_id` exists to answer. Two readers of one
        # question, and the one that decided was not the one that writes.
        track = getattr(self._screen, "run_track_id", None)
        track = int(track() if callable(track)
                    else getattr(self._screen, "_track_index", 0) or 0)
        self._entries = build(self._history, len(self._song().notes),
                              track, self.sort)
        self.cursor = max(0, min(self.cursor, len(self._entries) - 1))

    # -- keys and mouse -----------------------------------------------------

    def handle_event(self, event) -> bool:
        """Everything while the overlay is up. True when it was consumed."""
        if not self.open:
            return False
        if event.type == pygame.KEYDOWN:
            return self._handle_key(event)
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEMOTION,
                          pygame.MOUSEBUTTONUP):
            self._handle_mouse(event)
            return True
        return False

    def _handle_key(self, event) -> bool:
        key = event.key
        if key == pygame.K_d:
            # The key that opened it closes it. Without this, pressing it
            # again does nothing -- and a key that looks dead is the fault
            # this project keeps paying for.
            self.close()
            return True
        if key == pygame.K_ESCAPE:
            if self.mode in ("compare", "trend"):
                self.mode = "list"
            else:
                self.close()
            return True
        if key == pygame.K_t:
            # The trend is the one question the list cannot answer, and the
            # key that opens it closes it again -- from the comparison too,
            # because "is this bar getting better" is asked while looking at
            # a bar that went wrong.
            self.mode = "list" if self.mode == "trend" else "trend"
            if self.mode == "trend":
                # The cursor decides whose mistakes N walks, and the trend
                # shows only real evenings -- so a cursor left on "best ever"
                # would highlight no row at all and answer about a run that
                # is not in the picture.
                rows = [index for index, _ in self._trend_entries()]
                if rows and self.cursor not in rows:
                    self.cursor = rows[0]
            return True
        if key == pygame.K_n:
            # In both modes, because the question is the same one: a list
            # that says a run went badly and a comparison that draws it both
            # leave the player to FIND the passage by reading pixels.
            #
            # Shift tested first, because an `if` chain is read in order and
            # a shifted key placed after its unshifted twin is never reached.
            self._go_to_nest(drill=shift_held(event))
            return True
        if self.mode == "trend":
            # Only the cursor moves here. There is nothing to zoom: the grid
            # is the WHOLE song by construction -- a trend showing half of it
            # could not answer which bar is getting better.
            rows = [index for index, _ in self._trend_rows]
            if key in (pygame.K_DOWN, pygame.K_UP) and rows:
                at = rows.index(self.cursor) if self.cursor in rows else 0
                step = 1 if key == pygame.K_DOWN else -1
                self.cursor = rows[max(0, min(at + step, len(rows) - 1))]
            return True
        if self.mode == "list":
            if key == pygame.K_DOWN:
                self.cursor = min(self.cursor + 1, len(self._entries) - 1)
            elif key == pygame.K_UP:
                self.cursor = max(0, self.cursor - 1)
            elif key == pygame.K_s:
                self.sort = "score" if self.sort == "date" else "date"
                self.selected = []
                self._rebuild()
            elif key == pygame.K_SPACE:
                self._pick(self.cursor)
            elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._compare()
            return True
        # compare. Three axes, and +/- is the ZOOM -- *"+/- innerhalb Stats
        # Vergleich von 2 Durchgaengen zoomt das Griffbrett"*. It used to be
        # the bar's height, which made one key mean two things depending on
        # the view it was pressed in: that is the fault this project has now
        # paid for at the shifted shortcuts and at the scroll knob, and the
        # player read it exactly right. The height moved to UP/DOWN, which
        # keeps the setting he asked for without a modifier -- and Shift and
        # a German keyboard do not agree about what lives on the + key.
        if key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
            self.set_zoom(self.zoom + 1)
        elif key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self.set_zoom(self.zoom - 1)
        elif key == pygame.K_LEFT:
            self.scroll(-1)
        elif key == pygame.K_RIGHT:
            self.scroll(+1)
        elif key == pygame.K_UP:
            self.size = min(self.size + 1, SIZE_STEPS - 1)
        elif key == pygame.K_DOWN:
            self.size = max(0, self.size - 1)
        elif key == pygame.K_HOME:
            self.view_from_ms = 0.0
        return True

    def _pick(self, index: int) -> None:
        """Mark a run for comparison. Two at a time, oldest choice drops out."""
        if not (0 <= index < len(self._entries)):
            return
        entry = self._entries[index]
        if not entry.comparable:
            why = entry.why or "there is nothing in it"
            self._screen.say(f"That run cannot be drawn against this song: "
                             f"{why}")
            return
        if index in self.selected:
            self.selected.remove(index)
            return
        self.selected.append(index)
        if len(self.selected) > 2:
            self.selected.pop(0)

    def _compare(self) -> None:
        if len(self.selected) < 2 and self._entries:
            # One picked and the cursor on another is the second pick: that
            # is what pressing ENTER there means, and asking for one more
            # click would be a key that looks dead.
            self._pick(self.cursor)
        if len(self.selected) < 2:
            self._screen.say("Pick two runs to compare — SPACE or click")
            return
        self.mode = "compare"
        # The whole song first. "Where did it go wrong" is the question that
        # is asked before "which note", and only the whole picture answers it.
        self.zoom = 0
        self.view_from_ms = 0.0

    def _handle_mouse(self, event) -> None:
        if self.mode == "list":
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for index, rect in self._rows:
                    if rect.collidepoint(event.pos):
                        self.cursor = index
                        self._pick(index)
                        if len(self.selected) == 2:
                            self.mode = "compare"
                        return
            return
        if self.mode == "trend":
            self._trend_mouse(event)
            return
        self._compare_mouse(event)

    def _compare_mouse(self, event) -> None:
        """Right-drag over either bar to mark a passage to practise.

        The same gesture as on the strip, for the same reason: the left
        button is how a list is clicked and the right one is free, and a
        modifier wants a hand nobody has with a guitar on.
        """
        start, end = self.window()
        span = max(1.0, end - start)
        rect = next((r for _, r in self._bar_rects
                     if r.collidepoint(event.pos)), None)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3 and rect:
            at = start + strip.ms_for_x(event.pos[0] - rect.x, span, rect.width)
            self._drag_from = self._drag_to = at
        elif event.type == pygame.MOUSEMOTION and self._drag_from is not None:
            # Measured against the FIRST bar, not against whatever the mouse
            # happens to be over: the two are the same width and the same
            # song, and a drag that left the bar it started on would
            # otherwise stop tracking -- the mouse does not stop at an edge
            # the hand cannot feel.
            over = self._bar_rects[0][1] if self._bar_rects else rect
            if over is not None:
                self._drag_to = start + strip.ms_for_x(
                    event.pos[0] - over.x, span, over.width)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 3:
            self._take_passage()

    def _take_passage(self) -> None:
        """Set the loop, go there, and WAIT.

        *"Loop setzen, hinspringen, warten."* A comparison that started
        playing the moment the button came up would spend the first seconds
        of the passage with the fretting hand still on the mouse, and score
        them as missed.
        """
        first, last = self._drag_from, self._drag_to
        self._drag_from = self._drag_to = None
        if first is None or last is None or abs(last - first) < 200.0:
            return                  # a right click is also how a mouse is put down
        self.close()
        self._screen.take_passage(min(first, last), max(first, last))

    # -- the trend: is this bar getting better ------------------------------

    def _trend_entries(self) -> list[tuple[int, Entry]]:
        """The rows of the trend: real runs of this song, newest FIRST.

        In time order whatever the list is sorted by. "Evenings down" only
        means something chronologically, and a grid sorted by score would
        read as a trend that improved and then collapsed.

        The two pretend runs are left out: they are not evenings. "Best ever"
        is every evening's peak at once and "frequent errors" is a rule about
        all of them, so a row of either in a picture about time would be
        claiming a date it does not have.
        """
        rows = [(index, entry) for index, entry in enumerate(self._entries)
                if entry.run.kind == "run" and entry.comparable]
        rows.sort(key=lambda pair: pair[1].run.started, reverse=True)
        return rows[:TREND_RUNS]

    def _trend_surface(self, rows, cols, row_h: int) -> pygame.Surface:
        """The whole grid, built once and blitted after that.

        Twelve rows over a hundred and fifty bars is two thousand cells, and
        this display has had to move a loop out of the frame three times --
        the chord blocks, the note heads and the tab page. Keyed by the
        VERDICTS rather than by the runs, because the list is rebuilt whenever
        it is sorted and two runs with the same verdicts draw the same grid.
        """
        bars, _ = self._bars_of_notes()
        width = cols[-1][0] + cols[-1][1] if cols else 1
        height = len(rows) * (row_h + TREND_ROW_GAP)
        key = (tuple(entry.run.notes for _, entry in rows),
               width, row_h, len(cols), get_theme_name())
        if self._trend_key == key and self._trend_grid is not None:
            return self._trend_grid
        theme = get_theme()
        surface = _display_surface(max(1, width), max(1, height))
        surface.fill(theme.bg)
        for slot, (_, entry) in enumerate(rows):
            y = slot * (row_h + TREND_ROW_GAP)
            scored = runs_mod.bar_scores(entry.run.notes, bars)
            for bar, (x, w) in enumerate(cols):
                found = scored.get(bar)
                if found is None:
                    # A bar with nothing judged in it stays BLANK: nobody
                    # reached it, or it holds no notes of this track. A zero
                    # there would say the player got it wrong.
                    surface.fill(theme.lane_bg_even, (x, y, w, row_h))
                    continue
                right, judged, checked = found
                colour = heat(right / judged)
                if not checked:
                    # Nothing in this bar stood behind its verdict -- a strum
                    # credited without a window, or strikes the detector could
                    # make nothing of. It still counts; the colour says how
                    # much the app could check, which is the same drain the
                    # three views already apply to one note.
                    colour = unsure(colour)
                surface.fill(colour, (x, y, w, row_h))
        self._trend_key = key
        self._trend_grid = surface
        return surface

    def _trend_mouse(self, event) -> None:
        """Click a row to stand on it; right-drag the bars to practise them.

        The same two gestures as everywhere else in this overlay, over the
        axis this picture has: the grid's x is BARS and not time, so a drag
        marks the bars it crossed rather than a number of milliseconds -- and
        the bars are what a loop is set in anyway.
        """
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for index, rect in self._trend_rows:
                if rect.collidepoint(event.pos):
                    self.cursor = index
                    return
            return
        grid = self._trend_grid_rect
        if grid is None:
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            if grid.collidepoint(event.pos):
                at = self._bar_at(event.pos[0])
                self._trend_from_bar = self._trend_to_bar = at
        elif event.type == pygame.MOUSEMOTION and self._trend_from_bar is not None:
            self._trend_to_bar = self._bar_at(event.pos[0])
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 3:
            first, last = self._trend_from_bar, self._trend_to_bar
            self._trend_from_bar = self._trend_to_bar = None
            if first is None or last is None:
                return
            self._take_bars(min(first, last), max(first, last))

    def _bar_at(self, x: int) -> int:
        """Which bar the pointer is over, clamped to the song.

        Clamped rather than refused, because a drag does not stop at an edge
        the hand cannot feel -- the same reason the comparison measures its
        drag against the first bar whatever the mouse is over.
        """
        grid = self._trend_grid_rect
        count = max(1, len(self._trend_cols))
        if grid is None or grid.width <= 0:
            return 0
        share = (x - grid.x) / grid.width
        return max(0, min(count - 1, int(share * count)))

    def _take_bars(self, first: int, last: int) -> None:
        """Loop the bars that were dragged over, go there, and wait."""
        _, measures = self._bars_of_notes()
        if not measures or first >= len(measures):
            return
        last = min(last, len(measures) - 1)
        start, end, where = self._nest_span(first, last)
        self.close()
        self._screen.take_passage(start, end)
        self._screen.say(f"Loop set over {where} - SPACE plays it")

    # -- walking the places it went wrong -----------------------------------

    def _nest_source(self):
        """Whose errors N walks: the run being LOOKED at.

        In the list that is the row under the cursor -- including the two
        pretend runs, which is the point: "most frequent errors" is the one
        worth practising and it cannot say by itself where its clusters are.
        In a comparison it is the first of the two picked, because the
        comparison is drawn with that one on top and a key that walked the
        other would be answering about the bar the eye is not on.
        """
        if self.mode == "compare" and self.selected:
            index = self.selected[0]
        else:
            index = self.cursor
        if not (0 <= index < len(self._entries)):
            return None
        entry = self._entries[index]
        return entry.run if entry.fits else None

    def _bars_of_notes(self) -> tuple[list[int], list]:
        """Which bar each note is written in, and the bars themselves.

        The bar is the unit a player counts in and the unit a loop is set in,
        so the nests are grouped by the tab's OWN bars rather than by a number
        of milliseconds nobody fitted. Built once per song: it walks every
        note, and this is the loop that has been found growing with the song
        four times in this codebase.

        A tab that parsed without measure info falls back to one note per
        group -- less than the bars can do, and more than a key that silently
        does nothing.
        """
        if self._note_bars is None:
            notes = self._song().notes
            measures = list(getattr(self._song(), "measures", [])
                            or [])
            if measures:
                bars: list[int] = []
                at = 0
                for note in notes:
                    while (at + 1 < len(measures)
                           and note.timestamp_ms >= measures[at + 1].start_ms):
                        at += 1
                    bars.append(at)
            else:
                bars = list(range(len(notes)))
            self._note_bars = (bars, measures)
        return self._note_bars

    def _nest_span(self, first: int, last: int) -> tuple[float, float, str]:
        """A nest as (from ms, to ms, what to call it)."""
        _, measures = self._bars_of_notes()
        if measures:
            start, end = measures[first].start_ms, measures[last].end_ms
            low, high = measures[first].index + 1, measures[last].index + 1
            where = f"bar {low}" if low == high else f"bars {low}-{high}"
            return start, end, where
        notes = self._song().notes
        start = notes[first].timestamp_ms
        end = notes[last].timestamp_ms + notes[last].duration_ms
        return start, end, f"{format_ms(start)}-{format_ms(end)}"

    def _how_it_has_gone(self, first: int, last: int) -> str:
        """How often these bars have been played clean, over the stored runs.

        **The one thing the ladder could not say about itself.** `CLEAN_PASSES`
        is two and nothing had been fitted against -- the run history starts
        the day it ships -- so the demand sat there with no way to tell whether
        two clean passes at 70 % is a formality or an evening. The evenings are
        on disk now, judged per note, and the drill's own rule run over them
        answers it before a note is played.

        It MEASURES rather than decides: the constant is untouched, and what
        changes is that the number is beside it.

        Empty under two runs that reached the passage, because one run is not
        a rate -- the same floor "frequent errors" refuses below.
        """
        bars, _ = self._bars_of_notes()
        inside = [i for i, bar in enumerate(bars) if first <= bar <= last]
        if not inside:
            return ""
        clean, reached = drill_mod.clean_runs(
            "".join(entry.run.notes[i] for i in inside
                    if i < len(entry.run.notes))
            for entry in self._entries
            if entry.run.kind == "run" and entry.comparable)
        if reached < 2:
            return ""
        return f"clean in {clean} of {reached} runs"

    def _go_to_nest(self, drill: bool = False) -> None:
        """Set the loop over the next place this run went wrong, and wait.

        The same landing as a right-drag: loop, jump, hands free. The overlay
        STAYS UP, because walking a list is what this key is for and closing
        it would make every step cost a Shift+D -- and because the bar under
        the comparison then shows where the loop landed.
        """
        run = self._nest_source()
        if run is None:
            self._nest_note = "Nothing here to walk through"
            return
        if run.notes != self._nest_of:
            bars, _ = self._bars_of_notes()
            self._nests = runs_mod.error_nests(run.notes, bars)
            self._nest_of = run.notes
            self._nest_at = -1
        if not self._nests:
            self._nest_note = "Nothing went wrong in that run"
            return
        self._nest_at = (self._nest_at + 1) % len(self._nests)
        first, last, wrong = self._nests[self._nest_at]
        start, end, where = self._nest_span(first, last)
        if self.mode == "compare":
            # Bring it into the picture. Zoomed in, a nest three bars long is
            # off screen more often than not, and a key whose effect is a
            # two-pixel playhead nobody can find is a key that looks dead.
            # Only the position moves: the zoom is the player's.
            seen = self.window()
            self.view_from_ms = (start + end) / 2.0 - (seen[1] - seen[0]) / 2.0
        # How this passage has gone before, said at the moment the player is
        # deciding whether to work on it -- and, for the drill, the only thing
        # that says whether two clean passes is a formality here.
        seen = self._how_it_has_gone(first, last)
        seen = f" ({seen})" if seen else ""
        if drill:
            # The same passage, walked up the ladder instead of merely
            # looped. `start_drill` sets the loop itself, through the very
            # `take_passage` the plain key calls, so there is one answer to
            # what marking a passage means.
            self._screen.start_drill(start, end, where)
            self._nest_note = (f"{self._nest_at + 1} of {len(self._nests)}: "
                               f"drilling {where}{seen} - SPACE plays it")
        else:
            self._screen.take_passage(start, end)
            self._nest_note = (f"{self._nest_at + 1} of {len(self._nests)}: "
                               f"{where}, {wrong} note{'' if wrong == 1 else 's'} "
                               f"wrong{seen} - SPACE plays the loop")
        self._screen.say(self._nest_note)

    # -- the bars -----------------------------------------------------------

    def _note_positions(self, w: int, h: int, start: float,
                        end: float) -> list[tuple[int, int] | None]:
        """Where each note sits in a bar this size, over this stretch of song.

        `None` for a note outside the window: clamping one instead would pile
        every note before the view onto the left edge, which reads as a chord
        nobody played. Built once per size and window and kept -- a four-minute
        song is a couple of thousand notes, and this is the loop that has been
        found growing with the song four times in this codebase.
        """
        key = (w, h, round(start), round(end))
        found = self._positions.get(key)
        if found is None:
            timeline = self._song()
            span = max(1.0, end - start)
            spread = strip.row_spread_for(h)
            found = []
            for note in timeline.notes:
                at = note.timestamp_ms
                if at < start or at > end:
                    found.append(None)
                    continue
                found.append((int(strip.x_for_ms(at - start, span, w)),
                              int(strip.row_y(note.string, h, spread))))
            if len(self._positions) >= MAX_BARS:
                self._positions.clear()
            self._positions[key] = found
        return found

    _COLOUR = {runs_mod.HIT: "feedback_hit", runs_mod.CLOSE: "feedback_close",
               runs_mod.MISS: "feedback_miss"}

    def row_gap(self, spots) -> float:
        """How far apart two notes on ONE string sit here, in pixels.

        The tenth percentile, so a couple of freak-close pairs cannot speak
        for the rest -- the same rule and the same reason as
        `_spacing_percentile` on the scrolling board. One implementation,
        because the dot size and the fret label are two questions with one
        answer: a label is only safe in the room a dot was sized against.

        `inf` where nothing on any string has a neighbour to measure.
        """
        gaps: list[int] = []
        last: dict[int, int] = {}
        for note, spot in zip(self._song().notes, spots):
            if spot is None:
                continue
            was = last.get(note.string)
            if was is not None and spot[0] > was:
                gaps.append(spot[0] - was)
            last[note.string] = spot[0]
        if not gaps:
            return float("inf")
        gaps.sort()
        return float(gaps[max(0, int(len(gaps) * 0.10) - 1)])

    def dot_size(self, spots, h: int) -> int:
        """How big a dot may be drawn, measured rather than fitted.

        Two limits, and until the comparison could zoom only one of them was
        ever binding. VERTICALLY a dot must not close the gap between two
        string rows. HORIZONTALLY it must not close the gap between two notes
        ON THE SAME ROW -- which is the only place two dots can collide, since
        a row is a string.

        Measured off the notes actually in the window, at the tenth
        percentile so a couple of freak-close pairs cannot set the size for
        everything else. That is the same rule and the same reason as
        `_spacing_percentile` on the scrolling board. Zoomed out on a real
        song the horizontal limit binds and the dot stays small; zoomed in
        there is room and it grows, which is the whole point of zooming.
        """
        pitch = h * strip.row_spread_for(h) / strip.STRINGS
        room = self.row_gap(spots)
        # Zoomed all the way out the horizontal limit is a pixel or two, and
        # a dot that small stops reading as a note at all. There it is allowed
        # to touch its neighbour, because what the whole song says is DENSITY
        # and colour rather than which note is which -- the same argument the
        # strip's own `DOT_PX` is chosen on. The vertical limit still binds,
        # so the six rows never merge into one ribbon.
        room = max(room * 0.6, strip.DOT_PX * 2)
        return int(max(strip.DOT_PX, min(pitch * 0.5, room, MAX_DOT_PX)))

    def bar(self, run: runs_mod.Run, w: int, h: int,
            window: tuple[float, float] | None = None) -> pygame.Surface:
        """One run as a bar: the song in string colours, the verdicts over it.

        The same two layers the strip draws, in the same order and with the
        same arithmetic. A drained verdict keeps its hue and loses its
        conviction here too -- a comparison in which a strum-credited green
        looked like a green that was heard would be the thing the player
        already said the score does too much of.
        """
        # Keyed by the VERDICTS, not by the object. The synthetic runs are
        # rebuilt whenever the list is, so an address here would be handed
        # back for a different run the moment CPython reused one -- and two
        # runs with the same verdicts draw the same bar anyway.
        if window is None:
            window = (0.0, max(1.0, self._song().duration_ms))
        start, end = window
        key = (run.notes, w, h, round(start), round(end), get_theme_name())
        found = self._bars.get(key)
        if found is not None:
            return found
        theme = get_theme()
        surface = _display_surface(w, h)
        surface.fill(theme.lane_bg_even)
        notes = self._song().notes
        spots = self._note_positions(w, h, start, end)
        spread = strip.row_spread_for(h)
        if h >= 2 * strip.STRIP_HEIGHT:
            # Once the rows are far enough apart to need it, the six strings
            # are drawn under the dots. Without them a big bar is six rows of
            # floating colour: the same reason the board draws a fretboard
            # rather than a table of rows.
            for string in range(1, strip.STRINGS + 1):
                y = int(strip.row_y(string, h, spread))
                surface.fill(theme.lane_line, (0, y, w, 1))
        dot = self.dot_size(spots, h)
        # What ended up UNDER each dot, so a fret number drawn over it can
        # be given an ink that reads on it. The two layers disagree all the
        # time -- a bright verdict wants black where its string wanted white
        # -- so it has to be the colour actually painted, not the one the
        # note was born with.
        under: dict[tuple[int, int], tuple] = {}
        for i, note in enumerate(notes):
            spot = spots[i]
            if spot is None:
                continue
            if spot in under:
                continue
            under[spot] = STRING_COLORS[note.string]
            surface.fill(STRING_COLORS[note.string],
                         (spot[0], spot[1] - dot // 2, dot, dot))
        for i, char in enumerate(run.notes):
            if i >= len(spots) or spots[i] is None:
                continue
            name = self._COLOUR.get(char.lower())
            if name is None:
                continue
            colour = getattr(theme, name)
            if char.isupper():
                colour = unsure(colour)
            x, y = spots[i]
            under[(x, y)] = colour
            surface.fill(colour, (x, y - dot // 2, dot, dot))
        self._label_frets(surface, spots, dot, self.row_gap(spots), under)
        if len(self._bars) >= MAX_BARS:
            self._bars.clear()
        self._bars[key] = surface
        return surface

    @staticmethod
    def _ink(colour) -> tuple[int, int, int]:
        """Black or white on this dot, whichever can be read on it.

        The dot under a fret number is a verdict, and the verdicts run from
        a bright green to a drained red -- one fixed ink would disappear on
        half of them. Luminance decides, the way it does for any label on a
        colour that is not the designer's to choose.
        """
        r, g, b = colour[:3]
        return (16, 16, 16) if (r * 299 + g * 587 + b * 114) / 1000 > 140 \
            else (240, 240, 240)

    def _label_frets(self, surface: pygame.Surface, spots, dot: int,
                     gap: float, under: dict) -> None:
        """The fret number inside each dot, once there is room for it.

        *"In der hoechsten Zoom-Stufe alles anzeigen mit Bundnummern."* Two
        conditions, and both are measured rather than felt: the dot has to be
        `FRET_DIGIT_PX` tall for a digit to have a stroke, and the label has
        to fit the room between two notes on one string -- which is the same
        `row_gap` the dot was sized against, so a number can never reach the
        note beside it.

        A label is rendered once per VALUE, not once per note: a song has
        two dozen frets in it and a couple of thousand notes.
        """
        if dot < FRET_DIGIT_PX:
            return
        font = self._font("arial", dot)
        cut = gap * LABEL_SHARE
        made: dict[tuple[int, tuple], pygame.Surface | None] = {}
        done: set[tuple[int, int]] = set()
        for note, spot in zip(self._song().notes, spots):
            if spot is None or spot in done:
                continue
            done.add(spot)
            key = (note.fret, self._ink(under.get(spot, (0, 0, 0))))
            if key not in made:
                shape = font.render(str(note.fret), True, key[1])
                made[key] = shape if shape.get_width() <= cut else None
            shape = made[key]
            if shape is None:
                continue
            surface.blit(shape, (spot[0] + dot // 2 - shape.get_width() // 2,
                                 spot[1] - shape.get_height() // 2))

    # -- drawing ------------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        if not self.open:
            return
        theme = get_theme()
        w, h = surface.get_size()
        # Opaque, not a scrim. The song underneath is the same six rows of
        # coloured dots these bars are made of, and 5 % of it showing through
        # reads as a seventh run nobody can account for.
        surface.fill(theme.bg)
        panel = pygame.Rect(PANEL_PAD, PANEL_PAD,
                            w - 2 * PANEL_PAD, h - 2 * PANEL_PAD)
        if self.mode == "compare":
            self._draw_compare(surface, panel)
        elif self.mode == "trend":
            self._draw_trend(surface, panel)
        else:
            self._draw_list(surface, panel)

    @staticmethod
    def fit(font, text: str, width: int) -> str:
        """As much of `text` as fits, with an ellipsis where it was cut.

        A line drawn wider than the space it was given runs into whatever is
        beside it -- which on the first build of this list was the detail of
        a synthetic run running under its own percentages. The footer and the
        sync panel have each been fixed for this once already.
        """
        if width <= 0 or font.size(text)[0] <= width:
            return text
        cut = text
        while cut and font.size(cut + "…")[0] > width:
            cut = cut[:-1]
        return cut + "…"

    def _numbers(self, run: runs_mod.Run) -> str:
        """What to say about a run in one line.

        The synthetic "frequent errors" run has no percentage worth printing:
        every note in it is a mistake by construction, so its accuracy is 0 %
        and its timing is undefined -- three numbers all saying the same
        nothing. What it HAS is a count, and that is the thing to act on.
        """
        if run.kind == "errors":
            return f"{run.notes.count(runs_mod.MISS)} notes"
        return self._percent_line(run)

    def _percent_line(self, run: runs_mod.Run) -> str:
        overall, timing, right = strip.split_percentages(run.counts())
        if overall is None:
            return "nothing judged"
        def say(value):
            return "—" if value is None else f"{value:.0f}%"
        return f"{overall:.0f}%   T {say(timing)}   R {say(right)}"

    def _draw_list(self, surface: pygame.Surface, panel: pygame.Rect) -> None:
        theme = get_theme()
        title = self._font("consolas", 22)
        small = self._font("arial", 13)
        tiny = self._font("arial", 12)
        # The panel is drawn round what there IS. A frame enclosing eight
        # rows and six hundred pixels of nothing says the list is missing
        # something rather than that the player has played eight times.
        # Exactly what the header and the footer take, so the room left over
        # is a whole number of rows. A guess one row short is a run missing
        # from the bottom of the list with nothing to say it is there.
        # Two small lines at the foot: the keys, and what the last N
        # press landed on. The room for the second is reserved whether
        # or not there is one, so pressing N cannot resize the panel
        # the list is being read in.
        chrome = title.get_height() + tiny.get_height() * 3 + 48
        wanted = chrome + max(1, len(self._entries)) * ROW_H
        panel = pygame.Rect(panel.x, panel.y, panel.width,
                            max(140, min(panel.height, wanted)))
        pygame.draw.rect(surface, theme.lane_line, panel, 1)
        x = panel.x + 12
        y = panel.y + 10
        meta = self._song().metadata
        name = meta.title or "this song"
        if meta.artist:
            name = f"{meta.artist} — {name}"
        surface.blit(title.render(f"Runs — {name}", True, theme.hud_text),
                     (x, y))
        y += title.get_height() + 2
        sort_says = ("newest first (S sorts by score)" if self.sort == "date"
                     else "best first (S sorts by date)")
        surface.blit(tiny.render(sort_says, True, theme.hud_accent), (x, y))
        y += tiny.get_height() + 2
        # Why the second pretend run is not there. A row that is simply
        # missing cannot be told from a feature that does not work, and the
        # player asked the question outright -- *"Wie viele Laeufe brauche
        # ich um haeufige Fehler zu sehen?"* The room is taken either way,
        # so the list does not jump the moment the row appears.
        missing = ("" if any(e.run.kind == "errors" for e in self._entries)
                   else runs_mod.why_no_errors(
                       [e.run for e in self._entries
                        if e.run.kind == "run" and e.fits]))
        if missing:
            surface.blit(tiny.render(self.fit(tiny, missing,
                                              panel.right - 12 - x),
                                     True, theme.hud_text), (x, y))
        y += tiny.get_height() + 6

        bottom = panel.bottom - 28
        rows = visible_rows(bottom - y)
        self.first = scrolled(self.cursor, self.first, rows)
        self._rows = []
        for index in range(self.first, min(len(self._entries),
                                           self.first + rows)):
            entry = self._entries[index]
            row = pygame.Rect(x, y, panel.width - 24, strip.STRIP_HEIGHT)
            self._rows.append((index, row))
            if index == self.cursor:
                pygame.draw.rect(surface, theme.hud_accent,
                                 row.inflate(6, 4), 1)
            mark = ""
            if index in self.selected:
                mark = "AB"[self.selected.index(index)] + "  "
            head = theme.hud_accent if entry.run.kind != "run" else theme.hud_text
            # Two lines in the column: what it is, with its numbers
            # right-aligned beside it, and what it is made of underneath.
            # The numbers are on the TOP line rather than in a column of
            # their own, so the detail gets the full width to be cut to.
            numbers = self._numbers(entry.run) if entry.comparable else ""
            wide = LABEL_W - 16
            n_w = small.size(numbers)[0] if numbers else 0
            surface.blit(small.render(
                self.fit(small, mark + entry.title, wide - n_w - 14), True,
                head), (x, y))
            if numbers:
                surface.blit(small.render(numbers, True, theme.hud_text),
                             (x + wide - n_w, y))
            surface.blit(tiny.render(
                self.fit(tiny, entry.detail, wide), True, theme.hud_text),
                (x, y + small.get_height() + 1))
            bar_x = x + LABEL_W
            bar_w = row.right - bar_x
            if bar_w > 40 and entry.comparable:
                # Always the whole song in the list: a row is how the runs
                # are TOLD APART, and two rows showing different stretches
                # would be a comparison nobody asked for.
                surface.blit(self.bar(entry.run, bar_w, strip.STRIP_HEIGHT),
                             (bar_x, y))
            y += ROW_H
        if not self._entries:
            surface.blit(small.render(
                "No runs recorded yet — play the song and leave it",
                True, theme.hud_text), (x, y))
        foot = ("SPACE or click picks two · ENTER compares · S sorts · "
                "T is the trend over evenings · N loops the next mistake · "
                "Shift+N drills it · ESC closes")
        self._blit_foot(surface, panel, x, panel.right - 12 - x, foot)

    def _draw_trend(self, surface: pygame.Surface,
                    panel: pygame.Rect) -> None:
        """Bars across, evenings down, newest at the top.

        *"Wird Takt 42 besser?"* -- which neither of the two pretend runs can
        answer: "best ever" keeps only the peak of every evening and
        "frequent errors" only what is still wrong today. Read a COLUMN here
        and the answer is the picture: red at the bottom going green towards
        the top is a passage that was learnt, and a column red all the way up
        is the one to drill.
        """
        theme = get_theme()
        title = self._font("consolas", 20)
        small = self._font("arial", 13)
        tiny = self._font("arial", 12)
        rows = self._trend_entries()
        x = panel.x + 12
        width = panel.width - 24
        y = panel.y + 10
        meta = self._song().metadata
        head = f"Trend — {meta.title or 'this song'}"
        surface.blit(title.render(head, True, theme.hud_text), (x, y))
        y += title.get_height() + 4
        _, measures = self._bars_of_notes()
        if not rows or not measures:
            # Two different nothings, said apart. A tab with no bar lines has
            # no columns to draw and never will; a song nobody has finished
            # has no row yet. A single sentence for both would send the
            # player looking in the wrong place.
            #
            # ONE evening is not refused: a single row says which bars went
            # wrong tonight, which is worth having on its own, and the trend
            # fills in underneath it as the evenings accumulate. Refusing it
            # would make the picture appear out of nowhere on the second run.
            why = ("This tab has no bar lines, so there are no columns to "
                   "compare" if not measures else
                   "No run of this track is saved yet — play it and leave it")
            surface.blit(small.render(self.fit(small, why, width), True,
                                      theme.hud_text), (x, y))
            pygame.draw.rect(surface, theme.lane_line,
                             pygame.Rect(panel.x, panel.y, panel.width,
                                         (y - panel.y) + 60), 1)
            self._trend_rows = []
            self._trend_grid_rect = None
            self._blit_foot(surface, pygame.Rect(panel.x, panel.y, panel.width,
                                                 (y - panel.y) + 60),
                            x, width, "T or ESC goes back to the list")
            return
        surface.blit(tiny.render(
            self.fit(tiny, "each column is one bar of the song, newest "
                           "evening on top — read a column downwards", width),
            True, theme.hud_accent), (x, y))
        y += tiny.get_height() + 8

        grid_x = x + TREND_LABEL_W
        grid_w = max(1, panel.right - 12 - grid_x)
        ruler_h = tiny.get_height() + 3
        foot_h = tiny.get_height() * 2 + 14
        room = panel.bottom - y - ruler_h - foot_h
        row_h = trend_row_height(len(rows), room)
        self._trend_cols = trend_columns(len(measures), grid_w)
        grid = self._trend_surface(rows, self._trend_cols, row_h)

        # The ruler sits ABOVE the grid, because that is where the eye is
        # when it has found a column worth reading. Numbered as densely as
        # the digits allow and no denser -- the fault the board's own bar
        # lines and the comparison's were both thinned for.
        self._draw_trend_ruler(surface, grid_x, y, tiny)
        y += ruler_h

        label_font = self._font("arial", max(9, min(13, row_h - 4)))
        self._trend_rows = []
        for slot, (index, entry) in enumerate(rows):
            row_y = y + slot * (row_h + TREND_ROW_GAP)
            rect = pygame.Rect(x, row_y, width, row_h)
            self._trend_rows.append((index, rect))
            colour = theme.hud_accent if index == self.cursor else theme.hud_text
            text_y = row_y + max(0, (row_h - label_font.get_height()) // 2)
            pace, score = trend_row_label(entry.run)
            # Right to left from the grid's edge: the score, then the speed it
            # was played at. The speed is in the STREAK colour rather than the
            # row's own, because it is a caveat about the colours to its right
            # and not another reading of them.
            right = grid_x - 14
            if score:
                right -= label_font.size(score)[0]
                surface.blit(label_font.render(score, True, colour),
                             (right, text_y))
            if pace:
                right -= label_font.size(pace)[0] + 8
                surface.blit(
                    label_font.render(pace, True, theme.feedback_streak),
                    (right, text_y))
            surface.blit(label_font.render(
                self.fit(label_font, entry.title, max(10, right - 8 - x)),
                True, colour), (x, text_y))
        surface.blit(grid, (grid_x, y),
                     (0, 0, grid_w, len(rows) * (row_h + TREND_ROW_GAP)))
        for index, rect in self._trend_rows:
            if index == self.cursor:
                # Drawn AFTER the grid, or the blit covers it. N acts on this
                # row, so which one it is has to be visible at a glance --
                # the same outline the list draws round its own cursor.
                pygame.draw.rect(surface, theme.hud_accent,
                                 rect.inflate(6, 4), 1)
        grid_rect = pygame.Rect(grid_x, y, grid_w,
                                len(rows) * (row_h + TREND_ROW_GAP)
                                - TREND_ROW_GAP)
        self._trend_grid_rect = grid_rect
        pygame.draw.rect(surface, theme.lane_line, grid_rect, 1)
        self._draw_trend_mark(surface, grid_rect)
        y = grid_rect.bottom + 6

        # Round what is in it, not round the room there is. Same rule as the
        # list and the comparison, both of which were fixed for it.
        used = (y - panel.y) + foot_h
        panel = pygame.Rect(panel.x, panel.y, panel.width,
                            max(140, min(panel.height, used)))
        pygame.draw.rect(surface, theme.lane_line, panel, 1)
        foot = (f"{len(rows)} run{'' if len(rows) == 1 else 's'} over "
                f"{len(measures)} bars · click a row then N to loop its "
                "mistakes · right-drag the bars to practise them · "
                "T or ESC back to the list")
        self._blit_foot(surface, panel, x, width, foot)

    def _draw_trend_ruler(self, surface: pygame.Surface, grid_x: int,
                          y: int, font) -> None:
        """Bar numbers over the grid, thinned until they fit.

        Numbered from 1, the way a player counts and the way every other bar
        number in this app is written -- a column nobody can name is a column
        nobody can loop.
        """
        theme = get_theme()
        cols = self._trend_cols
        if not cols:
            return
        each = cols[0][1]
        every = 1 if each >= TREND_NUMBER_PX else \
            max(1, -(-TREND_NUMBER_PX // max(1, each)))
        for bar, (x, _) in enumerate(cols):
            if bar % every:
                continue
            drawn = font.render(str(bar + 1), True, theme.hud_text)
            surface.blit(drawn, (grid_x + x + 1, y))

    def _draw_trend_mark(self, surface: pygame.Surface,
                         rect: pygame.Rect) -> None:
        """The bars being dragged over, and the loop in force.

        Without the second, the key that sets a loop shows only where the
        playhead landed -- and how far the passage REACHES is the thing being
        chosen. The same pair the comparison draws, over bars instead of ms.
        """
        cols = self._trend_cols
        if not cols:
            return
        theme = get_theme()
        first, last = self._trend_from_bar, self._trend_to_bar
        if first is None and self._screen._loop_enabled:
            _, measures = self._bars_of_notes()
            inside = [m.index for m in measures
                      if self._screen._loop_start_ms <= m.start_ms
                      < self._screen._loop_end_ms]
            if inside:
                first, last = min(inside), max(inside)
        if first is None or last is None:
            return
        low, high = min(first, last), max(first, last)
        if low >= len(cols):
            return
        high = min(high, len(cols) - 1)
        x0 = cols[low][0]
        x1 = cols[high][0] + cols[high][1]
        shade = pygame.Surface((max(1, x1 - x0), rect.height), pygame.SRCALPHA)
        shade.fill(theme.loop_region)
        surface.blit(shade, (rect.x + x0, rect.y))

    def _draw_compare(self, surface: pygame.Surface,
                      panel: pygame.Rect) -> None:
        theme = get_theme()
        title = self._font("consolas", 20)
        small = self._font("arial", 13)
        tiny = self._font("arial", 12)
        picked = [self._entries[i] for i in self.selected
                  if 0 <= i < len(self._entries)]
        x = panel.x + 12
        width = panel.width - 24
        top = panel.y + 10
        # Two lines: the keys, and room for what N last landed on --
        # reserved either way, so pressing it cannot resize the bars.
        foot_h = tiny.get_height() * 2 + 14
        room = panel.bottom - top - foot_h
        label_h = small.get_height() + tiny.get_height() + 6
        ladder = size_ladder(strip.STRIP_HEIGHT,
                             max(strip.STRIP_HEIGHT,
                                 room // max(1, len(picked)) - label_h - 10))
        self.size = max(0, min(self.size, len(ladder) - 1))
        bar_h = ladder[self.size]
        view = self.window()
        # The frame is drawn round what is in it, not round the room there
        # is: a border with four hundred pixels of nothing under the second
        # bar says something failed to draw. Same rule as the list.
        used = (top - panel.y) + len(picked) * (label_h + bar_h + 14) + foot_h
        panel = pygame.Rect(panel.x, panel.y, panel.width,
                            max(140, min(panel.height, used)))
        pygame.draw.rect(surface, theme.lane_line, panel, 1)
        self._bar_rects = []
        y = top
        for slot, entry in enumerate(picked):
            head = f"{'AB'[slot]}   {entry.title}"
            surface.blit(title.render(head, True, theme.hud_accent), (x, y))
            numbers = self._numbers(entry.run)
            surface.blit(small.render(numbers, True, theme.hud_text),
                         (x + title.size(head)[0] + 20, y + 4))
            y += title.get_height() + 2
            surface.blit(tiny.render(entry.detail, True, theme.hud_text),
                         (x, y))
            y += tiny.get_height() + 4
            rect = pygame.Rect(x, y, width, bar_h)
            surface.blit(self.bar(entry.run, width, bar_h, view), rect.topleft)
            self._draw_bars_of_music(surface, rect, view)
            self._draw_marks(surface, rect, view)
            pygame.draw.rect(surface, theme.lane_line, rect, 1)
            self._bar_rects.append((slot, rect))
            y += bar_h + 14
        seen = (f"{format_ms(view[0])}–{format_ms(view[1])}"
                if self.zoom else "the whole song")
        foot = (f"+/- zoom ({self.zoom + 1}/{ZOOM_STEPS}) · "
                f"UP/DOWN bar size ({self.size + 1}/{len(ladder)}) · "
                f"LEFT/RIGHT move — showing {seen} · "
                "right-drag or N marks a passage · Shift+N drills it · "
                "T is the trend · ESC back")
        self._blit_foot(surface, panel, x, width, foot)

    def _blit_foot(self, surface: pygame.Surface, panel: pygame.Rect,
                   x: int, width: int, foot: str) -> None:
        """The keys, and above them what the last N press landed on.

        The overlay covers the HUD, so the screen's own status note is behind
        the panel while this is up -- a sentence nobody can see is the fault
        this project has now shipped four times. It is said here as well.
        """
        tiny = self._font("arial", 12)
        line = panel.bottom - tiny.get_height() - 8
        surface.blit(tiny.render(self.fit(tiny, foot, width), True,
                                 get_theme().hud_accent), (x, line))
        if self._nest_note:
            surface.blit(
                tiny.render(self.fit(tiny, self._nest_note, width), True,
                            get_theme().hud_text),
                (x, line - tiny.get_height() - 2))

    def _draw_bars_of_music(self, surface: pygame.Surface, rect: pygame.Rect,
                            view: tuple[float, float]) -> None:
        """Bar lines and their numbers, once there is room for them.

        Without them a zoomed-in picture says a note went wrong and gives the
        player no way to name the place -- and naming it is what turns "I keep
        getting this wrong" into a loop and a practice session. Drawn only
        where the spacing allows: closer than `BAR_TICK_PX` they are a picket
        fence behind the notes, which is what the board's own bar lines had to
        be thinned for.
        """
        measures = getattr(self._song(), "measures", None)
        if not measures:
            return
        theme = get_theme()
        start, end = view
        span = max(1.0, end - start)
        per_ms = rect.width / span
        inside = [m for m in measures if start <= m.start_ms <= end]
        if len(inside) < 2:
            return
        gap = per_ms * max(1.0, (inside[-1].start_ms - inside[0].start_ms)
                           / max(1, len(inside) - 1))
        if gap < BAR_TICK_PX:
            return
        font = self._font("arial", 11)
        every = 1 if gap >= BAR_NUMBER_PX else max(1, int(BAR_NUMBER_PX // gap))
        for measure in inside:
            at = rect.x + int(strip.x_for_ms(measure.start_ms - start,
                                             span, rect.width))
            surface.fill(theme.lane_line, (at, rect.y, 1, rect.height))
            if measure.index % every:
                continue
            # Numbered from 1, the way a player counts and the way every
            # other bar number in this app is written.
            drawn = font.render(str(measure.index + 1), True, theme.hud_text)
            surface.blit(drawn, (at + 2, rect.bottom - drawn.get_height() - 2))

    def _draw_marks(self, surface: pygame.Surface, rect: pygame.Rect,
                    view: tuple[float, float]) -> None:
        """Where the song is, and the passage being marked."""
        theme = get_theme()
        start, end = view
        span = max(1.0, end - start)
        # The passage being drawn, or -- once the button is up and N has set
        # one -- the loop that is actually in force. Without the second, the
        # key that sets a loop shows only where the playhead landed, and how
        # far the passage REACHES is the thing being chosen.
        marked = (self._drag_from, self._drag_to)
        if marked[0] is None and self._screen._loop_enabled:
            marked = (self._screen._loop_start_ms, self._screen._loop_end_ms)
        if marked[0] is not None and marked[1] is not None:
            x0 = strip.x_for_ms(min(marked) - start, span, rect.width)
            x1 = strip.x_for_ms(max(marked) - start, span, rect.width)
            shade = pygame.Surface((max(1, int(x1 - x0)), rect.height),
                                   pygame.SRCALPHA)
            shade.fill(theme.loop_region)
            surface.blit(shade, (rect.x + int(x0), rect.y))
        where = self._screen._playback_ms
        if not (start <= where <= end):
            return              # the playhead is not in the part being read
        at = rect.x + int(strip.x_for_ms(where - start, span, rect.width))
        surface.fill(theme.hit_zone,
                     (max(rect.x, min(at, rect.right - 2)), rect.y,
                      2, rect.height))
