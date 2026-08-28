"""Bring the practice history from another computer into this one.

Two machines, one player. The app keeps everything in `~/.pickhero/`, so
moving the history is a matter of merging two files -- but merging them by
hand is how a year of practice gets overwritten by an afternoon.

    python tools/merge_stats.py --from D:\\pickhero-vom-notebook
    python tools/merge_stats.py --from ...\\.pickhero --dry-run

Copy the OTHER machine's `.pickhero` folder somewhere this one can see it
(a stick, a cloud folder), then point --from at it. Nothing on the other
machine is touched; this only reads.

**Running it twice must change nothing the second time.** That is the whole
design constraint, because nobody remembers whether they already did it and
a history that doubles is worse than one that is missing:

- `practice_log.jsonl` is the real record -- one line per sitting, and what
  every total and the dashboard are built from. Sessions are merged by WHEN
  they started and WHICH song, so the same sitting cannot arrive twice.
- `progress.json` is a per-song high score, not a statistic. The better of
  the two records wins, whole, with its own history. `attempts` is the
  larger of the two rather than the sum: a sum cannot be done twice safely,
  and the honest count of sittings is in the practice log anyway.

A backup of anything changed is written next to it as `.bak` first.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.config import CONFIG_DIR  # noqa: E402
from pickhero import practice_log  # noqa: E402


def session_key(session) -> tuple:
    """What makes a sitting the same sitting.

    The start time is to the second and the song is in it, so two machines
    cannot invent the same one -- and the same one copied twice collapses.
    """
    return (session.started, session.song)


def merge_sessions(mine: list, theirs: list) -> tuple[list, int]:
    """(merged, how many were new), oldest first."""
    seen = {session_key(s) for s in mine}
    added = [s for s in theirs if session_key(s) not in seen]
    # A later merge must not depend on which order they arrived in.
    merged = sorted(mine + added, key=lambda s: (s.started, s.song))
    return merged, len(added)


def merge_progress(mine: dict, theirs: dict) -> tuple[dict, list[str]]:
    """(merged, the songs whose best came from the other machine)."""
    out = dict(mine)
    improved = []
    for song, other in theirs.items():
        current = out.get(song)
        if current is None:
            out[song] = dict(other)
            improved.append(song)
            continue
        merged = dict(current)
        # attempts: the larger, NOT the sum. See the module docstring.
        merged["attempts"] = max(current.get("attempts", 0),
                                 other.get("attempts", 0))
        merged["last_played"] = max(current.get("last_played", ""),
                                    other.get("last_played", ""))
        if other.get("best_accuracy", 0.0) > current.get("best_accuracy", 0.0):
            # The record is taken whole: hits, total and the histories belong
            # to the run that scored it, and mixing them makes a run that
            # never happened.
            for field in ("best_accuracy", "best_hits", "best_total",
                          "section_history", "tempo_history"):
                if field in other:
                    merged[field] = other[field]
            improved.append(song)
        out[song] = merged
    return out, improved


def _read_progress(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="source", required=True,
                    help="the other machine's .pickhero folder")
    ap.add_argument("--into", default=None,
                    help=f"where to merge into (default: {CONFIG_DIR})")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would happen and write nothing")
    args = ap.parse_args()

    source = Path(args.source)
    target = Path(args.into) if args.into else CONFIG_DIR
    if not source.is_dir():
        print(f"Nicht gefunden: {source}")
        print("Kopiere den Ordner .pickhero vom anderen Rechner hierher und")
        print("zeige mit --from darauf.")
        return 1
    if source.resolve() == target.resolve():
        print("Quelle und Ziel sind derselbe Ordner - nichts zu tun.")
        return 1

    their_log = source / "practice_log.jsonl"
    my_log = target / "practice_log.jsonl"
    mine = practice_log.read(my_log)
    theirs = practice_log.read(their_log)
    merged, added = merge_sessions(mine, theirs)

    their_progress = _read_progress(source / "progress.json")
    my_progress = _read_progress(target / "progress.json")
    progress, improved = merge_progress(my_progress, their_progress)

    print(f"Von:  {source}")
    print(f"Nach: {target}")
    print()
    print(f"Sitzungen hier:      {len(mine)}")
    print(f"Sitzungen dort:      {len(theirs)}")
    print(f"davon neu:           {added}")
    minutes = sum(s.seconds for s in merged if session_key(s) not in
                  {session_key(x) for x in mine}) / 60.0
    print(f"neue Uebungszeit:    {minutes:.0f} Minuten")
    print(f"Songs mit besserer Wertung von drueben: {len(improved)}")
    for song in improved[:10]:
        print(f"   {song}")
    if len(improved) > 10:
        print(f"   ... und {len(improved) - 10} weitere")

    if args.dry_run:
        print("\n--dry-run: nichts geschrieben.")
        return 0
    if not added and not improved:
        print("\nNichts Neues - die Dateien bleiben, wie sie sind.")
        return 0

    target.mkdir(parents=True, exist_ok=True)
    if added:
        _backup(my_log)
        with open(my_log, "w", encoding="utf-8") as handle:
            for session in merged:
                handle.write(json.dumps(session.__dict__, ensure_ascii=False) + "\n")
    if improved:
        _backup(target / "progress.json")
        (target / "progress.json").write_text(
            json.dumps(progress, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    print("\nZusammengefuehrt. Sicherungskopien liegen als .bak daneben.")
    print("Dashboard neu bauen:  python tools/make_dashboard.py --open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
