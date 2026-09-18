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


def belonging_stem(path) -> str | None:
    """The song this file belongs to, or None if it belongs to no song.

    A belonging is named `<stem><suffix>`, and `.runs.json` is not something
    `Path.suffix` can see -- it would read the stem as "<name>.runs". So the
    known suffixes are matched whole, longest first.
    """
    from pickhero.tabs.remove import AUDIO_SUFFIXES
    from pickhero.tabs.sidecar import SUFFIX as SETTINGS_SUFFIX
    from pickhero.tabs.songsterr import CACHE_SUFFIX
    from pickhero.runs import SUFFIX as RUNS_SUFFIX
    name = Path(path).name
    known = sorted((RUNS_SUFFIX, SETTINGS_SUFFIX, CACHE_SUFFIX)
                   + tuple(AUDIO_SUFFIXES), key=len, reverse=True)
    for suffix in known:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[:-len(suffix)]
    return None


def tab_named(folder, stem: str) -> Path | None:
    """This machine's tab for that song, whatever generation it is."""
    from pickhero.tabs.loader import GP_EXTENSIONS
    for extension in sorted(GP_EXTENSIONS):
        candidate = Path(folder) / (stem + extension)
        if candidate.is_file():
            return candidate
    return None


def songs_under(source, target) -> dict:
    """{stem: the files under `source` that belong to it}.

    **A tab makes a song. So does a belonging whose tab is already HERE.**
    That second half is what an export folder is -- the history without the
    tabs, because the tabs are on the other computer already and there is no
    reason to carry a megabyte of them back and forth to move a few hundred
    bytes of verdicts: *"Da gp, mp3, songsterr schon auf NB2 sind, sehe ich
    keinen Grund diese jedes Mal mitzukopieren."* Until now the import walked
    TABS, so a folder holding `<song>.runs.json` and nothing else was read as
    an empty folder and said so.

    What it deliberately will not do is let a stray file invent a song: a
    recording or a `.json` whose tab this machine has never seen is left
    alone, because nothing can say which song it is a belonging OF.
    """
    from pickhero.tabs.remove import belongings
    root, here = Path(source), Path(target)
    try:
        mine = here.resolve()
    except OSError:
        mine = here
    found: dict = {}
    for tab in find_songs(root):
        if tab.parent.resolve() == mine:
            continue                      # already looking at our own folder
        found[tab.stem] = belongings(tab)
    try:
        loose = sorted(p for p in root.rglob("*") if p.is_file())
    except OSError:
        loose = []
    for path in loose:
        stem = belonging_stem(path)
        if stem is None or stem in found:
            continue
        if path.parent.resolve() == mine:
            continue
        if tab_named(here, stem) is None:
            continue           # no tab anywhere for it: a stray file, not a song
        # `belongings` finds everything beside a tab by stem; the tab named
        # here does not exist, so it is not in the list, which is the point.
        found[stem] = belongings(path.with_name(stem + ".gp5"))
    return found


@dataclass
class Report:
    """What an import did, or would do."""

    source: str = ""
    dry_run: bool = False
    songs_added: list[str] = field(default_factory=list)
    files_added: int = 0
    sittings_added: int = 0
    runs_added: int = 0
    bests_improved: list[str] = field(default_factory=list)
    settings_changed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def anything(self) -> bool:
        return bool(self.songs_added or self.files_added or self.sittings_added
                    or self.runs_added or self.bests_improved
                    or self.settings_changed)

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
        elif self.files_added:
            # No tab arrived, so these are belongings of songs already here
            # -- which is what an export folder holds.
            out.append(f"{self.files_added} file"
                       + ("s" if self.files_added != 1 else "")
                       + " added beside songs you already have")
        if self.sittings_added:
            out.append(f"{self.sittings_added} practice sittings")
        if self.runs_added:
            out.append(f"{self.runs_added} run"
                       + ("s" if self.runs_added != 1 else "")
                       + " of songs you already have")
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
    from pickhero import runs as runs_mod
    from pickhero.tabs.loader import GP_EXTENSIONS
    target = Path(songs_dir)
    for stem, files in sorted(songs_under(source, target).items()):
        # The runs are the one belonging that MERGES rather than being kept
        # or copied whole: a history is a list, and two lists of different
        # evenings have a union. Everything else beside a tab is a single
        # answer, where "what this machine has wins" is the only safe rule.
        report.runs_added += _merge_runs(stem, files, target, report, runs_mod)
        wanted = [p for p in files if not (target / p.name).exists()]
        if not wanted:
            continue
        # A song is NEW when its tab arrived. A folder of nothing but
        # histories adds files to songs this machine already has, and
        # calling that "3 new songs" would be a count of something else.
        arrived = any(p.suffix.lower() in GP_EXTENSIONS
                      for p in wanted)
        if report.dry_run:
            if arrived:
                report.songs_added.append(stem)
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
        if copied and arrived:
            report.songs_added.append(stem)
        report.files_added += copied


