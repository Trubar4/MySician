"""Reading a recording back at another rate, which is half a pitch shift.

Linear interpolation is not a resampler, and the measurement is what said
so: against an ideal (FFT) resample of the player's own mix it managed
**29.8 dB** of signal to noise at +2 semitones, and lowpassing both to
18 kHz left it at 29.8 -- so that was not a band edge being fudged, it was
noise across the whole band. Shifting DOWN it also put **+17.9 dB** into
16-20 kHz where there should have been nothing at all: everything above the
new Nyquist, folded back down.

So the tests here are about those two things and not about the arithmetic:
how much of what was played survives, and what arrives that never was.
Every one of them fails on linear interpolation, which is the only thing
that makes them worth having.
"""

import numpy as np
import pytest

from pickhero.audio import timestretch
from pickhero.audio.timestretch import _resample

RATE = 44100


def _linear(samples, ratio, out_n):
    """What used to be here, kept as the thing these tests must reject."""
    index = np.arange(max(0, out_n), dtype=np.float64) * ratio
    low = np.clip(index.astype(np.int64), 0, max(0, len(samples) - 2))
    frac = (index - low).astype(np.float32).reshape(-1, 1)
    return (samples[low] * (1.0 - frac)
            + samples[low + 1] * frac).astype(np.float32)


