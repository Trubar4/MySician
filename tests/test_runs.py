"""The history of a song's runs, and the two runs nobody played.

*"Ich haette gerne Vergleiche gemacht von mehreren Durchgaengen des selben
Songs ... Eine Kombination mit der besten je getroffenen Note ... Haeufigste
Fehler."*

Everything here is strings and files -- no screen -- because the synthetic
runs are arithmetic and the drawing is not what can be wrong about them.
"""

import json

import pytest

from pickhero import runs


def _run(notes, started="2026-09-01T10:00:00+00:00"):
    return runs.make(notes, seconds=60.0, tempo_percent=100, track=0,
                     started=started)


# -- the alphabet -----------------------------------------------------------

def test_a_verdict_nothing_checked_is_written_in_capitals():
    assert runs.encode([("hit", True), ("hit", False),
                        ("miss", True), ("miss", False),
                        (None, True)]) == "hHmM."


def test_counts_come_out_of_the_string_not_from_beside_it():
    # The number in the list and the dots in the bar are the same arithmetic,
    # so they cannot drift apart -- and a drained verdict still counts, which
    # is what the player asked for: "es ist ok, wenn du sie als gueltig zaehlst".
    got = _run("hhHcCmM..").counts()
    assert got == {"hits": 3, "close": 2, "misses": 2, "total": 7,
                   "accuracy_percent": pytest.approx(3 / 7 * 100)}


def test_a_song_nobody_played_is_not_a_run():
    assert not runs.worth_keeping(_run("....."))
    assert runs.worth_keeping(_run("....h"))


# -- the file beside the tab ------------------------------------------------

def test_it_lives_beside_the_tab_under_the_tabs_own_name(tmp_path):
    tab = tmp_path / "AC-DC - Thunder.gp5"
    assert runs.path_for(tab) == tmp_path / "AC-DC - Thunder.runs.json"


def test_a_run_survives_the_round_trip(tmp_path):
    tab = tmp_path / "song.gp5"
    assert runs.append(tab, _run("hhcm."))
    back = runs.load(tab)
    assert len(back) == 1
    assert back[0].notes == "hhcm."
    assert back[0].note_count == 5
    assert back[0].tempo_percent == 100


def test_runs_are_kept_in_the_order_they_were_played(tmp_path):
    tab = tmp_path / "song.gp5"
    for i in range(3):
        runs.append(tab, _run("h" * (i + 1) + "." * (2 - i)))
    assert [r.notes for r in runs.load(tab)] == ["h..", "hh.", "hhh"]


def test_the_history_does_not_grow_for_ever(tmp_path):
    tab = tmp_path / "song.gp5"
    for _ in range(runs.MAX_RUNS + 10):
        runs.append(tab, _run("hm"))
    assert len(runs.load(tab)) == runs.MAX_RUNS


def test_a_file_that_cannot_be_read_is_no_runs_rather_than_a_crash(tmp_path):
    tab = tmp_path / "song.gp5"
    runs.path_for(tab).write_text("{not json", encoding="utf-8")
    assert runs.load(tab) == []


def test_a_version_nobody_here_understands_is_ignored(tmp_path):
    tab = tmp_path / "song.gp5"
    runs.path_for(tab).write_text(
        json.dumps({"version": runs.VERSION + 1,
                    "runs": [{"notes": "hhh"}]}), encoding="utf-8")
    assert runs.load(tab) == []


def test_a_run_of_another_track_does_not_fit_this_song(tmp_path):
    played = runs.make("hhh", 10.0, 100, track=2)
    assert played.fits(3, 2)
    assert not played.fits(3, 0)      # another instrument
    assert not played.fits(4, 2)      # the tab was edited


# -- best ever --------------------------------------------------------------

def test_best_ever_takes_the_best_each_note_has_ever_been():
    got = runs.best_ever([_run("mmm"), _run("cmh"), _run("mhm")])
    assert got.notes == "chh"


def test_best_ever_prefers_the_verdict_something_stood_behind():
    # Same verdict either way, so the one that was actually heard wins: a
    # green credited to a strum is not evidence that the string was fretted.
    assert runs.best_ever([_run("H"), _run("h")]).notes == "h"


