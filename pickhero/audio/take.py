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

**And "nothing left to fit" held only while nothing moved the mapping.** The
first build stored one `start_song_ms`, read at the keypress -- and
`_reanchor_audio_clock` moves that relationship at every seek, pause, resume,
tempo change and loop breath. Pressing record BEFORE pressing play is the
natural order, so the anchor fires *after* the reading, every time. Measured on
the player's own two takes: the one with two anchors in its run had a manifest
**3.7 s out** (its `start_song_ms` implied an offset of -5930 ms where the first
strike was placed at -9675 -- the second anchor's value to the millisecond),
and scored **31 %** read that way against **87 %** read at the offset the app
really used. The other, with one anchor and that one before the take, was right
to 350 ms.

So the manifest carries `song_at`: the app's own `audio_offset_ms`, marked
whenever it MOVES, as `[[wav_sample, offset_ms], ...]`. `song_ms_at` is the one
reader of it. Storing the matcher's offset rather than a clock pair matters --
the offset already carries the player's `K` calibration, which `_playback_ms`
does not, and that was the other 276 ms.

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

#: How far the audio-to-song mapping must move before a new mark is worth
#: keeping. The hit window is 150 ms and the creep over a real run (the
#: recording pull plus the sound-card tracking) was about 200 ms, so a tenth
#: of the window is fine enough that a mark is never why a strike misses, and
#: coarse enough that a creeping pull does not write one every frame.
OFFSET_STEP_MS = 20.0

#: Marks a take may carry. Bounded like everything else here: a four-minute
#: take drifting the whole time writes a few dozen, and a pathological one
#: cannot grow the manifest without limit.
MAX_OFFSET_MARKS = 4096

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
                 start_sample: int = 0, song_ms: float = 0.0,
                 offset_ms: float | None = None) -> None:
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
        # The mapping from this file's samples to song time, marked whenever
        # it moves. Seeded here so a take always carries at least one mark,
        # whatever the caller knows: without a first entry there is nothing to
        # read a strike against and the take is unscorable.
        if offset_ms is None:
            offset_ms = float(song_ms) - int(start_sample) * 1000.0 / max(
                1, int(sample_rate))
        self._offsets: list[list[float]] = [[0, float(offset_ms)]]
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

    def note_offset(self, ring_sample: int, offset_ms: float) -> None:
        """Mark the audio-to-song mapping, from the FRAME that holds both.

        Called once a frame with the matcher's own `audio_offset_ms`. Only a
        move worth `OFFSET_STEP_MS` is kept, so an anchor's jump is recorded
        exactly and the pull's creep is recorded in steps rather than per
        frame.

        Never raises and never blocks: this is on the game loop, and a take
        that cannot write a mark must not be why a frame dies.
        """
        try:
            if self._closing.is_set() or len(self._offsets) >= MAX_OFFSET_MARKS:
                return
            if abs(float(offset_ms) - self._offsets[-1][1]) < OFFSET_STEP_MS:
                return
            at = int(ring_sample) - int(self._meta["start_sample"])
            self._offsets.append([max(0, at), float(offset_ms)])
        except Exception:
            return

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
            # ...and every move of that mapping, because one reading of it is
            # only true until the next seek. `song_ms_at` is what reads this.
            "song_at": [[int(at), round(off, 1)] for at, off in self._offsets],
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


def offset_marks(take: dict) -> list[tuple[int, float]]:
    """The take's mapping marks, whatever vintage the manifest is.

    A take recorded before `song_at` existed carries only `start_song_ms`, so
    it gets the one mark that implies -- which is what it always meant. The
    fallback is here rather than in every caller, because five tools read this
    manifest and a format with five readers of its own is a format that
    drifts.
    """
    marks = take.get("song_at")
    if isinstance(marks, list) and marks:
        out = []
        for entry in marks:
            try:
                out.append((int(entry[0]), float(entry[1])))
            except (TypeError, ValueError, IndexError):
                continue
        if out:
            return sorted(out)
    start = int(take.get("start_sample", 0) or 0)
    rate = float(take.get("samplerate", 0) or 0)
    song = float(take.get("start_song_ms", 0.0) or 0.0)
    # Without a rate the old field cannot be turned into an offset at all, so
    # say so by returning nothing rather than inventing a zero that would
    # place every strike at the top of the song.
    if rate <= 0:
        return []
    return [(0, song - start * 1000.0 / rate)]


def song_ms_at(take: dict, wav_sample: int, sample_rate: int) -> float | None:
    """Where in the SONG the take's sample `wav_sample` was played.

    The app's own arithmetic rather than a reconstruction of it: a strike's
    adjusted stamp is `ring_ms + audio_offset_ms`, and `ring_ms` is this
    file's sample plus `start_sample`. `None` where the take carries no
    mapping at all, because a take nothing can place is a take to say so
    about rather than to score against bar one.
    """
    marks = offset_marks({**take, "samplerate": sample_rate})
    if not marks or sample_rate <= 0:
        return None
    offset = marks[0][1]
    for at, value in marks:
        if at > int(wav_sample):
            break
        offset = value
    ring = int(take.get("start_sample", 0) or 0) + int(wav_sample)
    return ring * 1000.0 / sample_rate + offset


def new_take_dir(root: Path, song: str) -> Path:
    """A folder per take, stamped and named, the way the recorder's are."""
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in song)[:60]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(root) / f"{stamp} {safe}".strip()
