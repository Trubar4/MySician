"""Tests for pickhero.tabs.loader module."""

from pathlib import Path

import pytest

from pickhero.audio.note_utils import STANDARD_TUNING
from pickhero.tabs.loader import TempoMap, is_guitar_track, list_tracks, load_gp_file

FIXTURES = Path(__file__).parent / "fixtures"


class TestTempoMap:
    def test_single_tempo(self):
        tm = TempoMap(120)
        # 960 ticks = 1 quarter note at 120 BPM = 500ms
        assert tm.tick_to_ms(960) == pytest.approx(500.0)
        # 0 ticks = 0ms
        assert tm.tick_to_ms(0) == 0.0

    def test_duration_at_single_tempo(self):
        tm = TempoMap(120)
        # Quarter note (960 ticks) at 120 BPM = 500ms
        assert tm.duration_ticks_to_ms(960, at_tick=0) == pytest.approx(500.0)
        # Half note = 1000ms
        assert tm.duration_ticks_to_ms(1920, at_tick=0) == pytest.approx(1000.0)

    def test_tempo_change(self):
        tm = TempoMap(120)
        # Tempo changes to 240 at tick 960
        tm.add_change(960, 240)

        # At tick 960: all time is at 120 BPM → 500ms
        assert tm.tick_to_ms(960) == pytest.approx(500.0)

        # At tick 1920: 500ms (first quarter at 120) + 250ms (next quarter at 240) = 750ms
        assert tm.tick_to_ms(1920) == pytest.approx(750.0)

    def test_multiple_tempo_changes(self):
        tm = TempoMap(120)
        tm.add_change(960, 240)   # Change at tick 960
        tm.add_change(1920, 60)   # Change at tick 1920

        # tick 0→960 at 120 BPM: 500ms
        # tick 960→1920 at 240 BPM: 250ms
        # tick 1920→2880 at 60 BPM: 1000ms
        assert tm.tick_to_ms(2880) == pytest.approx(1750.0)

    def test_tempo_at_tick(self):
        tm = TempoMap(120)
        tm.add_change(960, 200)
        assert tm.tempo_at_tick(0) == 120
        assert tm.tempo_at_tick(959) == 120
        assert tm.tempo_at_tick(960) == 200
        assert tm.tempo_at_tick(5000) == 200

    def test_duration_uses_local_tempo(self):
        tm = TempoMap(120)
        tm.add_change(960, 240)

        # Before change: 120 BPM
        assert tm.duration_ticks_to_ms(960, at_tick=0) == pytest.approx(500.0)
        # After change: 240 BPM
        assert tm.duration_ticks_to_ms(960, at_tick=960) == pytest.approx(250.0)


class TestListTracks:
    def test_slides(self):
        tracks = list_tracks(FIXTURES / "Slides.gp5")
        assert len(tracks) == 1
        assert tracks[0]["is_guitar"] is True

    def test_notes(self):
        tracks = list_tracks(FIXTURES / "notes.gp5")
        assert len(tracks) == 1
        assert tracks[0]["is_guitar"] is True

    def test_effects(self):
        tracks = list_tracks(FIXTURES / "Effects.gp5")
        assert len(tracks) == 1
        assert tracks[0]["is_guitar"] is True

    def test_tie(self):
        tracks = list_tracks(FIXTURES / "Tie.gp5")
        assert len(tracks) == 1
        assert tracks[0]["is_guitar"] is True

    def test_demo_v5(self):
        tracks = list_tracks(FIXTURES / "Demo_v5.gp5")
        assert len(tracks) == 5
        guitar_tracks = [t for t in tracks if t["is_guitar"]]
        assert len(guitar_tracks) == 2
        assert guitar_tracks[0]["name"] == "Rhythm Guitar"
        assert guitar_tracks[1]["name"] == "Solo Guitar"

    def test_canon(self):
        tracks = list_tracks(FIXTURES / "canon.gp5")
        assert len(tracks) == 9
        guitar_tracks = [t for t in tracks if t["is_guitar"]]
        assert len(guitar_tracks) == 4
        # String ensemble (inst=49) and synth strings (inst=51) excluded
        non_guitar_names = {t["name"] for t in tracks if not t["is_guitar"]}
        assert "Low Bassy Sound" in non_guitar_names
        assert "High Soundy Thing" in non_guitar_names

    def test_percussion_excluded(self):
        tracks = list_tracks(FIXTURES / "Demo_v5.gp5")
        perc = [t for t in tracks if t["is_percussion"]]
        assert len(perc) == 1
        assert perc[0]["is_guitar"] is False


