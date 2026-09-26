"""Song selection menu screen.

Scans a directory for GP3/GP4/GP5 files and provides keyboard/mouse navigation.
"""

from __future__ import annotations

from pathlib import Path

import pygame

from pickhero.audio.input import list_audio_devices
from pickhero.audio.note_utils import tuning_label
from pickhero.config import Config
from pickhero.progress import ProgressTracker
# .gpx is Guitar Pro 6. It was missing from this set for as long as the
# app had existed, so those files never appeared in the list to be
# opened. It lives with the loader now: what the app can READ is a
# property of the reader, and the downloader needs the same answer.
from pickhero.tabs.loader import GP_EXTENSIONS
from pickhero.tabs.song_index import SongIndex
from pickhero.ui import chips
from pickhero.ui.colors import cycle_theme, get_theme
from pickhero.ui.footer import wrap_on_bars
from pickhero.ui.keys import shift_held

#: The keys that act on the LIST rather than on the text in the box. Pressing
#: one lets go of the box without touching the filter it is holding.
LIST_KEYS = (pygame.K_UP, pygame.K_DOWN, pygame.K_PAGEUP, pygame.K_PAGEDOWN,
             pygame.K_HOME, pygame.K_END)


# How many items visible at once before scrolling
VISIBLE_ITEMS = 18

#: Rows one notch of the mouse wheel moves. Three is what every list on this
#: machine does, and a list is read by comparing neighbours -- a wheel that
#: jumped a screenful would be a second Page Down rather than a way to browse.
WHEEL_ROWS = 3

SORT_MODES = ["name_asc", "name_za", "accuracy", "last_played"]
SORT_LABELS = {
    "name_asc": "Name A-Z",
    "name_za": "Name Z-A",
    "accuracy": "Best %",
    "last_played": "Recent",
}


def _get_font(name: str, size: int) -> pygame.font.Font:
    """Try to load a system font with fallbacks."""
    for family in (name, "Courier New", "monospace"):
        font = pygame.font.SysFont(family, size)
        if font:
            return font
    return pygame.font.Font(None, size)


