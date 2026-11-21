"""The map between the tab's clock and the recording's.

The numbers in here are the player's own: they lined their recording up at
0:00 (-260 ms), 2:57 (-1330 ms) and 4:18 (-260 ms), which is the measurement
that says two points are not enough -- the best straight line through them
corrects by almost nothing and leaves a second standing in the middle.
"""

import pytest

from pickhero.audio.syncmap import (MADE_MAX_RATE, MAX_RATE, MIN_RATE,
                                    SyncMap)

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
        """One point cannot say a rate, so it says the same thing everywhere.

        It used to say -260 here and DISCARD the 99: the stored offset was
        ignored the moment any point existed. That is the fault behind a dead
        offset key, so the nudge is carried now -- but the property this test
        is about, that one point is flat, is unchanged.
        """
        m = SyncMap([(120_000.0, -260.0)], base_offset_ms=99.0)
        assert m.offset_at(0.0) == -161.0
        assert m.offset_at(300_000.0) == -161.0


class TestTheStoredOffsetIsANudgeOnTop:
    """The offset keys were dead on every song that had ever been synced.

    `offset_at` returned the interpolation and dropped `base_offset_ms` on
    the floor as soon as one point existed. The HUD went on printing a number
    the player could change with Shift+N/M while the recording did not move
    -- a feature that cannot be seen working -- and `_set_sync_point` then
    saved that same dead number, so the one press meant to rescue a drifting
    solo would have written an offset unrelated to anything being heard.
    """

    def test_a_nudge_moves_a_synced_map_everywhere(self):
        plain = SyncMap(THEIRS)
        nudged = SyncMap(THEIRS, base_offset_ms=250.0)
        for at in (0.0, 60_000.0, 120_000.0, 400_000.0):
            assert nudged.offset_at(at) == pytest.approx(
                plain.offset_at(at) + 250.0)

    def test_it_moves_the_recording_by_the_same_amount(self):
        plain = SyncMap(THEIRS)
        nudged = SyncMap(THEIRS, base_offset_ms=250.0)
        assert nudged.recording_at(90_000.0) == pytest.approx(
            plain.recording_at(90_000.0) - 250.0)

    def test_and_the_two_directions_still_invert(self):
        """The inverse is read over the points' recording positions, which
        were taken without the nudge -- so it has to be put back."""
        m = SyncMap(THEIRS, base_offset_ms=-175.0)
        for at in (10_000.0, 95_000.0, 240_000.0):
            assert m.song_at(m.recording_at(at)) == pytest.approx(at, abs=1e-6)

    def test_a_zero_nudge_changes_nothing(self):
        plain, zero = SyncMap(THEIRS), SyncMap(THEIRS, base_offset_ms=0.0)
        for at in (0.0, 120_000.0, 400_000.0):
            assert zero.offset_at(at) == plain.offset_at(at)


class TestWhatTheMapWillAdmitToGuessing:
    """Beyond the outermost point the map extrapolates, and the player has to
    be told where that begins -- see the chapter on the recording being synced
    only where somebody listened."""

    def test_it_says_the_span_it_was_measured_over(self):
        m = SyncMap(THEIRS)
        assert m.covers() == (THEIRS[0][0], THEIRS[-1][0])

    def test_one_point_covers_nothing_because_a_point_is_not_a_span(self):
        assert SyncMap([(1000.0, 5.0)]).covers() is None
        assert SyncMap([]).covers() is None

    def test_the_drift_it_saw_is_the_size_of_the_guess(self):
        m = SyncMap(THEIRS)
        offsets = [o for _, o in THEIRS]
        assert m.drift_seen_ms() == pytest.approx(max(offsets) - min(offsets))

    def test_nothing_measured_is_no_drift_rather_than_a_guess(self):
        assert SyncMap([]).drift_seen_ms() == 0.0


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


