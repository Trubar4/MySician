"""A note the reader cannot place must not take the whole track with it.

The player uploaded Bon Jovi, "My Guitar Lies Bleeding In My Arms", and the
app answered `ValueError: fret must be >= 0, got -1`. His guess was a
capodaster and the file says otherwise: `CapoFret` is 0 on all six staves,
`PartialCapoFret` 0, the flags `000000`. What the ten -1 frets really are is
MUTED notes -- every one carries `<Property name="Muted"><Enable/></Property>`
and they are every muted note in the file. GPIF writes -1 for a fret the
transcriber left out, which only happens on an X, because an X has no number
beside it.

Two things come out of that and they are tested apart, because they are
fixed in different places:

* A muted note with no fret is drawn as an X at fret 0. Nothing is lost --
  the display already prints "X" and never the number, the fret filter lets
  it through, MusicXML's <fret> stays non-negative, and the guide sounds the
  open string rather than a semitone below it. The control is the 801 muted
  notes across the player's six other songs: every one carries a real fret
  and not one is negative.

* ANY note the reader cannot place is skipped and counted rather than
  raising. Ten notes of about 3800 cost four of the six guitars in that
  file, and what the player saw was a traceback instead of a song. That is
  the rule `scan_files` already follows for a folder it cannot read.
"""

import zipfile

import pytest

from pickhero.tabs.loader import load_gp_file
from pickhero.tabs.merge import merge_by_bar
from pickhero.tabs.simplify import simplified
from pickhero.tabs.timeline import MeasureInfo, NoteEvent, SongMetadata, Timeline


GPIF = """<?xml version="1.0" encoding="UTF-8"?>
<GPIF>
  <Score><Title>Mutes</Title></Score>
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
  </MasterBars>
  <Bars><Bar id="0"><Voices>0</Voices></Bar></Bars>
  <Voices><Voice id="0"><Beats>0</Beats></Voice></Voices>
  <Beats>
    <Beat id="0"><Rhythm ref="0"/><Notes>0 1 2</Notes></Beat>
  </Beats>
  <Rhythms><Rhythm id="0"><NoteValue>Whole</NoteValue></Rhythm></Rhythms>
  <Notes>
    <Note id="0"><Properties>
      <Property name="String"><String>3</String></Property>
      <Property name="Fret"><Fret>10</Fret></Property>
    </Properties></Note>
    <Note id="1"><Properties>
      <Property name="String"><String>2</String></Property>
      <Property name="Fret"><Fret>__MIDDLE__</Fret></Property>
      __MUTED__
    </Properties></Note>
    <Note id="2"><Properties>
      <Property name="String"><String>1</String></Property>
      <Property name="Fret"><Fret>8</Fret></Property>
    </Properties></Note>
  </Notes>
</GPIF>
"""

# The shape the player's own file writes, three times over in bar 5: the
# octave grip with the middle string deadened -- index on the low string,
# ring on the high, and the string between them damped by the ring finger.
MUTED = '<Property name="Muted"><Enable /></Property>'


def _gp(tmp_path, middle_fret, muted=True, name="mutes"):
    xml = (GPIF.replace("__MIDDLE__", str(middle_fret))
               .replace("__MUTED__", MUTED if muted else ""))
    path = tmp_path / f"{name}_{middle_fret}_{int(muted)}.gp"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Content/score.gpif", xml)
        archive.writestr("VERSION", "7.0")
    return path


def _middle(timeline):
    """The note on the middle string of the grip (our string 4)."""
    return next(n for n in timeline.notes if n.string == 4)


