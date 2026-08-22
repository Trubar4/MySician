"""What does crediting a pitchless chug buy, and what does it cost?

`NoteMatcher._palm_mute_credit` counts a written palm mute when the strike
came back with no pitch at all. It is leniency, and the only rule in the app
granted without a recording behind it: every palm-muted take recorded so far
is a power CHORD, which a different and properly measured rule already
credits. A single chug had never been recorded.

Block 7 is that recording, and this reads it. Two numbers decide the rule, and
neither of them is an opinion:

- **What it buys.** How many correctly played chugs arrive with no pitch, and
  are therefore lost without the rule. If that is near zero the rule buys
  nothing and should go.
- **What it costs.** How many chugs on the WRONG fret it credits. A wrong
  fret sounds a different pitch, and a strike carrying a pitch never reaches
  this rule -- so the cost should be small. "Should be" is exactly what this
  project does not accept.

    python tools/record_reference.py --block 7
    python tools/check_palm_mute.py

For the error take the tab is the CORRECT shape, as everywhere here: the
manifest records what was played, and asking whether the wrong note is the
wrong note it was told to expect proves nothing.

Exits non-zero if the rule credits more wrong chugs than correct ones -- the
point at which it is doing harm rather than making up for the detector.
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
HOP = 512
# (take played, take whose shape the TAB writes, is a deliberate error)
CASES = [
    ("70_chug_slow_ok", "70_chug_slow_ok", False),
    ("71_chug_fast_ok", "71_chug_fast_ok", False),
    ("73_chug_riff", "73_chug_riff", False),
    ("72_chug_fast_sharp", "71_chug_fast_ok", True),
]


def capture(path: Path, sample_rate: int):
    """Strikes and verification windows, straight out of the audio thread."""
    with wave.open(str(path)) as handle:
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

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


def score(strikes, windows, written_midi: int, credit: bool):
    """Play the take against a tab writing one palm-muted note per strike."""
    notes = [
        NoteEvent(timestamp_ms=s.timestamp_ms, duration_ms=200.0,
                  midi_note=written_midi, string=6, fret=0, palm_mute=True)
        for s in strikes
    ]
    timeline = Timeline(notes, SongMetadata(title="chugs", tempo=150))
    matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                          late_window_ms=300.0, chord_verifier=ChordVerifier(),
                          palm_mute_credit=credit)
    for strike in strikes:
        matcher.process_detected_notes([strike], strike.timestamp_ms)
    matcher.process_strike_windows(windows)
    matcher.process_detected_notes(
        [], max(n.timestamp_ms for n in notes) + 5000)
    # GREEN only. A chug one fret off comes back a semitone away, which the
    # app rightly calls "close" and colours yellow -- counting that as
    # credited would hide the very thing this tool is looking for.
    return matcher.get_statistics()["hits"], len(notes)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=None, help="a take set containing block 7")
    args = ap.parse_args()

    directory = Path(args.dir) if args.dir else newest_with_chugs()
    if directory is None:
        print("Keine Aufnahme mit Block 7 gefunden. Aufnehmen mit:")
        print("  python tools/record_reference.py --block 7")
        print()
        print("Bis dahin ist die Palm-Mute-Nachsicht die einzige Regel der "
              "App ohne Messung dahinter.")
        return 1

    manifest = json.loads((directory / "manifest.json").read_text())
    takes = {t["id"]: t for t in manifest["takes"]}
    sample_rate = int(manifest.get("samplerate", 48000))

    print(f"{directory.name}\n")
    print(f"{'Take':22s} {'Anschlaege':>10s} {'ohne Ton':>9s} "
          f"{'gruen ohne':>11s} {'mit Regel':>10s} {'Unterschied':>12s}")
    print("-" * 80)
    bought = cost = 0
    for take_id, tab_id, is_error in CASES:
        if take_id not in takes or tab_id not in takes:
            print(f"{take_id:22s}  fehlt")
            continue
        strikes, windows = capture(directory / takes[take_id]["file"],
                                   sample_rate)
        if not strikes:
            print(f"{take_id:22s}  kein Anschlag erkannt")
            continue
        written = takes[tab_id]["expected_midi"][0][0]
        unpitched = sum(1 for s in strikes if s.note.unpitched)
        before, total = score(strikes, windows, written, credit=False)
        after, _ = score(strikes, windows, written, credit=True)
        gained = after - before
        if is_error:
            cost += gained
        else:
            bought += gained
        print(f"{take_id:22s} {len(strikes):10d} {unpitched:9d} "
              f"{f'{before}/{total}':>11s} {f'{after}/{total}':>10s} "
              f"{gained:+12d}"
              f"{'   <- falsch gespielt' if is_error else ''}")

    print()
    print(f"Die Regel rettet {bought} richtig gespielte Chugs "
          f"und laesst {cost} falsche durch.")
    if bought == 0 and cost == 0:
        print("Sie aendert hier gar nichts: kein Chug kam ohne Tonhoehe an.")
        print("Dann kauft die Nachsicht nichts und sollte wieder weg.")
    elif cost > bought:
        print("Sie kostet mehr, als sie bringt. So ist sie nicht zu halten.")
    else:
        print("Ein falscher Bund klingt weiter als falsche TONHOEHE und wird "
              "normal erkannt;")
        print("diese Regel spricht nur fuer Anschlaege, die gar keine tragen.")
    return 1 if cost > bought else 0


def newest_with_chugs():
    root = REPO_ROOT / "reference_recordings"
    for path in sorted(root.glob("*/manifest.json"), reverse=True):
        ids = {t["id"] for t in json.loads(path.read_text())["takes"]}
        if "70_chug_slow_ok" in ids:
            return path.parent
    return None


if __name__ == "__main__":
    sys.exit(main())
