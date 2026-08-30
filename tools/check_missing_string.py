"""Can the verifier tell a string that was PLAYED from one that was left out?

The chord credit is what makes a six-string chord score 94 % from a single
strike, and the chord verifier is what is supposed to police it. But the
verifier convicts only on positive evidence of a WRONG pitch, never on the
absence of the right one -- so a chord where three of six strings never sound
still passes. That is a deliberate decision (`chord_verify.py`), and this asks
whether it could be reversed: is a missing string measurable at all?

The reference set was recorded for exactly this. `23_E5_root_only` plays the
root of a power chord and leaves the fifth out; `33_Emaj_no_high_e` plays an E
major without the high e. Both are scored against the CORRECT shape, because
the manifest records what was PLAYED -- telling the verifier to expect the
error asks it whether the error is the error it was given.

    python tools/check_missing_string.py

Prints the evidence in dB for every expected string, played against left out.
Exits non-zero only if it cannot find the takes; the numbers are the answer.
"""

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.audio.chord_verify import (  # noqa: E402
    ChordVerifier, skip_samples, window_samples,
)
from pickhero.audio.detector import PitchDetector  # noqa: E402
from pickhero.audio.input import OnsetPitchCollector  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
HOP = 512
GATE_DB = -60.0
# (take, the take whose shape is the CORRECT one, which note was left out)
CASES = [
    ("20_E5_ok", "20_E5_ok", None),
    ("23_E5_root_only", "20_E5_ok", "highest"),
    ("30_Emaj_ok", "30_Emaj_ok", None),
    ("33_Emaj_no_high_e", "30_Emaj_ok", "highest"),
    ("34_Amin_ok", "34_Amin_ok", None),
    ("36_Dmaj_ok", "36_Dmaj_ok", None),
]


def load(path: Path):
    with wave.open(str(path)) as handle:
        channels, rate = handle.getnchannels(), handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, rate


def strikes_of(audio, rate):
    detector = PitchDetector(buf_size=4096, hop_size=HOP, sample_rate=rate)
    detector.set_noise_gate_db(GATE_DB)
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


def shape_of(take):
    return sorted({m for sh in take["expected_midi"]
                   for m in ([sh] if isinstance(sh, int) else sh)})


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="reference_recordings/20260814_160019")
    args = ap.parse_args()

    directory = REPO_ROOT / args.dir
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        print(f"{manifest_path} gibt es nicht.")
        return 1
    manifest = json.loads(manifest_path.read_text())
    takes = {t["id"]: t for t in manifest["takes"]}
    rate_hint = int(manifest.get("samplerate", 48000))
    verifier = ChordVerifier()

    print("Beweislage (level_db) fuer jeden erwarteten Ton.")
    print("o = wirklich gespielt, X = absichtlich weggelassen\n")
    played, absent, by_size = [], [], {}
    for take_id, shape_id, missing in CASES:
        if take_id not in takes or shape_id not in takes:
            print(f"{take_id}: fehlt")
            continue
        expected = shape_of(takes[shape_id])
        gone = max(expected) if missing == "highest" else None
        audio, rate = load(directory / takes[take_id]["file"])
        rate = rate or rate_hint
        print(f"{take_id:22} erwartet {expected}"
              + (f"   ohne {gone}" if gone else "   (korrekt)"))
        for strike in strikes_of(audio, rate)[:3]:
            start = strike.sample_pos + skip_samples(rate)
            end = start + window_samples(rate)
            if end > len(audio):
                continue
            verdicts = verifier.verify(audio[start:end], rate, expected)
            cells = []
            for note in expected:
                level = verdicts[note].level_db
                mark = "X" if note == gone else "o"
                cells.append(f"{note}{mark}={level:6.1f}")
                bucket = by_size.setdefault(len(expected), ([], []))
                (absent if note == gone else played).append(level)
                (bucket[1] if note == gone else bucket[0]).append(level)
            print("     " + "  ".join(cells))

    print()
    for size in sorted(by_size):
        yes, no = by_size[size]
        line = (f"{size} Saiten: gespielt n={len(yes)} "
                f"Median {np.median(yes):6.1f} dB, "
                f"10. Perzentil {np.percentile(yes, 10):6.1f}")
        if no:
            line += (f"  |  weggelassen n={len(no)} "
                     f"Median {np.median(no):6.1f}, "
                     f"90. Perzentil {np.percentile(no, 90):6.1f}")
            gap = np.percentile(yes, 10) - np.percentile(no, 90)
            line += f"  ->  {'Abstand' if gap > 0 else 'UEBERLAPPUNG'} {abs(gap):.0f} dB"
        print(line)
    print("\nEin Abstand heisst: eine fehlende Saite waere messbar. Eine "
          "Ueberlappung heisst,\ndass keine Schwelle existiert -- die "
          "Gutschrift laesst sich dort nicht schaerfen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
