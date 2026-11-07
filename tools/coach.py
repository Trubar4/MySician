"""Read a run log the way a teacher reads a recording: which BARS, and why.

A run log already says what became of every written note. What it does not
say -- and what makes the difference between a number and a lesson -- is
WHERE the failures cluster and WHICH KIND of failure they are, because those
are practised in completely different ways, and two of the five are not the
player's fault at all.

**Nine out of ten red notes are not evidence about the playing.** Measured
across three complete runs of two real songs (1384, 1384 and 3287 written
notes), every note the app marked missed, classified by joining it to the
strike that arrived nearest it:

| why the note failed | Leave A Light On | again | Californication |
|---|---|---|---|
| subharmonic -- an arpeggio read as one note | 72 % | 71 % | 65 % |
| a strike arrived carrying no pitch | 17 % | 10 % | 23 % |
| **a clean reading of a wrong pitch** | **8 %** | **9 %** | **9 %** |
| an octave out (scored green, counted here for completeness) | 2 % | 2 % | 1 % |
| no strike within the window at all | 0 % | 8 % | 2 % |

So a report that simply listed the weakest bars would spend most of its
advice on passages the player very likely played correctly -- which is the
worst thing a teacher can do, and exactly what the first version of this
did. See "An Arpeggio Comes Back As One Note" in CLAUDE.md for why.

What this therefore reports, and nothing else:

- **A strike that never arrived** is the one unambiguous fault. Nothing was
  played there, or nothing loud enough to be heard.
- **A clean reading of a different pitch, within a hand's reach**, says which
  fret was hit instead. Further out than that is not a finger -- see
  REACH_SEMITONES.
- **Timing** is honest even where the pitch is not: a subharmonic strike
  proves something was struck at that moment, whatever it was.
- Everything else is COUNTED and named as unreadable rather than dressed up
  as a mistake. Absence of evidence is the commonest thing in this signal
  path, and this file's whole argument is that it must not be scored.

Usage:
    python tools/coach.py ~/.pickhero/run_*.txt
        one report per log: the weak bars of that evening, and why.

    python tools/coach.py ~/.pickhero/run_*.txt --patterns
        what RECURS across many logs: the same sitting counted once, the
        runs banded so nothing is averaged across a code change, and every
        mistake grouped by fret, string, string change, chord size and
        technique. Takes the last 20 playing days unless --days says
        otherwise (--days 0 is every day there is).

    --each      print the per-log reports as well as the patterns.
"""
from __future__ import annotations

import bisect
import statistics
import sys
from collections import defaultdict
from pathlib import Path

# A note whose nearest strike is further away than the windows the run used
# had no strike at all. Read from the log rather than assumed, because both
# windows are settings.
DEFAULT_HIT_MS = 200.0
DEFAULT_LATE_MS = 440.0
# A bar with fewer notes than this cannot say anything about a passage.
MIN_BAR_NOTES = 4
# How far a wrong pitch may be from the written one and still be something a
# HAND did. Five semitones is about a hand's span up the neck, and it is also
# the distance between two adjacent strings -- so it covers both a fret slip
# and a string slip.
#
# Beyond that it is not the playing. Measured over two complete runs of the
# same song, 108 clean readings of a wrong pitch: the 69 within reach are
# read at a median confidence of 1.000 and only 1 % below 0.95, while the 39
# further out sit at 0.990 with 31 % below 0.95 -- and their intervals are
# -14, -17, -19, -21, which no guitarist fingers by accident. They are a
# lower string still ringing under the note, which is the detector's business
# and not the player's. Counted as mistakes they were a third of the advice.
REACH_SEMITONES = 5
# What counts as a passage worth naming. Not a grade: a bar under this, in a
# run whose readable notes mostly land, is where the practice time goes.
WEAK_SHARE = 0.62
# A passage whose failures are mostly unreadable is not a passage that was
# played badly. A SHARE and not a count: the first version demanded six
# readable failures, so a bar with three -- all of them clean wrong pitches,
# which is as clear as this gets -- was reported as unreadable.


