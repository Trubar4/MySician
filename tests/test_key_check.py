"""Is this stretch of the tab in the recording's KEY?

"Das ist der einzige Song bei dem der Anfang 7-7-6-0 komplett schraeg klingt,
also meine Gitarre um einen Viertelton anders als das MP3 ist." The
observation was exact and the diagnosis was not: his guitar reads +1.0 cents
over 5578 confident readings and the recording +2.1, where a quarter tone is
fifty -- and the first ten bars of that tab are a WHOLE TONE low. Nothing in
the app compared the two keys, so it took a session by hand, which is the
third tab/recording conflict measured that way after Thunder's length and Dry
County's bars.

The arithmetic was already there: both chroma curves are computed for the lag
search, and a key difference is a rotation of the twelve pitch classes.
"""

import numpy as np
import pytest

from pickhero.audio import autosync
from pickhero.config import Config
from pickhero.ui.scrolling import PlayingScreen

FPS = 20.0
WIDTH = int(autosync.KEY_WINDOW_S * FPS)


def _chroma(classes, frames, fps=FPS):
    """A chroma curve that walks a list of pitch classes, one per second."""
    out = np.zeros((frames, 12))
    for i in range(frames):
        out[i, classes[int(i / fps) % len(classes)]] = 1.0
        out[i, (classes[int(i / fps) % len(classes)] + 7) % 12] = 0.4
    return out


# A melody that is NOT rotation-symmetric: a run of pitch classes whose
# intervals do not repeat round the circle, or every rotation fits equally
# well and the test would pass on anything.
TUNE = [0, 4, 7, 11, 2, 5, 9, 1, 6, 10, 3, 8]


class TestItFindsAStretchInAnotherKey:
    def _pair(self, shift, seconds=60, where=slice(0, 24)):
        """(tab, rec) where the tab's `where` seconds are `shift` flat."""
        frames = int(seconds * FPS)
        rec = _chroma(TUNE, frames)
        tab = rec.copy()
        band = slice(int(where.start * FPS), int(where.stop * FPS))
        tab[band] = np.roll(rec[band], -shift, axis=1)
        return tab, rec

    def _rows(self, seconds=60):
        """A map saying the two are in step, which is what the fixture is."""
        return [(float(t), 0.0, 0.05)
                for t in range(0, seconds - int(autosync.WINDOW_S), 6)]

    def test_a_whole_tone_low_is_found_and_named(self):
        tab, rec = self._pair(2)
        gaps = autosync.key_check(tab, rec, FPS, self._rows())
        assert len(gaps) == 1
        assert gaps[0].semitones == 2
        assert gaps[0].from_s == pytest.approx(0.0, abs=0.1)

    def test_the_sign_says_which_way_to_move_the_tab(self):
        """+2 means the tab sits a tone UNDER the recording, so moving it up
        by two fixes it -- which is what the player actually did."""
        low, rec = self._pair(2)
        high, _ = self._pair(-2)
        assert autosync.key_check(low, rec, FPS, self._rows())[0].semitones == 2
        assert autosync.key_check(high, rec, FPS,
                                  self._rows())[0].semitones == -2

    def test_it_is_spelled_the_short_way_round_the_circle(self):
        """Eleven semitones up is a semitone down, and nobody transposes a
        tab by eleven."""
        tab, rec = self._pair(11)
        assert autosync.key_check(tab, rec, FPS,
                                  self._rows())[0].semitones == -1

    def test_a_tab_in_the_same_key_says_nothing(self):
        """The control, and it is the whole of the argument: the same
        fixture with nothing moved."""
        frames = int(60 * FPS)
        rec = _chroma(TUNE, frames)
        assert autosync.key_check(rec.copy(), rec, FPS, self._rows()) == []

    def test_the_stretch_is_where_it_was_READ_not_where_it_runs(self):
        """A run of windows covers from the first window's start to the
        last window's end, and that is all it claims."""
        tab, rec = self._pair(2, where=slice(0, 30))
        gap = autosync.key_check(tab, rec, FPS, self._rows())[0]
        assert gap.to_s <= 30 + autosync.KEY_WINDOW_S
        assert gap.windows >= autosync.KEY_MIN_WINDOWS