class TestLoadGPFile:
    def test_slides(self):
        tl = load_gp_file(FIXTURES / "Slides.gp5")
        assert len(tl) == 12
        assert tl.metadata.tempo == 120
        assert tl.metadata.tuning == STANDARD_TUNING

    def test_notes(self):
        tl = load_gp_file(FIXTURES / "notes.gp5")
        assert len(tl) == 28
        assert tl.metadata.tempo == 120

    def test_effects(self):
        tl = load_gp_file(FIXTURES / "Effects.gp5")
        assert len(tl) == 46
        assert tl.metadata.tempo == 120

    def test_tie(self):
        tl = load_gp_file(FIXTURES / "Tie.gp5")
        assert len(tl) == 11
        assert tl.metadata.tempo == 120

    def test_demo_v5_track0(self):
        """771, not the 729 this asserted before repeats were played.

        The file opens with |: over bars 0-2 and a first/second-time ending;
        the loader walked every bar once and silently dropped the repeat AND
        the second ending, so bar 4 was never played at all. The old number
        was the bug written down.
        """
        tl = load_gp_file(FIXTURES / "Demo_v5.gp5", track_index=0)
        assert len(tl) == 771
        assert tl.metadata.tempo == 165
        assert tl.metadata.track_name == "Rhythm Guitar"

    def test_canon_track0(self):
        tl = load_gp_file(FIXTURES / "canon.gp5", track_index=0)
        assert len(tl) == 1489
        assert tl.metadata.tempo == 90
        assert tl.metadata.track_name == "Guitar Player"

    def test_standard_tuning(self):
        for fname in ["Slides.gp5", "notes.gp5", "Effects.gp5", "Tie.gp5"]:
            tl = load_gp_file(FIXTURES / fname)
            assert tl.metadata.tuning == STANDARD_TUNING, f"{fname} tuning mismatch"

    def test_timestamps_monotonic(self):
        for fname in [
            "Slides.gp5", "notes.gp5", "Effects.gp5", "Tie.gp5",
            "Demo_v5.gp5", "canon.gp5",
        ]:
            tl = load_gp_file(FIXTURES / fname)
            timestamps = [n.timestamp_ms for n in tl.notes]
            for i in range(1, len(timestamps)):
                assert timestamps[i] >= timestamps[i - 1], (
                    f"{fname}: timestamp[{i}]={timestamps[i]} < [{i-1}]={timestamps[i-1]}"
                )

    def test_canon_tempo_changes_affect_timestamps(self):
        """Verify that tempo changes produce different ms values than constant tempo."""
        tl = load_gp_file(FIXTURES / "canon.gp5", track_index=0)
        last_note = tl.notes[-1]

        # At constant 90 BPM, the last note would be much later
        # With tempo changes (faster sections), it's compressed
        # Constant 90 BPM for the same tick would give ~598s
        # With tempo changes it's ~322s
        assert last_note.timestamp_ms < 400_000  # well under constant-tempo value

    def test_auto_select_guitar_track(self):
        # canon.gp5 has guitar track at index 0
        tl = load_gp_file(FIXTURES / "canon.gp5")
        assert tl.metadata.track_name == "Guitar Player"

    def test_explicit_track_index(self):
        tl = load_gp_file(FIXTURES / "Demo_v5.gp5", track_index=1)
        assert tl.metadata.track_name == "Solo Guitar"

    def test_duration_positive(self):
        for fname in ["Slides.gp5", "notes.gp5", "canon.gp5"]:
            tl = load_gp_file(FIXTURES / fname)
            assert tl.duration_ms > 0
            for note in tl.notes:
                assert note.duration_ms > 0, f"{fname}: note with 0 duration"


