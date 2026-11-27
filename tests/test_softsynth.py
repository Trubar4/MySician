"""The synth inside the app, for the machines that ship no MIDI port.

*"Midi output B und Sh+B gehen nicht."* A port is a wire; Windows ships
something at the other end of it and a clean macOS ships nothing, so both
backings were silent there.

Two halves, tested two ways. The VOICES are arithmetic over numpy, so they
are measured outright -- that the guide really is brighter than the band, that
the drum carries no pitch, that a high note does not alias, that nothing
clips and nothing starts at full amplitude. The MESSAGE handling is driven
through a fake mixer rather than skipped where there is no audio device: a
test that skips itself catches nothing on the machine that matters.
"""

from __future__ import annotations

import numpy as np
import pygame
import pytest

from pickhero.audio import softsynth
from pickhero.audio.softsynth import (
    BAND_VOICE, BASS_VOICE, DRUM_VOICE, GUIDE_VOICE, ROLE_BAND, ROLE_GUIDE,
    SoftSynth, render_wave, role_gain, voice_for,
)

RATE = 44100


def _centroid(wave) -> float:
    """Where the energy sits in the spectrum, in Hz. Brightness, measured."""
    spectrum = np.abs(np.fft.rfft(wave.astype(np.float64)))
    freqs = np.fft.rfftfreq(len(wave), 1.0 / RATE)
    total = spectrum.sum() or 1.0
    return float((spectrum * freqs).sum() / total)


def _dominant_hz(wave) -> float:
    spectrum = np.abs(np.fft.rfft(wave.astype(np.float64)))
    freqs = np.fft.rfftfreq(len(wave), 1.0 / RATE)
    return float(freqs[int(np.argmax(spectrum))])


class TestTheVoicesAreWhatTheyClaim:
    def test_a_note_comes_back_at_the_pitch_it_was_asked_for(self):
        """57 is A3, 220 Hz. The whole thing is worthless if this is wrong."""
        hz = _dominant_hz(render_wave(GUIDE_VOICE, 57, RATE))
        assert abs(hz - 220.0) < 3.0

    def test_the_guide_is_brighter_than_the_band(self):
        """Not a preference: he runs both at once, so the guide has to cut
        through. More harmonics is the whole mechanism."""
        guide = _centroid(render_wave(GUIDE_VOICE, 57, RATE))
        band = _centroid(render_wave(BAND_VOICE, 57, RATE))
        assert guide > band * 1.3

    def test_and_it_sits_louder(self):
        assert role_gain(ROLE_GUIDE) > role_gain(ROLE_BAND) * 1.5

    def test_the_drum_has_no_pitch_at_all(self):
        """A pitched tone on the drum channel is WRONG, not merely thin.

        Noise spreads its energy across the band, so no single bin can hold
        much of it -- where a tone puts most of its energy in one.
        """
        noise = np.abs(np.fft.rfft(render_wave(DRUM_VOICE, 38, RATE)))
        tone = np.abs(np.fft.rfft(render_wave(BAND_VOICE, 38, RATE)))
        assert noise.max() / noise.sum() < 0.02
        assert tone.max() / tone.sum() > 0.05

    def test_nothing_clips(self):
        for voice in (GUIDE_VOICE, BAND_VOICE, BASS_VOICE, DRUM_VOICE):
            wave = render_wave(voice, 45, RATE)
            assert np.max(np.abs(wave)) <= 1.0, voice.name

    def test_nothing_starts_at_full_amplitude(self):
        """A waveform that starts at its peak clicks at its leading edge, and
        on a plucked tone the click is louder than the note."""
        wave = render_wave(GUIDE_VOICE, 57, RATE)
        assert abs(float(wave[0])) < 0.02
        assert np.max(np.abs(wave[:64])) < np.max(np.abs(wave))

    def test_it_decays(self):
        wave = render_wave(GUIDE_VOICE, 57, RATE)
        third = len(wave) // 3
        early = np.max(np.abs(wave[third:2 * third]))
        late = np.max(np.abs(wave[2 * third:]))
        assert late < early * 0.7

    def test_a_high_note_does_not_alias(self):
        """A partial above the Nyquist frequency does not get quieter, it
        comes back somewhere else in the band. The loop has to stop instead.

        Measured at a LOW rate, which is the whole point: the first version
        of this test asked a high note at 44.1 kHz, where six harmonics of
        even a G7 still fit under the Nyquist -- so it passed with the guard
        removed, which makes it a control that cannot contain the thing it
        controls for. This project has now done that nine times.

        A high guide note therefore has fewer harmonics than a low one, and
        that is physics rather than a choice.
        """
        rate, pitch = 8000, 81                 # A5, 880 Hz; Nyquist 4000
        wave = render_wave(GUIDE_VOICE, pitch, rate)
        spectrum = np.abs(np.fft.rfft(wave.astype(np.float64)))
        freqs = np.fft.rfftfreq(len(wave), 1.0 / rate)
        fundamental = softsynth.pitch_hz(pitch)
        strong = freqs[spectrum > spectrum.max() * 0.02]
        # Every peak that is really there is a whole multiple of the
        # fundamental. A folded partial is not: 880 x 6 is 5280, which comes
        # back at 8000 - 5280 = 2720 and is no harmonic of anything.
        for hz in strong:
            nearest = round(hz / fundamental)
            assert abs(hz - nearest * fundamental) < 25.0, hz

    def test_a_low_pitch_is_shorter_at_a_low_rate_and_still_sounds(self):
        """A device that opened at some other rate must not render silence."""
        for rate in (22050, 44100, 48000):
            wave = render_wave(BASS_VOICE, 40, rate)
            assert len(wave) == int(rate * BASS_VOICE.seconds)
            assert np.max(np.abs(wave)) > 0.1


