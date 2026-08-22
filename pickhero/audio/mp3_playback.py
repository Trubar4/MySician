"""Real audio as a backing track, kept in step with the scrolling tab.

The MIDI backing is generated from the same timeline as the notes, so it can
never drift: it is told a position and plays the events at it. A recording
cannot be told a position that precisely -- it has its own clock, running in
the sound card, and the only control available is "start playing from here".
So this keeps a running comparison between where the song is and where the
recording has got to, and corrects only when the gap is big enough to hear.

Two limits are the reason this module is small, and both are worth knowing
before reaching for it:

- **A recording cannot be slowed down by playing it slower.** `pygame.mixer.music`
  plays a file at the rate it was recorded at, and resampling it to 80 % would
  drop the pitch by four semitones. Practice speed is therefore served by a
  stretched COPY of the file (`audio/timestretch.py`), which this player is
  simply handed instead of the original -- see `set_source` and the time
  scale it carries.
- **Encoder delay differs per file.** An MP3 decoder emits a short run of
  padding samples before the music, and how many depends on the encoder. No
  amount of arithmetic recovers it, which is why the per-song offset is a
  requirement rather than a convenience.
"""

from pathlib import Path

import pygame

# How far the recording may drift from the song before it is nudged back.
# Small enough that nobody hears the error, large enough that ordinary
# scheduling jitter does not cause a re-seek every frame -- a re-seek is
# audible, so correcting too eagerly is worse than the drift it fixes.
RESYNC_MS = 90.0
# Never re-seek more often than this. A file whose clock genuinely runs at a
# different rate would otherwise stutter continuously instead of drifting
# quietly, and quiet drift is the lesser fault.
MIN_RESYNC_GAP_MS = 1500.0


