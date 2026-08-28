"""Hearing the part you are meant to play.

`B` plays what the band plays; this is the other half -- the written notes of
the track being played, as something to listen to while learning it. They are
separate toggles because they answer different questions, and somebody
learning a solo wants the second without the first.
"""

from pathlib import Path

import pygame
import pytest

from pickhero.audio.midi_playback import BackingTrack, MidiEvent
from pickhero.config import Config
from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline
from pickhero.ui.scrolling import PlayingScreen

SONG = Path(__file__).resolve().parent.parent / "songs" / "timing_test_100bpm.gp5"


def _track(count=4):
    return BackingTrack([MidiEvent(timestamp_ms=100.0 * i, channel=0,
                                   event_type=0x90, data1=40 + i, data2=90)
                         for i in range(count)])


class _FakePlayer:
    """A MidiPlayer that records what the transport did to it."""

    def __init__(self, track):
        self._track = track
        self.muted = False
        self.calls = []

    def open(self):
        return True

    def set_muted(self, muted):
        self.muted = muted
        self.calls.append(("mute", muted))

    def seek(self, ms):
        self.calls.append(("seek", ms))

    def pause(self):
        self.calls.append(("pause",))

    def update(self, ms):
        self.calls.append(("update", ms))

    def play_click(self, velocity=100):
        self.calls.append(("click",))

    def close(self):
        self.calls.append(("close",))


@pytest.fixture
def screen(monkeypatch):
    monkeypatch.setattr("pickhero.ui.scrolling.MidiPlayer", _FakePlayer)
    notes = [NoteEvent(timestamp_ms=60_000.0, duration_ms=400.0, midi_note=40,
                       string=6, fret=0, measure=0)]
    timeline = Timeline(notes, SongMetadata(title="x", tempo=100))
    s = PlayingScreen(timeline, config=Config(), backing_track=_track(),
                      guide_track=_track(), song_key="x")
    s._playback_ms = 10_000.0
    return s


class TestTheTwoAreSeparate:
    def test_it_starts_silent(self, screen):
        """Producing this part is the point of the app; hearing it play
        itself on the first run would teach the wrong thing."""
        assert screen._guide_muted is True
        assert screen._guide_player.muted is True

    def test_shift_b_turns_it_on(self, screen):
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_b, mod=pygame.KMOD_LSHIFT))
        assert screen._guide_muted is False
        assert screen._guide_player.muted is False

    def test_plain_b_still_only_touches_the_band(self, screen):
        before = screen._guide_muted
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_b, mod=0))
        assert screen._guide_muted is before
        assert screen._backing_muted is not (not screen._config.backing_track_enabled)

    def test_shift_b_does_not_touch_the_band(self, screen):
        before = screen._backing_muted
        screen.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_b, mod=pygame.KMOD_LSHIFT))
        assert screen._backing_muted is before

    def test_the_choice_is_remembered(self, screen):
        screen._toggle_guide_track()
        assert screen._config.guide_track_enabled is True
        screen._toggle_guide_track()
        assert screen._config.guide_track_enabled is False

    def test_both_are_named_in_the_footer(self, screen):
        footer = " ".join(screen._footer_lines())
        assert "B: backing" in footer and "Shift+B: my part" in footer

    def test_a_song_without_one_says_so_rather_than_nothing(self, monkeypatch):
        """A dash is the answer to "why does pressing it do nothing"."""
        monkeypatch.setattr("pickhero.ui.scrolling.MidiPlayer", _FakePlayer)
        timeline = Timeline([], SongMetadata(title="x", tempo=100))
        s = PlayingScreen(timeline, config=Config(), song_key="x")
        assert "Shift+B: my part —" in " ".join(s._footer_lines())


class TestTheyMoveTogether:
    """A guide that is a bar out is worse than no guide."""

    def _both(self, screen):
        return screen._midi_player, screen._guide_player

    def test_a_seek_reaches_both(self, screen):
        band, guide = self._both(screen)
        band.calls.clear(); guide.calls.clear()
        screen.seek(20_000.0)
        assert any(c[0] == "seek" for c in band.calls)
        assert any(c[0] == "seek" for c in guide.calls)

    def test_a_frame_updates_both(self, screen):
        band, guide = self._both(screen)
        screen._playing = True
        band.calls.clear(); guide.calls.clear()
        screen.update()
        assert any(c[0] == "update" for c in band.calls)
        assert any(c[0] == "update" for c in guide.calls)

    def test_pausing_reaches_both(self, screen):
        band, guide = self._both(screen)
        screen._playing = True
        band.calls.clear(); guide.calls.clear()
        screen.toggle_play()
        assert ("pause",) in band.calls and ("pause",) in guide.calls

    def test_a_loop_turn_reaches_both(self, screen):
        band, guide = self._both(screen)
        screen._playing = True
        screen._loop_enabled = True
        screen._loop_start_ms = 1000.0
        screen._loop_end_ms = 2000.0
        screen._playback_ms = 2500.0
        screen._last_tick = None
        band.calls.clear(); guide.calls.clear()
        screen.update()
        assert any(c == ("seek", 1000.0) for c in band.calls)
        assert any(c == ("seek", 1000.0) for c in guide.calls)

    def test_leaving_the_song_closes_both(self, screen):
        band, guide = self._both(screen)
        screen.stop_audio()
        assert ("close",) in band.calls and ("close",) in guide.calls
        assert screen._guide_player is None


class TestWhatIsInEach:
    pytestmark = pytest.mark.skipif(not SONG.exists(), reason="song missing")

    def test_the_guide_holds_the_chosen_track_and_the_backing_the_rest(self):
        from pickhero.tabs.loader import (extract_backing_track, list_tracks,
                                          load_gp_file)
        timeline = load_gp_file(SONG)
        chosen = timeline.metadata.track_index
        every = {t["index"] for t in list_tracks(SONG)}
        band = extract_backing_track(SONG, exclude_track_indices={chosen})
        guide = extract_backing_track(SONG, exclude_track_indices=every - {chosen})
        assert len(guide) > 0 and len(band) > 0
        # They are different halves of the same file, not the same events.
        assert {e.data1 for e in guide.events} != {e.data1 for e in band.events}
