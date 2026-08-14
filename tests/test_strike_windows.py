"""Tests for the raw-audio path feeding per-string chord verification.

Covers the ring buffer that holds recent audio and the matcher's use of the
windows cut from it. No audio hardware and no real chords needed: the ring is
plain bookkeeping, and the matcher is exercised with a stub verifier so the
integration is tested independently of the signal processing.
"""

import numpy as np
import pytest

from pickhero.audio.chord_verify import StringVerdict
from pickhero.audio.detector import DetectedNote
from pickhero.audio.input import (
    MAX_QUEUED_WINDOWS, OnsetPitchCollector, StrikeWindow, TimestampedNote,
    _AudioRing,
)
from pickhero.matcher import MatchType, NoteMatcher
from pickhero.tabs.timeline import NoteEvent, Timeline

E2, B2, C3 = 40, 47, 48


class TestAudioRing:
    def test_reads_back_what_was_written(self):
        ring = _AudioRing(1000)
        data = np.arange(100, dtype=np.float32)
        ring.push(data)
        assert np.array_equal(ring.read(0, 100), data)

    def test_read_beyond_written_returns_none(self):
        ring = _AudioRing(1000)
        ring.push(np.zeros(50, dtype=np.float32))
        assert ring.read(0, 51) is None

    def test_reads_across_the_wrap_point(self):
        ring = _AudioRing(100)
        ring.push(np.arange(80, dtype=np.float32))
        ring.push(np.arange(80, 140, dtype=np.float32))
        # samples 60..119 straddle the wrap
        assert np.array_equal(ring.read(60, 60), np.arange(60, 120, dtype=np.float32))

    def test_overwritten_samples_return_none(self):
        ring = _AudioRing(100)
        ring.push(np.arange(300, dtype=np.float32))
        assert ring.read(0, 10) is None          # long gone
        assert ring.read(250, 50) is not None    # still inside the ring

    def test_chunk_larger_than_ring_keeps_the_tail(self):
        ring = _AudioRing(50)
        ring.push(np.arange(200, dtype=np.float32))
        assert np.array_equal(ring.read(150, 50), np.arange(150, 200, dtype=np.float32))

    def test_tracks_total_samples_written(self):
        ring = _AudioRing(100)
        ring.push(np.zeros(30, dtype=np.float32))
        ring.push(np.zeros(45, dtype=np.float32))
        assert ring.written == 75


class TestCollectorSamplePosition:
    def test_strike_carries_the_sample_position_of_its_onset(self):
        collector = OnsetPitchCollector()
        collector.process_frame(0.0, 0.0, True, 1000.0, 0.8, sample_pos=48000)
        strike = None
        for i in range(1, 14):
            strike = collector.process_frame(
                82.4, 0.95, False, 1000.0 + i, 0.8, sample_pos=48000 + i * 512
            )
            if strike is not None:
                break
        assert strike is not None
        # the onset's position, not the position where it settled
        assert strike.sample_pos == 48000

    def test_sample_position_is_optional(self):
        collector = OnsetPitchCollector()
        collector.process_frame(0.0, 0.0, True, 0.0, 0.8)
        strike = None
        for i in range(1, 14):
            strike = collector.process_frame(82.4, 0.95, False, float(i), 0.8)
            if strike is not None:
                break
        assert strike is not None and strike.sample_pos is None


class _StubVerifier:
    """Reports a fixed pitch per expected note, so the matcher wiring can be
    tested without depending on the signal processing."""

    def __init__(self, played: dict):
        self.played = played
        self.calls = 0

    def verify(self, audio, sample_rate, expected_midi):
        self.calls += 1
        return {
            m: StringVerdict(m, self.played.get(m, m), -10.0, 20.0, "direct")
            for m in sorted(set(expected_midi))
        }


def _chord_timeline():
    """Two notes struck together -- a power chord on strings 6 and 5."""
    return Timeline([
        NoteEvent(timestamp_ms=1000.0, duration_ms=500.0, midi_note=E2, string=6, fret=0),
        NoteEvent(timestamp_ms=1000.0, duration_ms=500.0, midi_note=B2, string=5, fret=2),
    ])


def _strike(midi, timestamp_ms, sample_pos):
    return TimestampedNote(
        note=DetectedNote(midi, 82.4, 0.95, "E2", True),
        timestamp_ms=timestamp_ms,
        sample_pos=sample_pos,
    )


def _window(sample_pos, sample_rate=48000):
    return StrikeWindow(1000.0, sample_pos, np.zeros(16384, dtype=np.float32), sample_rate)


