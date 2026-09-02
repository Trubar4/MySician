"""Finding the sync points by listening, instead of asking for five of them.

The control that matters is the one with NO drift at all: a recording sitting
exactly on the tab's grid must come back saying so. That control is what
caught the analysis window being counted from its first sample rather than its
middle -- a +50 ms shift with only 10 ms of scatter, which is a bias and not
noise, and half the budget of the 100 ms this exists to get under.
"""

import numpy as np
import pytest

from pickhero.audio import autosync
from pickhero.audio.syncmap import SyncMap
from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)

SR = 44100


def _song(seconds=90.0, per_second=4):
    """A tune with enough different pitches to be told apart from itself."""
    rng = np.random.default_rng(7)
    notes, measures = [], []
    step = 1000.0 / per_second
    for i in range(int(seconds * per_second)):
        notes.append(NoteEvent(
            timestamp_ms=i * step, duration_ms=step,
            midi_note=int(40 + rng.integers(0, 24)),
            string=1 + i % 6, fret=i % 12, measure=i // 4))
    for bar in range(int(seconds * per_second / 4)):
        measures.append(MeasureInfo(index=bar, start_ms=bar * 4 * step,
                                    end_ms=(bar + 1) * 4 * step))
    return Timeline(notes, SongMetadata(title="t", tempo=120),
                    measures=measures)


def _recording(timeline, truth: SyncMap, decay=2.0):
    """The same music, played where `truth` says the recording has it."""
    span = timeline.duration_ms / 1000.0 + 5.0
    out = np.zeros(int(span * SR) + SR, dtype=np.float32)
    clock = np.arange(len(out)) / SR
    for note in timeline.notes:
        start = truth.recording_at(note.timestamp_ms) / 1000.0
        stop = start + max(note.duration_ms / 1000.0, 0.2)
        a, b = int(start * SR), int(stop * SR)
        if a < 0 or b > len(out):
            continue
        freq = 440.0 * 2 ** ((note.midi_note - 69) / 12)
        seg = clock[a:b] - clock[a]
        envelope = np.exp(-seg * decay)
        for harmonic, amp in ((1, 1.0), (2, 0.5), (3, 0.3)):
            out[a:b] += (np.sin(2 * np.pi * freq * harmonic * seg)
                         * envelope * amp * 0.1).astype(np.float32)
    return out


def _rows(timeline, truth, decay=2.0):
    rec, fps = autosync.chroma_of_audio(_recording(timeline, truth, decay), SR)
    tab = autosync.chroma_of_timeline(timeline, fps)
    return autosync.drift_curve(tab, rec, fps)


def _signed_errors(timeline, truth, decay=2.0):
    rows = _rows(timeline, truth, decay)
    return [(-lag * 1000.0)
            - truth.offset_at((at + autosync.WINDOW_S / 2) * 1000.0)
            for at, lag in autosync.usable_rows(rows)]


class TestTheControlThatMustReadZero:
    """A recording exactly on the grid. Anything this reports is the
    measurement's own error, and there is nowhere else for it to come from."""

    def test_a_recording_with_no_drift_reads_no_drift(self):
        song = _song()
        errors = _signed_errors(song, SyncMap([(0.0, 0.0), (90_000.0, 0.0)]))
        assert len(errors) >= 5
        assert abs(float(np.median(errors))) < 50.0

    def test_and_the_answer_does_not_depend_on_how_long_notes_ring(self):
        """A shift that changes with the music is the two sides describing
        different stretches of time -- which is what the smoothing fixes."""
        song = SyncMap([(0.0, 0.0), (90_000.0, 0.0)])
        short = float(np.median(_signed_errors(_song(), song, decay=4.0)))
        long_ = float(np.median(_signed_errors(_song(), song, decay=0.5)))
        assert abs(short - long_) < 40.0

    def test_the_scatter_is_small_enough_to_be_worth_reading(self):
        errors = _signed_errors(_song(), SyncMap([(0.0, 0.0), (90_000.0, 0.0)]))
        assert float(np.std(errors)) < 30.0


class TestItFindsADriftThatIsReallyThere:

    def _truth(self):
        # Opposite directions, which is what no single rate can express.
        return SyncMap([(0.0, 0.0), (45_000.0, -450.0), (90_000.0, -150.0)])

    def test_the_points_follow_the_curve(self):
        song = _song()
        truth = self._truth()
        points, rows = (autosync.points_from_rows(_rows(song, truth)), None)
        assert len(points) >= 3
        found = SyncMap(points)
        worst = max(abs(found.offset_at(t) - truth.offset_at(t))
                    for t in range(0, 90_000, 1000))
        assert worst < 200.0

    def test_and_that_is_far_better_than_no_sync_at_all(self):
        song = _song()
        truth = self._truth()
        found = SyncMap(autosync.points_from_rows(_rows(song, truth)))
        nothing = max(abs(truth.offset_at(t)) for t in range(0, 90_000, 1000))
        worst = max(abs(found.offset_at(t) - truth.offset_at(t))
                    for t in range(0, 90_000, 1000))
        assert worst < nothing / 3


class TestTheSubFrameRefinement:
    """Without it the answer can only ever be a whole analysis frame."""

    def test_a_peak_between_two_samples_is_found(self):
        lags = np.array([-2, -1, 0, 1, 2])
        scores = np.array([0.0, 0.6, 1.0, 0.8, 0.0])
        assert 0.0 < autosync._refine(lags, scores, 2) < 0.5

    def test_a_symmetric_peak_stays_where_it_is(self):
        lags = np.array([-1, 0, 1])
        scores = np.array([0.5, 1.0, 0.5])
        assert autosync._refine(lags, scores, 1) == pytest.approx(0.0)

    def test_an_edge_is_left_alone(self):
        lags = np.array([0, 1, 2])
        scores = np.array([1.0, 0.5, 0.2])
        assert autosync._refine(lags, scores, 0) == 0.0

    def test_and_so_is_something_that_is_not_a_peak(self):
        lags = np.array([-1, 0, 1])
        scores = np.array([0.2, 0.3, 0.9])       # still climbing
        assert autosync._refine(lags, scores, 1) == 0.0


class TestWhatIsThrownAway:

    def test_a_window_that_could_not_tell_two_choruses_apart(self):
        rows = [(0.0, 0.0, 0.05), (6.0, 0.1, 0.001), (12.0, 0.2, 0.05),
                (18.0, 0.3, 0.05)]
        kept = autosync.usable_rows(rows)
        assert [at for at, _ in kept] == [0.0, 12.0, 18.0]

    def test_a_window_that_matched_the_wrong_chorus(self):
        rows = [(t, t * 0.001, 0.05) for t in (0.0, 6.0, 12.0, 18.0, 24.0)]
        rows.insert(3, (13.0, 20.0, 0.05))       # twenty seconds away
        kept = autosync.usable_rows(rows)
        assert all(abs(lag) < 1.0 for _, lag in kept)
        assert len(kept) == 5

    def test_too_few_windows_is_no_answer_rather_than_a_bad_one(self):
        assert autosync.usable_rows([(0.0, 0.0, 0.9), (6.0, 0.1, 0.9)]) == []
        assert autosync.points_from_rows([(0.0, 0.0, 0.9)]) == []


class TestThinningKeepsOnlyMeasuredPoints:

    def test_a_straight_line_needs_two_points(self):
        rows = [(t, -t * 0.01, 0.05) for t in range(0, 120, 6)]
        points = autosync.points_from_rows(rows)
        assert len(points) == 2

    def test_a_bend_keeps_the_bend(self):
        def lag(t):
            return -t * 0.01 if t <= 60 else -(120 - t) * 0.01
        rows = [(t, lag(t), 0.05) for t in range(0, 121, 6)]
        points = autosync.points_from_rows(rows)
        assert 3 <= len(points) <= 6
        # the corner is one of the MEASURED places, not an invention
        assert any(abs(at - (60 + autosync.WINDOW_S / 2) * 1000) < 1.0
                   for at, _ in points)

    def test_every_point_is_one_that_was_measured(self):
        rows = [(t, -np.sin(t / 20.0), 0.05) for t in range(0, 180, 6)]
        measured = {round((at + autosync.WINDOW_S / 2) * 1000.0, 1)
                    for at, _, _ in rows}
        assert all(at in measured for at, _ in autosync.points_from_rows(rows))

    def test_the_offset_is_the_negative_of_the_lag(self):
        """A recording that says this passage LATER than the tab has to be
        played from earlier in the file, which is a negative offset here."""
        rows = [(t, 0.5, 0.05) for t in range(0, 60, 6)]
        points = autosync.points_from_rows(rows)
        assert all(off == pytest.approx(-500.0) for _, off in points)
