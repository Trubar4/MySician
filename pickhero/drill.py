"""The ladder: a passage played slowly until it is clean, then faster.

*"Was könnten wir machen, damit ich gezieltes Feedback bekomme und üben
kann?"* -- and the answer that needed the least new machinery is the one
Rocksmith's riff repeater is: `error_nests` already finds the bars that keep
going wrong and `N` already loops one of them, so what was missing is the
SESSION. Slow, twice clean, faster, twice clean, up to full speed.

Everything about it is arithmetic over one pass's verdicts, so it lives here
with no pygame and no matcher -- the rule is tested on counts, the way
`runs.error_nests` is tested on strings.

- **Four steps, two clean passes each**, which is the player's own choice
  from four offered. Eight passes at best; on a three-bar nest at 132 BPM
  that is about a minute.
- **A pass with a mistake repeats the step and never drops below it.** The
  clean counter goes back to zero; the speed does not. Rocksmith's dynamic
  difficulty does step back and it was offered and declined -- *"nur
  wiederholen, nie zurück"* -- and the reason it is a defensible choice
  rather than a soft one is that the player holds PgUp/PgDn throughout and
  can move the speed himself at any moment, which ENDS the drill (see
  `by_hand` at the call site). An automatic that silently undoes what you
  just set by hand is worse than one that was never offered.
- **A pass where nothing was judged is not a clean pass.** Audio off, a
  guitar not plugged in, a passage nobody played: those are no evidence, and
  crediting them would walk the ladder to 100 % without a note being heard.
  That is the presumption of innocence this project runs on, pointed the
  other way: absence of evidence does not acquit either.
- **What counts as a mistake is `runs.is_error` and nothing else**, so the
  drill, "most frequent errors" and `N` all mean the same thing by it. A
  CLOSE is the right note played off the beat -- the timing percentage
  answers for that -- and a DRAINED miss is the app saying it could not
  tell.
"""

from __future__ import annotations

from dataclasses import dataclass

from pickhero import runs

#: The speeds, in order. Ends at full speed by construction: a ladder that
#: stops below it would leave the passage unplayed at the speed it is in.
LADDER: tuple[float, ...] = (0.70, 0.80, 0.90, 1.00)

#: Clean passes needed to move up. Two rather than one, because one clean
#: pass of three bars is inside what luck covers; and rather than three,
#: because fifteen passes is a sitting and not an exercise.
CLEAN_PASSES = 2

#: What a pass was.
CLEAN, WRONG, NOTHING = "clean", "wrong", "nothing"


@dataclass
class Drill:
    """One passage being walked up the ladder."""

    start_ms: float
    end_ms: float
    where: str = ""
    #: The practice speed to put back when this is over. The drill moves the
    #: speed and has to leave the song as it found it.
    restore_tempo: float = 1.0
    step: int = 0
    clean: int = 0
    passes: int = 0
    finished: bool = False

    @property
    def tempo(self) -> float:
        """The speed this step is played at."""
        return LADDER[min(self.step, len(LADDER) - 1)]

    @property
    def steps(self) -> int:
        return len(LADDER)

    def record(self, wrong: int, judged: int) -> str:
        """Take one pass's verdicts and say what it was.

        `wrong` is how many notes of the passage `runs.is_error` calls a
        mistake; `judged` is how many got any verdict at all. Returns
        CLEAN, WRONG or NOTHING -- the caller says it out loud, because a
        pass that changed nothing still has to be visible.
        """
        if self.finished:
            return NOTHING
        self.passes += 1
        if judged <= 0:
            return NOTHING
        if wrong > 0:
            self.clean = 0
            return WRONG
        self.clean += 1
        if self.clean >= CLEAN_PASSES:
            self.clean = 0
            if self.step + 1 >= len(LADDER):
                self.finished = True
            else:
                self.step += 1
        return CLEAN

    def line(self) -> str:
        """The one line the HUD shows while this is running."""
        if self.finished:
            return f"Drill done — {self.where} clean at 100 %"
        where = f"{self.where} — " if self.where else ""
        return (f"Drill: {where}{int(round(self.tempo * 100))} % — "
                f"{self.clean} of {CLEAN_PASSES} clean "
                f"(step {self.step + 1} of {len(LADDER)})")


def count(marks: str) -> tuple[int, int]:
    """(mistakes, notes judged) for one pass, from the verdict characters.

    Read through `runs.is_error` so there is one answer in the project to
    what a mistake is -- and `.` is the character for a note never judged,
    which is what separates "played it clean" from "played nothing".
    """
    judged = sum(1 for c in marks if c != runs.NOTHING)
    wrong = sum(1 for c in marks if runs.is_error(c))
    return wrong, judged


def clean_runs(passes) -> tuple[int, int]:
    """(clean, reached) over several passes at one passage.

    *"Wie demanding sind zwei saubere Durchgänge wirklich?"* -- the honest
    answer at the time this shipped was that nothing had been measured,
    because the run history starts the day it ships. It does not any more:
    the player's own evenings are stored per note, so the same rule that
    judges a drill pass can be run over every one of them and say how often
    this passage has in fact been played clean.

    - **The rule is `count` and nothing else**, so what the drill demands and
      what the history reports are the same question. Two readers of one rule
      is how they come to disagree, and here that would be a number that
      quietly contradicts the ladder beside it.
    - **A run that never reached the passage does not vote**, the way it does
      not vote in "frequent errors": a note nobody got to is evidence of
      neither, and counting it in the denominator would make every passage at
      the end of a song look impossible.
    - **This measures, it does not decide.** `CLEAN_PASSES` stays at two; what
      changes is that the player can see whether two is trivial or brutal for
      the bars in front of him, which is the thing a constant nobody has
      fitted most needs beside it.
    """
    clean = reached = 0
    for marks in passes:
        wrong, judged = count(marks)
        if not judged:
            continue
        reached += 1
        if not wrong:
            clean += 1
    return clean, reached
