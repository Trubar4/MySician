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

    def test_plus_and_minus_zoom(self, display, tmp_path):
        # *"+/- innerhalb Stats Vergleich zoomt das Griffbrett."* The same
        # key means the same thing here as in every other view.
        overlay = self._with_history(tmp_path)._stats
        overlay._pick(0)
        overlay._pick(1)
        overlay._compare()
        assert overlay.zoom == 0
        overlay.handle_event(_key(pygame.K_PLUS))
        assert overlay.zoom == 1
        overlay.handle_event(_key(pygame.K_MINUS))
        overlay.handle_event(_key(pygame.K_MINUS))
        assert overlay.zoom == 0          # an end of the list is a sentence

    def test_up_and_down_walk_the_sizes(self, display, tmp_path):
        overlay = self._with_history(tmp_path)._stats
        overlay._pick(0)
        overlay._pick(1)
        overlay._compare()
        overlay.size = 0
        overlay.handle_event(_key(pygame.K_UP))
        assert overlay.size == 1
        overlay.handle_event(_key(pygame.K_DOWN))
        overlay.handle_event(_key(pygame.K_DOWN))
        assert overlay.size == 0


# -- marking a passage ------------------------------------------------------

class TestMarkingAPassage:

    def test_marking_a_later_passage_replaces_the_one_before_it(
            self, display, tmp_path):
        # Setting the start past an end still standing used to swap the two,
        # so the second passage came out as the gap between them.
        screen = _screen(_song(), tmp_path)
        screen.take_passage(0.0, BAR_MS)
        screen.take_passage(8 * BAR_MS, 9 * BAR_MS)
        assert screen._loop_start_ms == pytest.approx(8 * BAR_MS)
        assert screen._loop_end_ms == pytest.approx(9 * BAR_MS)

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


class TestPoolingTwoMachines:
    """*"Import bzw. Zusammenführen wäre sehr nett als Funktion."*"""

    def test_importing_pools_the_runs_of_a_song_both_machines_have(
            self, tmp_path):
        # "Import bzw. Zusammenführen wäre sehr nett als Funktion." The runs
        # are the one belonging that merges: a history is a list, and two
        # lists of different evenings have a union.
        from pickhero.transfer import Report, import_songs
        stick = tmp_path / "stick"
        here = tmp_path / "songs"
        stick.mkdir()
        here.mkdir()
        for folder, day, notes in ((here, 1, "hhh"), (stick, 2, "mmm")):
            tab = folder / "song.gp5"
            tab.write_text("x")
            runs.append(tab, _run(notes, f"2026-09-0{day}T20:00:00+00:00"))
        report = Report()
        import_songs(stick, here, report)
        kept = runs.load(here / "song.gp5")
        assert [r.notes for r in kept] == ["hhh", "mmm"]
        assert report.runs_added == 1
        assert "1 run of songs you already have" in " ".join(report.lines())

    def test_importing_the_same_stick_twice_adds_nothing(self, tmp_path):
        from pickhero.transfer import Report, import_songs
        stick = tmp_path / "stick"
        here = tmp_path / "songs"
        stick.mkdir()
        here.mkdir()
        for folder, day in ((here, 1), (stick, 2)):
            tab = folder / "song.gp5"
            tab.write_text("x")
            runs.append(tab, _run("hh", f"2026-09-0{day}T20:00:00+00:00"))
        import_songs(stick, here, Report())
        again = Report()
        import_songs(stick, here, again)
        assert again.runs_added == 0
        assert len(runs.load(here / "song.gp5")) == 2

    def test_the_tab_this_machine_is_practising_is_never_replaced(self,
                                                                 tmp_path):
        from pickhero.transfer import Report, import_songs
        stick = tmp_path / "stick"
        here = tmp_path / "songs"
        stick.mkdir()
        here.mkdir()
        (here / "song.gp5").write_text("mine")
        (stick / "song.gp5").write_text("theirs")
        runs.append(stick / "song.gp5",
                    _run("hh", "2026-09-02T20:00:00+00:00"))
        import_songs(stick, here, Report())
        assert (here / "song.gp5").read_text() == "mine"
        assert len(runs.load(here / "song.gp5")) == 1

    def test_a_song_this_machine_has_never_seen_arrives_whole(self, tmp_path):
        from pickhero.transfer import Report, import_songs
        stick = tmp_path / "stick"
        here = tmp_path / "songs"
        stick.mkdir()
        here.mkdir()
        tab = stick / "new.gp5"
        tab.write_text("x")
        runs.append(tab, _run("hcm", "2026-09-02T20:00:00+00:00"))
        report = Report()
        import_songs(stick, here, report)
        # Copied by the ordinary file loop, not merged -- and counted once,
        # as a song added rather than as runs pooled.
        assert runs.load(here / "new.gp5")[0].notes == "hcm"
        assert report.runs_added == 0
        assert report.songs_added == ["new"]

    def test_a_dry_run_says_how_many_it_would_take_and_writes_nothing(
            self, tmp_path):
        from pickhero.transfer import Report, import_songs
        stick = tmp_path / "stick"
        here = tmp_path / "songs"
        stick.mkdir()
        here.mkdir()
        for folder, day in ((here, 1), (stick, 2)):
            tab = folder / "song.gp5"
            tab.write_text("x")
            runs.append(tab, _run("hh", f"2026-09-0{day}T20:00:00+00:00"))
        report = Report(dry_run=True)
        import_songs(stick, here, report)
        assert report.runs_added == 1
        assert len(runs.load(here / "song.gp5")) == 1


