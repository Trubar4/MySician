"""Every run of a song, in a file beside the song.

*"Ich haette gerne Vergleiche gemacht von mehreren Durchgaengen des selben
Songs."* The app already knew how a run went -- the matcher holds a verdict
per note and the strip draws them -- and threw all of it away the moment the
song was left. `progress.py` kept the BEST a song had ever been played, one
record overwritten as it improves, which cannot answer "is this passage
getting better" because it forgets everything except the peak.

So a run is kept whole: one character per note of the timeline, plus what a
number is only readable next to -- when it was played, at what speed, on
which track, and how far it got.

**Beside the tab, under the tab's own name**, which is the rule this project
follows for the sidecar, the bar map and the recording: *"Durchgaenge liegen
neben gp im gleichen Ordner."* Copying the songs folder carries the history
with it, and two machines editing different songs never touch one file.

Arithmetic and storage only -- no pygame -- so the synthetic runs below are
tested without a screen, and because the list, the comparison and the
miniature must never disagree about what a run says.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SUFFIX = ".runs.json"

#: Bumped only when what a stored run MEANS changes. An unknown version is
#: ignored rather than guessed at -- the same rule as the sidecar.
VERSION = 1

#: A run is a few hundred bytes and this is a practice history, not an
#: archive. Fifty is what `progress.py` already keeps.
MAX_RUNS = 50

# One character per note, in timeline order.
#
#   h c m   hit / close / miss, and something really checked it
#   H C M   the same verdict, with nothing behind it -- a chord string
#           credited to a strum nobody could confirm, or a note whose window
#           held a strike the detector could make nothing of
#   .       never judged: not reached, or filtered out of this run
#
# The case carries `matcher.unreliable`, which is the same distinction the
# three views drain the colour for. It is kept here because "most frequent
# errors" must not count a verdict nothing stood behind -- the player's own
# ruling, and the presumption of innocence one level up.
HIT, CLOSE, MISS, NOTHING = "h", "c", "m", "."
_DRAINED = {HIT: "H", CLOSE: "C", MISS: "M"}
_KINDS = {"hit": HIT, "close": CLOSE, "miss": MISS}

#: Best first. A reliable verdict outranks the same verdict with nothing
#: behind it, so "the best I ever played this note" prefers the one that was
#: actually heard over the one that was credited to a strum.
_RANK = {HIT: 0, "H": 1, CLOSE: 2, "C": 3, MISS: 4, "M": 5, NOTHING: 6}

#: What counts as a Fehler. A CLOSE is the right note played off the beat --
#: the timing percentage answers for that, and calling it an error too would
#: paint most of a run red. A drained miss is not evidence of anything.
_ERROR = MISS


def path_for(tab_path) -> Path:
    """Where this tab's runs live: beside it, under its own name.

    Given the history file itself it answers itself, so a caller that
    already HAS the file can ask about it without inventing a tab beside it.
    An export folder is exactly that: the histories with no tabs, because
    the tabs are already on the other computer.
    """
    tab = Path(tab_path)
    if tab.name.endswith(SUFFIX):
        return tab
    return tab.with_name(tab.stem + SUFFIX)


def encode(marks) -> str:
    """One character per note, from (kind, checked) pairs.

    `kind` is "hit", "close", "miss" or None; `checked` is False where
    nothing actually verified the verdict. Taking pairs rather than the
    matcher's own enum keeps this module free of the matcher, which is what
    lets the synthetic runs below be tested on strings.
    """
    out = []
    for kind, checked in marks:
        char = _KINDS.get(kind, NOTHING)
        if char is not NOTHING and not checked:
            char = _DRAINED[char]
        out.append(char)
    return "".join(out)


def is_error(char: str) -> bool:
    """Whether this verdict is a mistake the player can act on."""
    return char == _ERROR


@dataclass
class Run:
    """One pass through a song, as it was judged."""

    notes: str = ""
    started: str = ""
    seconds: float = 0.0
    tempo_percent: int = 100
    track: int = 0
    #: What the note string describes: the track and how many notes were in
    #: it. A run recorded against a different track, or against a tab that
    #: has since been edited, is still LISTED -- it happened -- but it
    #: cannot be drawn against this song's notes, and pretending otherwise
    #: would put every dot in the wrong place.
    note_count: int = 0
    #: "run", "best" or "errors". The two synthetic ones are computed on
    #: demand and never written; the field is what lets one drawing serve
    #: all three.
    kind: str = "run"
    label: str = ""

    def counts(self) -> dict:
        """hits / close / misses / total, in the shape the strip already reads.

        Counted out of the note string rather than stored beside it, so the
        number in the list and the dots in the bar cannot disagree -- and so
        a synthetic run is counted by the same arithmetic as a real one.
        """
        hits = self.notes.count(HIT) + self.notes.count("H")
        close = self.notes.count(CLOSE) + self.notes.count("C")
        misses = self.notes.count(MISS) + self.notes.count("M")
        total = hits + close + misses
        return {"hits": hits, "close": close, "misses": misses,
                "total": total,
                "accuracy_percent": hits / total * 100 if total else 0.0}

    def when(self) -> datetime | None:
        """When it was played, or None for a synthetic run."""
        try:
            return datetime.fromisoformat(self.started)
        except ValueError:
            return None

    def fits(self, note_count: int, track: int) -> bool:
        """Whether this run's verdicts describe the song now on screen."""
        return self.note_count == note_count and self.track == track

    def as_json(self) -> dict:
        return {"notes": self.notes, "started": self.started,
                "seconds": round(self.seconds, 1),
                "tempo_percent": self.tempo_percent, "track": self.track,
                "note_count": self.note_count}


