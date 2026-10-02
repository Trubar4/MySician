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

# How far a reading may be from a string the player has NAMED. Far wider
# than the automatic window on purpose: the whole reason to name a string is
# that the tuner would not own it -- a fresh string half a tone flat gets no
# answer from `nearest_string` at all, which is exactly when a tuner is most
# needed. Stopping short of 1200 keeps YIN's octave error out: a reading an
# octave up is the detector being wrong, not the string being wrong.
LOCKED_CENTS = 900.0

# The needle's smoothing, and it is NOT what made the tuner look restless --
# measured, because the obvious fix was the wrong one. Run over the six
# open-string reference takes through the real detector, with the needle
# sampled once a frame the way this screen samples it, the value moves
# **0.08 cents from frame to frame in the median and 0.22 in the 90th
# percentile** -- under two pixels on a 760 px needle. A slower smoothing
# would have been a fix for nothing. What the player was seeing is the
# reading APPEARING AND VANISHING, which is the two chapters below.
SMOOTHING = 0.25

# How far a FOLDED reading may sit from the string the player named. Half a
# semitone: a fold is only ever offered for a reading nothing else explains,
# and a fold that lands further off than this is not that string's harmonic
# series, it is a coincidence.
FOLD_CENTS = 50.0

# What a subharmonic reading may be multiplied by. Measured rather than
# chosen: on a hot, clean take of the low E (-11.5 dB peak) aubio's yinfast
# returns **a third of the pitch on 301 of 301 confident readings** -- 27.3 Hz
# against 82.4 -- so the low E of a guitar is, through this detector, simply
# not readable directly at all. Two is in the list for the ordinary octave
# error. Four and five are NOT, and the reason is arithmetic rather than
# taste: A2 over four is 27.5 Hz and E2 over three is 27.47, two cents apart,
# so a reading there cannot be attributed to a string by any rule.
FOLD_MULTIPLES = (2, 3)