class TestAMutedNoteWithNoFret:
    def test_the_track_opens_at_all(self, tmp_path):
        """The whole of it. Four of that file's six guitars would not."""
        timeline = load_gp_file(_gp(tmp_path, -1), track_index=0)
        assert len(timeline.notes) == 3

    def test_the_x_is_kept_as_a_dead_note(self, tmp_path):
        """It is played -- a damped string is part of the strum, and in five
        of the player's bars the X is the only note in its beat."""
        timeline = load_gp_file(_gp(tmp_path, -1), track_index=0)
        assert _middle(timeline).dead is True

    def test_its_fret_is_zero_rather_than_minus_one(self, tmp_path):
        timeline = load_gp_file(_gp(tmp_path, -1), track_index=0)
        assert _middle(timeline).fret == 0

    def test_it_sounds_the_open_string_not_a_semitone_below_it(self, tmp_path):
        """Guitar Pro's own Midi field for these is the open string MINUS
        ONE, because it computes a pitch from a fret that means "none". The
        guide track plays the written part, so it would have sounded that."""
        timeline = load_gp_file(_gp(tmp_path, -1), track_index=0)
        assert _middle(timeline).midi_note == 50      # open D, string 4

    def test_nothing_is_reported_as_unreadable(self, tmp_path):
        """It was READ, not skipped. A count here would send the player
        looking for a fault in a file that has none."""
        timeline = load_gp_file(_gp(tmp_path, -1), track_index=0)
        assert timeline.metadata.unreadable_notes == 0
        assert timeline.metadata.unreadable_line() == ""

    def test_a_muted_note_that_does_have_a_fret_keeps_it(self, tmp_path):
        """The control, and the commonest case by far: 801 muted notes
        across the player's six other songs, every one with a real fret.
        Clamping those to 0 would be inventing a hand position."""
        timeline = load_gp_file(_gp(tmp_path, 7), track_index=0)
        note = _middle(timeline)
        assert (note.fret, note.dead, note.midi_note) == (7, True, 57)


class TestANegativeFretOnANoteThatIsNotMuted:
    """Deliberately NOT clamped. That number would be SHOWN -- `_draw_notes`
    prints "X" only for a dead note -- so a 0 invented here would put a
    wrong fret on screen. No file to hand contains one; what is known is
    only that the file cannot explain it."""

    def test_the_track_still_opens(self, tmp_path):
        timeline = load_gp_file(_gp(tmp_path, -1, muted=False), track_index=0)
        assert len(timeline.notes) == 2

    def test_the_note_is_left_out(self, tmp_path):
        timeline = load_gp_file(_gp(tmp_path, -1, muted=False), track_index=0)
        assert [n.string for n in timeline.notes] == [3, 5]

    def test_and_it_is_counted_and_named(self, tmp_path):
        timeline = load_gp_file(_gp(tmp_path, -1, muted=False), track_index=0)
        meta = timeline.metadata
        assert meta.unreadable_notes == 1
        assert "fret" in meta.unreadable_line()
        assert "1 note" in meta.unreadable_line()


class TestTheCountIsCarriedByEveryDerivedTimeline:
    """Which is why it lives in the metadata. `transposed`, `simplified` and
    `merge_by_bar` all copy the metadata forward with `replace`; a field on
    the Timeline would have had to be added to each of the three, and the
    one that was forgotten would silently report a song as read whole."""

    def _unreadable(self, n=2):
        meta = SongMetadata(title="t", tempo=120,
                            tuning={6: 40, 5: 45, 4: 50, 3: 55, 2: 59, 1: 64},
                            unreadable_notes=n,
                            unreadable_reasons=("fret must be >= 0, got -1",))
        notes = [NoteEvent(timestamp_ms=0.0, duration_ms=500.0,
                           midi_note=52, string=3, fret=2),
                 NoteEvent(timestamp_ms=0.0, duration_ms=500.0,
                           midi_note=64, string=1, fret=0)]
        return Timeline(notes, meta,
                        measures=[MeasureInfo(index=0, start_ms=0.0,
                                              end_ms=2000.0)])

    def test_transposed(self):
        assert self._unreadable().transposed(2).metadata.unreadable_notes == 2

    def test_simplified(self):
        assert simplified(self._unreadable()).metadata.unreadable_notes == 2

    def test_merged(self):
        merged = merge_by_bar([self._unreadable(), self._unreadable(3)])
        assert merged.metadata.unreadable_notes == 2


class TestTheSentence:
    def test_a_song_read_whole_says_nothing(self):
        """The control. Almost every song is this one, so a line that always
        had something to say would be the wallpaper the HUD was cut down to
        remove."""
        assert SongMetadata().unreadable_line() == ""

    def test_one_note_is_singular(self):
        line = SongMetadata(unreadable_notes=1,
                            unreadable_reasons=("bad",)).unreadable_line()
        assert line.startswith("1 note in this tab")

    def test_the_count_and_the_reason_are_both_there(self):
        """A count alone sends the next report back here knowing no more
        than the first one did."""
        line = SongMetadata(
            unreadable_notes=3,
            unreadable_reasons=("fret must be >= 0, got -1",),
        ).unreadable_line()
        assert "3 notes" in line
        assert "fret must be >= 0, got -1" in line

    def test_it_survives_having_no_reason(self):
        assert SongMetadata(unreadable_notes=2).unreadable_line()


