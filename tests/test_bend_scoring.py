"""How far a bend actually went.

The visual has existed for a while; the scoring was deliberately blind,
accepting any pitch inside the bend's range, because "judging how FAR a bend
went needs a pitch contour the detector does not produce". That turned out to
be wrong in one word: the detector does produce one, every ~11.6 ms, and the
matcher was already being handed it and throwing it away.

The player's ruling is what these tests pin down: a bend that falls short is
YELLOW, never red, and the target has to be HELD as long as it is written,
roughly a quarter tone accurate.
"""

import math

import numpy as np
import pytest

from pickhero.audio.detector import DetectedNote, PitchDetector
from pickhero.audio.input import TimestampedNote
from pickhero.matcher import (
    BEND_MIN_SAMPLES, BEND_TOLERANCE_CENTS, MatchType, NoteMatcher,
)
from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline

FRAME_MS = 11.6          # one detector hop at 44.1 kHz
NOTE_MS = 1000.0         # when the bent note is written
MIDI = 55                # G3, third string
STRING = 3


def _bend_note(top=2.0, reaches_at=0.4, duration=1000.0, releases_at=1.0):
    """A note the tab bends up `top` semitones and holds to `releases_at`."""
    points = [(0.0, 0.0), (reaches_at, top), (releases_at, top)]
    if releases_at < 1.0:
        points.append((1.0, 0.0))
    return NoteEvent(timestamp_ms=NOTE_MS, duration_ms=duration,
                     midi_note=MIDI, string=STRING, fret=7, measure=0,
                     bend=tuple(points))


def _timeline(note):
    return Timeline([note], SongMetadata(title="bend", tempo=100))


def _matcher(note, **kwargs):
    return NoteMatcher(_timeline(note), timing_window_ms=150.0, **kwargs)


def _strike(midi=MIDI, ms=NOTE_MS):
    freq = 440.0 * 2 ** ((midi - 69) / 12)
    return TimestampedNote(
        note=DetectedNote(midi, freq, 0.95, "G3", True), timestamp_ms=ms)


def _reading(semitones_above, ms):
    """One frame of the sustained pitch stream, `semitones_above` the note."""
    midi = MIDI + semitones_above
    freq = 440.0 * 2 ** ((midi - 69) / 12)
    return TimestampedNote(
        note=DetectedNote(int(round(midi)), freq, 0.95, "x", False),
        timestamp_ms=ms)


def _contour(shape, note, step_ms=FRAME_MS):
    """Readings across the note, `shape` mapping 0..1 to semitones bent."""
    frames = []
    ms = note.timestamp_ms
    while ms <= note.timestamp_ms + note.duration_ms:
        position = (ms - note.timestamp_ms) / note.duration_ms
        frames.append(_reading(shape(position), ms))
        ms += step_ms
    return frames


def _play(matcher, note, shape, frames=None):
    """Strike the note, feed a contour, then run past its end."""
    matcher.process_detected_notes([_strike()], NOTE_MS)
    if shape is not None:
        frames = _contour(shape, note)
    for frame in (frames or []):
        matcher.process_detected_notes([frame], frame.timestamp_ms)
    matcher.process_detected_notes(
        [], note.timestamp_ms + note.duration_ms + 500)
    return matcher.get_note_state(note)


def _ramp(top, reaches_at=0.4, releases_at=1.0):
    """The pitch a well-played bend traces: up, hold, (release)."""
    def shape(position):
        if position < reaches_at:
            return top * position / reaches_at
        if position <= releases_at:
            return top
        fall = (position - releases_at) / max(1e-6, 1.0 - releases_at)
        return top * max(0.0, 1.0 - fall * 3)
    return shape


class TestABendThatWasPlayed:
    def test_a_full_bend_stays_green(self):
        note = _bend_note(top=2.0)
        assert _play(_matcher(note), note, _ramp(2.0)) == MatchType.HIT

    def test_a_quarter_tone_short_still_counts(self):
        """The player's own tolerance: about a quarter tone."""
        note = _bend_note(top=2.0)
        short = 2.0 - (BEND_TOLERANCE_CENTS - 5) / 100.0
        assert _play(_matcher(note), note, _ramp(short)) == MatchType.HIT

    def test_a_bend_and_release_written_as_such_counts(self):
        note = _bend_note(top=2.0, reaches_at=0.3, releases_at=0.7)
        assert _play(_matcher(note), note,
                     _ramp(2.0, reaches_at=0.3, releases_at=0.7)) \
            == MatchType.HIT


