"""Tests for pickhero.audio.midi_playback and backing track extraction."""

from pathlib import Path

import pytest

from pickhero.audio.midi_playback import (
    NOTE_ON, NOTE_OFF, PROGRAM_CHANGE, BackingTrack, MidiEvent,
)
from pickhero.tabs.loader import extract_backing_track, load_gp_file

FIXTURES = Path(__file__).parent / "fixtures"


class TestMidiEvent:
    def test_creation(self):
        ev = MidiEvent(100.0, 0, NOTE_ON, 60, 80)
        assert ev.timestamp_ms == 100.0
        assert ev.channel == 0
        assert ev.event_type == NOTE_ON
        assert ev.data1 == 60
        assert ev.data2 == 80

    def test_frozen(self):
        ev = MidiEvent(0.0, 0, NOTE_ON, 60, 80)
        with pytest.raises(AttributeError):
            ev.timestamp_ms = 1.0

    def test_ordering(self):
        a = MidiEvent(100.0, 0, NOTE_ON, 60, 80)
        b = MidiEvent(200.0, 0, NOTE_ON, 60, 80)
        assert a < b
        assert sorted([b, a]) == [a, b]


class TestBackingTrack:
    def _make_track(self):
        events = [
            MidiEvent(0.0, 0, PROGRAM_CHANGE, 33, 0),
            MidiEvent(0.0, 9, NOTE_ON, 36, 100),
            MidiEvent(100.0, 0, NOTE_ON, 40, 80),
            MidiEvent(200.0, 9, NOTE_OFF, 36, 0),
            MidiEvent(300.0, 0, NOTE_OFF, 40, 0),
            MidiEvent(500.0, 0, NOTE_ON, 45, 70),
            MidiEvent(600.0, 0, NOTE_OFF, 45, 0),
        ]
        return BackingTrack(events)

    def test_len(self):
        track = self._make_track()
        assert len(track) == 7

    def test_empty(self):
        track = BackingTrack()
        assert len(track) == 0
        assert track.get_events_until(1000.0) == []

    def test_get_events_until_advances_cursor(self):
        track = self._make_track()
        batch1 = track.get_events_until(150.0)
        # Should get events at 0, 0, 100
        assert len(batch1) == 3
        assert batch1[0].event_type == PROGRAM_CHANGE
        assert batch1[2].timestamp_ms == 100.0

        batch2 = track.get_events_until(350.0)
        # Should get events at 200, 300
        assert len(batch2) == 2
        assert batch2[0].timestamp_ms == 200.0
        assert batch2[1].timestamp_ms == 300.0

    def test_get_events_until_no_events(self):
        track = self._make_track()
        track.get_events_until(600.0)  # consume all
        assert track.get_events_until(1000.0) == []

    def test_seek_resets_cursor(self):
        track = self._make_track()
        track.get_events_until(600.0)  # consume all
        track.seek(100.0)
        batch = track.get_events_until(250.0)
        assert len(batch) == 2  # events at 100, 200

    def test_seek_to_zero(self):
        track = self._make_track()
        track.get_events_until(300.0)
        track.seek(0.0)
        batch = track.get_events_until(0.0)
        # Two events at t=0
        assert len(batch) == 2

    def test_get_program_changes_before(self):
        track = self._make_track()
        pcs = track.get_program_changes_before(500.0)
        assert len(pcs) == 1
        assert pcs[0].event_type == PROGRAM_CHANGE
        assert pcs[0].data1 == 33

    def test_program_change_at_zero_counts_at_zero(self):
        """Seeking to the start must still set the instruments.

        This used to assert the opposite. "Before" was implemented strictly,
        so a program change sitting at t=0 -- which is where every one of them
        sits -- was skipped whenever the song was seeked to its beginning, and
        melodic backing tracks played on whatever the synth already had. The
        question the caller is asking is which instruments apply AT this
        position, and one starting exactly here does.
        """
        track = self._make_track()
        pcs = track.get_program_changes_before(0.0)
        assert len(pcs) == 1
        assert pcs[0].data1 == 33

    def test_negative_position_still_sets_instruments(self):
        """A count-in puts playback before zero; instruments still apply."""
        track = self._make_track()
        assert len(track.get_program_changes_before(-2400.0)) == 1


