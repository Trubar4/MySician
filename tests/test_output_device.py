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


class TestThePicker:
    """One screen, two kinds, and they must not read each other's setting."""

    def _screen(self, kind, names=("Focusrite", "MacBook Air Speakers")):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.device_menu import DeviceMenuScreen
        pygame.init()
        import pickhero.audio.output as out
        from unittest.mock import patch
        with patch.object(out, "device_names", lambda: list(names)):
            return DeviceMenuScreen(Config(), kind), out

    def test_the_output_picker_lists_sdls_names(self):
        screen, _ = self._screen("output")
        labels = [label for label, _ in screen._items()]
        assert labels == ["System Default", "Focusrite", "MacBook Air Speakers"]

    def test_enter_writes_the_name_and_saves(self, monkeypatch):
        from pickhero.config import Config
        screen, out = self._screen("output")
        saved = []
        monkeypatch.setattr(Config, "save", lambda self: saved.append(1))
        monkeypatch.setattr(out, "set_output_device", lambda name: "")
        screen._selected = 1
        screen._apply_selection()
        assert screen._config.audio.output_device == "Focusrite"
        assert saved

    def test_the_default_row_clears_it(self, monkeypatch):
        from pickhero.config import Config
        screen, out = self._screen("output")
        monkeypatch.setattr(Config, "save", lambda self: None)
        monkeypatch.setattr(out, "set_output_device", lambda name: "")
        screen._config.audio.output_device = "Focusrite"
        screen._selected = 0
        screen._apply_selection()
        assert screen._config.audio.output_device == ""

    def test_it_reopens_at_once_rather_than_at_the_next_restart(self, monkeypatch):
        """A setting that takes effect later cannot be seen working."""
        from pickhero.config import Config
        screen, out = self._screen("output")
        monkeypatch.setattr(Config, "save", lambda self: None)
        asked = []
        monkeypatch.setattr(out, "set_output_device",
                            lambda name: asked.append(name) or "")
        screen._selected = 2
        screen._apply_selection()
        assert asked == ["MacBook Air Speakers"]

    def test_the_cursor_opens_on_the_one_in_force(self):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.device_menu import DeviceMenuScreen
        from unittest.mock import patch
        import pickhero.audio.output as out
        pygame.init()
        config = Config()
        config.audio.output_device = "MacBook Air Speakers"
        with patch.object(out, "device_names",
                          lambda: ["Focusrite", "MacBook Air Speakers"]):
            screen = DeviceMenuScreen(config, "output")
        assert screen._selected == 2
        assert screen._items()[2][1] is True

    def test_the_input_half_is_untouched(self, monkeypatch):
        """It writes an INDEX and never the output name."""
        import pygame
        from pickhero.config import Config
        from pickhero.ui.device_menu import DeviceMenuScreen
        from unittest.mock import patch
        import pickhero.ui.device_menu as dm
        pygame.init()
        monkeypatch.setattr(Config, "save", lambda self: None)
        with patch.object(dm, "list_audio_devices", lambda: [
                {"index": 7, "name": "Scarlett Solo", "host_api": "MME",
                 "sample_rate": 44100.0}]):
            screen = DeviceMenuScreen(Config(), "input")
            screen._selected = 1
            screen._apply_selection()
        assert screen._config.audio.device_index == 7
        assert screen._config.audio.output_device == ""

    def test_an_unknown_kind_is_the_input(self):
        """A screen that refuses to draw is worse than one that draws."""
        import pygame
        from pickhero.config import Config
        from pickhero.ui.device_menu import DeviceMenuScreen
        pygame.init()
        assert DeviceMenuScreen(Config(), "sideways")._kind == "input"

    def test_both_kinds_really_draw(self):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.device_menu import DeviceMenuScreen
        pygame.init()
        surface = pygame.Surface((1280, 720))
        for kind in ("input", "output"):
            DeviceMenuScreen(Config(), kind).render(surface)


