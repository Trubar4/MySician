"""Whatever is on the system clipboard.

Its own module because two screens need it and neither should have to
import the other: the player screen takes a Songsterr link off it (Ctrl+U),
and the search box takes one as text (Ctrl+V). The same tkinter the file
chooser already uses -- except on macOS, where see below.

**The two halves want opposite things from the interpreter**, which is the
bug the player reported: *"Nachdem ich erstmals mit Str+C den HUD kopiert
habe, geht es nicht mehr aus dem Speicher in der App. Ich kann nichts
anderes mehr einfuegen."*

- A WRITE has to leave an interpreter alive. Tk does not copy the text into
  the operating system; it registers itself as the clipboard's owner and
  hands the text over when somebody asks for it. Destroy the interpreter and
  there is nobody left to ask -- which is both halves of his report at once:
  the text never reaches another app, and this one reads back nothing.
  `root.update()` was put here for that and is not enough, because the ask
  comes whenever the other app feels like it, not before the next line runs.
- A READ has to NOT own the selection. Where Tk owns it, `clipboard_get`
  answers out of its own copy instead of going to the system, so the app
  would read back its own last copy for ever however many times the player
  copied something else.

So the write keeps one hidden window for the life of the process and the
read builds a throwaway one, and neither is the other's leftover.

**On macOS none of that may run at all, and the failure is not catchable.**
Tk asks `NSApp` for `macOSVersion`; pygame has already made SDL's own
application subclass the one and only `NSApp`, the Objective-C runtime
raises `NSInvalidArgumentException` with no Python frame to catch it, and the
process aborts -- so the `except Exception` below is decoration there. There
`macos.py` runs `pbcopy`/`pbpaste` instead, which really write the bytes to
the pasteboard and so need no owner window and cannot read back this app's
own copy. The whole dance above is Tk's alone.

None of this is reproducible on the machine it was written on -- there is no
tkinter and no desktop here -- so the reason is REPORTED rather than
swallowed into an empty string. `last_error` is what the next screenshot
has to carry: "no clipboard support", "the clipboard is empty" and "the
clipboard holds something that is not text" are three different problems and
were one return value.
"""

from __future__ import annotations

#: Why the last call came back with nothing. "" when it did not.
last_error = ""

#: The hidden window that owns what this app has copied. Never destroyed:
#: destroying it is what takes the text back off the clipboard. Stays None
#: on macOS, where there is nothing to own.
_owner = None


def _mac():
    """The macOS pasteboard module, or None anywhere else."""
    try:
        from pickhero.ui import macos
    except Exception:                      # pragma: no cover - importable here
        return None
    return macos if macos.is_mac() else None


def _tk():
    """tkinter, or None where there is no desktop to talk to."""
    try:
        import tkinter
        return tkinter
    except Exception:                      # pragma: no cover - no desktop here
        return None


def clipboard_text() -> str:
    """The clipboard's text, or "" -- and then `last_error` says why.

    A FRESH interpreter every time, deliberately. One that has copied
    something owns the selection, and Tk answers an owned selection out of
    its own copy without asking the system at all -- so reusing the window
    `put_clipboard` keeps alive would hand back this app's own last copy for
    ever, whatever the player had copied since.
    """
    global last_error
    macos = _mac()
    if macos is not None:
        try:
            text = macos.paste()
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}".strip() or "pbpaste failed"
            return ""
        last_error = "" if text else "the clipboard is empty"
        return text
    tkinter = _tk()
    if tkinter is None:
        last_error = "no clipboard support on this machine (tkinter missing)"
        return ""
    root = None
    try:
        root = tkinter.Tk()
        root.withdraw()
        text = str(root.clipboard_get())
    except Exception as exc:
        # TclError for an empty clipboard and for one holding an image, and
        # they are not the same problem. Narrowing it further would mean
        # naming Tcl's own wording, which moves between versions.
        last_error = f"{type(exc).__name__}: {exc}".strip() or "empty"
        return ""
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
    last_error = "" if text else "the clipboard is empty"
    return text


def put_clipboard(text: str) -> bool:
    """Put text on the clipboard. True if it got there.

    *"Kannst du etwas bauen, damit ich Text am Screen mit der Maus markieren
    und kopieren kann oder wenigstens ein generelles Ctrl+C?"*

    Selecting with the mouse would mean laying out every string as
    characters with hit boxes, in a window whose whole job is drawing music.
    Copying the WHOLE screen costs one key and answers the same need: the
    player was reading a 200-character error off a photograph of his monitor
    and typing it back to be diagnosed.

    The window is kept for the life of the process rather than destroyed
    after an `update()`. Tk serves the clipboard on demand from the owning
    interpreter, and "on demand" is whenever the other application asks --
    which is after this function has returned, after the next copy, and after
    the player has walked to the browser. One `update()` only covers whoever
    happened to ask inside it.
    """
    global last_error, _owner
    if not text:
        last_error = "nothing to copy"
        return False
    macos = _mac()
    if macos is not None:
        try:
            macos.copy(text)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}".strip() or "pbcopy failed"
            return False
        last_error = ""
        return True
    tkinter = _tk()
    if tkinter is None:
        last_error = "no clipboard support on this machine (tkinter missing)"
        return False
    try:
        if _owner is None:
            _owner = tkinter.Tk()
            _owner.withdraw()
        _owner.clipboard_clear()
        _owner.clipboard_append(text)
        _owner.update()            # hand it over to whoever is asking now
    except Exception as exc:
        # A window torn down by something else (the display going away, a
        # file chooser's own interpreter) must not make the key dead for the
        # rest of the session, so the next press builds a new one.
        _owner = None
        last_error = f"{type(exc).__name__}: {exc}".strip() or "copy failed"
        return False
    last_error = ""
    return True
