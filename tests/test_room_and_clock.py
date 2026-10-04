"""Two numbers that were measuring something other than what they name.

Both were read off the player's own Shinedown run
(`run_Shinedown___Monsters_20261004_162405.txt`), and in both cases the tell
was a control that disagreed:

- `level_room_db -27.4` with `input_hears_the_room yes` in the log, against
  "room -63 dB -- input level was fine" on the completion screen a few
  seconds later, from the same function. The room was the MEDIAN of a rolling
  five-second window of not-playing audio, and the last five seconds before a
  song starts is the end of the count-in -- exactly when a player brushes a
  string or checks their sound.

- `audio_clock_ratio 0.9238`, a device supposedly losing 76 ms a second,
  beside `audio_clock_pulled_ms 21` (worst error 29 ms), a flat
  `strike_delay_median 410` and **0 %** of strikes over budget. The fix for
  this shipped a week earlier and guarded the wrong door: it cleared the
  baseline inside `_track_audio_clock`, and `update()` returns ABOVE that
  call while the song is paused. `TestTheClockRatioDoesNotMeasureItsOwnPauses`
  went green on the broken code because it called the helper by hand -- so
  these drive the real `update()`.
"""

import pytest

from pickhero import level
from pickhero.config import Config
from pickhero.ui.scrolling import PlayingScreen
from pickhero.tabs.timeline import Timeline
from pickhero.tabs.loader import NoteEvent
from tests.test_scrolling import _make_timeline


def _long_timeline():
    """Long enough that 300 frames of update() do not reach the last bar --
    the completion path stops the audio and writes a run log."""
    return Timeline(notes=[
        NoteEvent(timestamp_ms=i * 1000.0, duration_ms=400.0,
                  midi_note=40, string=6, fret=0) for i in range(600)])


class _Capture:
    """A ring buffer's sample counter and nothing else."""

    def __init__(self, rate=1.0):
        self.heard = 0.0
        self.rate = rate

    def elapsed_ms(self):
        return self.heard

    def get_notes(self):
        return []

    def get_strike_windows(self):
        return []

    def get_signal_db(self):
        return -120.0

    def get_tuner_data(self, raw=False):
        return (0.0, 0.0)

    def stop(self):
        pass


class TestTheRoomIsTheQuietestStretch:
    """A room measured over a stretch the player was not quiet for is not one."""

    def test_too_little_is_not_a_room(self):
        assert level.quietest_stretch([-70.0] * (level.ROOM_SAMPLES - 1)) is None

    def test_the_quiet_half_wins_over_the_average(self):
        """Fails on a median of the whole window, which lands in between."""
        noisy = [-27.0] * level.ROOM_SAMPLES
        quiet = [-63.0] * level.ROOM_SAMPLES
        assert level.quietest_stretch(noisy + quiet) == pytest.approx(-63.0)
        assert level.quietest_stretch(quiet + noisy) == pytest.approx(-63.0)

    def test_one_quiet_frame_cannot_define_a_room(self):
        """Chosen by the stretch's own median, not by its minimum."""
        samples = [-27.0] * (level.ROOM_SAMPLES * 2)
        samples[level.ROOM_SAMPLES] = -120.0
        assert level.quietest_stretch(samples) == pytest.approx(-27.0)

    def test_the_newest_audio_is_always_in_a_stretch(self):
        """A run that stopped mid-step still has its last stretch measured."""
        n = level.ROOM_SAMPLES * 2 + level.ROOM_STEP - 1
        samples = [-20.0] * (n - level.ROOM_SAMPLES) + [-80.0] * level.ROOM_SAMPLES
        assert level.quietest_stretch(samples) == pytest.approx(-80.0)


