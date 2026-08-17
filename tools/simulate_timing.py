"""Feed strikes with a KNOWN timing fault through the matcher.

The timing report exists to say which of three problems a player has. That
claim is only worth something if the report gets the answer right when the
answer is already known, so this injects each fault deliberately and prints
what comes back out.

It also reports how many strikes were measurable at all. Strikes are only
counted where exactly one tab note can explain them, so a riff repeating one
pitch contributes nothing until the search has narrowed -- and how well that
works depends on the song, which is why it is measured here per song rather
than argued about.

    python tools/simulate_timing.py songs/timing_test_100bpm.gp5
    python tools/simulate_timing.py songs/chord_test_90bpm.gp5 --seeds 20
"""

import argparse
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.audio.detector import DetectedNote  # noqa: E402
from pickhero.audio.input import TimestampedNote  # noqa: E402
from pickhero.matcher import NoteMatcher  # noqa: E402
from pickhero.tabs.loader import load_gp_file  # noqa: E402

# (label, latency ms, jitter sigma ms, per-string extra delay, allowed verdicts)
#
# Some cases allow more than one answer, and deliberately so. Injecting no
# latency alongside 90 ms of jitter does not produce a sample whose median is
# zero: it lands twenty-odd milliseconds off by chance, and reporting "a bit
# of both" is then the truthful reading. What must not happen is the report
# calling that plain latency, which would send the player to press K over
# nothing. The tuples say what the diagnosis may not get wrong, not which
# word it has to use.
CASES = [
    ("clean playing, synced", 5.0, 12.0, {}, ("fine",)),
    ("plain input latency", 90.0, 12.0, {}, ("latency",)),
    ("loose playing", 0.0, 90.0, {}, ("scatter", "mixed")),
    ("loose playing, late too", 60.0, 90.0, {}, ("mixed",)),
    # The gap has to clear the 25 ms floor on the strings the song actually
    # plays, or the case tests the floor rather than the diagnosis.
    ("wound strings detected later", 20.0, 14.0,
     {6: 80.0, 5: 70.0, 4: 55.0, 3: 40.0, 2: 15.0}, ("per_string",)),
]


def play(timeline, latency, jitter, bias, seed, window_ms=150.0):
    rng = random.Random(seed)
    matcher = NoteMatcher(timeline, timing_window_ms=window_ms)
    strikes = 0
    for note in timeline.notes:
        offset = latency + bias.get(note.string, 0.0) + rng.gauss(0, jitter)
        t = note.timestamp_ms + offset
        matcher.process_detected_notes(
            [TimestampedNote(
                note=DetectedNote(note.midi_note, 100.0, 0.95, "x", True),
                timestamp_ms=t)],
            t,
        )
        strikes += 1
    return matcher, strikes


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("song", help="a .gp3/.gp4/.gp5 file to play against")
    ap.add_argument("--seeds", type=int, default=8,
                    help="repeats per case, to see past one lucky draw")
    ap.add_argument("--window", type=float, default=150.0, help="hit window ms")
    args = ap.parse_args()

    timeline = load_gp_file(args.song)
    per_string: dict[int, int] = {}
    for note in timeline.notes:
        per_string[note.string] = per_string.get(note.string, 0) + 1
    well_covered = sum(1 for count in per_string.values() if count >= 20)
    print(f"{Path(args.song).name}: {len(timeline)} notes on "
          f"{len(per_string)} strings, {well_covered} of them played often")
    if well_covered < 3:
        print("  (too few strings to diagnose a per-string delay here -- "
              "that case needs a song that uses the whole neck)")
    print()
    print(f"{'case':30s} {'verdict':11s} {'measured':>9s} {'median':>8s} "
          f"{'spread':>8s} {'gap':>7s}")
    print("-" * 78)

    failures = 0
    for label, latency, jitter, bias, expected in CASES:
        if "per_string" in expected and well_covered < 3:
            print(f"{label:30s} {'skipped':11s}  (song does not use enough strings)")
            continue
        verdicts, yields, medians, spreads, gaps = [], [], [], [], []
        for seed in range(args.seeds):
            matcher, strikes = play(timeline, latency, jitter, bias, seed,
                                    args.window)
            report = matcher.timing_report()
            if report is None:
                verdicts.append("no data")
                yields.append(0.0)
                continue
            verdicts.append(report["verdict"])
            yields.append(100.0 * report["count"] / strikes)
            medians.append(report["median_ms"])
            spreads.append(report["spread_ms"])
            gaps.append(report["string_gap_ms"])

        agreed = sum(1 for v in verdicts if v in expected)
        mark = ("" if agreed == len(verdicts)
                else "   <- expected " + " or ".join(expected))
        if agreed != len(verdicts):
            failures += 1
        common = max(set(verdicts), key=verdicts.count)
        print(f"{label:30s} {common:11s} {statistics.mean(yields):8.0f}% "
              f"{statistics.mean(medians) if medians else 0:7.0f}  "
              f"{statistics.mean(spreads) if spreads else 0:7.0f}  "
              f"{statistics.mean(gaps) if gaps else 0:6.0f}"
              f"  {agreed}/{len(verdicts)}{mark}")

    print()
    if failures:
        print(f"{failures} case(s) did not come back as expected.")
    else:
        print("Every injected fault was named correctly.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
