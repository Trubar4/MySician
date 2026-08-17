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


class TestTechniqueTolerance:
    """A bend or a slide leaves the written pitch on purpose.

    Scoring it against that one pitch would mark every correctly played bend
    in a tab as wrong, so the matcher accepts the whole region the technique
    covers. How far a bend actually went is a separate question.
    """

    def _bent(self, semitones: float) -> NoteEvent:
        return NoteEvent(
            timestamp_ms=1000.0, duration_ms=500.0, midi_note=64,
            string=1, fret=0,
            bend=((0.0, 0.0), (0.5, semitones), (1.0, semitones)),
        )

    def test_pitch_at_the_top_of_a_full_bend_counts_as_a_hit(self):
        matcher = _make_matcher([self._bent(2.0)])
        results = matcher.process_detected_notes([_detected(66, 1000.0)], 1000.0)
        assert results[0].match_type == MatchType.HIT

    def test_pitch_along_the_way_counts_too(self):
        matcher = _make_matcher([self._bent(2.0)])
        results = matcher.process_detected_notes([_detected(65, 1000.0)], 1000.0)
        assert results[0].match_type == MatchType.HIT

    def test_the_written_pitch_still_counts(self):
        matcher = _make_matcher([self._bent(2.0)])
        results = matcher.process_detected_notes([_detected(64, 1000.0)], 1000.0)
        assert results[0].match_type == MatchType.HIT

    def test_past_the_top_of_the_bend_is_no_longer_exact(self):
        """Tolerant is not unbounded -- two frets over the target is not it."""
        matcher = _make_matcher([self._bent(2.0)])
        results = matcher.process_detected_notes([_detected(68, 1000.0)], 1000.0)
        assert not results or results[0].match_type != MatchType.HIT

    def test_bending_down_is_not_a_thing(self):
        matcher = _make_matcher([self._bent(2.0)])
        results = matcher.process_detected_notes([_detected(62, 1000.0)], 1000.0)
        assert not results or results[0].match_type != MatchType.HIT

    def test_a_plain_note_keeps_its_single_pitch(self):
        matcher = _make_matcher([_note_event(1000.0, midi_note=64)])
        results = matcher.process_detected_notes([_detected(66, 1000.0)], 1000.0)
        assert not results or results[0].match_type != MatchType.HIT

    def test_slide_covers_the_span_up_to_its_target(self):
        source = NoteEvent(timestamp_ms=1000.0, duration_ms=400.0, midi_note=64,
                           string=1, fret=0, slide_to_next=True)
        target = NoteEvent(timestamp_ms=1400.0, duration_ms=400.0, midi_note=69,
                           string=1, fret=5)
        matcher = _make_matcher([source, target])
        results = matcher.process_detected_notes([_detected(67, 1000.0)], 1000.0)
        assert results[0].match_type == MatchType.HIT

    def test_slide_off_the_end_allows_a_couple_of_frets(self):
        note = NoteEvent(timestamp_ms=1000.0, duration_ms=400.0, midi_note=64,
                         string=1, fret=7, slide_out=1)
        matcher = _make_matcher([note])
        results = matcher.process_detected_notes([_detected(66, 1000.0)], 1000.0)
        assert results[0].match_type == MatchType.HIT

    def test_slide_off_upwards_does_not_excuse_a_lower_pitch(self):
        note = NoteEvent(timestamp_ms=1000.0, duration_ms=400.0, midi_note=64,
                         string=1, fret=7, slide_out=1)
        matcher = _make_matcher([note])
        results = matcher.process_detected_notes([_detected(62, 1000.0)], 1000.0)
        assert not results or results[0].match_type != MatchType.HIT