class TestMatcherVerification:
    def test_wrong_string_is_downgraded_to_miss(self):
        timeline = _chord_timeline()
        verifier = _StubVerifier({B2: C3})   # the fifth was fretted a semitone up
        matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                              chord_verifier=verifier)
        matcher.process_detected_notes([_strike(E2, 1000.0, 5000)], 1000.0)

        fifth = [n for n in timeline.notes if n.midi_note == B2][0]
        assert matcher.get_note_state(fifth) != MatchType.MISS

        results = matcher.process_strike_windows([_window(5000)])
        assert matcher.get_note_state(fifth) == MatchType.MISS
        assert any(r.match_type == MatchType.MISS for r in results)

    def test_correct_strings_are_left_alone(self):
        timeline = _chord_timeline()
        matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                              chord_verifier=_StubVerifier({}))
        matcher.process_detected_notes([_strike(E2, 1000.0, 5000)], 1000.0)
        before = dict((n.string, matcher.get_note_state(n)) for n in timeline.notes)
        matcher.process_strike_windows([_window(5000)])
        after = dict((n.string, matcher.get_note_state(n)) for n in timeline.notes)
        assert before == after

    def test_statistics_stay_consistent_after_a_downgrade(self):
        timeline = _chord_timeline()
        matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                              chord_verifier=_StubVerifier({B2: C3}))
        matcher.process_detected_notes([_strike(E2, 1000.0, 5000)], 1000.0)
        total_before = matcher.hits + matcher.close + matcher.misses
        matcher.process_strike_windows([_window(5000)])
        assert matcher.hits + matcher.close + matcher.misses == total_before
        assert matcher.misses == 1

    def test_window_without_a_matching_strike_is_ignored(self):
        timeline = _chord_timeline()
        matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                              chord_verifier=_StubVerifier({B2: C3}))
        matcher.process_detected_notes([_strike(E2, 1000.0, 5000)], 1000.0)
        matcher.process_strike_windows([_window(999999)])
        fifth = [n for n in timeline.notes if n.midi_note == B2][0]
        assert matcher.get_note_state(fifth) != MatchType.MISS

    def test_no_verifier_means_no_verification(self):
        timeline = _chord_timeline()
        matcher = NoteMatcher(timeline, timing_window_ms=150.0)
        matcher.process_detected_notes([_strike(E2, 1000.0, 5000)], 1000.0)
        assert matcher.process_strike_windows([_window(5000)]) == []

    def test_single_notes_are_not_queued_for_verification(self):
        timeline = Timeline([
            NoteEvent(timestamp_ms=1000.0, duration_ms=500.0, midi_note=E2,
                      string=6, fret=0),
        ])
        verifier = _StubVerifier({})
        matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                              chord_verifier=verifier)
        matcher.process_detected_notes([_strike(E2, 1000.0, 5000)], 1000.0)
        matcher.process_strike_windows([_window(5000)])
        assert verifier.calls == 0

    def test_already_missed_notes_are_not_downgraded_twice(self):
        timeline = _chord_timeline()
        matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                              chord_verifier=_StubVerifier({B2: C3}))
        matcher.process_detected_notes([_strike(E2, 1000.0, 5000)], 1000.0)
        matcher.process_strike_windows([_window(5000)])
        misses_after_first = matcher.misses
        matcher.process_strike_windows([_window(5000)])
        assert matcher.misses == misses_after_first

    def test_reset_drops_pending_verifications(self):
        timeline = _chord_timeline()
        matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                              chord_verifier=_StubVerifier({B2: C3}))
        matcher.process_detected_notes([_strike(E2, 1000.0, 5000)], 1000.0)
        matcher.reset()
        assert matcher.process_strike_windows([_window(5000)]) == []

    def test_disabling_the_verifier_clears_pending_work(self):
        timeline = _chord_timeline()
        matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                              chord_verifier=_StubVerifier({B2: C3}))
        matcher.process_detected_notes([_strike(E2, 1000.0, 5000)], 1000.0)
        matcher.chord_verifier = None
        assert matcher.process_strike_windows([_window(5000)]) == []

    def test_pending_verifications_are_bounded(self):
        """Windows can go missing (dropped buffers); the map must not grow."""
        timeline = _chord_timeline()
        matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                              chord_verifier=_StubVerifier({}))
        for i in range(200):
            matcher._pending_verifications[i] = []
        matcher.process_strike_windows([_window(5000)])
        assert len(matcher._pending_verifications) <= 32
