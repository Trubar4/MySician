"""The practice dashboard: what the page is handed, and what it is not.

Everything is added up in Python and the browser only draws, so most of what
can be tested here is the arithmetic. The exception is the week view, whose
sums are done in the browser -- that one is run through node where node is
there, because a week showing the wrong minutes is worse than no week.
"""

import json
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import make_dashboard  # noqa: E402

from pickhero import dashboard  # noqa: E402
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
        data = dashboard.build_data([
            _session(started="2026-08-24T10:00:00", seconds=600, strikes=100),
            _session(started="2026-08-24T20:00:00", seconds=300, strikes=50),
        ])
        assert len(data["days"]) == 1
        assert data["days"][0]["seconds"] == 900
        assert data["days"][0]["strikes"] == 150
        assert data["days"][0]["sessions"] == 2

    def test_days_come_out_oldest_first(self):
        data = dashboard.build_data([
            _session(started="2026-08-24T10:00:00"),
            _session(started="2026-08-01T10:00:00"),
        ])
        assert [d["day"] for d in data["days"]] == ["2026-08-01", "2026-08-24"]

    def test_the_totals_are_the_headline(self):
        data = dashboard.build_data([
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
        data = dashboard.build_data([_session(accuracy=None)])
        assert data["totals"]["best_accuracy"] is None


class TestTheStreak:
    def test_days_in_a_row_up_to_today(self):
        days = [_today(2), _today(1), _today(0)]
        assert dashboard.current_streak(days) == 3

    def test_a_gap_ends_it(self):
        days = [_today(5), _today(4), _today(1), _today(0)]
        assert dashboard.current_streak(days) == 2

    def test_yesterday_still_counts_because_today_is_young(self):
        """Opening the dashboard before practising must not read as a break."""
        assert dashboard.current_streak([_today(2), _today(1)]) == 2

    def test_the_day_before_yesterday_does_not(self):
        assert dashboard.current_streak([_today(3), _today(2)]) == 0

    def test_nothing_practised_is_no_streak(self):
        assert dashboard.current_streak([]) == 0


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


class TestWhereTheBuilderLives:
    """`pickhero.spec` bundles the package and nothing else, so a builder in
    `tools/` is simply absent on the machine that runs the EXE -- which is the
    machine whose dashboard most needs to keep itself up to date."""

    def test_the_page_is_built_by_the_package(self):
        assert Path(dashboard.__file__).parent.name == "pickhero"

    def test_the_package_needs_nothing_from_tools(self):
        source = Path(dashboard.__file__).read_text(encoding="utf-8")
        assert "sys.path" not in source
        assert "tools" not in source.split('"""', 2)[2]

    def test_the_command_line_only_wraps_it(self):
        """One builder, so a fix reaches the EXE and the terminal at once."""
        source = (Path(make_dashboard.__file__)).read_text(encoding="utf-8")
        assert "TEMPLATE" not in source
        assert "def build_data" not in source


class TestWriting:
    def test_nothing_recorded_writes_nothing(self, tmp_path):
        out = tmp_path / "dash.html"
        assert dashboard.write(out, sessions=[]) is None
        assert not out.exists()

    def test_it_makes_the_folder_it_writes_into(self, tmp_path):
        out = tmp_path / "deep" / "down" / "dash.html"
        assert dashboard.write(out, sessions=[_session()]) == out
        assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")

    def test_it_reads_the_log_when_handed_no_sessions(self, tmp_path):
        from pickhero import practice_log
        log = tmp_path / "log.jsonl"
        practice_log.append(_session(song="from_the_log"), log)
        out = tmp_path / "dash.html"
        dashboard.write(out, log=log)
        assert "from_the_log" in out.read_text(encoding="utf-8")


class TestTheAppWritesItOnTheWayOut:
    def test_after_the_sitting_is_written_not_before(self):
        """A page built at startup is always one session stale: it would never
        show the practising that was just done."""
        import inspect
        from pickhero.ui.app import App
        source = inspect.getsource(App.run)
        assert source.index("close_session") < source.index("_write_dashboard")

    def test_it_writes(self, monkeypatch):
        from pickhero.ui.app import App
        calls = []
        monkeypatch.setattr(dashboard, "write", lambda: calls.append(1))
        App._write_dashboard()
        assert calls == [1]

    def test_a_failure_cannot_take_the_app_down_on_the_way_out(
            self, monkeypatch, capsys):
        """An exception here is a crash on exit, which looks like data loss."""
        from pickhero.ui.app import App

        def boom():
            raise OSError("disk full")

        monkeypatch.setattr(dashboard, "write", boom)
        App._write_dashboard()                      # must not raise
        assert "disk full" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The week view. Its sums are done in the browser, so they are checked in a
# real JavaScript engine rather than by looking for the word "Woche".
# ---------------------------------------------------------------------------

HARNESS = """
import fs from 'fs';
const html = fs.readFileSync(process.argv[2], 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];
const nodes = new Map();
const make = id => ({id, textContent: '', innerHTML: '', value: 'minutes',
  disabled: false, style: {},
  classList: {toggle() {}, contains() { return true; }, add() {}},
  appendChild() {}, addEventListener() {}, onclick: null});
globalThis.document = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, make(id));
                       return nodes.get(id); },
  createElement() { return make('new'); },
};
const api = new Function(script + '\\n;return {stepWeek, isoWeek};')();
const snap = () => ({
  label: nodes.get('weekLabel').textContent,
  rows: nodes.get('week').innerHTML.split('<tr').length - 1,
  today: /class="today"/.test(nodes.get('week').innerHTML),
  numbers: [...nodes.get('week').innerHTML.matchAll(
    /<td class="num">([^<]*)<\\/td>/g)].map(m => m[1]),
  prev: nodes.get('weekPrev').disabled,
  next: nodes.get('weekNext').disabled,
});
const out = {now: snap()};
api.stepWeek(-1);
out.before = snap();
for (let i = 0; i < 300; i++) api.stepWeek(-1);
out.oldest = snap();
for (let i = 0; i < 300; i++) api.stepWeek(1);
out.newest = snap();
out.weeks = ['2026-01-01', '2026-08-24', '2020-12-28', '2021-01-04']
  .map(d => api.isoWeek(new Date(d + 'T00:00:00Z')));
console.log(JSON.stringify(out));
"""


@pytest.fixture
def week_view(tmp_path):
    """The week panel of a page built from a known set of sittings, as the
    browser would have it."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no JavaScript engine here to run the page in")

    from pickhero import practice_log
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    def sitting(day, hour, song, seconds, strikes):
        return Session(started=f"{day.isoformat()}T{hour:02d}:00:00", song=song,
                       seconds=seconds, strikes=strikes, tempo_percent=100)

    sessions = [
        sitting(monday, 18, "solo", 600.0, 300),
        sitting(monday, 20, "solo", 900.0, 450),         # same day, second go
        sitting(monday + timedelta(days=2), 19, "riff", 1200.0, 800),
        sitting(monday - timedelta(days=1), 17, "old", 300.0, 120),
    ]
    out = tmp_path / "dash.html"
    dashboard.write(out, sessions=sessions)
    harness = tmp_path / "harness.mjs"
    harness.write_text(HARNESS, encoding="utf-8")
    done = subprocess.run([node, str(harness), str(out)],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


class TestTheWeekView:
    def test_every_day_is_there_whether_it_was_practised_or_not(self, week_view):
        """A week with Wednesday missing cannot answer "did I practise"."""
        # a header, seven days, and the week's own total
        assert week_view["now"]["rows"] == 9

    def test_all_three_numbers_for_each_day(self, week_view):
        numbers = week_view["now"]["numbers"]
        assert len(numbers) == 8 * 3                 # seven days plus the sum
        assert numbers[0:3] == ["25", "750", "2"]    # Monday, both sittings
        assert numbers[3:6] == ["—"] * 3        # Tuesday, nothing
        assert numbers[6:9] == ["20", "800", "1"]    # Wednesday

    def test_the_week_adds_itself_up(self, week_view):
        assert week_view["now"]["numbers"][-3:] == ["45", "1.550", "3"]

    def test_today_is_marked(self, week_view):
        assert week_view["now"]["today"]

    def test_a_week_back_shows_that_week(self, week_view):
        before = week_view["before"]
        assert before["numbers"][-3:] == ["5", "120", "1"]
        assert not before["next"]                    # forward is open again

    def test_it_does_not_walk_off_into_empty_weeks(self, week_view):
        """Backwards stops at the first week ever practised."""
        assert week_view["oldest"]["prev"]
        assert week_view["oldest"]["label"] == week_view["before"]["label"]

    def test_and_never_into_the_future(self, week_view):
        assert week_view["newest"]["next"]
        assert week_view["newest"]["label"] == week_view["now"]["label"]

    def test_the_calendar_week_is_the_one_printed_in_germany(self, week_view):
        # 2026-01-01 is a Thursday, so KW 1; 2020-12-28 is in KW 53 of 2020
        assert week_view["weeks"] == [1, 35, 53, 1]


class TestThePageIsCurrentWithinASitting:
    """The player practised ten minutes, opened the page, and it said three.
    Nothing was lost -- the page was only rebuilt when the APP closed, so it
    described the state at the last close. Leaving a song is where "how long
    have I played today" is asked, and it is also where the sitting is
    written, so it is where the page has to be rebuilt.
    """

    def _app(self, written):
        import pygame
        from pickhero.ui.app import App

        class Screen:
            def __init__(self):
                self.stopped = False

            def handle_event(self, event):
                return "menu"

            def stop_audio(self):
                self.stopped = True
                written.append("session")

        class Menu:
            def scan_files(self):
                pass

        app = App.__new__(App)
        app._playing_screen = Screen()
        app._menu = Menu()
        app._state = "playing"
        app._write_dashboard = lambda: written.append("dashboard")
        return app, pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)

    def test_leaving_a_song_rebuilds_it(self):
        written = []
        app, event = self._app(written)
        app._handle_playing_event(event)
        assert "dashboard" in written

    def test_and_only_after_the_sitting_has_been_written(self):
        """The other order would show the page one session stale for ever,
        which is the fault this exists to fix."""
        written = []
        app, event = self._app(written)
        app._handle_playing_event(event)
        assert written.index("session") < written.index("dashboard")
