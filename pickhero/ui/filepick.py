"""Asking the operating system for a folder.

Its own module for the same reason the clipboard is: it is the one part
that cannot run without a desktop, and two screens should not each carry a
copy of the tkinter that does it.

**On macOS Tk cannot run in this process at all** -- it aborts the whole app
on a selector SDL's `NSApp` does not have, which no `except` can catch -- so
there the system's own chooser is asked through `osascript`. See `macos.py`.
"""

from __future__ import annotations


def pick_folder(title: str = "Choose a folder",
                start_dir: str | None = None) -> str:
    """A folder the player chose, or "" if they cancelled.

    "" for cancelled and "" for no tkinter, on purpose: neither is an error
    and the caller does the same thing about both.
    """
    try:
        from pickhero.ui import macos
        if macos.is_mac():
            return macos.choose_folder(title, start_dir)
    except Exception:
        return ""
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        try:
            chosen = filedialog.askdirectory(title=title,
                                             initialdir=start_dir or None)
        finally:
            root.destroy()
        return str(chosen or "")
    except Exception:
        return ""
