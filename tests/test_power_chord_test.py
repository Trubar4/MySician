"""The power-chord test tab is only worth its density and its shape.

The whole reason this file exists is the one case no reference take covers:
a WRONG power chord in a passage too dense for the chord verifier to get its
341 ms of audio. Two things have to hold or it measures the covered case
twice over -- the dense blocks must really be dense at the default tempo,
and the shape must be root + fifth + OCTAVE, because the three-string shape
is what the player's own song writes and the two-string one is what
`check_chord_credit.py` already holds error takes for.

`test_at_a_slow_tempo_nothing_is_dense` is the control: without it the
density assertions would pass on a file nobody could call dense.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from pickhero.audio.chord_verify import (
    MIN_WINDOW_MS, NEXT_STRIKE_GUARD_MS, SKIP_MS,
)
from pickhero.tabs.loader import load_gp_file

ROOT = Path(__file__).resolve().parent.parent
NEEDS_MS = MIN_WINDOW_MS + SKIP_MS + NEXT_STRIKE_GUARD_MS


def _tool():
    sys.path.insert(0, str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location(
        "make_power_chord_test", ROOT / "tools" / "make_power_chord_test.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return _tool()


def _written(tool, tmp_path, tempo=140, bars=4, offset=0):
    out = tmp_path / f"pc{tempo}_{offset}.gp5"
    sys.argv = ["make_power_chord_test.py", "--tempo", str(tempo),
                "--bars", str(bars), "--offset", str(offset),
                "--out", str(out)]
    assert tool.main() == 0
    return load_gp_file(out, track_index=0)


def _strikes(timeline):
    """(time, notes low string first, gap to the next strike)."""
    times = sorted({n.timestamp_ms for n in timeline.notes})
    out = []
    for i, t in enumerate(times):
        notes = sorted((n for n in timeline.notes if n.timestamp_ms == t),
                       key=lambda n: -n.string)
        gap = times[i + 1] - t if i + 1 < len(times) else float("inf")
        out.append((t, notes, gap))
    return out


class TestTheShape:
    def test_every_strike_is_three_strings(self, tool, tmp_path):
        for _, notes, _ in _strikes(_written(tool, tmp_path)):
            assert len(notes) == 3

    def test_root_fifth_octave(self, tool, tmp_path):
        """The octave is the string the player's own song writes and the
        reference takes do not."""
        for _, notes, _ in _strikes(_written(tool, tmp_path)):
            root = notes[0].midi_note
            assert [n.midi_note - root for n in notes] == [0, 7, 12]

    def test_adjacent_strings(self, tool, tmp_path):
        """A shape that skips a string is not one a hand makes."""
        for _, notes, _ in _strikes(_written(tool, tmp_path)):
            assert [n.string for n in notes] == [notes[0].string,
                                                 notes[0].string - 1,
                                                 notes[0].string - 2]

    def test_nothing_the_fret_filter_can_eat(self, tool, tmp_path):
        """A diagnostic whose notes a filter removes measures the filter --
        and `note_filter` reaches the MATCHER, so such a note is not merely
        hidden, it is never scored. Both copies, because the wrong one has to
        live under the same ceiling as the right one."""
        for offset in (0, 1):
            timeline = _written(tool, tmp_path, offset=offset)
            assert max(n.fret for n in timeline.notes) <= 7

    def test_standard_tuning_so_nothing_is_retuned(self, tool, tmp_path):
        timeline = _written(tool, tmp_path)
        assert timeline.metadata.tuning == {1: 64, 2: 59, 3: 55,
                                           4: 50, 5: 45, 6: 40}

    def test_nothing_in_it_is_unreadable(self, tool, tmp_path):
        assert _written(tool, tmp_path).metadata.unreadable_notes == 0


class TestEveryStrikeIsAttributable:
    def test_neighbouring_roots_are_three_semitones_apart(self, tool,
                                                          tmp_path):
        """A strike is only counted where exactly ONE tab note explains it."""
        roots = [notes[0].midi_note
                 for _, notes, _ in _strikes(_written(tool, tmp_path))]
        assert min(abs(a - b) for a, b in zip(roots, roots[1:])) >= 3

    def test_a_root_does_not_come_round_inside_half_a_second(self, tool,
                                                             tmp_path):
        strikes = _strikes(_written(tool, tmp_path))
        last = {}
        for at, notes, _ in strikes:
            root = notes[0].midi_note
            if root in last:
                assert at - last[root] > 500.0
            last[root] = at


class TestTheDensityIsTheWholePoint:
    def test_the_dense_blocks_lose_their_window(self, tool, tmp_path):
        """Half the chords at the default tempo, or there is nothing new."""
        strikes = _strikes(_written(tool, tmp_path))
        dropped = [s for s in strikes if s[2] <= NEEDS_MS]
        assert len(dropped) > len(strikes) / 2

    def test_and_some_still_have_one(self, tool, tmp_path):
        """The control inside the file: a wrong chord HAS to be convicted
        where the verifier can see it, whatever the dense blocks do."""
        strikes = _strikes(_written(tool, tmp_path))
        assert sum(1 for s in strikes if s[2] > NEEDS_MS) >= 10

    def test_at_a_slow_tempo_nothing_is_dense(self, tool, tmp_path):
        """Without this the two above would pass on any file at all."""
        strikes = _strikes(_written(tool, tmp_path, tempo=80))
        assert all(s[2] > NEEDS_MS for s in strikes)

    def test_enough_of_them_to_say_anything(self, tool, tmp_path):
        """12 notes is a measurement inside its own noise -- which is what
        the timing test's own power-chord block comes to."""
        timeline = _written(tool, tmp_path)
        assert len(timeline.notes) >= 200


