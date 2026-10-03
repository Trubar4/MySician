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


def _urlopen_full(url: str, timeout: int = REQUEST_TIMEOUT):
    """(body bytes, the response headers) for a URL.

    The headers come back because `Last-Modified` is the one DATE anything
    in this path carries. Neither Songsterr's meta reply nor the Guitar Pro
    format has a date field -- measured on the player's own eight tabs,
    where `Words`, `Music`, `Copyright` and `Tabber` are empty or name the
    downloader rather than a person -- so what their server says about the
    file is all there is, and it is a fact rather than an inference.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers


def _urlopen(url: str, timeout: int = REQUEST_TIMEOUT) -> bytes:
    """Fetch a URL with browser-like headers. Returns response body bytes."""
    return _urlopen_full(url, timeout)[0]


def _header_date(headers) -> str:
    """"2026-03-11" from an HTTP date header, or "" if there is not one.

    The day only: an hour on a CDN is the hour the file was copied there and
    says nothing a player would act on.
    """
    raw = ""
    try:
        raw = headers.get("Last-Modified") or ""
    except AttributeError:
        return ""
    if not raw:
        return ""
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return ""


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


#: How far back through a tab's history to look for a revision that still
#: has a file behind it. Eight because the walk costs one request per hop
#: and a tab edited daily for a fortnight is not the same tab any more --
#: past that the bar count starts to move, and a bar map whose count differs
#: is refused anyway.
SOURCE_HOPS = 8


def _source_of(song_id: int) -> tuple[str, int, str]:
    """(the GP file's URL, the revision it came from, why there is none).

    Two API calls per revision:
        1. /api/meta/{songId} -> revisionId
        2. /api/revision/{revisionId} -> source URL

    **The newest revision often has no file, and an older one does.** Read
    off the player's own data for Papa Roach 14907: revision 6688469 has no
    `source` key at all, and its `prevRevisionId` 5981666 has `"source": ""`.
    Songsterr keeps these tabs in their own format -- per-track hashes, an
    `audioV4` mix -- and a Guitar Pro file exists only where somebody
    uploaded one. Since every revision links to the one before it the
    history is walkable, and an earlier revision of the same tab is very
    nearly the same tab.

    The revision comes back as well as the URL because **the bar map has to
    come from the same one**. A file from revision N against a per-bar map
    from revision N+6 is two different edits of the song pretending to be
    one, and the drift would read as a bad measurement.

    **The reason is returned, not swallowed.** "Songsterr has no file for
    this tab" and "the network is down" are the same empty result and
    completely different problems, and the first build reported both by
    opening a browser at a page that did not load.
    """
    meta = _fetch_json(f"{SONGSTERR_META_URL}/{song_id}")
    if not isinstance(meta, dict):
        return "", 0, "Songsterr did not answer for this song"

    revision_id = meta.get("revisionId")
    if not revision_id:
        return "", 0, "Songsterr has no revision for this tab"

    seen: list[int] = []
    last_keys = "nothing"
    while revision_id and revision_id not in seen and len(seen) < SOURCE_HOPS:
        seen.append(revision_id)
        revision = _fetch_json(f"{SONGSTERR_REVISION_URL}/{revision_id}")
        if not isinstance(revision, dict):
            if len(seen) == 1:
                return "", 0, (f"Songsterr did not answer for revision "
                               f"{revision_id}")
            break                     # the walk ran out, not the network
        source = revision.get("source")
        if isinstance(source, str) and source.startswith("http"):
            return source, int(revision_id), ""
        last_keys = ", ".join(sorted(revision)[:8]) or "nothing"
        revision_id = revision.get("prevRevisionId")

    # Named with what was actually looked at. "Songsterr holds no file" said
    # of one revision is a guess; said of eight it is a finding.
    plural = "s" if len(seen) != 1 else ""
    return "", 0, (f"Songsterr holds no Guitar Pro file for this tab - "
                   f"{len(seen)} revision{plural} checked back to "
                   f"{seen[-1] if seen else 0} (it has: {last_keys})")


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


def download_tab(song_id: int, output_path: str | Path
                 ) -> tuple[Path | None, int, str, str]:
    """Download a tab. (what was written, its revision, why not, its date).

    `output_path`'s SUFFIX IS REPLACED by whatever Songsterr actually holds
    -- the caller knows the name, not the format. The revision comes back so
    the bar map can be asked for from the SAME edit of the song.

    The date is Songsterr's own `Last-Modified` for the file and is empty
    where their server does not send one. It is deliberately NOT the local
    file's time, which is the moment of the download and tells the player
    nothing he does not already know.
    """
    source_url, revision_id, why = _source_of(song_id)
    if not source_url:
        return None, 0, why, ""

    try:
        file_data, headers = _urlopen_full(source_url)
    except (urllib.error.URLError, OSError) as exc:
        return None, 0, f"The file could not be fetched: {exc}", ""

    output = Path(output_path).with_suffix(suffix_for(source_url))
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(file_data)
    except OSError as exc:
        return None, 0, f"It could not be saved: {exc}", ""
    return output, revision_id, "", _header_date(headers)


def _mmss(ms: float) -> str:
    """"4:40", the unit a song length is compared in."""
    total = int(round(max(0.0, ms) / 1000.0))
    return f"{total // 60}:{total % 60:02d}"


def describe_tab(path: str | Path) -> tuple[list[str], int]:
    """(lines about the file that just landed, how many bars it has).

    Tuning, tracks, length, bars.

    *"Kannst du mir beim Herunterladen noch mehr Details geben? Stimmung,
    Autor, Datum"* -- and the tuning is the one of those three that is a
    FACT rather than a field somebody forgot to fill in. It is read out of
    the file itself, through the same `describe_file` the song list uses, so
    the two can never disagree about what a song is tuned to.

    The length is here because it is the number the player compares against
    YouTube before concluding he has the wrong tab, and it is the WRITTEN
    length -- a song is as long as its bars, not as long as its last note.

    The bar count comes back as well as being said, because the bar map is
    refused when its own count differs -- and "72 bars against this tab's
    80" is a thing to act on where either number alone is not.

    Never raises: a file this cannot read is still a file that downloaded,
    and a detail line is not worth losing a song over.
    """
    from pickhero.audio.note_utils import tuning_label

    path = Path(path)
    out: list[str] = []
    try:
        from pickhero.tabs.song_index import describe_file
        info = describe_file(path)
    except Exception:
        return out, 0
    if info.readable and info.tracks:
        word = "guitar track" if info.tracks == 1 else "guitar tracks"
        tunings = info.distinct_tunings
        # The name AND the letters, where the tuning has a name. The song
        # list shows the letters alone because it has one short row for
        # every song; this is read once, about one song, and "Drop D" is
        # what the player calls it while "D A D G B E" is what he checks.
        said = []
        for letters in tunings[:2]:
            name = tuning_label(letters)
            said.append(f"{name} ({letters})" if name != letters else letters)
        if len(tunings) > 2:
            said.append(f"+{len(tunings) - 2} more")
        head = f"{info.tracks} {word}"
        out.append(f"{head} - {', '.join(said)}" if said else head)
    elif info.readable:
        out.append("No guitar track in it.")
    else:
        out.append("The file could not be read.")

    try:
        from pickhero.tabs.loader import load_gp_file
        timeline = load_gp_file(path, 0)
    except Exception:
        return out, 0
    bars = len(timeline.measures)
    if bars:
        out.append(f"{_mmss(timeline.duration_ms)} of music in {bars} "
                   f"{'bar' if bars == 1 else 'bars'}")
    return out, bars


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
    #: The revision the FILE came from, and the newest one Songsterr has.
    #: They differ whenever the walk had to go back for a revision that
    #: still has a Guitar Pro file behind it, and that is worth saying: a
    #: tab from one edit timed by a bar map from another is two different
    #: transcriptions pretending to be one.
    revision_id: int = 0
    latest_revision_id: int = 0
    #: How many bars the TAB has, which is what the bar map's own count is
    #: judged against: a map of a different edit is refused on that count.
    tab_bars: int = 0
    #: Songsterr's own `Last-Modified` for the file, "YYYY-MM-DD" or empty.
    file_date: str = ""
    #: True where Songsterr says they transcribed it from the audio
    #: themselves. **None where the reply does not say** -- an absent field
    #: is not evidence that a person uploaded it.
    ai_generated: bool | None = None
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


def _revision_notes(grab: "Grab") -> list[str]:
    """What Songsterr says about WHICH transcription this is.

    The closest thing their API has to an author. `aiGenerated` separates
    their own pipeline's transcription from one a person uploaded -- which
    is a quality signal a player can act on -- and the revision says which
    edit of the tab is on disk. Everything the player asked for by the name
    "Autor" is empty in the files themselves: measured over his eight tabs,
    `Words`, `Music`, `WordsAndMusic` and `Copyright` are blank in all
    eight and `Tabber` says "Songsterr Downloader", which is the tool.

    Nothing is said where nothing is known. An absent `aiGenerated` is not
    a person, and a revision of 0 is a tab that did not download.
    """
    out: list[str] = []
    if grab.ai_generated is True:
        out.append("Songsterr transcribed this one from the audio")
    elif grab.ai_generated is False:
        out.append("A person uploaded this transcription")
    if not grab.revision_id:
        return out
    if grab.latest_revision_id and grab.latest_revision_id != grab.revision_id:
        # Worth its own sentence: the newest revision often has no Guitar
        # Pro file and an older one does, so what landed is not the edit
        # Songsterr shows on its own site.
        out.append(f"Revision {grab.revision_id} - not the newest "
                   f"({grab.latest_revision_id}), which has no file")
    else:
        out.append(f"Revision {grab.revision_id}")
    return out


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
    written, revision_id, why, file_date = download_tab(song_id, out)
    grab.revision_id = revision_id
    grab.file_date = file_date
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
    if grab.tab_path is not None:
        lines, grab.tab_bars = describe_tab(grab.tab_path)
        grab.notes += lines
    if file_date:
        # Said as what it is. Songsterr's reply carries no transcription
        # date and neither does the Guitar Pro format, so this is when the
        # FILE last changed on their server and nothing more.
        grab.notes.append(f"Songsterr's file last changed {file_date}")

    # The bar map, cached BESIDE THE TAB. Asked for here rather than the
    # first time the player presses Ctrl+S, so the song works on a machine
    # with no network -- which is what "I use it locally, offline" means.
    step(0.35, "asking Songsterr for its bar map")
    try:
        meta = songsterr.fetch_meta(song_id)
        # THE REVISION THE FILE CAME FROM, where there was one. The newest
        # revision often has no Guitar Pro file and an older one does, and a
        # tab from revision N timed by a map from revision N+6 is two
        # different edits of the song pretending to be one. The latest is
        # the fallback, because an old revision may have no video points at
        # all -- and a map from the wrong edit is refused later on its bar
        # count anyway, which is the safety net this leans on.
        entries = []
        if revision_id and revision_id != int(meta["revisionId"]):
            try:
                entries = songsterr.fetch_entries(song_id, revision_id)
            except songsterr.NotFound:
                entries = []
        if not songsterr.all_bar_times(entries):
            entries = songsterr.fetch_entries(song_id,
                                              int(meta["revisionId"]))
        songsterr.save_cache(out, song_id, meta, entries)
        grab.latest_revision_id = int(meta["revisionId"])
        # Only where the reply says so, either way. A missing field means
        # Songsterr did not answer the question, not that it answered no.
        if isinstance(meta.get("aiGenerated"), bool):
            grab.ai_generated = bool(meta["aiGenerated"])
        grab.notes += _revision_notes(grab)
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
    # Beside the tab's own count, where they differ. A map whose count does
    # not match is refused later, and the two numbers together are what says
    # why -- a tab padded out to the end of the sheet has empty bars the map
    # never timed, and that case is accepted.
    if grab.tab_bars and grab.bars != grab.tab_bars:
        grab.notes.append(f"Bar map: {grab.bars} bars - the tab has "
                          f"{grab.tab_bars}")
    else:
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