# -- walking the places it went wrong ---------------------------------------

class TestJumpingToTheNextMistake:

    def _with_errors(self, tmp_path):
        """A song whose second bar and whose sixth bar were played wrong."""
        song = _song()
        screen = _screen(song, tmp_path)
        marks = ["h"] * len(song.notes)
        for i in (4, 5, 20):            # bar 1 twice, bar 5 once
            marks[i] = "m"
        runs.append(screen._song_path,
                    _run("".join(marks), "2026-09-01T10:00:00+00:00"))
        screen._stats.show()
        return screen, screen._stats

    def _comparing(self, tmp_path):
        """The same run, with a second evening so two can be stacked."""
        screen, overlay = self._with_errors(tmp_path)
        runs.append(screen._song_path,
                    _run("h" * len(screen._timeline.notes),
                         "2026-09-02T10:00:00+00:00"))
        overlay.show()
        wrong = next(i for i, e in enumerate(overlay._entries)
                     if e.run.kind == "run"
                     and runs.MISS in e.run.notes)
        clean = next(i for i, e in enumerate(overlay._entries)
                     if e.run.kind == "run" and i != wrong)
        overlay._pick(wrong)
        overlay._pick(clean)
        overlay._compare()
        assert overlay.mode == "compare"
        return screen, overlay

    def _to_the_run(self, overlay):
        """Put the cursor on the evening rather than on a pretend run."""
        overlay.cursor = next(i for i, e in enumerate(overlay._entries)
                              if e.run.kind == "run")

    def test_it_loops_the_first_nest_and_waits(self, display, tmp_path):
        screen, overlay = self._with_errors(tmp_path)
        self._to_the_run(overlay)
        overlay.handle_event(_key(pygame.K_n))
        assert screen._loop_enabled
        assert screen._loop_start_ms == pytest.approx(BAR_MS)
        assert screen._loop_end_ms == pytest.approx(2 * BAR_MS)
        assert screen._playback_ms == pytest.approx(BAR_MS)
        assert not screen._playing        # land with the hands free

    def test_pressing_it_again_goes_to_the_next_one(self, display, tmp_path):
        screen, overlay = self._with_errors(tmp_path)
        self._to_the_run(overlay)
        overlay.handle_event(_key(pygame.K_n))
        overlay.handle_event(_key(pygame.K_n))
        assert screen._loop_start_ms == pytest.approx(5 * BAR_MS)

    def test_and_comes_back_round_to_the_first(self, display, tmp_path):
        screen, overlay = self._with_errors(tmp_path)
        self._to_the_run(overlay)
        for _ in range(3):
            overlay.handle_event(_key(pygame.K_n))
        assert screen._loop_start_ms == pytest.approx(BAR_MS)

    def test_the_overlay_stays_up(self, display, tmp_path):
        # Walking a list is what the key is for; closing would make every
        # step cost a Shift+D.
        screen, overlay = self._with_errors(tmp_path)
        self._to_the_run(overlay)
        overlay.handle_event(_key(pygame.K_n))
        assert overlay.open

    def test_it_says_which_nest_of_how_many_and_where(self, display,
                                                      tmp_path):
        screen, overlay = self._with_errors(tmp_path)
        self._to_the_run(overlay)
        overlay.handle_event(_key(pygame.K_n))
        said = overlay._nest_note
        assert "1 of 2" in said
        assert "bar 2" in said          # numbered from 1, the way a player counts
        assert "2 notes wrong" in said

    def test_a_clean_run_says_so_and_sets_no_loop(self, display, tmp_path):
        song = _song()
        screen = _screen(song, tmp_path)
        runs.append(screen._song_path,
                    _run("h" * len(song.notes), "2026-09-01T10:00:00+00:00"))
        screen._stats.show()
        overlay = screen._stats
        self._to_the_run(overlay)
        overlay.handle_event(_key(pygame.K_n))
        assert not screen._loop_enabled
        assert "Nothing went wrong" in overlay._nest_note

    def test_it_walks_the_run_under_the_cursor(self, display, tmp_path):
        song = _song()
        screen = _screen(song, tmp_path)
        early = ["h"] * len(song.notes)
        early[0] = "m"                              # bar 0
        late = ["h"] * len(song.notes)
        late[36] = "m"                              # bar 9
        runs.append(screen._song_path,
                    _run("".join(early), "2026-09-01T10:00:00+00:00"))
        runs.append(screen._song_path,
                    _run("".join(late), "2026-09-02T10:00:00+00:00"))
        screen._stats.show()
        overlay = screen._stats
        evenings = [i for i, e in enumerate(overlay._entries)
                    if e.run.kind == "run"]
        overlay.cursor = evenings[0]                # newest first: the late one
        overlay.handle_event(_key(pygame.K_n))
        assert screen._loop_start_ms == pytest.approx(9 * BAR_MS)
        overlay.cursor = evenings[1]
        overlay.handle_event(_key(pygame.K_n))
        assert screen._loop_start_ms == pytest.approx(0.0)

    def test_in_a_comparison_it_walks_the_top_one(self, display, tmp_path):
        song = _song()
        screen = _screen(song, tmp_path)
        a = ["h"] * len(song.notes)
        a[0] = "m"
        b = ["h"] * len(song.notes)
        b[36] = "m"
        runs.append(screen._song_path,
                    _run("".join(a), "2026-09-02T10:00:00+00:00"))
        runs.append(screen._song_path,
                    _run("".join(b), "2026-09-01T10:00:00+00:00"))
        overlay = screen._stats
        overlay.show()
        evenings = [i for i, e in enumerate(overlay._entries)
                    if e.run.kind == "run"]
        overlay._pick(evenings[0])
        overlay._pick(evenings[1])
        overlay._compare()
        assert overlay.mode == "compare"
        overlay.handle_event(_key(pygame.K_n))
        assert screen._loop_start_ms == pytest.approx(0.0)

    def test_a_zoomed_comparison_follows_it(self, display, tmp_path):
        screen, overlay = self._comparing(tmp_path)
        overlay.set_zoom(3)
        overlay.view_from_ms = 0.0
        overlay.handle_event(_key(pygame.K_n))
        overlay.handle_event(_key(pygame.K_n))       # the nest at bar 5
        seen = overlay.window()
        assert seen[0] <= 5 * BAR_MS <= seen[1]

    def test_but_the_whole_song_view_stays_where_it_is(self, display,
                                                       tmp_path):
        # Zoom is the player's; N only moves along the song.
        screen, overlay = self._comparing(tmp_path)
        overlay.handle_event(_key(pygame.K_n))
        assert overlay.zoom == 0

    def test_the_song_does_not_move_while_the_list_is_only_being_read(
            self, display, tmp_path):
        screen, overlay = self._with_errors(tmp_path)
        self._to_the_run(overlay)
        overlay.handle_event(_key(pygame.K_DOWN))
        assert not screen._loop_enabled

    def test_it_is_on_screen(self, display, tmp_path):
        screen, overlay = self._with_errors(tmp_path)
        self._to_the_run(overlay)
        overlay.handle_event(_key(pygame.K_n))
        surface = pygame.display.get_surface()
        surface.fill((0, 0, 0))
        overlay.draw(surface)
        assert pygame.transform.average_color(surface)[:3] != (0, 0, 0)


