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


class TestEveryWayAKeyboardCanSayShift:
    """The player's machine sent a capital letter with no shift bit in
    event.mod at all, so Shift+U fell through to the search box and Shift+C
    fell through to the noise gate. One helper answers for every shortcut in
    the playing screen now, so the class of fault is closed rather than the
    two instances of it.
    """

    def _screen(self):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.scrolling import PlayingScreen

        pygame.init()
        pygame.display.set_mode((1280, 720))
        notes = [_note(s, f, m, 1000.0) for s, f, m in
                 ((6, 0, 40), (5, 2, 47), (4, 2, 52))]
        timeline = Timeline(notes, SongMetadata(title="t", tempo=120))
        return PlayingScreen(timeline, config=Config(), song_key="t")

    def _press(self, screen, **kwargs):
        import pygame

        screen.handle_event(pygame.event.Event(pygame.KEYDOWN,
                                               key=pygame.K_c, **kwargs))

    def test_the_event_says_so(self):
        import pygame

        screen = self._screen()
        self._press(screen, mod=pygame.KMOD_SHIFT, unicode="C")
        assert screen._chord_mode

    def test_only_the_character_says_so(self):
        """What the player's keyboard actually sent."""
        screen = self._screen()
        self._press(screen, mod=0, unicode="C")
        assert screen._chord_mode

    def test_a_plain_c_still_reaches_the_noise_gate(self):
        screen = self._screen()
        before = screen._noise_gate_db
        self._press(screen, mod=0, unicode="c")
        assert not screen._chord_mode
        assert screen._noise_gate_db != before

    def test_it_survives_a_key_with_no_unicode_at_all(self):
        """Some events carry none, and a key handler that raises takes the
        app down with it."""
        import pygame

        screen = self._screen()
        screen.handle_event(pygame.event.Event(pygame.KEYDOWN,
                                               key=pygame.K_c, mod=0))
        assert not screen._chord_mode


class TestTheCardsAndTheTextShareTheCorner:
    """Both wanted the top left. Drawn over each other neither can be read,
    which is what the player's screenshot showed -- the song title, the
    track, the tuning and the sync line all straight through the diagrams.
    The text is the half that can move.
    """

    def _screen(self, chord_view):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.scrolling import PlayingScreen

        pygame.init()
        surface = pygame.display.set_mode((1911, 1105))
        config = Config()
        config.chord_view = chord_view
        notes = [_note(s, f, m, 1000.0) for s, f, m in
                 ((6, 0, 40), (5, 2, 47), (4, 2, 52))]
        timeline = Timeline(notes, SongMetadata(title="t", tempo=120))
        screen = PlayingScreen(timeline, config=config, song_key="t")
        screen.render(surface)
        return screen, surface

    def test_the_text_starts_past_the_cards(self):
        from pickhero.ui.scrolling import CHORD_CARD_GAP, CHORD_CARD_SCALE
        from pickhero.ui.chord_view import card_size

        screen, _ = self._screen(True)
        width, _height = card_size(CHORD_CARD_SCALE)
        cards_right = 12 + 2 * (width + CHORD_CARD_GAP)
        assert screen._hud_left_x() >= cards_right

    def test_and_the_music_starts_below_them(self):
        """On the scrolling board the cards sat above a lane band that
        starts halfway down the window. The sheet reaches into that corner,
        and the first thing the player saw was a diagram over his top
        string."""
        from pickhero.ui.scrolling import CHORD_CARD_SCALE
        from pickhero.ui.chord_view import card_size

        screen, _ = self._screen(True)
        assert screen._hud_top_used() >= card_size(CHORD_CARD_SCALE)[1]

    def test_and_they_are_a_tenth_smaller_than_the_boards_own(self):
        from pickhero.ui.scrolling import CHORD_CARD_SCALE
        assert CHORD_CARD_SCALE == 0.9

    def test_and_stays_where_it_was_with_the_cards_off(self):
        """Nothing moves for a player who never turns this on."""
        screen, _ = self._screen(False)
        assert screen._hud_left_x() == 12

    def test_a_song_with_no_chords_does_not_indent_it_either(self):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.scrolling import PlayingScreen

        pygame.init()
        surface = pygame.display.set_mode((1911, 1105))
        config = Config()
        config.chord_view = True
        timeline = Timeline([_note(6, 3, 43, 0.0)],
                            SongMetadata(title="t", tempo=120))
        screen = PlayingScreen(timeline, config=config, song_key="t")
        screen.render(surface)
        assert screen._chord_shapes == []
        assert screen._hud_left_x() == 12

    def test_both_cards_are_the_same_size(self):
        """The second was smaller to say "this one is next" -- the label
        already says that, and being smaller made the grip you have to
        PREPARE the harder of the two to read."""
        from pickhero.ui.chord_view import card_size

        assert card_size() == card_size(1.0)

    def test_they_are_bigger_than_they_were(self):
        """The AREA, not both edges: the diagram lies across now, so it is
        wider and a little shorter than the upright one it replaced. It was
        150x132 = 19800 to begin with."""
        from pickhero.ui.chord_view import card_size

        width, height = card_size()
        assert width * height >= 2 * 150 * 132

    def test_and_they_clear_the_board(self):
        """The chord names sit just above each block, and a card hanging
        into the lanes would cover them."""
        from pickhero.ui.chord_view import card_size

        screen, _ = self._screen(True)
        _width, height = card_size()
        assert 6 + height <= screen._last_layout.lane_top


