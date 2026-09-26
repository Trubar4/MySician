"""The tunings as chips you can click, beside the search field.

*"Möchte alle Stimmungen als Mini Karte neben dem Suchfeld zum Anklicken mit
der Maus. Das Blättern mit dem Kürzel nervt mich etwas."*

Two halves, and the layout is the half that can be tested without a screen:
the drawing and the MOUSE read one list of rects, for the reason `strip.py`
gives -- two answers to "where is this chip" is a click landing on the
neighbour of the one under the pointer.

The other half is that the strip really reaches the screen and really pushes
the list down. A strip nothing blits is a feature that ships doing nothing,
which this project has now shipped four times.
"""

import pathlib
import pygame
import pytest

from pickhero.audio.note_utils import NAMED_TUNINGS, tuning_label, tuning_notes
from pickhero.config import Config
from pickhero.tabs import song_index
from pickhero.tabs.song_index import SongIndex, SongInfo
from pickhero.ui import chips
from pickhero.ui.menu import MenuScreen


def width_of(text: str) -> int:
    """A fixed 10 px a character, so the arithmetic is arithmetic."""
    return 10 * len(text)


# -- the arithmetic ------------------------------------------------------


class TestWhereAChipLands:
    def test_the_first_one_starts_where_it_was_told(self):
        out = chips.lay_out([("", "All")], width_of, left=52, top=120,
                            right=1000)
        assert (out[0].x, out[0].y) == (52, 120)
        assert out[0].w == 30 + 2 * chips.CHIP_PAD

    def test_they_sit_side_by_side_with_a_gap(self):
        out = chips.lay_out([("a", "aa"), ("b", "bb")], width_of,
                            left=0, top=0, right=1000)
        assert out[1].x == out[0].right + chips.CHIP_GAP
        assert out[1].y == out[0].y

    def test_no_two_chips_ever_overlap(self):
        entries = [(str(i), "Drop " + "X" * (i % 7)) for i in range(40)]
        out = chips.lay_out(entries, width_of, left=52, top=120, right=700)
        for i, a in enumerate(out):
            for b in out[i + 1:]:
                assert not (a.x < b.right and b.x < a.right
                            and a.y < b.bottom and b.y < a.bottom), (a, b)

    def test_nothing_reaches_past_the_right_edge(self):
        entries = [(str(i), "Eb Standard 12") for i in range(20)]
        out = chips.lay_out(entries, width_of, left=52, top=0, right=600)
        assert all(c.x >= 52 and c.right <= 600 for c in out)

    def test_it_wraps_rather_than_running_off(self):
        entries = [(str(i), "Drop D 20") for i in range(12)]
        out = chips.lay_out(entries, width_of, left=0, top=0, right=400)
        rows = sorted({c.y for c in out})
        assert len(rows) > 1
        assert rows[1] - rows[0] == chips.CHIP_H + chips.ROW_GAP
        # Every row starts back at the left edge.
        for y in rows:
            assert min(c.x for c in out if c.y == y) == 0

    def test_a_chip_wider_than_the_room_is_drawn_anyway(self):
        """Shortening the text is a decision for whoever wrote it.

        The same answer the footer gives a single entry wider than the
        screen -- a chip silently dropped is a filter that cannot be reached.
        """
        out = chips.lay_out([("x", "x" * 200)], width_of,
                            left=10, top=0, right=210)
        assert len(out) == 1
        assert out[0].x == 10 and out[0].w == 200

    def test_a_click_is_on_exactly_one_chip(self):
        entries = [(str(i), "Drop D 20") for i in range(9)]
        out = chips.lay_out(entries, width_of, left=52, top=120, right=500)
        for chip in out:
            point = (chip.x + chip.w // 2, chip.y + chip.h // 2)
            assert [c.value for c in out if c.hit(point)] == [chip.value]

    def test_a_click_between_two_chips_is_on_neither(self):
        out = chips.lay_out([("a", "aa"), ("b", "bb")], width_of,
                            left=0, top=0, right=1000)
        gap = (out[0].right + 1, out[0].y + 2)
        assert not any(c.hit(gap) for c in out)

    def test_the_height_is_the_rows_it_took(self):
        top = 120
        one = chips.lay_out([("a", "aa")], width_of, 0, top, 1000)
        assert chips.height(one, top) == chips.CHIP_H
        many = chips.lay_out([(str(i), "Drop D 20") for i in range(12)],
                             width_of, 0, top, 400)
        rows = len({c.y for c in many})
        assert chips.height(many, top) == \
            rows * chips.CHIP_H + (rows - 1) * chips.ROW_GAP

    def test_no_chips_is_no_height(self):
        assert chips.height([], 120) == 0


# -- what a chip SAYS ---------------------------------------------------


class TestTheLabelIsTheNameWhereThereIsOne:
    def test_the_common_ones_get_their_name(self):
        assert tuning_label("D A D G B E") == "Drop D"
        assert tuning_label("C G C F A D") == "Drop C"
        assert tuning_label("E A D G B E") == "Standard"

    def test_a_tuning_nobody_named_keeps_its_letters(self):
        """Inventing a name would be the guess this project refuses."""
        assert tuning_label("E A E A C# E") == "E A E A C# E"
        assert tuning_label("F C F A# D# G") == "F C F A# D# G"

    def test_every_named_tuning_round_trips(self):
        for name, shape in NAMED_TUNINGS:
            assert tuning_label(" ".join(tuning_notes(shape))) == name

    def test_nothing_in_nothing_out(self):
        assert tuning_label("") == ""
        assert tuning_label(None) == ""


# -- the index's counts -------------------------------------------------


class TestCountingTheFolder:
    def test_a_song_counts_once_per_distinct_tuning(self, tmp_path):
        """Six guitar tracks in standard tuning are one answer, not six."""
        index = SongIndex(tmp_path / "idx.json")
        song = tmp_path / "a.gp5"
        song.write_bytes(b"x")
        index._record(song, SongInfo(3, ["E A D G B E", "E A D G B E",
                                         "D A D G B E"]))
        assert index.tuning_counts([song]) == {"E A D G B E": 1,
                                               "D A D G B E": 1}

    def test_the_order_is_still_commonest_first(self, tmp_path):
        index = SongIndex(tmp_path / "idx.json")
        files = []
        for i, tuning in enumerate(["D A D G B E"] * 3 + ["E A D G B E"] * 5):
            song = tmp_path / f"s{i}.gp5"
            song.write_bytes(b"x")
            files.append(song)
            index._record(song, SongInfo(1, [tuning]))
        assert index.tunings_present(files) == ["E A D G B E", "D A D G B E"]

    def test_a_song_nobody_has_read_counts_for_nothing(self, tmp_path):
        index = SongIndex(tmp_path / "idx.json")
        song = tmp_path / "a.gp5"
        song.write_bytes(b"x")
        assert index.tuning_counts([song]) == {}


# -- the strip on the real screen ---------------------------------------


TUNINGS = ["E A D G B E"] * 5 + ["D A D G B E"] * 3 + ["C G C F A D"] * 2 \
    + ["E A E A C# E"]


def _menu(tmp_path, monkeypatch, tunings):
    """A song list over a folder whose index is already filled in.

    The files are not real GP files, so the background scan would record
    every one as unreadable and undo the entries; the filtering is what is
    under test, not the reading. Same idiom as `test_song_index.py`.
    """
    monkeypatch.setattr(song_index, "index_file",
                        lambda: tmp_path / "index.json")
    monkeypatch.setattr(SongIndex, "scan_in_background",
                        lambda self, files: None)
    songs = tmp_path / "songs"
    songs.mkdir()
    screen = MenuScreen(songs, config=Config())
    for i, tuning in enumerate(tunings):
        song = songs / f"Song {i:02d}.gp5"
        song.write_bytes(b"x")
        screen._index._record(song, SongInfo(1, [tuning]))
    screen.scan_files()
    return screen


@pytest.fixture
def menu(tmp_path, monkeypatch):
    """Eleven songs in four tunings, three of them named."""
    return _menu(tmp_path, monkeypatch, TUNINGS)


#: The player's own folder as his song index records it: the ten tunings
#: with the counts they really have, so the strip's width is measured
#: against his screen rather than against something convenient. One tuning
#: per song here, so the total is 95 rather than his 88 -- a song holding
#: two tunings is counted in both buckets, which is what the filter does.
HIS_FOLDER = (["E A D G B E"] * 41 + ["D A D G B E"] * 20
              + ["C G C F A D"] * 17 + ["D# G# C# F# A# D#"] * 5
              + ["C# G# C# F# A# D#"] * 3 + ["D G C F A D"] * 3
              + ["A E A D F# B"] * 2 + ["E A E A C# E"] * 2
              + ["C# G# C# F# B E"] + ["F C F A# D# G"])


@pytest.fixture
def his_menu(tmp_path, monkeypatch):
    return _menu(tmp_path, monkeypatch, HIS_FOLDER)


class TestTheStripIsReallyOnTheScreen:
    def test_it_is_laid_out_when_the_folder_has_several_tunings(self, menu):
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            screen = menu
            screen.render(pygame.Surface((1920, 1080)))
            labels = [c.label for c in screen._tuning_chips]
            assert labels[0] == "All 11"
            assert "Standard 5" in labels
            assert "Drop D 3" in labels
            assert "E A E A C# E 1" in labels
        finally:
            pygame.quit()

    def test_the_label_becomes_ink(self, menu):
        """A strip nothing blits is a feature that ships doing nothing.

        Counted strictly INSIDE the padding, so the chip's own outline
        cannot pass this on behalf of the text.
        """
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            surface = pygame.Surface((1920, 1080))
            menu.render(surface)
            assert menu._tuning_chips
            chip = menu._tuning_chips[1]
            inner = pygame.Rect(chip.rect).inflate(-2 * chips.CHIP_PAD, -10)
            painted = sum(1 for x in range(inner.x, inner.right)
                          for y in range(inner.y, inner.bottom)
                          if surface.get_at((x, y))[:3] != (20, 20, 30))
            assert painted > 30, painted
        finally:
            pygame.quit()

    def test_one_tuning_is_not_a_choice(self, tmp_path, monkeypatch):
        """A strip reading [All 4][Standard 4] says nothing twice."""
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            screen = _menu(tmp_path, monkeypatch, ["E A D G B E"] * 4)
            screen.render(pygame.Surface((1920, 1080)))
            assert screen._tuning_chips == []
        finally:
            pygame.quit()

    def test_an_unread_folder_shows_no_strip(self, tmp_path, monkeypatch):
        """Blank rows already mean "not read yet"; a strip would guess."""
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            screen = _menu(tmp_path, monkeypatch, [])
            for i in range(3):
                (screen._songs_dir / f"x{i}.gp5").write_bytes(b"x")
            screen.scan_files()
            screen.render(pygame.Surface((1920, 1080)))
            assert screen._tuning_chips == []
        finally:
            pygame.quit()

    @pytest.mark.parametrize("size", [(1280, 720), (1600, 900), (1920, 1080)])
    def test_the_list_starts_below_the_strip(self, menu, size):
        """It pushes the list down rather than being drawn through it.

        `list_top` was the constant 124 -- the same fault VISIBLE_ITEMS and
        the footer each paid for.
        """
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            screen = menu
            screen.render(pygame.Surface(size))
            lowest = max(c.bottom for c in screen._tuning_chips)
            assert screen._list_top >= lowest + 4
        finally:
            pygame.quit()

    def test_it_fits_on_one_row_on_the_screens_it_is_for(self, his_menu):
        """Full width under the box is what buys this.

        Beside the box there are only 1412 px on 1920 and 772 on 1280; this
        strip is 1264 px wide on the player's real folder, so beside the box
        it would wrap on every window he owns.
        """
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            for size in [(1600, 900), (1920, 1080), (1920, 1200)]:
                his_menu.render(pygame.Surface(size))
                assert len({c.y for c in his_menu._tuning_chips}) == 1, size
        finally:
            pygame.quit()

    def test_a_narrow_window_wraps_and_the_list_still_fits(self, his_menu):
        """Two rows costs song rows, and that is the honest trade -- but
        nothing may be drawn off the edge or through the list."""
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            his_menu.render(pygame.Surface((900, 700)))
            chips_on = his_menu._tuning_chips
            assert len({c.y for c in chips_on}) > 1
            assert all(c.right <= 900 - 24 for c in chips_on)
            assert his_menu._list_top > max(c.bottom for c in chips_on)
            assert his_menu._visible_items >= 3
        finally:
            pygame.quit()


class TestClickingOne:
    def _click(self, screen, chip):
        return screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1,
            pos=(chip.x + chip.w // 2, chip.y + chip.h // 2)))

    def test_it_filters(self, menu):
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            menu.render(pygame.Surface((1920, 1080)))
            drop_d = next(c for c in menu._tuning_chips
                          if c.value == "D A D G B E")
            self._click(menu, drop_d)
            assert menu._tuning_filter == "D A D G B E"
            assert len(menu._display_files) == 3
        finally:
            pygame.quit()

    def test_clicking_it_again_shows_everything(self, menu):
        """The chip that is on is the way to switch it off."""
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            menu.render(pygame.Surface((1920, 1080)))
            drop_d = next(c for c in menu._tuning_chips
                          if c.value == "D A D G B E")
            self._click(menu, drop_d)
            self._click(menu, drop_d)
            assert menu._tuning_filter == ""
            assert len(menu._display_files) == len(TUNINGS)
        finally:
            pygame.quit()

    def test_all_clears_it(self, menu):
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            menu.render(pygame.Surface((1920, 1080)))
            by_value = {c.value: c for c in menu._tuning_chips}
            self._click(menu, by_value["C G C F A D"])
            assert menu._tuning_filter == "C G C F A D"
            self._click(menu, by_value[""])
            assert menu._tuning_filter == ""
        finally:
            pygame.quit()

    def test_it_never_opens_a_song(self, menu):
        """A chip is not a row, and two clicks on one must not start playing."""
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            menu.render(pygame.Surface((1920, 1080)))
            chip = menu._tuning_chips[1]
            assert self._click(menu, chip) is None
            assert self._click(menu, chip) is None
        finally:
            pygame.quit()

    def test_it_keeps_the_cursor_on_the_song_it_was_on(self, menu):
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            menu.render(pygame.Surface((1920, 1080)))
            menu._selected = 2
            before = menu._display_files[2]
            self._click(menu, menu._tuning_chips[0])
            assert menu._display_files[menu._selected] == before
        finally:
            pygame.quit()

    def test_it_gives_the_letters_back_to_the_list(self, menu):
        """Clicking anything but the box drops the search focus."""
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            menu.render(pygame.Surface((1920, 1080)))
            menu._search_active = True
            self._click(menu, menu._tuning_chips[1])
            assert not menu._search_active
        finally:
            pygame.quit()

    def test_it_works_with_the_search_box_open(self, menu):
        """Finding a song and narrowing by tuning are the same job."""
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            menu._search_text = "Song 0"
            menu._search_active = True
            menu._apply_filter()
            menu.render(pygame.Surface((1920, 1080)))
            standard = next(c for c in menu._tuning_chips
                            if c.value == "E A D G B E")
            self._click(menu, standard)
            assert menu._tuning_filter == "E A D G B E"
            assert menu._search_text == "Song 0"
        finally:
            pygame.quit()

    def test_a_click_below_the_strip_is_still_a_song(self, menu):
        """The chips must not swallow the list they sit above."""
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            menu.render(pygame.Surface((1920, 1080)))
            menu.handle_event(pygame.event.Event(
                pygame.MOUSEBUTTONDOWN, button=1,
                pos=(300, menu._list_top + 2)))
            assert menu._tuning_filter == ""
            assert menu._selected == 0
        finally:
            pygame.quit()

    def test_his_folder_names_the_ones_he_asked_about(self, his_menu):
        """Ten tunings, three of them unnamed, commonest first."""
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            his_menu.render(pygame.Surface((1920, 1080)))
            assert [c.label for c in his_menu._tuning_chips] == [
                "All 95", "Standard 41", "Drop D 20", "Drop C 17",
                "Eb Standard 5", "Drop C# 3", "D Standard 3", "Drop A 2",
                "E A E A C# E 2", "C# G# C# F# B E 1", "F C F A# D# G 1"]
            drop_d = next(c for c in his_menu._tuning_chips
                          if c.value == "D A D G B E")
            self._click(his_menu, drop_d)
            assert len(his_menu._display_files) == 20
        finally:
            pygame.quit()

    def test_the_key_and_the_chips_agree(self, menu):
        """TAB walks exactly the chips the strip offers, in the same order.

        One helper answers both, so the strip cannot offer a tuning the key
        refuses -- the property `K` and its HUD line are held to.
        """
        pygame.init()
        pygame.display.set_mode((320, 240))
        try:
            menu.render(pygame.Surface((1920, 1080)))
            offered = [c.value for c in menu._tuning_chips]
            walked = [""]
            for _ in range(len(offered)):
                menu._cycle_tuning_filter()
                walked.append(menu._tuning_filter)
            assert walked[:len(offered)] == offered
            assert walked[-1] == ""
        finally:
            pygame.quit()
