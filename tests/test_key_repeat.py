"""One physical press is one action, unless the key is one a hand holds.

*"S -> songsterr, dann CTRL+V -> wenn ich nicht super schnell bin, wird der
Text im Speicher 2x eingefuegt. Haben wir das mit den Tastatur-Keys noch
immer nicht durchgaengig abgefangen?"*

No, and he is right that it is the shape of the list rather than the list.
`pygame.key.set_repeat(300, 40)` is one global setting for every key in the
app, and the guard against it named the keys caught misbehaving -- escape,
space, DELETE. Each of those was a REPORT, and Ctrl+V is the fourth.

So the list says which keys MAY repeat now. The two failure modes are not
equal: a key missing from an opt-out list destroys a song or duplicates a
paste, and a key missing from this one has to be pressed twice instead of
held. The list that can be wrong is the harmless one.
"""

from __future__ import annotations

import pygame

from pickhero.config import Config
from pickhero.ui import app as app_module
from pickhero.ui import clipboard

LINK = "https://www.songsterr.com/a/wsa/x-tab-s400231"


def _app(tmp_path, state="menu"):
    from pickhero.ui.app import App
    from pickhero.ui.download_menu import DownloadMenuScreen
    from pickhero.ui.menu import MenuScreen
    config = Config()
    config.songs_dir = str(tmp_path)
    config.save = lambda *a, **k: None
    app = App(config)
    app._menu = MenuScreen(tmp_path, config=config)
    app._download_menu = DownloadMenuScreen(tmp_path, config=config)
    app._state = state
    app._running = True
    return app


def _feed(app, events):
    """Through the REAL door, because that is where the guard is."""
    surface = pygame.Surface((10, 10))
    original = pygame.event.get
    pygame.event.get = lambda *a, **k: list(events)
    try:
        app._process_events(surface)
    finally:
        pygame.event.get = original


def _down(key, mod=0, unicode=""):
    return pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod,
                              unicode=unicode)


def _up(key, mod=0):
    return pygame.event.Event(pygame.KEYUP, key=key, mod=mod)


def _held(key, times, mod=0, unicode=""):
    """One press and its repeats: set_repeat(300, 40) gives about one every
    40 ms after the first 300, so 400 ms of holding is three events."""
    return [_down(key, mod, unicode) for _ in range(times)]


class TestAHeldCtrlVPastesOnce:
    def test_one_held_press_pastes_the_link_once(self, tmp_path, monkeypatch):
        """On the unfixed code this put the URL in three times."""
        monkeypatch.setattr(clipboard, "clipboard_text", lambda: LINK)
        app = _app(tmp_path, state="download")
        _feed(app, _held(pygame.K_v, 3, pygame.KMOD_LCTRL, "\x16"))
        assert app._download_menu._query.count("songsterr.com") == 1

    def test_two_deliberate_presses_paste_twice(self, tmp_path, monkeypatch):
        """The guard is a guard, not a removal: letting go re-arms it."""
        monkeypatch.setattr(clipboard, "clipboard_text", lambda: LINK)
        app = _app(tmp_path, state="download")
        _feed(app, [_down(pygame.K_v, pygame.KMOD_LCTRL, "\x16"),
                    _up(pygame.K_v, pygame.KMOD_LCTRL),
                    _down(pygame.K_v, pygame.KMOD_LCTRL, "\x16")])
        assert app._download_menu._query.count("songsterr.com") == 2


class TestWhatMayStillRepeat:
    """What a hand really holds down. These are the control: inverting the
    rule must not take the repeats that were always wanted."""

    def test_the_arrows(self):
        for key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP,
                    pygame.K_DOWN):
            assert key in app_module.MAY_REPEAT

    def test_backspace_eats_more_than_one_character(self):
        assert pygame.K_BACKSPACE in app_module.MAY_REPEAT

    def test_the_three_stepping_pairs(self):
        """Gated a second time at 150 ms by `_step_key_ready`, which is
        where the practice speed's own fix lives."""
        for key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN, pygame.K_PLUS,
                    pygame.K_MINUS, pygame.K_EQUALS, pygame.K_KP_PLUS,
                    pygame.K_KP_MINUS):
            assert key in app_module.MAY_REPEAT

    def test_a_held_arrow_really_does_arrive_more_than_once(self, tmp_path):
        """Asserted through the real door rather than off the set, because
        a guard that dropped everything would pass the lines above."""
        app = _app(tmp_path, state="menu")
        seen = []
        app._menu.handle_event = lambda e: seen.append(e)
        _feed(app, _held(pygame.K_DOWN, 4))
        assert len(seen) == 4


class TestEveryOtherKindOfKeyFiresOnce:
    """The four reports this list was grown by, and the classes they are."""

    def _reaching(self, tmp_path, events):
        # Counted at `_dispatch`, not at the menu. Five of the song list's
        # keys -- O, S, D, G, U -- are answered by the App itself and never
        # reach the screen, so a count taken there reads 0 for them and the
        # test would pass on anything.
        app = _app(tmp_path, state="menu")
        seen = []
        app._dispatch = lambda e: seen.append(e)
        _feed(app, events)
        return [e for e in seen if e.type == pygame.KEYDOWN]

    def test_a_toggle(self, tmp_path):
        """Escape and space: a repeat UNDOES the press."""
        assert len(self._reaching(tmp_path, _held(pygame.K_SPACE, 5))) == 1

    def test_a_destructive_key(self, tmp_path):
        """DELETE: a repeat CONFIRMS it, and a folder of songs went."""
        assert len(self._reaching(tmp_path, _held(pygame.K_DELETE, 5))) == 1

    def test_a_ladder(self, tmp_path):
        """Shift+T, Shift+C, Alt+S, N: a repeat walks past the answer."""
        shift = pygame.KMOD_LSHIFT
        held = _held(pygame.K_t, 5, shift, "T")
        assert len(self._reaching(tmp_path, held)) == 1

    def test_a_plain_letter(self, tmp_path):
        """Nobody holds U to switch the recording on four times."""
        assert len(self._reaching(tmp_path, _held(pygame.K_u, 5))) == 1

    def test_and_the_count_is_taken_where_every_key_passes(self, tmp_path):
        """The control for the four above: a key that DOES repeat is seen
        five times at the same place, so the counter is not simply blind."""
        assert len(self._reaching(tmp_path, _held(pygame.K_DOWN, 5))) == 5
