"""Generate a GP5 for checking CHORD recognition, not timing.

The timing test uses one pitch throughout on purpose, which says nothing about
whether the right chord was recognised. This plays four open chords that are
easy to grab and hard to confuse by ear -- C, Am, G, D -- so what is being
tested is the per-string verification rather than the player's accuracy.

It works up in difficulty. First one chord per bar with a bar of silence
after it, which gives the verifier a clean, isolated strike and gives you time
to see the verdict. Then one per bar without the gap, then two per bar, then a
strummed progression at eighth notes where chords ring into each other. Where
it starts getting things wrong is the answer being looked for.

Am and C differ by one note, as do G and D -- so a chord being mistaken for
its neighbour shows up as a specific wrong verdict rather than a vague miss.

    python tools/make_chord_test.py                  # 90 BPM -> songs/
    python tools/make_chord_test.py --tempo 70
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gp_builder import (  # noqa: E402
    EIGHTH, HALF, QUARTER, REST, WHOLE, build_song, write,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Open-position voicings as (string, fret), low string first.
C_MAJOR = [(5, 3), (4, 2), (3, 0), (2, 1), (1, 0)]
A_MINOR = [(5, 0), (4, 2), (3, 2), (2, 1), (1, 0)]
G_MAJOR = [(6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 3)]
D_MAJOR = [(4, 0), (3, 2), (2, 3), (1, 2)]

CHORDS = [("C", C_MAJOR), ("Am", A_MINOR), ("G", G_MAJOR), ("D", D_MAJOR)]


def _sections():
    isolated, back_to_back, twice, strummed = [], [], [], []

    for name, shape in CHORDS:
        # ring for a whole bar, then a bar of silence to read the verdict
        isolated.append([(WHOLE, shape)])
        isolated.append([(WHOLE, REST)])
        back_to_back.append([(WHOLE, shape)])
        twice.append([(HALF, shape), (HALF, shape)])

    # C - Am - G - D, one bar each, strummed on every eighth
    for name, shape in CHORDS:
        strummed.append([(EIGHTH, shape)] * 8)

    changes = [
        [(QUARTER, C_MAJOR), (QUARTER, C_MAJOR),
         (QUARTER, A_MINOR), (QUARTER, A_MINOR)],
        [(QUARTER, G_MAJOR), (QUARTER, G_MAJOR),
         (QUARTER, D_MAJOR), (QUARTER, D_MAJOR)],
    ]

    return [
        ("Count-in", [[(QUARTER, REST)] * 4]),
        ("One chord per bar, silence between - read the verdict here", isolated),
        ("Same chords, no gap", back_to_back),
        ("Twice per bar", twice),
        ("Two chords per bar - C Am, G D", changes * 2),
        ("Strummed eighths - chords ring into each other", strummed),
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tempo", type=int, default=90,
                    help="beats per minute (default 90)")
    ap.add_argument("--out", default=None, help="output .gp5 path")
    args = ap.parse_args()

    sections = _sections()
    song = build_song(f"Chord Test {args.tempo} BPM", args.tempo, sections,
                      track_name="Chords")
    out = Path(args.out) if args.out else (
        REPO_ROOT / "songs" / f"chord_test_{args.tempo}bpm.gp5"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    write(song, out)

    bars = len(song.tracks[0].measures)
    print(f"Written: {out}")
    print(f"  {bars} bars at {args.tempo} BPM  "
          f"(~{bars * 4 * 60 / args.tempo:.0f} s)")
    print("  Track 1 'Chords'  |  Track 2 'Click' sounds only where a chord is")
    print("\nChords (open position):")
    for name, shape in CHORDS:
        grip = "  ".join(f"S{s}/{f}" for s, f in shape)
        print(f"  {name:3s} {grip}")
    print("\nSections:")
    for label, bars_in in sections:
        print(f"  {len(bars_in):2d} bars  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
