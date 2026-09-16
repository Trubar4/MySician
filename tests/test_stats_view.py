"""Comparing several runs of one song.

*"Ich brauche eine Liste aller alten Durchgaenge ... ich muss zwei
Durchgaenge anklicken koennen, die dann verglichen werden koennen ... mit +/-
kann ich die Leiste groesser oder kleiner machen."*

The arithmetic is in `runs.py` and tested on strings there. What is tested
here is the list it becomes and the way in and out of it -- including the two
things that have to be true whatever the drawing does: the overlay owns the
keyboard while it is up, and marking a passage in it does not start playing.
"""

import pygame
import pytest

from pickhero import runs
from pickhero.config import Config
from pickhero.matcher import MatchType, NoteMatcher
from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)
from pickhero.ui import stats_view, strip
from pickhero.ui.scrolling import PlayingScreen

BAR_MS = 2000.0


@pytest.fixture
def display():
    pygame.init()
    pygame.display.set_mode((1280, 800))
    yield
    pygame.display.quit()


def _song(bars=12, per_bar=4):
    notes, measures = [], []
    for bar in range(bars):
        measures.append(MeasureInfo(index=bar, start_ms=bar * BAR_MS,
                                    end_ms=(bar + 1) * BAR_MS))
        for beat in range(per_bar):
            notes.append(NoteEvent(
                timestamp_ms=bar * BAR_MS + beat * (BAR_MS / per_bar),
                duration_ms=300.0, midi_note=40 + beat,
                string=1 + beat % 6, fret=beat, measure=bar))
    return Timeline(notes, SongMetadata(title="t", tempo=120),
                    measures=measures)


def _screen(song, tmp_path=None):
    path = ""
    if tmp_path is not None:
        tab = tmp_path / "song.gp5"
        tab.write_text("x")
        path = str(tab)
    screen = PlayingScreen(song, config=Config(), song_key="song",
                           song_path=path)
    screen._matcher = NoteMatcher(song)
    screen._audio_enabled = True
    screen.set_track_options([(0, "Guitar")], 0)
    return screen


def _run(notes, started):
    return runs.make(notes, 60.0, 100, 0, started=started)


def _key(key, mod=0):
    return pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod,
                              unicode="", scancode=0)


# -- the list ---------------------------------------------------------------

class TestTheList:

    def test_the_two_runs_nobody_played_come_first(self):
        history = [_run("mh" * 4, "2026-09-01T10:00:00+00:00"),
                   _run("mh" * 4, "2026-09-02T10:00:00+00:00"),
                   _run("mh" * 4, "2026-09-03T10:00:00+00:00")]
        rows = stats_view.build(history, 8, 0)
        assert [r.run.kind for r in rows[:2]] == ["best", "errors"]

    def test_and_stay_first_when_the_sort_changes(self):
        # Sorting them among the evenings would say they were evenings.
        history = [_run("hh", "2026-09-01T10:00:00+00:00"),
                   _run("mm", "2026-09-02T10:00:00+00:00")]
        for sort in ("date", "score"):
            rows = stats_view.build(history, 2, 0, sort)
            assert rows[0].run.kind == "best"

    def test_newest_first_by_default(self):
        history = [_run("hh", "2026-09-01T10:00:00+00:00"),
                   _run("mm", "2026-09-05T10:00:00+00:00")]
        rows = [r for r in stats_view.build(history, 2, 0) if r.run.kind == "run"]
        assert rows[0].run.started.startswith("2026-09-05")

    def test_best_first_when_sorted_by_score(self):
        history = [_run("mm", "2026-09-05T10:00:00+00:00"),
                   _run("hh", "2026-09-01T10:00:00+00:00")]
        rows = [r for r in stats_view.build(history, 2, 0, "score")
                if r.run.kind == "run"]
        assert rows[0].run.notes == "hh"

    def test_a_run_of_another_track_is_listed_but_not_drawn(self):
        # It happened, so it is in the list; its verdicts describe notes that
        # are not these notes, so nothing may be drawn against them.
        other = runs.make("hh", 60.0, 100, track=3,
                          started="2026-09-01T10:00:00+00:00")
        rows = stats_view.build([other], 2, 0)
        assert len(rows) == 1
        assert not rows[0].comparable
        assert "another track" in rows[0].detail

    def test_it_does_not_vote_in_the_synthetics_either(self):
        good = _run("hh", "2026-09-01T10:00:00+00:00")
        also = _run("hh", "2026-09-02T10:00:00+00:00")
        other = runs.make("mm", 60.0, 100, track=3,
                          started="2026-09-03T10:00:00+00:00")
        rows = stats_view.build([good, also, other], 2, 0)
        best = next(r for r in rows if r.run.kind == "best")
        assert "2 runs" in best.detail


