"""R renames a song, and everything keyed to its name goes with it.

*"Brauche eine Rename Song Möglichkeit in Taboverview."*

A tab downloaded from Songsterr arrives called whatever Songsterr calls it
-- `Thunder - Love Walked In v3` -- and the player wants it tidy. But the
name is the song's IDENTITY: `song_key` is the tab's stem, so renaming in
Explorer leaves the speed, the recording, the sync points, the Songsterr id,
the transpose and the practice history behind under the old name. It looks
like a rename that wiped the setup, and the leftovers wait to be inherited
by the next song that takes the old name.
"""

import pygame
import pytest

from pickhero.config import Config
from pickhero.tabs import remove


def _song(folder, stem="Thunder - Love Walked In v3"):
    tab = folder / f"{stem}.gp5"
    tab.write_bytes(b"gp")
    (folder / f"{stem}.songsterr.json").write_text("{}", encoding="utf-8")
    (folder / f"{stem}.mp3").write_bytes(b"audio")
    return tab


def _config(song_key="Thunder - Love Walked In v3"):
    config = Config()
    config.save = lambda: None
    config.set_songsterr_for(song_key, 2333598)
    config.set_tempo_factor_for(song_key, 0.7)
    config.set_mp3_anchors_for(song_key, [(0.0, -120.0)])
    config.set_transpose_for(song_key, 2)
    config.set_favourite(song_key, True)
    return config


class TestNamesThePlayerCanType:

    def test_what_windows_refuses_is_dropped(self):
        assert remove.safe_name('AC/DC: Thunder?') == "ACDC Thunder"

    def test_a_name_of_only_dots_is_nothing(self):
        """A leading dot is a file he cannot see afterwards."""
        assert remove.safe_name("  ...  ") == ""
        assert remove.safe_name("") == ""

    def test_an_ordinary_name_is_left_alone(self):
        assert remove.safe_name("AC-DC - Thunder") == "AC-DC - Thunder"


class TestRenaming:

    def test_the_tab_and_everything_beside_it_moves(self, tmp_path):
        tab = _song(tmp_path)
        report = remove.rename_song(tab, "AC-DC - Thunder", _config())
        assert report.ok
        assert not tab.exists()
        assert (tmp_path / "AC-DC - Thunder.gp5").is_file()
        assert (tmp_path / "AC-DC - Thunder.songsterr.json").is_file()
        assert (tmp_path / "AC-DC - Thunder.mp3").is_file()
        assert report.tab_path == tmp_path / "AC-DC - Thunder.gp5"

    def test_the_settings_travel_with_it(self, tmp_path):
        config = _config()
        remove.rename_song(_song(tmp_path), "AC-DC - Thunder", config)
        assert config.songsterr_for("AC-DC - Thunder") == 2333598
        assert config.tempo_factor_for("AC-DC - Thunder") == 0.7
        assert config.mp3_anchors_for("AC-DC - Thunder") == [(0.0, -120.0)]
        assert config.transpose_for("AC-DC - Thunder") == 2
        assert config.is_favourite("AC-DC - Thunder")
        assert config.songsterr_for("Thunder - Love Walked In v3") == 0

    def test_the_settings_are_found_not_listed(self, tmp_path):
        """The mirror of forget_song, and wrong in the same way if a tenth
        per-song dict is added and not added here."""
        config = _config()
        moved = set(config.rename_song("Thunder - Love Walked In v3", "X"))
        assert {"song_songsterr", "song_tempo_factors", "song_mp3_anchors",
                "song_transpose", "favourites"} <= moved

    def test_the_practice_history_moves_too(self, tmp_path, monkeypatch):
        """A rename is not a new piece. Losing a best-ever score because the
        file got a tidier name is the kind of thing nobody notices until they
        go looking months later."""
        from pickhero import progress
        monkeypatch.setattr(progress, "PROGRESS_FILE",
                            tmp_path / "progress.json")
        monkeypatch.setattr(progress, "CONFIG_DIR", tmp_path)
        progress.ProgressTracker().record_result(
            "Thunder - Love Walked In v3",
            {"accuracy_percent": 91.0, "hits": 120, "total": 130})

        remove.rename_song(_song(tmp_path), "AC-DC - Thunder", _config())

        tracker = progress.ProgressTracker()
        assert tracker.get_best("AC-DC - Thunder").best_accuracy == 91.0
        assert tracker.get_best("Thunder - Love Walked In v3") is None

    def test_a_name_already_taken_moves_nothing(self, tmp_path):
        """Checked BEFORE anything moves. A half-done rename leaves a tab
        under one name and its recording under another, which is worse than
        not renaming at all."""
        tab = _song(tmp_path)
        (tmp_path / "AC-DC - Thunder.mp3").write_bytes(b"someone else's")
        report = remove.rename_song(tab, "AC-DC - Thunder", _config())
        assert not report.ok
        assert "already there" in report.failed[0]
        assert tab.is_file()
        assert (tmp_path / "Thunder - Love Walked In v3.mp3").is_file()

    def test_an_unusable_name_is_refused(self, tmp_path):
        tab = _song(tmp_path)
        report = remove.rename_song(tab, "///", _config())
        assert not report.ok and tab.is_file()

    def test_renaming_to_the_same_name_is_not_an_error(self, tmp_path):
        tab = _song(tmp_path)
        report = remove.rename_song(tab, tab.stem, _config())
        assert report.ok and tab.is_file()

    def test_the_summary_says_the_settings_are_kept(self, tmp_path):
        report = remove.rename_song(_song(tmp_path), "AC-DC - Thunder",
                                    _config())
        assert "Settings and history kept" in report.summary()


