"""A synth inside the app, for the machines that ship no MIDI port.

*"Midi output B und Sh+B gehen nicht."* -- on a MacBook Air, and it is not a
bug in this app. `B` and `Shift+B` send MIDI: note numbers and instrument
numbers down a port. **A port is a wire**, and something at the other end has
to turn "note 64, instrument 30" into a sound. Windows ships that something
(the Microsoft GS Wavetable Synth, which is why those keys have worked there
since the first day); **a clean macOS ships none at all** -- CoreMIDI lists a
destination only where hardware or an application provides one, and Audio MIDI
Setup's IAC bus is off by default and is itself a wire rather than a synth. So
there was no destination to open and both backings were silent.

**This is not the consolation prize it first looks like, and the player's own
answer is what settled that.** *"Ich benutze B und Shift+B, wenn ich etwas
langsam uebe. In diesem Fall ist das MP3 kaum zu gebrauchen."*

| at 60 % practice speed | |
|---|---|
| the recording | a WSOLA copy: the level wobbles about 1 dB, the top of the band is 3-4 dB down, and he reports it as barely usable |
| **MIDI** | told a position, plays the events at it -- **exact at every speed, nothing is stretched** |

So at the speed these keys are actually used the MIDI path is the better one,
and an install-free synth makes it reachable on every machine instead of on
the one platform that happens to bundle a wavetable.

**The seam is one method.** `MidiPlayer`'s entire output surface is
`write_short(status, data1, data2)`, so an object with that method drops in
where the port is opened and `BackingTrack`, `MidiPlayer`, `_midi_all()`,
every transport call, `play_click`, `_silence` and the `Shift+A` panic path
are all untouched. This is a second kind of output, not a second code path
through the app.

Four decisions, and each is a refusal as much as a choice:

- **The real port wins wherever there is one.** The GS Wavetable Synth sounds
  better than anything here, so Windows is bit-for-bit what it was and this
  only ever fills a hole. The cost is named rather than hidden: the same key
  sounds different on the player's two laptops.
- **The guide sits in front and the band sits back**, because he runs both at
  once (*"sehr wohl B und Sh+B"*) and the guide is the part he is trying to
  hear himself against. One fixed balance rather than two volume rows: a
  setting that ends up at zero looks exactly like a broken feature.
- **Percussion is a noise burst, not a pitch.** A simple synth cannot fake a
  kit -- but a pitched tone on the drum channel is *wrong* rather than merely
  thin, and channel 9 is drums by convention in every file this app reads.
- **A handful of voices, not General MIDI.** 128 sampled instruments are the
  150 MB soundfont this app does not bundle, and a tone whose brightness
  follows the written instrument's family is the most that can honestly be
  derived from a program number alone.

The arithmetic is kept apart from pygame (`render_wave`) so the voices can be
tested on a machine with no audio device, which is where this was written.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pickhero.audio import output as audio_output


#: How loud each role is. The guide is the part the player is trying to hear
#: themselves against, so it is in front; the band is context.
GUIDE_GAIN = 0.55
BAND_GAIN = 0.22

#: A rendered note is this long and is then simply over. Note-off cuts it
#: early (see `FADE_MS`), which is what usually happens -- this is the cap
#: for a note nothing cuts, and every millisecond of it is cache memory.
NOTE_SECONDS = 1.2
BASS_SECONDS = 1.8
PERC_SECONDS = 0.09

#: Note-off is a fade rather than a stop. A hard stop clicks; this is short
#: enough to be the note ending rather than a release of its own.
FADE_MS = 60

#: A ramp onto the front of every note, for the same reason: a waveform that
#: starts at full amplitude clicks at its leading edge, and on a plucked tone
#: that click is louder than the note.
ATTACK_MS = 4.0

#: Cleared wholesale rather than evicted one at a time, the rule the font and
#: note-head caches already follow: a re-render is a millisecond and an LRU
#: here would be bookkeeping to save nothing. A melodic note is 0.21 MB of
#: stereo and a drum 0.016 MB, so this is a ceiling of about 27 MB -- well
#: above the 40-80 distinct (voice, pitch) pairs a real song reaches.
MAX_SOUNDS = 128

#: A six-string band chord plus a six-string guide chord is twelve voices
#: before a single decay tail overlaps, and pygame's default is eight. Only
#: ever raised: lowering it would take channels away from the click and the
#: recording.
VOICES_WANTED = 32

#: General MIDI puts the basses at 32-39. Below this pitch a note is treated
#: as a bass whatever the program says, because a tab's own channel often
#: carries no program change at all.
BASS_PROGRAMS = range(32, 40)
BASS_PITCH = 45

#: MIDI's drum channel, by convention in every format this app reads.
PERCUSSION_CHANNEL = 9

ROLE_GUIDE = "guide"
ROLE_BAND = "band"

# MIDI status bytes this understands. Anything else is ignored rather than
# refused: a synth that threw on an unknown message would take the frame
# loop down with it.
NOTE_ON = 0x90
NOTE_OFF = 0x80
PROGRAM_CHANGE = 0xC0
CONTROL_CHANGE = 0xB0

ALL_SOUND_OFF_CC = 120
ALL_NOTES_OFF_CC = 123
RESET_CONTROLLERS_CC = 121


@dataclass(frozen=True)
class Voice:
    """One timbre: the amplitude of each harmonic, and how fast it dies.

    `harmonics[0]` is the fundamental. More of them is a brighter, more
    cutting tone, which is what the guide needs in order to be heard through
    the band -- so the two roles differ in the length of this tuple as much
    as in their gain.
    """

    name: str
    harmonics: tuple[float, ...]
    decay_s: float
    seconds: float
    noise: bool = False


#: Bright and sustained: the player has to pick this one out from under the
#: band and match it.
GUIDE_VOICE = Voice("guide", (1.0, 0.50, 0.33, 0.22, 0.14, 0.09),
                    0.55, NOTE_SECONDS)
#: Duller on purpose. The band is context, and a band as bright as the guide
#: is a band competing with it.
BAND_VOICE = Voice("band", (1.0, 0.35, 0.15), 0.45, NOTE_SECONDS)
#: Longer, and almost all fundamental -- the harmonics of a bass line sit
#: exactly where the guitar part is.
BASS_VOICE = Voice("bass", (1.0, 0.30, 0.08), 0.90, BASS_SECONDS)
#: No pitch at all. See the module docstring: a pitched tone on the drum
#: channel is wrong rather than thin.
DRUM_VOICE = Voice("drum", (), 0.030, PERC_SECONDS, noise=True)


def voice_for(role: str, channel: int, program: int | None,
              pitch: int) -> Voice:
    """Which timbre a note gets.

    The role decides first and the written instrument second, because what
    this has to get right is the guide standing out -- a guide written for a
    bass guitar is still the part being learnt.
    """
    if channel == PERCUSSION_CHANNEL:
        return DRUM_VOICE
    if role == ROLE_GUIDE:
        return GUIDE_VOICE
    if program is not None and program in BASS_PROGRAMS:
        return BASS_VOICE
    if pitch < BASS_PITCH:
        return BASS_VOICE
    return BAND_VOICE


def pitch_hz(pitch: int) -> float:
    """A MIDI note number as a frequency. A4 = 440 Hz, as everywhere here."""
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0))


def render_wave(voice: Voice, pitch: int, rate: int):
    """One note as float samples in [-1, 1], before any gain.

    Kept apart from pygame so the voices can be measured without an audio
    device: that the guide really is brighter than the band, that the drum
    carries no pitch, that nothing clips and that nothing starts at full
    amplitude are all properties of this array.
    """
    import numpy as np

    rate = int(rate) if rate else int(audio_output.RATE)
    n = max(1, int(rate * voice.seconds))
    t = np.arange(n, dtype=np.float32) / float(rate)
    if voice.noise:
        # A fixed seed, because a drum that sounds different on every strike
        # is a drum nobody can tell from a glitch -- and because the cache
        # would otherwise hand out whichever one it built first anyway.
        rng = np.random.default_rng(pitch)
        wave = rng.standard_normal(n).astype(np.float32)
    else:
        wave = np.zeros(n, dtype=np.float32)
        hz = pitch_hz(pitch)
        for index, amplitude in enumerate(voice.harmonics, start=1):
            partial = hz * index
            if partial >= rate / 2.0:
                # Above the Nyquist frequency a partial does not get quieter,
                # it comes back somewhere else in the band -- which is the
                # aliasing the resampler chapter had to measure out of the
                # pitch shift. A high guide note has fewer harmonics for
                # this reason and not by choice.
                break
            wave += amplitude * np.sin(2.0 * np.pi * partial * t,
                                       dtype=np.float32)
    # Normalised OUTSIDE the branch, which is where the first version had it
    # wrong: `standard_normal` reaches about four sigma, and `astype(np.int16)`
    # does not clamp, it WRAPS -- so an over-full drum sample comes back with
    # its sign inverted rather than merely loud. The test that caught it asks
    # only that nothing exceeds 1.0.
    peak = float(np.max(np.abs(wave))) or 1.0
    wave /= peak
    wave *= np.exp(-t / max(voice.decay_s, 1e-4))
    attack = max(1, int(rate * ATTACK_MS / 1000.0))
    if attack < n:
        wave[:attack] *= np.linspace(0.0, 1.0, attack, dtype=np.float32)
    return wave


def role_gain(role: str) -> float:
    """How loud this role sits. One fixed balance; see the docstring."""
    return GUIDE_GAIN if role == ROLE_GUIDE else BAND_GAIN


class _RolePort:
    """What one `MidiPlayer` is handed: the shared synth, tagged.

    The guide and the band are built by the same extraction with the track
    filter inverted, so the guide's notes arrive on the CHANNEL the band
    would have used -- which means the channel number cannot say which is
    which and the role has to be carried by the object instead.

    It is a view rather than a synth of its own so that `panic()` still
    reaches everything with one call, and so that a guide pause silences the
    guide alone. On a real port it cannot: `_silence` sends CC 123 on all
    sixteen channels, which on one shared device stops the band's notes too.
    """

    def __init__(self, synth: "SoftSynth", role: str):
        self._synth = synth
        self._role = role

    def write_short(self, status: int, data1: int, data2: int = 0) -> None:
        self._synth.write_short(status, data1, data2, role=self._role)

    def warm(self, track) -> int:
        """Pre-render this part's notes. Tagged, because the voice depends
        on which part it is -- the guide's notes all get the guide voice."""
        return self._synth.warm(track, role=self._role)

    @property
    def role(self) -> str:
        return self._role


