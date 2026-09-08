"""Naming the chord under a group of notes.

Guitar Pro has a field for this and it is empty -- 5601 beats in the player's
own tab, not one chord name -- so the name has to come from the notes. That
is a READING of what is written rather than an invention, and this is the
line the module has to stay on the right side of: a name that is wrong now
and then teaches the player to distrust the line, and then it is worth
nothing even when it is right.
"""

import pytest

from pickhero.tabs.chords import name_chord

OPEN = {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40}


def _shape(*pairs):
    """MIDI notes for (string, fret) pairs, as a guitarist would grip them."""
    return [OPEN[s] + f for s, f in pairs]


class TestTheChordsAGuitaristPlays:
    def test_open_g(self):
        assert name_chord(_shape((6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 3))) == "G"

    def test_open_d(self):
        assert name_chord(_shape((4, 0), (3, 2), (2, 3), (1, 2))) == "D"

    def test_open_e_minor(self):
        assert name_chord(_shape((6, 0), (5, 2), (4, 2), (3, 0), (2, 0), (1, 0))) == "Em"

    def test_open_c(self):
        assert name_chord(_shape((5, 3), (4, 2), (3, 0), (2, 1), (1, 0))) == "C"

    def test_open_a_minor(self):
        assert name_chord(_shape((5, 0), (4, 2), (3, 2), (2, 1), (1, 0))) == "Am"

    def test_a_barre_f(self):
        assert name_chord(_shape((6, 1), (5, 3), (4, 3), (3, 2), (2, 1), (1, 1))) == "F"


class TestSevenths:
    def test_a_dominant_seventh(self):
        assert name_chord([48, 52, 55, 58]) == "C7"

    def test_a_major_seventh(self):
        assert name_chord([48, 52, 55, 59]) == "Cmaj7"

    def test_a_minor_seventh(self):
        assert name_chord([49, 52, 56, 59]) == "C#m7"

    def test_a_seventh_without_its_fifth_is_still_that_seventh(self):
        """Guitarists drop the fifth constantly and the shape is unambiguous."""
        assert name_chord([48, 52, 58]) == "C7"

    def test_a_triad_missing_a_note_is_not_named(self):
        """Dropping a note from a triad leaves something that is not a triad,
        and naming it anyway is the guess this refuses to make."""
        assert name_chord([48, 52]) is None       # C and E: a third


class TestWhenItRefuses:
    def test_a_single_note_is_not_a_chord(self):
        assert name_chord([64]) is None

    def test_the_same_note_in_two_octaves_is_not_a_chord(self):
        assert name_chord([40, 52]) is None

    def test_an_interval_that_is_not_a_fifth_is_not_a_chord(self):
        """A fourth counts: C under F is an F5 with its fifth in the bass,
        which is the same two notes as F-C and the same chord. Everything
        else is an interval, and calling a third "C" would be a claim about
        a note nobody played."""
        for interval in (1, 2, 3, 4, 6, 8, 9, 10, 11):
            assert name_chord([48, 48 + interval]) is None, interval
        assert name_chord([48, 53]) == "F5"      # the fourth, inverted

    def test_a_cluster_nobody_has_a_name_for_gets_none(self):
        assert name_chord([48, 49, 50, 51]) is None

    def test_no_notes_at_all(self):
        assert name_chord([]) is None


class TestPowerChords:
    def test_root_and_fifth(self):
        assert name_chord([40, 47]) == "E5"

    def test_with_the_octave_on_top(self):
        assert name_chord([40, 47, 52]) == "E5"

    def test_a_fifth_played_the_other_way_up_is_still_the_same_chord(self):
        """B under E is an E5 with the fifth in the bass, not a B chord."""
        assert name_chord([47, 52]) == "E5"


class TestWhichNoteItIsNamedOver:
    def test_the_root_in_the_bass_gives_a_plain_name(self):
        assert name_chord([55, 59, 62]) == "G"

    def test_an_inversion_is_named_over_its_bass(self):
        """B, E, G is an E minor with B underneath -- which is what a player
        calls it, and what the shape actually sounds like."""
        assert name_chord([47, 52, 55]) == "Em/B"

    def test_the_reading_whose_root_is_the_bass_wins(self):
        """C-E-G-A is both C6 and Am7; the bass decides."""
        assert name_chord([48, 52, 55, 57]) == "C6"
        assert name_chord([57, 60, 64, 67]) == "Am7"


class TestTheDisplayAsksItOnce:
    def _screen(self, notes):
        import pygame
        from pickhero.config import Config
        from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline
        from pickhero.ui.scrolling import PlayingScreen
        pygame.init()
        events = []
        for when, group in notes:
            for string, fret in group:
                events.append(NoteEvent(timestamp_ms=when, duration_ms=400.0,
                                        midi_note=OPEN[string] + fret,
                                        string=string, fret=fret, measure=0))
        timeline = Timeline(events, SongMetadata(title="x", tempo=120))
        screen = PlayingScreen(timeline, config=Config())
        screen.render(pygame.Surface((1400, 800)))
        return screen

    G = ((6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 3))
    D = ((4, 0), (3, 2), (2, 3), (1, 2))

    def test_only_the_changes_are_kept(self):
        """A name repeated over eight bars of the same chord is eight bars of
        noise; the moment the hand has to move is the thing worth seeing."""
        screen = self._screen([(0.0, self.G), (500.0, self.G),
                               (1000.0, self.G), (1500.0, self.D)])
        assert [name for _, name in screen._chord_names] == ["G", "D"]

    def test_a_solo_of_single_notes_produces_none(self):
        screen = self._screen([(float(i) * 200, ((1, 10 + i),)) for i in range(6)])
        assert screen._chord_names == []

    def test_the_change_is_marked_where_it_happens(self):
        screen = self._screen([(0.0, self.G), (1500.0, self.D)])
        assert dict(screen._chord_names)[1500.0] == "D"
