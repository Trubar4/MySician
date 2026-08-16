"""Generate a GP5 for checking how bends and slides are DRAWN.

Neither technique can be judged from a downloaded tab: if a bend looks wrong
there, it may be the tab that is wrong, or the transcription that put the
bend on the wrong beat. This file states exactly what it contains, so
anything the display gets wrong is the display's fault.

Every note is long and followed by a rest. That is deliberate -- a bend needs
room for its arc and a slide needs room for its connector, and the point here
is to look at them, not to play a passage.

Scoring is lenient for both: a bent note counts as played when the pitch is
anywhere between the written fret and the top of the bend, and a slide counts
across the span it travels. How far a bend actually went is a separate
question this file does not try to answer.

    python tools/make_technique_test.py              # 80 BPM -> songs/
    python tools/make_technique_test.py --tempo 60
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gp_builder import (  # noqa: E402
    HALF, QUARTER, REST, WHOLE, bend, build_song, slide, write,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# String 3 (G) around the 7th fret: where bends actually get played.
G_STRING = 3
B_STRING = 2


def _bar(*beats):
    return list(beats)


def _sections():
    half_bends = [
        _bar((WHOLE, [(G_STRING, 7, bend(1))])),
        _bar((WHOLE, REST)),
        _bar((WHOLE, [(B_STRING, 8, bend(1))])),
        _bar((WHOLE, REST)),
    ]
    full_bends = [
        _bar((WHOLE, [(G_STRING, 7, bend(2))])),
        _bar((WHOLE, REST)),
        _bar((WHOLE, [(B_STRING, 8, bend(2))])),
        _bar((WHOLE, REST)),
    ]
    big_bend = [
        _bar((WHOLE, [(G_STRING, 7, bend(3))])),
        _bar((WHOLE, REST)),
    ]

    # A shift slide needs the note it slides INTO, on the same string.
    slides_up = [
        _bar((HALF, [(G_STRING, 5, slide("to"))]), (HALF, [(G_STRING, 9)])),
        _bar((WHOLE, REST)),
        _bar((HALF, [(B_STRING, 3, slide("to"))]), (HALF, [(B_STRING, 10)])),
        _bar((WHOLE, REST)),
    ]
    slides_down = [
        _bar((HALF, [(G_STRING, 9, slide("to"))]), (HALF, [(G_STRING, 5)])),
        _bar((WHOLE, REST)),
        _bar((HALF, [(B_STRING, 10, slide("to"))]), (HALF, [(B_STRING, 3)])),
        _bar((WHOLE, REST)),
    ]
    open_slides = [
        _bar((HALF, [(G_STRING, 7, slide("up"))]), (HALF, REST)),
        _bar((HALF, [(G_STRING, 7, slide("down"))]), (HALF, REST)),
        _bar((HALF, [(G_STRING, 7, slide("in_up"))]), (HALF, REST)),
        _bar((HALF, [(G_STRING, 7, slide("in_down"))]), (HALF, REST)),
    ]
    mixed = [
        _bar((HALF, [(G_STRING, 5, slide("to"))]), (HALF, [(G_STRING, 7, bend(2))])),
        _bar((WHOLE, REST)),
        _bar((HALF, [(B_STRING, 8, bend(1))]), (HALF, [(B_STRING, 8, slide("down"))])),
        _bar((WHOLE, REST)),
    ]

    return [
        ("Count-in", [_bar((QUARTER, REST), (QUARTER, REST),
                           (QUARTER, REST), (QUARTER, REST))]),
        ("Half bends - arc should be labelled 1/2", half_bends),
        ("Full bends - arc should be labelled 'full' and reach higher", full_bends),
        ("One-and-a-half bend - labelled 1 1/2", big_bend),
        ("Slides UP - connector rises to the right", slides_up),
        ("Slides DOWN - connector falls to the right", slides_down),
        ("Slides with no target - stub off the head, then into it", open_slides),
        ("Mixed - slide into a bend, bend then slide off", mixed),
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tempo", type=int, default=80,
                    help="beats per minute (default 80)")
    ap.add_argument("--out", default=None, help="output .gp5 path")
    args = ap.parse_args()

    sections = _sections()
    song = build_song(f"Technique Test {args.tempo} BPM", args.tempo, sections,
                      track_name="Bends and Slides")
    out = Path(args.out) if args.out else (
        REPO_ROOT / "songs" / f"technique_test_{args.tempo}bpm.gp5"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    write(song, out)

    bars = len(song.tracks[0].measures)
    print(f"Written: {out}")
    print(f"  {bars} bars at {args.tempo} BPM  "
          f"(~{bars * 4 * 60 / args.tempo:.0f} s)")
    print("\nSections:")
    for label, bars_in in sections:
        print(f"  {len(bars_in):2d} bars  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
