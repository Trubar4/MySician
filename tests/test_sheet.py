"""Laying the song out as a sheet instead of a moving belt.

The scrolling view cannot part two notes without speeding everything up,
because a note's x IS its time times a speed -- measured on the player's
songs, half of Thunder's solo sits closer together than a head is wide. A
sheet has no hit line to reach, so x is free of time and the playhead
carries the time instead. These tests are about that freedom being real:
every note gets its room, and the playhead pays for it by running unevenly.
"""

import pytest

from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)
from pickhero.ui.sheet import (MIN_GAP_HEADS, QUARTER_HEADS, lay_out, row_at)

HEAD = 44.0
WIDTH = 1200.0


def _song(bars_of, tempo=120, bar_ms=2000.0):
    """A song from a list of per-bar onset offsets, in ms from the bar."""
    notes, measures = [], []
    for index, offsets in enumerate(bars_of):
        start = index * bar_ms
        measures.append(MeasureInfo(index=index, start_ms=start,
                                    end_ms=start + bar_ms))
        for i, offset in enumerate(offsets):
            notes.append(NoteEvent(timestamp_ms=start + offset,
                                   duration_ms=100.0, midi_note=40 + (i % 40),
                                   string=1 + (i % 6), fret=i % 13,
                                   measure=index))
    return Timeline(notes, SongMetadata(title="t", tempo=tempo),
                    measures=measures)


class TestEveryNoteGetsItsRoom:
    """The thing the scrolling view cannot do at any setting."""

    def _burst(self):
        """One roomy bar and one of sixteenths -- Thunder's solo in little."""
        return _song([[0.0, 1000.0], [i * 125.0 for i in range(16)]])

    def test_two_notes_on_one_string_never_touch(self):
        rows = lay_out(self._burst(), WIDTH, HEAD)
        for row in rows:
            per_string = {}
            for placed in row.notes:
                per_string.setdefault(placed.note.string, []).append(placed.x)
            for string, xs in per_string.items():
                xs.sort()
                for a, b in zip(xs, xs[1:]):
                    assert b - a >= HEAD, f"string {string}: {b - a:.1f} px"

    def test_and_the_gap_is_the_stated_one(self):
        rows = lay_out(self._burst(), WIDTH, HEAD)
        gaps = []
        for row in rows:
            xs = sorted({p.x for p in row.notes})
            gaps += [b - a for a, b in zip(xs, xs[1:])]
        assert min(gaps) >= MIN_GAP_HEADS * HEAD * 0.99

    def test_a_sixteenth_run_is_evenly_spaced(self):
        """Below the minimum the proportional term stops mattering, which is
        what makes a fast run readable instead of merely proportional."""
        rows = lay_out(self._burst(), WIDTH, HEAD)
        run = sorted(p.x for row in rows for p in row.notes
                     if p.note.timestamp_ms >= 2000.0)
        assert len(run) == 16, "the run was not laid out at all"
        steps = [b - a for a, b in zip(run, run[1:])]
        assert max(steps) - min(steps) < 1.0


class TestThePlayheadCarriesTheTime:
    def test_it_only_moves_forwards(self):
        rows = lay_out(_song([[0.0, 500.0, 1000.0], [0.0, 250.0]]),
                       WIDTH, HEAD)
        row = rows[0]
        seen = [row.x_at(ms) for ms in range(int(row.start_ms),
                                             int(row.end_ms), 25)]
        assert seen == sorted(seen)

    def test_it_passes_through_every_note(self):
        rows = lay_out(_song([[0.0, 500.0, 1000.0]]), WIDTH, HEAD)
        for placed in rows[0].notes:
            assert rows[0].x_at(placed.note.timestamp_ms) == pytest.approx(
                placed.x)

    def test_it_runs_slower_where_the_notes_are_closer(self):
        """The price of the room above, and the whole reason it is free.

        Both bars are put on ONE row on purpose -- across two rows the
        justification scales them differently and the comparison would be
        between two lines rather than between two bars."""
        song = _song([[0.0, 1000.0], [i * 125.0 for i in range(16)]])
        rows = lay_out(song, 4000.0, HEAD)
        assert len(rows) == 1, "the two bars have to share a line"
        row = rows[0]
        roomy = (row.x_at(1000.0) - row.x_at(500.0)) / 500.0
        dense = (row.x_at(2500.0) - row.x_at(2000.0)) / 500.0
        assert dense > roomy * 1.5, (roomy, dense)

    def test_a_moment_before_or_after_the_row_is_pinned_to_its_edge(self):
        rows = lay_out(_song([[0.0], [0.0]]), WIDTH, HEAD)
        row = rows[0]
        assert row.x_at(row.start_ms - 5_000) == row.xs[0]
        assert row.x_at(row.end_ms + 5_000) == row.xs[-1]


