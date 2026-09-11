"""Ctrl+C puts the screen's text on the clipboard.

*"Kannst du etwas bauen, damit ich Text am Screen mit der Maus markieren und
kopieren kann, oder wenigstens ein generelles Ctrl+C?"*

Asked after reading a 200-character Songsterr error off a photograph of a
monitor and typing it back to be diagnosed. Selecting with the mouse would
mean laying out every string as characters with hit boxes, in a window whose
whole job is drawing music. The whole screen costs one key and answers the
same need.
"""

from pathlib import Path

import pygame
import pytest

from pickhero.config import Config
from pickhero.tabs.downloader import SongsterrResult
from pickhero.ui import clipboard


def _app(tmp_path, state="menu"):
    from pickhero.ui.app import App
    from pickhero.ui.download_menu import DownloadMenuScreen
    from pickhero.ui.menu import MenuScreen
    config = Config()
    config.songs_dir = str(tmp_path)
    app = App.__new__(App)
    app._config = config
    app._menu = MenuScreen(tmp_path, config=config)
    app._download_menu = DownloadMenuScreen(tmp_path, config=config)
    app._playing_screen = None
    app._device_menu = None
    app._calibration_menu = None
    app._tuner_menu = None
    app._settings_menu = None
    app._state = state
    app._quit_armed = False
    app._held = set()
    app._running = True
    return app


def _copied(monkeypatch):
    box = []
    monkeypatch.setattr(clipboard, "put_clipboard",
                        lambda text: (box.append(text), True)[1])
    return box


def _press_ctrl_c(app):
    surface = pygame.Surface((10, 10))
    event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c,
                               mod=pygame.KMOD_LCTRL, unicode="\x03")
    monkey = [event]
    original = pygame.event.get
    pygame.event.get = lambda *a, **k: monkey
    try:
        app._process_events(surface)
    finally:
        pygame.event.get = original


class TestTheKeyReachesEveryScreen:

    def test_the_song_list(self, tmp_path, monkeypatch):
        (tmp_path / "AC-DC - Thunder.gp5").write_bytes(b"x")
        app = _app(tmp_path)
        app._menu.reload_files()
        box = _copied(monkeypatch)
        _press_ctrl_c(app)
        assert "AC-DC - Thunder" in box[0]

    def test_the_search_screen_where_it_was_asked_for(self, tmp_path,
                                                      monkeypatch):
        """The failure line names a Songsterr revision and the fields that
        revision carried. That is the string he was photographing."""
        app = _app(tmp_path, state="download")
        app._download_menu._notes = [
            "Songsterr holds no Guitar Pro file for this tab - 8 revisions "
            "checked back to 4100000 (it has: aiGenerated, artist, audioV4)"]
        box = _copied(monkeypatch)
        _press_ctrl_c(app)
        assert "8 revisions checked back to 4100000" in box[0]
        assert "audioV4" in box[0]

    def test_a_text_box_does_not_swallow_it(self, tmp_path, monkeypatch):
        """Guarding it behind "is anything being typed" would switch it off
        on the search screen, which is the exact screen it is for."""
        app = _app(tmp_path, state="download")
        app._download_menu._state = "input"
        app._download_menu._query = "papa roach"
        box = _copied(monkeypatch)
        _press_ctrl_c(app)
        assert "papa roach" in box[0]

    def test_a_screen_with_nothing_to_say_says_so(self, tmp_path,
                                                  monkeypatch):
        """Rather than copying an empty string and looking like a key that
        does nothing."""
        app = _app(tmp_path, state="settings")
        app._settings_menu = object()
        box = _copied(monkeypatch)
        _press_ctrl_c(app)
        assert box == []

    def test_a_clipboard_that_cannot_be_reached_is_named(self, tmp_path,
                                                         monkeypatch):
        (tmp_path / "song.gp5").write_bytes(b"x")
        app = _app(tmp_path)
        app._menu.reload_files()
        monkeypatch.setattr(clipboard, "put_clipboard", lambda text: False)
        _press_ctrl_c(app)
        assert "Could not reach the clipboard" in app._menu._reload_note

    def test_it_says_how_much_it_took(self, tmp_path, monkeypatch):
        (tmp_path / "song.gp5").write_bytes(b"x")
        app = _app(tmp_path)
        app._menu.reload_files()
        _copied(monkeypatch)
        _press_ctrl_c(app)
        assert "Copied 2 lines" in app._menu._reload_note

    def test_plain_c_is_not_a_copy(self, tmp_path, monkeypatch):
        """On the playing screen plain C is the noise gate."""
        app = _app(tmp_path)
        box = _copied(monkeypatch)
        surface = pygame.Surface((10, 10))
        original = pygame.event.get
        pygame.event.get = lambda *a, **k: [pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_c, mod=0, unicode="c")]
        try:
            app._process_events(surface)
        finally:
            pygame.event.get = original
        assert box == []


