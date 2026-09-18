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
        assert any("1 new song" in line for line in screen._transfer_lines)

    def test_the_report_is_cleared_by_any_key(self, tmp_path, monkeypatch):
        import pygame
        _song(tmp_path / "stick")
        screen = self._menu(tmp_path, tmp_path / "stick", monkeypatch)
        self._press(screen, pygame.K_i, mod=pygame.KMOD_LCTRL)
        assert screen._transfer_lines
        self._press(screen, pygame.K_DOWN)
        assert screen._transfer_lines == []

    def test_cancelling_the_chooser_does_nothing(self, tmp_path,
                                                 monkeypatch):
        import pygame
        screen = self._menu(tmp_path, "", monkeypatch)
        self._press(screen, pygame.K_i, mod=pygame.KMOD_LCTRL)
        assert screen._transfer_lines == []

    def test_it_works_with_the_filter_box_open(self, tmp_path, monkeypatch):
        """Ctrl, like Ctrl+M, so it reaches through the text box."""
        import pygame
        _song(tmp_path / "stick")
        screen = self._menu(tmp_path, tmp_path / "stick", monkeypatch)
        self._press(screen, pygame.K_f, unicode="f")
        self._press(screen, pygame.K_i, mod=pygame.KMOD_LCTRL)
        assert screen._transfer_lines

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


# ── History without the tabs ────────────────────────────────────────────────

def _runs_file(folder, stem, marks, started):
    """A `.runs.json` beside a tab that may or may not be there."""
    from pickhero import runs
    folder.mkdir(parents=True, exist_ok=True)
    runs.save(folder / f"{stem}.gp5",
              [runs.Run(notes=marks, started=started, seconds=60.0,
                        note_count=len(marks))])
    return folder / f"{stem}.runs.json"


class TestAFolderOfHistoriesWithNoTabs:
    """*"Da gp, mp3, songsterr schon auf NB2 sind, sehe ich keinen Grund
    diese jedes Mal mitzukopieren."*

    He is right, and it did not work: the import walked TABS, so a folder
    holding `<song>.runs.json` and nothing beside it was read as an empty
    folder and reported as one. Every test here fails on that version.
    """

    def test_the_runs_are_merged_into_the_tab_we_already_have(self, tmp_path):
        from pickhero import runs
        songs, config = _here(tmp_path)
        _song(songs)                      # the whole song, already here
        _runs_file(songs, "AC-DC - Thunder", "hh",
                   "2026-09-01T20:00:00+00:00")
        _runs_file(tmp_path / "stick" / "songs", "AC-DC - Thunder", "cc",
                   "2026-09-02T20:00:00+00:00")
        report = transfer.import_from(tmp_path / "stick", config)
        assert report.runs_added == 1
        stored = [r.notes for r in runs.load(songs / "AC-DC - Thunder.gp5")]
        assert stored == ["hh", "cc"]

    def test_the_settings_arrive_where_there_are_none(self, tmp_path):
        songs, config = _here(tmp_path)
        (songs / "AC-DC - Thunder.gp5").write_bytes(b"gp")
        (tmp_path / "stick" / "songs").mkdir(parents=True)
        (tmp_path / "stick" / "songs"
         / "AC-DC - Thunder.mysician.json").write_text("{}", encoding="utf-8")
        transfer.import_from(tmp_path / "stick", config)
        assert (songs / "AC-DC - Thunder.mysician.json").is_file()

    def test_it_does_not_claim_a_new_song(self, tmp_path):
        """No tab arrived, so nothing new is playable. Saying "1 new song"
        would be a count of something else."""
        songs, config = _here(tmp_path)
        _song(songs)
        _runs_file(songs, "AC-DC - Thunder", "hh", "2026-09-01T20:00:00+00:00")
        _runs_file(tmp_path / "stick", "AC-DC - Thunder", "cc",
                   "2026-09-02T20:00:00+00:00")
        report = transfer.import_from(tmp_path / "stick", config)
        assert report.songs_added == []
        assert report.anything                    # and NOT "Nothing new"
        assert not any("Nothing new" in line for line in report.lines())

    def test_a_stray_file_cannot_invent_a_song(self, tmp_path):
        """Nothing here has a tab for it, so nothing can say which song it
        is a belonging OF. It is left alone rather than copied in."""
        songs, config = _here(tmp_path)
        stick = tmp_path / "stick"
        stick.mkdir()
        (stick / "holiday snaps.mp3").write_bytes(b"not a song")
        _runs_file(stick, "A Song Nobody Has", "hh",
                   "2026-09-02T20:00:00+00:00")
        report = transfer.import_from(stick, config)
        assert list(songs.iterdir()) == []
        assert report.files_added == 0

    def test_running_it_twice_changes_nothing(self, tmp_path):
        from pickhero import runs
        songs, config = _here(tmp_path)
        _song(songs)
        _runs_file(songs, "AC-DC - Thunder", "hh", "2026-09-01T20:00:00+00:00")
        _runs_file(tmp_path / "stick", "AC-DC - Thunder", "cc",
                   "2026-09-02T20:00:00+00:00")
        transfer.import_from(tmp_path / "stick", config)
        again = transfer.import_from(tmp_path / "stick", config)
        assert again.runs_added == 0
        assert len(runs.load(songs / "AC-DC - Thunder.gp5")) == 2

    def test_the_history_file_answers_about_itself(self):
        """`path_for` given the history returns it, so a caller holding one
        does not have to invent a tab beside it."""
        from pathlib import Path
        from pickhero import runs
        here = Path("/x/Song.runs.json")
        assert runs.path_for(here) == here
        assert runs.path_for(Path("/x/Song.gp5")) == here