class TestLegatoCredit:
    """A hammered, pulled or slid-into note is never picked.

    Waiting for a strike on one can only ever end in a miss, which would mark
    every legato passage in a tab red for notes the player did play.
    """

    def _pair(self, lead_kw: dict, target_fret: int = 7) -> list[NoteEvent]:
        return [
            NoteEvent(timestamp_ms=1000.0, duration_ms=200.0, midi_note=64,
                      string=1, fret=5, **lead_kw),
            NoteEvent(timestamp_ms=1200.0, duration_ms=200.0, midi_note=66,
                      string=1, fret=target_fret),
        ]

    def _play_lead_then_run_past(self, notes, midi=64):
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([_detected(midi, 1000.0)], 1000.0)
        matcher.process_detected_notes([], 3000.0)
        return matcher, notes

    def test_hammered_note_inherits_the_hit(self):
        matcher, notes = self._play_lead_then_run_past(
            self._pair({"hammer_to_next": True})
        )
        assert matcher.get_note_state(notes[1]) == MatchType.HIT

    def test_pulled_note_inherits_it_too(self):
        matcher, notes = self._play_lead_then_run_past(
            self._pair({"hammer_to_next": True}, target_fret=3)
        )
        assert matcher.get_note_state(notes[1]) == MatchType.HIT

    def test_slide_target_inherits_the_hit(self):
        matcher, notes = self._play_lead_then_run_past(
            self._pair({"slide_to_next": True})
        )
        assert matcher.get_note_state(notes[1]) == MatchType.HIT

    def test_nothing_is_inherited_when_the_lead_was_missed(self):
        notes = self._pair({"hammer_to_next": True})
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([], 3000.0)   # never played
        assert matcher.get_note_state(notes[0]) == MatchType.MISS
        assert matcher.get_note_state(notes[1]) == MatchType.MISS

    def test_a_close_lead_passes_on_close_not_hit(self):
        notes = self._pair({"hammer_to_next": True})
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([_detected(65, 1000.0)], 1000.0)
        matcher.process_detected_notes([], 3000.0)
        assert matcher.get_note_state(notes[0]) == MatchType.CLOSE
        assert matcher.get_note_state(notes[1]) == MatchType.CLOSE

    def test_a_picked_note_still_has_to_be_played(self):
        """Only the note AFTER a legato mark is exempt, not every note."""
        notes = self._pair({})
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([_detected(64, 1000.0)], 1000.0)
        matcher.process_detected_notes([], 3000.0)
        assert matcher.get_note_state(notes[1]) == MatchType.MISS

    def test_playing_the_legato_note_yourself_still_scores_it(self):
        """Some players re-pick; that must not be punished either."""
        notes = self._pair({"hammer_to_next": True})
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([_detected(64, 1000.0)], 1000.0)
        matcher.process_detected_notes([_detected(66, 1200.0)], 1200.0)
        assert matcher.get_note_state(notes[1]) == MatchType.HIT


