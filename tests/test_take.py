"""Recording a take from inside the app.

*"Koennen wir ein das Recording in die App einbauen, damit es auf beiden NBs
geht?"* -- and the answer is yes AND better, for a reason the external tool
cannot have: the take shares the ring buffer's sample counter with every
strike, so `start_sample` makes a strike's stamp an index into the WAV and
nothing has to be aligned afterwards.

What these pin is the part that can go wrong silently: the audio thread must
never block or write, a dropped block must be COUNTED rather than hidden, and
the file must be finished wherever the stream ends.
"""

import json
import threading
import time
import wave

import numpy as np
import pygame
import pytest

from pickhero.audio import take as take_mod
from pickhero.audio.take import TakeRecorder, new_take_dir, takes_dir
from pickhero.config import Config
from pickhero.ui.scrolling import PlayingScreen
from tests.test_scrolling import _make_timeline


def _rec(tmp_path, **kw):
    kw.setdefault("song", "Shinedown - Monsters")
    kw.setdefault("tempo_percent", 100)
    return TakeRecorder(tmp_path / "take", 44100, **kw)


class TestTheFileAndTheManifest:
    def test_what_goes_in_comes_out(self, tmp_path):
        rec = _rec(tmp_path)
        block = np.linspace(-0.5, 0.5, 512, dtype=np.float32)
        for _ in range(10):
            rec.feed(block)
        rec.close()
        with wave.open(str(rec.path), "rb") as w:
            assert w.getnchannels() == 1
            assert w.getframerate() == 44100
            assert w.getnframes() == 5120
            back = np.frombuffer(w.readframes(5120), dtype=np.int16)
        # 16-bit PCM, so within one step of the float it came from.
        assert np.max(np.abs(back / 32767.0 - np.tile(block, 10))) < 1e-4

    def test_the_manifest_is_the_shape_the_tools_already_read(self, tmp_path):
        """analyze_play_along.py reads samplerate and a take with id
        play_along carrying file, song and tempo_percent. A format with two
        readers is a format that drifts."""
        rec = _rec(tmp_path, tempo_percent=90)
        rec.feed(np.zeros(512, dtype=np.float32))
        rec.close()
        m = json.loads((rec.dir / "manifest.json").read_text())
        assert m["samplerate"] == 44100 and m["channels"] == 1
        t = next(t for t in m["takes"] if t["id"] == "play_along")
        assert t["file"] == "play_along.wav"
        assert t["song"] == "Shinedown - Monsters"
        assert t["tempo_percent"] == 90
        assert (rec.dir / t["file"]).exists()

    def test_it_carries_what_the_external_recorder_cannot(self, tmp_path):
        """The ring's sample index of the first sample, and the song position
        at that instant. This is the whole reason to record from in here."""
        rec = _rec(tmp_path, start_sample=441000, song_ms=12345.6)
        rec.feed(np.zeros(512, dtype=np.float32))
        rec.close()
        t = json.loads((rec.dir / "manifest.json").read_text())["takes"][0]
        assert t["start_sample"] == 441000
        assert t["start_song_ms"] == pytest.approx(12345.6, abs=0.1)
        assert t["recorded_in_app"] is True

    def test_a_take_longer_than_the_cap_stops_itself(self, tmp_path):
        rec = _rec(tmp_path)
        take_mod_limit = rec._limit
        big = np.zeros(take_mod_limit // 2 + 1, dtype=np.float32)
        rec.feed(big)
        rec.feed(big)
        rec.feed(big)                              # past the cap: ignored
        rec.close()
        assert rec.samples <= take_mod_limit + len(big)


class TestTheAudioThreadIsNeverMadeToWait:
    """A stalled DISK, which is the real case: the worker is alive and the
    write does not return."""

    def _stalled(self, tmp_path):
        rec = _rec(tmp_path)
        held = threading.Event()
        real = rec._wave.writeframes
        rec._wave.writeframes = lambda b: (held.wait(10.0), real(b))[1]
        return rec, held

    def test_a_stalled_write_loses_blocks_rather_than_blocking(self, tmp_path):
        """A blocked callback is dropped buffers, which loses notes at random
        in the RUN -- far worse than a hole in a diagnostic recording."""
        rec, held = self._stalled(tmp_path)
        block = np.zeros(64, dtype=np.float32)
        started = time.perf_counter()
        for _ in range(take_mod.MAX_QUEUED_BLOCKS + 200):
            rec.feed(block)                     # must not hang
        spent = time.perf_counter() - started
        held.set()
        assert rec.dropped_blocks > 0
        assert spent < 1.0, "the audio thread waited on a disk"
        rec.close()

    def test_and_a_take_with_a_hole_says_so(self, tmp_path):
        rec, held = self._stalled(tmp_path)
        for _ in range(take_mod.MAX_QUEUED_BLOCKS + 200):
            rec.feed(np.zeros(64, dtype=np.float32))
        held.set()
        rec.close()
        t = json.loads((rec.dir / "manifest.json").read_text())["takes"][0]
        assert t["dropped_blocks"] > 0

    def test_a_write_that_raises_does_not_kill_the_worker(self, tmp_path):
        """A full disk is counted, not a thread that silently stopped and a
        file that quietly stopped growing."""
        rec = _rec(tmp_path)
        real = rec._wave.writeframes
        calls = {"n": 0}

        def flaky(b):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("no space left on device")
            return real(b)

        rec._wave.writeframes = flaky
        for _ in range(4):
            rec.feed(np.zeros(512, dtype=np.float32))
            time.sleep(0.02)
        rec.close()
        assert rec.dropped_blocks == 1
        with wave.open(str(rec.path), "rb") as w:
            assert w.getnframes() == 512 * 3

    def test_feeding_a_closed_take_is_harmless(self, tmp_path):
        rec = _rec(tmp_path)
        rec.close()
        rec.feed(np.zeros(64, dtype=np.float32))


class TestWhereItLives:
    def test_beside_the_settings_never_beside_the_tab(self, tmp_path):
        """A four-minute take is 21 MB. Next to the song it would travel with
        every folder copy and be deleted with the song."""
        assert takes_dir(tmp_path).parent == tmp_path
        assert takes_dir(tmp_path).name == "recordings"

    def test_a_folder_per_take_named_after_the_song(self, tmp_path):
        got = new_take_dir(tmp_path, 'Papa Roach: "Reckless"/v2')
        assert got.parent == tmp_path
        for bad in '":/\\':
            assert bad not in got.name
        assert "Papa Roach" in got.name


class _Capture:
    """Just enough of AudioCapture for the screen to drive a take."""

    def __init__(self):
        self._sample_rate = 44100
        self._take = None
        self.running = True
        self.dropped_buffers = 0

    def is_running(self):
        return self.running

    def elapsed_ms(self):
        return 1000.0

    def describe_device(self):
        return "a device"

    def start_take(self, rec):
        self._take = rec

    def stop_take(self):
        t, self._take = self._take, None
        return t.close() if t is not None else None

    @property
    def take(self):
        return self._take

    def get_notes(self):
        return []

    def get_strike_windows(self):
        return []

    def get_signal_db(self):
        return -120.0

    def get_tuner_data(self, raw=False):
        return (0.0, 0.0)

    def stop(self):
        self.running = False


class TestTheKey:
    def _screen(self, tmp_path, monkeypatch):
        # `isolated_config` has already pointed CONFIG_DIR at a throwaway
        # folder, and `take.py` reads it at call time rather than at import --
        # a constant captured at import cannot be redirected, which is the
        # fault the deleted-songs trash shipped with.
        screen = PlayingScreen(_make_timeline(), config=Config(),
                               song_key="A Song")
        screen._audio_capture = _Capture()
        screen._audio_enabled = True
        return screen

    def _press(self, screen, shift=True):
        ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_w,
                                mod=pygame.KMOD_LSHIFT if shift else 0,
                                unicode="W" if shift else "w")
        screen.handle_event(ev)

    def test_shift_w_starts_and_shift_w_finishes(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        self._press(screen)
        assert screen._audio_capture.take is not None
        where = screen._audio_capture.take.dir
        self._press(screen)
        assert screen._audio_capture.take is None
        assert (where / "manifest.json").exists()

    def test_the_plain_w_is_still_wait_mode(self, tmp_path, monkeypatch):
        """Tested before its unshifted twin, or it is never reached -- which
        is how the chord view once shipped inert."""
        screen = self._screen(tmp_path, monkeypatch)
        before = screen._wait_mode
        self._press(screen, shift=False)
        assert screen._wait_mode is not before
        assert screen._audio_capture.take is None

    def test_it_says_it_is_running(self, tmp_path, monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        assert screen.take_line() == ""
        assert not any("Recording take" in text
                       for text, _ in screen._left_notes())
        self._press(screen)
        assert "Shift+W" in screen.take_line()
        assert any("Recording take" in text
                   for text, _ in screen._left_notes())
        self._press(screen)

    def test_with_no_input_it_says_so_rather_than_nothing(self, tmp_path,
                                                          monkeypatch):
        screen = self._screen(tmp_path, monkeypatch)
        screen._audio_capture = None
        self._press(screen)
        assert "no input" in (screen._status_note or "")

    def test_leaving_the_song_finishes_the_take(self, tmp_path, monkeypatch):
        """Or the WAV is left half written with no manifest beside it."""
        screen = self._screen(tmp_path, monkeypatch)
        self._press(screen)
        where = screen._audio_capture.take.dir
        screen.stop_audio()
        assert (where / "manifest.json").exists()

    def test_it_is_never_started_by_itself(self, tmp_path, monkeypatch):
        """On a key, never automatically: 88 kB a second on every song is a
        few gigabytes over a week."""
        screen = self._screen(tmp_path, monkeypatch)
        for _ in range(5):
            screen.update()
        assert screen._audio_capture.take is None


class TestItIsReallyWiredToTheCallback:
    """A tap nothing calls is a feature that ships doing nothing, which this
    project has now shipped five times. So this drives the REAL callback."""

    def _capture(self):
        from pickhero.audio.input import AudioCapture
        return AudioCapture(Config())

    def test_the_real_callback_feeds_the_running_take(self, tmp_path):
        cap = self._capture()
        rec = _rec(tmp_path)
        cap.start_take(rec)
        for _ in range(8):
            cap._audio_callback(np.zeros((1024, 2), dtype=np.float32),
                                1024, None, None)
        assert rec.samples == 8 * 1024
        cap.stop_take()
        with wave.open(str(rec.path), "rb") as w:
            assert w.getnframes() == 8 * 1024

    def test_nothing_is_recorded_without_a_take(self, tmp_path):
        cap = self._capture()
        cap._audio_callback(np.zeros((512, 2), dtype=np.float32),
                            512, None, None)
        assert cap.take is None

    def test_what_is_recorded_is_what_the_DETECTOR_was_handed(self, tmp_path):
        """Not an average of the channels: the one the capture CHOSE. That is
        the whole argument for recording in here rather than beside it -- a
        second stream could pick the other input and nobody would know."""
        cap = self._capture()
        rec = _rec(tmp_path)
        cap.start_take(rec)
        loud = np.zeros((2048, 2), dtype=np.float32)
        loud[:, 1] = 0.5                       # the guitar is in input 2
        for _ in range(4):
            cap._audio_callback(loud, 2048, None, None)
        cap.stop_take()
        with wave.open(str(rec.path), "rb") as w:
            back = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        # 0.5 and not 0.25, which is what averaging the two would have given.
        assert np.max(back) / 32767.0 == pytest.approx(0.5, abs=0.01)

    def test_a_restarted_stream_closes_the_take(self, tmp_path):
        """A new ring is a new counter, so a take started against the old one
        can no longer say where its samples sit."""
        import inspect
        from pickhero.audio.input import AudioCapture
        assert "stop_take()" in inspect.getsource(AudioCapture.start)
        cap = self._capture()
        cap.start_take(_rec(tmp_path))
        cap.stop()                              # the device is gone with it
        assert cap.take is None

    def test_the_tap_does_no_IO_in_the_audio_thread(self):
        """Read off the source: the thing that would break is somebody
        putting a write back in, not the arithmetic."""
        import inspect
        from pickhero.audio.input import AudioCapture
        body = inspect.getsource(AudioCapture._audio_callback)
        after = body.split("take.feed(mono)")[0].split("take = self._take")[-1]
        assert after.strip() in ("if take is not None:",)

    def test_what_the_tap_costs_the_callback(self, tmp_path):
        """It must not be measurable against the 11.6 ms hop it runs in.
        Not a frame-time claim -- the RATIO, which is what can be compared."""
        import time
        cap = self._capture()
        block = np.zeros((512, 2), dtype=np.float32)

        def spend(n=120):
            start = time.perf_counter()
            for _ in range(n):
                cap._audio_callback(block, 512, None, None)
            return (time.perf_counter() - start) / n

        plain = spend()
        cap.start_take(_rec(tmp_path))
        taped = spend()
        cap.stop_take()
        assert taped < plain * 1.5 + 0.0005, (
            f"the tap cost {1000 * (taped - plain):.3f} ms a callback")