class TestFallingBackIsSaidOutLoud:
    """A device that will not open must never come out as silence."""

    def test_the_mixer_falls_back_to_the_system_default(self, monkeypatch):
        import pygame
        tried = []
        state = {"open": False}

        def init(*args, **kwargs):
            tried.append(kwargs.get("devicename"))
            if kwargs.get("devicename"):
                raise pygame.error("No such device")
            state["open"] = True

        monkeypatch.setattr(pygame.mixer, "get_init",
                            lambda: (44100, -16, 2) if state["open"] else None)
        monkeypatch.setattr(pygame.mixer, "pre_init", lambda *a, **k: None)
        monkeypatch.setattr(pygame.mixer, "init", init)
        monkeypatch.setattr(output, "_wanted", "Focusrite")
        monkeypatch.setattr(output, "_opened", "")
        assert output.ensure_mixer() is True
        assert tried == ["Focusrite", None]
        assert output.fallback_reason() == "Focusrite"

    def test_and_a_device_that_opens_is_named(self, monkeypatch):
        import pygame
        state = {"open": False}
        monkeypatch.setattr(pygame.mixer, "get_init",
                            lambda: (44100, -16, 2) if state["open"] else None)
        monkeypatch.setattr(pygame.mixer, "pre_init", lambda *a, **k: None)
        monkeypatch.setattr(pygame.mixer, "init",
                            lambda *a, **k: state.update(open=True))
        monkeypatch.setattr(output, "_wanted", "Focusrite")
        monkeypatch.setattr(output, "_opened", "")
        monkeypatch.setattr(output, "_fell_back", "Focusrite")
        assert output.ensure_mixer() is True
        assert output.open_device() == "Focusrite"
        assert output.fallback_reason() == ""

    def test_the_settings_row_says_it_refused(self, monkeypatch):
        """On the row, not in a note -- a note expires and this does not."""
        import pygame
        from pickhero.config import Config
        from pickhero.ui.settings_menu import SettingsMenuScreen
        pygame.init()
        config = Config()
        config.audio.output_device = "Focusrite"
        monkeypatch.setattr(output, "_fell_back", "Focusrite")
        screen = SettingsMenuScreen(config, None)
        row = next(r for r in screen._rows if r.key == "output")
        assert "would not open" in row.value()

    def test_and_names_it_plainly_when_it_opened(self, monkeypatch):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.settings_menu import SettingsMenuScreen
        pygame.init()
        config = Config()
        config.audio.output_device = "Focusrite"
        monkeypatch.setattr(output, "_fell_back", "")
        screen = SettingsMenuScreen(config, None)
        row = next(r for r in screen._rows if r.key == "output")
        assert row.value() == "Focusrite"
        assert row.is_default() is False

    def test_the_default_is_the_default(self):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.settings_menu import SettingsMenuScreen
        pygame.init()
        screen = SettingsMenuScreen(Config(), None)
        row = next(r for r in screen._rows if r.key == "output")
        assert row.value() == "system default"
        assert row.is_default() is True


class TestItIsReallyWiredUp:
    """A picker nothing opens is a feature that ships doing nothing."""

    def test_the_row_opens_the_output_picker(self):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.settings_menu import SettingsMenuScreen
        pygame.init()
        screen = SettingsMenuScreen(Config(), None)
        screen._selected = next(i for i, r in enumerate(screen._rows)
                                if r.key == "output")
        event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0,
                                   unicode="\r")
        assert screen.handle_event(event) == "output"

    def test_the_app_opens_it_as_the_output_kind(self, monkeypatch):
        import pygame
        from pickhero.config import Config
        from pickhero.ui.app import App
        pygame.init()
        app = App.__new__(App)
        app._config = Config()
        app._device_menu = None
        app._return_to = "menu"
        app._state = "settings"
        app._open_device_menu("settings", "output")
        assert app._device_menu._kind == "output"
        app._open_device_menu("settings")
        assert app._device_menu._kind == "input"

    def test_a_stored_name_is_honoured_before_anything_opens(self, monkeypatch):
        """At startup there is nothing to validate against, so it is stored."""
        import pygame
        monkeypatch.setattr(pygame.mixer, "get_init", lambda: None)
        monkeypatch.setattr(output, "_wanted", "")
        assert output.set_output_device("Focusrite") == ""
        assert output.wanted_device() == "Focusrite"

    def test_the_name_stays_on_this_machine(self):
        """An output name describes an interface, not a player.

        `song_settings()` returns only the `song_*` fields, so the whole
        audio block is machine-local by construction and a new name in it
        needs no rule of its own -- which is the half worth asserting.
        """
        from pickhero import transfer
        assert not any(f.startswith("audio") for f in transfer.song_settings())


class TestTheRateIsAskedForAndNotNegotiated:
    """The device's own rate is accepted, and the measurement says why.

    Both of the arguments this switch was flipped on before were false, and
    the player reported bad sound on BOTH of its positions -- which is the
    tell that it is not the cause. `allowedchanges=0` hands back the spec
    that was asked for and converts INSIDE SDL, so SDL resamples the
    recording either way and the OS converts in neither case. And SDL's
    resampler is not linear: 57.7 dB SNR measured through `sndarray` on a
    44.1 kHz file in a 48 kHz mixer, against 41.2 for linear interpolation
    and 106.9 for our own windowed sinc.

    What it does decide is the app's OWN audio: the synth and the click are
    rendered at the mixer rate, so a pinned rate has SDL resample them for
    nothing. Hence True.
    """

    def _asked(self, monkeypatch):
        import pygame
        seen = {}
        monkeypatch.setattr(pygame.mixer, "get_init",
                            lambda: (44100, -16, 2))
        monkeypatch.setattr(pygame.mixer, "pre_init", lambda *a, **k: None)
        monkeypatch.setattr(pygame.mixer, "init",
                            lambda *a, **k: seen.update(k))
        output._init("")
        return seen

    def test_the_device_rate_is_accepted(self, monkeypatch):
        import pygame
        assert output.ALLOW_RATE_CHANGE is True
        asked = self._asked(monkeypatch)
        assert asked.get("allowedchanges") == pygame.AUDIO_ALLOW_FREQUENCY_CHANGE

    def test_and_the_switch_still_works(self, monkeypatch):
        """Pinned, it is the behaviour that shipped for three days."""
        monkeypatch.setattr(output, "ALLOW_RATE_CHANGE", False)
        assert self._asked(monkeypatch).get("allowedchanges") == 0

    def test_the_format_and_the_channels_are_never_negotiable(self, monkeypatch):
        """`pygame.sndarray` reads what `_decode` hands the time-stretch, so
        an 8-bit or mono device would change what that means."""
        import pygame
        asked = self._asked(monkeypatch)
        allowed = asked.get("allowedchanges", 0)
        assert not allowed & pygame.AUDIO_ALLOW_FORMAT_CHANGE
        assert not allowed & pygame.AUDIO_ALLOW_CHANNELS_CHANGE

    def test_everything_downstream_reads_the_rate_rather_than_assuming_it(self):
        """A device at 48 kHz is only safe because nothing hardcodes 44100.

        The stretch writes the mixer's rate into the WAV it builds and the
        chroma reads it back; a constant in either would play a stretched
        copy 8.8 % flat, which is the fault the synth had one layer down.
        """
        import inspect
        from pickhero.audio import timestretch, autosync
        for module in (timestretch, autosync):
            source = inspect.getsource(module)
            assert "pygame.mixer.get_init()[0]" in source


