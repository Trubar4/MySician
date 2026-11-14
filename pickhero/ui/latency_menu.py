"""The latency wizard: play along with a click, and the offset sets itself.

*"Warum machen wir dafuer nicht einen konfig Button. Ich spiele Noten, wenn
ich sie sehe und du machst den Wert selbst."*

The second half is built here and the first half is corrected, because it
would have made the measurement worthless: **reacting to something you SEE
takes 150-250 ms**, so timing an answer to a shown note fits the offset to
a reaction time. Playing ALONG with a steady pulse is anticipatory and lands
within tens of milliseconds. So the wizard clicks and he plays with it.

**Nothing here decides whether the measurement is any good.** The clicks are
a one-note-per-beat tab, the real `NoteMatcher` scores it, and
`matcher.timing_report` already separates latency from the playing, refuses
a median inside its own noise and refuses a split between strings. `K` reads
that same verdict, so there is one answer in the app to "is this latency"
and this screen cannot disagree with the key.

**The click goes out through the MIXER**, which is the player's own choice
and a coherent one: it is the same output his MP3 backings come through, so
the offset this produces is the one that is right when he plays along to a
recording. For playing to the picture alone it over-corrects by the mixer's
output path -- `output.buffer_latency_ms()` is the known floor of that, and
the screen names it rather than subtracting a number nobody measured.
"""
from __future__ import annotations

import pygame

from pickhero import latency
from pickhero.audio import output
from pickhero.audio.input import AudioCapture
from pickhero.config import Config
from pickhero.matcher import NoteMatcher
from pickhero.ui.colors import get_theme

#: The report decides nothing below this many samples, and says so. Read
#: from the playing screen's own constant so K and this cannot drift apart.
MIN_SAMPLES = 8

#: Verdicts one offset can actually remove. The same pair `_auto_sync_timing`
#: accepts -- "mixed" is latency with loose playing on top, and the offset
#: still takes the constant part out, which is what it is for.
APPLIES = ("latency", "mixed")


def _font(size: int) -> pygame.font.Font:
    for family in ("Consolas", "Courier New", "monospace"):
        font = pygame.font.SysFont(family, size)
        if font:
            return font
    return pygame.font.Font(None, size)


