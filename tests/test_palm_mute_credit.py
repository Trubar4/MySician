"""A chug that comes back with no pitch at all.

A palm mute is a note the tab TELLS the player to choke, and a choked string
sometimes gives monophonic YIN nothing to lock onto. For a chord that is
already handled; for a single note the strike is normally held and confirmed
against the written pitch from the raw audio -- except that a chug riff runs
in eighths, the audio window is trimmed to the gap before the next strike, and
under the minimum it is dropped. So on exactly the passage where chugs live,
no evidence can arrive at all.

This is leniency, and the tests below are mostly about its edges: what it must
NOT credit. The thing that keeps it honest is that a wrong fret still sounds a
different pitch, and a strike carrying a pitch never reaches this path.
"""

import pytest

from pickhero.audio.detector import DetectedNote
from pickhero.audio.input import TimestampedNote
from pickhero.matcher import MatchType, NoteMatcher
from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline

NOTE_MS = 1000.0


def _note(**kwargs):
    base = dict(timestamp_ms=NOTE_MS, duration_ms=200.0, midi_note=40,
                string=6, fret=0, measure=0)
    base.update(kwargs)
    return NoteEvent(**base)


def _matcher(notes, **kwargs):
    timeline = Timeline(notes, SongMetadata(title="chug", tempo=150))
    return NoteMatcher(timeline, timing_window_ms=150.0, **kwargs)


def _unpitched(ms=NOTE_MS):
    """A strike loud enough to be real that produced no pitch."""
    return TimestampedNote(
        note=DetectedNote(0, 0.0, 0.0, "", True, unpitched=True),
        timestamp_ms=ms, sample_pos=None)


def _pitched(midi, ms=NOTE_MS):
    freq = 440.0 * 2 ** ((midi - 69) / 12)
    return TimestampedNote(
        note=DetectedNote(midi, freq, 0.95, "x", True), timestamp_ms=ms)


def _play(matcher, strike, notes):
    matcher.process_detected_notes([strike], strike.timestamp_ms)
    matcher.process_detected_notes([], NOTE_MS + 2000)
    return [matcher.get_note_state(n) for n in notes]


class TestWhatItCredits:
    def test_a_written_chug_is_credited(self):
        notes = [_note(palm_mute=True)]
        assert _play(_matcher(notes), _unpitched(), notes) == [MatchType.HIT]

    def test_it_counts_what_it_credited(self):
        notes = [_note(palm_mute=True)]
        matcher = _matcher(notes)
        _play(matcher, _unpitched(), notes)
        assert matcher.palm_mutes_credited == 1

    def test_a_muted_double_stop_is_one_stroke(self):
        """The picking hand chokes both strings at once; there is no second
        strike coming for the second string."""
        notes = [_note(palm_mute=True), _note(midi_note=47, string=5, fret=2,
                                              palm_mute=True)]
        assert _play(_matcher(notes), _unpitched(), notes) == \
            [MatchType.HIT, MatchType.HIT]

    def test_a_late_strike_inside_the_window_still_counts(self):
        notes = [_note(palm_mute=True)]
        assert _play(_matcher(notes), _unpitched(NOTE_MS + 100), notes) \
            == [MatchType.HIT]


class TestWhatItMustNotCredit:
    def test_a_note_with_no_palm_mute_is_not_credited(self):
        """Without a written mute this is just a note that was not heard, and
        the rescue path -- which needs actual evidence -- is what handles it."""
        notes = [_note()]
        assert _play(_matcher(notes), _unpitched(), notes) == [MatchType.MISS]

    def test_a_chug_on_the_wrong_fret_is_still_wrong(self):
        """The whole reason this is safe: a wrong fret sounds a PITCH, and a
        strike carrying a pitch never reaches this path at all."""
        notes = [_note(palm_mute=True)]
        state = _play(_matcher(notes), _pitched(43), notes)
        assert state == [MatchType.MISS]

    def test_a_chug_two_bars_away_is_not_reached(self):
        notes = [_note(palm_mute=True)]
        assert _play(_matcher(notes), _unpitched(NOTE_MS + 4000), notes) \
            == [MatchType.MISS]

    def test_one_strike_credits_one_chug_not_the_riff(self):
        """Eighths at 150 BPM are 200 ms apart, well outside the chord
        threshold: each has to be played."""
        notes = [_note(timestamp_ms=NOTE_MS + i * 200.0, palm_mute=True)
                 for i in range(4)]
        states = _play(_matcher(notes), _unpitched(), notes)
        assert states.count(MatchType.HIT) == 1

    def test_a_dead_note_is_not_this(self):
        """A dead note has its own rule and reaches it first; this must not
        double-count it."""
        notes = [_note(dead=True, palm_mute=True)]
        matcher = _matcher(notes)
        _play(matcher, _unpitched(), notes)
        assert matcher.palm_mutes_credited == 0

    def test_a_chord_is_not_this_either(self):
        """Two or more written notes are the chord rule's business, measured
        and already in place."""
        notes = [_note(), _note(midi_note=47, string=5, fret=2)]
        matcher = _matcher(notes)
        _play(matcher, _unpitched(), notes)
        assert matcher.palm_mutes_credited == 0


class TestTheSwitch:
    def test_it_can_be_turned_off(self):
        """It is the one rule here granted on partial evidence, so it has to
        be possible to take back."""
        notes = [_note(palm_mute=True)]
        matcher = _matcher(notes, palm_mute_credit=False)
        assert _play(matcher, _unpitched(), notes) == [MatchType.MISS]

    def test_reset_clears_the_count(self):
        notes = [_note(palm_mute=True)]
        matcher = _matcher(notes)
        _play(matcher, _unpitched(), notes)
        matcher.reset()
        assert matcher.palm_mutes_credited == 0

    def test_the_strike_log_says_which_rule_fired(self):
        notes = [_note(palm_mute=True)]
        matcher = _matcher(notes)
        _play(matcher, _unpitched(), notes)
        assert [t.outcome for t in matcher.strike_trace] == ["palm_mute"]