class Run:
    """One run log, parsed. Old logs are read too, with less to say."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.head: dict[str, str] = {}
        self.strikes: list[dict] = []
        self.notes: list[dict] = []
        where = None
        for line in self.path.read_text(encoding="utf-8",
                                        errors="replace").splitlines():
            if line.startswith("# every strike"):
                where = "s"
                continue
            if line.startswith("# every written note"):
                where = "n"
                continue
            if not line.strip() or line.startswith("#"):
                continue
            field = line.split("\t")
            if where is None:
                self.head[field[0]] = field[1] if len(field) > 1 else ""
            elif field[0] in ("strike_ms", "note_ms"):
                self.columns = field if where == "n" else getattr(
                    self, "columns", [])
            elif where == "s":
                self.strikes.append(dict(
                    ms=float(field[0]), adjusted=float(field[1]),
                    midi=int(field[3]), confidence=float(field[4]),
                    unpitched=field[5] == "1", subharmonic=field[6] == "1",
                    outcome=field[7]))
            else:
                self.notes.append(self._note(field))
        self._adjusted = sorted(s["adjusted"] for s in self.strikes)
        self._by_adjusted = {s["adjusted"]: s for s in self.strikes}

    def _note(self, field: list[str]) -> dict:
        """A row of the note table, old shape or new.

        The old one is `note_ms string midi verdict`; the new one carries the
        bar, the fret, the technique and how many strings were written at
        that moment. A log without them is still worth reading -- it simply
        cannot name a bar, and says so rather than inventing one.
        """
        if len(field) >= 8:
            return dict(ms=float(field[0]),
                        bar=None if field[1] == "-" else int(field[1]),
                        string=int(field[2]), fret=int(field[3]),
                        midi=int(field[4]), tech=field[5],
                        chord=int(field[6]), verdict=field[7])
        return dict(ms=float(field[0]), bar=None, string=int(field[1]),
                    fret=None, midi=int(field[2]), tech="-", chord=1,
                    verdict=field[3])

    @property
    def window_ms(self) -> float:
        return (float(self.head.get("hit_window_ms", DEFAULT_HIT_MS))
                + float(self.head.get("late_window_ms", DEFAULT_LATE_MS)))

    def nearest_strike(self, ms: float):
        """The strike closest to this moment, or None if none is near."""
        if not self._adjusted:
            return None, None
        i = bisect.bisect_left(self._adjusted, ms)
        best, gap = None, None
        for j in (i - 1, i, i + 1):
            if 0 <= j < len(self._adjusted):
                away = abs(self._adjusted[j] - ms)
                if gap is None or away < gap:
                    best, gap = self._by_adjusted[self._adjusted[j]], away
        if gap is None or gap > self.window_ms:
            return None, None
        return best, gap

    def why(self, note: dict) -> str:
        """Why this note failed -- and whether that is about the playing."""
        strike, _ = self.nearest_strike(note["ms"])
        if strike is None:
            return "nothing was struck"
        if strike["unpitched"]:
            return "struck, no pitch"
        if strike["subharmonic"]:
            return "struck, unreadable"
        difference = strike["midi"] - note["midi"]
        if difference % 12 == 0:
            return "octave"
        if abs(difference) > REACH_SEMITONES:
            return "struck, out of reach"
        return f"wrong pitch ({difference:+d})"


# The two verdicts that are evidence about the PLAYING. Everything else the
# classifier can say is evidence about the signal path.
PLAYER_FAULTS = ("nothing was struck", "wrong pitch")


def _number(text: str | None) -> float | None:
    """A header value as a number, or None where the log says it has none.

    The log prints "(nothing measured)" rather than a figure it does not
    have, which is the whole reason a missing measurement is visible at all
    -- so every reader of it has to expect a word where a number would be.
    """
    try:
        return float(text)               # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def trustworthy(run: Run) -> list[str]:
    """What makes this run's numbers unreadable, if anything.

    Asked FIRST and reported first. A wrong input device makes every other
    number in a run log meaningless, and this project has spent whole
    sessions diagnosing playing that was never in the signal path.
    """
    doubts = []
    if run.head.get("input_hears_the_room", "").startswith("yes"):
        doubts.append(f"the input hears the room, not the guitar "
                      f"({run.head.get('input_device', '?')})")
    under = _number(run.head.get("level_under_gate_percent"))
    if under is not None and under >= 20:
        doubts.append(f"{under:.0f} % of the audio was below the noise gate")
    loudest = _number(run.head.get("level_loudest_db"))
    if loudest is not None and loudest < -38.0:
        doubts.append(f"the input peaks at {loudest:.1f} dB, where the pitch "
                      f"starts coming back wrong")
    dropped = int(_number(run.head.get("dropped_buffers")) or 0)
    if dropped:
        doubts.append(f"{dropped} dropped buffers -- notes were lost at random")
    if int(_number(run.head.get("seeks")) or 0) and not run.strikes:
        doubts.append("the log was written straight after a seek, so it "
                      "describes only what happened since")
    return doubts


def passages(run: Run) -> list[dict]:
    """Runs of consecutive weak bars, with what is wrong in each."""
    by_bar: dict[int, list[dict]] = defaultdict(list)
    for note in run.notes:
        if note["verdict"] == "pending" or note["bar"] is None:
            continue
        by_bar[note["bar"]].append(note)

    scored = {}
    for bar, notes in by_bar.items():
        if len(notes) < MIN_BAR_NOTES:
            continue
        good = sum(1 for n in notes if n["verdict"] in ("hit", "close"))
        scored[bar] = good / len(notes)

    weak = sorted(b for b, share in scored.items() if share < WEAK_SHARE)
    runs, current = [], []
    for bar in weak:
        if current and bar == current[-1] + 1:
            current.append(bar)
        else:
            if current:
                runs.append(current)
            current = [bar]
    if current:
        runs.append(current)

    out = []
    for bars in runs:
        notes = [n for b in bars for n in by_bar[b]]
        reasons: dict[str, int] = defaultdict(int)
        for note in notes:
            if note["verdict"] not in ("hit", "close"):
                reasons[run.why(note)] += 1
        readable = sum(count for reason, count in reasons.items()
                       if reason.startswith(PLAYER_FAULTS))
        frets = [n["fret"] for n in notes if n["fret"] is not None]
        out.append(dict(
            bars=bars, notes=len(notes),
            good=sum(1 for n in notes if n["verdict"] in ("hit", "close")),
            start_ms=min(n["ms"] for n in notes),
            reasons=dict(reasons), readable=readable,
            frets=(min(frets), max(frets)) if frets else None,
            techniques=sorted({t for n in notes for t in n["tech"]
                               if t != "-"}),
            strings=float(statistics.mean(n["chord"] for n in notes)),
        ))
    return sorted(out, key=lambda p: (-p["readable"], -p["notes"]))


def timing(run: Run) -> dict[int, float]:
    """How far each bar's strikes sat from the notes written in it.

    Honest even where the pitch is not: a strike proves something was played
    at that moment, whatever the detector made of it.
    """
    per_bar: dict[int, list[float]] = defaultdict(list)
    for note in run.notes:
        if note["bar"] is None or note["verdict"] == "pending":
            continue
        strike, gap = run.nearest_strike(note["ms"])
        if strike is not None and gap is not None:
            per_bar[note["bar"]].append(strike["adjusted"] - note["ms"])
    return {bar: statistics.median(gaps)
            for bar, gaps in per_bar.items() if len(gaps) >= 4}


def report(run: Run) -> str:
    lines = []
    head = run.head
    reached = int(head.get("notes_reached", 0) or 0)
    hits = int(head.get("hits", 0) or 0)
    lines.append(f"{head.get('song', run.path.name)}")
    lines.append(f"  {hits}/{reached} notes"
                 + (f" ({100 * hits / reached:.0f} %)" if reached else "")
                 + f", at {head.get('tempo_percent', '?')} % speed"
                 + ("" if head.get("played_to_the_end") == "True"
                    else f", stopped at {float(head.get('reached_ms', 0))/1000:.0f} s"))

    doubts = trustworthy(run)
    if doubts:
        lines.append("\n  READ NOTHING ELSE HERE UNTIL THIS IS FIXED:")
        for doubt in doubts:
            lines.append(f"    - {doubt}")

    misses = defaultdict(int)
    intervals = defaultdict(int)
    for note in run.notes:
        if note["verdict"] in ("hit", "close", "pending"):
            continue
        reason = run.why(note)
        if reason.startswith("wrong pitch"):
            intervals[reason[len("wrong pitch ("):-1]] += 1
            reason = "wrong pitch"
        misses[reason] += 1
    total = sum(misses.values())
    if total:
        lines.append(f"\n  {total} notes did not count. Why:")
        for reason, count in sorted(misses.items(), key=lambda kv: -kv[1]):
            about = ("your playing" if reason.startswith(PLAYER_FAULTS)
                     else "the detector, not you")
            lines.append(f"    {count:5}  {100 * count / total:5.1f} %  "
                         f"{reason:22} -- {about}")
        if intervals:
            # Which way the wrong ones went. One fret flat over and over is a
            # hand that has drifted; scatter is reading the tab wrong.
            common = sorted(intervals.items(), key=lambda kv: -kv[1])[:4]
            lines.append("           of those, off by: "
                         + ", ".join(f"{k} semitones {v}x" for k, v in common))

    found = passages(run)
    if run.notes and all(n["bar"] is None for n in run.notes):
        lines.append("\n  This log has no bar numbers, so no passage can be "
                     "named -- it was written by an older build. One run with "
                     "the current one is all it takes.")
    elif not found:
        lines.append("\n  No weak passage stands out.")
    else:
        lines.append("\n  Where the practice time goes:")
        for passage in found[:6]:
            span = (f"bar {passage['bars'][0]}"
                    if len(passage["bars"]) == 1
                    else f"bars {passage['bars'][0]}-{passage['bars'][-1]}")
            lines.append(
                f"\n    {span}  ({passage['start_ms'] / 1000:.0f} s)  "
                f"{passage['good']}/{passage['notes']}")
            if passage["frets"]:
                lines.append(f"      frets {passage['frets'][0]}-"
                             f"{passage['frets'][1]}, "
                             f"{passage['strings']:.1f} strings per strike"
                             + (f", techniques: "
                                f"{' '.join(passage['techniques'])}"
                                if passage["techniques"] else ""))
            failures = sum(passage["reasons"].values())
            if failures and passage["readable"] / failures < 0.5:
                lines.append("      Mostly unreadable here -- little of this "
                             "is about the playing.")
            for reason, count in sorted(passage["reasons"].items(),
                                        key=lambda kv: -kv[1])[:3]:
                lines.append(f"      {count:3}x {reason}")

    late = timing(run)
    if late:
        worst = sorted(late.items(), key=lambda kv: -abs(kv[1]))[:3]
        overall = statistics.median(late.values())
        lines.append(f"\n  Timing: median {overall:+.0f} ms over "
                     f"{len(late)} bars"
                     + (" (early = ahead of the beat)" if overall < 0 else ""))
        for bar, offset in worst:
            lines.append(f"    bar {bar:4}  {offset:+6.0f} ms")
    return "\n".join(lines)




# ── Several logs at once: what RECURS ───────────────────────────────────────
#
# One log says why one evening went as it did. "What am I persistently bad
# at" is a different question, and answering it from a folder of logs needs
# three things the single-log report above does not:
#
#  1. **The same sitting is in there several times.** `D` writes a file on
#     request, and it is NOT in `App.NEVER_REPEAT` -- so a held key writes one
#     every 40 ms, and pressing it twice during one run writes two logs of
#     which the first is a PREFIX of the second. Measured on the player's own
#     folder: eight files of 19 294 bytes within twelve seconds, five of
#     57 093, three of 50 032. Counted as evenings, one sitting would weigh
#     eight.
#
#  2. **Logs from different builds are not one measurement.** Over eleven
#     weeks of this project the onset threshold went 0.30 -> 0.05 (+296 notes
#     on one song), the audio anchor was fixed (29 % -> 40 %), the gate stopped
#     discarding 40 % of the audio, and the subharmonic rescue shipped. A
#     "pattern" computed across that is largely the app improving. So runs are
#     banded, and nothing is ever averaged across a band.
#
#  3. **A RATE is about the detector; a DISTRIBUTION is about the hand.** How
#     many notes came back red moves with every threshold. WHICH frets, WHICH
#     strings and WHICH interval they were wrong by moves far less: a clean
#     reading of a different pitch at high confidence is a finger, whatever
#     the onset was set to. So every group below is reported as a rate
#     RELATIVE to its own band, never as an absolute score.

#: A new band starts after this many calendar days without a log. Reasoned,
#: not fitted -- there is nothing to fit it against, and it is named here
#: rather than buried so it can be re-measured once a longer history exists.
#: A fortnight is how long it has historically taken this project to change
#: the detector in a way that moves every number in a log.
BAND_GAP_DAYS = 14

#: What a log says when it does not know which build wrote it. `build_info`
#: answers "unknown build" from an EXE with no stamp, and a log written
#: before that field existed carries nothing at all. Neither is a build, so
#: neither may end a band.
UNSTAMPED = ("", "unknown build")

#: A group with fewer judgeable notes than this says nothing. The same
#: argument as MIN_BAR_NOTES, one level up.
MIN_GROUP_NOTES = 12


def played_day(path) -> str | None:
    """The calendar day out of the FILENAME: run_<song>_YYYYMMDD_HHMMSS.txt.

    Not the modification time. A log copied to a USB stick and back carries a
    new mtime and the same name, and this history is meant to travel between
    machines -- so the name is the only stamp that survives the journey.
    """
    stem = Path(path).stem
    for piece in reversed(stem.split("_")):
        if len(piece) == 8 and piece.isdigit():
            return f"{piece[:4]}-{piece[4:6]}-{piece[6:]}"
    return None


def _ordinal(day: str) -> int:
    """A day as a number, for gaps. Calendar-correct without datetime."""
    y, m, d = (int(p) for p in day.split("-"))
    # Days since an arbitrary epoch; only differences are ever used.
    m = (m + 9) % 12
    y = y - m // 10
    return 365 * y + y // 4 - y // 100 + y // 400 + (m * 306 + 5) // 10 + d


def recent_days(paths, days: int) -> list[Path]:
    """The logs belonging to the last `days` distinct days anybody played.

    Distinct DAYS rather than files: an evening that produced eleven logs is
    one day, and "the last twenty" has to mean twenty sittings and not twenty
    files, or a single busy evening swallows the whole window.
    """
    dated = [(played_day(p), Path(p)) for p in paths]
    known = sorted({day for day, _ in dated if day})
    keep = set(known[-days:]) if days > 0 else set(known)
    return [p for day, p in dated if day in keep]


def deduplicate(runs: list[Run]) -> tuple[list[Run], int]:
    """(the distinct sittings, how many files collapsed into them).

    Two logs are the same sitting when one's strike table is a PREFIX of the
    other's: that is what pressing `D` twice during one run produces, and it
    is why a byte-for-byte comparison is not enough -- the second file is
    longer, not identical. The longest survives, because it saw the most.

    A log with no strikes at all is never collapsed: nothing in it can show
    that it is the same evening as another, and guessing would throw away a
    sitting. Those are the 2 kB files where the microphone was dead.
    """
    kept: list[Run] = []
    dropped = 0
    # Keyed by song AND DAY: the prefix rule says "the same run, written
    # again", and that can only happen within one sitting. Keyed by song
    # alone, two evenings of the same piece whose strike tables agree would
    # collapse into one -- which a test caught, and which would have silently
    # halved a song's history.
    by_sitting: dict[tuple, list[Run]] = defaultdict(list)
    for run in runs:
        by_sitting[(run.head.get("song", run.path.name),
                    played_day(run.path))].append(run)

    for group in by_sitting.values():
        # Longest first, so a survivor is never superseded by a later look.
        group.sort(key=lambda r: len(r.strikes), reverse=True)
        survivors: list[Run] = []
        for run in group:
            if not run.strikes:
                survivors.append(run)
                continue
            mine = [(s["ms"], s["midi"]) for s in run.strikes]
            if any([(s["ms"], s["midi"]) for s in other.strikes[:len(mine)]]
                   == mine for other in survivors):
                dropped += 1
                continue
            survivors.append(run)
        kept += survivors
    kept.sort(key=lambda r: (played_day(r.path) or "", r.path.name))
    return kept, dropped


def bands(runs: list[Run]) -> list[dict]:
    """The runs split into stretches that measured the same instrument.

    A band ends at a change of `build`, or at a gap of BAND_GAP_DAYS. The
    build is the real answer and the gap is the fallback, because the stamp
    only became reliable late: every EXE the release workflow produced before
    it learnt to stamp reports "unknown build", so most of this player's
    history carries no build at all and only the calendar can separate it.

    **A log that does not say which build it is cannot contradict the one
    around it.** That is the same presumption of innocence the denominator
    runs on, and without it the report fragments: on this player's own
    2026-09-07 an unstamped log between two stamped ones split one evening
    into FOUR bands of 1, 6, 1 and 2 sittings, and 2026-09-09 into another
    four. Fifteen bands where the history holds about five, most of them
    too small for a single group to clear MIN_GROUP_NOTES. A band's build
    is the first one that really named itself.
    """
    out: list[dict] = []
    for run in sorted(runs, key=lambda r: (played_day(r.path) or "",
                                           r.path.name)):
        day = played_day(run.path)
        build = run.head.get("build", "").strip()
        if build in UNSTAMPED:
            build = ""
        if out:
            last = out[-1]
            same_build = not build or not last["build"] or build == last["build"]
            near = (day is None or last["last_day"] is None
                    or _ordinal(day) - _ordinal(last["last_day"])
                    <= BAND_GAP_DAYS)
            if same_build and near:
                last["runs"].append(run)
                last["last_day"] = day or last["last_day"]
                last["build"] = last["build"] or build
                continue
        out.append(dict(build=build, runs=[run],
                        first_day=day, last_day=day))
    return out


# ── The groups. Each takes a note and the one before it, and returns a
#    label or None for "this note says nothing about this question".

def _fret_region(note, previous) -> str | None:
    fret = note["fret"]
    if fret is None:
        return None
    if fret == 0:
        return "open"
    if fret <= 4:
        return "frets 1-4"
    if fret <= 11:
        return "frets 5-11"
    return "frets 12+"


def _string(note, previous) -> str | None:
    return f"string {note['string']}"


def _string_change(note, previous) -> str | None:
    """Whether the hand had to cross to another string to reach this note.

    The open question this exists to settle: on the reference takes every
    string change was heard (40/40) and the same-string notes were not
    (133/159) -- but every string change in that song sits in the slow
    opening, so the takes hold the easy half of the case and none of the
    hard half.
    """
    if previous is None:
        return None
    return ("same string" if previous["string"] == note["string"]
            else "string change")


def _chord_size(note, previous) -> str | None:
    written = note["chord"]
    if written <= 1:
        return "single note"
    if written == 2:
        return "2 strings"
    return "3+ strings"


def _technique(note, previous) -> str | None:
    marks = [t for t in note["tech"] if t != "-"]
    return f"technique {''.join(sorted(marks))}" if marks else None


GROUPS = (
    ("Where on the neck", _fret_region),
    ("Which string", _string),
    ("Crossing strings", _string_change),
    ("How many strings written", _chord_size),
    ("What the tab asks for", _technique),
)


def patterns(runs: list[Run]) -> dict:
    """Every judged note of these runs, grouped every way, with its verdict.

    The denominator is deliberately NOT every note: a note whose failure the
    detector could not read might have been played right or wrong, and
    counting it either way is a claim on no evidence. So each group reports
    faults over (good + faults) and says how many it had to set aside -- the
    presumption of innocence this project runs on, applied to the arithmetic
    rather than to one string of a chord.
    """
    tally: dict[str, dict[str, dict]] = {name: defaultdict(
        lambda: dict(good=0, faults=0, unreadable=0, intervals=defaultdict(int))
    ) for name, _ in GROUPS}
    timing_by: dict[str, list[float]] = defaultdict(list)
    # The band's own baseline, counted here rather than summed out of a
    # group. Every picker is allowed to answer None -- `_fret_region` does
    # it for a log written before the fret column existed, `_technique` for
    # every ordinary note -- so a total read off one group is a total over
    # whatever that group happened to see. On this player's first band the
    # fret group holds 1285 judgeable notes and the string group 20685, so
    # the "+30 pts worse than this band" beside every row was measured
    # against a population sixteen times smaller than the rows above it.
    total = dict(good=0, faults=0, unreadable=0)
    every_interval: dict[str, int] = defaultdict(int)

    for run in runs:
        ordered = sorted((n for n in run.notes
                          if n["verdict"] != "pending"),
                         key=lambda n: n["ms"])
        previous = None
        for note in ordered:
            good = note["verdict"] in ("hit", "close")
            reason = None if good else run.why(note)
            fault = bool(reason and reason.startswith(PLAYER_FAULTS))
            interval = (reason[len("wrong pitch ("):-1]
                        if fault and reason.startswith("wrong pitch")
                        else None)
            total["good" if good else "faults" if fault else "unreadable"] += 1
            if interval is not None:
                every_interval[interval] += 1
            for name, pick in GROUPS:
                label = pick(note, previous)
                if label is None:
                    continue
                cell = tally[name][label]
                if good:
                    cell["good"] += 1
                elif fault:
                    cell["faults"] += 1
                    if interval is not None:
                        cell["intervals"][interval] += 1
                else:
                    cell["unreadable"] += 1
            # Timing is honest where the pitch is not: a strike proves
            # something was struck at that moment. Chords against single
            # notes, WITHIN a run, which is what makes it immune to every
            # offset this app has ever got wrong.
            strike, gap = run.nearest_strike(note["ms"])
            if strike is not None and gap is not None:
                timing_by["chord" if note["chord"] > 1
                          else "single"].append(strike["adjusted"] - note["ms"])
            previous = note

    return dict(tally={name: dict(cells) for name, cells in tally.items()},
                total=total, intervals=dict(every_interval),
                timing={k: v for k, v in timing_by.items()})


def _kind_of(doubt: str) -> str:
    """One row per KIND of fault, not one per number inside the wording.

    `trustworthy` writes the measurement into the sentence -- "43 % of the
    audio was below the noise gate" -- so counting the sentences gave this
    player's first band FORTY rows, most of them 1x, for what is one fault
    with forty readings. The number belongs in the single-log report, where
    it is about one run; here the question is how many evenings went that
    way.
    """
    for mark, kind in (("below the noise gate", "the noise gate ate the audio"),
                       ("hears the room", "the input heard the room, "
                                          "not the guitar"),
                       ("peaks at", "the input was too quiet for the pitch"),
                       ("dropped buffers", "dropped buffers -- "
                                           "notes lost at random"),
                       ("after a seek", "written straight after a seek")):
        if mark in doubt:
            return kind
    return doubt.split("(")[0].strip()


def _rate(cell: dict) -> float | None:
    """Faults over the notes where a verdict could be read at all."""
    judged = cell["good"] + cell["faults"]
    return cell["faults"] / judged if judged else None


def pattern_report(runs: list[Run], dropped: int) -> str:
    """What recurs, band by band, with nothing averaged across a band."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("WHAT RECURS")
    lines.append("=" * 72)
    days = sorted({d for d in (played_day(r.path) for r in runs) if d})
    lines.append(f"{len(runs)} sittings over {len(days)} playing days"
                 + (f", {days[0]} to {days[-1]}" if days else "")
                 + (f" ({dropped} file"
                    + ("s were" if dropped != 1 else " was")
                    + " the same sitting written again)" if dropped else ""))

    for band in bands(runs):
        lines.append("")
        lines.append("-" * 72)
        span = (f"{band['first_day']} to {band['last_day']}"
                if band["first_day"] != band["last_day"]
                else str(band["first_day"]))
        lines.append(f"BAND  {span}   build: "
                     f"{band['build'] or '(none recorded)'}")
        lines.append(f"      {len(band['runs'])} sittings")

        # The trust check outranks everything, the same as in the single-log
        # report. A run whose input heard the room contributes nothing but
        # noise, so it is left OUT rather than averaged in.
        trusted, excluded = [], defaultdict(int)
        for run in band["runs"]:
            doubts = trustworthy(run)
            if doubts:
                for doubt in doubts:
                    excluded[_kind_of(doubt)] += 1
            else:
                trusted.append(run)
        if excluded:
            lines.append(f"      {len(band['runs']) - len(trusted)} of them "
                         f"left out as unreadable:")
            for doubt, count in sorted(excluded.items(), key=lambda kv: -kv[1]):
                lines.append(f"        {count:3}x {doubt}")
        if not trusted:
            lines.append("      Nothing readable in this band. The numbers "
                         "above say why, and they are fixed at the "
                         "interface, not by practising.")
            continue

        found = patterns(trusted)
        # The band's own baseline, over every judged note of the band and
        # independent of any one group. Every group is read against THIS,
        # never against another band: a rate is about the detector and only
        # its position relative to its own band is about the hand.
        total = found["total"]
        base = _rate(total)
        if base is None:
            lines.append("      No note in this band could be judged.")
            continue
        lines.append(f"      {total['faults']} readable mistakes in "
                     f"{total['good'] + total['faults']} judgeable notes "
                     f"-- {100 * base:.0f} %"
                     f"   ({total['unreadable']} set aside as unreadable)")

        for name, _ in GROUPS:
            cells = {label: cell
                     for label, cell in found["tally"][name].items()
                     if cell["good"] + cell["faults"] >= MIN_GROUP_NOTES}
            if len(cells) < 2:
                continue        # one group cannot be above or below itself
            lines.append(f"\n      {name}:")
            ordered = sorted(cells.items(),
                             key=lambda kv: -(_rate(kv[1]) or 0))
            for label, cell in ordered:
                rate = _rate(cell)
                judged = cell["good"] + cell["faults"]
                away = 100 * (rate - base)
                flag = ("  <-- worse than this band" if away >= 10
                        else "  (better)" if away <= -10 else "")
                lines.append(f"        {label:<26}{100 * rate:5.0f} %  of "
                             f"{judged:5} notes   {away:+5.0f} pts{flag}")
                if cell["unreadable"] > judged:
                    lines.append(f"{'':<34}(more unreadable than judgeable "
                                 f"here -- read this one with care)")

        # Which way the wrong ones went, over the whole band. One fret flat
        # over and over is a hand that has drifted; scatter is reading the
        # tab wrong, and the two are practised differently.
        every = found["intervals"]
        if every:
            lines.append("\n      Wrong by how much:")
            for interval, count in sorted(every.items(),
                                          key=lambda kv: -kv[1])[:6]:
                lines.append(f"        {interval:>4} semitones   {count:4}x")

        chord = found["timing"].get("chord") or []
        single = found["timing"].get("single") or []
        if len(chord) >= 20 and len(single) >= 20:
            lines.append("\n      Timing, within the run "
                         "(early = ahead of the beat):")
            lines.append(f"        single notes   "
                         f"{statistics.median(single):+6.0f} ms  "
                         f"of {len(single)} strikes")
            lines.append(f"        chords         "
                         f"{statistics.median(chord):+6.0f} ms  "
                         f"of {len(chord)} strikes")
            apart = statistics.median(chord) - statistics.median(single)
            if abs(apart) >= 30:
                lines.append(f"        the chords lead the single notes by "
                             f"{-apart:.0f} ms"
                             if apart < 0 else
                             f"        the chords trail the single notes by "
                             f"{apart:.0f} ms")
    return "\n".join(lines)


