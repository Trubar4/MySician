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
"""

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.audio.detector import PitchDetector  # noqa: E402
from pickhero.audio.note_utils import freq_to_midi_exact  # noqa: E402
from pickhero.matcher import (  # noqa: E402
    BEND_HOLD_FRACTION, BEND_MIN_SAMPLES, BEND_TOLERANCE_CENTS,
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


def contours(path: Path, written_midi: int):
    """One pitch contour per strike: [(ms since the strike, semitones), ...]."""
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    detector = PitchDetector(buf_size=4096, hop_size=HOP, sample_rate=rate)
    out: list[list[tuple[float, float]]] = []
    frames_since_onset = 0
    for i in range(0, len(audio) - HOP + 1, HOP):
        detector.process(audio[i:i + HOP])
        ms = i * 1000.0 / rate
        if detector.last_is_onset:
            out.append([])
            frames_since_onset = 0
            start_ms = ms
            continue
        if not out:
            continue
        frames_since_onset += 1
        if frames_since_onset <= SKIP_FRAMES or ms - start_ms > MAX_NOTE_MS:
            continue
        if detector.last_freq > 0 and \
                detector.last_confidence >= detector.confidence_threshold:
            out[-1].append((ms - start_ms,
                            freq_to_midi_exact(detector.last_freq) - written_midi))
    return [c for c in out if len(c) >= BEND_MIN_SAMPLES]


def measure(contour):
    """(highest reached, longest unbroken hold at the top in ms).

    The hold is measured against the take's OWN peak rather than against a
    written target, and in milliseconds rather than as a fraction: these takes
    have no tab behind them, so there is no written hold to take a fraction
    of. What can be measured is how long a hold lasts when somebody means it.
    """
    reached = max(semis for _, semis in contour)
    tolerance = BEND_TOLERANCE_CENTS / 100.0
    longest = run_start = None
    for ms, semis in contour:
        if abs(semis - reached) <= tolerance:
            if run_start is None:
                run_start = ms
            longest = max(longest or 0.0, ms - run_start)
        else:
            run_start = None
    return reached, longest or 0.0


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
        readings = [measure(c) for c in found]
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

    print()
    if failures:
        print(f"{failures} Take(s) werden von der Hoehenregel falsch "
              f"beurteilt.")
    else:
        print("Die Hoehenregel laesst jeden richtigen Bend gruen und faengt "
              "den zu flachen.")
    return 1 if failures else 0


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
