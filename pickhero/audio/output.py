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


def ensure_mixer() -> bool:
    """Open the mixer if it is not open. True when it is usable."""
    import pygame
    if pygame.mixer.get_init():
        return True
    try:
        pygame.mixer.pre_init(RATE, SIZE, CHANNELS, BUFFER)
        pygame.mixer.init(RATE, SIZE, CHANNELS, BUFFER)
    except Exception:
        # A machine with no output device at all still has to run: the
        # scoring, the picture and the input are all independent of this.
        return False
    return bool(pygame.mixer.get_init())


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
    return (f"{rate} Hz, {abs(fmt)}-bit, {channels} ch, "
            f"{BUFFER} frame buffer ({1000 * BUFFER / rate:.0f} ms)")
