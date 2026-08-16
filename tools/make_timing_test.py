"""Generate a GP5 for diagnosing timing and syncing the backing track.

A downloaded tab cannot tell you whether bad timing is the app's fault: it may
itself be sloppily transcribed, have a wrong tempo, or start half a beat off.
This writes a file where every note lands exactly on a beat by construction,
so if timing still feels wrong here, the cause is latency or the app -- and if
it feels right here but wrong on your own tabs, the tabs are the problem.

It carries a second, percussion track that clicks EXACTLY where the guitar
track has a note and stays silent on rests. That is what makes it usable for
lining the backing up with the display (N/M in the app): a click on every beat
gives you nothing to compare against, because you cannot tell which click
belongs to which note. A click that only sounds where a note is due can be
matched to that note by ear, and any offset between the two is audible at once.

The first section is a plain scale with a rest after every note, which is the
easiest possible case for that comparison. The sections after it get harder,
to find where fast strikes stop registering.

    python tools/make_timing_test.py                 # 100 BPM -> songs/
    python tools/make_timing_test.py --tempo 80
"""

import argparse
import sys
from pathlib import Path

import guitarpro
from guitarpro import models as gp

REPO_ROOT = Path(__file__).resolve().parent.parent

# Standard tuning, string 1 = high E. GP numbers strings the same way.
TUNING = [64, 59, 55, 50, 45, 40]

# Duration values follow the GP convention: 1 = whole, 4 = quarter, 8 = eighth.
WHOLE, HALF, QUARTER, EIGHTH = 1, 2, 4, 8

# General MIDI percussion, channel 10 (0-indexed 9).
SIDE_STICK = 37
PERCUSSION_CHANNEL = 9

REST: list = []
LOW_E = [(6, 0)]
E5 = [(6, 0), (5, 2)]

# One octave of E natural minor in open position, (string, fret).
E_MINOR_SCALE = [
    [(6, 0)], [(6, 2)], [(6, 3)], [(5, 0)],
    [(5, 2)], [(5, 3)], [(4, 0)], [(4, 2)],
]


def _measure_header(number, start, marker=None):
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


def _beat(voice, duration_value, notes):
    """One beat. `notes` is a list of (string, fret); empty means a rest."""
    beat = gp.Beat(voice=voice)
    beat.duration = gp.Duration(duration_value)
    beat.notes = []
    for string, fret in notes:
        note = gp.Note(beat=beat)
        note.value = fret
        note.string = string
        note.velocity = gp.Velocities.forte
        note.type = gp.NoteType.normal
        beat.notes.append(note)
    # Without an explicit status the writer emits the beat as 'empty' and the
    # reader then folds a whole bar's beats into one beat carrying every note.
    beat.status = gp.BeatStatus.normal if notes else gp.BeatStatus.rest
    return beat


def _sections():
    """(label, bars) where bars is a list of one bar each.

    A bar is a list of (duration, notes) filling exactly 4/4.
    """
    scale_bars = [
        [(QUARTER, E_MINOR_SCALE[i]), (QUARTER, REST),
         (QUARTER, E_MINOR_SCALE[i + 1]), (QUARTER, REST)]
        for i in range(0, len(E_MINOR_SCALE), 2)
    ]
    quarter_bar = [(QUARTER, LOW_E)] * 4
    chord_bar = [(QUARTER, E5)] * 4
    eighth_bar = [(EIGHTH, LOW_E)] * 8
    eighth_chord_bar = [(EIGHTH, E5)] * 8

    return [
        ("Count-in", [[(QUARTER, REST)] * 4]),

        ("Scale, note then rest - line the click up with the note here",
         scale_bars),
        ("Scale again, so there is time to adjust", scale_bars),

        ("Quarter notes - the main timing reference", [quarter_bar] * 8),
        ("Quarter-note power chords", [chord_bar] * 8),
        ("Eighth notes - do fast strikes still register", [eighth_bar] * 8),
        ("Eighth-note power chords - the metal case", [eighth_chord_bar] * 8),
        ("Off-beat quarters",
         [[(QUARTER, REST), (QUARTER, LOW_E), (QUARTER, REST), (QUARTER, LOW_E)]] * 4),
    ]


def _guitar_track(song: gp.Song) -> gp.Track:
    """The part to play, and the measure headers the whole song hangs off."""
    track = gp.Track(song=song)
    track.number = 1
    track.name = "Play this"
    track.fretCount = 24
    track.channel.instrument = 30  # distortion guitar
    track.strings = [
        gp.GuitarString(number=i + 1, value=v) for i, v in enumerate(TUNING)
    ]
    track.measures = []

    number = 1
    start = gp.Duration.quarterTime * 1  # GP measures start at 960
    for label, bars in _sections():
        for i, bar in enumerate(bars):
            header = _measure_header(number, start, marker=label if i == 0 else None)
            song.measureHeaders.append(header)

            measure = gp.Measure(track=track, header=header)
            voice = gp.Voice(measure=measure)
            voice.beats = [_beat(voice, dur, notes) for dur, notes in bar]
            second = gp.Voice(measure=measure)
            second.beats = []
            measure.voices = [voice, second]
            track.measures.append(measure)

            number += 1
            start += gp.Duration.quarterTime * 4
    return track


def _click_track(song: gp.Song, guitar: gp.Track, number: int) -> gp.Track:
    """A click exactly where the guitar has a note, silent on its rests.

    Mirrors the guitar bar for bar and beat for beat, so the two cannot drift
    apart no matter what the sections contain. The count-in is the exception:
    it clicks all four beats, because a count-in with nothing to count is not
    a count-in.
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
        beats = []
        for gbeat in guitar_measure.voices[0].beats:
            sounds = bar_idx == 0 or bool(gbeat.notes)
            beats.append(_beat(
                voice, gbeat.duration.value,
                [(6, SIDE_STICK)] if sounds else REST,
            ))
        voice.beats = beats
        second = gp.Voice(measure=measure)
        second.beats = []
        measure.voices = [voice, second]
        track.measures.append(measure)
    return track


def build_song(tempo: int) -> gp.Song:
    song = gp.Song()
    song.title = f"Timing Test {tempo} BPM"
    song.artist = "MySician"
    song.album = "Diagnostics"
    song.tempo = tempo
    song.tempoName = ""
    song.measureHeaders = []
    song.tracks = []

    guitar = _guitar_track(song)
    song.tracks.append(guitar)
    song.tracks.append(_click_track(song, guitar, number=2))
    return song


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tempo", type=int, default=100,
                    help="beats per minute (default 100)")
    ap.add_argument("--out", default=None, help="output .gp5 path")
    args = ap.parse_args()

    song = build_song(args.tempo)
    out = Path(args.out) if args.out else (
        REPO_ROOT / "songs" / f"timing_test_{args.tempo}bpm.gp5"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    guitarpro.write(song, str(out), version=(5, 1, 0))

    bars = len(song.tracks[0].measures)
    print(f"Written: {out}")
    print(f"  {bars} bars at {args.tempo} BPM  "
          f"(~{bars * 4 * 60 / args.tempo:.0f} s)")
    print("  Track 1 'Play this'  |  Track 2 'Click' sounds only where a note is")
    print("\nSections:")
    for label, bars_in in _sections():
        print(f"  {len(bars_in):2d} bars  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
