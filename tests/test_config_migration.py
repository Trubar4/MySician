"""Tests for settings migration on load."""

import json

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


def test_a_gate_above_the_ceiling_is_repaired(tmp_path, monkeypatch):
    """-20 dB was the old ceiling and the HUD's own advice could walk a gate
    there five decibels at a time. At -20 dB a run of a real song discarded
    40 % of its audio: the distorted chorus survived and the clean verse was
    deleted. A saved value up there is that bug's residue, not a choice."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(
        json.dumps({"audio": {"noise_gate_db": -20.0}}))
    assert config_mod.Config.load().audio.noise_gate_db == config_mod.MAX_GATE_DB


def test_a_gate_inside_the_range_is_left_alone(tmp_path, monkeypatch):
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(
        json.dumps({"audio": {"noise_gate_db": -65.0}}))
    assert config_mod.Config.load().audio.noise_gate_db == -65.0


def test_the_old_onset_threshold_is_migrated(tmp_path, monkeypatch):
    """0.3 was aubio's default and there is no UI to change it, so a stored
    0.3 came from that default. It hears 37 % of the picks in an arpeggio
    against 88 % at 0.05, because a new note under a ringing chord is a small
    change in spectral flux."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(
        json.dumps({"audio": {"onset_threshold": 0.3}}))
    assert Config.load().audio.onset_threshold == 0.05


def test_an_onset_threshold_someone_chose_is_left_alone(tmp_path, monkeypatch):
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(
        json.dumps({"audio": {"onset_threshold": 0.22}}))
    assert Config.load().audio.onset_threshold == 0.22
