"""A song's settings live beside the song, so the songs folder is the sync.

*"Wie bekomme ich alle Songs von NB1 auf NB2 mit Syncs etc.? Ich könnte
einen iCloud Link nutzen."*

Copying the songs folder carried the tab, its bar map and its recording --
and left the expensive part behind. The practice speed, the sync points, the
Songsterr id, the transpose and the star all lived in one `settings.json` in
the home folder, keyed by the tab's stem.

**Two machines writing one settings file is the problem, not the copying.**
A cloud folder syncs files; it cannot merge two edits of one JSON, and this
app saves that file on almost every keypress.
"""

import json

import pytest

from pickhero.config import Config
from pickhero.tabs import sidecar


def _song(folder, stem="AC-DC - Thunder"):
    tab = folder / f"{stem}.gp5"
    tab.write_bytes(b"gp")
    return tab


def _config(song_key="AC-DC - Thunder", mp3=None):
    config = Config()
    config.save = lambda: None
    config.set_songsterr_for(song_key, 2333598)
    config.set_sync_source_for(song_key, "songsterr")
    config.set_tempo_factor_for(song_key, 0.7)
    config.set_mp3_anchors_for(song_key, [(0.0, -120.0), (60_000.0, -130.0)])
    config.set_mp3_offset_for(song_key, 70.0)
    config.set_transpose_for(song_key, 2)
    config.set_favourite(song_key, True)
    if mp3:
        config.set_mp3_path_for(song_key, mp3)
    return config


class TestWhatTravels:

    def test_every_per_song_setting_is_found_not_listed(self, tmp_path):
        """The fourth reader of this set -- forget_song, rename_song,
        merge_stats and this -- and four are only safe because none of them
        writes the names down."""
        from dataclasses import fields
        config = Config()
        every = {f.name for f in fields(Config)
                 if f.name.startswith("song_")
                 and isinstance(getattr(config, f.name, None), dict)}
        assert set(sidecar.song_fields(config)) == every

    def test_the_expensive_things_are_in_it(self, tmp_path):
        got = sidecar.collect("AC-DC - Thunder", _config())["settings"]
        assert got["song_mp3_anchors"] == [[0.0, -120.0], [60_000.0, -130.0]]
        assert got["song_songsterr"] == 2333598
        assert got["song_sync_source"] == "songsterr"
        assert got["song_mp3_offsets"] == 70.0
        assert got["song_transpose"] == 2

    def test_the_star_travels(self, tmp_path):
        assert sidecar.collect("AC-DC - Thunder", _config())["favourite"]

    def test_the_recording_travels_as_a_name_not_a_path(self, tmp_path):
        r"""C:\Users\Admin\... means nothing on the other laptop, and the
        file itself is right there beside the tab."""
        got = sidecar.collect("AC-DC - Thunder", _config(
            mp3=r"C:\Users\Admin\songs\AC-DC - Thunder.mp3"))
        assert got["settings"]["song_mp3_paths"] == "AC-DC - Thunder.mp3"

    def test_nothing_belonging_to_the_machine_is_in_it(self, tmp_path):
        """The audio device, the calibration and the latency describe an
        interface and a sound card."""
        got = json.dumps(sidecar.collect("AC-DC - Thunder", _config()))
        for machine in ("calibration", "device_index", "latency", "theme",
                        "songs_dir"):
            assert machine not in got