def test_a_note_no_run_ever_reached_stays_blank():
    assert runs.best_ever([_run("h."), _run("c.")]).notes == "h."


def test_one_run_is_not_a_comparison():
    assert runs.best_ever([_run("hhh")]) is None


# -- most frequent errors ---------------------------------------------------

def test_an_error_in_more_than_half_the_runs_is_reported():
    got = runs.common_errors([_run("mh"), _run("mh"), _run("hh"), _run("mh")])
    assert got.notes == "m."


def test_exactly_half_is_not_more_than_half():
    assert runs.common_errors([_run("mh"), _run("hh")]) is None


def test_a_note_the_last_run_played_right_is_not_reported():
    # "Fehler die beim letzten Durchgang nicht mehr gemacht wurden, sollen
    # auch nicht angezeigt werden."
    assert runs.common_errors([_run("m"), _run("m"), _run("h")]) is None


def test_a_note_the_last_run_played_late_is_also_fixed_enough():
    assert runs.common_errors([_run("m"), _run("m"), _run("c")]) is None


def test_a_note_the_last_run_never_reached_is_still_reported():
    # Not reaching it says nothing about whether it is fixed, and dropping it
    # would hide every passage at the end of a song that is abandoned early.
    assert runs.common_errors([_run("m"), _run("m"), _run(".")]).notes == "m"


def test_a_verdict_nothing_checked_votes_neither_way():
    # "ja ausschliessen" -- a drained miss is the app saying it could not
    # tell, and three of those must not add up to a verdict about the player.
    assert runs.common_errors([_run("M"), _run("M"), _run("M")]) is None


def test_a_drained_verdict_does_not_shrink_the_denominator_either():
    # Two real runs, one of them wrong: that is exactly half, not more, and
    # the drained one in between must not tip it over.
    assert runs.common_errors([_run("m"), _run("M"), _run("h")]) is None


def test_playing_it_off_the_beat_is_not_a_Fehler():
    # CLOSE is the right note played late; the timing percentage answers for
    # that, and counting it here would paint most of a run red.
    assert runs.common_errors([_run("c"), _run("c"), _run("c")]) is None


def test_a_note_only_one_run_ever_reached_cannot_be_frequent():
    assert runs.common_errors([_run("m"), _run("."), _run(".")]) is None


def test_a_song_with_nothing_recurring_says_so_rather_than_drawing_nothing():
    assert runs.common_errors([_run("hh"), _run("hh"), _run("hh")]) is None


def test_the_label_says_what_it_is_made_of():
    # How many runs it is made of. How many NOTES it names is the run itself
    # -- read off the string by whoever draws it, so the sentence and the
    # picture cannot disagree about the count.
    got = runs.common_errors([_run("mm"), _run("mm"), _run("mm")])
    assert "3 runs" in got.label
    assert got.notes.count(runs.MISS) == 2
    assert got.kind == "errors"


# -- two machines -----------------------------------------------------------

def test_merging_takes_what_this_machine_has_not_got():
    mine = [_run("hh", "2026-09-01T20:00:00+00:00")]
    theirs = [_run("mm", "2026-09-02T20:00:00+00:00")]
    both, added = runs.merge(mine, theirs)
    assert added == 1
    assert [r.notes for r in both] == ["hh", "mm"]


def test_merging_twice_changes_nothing_the_second_time():
    # Nobody remembers whether they already imported, and a doubled history
    # cannot be told apart from having practised twice as much.
    mine = [_run("hh", "2026-09-01T20:00:00+00:00")]
    theirs = [_run("mm", "2026-09-02T20:00:00+00:00")]
    once, _ = runs.merge(mine, theirs)
    twice, added = runs.merge(once, theirs)
    assert added == 0
    assert [r.notes for r in twice] == [r.notes for r in once]


def test_the_last_run_is_the_most_recent_evening_not_the_last_file_read():
    # `common_errors` asks what the LAST run did, so the order has to be time.
    mine = [_run("hh", "2026-09-05T20:00:00+00:00")]
    theirs = [_run("mm", "2026-09-02T20:00:00+00:00")]
    both, _ = runs.merge(mine, theirs)
    assert both[-1].started.startswith("2026-09-05")


