"""How many of the written picks does the onset detector actually hear?

A strike that never arrives cannot be recovered by the matcher, the chord
verifier or any rescue -- so this is the first number to know about a take,
and the one place where a threshold decides how much of the song is even
audible to the rest of the app.

    python tools/sweep_onset_threshold.py
    python tools/sweep_onset_threshold.py --session <stamp> --run-log <file>

The alignment and the practice tempo are fitted ONCE, at the lowest threshold
in the sweep, and reused for every step: re-fitting per threshold would let a
threshold that deletes half the strikes also choose the grid it is judged
against. A tool must not gate on the value it is meant to question.

`--run-log` reads the written notes out of a MySician run log instead of a GP
file, so a take of a song that is not in `songs/` can still be scored. The log
prints every written note with its time, string and pitch, which is all a tab
needs to be here.
"""

import argparse
import bisect
import json
import sys
import wave
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_play_along import (  # noqa: E402
    HOP, MATCH_MS, best_alignment, check_tempo)
from pickhero.audio.detector import PitchDetector  # noqa: E402
from pickhero.audio.input import OnsetPitchCollector  # noqa: E402
from pickhero.tabs.loader import load_gp_file  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
# Low enough that nearly every pick gets through, so the run at this value is
# the control the others are aligned and compared against.
FLOOR_THRESHOLD = 0.02
GATE_DB = -55.0


def load_wav(path: Path):
    with wave.open(str(path)) as handle:
        rate, channels = handle.getframerate(), handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, rate


def strikes_at(audio, rate, threshold):
    detector = PitchDetector(buf_size=4096, hop_size=HOP, sample_rate=rate)
    detector.set_noise_gate_db(GATE_DB)
    detector._onset.set_threshold(threshold)
    collector = OnsetPitchCollector()
    out = []
    for i in range(0, len(audio) - HOP + 1, HOP):
        detector.process(audio[i:i + HOP])
        strike = collector.process_frame(
            detector.last_freq, detector.last_confidence,
            detector.last_is_onset, i * 1000.0 / rate,
            detector.confidence_threshold, sample_pos=i)
        if strike is not None:
            out.append(strike)
    return out


def notes_from_run_log(path: Path):
    """(timestamp_ms, midi) for every written note a run log lists."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    start = lines.index("note_ms\tstring\tmidi\tverdict") + 1
    out = []
    for line in lines[start:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        out.append((float(parts[0]), int(parts[2])))
    return sorted(set(out))


def score(strikes, notes, offset, tempo):
    """(picks heard, picks heard with the right pitch, spurious strikes)."""
    onsets = sorted({t for t, _ in notes})
    by_onset = defaultdict(list)
    for t, midi in notes:
        by_onset[t].append(midi)
    times = sorted(s.timestamp_ms - offset for s in strikes)
    if not times:
        return 0, 0, 0, len(onsets)
    at = {}
    for strike in strikes:
        at.setdefault(round(strike.timestamp_ms - offset, 3), []).append(strike)
    reach = max(times)
    within = [t for t in onsets if t / tempo <= reach]
    heard = right = 0
    for onset in within:
        played = onset / tempo
        index = bisect.bisect_left(times, played - MATCH_MS)
        if index >= len(times) or times[index] > played + MATCH_MS:
            continue
        heard += 1
        for time in times[index:]:
            if time > played + MATCH_MS:
                break
            for strike in at.get(round(time, 3), []):
                got = strike.note.midi_note
                if got and any((got - m) % 12 == 0 for m in by_onset[onset]):
                    right += 1
                    break
            else:
                continue
            break
    return heard, right, max(0, len(times) - heard), len(within)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", default=None)
    ap.add_argument("--run-log", default=None)
    args = ap.parse_args()

    root = REPO_ROOT / "reference_recordings"
    sessions = ([root / args.session] if args.session else
                sorted(p.parent for p in root.glob("*/play_along.wav")))

    for session in sessions:
        manifest = json.loads((session / "manifest.json").read_text())
        take = next((t for t in manifest["takes"]
                     if t.get("file") == "play_along.wav"), {})
        if args.run_log:
            notes = notes_from_run_log(Path(args.run_log))
            label = Path(args.run_log).name
        else:
            name = take.get("song") or ""
            if not name:
                # A take whose manifest cannot say what was played is not a
                # take, it is 45 seconds of audio. See the `corrected` note.
                print(f"{session.name}: im Manifest steht nicht, welcher "
                      f"Song gespielt wurde — uebersprungen\n")
                continue
            song = REPO_ROOT / "songs" / (
                name if name.endswith(".gp5") else name + ".gp5")
            if not song.exists():
                print(f"{session.name}: Songdatei fehlt ({name})\n")
                continue
            timeline = load_gp_file(str(song))
            notes = sorted({(n.timestamp_ms, n.midi_note)
                            for n in timeline.notes})
            label = song.name

        audio, rate = load_wav(session / "play_along.wav")
        control = strikes_at(audio, rate, FLOOR_THRESHOLD)
        stamps = [s.timestamp_ms for s in control]
        onsets = sorted({t for t, _ in notes})
        stated = take.get("tempo_percent")
        tempo, _ = check_tempo(stamps, onsets,
                               stated / 100.0 if stated else None)
        offset, fitted, tempo = best_alignment(stamps, onsets, tempo)

        print(f"{session.name} — {label}")
        print(f"  Tempo {tempo * 100:.0f} %, Start bei {offset / 1000:.2f}s, "
              f"{fitted} von {len(control)} Anschlaegen aufs Raster")
        print(f"  {'Schwelle':>9}{'Anschlaege':>12}{'Picks gehoert':>16}"
              f"{'richtige Tonhoehe':>19}{'ueberzaehlig':>13}")
        for threshold in (0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40):
            strikes = strikes_at(audio, rate, threshold)
            heard, right, extra, total = score(strikes, notes, offset, tempo)
            print(f"  {threshold:9.2f}{len(strikes):12d}"
                  f"{f'{heard}/{total} ({100 * heard / total:.0f} %)':>16}"
                  f"{f'{right}/{total} ({100 * right / total:.0f} %)':>19}"
                  f"{extra:13d}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
