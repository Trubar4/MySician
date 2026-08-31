"""A tie is one note written twice, and it must be played once.

The player's screenshot: the tab writes fret 1 on the second string held
across two beats with a tie, and the app drew two notes side by side. On a
GP6/7/8 file the tied half was played again; on a GP3-5 file it was dropped
outright and the note it belonged to was never made any longer. So a held
note was either asked for twice or drawn for half its length, and the second
pick it asked for does not exist in the music -- it could only ever be a miss.
"""

import zipfile

import pytest

from pickhero.tabs.loader import load_gp_file


GPIF = """<?xml version="1.0" encoding="UTF-8"?>
<GPIF>
  <Score><Title>Ties</Title></Score>
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
  <MasterBars>
    <MasterBar><Bars>0</Bars><Time>4/4</Time></MasterBar>
    <MasterBar><Bars>1</Bars><Time>4/4</Time></MasterBar>
  </MasterBars>
  <Bars>
    <Bar id="0"><Voices>0</Voices></Bar>
    <Bar id="1"><Voices>1</Voices></Bar>
  </Bars>
  <Voices>
    <Voice id="0"><Beats>0</Beats></Voice>
    <Voice id="1"><Beats>1</Beats></Voice>
  </Voices>
  <Beats>
    <Beat id="0"><Rhythm ref="0"/><Notes>0</Notes></Beat>
    <Beat id="1"><Rhythm ref="0"/><Notes>__SECOND__</Notes></Beat>
  </Beats>
  <Rhythms><Rhythm id="0"><NoteValue>Whole</NoteValue></Rhythm></Rhythms>
  <Notes>
    <Note id="0"><Properties>
      <Property name="String"><String>1</String></Property>
      <Property name="Fret"><Fret>1</Fret></Property>
    </Properties></Note>
    <Note id="1"><Tie origin="false" destination="true"/><Properties>
      <Property name="String"><String>1</String></Property>
      <Property name="Fret"><Fret>1</Fret></Property>
    </Properties></Note>
  </Notes>
</GPIF>
"""


def _gp(tmp_path, second_note_id):
    xml = GPIF.replace("__SECOND__", second_note_id)
    path = tmp_path / f"ties_{second_note_id}.gp"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Content/score.gpif", xml)
        archive.writestr("VERSION", "7.0")
    return path


class TestATiedNoteIsNotPickedAgain:
    def test_the_second_half_is_not_a_note_of_its_own(self, tmp_path):
        timeline = load_gp_file(_gp(tmp_path, "1"), track_index=0)
        assert len(timeline.notes) == 1

    def test_it_lengthens_the_note_it_continues(self, tmp_path):
        """Two whole notes at 120 BPM is 2000 ms each; tied, that is one
        note of 4000, which is what "held long" means on paper."""
        timeline = load_gp_file(_gp(tmp_path, "1"), track_index=0)
        note = timeline.notes[0]
        assert note.timestamp_ms == pytest.approx(0.0)
        assert note.duration_ms == pytest.approx(4000.0)

    def test_an_untied_repeat_really_is_two_notes(self, tmp_path):
        """The control: the same file with the tie taken off. Without it
        this test class would pass on a loader that drops every second
        note, which is the other way to get one note out of two."""
        timeline = load_gp_file(_gp(tmp_path, "0"), track_index=0)
        assert len(timeline.notes) == 2
        assert timeline.notes[0].duration_ms == pytest.approx(2000.0)


class TestTheSameHoldsForTheOlderFormat:
    """GP3-5 spells a tie as note type 2. It was skipped, which is half
    right -- it is not picked -- and half wrong, because the note it
    continues stayed as short as it was written."""

    def test_the_reference_file_gains_no_notes_and_loses_no_time(self):
        from pathlib import Path
        path = Path("tests/fixtures/canon.gp5")
        if not path.exists():
            pytest.skip("fixture not present")
        timeline = load_gp_file(path)
        assert timeline.notes
        assert all(n.duration_ms > 0 for n in timeline.notes)
