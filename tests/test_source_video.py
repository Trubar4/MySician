"""Which recording Songsterr's bar map was timed against.

*"Wie kriegen wir das Syncen besser hin? Es ist mir ein Raetsel wieso die
Songs bei Songsterr mit MP3 Background nicht das gleiche Problem haben."*

Because their player plays the video their map was made FOR. Their
`video-points` are timestamps into one particular YouTube upload, so there
is no constant to find and no missing section to bridge; a player's own MP3
is a different file with a different intro trim.

The id of that video has been in the map cached beside the tab since the
download screen first wrote one, `main_entry`'s own docstring calls it
*"the video to take"* -- and `video_id_of` was read once inside the fit and
shown to nobody. So the cheapest sync fix a song can have was unreadable.

Dry County is the case that made it worth saying: 27 of its 30 maps carry
233 points and the one marked `feature: null` carries **241, exactly the
tab's bar count**, so the tab is right and the player's recording is one of
the rips missing eight bars of the instrumental. No algorithm can recover
music that is not in the file; the matching audio makes it a non-question.
"""

import json

import pytest

from pickhero.config import Config
from pickhero.tabs import songsterr
from pickhero.tabs.timeline import MeasureInfo, NoteEvent, SongMetadata, Timeline
from pickhero.ui.scrolling import PlayingScreen

MAIN = "Q_UWZg1d6BU"
BACKING = "uz6lUArsTew"


def entry(video, feature, points):
    return {"videoId": video, "feature": feature,
            "points": [float(i) * 2.0 for i in range(points)]}


#: The shape of the player's own Dry County reply: one marked main recording
#: at the tab's own bar count, and a crowd of shorter backing tracks.
DRY_COUNTY = ([entry(BACKING, "backing", 233)]
              + [entry(f"alt{i}", "alternative", 233) for i in range(5)]
              + [entry(MAIN, None, 241)])


def write_cache(folder, entries, name="Bon Jovi - Dry County"):
    tab = folder / f"{name}.gp5"
    tab.write_bytes(b"not a real tab, nothing here reads it")
    songsterr.save_cache(tab, 12345, {"revisionId": 1, "title": "t",
                                      "artist": "a"}, entries)
    return tab


def timeline(bars=241):
    notes = [NoteEvent(timestamp_ms=i * 1000.0, midi_note=40, string=6,
                       fret=0, duration_ms=500.0) for i in range(bars)]
    measures = [MeasureInfo(index=i, start_ms=i * 1000.0,
                            end_ms=(i + 1) * 1000.0, beats=4, beat_type=4)
                for i in range(bars)]
    return Timeline(notes, SongMetadata(title="t", tempo=120,
                                        tuning={s: 40 for s in range(1, 7)}),
                    measures=measures)


# ── what the line says ─────────────────────────────────────────────────────

class TestTheLineNamesTheVideo:

    def test_it_names_the_marked_main_recording_not_the_majority(self):
        """27 maps say 233 and the marked one says 241. The marked one wins.

        Reading the majority as the truth is the mistake this measurement
        made first -- it reported the tab as the odd one out when the tab
        agrees with Songsterr's own recording exactly.
        """
        line = songsterr.source_video_line(DRY_COUNTY, 241)
        assert MAIN in line
        assert BACKING not in line
        assert "241" in line

    def test_a_count_that_matches_the_tab_says_the_audio_needs_no_sync(self):
        line = songsterr.source_video_line(DRY_COUNTY, 241)
        assert "same as this tab" in line

    def test_a_count_that_differs_prints_BOTH_and_draws_no_verdict(self):
        """`_covers` owns that decision -- it accepts a shorter map where
        every bar past it is empty, so a line calling 233-against-241 a
        mismatch would contradict the fit beside it."""
        line = songsterr.source_video_line(DRY_COUNTY, 200)
        assert "241" in line and "200" in line
        for word in ("mismatch", "wrong", "refused", "same as this tab"):
            assert word not in line

    def test_with_no_marked_recording_it_says_the_entry_is_not_one(self):
        """`main_entry` falls back to the first usable timeline, and a
        backing track is a different performance -- the What's Up lesson."""
        line = songsterr.source_video_line(
            [e for e in DRY_COUNTY if e.get("feature")], 241)
        assert BACKING in line
        assert "backing" in line
        assert "same as this tab" not in line

    @pytest.mark.parametrize("entries", [None, [], [{}],
                                         [{"videoId": "x", "points": [1.0]}],
                                         [{"videoId": "", "points": [1.0, 2.0]}]])
    def test_nothing_to_act_on_says_nothing(self, entries):
        assert songsterr.source_video_line(entries, 241) == ""


