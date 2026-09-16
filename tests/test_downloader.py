"""Tests for pickhero.tabs.downloader module."""

import json
import urllib.error
from unittest.mock import patch

from pickhero.tabs.downloader import (
    SongsterrResult,
    _get_source_url,
    download_gp5,
    get_songsterr_url,
    sanitize_filename,
    search,
)


class TestSearch:
    def test_returns_results(self):
        api_response = json.dumps([
            {"songId": 123, "title": "Smoke on the Water", "artist": "Deep Purple"},
            {"songId": 456, "title": "Paranoid", "artist": "Black Sabbath"},
        ]).encode()

        with patch("pickhero.tabs.downloader._urlopen", return_value=api_response):
            results = search("smoke")

        assert len(results) == 2
        assert results[0] == SongsterrResult(123, "Smoke on the Water", "Deep Purple")
        assert results[1] == SongsterrResult(456, "Paranoid", "Black Sabbath")

    def test_max_results(self):
        items = [{"songId": i, "title": f"Song {i}", "artist": "A"} for i in range(20)]
        api_response = json.dumps(items).encode()

        with patch("pickhero.tabs.downloader._urlopen", return_value=api_response):
            results = search("test", max_results=5)

        assert len(results) == 5

    def test_network_error_returns_empty(self):
        with patch(
            "pickhero.tabs.downloader._urlopen",
            side_effect=urllib.error.URLError("fail"),
        ):
            results = search("anything")
        assert results == []

    def test_malformed_json_returns_empty(self):
        with patch("pickhero.tabs.downloader._urlopen", return_value=b"not json"):
            results = search("anything")
        assert results == []

    def test_missing_fields_use_defaults(self):
        api_response = json.dumps([{"other": "data"}]).encode()
        with patch("pickhero.tabs.downloader._urlopen", return_value=api_response):
            results = search("test")

        assert len(results) == 1
        assert results[0].song_id == 0
        assert results[0].title == ""
        assert results[0].artist == ""


class TestGetSourceUrl:
    def _make_responses(self, meta_body, revision_body=None):
        """Helper: return a fake _urlopen that returns meta then revision JSON."""
        responses = iter(
            [json.dumps(b).encode() for b in [meta_body] + ([revision_body] if revision_body else [])]
        )
        def fake_urlopen(url, timeout=15):
            try:
                return next(responses)
            except StopIteration:
                raise urllib.error.URLError("unexpected call")
        return fake_urlopen

    def test_success(self):
        meta = {"revisionId": 999}
        revision = {"source": "https://gp.songsterr.com/export.abc.gp"}
        with patch("pickhero.tabs.downloader._urlopen",
                   side_effect=self._make_responses(meta, revision)):
            result = _get_source_url(42)
        assert result == "https://gp.songsterr.com/export.abc.gp"

    def test_meta_network_error(self):
        with patch("pickhero.tabs.downloader._urlopen",
                   side_effect=urllib.error.URLError("fail")):
            assert _get_source_url(42) is None

    def test_meta_not_dict(self):
        with patch("pickhero.tabs.downloader._urlopen",
                   return_value=json.dumps([1, 2]).encode()):
            assert _get_source_url(42) is None

    def test_meta_missing_revision_id(self):
        with patch("pickhero.tabs.downloader._urlopen",
                   return_value=json.dumps({"other": "data"}).encode()):
            assert _get_source_url(42) is None

    def test_revision_network_error(self):
        meta = {"revisionId": 999}
        calls = [0]
        def fake(url, timeout=15):
            calls[0] += 1
            if calls[0] == 1:
                return json.dumps(meta).encode()
            raise urllib.error.URLError("fail")
        with patch("pickhero.tabs.downloader._urlopen", side_effect=fake):
            assert _get_source_url(42) is None

    def test_revision_missing_source(self):
        meta = {"revisionId": 999}
        revision = {"other": "data"}
        with patch("pickhero.tabs.downloader._urlopen",
                   side_effect=self._make_responses(meta, revision)):
            assert _get_source_url(42) is None

    def test_non_http_source_rejected(self):
        meta = {"revisionId": 999}
        revision = {"source": "ftp://example.com/tab.gp5"}
        with patch("pickhero.tabs.downloader._urlopen",
                   side_effect=self._make_responses(meta, revision)):
            assert _get_source_url(42) is None

    def test_non_string_source_rejected(self):
        meta = {"revisionId": 999}
        revision = {"source": 12345}
        with patch("pickhero.tabs.downloader._urlopen",
                   side_effect=self._make_responses(meta, revision)):
            assert _get_source_url(42) is None