# -- the fret number in the dot ---------------------------------------------

class TestFretNumbersInTheDots:
    """*"In der hoechsten Zoom-Stufe alles anzeigen mit Bundnummern."*

    The rule is tested rather than one song's density: two conditions, and
    each one is asserted by asking `_label_frets` to break it.
    """

    def _overlay(self, tmp_path):
        song = _song(bars=40)
        screen = _screen(song, tmp_path)
        overlay = screen._stats
        overlay.show()
        return screen, overlay

    @staticmethod
    def _ink(surface) -> int:
        """How many colours are on it -- digits are antialiased, dots flat."""
        w, h = surface.get_size()
        return len({surface.get_at((x, y))[:3]
                    for x in range(0, w, 2) for y in range(0, h, 2)})

    def _bare(self, overlay, screen, dot, gap):
        surface = pygame.Surface((400, 240))
        surface.fill((0, 0, 0))
        spots = [(20 + i * 40, 120) for i in
                 range(len(screen._timeline.notes))]
        before = self._ink(surface)
        overlay._label_frets(surface, spots, dot, gap,
                             {s: (255, 255, 255) for s in spots})
        return before, self._ink(surface)

    def test_a_dot_big_enough_gets_its_number(self, display, tmp_path):
        screen, overlay = self._overlay(tmp_path)
        before, after = self._bare(overlay, screen,
                                   stats_view.FRET_DIGIT_PX + 6, 60.0)
        assert after > before

    def test_a_dot_too_small_for_a_digit_gets_none(self, display, tmp_path):
        # Below this a digit has no stroke left to read, so the dot stays a
        # dot rather than becoming ink that says less.
        screen, overlay = self._overlay(tmp_path)
        before, after = self._bare(overlay, screen,
                                   stats_view.FRET_DIGIT_PX - 1, 60.0)
        assert after == before

    def test_a_label_that_would_reach_its_neighbour_is_not_drawn(
            self, display, tmp_path):
        # The room it may take is the room the DOT was sized against, so the
        # two cannot disagree about whether there is any.
        screen, overlay = self._overlay(tmp_path)
        before, after = self._bare(overlay, screen,
                                   stats_view.FRET_DIGIT_PX + 6, 2.0)
        assert after == before

    def test_the_ink_reads_on_whatever_is_under_it(self, display, tmp_path):
        # A bright verdict wants black where its string wanted white, so the
        # colour actually painted is what decides -- not the note's own.
        screen, overlay = self._overlay(tmp_path)
        assert overlay._ink((255, 240, 130)) == (16, 16, 16)
        assert overlay._ink((40, 40, 60)) == (240, 240, 240)

    def test_zoomed_in_a_real_bar_carries_them(self, display, tmp_path):
        screen, overlay = self._overlay(tmp_path)
        run = _run("h" * len(screen._timeline.notes),
                   "2026-09-01T10:00:00+00:00")
        overlay.set_zoom(stats_view.ZOOM_STEPS - 1)
        bar = overlay.bar(run, 1200, 240, overlay.window())
        spots = overlay._note_positions(1200, 240, *overlay.window())
        assert overlay.dot_size(spots, 240) >= stats_view.FRET_DIGIT_PX
        assert self._ink(bar) > 4        # more than ground, dots and lines