class TestTheMixerIsTheONEThisModuleOpened:
    """`pygame.init()` opens the mixer itself, and that made two features inert.

    It initialises every pygame module, the mixer among them, with pygame's
    own defaults -- the system default device and a 512-frame buffer -- and
    `ensure_mixer` then returns early for the rest of the session. So the
    stored output name did nothing until `Shift+A` reopened it, the 2048
    frames `output.py` exists to ask for were never in force, and
    `describe()` printed a buffer the mixer did not have.
    """

    def test_pygame_init_really_does_open_it(self):
        """The premise. A pygame that stopped doing this would leave the
        call in `App.run` dead rather than wrong, and nothing would say so."""
        import pygame
        pygame.mixer.quit()
        pygame.quit()
        assert pygame.mixer.get_init() is None
        pygame.init()
        assert pygame.mixer.get_init() is not None

    def test_the_app_opens_it_before_pygame_init(self, monkeypatch):
        """Driven through the real `App.run`, because the fault was an
        ORDER and a test that calls the helper cannot see a caller that
        calls it too late."""
        import pygame
        from pickhero.config import Config
        from pickhero.ui import app as app_module

        order = []

        class Stop(Exception):
            pass

        monkeypatch.setattr(app_module.audio_output, "ensure_mixer",
                            lambda: order.append("mixer") or True)

        def stop_here():
            order.append("pygame")
            raise Stop

        monkeypatch.setattr(pygame, "init", stop_here)
        app = app_module.App.__new__(app_module.App)
        app._config = Config()
        try:
            app.run()
        except Stop:
            pass
        assert order == ["mixer", "pygame"]

    def test_a_mixer_it_did_not_open_claims_no_buffer(self, monkeypatch):
        """SDL does not report the buffer it got, so the constant may only
        be named for a stream this module opened."""
        import pygame
        monkeypatch.setattr(pygame.mixer, "get_init", lambda: (48000, -16, 2))
        monkeypatch.setattr(output, "_ours", False)
        monkeypatch.setattr(output, "_opened", "")
        assert "buffer unknown" in output.describe()
        assert "frame buffer" not in output.describe()

    def test_and_one_it_did_open_says_what_it_asked_for(self, monkeypatch):
        import pygame
        monkeypatch.setattr(pygame.mixer, "get_init", lambda: (44100, -16, 2))
        monkeypatch.setattr(output, "_ours", True)
        monkeypatch.setattr(output, "_opened", "")
        assert f"{output.BUFFER} frame buffer" in output.describe()

    def test_opening_it_here_marks_it_as_ours(self, monkeypatch):
        state = {"open": False}
        import pygame
        monkeypatch.setattr(pygame.mixer, "get_init",
                            lambda: (44100, -16, 2) if state["open"] else None)
        monkeypatch.setattr(pygame.mixer, "pre_init", lambda *a, **k: None)
        monkeypatch.setattr(pygame.mixer, "init",
                            lambda *a, **k: state.update(open=True))
        monkeypatch.setattr(output, "_ours", False)
        monkeypatch.setattr(output, "_wanted", "")
        assert output.ensure_mixer()
        assert output._ours is True

    def test_a_reopen_that_fails_does_not_claim_the_buffer(self, monkeypatch):
        """Where no device comes up at all, nothing was asked for either."""
        import pygame
        monkeypatch.setattr(pygame.mixer, "get_init", lambda: None)
        monkeypatch.setattr(pygame.mixer, "pre_init", lambda *a, **k: None)

        def no(*a, **k):
            raise pygame.error("no such device")

        monkeypatch.setattr(pygame.mixer, "init", no)
        monkeypatch.setattr(output, "_ours", True)
        monkeypatch.setattr(output, "_wanted", "Focusrite")
        assert not output.ensure_mixer()
        assert output._ours is False
