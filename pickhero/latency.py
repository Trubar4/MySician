"""What the latency wizard measures, as arithmetic.

*"Warum machen wir dafuer nicht einen konfig Button. Ich spiele Noten, wenn
ich sie sehe und du machst den Wert selbst."*

The idea is right and the mechanism in that sentence is not, which is the
one thing worth correcting before any of this: **reacting to something you
SEE takes 150-250 ms**, so a wizard that showed a note and timed the answer
would fit the offset to his reaction time and be out by more than the whole
hit window. Playing ALONG with a steady pulse is anticipatory and lands
within tens of milliseconds, which is why every latency calibration in every
DAW works that way. So the wizard plays a click and he plays with it.

**Nothing here judges the measurement.** A click is one note per beat, which
is a Timeline; the real `NoteMatcher` scores it and `timing_report` already
decides whether a median is an effect or a coincidence, whether the spread is
latency or the playing, and whether one offset could fix it at all. `K` reads
that same verdict. A second rule here could only produce the two disagreeing
-- which this project has already paid for, in the days when `K` applied a
looser test than the report and set an offset out of noise.

No pygame and no audio: the schedule, the tab and the wording are testable
without a screen, and the screen is then only the clicking and the drawing.
"""
from __future__ import annotations

from dataclasses import dataclass

from pickhero.matcher import LATENCY_SEARCH_MS
from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline)

#: Slow on purpose, and the reason is a constraint rather than comfort. The
#: matcher refuses a strike that two written notes explain equally well
#: (`_unambiguous`), and every click here is the SAME pitch -- so the beat
#: has to be longer than the window the candidate search looks in, which is
#: `LATENCY_SEARCH_MS` behind plus the hit window ahead. At 80 BPM that is
#: 750 ms against 650, and `spacing_clears_search` asserts it rather than
#: trusting the arithmetic.
CLICK_BPM = 80.0

#: Clicks that sound and are NOT scored, so finding the beat is not measured
#: as playing badly. Four is a bar.
LEAD_IN_CLICKS = 4

#: Clicks that are scored. Sixteen is twelve seconds at this tempo, which is
#: short enough to do twice, and twice the eight samples the report needs
#: before it will say anything at all.
SCORED_CLICKS = 16

#: Below this an open string is where `yinfast` folds: measured on the tuner's
#: own reference takes, the open low E at 82 Hz came back as a THIRD of its
#: pitch on 301 of 301 confident readings. A folded reading reads as a
#: subharmonic, `_times_its_own_strike` refuses it, and the wizard would
#: collect nothing while looking like a broken detector. A2 is the lowest
#: string the same takes read straight.
LOWEST_SAFE_MIDI = 45

#: The order to try, and it is the measurement rather than a preference. On
#: the tuner's reference takes D and G read 76 % and 71 % of their frames
#: usable AS RECORDED, where B was 18 % and the high e 13 % -- those two are
#: too quiet to calibrate on, and the low strings fold. So: the middle.
STRING_PREFERENCE = (4, 3, 5, 2, 6, 1)


@dataclass(frozen=True)
class Click:
    """One click: when it is due, and whether it is scored."""
    at_ms: float
    scored: bool


def beat_ms(bpm: float = CLICK_BPM) -> float:
    return 60_000.0 / float(bpm)


def spacing_clears_search(bpm: float = CLICK_BPM,
                          hit_window_ms: float = 150.0) -> bool:
    """Whether one click can ever be mistaken for its neighbour.

    The candidate search looks `LATENCY_SEARCH_MS` behind a strike and
    `hit_window_ms` ahead of it. Two written notes inside that span make the
    strike ambiguous and the sample is refused -- correctly, since nothing
    can say which note it belongs to. Every click being the same pitch is
    what makes this the binding constraint on the tempo.
    """
    return beat_ms(bpm) > LATENCY_SEARCH_MS + float(hit_window_ms)


def schedule(lead_in: int = LEAD_IN_CLICKS, scored: int = SCORED_CLICKS,
             bpm: float = CLICK_BPM) -> list[Click]:
    """Every click, in order, with the first beat at one whole beat in.

    Not at zero: the stream has just opened, and a click at millisecond zero
    is one nobody is ready for.
    """
    step = beat_ms(bpm)
    return [Click(at_ms=step * (i + 1), scored=i >= lead_in)
            for i in range(int(lead_in) + int(scored))]


