"""The practice diary: how long, how many notes, per song and per period.

`progress.py` keeps the best a song has ever been played and forgets the rest,
which cannot answer "how much did I play this month". This is the other half,
and what the tests pin down is mostly what the numbers MEAN -- real seconds
rather than song time, strikes rather than written notes -- because those are
the two things a dashboard built on top would silently get wrong.
"""

import json

import pytest

from pickhero import practice_log
from pickhero.practice_log import Session


def _session(**kwargs):
    base = dict(started="2026-08-24T19:30:00", song="solo", seconds=600.0,
                strikes=420, tempo_percent=80)
    base.update(kwargs)
    return Session(**base)


class TestWritingItDown:
    def test_a_session_becomes_one_line(self, tmp_path):
        path = tmp_path / "log.jsonl"
        practice_log.append(_session(), path)
        assert len(path.read_text().splitlines()) == 1

    def test_sessions_are_appended_never_rewritten(self, tmp_path):
        """A crash mid-write can cost the last line; a rewritten file can cost
        the year."""
        path = tmp_path / "log.jsonl"
        practice_log.append(_session(song="a"), path)
        practice_log.append(_session(song="b"), path)
        assert [s.song for s in practice_log.read(path)] == ["a", "b"]

    def test_every_line_stands_on_its_own(self, tmp_path):
        """The whole point of the format: three lines of anything can read it."""
        path = tmp_path / "log.jsonl"
        practice_log.append(_session(), path)
        row = json.loads(path.read_text().splitlines()[0])
        assert row["song"] == "solo" and row["strikes"] == 420

    def test_looking_at_a_song_is_not_practice(self, tmp_path):
        path = tmp_path / "log.jsonl"
        assert practice_log.append(_session(seconds=2.0), path) is False
        assert practice_log.read(path) == []

    def test_one_damaged_line_is_not_a_lost_year(self, tmp_path):
        path = tmp_path / "log.jsonl"
        practice_log.append(_session(song="a"), path)
        with open(path, "a") as handle:
            handle.write("{ this is not json\n")
        practice_log.append(_session(song="b"), path)
        assert [s.song for s in practice_log.read(path)] == ["a", "b"]

    def test_nothing_recorded_yet_is_not_an_error(self, tmp_path):
        assert practice_log.read(tmp_path / "nothing.jsonl") == []


class TestAddingItUp:
    LOG = [
        _session(started="2026-08-23T10:00:00", song="a", seconds=600, strikes=100),
        _session(started="2026-08-23T20:00:00", song="b", seconds=300, strikes=50),
        _session(started="2026-08-24T10:00:00", song="a", seconds=900, strikes=200),
        _session(started="2026-09-01T10:00:00", song="a", seconds=60, strikes=10),
    ]

    def test_a_day_adds_up_its_sessions(self):
        day = practice_log.totals(self.LOG, "day")[0]
        assert day.key == "2026-08-23"
        assert day.minutes == pytest.approx(15.0)
        assert day.strikes == 150
        assert day.sessions == 2

    def test_a_day_counts_the_songs_touched_not_the_sittings(self):
        assert len(practice_log.totals(self.LOG, "day")[0].songs) == 2

    def test_months_and_years_add_up_the_days(self):
        months = practice_log.totals(self.LOG, "month")
        assert [m.key for m in months] == ["2026-08", "2026-09"]
        assert months[0].minutes == pytest.approx(30.0)
        assert practice_log.totals(self.LOG, "year")[0].strikes == 360

    def test_a_song_adds_up_across_days(self):
        by_song = {t.key: t for t in practice_log.totals(self.LOG, "song")}
        assert by_song["a"].sessions == 3
        assert by_song["a"].minutes == pytest.approx(26.0)

    def test_periods_come_out_oldest_first(self):
        keys = [t.key for t in practice_log.totals(self.LOG, "day")]
        assert keys == sorted(keys)

    def test_an_empty_log_adds_up_to_nothing(self):
        assert practice_log.totals([], "day") == []


class TestWhatTheNumbersMean:
    """Both could be read two ways, and a dashboard would get them wrong
    silently."""

    def test_time_is_real_seconds_not_song_time(self):
        """At 70 % speed the song is shorter than the time you spent on it."""
        from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline
        from pickhero.ui.scrolling import PlayingScreen
        notes = [NoteEvent(timestamp_ms=1000.0, duration_ms=400.0, midi_note=40,
                           string=6, fret=0, measure=0)]
        screen = PlayingScreen(Timeline(notes, SongMetadata(title="x", tempo=100)),
                               song_key="x")
        screen.set_tempo_factor(0.7)
        screen._playing = True
        screen._last_tick = 0.0
        import time as _time
        screen.update()          # one tick of real time, whatever it was
        assert screen._session_seconds > 0
        # Song time advanced by 70 % of it; the diary keeps the whole second.
        assert screen._session_seconds >= screen._playback_ms / 1000.0 * 0.99

    def test_a_session_with_no_score_says_so_rather_than_claiming_zero(self,
                                                                      tmp_path):
        """A session spent looping four bars has no accuracy, and 0 % would be
        a lie a dashboard would happily average in."""
        session = _session(accuracy=None, notes_hit=None)
        practice_log.append(session, tmp_path / "log.jsonl")
        stored = practice_log.read(tmp_path / "log.jsonl")[0]
        assert stored.accuracy is None and stored.notes_hit is None


class TestTheAppWritesOne:
    def _screen(self, tmp_path, monkeypatch):
        from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline
        from pickhero.ui.scrolling import PlayingScreen
        monkeypatch.setattr(practice_log, "PRACTICE_FILE", tmp_path / "log.jsonl")
        notes = [NoteEvent(timestamp_ms=1000.0, duration_ms=400.0, midi_note=40,
                           string=6, fret=0, measure=0)]
        screen = PlayingScreen(Timeline(notes, SongMetadata(title="x", tempo=100)),
                               song_key="solo")
        screen._session_seconds = 300.0
        screen._session_strikes = 250
        return screen, tmp_path / "log.jsonl"

    def test_leaving_a_song_writes_the_session(self, tmp_path, monkeypatch):
        screen, path = self._screen(tmp_path, monkeypatch)
        assert screen.close_session() is True
        stored = practice_log.read(path)[0]
        assert stored.song == "solo" and stored.strikes == 250

    def test_it_is_written_once_however_often_the_song_ends(
            self, tmp_path, monkeypatch):
        """Finishing a song and then leaving it reaches this twice."""
        screen, path = self._screen(tmp_path, monkeypatch)
        screen.close_session()
        screen.close_session()
        assert len(practice_log.read(path)) == 1

    def test_a_song_with_no_key_is_not_recorded(self, tmp_path, monkeypatch):
        screen, _ = self._screen(tmp_path, monkeypatch)
        screen._song_key = ""
        assert screen.close_session() is False

    def test_the_speed_it_was_practised_at_is_kept(self, tmp_path, monkeypatch):
        screen, path = self._screen(tmp_path, monkeypatch)
        screen._tempo_factor = 0.7
        screen.close_session()
        assert practice_log.read(path)[0].tempo_percent == 70

    def test_a_diary_that_cannot_be_written_does_not_stop_the_app(
            self, tmp_path, monkeypatch):
        screen, _ = self._screen(tmp_path, monkeypatch)
        monkeypatch.setattr(practice_log, "append",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
        assert screen.close_session() is False