class TestABendThatFellShort:
    def test_half_a_bend_is_yellow(self):
        note = _bend_note(top=2.0)
        assert _play(_matcher(note), note, _ramp(1.0)) == MatchType.CLOSE

    def test_no_bend_at_all_is_yellow(self):
        note = _bend_note(top=2.0)
        assert _play(_matcher(note), note, lambda p: 0.0) == MatchType.CLOSE

    def test_it_is_never_red(self):
        """A bend played imperfectly is a note played imperfectly, not a note
        missed. The player ruled on this explicitly."""
        note = _bend_note(top=2.0)
        assert _play(_matcher(note), note, lambda p: 0.0) != MatchType.MISS

    def test_touching_the_target_and_letting_go_is_yellow(self):
        """Written held to the end of the note, let go straight away.

        The threshold is a measured one -- a bend has to stand for 30 % of the
        written hold -- so this case is written well clear of it rather than
        against it: reaching at 20 % of the note and gone again by 26 % is
        about a sixth of what the tab asked for.
        """
        note = _bend_note(top=2.0, reaches_at=0.2)
        assert _play(_matcher(note), note,
                     _ramp(2.0, reaches_at=0.2, releases_at=0.26)) \
            == MatchType.CLOSE

    def test_bending_past_the_target_is_not_judged(self):
        """Overshoot stays green, and that is a measurement, not a mercy.

        The player's own correct bends land between 7 and 51 cents ABOVE the
        written top -- every single one of them. A rule that marked sharp
        bends down would mark down the takes recorded to prove it works. So
        both questions are one-sided: did it reach the top, and was it still
        up there at the end. How far past the top it went is intonation, and
        nothing here was asked to judge that.
        """
        note = _bend_note(top=2.0)
        assert _play(_matcher(note), note, _ramp(3.0)) == MatchType.HIT


class TestWhatIsNotEvidence:
    """Measured on the real takes: during a vibratoed bend the detector throws
    out readings 9, 18 and 36 semitones below the written pitch."""

    def test_an_octave_error_does_not_read_as_the_bend_collapsing(self):
        note = _bend_note(top=2.0)
        good = _contour(_ramp(2.0), note)
        # Every third reading replaced by the kind of stray the detector
        # actually produces while the pitch is moving.
        for index in range(0, len(good), 3):
            good[index] = _reading(-12.0, good[index].timestamp_ms)
        assert _play(_matcher(note), note, None, good) == MatchType.HIT

    def test_strays_alone_are_not_enough_to_judge_anything(self):
        """Strip them out and there is nothing left, which is not evidence
        that the bend fell short."""
        note = _bend_note(top=2.0)
        frames = [_reading(-12.0, NOTE_MS + i * FRAME_MS) for i in range(40)]
        assert _play(_matcher(note), note, None, frames) == MatchType.HIT


class TestVibrato:
    """The case that was expected to embarrass the rule, and did.

    A vibratoed bend is played by releasing and re-bending, so its pitch
    spends much of the hold below the target on purpose -- 37 % of readings
    within a quarter tone on the player's own take. Counting frames read that
    as a bend let go; measuring the span from the first reading on target to
    the last reads it as what it is.
    """

    def test_a_bend_with_vibrato_on_it_stays_green(self):
        import math
        note = _bend_note(top=2.0, reaches_at=0.25)

        def shape(position):
            if position < 0.25:
                return 2.0 * position / 0.25
            # +-0.7 semitones at 6 Hz, which is what the take measures.
            return 2.0 - 0.7 * (1 - math.cos(2 * math.pi * 6 * position)) / 2

        assert _play(_matcher(note), note, shape) == MatchType.HIT

    def test_a_bend_let_go_at_once_is_still_caught(self):
        """The same span rule has to keep telling these two apart."""
        note = _bend_note(top=2.0, reaches_at=0.15)
        assert _play(_matcher(note), note,
                     _ramp(2.0, reaches_at=0.15, releases_at=0.25)) \
            == MatchType.CLOSE


class TestItNeverConvictsOnSilence:
    """The chord verifier's presumption of innocence, one level up. Absence of
    a contour is the commonest thing in this signal path."""

    def test_a_note_with_no_contour_keeps_its_verdict(self):
        note = _bend_note(top=2.0)
        assert _play(_matcher(note), note, None) == MatchType.HIT

    def test_too_few_readings_are_not_evidence(self):
        note = _bend_note(top=2.0)
        frames = [_reading(0.0, NOTE_MS + i * FRAME_MS)
                  for i in range(BEND_MIN_SAMPLES - 1)]
        assert _play(_matcher(note), note, None, frames) == MatchType.HIT

    def test_readings_from_another_note_do_not_count(self):
        """Only what sounded during the note itself can say anything about it."""
        note = _bend_note(top=2.0)
        frames = [_reading(0.0, NOTE_MS - 900 + i * FRAME_MS) for i in range(20)]
        assert _play(_matcher(note), note, None, frames) == MatchType.HIT

    def test_a_missed_note_is_not_made_worse(self):
        note = _bend_note(top=2.0)
        matcher = _matcher(note)
        for frame in _contour(lambda p: 0.0, note):
            matcher.process_detected_notes([frame], frame.timestamp_ms)
        matcher.process_detected_notes([], NOTE_MS + note.duration_ms + 500)
        assert matcher.get_note_state(note) == MatchType.MISS

    def test_a_hold_too_short_to_be_written_is_not_judged_on_holding(self):
        """A bend written across a sixteenth has no plateau; only the height
        is asked about."""
        note = _bend_note(top=2.0, reaches_at=0.6, duration=200.0)
        assert _play(_matcher(note), note,
                     _ramp(2.0, reaches_at=0.6)) == MatchType.HIT


