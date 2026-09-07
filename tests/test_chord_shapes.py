"""A chord as a grip, read out of the tab.

Measured before any of this was drawn: across three of the player's own
songs, 100, 92 and 96 % of the chord moments get a name from `chords.py`,
and each song contains only 3 to 13 distinct shapes -- so a song's whole
vocabulary is small and is built once. What the files do NOT carry is which
finger: two of the three give none at all and the third gives one for 41 of
120 positions, so no finger is ever drawn.
"""
import pytest

from pickhero.tabs.chord_shapes import (FRETS_SHOWN, ChordShape, changes_in,
                                        shape_of, shapes_in)
from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline


def _note(string, fret, midi, when=0.0, dead=False):
    return NoteEvent(timestamp_ms=when, duration_ms=500.0, midi_note=midi,
                     string=string, fret=fret, measure=0, dead=dead)


def _e_major():
    """The open E shape: 0-2-2-1-0-0 from the low string up."""
    return [_note(6, 0, 40), _note(5, 2, 47), _note(4, 2, 52),
            _note(3, 1, 56), _note(2, 0, 59), _note(1, 0, 64)]


class TestReadingTheGrip:

    def test_the_shape_is_the_strings_and_frets_that_were_written(self):
        shape = shape_of(_e_major())
        assert shape is not None
        assert shape.name == "E"
        assert shape.rows() == [(6, 0), (5, 2), (4, 2), (3, 1), (2, 0), (1, 0)]

    def test_a_string_nothing_is_written_on_has_no_fret(self):
        """Which on a guitar means "not struck" or "damped" -- the tab does
        not distinguish, so neither does the drawing."""
        shape = shape_of([_note(5, 0, 45), _note(4, 2, 52), _note(3, 2, 57)])
        assert shape is not None
        assert shape.rows()[0] == (6, None)
        assert shape.fret_on(6) is None
        assert shape.fret_on(4) == 2

    def test_a_single_note_is_not_a_grip(self):
        assert shape_of([_note(6, 3, 43)]) is None

    def test_and_neither_is_something_the_namer_will_not_name(self):
        """A diagram with no name over it is a picture nobody can look up."""
        assert shape_of([_note(6, 0, 40), _note(5, 1, 46)]) is None

    def test_a_dead_note_is_not_part_of_the_shape(self):
        """It has no pitch, so it cannot be in a chord and must not move the
        name."""
        notes = _e_major() + [_note(1, 5, 69, dead=True)]
        assert shape_of(notes).name == "E"

    def test_a_shape_up_the_neck_slides_the_window(self):
        """An open-position grid with a dot on the twelfth fret is not a
        diagram."""
        shape = shape_of([_note(6, 12, 52), _note(5, 14, 59),
                          _note(4, 14, 64)])
        assert shape is not None
        assert shape.base_fret == 12
        assert all(0 <= f - shape.base_fret <= FRETS_SHOWN
                   for _, f in shape.frets)

    def test_an_open_shape_keeps_the_nut(self):
        assert shape_of(_e_major()).base_fret == 0

    def test_two_readings_of_one_grip_are_the_same_thing(self):
        """Which is what telling a repeated chord from a new one needs."""
        assert shape_of(_e_major()) == shape_of(list(reversed(_e_major())))
        assert len({shape_of(_e_major()), shape_of(_e_major())}) == 1


class TestWalkingTheSong:

    def _song(self):
        notes = []
        for i, when in enumerate((0.0, 1000.0, 2000.0, 3000.0)):
            frets = ((6, 0, 40), (5, 2, 47), (4, 2, 52)) if i != 2 else \
                    ((5, 0, 45), (4, 2, 52), (3, 2, 57))
            notes += [_note(s, f, m, when) for s, f, m in frets]
        notes.append(_note(6, 5, 45, 4000.0))          # a single note
        return Timeline(notes, SongMetadata(title="t", tempo=120))

    def test_every_chord_moment_is_found(self):
        assert [w for w, _ in shapes_in(self._song())] == [0.0, 1000.0,
                                                           2000.0, 3000.0]

    def test_a_lone_note_is_not_one_of_them(self):
        assert all(w != 4000.0 for w, _ in shapes_in(self._song()))

    def test_only_the_CHANGES_are_kept(self):
        """A chord repeated over eight bars is one grip, not sixteen: showing
        it again at every strum buries the moment that needs preparing."""
        changes = changes_in(self._song())
        assert [w for w, _ in changes] == [0.0, 2000.0, 3000.0]
        assert [s.name for _, s in changes] == ["E5", "A5", "E5"]

    def test_a_song_with_no_chords_yields_nothing(self):
        lone = Timeline([_note(6, 3, 43, 0.0), _note(6, 5, 45, 1000.0)],
                        SongMetadata(title="t", tempo=120))
        assert changes_in(lone) == []