def _merge_runs(stem: str, files, target: Path, report: Report,
                runs_mod) -> int:
    """Take the other machine's runs of a song this one already has.

    Only where the tab is REALLY the same song by name -- the rule every
    belonging here follows. A song this machine has never seen is copied
    whole by the loop above, runs and all, and needs nothing from here.
    """
    theirs = next((p for p in files
                   if p.name.endswith(runs_mod.SUFFIX)), None)
    if theirs is None or not theirs.is_file():
        return 0
    here = tab_named(target, stem)        # the same song, in our folder
    if here is None or not runs_mod.path_for(here).is_file():
        # Nothing of ours to merge INTO: a song this machine has never seen,
        # or one it has never played. The ordinary file loop copies the
        # history over whole, which is the same answer by a cheaper route --
        # and running both would count it twice.
        return 0
    if report.dry_run:
        _, would = runs_mod.merge(runs_mod.load(here), runs_mod.load(theirs))
        return would
    backup(runs_mod.path_for(here))
    return runs_mod.merge_files(here, theirs)


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


# ── The other direction: writing a folder to carry ──────────────────────────

#: Where an export puts the song histories. A folder of its own, so the
#: import finds them exactly where it finds a real songs folder's -- and so
#: nothing an export writes can land beside the practice diary and be read
#: as part of it.
EXPORT_SONGS = "songs"


@dataclass
class Export:
    """What was written out, for the same panel the import report uses."""

    target: str = ""
    songs: list = field(default_factory=list)
    runs: int = 0
    sittings: int = 0
    bests: int = 0
    settings: bool = False
    problems: list = field(default_factory=list)

    @property
    def anything(self) -> bool:
        return bool(self.songs or self.sittings or self.bests
                    or self.settings)

    def lines(self) -> list[str]:
        out = [f"Wrote your history to {self.target}"]
        if self.songs:
            out.append(f"{self.runs} run" + ("s" if self.runs != 1 else "")
                       + f" over {len(self.songs)} song"
                       + ("s" if len(self.songs) != 1 else "")
                       + ": " + ", ".join(self.songs[:4])
                       + (" …" if len(self.songs) > 4 else ""))
        if self.sittings:
            out.append(f"{self.sittings} practice sittings")
        if self.bests:
            out.append(f"best scores for {self.bests} song"
                       + ("s" if self.bests != 1 else ""))
        if not self.anything:
            out.append("Nothing to write — no runs and no practice diary "
                       "on this machine yet.")
        out += self.problems
        out.append("No tabs, recordings or bar maps — take this folder to "
                   "the other computer and press Ctrl+I there.")
        return out


def export_to(folder, config=None, into: Path | None = None) -> Export:
    """Write everything this machine knows about PLAYING, and nothing else.

    *"Am liebsten waere mir ein Button oder Key in der Uebersicht, um alle
    History/Rundaten (ohne MP3, gp, songsterr) in einen Ordner zu schreiben,
    den ich dann kopieren kann."*

    Hand-copying the two files he named did not work, and the reason is that
    the import walked TABS: a folder holding `<song>.runs.json` and nothing
    beside it looked empty. `songs_under` reads it now, and this writes the
    folder that reads back.

    **The tabs, the recordings and the bar maps are deliberately absent.**
    They are the megabytes and they are already on the other machine. What
    is left is a couple of hundred kilobytes: a run is one character per
    note, capped at `runs.MAX_RUNS`, and a sitting is one line of JSON.

    Never raises. A folder that cannot be written is a report saying which
    file and why -- an export that half-succeeded in silence is worse than
    one that did not run.
    """
    from pickhero import runs as runs_mod
    from pickhero.tabs.sidecar import SUFFIX as SETTINGS_SUFFIX

    root = Path(folder)
    out = Export(target=str(root))
    songs_dir = Path(config.songs_path()) if config is not None else None
    home = Path(into) if into is not None else CONFIG_DIR

    if songs_dir is not None:
        try:
            if root.resolve() == songs_dir.resolve():
                out.problems.append("That is this machine's own songs folder.")
                return out
        except OSError:
            pass
    try:
        if root.resolve() == home.resolve():
            out.problems.append("That is this machine's own settings folder.")
            return out
    except OSError:
        pass

    try:
        (root / EXPORT_SONGS).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        out.problems.append(f"Could not write there: {exc.strerror or exc}")
        return out

    # ── the per-song history ───────────────────────────────────────────────
    if songs_dir is not None:
        for tab in sorted(find_songs(songs_dir)):
            history = runs_mod.path_for(tab)
            sidecar = tab.with_name(tab.stem + SETTINGS_SUFFIX)
            taken = 0
            for path in (history, sidecar):
                if not path.is_file():
                    continue
                try:
                    shutil.copy2(path, root / EXPORT_SONGS / path.name)
                    taken += 1
                except OSError as exc:
                    out.problems.append(
                        f"{path.name}: {exc.strerror or exc}")
            if taken:
                out.songs.append(tab.stem)
                out.runs += len(runs_mod.load(tab))

    # ── the diary, the best scores and the leftover settings ───────────────
    for name in ("practice_log.jsonl", "progress.json", "settings.json"):
        source = home / name
        if not source.is_file():
            continue
        try:
            shutil.copy2(source, root / name)
        except OSError as exc:
            out.problems.append(f"{name}: {exc.strerror or exc}")
            continue
        if name == "practice_log.jsonl":
            out.sittings = len(practice_log.read(source))
        elif name == "progress.json":
            out.bests = len(read_json(source))
        else:
            out.settings = True
    return out