class TestTheSwitchAndTheBooks:
    def test_it_can_be_turned_off(self):
        note = _bend_note(top=2.0)
        matcher = _matcher(note, bend_check=False)
        assert _play(matcher, note, lambda p: 0.0) == MatchType.HIT

    def test_the_counters_say_what_happened(self):
        note = _bend_note(top=2.0)
        matcher = _matcher(note)
        _play(matcher, note, lambda p: 0.0)
        assert (matcher.bends_judged, matcher.bends_short) == (1, 1)

    def test_a_bend_is_judged_once(self):
        note = _bend_note(top=2.0)
        matcher = _matcher(note)
        _play(matcher, note, lambda p: 0.0)
        for _ in range(5):
            matcher.process_detected_notes([], NOTE_MS + 5000)
        assert matcher.bends_judged == 1

    def test_reset_arms_it_again(self):
        note = _bend_note(top=2.0)
        matcher = _matcher(note)
        _play(matcher, note, lambda p: 0.0)
        matcher.reset()
        assert matcher.bends_judged == 0
        assert _play(matcher, note, lambda p: 0.0) == MatchType.CLOSE

    def test_wait_mode_readings_are_not_collected(self):
        """Wait mode pins every timestamp to one instant, so a contour taken
        there says nothing about how long anything was held."""
        note = _bend_note(top=2.0)
        matcher = _matcher(note)
        matcher.record_contour = False
        assert _play(matcher, note, lambda p: 0.0) == MatchType.HIT

    def test_an_unbent_note_is_never_touched(self):
        plain = NoteEvent(timestamp_ms=NOTE_MS, duration_ms=1000.0,
                          midi_note=MIDI, string=STRING, fret=7, measure=0)
        matcher = _matcher(plain)
        assert _play(matcher, plain, lambda p: 0.0) == MatchType.HIT
        assert matcher.bends_judged == 0

    def test_the_contour_does_not_grow_without_limit(self):
        note = _bend_note(top=2.0)
        matcher = _matcher(note)
        for i in range(4000):
            ms = NOTE_MS + i * FRAME_MS
            matcher.process_detected_notes([_reading(0.0, ms)], ms)
        assert len(matcher._contour) < 1000


class TestThroughTheRealDetector:
    """Fabricated contours prove the rule; they do not prove the detector can
    follow a bend at all. Synthetic audio through the real PitchDetector does
    -- and it is only a mechanism check: a real guitar is noisier, which is
    what block 6 of record_reference.py is for.
    """

    RATE = 44100
    HOP = 512

    def _bend_audio(self, semitones, base=196.0, rise_ms=250, total_ms=1200):
        n = int(self.RATE * total_ms / 1000)
        t = np.arange(n) / self.RATE
        ramp = np.clip(t / (rise_ms / 1000), 0, 1)
        freq = base * 2 ** (semitones * ramp / 12)
        phase = 2 * np.pi * np.cumsum(freq) / self.RATE
        wave = sum(amp * np.sin(k * phase)
                   for k, amp in [(1, 1.0), (2, 0.5), (3, 0.3), (4, 0.15)])
        return (wave * np.exp(-t * 0.8) / 3).astype(np.float32)

    def _highest(self, semitones):
        audio = self._bend_audio(semitones)
        detector = PitchDetector(buf_size=4096, hop_size=self.HOP,
                                 sample_rate=self.RATE)
        top = 0.0
        for i in range(0, len(audio) - self.HOP + 1, self.HOP):
            detector.process(audio[i:i + self.HOP])
            if detector.last_freq > 0 and detector.last_confidence >= 0.8:
                top = max(top, 12 * math.log2(detector.last_freq / 196.0))
        return top

    def test_a_full_bend_reads_as_a_full_bend(self):
        assert self._highest(2.0) == pytest.approx(2.0, abs=0.15)

    def test_a_half_bend_reads_as_a_half_bend(self):
        assert self._highest(1.0) == pytest.approx(1.0, abs=0.15)

    def test_a_half_bend_lands_outside_the_tolerance_of_a_full_one(self):
        """If it did not, no threshold could separate them and the whole
        feature would be a coin toss."""
        gap = self._highest(2.0) - self._highest(1.0)
        assert gap > BEND_TOLERANCE_CENTS / 100.0