class TestTheDiagramLiesTheWayTheBoardDoes:
    """Strings across, low E at the BOTTOM, frets left to right from the nut
    -- the same way round as the scrolling board, where string 6 sits on the
    lowest lane. A songbook prints the grid upright with the low string on
    the left, and mixing the two orientations in one app means rotating the
    picture in your head between one glance and the next.
    """

    def _grid(self, labelled=True):
        import pygame
        from pickhero.ui.chord_view import card_size, grid_rect

        pygame.init()
        pygame.display.set_mode((1280, 720))
        width, height = card_size()
        return grid_rect(pygame.Rect(12, 6, width, height), labelled)

    def test_the_low_E_is_at_the_bottom(self):
        from pickhero.ui.chord_view import string_rows

        rows = string_rows(self._grid())
        assert rows[0][0] == 6
        assert rows[0][1] == max(y for _, y in rows)

    def test_and_the_high_e_at_the_top(self):
        from pickhero.ui.chord_view import string_rows

        rows = string_rows(self._grid())
        assert rows[-1][0] == 1
        assert rows[-1][1] == min(y for _, y in rows)

    def test_it_matches_the_board_string_for_string(self):
        """The property that matters: the order down the card is the order
        down the lanes."""
        import pygame
        from pickhero.config import Config
        from pickhero.ui.chord_view import string_rows
        from pickhero.ui.scrolling import PlayingScreen, _Layout

        pygame.init()
        pygame.display.set_mode((1280, 720))
        layout = _Layout(screen_w=1280, screen_h=720, lane_height=60.0,
                         note_h=40.0, hit_zone_x=150.0, usable_width=1000.0,
                         pixels_per_ms=0.2, visible_window_ms=4000.0)
        board = [(s, layout.lane_top + (s - 0.5) * layout.lane_height)
                 for s in range(1, 7)]
        board.sort(key=lambda pair: pair[1])
        card = sorted(string_rows(self._grid()), key=lambda pair: pair[1])
        assert [s for s, _ in card] == [s for s, _ in board]

    def test_all_six_strings_are_there_and_evenly_spaced(self):
        from pickhero.ui.chord_view import string_rows

        rows = string_rows(self._grid())
        assert sorted(s for s, _ in rows) == [1, 2, 3, 4, 5, 6]
        gaps = [b - a for (_, a), (_, b) in zip(rows[1:][::-1], rows[::-1])]
        assert max(gaps) - min(gaps) < 0.001

    def test_the_grid_stays_inside_the_card(self):
        import pygame
        from pickhero.ui.chord_view import card_size, grid_rect

        pygame.init()
        pygame.display.set_mode((1280, 720))
        width, height = card_size()
        card = pygame.Rect(12, 6, width, height)
        for labelled in (True, False):
            grid = grid_rect(card, labelled)
            assert card.contains(grid), (labelled, grid, card)

    def test_there_is_room_before_the_nut_for_the_marks(self):
        """The rings and crosses go to the LEFT of the nut now, and drawn
        off the card they would simply not be there."""
        import pygame
        from pickhero.ui.chord_view import MARK_ROOM, card_size, grid_rect

        pygame.init()
        pygame.display.set_mode((1280, 720))
        width, height = card_size()
        card = pygame.Rect(12, 6, width, height)
        assert grid_rect(card, True).left - card.left >= MARK_ROOM

    def test_a_whole_card_draws_without_raising(self):
        import pygame
        from pickhero.ui.chord_view import card_size, draw_diagram

        pygame.init()
        surface = pygame.display.set_mode((1280, 720))
        width, height = card_size()
        for shape in (shape_of(_e_major()),
                      shape_of([_note(6, 12, 52), _note(5, 14, 59),
                                _note(4, 14, 64)])):
            draw_diagram(surface, pygame.Rect(12, 6, width, height), shape,
                         label="now")
            draw_diagram(surface, pygame.Rect(12, 6, width, height), shape,
                         label="next", dim=True)
