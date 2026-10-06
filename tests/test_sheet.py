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


class TestReadingAPositionBack:
    """*"Klick auf Griffbrett in hybrid View, um an eine Stelle zu springen."*

    The sheet is the one view where a pixel is not a time, so the click has
    to be read back through the very anchors the layout was built from.
    """

    def _row(self):
        return lay_out(_song([[0.0, 1000.0], [i * 125.0 for i in range(16)]]),
                       WIDTH, HEAD)[0]

    def test_it_is_the_inverse_of_the_playhead(self):
        row = self._row()
        for placed in row.notes:
            back = row.ms_at(row.x_at(placed.note.timestamp_ms))
            assert back == pytest.approx(placed.note.timestamp_ms, abs=0.5)

    def test_a_click_past_either_end_stays_in_the_row(self):
        row = self._row()
        assert row.ms_at(-500.0) == row.times[0]
        assert row.ms_at(WIDTH * 3) == row.times[-1]

    def test_clicking_a_note_lands_on_it_not_just_after_it(self):
        # A raw position inside a head is already past the note, which is
        # the one place it is no use.
        row = self._row()
        for placed in row.notes:
            middle = placed.x + placed.width / 2
            assert row.moment_at(middle) == pytest.approx(
                placed.note.timestamp_ms, abs=0.5)

    def test_it_snaps_to_the_nearer_of_the_two_it_sits_between(self):
        row = self._row()
        a, b = row.xs[0], row.xs[1]
        assert row.moment_at(a + (b - a) * 0.1) == row.times[0]
        assert row.moment_at(a + (b - a) * 0.9) == row.times[1]


class TestANoteValueIsTheSameWidthWhereverItIsWritten:
    """One tempo for a song that has twelve is the wrong unit for a sheet.

    *"Beim Song im Anhang passt das Tempo in der Hybrid View nicht. In den
    Takten 34-45 usw. fährt der Progress-Bar innerhalb eines Taktes mal
    langsamer und mal schneller. Das heisst kürzere Noten und Pausen und
    längere Noten können dieselbe Breite haben."*

    Guns N' Roses' "November Rain" is written at a header tempo of 70 and
    plays at twelve tempos between 70 and 91. Spacing came off the header, so
    an eighth measured 1.30 heads at 70 BPM and 1.17 at 78 -- across the
    `MIN_GAP_HEADS` floor of 1.18. Two bars of music apart, the same written
    rhythm was drawn proportionally in one bar and floored to a sixteenth's
    width in the next.
    """

    def _two_tempos(self, slow_ms, fast_ms, header):
        """Two 4/4 bars of straight eighths, at two different tempos."""
        notes, measures = [], []
        start = 0.0
        for index, bar_ms in enumerate((slow_ms, fast_ms)):
            measures.append(MeasureInfo(index=index, start_ms=start,
                                        end_ms=start + bar_ms,
                                        beats=4, beat_type=4))
            for i in range(8):
                notes.append(NoteEvent(timestamp_ms=start + i * bar_ms / 8,
                                       duration_ms=100.0, midi_note=40,
                                       string=1, fret=0, measure=index))
            start += bar_ms
        return Timeline(notes, SongMetadata(title="t", tempo=header),
                        measures=measures)

    def _gaps(self, row, bar_lines, which):
        """The pixel gaps between consecutive anchors inside one bar."""
        left = bar_lines[which]
        right = bar_lines[which + 1] if which + 1 < len(bar_lines) else None
        xs = [x for x in row.xs
              if x >= left - 1e-6 and (right is None or x <= right + 1e-6)]
        return [b - a for a, b in zip(xs, xs[1:])]

    def test_an_eighth_is_an_eighth_in_both_bars(self):
        # 70 BPM and 91 BPM: the two ends of that song's real tempo range.
        song = self._two_tempos(3428.6, 2637.4, header=70)
        rows = lay_out(song, 100_000.0, HEAD)       # one row, no justifying
        assert len(rows) == 1
        row = rows[0]
        slow = self._gaps(row, row.bar_lines, 0)
        fast = self._gaps(row, row.bar_lines, 1)
        # Inside each bar every gap is one eighth, so every gap is equal...
        for gaps in (slow, fast):
            assert max(gaps) == pytest.approx(min(gaps), rel=1e-6)
        # ...and an eighth is the same WIDTH in both, whatever the tempo.
        # (A row is justified to the line, so the absolute pixels are the
        # line's; what the layout decides is the ratio, and it is 1.)
        assert slow[0] == pytest.approx(fast[0], rel=1e-6)

    def test_an_eighth_asks_for_half_a_quarter_in_every_bar(self):
        """Before the scale: what the layout wants, in heads."""
        from pickhero.ui.sheet import _bar_anchors, _widths, quarter_ms
        song = self._two_tempos(3428.6, 2637.4, header=70)
        header_quarter = 60_000.0 / 70
        for bar in song.measures:
            per_ms = QUARTER_HEADS * HEAD / quarter_ms(bar, header_quarter)
            widths = _widths(_bar_anchors(bar, song.notes), per_ms,
                             MIN_GAP_HEADS * HEAD)
            assert widths == pytest.approx([QUARTER_HEADS * HEAD / 2] * 8,
                                           rel=1e-6)

    def test_the_fast_bar_used_to_be_floored_and_the_slow_one_not(self):
        """The bug, written down: it is what the fix has to not do."""
        song = self._two_tempos(3428.6, 2637.4, header=70)
        header_quarter = 60_000.0 / 70
        floor = MIN_GAP_HEADS * HEAD
        for bar_ms, floored in ((3428.6, False), (2637.4, True)):
            width = (bar_ms / 8) * QUARTER_HEADS * HEAD / header_quarter
            assert (width < floor) is floored

    def test_the_playhead_crosses_both_bars_at_the_same_speed(self):
        """Which is the whole of what he was reading off the screen."""
        song = self._two_tempos(3428.6, 2637.4, header=70)
        row = lay_out(song, 100_000.0, HEAD)[0]
        def speed(ms, span):
            return (row.x_at(ms + span) - row.x_at(ms)) / span
        slow = speed(100.0, 300.0)                 # inside the 70 BPM bar
        fast = speed(3428.6 + 100.0, 300.0)        # inside the 91 BPM bar
        # A sheet spaces by note VALUE, so the same written rhythm is the
        # same pixels -- and at 91 BPM those pixels are crossed faster.
        assert fast / slow == pytest.approx(3428.6 / 2637.4, rel=0.01)

    def test_a_bar_that_cannot_say_falls_back_to_the_header(self):
        from pickhero.ui.sheet import quarter_ms
        bar = MeasureInfo(index=0, start_ms=0.0, end_ms=0.0)
        assert quarter_ms(bar, 857.0) == 857.0

    def test_a_six_eight_bar_is_read_as_the_music_in_it(self):
        """6/8 and 3/4 are the same length of time and different music."""
        from pickhero.ui.sheet import quarter_ms
        three_four = MeasureInfo(index=0, start_ms=0.0, end_ms=1500.0,
                                 beats=3, beat_type=4)
        six_eight = MeasureInfo(index=0, start_ms=0.0, end_ms=1500.0,
                                beats=6, beat_type=8)
        assert quarter_ms(three_four, 1.0) == pytest.approx(500.0)
        assert quarter_ms(six_eight, 1.0) == pytest.approx(500.0)
        # Same here, which is right: six eighths ARE three crotchets. What a
        # fixed "a quarter of the bar" would have given is 375 ms.
        assert quarter_ms(six_eight, 1.0) != pytest.approx(375.0)


