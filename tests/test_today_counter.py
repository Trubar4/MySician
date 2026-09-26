"""How much has been played today, on the screen where the day is spent.

*"Kann ich in der Songuebersicht rechts oben einen Zaehler haben, wie viele
Minuten ich heute schon gespielt habe? Wie viele Songs und wie viele
Anschlaege?"*

Everything needed was already in `practice_log.Total`. What was missing was a
way to ask it about TODAY, and a screen that re-reads it when the answer has
just changed -- because the sitting is written when a song is LEFT, and
leaving a song lands on this screen.
"""

import pygame
import pytest

from pickhero import practice_log
from pickhero.config import Config
from pickhero.ui.menu import MenuScreen


@pytest.fixture
def songs(tmp_path):
    (tmp_path / "alpha.gp5").write_bytes(b"x")
    return tmp_path


def _session(started, song, seconds, strikes):
    return practice_log.Session(started=started, song=song, seconds=seconds,
                                strikes=strikes, tempo_percent=100)


class TestTheLineItself:
    def _screen(self, songs, total):
        screen = MenuScreen(songs, config=Config())
        screen._today = total
        return screen

    def test_it_names_all_three_numbers(self, songs):
        rows = [_session("2026-09-26T10:00:00", "A", 300.0, 400),
                _session("2026-09-26T11:00:00", "B", 300.0, 600)]
        total = practice_log.today(rows, day="2026-09-26")
        line = self._screen(songs, total)._today_line()
        assert "10 min" in line and "2 songs" in line and "1000 strikes" in line

    def test_one_song_is_not_plural(self, songs):
        rows = [_session("2026-09-26T10:00:00", "A", 60.0, 5)]
        total = practice_log.today(rows, day="2026-09-26")
        assert "1 song " in self._screen(songs, total)._today_line() + " "

    def test_a_day_nobody_played_says_nothing(self, songs):
        """A permanent "0 min - 0 songs - 0 strikes" is clutter on a screen
        that was cut down on purpose. The first sitting makes it appear."""
        assert self._screen(songs, None)._today_line() == ""


class TestItIsRereadWhereTheAnswerChanges:
    """The diary is written when a song is left, and leaving a song calls
    `scan_files`. A number that is right in the file and stale on the screen is
    indistinguishable from a diary that loses sittings -- which this project
    has already shipped once, when the dashboard was only rebuilt on exit and
    twelve sittings totalling 10.1 minutes read as three.
    """

    def test_scanning_reads_it(self, songs, monkeypatch, tmp_path):
        log = tmp_path / "practice_log.jsonl"
        monkeypatch.setattr(practice_log, "PRACTICE_FILE", log)
        screen = MenuScreen(songs, config=Config())
        assert screen._today_line() == ""
        practice_log.append(_session(practice_log.now_iso(), "A", 120.0, 250),
                            path=log)
        screen.scan_files()
        assert "250 strikes" in screen._today_line()

    def test_a_broken_diary_is_never_why_the_list_fails(self, songs,
                                                        monkeypatch):
        def boom():
            raise OSError("disk gone")
        monkeypatch.setattr(practice_log, "today", boom)
        screen = MenuScreen(songs, config=Config())
        screen.refresh_today()
        assert screen._today_line() == ""


class TestItIsReallyDrawn:
    """A counter nothing blits is a feature that ships doing nothing."""

    def test_the_line_reaches_the_screen(self, songs):
        pygame.init()
        pygame.display.set_mode((1280, 720))
        try:
            screen = MenuScreen(songs, config=Config())
            rows = [_session("2026-09-26T10:00:00", "A", 600.0, 1234)]
            screen._today = practice_log.today(rows, day="2026-09-26")
            surface = pygame.Surface((1280, 720))
            blank = pygame.Surface((1280, 720))
            screen.render(blank)
            screen._today = None
            screen.render(surface)
            # The top-right corner has ink with the counter and none without.
            band = pygame.Rect(700, 20, 560, 30)
            with_it = pygame.transform.chop(blank.subsurface(band), (0, 0, 0, 0))
            without = pygame.transform.chop(surface.subsurface(band), (0, 0, 0, 0))
            assert pygame.image.tostring(with_it, "RGB") != \
                pygame.image.tostring(without, "RGB")
        finally:
            pygame.display.quit()
            pygame.quit()
