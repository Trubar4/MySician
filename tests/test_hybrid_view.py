"""The hybrid view: the board's notes on a page that does not move.

The scrolling board cannot part two notes without speeding everything up --
x is time times a speed, so separation and speed are one number. `ui/sheet.py`
proves the arithmetic that dissolves that trade. These tests are about the
half that reaches the eye: which row is on top, that it slides there rather
than jumping, that a note keeps the colour it lit up in, and that none of it
costs a frame that grows with the song.
"""

import pygame
import pytest

from pickhero.config import Config
from pickhero.matcher import MatchType, NoteMatcher
from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)
from pickhero.ui import scrolling, sheet
from pickhero.ui.scrolling import PlayingScreen

BAR_MS = 2000.0


@pytest.fixture(autouse=True)
def _display():
    pygame.init()
    pygame.display.set_mode((1280, 800))
    yield
    pygame.display.quit()


def _song(bars=12, per_bar=4, strings=(6, 5, 4, 3)):
    notes, measures = [], []
    for bar in range(bars):
        measures.append(MeasureInfo(index=bar, start_ms=bar * BAR_MS,
                                    end_ms=(bar + 1) * BAR_MS))
        for beat in range(per_bar):
            notes.append(NoteEvent(
                timestamp_ms=bar * BAR_MS + beat * (BAR_MS / per_bar),
                duration_ms=BAR_MS / per_bar * 0.9, midi_note=40 + beat,
                string=strings[beat % len(strings)], fret=beat + 1,
                measure=bar))
    return Timeline(notes, SongMetadata(title="t", tempo=120),
                    measures=measures)


def _screen(song=None, config=None):
    screen = PlayingScreen(song or _song(), config=config or Config())
    screen._view = "hybrid"
    return screen


def _surface():
    """A surface the size of the player's window, off the display.

    A wrapper object cannot stand in for one here: pygame.draw takes a real
    Surface and this view draws lanes and bar lines as well as blitting, so
    what a test watches is the drawing FUNCTIONS, not the canvas.
    """
    return pygame.Surface((1280, 800))


def _settle(screen):
    """Wind the clock past the slide, so a test can ask where it ARRIVED."""
    screen._sheet_glide_at -= scrolling.TAB_GLIDE_S


class TestTheSheetIsBuiltOnceNotEveryFrame:
    """A layout walks every bar of the song. This display has had to move a
    loop like that out of the frame three times already -- 12.4 ms against a
    16.7 ms budget, the last time."""

    def test_a_second_frame_lays_nothing_out(self, monkeypatch):
        screen = _screen()
        built = []
        real = sheet.lay_out
        monkeypatch.setattr(
            scrolling.sheet, "lay_out",
            lambda *a, **k: (built.append(1), real(*a, **k))[1])
        surface = _surface()
        for ms in (0.0, 500.0, 1000.0, 1500.0):
            screen._playback_ms = ms
            screen.render(surface)
        assert len(built) == 1, f"laid out {len(built)} times"

    def test_but_a_size_change_does(self, monkeypatch):
        screen = _screen()
        surface = _surface()
        screen.render(surface)
        built = []
        real = sheet.lay_out
        monkeypatch.setattr(
            scrolling.sheet, "lay_out",
            lambda *a, **k: (built.append(1), real(*a, **k))[1])
        screen._size_sheet(+1)
        screen.render(surface)
        assert len(built) == 1

    def test_and_so_does_a_fret_filter(self, monkeypatch):
        screen = _screen()
        surface = _surface()
        screen.render(surface)
        built = []
        real = sheet.lay_out
        monkeypatch.setattr(
            scrolling.sheet, "lay_out",
            lambda *a, **k: (built.append(1), real(*a, **k))[1])
        screen._max_fret = 2
        screen.render(surface)
        assert len(built) == 1

    def test_a_filtered_note_takes_no_room_either(self):
        """A note that is not drawn must not be laid out for, or the song is
        spaced around notes nobody can see."""
        screen = _screen()
        screen._max_fret = 2
        rows = screen._sheet_layout(1200, 44.0)
        assert rows
        assert all(p.note.fret <= 2 for row in rows for p in row.notes)


