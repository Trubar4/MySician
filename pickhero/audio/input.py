"""Audio input capture using sounddevice.

Runs a sounddevice InputStream that feeds audio buffers to the pitch detector.
Detected notes are pushed to a thread-safe queue for consumption by the main thread.
"""

import queue
import statistics
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

from pickhero.audio.chord_verify import (
    guard_samples, min_window_samples, skip_samples, window_samples,
)
from pickhero.audio.detector import PitchDetector, DetectedNote
from pickhero.audio.note_utils import (
    GUITAR_MIDI_MIN, freq_to_midi, midi_to_name, is_in_guitar_range,
)
from pickhero.config import Config

# Seconds of raw audio kept for chord verification. Only ~0.4 s is needed per
# strike; the rest is slack for a main thread that falls behind.
RING_SECONDS = 3.0

# Verification windows held for the main thread. Each is ~64 KB, and a
# consumer more than this many strikes behind has no use for the old ones.
MAX_QUEUED_WINDOWS = 16


@dataclass
class TimestampedNote:
    """A detected note with a timestamp (ms from session start)."""
    note: DetectedNote
    timestamp_ms: float
    # Absolute sample index of the strike, so the raw audio window for chord
    # verification can be cut from the ring buffer once it has filled.
    sample_pos: int | None = None


@dataclass
class StrikeWindow:
    """Raw audio just after a strike, for per-string chord verification.

    Emitted up to ~380 ms after the strike (the verifier wants a 341 ms window
    to resolve a semitone), so it always trails the strike note it belongs to.
    On a fast passage it is cut short at the next strike and arrives sooner;
    if that leaves too little to judge, no window is emitted at all.

    Matched back to its strike by sample_pos, not by timestamp: the UI scales
    note timestamps by the tempo factor and overwrites them entirely in wait
    mode, while the sample index stays what the audio thread recorded.
    """
    timestamp_ms: float
    sample_pos: int
    audio: np.ndarray
    sample_rate: int


class _AudioRing:
    """Fixed-size ring of recent mono audio, indexed by absolute sample number.

    Written only from the audio thread; windows are copied out there too, so
    nothing mutable crosses the thread boundary.
    """

    def __init__(self, capacity: int):
        self._buf = np.zeros(capacity, dtype=np.float32)
        self._cap = capacity
        self.written = 0

    def push(self, chunk: np.ndarray) -> None:
        n = len(chunk)
        if n >= self._cap:
            self._buf[:] = chunk[-self._cap:]
        else:
            end = self.written % self._cap
            first = min(n, self._cap - end)
            self._buf[end:end + first] = chunk[:first]
            if n > first:
                self._buf[:n - first] = chunk[first:]
        self.written += n

    def read(self, start: int, length: int) -> np.ndarray | None:
        """Samples [start, start+length), or None if not (yet) available."""
        if start < 0 or length <= 0:
            return None
        if start + length > self.written:
            return None                       # has not arrived yet
        if start < self.written - self._cap:
            return None                       # already overwritten
        out = np.empty(length, dtype=np.float32)
        begin = start % self._cap
        first = min(length, self._cap - begin)
        out[:first] = self._buf[begin:begin + first]
        if length > first:
            out[first:] = self._buf[:length - first]
        return out


