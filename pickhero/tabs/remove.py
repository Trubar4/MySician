"""Move or delete a song and everything the app put there for it.

*"Ich brauche eine Möglichkeit Tabs inkl. allem (außer History) zu löschen."*

A tab is not one file any more. Downloading one writes the tab, a bar map
beside it, and the audio of the recording that map was made against; playing
it writes a speed, a recording path, sync anchors, a transpose and a sync
source into the settings. Deleting the tab in Explorer leaves all of that
behind, and the next song that happens to take the same name inherits it --
because `song_key` is the tab's STEM and nothing else.

Two rules decide what goes:

- **Only what sits beside the tab, under the tab's own name.** The recording
  the player picked himself out of his Downloads folder is his; the one this
  app downloaded is next to the tab and named after it. Same name, same
  folder, or it is not touched.
- **The practice history stays.** `progress.py` and `practice_log.py` are a
  record of what the player DID, and deleting a file does not undo an
  evening of playing it. He asked for this by name: *außer History*.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pickhero.config as config_module

from pickhero.tabs.sidecar import SUFFIX as SETTINGS_SUFFIX
from pickhero.runs import SUFFIX as RUNS_SUFFIX
from pickhero.tabs.songsterr import CACHE_SUFFIX

#: Everything the app might have downloaded or been pointed at as a
#: recording. Wider than what SDL can play, because a file this app put
#: there under the song's name is this app's to clean up whether or not it
#: turned out to be playable.
AUDIO_SUFFIXES = (".mp3", ".ogg", ".wav", ".flac", ".m4a", ".opus", ".webm")

#: Where a deleted song goes instead of nowhere.
#:
#: `Path.unlink()` does NOT reach the Windows recycle bin, so until this
#: existed DEL was the one irreversible key in the app -- and it sits one row
#: from the arrow keys. The player lost a folder of songs to it: key repeat
#: made one held press into 25 a second, each pair arming and confirming, and
#: with the tuning filter on it walked the filtered list. The repeat is fixed
#: where it belongs (`NEVER_REPEAT`), and this is the other half, because
#: **a guard stops the fault it was written for and a copy survives the one
#: nobody thought of.** It is the same rule the import already follows when
#: it leaves a `.bak` beside anything it rewrites.
#: Read through the module rather than captured at import, or it would be
#: the CONFIG_DIR that existed when this file was first imported -- which is
#: how the first version of this wrote into the player's real home directory
#: from inside the test suite, straight past the fixture that exists to stop
#: exactly that. A constant computed from another module's constant cannot be
#: redirected, and this project has paid for that once already at the build
#: stamp.
def trash_root() -> Path:
    return config_module.CONFIG_DIR / "deleted"

#: How many deletes are kept. A song with its recording is a few megabytes,
#: so twenty is a bounded cost -- and twenty is also more than a mis-held key
#: can get through in the second it now takes to notice.
MAX_TRASH = 20


@dataclass
class Removed:
    """What went, and what would not."""

    song_key: str = ""
    files: list[Path] = field(default_factory=list)
    settings: list[str] = field(default_factory=list)
    #: Named, not swallowed: a file still open in another program does not
    #: delete on Windows, and a delete that reports success while the song
    #: is still in the list is the worst of the three outcomes.
    failed: list[str] = field(default_factory=list)
    #: Where the files were kept, or None when nothing could be kept.
    kept_in: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        """One line for under the song list."""
        if self.failed:
            return f"Could not delete {self.song_key}: {self.failed[0]}"
        bits = [f"{len(self.files)} file" + ("s" if len(self.files) != 1
                                             else "")]
        if self.settings:
            bits.append("its settings")
        where = (" Ctrl+Z puts it back." if self.kept_in is not None
                 else " NOT kept — nothing to put back.")
        return (f"Deleted {self.song_key} — {', '.join(bits)}. "
                f"Practice diary kept.{where}")


def _trash_folder(stem: str):
    """A folder of its own for this delete, or None if none can be made.

    Named for the song AND the moment, because deleting the same song twice
    is an ordinary thing to do and the second one must not overwrite the
    first one's copy.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    safe = "".join(c if (c.isalnum() or c in " -_") else "_" for c in stem)
    folder = trash_root() / f"{stamp} {safe}".strip()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    except OSError:
        return None                  # a delete is never blocked by the copy


