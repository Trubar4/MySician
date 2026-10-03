"""Every keyboard shortcut on screen, as something the mouse can press.

*"Kann ich dafuer auch Tasten haben, die ich mit der Maus klicken kann in der
Fussleiste? Du kannst auch die Word underlinen, dann sind es "Links" bzw.
klickbare Buttons. Das koennten wir generell bei jedem Tastenkuerzel ueberall
machen."*

The footers, the status note and the help page are already the one place every
bound key is written down -- so the key NAME in that text is the only thing a
button needs. Nothing new has to be declared: this reads the lines the screens
already draw, underlines the key words in them, and hands a click back through
the screen's OWN `handle_event` as a synthetic key press. One dispatcher, so a
clicked key and a typed key cannot come to mean different things, and a
shortcut added later is clickable because it was written down.

What makes that safe is the grammar below being NARROW rather than clever. The
help page is full of capital letters that are not keys -- `X` is the dead-note
badge, `H` and `P` are the hammer-on and pull-off marks, `E` names a string --
so a lone letter counts only where the surrounding text says it is a key:
followed by a colon, carrying a modifier, slash-joined to another letter, or
leading a footer entry. The property the suite asserts is the one that matters:
every token this recognises in any footer or help line really is a bound key.

No drawing decisions and no screen state, so it can be tested without a
display -- and because two things read the same answer, the drawing and the
MOUSE, which must never disagree about where a word landed.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

import pygame

#: Keys whose label on screen is a word. A word is unambiguous wherever it
#: appears, so these need no colon and no modifier to count.
NAMED: dict[str, int] = {
    "SPACE": pygame.K_SPACE,
    "ENTER": pygame.K_RETURN,
    "RIGHT": pygame.K_RIGHT,
    "LEFT": pygame.K_LEFT,
    "DOWN": pygame.K_DOWN,
    "HOME": pygame.K_HOME,
    "PgUp": pygame.K_PAGEUP,
    "PgDn": pygame.K_PAGEDOWN,
    "END": pygame.K_END,
    "ESC": pygame.K_ESCAPE,
    "DEL": pygame.K_DELETE,
    "TAB": pygame.K_TAB,
    "UP": pygame.K_UP,
}

#: The punctuation keys that appear as themselves: `+/-` for the size and
#: `,/.` for the timing nudge. They are only ever read inside a group that
#: says what they are -- a bare hyphen in prose is not a key.
PUNCT: dict[str, int] = {
    "+": pygame.K_PLUS,
    "-": pygame.K_MINUS,
    ",": pygame.K_COMMA,
    ".": pygame.K_PERIOD,
}

MODS: dict[str, int] = {
    "Shift+": pygame.KMOD_SHIFT,
    "Ctrl+": pygame.KMOD_CTRL,
    "Alt+": pygame.KMOD_ALT,
}

#: How far outside the word a click still counts. A single underlined letter
#: is nine pixels of type, which is a button nobody can hit.
PAD_X = 5

#: The separators a line of shortcuts is built out of. The playing screen uses
#: the bar, the stats overlay the dot; either way what sits between two of
#: them is ONE entry, whose key leads it.
SEPARATORS = "|·"

# Longest first, so SPACE is not read as S and DOWN not as D.
_ATOMS = "|".join([*sorted(NAMED, key=len, reverse=True),
                   "F[1-9]", "[A-Z]", r"[-+,.]"])
_MOD = "|".join(re.escape(m) for m in MODS)
_TOKEN = rf"(?:(?:{_MOD})*(?:{_ATOMS}))"
#: A group is one key or several joined by slashes (`I/O`, `Ctrl+PgUp/PgDn`).
#: It may not start or end inside a word, which is what keeps the M of "MIDI"
#: and the hyphen of "12-14" out.
# ...and neither end of it may run into a hyphen: "RIGHT-drag the strip" is
# a mouse button and not the key that seeks forward a beat, and "F1-F6" is
# a range rather than two buttons with four missing keys between them.
_GROUP = re.compile(
    rf"(?<![\w+-]){_TOKEN}(?:\s?/\s?{_TOKEN})*(?![\w+]|-\w)")


@dataclass(frozen=True)
class Link:
    """One key word in a line: where it sits, and what pressing it means."""

    start: int
    end: int
    key: int
    mod: int


def _atom(text: str) -> int | None:
    """The pygame key a single atom names, or None if it names nothing."""
    if text in NAMED:
        return NAMED[text]
    if text in PUNCT:
        return PUNCT[text]
    if len(text) == 2 and text[0] == "F" and text[1].isdigit():
        return getattr(pygame, f"K_F{text[1]}", None)
    if len(text) == 1 and text.isupper() and text.isalpha():
        return getattr(pygame, f"K_{text.lower()}", None)
    return None


def _split(token: str) -> tuple[int, str]:
    """A token's modifier mask and the atom left under it."""
    mod = 0
    while True:
        for name, bit in MODS.items():
            if token.startswith(name):
                mod |= bit
                token = token[len(name):]
                break
        else:
            return mod, token


