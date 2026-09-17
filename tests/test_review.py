"""Looking back at a run, and saying what could not be judged.

Two asks, one session: *"Kannst du Dinge, die du nicht bewerten kannst grau
machen?"* and *"Können wir es so machen, dass ich zurückspringen kann und dann
auch in groß sehen, wie die Noten bewertet wurden? Sobald ich auf Play gehe,
überschreibe ich die vorigen Werte."*
"""

import pygame
import pytest

from pickhero.config import Config
from pickhero.matcher import MatchType, NoteMatcher
from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)
from pickhero.ui.colors import get_theme, unsure
from pickhero.ui.scrolling import PlayingScreen

BAR_MS = 2000.0


@pytest.fixture
def display():
    pygame.init()
    pygame.display.set_mode((1280, 800))
    yield
    pygame.display.quit()


def _song(bars=20, per_bar=4, strings=(6, 5, 4, 3)):
    notes, measures = [], []
    for bar in range(bars):
        measures.append(MeasureInfo(index=bar, start_ms=bar * BAR_MS,
                                    end_ms=(bar + 1) * BAR_MS))
        for beat in range(per_bar):
            notes.append(NoteEvent(
                timestamp_ms=bar * BAR_MS + beat * (BAR_MS / per_bar),
                duration_ms=300.0, midi_note=40 + beat,
                string=strings[beat % len(strings)], fret=beat, measure=bar))
    return Timeline(notes, SongMetadata(title="t", tempo=120),
                    measures=measures)


def _screen(song):
    screen = PlayingScreen(song, config=Config())
    screen._matcher = NoteMatcher(song)
    screen._audio_enabled = True
    return screen


class TestSayingWhatCouldNotBeJudged:

    def test_a_note_heard_as_itself_is_sound(self):
        song = _song()
        m = NoteMatcher(song)
        note = song.notes[0]
        m._record_match(note, MatchType.HIT, proved=True)
        assert m.unreliable(note) is None

    def test_a_chord_string_credited_to_a_strum_is_not(self):
        # Monophonic detection can never report a second chord tone, so most
        # of a six-string chord is credited on the evidence of the STRUM.
        # That has always been counted apart; now it is drawn apart too.
        song = _song()
        m = NoteMatcher(song)
        note = song.notes[0]
        m._record_match(note, MatchType.HIT, proved=False)
        assert m.unreliable(note) == "strum"

    def test_a_miss_with_an_unreadable_strike_in_its_window_is_not(self):
        song = _song()
        m = NoteMatcher(song)
        note = song.notes[2]
        m._unreadable_strikes.append(note.timestamp_ms + 20.0)
        m._mark_missed_notes(note.timestamp_ms + 4000.0)
        assert m.get_note_state(note) is MatchType.MISS
        assert m.unreliable(note) == "unreadable"

    def test_a_miss_with_nothing_struck_stays_full_strength(self):
        # The honest limit, and the one thing the player asked for that
        # cannot be given: "too quiet" and "not played" are the same silence
        # at the level of one note. The run log says it for the RUN.
        song = _song()
        m = NoteMatcher(song)
        note = song.notes[2]
        m._mark_missed_notes(note.timestamp_ms + 4000.0)
        assert m.get_note_state(note) is MatchType.MISS
        assert m.unreliable(note) is None

    def test_the_verdict_still_counts_exactly_as_it_did(self):
        # "Es ist ok, wenn du sie vorerst als gültig zählst, wie bisher auch."
        song = _song()
        m = NoteMatcher(song)
        for note in song.notes[:4]:
            m._record_match(note, MatchType.HIT, proved=False)
        assert m.get_statistics()["hits"] == 4
        assert m.get_statistics()["accuracy_percent"] == 100.0


class TestTheColourIsDrainedNotReplaced:

    def test_the_hue_survives(self, display):
        # Drained, not greyed: the player wants the verdict AND its footing.
        for name in ("feedback_hit", "feedback_close", "feedback_miss"):
            full = getattr(get_theme(), name)
            weak = unsure(full)
            assert weak != full
            brightest = max(range(3), key=lambda i: full[i])
            assert max(range(3), key=lambda i: weak[i]) == brightest

    def test_the_three_are_still_told_apart(self, display):
        t = get_theme()
        drained = {unsure(getattr(t, n)) for n in
                   ("feedback_hit", "feedback_close", "feedback_miss")}
        assert len(drained) == 3

    def test_all_three_views_ask_one_question(self, display):
        # One helper, so the board, the sheet and the strip cannot disagree
        # about whether a note was judged.
        song = _song()
        screen = _screen(song)
        note = song.notes[0]
        screen._matcher._record_match(note, MatchType.HIT, proved=False)
        green = get_theme().feedback_hit
        assert screen._drained(note, green) == unsure(green)
        assert screen._sheet_note_colour(note, (1, 2, 3)) == unsure(green)
        assert screen._strip_verdict_colour(note) == unsure(green)


