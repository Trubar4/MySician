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

from pickhero.tabs.loader import GP_EXTENSIONS

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
    #: Looked up by id rather than found by searching. The player pasted a
    #: link, or typed a number -- he has already picked his version on
    #: Songsterr's own site, and this is that exact one rather than whatever
    #: the search thinks he meant.
    by_id: bool = False


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


def _source_of(song_id: int) -> tuple[str, str]:
    """(the GP file's URL, why there is not one). Exactly one is filled in.

    Two API calls:
        1. /api/meta/{songId} → revisionId
        2. /api/revision/{revisionId} → source URL

    **The reason is returned, not swallowed.** "Songsterr has no file for
    this tab" and "the network is down" are the same empty result and
    completely different problems, and the first build reported both by
    opening a browser at a page that did not load. Not every tab on
    Songsterr has a source file behind it -- some exist only in their own
    format -- and a player who is told that stops trying.
    """
    meta = _fetch_json(f"{SONGSTERR_META_URL}/{song_id}")
    if not isinstance(meta, dict):
        return "", "Songsterr did not answer for this song"

    revision_id = meta.get("revisionId")
    if not revision_id:
        return "", "Songsterr has no revision for this tab"

    revision = _fetch_json(f"{SONGSTERR_REVISION_URL}/{revision_id}")
    if not isinstance(revision, dict):
        return "", f"Songsterr did not answer for revision {revision_id}"

    source = revision.get("source")
    if isinstance(source, str) and source.startswith("http"):
        return source, ""
    # **The keys are named.** Reaching here means the revision was fetched
    # and parsed and simply has no usable `source` -- which is a different
    # thing from the network being down, and the player hit it on four songs
    # in a row while a third-party downloader fetched all four. Whether the
    # field moved, was renamed, or is genuinely absent for these tabs is a
    # question the reply itself answers, and a message that quotes the reply
    # turns his next screenshot into that answer instead of another round.
    keys = ", ".join(sorted(revision)[:8]) or "nothing"
    return "", (f"Songsterr holds no Guitar Pro file for this tab "
                f"(revision {revision_id} has: {keys})")


def lookup(song_id: int) -> SongsterrResult | None:
    """One song by its id, or None if Songsterr does not have it.

    What makes a pasted link work: the player has already chosen his version
    over there -- *"wenn ich dort meine Wunschversion gefunden habe"* -- and
    searching for its name would hand him the four other transcriptions of
    the same song to pick from again.
    """
    meta = _fetch_json(f"{SONGSTERR_META_URL}/{song_id}")
    if not isinstance(meta, dict) or not meta.get("revisionId"):
        return None
    return SongsterrResult(
        song_id=int(song_id),
        # Named as well as it can be. A song with no title still has an id,
        # and a row that says nothing is worse than one that says the number
        # the player just typed.
        title=str(meta.get("title") or f"song {song_id}"),
        artist=str(meta.get("artist") or ""),
        by_id=True,
    )


def find(query: str, max_results: int = 10) -> list[SongsterrResult]:
    """Search, OR look up a pasted link or a typed id. Best answer first.

    Three shapes go in:

    - **A Songsterr link.** One answer, the song it names. Searching for the
      URL's text finds nothing, and the player has already decided.
    - **A bare number.** Ambiguous, and not rarely: `2112` is a Songsterr id
      and a Rush album. So it is BOTH -- the id first and marked, the search
      hits under it. Reading it as an id only would make a song named after
      a number unfindable; reading it as text only would make typing an id
      pointless.
    - **Anything else.** The search, unchanged.
    """
    from pickhero.tabs.songsterr import song_id_of

    text = (query or "").strip()
    song_id = song_id_of(text)
    if not song_id:
        return search(text, max_results)

    found = lookup(song_id)
    direct = [found] if found is not None else []
    if not text.isdigit():
        return direct                          # a link means one song
    others = [r for r in search(text, max_results) if r.song_id != song_id]
    return direct + others


def _get_source_url(song_id: int) -> str | None:
    """The GP file's URL, or None. Kept for callers that want just the URL."""
    return _source_of(song_id)[0] or None


def _slug(text: str) -> str:
    """Songsterr's own URL shape for a name: lower case, words joined by -."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def get_songsterr_url(song_id: int, title: str = "", artist: str = "") -> str:
    """The Songsterr browser URL for a song.

    A real page, not `/a/wsa/{id}`. Songsterr's URLs are
    `<artist>-<title>-tab-s<id>` and it redirects any slug with the right
    `-s<id>` tail to the right page -- but a BARE id is not that shape and
    the page does not load, which is what the player saw when a download
    failed and the browser opened on nothing.
    """
    name = _slug(f"{artist} {title}".strip()) or "tab"
    return f"{SONGSTERR_TAB_URL}/{name}-tab-s{song_id}"


def suffix_for(source_url: str) -> str:
    """The extension the downloaded file should carry.

    *"Why does it download a gp5 and when I use songsterr-downloader.com I
    get a gp?"* -- because this used to name every file `.gp5` whatever it
    was. The loader dispatches on the file's CONTENT, so a Guitar Pro 7 file
    called `.gp5` still opened; but it is a lie on disk, and it is the wrong
    file to hand to Guitar Pro itself.
    """
    from urllib.parse import urlparse
    found = Path(urlparse(source_url).path).suffix.lower()
    return found if found in GP_EXTENSIONS else ".gp5"


def download_tab(song_id: int, output_path: str | Path) -> tuple[Path | None,
                                                                 str]:
    """Download a tab. Returns (what was written, why nothing was).

    `output_path`'s SUFFIX IS REPLACED by whatever Songsterr actually holds
    -- the caller knows the name, not the format.
    """
    source_url, why = _source_of(song_id)
    if not source_url:
        return None, why

    try:
        file_data = _urlopen(source_url)
    except (urllib.error.URLError, OSError) as exc:
        return None, f"The file could not be fetched: {exc}"

    output = Path(output_path).with_suffix(suffix_for(source_url))
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(file_data)
    except OSError as exc:
        return None, f"It could not be saved: {exc}"
    return output, ""


def download_gp5(song_id: int, output_path: str | Path) -> bool:
    """Download a GP tab file from Songsterr.

    Args:
        song_id: Songsterr song ID.
        output_path: Where to save the downloaded file.

    Returns:
        True if download succeeded, False otherwise.

    The suffix of what lands is Songsterr's, not `output_path`'s -- see
    `download_tab`, which this is now a yes/no view of.
    """
    return download_tab(song_id, output_path)[0] is not None


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
    #: What the tab file is called, or would be called if Songsterr had one.
    #: The name to give a tab fetched somewhere else, so the bar map and the
    #: audio written here find it.
    wanted_name: str = ""

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
    # The suffix is Songsterr's: a Guitar Pro 7 file is a `.gp`, and calling
    # it `.gp5` was a lie on disk that only did not break the app because the
    # loader reads the CONTENT. Everything after this point takes its names
    # from what was actually written.
    written, why = download_tab(song_id, out)
    if written is None:
        # **Carry on anyway.** Songsterr does not hold a Guitar Pro file for
        # every tab -- the player hit this on four songs in a row and
        # fetched them himself from a third-party downloader. The bar map
        # and the audio are a separate request and they still work, so they
        # are still fetched and still written NEXT TO WHERE THE TAB WOULD
        # HAVE GONE. He drops his own download in under that name and the
        # song is set up: same folder, same stem is the only rule anything
        # here follows.
        grab.notes.append(why or "The tab could not be downloaded.")
    else:
        out = written
        grab.tab_path = out
    grab.wanted_name = out.name

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