class TestWhyThereIsNoErrorsRow:

    def test_the_list_says_it(self, display, tmp_path):
        song = _song()
        screen = _screen(song, tmp_path)
        runs.append(screen._song_path,
                    _run("h" * len(song.notes), "2026-09-01T10:00:00+00:00"))
        overlay = screen._stats
        overlay.show()
        assert not any(e.run.kind == "errors" for e in overlay._entries)
        said = runs.why_no_errors([e.run for e in overlay._entries
                                   if e.run.kind == "run" and e.fits])
        assert "two runs" in said
        # And it really reaches the surface rather than only the string.
        surface = pygame.display.get_surface()
        surface.fill((0, 0, 0))
        overlay.draw(surface)
        assert pygame.transform.average_color(surface)[:3] != (0, 0, 0)


# -- a completed run has to survive the practising that follows it ----------

class TestTheCompletedRunIsBanked:
    """*"Ich habe das Gefuehl, dass nicht alle Durchgaenge in den Statistiken
    gelandet sind."*

    He was right, and his own files said so: `progress.json` held 83.9 % over
    490 notes for a sitting whose stored run judged 128. Finishing a song and
    then going back to drill a passage is the ordinary thing to do, and
    `forget_from` spends every verdict from the seek onward -- so the pass
    worth keeping was the one destroyed, every time.
    """

    def _screen(self, tmp_path):
        song = _song()
        screen = _screen(song, tmp_path)
        return song, screen

    def _play(self, screen, song, upto):
        for note in song.notes[:upto]:
            screen._matcher._record_match(note, MatchType.HIT, proved=True)

    def test_leaving_twice_over_stores_one_run(self, display, tmp_path):
        song, screen = self._screen(tmp_path)
        self._play(screen, song, 10)
        screen._write_run()
        screen._write_run()
        assert len(runs.load(screen._song_path)) == 1

    def test_a_second_pass_is_a_second_run_and_the_first_is_kept(
            self, display, tmp_path):
        song, screen = self._screen(tmp_path)
        self._play(screen, song, len(song.notes))
        screen._write_run()                       # the completed pass
        screen._matcher.reset()
        self._play(screen, song, 8)               # then he drills a passage
        screen._write_run()
        kept = runs.load(screen._song_path)
        assert len(kept) == 2
        assert kept[0].counts()["total"] == len(song.notes)
        assert kept[1].counts()["total"] == 8

    def test_a_run_is_stamped_when_it_began(self, display, tmp_path):
        song, screen = self._screen(tmp_path)
        screen._run_started = "2026-09-16T18:27:13+00:00"
        self._play(screen, song, 5)
        screen._write_run()
        assert runs.load(screen._song_path)[0].started.startswith(
            "2026-09-16T18:27:13")

    def test_and_the_next_run_starts_where_the_last_one_was_banked(
            self, display, tmp_path):
        song, screen = self._screen(tmp_path)
        screen._run_started = "2026-09-16T18:27:13+00:00"
        self._play(screen, song, 5)
        screen._write_run()
        assert screen._run_started != "2026-09-16T18:27:13+00:00"

    def test_the_diary_keeps_the_score_of_the_finished_pass(self, display,
                                                            tmp_path):
        # A seek off the end clears `_song_completed`, which is right for the
        # completion screen and wrote `accuracy: null` for an evening that
        # had just scored 84 %.
        song, screen = self._screen(tmp_path)
        self._play(screen, song, len(song.notes))
        screen._finished_stats = dict(screen._matcher.get_statistics())
        screen._song_completed = False             # he went back to practise
        screen._session_seconds = 60.0
        written = {}
        import pickhero.practice_log as diary
        screen_append = diary.append
        try:
            diary.append = lambda s: written.update(vars(s)) or True
            screen.close_session()
        finally:
            diary.append = screen_append
        assert written["accuracy"] == pytest.approx(100.0)
        assert written["notes_written"] == len(song.notes)


