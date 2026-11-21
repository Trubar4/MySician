

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

    def test_a_bundled_stamp_wins_in_a_built_EXE(self, monkeypatch):
        """What a built EXE carries is what it is."""
        import pickhero.build_info as build_info

        monkeypatch.setattr(build_info, "_frozen", lambda: True)
        monkeypatch.setattr(build_info, "_bundled", lambda: "abc12345 built X")
        monkeypatch.setattr(build_info, "_from_git", lambda: "ffff0000 (checkout)")
        assert build_info.build_stamp() == "abc12345 built X"

    def test_and_LOSES_in_a_checkout(self, monkeypatch):
        """`build.bat` writes the stamp into the tree, so any checkout that
        has ever been built carries one. Believing it there means `git pull`
        and `python -m pickhero` go on naming the commit it was BUILT at --
        the confusion the stamp exists to end, caused by the stamp."""
        import pickhero.build_info as build_info

        monkeypatch.setattr(build_info, "_frozen", lambda: False)
        monkeypatch.setattr(build_info, "_bundled", lambda: "abc12345 built X")
        monkeypatch.setattr(build_info, "_from_git", lambda: "ffff0000 (checkout)")
        assert build_info.build_stamp() == "ffff0000 (checkout)"

    def test_but_a_tree_with_no_git_still_says_something(self, monkeypatch):
        """A source tree copied without `.git` has nothing else to offer, and
        a stale answer beats "unknown build"."""
        import pickhero.build_info as build_info

        monkeypatch.setattr(build_info, "_frozen", lambda: False)
        monkeypatch.setattr(build_info, "_bundled", lambda: "abc12345 built X")
        monkeypatch.setattr(build_info, "_from_git", lambda: "")
        assert build_info.build_stamp() == "abc12345 built X"

    def test_a_checkout_names_the_commit(self):
        """Running from source, the interesting thing is the commit."""
        from pickhero.build_info import _from_git

        stamp = _from_git()
        assert stamp == "" or "checkout" in stamp

    @staticmethod
    def _fake_tree(tmp_path, monkeypatch, *, loose=True, packed=False):
        """A `.git` just real enough for `_from_git`, with HEAD deliberately
        OLDER than the ref -- which is what a `git pull` leaves behind: it
        fast-forwards `refs/heads/<branch>` and never touches HEAD."""
        import os

        import pickhero.build_info as build_info

        git = tmp_path / ".git"
        (git / "refs" / "heads" / "claude").mkdir(parents=True)
        head = git / "HEAD"
        head.write_text("ref: refs/heads/claude/work\n", encoding="utf-8")
        ref = git / "refs" / "heads" / "claude" / "work"
        if loose:
            ref.write_text("4b2a09760768e902d92a3cea97b85cb36100c2b4\n",
                           encoding="utf-8")
        if packed:
            (git / "packed-refs").write_text(
                "# pack-refs with: peeled fully-peeled sorted \n"
                "4b2a09760768e902d92a3cea97b85cb36100c2b4 "
                "refs/heads/claude/work\n"
                "^0000000000000000000000000000000000000000\n",
                encoding="utf-8")
        from datetime import datetime, timezone

        def epoch(text):
            return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(
                tzinfo=timezone.utc).timestamp()

        old = epoch("2026-11-07 14:48")   # the branch was checked out
        new = epoch("2026-11-21 16:13")   # and pulled a fortnight later
        os.utime(head, (old, old))
        for later in (ref, git / "packed-refs"):
            if later.exists():
                os.utime(later, (new, new))
        pkg = tmp_path / "pickhero"
        pkg.mkdir()
        monkeypatch.setattr(build_info, "__file__",
                            str(pkg / "build_info.py"))
        return build_info

    def test_the_date_is_the_commit_it_names_rather_than_HEADs_own_mtime(
        self, tmp_path, monkeypatch
    ):
        """A `git pull` writes the REF, never HEAD -- which is only touched
        when somebody switches branch. Taking HEAD's mtime put today's
        commit beside the day the branch was checked out: on the player's own
        log, a sha from 16:13 stamped two weeks earlier. A date that stale
        makes a current build look ancient, which is the one confusion this
        stamp exists to end."""
        build_info = self._fake_tree(tmp_path, monkeypatch)
        stamp = build_info._from_git()
        assert "4b2a0976 work" in stamp
        assert "2026-11-21 16:13" in stamp, stamp

    def test_a_fresh_clone_keeps_its_refs_packed_and_still_says_something(
        self, tmp_path, monkeypatch
    ):
        """No loose file for the branch at all. Without `packed-refs` that is
        "unknown build" on a tree somebody has just cloned."""
        build_info = self._fake_tree(tmp_path, monkeypatch,
                                     loose=False, packed=True)
        stamp = build_info._from_git()
        assert "4b2a0976 work" in stamp
        assert "2026-11-21 16:13" in stamp, stamp

    def test_and_a_ref_nothing_can_resolve_is_not_a_crash(
        self, tmp_path, monkeypatch
    ):
        """Neither a loose file nor a packed one. The stamp falls back rather
        than raising while the window is coming up."""
        build_info = self._fake_tree(tmp_path, monkeypatch, loose=False)
        assert build_info._from_git() == ""

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
