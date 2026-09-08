"""How many instruments a song holds, how each is tuned, and filtering by it.

Both answers need the file unpacked, which is far too slow to do for a whole
folder while the player waits for a list. So what these pin down is mostly
the caching and the not-blocking: a wrong answer must never be shown while
the right one is still being read.
"""

import json
from pathlib import Path

import pygame
import pytest

from pickhero.config import Config
from pickhero.tabs import song_index
from pickhero.tabs.song_index import SongIndex, SongInfo, describe_tuning
from pickhero.ui.menu import MenuScreen

SONG = Path(__file__).resolve().parent.parent / "songs" / "timing_test_100bpm.gp5"

STANDARD = {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40}
DROP_D = {**STANDARD, 6: 38}
EB = {s: v - 1 for s, v in STANDARD.items()}


class TestNamingATuning:
    def test_it_reads_low_string_first_the_way_a_player_says_it(self):
        assert describe_tuning(STANDARD) == "E A D G B E"

    def test_drop_d_shows_the_dropped_string(self):
        assert describe_tuning(DROP_D) == "D A D G B E"

    def test_a_half_step_down_is_spelled_out(self):
        assert describe_tuning(EB) == "D# G# C# F# A# D#"

    def test_no_tuning_is_no_text_rather_than_a_guess(self):
        assert describe_tuning(None) == "" and describe_tuning({}) == ""


class TestTheLineTheListHasRoomFor:
    def test_one_track_is_singular(self):
        info = SongInfo(tracks=1, tunings=["E A D G B E"])
        assert info.summary() == "1 track · E A D G B E"

    def test_the_same_tuning_six_times_is_said_once(self):
        """Six guitars in standard tuning is one answer, not six."""
        info = SongInfo(tracks=6, tunings=["E A D G B E"] * 6)
        assert info.summary() == "6 tracks · E A D G B E"

    def test_two_tunings_are_both_named(self):
        info = SongInfo(tracks=2, tunings=["E A D G B E", "D A D G B E"])
        assert info.summary() == "2 tracks · E A D G B E, D A D G B E"

    def test_more_than_two_are_counted_rather_than_listed(self):
        info = SongInfo(tracks=4, tunings=["E A D G B E", "D A D G B E",
                                           "C G C F A D", "D# G# C# F# A# D#"])
        assert info.summary().endswith("+2")

    def test_a_file_that_could_not_be_read_says_that(self):
        assert SongInfo(readable=False).summary() == "could not be read"

    def test_a_file_with_no_guitar_in_it_says_that_instead(self):
        """A blank row means "not read yet". These two must never look the
        same, or every unindexed song looks like a piano piece."""
        assert SongInfo(tracks=0).summary() == "no guitar track"


class TestReadingARealFile:
    pytestmark = pytest.mark.skipif(not SONG.exists(), reason="song missing")

    def test_it_finds_the_playable_track_and_its_tuning(self, tmp_path):
        index = SongIndex(tmp_path / "i.json")
        index.scan([SONG])
        info = index.get(SONG)
        assert info.tracks == 1
        assert info.tunings == ["E A D G B E"]

    def test_the_click_track_is_not_described_as_an_instrument(self, tmp_path):
        """A drum track's "tuning" is not a tuning, and saying so is noise."""
        index = SongIndex(tmp_path / "i.json")
        index.scan([SONG])
        assert index.get(SONG).tracks == 1