class TestWhichVoiceANoteGets:
    def test_the_drum_channel_is_drums_whoever_asked(self):
        assert voice_for(ROLE_BAND, 9, 0, 38) is DRUM_VOICE
        assert voice_for(ROLE_GUIDE, 9, 0, 38) is DRUM_VOICE

    def test_the_guide_keeps_its_own_voice_whatever_the_instrument(self):
        """A guide written for a bass guitar is still the part being learnt."""
        assert voice_for(ROLE_GUIDE, 0, 33, 40) is GUIDE_VOICE

    def test_a_bass_program_is_a_bass(self):
        assert voice_for(ROLE_BAND, 0, 33, 60) is BASS_VOICE

    def test_and_so_is_a_low_note_with_no_program_at_all(self):
        """A tab's own channel often carries no program change."""
        assert voice_for(ROLE_BAND, 0, None, 38) is BASS_VOICE
        assert voice_for(ROLE_BAND, 0, None, 60) is BAND_VOICE


class _Chan:
    """One mixer channel, as far as this synth is concerned."""

    def __init__(self):
        self.sound = None
        self.volume = None
        self.faded = None

    def play(self, sound):
        self.sound = sound

    def set_volume(self, volume):
        self.volume = volume

    def fadeout(self, ms):
        self.faded = ms
        self.sound = None

    def get_sound(self):
        return self.sound


class _Mixer:
    """Enough of pygame.mixer to drive the message handling anywhere."""

    def __init__(self, count=8):
        self.channels = [_Chan() for _ in range(count)]
        self.made = 0

    def find_channel(self, force=False):
        for channel in self.channels:
            if channel.sound is None:
                return channel
        return self.channels[0] if force else None

    def make_sound(self, samples):
        self.made += 1
        return ("sound", self.made)


@pytest.fixture
def mixer(monkeypatch):
    fake = _Mixer()
    monkeypatch.setattr(softsynth.audio_output, "ensure_mixer", lambda: True)
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: (RATE, -16, 1))
    monkeypatch.setattr(pygame.mixer, "get_num_channels", lambda: 8)
    monkeypatch.setattr(pygame.mixer, "set_num_channels", lambda n: None)
    monkeypatch.setattr(pygame.mixer, "find_channel", fake.find_channel)
    monkeypatch.setattr(pygame.sndarray, "make_sound", fake.make_sound)
    return fake


@pytest.fixture
def synth(mixer):
    return SoftSynth()


def _on(synth, pitch, role=ROLE_BAND, channel=0, velocity=90):
    synth.write_short(0x90 | channel, pitch, velocity, role=role)


def _off(synth, pitch, role=ROLE_BAND, channel=0):
    synth.write_short(0x80 | channel, pitch, 0, role=role)


