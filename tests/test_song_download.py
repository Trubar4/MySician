"""One ENTER fetches the tab, its bar map, and the recording it is timed to.

Downloading a tab used to be the first of four jobs; the other three were the
player's. Find an MP3. Paste the Songsterr link back in with Ctrl+U. Press
Ctrl+S and hope the listening reads a song that repeats itself -- and on
What's Up it does not, it reads +9.9, -34.4, -6.2 and +21.1 s.

All four come out of the same reply, so all four happen on the one ENTER.
What these assert is the part that can be got wrong without anyone noticing:
WHICH video is taken, and that a step which fails says so in words rather
than leaving a song that looks downloaded and is not.
"""

import json
import sys
import types

import pytest

from pickhero.tabs import downloader, songsterr, youtube

# The real shape of a video-points reply, trimmed. The backing track is FIRST
# and LONGEST on purpose: that is the pair of properties that made the first
# build hand What's Up the wrong recording.
ENTRIES = [
    {"id": 11, "videoId": "backing1", "feature": "backing",
     "points": [0.0, 2.0, 4.0, 6.0, 8.0]},
    {"id": 12, "videoId": "-IWAOPi4VCc", "feature": None,
     "points": [0.94, 4.04, 7.11, 10.2]},
    {"id": 13, "videoId": "alt1", "feature": "alternative",
     "points": [-24.21, -21.11, -18.04, -14.95]},
]
META = {"songId": 2333598, "revisionId": 88, "title": "Thunder",
        "artist": "AC-DC"}


class TestWhichVideo:
    """`feature: null` is the recording the tab was written from."""

    def test_the_main_video_wins_over_a_longer_backing_track(self):
        chosen = songsterr.main_entry(ENTRIES)
        assert songsterr.video_id_of(chosen) == "-IWAOPi4VCc"

    def test_longest_would_have_picked_the_wrong_one(self):
        """The property that made What's Up read a 78-point backing track
        over its own 72-point main video. Asserted so the old rule cannot
        quietly come back."""
        assert len(songsterr.bar_times_of(ENTRIES)) == 5      # the backing
        assert len(songsterr.bar_times_of_entry(
            songsterr.main_entry(ENTRIES))) == 4              # the right one

    def test_a_tab_with_only_backing_tracks_still_gets_one(self):
        only_backing = [e for e in ENTRIES if e["feature"]]
        assert songsterr.main_entry(only_backing) is ENTRIES[0]

    def test_nothing_usable_is_none_not_a_raise(self):
        assert songsterr.main_entry([]) is None
        assert songsterr.main_entry([{"videoId": "x", "points": [5.0]}]) is None
        assert songsterr.main_entry(None) is None

    def test_an_entry_that_is_not_a_timeline_is_not_one(self):
        for points in ([], [1.0], [3.0, 1.0], ["a", "b"], None, [2.0, 2.0]):
            assert songsterr.bar_times_of_entry({"points": points}) == []


class TestTheOrderTheFitTriesThem:
    """First, not only: the recording on disk is not always the video."""

    def test_the_named_video_comes_first(self):
        order = songsterr.candidates_for(ENTRIES, "-IWAOPi4VCc")
        assert order[0] == [0.94, 4.04, 7.11, 10.2]
        assert len(order) == 3

    def test_and_the_others_are_still_there(self):
        """Because the player may already have had an MP3, or replaced the
        one that came down. The fit is what decides; this only reorders."""
        order = songsterr.candidates_for(ENTRIES, "-IWAOPi4VCc")
        assert [0.0, 2.0, 4.0, 6.0, 8.0] in order

    def test_an_unknown_video_changes_nothing(self):
        assert (songsterr.candidates_for(ENTRIES, "nobody")
                == songsterr.all_bar_times(ENTRIES))

    def test_preferred_puts_the_main_video_in_front(self):
        assert songsterr.preferred_bar_times(ENTRIES)[0] == [0.94, 4.04,
                                                             7.11, 10.2]


