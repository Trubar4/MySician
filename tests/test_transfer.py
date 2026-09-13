"""Bringing another computer's work in through a folder.

*"Auf NB2 geht keiner der Wege mit OneDrive, iCloud oder Dropbox. Was wäre
die Alternative, damit ich nur ein Minimum an Files kopieren muss?"*

A laptop that cannot have a cloud client installed still has a USB stick,
and a stick is a real folder. Most of a song already travels in it: since
the settings moved into `<name>.mysician.json`, a song IS its four files.
What is left is the chronological sitting log and whatever still sits in an
older `settings.json`.
"""

import json

import pytest

from pickhero import practice_log, transfer
from pickhero.config import Config


def _session(started, song="Thunder", seconds=600.0):
    return practice_log.Session(started=started, song=song, seconds=seconds,
                                strikes=100, tempo_percent=100)


def _their_machine(folder, sessions=(), progress=None, settings=None):
    """A `.pickhero` folder as it arrives on a stick."""
    theirs = folder / ".pickhero"
    theirs.mkdir(parents=True, exist_ok=True)
    if sessions:
        practice_log.write(theirs / "practice_log.jsonl", list(sessions))
    (theirs / "progress.json").write_text(json.dumps(progress or {}),
                                          encoding="utf-8")
    (theirs / "settings.json").write_text(json.dumps(settings or {}),
                                          encoding="utf-8")
    return theirs


def _song(folder, stem="AC-DC - Thunder", audio=True):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}.gp5").write_bytes(b"gp")
    (folder / f"{stem}.songsterr.json").write_text("{}", encoding="utf-8")
    (folder / f"{stem}.mysician.json").write_text(
        json.dumps({"version": 1, "song": stem,
                    "settings": {"song_transpose": 2}}), encoding="utf-8")
    if audio:
        (folder / f"{stem}.mp3").write_bytes(b"audio")
    return folder / f"{stem}.gp5"


def _here(tmp_path):
    """This machine: a songs folder and a settings folder."""
    songs = tmp_path / "mine" / "songs"
    songs.mkdir(parents=True)
    config = Config()
    config.songs_dir = str(songs)
    config.save = lambda: None
    return songs, config


class TestTheSongsComeAcross:

    def test_a_song_this_machine_has_not_got_arrives_whole(self, tmp_path):
        """Four files: the tab, the bar map, the recording and the settings.
        That is what a song IS now."""
        songs, config = _here(tmp_path)
        _song(tmp_path / "stick")
        report = transfer.import_from(tmp_path / "stick", config)
        assert report.songs_added == ["AC-DC - Thunder"]
        assert report.files_added == 4
        for suffix in (".gp5", ".songsterr.json", ".mysician.json", ".mp3"):
            assert (songs / f"AC-DC - Thunder{suffix}").is_file()

    def test_a_song_already_here_is_never_overwritten(self, tmp_path):
        """The other machine's copy must not silently replace the one being
        practised here."""
        songs, config = _here(tmp_path)
        (songs / "AC-DC - Thunder.gp5").write_bytes(b"MINE")
        _song(tmp_path / "stick")
        transfer.import_from(tmp_path / "stick", config)
        assert (songs / "AC-DC - Thunder.gp5").read_bytes() == b"MINE"

    def test_but_a_file_missing_beside_it_is_still_taken(self, tmp_path):
        """Per FILE, not per song: a sidecar arriving beside a tab we have
        can only add settings we have not got."""
        songs, config = _here(tmp_path)
        (songs / "AC-DC - Thunder.gp5").write_bytes(b"MINE")
        _song(tmp_path / "stick")
        transfer.import_from(tmp_path / "stick", config)
        assert (songs / "AC-DC - Thunder.mysician.json").is_file()
        assert (songs / "AC-DC - Thunder.gp5").read_bytes() == b"MINE"

    def test_songs_are_found_wherever_they_sit_in_the_folder(self, tmp_path):
        """The player picks a stick, not the exact subfolder."""
        songs, config = _here(tmp_path)
        _song(tmp_path / "stick" / "MySician" / "songs")
        report = transfer.import_from(tmp_path / "stick", config)
        assert report.songs_added == ["AC-DC - Thunder"]

    def test_running_it_twice_changes_nothing_the_second_time(self,
                                                              tmp_path):
        songs, config = _here(tmp_path)
        _song(tmp_path / "stick")
        transfer.import_from(tmp_path / "stick", config)
        again = transfer.import_from(tmp_path / "stick", config)
        assert again.songs_added == []
        assert not again.anything

    def test_pointing_it_at_our_own_songs_folder_is_refused(self, tmp_path):
        songs, config = _here(tmp_path)
        report = transfer.import_from(songs, config)
        assert "own songs folder" in report.problems[0]

    def test_a_folder_that_is_not_there(self, tmp_path):
        _, config = _here(tmp_path)
        report = transfer.import_from(tmp_path / "nope", config)
        assert "not there" in report.problems[0]