#: What `--patterns` takes when nothing says otherwise. Twenty sittings is
#: enough for a distribution to mean something and short enough that it is
#: mostly the current build.
DEFAULT_DAYS = 20


def _expand(words: list[str]) -> list[Path]:
    """The files named, with wildcards resolved HERE.

    The Windows command prompt does not expand `*`, so
    `coach.py %USERPROFILE%\\.pickhero\\run_*.txt` arrives as one literal
    word with a star in it and every path in this tool would be a file that
    is not there. cmd is the shell this is run from, so the expansion has to
    live in the tool.
    """
    out: list[Path] = []
    for word in words:
        path = Path(word)
        if path.exists():
            out.append(path)
            continue
        if any(c in word for c in "*?["):
            parent = path.parent if str(path.parent) != "" else Path(".")
            out += sorted(parent.glob(path.name))
            continue
        out.append(path)              # keep it, so the error names it
    return out


def _never_die_on_a_song_name() -> None:
    """A character that cannot be shown becomes a question mark.

    Every literal in this file is cp850-clean and a test holds it there --
    but a song NAME comes out of the log, and this player's own folder has
    "I Prevail - There\u2019s Fear In Letting Go" with a curly apostrophe,
    which cp850 cannot encode either. Without this the whole analysis runs,
    every number is computed, and the report is replaced by a traceback
    because of one character in one title. cp1252 encodes it fine, which is
    how a fault like this reaches only the console the player actually has.
    """
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):      # not a real stream, or too old
        pass


def main(argv: list[str]) -> int:
    _never_die_on_a_song_name()
    words = [a for a in argv[1:]]
    show_patterns = "--patterns" in words
    each = "--each" in words
    days = DEFAULT_DAYS if show_patterns else 0
    if "--days" in words:
        at = words.index("--days")
        try:
            days = int(words[at + 1])
        except (IndexError, ValueError):
            print("--days needs a number (0 means every day there is)")
            return 2
        del words[at:at + 2]
    paths = _expand([w for w in words if not w.startswith("--")])
    if not paths:
        print(__doc__)
        return 2

    missing = [p for p in paths if not p.is_file()]
    if missing:
        for path in missing:
            print(f"not there: {path}")
        paths = [p for p in paths if p.is_file()]
    if not paths:
        return 2

    if show_patterns and days > 0:
        before = len(paths)
        paths = recent_days(paths, days)
        print(f"{len(paths)} of {before} logs are from the last "
              f"{days} playing days\n")
        if not paths:
            return 2

    if not show_patterns or each:
        for path in paths:
            print(report(Run(path)))
            print()
    if show_patterns:
        runs, dropped = deduplicate([Run(p) for p in paths])
        print(pattern_report(runs, dropped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
