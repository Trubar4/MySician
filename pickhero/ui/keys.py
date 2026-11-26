"""Reading a keyboard, for every screen that has one.

`shift_held` lived in `scrolling.py`, where it was written, and the song
list therefore went on asking `event.mod & KMOD_SHIFT` by hand -- the very
check that failed on the player's machine twice (`Shift+U` in the search
box, `Shift+C` in the song). A helper that closes a class of fault only
closes it where it can be reached, so it lives here and both import it.

No pygame drawing and no screen state, so importing it costs nothing.
"""

from __future__ import annotations

import sys

import pygame


def shift_held(event) -> bool:
    """Was SHIFT down for this key press -- however the keyboard says so.

    Three signals, because one was not enough on the player's machine: a key
    arrived carrying a capital letter with no shift bit in `event.mod` at
    all, and the shortcut fell through to its unshifted twin. The event's own
    modifiers are the normal answer, the live keyboard state catches a stale
    one, and the CHARACTER catches the rest -- a capital letter is what was
    typed, whatever the layout did to say it.

    `pygame.key.get_mods()` needs the video system and raises without it, so
    it is guarded: a key handler that can raise takes the app down with it.
    """
    if getattr(event, "mod", 0) & pygame.KMOD_SHIFT:
        return True
    try:
        if pygame.key.get_mods() & pygame.KMOD_SHIFT:
            return True
    except pygame.error:
        pass
    letter = getattr(event, "unicode", "") or ""
    return len(letter) == 1 and letter.isalpha() and letter.isupper()


#: What counts as "the modifier the shortcuts are written with". On macOS
#: that is Cmd as well as Ctrl, because Cmd is what a Mac keyboard copies
#: with -- and `CLAUDE.md` said no mapping was needed on the grounds that
#: "the physical Ctrl key exists", which is reasoning rather than a
#: measurement and was overturned by the first report from that machine.
#:
#: Accepting it is strictly a repair rather than a new binding, because
#: today Cmd+C falls THROUGH to the plain `C` that raises the noise gate,
#: Cmd+Z toggles vsync and Cmd+S opens the sync panel -- the same shape as
#: `Shift+C` falling through to the gate, which this project has already
#: shipped and fixed once.
#:
#: Not on Windows or Linux: there the Meta key is the Windows/Super key and
#: it means something else entirely.
CTRL_MODS = pygame.KMOD_CTRL
if sys.platform == "darwin":
    CTRL_MODS |= pygame.KMOD_META


def ctrl_held(event) -> bool:
    """Was CTRL down for this key press -- or Cmd, on a Mac.

    Two signals rather than `shift_held`'s three: a modifier produces no
    character of its own, so there is nothing to read off `unicode`. The live
    keyboard state is guarded for the same reason as there -- a key handler
    that can raise takes the app down with it.
    """
    if getattr(event, "mod", 0) & CTRL_MODS:
        return True
    try:
        return bool(pygame.key.get_mods() & CTRL_MODS)
    except pygame.error:
        return False
