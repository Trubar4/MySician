"""The recording Songsterr timed the tab against, pulled down as a file.

Songsterr's bar map is a **timestamp per measure into a YouTube video**. Up
to now the player supplied his own MP3 and the app had to find the constant
between the two by listening -- and on What's Up, four chords repeated for
four minutes, that search reads +9.9, -34.4, -6.2 and +21.1 s and means
none of it.

Pull the audio from **the video the points were made against** and that
whole problem shrinks to an encoder delay. The map is not fitted to a
different recording of the song; it is fitted to the recording it came from.

This is yt-dlp doing the pulling, and ffmpeg turning what YouTube serves
into something the app can play. Both matter:

- YouTube hands out **m4a (AAC) or webm (Opus)**. SDL_mixer -- which is what
  `mp3_playback` is -- decodes MP3, OGG, FLAC and WAV. Neither of YouTube's
  two formats is on that list, so without ffmpeg there is a file on disk
  that silently will not play, which is this project's oldest failure mode.
- The re-encode costs an encoder delay of about 26 ms at the front. That is
  well inside what the sync fit finds anyway, and it is why the fit still
  runs rather than the offset being assumed to be zero.

Nothing here uploads, accounts for, or remembers anything: a video id goes
out, a file lands in the songs folder, and it stays there.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

#: What SDL_mixer can actually decode, out of what ffmpeg can write. MP3
#: rather than OGG because every file the app has ever been handed is one,
#: and `mp3_playback.seek` has been measured on MP3 decoding specifically.
CODEC = "mp3"
QUALITY = "192"

#: A watch URL rather than a bare id: yt-dlp accepts both, but an id that
#: begins with a dash is read as a switch, and video ids do begin with a
#: dash -- Thunder's is `-IWAOPi4VCc`.
WATCH = "https://www.youtube.com/watch?v={}"


class NotAvailable(RuntimeError):
    """Said out loud, with the thing that is missing named."""


def _search_folders():
    """Where an ffmpeg that belongs to this app would be, best first."""
    # The bundle before the machine. A frozen app that carries its own
    # ffmpeg must use that one: "works on the development laptop" is the
    # exact gap the build stamp and `--check-engraver` exist to close.
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:
        yield Path(bundle)
    if getattr(sys, "frozen", False):
        yield Path(sys.executable).parent
    # The source tree, so `build.bat` and a plain `python -m pickhero` look
    # in the same place.
    yield Path(__file__).resolve().parents[2] / "tools"


def ffmpeg_path() -> Path | None:
    """The ffmpeg this app should use, or None if there is not one."""
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for folder in _search_folders():
        try:
            found = folder / name
            if found.is_file():
                return found
        except OSError:
            continue
    on_path = shutil.which("ffmpeg")
    return Path(on_path) if on_path else None


def missing() -> str:
    """"" when the audio can be fetched, otherwise what is missing.

    A string rather than a bool because the two halves fail differently and
    the player can only act on the one that is actually absent.
    """
    try:
        import yt_dlp                                  # noqa: F401
    except ImportError:
        return "yt-dlp is not installed"
    if ffmpeg_path() is None:
        return "ffmpeg was not found"
    return ""


def available() -> bool:
    return not missing()


def audio_path_for(tab_path) -> Path:
    """The recording that belongs to this tab, by name.

    The same stem as the tab, because `song_key` IS the tab's stem and
    `mp3_path_for` already falls back to a file of the same name in the
    songs folder. Downloading to that name is what makes the recording
    survive being carried to a second machine.
    """
    tab = Path(tab_path)
    return tab.with_name(tab.stem + "." + CODEC)


def fetch_audio(video_id: str, out_path, on_progress=None) -> Path:
    """Download one video's audio to `out_path`. Returns the file written.

    Args:
        video_id: the YouTube id from Songsterr's video-points entry.
        out_path: where the finished file should land; its suffix is
            replaced, because the codec is decided here and not by whoever
            named the file.
        on_progress: called with (fraction 0..1, what it is doing). May be
            None. Returning False from it does NOT cancel -- yt-dlp has no
            seam for that -- it is there so the screen can keep drawing.

    Raises:
        NotAvailable: yt-dlp or ffmpeg is missing, the video is gone, or the
            download failed. The reason is in the message.
    """
    reason = missing()
    if reason:
        raise NotAvailable(reason)
    if not video_id:
        raise NotAvailable("no video id")

    import yt_dlp

    out = Path(out_path)
    out = out.with_name(out.stem + "." + CODEC)
    out.parent.mkdir(parents=True, exist_ok=True)

    def hook(status):
        if on_progress is None:
            return
        if status.get("status") == "downloading":
            total = (status.get("total_bytes")
                     or status.get("total_bytes_estimate") or 0)
            done = status.get("downloaded_bytes") or 0
            share = (done / total) if total else 0.0
            # Capped below 1.0: the conversion still has to happen, and a
            # bar that reaches the end and then sits there is a bar that
            # says the app has hung.
            on_progress(min(0.9, share * 0.9), "downloading the audio")
        elif status.get("status") == "finished":
            on_progress(0.9, "converting to MP3")

    options = {
        "format": "bestaudio/best",
        # yt-dlp appends the real extension, then the post-processor
        # replaces it. Handing it the stem is what keeps both from
        # producing `song.mp3.mp3`.
        "outtmpl": str(out.with_suffix("")) + ".%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": False,
        "ffmpeg_location": str(ffmpeg_path()),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": CODEC,
            "preferredquality": QUALITY,
        }],
        "progress_hooks": [hook],
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([WATCH.format(video_id)])
    except Exception as exc:                # yt-dlp raises its own family
        raise NotAvailable(f"{type(exc).__name__}: {exc}") from exc

    if not out.is_file():
        # The download reported success and there is no file. Said rather
        # than returned, because a path to nothing is what the player would
        # then try to play.
        raise NotAvailable(f"nothing was written to {out.name}")
    if on_progress is not None:
        on_progress(1.0, "audio ready")
    return out