class TestDownloadGp5:
    def test_success(self, tmp_path):
        source_url = "https://gp.songsterr.com/export.abc.gp"
        file_bytes = b"\x00GP5_FAKE_DATA"

        def fake_urlopen(url, timeout=15):
            if "meta" in url:
                return json.dumps({"revisionId": 999}).encode()
            if "revision" in url:
                return json.dumps({"source": source_url}).encode()
            return file_bytes

        output = tmp_path / "test.gp5"
        with patch("pickhero.tabs.downloader._urlopen", side_effect=fake_urlopen):
            result = download_gp5(42, output)

        assert result is True
        # The SOURCE decides the suffix, not the name it was asked for. This
        # test used to assert `test.gp5` while the source it mocked ends in
        # `.gp` -- so it asserted the bug the player reported: *"Why does it
        # download a gp5 and when I use songsterr-downloader.com I get a
        # gp?"*. The loader dispatches on content, so both opened; but a
        # Guitar Pro 7 file called .gp5 is the wrong file to hand to Guitar
        # Pro itself.
        assert not output.exists()
        assert (tmp_path / "test.gp").read_bytes() == file_bytes

    def test_source_url_not_found(self, tmp_path):
        with patch("pickhero.tabs.downloader._urlopen",
                   side_effect=urllib.error.URLError("fail")):
            result = download_gp5(42, tmp_path / "test.gp5")
        assert result is False

    def test_file_download_fails(self, tmp_path):
        source_url = "https://gp.songsterr.com/export.abc.gp"
        calls = [0]

        def fake_urlopen(url, timeout=15):
            calls[0] += 1
            if calls[0] == 1:
                return json.dumps({"revisionId": 999}).encode()
            if calls[0] == 2:
                return json.dumps({"source": source_url}).encode()
            raise urllib.error.URLError("download fail")

        output = tmp_path / "test.gp5"
        with patch("pickhero.tabs.downloader._urlopen", side_effect=fake_urlopen):
            result = download_gp5(42, output)

        assert result is False
        assert not output.exists()

    def test_creates_parent_dirs(self, tmp_path):
        source_url = "https://gp.songsterr.com/export.abc.gp"

        def fake_urlopen(url, timeout=15):
            if "meta" in url:
                return json.dumps({"revisionId": 999}).encode()
            if "revision" in url:
                return json.dumps({"source": source_url}).encode()
            return b"data"

        output = tmp_path / "sub" / "dir" / "test.gp5"
        with patch("pickhero.tabs.downloader._urlopen", side_effect=fake_urlopen):
            result = download_gp5(42, output)

        assert result is True
        assert (tmp_path / "sub" / "dir" / "test.gp").exists()


class TestGetSongsterrUrl:
    """A page that loads.

    `/a/wsa/{id}` is not a Songsterr URL and does not open -- which is what
    the player saw when a download failed and the browser opened on nothing.
    Songsterr's shape is `<artist>-<title>-tab-s<id>`, and it redirects any
    slug with the right `-s<id>` tail to the right page.
    """

    def test_it_is_the_shape_songsterr_actually_uses(self):
        assert (get_songsterr_url(2333598, "Love Walked In v4", "Thunder")
                == "https://www.songsterr.com/a/wsa/"
                   "thunder-love-walked-in-v4-tab-s2333598")

    def test_the_id_is_always_on_the_end(self):
        assert get_songsterr_url(999, "What's Up", "4 Non Blondes").endswith(
            "-tab-s999")

    def test_a_name_of_nothing_still_gives_a_page(self):
        assert get_songsterr_url(123) == (
            "https://www.songsterr.com/a/wsa/tab-tab-s123")


class TestSanitizeFilename:
    def test_removes_invalid_chars(self):
        assert sanitize_filename('AC/DC - Back In Black') == "ACDC - Back In Black"

    def test_removes_multiple_chars(self):
        assert sanitize_filename('test<>:"/\\|?*end') == "testend"

    def test_strips_whitespace(self):
        assert sanitize_filename("  hello  ") == "hello"

    def test_leaves_valid_chars(self):
        assert sanitize_filename("Artist - Song (Live)") == "Artist - Song (Live)"


