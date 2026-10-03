"""Searching the help page: a key, or a word.

*"Koennen wir in die Hilfe Schlagworte aufnehmen? Wenn ich eine Taste
eingebe, kommen die Befehle dieser Taste. Wenn ich ein Wort eingebe, wie
drill, kommen die Befehle dazu."*

The rule that makes a synonym table safe is the one asserted here: every
target it names really appears on the page. A synonym pointing at nothing is
a search that silently finds nothing -- indistinguishable from a feature
that does not work, which is this project's oldest fault.
"""

import pygame
import pytest

from pickhero.ui import help_search
from pickhero.ui.scrolling import PlayingScreen
from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)


def _screen():
    song = Timeline(
        [NoteEvent(timestamp_ms=0.0, duration_ms=500.0, midi_note=40,
                   string=6, fret=0)],
        SongMetadata(title="t", tempo=120),
        measures=[MeasureInfo(index=0, start_ms=0.0, end_ms=2000.0)])
    return PlayingScreen(song)


@pytest.fixture
def blocks():
    return _screen().help_blocks()


def _texts(hits):
    return [help_search.item_text(hit.item) for hit in hits]


class TestEverySynonymPointsAtSomethingReal:
    """The one rule that keeps a hand-written table honest."""

    def test_every_target_is_on_the_page(self, blocks):
        page = help_search.fold(
            " ".join(f"{head} " + " ".join(help_search.item_text(i)
                                           for i in items)
                     for head, items, _size in blocks))
        missing = {word: [t for t in targets
                          if help_search.fold(t) not in page]
                   for word, targets in help_search.SYNONYMS.items()}
        missing = {w: t for w, t in missing.items() if t}
        assert missing == {}, f"synonyms pointing at nothing: {missing}"

    def test_and_every_one_of_them_really_finds_a_line(self, blocks):
        empty = [word for word in help_search.SYNONYMS
                 if not help_search.search(word, blocks)]
        assert empty == []


class TestTypingAKey:
    def test_a_bare_letter_finds_every_command_on_it(self, blocks):
        """*"die Befehle dieser Taste"* is all of them: N, Shift+N and
        Ctrl+N are three different commands on one key."""
        found = " | ".join(_texts(help_search.search("n", blocks)))
        assert "Shift+N" in found and "Ctrl+N" in found

    def test_a_spelled_out_modifier_means_that_one(self, blocks):
        hits = [h for h in help_search.search("Shift+N", blocks) if h.by_key]
        assert hits
        assert all("Shift+N" in help_search.item_text(h.item) for h in hits)

    def test_the_german_control_key_is_the_control_key(self, blocks):
        assert help_search.as_key("Strg+S") == help_search.as_key("Ctrl+S")
        assert [h for h in help_search.search("strg+s", blocks) if h.by_key]

    def test_case_and_the_named_keys(self):
        assert help_search.as_key("pgdn")[0] == pygame.K_PAGEDOWN
        assert help_search.as_key("esc")[0] == pygame.K_ESCAPE
        assert help_search.as_key("f3")[0] == pygame.K_F3
        assert help_search.as_key("+")[0] == pygame.K_PLUS

    def test_a_word_is_not_a_key(self):
        assert help_search.as_key("drill") is None
        assert help_search.as_key("") is None
        assert help_search.as_key("hyper+n") is None

    def test_a_letter_leading_a_line_counts_here_though_it_is_not_a_button(
            self, blocks):
        """The page must not underline a badge letter as a button -- X is the
        dead note -- but in a SEARCH a letter at the front of a line is what
        the player meant. A false hit costs a line he can read; a miss costs
        a command he cannot find."""
        found = " | ".join(_texts(help_search.search("n", blocks)))
        assert "N loops the next place" in found