class TestTheHistoryComesAcross:

    def test_sittings_this_machine_does_not_have_arrive(self, tmp_path):
        songs, config = _here(tmp_path)
        mine = tmp_path / "mine" / ".pickhero"
        mine.mkdir(parents=True)
        practice_log.write(mine / "practice_log.jsonl",
                           [_session("2026-01-01T10:00:00")])
        _their_machine(tmp_path / "stick",
                       sessions=[_session("2026-01-01T10:00:00"),
                                 _session("2026-02-02T11:00:00")])

        report = transfer.import_from(tmp_path / "stick", config, into=mine)

        assert report.sittings_added == 1
        kept = practice_log.read(mine / "practice_log.jsonl")
        assert len(kept) == 2, "the same sitting arrived twice"

    def test_a_better_score_wins_and_a_worse_one_does_not(self, tmp_path):
        songs, config = _here(tmp_path)
        mine = tmp_path / "mine" / ".pickhero"
        mine.mkdir(parents=True)
        (mine / "progress.json").write_text(json.dumps(
            {"Thunder": {"best_accuracy": 90.0, "attempts": 5},
             "One": {"best_accuracy": 80.0, "attempts": 2}}),
            encoding="utf-8")
        _their_machine(tmp_path / "stick", progress={
            "Thunder": {"best_accuracy": 40.0, "attempts": 9},
            "One": {"best_accuracy": 95.0, "attempts": 1}})

        report = transfer.import_from(tmp_path / "stick", config, into=mine)

        kept = json.loads((mine / "progress.json").read_text(encoding="utf-8"))
        assert kept["Thunder"]["best_accuracy"] == 90.0
        assert kept["One"]["best_accuracy"] == 95.0
        assert report.bests_improved == ["One"]
        # attempts is the LARGER, not the sum: a sum cannot be done twice.
        assert kept["Thunder"]["attempts"] == 9

    def test_a_backup_is_left_beside_anything_rewritten(self, tmp_path):
        songs, config = _here(tmp_path)
        mine = tmp_path / "mine" / ".pickhero"
        mine.mkdir(parents=True)
        practice_log.write(mine / "practice_log.jsonl",
                           [_session("2026-01-01T10:00:00")])
        _their_machine(tmp_path / "stick",
                       sessions=[_session("2026-03-03T09:00:00")])
        transfer.import_from(tmp_path / "stick", config, into=mine)
        assert (mine / "practice_log.jsonl.bak").is_file()


class TestWhatMustNotTravel:

    def test_the_machine_s_own_settings_stay_put(self, tmp_path):
        """The audio device index, the calibration and the latency describe
        an interface and a sound card. Carrying them over breaks the other
        computer's input while looking like a settings problem."""
        songs, config = _here(tmp_path)
        mine = tmp_path / "mine" / ".pickhero"
        mine.mkdir(parents=True)
        (mine / "settings.json").write_text(json.dumps(
            {"audio": {"device_index": 3}, "calibration": {"ms": 12},
             "audio_latency_offset_ms": -40.0}), encoding="utf-8")
        _their_machine(tmp_path / "stick", settings={
            "audio": {"device_index": 99}, "calibration": {"ms": 999},
            "audio_latency_offset_ms": 999.0,
            "song_transpose": {"Thunder": 2}})

        transfer.import_from(tmp_path / "stick", config, into=mine)

        kept = json.loads((mine / "settings.json").read_text(encoding="utf-8"))
        assert kept["audio"]["device_index"] == 3
        assert kept["calibration"] == {"ms": 12}
        assert kept["audio_latency_offset_ms"] == -40.0
        assert kept["song_transpose"] == {"Thunder": 2}

    def test_a_per_song_setting_this_machine_has_wins(self, tmp_path):
        songs, config = _here(tmp_path)
        mine = tmp_path / "mine" / ".pickhero"
        mine.mkdir(parents=True)
        (mine / "settings.json").write_text(
            json.dumps({"song_tempo_factors": {"Thunder": 0.5}}),
            encoding="utf-8")
        _their_machine(tmp_path / "stick", settings={
            "song_tempo_factors": {"Thunder": 0.9, "One": 0.7}})

        transfer.import_from(tmp_path / "stick", config, into=mine)

        kept = json.loads((mine / "settings.json").read_text(encoding="utf-8"))
        assert kept["song_tempo_factors"] == {"Thunder": 0.5, "One": 0.7}

    def test_every_per_song_setting_is_found_not_listed(self):
        from dataclasses import fields
        blank = Config()
        every = {f.name for f in fields(Config)
                 if f.name.startswith("song_")
                 and isinstance(getattr(blank, f.name, None), dict)}
        assert set(transfer.song_settings()) == every

    def test_the_report_says_so_out_loud(self, tmp_path):
        songs, config = _here(tmp_path)
        _song(tmp_path / "stick")
        said = " ".join(transfer.import_from(tmp_path / "stick",
                                             config).lines())
        assert "audio device, calibration and latency were not touched" in said