class TestGoingBackToLook:

    def test_a_seek_keeps_every_verdict(self, display):
        # This is the whole feature. `seek` used to call `matcher.reset()`,
        # so going back to see how a passage was judged destroyed the answer
        # on the way -- which is what `hits 0` in a log taken after spooling
        # has always meant.
        song = _song()
        screen = _screen(song)
        for note in song.notes[:8]:
            screen._matcher._record_match(note, MatchType.HIT)
        screen.seek(0.0)
        assert screen._matcher.get_statistics()["hits"] == 8
        assert screen._matcher.get_note_state(song.notes[0]) is MatchType.HIT

    def test_play_spends_what_is_ahead_and_keeps_what_is_behind(self, display):
        song = _song()
        screen = _screen(song)
        for note in song.notes:
            screen._matcher._record_match(note, MatchType.HIT)
        here = song.notes[10].timestamp_ms
        screen.seek(here)
        screen.toggle_play()
        behind = [n for n in song.notes if n.timestamp_ms < here]
        ahead = [n for n in song.notes if n.timestamp_ms >= here]
        assert all(screen._matcher.get_note_state(n) is MatchType.HIT
                   for n in behind)
        assert all(screen._matcher.get_note_state(n) is MatchType.PENDING
                   for n in ahead)
        assert screen._matcher.get_statistics()["hits"] == len(behind)

    def test_a_loop_turn_re_judges_its_own_bars_and_nothing_else(self, display):
        # It used to reset the whole matcher every few seconds, so looping
        # four bars threw away everything played before the loop was set.
        song = _song()
        screen = _screen(song)
        for note in song.notes:
            screen._matcher._record_match(note, MatchType.HIT)
        screen._loop_start_ms, screen._loop_end_ms = 8 * BAR_MS, 10 * BAR_MS
        screen._loop_enabled = True
        screen._playing = True
        screen._last_tick = None
        screen._playback_ms = 10 * BAR_MS + 1.0
        screen.update()
        assert screen._matcher.get_note_state(song.notes[0]) is MatchType.HIT
        inside = [n for n in song.notes if n.timestamp_ms >= 8 * BAR_MS]
        assert all(screen._matcher.get_note_state(n) is MatchType.PENDING
                   for n in inside)

    def test_the_strip_shows_what_survived(self, display):
        # The layer is rebuilt from what the matcher HOLDS, not cleared --
        # otherwise the part that survived the forget would vanish from the
        # picture that is the whole point of going back.
        song = _song()
        screen, surface = _screen(song), pygame.Surface((1280, 800))
        for note in song.notes:
            screen._matcher._record_match(note, MatchType.HIT)
        screen._playback_ms = 10 * BAR_MS
        screen.toggle_play()
        # There is no sound card here, so starting playback switches audio
        # off; the strip then draws no verdicts, correctly. Put it back --
        # what is under test is the rebuild, not the device.
        screen._audio_enabled = True
        screen.render(surface)
        mini = screen._strip_mini_rect(1280, 800)
        early = song.notes[0]
        from pickhero.ui import strip as strip_mod
        x, y = strip_mod.dot(early, song.duration_ms, mini.width, mini.height)
        got = tuple(surface.get_at((mini.x + x + 1, mini.y + y))[:3])
        assert got == get_theme().feedback_hit


    def test_the_board_shows_them_too_after_a_seek(self, display):
        # The board's verdict colour used to live in the feedback effects,
        # which are animations and are cleared on every seek -- so scrubbing
        # back found the board in plain string colours while the sheet and
        # the strip still carried the run. Caught by looking at a render,
        # not by a test, which is why this one exists.
        from pickhero.ui.feedback import FeedbackRenderer
        song = _song()
        screen = _screen(song)
        note = song.notes[0]
        screen._matcher._record_match(note, MatchType.HIT)
        screen.seek(0.0)                     # clears the feedback effects
        assert not screen._feedback._effects
        colour = FeedbackRenderer().get_note_color(
            note, (1, 2, 3), 0.0, True, get_theme().feedback_hit)
        assert colour != (1, 2, 3)
        # Green-dominant, so it still reads as the verdict it was.
        assert colour[1] == max(colour)