class TestRowsAreWholeBars:
    def test_a_row_starts_and_ends_on_a_bar_line(self):
        rows = lay_out(_song([[0.0, 500.0]] * 12), WIDTH, HEAD)
        for row in rows:
            assert row.start_ms == row.first_bar * 2000.0
            assert row.end_ms == (row.last_bar + 1) * 2000.0

    def test_every_bar_appears_exactly_once(self):
        rows = lay_out(_song([[0.0, 500.0]] * 12), WIDTH, HEAD)
        covered = []
        for row in rows:
            covered += list(range(row.first_bar, row.last_bar + 1))
        assert covered == list(range(12))

    def test_a_row_fills_the_line(self):
        """Ragged right edges are what an engraver justifies away, and a
        half-empty line wastes the room the notes are short of."""
        rows = lay_out(_song([[0.0, 500.0]] * 12), WIDTH, HEAD)
        for row in rows:
            assert row.xs[-1] == pytest.approx(WIDTH)

    def test_a_denser_song_puts_fewer_bars_on_a_line(self):
        roomy = lay_out(_song([[0.0, 1000.0]] * 12), WIDTH, HEAD)
        dense = lay_out(_song([[i * 125.0 for i in range(16)]] * 12),
                        WIDTH, HEAD)
        assert len(dense) > len(roomy)

    def test_a_bar_too_dense_for_a_whole_line_says_so(self):
        """It is squeezed rather than dropped, and the row admits that its
        heads touch -- a silent overlap is the fault this whole view exists
        to end."""
        crammed = _song([[i * 5.0 for i in range(300)]])
        rows = lay_out(crammed, WIDTH, HEAD)
        assert len(rows) == 1 and rows[0].crowded

    def test_an_ordinary_song_is_never_crowded(self):
        rows = lay_out(_song([[0.0, 500.0, 1000.0, 1500.0]] * 8),
                       WIDTH, HEAD)
        assert not any(row.crowded for row in rows)


class TestWhatIsDrawnIsWhatTookRoom:
    def test_a_filtered_note_takes_no_room(self):
        """A note nobody can see must not be laid out for, or a song with a
        fret limit is spaced for notes that are not there."""
        song = _song([[0.0, 500.0, 1000.0, 1500.0]] * 6)
        everything = lay_out(song, WIDTH, HEAD)
        half = lay_out(song, WIDTH, HEAD, passes=lambda n: n.fret % 2 == 0)
        assert len(half) <= len(everything)
        assert all(p.note.fret % 2 == 0 for row in half for p in row.notes)

    def test_a_sustain_stops_short_of_the_next_note_on_its_string(self):
        notes = [
            NoteEvent(timestamp_ms=0.0, duration_ms=1900.0, midi_note=40,
                      string=6, fret=3),
            NoteEvent(timestamp_ms=500.0, duration_ms=100.0, midi_note=41,
                      string=6, fret=5),
        ]
        song = Timeline(notes, SongMetadata(title="t", tempo=120),
                        measures=[MeasureInfo(index=0, start_ms=0.0,
                                              end_ms=2000.0)])
        row = lay_out(song, WIDTH, HEAD)[0]
        first, second = sorted(row.notes, key=lambda p: p.x)
        assert first.x + first.width <= second.x + 1e-6

    def test_a_note_is_never_narrower_than_its_head(self):
        row = lay_out(_song([[i * 125.0 for i in range(16)]]), WIDTH, HEAD)[0]
        assert all(p.width >= HEAD for p in row.notes)


