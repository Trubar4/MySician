"""The play-along analyser has to find the practice tempo, not assume it.

This is not a nicety. The player's take was recorded at 80 %, the analyser
read it against the written grid, and reported that the detector had heard
22 % of the notes. The same recording, read at the speed it was played,
reads 96 %. A diagnostic that can be wrong by that much about the thing it
exists to measure sends a whole session chasing a problem that is not there.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "analyze_play_along", REPO_ROOT / "tools" / "analyze_play_along.py")
analyze = importlib.util.module_from_spec(_spec)
sys.modules["analyze_play_along"] = analyze
_spec.loader.exec_module(analyze)


def _played(onsets, tempo, start_ms, error_ms=0.0):
    """Strike times for a take of `onsets` played at `tempo`, started late."""
    return [start_ms + onset / tempo + error_ms for onset in onsets]


ONSETS = [i * 600.0 for i in range(24)]


class TestTempoIsMeasured:
    def test_a_take_at_full_speed_reads_as_full_speed(self):
        strikes = _played(ONSETS, 1.0, 2000.0)
        offset, hits, tempo = analyze.best_alignment(strikes, ONSETS)
        assert tempo == 1.0
        assert hits == len(ONSETS)
        assert offset == pytest.approx(2000.0, abs=analyze.ALIGN_STEP_MS)

    def test_a_take_at_eighty_percent_reads_as_eighty_percent(self):
        strikes = _played(ONSETS, 0.8, 3510.0)
        offset, hits, tempo = analyze.best_alignment(strikes, ONSETS)
        assert tempo == 0.8
        assert hits == len(ONSETS)

    def test_the_slowest_speed_the_app_offers_is_found_too(self):
        strikes = _played(ONSETS, 0.5, 1000.0)
        _, hits, tempo = analyze.best_alignment(strikes, ONSETS)
        assert tempo == 0.5
        assert hits == len(ONSETS)

    def test_a_stated_tempo_is_used_instead_of_searching(self):
        strikes = _played(ONSETS, 0.8, 3510.0)
        _, _, tempo = analyze.best_alignment(strikes, ONSETS, tempo=0.8)
        assert tempo == 0.8

    def test_reading_a_slow_take_at_full_speed_loses_most_of_it(self):
        """The failure this test exists to prevent: the first bar lines up
        and everything after it walks away."""
        strikes = _played(ONSETS, 0.8, 3510.0)
        _, hits, _ = analyze.best_alignment(strikes, ONSETS, tempo=1.0)
        assert hits < len(ONSETS) / 2

    def test_a_tie_is_not_won_by_the_most_stretched_grid(self):
        """At 50 % the grid is twice as dense in recording time, so almost any
        strike lands near something. Ties go to the tighter fit instead."""
        strikes = _played(ONSETS, 1.0, 2000.0)
        _, _, tempo = analyze.best_alignment(strikes, ONSETS)
        assert tempo == 1.0