class TestThePalmMuteSurvivesTheRoundTrip:
    def test_a_third_of_it_is_choked(self, tool, tmp_path):
        timeline = _written(tool, tmp_path)
        muted = [n for n in timeline.notes if n.palm_mute]
        assert len(muted) == 96

    def test_and_the_choked_block_is_the_dense_one(self, tool, tmp_path):
        """A palm-muted block the verifier reaches is the covered case."""
        strikes = _strikes(_written(tool, tmp_path))
        muted = [s for s in strikes if any(n.palm_mute for n in s[1])]
        assert sum(1 for s in muted if s[2] <= NEEDS_MS) >= 30


class TestTheCommittedFileIsTheOneTheToolWrites:
    """The tab is checked in, which the other three generators' are not.

    `tools/` is absent from the EXE, and this project has now paid three
    times for a thing that can only be reached from a shell not existing on
    the machine that needs it most -- so the file arrives with a `git pull`
    and can be copied to a laptop that has nothing but MySician.exe. A
    snapshot can drift from its generator in silence, and the writer is
    byte-deterministic, so it is pinned rather than trusted.
    """

    COMMITTED = ROOT / "songs" / "power_chord_test_140bpm.gp5"

    def test_it_is_there(self):
        assert self.COMMITTED.exists()

    def test_and_it_is_byte_for_byte_what_the_tool_writes(self, tool,
                                                          tmp_path):
        out = tmp_path / "fresh.gp5"
        sys.argv = ["make_power_chord_test.py", "--out", str(out)]
        assert tool.main() == 0
        assert out.read_bytes() == self.COMMITTED.read_bytes()