class TestTakingItOnTheOtherMachine:

    def _sent(self, tmp_path, **kw):
        tab = _song(tmp_path)
        sidecar.write(tab, "AC-DC - Thunder", _config(**kw))
        return tab

    def test_a_song_this_laptop_has_never_seen_gets_everything(self,
                                                               tmp_path):
        tab = self._sent(tmp_path)
        fresh = Config()
        taken = sidecar.adopt(tab, "AC-DC - Thunder", fresh)
        assert fresh.songsterr_for("AC-DC - Thunder") == 2333598
        assert fresh.tempo_factor_for("AC-DC - Thunder") == 0.7
        assert fresh.mp3_anchors_for("AC-DC - Thunder") == [
            (0.0, -120.0), (60_000.0, -130.0)]
        assert fresh.is_favourite("AC-DC - Thunder")
        assert "song_songsterr" in taken

    def test_what_this_machine_already_has_wins(self, tmp_path):
        """A sync that silently overwrites what you have just adjusted is
        worse than no sync -- and it is the rule merge_stats has always
        used, so the project has one rule rather than two."""
        tab = self._sent(tmp_path)
        mine = Config()
        mine.set_tempo_factor_for("AC-DC - Thunder", 0.5)
        sidecar.adopt(tab, "AC-DC - Thunder", mine)
        assert mine.tempo_factor_for("AC-DC - Thunder") == 0.5
        assert mine.songsterr_for("AC-DC - Thunder") == 2333598

    def test_the_recording_is_only_taken_if_it_is_really_there(self,
                                                               tmp_path):
        """Otherwise this stores a path to nothing, which the app reports as
        a moved recording."""
        tab = self._sent(tmp_path, mp3=r"C:\elsewhere\AC-DC - Thunder.mp3")
        fresh = Config()
        sidecar.adopt(tab, "AC-DC - Thunder", fresh)
        assert "AC-DC - Thunder" not in fresh.song_mp3_paths

    def test_and_it_is_resolved_here_when_it_is(self, tmp_path):
        (tmp_path / "AC-DC - Thunder.mp3").write_bytes(b"x")
        tab = self._sent(tmp_path, mp3=r"C:\elsewhere\AC-DC - Thunder.mp3")
        fresh = Config()
        sidecar.adopt(tab, "AC-DC - Thunder", fresh)
        assert fresh.mp3_path_for("AC-DC - Thunder") == str(
            tmp_path / "AC-DC - Thunder.mp3")

    def test_no_sidecar_is_nothing_and_not_a_raise(self, tmp_path):
        assert sidecar.read(tmp_path / "absent.gp5") is None
        assert sidecar.adopt(tmp_path / "absent.gp5", "x", Config()) == []

    def test_a_half_written_sidecar_is_ignored(self, tmp_path):
        tab = _song(tmp_path)
        sidecar.path_for(tab).write_text("{ not json", encoding="utf-8")
        assert sidecar.read(tab) is None

    def test_a_version_this_build_does_not_know_is_ignored(self, tmp_path):
        """Rather than guessed at."""
        tab = _song(tmp_path)
        sidecar.path_for(tab).write_text(
            json.dumps({"version": 99, "settings": {"song_transpose": 2}}),
            encoding="utf-8")
        assert sidecar.read(tab) is None

    def test_a_field_a_newer_build_knew_about_is_skipped(self, tmp_path):
        tab = _song(tmp_path)
        sidecar.path_for(tab).write_text(json.dumps(
            {"version": sidecar.VERSION, "song": "x",
             "settings": {"song_from_the_future": 1, "song_transpose": 2}}),
            encoding="utf-8")
        fresh = Config()
        assert sidecar.adopt(tab, "x", fresh) == ["song_transpose"]

    def test_a_folder_that_cannot_be_written_is_not_a_crash(self, tmp_path):
        assert sidecar.write(tmp_path / "no" / "such" / "s.gp5", "s",
                             Config()) is None


