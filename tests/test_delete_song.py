"""DEL removes a tab and everything the app put there for it.

*"Ich brauche eine Möglichkeit Tabs inkl. allem (außer History) zu löschen."*

A tab stopped being one file the day the download screen started writing a
bar map and an MP3 beside it -- and `song_key` is the tab's STEM, so a tab
deleted in Explorer leaves nine settings behind for the next song that takes
the same name to inherit.

The two rules these assert are the ones that make deleting safe rather than
merely thorough: only what sits beside the tab under the tab's own name, and
the practice history is never touched.
"""

import pygame
import pytest

from pickhero.config import Config
from pickhero.tabs import remove


def _song(folder, stem="AC-DC - Thunder", audio=True, cache=True):
    tab = folder / f"{stem}.gp5"
    tab.write_bytes(b"gp")
    if cache:
        (folder / f"{stem}.songsterr.json").write_text("{}", encoding="utf-8")
    if audio:
        (folder / f"{stem}.mp3").write_bytes(b"audio")
    return tab


class TestWhatCountsAsBelongingToTheSong:

    def test_the_tab_its_bar_map_and_its_audio(self, tmp_path):
        tab = _song(tmp_path)
        names = {p.name for p in remove.belongings(tab)}
        assert names == {"AC-DC - Thunder.gp5",
                         "AC-DC - Thunder.songsterr.json",
                         "AC-DC - Thunder.mp3"}

    def test_the_tab_comes_first(self, tmp_path):
        """So the confirmation can count files without listing nothing when
        the tab is all there is."""
        assert remove.belongings(_song(tmp_path))[0].suffix == ".gp5"

    def test_a_recording_the_player_picked_himself_is_his(self, tmp_path):
        """The rule that makes this safe: same folder AND same name, or it
        is not touched. An MP3 out of his Downloads folder is not the app's
        to delete, and neither is one sitting in the songs folder under a
        name this app did not choose."""
        tab = _song(tmp_path, audio=False)
        (tmp_path / "whatsup_original.mp3").write_bytes(b"his")
        (tmp_path / "AC-DC - Thunder (live).mp3").write_bytes(b"his too")
        names = {p.name for p in remove.belongings(tab)}
        assert names == {"AC-DC - Thunder.gp5",
                         "AC-DC - Thunder.songsterr.json"}

    def test_another_song_in_the_same_folder_is_untouched(self, tmp_path):
        tab = _song(tmp_path)
        other = _song(tmp_path, stem="Thunder - Love Walked In")
        remove.delete_song(tab)
        assert other.is_file()
        assert (tmp_path / "Thunder - Love Walked In.mp3").is_file()

    def test_a_tab_with_nothing_beside_it(self, tmp_path):
        tab = _song(tmp_path, audio=False, cache=False)
        assert remove.belongings(tab) == [tab]


class TestDeleting:

    def _config(self, song_key="AC-DC - Thunder"):
        config = Config()
        config.save = lambda: None
        config.set_songsterr_for(song_key, 2333598)
        config.set_mp3_path_for(song_key, "/somewhere/x.mp3")
        config.set_tempo_factor_for(song_key, 0.7)
        config.set_sync_source_for(song_key, "songsterr")
        config.set_transpose_for(song_key, 2)
        config.set_mp3_anchors_for(song_key, [(0.0, -100.0)])
        config.set_favourite(song_key, True)
        return config

    def test_the_files_and_the_settings_both_go(self, tmp_path):
        tab = _song(tmp_path)
        config = self._config()
        report = remove.delete_song(tab, config)
        assert report.ok
        assert len(report.files) == 3
        assert not tab.exists()
        assert not (tmp_path / "AC-DC - Thunder.mp3").exists()
        assert config.songsterr_for("AC-DC - Thunder") == 0
        assert config.tempo_factor_for("AC-DC - Thunder") == 1.0
        assert config.mp3_anchors_for("AC-DC - Thunder") == []
        assert not config.is_favourite("AC-DC - Thunder")

    def test_every_per_song_setting_is_found_not_listed(self, tmp_path):
        """A hand-written list would be right on the day it was written and
        quietly wrong the first time a tenth per-song dict was added. The
        fields are walked, so this asserts the walking, not a list."""
        config = self._config()
        dropped = set(config.forget_song("AC-DC - Thunder"))
        per_song = {f for f in vars(config)
                    if f.startswith("song_") and isinstance(
                        getattr(config, f), dict)}
        assert dropped - {"favourites"} <= per_song
        assert {"song_songsterr", "song_mp3_paths", "song_tempo_factors",
                "song_sync_source", "song_transpose", "song_mp3_anchors",
                "favourites"} <= dropped

    def test_the_practice_history_is_not_touched(self, tmp_path,
                                                 monkeypatch):
        """He asked for this by name -- *außer History*. It is a record of
        what he DID, and deleting a file does not undo an evening."""
        from pickhero import progress
        # Never the player's real file: a test that deletes an evening of
        # practice to prove that deleting does not delete it would be a poor
        # joke.
        monkeypatch.setattr(progress, "PROGRESS_FILE",
                            tmp_path / "progress.json")
        monkeypatch.setattr(progress, "CONFIG_DIR", tmp_path)
        tab = _song(tmp_path)
        progress.ProgressTracker().record_result(
            "AC-DC - Thunder",
            {"accuracy_percent": 91.0, "hits": 120, "total": 130})

        remove.delete_song(tab, self._config())

        kept = progress.ProgressTracker().get_best("AC-DC - Thunder")
        assert kept is not None and kept.best_accuracy == 91.0

    def test_a_file_that_will_not_go_is_named(self, tmp_path, monkeypatch):
        """A delete that reports success while the song is still in the list
        is the worst of the three outcomes."""
        tab = _song(tmp_path, audio=False, cache=False)

        def locked(self):
            raise OSError(32, "The process cannot access the file")

        monkeypatch.setattr("pathlib.Path.unlink", locked)
        report = remove.delete_song(tab, None)
        assert not report.ok
        assert "AC-DC - Thunder.gp5" in report.failed[0]
        assert "cannot delete" not in report.summary().lower()
        assert "Could not delete" in report.summary()

    def test_settings_are_optional(self, tmp_path):
        report = remove.delete_song(_song(tmp_path), None)
        assert report.ok and report.settings == []

    def test_the_summary_says_the_history_is_kept(self, tmp_path):
        report = remove.delete_song(_song(tmp_path), self._config())
        assert "History kept" in report.summary()


