"""The output device: what it names, and what it must not leave behind.

*"Ueber Focusrite kommt kein MP3 mehr auf dem Mac. Wenn ich umstelle, kommt
der Sound aus den NB Speakern. Wenn ich etwas ausserhalb der App laufen
lasse, kommt es ueber Focusrite. Mit D habe ich das richtige Interface
ausgewaehlt."*

`D` is the INPUT picker, and the app chooses no output at all: SDL takes the
system default when the mixer first opens and holds it for the session. So
the fault could not be narrowed from a run log, which named the stream's rate
and buffer and not one word about WHICH device -- the same gap the input
device was fixed for, where a log that does not name the device cannot tell a
wrong one from a blocked one.

Reading that path turned up a fault of its own, which is what the second
class here is about.
"""

from __future__ import annotations

import os
from pathlib import Path

from pickhero.audio import autosync, output


class TestTheOutputDevicesAreNamed:
    def test_the_names_are_strings(self):
        for name in output.device_names():
            assert isinstance(name, str)

    def test_it_says_so_rather_than_raising_without_sdl2(self, monkeypatch):
        """An older pygame must not be why a run log fails to write."""
        import builtins
        real = builtins.__import__

        def no_sdl2(name, *args, **kwargs):
            if name.startswith("pygame._sdl2"):
                raise ImportError("no _sdl2 here")
            return real(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_sdl2)
        assert output.device_names() == []
        assert output.describe_devices() == "(SDL named none)"

    def test_none_named_is_said_rather_than_left_blank(self, monkeypatch):
        monkeypatch.setattr(output, "device_names", lambda: [])
        assert output.describe_devices() == "(SDL named none)"

    def test_a_long_list_is_capped_and_counted(self, monkeypatch):
        """A Windows box lists the same interface several times over."""
        monkeypatch.setattr(output, "device_names",
                            lambda: [f"dev {i}" for i in range(9)])
        line = output.describe_devices()
        assert line.count(",") == output.MAX_NAMED_DEVICES - 1
        assert f"+{9 - output.MAX_NAMED_DEVICES} more" in line

    def test_the_stream_and_the_devices_are_said_APART(self, monkeypatch):
        """Two faults, two lines.

        A rate the device chose is a conversion; a device SDL cannot see is a
        driver. Together they are also one line too wide for the status note
        `Shift+A` writes, and that is the place the answer has to be read.
        """
        monkeypatch.setattr(output, "device_names",
                            lambda: ["Scarlett Solo USB"])
        assert "Scarlett" not in output.describe()
        assert "Scarlett" in output.describe_devices()


class TestTheRunLogCarriesIt:
    """A field nothing writes is a feature that ships doing nothing."""

    def test_the_log_names_the_devices_on_their_own_line(self, monkeypatch):
        import io

        from pickhero.config import Config
        from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline
        from pickhero.ui.scrolling import PlayingScreen

        monkeypatch.setattr(output, "device_names",
                            lambda: ["Scarlett Solo USB"])
        timeline = Timeline(
            notes=[NoteEvent(timestamp_ms=0.0, midi_note=40, string=6,
                             fret=0, duration_ms=500.0)],
            metadata=SongMetadata(title="t", artist="a", tempo=120.0,
                                  tuning=[40, 45, 50, 55, 59, 64]))
        from pickhero.matcher import NoteMatcher
        screen = PlayingScreen(timeline, config=Config())
        screen._song_key = "test.gp5"
        screen._matcher = NoteMatcher(timeline, timing_window_ms=150.0)
        buffer = io.StringIO()
        screen._write_run_log(buffer)
        fields = dict(line.split("\t", 1)
                      for line in buffer.getvalue().splitlines()
                      if "\t" in line)
        assert fields["output_devices"] == "Scarlett Solo USB"
        # Apart, not folded into the stream's own line.
        assert "Scarlett" not in fields["output_device"]


class TestTheDecodeDoesNotSilenceTheWholeSession:
    """`SDL_AUDIODRIVER` is read from the environment at EVERY mixer.init().

    Measured: open the mixer with it set to `dummy`, quit, change it, and the
    next init opens the other driver. So `decode`, which sets it to `dummy`
    so that reading a file does not grab the speakers, used to leave the
    whole PROCESS on the dummy driver -- every backing track, every click and
    every recording silent from then on, with nothing anywhere to say why.
    """

    def test_it_is_put_back_when_it_was_not_set(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SDL_AUDIODRIVER", raising=False)
        self._decode_something(tmp_path)
        assert "SDL_AUDIODRIVER" not in os.environ

    def test_a_driver_the_player_named_is_left_alone(self, monkeypatch,
                                                     tmp_path):
        monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
        self._decode_something(tmp_path)
        assert os.environ["SDL_AUDIODRIVER"] == "dummy"

    def test_and_the_decode_still_works(self, tmp_path):
        samples, rate = self._decode_something(tmp_path)
        assert rate > 0
        assert len(samples) > 0

    @staticmethod
    def _decode_something(tmp_path: Path):
        import wave
        import numpy as np
        path = tmp_path / "tone.wav"
        n = 4410
        data = (np.sin(np.arange(n) * 0.1) * 8000).astype("<i2")
        with wave.open(str(path), "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(44100)
            fh.writeframes(data.tobytes())
        import pygame
        was_open = bool(pygame.mixer.get_init())
        if was_open:
            pygame.mixer.quit()
        try:
            return autosync.decode(path)
        finally:
            if was_open:
                output.ensure_mixer()
