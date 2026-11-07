"""The run log read the way a teacher reads a recording.

The measurement this rests on: across three complete runs of two real songs,
only 8-9 % of the notes the app marked missed were a clean reading of a wrong
pitch. Two thirds were subharmonic -- an arpeggio read as one note, which is
the detector's known blind spot -- so a report that simply listed the weakest
bars would spend most of its advice on passages that were played correctly.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import coach                                            # noqa: E402

HEAD = """# MySician run log
song\tA Song
notes_written\t8
notes_reached\t8
notes_not_reached\t0
reached_ms\t8000
song_ms\t8000
played_to_the_end\tTrue
tempo_percent\t100
hit_window_ms\t200
late_window_ms\t440
dropped_buffers\t0
level_loudest_db\t-12.0
level_under_gate_percent\t0
hits\t4
"""

STRIKES = ("\n# every strike the audio thread produced\n"
           "strike_ms\tadjusted_ms\tplayback_ms\tmidi\tconf"
           "\tunpitched\tsubharm\toutcome\tnote_ms\tsemitones\n")

NOTES = ("\n# every written note and how it ended up\n"
         "note_ms\tbar\tstring\tfret\tmidi\ttech\tchord\tverdict\n")


def _log(tmp_path, strikes=(), notes=(), head=HEAD) -> coach.Run:
    text = head + STRIKES
    for at, midi, unpitched, subharm in strikes:
        text += (f"{at:.1f}\t{at:.1f}\t{at:.1f}\t{midi}\t0.90"
                 f"\t{int(unpitched)}\t{int(subharm)}\tmatched\t\t\n")
    text += NOTES
    for at, bar, string, fret, midi, tech, chord, verdict in notes:
        text += (f"{at:.1f}\t{bar}\t{string}\t{fret}\t{midi}\t{tech}"
                 f"\t{chord}\t{verdict}\n")
    path = tmp_path / "run.txt"
    path.write_text(text, encoding="utf-8")
    return coach.Run(path)


class TestWhyANoteFailed:
    """Five answers, and only two of them are about the playing."""

    def _one(self, tmp_path, strikes):
        run = _log(tmp_path, strikes,
                   [(1000.0, 1, 6, 3, 40, "-", 1, "miss")])
        return run.why(run.notes[0])

    def test_nothing_was_struck(self, tmp_path):
        assert self._one(tmp_path, []) == "nothing was struck"

    def test_and_a_strike_far_outside_the_window_is_the_same(self, tmp_path):
        assert self._one(tmp_path, [(5000.0, 40, False, False)]) \
            == "nothing was struck"

    def test_a_strike_with_no_pitch(self, tmp_path):
        assert self._one(tmp_path, [(1010.0, 0, True, False)]) \
            == "struck, no pitch"

    def test_a_subharmonic_is_not_a_reading_of_the_note(self, tmp_path):
        assert self._one(tmp_path, [(1010.0, 28, False, True)]) \
            == "struck, unreadable"

    def test_a_clean_wrong_pitch_names_the_interval(self, tmp_path):
        assert self._one(tmp_path, [(1010.0, 43, False, False)]) \
            == "wrong pitch (+3)"

    def test_an_octave_is_not_a_mistake(self, tmp_path):
        assert self._one(tmp_path, [(1010.0, 52, False, False)]) == "octave"

    def test_only_two_of_them_are_about_the_playing(self):
        about_playing = [r for r in ("nothing was struck", "wrong pitch (+3)",
                                     "struck, no pitch", "struck, unreadable",
                                     "octave")
                         if r.startswith(coach.PLAYER_FAULTS)]
        assert about_playing == ["nothing was struck", "wrong pitch (+3)"]


class TestAPassageIsNamedByItsBars:

    def _run(self, tmp_path):
        notes, strikes = [], []
        for i in range(40):
            bar = 1 + i // 4
            at = 500.0 * i
            # bars 4 and 5 are played badly and READABLY so
            bad = bar in (4, 5)
            notes.append((at, bar, 6, 3 + bar, 40 + bar, "-", 1,
                          "miss" if bad else "hit"))
            strikes.append((at + 10.0, 40 + bar + (5 if bad else 0),
                            False, False))
        return _log(tmp_path, strikes, notes)

    def test_the_weak_bars_come_out_as_one_passage(self, tmp_path):
        found = coach.passages(self._run(tmp_path))
        assert found and found[0]["bars"] == [4, 5]

    def test_and_it_says_which_frets(self, tmp_path):
        assert coach.passages(self._run(tmp_path))[0]["frets"] == (7, 8)

    def test_a_passage_nothing_could_be_read_in_says_so(self, tmp_path):
        """The presumption of innocence, one level up: a passage whose
        failures are all unreadable is not a passage that was played badly."""
        notes, strikes = [], []
        for i in range(40):
            bar, at = 1 + i // 4, 500.0 * i
            bad = bar in (4, 5)
            notes.append((at, bar, 6, 3, 40, "-", 1, "miss" if bad else "hit"))
            strikes.append((at + 10.0, 28, False, True))   # subharmonic
        run = _log(tmp_path, strikes, notes)
        weak = coach.passages(run)[0]
        assert weak["readable"] == 0
        assert "Mostly unreadable here" in coach.report(run)


class TestWhatMakesARunUnreadable:
    """A wrong input device makes every other number in a log meaningless,
    so it is asked first and reported before anything else."""

    def test_a_room_microphone(self, tmp_path):
        head = HEAD + ("input_hears_the_room\tyes\t(room -58.5 dB)\n"
                       "input_device\tMikrofonarray\n")
        run = _log(tmp_path, [], [], head=head)
        assert any("hears the room" in d for d in coach.trustworthy(run))

    def test_a_gate_that_ate_the_audio(self, tmp_path):
        head = HEAD.replace("level_under_gate_percent\t0",
                            "level_under_gate_percent\t99")
        assert any("noise gate" in d
                   for d in coach.trustworthy(_log(tmp_path, [], [], head)))

    def test_an_input_too_quiet_to_read(self, tmp_path):
        head = HEAD.replace("level_loudest_db\t-12.0",
                            "level_loudest_db\t-44.0")
        assert any("peaks at" in d
                   for d in coach.trustworthy(_log(tmp_path, [], [], head)))

    def test_a_measurement_the_log_does_not_have(self, tmp_path):
        """The log prints a word where it has no number, which is the whole
        reason a missing measurement is visible -- so every reader has to
        expect one."""
        head = HEAD.replace("level_loudest_db\t-12.0",
                            "level_loudest_db\t(nothing measured)")
        assert coach.trustworthy(_log(tmp_path, [], [], head)) == []

    def test_a_clean_run_raises_nothing(self, tmp_path):
        assert coach.trustworthy(_log(tmp_path, [], [])) == []


class TestAnOlderLogIsStillWorthReading:

    def test_it_is_read_without_the_new_columns(self, tmp_path):
        text = (HEAD + STRIKES
                + "\n# every written note and how it ended up\n"
                + "note_ms\tstring\tmidi\tverdict\n"
                + "1000.0\t6\t40\tmiss\n2000.0\t5\t45\thit\n")
        path = tmp_path / "old.txt"
        path.write_text(text, encoding="utf-8")
        run = coach.Run(path)
        assert len(run.notes) == 2
        assert run.notes[0]["bar"] is None

    def test_and_says_it_cannot_name_a_passage_rather_than_naming_one(
            self, tmp_path):
        text = (HEAD + STRIKES
                + "\n# every written note and how it ended up\n"
                + "note_ms\tstring\tmidi\tverdict\n"
                + "".join(f"{i * 500}.0\t6\t40\tmiss\n" for i in range(40)))
        path = tmp_path / "old.txt"
        path.write_text(text, encoding="utf-8")
        assert "no bar numbers" in coach.report(coach.Run(path))


class TestASongWithNoBarsToName:
    """A tab that parsed without measure info has no bars, and a "0" there
    would read as one."""

    def test_a_dash_is_read_as_no_bar_at_all(self, tmp_path):
        text = (HEAD + STRIKES + NOTES + "1000.0\t-\t6\t0\t40\t-\t1\tmiss\n")
        path = tmp_path / "run.txt"
        path.write_text(text, encoding="utf-8")
        assert coach.Run(path).notes[0]["bar"] is None


class TestAWrongPitchNoHandCouldHaveMade:
    """Measured over two complete runs of one song, 108 clean readings of a
    wrong pitch: the 69 within five semitones are read at a median confidence
    of 1.000 with 1 % below 0.95, while the 39 further out sit at 0.990 with
    31 % below -- and their intervals are -14, -17, -19, -21, which nobody
    fingers by accident. They are a lower string still ringing under the
    note. Counted as mistakes they were a third of the advice.
    """

    def _why(self, tmp_path, played_midi):
        run = _log(tmp_path, [(1010.0, played_midi, False, False)],
                   [(1000.0, 1, 6, 3, 40, "-", 1, "miss")])
        return run.why(run.notes[0])

    @pytest.mark.parametrize("semitones", [-5, -3, -1, 1, 2, 5])
    def test_within_a_hand_it_is_the_playing(self, tmp_path, semitones):
        why = self._why(tmp_path, 40 + semitones)
        assert why == f"wrong pitch ({semitones:+d})"
        assert why.startswith(coach.PLAYER_FAULTS)

    @pytest.mark.parametrize("semitones", [-21, -19, -14, -7, 6, 8, 11])
    def test_beyond_it_it_is_not(self, tmp_path, semitones):
        why = self._why(tmp_path, 40 + semitones)
        assert why == "struck, out of reach"
        assert not why.startswith(coach.PLAYER_FAULTS)

    def test_an_octave_is_still_its_own_answer(self, tmp_path):
        """It is green on screen and is the commonest reading this detector
        produces, so it must not become "out of reach" on the way past."""
        assert self._why(tmp_path, 52) == "octave"
        assert self._why(tmp_path, 28) == "octave"

    def test_the_boundary_is_a_hand_and_a_string(self, tmp_path):
        """Five semitones is about a hand's span AND the gap between two
        adjacent strings, so it covers a fret slip and a string slip."""
        assert coach.REACH_SEMITONES == 5


# ── Several logs at once ────────────────────────────────────────────────────

def _dated(tmp_path, name, strikes=(), notes=(), head=HEAD):
    """A log under a real run-log filename, so the day can be read off it."""
    text = head + STRIKES
    for at, midi, unpitched, subharm in strikes:
        text += (f"{at:.1f}\t{at:.1f}\t{at:.1f}\t{midi}\t1.00"
                 f"\t{int(unpitched)}\t{int(subharm)}\tmatched\t\t\n")
    text += NOTES
    for at, bar, string, fret, midi, tech, chord, verdict in notes:
        text += (f"{at:.1f}\t{bar}\t{string}\t{fret}\t{midi}\t{tech}"
                 f"\t{chord}\t{verdict}\n")
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return coach.Run(path)


class TestTheDayComesFromTheName:
    """A log that travelled on a USB stick has a new mtime and the old name."""

    def test_the_stamp_is_read_out_of_the_filename(self):
        assert coach.played_day(
            "run_Bon_Jovi___I_d_Die_For_You_20260902_203417.txt") \
            == "2026-09-02"

    def test_a_song_whose_name_holds_digits_is_not_mistaken_for_a_date(self):
        # Shinedown's "45" and "Papa Roach - 20 Something" are real titles.
        assert coach.played_day("run_Shinedown___45_20260906_182919.txt") \
            == "2026-09-06"

    def test_a_name_with_no_stamp_says_so_rather_than_guessing(self):
        assert coach.played_day("whatever.txt") is None


class TestTheWindowIsDaysNotFiles:
    """One busy evening must not swallow the whole window.

    The player's own folder has eleven logs from 2026-09-01 and one from
    2026-08-21: counted as files, "the last twenty" would be one evening
    and a bit.
    """

    def test_an_evening_of_many_logs_counts_as_one_day(self, tmp_path):
        names = [f"run_S_20260901_18000{i}.txt" for i in range(9)]
        names += ["run_S_20260902_180000.txt", "run_S_20260903_180000.txt"]
        for name in names:
            (tmp_path / name).write_text("x", encoding="utf-8")
        kept = coach.recent_days([tmp_path / n for n in names], 2)
        days = {coach.played_day(p) for p in kept}
        assert days == {"2026-09-02", "2026-09-03"}
        assert len(kept) == 2            # the nine-log evening is left out

    def test_zero_means_every_day_there_is(self, tmp_path):
        names = ["run_S_20260901_180000.txt", "run_S_20260902_180000.txt"]
        for name in names:
            (tmp_path / name).write_text("x", encoding="utf-8")
        assert len(coach.recent_days([tmp_path / n for n in names], 0)) == 2


class TestTheSameSittingWrittenTwice:
    """D is not in App.NEVER_REPEAT, so one run can produce many files.

    Measured on the player's own folder: eight files of 19 294 bytes within
    twelve seconds, five of 57 093, three of 50 032. Counted as evenings,
    one sitting would weigh eight.
    """

    def _pair(self, tmp_path, first, second):
        strikes = [(1000.0, 60, 0, 0), (2000.0, 62, 0, 0), (3000.0, 64, 0, 0)]
        a = _dated(tmp_path, "run_S_20260901_180000.txt", strikes[:first])
        b = _dated(tmp_path, "run_S_20260901_180005.txt", strikes[:second])
        return coach.deduplicate([a, b])

    def test_a_prefix_collapses_and_the_longest_survives(self, tmp_path):
        kept, dropped = self._pair(tmp_path, 3, 1)
        assert dropped == 1
        assert len(kept) == 1
        assert len(kept[0].strikes) == 3

    def test_an_identical_pair_collapses_too(self, tmp_path):
        # The commonest shape: the key pressed twice, nothing new heard.
        kept, dropped = self._pair(tmp_path, 3, 3)
        assert (len(kept), dropped) == (1, 1)

    def test_two_different_sittings_are_both_kept(self, tmp_path):
        a = _dated(tmp_path, "run_S_20260901_180000.txt",
                   [(1000.0, 60, 0, 0), (2000.0, 62, 0, 0)])
        b = _dated(tmp_path, "run_S_20260902_180000.txt",
                   [(1000.0, 67, 0, 0), (2000.0, 69, 0, 0)])
        kept, dropped = coach.deduplicate([a, b])
        assert (len(kept), dropped) == (2, 0)

    def test_two_logs_with_no_strikes_are_never_collapsed(self, tmp_path):
        """Nothing in them can show they are the same evening.

        These are the 2 kB files where the microphone was dead, and throwing
        a sitting away on no evidence is the fault this whole tool is built
        to avoid.
        """
        a = _dated(tmp_path, "run_S_20260901_180000.txt")
        b = _dated(tmp_path, "run_S_20260902_180000.txt")
        kept, dropped = coach.deduplicate([a, b])
        assert (len(kept), dropped) == (2, 0)

    def test_songs_are_never_mixed(self, tmp_path):
        strikes = [(1000.0, 60, 0, 0)]
        a = _dated(tmp_path, "run_A_20260901_180000.txt", strikes,
                   head=HEAD.replace("song\tA Song", "song\tA"))
        b = _dated(tmp_path, "run_B_20260901_180005.txt", strikes,
                   head=HEAD.replace("song\tA Song", "song\tB"))
        kept, dropped = coach.deduplicate([a, b])
        assert (len(kept), dropped) == (2, 0)


class TestRunsAreBandedSoNothingIsAveragedAcrossACodeChange:
    """Eleven weeks of this project is not one instrument.

    onset_threshold went 0.30 -> 0.05 (+296 notes on one song), the audio
    anchor was fixed (29 % -> 40 %), the gate stopped discarding 40 % of the
    audio. A pattern computed across that is the app improving.
    """

    def _run(self, tmp_path, day, build):
        return _dated(tmp_path, f"run_S_{day}_180000.txt",
                      head=HEAD.replace("song\tA Song",
                                        f"build\t{build}\nsong\tA Song"))

    def test_a_build_change_starts_a_new_band(self, tmp_path):
        runs = [self._run(tmp_path, "20260901", "aaaa"),
                self._run(tmp_path, "20260902", "bbbb")]
        assert len(coach.bands(runs)) == 2

    def test_a_long_gap_starts_one_too_even_with_no_build_recorded(
            self, tmp_path):
        """Most of this history carries no build at all.

        Every EXE the release workflow produced before it learnt to stamp
        reports "unknown build", so for those logs only the calendar can
        separate September from November.
        """
        runs = [self._run(tmp_path, "20260917", "unknown build"),
                self._run(tmp_path, "20261106", "unknown build")]
        assert len(coach.bands(runs)) == 2

    def test_but_a_short_gap_does_not(self, tmp_path):
        runs = [self._run(tmp_path, "20260901", "unknown build"),
                self._run(tmp_path, "20260910", "unknown build")]
        bands = coach.bands(runs)
        assert len(bands) == 1
        assert len(bands[0]["runs"]) == 2

    def test_the_gap_is_measured_in_calendar_days_across_a_month_end(
            self, tmp_path):
        # 31 Aug to 10 Sep is ten days, not seventy-nine.
        runs = [self._run(tmp_path, "20260831", "unknown build"),
                self._run(tmp_path, "20260910", "unknown build")]
        assert len(coach.bands(runs)) == 1


class TestTheDenominatorLeavesOutWhatNothingCouldRead:
    """A note whose failure the detector could not read is not a mistake.

    Counting it as one convicts on absence of evidence; counting it as a
    success flatters. It is set aside from BOTH sides and said out loud --
    the presumption of innocence this project runs on, applied to the
    arithmetic.
    """

    def _one(self, tmp_path, verdict, strike):
        notes = [(1000.0, 1, 3, 12, 49, "-", 1, verdict)]
        run = _dated(tmp_path, "run_S_20260901_180000.txt", strike, notes)
        found = coach.patterns([run])
        return found["tally"]["Where on the neck"]["frets 12+"]

    def test_a_clean_wrong_pitch_is_a_fault(self, tmp_path):
        cell = self._one(tmp_path, "miss", [(1000.0, 48, 0, 0)])
        assert (cell["faults"], cell["good"], cell["unreadable"]) == (1, 0, 0)

    def test_a_subharmonic_is_set_aside(self, tmp_path):
        cell = self._one(tmp_path, "miss", [(1000.0, 30, 0, 1)])
        assert (cell["faults"], cell["good"], cell["unreadable"]) == (0, 0, 1)

    def test_a_pitchless_strike_is_set_aside(self, tmp_path):
        cell = self._one(tmp_path, "miss", [(1000.0, 0, 1, 0)])
        assert (cell["faults"], cell["good"], cell["unreadable"]) == (0, 0, 1)

    def test_nothing_struck_at_all_is_a_fault(self, tmp_path):
        cell = self._one(tmp_path, "miss", [])
        assert (cell["faults"], cell["good"], cell["unreadable"]) == (1, 0, 0)

    def test_a_hit_counts_in_the_denominator(self, tmp_path):
        cell = self._one(tmp_path, "hit", [(1000.0, 49, 0, 0)])
        assert (cell["faults"], cell["good"], cell["unreadable"]) == (0, 1, 0)

    def test_a_pending_note_is_in_no_group_at_all(self, tmp_path):
        run = _dated(tmp_path, "run_S_20260901_180000.txt", [],
                     [(1000.0, 1, 3, 12, 49, "-", 1, "pending")])
        assert coach.patterns([run])["tally"]["Where on the neck"] == {}


class TestAPlantedWeaknessIsFound:
    """The control: a fault put in on purpose has to come out again.

    Without this the whole class passes on a report that groups correctly
    and finds nothing.
    """

    def _runs(self, tmp_path, bad_string, bad_from_fret):
        strikes, notes = [], []
        for i in range(240):
            # Independent, or the two cycles interfere: `1 + i % 6` is even
            # exactly when `i % 4` is odd, so string 3 never once met fret 14
            # and the planted fault was not in the fixture at all.
            string = 1 + (i % 6)
            fret = (0, 3, 7, 14)[(i // 6) % 4]
            midi = 40 + string * 3 + fret
            bad = string == bad_string and fret >= bad_from_fret
            ms = 500.0 + i * 400
            strikes.append((ms, midi - 1 if bad else midi, 0, 0))
            notes.append((ms, 1 + i // 8, string, fret, midi, "-", 1,
                          "miss" if bad else "hit"))
        return [_dated(tmp_path, "run_S_20260901_180000.txt", strikes, notes)]

    def test_the_string_it_was_planted_on_stands_out(self, tmp_path):
        found = coach.patterns(self._runs(tmp_path, 3, 14))
        rates = {label: coach._rate(cell)
                 for label, cell in found["tally"]["Which string"].items()}
        # One fret of the four is planted bad, so exactly a quarter of the
        # notes on that string fail -- asserted as the number the fixture
        # really implies rather than as a comfortable threshold.
        assert rates["string 3"] == 0.25
        assert all(rate == 0 for label, rate in rates.items()
                   if label != "string 3")

    def test_so_does_the_fret_region(self, tmp_path):
        found = coach.patterns(self._runs(tmp_path, 3, 14))
        rates = {label: coach._rate(cell)
                 for label, cell in found["tally"]["Where on the neck"].items()}
        assert rates["frets 12+"] > rates["frets 5-11"]

    def test_and_the_interval_names_which_way_the_hand_went(self, tmp_path):
        found = coach.patterns(self._runs(tmp_path, 3, 14))
        cell = found["tally"]["Which string"]["string 3"]
        assert list(cell["intervals"]) == ["-1"]

    def test_the_report_marks_it_as_worse_than_its_band(self, tmp_path):
        text = coach.pattern_report(self._runs(tmp_path, 3, 14), 0)
        assert "string 3" in text
        line = next(l for l in text.splitlines() if "string 3" in l)
        assert "worse than this band" in line


class TestAnUnreadableRunIsLeftOutOfThePatterns:
    """A wrong input device makes every other number in a log meaningless.

    This project has spent whole sessions diagnosing playing that was never
    in the signal path, so such a run is excluded rather than averaged in --
    and the exclusion is reported, because a silently dropped evening looks
    like a tool that lost it.
    """

    def test_it_is_excluded_and_named(self, tmp_path):
        dead = HEAD.replace("level_loudest_db\t-12.0",
                            "level_loudest_db\t-52.0")
        run = _dated(tmp_path, "run_S_20260901_180000.txt",
                     [(1000.0, 48, 0, 0)],
                     [(1000.0, 1, 3, 12, 49, "-", 1, "miss")], head=dead)
        text = coach.pattern_report([run], 0)
        assert "left out as unreadable" in text
        assert "Nothing readable in this band" in text
        assert "Which string" not in text

    def test_a_healthy_run_is_not_excluded(self, tmp_path):
        run = _dated(tmp_path, "run_S_20260901_180000.txt",
                     [(1000.0, 48, 0, 0)],
                     [(1000.0, 1, 3, 12, 49, "-", 1, "miss")])
        text = coach.pattern_report([run], 0)
        assert "left out as unreadable" not in text


class TestTheWildcardIsExpandedHere:
    """cmd.exe does not expand `*`, and cmd is the shell this is run from.

    `coach.py %USERPROFILE%\\.pickhero\\run_*.txt` arrives as one literal
    word with a star in it, so without this every path is a file that is
    not there.
    """

    def test_a_star_finds_the_files(self, tmp_path):
        for name in ("run_A_20260901_180000.txt", "run_B_20260902_180000.txt"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        (tmp_path / "other.log").write_text("x", encoding="utf-8")
        found = coach._expand([str(tmp_path / "run_*.txt")])
        assert sorted(p.name for p in found) == [
            "run_A_20260901_180000.txt", "run_B_20260902_180000.txt"]

    def test_a_plain_path_is_left_alone(self, tmp_path):
        path = tmp_path / "run_A_20260901_180000.txt"
        path.write_text("x", encoding="utf-8")
        assert coach._expand([str(path)]) == [path]

    def test_a_name_that_matches_nothing_is_kept_so_the_error_names_it(
            self, tmp_path):
        found = coach._expand([str(tmp_path / "nope.txt")])
        assert [p.name for p in found] == ["nope.txt"]


class TestTheWholeThingRunsFromTheCommandLine:
    """The end-to-end check, and it is not decoration.

    The pattern code was appended AFTER the `if __name__` guard, so running
    the script exited at that line before a single new function existed.
    Every unit test above passed on it, because importing the module is not
    running it. Only calling main() through the same door the player uses
    catches that.
    """

    def test_patterns_over_two_logs(self, tmp_path, capsys):
        for day in ("20260901", "20260902"):
            _dated(tmp_path, f"run_S_{day}_180000.txt",
                   [(1000.0, 48, 0, 0)] * 1,
                   [(1000.0, 1, 3, 12, 49, "-", 1, "miss")])
        code = coach.main(["coach.py", str(tmp_path / "run_*.txt"),
                           "--patterns"])
        out = capsys.readouterr().out
        assert code == 0
        assert "WHAT RECURS" in out
        assert "2 sittings over 2 playing days" in out

    def test_days_narrows_it(self, tmp_path, capsys):
        for day in ("20260901", "20260902", "20260903"):
            _dated(tmp_path, f"run_S_{day}_180000.txt")
        coach.main(["coach.py", str(tmp_path / "run_*.txt"),
                    "--patterns", "--days", "1"])
        assert "1 of 3 logs" in capsys.readouterr().out

    def test_without_patterns_it_still_reports_each_log(self, tmp_path,
                                                        capsys):
        _dated(tmp_path, "run_S_20260901_180000.txt")
        coach.main(["coach.py", str(tmp_path / "run_S_20260901_180000.txt")])
        out = capsys.readouterr().out
        assert "A Song" in out
        assert "WHAT RECURS" not in out


class TestASongNameNeverReplacesTheReportWithATraceback:
    """The literals are cp850-clean; a TITLE comes out of the log.

    The player's own folder holds "I Prevail - There’s Fear In Letting
    Go" -- a curly apostrophe, which cp1252 encodes and cp850 does not. So
    the fault appears only on the console he actually has, and it would
    throw away a finished analysis over one character in one title.
    """

    def test_the_apostrophe_really_is_the_problem(self):
        name = "I Prevail - There’s Fear In Letting Go"
        name.encode("cp1252")                     # why nobody saw it
        with pytest.raises(UnicodeEncodeError):
            name.encode("cp850")

    def test_the_report_survives_it(self, tmp_path, capsys):
        head = HEAD.replace("song\tA Song",
                            "song\tI Prevail - There’s Fear")
        _dated(tmp_path, "run_S_20260901_180000.txt",
               [(1000.0, 48, 0, 0)],
               [(1000.0, 1, 3, 12, 49, "-", 1, "miss")], head=head)
        assert coach.main(["coach.py",
                           str(tmp_path / "run_*.txt"), "--patterns"]) == 0
        assert "WHAT RECURS" in capsys.readouterr().out

    def test_a_stream_that_cannot_be_reconfigured_is_not_an_error(self,
                                                                  monkeypatch):
        """A pipe, a captured stream, an old Python: none is a reason to die."""
        class Plain:
            def write(self, _): pass
            def flush(self): pass
        monkeypatch.setattr(coach.sys, "stdout", Plain())
        coach._never_die_on_a_song_name()         # must not raise


class TestTheBaselineCountsEveryJudgedNote:
    """The band's own rate must not be read off one GROUP.

    Every picker may answer None -- `_fret_region` does for a log written
    before the fret column existed, `_technique` for every ordinary note --
    so a total summed out of one group is a total over whatever that group
    happened to see. Summed out of the fret group, this player's first band
    reported 1285 judgeable notes where the string group held 20685, and
    every "worse than this band" beside a row was measured against a
    population sixteen times smaller than the row above it.
    """

    def _old_format(self, tmp_path, name="run_S_20260901_180000.txt"):
        """A log from before the fret column -- which this folder is full
        of, and which no group but `Which string` can label."""
        rows = []
        for i in range(60):
            string = 1 + (i % 6)
            verdict = "miss" if string == 3 else "hit"
            rows.append(f"{500.0 + i * 400:.1f}\t{string}\t{40 + string}"
                        f"\t{verdict}\n")
        text = (HEAD + STRIKES
                + "\n# every written note and how it ended up\n"
                + "note_ms\tstring\tmidi\tverdict\n" + "".join(rows))
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return coach.Run(path)

    def test_a_log_with_no_frets_still_has_a_baseline(self, tmp_path):
        found = coach.patterns([self._old_format(tmp_path)])
        assert found["tally"]["Where on the neck"] == {}
        assert found["total"]["good"] + found["total"]["faults"] == 60

    def test_and_the_report_names_a_rate_rather_than_giving_up(self, tmp_path):
        out = coach.pattern_report([self._old_format(tmp_path)], 0)
        assert "No note in this band could be judged" not in out
        assert "60 judgeable notes" in out

    def test_the_baseline_matches_what_the_rows_are_compared_against(
            self, tmp_path):
        """The arithmetic the flags rest on: a group that sees every note
        must average to the baseline, or `+N pts` is measured against
        something else."""
        found = coach.patterns([self._old_format(tmp_path)])
        cells = found["tally"]["Which string"].values()
        assert sum(c["good"] + c["faults"] for c in cells) == 60
        assert sum(c["faults"] for c in cells) == found["total"]["faults"]
        assert coach._rate(found["total"]) == pytest.approx(10 / 60)

    def test_the_intervals_are_counted_once_and_not_per_group(self, tmp_path):
        """They were summed out of the same group, so an old log reported
        no wrong intervals at all however many it held."""
        strikes, notes = [], []
        for i in range(30):
            ms = 500.0 + i * 400
            strikes.append((ms, 39, 0, 0))
            notes.append((ms, 1 + i // 8, 6, 3, 40, "-", 1, "miss"))
        run = _dated(tmp_path, "run_S_20260901_180000.txt", strikes, notes)
        found = coach.patterns([run])
        assert sum(found["intervals"].values()) == 30
        # and the same count the fret group holds, since here every note
        # does carry a fret -- the control that says nothing was double
        # counted on the way out of the group.
        per_group = sum(
            sum(cell["intervals"].values())
            for cell in found["tally"]["Where on the neck"].values())
        assert per_group == 30


class TestALogThatDoesNotSayItsBuildDoesNotEndABand:
    """Absence of evidence, at the level of the banding.

    On this player's own 2026-09-07 an unstamped log between two stamped
    ones split one evening into four bands of 1, 6, 1 and 2 sittings, and
    2026-09-09 into another four -- fifteen bands where the history holds
    about five, most of them too small for any group to clear
    MIN_GROUP_NOTES.
    """

    def _run(self, tmp_path, name, build):
        head = HEAD + (f"build\t{build}\n" if build is not None else "")
        return _dated(tmp_path, name, [], [(1000.0, 1, 6, 3, 40, "-", 1,
                                            "hit")], head=head)

    def test_an_unstamped_log_joins_the_band_around_it(self, tmp_path):
        runs = [self._run(tmp_path, "run_A_20260907_180000.txt", "400ac736"),
                self._run(tmp_path, "run_B_20260907_190000.txt", None),
                self._run(tmp_path, "run_C_20260907_200000.txt", "400ac736")]
        found = coach.bands(runs)
        assert len(found) == 1
        assert found[0]["build"] == "400ac736"

    def test_and_so_does_one_that_says_unknown(self, tmp_path):
        runs = [self._run(tmp_path, "run_A_20260907_180000.txt", "400ac736"),
                self._run(tmp_path, "run_B_20260907_190000.txt",
                          "unknown build")]
        assert len(coach.bands(runs)) == 1

    def test_two_real_builds_still_part(self, tmp_path):
        """The control: this must not become a rule that never splits."""
        runs = [self._run(tmp_path, "run_A_20260907_180000.txt", "400ac736"),
                self._run(tmp_path, "run_B_20260909_180000.txt", "451fc182")]
        assert len(coach.bands(runs)) == 2

    def test_the_band_takes_the_build_that_named_itself(self, tmp_path):
        runs = [self._run(tmp_path, "run_A_20260907_180000.txt", None),
                self._run(tmp_path, "run_B_20260907_190000.txt", "400ac736")]
        found = coach.bands(runs)
        assert len(found) == 1 and found[0]["build"] == "400ac736"

    def test_a_long_gap_still_parts_them(self, tmp_path):
        runs = [self._run(tmp_path, "run_A_20260901_180000.txt", None),
                self._run(tmp_path, "run_B_20261101_180000.txt", None)]
        assert len(coach.bands(runs)) == 2


class TestOneRowPerKindOfFault:
    """`trustworthy` writes the measurement into the sentence, so counting
    the sentences gave forty rows for one fault with forty readings."""

    def test_the_gate_is_one_row_however_many_percentages(self):
        kinds = {coach._kind_of(f"{n} % of the audio was below the noise gate")
                 for n in (21, 43, 99)}
        assert len(kinds) == 1

    def test_the_level_is_one_row_however_many_decibels(self):
        kinds = {coach._kind_of(
            f"the input peaks at {db} dB, where the pitch starts coming "
            f"back wrong") for db in (-42.6, -68.8)}
        assert len(kinds) == 1

    def test_the_kinds_are_still_told_apart(self):
        said = [
            "43 % of the audio was below the noise gate",
            "the input hears the room, not the guitar (room -37 dB)",
            "the input peaks at -42.6 dB, where the pitch starts coming "
            "back wrong",
            "15 dropped buffers -- notes were lost at random",
            "the log was written straight after a seek, so it describes "
            "only what happened since",
        ]
        assert len({coach._kind_of(d) for d in said}) == len(said)

    def test_every_wording_trustworthy_can_produce_is_covered(self, tmp_path):
        """A kind this helper does not recognise falls back to the whole
        sentence, which is the forty-row behaviour coming straight back --
        so the helper is asserted against the function that writes them."""
        import inspect
        source = inspect.getsource(coach.trustworthy)
        said = [line for line in source.splitlines() if "doubts.append" in line]
        assert len(said) == 5, "trustworthy grew a doubt this test has not seen"


class TestTheStringNextToItIsNotAFinger:
    """*"a fourth and a fifth are the interval between adjacent guitar
    strings and the two notes of a power chord"* -- and on one of this
    player's runs 24 of the 50 clean unmatched strikes were exactly that.
    Over twenty days of logs it is the LARGEST group in the wrong-pitch
    bucket: +5 twenty-seven times, +4 twenty-six, against fourteen of the
    one-fret error the bucket exists to find.

    The same reading is also a hand on the wrong string, which IS a fault,
    and the log holds nothing that parts the two -- so it is set aside
    from both sides, the rule the denominator already runs on.
    """

    def _standard(self, tmp_path, heard, written_string=4, fret=2):
        """A log in standard tuning, so `midi - fret` names the strings."""
        tuning = {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40}
        notes, strikes = [], []
        for i, (string, midi) in enumerate(sorted(tuning.items())):
            ms = 500.0 + i * 400
            notes.append((ms, 1, string, 0, midi, "-", 1, "hit"))
            strikes.append((ms, midi, 0, 0))
        ms = 4000.0
        notes.append((ms, 2, written_string, fret,
                      tuning[written_string] + fret, "-", 1, "miss"))
        strikes.append((ms, heard, 0, 0))
        return _log(tmp_path, strikes, notes)

    def test_the_tuning_is_read_off_the_notes(self, tmp_path):
        run = self._standard(tmp_path, 52)
        assert run.neighbour_steps == frozenset({4, 5})

    def test_a_fourth_away_is_set_aside(self, tmp_path):
        run = self._standard(tmp_path, 50 + 2 + 5)
        assert run.why(run.notes[-1]) == "struck, the string next to it"

    def test_and_a_major_third_away(self, tmp_path):
        run = self._standard(tmp_path, 50 + 2 - 4)
        assert run.why(run.notes[-1]) == "struck, the string next to it"

    def test_it_is_not_counted_as_the_playing(self, tmp_path):
        run = self._standard(tmp_path, 50 + 2 + 5)
        assert not run.why(run.notes[-1]).startswith(coach.PLAYER_FAULTS)
        found = coach.patterns([run])
        assert found["total"]["faults"] == 0
        assert found["total"]["unreadable"] == 1
        assert found["neighbours"] == 1
        assert found["intervals"] == {}

    def test_one_fret_off_is_still_the_playing(self, tmp_path):
        """The control. A rule that sets aside everything finds nothing."""
        run = self._standard(tmp_path, 50 + 2 + 1)
        assert run.why(run.notes[-1]) == "wrong pitch (+1)"
        found = coach.patterns([run])
        assert found["total"]["faults"] == 1
        assert found["neighbours"] == 0

    def test_and_so_are_two_and_three_semitones(self, tmp_path):
        """The reason the set is read off the tuning rather than written
        down: over the sixteen named tunings the adjacent gaps run 2, 3,
        4, 5 and 7, so a hard-coded set swallows a plain wrong fret."""
        for off in (2, 3, -2, -3):
            run = self._standard(tmp_path, 50 + 2 + off)
            assert run.why(run.notes[-1]).startswith("wrong pitch"), off

    def test_a_log_with_no_frets_sets_nothing_aside(self, tmp_path):
        """It says nothing about its tuning, so it may not claim one."""
        text = (HEAD + STRIKES
                + "4000.0\t4000.0\t4000.0\t57\t0.90\t0\t0\tmatched\t\t\n"
                + "\n# every written note and how it ended up\n"
                + "note_ms\tstring\tmidi\tverdict\n"
                + "4000.0\t4\t52\tmiss\n")
        path = tmp_path / "old.txt"
        path.write_text(text, encoding="utf-8")
        run = coach.Run(path)
        assert run.neighbour_steps == frozenset()
        assert run.why(run.notes[0]) == "wrong pitch (+5)"

    def test_a_drop_tuning_was_never_counted_anyway(self, tmp_path):
        """Its sixth-to-fifth gap is 7 semitones, which `out of reach` has
        already taken -- so the drop case needs nothing added here."""
        tuning = {1: 62, 2: 57, 3: 53, 4: 48, 5: 43, 6: 36}   # Drop C
        notes, strikes = [], []
        for i, (string, midi) in enumerate(sorted(tuning.items())):
            notes.append((500.0 + i * 400, 1, string, 0, midi, "-", 1, "hit"))
            strikes.append((500.0 + i * 400, midi, 0, 0))
        notes.append((4000.0, 2, 6, 0, 36, "-", 1, "miss"))
        strikes.append((4000.0, 43, 0, 0))        # the fifth string, +7
        run = _log(tmp_path, strikes, notes)
        assert 7 not in run.neighbour_steps
        assert run.why(run.notes[-1]) == "struck, out of reach"

    def test_the_report_says_how_many_it_set_aside(self, tmp_path):
        run = self._standard(tmp_path, 50 + 2 + 5)
        run.path = tmp_path / "run_S_20260901_180000.txt"
        run.path.write_text((tmp_path / "run.txt").read_text(),
                            encoding="utf-8")
        out = coach.pattern_report([coach.Run(run.path)], 0)
        assert "adjacent strings" in out

    def test_the_two_readers_share_one_name(self, tmp_path):
        """`why` writes it and `patterns` counts it; spelled out twice, one
        of them silently counts nothing."""
        run = self._standard(tmp_path, 50 + 2 + 5)
        assert run.why(run.notes[-1]) == coach.NEIGHBOUR_STRING
        assert coach.patterns([run])["neighbours"] == 1


class TestADeadNoteHasNoPitchToBeWrongAbout:
    """Its written fret says where the hand DAMPS the string, not what will
    sound -- and the app knows it: `_dead_note_credit` accepts any strike,
    pitched or not. Comparing a strike against that fret is comparing
    against a note the tab never asked for.

    It cost a finding. On the player's own latest band it read 35 % wrong
    against an 8 % baseline and was reported to him as the one thing worth
    practising; judged honestly it is 8 % of 129 against 13 %, which is
    better than his average.
    """

    def _one(self, tmp_path, tech, heard):
        run = _log(tmp_path, [(1000.0, heard, False, False)],
                   [(1000.0, 1, 6, 3, 43, tech, 1, "miss")])
        return run.why(run.notes[0])

    def test_a_struck_dead_note_is_not_a_wrong_pitch(self, tmp_path):
        assert self._one(tmp_path, "d", 45) \
            == "struck, a dead note has no pitch to check"

    def test_nor_an_unpitched_one(self, tmp_path):
        run = _log(tmp_path, [(1000.0, 0, True, False)],
                   [(1000.0, 1, 6, 3, 43, "d", 1, "miss")])
        assert run.why(run.notes[0]).startswith("struck, a dead note")

    def test_and_it_is_not_counted_as_the_playing(self, tmp_path):
        run = _log(tmp_path, [(1000.0, 45, False, False)],
                   [(1000.0, 1, 6, 3, 43, "d", 1, "miss")])
        assert not run.why(run.notes[0]).startswith(coach.PLAYER_FAULTS)

    def test_a_dead_note_nobody_struck_is_still_his(self, tmp_path):
        """The control. A dead note IS a stroke the tab asks for, and the
        strike is the whole of its evidence -- so silence there is a fault
        and a rule that excused it would find nothing."""
        run = _log(tmp_path, [], [(1000.0, 1, 6, 3, 43, "d", 1, "miss")])
        assert run.why(run.notes[0]) == "nothing was struck"
        assert run.why(run.notes[0]).startswith(coach.PLAYER_FAULTS)

    def test_an_ordinary_note_is_judged_as_before(self, tmp_path):
        assert self._one(tmp_path, "-", 45) == "wrong pitch (+2)"

    def test_a_dead_note_inside_a_chug_run_counts_too(self, tmp_path):
        """`tech` is a set of letters, so a dead palm-muted note carries
        "dp" -- read as a whole word it would miss every one of them."""
        assert self._one(tmp_path, "dp", 45) \
            == "struck, a dead note has no pitch to check"
