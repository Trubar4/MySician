"""A key that destroys files may not repeat, and a delete may not be final.

*"Alle Songs mit Drop D Stimmung sind weg. Ich war in der Übersicht in diesem
Filter als die App abgestürzt ist. Alles weg MP3, GP, Map..."*

`pygame.key.set_repeat(300, 40)` is one global setting for every key, and DEL
arms on the first press and DELETES on the second -- so a held DEL is 25
confirmations a second, each pair taking one song and every file beside it.
With the tuning filter on it walks the filtered list, which is exactly the set
of songs that went.
"""

import pathlib
import pygame
import pytest

from pickhero.config import Config
from pickhero.tabs import remove
from pickhero.ui import app as app_module
from pickhero.ui.menu import MenuScreen


def _down():
    return pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DELETE,
                              unicode="", mod=0)


def _up():
    return pygame.event.Event(pygame.KEYUP, key=pygame.K_DELETE, mod=0)


@pytest.fixture
def songs(tmp_path):
    # The trash follows CONFIG_DIR, which conftest already redirects -- so
    # nothing here can reach the real ~/.pickhero.
    folder = tmp_path / "songs"
    folder.mkdir()
    for name in ("Drop A", "Drop B", "Drop C", "Drop D", "Drop E"):
        (folder / f"{name}.gp5").write_bytes(b"x")
        (folder / f"{name}.mp3").write_bytes(b"x")
        (folder / f"{name}.songsterr.json").write_text("{}")
    return folder


def _screen(folder, tmp_path):
    config = Config()
    config.save = lambda *a, **k: None
    return MenuScreen(folder, config=config)


class TestAHeldDeleteKeyTakesNothing:
    def test_forty_repeats_of_one_press_delete_nothing(self, songs, tmp_path):
        """Two seconds of a held key at set_repeat(300, 40). On the unfixed
        code this emptied the folder -- five songs, fifteen files."""
        screen = _screen(songs, tmp_path)
        for _ in range(43):
            screen.handle_event(_down())
        assert len(list(songs.glob("*.gp5"))) == 5
        assert len(list(songs.iterdir())) == 15

    def test_two_deliberate_presses_still_delete(self, songs, tmp_path):
        """The feature still works, which is what makes the guard a guard
        rather than a removal."""
        screen = _screen(songs, tmp_path)
        screen.handle_event(_down())
        screen.handle_event(_up())
        screen.handle_event(_down())
        screen.handle_event(_up())
        assert len(list(songs.glob("*.gp5"))) == 4

    def test_the_app_drops_the_repeats_before_any_screen_sees_them(self):
        """The first lock, and it is on the one door every screen's events
        come through. The second is in `_delete_selected`, because a screen
        is only ever as safe as whatever is handing it events."""
        assert pygame.K_DELETE in app_module.NEVER_REPEAT


class TestADeletedSongCanComeBack:
    """`Path.unlink()` does not reach the Windows recycle bin, so DEL was the
    one irreversible key in the app -- one row from the arrow keys."""

    def _delete_one(self, screen):
        screen.handle_event(_down())
        screen.handle_event(_up())
        screen.handle_event(_down())
        screen.handle_event(_up())

    def test_the_files_are_kept_not_destroyed(self, songs, tmp_path):
        screen = _screen(songs, tmp_path)
        self._delete_one(screen)
        kept = remove.trashed()
        assert len(kept) == 1
        assert sorted(p.name for p in kept[0].iterdir()) == [
            "Drop A.gp5", "Drop A.mp3", "Drop A.songsterr.json"]

    def test_ctrl_z_puts_the_last_one_back(self, songs, tmp_path):
        screen = _screen(songs, tmp_path)
        self._delete_one(screen)
        assert not (songs / "Drop A.gp5").exists()
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_z, unicode="\x1a",
            mod=pygame.KMOD_CTRL))
        assert (songs / "Drop A.gp5").exists()
        assert (songs / "Drop A.mp3").exists()
        assert (songs / "Drop A.songsterr.json").exists()

    def test_it_never_overwrites_a_song_that_took_the_name_again(
            self, songs, tmp_path):
        """An undo destroying something newer than what it undoes is worse
        than no undo."""
        screen = _screen(songs, tmp_path)
        self._delete_one(screen)
        (songs / "Drop A.gp5").write_bytes(b"NEW")
        report = remove.restore_last(songs)
        assert (songs / "Drop A.gp5").read_bytes() == b"NEW"
        assert any("already there" in f for f in report.failed)

    def test_nothing_to_put_back_is_said_and_not_raised(self, songs, tmp_path):
        screen = _screen(songs, tmp_path)
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_z, unicode="\x1a",
            mod=pygame.KMOD_CTRL))
        assert len(list(songs.glob("*.gp5"))) == 5

    def test_the_trash_is_bounded(self, songs, tmp_path, monkeypatch):
        monkeypatch.setattr(remove, "MAX_TRASH", 2)
        for _ in range(4):
            screen = _screen(songs, tmp_path)
            self._delete_one(screen)
        assert len(remove.trashed()) == 2

    def test_the_practice_diary_is_never_in_there(self, songs, tmp_path):
        """He asked for that by name: *ausser History*."""
        screen = _screen(songs, tmp_path)
        self._delete_one(screen)
        names = [p.name for folder in remove.trashed() for p in folder.iterdir()]
        assert not any("practice_log" in n or "progress" in n for n in names)
