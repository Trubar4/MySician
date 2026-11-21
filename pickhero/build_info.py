"""Which build is this, said where the player can read it.

Three fixes in a row were reported as "does nothing" while their code was in
the tree and under test -- Shift+U, Shift+C, and the footer that would not
wrap. Every one of them was a build that did not contain them, and each cost
a round trip to establish. Nothing in the app could say which version was
running, so "is this fixed" and "did this reach my machine" were the same
question with no way to tell them apart.

The stamp is written at build time by `build.bat` and bundled into the EXE.
Running from a checkout the git HEAD is read straight off the filesystem --
no subprocess, because this is asked while the window is coming up and a
build stamp must never be a reason the app is slow to start or fails to
start at all.

**The stamp only counts inside a built EXE**, and that was not always so.
`build.bat` writes it INTO the checkout (`pickhero/_build_stamp.txt`, which
is gitignored for that reason), and the stamp used to outrank git wherever
it was found. So any tree that had ever been built reported the commit it
was built at for ever after: `git pull`, run `python -m pickhero`, and the
app names a version from days ago. That is precisely the "is it fixed or did
it not reach the machine" confusion this whole file exists to end -- shipped
by the thing built to end it.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

STAMP_FILE = "_build_stamp.txt"
UNKNOWN = "unknown build"


def _frozen() -> bool:
    """Is this a built EXE rather than a checkout?

    `_MEIPASS` for a one-file build, `sys.frozen` for a one-dir one. Either
    is the question being asked: does this process have a build stamp of its
    own, or is the file beside the module a leftover from a build run here.
    """
    return getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")


def _bundled() -> str:
    """The stamp PyInstaller carried along, if this is a built EXE."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = Path(__file__).resolve().parent
    try:
        text = (Path(base) / STAMP_FILE).read_text(encoding="utf-8")
    except OSError:
        return ""
    return text.strip()


def _packed(git: Path, ref: str) -> str:
    """The sha for `ref` out of `packed-refs`, where git keeps a tidied one.

    A fresh clone has no loose file for its branch at all, so without this a
    checkout somebody has just cloned reports "unknown build" -- which is
    the one answer this module exists to avoid.
    """
    try:
        lines = (git / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        if line.startswith("#") or line.startswith("^"):
            continue
        sha, _, name = line.partition(" ")
        if name.strip() == ref:
            return sha.strip()
    return ""


def _from_git() -> str:
    """HEAD, read off the filesystem rather than by running git.

    A checkout is what a developer runs, and there the interesting thing is
    the commit. Reading `.git` directly costs a couple of file opens; a
    subprocess costs tens of milliseconds and can hang on a network drive.

    **The date comes from the file the SHA was read from, and that was a
    real bug.** It used to be `HEAD`'s own mtime -- and a `git pull` that
    fast-forwards a branch writes `refs/heads/<branch>`, never `HEAD`, which
    is only touched when somebody switches branch. So the line carried
    today's commit beside the date the branch was checked out: measured on
    the player's own log, a sha from 16:13 stamped **two weeks** earlier.
    A date that stale makes a current build look ancient, which is exactly
    the "is it fixed or did it not reach my machine" confusion this file is
    here to end -- and it nearly had me tell the player to pull a branch he
    was already running.
    """
    root = Path(__file__).resolve().parent.parent
    git = root / ".git"
    try:
        ref = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    try:
        stamped = git / "HEAD"
        if ref.startswith("ref: "):
            name = ref[5:]
            branch = name.rsplit("/", 1)[-1]
            target = git / name
            try:
                sha = target.read_text(encoding="utf-8").strip()
                stamped = target
            except OSError:
                # No loose file: a fresh clone keeps its refs packed, and
                # `packed-refs` was written when they were packed -- which
                # for a clone is when the commit arrived. HEAD's own mtime
                # can be far older, so it is the worse of the two here too.
                sha = _packed(git, name)
                if not sha:
                    return ""
                stamped = git / "packed-refs"
        else:
            sha, branch = ref, "detached"
        when = datetime.fromtimestamp(stamped.stat().st_mtime, timezone.utc)
        return f"{sha[:8]} {branch} {when:%Y-%m-%d %H:%M} (checkout)"
    except (OSError, ValueError):
        return ""


def build_stamp() -> str:
    """One line naming this build. Never raises and never blocks.

    A checkout asks git FIRST: the commit is what is running there, and a
    stamp beside the module is only what `build.bat` last wrote. The stamp is
    still the fallback, because a source tree copied without its `.git` has
    nothing else to say and a stale answer beats "unknown build".
    """
    if _frozen():
        return _bundled() or _from_git() or UNKNOWN
    return _from_git() or _bundled() or UNKNOWN


def write_stamp(target: Path, sha: str, when: str) -> Path:
    """Write the stamp a build should carry. Called by the build script."""
    target = Path(target)
    target.write_text(f"{sha} built {when}", encoding="utf-8")
    return target
