"""Everything that is set once, on one screen that says what it is set to.

Forty-one keys are handled while a song runs, and the footer needs two lines
to list them. That is fine for the ones the hands reach for with the guitar
still on -- play, wait mode, tempo, loop, auto-sync -- and wrong for the rest:
a fret limit, a muted string or a noise gate is set once and then lives on,
invisible, changing how everything scores.

That invisibility has already cost a session. A fret filter left switched on
made whole songs unplayable and nothing on screen said so, because the only
place the setting existed was in a keystroke nobody remembered pressing. So
the point of this screen is not that it can CHANGE things -- the keys could
already do that -- it is that it SHOWS them, all at once, with anything that
is not the standard value marked. A setting you can see is a setting you can
undo.

Keys: up/down to choose, left/right to change, ENTER to open the screens that
need one (audio device, calibration), R to put one row back to standard, ESC
to leave. Everything is saved as it is changed; there is no OK button to
forget to press.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pygame

from pickhero.audio.input import list_audio_devices
from pickhero.config import MAX_LATENCY_OFFSET_MS, Config
from pickhero.ui.scrolling import MAX_BACKING_OFFSET_MS
from pickhero.ui.colors import cycle_theme, get_theme

VISIBLE_ROWS = 14
# `active_strings` is indexed by GP string number minus one, so index 0 is the
# HIGH e. Guitarists name their strings the other way round, low first, and the
# row is drawn that way -- getting this backwards would mute the wrong string
# and look like a detection fault.
STRING_ORDER = [5, 4, 3, 2, 1, 0]
STRING_NAMES = {5: "E", 4: "A", 3: "D", 2: "G", 1: "B", 0: "e"}


def _get_font(name: str, size: int) -> pygame.font.Font:
    for family in (name, "Courier New", "monospace"):
        font = pygame.font.SysFont(family, size)
        if font:
            return font
    return pygame.font.Font(None, size)


@dataclass
class Setting:
    """One row: what it is called, what it says now, and how it changes."""

    key: str
    label: str
    value: Callable[[], str]
    # Called with -1 or +1. None for a row that only opens another screen.
    adjust: Callable[[int], None] | None = None
    # ENTER: the screen to switch to, or None.
    opens: str | None = None
    # What the setting means for someone holding a guitar, not for a
    # programmer. Shown for the selected row only.
    note: str = ""
    # True while the value is the one the app ships with. A row that is NOT
    # standard is marked, because that is the one that explains a surprise.
    is_default: Callable[[], bool] = field(default=lambda: True)


class SettingsMenuScreen:
    """The settings list. Owns no state of its own beyond the cursor."""

    def __init__(self, config: Config):
        self._config = config
        self._selected = 0
        self._scroll = 0
        # Which of the six strings the strings row is pointing at. That row is
        # six settings in one, so it carries a cursor of its own rather than
        # inventing a key nobody would guess.
        self._string_cursor = 0
        self._rows = self._build_rows()

    # -- the rows --

    def _build_rows(self) -> list[Setting]:
        c = self._config
        default = Config()

        def audio_device_name() -> str:
            index = c.audio.device_index
            if index is None:
                return "system default"
            try:
                for device in list_audio_devices():
                    if device["index"] == index:
                        return str(device["name"])
            except Exception:
                pass
            return f"device #{index}"

        def adjust_gate(step: int) -> None:
            c.audio.noise_gate_db = max(-90.0, min(-20.0,
                                                   c.audio.noise_gate_db + step))

        def adjust_window(step: int) -> None:
            c.timing_window_ms = max(50.0, min(500.0,
                                               c.timing_window_ms + step * 10))

        def adjust_latency(step: int) -> None:
            c.audio_latency_offset_ms = max(
                -MAX_LATENCY_OFFSET_MS,
                min(MAX_LATENCY_OFFSET_MS,
                    c.audio_latency_offset_ms + step * 10))

        def adjust_fret(step: int) -> None:
            c.max_fret = max(1, min(24, c.max_fret + step))

        def adjust_scroll(step: int) -> None:
            c.scroll_speed_factor = max(0.5, min(2.0,
                                                 round((c.scroll_speed_factor + step * 0.1) * 10) / 10))

        def adjust_count_in(step: int) -> None:
            c.count_in_beats = max(0, min(8, c.count_in_beats + step))

        def adjust_backing_offset(step: int) -> None:
            # A hundred milliseconds a press: the row has to be able to cross
            # ten seconds without turning into a thousand presses, and the
            # fine work is done with N/M while the song runs anyway.
            c.backing_offset_ms = max(-MAX_BACKING_OFFSET_MS,
                                      min(MAX_BACKING_OFFSET_MS,
                                          c.backing_offset_ms + step * 100))

        def strings_label() -> str:
            parts = []
            for position, index in enumerate(STRING_ORDER):
                name = STRING_NAMES[index] if c.active_strings[index] else "·"
                pointed = (self._rows[self._selected].key == "strings"
                           if getattr(self, "_rows", None) else False)
                if pointed and position == self._string_cursor:
                    parts.append(f"[{name}]")
                else:
                    parts.append(f" {name} ")
            return "".join(parts)

        return [
            Setting("device", "Audio input", audio_device_name, opens="device",
                    note="Which interface the guitar comes in on. ENTER to "
                         "choose.",
                    is_default=lambda: c.audio.device_index is None),
            Setting("calibrate", "Guitar calibration",
                    lambda: "done" if c.calibration else "not done",
                    opens="calibration",
                    note="Plays each open string once and learns your noise "
                         "floor. ENTER to run it.",
                    is_default=lambda: True),
            Setting("gate", "Noise gate",
                    lambda: f"{c.audio.noise_gate_db:.0f} dB", adjust_gate,
                    note="Below this, sound is ignored. Higher shuts out room "
                         "noise; too high swallows quiet notes.",
                    is_default=lambda: c.audio.noise_gate_db == default.audio.noise_gate_db),
            Setting("window", "Hit window",
                    lambda: f"{c.timing_window_ms:.0f} ms", adjust_window,
                    note="How far off the beat a note still counts. Wider is "
                         "kinder; narrower is Yousician-strict.",
                    is_default=lambda: c.timing_window_ms == default.timing_window_ms),
            Setting("latency", "Timing offset",
                    lambda: f"{c.audio_latency_offset_ms:+.0f} ms", adjust_latency,
                    note="Shifts every strike against the notes. K measures "
                         "this for you while playing — set by hand only if "
                         "you know why.",
                    is_default=lambda: c.audio_latency_offset_ms == 0.0),
            Setting("fret", "Fret limit", lambda: f"up to fret {c.max_fret}",
                    adjust_fret,
                    note="Notes above this fret are dropped from the song. "
                         "Left low by accident, whole songs go quiet.",
                    is_default=lambda: c.max_fret == default.max_fret),
            Setting("strings", "Strings played", strings_label, None,
                    note="LEFT/RIGHT picks a string, ENTER mutes it. A muted "
                         "string's notes are dropped from the song entirely — "
                         "the same trap as the fret limit.",
                    is_default=lambda: all(c.active_strings)),
            Setting("chords", "Chord scoring",
                    lambda: "every string" if not c.chord_partial_credit
                    else "one string is enough",
                    lambda step: setattr(c, "chord_partial_credit",
                                         not c.chord_partial_credit),
                    note="Whether a strum counts when only part of it was "
                         "heard. Monophonic detection rarely hears a whole "
                         "chord.",
                    is_default=lambda: c.chord_partial_credit is True),
            Setting("verify", "Per-string chord check",
                    lambda: "on" if c.chord_verify else "off",
                    lambda step: setattr(c, "chord_verify", not c.chord_verify),
                    note="Finds the one string of a chord on the wrong fret. "
                         "Costs about a third of a second before the verdict "
                         "settles.",
                    is_default=lambda: c.chord_verify is True),
            Setting("bend", "Bend check",
                    lambda: "on" if c.bend_check else "off",
                    lambda step: setattr(c, "bend_check", not c.bend_check),
                    note="Marks a bend yellow when it measurably fell short "
                         "of what the tab wrote, or was not held. Never red.",
                    is_default=lambda: c.bend_check is True),
            Setting("tempo", "Practice speed",
                    lambda: (f"per song — {len(c.song_tempo_factors)} slowed "
                             f"down" if c.song_tempo_factors
                             else "per song — none slowed down"),
                    None,
                    note="Set with PgUp/PgDn while playing and kept for that "
                         "song. Every other song opens at full speed.",
                    is_default=lambda: True),
            Setting("scroll", "Scroll speed",
                    lambda: f"{c.scroll_speed_factor:.1f}x", adjust_scroll,
                    note="How far ahead you see. Faster scrolling means more "
                         "warning and smaller notes.",
                    is_default=lambda: c.scroll_speed_factor == 1.0),
            Setting("countin", "Count-in",
                    lambda: f"{c.count_in_beats} beats", adjust_count_in,
                    note="Clicks before the song starts, so the first note is "
                         "not a surprise.",
                    is_default=lambda: c.count_in_beats == default.count_in_beats),
            Setting("backing", "MIDI backing track",
                    lambda: "on" if c.backing_track_enabled else "off",
                    lambda step: setattr(c, "backing_track_enabled",
                                         not c.backing_track_enabled),
                    note="The other instruments of the tab, played by a synth. "
                         "B while playing.",
                    is_default=lambda: c.backing_track_enabled is True),
            Setting("backing_offset", "MIDI backing sync",
                    lambda: f"{c.backing_offset_ms:+.0f} ms",
                    adjust_backing_offset,
                    note="Shifts the synth against the notes. N/M while "
                         "playing does 10 ms a press, Alt+N/M a second.",
                    is_default=lambda: c.backing_offset_ms == 0.0),
            Setting("mp3", "Recorded backing track",
                    lambda: "on" if c.mp3_backing_enabled else "off",
                    lambda step: setattr(c, "mp3_backing_enabled",
                                         not c.mp3_backing_enabled),
                    note="A real recording alongside the synth. Shift+U "
                         "picks the file; its sync reaches 8 minutes, for a "
                         "tab that is only the solo.",
                    is_default=lambda: c.mp3_backing_enabled is True),
            Setting("wait", "Wait mode",
                    lambda: "on" if c.wait_mode else "off",
                    lambda step: setattr(c, "wait_mode", not c.wait_mode),
                    note="The song holds still until you play the written "
                         "note. W while playing.",
                    is_default=lambda: c.wait_mode is False),
            Setting("theme", "Theme", lambda: c.theme,
                    lambda step: self._cycle_theme(),
                    note="Dark or light.",
                    is_default=lambda: True),
        ]

    def _cycle_theme(self) -> None:
        self._config.theme = cycle_theme()

    # -- input --

    def handle_event(self, event: pygame.event.Event) -> str | None:
        """ESC returns "back"; ENTER on a row may return another screen's name."""
        if event.type != pygame.KEYDOWN:
            return None
        if event.key == pygame.K_ESCAPE:
            return "back"

        row = self._rows[self._selected]
        if event.key == pygame.K_UP:
            self._selected = max(0, self._selected - 1)
            self._ensure_visible()
        elif event.key == pygame.K_DOWN:
            self._selected = min(len(self._rows) - 1, self._selected + 1)
            self._ensure_visible()
        elif event.key in (pygame.K_LEFT, pygame.K_RIGHT):
            step = -1 if event.key == pygame.K_LEFT else 1
            if row.key == "strings":
                self._string_cursor = max(0, min(5, self._string_cursor + step))
            elif row.adjust is not None:
                row.adjust(step)
                self._config.save()
        elif event.key == pygame.K_RETURN:
            if row.opens is not None:
                return row.opens
            if row.key == "strings":
                self._toggle_string(STRING_ORDER[self._string_cursor])
            elif row.adjust is not None:
                # A row with no screen behind it treats ENTER as "next value",
                # so a toggle can be worked without knowing it is a toggle.
                row.adjust(1)
                self._config.save()
        elif event.key == pygame.K_r:
            self._reset(row)
        return None

    def _toggle_string(self, index: int) -> None:
        """Mute or unmute one string, never all six.

        All six muted is a song with nothing in it, which reads as a broken
        app rather than as a setting -- the playing screen refuses it too.
        """
        strings = list(self._config.active_strings)
        strings[index] = not strings[index]
        if not any(strings):
            return
        self._config.active_strings = strings
        self._config.save()

    def _reset(self, row: Setting) -> None:
        """Put one setting back to what the app ships with.

        Named per row rather than as a global "reset everything": the value
        that needs undoing is usually one somebody changed by accident, and
        losing the device and the calibration along with it is a punishment
        for having noticed.
        """
        default = Config()
        if row.key == "gate":
            self._config.audio.noise_gate_db = default.audio.noise_gate_db
        elif row.key == "window":
            self._config.timing_window_ms = default.timing_window_ms
        elif row.key == "latency":
            self._config.audio_latency_offset_ms = 0.0
        elif row.key == "fret":
            self._config.max_fret = default.max_fret
        elif row.key == "strings":
            self._config.active_strings = [True] * 6
        elif row.key == "chords":
            self._config.chord_partial_credit = default.chord_partial_credit
        elif row.key == "verify":
            self._config.chord_verify = default.chord_verify
        elif row.key == "bend":
            self._config.bend_check = default.bend_check
        elif row.key == "scroll":
            self._config.scroll_speed_factor = 1.0
        elif row.key == "countin":
            self._config.count_in_beats = default.count_in_beats
        elif row.key == "backing":
            self._config.backing_track_enabled = default.backing_track_enabled
        elif row.key == "backing_offset":
            self._config.backing_offset_ms = 0.0
        elif row.key == "mp3":
            self._config.mp3_backing_enabled = default.mp3_backing_enabled
        elif row.key == "wait":
            self._config.wait_mode = False
        else:
            return
        self._config.save()

    def changed_rows(self) -> list[Setting]:
        """Every setting that is not on its standard value."""
        return [row for row in self._rows if not row.is_default()]

    def _ensure_visible(self) -> None:
        if self._selected < self._scroll:
            self._scroll = self._selected
        elif self._selected >= self._scroll + VISIBLE_ROWS:
            self._scroll = self._selected - VISIBLE_ROWS + 1

    # -- drawing --

    def render(self, surface: pygame.Surface) -> None:
        t = get_theme()
        surface.fill(t.menu_bg)
        width, height = surface.get_size()

        title_font = _get_font("arial", 32)
        item_font = _get_font("consolas", 21)
        hint_font = _get_font("arial", 16)

        title = title_font.render("Settings", True, t.hud_accent)
        surface.blit(title, (width // 2 - title.get_width() // 2, 22))

        changed = self.changed_rows()
        if changed:
            # The whole reason the screen exists: a setting nobody remembers
            # changing is the one that explains the surprise.
            summary = (f"{len(changed)} setting"
                       f"{'s' if len(changed) != 1 else ''} away from standard: "
                       + ", ".join(row.label for row in changed))
        else:
            summary = "Everything is on its standard value."
        sub = hint_font.render(summary[:150], True, t.hud_text)
        surface.blit(sub, (width // 2 - sub.get_width() // 2, 62))

        top, row_h, left = 100, 30, 70
        value_x = left + 320
        visible_end = min(self._scroll + VISIBLE_ROWS, len(self._rows))
        for i in range(self._scroll, visible_end):
            row = self._rows[i]
            y = top + (i - self._scroll) * row_h
            if i == self._selected:
                pygame.draw.rect(surface, t.menu_selected_bg,
                                 (left - 10, y, width - 2 * left + 20, row_h),
                                 border_radius=4)
                colour = t.menu_selected
            else:
                colour = t.menu_item
            surface.blit(item_font.render(row.label, True, colour), (left, y + 4))
            # A changed value is drawn in the accent colour and marked, so the
            # screen can be read at a glance rather than compared from memory.
            value_colour = t.hud_accent if not row.is_default() else colour
            mark = "• " if not row.is_default() else "  "
            surface.blit(item_font.render(mark + row.value(), True, value_colour),
                         (value_x, y + 4))

        if self._scroll > 0:
            more = hint_font.render("▲ more", True, t.hud_text)
            surface.blit(more, (width // 2 - more.get_width() // 2, top - 20))
        if visible_end < len(self._rows):
            more = hint_font.render("▼ more", True, t.hud_text)
            surface.blit(more, (width // 2 - more.get_width() // 2,
                                top + VISIBLE_ROWS * row_h + 4))

        note = self._rows[self._selected].note
        note_surf = hint_font.render(note[:140], True, t.hud_text)
        surface.blit(note_surf, (left, height - 62))

        hint = ("UP/DOWN: choose  |  LEFT/RIGHT: change  |  ENTER: open  |  "
                "R: back to standard  |  ESC: done (saved as you go)")
        hint_surf = hint_font.render(hint, True, t.hud_text)
        surface.blit(hint_surf,
                     (width // 2 - hint_surf.get_width() // 2, height - 34))
