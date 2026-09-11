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
from pathlib import Path

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


def all_bar_times(entries) -> list[list[float]]:
    """Every usable per-bar timeline in the reply, longest first.

    **All of them, because they are not the same song.** Thunder's three
    videos carry one curve shifted by a constant -- exactly -25.15 and
    -23.25 s on all 91 points -- and for that song any entry would do. What's
    Up does not behave like that at all: its main video has **72** points
    over 253 s and its backing videos have **78** over 278 s. They are
    different cuts of the piece, and only one of them is the recording the
    player has.

    So the caller tries them and keeps the one that fits (see
    `autosync.align_to_bar_times`). Taking the longest, which the first build
    did, would have handed What's Up the 78-point backing track and called
    the answer measured.
    """
    out: list[list[float]] = []
    for entry in entries or []:
        numbers = bar_times_of_entry(entry)
        if numbers and numbers not in out:
            out.append(numbers)
    return sorted(out, key=len, reverse=True)


def bar_times_of(entries) -> list[float]:
    """The longest usable timeline, for callers that want just one."""
    found = all_bar_times(entries)
    return found[0] if found else []


def fetch_bar_times(song_id: int) -> tuple[list[list[float]], dict]:
    """(every usable per-bar timeline, the metadata it came with).

    Every one, not the best one: which of them is the player's recording is
    a question only the recording can answer.
    """
    meta = fetch_meta(song_id)
    entries = fetch_entries(song_id, int(meta["revisionId"]))
    times = all_bar_times(entries)
    if not times:
        raise NotFound(f"song {song_id} has video points but none usable")
    return times, meta


# ── Which video, and keeping it ─────────────────────────────────────────────

def main_entry(entries) -> dict | None:
    """The entry for the recording the tab was written from, or None.

    Songsterr marks that one with `feature: null`; everything else is a
    backing track, a live cut, a solo-only upload or somebody's cover, and
    those are DIFFERENT RECORDINGS of the song rather than the same one
    shifted. What's Up is the proof: its main video carries 72 points over
    253 s and its backing videos carry 78 over 278 s. Taking the longest --
    which the first build did -- handed that song the backing track and
    called the answer measured.

    So when the player downloads the audio, this is the video to take: the
    points were made against it, which leaves the fit with almost nothing
    to find.
    """
    usable = [e for e in entries or []
              if isinstance(e, dict) and bar_times_of_entry(e)]
    if not usable:
        return None
    for entry in usable:
        if not entry.get("feature"):
            return entry
    return usable[0]


def video_id_of(entry) -> str:
    """The YouTube id in an entry, or "" when there is not one."""
    if not isinstance(entry, dict):
        return ""
    found = entry.get("videoId")
    return found.strip() if isinstance(found, str) else ""


def bar_times_of_entry(entry) -> list[float]:
    """One entry's per-bar timeline, or [] if it is not one.

    Same rules as `all_bar_times` applies to the whole reply -- at least two
    points, all numbers, strictly rising -- said once so both callers agree
    on what counts as a timeline.
    """
    if not isinstance(entry, dict):
        return []
    points = entry.get("points")
    if not isinstance(points, list) or len(points) < 2:
        return []
    try:
        numbers = [float(p) for p in points]
    except (TypeError, ValueError):
        return []
    if any(b <= a for a, b in zip(numbers, numbers[1:])):
        return []
    return numbers


def candidates_for(entries, video_id: str) -> list[list[float]]:
    """Every timeline, with the named video's first.

    First rather than only: the recording on disk is not always the video it
    was pulled from -- the player may have had an MP3 already, or replaced
    the one that came down -- and the fit is what decides. Putting the known
    video in front means the usual case is settled by the first try instead
    of by luck.
    """
    every = all_bar_times(entries)
    if not video_id:
        return every
    for entry in entries or []:
        if video_id_of(entry) != video_id:
            continue
        mine = bar_times_of_entry(entry)
        if mine:
            return [mine] + [t for t in every if t != mine]
    return every


#: Beside the tab, not in a cache folder keyed by id. The tab and its bar map
#: are one song: copy the songs folder to the second laptop and the map goes
#: with it, which is the same rule `mp3_path_for` already follows for the
#: recording. A cache the player cannot see is a cache the player cannot
#: carry.
CACHE_SUFFIX = ".songsterr.json"


def cache_path(tab_path) -> Path:
    """Where this tab's downloaded bar map lives."""
    tab = Path(tab_path)
    return tab.with_name(tab.stem + CACHE_SUFFIX)


def save_cache(tab_path, song_id: int, meta: dict, entries: list) -> Path:
    """Write the reply beside the tab. Returns the file it wrote.

    The WHOLE reply, every video, not just the one chosen: which entry fits
    is a question the recording answers, and a cache that has already
    answered it cannot be re-asked when the player swaps the MP3.
    """
    out = cache_path(tab_path)
    out.write_text(json.dumps({
        "songId": int(song_id),
        "revisionId": meta.get("revisionId"),
        "title": meta.get("title") or "",
        "artist": meta.get("artist") or "",
        "entries": entries,
    }, ensure_ascii=False), encoding="utf-8")
    return out


def load_cache(tab_path) -> dict | None:
    """The stored reply for this tab, or None if there is not a usable one.

    None rather than a raise: a missing or half-written cache means ask the
    network, which is what the app did before this existed.
    """
    try:
        raw = json.loads(cache_path(tab_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        return None
    return raw if all_bar_times(raw["entries"]) else None


def preferred_bar_times(entries) -> list[list[float]]:
    """Every timeline, with the main video's first.

    The order the fit should try them in when nobody has said which video
    the recording is. `main_entry` names the one the tab was written from,
    and after an in-app download that is also the one the audio was pulled
    from -- so the first try is usually the last one.
    """
    return candidates_for(entries, video_id_of(main_entry(entries)))


def bar_times_from_cache(tab_path) -> tuple[list[list[float]], dict] | None:
    """(timelines, metadata) stored beside this tab, or None.

    The same shape `fetch_bar_times` returns, so the caller asks the disk and
    the network the same way and the offline case is not a second code path.
    """
    raw = load_cache(tab_path)
    if raw is None:
        return None
    return preferred_bar_times(raw["entries"]), raw
