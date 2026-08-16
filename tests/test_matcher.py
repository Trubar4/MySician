"""Tests for pickhero.matcher module."""

import pytest

from pickhero.audio.detector import DetectedNote
from pickhero.audio.input import TimestampedNote
from pickhero.matcher import MatchType, MatchResult, NoteMatcher
from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline


def _note_event(timestamp_ms: float, midi_note: int = 64, string: int = 1,
                fret: int = 0, duration_ms: float = 500.0) -> NoteEvent:
    return NoteEvent(
        timestamp_ms=timestamp_ms,
        duration_ms=duration_ms,
        midi_note=midi_note,
        string=string,
        fret=fret,
    )


def _detected(midi_note: int, timestamp_ms: float, is_onset: bool = True,
              confidence: float = 0.95) -> TimestampedNote:
    return TimestampedNote(
        note=DetectedNote(
            midi_note=midi_note,
            frequency=440.0,  # placeholder
            confidence=confidence,
            name="A4",  # placeholder
            is_onset=is_onset,
        ),
        timestamp_ms=timestamp_ms,
    )


def _make_matcher(notes: list[NoteEvent], timing_window_ms: float = 100.0,
                  audio_offset_ms: float = 0.0,
                  chord_threshold_ms: float = 50.0) -> NoteMatcher:
    timeline = Timeline(notes, SongMetadata(title="Test", tempo=120))
    return NoteMatcher(
        timeline,
        timing_window_ms=timing_window_ms,
        audio_offset_ms=audio_offset_ms,
        chord_threshold_ms=chord_threshold_ms,
    )


class TestExactHit:
    def test_exact_midi_match(self):
        """Detected MIDI matches tab note exactly -> HIT."""
        tab_note = _note_event(1000.0, midi_note=64)
        matcher = _make_matcher([tab_note])

        detected = [_detected(64, 1000.0)]
        results = matcher.process_detected_notes(detected, 1050.0)

        hits = [r for r in results if r.match_type == MatchType.HIT]
        assert len(hits) == 1
        assert hits[0].semitone_distance == 0
        assert tab_note in hits[0].matched_events
        assert matcher.get_note_state(tab_note) == MatchType.HIT

    def test_exact_hit_updates_statistics(self):
        tab_note = _note_event(1000.0, midi_note=64)
        matcher = _make_matcher([tab_note])

        detected = [_detected(64, 1000.0)]
        matcher.process_detected_notes(detected, 1050.0)

        stats = matcher.get_statistics()
        assert stats["hits"] == 1
        assert stats["accuracy_percent"] == 100.0


class TestCloseMatch:
    def test_one_semitone_above(self):
        """±1 semitone -> CLOSE."""
        tab_note = _note_event(1000.0, midi_note=64)
        matcher = _make_matcher([tab_note])

        detected = [_detected(65, 1000.0)]  # +1 semitone
        results = matcher.process_detected_notes(detected, 1050.0)

        close = [r for r in results if r.match_type == MatchType.CLOSE]
        assert len(close) == 1
        assert close[0].semitone_distance == 1
        assert matcher.get_note_state(tab_note) == MatchType.CLOSE

    def test_one_semitone_below(self):
        tab_note = _note_event(1000.0, midi_note=64)
        matcher = _make_matcher([tab_note])

        detected = [_detected(63, 1000.0)]  # -1 semitone
        results = matcher.process_detected_notes(detected, 1050.0)

        close = [r for r in results if r.match_type == MatchType.CLOSE]
        assert len(close) == 1


class TestWrongNoteIgnored:
    def test_far_off_note_no_penalty(self):
        """>1 semitone away, no matching candidate -> no penalty."""
        tab_note = _note_event(1000.0, midi_note=64)
        matcher = _make_matcher([tab_note])

        detected = [_detected(70, 1000.0)]  # 6 semitones off
        results = matcher.process_detected_notes(detected, 1050.0)

        # No HIT or CLOSE results, note stays PENDING
        hit_close = [r for r in results if r.match_type in (MatchType.HIT, MatchType.CLOSE)]
        assert len(hit_close) == 0
        assert matcher.get_note_state(tab_note) == MatchType.PENDING