def now_iso() -> str:
    """The moment a run begins, in the one format this file sorts by.

    UTC and nothing else: `common_errors` reads the LAST run off a string
    sort, and a file holding one local timestamp beside one UTC timestamp
    sorts by whichever digits happen to be larger.
    """
    return datetime.now(timezone.utc).isoformat()


def make(notes: str, seconds: float, tempo_percent: int, track: int,
         started: str = "") -> Run:
    """A run of the song that has just been played.

    `started` is when the run BEGAN. Left out it is now, which is right only
    for a caller with nothing better -- the app passes the real one, because
    a run written when the song is left would otherwise be stamped minutes
    after the playing it describes.
    """
    return Run(notes=notes,
               started=started or now_iso(),
               seconds=seconds, tempo_percent=tempo_percent, track=track,
               note_count=len(notes))


def worth_keeping(run: Run) -> bool:
    """Whether this run says anything.

    A song opened and left without a note being judged is not a run, and a
    list filling up with empty ones would bury the ones that are not.
    """
    return run.counts()["total"] > 0


def load(tab_path) -> list[Run]:
    """Every stored run, newest LAST. Never raises."""
    try:
        raw = json.loads(path_for(tab_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        return []
    out: list[Run] = []
    for item in raw.get("runs", []):
        if not isinstance(item, dict) or not isinstance(item.get("notes"), str):
            continue
        try:
            out.append(Run(notes=item["notes"],
                           started=str(item.get("started", "")),
                           seconds=float(item.get("seconds", 0.0)),
                           tempo_percent=int(item.get("tempo_percent", 100)),
                           track=int(item.get("track", 0)),
                           note_count=int(item.get("note_count",
                                                   len(item["notes"])))))
        except (TypeError, ValueError):
            continue
    return out


def save(tab_path, kept: list[Run]) -> bool:
    """Write the history beside the tab, newest last. False if it could not be.

    Never raises: a history that cannot be written is a slower day, not a
    broken song -- the sidecar's rule, and for the same reason.
    """
    if not tab_path:
        return False
    try:
        path_for(tab_path).write_text(
            json.dumps({"version": VERSION,
                        "runs": [r.as_json() for r in kept[-MAX_RUNS:]]},
                       indent=1),
            encoding="utf-8")
    except OSError:
        return False
    return True


def append(tab_path, run: Run) -> bool:
    """Add a run to the file beside the tab. False if it could not be."""
    if not tab_path or not worth_keeping(run):
        return False
    return save(tab_path, load(tab_path) + [run])


def merge(mine: list[Run], theirs: list[Run]) -> tuple[list[Run], int]:
    """Both machines' evenings, in time order. Returns the list and how many
    came from `theirs` that this machine did not have.

    *"Import bzw. Zusammenfuehren waere sehr nett als Funktion."* Until now an
    import was per FILE and a `.runs.json` already here kept its own, so the
    other laptop's evenings were simply not taken. A history is a LIST, and
    two lists of different evenings have an obvious union -- which is not true
    of the settings beside them, where "what this machine has wins" is the
    only safe rule.

    - **Keyed by when it started AND what it says.** Two runs cannot share a
      microsecond, so the timestamp alone would do; carrying the verdicts as
      well makes the key stricter, and stricter fails the safe way. A doubled
      run would flatter the history; a dropped one is an evening gone, and
      this whole feature exists to stop that.
    - **Sorted by TIME, not by which file they came from.** `common_errors`
      asks what the LAST run did, and after a merge the last run has to be the
      most recent evening rather than whichever file was read second.
    - **Idempotent**, because nobody remembers whether they already imported:
      running it twice adds nothing the second time.
    """
    seen = {(r.started, r.notes) for r in mine}
    added = [r for r in theirs if (r.started, r.notes) not in seen]
    both = sorted(mine + added, key=lambda r: r.started)
    return both, len(added)


#: How many clean bars may sit INSIDE one nest. A four-bar phrase with one
#: good bar in the middle is one passage to practise; two clean bars is a
#: place you can stop, so the errors either side are two problems.
#:
#: **Not fitted against real data, and it cannot be yet** -- the run history
#: starts the day it ships, so there is nothing to measure a gap distribution
#: on. It is reasoned from what a practice loop IS, and it is one constant to
#: re-fit once a real history exists, which is the honest state to leave it in.
NEST_BRIDGE_BARS = 1


def error_nests(notes: str, bars, bridge: int = NEST_BRIDGE_BARS
                ) -> list[tuple[int, int, int]]:
    """Where a run went wrong, grouped into passages worth looping.

    Takes the verdict string and the bar number of every note -- integers and
    nothing else, so this is tested without a timeline and without a screen.
    Returns `(first_bar, last_bar, how many notes were wrong)`, in bar order.

    **Grouped by the tab's own BARS rather than by a number of milliseconds.**
    A bar is what a player counts in and what a loop is set in, and it is
    read off the file rather than chosen here -- the same reason the board's
    bar lines are drawn on real bar boundaries instead of at a pixel spacing.
    """
    wrong: dict[int, int] = {}
    for i, char in enumerate(notes):
        if i >= len(bars) or not is_error(char):
            continue
        wrong[bars[i]] = wrong.get(bars[i], 0) + 1
    if not wrong:
        return []
    out: list[tuple[int, int, int]] = []
    first = last = None
    count = 0
    for bar in sorted(wrong):
        if first is None:
            first = last = bar
            count = wrong[bar]
        elif bar - last <= bridge + 1:
            last = bar
            count += wrong[bar]
        else:
            out.append((first, last, count))
            first = last = bar
            count = wrong[bar]
    out.append((first, last, count))
    return out


def merge_files(mine_path, theirs_path) -> int:
    """Pull the other machine's runs into this tab's file. Never raises.

    Returns how many evenings were added, 0 for none and for anything that
    could not be read or written -- the caller reports a count, and a history
    that will not merge must not take an import down with it.
    """
    theirs = load(theirs_path)
    if not theirs:
        return 0
    mine = load(mine_path)
    both, added = merge(mine, theirs)
    if not added:
        return 0
    return added if save(mine_path, both) else 0


def best_ever(runs: list[Run]) -> Run | None:
    """The best each note has ever been played, gathered into one run.

    *"Was waer wenn ich aus jedem Durchgang den besten Wert nehme und daraus
    einen Durchgang interpoliere."* Not a run that happened -- it says so in
    its label -- but a real answer to "is this song within reach at all": a
    passage that is red here was never once played, and a passage that is
    green here has been, on some evening, in one piece.
    """
    runs = [r for r in runs if r.notes]
    if len(runs) < 2:
        return None
    width = max(len(r.notes) for r in runs)
    best = [NOTHING] * width
    for run in runs:
        for i, char in enumerate(run.notes):
            if _RANK.get(char, 6) < _RANK.get(best[i], 6):
                best[i] = char
    return Run(notes="".join(best), kind="best", note_count=width,
               label=f"the best each note has been, over {len(runs)} runs")


def common_errors(runs: list[Run]) -> Run | None:
    """Where the same mistake keeps happening. Empty if there is no such place.

    *"Wo ich in mehr als der Haelfte aller Durchgaenge Fehler gemacht habe.
    Fehler die beim letzten Durchgang nicht mehr gemacht wurden, sollen auch
    nicht angezeigt werden."*

    Three rules, and each one is there to stop this claiming something it
    cannot show:

    - **Only runs that reached the note vote on it.** A note nobody got to
      is not evidence that it goes well or badly, and counting it in the
      denominator would hide a passage at the end of a song that is wrong
      every single time it is reached.
    - **Only verdicts something stood behind.** A drained one is the app
      saying it could not tell -- *"ja ausschliessen"* -- so `H`, `C` and
      `M` abstain rather than vote either way. This is the presumption of
      innocence the chord verifier runs on, one level up.
    - **Fixed is fixed.** A note the LAST run played right is dropped,
      however many evenings it went wrong before. That is the player's own
      rule and it is what keeps the picture about today.
    """
    runs = [r for r in runs if r.notes]
    if len(runs) < 2:
        return None
    width = max(len(r.notes) for r in runs)
    out = [NOTHING] * width
    found = 0
    for i, verdict in enumerate(_verdicts(runs, width)):
        if verdict is _ERRORS_HERE:
            out[i] = MISS
            found += 1
    if not found:
        return None
    return Run(notes="".join(out), kind="errors", note_count=width,
               label=f"wrong in over half of {len(runs)} runs, and still wrong last time")


#: What `_verdicts` answers per note. Not strings, so a typo in a caller is
#: an error rather than a silently false comparison.
_ERRORS_HERE = object()         # wrong in over half of them, still wrong
_NOT_ENOUGH = object()          # fewer than two runs could judge it at all
_FIXED = object()               # it went wrong, and the last run got it right
_FINE = object()                # judged, and not wrong often enough


def _verdicts(runs: list[Run], width: int):
    """The rule behind "frequent errors", once, for every note.

    Both the row and the sentence that explains its absence read this, so
    the picture and the reason for the picture cannot disagree -- which is
    the fault this project has paid for at the repeats, at the transpose and
    at the shifted keys.
    """
    last = runs[-1].notes
    for i in range(width):
        votes = 0
        wrong = 0
        for run in runs:
            char = run.notes[i] if i < len(run.notes) else NOTHING
            if char not in (HIT, CLOSE, MISS):
                continue            # never reached, or nothing checked it
            votes += 1
            if is_error(char):
                wrong += 1
        if votes < 2:
            yield _NOT_ENOUGH
        elif wrong * 2 <= votes:
            yield _FINE
        elif i < len(last) and last[i] in (HIT, CLOSE):
            yield _FIXED            # not any more, it isn't
        else:
            yield _ERRORS_HERE


def why_no_errors(runs: list[Run]) -> str:
    """Why there is no "frequent errors" row, in one sentence.

    A row that is simply absent cannot be told from a feature that does not
    work -- this project's oldest lesson, and the player asked the question
    outright: *"Wie viele Laeufe brauche ich um haeufige Fehler zu sehen?"*
    The answer is TWO, and the reason it often takes more is the third rule:
    a verdict nothing could check does not vote, and on a strummed song most
    of the red is exactly that. So the sentence names the number of notes
    that abstained rather than leaving it to be guessed at.

    Empty where the row IS there, so the caller has one thing to check.
    """
    runs = [r for r in runs if r.notes]
    if len(runs) < 2:
        return ("Frequent errors needs two runs of this track — "
                f"there {'is 1' if len(runs) == 1 else 'are none'} so far")
    width = max(len(r.notes) for r in runs)
    counts = {_ERRORS_HERE: 0, _NOT_ENOUGH: 0, _FIXED: 0, _FINE: 0}
    for verdict in _verdicts(runs, width):
        counts[verdict] += 1
    if counts[_ERRORS_HERE]:
        return ""
    if counts[_FIXED]:
        return (f"No frequent errors — the {counts[_FIXED]} that kept going "
                "wrong all went right last time")
    return ("No note went wrong in over half of these runs. "
            f"{counts[_NOT_ENOUGH]} of {width} could not be judged twice — "
            "a strum nobody could check does not vote either way")
