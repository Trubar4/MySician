"""Bring another computer's work into this one.

*"Auf NB2 geht keiner der Wege mit OneDrive, iCloud oder Dropbox. Was wäre
die Alternative, damit ich nur ein Minimum an Files kopieren muss?"*

A laptop that cannot have a cloud client installed still has a USB stick,
and a stick is a real folder. So the transfer is: copy a folder across,
point this at it, and let it take what is missing.

**Most of it already travels.** Since the song settings moved into
`<name>.mysician.json` beside the tab, a song IS its four files -- the tab,
the bar map, the recording and the settings -- and copying the songs folder
carries the speed, the sync points, the Songsterr id, the transpose, the
star and the best score with it. What is left over is the chronological
sitting log, and whatever still sits in an older `settings.json`.

The rules are the ones `merge_stats.py` has always used, and they are here
now rather than there because `tools/` is not in the .exe and the laptop
that needs this is the one with only the .exe on it:

- **Running it twice changes nothing the second time.** Nobody remembers
  whether they already did it, and a history that doubles is worse than one
  that is missing.
- **What this machine has WINS.** A sync that silently overwrites what you
  just adjusted is worse than no sync.
- **Nothing that belongs to the MACHINE moves.** The audio device index, the
  calibration and the latency offset describe an interface and a sound card.
  Carrying them over breaks the other computer's input while looking like a
  settings problem. Copying the whole file is the obvious move and the
  wrong one.
- **Files are never overwritten, only added.** A song this machine already
  has keeps the copy it has, whatever the other one says.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field, fields
from pathlib import Path

from pickhero import practice_log
from pickhero.config import CONFIG_DIR, Config


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
        # attempts: the larger, NOT the sum. A sum cannot be done twice
        # safely, and the honest count of sittings is in the practice log.
        merged["attempts"] = max(current.get("attempts", 0),
                                 other.get("attempts", 0))
        merged["last_played"] = max(current.get("last_played", ""),
                                    other.get("last_played", ""))
        if other.get("best_accuracy", 0.0) > current.get("best_accuracy", 0.0):
            # The record is taken whole: hits, total and the histories belong
            # to the run that scored it, and mixing them makes a run that
            # never happened.
            for name in ("best_accuracy", "best_hits", "best_total",
                         "section_history", "tempo_history"):
                if name in other:
                    merged[name] = other[name]
            improved.append(song)
        out[song] = merged
    return out, improved


def song_settings() -> tuple[str, ...]:
    """Every per-song setting there is. FOUND, not listed.

    This was a hand-written tuple of seven names and by the time anybody
    looked it was missing two. The rule that makes finding them safe is a
    naming one that already holds: everything scoped to a SONG is a dict
    called `song_something`, and everything scoped to the machine is not.
    """
    blank = Config()
    return tuple(f.name for f in fields(Config)
                 if f.name.startswith("song_")
                 and isinstance(getattr(blank, f.name, None), dict))


def merge_settings(mine: dict, theirs: dict) -> tuple[dict, list[str]]:
    """(merged, what changed). Per-song entries and favourites only."""
    out = dict(mine)
    changed = []
    for name in song_settings():
        ours = dict(out.get(name) or {})
        added = 0
        for song, value in (theirs.get(name) or {}).items():
            if song not in ours:
                ours[song] = value
                added += 1
        if added:
            out[name] = ours
            changed.append(f"{name}: {added}")
    stars = list(out.get("favourites") or [])
    new_stars = [s for s in (theirs.get("favourites") or []) if s not in stars]
    if new_stars:
        out["favourites"] = stars + new_stars
        changed.append(f"favourites: {len(new_stars)}")
    return out, changed


def read_json(path: Path) -> dict:
    """A dict from a JSON file, or {} for anything that is not one."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def backup(path: Path) -> None:
    """Keep what is about to be replaced, beside it."""
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))


# ── Finding the other machine's things ──────────────────────────────────────

def find_pickhero(folder) -> Path | None:
    """The `.pickhero` folder inside (or equal to) what was picked.

    The player picks a stick, not a hidden folder three levels down. So the
    folder itself, a `.pickhero` in it, and one level below that are all
    accepted -- guessing right here is worth more than being strict.
    """
    root = Path(folder)
    for candidate in (root, root / ".pickhero"):
        if (candidate / "practice_log.jsonl").is_file() or \
                (candidate / "settings.json").is_file():
            return candidate
    try:
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "settings.json").is_file():
                return child
    except OSError:
        pass
    return None


def find_songs(folder) -> list[Path]:
    """Every tab under the picked folder, wherever it sits inside it."""
    from pickhero.tabs.loader import GP_EXTENSIONS
    try:
        return sorted(p for p in Path(folder).rglob("*")
                      if p.is_file() and p.suffix.lower() in GP_EXTENSIONS)
    except OSError:
        return []


