"""One place that owns the audio OUTPUT device.

The player reported the sound going "flattering and quieter" mid-session:
once it starts it stays for every song, it happens with the MIDI backing and
no recording at all, other applications are unaffected, and only restarting
the app clears it. Every part of that says the fault is a piece of state
this process holds, not a file and not a song.

Three things follow from it, and none of them is a diagnosis yet:

- **The mixer had no buffer of its own.** It was opened lazily in two places
  with pygame's defaults, and pygame 2 defaults to 512 frames -- 11.6 ms at
  44.1 kHz, on a machine that is also running an aubio analysis thread, a
  WSOLA stretch and a 60 Hz game loop. A backing track is not a monitoring
  path and nobody can hear its latency, so there is nothing to be won by
  cutting it that fine and a starved output is exactly what "flattering"
  sounds like. `BUFFER` is the whole change.
- **Nothing said what the output was actually doing.** The run log named the
  INPUT device, its rate and its dropped buffers, and said not one word about
  the side the player was listening to. It does now.
- **And there was no way to test it without restarting.** `reopen()` closes
  and reopens the mixer on a key, which is both a way to carry on and the
  experiment that settles it: if the sound comes back, the fault is here; if
  it does not, it is the shared Windows device and no key in this app will
  reach it.
"""

from __future__ import annotations

# What the mixer is asked for. 2048 frames is 46 ms at 44.1 kHz -- inaudible
# on a backing track, and four times the headroom of the default.
RATE = 44100
SIZE = -16
CHANNELS = 2
BUFFER = 2048

#: The rate is a REQUEST, not a demand. *"Warum ist die Playback-Qualitaet
#: des MP3s in der App wesentlich schlechter als mit einem Player?"* -- and
#: the one thing in this path that the app chooses and a standalone player
#: does not is a second rate conversion. Windows runs its shared mixer at
#: the device's own rate, almost always 48000; asking for 44100 makes the
#: OS convert everything the app plays, on top of whatever SDL_mixer already
#: did to the file. Three of the player's seven recordings are 48 kHz, so
#: those went 48000 -> (SDL) 44100 -> (Windows) 48000 for no reason at all.
#:
#: `AUDIO_ALLOW_FREQUENCY_CHANGE` lets SDL open the device at ITS rate and
#: report back what it got, which leaves exactly one conversion -- the same
#: one any player does. The format and the channel count are NOT negotiable:
#: `pygame.sndarray` reads what `_decode` hands the time-stretch, and a
#: device that came back 8-bit or mono would change what that means.
#:
#: **This is the best-founded suspect and not a diagnosis.** It has never
#: been reproduced here; what makes it worth doing anyway is that it costs
#: nothing and that `describe()` then prints the device's real rate, so the
#: next run log answers the question instead of another round of guessing.
ALLOW_RATE_CHANGE = True


#: The output the player ASKED for, in SDL's spelling, or "" for the system
#: default. Held here rather than passed in, because the four callers of
#: `ensure_mixer` (the stretch, the recording, the synth and the click) have
#: no business knowing about the config -- and a second reader of this answer
#: is how two of them come to open different devices.
_wanted = ""
#: What was really opened, and why it is not what was asked for. Both are
#: about THIS process on THIS machine, so neither is ever stored: the whole
#: reason they exist is that "asked" and "got" turned out to be different
#: things nobody could tell apart afterwards -- the same lesson as
#: `vsync_outcome`.
_opened = ""
_fell_back = ""


def set_output_device(name: str) -> str:
    """Choose which output the mixer opens. "" is the system default.

    Returns a sentence for the player, empty where nothing had to change.

    It REOPENS at once where the mixer is already on another device, because
    a setting that takes effect at the next restart is indistinguishable from
    one that does nothing -- and this app has shipped that four times. The
    caller is expected to know nothing is playing; the picker is reached from
    the settings screen, where no song is loaded.
    """
    global _wanted
    name = str(name or "")
    if name == _wanted and (not name or name == _opened):
        return ""
    _wanted = name
    import pygame
    if not pygame.mixer.get_init():
        return ""
    if not reopen():
        return "The audio output could not be reopened"
    return f"Audio output: {describe()}"


def wanted_device() -> str:
    """The output name the mixer is being asked for. "" is the default."""
    return _wanted