class TestMissedNote:
    def test_advance_past_window_without_detection(self):
        """Advance past window with no detection -> MISS."""
        tab_note = _note_event(1000.0, midi_note=64)
        matcher = _make_matcher([tab_note], timing_window_ms=100.0)

        # Advance playback to 1200ms (past 1000 + 100 window)
        results = matcher.process_detected_notes([], 1200.0)

        misses = [r for r in results if r.match_type == MatchType.MISS]
        assert len(misses) == 1
        assert matcher.get_note_state(tab_note) == MatchType.MISS

    def test_miss_updates_statistics(self):
        tab_note = _note_event(1000.0, midi_note=64)
        matcher = _make_matcher([tab_note], timing_window_ms=100.0)

        matcher.process_detected_notes([], 1200.0)

        stats = matcher.get_statistics()
        assert stats["misses"] == 1
        assert stats["accuracy_percent"] == 0.0


class TestChordMatching:
    def test_match_one_note_of_chord_marks_all(self):
        """Match one note of a simultaneous pair -> both marked HIT."""
        note_a = _note_event(1000.0, midi_note=64, string=1)
        note_b = _note_event(1000.0, midi_note=59, string=2)
        matcher = _make_matcher([note_a, note_b], chord_threshold_ms=50.0)

        # Detect just one note of the chord
        detected = [_detected(64, 1000.0)]
        results = matcher.process_detected_notes(detected, 1050.0)

        hits = [r for r in results if r.match_type == MatchType.HIT]
        assert len(hits) == 1
        # Both notes should be marked
        assert matcher.get_note_state(note_a) == MatchType.HIT
        assert matcher.get_note_state(note_b) == MatchType.HIT

    def test_non_simultaneous_notes_not_grouped(self):
        """Notes far apart in time should not be grouped as chord."""
        note_a = _note_event(1000.0, midi_note=64, string=1)
        note_b = _note_event(2000.0, midi_note=59, string=2)
        matcher = _make_matcher([note_a, note_b], chord_threshold_ms=50.0)

        detected = [_detected(64, 1000.0)]
        matcher.process_detected_notes(detected, 1050.0)

        assert matcher.get_note_state(note_a) == MatchType.HIT
        assert matcher.get_note_state(note_b) == MatchType.PENDING


class TestOnsetOnly:
    def test_non_onset_detections_filtered(self):
        """is_onset=False detections are filtered out."""
        tab_note = _note_event(1000.0, midi_note=64)
        matcher = _make_matcher([tab_note])

        detected = [_detected(64, 1000.0, is_onset=False)]
        results = matcher.process_detected_notes(detected, 1050.0)

        hit_close = [r for r in results if r.match_type in (MatchType.HIT, MatchType.CLOSE)]
        assert len(hit_close) == 0
        assert matcher.get_note_state(tab_note) == MatchType.PENDING


class TestTimingWindow:
    def test_detection_at_edge_of_window_matches(self):
        """Note detected at edge of window still matches."""
        tab_note = _note_event(1000.0, midi_note=64, duration_ms=500.0)
        matcher = _make_matcher([tab_note], timing_window_ms=100.0)

        # Detect at 1099ms — just within the 100ms window of a note at 1000ms
        detected = [_detected(64, 1099.0)]
        results = matcher.process_detected_notes(detected, 1099.0)

        hits = [r for r in results if r.match_type == MatchType.HIT]
        assert len(hits) == 1

    def test_detection_outside_window_no_match(self):
        """Detection outside window -> no match."""
        tab_note = _note_event(1000.0, midi_note=64, duration_ms=100.0)
        matcher = _make_matcher([tab_note], timing_window_ms=50.0)

        # Detect at 1200ms — the note ended at 1100ms, window is 50ms
        # get_active_notes_at_time checks [1200-50, 1200+50] = [1150, 1250]
        # note range is [1000, 1100] which doesn't overlap
        detected = [_detected(64, 1200.0)]
        results = matcher.process_detected_notes(detected, 1200.0)

        hits = [r for r in results if r.match_type == MatchType.HIT]
        assert len(hits) == 0


class TestStatistics:
    def test_mixed_results(self):
        notes = [
            _note_event(1000.0, midi_note=64, string=1),
            _note_event(2000.0, midi_note=59, string=2),
            _note_event(3000.0, midi_note=55, string=3),
        ]
        matcher = _make_matcher(notes, timing_window_ms=100.0)

        # Hit first note
        matcher.process_detected_notes([_detected(64, 1000.0)], 1050.0)
        # Close on second note
        matcher.process_detected_notes([_detected(60, 2000.0)], 2050.0)
        # Miss third note
        matcher.process_detected_notes([], 3200.0)

        stats = matcher.get_statistics()
        assert stats["hits"] == 1
        assert stats["close"] == 1
        assert stats["misses"] == 1
        assert stats["total"] == 3
        assert stats["accuracy_percent"] == pytest.approx(100 / 3)


