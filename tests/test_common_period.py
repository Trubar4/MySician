"""A reading that is the common period of two written notes.

*"9. Bund auf D-Saite wird praktisch nicht erkannt im schnellen Spiel."* B3 on
the D string alternating with E4 on the G string is a perfect fourth, so the
two share a period at E2 -- B3 is its third harmonic and E4 its fourth -- and
monophonic YIN reports that period instead of either string. Measured on the
player's own run: 185 of 338 missed notes were that one written B3, and 97
confident strikes came back as E2.

Nineteen semitones is not a whole number of octaves, so the octave
equivalence the matcher already grants never saw it.
"""

import pytest

from pickhero.audio.detector import DetectedNote
from pickhero.audio.input import TimestampedNote
from pickhero.matcher import (COMMON_PERIOD_PARTNERS, MatchType, NoteMatcher)
from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)

GAP_MS = 160.0          # the ostinato's own spacing at the written tempo
B3, E4, E2 = 59, 64, 40


def _song(pitches, gap_ms=GAP_MS, dead=(), strings=None):
    """One note per onset, `gap_ms` apart, on the string each pitch wants."""
    notes = []
    for i, midi in enumerate(pitches):
        notes.append(NoteEvent(
            timestamp_ms=i * gap_ms, duration_ms=gap_ms * 2,
            midi_note=midi,
            string=(strings[i] if strings else (3 if midi == E4 else 4)),
            fret=midi - 50, measure=0, dead=i in dead))
    return Timeline(notes, SongMetadata(title="t", tempo=120),
                    measures=[MeasureInfo(index=0, start_ms=0.0,
                                          end_ms=len(pitches) * gap_ms)])


def _strike(midi, at_ms, subharmonic=False, confidence=1.0):
    return TimestampedNote(
        note=DetectedNote(midi_note=midi, frequency=0.0,
                          confidence=confidence, name="", is_onset=True,
                          subharmonic=subharmonic),
        timestamp_ms=at_ms, sample_pos=None)


def _matcher(song, **kw):
    return NoteMatcher(song, timing_window_ms=250.0, late_window_ms=368.0,
                       **kw)


def _state(m, song, i):
    return m._get_state(song.notes[i])


# The ostinato as the tab writes it: E4, then two B3s, over and over.
OSTINATO = [E4, B3, B3] * 3


def _heard(m, song, i):
    """Feed the strike that really credits note `i`, as the app would.

    The E4 is heard as itself 92 % of the time on the player's instrument,
    which is why it is the partner rather than the beneficiary -- and while it
    is still PENDING the octave equivalence the matcher already grants claims
    an E2 reading for it (two octaves), so the rule is never reached. That is
    existing behaviour and it is right; it is also why every test below hears
    the E4 first.
    """
    note = song.notes[i]
    m.process_detected_notes([_strike(note.midi_note, note.timestamp_ms)],
                             note.timestamp_ms)


class TestTheCommonPeriodIsCredited:
    """Each of these fails on the code before the rule existed."""

    def test_a_third_of_the_written_pitch_credits_it(self):
        # E2 is 1/3 of the B3 and 1/4 of the E4 beside it.
        song = _song(OSTINATO)
        m = _matcher(song)
        _heard(m, song, 0)
        m.process_detected_notes([_strike(E2, GAP_MS)], GAP_MS)
        assert _state(m, song, 1) == MatchType.HIT

    def test_it_credits_the_nearest_of_the_partners(self):
        # Both E4s in reach are heard first, or octave equivalence claims the
        # reading for whichever of them is still pending -- two octaves is
        # zero to it, and that is the existing behaviour.
        song = _song(OSTINATO)
        m = _matcher(song)
        _heard(m, song, 0)
        _heard(m, song, 3)
        m.process_detected_notes([_strike(E2, 2 * GAP_MS)], 2 * GAP_MS)
        assert _state(m, song, 2) == MatchType.HIT
        assert _state(m, song, 1) == MatchType.PENDING

    def test_neither_partner_need_be_an_octave_of_the_reading(self):
        """1/3 and 1/6 are 19 and 31 semitones -- neither is an octave.

        Which is the whole reason the rule has to exist: the halves and
        quarters octave equivalence already grants, and the odd divisions it
        cannot see at all.
        """
        song = _song([B3, 71])          # B3 and the B an octave above it
        m = _matcher(song)
        m.process_detected_notes([_strike(E2, 0.0)], 0.0)
        assert _state(m, song, 0) == MatchType.HIT

    def test_a_partner_already_green_still_counts_as_one(self):
        """The partner is the note that WAS heard -- that is the whole case.

        B3 reads 26 % on the player's instrument and the E4 beside it 92 %, so
        by the time the E2 arrives its partner has been green for 160 ms. The
        first version of the rule looked for both among the PENDING notes and
        fired not once on the song it was built for.
        """
        song = _song(OSTINATO)
        m = _matcher(song)
        _heard(m, song, 0)
        assert _state(m, song, 0) == MatchType.HIT
        m.process_detected_notes([_strike(E2, GAP_MS)], GAP_MS)
        assert _state(m, song, 1) == MatchType.HIT

    def test_it_is_counted_and_drained_apart_from_a_strum(self):
        song = _song(OSTINATO)
        m = _matcher(song)
        _heard(m, song, 0)
        m.process_detected_notes([_strike(E2, GAP_MS)], GAP_MS)
        assert m.unreliable(song.notes[1]) == "common_period"
        assert m.notes_from_common_period == 1
        assert m.notes_by_strum == 0
        assert m.notes_proved == 1