class TestWritingTheFolderToCarry:
    """*"Am liebsten waere mir ein Button oder Key in der Uebersicht, um alle
    History/Rundaten (ohne MP3, gp, songsterr) in einen Ordner zu
    schreiben."*"""

    def _played(self, tmp_path):
        songs, config = _here(tmp_path)
        _song(songs)
        _runs_file(songs, "AC-DC - Thunder", "hhc",
                   "2026-09-01T20:00:00+00:00")
        mine = tmp_path / "mine" / ".pickhero"
        mine.mkdir(parents=True)
        practice_log.write(mine / "practice_log.jsonl",
                           [_session("2026-09-01T20:00:00")])
        (mine / "progress.json").write_text(
            json.dumps({"AC-DC - Thunder": {"best_accuracy": 0.9}}),
            encoding="utf-8")
        return songs, config, mine

    def test_what_it_writes_and_what_it_leaves_behind(self, tmp_path):
        songs, config, mine = self._played(tmp_path)
        out = tmp_path / "stick"
        report = transfer.export_to(out, config, into=mine)
        written = sorted(p.name for p in out.rglob("*") if p.is_file())
        assert written == ["AC-DC - Thunder.mysician.json",
                           "AC-DC - Thunder.runs.json",
                           "practice_log.jsonl", "progress.json"]
        assert report.songs == ["AC-DC - Thunder"]
        assert report.runs == 1
        assert report.sittings == 1

    def test_no_tab_no_recording_no_bar_map(self, tmp_path):
        """The megabytes are already on the other computer."""
        songs, config, mine = self._played(tmp_path)
        out = tmp_path / "stick"
        transfer.export_to(out, config, into=mine)
        names = [p.name for p in out.rglob("*") if p.is_file()]
        assert not any(n.endswith((".gp5", ".mp3", ".songsterr.json"))
                       for n in names)

    def test_the_other_machine_reads_it_back(self, tmp_path):
        """The round trip, which is the whole point: what this writes is
        what Ctrl+I reads."""
        from pickhero import runs
        songs, config, mine = self._played(tmp_path)
        out = tmp_path / "stick"
        transfer.export_to(out, config, into=mine)

        # NB2: the same song, a different evening, nothing else shared.
        theirs = tmp_path / "nb2" / "songs"
        theirs.mkdir(parents=True)
        (theirs / "AC-DC - Thunder.gp5").write_bytes(b"gp")
        _runs_file(theirs, "AC-DC - Thunder", "mmm",
                   "2026-08-30T20:00:00+00:00")
        other = Config()
        other.songs_dir = str(theirs)
        other.save = lambda: None
        report = transfer.import_from(out, other,
                                      into=tmp_path / "nb2" / ".pickhero")
        assert report.runs_added == 1
        assert [r.notes for r in runs.load(theirs / "AC-DC - Thunder.gp5")] \
            == ["mmm", "hhc"]
        assert report.sittings_added == 1
        assert (theirs / "AC-DC - Thunder.gp5").read_bytes() == b"gp"

    def test_it_refuses_this_machines_own_folders(self, tmp_path):
        songs, config, mine = self._played(tmp_path)
        for folder in (songs, mine):
            report = transfer.export_to(folder, config, into=mine)
            assert report.problems and not report.songs

    def test_a_machine_that_has_played_nothing_says_so(self, tmp_path):
        songs, config = _here(tmp_path)
        empty = tmp_path / "mine" / ".pickhero"
        empty.mkdir(parents=True)
        report = transfer.export_to(tmp_path / "stick", config, into=empty)
        assert any("Nothing to write" in line for line in report.lines())

    def test_the_report_says_the_folder_is_only_half_a_song(self, tmp_path):
        songs, config, mine = self._played(tmp_path)
        said = " ".join(transfer.export_to(tmp_path / "stick", config,
                                           into=mine).lines())
        assert "press Ctrl+I there" in said


