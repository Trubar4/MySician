"""Slowing a recording down without taking its pitch with it.

The whole reason this module exists is that playing a file slower drops its
pitch -- 80 % speed is four semitones flat, which is not a backing track any
more. So the test that matters is not "is it the right length" but "is it
still the same note", and both are here.
"""

import numpy as np
import pytest

from pickhero.audio import timestretch


def _tone(seconds=4.0, freq=220.0, rate=44100):
    t = np.arange(int(rate * seconds)) / rate
    # Two partials, because a pure sine is the one signal every stretch
    # algorithm gets right and would prove nothing.
    return (0.4 * np.sin(2 * np.pi * freq * t)
            + 0.25 * np.sin(2 * np.pi * freq * 3 * t)).astype(np.float32)


def _dominant(samples, rate=44100):
    mono = samples[:, 0] if samples.ndim > 1 else samples
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    return np.fft.rfftfreq(len(mono), 1 / rate)[int(np.argmax(spectrum))]


class TestThePitchSurvives:
    """A backing track four semitones flat is worse than no backing track."""

    @pytest.mark.parametrize("factor", [1.25, 1.5, 2.0])
    def test_the_note_is_unchanged(self, factor):
        out = timestretch.stretch(_tone(), factor)
        assert _dominant(out) == pytest.approx(220.0, abs=2.0)

    def test_resampling_would_have_failed_this_test(self):
        """The check has teeth: the obvious wrong implementation fails it.

        Stretching by repeating samples -- which is what playing the file
        slower does -- moves 220 Hz to 176 Hz, a major third down.
        """
        resampled = np.repeat(_tone(), 5)[::4]     # 1.25x longer, naively
        assert _dominant(resampled) < 200.0


class TestTheLength:
    @pytest.mark.parametrize("factor", [1.11, 1.25, 2.0])
    def test_it_comes_out_the_length_it_was_asked_for(self, factor):
        source = _tone()
        out = timestretch.stretch(source, factor)
        assert len(out) == pytest.approx(len(source) * factor, rel=0.01)

    def test_full_speed_is_left_alone(self):
        source = _tone(seconds=1.0)
        out = timestretch.stretch(source, 1.0)
        assert np.array_equal(out[:, 0], source)

    def test_something_too_short_to_stretch_is_returned_as_it_is(self):
        source = _tone(seconds=0.05)
        assert len(timestretch.stretch(source, 1.5)) == len(source)


class TestItStillSoundsLikeMusic:
    def test_the_loudness_is_kept(self):
        """Overlap-add with the windows in the wrong places cancels itself,
        and the give-away is a quieter result."""
        source = _tone()
        out = timestretch.stretch(source, 1.25)
        assert out.std() == pytest.approx(source.std(), rel=0.15)

    def test_nothing_comes_back_as_silence_or_nonsense(self):
        out = timestretch.stretch(_tone(), 1.5)
        assert np.isfinite(out).all()
        assert np.abs(out).max() > 0.1

    def test_a_stereo_file_stays_stereo(self):
        mono = _tone()
        stereo = np.stack([mono, np.roll(mono, 200)], axis=1)
        out = timestretch.stretch(stereo, 1.25)
        assert out.shape[1] == 2

    def test_the_two_sides_are_not_torn_apart(self):
        """Sliding the channels by different amounts would smear the image, so
        the similarity search runs on one summed signal."""
        mono = _tone()
        stereo = np.stack([mono, mono], axis=1)
        out = timestretch.stretch(stereo, 1.25)
        assert np.allclose(out[:, 0], out[:, 1])


class TestTheCache:
    """A whole song takes seconds to stretch. Twice would be unforgivable."""

    def test_the_name_changes_with_the_speed(self, tmp_path):
        song = tmp_path / "a.ogg"
        song.write_bytes(b"x")
        assert (timestretch.cache_name(song, 0.8)
                != timestretch.cache_name(song, 0.9))

    def test_the_name_changes_when_the_recording_does(self, tmp_path):
        song = tmp_path / "a.ogg"
        song.write_bytes(b"x")
        before = timestretch.cache_name(song, 0.8)
        song.write_bytes(b"a different recording entirely")
        assert timestretch.cache_name(song, 0.8) != before

    def test_an_existing_stretch_is_not_built_again(self, tmp_path, monkeypatch):
        song = tmp_path / "a.ogg"
        song.write_bytes(b"x")
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / timestretch.cache_name(song, 0.8)).write_bytes(b"wav")
        monkeypatch.setattr(timestretch, "_decode",
                            lambda p: pytest.fail("decoded a cached file"))
        assert timestretch.build(song, 0.8, cache).exists()

    def test_it_writes_a_file_that_can_be_read_back(self, tmp_path, monkeypatch):
        import wave
        song = tmp_path / "a.ogg"
        song.write_bytes(b"x")
        source = np.stack([_tone(seconds=2.0)] * 2, axis=1)
        monkeypatch.setattr(timestretch, "_decode", lambda p: (source, 44100))
        built = timestretch.build(song, 0.5, tmp_path / "cache")
        with wave.open(str(built)) as handle:
            assert handle.getnchannels() == 2
            assert handle.getframerate() == 44100
            assert handle.getnframes() == pytest.approx(len(source) * 2, rel=0.01)

    def test_an_interrupted_build_leaves_no_usable_file_behind(
            self, tmp_path, monkeypatch):
        """A half-written file would look exactly like a cache hit ever after."""
        song = tmp_path / "a.ogg"
        song.write_bytes(b"x")
        cache = tmp_path / "cache"

        def explode(path, samples, rate):
            path.write_bytes(b"half a file")
            raise OSError("disk full")

        monkeypatch.setattr(timestretch, "_decode",
                            lambda p: (_tone(seconds=2.0), 44100))
        monkeypatch.setattr(timestretch, "_write_wav", explode)
        with pytest.raises(OSError):
            timestretch.build(song, 0.8, cache)
        assert not (cache / timestretch.cache_name(song, 0.8)).exists()

    def test_the_cache_does_not_grow_without_limit(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        for i in range(timestretch.MAX_CACHED + 6):
            (cache / f"{i:03d}.wav").write_bytes(b"x")
        timestretch._prune(cache)
        assert len(list(cache.glob("*.wav"))) == timestretch.MAX_CACHED
