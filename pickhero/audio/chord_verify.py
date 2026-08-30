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

The window is as long as the playing allows. 341 ms is what a semitone needs
to separate cleanly, but a chord struck sooner than that cuts it short, and a
window running into the NEXT chord contains pitches the tab never expected
there -- which convicts strings that were played correctly. So the caller
truncates at the following strike, and two things keep the short window
honest: the analysis floor rises as it shortens, and the intruder tier is
withheld, having been fitted at the full length. Below MIN_WINDOW_MS the
chord gets no verdict at all.

Thresholds were fitted on reference_recordings/20260814_160019 (clean DI,
Focusrite, 48 kHz): 7/7 deliberate one-fret errors caught, 0 false alarms
over 33 confidently judged strings, and 0 false alarms at every window length
down to 190 ms. See tools/analyze_reference.py and tools/sweep_chord_window.py.

Pure numpy: no aubio, no pygame, so it stays testable without audio hardware.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from collections.abc import Sequence

from pickhero.audio.note_utils import midi_to_freq

# Analysis window. 341 ms is long enough that a semitone separates cleanly at
# the upper partials, which is where the decision is actually made -- at the
# fundamental a semitone is only ~12 Hz and blurs into one peak.
WINDOW_MS = 341.0
# Let the attack transient pass first. Longer skips lose palm mutes, which
# have decayed substantially by 120 ms.
SKIP_MS = 40.0

# Shortest window still worth judging, from tools/sweep_chord_window.py over
# the reference takes: no false alarm anywhere from 190 ms up, the first one at
# 180 ms (a palm-muted power chord, which by then is mostly decay). 200 ms
# keeps two steps of that margin. Under this floor a chord gets NO verdict
# rather than a guess -- the same presumption of innocence that protects a
# masked string protects a rushed one. It means chords struck less than
# ~255 ms apart (skip + window + guard) are not judged, which is eighth notes
# past about 118 BPM.
#
# This was 280 ms, which was fitted when the analysis floor was a fixed 150 Hz
# and short windows really did lie. MIN_HZ_SECONDS replaced that with a floor
# that RISES as the window shortens, which is what made shorter windows honest
# -- but nobody lowered this constant to collect the winnings, and the sweep
# could not report it either: it gated on this value and printed "below floor"
# with nothing judged, so the evidence that 280 was stale never appeared. The
# sweep now runs below the floor for exactly that reason.
MIN_WINDOW_MS = 200.0
# Pulled back from the next strike so its attack transient stays out of the
# window. One hop at 48 kHz is ~11 ms; 15 ms covers the detector's grid.
NEXT_STRIKE_GUARD_MS = 15.0

# Half-width of the search window around a partial. Wide enough to absorb a
# guitar tuned ~20 cents flat plus string inharmonicity, narrow enough not to
# reach the neighbouring semitone 100 cents away.
CENTS_WIN = 45.0
# Two partials closer than this count as shared, so neither identifies a note.
SEP_CENTS = 60.0
N_HARM = 14
# Partials below MIN_HZ_SECONDS / window_seconds are too coarsely resolved to
# tell a semitone apart, so the analysis starts above that -- and the floor
# rises as the window gets shorter. Derived and then confirmed: a partial's
# Hann main lobe is ~2/T Hz wide and has to stay outside the +-45 cent band
# around the neighbouring semitone, which needs f*T > ~61.
# tools/sweep_chord_window.py agrees -- at 61 the reference takes give 0 false
# alarms at every window length from 280 ms up, and the full window behaves
# exactly as originally calibrated (33 strings judged, 7/7 errors caught). The
# fixed 150 Hz this replaced was right at 341 ms but lies at 320 and 300.
MIN_HZ_SECONDS = 61.0

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


def min_window_samples(sample_rate: int) -> int:
    """Shortest window that still earns a verdict."""
    return int(round(MIN_WINDOW_MS / 1000.0 * sample_rate))


def skip_samples(sample_rate: int) -> int:
    return int(round(SKIP_MS / 1000.0 * sample_rate))


def guard_samples(sample_rate: int) -> int:
    return int(round(NEXT_STRIKE_GUARD_MS / 1000.0 * sample_rate))


def samples_needed(sample_rate: int) -> int:
    """Audio required after a strike before it can be verified."""
    return skip_samples(sample_rate) + window_samples(sample_rate)


