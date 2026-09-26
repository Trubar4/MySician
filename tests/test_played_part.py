"""A score for the stretch that was actually played.

*"Habe nur die 2. Hälfte des Songs gespielt. Kann ich dann auch für den
gespielten Teil eine Bewertung haben? Es ist ok, wenn ich keinen neuen Logs in
Best mache, wenn ich nicht den ganzen Song spiele, ich brauche aber eine
Bewertung von dem was ich gespielt habe."*

His run: 54.6 % over 324 notes, 75 of which crossed the playhead while he was
waiting for the second half. Read over the bars he played in, the same run is
**69.7 %** -- and the 52 notes he got wrong INSIDE those bars still count.
"""

import pygame
import pytest

from pickhero.played import Played, bar_of, played_bars, score


def _notes(spec):
    """(ms, reached, hit, close) from a compact "ms:verdict" list."""
    out = []
    for ms, verdict in spec:
        out.append((ms, verdict != "pending", verdict == "hit",
                    verdict == "close"))
    return out


BARS = [0.0, 1000.0, 2000.0, 3000.0, 4000.0]        # five bars of a second


class TestWhichBarsWerePlayed:
    def test_a_strike_puts_its_bar_in(self):
        assert played_bars([1500.0], BARS) == {2}

    def test_and_nothing_else(self):
        assert played_bars([1500.0, 1600.0], BARS) == {2}

    def test_a_song_with_no_bars_reads_nothing(self):
        """A tab that parsed without measure info has no bars to name, and a
        zero there would read as one."""
        assert played_bars([1500.0], []) == set()

    def test_bar_numbers_match_the_run_log(self):
        """The log numbers with `bisect_right` over the measure starts, and
        two different answers to "which bar" is how a weakest-section line
        comes to name a place nobody can find."""
        assert bar_of(0.0, BARS) == 1
        assert bar_of(999.0, BARS) == 1
        assert bar_of(1000.0, BARS) == 2


class TestTheScoreItself:
    def test_the_stretch_nobody_played_is_left_out(self):
        notes = _notes([(100.0, "miss"), (200.0, "miss"),      # bar 1, silent
                        (2100.0, "hit"), (2200.0, "miss")])    # bar 3, played
        got = score(notes, [2150.0], BARS)
        assert (got.hits, got.total) == (1, 2)
        assert got.percent == pytest.approx(50.0)
        assert got.skipped == 2

    def test_a_bar_played_entirely_WRONG_still_counts(self):
        """The rule that keeps this honest. Scoring only the bars that went
        well would quietly drop the hardest ones and hand back a flattering
        number -- the same fault as the one being fixed, the other way up."""
        notes = _notes([(2100.0, "miss"), (2200.0, "miss")])
        got = score(notes, [2150.0], BARS)
        assert (got.hits, got.total) == (0, 2)
        assert got.percent == pytest.approx(0.0)
        assert got.skipped == 0

    def test_a_sat_out_middle_is_not_charged_for(self):
        """A span from the first strike to the last cannot say "I played the
        intro and the solo and sat out the verse"."""
        notes = _notes([(100.0, "hit"), (1100.0, "miss"), (2100.0, "hit")])
        got = score(notes, [150.0, 2150.0], BARS)
        assert (got.hits, got.total) == (2, 2)
        assert got.skipped == 1

    def test_a_note_never_reached_is_neither_played_nor_skipped(self):
        notes = _notes([(2100.0, "hit"), (3100.0, "pending")])
        got = score(notes, [2150.0], BARS)
        assert (got.total, got.skipped) == (1, 0)

    def test_the_bars_are_named(self):
        notes = _notes([(1100.0, "hit"), (3100.0, "hit")])
        got = score(notes, [1150.0, 3150.0], BARS)
        assert got.bars_text() == "bars 2-4"

    def test_one_bar_reads_as_one_bar(self):
        notes = _notes([(1100.0, "hit")])
        assert score(notes, [1150.0], BARS).bars_text() == "bar 2"