class TestTechniques:
    """Bends and slides survive the trip out of a GP file.

    Uses the checked-in fixtures rather than a generated file: they were
    written by Guitar Pro itself, so they exercise the encoding the app will
    actually meet rather than the one this project happens to write.
    """

    def test_shift_slide_marks_the_source_note(self):
        notes = load_gp_file(FIXTURES / "Slides.gp5").notes
        assert notes[0].slide_to_next
        # the target is the next note on that string, and carries no slide
        assert not notes[1].slide_to_next

    def test_slide_into_a_note_records_its_direction(self):
        notes = load_gp_file(FIXTURES / "Slides.gp5").notes
        from_below = [n for n in notes if n.slide_in > 0]
        from_above = [n for n in notes if n.slide_in < 0]
        assert from_below and from_above

    def test_slide_off_a_note_records_its_direction(self):
        notes = load_gp_file(FIXTURES / "Slides.gp5").notes
        assert [n for n in notes if n.slide_out > 0]
        assert [n for n in notes if n.slide_out < 0]

    def test_a_note_can_be_slid_into_and_out_of(self):
        notes = load_gp_file(FIXTURES / "Slides.gp5").notes
        assert [n for n in notes if n.slide_in and n.slide_out]

    def test_bend_depth_comes_through_in_semitones(self):
        notes = load_gp_file(FIXTURES / "Effects.gp5").notes
        bent = [n for n in notes if n.bend]
        assert bent, "Effects.gp5 is the fixture that has a bend"
        assert bent[0].bend_semitones == pytest.approx(4.0)

    def test_bend_curve_starts_at_zero_and_is_ordered(self):
        notes = load_gp_file(FIXTURES / "Effects.gp5").notes
        curve = [n for n in notes if n.bend][0].bend
        positions = [p for p, _ in curve]
        assert positions[0] == pytest.approx(0.0)
        assert positions == sorted(positions)
        assert max(positions) <= 1.0

    def test_plain_notes_carry_no_technique(self):
        """Guards the default: everything decorated is as wrong as nothing."""
        for note in load_gp_file(FIXTURES / "notes.gp5").notes:
            assert note.bend == ()
            assert not note.slide_to_next
            assert note.slide_in == 0 and note.slide_out == 0
            assert not note.dead
            assert not note.palm_mute


class TestMuting:
    """Palm mutes and dead notes come out of the file at all.

    Both were being dropped: a palm mute was drawn as a note that rings for
    its full written length, and a dead note as an ordinary note on the fret
    the tab uses to say where the hand damps -- which the player then could
    not hit, because damping a string produces no such pitch.
    """

    def test_a_dead_note_is_flagged_as_dead(self):
        notes = load_gp_file(FIXTURES / "Effects.gp5").notes
        assert [n for n in notes if n.dead], "Effects.gp5 has a dead note"

    def test_a_dead_note_is_still_an_event(self):
        """It is played, so it has to be in the timeline to be drawn and
        scored -- skipping it would silently drop notes from a riff."""
        notes = load_gp_file(FIXTURES / "Effects.gp5").notes
        dead = [n for n in notes if n.dead][0]
        assert dead.duration_ms > 0
        assert 1 <= dead.string <= 6

    def test_palm_mute_is_flagged(self):
        notes = load_gp_file(FIXTURES / "Effects.gp5").notes
        assert [n for n in notes if n.palm_mute], "Effects.gp5 has a palm mute"

    def test_palm_mute_does_not_change_the_pitch(self):
        """The picking hand chokes the note; it does not transpose it. Scoring
        therefore stays exactly as it is for an open note."""
        notes = load_gp_file(FIXTURES / "Effects.gp5").notes
        muted = [n for n in notes if n.palm_mute][0]
        open_same_fret = [
            n for n in notes
            if not n.palm_mute and n.string == muted.string
            and n.fret == muted.fret
        ]
        for note in open_same_fret:
            assert note.midi_note == muted.midi_note

    def test_the_two_are_independent(self):
        """A muted riff mixes them, so neither may imply the other."""
        notes = load_gp_file(FIXTURES / "Demo_v5.gp5").notes
        assert [n for n in notes if n.dead and not n.palm_mute]
        assert [n for n in notes if n.palm_mute and not n.dead]