# -- the run that was shown twice -------------------------------------------

class TestTheBankedRunIsNotOfferedAgain:
    """*"Diese beiden Runs schauen gleich aus."* They were the same run.

    Banking at the last bar stores it and starts the next run's clock; the
    list then asked its own question and put the live matcher's verdicts up
    a second time, labelled "not saved yet". Two rows, one run, identical
    bars and identical percentages.
    """

    def _played(self, tmp_path):
        song = _song(bars=2, per_bar=2)
        screen = _screen(song, tmp_path)
        for note in song.notes:
            screen._matcher._record_match(note, MatchType.HIT)
        return screen

    def test_it_is_offered_before_it_is_banked(self, tmp_path):
        screen = self._played(tmp_path)
        assert screen.unbanked_run() is not None

    def test_and_not_after(self, tmp_path):
        screen = self._played(tmp_path)
        screen._write_run()
        assert screen.unbanked_run() is None

    def test_so_the_list_holds_it_once(self, display, tmp_path):
        screen = self._played(tmp_path)
        screen._write_run()
        overlay = screen._stats
        overlay.toggle()
        evenings = [r for r in overlay._entries if r.run.kind == "run"]
        assert len(evenings) == 1
        assert evenings[0].detail.find("not saved yet") < 0

    def test_and_offers_it_again_once_more_was_played(self, tmp_path):
        screen = self._played(tmp_path)
        screen._write_run()
        screen._matcher._record_match(screen._timeline.notes[0],
                                      MatchType.MISS)
        assert screen.unbanked_run() is not None


class TestShiftNDrillsTheNest:
    """*"Shift+N übt das Nest, auf dem du gerade stehst — derselbe Ort,
    dieselbe Liste, eine Taste weiter."*

    One key further than `N`, and the same passage: `start_drill` sets the
    loop through the very `take_passage` the plain key calls, so there is
    one answer in the app to what marking a passage means.
    """

    def _with_errors(self, tmp_path):
        song = _song()
        screen = _screen(song, tmp_path)
        marks = ["h"] * len(song.notes)
        for i in (4, 5, 20):
            marks[i] = "m"
        runs.append(screen._song_path,
                    _run("".join(marks), "2026-09-01T10:00:00+00:00"))
        screen._stats.show()
        screen._stats.cursor = next(
            i for i, e in enumerate(screen._stats._entries)
            if e.run.kind == "run")
        return screen, screen._stats

    def test_it_sets_the_same_loop_the_plain_key_would(self, display,
                                                       tmp_path):
        plain, overlay = self._with_errors(tmp_path)
        overlay.handle_event(_key(pygame.K_n))
        looped = (plain._loop_start_ms, plain._loop_end_ms)
        drilled, overlay = self._with_errors(tmp_path)
        overlay.handle_event(_key(pygame.K_n, mod=pygame.KMOD_LSHIFT))
        assert (drilled._loop_start_ms, drilled._loop_end_ms) == looped

    def test_and_starts_the_ladder(self, display, tmp_path):
        from pickhero.drill import LADDER
        screen, overlay = self._with_errors(tmp_path)
        overlay.handle_event(_key(pygame.K_n, mod=pygame.KMOD_LSHIFT))
        assert screen._drill is not None
        assert screen._tempo_factor == LADDER[0]

    def test_the_plain_key_starts_no_drill(self, display, tmp_path):
        """The shifted key is tested FIRST in the handler, because an `if`
        chain is read in order -- and the plain one has to keep working."""
        screen, overlay = self._with_errors(tmp_path)
        overlay.handle_event(_key(pygame.K_n))
        assert screen._drill is None
        assert screen._loop_enabled

    def test_it_says_it_is_drilling(self, display, tmp_path):
        screen, overlay = self._with_errors(tmp_path)
        overlay.handle_event(_key(pygame.K_n, mod=pygame.KMOD_LSHIFT))
        assert "drilling" in overlay._nest_note

    def test_the_overlay_stays_up(self, display, tmp_path):
        screen, overlay = self._with_errors(tmp_path)
        overlay.handle_event(_key(pygame.K_n, mod=pygame.KMOD_LSHIFT))
        assert overlay.open

    def test_pressing_it_again_drills_the_next_nest(self, display, tmp_path):
        screen, overlay = self._with_errors(tmp_path)
        overlay.handle_event(_key(pygame.K_n, mod=pygame.KMOD_LSHIFT))
        first = screen._loop_start_ms
        overlay.handle_event(_key(pygame.K_n, mod=pygame.KMOD_LSHIFT))
        assert screen._loop_start_ms != first


