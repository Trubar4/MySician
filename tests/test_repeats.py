"""Repeat signs: the order the bars are actually played in.

The loader walked every bar once and ignored |: :| entirely. A tab that lines
up with a recording only because it repeats is then shorter in the app than
the music is, so the picture and the backing start together and drift apart by
the length of every repeat that was skipped -- independent of practice speed,
because that stretches both alike.

This is pure arithmetic over the bar list, which is where a subtle error would
be invisible: it produces a plausible song that is simply the wrong one.
"""

import pytest

from pickhero.tabs.loader import MAX_PLAYED_BARS, BarRepeat, repeat_order

B = BarRepeat


class TestTheOrderBarsArePlayedIn:
    def test_a_song_with_no_repeats_is_unchanged(self):
        assert repeat_order([B(), B(), B()]) == [0, 1, 2]

    def test_nothing_at_all_plays_nothing(self):
        assert repeat_order([]) == []

    def test_a_section_between_the_signs_plays_twice(self):
        assert repeat_order([B(open=True), B(), B(close_count=2)]) == [
            0, 1, 2, 0, 1, 2]

    def test_a_count_of_three_plays_three_times(self):
        assert repeat_order([B(open=True), B(close_count=3)]) == [
            0, 1, 0, 1, 0, 1]

    def test_a_close_with_no_open_repeats_from_the_start(self):
        """What GP means by a :| with no |: of its own."""
        assert repeat_order([B(), B(), B(close_count=2)]) == [0, 1, 2, 0, 1, 2]

    def test_and_the_next_one_repeats_from_after_the_first(self):
        bars = [B(), B(close_count=2), B(), B(close_count=2)]
        assert repeat_order(bars) == [0, 1, 0, 1, 2, 3, 2, 3]

    def test_bars_before_the_open_are_played_once(self):
        bars = [B(), B(open=True), B(close_count=2), B()]
        assert repeat_order(bars) == [0, 1, 2, 1, 2, 3]

    def test_two_separate_sections(self):
        bars = [B(open=True), B(close_count=2),
                B(open=True), B(close_count=2)]
        assert repeat_order(bars) == [0, 1, 0, 1, 2, 3, 2, 3]


class TestAlternateEndings:
    def test_first_and_second_time_bars(self):
        """|: A B [1. C :|] [2. D]"""
        bars = [B(open=True), B(),
                B(alternatives=0b01, close_count=2),
                B(alternatives=0b10)]
        assert repeat_order(bars) == [0, 1, 2, 0, 1, 3]

    def test_three_endings(self):
        """Every ending but the last carries the :| sign, which is how the
        notation is actually written -- the repeat is what sends you back to
        the open, and the last ending is the one you leave from."""
        bars = [B(open=True),
                B(alternatives=0b001, close_count=3),
                B(alternatives=0b010, close_count=3),
                B(alternatives=0b100)]
        assert repeat_order(bars) == [0, 1, 0, 2, 0, 3]

    def test_an_ending_may_be_several_bars_long(self):
        bars = [B(open=True), B(),
                B(alternatives=0b01), B(alternatives=0b01, close_count=2),
                B(alternatives=0b10), B(alternatives=0b10)]
        assert repeat_order(bars) == [0, 1, 2, 3, 0, 1, 4, 5]

    def test_an_ending_marked_for_both_passes_plays_on_both(self):
        bars = [B(open=True), B(alternatives=0b11, close_count=2)]
        assert repeat_order(bars) == [0, 1, 0, 1]

    def test_the_music_after_the_endings_still_follows(self):
        bars = [B(open=True),
                B(alternatives=0b01, close_count=2),
                B(alternatives=0b10),
                B()]
        assert repeat_order(bars) == [0, 1, 0, 2, 3]


class TestItCannotRunAway:
    def test_a_bar_that_repeats_to_itself_still_terminates(self):
        assert len(repeat_order([B(open=True, close_count=99999)])) <= MAX_PLAYED_BARS

    def test_a_close_count_of_one_is_not_a_repeat(self):
        """GP writes 1 for "play once", which is no repeat at all."""
        assert repeat_order([B(open=True), B(close_count=1)]) == [0, 1]

    def test_a_nonsense_alternative_mask_does_not_hang(self):
        """No ending claims the pass, so the section must still end."""
        bars = [B(open=True), B(alternatives=0b1000, close_count=2)]
        assert len(repeat_order(bars)) <= MAX_PLAYED_BARS


# ---------------------------------------------------------------------------
# The two formats spell repeats differently and are off by one from each
# other, so each reader is checked against a file it wrote itself. The GPIF
# container is built by hand rather than vendored, the way tests/test_gpx.py
# already does -- real files are not ours to carry.
# ---------------------------------------------------------------------------

