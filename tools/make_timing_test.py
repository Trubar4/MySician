"""Generate a metronomic GP5 for diagnosing timing.

A downloaded tab cannot tell you whether bad timing is the app's fault: it may
itself be sloppily transcribed, have a wrong tempo, or start half a beat off.
This writes a file where every note lands exactly on a beat by construction,
so if timing still feels wrong here, the cause is latency or the app -- and if
it feels right here but wrong on your own tabs, the tabs are the problem.

Deliberately plain: open strings and one power-chord shape, nothing that needs
technique, so what you are testing is when a note registers, not whether you
played it well.

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


def _measure_header(number, start, tempo_marker=None):
    header = gp.MeasureHeader()
    header.number = number
    header.start = start
    header.timeSignature = gp.TimeSignature()
    header.timeSignature.numerator = 4
    header.timeSignature.denominator = gp.Duration(QUARTER)
    if tempo_marker:
        header.marker = gp.Marker()
        header.marker.title = tempo_marker
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


# Each section is (label, bars, beats-per-bar pattern) where the pattern is a
# list of (duration, notes) filling exactly one 4/4 bar.
LOW_E = [(6, 0)]
E5 = [(6, 0), (5, 2)]


def _sections():
    """The exercise, in order of increasing demand on timing."""
    return [
        ("Count-in - one silent bar", 1,
         [(QUARTER, []) for _ in range(4)]),

        ("Whole notes - is the very first note in the right place", 2,
         [(WHOLE, LOW_E)]),

        ("Half notes", 2,
         [(HALF, LOW_E), (HALF, LOW_E)]),

        ("Quarter notes - the main timing reference", 8,
         [(QUARTER, LOW_E) for _ in range(4)]),

        ("Quarter-note power chords", 8,
         [(QUARTER, E5) for _ in range(4)]),

        ("Eighth notes - tests whether fast strikes still register", 8,
         [(EIGHTH, LOW_E) for _ in range(8)]),

        ("Eighth-note power chords - the metal case", 8,
         [(EIGHTH, E5) for _ in range(8)]),

        ("Off-beat quarters - rest, note, rest, note", 4,
         [(QUARTER, []), (QUARTER, LOW_E), (QUARTER, []), (QUARTER, LOW_E)]),
    ]


def build_song(tempo: int) -> gp.Song:
    song = gp.Song()
    song.title = f"Timing Test {tempo} BPM"
    song.artist = "MySician"
    song.album = "Diagnostics"
    song.tempo = tempo
    song.tempoName = ""
    song.measureHeaders = []
    song.tracks = []

    track = gp.Track(song=song)
    track.number = 1
    track.name = "Timing Test"
    track.fretCount = 24
    track.channel.instrument = 30  # distortion guitar
    track.strings = [
        gp.GuitarString(number=i + 1, value=v) for i, v in enumerate(TUNING)
    ]
    track.measures = []

    number = 1
    start = gp.Duration.quarterTime * 1  # GP measures start at 960
    for label, bars, pattern in _sections():
        for bar in range(bars):
            header = _measure_header(
                number, start, tempo_marker=label if bar == 0 else None
            )
            song.measureHeaders.append(header)

            measure = gp.Measure(track=track, header=header)
            voice = gp.Voice(measure=measure)
            voice.beats = [_beat(voice, dur, notes) for dur, notes in pattern]
            # GP files always carry two voices; the second stays empty
            second = gp.Voice(measure=measure)
            second.beats = []
            measure.voices = [voice, second]
            track.measures.append(measure)

            number += 1
            start += gp.Duration.quarterTime * 4

    song.tracks.append(track)
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
    seconds = bars * 4 * 60 / args.tempo
    print(f"Written: {out}")
    print(f"  {bars} bars at {args.tempo} BPM  (~{seconds:.0f} s)")
    print("\nSections:")
    for label, count, _ in _sections():
        print(f"  {count:2d} bars  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
