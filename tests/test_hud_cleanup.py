"""What stays on the screen while you are trying to read music off it.

A line earns its place by saying something that CHANGES and that nothing
else on screen says. Measured against the old HUD on the player's own
screenshot: eight lines down the left, a caption across the middle and two
rows of twenty-three shortcuts along the bottom, of which he read four.
"""

import pygame
import pytest

from pickhero.audio.note_utils import NAMED_TUNINGS
from pickhero.config import Config
from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)
from pickhero.ui import scrolling, sheet
from pickhero.ui.scrolling import PlayingScreen


@pytest.fixture(autouse=True)
def _display():
    pygame.init()
    pygame.display.set_mode((1280, 800))
    yield
    pygame.display.quit()


def _named(name):
    return dict(next(shape for label, shape in NAMED_TUNINGS if label == name))


def _song(tuning=None, bars=8):
    notes, measures = [], []
    for bar in range(bars):
        measures.append(MeasureInfo(index=bar, start_ms=bar * 2000.0,
                                    end_ms=(bar + 1) * 2000.0))
        for beat in range(4):
            notes.append(NoteEvent(timestamp_ms=bar * 2000.0 + beat * 500.0,
                                   duration_ms=400.0, midi_note=40 + beat,
                                   string=6 - beat, fret=beat, measure=bar))
    meta = SongMetadata(title="t", tempo=120)
    meta.tuning = tuning if tuning is not None else _named("Standard")
    return Timeline(notes, meta, measures=measures)


def _screen(tuning=None, transpose=0):
    screen = PlayingScreen(_song(tuning), config=Config())
    screen._transpose = transpose
    return screen


class TestTheTuningsAreOneLine:
    """"Alle Varianten nebeneinander. Nie mehr als 5. Aktive Variante in
    Blau. Original-Variante mit *."

    A tab in Drop C played on a standard-tuned guitar is wrong on every
    single note and nothing else on screen says so -- the notes scroll by
    looking ordinary while every one of them scores red.
    """

    def _played(self, screen):
        return [n for n, role in screen.tuning_segments()
                if role in ("played", "both")]

    def _written(self, screen):
        return [n for n, role in screen.tuning_segments()
                if role in ("written", "both")]

    def test_never_more_than_five(self):
        for transpose in range(0, -6, -1):
            screen = _screen(_named("C Standard"), transpose)
            assert len(screen.tuning_segments()) <= scrolling.TUNINGS_SHOWN

    def test_the_one_being_played_is_always_in_it(self):
        """It is marked in blue, so leaving it out is a blue mark on
        nothing."""
        for transpose in range(0, -6, -1):
            screen = _screen(_named("C Standard"), transpose)
            assert len(self._played(screen)) == 1, f"at {transpose}"

    def test_and_so_is_the_one_it_was_written_in(self):
        for transpose in range(0, -6, -1):
            screen = _screen(_named("C Standard"), transpose)
            assert len(self._written(screen)) == 1, f"at {transpose}"

    def test_at_most_one_step_below_them(self):
        """Further down is a floppy string and a fret that buzzes, not a
        choice anybody would make."""
        screen = _screen(_named("C Standard"), -4)
        order, here = screen._tuning_order()
        shown = [n for n, _ in screen.tuning_segments()]
        written_at = next(i for i, (_, k) in enumerate(order) if k == 0)
        below = min(here, written_at) - (len(order) - len(shown))
        assert below <= scrolling.TUNINGS_BELOW + 1

    def test_the_top_of_the_list_is_the_standard_shaped_one(self):
        screen = _screen(_named("C Standard"), -4)
        assert screen.tuning_segments()[-1][0] == "EADGBE"

    def test_the_notes_are_the_notes_of_that_tuning(self):
        screen = _screen(_named("C Standard"), -4)
        assert ("CFA#D#GC", "played") in screen.tuning_segments()

    def test_a_song_at_its_written_tuning_marks_one_entry_twice(self):
        screen = _screen(_named("Standard"), 0)
        roles = dict(screen.tuning_segments())
        assert roles["EADGBE"] == "both"

    def test_a_song_with_nothing_to_step_to_says_nothing(self):
        screen = PlayingScreen(_song(tuning={}), config=Config())
        assert screen.tuning_segments() == []


