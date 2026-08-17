"""Generate a GP5 for checking how techniques are DRAWN and scored.

No technique can be judged from a downloaded tab: if a bend looks wrong
there, it may be the tab that is wrong, or the transcription that put the
bend on the wrong beat. This file states exactly what it contains, so
anything the display gets wrong is the display's fault.

Bends and slides come first, each note long and followed by a rest -- a bend
needs room for its arc and a slide needs room for its connector, and the point
there is to look at them, not to play a passage. The muting sections at the
end are the opposite on purpose: palm mutes and dead notes only exist in fast
riffs, so they are written as the eighth-note chugs they actually appear in.

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
    EIGHTH, HALF, QUARTER, REST, WHOLE, bend, build_song, dead, legato,
    palm_mute, slide, write,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# String 3 (G) around the 7th fret: where bends actually get played.
G_STRING = 3
B_STRING = 2
# The muting sections live where metal lives: the bottom three strings.
D_STRING = 4
A_STRING = 5
E_STRING = 6


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
    hammer_ons = [
        _bar((HALF, [(G_STRING, 5, legato())]), (HALF, [(G_STRING, 7)])),
        _bar((WHOLE, REST)),
        _bar((HALF, [(B_STRING, 8, legato())]), (HALF, [(B_STRING, 10)])),
        _bar((WHOLE, REST)),
    ]
    pull_offs = [
        _bar((HALF, [(G_STRING, 7, legato())]), (HALF, [(G_STRING, 5)])),
        _bar((WHOLE, REST)),
        _bar((HALF, [(B_STRING, 10, legato())]), (HALF, [(B_STRING, 8)])),
        _bar((WHOLE, REST)),
    ]
    mixed = [
        _bar((HALF, [(G_STRING, 5, slide("to"))]), (HALF, [(G_STRING, 7, bend(2))])),
        _bar((WHOLE, REST)),
        _bar((HALF, [(B_STRING, 8, bend(1))]), (HALF, [(B_STRING, 8, slide("down"))])),
        _bar((WHOLE, REST)),
        _bar((QUARTER, [(G_STRING, 5, legato())]), (QUARTER, [(G_STRING, 7, legato())]),
             (QUARTER, [(G_STRING, 5)]), (QUARTER, REST)),
        _bar((WHOLE, REST)),
    ]

    # Muting, written as the riffs it belongs to. A palm mute is still the
    # written pitch, so these score exactly like open notes; a dead note has
    # no pitch at all and counts as played on the strike alone.
    def chug(*specs):
        return _bar(*[(EIGHTH, list(s)) for s in specs])

    pm_open = [(E_STRING, 0, palm_mute())]
    e5 = [(E_STRING, 0, palm_mute()), (A_STRING, 2, palm_mute()),
          (D_STRING, 2, palm_mute())]
    x_open = [(E_STRING, 0, dead())]
    x_chord = [(E_STRING, 0, dead()), (A_STRING, 0, dead()),
               (D_STRING, 0, dead())]

    palm_mutes = [
        chug(*([pm_open] * 8)),
        chug(*([pm_open] * 8)),
        _bar((WHOLE, REST)),
        chug(*([e5] * 8)),
        chug(*([e5] * 8)),
        _bar((WHOLE, REST)),
    ]
    dead_notes = [
        _bar((QUARTER, x_open), (QUARTER, REST),
             (QUARTER, x_open), (QUARTER, REST)),
        _bar((QUARTER, x_chord), (QUARTER, REST),
             (QUARTER, x_chord), (QUARTER, REST)),
        _bar((WHOLE, REST)),
    ]
    # The everyday metal case: chugs with a muted stroke used as rhythm.
    muted_riff = [
        chug(pm_open, pm_open, x_open, pm_open,
             pm_open, x_open, pm_open, pm_open),
        chug(e5, e5, x_chord, e5, e5, x_chord, e5, e5),
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
        ("Hammer-ons - arc between the frets, badge H", hammer_ons),
        ("Pull-offs - same arc, badge P", pull_offs),
        ("Mixed - slide into a bend, bend then slide off, then H and P", mixed),
        ("Palm mute - short stubs, PM badge once at the start of each run",
         palm_mutes),
        ("Dead notes - X instead of a fret, single then muted strum",
         dead_notes),
        ("Muted riff - chugs with dead strokes between them", muted_riff),
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