def trashed() -> list[Path]:
    """Every kept delete, newest first."""
    try:
        return sorted((p for p in trash_root().iterdir() if p.is_dir()),
                      key=lambda p: p.name, reverse=True)
    except OSError:
        return []


def _prune_trash() -> None:
    """Keep the newest `MAX_TRASH` and let the rest go."""
    for stale in trashed()[MAX_TRASH:]:
        try:
            shutil.rmtree(stale)
        except OSError:
            pass


def restore_last(songs_dir) -> Removed:
    """Put the most recent delete back, and say what came.

    The per-song settings come back with it: the sidecar is one of the
    belongings, so it is in the folder, and the song list adopts what the
    folder knows and this machine does not on its next scan. The practice
    diary never went anywhere.

    A file whose name is already taken again is NOT overwritten -- that
    would be an undo destroying something newer than what it is undoing.
    """
    out = Removed()
    folders = trashed()
    if not folders:
        out.failed.append("nothing to put back")
        return out
    folder = folders[0]
    out.song_key = folder.name.split(" ", 1)[-1]
    target_dir = Path(songs_dir)
    for item in sorted(folder.iterdir()):
        target = target_dir / item.name
        if target.exists():
            out.failed.append(f"{item.name} (already there again)")
            continue
        try:
            shutil.move(str(item), str(target))
            out.files.append(target)
        except OSError as exc:
            out.failed.append(f"{item.name} ({exc.strerror or exc})")
    if not any(folder.iterdir()):
        try:
            folder.rmdir()
        except OSError:
            pass
    return out


def belongings(tab_path) -> list[Path]:
    """Every file that belongs to this tab, the tab itself first.

    Listed rather than deleted, so the confirmation can say how many there
    are before anything happens.
    """
    tab = Path(tab_path)
    found = [tab] if tab.is_file() else []
    beside = [tab.with_name(tab.stem + CACHE_SUFFIX),
              tab.with_name(tab.stem + SETTINGS_SUFFIX),
              # The runs of THIS tab. Not the practice diary, which is a
              # record of what the player did and stays: these are per-note
              # verdicts of a file that is about to stop existing, and the
              # next song to take the name would inherit them.
              tab.with_name(tab.stem + RUNS_SUFFIX)]
    beside += [tab.with_name(tab.stem + suffix) for suffix in AUDIO_SUFFIXES]
    found += [p for p in beside if p.is_file()]
    return found


def delete_song(tab_path, config=None) -> Removed:
    """Delete a tab, its bar map, its downloaded audio and its settings.

    Never raises. A file that will not go is reported by name -- the song
    list refreshes from the disk afterwards, so a half-done delete shows
    itself rather than being claimed as finished.
    """
    tab = Path(tab_path)
    out = Removed(song_key=tab.stem)

    keep = _trash_folder(tab.stem)
    for target in belongings(tab):
        try:
            if keep is not None:
                # MOVED, not unlinked. A move that fails leaves the file
                # exactly where it was, which is the safe way for this to go
                # wrong: a full disk makes DEL report a failure rather than
                # destroy a song it could not keep a copy of.
                shutil.move(str(target), str(keep / target.name))
            else:
                target.unlink()
            out.files.append(target)
            out.kept_in = keep
        except OSError as exc:
            out.failed.append(f"{target.name} ({exc.strerror or exc})")
    _prune_trash()

    if config is not None:
        forget = getattr(config, "forget_song", None)
        if forget is not None:
            out.settings = forget(tab.stem)
        try:
            config.save()
        except OSError as exc:
            out.failed.append(f"settings ({exc.strerror or exc})")
    return out


# ── Renaming ────────────────────────────────────────────────────────────────


@dataclass
class Renamed:
    """What moved, and what would not."""

    old_key: str = ""
    new_key: str = ""
    files: list[Path] = field(default_factory=list)
    tab_path: Path | None = None
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed and self.tab_path is not None

    def summary(self) -> str:
        if self.failed:
            return f"Could not rename {self.old_key}: {self.failed[0]}"
        extra = len(self.files) - 1
        also = f" and {extra} file" + ("s" if extra != 1 else "") if extra else ""
        return f"Renamed to {self.new_key}{also}. Settings and history kept."


