"""Tests for settings migration on load."""

import pickhero.config as config_mod
from pickhero.config import Config


def _redirect_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "settings.json")


def test_old_buf_size_default_migrates_to_4096(tmp_path, monkeypatch):
    _redirect_config(monkeypatch, tmp_path)
    old = Config()
    old.audio.buf_size = 2048  # old default persisted by earlier versions
    old.save()

    loaded = Config.load()
    assert loaded.audio.buf_size == 4096


def test_missing_yin_tolerance_gets_default(tmp_path, monkeypatch):
    _redirect_config(monkeypatch, tmp_path)
    Config().save()

    # Simulate a settings file written before the field existed
    import json
    with open(config_mod.CONFIG_FILE) as f:
        data = json.load(f)
    del data["audio"]["yin_tolerance"]
    with open(config_mod.CONFIG_FILE, "w") as f:
        json.dump(data, f)

    loaded = Config.load()
    assert loaded.audio.yin_tolerance == 0.15


def test_custom_buf_size_not_touched(tmp_path, monkeypatch):
    _redirect_config(monkeypatch, tmp_path)
    custom = Config()
    custom.audio.buf_size = 8192
    custom.save()

    loaded = Config.load()
    assert loaded.audio.buf_size == 8192


def test_old_timing_window_default_migrates_to_150(tmp_path, monkeypatch):
    _redirect_config(monkeypatch, tmp_path)
    old = Config()
    old.timing_window_ms = 100.0  # old default persisted by earlier versions
    old.save()

    loaded = Config.load()
    assert loaded.timing_window_ms == 150.0


def test_custom_timing_window_not_touched(tmp_path, monkeypatch):
    _redirect_config(monkeypatch, tmp_path)
    custom = Config()
    custom.timing_window_ms = 80.0
    custom.save()

    loaded = Config.load()
    assert loaded.timing_window_ms == 80.0