class TestMarkingAPassageWithTheMouse:

    def _mini(self, screen, surface):
        screen.render(surface)
        return screen._strip_mini_rect(1280, 800)

    def _drag(self, screen, mini, a, b):
        for kind, frac in ((pygame.MOUSEBUTTONDOWN, a),
                           (pygame.MOUSEMOTION, b),
                           (pygame.MOUSEBUTTONUP, b)):
            pos = (mini.x + int(mini.width * frac), mini.centery)
            event = (pygame.event.Event(kind, pos=pos)
                     if kind == pygame.MOUSEMOTION
                     else pygame.event.Event(kind, button=3, pos=pos))
            screen.handle_event(event)

    def test_a_right_drag_sets_the_loop_and_goes_there(self, display):
        song = _song()
        screen, surface = _screen(song), pygame.Surface((1280, 800))
        mini = self._mini(screen, surface)
        self._drag(screen, mini, 0.25, 0.5)
        duration = song.duration_ms
        assert screen._loop_enabled
        assert screen._loop_start_ms == pytest.approx(duration * 0.25,
                                                      abs=BAR_MS / 2)
        assert screen._loop_end_ms == pytest.approx(duration * 0.5,
                                                    abs=BAR_MS / 2)
        assert screen._playback_ms == pytest.approx(screen._loop_start_ms,
                                                    abs=1.0)

    def test_and_does_not_start_playing(self, display):
        # "Loop setzen, hinspringen, warten." The first seconds of a passage
        # played while the hand is still on the mouse score as missed.
        song = _song()
        screen, surface = _screen(song), pygame.Surface((1280, 800))
        mini = self._mini(screen, surface)
        self._drag(screen, mini, 0.25, 0.5)
        assert not screen.is_playing()

    def test_it_stops_a_song_that_was_running(self, display):
        song = _song()
        screen, surface = _screen(song), pygame.Surface((1280, 800))
        mini = self._mini(screen, surface)
        screen._playing = True
        self._drag(screen, mini, 0.25, 0.5)
        assert not screen.is_playing()

    def test_the_left_button_still_spools(self, display):
        song = _song()
        screen, surface = _screen(song), pygame.Surface((1280, 800))
        mini = self._mini(screen, surface)
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1,
            pos=(mini.x + int(mini.width * 0.6), mini.centery)))
        assert screen._loop_start_ms is None
        assert screen._playback_ms == pytest.approx(song.duration_ms * 0.6,
                                                    abs=BAR_MS)

    def test_a_right_click_without_a_drag_marks_nothing(self, display):
        # The right button is also how a mouse gets put down.
        song = _song()
        screen, surface = _screen(song), pygame.Surface((1280, 800))
        mini = self._mini(screen, surface)
        pos = (mini.centerx, mini.centery)
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=3, pos=pos))
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONUP, button=3, pos=pos))
        assert screen._loop_start_ms is None


class TestSpoolingIsNotPlayingBadly:
    """*Spooling to the last bar was banked as a run, at 9.2 %.*

    Measured on the player's own files: 91 seeks, `clock_song_s 44.6` over a
    208 s song, `played_to_the_end True` -- and 1331 of 1475 notes marked
    MISS, because the missed-note sweep marks everything behind the playhead.
    That stood in `progress.json` as an attempt beside two real passes at
    90 %. Music a seek skipped was never in front of the player.
    """

    def _screen(self, tmp_path):
        song = _song(bars=20, per_bar=4)
        tab = tmp_path / "song.gp5"
        tab.write_text("x")
        screen = PlayingScreen(song, config=Config(), song_key="song",
                               song_path=str(tab))
        screen._matcher = NoteMatcher(song)
        screen._audio_enabled = True
        return screen, song

    def test_notes_a_seek_jumped_over_stay_unreached(self, tmp_path):
        screen, song = self._screen(tmp_path)
        screen.seek(song.duration_ms)
        screen._matcher.process_detected_notes([], song.duration_ms)
        states = [screen._matcher.get_note_state(n) for n in song.notes]
        assert all(s is MatchType.PENDING for s in states)
        assert screen._matcher.get_statistics()["total"] == 0

    def test_and_a_note_the_playhead_really_passed_is_still_missed(self, tmp_path):
        screen, song = self._screen(tmp_path)
        screen._matcher.process_detected_notes([], 3 * BAR_MS)
        assert screen._matcher.get_statistics()["misses"] > 0

    def test_a_seek_BACK_still_lets_the_sweep_judge_again(self, tmp_path):
        screen, song = self._screen(tmp_path)
        screen._matcher.process_detected_notes([], 3 * BAR_MS)
        before = screen._matcher.get_statistics()["misses"]
        screen.seek(0.0)
        screen._matcher.forget_from(0.0)
        screen._matcher.process_detected_notes([], 3 * BAR_MS)
        assert screen._matcher.get_statistics()["misses"] == before

    def test_so_spooling_to_the_end_banks_nothing(self, tmp_path):
        screen, song = self._screen(tmp_path)
        screen.seek(song.duration_ms)
        screen._matcher.process_detected_notes([], song.duration_ms)
        assert screen.unbanked_run() is None