def calibration_string(tuning: dict[int, int]) -> tuple[int, int]:
    """(string, its open pitch) to ask for, out of this guitar's own tuning.

    Never a number written down: a drop tuning moves the low strings and a
    seven-string tab moves all of them, so the pitch has to be read off the
    tuning that is really there.
    """
    if not tuning:
        return 4, 50
    for string in STRING_PREFERENCE:
        pitch = tuning.get(string)
        if pitch is not None and pitch >= LOWEST_SAFE_MIDI:
            return string, int(pitch)
    # Everything folds. Take the HIGHEST string there is rather than the
    # preferred one: it is the only one with a chance of reading straight.
    string = max(tuning, key=lambda s: tuning[s])
    return int(string), int(tuning[string])


def timeline_at(times_ms, midi_note: int, string: int,
                bpm: float = CLICK_BPM) -> Timeline:
    """One note per given moment, as a tab the real matcher can score.

    The times come in rather than being derived, because the ones that
    matter are when each click REALLY went out: a click is fired by the frame
    loop, so it is up to one frame late, and scoring against the nominal beat
    would bake half a frame of bias into every offset this ever produces.

    `NoteEvent` is frozen, so they have to be built AT those times -- the
    first version shifted them afterwards and raised.
    """
    step = beat_ms(bpm)
    at = [float(t) for t in times_ms]
    notes = [NoteEvent(timestamp_ms=t, midi_note=int(midi_note),
                       string=int(string), fret=0,
                       duration_ms=step * 0.5, duration_quarters=0.5)
             for t in at]
    end = (at[-1] + step) if at else step
    measures = [MeasureInfo(index=i, start_ms=step * i,
                            end_ms=step * (i + 1), beats=1, beat_type=4)
                for i in range(int(end / step) + 1)]
    return Timeline(notes,
                    SongMetadata(title="Latency", tempo=float(bpm),
                                 tuning={int(string): int(midi_note)}),
                    measures=measures)


def click_timeline(midi_note: int, string: int, *,
                   lead_in: int = LEAD_IN_CLICKS,
                   scored: int = SCORED_CLICKS,
                   bpm: float = CLICK_BPM) -> Timeline:
    """The nominal schedule as a tab. Only the SCORED clicks are written.

    A lead-in click with a note on it would be counted as a miss or, worse,
    as a timing sample taken while the player was still finding the beat.
    """
    return timeline_at([c.at_ms for c in schedule(lead_in, scored, bpm)
                        if c.scored], midi_note, string, bpm)


def verdict_text(report: dict | None, applied: float | None,
                 output_latency_ms: float = 0.0) -> list[str]:
    """What to say about a finished measurement, in the report's own words.

    Three things have to be said and the third is the one a wizard like this
    usually leaves out: **how much of the answer is the click's own output
    path.** The value is right for playing along to something heard through
    this mixer, which is the case it was calibrated for; for playing to the
    picture alone it over-corrects by the mixer's buffer, and that buffer is
    known while the rest of the output path is not. So it is named as a
    floor rather than subtracted, because subtracting a number nobody
    measured is how a guess gets a decimal point.
    """
    if report is None:
        return ["Not enough was heard to measure anything.",
                "Play ON the click, one note a beat, and try again."]
    verdict = report.get("verdict")
    spread = report.get("spread_ms")
    samples = report.get("count")
    median = report.get("median_ms")
    head = [f"{samples} notes measured, median {median:+.0f} ms, "
            f"spread +/-{spread:.0f} ms"]
    if applied is None:
        if verdict == "scatter":
            return head + [
                "That is the PLAYING, not latency: the notes are spread too "
                "wide for one offset to fix.",
                "Nothing was stored. Play closer to the click, or leave the "
                "offset where it is."]
        if verdict == "per_string":
            return head + [
                "The strings disagree with each other, which one offset "
                "cannot fix either.",
                "Nothing was stored."]
        return head + [
            "The median is inside its own noise, so it is not an effect.",
            "Nothing was stored."]
    out = head + [f"Offset set to {applied:+.0f} ms."]
    if output_latency_ms > 0:
        out.append(
            f"About {output_latency_ms:.0f} ms of that is this mixer's own "
            f"buffer -- right when you play along to a recording through it, "
            f"and that much too much when you play to the picture alone.")
    return out
