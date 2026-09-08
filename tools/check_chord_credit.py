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
from take_harness import events, feed, strikes_of  # noqa: E402
from pickhero.config import Config  # noqa: E402
from pickhero.matcher import NoteMatcher  # noqa: E402
from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = REPO_ROOT / "reference_recordings" / "20260814_160019"

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
    # Power chords. Two strings, and the shape this player spends most of a
    # song on -- which is why the credit reaches down to two. The fifth of a
    # power chord cannot be CONFIRMED (its partials are a subset of the
    # root's), but a fifth on the wrong fret is a different pitch and is
    # convicted normally, which is what these four takes are here to hold.
    ("20_E5_ok", "20_E5_ok", False),
    ("21_E5_sharp", "20_E5_ok", True),
    ("22_E5_flat", "20_E5_ok", True),
    ("24_G5_ok", "24_G5_ok", False),
    ("25_G5_sharp", "24_G5_ok", True),
    ("26_A5_ok", "26_A5_ok", False),
    ("27_E5_palm", "27_E5_palm", False),
    ("28_E5_palm_sharp", "27_E5_palm", True),
    ("40_E5_fast", "20_E5_ok", False),
]


def score(take, strings, pitches, credit_pitchless: bool):
    """Play the take against a tab that writes `pitches` at every strike."""
    notes = [
        NoteEvent(timestamp_ms=s.timestamp_ms, duration_ms=400.0,
                  midi_note=pitch, string=string, fret=2)
        for s in strikes_of(take) for string, pitch in zip(strings, pitches)
    ]
    timeline = Timeline(sorted(notes, key=lambda n: n.timestamp_ms),
                        SongMetadata(title="reference", tempo=100))
    matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                          late_window_ms=300.0, chord_verifier=ChordVerifier())
    if not credit_pitchless:
        matcher._unpitched_chord_credit = lambda *a, **k: None
    feed(matcher, take)
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
        take = events(directory / takes[take_id]["file"], sample_rate)
        strikes = strikes_of(take)
        if not strikes:
            print(f"{take_id:20s}  no strikes detected -- skipped")
            continue
        strings = sorted((int(k) for k in takes[tab_id]["shapes"][0]), reverse=True)
        pitches = sorted(takes[tab_id]["expected_midi"][0])
        before, total, _ = score(take, strings, pitches, False)
        after, _, caught = score(
            events(directory / takes[take_id]["file"], sample_rate),
            strings, pitches, True)

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