class TestSayingWhatWouldHappen:

    def test_a_dry_run_writes_nothing(self, tmp_path):
        songs, config = _here(tmp_path)
        _song(tmp_path / "stick")
        report = transfer.import_from(tmp_path / "stick", config,
                                      dry_run=True)
        assert report.songs_added == ["AC-DC - Thunder"]
        assert not (songs / "AC-DC - Thunder.gp5").exists()
        assert "Would import" in report.lines()[0]

    def test_an_empty_folder_says_nothing_new(self, tmp_path):
        songs, config = _here(tmp_path)
        (tmp_path / "stick").mkdir()
        said = transfer.import_from(tmp_path / "stick", config).lines()
        assert any("Nothing new" in line for line in said)

    def test_songs_but_no_settings_folder_is_not_a_failure(self, tmp_path):
        songs, config = _here(tmp_path)
        _song(tmp_path / "stick")
        report = transfer.import_from(tmp_path / "stick", config)
        assert report.songs_added and not report.problems


class TestTheKey:

    def _menu(self, tmp_path, chosen, monkeypatch):
        from pickhero.ui import menu as menu_module
        from pickhero.ui.menu import MenuScreen
        songs, config = _here(tmp_path)
        monkeypatch.setattr("pickhero.ui.filepick.pick_folder",
                            lambda *a, **k: str(chosen))
        return MenuScreen(songs, config=config)

    def _press(self, screen, key, mod=0, unicode=""):
        import pygame
        screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=key,
                                               mod=mod, unicode=unicode))

    def test_ctrl_i_imports_and_the_song_appears(self, tmp_path,
                                                 monkeypatch):
        import pygame
        _song(tmp_path / "stick")
        screen = self._menu(tmp_path, tmp_path / "stick", monkeypatch)
        assert screen._display_files == []
        self._press(screen, pygame.K_i, mod=pygame.KMOD_LCTRL)
        assert [p.stem for p in screen._display_files] == ["AC-DC - Thunder"]
        assert any("1 new song" in line for line in screen._import_lines)

    def test_the_report_is_cleared_by_any_key(self, tmp_path, monkeypatch):
        import pygame
        _song(tmp_path / "stick")
        screen = self._menu(tmp_path, tmp_path / "stick", monkeypatch)
        self._press(screen, pygame.K_i, mod=pygame.KMOD_LCTRL)
        assert screen._import_lines
        self._press(screen, pygame.K_DOWN)
        assert screen._import_lines == []

    def test_cancelling_the_chooser_does_nothing(self, tmp_path,
                                                 monkeypatch):
        import pygame
        screen = self._menu(tmp_path, "", monkeypatch)
        self._press(screen, pygame.K_i, mod=pygame.KMOD_LCTRL)
        assert screen._import_lines == []

    def test_it_works_with_the_filter_box_open(self, tmp_path, monkeypatch):
        """Ctrl, like Ctrl+M, so it reaches through the text box."""
        import pygame
        _song(tmp_path / "stick")
        screen = self._menu(tmp_path, tmp_path / "stick", monkeypatch)
        self._press(screen, pygame.K_f, unicode="f")
        self._press(screen, pygame.K_i, mod=pygame.KMOD_LCTRL)
        assert screen._import_lines

    def test_both_hints_name_it(self):
        import inspect
        from pickhero.ui import menu
        for line in inspect.getsource(menu).splitlines():
            if "Type to search" in line or "F or /: search" in line:
                assert "Ctrl+I" in line


