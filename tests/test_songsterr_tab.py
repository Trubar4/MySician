"""Songsterr's own tab format, read as a Timeline.

Most Songsterr tabs have no Guitar Pro file behind them -- the revision
walk comes back empty because Songsterr keeps those tabs in a format of
their own -- so this reader is the only route to a playable song for them.
Every rule in it was measured against the third-party `.gp` of one revision
that has both (`tools/check_songsterr_tab.py`); what is pinned here is the
arithmetic, on dicts, because the JSONs are somebody else's tab and not
ours to vendor.
"""

from __future__ import annotations

import pytest

from pickhero.tabs import songsterr_tab as st


def _beat(duration, notes=(), **extra):
    beat = {"duration": list(duration), "notes": [dict(n) for n in notes]}
    beat.update(extra)
    return beat


def _bar(beats, **extra):
    bar = {"voices": [{"beats": list(beats)}]}
    bar.update(extra)
    return bar


def _track(bars, **extra):
    track = {
        "name": "Guitar", "instrumentId": 30, "strings": 6,
        "tuning": [64, 59, 55, 50, 45, 40],     # standard, HIGH first
        "measures": list(bars),
        "automations": {"tempo": [{"measure": 0, "position": 0,
                                   "bpm": 120, "type": 4}]},
    }
    track.update(extra)
    if "signature" not in track["measures"][0]:
        track["measures"][0]["signature"] = [4, 4]
    return track


def _one(bars, **extra):
    return st.timeline_from_tracks([_track(bars, **extra)], 0)


#: One quarter at 120 BPM.
Q = 500.0


class TestTheWrittenLength:
    """`duration` is a fraction of a WHOLE note, and it already includes
    the dot: a dotted quarter is written `type: 4, duration: [3, 8],
    dots: 1`. So `dots` and `type` say how to ENGRAVE the beat and
    `duration` says how long it is."""

    def test_a_quarter_is_a_quarter(self):
        timeline = _one([_bar([_beat([1, 4], [{"string": 0, "fret": 5}]),
                               _beat([3, 4], [])])])
        note = timeline.notes[0]
        assert note.duration_quarters == pytest.approx(1.0)
        assert note.duration_ms == pytest.approx(Q)

    def test_the_dot_is_in_the_duration_and_must_not_be_applied_twice(self):
        """A dotted quarter is 1.5 quarters. Applying `dots` as well would
        make it 2.25 and move every note after it."""
        timeline = _one([_bar([
            _beat([3, 8], [{"string": 0, "fret": 5}], type=4, dots=1),
            _beat([1, 4], [{"string": 0, "fret": 7}]),
            _beat([3, 8], [], type=4, dots=1)])])
        first, second = timeline.notes
        assert first.duration_quarters == pytest.approx(1.5)
        assert second.timestamp_ms == pytest.approx(1.5 * Q)

    def test_a_beat_the_reader_cannot_measure_takes_no_time(self):
        timeline = _one([_bar([_beat(["?", 0], [{"string": 0, "fret": 1}]),
                               _beat([1, 1], [{"string": 0, "fret": 2}])])])
        assert [n.timestamp_ms for n in timeline.notes] == [0.0, 0.0]


