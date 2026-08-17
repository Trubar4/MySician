"""Pitch and onset detection using aubio.

Wraps aubio's YIN pitch detector and onset detector.
Processes audio buffers and returns detected notes.
"""

from dataclasses import dataclass

import aubio
import numpy as np

from pickhero.audio.note_utils import (
    GUITAR_MIDI_MIN, freq_to_midi, midi_to_name, is_in_guitar_range,
)


@dataclass
class DetectedNote:
    """A single detected pitch event."""
    midi_note: int
    frequency: float
    confidence: float
    name: str
    is_onset: bool  # True if a new note strike was detected
    # True if the pitch was octave-folded up from below the guitar range.
    # A subharmonic (e.g. 41 Hz from an E5 power chord) can only be produced
    # by multiple strings sounding at once — it is evidence of a strummed
    # chord, not a single note.
    subharmonic: bool = False
    # True for a strike loud enough to be real that never produced a pitch at
    # all: a dead note, or one choked so hard YIN finds no period. midi_note
    # and frequency carry nothing in that case — the tab is the only thing
    # that can say whether such a strike was what the music asked for.
    unpitched: bool = False


class PitchDetector:
    """Real-time pitch and onset detection for guitar audio.

    Processes audio buffers (hop_size samples each) and returns
    detected notes with confidence values.
    """

    def __init__(
        self,
        buf_size: int = 4096,
        hop_size: int = 512,
        sample_rate: int = 44100,
        confidence_threshold: float = 0.8,
        onset_threshold: float = 0.3,
        noise_gate_db: float = -60.0,
        yin_tolerance: float = 0.15,
        calibration: dict | None = None,
    ):
        self.buf_size = buf_size
        self.hop_size = hop_size
        self.sample_rate = sample_rate
        self.confidence_threshold = confidence_threshold
        self.onset_threshold = onset_threshold
        self.noise_gate_db = noise_gate_db
        self.yin_tolerance = yin_tolerance
        self.last_signal_db: float = -120.0
        self.last_freq: float = 0.0
        self.last_confidence: float = 0.0
        self.last_is_onset: bool = False

        # Octave jump protection
        self._prev_freq: float = 0.0
        self._calibration = calibration

        # Pitch detector — yinfast is the YIN algorithm computed via FFT,
        # cheap enough for a 4096 window (covers ~7.6 periods of low E at
        # 82 Hz; 2048 covers only ~3.8 and octave-errors on bass strings).
        # yin_tolerance is the YIN dip threshold (aubio default 0.15) and
        # must NOT be confused with the confidence filter below: a high
        # tolerance makes YIN accept the first weak dip, i.e. harmonics
        # instead of the fundamental.
        self._pitch = aubio.pitch("yinfast", buf_size, hop_size, sample_rate)
        self._pitch.set_unit("Hz")
        self._pitch.set_tolerance(yin_tolerance)

        # Onset detector — keep a short 2048 window for strike-timing
        # precision regardless of the pitch window size
        self._onset_buf_size = min(buf_size, 2048)
        self._onset = aubio.onset("default", self._onset_buf_size, hop_size, sample_rate)
        self._onset.set_threshold(onset_threshold)

    def process(self, audio_buffer: np.ndarray) -> DetectedNote | None:
        """Process a single audio buffer (hop_size float32 samples).

        Args:
            audio_buffer: 1D numpy array of float32 samples, length == hop_size.

        Returns:
            DetectedNote if a confident pitch was detected, None otherwise.
        """
        # Ensure correct format for aubio
        if audio_buffer.dtype != np.float32:
            audio_buffer = audio_buffer.astype(np.float32)

        # Check noise gate (RMS level)
        rms = np.sqrt(np.mean(audio_buffer ** 2))
        if rms > 0:
            db = 20 * np.log10(rms)
        else:
            db = -120.0

        self.last_signal_db = db

        if db < self.noise_gate_db:
            self.last_is_onset = False
            return None

        # Detect pitch
        freq = float(self._pitch(audio_buffer)[0])
        confidence = float(self._pitch.get_confidence())

        # Correct octave jumps before exposing values
        if freq > 0:
            freq = self._correct_octave_jump(freq, confidence)

        # Store values for tuner (after octave correction)
        self.last_freq = freq
        self.last_confidence = confidence

        # Detect onset — exposed via last_is_onset even when the confidence
        # filter below rejects this frame, because the attack transient of a
        # strike often fails the filter while the onset itself is real
        is_onset = bool(self._onset(audio_buffer))
        self.last_is_onset = is_onset

        # Filter: need minimum confidence and valid frequency
        if confidence < self.confidence_threshold or freq <= 0:
            return None

        midi_note = freq_to_midi(freq)
        if not is_in_guitar_range(midi_note):
            return None

        return DetectedNote(
            midi_note=midi_note,
            frequency=freq,
            confidence=confidence,
            name=midi_to_name(midi_note),
            is_onset=is_onset,
        )

    def _correct_octave_jump(self, freq: float, confidence: float) -> float:
        """Suppress octave jumps caused by harmonic detection.

        If the new frequency is ~2x or ~0.5x the previous, and confidence
        isn't very high, prefer the previous frequency (likely the fundamental).
        When calibration data is available, also check if freq/2 matches a
        known open-string fundamental.
        """
        corrected = freq

        # Calibration-based correction: if freq/2 is near a calibrated string,
        # prefer freq/2 (the fundamental was likely the intended note).
        # Only when confidence is shaky — a confident detection one octave
        # above an open string is usually a real fretted note (e.g. E3),
        # not a harmonic of the open string.
        if self._calibration and freq > 0 and confidence < 0.9:
            cal_strings = self._calibration.get("strings", {})
            half_freq = freq / 2.0
            for cal in cal_strings.values():
                cal_freq = cal.get("frequency", 0)
                if cal_freq > 0:
                    ratio = half_freq / cal_freq
                    # Within ±1 semitone of a calibrated fundamental
                    if 0.944 < ratio < 1.06:
                        corrected = half_freq
                        self._prev_freq = corrected
                        return corrected

        # Generic ratio-based correction — but never rewrite frequencies
        # below the guitar range: those are chord subharmonics (multiple
        # strings sounding), which the onset collector folds and flags
        if freq_to_midi(freq) < GUITAR_MIDI_MIN:
            self._prev_freq = freq
            return freq

        if self._prev_freq > 0 and confidence < 0.95:
            ratio = freq / self._prev_freq
            if 1.95 <= ratio <= 2.05:
                # One octave up — prefer previous (fundamental)
                corrected = self._prev_freq
            elif 0.48 <= ratio <= 0.52:
                # One octave down — prefer previous
                corrected = self._prev_freq

        self._prev_freq = corrected
        return corrected

    def set_noise_gate_db(self, db: float) -> None:
        """Update the noise gate threshold (dB). Takes effect on next process() call."""
        self.noise_gate_db = db

    def reset(self):
        """Reset detector state. Call when starting a new song/session."""
        self._prev_freq = 0.0

        # Re-create detectors to clear internal state
        self._pitch = aubio.pitch(
            "yinfast", self.buf_size, self.hop_size, self.sample_rate
        )
        self._pitch.set_unit("Hz")
        self._pitch.set_tolerance(self.yin_tolerance)

        self._onset_buf_size = min(self.buf_size, 2048)
        self._onset = aubio.onset(
            "default", self._onset_buf_size, self.hop_size, self.sample_rate
        )
        self._onset.set_threshold(self.onset_threshold)