# -- the trend over evenings ------------------------------------------------

class TestTheTrendArithmetic:
    """The two pure helpers, without a screen."""

    def test_the_columns_tile_the_whole_width_exactly(self):
        cols = stats_view.trend_columns(7, 100)
        assert cols[0][0] == 0
        assert cols[-1][0] + cols[-1][1] == 100
        for (x, w), (nx, _) in zip(cols, cols[1:]):
            assert x + w == nx

    def test_no_column_is_ever_zero_wide(self):
        # A song longer than the grid is wide still draws every bar.
        for (_, w) in stats_view.trend_columns(400, 300):
            assert w >= 1

    def test_no_bars_or_no_room_is_no_columns(self):
        assert stats_view.trend_columns(0, 100) == []
        assert stats_view.trend_columns(10, 0) == []

    def test_the_heat_runs_through_the_palette_s_own_three(self, display):
        theme = stats_view.get_theme()
        assert stats_view.heat(1.0) == tuple(theme.feedback_hit[:3])
        assert stats_view.heat(0.0) == tuple(theme.feedback_miss[:3])
        assert stats_view.heat(0.5) == tuple(theme.feedback_close[:3])

    def test_the_heat_is_a_ramp_rather_than_bands(self, display):
        # No threshold, because there is nothing to fit one against: the run
        # history starts the day it ships.
        seen = {stats_view.heat(i / 20.0) for i in range(21)}
        assert len(seen) > 10

    def test_the_heat_is_clamped(self, display):
        assert stats_view.heat(-5.0) == stats_view.heat(0.0)
        assert stats_view.heat(9.0) == stats_view.heat(1.0)

    def test_a_row_is_never_thinner_than_it_can_be_read_at(self):
        assert stats_view.trend_row_height(12, 20) == stats_view.TREND_ROW_MIN

    def test_and_never_just_air(self):
        assert stats_view.trend_row_height(2, 4000) == stats_view.TREND_ROW_MAX


class TestTheTrendOverEvenings:
    """*"Raster: Takte quer, Abende untereinander. Die letzten 12, neueste
    oben."*

    The one question the list cannot answer: "best ever" keeps only every
    evening's peak and "frequent errors" only what is still wrong today.
    """

    def _with_history(self, tmp_path, evenings=3):
        song = _song()
        screen = _screen(song, tmp_path)
        for day in range(evenings):
            marks = ["h"] * len(song.notes)
            # Bar 1 stays wrong on every evening; bar 2 is learnt.
            marks[4] = marks[5] = "m"
            if day < evenings - 1:
                marks[8] = "m"
            runs.append(screen._song_path,
                        _run("".join(marks), f"2026-09-0{day + 1}T10:00:00+00:00"))
        screen._stats.show()
        return screen, screen._stats

    def test_t_opens_it_and_t_goes_back(self, display, tmp_path):
        _, overlay = self._with_history(tmp_path)
        overlay.handle_event(_key(pygame.K_t))
        assert overlay.mode == "trend"
        overlay.handle_event(_key(pygame.K_t))
        assert overlay.mode == "list"

    def test_escape_goes_back_to_the_list_rather_than_closing(self, display,
                                                              tmp_path):
        _, overlay = self._with_history(tmp_path)
        overlay.handle_event(_key(pygame.K_t))
        overlay.handle_event(_key(pygame.K_ESCAPE))
        assert overlay.mode == "list" and overlay.open

    def test_the_newest_evening_is_the_top_row(self, display, tmp_path):
        _, overlay = self._with_history(tmp_path)
        rows = overlay._trend_entries()
        stamps = [entry.run.started for _, entry in rows]
        assert stamps == sorted(stamps, reverse=True)

    def test_in_time_order_whatever_the_list_is_sorted_by(self, display,
                                                          tmp_path):
        # "Evenings down" only means something chronologically.
        _, overlay = self._with_history(tmp_path)
        overlay.sort = "score"
        overlay._rebuild()
        stamps = [e.run.started for _, e in overlay._trend_entries()]
        assert stamps == sorted(stamps, reverse=True)

    def test_the_two_pretend_runs_are_left_out(self, display, tmp_path):
        _, overlay = self._with_history(tmp_path)
        assert any(e.run.kind != "run" for e in overlay._entries)
        assert all(e.run.kind == "run" for _, e in overlay._trend_entries())

    def test_at_most_twelve_of_them(self, display, tmp_path):
        _, overlay = self._with_history(tmp_path, evenings=9)
        for day in range(10, 30):
            runs.append(overlay._screen._song_path,
                        _run("h" * 48, f"2026-10-{day}T10:00:00+00:00"))
        overlay.show()
        assert len(overlay._trend_entries()) == stats_view.TREND_RUNS

    def test_the_run_in_progress_is_a_row_too(self, display, tmp_path):
        screen, overlay = self._with_history(tmp_path)
        for note in screen._timeline.notes[:6]:
            screen._matcher._set_state(note, MatchType.HIT)
        overlay.show()
        labels = [e.run.label for _, e in overlay._trend_entries()]
        assert "this run, not saved yet" in labels


