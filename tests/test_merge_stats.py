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


def _folder(tmp_path, name, sessions=(), progress=None):
    folder = tmp_path / name
    folder.mkdir()
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
