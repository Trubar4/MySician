"""Where a recording is told to go.

The recorder's hint has now been wrong twice, and the second time was the
marker built to fix the first: `UPLOAD_BRANCH` named a branch from an
earlier session, so the hint told the player to switch AWAY from the branch
being read. A recording pushed to a branch nobody reads has not been sent.

`pick_upload_branch` is the decision with git taken out of it, so the rule
can be asserted rather than tried on a laptop.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from record_reference import pick_upload_branch  # noqa: E402

HERE = "claude/somewhere-else"


class TestTheMarkerDecides:
    def test_what_it_names_is_taken(self):
        branch, switch, why = pick_upload_branch(
            "claude/work", [("claude/work", 100)], HERE)
        assert (branch, switch, why) == ("claude/work", True, None)

    def test_and_no_switch_is_asked_for_when_it_is_already_here(self):
        branch, switch, _ = pick_upload_branch(
            "claude/work", [("claude/work", 100)], "claude/work")
        assert (branch, switch) == ("claude/work", False)

    def test_an_equally_fresh_branch_does_not_overrule_it(self):
        branch, _, why = pick_upload_branch(
            "claude/work", [("claude/other", 100), ("claude/work", 100)], HERE)
        assert (branch, why) == ("claude/work", None)


class TestAStaleMarkerIsOverruled:
    def test_newer_commits_elsewhere_win(self):
        branch, _, why = pick_upload_branch(
            "claude/old", [("claude/now", 200), ("claude/old", 100)], HERE)
        assert branch == "claude/now"
        assert "claude/old" in why

    def test_a_branch_the_remote_has_never_heard_of_loses(self):
        branch, _, why = pick_upload_branch(
            "claude/gone", [("claude/now", 100)], HERE)
        assert branch == "claude/now"
        assert "not on the remote" in why

    def test_being_overruled_is_always_said(self):
        """A name nobody expected is worse than no name."""
        for branches in ([("claude/now", 200), ("claude/old", 100)],
                         [("claude/now", 100)]):
            _, _, why = pick_upload_branch("claude/old", branches, HERE)
            assert why


class TestWhenThereIsNothingToGoOn:
    def test_no_marker_falls_back_to_the_freshest_branch(self):
        branch, _, why = pick_upload_branch(
            None, [("claude/now", 200), ("claude/old", 100)], HERE)
        assert (branch, why) == ("claude/now", None)

    def test_nothing_at_all_names_the_checkout_and_asks_for_no_switch(self):
        assert pick_upload_branch(None, [], HERE) == (HERE, False, None)

    def test_and_a_detached_head_says_so_rather_than_naming_a_branch(self):
        branch, switch, _ = pick_upload_branch(None, [], "")
        assert (branch, switch) == ("<dein-branch>", False)