class TestTheScreensRoom:
    """The same rule, through the screen that reads it."""

    def _screen(self):
        screen = PlayingScreen(_make_timeline(), config=Config())
        screen._playing = False
        screen._playback_ms = -2000.0
        return screen

    def _hear(self, screen, db, frames):
        for _ in range(frames):
            screen._track_levels(db)

    def test_a_brushed_string_in_the_count_in_does_not_become_the_room(self):
        """His run: -27 dB over the count-in, then genuine quiet.

        Fails on the rolling median, which reports the midpoint of the two.
        """
        self._hear(screen := self._screen(), -27.0, 150)
        self._hear(screen, -63.0, 150)
        assert screen.room_db() == pytest.approx(-63.0)

    def test_the_room_only_ever_comes_down(self):
        """A room that rose would raise the automatic gate with it, and a gate
        that deletes a strike costs a note nothing downstream can recover.
        """
        self._hear(screen := self._screen(), -70.0, 150)
        assert screen.room_db() == pytest.approx(-70.0)
        self._hear(screen, -20.0, level.ROOM_WINDOW * 2)
        assert screen.room_db() == pytest.approx(-70.0)

    def test_the_verdict_follows(self):
        """His run reproduced: `input_hears_the_room` was a false alarm.

        250 noisy frames against 100 quiet ones is the shape that made the
        rolling median land on -27.4 -- within QUIET_MARGIN_DB of a playing
        median of -24.9, which is what raised it.
        """
        self._hear(screen := self._screen(), -27.0, 250)
        self._hear(screen, -63.0, 100)
        screen._level_samples = [-8.4, -24.9, -24.9, -40.0]
        report = screen._level_report()
        assert not report.hears_the_room
        assert report.verdict != "room"


class TestTheClockRatioSurvivesAPause:
    """Driven through the real `update()`, because that is where it broke."""

    def _screen(self):
        screen = PlayingScreen(_long_timeline(), config=Config())
        screen._audio_enabled = False
        screen._matcher = type("M", (), {"audio_offset_ms": 0.0})()
        screen._playing = True
        screen._playback_ms = 1000.0
        screen._last_tick = None
        return screen

    def _run(self, screen, capture, frames, device_rate=1.0, step=0.0167):
        import time
        now = time.perf_counter()
        for _ in range(frames):
            now += step
            screen._last_tick = now - step
            if screen._playing:
                capture.heard += step * 1000.0 * device_rate
            screen._update_now = now
            _patched_update(screen, now)

    def test_a_pause_is_not_a_drifting_device(self):
        screen = self._screen()
        capture = _Capture()
        screen._audio_capture = capture

        self._run(screen, capture, 60)            # a second of honest playing
        screen._playing = False
        self._run(screen, capture, 10)            # paused...
        capture.heard += 10_000.0                 # ...for ten seconds
        screen._playing = True
        self._run(screen, capture, 60)

        assert screen._audio_clock_heard_ms > 1000.0
        ratio = screen._audio_clock_song_ms / screen._audio_clock_heard_ms
        assert ratio == pytest.approx(1.0, abs=0.02)

    def test_a_device_that_really_drifts_is_still_reported(self):
        """The control: the fix must not make the measurement blind."""
        screen = self._screen()
        capture = _Capture()
        screen._audio_capture = capture
        self._run(screen, capture, 240, device_rate=0.95)
        ratio = screen._audio_clock_song_ms / screen._audio_clock_heard_ms
        assert ratio == pytest.approx(1.0 / 0.95, abs=0.01)

    def test_a_skipped_gap_is_counted_and_not_averaged_in(self):
        screen = self._screen()
        capture = _Capture()
        screen._audio_capture = capture
        self._run(screen, capture, 60)
        before = screen._audio_clock_gaps
        capture.heard += 5_000.0                  # the counter jumped
        self._run(screen, capture, 2)
        assert screen._audio_clock_gaps == before + 1


def _patched_update(screen, now):
    """`update()` with its perf_counter pinned, so a frame is exactly 16.7 ms.

    A real busy wait cannot be tested by freezing time -- but this loop only
    needs the frame LENGTH to be known, and that is one monkeypatch deep.
    """
    import pickhero.ui.scrolling as mod
    real = mod.time.perf_counter
    mod.time.perf_counter = lambda: now
    try:
        screen.update()
    finally:
        mod.time.perf_counter = real