# -- the geometry -----------------------------------------------------------

class TestTheGeometry:

    def test_the_cursor_is_always_on_screen(self):
        assert stats_view.scrolled(9, 0, 5) == 5
        assert stats_view.scrolled(0, 5, 5) == 0
        assert stats_view.scrolled(3, 0, 5) == 0

    def test_the_ladder_starts_at_the_strip_and_ends_at_the_room(self):
        ladder = stats_view.size_ladder(strip.STRIP_HEIGHT, 400)
        assert ladder[0] == strip.STRIP_HEIGHT
        assert ladder[-1] == 400
        assert ladder == sorted(ladder)

    def test_a_window_with_no_room_still_offers_one_size(self):
        assert stats_view.size_ladder(37, 10) == [37]

    def test_a_bigger_bar_spreads_the_strings_further_apart(self):
        # The margin ROW_SPREAD leaves is what makes the strip's playhead
        # legible; at four times the size it is just air.
        near = strip.row_y(1, 400, strip.row_spread_for(400))
        far = strip.row_y(6, 400, strip.row_spread_for(400))
        plain = strip.row_y(6, 400) - strip.row_y(1, 400)
        assert far - near > plain
        assert strip.row_spread_for(strip.STRIP_HEIGHT) == strip.ROW_SPREAD


# -- what the numbers say ---------------------------------------------------

class TestWhatIsSaid:

    def test_the_errors_row_counts_notes_rather_than_claiming_nought_percent(
            self, display):
        screen = _screen(_song())
        overlay = screen._stats
        errors = runs.common_errors([_run("mm", "2026-09-01T10:00:00+00:00"),
                                     _run("mm", "2026-09-02T10:00:00+00:00")])
        # Every note in it is a mistake by construction, so "0 %" is three
        # numbers all saying the same nothing.
        assert overlay._numbers(errors) == "2 notes"

    def test_a_line_wider_than_its_column_is_cut_rather_than_run_over(
            self, display):
        screen = _screen(_song())
        font = screen._stats._font("arial", 12)
        long = "wrong in over half of 6 runs, and still wrong last time"
        cut = screen._stats.fit(font, long, 80)
        assert cut.endswith("…") and font.size(cut)[0] <= 80

    def test_a_line_that_fits_is_left_exactly_alone(self, display):
        screen = _screen(_song())
        font = screen._stats._font("arial", 12)
        assert screen._stats.fit(font, "short", 400) == "short"


# -- the way in and out -----------------------------------------------------

class TestTheWayInAndOut:

    def test_shift_d_opens_it_and_stops_the_clock(self, display):
        screen = _screen(_song())
        screen.toggle_play()
        screen._audio_enabled = True      # no sound card here; not the point
        assert screen._playing
        screen.handle_event(_key(pygame.K_d, pygame.KMOD_LSHIFT))
        assert screen._stats.open
        # The overlay covers the music: a song left running behind it is a
        # run being scored through a screen nobody can see.
        assert not screen._playing

    def test_plain_d_still_writes_the_run_log(self, display, tmp_path):
        screen = _screen(_song())
        screen.handle_event(_key(pygame.K_d))
        assert not screen._stats.open

    def test_the_same_key_closes_it(self, display):
        screen = _screen(_song())
        screen.handle_event(_key(pygame.K_d, pygame.KMOD_LSHIFT))
        screen.handle_event(_key(pygame.K_d, pygame.KMOD_LSHIFT))
        assert not screen._stats.open

    def test_while_it_is_up_it_owns_the_keyboard(self, display):
        # S opens the sync panel in the song. Inside the overlay it sorts,
        # and nothing underneath may hear it -- the same rule the track
        # picker already follows.
        screen = _screen(_song())
        was = screen._show_sync
        screen._stats.show()
        screen.handle_event(_key(pygame.K_s))
        assert screen._show_sync is was
        assert screen._stats.sort == "score"

    def test_escape_closes_the_overlay_and_not_the_song(self, display):
        screen = _screen(_song())
        screen._stats.show()
        assert screen.handle_event(_key(pygame.K_ESCAPE)) is None
        assert not screen._stats.open


