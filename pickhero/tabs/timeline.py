"""Song timeline data structures.

NoteEvent represents a single note in a tab. Timeline holds a sorted sequence
of NoteEvents and provides efficient range queries for the game loop.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field


@dataclass(frozen=True)
class NoteEvent:
    """A single note event in the song timeline."""

    timestamp_ms: float
    duration_ms: float
    midi_note: int
    string: int  # 1-6 (1=high E)
    fret: int    # 0=open
    measure: int = 0  # measure index (0-based)

    # Bend curve as ((position, semitones), ...) with position 0..1 across the
    # note's own duration. A tuple rather than a list so NoteEvent stays
    # frozen and hashable -- the matcher and feedback both key on notes.
    bend: tuple[tuple[float, float], ...] = ()
    # Slid into the FOLLOWING note on this string (shift or legato slide).
    # The target is that note, so nothing needs storing here.
    slide_to_next: bool = False
    # Hammered onto / pulled off to the following note, which is therefore
    # not picked again. Which of the two it is follows from the frets.
    hammer_to_next: bool = False
    # Slid into this note from below (+1) / above (-1), with no start note.
    slide_in: int = 0
    # Slid off this note upwards (+1) / downwards (-1), with no target note.
    slide_out: int = 0
    # Written as a dead note (X in the tab): the fretting hand only damps the
    # string, so the strike is a click and `fret` says where the hand sits
    # rather than which pitch will sound. Nothing about the audio can confirm
    # a pitch here, which is why the matcher accepts any strike for one.
    dead: bool = False
    # Palm-muted. Still the written pitch -- the picking hand shortens and
    # chokes it, it does not change it -- so scoring is unaffected; the flag
    # exists so the display can show what the tab asked for.
    palm_mute: bool = False

    @property
    def bend_semitones(self) -> float:
        """How far the pitch rises at the top of the bend, 0 if unbent."""
        return max((v for _, v in self.bend), default=0.0)

    @property
    def leads_into_next(self) -> bool:
        """True when the following note on this string is not picked again."""
        return self.slide_to_next or self.hammer_to_next

    def __post_init__(self):
        if self.timestamp_ms < 0:
            raise ValueError(f"timestamp_ms must be >= 0, got {self.timestamp_ms}")
        if self.duration_ms < 0:
            raise ValueError(f"duration_ms must be >= 0, got {self.duration_ms}")
        if not 0 <= self.midi_note <= 127:
            raise ValueError(f"midi_note must be 0-127, got {self.midi_note}")
        if not 1 <= self.string <= 6:
            raise ValueError(f"string must be 1-6, got {self.string}")
        if self.fret < 0:
            raise ValueError(f"fret must be >= 0, got {self.fret}")
        if any(not 0.0 <= pos <= 1.0 for pos, _ in self.bend):
            raise ValueError(f"bend positions must be 0..1, got {self.bend}")

    @property
    def end_ms(self) -> float:
        return self.timestamp_ms + self.duration_ms


@dataclass(frozen=True)
class MeasureInfo:
    """Time range for a single measure/bar."""
    index: int
    start_ms: float
    end_ms: float


@dataclass
class SongMetadata:
    """Metadata extracted from a GP file."""

    title: str = ""
    artist: str = ""
    album: str = ""
    track_name: str = ""
    tempo: int = 120
    tuning: dict[int, int] = field(default_factory=dict)
    track_index: int = 0


class Timeline:
    """Sorted collection of NoteEvents with efficient range queries."""

    def __init__(self, notes: list[NoteEvent], metadata: SongMetadata | None = None,
                 measures: list[MeasureInfo] | None = None):
        self._notes = sorted(notes, key=lambda n: (n.timestamp_ms, n.string))
        self._timestamps = [n.timestamp_ms for n in self._notes]
        self.metadata = metadata or SongMetadata()
        self._measures = measures or []
        self._cursor = 0

    def __len__(self) -> int:
        return len(self._notes)

    def __repr__(self) -> str:
        title = self.metadata.title or "Untitled"
        return f"Timeline('{title}', {len(self)} notes, {self.duration_ms:.0f}ms)"

    @property
    def notes(self) -> list[NoteEvent]:
        return list(self._notes)

    @property
    def measures(self) -> list[MeasureInfo]:
        return list(self._measures)

    @property
    def duration_ms(self) -> float:
        if not self._notes:
            return 0.0
        return max(n.end_ms for n in self._notes)

    def get_notes_in_range(self, start_ms: float, end_ms: float) -> list[NoteEvent]:
        """Return notes whose timestamp_ms falls within [start_ms, end_ms)."""
        left = bisect.bisect_left(self._timestamps, start_ms)
        right = bisect.bisect_left(self._timestamps, end_ms)
        return self._notes[left:right]

    def get_active_notes_at_time(self, time_ms: float, window_ms: float = 100.0) -> list[NoteEvent]:
        """Return notes that overlap the window [time_ms - window_ms, time_ms + window_ms].

        A note is active if its sounding range [timestamp_ms, end_ms] overlaps the window.
        """
        window_start = time_ms - window_ms
        window_end = time_ms + window_ms

        # Find candidates: notes that start before window_end
        right = bisect.bisect_right(self._timestamps, window_end)

        result = []
        for i in range(right):
            note = self._notes[i]
            if note.end_ms >= window_start and note.timestamp_ms <= window_end:
                result.append(note)
        return result

    def seek(self, time_ms: float) -> None:
        """Move the cursor to the first note at or after time_ms."""
        self._cursor = bisect.bisect_left(self._timestamps, time_ms)

    def get_next_notes(self, count: int = 1) -> list[NoteEvent]:
        """Return up to `count` notes from the cursor, advancing it."""
        end = min(self._cursor + count, len(self._notes))
        result = self._notes[self._cursor:end]
        self._cursor = end
        return result
