"""Per-string chord verification.

Answers, for one struck chord whose expected notes are known from the tab,
not just "did this string sound" but "did it sound on the right fret".

Method: for each expected note, score competing pitch hypotheses (the note
itself and its neighbours) using only the partials that no OTHER expected
note of the chord can produce, measured in dB below the frame peak. A string
fretted one semitone off produces partials nothing else explains, and stands
out by 20 dB or more.

Presumption of innocence: a string is only called wrong on positive evidence
of a wrong pitch, never on absence of evidence for the right one. Two facts
force this. A string whose expected note is an octave or fifth of a lower
string in the same chord can never be confirmed -- its partials are a strict
subset of one already sounding. And judging such a string anyway means
ranking noise, which is exactly how the first calibration run produced false
alarms. Where the expected note is masked, a foreign pitch is still plainly
visible, so a second tier flags an intruder that clears a stricter level.

Thresholds were fitted on reference_recordings/20260814_160019 (clean DI,
Focusrite, 48 kHz): 7/7 deliberate one-fret errors caught, 0 false alarms
over 33 confidently judged strings. See tools/analyze_reference.py.

Pure numpy: no aubio, no pygame, so it stays testable without audio hardware.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pickhero.audio.note_utils import midi_to_freq

# Analysis window. 341 ms is long enough that a semitone separates cleanly at
# the upper partials, which is where the decision is actually made -- at the
# fundamental a semitone is only ~12 Hz and blurs into one peak.
WINDOW_MS = 341.0
# Let the attack transient pass first. Longer skips lose palm mutes, which
# have decayed substantially by 120 ms.
SKIP_MS = 40.0

# Half-width of the search window around a partial. Wide enough to absorb a
# guitar tuned ~20 cents flat plus string inharmonicity, narrow enough not to
# reach the neighbouring semitone 100 cents away.
CENTS_WIN = 45.0
# Two partials closer than this count as shared, so neither identifies a note.
SEP_CENTS = 60.0
N_HARM = 14
# Below this the FFT grid is too coarse for the partial window to be reliable.
MIN_HZ = 150.0

# Decision thresholds, all in dB below the frame's strongest peak.
PRESENT_DB = -32.0    # genuine detections landed at -3..-30, noise at -36..-39
MARGIN_DB = 8.0       # at 5 dB a correctly played low E was mis-called once
INTRUDER_DB = -25.0   # stricter bar for flagging a masked string as wrong

# How far either side of the expected note to test hypotheses.
SPAN_SEMITONES = 2


@dataclass
class StringVerdict:
    """What one string of a chord actually played."""
    expected_midi: int
    played_midi: int | None   # None when no fair verdict is possible
    level_db: float
    margin_db: float
    via: str                  # "direct", "intruder", or "" when undecided

    @property
    def decided(self) -> bool:
        return self.played_midi is not None

    @property
    def correct(self) -> bool:
        """True only on a positive verdict that the expected note sounded."""
        return self.played_midi == self.expected_midi

    @property
    def wrong(self) -> bool:
        """True only on positive evidence of a different pitch."""
        return self.decided and self.played_midi != self.expected_midi


def window_samples(sample_rate: int) -> int:
    return int(round(WINDOW_MS / 1000.0 * sample_rate))


def skip_samples(sample_rate: int) -> int:
    return int(round(SKIP_MS / 1000.0 * sample_rate))


def samples_needed(sample_rate: int) -> int:
    """Audio required after a strike before it can be verified."""
    return skip_samples(sample_rate) + window_samples(sample_rate)


class ChordVerifier:
    """Scores which pitch each string of an expected chord actually played."""

    def __init__(
        self,
        present_db: float = PRESENT_DB,
        margin_db: float = MARGIN_DB,
        intruder_db: float = INTRUDER_DB,
        span: int = SPAN_SEMITONES,
    ):
        self.present_db = present_db
        self.margin_db = margin_db
        self.intruder_db = intruder_db
        self.span = span
        # Partial frequencies are fixed per MIDI note; cache them.
        self._partials: dict[tuple[int, int], list[float]] = {}

    def _partial_freqs(self, midi: int, sample_rate: int, n_harm: int) -> list[float]:
        key = (midi, n_harm)
        cached = self._partials.get(key)
        if cached is None:
            f0 = midi_to_freq(midi)
            cached = [f0 * h for h in range(1, n_harm + 1)]
            self._partials[key] = cached
        nyq = sample_rate * 0.45
        return [f for f in cached if f < nyq]

    def _score(
        self, freqs: np.ndarray, mags: np.ndarray, peak: float,
        cand: int, others: list[int], sample_rate: int,
    ) -> float | None:
        """Evidence for `cand` in dB below the frame peak.

        None means every partial of `cand` is shared with another expected
        note, so its presence is undecidable from this spectrum alone.
        """
        # Distortion and string coupling extend harmonic series far up, so a
        # partial only counts as this note's own if no other expected note
        # produces it anywhere below Nyquist.
        theirs = [
            pf for o in others for pf in self._partial_freqs(o, sample_rate, 60)
        ]
        vals = []
        for fh in self._partial_freqs(cand, sample_rate, N_HARM):
            if fh < MIN_HZ:
                continue
            if any(abs(1200 * np.log2(fh / tf)) < SEP_CENTS for tf in theirs):
                continue
            lo = fh * 2 ** (-CENTS_WIN / 1200)
            hi = fh * 2 ** (CENTS_WIN / 1200)
            idx = np.searchsorted(freqs, (lo, hi))
            band = mags[idx[0]:max(idx[1], idx[0] + 1)]
            amp = float(band.max()) if band.size else 0.0
            vals.append(20 * np.log10(amp / peak + 1e-12))
        if not vals:
            return None
        vals.sort(reverse=True)
        best = vals[:3]
        return float(sum(best) / len(best))

    def verify(
        self, audio: np.ndarray, sample_rate: int, expected_midi: list[int],
    ) -> dict[int, StringVerdict]:
        """Judge each expected note of one struck chord.

        Args:
            audio: mono window starting shortly after the strike.
            sample_rate: samples per second of `audio`.
            expected_midi: MIDI notes the tab expects for this chord.

        Returns:
            {expected_midi: StringVerdict}, one entry per distinct expected note.
        """
        expected = sorted(set(expected_midi))
        if len(audio) < 1024 or len(expected) < 2:
            return {}

        window = np.asarray(audio, dtype=np.float64)
        window = window * np.hanning(len(window))
        mags = np.abs(np.fft.rfft(window, n=len(window) * 2))
        freqs = np.fft.rfftfreq(len(window) * 2, 1.0 / sample_rate)
        peak = float(mags.max()) + 1e-12

        verdicts: dict[int, StringVerdict] = {}
        for target in expected:
            others = [o for o in expected if o != target]
            scores: dict[int, float] = {}
            for cand in range(target - self.span, target + self.span + 1):
                s = self._score(freqs, mags, peak, cand, others, sample_rate)
                if s is not None:
                    scores[cand] = s
            verdicts[target] = self._decide(target, scores)
        return verdicts

    def _decide(self, target: int, scores: dict[int, float]) -> StringVerdict:
        if not scores:
            return StringVerdict(target, None, -120.0, 0.0, "")
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best_midi, best_db = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else -120.0
        margin = best_db - runner_up

        if target not in scores:
            # Expected note is masked by an octave/fifth already sounding: it
            # can never be confirmed, but an intruder still shows up loudly.
            if best_midi != target and best_db > self.intruder_db and margin > self.margin_db:
                return StringVerdict(target, best_midi, best_db, margin, "intruder")
            return StringVerdict(target, None, best_db, margin, "")

        if best_db <= self.present_db or margin <= self.margin_db:
            return StringVerdict(target, None, best_db, margin, "")
        return StringVerdict(target, best_midi, best_db, margin, "direct")
