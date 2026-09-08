"""Write the practice dashboard from the command line.

The page itself is built by `pickhero/dashboard.py` -- it lives in the package
because `pickhero.spec` bundles the package alone, and the app writes the page
itself when the window closes. This is the way to write it on demand, or from
a log file somewhere else.

    python tools/make_dashboard.py              # writes ~/.pickhero/dashboard.html
    python tools/make_dashboard.py --open       # and opens it in the browser
    python tools/make_dashboard.py --out x.html --file some_log.jsonl
"""

import argparse
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero import dashboard, practice_log  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=None, help="a practice log to read")
    ap.add_argument("--out", default=None, help="where to write the page")
    ap.add_argument("--open", action="store_true", help="open it when done")
    args = ap.parse_args()

    sessions = practice_log.read(Path(args.file) if args.file else None)
    if not sessions:
        print("Noch nichts aufgezeichnet.")
        print(f"Die App schreibt nach {practice_log.PRACTICE_FILE},")
        print("sobald du einen Song mindestens "
              f"{practice_log.MIN_SESSION_SECONDS:.0f} Sekunden gespielt hast.")
        return 1

    out = dashboard.write(Path(args.out) if args.out else None, sessions)
    print(f"{out}  ({out.stat().st_size // 1024} KB, {len(sessions)} Sitzungen)")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
