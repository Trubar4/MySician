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


class TestWhyThereIsNoFile:
    """*"Songsterr holds no Guitar Pro file for this tab"* on four songs in
    a row, while a third-party downloader fetched all four.

    Reaching that message means the revision WAS fetched and parsed and
    simply has no usable `source`. Whether the field moved, was renamed, or
    is genuinely absent is a question the reply itself answers -- so the
    message quotes it, and the player's next screenshot is the answer
    instead of another round.
    """

    def _revision(self, monkeypatch, payload):
        from pickhero.tabs import downloader as d
        monkeypatch.setattr(
            d, "_fetch_json",
            lambda url: {"revisionId": 77} if "meta" in url else payload)

    def test_the_keys_that_were_there_are_named(self, monkeypatch):
        from pickhero.tabs import downloader as d
        self._revision(monkeypatch, {"id": 77, "attachmentUrl": "x",
                                     "songId": 5})
        _, why = d._source_of(1)
        assert "attachmentUrl" in why and "songId" in why
        assert "revision 77" in why

    def test_an_empty_revision_says_nothing_rather_than_a_blank(self,
                                                                monkeypatch):
        from pickhero.tabs import downloader as d
        self._revision(monkeypatch, {})
        assert "nothing" in d._source_of(1)[1]

    def test_a_source_that_is_there_is_still_just_used(self, monkeypatch):
        from pickhero.tabs import downloader as d
        self._revision(monkeypatch, {"source": "https://gp/x.gp5"})
        assert d._source_of(1) == ("https://gp/x.gp5", "")

    def test_a_dead_network_is_not_the_same_message(self, monkeypatch):
        """"The network is down" and "this tab has no file" send the player
        to completely different places."""
        from pickhero.tabs import downloader as d
        monkeypatch.setattr(d, "_fetch_json", lambda url: None)
        assert "did not answer" in d._source_of(1)[1]