class TestOneSpeedThroughOneBar:
    """*"Hier ändert sich bspw. im Takt im Anhangsbild noch immer die
    Geschwindigkeit des Balkens innerhalb einem Takt. Es müssten aber manche
    Töne breiter sein. Niemand sagt, dass jeder Takt gleich viel Platz in der
    Breite haben muss."*

    Reading the tempo per bar stopped a note value changing width from one
    bar to the next. It did nothing about the floor being applied gap by
    gap, which equalises the notes it is meant to keep apart. Bar 38 of his
    November Rain, at 78 BPM with a 50 px head, as it was drawn:

        gap      385  192  192  385  385  385  192  192  385  385   ms
        wanted    65   33   33   65   65   65   33   33   65   65   px
        drawn     65   59   59   65   65   65   59   59   65   65   px

    An eighth and a sixteenth 10 % apart for twice the duration, so the
    playhead crossed the sixteenths 1.82x faster. Stretching the whole bar
    instead keeps the ratios exact -- 118 against 59 -- and the bar grows
    from 626 px to 944 px, which is the price he named.
    """

    # Bar 38, 4/4 at 78 BPM: eighth eighth-of-two-sixteenths, and so on.
    BAR_MS = 3076.9
    GAPS = (385.0, 192.0, 192.0, 385.0, 385.0, 385.0, 192.0, 192.0, 385.0)

    def _bar(self, gaps=None, bar_ms=None, tempo=78):
        gaps = self.GAPS if gaps is None else gaps
        bar_ms = self.BAR_MS if bar_ms is None else bar_ms
        offsets, at = [0.0], 0.0
        for gap in gaps:
            at += gap
            offsets.append(at)
        return _song([offsets], tempo=tempo, bar_ms=bar_ms)

    def _widths_of(self, song, head=HEAD):
        from pickhero.ui.sheet import _bar_anchors, _widths, quarter_ms
        bar = song.measures[0]
        fallback = 60_000.0 / song.metadata.tempo
        per_ms = QUARTER_HEADS * head / quarter_ms(bar, fallback)
        return _widths(_bar_anchors(bar, song.notes), per_ms,
                       MIN_GAP_HEADS * head)

    def test_a_sixteenth_is_half_an_eighth(self):
        """The whole complaint, in one ratio. It was 1.10 before."""
        widths = self._widths_of(self._bar())
        eighths = [w for w, g in zip(widths, self.GAPS) if g > 300.0]
        sixteenths = [w for w, g in zip(widths, self.GAPS) if g < 300.0]
        assert min(eighths) == pytest.approx(max(eighths), rel=1e-6)
        assert min(sixteenths) == pytest.approx(max(sixteenths), rel=1e-6)
        assert eighths[0] / sixteenths[0] == pytest.approx(2.0, rel=0.01)

    def test_the_playhead_runs_at_one_speed_through_it(self):
        row = lay_out(self._bar(), 100_000.0, HEAD)[0]

        def speed(ms, span=80.0):
            return (row.x_at(ms + span) - row.x_at(ms)) / span

        # Inside the first eighth, and inside the first sixteenth after it.
        assert speed(100.0) == pytest.approx(speed(450.0), rel=0.02)

    def test_the_bar_gets_wider_instead(self):
        """Which is the half he granted: no bar owes the next one its width."""
        from pickhero.ui.sheet import _bar_anchors, quarter_ms
        head = 50.0
        song = self._bar()
        bar = song.measures[0]
        per_ms = QUARTER_HEADS * head / quarter_ms(bar, 1.0)
        anchors = _bar_anchors(bar, song.notes)
        wanted = [(b - a) * per_ms for a, b in zip(anchors, anchors[1:])]
        was = sum(max(w, MIN_GAP_HEADS * head) for w in wanted)
        now = sum(self._widths_of(song, head))
        assert was == pytest.approx(626.0, rel=0.02)     # what he saw
        assert now == pytest.approx(944.0, rel=0.02)     # what it costs

    def test_two_heads_still_never_touch(self):
        head = 50.0
        widths = self._widths_of(self._bar(), head)
        assert min(widths) >= MIN_GAP_HEADS * head - 1e-6

    def test_a_bar_of_one_value_costs_nothing(self):
        """The control. Where every gap is the same, there is nothing to
        part and the stretch must not widen the bar by a pixel."""
        sixteenths = (192.0,) * 15
        widths = self._widths_of(self._bar(sixteenths,
                                           bar_ms=sum(sixteenths)))
        assert len(widths) == 15
        assert sum(widths) == pytest.approx(15 * MIN_GAP_HEADS * HEAD,
                                            rel=1e-6)

    def test_the_stretch_is_capped(self):
        """A bar of crotchets with one thirty-second in it asks for 3.63x."""
        from pickhero.ui.sheet import MAX_BAR_STRETCH
        from pickhero.ui.sheet import _bar_anchors, quarter_ms
        gaps = (769.0, 769.0, 96.0, 96.0, 769.0)
        song = self._bar(gaps, bar_ms=sum(gaps))
        bar = song.measures[0]
        per_ms = QUARTER_HEADS * HEAD / quarter_ms(bar, 1.0)
        anchors = _bar_anchors(bar, song.notes)
        wanted = [(b - a) * per_ms for a, b in zip(anchors, anchors[1:])]
        widths = self._widths_of(song)
        # A thirty-second against a crotchet asks for 3.63x and gets 2.0,
        # so the short gap is floored and the bar still varies in speed --
        # which is honest: it cannot be drawn in proportion at this size.
        assert widths[0] / wanted[0] == pytest.approx(MAX_BAR_STRETCH,
                                                      rel=1e-6)
        assert min(widths) >= MIN_GAP_HEADS * HEAD - 1e-6

    def test_an_anchor_a_hair_away_does_not_blow_the_bar_up(self):
        """Two onsets a fraction of a millisecond apart really occur, and
        without the cap they ask for a stretch of ten to the thirteenth."""
        gaps = (769.0, 0.0001, 769.0, 769.0)
        widths = self._widths_of(self._bar(gaps, bar_ms=sum(gaps)))
        assert sum(widths) < 20 * QUARTER_HEADS * HEAD
        assert min(widths) >= MIN_GAP_HEADS * HEAD - 1e-6

    def test_the_stretch_does_not_depend_on_the_head_size(self):
        """Which is why +/- is still the lever for bars per row: the shape of
        a bar is the same at every zoom, only its pixels change."""
        shapes = []
        for head in (29.0, 44.0, 58.0):
            widths = self._widths_of(self._bar(), head)
            shapes.append([w / widths[0] for w in widths])
        for shape in shapes[1:]:
            assert shape == pytest.approx(shapes[0], rel=1e-6)