class TestTheCardsOnScreen:
    """The grip being PLAYED and the one coming next -- the two the reference
    app keeps in the corner, and the two a player needs: what the hand is on,
    and what it has to move to."""

    def _screen(self):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.scrolling import PlayingScreen

        pygame.init()
        surface = pygame.display.set_mode((1280, 720))
        notes = []
        for when, frets in ((0.0, ((6, 0, 40), (5, 2, 47), (4, 2, 52))),
                            (4000.0, ((5, 0, 45), (4, 2, 52), (3, 2, 57))),
                            (8000.0, ((6, 3, 43), (5, 2, 47), (4, 0, 50)))):
            notes += [_note(s, f, m, when) for s, f, m in frets]
        timeline = Timeline(notes, SongMetadata(title="t", tempo=120))
        screen = PlayingScreen(timeline, config=Config(), song_key="t")
        screen.render(surface)              # builds the shapes
        return screen, surface

    def test_it_names_what_is_held_and_what_comes_next(self):
        screen, _ = self._screen()
        screen._playback_ms = 5000.0
        now, nxt = screen._chord_now_and_next()
        assert now.name == "A5" and nxt.name == "G"

    def test_a_chord_stays_up_until_the_next_one_starts(self):
        """Not the NEAREST grip: a chord is held, so the card must not flip
        to the next one halfway through the bar."""
        screen, _ = self._screen()
        for ms in (100.0, 2000.0, 3900.0):
            screen._playback_ms = ms
            assert screen._chord_now_and_next()[0].name == "E5", ms

    def test_before_the_first_chord_it_shows_what_is_COMING(self):
        screen, _ = self._screen()
        screen._playback_ms = -2000.0
        now, nxt = screen._chord_now_and_next()
        assert now is None and nxt.name == "E5"

    def test_after_the_last_one_there_is_no_next(self):
        screen, _ = self._screen()
        screen._playback_ms = 20_000.0
        assert screen._chord_now_and_next()[1] is None

    def test_a_song_with_no_chords_draws_no_cards(self):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.scrolling import PlayingScreen

        pygame.init()
        surface = pygame.display.set_mode((1280, 720))
        timeline = Timeline([_note(6, 3, 43, 0.0), _note(6, 5, 45, 1000.0)],
                            SongMetadata(title="t", tempo=120))
        screen = PlayingScreen(timeline, config=Config(), song_key="t")
        screen.render(surface)
        assert screen._chord_shapes == []
        assert screen._chord_now_and_next() == (None, None)

    def test_shift_c_turns_the_mode_on_and_off_and_says_so(self):
        import pygame

        screen, _ = self._screen()
        screen._chord_mode = True
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_c, mod=pygame.KMOD_SHIFT))
        assert not screen._chord_mode
        assert "off" in screen._status_note_text()

    def test_drawing_one_costs_a_fraction_of_a_frame(self):
        """It runs sixty times a second over a list that grows with the
        song, which is the loop this project has had to move out of a frame
        three times already."""
        import time

        screen, surface = self._screen()
        screen._playback_ms = 6000.0
        for _ in range(5):
            screen.render(surface)
        started = time.perf_counter()
        for _ in range(60):
            screen.render(surface)
        with_cards = (time.perf_counter() - started) / 60
        screen._chord_mode = False
        started = time.perf_counter()
        for _ in range(60):
            screen.render(surface)
        without = (time.perf_counter() - started) / 60
        assert with_cards - without < 0.002, (with_cards, without)


