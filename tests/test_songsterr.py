"""Songsterr's own per-bar timing, and how it is fitted to a real recording.

Songsterr does not find its sync by listening: it stores a timestamp per
measure into a YouTube video. That is a made map, and a made map never fails
on a song that repeats itself -- which is exactly where a windowed search
does. It is also coarser: measured against the player's Thunder recording on
readings neither map was fitted to, the listening is 8-16 ms and this is
80-92 ms. So it is the fallback, not the answer.
"""

import json

import pytest

from pickhero.audio import autosync
from pickhero.tabs import songsterr
from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)

# One entry of the real reply for song 2333598, trimmed to eight bars. The
# shape is what is being tested; the whole 91 are in the app's own run.
REAL = [
    {"id": 1986470, "videoId": "-IWAOPi4VCc", "feature": None,
     "points": [0.94, 4.04, 7.11, 10.2, 13.27, 16.42, 19.53, 22.62]},
    {"id": 3115424, "videoId": "2G-FAjzT5Fo", "feature": "alternative",
     "points": [-24.21, -21.11, -18.04, -14.95, -11.88, -8.73, -5.62, -2.53]},
]


class TestFindingTheSongInALink:

    def test_a_songsterr_url(self):
        assert songsterr.song_id_of(
            "https://www.songsterr.com/a/wsa/"
            "thunder-love-walked-in-v4-tab-s2333598") == 2333598

    def test_a_bare_id_because_somebody_will_type_one(self):
        assert songsterr.song_id_of("2333598") == 2333598

    def test_and_anything_else_is_not_a_link(self):
        for text in ("", "   ", "https://example.com/song", "hello", None):
            assert songsterr.song_id_of(text) is None

    def test_a_url_with_a_trailing_slash_or_query(self):
        assert songsterr.song_id_of(
            "https://www.songsterr.com/a/wsa/x-tab-s2333598?foo=1") == 2333598


class TestReadingTheBarTimes:

    def test_the_points_come_out_as_seconds(self):
        assert songsterr.bar_times_of(REAL)[:3] == [0.94, 4.04, 7.11]

    def test_a_video_that_starts_later_carries_the_same_curve(self):
        """Three videos of Thunder differ from the first by exactly -25.15
        and -23.25 s on all 91 points. The shape is the data; the constant is
        found against the recording the player actually has."""
        first, second = REAL[0]["points"], REAL[1]["points"]
        gaps = {round(b - a, 6) for a, b in zip(first, second)}
        assert len(gaps) == 1

    def test_every_usable_timeline_comes_back(self):
        """They are not the same song. What's Up's main video carries 72
        points over 253 s and its backing videos 78 over 278 s, and only one
        of them is the file in the player's folder."""
        entries = [{"points": [0.0, 1.0]}, {"points": [0.0, 1.0, 2.0, 3.0]}]
        assert [len(t) for t in songsterr.all_bar_times(entries)] == [4, 2]

    def test_the_same_timeline_twice_counts_once(self):
        """What's Up offers six entries and three distinct timelines."""
        entries = [{"points": [0.0, 1.0, 2.0]}] * 3
        assert len(songsterr.all_bar_times(entries)) == 1

    def test_and_one_of_them_is_still_available_on_its_own(self):
        entries = [{"points": [0.0, 1.0]}, {"points": [0.0, 1.0, 2.0, 3.0]}]
        assert len(songsterr.bar_times_of(entries)) == 4

    def test_rubbish_entries_are_skipped_rather_than_crashed_on(self):
        entries = [{"points": "nonsense"}, {}, {"points": [1.0]},
                   {"points": [3.0, 2.0, 1.0]}, {"points": [0.0, 1.0, 2.0]}]
        assert songsterr.bar_times_of(entries) == [0.0, 1.0, 2.0]
        assert songsterr.all_bar_times(entries) == [[0.0, 1.0, 2.0]]

    def test_nothing_usable_is_an_empty_answer_not_an_exception(self):
        assert songsterr.bar_times_of([]) == []
        assert songsterr.bar_times_of(None) == []
        assert songsterr.all_bar_times(None) == []


