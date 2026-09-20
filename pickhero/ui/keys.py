"""Reading a keyboard, for every screen that has one.

`shift_held` lived in `scrolling.py`, where it was written, and the song
list therefore went on asking `event.mod & KMOD_SHIFT` by hand -- the very
check that failed on the player's machine twice (`Shift+U` in the search
box, `Shift+C` in the song). A helper that closes a class of fault only
closes it where it can be reached, so it lives here and both import it.

No pygame drawing and no screen state, so importing it costs nothing.
"""

from __future__ import annotations

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