def test_two_runs_that_say_different_things_both_survive():
    # The key carries the verdicts as well as the moment: stricter fails the
    # safe way, because a dropped evening is what this exists to prevent.
    same = "2026-09-01T20:00:00+00:00"
    both, added = runs.merge([_run("hh", same)], [_run("mm", same)])
    assert added == 1 and len(both) == 2


def test_a_merged_history_still_does_not_grow_for_ever():
    mine = [_run("h", f"2026-09-01T20:00:{i:02d}+00:00") for i in range(40)]
    theirs = [_run("m", f"2026-09-02T20:00:{i:02d}+00:00") for i in range(40)]
    both, _ = runs.merge(mine, theirs)
    assert len(both) == 80        # the merge itself keeps everything
    # The cap is `save`'s: it is the FILE that must not grow for ever, and
    # trimming inside the merge would throw away runs the caller may want.
    assert len(both[-runs.MAX_RUNS:]) == runs.MAX_RUNS


def test_the_file_on_disk_takes_the_other_machines_evenings(tmp_path):
    here = tmp_path / "here"
    there = tmp_path / "there"
    here.mkdir()
    there.mkdir()
    for folder, day in ((here, 1), (there, 2)):
        tab = folder / "song.gp5"
        tab.write_text("x")
        runs.append(tab, _run("hh", f"2026-09-0{day}T20:00:00+00:00"))
    assert runs.merge_files(here / "song.gp5", there / "song.gp5") == 1
    kept = runs.load(here / "song.gp5")
    assert len(kept) == 2
    assert runs.merge_files(here / "song.gp5", there / "song.gp5") == 0


def test_a_history_that_cannot_be_read_does_not_take_the_import_down(tmp_path):
    here = tmp_path / "song.gp5"
    here.write_text("x")
    there = tmp_path / "other.gp5"
    there.write_text("x")
    runs.path_for(there).write_text("{not json", encoding="utf-8")
    assert runs.merge_files(here, there) == 0


# -- where a run went wrong -------------------------------------------------

class TestErrorNests:

    def test_neighbouring_bars_are_one_passage(self):
        # Four notes a bar, wrong in bars 0 and 1: one nest, not two.
        notes = "m..m" "m..." + "h" * 8
        bars = [0] * 4 + [1] * 4 + [2] * 4 + [3] * 4
        assert runs.error_nests(notes, bars) == [(0, 1, 3)]

    def test_one_clean_bar_is_bridged_and_two_are_not(self):
        bars = [b for b in range(6) for _ in range(2)]
        one = "m." + ".." + "m." + "h" * 6        # bars 0 and 2
        assert runs.error_nests(one, bars) == [(0, 2, 2)]
        two = "m." + ".." + ".." + "m." + "hhhh"  # bars 0 and 3
        assert runs.error_nests(two, bars) == [(0, 0, 1), (3, 3, 1)]

    def test_nothing_wrong_is_no_nests(self):
        assert runs.error_nests("hhcc", [0, 0, 1, 1]) == []

    def test_a_close_is_not_a_mistake_here_either(self):
        # The same rule as common_errors: a CLOSE is the right note off the
        # beat, and looping a bar for it would say it was wrong.
        assert runs.error_nests("cccc", [0, 0, 1, 1]) == []

    def test_a_drained_verdict_does_not_call_a_passage_wrong(self):
        # `M` is the app saying it could not tell. Sending the player to
        # practise a bar on that is convicting on absence of evidence.
        assert runs.error_nests("MMMM", [0, 0, 1, 1]) == []

    def test_they_come_out_in_bar_order_with_their_counts(self):
        notes = "m" "m" "." "." "m" "." "." "." "." "m" "m"
        bars = [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 8]
        assert runs.error_nests(notes, bars) == [(0, 0, 2), (3, 3, 1),
                                                 (8, 8, 2)]

    def test_a_note_with_no_bar_is_left_out_rather_than_guessed(self):
        # A verdict string longer than the bars it was given describes notes
        # this timeline does not have.
        assert runs.error_nests("hhmm", [0, 0]) == []