GPIF = """<?xml version="1.0" encoding="UTF-8"?>
<GPIF>
  <Score><Title>Repeats</Title></Score>
  <MasterTrack>
    <Tracks>0</Tracks>
    <Automations><Automation>
      <Type>Tempo</Type><Bar>0</Bar><Value>120 2</Value>
    </Automation></Automations>
  </MasterTrack>
  <Tracks><Track id="0">
    <Name>Guitar</Name>
    <GeneralMidi><Program>30</Program></GeneralMidi>
    <MIDI><Program>30</Program></MIDI>
    <Properties><Property name="Tuning">
      <Pitches>40 45 50 55 59 64</Pitches>
    </Property></Properties>
  </Track></Tracks>
  <MasterBars>__BARS__</MasterBars>
  <Bars>__BARDEFS__</Bars>
  <Voices>__VOICES__</Voices>
  <Beats>__BEATS__</Beats>
  <Rhythms><Rhythm id="0"><NoteValue>Whole</NoteValue></Rhythm></Rhythms>
  <Notes><Note id="0"><Properties>
    <Property name="String"><String>0</String></Property>
    <Property name="Fret"><Fret>0</Fret></Property>
  </Properties></Note></Notes>
</GPIF>
"""


def _gp_with_repeats(tmp_path, marks):
    """A .gp of len(marks) bars, one whole note each.

    `marks` is one XML snippet per bar, inserted into its MasterBar -- that is
    where <Repeat> and <AlternateEndings> live.
    """
    import zipfile

    bars, bardefs, voices, beats = [], [], [], []
    for i, mark in enumerate(marks):
        bars.append(f"<MasterBar><Bars>{i}</Bars><Time>4/4</Time>{mark}"
                    f"</MasterBar>")
        bardefs.append(f'<Bar id="{i}"><Voices>{i}</Voices></Bar>')
        voices.append(f'<Voice id="{i}"><Beats>{i}</Beats></Voice>')
        beats.append(f'<Beat id="{i}"><Rhythm ref="0"/><Notes>0</Notes></Beat>')
    xml = (GPIF.replace("__BARS__", "".join(bars))
                .replace("__BARDEFS__", "".join(bardefs))
                .replace("__VOICES__", "".join(voices))
                .replace("__BEATS__", "".join(beats)))
    path = tmp_path / "repeats.gp"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Content/score.gpif", xml)
        archive.writestr("VERSION", "7.0")
    return path


class TestTheGpifReader:
    def _bars(self, tmp_path, marks):
        from pickhero.tabs.loader import load_gp_file
        return load_gp_file(_gp_with_repeats(tmp_path, marks), track_index=0)

    def test_no_repeat_plays_each_bar_once(self, tmp_path):
        timeline = self._bars(tmp_path, ["", "", ""])
        assert len(timeline.measures) == 3
        assert len(timeline.notes) == 3

    def test_a_repeated_section_plays_twice(self, tmp_path):
        timeline = self._bars(tmp_path, [
            '<Repeat start="true"/>', "",
            '<Repeat end="true" count="2"/>', ""])
        assert len(timeline.measures) == 7      # 3 + 3 + 1
        assert len(timeline.notes) == 7

    def test_gpif_counts_passes_where_gp5_counts_repeats(self, tmp_path):
        """count="3" is three times through, not four."""
        timeline = self._bars(tmp_path, [
            '<Repeat start="true"/>', '<Repeat end="true" count="3"/>'])
        assert len(timeline.measures) == 6

    def test_a_count_below_two_is_not_a_repeat(self, tmp_path):
        timeline = self._bars(tmp_path, [
            '<Repeat start="true"/>', '<Repeat end="true" count="1"/>'])
        assert len(timeline.measures) == 2

    def test_alternate_endings(self, tmp_path):
        timeline = self._bars(tmp_path, [
            '<Repeat start="true"/>',
            '<AlternateEndings>1</AlternateEndings>'
            '<Repeat end="true" count="2"/>',
            "<AlternateEndings>2</AlternateEndings>"])
        # A, 1st ending, A, 2nd ending
        assert len(timeline.measures) == 4

    def test_the_bars_come_out_in_time_order(self, tmp_path):
        timeline = self._bars(tmp_path, [
            '<Repeat start="true"/>', "",
            '<Repeat end="true" count="2"/>'])
        starts = [m.start_ms for m in timeline.measures]
        assert starts == sorted(starts)
        assert len(set(starts)) == len(starts)

    def test_and_so_do_the_notes(self, tmp_path):
        timeline = self._bars(tmp_path, [
            '<Repeat start="true"/>', '<Repeat end="true" count="2"/>'])
        stamps = [n.timestamp_ms for n in timeline.notes]
        assert stamps == sorted(stamps)
        assert len(set(stamps)) == len(stamps)

    def test_the_backing_track_agrees_with_the_notes(self, tmp_path):
        from pickhero.tabs.loader import extract_backing_track, load_gp_file
        path = _gp_with_repeats(tmp_path, [
            '<Repeat start="true"/>', "",
            '<Repeat end="true" count="2"/>'])
        timeline = load_gp_file(path, track_index=0)
        backing = extract_backing_track(path, exclude_track_indices=set())
        last = max(e.timestamp_ms for e in backing.events)
        assert abs(last - timeline.duration_ms) < 100.0
