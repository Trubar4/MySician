"""A tuner that knows which string you are tuning.

The playing screen already carries a chromatic strip -- it names the nearest
note and its cents. That is the wrong instrument for tuning up: it says "you
are playing a G#", not "your D string is 34 cents flat", and it cannot show
which strings are already done.

No library is needed for any of this. The pitch is aubio's, which the app has
run since the first day, and a tuner is that pitch measured against a target:
`1200 * log2(heard / target)` cents. What a tuner has to get right is not the
arithmetic but what it refuses to say.

- **It reads the RAW pitch, not the calibrated one.** `_correct_octave_jump`
  halves a frequency whose half lands near a calibrated string -- and this
  player's stored calibration has the A string an octave low. Being wrong
  about the octave while playing costs one note; being wrong about it while
  tuning makes them detune the guitar to match.
- **It picks the string, and abstains when it cannot.** A pitch more than
  `CATCH_SEMITONES` from every string of the chosen tuning names nothing. The
  strings of any tuning here sit at least three semitones apart, so inside
  that window the nearest one is unambiguous; outside it, a tuner that
  guesses sends the player the wrong way, and further out with every turn.
- **In tune is a state that has to be HELD.** One frame inside the band is a
  string passing through the note on its way somewhere else. `STEADY_MS` of
  it is a string that is actually there.
"""

from __future__ import annotations

import math
import time

import pygame

from pickhero.audio.input import AudioCapture
from pickhero.audio.note_utils import (
    NAMED_TUNINGS, midi_to_freq, midi_to_name, tuning_for_notes,
)
from pickhero.config import Config
from pickhero.ui.colors import get_theme

# How far from a string a reading may be and still be about that string --
# an upper bound, not the whole rule. The test that asserts the window
# cannot reach two strings failed on DADGAD, whose G and A sit a WHOLE TONE
# apart: a fixed 2-semitone window would have owned both, and the tuner
# would have named whichever it rounded to. So the window is half the
# closest pair in the tuning actually chosen, capped here. On DADGAD that is
# one semitone, which is still far more than a guitar drifts.
CATCH_SEMITONES = 2.0

# Inside this the string is in tune. Five cents is under what an ear picks
# out on a single note and inside what a guitar holds between songs anyway.
IN_TUNE_CENTS = 5.0
CLOSE_CENTS = 15.0

# How long the reading has to stay inside the band before the string counts
# as done. A string sweeping past the note is inside it for one frame.
STEADY_MS = 400.0

# Only readings this confident are used at all.
MIN_CONFIDENCE = 0.75

# The needle's smoothing. A raw YIN reading on a decaying low string jitters
# by a few cents, which makes a needle that never settles and cannot be
# tuned against.
SMOOTHING = 0.25


def nearest_string(freq: float, tuning: dict[int, int]) -> tuple[int, float] | None:
    """(string, cents) for the string this reading is about, or None.

    None is a real answer and the common one: a muted thunk, a harmonic, or
    a string so far out that no target owns it.
    """
    if freq <= 0 or len(tuning) < 2:
        return None
    pitches = sorted(tuning.values())
    closest_pair = min(b - a for a, b in zip(pitches, pitches[1:]))
    catch = min(CATCH_SEMITONES, closest_pair / 2.0)
    best: tuple[int, float] | None = None
    for string, midi in tuning.items():
        target = midi_to_freq(midi)
        if target <= 0:
            continue
        cents = 1200.0 * math.log2(freq / target)
        if abs(cents) > catch * 100.0:
            continue
        if best is None or abs(cents) < abs(best[1]):
            best = (string, cents)
    return best