# Seven pitches, and the first note moved onto the seventh string -- which
# `num_strings - gp7_string` puts at our string 7, a string this app does
# not have. The other two notes still land on strings it does.
SEVEN = (GPIF.replace("<Pitches>40 45 50 55 59 64</Pitches>",
                      "<Pitches>35 40 45 50 55 59 64</Pitches>")
              .replace("<String>3</String>", "<String>0</String>"))


def _seven(tmp_path, middle_fret):
    """A seven-string tab, which this app plays on six.

    `tools/retune.py` is what rewrites one properly; the reader can only
    leave the notes it has nowhere to put, and until now it did that in
    silence.
    """
    xml = SEVEN.replace("__MIDDLE__", str(middle_fret)).replace("__MUTED__", "")
    path = tmp_path / f"seven_{middle_fret}.gp"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Content/score.gpif", xml)
        archive.writestr("VERSION", "7.0")
    return path


class TestItIsTheCLASSThatIsCoveredAndNotOneQuirk:
    """`_note_or_skip` wraps the only `NoteEvent(` construction in the
    package, so every invariant NoteEvent has -- and any added later -- is a
    skipped note rather than a lost track. Testing only the fret would pin
    the instance that was reported and leave the next one to be found the
    same way."""

    def test_a_pitch_outside_midi_is_skipped_not_raised(self, tmp_path):
        """Fret 120 on the high E is MIDI 184. A damaged beat, not a
        technique, but the track is still worth opening."""
        timeline = load_gp_file(_gp(tmp_path, 120, muted=False), track_index=0)
        assert len(timeline.notes) == 2
        assert timeline.metadata.unreadable_notes == 1
        assert "midi_note" in timeline.metadata.unreadable_line()

    def test_a_string_this_app_does_not_have_is_counted_too(self, tmp_path):
        """It was skipped silently before, so one kind of unreadable note
        was counted and another was not, and the number meant neither."""
        timeline = load_gp_file(_seven(tmp_path, 7), track_index=0)
        assert timeline.metadata.unreadable_notes >= 1
        assert "string" in timeline.metadata.unreadable_line()

    def test_the_rest_of_that_track_still_plays(self, tmp_path):
        timeline = load_gp_file(_seven(tmp_path, 7), track_index=0)
        assert len(timeline.notes) >= 2


class TestItReallyReachesTheScreenAndTheLog:
    """A measurement nothing calls is a feature that ships doing nothing --
    four times in this project so far. So the wiring is driven rather than
    the helper: the real `App._load_song`, and the real run log."""

    @pytest.fixture
    def app(self):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.app import App
        pygame.init()
        pygame.display.set_mode((640, 480))
        application = App(Config())
        application._menu = None
        return application

    def test_opening_the_song_says_what_could_not_be_read(self, app, tmp_path):
        app._load_song(_gp(tmp_path, -1, muted=False))
        said = app._playing_screen._status_note_text()
        assert "could not be read" in said

    def test_a_clean_tab_says_nothing(self, app, tmp_path):
        """The control. Almost every song is this one."""
        app._load_song(_gp(tmp_path, 7))
        assert app._playing_screen._status_note_text() == ""

    def _log_text(self, path):
        """The real writer, the way the run-log tests already drive it."""
        import io
        from pickhero.config import Config
        from pickhero.matcher import NoteMatcher
        from pickhero.ui.scrolling import PlayingScreen
        timeline = load_gp_file(path, track_index=0)
        screen = PlayingScreen(timeline, config=Config())
        screen._song_key = "mutes"
        screen._matcher = NoteMatcher(timeline, timing_window_ms=150.0)
        buffer = io.StringIO()
        screen._write_run_log(buffer)
        return buffer.getvalue()

    def test_the_run_log_names_it(self, tmp_path):
        written = self._log_text(_gp(tmp_path, -1, muted=False))
        assert "unreadable_notes\t1" in written
        assert "fret must be >= 0" in written

    def test_and_leaves_the_line_out_when_there_is_nothing_to_say(self,
                                                                  tmp_path):
        written = self._log_text(_gp(tmp_path, 7))
        assert written                       # the log was really written
        assert "unreadable_notes" not in written