class TestWhatItRefuses:
    def _rows(self, seconds=60):
        return [(float(t), 0.0, 0.05)
                for t in range(0, seconds - int(autosync.WINDOW_S), 6)]

    def test_one_window_is_the_song_rhyming_with_itself(self):
        """Godsmack has a PAIR of windows at +7 whose margin is 0.085 --
        above anything the real fault produces -- in a five-minute song. So
        the margin alone cannot be the filter, and a run is needed."""
        frames = int(60 * FPS)
        rec = _chroma(TUNE, frames)
        tab = rec.copy()
        band = slice(0, int(autosync.KEY_WINDOW_S * FPS))
        tab[band] = np.roll(rec[band], -2, axis=1)
        assert autosync.key_check(tab, rec, FPS, self._rows()) == []

    def test_a_run_of_weak_readings_is_the_chroma_saying_nothing(self):
        """And Godsmack also has FIVE consecutive windows agreeing on +4 at
        a margin of 0.006, so the run alone cannot be the filter either.
        Noise with no key in it must come back empty."""
        rng = np.random.default_rng(4)
        frames = int(60 * FPS)
        rec = rng.random((frames, 12))
        tab = rng.random((frames, 12))
        gaps = autosync.key_check(tab, rec, FPS, self._rows())
        assert all(g.margin >= autosync.KEY_MARGIN for g in gaps)

    def test_both_filters_are_load_bearing(self):
        """Neither is a tightening of the other: measured on the player's
        own songs, one rejects a two-window pair at 0.085 and the other a
        five-window run at 0.006."""
        assert autosync.KEY_MIN_WINDOWS >= 2
        assert 0.0 < autosync.KEY_MARGIN < 0.1

    def test_no_map_is_no_question(self):
        """"Could not read it" and "needs no correction" are different
        answers everywhere else here. With no usable window there is no
        place to ask about, so nothing is claimed."""
        tab, rec = _chroma(TUNE, int(60 * FPS)), _chroma(TUNE, int(60 * FPS))
        assert autosync.key_check(tab, np.roll(rec, 3, axis=1), FPS, []) == []

    def test_a_recording_shorter_than_one_window_is_not_a_reading(self):
        short = _chroma(TUNE, WIDTH - 5)
        tab = _chroma(TUNE, int(60 * FPS))
        assert autosync.key_check(tab, short, FPS, self._rows()) == []


class TestTheWindowIsShorterThanTheLagSearch:
    def test_and_the_reason_is_the_bar_line_the_key_changes_at(self):
        """A twenty-second window straddles it. Measured on the fault this
        was built for, the reading goes from one window at 12 s -- which
        only the margin passes -- to a run of three at 10 s."""
        assert autosync.KEY_WINDOW_S < autosync.WINDOW_S
        assert autosync.KEY_STEP_S < autosync.KEY_WINDOW_S

    def test_the_lag_is_not_searched_freely(self):
        """The question is "at the place the map puts it", and a free search
        measures worse: the fault's margin falls from 0.154 to 0.065 because
        rotation 0 finds a spurious match of its own elsewhere."""
        assert autosync.KEY_LAG_S < autosync.MAX_LAG_S


class TestItTravelsThroughFindAndTheReport:
    def test_find_carries_the_gaps(self, monkeypatch):
        rows = [(float(t), 0.0, 0.05) for t in range(0, 240, 6)]
        gap = autosync.KeyGap(0.0, 20.0, 2, 0.07, 3)
        monkeypatch.setattr(
            autosync, "measure",
            lambda tab, audio, progress=None, tolerance_ms=25.0: (
                autosync.points_from_rows(rows), rows, (240.0, 240.1), [gap]))
        assert autosync.find("tab", "audio")["key_gaps"] == [gap]

    def test_measure_really_asks(self, monkeypatch, tmp_path):
        """A measurement nothing calls is a feature that ships doing
        nothing, which this project has shipped four times."""
        asked = []
        tab, rec = _chroma(TUNE, 400), _chroma(TUNE, 400)
        monkeypatch.setattr(autosync, "decode",
                            lambda path, samplerate=44100:
                            (np.zeros(44100), 44100))
        monkeypatch.setattr(autosync, "chroma_of_audio",
                            lambda s, r, progress=None: (rec, FPS))
        monkeypatch.setattr(autosync, "chroma_of_tab_file",
                            lambda path, fps: tab)
        monkeypatch.setattr(autosync, "drift_curve",
                            lambda *a, **k: [(0.0, 0.0, 0.05)])
        monkeypatch.setattr(autosync, "key_check",
                            lambda *a, **k: asked.append(a) or [])
        autosync.measure("tab", "audio")
        assert asked, "measure() never asked about the key"