class LatencyMenuScreen:
    """Measure the input latency against a click, and store it."""

    def __init__(self, config: Config, tuning: dict[int, int] | None = None):
        self._config = config
        self._tuning = dict(tuning or {1: 64, 2: 59, 3: 55,
                                       4: 50, 5: 45, 6: 40})
        self._string, self._midi = latency.calibration_string(self._tuning)
        self._capture: AudioCapture | None = None
        self._state = "ready"         # ready, running, done, error
        self._error = ""
        self._lines: list[str] = []
        self._schedule = latency.schedule()
        self._fired: list[float] = []      # ring ms each click really went out
        self._next_click = 0
        self._strikes: list = []
        self._began_ms = 0.0
        self._applied: float | None = None
        self._heard: int | None = None     # the pitch the run really read

    # ── what it asks of the player ─────────────────────────────────────────

    @property
    def note_name(self) -> str:
        from pickhero.audio.note_utils import midi_to_name
        return midi_to_name(self._midi)

    def prompt(self) -> str:
        """Which string to try, and why that one -- a HINT, not a demand.

        The measurement reads the pitch off what was played
        (`latency.pitch_played`), so any string will do and the guitar need
        not be in the song's tuning. What the hint is for is reliability: not
        the low E, whatever a player would reach for first, because measured
        on the tuner's own reference takes `yinfast` returns a THIRD of its
        pitch on 301 of 301 confident readings of an open low E -- and not
        the top two either, which read 18 % and 13 % of their frames at the
        level those takes were recorded at.
        """
        return (f"Play ONE open string on every click — "
                f"try string {self._string} ({self.note_name}).")

    def pick_string(self, string: int) -> None:
        """1-6 moves the HINT. It cannot change what is measured."""
        pitch = self._tuning.get(int(string))
        if pitch is None or self._state == "running":
            return
        self._string, self._midi = int(string), int(pitch)

    def _backing_offset(self) -> float:
        """The GLOBAL backing offset, which the click is delayed by.

        `backing_offset_for("")` is the global value rather than any song's
        override: what this screen stores is one number per setup, so it has
        to be measured against the one backing delay that is also per setup.
        """
        getter = getattr(self._config, "backing_offset_for", None)
        if getter is None:
            return float(getattr(self._config, "backing_offset_ms", 0.0))
        return float(getter(""))

    # ── the run ────────────────────────────────────────────────────────────

    def handle_event(self, event: pygame.event.Event) -> str | None:
        if event.type != pygame.KEYDOWN:
            return None
        if event.key == pygame.K_ESCAPE:
            self._stop()
            return "back"
        if pygame.K_1 <= event.key <= pygame.K_6:
            self.pick_string(event.key - pygame.K_1 + 1)
            return None
        if event.key in (pygame.K_SPACE, pygame.K_RETURN):
            if self._state == "running":
                self._finish()
            else:
                self._begin()
        return None

    def _begin(self) -> None:
        self._lines = []
        self._applied = None
        self._fired = []
        self._strikes = []
        self._next_click = 0
        try:
            if self._capture is None:
                self._capture = AudioCapture(self._config)
            self._capture.start()
        except Exception as exc:                        # noqa: BLE001
            self._stop()
            self._state, self._error = "error", str(exc)
            return
        # The ring's own counter is the ONE clock here: the clicks are fired
        # against it and the strikes are stamped from it, so there is no
        # offset to find and no anchor to keep -- which is the whole reason
        # this screen can be exact where the playing screen needs
        # `_reanchor_audio_clock`.
        self._began_ms = self._capture.elapsed_ms()
        self._state = "running"

    def _stop(self) -> None:
        if self._capture is not None:
            self._capture.stop()
            self._capture = None

    def update(self) -> None:
        if self._state != "running" or self._capture is None:
            return
        now = self._capture.elapsed_ms() - self._began_ms
        # Drained EVERY frame, the way the app does. `strike_queue` holds 16
        # and collecting at the end would keep only the last sixteen of
        # them -- the fault four check tools shipped with.
        for note in self._capture.get_notes():
            if note.note.is_onset:
                self._strikes.append(note)
        delay = self._backing_offset()
        while (self._next_click < len(self._schedule)
               and now >= self._schedule[self._next_click].at_ms + delay):
            sound = output.click_sound()
            if sound is not None:
                sound.play()
            # The moment it REALLY went out, not the moment it was due. A
            # click is fired by the frame loop, so it is up to one frame
            # late, and scoring against the nominal time would bake half a
            # frame of bias into every offset this screen ever produces.
            self._fired.append(now)
            self._next_click += 1
        if self._next_click >= len(self._schedule) and now > (
                self._schedule[-1].at_ms + delay + latency.beat_ms()):
            self._finish()

    def _finish(self) -> None:
        """Score what was played, and store the offset if it is one."""
        self._state = "done"
        strikes, fired = self._strikes, list(self._fired)
        self._stop()
        self._heard = latency.pitch_played(strikes)
        report = self._score(strikes, fired)
        if report is not None and report.get("verdict") in APPLIES:
            # The same arithmetic `_auto_sync_timing` uses, and the same
            # sign: the report's median is how LATE a strike was, and the
            # offset has to move strikes by the opposite of it. No tempo
            # factor -- there is no practice speed on this screen.
            self._applied = self._store(-report["median_ms"])
        self._lines = latency.verdict_text(report, self._applied,
                                           output.buffer_latency_ms(),
                                           self._heard,
                                           self._backing_offset())

    def _score(self, strikes, fired) -> dict | None:
        """The clicks as a tab, the real matcher, and the real report."""
        delay = self._backing_offset()
        # The written times are the fire times with the DELIBERATE delay
        # taken back out. Two different things are riding on this and they
        # pull opposite ways:
        #
        # - the frame's own lateness is unpredictable and he cannot play to
        #   it, so he plays to what he HEARS and the fired time is the truth.
        #   Scoring against the nominal beat bakes half a frame of bias into
        #   every offset -- measured at +50.5 ms on a ragged frame loop.
        # - the backing offset is deliberate and constant, and it is part of
        #   the answer: at song time T the backing sounds the note written at
        #   T - B, so he hears it B late and his strike lands B late with it.
        #   An offset measured without B is wrong by exactly B the moment he
        #   plays along to the backing.
        #
        # Subtracting B from the fire time keeps the jitter cancelling AND
        # leaves B in the median, which is the only arrangement that is right
        # about both.
        scored = [at - delay for at, click in zip(fired, self._schedule)
                  if click.scored]
        if len(scored) < MIN_SAMPLES or not strikes:
            return None
        heard = latency.pitch_played(strikes)
        if heard is None:
            return None
        # Written AT the pitch that came back, so the guitar's tuning cannot
        # make this screen collect nothing. See `latency.pitch_played`.
        timeline = latency.timeline_at(scored, heard, self._string)
        window = float(self._config.timing_window_ms)
        matcher = NoteMatcher(timeline, timing_window_ms=window,
                              late_window_ms=300.0)
        matcher.audio_offset_ms = 0.0
        last = self._began_ms
        for note in strikes:
            note.timestamp_ms -= self._began_ms
            matcher.process_detected_notes([note], note.timestamp_ms)
            last = note.timestamp_ms
        matcher.process_detected_notes([], last + 1000.0)
        return matcher.timing_report(MIN_SAMPLES)

    def _store(self, value_ms: float) -> float:
        from pickhero.config import MAX_LATENCY_OFFSET_MS
        clamped = max(-MAX_LATENCY_OFFSET_MS,
                      min(MAX_LATENCY_OFFSET_MS, float(value_ms)))
        self._config.audio_latency_offset_ms = clamped
        self._config.save()
        return clamped

    # ── the screen ─────────────────────────────────────────────────────────

    def render(self, surface: pygame.Surface) -> None:
        theme = get_theme()
        surface.fill(theme.menu_bg)
        width = surface.get_width()
        big, mid, small = _font(40), _font(24), _font(18)

        def centred(font, text, colour, y):
            drawn = font.render(text, True, colour)
            surface.blit(drawn, (width // 2 - drawn.get_width() // 2, y))

        centred(big, "Timing offset", theme.hud_accent, 60)
        centred(mid, f"stored: {self._config.audio_latency_offset_ms:+.0f} ms",
                theme.hud_text, 120)

        if self._state == "error":
            centred(mid, "The input device could not be opened",
                    theme.feedback_miss, 220)
            centred(small, self._error[:90], theme.hud_text, 260)
        elif self._state == "ready":
            centred(mid, self.prompt(), theme.hud_text, 220)
            centred(small, "Play WITH the click, not after it — reacting to "
                           "what you see is a fifth of a second all by "
                           "itself.", theme.hud_text, 262)
            centred(small, f"{latency.LEAD_IN_CLICKS} clicks to find the "
                           f"beat, then {latency.SCORED_CLICKS} are measured "
                           f"({latency.SCORED_CLICKS * latency.beat_ms() / 1000:.0f} s).",
                    theme.hud_text, 292)
            centred(mid, "SPACE starts   |   1-6 suggests a string   |   ESC back",
                    theme.hud_accent, 360)
        elif self._state == "running":
            done = max(0, self._next_click - latency.LEAD_IN_CLICKS)
            total = latency.SCORED_CLICKS
            if self._next_click <= latency.LEAD_IN_CLICKS:
                centred(big, "…", theme.hud_text, 200)
                centred(mid, "find the beat", theme.hud_text, 260)
            else:
                centred(big, f"{min(done, total)} / {total}",
                        theme.hud_accent, 200)
                centred(mid, f"{len(self._strikes)} picks heard",
                        theme.hud_text, 260)
            centred(small, self.prompt(), theme.hud_text, 320)
            centred(small, "SPACE stops early   |   ESC back",
                    theme.hud_text, 360)
        else:
            y = 200
            for i, line in enumerate(self._lines):
                colour = theme.hud_accent if i == 0 else theme.hud_text
                centred(mid if i == 0 else small, line, colour, y)
                y += 34 if i == 0 else 28
            centred(mid, "SPACE measures again   |   ESC back",
                    theme.hud_accent, y + 30)

    def copy_text(self) -> str:
        """Ctrl+C: the measurement, so a report can be pasted rather than
        read off a photograph of the monitor."""
        head = [f"stored offset {self._config.audio_latency_offset_ms:+.0f} ms",
                self.prompt()]
        return "\n".join(head + self._lines)
