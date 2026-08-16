"""Shared pieces for writing practice GP5 files.

Both generators need the same fiddly bits — measure headers, beats that
actually survive a write/read round trip, and a click track that mirrors the
part being practised. Getting any of them subtly wrong produces a file that
looks right in memory and wrong on disk, so they live in one place.
"""

from __future__ import annotations

import guitarpro
from guitarpro import models as gp

# Standard tuning, string 1 = high E. GP numbers strings the same way.
TUNING = [64, 59, 55, 50, 45, 40]

# Duration values follow the GP convention: 1 = whole, 4 = quarter, 8 = eighth.
WHOLE, HALF, QUARTER, EIGHTH = 1, 2, 4, 8

# General MIDI percussion, channel 10 (0-indexed 9).
SIDE_STICK = 37
PERCUSSION_CHANNEL = 9

REST: list = []


def measure_header(number: int, start: int, marker: str | None = None):
    header = gp.MeasureHeader()
    header.number = number
    header.start = start
    header.timeSignature = gp.TimeSignature()
    header.timeSignature.numerator = 4
    header.timeSignature.denominator = gp.Duration(QUARTER)
    if marker:
        header.marker = gp.Marker()
        header.marker.title = marker
    return header


def bend(semitones: float):
    """A bend that rises over the note and holds, for `beat`'s note specs.

    Points are (position 0-12 across the note, value in semitones), which is
    what pyguitarpro normalises to on both read and write.
    """
    effect = gp.BendEffect()
    effect.type = gp.BendType.bend
    effect.value = int(round(semitones * 50))   # GP's own 1/50-tone units
    effect.points = [
        gp.BendPoint(position=0, value=0),
        gp.BendPoint(position=6, value=int(round(semitones))),
        gp.BendPoint(position=12, value=int(round(semitones))),
    ]
    return ("bend", effect)


def legato():
    """Hammer-on / pull-off to the next note, for `beat`'s note specs.

    Which of the two it is follows from the frets, so GP does not record it
    separately and neither do we.
    """
    return ("hammer", True)


def slide(kind: str):
    """A slide, as one of 'to' (to the next note), 'up' or 'down' (off it)."""
    return ("slide", {
        "to": gp.SlideType.shiftSlideTo,
        "up": gp.SlideType.outUpwards,
        "down": gp.SlideType.outDownwards,
        "in_up": gp.SlideType.intoFromBelow,
        "in_down": gp.SlideType.intoFromAbove,
    }[kind])


def beat(voice, duration_value: int, notes):
    """One beat. `notes` is a list of (string, fret); empty means a rest.

    A note may carry techniques as (string, fret, effect, ...), where each
    effect comes from `bend()` or `slide()`.
    """
    b = gp.Beat(voice=voice)
    b.duration = gp.Duration(duration_value)
    b.notes = []
    for spec in notes:
        string, fret, effects = spec[0], spec[1], spec[2:]
        note = gp.Note(beat=b)
        note.value = fret
        note.string = string
        note.velocity = gp.Velocities.forte
        note.type = gp.NoteType.normal
        for kind, payload in effects:
            if kind == "bend":
                note.effect.bend = payload
            elif kind == "hammer":
                note.effect.hammer = payload
            else:
                note.effect.slides.append(payload)
        b.notes.append(note)
    # Without an explicit status the writer emits the beat as 'empty' and the
    # reader then folds a whole bar's beats into one beat carrying every note.
    b.status = gp.BeatStatus.normal if notes else gp.BeatStatus.rest
    return b


def guitar_track(song: gp.Song, sections, name: str = "Play this") -> gp.Track:
    """Build the part to practise, and the measure headers the song hangs off.

    `sections` is a list of (label, bars); a bar is a list of
    (duration, notes) filling exactly 4/4.
    """
    track = gp.Track(song=song)
    track.number = 1
    track.name = name
    track.fretCount = 24
    track.channel.instrument = 30  # distortion guitar
    track.strings = [
        gp.GuitarString(number=i + 1, value=v) for i, v in enumerate(TUNING)
    ]
    track.measures = []

    number = 1
    start = gp.Duration.quarterTime * 1  # GP measures start at 960
    for label, bars in sections:
        for i, bar in enumerate(bars):
            header = measure_header(number, start, label if i == 0 else None)
            song.measureHeaders.append(header)

            measure = gp.Measure(track=track, header=header)
            voice = gp.Voice(measure=measure)
            voice.beats = [beat(voice, dur, notes) for dur, notes in bar]
            second = gp.Voice(measure=measure)
            second.beats = []
            measure.voices = [voice, second]
            track.measures.append(measure)

            number += 1
            start += gp.Duration.quarterTime * 4
    return track


def click_track(song: gp.Song, guitar: gp.Track, number: int = 2) -> gp.Track:
    """A click exactly where the guitar has a note, silent on its rests.

    Mirrors the guitar bar for bar and beat for beat, so the two cannot drift
    apart whatever the sections contain. A click on every beat would give
    nothing to line up against, since there would be no telling which click
    belongs to which note. The first bar is the exception and clicks
    throughout: a count-in with nothing to count is not a count-in.
    """
    track = gp.Track(song=song)
    track.number = number
    track.name = "Click"
    track.isPercussionTrack = True
    track.channel.channel = PERCUSSION_CHANNEL
    track.channel.effectChannel = PERCUSSION_CHANNEL
    track.channel.instrument = 0
    # Open-string value 0, so the "fret" IS the GM drum note. With a guitar
    # tuning the extractor adds the string's pitch and a side stick becomes a
    # wood block.
    track.strings = [gp.GuitarString(number=i + 1, value=0) for i in range(6)]
    track.measures = []

    for bar_idx, guitar_measure in enumerate(guitar.measures):
        measure = gp.Measure(track=track, header=guitar_measure.header)
        voice = gp.Voice(measure=measure)
        voice.beats = [
            beat(voice, gbeat.duration.value,
                 [(6, SIDE_STICK)] if (bar_idx == 0 or gbeat.notes) else REST)
            for gbeat in guitar_measure.voices[0].beats
        ]
        second = gp.Voice(measure=measure)
        second.beats = []
        measure.voices = [voice, second]
        track.measures.append(measure)
    return track


def build_song(title: str, tempo: int, sections, track_name="Play this") -> gp.Song:
    """A two-track song: the part to practise plus its click."""
    song = gp.Song()
    song.title = title
    song.artist = "MySician"
    song.album = "Diagnostics"
    song.tempo = tempo
    song.tempoName = ""
    song.measureHeaders = []
    song.tracks = []

    guitar = guitar_track(song, sections, name=track_name)
    song.tracks.append(guitar)
    song.tracks.append(click_track(song, guitar))
    return song


def write(song: gp.Song, path) -> None:
    guitarpro.write(song, str(path), version=(5, 1, 0))
