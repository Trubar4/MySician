"""Songsterr search and download screen.

Lets the user search for tabs online and download them to the songs
directory. Falls back to opening the browser if direct download fails.

**ENTER fetches the whole song, not just the tab.** Songsterr answers one
request with the tab, a timestamp per measure, and the YouTube id of the
recording those timestamps were made against -- and the player was doing the
last three of those by hand: find an MP3, paste the link back with Ctrl+U,
then press Ctrl+S and hope the listening reads a song that repeats itself.
They all come from the same reply, so they all happen on the one ENTER now.

What gets stored, per song:

- the tab, in the songs folder
- the bar map, beside the tab as `<name>.songsterr.json`, so the song still
  syncs on a machine with no network
- the Songsterr id, so `Ctrl+U` is not needed
- the audio, pulled from **the very video the bar map is in** -- which
  leaves the fit an encoder delay to find instead of a constant between two
  different recordings of the song
"""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

import pygame

from pickhero.tabs.downloader import (
    SongsterrResult,
    find,
    get_songsterr_url,
    grab_song,
    sanitize_filename,
)
from pickhero.ui.colors import get_theme

VISIBLE_ITEMS = 14


def _get_font(name: str, size: int) -> pygame.font.Font:
    for family in (name, "Courier New", "monospace"):
        font = pygame.font.SysFont(family, size)
        if font:
            return font
    return pygame.font.Font(None, size)


