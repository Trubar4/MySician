"""Note matching engine.

Compares detected audio notes against the tab timeline to produce
hit/close/miss feedback. No pygame dependency — pure logic.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from pickhero.audio.note_utils import semitone_distance
from pickhero.audio.chord_verify import ChordVerifier
from pickhero.audio.input import StrikeWindow, TimestampedNote
from pickhero.tabs.timeline import NoteEvent, Timeline


# How far around a strike to search for its intended tab note when
# measuring input latency. Deliberately much wider than the timing window:
# latency larger than the window would otherwise be unmeasurable, because
# only matched notes would contribute samples (and those are capped at the
# window by definition).
LATENCY_SEARCH_MS = 500.0

# The wide search above is what a riff repeating one pitch trips over: two
# equally good candidates land in it, the measurement refuses (rightly, see
# _record_timing_sample), and repetitive passages contribute almost nothing.
# On the timing test only 39 % of strikes were measured. Once enough samples
# exist to say roughly where the strikes sit, the search narrows to the range
# they actually occupy, which makes those same passages unambiguous.
TIGHTEN_SEARCH_AFTER = 12       # samples before narrowing is trusted
SEARCH_SPREAD_MULTIPLE = 4.0    # how many MADs of headroom to keep
MIN_SEARCH_MS = 120.0           # never narrower than plain human earliness
# How much nearer the best candidate must be than the runner-up before a
# strike counts as belonging to it rather than to its neighbour.
AMBIGUITY_RATIO = 2.0

# How far a slide with no written target is allowed to travel before the
# pitch counts as a different note. Slides off the end of a phrase have no
# destination in the tab, so the only honest bound is a generous one.
OPEN_SLIDE_SEMITONES = 2

# How many strings a chord needs before a strike carrying no pitch at all is
# accepted as having played it. Fitted on the reference recordings rather than
# chosen: strikes that produce no confident pitch run at 16-17 % on one and two
# strings, where the detector nearly always manages, and at 38-55 % from four
# strings up, where a six-note strum gives monophonic YIN no single period to
# lock onto. The jump sits between two strings and four, so three is the line.
# Below it, accepting a pitchless strike would be leniency bought with nothing.
MIN_UNPITCHED_CHORD_STRINGS = 3

# Timing report. Bins are wide enough that a handful of samples still forms a
# visible shape, narrow enough to separate latency from scatter by eye.
TIMING_BIN_MS = 10.0
TIMING_MIN_SAMPLES = 8
# The axis always reaches at least this far either side of the beat, so a
# tight group is seen at its distance from zero rather than filling the frame.
HISTOGRAM_MIN_SPAN_MS = 120.0
HISTOGRAM_MAX_BINS = 34
HISTOGRAM_BIN_LADDER = (10.0, 20.0, 25.0, 50.0, 100.0)
# A string needs this many strikes before its median is worth believing.
STRING_MIN_SAMPLES = 5
# Below this a difference between strings is not worth mentioning at all.
MIN_STRING_GAP_MS = 25.0
# Turning a MAD into the standard error of a difference of two medians; see
# _string_gap. The multiple is how many of those a gap must clear to count.
MEDIAN_SE_FACTOR = 1.85
STRING_GAP_SIGMAS = 2.5
# The same test applied to the overall median before calling it latency.
MEDIAN_SIGMAS = 2.0
# Scatter above this is what a player notices as "the timing is off" even
# when the average is right. Roughly a 32nd note at 120 BPM.
SCATTER_MS = 30.0
# Latency this small is not worth chasing; it is inside what a strum spans.
FINE_MS = 20.0


class MatchType(Enum):
    PENDING = "pending"
    HIT = "hit"
    CLOSE = "close"
    MISS = "miss"


@dataclass
class MatchResult:
    """Result of matching a detected note against the timeline."""
    match_type: MatchType
    matched_events: list[NoteEvent] = field(default_factory=list)
    semitone_distance: int | None = None


@dataclass(frozen=True)
class TimingSample:
    """One strike measured against the tab note it belongs to.

    Carries more than the error itself because two numbers cannot say which
    timing problem you have. A median error of 90 ms with everything tightly
    around it is latency and one offset fixes it; the same 90 ms spread
    evenly is the player, and no offset touches it; the same 90 ms split by
    string is the detector reacting slower to a wound low E than to a plain
    high one, which is neither, and which only shows up when the samples are
    kept apart.
    """
    delta_ms: float     # signed, positive = the strike registered late
    string: int
    midi_note: int
    note_ms: float      # where the note sits in the song


@dataclass
class StrikeTrace:
    """What became of one strike, kept so a bad run can be read afterwards.

    A percentage at the end of a song says how much went wrong and nothing
    about where. The same recording that scored 35 % in the app scored 97 %
    when the same detector and the same matcher were run over it offline, and
    no number on screen could say which of the two dozen things between the
    two was responsible. This is that missing evidence: one line per strike,
    written as it happened.

    Recording only -- nothing here is read back by the matcher.
    """
    strike_ms: float        # timestamp as the audio thread stamped it
    adjusted_ms: float      # after the offset, i.e. where the song thinks it was
    playback_ms: float      # where the song actually was at that moment
    midi_note: int
    confidence: float
    unpitched: bool
    subharmonic: bool
    outcome: str            # hit / close / dead / chord / unmatched / ignored
    note_ms: float | None   # the tab note it was credited to, if any
    semitones: int | None   # how far off that note it was


class NoteMatcher:
    """Matches detected audio notes against tab timeline events.

    Each NoteEvent in the timeline is tracked by state (PENDING -> HIT/CLOSE/MISS).
    Detected notes are compared to PENDING events within the timing window.
    """

    def __init__(
        self,
        timeline: Timeline,
        timing_window_ms: float = 100.0,
        audio_offset_ms: float = 0.0,
        chord_threshold_ms: float = 50.0,
        note_filter: Callable[[NoteEvent], bool] | None = None,
        chord_partial_credit: bool = True,
        late_window_ms: float = 0.0,
        chord_verifier: ChordVerifier | None = None,
    ):
        self._timeline = timeline
        self._timing_window_ms = timing_window_ms
        self._audio_offset_ms = audio_offset_ms
        self._chord_threshold_ms = chord_threshold_ms
        # Strike notes arrive up to ~70 ms after their timestamp (the onset
        # collector waits for the pitch to settle) — delay miss-marking so
        # a late-arriving strike can still claim its note
        self._late_window_ms = late_window_ms
        self.note_filter = note_filter
        self.chord_partial_credit = chord_partial_credit

        # State per note event, keyed by (timestamp_ms, string)
        self._note_states: dict[tuple[float, int], MatchType] = {}

        # Statistics
        self.hits = 0
        self.close = 0
        self.misses = 0

        # Signed timing error of each strike vs the nearest pitch-matching
        # tab note (positive = detected late). Used for latency calibration.
        # Recording is disabled while wait mode pins timestamps, which would
        # otherwise contribute meaningless zero errors.
        self.timing_errors_ms: list[float] = []
        # The same measurements with their context kept, for the report.
        self.timing_samples: list[TimingSample] = []
        # Strikes that could have belonged to two notes at once. A high count
        # next to a low sample count is the report's own health warning.
        self.timing_ambiguous = 0
        self.record_timing_samples = True

        # Per-measure statistics: {measure_idx: {"hits": n, "close": n, "misses": n}}
        self._measure_stats: dict[int, dict[str, int]] = defaultdict(
            lambda: {"hits": 0, "close": 0, "misses": 0}
        )

        # Per-string chord verification. The pitch path above credits a chord
        # immediately so feedback stays responsive; the raw audio needed to
        # tell WHICH string was mis-fretted only arrives ~380 ms later, and
        # then downgrades the strings it can prove wrong. With no verifier the
        # behaviour is exactly the pitch path's.
        self._chord_verifier = chord_verifier
        self._pending_verifications: dict[int, list[NoteEvent]] = {}
        self.chord_verifications = 0
        self.chord_strings_corrected = 0

        # One line per strike, and one per string a chord verdict took back.
        # Written only; see StrikeTrace for why it exists.
        self.strike_trace: list[StrikeTrace] = []

        self._pitch_ranges = self._build_pitch_ranges(timeline)
        self._legato_sources = self._build_legato_sources(timeline)

    @staticmethod
    def _build_legato_sources(
        timeline: Timeline,
    ) -> dict[tuple[float, int], tuple[float, int]]:
        """Notes that are not picked, mapped to the note they follow from.

        A hammer-on, a pull-off and both kinds of slide all continue a note
        already ringing, so no onset ever arrives for the second one.
        """
        by_string: dict[int, list[NoteEvent]] = {}
        for note in timeline.notes:
            by_string.setdefault(note.string, []).append(note)

        out: dict[tuple[float, int], tuple[float, int]] = {}
        for string, group in by_string.items():
            group.sort(key=lambda n: n.timestamp_ms)
            for note, following in zip(group, group[1:]):
                if note.leads_into_next and following.timestamp_ms > note.timestamp_ms:
                    out[(following.timestamp_ms, string)] = (note.timestamp_ms, string)
        return out

    @staticmethod
    def _build_pitch_ranges(timeline: Timeline) -> dict[tuple[float, int], tuple[int, int]]:
        """Pitches each note may legitimately sound, beyond its written one.

        A bend or a slide deliberately leaves the written pitch, so scoring
        against that pitch alone marks correctly played technique as wrong.
        Only notes that carry a technique get an entry; everything else keeps
        the plain single-pitch comparison.

        Tolerant on purpose. Judging how FAR a bend went is a separate
        problem, and being strict about it before it can be measured would
        turn every bend in a tab red.
        """
        by_string: dict[int, list[NoteEvent]] = {}
        for note in timeline.notes:
            by_string.setdefault(note.string, []).append(note)
        for group in by_string.values():
            group.sort(key=lambda n: n.timestamp_ms)

        ranges: dict[tuple[float, int], tuple[int, int]] = {}
        for group in by_string.values():
            for i, note in enumerate(group):
                low = high = note.midi_note
                if note.bend:
                    high += int(math.ceil(note.bend_semitones))
                if note.slide_out > 0 or note.slide_in < 0:
                    high += OPEN_SLIDE_SEMITONES
                if note.slide_out < 0 or note.slide_in > 0:
                    low -= OPEN_SLIDE_SEMITONES
                if note.slide_to_next and i + 1 < len(group):
                    target = group[i + 1].midi_note
                    low, high = min(low, target), max(high, target)
                if (low, high) != (note.midi_note, note.midi_note):
                    ranges[(note.timestamp_ms, note.string)] = (low, high)
        return ranges

    def _pitch_distance(self, detected_midi: int, note: NoteEvent) -> int:
        """Semitones from a detected pitch to the nearest one `note` allows."""
        low, high = self._pitch_ranges.get(
            (note.timestamp_ms, note.string), (note.midi_note, note.midi_note)
        )
        if low <= detected_midi <= high:
            return 0
        return min(abs(detected_midi - low), abs(detected_midi - high))

    @property
    def audio_offset_ms(self) -> float:
        return self._audio_offset_ms

    @audio_offset_ms.setter
    def audio_offset_ms(self, value: float) -> None:
        self._audio_offset_ms = value

    @property
    def timing_window_ms(self) -> float:
        return self._timing_window_ms

    @timing_window_ms.setter
    def timing_window_ms(self, value: float) -> None:
        self._timing_window_ms = value

    @property
    def chord_verifier(self) -> ChordVerifier | None:
        return self._chord_verifier

    @chord_verifier.setter
    def chord_verifier(self, verifier: ChordVerifier | None) -> None:
        self._chord_verifier = verifier
        if verifier is None:
            # Chords recorded while it was on would otherwise wait forever
            self._pending_verifications.clear()

    @property
    def late_window_ms(self) -> float:
        return self._late_window_ms

    @late_window_ms.setter
    def late_window_ms(self, value: float) -> None:
        self._late_window_ms = value

    def _note_key(self, event: NoteEvent) -> tuple[float, int]:
        return (event.timestamp_ms, event.string)

    def _get_state(self, event: NoteEvent) -> MatchType:
        return self._note_states.get(self._note_key(event), MatchType.PENDING)

    def _set_state(self, event: NoteEvent, state: MatchType) -> None:
        self._note_states[self._note_key(event)] = state

    def _is_filtered(self, event: NoteEvent) -> bool:
        """Return True if this note should be excluded by the difficulty filter."""
        if self.note_filter is None:
            return False
        return not self.note_filter(event)

    def get_note_state(self, event: NoteEvent) -> MatchType:
        """Get the current match state of a timeline note."""
        return self._get_state(event)

    def _find_chord_siblings(self, event: NoteEvent) -> list[NoteEvent]:
        """Find notes within chord_threshold_ms of the given event."""
        return [
            n for n in self._timeline.get_active_notes_at_time(
                event.timestamp_ms, self._chord_threshold_ms
            )
            if abs(n.timestamp_ms - event.timestamp_ms) <= self._chord_threshold_ms
        ]

    def _undo_match(self, event: NoteEvent, match_type: MatchType) -> None:
        """Reverse the counters a previous _record_match applied."""
        if match_type == MatchType.HIT:
            self.hits -= 1
            self._measure_stats[event.measure]["hits"] -= 1
        elif match_type == MatchType.CLOSE:
            self.close -= 1
            self._measure_stats[event.measure]["close"] -= 1
        elif match_type == MatchType.MISS:
            self.misses -= 1
            self._measure_stats[event.measure]["misses"] -= 1

    def _rerecord_match(self, event: NoteEvent, match_type: MatchType) -> None:
        """Change an already-recorded verdict, keeping the statistics honest."""
        previous = self._get_state(event)
        if previous == match_type:
            return
        self._undo_match(event, previous)
        self._record_match(event, match_type)

    def _record_match(self, event: NoteEvent, match_type: MatchType) -> None:
        """Record a match for a note, updating stats and measure stats."""
        self._set_state(event, match_type)
        if match_type == MatchType.HIT:
            self.hits += 1
            self._measure_stats[event.measure]["hits"] += 1
        elif match_type == MatchType.CLOSE:
            self.close += 1
            self._measure_stats[event.measure]["close"] += 1
        elif match_type == MatchType.MISS:
            self.misses += 1
            self._measure_stats[event.measure]["misses"] += 1

    def has_pending_notes_at(self, playback_ms: float) -> bool:
        """Return True if there are unmatched notes at or before playback_ms."""
        window_start = playback_ms - self._timing_window_ms
        candidates = self._timeline.get_notes_in_range(window_start, playback_ms + 1)
        for note in candidates:
            if self._is_filtered(note):
                continue
            if self._get_state(note) == MatchType.PENDING:
                return True
        return False

    def _mark_missed_notes(self, playback_ms: float) -> list[MatchResult]:
        """Mark PENDING notes that have passed the timing window as MISS."""
        results = []
        cutoff = playback_ms - self._timing_window_ms - self._late_window_ms
        if cutoff <= 0:
            return results

        # Check notes that should have been played by now
        candidates = self._timeline.get_notes_in_range(0, cutoff)
        for note in candidates:
            if self._is_filtered(note):
                continue
            if self._get_state(note) != MatchType.PENDING:
                continue
            inherited = self._legato_credit(note)
            if inherited is not None:
                self._record_match(note, inherited)
                results.append(MatchResult(
                    match_type=inherited,
                    matched_events=[note],
                    semitone_distance=None,
                ))
                continue
            self._record_match(note, MatchType.MISS)
            results.append(MatchResult(
                match_type=MatchType.MISS,
                matched_events=[note],
                semitone_distance=None,
            ))
        return results

    def _legato_credit(self, note: NoteEvent) -> MatchType | None:
        """The state a hammered, pulled or slid-into note inherits, if any.

        Such a note is never picked -- that is what the technique means -- so
        waiting for a strike on it can only ever end in a miss. It stands or
        falls with the note it came from: if that one was played, this one
        was too, and if it was not, this one is missed on its own account.
        """
        source_key = self._legato_sources.get((note.timestamp_ms, note.string))
        if source_key is None:
            return None
        state = self._note_states.get(source_key)
        return state if state in (MatchType.HIT, MatchType.CLOSE) else None

    def _dead_note_credit(self, adjusted_ms: float) -> MatchResult | None:
        """Credit a written dead note for a strike no pitch accounts for.

        A dead note is the fretting hand damping the string. The tab writes a
        fret to say where the hand sits, but the sound is a click, and there
        is no pitch in it to check -- so the only honest test is that
        something was struck where one was written, which is also the whole
        of what the player was asked to do. Left unhandled, every dead note in
        a tab times out as a miss no matter how well it was played.
        """
        candidates = self._timeline.get_active_notes_at_time(
            adjusted_ms, self._timing_window_ms
        )
        pending = [
            n for n in candidates
            if n.dead and self._get_state(n) == MatchType.PENDING
            and not self._is_filtered(n)
        ]
        if not pending:
            return None
        nearest = min(pending, key=lambda n: abs(n.timestamp_ms - adjusted_ms))
        # A muted strum writes a dead note on several strings at once, and one
        # stroke is all of them -- there is no second click to wait for.
        struck = [
            n for n in pending
            if abs(n.timestamp_ms - nearest.timestamp_ms) <= self._chord_threshold_ms
        ]
        for note in struck:
            self._record_match(note, MatchType.HIT)
        return MatchResult(
            match_type=MatchType.HIT, matched_events=struck,
            semitone_distance=None,
        )

    def _unpitched_chord_credit(
        self, adjusted_ms: float, sample_pos: int | None,
    ) -> MatchResult | None:
        """Credit a written chord for a strum that produced no pitch at all.

        A full chord gives monophonic YIN no single period to lock onto, so a
        correctly played strum routinely arrives carrying no note whatsoever.
        Measured on the reference recordings: 38-55 % of strikes on chords of
        four strings and up produce no pitch, against 16 % on one or two.
        Scored on pitch alone, those strums go red however well they were
        fretted -- which is exactly what the player reports, and it is the
        detector's limitation being charged to them.

        This does not guess at the fretting, and it does not have to. The
        strike still goes to the chord verifier, which reads the raw audio and
        convicts any string it can positively show to be wrong. So the strum
        is credited and the fingers are still checked -- the presumption of
        innocence the verifier already runs on, applied one level up.
        """
        candidates = self._timeline.get_active_notes_at_time(
            adjusted_ms, self._timing_window_ms
        )
        pending = [
            n for n in candidates
            if self._get_state(n) == MatchType.PENDING
            and not self._is_filtered(n) and not n.dead
        ]
        if not pending:
            return None
        nearest = min(pending, key=lambda n: abs(n.timestamp_ms - adjusted_ms))
        struck = [
            n for n in pending
            if abs(n.timestamp_ms - nearest.timestamp_ms) <= self._chord_threshold_ms
        ]
        if len(struck) < MIN_UNPITCHED_CHORD_STRINGS:
            return None
        for note in struck:
            self._record_match(note, MatchType.HIT)
        if self._chord_verifier is not None and sample_pos is not None:
            self._pending_verifications[sample_pos] = struck
        return MatchResult(
            match_type=MatchType.HIT, matched_events=struck,
            semitone_distance=None,
        )

    def process_detected_notes(
        self, detected: list[TimestampedNote], playback_ms: float
    ) -> list[MatchResult]:
        """Process detected notes against the timeline.

        Args:
            detected: Notes from AudioCapture.get_notes()
            playback_ms: Current playback position in the song

        Returns:
            List of match results for this frame.
        """
        results = []

        # First, mark any notes that have passed the window as missed
        results.extend(self._mark_missed_notes(playback_ms))

        # Process each detected note with an onset
        for ts_note in detected:
            if not ts_note.note.is_onset:
                continue

            adjusted_ms = ts_note.timestamp_ms + self._audio_offset_ms
            detected_midi = ts_note.note.midi_note

            if ts_note.note.unpitched:
                # Nothing here to compare against a pitch or to measure a
                # timing offset with. Two things in a tab can still account
                # for such a strike: a written dead note, which has no pitch
                # by definition, and a full chord, which regularly defeats a
                # monophonic detector however well it was played. A dead note
                # is checked first, being the more specific intent.
                dead = self._dead_note_credit(adjusted_ms)
                credit = dead or self._unpitched_chord_credit(
                    adjusted_ms, ts_note.sample_pos)
                if credit is not None:
                    results.append(credit)
                self._trace(ts_note, adjusted_ms, playback_ms,
                            "dead" if dead is not None else
                            ("chord" if credit is not None else "unmatched"),
                            credit.matched_events[0] if credit
                            and credit.matched_events else None, None)
                continue

            self._record_timing_sample(adjusted_ms, detected_midi)

            # Find tab notes active near this time
            candidates = self._timeline.get_active_notes_at_time(
                adjusted_ms, self._timing_window_ms
            )

            # Filter to PENDING and non-filtered only. Dead notes are held
            # back: they have no pitch to compare, so letting one compete for
            # a pitched strike would let it swallow the strike meant for the
            # real note beside it. They get their chance below, on strikes
            # nothing else can explain.
            pending = [
                n for n in candidates
                if self._get_state(n) == MatchType.PENDING
                and not self._is_filtered(n) and not n.dead
            ]

            # Find closest match by semitone distance (with octave equivalence)
            best = None
            best_dist = None
            for note in pending:
                dist = self._pitch_distance(detected_midi, note)
                # Octave equivalence: if off by ~12 semitones, treat as 0
                octave_dist = dist % 12 if dist >= 12 else dist
                effective = min(dist, octave_dist)
                if best_dist is None or effective < best_dist:
                    best = note
                    best_dist = effective

            # Classify match
            if best_dist == 0:
                match_type = MatchType.HIT
            elif best_dist == 1:
                match_type = MatchType.CLOSE
            else:
                # No written pitch explains this strike. A dead note might:
                # damping the string still lets some pitch through, and which
                # pitch that is says nothing about whether the mute was right.
                dead_result = self._dead_note_credit(adjusted_ms)
                if dead_result is not None:
                    results.append(dead_result)
                self._trace(ts_note, adjusted_ms, playback_ms,
                            "dead" if dead_result is not None else "unmatched",
                            dead_result.matched_events[0] if dead_result
                            and dead_result.matched_events else None, best_dist)
                # Too far off otherwise — ignore this detection, no penalty
                continue

            # Chord handling
            siblings = self._find_chord_siblings(best)
            # Filter out excluded notes from siblings
            siblings = [s for s in siblings if not self._is_filtered(s)]

            # A subharmonic pitch can only be produced by several strings
            # sounding together — the strum itself is proven, so credit the
            # whole chord (a monophonic detector can never report a second
            # chord tone to reach the majority threshold).
            strummed = getattr(ts_note.note, "subharmonic", False)

            if self.chord_partial_credit and len(siblings) > 1 and strummed:
                matched_events = []
                for sibling in siblings:
                    if self._get_state(sibling) == MatchType.PENDING:
                        self._record_match(sibling, match_type)
                        matched_events.append(sibling)
            elif self.chord_partial_credit and len(siblings) > 1:
                # Partial credit mode: only mark the matched note
                matched_events = []
                if self._get_state(best) == MatchType.PENDING:
                    self._record_match(best, match_type)
                    matched_events.append(best)

                # Check if majority of chord is now matched
                total_in_chord = len(siblings)
                needed = math.ceil(total_in_chord / 2)
                matched_count = sum(
                    1 for s in siblings
                    if self._get_state(s) in (MatchType.HIT, MatchType.CLOSE)
                )
                if matched_count >= needed:
                    # Auto-complete remaining pending notes
                    for s in siblings:
                        if self._get_state(s) == MatchType.PENDING:
                            self._record_match(s, match_type)
                            matched_events.append(s)
            else:
                # Easy mode (old behavior): mark all chord siblings
                matched_events = []
                for sibling in siblings:
                    if self._get_state(sibling) == MatchType.PENDING:
                        self._record_match(sibling, match_type)
                        matched_events.append(sibling)

                # Ensure the best note itself is included
                if best not in matched_events:
                    if self._get_state(best) == MatchType.PENDING:
                        self._record_match(best, match_type)
                        matched_events.append(best)

            # Remember the chord so the audio window for this strike, which
            # arrives later, can be checked string by string. Keyed by sample
            # position: timestamps are rescaled by tempo and rewritten in wait
            # mode, the sample index is not.
            # Dead notes are left out: the verifier is told which pitches to
            # expect, and a damped string sounds none of the one written for
            # it. Handing it that pitch would have it hunt for partials that
            # were never there and convict a neighbour for their absence.
            verifiable = [s for s in siblings if not s.dead]
            if (self._chord_verifier is not None and len(verifiable) > 1
                    and ts_note.sample_pos is not None):
                self._pending_verifications[ts_note.sample_pos] = verifiable

            self._trace(ts_note, adjusted_ms, playback_ms,
                        match_type.value, best, best_dist)

            results.append(MatchResult(
                match_type=match_type,
                matched_events=matched_events,
                semitone_distance=best_dist,
            ))

        return results

    def _trace(
        self, ts_note: TimestampedNote, adjusted_ms: float, playback_ms: float,
        outcome: str, note: NoteEvent | None, semitones: int | None,
    ) -> None:
        """Record what became of one strike. Never read back by the matcher."""
        self.strike_trace.append(StrikeTrace(
            strike_ms=ts_note.timestamp_ms,
            adjusted_ms=adjusted_ms,
            playback_ms=playback_ms,
            midi_note=ts_note.note.midi_note,
            confidence=ts_note.note.confidence,
            unpitched=bool(ts_note.note.unpitched),
            subharmonic=bool(getattr(ts_note.note, "subharmonic", False)),
            outcome=outcome,
            note_ms=note.timestamp_ms if note is not None else None,
            semitones=semitones,
        ))

    def process_strike_windows(
        self, windows: list[StrikeWindow]
    ) -> list[MatchResult]:
        """Verify chords string by string against the raw audio of each strike.

        Only downgrades: a string is marked MISS when the audio positively
        shows a different pitch on it. A string whose expected note cannot be
        confirmed (an octave or fifth of a lower string already sounding) is
        left alone, so absence of evidence is never treated as a wrong note.

        A chord played too close to the next one never gets a window at all —
        the audio thread drops it rather than hand over a window polluted by
        the following chord — so pending entries have to be pruned even on a
        call with nothing to apply.
        """
        results: list[MatchResult] = []
        if self._chord_verifier is None:
            return results
        if not windows:
            self._prune_pending_verifications()
            return results

        for window in windows:
            siblings = self._pending_verifications.pop(window.sample_pos, None)
            if not siblings:
                continue
            expected = [n.midi_note for n in siblings]
            verdicts = self._chord_verifier.verify(
                window.audio, window.sample_rate, expected
            )
            if not verdicts:
                continue
            self.chord_verifications += 1
            for note in siblings:
                verdict = verdicts.get(note.midi_note)
                if verdict is None or not verdict.wrong:
                    continue
                if self._get_state(note) not in (MatchType.HIT, MatchType.CLOSE):
                    continue
                self._rerecord_match(note, MatchType.MISS)
                self.chord_strings_corrected += 1
                self.strike_trace.append(StrikeTrace(
                    strike_ms=window.timestamp_ms,
                    adjusted_ms=window.timestamp_ms,
                    playback_ms=window.timestamp_ms,
                    midi_note=note.midi_note,
                    confidence=0.0,
                    unpitched=False,
                    subharmonic=False,
                    outcome="string_taken_back",
                    note_ms=note.timestamp_ms,
                    semitones=None,
                ))
                results.append(MatchResult(
                    match_type=MatchType.MISS,
                    matched_events=[note],
                    semitone_distance=None,
                ))

        self._prune_pending_verifications()
        return results

    def _prune_pending_verifications(self, keep: int = 32) -> None:
        """Drop chords whose audio window never showed up (dropped buffers)."""
        if len(self._pending_verifications) <= keep:
            return
        for stamp in sorted(self._pending_verifications)[:-keep]:
            del self._pending_verifications[stamp]

    def _search_radius_ms(self) -> float:
        """How far back to look for the note a strike belongs to.

        Wide while nothing is known, because a latency larger than the timing
        window would otherwise be unmeasurable. Narrow once the samples say
        where the strikes actually sit: a wide search over a riff that repeats
        one pitch finds two equally good candidates and then refuses to
        measure at all, which is why repetitive passages -- most of a metal
        song -- contributed barely a third of their strikes.
        """
        if len(self.timing_errors_ms) < TIGHTEN_SEARCH_AFTER:
            return LATENCY_SEARCH_MS
        median = statistics.median(self.timing_errors_ms)
        spread = statistics.median([abs(e - median) for e in self.timing_errors_ms])
        needed = abs(median) + SEARCH_SPREAD_MULTIPLE * spread
        return min(LATENCY_SEARCH_MS, max(MIN_SEARCH_MS, needed))

    def _times_its_own_strike(self, note: NoteEvent) -> bool:
        """Whether this note can honestly say when it was played.

        The timing report answers "how far from the beat do you pick", so a
        note may only contribute if its written pitch really sounds at its
        written moment, on a pick of its own. Three kinds cannot:

        - a dead note has no pitch at all; its fret says where the hand damps
        - a bent or sliding note leaves its written pitch on purpose, and the
          collector reports the settled pitch, which is the one it moved TO
        - a hammered, pulled or slid-into note is never picked at all, so any
          strike credited to it belongs to something else

        A hammer-on SOURCE is picked normally at its written pitch and keeps
        contributing. This is the same rule the report already applied to dead
        notes, carried through: measuring against a pitch that did not sound
        when it was written invents the number it then reports. It cost real
        damage once -- a run over a technique test scattered by +-75 ms, and
        the offset built from it sat in the config for days.
        """
        if note.dead:
            return False
        key = (note.timestamp_ms, note.string)
        if key in self._pitch_ranges:      # bend, or a slide in any direction
            return False
        if key in self._legato_sources:    # never picked; inherits its source
            return False
        return True

    def _record_timing_sample(self, adjusted_ms: float, detected_midi: int) -> None:
        """Measure the strike's offset from the nearest pitch-matching tab note.

        Independent of match outcome and searched over a wider range than the
        timing window, so K (auto-sync) can measure latency even when it is so
        large that nothing scores.
        """
        if not self.record_timing_samples:
            return
        # Asymmetric search: input latency is always positive (a strike can
        # never be detected before it was played), so a strike may trail its
        # note by up to the search radius but can only precede it by normal
        # human earliness (the timing window). Without this, repeated
        # same-pitch riffs alias the measurement onto the NEXT note.
        radius = self._search_radius_ms()
        candidates = self._timeline.get_notes_in_range(
            adjusted_ms - radius, adjusted_ms + self._timing_window_ms
        )
        found: list[tuple[float, NoteEvent]] = []
        for note in candidates:
            if self._is_filtered(note):
                continue
            if not self._times_its_own_strike(note):
                continue
            dist = semitone_distance(detected_midi, note.midi_note)
            octave_dist = dist % 12 if dist >= 12 else dist
            if min(dist, octave_dist) > 1:
                continue
            delta = adjusted_ms - note.timestamp_ms
            if delta < -self._timing_window_ms or delta > radius:
                continue
            found.append((delta, note))
        if not found:
            return

        chosen = self._unambiguous(found)
        if chosen is None:
            self.timing_ambiguous += 1
            return
        delta, note = chosen
        self.timing_errors_ms.append(delta)
        self.timing_samples.append(
            TimingSample(delta_ms=delta, string=note.string,
                         midi_note=note.midi_note, note_ms=note.timestamp_ms)
        )

    def _unambiguous(
        self, found: list[tuple[float, NoteEvent]],
    ) -> tuple[float, NoteEvent] | None:
        """The one note a strike belongs to, or None when it cannot be told.

        A riff repeating one pitch puts two equally good candidates in the
        search window, and there is then no way to tell "late against this
        note" from "early against the next". Picking the nearer one measures
        against the wrong note, which is what walked the offset out to
        nonsense on every auto-sync instead of converging.

        No cleverness is available here, only refusal: a prior about what a
        plausible latency looks like still gets the 490-late / 110-early case
        backwards, and both readings are things a human really does. What
        DOES resolve it is evidence rather than assumption -- once enough
        unambiguous strikes exist elsewhere in the song, _search_radius_ms
        narrows the window until the second candidate falls outside it, and
        these same strikes start counting by themselves.
        """
        return found[0] if len(found) == 1 else None

    def reset_timing_samples(self) -> None:
        """Forget every measurement, so the next one starts from scratch."""
        self.timing_errors_ms.clear()
        self.timing_samples.clear()
        self.timing_ambiguous = 0

    def median_timing_error_ms(self, min_samples: int = 5) -> float | None:
        """Median signed timing error of matched strikes, or None if too few.

        Positive = strikes register late (the player feels forced to play
        early). Compensate by lowering audio_offset_ms by this amount.
        """
        if len(self.timing_errors_ms) < min_samples:
            return None
        return statistics.median(self.timing_errors_ms)

    def timing_spread_ms(self, min_samples: int = 5) -> float | None:
        """How much strike timing scatters around its own median.

        This is the number that says WHICH timing problem you have. A large
        median error with a small spread is plain latency: every strike is
        off by the same amount, and shifting the offset fixes all of them.
        A large spread means the strikes disagree with each other, which no
        offset can repair — that is jitter in delivery or detection, or
        simply uneven playing.

        Median absolute deviation rather than standard deviation, so a
        couple of wild outliers do not swamp the figure.
        """
        if len(self.timing_errors_ms) < min_samples:
            return None
        median = statistics.median(self.timing_errors_ms)
        return statistics.median([abs(e - median) for e in self.timing_errors_ms])

    def timing_histogram(self) -> list[tuple[float, int]]:
        """(bin start in ms, count) over an axis that always contains zero.

        The shape is the diagnosis. One narrow hill away from zero is
        latency. One wide hill centred on zero is the player. Two hills is
        something structural -- two kinds of note detected at two different
        delays.

        The axis deliberately reaches past the data to zero and a little
        beyond. Fitted to the samples alone, a tightly grouped player gets
        four fat bars with no reference point, and the one thing the picture
        exists to show -- how far from the beat that group sits -- is exactly
        what falls off the edge.
        """
        if not self.timing_samples:
            return []
        deltas = [s.delta_ms for s in self.timing_samples]
        low_ms = min(min(deltas), -HISTOGRAM_MIN_SPAN_MS)
        high_ms = max(max(deltas), HISTOGRAM_MIN_SPAN_MS)
        bin_ms = TIMING_BIN_MS
        for candidate in HISTOGRAM_BIN_LADDER:
            bin_ms = candidate
            if (high_ms - low_ms) / bin_ms <= HISTOGRAM_MAX_BINS:
                break

        low = math.floor(low_ms / bin_ms) * bin_ms
        high = math.floor(high_ms / bin_ms) * bin_ms
        bins = {low + i * bin_ms: 0
                for i in range(int(round((high - low) / bin_ms)) + 1)}
        for d in deltas:
            bins[min(max(math.floor(d / bin_ms) * bin_ms, low), high)] += 1
        return sorted(bins.items())

    def timing_bin_ms(self) -> float:
        """Bin width the histogram settled on, for labelling its axis."""
        bars = self.timing_histogram()
        if len(bars) < 2:
            return TIMING_BIN_MS
        return bars[1][0] - bars[0][0]

    def timing_by_string(self) -> dict[int, tuple[float, int]]:
        """{string: (median error, sample count)}.

        A per-string difference is neither latency nor playing: it is the
        detector reacting at different speeds to different strings, and one
        global offset cannot fix it. Worth knowing before blaming either.
        """
        grouped: dict[int, list[float]] = defaultdict(list)
        for sample in self.timing_samples:
            grouped[sample.string].append(sample.delta_ms)
        return {s: (statistics.median(v), len(v)) for s, v in sorted(grouped.items())}

    def timing_report(self, min_samples: int = TIMING_MIN_SAMPLES) -> dict | None:
        """Everything needed to say WHICH timing problem this is, or None.

        `residual_ms` is the number that settles the argument: the scatter
        that would still be there after the median was compensated away. If
        it is small, the problem is latency and K fixes it. If it is most of
        the original error, no offset will help and the answer lies in the
        playing, the passage, or the per-string breakdown.
        """
        deltas = [s.delta_ms for s in self.timing_samples]
        if len(deltas) < min_samples:
            return None
        ordered = sorted(deltas)
        median = statistics.median(ordered)
        spread = statistics.median([abs(d - median) for d in ordered])
        before = statistics.mean([abs(d) for d in ordered])
        after = statistics.mean([abs(d - median) for d in ordered])
        by_string = self.timing_by_string()
        string_gap, gap_is_real = self._string_gap(by_string, spread)
        return {
            "verdict": self._timing_verdict(median, spread, gap_is_real,
                                            len(ordered)),
            "string_gap_real": gap_is_real,
            "count": len(ordered),
            "ambiguous": self.timing_ambiguous,
            "median_ms": median,
            "spread_ms": spread,
            "p10_ms": ordered[max(0, int(len(ordered) * 0.10) - 1)],
            "p90_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.90))],
            "mean_error_ms": before,
            "residual_ms": after,
            "explained_fraction": 0.0 if before <= 0 else max(0.0, 1.0 - after / before),
            "by_string": by_string,
            "string_gap_ms": string_gap,
            "histogram": self.timing_histogram(),
        }

    @staticmethod
    def _string_gap(
        by_string: dict[int, tuple[float, int]], spread: float,
    ) -> tuple[float, bool]:
        """(spread between string medians, is it more than chance).

        Two medians built from a couple of dozen loose strikes each differ by
        tens of milliseconds through pure chance, and blaming the detector for
        that would be the same mistake the chord verifier was taught not to
        make. The standard error of a median is about 1.25 sigma / sqrt(n),
        and the MAD reported here is about 0.675 sigma, so the error of the
        DIFFERENCE of two medians comes to roughly the factor below. Only a
        gap clearing several of those counts as evidence.
        """
        usable = {s: v for s, v in by_string.items() if v[1] >= STRING_MIN_SAMPLES}
        if len(usable) < 2:
            return 0.0, False
        lowest = min(usable, key=lambda s: usable[s][0])
        highest = max(usable, key=lambda s: usable[s][0])
        gap = usable[highest][0] - usable[lowest][0]
        error = MEDIAN_SE_FACTOR * spread * math.sqrt(
            1.0 / usable[lowest][1] + 1.0 / usable[highest][1]
        )
        return gap, gap > max(MIN_STRING_GAP_MS, STRING_GAP_SIGMAS * error)

    @staticmethod
    def _timing_verdict(
        median: float, spread: float, gap_is_real: bool, count: int = 0,
    ) -> str:
        """Which of the timing problems this is.

        Order matters. A per-string difference is checked first because it
        masquerades as both of the others: it shifts the median like latency
        and widens the spread like bad playing, while being neither, and one
        global offset cannot remove it.

        The median is measured against its own uncertainty rather than a flat
        threshold. Loose playing centred on the beat still lands its median
        twenty-odd milliseconds off by chance, and calling that latency sends
        the player to press K over nothing.
        """
        if gap_is_real:
            return "per_string"
        error = MEDIAN_SE_FACTOR * spread / math.sqrt(count) if count else 0.0
        late = abs(median) > max(FINE_MS, MEDIAN_SIGMAS * error)
        loose = spread > SCATTER_MS
        if late and loose:
            # Both at once, and saying only "latency" would send the player
            # off to press K and find most of the problem still there.
            return "mixed"
        if late:
            return "latency"
        if loose:
            return "scatter"
        return "fine"

    def get_statistics(self) -> dict:
        """Return current match statistics."""
        total = self.hits + self.close + self.misses
        accuracy = (self.hits / total * 100) if total > 0 else 0.0
        return {
            "hits": self.hits,
            "close": self.close,
            "misses": self.misses,
            "total": total,
            "accuracy_percent": accuracy,
        }

    def get_weakest_sections(
        self, threshold: float = 0.6, min_length: int = 2
    ) -> list[tuple[int, int, float]]:
        """Find contiguous measures below accuracy threshold.

        Returns list of (start_measure, end_measure, accuracy) sorted by
        accuracy ascending. Only returns sections of at least min_length measures.
        """
        if not self._measure_stats:
            return []

        max_measure = max(self._measure_stats.keys())
        weak_runs: list[tuple[int, int, float]] = []
        run_start = None
        run_hits = 0
        run_total = 0

        for m in range(max_measure + 1):
            stats = self._measure_stats.get(m)
            if stats is None:
                # No notes in this measure — not weak, break any run
                if run_start is not None and (m - run_start) >= min_length:
                    acc = run_hits / run_total if run_total > 0 else 0.0
                    weak_runs.append((run_start, m - 1, acc * 100))
                run_start = None
                run_hits = 0
                run_total = 0
                continue

            total = stats["hits"] + stats["close"] + stats["misses"]
            if total == 0:
                if run_start is not None and (m - run_start) >= min_length:
                    acc = run_hits / run_total if run_total > 0 else 0.0
                    weak_runs.append((run_start, m - 1, acc * 100))
                run_start = None
                run_hits = 0
                run_total = 0
                continue

            acc = stats["hits"] / total
            if acc < threshold:
                if run_start is None:
                    run_start = m
                    run_hits = 0
                    run_total = 0
                run_hits += stats["hits"]
                run_total += total
            else:
                if run_start is not None and (m - run_start) >= min_length:
                    run_acc = run_hits / run_total if run_total > 0 else 0.0
                    weak_runs.append((run_start, m - 1, run_acc * 100))
                run_start = None
                run_hits = 0
                run_total = 0

        # Close any open run
        if run_start is not None and (max_measure + 1 - run_start) >= min_length:
            acc = run_hits / run_total if run_total > 0 else 0.0
            weak_runs.append((run_start, max_measure, acc * 100))

        # Sort by accuracy ascending (weakest first)
        weak_runs.sort(key=lambda x: x[2])
        return weak_runs

    def reset(self) -> None:
        """Clear all state. Call on seek/restart."""
        self._note_states.clear()
        self.hits = 0
        self.close = 0
        self.misses = 0
        self._measure_stats.clear()
        self.reset_timing_samples()
        # Chords awaiting their audio window belong to the abandoned position
        self._pending_verifications.clear()
        self.chord_verifications = 0
        self.chord_strings_corrected = 0
        self.strike_trace.clear()

        # One line per strike, and one per string a chord verdict took back.
        # Written only; see StrikeTrace for why it exists.
        self.strike_trace: list[StrikeTrace] = []
