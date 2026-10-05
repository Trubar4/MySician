"""Put an ffmpeg.exe in tools/, so the build can bundle one.

yt-dlp downloads what YouTube serves -- m4a (AAC) or webm (Opus). The app
plays through SDL_mixer, which decodes MP3, OGG, FLAC and WAV and none of
those two. Without ffmpeg the audio arrives on disk and silently will not
play, which is this project's oldest failure mode wearing a new hat.

**Opt-in, and no build runs it by itself.** An ffmpeg.exe is 37 MB of the
bundle -- near half of it -- and it serves one feature that does not
currently work: YouTube's bot check killed the audio half of the download,
so the recording is picked by hand. The app also finds an ffmpeg.exe dropped
BESIDE MySician.exe with no rebuild at all (`youtube._search_folders`),
which is what the download screen tells the player to do. So this is run
deliberately, before PyInstaller, by somebody who wants one in the bundle;
the spec picks up `tools/ffmpeg*` whenever it is there.

    python tools/fetch_ffmpeg.py            # fetch if missing
    python tools/fetch_ffmpeg.py --force    # fetch again

Prints one line and exits 0 when there is an ffmpeg to bundle, non-zero when
there is not -- so a build can carry on and produce an .exe that simply
cannot fetch audio, rather than failing over it.

**Every line it prints is ASCII, deliberately.** It printed one arrow, which
is in neither cp1252 nor cp850 -- so on the one path that SUCCEEDS, the
final print raised UnicodeEncodeError and the script exited 1 with the file
already on disk. That is how a green workflow came to carry a red "exit
code 1" annotation beside an .exe that had grown by 37 MB.
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

# gyan.dev's "essentials" build: one static ffmpeg.exe with the encoders
# that matter here and none of the 200 MB of the full build. Pinned to the
# release channel rather than a version, because a link to a version that
# has been rotated away is a build that fails on a Tuesday.
URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

TOOLS = Path(__file__).resolve().parent
NAME = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
TARGET = TOOLS / NAME


def _from_zip(data: bytes) -> bytes:
    """The ffmpeg binary inside the release zip.

    The archive's top folder carries the version, so the member is found by
    what it ENDS with rather than by a path that changes every release.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.namelist():
            if member.replace("\\", "/").endswith("bin/" + NAME):
                return archive.read(member)
    raise RuntimeError(f"no bin/{NAME} inside the archive")


def main(argv: list[str]) -> int:
    force = "--force" in argv

    if TARGET.is_file() and not force:
        size = TARGET.stat().st_size // (1024 * 1024)
        print(f"ffmpeg: already here ({size} MB) - {TARGET}")
        return 0

    # Already installed on this machine? Copying it beats downloading 80 MB,
    # and a build machine that can run ffmpeg has a working one by
    # definition.
    on_path = shutil.which("ffmpeg")
    if on_path and not force:
        shutil.copy2(on_path, TARGET)
        print(f"ffmpeg: copied from {on_path}")
        return 0

    print(f"ffmpeg: downloading from {URL} ...")
    try:
        with urllib.request.urlopen(URL, timeout=300) as response:
            data = response.read()
        TARGET.write_bytes(_from_zip(data))
    except Exception as exc:
        # Not fatal. The .exe still builds; it just cannot fetch audio, and
        # the download screen says so in words rather than failing silently.
        print(f"ffmpeg: NOT bundled - {type(exc).__name__}: {exc}")
        print("        The build carries on. YouTube audio will report "
              "'ffmpeg was not found'.")
        return 1

    if os.name != "nt":
        TARGET.chmod(0o755)
    size = TARGET.stat().st_size // (1024 * 1024)
    print(f"ffmpeg: {size} MB written to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
