"""The practice dashboard: what the page is handed, and what it is not.

Everything is added up in Python and the browser only draws, so what can be
tested here is the whole of the arithmetic. The drawing was checked by opening
the generated file in a real browser and looking at it, which is the only way
to check a drawing.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import make_dashboard  # noqa: E402

from pickhero.practice_log import Session  # noqa: E402


def _session(**kwargs):
    base = dict(started="2026-08-24T19:30:00", song="solo", seconds=600.0,
                strikes=420, tempo_percent=80)
    base.update(kwargs)
    return Session(**base)


def _today(offset=0):
    return (date.today() - timedelta(days=offset)).isoformat()


class TestWhatThePageIsHanded:
    def test_days_are_added_up_across_sittings(self):
        data = make_dashboard.build_data([
            _session(started="2026-08-24T10:00:00", seconds=600, strikes=100),
            _session(started="2026-08-24T20:00:00", seconds=300, strikes=50),
        ])
        assert len(data["days"]) == 1
        assert data["days"][0]["seconds"] == 900
        assert data["days"][0]["strikes"] == 150
        assert data["days"][0]["sessions"] == 2

    def test_days_come_out_oldest_first(self):
        data = make_dashboard.build_data([
            _session(started="2026-08-24T10:00:00"),
            _session(started="2026-08-01T10:00:00"),
        ])
        assert [d["day"] for d in data["days"]] == ["2026-08-01", "2026-08-24"]

    def test_the_totals_are_the_headline(self):
        data = make_dashboard.build_data([
            _session(seconds=3600, strikes=1000, accuracy=91.0),
            _session(started="2026-08-23T19:00:00", seconds=1800, strikes=500,
                     song="other"),
        ])
        totals = data["totals"]
        assert totals["hours"] == 1.5
        assert totals["strikes"] == 1500
        assert totals["songs"] == 2
        assert totals["best_accuracy"] == 91.0

    def test_no_scored_run_is_none_not_zero(self):
        """A dashboard would draw 0 % as a bad day rather than as no data."""
        data = make_dashboard.build_data([_session(accuracy=None)])
        assert data["totals"]["best_accuracy"] is None


class TestTheStreak:
    def test_days_in_a_row_up_to_today(self):
        days = [_today(2), _today(1), _today(0)]
        assert make_dashboard.current_streak(days) == 3

    def test_a_gap_ends_it(self):
        days = [_today(5), _today(4), _today(1), _today(0)]
        assert make_dashboard.current_streak(days) == 2

    def test_yesterday_still_counts_because_today_is_young(self):
        """Opening the dashboard before practising must not read as a break."""
        assert make_dashboard.current_streak([_today(2), _today(1)]) == 2

    def test_the_day_before_yesterday_does_not(self):
        assert make_dashboard.current_streak([_today(3), _today(2)]) == 0

    def test_nothing_practised_is_no_streak(self):
        assert make_dashboard.current_streak([]) == 0


class TestTheFileItWrites:
    def _log(self, tmp_path, sessions):
        path = tmp_path / "log.jsonl"
        from pickhero import practice_log
        for session in sessions:
            practice_log.append(session, path)
        return path

    def test_it_is_one_file_that_needs_nothing_else(self, tmp_path, monkeypatch):
        """An offline-first practice app whose dashboard needs the internet to
        draw a bar chart is a contradiction."""
        log = self._log(tmp_path, [_session()])
        out = tmp_path / "dash.html"
        monkeypatch.setattr(sys, "argv",
                            ["x", "--file", str(log), "--out", str(out)])
        assert make_dashboard.main() == 0
        html = out.read_text(encoding="utf-8")
        assert "<script src=" not in html          # no CDN, no second file
        assert "http://" not in html and "https://" not in html

    def test_the_sessions_travel_with_it(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, [_session(song="metallica_one_solo")])
        out = tmp_path / "dash.html"
        monkeypatch.setattr(sys, "argv",
                            ["x", "--file", str(log), "--out", str(out)])
        make_dashboard.main()
        html = out.read_text(encoding="utf-8")
        payload = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
        assert json.loads(payload)["sessions"][0]["song"] == "metallica_one_solo"

    def test_nothing_recorded_yet_says_so_rather_than_writing_a_blank_page(
            self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv",
                            ["x", "--file", str(tmp_path / "none.jsonl"),
                             "--out", str(tmp_path / "dash.html")])
        assert make_dashboard.main() == 1
        assert "Noch nichts aufgezeichnet" in capsys.readouterr().out
        assert not (tmp_path / "dash.html").exists()

    def test_a_song_name_cannot_break_out_of_the_data(self, tmp_path, monkeypatch):
        """The name comes from a file on disk and lands inside a <script>."""
        log = self._log(tmp_path, [_session(song='</script><b>x')])
        out = tmp_path / "dash.html"
        monkeypatch.setattr(sys, "argv",
                            ["x", "--file", str(log), "--out", str(out)])
        make_dashboard.main()
        assert "</script><b>" not in out.read_text(encoding="utf-8")