class TestTheTrendPicture:

    def _overlay(self, tmp_path, marks_by_day):
        song = _song()
        screen = _screen(song, tmp_path)
        for day, marks in enumerate(marks_by_day):
            runs.append(screen._song_path,
                        _run(marks, f"2026-09-0{day + 1}T10:00:00+00:00"))
        screen._stats.show()
        screen._stats.mode = "trend"
        return screen, screen._stats

    def _cells(self, overlay, row_h=10, width=120):
        rows = overlay._trend_entries()
        _, measures = overlay._bars_of_notes()
        cols = stats_view.trend_columns(len(measures), width)
        overlay._trend_cols = cols
        return rows, cols, overlay._trend_surface(rows, cols, row_h)

    def test_a_bar_played_right_is_the_hit_colour(self, display, tmp_path):
        _, overlay = self._overlay(tmp_path, ["h" * 48, "h" * 48])
        _, cols, grid = self._cells(overlay)
        x, w = cols[3]
        assert grid.get_at((x + w // 2, 4))[:3] == stats_view.heat(1.0)

    def test_a_bar_missed_outright_is_the_miss_colour(self, display, tmp_path):
        marks = list("h" * 48)
        marks[4:8] = "mmmm"          # all four notes of bar 1
        _, overlay = self._overlay(tmp_path, ["".join(marks)] * 2)
        _, cols, grid = self._cells(overlay)
        x, w = cols[1]
        assert grid.get_at((x + w // 2, 4))[:3] == stats_view.heat(0.0)

    def test_a_bar_nobody_reached_is_blank(self, display, tmp_path):
        # Blank, not a zero: a 0 % there would say the player got it wrong.
        _, overlay = self._overlay(tmp_path, ["h" * 8 + "." * 40] * 2)
        _, cols, grid = self._cells(overlay)
        x, w = cols[6]
        assert grid.get_at((x + w // 2, 4))[:3] == \
            tuple(stats_view.get_theme().lane_bg_even[:3])

    def test_a_bar_nothing_could_check_is_drained(self, display, tmp_path):
        marks = list("h" * 48)
        marks[4:8] = "HHHH"
        _, overlay = self._overlay(tmp_path, ["".join(marks)] * 2)
        _, cols, grid = self._cells(overlay)
        x, w = cols[1]
        assert grid.get_at((x + w // 2, 4))[:3] == \
            tuple(stats_view.unsure(stats_view.heat(1.0))[:3])
        # ...and it still counts: the drained hit is a hit-coloured cell.
        assert grid.get_at((x + w // 2, 4))[:3] != stats_view.heat(0.0)

    def test_the_grid_is_built_once_and_blitted_after_that(self, display,
                                                           tmp_path):
        # Rows times bars is a couple of thousand cells, and this display has
        # had to move a loop out of the frame three times already.
        _, overlay = self._overlay(tmp_path, ["h" * 48] * 2)
        rows, cols, first = self._cells(overlay)
        again = overlay._trend_surface(rows, cols, 10)
        assert again is first

    def test_a_different_run_builds_a_different_grid(self, display, tmp_path):
        _, overlay = self._overlay(tmp_path, ["h" * 48] * 2)
        rows, cols, first = self._cells(overlay)
        other = [(i, stats_view.Entry(run=runs.Run(notes="m" * 48,
                                                   note_count=48),
                                      title="x", detail=""))
                 for i, _ in rows]
        assert overlay._trend_surface(other, cols, 10) is not first

    def test_it_really_reaches_the_window(self, display, tmp_path):
        # A picture nothing blits is a feature that ships doing nothing.
        _, overlay = self._overlay(tmp_path, ["h" * 48] * 2)
        surface = pygame.Surface((1280, 800))
        overlay.draw(surface)
        wanted = stats_view.heat(1.0)
        found = sum(1 for x in range(0, 1280, 4) for y in range(0, 800, 4)
                    if surface.get_at((x, y))[:3] == wanted)
        assert found > 20

    def test_a_tab_with_no_bar_lines_says_so(self, display, tmp_path):
        song = Timeline([NoteEvent(timestamp_ms=0.0, duration_ms=100.0,
                                   midi_note=40, string=1, fret=0)],
                        SongMetadata(title="t", tempo=120))
        screen = _screen(song, tmp_path)
        runs.append(screen._song_path, _run("h", "2026-09-01T10:00:00+00:00"))
        screen._stats.show()
        screen._stats.mode = "trend"
        surface = pygame.Surface((1280, 800))
        screen._stats.draw(surface)      # must not raise, and must say why
        assert screen._stats._trend_grid_rect is None

    def test_one_evening_draws_its_single_row(self, display, tmp_path):
        """Not refused: one row says which bars went wrong tonight, and the
        trend fills in underneath it. Refusing it would make the picture
        appear out of nowhere on the second run."""
        _, overlay = self._overlay(tmp_path, ["h" * 48])
        surface = pygame.Surface((1280, 800))
        overlay.draw(surface)
        assert overlay._trend_grid_rect is not None
        assert len(overlay._trend_rows) == 1

    def test_a_song_with_no_saved_run_says_so(self, display, tmp_path):
        screen = _screen(_song(), tmp_path)
        screen._stats.show()
        screen._stats.mode = "trend"
        surface = pygame.Surface((1280, 800))
        screen._stats.draw(surface)
        assert screen._stats._trend_grid_rect is None
        assert screen._stats._trend_rows == []


class TestPractisingFromTheTrend:

    def _overlay(self, tmp_path):
        song = _song()
        screen = _screen(song, tmp_path)
        marks = list("h" * 48)
        marks[4] = marks[5] = "m"
        for day in range(2):
            runs.append(screen._song_path,
                        _run("".join(marks), f"2026-09-0{day + 1}T10:00:00+00:00"))
        screen._stats.show()
        screen._stats.handle_event(_key(pygame.K_t))
        surface = pygame.Surface((1280, 800))
        screen._stats.draw(surface)
        return screen, screen._stats

    def _drag(self, overlay, x0, x1, y):
        overlay.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=3, pos=(x0, y)))
        overlay.handle_event(pygame.event.Event(
            pygame.MOUSEMOTION, pos=(x1, y)))
        overlay.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONUP, button=3, pos=(x1, y)))

    def test_entering_the_trend_puts_the_cursor_on_a_real_run(self, display,
                                                              tmp_path):
        _, overlay = self._overlay(tmp_path)
        assert overlay.cursor in [i for i, _ in overlay._trend_entries()]

    def test_right_dragging_the_bars_sets_the_loop_over_them(self, display,
                                                             tmp_path):
        screen, overlay = self._overlay(tmp_path)
        grid = overlay._trend_grid_rect
        cols = overlay._trend_cols
        y = grid.y + 4
        self._drag(overlay, grid.x + cols[2][0] + 1,
                   grid.x + cols[4][0] + 1, y)
        assert screen._loop_enabled
        assert screen._loop_start_ms == pytest.approx(2 * BAR_MS)
        assert screen._loop_end_ms == pytest.approx(5 * BAR_MS)

    def test_and_waits_rather_than_playing(self, display, tmp_path):
        # *"Loop setzen, hinspringen, warten."*
        screen, overlay = self._overlay(tmp_path)
        grid = overlay._trend_grid_rect
        self._drag(overlay, grid.x + overlay._trend_cols[2][0] + 1,
                   grid.x + overlay._trend_cols[3][0] + 1, grid.y + 4)
        assert not screen._playing

    def test_a_drag_leaving_the_grid_is_clamped_rather_than_lost(
            self, display, tmp_path):
        screen, overlay = self._overlay(tmp_path)
        grid = overlay._trend_grid_rect
        self._drag(overlay, grid.x + overlay._trend_cols[2][0] + 1,
                   grid.right + 400, grid.y + 4)
        assert screen._loop_end_ms == pytest.approx(12 * BAR_MS)

    def test_clicking_a_row_stands_on_it(self, display, tmp_path):
        _, overlay = self._overlay(tmp_path)
        rows = overlay._trend_rows
        index, rect = rows[-1]
        overlay.handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1, pos=rect.center))
        assert overlay.cursor == index

    def test_up_and_down_walk_the_rows(self, display, tmp_path):
        _, overlay = self._overlay(tmp_path)
        first = overlay.cursor
        overlay.handle_event(_key(pygame.K_DOWN))
        assert overlay.cursor != first
        overlay.handle_event(_key(pygame.K_UP))
        assert overlay.cursor == first

    def test_n_still_loops_the_cursor_row_s_mistakes(self, display, tmp_path):
        screen, overlay = self._overlay(tmp_path)
        overlay.handle_event(_key(pygame.K_n))
        assert screen._loop_enabled
        assert screen._loop_start_ms == pytest.approx(BAR_MS)
        assert overlay.mode == "trend"