class TestTheKey:
    """DEL twice, because it sits one row from the arrow keys."""

    def _menu(self, tmp_path, monkeypatch):
        from pickhero.ui.menu import MenuScreen
        config = Config()
        config.save = lambda: None
        screen = MenuScreen(tmp_path, config=config)
        return screen

    def _press(self, screen, key, mod=0):
        return screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=key, mod=mod, unicode=""))

    def test_one_press_asks_and_deletes_nothing(self, tmp_path, monkeypatch):
        tab = _song(tmp_path)
        screen = self._menu(tmp_path, monkeypatch)
        self._press(screen, pygame.K_DELETE)
        assert tab.is_file()
        assert "Delete AC-DC - Thunder and 3 files?" in screen._reload_note
        assert "history is kept" in screen._reload_note

    def test_two_presses_delete(self, tmp_path, monkeypatch):
        tab = _song(tmp_path)
        screen = self._menu(tmp_path, monkeypatch)
        self._press(screen, pygame.K_DELETE)
        self._press(screen, pygame.K_DELETE)
        assert not tab.exists()
        assert not (tmp_path / "AC-DC - Thunder.mp3").exists()
        assert screen._display_files == []

    def test_any_other_key_cancels(self, tmp_path, monkeypatch):
        tab = _song(tmp_path)
        screen = self._menu(tmp_path, monkeypatch)
        self._press(screen, pygame.K_DELETE)
        self._press(screen, pygame.K_DOWN)
        self._press(screen, pygame.K_DELETE)
        assert tab.is_file(), "the second DEL confirmed a cancelled question"

    def test_an_armed_delete_never_outlives_its_question(self, tmp_path,
                                                         monkeypatch):
        """Arm on one song, move, press again: the second press must ask
        about the song it is now on, not delete the one it was on."""
        first = _song(tmp_path, stem="A song", audio=False, cache=False)
        second = _song(tmp_path, stem="B song", audio=False, cache=False)
        screen = self._menu(tmp_path, monkeypatch)
        self._press(screen, pygame.K_DELETE)
        self._press(screen, pygame.K_DOWN)
        self._press(screen, pygame.K_DELETE)
        assert first.is_file() and second.is_file()
        assert "B song" in screen._reload_note

    def test_del_while_searching_is_left_alone(self, tmp_path, monkeypatch):
        """There it is the key somebody reaches for to fix a typo."""
        tab = _song(tmp_path)
        screen = self._menu(tmp_path, monkeypatch)
        self._press(screen, pygame.K_f)
        assert screen._search_active
        self._press(screen, pygame.K_DELETE)
        self._press(screen, pygame.K_DELETE)
        assert tab.is_file()

    def test_an_empty_list_says_so(self, tmp_path, monkeypatch):
        screen = self._menu(tmp_path, monkeypatch)
        self._press(screen, pygame.K_DELETE)
        assert "Nothing selected" in screen._reload_note

    def test_the_hint_line_names_the_key(self, tmp_path, monkeypatch):
        """A feature nobody can see is one the player has not got."""
        import inspect
        from pickhero.ui import menu
        assert "DEL: delete song" in inspect.getsource(menu)