class OnsetPitchCollector:
    """Aggregates post-onset pitch frames into one note per strike.

    The frame where the onset fires contains the attack transient, where
    YIN either fails the confidence filter or reports a wrong pitch — and
    with a 4096 pitch window the first frames after the strike still mostly
    contain the PREVIOUS note's decay. Matching a strike on the onset frame
    alone loses most notes or blames the old pitch. So: when an onset
    fires, skip the first frames until the analysis window has flushed,
    collect confident pitch frames, and emit one note carrying the median
    of the latest samples and the strike timestamp.
    """

    # Frames to ignore right after the onset (analysis window still
    # dominated by the previous note / attack transient)
    SKIP_FRAMES = 3
    # Total frames after the onset before the strike is finalized
    # (~128 ms at 48 kHz / hop 512); a re-strike finalizes earlier
    COLLECT_FRAMES = 12
    # Median over the latest samples only — they are the most settled
    MEDIAN_LAST = 4

    # A pitch below the guitar range is a chord subharmonic (the common
    # period of several strings): fold it up by octaves until it is
    # playable. At most 2 octaves — anything deeper is noise.
    MAX_FOLD_OCTAVES = 2

    def __init__(self):
        self._pending_t_ms: float | None = None
        self._pending_sample: int | None = None
        self._frame_count = 0
        self._freqs: list[float] = []
        self._confs: list[float] = []
        self._folded: list[bool] = []

    def reset(self) -> None:
        self._pending_t_ms = None
        self._pending_sample = None
        self._frame_count = 0
        self._freqs = []
        self._confs = []
        self._folded = []

    def process_frame(
        self, freq: float, confidence: float, is_onset: bool,
        t_ms: float, min_confidence: float, sample_pos: int | None = None,
    ) -> TimestampedNote | None:
        """Feed one detector frame; returns a strike note when one settles."""
        if is_onset:
            finished = self._finalize()  # a rapid re-strike closes the previous one
            self._pending_t_ms = t_ms
            self._pending_sample = sample_pos
            self._frame_count = 0
            self._freqs = []
            self._confs = []
            self._folded = []
            return finished

        if self._pending_t_ms is None:
            return None

        self._frame_count += 1
        if self._frame_count > self.SKIP_FRAMES:
            self._collect(freq, confidence, min_confidence)
        if self._frame_count >= self.COLLECT_FRAMES:
            return self._finalize()
        return None

    def _collect(self, freq: float, confidence: float, min_confidence: float) -> None:
        if confidence < min_confidence or freq <= 0:
            return
        folded = False
        for _ in range(self.MAX_FOLD_OCTAVES):
            if freq_to_midi(freq) >= GUITAR_MIDI_MIN:
                break
            freq *= 2.0
            folded = True
        if is_in_guitar_range(freq_to_midi(freq)):
            self._freqs.append(freq)
            self._confs.append(confidence)
            self._folded.append(folded)

    def _finalize(self) -> TimestampedNote | None:
        if self._pending_t_ms is None:
            return None
        t_ms = self._pending_t_ms
        sample_pos = self._pending_sample
        freqs, confs, folded = self._freqs, self._confs, self._folded
        self.reset()
        if not freqs:
            # A strike that passed the noise gate but never produced a pitch.
            # Dropping it silently is what made dead notes unhittable: the tab
            # asks for a percussive click, the detector had nothing to report,
            # and the note timed out as a miss however well it was played.
            # Reported as unpitched instead, for the matcher to accept only
            # where the tab actually wrote a dead note.
            note = DetectedNote(
                midi_note=0, frequency=0.0, confidence=0.0, name="",
                is_onset=True, unpitched=True,
            )
            return TimestampedNote(note=note, timestamp_ms=t_ms,
                                   sample_pos=sample_pos)
        used = freqs[-self.MEDIAN_LAST:]
        used_folded = folded[-self.MEDIAN_LAST:]
        freq = statistics.median(used)
        midi = freq_to_midi(freq)
        note = DetectedNote(
            midi_note=midi,
            frequency=freq,
            confidence=max(confs),
            name=midi_to_name(midi),
            is_onset=True,
            subharmonic=sum(used_folded) * 2 >= len(used_folded),
        )
        return TimestampedNote(note=note, timestamp_ms=t_ms, sample_pos=sample_pos)