class TestThePracticeRecord:
    """*"Bestwerte pro Song mit ins Sidecar."*"""

    def _tracker(self, tmp_path, monkeypatch):
        from pickhero import progress
        monkeypatch.setattr(progress, "PROGRESS_FILE",
                            tmp_path / "progress.json")
        monkeypatch.setattr(progress, "CONFIG_DIR", tmp_path)
        return progress.ProgressTracker

    def test_the_best_score_travels(self, tmp_path, monkeypatch):
        Tracker = self._tracker(tmp_path, monkeypatch)
        tab = _song(tmp_path)
        played = Tracker()
        played.record_result("AC-DC - Thunder",
                             {"accuracy_percent": 91.0, "hits": 120,
                              "total": 130})
        sidecar.write(tab, "AC-DC - Thunder", _config(),
                      played.get_best("AC-DC - Thunder"))

        (tmp_path / "progress.json").unlink()        # the other laptop
        fresh = Tracker()
        assert sidecar.adopt_best(tab, "AC-DC - Thunder", fresh)
        assert fresh.get_best("AC-DC - Thunder").best_accuracy == 91.0

    def test_a_record_already_here_is_never_replaced(self, tmp_path,
                                                     monkeypatch):
        """A best score is one number with its own history behind it, and
        half of each is neither. merge_stats is still the tool for two
        machines that both played the song."""
        Tracker = self._tracker(tmp_path, monkeypatch)
        tab = _song(tmp_path)
        played = Tracker()
        played.record_result("AC-DC - Thunder",
                             {"accuracy_percent": 91.0, "hits": 1,
                              "total": 2})
        sidecar.write(tab, "AC-DC - Thunder", _config(),
                      played.get_best("AC-DC - Thunder"))

        mine = Tracker()
        mine.record_result("AC-DC - Thunder",
                           {"accuracy_percent": 40.0, "hits": 1, "total": 2})
        assert not sidecar.adopt_best(tab, "AC-DC - Thunder", mine)
        assert mine.get_best("AC-DC - Thunder").best_accuracy == 91.0

    def test_a_sidecar_with_no_record_takes_nothing(self, tmp_path,
                                                    monkeypatch):
        Tracker = self._tracker(tmp_path, monkeypatch)
        tab = _song(tmp_path)
        sidecar.write(tab, "AC-DC - Thunder", _config())
        assert not sidecar.adopt_best(tab, "AC-DC - Thunder", Tracker())


class TestItMovesWithTheSong:

    def test_rename_carries_it_and_rewrites_the_name_inside(self, tmp_path):
        from pickhero.tabs import remove
        tab = _song(tmp_path)
        config = _config()
        sidecar.write(tab, "AC-DC - Thunder", config)
        remove.rename_song(tab, "Thunder", config)
        moved = sidecar.read(tmp_path / "Thunder.gp5")
        assert moved is not None and moved["song"] == "Thunder"
        assert not sidecar.path_for(tab).exists()

    def test_delete_takes_it_with_the_song(self, tmp_path):
        from pickhero.tabs import remove
        tab = _song(tmp_path)
        sidecar.write(tab, "AC-DC - Thunder", _config())
        remove.delete_song(tab, _config())
        assert not sidecar.path_for(tab).exists()

    def test_the_song_list_picks_it_up_when_it_scans(self, tmp_path):
        """A star has to show in the LIST -- "my favourites are gone" is
        what copying the folder used to look like."""
        from pickhero.ui.menu import MenuScreen
        tab = _song(tmp_path)
        sidecar.write(tab, "AC-DC - Thunder", _config())
        fresh = Config()
        fresh.save = lambda: None
        screen = MenuScreen(tmp_path, config=fresh)
        assert fresh.is_favourite("AC-DC - Thunder")
        assert fresh.songsterr_for("AC-DC - Thunder") == 2333598
        assert "picked up settings for 1 song" in screen._reload_note

    def test_a_song_that_arrives_while_the_app_is_open_is_picked_up_by_f5(
            self, tmp_path):
        """Which is what a cloud folder does: the file lands while the app
        is running. `reload_files` owns its own line, so the count is folded
        into it rather than set behind its back."""
        from pickhero.ui.menu import MenuScreen
        _song(tmp_path, "already here")
        config = Config()
        config.save = lambda: None
        screen = MenuScreen(tmp_path, config=config)
        assert screen._adopted == 0

        arrived = _song(tmp_path)
        sidecar.write(arrived, "AC-DC - Thunder", _config())
        said = screen.reload_files()

        assert "1 new" in said and "1 picked up settings" in said
        assert config.is_favourite("AC-DC - Thunder")

    def test_and_a_scan_that_takes_nothing_says_nothing_about_it(self,
                                                                 tmp_path):
        """Stale news is what a second writer of the note produces."""
        from pickhero.ui.menu import MenuScreen
        tab = _song(tmp_path)
        sidecar.write(tab, "AC-DC - Thunder", _config())
        config = Config()
        config.save = lambda: None
        screen = MenuScreen(tmp_path, config=config)
        assert "picked up" not in screen.reload_files()

    def test_one_unreadable_sidecar_does_not_break_the_list(self, tmp_path):
        from pickhero.ui.menu import MenuScreen
        _song(tmp_path, "good")
        bad = _song(tmp_path, "bad")
        sidecar.path_for(bad).write_text("nonsense", encoding="utf-8")
        config = Config()
        config.save = lambda: None
        screen = MenuScreen(tmp_path, config=config)
        screen.reload_files()
        assert len(screen._display_files) == 2