class TestFindingTheRow:
    def test_it_finds_the_row_a_moment_belongs_to(self):
        rows = lay_out(_song([[0.0, 500.0]] * 12), WIDTH, HEAD)
        for row in rows:
            assert row_at(rows, row.start_ms + 1.0) == row.index

    def test_before_the_song_is_the_first_row_and_after_it_the_last(self):
        rows = lay_out(_song([[0.0, 500.0]] * 12), WIDTH, HEAD)
        assert row_at(rows, -5_000.0) == 0
        assert row_at(rows, 10_000_000.0) == rows[-1].index

    def test_an_empty_song_does_not_raise(self):
        song = Timeline([], SongMetadata(title="t", tempo=120))
        rows = lay_out(song, WIDTH, HEAD)
        assert len(rows) == 1 and rows[0].notes == ()


class TestHowTheRowsStack:
    """The head size decides everything the eye gets: how big a note is
    drawn, how far two of them have to sit apart, and therefore how many
    bars a row holds. One number, which is why +/- is one control."""

    def test_two_rows_fit_the_room_they_were_sized_for(self):
        from pickhero.ui.sheet import ROWS_SHOWN, head_for_room, rows_that_fit
        for room in (400.0, 620.0, 780.0, 1000.0):
            head = head_for_room(room)
            assert rows_that_fit(room, head) >= ROWS_SHOWN, f"room {room}"

    def test_a_cramped_window_never_shrinks_the_head_past_reading(self):
        """Below the floor the fret number inside the head stops being a
        number, which is the fault the scrolling view spent a session on."""
        from pickhero.ui.sheet import MIN_HEAD_PX, head_for_room
        assert head_for_room(120.0) == MIN_HEAD_PX

    def test_bigger_heads_mean_fewer_rows_in_the_same_room(self):
        from pickhero.ui.sheet import head_for_room, rows_that_fit
        room = 780.0
        base = head_for_room(room)
        assert rows_that_fit(room, base * 1.75) < rows_that_fit(room, base)

    def test_there_is_always_at_least_one_row(self):
        from pickhero.ui.sheet import rows_that_fit
        assert rows_that_fit(50.0, 80.0) == 1

    def test_a_row_is_six_lanes_and_the_numbers_above_them(self):
        from pickhero.ui.sheet import LANE_HEADS, NUMBER_STRIP, row_height
        assert row_height(40.0) == 6 * LANE_HEADS * 40.0 + NUMBER_STRIP

    def test_bigger_heads_mean_fewer_bars_on_a_row(self):
        """The trade the player asked to be able to hold himself."""
        song = _song([[i * 125.0 for i in range(16)] for _ in range(24)])
        small = lay_out(song, WIDTH, 30.0)
        large = lay_out(song, WIDTH, 60.0)
        assert len(large) > len(small)
        assert (large[0].last_bar - large[0].first_bar
                <= small[0].last_bar - small[0].first_bar)


class TestTheStringsAreStringsNotLines:
    """"Die Saiten am Griffbrett sind nicht mehr sichtbar. Die tiefe Saite
    muss wesentlich dicker sein als die dünnste." Six lines of one weight
    throw away the strongest cue a guitarist has for which lane is which --
    the low E is a rope and the high e is a hair, and the eye knows it
    before it has read a single fret number."""

    def test_there_is_one_for_every_string(self):
        from pickhero.ui.sheet import string_widths
        assert len(string_widths(68.0)) == 6

    def test_they_get_thicker_towards_the_low_e(self):
        from pickhero.ui.sheet import string_widths
        widths = string_widths(68.0)
        assert widths == tuple(sorted(widths))

    def test_and_the_low_e_is_unmistakably_thicker(self):
        """Not a little thicker. The gauges of a light set run .010 to .046,
        which is the ratio these are taken from."""
        from pickhero.ui.sheet import string_widths
        for lane in (40.0, 55.0, 68.0, 90.0):
            widths = string_widths(lane)
            assert widths[5] >= 3 * widths[0], f"lane {lane}: {widths}"

    def test_none_of_them_vanishes_on_a_small_screen(self):
        from pickhero.ui.sheet import string_widths
        assert all(w >= 1 for w in string_widths(12.0))

    def test_a_taller_lane_carries_thicker_strings(self):
        from pickhero.ui.sheet import string_widths
        assert string_widths(90.0)[5] > string_widths(45.0)[5]