class TestACorrectionIsASecondLayer:
    """What the player bends by ear, on top of what was measured.

    The measurement that made this necessary: dropped into the anchor list,
    a correction only removes the measured points it CONTRADICTS, and a
    measured point five seconds away differing by 350 ms implies 7 % -- under
    the clamp, so it survives and pulls the correction back. On a bar map
    with a point every five seconds that is a triangle five seconds wide:

        0:15  0 ms | 0:18  210 | 0:20  350 | 0:22  210 | 0:25  0

    The rule before it deleted every measured point within thirty seconds
    instead, which destroyed the map's shape where it was most likely to be
    right. Both are wrong, in opposite directions.
    """

    # A bar map: a point every five seconds, all saying the same thing.
    DENSE = [(float(i) * 5_000.0, -1000.0) for i in range(25)]

    def test_the_measured_map_alone_is_unchanged(self):
        """The control: a song nobody has corrected reads as it always did."""
        plain, layered = SyncMap(THEIRS), SyncMap(THEIRS, corrections=[])
        for at in range(0, 300_000, 1000):
            assert layered.offset_at(at) == plain.offset_at(at)

    def test_a_correction_survives_a_dense_map_around_it(self):
        m = SyncMap(self.DENSE, corrections=[(20_000.0, -350.0)])
        for at in (15_000.0, 20_000.0, 25_000.0, 60_000.0, 120_000.0):
            assert m.offset_at(at) == pytest.approx(-1350.0)

    def test_one_correction_is_the_old_stored_offset(self):
        """A strict generalisation: one correction is a constant over the
        whole song, which is exactly what `base_offset_ms` is."""
        nudged = SyncMap(THEIRS, base_offset_ms=250.0)
        fixed = SyncMap(THEIRS, corrections=[(120_000.0, 250.0)])
        for at in range(0, 300_000, 1000):
            assert fixed.offset_at(at) == pytest.approx(nudged.offset_at(at))

    def test_between_two_of_them_it_is_the_line_joining_them(self):
        m = SyncMap(THEIRS, corrections=[(10_000.0, 350.0),
                                         (70_000.0, 120.0)])
        assert m.correction_at(10_000.0) == pytest.approx(350.0)
        assert m.correction_at(40_000.0) == pytest.approx(235.0)
        assert m.correction_at(70_000.0) == pytest.approx(120.0)

    def test_and_held_outside_them_rather_than_extrapolated(self):
        """A slope fitted to two observed residuals would invent drift the
        map underneath already carries -- and at that rate this one would
        reach zero at 1:01 and keep going."""
        m = SyncMap(THEIRS, corrections=[(10_000.0, 350.0),
                                         (70_000.0, 120.0)])
        assert m.correction_at(0.0) == pytest.approx(350.0)
        assert m.correction_at(250_000.0) == pytest.approx(120.0)

    def test_corrections_with_no_map_are_held_too(self):
        """The case the sentinels were added for: with nothing measured, the
        outermost segment IS a correction, so a plain interpolation
        extrapolated it -- 500 ms read as 1038 ms two minutes later."""
        m = SyncMap([], corrections=[(10_000.0, 500.0), (70_000.0, 300.0)])
        assert m.offset_at(0.0) == pytest.approx(500.0)
        assert m.offset_at(250_000.0) == pytest.approx(300.0)

    def test_but_the_map_still_extrapolates_past_its_last_point(self):
        """Deliberately, and documented: a recording drifting at the last
        point is still drifting after it. A correction inside the map must
        not switch that off."""
        plain = SyncMap(THEIRS)
        fixed = SyncMap(THEIRS, corrections=[(120_000.0, 100.0)])
        assert (fixed.offset_at(400_000.0) - plain.offset_at(400_000.0)
                == pytest.approx(100.0))
        assert plain.offset_at(400_000.0) != pytest.approx(
            plain.offset_at(258_000.0))

    def test_the_inverse_is_still_exact(self):
        """The reason both layers are evaluated into one curve: the recording
        is a piecewise-linear function of song time either way, and the
        round trip has to come back to the millisecond."""
        m = SyncMap(THEIRS, base_offset_ms=-175.0,
                    corrections=[(10_000.0, 350.0), (70_000.0, 120.0)])
        for at in range(0, 300_000, 977):
            assert m.song_at(m.recording_at(at)) == pytest.approx(at, abs=1e-6)

    def test_two_corrections_too_close_cannot_imply_a_wild_rate(self):
        """The same clamp the measured points get, because the total is what
        the picture follows."""
        m = SyncMap([], corrections=[(0.0, 0.0), (1000.0, -900.0)])
        rate, = m.rates()
        assert rate == pytest.approx(MAX_RATE)

    def test_a_correction_counts_as_a_section(self):
        """`rates()` answers what the app is APPLYING -- the run log's
        mp3_sync_sections and the panel's figures -- and the player heard the
        recording drift, which is evidence about it."""
        assert len(SyncMap([], corrections=[(0.0, 0.0),
                                            (100_000.0, -1000.0)]).rates()) == 1

    def test_but_covers_is_still_only_what_was_measured(self):
        """Beyond the outermost MEASURED point the map extrapolates, and the
        panel says so. A correction by ear is not a measurement of the span.
        """
        m = SyncMap(THEIRS, corrections=[(400_000.0, 50.0)])
        assert m.covers() == (0.0, 258_000.0)

    def test_nothing_at_all_is_still_empty(self):
        assert SyncMap([], base_offset_ms=-420.0).empty
        assert not SyncMap([], corrections=[(0.0, 5.0)]).empty