class TestTheKey:

    def _menu(self, tmp_path):
        from pickhero.ui.menu import MenuScreen
        config = _config()
        return MenuScreen(tmp_path, config=config)

    def _press(self, screen, key, mod=0, unicode=""):
        return screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=key, mod=mod, unicode=unicode))

    def _type(self, screen, text):
        for ch in text:
            self._press(screen, ord(ch) if len(ch) == 1 else 0, unicode=ch)

    def test_r_opens_the_editor_on_the_current_name(self, tmp_path):
        _song(tmp_path)
        screen = self._menu(tmp_path)
        self._press(screen, pygame.K_r, unicode="r")
        assert screen._rename_text == "Thunder - Love Walked In v3"

    def test_enter_renames(self, tmp_path):
        _song(tmp_path)
        screen = self._menu(tmp_path)
        self._press(screen, pygame.K_r, unicode="r")
        screen._rename_text = "AC-DC - Thunder"
        self._press(screen, pygame.K_RETURN)
        assert (tmp_path / "AC-DC - Thunder.gp5").is_file()
        assert screen._config.songsterr_for("AC-DC - Thunder") == 2333598

    def test_escape_cancels(self, tmp_path):
        tab = _song(tmp_path)
        screen = self._menu(tmp_path)
        self._press(screen, pygame.K_r, unicode="r")
        screen._rename_text = "something else"
        self._press(screen, pygame.K_ESCAPE)
        assert tab.is_file() and screen._renaming is None

    def test_the_editor_owns_every_key_while_it_is_open(self, tmp_path):
        """Otherwise typing a name is a minefield: `d` in "Thunderstruck"
        would arm the delete and ESC would leave the song list."""
        tab = _song(tmp_path)
        screen = self._menu(tmp_path)
        self._press(screen, pygame.K_r, unicode="r")
        screen._rename_text = ""
        self._type(screen, "Thunderstruck")
        assert screen._rename_text == "Thunderstruck"
        assert screen._delete_armed is None
        assert not screen._search_active
        assert tab.is_file()

    def test_a_character_windows_refuses_never_reaches_the_box(self,
                                                                tmp_path):
        """Said at the keypress, not when ENTER dies with a Windows error
        nobody can read."""
        _song(tmp_path)
        screen = self._menu(tmp_path)
        self._press(screen, pygame.K_r, unicode="r")
        screen._rename_text = "AC"
        self._press(screen, pygame.K_SLASH, unicode="/")
        assert screen._rename_text == "AC"
        assert "cannot be in a file name" in screen._reload_note

    def test_a_space_is_a_perfectly_good_character(self, tmp_path):
        _song(tmp_path)
        screen = self._menu(tmp_path)
        self._press(screen, pygame.K_r, unicode="r")
        screen._rename_text = "AC-DC"
        self._press(screen, pygame.K_SPACE, unicode=" ")
        assert screen._rename_text == "AC-DC "

    def test_backspace_deletes(self, tmp_path):
        _song(tmp_path)
        screen = self._menu(tmp_path)
        self._press(screen, pygame.K_r, unicode="r")
        screen._rename_text = "abc"
        self._press(screen, pygame.K_BACKSPACE)
        assert screen._rename_text == "ab"

    def test_the_cursor_follows_the_renamed_song(self, tmp_path):
        _song(tmp_path, stem="A song")
        _song(tmp_path, stem="Z song")
        screen = self._menu(tmp_path)
        self._press(screen, pygame.K_DOWN)
        self._press(screen, pygame.K_r, unicode="r")
        screen._rename_text = "B song"
        self._press(screen, pygame.K_RETURN)
        assert screen._selected_path().stem == "B song"

    def test_r_is_left_alone_while_searching(self, tmp_path):
        _song(tmp_path)
        screen = self._menu(tmp_path)
        self._press(screen, pygame.K_f, unicode="f")
        self._press(screen, pygame.K_r, unicode="r")
        assert screen._renaming is None
        assert screen._search_text == "r"

    def test_the_hint_line_names_the_key(self):
        import inspect
        from pickhero.ui import menu
        assert "R: rename" in inspect.getsource(menu)

    def test_an_empty_list_says_so(self, tmp_path):
        screen = self._menu(tmp_path)
        self._press(screen, pygame.K_r, unicode="r")
        assert "Nothing selected" in screen._reload_note


