"""Tests for audio input device settings resolution.

USB interfaces (e.g. Focusrite) often reject the 44100 Hz default in Windows
shared mode. AudioCapture must fall back to a rate/channel combination the
device actually accepts, and keep the detector's sample rate in sync.
"""

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
