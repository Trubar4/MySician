

class TestFretFilterDoesNotSurviveRestart:
    """A limit left on from last time deletes notes without saying so.

    Filtered notes are not drawn, not scored, and not even counted as missed,
    so the accuracy shown is for whatever is left. A run of the timing test
    lost a sixth of its notes to a forgotten limit of 7, and nothing on screen
    said the number was measuring a filter.
    """

    def test_a_stored_limit_is_ignored_on_load(self, tmp_path, monkeypatch):
        import json
        import pickhero.config as config_mod
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"max_fret": 7}))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", path)
        assert config_mod.Config.load().max_fret == 24

    def test_everything_else_still_loads(self, tmp_path, monkeypatch):
        import json
        import pickhero.config as config_mod
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"max_fret": 3, "tempo_factor": 0.8}))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", path)
        loaded = config_mod.Config.load()
        assert loaded.max_fret == 24
        assert loaded.tempo_factor == 0.8