def open_device() -> str:
    """What the mixer really opened, or "" for the system default."""
    return _opened


def fallback_reason() -> str:
    """Why the asked-for device was not the one opened. "" when it was."""
    return _fell_back


def _init(devicename: str) -> bool:
    """One attempt at opening the mixer. True when it came up."""
    import pygame
    allowed = (pygame.AUDIO_ALLOW_FREQUENCY_CHANGE
               if ALLOW_RATE_CHANGE and hasattr(
                   pygame, "AUDIO_ALLOW_FREQUENCY_CHANGE") else 0)
    extra = {"devicename": devicename} if devicename else {}
    try:
        pygame.mixer.pre_init(RATE, SIZE, CHANNELS, BUFFER,
                              allowedchanges=allowed, **extra)
        pygame.mixer.init(RATE, SIZE, CHANNELS, BUFFER,
                          allowedchanges=allowed, **extra)
    except TypeError:
        # An older pygame without `allowedchanges` -- or without
        # `devicename`, which is why the retry drops BOTH. The rate is then
        # what it always was, which is the behaviour this replaces rather
        # than a failure, so it opens rather than reporting no output at all.
        try:
            pygame.mixer.pre_init(RATE, SIZE, CHANNELS, BUFFER)
            pygame.mixer.init(RATE, SIZE, CHANNELS, BUFFER)
        except Exception:
            return False
    except Exception:
        # A machine with no output device at all still has to run: the
        # scoring, the picture and the input are all independent of this.
        return False
    return bool(pygame.mixer.get_init())


def ensure_mixer() -> bool:
    """Open the mixer if it is not open. True when it is usable.

    A named device that will not open FALLS BACK to the system default and
    says why. Refusing outright would leave the player with a silent app and
    one stored name to blame, where a device that was unplugged since it was
    chosen is the commonest thing that happens to a name.
    """
    global _opened, _fell_back
    import pygame
    if pygame.mixer.get_init():
        return True
    _fell_back = ""
    if _wanted and _init(_wanted):
        _opened = _wanted
        return True
    if _wanted:
        _fell_back = _wanted
    if _init(""):
        _opened = ""
        return True
    _opened = ""
    return False


def reopen() -> bool:
    """Close and reopen the mixer, losing whatever it was playing.

    The caller is expected to reload and reposition its own audio -- this
    knows nothing about songs.
    """
    import pygame
    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()
    except Exception:
        pass
    return ensure_mixer()


#: How many device names a one-line description may carry. A Windows box
#: lists the same interface several times over; the rest are counted.
MAX_NAMED_DEVICES = 4


def device_names() -> list[str]:
    """Every audio OUTPUT device SDL can see, in SDL's own spelling.

    *"Ueber Focusrite kommt kein MP3 mehr auf dem Mac. Wenn ich etwas
    ausserhalb der App laufen lasse, kommt es ueber Focusrite."*

    This is the list the picker offers, and it is also what a run log can say
    about a machine whose output nobody chose: with no stored name the mixer
    takes the system default, and SDL cannot be asked which device that was.
    So `describe_devices()` says what was on OFFER and never which is open --
    a device the player is listening through that is not in this list AT ALL
    is the whole diagnosis, and one that is in it rules the same thing out.
    Only a device the app itself CHOSE may be named, which is what `_opened`
    is for. The same rule the input device already follows, where a log that
    does not name the device cannot tell a wrong one from a blocked one.

    SDL's own spelling, because `mixer.init(devicename=...)` takes these
    strings and nothing else -- so this is also the list a picker would offer.
    """
    try:
        import pygame
        from pygame._sdl2 import audio as sdl2_audio
    except Exception:
        # An older pygame, or one built without _sdl2. Saying nothing is the
        # honest answer; it must never be why the run log fails to write.
        return []
    try:
        return [str(name) for name in sdl2_audio.get_audio_device_names(False)]
    except Exception:
        return []


def describe_devices() -> str:
    """The output devices on offer, as its OWN line.

    Apart from `describe()` on purpose. One says what the open stream is set
    to and the other what the machine offered, and they are fixed in different
    places: a rate the device chose is a conversion, a device missing from
    this list is a driver. Said together on one line they would be a sentence
    too wide for the status note that `Shift+A` writes -- and that line is
    where the answer has to be readable, since a run log is written later.
    """
    names = device_names()
    if not names:
        return "(SDL named none)"
    shown = names[:MAX_NAMED_DEVICES]
    rest = len(names) - len(shown)
    return ", ".join(shown) + (f" +{rest} more" if rest > 0 else "")