class TestExtractBackingTrack:
    def test_single_guitar_track_produces_empty(self):
        """Slides.gp5 has only one guitar track — backing should be empty."""
        bt = extract_backing_track(FIXTURES / "Slides.gp5")
        assert len(bt) == 0

    def test_demo_v5_has_backing_events(self):
        """Demo_v5.gp5 has 5 tracks (2 guitar, bass, keys, drums)."""
        bt = extract_backing_track(FIXTURES / "Demo_v5.gp5")
        assert len(bt) > 0

    def test_demo_v5_exclude_specific_track(self):
        """Exclude only track 0, other tracks including guitar track 1 become backing."""
        bt = extract_backing_track(
            FIXTURES / "Demo_v5.gp5", exclude_track_indices={0},
        )
        assert len(bt) > 0

    def test_events_sorted(self):
        bt = extract_backing_track(FIXTURES / "Demo_v5.gp5")
        timestamps = [e.timestamp_ms for e in bt.events]
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1]

    def test_has_program_changes(self):
        """Non-percussion tracks should have program change events."""
        bt = extract_backing_track(FIXTURES / "Demo_v5.gp5")
        pcs = [e for e in bt.events if e.event_type == PROGRAM_CHANGE]
        assert len(pcs) > 0

    def test_note_on_off_pairs(self):
        """Every note_on should have a corresponding note_off."""
        bt = extract_backing_track(FIXTURES / "Demo_v5.gp5")
        on_count = sum(1 for e in bt.events if e.event_type == NOTE_ON)
        off_count = sum(1 for e in bt.events if e.event_type == NOTE_OFF)
        assert on_count == off_count
        assert on_count > 0

    def test_canon_has_many_backing_events(self):
        """canon.gp5 has 9 tracks, 4 guitar — should have lots of backing."""
        bt = extract_backing_track(FIXTURES / "canon.gp5")
        assert len(bt) > 100

    def test_channels_in_valid_range(self):
        bt = extract_backing_track(FIXTURES / "Demo_v5.gp5")
        for event in bt.events:
            assert 0 <= event.channel <= 15

    def test_track_index_recorded_in_metadata(self):
        """load_gp_file should record the selected track index."""
        tl = load_gp_file(FIXTURES / "Demo_v5.gp5")
        assert tl.metadata.track_index == 0  # first guitar track

        tl2 = load_gp_file(FIXTURES / "Demo_v5.gp5", track_index=1)
        assert tl2.metadata.track_index == 1


class _DeadOutput:
    """What pygame hands back when a device refuses to open: an object that
    looks fine and throws on every write."""

    def __init__(self):
        self.writes = 0

    def write_short(self, *args):
        self.writes += 1
        raise Exception("midi Output not open.")


class _LiveOutput:
    def __init__(self):
        self.messages = []

    def write_short(self, status, data1, data2):
        self.messages.append((status, data1, data2))


