"""Moving a stretch of one track, in a GP7/8 file.

*"Kannst du das Tab in den Anfangstakten korrigieren?"* -- the clean guitar's
ten-bar intro on Carol Of The Bells is written a whole tone below the
recording, measured two independent ways. What that needs is the frets moved
in those bars of that track and nowhere else, and a quietly transposed tab is
worse than no tab, so the properties are what is asserted here.
"""

import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import transpose_bars as tb  # noqa: E402

from pickhero.tabs.loader import load_gp_file

# Two tracks, three bars each. Track 0's notes carry the full set of pitch
# fields a real GP8 file writes; track 1's carry only the fret, because a
# file without them is still valid and must not be refused.
GPIF = """<?xml version="1.0" encoding="utf-8"?>
<GPIF>
  <Score><Title>Two</Title></Score>
  <MasterTrack><Tracks>0 1</Tracks>
    <Automations><Automation>
      <Type>Tempo</Type><Bar>0</Bar><Value>120 2</Value>
    </Automation></Automations>
  </MasterTrack>
  <Tracks>
    <Track id="0"><Name>Clean</Name><GeneralMidi><Program>27</Program></GeneralMidi>
      <Properties><Property name="Tuning">
        <Pitches>40 45 50 55 59 64</Pitches></Property></Properties></Track>
    <Track id="1"><Name>Lead</Name><GeneralMidi><Program>30</Program></GeneralMidi>
      <Properties><Property name="Tuning">
        <Pitches>40 45 50 55 59 64</Pitches></Property></Properties></Track>
  </Tracks>
  <MasterBars>
    <MasterBar><Bars>0 3</Bars><Time>4/4</Time></MasterBar>
    <MasterBar><Bars>1 4</Bars><Time>4/4</Time></MasterBar>
    <MasterBar><Bars>2 5</Bars><Time>4/4</Time></MasterBar>
  </MasterBars>
  <Bars>
    <Bar id="0"><Voices>0</Voices></Bar><Bar id="1"><Voices>1</Voices></Bar>
    <Bar id="2"><Voices>2</Voices></Bar><Bar id="3"><Voices>3</Voices></Bar>
    <Bar id="4"><Voices>4</Voices></Bar><Bar id="5"><Voices>5</Voices></Bar>
  </Bars>
  <Voices>
    <Voice id="0"><Beats>0</Beats></Voice><Voice id="1"><Beats>1</Beats></Voice>
    <Voice id="2"><Beats>2</Beats></Voice><Voice id="3"><Beats>3</Beats></Voice>
    <Voice id="4"><Beats>4</Beats></Voice><Voice id="5"><Beats>5</Beats></Voice>
  </Voices>
  <Beats>
    <Beat id="0"><Rhythm ref="0"/><Notes>0</Notes></Beat>
    <Beat id="1"><Rhythm ref="0"/><Notes>1</Notes></Beat>
    <Beat id="2"><Rhythm ref="0"/><Notes>2</Notes></Beat>
    <Beat id="3"><Rhythm ref="0"/><Notes>3</Notes></Beat>
    <Beat id="4"><Rhythm ref="0"/><Notes>4</Notes></Beat>
    <Beat id="5"><Rhythm ref="0"/><Notes>5</Notes></Beat>
  </Beats>
  <Rhythms><Rhythm id="0"><NoteValue>Whole</NoteValue></Rhythm></Rhythms>
  <Notes>
    {notes}
  </Notes>
</GPIF>
"""

FULL = """<Note id="{i}"><Properties>
      <Property name="ConcertPitch"><Pitch>
        <Step>A</Step><Accidental/><Octave>4</Octave></Pitch></Property>
      <Property name="TransposedPitch"><Pitch>
        <Step>A</Step><Accidental/><Octave>4</Octave></Pitch></Property>
      <Property name="String"><String>2</String></Property>
      <Property name="Fret"><Fret>7</Fret></Property>
      <Property name="Midi"><Number>57</Number></Property>
    </Properties></Note>"""

BARE = """<Note id="{i}"><Properties>
      <Property name="String"><String>2</String></Property>
      <Property name="Fret"><Fret>7</Fret></Property>
    </Properties></Note>"""


def _xml():
    notes = "\n    ".join(
        [FULL.format(i=i) for i in range(3)] + [BARE.format(i=i)
                                                for i in range(3, 6)])
    return GPIF.format(notes=notes)


@pytest.fixture
def song(tmp_path):
    path = tmp_path / "two.gp"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Content/score.gpif", _xml())
    return path


def _notes(path, track):
    return load_gp_file(str(path), track_index=track).notes


