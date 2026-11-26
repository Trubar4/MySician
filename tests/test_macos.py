"""Asking macOS instead of Tk, and never asking Tk there.

The fault these hold shut is not a wrong answer, it is an ABORT: Tk asks
`NSApp` for `macOSVersion`, pygame has already made SDL's own subclass the
one and only `NSApp`, and the Objective-C runtime raises with no Python
frame to catch it. So the property worth asserting is that on darwin the
three call sites never reach tkinter at all -- which is why the fixture
below puts a tkinter in `sys.modules` that explodes on being touched
rather than trusting a reading of the source.
"""

from __future__ import annotations

import subprocess
import sys
import types

import pytest

from pickhero.audio import mp3_playback
from pickhero.ui import clipboard, filepick, macos


class _Exploding(types.ModuleType):
    """A tkinter that fails the way the real one does on macOS: fatally."""

    def __getattr__(self, name):
        raise AssertionError("tkinter was touched: %s" % name)


@pytest.fixture
def on_a_mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "tkinter", _Exploding("tkinter"))
    monkeypatch.setattr(clipboard, "_owner", None)
    monkeypatch.setattr(clipboard, "last_error", "")
    return monkeypatch


class _Run:
    """Stands in for `subprocess.run`, recording what was asked."""

    def __init__(self, out: str = "", code: int = 0, raises=None):
        self.out = out
        self.code = code
        self.raises = raises
        self.calls: list[list[str]] = []
        self.stdin: bytes | None = None

    def __call__(self, argv, **kw):
        if self.raises is not None:
            raise self.raises
        self.calls.append(list(argv))
        self.stdin = kw.get("input")
        done = types.SimpleNamespace(returncode=self.code)
        if kw.get("text"):
            done.stdout, done.stderr = self.out, ""
        else:
            done.stdout, done.stderr = self.out.encode("utf-8"), b""
        return done

    @property
    def script(self) -> str:
        """The AppleScript of the last call."""
        return self.calls[-1][-1]


class TestTheThreeCallSitesNeverReachTk:
    """The whole point: on darwin not one of them may touch tkinter."""

    def test_the_folder_chooser_asks_osascript(self, on_a_mac):
        run = _Run("/Users/p/Music\n")
        on_a_mac.setattr(subprocess, "run", run)
        assert filepick.pick_folder("Songs", "/Users/p") == "/Users/p/Music"
        assert run.calls[-1][0] == "osascript"

    def test_the_audio_chooser_asks_osascript(self, on_a_mac):
        run = _Run("/Users/p/take.mp3\n")
        on_a_mac.setattr(subprocess, "run", run)
        assert mp3_playback.pick_audio_file("/Users/p") == "/Users/p/take.mp3"
        assert run.calls[-1][0] == "osascript"

    def test_reading_the_clipboard_asks_pbpaste(self, on_a_mac):
        run = _Run("https://www.songsterr.com/a/wsa/x-s123")
        on_a_mac.setattr(subprocess, "run", run)
        assert clipboard.clipboard_text().endswith("s123")
        assert run.calls[-1] == ["pbpaste"]

    def test_writing_the_clipboard_asks_pbcopy(self, on_a_mac):
        run = _Run()
        on_a_mac.setattr(subprocess, "run", run)
        assert clipboard.put_clipboard("hello") is True
        assert run.calls[-1] == ["pbcopy"]
        assert run.stdin == b"hello"

    def test_and_keeps_no_owner_window(self, on_a_mac):
        """Tk needed one because it only PROMISES to hand the text over.

        pbcopy writes the bytes, so there is nothing to own -- and an owner
        is exactly what made a read answer out of this app's own copy.
        """
        on_a_mac.setattr(subprocess, "run", _Run())
        clipboard.put_clipboard("hello")
        assert clipboard._owner is None

    def test_a_write_then_a_read_goes_to_the_system(self, on_a_mac):
        """The bug the Tk version shipped: a copy made later pastes back."""
        board = {"text": ""}

        def run(argv, **kw):
            if argv == ["pbcopy"]:
                board["text"] = kw["input"].decode("utf-8")
                return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
            return types.SimpleNamespace(
                returncode=0, stdout=board["text"].encode("utf-8"), stderr=b"")

        on_a_mac.setattr(subprocess, "run", run)
        clipboard.put_clipboard("ours")
        board["text"] = "copied in the browser since"
        assert clipboard.clipboard_text() == "copied in the browser since"


