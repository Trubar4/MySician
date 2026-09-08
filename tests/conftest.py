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


@pytest.fixture(autouse=True)
def _fresh_font_cache():
    """Fonts do not survive a pygame session, and several tests end one.

    `scrolling` caches fonts and the surfaces drawn with them, which is what
    took the playing screen from 15 ms a frame to 1.5 ms. A font kept across
    `pygame.quit()` is a dangling pointer, so a test that quits pygame would
    otherwise segfault the NEXT test that draws -- which is exactly what
    happened, and it is a real hazard rather than a test artifact.
    """
    from pickhero.ui import scrolling
    scrolling.clear_font_cache()
    yield
    scrolling.clear_font_cache()
