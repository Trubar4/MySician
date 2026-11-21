"""Songsterr's own per-bar timing, and how it is fitted to a real recording.

Songsterr does not find its sync by listening: it stores a timestamp per
measure into a YouTube video. That is a made map, and a made map never fails
on a song that repeats itself -- which is exactly where a windowed search
does. It is also coarser: measured against the player's Thunder recording on
readings neither map was fitted to, the listening is 8-16 ms and this is
80-92 ms. So it is the fallback, not the answer.
"""

import json
import types

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

    def _bar_map(self, monkeypatch, points, title="t", asked=None,
                 raises=None):
        """Stand in for the network, at the two calls the app actually makes.

        `fetch_bar_times` is not one of them any more: the app asks the disk
        first and the network second (`_songsterr_bar_times`), so patching
        the old one-shot helper patched something nothing called -- and every
        one of these tests still passed while measuring nothing.
        """
        def meta(song_id):
            if asked is not None:
                asked.append(song_id)
            if raises is not None:
                raise raises
            return {"songId": song_id, "revisionId": 1, "title": title}

        monkeypatch.setattr(songsterr, "fetch_meta", meta)
        monkeypatch.setattr(
            songsterr, "fetch_entries",
            lambda sid, rev: [{"id": 1, "videoId": "v", "feature": None,
                               "points": list(points)}] if points else [])

    def _listening(self, monkeypatch, readable):
        monkeypatch.setattr(autosync, "find", lambda *a, **k: {
            "points": [(0.0, -100.0), (60_000.0, -120.0)] if readable else [],
            "readable": readable, "share": 0.8 if readable else 0.1,
            "windows": 40, "ambiguous": 2, "usable": 32 if readable else 4,
            "breaks": [], "sections": 1, "sections_used": 1, "unreadable": [],
            "covered": (0.0, 240.0), "song_s": 260.0, "tab_s": 260.0,
            "recording_s": 260.0, "length_gap": 0.0, "wrong_length": False,
        })

    def test_the_bar_map_is_asked_first_and_kept_when_it_fits(
            self, tmp_path, monkeypatch):
        """It is a file read beside the tab and it cannot fail, where the
        listening is seconds of FFT that sometimes reads nothing at all --
        and this runs by itself when a song opens."""
        heard = []
        monkeypatch.setattr(autosync, "find",
                            lambda *a, **k: heard.append(1) or {})
        self._bar_map(monkeypatch, [0.94, 4.04])
        monkeypatch.setattr(autosync, "align_to_bar_times",
                            lambda tl, path, bars, progress=None: {
                                "source": "songsterr", "bars": 2,
                                "measures": 2, "readable": True,
                                "points": [(0.0, -2150.0), (9231.0, -2160.0)],
                                "windows": 40, "usable": 38, "ambiguous": 1,
                                "constant_s": 1.21, "scatter_ms": 76.0,
                                "breaks": [], "covered": (0.0, 240.0),
                                "song_s": 260.0, "wrong_bars": False})
        screen = self._screen(tmp_path, monkeypatch, song_id=2333598)
        screen._start_auto_sync()
        self._run(screen)
        assert heard == [], "the listening ran when it was not needed"
        assert screen._mp3_anchors() == [(0.0, -2150.0), (9231.0, -2160.0)]
        # ... and that it was MADE travels with it, because that is what
        # decides how far a segment of it may bend. A step in a per-bar map
        # is music -- a held bar, a fermata -- where a step in a searched
        # one is a window that matched the wrong chorus.
        assert screen._config.mp3_map_is_made("song")
        assert screen._sync_map().made

    def test_and_the_listening_takes_over_when_the_map_does_not_fit(
            self, tmp_path, monkeypatch):
        """Eva Under Fire: the bar map agrees with 2 of 24 windows and the
        listening with 23. The old default stored "songsterr", which never
        falls back, so that song got no sync at all."""
        self._listening(monkeypatch, readable=True)
        self._bar_map(monkeypatch, [0.94, 4.04])
        monkeypatch.setattr(autosync, "align_to_bar_times",
                            lambda tl, path, bars, progress=None: {
                                "source": "songsterr", "bars": 2,
                                "measures": 2, "readable": False,
                                "points": [], "windows": 24, "usable": 2,
                                "ambiguous": 0, "constant_s": 0.0,
                                "scatter_ms": 0.0, "breaks": [],
                                "covered": None, "song_s": 260.0,
                                "wrong_bars": False})
        screen = self._screen(tmp_path, monkeypatch, song_id=2333598)
        screen._start_auto_sync()
        self._run(screen)
        assert screen._mp3_anchors() == [(0.0, -100.0), (60_000.0, -120.0)]
        # The listening CAN match the wrong chorus, so these points get the
        # tight clamp -- and the bar map's permission must not be left
        # standing from the attempt before it.
        assert not screen._config.mp3_map_is_made("song")
        assert not screen._sync_map().made

    def test_and_neither_says_both_were_tried(self, tmp_path, monkeypatch):
        """"The bar map does not fit" on its own sends the player to press
        Alt+S for the thing that has already failed."""
        self._listening(monkeypatch, readable=False)
        self._bar_map(monkeypatch, [0.94, 4.04])
        monkeypatch.setattr(autosync, "align_to_bar_times",
                            lambda tl, path, bars, progress=None: {
                                "source": "songsterr", "bars": 2,
                                "measures": 2, "readable": False,
                                "points": [], "windows": 24, "usable": 2,
                                "ambiguous": 0, "constant_s": 0.0,
                                "scatter_ms": 0.0, "breaks": [],
                                "covered": None, "song_s": 260.0,
                                "wrong_bars": False})
        screen = self._screen(tmp_path, monkeypatch, song_id=2333598)
        screen._start_auto_sync()
        self._run(screen)
        panel = " ".join(screen._sync_lines)
        assert "2 of 24" in panel
        assert "listening kept 4 of 40" in panel
        assert screen._mp3_anchors() == []

    def test_a_long_map_is_fitted_and_stored(self, tmp_path,
                                              monkeypatch):
        self._listening(monkeypatch, readable=False)
        self._bar_map(monkeypatch,
                      [0.94, 4.04, 7.11, 10.2, 13.27, 16.42, 19.53, 22.62],
                      title="Love Walked In v4")
        monkeypatch.setattr(autosync, "align_to_bar_times",
                            lambda tl, path, bars, progress=None: {
                                "source": "songsterr", "bars": len(bars[0]),
                                "measures": len(bars[0]), "readable": True,
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
        # Which measurement is under the song, and whether its own error was
        # read out of the windows. It used to say "coarser than listening
        # (80-90 ms against 10)", which was true of one constant and is a
        # lie about a refined map -- see `autosync.residual_curve`. A report
        # carrying no refinement says so rather than implying one.
        assert "not refined" in panel, \
            "a map nobody refined is being presented as a refined one"
        assert "repeats itself" in panel, \
            "the one thing it does that the listening cannot is unsaid"

    def test_and_a_refined_one_says_what_was_taken_out(self, tmp_path,
                                                       monkeypatch):
        """The gap the refinement closes is the one the old line announced:
        80-92 ms from the bar times alone against 8-16 from the listening."""
        self._listening(monkeypatch, readable=False)
        self._bar_map(monkeypatch, [0.94, 4.04, 7.11, 10.2])
        monkeypatch.setattr(autosync, "align_to_bar_times",
                            lambda tl, path, bars, progress=None: {
                                "source": "songsterr", "bars": 4,
                                "measures": 4, "readable": True,
                                "points": [(0.0, -2150.0), (9260.0, -2160.0)],
                                "windows": 40, "usable": 38, "ambiguous": 1,
                                "constant_s": 1.21, "scatter_ms": 102.0,
                                "refined": 35, "breaks": [],
                                "covered": (0.0, 240.0), "song_s": 260.0,
                                "wrong_bars": False,
                            })
        screen = self._screen(tmp_path, monkeypatch, song_id=2333598)
        screen._start_auto_sync()
        self._run(screen)
        panel = " ".join(screen._sync_lines)
        assert "refined by 35" in panel and "102 ms" in panel
        assert "not refined" not in panel

    def test_with_no_link_pasted_it_is_never_asked(self, tmp_path, monkeypatch):
        asked = []
        self._listening(monkeypatch, readable=False)
        self._bar_map(monkeypatch, [0.94, 4.04], asked=asked)
        screen = self._screen(tmp_path, monkeypatch)
        screen._start_auto_sync()
        self._run(screen)
        assert asked == []
        assert "could not read" in " ".join(screen._sync_lines)

    def test_a_songsterr_that_has_nothing_is_named_not_swallowed(
            self, tmp_path, monkeypatch):
        self._listening(monkeypatch, readable=False)

        self._bar_map(monkeypatch, [],
                      raises=songsterr.NotFound("Songsterr answered 404"))
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
        TestInsideTheApp()._bar_map(monkeypatch, [0.94, 4.04, 7.11, 10.2])
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

    def test_auto_takes_the_map_first_and_falls_back(self, tmp_path,
                                                      monkeypatch):
        """A song nobody has decided about has to do something, and after
        two songs each of which defeated one measurement, the honest
        something is whichever of the two works."""
        asked = self._both(monkeypatch, bars_readable=False)
        screen = self._screen(tmp_path, monkeypatch)
        assert screen._sync_source() == "auto"
        self._ctrl_s(screen)
        self._run(screen)
        assert asked == ["songsterr", "listened"]

    def test_and_the_listening_is_not_asked_when_the_map_fits(
            self, tmp_path, monkeypatch):
        asked = self._both(monkeypatch)
        screen = self._screen(tmp_path, monkeypatch)
        self._ctrl_s(screen)
        self._run(screen)
        assert asked == ["songsterr"]

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


class TestAMapMayCoverJustTheMusic:
    """"Habe das GP neu von Songsterr geladen... Hab ich noch immer das
    falsche GP?" No. What's Up is 80 bars of which the last TEN carry no
    note on any pitched track -- a Guitar Pro export padded out to the end
    of the sheet. Songsterr times the 72 that have music in them, and
    refusing on the count alone threw away a map that fits perfectly: 21 of
    39 windows agree with it to 17 ms.
    """

    def _padded(self, music=6, empty=4, bar_ms=3500.0):
        notes, measures = [], []
        for bar in range(music + empty):
            measures.append(MeasureInfo(index=bar, start_ms=bar * bar_ms,
                                        end_ms=(bar + 1) * bar_ms))
            if bar >= music:
                continue
            for beat in range(2):
                notes.append(NoteEvent(
                    timestamp_ms=bar * bar_ms + beat * bar_ms / 2,
                    duration_ms=400.0, midi_note=40 + beat, string=6,
                    fret=beat, measure=bar))
        return Timeline(notes, SongMetadata(title="t", tempo=65),
                        measures=measures)

    def _times(self, n, step=3.4):
        return [round(i * step, 2) for i in range(n)]

    def test_a_map_of_the_musical_bars_is_accepted(self):
        song = self._padded(music=6, empty=4)
        starts = [m.start_ms for m in song.measures]
        assert autosync._covers(song, starts, self._times(6))

    def test_and_so_is_one_that_reaches_a_little_past_the_music(self):
        song = self._padded(music=6, empty=4)
        starts = [m.start_ms for m in song.measures]
        assert autosync._covers(song, starts, self._times(8))

    def test_but_not_one_that_stops_inside_the_music(self):
        """Those bars have notes, and a map that does not reach them would
        be silently wrong for every one of them."""
        song = self._padded(music=6, empty=4)
        starts = [m.start_ms for m in song.measures]
        assert not autosync._covers(song, starts, self._times(4))

    def test_nor_one_with_more_bars_than_the_tab_has(self):
        song = self._padded(music=6, empty=4)
        starts = [m.start_ms for m in song.measures]
        assert not autosync._covers(song, starts, self._times(12))

    def test_an_exact_match_is_always_fine(self):
        song = self._padded(music=6, empty=0)
        starts = [m.start_ms for m in song.measures]
        assert autosync._covers(song, starts, self._times(6))

    def test_an_empty_map_covers_nothing(self):
        song = self._padded()
        assert not autosync._covers(song, [m.start_ms for m in song.measures], [])

    def test_the_padded_tab_is_no_longer_refused(self, monkeypatch):
        """The whole point: this is the case that was thrown away."""
        import numpy as np
        song = self._padded(music=6, empty=4)
        monkeypatch.setattr(autosync, "decode",
                            lambda p, samplerate=44100: (np.zeros(44100), 44100))
        monkeypatch.setattr(autosync, "chroma_of_audio",
                            lambda s, r, progress=None: (np.zeros((100, 12)), 21.5))
        monkeypatch.setattr(autosync, "chroma_of_timeline",
                            lambda tl, fps: np.zeros((100, 12)))
        monkeypatch.setattr(autosync, "drift_curve",
                            lambda tab, rec, fps, progress=None: [
                                (float(i * 6), lag, 0.9) for i, lag in
                                enumerate([1.2, 1.25, 1.18, 1.22, 1.21])])
        report = autosync.align_to_bar_times(song, "audio.mp3",
                                             [self._times(6)])
        assert not report["wrong_bars"]
        assert report["readable"] and report["bars"] == 6


class TestPickingARecordingLinesItUp:
    """*"Wenn ich nur noch das MP3 laden und mit sh+U im Song verknüpfen
    muss und die Songsterr Sync schon im Song ist, dann reicht das."*

    Shift+U then Ctrl+S is two keys where the second exists only because
    nothing connected the first to the obvious next step. The recording is
    new, it has no sync, and there is exactly one thing to do with it.
    """

    def _screen(self, tmp_path, monkeypatch, song_id=2333598):
        return TestInsideTheApp()._screen(tmp_path, monkeypatch, song_id)

    def _picks(self, monkeypatch, chosen):
        from pickhero.ui import scrolling
        monkeypatch.setattr(scrolling, "pick_audio_file",
                            lambda start_dir=None: chosen)

    def _started(self, monkeypatch):
        from pickhero.ui.scrolling import PlayingScreen
        runs = []
        monkeypatch.setattr(PlayingScreen, "_start_auto_sync",
                            lambda self: runs.append(self._song_key))
        return runs

    def test_a_new_recording_syncs_itself(self, tmp_path, monkeypatch):
        runs = self._started(monkeypatch)
        screen = self._screen(tmp_path, monkeypatch)
        other = tmp_path / "another.mp3"
        other.write_bytes(b"x")
        self._picks(monkeypatch, str(other))
        screen._open_mp3_dialog()
        assert runs == ["song"]

    def test_a_song_that_already_has_sync_is_left_alone(self, tmp_path,
                                                        monkeypatch):
        """Those points carry the player's own work -- Shift+N/M nudges, a
        pin, an anchor set by ear in the middle of a song that drifts. And
        re-picking the same file is exactly what somebody does after moving
        it."""
        runs = self._started(monkeypatch)
        screen = self._screen(tmp_path, monkeypatch)
        screen._config.set_mp3_anchors_for("song", [(0.0, -120.0),
                                                    (60_000.0, -130.0)])
        self._picks(monkeypatch, screen._mp3_path())
        screen._open_mp3_dialog()
        assert runs == []
        assert screen._mp3_anchors() == [(0.0, -120.0), (60_000.0, -130.0)]

    def test_a_genuinely_different_file_drops_the_old_points_and_resyncs(
            self, tmp_path, monkeypatch):
        """Points measured against another rip put the new file out by
        seconds while LOOKING like a synced song."""
        runs = self._started(monkeypatch)
        screen = self._screen(tmp_path, monkeypatch)
        screen._config.set_mp3_anchors_for("song", [(0.0, -120.0),
                                                    (60_000.0, -130.0)])
        other = tmp_path / "another.mp3"
        other.write_bytes(b"x")
        self._picks(monkeypatch, str(other))
        screen._open_mp3_dialog()
        assert screen._mp3_anchors() == []
        assert runs == ["song"]

    def test_sync_by_hand_is_a_choice_and_is_obeyed(self, tmp_path,
                                                    monkeypatch):
        runs = self._started(monkeypatch)
        screen = self._screen(tmp_path, monkeypatch)
        screen._config.set_sync_source_for("song", "hand")
        other = tmp_path / "another.mp3"
        other.write_bytes(b"x")
        self._picks(monkeypatch, str(other))
        screen._open_mp3_dialog()
        assert runs == []

    def test_a_cancelled_chooser_starts_nothing(self, tmp_path, monkeypatch):
        runs = self._started(monkeypatch)
        screen = self._screen(tmp_path, monkeypatch)
        self._picks(monkeypatch, None)
        screen._open_mp3_dialog()
        assert runs == []

    def test_no_link_is_needed_because_the_map_is_beside_the_tab(
            self, tmp_path, monkeypatch):
        """*"Link sollte ich nicht pasten müssen, weil es mit dem Download
        bereits beim GP dabei liegt."* The cache is read before the network,
        so a downloaded song syncs with no link and no connection."""
        from pickhero.ui.scrolling import PlayingScreen
        tab = tmp_path / "song.gp5"
        tab.write_bytes(b"x")
        songsterr.save_cache(tab, 2333598, {"revisionId": 1,
                                            "title": "Thunder"},
                             [{"id": 1, "videoId": "v", "feature": None,
                               "points": [0.94, 4.04, 7.11]}])

        def never(*a, **k):
            raise AssertionError("asked the network with a map on disk")

        monkeypatch.setattr(songsterr, "fetch_meta", never)
        view = types.SimpleNamespace(_song_path=str(tab))
        bars, meta = PlayingScreen._songsterr_bar_times(view, 2333598)
        assert bars[0] == [0.94, 4.04, 7.11]


class TestOpeningTheSongIsEnough:
    """*"Ich hätte gerne, dass das beim ersten Öffnen automatisch passiert.
    MP3 wird verknüpft, Barmap wird gelesen und gesynct."*

    Two halves had to be joined. The download writes a settings entry
    pointing at the audio -- and an entry written under a name that later
    changed points nowhere while the file sits right beside the tab. And
    even with the recording found, the measurement waited for Ctrl+S.
    """

    def _screen(self, tmp_path, monkeypatch, with_entry=True, audio=True,
                stem="song"):
        import pygame
        from pickhero.audio.mp3_playback import Mp3Player
        from pickhero.config import Config
        from pickhero.ui.scrolling import PlayingScreen
        pygame.init()
        pygame.display.set_mode((640, 480))
        tab = tmp_path / f"{stem}.gp5"
        tab.write_bytes(b"gp")
        if audio:
            (tmp_path / f"{stem}.mp3").write_bytes(b"x")
        config = Config()
        if with_entry and audio:
            config.set_mp3_path_for(stem, str(tmp_path / f"{stem}.mp3"))
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        monkeypatch.setattr(Mp3Player, "ready", property(lambda self: True))
        return PlayingScreen(_song(), config=config, song_key=stem,
                             song_path=str(tab))

    def test_the_recording_beside_the_tab_is_adopted(self, tmp_path,
                                                     monkeypatch):
        """The FILE outranks the note about it. Same folder, same stem."""
        screen = self._screen(tmp_path, monkeypatch, with_entry=False)
        assert screen._mp3_path() == str(tmp_path / "song.mp3")
        assert screen._config.song_mp3_paths["song"] == str(
            tmp_path / "song.mp3")

    def test_a_recording_the_player_chose_is_never_replaced(self, tmp_path,
                                                            monkeypatch):
        mine = tmp_path / "my own take.mp3"
        mine.write_bytes(b"x")
        screen = self._screen(tmp_path, monkeypatch, with_entry=False)
        screen._config.set_mp3_path_for("song", str(mine))
        assert screen._adopt_audio_beside_tab() == ""
        assert screen._mp3_path() == str(mine)

    def test_no_file_beside_the_tab_adopts_nothing(self, tmp_path,
                                                   monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, with_entry=False,
                              audio=False)
        assert screen._mp3_path() == ""

    def test_opening_it_measures_it(self, tmp_path, monkeypatch):
        from pickhero.ui.scrolling import PlayingScreen
        runs = []
        monkeypatch.setattr(PlayingScreen, "_start_auto_sync",
                            lambda self: runs.append(self._song_key))
        screen = self._screen(tmp_path, monkeypatch, with_entry=False)
        assert runs == [], "measured from inside __init__, before a frame"
        screen.update()
        assert runs == ["song"]

    def test_and_only_once(self, tmp_path, monkeypatch):
        """A measurement that finds nothing leaves the points empty, and
        re-arming would measure again every frame for the rest of the
        song."""
        from pickhero.ui.scrolling import PlayingScreen
        runs = []
        monkeypatch.setattr(PlayingScreen, "_start_auto_sync",
                            lambda self: runs.append(1))
        screen = self._screen(tmp_path, monkeypatch, with_entry=False)
        for _ in range(5):
            screen.update()
        assert runs == [1]

    def test_a_song_that_is_already_lined_up_is_left_alone(self, tmp_path,
                                                           monkeypatch):
        from pickhero.ui.scrolling import PlayingScreen
        runs = []
        monkeypatch.setattr(PlayingScreen, "_start_auto_sync",
                            lambda self: runs.append(1))
        tab = tmp_path / "song.gp5"
        tab.write_bytes(b"gp")
        (tmp_path / "song.mp3").write_bytes(b"x")
        from pickhero.config import Config
        config = Config()
        config.set_mp3_anchors_for("song", [(0.0, -120.0)])
        from pickhero.audio.mp3_playback import Mp3Player
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        screen = PlayingScreen(_song(), config=config, song_key="song",
                               song_path=str(tab))
        screen.update()
        assert runs == []

    def test_ctrl_s_disarms_the_one_waiting_for_the_first_frame(
            self, tmp_path, monkeypatch):
        """Otherwise the whole measurement runs a second time on the next
        frame -- the first thread has finished by then, so the
        already-running guard does not catch it."""
        screen = self._screen(tmp_path, monkeypatch, with_entry=False)
        assert screen._sync_on_open
        monkeypatch.setattr(autosync, "find", lambda *a, **k: {
            "points": [], "readable": False, "share": 0.1, "windows": 4,
            "ambiguous": 0, "usable": 0, "breaks": [], "sections": 1,
            "sections_used": 0, "unreadable": [], "covered": (0.0, 1.0),
            "song_s": 1.0, "tab_s": 1.0, "recording_s": 1.0,
            "length_gap": 0.0, "wrong_length": False})
        screen._start_auto_sync()
        assert not screen._sync_on_open


class TestAnUndecidedSongStartsOnTheBarMap:
    """*"Ich hätte gerne standardmäßig Songsterr map nehmen, wenn noch nichts
    hinterlegt ist. Wenn schon was da ist, dann lassen wir es so."*

    This reverses a default chosen a week earlier, and the reason is that
    the ground moved. The listening is 8-16 ms against the map's 80-92, so
    `auto` produced the better answer -- back when it only ran on Ctrl+S.
    Now it runs BY ITSELF when a song opens, and the comparison is between
    a file read that cannot fail and seconds of FFT at the moment the player
    is reaching for the space bar.

    **And the first shape of it REWROTE the stored source to "songsterr",
    which never falls back.** Eva Under Fire's bar map agrees with 2 of 24
    windows and its listening with 23, so that song stored nothing and the
    measurement that reads it was never asked. `auto` takes the map first
    itself now, so nothing is written and the fallback survives.
    """

    def _screen(self, tmp_path, monkeypatch, cached=True, source=None,
                anchors=None, song_id=0):
        import pygame
        from pickhero.audio.mp3_playback import Mp3Player
        from pickhero.config import Config
        from pickhero.ui.scrolling import PlayingScreen
        pygame.init()
        pygame.display.set_mode((640, 480))
        tab = tmp_path / "song.gp5"
        tab.write_bytes(b"gp")
        (tmp_path / "song.mp3").write_bytes(b"x")
        if cached:
            songsterr.save_cache(tab, 2333598, {"revisionId": 1,
                                                "title": "t"},
                                 [{"id": 1, "videoId": "v", "feature": None,
                                   "points": [0.94, 4.04, 7.11]}])
        config = Config()
        config.set_mp3_path_for("song", str(tmp_path / "song.mp3"))
        if song_id:
            config.set_songsterr_for("song", song_id)
        if source:
            config.set_sync_source_for("song", source)
        if anchors:
            config.set_mp3_anchors_for("song", anchors)
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        monkeypatch.setattr(PlayingScreen, "_start_auto_sync",
                            lambda self: None)
        return PlayingScreen(_song(), config=config, song_key="song",
                             song_path=str(tab))

    def test_a_cached_bar_map_is_what_auto_reaches_for_first(
            self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        screen.update()
        assert screen._sync_source() == "auto"
        assert screen._bar_map_available()

    def test_and_nothing_is_written_behind_the_players_back(self, tmp_path,
                                                            monkeypatch):
        """The rewrite made every undecided song an explicit "Songsterr
        only", which never falls back -- so a song the map cannot place was
        left with no sync at all and the panel said the player had chosen
        it."""
        screen = self._screen(tmp_path, monkeypatch)
        screen.update()
        assert screen._config.song_sync_source == {}
        assert "listen" in " ".join(
            t for t, _ in screen.sync_block_lines())

    def test_a_stored_link_counts_even_with_no_cache_yet(self, tmp_path,
                                                         monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, cached=False,
                              song_id=2333598)
        screen.update()
        assert screen._bar_map_available()

    def test_a_song_with_no_map_at_all_just_listens(self, tmp_path,
                                                     monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, cached=False)
        screen.update()
        assert screen._sync_source() == "auto"
        assert not screen._bar_map_available()

    def test_a_song_that_is_already_lined_up_is_not_touched(self, tmp_path,
                                                            monkeypatch):
        """*"Wenn schon was da ist, dann lassen wir es so."*"""
        screen = self._screen(tmp_path, monkeypatch,
                              anchors=[(0.0, -120.0), (60_000.0, -130.0)])
        screen.update()
        assert screen._sync_source() == "auto"
        assert screen._mp3_anchors() == [(0.0, -120.0), (60_000.0, -130.0)]

    @pytest.mark.parametrize("chosen", ["listen", "songsterr", "hand"])
    def test_a_choice_the_player_made_is_never_overwritten(self, tmp_path,
                                                            monkeypatch,
                                                            chosen):
        screen = self._screen(tmp_path, monkeypatch, source=chosen)
        screen.update()
        assert screen._sync_source() == chosen

    def test_a_correction_set_by_hand_survives(self, tmp_path, monkeypatch):
        """The 70 ms he dialled in with Shift+M is his work, and it sits in
        a different setting from the points. Defaulting the SOURCE must not
        touch it."""
        screen = self._screen(tmp_path, monkeypatch)
        screen._config.set_mp3_offset_for("song", 70.0)
        screen.update()
        assert screen._config.mp3_offset_for("song") == 70.0

    def test_sync_by_hand_still_measures_nothing(self, tmp_path,
                                                 monkeypatch):
        from pickhero.ui.scrolling import PlayingScreen
        runs = []
        screen = self._screen(tmp_path, monkeypatch, source="hand")
        monkeypatch.setattr(PlayingScreen, "_start_auto_sync",
                            lambda self: runs.append(1))
        screen.update()
        assert runs == []


class TestATabCarriedAcrossWithItsBarMap:
    """*"Bei Born to be my baby scheinen alle Syncs zu versagen."*

    Measured on the player's own three files, both measurements WORK and
    agree with each other to about 100 ms across the whole song, and the
    follow loop tracks them to 17 ms with no snaps. Nothing downstream was
    broken. The failure was one step before all of it:

    He copied the tab and its `.songsterr.json` across by hand. No sidecar,
    so no stored song id. `_bar_map_available` said yes -- the CACHE is
    there -- so the new default set the source to "songsterr". Then
    `_start_auto_sync` refused, because it asked a DIFFERENT question: is an
    ID stored. So the measurement never ran, nothing was ever stored, and
    every attempt answered "no link is stored".

    Two questions about the same thing, asked differently in two places.
    """

    def _screen(self, tmp_path, monkeypatch, cached=True, song_id=0):
        import pygame
        from pickhero.audio.mp3_playback import Mp3Player
        from pickhero.config import Config
        from pickhero.ui.scrolling import PlayingScreen
        pygame.init()
        pygame.display.set_mode((640, 480))
        tab = tmp_path / "Bon Jovi - Born To Be My Baby.gp5"
        tab.write_bytes(b"gp")
        (tmp_path / "Bon Jovi - Born To Be My Baby.mp3").write_bytes(b"x")
        if cached:
            songsterr.save_cache(tab, 27278, {"revisionId": 8465060,
                                              "title": "Born To Be My Baby"},
                                 [{"id": 1, "videoId": "T6oyujbaw1E",
                                   "feature": None,
                                   "points": [0.01, 1.83, 3.66]}])
        config = Config()
        config.set_mp3_path_for(tab.stem, str(tmp_path / f"{tab.stem}.mp3"))
        if song_id:
            config.set_songsterr_for(tab.stem, song_id)
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        return PlayingScreen(_song(), config=config, song_key=tab.stem,
                             song_path=str(tab))

    def test_the_song_id_is_taken_out_of_the_bar_map(self, tmp_path,
                                                     monkeypatch):
        """It was written into the cache from the first day and nothing read
        it back, so Ctrl+U existed to type in a number the song was carrying
        all along."""
        screen = self._screen(tmp_path, monkeypatch)
        assert screen._songsterr_id() == 27278
        assert screen._config.songsterr_for(
            "Bon Jovi - Born To Be My Baby") == 27278

    def test_an_id_the_player_has_is_not_replaced(self, tmp_path,
                                                  monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, song_id=999)
        assert screen._songsterr_id() == 999

    def test_the_measurement_actually_runs(self, tmp_path, monkeypatch):
        """It refused outright before: the default had set the source to
        Songsterr and the guard asked for an id that was not stored."""
        from pickhero.ui.scrolling import PlayingScreen
        runs = []
        monkeypatch.setattr(autosync, "find", lambda *a, **k: runs.append(1))
        screen = self._screen(tmp_path, monkeypatch)
        screen.update()
        assert screen._sync_source() == "auto"
        assert screen._bar_map_available()
        assert screen._auto_sync_thread is not None, \
            "the sync refused instead of measuring"
        screen._auto_sync_thread.join(30)

    def test_the_guard_asks_the_same_question_the_work_answers(
            self, tmp_path, monkeypatch):
        """A cache alone is enough to measure, so it must be enough to be
        allowed to try."""
        screen = self._screen(tmp_path, monkeypatch)
        assert screen._bar_map_available()
        monkeypatch.setattr(screen._config, "song_songsterr", {})
        assert screen._bar_map_available(), "the cache alone is a bar map"

    def test_a_song_with_neither_still_says_so(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, cached=False)
        screen._config.set_sync_source_for(screen._song_key, "songsterr")
        screen._start_auto_sync()
        assert screen._auto_sync_thread is None
        assert "no bar map is here" in screen._status_note_text()


class TestAMapThatBeginsBeforeTheRecordingDoes:
    """One bad candidate must not take the other forty-three with it.

    Songsterr offered 44 maps for the player's Californication and six of
    them start before zero, one at -40.25 s. `replace_times` moves every
    note onto the map, `NoteEvent` refuses a negative timestamp, and
    `align_to_bar_times` died on the first such candidate -- before any of
    the 38 good ones had been tried. The song was left with no sync map at
    all, and its source was pinned to Songsterr so nothing fell back to the
    listening either. Read with the bad candidates dropped, the same song
    fits at 48 of 51 windows and 95 ms of scatter.
    """

    def test_it_does_not_take_the_good_ones_with_it(self, monkeypatch):
        negative = [t - 40.25 for t in REAL[0]["points"]]
        report = TestFittingItToARecording()._aligned(
            monkeypatch, [1.2, 1.25, 1.18, 1.22, 1.21],
            times=[negative, REAL[0]["points"]])
        assert report["readable"]
        assert report["points"]

    def test_the_real_reply_carries_one(self, monkeypatch):
        """REAL[1] is a genuine Songsterr entry and it starts at -24.21 s."""
        assert min(REAL[1]["points"]) < 0
        report = TestFittingItToARecording()._aligned(
            monkeypatch, [1.2, 1.25, 1.18, 1.22, 1.21],
            times=[REAL[1]["points"]])
        assert report["readable"]

    def test_a_map_that_starts_at_zero_is_passed_through_untouched(self):
        """The control: every candidate that worked before is bit-identical."""
        assert autosync._from_zero(REAL[0]["points"]) == REAL[0]["points"]
        assert autosync._from_zero([]) == []

    def test_moving_the_whole_map_moves_the_constant_and_nothing_else(self):
        """Why this is a normalisation and not a repair.

        The fit looks for ONE constant between the map's clock and this
        recording's, so shifting every bar by S moves that constant by -S
        and `start - (time + constant)` is unchanged to the millisecond.
        Asserted through the real `_fit_one` with the lags moved by the same
        S, which is what the measurement would really report.
        """
        starts = [i * 3077.0 for i in range(8)]
        times = REAL[0]["points"]
        rows = [(float(i * 6), 1.2, 0.9) for i in range(6)]
        base = {"points": [], "windows": 0, "usable": 0, "share": 0.0}

        moved = autosync._from_zero([t - 40.25 for t in times])
        assert min(moved) >= 0.0
        # Whatever shift the normalisation chose, not one written down here:
        # the map moved by `net` seconds, so the recording is found `net`
        # seconds earlier against it.
        net = moved[0] - times[0]
        shifted_rows = [(at, lag - net, margin) for at, lag, margin in rows]

        here = autosync._fit_one(base, starts, times, rows, 25.0)
        there = autosync._fit_one(base, starts, moved, shifted_rows, 25.0)
        assert here["points"] == there["points"]


class TestOneShapeIsOneCandidate:
    """Songsterr offered 44 maps for one song and 34 are one curve shifted.

    The fit's whole job is to find one constant, so a map differing from
    another only by a constant is the same answer written twice -- and each
    one costs a full drift curve over the song. Measured on the player's
    Californication: 44 candidates to 11, and the run from 2.6 minutes to
    42 seconds. *"SYNC comparing bei 50 % bleibt haengen."*
    """

    def test_shifted_copies_are_one(self):
        base = REAL[0]["points"]
        shifted = [[t + d for t in base] for d in (0.0, -25.15, 7.5, 40.0)]
        assert len(autosync._distinct(shifted)) == 1

    def test_a_different_curve_survives(self):
        a = REAL[0]["points"]
        b = [t * 1.01 for t in a]          # a different shape, not a shift
        assert len(autosync._distinct([a, [x + 9 for x in a], b])) == 2

    def test_the_first_of_a_set_wins(self):
        """`candidates_for` puts the video Songsterr marked first, and that
        ordering has to survive the thinning."""
        base = REAL[0]["points"]
        out = autosync._distinct([[t + 3.0 for t in base], base])
        assert out[0] == [t + 3.0 for t in base]

    def test_it_is_really_wired_into_the_fit(self, monkeypatch):
        """A rule nothing calls is a rule that ships doing nothing."""
        import numpy as np
        seen = []
        monkeypatch.setattr(autosync, "decode",
                            lambda p, samplerate=44100: (np.zeros(44100), 44100))
        monkeypatch.setattr(autosync, "chroma_of_audio",
                            lambda s, r, progress=None: (np.zeros((100, 12)), 21.5))
        monkeypatch.setattr(autosync, "chroma_of_timeline",
                            lambda tl, fps: np.zeros((100, 12)))
        monkeypatch.setattr(autosync, "drift_curve",
                            lambda *a, **k: seen.append(1) or [])
        base = REAL[0]["points"]
        autosync.align_to_bar_times(
            _song(bars=8), "audio.mp3",
            [[t + d for t in base] for d in (0.0, 1.0, 2.0, 3.0)])
        assert len(seen) == 1, "four shifts of one map cost four drift curves"


class TestTheSameMapWrittenDownOnTwoDecimals:
    """*"Es haengt wieder bei comparing 51% fuer 5 Minuten."*

    `_distinct` compared shapes to the MILLISECOND, and Songsterr writes its
    points to two decimals -- so its own re-uploads of one transcription,
    which differ from each other by exactly one unit of that 10 ms grid, each
    counted as a fresh answer worth a full drift curve. On the player's
    "Can't Stop": 56 offered, 32 distinct to the millisecond, **28 of them
    within 10 ms of each other**, 75 s of arithmetic here and two to four
    times that on his laptop. Within `SAME_MAP_MS` the same song gives 5.
    """

    def _grid(self, base, jitter):
        """The same map re-rounded onto Songsterr's own 2-decimal grid."""
        return [round(t + j, 2) for t, j in zip(base, jitter)]

    def test_one_unit_of_songsterrs_grid_is_not_a_new_map(self):
        base = REAL[0]["points"]
        nudged = [self._grid(base, [0.01 if i % 3 else -0.01
                                    for i in range(len(base))])
                  for _ in range(4)]
        assert len(autosync._distinct([base] + nudged)) == 1

    def test_a_map_that_really_differs_survives(self):
        """The four 102-point maps in his file sit up to 940 ms apart."""
        base = REAL[0]["points"]
        apart = list(base)
        apart[3] += 0.94
        assert len(autosync._distinct([base, apart])) == 2

    def test_one_point_over_the_line_is_enough(self):
        """It is the WORST disagreement that decides, not the average."""
        base = REAL[0]["points"]
        under = list(base)
        under[4] += autosync.SAME_MAP_MS / 1000.0 * 0.9
        over = list(base)
        over[4] += autosync.SAME_MAP_MS / 1000.0 * 1.1
        assert len(autosync._distinct([base, under])) == 1
        assert len(autosync._distinct([base, over])) == 2

    def test_a_different_number_of_bars_is_never_the_same_map(self):
        """A map of another bar count is a map of another tab, whatever its
        bar lines say -- and `_covers` decides that, not this."""
        base = REAL[0]["points"]
        assert len(autosync._distinct([base, list(base[:-1])])) == 2

    def test_everything_dropped_is_within_the_tolerance_of_something_kept(self):
        """The safety argument, asserted rather than reasoned.

        First-wins clustering is not an equivalence relation -- A and C can
        each sit within the tolerance of B and further from each other. What
        it does guarantee is this, and this is what makes dropping safe: no
        candidate is thrown away that any player could tell from one still
        being tried.
        """
        base = REAL[0]["points"]
        # Scaled, not shifted: a shift IS the same shape, so a fixture of
        # shifts would collapse to one and prove nothing. Over this map's
        # 21.68 s these are ~43 ms apart per step, so neighbours merge and
        # distant ones do not -- the case the guarantee is about.
        offered = []
        for i in range(24):
            offered.append([round(t * (1.0 + i * 0.002)
                                  + (i % 5 - 2) * 0.01, 2) for t in base])
        kept = autosync._distinct(offered)
        assert 1 < len(kept) < len(offered)
        for c in offered:
            shape = [t - c[0] for t in c]
            assert any(autosync._same_map(shape, [t - k[0] for t in k])
                       for k in kept), c

    def test_the_shifts_it_already_merged_are_still_merged(self):
        """The control for the change: nothing that worked before moves."""
        base = REAL[0]["points"]
        shifted = [[t + d for t in base] for d in (0.0, -25.15, 7.5, 40.0)]
        assert len(autosync._distinct(shifted)) == 1


class TestTheProgressBarMovesThroughAllOfThem:
    """`0.5 + 0.5 * (taken + 1) / n * f` restarts at a half for every
    candidate and reaches 51 % on the first of 44, so a measurement doing
    exactly what it should reads as a freeze at 50 %."""

    def _run(self, monkeypatch, maps):
        rows = [(float(i * 6), 1.2, 0.9) for i in range(5)]
        import numpy as np
        monkeypatch.setattr(autosync, "decode",
                            lambda p, samplerate=44100: (np.zeros(44100), 44100))
        monkeypatch.setattr(autosync, "chroma_of_audio",
                            lambda s, r, progress=None: (np.zeros((100, 12)), 21.5))
        monkeypatch.setattr(autosync, "chroma_of_timeline",
                            lambda tl, fps: np.zeros((100, 12)))

        def curve(tab, rec, fps, progress=None):
            if progress:
                for k in range(1, 5):
                    progress(k / 4)
            return rows
        monkeypatch.setattr(autosync, "drift_curve", curve)
        seen = []
        autosync.align_to_bar_times(
            _song(bars=8), "audio.mp3", maps,
            lambda f, what: seen.append((f, what)) or True)
        return seen

    def _maps(self, n):
        """n maps that `_distinct` really keeps, and it is asserted here.

        The step was 0.003, which over this fixture's 21.68 s span is 65 ms
        -- under `SAME_MAP_MS`, so every one of them was the same map and
        this class stopped testing what it says once `_distinct` grew a
        tolerance. A fixture that quietly stops producing its own case is
        the fault this file writes chapters about, so the guard is in the
        fixture rather than in somebody's memory.
        """
        base = REAL[0]["points"]
        maps = [[t * (1.0 + i * 0.03) for t in base] for i in range(n)]
        assert len(autosync._distinct(maps)) == n, "the maps are not distinct"
        return maps

    def test_it_never_walks_backwards(self, monkeypatch):
        seen = self._run(monkeypatch, self._maps(6))
        bars = [f for f, _ in seen]
        assert bars == sorted(bars), "the percentage went back down"

    def test_it_reaches_the_end(self, monkeypatch):
        seen = self._run(monkeypatch, self._maps(6))
        assert max(f for f, _ in seen) == pytest.approx(1.0)

    def test_it_leaves_the_half_on_the_first_candidate(self, monkeypatch):
        """The whole complaint: 44 candidates put the first one's finish at
        51 %, and then the next began again at 50."""
        seen = self._run(monkeypatch, self._maps(20))
        comparing = [f for f, what in seen if what.startswith("comparing")]
        assert max(comparing[:4]) > 0.5

    def test_it_says_which_map(self, monkeypatch):
        seen = self._run(monkeypatch, self._maps(3))
        words = {what for _, what in seen if what.startswith("comparing")}
        assert "comparing map 1 of 3" in words
        assert "comparing map 3 of 3" in words


class TestABarLineNoDriftCouldPutThere:
    """Songsterr's bar 0 for Papa Roach's "Reckless" is the VIDEO's start,
    not the first bar line: that bar reads 1834 ms where every other bar of
    the song reads about 2970. `SyncMap` clamps the segment at `MAX_RATE`
    and spreads the rest over the music -- the `+11.11 %` first section in
    the player's run log, and the 628 ms the picture was pulled by.
    """

    def _map(self, bars=20, bar_ms=2926.8, first=None):
        """A map drifting smoothly at 1 %, with one point movable."""
        pts = [(i * bar_ms, i * bar_ms * 0.01) for i in range(bars)]
        if first is not None:
            pts[0] = (0.0, first)
        return pts

    def test_a_smooth_map_loses_nothing(self):
        pts = self._map()
        assert autosync._without_spikes(pts) == pts

    def test_the_video_start_goes(self):
        pts = self._map(first=-1091.0)
        kept = autosync._without_spikes(pts)
        assert len(kept) == len(pts) - 1
        assert kept[0][0] == pytest.approx(2926.8)

    def test_only_the_bad_one_goes(self):
        """One bad point drags the line for the two beside it, so bar 1
        also fails while bar 0 is still in -- and passes the moment it is
        gone. Worst first, then measured again."""
        pts = self._map(first=-1091.0)
        kept = autosync._without_spikes(pts)
        assert [p[0] for p in kept] == [p[0] for p in pts[1:]]

    def test_a_spike_in_the_middle_goes_too(self):
        pts = self._map()
        pts[7] = (pts[7][0], pts[7][1] + 900.0)
        kept = autosync._without_spikes(pts)
        assert len(kept) == len(pts) - 1
        assert all(p[0] != pts[7][0] for p in kept)

    def test_a_disagreement_nobody_could_see_is_kept(self):
        """Floored at SPIKE_FLOOR_S, so a reading inside the 100 ms where
        picture and sound still read as one event is never refused."""
        pts = self._map()
        pts[7] = (pts[7][0], pts[7][1] + 80.0)
        assert autosync._without_spikes(pts) == pts

    def test_it_never_eats_a_short_map(self):
        pts = [(0.0, 0.0), (1000.0, 900.0), (2000.0, 0.0)]
        assert len(autosync._without_spikes(pts)) == 3

    def test_the_fit_counts_what_it_dropped(self, monkeypatch):
        report = TestFittingItToARecording()._aligned(
            monkeypatch, [1.2, 1.25, 1.18, 1.22, 1.21])
        assert "spikes" in report


class TestTheMapsOwnWindowsRefineIt:
    """`drift_curve` has already run on the map-warped tab inside the fit.

    `_fit_one` took the MEDIAN of its lags and threw the rest away -- and
    the rest is the map's own error, window by window, already measured.
    Scored on windows the curve was NOT fitted to (every other one):

        Parkway Drive   91 ms median / 200 ms p90  ->   4 ms /  9 ms
        Eva Under Fire  88 / 148                   ->   8 / 16
        Dry County     183 / 345                   ->  12 / 50

    which closes the 80-92 ms against 8-16 ms this project records for
    Songsterr's map against the listening, out of numbers that were already
    there. The follow loop is unmoved (237 -> 237 ms worst on Parkway) and
    the spike filter drops exactly as many points either way (17 of 149 on
    Dry County), so neither is paying for it.
    """

    BARS = [i * 2000.0 for i in range(40)]
    TIMES = [i * 2.0 for i in range(40)]

    def _rows(self, lag_of):
        """One window every 6 s with a healthy margin, as drift_curve emits
        them: `at` is the window START in the warped tab's own time."""
        return [(float(i * 6), lag_of(i * 6.0), 0.9) for i in range(13)]

    def _fit(self, rows):
        base = {"points": [], "windows": 0, "usable": 0, "share": 0.0,
                "refined": 0, "spikes": 0}
        return autosync._fit_one(base, self.BARS, self.TIMES, rows, 25.0)

    def _offset_at(self, report, song_ms):
        from pickhero.audio.syncmap import SyncMap
        return SyncMap(report["points"]).offset_at(song_ms)

    def test_a_constant_error_is_still_one_constant(self):
        """The control: where every window agrees, the curve is flat and the
        answer is bit-for-bit what the median gave."""
        rows = self._rows(lambda at: 1.5)
        report = self._fit(rows)
        assert report["refined"] == len(rows)
        for song_ms in (0.0, 40_000.0, 78_000.0):
            assert self._offset_at(report, song_ms) == pytest.approx(-1500.0)

    def test_a_sloped_error_is_followed(self):
        """A map whose own windows say it drifts: 0 at the start and 500 ms
        by 1:12, which one constant can only split the difference on."""
        rows = self._rows(lambda at: at / 144.0)       # 0.5 s over 72 s
        report = self._fit(rows)
        assert report["refined"] == len(rows)
        # Each window's reading, reproduced where the window sits. The
        # window's MIDDLE, because the lag was measured over all 20 s of it.
        for at, lag, _ in rows:
            song_ms = (at + autosync.WINDOW_S / 2.0) * 1000.0
            assert self._offset_at(report, song_ms) == pytest.approx(
                -lag * 1000.0, abs=15.0)

    def test_and_one_constant_could_not_have(self):
        """The control for the control: the old answer is off by a quarter
        of a second at the ends, which is over the 100 ms where picture and
        sound stop reading as one event."""
        rows = self._rows(lambda at: at / 144.0)
        median = sorted(lag for _, lag, _ in rows)[len(rows) // 2]
        worst = max(abs(lag - median) for _, lag, _ in rows) * 1000.0
        assert worst > 200.0

    def test_a_window_past_a_break_does_not_refine_it(self):
        """`BAR_MAP_AGREE_S` ties a window to THIS map's constant. A ten
        second residual is the two files parting company, and letting it in
        would warp the map rather than correct it."""
        rows = self._rows(lambda at: 1.5 + (10.0 if at >= 48 else 0.0))
        report = self._fit(rows)
        assert report["refined"] == 8, "a break was read as a correction"
        assert self._offset_at(report, 70_000.0) == pytest.approx(-1500.0)

    def test_an_ambiguous_window_does_not_refine_it_either(self):
        """The margin says the window could not tell one chorus from
        another, and a reading that cannot is not evidence about anything."""
        rows = [(at, lag, 0.9 if i % 2 else 0.0)
                for i, (at, lag, _) in enumerate(self._rows(lambda at: 1.5))]
        assert self._fit(rows)["refined"] == 6

    def test_the_residual_is_held_outside_the_windows_that_measured_it(self):
        """Not extrapolated, for the reason `SyncMap.correction_at` is not:
        a slope fitted to a residual says nothing about the stretch nobody
        could read, and the map underneath carries the drift there."""
        xs, ys = [10.0, 20.0], [0.1, 0.3]
        assert autosync.residual_at(0.0, xs, ys) == pytest.approx(0.1)
        assert autosync.residual_at(15.0, xs, ys) == pytest.approx(0.2)
        assert autosync.residual_at(900.0, xs, ys) == pytest.approx(0.3)
        assert autosync.residual_at(5.0, [], []) == 0.0