class TestWhenItSaysNothing:
    def test_a_song_played_end_to_end_has_nothing_to_add(self):
        """The two numbers are the same, and a line repeating one already on
        screen is the wallpaper the HUD was cut down to remove."""
        notes = _notes([(100.0, "hit"), (1100.0, "miss")])
        got = score(notes, [150.0, 1150.0], BARS)
        assert got.skipped == 0
        assert not got.worth_saying

    def test_a_song_nobody_played_says_nothing_rather_than_zero(self):
        """Nothing played is not the same as everything missed, and a zero
        would claim it was."""
        got = score(_notes([(100.0, "miss")]), [], BARS)
        assert got.percent is None
        assert not got.worth_saying

    def test_the_second_half_of_a_song_is_worth_saying(self):
        notes = _notes([(100.0, "miss"), (2100.0, "hit")])
        assert score(notes, [2150.0], BARS).worth_saying


class TestItIsWiredIntoTheScreen:
    """A number nothing draws is a feature that ships doing nothing, which
    this project has now shipped four times."""

    def _screen(self):
        from pickhero.config import Config
        from pickhero.matcher import NoteMatcher
        from pickhero.tabs.timeline import MeasureInfo, NoteEvent, SongMetadata, Timeline
        from pickhero.ui.scrolling import PlayingScreen
        notes = [NoteEvent(100.0, 200.0, 64, 1, 5),      # bar 1
                 NoteEvent(2100.0, 200.0, 64, 1, 5),     # bar 3
                 NoteEvent(2300.0, 200.0, 64, 1, 5)]
        measures = [MeasureInfo(index=i, start_ms=s, end_ms=s + 1000.0)
                    for i, s in enumerate((0.0, 1000.0, 2000.0, 3000.0))]
        timeline = Timeline(notes, SongMetadata(title="T", artist="A", tempo=120),
                            measures=measures)
        screen = PlayingScreen(timeline, config=Config())
        screen._matcher = NoteMatcher(timeline)
        return screen, timeline

    def test_the_screen_asks_the_same_question(self):
        from pickhero.matcher import MatchType
        screen, timeline = self._screen()
        screen._heard_at = [2150.0]
        for note, state in zip(timeline.notes,
                               (MatchType.MISS, MatchType.HIT, MatchType.MISS)):
            screen._matcher._set_state(note, state)
        part = screen._played_part()
        assert (part.hits, part.total, part.skipped) == (1, 2, 1)

    def test_a_replayed_passage_forgets_its_old_strikes(self):
        """`forget_from` spends the verdicts from the seek onward, so the
        strikes have to go with them -- or the bars stay counted as played
        while the notes in them are PENDING again."""
        screen, _ = self._screen()
        screen._heard_at = [500.0, 2150.0, 2500.0]
        screen._forget_heard_from(2000.0)
        assert screen._heard_at == [500.0]

    def test_the_line_reaches_the_bottom_left_of_the_screen(self):
        from pickhero.matcher import MatchType
        from pickhero.ui import strip
        pygame.init()
        pygame.display.set_mode((1280, 720))
        try:
            screen, timeline = self._screen()
            for note, state in zip(timeline.notes,
                                   (MatchType.MISS, MatchType.HIT, MatchType.HIT)):
                screen._matcher._set_state(note, state)
            # The counters the strip's own numbers read; without them the
            # block returns before it draws anything at all.
            screen._matcher.hits, screen._matcher.misses = 2, 1
            rect = pygame.Rect(0, 0, strip.STRIP_NUMBERS_W, strip.STRIP_HEIGHT)
            with_it = pygame.Surface((strip.STRIP_NUMBERS_W, strip.STRIP_HEIGHT))
            without = with_it.copy()
            screen._heard_at = [2150.0]
            screen._blit_strip_numbers(with_it, rect)
            screen._heard_at = [150.0, 2150.0]          # everything played
            screen._blit_strip_numbers(without, rect)
            band = pygame.Rect(strip.STRIP_NUMBERS_W - strip.STRIP_PLAYED_W, 0,
                               strip.STRIP_PLAYED_W, strip.STRIP_HEIGHT)
            assert (pygame.image.tostring(with_it.subsurface(band), "RGB")
                    != pygame.image.tostring(without.subsurface(band), "RGB"))
        finally:
            pygame.display.quit()
            pygame.quit()

    def test_the_miniature_never_moves(self, ):
        """A column that appears when the first verdict lands slides the
        whole song sideways -- a real bug on this strip once, found by a
        verdict landing on the wrong pixel."""
        from pickhero.ui import strip
        assert strip.STRIP_PLAYED_W < strip.STRIP_NUMBERS_W
        # The room is a constant, so nothing about the score can move it.
        assert isinstance(strip.STRIP_NUMBERS_W, int)