class TestDeadNotes:
    """A dead note is a click, so the strike is the whole of the evidence.

    Its written fret says where the fretting hand damps the string, not which
    pitch comes out. Scored against that pitch, every dead note in a tab is a
    miss no matter how well it was played -- and a heavily muted riff is most
    of a metal tab.
    """

    def _unpitched(self, timestamp_ms: float) -> TimestampedNote:
        return TimestampedNote(
            note=DetectedNote(midi_note=0, frequency=0.0, confidence=0.0,
                              name="", is_onset=True, unpitched=True),
            timestamp_ms=timestamp_ms,
        )

    def _dead(self, timestamp_ms: float, string: int = 6,
              midi_note: int = 40) -> NoteEvent:
        return NoteEvent(timestamp_ms=timestamp_ms, duration_ms=200.0,
                         midi_note=midi_note, string=string, fret=0, dead=True)

    def test_a_pitchless_strike_plays_a_dead_note(self):
        note = self._dead(1000.0)
        matcher = _make_matcher([note])
        matcher.process_detected_notes([self._unpitched(1000.0)], 1000.0)
        assert matcher.get_note_state(note) == MatchType.HIT

    def test_a_pitched_strike_plays_one_too(self):
        """Damping a string still lets some pitch through, and which pitch it
        is says nothing about whether the mute was right."""
        note = self._dead(1000.0)
        matcher = _make_matcher([note])
        matcher.process_detected_notes([_detected(52, 1000.0)], 1000.0)
        assert matcher.get_note_state(note) == MatchType.HIT

    def test_a_dead_note_nobody_struck_is_still_missed(self):
        note = self._dead(1000.0)
        matcher = _make_matcher([note])
        matcher.process_detected_notes([], 3000.0)
        assert matcher.get_note_state(note) == MatchType.MISS

    def test_a_muted_strum_is_one_stroke(self):
        """A dead note on three strings at once has one click, not three."""
        notes = [self._dead(1000.0, string=s, midi_note=m)
                 for s, m in ((6, 40), (5, 45), (4, 50))]
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([self._unpitched(1000.0)], 1000.0)
        assert all(matcher.get_note_state(n) == MatchType.HIT for n in notes)

    def test_a_dead_note_does_not_swallow_its_neighbour_s_strike(self):
        """The written note beside it must still get the strike it needs.

        A dead note accepts any pitch, so letting it compete on equal terms
        would have it eat the strike meant for the real note next to it and
        leave that one to time out as a miss.
        """
        real = _note_event(1000.0, midi_note=64, string=1, fret=12)
        dead = self._dead(1000.0)
        matcher = _make_matcher([real, dead])
        matcher.process_detected_notes([_detected(64, 1000.0)], 1000.0)
        assert matcher.get_note_state(real) == MatchType.HIT

    def test_a_dead_note_is_never_measured_for_timing(self):
        """Its written pitch never sounds, so an offset measured against it
        would be a made-up number in the timing report."""
        matcher = _make_matcher([self._dead(1000.0, midi_note=64, string=1)])
        matcher.process_detected_notes([_detected(64, 1080.0)], 1080.0)
        assert matcher.timing_samples == []

    def test_an_unpitched_strike_credits_nothing_when_no_dead_note_is_written(self):
        """Rustle, a hand knock, a choked accident: none of them is a note."""
        note = _note_event(1000.0, midi_note=64)
        matcher = _make_matcher([note])
        results = matcher.process_detected_notes([self._unpitched(1000.0)], 1000.0)
        assert results == []
        assert matcher.get_note_state(note) == MatchType.PENDING

    def test_a_dead_note_is_kept_out_of_chord_verification(self):
        """The verifier is told which pitches to expect, and a damped string
        sounds none of the one written for it."""
        notes = [
            _note_event(1000.0, midi_note=40, string=6, fret=0),
            _note_event(1000.0, midi_note=47, string=5, fret=2),
            self._dead(1000.0, string=4, midi_note=52),
        ]
        matcher = _make_matcher(notes)
        matcher.chord_verifier = object()   # only its presence matters here
        matcher.process_detected_notes(
            [TimestampedNote(
                note=DetectedNote(midi_note=40, frequency=82.4, confidence=0.9,
                                  name="E2", is_onset=True),
                timestamp_ms=1000.0, sample_pos=4096)],
            1000.0,
        )
        expected = matcher._pending_verifications[4096]
        assert notes[2] not in expected
        assert len(expected) == 2


