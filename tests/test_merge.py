"""Two tracks of one file, played as one part.

The rule is per BAR, and the reason is a measurement: on the file this was
built for, 184 notes of the lead want the same string at the same
millisecond as a note of the rhythm guitar. A guitar cannot play two notes
on one string, and GP5 cannot even write it down -- `tools/retune.py` paid
for that once, with a file that would not open at all. So "the lead wins"
has to say what it wins, and the bar is the only unit with no collisions by
construction.
"""

import pytest

from pickhero.config import Config
from pickhero.tabs import merge as merge_mod
from pickhero.tabs.timeline import MeasureInfo, NoteEvent, SongMetadata, Timeline


def _note(bar: int, ms: float, string: int = 1, fret: int = 5) -> NoteEvent:
    return NoteEvent(timestamp_ms=ms, duration_ms=200.0, midi_note=64,
                     string=string, fret=fret, measure=bar)


def _track(index: int, name: str, bars: dict[int, int],
           tuning: dict | None = None) -> Timeline:
    """A timeline with `count` notes in each named bar."""
    notes = []
    for bar, count in sorted(bars.items()):
        for n in range(count):
            notes.append(_note(bar, bar * 1000.0 + n * 100.0))
    meta = SongMetadata(track_name=name, track_index=index,
                        tuning=tuning if tuning is not None else {1: 64, 6: 40})
    measures = [MeasureInfo(i, i * 1000.0, (i + 1) * 1000.0) for i in range(8)]
    return Timeline(notes, meta, measures)


class TestABarIsTheUnit:

    def test_a_bar_the_lead_sits_out_comes_from_the_filler(self):
        lead = _track(2, "Lead", {0: 2, 2: 2})
        rhythm = _track(1, "Rhythm", {0: 4, 1: 4, 2: 4, 3: 4})
        merged = merge_mod.merge_by_bar([lead, rhythm])
        bars = {n.measure for n in merged.notes}
        assert bars == {0, 1, 2, 3}

    def test_a_bar_both_play_is_the_primary_s_whole(self):
        """Never interleaved: 184 notes on the file this was built for want
        the same string at the same millisecond, which is not a thing a
        guitar can do. The filler's notes in that bar are dropped, in all 42
        of its shared bars -- which is the cost, not a bug."""
        lead = _track(2, "Lead", {0: 2})
        rhythm = _track(1, "Rhythm", {0: 4, 1: 4})
        merged = merge_mod.merge_by_bar([lead, rhythm])
        in_bar_0 = [n for n in merged.notes if n.measure == 0]
        assert len(in_bar_0) == 2          # the lead's two, not six, not four

    def test_no_moment_ends_up_with_two_notes_on_one_string(self):
        lead = _track(2, "Lead", {0: 3, 1: 3})
        rhythm = _track(1, "Rhythm", {0: 3, 1: 3, 2: 3})
        merged = merge_mod.merge_by_bar([lead, rhythm])
        seen = [(n.timestamp_ms, n.string) for n in merged.notes]
        assert len(seen) == len(set(seen))

    def test_the_order_is_the_priority(self):
        a = _track(0, "A", {0: 1})
        b = _track(1, "B", {0: 1, 1: 1})
        assert {n.measure for n in merge_mod.merge_by_bar([a, b]).notes} == {0, 1}
        # Turn them round and the SAME bars are covered by the other track.
        first_wins = merge_mod.merge_by_bar([b, a])
        assert len(first_wins.notes) == 2

    def test_a_third_track_only_fills_what_is_still_empty(self):
        a = _track(0, "A", {0: 1})
        b = _track(1, "B", {1: 1})
        c = _track(2, "C", {0: 9, 1: 9, 2: 1})
        merged = merge_mod.merge_by_bar([a, b, c])
        assert len(merged.notes) == 3
        assert {n.measure for n in merged.notes} == {0, 1, 2}

    def test_the_notes_come_out_in_time_order(self):
        lead = _track(2, "Lead", {2: 2})
        rhythm = _track(1, "Rhythm", {0: 2, 1: 2, 2: 2})
        merged = merge_mod.merge_by_bar([lead, rhythm])
        stamps = [n.timestamp_ms for n in merged.notes]
        assert stamps == sorted(stamps)

    def test_the_grid_and_the_tempo_come_from_the_primary(self):
        lead = _track(2, "Lead", {0: 1})
        rhythm = _track(1, "Rhythm", {0: 1, 1: 1})
        merged = merge_mod.merge_by_bar([lead, rhythm])
        assert merged.metadata.track_index == 2
        assert len(merged.measures) == len(lead.measures)

    def test_the_name_says_it_is_two_tracks(self):
        """The picture and the run log both print it, so a merged part
        carrying one track's name is a lie in two places."""
        merged = merge_mod.merge_by_bar([_track(2, "Lead", {0: 1}),
                                         _track(1, "Rhythm", {0: 1, 1: 1})])
        assert merged.metadata.track_name == "Lead + Rhythm"


class TestWhatItRefuses:

    def test_one_track_is_not_a_merge(self):
        assert merge_mod.refuse_reason([_track(0, "A", {0: 1})]) is not None

    def test_two_tunings_are_two_instruments(self):
        """A NoteEvent carries its string and its fret, and the picture is
        drawn from them -- so a Drop C track merged into a Standard one puts
        fret numbers on the board that are readable and wrong."""
        a = _track(0, "A", {0: 1}, tuning={1: 64, 6: 40})
        b = _track(1, "B", {1: 1}, tuning={1: 64, 6: 36})
        assert merge_mod.refuse_reason([a, b]) == "those tracks are tuned differently"
        with pytest.raises(ValueError):
            merge_mod.merge_by_bar([a, b])

    def test_the_same_tuning_is_allowed(self):
        a = _track(0, "A", {0: 1})
        b = _track(1, "B", {1: 1})
        assert merge_mod.refuse_reason([a, b]) is None


class TestARunOfAMergeIsNotARunOfItsLeadTrack:

    def test_the_id_can_never_collide_with_a_real_track(self):
        """A stored run carries its track, and runs.py refuses to draw one of
        another track against these notes -- which is right here, and only
        works if the two are told apart."""
        for i in range(8):
            assert merge_mod.merged_track_id(i) < 0
        assert len({merge_mod.merged_track_id(i) for i in range(8)}) == 8


class TestItIsRememberedPerSong:

    def test_nothing_is_stored_for_an_ordinary_song(self):
        c = Config()
        c.set_track_merge_for("Thunder", [2])
        assert "Thunder" not in c.song_track_merges
        assert c.track_merge_for("Thunder") == []

    def test_the_order_survives_a_round_trip(self):
        c = Config()
        c.set_track_merge_for("Thunder", [2, 1])
        assert c.track_merge_for("Thunder") == [2, 1]

    def test_it_travels_and_is_forgotten_with_the_song(self):
        """Every per-song setting is a dict named song_something, found by
        four readers that none of them writes down."""
        from pickhero.tabs import sidecar
        c = Config()
        c.set_track_merge_for("Thunder", [2, 1])
        assert "song_track_merges" in sidecar.song_fields(c)
        assert sidecar.collect("Thunder", c)["settings"]["song_track_merges"] == [2, 1]
        c.rename_song("Thunder", "AC-DC - Thunder")
        assert c.track_merge_for("AC-DC - Thunder") == [2, 1]
        c.forget_song("AC-DC - Thunder")
        assert c.track_merge_for("AC-DC - Thunder") == []
