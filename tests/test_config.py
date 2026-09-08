

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


class TestTheBuildStamp:
    """Three fixes in a row were reported as "does nothing" while their code
    was in the tree and under test. Every one was an older EXE, and each cost
    a round trip to establish, because nothing in the app could say which
    version it was: "is it fixed" and "did it reach the machine" were the
    same question with no way to tell them apart.
    """

    def test_it_never_raises_and_always_says_something(self):
        from pickhero.build_info import build_stamp

        stamp = build_stamp()
        assert isinstance(stamp, str) and stamp

    def test_a_bundled_stamp_wins(self, tmp_path, monkeypatch):
        """What a built EXE carries. It beats the checkout, because a bundle
        that also happens to sit in a git tree is still a bundle."""
        import pickhero.build_info as build_info

        monkeypatch.setattr(build_info, "_bundled", lambda: "abc12345 built X")
        assert build_info.build_stamp() == "abc12345 built X"

    def test_a_checkout_names_the_commit(self):
        """Running from source, the interesting thing is the commit."""
        from pickhero.build_info import _from_git

        stamp = _from_git()
        assert stamp == "" or "checkout" in stamp

    def test_neither_is_not_a_crash(self, monkeypatch):
        import pickhero.build_info as build_info

        monkeypatch.setattr(build_info, "_bundled", lambda: "")
        monkeypatch.setattr(build_info, "_from_git", lambda: "")
        assert build_info.build_stamp() == build_info.UNKNOWN

    def test_writing_one_is_what_the_build_script_calls(self, tmp_path):
        from pickhero.build_info import write_stamp

        target = write_stamp(tmp_path / "_build_stamp.txt", "deadbeef",
                             "2026-09-08 12:00")
        assert target.read_text(encoding="utf-8") == \
            "deadbeef built 2026-09-08 12:00"

    def test_the_run_log_carries_it(self):
        import io

        import pygame
        from pickhero.config import Config
        from pickhero.matcher import NoteMatcher
        from pickhero.tabs.timeline import (NoteEvent, SongMetadata, Timeline)
        from pickhero.ui.scrolling import PlayingScreen

        pygame.init()
        pygame.display.set_mode((1280, 720))
        timeline = Timeline(
            [NoteEvent(timestamp_ms=0.0, duration_ms=100.0, midi_note=40,
                       string=6, fret=0, measure=0)],
            SongMetadata(title="t", tempo=120))
        screen = PlayingScreen(timeline, config=Config(), song_key="t")
        screen._matcher = NoteMatcher(timeline, timing_window_ms=150.0)
        buffer = io.StringIO()
        screen._write_run_log(buffer)
        assert "\nbuild\t" in buffer.getvalue()
