"""Two tracks of one file, played as one part.

*"Ich möchte 2 Spuren aus einem GP in eine neue kombinieren. Spur 3 ist die
Hauptspur. Immer wenn sie leer ist, füll sie mit Spur 2 auf. Wo beide Töne
haben, gewinnt Spur 3."*

A lead guitar that sits out the verses is a track with holes in it, and the
rhythm guitar is what fills them. Measured on the player's own file (Bad
Omens, "Just Pretend", lead against rhythm):

| | |
|---|---|
| bars with a note on either track | 81 |
| bars the lead sits out entirely | **25** |
| bars only the lead plays | 14 |
| bars both of them play | 42 |
| **notes wanting the SAME STRING at the same millisecond** | **184** |

That last row is what decides the rule. Two notes on one string is not
something a guitar can do -- it is the trap `tools/retune.py` paid for, where
the written file would not open at all -- so "the lead wins" has to say WHAT
it wins, and the only answer with no collisions by construction is that it
wins the whole BAR.

**A bar is the unit.** A bar the primary plays is the primary's, whole; a bar
it does not play at all is taken from the next track in the list. Nothing is
ever interleaved inside a bar, so no moment can end up with two notes on one
string and no bar can end up a hybrid nobody wrote. On this file that is
563 + 1318 notes in, 957 out, and **0 collisions**.

**The cost is named rather than hidden: in all 42 shared bars the filler's
notes are dropped**, 8 of the lead's against 24 of the rhythm's in the
chorus. That is the point where the lead really does replace the riff -- but
it is also the shape a wrong merge has, and the two cannot be told apart by
counting. What CAN be told apart is a bar the primary barely touches, and
there is exactly **one** here (bar 132, one note against three). So the rule
is right for this file and would be wrong for a tab where the lead answers
the riff bar by bar; the numbers above are how to tell, not a guarantee.

Arithmetic and nothing else -- no file reading, no pygame -- so the rule is
tested on bare timelines, and because the app and any tool that wants it must
not each have their own idea of what a merge is.
"""

from __future__ import annotations

from dataclasses import replace

from pickhero.tabs.timeline import SongMetadata, Timeline


def refuse_reason(timelines: list[Timeline]) -> str | None:
    """Why these tracks cannot be merged, or None if they can.

    **The tuning is the one thing that must agree.** A `NoteEvent` carries its
    string and fret as well as its pitch, and the picture is drawn from the
    string and the fret. Merging a Drop C track into a Standard one puts fret
    numbers on the board that belong to two different instruments: every one
    of them readable, and half of them a lie about what to press. Transposing
    one to match is a different operation and already exists (`R`, and
    `tools/retune.py` for the frets).
    """
    if len(timelines) < 2:
        return "pick at least two tracks"
    first = timelines[0].metadata.tuning
    for other in timelines[1:]:
        if other.metadata.tuning != first:
            return "those tracks are tuned differently"
    return None


def bars_played(timeline: Timeline) -> set[int]:
    """Which bars this track has any note in."""
    return {n.measure for n in timeline.notes}


def merge_by_bar(timelines: list[Timeline]) -> Timeline:
    """One timeline, each bar taken from the first track that plays it.

    The order IS the priority: the first list entry is the main part and
    every later one only ever fills bars nobody before it played.
    """
    reason = refuse_reason(timelines)
    if reason is not None:
        raise ValueError(reason)

    taken: set[int] = set()
    notes = []
    for timeline in timelines:
        mine = bars_played(timeline) - taken
        notes += [n for n in timeline.notes if n.measure in mine]
        taken |= mine

    # The bar grid, the tempo and the tuning come from the PRIMARY: the bars
    # of one file are one clock (bar 40 of the rhythm guitar is bar 40 of the
    # lead), so there is nothing to reconcile -- but the track NAME would be
    # a lie, and the picture and the run log both print it.
    lead = timelines[0]
    names = " + ".join(t.metadata.track_name or f"track {t.metadata.track_index + 1}"
                       for t in timelines)
    meta = replace(lead.metadata, track_name=names)
    return Timeline(notes, meta, list(lead.measures))


def merged_track_id(primary_index: int) -> int:
    """The track id a merged arrangement is recorded under.

    Negative, because a real index never is, and a stored RUN carries the
    track it was played on: a merged part is not the notes of the track it
    leads with, and a run history that let the two share an id would draw one
    against the other's notes. `runs.py` already lists a run of another track
    without drawing it, which is exactly the right behaviour here -- it only
    needs the two to be told apart.
    """
    return -(primary_index + 1)
