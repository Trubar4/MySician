"""Whatever is on the system clipboard.

Its own module because two screens need it and neither should have to
import the other: the player screen takes a Songsterr link off it (Ctrl+U),
and the search box takes one as text (Ctrl+V). The same tkinter the file
chooser already uses.
"""

from __future__ import annotations


def clipboard_text() -> str:
    """The clipboard's text, or "" when there is no way to ask.

    One seam because it is the one part of pasting that cannot run without
    a desktop -- no tkinter and an empty clipboard look the same from here,
    and the advice to the player is the same either way.
    """
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        try:
            return str(root.clipboard_get())
        finally:
            root.destroy()
    except Exception:
        return ""