class MenuScreen:
    """File browser for selecting GP tab files."""

    def __init__(self, songs_dir: Path, config: Config | None = None,
                 progress: ProgressTracker | None = None):
        self._songs_dir = Path(songs_dir)
        self._config = config
        self._progress = progress
        self._sort_mode: str = config.sort_mode if config else "name_asc"
        self._files: list[Path] = []
        # What the last F5 found, shown until the next keypress. A refresh
        # that looks like nothing happened is indistinguishable from a dead
        # key, and this one usually finds exactly one new file.
        self._reload_note: str = ""
        #: How many songs took settings out of the folder on the last scan.
        self._adopted: int = 0
        #: How many rows the last frame had room for. VISIBLE_ITEMS until
        #: something has been drawn -- a keypress can arrive first.
        self._visible_items: int = VISIBLE_ITEMS
        #: True while the DEL that armed a delete is still physically down.
        #: The confirmation must be a press the player MADE -- see
        #: `_delete_selected`.
        self._delete_key_down: bool = False
        #: And how many had their settings written back into it.
        self._backfilled: int = 0
        #: The last import's report, shown over the list until a key is
        #: pressed. A multi-line answer, because "23 songs, 140 sittings,
        #: 4 better scores" is four facts and the note line holds one.
        self._transfer_lines: list[str] = []
        #: The song being renamed and the name being typed, or None. A tab
        #: downloaded from Songsterr arrives called whatever Songsterr calls
        #: it, and that name is the song's identity everywhere: `song_key` IS
        #: the stem, so a tidy-up in Explorer silently orphans the speed, the
        #: recording, the sync points and the practice history.
        self._renaming = None
        self._rename_text: str = ""
        #: Where the next character goes, 0..len(text). Without one the
        #: editor could only ever grow and shrink at the end.
        self._rename_caret: int = 0
        #: The song DEL is waiting for a second press on, or None. Deleting
        #: is the one thing on this screen that cannot be undone, and DEL
        #: sits one row from the arrow keys -- so it asks first, and the
        #: question names the song and counts the files.
        self._delete_armed = None
        # How many instruments each song holds and how each is tuned, read on
        # a thread and remembered between sessions. See tabs/song_index.py.
        self._index = SongIndex()
        self._tuning_filter: str = ""
        self._favourites_only: bool = False
        self._new_only: bool = False
        #: The footer as it was last drawn, so a test can ask
        #: whether every key it names really fits the window.
        self._last_hint: str = ""
        #: Where the search box was last drawn, so a click can be told to be
        #: inside or outside it. Set by render, the way the list rows are.
        self._search_box = None
        #: The tuning chips, as the last frame placed them. What a
        #: click is tested against -- the drawing and the mouse read
        #: the same list, so they cannot disagree about where one is.
        self._tuning_chips: list[chips.Chip] = []
        self._search_text: str = ""
        self._search_active: bool = False
        self._filtered_files: list[Path] = []
        self._selected = 0
        self._scroll_offset = 0
        self._last_click_time = 0
        self._device_name = self._resolve_device_name()
        self.scan_files()

    def refresh_today(self) -> None:
        """Re-read the practice diary for today's totals.

        The file is written when a song is LEFT (`close_session`), and leaving
        a song lands here -- so this is called on every entry and on every
        reload rather than once at startup. **A number that is right in the
        file and stale on the screen is indistinguishable from a diary that
        loses sittings**, which this project has already shipped once: the
        dashboard was rebuilt only when the app closed, so twelve sittings
        totalling 10.1 minutes read as three.

        Never per frame: it reads the whole log.
        """
        from pickhero import practice_log
        try:
            self._today = practice_log.today()
        except Exception:
            self._today = None          # a diary is never why the list fails

    def _today_line(self) -> str:
        """Today, in the three numbers that were asked for.

        Silent on a day nobody has played: a permanent "0 min - 0 songs -
        0 strikes" is clutter on a screen that was cut down on purpose, and
        the first sitting makes it appear.
        """
        total = getattr(self, "_today", None)
        if total is None or total.seconds <= 0:
            return ""
        songs = len(total.songs)
        return (f"today: {total.minutes:.0f} min  ·  "
                f"{songs} song{'' if songs == 1 else 's'}  ·  "
                f"{total.strikes} strikes")

    def refresh_device_name(self) -> None:
        """Re-resolve the current device name (call after device selection)."""
        self._device_name = self._resolve_device_name()

    def _resolve_device_name(self) -> str:
        """Return a display name for the current audio device."""
        if self._config is None:
            return "Default"
        idx = self._config.audio.device_index
        if idx is None:
            return "System Default"
        try:
            for dev in list_audio_devices():
                if dev["index"] == idx:
                    return dev["name"]
        except Exception:
            pass
        return f"Device #{idx}"

    @property
    def _display_files(self) -> list[Path]:
        """Return the currently visible file list (filtered or full)."""
        return self._filtered_files

    @property
    def is_searching(self) -> bool:
        """True when search mode is active."""
        return self._search_active

    @property
    def is_renaming(self) -> bool:
        """True while a song's name is being typed."""
        return self._renaming is not None

    @property
    def is_typing(self) -> bool:
        """True when a letter belongs to a text box and not to a shortcut.

        `is_searching` used to be that test, and then the rename editor
        arrived and was not part of it -- so `o` in the middle of typing a
        name opened the settings screen, `s` the downloader and `d` the
        device list. The App asks this now, so a text box added later is
        covered by being a text box rather than by somebody remembering to
        come back here.
        """
        return self._search_active or self.is_renaming

    def _apply_filter(self) -> None:
        """Filter _files by search text and tuning, sort, reset selection."""
        if self._search_text:
            query = self._search_text.lower()
            self._filtered_files = [
                p for p in self._files if query in p.stem.lower()
            ]
        else:
            self._filtered_files = list(self._files)
        if self._favourites_only and self._config is not None:
            self._filtered_files = [
                p for p in self._filtered_files
                if self._config.is_favourite(p.stem)
            ]
        if self._new_only and self._config is not None:
            self._filtered_files = [
                p for p in self._filtered_files if self._is_new(p)
            ]
        if self._tuning_filter:
            # A song still being indexed is kept OUT rather than shown: while
            # the filter is on, a row with no answer yet would look like an
            # answer of "yes", and the count beside it would be wrong.
            self._filtered_files = [
                p for p in self._filtered_files
                if self._index.has(p, self._tuning_filter)
            ]
        self._sort_files()
        self._selected = 0
        self._scroll_offset = 0

    def _sort_files(self) -> None:
        """Sort _filtered_files by current sort mode."""
        mode = self._sort_mode
        if mode == "name_asc":
            self._filtered_files.sort(key=lambda p: p.name.lower())
        elif mode == "name_za":
            self._filtered_files.sort(key=lambda p: p.name.lower(), reverse=True)
        elif mode == "accuracy":
            def acc_key(p: Path) -> tuple[int, float]:
                rec = self._progress.get_best(p.stem) if self._progress else None
                if rec and rec.attempts > 0:
                    return (0, -rec.best_accuracy)  # played: sort by accuracy desc
                return (1, 0.0)  # unplayed at bottom
            self._filtered_files.sort(key=acc_key)
        elif mode == "last_played":
            def played_key(p: Path) -> tuple[int, str]:
                rec = self._progress.get_best(p.stem) if self._progress else None
                if rec and rec.last_played:
                    return (0, rec.last_played)
                return (1, "")
            # Most recent first: reverse so latest ISO date comes first
            self._filtered_files.sort(key=played_key, reverse=True)

    def _cycle_sort(self) -> None:
        """Advance to the next sort mode."""
        idx = SORT_MODES.index(self._sort_mode) if self._sort_mode in SORT_MODES else 0
        self._sort_mode = SORT_MODES[(idx + 1) % len(SORT_MODES)]
        self._sort_files()
        self._selected = 0
        self._scroll_offset = 0
        if self._config:
            self._config.sort_mode = self._sort_mode
            self._config.save()

    def scan_files(self) -> None:
        """Scan songs directory (recursively) for GP files, and read the diary.

        Both, because leaving a song calls this and leaving a song is where
        the sitting was just written -- so the count on screen is the count in
        the file rather than the one from when the app started.

        A folder that cannot be read is an EMPTY LIST and a line saying so,
        never an exception. This used to call mkdir(parents=True) and die
        before the first frame when the folder could not be made -- see
        Config.songs_path, which is where the folder is chosen now.
        """
        try:
            self._songs_dir.mkdir(parents=True, exist_ok=True)
            # NOT what was deleted. `Config.songs_path` falls back to the
            # folder beside settings.json when the configured one cannot be
            # made, and the trash lives under that same folder -- so without
            # this a deleted song walks straight back into the list, and
            # deleting it again would only move it deeper.
            from pickhero.tabs.remove import trash_root
            trash = trash_root().resolve()
            found = sorted(
                p
                for p in self._songs_dir.rglob("*")
                if p.is_file() and p.suffix.lower() in GP_EXTENSIONS
                and trash not in p.resolve().parents
            )
        except OSError as exc:
            self._reload_note = (f"Cannot read {self._songs_dir} — "
                                 f"{exc.strerror or exc}")
            found = []
        self._files = found
        self.refresh_today()
        # Counted, not announced from in here. `reload_files` OWNS the note
        # -- it returns one and the caller displays it -- so a message set
        # here is either overwritten immediately or, worse, left standing
        # after the next scan that found nothing.
        self._adopted = self._adopt_sidecars(found)
        # And the other direction: a song whose settings only exist in
        # settings.json gets its file written NOW rather than the next time
        # it happens to be opened and left. Without this, "every song has
        # four files" was a promise that came true one song at a time, in
        # whatever order they were played -- and the player would have had
        # to visit every one before copying anything anywhere.
        self._backfilled = self._write_missing_sidecars(found)
        if self._adopted or self._backfilled:
            self._reload_note = self._adopted_note()
        self._search_text = ""
        self._search_active = False
        self._index.scan_in_background(self._files)
        self._apply_filter()

    def set_songs_dir(self, folder) -> str:
        """Look somewhere else from now on. Returns what it found there.

        The search and the cursor are dropped on purpose: they belong to the
        list that was being looked at, and this is a different one.
        """
        self._songs_dir = Path(folder)
        self._search_text, self._search_active = "", False
        self._selected = self._scroll_offset = 0
        return self.reload_files()

    def reload_files(self) -> str:
        """Read the folder again without losing the player's place (F5).

        A file copied in while the app is open was invisible until it was
        restarted. Rescanning is the whole of the work; what takes care is
        everything around it.

        The search is KEPT. Dropping a new song in the middle of hunting for
        one and coming back to an unfiltered list means typing it all again,
        and the new file is very likely the one being searched for.

        The selection is kept too, by name rather than by row: the list is
        sorted, so a file added above the cursor moves every row below it and
        the highlight would land on a different song than the one it was on.

        And it says what it found. A refresh that looks exactly like no
        refresh cannot be told apart from a dead key -- the same rule as every
        other silent failure in this app.
        """
        before = set(self._files)
        selected = self._selected_path()
        text, active = self._search_text, self._search_active
        self.scan_files()
        self._search_text, self._search_active = text, active
        self._apply_filter()
        self._select_path(selected)
        added = len(set(self._files) - before)
        gone = len(before - set(self._files))
        parts = [f"{len(self._files)} songs"]
        if added:
            parts.append(f"{added} new")
        if gone:
            parts.append(f"{gone} gone")
        if not added and not gone:
            parts.append("nothing changed")
        if self._adopted:
            parts.append(f"{self._adopted} picked up settings")
        if self._backfilled:
            parts.append(f"{self._backfilled} got a settings file")
        return "Reloaded: " + ", ".join(parts)

    def _blit_transfer_report(self, surface, w, h, item_font, hint_font,
                              t) -> None:
        """What the last import or export did, over the list until a key.

        One panel for both directions, because they answer the same question
        -- what just moved -- and two would be two places to look.
        """
        line_h = 26
        block = len(self._transfer_lines) * line_h + 56
        top = max(80, h // 2 - block // 2)
        panel = pygame.Rect(40, top - 20, w - 80, block)
        pygame.draw.rect(surface, t.menu_bg, panel, border_radius=6)
        pygame.draw.rect(surface, t.hud_accent, panel, width=1,
                         border_radius=6)
        for i, line in enumerate(self._transfer_lines):
            font = item_font if i == 0 else hint_font
            colour = t.hud_accent if i == 0 else t.hud_text
            drawn = font.render(line, True, colour)
            surface.blit(drawn, (w // 2 - drawn.get_width() // 2,
                                 top + i * line_h))
        hint = hint_font.render("Any key closes this", True, t.hud_text)
        surface.blit(hint, (w // 2 - hint.get_width() // 2,
                            top + len(self._transfer_lines) * line_h + 8))

    def _is_new(self, path) -> bool:
        """Is this song still one the player has not started?

        One implementation, asked by the row, the filter and both keys. The
        rule itself lives on the config; what belongs here is the only thing
        this screen knows that it does not -- whether the song was ever
        played.
        """
        if self._config is None or path is None:
            return False
        record = self._progress.get_best(path.stem) if self._progress else None
        played = record is not None and record.attempts > 0
        return self._config.is_new(path.stem, played)

    def _set_new(self, new: bool) -> None:
        """Mark the selected song new, or take the mark off (Ctrl+N / Ctrl+Shift+N).

        Ctrl and not Shift, for the reason `Ctrl+M` is: `Shift+N` is how a
        capital N is typed, and a filter box that cannot spell "Nirvana" is
        not a filter box. The plain `N` is the sort key and stays there --
        a shortcut that has been under the player's fingers for months is
        not worth taking for a mnemonic.

        Set and unset rather than a toggle, again like the favourite: while
        the filter box is open the note under it is the last thing being
        read, so a toggle means finding out afterwards which way it went.
        """
        if self._config is None:
            return
        song = self._selected_path()
        if song is None:
            self.say("Nothing selected")
            return
        if self._is_new(song) == new:
            self.say(("Already new: " if new
                      else "Not marked new anyway: ") + song.stem[:40])
            return
        self._config.set_new(song.stem, new)
        self._config.save()
        self._write_sidecar(song)
        self.say(("Marked new: " if new else "No longer new: ")
                 + song.stem[:40])
        if self._new_only:
            # It has just left the list it is being shown in, so the list has
            # to be rebuilt and the cursor put somewhere that exists.
            self._apply_filter()
            self._select_path(song)

    def _toggle_new_only(self) -> None:
        """Show only the songs still marked new, or all of them again (Shift+N).

        Refuses when nothing is new: a filter that empties the list looks
        exactly like a list that has lost its songs.
        """
        if self._config is None:
            return
        if not self._new_only and not any(self._is_new(p) for p in self._files):
            self._reload_note = "Nothing is marked new"
            return
        selected = self._selected_path()
        self._new_only = not self._new_only
        self._apply_filter()
        self._select_path(selected)

    def _toggle_favourite(self) -> None:
        """Star the selected song, or take the star off (M).

        One line, because `_set_favourite` is the same work said explicitly
        and two copies of it would drift.
        """
        if self._config is None:
            return
        song = self._selected_path()
        if song is not None:
            self._set_favourite(not self._config.is_favourite(song.stem))

    def _set_favourite(self, starred: bool) -> None:
        """Star the selected song, or take the star off. No toggling.

        *"Im Filter kann ich keine Favoriten setzen."*

        `M` toggles, and a toggle is the wrong shape for this: while the
        filter box is open the note is the last thing being read, so
        pressing it means finding out afterwards which way it went. Two
        keys that SAY what they do can be pressed without looking, and
        pressing the same one twice is harmless.

        Ctrl, not Shift: a Ctrl combination produces no character, so it
        works mid-word. `Shift+M` is how a capital M is typed, and a filter
        box that cannot spell Metallica is a filter box.
        """
        if self._config is None:
            return
        song = self._selected_path()
        if song is None:
            self.say("Nothing selected")
            return
        if self._config.is_favourite(song.stem) == starred:
            self.say(("Already a favourite: " if starred
                      else "Not a favourite anyway: ") + song.stem[:40])
            return
        self._config.set_favourite(song.stem, starred)
        self._config.save()
        self._write_sidecar(song)
        self.say(("Favourite: " if starred else "No longer a favourite: ")
                 + song.stem[:40])
        if self._favourites_only:
            # It has just left the list it is being shown in, so the list
            # has to be rebuilt and the cursor put somewhere that exists.
            self._apply_filter()
            self._select_path(song)

    def _toggle_favourites_only(self) -> None:
        """Show only the starred songs, or all of them again (Shift+M).

        Refuses when nothing is starred: a filter that empties the list looks
        exactly like a list that has lost its songs.
        """
        if self._config is None:
            return
        if not self._favourites_only and not any(
                self._config.is_favourite(p.stem) for p in self._files):
            self._reload_note = "No favourites yet — M marks the selected song"
            return
        selected = self._selected_path()
        self._favourites_only = not self._favourites_only
        self._apply_filter()
        self._select_path(selected)

    def _set_tuning_filter(self, tuning: str) -> None:
        """Show one tuning, or all of them when it is already the one shown.

        Clicking the chip that is already on means "all", so the strip needs
        no separate way to switch the filter off -- and pressing a chip twice
        is harmless rather than a state nobody chose.
        """
        selected = self._selected_path()
        self._tuning_filter = "" if tuning == self._tuning_filter else tuning
        self._reload_note = ""
        self._apply_filter()
        self._select_path(selected)

    def _cycle_tuning_filter(self) -> None:
        """All songs -> each tuning in turn -> all songs again.

        Built from the tunings actually present, so it can never offer one
        that would empty the list, and it is rebuilt on every press: a song
        indexed since the last one has to be able to join.
        """
        selected = self._selected_path()
        options = self._index.tunings_present(self._files)
        if not options:
            self._tuning_filter = ""
            self._reload_note = ("Tunings are still being read"
                                 if self._index.busy else "No tunings known yet")
            return
        try:
            position = options.index(self._tuning_filter) + 1
        except ValueError:
            position = 0
        self._tuning_filter = "" if position >= len(options) else options[position]
        self._apply_filter()
        self._select_path(selected)

    def _selected_path(self):
        """The song under the cursor, or None when the list is empty."""
        files = self._display_files
        if not files or not (0 <= self._selected < len(files)):
            return None
        return files[self._selected]

    def selected_tuning(self) -> tuple[str, str]:
        """(open strings, song name) of the song under the cursor.

        For the tuner, which the player opens from here: the list already
        shows every song's tuning, so asking them to dial it in again is
        asking for something this screen has. Empty when the song has not
        been read yet, or holds no guitar -- and an empty answer is what
        makes the tuner fall back to Standard.
        """
        path = self._selected_path()
        if path is None:
            return "", ""
        info = self._index.get(path)
        if info is None or not info.distinct_tunings:
            return "", path.stem
        return info.distinct_tunings[0], path.stem

    def _select_path(self, path) -> None:
        """Put the cursor back on this song, or leave it where it fits."""
        files = self._display_files
        if not files:
            self._selected = 0
            self._scroll_offset = 0
            return
        if path is not None and path in files:
            self._selected = files.index(path)
        else:
            self._selected = min(self._selected, len(files) - 1)
        self._ensure_visible()

    def say(self, text: str) -> None:
        """Put one line under the list, until the player presses anything.

        The same line reload_files and the favourites use. A screen with one
        place for a note is a screen where the note is always in the same
        place.
        """
        self._reload_note = text

    def _import_from_folder(self) -> None:
        """Ctrl+I: take another computer's songs and history from a folder.

        A laptop that cannot have a cloud client installed still has a USB
        stick, and a stick is a real folder. Nothing is overwritten and a
        `.bak` is left beside anything rewritten, so this runs in one step
        rather than asking first -- the report says what happened, which is
        the thing that actually needs seeing.
        """
        if self._config is None:
            return
        from pickhero.transfer import import_from
        from pickhero.ui.filepick import pick_folder
        self.say("Opening the folder chooser…")
        chosen = pick_folder("Folder from the other computer",
                             str(self._songs_dir))
        # Every key repeat that arrived while the dialog held the app is
        # still queued, and each one would open it again -- the same bill
        # the recording chooser already paid.
        try:
            pygame.event.clear(pygame.KEYDOWN)
            pygame.event.clear(pygame.KEYUP)
        except Exception:
            pass
        self._reload_note = ""
        if not chosen:
            return                     # cancelled, or no desktop to ask
        report = import_from(chosen, self._config)
        self._transfer_lines = report.lines()
        if report.songs_added:
            self.reload_files()        # the new songs have to appear

    def _export_to_folder(self) -> None:
        """Ctrl+E: write this machine's history and runs into a folder.

        *"Da gp, mp3, songsterr schon auf NB2 sind, sehe ich keinen Grund
        diese jedes Mal mitzukopieren."* Right -- and hand-copying the two
        files that ARE needed did not work, because the import walked tabs
        and a folder of histories with no tab beside them read as empty.

        The mirror of Ctrl+I in every respect: the same folder chooser, the
        same panel, the same key-repeat drain. What it writes is what an
        import reads.
        """
        if self._config is None:
            return
        from pickhero.transfer import export_to
        from pickhero.ui.filepick import pick_folder
        self.say("Opening the folder chooser…")
        chosen = pick_folder("Folder to write your history into",
                             str(self._songs_dir))
        try:
            pygame.event.clear(pygame.KEYDOWN)
            pygame.event.clear(pygame.KEYUP)
        except Exception:
            pass
        self._reload_note = ""
        if not chosen:
            return                     # cancelled, or no desktop to ask
        self._transfer_lines = export_to(chosen, self._config).lines()

    def _start_rename(self) -> None:
        """R: edit the name of the song under the cursor."""
        path = self._selected_path()
        if path is None:
            self.say("Nothing selected")
            return
        self._renaming = path
        self._rename_text = path.stem
        # At the END, because the usual edit is trimming what Songsterr
        # called it -- " v3" off the back.
        self._rename_caret = len(self._rename_text)
        self._delete_armed = None

    def _handle_rename_key(self, event) -> None:
        """Type, move, ENTER to keep, ESC to drop it.

        **A caret, not an append.** The first version could only add at the
        end and rub out from the end, so fixing the FRONT of a name meant
        deleting the whole thing and typing it again -- and the names that
        need fixing are Songsterr's, where the artist is at the front. Every
        key here is what it is in any other text field: arrows move, Home
        and End jump, Backspace eats behind, Delete eats in front.
        """
        from pickhero.tabs.remove import safe_name
        text, caret = self._rename_text, self._rename_caret

        if event.key == pygame.K_ESCAPE:
            self._renaming = None
            self.say("Rename cancelled")
            return None
        if event.key == pygame.K_RETURN:
            self._finish_rename()
            return None
        if event.key == pygame.K_LEFT:
            self._rename_caret = max(0, caret - 1)
            return None
        if event.key == pygame.K_RIGHT:
            self._rename_caret = min(len(text), caret + 1)
            return None
        if event.key == pygame.K_HOME:
            self._rename_caret = 0
            return None
        if event.key == pygame.K_END:
            self._rename_caret = len(text)
            return None
        if event.key == pygame.K_BACKSPACE:
            if caret:
                self._rename_text = text[:caret - 1] + text[caret:]
                self._rename_caret = caret - 1
            return None
        if event.key == pygame.K_DELETE:
            # In here it is a text key, and the song list's own DEL never
            # sees it: the editor owns every key while it is open.
            self._rename_text = text[:caret] + text[caret + 1:]
            return None

        ch = event.unicode
        if ch and ch.isprintable() and ch not in ("\r", "\n", "\t"):
            # Refused as it is typed, not when ENTER fails. A colon is a
            # rename that dies with a Windows error nobody can read, and the
            # place to say so is the moment the key is pressed.
            if safe_name(ch) or ch == " ":
                self._rename_text = text[:caret] + ch + text[caret:]
                self._rename_caret = caret + 1
            else:
                self.say(f"{ch} cannot be in a file name")
        return None

    def _finish_rename(self) -> None:
        """Move the tab and everything keyed to its name."""
        from pickhero.tabs.remove import rename_song
        path, self._renaming = self._renaming, None
        if path is None:
            return
        report = rename_song(path, self._rename_text, self._config)
        # From the DISK. A rename that half happened must show itself rather
        # than be claimed as finished.
        self.reload_files()
        if report.tab_path is not None:
            self._select_path(report.tab_path)
        self.say(report.summary())

    def _delete_selected(self) -> None:
        """First DEL asks, second DEL does it.

        The question names the song and counts the files, because a tab is
        not one file any more -- the download screen writes a bar map and an
        MP3 beside it, and "delete Thunder" meaning three files is worth
        seeing before it happens rather than after.
        """
        from pickhero.tabs.remove import belongings, delete_song
        path = self._selected_path()
        if path is None:
            self.say("Nothing selected")
            return

        if self._delete_armed != path:
            self._delete_armed = path
            self._delete_key_down = True
            count = len(belongings(path))
            what = f"{count} file" + ("s" if count != 1 else "")
            self.say(f"Delete {path.stem} and {what}? "
                     f"DEL again to confirm, any other key cancels. "
                     f"Your practice history is kept.")
            return

        if self._delete_key_down:
            # The SAME press, arriving again. `NEVER_REPEAT` in App already
            # drops these, and this is the second lock on the same door
            # because the first one is somewhere else: a screen is only ever
            # as safe as whatever is handing it events, and a test, a tool or
            # a future dispatcher calling this directly must not be able to
            # delete a folder of songs with one finger.
            #
            # The rule is physical, not a timer: the confirmation has to be
            # a press the player MADE, which is what a KEYUP in between says
            # and what a stalled frame draining a burst of repeats cannot
            # fake. Same reason the tempo gate waits for its key to come up.
            return

        self._delete_armed = None
        report = delete_song(path, self._config)
        # From the DISK, not from the list in memory. A file that would not
        # delete is still there, and a list that quietly dropped it would be
        # claiming a delete that did not happen.
        self.reload_files()
        self.say(report.summary())

    def _undo_delete(self) -> None:
        """Ctrl+Z: put the last deleted song back.

        `Path.unlink()` never reached the Windows recycle bin, so DEL was
        the one irreversible key in the app -- and it sits one row from the
        arrow keys. Ctrl rather than a plain letter for the reason Ctrl+M
        and Ctrl+N are: it produces no character, so it cannot be a key the
        filter box wanted.
        """
        from pickhero.tabs.remove import restore_last
        report = restore_last(self._songs_dir)
        self.reload_files()
        if report.files:
            self._select_path(report.files[0])
            self.say(f"Put {report.song_key} back — {len(report.files)} file"
                     f"{'' if len(report.files) == 1 else 's'}"
                     + (f". Not: {', '.join(report.failed)}"
                        if report.failed else ""))
        else:
            self.say("Nothing to put back"
                     + (f" — {', '.join(report.failed)}"
                        if report.failed and report.failed != ["nothing to put back"]
                        else ""))

    def _hint_text(self) -> str:
        """The footer's shortcuts, as one string.

        Pulled out of the drawing so the ROOM it needs can be asked
        for before the list is laid out, and the drawing and the
        measurement cannot read two different strings. Same seam as
        `_footer_block` on the playing screen, for the same reason.
        """
        if self._search_active:
            return "Type to search  |  UP/DOWN or a click: leave the box, keep the filter  |  TAB or click: tuning  |  Shift+U: tuner (keeps the search)  |  Ctrl+M: favourite (Ctrl+Shift+M: not)  |  Ctrl+N: not new (Ctrl+Shift+N: new)  |  DEL: delete song (Ctrl+Z undo)  |  Ctrl+I: import  |  Ctrl+E: export history  |  Ctrl+C: copy screen  |  F5: reload list  |  BACKSPACE: edit  |  ESC: clear  |  ENTER: select  |  UP/DOWN: navigate"
        else:
            sort_label = SORT_LABELS.get(self._sort_mode, "Name A-Z")
            tune_label = self._tuning_filter or "all"
            fav = "on" if self._favourites_only else "off"
            new_f = "on" if self._new_only else "off"
            return f"F or /: search  |  M: favourite (Ctrl+M / Ctrl+Shift+M set / unset, Shift+M: only, {fav})  |  Ctrl+N / Ctrl+Shift+N: not new / new (Shift+N: only, {new_f})  |  TAB or a click: tuning ({tune_label})  |  R: rename  |  DEL: delete song (Ctrl+Z undo)  |  Ctrl+I: import from another PC  |  Ctrl+E: export history  |  Ctrl+C: copy screen  |  F5: reload list  |  N: sort ({sort_label})  |  ENTER: select  |  O: settings  |  S: get a song (tab+sync+audio)  |  D: audio device  |  U: tuner (Shift+U while searching)  |  G: calibrate  |  T: theme  |  ESC: quit"

    def _bottom_height(self, w: int, hint_font) -> int:
        """How much of the window the block along the bottom edge takes.

        The footer WRAPS -- 2554 px of shortcuts on a 1920 window -- so its
        height is a measurement and never a constant, and the device line and
        the scoring hint stack on top of it. Asked before the list is laid
        out, so the list can be given what is left instead of being drawn
        through it, which is what the player's screenshot showed.
        """
        lines = wrap_on_bars(self._hint_text(), hint_font, w - 24)
        line_h = hint_font.get_height() + 2
        # the footer itself, its 36 px bottom margin, and the two lines above
        return (36 + len(lines) * line_h
                + 2 * (hint_font.get_height() + 4) + 8)

    def _write_sidecar(self, path) -> None:
        """Put this song's settings back beside the song. Never raises."""
        if self._config is None or path is None:
            return
        try:
            from pickhero.tabs import sidecar
            best = None
            try:
                from pickhero.progress import ProgressTracker
                best = ProgressTracker().get_best(path.stem)
            except Exception:
                pass
            sidecar.write(path, path.stem, self._config, best)
        except Exception:
            pass

    def _adopt_sidecars(self, found) -> int:
        """Take the settings that travelled with the songs.

        Here rather than when a song is opened, because a star has to show
        in the LIST -- and "my favourites are gone" is what copying the
        folder to the second laptop used to look like.

        Only what this machine has no answer for; `sidecar.adopt` is the one
        that decides. Nothing is written back: a folder that is read
        produces no cloud conflict.
        """
        if self._config is None:
            return 0
        from pickhero.tabs import sidecar
        taken = 0
        for path in found:
            try:
                if sidecar.adopt(path, path.stem, self._config):
                    taken += 1
            except Exception:
                continue          # one bad file is not a broken song list
        if not taken:
            return 0
        try:
            self._config.save()
        except OSError:
            return 0
        return taken

    def _adopted_note(self) -> str:
        """What the last scan took out of, and put into, the songs folder."""
        parts = []
        if self._adopted:
            parts.append(f"picked up settings for {self._adopted} song"
                         + ("s" if self._adopted != 1 else ""))
        if self._backfilled:
            parts.append(f"wrote settings beside {self._backfilled} song"
                         + ("s" if self._backfilled != 1 else ""))
        return "Songs folder: " + ", ".join(parts)

    def _write_missing_sidecars(self, found) -> int:
        """Give every song that HAS settings a file to carry them in.

        Only where there is no sidecar yet. An existing one is left alone --
        it may have come from the other machine and be newer than anything
        here, and overwriting it on a scan would undo an import.
        """
        if self._config is None:
            return 0
        from pickhero.tabs import sidecar
        best_of = {}
        try:
            from pickhero.progress import ProgressTracker
            tracker = ProgressTracker()
            best_of = {p.stem: tracker.get_best(p.stem) for p in found}
        except Exception:
            pass
        written = 0
        for path in found:
            try:
                if sidecar.path_for(path).exists():
                    continue
                best = best_of.get(path.stem)
                if not sidecar.worth_writing(path.stem, self._config, best):
                    continue
                if sidecar.write(path, path.stem, self._config, best):
                    written += 1
            except Exception:
                continue          # one bad song is not a broken song list
        return written

    def copy_text(self) -> str:
        """The song list as text, with the note under it."""
        out = ["MySician — songs"]
        if self._search_text:
            out.append(f"Search: {self._search_text}")
        if self._reload_note:
            out.append(self._reload_note)
        out += [f"{'> ' if p == self._selected_path() else '  '}{p.stem}"
                for p in self._display_files]
        return "\n".join(out)

    def handle_event(self, event: pygame.event.Event) -> Path | str | None:
        """Process input. Returns Path (file selected), "escape" (quit), or None."""
        files = self._display_files

        if event.type == pygame.KEYUP and event.key == pygame.K_DELETE:
            # The finger came off. Only now may a second DEL be the
            # CONFIRMATION rather than the same press arriving again.
            self._delete_key_down = False
            return None

        if event.type == pygame.KEYDOWN:
            if event.key != pygame.K_F5:
                # A note is for what just happened, not for the rest of the
                # session. Any other key means the player has moved on.
                self._reload_note = ""
            # The editor owns every key while it is open. Before ESC, before
            # the search, before DEL -- otherwise typing a song's name is a
            # minefield of shortcuts, and `d` in "Thunderstruck" deletes it.
            if self._renaming is not None:
                return self._handle_rename_key(event)

            if event.key != pygame.K_DELETE:
                # Moving the cursor, searching, or anything else at all
                # answers "no". An armed delete must never outlive the
                # question that armed it, or the second DEL lands on a
                # different song than the one that was named.
                self._delete_armed = None
                self._delete_key_down = False
            if event.key == pygame.K_ESCAPE:
                # A filter left standing with the box let go is still a
                # filter, and ESC is what everybody presses to drop it. It
                # used to reach the quit prompt instead, which is a trap on
                # a screen the player arrives at with ESC under their finger.
                if self._search_active or self._search_text:
                    self._search_text = ""
                    self._search_active = False
                    self._apply_filter()
                    return None
                return "escape"

            # F, / and Ctrl+F all open the search. One of them is the key
            # this app happened to pick; the other two are the ones everybody
            # already has in their fingers.
            if not self._search_active and (
                    event.key == pygame.K_f
                    or event.unicode == "/"
                    or (event.key == pygame.K_f and event.mod & pygame.KMOD_CTRL)):
                # It RESUMES rather than clears. Leaving the box to press U
                # or M and coming back to narrow the filter further is the
                # whole workflow this was asked for; clearing it would make
                # the way back out a one-way door. ESC is what empties it.
                self._search_active = True
                self._apply_filter()
                return None

            # Ctrl+M stars, Ctrl+Shift+M unstars -- and BEFORE the search
            # guard below, because the whole point is that they work while
            # the filter box is open. Ctrl produces no character, so nothing
            # is stolen from the box: Shift+M is how a capital M is typed,
            # and a filter that cannot spell Metallica is not a filter.
            if event.key == pygame.K_n and event.mod & pygame.KMOD_CTRL:
                self._set_new(bool(event.mod & pygame.KMOD_SHIFT))
                return None

            if event.key == pygame.K_m and event.mod & pygame.KMOD_CTRL:
                self._set_favourite(not (event.mod & pygame.KMOD_SHIFT))
                return None

            # M marks, Shift+M filters. A letter is fine here because it is
            # only reached when the search box is closed -- and "merken" is
            # what the player calls it.
            if event.key == pygame.K_m and not self._search_active:
                if shift_held(event):
                    self._toggle_favourites_only()
                else:
                    self._toggle_favourite()
                return None

            # TAB steps through the tunings the folder actually contains,
            # ending back at all of them. Not a letter, for the same reason
            # as F5 -- and the same key that steps through a song's tracks
            # once one is open, which is the same idea one level up.
            if event.key == pygame.K_TAB:
                self._cycle_tuning_filter()
                return None

            # F5, the key everybody already reaches for. It cannot be a
            # letter: the search box takes those the moment it is open.
            if event.key == pygame.K_F5:
                self._reload_note = self.reload_files()
                return None

            # Any key clears the import report first -- it is a thing that
            # has been read, not a mode, so nothing should have to be aimed
            # at it.
            if self._transfer_lines:
                self._transfer_lines = []

            # Ctrl+I, because the laptop that needs it is the one with only
            # the .exe on it and no way to run a script. Ctrl so it works
            # with the filter box open, like Ctrl+M.
            if event.key == pygame.K_i and event.mod & pygame.KMOD_CTRL:
                self._import_from_folder()
                return None

            # Ctrl+E, the other direction, and Ctrl for the same reason: it
            # produces no character, so it works with the filter box open.
            if event.key == pygame.K_e and event.mod & pygame.KMOD_CTRL:
                self._export_to_folder()
                return None

            # R, next to DEL in what it touches: both act on the song under
            # the cursor and both move more than the file. A letter, so it
            # stays out of the search box.
            if event.key == pygame.K_r and not self._search_active:
                self._start_rename()
                return None

            # DEL, twice -- and while the filter box is open too. The first
            # version kept it out of there on the theory that DEL is a
            # typing key. It is not, in THIS box: backspace is what edits
            # the search text and DEL does nothing there, while "find the
            # song, then delete it" is the order somebody actually works in.
            if event.key == pygame.K_z and event.mod & pygame.KMOD_CTRL:
                self._undo_delete()
                return None
            if event.key == pygame.K_DELETE:
                self._delete_selected()
                return None

            if event.key == pygame.K_BACKSPACE:
                if self._search_active:
                    if self._search_text:
                        self._search_text = self._search_text[:-1]
                        self._apply_filter()
                    else:
                        self._search_active = False
                        self._apply_filter()
                return None

            # Moving through the list LETS GO of the box, and the filter
            # stays standing.
            #
            # *"Brauche eine Möglichkeit, dass der Textfilter F aktiv ist und
            # ich rausklicke und dann alle anderen Tasten funktionieren."*
            # The two states were always separate -- `_search_text` is the
            # filter and `_search_active` is only "the box owns the
            # letters" -- and nothing anywhere set the second one down
            # without also throwing the first one away. So a filtered list
            # could not be tuned from (`U`), starred (`M`) or sorted (`N`)
            # without clearing the filter first.
            #
            # One place rather than six, because a navigation key added
            # later would otherwise have to remember.
            if self._search_active and event.key in LIST_KEYS:
                self._search_active = False

            # Navigation keys
            if event.key == pygame.K_UP:
                if files:
                    self._selected = max(0, self._selected - 1)
                    self._ensure_visible()
                return None
            if event.key == pygame.K_DOWN:
                if files:
                    self._selected = min(len(files) - 1, self._selected + 1)
                    self._ensure_visible()
                return None
            if event.key == pygame.K_RETURN:
                if files:
                    return files[self._selected]
                return None
            if event.key == pygame.K_PAGEUP:
                if files:
                    self._selected = max(0, self._selected - self._visible_items)
                    self._ensure_visible()
                return None
            if event.key == pygame.K_PAGEDOWN:
                if files:
                    self._selected = min(
                        len(files) - 1, self._selected + self._visible_items
                    )
                    self._ensure_visible()
                return None
            if event.key == pygame.K_HOME:
                if files:
                    self._selected = 0
                    self._ensure_visible()
                return None
            if event.key == pygame.K_END:
                if files:
                    self._selected = len(files) - 1
                    self._ensure_visible()
                return None

            # N key: sort, or Shift+N for the songs still marked new.
            # Tested before the plain key, because an `if` chain is read in
            # order and a shifted key placed after its unshifted twin is
            # never reached -- which is how the chord view once shipped inert.
            if event.key == pygame.K_n and not self._search_active:
                if shift_held(event):
                    self._toggle_new_only()
                else:
                    self._cycle_sort()
                return None

            # T key: toggle theme (only when not searching)
            if event.key == pygame.K_t and not self._search_active:
                name = cycle_theme()
                if self._config:
                    self._config.theme = name
                    self._config.save()
                return None

            # Printable character → append to search (only when search is active)
            if self._search_active:
                ch = event.unicode
                if ch and ch.isprintable() and ch not in ("\r", "\n", "\t"):
                    self._search_text += ch
                    self._apply_filter()
                    return None

        elif event.type == pygame.MOUSEWHEEL:
            # The wheel moves the CURSOR, not a view of its own. The scroll
            # offset is derived from the selection on every frame, so a wheel
            # that only moved the view would be dragged straight back by the
            # next redraw -- and everything else on this screen (ENTER, DEL,
            # R, the tuner) acts on the selected song, so a list scrolled
            # away from its cursor answers a question nobody asked.
            if files:
                self._reload_note = ""
                step = -event.y * WHEEL_ROWS
                self._selected = max(0, min(len(files) - 1,
                                            self._selected + step))
            return None

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            # Clicking the box takes the letters, clicking anything else
            # gives them back -- which is what every text field on every
            # screen does, and is the half of this the player asked for by
            # name (*"Rausklicken entweder per Maus oder mit Pfeiltasten"*).
            if self._search_box is not None:
                self._search_active = self._search_box.collidepoint(event.pos)
            # A chip before a row: the strip sits above the list, so nothing
            # can be both, but the tuning is what this click is ABOUT and a
            # miss must not move the cursor as a side effect.
            for chip in self._tuning_chips:
                if chip.hit(event.pos):
                    self._set_tuning_filter(chip.value)
                    return None
            idx = self._hit_test(event.pos)
            if idx is not None and files:
                now = pygame.time.get_ticks()
                if idx == self._selected and now - self._last_click_time < 400:
                    return files[self._selected]
                self._selected = idx
                self._last_click_time = now

        return None

    def render(self, surface: pygame.Surface) -> None:
        """Draw the menu screen."""
        t = get_theme()
        surface.fill(t.menu_bg)
        w, h = surface.get_size()
        files = self._display_files

        title_font = _get_font("arial", 36)
        item_font = _get_font("consolas", 22)
        hint_font = _get_font("arial", 16)

        # Title
        title_surf = title_font.render("PickHero", True, t.hud_accent)
        surface.blit(title_surf, (w // 2 - title_surf.get_width() // 2, 24))

        # Subtitle
        sub_surf = hint_font.render(
            "Select a song to play", True, t.hud_text
        )
        surface.blit(sub_surf, (w // 2 - sub_surf.get_width() // 2, 68))

        # Today, top right. Asked for by name -- "wie viele Minuten habe ich
        # heute schon gespielt? Wie viele Songs und wie viele Anschlaege?" --
        # and it belongs on THIS screen because this is where a sitting ends.
        # Right-aligned so a long number grows away from the centred title
        # rather than into it.
        today = self._today_line()
        if today:
            today_surf = hint_font.render(today, True, t.hud_accent)
            surface.blit(today_surf, (w - today_surf.get_width() - 24, 28))

        item_h = 30
        list_left = 60
        list_width = w - 120

        # The search box is always drawn, empty or not. It existed as a hidden
        # mode for a long time and was asked for as a missing feature, which is
        # what a feature nobody can see amounts to.
        box = pygame.Rect(list_left - 8, 88, min(420, list_width), 26)
        self._search_box = box          # what a click is tested against
        pygame.draw.rect(surface, t.menu_selected_bg if self._search_active
                         else t.menu_bg, box, border_radius=4)
        pygame.draw.rect(surface, t.hud_accent if self._search_active
                         else t.hud_text, box, width=1, border_radius=4)
        if self._search_active:
            label, colour = f"{self._search_text}_", t.hud_accent
        elif self._search_text:
            # The caret is gone and so is the fill, which is the signal --
            # and the way BACK is named, because a filter you cannot get
            # back into is one you have to retype.
            label, colour = f"{self._search_text}   (F edits)", t.menu_item
        else:
            label, colour = "Search  (F or /)", t.hud_text
        # Not while renaming: the editor borrows this box, and drawing both
        # puts two strings on top of each other.
        if self._renaming is None:
            surface.blit(item_font.render(label, True, colour),
                         (box.x + 8, box.y + 2))
        if self._renaming is not None:
            # In the search box's place, because it is the same job -- typing
            # into the one text field this screen has -- and a second box
            # somewhere else is a second thing to find.
            pygame.draw.rect(surface, t.menu_selected_bg, box, border_radius=4)
            pygame.draw.rect(surface, t.hud_accent, box, width=1,
                             border_radius=4)
            surface.blit(item_font.render(self._rename_text, True,
                                          t.hud_accent), (box.x + 8, box.y + 2))
            # A bar WHERE THE CARET IS, not an underscore stuck on the end.
            # The trailing "_" was fine while the editor could only append;
            # with arrow keys it would say the cursor is somewhere it is
            # not, which is worse than no cursor at all.
            caret = max(0, min(len(self._rename_text), self._rename_caret))
            caret_x = box.x + 8 + item_font.size(
                self._rename_text[:caret])[0]
            pygame.draw.line(surface, t.hud_accent, (caret_x, box.y + 4),
                             (caret_x, box.bottom - 4), 2)
            surface.blit(hint_font.render("Rename — arrows move, ENTER "
                                          "keeps it, ESC cancels", True,
                                          t.hud_accent),
                         (box.right + 12, box.y + 6))
        elif self._reload_note:
            note_surf = hint_font.render(self._reload_note, True, t.hud_accent)
            surface.blit(note_surf, (box.right + 12, box.y + 6))
        elif self._favourites_only:
            label = f"* favourites — {len(files)} of {len(self._files)} songs"
            surface.blit(hint_font.render(label, True, t.hud_accent),
                         (box.right + 12, box.y + 6))
        elif self._new_only:
            label = f"NEW — {len(files)} of {len(self._files)} songs"
            surface.blit(hint_font.render(label, True, t.hud_accent),
                         (box.right + 12, box.y + 6))
        elif self._tuning_filter:
            # A filter nobody can see is a list that has lost songs. It says
            # what is being shown AND how many, next to the box that is the
            # other reason a list can be short.
            label = (f"{self._tuning_filter}  —  {len(files)} of "
                     f"{len(self._files)} songs")
            surface.blit(hint_font.render(label, True, t.hud_accent),
                         (box.right + 12, box.y + 6))
        elif self._search_text:
            count_label = f"{len(files)} of {len(self._files)} songs"
            count_surf = hint_font.render(count_label, True, t.hud_text)
            surface.blit(count_surf, (box.right + 12, box.y + 6))
        elif self._index.busy:
            # Rows fill themselves in, so the empty ones need explaining.
            done, total = self._index.scanned, self._index.to_scan
            surface.blit(hint_font.render(
                f"reading songs… {done}/{total}", True, t.hud_text),
                (box.right + 12, box.y + 6))
        # Every tuning in the folder, as a chip you can click. TAB still
        # walks them -- taking away a shortcut that works costs and buys
        # nothing -- but walking a list nobody can see is what the player
        # objected to, and the count on each chip answers "what is actually
        # in my folder", which the cycle never could.
        #
        # Its own row under the box rather than beside it: beside is where
        # the status line lives (the rename hint, "reading songs 12/240",
        # the filter count), and a strip that collides with those only on
        # some windows is worse than one that always works.
        #
        # Full width buys a single row where beside the box could not.
        # Measured on the player's own folder -- eleven chips, 1264 px --
        # against the room there is: 1844 px at 1920 and 1524 at 1600, so
        # one row on either of his screens; 1204 px at 1280, where it takes
        # two and costs six song rows. Commonest first is what makes that
        # acceptable: the wrap puts the tunings nobody has on the second row.
        present = self._index.tunings_present(self._files)
        chips_top = box.bottom + 8
        if len(present) < 2:
            # One tuning is not a choice, and no tuning is not a strip.
            self._tuning_chips = []
        else:
            counts = self._index.tuning_counts(self._files)
            entries = [("", f"All {len(self._files)}")]
            entries += [(t, f"{tuning_label(t)} {counts[t]}") for t in present]
            self._tuning_chips = chips.lay_out(
                entries, lambda text: hint_font.size(text)[0],
                left=box.x, top=chips_top, right=w - 24)
        mouse = pygame.mouse.get_pos()
        for chip in self._tuning_chips:
            rect = pygame.Rect(chip.rect)
            on = chip.value == self._tuning_filter
            hover = rect.collidepoint(mouse)
            if on:
                pygame.draw.rect(surface, t.hud_accent, rect,
                                 border_radius=chips.CHIP_H // 2)
            elif hover:
                # A chip that does not react to the pointer reads as a label.
                pygame.draw.rect(surface, t.menu_selected_bg, rect,
                                 border_radius=chips.CHIP_H // 2)
            pygame.draw.rect(surface,
                             t.hud_accent if on or hover else t.hud_text,
                             rect, width=1, border_radius=chips.CHIP_H // 2)
            label = hint_font.render(chip.label, True,
                                     t.menu_bg if on else t.menu_item)
            # Clipped, because a chip wider than the room is clamped to it
            # and its text would otherwise run over the one beside it.
            surface.blit(label, (rect.x + chips.CHIP_PAD,
                                 rect.y + (rect.h - label.get_height()) // 2),
                         pygame.Rect(0, 0, max(0, chip.w - 2 * chips.CHIP_PAD),
                                     label.get_height()))

        # The strip pushes the list down, so this cannot be the constant it
        # was -- the same fault VISIBLE_ITEMS and the footer each paid for.
        list_top = max(124, chips_top
                       + chips.height(self._tuning_chips, chips_top) + 10)
        # VISIBLE_ITEMS was a constant, so on a window short enough the list
        # simply ran through the device line, the scoring hint and the
        # footer -- all three of which the player's screenshot has printed
        # over each other and over the songs. It is the room there is now,
        # and never more than the constant, so a tall window is unchanged.
        # The "more" line is drawn UNDER the last row, so the rows have to
        # leave room for the whole of it -- a 4 px allowance was all it had,
        # and the arrow that says the list continues was then printed over
        # the lines along the bottom on a short window. Found by the test
        # above when the footer grew by one entry.
        arrow_room = 4 + hint_font.get_height()
        rows = max(3, (h - list_top - arrow_room
                       - self._bottom_height(w, hint_font)) // item_h)
        visible = min(VISIBLE_ITEMS, rows)
        self._visible_items = visible

        # Empty states
        if not files:
            if not self._files:
                empty_msg = (
                    f"No songs found — add .gp3/.gp4/.gp5 files to {self._songs_dir}/"
                )
            else:
                bits = []
                if self._search_text:
                    bits.append(f'"{self._search_text}"')
                if self._favourites_only:
                    bits.append("favourites")
                if self._new_only:
                    bits.append("new")
                if self._tuning_filter:
                    bits.append(self._tuning_filter)
                empty_msg = f"No songs match {' + '.join(bits)}" if bits else \
                    "No songs match"
            msg_surf = item_font.render(empty_msg, True, t.menu_item)
            surface.blit(msg_surf, (w // 2 - msg_surf.get_width() // 2, h // 2))
        else:
            # File list
            visible_end = min(self._scroll_offset + visible, len(files))
            for i in range(self._scroll_offset, visible_end):
                y = list_top + (i - self._scroll_offset) * item_h
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

                # Everything on a row is laid out from the RIGHT edge
                # inwards, and the song name is cut to whatever is left. A
                # long title would otherwise run under the score, and the
                # thing it collides with is the thing being compared.
                right = list_left + list_width - 8

                if self._progress is not None:
                    record = self._progress.get_best(files[i].stem)
                    if record is not None and record.attempts > 0:
                        pct = f"{record.best_accuracy:.0f}%"
                        pct_surf = item_font.render(pct, True, t.hud_accent)
                        right -= pct_surf.get_width()
                        surface.blit(pct_surf, (right, y + 4))

                        att = f"{record.attempts}x"
                        att_surf = item_font.render(att, True, t.hud_text)
                        right -= att_surf.get_width() + 12
                        surface.blit(att_surf, (right, y + 4))
                        right -= 16

                info = self._index.get(files[i])
                summary = info.summary() if info else ""
                if summary:
                    sum_surf = hint_font.render(summary, True, t.hud_text)
                    right -= sum_surf.get_width()
                    surface.blit(sum_surf, (right, y + 8))
                    right -= 16

                # The star sits LEFT of the name, in a column of its own, so
                # the eye scans one edge instead of hunting along each row --
                # and so a long title cannot push it off the screen. NEW goes
                # in the same column and after it, for the same reason: one
                # edge to scan, and a long title can push neither off.
                star_w = 0
                if self._config is not None and self._config.is_favourite(
                        files[i].stem):
                    star = item_font.render("*", True, t.hud_accent)
                    surface.blit(star, (list_left, y + 4))
                    star_w = star.get_width() + 6
                if self._is_new(files[i]):
                    # In the streak colour rather than the accent: the accent
                    # is what the star and half the header are already drawn
                    # in, and a mark that shares its colour with a mark
                    # beside it says nothing the position did not.
                    tag = hint_font.render("NEW", True, t.feedback_streak)
                    surface.blit(tag, (list_left + star_w, y + 8))
                    star_w += tag.get_width() + 8

                # Show relative path for subfolder files, just name for root
                rel = files[i].relative_to(self._songs_dir)
                label = str(rel) if len(rel.parts) > 1 else files[i].name
                room = max(40, right - list_left - star_w)
                while label and item_font.size(label)[0] > room:
                    label = label[:-1]
                text_surf = item_font.render(label, True, color)
                surface.blit(text_surf, (list_left + star_w, y + 4))

            # Scroll indicators
            if self._scroll_offset > 0:
                arrow = hint_font.render("▲ more", True, t.hud_text)
                surface.blit(arrow, (w // 2 - arrow.get_width() // 2, list_top - 20))
            if visible_end < len(files):
                arrow = hint_font.render("▼ more", True, t.hud_text)
                y_bottom = list_top + visible * item_h + 4
                surface.blit(arrow, (w // 2 - arrow.get_width() // 2, y_bottom))

        # Controls hint. Drawn FIRST and reporting the y it starts at, so
        # everything else along the bottom stacks upward from it -- a
        # second wrapped line pushes the block up instead of through the
        # lines above it. The same rule the playing screen's footer,
        # its sync panel and its completion overlay have each been fixed
        # for once already.
        hint = self._hint_text()
        # It measured 2554 px on a 1920 window before a single entry was
        # added to it, so both ends were simply not there -- which is how a
        # new shortcut looks exactly like one that never shipped.
        self._last_hint = hint          # what the test reads back
        lines = wrap_on_bars(hint, hint_font, w - 24)
        line_h = hint_font.get_height() + 2
        footer_top = h - 36 - (len(lines) - 1) * line_h
        for n, one in enumerate(lines):
            surf = hint_font.render(one, True, t.hud_text)
            surface.blit(surf, (w // 2 - surf.get_width() // 2,
                                footer_top + n * line_h))

        # And the two lines above it stack on MEASURED heights, upward from
        # the footer's own top. They used to sit at -20 and -36, a 16 px
        # step for an 18 px font, so they were printed through each other
        # and through the footer the moment it wrapped to three lines --
        # which the song list's footer now does, since it was taught to wrap
        # at all. A constant offset at the bottom of a screen is the fault
        # the playing screen's footer, its sync panel and its completion
        # overlay have each been fixed for once already.
        y = footer_top
        for text in (f"Press A during playback to enable scoring",
                     f"Audio: {self._device_name}"):
            surf = hint_font.render(text, True, t.hud_text)
            y -= surf.get_height() + 4
            surface.blit(surf, (w // 2 - surf.get_width() // 2, y))
        # What the LIST may not reach into, remembered for the next frame.
        self._bottom_used = h - y + 8

        if self._transfer_lines:
            self._blit_transfer_report(surface, w, h, item_font, hint_font, t)

        # The build, bottom right and out of the way. It is asked for
        # exactly once per report -- "which version are you running" --
        # and answering it has cost several rounds.
        from pickhero.build_info import build_stamp
        stamp = _get_font("arial", 11).render(
            build_stamp(), True, t.lane_line)
        surface.blit(stamp, (w - stamp.get_width() - 8,
                             h - stamp.get_height() - 2))
        # Store layout for hit testing
        self._list_top = list_top
        self._item_h = item_h
        self._list_left = list_left
        self._list_width = list_width

    def _ensure_visible(self) -> None:
        """Adjust scroll offset so selected item is visible."""
        if self._selected < self._scroll_offset:
            self._scroll_offset = self._selected
        elif self._selected >= self._scroll_offset + self._visible_items:
            self._scroll_offset = self._selected - self._visible_items + 1

    def _hit_test(self, pos: tuple[int, int]) -> int | None:
        """Return index of file at mouse position, or None."""
        x, y = pos
        top = getattr(self, "_list_top", 110)
        item_h = getattr(self, "_item_h", 30)
        left = getattr(self, "_list_left", 60)
        width = getattr(self, "_list_width", 1000)

        if x < left - 8 or x > left + width:
            return None
        rel_y = y - top
        if rel_y < 0:
            return None
        idx = self._scroll_offset + int(rel_y // item_h)
        if 0 <= idx < len(self._display_files):
            return idx
        return None