class TestChoosingTheSongsFolder:
    """*"Wie kann ich jetzt den Standort-Ordner ändern?"*

    It could only be done with `--songs` on the command line, which on the
    laptop that has nothing but MySician.exe on it means it could not be
    done at all -- the same gap that made `tools/merge_stats.py` useless
    where it was most needed.
    """

    def _app(self, tmp_path, chosen, monkeypatch):
        import pygame
        from pickhero.ui.app import App
        from pickhero.ui.menu import MenuScreen
        from pickhero.ui.settings_menu import SettingsMenuScreen
        songs, config = _here(tmp_path)
        (songs / "Old song.gp5").write_bytes(b"gp")
        monkeypatch.setattr("pickhero.ui.filepick.pick_folder",
                            lambda *a, **k: str(chosen))
        app = App.__new__(App)
        app._config = config
        app._menu = MenuScreen(songs, config=config)
        app._settings_menu = SettingsMenuScreen(config)
        app._state = "settings"
        return app

    def test_the_row_is_there_and_says_where_it_is(self, tmp_path):
        from pickhero.ui.settings_menu import SettingsMenuScreen
        songs, config = _here(tmp_path)
        rows = {r.key: r for r in SettingsMenuScreen(config)._rows}
        assert "songs" in rows
        assert rows["songs"].value().endswith("songs")

    def test_a_long_path_is_shortened_from_the_front(self, tmp_path):
        """The half that identifies it is the END -- every one of these
        paths starts the same way."""
        from pickhero.ui.settings_menu import SettingsMenuScreen
        _, config = _here(tmp_path)
        config.songs_dir = "C:\\\\Users\\\\Admin\\\\Documents\\\\Guitar\\\\Tabs\\\\everything\\\\songs"
        shown = {r.key: r for r in SettingsMenuScreen(config)._rows}[
            "songs"].value()
        assert shown.startswith("…") and shown.endswith("songs")
        assert len(shown) <= 42

    def test_choosing_one_points_the_list_at_it(self, tmp_path,
                                                monkeypatch):
        elsewhere = tmp_path / "other place"
        elsewhere.mkdir()
        (elsewhere / "New song.gp5").write_bytes(b"gp")
        app = self._app(tmp_path, elsewhere, monkeypatch)
        app._choose_songs_folder()
        assert [p.stem for p in app._menu._display_files] == ["New song"]
        assert app._config.songs_dir == str(elsewhere.resolve())

    def test_the_path_is_stored_absolute(self, tmp_path, monkeypatch):
        """A relative one resolves against wherever the .exe was started
        from, which is how this app once died before drawing a frame."""
        elsewhere = tmp_path / "other place"
        elsewhere.mkdir()
        app = self._app(tmp_path, elsewhere, monkeypatch)
        app._choose_songs_folder()
        from pathlib import Path as _Path
        assert _Path(app._config.songs_dir).is_absolute()

    def test_it_goes_back_to_the_list(self, tmp_path, monkeypatch):
        """The answer to "did that work" is the list of songs, not a
        settings row."""
        elsewhere = tmp_path / "other place"
        elsewhere.mkdir()
        app = self._app(tmp_path, elsewhere, monkeypatch)
        app._choose_songs_folder()
        assert app._state == "menu" and app._settings_menu is None

    def test_cancelling_changes_nothing(self, tmp_path, monkeypatch):
        app = self._app(tmp_path, "", monkeypatch)
        before = app._config.songs_dir
        app._choose_songs_folder()
        assert app._config.songs_dir == before
        assert app._state == "settings"

    def test_the_new_folder_s_sidecars_are_picked_up(self, tmp_path,
                                                     monkeypatch):
        """Pointing at a folder carried over from the other machine has to
        bring its settings with it, like any other scan."""
        elsewhere = tmp_path / "from nb1"
        _song(elsewhere)
        app = self._app(tmp_path, elsewhere, monkeypatch)
        app._choose_songs_folder()
        assert app._config.transpose_for("AC-DC - Thunder") == 2