class TestTheSyncPanelIsBehindS:
    """"Links oben nichts mehr, aber unten ein Block zum Ein- und
    Ausblenden." None of it is needed while playing and all of it is needed
    while syncing, which is what a key is for."""

    def test_it_starts_shut(self):
        assert _screen()._show_sync is False

    def test_s_opens_it_and_s_shuts_it(self):
        screen = _screen()
        for expected in (True, False, True):
            screen.handle_event(pygame.event.Event(
                pygame.KEYDOWN, key=pygame.K_s, mod=0))
            assert screen._show_sync is expected

    def test_shift_s_still_pins_a_sync_point(self):
        """The panel key must not have eaten the one it advertises."""
        screen = _screen()
        called = []
        screen._set_sync_point = lambda: called.append(1)
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_s, mod=pygame.KMOD_LSHIFT))
        assert called and screen._show_sync is False

    def test_ctrl_s_still_starts_the_automatic_pass(self):
        screen = _screen()
        called = []
        screen._start_auto_sync = lambda: called.append(1)
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_s, mod=pygame.KMOD_LCTRL))
        assert called and screen._show_sync is False

    def test_everything_the_player_listed_is_in_it(self):
        screen = _screen()
        screen._config.backing_offset_ms = -370.0
        screen._sync_lines = ["SYNC   0:10 -257 ms"]
        text = "  ".join(t for t, _ in screen.sync_block_lines())
        assert "Backing: -370 ms (N/M)" in text
        assert "SYNC   0:10 -257 ms" in text
        assert "Sync:" in text          # the strike-timing offset (K)

    def test_and_none_of_it_is_on_the_screen_while_it_is_shut(self):
        screen = _screen()
        screen._sync_lines = ["SYNC   0:10 -257 ms"]
        assert "SYNC   0:10 -257 ms" not in self._drawn(screen)

    def test_but_all_of_it_is_when_it_is_open(self):
        screen = _screen()
        screen._sync_lines = ["SYNC   0:10 -257 ms"]
        screen._show_sync = True
        assert "SYNC   0:10 -257 ms" in self._drawn(screen)

    def _drawn(self, screen) -> str:
        seen = []
        real = scrolling._get_font

        class Watched:
            def __init__(self, font):
                self._font = font

            def render(self, text, *a, **k):
                seen.append(text)
                return self._font.render(text, *a, **k)

            def __getattr__(self, name):
                return getattr(self._font, name)

        scrolling._get_font = lambda *a, **k: Watched(real(*a, **k))
        try:
            screen.render(pygame.Surface((1280, 800)))
        finally:
            scrolling._get_font = real
        return "  ".join(seen)

    def test_the_music_does_not_reflow_when_it_opens(self):
        """Counting the panel's lines as room taken made the sheet re-break
        its lines the moment S was pressed: a smaller head, more bars on a
        row, and a different page while you were looking at it."""
        screen = _screen()
        screen._view = "hybrid"
        surface = pygame.Surface((1280, 800))
        screen.render(surface)
        before = screen._sheet_key
        screen._show_sync = True
        screen.render(surface)
        assert screen._sheet_key == before

    def test_a_warning_inside_it_reaches_the_outside(self):
        """The one line built last session says the recording is guessed
        where you are standing. Shut in a panel it would be invisible at
        exactly the moment it is the only thing that explains the picture."""
        screen = _screen()
        screen._beyond_sync_line = lambda: "SYNC  before the measured part"
        colour = dict(screen.footer_segments())["S: Sync"]
        assert colour == "feedback_close"


class TestWhatIsGoneFromTheLeftColumn:
    """"Hit Windows und Scroll ausblenden. VSYNC-Zeile ausblenden." Each of
    them is a number that is set once and then never looked at again, on a
    screen whose whole job is to be read while both hands are busy."""

    def _drawn(self, screen) -> str:
        return TestTheSyncPanelIsBehindS()._drawn(screen)

    def test_the_hit_window_the_scroll_line_and_the_pacing_are_gone(self):
        screen = _screen()
        text = self._drawn(screen)
        for gone in ("Hit window:", "Scroll:", "Pace:", "vsync:"):
            assert gone not in text, f"{gone} is still on screen"

    def test_and_so_is_the_caption_over_the_music(self):
        screen = _screen()
        screen._view = "hybrid"
        text = self._drawn(screen)
        assert "bars a row" not in text
        assert "BPM" not in text.split("PgDn")[0], "the tempo is still centred"

    def test_the_dropout_warning_stays(self):
        """"Nur Audio dropouts." A machine losing buffers loses notes at
        random, which looks exactly like bad detection or bad playing."""
        screen = _screen()
        screen._audio_enabled = True
        screen._audio_capture = type("C", (), {"dropped_buffers": 12})()
        assert any("Audio dropouts: 12" in t
                   for t, _ in screen._left_notes())

    def test_the_fret_filter_still_says_it_is_on(self):
        """The invisible setting that cost this project a session."""
        screen = _screen()
        screen._max_fret = 5
        assert screen._left_notes(), "a fret limit is silent again"


