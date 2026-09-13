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
- `settings.json` is merged **selectively, and that is the whole point.**
  What belongs to the SONG comes across -- its practice speed, its backing
  track and both offsets, and the favourites. What belongs to the MACHINE
  must not: the audio device index, the calibration and the latency offset
  describe an interface and a sound card, and carrying them over would break
  the other computer's input while looking like a settings problem. Copying
  the whole file is the obvious move and the wrong one.

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
# The merge itself lives in the PACKAGE, not here. `tools/` is not in the
# .exe, and the laptop that most needs to merge is the one with only the
# .exe on it -- so the song list can do this too (Ctrl+I) and both front
# ends run the same code. This file is the command line for it.
from pickhero.transfer import (  # noqa: E402
    backup as _backup, merge_progress, merge_sessions, merge_settings,
    read_json as _read_json, session_key, song_settings)

SONG_SETTINGS = song_settings()
_read_progress = _read_json


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

    my_settings = _read_json(target / "settings.json")
    settings, setting_changes = merge_settings(
        my_settings, _read_json(source / "settings.json"))

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

    if setting_changes:
        print()
        print("Einstellungen pro Song:")
        for line in setting_changes:
            print(f"   {line}")
        print("   (Audiogeraet, Kalibrierung und Latenz bleiben, wie sie hier "
              "sind — die gehoeren zum Rechner)")

    if args.dry_run:
        print("\n--dry-run: nichts geschrieben.")
        return 0
    if not added and not improved and not setting_changes:
        print("\nNichts Neues - die Dateien bleiben, wie sie sind.")
        return 0

    target.mkdir(parents=True, exist_ok=True)
    if added:
        _backup(my_log)
        practice_log.write(my_log, merged)
    if improved:
        _backup(target / "progress.json")
        (target / "progress.json").write_text(
            json.dumps(progress, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    if setting_changes:
        _backup(target / "settings.json")
        (target / "settings.json").write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    print("\nZusammengefuehrt. Sicherungskopien liegen als .bak daneben.")
    print("Dashboard neu bauen:  python tools/make_dashboard.py --open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