class TestWhatEachScreenSays:

    def test_the_search_screen_lists_its_results(self, tmp_path):
        from pickhero.ui.download_menu import DownloadMenuScreen
        screen = DownloadMenuScreen(tmp_path)
        screen._results = [SongsterrResult(14907, "Between Angels And "
                                           "Insects", "Papa Roach")]
        said = screen.copy_text()
        assert "Papa Roach - Between Angels And Insects" in said
        assert "s14907" in said

    def test_the_song_list_marks_the_cursor(self, tmp_path):
        from pickhero.ui.menu import MenuScreen
        (tmp_path / "A song.gp5").write_bytes(b"x")
        (tmp_path / "B song.gp5").write_bytes(b"x")
        screen = MenuScreen(tmp_path, config=Config())
        screen.reload_files()
        lines = screen.copy_text().splitlines()
        assert lines[1].startswith("> ")
        assert lines[2].startswith("  ")

    def test_the_playing_screen_carries_the_sync_panel(self, tmp_path,
                                                       monkeypatch):
        """That panel is where every number this project argues about
        lives."""
        from pickhero.audio.mp3_playback import Mp3Player
        from pickhero.ui.scrolling import PlayingScreen
        from tests.test_songsterr import _song
        pygame.init()
        pygame.display.set_mode((640, 480))
        audio = tmp_path / "song.mp3"
        audio.write_bytes(b"x")
        config = Config()
        config.set_mp3_path_for("song", str(audio))
        config.set_songsterr_for("song", 2333598)
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        screen = PlayingScreen(_song(), config=config, song_key="song")
        said = screen.copy_text()
        assert "Song: song" in said
        assert "Songsterr 2333598" in said

    def test_a_hud_that_cannot_describe_itself_still_copies(self, tmp_path,
                                                            monkeypatch):
        """Half the lines beats a traceback."""
        from pickhero.ui.scrolling import PlayingScreen
        from tests.test_songsterr import _song
        pygame.init()
        pygame.display.set_mode((640, 480))
        screen = PlayingScreen(_song(), config=Config(), song_key="song")
        monkeypatch.setattr(PlayingScreen, "sync_block_lines",
                            lambda self: 1 / 0)
        assert "Song: song" in screen.copy_text()


class TestTheKeyIsWrittenDown:
    """A feature nobody can find is one the player has not got."""

    def test_the_song_list_hint_names_it(self):
        import inspect
        from pickhero.ui import menu
        assert "Ctrl+C: copy screen" in inspect.getsource(menu)

    def test_the_search_screen_hint_names_it(self):
        import inspect
        from pickhero.ui import download_menu
        assert "Ctrl+C: copy this screen" in inspect.getsource(download_menu)

    def test_and_the_rename_hint_does_not_advertise_dead_keys(self):
        """R and DEL are both guarded by `not self._search_active`, so a
        search hint offering them advertises keys that do nothing."""
        import inspect
        from pickhero.ui import menu
        searching = [line for line in inspect.getsource(menu).splitlines()
                     if "Type to search" in line][0]
        assert "R: rename" not in searching
        assert "DEL: delete" not in searching