class TestUnpitchedChordCredit:
    """A strummed chord regularly produces no pitch at all, and is still played.

    Monophonic YIN finds no single period in a six-string strum: on the
    reference recordings 38-55 % of strikes on chords of four strings and up
    carry no note, against 16 % on one or two. Scored on pitch alone, those
    strums go red however well they were fretted.
    """

    def _unpitched(self, timestamp_ms: float,
                   sample_pos: int | None = None) -> TimestampedNote:
        return TimestampedNote(
            note=DetectedNote(midi_note=0, frequency=0.0, confidence=0.0,
                              name="", is_onset=True, unpitched=True),
            timestamp_ms=timestamp_ms, sample_pos=sample_pos,
        )

    def _chord(self, size: int, timestamp_ms: float = 1000.0) -> list[NoteEvent]:
        strings = [(6, 40), (5, 47), (4, 52), (3, 56), (2, 59), (1, 64)]
        return [
            NoteEvent(timestamp_ms=timestamp_ms, duration_ms=400.0,
                      midi_note=midi, string=s, fret=2)
            for s, midi in strings[:size]
        ]

    def test_a_three_string_chord_is_credited(self):
        notes = self._chord(3)
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([self._unpitched(1000.0)], 1000.0)
        assert all(matcher.get_note_state(n) == MatchType.HIT for n in notes)

    def test_a_six_string_chord_is_credited(self):
        notes = self._chord(6)
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([self._unpitched(1000.0)], 1000.0)
        assert all(matcher.get_note_state(n) == MatchType.HIT for n in notes)

    def test_a_power_chord_is_not(self):
        """Two strings nearly always do produce a pitch, so accepting a
        pitchless strike there would be leniency bought with nothing."""
        notes = self._chord(2)
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([self._unpitched(1000.0)], 1000.0)
        assert all(matcher.get_note_state(n) == MatchType.PENDING for n in notes)

    def test_a_single_note_is_not(self):
        notes = self._chord(1)
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([self._unpitched(1000.0)], 1000.0)
        assert matcher.get_note_state(notes[0]) == MatchType.PENDING

    def test_nothing_is_credited_where_no_chord_is_written(self):
        """Silence, a knock, a muffled accident: none of them is a chord."""
        matcher = _make_matcher(self._chord(3, timestamp_ms=9000.0))
        results = matcher.process_detected_notes([self._unpitched(1000.0)], 1000.0)
        assert results == []

    def test_the_strum_still_goes_to_the_chord_verifier(self):
        """Crediting the strum must not stop the fingers being checked --
        that is the whole reason it is safe to credit it."""
        notes = self._chord(4)
        matcher = _make_matcher(notes)
        matcher.chord_verifier = object()   # only its presence matters here
        matcher.process_detected_notes(
            [self._unpitched(1000.0, sample_pos=8192)], 1000.0)
        assert set(matcher._pending_verifications[8192]) == set(notes)

    def test_a_verdict_can_still_take_a_string_back(self):
        """The credit is a starting position, not a promise."""
        notes = self._chord(3)
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([self._unpitched(1000.0)], 1000.0)
        matcher._rerecord_match(notes[1], MatchType.MISS)
        assert matcher.get_note_state(notes[1]) == MatchType.MISS
        assert matcher.get_statistics()["hits"] == 2

    def test_dead_notes_are_not_counted_toward_the_chord(self):
        """A dead note has its own rule and sounds no pitch, so it must not
        push a two-string shape over the line."""
        notes = self._chord(2) + [
            NoteEvent(timestamp_ms=1000.0, duration_ms=400.0, midi_note=52,
                      string=4, fret=0, dead=True)
        ]
        matcher = _make_matcher(notes)
        matcher.process_detected_notes([self._unpitched(1000.0)], 1000.0)
        assert matcher.get_note_state(notes[2]) == MatchType.HIT      # the dead one
        assert matcher.get_note_state(notes[0]) == MatchType.PENDING
        assert matcher.get_note_state(notes[1]) == MatchType.PENDING