class TestItReadsTheMapBesideTheTab:

    def test_a_cached_map_is_found_by_the_tab_path(self, tmp_path):
        tab = write_cache(tmp_path, DRY_COUNTY)
        assert MAIN in songsterr.source_video_line_for(tab, 241)

    def test_a_tab_with_no_map_says_nothing(self, tmp_path):
        tab = tmp_path / "lonely.gp5"
        tab.write_bytes(b"x")
        assert songsterr.source_video_line_for(tab, 241) == ""

    def test_an_unreadable_map_says_nothing_rather_than_raising(self, tmp_path):
        tab = write_cache(tmp_path, DRY_COUNTY)
        songsterr.cache_path(tab).write_text("{ not json", encoding="utf-8")
        assert songsterr.source_video_line_for(tab, 241) == ""


# ── and the panel really shows it ──────────────────────────────────────────

class TestTheSyncPanelShowsIt:

    def _screen(self, tmp_path, entries=DRY_COUNTY, bars=241):
        tab = write_cache(tmp_path, entries)
        return PlayingScreen(timeline(bars), config=Config(),
                             song_key=tab.stem, song_path=str(tab))

    def test_the_video_is_on_the_sync_panel(self, tmp_path):
        screen = self._screen(tmp_path)
        assert MAIN in screen._source_video_line()

    def test_a_song_with_no_map_puts_no_line_there(self, tmp_path):
        tab = tmp_path / "no map.gp5"
        tab.write_bytes(b"x")
        screen = PlayingScreen(timeline(8), config=Config(),
                               song_key=tab.stem, song_path=str(tab))
        assert screen._source_video_line() == ""

    def test_it_is_read_once_and_never_per_frame(self, tmp_path):
        """The panel is redrawn every frame while it is open and this touches
        the DISK, so sixty frames must cost one read. A per-frame read is the
        loop this project has had to move out of the frame four times.

        The first version of this test asserted ZERO reads over sixty calls,
        because its fixture had never asked -- so the first of the sixty did
        the read and the test was wrong about its own premise rather than
        about the code.
        """
        screen = self._screen(tmp_path)
        reads = []
        real = songsterr.load_cache

        def counted(path):
            reads.append(path)
            return real(path)

        songsterr.load_cache = counted
        try:
            for _ in range(60):
                screen._source_video_line()
        finally:
            songsterr.load_cache = real
        assert len(reads) == 1

    def test_a_map_it_cannot_read_never_takes_the_panel_down(self, tmp_path):
        screen = self._screen(tmp_path)
        screen._source_video = None
        real = songsterr.source_video_line_for

        def boom(*a, **k):
            raise RuntimeError("the disk said no")

        songsterr.source_video_line_for = boom
        try:
            assert screen._source_video_line() == ""
        finally:
            songsterr.source_video_line_for = real


# ── and the download screen says it where it is most useful ────────────────

class TestTheDownloadNamesIt:
    """Right after a fetch is where it matters most: the player has a tab
    and no recording yet, and *this* is the video to get the audio from.

    Driven through the real `grab_song` rather than by reading the source,
    because a note nothing appends is a feature that ships doing nothing --
    which this project has shipped four times.
    """

    def _grab(self, tmp_path, monkeypatch, entries=DRY_COUNTY):
        from pickhero.tabs import downloader, songsterr as live

        out = tmp_path / "Bon Jovi - Dry County.gp5"

        def fake_download(song_id, path):
            written = tmp_path / "Bon Jovi - Dry County.gp5"
            written.write_bytes(b"not a real tab")
            return written, 7, None, ""

        monkeypatch.setattr(downloader, "download_tab", fake_download)
        monkeypatch.setattr(live, "fetch_meta",
                            lambda song_id: {"revisionId": 7, "title": "t",
                                             "artist": "a"})
        monkeypatch.setattr(live, "fetch_entries",
                            lambda song_id, rev: list(entries))
        return downloader.grab_song(12345, out, want_audio=False)

    def test_the_notes_name_the_video(self, tmp_path, monkeypatch):
        grab = self._grab(tmp_path, monkeypatch)
        assert any(MAIN in note for note in grab.notes), grab.notes

    def test_a_reply_with_no_video_adds_no_note(self, tmp_path, monkeypatch):
        bare = [{"points": [0.0, 2.0, 4.0]}]
        grab = self._grab(tmp_path, monkeypatch, bare)
        assert not any("timed its points" in note for note in grab.notes)
