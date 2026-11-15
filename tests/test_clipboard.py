"""The two halves of the clipboard want opposite things from Tk.

*"Nachdem ich erstmals mit Str+C den HUD kopiert habe, geht es nicht mehr
aus dem Speicher in der App. Ich kann nichts anderes mehr einfuegen - wie
bspw. einen Songsterr Link. Ausserhalb der App gehen andere Str+C
Speicher."*

Tk does not copy text into the operating system -- it becomes the
clipboard's OWNER and serves the text when somebody asks. Every call here
used to build an interpreter and destroy it again, so:

- a WRITE left nobody to serve what had been copied, and
- a READ from an interpreter that owns the selection is answered out of
  Tk's own copy without the system being asked at all.

There is no tkinter and no desktop on the machine this was written on, so
what is pinned is the SHAPE -- which window each half uses -- through a
stand-in. The mechanism itself is only reproducible at the player's.
"""

from __future__ import annotations

import pytest

from pickhero.ui import clipboard


class FakeRoot:
    def __init__(self, tk):
        self._tk = tk
        self.destroyed = False
        tk.built.append(self)

    def withdraw(self):
        pass

    def clipboard_get(self):
        if self._tk.content is None:
            raise RuntimeError("CLIPBOARD selection doesn't exist")
        return self._tk.content

    def clipboard_clear(self):
        self._tk.content = ""

    def clipboard_append(self, text):
        self._tk.content = (self._tk.content or "") + text

    def update(self):
        self._tk.updates += 1

    def destroy(self):
        self.destroyed = True


class FakeTk:
    def __init__(self, content=None):
        self.content = content
        self.built: list[FakeRoot] = []
        self.updates = 0

    def Tk(self):
        return FakeRoot(self)


@pytest.fixture(autouse=True)
def _fresh():
    clipboard._owner = None
    clipboard.last_error = ""
    yield
    clipboard._owner = None
    clipboard.last_error = ""


@pytest.fixture
def tk(monkeypatch):
    fake = FakeTk()
    monkeypatch.setattr(clipboard, "_tk", lambda: fake)
    return fake


class TestTheWriteKeepsItsWindow:
    """Destroying it is what takes the text back off the clipboard."""

    def test_the_window_is_not_destroyed(self, tk):
        assert clipboard.put_clipboard("hello") is True
        assert len(tk.built) == 1
        assert tk.built[0].destroyed is False

    def test_a_second_copy_reuses_it(self, tk):
        clipboard.put_clipboard("one")
        clipboard.put_clipboard("two")
        assert len(tk.built) == 1          # not two interpreters
        assert tk.content == "two"

    def test_a_window_that_broke_is_replaced_rather_than_dead_for_ever(self, tk):
        clipboard.put_clipboard("one")

        def boom():
            raise RuntimeError("application has been destroyed")

        tk.built[0].update = boom
        assert clipboard.put_clipboard("two") is False
        assert "destroyed" in clipboard.last_error
        # The next press must still work, or the key is dead for the session.
        assert clipboard.put_clipboard("three") is True
        assert len(tk.built) == 2


class TestTheReadDoesNotUseThatWindow:
    """An interpreter that owns the selection answers out of its own copy."""

    def test_reading_builds_its_own_and_throws_it_away(self, tk):
        clipboard.put_clipboard("the HUD")
        owner = tk.built[0]
        tk.content = "https://www.songsterr.com/a/wsa/x-tab-s123"
        assert clipboard.clipboard_text().endswith("s123")
        assert len(tk.built) == 2
        assert tk.built[1].destroyed is True
        assert owner.destroyed is False

    def test_a_copy_does_not_make_later_pastes_read_it_back(self, tk):
        """The player's report, as a property."""
        clipboard.put_clipboard("Audio: ... the whole HUD ...")
        tk.content = "something else entirely"
        assert clipboard.clipboard_text() == "something else entirely"


class TestItSaysWhyRatherThanReturningNothing:
    """Three different problems were one empty string."""

    def test_no_desktop_is_named(self, monkeypatch):
        monkeypatch.setattr(clipboard, "_tk", lambda: None)
        assert clipboard.clipboard_text() == ""
        assert "tkinter" in clipboard.last_error
        assert clipboard.put_clipboard("x") is False
        assert "tkinter" in clipboard.last_error

    def test_an_empty_clipboard_is_named(self, tk):
        assert clipboard.clipboard_text() == ""
        assert clipboard.last_error

    def test_a_good_read_clears_the_reason(self, tk):
        clipboard.clipboard_text()
        assert clipboard.last_error
        tk.content = "a link"
        assert clipboard.clipboard_text() == "a link"
        assert clipboard.last_error == ""

    def test_copying_nothing_is_not_a_clipboard_fault(self, tk):
        assert clipboard.put_clipboard("") is False
        assert "nothing to copy" in clipboard.last_error
        assert tk.built == []
