"""A recording as a backing track, kept in step with the scrolling tab.

The MIDI backing is generated from the same timeline as the notes and can be
told exactly where to be. A recording has its own clock, running in the sound
card, and the only control available is "start from here" -- so what is tested
here is the correcting, the switching, and the two limits that make the whole
feature honest: a recording cannot be slowed down, and its encoder padding
cannot be derived.
"""

import pygame
import pytest

from pickhero.audio.mp3_playback import (
    MIN_RESYNC_GAP_MS, RESYNC_MS, Mp3Player,
)
from pickhero.config import Config
from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline
from pickhero.ui.scrolling import PlayingScreen


def _timeline():
    notes = [NoteEvent(timestamp_ms=1000.0, duration_ms=400.0, midi_note=40,
                       string=6, fret=0, measure=0)]
    return Timeline(notes, SongMetadata(title="Test", tempo=100))


class _FakeMusic:
    """pygame.mixer.music, with a clock the test drives."""

    def __init__(self):
        self.elapsed = 0
        self.started_at: list[float] = []
        self.playing = False

    def stop(self):
        self.playing = False

    def play(self, loops=0, start=0.0):
        self.started_at.append(start)
        self.playing = True
        self.elapsed = 0

    def get_pos(self):
        return self.elapsed if self.playing else -1

    def load(self, path):
        pass

    def unload(self):
        pass


@pytest.fixture
def music(monkeypatch, tmp_path):
    fake = _FakeMusic()
    monkeypatch.setattr(pygame.mixer, "music", fake)
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: True)
    return fake


@pytest.fixture
def song_file(tmp_path):
    path = tmp_path / "backing.mp3"
    path.write_bytes(b"not really an mp3, but it exists")
    return path


class TestFollowingTheSong:
    def test_it_starts_from_the_position_it_is_given(self, music, song_file):
        player = Mp3Player(song_file)
        assert player.open()
        player.update(4000.0)
        assert music.started_at == [4.0]

    def test_small_drift_is_left_alone(self, music, song_file):
        """A re-seek is audible, so correcting an error nobody can hear costs
        more than the error does."""
        player = Mp3Player(song_file)
        player.open()
        player.update(0.0)
        music.elapsed = 1000
        player.update(1000.0 + RESYNC_MS / 2)
        assert len(music.started_at) == 1

    def test_real_drift_is_corrected(self, music, song_file):
        player = Mp3Player(song_file)
        player.open()
        player.update(0.0)
        music.elapsed = 1000
        player.update(1000.0 + RESYNC_MS * 2)
        assert len(music.started_at) == 2

    def test_it_will_not_correct_twice_in_quick_succession(self, music, song_file):
        """A file whose clock genuinely runs at a different rate would stutter
        continuously instead of drifting quietly, and quiet drift is the
        lesser fault."""
        player = Mp3Player(song_file)
        player.open()
        player.update(0.0)
        music.elapsed = 100
        player.update(100.0 + RESYNC_MS * 2)
        seeks = len(music.started_at)
        music.elapsed = 150
        player.update(150.0 + RESYNC_MS * 2)
        assert len(music.started_at) == seeks

    def test_it_corrects_again_once_the_gap_has_passed(self, music, song_file):
        player = Mp3Player(song_file)
        player.open()
        player.update(0.0)
        music.elapsed = 100
        player.update(100.0 + RESYNC_MS * 2)
        seeks = len(music.started_at)
        later = 100.0 + MIN_RESYNC_GAP_MS + 100
        music.elapsed = int(later)
        player.update(later + RESYNC_MS * 2)
        assert len(music.started_at) == seeks + 1

    def test_it_stays_silent_during_the_count_in(self, music, song_file):
        player = Mp3Player(song_file)
        player.open()
        player.update(-2000.0)
        assert music.started_at == []

    def test_a_missing_file_is_reported_not_raised(self, tmp_path):
        player = Mp3Player(tmp_path / "gone.mp3")
        assert not player.open()
        assert "not found" in player.error.lower()

    def test_pausing_remembers_where_the_song_was(self, music, song_file):
        player = Mp3Player(song_file)
        player.open()
        player.update(2000.0)
        music.elapsed = 500
        player.pause()
        assert player.position_ms() == pytest.approx(2500.0)