def spans(text: str, lead: bool = False) -> list[Link]:
    """Every key word in `text`, in the order it is written.

    `lead` is for a footer ENTRY, where the convention is that the key comes
    first: "F or /: search" and "N or a click: sort" are keys by position.
    It is deliberately off for prose, where the help page's badge letters sit
    at the start of a line and mean something else entirely.
    """
    out: list[Link] = []
    for match in _GROUP.finditer(text):
        group = match.group(0)
        parts, at, places = group.split("/"), 0, []
        for part in parts:
            start = group.index(part.strip(), at) if part.strip() else at
            places.append((start, start + len(part.strip())))
            at = start + len(part.strip())
        tokens = [_split(p.strip()) for p in parts]
        if not tokens or any(_atom(atom) is None for _, atom in tokens):
            continue
        # A modifier written once in front of a slashed pair belongs to both:
        # "Ctrl+PgUp/PgDn" is two Ctrl presses, and reading the second as a
        # plain PgDn would END the drill the first one steps.
        if tokens[0][0]:
            tokens = [(mod or tokens[0][0], atom) for mod, atom in tokens]
        named = all(mod or atom in NAMED for mod, atom in tokens)
        letters = len(tokens) > 1 and all(len(atom) == 1 for _, atom in tokens)
        colon = text[match.end():match.end() + 1] == ":"
        # Leading whitespace does not stop an entry's key from LEADING it:
        # a line of entries is split at its separators and the pieces keep
        # the spaces around them.
        first = len(text) - len(text.lstrip())
        if not (colon or named or letters or group == "+/-"
                or (lead and match.start() == first)):
            continue
        for (start, end), (mod, atom) in zip(places, tokens):
            out.append(Link(match.start() + start, match.start() + end,
                            _atom(atom), mod))
    return out


def press(key: int, mod: int = 0) -> pygame.event.Event:
    """The key press a click stands for.

    A real event, handed to the screen's own handler, because the alternative
    is a second dispatcher -- and two answers to "what does this key do" is
    the fault this project has paid for at the repeats, at the transpose and
    at the shifted shortcuts.
    """
    char = ""
    if 32 <= key < 127 and not mod & (pygame.KMOD_CTRL | pygame.KMOD_ALT):
        char = chr(key)
        # `shift_held` reads the CHARACTER as one of its three signals, so an
        # unshifted letter must not arrive in capitals.
        char = char.upper() if mod & pygame.KMOD_SHIFT else char.lower()
    return pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod,
                              unicode=char, scancode=0)


def release(key: int) -> pygame.event.Event:
    """The matching key-up.

    Two handlers in this app wait for a finger to come off -- `Shift+S` so a
    key repeat cannot take a second sync point, and `DEL` so one held key
    cannot delete a folder of songs. A click that never lets go would arm
    them for ever.
    """
    return pygame.event.Event(pygame.KEYUP, key=key, mod=0, scancode=0)


@functools.lru_cache(maxsize=256)
def _rule(width: int, colour: tuple) -> pygame.Surface:
    """The underline, as a surface rather than a drawn line.

    Blitting is the one thing every caller here can do: the footer's own
    tests hand this function a recorder that is not a real Surface, and a
    display is not always up. Cached because a frame underlines a dozen
    words and they are all the same few widths.
    """
    rule = pygame.Surface((max(1, width), 1))
    rule.fill(colour)
    return rule


class Links:
    """Where every key word was drawn this frame, newest on top."""

    def __init__(self) -> None:
        self._items: list[tuple[pygame.Rect, int, int]] = []

    def clear(self) -> None:
        self._items.clear()

    def add(self, rect: pygame.Rect, key: int, mod: int) -> None:
        self._items.append((rect, key, mod))

    def at(self, pos) -> tuple[int, int] | None:
        """The key under `pos`, or None. Reversed: what was drawn last is on
        top, which is what the player sees and therefore what they aimed at."""
        for rect, key, mod in reversed(self._items):
            if rect.collidepoint(pos):
                return key, mod
        return None

    def __len__(self) -> int:
        return len(self._items)


def blit_centred(surface: pygame.Surface, font, text: str, y: int,
                 width: int, colour, links: "Links | None" = None,
                 sep: str | None = "|") -> None:
    """A centred line of shortcuts, every key in it a button.

    The shape every list screen in this app draws its hint in -- the device
    list, the settings, the downloader. One helper rather than five copies,
    which is the fault this project has paid for at the repeats and at the
    shifted keys.
    """
    blit(surface, font, text, width // 2 - font.size(text)[0] // 2, y,
         colour, links, sep=sep)


def blit(surface: pygame.Surface, font, text: str, x: int, y: int, colour,
         links: Links | None = None, lead: bool = False,
         sep: str | None = None) -> int:
    """Draw `text`, underline the keys in it, record where they landed.

    Returns the width it took, so a caller that centres a line can go on
    doing so. With `sep` the text is a LIST of entries -- each one is parsed
    with its key leading, and the separators themselves are plain text.
    """
    if sep:
        at = x
        for n, part in enumerate(re.split(f"([{re.escape(sep)}])", text)):
            at += blit(surface, font, part, at, y, colour, links,
                       lead=part not in sep)
        return at - x
    found = spans(text, lead=lead)
    if not found:
        drawn = font.render(text, True, colour)
        surface.blit(drawn, (x, y))
        return drawn.get_width()
    at, left = 0, x
    for link in found:
        for piece, is_key in ((text[at:link.start], False),
                              (text[link.start:link.end], True)):
            if not piece:
                continue
            drawn = font.render(piece, True, colour)
            surface.blit(drawn, (left, y))
            w, h = drawn.get_width(), drawn.get_height()
            if is_key:
                surface.blit(_rule(w, tuple(colour)), (left, y + h - 2))
                if links is not None:
                    links.add(pygame.Rect(left - PAD_X, y, w + 2 * PAD_X, h),
                              link.key, link.mod)
            left += w
        at = link.end
    if at < len(text):
        drawn = font.render(text[at:], True, colour)
        surface.blit(drawn, (left, y))
        left += drawn.get_width()
    return left - x