class TestKeepingItBesideTheTab:
    """A cache the player cannot carry is a cache the player has not got."""

    def test_it_lands_next_to_the_tab(self, tmp_path):
        tab = tmp_path / "AC-DC - Thunder.gp5"
        tab.write_bytes(b"not really a tab")
        written = songsterr.save_cache(tab, 2333598, META, ENTRIES)
        assert written == tmp_path / "AC-DC - Thunder.songsterr.json"
        assert written.is_file()

    def test_what_comes_back_is_what_went_in(self, tmp_path):
        tab = tmp_path / "song.gp5"
        tab.write_bytes(b"x")
        songsterr.save_cache(tab, 2333598, META, ENTRIES)
        bars, raw = songsterr.bar_times_from_cache(tab)
        assert raw["songId"] == 2333598
        assert raw["title"] == "Thunder"
        assert bars[0] == [0.94, 4.04, 7.11, 10.2]

    def test_every_video_is_kept_not_just_the_chosen_one(self, tmp_path):
        """Which entry fits is a question the RECORDING answers, and a cache
        that has already answered it cannot be re-asked when the player swaps
        the MP3."""
        tab = tmp_path / "song.gp5"
        tab.write_bytes(b"x")
        songsterr.save_cache(tab, 1, META, ENTRIES)
        stored = json.loads(
            (tmp_path / "song.songsterr.json").read_text(encoding="utf-8"))
        assert len(stored["entries"]) == 3

    def test_no_cache_is_none_and_not_a_raise(self, tmp_path):
        assert songsterr.load_cache(tmp_path / "absent.gp5") is None
        assert songsterr.bar_times_from_cache(tmp_path / "absent.gp5") is None

    def test_a_half_written_cache_is_none(self, tmp_path):
        """Ask the network, which is what the app did before this existed."""
        tab = tmp_path / "song.gp5"
        tab.write_bytes(b"x")
        (tmp_path / "song.songsterr.json").write_text("{ not json",
                                                      encoding="utf-8")
        assert songsterr.load_cache(tab) is None

    def test_a_cache_with_no_usable_timeline_is_none(self, tmp_path):
        tab = tmp_path / "song.gp5"
        tab.write_bytes(b"x")
        songsterr.save_cache(tab, 1, META, [{"videoId": "x", "points": [1.0]}])
        assert songsterr.load_cache(tab) is None


class TestNamingTheRecording:

    def test_the_audio_takes_the_tab_s_name(self):
        """`song_key` IS the tab's stem, and `mp3_path_for` falls back to a
        file of the same name in the songs folder. Same name is what makes
        the recording survive being carried to a second machine."""
        assert (youtube.audio_path_for("/songs/AC-DC - Thunder.gp5").name
                == "AC-DC - Thunder.mp3")

    def test_missing_names_the_half_that_is_missing(self, monkeypatch):
        monkeypatch.setattr(youtube, "ffmpeg_path", lambda: None)
        assert youtube.missing() in ("yt-dlp is not installed",
                                     "ffmpeg was not found")
        assert not youtube.available()

    def test_no_video_id_is_refused_before_anything_is_fetched(self,
                                                              monkeypatch):
        monkeypatch.setattr(youtube, "missing", lambda: "")
        with pytest.raises(youtube.NotAvailable):
            youtube.fetch_audio("", "/tmp/x.mp3")


def _fake_songsterr(monkeypatch, entries=ENTRIES, raises=None):
    def meta(song_id):
        if raises:
            raise raises
        return META

    monkeypatch.setattr(songsterr, "fetch_meta", meta)
    monkeypatch.setattr(songsterr, "fetch_entries",
                        lambda song_id, rev: entries)