class TestANoteIsAsLongAsItSounds:
    """The same length rules the scrolling board uses, because a note that
    reads as held on one view and choked on the other is two answers to one
    question -- and the choked one is the true one."""

    def _placed(self, note_kwargs, gap_ms=2000.0, head=HEAD):
        notes = []
        for i, extra in enumerate(note_kwargs):
            fields = {"duration_ms": 200.0}
            fields.update(extra)
            notes.append(NoteEvent(timestamp_ms=i * gap_ms, midi_note=40,
                                   string=6, fret=3, measure=0, **fields))
        measures = [MeasureInfo(index=0, start_ms=0.0,
                                end_ms=len(notes) * gap_ms)]
        song = Timeline(notes, SongMetadata(title="t", tempo=120),
                        measures=measures)
        rows = lay_out(song, WIDTH, head)
        return rows[0].notes

    def test_the_numbers_are_the_boards_own(self):
        """They live in both files because the drawing imports this module
        and not the other way round. This is what stops them drifting."""
        from pickhero.ui import scrolling, sheet
        assert sheet.SUSTAIN_GAP == scrolling.SUSTAIN_GAP_FRACTION
        assert sheet.SLIDE_GAP == scrolling.SLIDE_GAP_FRACTION
        assert sheet.PALM_MUTE_MAX_HEADS == scrolling.PALM_MUTE_MAX_HEADS

    def test_a_dead_note_is_a_click_not_a_sustain(self):
        plain, dead = self._placed([{}, {"dead": True}])
        assert dead.width <= HEAD
        assert dead.width <= plain.width

    def test_a_palm_muted_note_is_choked(self):
        from pickhero.ui.sheet import PALM_MUTE_MAX_HEADS
        notes = self._placed([{"palm_mute": True, "duration_ms": 1900.0},
                              {}])
        assert notes[0].width <= HEAD * PALM_MUTE_MAX_HEADS + 0.01

    def test_a_let_ring_note_sounds_until_the_next_one_on_its_string(self):
        """A let-ring eighth is still an eighth -- what it says is that the
        string is never damped."""
        short, _ = self._placed([{"let_ring": True}, {}])
        plain, _ = self._placed([{}, {}])
        assert short.width > plain.width * 2

    def test_and_still_stops_short_of_it(self):
        rung, following = self._placed([{"let_ring": True}, {}])
        assert rung.x + rung.width < following.x

    def test_every_note_leaves_a_gap_before_the_next(self):
        """Without it a run of eighths renders as one unbroken ribbon."""
        notes = self._placed([{"duration_ms": 2000.0}] * 4)
        for note, following in zip(notes, notes[1:]):
            assert note.x + note.width < following.x

    def test_a_slide_gives_up_more_of_it_so_the_connector_fits(self):
        sliding, _ = self._placed([{"slide_to_next": True,
                                    "duration_ms": 1900.0}, {}])
        holding, _ = self._placed([{"duration_ms": 1900.0}, {}])
        assert sliding.width < holding.width

    def test_a_note_never_shrinks_below_its_own_head(self):
        """Whatever the rules say, a head is a head."""
        for extra in ({"dead": True}, {"palm_mute": True},
                      {"slide_to_next": True}, {}):
            for note in self._placed([extra, {}], gap_ms=120.0):
                assert note.width >= HEAD
