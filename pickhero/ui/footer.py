"""The line of keyboard shortcuts along the bottom, for any screen with one.

Twenty-three shortcuts are 2986 px of text and the player's window is 1911,
so a single centred line is cut at BOTH ends -- the first entry and the
last, which is why a newly added key looked like a key that had never
shipped. The playing screen was fixed for that; the SONG LIST was not, and
its own hint line measured 2554 px before a single entry was added to it.

So the wrapping lives here rather than in either screen: one implementation
of "make the keys fit", reachable from both.
"""

from __future__ import annotations


def wrap_on_bars(line: str, font, width: int) -> list[str]:
    """Break one footer line into as many as it takes to fit `width`.

    At the "|" the entries already carry, so a shortcut is never split down
    the middle. A single entry wider than the screen is left alone -- there
    is nothing to be done about it here, and shortening the text is a
    decision for whoever wrote it.
    """
    if font.size(line)[0] <= width:
        return [line]
    out: list[str] = []
    current = ""
    for part in line.split("|"):
        candidate = part if not current else f"{current}|{part}"
        if current and font.size(candidate)[0] > width:
            out.append(current.strip())
            current = part
        else:
            current = candidate
    if current.strip():
        out.append(current.strip())
    return out or [line]


def wrap_words(line: str, font, width: int) -> list[str]:
    """Break one line of PROSE into as many as it takes to fit `width`.

    `wrap_on_bars` breaks at the "|" a footer carries, which a sentence does
    not have. The download screen's notes are sentences -- and a list of the
    fields a Songsterr revision holds, which is the longest of them and grew
    when it stopped truncating itself.

    Every one of them was blitted centred and unwrapped, so anything wider
    than the window lost BOTH ends. Fourth time in this project: the playing
    screen's footer, the sync panel, the completion overlay, this.

    A single word wider than the screen is left alone, the same answer
    `wrap_on_bars` gives a single entry: shortening the text is a decision
    for whoever wrote it.
    """
    if font.size(line)[0] <= width:
        return [line]
    out: list[str] = []
    current = ""
    for word in line.split(" "):
        candidate = word if not current else f"{current} {word}"
        if current and font.size(candidate)[0] > width:
            out.append(current)
            current = word
        else:
            current = candidate
    if current:
        out.append(current)
    return out or [line]