class TestTheRowInTheHandIsTheTopOne:
    """The page view had to learn this the hard way: "hold while the row is
    anywhere on screen" showed rows in PAIRS, so half the song was played
    with no sight of what was coming."""

    def _scroll_at(self, screen, ms, surface):
        screen._playback_ms = ms
        screen.render(surface)
        _settle(screen)
        screen.render(surface)
        return screen._sheet_scroll

    def test_it_is_the_row_being_played_that_goes_on_top(self):
        screen = _screen()
        surface = _surface()
        screen.render(surface)
        rows = screen._sheet_rows
        head = screen._sheet_head_px(screen._tab_room(screen._layout(surface))[1])
        pitch = sheet.row_height(head) + sheet.ROW_GAP
        assert len(rows) >= 3, "this song would not show the fault at all"
        for row in rows[:3]:
            got = self._scroll_at(screen, row.start_ms + 10.0, surface)
            assert got == pytest.approx(row.index * pitch)

    def test_so_the_next_row_is_always_underneath(self):
        screen = _screen()
        surface = _surface()
        screen.render(surface)
        _, room = screen._tab_room(screen._layout(surface))
        head = screen._sheet_head_px(room)
        assert sheet.rows_that_fit(room, head) >= 2, \
            "two rows must fit at the size the view opens at"

    def test_it_holds_still_while_the_playhead_crosses_a_row(self):
        screen = _screen()
        surface = _surface()
        first = self._scroll_at(screen, 100.0, surface)
        assert self._scroll_at(screen, BAR_MS - 100.0, surface) == first


class TestItSlidesRatherThanJumps:
    """"Lieber wäre mir, wenn sich die Zeile nach oben schiebt und nicht
    springt." A step is the cheapest thing to draw and the hardest thing to
    follow."""

    def test_the_first_frame_of_a_row_change_is_not_the_last(self):
        screen = _screen()
        surface = _surface()
        screen.render(surface)
        _settle(screen)
        screen.render(surface)
        settled = screen._sheet_scroll
        screen._playback_ms = screen._sheet_rows[1].start_ms + 10.0
        screen.render(surface)
        moving = screen._sheet_scroll
        _settle(screen)
        screen.render(surface)
        assert settled <= moving < screen._sheet_scroll, "it jumped"

    def test_a_seek_across_the_song_arrives_at_once(self):
        """Half a song away would otherwise crawl while the music is already
        somewhere else."""
        screen = _screen(_song(bars=40))
        surface = _surface()
        screen.render(surface)
        _settle(screen)
        screen.render(surface)
        screen._playback_ms = screen._sheet_rows[-1].start_ms + 10.0
        screen.render(surface)
        head = screen._sheet_head_px(screen._tab_room(screen._layout(surface))[1])
        pitch = sheet.row_height(head) + sheet.ROW_GAP
        assert screen._sheet_scroll == pytest.approx(
            screen._sheet_rows[-1].index * pitch)


class TestANoteKeepsTheColourItLitUpIn:
    """"Die Note soll aufleuchten, wenn der Balken sie überquert. Sie kann
    dann die Farbe behalten. Damit kann ich sogar super zurückschauen, wo
    Fehler waren." On a page that holds still, the row behind the playhead is
    a record of the run -- which the scrolling board can never be, because it
    has already carried it off the screen."""

    def _colours(self, screen, surface):
        """Every head colour drawn, keyed by the note it belongs to."""
        seen = {}
        real = scrolling._head_surface
        import unittest.mock as mock
        with mock.patch.object(
                scrolling, "_head_surface",
                side_effect=lambda w, h, c, b: seen.setdefault(
                    (w, h, c, b), real(w, h, c, b))) as _:
            screen.render(surface)
        return [key[2] for key in seen]

    def _judge(self, screen, note, verdict, at=0.0):
        """Report a verdict the way the audio really does.

        Through the matcher AND the feedback renderer, not by poking a state:
        the colour on screen is the renderer's answer, so a test that sets
        only the matcher's state proves the drawing works on a path the app
        never takes.
        """
        from pickhero.matcher import MatchResult
        screen._matcher._set_state(note, verdict)
        screen._feedback.add_results(
            [MatchResult(match_type=verdict, matched_events=[note])], at)

    def test_a_judged_note_is_drawn_in_its_verdict_colour(self):
        song = _song()
        screen = _screen(song)
        screen._matcher = NoteMatcher(song)
        screen._audio_enabled = True
        self._judge(screen, song.notes[0], MatchType.HIT)
        screen._playback_ms = 100.0
        assert scrolling.get_theme().feedback_hit in self._colours(
            screen, _surface()), "the note never lit up"

    def test_and_it_is_still_that_colour_a_whole_row_later(self):
        """The point of a page that holds still: the row behind the playhead
        is a record of the run, and it can be looked back at."""
        song = _song()
        screen = _screen(song)
        screen._matcher = NoteMatcher(song)
        screen._audio_enabled = True
        self._judge(screen, song.notes[0], MatchType.MISS)
        screen._playback_ms = BAR_MS * 1.5      # long past the flash
        colours = self._colours(screen, _surface())
        from pickhero.ui.colors import dimmed
        miss = scrolling.get_theme().feedback_miss
        assert dimmed(miss, 0.6) in colours or miss in colours, \
            "the mistake was already forgotten"

    def test_a_note_not_yet_reached_is_still_its_own_string(self):
        song = _song()
        screen = _screen(song)
        screen._playback_ms = 0.0
        colours = self._colours(screen, _surface())
        from pickhero.ui.colors import STRING_COLORS
        assert any(c in colours for c in STRING_COLORS.values())