class AudioCapture:
    """Captures audio from an input device and runs pitch detection.

    Detected notes are pushed to `note_queue` for consumption by other threads.
    The sounddevice callback runs in a separate thread automatically.
    """

    def __init__(self, config: Config | None = None):
        if config is None:
            config = Config()
        self.config = config
        ac = config.audio

        calibration = getattr(config, 'calibration', None) or None
        self.detector = PitchDetector(
            buf_size=ac.buf_size,
            hop_size=ac.hop_size,
            sample_rate=ac.sample_rate,
            confidence_threshold=ac.confidence_threshold,
            onset_threshold=ac.onset_threshold,
            noise_gate_db=ac.noise_gate_db,
            yin_tolerance=ac.yin_tolerance,
            calibration=calibration if calibration else None,
        )
        self.note_queue: queue.Queue[TimestampedNote] = queue.Queue()
        # Raw audio windows for chord verification, one per strike. Separate
        # from note_queue because they trail their strike by ~380 ms.
        self.strike_queue: queue.Queue[StrikeWindow] = queue.Queue()
        self._ring = _AudioRing(int(ac.sample_rate * RING_SECONDS))
        # [timestamp_ms, sample_pos, length] — length shrinks when a following
        # strike arrives before the full window has been collected.
        self._pending_windows: list[list[float]] = []
        self._sample_rate: int = int(ac.sample_rate)
        self._stream: sd.InputStream | None = None
        self._signal_db: float = -120.0
        self._tuner_freq: float = 0.0
        self._tuner_confidence: float = 0.0
        # Cached (samplerate, channels) probe result, keyed by device index
        self._resolved_settings: tuple[int, int] | None = None
        self._resolved_device: int | None = None
        self._onset_collector = OnsetPitchCollector()
        # Callbacks that arrived with an overflow warning. Worth showing: a
        # machine that drops audio steadily cannot be diagnosed from the
        # scoring, because the symptom is notes going missing at random.
        self.dropped_buffers = 0

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """Sounddevice callback — runs in audio thread."""
        if status:
            # An overflow says samples were lost BEFORE this callback. The ones
            # in hand are still good audio, so they are processed like any
            # other -- this used to return instead, which threw them away too
            # AND left the ring's sample counter where it was. Since every
            # strike is stamped from that counter, each discarded buffer
            # shifted the rest of the song 10.7 ms early, and the error
            # accumulated: measured on a real take, 2 % of buffers dropped this
            # way took detection from 42 of 46 strikes down to 17, while the
            # same drops with the counter still advancing cost only two.
            self.dropped_buffers += 1

        # indata shape: (frames, channels) — downmix so the guitar is picked
        # up no matter which interface input (1 or 2) it is plugged into
        if indata.shape[1] > 1:
            mono = indata.mean(axis=1)
        else:
            mono = indata[:, 0].copy()

        # Keep the raw audio so a strike's verification window can be cut out
        # once enough of it has arrived. Sample index of hop i is base + i.
        base = self._ring.written
        self._ring.push(mono)

        # Process in hop_size chunks
        hop = self.detector.hop_size
        for i in range(0, len(mono) - hop + 1, hop):
            chunk = mono[i:i + hop]
            result = self.detector.process(chunk)
            self._signal_db = self.detector.last_signal_db
            self._tuner_freq = self.detector.last_freq
            self._tuner_confidence = self.detector.last_confidence
            # Timestamp from the SAMPLE position, not the wall clock. The
            # callback reads perf_counter when it happens to run, so a block
            # delivered late — or several delivered back to back, as WASAPI
            # does under load — stamped strikes with whatever time processing
            # started, wandering by however long the burst was. The sample
            # index advances exactly with the audio and cannot drift.
            elapsed_ms = (base + i) * 1000.0 / self._sample_rate

            # Strike notes come from the onset collector: it waits for the
            # pitch to settle after the attack and stamps the strike time
            strike = self._onset_collector.process_frame(
                self.detector.last_freq,
                self.detector.last_confidence,
                self.detector.last_is_onset,
                elapsed_ms,
                self.detector.confidence_threshold,
                sample_pos=base + i,
            )
            if strike is not None:
                self.note_queue.put(strike)
                if strike.sample_pos is not None:
                    self._pending_windows.append(
                        [strike.timestamp_ms, strike.sample_pos,
                         window_samples(self._sample_rate)]
                    )

            # Order matters: the strike above is the one this onset just
            # closed, so it has to be in the list before being trimmed.
            if self.detector.last_is_onset:
                self._limit_pending_windows(base + i)

            # Sustained pitch stream (tuner, console, wait mode) — onset
            # flag cleared so strikes are only reported by the collector
            if result is not None:
                result.is_onset = False
                self.note_queue.put(TimestampedNote(note=result, timestamp_ms=elapsed_ms))

        self._emit_ready_windows()

    def _limit_pending_windows(self, next_pos: int) -> None:
        """Cut earlier strikes' windows short so they end before this one.

        A window that runs into the following chord contains pitches the tab
        never expected there, and the verifier would convict strings that were
        played correctly — exactly what fast chord changes were doing. Trim to
        the gap actually available, and drop the strike entirely when the gap
        is too small to judge: no verdict beats a wrong one.
        """
        if not self._pending_windows:
            return
        sr = self._sample_rate
        skip = skip_samples(sr)
        limit = next_pos - guard_samples(sr)
        floor = min_window_samples(sr)
        kept = []
        for entry in self._pending_windows:
            start = int(entry[1]) + skip
            entry[2] = min(int(entry[2]), limit - start)
            if entry[2] >= floor:
                kept.append(entry)
        self._pending_windows = kept

    def _emit_ready_windows(self) -> None:
        """Publish verification windows whose audio has fully arrived."""
        if not self._pending_windows:
            return
        sr = self._sample_rate
        skip = skip_samples(sr)
        still_waiting = []
        for entry in self._pending_windows:
            t_ms, pos, length = entry[0], int(entry[1]), int(entry[2])
            if self._ring.written < pos + skip + length:
                still_waiting.append(entry)
                continue
            audio = self._ring.read(pos + skip, length)
            if audio is not None:
                # Bounded: capture also runs with no matcher consuming these
                # (tuner, calibration), and an unread queue must not grow.
                while self.strike_queue.qsize() >= MAX_QUEUED_WINDOWS:
                    try:
                        self.strike_queue.get_nowait()
                    except queue.Empty:
                        break
                self.strike_queue.put(StrikeWindow(t_ms, pos, audio, sr))
            # dropped otherwise: the ring wrapped before we got to it
        self._pending_windows = still_waiting

    def _resolve_input_settings(self) -> tuple[int, int]:
        """Find a (samplerate, channels) combination the input device accepts.

        USB interfaces (e.g. Focusrite) often run at 48000 Hz in Windows shared
        mode and reject the 44100 Hz default with "Invalid sample rate". Probe
        the configured rate first, then the device default, then common rates;
        for each rate try mono first, then stereo (callback uses channel 0).
        """
        ac = self.config.audio
        if self._resolved_settings is not None and self._resolved_device == ac.device_index:
            return self._resolved_settings

        candidates = [int(ac.sample_rate)]
        try:
            info = sd.query_devices(ac.device_index, "input")
            default_sr = int(info["default_samplerate"])
            if default_sr not in candidates:
                candidates.append(default_sr)
        except Exception:
            pass
        for sr in (48000, 44100, 96000, 88200, 32000, 22050):
            if sr not in candidates:
                candidates.append(sr)

        resolved = (int(ac.sample_rate), 1)
        for sr in candidates:
            found = False
            for ch in (1, 2):
                try:
                    sd.check_input_settings(
                        device=ac.device_index, channels=ch,
                        samplerate=sr, dtype="float32",
                    )
                    resolved = (sr, ch)
                    found = True
                    break
                except Exception:
                    continue
            if found:
                break

        self._resolved_settings = resolved
        self._resolved_device = ac.device_index
        return resolved

    def start(self):
        """Start audio capture."""
        ac = self.config.audio
        sample_rate, channels = self._resolve_input_settings()
        # aubio detectors must run at the stream's actual rate or every
        # detected frequency would be scaled by the rate mismatch
        self.detector.sample_rate = sample_rate
        self.detector.reset()
        self._onset_collector.reset()

        # The ring is indexed in samples, so it must match the resolved rate
        self._sample_rate = sample_rate
        self._ring = _AudioRing(int(sample_rate * RING_SECONDS))
        self._pending_windows = []
        self.dropped_buffers = 0

        # Drain any leftover notes
        while not self.note_queue.empty():
            try:
                self.note_queue.get_nowait()
            except queue.Empty:
                break
        while not self.strike_queue.empty():
            try:
                self.strike_queue.get_nowait()
            except queue.Empty:
                break

        # Sample counter restarts with the stream, so timestamps do too
        self._stream = sd.InputStream(
            device=ac.device_index,
            channels=channels,
            samplerate=sample_rate,
            blocksize=ac.hop_size,
            dtype="float32",
            callback=self._audio_callback,
        )
        self._stream.start()

    def stop(self):
        """Stop audio capture."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def set_noise_gate_db(self, db: float) -> None:
        """Update the noise gate threshold on the detector.

        Thread-safe: single float attribute write is atomic under the GIL.
        """
        self.detector.set_noise_gate_db(db)

    def get_signal_db(self) -> float:
        """Return the latest signal level in dB. Thread-safe (single float read under GIL)."""
        return self._signal_db

    def get_tuner_data(self) -> tuple[float, float]:
        """Return (frequency_hz, confidence) for tuner display. Thread-safe."""
        return (self._tuner_freq, self._tuner_confidence)

    def get_notes(self) -> list[TimestampedNote]:
        """Drain all pending detected notes from the queue (non-blocking)."""
        notes = []
        while True:
            try:
                notes.append(self.note_queue.get_nowait())
            except queue.Empty:
                break
        return notes

    def get_strike_windows(self) -> list[StrikeWindow]:
        """Drain raw audio windows for chord verification (non-blocking)."""
        windows = []
        while True:
            try:
                windows.append(self.strike_queue.get_nowait())
            except queue.Empty:
                break
        return windows


def list_audio_devices() -> list[dict]:
    """List available audio input devices."""
    devices = sd.query_devices()
    inputs = []
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            inputs.append({
                "index": i,
                "name": dev["name"],
                "channels": dev["max_input_channels"],
                "sample_rate": dev["default_samplerate"],
            })
    return inputs


def validate_device_index(index: int | None) -> bool:
    """Check if a device index exists and has input channels.

    Returns True for None (system default) or a valid input device index.
    """
    if index is None:
        return True
    try:
        info = sd.query_devices(index)
        return info["max_input_channels"] > 0
    except (sd.PortAudioError, IndexError, ValueError):
        return False


def run_console_demo():
    """Interactive console demo for testing pitch detection.

    Lists audio devices, lets user pick one, then prints detected notes in real-time.
    """
    print("Available audio input devices:")
    print("-" * 50)
    devices = list_audio_devices()
    if not devices:
        print("No audio input devices found!")
        return

    for dev in devices:
        marker = " *" if dev["index"] == sd.default.device[0] else ""
        print(f"  [{dev['index']}] {dev['name']} ({dev['channels']}ch, {dev['sample_rate']:.0f}Hz){marker}")
    print()

    choice = input("Select device index (Enter for default): ").strip()
    config = Config()
    if choice:
        try:
            config.audio.device_index = int(choice)
        except ValueError:
            print("Invalid input, using default device.")

    print()
    print("Listening... play some notes! (Ctrl+C to stop)")
    print(f"  Config: buf={config.audio.buf_size}, hop={config.audio.hop_size}, "
          f"confidence>={config.audio.confidence_threshold}, noise_gate={config.audio.noise_gate_db}dB")
    print("-" * 60)
    print(f"{'Time':>8}  {'Note':>5}  {'MIDI':>4}  {'Freq':>8}  {'Conf':>5}  {'Onset'}")
    print("-" * 60)

    capture = AudioCapture(config)
    capture.start()

    last_note = ""
    try:
        while True:
            notes = capture.get_notes()
            for tn in notes:
                n = tn.note
                # Only print on onset or note change to reduce spam
                current = n.name
                if n.is_onset or current != last_note:
                    onset_marker = ">>>" if n.is_onset else "   "
                    print(f"{tn.timestamp_ms:7.0f}ms  {n.name:>5}  {n.midi_note:>4}  "
                          f"{n.frequency:7.1f}Hz  {n.confidence:.2f}  {onset_marker}")
                    last_note = current
            time.sleep(0.01)  # ~100Hz polling, avoid busy-wait
    finally:
        capture.stop()
