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

    @property
    def audio_offset_ms(self) -> float:
        return self._audio_offset_ms

    @audio_offset_ms.setter
    def audio_offset_ms(self, value: float) -> None:
        self._audio_offset_ms = value

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
            if self._get_state(note) == MatchType.PENDING:
                self._record_match(note, MatchType.MISS)
                results.append(MatchResult(
                    match_type=MatchType.MISS,
                    matched_events=[note],
                    semitone_distance=None,
                ))
        return results

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

            self._record_timing_sample(adjusted_ms, detected_midi)

            # Find tab notes active near this time
            candidates = self._timeline.get_active_notes_at_time(
                adjusted_ms, self._timing_window_ms
            )

            # Filter to PENDING and non-filtered only
            pending = [
                n for n in candidates
                if self._get_state(n) == MatchType.PENDING and not self._is_filtered(n)
            ]
            if not pending:
                continue

            # Find closest match by semitone distance (with octave equivalence)
            best = None
            best_dist = None
            for note in pending:
                dist = semitone_distance(detected_midi, note.midi_note)
                # Octave equivalence: if off by ~12 semitones, treat as 0
                octave_dist = dist % 12 if dist >= 12 else dist
                effective = min(dist, octave_dist)
                if best_dist is None or effective < best_dist:
                    best = note
                    best_dist = effective

            if best is None or best_dist is None:
                continue

            # Classify match
            if best_dist == 0:
                match_type = MatchType.HIT
            elif best_dist == 1:
                match_type = MatchType.CLOSE
            else:
                # Too far off — ignore this detection, no penalty
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
            if (self._chord_verifier is not None and len(siblings) > 1
                    and ts_note.sample_pos is not None):
                self._pending_verifications[ts_note.sample_pos] = siblings

            results.append(MatchResult(
                match_type=match_type,
                matched_events=matched_events,
                semitone_distance=best_dist,
            ))

        return results

    def process_strike_windows(
        self, windows: list[StrikeWindow]
    ) -> list[MatchResult]:
        """Verify chords string by string against the raw audio of each strike.

        Only downgrades: a string is marked MISS when the audio positively
        shows a different pitch on it. A string whose expected note cannot be
        confirmed (an octave or fifth of a lower string already sounding) is
        left alone, so absence of evidence is never treated as a wrong note.
        """
        results: list[MatchResult] = []
        if self._chord_verifier is None or not windows:
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

    def _record_timing_sample(self, adjusted_ms: float, detected_midi: int) -> None:
        """Measure the strike's offset from the nearest pitch-matching tab note.

        Independent of match outcome and searched over a much wider range
        than the timing window, so K (auto-sync) can measure latency even
        when it is so large that nothing scores.
        """
        if not self.record_timing_samples:
            return
        # Asymmetric search: input latency is always positive (a strike can
        # never be detected before it was played), so a strike may trail its
        # note by up to LATENCY_SEARCH_MS but can only precede it by normal
        # human earliness (the timing window). Without this, repeated
        # same-pitch riffs alias the measurement onto the NEXT note.
        candidates = self._timeline.get_notes_in_range(
            adjusted_ms - LATENCY_SEARCH_MS, adjusted_ms + self._timing_window_ms
        )
        best_delta: float | None = None
        for note in candidates:
            if self._is_filtered(note):
                continue
            dist = semitone_distance(detected_midi, note.midi_note)
            octave_dist = dist % 12 if dist >= 12 else dist
            if min(dist, octave_dist) > 1:
                continue
            delta = adjusted_ms - note.timestamp_ms
            if delta < -self._timing_window_ms or delta > LATENCY_SEARCH_MS:
                continue
            if best_delta is None or abs(delta) < abs(best_delta):
                best_delta = delta
        if best_delta is not None:
            self.timing_errors_ms.append(best_delta)

    def median_timing_error_ms(self, min_samples: int = 5) -> float | None:
        """Median signed timing error of matched strikes, or None if too few.

        Positive = strikes register late (the player feels forced to play
        early). Compensate by lowering audio_offset_ms by this amount.
        """
        if len(self.timing_errors_ms) < min_samples:
            return None
        return statistics.median(self.timing_errors_ms)

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
        self.timing_errors_ms.clear()
        # Chords awaiting their audio window belong to the abandoned position
        self._pending_verifications.clear()
        self.chord_verifications = 0
        self.chord_strings_corrected = 0
