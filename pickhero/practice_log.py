"""What was practised, for how long, and how many notes were struck.

`progress.py` keeps the BEST a song has ever been played: one record per song,
overwritten as it improves. That answers "am I getting better at this piece"
and cannot answer "how much did I play this month", because it forgets
everything except the peak.

So this is the other half, and it is deliberately the dumbest thing that
works: one line of JSON per session, appended, never rewritten. Nothing here
aggregates or interprets -- `tools/practice_report.py` does the day, month and
year totals, and anything else can read the file with three lines of code,
which is the point of a format where every line stands on its own.

Two decisions worth knowing, because they decide what the numbers MEAN:

- **Time is real seconds with the song running.** Not song time, which at
  70 % practice speed is shorter than the time you actually spent, and not
  wall-clock time, which counts the coffee you had with the app paused.
- **A struck note is a strike the microphone heard**, not a note the tab
  wrote and not a note that scored. It is what you played, including the
  wrong ones, and it is zero when audio is off -- with the time still counted,
  because you were still playing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from pickhero.config import CONFIG_DIR

PRACTICE_FILE = CONFIG_DIR / "practice_log.jsonl"

# Sessions shorter than this are not written. Opening a song to look at it is
# not practice, and a row for every accidental keypress makes the file harder
# to read for no gain.
MIN_SESSION_SECONDS = 5.0


@dataclass
class Session:
    """One sitting with one song."""

    started: str                  # local time, ISO 8601
    song: str
    seconds: float                # real seconds with the song running
    strikes: int                  # notes the microphone heard you play
    tempo_percent: int
    # Only set when the song was played to the end WITH scoring on. A session
    # spent looping four bars has no accuracy and should not pretend to.
    notes_hit: int | None = None
    notes_written: int | None = None
    accuracy: float | None = None

    @property
    def day(self) -> str:
        return self.started[:10]          # YYYY-MM-DD

    @property
    def month(self) -> str:
        return self.started[:7]           # YYYY-MM

    @property
    def year(self) -> str:
        return self.started[:4]


def append(session: Session, path: Path | None = None) -> bool:
    """Write one session. False if it was too short to be worth a line."""
    if session.seconds < MIN_SESSION_SECONDS:
        return False
    target = Path(path) if path else PRACTICE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    # Appended, never rewritten: a crash mid-write can cost the last line and
    # nothing else, where a rewritten file can cost the year.
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(session), ensure_ascii=False) + "\n")
    return True


def read(path: Path | None = None) -> list[Session]:
    """Every session ever written. A damaged line is skipped, not fatal."""
    target = Path(path) if path else PRACTICE_FILE
    if not target.exists():
        return []
    out = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Session(**json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue                      # one bad line is not a lost year
    return out


@dataclass
class Total:
    """What a period adds up to."""

    key: str
    seconds: float = 0.0
    strikes: int = 0
    sessions: int = 0
    songs: set = field(default_factory=set)

    @property
    def minutes(self) -> float:
        return self.seconds / 60.0


def totals(sessions: list[Session], period: str = "day") -> list[Total]:
    """Add the sessions up by day, month, year or song, oldest first."""
    pick = {"day": lambda s: s.day, "month": lambda s: s.month,
            "year": lambda s: s.year, "song": lambda s: s.song}[period]
    out: dict[str, Total] = {}
    for session in sessions:
        key = pick(session)
        total = out.setdefault(key, Total(key))
        total.seconds += session.seconds
        total.strikes += session.strikes
        total.sessions += 1
        total.songs.add(session.song)
    return [out[key] for key in sorted(out)]


def now_iso() -> str:
    """Local time, because a practice diary is read in the time you live in."""
    return datetime.now().isoformat(timespec="seconds")
