"""The engraved page, and where the playhead goes on it."""

import pygame
import pytest

from pickhero.tabs.timeline import (
    MeasureInfo, NoteEvent, SongMetadata, Timeline,
)
from pickhero.ui.tab_view import (
    DEFAULT_ZOOM, TabEngraving, ZOOM_STEPS, _view_box,
)

verovio = pytest.importorskip("verovio")


@pytest.fixture(autouse=True)
def _display():
    pygame.init()
    pygame.display.set_mode((640, 480))
    yield
    pygame.display.quit()


def _song(bars=8, per_bar=4):
    notes = []
    for bar in range(bars):
        for beat in range(per_bar):
            notes.append(NoteEvent(
                timestamp_ms=bar * 2000.0 + beat * 500.0,
                duration_ms=500.0, midi_note=40 + beat,
                string=1 + (beat % 6), fret=beat, measure=bar,
                duration_quarters=1.0))
    measures = [MeasureInfo(index=b, start_ms=b * 2000.0,
                            end_ms=(b + 1) * 2000.0) for b in range(bars)]
    return Timeline(notes, SongMetadata(title="T", tempo=120,
                                        tuning={1: 64, 2: 59, 3: 55,
                                                4: 50, 5: 45, 6: 40}),
                    measures=measures)


class TestTheViewBox:
    def test_it_reads_the_page_units(self):
        assert _view_box('<svg viewBox="0 0 24000 31390">') == (24000.0, 31390.0)

    def test_a_page_without_one_claims_nothing(self):
        assert _view_box("<svg>") == (0.0, 0.0)


class TestEveryNoteIsFound:
    def test_the_whole_song_is_placed(self):
        """An engraving that silently loses a fifth of the song still looks
        like a tab, and the playhead would just skip those bars."""
        engraving = TabEngraving(_song())
        placed, written = engraving.found()
        assert placed == written

    def test_positions_are_fractions_of_the_page(self):
        """Not pixels. A page whose viewBox is 24000 wide rasterises to
        whatever pygame felt like, and a mapping that ignores that is out by
        a factor of twenty-five while looking plausible."""
        engraving = TabEngraving(_song())
        for _, _, x, y in engraving.spots:
            assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0


class TestZoomHasToBeVisible:
    def test_zooming_in_puts_fewer_bars_on_a_line(self):
        """The first version stepped verovio's `scale`, which made a bigger
        bitmap of the identical layout -- blitted to the window, nothing
        changed at all.

        Asserted on how far through the document the music reaches, not on
        the page count: the same song can still fit on one page at two zoom
        levels while its layout is completely different.
        """
        def reach(index):
            engraving = TabEngraving(_song(bars=24), zoom_index=index)
            _, page, _, y = engraving.spots[-1]
            return page + y

        assert reach(len(ZOOM_STEPS) - 1) > reach(0) * 1.5

    def test_the_steps_only_narrow(self):
        assert list(ZOOM_STEPS) == sorted(ZOOM_STEPS, reverse=True)

    def test_no_zoom_loses_a_note(self):
        song = _song(bars=12)
        for index in range(len(ZOOM_STEPS)):
            engraving = TabEngraving(song, zoom_index=index)
            assert engraving.found()[0] == engraving.found()[1]

    def test_the_index_is_clamped_rather_than_crashing(self):
        assert TabEngraving(_song(), zoom_index=99).zoom == ZOOM_STEPS[-1]
        assert TabEngraving(_song(), zoom_index=-5).zoom == ZOOM_STEPS[0]


class TestThePlayhead:
    def test_it_never_walks_backwards_along_a_line(self):
        engraving = TabEngraving(_song(bars=12))
        previous = None
        for ms in range(0, 24_000, 100):
            spot = engraving.at_ms(ms)
            assert spot is not None
            if (previous is not None and spot[0] == previous[0]
                    and spot[2] == previous[2]):
                assert spot[1] >= previous[1] - 1e-9
            previous = spot

    def test_before_the_first_note_it_sits_on_it(self):
        engraving = TabEngraving(_song())
        assert engraving.at_ms(-5000.0) == engraving.at_ms(0.0)

    def test_after_the_last_note_it_stays_there(self):
        engraving = TabEngraving(_song())
        last = engraving.spots[-1]
        assert engraving.at_ms(1e9) == (last[1], last[2], last[3])

    def test_it_moves_between_two_notes_on_one_line(self):
        engraving = TabEngraving(_song(bars=2))
        first, second = engraving.spots[0], engraving.spots[1]
        if first[1] == second[1] and first[3] == second[3]:
            middle = engraving.at_ms((first[0] + second[0]) / 2)
            assert first[2] < middle[1] < second[2]

    def test_a_song_with_nothing_in_it_says_so(self):
        empty = Timeline([], SongMetadata(title="T", tempo=120),
                         measures=[MeasureInfo(index=0, start_ms=0.0,
                                               end_ms=2000.0)])
        assert TabEngraving(empty).at_ms(0.0) is None