class TestBothBackingsAtOnce:
    """The player's own requirement, and the reason B does not cycle.

    Lining a recording up against the click means hearing both, then
    switching one off. A single control that goes off -> MIDI -> recording
    makes the state the job needs unreachable.
    """

    def _screen(self, tmp_path, monkeypatch):
        song = tmp_path / "backing.mp3"
        song.write_bytes(b"x")
        config = Config()
        config.set_mp3_path_for("song", str(song))
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        monkeypatch.setattr(Mp3Player, "ready", property(lambda self: True))
        screen = PlayingScreen(_timeline(), config=config, song_key="song")
        return screen

    def test_the_two_are_switched_independently(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        before = screen._backing_muted
        screen._toggle_mp3_backing()
        assert screen._backing_muted == before

    def test_the_recording_remembers_being_switched_off(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        screen._toggle_mp3_backing()
        assert screen._config.mp3_backing_enabled is False


class TestPerSongSettings:
    def test_the_path_is_remembered_for_this_song_only(self):
        config = Config()
        config.set_mp3_path_for("song_a", "/music/a.mp3")
        assert config.mp3_path_for("song_a") == "/music/a.mp3"
        assert config.mp3_path_for("song_b") == ""

    def test_the_offset_has_no_global_fallback(self):
        """Encoder padding belongs to the file, not to the setup, so one
        song's value must never be borrowed by another."""
        config = Config()
        config.set_mp3_offset_for("song_a", -120.0)
        assert config.mp3_offset_for("song_a") == -120.0
        assert config.mp3_offset_for("song_b") == 0.0

    def test_a_positive_offset_makes_the_recording_sound_later(self):
        config = Config()
        config.set_mp3_offset_for("song", 200.0)
        screen = PlayingScreen(_timeline(), config=config, song_key="song")
        assert screen._mp3_ms(5000.0) == pytest.approx(4800.0)

    def test_the_offset_is_clamped(self):
        config = Config()
        screen = PlayingScreen(_timeline(), config=config, song_key="song")
        for _ in range(200):
            screen._adjust_mp3_offset(50.0)
        assert config.mp3_offset_for("song") <= 400.0


class TestSlowedPractice:
    """A recording cannot be slowed down, and must say so rather than drift."""

    def _screen(self, tmp_path, monkeypatch, tempo):
        song = tmp_path / "backing.mp3"
        song.write_bytes(b"x")
        config = Config()
        config.set_mp3_path_for("song", str(song))
        config.tempo_factor = tempo
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        monkeypatch.setattr(Mp3Player, "ready", property(lambda self: True))
        return PlayingScreen(_timeline(), config=config, song_key="song")

    def test_it_plays_at_full_speed(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, 1.0)
        assert screen._mp3_plays()

    def test_it_is_silent_below_full_speed(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, 0.8)
        assert not screen._mp3_plays()

    def test_the_hud_says_why_it_is_silent(self, tmp_path, monkeypatch):
        """Silent with no explanation reads as broken, and the player would go
        looking for a fault that is not there."""
        screen = self._screen(tmp_path, monkeypatch, 0.8)
        text = screen._mp3_hud_text()
        assert "full speed" in text and "MIDI" in text

    def test_a_missing_file_is_named_on_screen(self, tmp_path):
        config = Config()
        config.set_mp3_path_for("song", str(tmp_path / "moved.mp3"))
        screen = PlayingScreen(_timeline(), config=config, song_key="song")
        assert "not found" in screen._mp3_hud_text().lower()

    def test_a_song_with_no_recording_says_nothing(self):
        screen = PlayingScreen(_timeline(), config=Config(), song_key="song")
        assert screen._mp3_hud_text() == ""


class TestWhenTheRecordingRunsOut:
    """A backing track is often shorter than the practice loop around it."""

    def test_it_is_not_restarted_once_it_has_ended(self, music, song_file):
        player = Mp3Player(song_file)
        player.open()
        player.update(0.0)
        music.playing = False           # the mixer reached the end
        player.update(1000.0)
        started = len(music.started_at)
        player.update(2000.0)
        assert len(music.started_at) == started

    def test_looping_back_lets_it_play_again(self, music, song_file):
        player = Mp3Player(song_file)
        player.open()
        player.update(30_000.0)
        music.playing = False
        player.update(31_000.0)
        started = len(music.started_at)
        player.update(0.0)              # loop marker jumped back
        assert len(music.started_at) == started + 1

    def test_an_explicit_seek_always_plays_again(self, music, song_file):
        player = Mp3Player(song_file)
        player.open()
        player.update(0.0)
        music.playing = False
        player.update(1000.0)
        started = len(music.started_at)
        player.seek(5000.0)
        assert len(music.started_at) == started + 1


class TestFailureIsNamed:
    """A backing track that silently does not play is indistinguishable from
    a feature that does not work."""

    def test_a_decoder_that_cannot_seek_says_so(self, music, song_file, monkeypatch):
        player = Mp3Player(song_file)
        player.open()

        def refuse(loops=0, start=0.0):
            raise pygame.error("Position not implemented for music type")

        monkeypatch.setattr(music, "play", refuse)
        player.update(5000.0)
        assert "middle" in player.error

    def test_the_screen_shows_that_error(self, tmp_path, monkeypatch):
        song = tmp_path / "backing.mp3"
        song.write_bytes(b"x")
        config = Config()
        config.set_mp3_path_for("song", str(song))
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        screen = PlayingScreen(_timeline(), config=config, song_key="song")
        screen._mp3_player.error = "This file cannot start from the middle"
        assert "middle" in screen._mp3_hud_text()
