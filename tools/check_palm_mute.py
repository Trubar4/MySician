"""How often does a single chug arrive with no pitch at all?

This measured a rule out of the app, which is what it was built to be able to
do. `NoteMatcher._palm_mute_credit` used to credit a written palm mute when
the strike came back carrying no pitch, on the argument that a choked string
often gives monophonic YIN nothing to lock onto and that a chug riff is too
fast for the audio window that would otherwise confirm it. Every palm-muted
take at the time was a power CHORD, where a pitchless strike really does run
at 16-20 %, so the argument looked sound.

Block 7 is single chugs, and it says the opposite. On 87 correctly played
chugs a strike arrives pitchless **3 times** -- 3.4 %. On the take played a
fret off, 3.5 %: the same rate. So the leniency would have bought three notes
in eighty-seven, and paid for them by turning two wrong ones green. It was
removed. This tool stays, because a finding nobody can re-run is an opinion.

    python tools/record_reference.py --block 7
    python tools/check_palm_mute.py

It reports what the app does now, and beside it what the removed rule WOULD
have done, so the decision can be re-taken against a better recording.

Two things to know when reading the numbers. For the error take the tab is
the CORRECT shape, as everywhere here -- the manifest records what was played.
And a palm-muted low string is heard an octave above what was played on nearly
every strike, which costs nothing: the matcher grants octave equivalence.

Exits non-zero if a pitchless chug ever becomes commoner than a fifth of
strikes, which is where the rule's original premise would start being true
again.
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


# The share of pitchless strikes at which the removed rule's premise -- that a
# choked string often produces no pitch -- would start holding for single
# notes. It holds for chords, which is why they have a rule of their own.
PREMISE_SHARE = 0.20


def score(strikes, windows, written_midi: int):
    """Play the take against a tab writing one palm-muted note per strike."""
    notes = [
        NoteEvent(timestamp_ms=s.timestamp_ms, duration_ms=200.0,
                  midi_note=written_midi, string=6, fret=0, palm_mute=True)
        for s in strikes
    ]
    timeline = Timeline(notes, SongMetadata(title="chugs", tempo=150))
    matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                          late_window_ms=300.0, chord_verifier=ChordVerifier())
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
    print(f"{'Take':22s} {'Anschlaege':>10s} {'ohne Ton':>9s} {'Quote':>7s} "
          f"{'gruen':>9s} {'Regel haette':>14s}")
    print("-" * 80)
    bought = cost = 0
    played = pitchless_total = 0
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
        green, total = score(strikes, windows, written)
        # What the removed rule would have done: credit exactly the strikes
        # that carried no pitch at all.
        if is_error:
            cost += unpitched
        else:
            bought += unpitched
            played += len(strikes)
        pitchless_total += unpitched
        share = unpitched / max(1, len(strikes))
        print(f"{take_id:22s} {len(strikes):10d} {unpitched:9d} {share:6.1%} "
              f"{f'{green}/{total}':>9s} {f'+{unpitched}':>14s}"
              f"{'   <- falsch gespielt' if is_error else ''}")

    share = bought / max(1, played)
    print()
    print(f"Ein einzelner Chug kommt in {share:.1%} der Anschlaege ohne "
          f"Tonhoehe an ({bought} von {played}).")
    print(f"Die Nachsicht haette davon {bought} gerettet und dabei {cost} "
          f"falsch gegriffene mitgruen gemacht.")
    print()
    if share >= PREMISE_SHARE:
        print("Damit traegt die urspruengliche Annahme wieder: ein Chug "
              "verliert seine Tonhoehe oft genug,")
        print("dass sich die Frage neu stellt. Diese Aufnahme gehoert "
              "angesehen.")
    else:
        print("Die urspruengliche Annahme traegt nicht: ein Chug behaelt "
              "seine Tonhoehe fast immer,")
        print("und wenn nicht, sagt das nichts darueber aus, ob der Bund "
              "stimmte. Deshalb ist die Regel raus.")
    return 1 if share >= PREMISE_SHARE else 0


def newest_with_chugs():
    root = REPO_ROOT / "reference_recordings"
    for path in sorted(root.glob("*/manifest.json"), reverse=True):
        ids = {t["id"] for t in json.loads(path.read_text())["takes"]}
        if "70_chug_slow_ok" in ids:
            return path.parent
    return None


if __name__ == "__main__":
    sys.exit(main())
