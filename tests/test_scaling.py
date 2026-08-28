"""Work that grew with the length of the song.

Reported as the picture and the sound stuttering now and then WHILE playing,
worse the longer the song had been running. Both causes were the same shape:
a loop that started at the beginning of the song every time it ran, so its
cost grew with how far the player had got -- and both are asked once per
STRIKE, which is why it arrived in bursts exactly when the hands were busiest.
"""

import time

import pytest

from pickhero.audio.detector import DetectedNote
from pickhero.audio.input import TimestampedNote
from pickhero.matcher import MatchType, NoteMatcher
from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline


def _dense(bars=1200):
    """Sixteenths on three strings -- a real metal arrangement's density."""
    notes = []
    t = 0.0
    for i in range(bars):
        for string in (1, 2, 3):
            notes.append(NoteEvent(timestamp_ms=t, duration_ms=100.0,
                                   midi_note=40 + string * 5, string=string,
                                   fret=i % 13, measure=i // 16))
        t += 111.0
    return Timeline(notes, SongMetadata(title="dense", tempo=135))


def _strike(ms, midi=45):
    return TimestampedNote(
        note=DetectedNote(midi, 100.0, 0.95, "x", True), timestamp_ms=ms)


class TestFindingWhatIsSounding:
    def test_it_does_not_get_slower_the_further_in_you_are(self):
        """It scanned from the first note of the song every time, and the
        matcher asks it up to five times per strike."""
        timeline = _dense()
        def cost(at):
            best = 1e9
            for _ in range(3):
                start = time.perf_counter()
                for _ in range(300):
                    timeline.get_active_notes_at_time(at, 200.0)
                best = min(best, time.perf_counter() - start)
            return best
        early, late = cost(5_000.0), cost(120_000.0)
        assert late < early * 3

    def test_it_still_finds_a_note_that_is_still_ringing(self):
        """The bound is the longest note in the song, not a guess."""
        long_note = NoteEvent(timestamp_ms=1000.0, duration_ms=4000.0,
                              midi_note=40, string=6, fret=0, measure=0)
        later = NoteEvent(timestamp_ms=4800.0, duration_ms=100.0,
                          midi_note=45, string=5, fret=0, measure=0)
        timeline = Timeline([long_note, later])
        found = timeline.get_active_notes_at_time(4900.0, 100.0)
        assert long_note in found and later in found

    def test_a_note_that_has_finished_is_not_returned(self):
        note = NoteEvent(timestamp_ms=0.0, duration_ms=100.0, midi_note=40,
                         string=6, fret=0, measure=0)
        timeline = Timeline([note])
        assert timeline.get_active_notes_at_time(5000.0, 100.0) == []


class TestHowLongTheSongIs:
    def test_it_is_not_recomputed_over_every_note_each_time(self):
        timeline = _dense()
        start = time.perf_counter()
        for _ in range(20_000):
            timeline.duration_ms
        assert (time.perf_counter() - start) < 0.5

    def test_it_is_still_the_last_note_to_stop_sounding(self):
        notes = [NoteEvent(timestamp_ms=0.0, duration_ms=9000.0, midi_note=40,
                           string=6, fret=0, measure=0),
                 NoteEvent(timestamp_ms=1000.0, duration_ms=100.0, midi_note=45,
                           string=5, fret=0, measure=0)]
        assert Timeline(notes).duration_ms == pytest.approx(9000.0)

    def test_a_song_with_no_notes_is_no_seconds_long(self):
        assert Timeline([]).duration_ms == 0.0


class TestSweepingUpMissedNotes:
    def test_a_strike_late_in_the_song_costs_no_more_than_an_early_one(self):
        """It re-judged every note from the start of the song on every
        strike -- thousands of state lookups per note played."""
        timeline = _dense()
        def cost(at):
            matcher = NoteMatcher(timeline, timing_window_ms=200.0)
            matcher._mark_missed_notes(at)          # catch up, as a run does
            best = 1e9
            for _ in range(3):
                start = time.perf_counter()
                for i in range(200):
                    ms = at + i * 111.0
                    matcher.process_detected_notes([_strike(ms)], ms)
                best = min(best, time.perf_counter() - start)
            return best
        assert cost(120_000.0) < cost(5_000.0) * 3

    def test_a_note_gone_past_is_still_marked_missed(self):
        timeline = _dense(bars=20)
        matcher = NoteMatcher(timeline, timing_window_ms=200.0)
        matcher.process_detected_notes([], 3000.0)
        first = timeline.notes[0]
        assert matcher.get_note_state(first) is MatchType.MISS

    def test_notes_go_on_being_marked_as_the_song_runs(self):
        """The mark must advance, not stop the sweep after the first look."""
        timeline = _dense(bars=20)
        matcher = NoteMatcher(timeline, timing_window_ms=200.0)
        matcher.process_detected_notes([], 1000.0)
        matcher.process_detected_notes([], 2200.0)
        later = [n for n in timeline.notes if n.timestamp_ms > 1200.0][0]
        assert matcher.get_note_state(later) is MatchType.MISS

    def test_a_reset_makes_it_look_at_everything_again(self):
        """After a seek every note is PENDING, so the mark has to go back."""
        timeline = _dense(bars=20)
        matcher = NoteMatcher(timeline, timing_window_ms=200.0)
        matcher.process_detected_notes([], 3000.0)
        matcher.reset()
        assert matcher._missed_swept_ms == 0.0
        matcher.process_detected_notes([], 3000.0)
        assert matcher.get_note_state(timeline.notes[0]) is MatchType.MISS

    def test_the_song_moving_backwards_without_a_reset_is_survived(self):
        timeline = _dense(bars=20)
        matcher = NoteMatcher(timeline, timing_window_ms=200.0)
        matcher.process_detected_notes([], 3000.0)
        matcher._note_states.clear()             # as if it were fresh
        matcher.process_detected_notes([], 500.0)
        matcher.process_detected_notes([], 3000.0)
        assert matcher.get_note_state(timeline.notes[0]) is MatchType.MISS