class TestTheBarGrid:
    """A bar is as long as its SIGNATURE, not as long as the beats it
    happens to hold. A sparsely filled bar read short moves every bar after
    it -- the fault that cost the MusicXML export 42 seconds on one song."""

    def test_an_underfilled_bar_does_not_move_the_next_one(self):
        timeline = _one([_bar([_beat([1, 4], [{"string": 0, "fret": 1}])]),
                         _bar([_beat([1, 4], [{"string": 0, "fret": 2}])])])
        assert timeline.notes[1].timestamp_ms == pytest.approx(4 * Q)

    def test_and_it_says_so(self):
        timeline = _one([_bar([_beat([1, 4], [{"string": 0, "fret": 1}])])])
        assert "do not fill their signature" in \
            timeline.metadata.unreadable_line()

    def test_a_signature_is_carried_forward(self):
        """It appears only where it CHANGES -- once, on measure 0, in the
        revision this was read against."""
        timeline = _one([_bar([_beat([3, 4], [{"string": 0, "fret": 1}])],
                              signature=[3, 4]),
                         _bar([_beat([3, 4], [{"string": 0, "fret": 2}])])])
        assert [m.beat_type for m in timeline.measures] == [4, 4]
        assert [m.beats for m in timeline.measures] == [3, 3]
        assert timeline.notes[1].timestamp_ms == pytest.approx(3 * Q)

    def test_a_pickup_bar_really_is_shorter(self):
        """`anacrusis` says in the file that the first bar is a pickup, and
        measured on the real revision it holds exactly half a bar. Padding
        it to a full one puts a second of silence between the pickup and
        the downbeat it exists to lead into."""
        timeline = _one([_bar([_beat([1, 8], [], rest=True),
                               _beat([1, 8], [{"string": 0, "fret": 1}]),
                               _beat([1, 8], [{"string": 0, "fret": 2}]),
                               _beat([1, 8], [{"string": 0, "fret": 3}])]),
                         _bar([_beat([1, 1], [{"string": 0, "fret": 4}])])],
                        anacrusis=True)
        assert timeline.measures[0].end_ms == pytest.approx(2 * Q)
        assert timeline.notes[-1].timestamp_ms == pytest.approx(2 * Q)

    def test_and_a_short_first_bar_without_the_flag_is_not_one(self):
        """The control. Without it the rule above would hold for every
        underfilled opening bar, which is a transcription fault rather than
        a pickup."""
        timeline = _one([_bar([_beat([1, 2], [{"string": 0, "fret": 1}])]),
                         _bar([_beat([1, 1], [{"string": 0, "fret": 4}])])])
        assert timeline.notes[-1].timestamp_ms == pytest.approx(4 * Q)


class TestEachVoiceStartsAtTheBarLine:
    def test_a_second_voice_is_not_appended_to_the_first(self):
        """A let-ring bass note under a run of eighths lands where it is
        written rather than after the run."""
        bar = {"voices": [
            {"beats": [_beat([1, 8], [{"string": 0, "fret": 1}]),
                       _beat([1, 8], [{"string": 0, "fret": 2}])]},
            {"beats": [_beat([1, 1], [{"string": 5, "fret": 3}])]}],
            "signature": [4, 4]}
        timeline = _one([bar])
        low = [n for n in timeline.notes if n.string == 6][0]
        assert low.timestamp_ms == pytest.approx(0.0)


class TestTheStrings:
    """Songsterr counts strings from the HIGH one at 0; this app counts
    from the high one at 1. The GPIF reader's tuning array is LOW first, so
    the two conversions are deliberately not the same."""

    def test_string_zero_is_the_high_one(self):
        timeline = _one([_bar([_beat([1, 1], [{"string": 0, "fret": 0},
                                              {"string": 5, "fret": 0}])])])
        by_string = {n.string: n.midi_note for n in timeline.notes}
        assert by_string == {1: 64, 6: 40}

    def test_the_pitch_comes_from_the_tuning(self):
        timeline = _one([_bar([_beat([1, 1], [{"string": 5, "fret": 3}])])])
        assert timeline.notes[0].midi_note == 43

    def test_the_picker_reads_the_tuning_low_string_first(self):
        """Which is the order the GP loader returns and the order a
        guitarist tunes in."""
        rows = st.list_tracks([_track([_bar([])])])
        assert rows[0]["tuning"] == [40, 45, 50, 55, 59, 64]

    def test_a_string_this_tab_does_not_have_is_counted_not_raised(self):
        timeline = _one([_bar([_beat([1, 1], [{"string": 7, "fret": 0},
                                              {"string": 0, "fret": 0}])])])
        assert len(timeline.notes) == 1
        assert timeline.metadata.unreadable_notes == 1
        assert "string 8" in timeline.metadata.unreadable_line()

    def test_a_note_with_no_fret_is_counted_too(self):
        timeline = _one([_bar([_beat([1, 1], [{"string": 0}])])])
        assert timeline.metadata.unreadable_notes == 1


