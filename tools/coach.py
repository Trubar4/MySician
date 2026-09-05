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
- **A clean reading of a different pitch** says which fret was hit instead.
- **Timing** is honest even where the pitch is not: a subharmonic strike
  proves something was struck at that moment, whatever it was.
- Everything else is COUNTED and named as unreadable rather than dressed up
  as a mistake. Absence of evidence is the commonest thing in this signal
  path, and this file's whole argument is that it must not be scored.

Usage:
    python tools/coach.py ~/.pickhero/run_*.txt
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
# What counts as a passage worth naming. Not a grade: a bar under this, in a
# run whose readable notes mostly land, is where the practice time goes.
WEAK_SHARE = 0.62
# Fewer readable notes than this in a passage and the verdict is "could not
# be read", not "played badly".
MIN_READABLE = 6


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
                         f"{reason:22} — {about}")
        if intervals:
            # Which way the wrong ones went. One fret flat over and over is a
            # hand that has drifted; scatter is reading the tab wrong.
            common = sorted(intervals.items(), key=lambda kv: -kv[1])[:4]
            lines.append("           of those, off by: "
                         + ", ".join(f"{k} semitones {v}x" for k, v in common))

    found = passages(run)
    if run.notes and all(n["bar"] is None for n in run.notes):
        lines.append("\n  This log has no bar numbers, so no passage can be "
                     "named — it was written by an older build. One run with "
                     "the current one is all it takes.")
    elif not found:
        lines.append("\n  No weak passage stands out.")
    else:
        lines.append("\n  Where the practice time goes:")
        for passage in found[:6]:
            span = (f"bar {passage['bars'][0]}"
                    if len(passage["bars"]) == 1
                    else f"bars {passage['bars'][0]}–{passage['bars'][-1]}")
            lines.append(
                f"\n    {span}  ({passage['start_ms'] / 1000:.0f} s)  "
                f"{passage['good']}/{passage['notes']}")
            if passage["frets"]:
                lines.append(f"      frets {passage['frets'][0]}–"
                             f"{passage['frets'][1]}, "
                             f"{passage['strings']:.1f} strings per strike"
                             + (f", techniques: "
                                f"{' '.join(passage['techniques'])}"
                                if passage["techniques"] else ""))
            if passage["readable"] < MIN_READABLE:
                lines.append("      Not enough of it could be read to say "
                             "anything about the playing.")
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


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv[1:]]
    if not paths:
        print(__doc__)
        return 2
    for path in paths:
        print(report(Run(path)))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
