"""Generate a short GP5 for diagnosing timing and syncing the backing track.

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

## Why the pitch keeps moving

The first version of this file repeated one open low E for eighty seconds, and
it measured almost nothing. A strike is only counted where exactly ONE tab note
can explain it, and two notes of the same pitch a few hundred milliseconds
apart make that impossible -- there is no way to tell "late against this one"
from "early against the next", so both are refused. A page of identical notes
is therefore not a harder test, it is no test: it is boring to play AND it
yields no data. Every passage here moves the pitch by at least three semitones
between neighbours instead, which is what makes each strike attributable.

The order is deliberate: isolated notes first, because the search for the note
a strike belongs to only narrows once it has a dozen clean samples to go on,
and everything after it depends on that having happened.

    python tools/make_timing_test.py                 # 100 BPM -> songs/
    python tools/make_timing_test.py --tempo 80
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gp_builder import (  # noqa: E402
    EIGHTH, QUARTER, REST, build_song, write,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Isolated notes to line the click up against, spread over the neck so each
# one is unmistakable. (string, fret).
REFERENCE = [[(6, 0)], [(5, 2)], [(4, 2)], [(3, 4)]]

# A quarter-note line that never sits still. Neighbours are at least three
# semitones apart, which is what keeps every strike attributable.
QUARTER_LINE = [[(6, 0)], [(6, 3)], [(5, 2)], [(5, 5)],
                [(4, 2)], [(4, 5)], [(5, 5)], [(6, 3)]]

# Power chords with a moving root -- the metal case, and still measurable.
POWER_CHORDS = [[(6, 0), (5, 2)], [(6, 3), (5, 5)],
                [(6, 5), (5, 7)], [(6, 8), (5, 10)]]

# Eighth-note pedal riff: open E alternating with a climbing note. The pedal
# repeats, but never within the half second that would make it ambiguous.
PEDAL_RIFF = [[(6, 0)], [(6, 5)], [(6, 0)], [(6, 7)],
              [(6, 0)], [(6, 8)], [(6, 0)], [(6, 10)]]


def _sections():
    """(label, bars) where a bar is a list of (duration, notes) in 4/4."""
    reference_bar = [
        item for note in REFERENCE for item in ((QUARTER, note), (QUARTER, REST))
    ]
    # Four notes, each followed by a rest, fills two bars.
    reference_bars = [reference_bar[:4], reference_bar[4:]]

    quarter_bars = [[(QUARTER, n) for n in QUARTER_LINE[:4]],
                    [(QUARTER, n) for n in QUARTER_LINE[4:]]]
    chord_bar = [(QUARTER, c) for c in POWER_CHORDS]
    eighth_bar = [(EIGHTH, n) for n in PEDAL_RIFF]
    chord_eighths = [(EIGHTH, c) for c in POWER_CHORDS] * 2
    offbeat_bar = [
        item for note in QUARTER_LINE[:2]
        for item in ((QUARTER, REST), (QUARTER, note))
    ]

    return [
        ("Count-in", [[(QUARTER, REST)] * 4]),
        ("Single notes with a rest after each - line the click up here",
         reference_bars),
        ("Quarter notes, moving - the main timing reference", quarter_bars),
        ("Quarter-note power chords", [chord_bar] * 2),
        ("Eighth notes - do fast strikes still register", [eighth_bar] * 2),
        ("Eighth-note power chords - the metal case", [chord_eighths] * 2),
        ("Off-beat quarters", [offbeat_bar]),
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tempo", type=int, default=100,
                    help="beats per minute (default 100)")
    ap.add_argument("--out", default=None, help="output .gp5 path")
    args = ap.parse_args()

    sections = _sections()
    song = build_song(f"Timing Test {args.tempo} BPM", args.tempo, sections)
    out = Path(args.out) if args.out else (
        REPO_ROOT / "songs" / f"timing_test_{args.tempo}bpm.gp5"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    write(song, out)

    bars = len(song.tracks[0].measures)
    print(f"Written: {out}")
    print(f"  {bars} bars at {args.tempo} BPM  "
          f"(~{bars * 4 * 60 / args.tempo:.0f} s)")
    print("  Track 1 'Play this'  |  Track 2 'Click' sounds only where a note is")
    print("\nSections:")
    for label, bars_in in sections:
        print(f"  {len(bars_in):2d} bars  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
