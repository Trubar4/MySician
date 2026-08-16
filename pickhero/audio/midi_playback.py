"""Real-time MIDI playback for backing tracks.

Sends MIDI note-on/note-off events through pygame.midi.Output, driven by the
existing playback clock. No audio files or temp files needed.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field


# MIDI status bytes
NOTE_ON = 0x90
NOTE_OFF = 0x80
PROGRAM_CHANGE = 0xC0

# All-notes-off CC
ALL_NOTES_OFF_CC = 123


@dataclass(frozen=True, order=True)
class MidiEvent:
    """A single MIDI event, ordered by timestamp."""

    timestamp_ms: float
    channel: int       # 0-15
    event_type: int    # 0x90, 0x80, 0xC0
    data1: int         # note number or program number
    data2: int = 0     # velocity (ignored for program change)


class BackingTrack:
    """Sorted list of MidiEvents with cursor-based scheduling."""

    def __init__(self, events: list[MidiEvent] | None = None):
        self._events = sorted(events or [])
        self._timestamps = [e.timestamp_ms for e in self._events]
        self._cursor = 0

    def __len__(self) -> int:
        return len(self._events)

    @property
    def events(self) -> list[MidiEvent]:
        return list(self._events)

    def get_events_until(self, time_ms: float) -> list[MidiEvent]:
        """Return events from cursor to time_ms (inclusive), advancing cursor."""
        if self._cursor >= len(self._events):
            return []
        end = bisect.bisect_right(self._timestamps, time_ms, lo=self._cursor)
        result = self._events[self._cursor:end]
        self._cursor = end
        return result

    def seek(self, time_ms: float) -> None:
        """Reset cursor to the first event at or after time_ms."""
        self._cursor = bisect.bisect_left(self._timestamps, time_ms)

    def get_program_changes_before(self, time_ms: float) -> list[MidiEvent]:
        """Return the last program change per channel before time_ms.

        Used to re-send instrument assignments after seeking.
        """
        # bisect_RIGHT, and never before zero: program changes sit at t=0, and
        # bisect_left excluded them, so seeking to the start of a song (or to
        # a count-in, which is negative) sent no instrument assignments at all
        # and every melodic backing track played on whatever the synth
        # happened to have on that channel.
        end = bisect.bisect_right(self._timestamps, max(0.0, time_ms))
        latest: dict[int, MidiEvent] = {}
        for event in self._events[:end]:
            if event.event_type == PROGRAM_CHANGE:
                latest[event.channel] = event
        return list(latest.values())


def _init_midi_once() -> bool:
    """Initialise pygame.midi exactly once per process.

    pygame.midi.quit() invalidates the device IDs PortMidi handed out, so a
    player that quit on close left the next one to open a stale default id and
    fail with "Invalid device ID" -- which is why the backing worked on the
    first song of a session and went silent on every one after it.
    """
    global _MIDI_READY
    if _MIDI_READY:
        return True
    try:
        import pygame.midi
        pygame.midi.init()
        _MIDI_READY = True
    except Exception as e:
        print(f"MIDI init failed: {e}")
    return _MIDI_READY


def list_midi_outputs() -> list[tuple[int, str]]:
    """Available MIDI output devices as (id, name)."""
    if not _init_midi_once():
        return []
    import pygame.midi
    outputs = []
    for device_id in range(pygame.midi.get_count()):
        info = pygame.midi.get_device_info(device_id)
        if info is None:
            continue
        _, name, _, is_output, _ = info
        if is_output:
            outputs.append((device_id, name.decode(errors="replace")
                            if isinstance(name, bytes) else str(name)))
    return outputs


# A software synth is what actually makes sound on a stock Windows box; a
# hardware port opens fine and stays silent.
_SYNTH_HINTS = ("wavetable", "synth", "gs ", "fluid", "timidity")


def _pick_output_device() -> int:
    """Choose a MIDI output, preferring one that can make a sound by itself.

    get_default_output_id() is not trustworthy: it returns -1 on machines with
    no default set, and a stale id after a quit/init cycle.
    """
    outputs = list_midi_outputs()
    if not outputs:
        return -1
    for device_id, name in outputs:
        if any(hint in name.lower() for hint in _SYNTH_HINTS):
            return device_id

    import pygame.midi
    try:
        default_id = pygame.midi.get_default_output_id()
    except Exception:
        default_id = -1
    if any(device_id == default_id for device_id, _ in outputs):
        return default_id
    return outputs[0][0]


_MIDI_READY = False
# One process, one backing track, one output. Opening a second one competes
# with the first for a device that only allows a single handle, and the loser
# gets an object that reports success and then refuses every write.
_SHARED_OUTPUT = None
_SHARED_OUTPUT_NAME = ""


def _open_shared_output():
    """Open the MIDI output once, verifying it actually works.

    pygame.midi.Output() does NOT raise when the device refuses to open: it
    prints "Unable to open Midi OutputDevice" and hands back an object whose
    every write throws "midi Output not open". So the handle is proven with a
    real message before it is accepted.
    """
    global _SHARED_OUTPUT, _SHARED_OUTPUT_NAME
    if _SHARED_OUTPUT is not None:
        return _SHARED_OUTPUT
    if not _init_midi_once():
        return None

    import pygame.midi
    outputs = list_midi_outputs()
    if not outputs:
        print("No MIDI output device found - backing track will be silent")
        return None

    preferred = _pick_output_device()
    order = [preferred] + [d for d, _ in outputs if d != preferred]
    names = dict(outputs)
    for device_id in order:
        try:
            candidate = pygame.midi.Output(device_id)
            # Prove it: an all-notes-off on channel 0 is inaudible and fails
            # loudly on a handle that only pretended to open.
            candidate.write_short(0xB0, 123, 0)
        except Exception as e:
            print(f"  MIDI device {device_id} ({names.get(device_id, '?')}) "
                  f"unusable: {e}")
            continue
        _SHARED_OUTPUT = candidate
        _SHARED_OUTPUT_NAME = names.get(device_id, str(device_id))
        print(f"MIDI output: {_SHARED_OUTPUT_NAME}")
        return _SHARED_OUTPUT

    print("No MIDI output could be opened - backing track will be silent")
    return None


class MidiPlayer:
    """Wraps pygame.midi.Output for backing track playback."""

    def __init__(self, backing_track: BackingTrack):
        self._track = backing_track
        self._output = None
        self._active_notes: set[tuple[int, int]] = set()  # (channel, note)
        self._muted = False
        self._opened = False

    def open(self) -> bool:
        """Initialize pygame.midi and open the default output device.

        Returns True on success, False if MIDI is unavailable.
        """
        self._output = _open_shared_output()
        self._opened = self._output is not None
        return self._opened

    def play_click(self, velocity: int = 100) -> None:
        """Play a single metronome click on the MIDI percussion channel.

        Uses channel 9 (percussion), note 76 (hi wood block).
        """
        self._send(NOTE_ON | 9, 76, velocity)

    def update(self, playback_ms: float) -> None:
        """Fire all events up to playback_ms. Called each frame."""
        events = self._track.get_events_until(playback_ms)
        if not events or self._output is None:
            return
        for event in events:
            self._dispatch(event)

    def seek(self, time_ms: float) -> None:
        """Silence all notes, reset cursor, re-send program changes."""
        self._all_notes_off()
        self._track.seek(time_ms)
        # Re-send instrument assignments so the right sounds play
        if self._output is not None and not self._muted:
            for pc in self._track.get_program_changes_before(time_ms):
                self._send(PROGRAM_CHANGE | pc.channel, pc.data1, 0)

    def pause(self) -> None:
        """Silence all active notes."""
        self._all_notes_off()

    def set_muted(self, muted: bool) -> None:
        """Toggle output. When muted, cursor still advances to avoid burst on unmute."""
        if muted and not self._muted:
            self._all_notes_off()
        self._muted = muted

    @property
    def is_muted(self) -> bool:
        return self._muted

    def close(self) -> None:
        """Silence notes and close MIDI output."""
        self._all_notes_off()
        # The output is shared and stays open for the next song; closing it
        # here is what made every song after the first one silent.
        self._output = None
        # Deliberately NOT calling pygame.midi.quit(): it invalidates the
        # device ids for everything opened afterwards in this process.
        self._opened = False

    def _send(self, status: int, data1: int, data2: int) -> None:
        """Send one MIDI message, and never let it take the app down.

        A device can go away mid-song (unplugged, grabbed by another program),
        and pygame surfaces that as an exception from write_short. A backing
        track falling silent is a nuisance; the app dying is not.
        """
        if self._output is None or self._muted:
            return
        try:
            self._output.write_short(status, data1, data2)
        except Exception as e:
            print(f"MIDI output lost ({e}) - backing track is now silent")
            global _SHARED_OUTPUT
            _SHARED_OUTPUT = None
            self._output = None

    def _dispatch(self, event: MidiEvent) -> None:
        """Send a single MIDI event to the output."""
        if event.event_type == NOTE_ON:
            self._active_notes.add((event.channel, event.data1))
            self._send(NOTE_ON | event.channel, event.data1, event.data2)
        elif event.event_type == NOTE_OFF:
            self._active_notes.discard((event.channel, event.data1))
            self._send(NOTE_OFF | event.channel, event.data1, event.data2)
        elif event.event_type == PROGRAM_CHANGE:
            self._send(PROGRAM_CHANGE | event.channel, event.data1, 0)

    def _all_notes_off(self) -> None:
        """Send note-off for all tracked active notes, then CC123 on all channels."""
        if self._output is None:
            return
        # Explicit note-off for tracked notes
        for channel, note in list(self._active_notes):
            try:
                self._output.write_short(NOTE_OFF | channel, note, 0)
            except Exception:
                pass
        self._active_notes.clear()
        # CC 123 (All Notes Off) on all 16 channels as safety net
        for ch in range(16):
            try:
                self._output.write_short(0xB0 | ch, ALL_NOTES_OFF_CC, 0)
            except Exception:
                pass
