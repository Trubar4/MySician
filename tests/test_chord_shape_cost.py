"""The chord blocks re-derived their own shapes sixty times a second.

*"Ja bitte, schau dir die Frames an."* His log reads `frame_ms_median 15.6`
with `frames_over_budget_percent 34`, against the 3.7 and 4.5 ms this project
recorded for the two views -- and those figures were taken on Thunder's lead,
**167 notes**, the sparsest track in the folder and the one song where the
chord blocks cost nothing at all.

`shapes_in` says in its own docstring that it is "built once per song... this
walks every note in the piece, which is exactly the kind of loop this project
has already had to move out of a frame three times". Both block paths called
`shape_of` per group per FRAME instead -- harmless while the blocks were off by
default, a permanent cost from the moment the marking became always-on.

The saving is 0.05 to 0.78 ms and the frame's own run-to-run spread here is
over a millisecond, so this is asserted as a COUNT. The correctness half is the
better reason to keep it: the grip cards, the board's blocks and the sheet's
blocks now read ONE answer, where the cards came off the written timeline and
the blocks re-derived from the FILTERED notes on screen.
"""

import pygame
import pytest

from pickhero.config import Config
from pickhero.tabs import chord_shapes
from pickhero.tabs.timeline import NoteEvent, Timeline
from pickhero.matcher import MatchType, NoteMatcher
from pickhero.ui.scrolling import CHORD_BLOCKS, CHORD_OFF, PlayingScreen


#: Real grips, because `shape_of` refuses anything the namer will not name --
#: a cluster of adjacent semitones produces no shape and would leave the map
#: empty, which the two call COUNTS below would pass just as happily. That is
#: what `test_the_map_is_built_and_really_holds_the_chords` is for, and it is
#: what caught the first version of this fixture.
GRIPS = (
    ((6, 0), (5, 2), (4, 2)),      # E5
    ((6, 3), (5, 5), (4, 5)),      # G5
    ((6, 5), (5, 7), (4, 7)),      # A5
)
OPEN_MIDI = {6: 40, 5: 45, 4: 50, 3: 55, 2: 59, 1: 64}


def _chord_song(chords=24, step=500.0):
    """A song that strums, which is where the blocks cost anything."""
    notes = []
    for i in range(chords):
        at = 1000.0 + i * step
        for string, fret in GRIPS[i % len(GRIPS)]:
            notes.append(NoteEvent(
                timestamp_ms=at, midi_note=OPEN_MIDI[string] + fret,
                string=string, fret=fret, duration_ms=step * 0.9))
    return Timeline(notes=notes)


class TestTheShapeIsAnsweredOncePerSong:

    @pytest.fixture(autouse=True)
    def _screen_up(self, isolated_config):
        pygame.init()
        pygame.display.set_mode((1280, 720))
        yield
        pygame.display.quit()
        pygame.quit()

    def _screen(self, view="standard", level=CHORD_BLOCKS):
        tl = _chord_song()
        screen = PlayingScreen(tl, config=Config())
        screen._view = view
        screen._chord_level = level
        screen._audio_enabled = True
        screen._playing = True
        screen._playback_ms = 3000.0
        screen._matcher = NoteMatcher(tl, timing_window_ms=150.0)
        for i, note in enumerate(tl.notes):
            if note.timestamp_ms < 3000.0:
                screen._matcher._set_state(
                    note, MatchType.HIT if i % 2 else MatchType.MISS)
        return screen

    def _shape_calls(self, screen, frames=10):
        surface = pygame.Surface((1280, 720))
        screen.render(surface)                 # build the per-song answers
        calls = []
        real = chord_shapes.shape_of
        chord_shapes.shape_of = lambda notes: (calls.append(1), real(notes))[1]
        try:
            for i in range(frames):
                screen._playback_ms = 3000.0 + i * 16.7
                screen.render(surface)
        finally:
            chord_shapes.shape_of = real
        return len(calls)

    def test_the_board_asks_nothing_in_a_frame(self):
        assert self._shape_calls(self._screen("standard")) == 0

    def test_the_sheet_asks_nothing_in_a_frame(self):
        """The sheet is where it cost most: 64 calls a frame on a real song."""
        assert self._shape_calls(self._screen("hybrid")) == 0

    def test_the_map_is_built_and_really_holds_the_chords(self):
        """A map that came back empty would also pass the two counts above."""
        screen = self._screen()
        screen.render(pygame.Surface((1280, 720)))
        assert len(screen._chord_shape_at) == 24
        assert all(shape is not None
                   for shape in screen._chord_shape_at.values())

    def test_the_blocks_are_still_drawn(self):
        """The whole point: cheaper must not mean absent."""
        screen = self._screen(level=CHORD_BLOCKS)
        with_blocks = pygame.Surface((1280, 720))
        screen.render(with_blocks)
        screen._chord_level = CHORD_OFF
        without = pygame.Surface((1280, 720))
        screen.render(without)
        assert (pygame.image.tostring(with_blocks, "RGB")
                != pygame.image.tostring(without, "RGB"))

    def test_the_cards_and_the_blocks_read_the_same_answer(self):
        """They did not: the cards come off the written timeline and the
        blocks re-derived from the notes left after the fret filter, so a
        muted string could put a different name on the block from the one on
        the card beside it."""
        screen = self._screen()
        screen.render(pygame.Surface((1280, 720)))
        for when, shape in screen._chord_shapes:
            assert screen._chord_shape_at[int(round(when))] == shape


class TestTheLogSaysWhatWasBeingDrawn:
    """`frame_ms_median 15.6` is a sheet working as designed OR a board in
    trouble, and nothing in the log could tell them apart."""

    @pytest.fixture(autouse=True)
    def _screen_up(self, isolated_config):
        pygame.init()
        pygame.display.set_mode((1280, 720))
        yield
        pygame.display.quit()
        pygame.quit()

    def _line(self, view, level):
        import io
        tl = _chord_song()
        screen = PlayingScreen(tl, config=Config())
        screen._view = view
        screen._chord_level = level
        screen._playing = True
        screen._playback_ms = 3000.0
        screen.render(pygame.Surface((1234, 567)))
        screen.record_frame_ms(12.3)
        fh = io.StringIO()
        screen._frame_line(fh)
        return fh.getvalue()

    def test_it_names_the_view(self):
        assert "hybrid view" in self._line("hybrid", CHORD_BLOCKS)
        assert "standard view" in self._line("standard", CHORD_BLOCKS)

    def test_it_names_the_chord_rung(self):
        """The blocks cost +0.8 to +2.7 ms on a song that strums, measured
        over five repeats a condition -- so which rung was up is part of
        what the number is a number of."""
        assert "chords blocks" in self._line("standard", CHORD_BLOCKS)
        assert "chords off" in self._line("standard", CHORD_OFF)

    def test_it_names_the_window(self):
        """These times are mostly fill and blit, so they scale with pixels."""
        assert "1234x567" in self._line("standard", CHORD_BLOCKS)
