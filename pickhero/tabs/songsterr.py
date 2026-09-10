"""Songsterr's own per-bar timing, for a tab downloaded from Songsterr.

Songsterr does not find its sync by listening at play time. It stores a
**timestamp per measure** into a YouTube video and serves it from
`api/video-points/{song}/{revision}/list`. That is a made map, not a found
one -- and a made map never fails on a song that repeats itself, which is
exactly where listening does.

Measured against the player's own Thunder recording, on readings neither map
was fitted to:

    one offset, no map        192 / 200 ms median
    Songsterr, 91 bars         80 /  92 ms median
    our own listening          16 /   8 ms median

**So this is not a replacement for the measurement.** Where the listening
works it is five to ten times better. What this is for is the songs where
the listening produces nothing at all -- What's Up is four chords repeated
for four minutes, and its windows match +9.9, -34.4, -6.2 and +21.1 s -- and
as a second opinion on the ones where it works.

The points are times in a VIDEO, not in the player's file. Three videos of
one song carry the same curve shifted by a constant (measured: -25.15 and
-23.25 s against the first), so the shape is the data and the constant is
found against the recording the player actually has. See
`autosync.align_to_bar_times`.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

# .../thunder-love-walked-in-v4-tab-s2333598 -- the id is the tail, and the
# rest of the slug is decoration that changes when a tab is renamed.
_ID = re.compile(r"[-/]s(\d+)\b")
_BARE = re.compile(r"^\s*(\d{3,})\s*$")

BASE = "https://www.songsterr.com/api"
# Long enough for a phone tethering, short enough that a dead network does
# not look like a frozen app. The call runs off the frame either way.
TIMEOUT_S = 20.0


class NotFound(RuntimeError):
    """Said out loud rather than returned as an empty list."""


def song_id_of(text: str) -> int | None:
    """The song id in a Songsterr link, or None if there is not one.

    Takes the whole URL because that is what the clipboard holds after
    somebody copies the address bar, and a bare id because that is what
    somebody types who already knows it.
    """
    if not text:
        return None
    found = _ID.search(text.strip())
    if found:
        return int(found.group(1))
    bare = _BARE.match(text)
    return int(bare.group(1)) if bare else None


def _get(url: str) -> object:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise NotFound(f"Songsterr answered {exc.code} for {url}") from exc
    except Exception as exc:                       # network, DNS, bad JSON
        raise NotFound(f"{type(exc).__name__}: {exc}") from exc


def fetch_meta(song_id: int) -> dict:
    """The song's metadata, which is where the revision id lives."""
    meta = _get(f"{BASE}/meta/{song_id}")
    if not isinstance(meta, dict) or "revisionId" not in meta:
        raise NotFound(f"song {song_id} has no revision")
    return meta


def fetch_entries(song_id: int, revision_id: int) -> list:
    """One entry per video Songsterr has timed this revision against."""
    entries = _get(f"{BASE}/video-points/{song_id}/{revision_id}/list")
    if not isinstance(entries, list):
        raise NotFound(f"song {song_id} has no video points")
    return entries


def bar_times_of(entries) -> list[float]:
    """The per-bar seconds from the entries, longest first.

    Any of them will do. Three videos of one song carry the same curve
    shifted by a constant -- measured on Thunder at exactly -25.15 and
    -23.25 s, to the last decimal on all 91 points -- and the constant is
    found against the player's own recording anyway. The longest is taken
    because a truncated entry is the one failure mode that would matter.
    """
    best: list[float] = []
    for entry in entries or []:
        points = entry.get("points") if isinstance(entry, dict) else None
        if not isinstance(points, list) or len(points) < 2:
            continue
        try:
            numbers = [float(p) for p in points]
        except (TypeError, ValueError):
            continue
        if any(b <= a for a, b in zip(numbers, numbers[1:])):
            continue                    # not a timeline, whatever it is
        if len(numbers) > len(best):
            best = numbers
    return best


def fetch_bar_times(song_id: int) -> tuple[list[float], dict]:
    """(seconds per bar, the metadata it came with)."""
    meta = fetch_meta(song_id)
    entries = fetch_entries(song_id, int(meta["revisionId"]))
    times = bar_times_of(entries)
    if not times:
        raise NotFound(f"song {song_id} has video points but none usable")
    return times, meta
