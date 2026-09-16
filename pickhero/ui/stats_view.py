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

from pickhero import runs as runs_mod
from pickhero.ui import strip
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

    @property
    def comparable(self) -> bool:
        return self.fits and bool(self.run.notes)


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
        if not fits:
            detail += "   — another track, or the tab has changed since"
        rows.append(Entry(run=run, title=_when(run) or "a run",
                          detail=detail, fits=fits))
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
        self.mode = "list"           # "list" or "compare"
        self.cursor = 0
        self.first = 0
        self.sort = "date"
        self.selected: list[int] = []
        self.size = 1                # index into the ladder, in compare mode
        self._history: list[runs_mod.Run] = []
        self._entries: list[Entry] = []
        self._bars: dict[tuple, pygame.Surface] = {}
        self._positions: dict[tuple[int, int], list[tuple[int, int]]] = {}
        self._rows: list[tuple[int, pygame.Rect]] = []
        self._bar_rects: list[tuple[int, pygame.Rect]] = []
        self._drag_from: float | None = None
        self._drag_to: float | None = None

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

    def _this_run(self):
        try:
            run = self._screen.current_run()
        except Exception:
            return None
        if not runs_mod.worth_keeping(run):
            return None
        run.label = "this run, not saved yet"
        return run

    def _rebuild(self) -> None:
        self._entries = build(self._history, len(self._screen._timeline.notes),
                              int(getattr(self._screen, "_track_index", 0) or 0),
                              self.sort)
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
            if self.mode == "compare":
                self.mode = "list"
            else:
                self.close()
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
        # compare
        if key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
            self.size = min(self.size + 1, SIZE_STEPS - 1)
        elif key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self.size = max(0, self.size - 1)
        return True

    def _pick(self, index: int) -> None:
        """Mark a run for comparison. Two at a time, oldest choice drops out."""
        if not (0 <= index < len(self._entries)):
            return
        if not self._entries[index].comparable:
            self._screen.say("That run was played on another track — "
                             "its notes are not these notes")
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
        self._compare_mouse(event)

    def _compare_mouse(self, event) -> None:
        """Right-drag over either bar to mark a passage to practise.

        The same gesture as on the strip, for the same reason: the left
        button is how a list is clicked and the right one is free, and a
        modifier wants a hand nobody has with a guitar on.
        """
        duration = self._screen._timeline.duration_ms
        rect = next((r for _, r in self._bar_rects
                     if r.collidepoint(event.pos)), None)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3 and rect:
            at = strip.ms_for_x(event.pos[0] - rect.x, duration, rect.width)
            self._drag_from = self._drag_to = at
        elif event.type == pygame.MOUSEMOTION and self._drag_from is not None:
            # Measured against the FIRST bar, not against whatever the mouse
            # happens to be over: the two are the same width and the same
            # song, and a drag that left the bar it started on would
            # otherwise stop tracking -- the mouse does not stop at an edge
            # the hand cannot feel.
            span = self._bar_rects[0][1] if self._bar_rects else rect
            if span is not None:
                self._drag_to = strip.ms_for_x(event.pos[0] - span.x,
                                               duration, span.width)
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

    # -- the bars -----------------------------------------------------------

    def _note_positions(self, w: int, h: int) -> list[tuple[int, int]]:
        """Where each note of the song sits in a bar this size.

        Built once per size and kept: a four-minute song is a couple of
        thousand notes, and this is the loop that has been found growing with
        the song three times in this codebase.
        """
        key = (w, h)
        found = self._positions.get(key)
        if found is None:
            timeline = self._screen._timeline
            duration = timeline.duration_ms
            spread = strip.row_spread_for(h)
            found = [strip.dot(n, duration, w, h, spread)
                     for n in timeline.notes]
            self._positions[key] = found
        return found

    _COLOUR = {runs_mod.HIT: "feedback_hit", runs_mod.CLOSE: "feedback_close",
               runs_mod.MISS: "feedback_miss"}

    def bar(self, run: runs_mod.Run, w: int, h: int) -> pygame.Surface:
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
        key = (run.notes, w, h, get_theme_name())
        found = self._bars.get(key)
        if found is not None:
            return found
        theme = get_theme()
        surface = pygame.Surface((w, h))
        try:
            surface = surface.convert()
        except pygame.error:
            pass                   # no display yet; the surface still works
        surface.fill(theme.lane_bg_even)
        notes = self._screen._timeline.notes
        spots = self._note_positions(w, h)
        spread = strip.row_spread_for(h)
        if h >= 2 * strip.STRIP_HEIGHT:
            # Once the rows are far enough apart to need it, the six strings
            # are drawn under the dots. Without them a big bar is six rows of
            # floating colour: the same reason the board draws a fretboard
            # rather than a table of rows.
            for string in range(1, strip.STRINGS + 1):
                y = int(strip.row_y(string, h, spread))
                surface.fill(theme.lane_line, (0, y, w, 1))
        # The dot can only grow so far, and the limit is HORIZONTAL: a song
        # puts a couple of hundred notes per string across the bar's width,
        # so a dot much past this merges its row into one solid line. What a
        # bigger bar really buys is the six rows moving apart -- which is
        # `row_spread_for`, not this.
        dot = max(2, min(strip.DOT_PX * 2,
                         int(h * strip.row_spread_for(h) / 12)))
        drawn: set[tuple[int, int]] = set()
        for i, note in enumerate(notes):
            x, y = spots[i]
            if (x, y) in drawn:
                continue
            drawn.add((x, y))
            surface.fill(STRING_COLORS[note.string],
                         (x, y - dot // 2, dot, dot))
        for i, char in enumerate(run.notes):
            if i >= len(spots):
                break
            name = self._COLOUR.get(char.lower())
            if name is None:
                continue
            colour = getattr(theme, name)
            if char.isupper():
                colour = unsure(colour)
            x, y = spots[i]
            surface.fill(colour, (x, y - dot // 2, dot, dot))
        if len(self._bars) >= MAX_BARS:
            self._bars.clear()
        self._bars[key] = surface
        return surface

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
        chrome = title.get_height() + tiny.get_height() + 48
        wanted = chrome + max(1, len(self._entries)) * ROW_H
        panel = pygame.Rect(panel.x, panel.y, panel.width,
                            max(140, min(panel.height, wanted)))
        pygame.draw.rect(surface, theme.lane_line, panel, 1)
        x = panel.x + 12
        y = panel.y + 10
        meta = self._screen._timeline.metadata
        name = meta.title or "this song"
        if meta.artist:
            name = f"{meta.artist} — {name}"
        surface.blit(title.render(f"Runs — {name}", True, theme.hud_text),
                     (x, y))
        y += title.get_height() + 2
        sort_says = ("newest first (S sorts by score)" if self.sort == "date"
                     else "best first (S sorts by date)")
        surface.blit(tiny.render(sort_says, True, theme.hud_accent), (x, y))
        y += tiny.get_height() + 8

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
                surface.blit(self.bar(entry.run, bar_w, strip.STRIP_HEIGHT),
                             (bar_x, y))
            y += ROW_H
        if not self._entries:
            surface.blit(small.render(
                "No runs recorded yet — play the song and leave it",
                True, theme.hud_text), (x, y))
        foot = ("SPACE or click picks two · ENTER compares · S sorts · "
                "ESC closes")
        surface.blit(tiny.render(foot, True, theme.hud_accent),
                     (x, panel.bottom - tiny.get_height() - 8))

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
        foot_h = tiny.get_height() + 12
        room = panel.bottom - top - foot_h
        label_h = small.get_height() + tiny.get_height() + 6
        ladder = size_ladder(strip.STRIP_HEIGHT,
                             max(strip.STRIP_HEIGHT,
                                 room // max(1, len(picked)) - label_h - 10))
        self.size = max(0, min(self.size, len(ladder) - 1))
        bar_h = ladder[self.size]
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
            surface.blit(self.bar(entry.run, width, bar_h), rect.topleft)
            self._draw_marks(surface, rect)
            pygame.draw.rect(surface, theme.lane_line, rect, 1)
            self._bar_rects.append((slot, rect))
            y += bar_h + 14
        foot = (f"+/- size ({self.size + 1}/{len(ladder)}) · "
                "right-drag marks a passage to practise · ESC back")
        surface.blit(tiny.render(foot, True, theme.hud_accent),
                     (x, panel.bottom - tiny.get_height() - 8))

    def _draw_marks(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        """Where the song is, and the passage being marked."""
        theme = get_theme()
        duration = self._screen._timeline.duration_ms
        if self._drag_from is not None and self._drag_to is not None:
            x0 = strip.x_for_ms(min(self._drag_from, self._drag_to),
                                duration, rect.width)
            x1 = strip.x_for_ms(max(self._drag_from, self._drag_to),
                                duration, rect.width)
            shade = pygame.Surface((max(1, int(x1 - x0)), rect.height),
                                   pygame.SRCALPHA)
            shade.fill(theme.loop_region)
            surface.blit(shade, (rect.x + int(x0), rect.y))
        at = rect.x + int(strip.x_for_ms(max(0.0, self._screen._playback_ms),
                                         duration, rect.width))
        surface.fill(theme.hit_zone,
                     (max(rect.x, min(at, rect.right - 2)), rect.y,
                      2, rect.height))
