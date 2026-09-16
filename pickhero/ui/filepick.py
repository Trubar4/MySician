"""Asking the operating system for a folder.

Its own module for the same reason the clipboard is: it is the one part
that cannot run without a desktop, and two screens should not each carry a
copy of the tkinter that does it.
"""

from __future__ import annotations


def pick_folder(title: str = "Choose a folder",
                start_dir: str | None = None) -> str:
    """A folder the player chose, or "" if they cancelled.

    "" for cancelled and "" for no tkinter, on purpose: neither is an error
    and the caller does the same thing about both.
    """
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
