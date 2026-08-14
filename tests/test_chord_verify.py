"""Tests for per-string chord verification.

Uses synthesised guitar tones (harmonic series with decay and inharmonicity)
rather than pure sine waves: the verification decides on upper partials, and
a sine wave has none, so sines would test nothing.
"""

import numpy as np
import pytest

from pickhero.audio.chord_verify import (
    ChordVerifier, StringVerdict, samples_needed, skip_samples, window_samples,
)
from pickhero.audio.note_utils import midi_to_freq

SR = 48000

E2, A2, B2, C3, D3, E3, Gs3, B3, E4 = 40, 45, 47, 48, 50, 52, 56, 59, 64


def pluck(midi, duration_s=0.5, sr=SR, seed=0, n_harm=14):
    """One plucked steel string: decaying harmonics with slight inharmonicity."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(duration_s * sr)) / sr
    f0 = midi_to_freq(midi)
    sig = np.zeros_like(t)
    for h in range(1, n_harm + 1):
        fh = f0 * h * np.sqrt(1 + 8e-5 * h * h)
        if fh > sr * 0.45:
            break
        sig += (1.0 / h ** 0.9) * np.exp(-t * (1.2 + 0.3 * h)) * np.sin(
            2 * np.pi * fh * t + rng.uniform(0, 2 * np.pi)
        )
    return sig


def chord(midis, duration_s=0.5, sr=SR):
    mix = np.zeros(int(duration_s * sr))
    for i, m in enumerate(midis):
        mix += pluck(m, duration_s, sr, seed=i)
    return mix / (np.abs(mix).max() + 1e-9)


def window(midis, sr=SR):
    """A verification-sized window of a struck chord."""
    return chord(midis, duration_s=WINDOW_S, sr=sr)[:window_samples(sr)]


WINDOW_S = 0.5


class TestWindowSizing:
    def test_window_is_long_enough_to_resolve_a_semitone(self):
        # A semitone at the 5th partial of the low E is ~30 Hz apart; the
        # window must be longer than 1/30 s for those to be separate peaks.
        assert window_samples(SR) / SR > 1 / 30

    def test_samples_needed_covers_skip_and_window(self):
        assert samples_needed(SR) == skip_samples(SR) + window_samples(SR)

    def test_scales_with_sample_rate(self):
        assert window_samples(44100) < window_samples(48000)


class TestVerdict:
    def test_correct_when_played_matches_expected(self):
        v = StringVerdict(E2, E2, -10.0, 20.0, "direct")
        assert v.decided and v.correct and not v.wrong

    def test_wrong_only_on_positive_evidence(self):
        assert StringVerdict(E2, C3, -10.0, 20.0, "direct").wrong

    def test_undecided_is_neither_correct_nor_wrong(self):
        v = StringVerdict(E2, None, -50.0, 1.0, "")
        assert not v.decided and not v.correct and not v.wrong


class TestPowerChords:
    def test_correct_fifth_is_confirmed(self):
        verifier = ChordVerifier()
        verdicts = verifier.verify(window([E2, B2]), SR, [E2, B2])
        assert verdicts[B2].correct

    def test_fifth_one_fret_sharp_is_caught(self):
        verifier = ChordVerifier()
        # played C3 where the tab expects B2
        verdicts = verifier.verify(window([E2, C3]), SR, [E2, B2])
        assert verdicts[B2].wrong
        assert verdicts[B2].played_midi == C3

    def test_fifth_one_fret_flat_is_caught(self):
        verifier = ChordVerifier()
        verdicts = verifier.verify(window([E2, 46]), SR, [E2, B2])
        assert verdicts[B2].wrong
        assert verdicts[B2].played_midi == 46

    def test_missing_fifth_yields_no_verdict(self):
        """Absence of evidence must not become evidence of a wrong note."""
        verifier = ChordVerifier()
        verdicts = verifier.verify(window([E2]), SR, [E2, B2])
        assert not verdicts[B2].wrong

    def test_root_is_confirmed_too(self):
        verifier = ChordVerifier()
        verdicts = verifier.verify(window([E2, B2]), SR, [E2, B2])
        assert verdicts[E2].correct


class TestFullChords:
    def test_third_of_e_major_is_confirmed(self):
        verifier = ChordVerifier()
        emaj = [E2, B2, E3, Gs3, B3, E4]
        verdicts = verifier.verify(window(emaj), SR, emaj)
        assert verdicts[Gs3].correct

    def test_third_played_a_fret_flat_is_caught(self):
        """The case that decides major vs minor -- G instead of G#."""
        verifier = ChordVerifier()
        emaj = [E2, B2, E3, Gs3, B3, E4]
        played = [E2, B2, E3, 55, B3, E4]
        verdicts = verifier.verify(window(played), SR, emaj)
        assert verdicts[Gs3].wrong
        assert verdicts[Gs3].played_midi == 55

    def test_octave_masked_string_gets_no_verdict_when_correct(self):
        """E3 is an octave of E2: its partials are a strict subset, so it can
        never be confirmed. It must not be called wrong either."""
        verifier = ChordVerifier()
        emaj = [E2, B2, E3, Gs3, B3, E4]
        verdicts = verifier.verify(window(emaj), SR, emaj)
        assert not verdicts[E3].wrong

    def test_no_false_alarm_on_a_correctly_played_chord(self):
        verifier = ChordVerifier()
        for shape in ([E2, B2, E3, Gs3, B3, E4], [A2, E3, 57, 60, E4]):
            verdicts = verifier.verify(window(shape), SR, shape)
            wrong = [m for m, v in verdicts.items() if v.wrong]
            assert wrong == [], f"false alarm on {shape}: {wrong}"


class TestGuards:
    def test_single_note_is_not_a_chord(self):
        assert ChordVerifier().verify(window([E2]), SR, [E2]) == {}

    def test_too_short_audio_returns_nothing(self):
        assert ChordVerifier().verify(np.zeros(64), SR, [E2, B2]) == {}

    def test_silence_produces_no_wrong_verdicts(self):
        verifier = ChordVerifier()
        verdicts = verifier.verify(np.zeros(window_samples(SR)), SR, [E2, B2])
        assert not any(v.wrong for v in verdicts.values())

    def test_duplicate_expected_notes_are_collapsed(self):
        verifier = ChordVerifier()
        verdicts = verifier.verify(window([E2, B2]), SR, [E2, B2, B2])
        assert sorted(verdicts) == [E2, B2]
