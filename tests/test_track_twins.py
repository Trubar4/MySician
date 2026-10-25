"""Two tracks of one part, and a tuning nobody named.

Both come off one file -- I Prevail, "There's Fear In Letting Go" -- and
they are separate faults that happened to arrive together.

*"Mir zeigt es alle Spuren mit Drop A an, wenn ich sie der Reihe nach
oeffne."* Its two "Pitched" tracks are in E B E A C# F#, five semitones
under Drop A, which no NAMED tuning matches -- and `reachable_tunings`
listed only named tunings, so there was no entry at nothing and the strip
starred the nearest named one as the tuning the file was written in. The one
line on screen whose whole job is saying what the tab is in named a tuning
the tab does not use.

*"Spuren, die ident sind, kannst du eine von beiden ausblenden."* Its Rhythm
and its Lead guitar are note for note the same part, 513 notes each, so a
merge of the two is a no-op and a picker offering both asks a question with
one answer.
"""

import guitarpro
import pytest
from guitarpro import models as gp

from pickhero.audio.note_utils import (NAMED_TUNINGS, reachable_tunings,
                                       tuning_notes)
from pickhero.tabs.loader import duplicate_tracks, list_tracks, load_gp_file
from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline
from pickhero.ui.scrolling import PlayingScreen

TUNINGS = dict(NAMED_TUNINGS)

#: Drop A taken down a fourth: the shape the player's file is really in, and
#: the shape no name covers.
DROP_E = {s: v - 5 for s, v in TUNINGS["Drop A"].items()}


# ── the tuning strip ────────────────────────────────────────────────────────

class TestATuningNobodyNamedIsStillTheOne:

    def test_an_unnamed_written_tuning_is_in_the_list_at_nothing(self):
        names = dict((shift, name) for name, shift in reachable_tunings(DROP_E))
        assert 0 in names
        assert names[0] == " ".join(tuning_notes(DROP_E))

    def test_and_the_named_ones_are_still_offered_above_it(self):
        shifts = dict(reachable_tunings(DROP_E))
        assert shifts["Drop A"] == 5

    def test_a_named_tuning_is_not_listed_twice(self):
        drop_c = reachable_tunings(TUNINGS["Drop C"])
        at_nothing = [name for name, shift in drop_c if shift == 0]
        assert at_nothing == ["Drop C"]

    def test_nothing_is_reachable_from_nothing(self):
        assert reachable_tunings({}) == []


class TestTheStripSaysWhatTheFileIsIn:
    """Fails on the unfixed code: the strip read AEADF#B* for a Drop E tab."""

    def _screen(self, tuning):
        notes = [NoteEvent(timestamp_ms=0.0, duration_ms=500.0,
                           midi_note=tuning[6], string=6, fret=0, measure=0)]
        screen = PlayingScreen.__new__(PlayingScreen)
        screen._timeline = Timeline(
            notes, SongMetadata(title="t", tempo=120, tuning=dict(tuning)))
        screen._transpose = 0
        return screen

    def test_the_written_tuning_is_the_starred_one(self):
        segments = self._screen(DROP_E).tuning_segments()
        written = [notes for notes, role, _ in segments
                   if role in ("written", "both")]
        assert written == ["".join(tuning_notes(DROP_E))]

    def test_and_it_is_the_one_being_played(self):
        segments = self._screen(DROP_E).tuning_segments()
        played = [notes for notes, role, _ in segments
                  if role in ("played", "both")]
        assert played == ["".join(tuning_notes(DROP_E))]

    def test_as_written_is_no_transpose_at_all(self):
        segments = self._screen(DROP_E).tuning_segments()
        assert [shift for _, role, shift in segments
                if role == "both"] == [0]

    def test_a_named_tuning_is_unchanged(self):
        segments = self._screen(TUNINGS["Drop C"]).tuning_segments()
        assert [notes for notes, role, _ in segments
                if role == "both"] == ["".join(tuning_notes(TUNINGS["Drop C"]))]

    def test_the_key_cannot_offer_what_it_will_not_do(self):
        """The property R and its own line are already held to.

        Walked from every position in the list, because the order is built
        from the WRITTEN tuning: the timeline carries the tuning being
        played, so moving the transpose has to move both together.
        """
        order, _here = self._screen(DROP_E)._tuning_order()
        for i, (_name, shift) in enumerate(order):
            screen = self._screen({s: v + shift for s, v in DROP_E.items()})
            screen._transpose = shift
            screen._say = lambda *a, **k: None
            order_now, here_now = screen._tuning_order()
            assert here_now == i
            label = screen.tuning_step_label()
            up = order_now[i + 1][1] if i + 1 < len(order_now) else None
            if up is None:
                assert label.split("Shift+R")[0].rstrip().endswith("—")
            else:
                assert screen._choose_tuning(up) == ("transpose", up)