class TestATieIsOneNoteWrittenTwice:
    def test_nothing_is_appended_and_the_note_gets_longer(self):
        timeline = _one([_bar([_beat([1, 4], [{"string": 0, "fret": 5}]),
                               _beat([1, 4], [{"string": 0, "fret": 5,
                                               "tie": True}]),
                               _beat([1, 2], [])])])
        assert len(timeline.notes) == 1
        assert timeline.notes[0].duration_ms == pytest.approx(2 * Q)

    def test_and_without_the_tie_it_really_is_two_notes(self):
        """The control. Without it the class would pass on a reader that
        simply drops every second note."""
        timeline = _one([_bar([_beat([1, 4], [{"string": 0, "fret": 5}]),
                               _beat([1, 4], [{"string": 0, "fret": 5}]),
                               _beat([1, 2], [])])])
        assert len(timeline.notes) == 2
        assert timeline.notes[0].duration_ms == pytest.approx(Q)

    def test_a_tie_across_a_bar_line_reaches_back(self):
        timeline = _one([_bar([_beat([1, 1], [{"string": 0, "fret": 5}])]),
                         _bar([_beat([1, 1], [{"string": 0, "fret": 5,
                                               "tie": True}])])])
        assert len(timeline.notes) == 1
        assert timeline.notes[0].duration_ms == pytest.approx(8 * Q)


class TestTheBend:
    """Measured against the .gp of the same revision rather than guessed:
    `tone: 100` is its 2.0 semitones and `position: 60` is its 1.0."""

    def test_the_two_units(self):
        timeline = _one([_bar([_beat([1, 1], [
            {"string": 0, "fret": 11,
             "bend": {"tone": 100,
                      "points": [{"position": 0, "tone": 0},
                                 {"position": 30, "tone": 50},
                                 {"position": 60, "tone": 100}]}}])])])
        assert timeline.notes[0].bend == ((0.0, 0.0), (0.5, 1.0), (1.0, 2.0))

    def test_a_curve_that_never_leaves_zero_is_not_a_bend(self):
        """A bend the tab declares and does not use would draw an arc of no
        height. The GP reader drops it for the same reason."""
        timeline = _one([_bar([_beat([1, 1], [
            {"string": 0, "fret": 5,
             "bend": {"tone": 0, "points": [{"position": 0, "tone": 0},
                                            {"position": 60, "tone": 0}]}}])])])
        assert timeline.notes[0].bend == ()

    def test_an_unbent_note_has_no_curve(self):
        timeline = _one([_bar([_beat([1, 1], [{"string": 0, "fret": 5}])])])
        assert timeline.notes[0].bend == ()


class TestTheOtherTechniques:
    def test_let_ring_is_a_beat_and_reaches_every_note_in_it(self):
        timeline = _one([_bar([_beat([1, 1], [{"string": 0, "fret": 0},
                                              {"string": 1, "fret": 2}],
                                     letRing=True)])])
        assert all(n.let_ring for n in timeline.notes)

    def test_palm_mute_is_read_from_the_beat_or_the_note(self):
        timeline = _one([_bar([_beat([1, 2], [{"string": 0, "fret": 0}],
                                     palmMute=True),
                               _beat([1, 2], [{"string": 0, "fret": 0,
                                               "palmMute": True}])])])
        assert all(n.palm_mute for n in timeline.notes)

    def test_a_dead_note_is_flagged_and_kept(self):
        timeline = _one([_bar([_beat([1, 1], [{"string": 0, "fret": 5,
                                               "dead": True}])])])
        assert timeline.notes[0].dead
        assert timeline.notes[0].fret == 5

    @pytest.mark.parametrize("spelling", ["shift", "legato"])
    def test_both_slides_to_the_next_note_are_slides(self, spelling):
        """A LEGATO slide maps to `slide_to_next` and not to
        `hammer_to_next`, which is what the GP loader does with GP5's own
        legato slide -- a second reader must not score one technique two
        ways."""
        timeline = _one([_bar([_beat([1, 1], [{"string": 0, "fret": 5,
                                               "slide": spelling}])])])
        note = timeline.notes[0]
        assert note.slide_to_next and not note.hammer_to_next
        assert note.slide_in == 0 and note.slide_out == 0

    def test_a_slide_out_carries_its_direction(self):
        timeline = _one([_bar([_beat([1, 2], [{"string": 0, "fret": 5,
                                               "slide": "upwards"}]),
                               _beat([1, 2], [{"string": 0, "fret": 5,
                                               "slide": "downwards"}])])])
        assert [n.slide_out for n in timeline.notes] == [1, -1]
        assert not any(n.slide_to_next for n in timeline.notes)

    def test_a_slide_nobody_has_seen_is_no_slide(self):
        timeline = _one([_bar([_beat([1, 1], [{"string": 0, "fret": 5,
                                               "slide": "sideways"}])])])
        note = timeline.notes[0]
        assert not note.slide_to_next and not note.slide_in \
            and not note.slide_out