class TestAMadeMapMayHoldAStep:
    """Where the points came from decides how far a segment may bend.

    The tight pair protects against a SEARCH: the listening slides a window
    along the recording and a window that matched the wrong chorus lands
    seconds away, so two points close together imply any rate at all.
    Songsterr's per-bar map cannot make that mistake -- it is written down
    rather than searched for -- so a step in it is music.

    The step that found this is on the player's own Trans-Siberian Orchestra
    file: the tab writes its first ten bars at 3.12 s each where the
    recording plays 2.90, and then holds bar 11 for 6.08 s where the tab
    writes 3.12. Spread at the tight clamp, that demanded three times the
    authority `_follow_recording` has and the picture fell a second behind.
    """

    # 19 s apart, carrying 2.3 s: the shape the simplification leaves of
    # that held bar, and a rate of about 1.19.
    STEP = [(0.0, 0.0), (18_700.0, 745.0), (31_170.0, -1556.0),
            (56_470.0, -1323.0)]

    def test_a_found_map_is_clamped_exactly_as_before(self):
        m = SyncMap(self.STEP)
        assert max(m.rates()) == pytest.approx(MAX_RATE)
        assert min(m.rates()) >= MIN_RATE

    def test_a_made_map_carries_the_step(self):
        m = SyncMap(self.STEP, made=True)
        assert max(m.rates()) == pytest.approx(1.185, abs=0.01)

    def test_and_the_offset_it_reports_is_the_one_measured(self):
        """The clamp is not a detail of the rates: it is what the picture is
        placed by. Halfway across the step the found map is out by most of
        it, and the made one lands on the line between the two points."""
        made = SyncMap(self.STEP, made=True)
        found = SyncMap(self.STEP)
        middle = (18_700.0 + 31_170.0) / 2
        assert made.offset_at(middle) == pytest.approx(-405.5, abs=1.0)
        assert abs(found.offset_at(middle) - made.offset_at(middle)) > 500.0

    def test_a_made_map_is_still_invertible(self):
        """The wider bound may not reach zero: `song_at` is interpolated
        over the points' recording positions, and a segment at rate 0 would
        make the map non-monotone."""
        m = SyncMap(self.STEP, made=True)
        for at in range(0, 60_000, 331):
            assert m.song_at(m.recording_at(at)) == pytest.approx(at, abs=1e-6)

    def test_even_a_map_that_asks_for_the_impossible_stays_a_map(self):
        """Two points a second apart differing by ten seconds is not music,
        whoever made it. The bound still holds, just further out."""
        m = SyncMap([(0.0, 0.0), (1000.0, -10_000.0)], made=True)
        rate, = m.rates()
        assert rate == pytest.approx(MADE_MAX_RATE)
        assert m.song_at(m.recording_at(500.0)) == pytest.approx(500.0,
                                                                 abs=1e-6)

    def test_the_default_is_found(self):
        """A map with no provenance is treated as the riskier kind. Every
        song synced before this flag existed is one of those."""
        assert not SyncMap(self.STEP).made
        assert SyncMap(self.STEP, made=True).made
