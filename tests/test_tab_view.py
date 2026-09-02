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


def _engraved(screen):
    """Drive the threaded build to the point the main loop has taken it.

    The engraving runs on a thread, so a single update() only STARTS it --
    the page reaches the screen on a later frame. Everything a test wants to
    look at is on the far side of that.
    """
    screen.update()
    if screen._tab_thread is not None:
        screen._tab_thread.join(60)
    screen.update()
    return screen


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


class TestTheViewInsideThePlayingScreen:
    """One screen, one clock, one set of keys -- the page is a way of
    DRAWING the song, not a second app with its own transport."""

    def _screen(self):
        from pickhero.ui.scrolling import PlayingScreen
        from pickhero.config import Config
        return PlayingScreen(_song(bars=6), config=Config())

    def test_shift_t_switches_and_plain_t_still_themes(self):
        screen = self._screen()
        theme_before = screen._config.theme
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_t, mod=pygame.KMOD_SHIFT))
        assert screen._tab_mode
        assert screen._config.theme == theme_before
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_t, mod=0))
        assert screen._tab_mode          # theme keys must not switch views

    def test_the_engraving_waits_a_frame_and_then_runs_off_the_loop(self):
        """Seconds of work with nothing on screen is a frozen app, and this
        project has already shipped that twice."""
        screen = self._screen()
        screen._toggle_tab_mode()
        assert screen._tab_due and screen._tab_engraving is None
        assert "Engraving" in screen._status_note_text()
        screen.update()
        assert screen._tab_thread is not None, "the build must not block a frame"
        _engraved(screen)
        assert screen._tab_engraving is not None

    def test_it_is_built_while_the_song_is_paused(self):
        """Which is exactly when the page view gets opened."""
        screen = self._screen()
        screen._playing = False
        screen._toggle_tab_mode()
        _engraved(screen)
        assert screen._tab_engraving is not None

    def test_zooming_rebuilds_and_says_so(self):
        screen = self._screen()
        screen._toggle_tab_mode()
        _engraved(screen)
        screen._zoom_tab(+1)
        assert screen._tab_engraving is None and screen._tab_due
        assert "Zoom 4" in screen._status_note_text()

    def test_the_end_of_the_zoom_range_is_named(self):
        screen = self._screen()
        screen._tab_zoom = len(ZOOM_STEPS) - 1
        screen._zoom_tab(+1)
        assert "closest" in screen._status_note_text()
        assert not screen._tab_due

    def test_plus_zooms_on_the_page_and_speeds_the_board(self):
        screen = self._screen()
        before = screen._scroll_factor()
        screen._tab_mode = True
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_PLUS, mod=0))
        assert screen._scroll_factor() == before

    def test_a_missing_engraver_is_named_not_swallowed(self, monkeypatch):
        import pickhero.ui.tab_view as module
        screen = self._screen()

        def explode(*_a, **_k):
            raise RuntimeError("data files are missing")

        monkeypatch.setattr(module, "TabEngraving", explode)
        screen._toggle_tab_mode()
        _engraved(screen)
        assert screen._tab_engraving is None
        assert "data files are missing" in screen._status_note_text()

    def test_it_draws_without_raising(self):
        screen = self._screen()
        surface = pygame.display.set_mode((1280, 720))
        screen._toggle_tab_mode()
        _engraved(screen)
        for ms in (0.0, 3000.0, 11_000.0):
            screen._playback_ms = ms
            screen.render(surface)


class TestNothingHereGrowsWithTheSong:
    """A page holds most of a song, and the drawing must only ever touch the
    notes that are on screen. Measured before the slice: 12.4 ms a frame on
    a real song against a 16.7 ms budget, where the scrolling view costs 3.2.
    """

    def test_only_the_visible_notes_are_asked_about(self, monkeypatch):
        from pickhero.ui.scrolling import PlayingScreen
        from pickhero.config import Config
        from pickhero.matcher import NoteMatcher

        song = _song(bars=60)
        screen = PlayingScreen(song, config=Config())
        screen._matcher = NoteMatcher(song)
        surface = pygame.display.set_mode((1280, 720))
        screen.render(surface)
        screen._toggle_tab_mode()
        _engraved(screen)

        asked = []
        real = screen._matcher.get_note_state
        monkeypatch.setattr(screen._matcher, "get_note_state",
                            lambda note: (asked.append(note), real(note))[1])
        screen._playback_ms = 40_000.0
        screen.render(surface)
        assert asked, "the verdicts are not being drawn at all"
        assert len(asked) < len(song.notes) / 2

    def test_a_page_is_scaled_once_not_every_frame(self):
        from pickhero.ui.tab_view import TabEngraving, fit
        pygame.display.set_mode((1280, 720))
        engraving = TabEngraving(_song(bars=8))
        page = engraving.pages[0]
        first = fit(page, 800)
        assert fit(page, 800) is first

    def test_the_page_is_converted_for_the_display(self):
        """Unconverted, blitting the band on screen costs 8.36 ms against
        0.26 -- half a frame budget spent on a pixel format."""
        from pickhero.ui.tab_view import TabEngraving, fit
        screen = pygame.display.set_mode((1280, 720))
        engraving = TabEngraving(_song(bars=8))
        fitted = fit(engraving.pages[0], 1280)
        assert fitted.get_bitsize() == screen.get_bitsize()


class TestTheEngraverOnAThread:
    """verovio's default resource path is thread-local. Without setting it on
    the thread that builds the pages, the toolkit constructs, the score loads,
    and the SVG comes back empty -- 212 characters against 21712. Nothing
    raises, so the only tell is a blank page, which is exactly the fault this
    project keeps shipping.
    """

    def test_a_page_engraved_on_a_thread_has_notes_on_it(self):
        import threading
        from pickhero.ui.tab_view import engrave

        out = {}

        def work():
            try:
                out["pages"] = engrave(_song(bars=4), raster_width=640)
            except Exception as exc:                     # pragma: no cover
                out["error"] = exc

        thread = threading.Thread(target=work)
        thread.start()
        thread.join(60)
        assert "error" not in out, out.get("error")
        pages = out["pages"]
        assert pages and pages[0].spots, "engraved on a thread and came back empty"


class TestThePageIsNotCoveredByTheScore:
    """The completion overlay draws unconditionally -- every caller decides.
    Calling it without the check put the score over the page every frame,
    and the page view looked like it only ever showed the end of the song.
    """

    def _screen(self):
        from pickhero.ui.scrolling import PlayingScreen
        from pickhero.config import Config
        return PlayingScreen(_song(bars=6), config=Config())

    def test_an_unfinished_song_shows_the_page(self, monkeypatch):
        screen = self._screen()
        surface = pygame.display.set_mode((1280, 720))
        screen.render(surface)
        screen._toggle_tab_mode()
        _engraved(screen)
        drawn = []
        monkeypatch.setattr(type(screen), "_draw_completion_overlay",
                            lambda self, *a: drawn.append(1))
        screen._song_completed = False
        screen.render(surface)
        assert drawn == []

    def test_a_finished_song_still_gets_its_score(self, monkeypatch):
        screen = self._screen()
        surface = pygame.display.set_mode((1280, 720))
        screen.render(surface)
        screen._toggle_tab_mode()
        _engraved(screen)
        drawn = []
        monkeypatch.setattr(type(screen), "_draw_completion_overlay",
                            lambda self, *a: drawn.append(1))
        screen._song_completed = True
        screen.render(surface)
        assert drawn == [1]
