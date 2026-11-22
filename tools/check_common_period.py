"""Does crediting a common period help the right notes and refuse a wrong fret?

Two notes a perfect fourth apart -- B3 on the D string and E4 on the G
string, which is the whole of Carol Of The Bells -- share a period two
octaves and a fifth below either of them. Monophonic YIN locks onto it and
reports E2: B3 is its third harmonic and E4 its fourth. Measured on the
player's own run, 185 of 338 missed notes were that one written B3 and 97
confident strikes reported E2, so the detector was hearing the music and the
matcher had no interval for it -- 19 semitones is not a whole number of
octaves, and octave equivalence is all it had.

The rule credits the written note whose common period came back, and it
requires TWO written partners to share that period. That is the whole safety
argument: one mis-fretted string cannot be an exact sub-multiple of two
different written pitches at once, where a missing fundamental is that by
definition.

Usage:

    python tools/check_common_period.py
    python tools/check_common_period.py --right <take dir> --wrong <take dir>

Both takes are the same ten bars of the same passage on the same guitar: one
played as written, one played a fret high. The second is what makes the first
worth anything -- scored alone, a rule that credits everything would look
perfect. Exits non-zero if the correct take falls below MIN_CORRECT of the
gain it was measured at, or if the error take gains more than MAX_WRONG.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import take_harness as th  # noqa: E402

from pickhero.audio.chord_verify import ChordVerifier  # noqa: E402
from pickhero.audio.take import song_ms_at  # noqa: E402
from pickhero.config import Config  # noqa: E402
from pickhero.matcher import MatchType, NoteMatcher  # noqa: E402
from pickhero.tabs.loader import load_gp_file  # noqa: E402
from pickhero.tabs.merge import merge_by_bar  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SONG = REPO / "songs" / "Trans-Siberian Orchestra - Carol Of The Bells.gp"
# What the player plays: the lead guitar, with the bars it sits out filled
# from the clean guitar and the third distortion track. His own setting.
TRACKS = (1, 0, 3)
# The stretch both takes cover: the B3/E4 ostinato, which is where the
# missing fundamental lives. Bars 12-21 as the tab numbers them, which is
# measure index 11 upward -- the run log's `bar` column is 1-based and
# NoteEvent.measure is not.
BARS = range(11, 21)

RIGHT = REPO / "reference_recordings" / (
    "20261121_190952 Trans-Siberian Orchestra - Carol Of The Bells")
WRONG = REPO / "reference_recordings" / (
    "20261122_154133 Trans-Siberian Orchestra - Carol Of The Bells")

# The player's own settings on the runs these takes came from.
TIMING_WINDOW_MS = 250.0
LATE_WINDOW_MS = 368.0

# A record of 2026-11-22 with margin, not a fitted bound. The correct take
# gained 19 of the 84 notes in those bars and the error take gained none.
MIN_CORRECT = 12
MAX_WRONG = 2


def score(folder: Path, timeline, rule: bool):
    """Verdicts for the notes in BARS, through the real capture path."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    take = manifest["takes"][0]
    rate = int(manifest.get("samplerate", 44100))
    wav = folder / take["file"]
    events = th.events(wav, rate, Config())
    matcher = NoteMatcher(timeline, timing_window_ms=TIMING_WINDOW_MS,
                          late_window_ms=LATE_WINDOW_MS,
                          chord_verifier=ChordVerifier(),
                          common_period=rule)
    for kind, item in events:
        if kind == "strike":
            sample = int(item.timestamp_ms * rate / 1000.0)
            song = song_ms_at(take, sample, rate)
            if song is None:
                return None
            item.timestamp_ms = song
            matcher.process_detected_notes([item], song)
        else:
            matcher.process_strike_windows([item])
    last = max((i.timestamp_ms for k, i in events if k == "strike"), default=0.0)
    matcher.process_detected_notes([], last + 2000.0)

    notes = [n for n in timeline.notes if n.measure in BARS]
    green = sum(1 for n in notes
                if matcher._get_state(n) in (MatchType.HIT, MatchType.CLOSE))
    by_period = sum(1 for n in notes
                    if matcher.unreliable(n) == "common_period")
    return green, len(notes), by_period, matcher.notes_from_common_period


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--right", default=str(RIGHT))
    ap.add_argument("--wrong", default=str(WRONG))
    ap.add_argument("--song", default=str(SONG))
    args = ap.parse_args()

    song = Path(args.song)
    if not song.exists():
        print(f"{song.name} is not in songs/ -- it is the player's own music "
              "and is not vendored. Put it there and run this again.")
        return 0
    base = load_gp_file(str(song), track_index=TRACKS[0])
    timeline = merge_by_bar([base] + [load_gp_file(str(song), track_index=t)
                                      for t in TRACKS[1:]])

    rows, ok = [], True
    for label, folder in (("played as written", Path(args.right)),
                          ("played a fret high", Path(args.wrong))):
        if not (folder / "manifest.json").exists():
            print(f"{folder.name}: no manifest, skipped")
            return 0
        off = score(folder, timeline, rule=False)
        on = score(folder, timeline, rule=True)
        if off is None or on is None:
            print(f"{folder.name}: the manifest carries no song mapping")
            return 1
        rows.append((label, off, on))

    print(f"bars {BARS.start + 1}-{BARS.stop}, "
          f"{rows[0][1][1]} written notes\n")
    print(f"{'take':20} {'rule off':>10} {'rule on':>10} {'gained':>8}")
    for label, off, on in rows:
        print(f"{label:20} {off[0]:>5}/{off[1]:<4} {on[0]:>5}/{on[1]:<4} "
              f"{on[0] - off[0]:>+8}")
    gained_right = rows[0][2][0] - rows[0][1][0]
    gained_wrong = rows[1][2][0] - rows[1][1][0]
    print(f"\nnotes credited to a common period: "
          f"{rows[0][2][2]} on the correct take, {rows[1][2][2]} on the wrong one")

    if gained_right < MIN_CORRECT:
        print(f"\nFAIL: the correct take gained {gained_right}, "
              f"under the {MIN_CORRECT} it was measured at")
        ok = False
    if gained_wrong > MAX_WRONG:
        print(f"\nFAIL: the wrong fret gained {gained_wrong}, "
              f"over the {MAX_WRONG} allowed -- the rule is crediting a "
              "mistake")
        ok = False
    if ok:
        print("\nOK")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