def describe() -> str:
    """What the output device is actually set to, for the run log.

    A number nobody records is a number nobody can compare against the run
    that went wrong -- the same reason the input device is named.
    """
    import pygame
    got = pygame.mixer.get_init()
    if not got:
        return "(not open)"
    rate, fmt, channels = got[0], got[1], got[2]
    # What was ASKED FOR beside what was got, where they differ. The rate is
    # a request now, so a log that prints only the answer cannot say whether
    # the device chose it or the app did -- and that is the whole question.
    asked = "" if rate == RATE else f" (asked {RATE})"
    # The device is NAMED only where the app chose it, which is the whole
    # difference this picker makes: SDL cannot be asked which output it took
    # for itself, so "system default" is an honest answer and a guessed name
    # would not be. A fall-back says so rather than reading as a choice.
    where = _opened or "system default"
    if _fell_back:
        where += f" — {_fell_back} would not open"
    return (f"{where}, {rate} Hz{asked}, {abs(fmt)}-bit, {channels} ch, "
            f"{BUFFER} frame buffer ({1000 * BUFFER / rate:.0f} ms)")


#: How long a click lasts. Short enough to be a point in time rather than a
#: sound with a shape -- a long click has no single moment to play ON.
CLICK_MS = 25.0

#: Its pitch. High enough to cut through a guitar without being in the way
#: of it, and the detector never hears it: the click goes OUT.
CLICK_HZ = 1800.0

_click_sound = None


def buffer_latency_ms() -> float:
    """What the mixer's own buffer costs on the way OUT, in milliseconds.

    The one part of the output path that is KNOWN. The driver and the OS mixer
    add more and nothing here can see it, so this is a floor and the latency
    wizard says it as one -- naming a floor is honest where subtracting an
    unmeasured number is a guess with a decimal point on it.
    """
    import pygame
    try:
        init = pygame.mixer.get_init()
    except pygame.error:
        init = None
    rate = int(init[0]) if init else RATE
    return 1000.0 * BUFFER / float(rate or RATE)


def click_sound():
    """A metronome click, built once. None if the mixer will not open.

    Built rather than shipped as a file: a 25 ms burst is four lines of
    arithmetic, and a .wav in the package is one more thing `pickhero.spec`
    has to carry and one more thing to be absent inside the EXE.
    """
    global _click_sound
    if _click_sound is not None:
        return _click_sound
    if not ensure_mixer():
        return None
    # Imported HERE because this module has no module-level pygame -- and
    # the first version of this function assumed it did. A bare
    # `except Exception` then swallowed the NameError and the wizard had a
    # click that was silently None on a machine whose mixer opened fine.
    # The except below is for the DEVICE, so it must not be wide enough to
    # hide a mistake like that one.
    import pygame
    import pygame.sndarray
    try:
        import numpy as np

        init = pygame.mixer.get_init()
        rate = int(init[0]) if init else RATE
        channels = int(init[2]) if init else CHANNELS
        n = max(1, int(rate * CLICK_MS / 1000.0))
        t = np.arange(n, dtype=np.float32) / float(rate)
        # A fast exponential decay, so the attack is the moment and the tail
        # is not. A plain rectangular burst clicks at BOTH ends.
        wave = (np.sin(2.0 * np.pi * CLICK_HZ * t)
                * np.exp(-t * 1000.0 / (CLICK_MS * 0.35)))
        samples = (wave * 0.45 * 32767.0).astype(np.int16)
        if channels > 1:
            samples = np.repeat(samples[:, None], channels, axis=1)
        _click_sound = pygame.sndarray.make_sound(np.ascontiguousarray(samples))
        return _click_sound
    except (pygame.error, ImportError, ValueError):
        # A wizard that cannot click is a wizard that says so, not one that
        # takes the app down -- the rule `scan_files` and `describe_tab`
        # already follow. NARROW on purpose: see above.
        return None


def forget_click() -> None:
    """Drop the built click. A Sound does not survive `pygame.quit()`."""
    global _click_sound
    _click_sound = None