class TestRests:
    def test_a_rest_beat_advances_the_clock_and_sounds_nothing(self):
        timeline = _one([_bar([_beat([1, 2], [{"rest": True}], rest=True),
                               _beat([1, 2], [{"string": 0, "fret": 5}])])])
        assert len(timeline.notes) == 1
        assert timeline.notes[0].timestamp_ms == pytest.approx(2 * Q)

    def test_a_rest_note_inside_a_sounding_beat_is_skipped(self):
        timeline = _one([_bar([_beat([1, 1], [{"rest": True},
                                              {"string": 0, "fret": 5}])])])
        assert len(timeline.notes) == 1
        assert timeline.metadata.unreadable_notes == 0


class TestTheTempo:
    def test_a_change_moves_what_comes_after_it_and_not_what_came_before(self):
        """The positions are worked out in QUARTERS and turned into time
        afterwards, so a tempo change inside a note is accounted for rather
        than averaged away."""
        bars = [_bar([_beat([1, 1], [{"string": 0, "fret": 1}])]),
                _bar([_beat([1, 1], [{"string": 0, "fret": 2}])]),
                _bar([_beat([1, 1], [{"string": 0, "fret": 3}])])]
        timeline = st.timeline_from_tracks([_track(bars, automations={
            "tempo": [{"measure": 0, "position": 0, "bpm": 120, "type": 4},
                       {"measure": 2, "position": 0, "bpm": 240, "type": 4}]
        })], 0)
        first, second, third = timeline.notes
        assert first.timestamp_ms == pytest.approx(0.0)
        assert second.timestamp_ms == pytest.approx(4 * Q)
        assert second.duration_ms == pytest.approx(4 * Q)
        assert third.timestamp_ms == pytest.approx(8 * Q)
        assert third.duration_ms == pytest.approx(2 * Q)

    def test_the_bpm_counts_the_note_value_the_file_names(self):
        bars = [_bar([_beat([1, 4], [{"string": 0, "fret": 1}]),
                      _beat([3, 4], [])])]
        timeline = st.timeline_from_tracks([_track(bars, automations={
            "tempo": [{"measure": 0, "position": 0, "bpm": 120, "type": 8}]
        })], 0)
        assert timeline.notes[0].duration_ms == pytest.approx(1000.0)

    def test_and_anything_but_a_plain_quarter_count_says_so(self):
        """Only `type: 4` at `position: 0` appears in the revision this was
        fitted on, so a number nobody could check is printed beside the
        sentence saying so."""
        bars = [_bar([_beat([1, 1], [{"string": 0, "fret": 1}])])]
        timeline = st.timeline_from_tracks([_track(bars, automations={
            "tempo": [{"measure": 0, "position": 0.5, "bpm": 90, "type": 8}]
        })], 0)
        line = timeline.metadata.unreadable_line()
        assert "inside a bar" in line and "1/8 notes" in line

    def test_a_track_with_no_tempo_at_all_still_plays(self):
        bars = [_bar([_beat([1, 4], [{"string": 0, "fret": 1}]),
                      _beat([3, 4], [])])]
        timeline = st.timeline_from_tracks([_track(bars, automations={})], 0)
        assert timeline.metadata.tempo == 120
        assert timeline.notes[0].duration_ms == pytest.approx(Q)


