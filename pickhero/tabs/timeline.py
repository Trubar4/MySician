"""Song timeline data structures.

NoteEvent represents a single note in a tab. Timeline holds a sorted sequence
of NoteEvents and provides efficient range queries for the game loop.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field, replace
from typing import Sequence


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
    # How many notes the reader could not place, and why. A tab is somebody
    # else's file and a reader cannot be complete, so this is not an error
    # state -- it is the one number that tells a song with a quirk in it
    # apart from a song that was read whole. Carried in the metadata rather
    # than on the Timeline because `transposed`, `merge_by_bar` and
    # `simplified` all copy the metadata forward with `replace` and would
    # each have had to be taught to carry a field of their own.
    unreadable_notes: int = 0
    # Distinct reasons, in the order they first came up. A tuple so this
    # stays as copyable as the rest of the record.
    unreadable_reasons: tuple[str, ...] = ()

    def unreadable_line(self) -> str:
        """One sentence about the notes the reader could not place.

        Empty when the song was read whole, so a screen that always had
        something to say about this -- which is almost every song -- would
        be saying nothing. The COUNT is the size of it and the REASON is
        what it is: "3 notes could not be read" sends the next report back
        here with no more information than the first one had.
        """
        if not self.unreadable_notes and not self.unreadable_reasons:
            return ""
        reasons = "; ".join(self.unreadable_reasons)
        if not self.unreadable_notes:
            # A reason that is not about a NOTE used to be stored and never
            # said, because this gated on the COUNT: a Songsterr tab
            # carrying a repeat nobody has read, or a bar that does not
            # fill its signature, reported nothing at all unless a note
            # happened to be unreadable as well. That is the feature that
            # cannot be seen working, in the one place whose whole job is
            # saying what the reader could not do.
            return f"This tab is not read exactly as written: {reasons}"
        n = self.unreadable_notes
        what = "1 note" if n == 1 else f"{n} notes"
        if not reasons:
            return f"{what} in this tab could not be read"
        return f"{what} in this tab could not be read: {reasons}"


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
        # As long as the WRITTEN PIECE, not as long as the notes. A tab
        # whose final bars are empty -- an outro the guitar sits out -- ends
        # here the moment the last note stops, and everything downstream
        # then agrees that the song is over while the recording plays on.
        # Measured on the player's own files: "What's Up" writes 80 bars of
        # 3.69 s, so the piece runs 295.4 s, and its last note is at 243.5.
        # Fifty-two seconds of music with the picture already finished, and
        # from the inside that is indistinguishable from a sync fault.
        # (Kid Rock loses 1.6 s to the same thing; the other two lose none.)
        #
        # The larger of the two rather than the bars alone, because a
        # let-ring note may sound past the last bar line and a song is not
        # over while something is still ringing.
        self._duration_ms = max(
            max((n.end_ms for n in self._notes), default=0.0),
            self._measures[-1].end_ms if self._measures else 0.0,
        )
        # How far back a note can START and still be sounding now. Notes are
        # sorted by their start, so this is what turns "which notes are
        # sounding" from a scan of the whole song into a slice of it.
        self._longest_ms = max((n.duration_ms for n in self._notes), default=0.0)

    def transposed(self, semitones: int) -> "Timeline":
        """The same song, played on a guitar tuned `semitones` away.

        The FRET NUMBERS DO NOT MOVE. Drop C and Drop D differ by a uniform
        two semitones, so the same shapes on a Drop D guitar are the same
        music a tone higher -- the picture is identical and only the pitch
        the app expects to HEAR changes. That is why this is a shift of the
        notes and the tuning and nothing else; anything that had to move a
        fret would be a different operation (see tools/retune.py).
        """
        if not semitones:
            return self
        notes = [replace(n, midi_note=n.midi_note + semitones)
                 for n in self._notes]
        meta = replace(
            self.metadata,
            tuning={s: v + semitones for s, v in self.metadata.tuning.items()})
        return Timeline(notes, meta, list(self._measures))

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

# Reading a tab is the one job two readers in this package share -- the GP
# loader and the Songsterr one -- so the two rules about an incomplete read
# live here, beside the dataclass they are about, rather than in whichever
# reader needed them first. Two copies would be two answers to what a tab
# is, which is the fault this project has paid for at the repeats, at the
# transpose and at the shifted keys.

def note_or_skip(skipped: list[str], **fields) -> NoteEvent | None:
    """A NoteEvent, or None with the reason written down.

    A tab is somebody else's file and a reader cannot be complete. GPIF
    writes a fret of -1 for a note whose fret the transcriber left out; a
    seven-string tab puts a note on a string this app does not have; a
    damaged beat can put a pitch outside MIDI's range. NoteEvent refuses all
    of those on purpose -- that invariant is what caught the -1 -- but one
    such note used to raise straight out of the loader and take the whole
    TRACK with it. On the file that prompted this, ten notes of about 3800
    cost four of the six guitars, and what the player saw was a traceback
    rather than a song.

    So a note the reader cannot place is skipped and the reason kept, which
    is the rule `scan_files` already follows for a folder it cannot read: a
    screen saying "3 notes could not be read" is a screen; a traceback is
    not. It covers every invariant NoteEvent has and any added later, rather
    than the one quirk that happened to be reported.
    """
    try:
        return NoteEvent(**fields)
    except ValueError as exc:
        skipped.append(str(exc))
        return None

def extend_tied(events: list[NoteEvent], string: int,
                 start_ms: float, duration_ms: float) -> None:
    """Make the note this one continues last through it.

    A tie is one note written twice, and both formats had only the first
    half of that right: the continuation was skipped (GP3-5) or played again
    (GPIF), and in neither case did the note it belongs to get any longer.
    So a note held across two beats was drawn for one -- or, on a GP6/7/8
    file, drawn twice, which is what the player reported.

    Nothing is appended: a tied note is not struck, and giving it an event of
    its own would ask for a pick the music does not contain.
    """
    for i in range(len(events) - 1, -1, -1):
        event = events[i]
        if event.string == string:
            # NoteEvent is frozen on purpose -- a note that can be edited in
            # place is a note some later pass can quietly move.
            events[i] = replace(event, duration_ms=max(
                event.duration_ms,
                start_ms + duration_ms - event.timestamp_ms))
            return


def distinct_reasons(skipped: Sequence[str],
                      limit: int = 3) -> tuple[str, ...]:
    """The reasons, once each, in the order they first came up.

    One quirk in a file is usually the same quirk a few dozen times, so the
    count is the size of it and the reasons are what it IS. Capped because
    this goes on a screen and in a run log, and a hundred copies of one
    sentence says less than three.
    """
    seen: list[str] = []
    for reason in skipped:
        if reason not in seen:
            seen.append(reason)
        if len(seen) == limit:
            break
    return tuple(seen)
