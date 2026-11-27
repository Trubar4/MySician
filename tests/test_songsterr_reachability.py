"""Four problems, one sentence, and the player sent to retype his query.

*"Die Songsterr Suche bringt auf Mac keine Ergebnisse."* The search answered
"No results found" -- which is also what it answers when the machine is
offline, when a proxy refuses, and when the HTTPS handshake cannot be
verified. For three of those, retyping the query cannot possibly help.

The commonest of them on macOS is not about this app at all: a python.org
build ships its own OpenSSL and looks for root certificates inside its own
framework rather than in the system keychain, and the file that puts them
there is installed by `Install Certificates.command`, which the installer
does not run. Until it is run, every HTTPS request from every Python program
on that machine fails verification.
"""

from __future__ import annotations

import ssl
import urllib.error
from pathlib import Path

import pygame
import pytest

from pickhero.tabs import downloader
from pickhero.ui.download_menu import DownloadMenuScreen


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(downloader, "last_error", "")


def _verify_error():
    exc = ssl.SSLCertVerificationError("unable to get local issuer")
    return urllib.error.URLError(exc)


class TestTheReasonIsReported:
    def test_a_failed_request_says_why(self, monkeypatch):
        def boom(url, timeout=10):
            raise urllib.error.URLError(OSError("Network is unreachable"))

        monkeypatch.setattr(downloader, "_urlopen", boom)
        assert downloader._fetch_json("https://x/y") is None
        assert "unreachable" in downloader.last_error

    def test_a_certificate_failure_is_named_as_itself(self, monkeypatch):
        """Not "URLError", which tells a player nothing. This is the one
        cause whose fix is a file already on their machine."""
        monkeypatch.setattr(downloader, "_urlopen",
                            lambda url, timeout=10: (_ for _ in ()).throw(
                                _verify_error()))
        downloader._fetch_json("https://x/y")
        assert "cannot verify HTTPS certificates" in downloader.last_error

    def test_an_http_code_is_the_code(self, monkeypatch):
        """HTTPError is a subclass of URLError and carries a `reason` of its
        own, so testing in the other order reports a 404 as a socket fault."""
        exc = urllib.error.HTTPError("u", 503, "nope", None, None)
        monkeypatch.setattr(downloader, "_urlopen",
                            lambda url, timeout=10: (_ for _ in ()).throw(exc))
        downloader._fetch_json("https://x/y")
        assert "HTTP 503" in downloader.last_error

    def test_a_reply_that_is_not_json_is_its_own_problem(self, monkeypatch):
        monkeypatch.setattr(downloader, "_urlopen",
                            lambda url, timeout=10: b"<html>")
        assert downloader._fetch_json("https://x/y") is None
        assert "not JSON" in downloader.last_error

    def test_and_a_good_reply_clears_it(self, monkeypatch):
        """Or a successful search would report the last failure for ever."""
        monkeypatch.setattr(downloader, "last_error", "something old")
        monkeypatch.setattr(downloader, "_urlopen",
                            lambda url, timeout=10: b'{"a": 1}')
        assert downloader._fetch_json("https://x/y") == {"a": 1}
        assert downloader.last_error == ""


class TestTheTrustStore:
    def test_a_working_store_asks_for_no_fix(self):
        """This machine has one, so this is also the control: the advice
        must not appear where there is nothing wrong."""
        if downloader._no_trust_store():
            pytest.skip("this Python has no trust store either")
        assert downloader.trust_store_fix() == ()

    def test_and_the_context_is_left_alone(self):
        """A FALLBACK, never an override. Where the system store works it is
        used, so this cannot weaken a machine that was already fine."""
        if downloader._no_trust_store():
            pytest.skip("this Python has no trust store either")
        assert downloader._ssl_context() is None

    def test_verification_is_never_switched_off(self):
        """A search is not worth a connection nobody checked."""
        import inspect
        source = inspect.getsource(downloader)
        assert "CERT_NONE" not in source
        assert "_create_unverified" not in source
        assert "check_hostname = False" not in source

    def test_an_empty_store_names_the_macos_script_with_this_version(
            self, monkeypatch):
        """A path to copy rather than a thing to go and look for -- and the
        version is this process's own, because 3.12's script is in a folder
        named 3.12."""
        monkeypatch.setattr(downloader, "_no_trust_store", lambda: True)
        monkeypatch.setattr(downloader.sys, "platform", "darwin")
        said = " ".join(downloader.trust_store_fix())
        assert "Install Certificates.command" in said
        assert "%d.%d" % downloader.sys.version_info[:2] in said

    def test_and_says_something_else_everywhere_else(self, monkeypatch):
        """macOS advice printed on Windows is advice nobody can act on, which
        this project has already shipped once for ffmpeg."""
        monkeypatch.setattr(downloader, "_no_trust_store", lambda: True)
        monkeypatch.setattr(downloader.sys, "platform", "win32")
        said = " ".join(downloader.trust_store_fix())
        assert "Install Certificates" not in said
        assert "certifi" in said

    def test_the_fix_names_no_placeholder_in_brackets(self, monkeypatch):
        """A `<soundfont>.sf2` is what zsh answers with "no such file", and
        the player ran that line verbatim, as anybody would."""
        monkeypatch.setattr(downloader, "_no_trust_store", lambda: True)
        for platform in ("darwin", "win32"):
            monkeypatch.setattr(downloader.sys, "platform", platform)
            said = " ".join(downloader.trust_store_fix())
            assert "<" not in said and ">" not in said, platform


