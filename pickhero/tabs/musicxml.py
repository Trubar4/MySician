"""The timeline as MusicXML tablature, so an engraver can draw it.

The scrolling display draws its own notes and needs nothing here. A real tab
-- six lines, fret numbers, and stems saying how long each note is -- is
music ENGRAVING, and this project is not going to grow a notation engine.
verovio does that, it is Python, it ships as a wheel, and it renders 150 bars
in 90 ms; what it cannot do is read Guitar Pro. This is the bridge.

Two things had to exist before it could be written honestly:

- **The written note value.** `NoteEvent.duration_quarters` carries what the
  tab said, because milliseconds cannot be read back into a note value --
  a tempo change or a triplet makes the arithmetic ambiguous, and a stem is
  drawn from the written value or not at all.
- **The time signature.** 3/4 and 6/8 at the same tempo are the same length
  of time and a different piece of music, so `MeasureInfo` carries it rather
  than the exporter guessing from the length of the bar.

**verovio's own timemap is not used, and must not be.** Measured in
isolation: on a TABLATURE staff verovio mis-times rests. The identical
document rendered as standard notation puts a bar of "quarter, quarter rest,
quarter, quarter" at quarters 0, 2, 3 -- correct -- and as tablature at 0, 3,
4, with the rest advancing two quarters instead of one and the bar
overflowing. `<forward>` is not honoured there either. So neither mechanism
for silence survives a tab staff, and every number the timemap reports for
one is unreliable.

That costs nothing, because the timemap was a convenience and never the
authority: the app already knows when every note sounds, from its own
timeline. What is needed from the engraver is the PICTURE. Each exported
note carries an `id` of our own (`n<index into timeline.notes>`), verovio
puts it on the `<g>` in the SVG, and `note_positions` reads the pixel
coordinates back out. Time comes from us, position comes from verovio, and
neither has an opinion about the other.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

from pickhero.audio.note_utils import STANDARD_TUNING
from pickhero.tabs.timeline import NoteEvent, Timeline

# Ticks per quarter note in the exported file. 480 divides a triplet eighth
# (160) and a 32nd (60) exactly, so nothing here needs rounding.
DIVISIONS = 480

# Written value in quarters -> MusicXML type. Dots are handled separately.
_TYPES: list[tuple[float, str]] = [
    (8.0, "breve"), (4.0, "whole"), (2.0, "half"), (1.0, "quarter"),
    (0.5, "eighth"), (0.25, "16th"), (0.125, "32nd"), (0.0625, "64th"),
]

_STEPS = ["C", "C", "D", "D", "E", "F", "F", "G", "G", "A", "A", "B"]
_ALTER = [0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0]


def note_type(quarters: float) -> tuple[str, int]:
    """(type, dots) for a written value, e.g. 1.5 -> ("quarter", 1).

    Falls back to the nearest smaller printable value rather than refusing:
    an odd length is usually a tie the reader has already merged, and a stem
    that is slightly wrong beats a bar the engraver will not draw at all.
    """
    if quarters <= 0:
        return "quarter", 0
    for dots, factor in ((0, 1.0), (1, 1.5), (2, 1.75)):
        for value, name in _TYPES:
            if abs(quarters - value * factor) < 1e-6:
                return name, dots
    for value, name in _TYPES:
        if quarters >= value - 1e-9:
            return name, 0
    return "64th", 0


def split_value(quarters: float) -> list[tuple[float, str, int]]:
    """A duration as printable note values, longest first.

    A gap in a single-voice tab IS a rest, and writing it as one is reading
    the tab rather than inventing anything -- what would be invention is
    guessing at a value no standard note has. Greedy over the printable
    values, which is what any notation program does.
    """
    out: list[tuple[float, str, int]] = []
    left = quarters
    guard = 0
    while left > 1.0 / DIVISIONS and guard < 16:
        guard += 1
        for value, name in _TYPES:
            for dots, factor in ((1, 1.5), (0, 1.0)):
                size = value * factor
                if size <= left + 1e-9:
                    out.append((size, name, dots))
                    left -= size
                    break
            else:
                continue
            break
        else:
            break
    return out


def _rests(quarters: float, voice: int = 1) -> str:
    return "".join(
        f"<note><rest/><duration>{round(size * DIVISIONS)}</duration>"
        f"<voice>{voice}</voice><type>{name}</type>{'<dot/>' * dots}</note>"
        for size, name, dots in split_value(quarters))


def _pitch(midi: int) -> str:
    step = _STEPS[midi % 12]
    alter = _ALTER[midi % 12]
    octave = midi // 12 - 1
    alter_el = f"<alter>{alter}</alter>" if alter else ""
    return f"<pitch><step>{step}</step>{alter_el}<octave>{octave}</octave></pitch>"


def _staff_tunings(tuning: dict[int, int]) -> str:
    """Open strings, LINE 1 being the lowest -- MusicXML counts up from the
    bottom of the staff while the app numbers strings down from the high e."""
    out = []
    strings = sorted(tuning, reverse=True)          # low string first
    for line, string in enumerate(strings, start=1):
        midi = tuning[string]
        step = _STEPS[midi % 12]
        alter = _ALTER[midi % 12]
        alter_el = (f"<tuning-alter>{alter}</tuning-alter>" if alter else "")
        out.append(
            f'<staff-tuning line="{line}">'
            f"<tuning-step>{step}</tuning-step>{alter_el}"
            f"<tuning-octave>{midi // 12 - 1}</tuning-octave></staff-tuning>")
    return "".join(out)


def note_positions(svg: str) -> dict[str, tuple[int, int]]:
    """Where the engraver put each of our notes, by the id we gave it.

    This is the whole reason the export stamps ids: the app knows WHEN a note
    sounds and verovio knows WHERE it was drawn, and neither needs to be
    asked about the other.
    """
    return {m.group(1): (int(m.group(2)), int(m.group(3)))
            for m in re.finditer(
                r'<g id="(n\d+)" class="note">\s*<text x="(-?\d+)" y="(-?\d+)"',
                svg)}


def _note_xml(note: NoteEvent, quarters: float, in_chord: bool,
              string_count: int, voice: int = 1,
              note_id: str = "") -> str:
    kind, dots = note_type(quarters)
    duration = max(1, round(quarters * DIVISIONS))
    # MusicXML numbers strings from 1 at the HIGHEST-sounding string, which is
    # what the app does too -- so this passes straight through. It is written
    # down because getting it backwards mirrors the whole tab and looks like a
    # rendering bug rather than an off-by-one.
    parts = [
        f'<note id="{note_id}">' if note_id else "<note>",
        "<chord/>" if in_chord else "",
        _pitch(note.midi_note),
        f"<duration>{duration}</duration>",
        f"<voice>{voice}</voice>",
        f"<type>{kind}</type>",
        "<dot/>" * dots,
        "<notations><technical>",
        f"<string>{note.string}</string><fret>{note.fret}</fret>",
        "</technical></notations>",
        "</note>",
    ]
    return "".join(parts)


def to_musicxml(timeline: Timeline, title: str = "") -> str:
    """The whole timeline as one MusicXML part of tablature."""
    tuning = timeline.metadata.tuning or STANDARD_TUNING
    string_count = len(tuning) or 6
    by_measure: dict[int, list[NoteEvent]] = {}
    ids: dict[int, str] = {}
    for position, note in enumerate(timeline.notes):
        by_measure.setdefault(note.measure, []).append(note)
        ids[id(note)] = f"n{position}"

    measures = timeline.measures or []
    order = sorted({m.index for m in measures} | set(by_measure))
    info = {m.index: m for m in measures}

    body: list[str] = []
    last_sig: tuple[int, int] | None = None
    last_bpm: float | None = None
    for index in order:
        bar = info.get(index)
        beats = bar.beats if bar else 4
        beat_type = bar.beat_type if bar else 4
        attrs = []
        if last_sig is None:
            attrs.append(f"<divisions>{DIVISIONS}</divisions>")
        if (beats, beat_type) != last_sig:
            attrs.append(f"<time><beats>{beats}</beats>"
                         f"<beat-type>{beat_type}</beat-type></time>")
        if last_sig is None:
            attrs.append("<clef><sign>TAB</sign><line>5</line></clef>")
            attrs.append(f"<staff-details><staff-lines>{string_count}"
                         f"</staff-lines>{_staff_tunings(tuning)}"
                         f"</staff-details>")
        last_sig = (beats, beat_type)

        notes = sorted(by_measure.get(index, []),
                       key=lambda n: (n.timestamp_ms, -n.string))
        start_ms = bar.start_ms if bar else (notes[0].timestamp_ms if notes else 0.0)
        # Milliseconds per quarter, from where this bar starts to where the
        # NEXT one does -- not from its own end_ms. The GP3-5 reader sets
        # end_ms from the last BEAT in the bar, so a bar that is not filled
        # with notes reads short, and a tempo computed from it comes out too
        # fast: measured, the timing test lost 3.6 s that way and every note
        # after the first sparse bar was early.
        span = 0.0
        if bar is not None:
            following = info.get(index + 1)
            if following is not None:
                span = following.start_ms - bar.start_ms
            if span <= 0:
                span = bar.end_ms - bar.start_ms
        per_quarter = (span / (beats * 4.0 / beat_type)) if span > 0 else 500.0

        # The bar's own tempo, so the engraver's timemap is in the same
        # milliseconds the app plays in. Without it verovio assumes 120 BPM
        # and every position it reports is wrong by the ratio -- which is a
        # playhead that drifts, and this project has spent enough on those.
        total = beats * 4.0 / beat_type          # the bar, in quarters
        pieces: list[str] = []
        bpm = 60_000.0 / per_quarter if per_quarter > 0 else 120.0
        if last_bpm is None or abs(bpm - last_bpm) > 0.01:
            pieces.append(f'<direction placement="above"><sound '
                          f'tempo="{bpm:.4f}"/></direction>')
            last_bpm = bpm

        # Silence is written as RESTS, not as <forward>. Measured: verovio
        # honours a rest's duration in its timemap and does not honour a
        # forward's, so a bar padded with forwards renders at the right length
        # and reports the wrong times -- which is a playhead that drifts, and
        # the counts all matched while every position was wrong.
        #
        # But a rest cannot sit where a note is still sounding, and guitar tab
        # overlaps constantly: a let-ring bass note under a run of eighths.
        # That is what VOICES are for. Each onset goes into the first voice
        # whose cursor has reached it; a bar of single notes stays in one
        # voice and looks exactly as it did.
        chords: list[tuple[float, list[NoteEvent]]] = []
        cursor = 0
        while cursor < len(notes):
            onset = notes[cursor].timestamp_ms
            group = [n for n in notes[cursor:] if n.timestamp_ms == onset]
            cursor += len(group)
            chords.append((max(0.0, min(total,
                                        (onset - start_ms) / per_quarter)),
                           group))

        voices: list[list[tuple[float, float, list[NoteEvent]]]] = []
        ends: list[float] = []
        for target, group in chords:
            quarters = min(
                (n.duration_quarters or
                 (n.duration_ms / per_quarter if per_quarter else 1.0))
                for n in group)
            # Never past the bar line: a bar whose contents do not add up
            # makes the engraver misplace everything after it, which is a
            # worse lie than a note drawn one bar short.
            quarters = max(1.0 / DIVISIONS, min(quarters, total - target))
            # And never a length no single note head can spell. A tie the
            # reader merged is often 2.5 or 5 quarters, and <type> then said
            # "half" while <duration> said 2.5 -- verovio follows the TYPE,
            # so the note ended early and every onset after it in the bar
            # moved. Measured: Bon Jovi's second onset landed 455 ms late,
            # at the very first bar. The head is the longest printable value
            # that fits and the rest of the ring becomes rests, so the two
            # can never disagree again. A note drawn shorter than it sounds
            # is a cosmetic loss; an onset in the wrong place is not.
            printable = split_value(quarters)
            quarters = printable[0][0] if printable else quarters
            for v, end_at in enumerate(ends):
                if end_at <= target + 1e-6:
                    voices[v].append((target, quarters, group))
                    ends[v] = target + quarters
                    break
            else:
                voices.append([(target, quarters, group)])
                ends.append(target + quarters)

        if not voices:
            pieces.append(f'<note><rest measure="yes"/><duration>'
                          f"{round(total * DIVISIONS)}</duration></note>")
        for number, voice in enumerate(voices, start=1):
            if number > 1:
                pieces.append(f"<backup><duration>"
                              f"{round(total * DIVISIONS)}</duration></backup>")
            at = 0.0
            for target, quarters, group in voice:
                if target - at > 1e-6:
                    pieces.append(_rests(target - at, number))
                first = True
                for note in group:
                    pieces.append(_note_xml(note, quarters, not first,
                                            string_count, number,
                                            ids.get(id(note), "")))
                    first = False
                at = target + quarters
            if total - at > 1e-6:
                pieces.append(_rests(total - at, number))

        body.append(f'<measure number="{index + 1}">'
                    + (f"<attributes>{''.join(attrs)}</attributes>" if attrs else "")
                    + "".join(pieces) + "</measure>")

    name = escape(title or timeline.metadata.title or "Tab")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<score-partwise version="3.1">'
        f"<work><work-title>{name}</work-title></work>"
        '<part-list><score-part id="P1">'
        "<part-name>Guitar</part-name></score-part></part-list>"
        '<part id="P1">' + "".join(body) + "</part></score-partwise>"
    )
