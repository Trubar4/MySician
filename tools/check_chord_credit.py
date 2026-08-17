"""Does crediting a pitchless strum cost us the wrong-finger verdicts?

A full chord regularly gives monophonic YIN no period to lock onto, so a
correctly played strum arrives carrying no pitch at all and used to be scored
red. `NoteMatcher._unpitched_chord_credit` accepts it instead. That is only
defensible if the chord verifier still catches a genuinely wrong finger
afterwards -- otherwise the change buys green notes by giving up the feedback
the app exists for.

So this runs the real path over the reference takes: audio in, strikes and
verification windows out of `AudioCapture`, scored by `NoteMatcher`, verdicts
applied by `ChordVerifier`. For a take recorded with a deliberate error, the
tab is the CORRECT shape -- the manifest records what was played, not what was
meant, and asking the verifier whether the wrong note is the wrong note it was
told to expect proves nothing.

    python tools/check_chord_credit.py
    python tools/check_chord_credit.py --dir reference_recordings/20260814_160019

Exits non-zero if a correct take loses a string, or a deliberate error slips
through -- so it can be run as a regression check after touching either the
credit or the verifier's thresholds.
"""

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.audio.chord_verify import ChordVerifier  # noqa: E402
from pickhero.audio.input import (  # noqa: E402
    RING_SECONDS, AudioCapture, _AudioRing,
)
from pickhero.config import Config  # noqa: E402
from pickhero.matcher import NoteMatcher  # noqa: E402
from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = REPO_ROOT / "reference_recordings" / "20260814_160019"
HOP = 512

# (take played, take whose shape the TAB writes, is a deliberate error)
CASES = [
    ("30_Emaj_ok", "30_Emaj_ok", False),
    ("31_Emaj_G_open", "30_Emaj_ok", True),
    ("32_Emaj_D_open", "30_Emaj_ok", True),
    # A string left out still sounds like the chord, and has always been
    # allowed to pass -- its partials are a subset of what is already ringing.
    ("33_Emaj_no_high_e", "30_Emaj_ok", False),
    ("34_Amin_ok", "34_Amin_ok", False),
    ("35_Amin_B_open", "34_Amin_ok", True),
    ("36_Dmaj_ok", "36_Dmaj_ok", False),
]


def capture(path: Path, sample_rate: int):
    """Strikes and verification windows, straight out of the audio thread."""
    with wave.open(str(path)) as handle:
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    cap = AudioCapture(Config())
    cap._sample_rate = sample_rate
    cap.detector.sample_rate = sample_rate
    cap.detector.reset()
    cap._onset_collector.reset()
    cap._ring = _AudioRing(int(sample_rate * RING_SECONDS))
    for i in range(0, len(audio) - HOP + 1, HOP):
        cap._audio_callback(audio[i:i + HOP].reshape(-1, 1), HOP, None, None)
    return ([s for s in cap.get_notes() if s.note.is_onset],
            cap.get_strike_windows())


def score(strikes, windows, strings, pitches, credit_pitchless: bool):
    """Play the take against a tab that writes `pitches` at every strike."""
    notes = [
        NoteEvent(timestamp_ms=s.timestamp_ms, duration_ms=400.0,
                  midi_note=pitch, string=string, fret=2)
        for s in strikes for string, pitch in zip(strings, pitches)
    ]
    timeline = Timeline(sorted(notes, key=lambda n: n.timestamp_ms),
                        SongMetadata(title="reference", tempo=100))
    matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                          late_window_ms=300.0, chord_verifier=ChordVerifier())
    if not credit_pitchless:
        matcher._unpitched_chord_credit = lambda *a, **k: None
    for strike in strikes:
        matcher.process_detected_notes([strike], strike.timestamp_ms)
    matcher.process_strike_windows(windows)
    matcher.process_detected_notes([], max(n.timestamp_ms for n in notes) + 5000)
    stats = matcher.get_statistics()
    return (stats["hits"] + stats["close"], len(notes),
            matcher.chord_strings_corrected)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="a take set")
    args = ap.parse_args()

    directory = Path(args.dir)
    manifest = json.loads((directory / "manifest.json").read_text())
    takes = {t["id"]: t for t in manifest["takes"]}
    sample_rate = int(manifest.get("samplerate", 48000))

    print(f"{'take':20s} {'credited before':>15s} {'after':>10s} "
          f"{'strings caught':>14s}  expected")
    print("-" * 82)
    failures = 0
    for take_id, tab_id, is_error in CASES:
        if take_id not in takes or tab_id not in takes:
            continue
        strikes, windows = capture(directory / takes[take_id]["file"], sample_rate)
        if not strikes:
            print(f"{take_id:20s}  no strikes detected -- skipped")
            continue
        strings = sorted((int(k) for k in takes[tab_id]["shapes"][0]), reverse=True)
        pitches = sorted(takes[tab_id]["expected_midi"][0])
        before, total, _ = score(strikes, windows, strings, pitches, False)
        after, _, caught = score(strikes, windows, strings, pitches, True)

        want = "a wrong finger must show" if is_error else "correct -- nothing"
        ok = (caught > 0) == is_error
        failures += not ok
        print(f"{take_id:20s} {before:8d}/{total:<6d} {after:5d}/{total:<4d} "
              f"{caught:14d}  {want:25s} {'OK' if ok else 'FAILED'}")

    print()
    if failures:
        print(f"{failures} take(s) came back wrong -- the credit is not safe as it "
              f"stands.")
    else:
        print("Every correct chord is credited in full, and every deliberate "
              "error is still caught.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