class DownloadMenuScreen:
    """Songsterr search/download screen — 3 states: input, results, status."""

    def __init__(self, songs_dir: Path, config=None):
        self._songs_dir = songs_dir
        # Optional so the screen can still be built and drawn without one.
        # What it costs when absent is the remembering, not the download.
        self._config = config
        self._state = "input"  # "input" | "results" | "status" | "done"
        self._query = ""
        self._results: list[SongsterrResult] = []
        self._selected = 0
        self._scroll_offset = 0
        self._status_msg = ""
        self._searching = False
        self._downloading = False
        self._download_result: str | None = None  # "downloaded" or "failed"
        #: (0..1, what it is doing). Written by the worker thread, read by
        #: the frame -- a float and a string, so nothing needs a lock.
        self._progress: tuple[float, str] = (0.0, "")
        #: One line per thing that came down, or did not. Held until the
        #: player has read them: a song that arrived without its audio looks
        #: exactly like one that arrived with it until somebody says so.
        self._notes: list[str] = []

    def handle_event(self, event: pygame.event.Event) -> str | None:
        """Process input. Returns "back" (ESC from input) or "downloaded" (success)."""
        if self._state == "input":
            return self._handle_input_event(event)
        elif self._state == "results":
            return self._handle_results_event(event)
        elif self._state == "status":
            return self._handle_status_event(event)
        elif self._state == "done":
            return self._handle_done_event(event)
        return None

    def _handle_input_event(self, event: pygame.event.Event) -> str | None:
        if event.type != pygame.KEYDOWN:
            return None

        if event.key == pygame.K_ESCAPE:
            return "back"

        if event.key == pygame.K_RETURN and self._query.strip() and not self._searching:
            self._searching = True
            self._status_msg = "Searching..."
            self._state = "status"
            thread = threading.Thread(target=self._do_search, daemon=True)
            thread.start()
            return None

        # Ctrl+V, because a Songsterr URL is 60 characters of slug nobody
        # types. Without this "paste a link" means "read it off the screen
        # and copy it in by hand", which is not pasting.
        if event.key == pygame.K_v and event.mod & pygame.KMOD_CTRL:
            from pickhero.ui.clipboard import clipboard_text
            # One line, whitespace out. A URL copied from a browser can
            # arrive with a trailing newline, and `song_id_of` would still
            # find the id -- but the box would draw a second empty line.
            pasted = " ".join(clipboard_text().split())
            if pasted:
                self._query += pasted
            return None

        if event.key == pygame.K_BACKSPACE:
            self._query = self._query[:-1]
            return None

        ch = event.unicode
        if ch and ch.isprintable() and ch not in ("\r", "\n", "\t"):
            self._query += ch

        return None

    def _handle_results_event(self, event: pygame.event.Event) -> str | None:
        if event.type != pygame.KEYDOWN:
            return None

        if event.key == pygame.K_ESCAPE:
            self._state = "input"
            return None

        if event.key == pygame.K_UP:
            self._selected = max(0, self._selected - 1)
            self._ensure_visible()
        elif event.key == pygame.K_DOWN:
            self._selected = min(len(self._results) - 1, self._selected + 1)
            self._ensure_visible()
        elif event.key == pygame.K_PAGEUP:
            self._selected = max(0, self._selected - VISIBLE_ITEMS)
            self._ensure_visible()
        elif event.key == pygame.K_PAGEDOWN:
            self._selected = min(len(self._results) - 1, self._selected + VISIBLE_ITEMS)
            self._ensure_visible()
        elif event.key == pygame.K_HOME:
            self._selected = 0
            self._ensure_visible()
        elif event.key == pygame.K_END:
            self._selected = len(self._results) - 1
            self._ensure_visible()
        elif event.key == pygame.K_RETURN and self._results:
            self._start_download(self._results[self._selected])

        return None

    def _handle_status_event(self, event: pygame.event.Event) -> str | None:
        # Check if background work finished
        if self._download_result == "downloaded":
            self._download_result = None
            # Not straight back to the song list: the notes say whether the
            # audio and the bar map came too, and a screen that closes
            # itself is a screen nobody reads.
            self._state = "done"
            return None

        if self._download_result == "failed":
            self._download_result = None
            self._state = "results"
            return None

        if event.type != pygame.KEYDOWN:
            return None

        if event.key == pygame.K_ESCAPE:
            if self._searching or self._downloading:
                return None  # can't cancel network ops
            self._state = "input"
            return None

        return None

    def _handle_done_event(self, event: pygame.event.Event) -> str | None:
        """Any key goes back to the songs, which is the only thing to do."""
        if event.type != pygame.KEYDOWN:
            return None
        return "downloaded"

    def say(self, text: str) -> None:
        """One line under whatever the screen is showing."""
        self._status_msg = text

    def copy_text(self) -> str:
        """Everything on this screen, as text.

        The reason this screen has it first: its failure line names a
        Songsterr revision and the fields that revision carried, and the
        player was reading that off a photograph of his monitor and typing
        it back.
        """
        out = ["Search Songsterr", f"Query: {self._query}"]
        if self._status_msg:
            out.append(self._status_msg)
        out += list(self._notes)
        out += [f"{'> ' if i == self._selected else '  '}"
                f"{r.artist} - {r.title}  (s{r.song_id})"
                for i, r in enumerate(self._results)]
        return "\n".join(out)

    @staticmethod
    def _wake() -> None:
        """Nudge the event loop, because the answer came from a thread.

        Every state change on this screen is read inside `handle_event`, and
        `handle_event` only runs when pygame has an event. A player who takes
        his hands off the keyboard while a song downloads generates none --
        so without this the finished download sits there until something is
        touched, which is indistinguishable from a hang.
        """
        try:
            pygame.event.post(pygame.event.Event(pygame.USEREVENT))
        except pygame.error:
            # No display, no queue: a test, and nothing is waiting on it.
            pass

    def _do_search(self) -> None:
        """Run search in background thread.

        `find`, not `search`: a pasted Songsterr link or a typed id goes
        straight to that song. The player who has already picked his version
        over there should not be handed the other four transcriptions of the
        same song to choose from again.
        """
        results = find(self._query.strip())
        self._results = results
        self._selected = 0
        self._scroll_offset = 0
        self._searching = False
        if results:
            self._state = "results"
        else:
            self._status_msg = ("Songsterr has no song with that id. "
                                "Press ESC to try again."
                                if self._query.strip().isdigit() else
                                "No results found. Press ESC to try again.")
        self._wake()

    def _start_download(self, result: SongsterrResult) -> None:
        """Begin download in background thread."""
        self._downloading = True
        self._status_msg = f"Downloading {result.artist} - {result.title}..."
        self._state = "status"
        thread = threading.Thread(
            target=self._do_download, args=(result,), daemon=True
        )
        thread.start()

    def _do_download(self, result: SongsterrResult) -> None:
        """Run download in background thread."""
        name = sanitize_filename(f"{result.artist} - {result.title}")
        output_path = self._songs_dir / f"{name}.gp5"

        def report(fraction: float, what: str) -> None:
            self._progress = (fraction, what)
            self._status_msg = f"{name} — {what}…"

        grab = grab_song(result.song_id, output_path, on_progress=report)
        self._downloading = False
        self._progress = (1.0, "")
        if not grab.ok:
            # WHY, then what to do about it. "Songsterr holds no Guitar Pro
            # file for this tab" is not a dead end -- it means THIS request
            # has no file behind it, and the player has a downloader of his
            # own that does. The bar map came down anyway and is already
            # sitting in the songs folder waiting for a tab of that name, so
            # the useful thing to say is the NAME.
            self._notes = [grab.notes[0] if grab.notes
                           else "The tab could not be downloaded."]
            if grab.bars:
                self._notes.append(f"Its bar map ({grab.bars} bars) is saved "
                                   f"and waiting.")
                self._notes.append(f"Fetch the tab yourself and save it in "
                                   f"your songs folder as:")
                self._notes.append(grab.wanted_name)
                self._notes.append("Then the sync is already set up.")
            self._notes.append("Songsterr is open in your browser.")
            self._status_msg = f"{name} — not downloaded"
            webbrowser.open(get_songsterr_url(result.song_id, result.title,
                                              result.artist))
            self._state = "done"
            self._download_result = None
            self._wake()
            return

        # The tab's REAL name: `grab.tab_path`, not the name it was asked
        # for. Songsterr decides the format and the suffix follows it.
        self._notes = [f"Tab: {grab.tab_path.name}"] + list(grab.notes)
        self._remember(grab.tab_path.stem, grab)
        self._status_msg = f"Downloaded: {name}"
        self._download_result = "downloaded"
        self._wake()

    def _remember(self, song_key: str, grab) -> None:
        """Store what the download learnt, against this song.

        `song_key` is the tab's STEM -- that is what `App` hands the player
        screen -- so writing it here is what makes `Ctrl+U` unnecessary and
        the recording load itself. Best-effort on purpose: a settings file
        that cannot be written is a worse day than a song that has to be
        pointed at its MP3 by hand, but it is not a reason to throw the
        download away.
        """
        config = self._config
        if config is None:
            return
        try:
            setter = getattr(config, "set_songsterr_for", None)
            if setter is not None:
                setter(song_key, grab.song_id)
            if grab.audio_path is not None:
                setter = getattr(config, "set_mp3_path_for", None)
                if setter is not None:
                    setter(song_key, str(grab.audio_path))
            config.save()
        except (OSError, AttributeError, ValueError) as exc:
            self._notes.append(f"Settings not saved: {exc}")

    def render(self, surface: pygame.Surface) -> None:
        t = get_theme()
        surface.fill(t.menu_bg)
        w, h = surface.get_size()

        title_font = _get_font("arial", 36)
        item_font = _get_font("consolas", 22)
        hint_font = _get_font("arial", 16)

        # Title
        title_surf = title_font.render("Search Songsterr", True, t.hud_accent)
        surface.blit(title_surf, (w // 2 - title_surf.get_width() // 2, 24))

        if self._state == "input":
            self._render_input(surface, w, h, item_font, hint_font, t)
        elif self._state == "results":
            self._render_results(surface, w, h, item_font, hint_font, t)
        elif self._state == "status":
            self._render_status(surface, w, h, item_font, hint_font, t)
        elif self._state == "done":
            self._render_done(surface, w, h, item_font, hint_font, t)

    def _render_input(self, surface, w, h, item_font, hint_font, t) -> None:
        sub_surf = hint_font.render(
            "Type a song or artist — or paste a Songsterr link or id",
            True, t.hud_text
        )
        surface.blit(sub_surf, (w // 2 - sub_surf.get_width() // 2, 68))

        # Search box
        box_left = 60
        box_top = 140
        box_width = w - 120
        box_height = 36

        pygame.draw.rect(
            surface, t.menu_selected_bg,
            (box_left, box_top, box_width, box_height),
            border_radius=4,
        )
        # A pasted Songsterr URL is longer than the box. The TAIL is what
        # matters -- the id lives on the end -- and a caret that has walked
        # off the right edge looks like a box that stopped taking input.
        prompt = f"> {self._query}_"
        prompt_surf = item_font.render(prompt, True, t.menu_selected)
        room = box_width - 16
        if prompt_surf.get_width() > room:
            prompt_surf = prompt_surf.subsurface(
                (prompt_surf.get_width() - room, 0, room,
                 prompt_surf.get_height()))
        surface.blit(prompt_surf, (box_left + 8, box_top + 6))

        hint = ("ENTER: search  |  Ctrl+V: paste a link  |  "
                "Ctrl+C: copy this screen  |  ESC: back")
        hint_surf = hint_font.render(hint, True, t.hud_text)
        surface.blit(hint_surf, (w // 2 - hint_surf.get_width() // 2, h - 36))

    def _render_results(self, surface, w, h, item_font, hint_font, t) -> None:
        marked = any(r.by_id for r in self._results)
        said = (f"★ the song your link names"
                + (f", and {len(self._results) - 1} search hits"
                   if len(self._results) > 1 else "")
                if marked else
                f"{len(self._results)} results for \"{self._query}\"")
        sub_surf = hint_font.render(said, True, t.hud_text)
        surface.blit(sub_surf, (w // 2 - sub_surf.get_width() // 2, 68))

        list_top = 110
        item_h = 30
        list_left = 60
        list_width = w - 120

        visible_end = min(self._scroll_offset + VISIBLE_ITEMS, len(self._results))
        for i in range(self._scroll_offset, visible_end):
            y = list_top + (i - self._scroll_offset) * item_h
            result = self._results[i]

            if i == self._selected:
                pygame.draw.rect(
                    surface, t.menu_selected_bg,
                    (list_left - 8, y, list_width, item_h),
                    border_radius=4,
                )
                color = t.menu_selected
            else:
                color = t.menu_item

            # Marked, because the whole point of pasting a link is that
            # THIS one is the version he chose. A row that looks like the
            # four search hits under it does not say that.
            name = f"{result.artist} - {result.title}" if result.artist \
                else result.title
            label = f"★ {name}  (s{result.song_id})" if result.by_id else name
            text_surf = item_font.render(label, True, color)
            surface.blit(text_surf, (list_left, y + 4))

        # Scroll indicators
        if self._scroll_offset > 0:
            arrow = hint_font.render("▲ more", True, t.hud_text)
            surface.blit(arrow, (w // 2 - arrow.get_width() // 2, list_top - 20))
        if visible_end < len(self._results):
            arrow = hint_font.render("▼ more", True, t.hud_text)
            y_bottom = list_top + VISIBLE_ITEMS * item_h + 4
            surface.blit(arrow, (w // 2 - arrow.get_width() // 2, y_bottom))

        hint = ("UP/DOWN: navigate  |  ENTER: tab + sync + audio  |  "
                "ESC: back to search")
        hint_surf = hint_font.render(hint, True, t.hud_text)
        surface.blit(hint_surf, (w // 2 - hint_surf.get_width() // 2, h - 36))

    def _render_status(self, surface, w, h, item_font, hint_font, t) -> None:
        msg_surf = item_font.render(self._status_msg, True, t.hud_text)
        surface.blit(msg_surf, (w // 2 - msg_surf.get_width() // 2, h // 2))

        # Four network steps in a row, one of them a whole song's audio.
        # Without a bar that is thirty seconds of a frozen-looking screen,
        # and the player kills the app.
        fraction, _ = self._progress
        if self._downloading:
            bar_w = min(420, w - 120)
            bar_x = w // 2 - bar_w // 2
            bar_y = h // 2 + 40
            pygame.draw.rect(surface, t.menu_selected_bg,
                             (bar_x, bar_y, bar_w, 10), border_radius=5)
            done = int(bar_w * max(0.0, min(1.0, fraction)))
            if done > 0:
                pygame.draw.rect(surface, t.hud_accent,
                                 (bar_x, bar_y, done, 10), border_radius=5)

        if not self._searching and not self._downloading:
            hint = "ESC: back"
            hint_surf = hint_font.render(hint, True, t.hud_text)
            surface.blit(hint_surf, (w // 2 - hint_surf.get_width() // 2, h - 36))

    def _render_done(self, surface, w, h, item_font, hint_font, t) -> None:
        """What arrived, line by line.

        The tab always did. Whether the bar map and the audio came with it is
        the whole reason this screen exists: a song that quietly arrived
        without its recording looks exactly like one that did not, until the
        player is standing in front of it with a guitar.
        """
        head = item_font.render(self._status_msg, True, t.hud_accent)
        surface.blit(head, (w // 2 - head.get_width() // 2, 110))

        top = 170
        for i, note in enumerate(self._notes):
            line = hint_font.render(note, True, t.hud_text)
            surface.blit(line, (w // 2 - line.get_width() // 2, top + i * 26))

        hint = "Ctrl+C: copy this screen  |  any key: back to the songs"
        hint_surf = hint_font.render(hint, True, t.hud_text)
        surface.blit(hint_surf, (w // 2 - hint_surf.get_width() // 2, h - 36))

    def _ensure_visible(self) -> None:
        if self._selected < self._scroll_offset:
            self._scroll_offset = self._selected
        elif self._selected >= self._scroll_offset + VISIBLE_ITEMS:
            self._scroll_offset = self._selected - VISIBLE_ITEMS + 1
