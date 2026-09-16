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


def put_clipboard(text: str) -> bool:
    """Put text on the clipboard. True if it got there.

    *"Kannst du etwas bauen, damit ich Text am Screen mit der Maus markieren
    und kopieren kann oder wenigstens ein generelles Ctrl+C?"*

    Selecting with the mouse would mean laying out every string as
    characters with hit boxes, in a window whose whole job is drawing music.
    Copying the WHOLE screen costs one key and answers the same need: the
    player was reading a 200-character error off a photograph of his monitor
    and typing it back to be diagnosed.

    tkinter's clipboard is emptied when its interpreter is destroyed, so the
    window is kept alive for one `update()` after the append -- without it
    the copy appears to work and the paste comes back empty on Windows.
    """
    if not text:
        return False
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        try:
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()              # hand it to the window manager
        finally:
            root.destroy()
        return True
    except Exception:
        return False