def safe_name(text: str) -> str:
    """A name Windows will actually accept, or "" if nothing is left.

    The player types this, so it is checked here rather than trusted: a
    colon or a slash in a tab name is a rename that fails with an error he
    has no way to interpret, and a leading dot is a file he cannot see.
    """
    import re
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text or "").strip(" .")
    return cleaned


def rename_song(tab_path, new_stem: str, config=None) -> Renamed:
    """Rename a tab, and take its bar map, audio and settings with it.

    **The settings travel because `song_key` IS the stem.** Renaming the tab
    alone would leave the speed, the recording, the sync points, the
    Songsterr id and the transpose behind under the old name -- looking to
    the player like a rename that quietly wiped the song's setup, and
    waiting to be inherited by the next song that takes the old name.

    The practice history is keyed the same way, and it moves too: it is a
    record of playing THIS PIECE, and the piece did not change.

    Never raises. Nothing is moved unless the new name is free.
    """
    tab = Path(tab_path)
    out = Renamed(old_key=tab.stem, new_key=new_stem)

    wanted = safe_name(new_stem)
    if not wanted:
        out.failed.append("that name has no usable characters")
        return out
    out.new_key = wanted
    if wanted == tab.stem:
        out.tab_path = tab
        return out                         # nothing to do, and not an error

    moves = [(p, p.with_name(wanted + p.name[len(tab.stem):]))
             for p in belongings(tab)]
    clash = [b for _, b in moves if b.exists()]
    if clash:
        # Checked BEFORE anything moves. A half-done rename leaves a tab
        # under one name and its recording under another, which is worse
        # than not renaming at all.
        out.failed.append(f"{clash[0].name} is already there")
        return out

    for source, target in moves:
        try:
            source.rename(target)
            out.files.append(target)
            if target.suffix == tab.suffix:
                out.tab_path = target
        except OSError as exc:
            out.failed.append(f"{source.name} ({exc.strerror or exc})")
            return out

    if config is not None:
        mover = getattr(config, "rename_song", None)
        if mover is not None:
            mover(tab.stem, wanted)
        # The settings carry a PATH to the recording, and the recording has
        # just moved. Carrying the entry to the new song key without
        # rewriting it leaves a note pointing at a file that is no longer
        # there -- which is not "no recording", it is a wrong one, and those
        # fail differently.
        _repoint_recording(config, wanted, moves)
        try:
            config.save()
        except OSError as exc:
            out.failed.append(f"settings ({exc.strerror or exc})")
    _carry_history(tab.stem, wanted)
    # The sidecar moved with the rest, but it still says the OLD name
    # inside. Rewritten from what the settings now hold, so a copy of this
    # folder carries the song under the name it actually has.
    if out.tab_path is not None and config is not None:
        from pickhero.tabs import sidecar
        best = None
        try:
            from pickhero.progress import ProgressTracker
            best = ProgressTracker().get_best(wanted)
        except Exception:
            pass
        sidecar.write(out.tab_path, wanted, config, best)
    return out


def _carry_history(old_key: str, new_key: str) -> None:
    """Move the practice record too. Silent when there is nothing to move.

    A rename is not a new piece. Losing the best-ever score for a song
    because its file was given a tidier name is the kind of thing nobody
    notices until they go looking for it months later.
    """
    try:
        from pickhero.progress import ProgressTracker
        tracker = ProgressTracker()
        record = tracker.get_best(old_key)
        if record is None:
            return
        tracker._data[new_key] = record
        tracker._data.pop(old_key, None)
        tracker._save()
    except Exception:
        # The history is a bonus here, not the job. A tracker that cannot be
        # written must not take the rename down with it.
        pass


def _repoint_recording(config, song_key: str, moves) -> None:
    """Point the stored recording at where the file actually went."""
    getter = getattr(config, "song_mp3_paths", None)
    if not isinstance(getter, dict):
        return
    stored = str(getter.get(song_key, "") or "")
    if not stored:
        return
    for source, target in moves:
        if str(source) == stored:
            getter[song_key] = str(target)
            return
