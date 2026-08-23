"""A chug that comes back with no pitch is NOT credited, and that is measured.

For one week it was. The argument ran: a palm mute is a note the tab tells
the player to choke, a choked string sometimes gives monophonic YIN nothing to
lock onto, and a chug riff is too fast for the audio window that would
otherwise confirm it -- so on exactly the passage where chugs live, no
evidence can ever arrive. Crediting the strike looked like the only way not to
punish good playing.

Block 7 of `record_reference.py` was recorded to settle it, and it settled it
the other way. On 87 correctly played chugs a strike arrives with no pitch at
all **3 times** -- 3.4 %, not the 16-20 % a power chord shows. On the take
played a fret off it happens at 3.5 %, the same rate. So a pitchless chug is
neither common nor a sign of anything: the leniency would have bought three
notes in eighty-seven and paid for them by turning two wrong ones green.

What the takes did show is that a palm-muted low string is heard an OCTAVE
above what was played, on nearly every strike. That costs nothing, because
the matcher grants octave equivalence on purpose.

These tests hold the removal in place: a written palm mute changes scoring in
no way at all.
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


class TestAPitchlessChugIsNotCredited:
    def test_a_written_palm_mute_gets_no_special_treatment(self):
        notes = [_note(palm_mute=True)]
        assert _play(_matcher(notes), _unpitched(), notes) == [MatchType.MISS]

    def test_it_scores_exactly_as_the_same_note_without_the_mute_would(self):
        """A palm mute does not change the pitch, so it must not change the
        scoring either. That is the whole of the rule now."""
        muted = [_note(palm_mute=True)]
        plain = [_note()]
        assert (_play(_matcher(muted), _unpitched(), muted)
                == _play(_matcher(plain), _unpitched(), plain))

    def test_the_strike_log_does_not_claim_a_rule_fired(self):
        notes = [_note(palm_mute=True)]
        matcher = _matcher(notes)
        _play(matcher, _unpitched(), notes)
        assert [t.outcome for t in matcher.strike_trace] == ["unmatched"]


class TestWhatStillHolds:
    """The three rules the pitchless path does have, none of which was
    touched by removing the fourth."""

    def test_a_dead_note_still_counts_on_the_strike_alone(self):
        notes = [_note(dead=True)]
        assert _play(_matcher(notes), _unpitched(), notes) == [MatchType.HIT]

    def test_a_muted_chug_played_right_is_heard_normally(self):
        """The measurement's real finding: 97 % of chugs carry a pitch, and
        those never needed a rule."""
        notes = [_note(palm_mute=True)]
        assert _play(_matcher(notes), _pitched(40), notes) == [MatchType.HIT]

    def test_an_octave_up_is_still_the_same_note(self):
        """A palm-muted low string is heard an octave above what was played on
        nearly every strike -- measured on block 7, 59 of 61."""
        notes = [_note(palm_mute=True)]
        assert _play(_matcher(notes), _pitched(52), notes) == [MatchType.HIT]

    def test_a_chug_on_the_wrong_fret_is_still_wrong(self):
        notes = [_note(palm_mute=True)]
        assert _play(_matcher(notes), _pitched(43), notes) == [MatchType.MISS]

    def test_a_pitchless_strum_still_credits_a_written_chord(self):
        notes = [_note(palm_mute=True),
                 _note(midi_note=47, string=5, fret=2, palm_mute=True)]
        assert _play(_matcher(notes), _unpitched(), notes) == \
            [MatchType.HIT, MatchType.HIT]


class TestTheSettingIsGoneWithoutBreakingAnything:
    def test_an_old_config_file_still_loads(self, tmp_path, monkeypatch):
        """The setting outlived the rule in anyone's saved file, and an
        unknown key would throw the whole config away -- device, calibration
        and sync with it."""
        import json
        import pickhero.config as config_module
        from pickhero.config import Config

        path = tmp_path / "settings.json"
        monkeypatch.setattr(config_module, "CONFIG_FILE", path)
        path.write_text(json.dumps({"palm_mute_credit": True,
                                    "timing_window_ms": 175.0}))
        assert Config.load().timing_window_ms == pytest.approx(175.0)