class TestEverywhereElseIsUntouched:

    def test_the_folder_chooser_still_uses_tk_on_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(subprocess, "run",
                            _Run(raises=AssertionError("asked the shell")))
        # No tkinter here, so "" is the honest answer -- what matters is that
        # it did not shell out.
        assert filepick.pick_folder() == ""

    def test_is_mac_is_the_only_switch(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert macos.is_mac() is False
        monkeypatch.setattr(sys, "platform", "darwin")
        assert macos.is_mac() is True


class TestAQuoteInANameDoesNotBecomeScript:
    """A song's own name reaches the AppleScript source."""

    def test_a_quote_is_escaped(self):
        assert macos._quoted('say "hi"') == '"say \\"hi\\""'

    def test_and_a_backslash_first(self):
        assert macos._quoted("a\\b") == '"a\\\\b"'

    def test_so_a_hostile_name_stays_one_literal(self, monkeypatch):
        run = _Run("/ok")
        monkeypatch.setattr(subprocess, "run", run)
        macos.choose_folder('" & (do shell script "rm x") & "')
        # Every quote in the script is either one of the two delimiters or
        # escaped: nothing closed the literal early.
        body = run.script
        unescaped = body.count(chr(34)) - body.count(chr(92) + chr(34))
        assert unescaped == 2          # exactly the one literal's delimiters
        assert "do shell script" in body            # as TEXT, not as script
        assert body.startswith("POSIX path of (choose folder with prompt")


class TestWhatTheScriptAsksFor:

    def test_the_extensions_become_a_type_list(self, monkeypatch):
        run = _Run("/x.mp3")
        monkeypatch.setattr(subprocess, "run", run)
        macos.choose_file("T", None, ("mp3", ".ogg"))
        assert 'of type {"mp3", "ogg"}' in run.script

    def test_no_extensions_offers_everything(self, monkeypatch):
        run = _Run("/x")
        monkeypatch.setattr(subprocess, "run", run)
        macos.choose_file("T")
        assert "of type" not in run.script

    def test_the_start_folder_is_a_posix_file(self, monkeypatch):
        run = _Run("/x")
        monkeypatch.setattr(subprocess, "run", run)
        macos.choose_folder("T", "/Users/p/songs")
        assert 'default location (POSIX file "/Users/p/songs")' in run.script

    def test_and_is_left_out_when_there_is_none(self, monkeypatch):
        run = _Run("/x")
        monkeypatch.setattr(subprocess, "run", run)
        macos.choose_folder("T", None)
        assert "default location" not in run.script

    def test_a_posix_path_is_asked_for_not_an_alias(self, monkeypatch):
        """Without it AppleScript answers `alias Macintosh HD:Users:...`."""
        run = _Run("/x")
        monkeypatch.setattr(subprocess, "run", run)
        macos.choose_file("T")
        assert run.script.startswith("POSIX path of (")


class TestCancelledIsNotAnError:

    def test_a_cancelled_chooser_is_empty(self, monkeypatch):
        # osascript exits -128 when the player cancels.
        monkeypatch.setattr(subprocess, "run", _Run("", code=1))
        assert macos.choose_file("T") == ""
        assert macos.choose_folder("T") == ""

    def test_and_so_the_callers_see_nothing(self, on_a_mac):
        on_a_mac.setattr(subprocess, "run", _Run("", code=1))
        assert filepick.pick_folder() == ""
        assert mp3_playback.pick_audio_file() is None

    def test_no_osascript_at_all_is_also_empty(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run",
                            _Run(raises=FileNotFoundError("osascript")))
        assert macos.choose_folder("T") == ""


class TestAFailureSaysWhy:
    """`last_error` is what the next screenshot has to carry."""

    def test_pbpaste_failing_is_reported_not_swallowed(self, on_a_mac):
        on_a_mac.setattr(subprocess, "run", _Run("", code=1))
        assert clipboard.clipboard_text() == ""
        assert "pbpaste" in clipboard.last_error

    def test_pbcopy_failing_is_reported(self, on_a_mac):
        on_a_mac.setattr(subprocess, "run", _Run("", code=1))
        assert clipboard.put_clipboard("x") is False
        assert "pbcopy" in clipboard.last_error

    def test_a_missing_tool_names_itself(self, on_a_mac):
        on_a_mac.setattr(subprocess, "run",
                         _Run(raises=FileNotFoundError("pbcopy")))
        assert clipboard.put_clipboard("x") is False
        assert "pbcopy" in clipboard.last_error

    def test_an_empty_clipboard_says_so(self, on_a_mac):
        on_a_mac.setattr(subprocess, "run", _Run(""))
        assert clipboard.clipboard_text() == ""
        assert clipboard.last_error == "the clipboard is empty"

    def test_and_a_good_read_clears_it(self, on_a_mac):
        on_a_mac.setattr(subprocess, "run", _Run("x"))
        clipboard.last_error = "stale"
        assert clipboard.clipboard_text() == "x"
        assert clipboard.last_error == ""