class TestTheScreenSaysIt:
    def _searched(self, monkeypatch, results, reason="", tmp_path=None):
        monkeypatch.setattr("pickhero.ui.download_menu.find",
                            lambda q, *a, **k: list(results))
        monkeypatch.setattr(downloader, "last_error", reason)
        menu = DownloadMenuScreen(tmp_path or Path("."))
        menu._query = "nirvana"
        # What ENTER does, which is the only route to `_do_search`: the
        # state is set before the thread starts, and a failed search leaves
        # it where it was. Without this the status is never rendered at all
        # and the drawing test passes on anything.
        menu._searching = True
        menu._state = "status"
        menu._do_search()
        return menu

    def test_a_network_failure_is_not_reported_as_no_results(self,
                                                             monkeypatch):
        menu = self._searched(monkeypatch, [], reason="URLError: offline")
        assert "Could not reach Songsterr" in menu._status_msg
        assert "offline" in menu._status_msg

    def test_but_songsterr_really_having_nothing_still_says_so(self,
                                                               monkeypatch):
        """The sentence is kept for the case it was written for."""
        menu = self._searched(monkeypatch, [], reason="")
        assert "No results found" in menu._status_msg

    def test_the_fix_is_under_it_and_is_not_a_newline(self, monkeypatch):
        """`Font.render` is single-line: a "\\n" is a box, not a break. So
        the lines are a LIST, drawn wrapped the way `_render_done` already
        draws its notes."""
        monkeypatch.setattr(downloader, "_no_trust_store", lambda: True)
        monkeypatch.setattr(downloader.sys, "platform", "darwin")
        menu = self._searched(monkeypatch, [],
                              reason="cannot verify HTTPS certificates")
        assert "\n" not in menu._status_msg
        assert any("Install Certificates.command" in line
                   for line in menu._status_notes)

    def test_ctrl_c_copies_the_fix_and_not_just_the_headline(self,
                                                             monkeypatch):
        """It is the one line worth sending, and the player was reading this
        kind of thing off a photograph of his monitor."""
        monkeypatch.setattr(downloader, "_no_trust_store", lambda: True)
        monkeypatch.setattr(downloader.sys, "platform", "darwin")
        menu = self._searched(monkeypatch, [], reason="cannot verify")
        assert "Install Certificates.command" in menu.copy_text()

    def test_a_later_search_does_not_keep_the_old_lines(self, monkeypatch):
        """Stale news standing under a fresh message is a fault this project
        has shipped for the MP3 offset already."""
        monkeypatch.setattr(downloader, "_no_trust_store", lambda: True)
        monkeypatch.setattr(downloader.sys, "platform", "darwin")
        menu = self._searched(monkeypatch, [], reason="cannot verify")
        assert menu._status_notes
        monkeypatch.setattr(downloader, "_no_trust_store", lambda: False)
        monkeypatch.setattr(downloader, "last_error", "")
        menu._query = "something else"
        menu._do_search()
        assert not any("Install Certificates" in line
                       for line in menu._status_notes)

    def test_the_lines_really_reach_the_screen(self, monkeypatch):
        """A sentence nothing blits is a feature that ships doing nothing,
        which this project has now shipped five times."""
        pygame.init()
        pygame.display.set_mode((900, 600))
        monkeypatch.setattr(downloader, "_no_trust_store", lambda: True)
        monkeypatch.setattr(downloader.sys, "platform", "darwin")
        menu = self._searched(monkeypatch, [], reason="cannot verify")
        with_lines = pygame.Surface((900, 600))
        menu.render(with_lines)
        menu._status_notes = []
        without = pygame.Surface((900, 600))
        menu.render(without)
        assert pygame.image.tostring(with_lines, "RGB") != \
            pygame.image.tostring(without, "RGB")