# ── identical tracks ────────────────────────────────────────────────────────

def _track(song, name, bars, tuning=None, number=1):
    """One guitar track whose bars are [(duration, [(string, fret)...])...]."""
    track = gp.Track(song=song)
    track.number = number
    track.name = name
    track.fretCount = 24
    track.channel.instrument = 30
    pitches = list((tuning or TUNINGS["Standard"]).items())
    track.strings = [gp.GuitarString(number=s, value=v)
                     for s, v in sorted(pitches)]
    track.measures = []
    for header, bar in zip(song.measureHeaders, bars):
        measure = gp.Measure(track=track, header=header)
        voice = gp.Voice(measure=measure)
        beats = []
        for duration, specs in bar:
            b = gp.Beat(voice=voice)
            b.duration = gp.Duration(duration)
            b.notes = []
            for string, fret in specs:
                note = gp.Note(beat=b)
                note.value = fret
                note.string = string
                note.velocity = gp.Velocities.forte
                note.type = gp.NoteType.normal
                b.notes.append(note)
            b.status = gp.BeatStatus.normal if specs else gp.BeatStatus.rest
            beats.append(b)
        voice.beats = beats
        second = gp.Voice(measure=measure)
        second.beats = []
        measure.voices = [voice, second]
        track.measures.append(measure)
    return track


RIFF = [[(4, [(6, 3)]), (4, [(6, 5)]), (4, [(6, 3)]), (4, [(6, 0)])]]
OTHER = [[(4, [(1, 7)]), (4, [(1, 5)]), (4, [(2, 7)]), (4, [(1, 0)])]]
NOTHING = [[(4, []), (4, []), (4, []), (4, [])]]


@pytest.fixture
def twins(tmp_path):
    """A file whose tracks 0 and 1 are the same part, 2 is not, 3 is empty."""
    song = gp.Song()
    song.title = "Twins"
    song.tempo = 120
    song.measureHeaders = []
    header = gp.MeasureHeader()
    header.number = 1
    header.start = gp.Duration.quarterTime
    header.timeSignature = gp.TimeSignature()
    header.timeSignature.numerator = 4
    header.timeSignature.denominator = gp.Duration(4)
    song.measureHeaders.append(header)
    song.tracks = [
        _track(song, "Rhythm", RIFF, number=1),
        _track(song, "Lead", RIFF, number=2),
        _track(song, "Different", OTHER, number=3),
        _track(song, "Silent", NOTHING, number=4),
    ]
    path = tmp_path / "twins.gp5"
    guitarpro.write(song, str(path))
    return path


class TestTheFileReallyHoldsWhatTheFixtureClaims:
    """Without this the whole class could pass on a file nobody could read."""

    def test_four_guitar_tracks(self, twins):
        tracks = list_tracks(twins)
        assert [t["name"] for t in tracks] == ["Rhythm", "Lead", "Different",
                                               "Silent"]
        assert all(t["is_guitar"] for t in tracks)

    def test_the_twins_really_are_identical(self, twins):
        assert load_gp_file(twins, 0).notes == load_gp_file(twins, 1).notes

    def test_and_the_third_really_is_not(self, twins):
        assert load_gp_file(twins, 0).notes != load_gp_file(twins, 2).notes


