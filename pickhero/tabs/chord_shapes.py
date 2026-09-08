"""The GRIP under a chord: which string on which fret, and what it is called.

The scrolling board says WHEN to play and the fret numbers say where, but a
chord is a shape the hand makes, and six numbers spread down six lanes is not
a shape. A diagram is -- which is why every songbook, every chord app and
Yousician draw one.

What the files actually carry, measured across the player's own songs:

- **The shape is always there.** Every note has a string and a fret, so the
  grid can be drawn from the tab itself. This is a reading of what is
  written, not an invention, which is the line `chords.py` already walks.
- **The NAME comes from the notes too**, via `chords.py`, which abstains
  rather than guesses. Measured: 100, 92 and 96 % of the chord moments in
  three real songs get a name, and each song contains only 3 to 13 distinct
  shapes -- so a song's whole vocabulary fits on one screen and is built
  once.
- **Which FINGER is not available and must not be drawn.** Two of the three
  songs carry no fingering at all, and the third gives a finger for 41 of
  120 positions (`finger="None"` for the rest). A diagram that colours the
  fingers on one song and greys them on the next teaches nothing; a plain
  dot is honest everywhere.
- A Guitar Pro file MAY carry the transcriber's own diagrams
  (`DiagramCollection`), which additionally know about muted and barred
  strings. One of the three songs has them. Not read here: a picture that is
  better on one song in three is worse than one that is the same everywhere,
  and the notes are the source both agree on.
"""

from __future__ import annotations

from dataclasses import dataclass

from pickhero.tabs.chords import name_chord
from pickhero.tabs.timeline import NoteEvent, Timeline

# Below this many strings at one instant there is no shape to show. A single
# note is a note; the board already says everything about it.
MIN_STRINGS = 2
# How many frets a diagram shows. Five is what a songbook draws and what fits
# every open shape without sliding the window.
FRETS_SHOWN = 5


@dataclass(frozen=True)
class ChordShape:
    """One grip, as a diagram would draw it.

    `frets` is (GP string number, fret) pairs, low string first -- a TUPLE
    rather than a dict so two readings of the same grip compare and hash as
    one thing, which is what `changes_in` needs to tell a repeated chord
    from a new one. String 6 is the low E; a string that is not in it was
    not written at that moment, which on a guitar means either "not struck"
    or "damped", and the tab does not distinguish.
    """

    name: str
    frets: tuple[tuple[int, int], ...]
    base_fret: int

    @property
    def strings(self) -> int:
        return len(self.frets)

    def fret_on(self, string: int) -> int | None:
        for s, fret in self.frets:
            if s == string:
                return fret
        return None

    def rows(self) -> list[tuple[int, int | None]]:
        """(string, fret) low string first, with None where nothing is written.

        Low string first, the order a guitarist reads a diagram and the
        reverse of the string NUMBERS -- string 6 is the low E.
        """
        return [(s, self.fret_on(s)) for s in range(6, 0, -1)]


def shape_of(notes: list[NoteEvent]) -> ChordShape | None:
    """The grip these simultaneous notes make, or None if there is not one.

    None for a single note, and for a set the namer will not name: a diagram
    with no name over it is a picture nobody can look up, and this module
    inherits `chords.py`'s refusal to guess.
    """
    frets = {n.string: n.fret for n in notes if not n.dead}
    if len(frets) < MIN_STRINGS:
        return None
    name = name_chord([n.midi_note for n in notes if not n.dead])
    if not name:
        return None
    fretted = [f for f in frets.values() if f > 0]
    # Where the shape sits up the neck, the diagram slides with it -- an
    # open-position grid with a dot on the twelfth fret is not a diagram.
    lowest = min(fretted) if fretted else 0
    base = lowest if lowest > FRETS_SHOWN else 0
    return ChordShape(
        name=name,
        frets=tuple(sorted(frets.items(), reverse=True)),
        base_fret=base,
    )


def shapes_in(timeline: Timeline) -> list[tuple[float, ChordShape]]:
    """Every moment in the song that has a grip, in order.

    Built once per song. The board is redrawn sixty times a second and this
    walks every note in the piece, which is exactly the kind of loop this
    project has already had to move out of a frame three times.
    """
    at: dict[int, list[NoteEvent]] = {}
    for note in timeline.notes:
        at.setdefault(int(round(note.timestamp_ms)), []).append(note)
    out = []
    for when in sorted(at):
        shape = shape_of(at[when])
        if shape is not None:
            out.append((float(when), shape))
    return out


def changes_in(timeline: Timeline) -> list[tuple[float, ChordShape]]:
    """The moments the hand has to MOVE, which is what a player watches for.

    A chord repeated over eight bars is one grip, not sixteen: showing it
    again at every strum is eight bars of noise, and it buries the moment
    that actually needs preparing.
    """
    out: list[tuple[float, ChordShape]] = []
    for when, shape in shapes_in(timeline):
        if out and out[-1][1] == shape:
            continue
        out.append((when, shape))
    return out