class TestGivingEverySongItsFile:
    """*"Ab wann hat jeder Song 4 Files? Einmalig beim Öffnen der App wäre
    praktischer."*

    Right: the sidecar was written on the way OUT of a song, so "every song
    has four files" came true one song at a time, in whatever order they
    happened to be played -- and the player would have had to visit every
    one before copying anything anywhere.
    """

    def _list(self, tmp_path, config):
        from pickhero.ui.menu import MenuScreen
        return MenuScreen(tmp_path, config=config)

    def test_a_song_with_settings_gets_one_on_the_first_scan(self, tmp_path):
        tab = _song(tmp_path)
        config = _config()
        assert not sidecar.path_for(tab).exists()
        screen = self._list(tmp_path, config)
        assert sidecar.path_for(tab).is_file()
        assert screen._backfilled == 1
        assert "wrote settings beside 1 song" in screen._reload_note

    def test_what_it_wrote_is_what_the_song_carries(self, tmp_path):
        tab = _song(tmp_path)
        self._list(tmp_path, _config())
        got = sidecar.read(tab)["settings"]
        assert got["song_songsterr"] == 2333598
        assert got["song_mp3_anchors"] == [[0.0, -120.0], [60_000.0, -130.0]]

    def test_a_song_nobody_has_touched_gets_nothing(self, tmp_path):
        """An empty sidecar would be clutter that also lies -- a file saying
        "settings live here" when they do not."""
        tab = _song(tmp_path, "Never played")
        config = Config()
        config.save = lambda: None
        screen = self._list(tmp_path, config)
        assert not sidecar.path_for(tab).exists()
        assert screen._backfilled == 0

    def test_an_existing_sidecar_is_left_alone(self, tmp_path):
        """It may have come from the other machine and be newer than
        anything here. Overwriting it on a scan would undo an import."""
        tab = _song(tmp_path)
        sidecar.path_for(tab).write_text(json.dumps(
            {"version": sidecar.VERSION, "song": "AC-DC - Thunder",
             "settings": {"song_transpose": 7}}), encoding="utf-8")
        self._list(tmp_path, _config())
        assert sidecar.read(tab)["settings"]["song_transpose"] == 7

    def test_a_second_start_writes_nothing_more(self, tmp_path):
        _song(tmp_path)
        config = _config()
        self._list(tmp_path, config)
        again = self._list(tmp_path, config)
        assert again._backfilled == 0

    def test_the_favourite_alone_is_worth_a_file(self, tmp_path):
        tab = _song(tmp_path, "Starred")
        config = Config()
        config.save = lambda: None
        config.set_favourite("Starred", True)
        self._list(tmp_path, config)
        assert sidecar.read(tab)["favourite"]

    def test_and_so_is_a_best_score_on_its_own(self, tmp_path, monkeypatch):
        from pickhero import progress
        monkeypatch.setattr(progress, "PROGRESS_FILE",
                            tmp_path / "progress.json")
        monkeypatch.setattr(progress, "CONFIG_DIR", tmp_path)
        tab = _song(tmp_path, "Only played")
        progress.ProgressTracker().record_result(
            "Only played", {"accuracy_percent": 77.0, "hits": 1, "total": 2})
        config = Config()
        config.save = lambda: None
        self._list(tmp_path, config)
        assert sidecar.read(tab)["best"]["best_accuracy"] == 77.0

    def test_one_song_that_cannot_be_written_does_not_stop_the_rest(
            self, tmp_path, monkeypatch):
        _song(tmp_path, "A song")
        _song(tmp_path, "B song")
        real = sidecar.write
        calls = []

        def sometimes(tab_path, song_key, config, best=None):
            calls.append(song_key)
            if song_key == "A song":
                raise OSError("locked")
            return real(tab_path, song_key, config, best)

        monkeypatch.setattr(sidecar, "write", sometimes)
        config = Config()
        config.save = lambda: None
        config.set_favourite("A song", True)
        config.set_favourite("B song", True)
        screen = self._list(tmp_path, config)
        assert screen._backfilled == 1
        assert sidecar.path_for(tmp_path / "B song.gp5").is_file()
