"""A recording as a backing track, kept in step with the scrolling tab.

The MIDI backing is generated from the same timeline as the notes and can be
told exactly where to be. A recording has its own clock, running in the sound
card, and the only control available is "start from here" -- so what is tested
here is the correcting, the switching, and the two limits that make the whole
feature honest: a recording cannot be slowed down, and its encoder padding
cannot be derived.
"""

import threading

import pygame
import pytest

from pickhero.audio import timestretch

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

    def test_the_offset_reaches_far_enough_for_a_real_intro(self):
        """Half a second was the MIDI backing's range and it is nowhere near
        enough here: a recording can have a count-in, an intro or studio
        silence before the first beat."""
        config = Config()
        screen = PlayingScreen(_timeline(), config=config, song_key="song")
        for _ in range(10):
            screen._adjust_mp3_offset(-1000.0)
        assert config.mp3_offset_for("song") == -10_000.0

    def test_the_offset_reaches_a_solo_four_minutes_into_a_track(self):
        """A tab is not always the whole song. A GP file holding only the solo
        has to be lined up against a recording that plays four minutes of
        music first, in either direction."""
        config = Config()
        screen = PlayingScreen(_timeline(), config=config, song_key="song")
        for _ in range(30):
            screen._adjust_mp3_offset(-10_000.0)
        assert config.mp3_offset_for("song") == -300_000.0

    def test_the_offset_is_still_clamped_somewhere(self):
        config = Config()
        screen = PlayingScreen(_timeline(), config=config, song_key="song")
        for _ in range(200):
            screen._adjust_mp3_offset(10_000.0)
        assert config.mp3_offset_for("song") == 480_000.0


