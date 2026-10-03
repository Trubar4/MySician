"""Searching the help page: type a key, or type a word.

*"Koennen wir in die Hilfe Schlagworte aufnehmen? Wenn ich eine Taste
eingebe, kommen die Befehle dieser Taste. Wenn ich ein Wort eingebe, wie
drill, kommen die Befehle dazu."*

Two questions, and the first one needs no text matching at all: **the page
already knows which keys each of its lines names**, because `clickable`
parses them so the mouse can press them. So a search for `N` asks that
parser rather than looking for the letter n, which would hit every line with
an n in it. One reader for the buttons and the search, so the search can
never offer a key the page does not itself treat as a key.

The second is a substring match, with a table of German words on top. The
help is written in English and the player is not, so "tempo" has to find
"practice speed" and "stimmung" has to find "tuning". **A synonym that
points at nothing is a search that silently finds nothing**, so the suite
asserts that every target below really appears on the page -- the same rule
as everything else here that is allowed to be a constant.

Arithmetic and no drawing, so it is tested without a screen, and because the
drawing and the keyboard must agree about what the results are.
"""

from __future__ import annotations

from dataclasses import dataclass

import pygame

from pickhero.ui import clickable

#: A one-letter query is a key and nothing else: matched as text it would
#: find most of the page.
MIN_WORD = 2

#: German (and short) search words, and the English text on the page they
#: should find. Matched by PREFIX, so "stimm" finds both the tuning and the
#: tuner, which is what typing one letter at a time wants.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "tempo": ("practice speed",),
    "geschwindigkeit": ("practice speed",),
    "langsamer": ("practice speed",),
    "stimmung": ("tuning",),
    "stimmgeraet": ("tuner",),
    "saite": ("string",),
    "saiten": ("string",),
    "bund": ("fret",),
    "buende": ("fret",),
    "schleife": ("loop",),
    "ueben": ("drill",),
    "uebung": ("drill",),
    # "record", not "recording": the key that switches it on says "recorded
    # backing", and a target that misses the key it is about is worse than
    # no synonym at all.
    "aufnahme": ("record",),
    # What the player calls the recording in every message he writes. The
    # page never says MP3 once.
    "mp3": ("record",),
    "lautstaerke": ("noise gate",),
    "pegel": ("noise gate",),
    "rauschen": ("noise gate",),
    "takt": ("bar",),
    "takte": ("bar",),
    "akkord": ("chord",),
    "akkorde": ("chord",),
    "griff": ("chord",),
    "griffe": ("chord",),
    "note": ("note",),
    "noten": ("note",),
    "fehler": ("mistake",),
    "durchgang": ("every run",),
    "durchgaenge": ("every run",),
    "laeufe": ("every run",),
    "statistik": ("every run",),
    "vergleich": ("Pick two",),
    # "chord view" was one of these and the suite caught it the hour the
    # line was reworded -- which is the whole point of asserting that every
    # target really appears on the page.
    "ansicht": ("tab page", "hybrid sheet"),
    "pause": ("pause",),
    "warten": ("wait mode",),
    "hilfe": ("help",),
    "farbe": ("colour",),
    "farben": ("colour",),
    "gruen": ("Green",),
    "rot": ("Red",),
    "gelb": ("Yellow",),
    "seite": ("tab page",),
    "groesse": ("note size",),
    "zoom": ("zoom",),
    "synchron": ("sync",),
    "latenz": ("timing offset",),
    "verzoegerung": ("timing offset",),
    "stumm": ("mute",),
    "protokoll": ("run log",),
    "gitarre": ("guitar",),
    "spur": ("track",),
    "spuren": ("track",),
    "daempfen": ("PALM MUTE", "DEAD NOTE"),
    "begleitung": ("backing",),
    "spulen": ("spool",),
    "markieren": ("mark a passage",),
    "kopieren": ("clipboard",),
    "thema": ("theme",),
    "trend": ("trend",),
}

#: What a German keyboard calls the control key.
_MOD_WORDS = {"shift": pygame.KMOD_SHIFT, "ctrl": pygame.KMOD_CTRL,
              "strg": pygame.KMOD_CTRL, "alt": pygame.KMOD_ALT}

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                          "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def fold(text: str) -> str:
    """Lower case with the umlauts spelled out.

    So "lautstärke" and "lautstaerke" are the same query -- a player types
    whichever his keyboard gives him first, and a search that answers only
    one of the two spellings looks broken half the time.
    """
    return (text or "").translate(_UMLAUTS).lower()


@dataclass(frozen=True)
class Hit:
    """One line of the help page that answers the query."""

    heading: str
    item: object        # the help item itself, so the drawing is unchanged
    size: str
    #: True where it matched because it NAMES the key that was typed.
    by_key: bool