class TestTheEditorOwnsTheKeyboardEverywhere:
    """*"Während ich im Rename bin, darf ich gewisse Buchstaben nicht
    drücken. Mit O komme ich direkt in Settings."*

    The song list's own handler already gave the editor every key. The App
    never got that far: it consumes D, O, S, G and U BEFORE handing the
    event on, guarded by `is_searching` -- a test written when the search
    box was the only text field there was.
    """

    def _app(self, tmp_path):
        """An App wired to a real menu, without a window.

        `App.__init__` does not build the menu -- `run()` does -- so it is
        assembled the way `test_song_list` already assembles one.
        """
        from pickhero.config import Config
        from pickhero.ui.app import App
        from pickhero.ui.menu import MenuScreen
        config = _config()
        config.songs_dir = str(tmp_path)
        app = App.__new__(App)
        app._config = config
        app._menu = MenuScreen(tmp_path, config=config)
        app._state = "menu"
        app._return_to = "menu"
        app._tuner_menu = None
        app._settings_menu = None
        app._download_menu = None
        app._device_menu = None
        app._quit_armed = False
        app._held = set()
        app._open_tuner = lambda came_from: setattr(app, "_state", "tuner")
        app._open_device_menu = lambda came_from: setattr(app, "_state",
                                                          "device")
        app._open_calibration = lambda came_from: setattr(app, "_state",
                                                          "calibrate")
        return app

    def _press(self, app, key, mod=0, unicode=""):
        app._handle_menu_event(pygame.event.Event(
            pygame.KEYDOWN, key=key, mod=mod, unicode=unicode))

    @pytest.mark.parametrize("key,letter", [
        (pygame.K_o, "o"), (pygame.K_s, "s"), (pygame.K_d, "d"),
        (pygame.K_g, "g"), (pygame.K_u, "u"),
    ])
    def test_every_shortcut_letter_is_just_a_letter(self, tmp_path, key,
                                                    letter):
        _song(tmp_path)
        app = self._app(tmp_path)
        self._press(app, pygame.K_r, unicode="r")
        app._menu._rename_text = ""
        self._press(app, key, unicode=letter)
        assert app._state == "menu", f"{letter} left the song list"
        assert app._menu._rename_text == letter

    def test_shift_u_is_a_capital_u_and_not_the_tuner(self, tmp_path):
        """U2 is a band. A text box that swallows a character is one the
        player cannot finish a name in."""
        _song(tmp_path)
        app = self._app(tmp_path)
        self._press(app, pygame.K_r, unicode="r")
        app._menu._rename_text = ""
        self._press(app, pygame.K_u, mod=pygame.KMOD_LSHIFT, unicode="U")
        assert app._state == "menu"
        assert app._menu._rename_text == "U"

    def test_but_shift_u_still_reaches_the_tuner_while_searching(self,
                                                                 tmp_path):
        """That exception was asked for and it keeps working: tuning up in
        the middle of hunting for a song is exactly when it is wanted."""
        _song(tmp_path)
        app = self._app(tmp_path)
        self._press(app, pygame.K_f, unicode="f")
        self._press(app, pygame.K_u, mod=pygame.KMOD_LSHIFT, unicode="U")
        assert app._state == "tuner"

    def test_and_o_still_opens_the_settings_when_nothing_is_being_typed(
            self, tmp_path):
        _song(tmp_path)
        app = self._app(tmp_path)
        self._press(app, pygame.K_o, unicode="o")
        assert app._state == "settings"

    def test_is_typing_covers_both_boxes(self, tmp_path):
        _song(tmp_path)
        app = self._app(tmp_path)
        assert not app._menu.is_typing
        self._press(app, pygame.K_r, unicode="r")
        assert app._menu.is_typing and app._menu.is_renaming
        self._press(app, pygame.K_ESCAPE)
        assert not app._menu.is_typing


