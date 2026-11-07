"""Which tuning is the guitar in? Ask for the strings that decide it.

*"Ich vergesse über Nacht oft, was ich als letztes als Tuning hatte."* The
tuner already asks the player to say which tuning they are in -- and that is
the one thing they came to find out. The information is in the instrument:
six open strings name a tuning outright.

What this module is, and is not: it is arithmetic over `NAMED_TUNINGS` and a
small amount of state. No audio, no pygame, no clock -- so the whole rule is
tested without a screen, and the screen cannot hold a second opinion about
what a reading means.

**It is a GUIDED query rather than a listener, and the reason is the
fold.** Through aubio's yinfast the low E of a guitar comes back as a THIRD
of its pitch on every confident reading of a clean take, so a detector that
simply listens cannot read the one string that matters most -- and
multiplying a reading back up is only safe when something else says which
string it is. The tuner already learnt that the hard way: a reading of
27.5 Hz is the low E over three AND the A string over four, two cents
apart. So the app NAMES the string it wants, which is what makes the fold a
hypothesis to confirm instead of a guess -- the same shape as
`ChordVerifier.confirms`.

**It asks only the strings that decide.** Measured over the sixteen named
tunings: the high three strings separate only 10 of them (Standard and
Drop D are identical there -- a Drop tuning moves string 6 and nothing
else), the low three separate 14, and no three strings separate all
sixteen. Asked low string first, skipping any string every surviving
candidate agrees about, the query finishes in **two** questions for 11 of
the 16 and needs four at worst (Drop D against DADGAD, which differ only on
strings 2 and 1).

**Five agreeing readings, unbroken.** A single frame can be a harmonic, a
neighbour still ringing, or a fold of something else. Narrowing on one is
the expensive mistake, because it changes the targets the NEXT string is
read against -- so a disagreeing reading resets the run rather than being
averaged into it, the same rule the bend hold uses.

**It convicts no reading it cannot place.** A reading further than half a
semitone from every pitch a surviving candidate uses on that string names
nothing, and one that sits between two of them -- which is a string a
quarter-tone out -- refuses and says which two. Guessing there would set
the wrong targets for every string after it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from pickhero.audio.note_utils import (
    NAMED_TUNINGS, freq_to_cents_deviation, midi_to_freq, midi_to_name,
    tuning_notes,
)

#: Strings are asked in this order -- the low E first, the way a guitarist
#: tunes. It is also where the information is: the low three strings
#: separate 14 of the 16 named tunings where the high three separate 10.
ASK_ORDER = (6, 5, 4, 3, 2, 1)

#: How far a reading may sit from a candidate pitch and still be about it.
#: Half a semitone, because the candidate pitches for one string are
#: semitones apart: anything further is a different note, not this one
#: mistuned.
MATCH_CENTS = 50.0

#: Accepted, but said out loud. A string this far off is still plainly that
#: note -- and it is also a string worth tuning before anything else is
#: read, because the next question's targets depend on this answer.
OUT_OF_TUNE_CENTS = 30.0

#: What a reading may be multiplied by to reach its fundamental. Two for the
#: ordinary octave error, three because that is what the low E really does.
#: Four and five are not here for the reason the tuner gives: A2 over four
#: and E2 over three are two cents apart, so a reading there cannot be
#: attributed to a string by any rule at all.
FOLD_MULTIPLES = (1, 2, 3)

#: Agreeing readings before a string is taken as answered.
CONFIRM_READINGS = 5


def pitches_on(string: int,
               candidates: list[tuple[str, dict[int, int]]]) -> list[int]:
    """The distinct pitches these tunings use on that string, lowest first."""
    return sorted({shape[string] for _, shape in candidates
                   if string in shape})


def candidates_for(heard: dict[int, int],
                   tunings: list[tuple[str, dict[int, int]]] | None = None
                   ) -> list[tuple[str, dict[int, int]]]:
    """Every named tuning still consistent with the strings heard so far."""
    pool = NAMED_TUNINGS if tunings is None else tunings
    return [(name, shape) for name, shape in pool
            if all(shape.get(s) == midi for s, midi in heard.items())]


def fifth_pair(pitches: list[int]) -> tuple[int, int] | None:
    """Two candidate pitches a FIFTH apart, which no fold can tell apart.

    If a reading is half of pitch A it is also, to within two cents, a
    third of A+7 -- because 3/2 against the tempered fifth is +2 cents. So
    where both A and A+7 are pitches a surviving candidate uses on this
    string, a folded reading cannot be placed at all, and the tuner's own
    warning (E2 over three against A2 over four) is this same arithmetic one
    octave down.

    Over the full sixteen this bites on **strings 6, 5 and 4** -- (33, 40),
    (40, 47) and (45, 52) -- and is impossible on 3, 2 and 1, whose pitches
    span under a fifth.
    """
    have = set(pitches)
    for low in sorted(have):
        if low + 7 in have:
            return (low, low + 7)
    return None


def next_string(candidates: list[tuple[str, dict[int, int]]],
                asked: set[int]) -> int | None:
    """The string to ask for next, or None when there is nothing to ask.

    A string the surviving candidates all AGREE about cannot narrow
    anything, so asking for it spends a question on an answer already in
    hand. Of the rest, one whose targets carry no `fifth_pair` is asked
    first, and only then `ASK_ORDER` -- the low string first, which is
    where the information is and the order a guitarist tunes in.

    **Avoiding the fifth-pair costs nothing, which is why it is done.**
    Measured over all sixteen: low-string-first alone needs 2.25 questions
    on average and 4 at worst, and asks **16 of 16** opening questions
    whose targets a fold cannot separate. Preferring a clean string gives
    **the same 2.25 and the same 4** and brings that to **0** -- the first
    question becomes string 3, whose pitches span under a fifth, and the low
    strings are asked once the candidates have narrowed and their own pair
    is gone.

    None means the query is over, and with two or more candidates left that
    cannot happen: no two named tunings are the same shape, so some string
    they differ on is always left. The test asserts that rather than
    trusting it.
    """
    if len(candidates) < 2:
        return None
    best: tuple[tuple[int, int], int] | None = None
    for index, string in enumerate(ASK_ORDER):
        if string in asked:
            continue
        pitches = pitches_on(string, candidates)
        if len(pitches) < 2:
            continue
        rank = (1 if fifth_pair(pitches) else 0, index)
        if best is None or rank < best[0]:
            best = (rank, string)
    return best[1] if best else None


@dataclass(frozen=True)
class Reading:
    """What one frequency says about the string that was asked for.

    `midi` is the pitch it was placed at, or None when it could not be
    placed; `why` says which, in words the screen can print.
    """

    midi: int | None
    cents: float = 0.0
    multiple: int = 1
    why: str = ""

    @property
    def out_of_tune(self) -> bool:
        return self.midi is not None and abs(self.cents) > OUT_OF_TUNE_CENTS


def interpret(freq: float, targets: list[int]) -> Reading:
    """Place a reading on one of the pitches that string could be.

    A DIRECT reading outranks a fold, because a fold is only ever offered
    for a reading nothing else explains. **The folds themselves do not
    outrank each other**, and that distinction is the whole safety of this:
    read in order of multiple, the low E of a STANDARD guitar comes back as
    A1 -- 82.41/3 is 27.47 Hz and twice that is 54.94, two cents under A1,
    which is Drop A's sixth string. Ranking two against three would have
    answered "Drop A" for the commonest tuning there is, and ranking three
    against two answers "Standard" for Drop A's own octave error. Neither
    is decidable from one reading, so both folds are weighed together and a
    disagreement refuses.

    Two pitches within the window is therefore a refusal rather than a
    round -- whether from a fold or from a string sitting a quarter-tone
    out, where the answer really is unknown. Placing it anyway would read
    every later string against the wrong targets.
    """
    if freq <= 0 or not targets:
        return Reading(None, why="nothing to compare it with")

    def fits(multiples: tuple[int, ...]) -> list[tuple[float, int, float, int]]:
        found = []
        for mult in multiples:
            for midi in targets:
                target = midi_to_freq(midi)
                if target <= 0:
                    continue
                cents = 1200.0 * math.log2(freq * mult / target)
                if abs(cents) <= MATCH_CENTS:
                    found.append((abs(cents), midi, cents, mult))
        return sorted(found)

    for group in ((1,), tuple(m for m in FOLD_MULTIPLES if m != 1)):
        found = fits(group)
        if not found:
            continue
        pitches = {midi for _, midi, _, _ in found}
        if len(pitches) > 1:
            names = " or ".join(
                midi_to_name(m) for m in sorted(pitches)[:2])
            return Reading(None, why=f"could be {names}")
        _, midi, cents, mult = found[0]
        return Reading(midi, cents, mult)
    nearest, _ = freq_to_cents_deviation(freq)
    heard = f" ({midi_to_name(nearest)})" if nearest >= 0 else ""
    return Reading(None, why=f"{freq:.0f} Hz{heard} is not one of them")


@dataclass
class Detection:
    """The narrowing, one string at a time.

    `offer` is fed every confident reading while a string is being asked
    for; it answers what became of that reading. The state it keeps is the
    strings already heard and the run of agreeing readings on the string
    being asked about now.
    """

    tunings: list[tuple[str, dict[int, int]]] | None = None
    heard: dict[int, int] = field(default_factory=dict)
    cents: dict[int, float] = field(default_factory=dict)
    asked: set[int] = field(default_factory=set)
    _run: tuple[int, int, float] | None = None

    # -- what is still possible -----------------------------------------

    @property
    def candidates(self) -> list[tuple[str, dict[int, int]]]:
        return candidates_for(self.heard, self.tunings)

    @property
    def asking(self) -> int | None:
        """The string the player is being asked to play, or None when done."""
        found = next_string(self.candidates, self.asked)
        if found is not None:
            return found
        if len(self.candidates) == 1:
            return None
        # No candidate survives, so there is nothing left to narrow -- but
        # the notes themselves are still worth having, and they are the
        # whole answer for a tuning nobody named. So the query carries on
        # through the strings that have not been ASKED, which is not the
        # same as the ones not heard: a string skipped because it would not
        # read must not come round again.
        for string in ASK_ORDER:
            if string not in self.asked:
                return string
        return None

    @property
    def finished(self) -> bool:
        """True once asking for more cannot change the answer."""
        return self.asking is None

    @property
    def stalled(self) -> bool:
        """Finished with no answer: it ran out of strings to ask.

        Only reachable by SKIPPING a string that would have decided it. The
        screen has to say so rather than print a name, and `still_possible`
        is what it says instead -- two tunings is a far more useful answer
        than whichever of them a guess would have landed on.
        """
        return self.finished and self.answer() is None

    @property
    def targets(self) -> list[int]:
        """The pitches a surviving candidate uses on the string being asked
        about. Semitones apart, which is what `interpret`'s window is sized
        against."""
        string = self.asking
        if string is None:
            return []
        return pitches_on(string, self.candidates)

    @property
    def wider_targets(self) -> list[int]:
        """Every pitch ANY named tuning uses on that string.

        A second tier, tried only when the first places nothing. Two things
        need it, and without it neither works at all:

        - a guitar whose strings are not all from one named tuning. The
          reading is then outside every surviving candidate, and placing it
          against the wider set is what empties the candidate list -- which
          is the honest answer, and the one the six notes are then reported
          as.
        - the strings AFTER that, which have no candidates left to supply
          targets and must still be named.

        It is a tier rather than the rule because it is wider than it is
        safe to be by default: the full set carries the fifth-pair on
        strings 6, 5 and 4, so a folded reading against it can be refused
        where the narrowed set would have placed it.
        """
        string = self.asking
        if string is None:
            return []
        pool = NAMED_TUNINGS if self.tunings is None else self.tunings
        return pitches_on(string, pool)

    # -- feeding it -----------------------------------------------------

    def offer(self, freq: float) -> Reading:
        """Hand it one confident reading of the string being asked for."""
        string = self.asking
        if string is None:
            return Reading(None, why="nothing is being asked for")
        found = interpret(freq, self.targets)
        if found.midi is None:
            wider = self.wider_targets
            if wider != self.targets:
                found = interpret(freq, wider)
        if found.midi is None:
            self._run = None
            return found
        previous = self._run
        if previous is not None and previous[0] == string and previous[1] == found.midi:
            count = previous[2] + 1
        else:
            count = 1
        self._run = (string, found.midi, count)
        if count >= CONFIRM_READINGS:
            self.heard[string] = found.midi
            self.cents[string] = found.cents
            self.asked.add(string)
            self._run = None
        return found

    def skip(self) -> None:
        """Give up on the string being asked for and move on.

        A string that will not read -- a dead one, or one so far out that
        nothing places it -- must not be able to stop the query. What is
        lost is only the narrowing that string would have done, and the
        answer says how many tunings are still standing.
        """
        string = self.asking
        if string is not None:
            self.asked.add(string)

    def reset(self) -> None:
        self.heard.clear()
        self.cents.clear()
        self.asked.clear()
        self._run = None

    # -- the answer -----------------------------------------------------

    @property
    def confirmed(self) -> int:
        """How many readings into the current string's run."""
        return self._run[2] if self._run is not None else 0

    def notes_heard(self) -> str:
        """The open strings as they were heard, low string first."""
        return " ".join(
            midi_to_name(self.heard[s]).rstrip("0123456789")
            for s in ASK_ORDER if s in self.heard)

    def evidence(self) -> str:
        """Which string gave which note -- "6: E, 3: G".

        The letters alone are the answer only when all six were heard. The
        query usually asks two, so "heard E G" would be two notes with
        nothing to say which strings they came off, and a player checking
        the answer could not check it.
        """
        return ", ".join(
            f"{s}: {midi_to_name(self.heard[s]).rstrip('0123456789')}"
            for s in ASK_ORDER if s in self.heard)

    def answer(self) -> tuple[str, dict[int, int]] | None:
        """(name, shape) once it is settled, or None while it is not.

        A tuning nobody named is answered as its own letters, which is what
        `reachable_tunings` already does for the same reason: the letters
        are the fact and a name is a label.
        """
        if not self.finished:
            return None
        found = self.candidates
        if len(found) == 1:
            return found[0]
        if found or len(self.heard) < len(ASK_ORDER):
            # Either two tunings are still standing or a string was skipped
            # that would have decided between them. Naming one of them
            # would be a guess, and a partial shape is not a tuning.
            return None
        shape = dict(self.heard)
        return (" ".join(tuning_notes(shape)), shape)

    def still_possible(self, most: int = 4) -> str:
        """The names still standing, for the screen to print."""
        names = [name for name, _ in self.candidates]
        if not names:
            return "no named tuning fits what it heard"
        if len(names) <= most:
            return " · ".join(names)
        return f"{' · '.join(names[:most])} and {len(names) - most} more"
