"""PickHero application — PyGame game loop and state machine.

Two states: MENU (song selection) and PLAYING (scrolling display).
"""

from __future__ import annotations

import time
from pathlib import Path

import pygame

from pickhero import dashboard
from pickhero.audio.input import validate_device_index
from pickhero.config import Config
from pickhero.progress import ProgressTracker
from pickhero.tabs.loader import extract_backing_track, load_gp_file
from pickhero.ui.colors import set_theme
from pickhero.ui.calibration_menu import CalibrationMenuScreen
from pickhero.ui.tuner_menu import TunerMenuScreen
from pickhero.ui.device_menu import DeviceMenuScreen
from pickhero.ui.download_menu import DownloadMenuScreen
from pickhero.ui.menu import MenuScreen
from pickhero.ui.settings_menu import SettingsMenuScreen
from pickhero.ui import scrolling
from pickhero.ui.scrolling import PlayingScreen


class App:
    """Main application with game loop."""

    def __init__(self, config: Config | None = None):
        self._config = config or Config.load()
        self._progress = ProgressTracker()
        self._running = False
        self._state = "menu"
        self._load_error: str | None = None
        self._current_song_path: Path | None = None
        self._current_track_index: int | None = None
        # The last file's track list. See _tracks_of.
        self._tracks_cache_key: str | None = None
        self._tracks_cache: list[dict] = []
        self._menu: MenuScreen | None = None
        self._playing_screen: PlayingScreen | None = None
        self._device_menu: DeviceMenuScreen | None = None
        self._download_menu: DownloadMenuScreen | None = None
        self._calibration_menu: CalibrationMenuScreen | None = None
        self._tuner_menu: TunerMenuScreen | None = None
        self._settings_menu: SettingsMenuScreen | None = None
        # Where ESC goes from the device and calibration screens. They are
        # reachable from the song list AND from the settings screen, and
        # dropping the player somewhere they did not come from is how a menu
        # starts to feel like a maze.
        self._return_to = "menu"

    def run(self) -> None:
        """Initialize PyGame, run main loop, clean up."""
        # Apply saved theme
        set_theme(self._config.theme)

        # Validate saved audio device — fall back to default if unavailable
        if not validate_device_index(self._config.audio.device_index):
            print(
                f"Saved audio device #{self._config.audio.device_index} not available, "
                "falling back to system default."
            )
            self._config.audio.device_index = None
            self._config.save()

        pygame.init()
        # Fonts and the surfaces drawn with them belong to this
        # session; one kept from a previous pygame.quit() would crash.
        scrolling.clear_font_cache()
        pygame.key.set_repeat(300, 40)  # 300ms delay, then repeat every 40ms
        pygame.display.set_caption("PickHero")

        dc = self._config.display
        surface = pygame.display.set_mode(
            (dc.width, dc.height), pygame.RESIZABLE
        )
        clock = pygame.time.Clock()

        songs_dir = Path(self._config.songs_dir)
        self._menu = MenuScreen(songs_dir, config=self._config, progress=self._progress)
        self._state = "menu"
        self._running = True

        while self._running:
            frame_started = time.perf_counter()
            self._process_events(surface)
            self._update()
            self._render(surface)
            pygame.display.flip()
            # How long the work took, BEFORE the wait that pads it out to
            # 60 Hz. clock.get_fps() would report the padded rate and read a
            # healthy 60 right up to the moment the machine can no longer
            # keep up -- which is the one thing it is being asked about.
            if self._playing_screen is not None:
                self._playing_screen.record_frame_ms(
                    (time.perf_counter() - frame_started) * 1000.0)
            clock.tick(60)

        # Closing the window ends a session as surely as pressing ESC does.
        if self._playing_screen is not None:
            self._playing_screen.close_session()
        self._write_dashboard()
        pygame.quit()

    @staticmethod
    def _write_dashboard() -> None:
        """Refresh the practice dashboard on the way out.

        On the way OUT rather than on the way in, and after `close_session`
        rather than before it: the sitting that just ended is written by that
        call, so a page built at startup is always one session stale -- it
        would never show the practising that had just been done, which is the
        practising anyone opens it to look at. Leaving a song reaches this
        too, so the page is current within a sitting and not only after one.

        Measured on generated logs: 1.5 ms at 100 sittings, 38 ms at 5000,
        224 ms at 20000 -- and all of it after the last frame, where there is
        nothing left to stutter.

        Nothing here may take the app down. A dashboard that fails to write is
        a page that is a day old; an exception on the way out is a crash on
        exit, which looks like data loss and is what the player would report.
        """
        try:
            dashboard.write()
        except Exception as exc:                    # noqa: BLE001
            print(f"Dashboard konnte nicht geschrieben werden: {exc}")

    def _process_events(self, surface: pygame.Surface) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self._running = False
                return

            if event.type == pygame.VIDEORESIZE:
                surface = pygame.display.set_mode(
                    (event.w, event.h), pygame.RESIZABLE
                )

            if self._state == "menu":
                self._handle_menu_event(event)
            elif self._state == "playing":
                self._handle_playing_event(event)
            elif self._state == "device":
                self._handle_device_event(event)
            elif self._state == "download":
                self._handle_download_event(event)
            elif self._state == "calibration":
                self._handle_calibration_event(event)
            elif self._state == "tuner":
                self._handle_tuner_event(event)
            elif self._state == "settings":
                self._handle_settings_event(event)

    def _handle_menu_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and not self._menu.is_searching:
            if event.key == pygame.K_d:
                self._open_device_menu("menu")
                return
            if event.key == pygame.K_o:
                self._settings_menu = SettingsMenuScreen(self._config)
                self._state = "settings"
                return
            if event.key == pygame.K_s:
                songs_dir = Path(self._config.songs_dir)
                self._download_menu = DownloadMenuScreen(songs_dir)
                self._state = "download"
                return
            if event.key == pygame.K_g:
                self._open_calibration("menu")
                return
            if event.key == pygame.K_u:
                self._open_tuner("menu")
                return

        result = self._menu.handle_event(event)
        if result == "escape":
            self._running = False
        elif isinstance(result, Path):
            self._load_song(result)

    def _handle_playing_event(self, event: pygame.event.Event) -> None:
        result = self._playing_screen.handle_event(event)
        if isinstance(result, tuple) and result[0] == "transpose":
            # A different tuning is a different set of pitches, so the whole
            # plan is rebuilt -- and the position is carried over, the same
            # as an instrument change, because you change tuning precisely
            # at the passage you were trying to play.
            setter = getattr(self._config, "set_transpose_for", None)
            if setter is not None:
                setter(self._current_song_path.stem, result[1])
                self._config.save()
            self._load_song(self._current_song_path,
                            self._current_track_index,
                            resume_at_ms=self._playing_screen.position_ms())
            return
        if isinstance(result, tuple) and result[0] == "select_track":
            # Stay where the song is. The screen is rebuilt from scratch, so
            # the position has to be carried over by hand.
            where = self._playing_screen.position_ms()
            self._load_song(self._current_song_path, result[1],
                            resume_at_ms=where)
            return
        if result == "menu":
            self._playing_screen.stop_audio()      # writes the sitting
            self._playing_screen = None
            self._state = "menu"
            self._menu.scan_files()
            # Leaving a song is where "how long have I played today" gets
            # asked, and until now the page could only answer it after the
            # app was CLOSED -- so a player who had practised for ten minutes
            # was shown three, which reads as a diary that loses sittings
            # rather than as a page that is out of date. It costs 1.5 ms at
            # a hundred sittings, on a frame that is tearing a screen down
            # anyway, and it is written AFTER stop_audio for the same reason
            # it is written after close_session on the way out: that call is
            # what puts the sitting in the log.
            self._write_dashboard()

    def _open_device_menu(self, came_from: str) -> None:
        self._device_menu = DeviceMenuScreen(self._config)
        self._return_to = came_from
        self._state = "device"

    def _open_calibration(self, came_from: str) -> None:
        self._calibration_menu = CalibrationMenuScreen(self._config)
        self._return_to = came_from
        self._state = "calibration"

    def _handle_settings_event(self, event: pygame.event.Event) -> None:
        result = self._settings_menu.handle_event(event)
        if result == "back":
            # The song list shows the input device and the theme, both of
            # which may have just changed under it.
            self._menu.refresh_device_name()
            set_theme(self._config.theme)
            self._settings_menu = None
            self._state = "menu"
        elif result == "device":
            self._open_device_menu("settings")
        elif result == "calibration":
            self._open_calibration("settings")

    def _handle_device_event(self, event: pygame.event.Event) -> None:
        result = self._device_menu.handle_event(event)
        if result in ("back", "selected"):
            if result == "selected":
                self._menu.refresh_device_name()
            self._device_menu = None
            self._state = self._return_to

    def _handle_download_event(self, event: pygame.event.Event) -> None:
        result = self._download_menu.handle_event(event)
        if result in ("back", "downloaded"):
            if result == "downloaded":
                self._menu.scan_files()
            self._download_menu = None
            self._state = "menu"

    def _open_tuner(self, came_from: str) -> None:
        # Opened on the tuning of the song under the cursor. The list already
        # shows it on every row, so making the player dial it in again is
        # asking them for something the app has.
        letters, song = ("", "")
        if came_from == "menu":
            letters, song = self._menu.selected_tuning()
        self._tuner_menu = TunerMenuScreen(self._config, letters, song)
        self._return_to = came_from
        self._state = "tuner"

    def _handle_tuner_event(self, event: pygame.event.Event) -> None:
        if self._tuner_menu.handle_event(event) == "escape":
            # The input device is held open while this screen is up, and a
            # screen that keeps a device after it is gone is the fault this
            # project has now paid for three times.
            self._tuner_menu.close()
            self._tuner_menu = None
            self._state = self._return_to

    def _handle_calibration_event(self, event: pygame.event.Event) -> None:
        result = self._calibration_menu.handle_event(event)
        if result == "back":
            self._calibration_menu = None
            self._state = self._return_to
        elif result == "complete":
            # Save calibration results to config
            from datetime import datetime
            results = self._calibration_menu.get_results()
            self._config.calibration = {"strings": {}, "calibrated_at": datetime.now().isoformat()}
            for string_num, cal in results.items():
                self._config.set_string_calibration(string_num, cal)
            self._config.save()
            self._calibration_menu = None
            self._state = self._return_to

    def _tracks_of(self, path: Path) -> list[dict]:
        """The file's track list, parsed once and kept.

        Reading it means unpacking the whole file -- and for a GP6 container,
        decompressing it first. It was read TWICE per call below and again on
        every change of instrument, though a file's tracks cannot change
        while it is sitting there being played.
        """
        key = str(path)
        if self._tracks_cache_key != key:
            from pickhero.tabs.loader import list_tracks
            try:
                self._tracks_cache = list_tracks(path)
            except Exception:
                self._tracks_cache = []
            self._tracks_cache_key = key
        return self._tracks_cache

    def _playable_track_indices(self, path: Path) -> list[int]:
        """Tracks worth offering: the guitar ones, or everything if none."""
        tracks = self._tracks_of(path)
        guitars = [t["index"] for t in tracks
                   if t.get("is_guitar") and not t.get("is_percussion")]
        return guitars or [t["index"] for t in tracks]

    def _track_options(self, path: Path) -> list[tuple[int, str]]:
        """(index, label) for every track worth offering."""
        tracks = self._tracks_of(path)
        wanted = set(self._playable_track_indices(path))
        return [(t["index"], f"{t['index'] + 1}. {t['name']}")
                for t in tracks if t["index"] in wanted]

    def _render_load_error(self, surface: pygame.Surface) -> None:
        """Say why the last song refused to load, on the menu it fell back to."""
        if not getattr(self, "_load_error", None):
            return
        import pygame as _pg
        font = _pg.font.SysFont("arial", 15) or _pg.font.Font(None, 15)
        text = f"Could not load  {self._load_error}"
        surf = font.render(text[:160], True, (255, 120, 120))
        y = surface.get_height() - surf.get_height() - 8
        _pg.draw.rect(surface, (40, 10, 10),
                      (0, y - 4, surface.get_width(), surf.get_height() + 8))
        surface.blit(surf, (12, y))

    def _load_song(self, path: Path, track_index: int | None = None,
                   resume_at_ms: float | None = None) -> None:
        """Load a GP file and switch to playing state.

        `resume_at_ms` keeps the position across an instrument change. The
        tracks of one file share a clock -- bar 40 of the rhythm guitar is
        bar 40 of the lead -- so throwing the position away and starting at
        the first note again is not a fresh start, it is losing your place.
        Somebody comparing two versions of a passage changes track precisely
        BECAUSE they are at that passage.

        The screen being replaced is torn down FIRST. Changing instrument
        reaches this too, and it used to leave the old one holding the input
        stream and the MIDI output port -- so the new screen then opened a
        second of each, which on Windows is why changing track took longer
        than opening the song did. It also lost the sitting: close_session
        lives in stop_audio, and nothing called it on this path.
        """
        if self._playing_screen is not None:
            self._playing_screen.stop_audio()
        try:
            timeline = load_gp_file(path, track_index)
        except Exception as e:
            # Show it, do not just log it: returning to the menu in silence
            # looks like the song was ignored rather than that it failed.
            self._load_error = f"{path.name}: {type(e).__name__}: {e}"
            try:
                print(f"Error loading {path}: {e}")
            except UnicodeEncodeError:
                print(f"Error loading {path}: {type(e).__name__}")
            return
        self._load_error = None

        # Played on a guitar tuned somewhere else. The fret numbers do not
        # move -- Drop C and Drop D differ by a uniform tone, so the same
        # shapes are the same music a tone higher -- so this shifts what the
        # app expects to HEAR and nothing else. Applied here, before anything
        # downstream is built, so the matcher, the MIDI backing and the guide
        # track are all made from one transposed plan rather than each
        # applying the shift for itself.
        transpose = 0
        getter = getattr(self._config, "transpose_for", None)
        if getter is not None:
            transpose = getter(path.stem)
        timeline = timeline.transposed(transpose)

        # Extract backing track (everything EXCEPT the track being played)
        chosen = timeline.metadata.track_index
        backing_track = None
        try:
            backing_track = extract_backing_track(
                path, exclude_track_indices={chosen},
            )
        except Exception as e:
            print(f"Backing track extraction failed: {e}")

        # And the mirror of it: ONLY the track being played, so the part the
        # player is meant to produce can be heard while learning it. Built by
        # excluding every other track, which is the same extraction run the
        # other way round -- no second code path to keep in step.
        guide_track = None
        try:
            others = {t["index"] for t in self._tracks_of(path)} - {chosen}
            if others or chosen is not None:
                guide_track = extract_backing_track(
                    path, exclude_track_indices=others,
                )
        except Exception as e:
            print(f"Guide track extraction failed: {e}")

        dc = self._config.display
        self._playing_screen = PlayingScreen(
            timeline,
            visible_beats=dc.visible_beats,
            hit_zone_fraction=dc.hit_zone_fraction,
            config=self._config,
            backing_track=backing_track,
            guide_track=guide_track,
            progress_tracker=self._progress,
            song_key=path.stem,
            song_path=str(path),
            transpose=transpose,
        )
        self._state = "playing"
        self._current_song_path = path
        self._current_track_index = timeline.metadata.track_index
        self._playing_screen.set_track_options(
            self._track_options(path), timeline.metadata.track_index
        )

        if resume_at_ms is not None:
            # Clamped, because the new track may be shorter than the old one.
            self._playing_screen.seek(
                max(0.0, min(resume_at_ms, timeline.duration_ms)))
        # Skip ahead so the first note is just entering the visible window
        elif timeline.notes:
            first_note_ms = timeline.notes[0].timestamp_ms
            seek_to = max(0.0, first_note_ms - self._playing_screen._visible_window_ms)
            if seek_to > 0:
                self._playing_screen.seek(seek_to)

    def _update(self) -> None:
        if self._state == "playing" and self._playing_screen is not None:
            self._playing_screen.update()
        elif self._state == "calibration" and self._calibration_menu is not None:
            self._calibration_menu.update()
        elif self._state == "tuner" and self._tuner_menu is not None:
            self._tuner_menu.update()

    def _render(self, surface: pygame.Surface) -> None:
        if self._state == "menu" and self._menu is not None:
            self._menu.render(surface)
            self._render_load_error(surface)
        elif self._state == "playing" and self._playing_screen is not None:
            self._playing_screen.render(surface)
        elif self._state == "device" and self._device_menu is not None:
            self._device_menu.render(surface)
        elif self._state == "download" and self._download_menu is not None:
            self._download_menu.render(surface)
        elif self._state == "calibration" and self._calibration_menu is not None:
            self._calibration_menu.render(surface)
        elif self._state == "tuner" and self._tuner_menu is not None:
            self._tuner_menu.render(surface)
        elif self._state == "settings" and self._settings_menu is not None:
            self._settings_menu.render(surface)