class TestWhatItRefuses:

    def test_one_partner_alone_is_not_enough(self):
        """A lone B3 with nothing sharing its period proves nothing."""
        song = _song([B3, B3, B3])
        m = _matcher(song)
        m.process_detected_notes([_strike(E2, GAP_MS)], GAP_MS)
        assert all(_state(m, song, i) == MatchType.PENDING for i in range(3))
        assert m.notes_from_common_period == 0

    @pytest.mark.parametrize("off", (-1, 1))
    def test_a_semitone_off_the_period_is_not_the_period(self, off):
        """The wrong fret is what says this has to be exact.

        Played a fret high, the detector reported C3 on 44 strikes -- one
        semitone from B2, which is half of the written B3. A tolerance of one
        semitone credits 46 of them and the rule is worth nothing.
        """
        song = _song([E4, B3, B3])
        m = _matcher(song)
        m.process_detected_notes([_strike(E2 + off, GAP_MS)], GAP_MS)
        assert _state(m, song, 1) == MatchType.PENDING

    def test_a_dead_note_supplies_no_harmonic(self):
        song = _song(OSTINATO, dead=(0, 3))
        m = _matcher(song)
        m.process_detected_notes([_strike(E2, GAP_MS)], GAP_MS)
        assert m.notes_from_common_period == 0

    def test_a_note_the_fret_filter_hides_is_not_a_partner(self):
        song = _song(OSTINATO)
        m = _matcher(song)
        m.note_filter = lambda n: n.midi_note != E4
        m.process_detected_notes([_strike(E2, GAP_MS)], GAP_MS)
        assert m.notes_from_common_period == 0

    def test_the_rule_can_be_switched_off(self):
        """The control run of tools/check_common_period.py."""
        song = _song(OSTINATO)
        m = _matcher(song, common_period=False)
        _heard(m, song, 0)
        m.process_detected_notes([_strike(E2, GAP_MS)], GAP_MS)
        assert _state(m, song, 1) == MatchType.PENDING

    def test_two_partners_really_is_the_shipped_requirement(self):
        assert COMMON_PERIOD_PARTNERS == 2


class TestItDoesNotDisturbWhatWorked:

    def test_a_reading_that_matches_a_written_pitch_is_an_ordinary_hit(self):
        song = _song(OSTINATO)
        m = _matcher(song)
        m.process_detected_notes([_strike(B3, GAP_MS)], GAP_MS)
        assert m.unreliable(song.notes[1]) is None
        assert m.notes_proved == 1
        assert m.notes_from_common_period == 0

    def test_a_dead_note_still_catches_a_strike_nothing_explains(self):
        """The common period is asked first, so it must not eat this."""
        song = _song([B3, B3], dead=(0, 1))
        m = _matcher(song)
        m.process_detected_notes([_strike(37, 0.0)], 0.0)
        assert _state(m, song, 0) == MatchType.HIT

    def test_the_counter_is_cleared_by_reset(self):
        song = _song(OSTINATO)
        m = _matcher(song)
        _heard(m, song, 0)
        m.process_detected_notes([_strike(E2, GAP_MS)], GAP_MS)
        assert m.notes_from_common_period == 1
        m.reset()
        assert m.notes_from_common_period == 0

    def test_forgetting_the_stretch_takes_the_credit_back(self):
        song = _song(OSTINATO)
        m = _matcher(song)
        _heard(m, song, 0)
        m.process_detected_notes([_strike(E2, GAP_MS)], GAP_MS)
        m.forget_from(0.0)
        assert m.notes_from_common_period == 0
        assert m.notes_by_strum == 0
        assert _state(m, song, 1) == MatchType.PENDING