class TestOnlyGuitars:
    def test_a_bass_or_a_piano_is_not_counted_as_an_instrument_to_play(self,
                                                                       tmp_path,
                                                                       monkeypatch):
        """Counting them makes the number answer a different question."""
        from pickhero.tabs import loader
        monkeypatch.setattr(loader, "list_tracks", lambda p: [
            {"index": 0, "name": "Guitar", "is_guitar": True,
             "is_percussion": False, "tuning": STANDARD},
            {"index": 1, "name": "Bass", "is_guitar": False,
             "is_percussion": False, "tuning": {1: 43, 2: 38, 3: 33, 4: 28}},
            {"index": 2, "name": "Drums", "is_guitar": False,
             "is_percussion": True, "tuning": {}},
        ])
        info = song_index.describe_file(tmp_path / "x.gp5")
        assert info.tracks == 1 and info.tunings == ["E A D G B E"]

    def test_a_file_with_no_guitar_at_all_is_readable_and_empty(self, tmp_path,
                                                                monkeypatch):
        from pickhero.tabs import loader
        monkeypatch.setattr(loader, "list_tracks", lambda p: [
            {"index": 0, "name": "Piano", "is_guitar": False,
             "is_percussion": False, "tuning": {}}])
        info = song_index.describe_file(tmp_path / "x.gp5")
        assert info.tracks == 0 and info.readable is True
        assert info.summary() == "no guitar track"

    def test_an_index_from_before_this_is_read_again(self, tmp_path):
        """Its number counted more than guitars, so it answers a different
        question and must not be believed."""
        song = tmp_path / "a.gp5"
        song.write_bytes(b"x")
        old = {str(song): {"stamp": song_index.file_stamp(song),
                           "tracks": 5, "tunings": ["E A D G B E"],
                           "names": []}}
        (tmp_path / "i.json").write_text(json.dumps(old))
        assert SongIndex(tmp_path / "i.json").get(song) is None


class TestNotReadingItTwice:
    def _index(self, tmp_path, monkeypatch, calls):
        monkeypatch.setattr(song_index, "describe_file",
                            lambda p: calls.append(p) or SongInfo(
                                tracks=1, tunings=["E A D G B E"]))
        return SongIndex(tmp_path / "i.json")

    def _file(self, tmp_path, name="a.gp5"):
        f = tmp_path / name
        f.write_bytes(b"x")
        return f

    def test_a_file_already_known_is_not_opened_again(self, tmp_path,
                                                      monkeypatch):
        calls = []
        index = self._index(tmp_path, monkeypatch, calls)
        song = self._file(tmp_path)
        index.scan([song])
        index.scan([song])
        assert len(calls) == 1

    def test_a_new_session_reads_the_file_from_disk_not_the_song(self, tmp_path,
                                                                 monkeypatch):
        calls = []
        index = self._index(tmp_path, monkeypatch, calls)
        song = self._file(tmp_path)
        index.scan([song])
        again = self._index(tmp_path, monkeypatch, calls)
        again.scan([song])
        assert len(calls) == 1
        assert again.get(song).tunings == ["E A D G B E"]

    def test_a_changed_file_is_read_afresh_rather_than_believed(self, tmp_path,
                                                                monkeypatch):
        calls = []
        index = self._index(tmp_path, monkeypatch, calls)
        song = self._file(tmp_path)
        index.scan([song])
        import os, time
        song.write_bytes(b"xxxxxx")
        os.utime(song, (time.time() + 10, time.time() + 10))
        assert index.get(song) is None
        index.scan([song])
        assert len(calls) == 2

    def test_a_damaged_index_costs_a_rescan_not_a_crash(self, tmp_path,
                                                        monkeypatch):
        (tmp_path / "i.json").write_text("{ not json")
        calls = []
        index = self._index(tmp_path, monkeypatch, calls)
        song = self._file(tmp_path)
        index.scan([song])
        assert index.get(song) is not None


