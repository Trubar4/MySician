#!/usr/bin/env python3
"""Print EVERY field Songsterr answers with, for one song.

    python tools/songsterr_fields.py 2333598
    python tools/songsterr_fields.py https://www.songsterr.com/a/wsa/...-s2333598

*"Kannst du mir beim Herunterladen noch mehr Details geben? Stimmung, Autor,
Datum"* -- and two of those three cannot be found in anything this project
holds. Measured rather than assumed:

- **The tabs carry no author.** Over the player's eight files, the GPIF
  `<Score>` block has `Words`, `Music`, `WordsAndMusic` and `Copyright`
  EMPTY in all eight, and `Tabber` says "Songsterr Downloader" -- the tool,
  not a person. Same story as the fingering (0 of 6193 notes) and the chord
  names (0 of 5601 beats): the field exists and transcribers leave it blank.
- **Nothing carries a date.** Not the Guitar Pro format, and not the two
  cached Songsterr replies on disk, which keep `songId, revisionId, title,
  artist, entries` and whose entries hold `videoId, feature, status,
  points`. The one date in the whole path is the `Last-Modified` the file
  server sends, which `download_tab` now reads.

What this cannot settle from here is whether the LIVE reply carries more
than the four fields `save_cache` keeps -- songsterr.com is not reachable
from the machine this was written on. So this prints the lot, on a machine
that can reach it, and the answer decides what the download screen says.
Nothing is written and nothing is guessed at: every key of every reply, with
its value, and the walk back through the revisions the download itself does.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.tabs import downloader, songsterr                 # noqa: E402

#: Worth calling out by name if it ever turns up, because it is what was
#: asked for. Matched case-insensitively against every key of every reply.
WANTED = ("author", "user", "tabber", "by", "credit", "uploader", "owner",
          "date", "created", "updated", "modified", "time", "version",
          "tuning", "difficulty", "capo")


def _show(what: str, value: object, indent: str = "  ") -> None:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > 160:
        text = text[:157] + "..."
    mark = " <-- asked for" if any(w in what.lower() for w in WANTED) else ""
    print(f"{indent}{what} = {text}{mark}")


def _reply(url: str) -> object | None:
    print(f"\n== {url}")
    data = downloader._fetch_json(url)
    if data is None:
        print("  (no answer -- network, or Songsterr said no)")
        return None
    if isinstance(data, dict):
        for key in sorted(data):
            _show(key, data[key])
    elif isinstance(data, list):
        print(f"  a list of {len(data)}")
        if data and isinstance(data[0], dict):
            for key in sorted(data[0]):
                _show(f"[0].{key}", data[0][key])
    return data


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    song_id = songsterr.song_id_of(argv[1])
    if not song_id:
        print(f"Not a Songsterr id or link: {argv[1]!r}")
        return 2

    print(f"Songsterr song {song_id}")
    meta = _reply(f"{downloader.SONGSTERR_META_URL}/{song_id}")
    if isinstance(meta, dict):
        tracks = meta.get("tracks")
        if isinstance(tracks, list) and tracks and isinstance(tracks[0], dict):
            print(f"\n== meta tracks ({len(tracks)})")
            for i, track in enumerate(tracks):
                print(f"  track {i}:")
                for key in sorted(track):
                    _show(f"{key}", track[key], indent="    ")

    # The same walk `download_tab` does: the newest revision often has no
    # Guitar Pro file behind it and an older one does, so each one's fields
    # are worth seeing rather than only the first.
    revision = meta.get("revisionId") if isinstance(meta, dict) else None
    seen: list[int] = []
    while revision and revision not in seen and len(seen) < downloader.SOURCE_HOPS:
        seen.append(revision)
        got = _reply(f"{downloader.SONGSTERR_REVISION_URL}/{revision}")
        if not isinstance(got, dict):
            break
        revision = got.get("prevRevisionId")

    if isinstance(meta, dict) and meta.get("revisionId"):
        _reply(f"{songsterr.BASE}/video-points/{song_id}/"
               f"{meta['revisionId']}/list")

    print("\nPaste this whole output back. If an author or a date is in "
          "there, it goes on the download screen; if it is not, that is the "
          "answer and the screen stops promising one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