class TestTimingReport:
    """The report exists to say WHICH timing problem this is.

    Two numbers cannot: a 90 ms error can be latency (one offset fixes it),
    the player (no offset touches it), or the detector reacting at different
    speeds to different strings (neither). Each has to come out named.
    """

    def _with(self, samples) -> NoteMatcher:
        from pickhero.matcher import TimingSample
        m = _make_matcher([_note_event(1000.0)])
        for delta, string in samples:
            m.timing_errors_ms.append(delta)
            m.timing_samples.append(
                TimingSample(delta_ms=delta, string=string,
                             midi_note=64, note_ms=1000.0)
            )
        return m

    @staticmethod
    def _spread(centre, width, count, string=1):
        """`count` samples evenly across centre +/- width."""
        if count == 1:
            return [(centre, string)]
        step = 2 * width / (count - 1)
        return [(centre - width + i * step, string) for i in range(count)]

    def test_no_report_below_the_minimum(self):
        assert self._with(self._spread(0, 10, 4)).timing_report() is None

    def test_tight_group_away_from_zero_is_latency(self):
        m = self._with(self._spread(90, 8, 30))
        assert m.timing_report()["verdict"] == "latency"

    def test_wide_group_over_zero_is_scatter(self):
        m = self._with(self._spread(0, 110, 30))
        assert m.timing_report()["verdict"] == "scatter"

    def test_both_at_once_is_named_as_both(self):
        """Saying only "latency" would send the player to press K and find
        most of the problem still sitting there."""
        m = self._with(self._spread(80, 110, 60))
        assert m.timing_report()["verdict"] == "mixed"

    def test_a_median_inside_its_own_noise_is_not_latency(self):
        """Loose playing centred on the beat still lands its median twenty-odd
        milliseconds off by chance. That is not something K should chase."""
        import random
        rng = random.Random(4)
        m = self._with([(rng.gauss(0, 90), 1) for _ in range(40)])
        report = m.timing_report()
        assert abs(report["median_ms"]) < 40      # the chance offset
        assert report["verdict"] in ("scatter", "mixed")

    def test_tight_group_on_zero_is_fine(self):
        m = self._with(self._spread(3, 8, 30))
        assert m.timing_report()["verdict"] == "fine"

    def test_strings_at_different_delays_are_named(self):
        samples = (self._spread(10, 6, 20, string=1)
                   + self._spread(90, 6, 20, string=6))
        assert self._with(samples).timing_report()["verdict"] == "per_string"

    def test_loose_playing_is_not_blamed_on_the_strings(self):
        """Two medians of a few dozen loose strikes differ by chance alone.

        This is the case that must NOT be called a per-string problem, or the
        report sends the user hunting a detector bug that is not there.
        """
        samples = (self._spread(0, 120, 20, string=1)
                   + self._spread(40, 120, 20, string=6))
        report = self._with(samples).timing_report()
        assert report["verdict"] == "scatter"
        assert not report["string_gap_real"]

    def test_a_string_played_twice_is_not_evidence(self):
        samples = self._spread(10, 6, 30, string=1) + [(200.0, 6), (210.0, 6)]
        assert not self._with(samples).timing_report()["string_gap_real"]

    def test_residual_is_what_survives_compensation(self):
        m = self._with(self._spread(100, 10, 30))
        report = m.timing_report()
        assert report["mean_error_ms"] == pytest.approx(100.0, abs=1)
        assert report["residual_ms"] < 10
        assert report["explained_fraction"] > 0.85

    def test_scatter_cannot_be_compensated_away(self):
        m = self._with(self._spread(0, 100, 30))
        assert m.timing_report()["explained_fraction"] < 0.1


class TestTimingHistogram:
    def _with(self, deltas) -> NoteMatcher:
        from pickhero.matcher import TimingSample
        m = _make_matcher([_note_event(1000.0)])
        for d in deltas:
            m.timing_errors_ms.append(d)
            m.timing_samples.append(
                TimingSample(delta_ms=d, string=1, midi_note=64, note_ms=1000.0))
        return m

    def test_empty_without_samples(self):
        assert _make_matcher([_note_event(1000.0)]).timing_histogram() == []

    def test_axis_always_contains_the_beat(self):
        """A tight group far from zero must be seen at its distance from it."""
        bars = self._with([95.0, 97.0, 99.0, 101.0]).timing_histogram()
        lows = [low for low, _ in bars]
        assert min(lows) <= 0 <= max(lows)

    def test_every_sample_lands_in_a_bin(self):
        deltas = [-40.0, -5.0, 0.0, 12.0, 88.0, 88.0]
        bars = self._with(deltas).timing_histogram()
        assert sum(count for _, count in bars) == len(deltas)

    def test_bins_widen_rather_than_multiply(self):
        """A wild take must not produce a hundred one-pixel bars."""
        from pickhero.matcher import HISTOGRAM_MAX_BINS
        m = self._with([-900.0, 0.0, 900.0])
        assert len(m.timing_histogram()) <= HISTOGRAM_MAX_BINS + 1
        assert m.timing_bin_ms() > 10.0

    def test_bins_are_evenly_spaced(self):
        bars = self._with([-30.0, 10.0, 50.0]).timing_histogram()
        steps = {round(b[0] - a[0], 6) for a, b in zip(bars, bars[1:])}
        assert len(steps) == 1


