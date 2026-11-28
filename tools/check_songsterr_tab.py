"""Read Songsterr's own tab format and check it against a Guitar Pro file.

Usage:
    python tools/check_songsterr_tab.py <dir of {n}.json> <same revision.gp>

Most Songsterr tabs have no GP file behind them, which is why
`pickhero/tabs/songsterr_tab.py` exists at all -- and it is exactly why its
rules cannot be checked on one of those. So the control is a revision that
has BOTH: the JSON Songsterr serves, and a `.gp` a third-party downloader
built from the same data. Convert the one, load the other, compare note for
note.

What it is not: a claim that the two must agree about everything. They do
not, and that is measured rather than glossed over -- this source carries
let-ring, more slides, more bends and the chord names, and that GPIF
carries palm mutes it does not. The counts are printed BOTH ways so a
difference is a reading rather than a verdict.

Exits non-zero when the note count or a note's PLACE disagrees, which is
the half that cannot be a matter of taste.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.tabs import load_gp_file                      # noqa: E402
from pickhero.tabs import songsterr_tab as st               # noqa: E402

#: A note's place has to agree to better than this. Both sides work the
#: same bar grid out of the same durations, so anything above rounding is a
#: real difference in how a bar was read.
PLACE_MS = 1.0

#: How many BLOCKS the two sources may be split into, and how small a block
#: may be. A per-note timing fault SCATTERS -- every note on its own offset
#: -- where a structural difference between two files moves a whole stretch
#: of the song together. So the shape of the disagreement is what is
#: checked, and the tool NAMES the blocks rather than ruling on them.
MAX_BLOCKS = 2
MIN_BLOCK = 3


def load_tracks(folder: Path) -> list[dict]:
    """Every {n}.json in the folder, in track order."""
    found = {}
    for path in folder.glob("*.json"):
        try:
            found[int(path.stem)] = json.loads(path.read_text())
        except (ValueError, OSError) as exc:
            print("  could not read %s: %s" % (path.name, exc))
    return [found[k] for k in sorted(found)]


def techniques(notes) -> dict[str, int]:
    """What the tab asked for, counted."""
    out = {"notes": len(notes)}
    for name, test in (("letRing", lambda n: n.let_ring),
                       ("palmMute", lambda n: n.palm_mute),
                       ("dead", lambda n: n.dead),
                       ("slide", lambda n: n.slide_to_next),
                       ("slideIn", lambda n: n.slide_in),
                       ("slideOut", lambda n: n.slide_out),
                       ("bend", lambda n: n.bend),
                       ("hammer", lambda n: n.hammer_to_next)):
        count = sum(1 for n in notes if test(n))
        if count:
            out[name] = count
    return out


def blocks(ours, theirs) -> tuple[list[tuple[float, int, int, int]], int]:
    """How the two sources disagree, as (offset ms, notes, first bar, last).

    Keyed by (string, fret, midi) in time order, so a note the one source
    holds and the other does not shifts nothing after it -- walking two
    lists in step is how four tools in this project came to measure
    themselves.

    On the revision this was built for the answer is two blocks, and the
    second one is the finding rather than a fault of ours. Songsterr writes
    the pickup bar as the half bar it is (a rest and three eighths, with
    `anacrusis: true` on the track); the third-party downloader gives that
    bar a FULL bar of time and leaves its notes at the front of it. So the
    pickup's own three notes sit at the same millisecond in both sources
    and everything after them is a second later in the .gp -- which puts
    1250 ms of silence between the pickup and the first downbeat, where a
    pickup exists precisely to lead into it. Two readings, and this one is
    the one that agrees with Songsterr's own per-bar map.
    """
    import difflib
    key = lambda n: (n.string, n.fret, n.midi_note)          # noqa: E731
    matcher = difflib.SequenceMatcher(a=[key(n) for n in ours],
                                      b=[key(n) for n in theirs],
                                      autojunk=False)
    pairs = [(ours[i + k], theirs[j + k])
             for i, j, size in matcher.get_matching_blocks()
             for k in range(size)]
    grouped: dict[float, list[int]] = {}
    for mine, yours in pairs:
        offset = round((yours.timestamp_ms - mine.timestamp_ms) / PLACE_MS)
        grouped.setdefault(offset * PLACE_MS, []).append(mine.measure)
    out = [(offset, len(bars), min(bars), max(bars))
           for offset, bars in grouped.items()]
    out.sort(key=lambda row: -row[1])
    return out, len(pairs)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    folder, gp = Path(argv[0]), Path(argv[1])
    tracks = load_tracks(folder)
    if not tracks:
        print("No {n}.json in %s" % folder)
        return 2
    print("%d track(s) in %s" % (len(tracks), folder.name))
    print()

    rows = st.list_tracks(tracks)
    bad = 0
    for row in rows:
        index = row["index"]
        ours = st.timeline_from_tracks(tracks, index)
        mark = "guitar" if row["is_guitar"] else (
            "drums" if row["is_percussion"] else "other")
        print("track %d  %-7s %-34s %d string(s)"
              % (index, mark, row["name"][:34], row["num_strings"]))
        if row["is_percussion"]:
            print("    drum kit -- no pitched notes to compare")
            print()
            continue
        try:
            theirs = load_gp_file(str(gp), track_index=index)
        except Exception as exc:                  # noqa: BLE001
            print("    the .gp has no track %d: %s" % (index, exc))
            print()
            continue

        mine_t = techniques(ours.notes)
        their_t = techniques(theirs.notes)
        print("    ours %s" % mine_t)
        print("    .gp  %s" % their_t)
        if mine_t["notes"] != their_t["notes"]:
            print("    NOTE COUNT DIFFERS by %+d"
                  % (mine_t["notes"] - their_t["notes"]))
            bad += 1
        found, compared = blocks(ours.notes, theirs.notes)
        for offset, count, first, last in found:
            print("    %4d of %d notes, the .gp %+.1f ms away"
                  " (our bars %d-%d)" % (count, compared, offset, first, last))
        scattered = [row for row in found if row[1] < MIN_BLOCK]
        if len(found) > MAX_BLOCKS or scattered:
            bad += 1
            print("    PLACES DISAGREE: %d block(s), %d of them under %d"
                  " notes -- a scattered offset is a timing fault, not a"
                  " difference between two files"
                  % (len(found), len(scattered), MIN_BLOCK))
        line = ours.metadata.unreadable_line()
        if line:
            print("    %s" % line)
        print()

    if bad:
        print("FAIL: %d track(s) disagree about a note or a count." % bad)
    else:
        print("OK: every pitched track agrees note for note, in at most"
              " %d block(s)." % MAX_BLOCKS)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
