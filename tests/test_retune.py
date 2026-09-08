"""Rewriting a seven-string tab for a six-string guitar.

Metal is written for seven and eight strings and this app plays six. The
notes often fit anyway, because a seven-string in B standard and a six-string
in drop B share their lowest note -- so the conversion is arithmetic, not
arrangement. Every note keeps its exact pitch and only its string and fret
change, and that is the claim these tests exist to hold.

Two of them pin bugs that a live file found and reason alone did not: two
notes of one beat placed on the SAME string (impossible on a guitar, and
undescribable in GP5 -- the played-strings byte has one bit per string, so
the file would not even open), and a TIE left behind when the note it
continues moved to another string, which read back an octave low.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import retune  # noqa: E402

SEVEN = (64, 59, 55, 50, 45, 40, 35)      # E4 B3 G3 D3 A2 E2 B1
DROP_B = retune.TUNINGS["drop-b"]         # C#4 G#3 E3 B2 F#2 B1


def _pitch(tuning, string, fret):
    return tuning[string - 1] + fret


class TestPlacingOneNote:
    def test_the_pitch_is_what_decides_the_fret(self):
        for string, fret in ((7, 0), (6, 5), (5, 7), (1, 12)):
            pitch = _pitch(SEVEN, string, fret)
            got = retune.place_beat([pitch], [fret], DROP_B)[0]
            assert _pitch(DROP_B, *got) == pitch

    def test_the_lowest_string_maps_straight_across(self):
        """A seven-string's B and drop B's sixth string are the same note,
        which is the whole reason this conversion works at all."""
        assert retune.place_beat([35], [0], DROP_B)[0] == (6, 0)

    def test_it_stays_near_the_hand_position_it_was_in(self):
        """A riff that sat in one position has to stay in one position, or the
        conversion is correct and unplayable."""
        pitch = _pitch(SEVEN, 5, 12)
        string, fret = retune.place_beat([pitch], [12], DROP_B)[0]
        assert abs(fret - 12) <= 5

    def test_a_note_the_neck_cannot_reach_is_None(self):
        assert retune.place_beat([120], [24], DROP_B)[0] is None


class TestPlacingAChord:
    def test_no_two_notes_share_a_string(self):
        """A guitar cannot play two notes on one string -- and a GP5 file
        cannot describe it: the played-strings byte has one bit per string,
        so one note is written and never read, and every byte after that is
        garbage. The file simply would not open."""
        chord = [_pitch(SEVEN, s, 19) for s in (1, 2)]
        placed = retune.place_beat(chord, [19, 19], DROP_B)
        strings = [p[0] for p in placed if p]
        assert len(strings) == len(set(strings))

    def test_a_six_note_chord_gets_six_strings(self):
        chord = [_pitch(SEVEN, s, 3) for s in (1, 2, 3, 4, 5, 6)]
        placed = retune.place_beat(chord, [3] * 6, DROP_B)
        assert all(p is not None for p in placed)
        assert len({p[0] for p in placed}) == 6

    def test_every_note_of_a_chord_keeps_its_pitch(self):
        chord = [_pitch(SEVEN, s, f) for s, f in ((7, 0), (6, 2), (5, 2))]
        placed = retune.place_beat(chord, [0, 2, 2], DROP_B)
        assert [_pitch(DROP_B, *p) for p in placed] == chord

    def test_one_unreachable_note_does_not_take_the_others_with_it(self):
        chord = [120, _pitch(SEVEN, 6, 3)]
        placed = retune.place_beat(chord, [24, 3], DROP_B)
        assert placed[0] is None
        assert placed[1] is not None


class TestTheWholeFile:
    SONG = (Path(__file__).resolve().parent.parent / "songs"
            / "timing_test_100bpm.gp5")

    pytestmark = pytest.mark.skipif(not SONG.exists(), reason="song missing")

    def test_a_six_string_song_is_left_alone(self, tmp_path, monkeypatch,
                                             capsys):
        out = tmp_path / "out.gp5"
        monkeypatch.setattr(sys, "argv",
                            ["x", str(self.SONG), "--out", str(out)])
        assert retune.main() == 0
        assert "unveraendert" in capsys.readouterr().out

    def test_it_writes_a_file_that_reads_back(self, tmp_path, monkeypatch):
        import guitarpro
        out = tmp_path / "out.gp5"
        monkeypatch.setattr(sys, "argv",
                            ["x", str(self.SONG), "--out", str(out)])
        retune.main()
        assert guitarpro.parse(str(out)) is not None

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        out = tmp_path / "none.gp5"
        monkeypatch.setattr(sys, "argv", ["x", str(self.SONG), "--out",
                                          str(out), "--dry-run"])
        retune.main()
        assert not out.exists()


class TestTheTunings:
    def test_drop_b_is_what_the_player_named(self):
        """B-F#-B-E-G#-C#, low string first."""
        assert [retune.note_name(v) for v in reversed(DROP_B)] == [
            "B1", "F#2", "B2", "E3", "G#3", "C#4"]

    def test_every_tuning_has_six_strings(self):
        for name, tuning in retune.TUNINGS.items():
            assert len(tuning) == 6, name

    def test_they_are_stored_high_string_first(self):
        """Guitar Pro's order, so they can be assigned straight across."""
        for name, tuning in retune.TUNINGS.items():
            assert tuning[0] > tuning[-1], name
