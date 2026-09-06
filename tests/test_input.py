"""Tests for audio input device settings resolution.

USB interfaces (e.g. Focusrite) often reject the 44100 Hz default in Windows
shared mode. AudioCapture must fall back to a rate/channel combination the
device actually accepts, and keep the detector's sample rate in sync.
"""

import numpy as np
import pytest

import pickhero.audio.input as input_mod
from pickhero.audio.input import AudioCapture
from pickhero.config import Config


def _make_capture() -> AudioCapture:
    config = Config()
    config.audio.device_index = 3
    return AudioCapture(config)


def _patch_sd(monkeypatch, accepted: set[tuple[int, int]],
              default_samplerate: float = 48000.0, probe_log: list | None = None):
    """Patch sounddevice probing so only `accepted` (samplerate, channels) pass."""

    def fake_query_devices(device=None, kind=None):
        return {"default_samplerate": default_samplerate}

    def fake_check_input_settings(device=None, channels=None, samplerate=None, dtype=None):
        if probe_log is not None:
            probe_log.append((samplerate, channels))
        if (samplerate, channels) not in accepted:
            raise Exception("Invalid sample rate")

    monkeypatch.setattr(input_mod.sd, "query_devices", fake_query_devices)
    monkeypatch.setattr(input_mod.sd, "check_input_settings", fake_check_input_settings)


def test_configured_rate_used_when_accepted(monkeypatch):
    capture = _make_capture()
    _patch_sd(monkeypatch, accepted={(44100, 1)})
    assert capture._resolve_input_settings() == (44100, 1)


def test_falls_back_to_device_default_rate(monkeypatch):
    capture = _make_capture()
    _patch_sd(monkeypatch, accepted={(48000, 1)})
    assert capture._resolve_input_settings() == (48000, 1)


def test_falls_back_to_stereo_when_mono_rejected(monkeypatch):
    capture = _make_capture()
    _patch_sd(monkeypatch, accepted={(48000, 2)})
    assert capture._resolve_input_settings() == (48000, 2)


def test_returns_configured_rate_when_nothing_accepted(monkeypatch):
    capture = _make_capture()
    _patch_sd(monkeypatch, accepted=set())
    assert capture._resolve_input_settings() == (44100, 1)


def test_result_is_cached_per_device(monkeypatch):
    capture = _make_capture()
    probe_log: list = []
    _patch_sd(monkeypatch, accepted={(48000, 1)}, probe_log=probe_log)

    first = capture._resolve_input_settings()
    probes_after_first = len(probe_log)
    second = capture._resolve_input_settings()

    assert first == second == (48000, 1)
    assert len(probe_log) == probes_after_first  # no re-probe

    # Changing the device invalidates the cache
    capture.config.audio.device_index = 7
    capture._resolve_input_settings()
    assert len(probe_log) > probes_after_first


def test_query_devices_failure_still_resolves(monkeypatch):
    capture = _make_capture()

    def broken_query_devices(device=None, kind=None):
        raise Exception("no such device")

    def fake_check_input_settings(device=None, channels=None, samplerate=None, dtype=None):
        if (samplerate, channels) != (48000, 1):
            raise Exception("Invalid sample rate")

    monkeypatch.setattr(input_mod.sd, "query_devices", broken_query_devices)
    monkeypatch.setattr(input_mod.sd, "check_input_settings", fake_check_input_settings)

    assert capture._resolve_input_settings() == (48000, 1)


class TestOverflowedBuffersAreStillUsed:
    """A dropped buffer used to stop the clock, not just lose the audio.

    Every strike is stamped from the ring's sample counter. Returning early
    left that counter where it was, so each discarded buffer shifted the rest
    of the song 10.7 ms early and the error accumulated. On a real take, 2 %
    of buffers dropped that way took detection from 42 of 46 strikes to 17;
    the same drops with the counter still advancing cost two.
    """

    def _capture(self):
        from pickhero.audio.input import AudioCapture, _AudioRing, RING_SECONDS
        from pickhero.config import Config
        cap = AudioCapture(Config())
        cap._sample_rate = 48000
        cap.detector.sample_rate = 48000
        cap._ring = _AudioRing(int(48000 * RING_SECONDS))
        return cap

    def _block(self, n=512):
        return np.zeros((n, 1), dtype=np.float32)

    def test_an_overflowed_buffer_still_advances_the_clock(self):
        cap = self._capture()
        cap._audio_callback(self._block(), 512, None, "input overflow")
        assert cap._ring.written == 512

    def test_the_clock_agrees_whether_or_not_there_was_an_overflow(self):
        clean, flagged = self._capture(), self._capture()
        for _ in range(10):
            clean._audio_callback(self._block(), 512, None, None)
            flagged._audio_callback(self._block(), 512, None, "input overflow")
        assert clean._ring.written == flagged._ring.written

    def test_overflows_are_counted_so_they_can_be_shown(self):
        cap = self._capture()
        cap._audio_callback(self._block(), 512, None, None)
        assert cap.dropped_buffers == 0
        cap._audio_callback(self._block(), 512, None, "input overflow")
        cap._audio_callback(self._block(), 512, None, "input overflow")
        assert cap.dropped_buffers == 2


class TestADropoutWhileMeasuringIsNotADropoutWhilePlaying:
    """A background measurement is seconds of FFT on a worker thread and can
    starve the audio callback -- but it does not use the microphone, and the
    player is not meant to be playing during it. Counting those together
    with the ones that lose notes makes a harmless number and a serious one
    look identical.
    """

    def _capture(self):
        import numpy as np
        from pickhero.audio.input import RING_SECONDS, AudioCapture, _AudioRing
        from pickhero.config import Config

        capture = AudioCapture(Config())
        capture._sample_rate = 48000
        capture.detector.sample_rate = 48000
        capture.detector.reset()
        capture._onset_collector.reset()
        capture._ring = _AudioRing(int(48000 * RING_SECONDS))
        return capture, np.zeros((512, 1), dtype=np.float32)

    def test_a_quiet_run_counts_neither(self):
        capture, block = self._capture()
        capture._audio_callback(block, 512, None, None)
        assert capture.dropped_buffers == 0
        assert capture.dropped_while_busy == 0

    def test_an_overflow_while_playing_counts_only_once(self):
        capture, block = self._capture()
        capture._audio_callback(block, 512, None, "overflow")
        assert capture.dropped_buffers == 1
        assert capture.dropped_while_busy == 0

    def test_and_one_while_measuring_counts_in_both(self):
        capture, block = self._capture()
        capture.busy = True
        capture._audio_callback(block, 512, None, "overflow")
        assert capture.dropped_buffers == 1
        assert capture.dropped_while_busy == 1

    def test_the_clock_still_advances_through_a_dropout(self):
        """The oldest lesson here: a status flag says samples were lost
        BEFORE the callback, so the buffer in hand is good and the sample
        counter must not stand still."""
        capture, block = self._capture()
        capture.busy = True
        before = capture._ring.written
        capture._audio_callback(block, 512, None, "overflow")
        assert capture._ring.written == before + 512
