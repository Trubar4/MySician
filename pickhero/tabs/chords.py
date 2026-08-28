"""Naming the chord under a group of notes.

Guitar Pro has a field for this and it is empty: 5601 beats in the player's
own tab, not one chord name. So the name has to come from the notes -- which
is a READING of what is written, not an invention, and that is the line this
module has to stay on the right side of.

It abstains rather than guesses. A name that is wrong now and then is worse
than no name: the player would learn to distrust the line, and then it is
worth nothing even when it is right. So:

- Two notes are a chord only when they are a POWER chord (root and fifth).
  Any other interval is an interval, and calling a third "C" would be a
  claim about a note nobody played.
- Three or more must match a known quality exactly, on pitch classes. A
  seventh chord may leave out its fifth -- guitarists do it constantly and
  the shape is unambiguous -- and nothing else may leave anything out.
- Where two readings fit, the one whose root is the LOWEST note wins, and if
  neither is, the chord is named over its bass ("G/B"). Both are what a
  player would call it.
"""

from __future__ import annotations

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Interval sets from the root, in semitones, and what to call them. Ordered:
# the first exact match wins, so the plainer reading comes first.
QUALITIES: tuple[tuple[frozenset[int], str], ...] = (
    (frozenset({0, 7}), "5"),
    (frozenset({0, 4, 7}), ""),
    (frozenset({0, 3, 7}), "m"),
    (frozenset({0, 3, 6}), "dim"),
    (frozenset({0, 4, 8}), "aug"),
    (frozenset({0, 2, 7}), "sus2"),
    (frozenset({0, 5, 7}), "sus4"),
    (frozenset({0, 4, 7, 10}), "7"),
    (frozenset({0, 4, 7, 11}), "maj7"),
    (frozenset({0, 3, 7, 10}), "m7"),
    (frozenset({0, 3, 7, 11}), "mMaj7"),
    (frozenset({0, 3, 6, 10}), "m7b5"),
    (frozenset({0, 3, 6, 9}), "dim7"),
    (frozenset({0, 4, 7, 9}), "6"),
    (frozenset({0, 3, 7, 9}), "m6"),
    (frozenset({0, 5, 7, 10}), "7sus4"),
    (frozenset({0, 2, 4, 7}), "add9"),
    (frozenset({0, 2, 3, 7}), "madd9"),
    (frozenset({0, 2, 4, 7, 10}), "9"),
    (frozenset({0, 2, 3, 7, 10}), "m9"),
    (frozenset({0, 2, 4, 7, 11}), "maj9"),
)

# Four-note qualities a guitarist routinely plays without the fifth. Only
# these: dropping a note from a triad leaves something that is not a triad,
# and naming it anyway is the guess this module refuses to make.
FIFTHLESS = frozenset({"7", "maj7", "m7", "mMaj7", "6", "m6"})


def name_chord(midi_notes) -> str | None:
    """What a player would call these notes sounding together, or None.

    None is a real answer and the commonest one on a solo: a run of single
    notes has no chord, and neither does a two-note fragment that is not a
    fifth.
    """
    pitches = sorted(set(int(n) for n in midi_notes))
    if len(pitches) < 2:
        return None
    classes = {p % 12 for p in pitches}
    bass = pitches[0] % 12

    if len(classes) == 1:
        return None                       # the same note in two octaves
    if len(classes) == 2:
        low, high = sorted(classes)
        for root, other in ((low, high), (high, low)):
            if (other - root) % 12 == 7:
                return f"{NOTE_NAMES[root]}5"
        return None

    best = _match(classes, bass, exact=True)
    if best is None:
        best = _match(classes, bass, exact=False)
    if best is None:
        return None
    root, suffix = best
    name = NOTE_NAMES[root] + suffix
    if bass != root:
        name += f"/{NOTE_NAMES[bass]}"
    return name


def _match(classes: set[int], bass: int, exact: bool):
    """(root, suffix) for the best reading of these pitch classes, or None."""
    found = []
    for root in classes:
        intervals = frozenset((c - root) % 12 for c in classes)
        for shape, suffix in QUALITIES:
            if intervals == shape:
                found.append((root, suffix))
                break
            if (not exact and suffix in FIFTHLESS
                    and intervals == shape - {7}):
                found.append((root, suffix))
                break
    if not found:
        return None
    # The reading whose root is the bass is what a player would call it.
    for root, suffix in found:
        if root == bass:
            return (root, suffix)
    return found[0]
