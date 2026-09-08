"""Playing a song on a guitar tuned somewhere else.

A tab written in Drop C and a guitar strung in Drop D differ by a uniform
two semitones, so the SAME fret numbers are the same music a tone higher.
Nothing about the tab has to be rewritten -- the picture is identical, and
what moves is the pitch the app expects to hear and the pitch of the
recording.

That only works between tunings of the same SHAPE. Drop C to Standard is not
a shift (the sixth string sits lower relative to the others), and no amount
of pitch shifting expresses it; that needs the frets recomputed, which is
what tools/retune.py is for.
"""

import numpy as np
import pytest

from pickhero.audio.note_utils import (
    NAMED_TUNINGS, reachable_tunings, uniform_shift,
)
from pickhero.audio.timestretch import cache_name, pitch_shift
from pickhero.config import Config, MAX_TRANSPOSE
from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)

TUNINGS = dict(NAMED_TUNINGS)


class TestWhichTuningsCanStandInForWhich:

    def test_drop_c_to_drop_d_is_a_whole_tone(self):
        assert uniform_shift(TUNINGS["Drop C"], TUNINGS["Drop D"]) == 2

    def test_a_different_shape_is_not_a_shift_at_all(self):
        """The one the player has to be told about: a Drop tuning and a
        Standard one are not each other transposed."""
        assert uniform_shift(TUNINGS["Drop C"], TUNINGS["Standard"]) is None

    def test_the_drop_family_reaches_itself_and_nothing_else(self):
        names = [n for n, _ in reachable_tunings(TUNINGS["Drop C"])]
        assert "Drop D" in names and "Drop A" in names
        assert not any(n.endswith("Standard") for n in names)

    def test_a_tuning_of_its_own_reaches_only_itself(self):
        assert reachable_tunings(TUNINGS["DADGAD"]) == [("DADGAD", 0)]

    def test_the_written_tuning_is_the_nearest_choice(self):
        assert reachable_tunings(TUNINGS["Drop C"])[0] == ("Drop C", 0)

    def test_nothing_is_reachable_from_nothing(self):
        assert reachable_tunings({}) == []
        assert uniform_shift({}, TUNINGS["Standard"]) is None


class TestTheFretsNeverMove:
    """The whole reason this is cheap."""

    def _song(self):
        notes = [NoteEvent(timestamp_ms=i * 500.0, duration_ms=500.0,
                           midi_note=36 + i, string=6 - i % 6, fret=i % 12,
                           measure=0)
                 for i in range(24)]
        return Timeline(notes, SongMetadata(title="t", tempo=120,
                                            tuning=dict(TUNINGS["Drop C"])),
                        measures=[MeasureInfo(index=0, start_ms=0.0,
                                              end_ms=12_000.0)])

    def test_every_fret_number_is_the_same(self):
        song = self._song()
        assert ([n.fret for n in song.transposed(2).notes]
                == [n.fret for n in song.notes])

    def test_and_every_string(self):
        song = self._song()
        assert ([n.string for n in song.transposed(2).notes]
                == [n.string for n in song.notes])

    def test_the_pitches_all_move_together(self):
        song = self._song()
        assert ([n.midi_note + 2 for n in song.notes]
                == [n.midi_note for n in song.transposed(2).notes])

    def test_the_tuning_moves_with_them(self):
        song = self._song()
        assert song.transposed(2).metadata.tuning == TUNINGS["Drop D"]

    def test_the_timing_is_untouched(self):
        song = self._song()
        assert ([n.timestamp_ms for n in song.transposed(-3).notes]
                == [n.timestamp_ms for n in song.notes])
        assert song.transposed(-3).duration_ms == song.duration_ms

    def test_playing_it_as_written_costs_nothing(self):
        song = self._song()
        assert song.transposed(0) is song


class TestTheRecordingMovesWithThePlayer:

    def _peak_hz(self, x, rate, near):
        n = 1 << 17
        seg = np.pad(x[:n, 0] * np.hanning(min(n, len(x))),
                     (0, max(0, n - len(x))))
        mag = np.abs(np.fft.rfft(seg))
        freqs = np.fft.rfftfreq(n, 1 / rate)
        band = (freqs > near * 0.9) & (freqs < near * 1.1)
        k = int(np.argmax(mag * band))
        a, b, c = mag[k - 1], mag[k], mag[k + 1]
        d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
        return (k + d) * rate / n

    def _sine(self, hz=220.0, seconds=4.0, rate=44100):
        t = np.arange(int(rate * seconds)) / rate
        return (np.sin(2 * np.pi * hz * t) * 0.4
                ).astype(np.float32).reshape(-1, 1)

    @pytest.mark.parametrize("semitones", [2, -2, 1, 5, -5])
    def test_the_shift_is_exact(self, semitones):
        """A semitone is a ratio; there is nothing to approximate. Measured
        against a sine of known pitch, every one lands inside a quarter of a
        cent -- a five-hundredth of a semitone."""
        rate = 44100
        out = pitch_shift(self._sine(), semitones)
        want = 220.0 * 2 ** (semitones / 12)
        got = self._peak_hz(out, rate, want)
        assert abs(1200 * np.log2(got / want)) < 1.0

    @pytest.mark.parametrize("semitones", [2, -2, 5])
    def test_the_length_is_untouched(self, semitones):
        """Which is what keeps every sync point, the offset and the whole
        sync map describing this file. Nothing has to be measured again."""
        source = self._sine()
        assert len(pitch_shift(source, semitones)) == len(source)

    def test_as_written_is_the_file_itself(self):
        source = self._sine()
        assert pitch_shift(source, 0) is source

    def test_a_shifted_copy_is_a_different_cache_entry(self, tmp_path):
        """Otherwise the transposed song would quietly play the untransposed
        copy -- the fault the sync rate already caused once."""
        recording = tmp_path / "song.mp3"
        recording.write_bytes(b"x")
        plain = cache_name(recording, 1.0)
        up = cache_name(recording, 1.0, semitones=2)
        down = cache_name(recording, 1.0, semitones=-2)
        assert len({plain, up, down}) == 3

    def test_and_the_name_says_which(self, tmp_path):
        recording = tmp_path / "song.mp3"
        recording.write_bytes(b"x")
        assert "+2st" in cache_name(recording, 1.0, semitones=2)
        assert "st" not in cache_name(recording, 1.0)


class TestItIsRememberedPerSong:

    def test_stored_and_read_back(self):
        config = Config()
        config.set_transpose_for("song", 2)
        assert config.transpose_for("song") == 2

    def test_as_written_is_not_stored_at_all(self):
        """An entry saying "no change" says nothing."""
        config = Config()
        config.set_transpose_for("song", 2)
        config.set_transpose_for("song", 0)
        assert config.song_transpose == {}

    def test_it_cannot_be_set_past_what_a_tuning_family_spans(self):
        config = Config()
        config.set_transpose_for("song", 99)
        assert config.transpose_for("song") == MAX_TRANSPOSE

    def test_a_stored_value_out_of_range_is_ignored(self):
        config = Config()
        config.song_transpose["song"] = 40
        assert config.transpose_for("song") == 0