class TestBarNumbersAndLines:
    """"Ja Taktnummern und Striche anzeigen." A sheet without them is a sheet
    you cannot talk about -- "the run in bar 34" is how a passage gets found
    again, and how a loop gets set."""

    def test_the_first_bar_is_numbered_one_not_zero(self):
        screen = _screen()
        rows = screen._sheet_layout(1200, 44.0)
        assert rows[0].first_bar == 0, "measures are counted from zero inside"
        # Drawn as first_bar + step + 1 -- the number a player reads.
        assert rows[0].first_bar + 1 == 1

    def test_every_bar_of_a_row_gets_a_line(self):
        screen = _screen()
        rows = screen._sheet_layout(1200, 44.0)
        for row in rows:
            assert len(row.bar_lines) == row.last_bar - row.first_bar + 1

    def test_it_draws_without_raising_at_every_size(self):
        screen = _screen()
        surface = _surface()
        for zoom in range(len(sheet.ZOOM_STEPS)):
            screen._sheet_zoom = zoom
            screen._sheet_key = ()
            for ms in (0.0, 3000.0, 11_000.0):
                screen._playback_ms = ms
                screen.render(surface)


class TestPlusAndMinusTradeSizeForBars:
    """"Kann ich mit +/- die Größe ändern und dadurch die Anzahl Takte je
    Zeile einstellen?" -- yes, and it is one control rather than two because
    it is one number: the head sets both how big a note is drawn and how far
    apart two of them have to sit."""

    def _bars_in_first_row(self, screen):
        rows = screen._sheet_layout(1200, screen._sheet_head_px(600))
        return rows[0].last_bar - rows[0].first_bar + 1

    def test_bigger_notes_mean_fewer_bars_on_a_row(self):
        screen = _screen(_song(bars=40, per_bar=8))
        wide = self._bars_in_first_row(screen)
        screen._sheet_zoom = len(sheet.ZOOM_STEPS) - 1
        screen._sheet_key = ()
        assert self._bars_in_first_row(screen) < wide

    def test_and_smaller_ones_mean_more(self):
        screen = _screen(_song(bars=40, per_bar=8))
        screen._sheet_zoom = len(sheet.ZOOM_STEPS) - 1
        screen._sheet_key = ()
        tight = self._bars_in_first_row(screen)
        screen._sheet_zoom = 0
        screen._sheet_key = ()
        assert self._bars_in_first_row(screen) > tight

    def test_the_key_is_the_scroll_speed_only_on_the_board(self):
        """+/- means three things in three views, and pressing it in the
        hybrid must not silently re-time the scrolling one."""
        screen = _screen()
        before = screen._scroll_factor()
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_PLUS, mod=0))
        assert screen._scroll_factor() == before
        assert screen._sheet_zoom == sheet.ZOOM_DEFAULT + 1

    def test_the_end_of_the_range_is_named_not_silent(self):
        screen = _screen()
        screen._sheet_zoom = len(sheet.ZOOM_STEPS) - 1
        screen._size_sheet(+1)
        assert "as big as they go" in screen._status_note_text()


