"""How much you actually practised, per day, month, year and song.

The app writes one line of JSON per session to `~/.pickhero/practice_log.jsonl`
and does nothing else with it. This adds it up. It is deliberately a separate
program: the app's job is to be played, and a diary that has to be rendered
inside it is a diary that competes with the notes for screen space.

    python tools/practice_report.py                 # the last 14 days
    python tools/practice_report.py --by month
    python tools/practice_report.py --by song
    python tools/practice_report.py --csv > uebung.csv

The CSV is there because the question behind this was "so I can build a
dashboard". Every line of the log stands on its own, so anything that reads
JSON or CSV can take it from here.

What the numbers mean, because both could be read two ways:

- **Minutes are real time with the song running.** Not song time -- at 70 %
  practice speed the song is shorter than the time you spent on it -- and not
  wall-clock time, which would count the coffee you had with it paused.
- **Strikes are notes the microphone heard**, right ones and wrong ones. They
  are zero for a session played with audio off, and the minutes still count:
  you were still playing.
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero import practice_log  # noqa: E402


def bar(value: float, largest: float, width: int = 24) -> str:
    """A row of blocks, because a column of numbers hides its own shape."""
    if largest <= 0:
        return ""
    return "#" * max(1, int(round(width * value / largest)))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--by", default="day",
                    choices=["day", "month", "year", "song"],
                    help="what to add up (default: day)")
    ap.add_argument("--last", type=int, default=14,
                    help="how many rows to show, newest last (0 = all)")
    ap.add_argument("--csv", action="store_true",
                    help="write the rows as CSV instead, for a dashboard")
    ap.add_argument("--file", default=None, help="a practice log to read")
    args = ap.parse_args()

    sessions = practice_log.read(Path(args.file) if args.file else None)
    if not sessions:
        print("Noch nichts aufgezeichnet.")
        print(f"Die App schreibt nach {practice_log.PRACTICE_FILE},")
        print("sobald du einen Song mindestens "
              f"{practice_log.MIN_SESSION_SECONDS:.0f} Sekunden gespielt hast.")
        return 1

    rows = practice_log.totals(sessions, args.by)
    if args.last:
        rows = rows[-args.last:]

    if args.csv:
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow([args.by, "minutes", "strikes", "sessions", "songs"])
        for row in rows:
            writer.writerow([row.key, f"{row.minutes:.1f}", row.strikes,
                             row.sessions, len(row.songs)])
        return 0

    label = {"day": "Tag", "month": "Monat", "year": "Jahr",
             "song": "Song"}[args.by]
    # Song names are as long as songs are called; a fixed column turns the
    # whole table into a staircase.
    width = min(38, max(len(label), max(len(row.key) for row in rows)))
    # Counting songs is only worth a column when a row can hold several.
    songs_column = args.by != "song"
    header = f"{label:<{width}s} {'Minuten':>9s} {'Anschlaege':>11s} {'Sitzungen':>10s}"
    print(header + (f" {'Songs':>6s}" if songs_column else ""))
    print("-" * (len(header) + (7 if songs_column else 0) + 26))
    longest = max(row.minutes for row in rows)
    for row in rows:
        line = (f"{row.key[:width]:<{width}s} {row.minutes:9.1f} "
                f"{row.strikes:11d} {row.sessions:10d}")
        if songs_column:
            line += f" {len(row.songs):6d}"
        print(f"{line}  {bar(row.minutes, longest)}")

    every = practice_log.totals(sessions, "year")
    print()
    print(f"Insgesamt: {sum(t.minutes for t in every) / 60:.1f} Stunden, "
          f"{sum(t.strikes for t in every):,} Anschlaege, "
          f"{sum(t.sessions for t in every)} Sitzungen "
          f"an {len(practice_log.totals(sessions, 'day'))} Tagen.")
    scored = [s for s in sessions if s.accuracy is not None]
    if scored:
        best = max(scored, key=lambda s: s.accuracy)
        print(f"Beste gewertete Runde: {best.accuracy:.1f} % "
              f"bei {best.song} am {best.day}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
