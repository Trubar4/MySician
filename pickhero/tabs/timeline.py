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
    # The note value the tab WROTE, in quarter notes: 1.0 for a quarter, 0.5
    # for an eighth, 0.75 for a dotted eighth. Kept beside duration_ms rather
    # than derived from it, because milliseconds cannot be read back into a
    # note value: a tempo change or a triplet makes the arithmetic ambiguous,
    # and an engraver needs the written value to draw a stem at all. 0.0 means
    # the reader did not supply one.
    duration_quarters: float = 0.0
    # "let ring": the string is not damped, so the note sounds on until
    # something else is played on it. It does NOT change the written value --
    # a let-ring eighth is still an eighth, which is why the tie fix left
    # these looking short. It changes how long the note is DRAWN, and nothing
    # about how it is scored: the pick is at the written moment either way.
    let_ring: bool = False

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
    # What the bar is written IN. Kept because an engraver cannot draw a bar
    # without it, and because it cannot be recovered from the milliseconds:
    # 3/4 at 120 BPM and 6/8 at 120 BPM are the same length of time and a
    # different piece of music.
    beats: int = 4
    beat_type: int = 4


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
        # Both computed once, because the notes never change after this and
        # both were being recomputed over every note in the song, several
        # times a frame. On a dense song that was the single biggest cost in
        # the whole loop.
        self._duration_ms = max((n.end_ms for n in self._notes), default=0.0)
        # How far back a note can START and still be sounding now. Notes are
        # sorted by their start, so this is what turns "which notes are
        # sounding" from a scan of the whole song into a slice of it.
        self._longest_ms = max((n.duration_ms for n in self._notes), default=0.0)

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
        return self._duration_ms

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

        # Candidates start before the window ends AND late enough to still be
        # sounding in it. The second bound is the one that matters: this used
        # to scan from the first note of the song every time, so the cost grew
        # the further in the player got -- and the matcher asks it up to five
        # times per strike. Three minutes into a dense song that is tens of
        # thousands of comparisons per note played, arriving in bursts exactly
        # when the hands are busiest.
        right = bisect.bisect_right(self._timestamps, window_end)
        left = bisect.bisect_left(self._timestamps,
                                  window_start - self._longest_ms)

        result = []
        for i in range(left, right):
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
