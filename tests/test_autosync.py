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


class TestASongThatRepeatsItself:
    """Godsmack's "Awake": a metal song whose riffs recur, so a window
    regularly matches the WRONG repeat. The lags came back in plateaus about
    8 s apart, stepping from +10.9 s down to -17.9 s over the song -- and a
    straight line fits that staircase at -12 % perfectly, so the outlier
    filter found nothing to drop and all nineteen were stored as sync points.
    The app then snapped the picture 4.8 s three times to follow them.

    A median is robust to a MINORITY of wrong readings. Whole clusters, each
    consistent with the next, are a majority.
    """

    def _staircase(self):
        """The shape the real song produced: plateaus a repeat-length apart,
        walking one way, with a small real drift inside each."""
        rows = []
        for i in range(24):
            at = i * 6.0
            plateau = -8.4 * (i // 4)          # a wrong repeat, every so often
            rows.append((at, 10.0 + plateau + at * 0.005, 0.05))
        return rows

    def test_the_fitted_drift_cannot_be_something_no_recording_does(self):
        rows = self._staircase()
        kept = autosync.usable_rows(rows)
        xs = [a for a, _ in kept]
        ys = [b for _, b in kept]
        slope, _ = autosync._robust_line(xs, ys)
        assert abs(slope) <= autosync.MAX_DRIFT_RATE

    def test_only_one_plateau_survives(self):
        """They cannot all be right: they disagree by whole repeat lengths."""
        kept = autosync.usable_rows(self._staircase())
        lags = [b for _, b in kept]
        assert lags, "everything was thrown away"
        assert max(lags) - min(lags) < 3.0, lags

    def test_and_it_is_the_one_most_windows_agree_on(self):
        kept = autosync.usable_rows(self._staircase())
        lags = [b for _, b in kept]
        # The first plateau holds four of the six groups' worth of readings.
        assert 9.0 < sum(lags) / len(lags) < 11.0

    def test_a_real_one_percent_drift_is_still_followed(self):
        """The bound must not eat the thing this exists to measure."""
        rows = [(i * 6.0, i * 6.0 * 0.01, 0.05) for i in range(30)]
        kept = autosync.usable_rows(rows)
        assert len(kept) == len(rows)
        slope, _ = autosync._robust_line([a for a, _ in kept],
                                         [b for _, b in kept])
        assert slope == pytest.approx(0.01, abs=0.002)

    def test_two_windows_too_close_together_say_nothing_about_a_rate(self):
        """The offset moves in fractions of a second and the noise is
        comparable, so a pair five seconds apart implies any rate at all."""
        rows = [(0.0, 0.0, 0.05), (5.0, 2.0, 0.05), (10.0, 0.1, 0.05),
                (60.0, 0.2, 0.05), (120.0, 0.3, 0.05)]
        slope, _ = autosync._robust_line([r[0] for r in rows],
                                         [r[1] for r in rows])
        assert abs(slope) <= autosync.MAX_DRIFT_RATE


class TestASpikeInAnOtherwiseGoodCurve:
    """Bad Omens, "Like a Villain". The recording really does drift against
    the tab -- 0.17 %, smooth, nine of the twelve stored points within 52 ms
    of the line. The other three sit 175, 561 and 1349 ms off it, which is 4,
    13 and 31 times the scatter and comfortably inside the three seconds the
    filter allowed. Each spike poisons the two sections around it: the stored
    map implied +22 %, -7.3 % and +9.3 %, one of them clamped at MAX_RATE.

    The wrong-chorus case is tens of seconds away, so the threshold fitted
    against it cannot see this at all.
    """

    def _like_a_villain(self):
        """The twelve points the app stored, read off the player's run log."""
        return [(10, -449), (28, -1767), (34, -444), (52, -326), (64, -344),
                (70, 215), (88, -368), (112, -260), (118, -89), (166, -203),
                (172, -172), (196, -183)]

    def _rows(self):
        return [(float(t), lag / 1000.0, 0.05)
                for t, lag in self._like_a_villain()]

    def test_the_three_spikes_are_dropped(self):
        kept = [int(at) for at, _ in autosync.usable_rows(self._rows())]
        assert kept == [10, 34, 52, 64, 88, 112, 166, 172, 196]

    def test_and_the_drift_that_is_really_there_survives(self):
        kept = autosync.usable_rows(self._rows())
        slope, _ = autosync._robust_line([a for a, _ in kept],
                                         [b for _, b in kept])
        assert slope == pytest.approx(0.0017, abs=0.0005)

    def test_what_the_spikes_cost_on_the_map(self):
        """The number that makes this worth fixing: a jump the size of a bar
        against an error nobody can see."""
        from pickhero.audio.syncmap import SyncMap

        clean = [(t, off) for t, off in self._like_a_villain()
                 if t not in (28, 70, 118)]
        xs = np.array([t for t, _ in clean], float)
        ys = np.array([o for _, o in clean], float)
        a, b = np.polyfit(xs, ys, 1)

        def worst(points):
            m = SyncMap([(t * 1000.0, off) for t, off in points], 0.0)
            return max(abs(m.offset_at(t * 1000.0) - (a * t + b))
                       for t in np.arange(0, 205, 0.5))

        assert worst(self._like_a_villain()) > 1300.0
        assert worst(clean) < 100.0

    def test_a_song_whose_windows_agree_exactly_loses_nothing(self):
        """The floor. Without it the tolerance collapses onto a scatter of
        nothing and the least-agreeing window of a clean song is thrown away
        for disagreeing by a millisecond."""
        rows = [(i * 6.0, 0.5 + (i % 3) * 0.001, 0.05) for i in range(20)]
        assert len(autosync.usable_rows(rows)) == len(rows)

    def test_and_the_ceiling_still_holds_for_a_scattered_song(self):
        """A song whose windows scatter by seconds must not have its
        tolerance grow to match: a wrong chorus is what that would admit."""
        assert autosync.spike_tolerance([-4.0, 4.0, -4.0, 4.0]) \
            == autosync.OUTLIER_S