class TestLegato:
    def test_hammer_marks_the_note_it_starts_from(self):
        """GP flags the source, the same way it flags a shift slide."""
        notes = load_gp_file(FIXTURES / "Effects.gp5").notes
        legato = [n for n in notes if n.hammer_to_next]
        assert legato, "Effects.gp5 is the fixture that has a hammer-on"
        assert legato[0].leads_into_next

    def test_a_slide_also_leads_into_the_next_note(self):
        notes = load_gp_file(FIXTURES / "Slides.gp5").notes
        assert notes[0].leads_into_next

    def test_a_plain_note_leads_into_nothing(self):
        for note in load_gp_file(FIXTURES / "notes.gp5").notes:
            assert not note.hammer_to_next
            assert not note.leads_into_next


class TestRepeatsAreActuallyPlayed:
    """A tab that lines up with a recording only because it repeats.

    Demo_v5.gp5 opens with |: over bars 0-2 and a first/second-time ending,
    which is exactly the structure that used to be dropped: the section played
    once and the second ending never at all.
    """

    def _plan(self):
        import guitarpro
        from pickhero.tabs.loader import (_bar_repeat, _build_tempo_map,
                                          repeat_order)
        song = guitarpro.parse(str(FIXTURES / "Demo_v5.gp5"))
        headers = list(song.measureHeaders)
        return headers, repeat_order([_bar_repeat(h) for h in headers])

    def test_the_repeated_section_is_played_twice(self):
        headers, order = self._plan()
        assert order[:8] == [0, 1, 2, 3, 0, 1, 2, 4]

    def test_no_written_bar_is_left_unplayed(self):
        headers, order = self._plan()
        assert set(order) == set(range(len(headers)))

    def test_the_song_is_longer_than_what_is_written(self):
        headers, order = self._plan()
        assert len(order) > len(headers)

    def test_the_measures_are_numbered_in_playing_order(self):
        """A repeated section is two passes on screen, so saying "bar 12"
        twice would make the weakest-section report name a place nobody can
        find."""
        tl = load_gp_file(FIXTURES / "Demo_v5.gp5", track_index=0)
        assert [m.index for m in tl.measures] == list(range(len(tl.measures)))
        assert all(b.start_ms >= a.start_ms
                   for a, b in zip(tl.measures, tl.measures[1:]))

    def test_the_backing_track_follows_the_same_plan(self):
        """Two walks of their own is the same drift one level down."""
        from pickhero.tabs.loader import extract_backing_track
        tl = load_gp_file(FIXTURES / "Demo_v5.gp5", track_index=0)
        backing = extract_backing_track(FIXTURES / "Demo_v5.gp5",
                                        exclude_track_indices={0})
        last = max(e.timestamp_ms for e in backing.events)
        assert abs(last - tl.duration_ms) < 2000.0

    def test_a_file_without_repeats_is_untouched(self):
        """The control: canon.gp5 has none, and must read exactly as before."""
        tl = load_gp_file(FIXTURES / "canon.gp5", track_index=0)
        assert len(tl) == 1489