class TestTypingAWord:
    def test_a_word_finds_what_it_is_about(self, blocks):
        assert len(help_search.search("drill", blocks)) >= 5

    def test_german_reaches_the_english_page(self, blocks):
        assert "practice speed" in " ".join(
            _texts(help_search.search("tempo", blocks)))
        assert "tuning" in " ".join(
            _texts(help_search.search("stimmung", blocks)))

    def test_either_spelling_of_an_umlaut(self, blocks):
        assert (_texts(help_search.search("lautstärke", blocks))
                == _texts(help_search.search("lautstaerke", blocks)))

    def test_a_prefix_finds_it_while_it_is_still_being_typed(self, blocks):
        assert help_search.search("stimm", blocks)

    def test_a_plural_does_not_need_a_row_of_its_own(self, blocks):
        assert help_search.search("statistiken", blocks)

    def test_nothing_matching_is_an_empty_answer_not_a_crash(self, blocks):
        assert help_search.search("qwertzuiop", blocks) == []

    def test_a_query_that_is_both_is_answered_as_both(self, blocks):
        """The same rule the song search follows for a bare number: reading
        TAB only as a key makes the tab page unfindable."""
        hits = help_search.search("tab", blocks)
        assert any(h.by_key for h in hits)
        assert any(not h.by_key for h in hits)

    def test_the_key_answers_come_first(self, blocks):
        kinds = [h.by_key for h in help_search.search("tab", blocks)]
        assert kinds == sorted(kinds, reverse=True)

    def test_nothing_is_listed_twice(self, blocks):
        hits = help_search.search("n", blocks) + help_search.search("tab",
                                                                    blocks)
        for query in ("n", "tab", "drill", "sync"):
            found = [(h.heading, help_search.item_text(h.item))
                     for h in help_search.search(query, blocks)]
            assert len(found) == len(set(found))


class TestTheBoxOwnsTheKeysWhileItIsOpen:
    """A text box is a text box wherever it is asked about -- the rule the
    rename editor needed, and the reason the gate sits at the top of
    `handle_event` rather than beside the help key."""

    def _press(self, screen, key, unicode=""):
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=key, unicode=unicode, mod=0))

    def test_slash_opens_it_only_while_the_help_is_up(self):
        screen = _screen()
        self._press(screen, pygame.K_SLASH, "/")
        assert screen._help_find is None, "no help, no search box"
        screen._show_help = True
        self._press(screen, pygame.K_SLASH, "/")
        assert screen._help_find == ""

    def test_a_layout_that_sends_another_key_code_still_opens_it(self):
        """A slash is Shift+7 on a German keyboard, so the character is the
        signal that survives -- the same lesson as `shift_held`."""
        screen = _screen()
        screen._show_help = True
        self._press(screen, pygame.K_7, "/")
        assert screen._help_find == ""

    def test_h_is_a_letter_while_typing(self):
        screen = _screen()
        screen._show_help = True
        screen._help_find = ""
        self._press(screen, pygame.K_h, "h")
        assert screen._show_help, "H must not close the help mid-query"
        assert screen._help_find == "h"

    def test_a_shortcut_letter_does_not_fire(self):
        screen = _screen()
        screen._show_help = True
        screen._help_find = ""
        theme = screen._config.theme if screen._config else None
        self._press(screen, pygame.K_t, "t")
        self._press(screen, pygame.K_d, "d")
        assert screen._help_find == "td"
        if screen._config is not None:
            assert screen._config.theme == theme

    def test_backspace_then_escape(self):
        screen = _screen()
        screen._show_help = True
        screen._help_find = "ab"
        self._press(screen, pygame.K_BACKSPACE)
        assert screen._help_find == "a"
        self._press(screen, pygame.K_BACKSPACE)
        assert screen._help_find == "", "an empty box is still an open box"
        self._press(screen, pygame.K_ESCAPE)
        assert screen._help_find is None and screen._show_help, \
            "ESC shuts the search and leaves the song alone"

    def test_escape_while_typing_never_leaves_the_song(self):
        screen = _screen()
        screen._show_help = True
        screen._help_find = "drill"
        answer = screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_ESCAPE, unicode="", mod=0))
        assert answer is None

    def test_enter_shuts_it_too(self):
        screen = _screen()
        screen._show_help = True
        screen._help_find = "drill"
        self._press(screen, pygame.K_RETURN)
        assert screen._help_find is None

    def test_the_hits_are_what_the_screen_offers(self):
        screen = _screen()
        screen._show_help = True
        screen._help_find = "drill"
        assert screen.help_hits() == help_search.search("drill",
                                                        screen.help_blocks())
        screen._help_find = ""
        assert screen.help_hits() == []


class TestItIsReallyDrawn:
    """A feature that cannot be seen working is indistinguishable from one
    that does not work -- this project's own oldest fault, and it has shipped
    four times. So the results are asserted to reach the SCREEN, not only the
    list they come from."""

    def _links_after(self, query):
        pygame.init()
        surface = pygame.display.set_mode((1280, 720))
        screen = _screen()
        screen._show_help = True
        screen._help_find = query
        screen.render(surface)
        return len(screen.links)

    def test_the_search_replaces_the_pages(self):
        pages = self._links_after(None)
        nothing = self._links_after("qwertzuiop")
        assert nothing < pages, "a search showing no hits draws no page"

    def test_a_hit_is_drawn_as_a_button_like_any_other_line(self):
        assert self._links_after("drill") > self._links_after("qwertzuiop")