class TestReset:
    def test_reset_clears_state(self):
        tab_note = _note_event(1000.0, midi_note=64)
        matcher = _make_matcher([tab_note])

        matcher.process_detected_notes([_detected(64, 1000.0)], 1050.0)
        assert matcher.get_note_state(tab_note) == MatchType.HIT

        matcher.reset()
        assert matcher.get_note_state(tab_note) == MatchType.PENDING
        stats = matcher.get_statistics()
        assert stats["hits"] == 0
        assert stats["close"] == 0
        assert stats["misses"] == 0


class TestNoDoubleMatch:
    def test_same_note_cannot_be_matched_twice(self):
        """Same tab note can't be matched twice."""
        tab_note = _note_event(1000.0, midi_note=64)
        matcher = _make_matcher([tab_note])

        # First detection -> HIT
        matcher.process_detected_notes([_detected(64, 1000.0)], 1050.0)
        assert matcher.hits == 1

        # Second detection of same pitch -> should not increment
        matcher.process_detected_notes([_detected(64, 1010.0)], 1060.0)
        assert matcher.hits == 1


class TestAudioOffset:
    def test_offset_shifts_detection_time(self):
        """audio_offset_ms shifts the detected timestamp to song time."""
        tab_note = _note_event(5000.0, midi_note=64, duration_ms=500.0)
        matcher = _make_matcher([tab_note], timing_window_ms=100.0,
                                audio_offset_ms=5000.0)

        # Detection at 0ms audio time + 5000ms offset = 5000ms song time
        detected = [_detected(64, 0.0)]
        results = matcher.process_detected_notes(detected, 5050.0)

        hits = [r for r in results if r.match_type == MatchType.HIT]
        assert len(hits) == 1


class TestTimingErrorTracking:
    def test_timing_errors_recorded_for_matches(self):
        notes = [_note_event(1000.0), _note_event(2000.0)]
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([_detected(64, 1040.0)], 1050.0)
        matcher.process_detected_notes([_detected(64, 2050.0)], 2060.0)
        assert matcher.timing_errors_ms == [40.0, 50.0]

    def test_median_requires_min_samples(self):
        notes = [_note_event(1000.0)]
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([_detected(64, 1040.0)], 1050.0)
        assert matcher.median_timing_error_ms(min_samples=5) is None
        assert matcher.median_timing_error_ms(min_samples=1) == 40.0

    def test_median_reflects_audio_offset(self):
        """After compensating, the reported error should shrink to zero."""
        notes = [_note_event(t * 1000.0) for t in range(1, 6)]
        matcher = _make_matcher(notes, audio_offset_ms=-40.0)
        for t in range(1, 6):
            # Strikes consistently detected 40 ms late, offset cancels it
            matcher.process_detected_notes([_detected(64, t * 1000.0 + 40.0)], t * 1000.0 + 50.0)
        assert matcher.median_timing_error_ms() == 0.0

    def test_reset_clears_timing_errors(self):
        notes = [_note_event(1000.0)]
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([_detected(64, 1040.0)], 1050.0)
        matcher.reset()
        assert matcher.timing_errors_ms == []


class TestLateWindow:
    def test_late_strike_can_still_match_within_grace(self):
        """A strike note arriving after the timing window (collector delay)
        must still claim its tab note when late_window_ms covers it."""
        notes = [_note_event(1000.0)]
        timeline = Timeline(notes, SongMetadata(title="Test", tempo=120))
        matcher = NoteMatcher(timeline, timing_window_ms=100.0, late_window_ms=150.0)

        # Playback has advanced past the timing window, but not past the grace
        results = matcher.process_detected_notes([], 1180.0)
        assert results == []  # not yet marked missed

        # The strike (timestamped inside the window) arrives late
        results = matcher.process_detected_notes([_detected(64, 1080.0)], 1190.0)
        assert len(results) == 1
        assert results[0].match_type == MatchType.HIT

    def test_miss_marked_after_grace_expires(self):
        notes = [_note_event(1000.0)]
        timeline = Timeline(notes, SongMetadata(title="Test", tempo=120))
        matcher = NoteMatcher(timeline, timing_window_ms=100.0, late_window_ms=150.0)
        results = matcher.process_detected_notes([], 1260.0)
        assert len(results) == 1
        assert results[0].match_type == MatchType.MISS


