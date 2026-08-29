"""Bringing another computer's practice history in.

The property that matters is that running it twice changes nothing the
second time. Nobody remembers whether they already merged, and a history
that has doubled is worse than one that is missing -- it cannot be told
apart from having practised twice as much.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import merge_stats  # noqa: E402

from pickhero import practice_log  # noqa: E402
from pickhero.practice_log import Session  # noqa: E402


def _session(started, song="solo", seconds=600.0, strikes=100, **kw):
    return Session(started=started, song=song, seconds=seconds,
                   strikes=strikes, tempo_percent=100, **kw)


def _folder(tmp_path, name, sessions=(), progress=None, settings=None):
    folder = tmp_path / name
    folder.mkdir()
    if settings is not None:
        (folder / "settings.json").write_text(json.dumps(settings))
    for session in sessions:
        practice_log.append(session, folder / "practice_log.jsonl")
    if progress is not None:
        (folder / "progress.json").write_text(json.dumps(progress))
    return folder


class TestMergingSittings:
    def test_the_other_machine_s_sittings_arrive(self):
        mine = [_session("2026-08-24T10:00:00")]
        theirs = [_session("2026-08-25T10:00:00", song="other")]
        merged, added = merge_stats.merge_sessions(mine, theirs)
        assert added == 1 and len(merged) == 2

    def test_the_same_sitting_arrives_only_once(self):
        one = _session("2026-08-24T10:00:00")
        merged, added = merge_stats.merge_sessions([one], [one])
        assert added == 0 and len(merged) == 1

    def test_two_songs_at_the_same_moment_are_two_sittings(self):
        """The song is part of what makes a sitting itself."""
        a = _session("2026-08-24T10:00:00", song="a")
        b = _session("2026-08-24T10:00:00", song="b")
        merged, added = merge_stats.merge_sessions([a], [b])
        assert added == 1 and len(merged) == 2

    def test_the_result_is_in_order_however_it_arrived(self):
        mine = [_session("2026-08-24T10:00:00")]
        theirs = [_session("2026-08-01T10:00:00", song="early")]
        merged, _ = merge_stats.merge_sessions(mine, theirs)
        assert [s.started for s in merged] == sorted(s.started for s in merged)


class TestMergingTheHighScores:
    def _record(self, accuracy, hits=10, total=20, attempts=1, played="2026-08-01"):
        return {"best_accuracy": accuracy, "best_hits": hits,
                "best_total": total, "attempts": attempts,
                "last_played": played, "section_history": [],
                "tempo_history": [{"attempt": 1, "accuracy": accuracy}]}

    def test_a_better_run_from_the_other_machine_wins(self):
        mine = {"song": self._record(60.0)}
        theirs = {"song": self._record(90.0, hits=18)}
        merged, improved = merge_stats.merge_progress(mine, theirs)
        assert merged["song"]["best_accuracy"] == 90.0
        assert merged["song"]["best_hits"] == 18
        assert improved == ["song"]

    def test_a_worse_run_does_not(self):
        mine = {"song": self._record(90.0, hits=18)}
        merged, improved = merge_stats.merge_progress(
            mine, {"song": self._record(60.0, hits=12)})
        assert merged["song"]["best_hits"] == 18 and improved == []

    def test_the_winning_record_is_taken_whole(self):
        """Mixing the hits of one run with the accuracy of another describes a
        run that never happened."""
        mine = {"song": self._record(60.0, hits=6, total=10)}
        theirs = {"song": self._record(90.0, hits=45, total=50)}
        merged, _ = merge_stats.merge_progress(mine, theirs)
        best = merged["song"]
        assert (best["best_hits"], best["best_total"]) == (45, 50)
        assert best["tempo_history"] == theirs["song"]["tempo_history"]

    def test_a_song_only_played_over_there_comes_across(self):
        merged, improved = merge_stats.merge_progress({}, {"new": self._record(50.0)})
        assert "new" in merged and improved == ["new"]

    def test_attempts_are_not_summed(self):
        """A sum cannot be done twice safely, and the honest count of sittings
        is in the practice log."""
        mine = {"song": self._record(90.0, attempts=30)}
        theirs = {"song": self._record(50.0, attempts=4)}
        merged, _ = merge_stats.merge_progress(mine, theirs)
        assert merged["song"]["attempts"] == 30

    def test_the_later_date_is_kept(self):
        mine = {"song": self._record(90.0, played="2026-08-01")}
        theirs = {"song": self._record(50.0, played="2026-08-25")}
        merged, _ = merge_stats.merge_progress(mine, theirs)
        assert merged["song"]["last_played"] == "2026-08-25"


class TestDoingItTwice:
    """The whole design constraint."""

    def _run(self, tmp_path, monkeypatch, source, target, extra=()):
        argv = ["x", "--from", str(source), "--into", str(target), *extra]
        monkeypatch.setattr(sys, "argv", argv)
        return merge_stats.main()

    def test_a_second_merge_adds_nothing(self, tmp_path, monkeypatch, capsys):
        source = _folder(tmp_path, "stick",
                         [_session("2026-08-25T10:00:00", song="b")],
                         {"b": {"best_accuracy": 80.0, "attempts": 2}})
        target = _folder(tmp_path, "home",
                         [_session("2026-08-24T10:00:00")],
                         {"a": {"best_accuracy": 50.0, "attempts": 1}})
        assert self._run(tmp_path, monkeypatch, source, target) == 0
        after_first = (target / "practice_log.jsonl").read_text()
        first_progress = (target / "progress.json").read_text()
        capsys.readouterr()
        assert self._run(tmp_path, monkeypatch, source, target) == 0
        assert (target / "practice_log.jsonl").read_text() == after_first
        assert (target / "progress.json").read_text() == first_progress
        assert "Nichts Neues" in capsys.readouterr().out

    def test_the_sittings_from_both_are_there_afterwards(self, tmp_path,
                                                         monkeypatch):
        source = _folder(tmp_path, "stick", [_session("2026-08-25T10:00:00",
                                                      song="b")])
        target = _folder(tmp_path, "home", [_session("2026-08-24T10:00:00")])
        self._run(tmp_path, monkeypatch, source, target)
        songs = {s.song for s in practice_log.read(target / "practice_log.jsonl")}
        assert songs == {"solo", "b"}

    def test_a_backup_is_left_behind(self, tmp_path, monkeypatch):
        source = _folder(tmp_path, "stick", [_session("2026-08-25T10:00:00",
                                                      song="b")])
        target = _folder(tmp_path, "home", [_session("2026-08-24T10:00:00")])
        self._run(tmp_path, monkeypatch, source, target)
        assert (target / "practice_log.jsonl.bak").exists()

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        source = _folder(tmp_path, "stick", [_session("2026-08-25T10:00:00",
                                                      song="b")])
        target = _folder(tmp_path, "home", [_session("2026-08-24T10:00:00")])
        before = (target / "practice_log.jsonl").read_text()
        self._run(tmp_path, monkeypatch, source, target, ["--dry-run"])
        assert (target / "practice_log.jsonl").read_text() == before

    def test_a_missing_source_says_where_to_look(self, tmp_path, monkeypatch,
                                                 capsys):
        target = _folder(tmp_path, "home")
        assert self._run(tmp_path, monkeypatch, tmp_path / "nope", target) == 1
        assert ".pickhero" in capsys.readouterr().out

    def test_merging_a_folder_into_itself_is_refused(self, tmp_path, monkeypatch,
                                                     capsys):
        """It would work, but only by accident, and it always means a mistake."""
        target = _folder(tmp_path, "home", [_session("2026-08-24T10:00:00")])
        assert self._run(tmp_path, monkeypatch, target, target) == 1
        assert "derselbe Ordner" in capsys.readouterr().out


class TestTheSettingsThatBelongToTheSONG:
    """The per-song settings live in settings.json, which this did not touch
    at all -- so the practice speed, the backing track and both offsets were
    left behind on the other machine."""

    def test_a_song_only_set_up_over_there_comes_across(self):
        merged, changed = merge_stats.merge_settings(
            {"song_tempo_factors": {"a": 0.8}},
            {"song_tempo_factors": {"b": 0.7}})
        assert merged["song_tempo_factors"] == {"a": 0.8, "b": 0.7}
        assert changed

    def test_what_this_machine_already_has_wins(self):
        """It was set HERE, on this instrument, in this room. A sync that
        silently overwrites what you just adjusted is worse than no sync."""
        merged, _ = merge_stats.merge_settings(
            {"song_tempo_factors": {"a": 0.8}},
            {"song_tempo_factors": {"a": 0.5}})
        assert merged["song_tempo_factors"]["a"] == 0.8

    def test_all_four_per_song_settings_travel(self):
        theirs = {f: {"song": "x"} for f in merge_stats.SONG_SETTINGS}
        merged, _ = merge_stats.merge_settings({}, theirs)
        for field in merge_stats.SONG_SETTINGS:
            assert merged[field] == {"song": "x"}, field

    def test_favourites_are_added_never_removed(self):
        merged, _ = merge_stats.merge_settings(
            {"favourites": ["a", "b"]}, {"favourites": ["b", "c"]})
        assert merged["favourites"] == ["a", "b", "c"]

    def test_what_belongs_to_the_MACHINE_stays_put(self):
        """The device index, the calibration and the latency offset describe
        an interface and a sound card. Carrying them over would break the
        other computer's input while looking like a settings problem."""
        mine = {"audio": {"device_index": 7}, "calibration": {"1": 82.4},
                "audio_latency_offset_ms": -220.0, "theme": "dark"}
        merged, _ = merge_stats.merge_settings(mine, {
            "audio": {"device_index": 3}, "calibration": {"1": 99.9},
            "audio_latency_offset_ms": 0.0, "theme": "light"})
        assert merged["audio"]["device_index"] == 7
        assert merged["calibration"] == {"1": 82.4}
        assert merged["audio_latency_offset_ms"] == -220.0
        assert merged["theme"] == "dark"

    def test_nothing_new_reports_nothing(self):
        merged, changed = merge_stats.merge_settings(
            {"favourites": ["a"]}, {"favourites": ["a"]})
        assert changed == []

    def test_a_second_merge_changes_nothing(self, tmp_path, monkeypatch, capsys):
        source = _folder(tmp_path, "stick",
                         settings={"song_tempo_factors": {"b": 0.7},
                                   "favourites": ["b"]})
        target = _folder(tmp_path, "home",
                         settings={"song_tempo_factors": {"a": 0.8},
                                   "favourites": ["a"], "theme": "dark"})
        argv = ["x", "--from", str(source), "--into", str(target)]
        monkeypatch.setattr(sys, "argv", argv)
        assert merge_stats.main() == 0
        after = (target / "settings.json").read_text()
        capsys.readouterr()
        assert merge_stats.main() == 0
        assert (target / "settings.json").read_text() == after
        assert "Nichts Neues" in capsys.readouterr().out

    def test_the_settings_really_land_on_disk(self, tmp_path, monkeypatch):
        source = _folder(tmp_path, "stick",
                         settings={"song_mp3_offsets": {"solo": -1200.0}})
        target = _folder(tmp_path, "home", settings={"theme": "dark"})
        monkeypatch.setattr(sys, "argv",
                            ["x", "--from", str(source), "--into", str(target)])
        merge_stats.main()
        got = json.loads((target / "settings.json").read_text())
        assert got["song_mp3_offsets"]["solo"] == -1200.0
        assert got["theme"] == "dark"
