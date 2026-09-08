"""Changing instrument mid-song.

Reported as taking longer than opening the song does, which is the tell: the
two go through the same code, so whatever is slower must be something the
FIRST open does not have -- an old screen still holding the input stream and
the MIDI output port, and a file being unpacked more often than it needs.
"""

from pathlib import Path

import pygame
import pytest

from pickhero.config import Config
from pickhero.ui.app import App

SONG = Path(__file__).resolve().parent.parent / "songs" / "timing_test_100bpm.gp5"

pytestmark = pytest.mark.skipif(not SONG.exists(), reason="reference song missing")


@pytest.fixture
def app():
    pygame.init()
    pygame.display.set_mode((640, 480))
    application = App(Config())
    application._menu = None
    return application


class TestTheOldScreenIsTornDown:
    def test_changing_instrument_stops_the_screen_it_replaces(self, app):
        """It held the input stream and the MIDI port open, so the new screen
        opened a second of each -- which on Windows is a real device open."""
        app._load_song(SONG)
        old = app._playing_screen
        stopped = []
        old.stop_audio = lambda: stopped.append(1)
        app._load_song(SONG, 0)
        assert stopped == [1]
        assert app._playing_screen is not old

    def test_the_sitting_is_not_lost_by_changing_instrument(self, app,
                                                            tmp_path,
                                                            monkeypatch):
        """close_session lives in stop_audio, and nothing called it here."""
        from pickhero import practice_log
        monkeypatch.setattr(practice_log, "PRACTICE_FILE", tmp_path / "log.jsonl")
        app._load_song(SONG)
        app._playing_screen._session_seconds = 120.0
        app._playing_screen._session_strikes = 42
        app._load_song(SONG, 0)
        written = practice_log.read(tmp_path / "log.jsonl")
        assert len(written) == 1 and written[0].strikes == 42

    def test_opening_the_first_song_has_nothing_to_tear_down(self, app):
        app._load_song(SONG)
        assert app._playing_screen is not None


class TestTheFileIsUnpackedOnce:
    def _counting(self, monkeypatch):
        import pickhero.tabs.loader as loader
        calls = []
        real = loader.list_tracks
        def counted(path, *a, **k):
            calls.append(path)
            return real(path, *a, **k)
        monkeypatch.setattr(loader, "list_tracks", counted)
        return calls

    def test_the_track_list_is_read_once_per_song(self, app, monkeypatch):
        """It was read twice: once for the labels and once for which tracks
        are guitars. For a GP6 container that means decompressing it twice."""
        calls = self._counting(monkeypatch)
        app._load_song(SONG)
        assert len(calls) == 1

    def test_changing_instrument_does_not_read_it_again(self, app, monkeypatch):
        """A file's tracks cannot change while it sits there being played."""
        app._load_song(SONG)
        calls = self._counting(monkeypatch)
        app._load_song(SONG, 0)
        assert calls == []

    def test_a_different_song_is_read_afresh(self, app, monkeypatch, tmp_path):
        """The cache is keyed by the file, not simply kept."""
        app._load_song(SONG)
        app._tracks_cache_key = str(tmp_path / "other.gp5")
        app._tracks_cache = [{"index": 9, "name": "stale"}]
        assert app._tracks_of(SONG) != [{"index": 9, "name": "stale"}]