class TestNothingHereGrowsWithTheSong:
    """The same rule the page view is held to."""

    def test_only_the_notes_on_screen_are_asked_about(self, monkeypatch):
        song = _song(bars=120)
        screen = _screen(song)
        screen._matcher = NoteMatcher(song)
        screen._audio_enabled = True
        surface = _surface()
        screen.render(surface)
        asked = []
        real = screen._matcher.get_note_state
        monkeypatch.setattr(screen._matcher, "get_note_state",
                            lambda note: (asked.append(note), real(note))[1])
        screen._playback_ms = 100_000.0
        screen.render(surface)
        assert asked, "the verdicts are not being drawn at all"
        assert len(asked) < len(song.notes) / 4


class TestTheChordsComeAlong:
    """"Kriegen wir die Chordsview auch unter in der Hybrid View?" The grip
    cards never depended on the scrolling at all, so they are the board's own
    method called from here. Only the BLOCK had to be told where the notes
    ended up."""

    def _chord_song(self, bars=8):
        notes, measures = [], []
        for bar in range(bars):
            measures.append(MeasureInfo(index=bar, start_ms=bar * BAR_MS,
                                        end_ms=(bar + 1) * BAR_MS))
            for beat in range(2):
                when = bar * BAR_MS + beat * 1000.0
                # A real E minor, with the pitches the strings actually
                # sound: the name comes from the NOTES, and a shape whose
                # midi numbers are invented gets no name and no diagram.
                for string, fret, midi in ((6, 0, 40), (5, 2, 47), (4, 2, 52),
                                           (3, 0, 55), (2, 0, 59), (1, 0, 64)):
                    notes.append(NoteEvent(
                        timestamp_ms=when, duration_ms=900.0,
                        midi_note=midi, string=string, fret=fret,
                        measure=bar))
        return Timeline(notes, SongMetadata(title="t", tempo=120),
                        measures=measures)

    def test_the_grip_cards_are_drawn_in_the_hybrid_too(self, monkeypatch):
        import pickhero.ui.chord_view as chord_view
        screen = _screen(self._chord_song())
        screen._chord_mode = True
        drawn = []
        monkeypatch.setattr(chord_view, "draw_diagram",
                            lambda *a, **k: drawn.append(a))
        screen._playback_ms = 100.0
        screen.render(_surface())
        assert drawn, "no grip reached the screen"

    def test_and_the_blocks_land_on_the_notes(self):
        screen = _screen(self._chord_song())
        screen._chord_mode = True
        rows = screen._sheet_layout(1200, 44.0)
        row = rows[0]
        at = {}
        for placed in row.notes:
            at.setdefault(int(round(placed.note.timestamp_ms)), []).append(placed)
        assert any(len(g) >= 2 for g in at.values()), \
            "this song has no chord to block at all"

    def test_a_song_with_no_chords_draws_nothing_extra(self):
        screen = _screen()
        screen._chord_mode = True
        screen._playback_ms = 100.0
        screen.render(_surface())        # must not raise


class TestTheDefaultViewIsASetting:
    """"Es ist eine dritte Ansicht und in O Settings kann ich default
    einstellen." A view chosen by a keystroke nobody remembers pressing is
    the fret-filter trap again."""

    def test_a_song_opens_in_the_view_that_was_set(self):
        config = Config()
        config.default_view = "hybrid"
        assert PlayingScreen(_song(), config=config)._view == "hybrid"

    def test_a_nonsense_value_falls_back_rather_than_blanking(self):
        config = Config()
        config.default_view = "sideways"
        assert PlayingScreen(_song(), config=config)._view == "standard"

    def test_opening_on_the_page_still_asks_for_the_engraving(self):
        config = Config()
        config.default_view = "tab"
        assert PlayingScreen(_song(), config=config)._tab_due

    def test_the_settings_screen_walks_the_three(self):
        from pickhero.ui.settings_menu import SettingsMenuScreen
        config = Config()
        menu = SettingsMenuScreen(config)
        row = next(r for r in menu._rows if r.key == "view")
        seen = [config.default_view]
        for _ in scrolling.VIEWS:
            row.adjust(+1)
            seen.append(config.default_view)
        assert seen == ["standard", "hybrid", "tab", "standard"]
        assert "hold" in row.note or "sheet" in row.note