class TestMidiPlayerSurvivesABadDevice:
    """A backing track falling silent is a nuisance; the app dying is not."""

    def _player(self, output):
        from pickhero.audio.midi_playback import MidiPlayer
        player = MidiPlayer(self._track())
        player._output = output
        return player

    def _track(self):
        return BackingTrack([
            MidiEvent(0.0, 0, PROGRAM_CHANGE, 33, 0),
            MidiEvent(100.0, 0, NOTE_ON, 40, 80),
            MidiEvent(300.0, 0, NOTE_OFF, 40, 0),
        ])

    def test_update_does_not_raise_on_a_dead_output(self):
        player = self._player(_DeadOutput())
        player.update(500.0)          # used to crash the whole app

    def test_seek_does_not_raise_on_a_dead_output(self):
        self._player(_DeadOutput()).seek(0.0)

    def test_click_does_not_raise_on_a_dead_output(self):
        self._player(_DeadOutput()).play_click()

    def test_a_dead_output_is_dropped_rather_than_retried_forever(self):
        dead = _DeadOutput()
        player = self._player(dead)
        player.update(500.0)
        player.update(1000.0)
        assert dead.writes == 1
        assert player._output is None

    def test_a_working_output_still_gets_its_events(self):
        live = _LiveOutput()
        player = self._player(live)
        player.update(500.0)
        assert len(live.messages) == 3

    def test_muting_stops_the_notes(self):
        """Silencing still sends all-notes-off, so only NOTE_ON is checked."""
        live = _LiveOutput()
        player = self._player(live)
        player.set_muted(True)
        live.messages.clear()
        player.update(500.0)
        assert not any(status & 0xF0 == NOTE_ON for status, _, _ in live.messages)

    def test_close_leaves_the_shared_output_open_for_the_next_song(self):
        """Closing it is what made every song after the first one silent."""
        import pickhero.audio.midi_playback as mp
        live = _LiveOutput()
        mp._SHARED_OUTPUT = live
        player = self._player(live)
        player.close()
        assert mp._SHARED_OUTPUT is live
        mp._SHARED_OUTPUT = None


class TestSeekingDoesNotWalkTheWholeSong:
    """A seek re-sends the instrument assignments, and it did that by copying
    every event up to that point and scanning it -- 0.87 ms three minutes into
    a full arrangement, per player, and a held arrow key is 25 seeks a
    second."""

    def _big(self):
        from pickhero.audio.midi_playback import PROGRAM_CHANGE
        events = [MidiEvent(timestamp_ms=0.0, channel=c,
                            event_type=PROGRAM_CHANGE, data1=30)
                  for c in range(6)]
        t = 0.0
        for _ in range(3000):
            for ch in range(6):
                events.append(MidiEvent(timestamp_ms=t, channel=ch,
                                        event_type=0x90, data1=40 + ch, data2=90))
            t += 111.0
        return BackingTrack(events)

    def test_it_costs_the_same_at_the_end_as_at_the_start(self):
        import time
        track = self._big()
        def cost(at):
            best = 1e9
            for _ in range(5):
                start = time.perf_counter()
                for _ in range(200):
                    track.get_program_changes_before(at)
                best = min(best, time.perf_counter() - start)
            return best
        assert cost(300_000.0) < cost(5_000.0) * 3

    def test_it_still_returns_the_latest_per_channel(self):
        from pickhero.audio.midi_playback import PROGRAM_CHANGE
        track = BackingTrack([
            MidiEvent(timestamp_ms=0.0, channel=0, event_type=PROGRAM_CHANGE,
                      data1=30),
            MidiEvent(timestamp_ms=1000.0, channel=0,
                      event_type=PROGRAM_CHANGE, data1=48),
            MidiEvent(timestamp_ms=5000.0, channel=0,
                      event_type=PROGRAM_CHANGE, data1=60),
        ])
        got = track.get_program_changes_before(2000.0)
        assert [e.data1 for e in got] == [48]

    def test_one_per_channel(self):
        from pickhero.audio.midi_playback import PROGRAM_CHANGE
        track = BackingTrack([
            MidiEvent(timestamp_ms=0.0, channel=c, event_type=PROGRAM_CHANGE,
                      data1=30 + c) for c in range(4)])
        assert len(track.get_program_changes_before(1000.0)) == 4

    def test_seeking_to_the_very_start_still_sends_them(self):
        """They sit at t=0, and excluding them left every backing track
        playing on whatever the synth happened to have on that channel."""
        from pickhero.audio.midi_playback import PROGRAM_CHANGE
        track = BackingTrack([MidiEvent(timestamp_ms=0.0, channel=0,
                                        event_type=PROGRAM_CHANGE, data1=30)])
        assert len(track.get_program_changes_before(0.0)) == 1
        assert len(track.get_program_changes_before(-2000.0)) == 1