class TestStrummedChordCredit:
    """A strike carrying subharmonic (polyphonic) evidence credits the chord."""

    def _power_chord(self):
        # E5 power chord: E2 + B2 + E3 at the same time on strings 6/5/4
        return [
            _note_event(1000.0, midi_note=40, string=6),
            _note_event(1000.0, midi_note=47, string=5),
            _note_event(1000.0, midi_note=52, string=4),
        ]

    def _strike(self, midi: int, ts: float, subharmonic: bool) -> TimestampedNote:
        n = _detected(midi, ts)
        n.note.subharmonic = subharmonic
        return n

    def test_subharmonic_strike_credits_whole_chord(self):
        matcher = _make_matcher(self._power_chord())
        # Detector folded E1 (28) up to E2 (40) and flagged the strum
        results = matcher.process_detected_notes(
            [self._strike(40, 1010.0, subharmonic=True)], 1020.0)
        assert matcher.hits == 3
        assert matcher.misses == 0
        assert len(results) == 1
        assert len(results[0].matched_events) == 3

    def test_plain_strike_keeps_partial_credit(self):
        matcher = _make_matcher(self._power_chord())
        # Single picked string: no polyphonic evidence, only that note counts
        matcher.process_detected_notes(
            [self._strike(40, 1010.0, subharmonic=False)], 1020.0)
        assert matcher.hits == 1

    def test_two_plain_strikes_complete_chord_by_majority(self):
        matcher = _make_matcher(self._power_chord())
        matcher.process_detected_notes(
            [self._strike(40, 1010.0, subharmonic=False)], 1020.0)
        matcher.process_detected_notes(
            [self._strike(47, 1030.0, subharmonic=False)], 1040.0)
        # 2 of 3 matched -> majority auto-completes the third
        assert matcher.hits == 3


class TestUnbiasedLatencyMeasurement:
    def test_error_recorded_even_when_strike_misses_window(self):
        """Latency larger than the timing window must still be measurable,
        otherwise auto-sync can never correct it."""
        notes = [_note_event(1000.0)]
        matcher = _make_matcher(notes)
        # Strike detected 250 ms late — far outside the 100 ms window
        results = matcher.process_detected_notes([_detected(64, 1250.0)], 1260.0)
        assert all(r.match_type != MatchType.HIT for r in results)
        assert matcher.timing_errors_ms == [250.0]

    def test_error_measured_against_nearest_pitch_match(self):
        notes = [_note_event(1000.0, midi_note=64), _note_event(1300.0, midi_note=46)]
        matcher = _make_matcher(notes)
        # E4 strike at 1240: nearest E4 note is at 1000 (the Bb2 at 1300 is
        # closer in time but doesn't match the pitch, not even octave-wise)
        matcher.process_detected_notes([_detected(64, 1240.0)], 1250.0)
        assert matcher.timing_errors_ms == [240.0]

    def test_unrelated_pitch_records_nothing(self):
        notes = [_note_event(1000.0, midi_note=64)]
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([_detected(45, 1050.0)], 1060.0)
        assert matcher.timing_errors_ms == []

    def test_recording_can_be_disabled_for_wait_mode(self):
        notes = [_note_event(1000.0)]
        matcher = _make_matcher(notes)
        matcher.record_timing_samples = False
        matcher.process_detected_notes([_detected(64, 1040.0)], 1050.0)
        assert matcher.timing_errors_ms == []

    def test_repeated_riff_does_not_alias_to_next_note(self):
        """With identical notes every 500 ms and +300 ms latency, the
        measurement must attribute the strike to the PAST note (+300),
        not the closer upcoming one (-200): latency is never negative."""
        notes = [_note_event(t * 500.0, midi_note=40, duration_ms=120.0)
                 for t in range(1, 5)]
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([_detected(40, 800.0)], 810.0)
        assert matcher.timing_errors_ms == [300.0]