class TestOneEnterFetchesTheSong:

    def test_everything_comes_down_together(self, tmp_path, monkeypatch):
        tab = tmp_path / "AC-DC - Thunder.gp5"
        monkeypatch.setattr(downloader, "download_gp5",
                            lambda sid, out: (out.write_bytes(b"gp"), True)[1])
        _fake_songsterr(monkeypatch)
        pulled = []
        monkeypatch.setattr(youtube, "fetch_audio",
                            lambda vid, out, cb=None: (pulled.append(vid),
                                                       tmp_path / "a.mp3")[1])

        grab = downloader.grab_song(2333598, tab)

        assert grab.ok
        assert grab.video_id == "-IWAOPi4VCc"     # the main video, not backing
        assert pulled == ["-IWAOPi4VCc"]
        assert grab.bars == 4
        assert grab.audio_path == tmp_path / "a.mp3"
        assert (tmp_path / "AC-DC - Thunder.songsterr.json").is_file()

    def test_the_progress_reaches_the_end(self, tmp_path, monkeypatch):
        """Four network steps, one of them a whole song's audio. Without this
        the screen looks frozen and the player kills the app."""
        monkeypatch.setattr(downloader, "download_gp5",
                            lambda sid, out: (out.write_bytes(b"gp"), True)[1])
        _fake_songsterr(monkeypatch)
        monkeypatch.setattr(
            youtube, "fetch_audio",
            lambda vid, out, cb=None: (cb(0.5, "downloading the audio"),
                                       cb(1.0, "audio ready"),
                                       tmp_path / "a.mp3")[2])
        seen = []
        downloader.grab_song(1, tmp_path / "s.gp5",
                             on_progress=lambda f, w: seen.append(f))
        assert seen == sorted(seen)
        assert seen[0] < 0.2 and seen[-1] == pytest.approx(1.0)

    def test_a_tab_that_will_not_download_is_not_ok(self, tmp_path,
                                                   monkeypatch):
        monkeypatch.setattr(downloader, "download_gp5", lambda sid, out: False)
        grab = downloader.grab_song(1, tmp_path / "s.gp5")
        assert not grab.ok
        assert "could not be downloaded" in grab.notes[0]

    def test_no_bar_map_still_leaves_a_song_to_practise(self, tmp_path,
                                                       monkeypatch):
        monkeypatch.setattr(downloader, "download_gp5",
                            lambda sid, out: (out.write_bytes(b"gp"), True)[1])
        _fake_songsterr(monkeypatch, raises=songsterr.NotFound("404"))
        grab = downloader.grab_song(1, tmp_path / "s.gp5")
        assert grab.ok                       # the tab is the thing asked for
        assert grab.video_id == ""
        assert any("No bar map" in n for n in grab.notes)

    def test_a_missing_ffmpeg_is_named_not_swallowed(self, tmp_path,
                                                     monkeypatch):
        """'ffmpeg was not found' and 'this video is private' send the player
        to completely different places."""
        monkeypatch.setattr(downloader, "download_gp5",
                            lambda sid, out: (out.write_bytes(b"gp"), True)[1])
        _fake_songsterr(monkeypatch)

        def refuse(vid, out, cb=None):
            raise youtube.NotAvailable("ffmpeg was not found")

        monkeypatch.setattr(youtube, "fetch_audio", refuse)
        grab = downloader.grab_song(1, tmp_path / "s.gp5")
        assert grab.ok
        assert grab.bars == 4                # the bar map still arrived
        assert grab.audio_path is None
        assert any("ffmpeg was not found" in n for n in grab.notes)

    def test_want_audio_false_stops_before_youtube(self, tmp_path,
                                                  monkeypatch):
        monkeypatch.setattr(downloader, "download_gp5",
                            lambda sid, out: (out.write_bytes(b"gp"), True)[1])
        _fake_songsterr(monkeypatch)

        def never(*a, **k):
            raise AssertionError("asked YouTube when it was told not to")

        monkeypatch.setattr(youtube, "fetch_audio", never)
        grab = downloader.grab_song(1, tmp_path / "s.gp5", want_audio=False)
        assert grab.ok and grab.bars == 4 and grab.audio_path is None


class TestWhatTheDownloadRemembers:
    """So Ctrl+U is not needed and the recording loads itself."""

    def _screen(self, tmp_path, config):
        from pickhero.ui.download_menu import DownloadMenuScreen
        return DownloadMenuScreen(tmp_path, config=config)

    def test_the_song_id_and_the_recording_are_stored(self, tmp_path):
        from pickhero.config import Config
        config = Config()
        config.save = lambda: None
        screen = self._screen(tmp_path, config)
        grab = downloader.Grab(song_id=2333598,
                               tab_path=tmp_path / "Thunder.gp5",
                               video_id="-IWAOPi4VCc",
                               audio_path=tmp_path / "Thunder.mp3")
        screen._remember("Thunder", grab)
        assert config.songsterr_for("Thunder") == 2333598
        assert config.song_mp3_paths["Thunder"] == str(tmp_path / "Thunder.mp3")

    def test_no_audio_means_no_recording_is_claimed(self, tmp_path):
        """A path to a file that is not there is worse than no path: the app
        would report the recording as moved."""
        from pickhero.config import Config
        config = Config()
        config.save = lambda: None
        screen = self._screen(tmp_path, config)
        screen._remember("Thunder", downloader.Grab(song_id=7))
        assert config.songsterr_for("Thunder") == 7
        assert "Thunder" not in config.song_mp3_paths

    def test_settings_that_will_not_save_do_not_lose_the_download(self,
                                                                 tmp_path):
        from pickhero.config import Config
        config = Config()

        def refuse():
            raise OSError("read-only")

        config.save = refuse
        screen = self._screen(tmp_path, config)
        screen._notes = []
        screen._remember("Thunder", downloader.Grab(song_id=7))
        assert any("Settings not saved" in n for n in screen._notes)

    def test_a_screen_without_a_config_still_works(self, tmp_path):
        screen = self._screen(tmp_path, None)
        screen._remember("Thunder", downloader.Grab(song_id=7))   # no raise


