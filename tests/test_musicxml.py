"""The bridge from our timeline to an engraver.

verovio draws real tablature -- six lines, fret numbers, stems saying how
long each note is -- and renders 150 bars in 90 ms. What it cannot do is
read Guitar Pro. Everything here is about handing it something true.
"""

import pytest

from pickhero.tabs.musicxml import (
    DIVISIONS, note_type, split_value, to_musicxml,
)
from pickhero.tabs.timeline import (
    MeasureInfo, NoteEvent, SongMetadata, Timeline,
)


def _timeline(notes, measures=None, tuning=None):
    return Timeline(
        notes,
        SongMetadata(title="T", tempo=120,
                     tuning=tuning or {1: 64, 2: 59, 3: 55,
                                       4: 50, 5: 45, 6: 40}),
        measures=measures or [MeasureInfo(index=0, start_ms=0.0,
                                          end_ms=2000.0)],
    )


class TestTheWrittenValue:
    @pytest.mark.parametrize("quarters,expected", [
        (4.0, ("whole", 0)), (2.0, ("half", 0)), (1.0, ("quarter", 0)),
        (0.5, ("eighth", 0)), (0.25, ("16th", 0)), (0.125, ("32nd", 0)),
        (1.5, ("quarter", 1)), (0.75, ("eighth", 1)), (3.0, ("half", 1)),
    ])
    def test_the_common_values_and_their_dots(self, quarters, expected):
        assert note_type(quarters) == expected

    def test_an_odd_length_still_prints_something(self):
        """Usually a tie the reader merged. A stem that is slightly wrong
        beats a bar the engraver will not draw at all."""
        kind, _ = note_type(5.0)
        assert kind == "whole"

    def test_nothing_is_a_quarter_rather_than_a_crash(self):
        assert note_type(0.0) == ("quarter", 0)


class TestSplittingSilence:
    def test_it_adds_back_up_to_what_it_was_given(self):
        for quarters in (0.5, 1.0, 1.75, 2.5, 3.0, 4.0, 7.5):
            assert sum(s for s, _, _ in split_value(quarters)) == pytest.approx(
                quarters, abs=1.0 / DIVISIONS)

    def test_it_prefers_the_longest_value_that_fits(self):
        assert split_value(4.0) == [(4.0, "whole", 0)]
        assert split_value(3.0) == [(3.0, "half", 1)]

    def test_it_terminates_on_anything(self):
        for quarters in (0.001, 0.3333, 11.7):
            assert len(split_value(quarters)) < 16