def item_label(item) -> str:
    """The part of a help item the page makes clickable.

    A (label, value) item underlines keys in its label only, so that is
    where a key search has to look -- the value is the setting's state and
    a capital in it is not a shortcut.
    """
    if isinstance(item, str):
        return item
    if isinstance(item[0], tuple):
        return item[1]                              # a colour swatch
    return item[0]


def item_text(item) -> str:
    """A help item as plain text, value included.

    One reader for three item shapes, used by the search AND by
    `help_lines`, so the test that checks every bound key is written down
    cannot disagree with what is searched.
    """
    if isinstance(item, str):
        return item
    if isinstance(item[0], tuple):
        return item[1]
    return f"{item[0]}   {item[1]}"


def as_key(query: str) -> tuple[int, int, bool] | None:
    """(key, modifier, was a modifier typed) for a query naming a key.

    None where it names none. "n", "N", "shift+p", "Strg+S", "pgdn", "esc"
    and "f3" all name one; "drill" does not.
    """
    text = (query or "").strip()
    if not text:
        return None
    if text in clickable.PUNCT:
        return clickable.PUNCT[text], 0, False
    parts = text.split("+")
    mod = 0
    for piece in parts[:-1]:
        bit = _MOD_WORDS.get(piece.strip().lower())
        if bit is None:
            return None
        mod |= bit
    atom = parts[-1].strip()
    if not atom:
        return None
    # The page's own spelling, so `clickable` answers about it: SPACE, PgDn,
    # ESC are written as words and the letters as capitals.
    for name in clickable.NAMED:
        if name.lower() == atom.lower():
            return clickable.NAMED[name], mod, bool(mod)
    if len(atom) == 2 and atom[0] in "fF" and atom[1].isdigit():
        key = getattr(pygame, f"K_F{atom[1]}", None)
        return (key, mod, bool(mod)) if key else None
    if len(atom) == 1 and atom.isalpha():
        key = getattr(pygame, f"K_{atom.lower()}", None)
        return (key, mod, bool(mod)) if key else None
    return None


def _names_key(text: str, key: int, mod: int, exact: bool) -> bool:
    """Does this line name that key?

    A bare letter matches every modifier -- *"die Befehle dieser Taste"* is
    all of them, and N, Shift+N and Ctrl+N are three different commands on
    one key. A query that spells the modifier out means that one.

    `lead` is ON here where the DRAWING leaves it off, and the asymmetry is
    deliberate. The page must not underline a badge letter as a button --
    `X` is the dead note, `P` the pull-off -- but in a search a letter
    standing at the front of a line is what the player meant, and "N loops
    the next place that run went wrong" is a command on N however it is
    written. A false hit costs one line he can read; a miss costs a command
    he cannot find.
    """
    for link in clickable.spans(text, lead=True):
        if link.key != key:
            continue
        if not exact or link.mod == mod:
            return True
    return False


def _wanted(query: str) -> list[str]:
    """The folded strings a word query should look for.

    The query itself, plus whatever the synonym table says it means. A
    synonym matches on a PREFIX either way round: the query inside the word
    so that typing one letter at a time starts finding things, and the word
    inside the query so that a plural ("durchgaenge", "statistiken") does
    not have to be a row of its own.
    """
    folded = fold(query).strip()
    out = [folded]
    if len(folded) < MIN_WORD:
        return out
    for word, targets in SYNONYMS.items():
        if word.startswith(folded) or folded.startswith(word):
            out += [fold(t) for t in targets]
    return out


def search(query: str, blocks) -> list[Hit]:
    """Every help line that answers `query`, keys first, then words.

    A query that is BOTH a key and a word is answered as both, the way the
    song search reads a bare number as an id and as text: `TAB` is a key and
    "tab page" is a view, and answering only one of them makes the other
    unfindable.
    """
    text = (query or "").strip()
    if not text:
        return []
    named = as_key(text)
    wanted = [w for w in _wanted(text) if len(w) >= MIN_WORD]

    out: list[Hit] = []
    seen: set[tuple[str, str]] = set()
    for by_key in (True, False):
        if by_key and named is None:
            continue
        if not by_key and not wanted:
            continue
        for heading, items, size in blocks:
            for item in items:
                label, whole = item_label(item), item_text(item)
                mark = (heading, whole)
                if mark in seen:
                    continue
                if by_key:
                    key, mod, exact = named
                    if not _names_key(label, key, mod, exact):
                        continue
                else:
                    haystack = fold(f"{heading} {whole}")
                    if not any(w in haystack for w in wanted):
                        continue
                seen.add(mark)
                out.append(Hit(heading, item, size, by_key))
    return out