class TestTheFallbackToTheBuiltInSynth:
    """*"Midi output B und Sh+B gehen nicht."*

    Windows ships a wavetable synth as a MIDI destination and a clean macOS
    ships none, so on that machine there was nothing to open and both
    backings were silent. The REAL port still wins wherever there is one --
    it sounds better than anything written here, so Windows is bit-for-bit
    what it was and this only ever fills a hole.
    """

    def _no_ports(self, monkeypatch):
        from pickhero.audio import midi_playback as mp
        monkeypatch.setattr(mp, "_SHARED_OUTPUT", None)
        monkeypatch.setattr(mp, "_SHARED_OUTPUT_NAME", "")
        monkeypatch.setattr(mp, "list_midi_outputs", lambda: [])
        monkeypatch.setattr(mp, "_init_midi_once", lambda: True)
        return mp

    def test_no_port_gives_the_synth_instead_of_nothing(self, monkeypatch):
        from pickhero.audio import softsynth
        mp = self._no_ports(monkeypatch)
        out = mp._open_shared_output()
        assert isinstance(out, softsynth.SoftSynth)

    def test_and_the_run_log_says_no_device_was_involved(self, monkeypatch):
        """A log naming a port that does not exist cannot say why a backing
        sounds different on one of the player's two laptops."""
        mp = self._no_ports(monkeypatch)
        mp._open_shared_output()
        assert "no MIDI device" in mp.output_name()

    def test_a_real_port_is_handed_straight_through(self, monkeypatch):
        """Asked for by NAME rather than by type, so this module does not
        import the synth to hand out one of its views -- and a real device,
        which has no `port`, is passed through untouched."""
        from pickhero.audio import midi_playback as mp

        class _Port:
            def write_short(self, *a):
                pass

        port = _Port()
        assert mp._player_port(port, "guide") is port
        assert mp._player_port(None, "guide") is None

    def test_the_synth_gives_each_player_its_own_view(self, monkeypatch):
        """The guide and the band are extracted by the same code with the
        track filter inverted, so the guide's notes arrive on the CHANNEL the
        band would have used. Only the object can say which part they are."""
        mp = self._no_ports(monkeypatch)
        synth = mp._open_shared_output()
        assert mp._player_port(synth, "guide").role == "guide"
        assert mp._player_port(synth, "band").role == "band"

    def test_opening_a_player_really_pre_renders_its_notes(self, monkeypatch):
        """A cold six-string chord on both parts costs 6.95 ms of a 16.7 ms
        frame, so the notes are rendered at song load instead. A feature
        nothing calls is a feature that ships doing nothing, which this
        project has shipped four times -- so this drives `open()` rather
        than `warm()`.
        """
        mp = self._no_ports(monkeypatch)
        track = BackingTrack([
            MidiEvent(timestamp_ms=0.0, channel=0, event_type=0x90,
                      data1=40 + i, data2=90) for i in range(5)])
        player = mp.MidiPlayer(track, role="guide")
        assert player.open()
        synth = mp._SHARED_OUTPUT
        assert len(synth._sounds) == 5
        # And a second open renders nothing again: they are already there.
        assert synth.warm(track, role="guide") == 0

    def test_a_player_whose_warm_raises_still_opens(self, monkeypatch):
        """The band is a nuisance to lose; the app dying is not -- the rule
        `MidiPlayer._send` already follows."""
        mp = self._no_ports(monkeypatch)

        def explode(self, track):
            raise RuntimeError("no")

        monkeypatch.setattr(
            "pickhero.audio.softsynth._RolePort.warm", explode)
        player = mp.MidiPlayer(BackingTrack([]), role="band")
        assert player.open()

    def test_shift_a_reaches_the_synth_through_the_module(self, monkeypatch):
        """`panic()` is a module function, which is what makes it work: a
        player dropped without being closed still has its notes sounding and
        by definition nothing is tracking them.

        It writes to the SYNTH rather than to a player's view, so the
        role-less message has to stop every part. Getting that the other way
        round is a Shift+A that silences the band and leaves the hum.
        """
        mp = self._no_ports(monkeypatch)
        synth = mp._open_shared_output()
        mp._player_port(synth, "band").write_short(0x90, 40, 90)
        mp._player_port(synth, "guide").write_short(0x90, 52, 90)
        assert len(synth._playing) == 2
        assert mp.panic() is True
        assert synth._playing == {}