class TestRenamingBeforeTheFirstOpen:
    """*"Jetzt habe ich vor dem ersten Öffnen ein Rename gemacht. Das MP3
    wurde wieder nicht gefunden."*

    The download writes `song_mp3_paths[key] = "<songs>/Old Name.mp3"`. The
    rename moves the FILE and carries the ENTRY to the new key -- with the
    old file name still inside it. So the note pointed at a file that was no
    longer there, which is not "no recording": it is a WRONG one, and the
    adoption that would have found the right one only ran when the note was
    empty. The rule "the file on disk outranks the note about it" had been
    written down and then applied by halves.
    """

    def _config_with_path(self, tmp_path, stem):
        config = _config(stem)
        config.set_mp3_path_for(stem, str(tmp_path / f"{stem}.mp3"))
        return config

    def test_the_stored_path_follows_the_file(self, tmp_path):
        stem = "Thunder - Love Walked In v3"
        tab = _song(tmp_path, stem)
        config = self._config_with_path(tmp_path, stem)
        remove.rename_song(tab, "AC-DC - Thunder", config)
        assert config.mp3_path_for("AC-DC - Thunder") == str(
            tmp_path / "AC-DC - Thunder.mp3")

    def test_a_recording_that_did_not_move_is_left_pointing_at_itself(
            self, tmp_path):
        """His own file, somewhere else entirely. The rename did not touch
        it and neither does this."""
        stem = "Thunder - Love Walked In v3"
        tab = _song(tmp_path, stem)
        mine = tmp_path / "elsewhere.mp3"
        mine.write_bytes(b"x")
        config = _config(stem)
        config.set_mp3_path_for(stem, str(mine))
        remove.rename_song(tab, "AC-DC - Thunder", config)
        assert config.mp3_path_for("AC-DC - Thunder") == str(mine)

    def test_and_the_song_finds_it_even_if_the_note_is_wrong(self, tmp_path,
                                                             monkeypatch):
        """The belt to the braces: whatever the note says, a file beside the
        tab under the tab's own stem is the recording."""
        import pygame
        from pickhero.audio.mp3_playback import Mp3Player
        from pickhero.ui.scrolling import PlayingScreen
        from tests.test_songsterr import _song as _timeline
        pygame.init()
        pygame.display.set_mode((640, 480))
        tab = tmp_path / "AC-DC - Thunder.gp5"
        tab.write_bytes(b"gp")
        (tmp_path / "AC-DC - Thunder.mp3").write_bytes(b"x")
        config = _config("AC-DC - Thunder")
        # The note a rename used to leave behind: right key, dead path.
        config.set_mp3_path_for("AC-DC - Thunder",
                                str(tmp_path / "Old Name.mp3"))
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        screen = PlayingScreen(_timeline(), config=config,
                               song_key="AC-DC - Thunder",
                               song_path=str(tab))
        assert screen._mp3_path() == str(tmp_path / "AC-DC - Thunder.mp3")

    def test_a_path_that_is_merely_moved_is_still_respected(self, tmp_path,
                                                            monkeypatch):
        """`mp3_path_for` already falls back to the same NAME in the songs
        folder for a settings file carried to a second machine. That is a
        live path, not a leftover, and adoption must not fight it."""
        import pygame
        from pickhero.audio.mp3_playback import Mp3Player
        from pickhero.ui.scrolling import PlayingScreen
        from tests.test_songsterr import _song as _timeline
        pygame.init()
        pygame.display.set_mode((640, 480))
        tab = tmp_path / "Song.gp5"
        tab.write_bytes(b"gp")
        mine = tmp_path / "my take.mp3"
        mine.write_bytes(b"x")
        (tmp_path / "Song.mp3").write_bytes(b"x")
        config = _config("Song")
        config.set_mp3_path_for("Song", str(mine))
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        screen = PlayingScreen(_timeline(), config=config, song_key="Song",
                               song_path=str(tab))
        assert screen._mp3_path() == str(mine)