class TestWhatThePanelSays:
    def _screen(self, tmp_path, monkeypatch, bar_ms=3000.0, bars=40):
        from pickhero.tabs.timeline import (MeasureInfo, NoteEvent,
                                            SongMetadata, Timeline)
        song = tmp_path / "backing.mp3"
        song.write_bytes(b"x")
        config = Config()
        config.set_mp3_path_for("song", str(song))
        notes = [NoteEvent(timestamp_ms=i * bar_ms, duration_ms=400.0,
                           midi_note=40, string=6, fret=3, measure=i)
                 for i in range(bars)]
        measures = [MeasureInfo(index=i, start_ms=i * bar_ms,
                                end_ms=(i + 1) * bar_ms) for i in range(bars)]
        timeline = Timeline(notes, SongMetadata(title="t", tempo=120),
                            measures=measures)
        return PlayingScreen(timeline, config=config, song_key="song")

    def _report(self, gaps):
        return {
            "points": [(0.0, 0.0), (100.0, 10.0)], "readable": True,
            "share": 0.9, "windows": 40, "ambiguous": 1, "usable": 36,
            "breaks": [], "sections": 1, "sections_used": 1,
            "unreadable": [], "covered": (0.0, 240.0), "song_s": 240.0,
            "tab_s": 240.0, "recording_s": 240.0, "length_gap": 0.0,
            "wrong_length": False, "key_gaps": gaps,
        }

    def test_it_says_the_bars_and_the_semitones(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        gap = autosync.KeyGap(0.0, 20.8, 2, 0.067, 3)
        lines = " ".join(screen._auto_sync_report_lines(
            self._report([gap])["points"], self._report([gap])))
        assert "2 semitones under the recording" in lines
        assert "bars 1-7" in lines, lines

    def test_it_says_which_way(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        gap = autosync.KeyGap(0.0, 20.8, -1, 0.067, 3)
        lines = " ".join(screen._auto_sync_report_lines(
            self._report([gap])["points"], self._report([gap])))
        assert "1 semitone over the recording" in lines

    def test_it_says_the_stretch_may_run_further(self, tmp_path, monkeypatch):
        """On his own song the run reaches 0:21 and the fault runs to bar 10
        at 0:31. Claiming an end the measurement does not have would send
        him to transpose seven bars of ten."""
        screen = self._screen(tmp_path, monkeypatch)
        gap = autosync.KeyGap(0.0, 20.8, 2, 0.067, 3)
        lines = " ".join(screen._auto_sync_report_lines(
            self._report([gap])["points"], self._report([gap])))
        assert "may run further" in lines

    def test_a_song_in_one_key_says_nothing_about_keys(self, tmp_path,
                                                       monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        lines = " ".join(screen._auto_sync_report_lines(
            self._report([])["points"], self._report([])))
        assert "semitone" not in lines

    def test_the_bar_map_path_says_it_too(self, tmp_path, monkeypatch):
        """The bar-map path is the one that RUNS on the song this was built
        for -- `auto` tries the map first and it fits -- so a line only on
        the listening path would never have been seen."""
        screen = self._screen(tmp_path, monkeypatch)
        report = self._report([autosync.KeyGap(0.0, 20.8, 2, 0.067, 3)])
        report.update(source="songsterr", bars=98, measures=98,
                      constant_s=-1.3, scatter_ms=73.0, spikes=0, refined=25,
                      offered=[98], wrong_bars=False)
        lines = " ".join(screen._auto_sync_report_lines(report["points"],
                                                        report))
        assert "2 semitones under the recording" in lines

    def test_it_reaches_the_HUD_as_well_as_the_panel(self, tmp_path,
                                                     monkeypatch):
        """`_sync_lines` is drawn by `S` and by nothing else. A player who
        never presses it would never be told -- the fifth time in this
        project that a feature could not be seen working."""
        screen = self._screen(tmp_path, monkeypatch)
        gap = autosync.KeyGap(0.0, 20.8, 2, 0.067, 3)
        screen._say_break(self._report([gap]))
        assert "semitones under the recording" in screen._status_note_text()

    def test_the_key_gap_outranks_a_break(self, tmp_path, monkeypatch):
        """It is the more specific finding and the more actionable one: it
        names which bars to transpose and by how much."""
        screen = self._screen(tmp_path, monkeypatch)
        report = self._report([autosync.KeyGap(0.0, 20.8, 2, 0.067, 3)])
        report["breaks"] = [autosync.Break(at_s=100.0, before_s=94.0,
                                           jump_s=-12.0, bars=4.0, bar_s=3.0)]
        screen._say_break(report)
        assert "semitones" in screen._status_note_text()


class TestOneDissentingWindowIsNotTwoFaults:
    """Measured on Kid Rock with an injected tone: the window at 99.9 s
    reads a fourth while the eight around it read the tone, so the run
    breaks and a forty-second error came back as 84-104 s and 104-134 s.
    Two lines about one fault is a panel saying the wrong shape of thing.
    """

    def test_stretches_that_meet_are_one_stretch(self):
        joined = autosync._joined([autosync.KeyGap(84.0, 104.0, -2, 0.13, 3),
                                   autosync.KeyGap(104.0, 134.0, -2, 0.23, 6)])
        assert len(joined) == 1
        assert joined[0].from_s == 84.0 and joined[0].to_s == 134.0
        assert joined[0].windows == 9
        assert joined[0].margin == 0.23, "the stronger reading of the two"

    def test_a_real_hole_is_left_as_two(self):
        """Exactly one window is bridged, and that falls out of the
        arithmetic: the step is half the window, so two dissenters leave a
        gap no run can span."""
        pair = [autosync.KeyGap(0.0, 20.0, 2, 0.1, 3),
                autosync.KeyGap(30.0, 50.0, 2, 0.1, 3)]
        assert autosync._joined(pair) == pair

    def test_a_different_interval_is_a_different_fault(self):
        pair = [autosync.KeyGap(0.0, 20.0, 2, 0.1, 3),
                autosync.KeyGap(20.0, 40.0, -1, 0.1, 3)]
        assert autosync._joined(pair) == pair

    def test_the_step_really_is_half_the_window(self):
        """Which is what makes "one window" the bridge rather than a
        constant somebody chose."""
        assert autosync.KEY_STEP_S * 2 == autosync.KEY_WINDOW_S
