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


class TestAStatedTempoIsCheckedNotBelieved:
    """The manifest said 80 % for a take played at 100 % and the report came
    back at 13 %, which reads exactly like a detector that has stopped
    working. A tool that reports a number without checking the assumption
    underneath it is measuring itself, and this one had done that once
    already over the very same field."""

    def test_a_wrong_manifest_is_overruled_and_named(self):
        strikes = _played(ONSETS, 1.0, 1900.0)
        tempo, note = analyze.check_tempo(strikes, ONSETS, 0.8)
        assert tempo == pytest.approx(1.0)
        assert "80" in note and "100" in note

    def test_a_right_manifest_is_left_alone(self):
        strikes = _played(ONSETS, 0.8, 1900.0)
        tempo, note = analyze.check_tempo(strikes, ONSETS, 0.8)
        assert tempo == pytest.approx(0.8)
        assert note == ""

    def test_a_take_with_no_stated_tempo_is_still_measured(self):
        """Nothing to check, so nothing to complain about."""
        assert analyze.check_tempo(_played(ONSETS, 0.8, 0.0), ONSETS, None) == (None, "")

    def test_sloppy_playing_does_not_overrule_the_manifest(self):
        """A take played loosely at the stated speed still fits it best, and
        a near-tie must not flap the reading between two speeds."""
        jitter = [40.0 * ((i % 5) - 2) for i in range(len(ONSETS))]
        strikes = [t + j for t, j in zip(_played(ONSETS, 0.8, 1200.0), jitter)]
        tempo, note = analyze.check_tempo(strikes, ONSETS, 0.8)
        assert tempo == pytest.approx(0.8) and note == ""


class TestTheRecorderWritesTheSpeedTheSongIsPlayedAt:
    """`tempo_percent` is what every analysis reads the take against, so the
    recorder writing the wrong one costs the whole take. It wrote the global
    `tempo_factor` after practice speed became a per-song setting."""

    def _recorder(self, tmp_path, monkeypatch, settings):
        import json as _json
        spec = importlib.util.spec_from_file_location(
            "record_reference", REPO_ROOT / "tools" / "record_reference.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["record_reference"] = module
        spec.loader.exec_module(module)
        home = tmp_path / "home"
        (home / ".pickhero").mkdir(parents=True)
        (home / ".pickhero" / "settings.json").write_text(_json.dumps(settings))
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        return module

    def test_the_songs_own_speed_wins_over_the_global_one(self, tmp_path,
                                                          monkeypatch):
        module = self._recorder(tmp_path, monkeypatch, {
            "tempo_factor": 0.8,
            "song_tempo_factors": {"timing_test_100bpm": 0.7}})
        assert module.practice_tempo("timing_test_100bpm.gp5") == pytest.approx(0.7)

    def test_a_song_with_no_entry_opens_at_full_speed(self, tmp_path,
                                                      monkeypatch):
        """This is the bug: the global said 80 %, the app played it at 100 %."""
        module = self._recorder(tmp_path, monkeypatch, {
            "tempo_factor": 0.8, "song_tempo_factors": {"something_else": 0.6}})
        assert module.practice_tempo("timing_test_100bpm.gp5") == pytest.approx(1.0)

    def test_no_settings_file_says_nothing_rather_than_guessing(self, tmp_path,
                                                               monkeypatch):
        module = self._recorder(tmp_path, monkeypatch, {})
        (tmp_path / "home" / ".pickhero" / "settings.json").unlink()
        assert module.practice_tempo("x.gp5") is None