class TestTimingSampleYield:
    """Repeated pitches used to contribute almost nothing.

    A wide search over a riff that repeats one note finds two equally good
    candidates, and the measurement rightly refuses. Narrowing the search
    once the offset is roughly known is what brings those strikes back.
    """

    def _riff(self, count=40, gap_ms=300.0):
        """A run of one pitch, opened by a few distinct ones.

        Real songs start somewhere before the riff, and those first notes are
        what the measurement bootstraps on: nothing can disambiguate a
        repeated pitch until the offset is roughly known.
        """
        # Spaced by more than a semitone: the pitch match accepts +/-1, so a
        # chromatic run is as ambiguous as a repeated note.
        intro = [_note_event(400.0 + i * 300.0, midi_note=45 + 3 * (i % 5),
                             string=5)
                 for i in range(14)]
        start = intro[-1].timestamp_ms + 600.0
        return intro + [_note_event(start + i * gap_ms, midi_note=40, string=6)
                        for i in range(count)]

    def _play(self, notes, latency=60.0):
        matcher = _make_matcher(notes, timing_window_ms=150.0)
        for note in notes:
            t = note.timestamp_ms + latency
            matcher.process_detected_notes([_detected(note.midi_note, t)], t)
        return matcher

    def test_repeated_pitches_are_measured_once_the_offset_is_known(self):
        notes = self._riff()
        matcher = self._play(notes)
        assert len(matcher.timing_errors_ms) > len(notes) * 0.6

    def test_a_song_of_nothing_but_one_pitch_measures_almost_nothing(self):
        """The honest answer, not a bug.

        With no note of a different pitch anywhere, "late on this one" and
        "early on the next" never separate, so nothing bootstraps and the
        search never narrows. Only the very first strike counts, having no
        predecessor to be confused with.
        """
        notes = [_note_event(1000.0 + i * 300.0, midi_note=40, string=6)
                 for i in range(30)]
        matcher = self._play(notes)
        assert len(matcher.timing_errors_ms) <= 2
        assert matcher.timing_ambiguous > 20
        assert matcher.timing_report() is None

    def test_the_search_starts_wide(self):
        from pickhero.matcher import LATENCY_SEARCH_MS
        matcher = _make_matcher(self._riff())
        assert matcher._search_radius_ms() == LATENCY_SEARCH_MS

    def test_and_narrows_once_there_is_something_to_narrow_to(self):
        from pickhero.matcher import LATENCY_SEARCH_MS
        matcher = self._play(self._riff())
        assert matcher._search_radius_ms() < LATENCY_SEARCH_MS

    def test_it_never_narrows_below_human_earliness(self):
        from pickhero.matcher import MIN_SEARCH_MS
        matcher = self._play(self._riff(), latency=0.0)
        assert matcher._search_radius_ms() >= MIN_SEARCH_MS

    def test_ambiguous_strikes_are_counted_not_silently_dropped(self):
        notes = [_note_event(1000.0, midi_note=40, string=6),
                 _note_event(1400.0, midi_note=40, string=6)]
        matcher = _make_matcher(notes, timing_window_ms=150.0)
        matcher.process_detected_notes([_detected(40, 1450.0)], 1450.0)
        assert matcher.timing_errors_ms == []
        assert matcher.timing_ambiguous == 1

    def test_samples_carry_the_string_they_were_measured_on(self):
        notes = [_note_event(1000.0, midi_note=45, string=5)]
        matcher = _make_matcher(notes, timing_window_ms=150.0)
        matcher.process_detected_notes([_detected(45, 1040.0)], 1040.0)
        assert matcher.timing_samples[0].string == 5
        assert matcher.timing_samples[0].delta_ms == pytest.approx(40.0)

    def test_reset_forgets_everything(self):
        matcher = self._play(self._riff())
        matcher.reset_timing_samples()
        assert matcher.timing_errors_ms == []
        assert matcher.timing_samples == []
        assert matcher.timing_ambiguous == 0
        assert matcher.timing_report() is None
