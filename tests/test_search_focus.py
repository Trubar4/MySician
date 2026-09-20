"""Letting go of the filter box without letting go of the filter.

*"Brauche eine Möglichkeit, dass der Textfilter F aktiv ist und ich
rausklicke und dann alle anderen Tasten funktionieren, ohne dass ich weiter
in den Filter schreibe. Rausklicken entweder per Maus oder mit Pfeiltasten
fahren -> schon kann ich U für Stimmen, M für Favorit, Str+N für Neu
nutzen."*

The two states were always separate — `_search_text` is the filter and
`_search_active` is only "the box owns the letters" — and nothing anywhere
put the second one down without throwing the first one away with it. So a
filtered list could not be tuned from, starred or sorted without retyping
the filter afterwards.
"""

import pygame
import pytest

from pickhero.config import Config
from pickhero.ui.menu import MenuScreen


def _key(key, mod=0, unicode=""):
    return pygame.event.Event(pygame.KEYDOWN, key=key, unicode=unicode, mod=mod)


def _click(pos):
    return pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)


@pytest.fixture(autouse=True)
def _screen():
    """render() needs fonts and a surface; handle_event does not."""
    pygame.init()
    try:
        pygame.display.set_mode((1280, 720))
    except pygame.error:
        pytest.skip("no display")
    pygame.font.init()


@pytest.fixture
def songs(tmp_path):
    for name in ("alpha.gp5", "bravo.gp5", "charlie.gp5", "brava.gp5"):
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


@pytest.fixture
def menu(songs):
    screen = MenuScreen(songs, config=Config())
    screen.handle_event(_key(pygame.K_f))
    for ch in "br":
        screen.handle_event(_key(ord(ch), unicode=ch))
    return screen


class TestTheFilterIsTyped:
    def test_the_box_has_the_letters_and_the_list_is_narrowed(self, menu):
        assert menu.is_searching
        assert menu._search_text == "br"
        assert [p.stem for p in menu._display_files] == ["brava", "bravo"]


class TestLettingGo:

    @pytest.mark.parametrize("key", [pygame.K_UP, pygame.K_DOWN,
                                     pygame.K_PAGEUP, pygame.K_PAGEDOWN,
                                     pygame.K_HOME, pygame.K_END])
    def test_a_list_key_lets_go_and_keeps_the_filter(self, menu, key):
        menu.handle_event(_key(key))
        assert menu.is_searching is False
        assert menu._search_text == "br"
        assert [p.stem for p in menu._display_files] == ["brava", "bravo"]

    def test_a_click_outside_the_box_lets_go(self, menu):
        menu.render(pygame.Surface((1280, 720)))
        outside = (menu._search_box.x + 10, menu._search_box.bottom + 80)
        menu.handle_event(_click(outside))
        assert menu.is_searching is False
        assert menu._search_text == "br"

    def test_a_click_INSIDE_the_box_takes_the_letters_back(self, menu):
        menu.render(pygame.Surface((1280, 720)))
        menu.handle_event(_key(pygame.K_DOWN))
        assert menu.is_searching is False
        menu.handle_event(_click(menu._search_box.center))
        assert menu.is_searching is True
        assert menu._search_text == "br"

    def test_enter_still_opens_the_song_rather_than_letting_go(self, menu):
        """ENTER acts on the list and does NOT belong in that set: it leaves
        the screen entirely, so dropping the focus first would be a state
        nobody is ever in."""
        assert menu.handle_event(_key(pygame.K_RETURN)) is not None


class TestWhatWorksOnceItHasLetGo:
    """The whole point. Every one of these is a key the player named."""

    def _let_go(self, menu):
        menu.handle_event(_key(pygame.K_DOWN))
        return menu

    def test_the_app_stops_treating_it_as_a_text_field(self, menu):
        """`is_typing` is what the App asks before it consumes D, O, S, G
        and U -- so U for the tuner is this one assertion."""
        assert menu.is_typing is True
        self._let_go(menu)
        assert menu.is_typing is False

    def test_m_stars_the_song_instead_of_typing_an_m(self, menu):
        self._let_go(menu)
        song = menu._selected_path()
        menu.handle_event(_key(pygame.K_m, unicode="m"))
        assert menu._config.is_favourite(song.stem) is True
        assert menu._search_text == "br"

    def test_n_sorts_instead_of_typing_an_n(self, menu):
        self._let_go(menu)
        was = menu._sort_mode
        menu.handle_event(_key(pygame.K_n, unicode="n"))
        assert menu._sort_mode != was

    def test_ctrl_n_worked_all_along_and_still_does(self, menu):
        """It was built to work mid-word, so it is the control: if this ever
        starts depending on the focus, the Ctrl rule has been broken."""
        song = menu._selected_path()
        menu.handle_event(_key(pygame.K_n, mod=pygame.KMOD_CTRL))
        assert menu._is_new(song) is False
        assert menu.is_searching is True

    def test_t_is_a_letter_again_only_while_the_box_has_it(self, menu):
        from pickhero.ui.colors import get_theme
        before = get_theme()
        menu.handle_event(_key(pygame.K_t, unicode="t"))
        assert menu._search_text == "brt"        # still a letter
        assert get_theme() is before


class TestGettingBackIn:

    def test_f_resumes_rather_than_clears(self, menu):
        """Leaving to press U and coming back to narrow it further is the
        workflow this exists for; clearing would make the way out a one-way
        door."""
        menu.handle_event(_key(pygame.K_DOWN))
        menu.handle_event(_key(pygame.K_f, unicode="f"))
        assert menu.is_searching is True
        assert menu._search_text == "br"
        for ch in "avo":
            menu.handle_event(_key(ord(ch), unicode=ch))
        assert menu._search_text == "bravo"
        assert [p.stem for p in menu._display_files] == ["bravo"]

    def test_escape_empties_a_filter_that_has_been_let_go(self, menu):
        """It used to reach the quit prompt instead -- a trap on a screen
        the player arrives at with ESC already under their finger."""
        menu.handle_event(_key(pygame.K_DOWN))
        assert menu.handle_event(_key(pygame.K_ESCAPE)) is None
        assert menu._search_text == ""
        assert len(menu._display_files) == 4

    def test_escape_with_no_filter_still_offers_to_quit(self, songs):
        menu = MenuScreen(songs, config=Config())
        assert menu.handle_event(_key(pygame.K_ESCAPE)) == "escape"

    def test_the_box_says_which_state_it_is_in(self, menu):
        """A caret is the signal, and the way back has to be named: a filter
        you cannot get back into is one you have to retype."""
        menu.render(pygame.Surface((1280, 720)))
        typing = menu.copy_text()
        menu.handle_event(_key(pygame.K_DOWN))
        menu.render(pygame.Surface((1280, 720)))
        assert menu.is_searching is False
        # The list it describes is the same one either way.
        assert "brava" in typing and "brava" in menu.copy_text()
