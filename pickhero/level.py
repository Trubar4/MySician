"""What the input level did over a run, and what to do about it.

*"Es stand nicht da, dass ich die Gitarre lauter machen soll. Bedienerfehler?"*

It did say so -- while the song was RUNNING, in the bottom-right column, in
14 px type, to somebody with both hands on a guitar. `_level_advice` is
deliberately silent once the song stops, because the tracked peak decays after
the last note and it once reported a level fault that was not there. The
consequence nobody had noticed is that the **completion screen**, which is the
one place the question "why was it 21 %" actually gets asked, said nothing
about the input at all -- while the run log beside it carried four measured
numbers that answer it.

So the measurements live here rather than inside the log writer, and the log
and the completion screen read the same ones. Two readers of this arithmetic
is how the screen and the log come to disagree about the same run.

Nothing here is a new threshold: every bound is one the HUD advice already
used, so the screen cannot grade a run more or less harshly than the live
line did.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Sequence

from pickhero.config import MAX_GATE_DB, MIN_GATE_DB

# Input level advice. A level at or below this has not been measured yet --
# the meter reads -120 dB before any audio arrives.
SIGNAL_UNKNOWN_DB = -119.0
# An RMS this high over a 512-sample hop means the peaks are already against
# the ceiling, and a clipped waveform has no period for YIN to find.
CLIPPING_DB = -8.0
# How loud the loudest hop has to be for the detector to keep its grip.
# Measured, not guessed: the player's own play-along take was attenuated in
# steps and read back through the real detector, which gives the level at
# which pitch accuracy starts to rot. In the same units the HUD shows (RMS
# over one 512-sample hop):
#
#   loudest hop   -20   -32   -38   -44   -50   -56 dB
#   heard right    96    96    91    83    52     9 %
#
# So the knee sits around -38 and the collapse below -44. Note what fails
# first: strikes keep arriving, they just carry the WRONG PITCH -- which is
# why "few strikes" is the wrong thing to look for, and why the completion
# screen counts strikes heard next to notes landed.
QUIET_PEAK_DB = -40.0
# How far the loudest playing must clear the gate before the gate itself is
# the thing eating the notes. A strike decays fast, so most of a note sits
# well below its own peak.
QUIET_MARGIN_DB = 12.0
# How far the quietest moment must stay UNDER the gate before background hum
# starts firing onsets of its own.
NOISE_MARGIN_DB = 6.0
# Per frame, so one loud accident does not fix the advice in place for the
# rest of the song.
LEVEL_DECAY_DB = 0.05

# The room is what the microphone hears while the song is NOT running, which
# is the only moment it can be read: a low percentile of a take that is being
# PLAYED is not the room. Measured across one session's reference takes, the
# 2nd percentile ranged from -35 dB on a dense passage with no gaps to -94 dB
# on a sparse one, against a recorded room of -73 -- so a percentile says how
# busy the playing was, not how quiet the room is.
#
# The QUIETEST contiguous stretch of it, not the median of the whole window,
# and that correction cost a false alarm on the player's own run. The window
# was a rolling 300 frames, so "the room" was always the last five seconds in
# which the song was not running -- which is the end of the count-in, exactly
# when a player brushes a string or checks their sound. Measured on
# `run_Shinedown___Monsters_20261004_162405.txt`: the log recorded
# `level_room_db -27.4` and raised `input_hears_the_room yes`, while the
# completion screen a few seconds later read -63 dB from the same function.
# Thirty-six decibels, one run, one measurement -- and the verdict flipped
# with it.
#
# So the room is the quietest run of ROOM_SAMPLES frames anywhere in the
# not-playing audio: a stretch the player really was quiet for, rather than
# an average over one they were not. A median WITHIN that stretch, so one
# frame of silence cannot define a room.
ROOM_WINDOW = 900
ROOM_SAMPLES = 90
#: How far the search slides between stretches. Half a stretch, so a quiet
#: moment cannot fall between two of them and be missed entirely.
ROOM_STEP = ROOM_SAMPLES // 2

#: Within this far of the loudest hop is the PLAYING; everything below it is
#: the gaps between notes. The same window the run log has always used.
PLAYING_WINDOW_DB = 30.0
#: The step the X and C keys move the gate in. A gate within one press of
#: where the measurement puts it is not worth a sentence.
GATE_STEP_DB = 5.0


def quietest_stretch(samples: Sequence[float]) -> float | None:
    """The room: the median of the quietest contiguous run of samples.

    `None` while fewer than ROOM_SAMPLES have been heard -- a room measured
    over half a second is not a room, and answering anyway is how a gate
    comes to be derived from one brushed string.

    Chosen by the stretch's own median rather than by its minimum, so a
    single quiet frame inside a noisy stretch cannot win, and read back as
    that same median, so the answer is a figure the stretch really produced.
    """
    n = len(samples)
    if n < ROOM_SAMPLES:
        return None
    seq = list(samples)
    best: float | None = None
    for start in range(0, n - ROOM_SAMPLES + 1, ROOM_STEP):
        here = statistics.median(seq[start:start + ROOM_SAMPLES])
        if best is None or here < best:
            best = here
    # The last stretch is measured even when the step does not land on it:
    # on a run that stopped mid-step it is the only one with the newest
    # audio in it.
    tail = statistics.median(seq[n - ROOM_SAMPLES:])
    return tail if best is None or tail < best else best


def gate_band(peak: float, floor: float) -> tuple[float, float]:
    """The window a noise gate may sit in, as (lowest, highest).

    Above the room by NOISE_MARGIN_DB so hum does not fire onsets of its own,
    and below the playing by QUIET_MARGIN_DB so a decaying note survives --
    capped by `MAX_GATE_DB`, which is the level at which the DETECTOR gives
    up and therefore the point past which gating wins nothing.

    The band can be EMPTY (lowest > highest) and that is a real state, not an
    error: a hot, compressed signal has less than NOISE_MARGIN_DB +
    QUIET_MARGIN_DB of range to put a gate in. It has to be a state the
    advice can express, because for one cycle it was not -- the two pieces of
    advice named keys that undo each other, and with no gate able to satisfy
    both, the panel asked for X, then C, then X for ever. Which is what the
    player saw, and they pressed C until the gate reached the old ceiling and
    the clean half of the song stopped being heard.
    """
    return floor + NOISE_MARGIN_DB, min(peak - QUIET_MARGIN_DB, MAX_GATE_DB)


def suggested_gate_db(peak: float, floor: float) -> float:
    """A gate inside the band, on the 5 dB grid the X and C keys move in.

    As LOW in the band as still clears the room: the two failures are not
    each other's equals. A gate under the room costs spurious onsets, which
    the confidence filter and the matcher's candidate search already throw
    away; a gate over the playing costs the strikes themselves, and a strike
    that never arrives cannot be recovered by anything downstream.
    """
    lowest, highest = gate_band(peak, floor)
    target = math.ceil(lowest / 5.0) * 5.0
    if target > highest:
        target = math.floor(highest / 5.0) * 5.0
    return max(MIN_GATE_DB, min(MAX_GATE_DB, target))


@dataclass(frozen=True)
class Report:
    """What the microphone heard over one run, and the verdict on it."""

    loudest: float
    median_playing: float
    under_gate_percent: float
    gate: float
    auto: bool
    #: None where the song never stood still long enough to read the room.
    room: float | None = None

    @property
    def suggested(self) -> float | None:
        """Where the measurement puts the gate, or None without a room."""
        if self.room is None:
            return None
        return suggested_gate_db(self.loudest, self.room)

    @property
    def band(self) -> tuple[float, float] | None:
        if self.room is None:
            return None
        return gate_band(self.loudest, self.room)

    @property
    def hears_the_room(self) -> bool:
        """The input sounded the same whether the guitar was played or not.

        The quantity is the DISTANCE between the room and the playing, not
        the room's own level: a loud room with a real instrument in front of
        the microphone is not this fault, and an earlier version of this rule
        used the gate ceiling as a proxy and convicted a healthy Focusrite.
        """
        return (self.room is not None
                and self.median_playing - self.room < QUIET_MARGIN_DB)

    @property
    def verdict(self) -> str:
        """Which of the five states this run was in.

        Ordered the way `_level_advice` orders them, and for the same reason:
        a wrong input device makes every other number here unreadable, so it
        is asked first.
        """
        if self.hears_the_room:
            return "room"
        if self.loudest >= CLIPPING_DB:
            return "loud"
        if self.loudest < QUIET_PEAK_DB:
            return "quiet"
        suggested = self.suggested
        if suggested is not None and self.gate > suggested + GATE_STEP_DB:
            return "gate"
        return "fine"

    @property
    def is_fault(self) -> bool:
        return self.verdict != "fine"

    def headline(self) -> str:
        """One sentence, in the past tense: this is read after the run."""
        verdict = self.verdict
        if verdict == "room":
            return ("Input was hearing the room, not the guitar — wrong "
                    "device? Pick your interface with D in the song list")
        if verdict == "loud":
            return ("Input was too loud — turn the interface down "
                    "(it distorts the pitch)")
        if verdict == "quiet":
            return ("Input was too quiet for reliable pitch — turn the "
                    "interface up (notes come back as the wrong note)")
        if verdict == "gate":
            # The percentage and the value that would not have discarded it,
            # in one sentence. A percentage of thrown-away audio is only
            # readable beside the gate that would have kept it.
            where = "" if self.auto else " — X lowers it, O re-arms the automatic"
            return (f"The {self.gate:.0f} dB gate threw away "
                    f"{self.under_gate_percent:.0f} % of the audio; the room "
                    f"puts it at {self.suggested:.0f} dB{where}")
        return "Input level was fine"

    def numbers(self) -> str:
        """The four measurements, which is what makes the verdict checkable."""
        room = ("room not measured" if self.room is None
                else f"room {self.room:.0f} dB")
        return (f"loudest {self.loudest:.0f} dB · while playing "
                f"{self.median_playing:.0f} dB · {room} · "
                f"{self.under_gate_percent:.0f} % under the "
                f"{self.gate:.0f} dB gate ({'auto' if self.auto else 'by hand'})")


def measure(samples: Sequence[float], room: float | None,
            gate: float, auto: bool) -> Report | None:
    """Read a run's level samples, or None where nothing was heard."""
    if not samples:
        return None
    loudest = max(samples)
    playing = [db for db in samples if db > loudest - PLAYING_WINDOW_DB]
    under = sum(1 for db in samples if db < gate)
    return Report(loudest=loudest,
                  median_playing=statistics.median(playing) if playing
                  else loudest,
                  under_gate_percent=100.0 * under / len(samples),
                  gate=gate, auto=auto, room=room)
