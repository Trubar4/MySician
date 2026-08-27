"""PickHero application — PyGame game loop and state machine.

Two states: MENU (song selection) and PLAYING (scrolling display).
"""

from __future__ import annotations

import time
from pathlib import Path

import pygame

from pickhero.audio.input import validate_device_index
from pickhero.config import Config
from pickhero.progress import ProgressTracker
from pickhero.tabs.loader import extract_backing_track, load_gp_file
from pickhero.ui.colors import set_theme
from pickhero.ui.calibration_menu import CalibrationMenuScreen
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
        self._menu: MenuScreen | None = None
        self._playing_screen: PlayingScreen | None = None
        self._device_menu: DeviceMenuScreen | None = None
        self._download_menu: DownloadMenuScreen | None = None
        self._calibration_menu: CalibrationMenuScreen | None = None
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
        pygame.quit()

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

        result = self._menu.handle_event(event)
        if result == "escape":
            self._running = False
        elif isinstance(result, Path):
            self._load_song(result)

    def _handle_playing_event(self, event: pygame.event.Event) -> None:
        result = self._playing_screen.handle_event(event)
        if isinstance(result, tuple) and result[0] == "select_track":
            self._load_song(self._current_song_path, result[1])
            return
        if result == "menu":
            self._playing_screen.stop_audio()
            self._playing_screen = None
            self._state = "menu"
            self._menu.scan_files()

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

    def _playable_track_indices(self, path: Path) -> list[int]:
        """Tracks worth offering: the guitar ones, or everything if none."""
        from pickhero.tabs.loader import list_tracks
        try:
            tracks = list_tracks(path)
        except Exception:
            return []
        guitars = [t["index"] for t in tracks
                   if t.get("is_guitar") and not t.get("is_percussion")]
        return guitars or [t["index"] for t in tracks]

    def _track_options(self, path: Path) -> list[tuple[int, str]]:
        """(index, label) for every track worth offering."""
        from pickhero.tabs.loader import list_tracks
        try:
            tracks = list_tracks(path)
        except Exception:
            return []
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

    def _load_song(self, path: Path, track_index: int | None = None) -> None:
        """Load a GP file and switch to playing state."""
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

        # Extract backing track (non-guitar tracks as MIDI)
        backing_track = None
        try:
            backing_track = extract_backing_track(
                path, exclude_track_indices={timeline.metadata.track_index},
            )
        except Exception as e:
            print(f"Backing track extraction failed: {e}")

        dc = self._config.display
        self._playing_screen = PlayingScreen(
            timeline,
            visible_beats=dc.visible_beats,
            hit_zone_fraction=dc.hit_zone_fraction,
            config=self._config,
            backing_track=backing_track,
            progress_tracker=self._progress,
            song_key=path.stem,
        )
        self._state = "playing"
        self._current_song_path = path
        self._current_track_index = timeline.metadata.track_index
        self._playing_screen.set_track_options(
            self._track_options(path), timeline.metadata.track_index
        )

        # Skip ahead so the first note is just entering the visible window
        if timeline.notes:
            first_note_ms = timeline.notes[0].timestamp_ms
            seek_to = max(0.0, first_note_ms - self._playing_screen._visible_window_ms)
            if seek_to > 0:
                self._playing_screen.seek(seek_to)

    def _update(self) -> None:
        if self._state == "playing" and self._playing_screen is not None:
            self._playing_screen.update()
        elif self._state == "calibration" and self._calibration_menu is not None:
            self._calibration_menu.update()

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
        elif self._state == "settings" and self._settings_menu is not None:
            self._settings_menu.render(surface)
