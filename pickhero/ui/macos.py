"""Asking macOS for the clipboard and for a file, without Tk.

**Tk and SDL cannot share a process on macOS, and the failure is not
catchable.** The player's own stack trace:

    *** Terminating app due to uncaught exception 'NSInvalidArgumentException',
        reason: '-[SDLApplication macOSVersion]: unrecognized selector'
        5  libtk8.6.dylib  GetRGBA
        6  libtk8.6.dylib  TkpGetColor
        ...
        19 _tkinter...so   _tkinter_create

pygame has already made SDL's own `NSApplication` subclass the one and only
`NSApp`. Tk then asks `NSApp` for `macOSVersion`, which only Tk's own
`TKApplication` implements -- so the Objective-C runtime raises, nothing in
Python is in the call stack to catch it, and `abort()` follows. Every
`try/except Exception` wrapped round these calls is decoration there.

Nor is it an ordering problem: whichever toolkit installs `NSApp` first, the
other one's selectors are missing on it. That half is reasoning rather than
measurement -- what is measured is that Tk second aborts.

So on macOS the three things Tk was for are asked of the system directly:

- **`pbcopy` really writes the bytes to the pasteboard**, which is the one
  thing Tk never did (it registers as the OWNER and serves the text on
  demand, which is why destroying its interpreter took the text back off the
  clipboard -- see `clipboard.py`). So there is no window to keep alive and
  no owner to be, and the read cannot come back with this app's own last
  copy.
- **`osascript` opens the system's own chooser**, in its own process, so it
  comes up in front without the `-topmost` hack Tk needed on Windows.

The subprocess is the first in `pickhero/`, and the reason `build_info`
refuses one does not apply: that is asked while the window is coming up,
where this is a keypress that is already going to block on a human.
"""

from __future__ import annotations

import subprocess
import sys

#: How long a command that does NOT wait for a person may take. A chooser
#: has no timeout at all, because the player is browsing in it.
QUICK_S = 5.0


class ClipboardFailed(Exception):
    """pbcopy or pbpaste could not be run, or came back non-zero."""


def is_mac() -> bool:
    return sys.platform == "darwin"


def _script(source: str) -> str:
    """Run one AppleScript and hand back its single line of output.

    "" for cancelled, "" for anything that went wrong: the callers treat the
    two the same, and a cancel really is a non-zero exit (-128).
    """
    try:
        done = subprocess.run(["osascript", "-e", source],
                              capture_output=True, text=True)
    except (OSError, ValueError):
        return ""
    if done.returncode != 0:
        return ""
    return done.stdout.strip()


def _quoted(text: str) -> str:
    """One AppleScript string literal.

    A song's own name reaches this, and a name holding a quote would end the
    literal and leave the rest of it as script. Escaping the backslash first
    is the order that matters.
    """
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _at(start_dir: str | None) -> str:
    if not start_dir:
        return ""
    return f" default location (POSIX file {_quoted(start_dir)})"


def paste() -> str:
    """Whatever is on the pasteboard, as text. "" and `last_error` otherwise."""
    try:
        done = subprocess.run(["pbpaste"], capture_output=True,
                              timeout=QUICK_S)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ClipboardFailed(f"{type(exc).__name__}: {exc}") from exc
    if done.returncode != 0:
        raise ClipboardFailed("pbpaste failed")
    return done.stdout.decode("utf-8", errors="replace")


def copy(text: str) -> None:
    """Put text on the pasteboard. Raises `ClipboardFailed` if it did not."""
    try:
        done = subprocess.run(["pbcopy"], input=text.encode("utf-8"),
                              capture_output=True, timeout=QUICK_S)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ClipboardFailed(f"{type(exc).__name__}: {exc}") from exc
    if done.returncode != 0:
        raise ClipboardFailed("pbcopy failed")


def choose_file(title: str, start_dir: str | None = None,
                extensions: tuple[str, ...] = ()) -> str:
    """A file the player chose, or "" if they cancelled.

    `extensions` are bare suffixes ("mp3"). AppleScript's `of type` takes
    them without the dot; given none it offers everything, which is what the
    Tk dialog's "All files" row was for.
    """
    kinds = ""
    if extensions:
        listed = ", ".join(_quoted(ext.lstrip(".")) for ext in extensions)
        kinds = f" of type {{{listed}}}"
    return _script("POSIX path of (choose file with prompt "
                   f"{_quoted(title)}{kinds}{_at(start_dir)})")


def choose_folder(title: str, start_dir: str | None = None) -> str:
    """A folder the player chose, or "" if they cancelled."""
    return _script("POSIX path of (choose folder with prompt "
                   f"{_quoted(title)}{_at(start_dir)})")
