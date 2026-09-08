"""Tests for per-string chord verification.

Uses synthesised guitar tones (harmonic series with decay and inharmonicity)
rather than pure sine waves: the verification decides on upper partials, and
a sine wave has none, so sines would test nothing.
"""

import numpy as np
import pytest

from pickhero.audio.chord_verify import (
    CONFIRM_MARGIN_DB, MIN_WINDOW_MS, ChordVerifier, StringVerdict,
    guard_samples, min_hz_for, min_window_samples, samples_needed,
    skip_samples, window_samples,
)
from pickhero.audio.note_utils import midi_to_freq

SR = 48000

E2, A2, B2, C3, D3, E3, Gs3, B3, E4 = 40, 45, 47, 48, 50, 52, 56, 59, 64


def pluck(midi, duration_s=0.5, sr=SR, seed=0, n_harm=14):
    """One plucked steel string: decaying harmonics with slight inharmonicity.

    Two properties of a real string are load-bearing here, both measured off
    the reference recordings:

    Roll-off. A DI'd electric holds its partials within ~13 dB of the peak up
    to the twelfth, and the verification decides on exactly those upper
    partials. A tone fading to nothing by the fourth harmonic would make
    these tests pass or fail on a signal no guitar produces.

    Unevenness. Plucking at a point along the string nulls every harmonic
    with a node there, so real partial levels jump around by tens of dB. A
    perfectly smooth series is what lets a WHOLE-TONE neighbour score almost
    as well as the true note -- 9:8 means every ninth partial coincides --
    and that near-tie is an artefact of the synthesis, not of the method.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(int(duration_s * sr)) / sr
    f0 = midi_to_freq(midi)
    pluck_point = 0.19 + 0.03 * (seed % 4)      # fraction along the string
    sig = np.zeros_like(t)
    for h in range(1, n_harm + 1):
        fh = f0 * h * np.sqrt(1 + 8e-5 * h * h)
        if fh > sr * 0.45:
            break
        comb = abs(np.sin(np.pi * h * pluck_point))
        sig += comb * (1.0 / h ** 0.35) * np.exp(-t * (1.0 + 0.15 * h)) * np.sin(
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

    def test_floor_is_below_the_full_window(self):
        assert 0 < min_window_samples(SR) < window_samples(SR)

    def test_guard_is_short_next_to_the_skip(self):
        # Only has to keep the next attack out, not the note after it.
        assert 0 < guard_samples(SR) < skip_samples(SR)

    def test_the_floor_is_where_the_sweep_said_it_could_be(self):
        """The floor decides how fast a chord may be played and still be
        checked, so it must track what the reference takes actually allow.

        It went stale once: fitted at 280 ms when the analysis floor was a
        fixed 150 Hz, then left there after MIN_HZ_SECONDS made shorter
        windows honest. tools/sweep_chord_window.py finds no false alarm from
        190 ms up and the first at 180 ms, so 200 ms keeps two steps of
        margin -- and anything at or above 190 would be defensible. A value
        below that is not, and one far above it is leaving verdicts unclaimed.
        """
        assert 190.0 <= MIN_WINDOW_MS <= 240.0

    def test_chords_an_eighth_apart_can_still_be_judged(self):
        """The point of the floor, stated as the music it allows.

        Eighth notes at 110 BPM are 273 ms apart, which is ordinary rhythm
        playing rather than a metal tempo -- the previous floor refused
        anything faster than about 90 BPM.
        """
        needed_ms = 1000.0 * (skip_samples(SR) + min_window_samples(SR)
                              + guard_samples(SR)) / SR
        eighth_at_110bpm = 30_000.0 / 110.0
        assert needed_ms < eighth_at_110bpm


class TestAnalysisFloor:
    """A shortened window must judge on higher partials, not the same ones."""

    def test_shorter_window_raises_the_floor(self):
        full = min_hz_for(window_samples(SR), SR)
        half = min_hz_for(window_samples(SR) // 2, SR)
        assert half > full

    def test_floor_stays_finite_at_the_shortest_usable_window(self):
        assert min_hz_for(min_window_samples(SR), SR) < 1000.0

    def test_empty_window_resolves_nothing(self):
        assert min_hz_for(0, SR) == float("inf")


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

    def test_root_of_a_bare_power_chord_is_never_called_wrong(self):
        """The root is the hardest string in a two-note power chord.

        A fifth swallows the root's 3rd, 6th, 9th and 12th partials, and the
        analysis floor rules out the 1st and 2nd, so what is left to identify
        it by is thin -- thin enough that the whole-tone neighbours, which
        share every ninth partial with it, come close. Confirming it is
        therefore not guaranteed; not convicting it is.
        """
        verifier = ChordVerifier()
        verdicts = verifier.verify(window([E2, B2]), SR, [E2, B2])
        assert not verdicts[E2].wrong

    def test_root_is_confirmed_when_the_chord_leaves_it_partials(self):
        verifier = ChordVerifier()
        # a seventh rather than a fifth: the two series barely overlap
        verdicts = verifier.verify(window([E2, D3]), SR, [E2, D3])
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


class TestIntruderTier:
    """Convicting a string whose expected note cannot be confirmed at all is
    the thinnest evidence the verifier ever acts on. It was fitted at the full
    window, and on a shortened one it was the only source of false alarms in
    tools/sweep_chord_window.py -- so it does not travel."""

    # G#3 masked by E2's fifth partial, with a loud G3 sitting where it is not
    scores = {55: -12.0, 54: -40.0}

    def test_full_window_may_flag_an_intruder(self):
        verdict = ChordVerifier()._decide(Gs3, dict(self.scores), True)
        assert verdict.wrong and verdict.via == "intruder"

    def test_truncated_window_may_not(self):
        verdict = ChordVerifier()._decide(Gs3, dict(self.scores), False)
        assert not verdict.decided

    def test_direct_verdicts_are_unaffected(self):
        # target itself scored, so this is the direct tier either way
        scores = {Gs3: -50.0, 55: -10.0}
        for allow in (True, False):
            verdict = ChordVerifier()._decide(Gs3, dict(scores), allow)
            assert verdict.wrong and verdict.via == "direct"


class TestGuards:
    def test_single_note_is_not_a_chord(self):
        assert ChordVerifier().verify(window([E2]), SR, [E2]) == {}

    def test_too_short_audio_returns_nothing(self):
        assert ChordVerifier().verify(np.zeros(64), SR, [E2, B2]) == {}

    def test_window_below_the_floor_gets_no_verdict_at_all(self):
        """Chords played faster than they can be told apart are not judged.

        The wrong note is plainly there in the audio, so this is not a
        detection failure -- it is the refusal to decide on a window too
        short to separate a semitone reliably.
        """
        verifier = ChordVerifier()
        short = chord([E2, C3], duration_s=WINDOW_S)[:min_window_samples(SR) - 1]
        assert verifier.verify(short, SR, [E2, B2]) == {}

    def test_just_above_the_floor_still_judges(self):
        verifier = ChordVerifier()
        cut = chord([E2, C3], duration_s=WINDOW_S)[:min_window_samples(SR)]
        verdicts = verifier.verify(cut, SR, [E2, B2])
        assert verdicts, "the floor must be usable, not merely defined"
        assert not verdicts[E2].wrong          # E2 was played correctly

    def test_silence_produces_no_wrong_verdicts(self):
        verifier = ChordVerifier()
        verdicts = verifier.verify(np.zeros(window_samples(SR)), SR, [E2, B2])
        assert not any(v.wrong for v in verdicts.values())

    def test_duplicate_expected_notes_are_collapsed(self):
        verifier = ChordVerifier()
        verdicts = verifier.verify(window([E2, B2]), SR, [E2, B2, B2])
        assert sorted(verdicts) == [E2, B2]


class TestAcquittingIsNotConvicting:
    """`verify` has to CHOOSE which note a string played and must not be
    talked into the wrong one; `confirms` only asks whether the written note
    is there and can never convict. One threshold for both was a borrowed
    constant, and it refused ten of fifteen rescues on the player's arpeggio
    -- every one with the written note winning outright, beaten on the margin
    alone by a rival one or two semitones away.
    """

    def test_the_two_margins_are_separate_knobs(self):
        verifier = ChordVerifier(margin_db=8.0, confirm_margin_db=2.0)
        assert verifier.margin_db == 8.0
        assert verifier.confirm_margin_db == 2.0

    def test_confirming_uses_its_own(self):
        """The written note wins by 3 dB: enough to acquit, not enough to
        convict a string of having played something else."""
        verifier = ChordVerifier(margin_db=8.0, confirm_margin_db=2.0)
        scores = {40: -10.0, 41: -13.0}
        assert verifier._decide(40, scores, allow_intruder=False,
                                margin_db=2.0).correct
        assert not verifier._decide(40, scores).correct

    def test_and_a_rival_that_really_wins_is_still_refused(self):
        """It only ever acquits -- it must not acquit the wrong note."""
        verifier = ChordVerifier(confirm_margin_db=2.0)
        assert not verifier._decide(40, {40: -20.0, 41: -8.0},
                                    allow_intruder=False,
                                    margin_db=2.0).correct

    def test_a_note_too_quiet_to_be_there_is_refused_whatever_the_margin(self):
        """The presence floor is a different question from the margin, and
        loosening one must not loosen the other."""
        verifier = ChordVerifier(present_db=-32.0, confirm_margin_db=0.0)
        assert not verifier._decide(40, {40: -40.0, 41: -90.0},
                                    allow_intruder=False,
                                    margin_db=0.0).correct

    def test_the_shipped_value_sits_between_the_two_populations(self):
        """Measured: the 54 genuine rescues on the arpeggio take have a worst
        margin of 2.2 dB, and the single confirmation on a DAMPED control --
        where anything confirmed is a note being invented -- sits at 1.2 dB.
        A value outside that window either invents notes or stops rescuing.
        """
        assert 1.2 < CONFIRM_MARGIN_DB <= 2.2