class SoftSynth:
    """Renders MIDI notes through the mixer. `write_short` and nothing else.

    One instance per process, held where the port would be, so the run log,
    `output_name()` and the panic path all find it where they already look.
    """

    def __init__(self):
        import pygame

        self._rate = int(audio_output.RATE)
        self._channels = int(audio_output.CHANNELS)
        init = pygame.mixer.get_init()
        if init:
            self._rate, self._channels = int(init[0]), int(init[2])
        # Raised, never lowered: the click and the recording take channels
        # from the same pool.
        try:
            if pygame.mixer.get_num_channels() < VOICES_WANTED:
                pygame.mixer.set_num_channels(VOICES_WANTED)
        except pygame.error:
            pass
        self._sounds: dict[tuple[str, int], object] = {}
        #: (role, channel, pitch) -> the mixer channel it is sounding on, so
        #: a note-off reaches the one voice it belongs to and a repeated note
        #: replaces itself instead of stacking.
        self._playing: dict[tuple[str, int, int], tuple] = {}
        self._programs: dict[tuple[str, int], int] = {}

    @property
    def name(self) -> str:
        """For the run log. It has to say that no MIDI device was involved."""
        return f"built-in synth ({self._rate} Hz, no MIDI device)"

    def port(self, role: str) -> _RolePort:
        """A tagged view for one player."""
        return _RolePort(self, role)

    # -- the whole output surface ------------------------------------------

    def write_short(self, status: int, data1: int, data2: int = 0,
                    role: str | None = None) -> None:
        """One MIDI message. Anything unrecognised is ignored, not refused.

        `role` is None for a message sent to the synth ITSELF rather than
        through a player's view -- which is what `panic()` does, reaching the
        shared object on purpose so that a player dropped without being
        closed cannot leave anything sounding. So an all-notes-off with no
        role stops EVERY part, where one through a view stops that part
        alone. Getting this the other way round is a `Shift+A` that silences
        the band and leaves the hum.
        """
        kind = status & 0xF0
        channel = status & 0x0F
        if kind == NOTE_ON and data2 > 0:
            self._note_on(role or ROLE_BAND, channel, int(data1), int(data2))
        elif kind == NOTE_OFF or (kind == NOTE_ON and data2 == 0):
            # A note-on at velocity 0 IS a note-off; some writers send only
            # that, and a synth that waits for 0x80 holds those notes for ever.
            self._note_off(role or ROLE_BAND, channel, int(data1))
        elif kind == PROGRAM_CHANGE:
            self._programs[(role or ROLE_BAND, channel)] = int(data1)
        elif kind == CONTROL_CHANGE and data1 in (
                ALL_SOUND_OFF_CC, ALL_NOTES_OFF_CC):
            self.all_off(role=role)
        # RESET_CONTROLLERS_CC has nothing to reset here: there is no volume,
        # pan or sustain to put back. Ignored rather than special-cased.

    def warm(self, track, role: str = ROLE_BAND) -> int:
        """Render every note this track will ask for, before it asks.

        Measured rather than assumed, which is why this exists: a warm
        twelve-voice chord costs **0.021 ms** and a cold one **6.95 ms of a
        16.7 ms frame** -- half a frame, on exactly the frames where new
        pitches first appear, which in a song is the opening bar of every
        section. That is the loop this display has had to move out of the
        frame four times already (the chord blocks, the note heads, the tab
        page, the strip).

        The track knows every pitch and every program change, so the set is
        exact: nothing is rendered that the song does not play. Stops at
        `MAX_SOUNDS` rather than clearing the cache it is in the middle of
        filling -- past that the rest is rendered on demand, which is the
        behaviour this replaces rather than a failure.

        Returns how many it built, for the measurement.
        """
        # Before the `key in self._sounds` check below, or a cache rendered
        # at the old rate would be believed for the notes it already holds.
        self._follow_the_mixer()
        programs: dict[int, int] = {}
        wanted: list[tuple[Voice, int]] = []
        seen: set[tuple[str, int]] = set()
        for event in track.events:
            if event.event_type == PROGRAM_CHANGE:
                programs[event.channel] = int(event.data1)
                continue
            if event.event_type != NOTE_ON or event.data2 <= 0:
                continue
            pitch = int(event.data1)
            voice = voice_for(role, event.channel,
                              programs.get(event.channel), pitch)
            key = (voice.name, pitch)
            if key in seen or key in self._sounds:
                continue
            seen.add(key)
            wanted.append((voice, pitch))
        built = 0
        for voice, pitch in wanted:
            if len(self._sounds) >= MAX_SOUNDS:
                break
            if self._sound_for(voice, pitch) is not None:
                built += 1
        return built

    def all_off(self, role: str | None = None) -> None:
        """Stop sounding. One role, or everything when none is named.

        `panic()` reaches the synth itself rather than a view, which is what
        makes a hum survive nothing -- and a guide pause reaches its own view,
        which is what stops it taking the band with it.
        """
        for key in [k for k in self._playing if role is None or k[0] == role]:
            self._stop(key)

    def close(self) -> None:
        self.all_off()
        self._sounds.clear()

    # -- the mixer ---------------------------------------------------------

    def _note_on(self, role: str, channel: int, pitch: int,
                 velocity: int) -> None:
        import pygame

        key = (role, channel, pitch)
        if key in self._playing:
            # The same pitch struck again on the same part. Replacing it is
            # what a string does; letting both ring doubles the level.
            self._stop(key)
        voice = voice_for(role, channel, self._programs.get((role, channel)),
                          pitch)
        sound = self._sound_for(voice, pitch)
        if sound is None:
            return
        try:
            mixer_channel = pygame.mixer.find_channel(True)
            if mixer_channel is None:
                return
            # The role's balance and the written velocity, both at PLAY time
            # rather than baked into the samples -- so one rendered note
            # serves the guide and the band, and the cache cannot hand back a
            # note at the other part's volume.
            mixer_channel.set_volume(
                min(1.0, role_gain(role) * max(0.25, velocity / 100.0)))
            mixer_channel.play(sound)
        except pygame.error:
            # A device that went away mid-song must not take the frame loop
            # with it, the rule `MidiPlayer._send` already follows.
            return
        self._playing[key] = (mixer_channel, sound)

    def _note_off(self, role: str, channel: int, pitch: int) -> None:
        self._stop((role, channel, pitch))

    def _stop(self, key) -> None:
        """Fade this note out -- but only if it is still the one sounding.

        `find_channel(True)` steals the longest-playing channel, which is
        usually one of ours, so a tracked note can have been displaced by a
        later one. Fading the channel without checking would silence the note
        that took its place: at high polyphony a note-off would cut a note
        that had only just been struck.
        """
        import pygame

        held = self._playing.pop(key, None)
        if held is None:
            return
        mixer_channel, sound = held
        try:
            if mixer_channel.get_sound() is not sound:
                return
            mixer_channel.fadeout(FADE_MS)
        except pygame.error:
            pass

    def _follow_the_mixer(self) -> None:
        """Re-read the rate, and throw the rendered notes away if it moved.

        The rate was read ONCE, in `__init__`, and `Shift+A` reopens the
        mixer: `AUDIO_ALLOW_FREQUENCY_CHANGE` lets SDL open at the device's
        own rate, so reopening onto another output can come back at 48000
        where it was 44100. `make_sound` reads a raw array at whatever rate
        the MIXER is on, so every cached note then plays 8.8 % sharp and
        short -- a hiss and a wrong pitch, from a key whose whole job is to
        make the sound sane again.

        Asked here rather than wired to `reopen()`, so it cannot be missed by
        a caller: any route that changes the rate is covered by being a route
        that changes the rate. The reopen is seconds of device work, so a
        `get_init()` beside it is free, and a note is about a millisecond to
        render again.
        """
        import pygame
        try:
            init = pygame.mixer.get_init()
        except pygame.error:
            return
        if not init:
            return
        rate, channels = int(init[0]), int(init[2])
        if rate == self._rate and channels == self._channels:
            return
        self._rate, self._channels = rate, channels
        self._sounds.clear()

    def _sound_for(self, voice: Voice, pitch: int):
        """One rendered note per voice and pitch, built once and kept.

        Cleared wholesale at `MAX_SOUNDS` rather than evicted one at a time:
        a re-render is about a millisecond, and an LRU here would be
        bookkeeping to save nothing. The font and note-head caches already
        work this way.
        """
        import pygame
        import pygame.sndarray

        self._follow_the_mixer()
        key = (voice.name, pitch)
        cached = self._sounds.get(key)
        if cached is not None:
            return cached
        if len(self._sounds) >= MAX_SOUNDS:
            self._sounds.clear()
        try:
            import numpy as np

            wave = render_wave(voice, pitch, self._rate)
            samples = (wave * 32767.0).astype(np.int16)
            if self._channels > 1:
                samples = np.repeat(samples[:, None], self._channels, axis=1)
            sound = pygame.sndarray.make_sound(np.ascontiguousarray(samples))
        except (pygame.error, ImportError, ValueError):
            # Narrow on purpose: this must not be wide enough to hide a
            # mistake in the arithmetic above. `output.click_sound` carries
            # the same comment, and for the same reason.
            return None
        self._sounds[key] = sound
        return sound


def open_synth() -> SoftSynth | None:
    """The built-in synth, or None where even the mixer will not open."""
    if not audio_output.ensure_mixer():
        return None
    try:
        return SoftSynth()
    except Exception as exc:                 # pragma: no cover - no device here
        print(f"Built-in synth unavailable: {exc}")
        return None
