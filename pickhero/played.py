"""The score for the stretch you actually played.

*"Habe nur die 2. Hälfte des Songs gespielt. Kann ich dann auch für den
gespielten Teil eine Bewertung haben?"*

His run: 54.6 % over 324 notes, and 75 of those he never attempted -- the song
ran from the start while he waited, so bars 18-25 are `miss` with no strike
anywhere near them. `get_statistics` counts everything REACHED, and a note the
playhead crossed while nobody was playing was reached. Over the part he did
play the same run is **71.7 %**.

**A bar is "played" if the microphone heard a strike in it**, and every
written note in such a bar counts -- the ones that went wrong included. That
is the whole rule, and the two things it refuses are what make it honest:

- **Not "a bar where something scored".** A bar played entirely wrong has no
  hit and no close, so scoring only the bars that went well would quietly drop
  the hardest ones and hand back a flattering number -- which is the same
  fault, in the other direction, as the one being fixed.
- **Not a span from the first strike to the last.** A single interval cannot
  say "I played the intro and the solo and sat out the middle", and it would
  charge him for a section he deliberately skipped. Bars are what a player
  counts in, what a loop is set in, and what the weakest-section line already
  names.

A stray strike puts its bar in, which is why the bars are NAMED on screen
rather than only the percentage: a range that is not the one you played is a
thing you can see, where a number on its own is not.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Played:
    """What was played, and how it went."""

    hits: int = 0
    close: int = 0
    total: int = 0
    first_bar: int | None = None
    last_bar: int | None = None
    #: Notes the playhead crossed in bars nobody played in.
    skipped: int = 0

    @property
    def percent(self) -> float | None:
        """None rather than 0.0 where nothing was played.

        Nothing played is not the same as everything missed, and a zero would
        claim it was -- the rule the strip's own numbers already follow.
        """
        return 100.0 * self.hits / self.total if self.total else None

    @property
    def worth_saying(self) -> bool:
        """Whether this says anything the overall score does not.

        A song played from end to end has nothing here: the two numbers are
        the same and a line that repeats one already on screen is the
        wallpaper the HUD was cut down to remove.
        """
        return self.total > 0 and self.skipped > 0

    def bars_text(self) -> str:
        if self.first_bar is None:
            return ""
        if self.first_bar == self.last_bar:
            return f"bar {self.first_bar}"
        return f"bars {self.first_bar}-{self.last_bar}"


def bar_of(ms: float, bar_starts: Sequence[float]) -> int | None:
    """Which bar a moment falls in, numbered the way the run log numbers it."""
    if not bar_starts:
        return None
    return bisect.bisect_right(bar_starts, ms + 1e-6)


def played_bars(strike_ms: Iterable[float],
                bar_starts: Sequence[float]) -> set[int]:
    """The bars the microphone heard something in."""
    out = set()
    for ms in strike_ms:
        bar = bar_of(ms, bar_starts)
        if bar is not None:
            out.add(bar)
    return out


def score(notes: Iterable[tuple[float, bool, bool, bool]],
          strike_ms: Iterable[float],
          bar_starts: Sequence[float]) -> Played:
    """Score the bars that were played.

    `notes` is `(timestamp_ms, reached, is_hit, is_close)` per written note --
    plain values rather than matcher objects, so this is testable without a
    timeline and cannot grow a second opinion about what a verdict is.
    """
    bars = played_bars(strike_ms, bar_starts)
    if not bars:
        return Played()
    hits = close = total = skipped = 0
    first = last = None
    for ms, reached, is_hit, is_close in notes:
        if not reached:
            continue                      # never in front of the player at all
        bar = bar_of(ms, bar_starts)
        if bar not in bars:
            skipped += 1
            continue
        total += 1
        hits += bool(is_hit)
        close += bool(is_close)
        first = bar if first is None else min(first, bar)
        last = bar if last is None else max(last, bar)
    return Played(hits=hits, close=close, total=total,
                  first_bar=first, last_bar=last, skipped=skipped)