class TestTheChordBlocks:
    """Six fret numbers spread down six lanes are not a shape. A block that
    spans the strings says "this is one grip" before a number has been read.

    It goes UNDER the note heads rather than instead of them: the heads keep
    their own per-string verdicts, which is the thing this app spent a whole
    chapter learning to report.
    """

    def _screen(self):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.scrolling import PlayingScreen

        pygame.init()
        surface = pygame.display.set_mode((1280, 720))
        notes = [_note(s, f, m, 1000.0) for s, f, m in
                 ((6, 0, 40), (5, 2, 47), (4, 2, 52))]
        notes.append(_note(3, 7, 62, 2000.0))           # a single note
        timeline = Timeline(notes, SongMetadata(title="t", tempo=120))
        screen = PlayingScreen(timeline, config=Config(), song_key="t")
        screen._chord_mode = True
        screen.render(surface)
        return screen, surface

    def test_a_chord_gets_one_block(self):
        screen, surface = self._screen()
        screen._playback_ms = 500.0
        screen.render(surface)
        blocks = screen._chord_blocks_in_view(screen._last_layout)
        assert len(blocks) == 1
        assert blocks[0][4].name == "E5"

    def test_a_single_note_gets_none(self):
        """Kid Rock writes 12 chords in 444 moments; a block round every
        lone note would be a box round the whole song."""
        screen, surface = self._screen()
        screen._playback_ms = 1900.0
        screen.render(surface)
        blocks = screen._chord_blocks_in_view(screen._last_layout)
        assert all(b[4].strings >= 2 for b in blocks)
        assert all(b[5][0].timestamp_ms == 1000.0 for b in blocks)

    def test_the_block_spans_exactly_the_strings_that_are_written(self):
        screen, surface = self._screen()
        screen._playback_ms = 500.0
        screen.render(surface)
        x, width, top, bottom, shape, notes = \
            screen._chord_blocks_in_view(screen._last_layout)[0]
        layout = screen._last_layout
        assert top == pytest.approx(layout.lane_top + 3 * layout.lane_height)
        assert bottom == pytest.approx(layout.lane_top + 6 * layout.lane_height)

    def test_one_wrong_string_colours_the_whole_block_wrong(self):
        """The block cannot show six answers, so it shows the worst -- a
        chord with one string wrong is not a chord that went well. The heads
        on top keep the detail."""
        from pickhero.matcher import MatchType
        from pickhero.ui.colors import get_theme

        screen, surface = self._screen()
        screen._audio_enabled = True
        notes = [n for n in screen._timeline.notes if n.timestamp_ms == 1000.0]
        states = {id(notes[0]): MatchType.MISS}

        class Stub:
            def get_note_state(self, note):
                return states.get(id(note), MatchType.HIT)

        screen._matcher = Stub()
        assert screen._chord_block_colour(notes) == get_theme().feedback_miss
        states.clear()
        assert screen._chord_block_colour(notes) == get_theme().feedback_hit

    def test_a_chord_still_being_played_is_not_judged(self):
        from pickhero.matcher import MatchType
        from pickhero.ui.colors import get_theme

        screen, surface = self._screen()
        screen._audio_enabled = True

        class Stub:
            def get_note_state(self, note):
                return MatchType.PENDING

        screen._matcher = Stub()
        notes = [n for n in screen._timeline.notes if n.timestamp_ms == 1000.0]
        assert screen._chord_block_colour(notes) == get_theme().lane_line

    def test_the_mode_is_off_until_it_is_asked_for(self):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.scrolling import PlayingScreen

        pygame.init()
        pygame.display.set_mode((1280, 720))
        timeline = Timeline([_note(6, 0, 40, 0.0)],
                            SongMetadata(title="t", tempo=120))
        assert not PlayingScreen(timeline, config=Config(),
                                 song_key="t")._chord_mode

    def test_the_blocks_cost_a_fraction_of_a_frame(self):
        import time

        screen, surface = self._screen()
        screen._playback_ms = 500.0
        for _ in range(5):
            screen.render(surface)
        started = time.perf_counter()
        for _ in range(60):
            screen.render(surface)
        on = (time.perf_counter() - started) / 60
        screen._chord_mode = False
        started = time.perf_counter()
        for _ in range(60):
            screen.render(surface)
        off = (time.perf_counter() - started) / 60
        assert on - off < 0.004, (on, off)
