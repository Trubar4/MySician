"""One easier reading of a song, made only of notes that were already there.

*"Kann man Tabs vereinfachen, ohne dass sie wirklich schlechter klingen beim
mitspielen?"* — measured across the player's own six songs before anything
was written, and the answer is that there are TWO axes and only one of them
is cheap:

| | notes | picks | notes/pick | octave doublings | on the beat |
|---|---|---|---|---|---|
| Godsmack, "Awake" | 2561 | 883 | **2.90** | **34 %** | 35 % |
| 4 Non Blondes | 480 | 280 | 1.71 | 21 % | 39 % |
| Bon Jovi, "I'd Die For You" | 746 | 488 | 1.53 | 5 % | 50 % |
| Kid Rock | 490 | 444 | 1.10 | 5 % | 75 % |
| Thunder, "Love Walked In" | 167 | 163 | **1.02** | **1 %** | 29 % |

**An octave doubling is a pitch class a LOWER string is already sounding at
that same moment.** Dropping one removes a note and **not one pick**: the
picking hand does exactly what it did, the fretting hand holds a smaller
shape. On the metal song that is 871 notes gone and 883 strums unchanged.

**The fifth is NOT that, and reading it as one was the first mistake here.**
Measured together they came to 60 % and looked like a free lunch; a power
chord without its fifth is a single note. `chord_verify` cannot confirm
either of them — their partials are a subset of a lower note's — but "the
app cannot hear it" and "the ear cannot hear it" are different claims, and
only the first is established. So only the octaves go.

**And the rhythm is untouched on purpose.** Thunder's solo is 1.02 notes a
pick: there is no fat on it, and the only way to make it easier is to take
PICKS away, which is a different arrangement rather than a reduction of this
one. That is what Yousician's `basic` is, hand-arranged, and it is not this.

Arithmetic and nothing else -- no file reading, no pygame -- so the four
properties below are asserted on bare timelines.
"""

from __future__ import annotations

from collections import defaultdict

from pickhero.tabs.timeline import Timeline


def _by_onset(notes) -> dict:
    """Notes grouped by the moment the hand strikes them."""
    groups: dict[float, list] = defaultdict(list)
    for note in notes:
        groups[round(note.timestamp_ms, 3)].append(note)
    return groups


def _has_technique(note) -> bool:
    """Does the tab ask for anything but a plain pick on this note?"""
    return bool(note.bend or note.slide_to_next or note.slide_in
                or note.slide_out or note.hammer_to_next or note.dead)


def doublings(timeline: Timeline) -> list:
    """The notes an easier reading would leave out. Never anything else.

    A note qualifies when its pitch class is ALREADY SOUNDING on a lower
    note struck at the same moment -- so the chord keeps every pitch class it
    had, keeps its bass, and keeps its name.

    Three kinds are kept however doubled they are, and each is a bug avoided
    rather than a nicety:

    - **anything carrying a technique**, because a bend or a slide is the
      music rather than the harmony, and because a note that some earlier
      note hammers or slides INTO cannot be removed without orphaning it --
      `_legato_credit` finds its source by looking for the next note on the
      string, so taking one out would hand the credit to the wrong note;
    - **a DEAD note**, which sounds no pitch at all: it can be neither a
      doubling nor the reason for one, and removing one from a muted strum
      changes the stroke;
    - **the lowest note of the moment**, which is what "already sounding"
      is measured against.
    """
    out = []
    for group in _by_onset(timeline.notes).values():
        sounding = sorted(n.midi_note for n in group if not n.dead)
        for note in group:
            if note.dead or _has_technique(note):
                continue
            if any(lower < note.midi_note
                   and (note.midi_note - lower) % 12 == 0
                   for lower in sounding):
                out.append(note)
    return _keep_the_stroke(timeline, _keep_legato_targets(timeline, out))


def _keep_the_stroke(timeline: Timeline, doomed: list) -> list:
    """Put back any removal that would open a GAP in a strum that had none.

    "Same strums" is the headline of this whole rule, and it was nearly
    false. Measured on the player's six songs: on the power-chord material
    the removals are overwhelmingly three strings becoming two ADJACENT ones
    (Godsmack, 729 of them) -- but a full open chord loses its inner strings
    too, and strings 6-5-3 is a stroke that has to MISS the fourth, which is
    harder than the chord it replaces. On Kid Rock that turned 0 gapped
    strums into 5.

    Five moments in four hundred is small, and a headline that is false five
    times is the kind of thing this project writes chapters about. So the
    removals are taken from the OUTSIDE in and each is kept only while what
    is left is still one unbroken sweep.
    """
    if not doomed:
        return doomed
    drop = {id(n) for n in doomed}
    for group in _by_onset(timeline.notes).values():
        strings = sorted(n.string for n in group)
        if strings != list(range(strings[0], strings[-1] + 1)):
            continue                  # it already had a gap; leave it be
        # Outermost first: the top string is the one a strum can stop short
        # of, so it is the one whose removal costs the stroke nothing.
        here = sorted((n for n in group if id(n) in drop),
                      key=lambda n: n.string)
        kept = set(strings)
        for note in here:
            rest = sorted(kept - {note.string})
            if rest and rest != list(range(rest[0], rest[-1] + 1)):
                drop.discard(id(note))    # this one would open the gap
            else:
                kept.discard(note.string)
    return [n for n in doomed if id(n) in drop]


def _keep_legato_targets(timeline: Timeline, doomed: list) -> list:
    """Drop from the list any note an earlier note slides or hammers INTO.

    The tab carries no link from a legato source to its target -- the matcher
    finds it by walking to the next note on the string -- so removing a
    target silently moves the credit to whatever comes after it.
    """
    if not doomed:
        return doomed
    marked = set()
    last_on_string: dict = {}
    for note in sorted(timeline.notes, key=lambda n: n.timestamp_ms):
        before = last_on_string.get(note.string)
        if before is not None and (before.hammer_to_next
                                   or before.slide_to_next):
            marked.add(id(note))
        last_on_string[note.string] = note
    return [n for n in doomed if id(n) not in marked]


def simplified(timeline: Timeline) -> Timeline:
    """The same song with its octave doublings left out.

    Nothing is added, nothing is moved and nothing changes pitch. What comes
    back is a SUBSET of what was written, which is what makes the four
    properties in `tests/test_simplify.py` assertable without ears:
    every moment keeps its pitch classes, its bass note and its chord name,
    and the song keeps every one of its picks.
    """
    leave_out = {id(n) for n in doublings(timeline)}
    if not leave_out:
        return timeline
    kept = [n for n in timeline.notes if id(n) not in leave_out]
    return Timeline(kept, timeline.metadata, list(timeline.measures))


def how_much(timeline: Timeline) -> tuple[int, int]:
    """(notes an easier reading would drop, notes written). For the screen.

    A reduction nobody can see the size of is a setting nobody can judge --
    and on a solo the answer is honestly "almost nothing", which the player
    is better off reading than discovering.
    """
    return len(doublings(timeline)), len(timeline.notes)