def _pitch_of(path, note_id):
    root = ET.fromstring(zipfile.ZipFile(path).read(tb.GPIF))
    note = next(n for n in root.find("Notes").findall("Note")
                if n.get("id") == note_id)
    pitch = tb._prop(note, "ConcertPitch").find("Pitch")
    return (pitch.findtext("Step"), pitch.findtext("Accidental") or "",
            pitch.findtext("Octave"))


class TestOnlyTheChosenBarsOfTheChosenTrackMove:

    def test_the_bars_asked_for_move(self, song, tmp_path):
        out = tmp_path / "fixed.gp"
        assert tb.rewrite(song, out, 0, range(1, 3), 2) == 2
        was, now = _notes(song, 0), _notes(out, 0)
        assert [n.midi_note for n in was] == [57, 57, 57]
        assert [n.midi_note for n in now] == [59, 59, 57]

    def test_the_other_track_is_untouched(self, song, tmp_path):
        out = tmp_path / "fixed.gp"
        tb.rewrite(song, out, 0, range(1, 4), 2)
        assert ([n.midi_note for n in _notes(out, 1)]
                == [n.midi_note for n in _notes(song, 1)])

    def test_the_frets_are_what_moved(self, song, tmp_path):
        out = tmp_path / "fixed.gp"
        tb.rewrite(song, out, 0, range(1, 2), 2)
        assert [n.fret for n in _notes(out, 0)] == [9, 7, 7]
        assert [n.string for n in _notes(out, 0)] == [4, 4, 4]

    def test_a_track_with_no_pitch_fields_still_moves(self, song, tmp_path):
        """The app derives the pitch, so a bare note is not a reason to stop."""
        out = tmp_path / "fixed.gp"
        assert tb.rewrite(song, out, 1, range(1, 4), 2) == 3
        assert [n.midi_note for n in _notes(out, 1)] == [59, 59, 59]

    def test_the_check_agrees(self, song, tmp_path):
        out = tmp_path / "fixed.gp"
        tb.rewrite(song, out, 0, range(1, 3), 2)
        assert tb.check(song, out, 0, range(1, 3), 2)

    def test_the_check_can_fail(self, song, tmp_path):
        """A control: a check that cannot fail is the thing it exists to catch."""
        out = tmp_path / "fixed.gp"
        tb.rewrite(song, out, 0, range(1, 3), 2)
        assert not tb.check(song, out, 0, range(1, 3), 1)


class TestTheFileStaysAFile:

    def test_the_spelling_follows_the_pitch(self, song, tmp_path):
        """GPIF writes a pitch four times over and Guitar Pro reads them."""
        out = tmp_path / "fixed.gp"
        tb.rewrite(song, out, 0, range(1, 2), 2)
        assert _pitch_of(song, "0") == ("A", "", "4")
        assert _pitch_of(out, "0") == ("B", "", "4")

    def test_a_sharp_is_spelled_with_an_accidental(self, song, tmp_path):
        out = tmp_path / "fixed.gp"
        tb.rewrite(song, out, 0, range(1, 2), 1)
        assert _pitch_of(out, "0") == ("A", "#", "4")

    def test_the_octave_follows_the_pitch_over_a_boundary(self, song,
                                                          tmp_path):
        # A3 (57) up 15 semitones is C5 (72), a new octave by GPIF's count.
        out = tmp_path / "fixed.gp"
        tb.rewrite(song, out, 0, range(1, 2), 15)
        assert _pitch_of(out, "0") == ("C", "", "6")
        assert _notes(out, 0)[0].midi_note == 72

    def test_every_other_file_in_the_container_survives(self, song, tmp_path):
        with zipfile.ZipFile(song, "a") as zf:
            zf.writestr("VERSION", "7")
        out = tmp_path / "fixed.gp"
        tb.rewrite(song, out, 0, range(1, 2), 2)
        assert zipfile.ZipFile(out).read("VERSION") == b"7"


class TestWhatItRefuses:

    def test_a_fret_off_the_neck_is_refused(self, song, tmp_path):
        out = tmp_path / "fixed.gp"
        with pytest.raises(SystemExit, match="not on the neck"):
            tb.rewrite(song, out, 0, range(1, 2), -8)

    def test_a_bar_the_file_does_not_have_is_refused(self, song, tmp_path):
        out = tmp_path / "fixed.gp"
        with pytest.raises(SystemExit, match="not in this file"):
            tb.rewrite(song, out, 0, range(1, 9), 2)

    def test_a_track_the_bar_does_not_have_is_refused(self, song, tmp_path):
        out = tmp_path / "fixed.gp"
        with pytest.raises(SystemExit, match="no track"):
            tb.rewrite(song, out, 5, range(1, 2), 2)