class TestReachingTheOffsetWithTheKeys:
    """Three steps on one pair of keys, because the hands are on the guitar
    and a second pair would not be found."""

    def _screen(self, tmp_path, monkeypatch):
        config = Config()
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        monkeypatch.setattr(Mp3Player, "ready", property(lambda self: True))
        return PlayingScreen(_timeline(), config=config, song_key="song")

    def _nudge(self, screen, mods):
        screen._nudge_backing(1, mods)

    def test_shift_moves_ten_milliseconds(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        self._nudge(screen, pygame.KMOD_SHIFT)
        assert screen._mp3_offset() == pytest.approx(10.0)

    def test_ctrl_moves_a_second(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        self._nudge(screen, pygame.KMOD_CTRL)
        assert screen._mp3_offset() == pytest.approx(1000.0)

    def test_ctrl_and_shift_together_move_ten_seconds(self, tmp_path, monkeypatch):
        """Tested before Ctrl alone, or the widest step is unreachable."""
        screen = self._screen(tmp_path, monkeypatch)
        self._nudge(screen, pygame.KMOD_CTRL | pygame.KMOD_SHIFT)
        assert screen._mp3_offset() == pytest.approx(10_000.0)

    def test_plain_keys_move_the_midi_backing_not_the_recording(
            self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        self._nudge(screen, 0)
        assert screen._mp3_offset() == 0.0
        assert screen._backing_offset() == pytest.approx(10.0)

    def test_alt_moves_the_midi_backing_a_second(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        self._nudge(screen, pygame.KMOD_ALT)
        assert screen._backing_offset() == pytest.approx(1000.0)

    def test_the_midi_backing_reaches_ten_seconds(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        for _ in range(20):
            self._nudge(screen, pygame.KMOD_ALT)
        assert screen._backing_offset() == pytest.approx(10_000.0)


class TestTheOffsetIsReadable:
    """A number nobody can check against a player's time display is not a
    reading."""

    def test_milliseconds_while_it_is_a_sync(self):
        from pickhero.ui.scrolling import _offset_text
        assert _offset_text(-120.0) == "-120 ms"

    def test_seconds_while_it_is_an_intro(self):
        from pickhero.ui.scrolling import _offset_text
        assert _offset_text(2500.0) == "+2.50 s"

    def test_minutes_once_the_tab_is_only_the_solo(self):
        from pickhero.ui.scrolling import _offset_text
        assert _offset_text(-192_400.0) == "-3:12.4 min"


class TestSlowedPractice:
    """Practising slowly against the real recording, not against a click.

    Playing the file slower would drop its pitch four semitones, so a copy is
    stretched instead. Building one takes seconds, so what matters here is
    that the recording stays SILENT until the right copy is loaded -- playing
    on at full speed under a song running at 80 % puts it a bar out within
    seconds, which is worse than silence.
    """

    def _screen(self, tmp_path, monkeypatch, tempo):
        song = tmp_path / "backing.mp3"
        song.write_bytes(b"x")
        config = Config()
        config.set_mp3_path_for("song", str(song))
        config.set_tempo_factor_for("song", tempo)
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        monkeypatch.setattr(Mp3Player, "ready", property(lambda self: True))
        screen = PlayingScreen(_timeline(), config=config, song_key="song")
        screen._playing = True
        return screen

    def test_it_plays_at_full_speed(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, 1.0)
        assert screen._mp3_plays()

    def test_it_is_silent_until_the_stretched_copy_is_there(
            self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, 0.8)
        assert not screen._mp3_plays()

    def test_the_hud_says_it_is_being_fitted(self, tmp_path, monkeypatch):
        """Silence with no explanation reads as broken, and the player would go
        looking for a fault that is not there."""
        screen = self._screen(tmp_path, monkeypatch, 0.8)
        assert "80 % speed" in screen._mp3_hud_text()

    def test_the_stretched_copy_is_played_once_it_lands(
            self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, 0.8)
        stretched = tmp_path / "backing_080.wav"
        stretched.write_bytes(b"x")
        screen._mp3_stretch_done = (0.8, screen._mp3_path(), str(stretched))
        screen._ensure_mp3_source()
        assert screen._mp3_plays()
        # A song millisecond costs a quarter more file milliseconds at 80 %,
        # or the recording would run away from the notes.
        assert screen._mp3_player.time_scale == pytest.approx(1.25)
        assert screen._mp3_player.path == stretched

    def test_going_back_to_full_speed_returns_to_the_original(
            self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, 0.8)
        screen._mp3_stretch_done = (0.8, screen._mp3_path(),
                                    str(tmp_path / "backing_080.wav"))
        screen._ensure_mp3_source()
        screen._tempo_factor = 1.0
        screen._ensure_mp3_source()
        assert screen._mp3_player.path.name == "backing.mp3"
        assert screen._mp3_player.time_scale == pytest.approx(1.0)

    def test_a_file_that_cannot_be_stretched_is_named_on_screen(
            self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, 0.8)
        screen._mp3_stretch_failed = (0.8, screen._mp3_path(),
                                      "cannot be slowed down (X) — "
                                      "convert it to OGG or WAV")
        assert "convert it" in screen._mp3_hud_text()
        assert not screen._mp3_plays()

    def test_a_new_recording_does_not_inherit_the_old_stretch(
            self, tmp_path, monkeypatch):
        """The stretch belongs to one file. Picking another one and getting the
        first one's audio would be silent-running nonsense."""
        screen = self._screen(tmp_path, monkeypatch, 0.8)
        screen._mp3_stretch_done = (0.8, str(tmp_path / "another.mp3"),
                                    str(tmp_path / "another_080.wav"))
        screen._ensure_mp3_source()
        assert not screen._mp3_plays()

    def test_the_tempo_key_does_not_wait_for_the_stretch(
            self, tmp_path, monkeypatch):
        """Seconds of work in the game loop is a frozen app, which this project
        has already shipped once (the seek that reopened the input device)."""
        ran_in = []
        monkeypatch.setattr(
            timestretch, "build",
            lambda path, tempo, cache, progress=None: ran_in.append(
                threading.current_thread()) or (tmp_path / "x.wav"))
        screen = self._screen(tmp_path, monkeypatch, 0.8)
        screen._ensure_mp3_source()
        screen._mp3_stretch_thread.join(timeout=5)
        assert ran_in and ran_in[0] is not threading.main_thread()

    def test_the_player_is_told_how_far_along_it_is(self, tmp_path, monkeypatch):
        """Five to twenty seconds of silence with nothing moving on screen is
        indistinguishable from a feature that does not work."""
        screen = self._screen(tmp_path, monkeypatch, 0.8)
        screen._mp3_stretch_progress = 0.42
        assert "42%" in screen._mp3_hud_text()

    def test_a_build_nobody_wants_any_more_is_abandoned(
            self, tmp_path, monkeypatch):
        """Stepping the tempo down three times must not build three copies
        before reaching the one that was asked for."""
        reports = []
        monkeypatch.setattr(
            timestretch, "build",
            lambda path, tempo, cache, progress=None:
                reports.append(progress(0.5)) or (tmp_path / "x.wav"))
        screen = self._screen(tmp_path, monkeypatch, 0.8)
        screen._ensure_mp3_source()
        screen._mp3_stretch_thread.join(timeout=5)
        assert reports == [True]

        reports.clear()
        screen._tempo_factor = 0.6           # the player moved on mid-build
        screen._mp3_stretch_wanted = None
        screen._start_mp3_stretch(0.8)
        screen._mp3_stretch_thread.join(timeout=5)
        assert reports == [False]

    def test_a_failed_speed_is_not_attempted_again_every_frame(
            self, tmp_path, monkeypatch):
        """Retrying a decode that cannot work would spawn a thread a frame."""
        screen = self._screen(tmp_path, monkeypatch, 0.8)
        screen._mp3_stretch_failed = (0.8, screen._mp3_path(), "no")
        started = []
        monkeypatch.setattr(screen, "_start_mp3_stretch",
                            lambda tempo: started.append(tempo))
        for _ in range(5):
            screen._ensure_mp3_source()
        assert started == []

    def test_a_missing_file_is_named_on_screen(self, tmp_path):
        config = Config()
        config.set_mp3_path_for("song", str(tmp_path / "moved.mp3"))
        screen = PlayingScreen(_timeline(), config=config, song_key="song")
        assert "not found" in screen._mp3_hud_text().lower()

    def test_a_song_with_no_recording_says_how_to_give_it_one(self):
        """It used to say nothing at all, so the key that assigns a recording
        was invisible and U looked as though it had been removed -- which is
        what the player reported. A feature that silently does nothing cannot
        be told apart from a broken one."""
        screen = PlayingScreen(_timeline(), config=Config(), song_key="song")
        text = screen._mp3_hud_text()
        assert "Shift+U" in text and "no backing track" in text.lower()


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


class TestPauseMeansSilence:
    """Pausing has to silence both backings or neither.

    Every route to the recording goes through Mp3Player.seek, and seeking
    STARTS playback. So nudging the offset on a paused song set the recording
    playing under a picture standing still -- which is precisely the state the
    offset is supposed to be judged in, and made it impossible to judge.
    """

    def _screen(self, tmp_path, monkeypatch, playing):
        song = tmp_path / "backing.mp3"
        song.write_bytes(b"x")
        config = Config()
        config.set_mp3_path_for("song", str(song))
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        monkeypatch.setattr(Mp3Player, "ready", property(lambda self: True))
        started = []
        monkeypatch.setattr(Mp3Player, "seek",
                            lambda self, ms: started.append(ms))
        screen = PlayingScreen(_timeline(), config=config, song_key="song")
        screen._playing = playing
        screen._playback_ms = 5000.0
        return screen, started

    def test_nudging_the_offset_while_paused_makes_no_sound(self, tmp_path,
                                                            monkeypatch):
        screen, started = self._screen(tmp_path, monkeypatch, playing=False)
        screen._adjust_mp3_offset(-10.0)
        assert started == []

    def test_it_still_stores_the_value_while_paused(self, tmp_path, monkeypatch):
        """Silent is not the same as ignored -- the number has to move, or the
        key looks broken."""
        screen, _ = self._screen(tmp_path, monkeypatch, playing=False)
        screen._adjust_mp3_offset(-10.0)
        assert screen._config.mp3_offset_for("song") == -10.0

    def test_the_hud_shows_the_new_value_while_paused(self, tmp_path,
                                                      monkeypatch):
        screen, _ = self._screen(tmp_path, monkeypatch, playing=False)
        screen._adjust_mp3_offset(-30.0)
        assert "-30 ms" in screen._mp3_hud_text()

    def test_nudging_while_playing_moves_the_recording(self, tmp_path,
                                                       monkeypatch):
        """The whole point of the key: hear the shift as it is made."""
        screen, started = self._screen(tmp_path, monkeypatch, playing=True)
        screen._adjust_mp3_offset(-10.0)
        assert started == [pytest.approx(5010.0)]

    def test_seeking_while_paused_makes_no_sound(self, tmp_path, monkeypatch):
        screen, started = self._screen(tmp_path, monkeypatch, playing=False)
        screen.seek(9000.0)
        assert started == [-1.0]      # -1 is the player's "be quiet"

    def test_a_paused_song_keeps_the_recording_stopped(self, tmp_path,
                                                       monkeypatch):
        screen, _ = self._screen(tmp_path, monkeypatch, playing=False)
        assert not screen._mp3_plays()


class TestSeekingWhilePlaying:
    """Spulen must take the recording with it.

    play(start=...) itself was verified against SDL's own output -- a file
    whose pitch encodes its own timestamp, played from 5/10/15 s, comes out at
    the right pitch every time. So if a jump does not carry, the fault is in
    this path rather than in pygame.
    """

    def _screen(self, tmp_path, monkeypatch):
        song = tmp_path / "backing.mp3"
        song.write_bytes(b"x")
        config = Config()
        config.set_mp3_path_for("song", str(song))
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        monkeypatch.setattr(Mp3Player, "ready", property(lambda self: True))
        seeks = []
        monkeypatch.setattr(Mp3Player, "seek", lambda self, ms: seeks.append(ms))
        notes = [NoteEvent(timestamp_ms=t, duration_ms=200.0, midi_note=40,
                           string=6, fret=0, measure=0)
                 for t in (1000.0, 20000.0)]
        screen = PlayingScreen(Timeline(notes, SongMetadata(title="T", tempo=100)),
                               config=config, song_key="song")
        screen._playing = True
        screen._playback_ms = 5000.0
        return screen, seeks

    def test_seeking_forward_moves_the_recording(self, tmp_path, monkeypatch):
        screen, seeks = self._screen(tmp_path, monkeypatch)
        screen.seek(12000.0)
        assert seeks == [pytest.approx(12000.0)]

    def test_seeking_back_moves_the_recording(self, tmp_path, monkeypatch):
        screen, seeks = self._screen(tmp_path, monkeypatch)
        screen.seek(2000.0)
        assert seeks == [pytest.approx(2000.0)]

    def test_the_offset_is_applied_to_the_jump(self, tmp_path, monkeypatch):
        screen, seeks = self._screen(tmp_path, monkeypatch)
        screen._config.set_mp3_offset_for("song", 250.0)
        screen.seek(12000.0)
        assert seeks == [pytest.approx(11750.0)]

    def test_an_arrow_key_reaches_the_recording(self, tmp_path, monkeypatch):
        """The key, not just the method underneath it."""
        screen, seeks = self._screen(tmp_path, monkeypatch)
        event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT, mod=0)
        screen.handle_event(event)
        assert len(seeks) == 1 and seeks[0] > 5000.0


class TestTheOffsetIsVisible:
    """The one key whose whole job is to change a number must show it.

    Picking a file used to leave "Backing track: x.mp3" on screen for the rest
    of the session, and a note outranks the ordinary line -- so the offset the
    player was adjusting was never displayed at all.
    """

    def _screen(self, tmp_path, monkeypatch):
        song = tmp_path / "backing.mp3"
        song.write_bytes(b"x")
        config = Config()
        config.set_mp3_path_for("song", str(song))
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        monkeypatch.setattr(Mp3Player, "ready", property(lambda self: True))
        monkeypatch.setattr(Mp3Player, "seek", lambda self, ms: None)
        return PlayingScreen(_timeline(), config=config, song_key="song")

    def test_the_offset_is_on_screen(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        screen._adjust_mp3_offset(-40.0)
        assert "-40 ms" in screen._mp3_hud_text()

    def test_choosing_a_file_leaves_no_note_behind(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        chosen = str(tmp_path / "backing.mp3")
        monkeypatch.setattr("pickhero.ui.scrolling.pick_audio_file",
                            lambda d=None: chosen)
        screen._choose_mp3_backing()
        screen._open_mp3_dialog()          # the key arms it, a frame later it opens
        assert screen._mp3_note == ""
        assert "+0 ms" in screen._mp3_hud_text()


    def test_a_note_gives_way_once_the_offset_moves(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        screen._mp3_note = "something from earlier"
        screen._adjust_mp3_offset(10.0)
        assert "10 ms" in screen._mp3_hud_text()


class TestTheFileChooserDoesNotLookLikeADeadKey:
    """Reported: several seconds of frozen app after Shift+U, and then the
    dialog opening again and again, each one needing to be cancelled."""

    def _screen(self, tmp_path, monkeypatch):
        config = Config()
        screen = PlayingScreen(_timeline(), config=config, song_key="song")
        monkeypatch.setattr("pickhero.ui.scrolling.pick_audio_file",
                            lambda d=None: str(tmp_path / "backing.mp3"))
        return screen

    def test_the_key_says_what_is_happening_before_anything_blocks(
            self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        screen._choose_mp3_backing()
        assert "chooser" in screen._mp3_hud_text().lower()
        assert screen._mp3_dialog_due is True

    def test_it_does_not_open_until_that_has_been_drawn(self, tmp_path,
                                                        monkeypatch):
        """Opened from the key press itself, nothing is drawn in between and
        the app simply stops -- which is what a dead key looks like."""
        opened = []
        screen = self._screen(tmp_path, monkeypatch)
        monkeypatch.setattr(screen, "_open_mp3_dialog",
                            lambda: opened.append(1))
        screen._choose_mp3_backing()
        screen.update()
        assert opened == []                 # nothing drawn yet
        screen._mp3_dialog_armed = True     # what render() does
        screen.update()
        assert opened == [1]

    def test_the_keys_that_piled_up_while_it_blocked_are_dropped(
            self, tmp_path, monkeypatch):
        """Key repeat is 40 ms, so seconds of blocked frame are dozens of
        keydowns -- and each one reopened the chooser."""
        pygame.init()
        screen = self._screen(tmp_path, monkeypatch)
        def slow_dialog(d=None):
            for _ in range(12):
                pygame.event.post(pygame.event.Event(
                    pygame.KEYDOWN, key=pygame.K_u, mod=pygame.KMOD_LSHIFT))
            return str(tmp_path / "backing.mp3")
        monkeypatch.setattr("pickhero.ui.scrolling.pick_audio_file", slow_dialog)
        screen._open_mp3_dialog()
        assert pygame.event.get(pygame.KEYDOWN) == []

    def test_a_cancelled_dialog_clears_its_note_too(self, tmp_path, monkeypatch):
        """Otherwise "Opening the file chooser..." outranks the ordinary line
        for the rest of the session."""
        screen = self._screen(tmp_path, monkeypatch)
        monkeypatch.setattr("pickhero.ui.scrolling.pick_audio_file",
                            lambda d=None: None)
        screen._mp3_note = "Opening the file chooser..."
        screen._open_mp3_dialog()
        assert "chooser" not in screen._mp3_hud_text().lower()




class TestARecordingThatWillNotFollow:
    """A decoder that cannot seek accepts play(start=...) without complaint
    and starts from the top anyway. The only evidence is the gap that never
    closes -- and without it, "the arrow keys do nothing to the backing" is
    indistinguishable from "you were paused"."""

    def _screen(self, tmp_path, monkeypatch, drift):
        song = tmp_path / "backing.mp3"
        song.write_bytes(b"x")
        config = Config()
        config.set_mp3_path_for("song", str(song))
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        monkeypatch.setattr(Mp3Player, "ready", property(lambda self: True))
        monkeypatch.setattr(Mp3Player, "update", lambda self, ms: None)
        monkeypatch.setattr(Mp3Player, "drift_ms", lambda self, ms: drift)
        screen = PlayingScreen(_timeline(), config=config, song_key="song")
        screen._playing = True
        return screen

    def _run_for(self, screen, ms):
        for t in range(0, int(ms), 16):
            screen._playback_ms = float(t)
            screen._update_mp3()

    def test_a_gap_that_closes_says_nothing(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, drift=20.0)
        self._run_for(screen, 10_000)
        assert not screen._mp3_is_stuck()

    def test_a_gap_that_stays_open_is_named(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, drift=4000.0)
        self._run_for(screen, 10_000)
        assert screen._mp3_is_stuck()
        assert "cannot be seeked" in screen._mp3_hud_text()

    def test_it_waits_before_calling_it(self, tmp_path, monkeypatch):
        """A single frame behind is a re-seek waiting to happen, not a fault."""
        screen = self._screen(tmp_path, monkeypatch, drift=4000.0)
        self._run_for(screen, 1_000)
        assert not screen._mp3_is_stuck()

    def test_pausing_clears_it(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch, drift=4000.0)
        self._run_for(screen, 10_000)
        screen._playing = False
        screen._update_mp3()
        assert not screen._mp3_is_stuck()


class TestTwoStepSizes:
    """One step cannot serve both jobs: 10 ms is what a sync is judged in,
    and reaching five seconds at 10 ms a press is five hundred presses."""

    def _screen(self, tmp_path, monkeypatch):
        song = tmp_path / "backing.mp3"
        song.write_bytes(b"x")
        config = Config()
        config.set_mp3_path_for("song", str(song))
        monkeypatch.setattr(Mp3Player, "open", lambda self: True)
        monkeypatch.setattr(Mp3Player, "ready", property(lambda self: True))
        monkeypatch.setattr(Mp3Player, "seek", lambda self, ms: None)
        return PlayingScreen(_timeline(), config=config, song_key="song")

    def _press(self, screen, key, mod):
        screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod))

    def test_shift_moves_by_ten_milliseconds(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        self._press(screen, pygame.K_m, pygame.KMOD_LSHIFT)
        assert screen._config.mp3_offset_for("song") == 10.0

    def test_ctrl_moves_by_a_whole_second(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        self._press(screen, pygame.K_m, pygame.KMOD_LCTRL)
        assert screen._config.mp3_offset_for("song") == 1000.0

    def test_plain_n_and_m_still_move_the_midi_backing(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        self._press(screen, pygame.K_m, 0)
        assert screen._config.mp3_offset_for("song") == 0.0
        assert screen._backing_offset() == 10.0

    def test_seconds_are_shown_as_seconds(self, tmp_path, monkeypatch):
        """A five-digit millisecond count is not a number anyone reads."""
        screen = self._screen(tmp_path, monkeypatch)
        screen._adjust_mp3_offset(-4500.0)
        assert "-4.50 s" in screen._mp3_hud_text()

    def test_small_values_stay_in_milliseconds(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        screen._adjust_mp3_offset(-120.0)
        assert "-120 ms" in screen._mp3_hud_text()


class TestACorrectionThatDoesNotCorrect:
    """Reported as the recording stuttering now and then while playing, worse
    with a larger offset. Each re-seek is a decode up to that point, so a
    correction that has to be repeated every 1.5 s is a stutter bought with
    nothing."""

    def _stuck(self, music, song_file, monkeypatch):
        """A recording whose clock never moves, however often it is seeked."""
        player = Mp3Player(song_file)
        player.open()
        player.update(0.0)
        # position_ms is a method here, not a property.
        monkeypatch.setattr(player, "position_ms", lambda: 0.0)
        return player

    def test_a_drift_that_will_not_close_stops_being_chased(
            self, music, song_file, monkeypatch):
        from pickhero.audio import mp3_playback
        player = self._stuck(music, song_file, monkeypatch)
        before = len(music.started_at)
        at = 0.0
        for _ in range(400):                       # 40 seconds of song
            at += 100.0
            player.update(at)
        # Unchecked this is one seek every 1.5 s -- 26 of them.
        assert len(music.started_at) - before < 10
        assert player._resync_gap_ms > mp3_playback.MIN_RESYNC_GAP_MS

    def test_the_first_correction_still_happens_at_once(
            self, music, song_file, monkeypatch):
        player = self._stuck(music, song_file, monkeypatch)
        before = len(music.started_at)
        player.update(2000.0)
        assert len(music.started_at) == before + 1

    def test_holding_sync_puts_it_back_to_the_short_gap(
            self, music, song_file, monkeypatch):
        from pickhero.audio import mp3_playback
        player = self._stuck(music, song_file, monkeypatch)
        for at in (2000.0, 6000.0, 12000.0, 30000.0):
            player.update(at)
        assert player._resync_gap_ms > mp3_playback.MIN_RESYNC_GAP_MS
        monkeypatch.setattr(player, "position_ms", lambda: 40000.0)
        player.update(40000.0)                     # in sync again
        assert player._resync_gap_ms == mp3_playback.MIN_RESYNC_GAP_MS

    def test_how_long_the_seek_took_is_recorded(self, music, song_file,
                                                monkeypatch):
        """A decode deep into an MP3 is the stall the player sees, and without
        a number it cannot be told from the drift it was meant to cure."""
        import time as _t
        player = self._stuck(music, song_file, monkeypatch)
        real = player._start_at
        monkeypatch.setattr(player, "_start_at",
                            lambda ms: (_t.sleep(0.01), real(ms))[1])
        player.update(2000.0)
        assert player.worst_seek_ms >= 5.0


class TestTheRecordingsOwnSpeed:
    """A tab is a fixed grid and a band is not.

    Measured on the song this was built for: the downloaded tab and a
    recording of the real performance walk apart by 1.09 %, which is 2.7 s
    over four minutes and no single offset can follow it. Given the offset
    that is right at two places, the line between them is the speed.
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

    def _sync(self, screen, s1, o1, s2, o2):
        screen._config.set_mp3_offset_for("song", o1)
        screen._playback_ms = s1
        screen._set_sync_point()
        screen._config.set_mp3_offset_for("song", o2)
        screen._playback_ms = s2
        screen._set_sync_point()

    def test_the_players_own_song_comes_back_at_the_measured_rate(
        self, tmp_path, monkeypatch
    ):
        """Chroma alignment put Bon Jovi at -10.9 ms per second. Lining the
        recording up at 10 s and 250 s reproduces it from two keypresses."""
        screen = self._screen(tmp_path, monkeypatch)
        drift = 0.0109
        self._sync(screen, 10_000.0, 10_000.0 * drift,
                   250_000.0, 250_000.0 * drift)
        assert screen._mp3_rate() == pytest.approx(1.0 - drift, abs=1e-4)
        # The recording is played LONGER, which is what closes the gap.
        assert screen._mp3_build_tempo() < 1.0

    def test_it_keeps_the_first_point_it_was_given(self, tmp_path, monkeypatch):
        """Fixing the drift must not throw away the alignment just shown."""
        screen = self._screen(tmp_path, monkeypatch)
        s1, o1 = 10_000.0, 109.0
        self._sync(screen, s1, o1, 250_000.0, 2725.0)
        rate = screen._mp3_rate()
        # The musical moment at s1 is (s1 - offset) * rate, and it may not
        # have moved.
        assert ((s1 - screen._mp3_offset()) * rate
                == pytest.approx((s1 - o1) * 1.0, abs=1.0))

    def test_the_scroll_speed_is_not_touched(self, tmp_path, monkeypatch):
        """The correction goes into the file's length, never into the scale:
        the scale is what makes one real second advance the song by tempo
        seconds, and changing it would change how fast the notes scroll."""
        screen = self._screen(tmp_path, monkeypatch)
        before = screen._mp3_scale()
        self._sync(screen, 10_000.0, 0.0, 250_000.0, 2000.0)
        assert screen._mp3_rate() != 1.0
        assert screen._mp3_scale() == before

    def test_a_correction_makes_the_source_stop_fitting(
        self, tmp_path, monkeypatch
    ):
        """Otherwise the copy is never built and the setting does nothing --
        the scale alone cannot see a rate change."""
        screen = self._screen(tmp_path, monkeypatch)
        assert screen._mp3_source_fits()
        self._sync(screen, 10_000.0, 0.0, 250_000.0, 2000.0)
        assert not screen._mp3_source_fits()

    def test_two_points_too_close_together_are_refused(
        self, tmp_path, monkeypatch
    ):
        """10 ms is one keypress, and over five seconds that is 0.2 % --
        a fifth of the whole effect, invented by the last key pressed."""
        screen = self._screen(tmp_path, monkeypatch)
        self._sync(screen, 10_000.0, 0.0, 15_000.0, 200.0)
        assert screen._mp3_rate() == 1.0
        assert "Too close" in screen._status_note_text()

    def test_the_first_point_survives_a_refusal(self, tmp_path, monkeypatch):
        """The player only has to move further away, not start again."""
        screen = self._screen(tmp_path, monkeypatch)
        self._sync(screen, 10_000.0, 0.0, 15_000.0, 200.0)
        screen._config.set_mp3_offset_for("song", 2000.0)
        screen._playback_ms = 250_000.0
        screen._set_sync_point()
        assert screen._mp3_rate() != 1.0

    def test_an_impossible_pair_is_named_rather_than_played(
        self, tmp_path, monkeypatch
    ):
        """A real mismatch is about a percent. Twenty means the two points
        were not what they looked like, and playing the song at that speed
        is indistinguishable from a broken recording."""
        screen = self._screen(tmp_path, monkeypatch)
        self._sync(screen, 10_000.0, 0.0, 100_000.0, 20_000.0)
        assert screen._mp3_rate() == 1.0
        assert "cannot both be right" in screen._status_note_text()

    def test_clearing_puts_the_recording_back(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        self._sync(screen, 10_000.0, 0.0, 250_000.0, 2000.0)
        screen._clear_sync_rate()
        assert screen._mp3_rate() == 1.0
        assert screen._mp3_build_tempo() == 1.0

    def test_it_survives_a_practice_speed_change(self, tmp_path, monkeypatch):
        """The two are independent: the speed says how fast to play, the
        rate says which recording matches this tab. Both land in the build."""
        screen = self._screen(tmp_path, monkeypatch)
        self._sync(screen, 10_000.0, 0.0, 250_000.0, 2725.0)
        rate = screen._mp3_rate()
        screen._tempo_factor = 0.7
        assert screen._mp3_build_tempo() == pytest.approx(0.7 * rate)
        assert screen._mp3_scale() == pytest.approx(1.0 / 0.7)

    def test_the_first_press_says_what_to_do_next(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        screen._playback_ms = 12_000.0
        screen._set_sync_point()
        note = screen._status_note_text()
        assert "0:12" in note and "Shift+S" in note

    def test_a_song_with_no_recording_says_so(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        screen._mp3_player = None
        screen._set_sync_point()
        assert "Shift+U" in screen._status_note_text()