class TestWhatThisReaderDoesNotRead:
    """The structures that could cost whole bars -- a repeat, an alternate
    ending, a tuplet -- are absent from the one revision these rules were
    measured against, so nothing can be verified about them. What CAN be
    done is refuse to be silent."""

    def test_a_repeat_nobody_has_seen_is_named(self):
        timeline = _one([_bar([_beat([1, 1], [{"string": 0, "fret": 1}])],
                              repeatStart=True, repeatCount=2)])
        line = timeline.metadata.unreadable_line()
        assert "repeatStart" in line and "repeatCount" in line

    def test_and_a_key_it_does_read_is_not_named(self):
        """The control: without it the rule would pass by naming
        everything, which says nothing."""
        timeline = _one([_bar([_beat([1, 1], [{"string": 0, "fret": 1}],
                                     letRing=True, beamStart=True)],
                              marker={"text": "Chorus"})])
        assert "which this reader does not read" not in \
            timeline.metadata.unreadable_line()

    def test_an_unknown_note_key_is_named_with_its_count(self):
        timeline = _one([_bar([_beat([1, 2], [{"string": 0, "fret": 1,
                                               "tapped": True}]),
                               _beat([1, 2], [{"string": 0, "fret": 2,
                                               "tapped": True}])])])
        assert "2x a note carrying tapped" in \
            timeline.metadata.unreadable_line()


class TestTheTrackPicker:
    def test_vocals_written_as_a_sax_are_not_a_guitar(self):
        """These tabs put the vocal line on a six-string staff with guitar
        tuning, so the program number is the only thing that says it is not
        a guitar part."""
        rows = st.list_tracks([_track([_bar([])], instrumentId=66,
                                      name="Lead Vocals")])
        assert not rows[0]["is_guitar"]
        assert not rows[0]["is_percussion"]

    def test_a_distortion_guitar_is_one(self):
        rows = st.list_tracks([_track([_bar([])], instrumentId=30)])
        assert rows[0]["is_guitar"]

    def test_the_drum_kit_is_percussion_and_has_no_tuning(self):
        rows = st.list_tracks([{"name": "Drums", "instrumentId": 1024,
                                "strings": 6, "tuning": None,
                                "measures": []}])
        assert rows[0]["is_percussion"] and not rows[0]["is_guitar"]

    def test_a_bass_is_neither_guitar_nor_drums(self):
        rows = st.list_tracks([_track([_bar([])], instrumentId=34,
                                      tuning=[43, 38, 33, 28])])
        assert not rows[0]["is_guitar"] and not rows[0]["is_percussion"]
        assert rows[0]["num_strings"] == 4

    def test_a_track_with_no_name_still_has_a_row(self):
        rows = st.list_tracks([{"measures": []}, "nonsense"])
        assert [row["name"] for row in rows] == ["Track 1", "Track 2"]


class TestNothingHereRaises:
    """A tab is somebody else's file, and this one arrives over the
    network. A reader that raises takes the song list with it."""

    @pytest.mark.parametrize("tracks", [
        [], [None], [{}], [{"measures": None}],
        [{"measures": [None, {"voices": None}, {"voices": [None]}]}],
        [{"measures": [{"voices": [{"beats": None}]}]}],
        [{"measures": [{"voices": [{"beats": [None, {}]}]}]}],
        [{"measures": [{"voices": [{"beats": [{"duration": [1, 1],
                                               "notes": [None, 3]}]}]}]}],
    ])
    def test_it_comes_back_with_a_timeline(self, tracks):
        timeline = st.timeline_from_tracks(tracks, 0)
        assert timeline.notes == []
        assert st.list_tracks(tracks) is not None

    def test_a_track_index_outside_the_file_falls_back_to_the_first(self):
        timeline = _one([_bar([_beat([1, 1], [{"string": 0, "fret": 5}])])])
        far = st.timeline_from_tracks([_track([_bar(
            [_beat([1, 1], [{"string": 0, "fret": 5}])])])], 9)
        assert len(far.notes) == len(timeline.notes) == 1