class TestTheWrongCopy:
    """*"Kannst du nicht ein 2. File machen mit dem Bund hoeher, damit ich es
    nicht selbst beim Spiel korrigieren muss und du vergleichst halt beide mit
    dem gleichen GP?"*

    One fret up, nothing else, and both takes scored against the UNMOVED tab.
    That is better than a deliberately mis-fretted take rather than merely
    easier: a hand fighting its own habit plays less cleanly, and a chord
    convicted for being sloppy says nothing about whether a semitone is
    convicted. Here the only difference reaching the verifier is the pitch.

    Which is a property to assert and not a hope -- if the copy moved the
    rhythm, the strings or the durations by so much as a millisecond, song
    time would stop meaning the same moment in both and the comparison would
    be measuring the alignment instead.
    """

    def _pair(self, tool, tmp_path):
        return (_written(tool, tmp_path, offset=0),
                _written(tool, tmp_path, offset=1))

    def test_only_the_pitch_moves(self, tool, tmp_path):
        right, wrong = self._pair(tool, tmp_path)
        key = lambda tl: [(n.timestamp_ms, n.string, n.duration_ms,
                           n.palm_mute, n.dead) for n in tl.notes]
        assert key(right) == key(wrong)

    def test_and_it_moves_by_exactly_one_semitone(self, tool, tmp_path):
        right, wrong = self._pair(tool, tmp_path)
        assert {b.midi_note - a.midi_note
                for a, b in zip(right.notes, wrong.notes)} == {1}
        assert {b.fret - a.fret
                for a, b in zip(right.notes, wrong.notes)} == {1}

    def test_the_song_is_the_same_length(self, tool, tmp_path):
        """Song time has to be the same millisecond in both, or the strikes
        of one take cannot be read against the other's tab."""
        right, wrong = self._pair(tool, tmp_path)
        assert right.duration_ms == wrong.duration_ms
        assert len(right.measures) == len(wrong.measures)

    def test_it_is_still_a_power_chord(self, tool, tmp_path):
        """A uniform shift keeps every interval, so the wrong copy is a wrong
        power chord and not some other shape."""
        for _, notes, _ in _strikes(_written(tool, tmp_path, offset=1)):
            root = notes[0].midi_note
            assert [n.midi_note - root for n in notes] == [0, 7, 12]

    def test_its_strikes_are_attributable_too(self, tool, tmp_path):
        roots = [notes[0].midi_note
                 for _, notes, _ in _strikes(_written(tool, tmp_path,
                                                      offset=1))]
        assert min(abs(a - b) for a, b in zip(roots, roots[1:])) >= 3

    def test_it_is_just_as_dense(self, tool, tmp_path):
        """The whole question is the dense case, so the copy that asks it has
        to lose its windows exactly where the other one does."""
        right, wrong = self._pair(tool, tmp_path)
        assert ([s[2] for s in _strikes(right)]
                == [s[2] for s in _strikes(wrong)])
        dropped = [s for s in _strikes(wrong) if s[2] <= NEEDS_MS]
        assert len(dropped) > len(_strikes(wrong)) / 2

    def test_offset_zero_is_the_file_itself(self, tool, tmp_path):
        """Or the flag would be a second way of writing the same tab."""
        out = tmp_path / "explicit.gp5"
        sys.argv = ["make_power_chord_test.py", "--offset", "0",
                    "--out", str(out)]
        assert tool.main() == 0
        assert (out.read_bytes()
                == (ROOT / "songs" / "power_chord_test_140bpm.gp5").read_bytes())

    def test_down_is_refused_rather_than_clamped(self, tool, tmp_path):
        """The lowest root is fret 0, so a copy a fret FLAT cannot exist --
        and silently clamping it to 0 would write the right tab under a name
        that says it is the wrong one."""
        sys.argv = ["make_power_chord_test.py", "--offset", "-1",
                    "--out", str(tmp_path / "nope.gp5")]
        assert tool.main() == 2
        assert not (tmp_path / "nope.gp5").exists()

    def test_the_committed_copy_is_what_the_tool_writes(self, tool, tmp_path):
        out = tmp_path / "fresh_up1.gp5"
        sys.argv = ["make_power_chord_test.py", "--offset", "1",
                    "--out", str(out)]
        assert tool.main() == 0
        committed = ROOT / "songs" / "power_chord_test_140bpm_up1.gp5"
        assert committed.exists()
        assert out.read_bytes() == committed.read_bytes()