def min_hz_for(window_len: int, sample_rate: int) -> float:
    """Lowest partial frequency this window length can still resolve.

    A short window smears neighbouring semitones into one peak at low
    frequencies, so the partials that carry the decision have to be taken
    from higher up the series. Returning a higher floor is what keeps a
    truncated window honest instead of merely faster.
    """
    seconds = window_len / float(sample_rate) if sample_rate > 0 else 0.0
    if seconds <= 0:
        return float("inf")
    return MIN_HZ_SECONDS / seconds


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
        cand: int, others: list[int], sample_rate: int, min_hz: float,
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
            if fh < min_hz:
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
        if len(expected) < 2:
            return {}
        # Too short to separate a semitone anywhere useful. The caller hands
        # over whatever fits before the next strike, so this is the case of
        # chords played faster than they can be told apart -- no verdict.
        if len(audio) < min_window_samples(sample_rate):
            return {}
        min_hz = min_hz_for(len(audio), sample_rate)
        # The intruder tier convicts a string whose expected note cannot be
        # confirmed at all, on the strength of what it hears instead. That is
        # the thinnest evidence the verifier ever acts on, and it was fitted
        # at the full window; on a shortened one the raised floor leaves it
        # only a couple of partials to work from, and it was the sole source
        # of false alarms in the length sweep. Truncated windows get the
        # direct tier only.
        allow_intruder = len(audio) >= window_samples(sample_rate)

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
                s = self._score(
                    freqs, mags, peak, cand, others, sample_rate, min_hz
                )
                if s is not None:
                    scores[cand] = s
            verdicts[target] = self._decide(target, scores, allow_intruder)
        return verdicts

    def confirms(
        self, audio: np.ndarray, sample_rate: int, midi_note: int,
        sounding: Sequence[int] = (),
    ) -> bool:
        """Is this ONE written note actually present in the audio?

        A different question from `verify`, and the reason it needs its own
        method. `verify` asks which of several expected notes each string
        played, and can convict; this only ever ACQUITS. It exists for the
        strike that arrives carrying no pitch at all, where there is nothing
        wrong to correct and nothing to credit either -- measured on a line
        played across the strings without damping, where a fast run loses 24
        points of usable strikes to exactly that.

        No intruder tier: with one expected note there is no chord for it to
        be masked by, so "something else is louder" says only that another
        string is still ringing, which is the premise rather than evidence.
        Only a direct confirmation counts.

        `sounding` is what the TAB says is still ringing on the other strings.
        Without it every rival hypothesis a semitone or two away may claim any
        partial in its bands, including the partials of the neighbours -- and
        in an arpeggio the neighbours are loud, so the margin between the
        written note and its rivals collapses even where the written note is
        plainly the strongest thing there. Measured on the player's own take:
        of 16 refusals, 14 had the written note winning at -0.7 to -10 dB and
        failing on the margin alone. A partial identifies a note only if no
        other sounding string produces it, which is the rule `verify` already
        applies to the tones of a chord.
        """
        if len(audio) < min_window_samples(sample_rate):
            return False
        window = np.asarray(audio, dtype=np.float64)
        window = window * np.hanning(len(window))
        mags = np.abs(np.fft.rfft(window, n=len(window) * 2))
        freqs = np.fft.rfftfreq(len(window) * 2, 1.0 / sample_rate)
        peak = float(mags.max()) + 1e-12
        min_hz = min_hz_for(len(audio), sample_rate)
        scores: dict[int, float] = {}
        others = [m for m in dict.fromkeys(sounding) if m != midi_note]
        for cand in range(midi_note - self.span, midi_note + self.span + 1):
            score = self._score(freqs, mags, peak, cand,
                                [m for m in others if m != cand],
                                sample_rate, min_hz)
            if score is not None:
                scores[cand] = score
        verdict = self._decide(midi_note, scores, allow_intruder=False)
        return verdict.correct

    def _decide(
        self, target: int, scores: dict[int, float], allow_intruder: bool = True,
    ) -> StringVerdict:
        if not scores:
            return StringVerdict(target, None, -120.0, 0.0, "")
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best_midi, best_db = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else -120.0
        margin = best_db - runner_up

        if target not in scores:
            # Expected note is masked by an octave/fifth already sounding: it
            # can never be confirmed, but an intruder still shows up loudly.
            if (allow_intruder and best_midi != target
                    and best_db > self.intruder_db and margin > self.margin_db):
                return StringVerdict(target, best_midi, best_db, margin, "intruder")
            return StringVerdict(target, None, best_db, margin, "")

        if best_db <= self.present_db or margin <= self.margin_db:
            return StringVerdict(target, None, best_db, margin, "")
        return StringVerdict(target, best_midi, best_db, margin, "direct")