class TestWhichTuningsArePresent:
    def _stocked(self, tmp_path, mapping):
        index = SongIndex(tmp_path / "i.json")
        files = []
        for name, tunings in mapping.items():
            f = tmp_path / f"{name}.gp5"
            f.write_bytes(b"x")
            index._record(f, SongInfo(tracks=len(tunings), tunings=tunings))
            files.append(f)
        return index, files

    def test_the_commonest_comes_first(self):
        """The filter exists to reach what is actually played; standard tuning
        must not sit between two things nobody has."""
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        index, files = self._stocked(tmp, {
            "a": ["E A D G B E"], "b": ["E A D G B E"], "c": ["D A D G B E"]})
        assert index.tunings_present(files)[0] == "E A D G B E"

    def test_a_song_with_two_tunings_counts_for_both(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        index, files = self._stocked(tmp, {
            "a": ["E A D G B E", "D A D G B E"]})
        assert set(index.tunings_present(files)) == {"E A D G B E", "D A D G B E"}

    def test_songs_not_read_yet_contribute_nothing(self, tmp_path):
        index = SongIndex(tmp_path / "i.json")
        unknown = tmp_path / "x.gp5"
        unknown.write_bytes(b"x")
        assert index.tunings_present([unknown]) == []


def _key(key):
    return pygame.event.Event(pygame.KEYDOWN, key=key, unicode="", mod=0)


class TestFilteringTheList:
    @pytest.fixture
    def menu(self, tmp_path, monkeypatch):
        monkeypatch.setattr(song_index, "index_file",
                            lambda: tmp_path / "index.json")
        # These are not real GP files, so the background scan would record
        # every one of them as unreadable and undo the entries below. The
        # filtering is what is under test here, not the reading.
        monkeypatch.setattr(SongIndex, "scan_in_background",
                            lambda self, files: None)
        songs = tmp_path / "songs"
        songs.mkdir()
        for name in ("standard_a", "standard_b", "dropd"):
            (songs / f"{name}.gp5").write_bytes(b"x")
        screen = MenuScreen(songs, config=Config())
        screen._index._record(songs / "standard_a.gp5",
                              SongInfo(1, ["E A D G B E"]))
        screen._index._record(songs / "standard_b.gp5",
                              SongInfo(1, ["E A D G B E"]))
        screen._index._record(songs / "dropd.gp5",
                              SongInfo(2, ["D A D G B E", "E A D G B E"]))
        screen._apply_filter()
        return screen

    def test_tab_steps_to_the_commonest_tuning(self, menu):
        menu.handle_event(_key(pygame.K_TAB))
        assert menu._tuning_filter == "E A D G B E"
        assert len(menu._display_files) == 3     # dropd has a standard track too

    def test_stepping_again_reaches_the_next_one(self, menu):
        menu.handle_event(_key(pygame.K_TAB))
        menu.handle_event(_key(pygame.K_TAB))
        assert menu._tuning_filter == "D A D G B E"
        assert [p.stem for p in menu._display_files] == ["dropd"]

    def test_it_comes_back_round_to_all_of_them(self, menu):
        for _ in range(3):
            menu.handle_event(_key(pygame.K_TAB))
        assert menu._tuning_filter == ""
        assert len(menu._display_files) == 3

    def test_the_search_and_the_tuning_filter_both_apply(self, menu):
        menu.handle_event(_key(pygame.K_f))
        for ch in "standard":
            menu.handle_event(pygame.event.Event(
                pygame.KEYDOWN, key=ord(ch), unicode=ch, mod=0))
        menu.handle_event(_key(pygame.K_TAB))
        assert menu._tuning_filter == "E A D G B E"
        assert {p.stem for p in menu._display_files} == {"standard_a", "standard_b"}

    def test_a_song_not_indexed_yet_is_not_shown_as_a_match(self, menu, tmp_path):
        """While the filter is on, "no answer yet" would read as "yes"."""
        (tmp_path / "songs" / "unknown.gp5").write_bytes(b"x")
        menu.reload_files()
        menu.handle_event(_key(pygame.K_TAB))
        assert "unknown" not in {p.stem for p in menu._display_files}

    def test_the_cursor_stays_on_its_song_through_a_filter_change(self, menu):
        menu._selected = [p.stem for p in menu._display_files].index("dropd")
        menu.handle_event(_key(pygame.K_TAB))
        assert menu._display_files[menu._selected].stem == "dropd"

    def test_with_nothing_indexed_it_says_so_rather_than_emptying_the_list(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(song_index, "index_file",
                            lambda: tmp_path / "i2.json")
        monkeypatch.setattr(SongIndex, "scan_in_background",
                            lambda self, files: None)
        songs = tmp_path / "s2"
        songs.mkdir()
        (songs / "a.gp5").write_bytes(b"x")
        screen = MenuScreen(songs, config=Config())
        screen.handle_event(_key(pygame.K_TAB))
        assert screen._tuning_filter == ""
        assert screen._reload_note
        assert len(screen._display_files) == 1
