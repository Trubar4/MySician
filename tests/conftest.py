"""Shared test setup.

Several UI actions persist their setting as a side effect — adjusting the
scroll trim, the hit window, the backing offset. Exercising those in a test
would otherwise rewrite the real ~/.pickhero/settings.json and hand the user
back a config they never chose, simply for having run the suite.
"""

import pytest

import pickhero.config as config_module


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point config storage at a throwaway directory for every test."""
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / ".pickhero")
    monkeypatch.setattr(
        config_module, "CONFIG_FILE", tmp_path / ".pickhero" / "settings.json"
    )
    yield