def _song(bars=8, bar_ms=3077.0, per_bar=4):
    notes, measures = [], []
    for bar in range(bars):
        measures.append(MeasureInfo(index=bar, start_ms=bar * bar_ms,
                                    end_ms=(bar + 1) * bar_ms))
        for beat in range(per_bar):
            notes.append(NoteEvent(
                timestamp_ms=bar * bar_ms + beat * bar_ms / per_bar,
                duration_ms=400.0, midi_note=40 + beat, string=6 - beat % 4,
                fret=beat, measure=bar))
    return Timeline(notes, SongMetadata(title="t", tempo=78),
                    measures=measures)


class TestWhereAMadeMapPutsAMoment:

    def test_a_bar_line_lands_on_its_own_time(self):
        song = _song()
        starts = [m.start_ms for m in song.measures]
        times = REAL[0]["points"]
        for i, want in enumerate(times):
            got = autosync.bar_lag(starts[i], starts, times)
            assert got == pytest.approx(want - starts[i] / 1000.0)

    def test_the_middle_of_a_bar_is_between_its_edges(self):
        song = _song()
        starts = [m.start_ms for m in song.measures]
        times = REAL[0]["points"]
        middle = (starts[2] + starts[3]) / 2
        at = middle / 1000.0 + autosync.bar_lag(middle, starts, times)
        assert times[2] < at < times[3]

    def test_a_map_with_nothing_in_it_moves_nothing(self):
        assert autosync.bar_lag(1000.0, [], []) == 0.0

    def test_the_warped_song_has_the_same_notes_in_new_places(self):
        song = _song()
        starts = [m.start_ms for m in song.measures]
        times = REAL[0]["points"]
        warped = autosync.replace_times(song, starts, times)
        assert len(warped.notes) == len(song.notes)
        assert warped.measures[0].start_ms == pytest.approx(940.0)
        assert all(n.duration_ms > 0 for n in warped.notes)


class TestFittingItToARecording:
    """The one number that is not in the map: where that video sits in the
    file the player actually has."""

    def _aligned(self, monkeypatch, lags, bars=8, times=None):
        """Stand in for the listening, so the fitting is what is tested."""
        song = _song(bars=bars)
        rows = [(float(i * 6), lag, 0.9) for i, lag in enumerate(lags)]
        monkeypatch.setattr(autosync, "decode",
                            lambda path, samplerate=44100: (
                                __import__("numpy").zeros(44100), 44100))
        monkeypatch.setattr(autosync, "chroma_of_audio",
                            lambda s, r, progress=None: (
                                __import__("numpy").zeros((100, 12)), 21.5))
        monkeypatch.setattr(autosync, "chroma_of_timeline",
                            lambda tl, fps: __import__("numpy").zeros((100, 12)))
        monkeypatch.setattr(autosync, "drift_curve",
                            lambda tab, rec, fps, progress=None: rows)
        return autosync.align_to_bar_times(
            song, "audio.mp3", times or REAL[0]["points"][:bars])

    def test_windows_that_agree_give_one_constant(self, monkeypatch):
        report = self._aligned(monkeypatch, [1.2, 1.25, 1.18, 1.22, 1.21])
        assert report["readable"]
        assert report["constant_s"] == pytest.approx(1.21, abs=0.05)
        assert report["scatter_ms"] < 60

    def test_and_the_points_carry_that_constant(self, monkeypatch):
        report = self._aligned(monkeypatch, [1.2, 1.25, 1.18, 1.22, 1.21])
        first_offset = report["points"][0][1]
        # bar 1 is at 0.94 s in the video, the video is 1.21 s into the file
        assert first_offset == pytest.approx(-(0.94 + 1.21) * 1000, abs=60)

    def test_windows_that_do_not_agree_are_not_this_recording(self, monkeypatch):
        report = self._aligned(monkeypatch, [1.2, -30.0, 18.0, -6.0, 25.0])
        assert not report["readable"]
        assert report["points"] == []

    def test_a_map_of_a_different_length_is_refused_not_stretched(self):
        """Repeats written out differently, or a revision that moved on.
        Stretching it would be silent and wrong everywhere after the first
        difference."""
        report = autosync.align_to_bar_times(
            _song(bars=8), "audio.mp3", REAL[0]["points"][:5])
        assert report["wrong_bars"] and not report["readable"]
        assert report["bars"] == 5 and report["measures"] == 8

    def test_an_empty_map_is_an_answer_not_a_crash(self):
        report = autosync.align_to_bar_times(_song(), "audio.mp3", [])
        assert not report["readable"] and report["points"] == []

    def test_the_answer_says_which_measurement_it_came_from(self, monkeypatch):
        report = self._aligned(monkeypatch, [1.2, 1.25, 1.18, 1.22, 1.21])
        assert report["source"] == "songsterr"