class TestWhichTracksAreCopies:

    def test_the_later_copy_is_the_one_named(self, twins):
        assert duplicate_tracks(twins) == {1: 0}

    def test_a_track_that_plays_something_else_is_not_a_copy(self, twins):
        assert 2 not in duplicate_tracks(twins)

    def test_an_empty_track_is_not_a_copy_of_another_empty_one(self, twins):
        """Two tracks with no notes play nothing, which is not a part."""
        assert 3 not in duplicate_tracks(twins)

    def test_only_the_tracks_asked_about(self, twins):
        assert duplicate_tracks(twins, [0, 2]) == {}
        assert duplicate_tracks(twins, [1, 0]) == {0: 1}

    def test_a_file_it_cannot_read_has_no_twins(self, tmp_path):
        bad = tmp_path / "not-a-tab.gp5"
        bad.write_bytes(b"nothing of the kind")
        assert duplicate_tracks(bad, [0, 1]) == {}


class TestThePickerLeavesOneOut:

    def _app(self):
        from pickhero.ui.app import App
        app = App.__new__(App)
        app._tracks_cache_key = None
        app._tracks_cache = []
        app._dupes_cache_key = None
        app._dupes_cache = {}
        return app

    def test_the_copy_is_not_offered(self, twins):
        assert [i for i, _ in self._app()._track_options(twins)] == [0, 2, 3]

    def test_the_numbers_keep_the_real_track(self, twins):
        """A gap, not a renumbering: a merge and a run history point at it."""
        labels = dict(self._app()._track_options(twins))
        assert labels[2].startswith("3. ")

    def test_the_track_being_played_is_never_hidden(self, twins):
        """Or the picker's `*` marks a row that does not exist."""
        app = self._app()
        assert [i for i, _ in app._track_options(twins, [1])] == [0, 1, 2, 3]
        assert app._hidden_tracks(twins, [1]) == {}

    def test_a_track_of_the_live_merge_is_never_hidden(self, twins):
        app = self._app()
        assert [i for i, _ in app._track_options(twins, [2, 1])] == [0, 1, 2, 3]

    def test_a_file_with_no_copies_offers_everything(self, twins, tmp_path):
        app = self._app()
        assert app._duplicates_of(twins) == {1: 0}
        # A different file re-reads rather than believing the last answer.
        other = tmp_path / "one.gp5"
        other.write_bytes(b"not a tab")
        assert app._duplicates_of(other) == {}


class TestSayingWhatWasLeftOut:
    """A row that simply vanishes cannot be told from a reader that broke."""

    def _screen(self, hidden, names, merge=()):
        screen = PlayingScreen.__new__(PlayingScreen)
        screen._track_options = [(0, "1. Rhythm"), (2, "3. Different")]
        screen._track_index = 0
        screen._merge_tracks = list(merge)
        screen._merge_picked = list(merge)
        screen._hidden_tracks = dict(hidden)
        screen._track_names = dict(names)
        return screen

    def test_it_names_the_copy_and_the_original(self):
        line = self._screen({1: 0}, {0: "Rhythm", 1: "Lead"})._duplicate_line()
        assert line == "2. Lead hidden — same notes as 1."

    def test_several_are_counted(self):
        line = self._screen({1: 0, 3: 0},
                            {0: "Rhythm"})._duplicate_line()
        assert line == "2 identical copies hidden"

    def test_the_wording_fits_the_narrowest_panel_there_is(self):
        """_fit_line shrinks the MIDDLE away, so a sentence too long for the
        box comes out as "2. Lead Guitar hidden ... ys exactly what 1. does"
        -- which is worse than saying less."""
        import pygame
        from pickhero.ui.scrolling import (TRACK_MENU_MIN_WIDTH, _get_font)
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            font = _get_font("arial", 15)
            for hidden, names in (({1: 0}, {1: "Lead Guitar"}),
                                  ({1: 0, 3: 0}, {})):
                line = self._screen(hidden, names)._duplicate_line()
                assert font.size(line)[0] <= TRACK_MENU_MIN_WIDTH + 56 - 32
        finally:
            pygame.display.quit()
            pygame.quit()

    def test_nothing_hidden_says_nothing(self):
        assert self._screen({}, {})._duplicate_line() == ""

    def test_the_merge_line_wins_while_tracks_are_marked(self):
        screen = self._screen({1: 0}, {0: "Rhythm", 1: "Lead"}, merge=[0, 2])
        assert (screen._merge_line() or screen._duplicate_line()).startswith(
            "merge:")