class TestTheExportKey:

    def _menu(self, tmp_path, chosen, monkeypatch):
        from pickhero.ui.menu import MenuScreen
        songs, config = _here(tmp_path)
        _song(songs)
        _runs_file(songs, "AC-DC - Thunder", "hh", "2026-09-01T20:00:00+00:00")
        monkeypatch.setattr("pickhero.ui.filepick.pick_folder",
                            lambda *a, **k: str(chosen))
        return MenuScreen(songs, config=config)

    def _press(self, screen, key, mod=0, unicode=""):
        import pygame
        screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=key,
                                               mod=mod, unicode=unicode))

    def test_ctrl_e_writes_the_folder_and_reports(self, tmp_path,
                                                 monkeypatch):
        import pygame
        out = tmp_path / "stick"
        screen = self._menu(tmp_path, out, monkeypatch)
        self._press(screen, pygame.K_e, mod=pygame.KMOD_LCTRL)
        assert (out / "songs" / "AC-DC - Thunder.runs.json").is_file()
        assert screen._transfer_lines

    def test_cancelling_the_chooser_does_nothing(self, tmp_path, monkeypatch):
        import pygame
        screen = self._menu(tmp_path, "", monkeypatch)
        self._press(screen, pygame.K_e, mod=pygame.KMOD_LCTRL)
        assert screen._transfer_lines == []

    def test_a_plain_e_is_not_it(self, tmp_path, monkeypatch):
        """It types an "e" into the filter box, like any other letter."""
        import pygame
        out = tmp_path / "stick"
        screen = self._menu(tmp_path, out, monkeypatch)
        self._press(screen, pygame.K_e, unicode="e")
        assert not out.exists()

    def test_it_works_with_the_filter_box_open(self, tmp_path, monkeypatch):
        import pygame
        out = tmp_path / "stick"
        screen = self._menu(tmp_path, out, monkeypatch)
        self._press(screen, pygame.K_f, unicode="f")
        self._press(screen, pygame.K_e, mod=pygame.KMOD_LCTRL)
        assert screen._transfer_lines

    def test_both_hints_name_it(self):
        import inspect
        from pickhero.ui import menu
        for line in inspect.getsource(menu).splitlines():
            if "Type to search" in line or "F or /: search" in line:
                assert "Ctrl+E" in line