class Mp3Player:
    """Plays one audio file, following a song position given in milliseconds.

    Accepts anything SDL_mixer decodes -- MP3, OGG, FLAC, WAV. Named for the
    format the player asked for.
    """

    def __init__(self, path: str | Path, time_scale: float = 1.0):
        self.path = Path(path)
        # File milliseconds per song millisecond. 1.0 for the recording as it
        # was made; 1/0.8 = 1.25 for a copy stretched for 80 % practice speed,
        # where the same music takes a quarter longer to play. Everything this
        # class is told and everything it reports is in SONG time, so nothing
        # outside it has to know which file is loaded.
        self._scale = float(time_scale)
        self._ready = False
        self._muted = False
        self._playing = False
        # The song position the current play() call started from, and the
        # clock reading when it did. get_pos() counts from the play() call and
        # knows nothing about the start offset it was given.
        self._origin_ms = 0.0
        self._last_resync_ms = float("-inf")
        # The recording ran out. A backing track is often shorter than the
        # practice loop around it, and without this the sync would restart it
        # from past its own end once a frame for the rest of the song.
        self._ended = False
        self.error: str | None = None

    def open(self) -> bool:
        """Load the file. False (with .error set) if it cannot be played."""
        if not self.path.exists():
            self.error = f"File not found: {self.path.name}"
            return False
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.load(str(self.path))
        except Exception as exc:               # pygame.error, OSError, ...
            self.error = f"{type(exc).__name__}: {exc}"
            return False
        self._ready = True
        self.error = None
        return True

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def time_scale(self) -> float:
        """File milliseconds per song millisecond for the loaded file."""
        return self._scale

    def set_source(self, path: str | Path, time_scale: float = 1.0) -> bool:
        """Play a different file for the same song, at a different time scale.

        Used to swap in a tempo-stretched copy when the practice speed
        changes. Returns False (with .error set) if the new file will not
        open, leaving the player stopped rather than playing the wrong speed.
        """
        self.pause()
        self.path = Path(path)
        self._scale = float(time_scale)
        self._ready = False
        self._ended = False
        self._origin_ms = 0.0
        self._last_resync_ms = float("-inf")
        return self.open()

    def set_muted(self, muted: bool) -> None:
        """Silence or unsilence the recording without losing its place."""
        self._muted = muted
        if muted:
            self.pause()

    def is_muted(self) -> bool:
        return self._muted

    def update(self, position_ms: float) -> None:
        """Play from `position_ms`, correcting the recording if it has drifted.

        Called once a frame with the song position the recording should be at.
        """
        if not self._ready or self._muted:
            return
        if position_ms < 0:
            # Still counting in. The recording starts when the song does.
            self.pause()
            return
        if position_ms < self._origin_ms:
            # The song jumped back -- a loop, or a seek. Whatever the
            # recording did before, it can play again from here.
            self._ended = False
        if self._playing and pygame.mixer.music.get_pos() < 0:
            # Playing as far as this object knows, but the mixer has stopped:
            # the recording is over.
            self._playing = False
            self._ended = True
        if not self._playing:
            if not self._ended:
                self._start_at(position_ms)
            return
        drift = position_ms - self.position_ms()
        if abs(drift) < RESYNC_MS:
            return
        # Correcting is audible, so it is rate-limited. A file whose clock
        # genuinely runs at a different rate would otherwise stutter
        # continuously rather than drift quietly, and quiet drift is the
        # lesser fault. Only real corrections count against the limit -- the
        # first start and an explicit seek are not corrections.
        if position_ms - self._last_resync_ms < MIN_RESYNC_GAP_MS:
            return
        self._start_at(position_ms)
        self._last_resync_ms = position_ms

    def position_ms(self) -> float:
        """Where the recording has got to, in song time."""
        if not self._playing:
            return self._origin_ms
        elapsed = pygame.mixer.music.get_pos()
        if elapsed < 0:                        # finished, or never started
            return self._origin_ms
        # get_pos() counts real milliseconds of the FILE. A stretched copy
        # spends `scale` of them on every millisecond of song.
        return self._origin_ms + float(elapsed) / self._scale

    def drift_ms(self, target_ms: float) -> float:
        """How far behind (positive) or ahead the recording is running."""
        return target_ms - self.position_ms()

    def seek(self, position_ms: float) -> None:
        """Jump the recording to a song position."""
        if not self._ready or self._muted:
            self._origin_ms = max(0.0, position_ms)
            return
        if position_ms < 0:
            self.pause()
            return
        self._ended = False
        self._start_at(position_ms)

    def pause(self) -> None:
        """Stop, remembering where the song was."""
        if not self._ready:
            return
        if self._playing:
            self._origin_ms = self.position_ms()
        self._playing = False
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass

    def close(self) -> None:
        self.pause()
        self._ready = False
        try:
            pygame.mixer.music.unload()
        except Exception:
            # unload() is pygame 2.0+; stopping is enough on older builds.
            pass

    def _start_at(self, position_ms: float) -> None:
        """(Re)start playback from a song position.

        A position past the end of the recording is not an error -- a backing
        track is often shorter than the practice loop around it -- so it
        simply leaves the recording silent rather than raising.
        """
        start_s = max(0.0, position_ms) * self._scale / 1000.0
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.play(start=start_s)
        except Exception as exc:
            # Not every decoder can start from the middle of a file. Say so
            # rather than falling silent: a backing track that plays from the
            # top of the song and not after a seek is a specific, fixable
            # thing, and indistinguishable from "broken" without the message.
            self._playing = False
            if start_s > 0:
                self.error = ("This file cannot start from the middle "
                              f"({type(exc).__name__}) — try an OGG or WAV")
            else:
                self.error = f"{type(exc).__name__}: {exc}"
            return
        self.error = None
        self._origin_ms = max(0.0, position_ms)
        self._playing = True
        self._ended = False


def pick_audio_file(initial_dir: str | None = None) -> str | None:
    """Ask the player for a file with the operating system's own dialog.

    PyGame has no file dialog and building one means reimplementing a browser
    the player already knows. tkinter ships with Python and gives the real
    Windows dialog for ten lines, so that is what this is. Returns None when
    tkinter is unavailable (some Linux builds) or the player cancels, and the
    caller must treat both the same way.
    """
    try:
        import tkinter
        from tkinter import filedialog
    except Exception:
        return None
    try:
        root = tkinter.Tk()
        root.withdraw()
        # Without this the dialog can open behind the game window on Windows
        # and look like nothing happened at all.
        root.attributes("-topmost", True)
        chosen = filedialog.askopenfilename(
            title="Backing track for this song",
            initialdir=initial_dir or None,
            filetypes=[
                ("Audio", "*.mp3 *.ogg *.wav *.flac"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()
    except Exception:
        return None
    return chosen or None
