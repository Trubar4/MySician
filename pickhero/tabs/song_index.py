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

Only GUITAR tracks are described. A drum track's "tuning" is not a tuning,
a bass has four strings, and a piano has none -- counting any of them as an
instrument to play makes the number answer a different question than the one
being asked. A file that turns out to hold no guitar at all says so in as
many words: "no guitar track" is an answer, where a blank row means the file
has not been read yet, and those two must never look the same.

(The track picker inside a song still falls back to offering everything when
a file has no guitar, so such a file can be opened. This is the song LIST
saying what is in it, which is a different question.)
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

# Bumped when what an entry MEANS changes, so old ones are re-read rather
# than believed. It went to 2 when only guitar tracks began to be counted:
# an older entry's number answers a different question. And to 3 when the
# open strings' PITCH was stored beside their letters -- an older entry
# cannot be ordered by pitch at all, and reconstructing it from the letters
# is a guess (they carry no octave), so those files are read again.
ENTRY_VERSION = 3


@dataclass
class SongInfo:
    """The guitar tracks of one file, in the order the file lists them."""

    tracks: int = 0
    tunings: list[str] = field(default_factory=list)   # "E A D G B E" each
    names: list[str] = field(default_factory=list)     # track names
    # The mean open-string MIDI of each guitar track, parallel to `tunings`.
    # The letters have no octave, so this is the only thing that can order
    # the tuning filter by pitch -- see `tuning_pitch`.
    pitch: list[float] = field(default_factory=list)
    # Whether the file could be opened at all. Without this, a file that
    # failed to parse and a file holding no guitar are the same empty record,
    # and the row would blame the wrong one.
    readable: bool = True

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
        if not self.readable:
            return "could not be read"
        if not self.tracks:
            # Read, and there is no guitar in it. Distinct from a blank row,
            # which means it has not been read yet.
            return "no guitar track"
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


def mean_pitch(tuning: dict[int, int] | None) -> float:
    """The mean of the open strings, in MIDI, or 0.0 for no tuning.

    What "by pitch" means for a whole tuning, and the only measure of the two
    obvious ones that matches what was asked for. Over the sixteen named
    tunings, ordering by the mean gives Standard, Drop D, ...; ordering by the
    LOWEST string puts Eb Standard between them, because Eb is a semitone
    under E while Drop D's other five strings have not moved at all.
    """
    if not tuning:
        return 0.0
    values = [tuning[s] for s in sorted(tuning)]
    return sum(values) / len(values)


def describe_file(path: Path) -> SongInfo:
    """Open one file and say what is playable in it."""
    from pickhero.tabs.loader import list_tracks
    try:
        tracks = list_tracks(path)
    except Exception:
        return SongInfo(readable=False)
    guitars = [t for t in tracks
               if t.get("is_guitar") and not t.get("is_percussion")]
    return SongInfo(
        tracks=len(guitars),
        tunings=[describe_tuning(t.get("tuning")) for t in guitars],
        names=[t.get("name", "") for t in guitars],
        pitch=[mean_pitch(t.get("tuning")) for t in guitars],
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
        if entry.get("v") != ENTRY_VERSION:
            # Written before guitar tracks became the only ones counted, so
            # its number answers a question nobody asked any more.
            return None
        return SongInfo(tracks=entry.get("tracks", 0),
                        tunings=list(entry.get("tunings", [])),
                        names=list(entry.get("names", [])),
                        pitch=[float(v) for v in entry.get("pitch", [])],
                        readable=bool(entry.get("readable", True)))

    def tuning_counts(self, files: list[Path]) -> dict[str, int]:
        """How many of these songs have a track in each tuning.

        A song is counted once per DISTINCT tuning it holds, which is what
        the filter matches on -- six guitar tracks in standard tuning are one
        answer, not six.
        """
        counts: dict[str, int] = {}
        for file in files:
            info = self.get(file)
            if info is None:
                continue
            for tuning in info.distinct_tunings:
                counts[tuning] = counts.get(tuning, 0) + 1
        return counts

    def tuning_pitch(self, files: list[Path]) -> dict[str, float]:
        """The mean open-string pitch of each tuning present, in MIDI.

        Read off the index rather than off the letters, which carry no
        octave: "E A D G B E" could be a standard guitar or a Nashville
        high-strung one, and reconstructing the octaves would be a guess.
        The first reading of a tuning wins, so a letter string the folder
        holds at two octaves still gets one answer -- the filter keys on the
        letters and already treats those as one tuning.
        """
        out: dict[str, float] = {}
        for file in files:
            info = self.get(file)
            if info is None:
                continue
            for tuning, pitch in zip(info.tunings, info.pitch):
                if tuning and tuning not in out:
                    out[tuning] = float(pitch)
        return out

    def tunings_present(self, files: list[Path]) -> list[str]:
        """Every tuning that appears in these songs, HIGHEST pitch first.

        "Kannst du die Tuning Filter in der Uebersicht nach Tonhoehe
        sortieren? Start mit e Standard, dann drop d, usw." -- which
        overturns the commonest-first rule this used to follow, and the
        reason that rule gave ("standard tuning should not sit between two
        things nobody has") survives anyway on his own folder: nothing in it
        is tuned above standard, so standard is still first. On a folder of
        metal it will not be, and the count on each chip is what says so.

        A tuning with no pitch stored goes last rather than first: an entry
        written before `ENTRY_VERSION` 3 has none, and a strip that opened
        with those would read as a sort that is not working. The letters
        break a tie, so the order is the same on every machine.
        """
        pitch = self.tuning_pitch(files)
        return sorted(self.tuning_counts(files),
                      key=lambda k: (-pitch.get(k, -1.0), k))

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
                "v": ENTRY_VERSION,
                "stamp": file_stamp(file),
                "tracks": info.tracks,
                "tunings": info.tunings,
                "names": info.names,
                "pitch": info.pitch,
                "readable": info.readable,
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
