"""Write the build stamp the EXE carries, so the running app can say which
version it is.

One writer, called by `build.bat` AND by the release workflow. It had two
before this: `build.bat` wrote the format with an inline `python -c` while
`build_info.write_stamp` sat in the package, tested and called by nothing --
and the workflow wrote no stamp at all, so **every EXE GitHub Actions has
ever produced reported `unknown build`**. That is the one question the stamp
exists to answer: "is it fixed" and "did it reach the machine" are the same
question without it, and this project has paid for that three times.

Run from the project root, before PyInstaller:

    python tools/stamp_build.py
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pickhero.build_info import STAMP_FILE, write_stamp  # noqa: E402


def head_sha() -> str:
    """HEAD's short hash, or a word saying there is no git here.

    Never raises: a build must not fail because it could not name itself.
    """
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            capture_output=True, text=True, cwd=ROOT, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "no-git"
    return done.stdout.strip() or "no-git"


def main() -> int:
    target = write_stamp(
        ROOT / "pickhero" / STAMP_FILE,
        head_sha(),
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    print(f"Build stamp: {target.read_text(encoding='utf-8')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