@dataclass
class Report:
    """What an import did, or would do."""

    source: str = ""
    dry_run: bool = False
    songs_added: list[str] = field(default_factory=list)
    files_added: int = 0
    sittings_added: int = 0
    bests_improved: list[str] = field(default_factory=list)
    settings_changed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def anything(self) -> bool:
        return bool(self.songs_added or self.sittings_added
                    or self.bests_improved or self.settings_changed)

    def lines(self) -> list[str]:
        """One line per kind of thing, for the screen."""
        head = "Would import from" if self.dry_run else "Imported from"
        out = [f"{head} {self.source}"]
        if self.songs_added:
            out.append(f"{len(self.songs_added)} new song"
                       + ("s" if len(self.songs_added) != 1 else "")
                       + f" ({self.files_added} files): "
                       + ", ".join(self.songs_added[:4])
                       + (" …" if len(self.songs_added) > 4 else ""))
        if self.sittings_added:
            out.append(f"{self.sittings_added} practice sittings")
        if self.bests_improved:
            out.append(f"{len(self.bests_improved)} better scores: "
                       + ", ".join(self.bests_improved[:4])
                       + (" …" if len(self.bests_improved) > 4 else ""))
        if self.settings_changed:
            out.append("settings: " + ", ".join(self.settings_changed))
        if not self.anything:
            out.append("Nothing new — this machine already has all of it.")
        out += self.problems
        out.append("Your audio device, calibration and latency were not "
                   "touched.")
        return out


def import_songs(source, songs_dir, report: Report) -> None:
    """Copy over song files this machine has not got. Never overwrites.

    Per FILE, not per song: a sidecar that arrives beside a tab we already
    have can only add settings we do not have, and the tab itself is left
    exactly as it is. Overwriting would mean the other machine's copy of a
    song silently replacing the one being practised here.
    """
    from pickhero.tabs.remove import belongings
    target = Path(songs_dir)
    for tab in find_songs(source):
        if Path(tab).parent.resolve() == target.resolve():
            continue                      # already looking at our own folder
        wanted = [p for p in belongings(tab)
                  if not (target / p.name).exists()]
        if not wanted:
            continue
        if report.dry_run:
            report.songs_added.append(tab.stem)
            report.files_added += len(wanted)
            continue
        copied = 0
        for path in wanted:
            try:
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target / path.name)
                copied += 1
            except OSError as exc:
                report.problems.append(f"{path.name}: {exc.strerror or exc}")
        if copied:
            report.songs_added.append(tab.stem)
            report.files_added += copied


def import_from(source, config=None, dry_run: bool = False,
                into: Path | None = None) -> Report:
    """Take everything this machine is missing out of `source`.

    Never raises. A folder with nothing in it is a report saying so, which
    is what the player needs to see -- not a traceback and not silence.
    """
    root = Path(source)
    report = Report(source=str(root), dry_run=dry_run)
    if not root.is_dir():
        report.problems.append("That folder is not there.")
        return report

    target = Path(into) if into is not None else CONFIG_DIR
    songs_dir = (config.songs_path() if config is not None
                 else target / "songs")
    if root.resolve() == Path(songs_dir).resolve():
        report.problems.append("That is this machine's own songs folder.")
        return report

    import_songs(root, songs_dir, report)

    theirs = find_pickhero(root)
    if theirs is None:
        if not report.anything:
            report.problems.append(
                "No settings or practice history found in there — only the "
                "songs were looked at.")
        return report
    if theirs.resolve() == target.resolve():
        report.problems.append("That is this machine's own settings folder.")
        return report

    # ── the sitting log ────────────────────────────────────────────────────
    my_log, their_log = target / "practice_log.jsonl", \
        theirs / "practice_log.jsonl"
    merged_log, report.sittings_added = merge_sessions(
        practice_log.read(my_log), practice_log.read(their_log))

    # ── the best scores ────────────────────────────────────────────────────
    progress, report.bests_improved = merge_progress(
        read_json(target / "progress.json"),
        read_json(theirs / "progress.json"))

    # ── and whatever is still only in an older settings.json ───────────────
    settings, report.settings_changed = merge_settings(
        read_json(target / "settings.json"),
        read_json(theirs / "settings.json"))

    if dry_run or not report.anything:
        return report

    try:
        target.mkdir(parents=True, exist_ok=True)
        if report.sittings_added:
            backup(my_log)
            if not practice_log.write(my_log, merged_log):
                report.problems.append("The practice log could not be "
                                       "written.")
        if report.bests_improved:
            backup(target / "progress.json")
            (target / "progress.json").write_text(
                json.dumps(progress, indent=2), encoding="utf-8")
        if report.settings_changed:
            backup(target / "settings.json")
            (target / "settings.json").write_text(
                json.dumps(settings, indent=2), encoding="utf-8")
    except OSError as exc:
        report.problems.append(f"Could not write: {exc.strerror or exc}")
    return report
