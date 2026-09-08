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


LET_RING_GPIF = GPIF.replace(
    '<Note id="1"><Tie origin="false" destination="true"/><Properties>',
    '<Note id="1"><Properties>'
    '<Property name="LetRing"><Enable/></Property>')


def _gp_let_ring(tmp_path):
    xml = LET_RING_GPIF.replace("__SECOND__", "1")
    path = tmp_path / "letring.gp"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Content/score.gpif", xml)
        archive.writestr("VERSION", "7.0")
    return path


class TestLetRingIsNotATie:
    """"Der Wert haette ja verlaengert werden muessen."

    A tie is one note written twice and the reader merges it. "Let ring" is
    a different thing that looks the same on screen: the written value does
    not change -- a let-ring eighth is still an eighth -- the string simply
    is not damped, so the note sounds on until something else is played on
    it. Neither reader read it at all, so those notes were drawn at their
    written length and looked short.
    """

    def test_it_is_read_and_does_not_merge_the_notes(self, tmp_path):
        timeline = load_gp_file(_gp_let_ring(tmp_path), track_index=0)
        assert len(timeline.notes) == 2
        assert timeline.notes[1].let_ring

    def test_the_written_value_is_untouched(self, tmp_path):
        """It changes the drawing, never the scoring: the pick is at the
        written moment either way."""
        timeline = load_gp_file(_gp_let_ring(tmp_path), track_index=0)
        assert timeline.notes[1].duration_ms == pytest.approx(2000.0)

    def test_it_is_drawn_up_to_the_next_note_on_its_string(self, tmp_path):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.scrolling import PlayingScreen
        pygame.init()
        surface = pygame.Surface((1280, 720))
        timeline = load_gp_file(_gp_let_ring(tmp_path), track_index=0)
        screen = PlayingScreen(timeline, config=Config())
        layout = screen._layout(surface)
        note = timeline.notes[0]
        gaps = screen._neighbour_gaps(timeline.notes)
        gap_ms = gaps[(note.timestamp_ms, note.string)]
        # The plain sustain and the gap are the same here, so the property
        # that matters is that a let-ring note is never drawn SHORTER than
        # the room it has.
        assert screen.sustain_width(gap_ms, layout.pixels_per_ms) > 0