class TestTheTopOfTheScreenIsMeasured:
    """The music used to start at a fixed 150 px, fitted to a HUD with eight
    lines down the left. With three it left 90 px of empty window above the
    staff and took it off the bottom row."""

    def test_a_shorter_column_gives_the_music_more_room(self):
        screen = _screen()
        tall = screen._hud_top_used()
        screen._max_fret = 5                      # one more line
        assert screen._hud_top_used() > tall

    def test_the_music_starts_below_the_text_not_on_it(self):
        screen = _screen()
        surface = pygame.Surface((1280, 800))
        layout = screen._layout(surface)
        top, _ = screen._tab_room(layout)
        assert top > screen._hud_top_used()


class TestTheHelpSaysWhatEverythingIsSetTo:
    """"Kannst du unter Help auch den aktuellen Status aller Werte
    anzeigen?" -- and it is the difference between a page you read once and
    a page worth opening mid-song. "G: hit window" is a key; "±150 ms" is
    the answer to the question you opened the page with.
    """

    def _text(self, screen) -> str:
        return "  ".join(screen.help_lines())

    def test_the_settings_carry_their_values(self):
        screen = _screen()
        screen._config.timing_window_ms = 120.0
        screen._max_fret = 9
        screen._config.theme = "light"
        text = self._text(screen)
        assert "±120 ms" in text
        assert "up to fret 9" in text
        assert "light" in text

    def test_a_value_follows_the_thing_it_names(self):
        screen = _screen()
        screen._audio_enabled = False
        assert "A: audio on/off   off" in self._text(screen)
        screen._audio_enabled = True
        assert "A: audio on/off   on" in self._text(screen)

    def test_the_view_and_the_tempo_are_in_it(self):
        screen = _screen()
        screen._view = "hybrid"
        screen._tempo_factor = 0.8
        text = self._text(screen)
        assert "Hybrid" in text
        assert "120 BPM (80 %)" in text

    def test_a_missing_backing_says_so_rather_than_off(self):
        """A dash is the answer to "why does pressing it do nothing"; "off"
        would send the player looking for the key that turns it on."""
        screen = _screen()
        assert screen._midi_player is None
        assert "B: MIDI backing   —" in self._text(screen)

    def test_the_muted_strings_are_shown_as_strings(self):
        screen = _screen()
        screen._active_strings[0] = False        # the high e
        assert "EADGB·" in self._text(screen)

    def test_and_the_keys_with_no_value_are_still_plain_lines(self):
        screen = _screen()
        assert "L: loop the weakest part" in screen.help_lines()

    def test_it_draws_without_raising(self):
        screen = _screen()
        screen._show_help = True
        screen.render(pygame.Surface((1280, 800)))


class TestTheRecordedBackingIsInTheFooter:
    """"Es fehlt U: MP3 On/Off." It is a sound you can hear or not, the same
    kind of switch as B and Shift+B beside it -- and this player has already
    reported U looking removed once, when its line went quiet."""

    def test_it_is_there(self):
        assert any(text.startswith("U: MP3")
                   for text, _ in _screen().footer_segments())

    def test_a_song_with_no_recording_shows_a_dash(self):
        screen = _screen()
        assert screen._mp3_player is None
        assert ("U: MP3 —", "hud_text") in screen.footer_segments()

    def test_and_it_lights_up_when_the_recording_is_playing(self):
        screen = _screen()
        screen._mp3_player = object()
        screen._mp3_muted = False
        assert ("U: MP3 on", "hud_accent") in screen.footer_segments()
        screen._mp3_muted = True
        assert ("U: MP3 off", "hud_text") in screen.footer_segments()