# Below this the pitch stops being worth reading -- the same knee the playing
# screen's QUIET_PEAK_DB sits at, measured the same way. Normalising the
# reference takes to -12 dBFS takes the B string from 18 % of readings usable
# to 88 % and the high e from 13 % to 71 %, with nothing else changed. So a
# tuner that says nothing about the level is a tuner that looks broken on a
# signal the player could simply turn up.
QUIET_PEAK_DB = -38.0


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
        #: The string the player named, or None for "whichever is heard".
        #: *"damit ich beim Stimmen die Saite wählen kann, falls die falsche
        #: erkannt wird"* -- and the case that needs it most is the one
        #: where NO string is recognised, because the one in his hand is too
        #: far out for any of them to own it.
        self._locked: int | None = None
        self._last_heard = 0.0
        #: The loudest level heard while a pitch was coming through.
        self._peak_db = -120.0
        #: (when, Hz) of the last confident reading no string owned.
        self._stray: tuple[float, float] | None = None
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
        # Measured against targets that are no longer the question.
        self._stray = None

    def update(self) -> None:
        if self._capture is None:
            return
        # Raw: the calibration must not be allowed an opinion here.
        freq, confidence = self._capture.get_tuner_data(raw=True)
        if freq <= 0 or confidence < MIN_CONFIDENCE:
            return
        # The loudest thing heard while a pitch was coming through, which is
        # "while playing". No decay: a tuner is up for half a minute and the
        # question is whether the signal is ever strong enough, not what it
        # is doing this instant.
        self._peak_db = max(self._peak_db, self._capture.get_signal_db())
        found = self._reading(float(freq))
        if found is None:
            # A confident reading that no string owns. Until now this was
            # simply dropped, and the screen went on saying "Play a string"
            # while the player was playing one -- which is this project's
            # own definition of a feature that cannot be told from a broken
            # one. It is remembered so the screen can say what it heard and
            # what to press.
            self._stray = (time.perf_counter() * 1000.0, float(freq))
            return
        self._stray = None
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

    def _reading(self, freq: float) -> tuple[int, float] | None:
        """(string, cents) for this frequency, or None to ignore it.

        With a string named, every reading is about THAT string and the
        catch window does not apply -- naming it is the player saying so.
        Without one, the tuner guesses as it always did.
        """
        if self._locked is None:
            return nearest_string(freq, self.tuning)
        target = midi_to_freq(self.tuning[self._locked])
        if freq <= 0 or target <= 0:
            return None
        cents = 1200.0 * math.log2(freq / target)
        if abs(cents) <= LOCKED_CENTS:
            return (self._locked, cents)
        return self._folded(freq, target)

    def _folded(self, freq: float, target: float) -> tuple[int, float] | None:
        """A subharmonic of the NAMED string, read as that string.

        The low E of a guitar comes back from yinfast as a third of its
        pitch, on every confident reading of a clean take -- so without this
        the bottom string of every tuning cannot be tuned at all, by the
        automatic path or by naming it. Multiplying the reading back up is
        exact: if the detector found the period of three cycles then
        `1200*log2(3f/target)` is the cents of the fundamental to the last
        decimal, so nothing is estimated and no bias is introduced.

        **Only with the string named**, and that is the whole safety
        argument rather than a convenience. A reading of 27.5 Hz is the low
        E over three AND the A string over four, two cents apart -- and the
        second really happens: on a weak take in the reference set the A
        string reads a quarter of its pitch on 237 consecutive readings, so
        an automatic fold would have shown "E" to a player holding the A
        string and had them tune it down a fifth. A run-length rule does not
        separate those two cases (237 against 301) and was dropped for
        changing nothing. What separates them is the player saying which
        string is in their hand.
        """
        for mult in FOLD_MULTIPLES:
            cents = 1200.0 * math.log2(freq * mult / target)
            if abs(cents) <= FOLD_CENTS:
                return (self._locked, cents)
        return None

    def _lock(self, string: int) -> None:
        """Name a string, or let go of the one already named.

        The same key both ways: the player who pressed 5 to get away from a
        wrong guess presses 5 again to stop, without having to find a second
        key for it. Letting go clears that string's reading too -- it was
        measured against a target that is no longer the question.
        """
        if string not in self.tuning:
            return
        self._locked = None if self._locked == string else string
        self._cents.pop(string, None)
        self._steady_since.pop(string, None)
        self._done.discard(string)
        self._active = self._locked

    def handle_event(self, event: pygame.event.Event) -> str | None:
        if event.type != pygame.KEYDOWN:
            return None
        if event.key in (pygame.K_ESCAPE, pygame.K_g):
            return "escape"
        # 1 to 6, the way a guitarist counts them and the way the rest of
        # this app already numbers them: 6 is the low E, 1 the high one.
        # They are written under the pips, so nothing has to be remembered.
        if pygame.K_1 <= event.key <= pygame.K_6:
            self._lock(event.key - pygame.K_1 + 1)
            return None
        if pygame.K_KP1 <= event.key <= pygame.K_KP6:
            self._lock(event.key - pygame.K_KP1 + 1)
            return None
        if event.key in (pygame.K_LEFT, pygame.K_UP):
            self._choose_tuning(-1)
        elif event.key in (pygame.K_RIGHT, pygame.K_DOWN):
            self._choose_tuning(+1)
        elif event.key == pygame.K_r:
            self._cents.clear()
            self._steady_since.clear()
            self._done.clear()
            self._active = None
            self._locked = None
            self._stray = None
            self._peak_db = -120.0
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
        if self._locked is not None and self._cents.get(self._locked) is None:
            # Named but not heard yet. "Play a string" would be wrong -- it
            # is one particular string that is being waited for.
            return (f"Play string {self._locked}",
                    midi_to_name(self.tuning[self._locked]))
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

    #: How long a stray reading is still news.
    STRAY_MS = 2000.0

    def notes(self) -> list[str]:
        """What the screen has to say beyond the needle, newest first.

        Two states that used to be invisible, and both of them look exactly
        like a tuner that does not work:

        - a confident reading no string owns, which on the bottom string of
          any tuning is EVERY reading (see `_folded`), and
        - a signal too weak for the pitch to be worth reading, which on the
          reference takes costs the B string 70 points of its readings.

        Returned rather than drawn, so the rule is testable without a
        screen and the words are in one place.
        """
        out: list[str] = []
        if self._stray is not None and self._locked is None:
            when, freq = self._stray
            if time.perf_counter() * 1000.0 - when <= self.STRAY_MS:
                out.append(
                    f"Heard {freq:.0f} Hz — no string of {self.tuning_name} "
                    f"is near it. Press 1-6 to say which string you are "
                    f"playing.")
        if self._peak_db > -120.0 and self._peak_db < QUIET_PEAK_DB:
            out.append(
                f"Input is quiet ({self._peak_db:.0f} dB) — turn the "
                f"interface up. The pitch stops being reliable below "
                f"{QUIET_PEAK_DB:.0f} dB.")
        return out

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
            if string == self._locked:
                # A second, thicker ring: the named string has to be
                # findable at a glance from across the room, and it is a
                # different fact from "this is the one being heard".
                pygame.draw.circle(surface, t.hud_accent, (x, pips_y),
                                   pip_r + 10, 3)
            label = small.render(
                midi_to_name(self.tuning[string]).rstrip("0123456789"), True,
                t.hud_text)
            surface.blit(label, (x - label.get_width() // 2, pips_y + 20))
            # The KEY, under the note. Written down because a tuner is read
            # with a guitar in both hands and nothing should have to be
            # remembered -- and because 6 being the low E is a convention,
            # not something the screen would otherwise say.
            number = small.render(str(string), True,
                                  t.hud_accent if string == self._locked
                                  else t.signal_cold)
            surface.blit(number, (x - number.get_width() // 2, pips_y + 40))

        # Everything between the pips and the shortcut line stacks UPWARD
        # from h-80 on measured heights, so a second line pushes the block
        # up instead of through the one above it -- the rule the playing
        # screen's footer, its sync panel and its completion overlay have
        # each been fixed for once already.
        tail: list[tuple[str, tuple[int, int, int]]] = []
        if self._locked is not None:
            note = midi_to_name(self.tuning[self._locked])
            tail.append((f"Listening only to string {self._locked} ({note}) — "
                         f"{self._locked} again for automatic", t.hud_accent))
        for line in self.notes():
            tail.append((line, t.feedback_close))
        y = h - 80
        for text, colour in reversed(tail):
            surf = small.render(text, True, colour)
            y -= surf.get_height() + 4
            surface.blit(surf, (w // 2 - surf.get_width() // 2, y))

        done = small.render(
            f"{len(self._done)} of 6 in tune   |   1-6: pick the string   "
            f"|   R: start over   |   ESC: back", True, t.hud_text)
        surface.blit(done, (w // 2 - done.get_width() // 2, h - 56))