class TestWhatComesOut:
    def test_a_note_carries_its_string_and_fret(self):
        xml = to_musicxml(_timeline([
            NoteEvent(timestamp_ms=0.0, duration_ms=500.0, midi_note=45,
                      string=5, fret=0, duration_quarters=1.0)]))
        assert "<string>5</string><fret>0</fret>" in xml
        assert "<sign>TAB</sign>" in xml

    def test_the_staff_is_tuned_low_string_first(self):
        """MusicXML counts staff lines up from the bottom while the app
        numbers strings down from the high e. Getting it backwards mirrors
        the whole tab and reads as a rendering fault."""
        xml = to_musicxml(_timeline([]))
        first = xml.index('<staff-tuning line="1">')
        assert "<tuning-step>E</tuning-step>" in xml[first:first + 120]
        assert "<tuning-octave>2</tuning-octave>" in xml[first:first + 120]

    def test_notes_at_one_instant_are_one_chord(self):
        xml = to_musicxml(_timeline([
            NoteEvent(timestamp_ms=0.0, duration_ms=500.0, midi_note=40,
                      string=6, fret=0, duration_quarters=1.0),
            NoteEvent(timestamp_ms=0.0, duration_ms=500.0, midi_note=45,
                      string=5, fret=0, duration_quarters=1.0)]))
        assert xml.count("<chord/>") == 1

    def test_an_empty_bar_keeps_its_length(self):
        """Without this an empty bar collapses to nothing and every bar
        after it moves -- measured on the player's own songs, 42 seconds
        lost over one of them with every onset count still correct."""
        xml = to_musicxml(_timeline([], measures=[
            MeasureInfo(index=0, start_ms=0.0, end_ms=2000.0),
            MeasureInfo(index=1, start_ms=2000.0, end_ms=4000.0)]))
        assert xml.count('<rest measure="yes"/>') == 2

    def test_silence_is_a_rest_and_not_a_forward(self):
        """verovio honours a rest's duration in its timemap and does not
        honour a forward's, so a bar padded with forwards renders at the
        right length and reports the wrong times."""
        xml = to_musicxml(_timeline([
            NoteEvent(timestamp_ms=1000.0, duration_ms=500.0, midi_note=40,
                      string=6, fret=0, duration_quarters=1.0)]))
        assert "<forward>" not in xml
        assert "<rest/>" in xml

    def test_an_overlapping_note_gets_a_voice_of_its_own(self):
        """A rest cannot sit where a note is still sounding, and guitar tab
        overlaps constantly -- a let-ring bass note under a run above it."""
        xml = to_musicxml(_timeline([
            NoteEvent(timestamp_ms=0.0, duration_ms=2000.0, midi_note=40,
                      string=6, fret=0, duration_quarters=4.0),
            NoteEvent(timestamp_ms=500.0, duration_ms=500.0, midi_note=64,
                      string=1, fret=0, duration_quarters=1.0)],
            measures=[MeasureInfo(index=0, start_ms=0.0, end_ms=2000.0)]))
        assert "<voice>2</voice>" in xml
        assert "<backup>" in xml

    def test_the_bar_carries_its_own_tempo(self):
        """Without it verovio assumes 120 BPM and every position it reports
        is wrong by the ratio, which is a playhead that drifts."""
        xml = to_musicxml(_timeline([], measures=[
            MeasureInfo(index=0, start_ms=0.0, end_ms=2400.0)]))
        assert "<sound tempo=" in xml

    def test_the_time_signature_is_the_bar_s_own(self):
        xml = to_musicxml(_timeline([], measures=[
            MeasureInfo(index=0, start_ms=0.0, end_ms=2000.0,
                        beats=6, beat_type=8)]))
        assert "<beats>6</beats><beat-type>8</beat-type>" in xml

    def test_a_song_title_cannot_break_the_document(self):
        timeline = _timeline([])
        timeline.metadata.title = "A <song> & \"friends\""
        xml = to_musicxml(timeline)
        import xml.etree.ElementTree as ET
        ET.fromstring(xml)          # raises if the escaping is wrong

    def test_every_export_is_well_formed(self):
        import xml.etree.ElementTree as ET
        notes = [NoteEvent(timestamp_ms=i * 250.0, duration_ms=250.0,
                           midi_note=40 + i, string=1 + (i % 6),
                           fret=i % 13, measure=i // 8,
                           duration_quarters=0.5) for i in range(40)]
        measures = [MeasureInfo(index=b, start_ms=b * 2000.0,
                                end_ms=(b + 1) * 2000.0) for b in range(5)]
        ET.fromstring(to_musicxml(_timeline(notes, measures)))


class TestTheEngraversTimemapIsNotUsed:
    """Measured in isolation: on a TABLATURE staff verovio mis-times rests.

    The identical document as standard notation puts "quarter, quarter rest,
    quarter, quarter" at quarters 0, 2, 3 -- correct -- and as tablature at
    0, 3, 4, with the rest advancing two quarters instead of one. <forward>
    is not honoured there either, so neither mechanism for silence survives
    a tab staff.

    It costs nothing, because the timemap was never the authority: the app
    knows when every note sounds from its own timeline. What is needed from
    the engraver is where it drew them.
    """

    def _timeline_with(self, count):
        notes = [NoteEvent(timestamp_ms=i * 500.0, duration_ms=500.0,
                           midi_note=40 + i, string=1 + (i % 6), fret=i % 5,
                           measure=i // 4, duration_quarters=1.0)
                 for i in range(count)]
        measures = [MeasureInfo(index=b, start_ms=b * 2000.0,
                                end_ms=(b + 1) * 2000.0)
                    for b in range((count + 3) // 4)]
        return _timeline(notes, measures)

    def test_every_note_carries_an_id_of_ours(self):
        xml = to_musicxml(self._timeline_with(8))
        for i in range(8):
            assert f'<note id="n{i}"' in xml

    def test_the_id_is_the_index_into_the_timeline(self):
        """The app maps a note's time to its picture through this and
        nothing else, so the two must never drift apart."""
        timeline = self._timeline_with(6)
        xml = to_musicxml(timeline)
        first = xml.index('<note id="n3"')
        fret = timeline.notes[3].fret
        assert f"<fret>{fret}</fret>" in xml[first:first + 400]

    def test_positions_are_read_back_by_that_id(self):
        from pickhero.tabs.musicxml import note_positions
        svg = ('<g id="n0" class="note">  <text x="120" y="240">'
               '<tspan>3</tspan></text></g>'
               '<g id="n7" class="note">\n<text x="-5" y="99">')
        assert note_positions(svg) == {"n0": (120, 240), "n7": (-5, 99)}

    def test_a_page_with_nothing_on_it_yields_nothing(self):
        from pickhero.tabs.musicxml import note_positions
        assert note_positions("<svg></svg>") == {}