def _ideal(seg, out_n):
    """A band-limited resample of `seg` into `out_n` frames, exactly.

    Truncating the spectrum IS the brick wall a resampler approximates, so
    this is the reference rather than a second opinion -- and it is only a
    reference because the ratio it implies, `len(seg) / out_n`, is rational
    and is the one the caller is handed.
    """
    out = np.empty((out_n, seg.shape[1]))
    for channel in range(seg.shape[1]):
        spectrum = np.fft.rfft(seg[:, channel].astype(np.float64))
        keep = spectrum[:out_n // 2 + 1].copy()
        if out_n % 2 == 0:
            keep[-1] = keep[-1].real
        out[:, channel] = np.fft.irfft(keep, n=out_n) * (out_n / len(seg))
    return out


def _snr_db(got, ref, trim=2048):
    got = got[trim:-trim].astype(np.float64)
    ref = ref[trim:-trim]
    return 10 * np.log10(np.sum(ref ** 2) / np.sum((got - ref) ** 2))


def _tone(hz, frames, amplitude=0.4):
    t = np.arange(frames) / RATE
    return (amplitude * np.sin(2 * np.pi * hz * t)
            ).astype(np.float32).reshape(-1, 1)


def _level_db(samples, hz, trim=2048):
    """How loud a tone of exactly `hz` is, in dB.

    Correlated against that frequency rather than read off an FFT bin: a
    Hanning window loses up to 1.4 dB to a peak that falls between two
    bins, which is larger than anything being measured here.
    """
    mono = samples[trim:-trim, 0].astype(np.float64)
    window = np.hanning(len(mono))
    turn = np.exp(-2j * np.pi * hz * np.arange(len(mono)) / RATE)
    return 20 * np.log10(2.0 * abs(np.sum(mono * window * turn))
                         / np.sum(window) + 1e-12)


class TestWhatArrivesThatWasNeverPlayed:
    """Reading faster than the file throws samples away, so everything
    above the new Nyquist has to go BEFORE it folds back down."""

    def test_a_tone_above_the_new_nyquist_is_not_heard_at_all(self):
        # Read at 1.5x, so the new Nyquist is two thirds of the old one and
        # a 17.6 kHz tone has nowhere to be. Linear leaves it sitting at
        # 11.7 kHz, in the middle of the music.
        source = _tone(17_640.0, 1 << 16)
        out_n = int(len(source) / 1.5)
        got = _resample(source, 1.5, out_n)
        was = _linear(source, 1.5, out_n)
        # Wherever it folds to is beside the point: nothing at all should
        # come out, and with linear almost all of it does.
        assert 20 * np.log10(np.std(got) + 1e-12) < -40
        assert 20 * np.log10(np.std(was) + 1e-12) > -20

    def test_shifting_down_leaves_the_top_of_the_band_alone(self):
        """A tone that stays inside the band is not an excuse to invent one
        above it: upsampling cannot alias, and the kernel must not ring."""
        source = _tone(1_000.0, 1 << 16)
        got = _resample(source, 0.890899, int(len(source) / 0.890899))
        mono = got[2048:-2048, 0]
        mag = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
        freqs = np.fft.rfftfreq(len(mono), 1 / RATE)
        top = freqs > 16_000
        assert 20 * np.log10(np.max(mag[top]) / np.max(mag)) < -80


class TestHowMuchOfTheMusicSurvives:

    def _noise(self, frames, cutoff):
        """Noise filled to `cutoff` of Nyquist and nothing above it, so the
        kernel's own transition band is not what is being measured."""
        rng = np.random.default_rng(7)
        spectrum = np.fft.rfft(rng.standard_normal((frames, 2)), axis=0)
        keep = int(cutoff * (frames // 2 + 1))
        spectrum[keep:] = 0.0
        out = np.fft.irfft(spectrum, n=frames, axis=0)
        return (0.2 * out / np.max(np.abs(out))).astype(np.float32)

    @pytest.mark.parametrize("semitones", [2, -2])
    def test_the_ideal_resample_is_matched(self, semitones):
        out_n = 1 << 15
        ratio = 2.0 ** (semitones / 12.0)
        in_n = int(round(out_n * ratio))
        # Rational, so the FFT really is the answer and not an opinion.
        ratio = in_n / out_n
        source = self._noise(in_n, 0.8 * min(1.0, 1.0 / ratio))
        ref = _ideal(source, out_n)
        assert _snr_db(_resample(source, ratio, out_n), ref) > 70
        assert _snr_db(_linear(source, ratio, out_n), ref) < 45

    @pytest.mark.parametrize("hz", [220.0, 2_000.0, 8_000.0, 14_000.0])
    def test_a_tone_keeps_its_level_wherever_it_sits(self, hz):
        """Linear is a lowpass filter nobody asked for: measured on real
        music it took 2.2 dB off 12-16 kHz, which is a mix going dull."""
        source = _tone(hz, 1 << 16)
        ratio = 0.890899
        got = _resample(source, ratio, int(len(source) / ratio))
        assert abs(_level_db(got, hz * ratio) - _level_db(source, hz)) < 0.2


class TestTheGainAtDc:
    """Every row of the table sums to one, so the level cannot ripple at
    the fraction's own period -- which would be a tone of its own rather
    than the loss of one."""

    def test_a_constant_stays_constant(self):
        flat = np.full((1 << 14, 1), 0.5, dtype=np.float32)
        got = _resample(flat, 1.122462, 8000)
        inside = got[64:-64]
        assert np.max(np.abs(inside - 0.5)) < 1e-5

    def test_every_tabulated_phase_sums_to_one(self):
        for cutoff in (1.0, 0.890899, 0.5):
            table, offs = timestretch._kernel_table(cutoff)
            assert len(offs) == timestretch.TAPS
            assert np.max(np.abs(table.sum(axis=1) - 1.0)) < 1e-6


class TestNothingHereGrowsWithTheSong:

    def test_the_blocking_cannot_be_seen(self, monkeypatch):
        """The whole song is resampled a block at a time, so a block
        boundary must not be a seam -- and a test that only ever runs one
        block would never find out."""
        source = _tone(440.0, 20_000)
        whole = _resample(source, 1.122462, 15_000)
        monkeypatch.setattr(timestretch, "BLOCK", 97)
        in_pieces = _resample(source, 1.122462, 15_000)
        assert np.array_equal(whole, in_pieces)


class TestTheShapeItWasAskedFor:

    @pytest.mark.parametrize("out_n", [0, 1, 999])
    def test_the_length_is_the_one_asked_for(self, out_n):
        assert len(_resample(_tone(440.0, 4096), 1.3, out_n)) == out_n

    def test_mono_stays_mono_and_stereo_stays_stereo(self):
        mono = _tone(440.0, 4096)[:, 0]
        assert _resample(mono, 1.3, 1000).shape == (1000,)
        stereo = np.repeat(_tone(440.0, 4096), 2, axis=1)
        assert _resample(stereo, 1.3, 1000).shape == (1000, 2)

    def test_an_empty_recording_is_silence_and_not_a_crash(self):
        got = _resample(np.zeros((0, 2), dtype=np.float32), 1.3, 10)
        assert got.shape == (10, 2) and not np.any(got)

    def test_past_the_end_is_silence_and_not_the_last_sample_held(self):
        """The recording really does stop there."""
        source = np.full((2048, 1), 0.5, dtype=np.float32)
        got = _resample(source, 1.0, 2048 + 64)
        assert abs(got[-1, 0]) < 1e-3
        assert abs(got[1024, 0] - 0.5) < 1e-5