class TestSyncingOffline:
    """The song still syncs on a machine with no network."""

    def _view(self, tab_path):
        return types.SimpleNamespace(_song_path=str(tab_path))

    def test_the_cache_is_asked_before_songsterr(self, tmp_path, monkeypatch):
        from pickhero.ui.scrolling import PlayingScreen
        tab = tmp_path / "song.gp5"
        tab.write_bytes(b"x")
        songsterr.save_cache(tab, 2333598, META, ENTRIES)

        def never(*a, **k):
            raise AssertionError("went to the network with a cache on disk")

        monkeypatch.setattr(songsterr, "fetch_meta", never)
        bars, meta = PlayingScreen._songsterr_bar_times(self._view(tab),
                                                        2333598)
        assert bars[0] == [0.94, 4.04, 7.11, 10.2]
        assert meta["title"] == "Thunder"

    def test_a_pasted_link_is_cached_on_the_first_press(self, tmp_path,
                                                        monkeypatch):
        from pickhero.ui.scrolling import PlayingScreen
        tab = tmp_path / "song.gp5"
        tab.write_bytes(b"x")
        _fake_songsterr(monkeypatch)
        PlayingScreen._songsterr_bar_times(self._view(tab), 2333598)
        assert (tmp_path / "song.songsterr.json").is_file()

    def test_a_song_with_no_usable_timeline_says_so(self, tmp_path,
                                                    monkeypatch):
        from pickhero.ui.scrolling import PlayingScreen
        tab = tmp_path / "song.gp5"
        tab.write_bytes(b"x")
        _fake_songsterr(monkeypatch, entries=[{"videoId": "x",
                                               "points": [1.0]}])
        with pytest.raises(songsterr.NotFound):
            PlayingScreen._songsterr_bar_times(self._view(tab), 2333598)


class TestWhatYtDlpIsActuallyToldToDo:
    """The options, because getting them wrong is silent.

    yt-dlp appends the real extension and the post-processor then replaces
    it, so handing it a name that already ends in `.mp3` produces
    `song.mp3.mp3` -- a file the app will never look for, next to a download
    that reported success.
    """

    def _fake_yt_dlp(self, monkeypatch, tmp_path, writes=True):
        seen = {}

        class DL:
            def __init__(self, options):
                seen["options"] = options

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def download(self, urls):
                seen["urls"] = urls
                if writes:
                    out = str(seen["options"]["outtmpl"])
                    pathlib_path = out.replace(".%(ext)s", ".mp3")
                    open(pathlib_path, "wb").write(b"audio")

        module = types.ModuleType("yt_dlp")
        module.YoutubeDL = DL
        monkeypatch.setitem(sys.modules, "yt_dlp", module)
        monkeypatch.setattr(youtube, "ffmpeg_path",
                            lambda: tmp_path / "ffmpeg")
        return seen

    def test_the_name_does_not_end_up_doubled(self, tmp_path, monkeypatch):
        seen = self._fake_yt_dlp(monkeypatch, tmp_path)
        written = youtube.fetch_audio("abc", tmp_path / "Thunder.mp3")
        assert seen["options"]["outtmpl"].endswith("Thunder.%(ext)s")
        assert written == tmp_path / "Thunder.mp3"
        assert written.is_file()

    def test_a_video_id_that_starts_with_a_dash_is_not_read_as_a_switch(
            self, tmp_path, monkeypatch):
        """Thunder's is `-IWAOPi4VCc`. A watch URL, not a bare id."""
        seen = self._fake_yt_dlp(monkeypatch, tmp_path)
        youtube.fetch_audio("-IWAOPi4VCc", tmp_path / "t.mp3")
        assert seen["urls"] == [
            "https://www.youtube.com/watch?v=-IWAOPi4VCc"]

    def test_it_is_told_where_ffmpeg_is_and_what_to_write(self, tmp_path,
                                                          monkeypatch):
        seen = self._fake_yt_dlp(monkeypatch, tmp_path)
        youtube.fetch_audio("abc", tmp_path / "t.mp3")
        options = seen["options"]
        assert options["ffmpeg_location"] == str(tmp_path / "ffmpeg")
        assert options["postprocessors"][0]["preferredcodec"] == "mp3"
        assert options["noplaylist"] is True
        # Never turned off. The proxy and the CA bundle are the machine's
        # business; an app that skips certificate checks to make a download
        # work is an app that has stopped checking.
        assert options["nocheckcertificate"] is False

    def test_a_download_that_writes_nothing_says_so(self, tmp_path,
                                                    monkeypatch):
        """A path to a file that is not there is what the player would then
        try to play."""
        self._fake_yt_dlp(monkeypatch, tmp_path, writes=False)
        with pytest.raises(youtube.NotAvailable, match="nothing was written"):
            youtube.fetch_audio("abc", tmp_path / "t.mp3")

    def test_the_progress_stops_short_of_the_end_until_it_is_done(
            self, tmp_path, monkeypatch):
        """A bar that reaches the end and then sits there is a bar that says
        the app has hung -- and the conversion still has to happen."""
        seen = self._fake_yt_dlp(monkeypatch, tmp_path)
        steps = []
        youtube.fetch_audio("abc", tmp_path / "t.mp3",
                            lambda f, what: steps.append(f))
        hook = seen["options"]["progress_hooks"][0]
        hook({"status": "downloading", "total_bytes": 100,
              "downloaded_bytes": 100})
        assert steps[-1] <= 0.9
        hook({"status": "finished"})
        assert steps[-1] == pytest.approx(0.9)