class TestInsideTheApp:
    """Ctrl+U pastes the link, Ctrl+S falls back to it, and the panel never
    dresses one measurement up as the other."""

    import pygame as _pygame

    def _screen(self, tmp_path, monkeypatch, song_id=0):
        import pygame
        from pickhero.audio.mp3_playback import Mp3Player
        from pickhero.config import Config
        from pickhero.ui.scrolling import PlayingScreen
        pygame.init()
        pygame.display.set_mode((640, 480))
        audio = tmp_path / "backing.mp3"
        audio.write_bytes(b"x")
        config = Config()
        config.set_mp3_path_for("song", str(audio))
        if song_id:
            config.set_songsterr_for("song", song_id)
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        monkeypatch.setattr(Mp3Player, "ready", property(lambda self: True))
        return PlayingScreen(_song(), config=config, song_key="song")

    def _clipboard(self, monkeypatch, text):
        from pickhero.ui.scrolling import PlayingScreen
        monkeypatch.setattr(PlayingScreen, "_clipboard_text",
                            staticmethod(lambda: text))

    def test_ctrl_u_remembers_the_song(self, tmp_path, monkeypatch):
        import pygame
        screen = self._screen(tmp_path, monkeypatch)
        self._clipboard(monkeypatch, "https://www.songsterr.com/a/wsa/"
                                     "thunder-love-walked-in-v4-tab-s2333598")
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_u, mod=pygame.KMOD_LCTRL))
        assert screen._config.songsterr_for("song") == 2333598
        assert "2333598" in screen._status_note_text()

    def test_a_clipboard_with_no_link_says_what_to_copy(self, tmp_path,
                                                        monkeypatch):
        import pygame
        screen = self._screen(tmp_path, monkeypatch)
        self._clipboard(monkeypatch, "just some text")
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_u, mod=pygame.KMOD_LCTRL))
        assert screen._config.songsterr_for("song") == 0
        assert "Songsterr link" in screen._status_note_text()

    def test_no_desktop_to_ask_reads_as_an_empty_clipboard(self):
        """No tkinter and nothing copied look the same from here."""
        from pickhero.ui.scrolling import PlayingScreen
        assert isinstance(PlayingScreen._clipboard_text(), str)

    def test_plain_u_and_shift_u_are_untouched(self, tmp_path, monkeypatch):
        import pygame
        screen = self._screen(tmp_path, monkeypatch)
        before = screen._mp3_muted
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_u, mod=0))
        assert screen._mp3_muted is not before

    def _run(self, screen):
        screen.update()
        if screen._auto_sync_thread is not None:
            screen._auto_sync_thread.join(30)
        screen._take_auto_sync()

    def _listening(self, monkeypatch, readable):
        monkeypatch.setattr(autosync, "find", lambda *a, **k: {
            "points": [(0.0, -100.0), (60_000.0, -120.0)] if readable else [],
            "readable": readable, "share": 0.8 if readable else 0.1,
            "windows": 40, "ambiguous": 2, "usable": 32 if readable else 4,
            "breaks": [], "sections": 1, "sections_used": 1, "unreadable": [],
            "covered": (0.0, 240.0), "song_s": 260.0, "tab_s": 260.0,
            "recording_s": 260.0, "length_gap": 0.0, "wrong_length": False,
        })

    def test_the_listening_is_asked_first_and_kept_when_it_works(
            self, tmp_path, monkeypatch):
        """It is five to ten times finer than the bar map where it works."""
        asked = []
        self._listening(monkeypatch, readable=True)
        monkeypatch.setattr(songsterr, "fetch_bar_times",
                            lambda song_id: asked.append(song_id) or ([], {}))
        screen = self._screen(tmp_path, monkeypatch, song_id=2333598)
        screen._start_auto_sync()
        self._run(screen)
        assert asked == [], "Songsterr was asked when it was not needed"
        assert screen._mp3_anchors() == [(0.0, -100.0), (60_000.0, -120.0)]

    def test_and_the_bar_map_takes_over_when_it_does_not(self, tmp_path,
                                                          monkeypatch):
        self._listening(monkeypatch, readable=False)
        monkeypatch.setattr(
            songsterr, "fetch_bar_times",
            lambda song_id: ([0.94, 4.04, 7.11, 10.2, 13.27, 16.42, 19.53,
                              22.62], {"title": "Love Walked In v4"}))
        monkeypatch.setattr(autosync, "align_to_bar_times",
                            lambda tl, path, bars, progress=None: {
                                "source": "songsterr", "bars": len(bars),
                                "measures": len(bars), "readable": True,
                                "points": [(0.0, -2150.0), (21539.0, -2160.0)],
                                "windows": 40, "usable": 38, "ambiguous": 1,
                                "constant_s": 1.21, "scatter_ms": 76.0,
                                "breaks": [], "covered": (0.0, 240.0),
                                "song_s": 260.0, "wrong_bars": False,
                            })
        screen = self._screen(tmp_path, monkeypatch, song_id=2333598)
        screen._start_auto_sync()
        self._run(screen)
        assert screen._mp3_anchors() == [(0.0, -2150.0), (21539.0, -2160.0)]
        panel = " ".join(screen._sync_lines)
        assert "Songsterr" in panel and "8 bar times" in panel
        assert "coarser than listening" in panel, \
            "it is being dressed up as the finer measurement"

    def test_with_no_link_pasted_it_is_never_asked(self, tmp_path, monkeypatch):
        asked = []
        self._listening(monkeypatch, readable=False)
        monkeypatch.setattr(songsterr, "fetch_bar_times",
                            lambda song_id: asked.append(song_id) or ([], {}))
        screen = self._screen(tmp_path, monkeypatch)
        screen._start_auto_sync()
        self._run(screen)
        assert asked == []
        assert "could not read" in " ".join(screen._sync_lines)

    def test_a_songsterr_that_has_nothing_is_named_not_swallowed(
            self, tmp_path, monkeypatch):
        self._listening(monkeypatch, readable=False)

        def missing(song_id):
            raise songsterr.NotFound("Songsterr answered 404")

        monkeypatch.setattr(songsterr, "fetch_bar_times", missing)
        screen = self._screen(tmp_path, monkeypatch, song_id=999)
        screen._start_auto_sync()
        self._run(screen)
        assert screen._mp3_anchors() == []
        assert "404" in " ".join(screen._sync_lines)