class TestFindingARevisionThatStillHasAFile:
    """*"Songsterr holds no Guitar Pro file for this tab"* on four songs in
    a row, while a third-party downloader fetched all four.

    The player's own data for Papa Roach 14907 says what is going on:
    revision 6688469 has **no `source` key at all**, and its
    `prevRevisionId` 5981666 has **`"source": ""`**. Songsterr keeps these
    tabs in their own format -- per-track hashes, an `audioV4` mix -- and a
    Guitar Pro file exists only where somebody uploaded one. But every
    revision links to the one before it, so the history is walkable.
    """

    #: Trimmed to the shape that matters, from the reply the player sent.
    PAPA_ROACH = {
        6688469: {"revisionId": 6688469, "songId": 14907,
                  "artist": "Papa Roach", "audioV4": "v4-SKh",
                  "prevRevisionId": 5981666},
        5981666: {"revisionId": 5981666, "songId": 14907, "source": "",
                  "prevRevisionId": 4100000},
        4100000: {"revisionId": 4100000,
                  "source": "https://gp.songsterr.com/x.gp5"},
    }

    def _songsterr(self, monkeypatch, revisions, latest=6688469):
        from pickhero.tabs import downloader as d

        def fetch(url):
            if "meta" in url:
                return {"revisionId": latest}
            return revisions.get(int(url.rsplit("/", 1)[-1]))

        monkeypatch.setattr(d, "_fetch_json", fetch)

    def test_the_walk_finds_the_older_revision_that_has_one(self,
                                                            monkeypatch):
        from pickhero.tabs import downloader as d
        self._songsterr(monkeypatch, self.PAPA_ROACH)
        url, revision, why = d._source_of(14907)
        assert url == "https://gp.songsterr.com/x.gp5"
        assert why == ""

    def test_and_says_which_revision_it_came_from(self, monkeypatch):
        """The bar map has to come from the SAME one: a tab from revision N
        timed by a map from revision N+6 is two different edits of the song
        pretending to be one."""
        from pickhero.tabs import downloader as d
        self._songsterr(monkeypatch, self.PAPA_ROACH)
        assert d._source_of(14907)[1] == 4100000

    def test_an_empty_source_string_does_not_count(self, monkeypatch):
        """Revision 5981666 has `"source": ""`. A key that is there and
        empty is not a file."""
        from pickhero.tabs import downloader as d
        self._songsterr(monkeypatch, {
            5981666: {"source": ""}}, latest=5981666)
        assert d._source_of(1)[0] == ""

    def test_a_history_with_no_file_anywhere_says_how_far_it_looked(
            self, monkeypatch):
        """"No file" said of one revision is a guess; said of eight it is a
        finding."""
        from pickhero.tabs import downloader as d
        chain = {n: {"revisionId": n, "audioV4": "x", "prevRevisionId": n - 1}
                 for n in range(100, 80, -1)}
        self._songsterr(monkeypatch, chain, latest=100)
        url, _, why = d._source_of(1)
        assert url == ""
        assert f"{d.SOURCE_HOPS} revisions checked" in why
        assert "audioV4" in why, "the keys it did find are named"

    def test_it_stops_rather_than_looping_on_a_self_referencing_history(
            self, monkeypatch):
        from pickhero.tabs import downloader as d
        self._songsterr(monkeypatch, {7: {"revisionId": 7,
                                          "prevRevisionId": 7}}, latest=7)
        assert d._source_of(1)[0] == ""

    def test_a_source_on_the_newest_revision_is_taken_at_once(self,
                                                              monkeypatch):
        from pickhero.tabs import downloader as d
        self._songsterr(monkeypatch, {9: {"source": "https://gp/x.gp5"}},
                        latest=9)
        assert d._source_of(1) == ("https://gp/x.gp5", 9, "")

    def test_a_dead_network_is_not_the_same_message(self, monkeypatch):
        """"The network is down" and "this tab has no file" send the player
        to completely different places."""
        from pickhero.tabs import downloader as d
        monkeypatch.setattr(d, "_fetch_json", lambda url: None)
        assert "did not answer" in d._source_of(1)[2]
