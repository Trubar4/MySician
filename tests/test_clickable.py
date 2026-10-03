"""Every shortcut on screen as a button, and nothing else as one.

The grammar is deliberately narrow, so these are mostly tests of what it
REFUSES: the help page is full of capital letters that are not keys, and a
parser that turned the dead-note badge into a button would make the help page
dangerous to read with a mouse in your hand.

The invariant that matters is the last class: every token this recognises in a
real footer or help line is a key the screen really answers.
"""

import inspect
import re

import pygame
import pytest

from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline
from pickhero.ui import clickable
from pickhero.ui.keys import ctrl_held, shift_held
from pickhero.ui.scrolling import PlayingScreen


def _timeline(notes=None) -> Timeline:
    meta = SongMetadata(title="Test", artist="Tester", tempo=120)
    return Timeline(notes or [NoteEvent(0.0, 500.0, 40, 6, 0)], meta)


def _words(text, lead=False):
    return [text[link.start:link.end] for link in clickable.spans(text, lead)]


def _keys(text, lead=False):
    return [(pygame.key.name(link.key), link.mod)
            for link in clickable.spans(text, lead)]


class TestWhatCounts:
    def test_a_named_key_needs_nothing_round_it(self):
        assert _words("SPACE plays it") == ["SPACE"]

    def test_a_letter_counts_when_a_colon_follows_it(self):
        assert _keys("G: 150 ms") == [("g", 0)]

    def test_a_slashed_pair_is_two_keys(self):
        assert _keys("PgDn/PgUp: Tempo 132 BPM") == [
            ("page down", 0), ("page up", 0)]

    def test_two_letters_joined_by_a_slash_are_both_keys(self):
        """`I/O` and `X/C` are written that way all over this app, and the
        slash is what says they are keys rather than prose."""
        assert _keys("then I/O marks a passage") == [("i", 0), ("o", 0)]

    def test_a_modifier_is_carried(self):
        assert _keys("Shift+P drills it") == [("p", pygame.KMOD_SHIFT)]
        assert _keys("Ctrl+Shift+M: no favourite") == [
            ("m", pygame.KMOD_SHIFT | pygame.KMOD_CTRL)]

    def test_a_modifier_written_once_belongs_to_the_pair(self):
        """`Ctrl+PgUp/PgDn` is two Ctrl presses. Reading the second as a
        plain PgDn would END the drill the first one steps."""
        assert _keys("Ctrl+PgUp/PgDn steps the speed") == [
            ("page up", pygame.KMOD_CTRL), ("page down", pygame.KMOD_CTRL)]

    def test_but_a_modifier_on_the_second_only_stays_there(self):
        assert _keys("R / Shift+R: another tuning") == [
            ("r", 0), ("r", pygame.KMOD_SHIFT)]

    def test_the_size_keys_are_their_own_literal(self):
        assert _keys("+/- Size (1.0x)") == [("+", 0), ("-", 0)]

    def test_and_the_nudge_keys(self):
        assert _keys(",/.: nudge that offset by 10 ms") == [(",", 0), (".", 0)]

    def test_a_function_key(self):
        assert _keys("F5: reload list") == [("f5", 0)]

    def test_a_footer_entry_may_lead_with_its_key(self):
        """"<letter> or a click: sort" is a key by POSITION, which is the
        convention every footer in this app follows."""
        assert _words("N or a click: sort (Recent)", lead=True) == ["N"]
        assert _words("N or a click: sort (Recent)") == []


class TestWhatIsNotAKey:
    """The help page says these, and none of them is a shortcut."""

    @pytest.mark.parametrize("line", [
        "X    DEAD NOTE. Damp the string with the fretting hand",
        "H    HAMMER-ON. Strike the first note, then hammer",
        "P    PULL-OFF. The same in reverse, lifting the finger",
        "PM   PALM MUTE starts here and runs on",
        "H, P and SL notes are not struck, so they score",
        "Row 6 (bottom) = low E  (thickest)",
        "Standard: the board scrolls right-to-left",
        "Hybrid: the sheet holds still and the playhead moves",
        "Green: credited because a strum was heard",
        "Ctrl+Shift: by ten seconds",
        "Loop set over bars 12-14",
        "N/M: MIDI backing earlier",          # MIDI is not four keys
        "Tempo 132 BPM (100%)",
        "A note's colour matches its row",
    ])
    def test_nothing_in_it_is_offered_as_a_button(self, line):
        extra = [w for w in _words(line)
                 if w not in ("N", "M", ",", "/")]
        assert extra == [], f"{line!r} offered {extra}"

    def test_a_badge_letter_leading_a_line_is_still_not_a_key(self):
        """`lead` is for footer ENTRIES only. Turned on for the help page it
        would make the dead-note badge delete the fret filter."""
        assert _words("X    DEAD NOTE. Damp the string") == []

    def test_a_hyphen_either_side_means_it_is_not_a_key(self):
        """`RIGHT-drag the strip` is the mouse button, not the key that seeks
        forward a beat -- and `F1-F6` is a range, not two buttons with four
        missing keys between them."""
        assert _words("RIGHT-drag the strip to mark a passage") == []
        assert _words("F1-F6: the six strings") == []

    def test_a_bare_modifier_name_is_not_a_key(self):
        assert _words("LEFT/RIGHT: a beat   Shift: a bar   Ctrl: 30 s") == [
            "LEFT", "RIGHT"]


