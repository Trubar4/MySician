"""Everything about a song, in a file beside the song.

*"Wie bekomme ich alle Songs von NB1 auf NB2 mit Syncs etc.? Ich könnte
einen iCloud Link nutzen."*

Copying the songs folder used to carry the tab, its bar map and its
recording -- and leave the expensive part behind. The practice speed, the
sync points, the Songsterr id, the transpose and the star all lived in one
`settings.json` in the home folder, keyed by the tab's stem. So the second
laptop got the songs and none of the work, and the only way across was a
merge tool run by hand.

**Two machines writing one settings file is the problem, not the copying.**
A cloud folder syncs files; it cannot merge two edits of the same JSON, and
the app saves that file on almost every keypress. Last writer wins, and what
loses is silent.

So what belongs to a SONG lives with the song, in `<name>.mysician.json`,
and `settings.json` keeps only what belongs to this MACHINE -- the audio
device, the calibration, the input latency. Put the songs folder in iCloud
and two laptops editing different songs never touch the same file.

Three rules make it safe:

- **What this machine already has WINS.** Only settings with no local entry
  are taken. That is the rule `merge_stats.py` has always used, and one rule
  in the project beats two.
- **The recording travels as a NAME, not a path.** `C:\\Users\\Admin\\...`
  means nothing on the other laptop, and `mp3_path_for` already falls back
  to the same name in the songs folder.
- **Never a raise.** A sidecar that cannot be read or written is a slower
  day, not a broken song.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

SUFFIX = ".mysician.json"

#: Bumped only if the shape changes in a way an older build would misread.
#: An unknown version is ignored rather than guessed at.
VERSION = 1


def path_for(tab_path) -> Path:
    """Where this tab's travelling settings live."""
    tab = Path(tab_path)
    return tab.with_name(tab.stem + SUFFIX)


def song_fields(config) -> tuple[str, ...]:
    """Every per-song dict on the config. Found, not listed.

    The fourth reader of this set -- `forget_song`, `rename_song`,
    `merge_stats` and this -- and the only reason four readers are safe is
    that none of them writes the names down.
    """
    return tuple(f.name for f in fields(type(config))
                 if f.name.startswith("song_")
                 and isinstance(getattr(config, f.name, None), dict))


def collect(song_key: str, config, best=None) -> dict:
    """What this song is worth carrying, as plain JSON.

    `best` is the practice record (`progress.SongRecord`) or None. It is
    per song by nature -- *"Bestwerte pro Song mit ins Sidecar"* -- while
    the chronological sitting log is not, and stays where it is.
    """
    out: dict = {"version": VERSION, "song": song_key, "settings": {}}
    for name in song_fields(config):
        held = getattr(config, name, None)
        if isinstance(held, dict) and song_key in held:
            value = held[song_key]
            if name == "song_mp3_paths":
                # The NAME only. An absolute path is this machine's answer
                # to "where did I put it", and it is wrong on the other one
                # -- while the file itself is right there beside the tab.
                value = str(value).replace("\\", "/").rsplit("/", 1)[-1]
            out["settings"][name] = value
    out["favourite"] = song_key in (config.favourites or [])
    if best is not None:
        from dataclasses import asdict, is_dataclass
        out["best"] = asdict(best) if is_dataclass(best) else dict(best)
    return out


def write(tab_path, song_key: str, config, best=None) -> Path | None:
    """Write the song's settings beside the tab. None if it could not be.

    Written on the way OUT of a song and after anything expensive -- a
    measurement, a rename, a download -- rather than on every keypress. The
    file is small, but a cloud folder that sees it change forty times a
    minute is a cloud folder fighting itself.
    """
    if not song_key or not tab_path:
        return None
    out = path_for(tab_path)
    try:
        out.write_text(json.dumps(collect(song_key, config, best), indent=1),
                       encoding="utf-8")
    except OSError:
        return None
    return out


def read(tab_path) -> dict | None:
    """The sidecar beside this tab, or None if there is not a usable one."""
    try:
        raw = json.loads(path_for(tab_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        return None
    return raw if isinstance(raw.get("settings"), dict) else None


def adopt(tab_path, song_key: str, config) -> list[str]:
    """Take what this machine does not have. Returns what it took.

    **Only what is missing.** An entry set here was set on this instrument,
    in this room, and a sync that silently overwrites what you have just
    adjusted is worse than no sync. A song this laptop has never seen gets
    everything; one it has an opinion about keeps it.
    """
    raw = read(tab_path)
    if raw is None or not song_key:
        return []
    taken: list[str] = []
    known = set(song_fields(config))
    for name, value in raw["settings"].items():
        if name not in known:
            continue                  # written by a build that knew more
        held = getattr(config, name)
        if song_key in held:
            continue                  # ours wins
        if name == "song_mp3_paths":
            # A name, resolved HERE. It only counts if the file is really
            # beside the tab -- otherwise this would store a path to
            # nothing, which the app reports as a moved recording.
            beside = Path(tab_path).with_name(str(value))
            if not beside.is_file():
                continue
            value = str(beside)
        held[song_key] = value
        taken.append(name)
    if raw.get("favourite") and song_key not in (config.favourites or []):
        config.favourites.append(song_key)
        taken.append("favourite")
    return taken


def adopt_best(tab_path, song_key: str, tracker) -> bool:
    """Take the practice record too, if this machine has none for the song.

    Never merges two records: a best score is one number with its own
    history behind it, and half of each is neither. `merge_stats.py` is
    still the tool for reconciling two machines that both played the song.
    """
    raw = read(tab_path)
    if raw is None or not isinstance(raw.get("best"), dict):
        return False
    if tracker.get_best(song_key) is not None:
        return False
    try:
        from pickhero.progress import SongRecord
        known = {f.name for f in fields(SongRecord)}
        tracker._data[song_key] = SongRecord(
            **{k: v for k, v in raw["best"].items() if k in known})
        tracker._save()
    except Exception:
        return False
    return True