class TestTheOneMethodItHas:
    def test_a_note_on_plays_something(self, synth, mixer):
        _on(synth, 57)
        assert any(c.sound is not None for c in mixer.channels)

    def test_a_note_off_fades_it(self, synth, mixer):
        _on(synth, 57)
        channel = next(c for c in mixer.channels if c.sound is not None)
        _off(synth, 57)
        assert channel.faded == softsynth.FADE_MS

    def test_a_note_on_at_velocity_zero_IS_a_note_off(self, synth, mixer):
        """Some writers send only that, and a synth that waits for 0x80
        holds those notes for ever."""
        _on(synth, 57)
        channel = next(c for c in mixer.channels if c.sound is not None)
        synth.write_short(0x90, 57, 0, role=ROLE_BAND)
        assert channel.faded == softsynth.FADE_MS

    def test_the_same_pitch_struck_again_replaces_itself(self, synth, mixer):
        """Letting both ring doubles the level; a string does not do that."""
        _on(synth, 57)
        first = next(c for c in mixer.channels if c.sound is not None)
        _on(synth, 57)
        assert first.faded == softsynth.FADE_MS

    def test_velocity_and_the_role_balance_both_reach_the_volume(self, synth,
                                                                 mixer):
        _on(synth, 57, role=ROLE_GUIDE, velocity=100)
        loud = next(c.volume for c in mixer.channels if c.sound is not None)
        for channel in mixer.channels:
            channel.sound = None
        _on(synth, 59, role=ROLE_BAND, velocity=100)
        quiet = next(c.volume for c in mixer.channels if c.sound is not None)
        assert loud > quiet

    def test_one_rendered_note_serves_both_parts(self, synth, mixer):
        """The gain is applied at PLAY time, so the cache cannot hand back a
        note at the other part's volume -- and the band's three voices do not
        each need their own copy of every pitch."""
        _on(synth, 57, role=ROLE_BAND, channel=0)
        _on(synth, 57, role=ROLE_BAND, channel=1)
        assert mixer.made == 1

    def test_an_unknown_message_is_ignored_and_never_raises(self, synth):
        """A synth that threw on a message it did not know would take the
        frame loop down with it."""
        synth.write_short(0xF0, 0, 0)
        synth.write_short(0xB0, 7, 100)              # volume: nothing to set
        synth.write_short(0xB0, softsynth.RESET_CONTROLLERS_CC, 0)

    def test_a_program_change_is_remembered_per_part(self, synth, mixer):
        synth.write_short(0xC0, 33, 0, role=ROLE_BAND)
        _on(synth, 60, role=ROLE_BAND)
        _on(synth, 60, role=ROLE_GUIDE)
        # A bass for the band, the guide's own voice for the guide: two
        # different renders of one pitch.
        assert mixer.made == 2


class TestSilencingTheRightPart:
    def test_a_guide_pause_leaves_the_band_sounding(self, synth, mixer):
        """On a real shared port it cannot: `_silence` sends CC 123 on all
        sixteen channels, so a guide pause takes the band with it."""
        _on(synth, 57, role=ROLE_BAND)
        band = next(c for c in mixer.channels if c.sound is not None)
        _on(synth, 59, role=ROLE_GUIDE)
        synth.port(ROLE_GUIDE).write_short(
            0xB0, softsynth.ALL_NOTES_OFF_CC, 0)
        assert band.faded is None

    def test_but_a_panic_reaches_everything(self, synth, mixer):
        """`panic()` writes to the synth ITSELF rather than to a view, on
        purpose: a player dropped without being closed is exactly the case,
        and getting this the other way round is a Shift+A that silences the
        band and leaves the hum."""
        _on(synth, 57, role=ROLE_BAND)
        _on(synth, 59, role=ROLE_GUIDE)
        sounding = [c for c in mixer.channels if c.sound is not None]
        assert len(sounding) == 2
        synth.write_short(0xB0, softsynth.ALL_SOUND_OFF_CC, 0)
        assert all(c.faded == softsynth.FADE_MS for c in sounding)

    def test_a_stolen_channel_is_not_faded_by_the_wrong_note_off(self,
                                                                 synth,
                                                                 mixer):
        """`find_channel(True)` steals the longest-playing channel, which is
        usually one of ours. Fading it without checking would silence the
        note that took its place -- at high polyphony a note-off would cut a
        note that had only just been struck.
        """
        for pitch in range(40, 48):                  # fill all eight
            _on(synth, pitch)
        stolen = mixer.channels[0]
        _on(synth, 70)                               # steals channel 0
        assert stolen.sound is not None
        _off(synth, 40)                              # the note that was there
        assert stolen.faded is None
        assert stolen.sound is not None


class TestTheRolePort:
    def test_it_tags_what_it_forwards(self, synth, mixer):
        synth.port(ROLE_GUIDE).write_short(0x90, 60, 90)
        assert mixer.made == 1
        for channel in mixer.channels:
            channel.sound = None
        # The same pitch through the band's view is a DIFFERENT voice, which
        # is how we know the tag arrived.
        synth.port(ROLE_BAND).write_short(0x90, 60, 90)
        assert mixer.made == 2

    def test_it_names_its_role(self, synth):
        assert synth.port(ROLE_GUIDE).role == ROLE_GUIDE