class TestThePressItStandsFor:
    def test_a_plain_letter_does_not_read_as_shifted(self):
        """`shift_held` reads the CHARACTER as one of its three signals, so
        an unshifted letter must not arrive in capitals."""
        assert not shift_held(clickable.press(pygame.K_c))

    def test_a_shifted_letter_does(self):
        assert shift_held(clickable.press(pygame.K_c, pygame.KMOD_SHIFT))

    def test_a_ctrl_combination_reads_as_ctrl_and_not_as_shift(self):
        event = clickable.press(pygame.K_s, pygame.KMOD_CTRL)
        assert ctrl_held(event) and not shift_held(event)

    def test_it_is_a_real_keydown(self):
        event = clickable.press(pygame.K_SPACE)
        assert event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE

    def test_and_a_matching_release_exists(self):
        assert clickable.release(pygame.K_DELETE).type == pygame.KEYUP


class TestWhereTheWordsLanded:
    def _font(self):
        pygame.font.init()
        return pygame.font.SysFont("arial", 14)

    def test_a_link_is_recorded_where_the_word_was_drawn(self):
        pygame.init()
        surface = pygame.Surface((400, 40))
        links = clickable.Links()
        clickable.blit(surface, self._font(), "H: help", 10, 5,
                       (255, 255, 255), links)
        assert len(links) == 1
        assert links.at((12, 10)) == (pygame.K_h, 0)
        assert links.at((300, 10)) is None

    def test_the_word_is_underlined(self):
        """The underline is the whole affordance -- it is what says the word
        can be pressed, so a key word drawn without one is a key nobody
        knows is there."""
        pygame.init()
        plain = pygame.Surface((200, 30))
        linked = pygame.Surface((200, 30))
        font = self._font()
        clickable.blit(plain, font, "no keys at all", 2, 2, (255, 255, 255))
        clickable.blit(linked, font, "H: help", 2, 2, (255, 255, 255))
        def bottom_row(surface):
            h = font.get_height()
            return sum(1 for x in range(200)
                       if surface.get_at((x, 2 + h - 2))[:3] != (0, 0, 0))
        assert bottom_row(linked) > bottom_row(plain)

    def test_a_line_of_entries_splits_at_its_separators(self):
        pygame.init()
        links = clickable.Links()
        clickable.blit(pygame.Surface((900, 40)), self._font(),
                       "S sorts · T is the trend · ESC closes", 0, 0,
                       (255, 255, 255), links, sep="·")
        assert len(links) == 3

    def test_the_width_is_what_the_text_takes(self):
        """A caller that centres a line goes on doing so."""
        pygame.init()
        font = self._font()
        text = "Shift+P drills it"
        got = clickable.blit(pygame.Surface((400, 40)), font, text, 0, 0,
                             (255, 255, 255))
        assert abs(got - font.size(text)[0]) <= 3