class TestTheLineIsReallyOnTheScreen:
    """A sentence nothing blits is a feature that ships doing nothing."""

    @pytest.fixture(autouse=True)
    def _display_up(self, isolated_config):
        import pygame
        pygame.init()
        pygame.display.set_mode((1280, 720))
        yield
        pygame.display.quit()
        pygame.quit()

    def _screen(self):
        from pickhero.config import Config
        from pickhero.tabs.timeline import MeasureInfo
        notes = [NoteEvent(timestamp_ms=i * 500.0, duration_ms=400.0,
                           midi_note=40 + i, string=6, fret=i, measure=0)
                 for i in range(4)]
        timeline = Timeline(
            notes, SongMetadata(title="t", tempo=120,
                                tuning=dict(TUNINGS["Standard"])),
            measures=[MeasureInfo(index=0, start_ms=0.0, end_ms=2000.0)])
        screen = PlayingScreen(timeline, config=Config())
        screen.set_track_options(
            [(0, "1. Rhythm Guitar"), (2, "3. Clean Guitar")], 0,
            hidden={1: 0},
            names={0: "Rhythm Guitar", 1: "Lead Guitar", 2: "Clean Guitar"})
        screen._track_menu_open = True
        return screen

    def _panel(self, screen):
        """Where the panel landed, and the row its note is drawn in."""
        from pickhero.ui.scrolling import (TRACK_MENU_ROW_H, _get_font,
                                           track_menu_box)
        rows = [label for _, label in screen._track_options]
        width, height = track_menu_box(
            [_get_font("arial", 18).size(r)[0] for r in rows], len(rows))
        x = int(1280 / 2 - width / 2)
        y = int(720 / 2 - height / 2)
        note_y = y + 40 + TRACK_MENU_ROW_H * len(rows) + 4
        return x, y, width, height, note_y

    def _ink(self, screen, band=None):
        import pygame
        surface = pygame.Surface((1280, 720))
        before = surface.copy()
        screen._draw_track_menu(surface)
        top, bottom = band or (0, 720)
        return sum(1 for x in range(1280) for y in range(top, bottom)
                   if surface.get_at((x, y)) != before.get_at((x, y)))

    def test_the_panel_is_drawn(self):
        assert self._ink(self._screen()) > 0

    def test_and_the_note_adds_ink_of_its_own(self):
        """Against the SAME panel with nothing hidden, not against black:
        the panel's own ground fills that row either way."""
        import pygame
        screen = self._screen()
        _x, _y, _w, _h, note_y = self._panel(screen)
        said = pygame.Surface((1280, 720))
        screen._draw_track_menu(said)
        bare = self._screen()
        bare._hidden_tracks = {}
        silent = pygame.Surface((1280, 720))
        bare._draw_track_menu(silent)
        differ = sum(1 for x in range(1280) for y in range(note_y, note_y + 20)
                     if said.get_at((x, y)) != silent.get_at((x, y)))
        assert differ > 100

    def test_the_note_is_inside_the_panel(self):
        screen = self._screen()
        x, y, width, height, note_y = self._panel(screen)
        assert y < note_y < y + height

    def test_the_line_fits_inside_the_panel(self):
        """_fit_line shrinks the middle away; a line wider than the box
        would be centred and cut at BOTH ends."""
        from pickhero.ui.scrolling import _get_font, track_menu_box
        screen = self._screen()
        title = _get_font("arial", 15)
        rows = [label for _, label in screen._track_options]
        width, _h = track_menu_box([_get_font("arial", 18).size(r)[0]
                                    for r in rows], len(rows))
        drawn = screen._fit_line(title, screen._duplicate_line(),
                                 width - 32, (255, 255, 255))
        assert drawn.get_width() <= width - 32
