"""Does the partial check rescue the notes that ringing strings swallow?

A line played across the strings without damping loses strikes to pitchlessness
-- 24 points of usable strikes on the fast take, measured by
`tools/analyze_ringing.py`. Nothing comes back WRONG; the strikes simply carry
no pitch, because the ringing neighbours leave monophonic YIN no single period.

So the matcher holds such a strike when a single note is written there and asks
`ChordVerifier.confirms` whether that written pitch is present in the audio
afterwards. This runs the real path over the block 5 takes and counts what that
buys, and -- more importantly -- what it costs on the DAMPED takes, which are
the control: a rescue that fires there is crediting a note nobody played.

    python tools/check_ringing_rescue.py
    python tools/check_ringing_rescue.py --dir reference_recordings/<stamp>

Exits non-zero if a damped take gains anything, since that would mean the
check is inventing notes rather than recovering them.
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
from pickhero.matcher import MatchType, NoteMatcher  # noqa: E402
from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_ringing import align  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
HOP = 512
TAKES = [
    ("langsam, gedaempft", "50_across_slow_damped", False),
    ("langsam, klingend", "51_across_slow_ringing", True),
    ("schnell, gedaempft", "52_across_fast_damped", False),
    ("schnell, klingend", "53_across_fast_ringing", True),
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


def intended(strikes, line):
    """Which written note each strike was meant to be.

    The take has no click, so the tab is reconstructed from what was asked
    for: the line up and back down, as many times as the strikes reach.

    Aligned rather than walked, and it has to be. Walking the two lists in
    step treats every stray pick noise as a note and shifts the whole rest of
    the take by one -- the damped control then appears to GAIN a note from the
    rescue, and the tool convicts the very thing it exists to test. Anchoring
    on the pitched strikes alone is no better: it stalls the moment a note is
    missing, which is precisely the case under test.

    So the same Needleman-Wunsch alignment the analysis uses, which tolerates
    both an extra strike and a missing one. A pitchless strike still pairs
    with the note it replaced, because pairing it costs less than gapping both
    sides.
    """
    sequence = (line + line[::-1]) * 4
    detected = [None if s.note.unpitched else s.note.midi_note for s in strikes]
    return [(strikes[i], sequence[j]) for i, j in align(detected, sequence)]


def score(pairs, windows, verify: bool):
    """Play the take against a tab written at the strikes' own times."""
    notes = [
        NoteEvent(timestamp_ms=strike.timestamp_ms, duration_ms=300.0,
                  midi_note=want, string=6, fret=5)
        for strike, want in pairs
    ]
    if not notes:
        return 0, 0
    timeline = Timeline(notes, SongMetadata(title="ringing", tempo=100))
    matcher = NoteMatcher(timeline, timing_window_ms=150.0,
                          late_window_ms=300.0,
                          chord_verifier=ChordVerifier() if verify else None)
    for strike, _ in pairs:
        matcher.process_detected_notes([strike], strike.timestamp_ms)
    matcher.process_strike_windows(windows)
    matcher.process_detected_notes(
        [], max(n.timestamp_ms for n in notes) + 5000)
    hits = sum(1 for n in notes
               if matcher.get_note_state(n) in (MatchType.HIT, MatchType.CLOSE))
    return hits, len(notes)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=None, help="a take set with block 5")
    args = ap.parse_args()

    directory = Path(args.dir) if args.dir else newest_with_block5()
    if directory is None:
        print("Keine Aufnahme mit Block 5 gefunden. Aufnehmen mit:")
        print("  python tools/record_reference.py --block 5")
        return 1
    manifest = json.loads((directory / "manifest.json").read_text())
    takes = {t["id"]: t for t in manifest["takes"]}
    sample_rate = int(manifest.get("samplerate", 48000))

    print(f"{directory.name}\n")
    print(f"{'Take':22s} {'ohne Rettung':>14s} {'mit Rettung':>13s} "
          f"{'gewonnen':>10s}")
    print("-" * 64)
    failures = 0
    for label, take_id, ringing in TAKES:
        if take_id not in takes:
            print(f"{label:22s}  fehlt")
            continue
        take = takes[take_id]
        strikes, windows = capture(directory / take["file"], sample_rate)
        line = [notes[0] for notes in take["expected_midi"]]
        pairs = intended(strikes, line)
        before, total = score(pairs, windows, verify=False)
        after, _ = score(pairs, windows, verify=True)
        gained = after - before
        # A damped take is the control. Its strikes carry a pitch, so there is
        # nothing to rescue -- anything gained there is a note being invented.
        bad = (not ringing) and gained > 0
        failures += bad
        print(f"{label:22s} {f'{before}/{total}':>14s} {f'{after}/{total}':>13s} "
              f"{gained:>+10d}{'   INVENTIERT' if bad else ''}")

    print()
    if failures:
        print(f"{failures} gedaempfte(r) Take(s) haben dazugewonnen -- die "
              f"Rettung erfindet Noten und ist so nicht sicher.")
    else:
        print("Kein gedaempfter Take gewinnt etwas dazu: gerettet wird nur, "
              "was wirklich klang.")
    return 1 if failures else 0


def newest_with_block5():
    """The most recent take set that actually contains block 5."""
    root = REPO_ROOT / "reference_recordings"
    candidates = []
    for path in sorted(root.glob("*/manifest.json"), reverse=True):
        ids = {t["id"] for t in json.loads(path.read_text())["takes"]}
        if "53_across_fast_ringing" in ids:
            candidates.append(path.parent)
    return candidates[0] if candidates else None


if __name__ == "__main__":
    sys.exit(main())
