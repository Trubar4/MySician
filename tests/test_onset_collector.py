"""Tests for OnsetPitchCollector.

A strike's onset frame carries the attack transient, and with a large
pitch window the first frames after the strike still contain the previous
note's decay. The collector must skip those, aggregate the settled frames,
and stamp the note with the strike time.
"""

from pickhero.audio.input import OnsetPitchCollector

MIN_CONF = 0.8

E2 = 82.41
A2 = 110.0

SKIP = OnsetPitchCollector.SKIP_FRAMES
COLLECT = OnsetPitchCollector.COLLECT_FRAMES


def _feed_frames(c, frames):
    """Feed (freq, conf, is_onset) frames at 12 ms spacing, return emissions."""
    out = []
    for i, (freq, conf, onset) in enumerate(frames):
        r = c.process_frame(freq, conf, onset, i * 12.0, MIN_CONF)
        if r is not None:
            out.append(r)
    return out


def test_strike_with_unconfident_attack_frame_still_detected():
    c = OnsetPitchCollector()
    # Onset frame: garbage pitch, low confidence (typical attack transient),
    # then settled confident frames
    frames = [(163.0, 0.3, True)] + [(E2, 0.94, False)] * COLLECT
    notes = _feed_frames(c, frames)

    assert len(notes) == 1
    assert notes[0].note.is_onset is True
    assert notes[0].note.midi_note == 40  # E2
    assert notes[0].timestamp_ms == 0.0  # strike time, not settle time


def test_early_decay_frames_of_previous_note_are_skipped():
    c = OnsetPitchCollector()
    # First frames after the onset still show the previous note (E2);
    # the settled frames show the actually played A2
    frames = [(0.0, 0.0, True)]
    frames += [(E2, 0.95, False)] * SKIP  # stale window content
    frames += [(A2, 0.92, False)] * (COLLECT - SKIP)
    notes = _feed_frames(c, frames)

    assert len(notes) == 1
    assert notes[0].note.midi_note == 45  # A2, not the stale E2


def test_emits_on_timeout_with_single_confident_frame():
    c = OnsetPitchCollector()
    frames = [(0.0, 0.0, True)]
    frames += [(A2, 0.4, False)] * (COLLECT - 2)  # unconfident
    frames += [(A2, 0.9, False)] * 2  # one late confident pair
    notes = _feed_frames(c, frames)
    assert len(notes) == 1
    assert notes[0].note.midi_note == 45
    assert notes[0].timestamp_ms == 0.0


def test_pitchless_strike_is_reported_as_unpitched():
    """A strike with no pitch in it is still a strike, and says so.

    It passed the noise gate, so something was played; only the pitch is
    missing. Dropping it is what made dead notes impossible to hit, because
    the one honest piece of evidence for a dead note never left the audio
    thread. Whether the tab wanted one here is the matcher's business.
    """
    c = OnsetPitchCollector()
    frames = [(0.0, 0.0, True)] + [(0.0, 0.1, False)] * (COLLECT + 2)
    notes = _feed_frames(c, frames)
    assert len(notes) == 1
    assert notes[0].note.unpitched is True
    assert notes[0].note.is_onset is True
    assert notes[0].timestamp_ms == 0.0


def test_rapid_restrike_closes_previous_collection():
    c = OnsetPitchCollector()
    frames = [(E2, 0.9, True)]
    frames += [(E2, 0.9, False)] * (SKIP + 2)  # first strike collects a bit
    frames += [(A2, 0.9, True)]  # re-strike before timeout
    frames += [(A2, 0.9, False)] * COLLECT
    notes = _feed_frames(c, frames)

    assert len(notes) == 2
    assert notes[0].note.midi_note == 40
    assert notes[0].timestamp_ms == 0.0
    assert notes[1].note.midi_note == 45
    assert notes[1].timestamp_ms == (1 + SKIP + 2) * 12.0


def test_median_of_latest_samples_rejects_outlier():
    c = OnsetPitchCollector()
    frames = [(0.0, 0.0, True)]
    frames += [(E2, 0.9, False)] * SKIP
    frames += [(E2, 0.9, False), (E2 * 2, 0.9, False)]  # harmonic outlier
    frames += [(E2, 0.9, False)] * (COLLECT - SKIP - 2)
    notes = _feed_frames(c, frames)
    assert len(notes) == 1
    assert notes[0].note.midi_note == 40


def test_frames_without_pending_onset_are_ignored():
    c = OnsetPitchCollector()
    frames = [(E2, 0.95, False)] * 10
    assert _feed_frames(c, frames) == []


def test_subharmonic_is_folded_into_range_and_flagged():
    """41 Hz (E5 power chord subharmonic) folds up to E2 with the strum flag."""
    c = OnsetPitchCollector()
    frames = [(0.0, 0.0, True)] + [(41.2, 0.92, False)] * COLLECT
    notes = _feed_frames(c, frames)
    assert len(notes) == 1
    assert notes[0].note.midi_note == 40  # E2
    assert notes[0].note.subharmonic is True


def test_two_octave_subharmonic_folds():
    """27.5 Hz (A5 chord double subharmonic) folds up to A2."""
    c = OnsetPitchCollector()
    frames = [(0.0, 0.0, True)] + [(27.5, 0.9, False)] * COLLECT
    notes = _feed_frames(c, frames)
    assert len(notes) == 1
    assert notes[0].note.midi_note == 45  # A2
    assert notes[0].note.subharmonic is True


def test_normal_pitch_not_flagged_as_subharmonic():
    c = OnsetPitchCollector()
    frames = [(0.0, 0.0, True)] + [(E2, 0.94, False)] * COLLECT
    notes = _feed_frames(c, frames)
    assert len(notes) == 1
    assert notes[0].note.subharmonic is False


def test_too_deep_frequency_rejected():
    """A frequency needing more than 2 octave folds is noise, not a chord.

    The strike survives as unpitched -- something was struck -- but carries no
    note, so it can never be credited against a written pitch.
    """
    c = OnsetPitchCollector()
    frames = [(0.0, 0.0, True)] + [(12.0, 0.9, False)] * (COLLECT + 1)
    notes = _feed_frames(c, frames)
    assert len(notes) == 1
    assert notes[0].note.unpitched is True
    assert notes[0].note.midi_note == 0
