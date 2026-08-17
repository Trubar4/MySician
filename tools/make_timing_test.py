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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gp_builder import (  # noqa: E402
    EIGHTH, QUARTER, REST, build_song, write,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

LOW_E = [(6, 0)]
E5 = [(6, 0), (5, 2)]

# One octave of E natural minor in open position, (string, fret).
E_MINOR_SCALE = [
    [(6, 0)], [(6, 2)], [(6, 3)], [(5, 0)],
    [(5, 2)], [(5, 3)], [(4, 0)], [(4, 2)],
]


def _sections():
    """(label, bars) where a bar is a list of (duration, notes) in 4/4."""
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
