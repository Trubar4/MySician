"""Delete a song and everything the app put there for it.

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

from dataclasses import dataclass, field
from pathlib import Path

from pickhero.tabs.songsterr import CACHE_SUFFIX

#: Everything the app might have downloaded or been pointed at as a
#: recording. Wider than what SDL can play, because a file this app put
#: there under the song's name is this app's to clean up whether or not it
#: turned out to be playable.
AUDIO_SUFFIXES = (".mp3", ".ogg", ".wav", ".flac", ".m4a", ".opus", ".webm")


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
        return f"Deleted {self.song_key} — {', '.join(bits)}. History kept."


def belongings(tab_path) -> list[Path]:
    """Every file that belongs to this tab, the tab itself first.

    Listed rather than deleted, so the confirmation can say how many there
    are before anything happens.
    """
    tab = Path(tab_path)
    found = [tab] if tab.is_file() else []
    beside = [tab.with_name(tab.stem + CACHE_SUFFIX)]
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

    for target in belongings(tab):
        try:
            target.unlink()
            out.files.append(target)
        except OSError as exc:
            out.failed.append(f"{target.name} ({exc.strerror or exc})")

    if config is not None:
        forget = getattr(config, "forget_song", None)
        if forget is not None:
            out.settings = forget(tab.stem)
        try:
            config.save()
        except OSError as exc:
            out.failed.append(f"settings ({exc.strerror or exc})")
    return out