class TestTheChordsOnTheSheet:
    """"Die Chords überlappen oben das Griffbrett" and "Chords um 10 %
    kleiner". On the scrolling board the grip cards sit above a lane band
    that starts halfway down the window; the sheet reaches into that corner,
    so the first thing on screen was a diagram over the top string."""

    # A real E minor and a real A minor, with the pitches the strings
    # actually sound: the name comes from the NOTES, and a shape whose midi
    # numbers are invented gets no name and no diagram.
    EM = ((6, 0, 40), (5, 2, 47), (4, 2, 52), (3, 0, 55), (2, 0, 59), (1, 0, 64))
    AM = ((5, 0, 45), (4, 2, 52), (3, 2, 57), (2, 1, 60), (1, 0, 64))

    def _chord_screen(self, per_bar=2, alternating=False):
        notes, measures = [], []
        for bar in range(8):
            measures.append(MeasureInfo(index=bar, start_ms=bar * 2000.0,
                                        end_ms=(bar + 1) * 2000.0))
            for beat in range(per_bar):
                when = bar * 2000.0 + beat * (2000.0 / per_bar)
                shape = self.AM if alternating and beat % 2 else self.EM
                for string, fret, midi in shape:
                    notes.append(NoteEvent(
                        timestamp_ms=when, duration_ms=900.0, midi_note=midi,
                        string=string, fret=fret, measure=bar))
        meta = SongMetadata(title="t", tempo=120)
        meta.tuning = _named("Standard")
        config = Config()
        config.chord_view = True
        screen = PlayingScreen(Timeline(notes, meta, measures=measures),
                               config=config)
        screen._view = "hybrid"
        return screen

    def test_the_music_starts_below_the_grip_cards(self):
        from pickhero.ui.chord_view import card_size
        from pickhero.ui.scrolling import CHORD_CARD_SCALE
        screen = self._chord_screen()
        screen.render(pygame.Surface((1280, 800)))
        assert screen._chord_shapes, "this song has no grips at all"
        top, _ = screen._tab_room(screen._layout(pygame.Surface((1280, 800))))
        assert top > card_size(CHORD_CARD_SCALE)[1]

    def test_and_goes_back_up_when_the_chords_are_off(self):
        screen = self._chord_screen()
        surface = pygame.Surface((1280, 800))
        screen.render(surface)
        with_cards = screen._hud_top_used()
        screen._chord_mode = False
        assert screen._hud_top_used() < with_cards

    def test_the_row_makes_room_for_the_names(self):
        """A name sized to be read at a glance does not fit in the strip a
        bar number needs, and drawn there anyway it sat on the top string."""
        screen = self._chord_screen()
        screen.render(pygame.Surface((1280, 800)))
        assert screen._sheet_strip() == sheet.CHORD_STRIP
        screen._chord_mode = False
        assert screen._sheet_strip() == sheet.NUMBER_STRIP

    def test_and_the_head_is_sized_for_that_strip(self):
        """Otherwise the taller strip is simply taken off the bottom row."""
        screen = self._chord_screen()
        screen.render(pygame.Surface((1280, 800)))   # builds the name list
        with_names = screen._sheet_head_px(700)
        screen._chord_mode = False
        assert screen._sheet_head_px(700) > with_names

    def test_two_rows_still_fit_with_the_names_on(self):
        screen = self._chord_screen()
        screen.render(pygame.Surface((1280, 800)))
        head = screen._sheet_head_px(700)
        assert sheet.rows_that_fit(700, head, screen._sheet_strip()) >= 2

    def _names(self, screen, row, width=90):
        """The names one row would draw, at a fixed label width."""
        return screen.sheet_chord_names(row, 0, lambda text: width)

    def test_a_name_is_never_drawn_over_the_one_before_it(self):
        """Measured on the player's screenshot: three changes inside one bar
        came out as "DadA/EF#"."""
        screen = self._chord_screen(alternating=True, per_bar=8)
        screen.render(pygame.Surface((1280, 800)))
        row = screen._sheet_rows[0]
        changes = sum(1 for when, _ in screen._chord_names
                      if row.start_ms <= when < row.end_ms)
        assert changes >= 8, "this song would not show the fault at all"
        spots = self._names(screen, row)
        assert spots, "no chord was named at all"
        for (x, _), (nx, _) in zip(spots, spots[1:]):
            assert nx - x >= 90 + scrolling.SHEET_NAME_GAP
        assert len(spots) < changes

    def test_a_narrower_name_lets_more_of_them_through(self):
        """The rule is the width of the text, not a fixed count."""
        screen = self._chord_screen(alternating=True, per_bar=8)
        screen.render(pygame.Surface((1280, 800)))
        row = screen._sheet_rows[0]
        assert len(self._names(screen, row, 20)) >= \
            len(self._names(screen, row, 200))

    def test_but_every_row_names_the_chord_it_is_in(self):
        """A row whose chord started on the row above stays in front of you
        for four seconds saying nothing."""
        screen = self._chord_screen()
        screen.render(pygame.Surface((1280, 800)))
        later = next(r for r in screen._sheet_rows if r.index == 1)
        assert self._names(screen, later), \
            "the row in the hand names no chord at all"
        assert self._names(screen, later)[0][1] == "Em"