class TestItIsWiredIntoTheScreens:
    """A measurement nothing calls is a feature that ships doing nothing --
    which this project has now done four times.

    The click comes in at the App, which is the one door every screen's
    events come through. It has to be: five of the song list's own keys
    (O, S, D, G, U) are answered THERE, so a screen dispatching its own
    clicks would have five shortcuts that work typed and not clicked.
    """

    def _app(self):
        from pickhero.ui.app import App
        pygame.init()
        pygame.display.set_mode((1280, 720))
        return App()

    def _playing(self):
        app = self._app()
        app._playing_screen = PlayingScreen(_timeline())
        app._state = "playing"
        app._playing_screen.render(pygame.display.get_surface())
        return app

    @staticmethod
    def _where(links, key, mod=0):
        return next((rect.center for rect, k, m in links._items
                     if k == key and m == mod), None)

    def test_clicking_the_help_key_in_the_footer_opens_the_help(self):
        app = self._playing()
        where = self._where(app._playing_screen.links, pygame.K_h)
        assert where is not None, "H is in the footer and was not clickable"
        assert not app._playing_screen._show_help
        app._press_link(*app._link_at(where))
        assert app._playing_screen._show_help

    def test_the_app_finds_the_link_under_the_pointer(self):
        app = self._playing()
        where = self._where(app._playing_screen.links, pygame.K_h)
        assert app._link_at(where) == (pygame.K_h, 0)
        assert app._link_at((0, 0)) is None

    def test_a_click_lets_go_of_the_key_again(self):
        """`Shift+S` and `DEL` both wait for a finger to come off before a
        second press may mean something else."""
        app = self._playing()
        app._press_link(pygame.K_s, pygame.KMOD_SHIFT)
        assert app._playing_screen._sync_key_held is False

    def test_the_links_are_rebuilt_every_frame(self):
        """A word that stopped being drawn stops being clickable."""
        app = self._playing()
        surface = pygame.display.get_surface()
        first = len(app._playing_screen.links)
        app._playing_screen.render(surface)
        assert len(app._playing_screen.links) == first

    def test_the_run_comparison_offers_its_own_keys_while_it_is_up(self):
        """It owns the whole screen, so its footer is what a click may hit."""
        app = self._playing()
        app._playing_screen._open_stats()
        app._playing_screen.render(pygame.display.get_surface())
        links = app._playing_screen.links
        assert links is app._playing_screen._stats.links
        assert len(links) > 0

    def test_the_song_list_footer_is_clickable(self):
        from pickhero.ui.menu import MenuScreen
        app = self._app()
        app._menu = MenuScreen(songs_dir="nowhere", config=app._config)
        app._state = "menu"
        app._menu.render(pygame.display.get_surface())
        where = self._where(app._menu.links, pygame.K_t)
        assert where is not None, "T: theme is in the footer"
        assert app._link_at(where) == (pygame.K_t, 0)

    def test_a_clicked_letter_does_not_land_in_the_search_box(self):
        """The screen never sees this click, so it cannot let go of its own
        box the way it does for any other click outside it."""
        from pickhero.ui.menu import MenuScreen
        app = self._app()
        app._menu = MenuScreen(songs_dir="nowhere", config=app._config)
        app._state = "menu"
        app._menu._search_active = True
        # N sorts the list. Deliberately not T, which cycles the THEME: a
        # test that leaves the palette switched breaks the colour tests two
        # files away, which is exactly what it did.
        app._press_link(pygame.K_n, 0)
        assert app._menu._search_text == ""
        assert app._menu._search_active is False

    def test_but_a_rename_in_progress_refuses(self):
        """The editor owns every key while it is open, and a click reaching
        past it would lose a half-typed name."""
        from pickhero.ui.menu import MenuScreen
        app = self._app()
        app._menu = MenuScreen(songs_dir="nowhere", config=app._config)
        app._state = "menu"
        app._menu._renaming = object()
        app._menu._rename_text = "Thunder"
        app._press_link(pygame.K_n, 0)
        assert app._menu._rename_text == "Thunder"