class TestTimingSpread:
    """Spread is what tells latency (fixable by an offset) from jitter."""

    def _matcher_with(self, errors):
        timeline = Timeline([_note_event(1000.0)])
        m = NoteMatcher(timeline)
        m.timing_errors_ms = list(errors)
        return m

    def test_none_until_enough_samples(self):
        assert self._matcher_with([10.0, 12.0]).timing_spread_ms() is None

    def test_constant_offset_has_no_spread(self):
        """Every strike 80 ms late: pure latency, an offset fixes all of it."""
        m = self._matcher_with([80.0] * 8)
        assert m.median_timing_error_ms() == pytest.approx(80.0)
        assert m.timing_spread_ms() == pytest.approx(0.0)

    def test_scattered_strikes_have_large_spread(self):
        m = self._matcher_with([-60.0, 70.0, -50.0, 80.0, -70.0, 60.0, 0.0])
        assert m.timing_spread_ms() > 40.0

    def test_outliers_do_not_dominate(self):
        """Median absolute deviation, so one wild strike is not the verdict."""
        tight = self._matcher_with([20.0] * 9 + [900.0])
        assert tight.timing_spread_ms() == pytest.approx(0.0)

    def test_spread_is_independent_of_offset(self):
        near = self._matcher_with([-5.0, 0.0, 5.0, -5.0, 0.0, 5.0])
        far = self._matcher_with([195.0, 200.0, 205.0, 195.0, 200.0, 205.0])
        assert near.timing_spread_ms() == pytest.approx(far.timing_spread_ms())


class TestTimingSampleAmbiguity:
    """A repeated riff must not be measured against the wrong note."""

    def _repeating(self, spacing_ms=600.0, count=8, midi=40):
        return Timeline([
            _note_event(i * spacing_ms, midi_note=midi, string=6)
            for i in range(count)
        ])

    def test_clear_attribution_is_recorded(self):
        m = NoteMatcher(self._repeating(), timing_window_ms=150.0)
        # 60 ms after note 3, and 540 ms from the next: unambiguous
        m._record_timing_sample(1860.0, 40)
        assert m.timing_errors_ms == [pytest.approx(60.0)]

    def test_two_candidate_notes_are_rejected(self):
        """Exactly the case that used to walk the offset out to nonsense.

        At 1690 the strike is 490 ms after the note at 1200 and 110 ms before
        the one at 1800. Both are in the search window and both are the same
        pitch, so "late against the first" and "early against the second" are
        indistinguishable — and taking the nearer one measures -110 when the
        truth is +490, which auto-sync would then apply in the wrong direction.
        """
        m = NoteMatcher(self._repeating(), timing_window_ms=150.0)
        m._record_timing_sample(1690.0, 40)
        assert m.timing_errors_ms == []

    def test_asymmetric_window_keeps_late_notes_out_of_reach(self):
        """A strike is never attributed to a note it clearly preceded."""
        m = NoteMatcher(self._repeating(), timing_window_ms=150.0)
        m._record_timing_sample(1500.0, 40)   # 300 ms early for 1800 is not credible
        assert m.timing_errors_ms == [pytest.approx(300.0)]

    def test_no_matching_pitch_records_nothing(self):
        m = NoteMatcher(self._repeating(), timing_window_ms=150.0)
        m._record_timing_sample(1860.0, 60)
        assert m.timing_errors_ms == []

    def test_widely_spaced_notes_still_measure(self):
        m = NoteMatcher(self._repeating(spacing_ms=4000.0), timing_window_ms=150.0)
        m._record_timing_sample(4200.0, 40)
        assert m.timing_errors_ms == [pytest.approx(200.0)]


class TestTimingWindowSetter:
    """The hit window is adjustable while playing, so it must take effect."""

    def test_widening_lets_a_late_strike_score(self):
        timeline = Timeline([_note_event(1000.0, midi_note=64, string=1)])
        m = NoteMatcher(timeline, timing_window_ms=100.0)
        m.timing_window_ms = 250.0
        results = m.process_detected_notes([_detected(64, 1180.0)], 1200.0)
        assert results and results[0].match_type == MatchType.HIT

    def test_narrowing_is_applied_too(self):
        """The same strike that scored at 250 ms must not score at 50 ms.
        It comes back as a miss, not as nothing, since the note has by then
        passed its window."""
        timeline = Timeline([_note_event(1000.0, midi_note=64, string=1)])
        m = NoteMatcher(timeline, timing_window_ms=250.0)
        m.timing_window_ms = 50.0
        results = m.process_detected_notes([_detected(64, 1180.0)], 1200.0)
        assert not any(r.match_type == MatchType.HIT for r in results)
