"""Songsterr tab search and download.

Provides search and download functionality for Guitar Pro tabs from Songsterr.
Falls back to opening the browser when direct download fails.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

SONGSTERR_API_URL = "https://www.songsterr.com/api/songs"
SONGSTERR_META_URL = "https://www.songsterr.com/api/meta"
SONGSTERR_REVISION_URL = "https://www.songsterr.com/api/revision"
SONGSTERR_TAB_URL = "https://www.songsterr.com/a/wsa"

REQUEST_TIMEOUT = 15

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class SongsterrResult:
    """A search result from Songsterr."""

    song_id: int
    title: str
    artist: str


def _urlopen(url: str, timeout: int = REQUEST_TIMEOUT) -> bytes:
    """Fetch a URL with browser-like headers. Returns response body bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_json(url: str) -> dict | list | None:
    """Fetch a URL and parse as JSON. Returns None on any error."""
    try:
        data = _urlopen(url)
    except (urllib.error.URLError, OSError):
        return None
    try:
        return json.loads(data)
    except (json.JSONDecodeError, ValueError):
        return None


def search(query: str, max_results: int = 10) -> list[SongsterrResult]:
    """Search Songsterr for tabs matching a query.

    Args:
        query: Search string (song name, artist, etc.).
        max_results: Maximum results to return.

    Returns:
        List of SongsterrResult, empty on error.
    """
    params = urllib.parse.urlencode({"pattern": query})
    url = f"{SONGSTERR_API_URL}?{params}"
    items = _fetch_json(url)
    if not isinstance(items, list):
        return []

    results = []
    for item in items[:max_results]:
        results.append(
            SongsterrResult(
                song_id=item.get("songId", 0),
                title=item.get("title", ""),
                artist=item.get("artist", ""),
            )
        )
    return results


def _get_source_url(song_id: int) -> str | None:
    """Get the GP file download URL via Songsterr's API.

    Two API calls:
        1. /api/meta/{songId} → revisionId
        2. /api/revision/{revisionId} → source URL

    Returns None if the source URL can't be resolved.
    """
    meta = _fetch_json(f"{SONGSTERR_META_URL}/{song_id}")
    if not isinstance(meta, dict):
        return None

    revision_id = meta.get("revisionId")
    if not revision_id:
        return None

    revision = _fetch_json(f"{SONGSTERR_REVISION_URL}/{revision_id}")
    if not isinstance(revision, dict):
        return None

    source = revision.get("source")
    if isinstance(source, str) and source.startswith("http"):
        return source
    return None


def get_songsterr_url(song_id: int) -> str:
    """Return the Songsterr browser URL for a given song ID."""
    return f"{SONGSTERR_TAB_URL}/{song_id}"


def download_gp5(song_id: int, output_path: str | Path) -> bool:
    """Download a GP tab file from Songsterr.

    Args:
        song_id: Songsterr song ID.
        output_path: Where to save the downloaded file.

    Returns:
        True if download succeeded, False otherwise.
    """
    source_url = _get_source_url(song_id)
    if not source_url:
        return False

    try:
        file_data = _urlopen(source_url)
    except (urllib.error.URLError, OSError):
        return False

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(file_data)
    return True


def sanitize_filename(name: str) -> str:
    """Remove characters that are invalid in filenames."""
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


# ── One song, everything it needs ───────────────────────────────────────────
#
# Downloading a tab used to be the first of four jobs. The other three were
# the player's: find the recording, paste the Songsterr link back into the
# app with Ctrl+U, then press Ctrl+S and hope the listening reads a song that
# repeats itself. Every one of them is answered by the same reply the tab
# came in, so all four happen here now, on one ENTER.


@dataclass
class Grab:
    """What came down for one song, and what did not."""

    song_id: int
    tab_path: Path | None = None
    video_id: str = ""
    bars: int = 0
    audio_path: Path | None = None
    #: In the player's words, one line per thing that happened. Shown on the
    #: download screen, because a step that fails silently is a step the
    #: player will spend an evening looking for.
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """The tab arrived. The rest is a bonus, not a requirement.

        A song with no video on Songsterr, or a machine with no ffmpeg, is
        still a song to practise -- it just syncs the way it did before.
        """
        return self.tab_path is not None


def grab_song(song_id: int, output_path: str | Path,
              want_audio: bool = True, on_progress=None) -> Grab:
    """Fetch a tab and everything Songsterr knows about its timing.

    Args:
        song_id: Songsterr song id.
        output_path: where the tab file should be written.
        want_audio: also pull the video's audio with yt-dlp.
        on_progress: called with (fraction 0..1, what it is doing).

    Returns:
        A `Grab`. Check `.ok` for the tab; `.notes` says what else happened.

    Never raises for a missing piece. The tab is the thing being asked for
    and the rest is best-effort, so a dead video or an absent ffmpeg reports
    itself in `notes` rather than taking the download down with it.
    """
    from pickhero.tabs import songsterr, youtube

    def step(fraction: float, what: str) -> None:
        if on_progress is not None:
            on_progress(fraction, what)

    grab = Grab(song_id=int(song_id))
    out = Path(output_path)

    step(0.05, "downloading the tab")
    if not download_gp5(song_id, out):
        grab.notes.append("The tab could not be downloaded.")
        return grab
    grab.tab_path = out

    # The bar map, cached BESIDE THE TAB. Asked for here rather than the
    # first time the player presses Ctrl+S, so the song works on a machine
    # with no network -- which is what "I use it locally, offline" means.
    step(0.35, "asking Songsterr for its bar map")
    try:
        meta = songsterr.fetch_meta(song_id)
        entries = songsterr.fetch_entries(song_id, int(meta["revisionId"]))
        songsterr.save_cache(out, song_id, meta, entries)
    except songsterr.NotFound as exc:
        grab.notes.append(f"No bar map: {exc}")
        return grab
    except OSError as exc:
        grab.notes.append(f"The bar map could not be saved: {exc}")
        return grab

    chosen = songsterr.main_entry(entries)
    if chosen is None:
        grab.notes.append("Songsterr has no timing for this tab.")
        return grab
    grab.video_id = songsterr.video_id_of(chosen)
    grab.bars = len(songsterr.bar_times_of_entry(chosen))
    grab.notes.append(f"Bar map: {grab.bars} bars.")

    if not want_audio:
        return grab
    if not grab.video_id:
        grab.notes.append("Its bar map names no video, so no audio.")
        return grab

    step(0.5, "downloading the audio")
    try:
        grab.audio_path = youtube.fetch_audio(
            grab.video_id,
            youtube.audio_path_for(out),
            lambda f, what: step(0.5 + 0.5 * f, what),
        )
        grab.notes.append(f"Audio: {grab.audio_path.name}")
    except youtube.NotAvailable as exc:
        # Named, not swallowed. "ffmpeg was not found" and "this video is
        # private" send the player to completely different places.
        grab.notes.append(f"No audio: {exc}")
    return grab
