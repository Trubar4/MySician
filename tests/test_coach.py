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
        assert coach.MIN_READABLE > 0
        assert "Not enough of it could be read" in coach.report(run)


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
