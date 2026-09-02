"""The map between the tab's clock and the recording's.

The numbers in here are the player's own: they lined their recording up at
0:00 (-260 ms), 2:57 (-1330 ms) and 4:18 (-260 ms), which is the measurement
that says two points are not enough -- the best straight line through them
corrects by almost nothing and leaves a second standing in the middle.
"""

import pytest

from pickhero.audio.syncmap import MAX_RATE, MIN_RATE, SyncMap

THEIRS = [(0.0, -260.0), (177_000.0, -1330.0), (258_000.0, -260.0)]


class TestNoPointsIsTodaysBehaviour:
    """A song nobody has synced must play exactly as it always did."""

    def test_an_empty_map_is_the_stored_offset(self):
        m = SyncMap([], base_offset_ms=-420.0)
        assert m.empty
        assert m.offset_at(0.0) == -420.0
        assert m.offset_at(9_999_999.0) == -420.0
        assert m.recording_at(1000.0) == 1420.0

    def test_and_its_inverse_agrees(self):
        m = SyncMap([], base_offset_ms=-420.0)
        assert m.song_at(m.recording_at(5000.0)) == pytest.approx(5000.0)

    def test_one_point_is_a_constant_offset(self):
        m = SyncMap([(120_000.0, -260.0)], base_offset_ms=99.0)
        assert m.offset_at(0.0) == -260.0
        assert m.offset_at(300_000.0) == -260.0


class TestTheLineBetweenTwoPoints:

    def test_it_passes_through_every_point(self):
        m = SyncMap(THEIRS)
        for song, offset in THEIRS:
            assert m.offset_at(song) == pytest.approx(offset)

    def test_halfway_is_halfway(self):
        m = SyncMap(THEIRS)
        assert m.offset_at(88_500.0) == pytest.approx(-795.0)

    def test_the_two_directions_are_inverses(self):
        m = SyncMap(THEIRS)
        for song in (0.0, 40_000.0, 177_000.0, 220_000.0, 258_000.0):
            assert m.song_at(m.recording_at(song)) == pytest.approx(song)

    def test_the_recording_never_runs_backwards(self):
        """It has to be a clock, whatever points were set."""
        m = SyncMap(THEIRS)
        last = float("-inf")
        for song in range(0, 300_000, 250):
            here = m.recording_at(float(song))
            assert here > last
            last = here

    def test_the_rates_are_the_two_the_player_measured(self):
        m = SyncMap(THEIRS)
        first, second = m.rates()
        assert first == pytest.approx(1.00605, abs=1e-5)
        assert second == pytest.approx(0.98679, abs=1e-5)


class TestPointsSetTooCloseCannotRunAway:
    """A small mistake over a short span implies a wild rate, and the rest of
    the song would then be corrected by minutes."""

    def test_the_slope_is_bounded(self):
        m = SyncMap([(0.0, 0.0), (1000.0, -900.0)])   # implies +90 %
        rate, = m.rates()
        assert rate == pytest.approx(MAX_RATE)

    def test_and_bounded_in_the_other_direction(self):
        m = SyncMap([(0.0, 0.0), (1000.0, 900.0)])
        rate, = m.rates()
        assert rate == pytest.approx(MIN_RATE)

    def test_a_bounded_map_still_inverts(self):
        m = SyncMap([(0.0, 0.0), (1000.0, -900.0)])
        for song in (0.0, 500.0, 5000.0):
            assert m.song_at(m.recording_at(song)) == pytest.approx(song)


class TestOutsideTheOutermostPoints:
    """A recording drifting at the last point is still drifting after it."""

    def test_it_keeps_the_end_segments_slope(self):
        m = SyncMap(THEIRS)
        # 4:18 to 5:00 continues the -1.32 % of the last segment.
        assert m.offset_at(300_000.0) == pytest.approx(-260.0 + 42_000
                                                       * 1070.0 / 81_000.0)

    def test_before_the_first_point_too(self):
        m = SyncMap([(60_000.0, 0.0), (120_000.0, -600.0)])
        assert m.offset_at(0.0) == pytest.approx(600.0)

    def test_and_the_extrapolation_is_bounded_as_well(self):
        m = SyncMap([(0.0, 0.0), (1000.0, -900.0)])
        # An hour later the offset may not have run to minutes.
        assert abs(m.offset_at(3_600_000.0)) <= 3_600_000 * (MAX_RATE - 1.0) + 1


class TestWhatTheScreenAsksIt:

    def test_how_far_out_the_recording_is(self):
        assert SyncMap(THEIRS).worst_correction_ms() == 1330.0

    def test_and_with_no_points_it_is_the_plain_offset(self):
        assert SyncMap([], base_offset_ms=-260.0).worst_correction_ms() == 260.0

    def test_unsorted_points_are_accepted(self):
        m = SyncMap(list(reversed(THEIRS)))
        assert m.offset_at(177_000.0) == pytest.approx(-1330.0)


class TestItBeatsWhatWeHadBefore:
    """The measurement the whole design rests on, run as a test so it cannot
    quietly stop being true: integrated over the drift curve measured on the
    player's own song, more points must mean less error."""

    @staticmethod
    def _truth(t_ms: float) -> float:
        """The offset curve, from the five local rates measured by chroma."""
        centres = [30_000.0, 75_000.0, 120_000.0, 165_000.0, 210_000.0]
        rates = [-0.0104, -0.0052, -0.0070, -0.0177, -0.0140]
        total, step = 0.0, 250.0
        at = 0.0
        while at < t_ms:
            span = min(step, t_ms - at)
            # local rate, linearly between the window centres
            if at <= centres[0]:
                r = rates[0]
            elif at >= centres[-1]:
                r = rates[-1]
            else:
                i = max(i for i in range(len(centres)) if centres[i] <= at)
                f = (at - centres[i]) / (centres[i + 1] - centres[i])
                r = rates[i] + f * (rates[i + 1] - rates[i])
            total += r * span    # a dimensionless rate over ms is ms
            at += span
        return total

    def _worst(self, n_points: int) -> float:
        span = 240_000.0
        points = [(span * i / (n_points - 1), self._truth(span * i / (n_points - 1)))
                  for i in range(n_points)]
        m = SyncMap(points)
        return max(abs(self._truth(t) - m.offset_at(t))
                   for t in range(0, 240_000, 1000))

    def test_more_points_is_always_at_least_as_good(self):
        errors = [self._worst(n) for n in (2, 3, 5, 9)]
        assert errors == sorted(errors, reverse=True)

    def test_five_points_are_under_the_audible_threshold(self):
        """100 ms is where picture and sound stop reading as one event --
        the same number check_song_sync.py judges a whole song by."""
        assert self._worst(5) < 100.0

    def test_and_two_points_are_not(self):
        """Which is why this was rebuilt: two points is the straight line the
        app shipped, and it leaves a quarter of a second."""
        assert self._worst(2) > 200.0