# -- picking two ------------------------------------------------------------

class TestPickingTwo:

    def _with_history(self, tmp_path, display_ok=True):
        song = _song()
        screen = _screen(song, tmp_path)
        for day in (1, 2, 3):
            runs.append(screen._song_path,
                        _run("h" * len(song.notes),
                             f"2026-09-0{day}T10:00:00+00:00"))
        screen._stats.show()
        return screen

    def test_clicking_two_rows_stacks_them(self, display, tmp_path):
        screen = self._with_history(tmp_path)
        overlay = screen._stats
        screen.render(pygame.display.get_surface())   # rows have to exist
        assert overlay._rows
        for _, rect in overlay._rows[:2]:
            screen.handle_event(pygame.event.Event(
                pygame.MOUSEBUTTONDOWN, button=1, pos=rect.center))
        assert overlay.mode == "compare"
        assert len(overlay.selected) == 2

    def test_a_third_pick_drops_the_first(self, display, tmp_path):
        screen = self._with_history(tmp_path)
        overlay = screen._stats
        for index in (0, 1, 2):
            overlay._pick(index)
        assert overlay.selected == [1, 2]

    def test_picking_the_same_row_again_lets_it_go(self, display, tmp_path):
        overlay = self._with_history(tmp_path)._stats
        overlay._pick(0)
        overlay._pick(0)
        assert overlay.selected == []

    def test_a_run_of_another_track_cannot_be_picked(self, display, tmp_path):
        screen = _screen(_song(), tmp_path)
        runs.append(screen._song_path,
                    runs.make("hh", 60.0, 100, track=7,
                              started="2026-09-01T10:00:00+00:00"))
        screen._stats.show()
        screen._stats._pick(0)
        assert screen._stats.selected == []

    def test_plus_and_minus_walk_the_sizes(self, display, tmp_path):
        overlay = self._with_history(tmp_path)._stats
        overlay._pick(0)
        overlay._pick(1)
        overlay._compare()
        overlay.size = 0
        overlay.handle_event(_key(pygame.K_PLUS))
        assert overlay.size == 1
        overlay.handle_event(_key(pygame.K_MINUS))
        overlay.handle_event(_key(pygame.K_MINUS))
        assert overlay.size == 0          # an end of the list is a sentence


# -- marking a passage ------------------------------------------------------

