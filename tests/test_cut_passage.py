"""Cutting a passage out of a real tab into its own GP5.

Two takes of different bars are not a comparison, so the passage has to be a
FILE. What this pins is that the file really IS the passage: nothing moved,
nothing lost, and the `--offset` copy differs by exactly one semitone and by
nothing else -- which is the property the whole conviction measurement rests
on, since one take's strikes are scored against the other take's tab.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from gp_builder import REST, build_song, palm_mute, write  # noqa: E402

from pickhero.tabs.loader import load_gp_file  # noqa: E402

DROP_C = [62, 57, 53, 48, 43, 36]        # string 1 (high) first, as GP counts
QUARTER, EIGHTH, SIXTEENTH = 4, 8, 16


@pytest.fixture
def tool():
    spec = importlib.util.spec_from_file_location(
        "cut_passage", ROOT / "tools" / "cut_passage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def power(fret, *effects):
    """A three-string power chord in Drop C: root, fifth, octave."""
    return [(6, fret, *effects), (5, fret, *effects), (4, fret, *effects)]


def chug_bar(fret):
    """Sixteenths, a dotted eighth and a quarter -- the shapes a real chug
    riff is written in, so the dotted value is exercised rather than assumed.
    """
    # 0.25 + 0.25 + 0.25 + 0.75 + 0.5 + 1 + 1 == 4 quarters exactly. A bar
    # that does not fill its signature writes a LONGER measure, and the cut
    # then refuses it for holding bars of two lengths -- which is the guard
    # working and is not what this fixture is for.
    return [(SIXTEENTH, power(fret, palm_mute())),
            (SIXTEENTH, power(fret, palm_mute())),
            (SIXTEENTH, REST),
            ((EIGHTH, True), power(fret, palm_mute())),
            (EIGHTH, power(fret, palm_mute())),
            (QUARTER, power(fret)),
            (QUARTER, REST)]


@pytest.fixture
def source(tmp_path):
    """A Drop C tab of six chug bars, to cut bars 2-4 out of."""
    bars = [chug_bar(f) for f in (0, 0, 1, 1, 5, 0)]
    song = build_song("Source", 112, [("Riff", bars)], tuning=DROP_C)
    path = tmp_path / "source.gp5"
    write(song, path)
    return path


def notes_of(path, first_bar_ms=None):
    out = load_gp_file(str(path), track_index=0)
    notes = sorted(out.notes, key=lambda n: (n.timestamp_ms, n.string))
    if first_bar_ms is not None:
        notes = [n for n in notes if n.timestamp_ms >= first_bar_ms - 1.0]
    return out, notes


class TestTheCutIsThePassage:

    def test_it_writes_the_bars_it_was_asked_for(self, tool, source, tmp_path):
        out = tmp_path / "cut.gp5"
        sys.argv = ["cut_passage.py", str(source), "--bars", "2-4",
                    "--out", str(out)]
        assert tool.main() == 0
        src, src_notes = notes_of(source)
        bars = [m for m in src.measures if 2 <= m.index <= 4]
        want = [n for m in bars for n in src_notes
                if m.start_ms <= n.timestamp_ms < m.end_ms]
        cut, cut_notes = notes_of(out)
        bar_ms = bars[0].end_ms - bars[0].start_ms
        played = [n for n in cut_notes if n.timestamp_ms >= bar_ms - 1.0]
        assert len(played) == len(want)

    def test_and_nothing_moved(self, tool, source, tmp_path):
        """Every onset, string, fret and mute survives the write.

        A cut that moves an onset by a sixteenth is a cut that measures the
        rhythm rather than the fretting, and nothing on screen would say so.
        """
        out = tmp_path / "cut.gp5"
        sys.argv = ["cut_passage.py", str(source), "--bars", "2-4",
                    "--out", str(out)]
        assert tool.main() == 0
        src, src_notes = notes_of(source)
        bars = [m for m in src.measures if 2 <= m.index <= 4]
        bar_ms = bars[0].end_ms - bars[0].start_ms
        base = bars[0].start_ms - bar_ms
        want = [n for m in bars for n in src_notes
                if m.start_ms <= n.timestamp_ms < m.end_ms]
        cut, cut_notes = notes_of(out, bar_ms)
        for a, b in zip(want, cut_notes):
            assert a.string == b.string
            assert a.fret == b.fret
            assert a.palm_mute == b.palm_mute
            assert abs((a.timestamp_ms - base) - b.timestamp_ms) < 2.0

    def test_a_dotted_value_survives(self, tool, source, tmp_path):
        """Three quarters of a beat is a note value, not a sixteenth plus a
        rest -- and writing it as the latter moves every onset after it."""
        out = tmp_path / "cut.gp5"
        sys.argv = ["cut_passage.py", str(source), "--bars", "2-4",
                    "--out", str(out)]
        assert tool.main() == 0
        cut, notes = notes_of(out)
        held = {round(n.duration_quarters, 3) for n in notes}
        assert 0.75 in held, held

    def test_the_count_in_bar_is_empty(self, tool, source, tmp_path):
        out = tmp_path / "cut.gp5"
        sys.argv = ["cut_passage.py", str(source), "--bars", "2-4",
                    "--out", str(out)]
        assert tool.main() == 0
        cut, notes = notes_of(out)
        bar_ms = cut.measures[0].end_ms - cut.measures[0].start_ms
        assert not [n for n in notes if n.timestamp_ms < bar_ms - 1.0]


class TestTheOffsetCopy:

    def _both(self, tool, source, tmp_path):
        right, wrong = tmp_path / "a.gp5", tmp_path / "b.gp5"
        sys.argv = ["cut_passage.py", str(source), "--bars", "2-4",
                    "--out", str(right)]
        assert tool.main() == 0
        sys.argv = ["cut_passage.py", str(source), "--bars", "2-4",
                    "--offset", "1", "--out", str(wrong)]
        assert tool.main() == 0
        return notes_of(right), notes_of(wrong)

    def test_every_pitch_is_exactly_one_semitone_higher(self, tool, source,
                                                        tmp_path):
        (a, an), (b, bn) = self._both(tool, source, tmp_path)
        assert len(an) == len(bn)
        for x, y in zip(an, bn):
            assert y.midi_note == x.midi_note + 1
            assert y.fret == x.fret + 1

    def test_and_nothing_else_differs(self, tool, source, tmp_path):
        """Same rhythm to the millisecond, same strings, same mutes.

        One take's strikes are read against the other take's tab, so song
        time has to be the same moment in both files -- a copy that moved the
        rhythm would have the comparison measuring the alignment instead.
        """
        (a, an), (b, bn) = self._both(tool, source, tmp_path)
        assert a.duration_ms == b.duration_ms
        assert len(a.measures) == len(b.measures)
        for x, y in zip(an, bn):
            assert x.timestamp_ms == y.timestamp_ms
            assert x.duration_ms == y.duration_ms
            assert x.string == y.string
            assert x.palm_mute == y.palm_mute
            assert x.dead == y.dead

    def test_the_TUNING_is_not_moved_with_the_frets(self, tool, source,
                                                    tmp_path):
        """The offset moves the FRETS. A tuning moved with them puts the copy
        two semitones up while every fret still reads one higher -- which the
        fret comparison alone cannot see, and which the first version of this
        tool did.
        """
        (a, an), (b, bn) = self._both(tool, source, tmp_path)
        assert a.metadata.tuning == b.metadata.tuning
        # and the SOURCE's tuning, not the builder's standard default
        src = load_gp_file(str(source), track_index=0)
        assert a.metadata.tuning == src.metadata.tuning

    def test_a_cut_of_a_drop_tuning_stays_in_it(self, tool, source, tmp_path):
        (a, an), (b, bn) = self._both(tool, source, tmp_path)
        assert a.metadata.tuning[6] == 36      # C2, not E2
        assert b.metadata.tuning[6] == 36


class TestWhatItRefuses:

    def test_bars_of_different_lengths(self, tool, tmp_path):
        """One tempo is written, so a passage crossing a tempo change would
        come out at the wrong speed -- which nothing downstream could see."""
        import guitarpro
        from guitarpro import models as gp
        bars = [[(QUARTER, power(0))] * 4 for _ in range(3)]
        song = build_song("Mixed", 112, [("Riff", bars)], tuning=DROP_C)
        # Bar 1 in 3/4, with every start recomputed from the signatures --
        # gp_builder advances by four quarters whatever they say.
        song.measureHeaders[1].timeSignature.numerator = 3
        song.tracks[0].measures[1].voices[0].beats = \
            song.tracks[0].measures[1].voices[0].beats[:3]
        song.tracks[1].measures[1].voices[0].beats = \
            song.tracks[1].measures[1].voices[0].beats[:3]
        at = gp.Duration.quarterTime
        for header in song.measureHeaders:
            header.start = at
            at += gp.Duration.quarterTime * header.timeSignature.numerator
        path = tmp_path / "mixed.gp5"
        guitarpro.write(song, str(path), version=(5, 1, 0))
        sys.argv = ["cut_passage.py", str(path), "--bars", "0-2",
                    "--out", str(tmp_path / "x.gp5")]
        with pytest.raises(SystemExit):
            tool.main()

    def test_a_bar_range_that_is_not_there(self, tool, source, tmp_path):
        sys.argv = ["cut_passage.py", str(source), "--bars", "90-99",
                    "--out", str(tmp_path / "x.gp5")]
        with pytest.raises(SystemExit):
            tool.main()


class TestThePassageThisWasBuiltFor:
    """Godsmack bars 103-116, if that tab is on this machine.

    The cut is NOT committed and must not be: `.gitignore` keeps every
    `songs/*.gp` out because they are somebody's music, and the one exception
    it grants is for the generated diagnostic tabs, which this is not -- it is
    fourteen bars of a real transcription. So the player regenerates it from
    his own file with one command, and the suite skips rather than failing on
    a clone that has no Godsmack.
    """

    SOURCE = ROOT / "songs" / "Godsmack - Awake.gp"
    BARS = "103-116"

    @pytest.fixture
    def cuts(self, tool, tmp_path):
        if not self.SOURCE.exists():
            pytest.skip("the source tab is not on this machine")
        made = {}
        for offset in (0, 1):
            out = tmp_path / f"cut{offset}.gp5"
            sys.argv = ["cut_passage.py", str(self.SOURCE), "--bars",
                        self.BARS, "--offset", str(offset), "--out", str(out)]
            assert tool.main() == 0
            made[offset] = out
        return made

    def test_it_is_the_uncovered_case(self, cuts):
        """Dense enough that most strikes get no verification window, which
        is the whole reason this passage was chosen over an isolated one."""
        from pickhero.audio.chord_verify import (
            MIN_WINDOW_MS, NEXT_STRIKE_GUARD_MS, SKIP_MS)
        floor = MIN_WINDOW_MS + SKIP_MS + NEXT_STRIKE_GUARD_MS
        cut, notes = notes_of(cuts[0])
        at = sorted({round(n.timestamp_ms, 1) for n in notes})
        gaps = [b - a for a, b in zip(at, at[1:])]
        dropped = sum(1 for g in gaps if g < floor)
        assert dropped > len(gaps) / 2, f"{dropped} of {len(gaps)}"

    def test_and_it_is_three_string_power_chords_in_drop_c(self, cuts):
        cut, notes = notes_of(cuts[0])
        assert cut.metadata.tuning[6] == 36        # C2, not E2
        strokes = {}
        for n in notes:
            strokes.setdefault(round(n.timestamp_ms, 1), []).append(n)
        sizes = {len(v) for v in strokes.values()}
        assert sizes == {3}, sizes

    def test_the_copy_is_one_semitone_up_and_nothing_else(self, cuts):
        a, an = notes_of(cuts[0])
        b, bn = notes_of(cuts[1])
        assert len(an) == len(bn)
        assert a.metadata.tuning == b.metadata.tuning
        for x, y in zip(an, bn):
            assert y.midi_note == x.midi_note + 1
            assert y.fret == x.fret + 1
            assert x.timestamp_ms == y.timestamp_ms
            assert x.palm_mute == y.palm_mute

    def test_every_fret_stays_reachable_in_the_copy(self, cuts):
        """The whole shape slides, so the copy must not run off the neck --
        and nothing above the 7th fret keeps it under any fret filter the
        player has set, which `note_filter` applies to the MATCHER as well."""
        b, bn = notes_of(cuts[1])
        assert max(n.fret for n in bn) <= 7
