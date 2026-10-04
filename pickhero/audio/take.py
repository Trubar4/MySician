"""Record what the detector hears, from inside the app.

*"Koennen wir ein das Recording in die App einbauen, damit es auf beiden NBs
geht?"* -- `tools/record_reference.py --play-along` only exists in a checkout,
and the laptop that most needs to produce a take has nothing on it but
`MySician.exe`. **Anything reachable only from a shell does not exist on the
machine that needs it most**: the fourth instance of that in this project,
after the merge tool in `tools/`, the ffmpeg fetch and the songs folder.

**And an in-app take is better than the tool's, for a reason the tool cannot
fix.** An external recorder opens its own stream, so its audio has its own
clock and the alignment has to be SEARCHED for afterwards -- which is where
every alignment fault in this project came from: the take read as "the intro
again", the 3-of-46 against the wrong tab, the manifest stating the wrong
speed. This records the very array the detector is handed, stamped with the
ring buffer's own sample counter, so `start_sample` makes a strike's timestamp
an index into the WAV. There is nothing left to fit.

Three things it must not do:

- **Never write in the audio callback.** A disk touched from the audio thread
  is dropped buffers, which is the one fault that loses notes at random. The
  callback does one `put_nowait`; a worker drains it.
- **Never block it either.** The queue is bounded, and a worker that falls
  behind loses blocks rather than stalling the capture -- counted and written
  into the manifest, because a take with a hole in it cannot be scored and has
  to say so rather than look complete.
- **Never grow without a bound.** 88 kB a second, so `MAX_SECONDS` of it.

`record_reference.py` keeps the 29 guided exercises: they need the prompts, and
they need its own standing promise -- *"standalone on purpose, so it still runs
when the detection stack is broken"* -- which a recorder living inside the app
cannot have. This replaces `--play-along` and nothing else.
"""

from __future__ import annotations

import json
import queue
import threading
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

#: A take longer than this is stopped by itself. 88 kB a second of 16-bit
#: mono, so ten minutes is 53 MB -- past any song, and far short of a key
#: left on by accident filling a disk overnight.
MAX_SECONDS = 600.0

#: Blocks the worker may fall behind by. One block is a callback (512 samples,
#: 11.6 ms at 44.1 kHz), so this is about three seconds of slack -- more than
#: any disk hiccup, and bounded so a stalled write can never back up into the
#: audio thread.
MAX_QUEUED_BLOCKS = 256


def takes_dir(config_dir: Path) -> Path:
    """Where takes live: beside the settings, never beside the tab.

    A four-minute take is 21 MB. Next to the song it would travel with every
    folder copy and `runs.belongings()` would take it along when the song is
    deleted -- neither of which is true of a diagnostic recording.
    """
    return config_dir / "recordings"


class TakeRecorder:
    """One play-along take: a WAV and the manifest that makes it readable."""

    def __init__(self, out_dir: Path, sample_rate: int, *, song: str,
                 tempo_percent: int, device: str = "",
                 transpose: int = 0, tuning: str = "",
                 start_sample: int = 0, song_ms: float = 0.0) -> None:
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "play_along.wav"
        self.sample_rate = int(sample_rate)
        self.samples = 0
        self.dropped_blocks = 0
        self._meta = dict(song=song, tempo_percent=int(tempo_percent),
                          device=device, transpose=int(transpose),
                          tuning=tuning, start_sample=int(start_sample),
                          start_song_ms=float(song_ms))
        self._limit = int(MAX_SECONDS * self.sample_rate)
        self._q: queue.Queue = queue.Queue(maxsize=MAX_QUEUED_BLOCKS)
        self._closing = threading.Event()
        self._wave = wave.open(str(self.path), "wb")
        self._wave.setnchannels(1)
        self._wave.setsampwidth(2)
        self._wave.setframerate(self.sample_rate)
        self._worker = threading.Thread(target=self._run, daemon=True,
                                        name="take-recorder")
        self._worker.start()

    # ---------------------------------------------------------------- audio
    @property
    def seconds(self) -> float:
        return self.samples / self.sample_rate if self.sample_rate else 0.0

    @property
    def full(self) -> bool:
        return self.samples >= self._limit

    def feed(self, mono: np.ndarray) -> None:
        """Called from the AUDIO thread. One put, no allocation, no I/O."""
        if self._closing.is_set() or self.samples >= self._limit:
            return
        try:
            self._q.put_nowait(mono)
        except queue.Full:
            # Losing audio beats stalling the capture: a block missing from
            # the file is a take that says so, where a blocked callback is
            # dropped buffers and notes lost at random in the RUN itself.
            self.dropped_blocks += 1
            return
        self.samples += len(mono)

    # --------------------------------------------------------------- worker
    def _run(self) -> None:
        while True:
            try:
                block = self._q.get(timeout=0.2)
            except queue.Empty:
                if self._closing.is_set():
                    return
                continue
            if block is None:
                return
            try:
                pcm = (np.clip(block, -1.0, 1.0) * 32767.0).astype(np.int16)
                self._wave.writeframes(pcm.tobytes())
            except Exception:                   # a full disk, a dead handle
                self.dropped_blocks += 1

    # ---------------------------------------------------------------- close
    def close(self) -> dict:
        """Finish the file and write the manifest. Returns what it recorded."""
        self._closing.set()
        self._q.put(None)
        self._worker.join(timeout=5.0)
        try:
            self._wave.close()
        except Exception:
            pass
        return self._write_manifest()

    def _write_manifest(self) -> dict:
        # The shape `tools/analyze_play_along.py` already reads, field for
        # field. A format with two readers is a format that drifts, and this
        # one has four more in `tools/check_*.py`.
        take = {
            "id": "play_along",
            "block": 99,
            "title": f"Durchgespielt: {self._meta['song']}",
            "file": self.path.name,
            "technique": "free",
            "intentional_error": "",
            "shapes": [],
            "expected_midi": [],
            "song": self._meta["song"],
            "tempo_percent": self._meta["tempo_percent"],
            "seconds": round(self.seconds, 2),
            # What the tool's own take can never carry: the app's ring-buffer
            # sample index of this file's first sample, and the song position
            # at that instant. A strike's stamp is an index into the WAV.
            "start_sample": self._meta["start_sample"],
            "start_song_ms": round(self._meta["start_song_ms"], 1),
            "transpose": self._meta["transpose"],
            "tuning": self._meta["tuning"],
            "dropped_blocks": self.dropped_blocks,
            "recorded_in_app": True,
        }
        manifest = {
            "created": datetime.now().isoformat(timespec="seconds"),
            "samplerate": self.sample_rate,
            "channels": 1,
            "device": self._meta["device"],
            # The app transposes the TAB rather than retuning the player, so
            # the uniform offset the recorder asks a human for is known here.
            "tuning_offset_semitones": 0,
            "drop_semitones": 0,
            "recorded_in_app": True,
            "takes": [take],
        }
        (self.dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8")
        return take


def new_take_dir(root: Path, song: str) -> Path:
    """A folder per take, stamped and named, the way the recorder's are."""
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in song)[:60]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(root) / f"{stamp} {safe}".strip()
