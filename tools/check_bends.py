"""Do the bend thresholds pass good playing and catch bad?

Three numbers decide whether a bend is marked down -- a quarter tone of
tolerance, half of the written hold, four readings of evidence -- and when
they were written there was no recording of a bend to fit them to. That makes
them the only thresholds in this app that were guessed, which is exactly the
thing `CLAUDE.md` says not to do. This tool is how they stop being guesses.

    python tools/record_reference.py --block 6
    python tools/check_bends.py

Block 6 records the pairs that make it a measurement: a full bend and a
deliberately shallow one, a held bend and one let go at once, a bend and
release, and a bend with vibrato -- the case most likely to embarrass a rule
that counts frames on target.

What it prints is not a score but two WINDOWS -- one per rule:

- **Height.** How far each bend fell short of what it was aiming for. The
  tolerance has to sit above every correct take's shortfall and below the
  deliberately shallow one's.
- **Hold.** How long the pitch actually stood at the top, in milliseconds.
  These takes are played free, with no tab and no clock, so a FRACTION of a
  written hold cannot be measured here -- only how long a hold lasts when
  somebody means it, against how long it lasts when they do not.

If a window has no room in it, no threshold works and the rule needs
rethinking rather than tuning. Exits non-zero if the height rule marks down a
correct take or lets the shallow one through.

**Only picks count as bends, and finding that out took a wrong answer first.**
The first version of this tool treated every onset as a bend attempt and duly
reported that correct and deliberately-shallow bends overlap, so no threshold
could work. They do not: the aubio onset detector fires again during a note's
decay, and those re-triggers came back as bends that never left the written
pitch. Measured on the player's own takes, a real pick peaks at -6 to -8.5 dB
and every one of these ghosts at -21 to -49 dB -- 13 dB clear of the quietest
real one. So a segment is a bend attempt only if its strike was loud enough to
be a pick. A tool whose control comes back broken is measuring itself.

None of this touches the app: the matcher reads the contour over the note's
WRITTEN window, out of the tab, and never has to guess where a note began.
"""

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.audio.detector import DetectedNote, PitchDetector  # noqa: E402
from pickhero.audio.input import TimestampedNote  # noqa: E402
from pickhero.audio.note_utils import freq_to_midi_exact  # noqa: E402
from pickhero.matcher import (  # noqa: E402
    BEND_HOLD_FRACTION, BEND_MIN_SAMPLES, BEND_TOLERANCE_CENTS, MatchType,
    NoteMatcher,
)
from pickhero.tabs.timeline import (  # noqa: E402
    NoteEvent, SongMetadata, Timeline,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
HOP = 512
# (take id, semitones aimed for, what is deliberately wrong with it)
TAKES = [
    ("60_bend_full_ok", 2.0, ""),
    ("61_bend_half_ok", 1.0, ""),
    ("64_bend_release", 2.0, ""),
    ("65_bend_vibrato", 2.0, ""),
    ("62_bend_too_short", 2.0, "height"),
    ("63_bend_not_held", 2.0, "hold"),
]
# The tab the verdict pass writes: what the exercises actually asked for -- a
# two-second note with the bend reaching at 40 % and held to the end. Every
# take gets the same one, including the two deliberate errors: the whole point
# of those is that the tab asks for something they do not deliver.
WRITTEN_NOTE_MS = 2000.0
WRITTEN_REACHES_AT = 0.4
# How many times each exercise asks for the bend to be played. Fewer strikes
# found than this is worth saying out loud: it means the reading rests on less
# than it looks like it does.
STRIKES_ASKED_FOR = 3
# A strike's contour is read from here until the next strike. The bends are
# played with a gap between them, so this only has to be long enough to hold
# one bend and short enough not to run into the next.
MAX_NOTE_MS = 2500.0
# Frames right after the attack, where the analysis window still holds the
# previous sound. Same reasoning as OnsetPitchCollector.SKIP_FRAMES.
SKIP_FRAMES = 3
# How far below the take's loudest strike a strike may be and still count as a
# pick. The onset detector re-triggers during a decaying note, and those
# ghosts sit 13 dB or more below the quietest real pick on these takes.
PICK_MARGIN_DB = 14.0
# Two onsets closer than this are one pick. A hard attack can fire the
# detector twice within a few frames.
MIN_PICK_GAP_MS = 300.0
# A vibrato dip below the target is not the end of a hold. Anything longer
# than this is.
HOLD_GAP_MS = 250.0


def contours(path: Path, written_midi: int):
    """One pitch contour per PICK: [(ms since the strike, semitones), ...]."""
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    detector = PitchDetector(buf_size=4096, hop_size=HOP, sample_rate=rate)
    onsets: list[tuple[float, float, int]] = []      # (ms, peak dB, index)
    readings: list[tuple[float, float]] = []         # (ms, semitones)
    for i in range(0, len(audio) - HOP + 1, HOP):
        detector.process(audio[i:i + HOP])
        ms = i * 1000.0 / rate
        if detector.last_is_onset:
            # Straight out of the audio rather than out of the detector: what
            # separates a pick from a re-trigger is how hard the string was
            # hit, and that is the waveform right after the onset.
            peak = float(np.abs(audio[i:i + int(rate * 0.1)]).max())
            onsets.append((ms, 20 * np.log10(max(peak, 1e-9)), i))
        if (detector.last_freq > 0
                and detector.last_confidence >= detector.confidence_threshold):
            readings.append(
                (ms, freq_to_midi_exact(detector.last_freq) - written_midi))

    picks = _picks(onsets)
    out = []
    for index, (start_ms, _, _) in enumerate(picks):
        end_ms = picks[index + 1][0] if index + 1 < len(picks) else float("inf")
        end_ms = min(end_ms, start_ms + MAX_NOTE_MS)
        skip_until = start_ms + SKIP_FRAMES * HOP * 1000.0 / rate
        contour = [(ms - start_ms, semis) for ms, semis in readings
                   if skip_until < ms < end_ms]
        if len(contour) >= BEND_MIN_SAMPLES:
            out.append(contour)
    return out


def _picks(onsets):
    """The onsets that were really picks.

    Two things are thrown away: a re-trigger during a decaying note, which is
    far quieter than any real pick, and a second fire within a few frames of
    the first, which is one hard attack counted twice.
    """
    if not onsets:
        return []
    loudest = max(db for _, db, _ in onsets)
    picks = []
    for ms, db, index in onsets:
        if db < loudest - PICK_MARGIN_DB:
            continue
        if picks and ms - picks[-1][0] < MIN_PICK_GAP_MS:
            continue
        picks.append((ms, db, index))
    return picks


def measure(contour, target: float):
    """(highest reached, how long the TARGET was held, in ms).

    The hold is measured against what the bend was aiming for, because that is
    what the app compares against -- and a dip shorter than `HOLD_GAP_MS` does
    not end it. That second part is not a convenience: vibrato swings the
    pitch either side of the target on purpose, and counting frames without it
    reads a held bend with vibrato on it as a bend let go four times a second.

    Milliseconds rather than a fraction, since these takes have no tab and so
    no written hold to take a fraction of.
    """
    reached = max(semis for _, semis in contour)
    tolerance = BEND_TOLERANCE_CENTS / 100.0
    on = [ms for ms, semis in contour if abs(semis - target) <= tolerance]
    if not on:
        return reached, 0.0
    longest = 0.0
    run_start = previous = on[0]
    for ms in on[1:]:
        if ms - previous > HOLD_GAP_MS:
            run_start = ms
        previous = ms
        longest = max(longest, ms - run_start)
    return reached, longest


def verdict(contour, target: float, written_midi: int, release: bool):
    """What the app itself would say about one bend: HIT, CLOSE or PENDING.

    The real matcher, fed the real contour, against a tab written the way the
    exercise was asked for. Everything above this is arithmetic about the
    playing; this is the rule the player actually meets.
    """
    points = ((0.0, 0.0), (WRITTEN_REACHES_AT, target),
              (1.0, 0.0 if release else target))
    note = NoteEvent(timestamp_ms=1000.0, duration_ms=WRITTEN_NOTE_MS,
                     midi_note=written_midi, string=3, fret=7, measure=0,
                     bend=points)
    matcher = NoteMatcher(Timeline([note], SongMetadata(title="bend", tempo=100)),
                          timing_window_ms=150.0)
    freq = 440.0 * 2 ** ((written_midi - 69) / 12)
    matcher.process_detected_notes(
        [TimestampedNote(note=DetectedNote(written_midi, freq, 0.95, "x", True),
                         timestamp_ms=1000.0)], 1000.0)
    for ms, semitones in contour:
        midi = written_midi + semitones
        matcher.process_detected_notes(
            [TimestampedNote(
                note=DetectedNote(int(round(midi)),
                                  440.0 * 2 ** ((midi - 69) / 12),
                                  0.95, "x", False),
                timestamp_ms=1000.0 + ms)], 1000.0 + ms)
    matcher.process_detected_notes([], 1000.0 + WRITTEN_NOTE_MS + 500)
    return matcher.get_note_state(note)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=None, help="a take set containing block 6")
    args = ap.parse_args()

    directory = Path(args.dir) if args.dir else newest_with_bends()
    if directory is None:
        print("Keine Aufnahme mit Block 6 gefunden. Aufnehmen mit:")
        print("  python tools/record_reference.py --block 6")
        print()
        print("Bis dahin sind die drei Bend-Schwellen ungemessen:")
        print(f"  Toleranz     {BEND_TOLERANCE_CENTS:.0f} Cent")
        print(f"  Haltedauer   {BEND_HOLD_FRACTION:.0%} des Geschriebenen")
        print(f"  Belege       {BEND_MIN_SAMPLES} Messwerte")
        return 1

    manifest = json.loads((directory / "manifest.json").read_text())
    takes = {t["id"]: t for t in manifest["takes"]}

    print(f"{directory.name}\n")
    print(f"{'Take':22s} {'Ziel':>5s} {'Bends':>6s} {'erreicht':>22s} "
          f"{'oben gehalten':>18s}")
    print("-" * 80)
    shortfalls: dict[str, list[float]] = {"": [], "height": [], "hold": []}
    holds: dict[str, list[float]] = {"": [], "height": [], "hold": []}
    failures = 0
    tolerance = BEND_TOLERANCE_CENTS / 100.0
    for take_id, target, wrong in TAKES:
        if take_id not in takes:
            print(f"{take_id:22s}  fehlt")
            continue
        take = takes[take_id]
        written = take["expected_midi"][0][0]
        found = contours(directory / take["file"], written)
        if not found:
            print(f"{take_id:22s}  kein Anschlag erkannt")
            continue
        readings = [measure(c, target) for c in found]
        reached = [r for r, _ in readings]
        held = [h for _, h in readings]
        shortfalls[wrong].extend(target - r for r in reached)
        holds[wrong].extend(held)

        # Only the HEIGHT rule is judged here. The hold rule compares against
        # the written hold, and these takes have no tab -- emulating it would
        # be inventing the very number the columns exist to supply.
        too_shallow = [r < target - tolerance for r in reached]
        bad = (not wrong and any(too_shallow)) or \
              (wrong == "height" and not all(too_shallow))
        failures += bad
        short = "" if len(found) >= STRIKES_ASKED_FOR else \
            f"  (nur {len(found)} von {STRIKES_ASKED_FOR})"
        print(f"{take_id:22s} {target:4.1f}  {len(found):5d}  "
              f"{min(reached):+5.2f} bis {max(reached):+5.2f} Halbtoene  "
              f"{min(held):6.0f} bis {max(held):5.0f} ms"
              f"{'   FALSCHALARM' if bad and not wrong else ''}"
              f"{'   DURCHGERUTSCHT' if bad and wrong else ''}{short}")

    print()
    if shortfalls[""] and shortfalls["height"]:
        worst_ok = max(shortfalls[""])
        best_error = min(shortfalls["height"])
        print("HOEHE  (Konstante: BEND_TOLERANCE_CENTS = "
              f"{BEND_TOLERANCE_CENTS:.0f} Cent)")
        print(f"  Richtige Bends fehlen bis zu   {worst_ok * 100:+6.0f} Cent")
        print(f"  Der zu flache fehlt mindestens {best_error * 100:+6.0f} Cent")
        _window(worst_ok * 100, best_error * 100, "Cent")
    if holds[""] and holds["hold"]:
        print()
        print("HALTEN  (Konstante: BEND_HOLD_FRACTION = "
              f"{BEND_HOLD_FRACTION:.0%} der geschriebenen Haltezeit)")
        print(f"  Richtige Bends halten mindestens {min(holds['']):6.0f} ms")
        print(f"  Der nicht gehaltene haelt bis zu {max(holds['hold']):6.0f} ms")
        _window(max(holds["hold"]), min(holds[""]), "ms")

    # -- and what the app itself says, which is the only thing the player
    # -- ever sees. Same audio, real matcher, real rule.
    print()
    print(f"{'Take':22s} {'gruen':>6s} {'gelb':>5s}   erwartet")
    print("-" * 80)
    wrong_verdicts = 0
    for take_id, target, broken in TAKES:
        if take_id not in takes:
            continue
        take = takes[take_id]
        written = take["expected_midi"][0][0]
        found = contours(directory / take["file"], written)
        states = [verdict(c, target, written, release=take_id.endswith("release"))
                  for c in found]
        green = sum(1 for st in states if st == MatchType.HIT)
        yellow = sum(1 for st in states if st == MatchType.CLOSE)
        want = "alle gelb" if broken else "alle gruen"
        ok = (yellow == len(states)) if broken else (green == len(states))
        wrong_verdicts += not ok
        print(f"{take_id:22s} {green:6d} {yellow:5d}   {want:12s} "
              f"{'OK' if ok else '<<< FALSCH'}")

    print()
    if failures or wrong_verdicts:
        print(f"{failures + wrong_verdicts} Take(s) werden falsch beurteilt.")
    else:
        print("Jeder richtig gespielte Bend bleibt gruen, jeder absichtliche "
              "Fehler wird gelb.")
    return 1 if (failures or wrong_verdicts) else 0


def _window(low: float, high: float, unit: str) -> None:
    """Say whether a threshold can sit between the two readings at all."""
    if high > low:
        print(f"  -> Platz dazwischen: {high - low:.0f} {unit}. "
              f"Der Schwellenwert muss hier hinein.")
    else:
        print(f"  -> KEIN Platz: richtig und falsch ueberlappen sich um "
              f"{low - high:.0f} {unit}.")
        print("     Dann hilft kein Schwellenwert, sondern nur eine andere "
              "Regel.")


def newest_with_bends():
    root = REPO_ROOT / "reference_recordings"
    for path in sorted(root.glob("*/manifest.json"), reverse=True):
        ids = {t["id"] for t in json.loads(path.read_text())["takes"]}
        if "60_bend_full_ok" in ids:
            return path.parent
    return None


if __name__ == "__main__":
    sys.exit(main())