class TestEveryButtonIsAKeyTheScreenAnswers:
    """The whole safety argument, asserted rather than reasoned.

    The grammar reads text nobody wrote for it, so the thing to pin is not
    its wording but that it never invents a key: if it starts offering one
    the screen does not answer, this fails.
    """

    def _handled(self, *funcs) -> set[int]:
        out = set()
        for func in funcs:
            for name in re.findall(r"pygame\.K_(\w+)",
                                   inspect.getsource(func)):
                value = getattr(pygame, f"K_{name}", None)
                if value is not None:
                    out.add(value)
        return out

    def _offered(self, lines, lead):
        return {link.key
                for line in lines for link in clickable.spans(line, lead)}

    def _extra(self, offered, handled):
        return sorted(pygame.key.name(k) for k in offered - handled)

    def test_the_playing_footer_offers_only_keys_it_handles(self):
        pygame.init()
        pygame.display.set_mode((1280, 720))
        screen = PlayingScreen(_timeline())
        handled = self._handled(PlayingScreen.handle_event)
        offered = self._offered([t for t, _ in screen.footer_segments()], True)
        extra = self._extra(offered, handled)
        assert extra == [], f"the footer offers unbound keys: {extra}"

    def test_the_help_page_offers_only_keys_it_handles(self):
        """The run comparison's keys are in here too, and they are keys: this
        page documents three surfaces, and UP does nothing on the board
        whether it is clicked or typed."""
        from pickhero.ui.stats_view import StatsOverlay
        pygame.init()
        pygame.display.set_mode((1280, 720))
        screen = PlayingScreen(_timeline())
        handled = self._handled(PlayingScreen.handle_event,
                                StatsOverlay._handle_key)
        extra = self._extra(self._offered(screen.help_lines(), False), handled)
        assert extra == [], f"the help offers unbound keys: {extra}"

    def test_the_song_list_footer_offers_only_keys_it_handles(self):
        """Five of them are answered by the App rather than by the list, and
        that is exactly why the click comes in at the App."""
        from pickhero.ui.app import App
        from pickhero.ui.menu import MenuScreen
        menu = MenuScreen(songs_dir="nowhere")
        handled = self._handled(MenuScreen.handle_event,
                                App._handle_menu_event, App._dispatch)
        offered = self._offered(menu._hint_text().split("|"), True)
        extra = self._extra(offered, handled)
        assert extra == [], f"the song list offers unbound keys: {extra}"

    def test_the_run_comparison_footers_offer_only_keys_it_handles(self):
        """Read off what was really DRAWN, in all three modes.

        Not off the source: its footers are strings inside three drawing
        methods, and a regex over the module reads the comments too -- which
        is how the first version of this test convicted the word "," in a
        docstring. What a player can click is what landed in `links`.
        """
        from pickhero import runs as runs_mod
        from pickhero.ui.stats_view import StatsOverlay
        pygame.init()
        surface = pygame.display.set_mode((1280, 800))
        notes = [NoteEvent(float(n) * 500.0, 300.0, 40 + n % 6,
                           1 + n % 6, n % 5, n // 4) for n in range(24)]
        screen = PlayingScreen(_timeline(notes))
        history = [runs_mod.make("h" * 24, 60.0, 100, 0,
                                 started=f"2026-10-0{n + 1}T20:00:00")
                   for n in range(3)]
        screen._stats.open = True
        screen._stats._history = history
        screen._stats._rebuild()
        offered = set()
        for mode in ("list", "trend", "compare"):
            screen._stats.mode = mode
            screen._stats.selected = [0, 1]
            screen.render(surface)
            offered |= {key for _, key, _ in screen._stats.links._items}
        handled = self._handled(StatsOverlay._handle_key)
        extra = self._extra(offered, handled)
        assert extra == [], f"the stats footers offer unbound keys: {extra}"


class TestTheOtherScreensToo:
    """*"Das koennten wir generell bei jedem Tastenkuerzel ueberall machen."*

    The device list, the settings and the downloader each draw one centred
    line of shortcuts, in the same shape, which is why they share one helper
    rather than four copies of it.
    """

    def _screens(self):
        from pickhero.config import Config
        from pickhero.ui.device_menu import DeviceMenuScreen
        from pickhero.ui.download_menu import DownloadMenuScreen
        from pickhero.ui.settings_menu import SettingsMenuScreen
        return [("device", DeviceMenuScreen(Config()), DeviceMenuScreen),
                ("settings", SettingsMenuScreen(Config()), SettingsMenuScreen),
                ("download", DownloadMenuScreen("/tmp", config=Config()),
                 DownloadMenuScreen)]

    def test_each_one_offers_its_keys(self):
        pygame.init()
        surface = pygame.display.set_mode((1280, 720))
        for name, screen, _ in self._screens():
            screen.render(surface)
            assert len(screen.links) > 0, f"{name} has no clickable keys"

    def test_and_only_keys_it_answers(self):
        pygame.init()
        surface = pygame.display.set_mode((1280, 720))
        for name, screen, cls in self._screens():
            screen.render(surface)
            # The downloader's handler is a dispatcher over four states, so
            # the keys are in the four methods under it.
            # The downloader's handler is a dispatcher over four states, so
            # the keys are in the four methods under it -- and Ctrl+C is
            # answered by the App on every screen, which is where a click
            # comes in too.
            from pickhero.ui.app import App
            handled = TestEveryButtonIsAKeyTheScreenAnswers()._handled(
                App._dispatch,
                *[getattr(cls, n) for n in dir(cls)
                  if n.startswith("_handle") or n == "handle_event"])
            offered = {key for _, key, _ in screen.links._items}
            extra = sorted(pygame.key.name(k) for k in offered - handled)
            assert extra == [], f"{name} offers unbound keys: {extra}"