class TunerMenuScreen:
    """Pick a tuning, play a string, see how far off it is."""

    def __init__(self, config: Config, tuning_notes: str = "",
                 song: str = ""):
        self._config = config
        self._capture: AudioCapture | None = None
        self._error = ""
        # The tuning of the song that was highlighted when this was opened.
        # The app already knows it -- the song list shows it on every row --
        # so asking the player to dial it in again is asking them for
        # something we have. A song in a tuning nobody named, or none at all,
        # opens on Standard exactly as before.
        self._tuning_index = self._index_of(tuning_notes)
        self._song = song if self._tuning_index else ""
        # string -> cents, smoothed; and string -> when it became steady
        self._cents: dict[int, float] = {}
        self._steady_since: dict[int, float] = {}
        self._done: set[int] = set()
        self._active: int | None = None
        self._last_heard = 0.0
        self._start_capture()

    @staticmethod
    def _index_of(letters: str) -> int:
        """Which named tuning those open strings are, 0 (Standard) if none."""
        shape = tuning_for_notes(letters)
        if shape is None:
            return 0
        for index, (_, candidate) in enumerate(NAMED_TUNINGS):
            if candidate == shape:
                return index
        return 0

    # -- audio ---------------------------------------------------------

    def _start_capture(self) -> None:
        try:
            self._capture = AudioCapture(self._config)
            self._capture.start()
            self._error = ""
        except Exception as exc:
            self._capture = None
            self._error = str(exc)

    def close(self) -> None:
        if self._capture is not None:
            self._capture.stop()
            self._capture = None

    # -- state ---------------------------------------------------------

    @property
    def tuning_name(self) -> str:
        return NAMED_TUNINGS[self._tuning_index][0]

    @property
    def tuning(self) -> dict[int, int]:
        return NAMED_TUNINGS[self._tuning_index][1]

    def _choose_tuning(self, step: int) -> None:
        self._tuning_index = (self._tuning_index + step) % len(NAMED_TUNINGS)
        # Chosen by hand now, so the line naming the song it came from would
        # be describing something that is no longer true.
        self._song = ""
        # Everything measured was measured against the old targets.
        self._cents.clear()
        self._steady_since.clear()
        self._done.clear()
        self._active = None

    def update(self) -> None:
        if self._capture is None:
            return
        # Raw: the calibration must not be allowed an opinion here.
        freq, confidence = self._capture.get_tuner_data(raw=True)
        if freq <= 0 or confidence < MIN_CONFIDENCE:
            return
        found = nearest_string(float(freq), self.tuning)
        if found is None:
            return
        string, cents = found
        now = time.perf_counter() * 1000.0
        self._last_heard = now
        self._active = string
        previous = self._cents.get(string)
        self._cents[string] = (cents if previous is None
                               else previous + (cents - previous) * SMOOTHING)
        if abs(self._cents[string]) <= IN_TUNE_CENTS:
            started = self._steady_since.setdefault(string, now)
            if now - started >= STEADY_MS:
                self._done.add(string)
        else:
            self._steady_since.pop(string, None)
            self._done.discard(string)

    def handle_event(self, event: pygame.event.Event) -> str | None:
        if event.type != pygame.KEYDOWN:
            return None
        if event.key in (pygame.K_ESCAPE, pygame.K_g):
            return "escape"
        if event.key in (pygame.K_LEFT, pygame.K_UP):
            self._choose_tuning(-1)
        elif event.key in (pygame.K_RIGHT, pygame.K_DOWN):
            self._choose_tuning(+1)
        elif event.key == pygame.K_r:
            self._cents.clear()
            self._steady_since.clear()
            self._done.clear()
            self._active = None
        return None

    # -- drawing -------------------------------------------------------

    def _row_colour(self, string: int, theme) -> tuple[int, int, int]:
        cents = self._cents.get(string)
        if cents is None:
            return theme.hud_text
        if string in self._done:
            return theme.tuner_in_tune
        if abs(cents) <= CLOSE_CENTS:
            return theme.tuner_close
        return theme.tuner_off

    def advice(self) -> tuple[str, str]:
        """(what to do, the note being tuned) -- in words, not in cents.

        "-34 cents" asks the player to know that negative means flat and that
        flat means turn the peg the tightening way. The thing they DO is the
        thing to say; the number stays underneath for anyone who wants it.
        """
        if self._active is None:
            return "Play a string", ""
        note = midi_to_name(self.tuning[self._active])
        cents = self._cents.get(self._active)
        if cents is None:
            return "Play a string", ""
        if self._active in self._done:
            return "In tune", note
        if abs(cents) <= IN_TUNE_CENTS:
            return "Hold it…", note
        return ("Too low — tighten" if cents < 0
                else "Too high — loosen"), note

    def render(self, surface: pygame.Surface) -> None:
        """One string, big, and six pips for the rest.

        Six bars at once is five rows of nothing moving and one to find --
        which is what the little arrow beside them was there to solve. A
        tuner is about the string in your hand, so that one gets the screen
        and the others shrink to whether they are done.
        """
        t = get_theme()
        surface.fill(t.menu_bg)
        w, h = surface.get_size()
        huge = pygame.font.SysFont("Arial", 96, bold=True)
        title = pygame.font.SysFont("Arial", 30, bold=True)
        body = pygame.font.SysFont("Arial", 22)
        small = pygame.font.SysFont("Arial", 16)

        head = title.render(self.tuning_name, True, t.hud_accent)
        surface.blit(head, (w // 2 - head.get_width() // 2, 24))
        under = (f"from {self._song}" if self._song
                 else "LEFT / RIGHT: another tuning")
        hint = small.render(under, True, t.hud_text)
        surface.blit(hint, (w // 2 - hint.get_width() // 2, 62))

        if self._error:
            msg = body.render(f"No input: {self._error}", True, t.feedback_miss)
            surface.blit(msg, (w // 2 - msg.get_width() // 2, h // 2))
            return

        action, note = self.advice()
        colour = (self._row_colour(self._active, t) if self._active is not None
                  else t.hud_text)

        # The note being tuned, in the size a tuner is read at -- across the
        # room, over the top of a guitar.
        if note:
            name = huge.render(note, True, colour)
            surface.blit(name, (w // 2 - name.get_width() // 2, h // 2 - 190))

        # One needle, as wide as the screen allows.
        bar_w = min(760, w - 120)
        bar_x, bar_y = w // 2 - bar_w // 2, h // 2 - 40
        pygame.draw.rect(surface, t.signal_cold, (bar_x, bar_y, bar_w, 22))
        centre = bar_x + bar_w // 2
        band = max(2, int(bar_w / 2 * IN_TUNE_CENTS / 50.0))
        pygame.draw.rect(surface, t.hud_text,
                         (centre - band, bar_y, 2 * band, 22), 1)
        pygame.draw.line(surface, t.hud_text,
                         (centre, bar_y - 10), (centre, bar_y + 32), 1)
        cents = (self._cents.get(self._active)
                 if self._active is not None else None)
        if cents is not None:
            offset = int(max(-1.0, min(1.0, cents / 50.0)) * (bar_w // 2))
            pygame.draw.rect(surface, colour,
                             (centre + offset - 5, bar_y - 8, 10, 38))

        # What to DO about it.
        say = title.render(action, True, colour)
        surface.blit(say, (w // 2 - say.get_width() // 2, h // 2 + 24))
        if cents is not None and note:
            fine = small.render(
                f"{'+' if cents >= 0 else ''}{cents:.0f} ¢", True, t.hud_text)
            surface.blit(fine, (w // 2 - fine.get_width() // 2, h // 2 + 66))

        # Six pips, low string first: which ones are done. Small on purpose --
        # they are a checklist, not the thing being read.
        pip_r, gap = 13, 46
        pips_x = w // 2 - (5 * gap) // 2
        pips_y = h // 2 + 122
        for i, string in enumerate(sorted(self.tuning, reverse=True)):
            x = pips_x + i * gap
            done = string in self._done
            pygame.draw.circle(surface, t.tuner_in_tune if done else t.signal_cold,
                               (x, pips_y), pip_r, 0 if done else 2)
            if string == self._active:
                pygame.draw.circle(surface, t.hud_accent, (x, pips_y),
                                   pip_r + 5, 2)
            label = small.render(
                midi_to_name(self.tuning[string]).rstrip("0123456789"), True,
                t.hud_text)
            surface.blit(label, (x - label.get_width() // 2, pips_y + 20))

        done = small.render(
            f"{len(self._done)} of 6 in tune   |   R: start over   "
            f"|   ESC: back", True, t.hud_text)
        surface.blit(done, (w // 2 - done.get_width() // 2, h - 56))