class TestTheChoiceIsThePlayersNotTheApps:
    """"Kannst du es so bauen, dass ich entscheiden kann, ob Songsterr oder
    manuell. Ich habe das Gefühl es entscheidet noch immer selbst."

    He read it exactly right. The first build asked the listening whether
    the listening had worked, and only fell back when it said no -- a circle
    with the player outside it. Alt+S is a CHOICE now, four answers, and
    Ctrl+S obeys it.
    """

    def _screen(self, tmp_path, monkeypatch, song_id=2333598):
        return TestInsideTheApp()._screen(tmp_path, monkeypatch, song_id)

    def _run(self, screen):
        return TestInsideTheApp()._run(screen)

    def _both(self, monkeypatch, listening_readable=True, bars_readable=True):
        asked = []
        monkeypatch.setattr(autosync, "find", lambda *a, **k: asked.append(
            "listened") or {
            "points": [(0.0, -100.0), (60_000.0, -120.0)]
            if listening_readable else [],
            "readable": listening_readable, "share": 0.8, "windows": 40,
            "ambiguous": 2, "usable": 32, "breaks": [], "sections": 1,
            "sections_used": 1, "unreadable": [], "covered": (0.0, 240.0),
            "song_s": 260.0, "tab_s": 260.0, "recording_s": 260.0,
            "length_gap": 0.0, "wrong_length": False})
        monkeypatch.setattr(songsterr, "fetch_bar_times",
                            lambda song_id: ([0.94, 4.04, 7.11, 10.2],
                                             {"title": "t"}))
        monkeypatch.setattr(autosync, "align_to_bar_times",
                            lambda tl, path, bars, progress=None: asked.append(
                                "songsterr") or {
                                "source": "songsterr", "bars": 4,
                                "measures": 4, "readable": bars_readable,
                                "points": [(0.0, -2150.0), (9231.0, -2160.0)]
                                if bars_readable else [],
                                "windows": 40, "usable": 38, "ambiguous": 1,
                                "constant_s": 1.21, "scatter_ms": 76.0,
                                "breaks": [], "covered": (0.0, 240.0),
                                "song_s": 260.0, "wrong_bars": False})
        return asked

    def _alt_s(self, screen):
        import pygame
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_s, mod=pygame.KMOD_LALT))

    def _ctrl_s(self, screen):
        import pygame
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_s, mod=pygame.KMOD_LCTRL))

    def test_alt_s_walks_the_four_answers(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        seen = [screen._sync_source()]
        for _ in range(4):
            self._alt_s(screen)
            seen.append(screen._sync_source())
        assert seen == ["auto", "listen", "songsterr", "hand", "auto"]

    def test_and_says_which_one_it_landed_on(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        self._alt_s(screen)
        assert "listen only" in screen._status_note_text()

    def test_listen_only_never_asks_songsterr(self, tmp_path, monkeypatch):
        """Even when the listening comes back with nothing. That is the
        player saying the bar map is not what he wants here."""
        asked = self._both(monkeypatch, listening_readable=False)
        screen = self._screen(tmp_path, monkeypatch)
        screen._config.set_sync_source_for("song", "listen")
        self._ctrl_s(screen)
        self._run(screen)
        assert asked == ["listened"]
        assert screen._mp3_anchors() == []

    def test_songsterr_only_never_listens(self, tmp_path, monkeypatch):
        """Even when the bar map does not fit. Falling back here would be
        the app deciding again, which is the thing that was reported."""
        asked = self._both(monkeypatch, bars_readable=False)
        screen = self._screen(tmp_path, monkeypatch)
        screen._config.set_sync_source_for("song", "songsterr")
        self._ctrl_s(screen)
        self._run(screen)
        assert asked == ["songsterr"]
        assert screen._mp3_anchors() == []

    def test_and_it_is_used_even_when_the_listening_would_have_worked(
            self, tmp_path, monkeypatch):
        asked = self._both(monkeypatch)
        screen = self._screen(tmp_path, monkeypatch)
        screen._config.set_sync_source_for("song", "songsterr")
        self._ctrl_s(screen)
        self._run(screen)
        assert asked == ["songsterr"]
        assert screen._mp3_anchors() == [(0.0, -2150.0), (9231.0, -2160.0)]

    def test_by_hand_makes_ctrl_s_do_nothing_and_say_so(self, tmp_path,
                                                         monkeypatch):
        asked = self._both(monkeypatch)
        screen = self._screen(tmp_path, monkeypatch)
        screen._config.set_sync_source_for("song", "hand")
        self._ctrl_s(screen)
        assert asked == []
        assert screen._auto_sync_thread is None
        assert "by hand" in screen._status_note_text()

    def test_songsterr_with_no_link_refuses_rather_than_listening(
            self, tmp_path, monkeypatch):
        asked = self._both(monkeypatch)
        screen = self._screen(tmp_path, monkeypatch, song_id=0)
        screen._config.set_sync_source_for("song", "songsterr")
        self._ctrl_s(screen)
        assert asked == []
        assert "Ctrl+U" in screen._status_note_text()

    def test_auto_is_still_the_old_behaviour(self, tmp_path, monkeypatch):
        """A song nobody has decided about has to do something."""
        asked = self._both(monkeypatch, listening_readable=False)
        screen = self._screen(tmp_path, monkeypatch)
        assert screen._sync_source() == "auto"
        self._ctrl_s(screen)
        self._run(screen)
        assert asked == ["listened", "songsterr"]

    def test_plain_s_still_opens_the_panel(self, tmp_path, monkeypatch):
        """Alt is neither Ctrl nor Shift, so the bare-S branch would have
        swallowed it."""
        import pygame
        screen = self._screen(tmp_path, monkeypatch)
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_s, mod=0))
        assert screen._show_sync is True
        assert screen._sync_source() == "auto", "the source moved too"

    def test_the_panel_names_the_source_and_the_key(self, tmp_path,
                                                    monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        panel = " ".join(text for text, _ in screen.sync_block_lines())
        assert "source:" in panel and "Alt+S" in panel
        assert "2333598" in panel

    def test_and_says_when_no_link_is_stored(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, song_id=0)
        panel = " ".join(text for text, _ in screen.sync_block_lines())
        assert "no Songsterr link" in panel and "Ctrl+U" in panel

    def test_the_choice_is_kept_per_song(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        self._alt_s(screen)
        assert screen._config.sync_source_for("song") == "listen"
        assert screen._config.sync_source_for("another") == "auto"


class TestPickingTheRightVideo:
    """"Hast du etwas geändert?" -- yes, twice, and both because of What's
    Up's reply. Its six entries carry three different timelines: the main
    video has 72 points over 253 s, the backing tracks 78 over 278 s. Only
    one of them is the recording in the player's folder, and the first build
    took the LONGEST, which is the wrong one.
    """

    MAIN = [0.0, 3.5, 7.0, 10.5, 14.0, 17.5, 21.0, 24.5]
    BACKING = [9.0, 12.5, 16.0, 19.5, 23.0, 26.5, 30.0, 33.5, 37.0, 40.5]

    def _song(self, bars):
        return _song(bars=bars, bar_ms=3500.0, per_bar=2)

    def _measured(self, monkeypatch, lags_by_bars):
        """Give each candidate its own agreement, by how many bars it has."""
        import numpy as np
        monkeypatch.setattr(autosync, "decode",
                            lambda path, samplerate=44100: (np.zeros(44100),
                                                            44100))
        monkeypatch.setattr(autosync, "chroma_of_audio",
                            lambda s, r, progress=None: (np.zeros((100, 12)),
                                                         21.5))
        seen = {}

        def warp(timeline, starts, times):
            seen["bars"] = len(times)
            return timeline

        monkeypatch.setattr(autosync, "replace_times", warp)
        monkeypatch.setattr(autosync, "chroma_of_timeline",
                            lambda tl, fps: np.zeros((100, 12)))
        monkeypatch.setattr(
            autosync, "drift_curve",
            lambda tab, rec, fps, progress=None: [
                (float(i * 6), lag, 0.9)
                for i, lag in enumerate(lags_by_bars[seen["bars"]])])

    def test_the_candidate_the_recording_agrees_with_wins(self, monkeypatch):
        steady = [1.2, 1.25, 1.18, 1.22, 1.21]
        noise = [1.2, -30.0, 18.0, -6.0, 25.0]
        self._measured(monkeypatch, {8: steady, 10: noise})
        report = autosync.align_to_bar_times(
            self._song(8), "audio.mp3", [self.BACKING, self.MAIN])
        assert report["readable"]
        assert report["bars"] == 8, "it took the longer one again"

    def test_a_single_timeline_is_still_accepted(self, monkeypatch):
        self._measured(monkeypatch, {8: [1.2, 1.25, 1.18, 1.22, 1.21]})
        report = autosync.align_to_bar_times(
            self._song(8), "audio.mp3", self.MAIN)
        assert report["readable"] and report["bars"] == 8

    def test_a_candidate_of_the_wrong_length_is_never_tried(self, monkeypatch):
        """Stretching it would be silent and wrong everywhere after the
        first difference."""
        tried = []
        monkeypatch.setattr(autosync, "decode",
                            lambda *a, **k: tried.append(1) or (None, 0))
        report = autosync.align_to_bar_times(
            self._song(8), "audio.mp3", [self.BACKING])
        assert report["wrong_bars"] and not report["readable"]
        assert tried == [], "it decoded the recording for nothing"

    def test_and_the_counts_it_was_offered_are_reported(self, monkeypatch):
        """"Songsterr times 72 or 78 bars and this tab has 80" is a thing to
        act on; "a different revision" on its own is not."""
        report = autosync.align_to_bar_times(
            self._song(9), "audio.mp3", [self.BACKING, self.MAIN])
        assert sorted(report["offered"]) == [8, 10]
        assert report["measures"] == 9