class TestMarkingAPassage:

    def test_a_right_drag_sets_the_loop_goes_there_and_waits(
            self, display, tmp_path):
        song = _song()
        screen = _screen(song, tmp_path)
        for day in (1, 2):
            runs.append(screen._song_path,
                        _run("h" * len(song.notes),
                             f"2026-09-0{day}T10:00:00+00:00"))
        screen._stats.show()
        overlay = screen._stats
        overlay._pick(0)
        overlay._pick(1)
        overlay._compare()
        screen.render(pygame.display.get_surface())
        assert overlay._bar_rects
        rect = overlay._bar_rects[0][1]
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=3,
            pos=(rect.x + rect.width // 4, rect.centery)))
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEMOTION, pos=(rect.x + rect.width // 2, rect.centery)))
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONUP, button=3,
            pos=(rect.x + rect.width // 2, rect.centery)))
        assert screen._loop_enabled
        assert screen._loop_start_ms is not None
        # "Loop setzen, hinspringen, warten": it must NOT start playing.
        assert not screen._playing
        assert abs(screen._playback_ms - screen._loop_start_ms) < 1.0
        assert not overlay.open

    def test_a_right_click_is_not_a_passage(self, display, tmp_path):
        # The right button is also how a mouse gets put down.
        song = _song()
        screen = _screen(song, tmp_path)
        for day in (1, 2):
            runs.append(screen._song_path,
                        _run("h" * len(song.notes),
                             f"2026-09-0{day}T10:00:00+00:00"))
        screen._stats.show()
        overlay = screen._stats
        overlay._pick(0)
        overlay._pick(1)
        overlay._compare()
        screen.render(pygame.display.get_surface())
        rect = overlay._bar_rects[0][1]
        for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            screen.handle_event(pygame.event.Event(kind, button=3,
                                                   pos=rect.center))
        assert not screen._loop_enabled
        assert overlay.open


# -- the run that gets written ---------------------------------------------

class TestKeepingARun:

    def test_leaving_the_song_writes_it_beside_the_tab(self, display,
                                                       tmp_path):
        song = _song()
        screen = _screen(song, tmp_path)
        for note in song.notes[:5]:
            screen._matcher._record_match(note, MatchType.HIT, proved=True)
        screen.stop_audio()
        kept = runs.load(screen._song_path)
        assert len(kept) == 1
        assert kept[0].notes.startswith("hhhhh")
        assert kept[0].note_count == len(song.notes)

    def test_a_string_credited_to_a_strum_is_written_in_capitals(
            self, display, tmp_path):
        song = _song()
        screen = _screen(song, tmp_path)
        screen._matcher._record_match(song.notes[0], MatchType.HIT,
                                      proved=False)
        screen.stop_audio()
        assert runs.load(screen._song_path)[0].notes[0] == "H"

    def test_leaving_twice_writes_one_run(self, display, tmp_path):
        # stop_audio is reached more than once on the way out -- the screen
        # is torn down and the state change calls it again. The run log has
        # already been written twice for exactly this.
        song = _song()
        screen = _screen(song, tmp_path)
        screen._matcher._record_match(song.notes[0], MatchType.HIT)
        screen.stop_audio()
        screen.close_session()
        assert len(runs.load(screen._song_path)) == 1

    def test_a_song_opened_and_left_writes_nothing(self, display, tmp_path):
        screen = _screen(_song(), tmp_path)
        screen.stop_audio()
        assert runs.load(screen._song_path) == []

    def test_the_run_in_progress_is_in_the_list_before_it_is_saved(
            self, display, tmp_path):
        # It is written when the song is LEFT, so without this the list
        # answers "how did that go" a song later than it was asked.
        song = _song()
        screen = _screen(song, tmp_path)
        for note in song.notes[:5]:
            screen._matcher._record_match(note, MatchType.HIT)
        screen._stats.show()
        assert any("not saved yet" in e.detail for e in screen._stats._entries)


# -- it is drawn at all -----------------------------------------------------

class TestItIsOnScreen:

    @pytest.mark.parametrize("view", ["standard", "hybrid", "tab"])
    def test_every_view_draws_it(self, display, tmp_path, view):
        # Three views and three returns in render(): three copies of one call
        # is how a view quietly ends up without it.
        song = _song()
        screen = _screen(song, tmp_path)
        runs.append(screen._song_path,
                    _run("h" * len(song.notes), "2026-09-01T10:00:00+00:00"))
        screen._set_view(view)
        surface = pygame.display.get_surface()
        screen._stats.show()
        screen.render(surface)
        # The panel's own ground, top left of the window, over whatever the
        # view had drawn there.
        assert screen._stats._rows

    def test_the_button_top_right_opens_it(self, display, tmp_path):
        screen = _screen(_song(), tmp_path)
        screen.render(pygame.display.get_surface())
        assert screen._stats_button is not None
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1,
            pos=screen._stats_button.center))
        assert screen._stats.open


# -- the file is a belonging of the tab -------------------------------------

class TestItTravelsWithTheTab:

    def test_deleting_the_song_takes_its_runs(self, tmp_path):
        from pickhero.tabs.remove import belongings, delete_song
        tab = tmp_path / "song.gp5"
        tab.write_text("x")
        runs.append(tab, _run("hh", "2026-09-01T10:00:00+00:00"))
        assert runs.path_for(tab) in belongings(tab)
        delete_song(tab)
        assert not runs.path_for(tab).exists()

    def test_renaming_the_song_carries_them(self, tmp_path):
        from pickhero.tabs.remove import rename_song
        tab = tmp_path / "old.gp5"
        tab.write_text("x")
        runs.append(tab, _run("hh", "2026-09-01T10:00:00+00:00"))
        out = rename_song(tab, "new")
        assert not out.failed
        assert runs.load(tmp_path / "new.gp5")[0].notes == "hh"

    def test_importing_a_song_brings_its_runs_with_it(self, tmp_path):
        # `belongings` is the one reader, so this is free -- and it is the
        # reason the list is not empty on the second laptop.
        from pickhero.transfer import Report, import_songs
        stick = tmp_path / "stick"
        stick.mkdir()
        here = tmp_path / "songs"
        here.mkdir()
        tab = stick / "song.gp5"
        tab.write_text("x")
        runs.append(tab, _run("hcm", "2026-09-01T10:00:00+00:00"))
        import_songs(stick, here, Report())
        assert runs.load(here / "song.gp5")[0].notes == "hcm"


# -- moving through the comparison ------------------------------------------

class TestZoomAndScroll:

    def _compare(self, tmp_path, bars=12):
        song = _song(bars=bars)
        screen = _screen(song, tmp_path)
        for day in (1, 2):
            runs.append(screen._song_path,
                        _run("h" * len(song.notes),
                             f"2026-09-0{day}T10:00:00+00:00"))
        screen._stats.show()
        screen._stats._pick(0)
        screen._stats._pick(1)
        screen._stats._compare()
        return screen

    def test_it_opens_on_the_whole_song(self, display, tmp_path):
        # "Where did it go wrong" is asked before "which note", and only the
        # whole picture answers the first.
        overlay = self._compare(tmp_path)._stats
        assert overlay.zoom == 0
        start, end = overlay.window()
        assert (start, end) == (0.0, overlay._screen._timeline.duration_ms)

    def test_zooming_keeps_the_middle_where_it_is(self, display, tmp_path):
        overlay = self._compare(tmp_path)._stats
        overlay.view_from_ms = 8000.0
        overlay.set_zoom(1)
        before = sum(overlay.window()) / 2
        overlay.set_zoom(3)
        assert abs(sum(overlay.window()) / 2 - before) < 1.0

    def test_the_arrows_move_along_the_song(self, display, tmp_path):
        screen = self._compare(tmp_path)
        overlay = screen._stats
        overlay.set_zoom(2)
        was = overlay.window()[0]
        screen.handle_event(_key(pygame.K_RIGHT))
        assert overlay.window()[0] > was
        screen.handle_event(_key(pygame.K_LEFT))
        assert abs(overlay.window()[0] - was) < 1.0

    def test_it_cannot_be_scrolled_off_either_end(self, display, tmp_path):
        overlay = self._compare(tmp_path)._stats
        overlay.set_zoom(2)
        duration = overlay._screen._timeline.duration_ms
        for _ in range(40):
            overlay.scroll(+1)
        assert overlay.window()[1] <= duration + 1.0
        for _ in range(80):
            overlay.scroll(-1)
        assert overlay.window()[0] >= -1.0

    def test_the_zoom_has_ends_and_they_are_sentences(self, display, tmp_path):
        overlay = self._compare(tmp_path)._stats
        for _ in range(20):
            overlay.set_zoom(overlay.zoom + 1)
        assert overlay.zoom == stats_view.ZOOM_STEPS - 1
        for _ in range(20):
            overlay.set_zoom(overlay.zoom - 1)
        assert overlay.zoom == 0

    def test_a_note_outside_the_window_is_not_placed(self, display, tmp_path):
        # Clamping one instead would pile every note before the view onto the
        # left edge, which reads as a chord nobody played.
        overlay = self._compare(tmp_path)._stats
        spots = overlay._note_positions(600, 120, 4000.0, 8000.0)
        notes = overlay._screen._timeline.notes
        for note, spot in zip(notes, spots):
            inside = 4000.0 <= note.timestamp_ms <= 8000.0
            assert (spot is not None) == inside

    def test_a_right_drag_lands_where_the_picture_says(self, display,
                                                      tmp_path):
        screen = self._compare(tmp_path)
        overlay = screen._stats
        overlay.set_zoom(3)
        start, end = overlay.window()
        screen.render(pygame.display.get_surface())
        rect = overlay._bar_rects[0][1]
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=3,
            pos=(rect.x + rect.width // 4, rect.centery)))
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEMOTION,
            pos=(rect.x + 3 * rect.width // 4, rect.centery)))
        screen.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONUP, button=3,
            pos=(rect.x + 3 * rect.width // 4, rect.centery)))
        # Inside the stretch being looked at, not somewhere near the start of
        # the song -- which is what reading x against the whole song gives.
        assert start <= screen._loop_start_ms <= end
        assert start <= screen._loop_end_ms <= end
        assert screen._loop_start_ms > start + (end - start) * 0.1

    def test_the_dot_grows_when_there_is_room_for_it(self, display, tmp_path):
        overlay = self._compare(tmp_path, bars=60)._stats
        duration = overlay._screen._timeline.duration_ms
        whole = overlay.dot_size(
            overlay._note_positions(900, 200, 0.0, duration), 200)
        near = overlay.dot_size(
            overlay._note_positions(900, 200, 0.0, duration / 32), 200)
        assert near > whole

    def test_the_rows_never_merge_however_much_room_there_is(self, display,
                                                            tmp_path):
        # The vertical limit still binds: a list row is 37 px and six rows in
        # it sit about four pixels apart.
        overlay = self._compare(tmp_path)._stats
        spots = overlay._note_positions(900, strip.STRIP_HEIGHT, 0.0, 500.0)
        pitch = strip.STRIP_HEIGHT * strip.ROW_SPREAD / strip.STRINGS
        assert overlay.dot_size(spots, strip.STRIP_HEIGHT) <= pitch

    def test_the_list_always_shows_the_whole_song(self, display, tmp_path):
        # A row is how the runs are told apart; two rows showing different
        # stretches would be a comparison nobody asked for.
        screen = self._compare(tmp_path)
        overlay = screen._stats
        overlay.set_zoom(3)
        overlay.mode = "list"
        screen.render(pygame.display.get_surface())
        whole = (0.0, screen._timeline.duration_ms)
        assert any(key[3] == round(whole[0]) and key[4] == round(whole[1])
                   for key in overlay._bars)


# -- the two runs nobody played are ordinary rows ---------------------------

class TestThePretendRuns:

    def test_best_ever_can_be_picked_like_any_other(self, display, tmp_path):
        song = _song()
        screen = _screen(song, tmp_path)
        for day in (1, 2):
            runs.append(screen._song_path,
                        _run("h" * len(song.notes),
                             f"2026-09-0{day}T10:00:00+00:00"))
        screen._stats.show()
        overlay = screen._stats
        assert overlay._entries[0].run.kind == "best"
        overlay._pick(0)
        assert overlay.selected == [0]

    def test_frequent_errors_can_be_picked_too(self, display, tmp_path):
        song = _song()
        screen = _screen(song, tmp_path)
        wrong = "m" + "h" * (len(song.notes) - 1)
        for day in (1, 2, 3):
            runs.append(screen._song_path,
                        _run(wrong, f"2026-09-0{day}T10:00:00+00:00"))
        screen._stats.show()
        overlay = screen._stats
        kinds = [e.run.kind for e in overlay._entries[:2]]
        assert kinds == ["best", "errors"]
        overlay._pick(1)
        assert overlay.selected == [1]
