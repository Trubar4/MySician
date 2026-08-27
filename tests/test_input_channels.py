"""Which input the capture listens to when the guitar is in the second one."""
import numpy as np, pytest
from pickhero.audio.input import AudioCapture
from pickhero.config import Config


def _capture():
    cap = AudioCapture(Config())
    cap._sample_rate = 44100
    from pickhero.audio.input import _AudioRing
    cap._ring = _AudioRing(44100)
    cap._pending_windows = []
    cap._channel_energy = None
    return cap


class TestTheGuitarIsInOneInputOfTwo:
    def _buffer(self, loud_channel, frames=512):
        data = np.zeros((frames, 2), dtype="float32")
        t = np.arange(frames) / 44100.0
        data[:, loud_channel] = (0.5 * np.sin(2 * np.pi * 110 * t)).astype("float32")
        return data

    def test_the_loud_channel_is_the_one_that_is_heard(self):
        cap = _capture()
        for _ in range(20):
            cap._audio_callback(self._buffer(1), 512, None, None)
        heard = cap._ring.read(cap._ring.written - 512, 512)
        assert float(np.sqrt(np.mean(heard ** 2))) > 0.2

    def test_nothing_is_given_away_by_averaging(self):
        """The mean would halve it -- 6 dB, where the pitch starts rotting
        below -38 dB and collapses under -44."""
        cap = _capture()
        for _ in range(20):
            cap._audio_callback(self._buffer(0), 512, None, None)
        mono = _capture()
        frames = 512
        t = np.arange(frames) / 44100.0
        one = np.zeros((frames, 1), dtype="float32")
        one[:, 0] = (0.5 * np.sin(2 * np.pi * 110 * t)).astype("float32")
        for _ in range(20):
            mono._audio_callback(one, 512, None, None)
        stereo_rms = float(np.sqrt(np.mean(cap._ring.read(cap._ring.written - 512, 512) ** 2)))
        mono_rms = float(np.sqrt(np.mean(mono._ring.read(mono._ring.written - 512, 512) ** 2)))
        assert stereo_rms == pytest.approx(mono_rms, rel=0.02)
