"""What is inside each song, without opening it.

The song list wants to say how many instruments a file holds and how each is
tuned. Both answers need the file unpacked -- and for a GP6 container,
decompressed first -- which is far too slow to do for a whole folder while
the player waits for a list to appear.

So it is read once and remembered:

- **Kept on disk** (`~/.pickhero/song_index.json`), keyed by the file's size
  and modification time. A file that has not changed is never opened again,
  and one that HAS changed is read afresh rather than believed.
- **Read on a thread**, newest file first, so the list is on screen from the
  first frame and fills itself in. A song not yet indexed simply shows
  nothing; it never shows a wrong answer while it waits.
- **Saved as it goes.** A folder of two hundred songs is seconds of work, and
  quitting halfway through must not throw all of it away.

Only the tracks worth playing are described -- the guitars, or everything if
a file has no guitar at all, which is the same rule the track picker uses.
A drum track's "tuning" is not a tuning and saying so would be noise.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from pickhero import config as config_module
from pickhero.audio.note_utils import tuning_notes


def index_file() -> Path:
    """Where the index lives, read through the module rather than bound at
    import: the test suite redirects the config directory, and an index that
    ignored that would write into the player's real one."""
    return config_module.CONFIG_DIR / "song_index.json"

# Saved this often while scanning, so a folder read halfway is not lost.
SAVE_EVERY = 25


@dataclass
class SongInfo:
    """The playable tracks of one file, in the order the file lists them."""

    tracks: int = 0
    tunings: list[str] = field(default_factory=list)   # "E A D G B E" each
    names: list[str] = field(default_factory=list)     # track names

    @property
    def distinct_tunings(self) -> list[str]:
        """Each tuning once, in the order it first appears.

        Six guitar tracks in standard tuning are one answer, not six, and a
        row that repeats it six times says less than one that says it once.
        """
        out: list[str] = []
        for tuning in self.tunings:
            if tuning and tuning not in out:
                out.append(tuning)
        return out

    def summary(self) -> str:
        """The one line the song list has room for."""
        if not self.tracks:
            return ""
        word = "track" if self.tracks == 1 else "tracks"
        tunings = self.distinct_tunings
        if not tunings:
            return f"{self.tracks} {word}"
        shown = ", ".join(tunings[:2])
        if len(tunings) > 2:
            shown += f", +{len(tunings) - 2}"
        return f"{self.tracks} {word} · {shown}"


def describe_tuning(tuning: dict[int, int] | None) -> str:
    """"E A D G B E", low string first, as a player says it.

    The common names ("Drop D") are what the app shows elsewhere, but the
    letters are what the request asked for and what an unnamed tuning has to
    fall back on anyway -- so the letters are the answer here and the name is
    left to the tuning HUD.
    """
    notes = tuning_notes(tuning)
    return " ".join(notes) if notes else ""


def describe_file(path: Path) -> SongInfo:
    """Open one file and say what is playable in it."""
    from pickhero.tabs.loader import list_tracks
    try:
        tracks = list_tracks(path)
    except Exception:
        return SongInfo()
    playable = [t for t in tracks
                if t.get("is_guitar") and not t.get("is_percussion")]
    if not playable:
        playable = [t for t in tracks if not t.get("is_percussion")] or tracks
    return SongInfo(
        tracks=len(playable),
        tunings=[describe_tuning(t.get("tuning")) for t in playable],
        names=[t.get("name", "") for t in playable],
    )


def file_stamp(path: Path) -> str:
    """Size and modification time: what says a file is still the same one."""
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{stat.st_size}:{stat.st_mtime_ns}"


class SongIndex:
    """The index, read from disk and filled in on a thread."""

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path else index_file()
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.scanned = 0
        self.to_scan = 0
        self._load()

    # -- reading -----------------------------------------------------------

    def get(self, file: Path) -> SongInfo | None:
        """What is in this file, or None while it has not been read yet."""
        entry = self._entries.get(str(file))
        if not entry or entry.get("stamp") != file_stamp(file):
            return None
        return SongInfo(tracks=entry.get("tracks", 0),
                        tunings=list(entry.get("tunings", [])),
                        names=list(entry.get("names", [])))

    def tunings_present(self, files: list[Path]) -> list[str]:
        """Every tuning that appears in these songs, commonest first.

        Ordered by how many songs use it rather than alphabetically: the
        filter exists to reach the ones actually played, and standard tuning
        should not sit between two things nobody has.
        """
        counts: dict[str, int] = {}
        for file in files:
            info = self.get(file)
            if info is None:
                continue
            for tuning in info.distinct_tunings:
                counts[tuning] = counts.get(tuning, 0) + 1
        return sorted(counts, key=lambda k: (-counts[k], k))

    def has(self, file: Path, tuning: str) -> bool:
        """Whether any playable track of this song uses that tuning."""
        info = self.get(file)
        return bool(info and tuning in info.distinct_tunings)

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- filling in --------------------------------------------------------

    def scan(self, files: list[Path]) -> None:
        """Read whatever is missing, here and now. Used by the tests."""
        missing = [f for f in files if self.get(f) is None]
        self.to_scan = len(missing)
        self.scanned = 0
        for i, file in enumerate(missing, 1):
            if self._stop.is_set():
                return
            self._record(file, describe_file(file))
            self.scanned = i
            if i % SAVE_EVERY == 0:
                self.save()
        if missing:
            self.save()

    def scan_in_background(self, files: list[Path]) -> None:
        """Same, on a thread, so the list is on screen while it happens."""
        if self.busy:
            return
        # Newest first: a file just copied in is the one being looked for.
        ordered = sorted(files, key=lambda f: -_mtime(f))
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.scan, args=(ordered,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _record(self, file: Path, info: SongInfo) -> None:
        with self._lock:
            self._entries[str(file)] = {
                "stamp": file_stamp(file),
                "tracks": info.tracks,
                "tunings": info.tunings,
                "names": info.names,
            }

    # -- disk --------------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            self._entries = {k: v for k, v in data.items() if isinstance(v, dict)}

    def save(self) -> None:
        """Write the index. A failure here costs a rescan, never a crash."""
        with self._lock:
            snapshot = dict(self._entries)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
