"""The audio device pickers -- the input, and now the output too.

Lists the devices and writes the choice to the config at once, on confirm.

ONE screen for both halves, with a `kind`. *"Mit D habe ich das richtige
Interface ausgewaehlt"* -- that is the INPUT, and for months there was no
answer at all for the other side: SDL took the system default at the first
`mixer.init()` and the whole session played to it. Everything about picking
one from a list is the same job, and two copies of it would be two ideas of
what the arrow keys do; what really differs is four small things, and they
are the four places this file branches.

The two are NOT the same device list, and must not be: an input is named by
PortAudio and carries an index, an API and a rate, while an output is named
by SDL -- which is the spelling `mixer.init(devicename=...)` takes and
nothing else.
"""

from __future__ import annotations

import pygame

from pickhero.audio import output as audio_output
from pickhero.audio.input import list_audio_devices
from pickhero.config import Config
from pickhero.ui import clickable
from pickhero.ui.colors import get_theme

# Max items visible before scrolling
VISIBLE_ITEMS = 14


def _get_font(name: str, size: int) -> pygame.font.Font:
    for family in (name, "Courier New", "monospace"):
        font = pygame.font.SysFont(family, size)
        if font:
            return font
    return pygame.font.Font(None, size)


class DeviceMenuScreen:
    """Device selector screen — mirrors MenuScreen list-selection pattern."""

    def __init__(self, config: Config, kind: str = "input"):
        #: Where the hint line's underlined keys landed. Rebuilt by the
        #: drawing and read by the App, so a click cannot land on the
        #: neighbour of the word under the pointer.
        self._links = clickable.Links()
        self._config = config
        #: "input" or "output". Anything else is the input, because a screen
        #: that refuses to draw is worse than one that draws the commoner
        #: half of what it is for.
        self._kind = "output" if kind == "output" else "input"
        self._devices: list[dict] = []
        self._selected = 0
        self._scroll_offset = 0
        self._refresh_devices()

    def _refresh_devices(self) -> None:
        """Re-scan the devices of this kind and put the cursor on the one set."""
        if self._kind == "output":
            self._devices = [{"name": name}
                             for name in audio_output.device_names()]
        else:
            self._devices = list_audio_devices()
        # Set selection to match current config
        self._selected = 0  # "System Default" is index 0
        for i, label in enumerate(self._items()):
            if i and label[1]:
                self._selected = i
                break
        self._scroll_offset = 0
        self._ensure_visible()

    def _items(self) -> list[tuple[str, bool]]:
        """(label, is the one in force) for the default and every device.

        One reader for the drawing and for where the cursor opens, so the dot
        can never sit on a row the cursor did not start on.
        """
        if self._kind == "output":
            wanted = self._config.audio.output_device
            items: list[tuple[str, bool]] = [("System Default", not wanted)]
            for dev in self._devices:
                name = str(dev["name"])
                items.append((name, name == wanted))
            return items
        current = self._config.audio.device_index
        items = [("System Default", current is None)]
        for dev in self._devices:
            # The API, not just the index. One interface is listed several
            # times over and they do not behave the same: measured across the
            # player's run logs, the same Scarlett Solo kept the clock flat on
            # one entry and drifted 0.61 % on another.
            api = dev.get("host_api") or "?"
            items.append((f"[{dev['index']}] {dev['name']}  "
                          f"({api}, {dev['sample_rate']:.0f} Hz)",
                          dev["index"] == current))
        return items

    @property
    def _item_count(self) -> int:
        return 1 + len(self._devices)  # "System Default" + real devices

    def handle_event(self, event: pygame.event.Event) -> str | None:
        """Process input. Returns "back" on ESC, "selected" on confirm, else None."""
        if event.type != pygame.KEYDOWN:
            return None

        if event.key == pygame.K_ESCAPE:
            return "back"

        if event.key == pygame.K_UP:
            self._selected = max(0, self._selected - 1)
            self._ensure_visible()
        elif event.key == pygame.K_DOWN:
            self._selected = min(self._item_count - 1, self._selected + 1)
            self._ensure_visible()
        elif event.key == pygame.K_PAGEUP:
            self._selected = max(0, self._selected - VISIBLE_ITEMS)
            self._ensure_visible()
        elif event.key == pygame.K_PAGEDOWN:
            self._selected = min(self._item_count - 1, self._selected + VISIBLE_ITEMS)
            self._ensure_visible()
        elif event.key == pygame.K_HOME:
            self._selected = 0
            self._ensure_visible()
        elif event.key == pygame.K_END:
            self._selected = self._item_count - 1
            self._ensure_visible()
        elif event.key == pygame.K_r:
            self._refresh_devices()
        elif event.key == pygame.K_RETURN:
            self._apply_selection()
            return "selected"

        return None

    def _apply_selection(self) -> None:
        """Persist the selected device to config."""
        picked = None if self._selected == 0 else self._devices[self._selected - 1]
        if self._kind == "output":
            name = "" if picked is None else str(picked["name"])
            self._config.audio.output_device = name
            self._config.save()
            # REOPENED here rather than at the next restart. A setting that
            # takes effect later is indistinguishable from one that does
            # nothing, which this app has shipped four times -- and nothing
            # is playing: this screen is reached from the song list and the
            # settings, never from inside a song.
            audio_output.set_output_device(name)
            return
        self._config.audio.device_index = (
            None if picked is None else picked["index"])
        self._config.save()

    @property
    def links(self) -> clickable.Links:
        """Where the hint line's underlined keys landed, for the App to ask."""
        return self._links

    def render(self, surface: pygame.Surface) -> None:
        """Draw the device selector screen."""
        self._links.clear()
        t = get_theme()
        surface.fill(t.menu_bg)
        w, h = surface.get_size()

        title_font = _get_font("arial", 36)
        item_font = _get_font("consolas", 22)
        hint_font = _get_font("arial", 16)

        # Title
        title = ("Audio Output" if self._kind == "output"
                 else "Audio Device")
        title_surf = title_font.render(title, True, t.hud_accent)
        surface.blit(title_surf, (w // 2 - title_surf.get_width() // 2, 24))

        sub = ("Where the backing track and the metronome are played"
               if self._kind == "output"
               else "Select an audio input device")
        sub_surf = hint_font.render(sub, True, t.hud_text)
        surface.blit(sub_surf, (w // 2 - sub_surf.get_width() // 2, 68))

        list_top = 110
        item_h = 30
        list_left = 60
        list_width = w - 120

        items = self._items()

        # Render visible slice
        visible_end = min(self._scroll_offset + VISIBLE_ITEMS, len(items))
        for i in range(self._scroll_offset, visible_end):
            y = list_top + (i - self._scroll_offset) * item_h
            label, is_active = items[i]

            if i == self._selected:
                pygame.draw.rect(
                    surface,
                    t.menu_selected_bg,
                    (list_left - 8, y, list_width, item_h),
                    border_radius=4,
                )
                color = t.menu_selected
            else:
                color = t.menu_item

            # Active device indicator
            if is_active:
                check = item_font.render("●", True, t.menu_check)
                surface.blit(check, (list_left - 4, y + 4))

            text_surf = item_font.render(label, True, color)
            surface.blit(text_surf, (list_left + 20, y + 4))

        # Scroll indicators
        if self._scroll_offset > 0:
            arrow = hint_font.render("▲ more", True, t.hud_text)
            surface.blit(arrow, (w // 2 - arrow.get_width() // 2, list_top - 20))
        if visible_end < len(items):
            arrow = hint_font.render("▼ more", True, t.hud_text)
            y_bottom = list_top + VISIBLE_ITEMS * item_h + 4
            surface.blit(arrow, (w // 2 - arrow.get_width() // 2, y_bottom))

        # Hints
        hint = "UP/DOWN: navigate  |  ENTER: select  |  R: refresh  |  ESC: back"
        clickable.blit_centred(surface, hint_font, hint, h - 36, w,
                               t.hud_text, self._links)

    def _ensure_visible(self) -> None:
        if self._selected < self._scroll_offset:
            self._scroll_offset = self._selected
        elif self._selected >= self._scroll_offset + VISIBLE_ITEMS:
            self._scroll_offset = self._selected - VISIBLE_ITEMS + 1
