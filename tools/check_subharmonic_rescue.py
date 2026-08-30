"""What does a subharmonic strike cost, and what does putting it to the audio buy?

A subharmonic pitch is not a reading of one string. The detector folds it up
from BELOW the guitar's range because several strings ringing together share
that period -- so its value names the chord sounding in the room, not the note
just struck. On an arpeggio, where the tab writes single notes that are meant
to ring into each other, that is most of what the detector produces.

The matcher therefore treats a subharmonic that matches nothing written the
way it treats no pitch at all: it holds the strike and asks the audio whether
the written note is actually present.

    python tools/check_subharmonic_rescue.py

This scores the real play-along takes through the real matcher, with the rule
on and off. It exits non-zero if a take LOSES notes -- the "it must not invent
notes" half is held by check_ringing_rescue.py (whose damped takes are the
control) and by check_chord_credit.py (whose deliberate errors must stay red).
"""

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_play_along import best_alignment, check_tempo  # noqa: E402
from pickhero.audio.chord_verify import ChordVerifier  # noqa: E402
from pickhero.audio.input import (  # noqa: E402
    RING_SECONDS, AudioCapture, _AudioRing,
)
from pickhero.config import Config  # noqa: E402
from pickhero.matcher import MatchType, NoteMatcher  # noqa: E402
from pickhero.tabs.loader import load_gp_file  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
HOP = 512


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


def score(timeline, strikes, windows, offset_ms, tempo, rescue: bool):
    """Play the take against the written tab, exactly as the app would.

    The chord verifier is present on BOTH sides. Comparing the rule against a
    matcher with no verifier at all measures the chord VERDICTS instead, which
    take strings back for their own reasons -- the first version of this tool
    did that and reported a take losing a note to a rule that had not fired.
    """
    matcher = NoteMatcher(
        timeline, timing_window_ms=200.0, late_window_ms=450.0,
        chord_verifier=ChordVerifier(), subharmonic_rescue=rescue)
    # The recording starts before the song does, and a take played at 80 % is
    # stretched against the written grid -- both are found by the alignment
    # and neither is guessed here.
    matcher.audio_offset_ms = -offset_ms
    for strike in strikes:
        strike.timestamp_ms *= tempo
        matcher.process_detected_notes([strike], strike.timestamp_ms - offset_ms)
    for window in windows:
        window.timestamp_ms *= tempo
    matcher.process_strike_windows(windows)
    matcher.process_detected_notes([], timeline.duration_ms + 5000.0)
    hits = sum(1 for n in timeline.notes
               if matcher.get_note_state(n) in (MatchType.HIT, MatchType.CLOSE))
    return hits, matcher.rescued_notes


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=None)
    args = ap.parse_args()

    sessions = ([Path(args.dir)] if args.dir else
                sorted(p.parent for p in
                       (REPO_ROOT / "reference_recordings").glob(
                           "*/play_along.wav")))
    if not sessions:
        print("Keine Play-Along-Aufnahme gefunden.")
        return 1

    print(f"{'Take':20s} {'Anschlaege':>11s} {'subharm.':>9s} "
          f"{'ohne':>8s} {'mit':>8s} {'gerettet':>9s}")
    print("-" * 72)
    failures = 0
    for session in sessions:
        manifest = json.loads((session / "manifest.json").read_text())
        take = next((t for t in manifest["takes"]
                     if t.get("file") == "play_along.wav"), {})
        name = take.get("song", "")
        song = REPO_ROOT / "songs" / (
            name if name.endswith(".gp5") else name + ".gp5")
        if not song.exists():
            print(f"{session.name:20s}  Songdatei fehlt: {song.name}")
            continue
        timeline = load_gp_file(str(song))
        rate = int(manifest.get("samplerate", 48000))

        strikes, windows = capture(session / "play_along.wav", rate)
        stated = take.get("tempo_percent")
        onsets = sorted({n.timestamp_ms for n in timeline.notes})
        stamps = [s.timestamp_ms for s in strikes]
        tempo, _ = check_tempo(stamps, onsets,
                               stated / 100.0 if stated else None)
        offset, _, tempo = best_alignment(stamps, onsets, tempo)

        subharmonic = sum(1 for s in strikes
                          if getattr(s.note, "subharmonic", False))
        # Two independent runs: the matcher mutates the strikes it is given.
        before, _ = score(timeline, *capture(session / "play_along.wav", rate),
                          offset, tempo, rescue=False)
        after, rescued = score(
            timeline, *capture(session / "play_along.wav", rate),
            offset, tempo, rescue=True)
        total = len(timeline.notes)
        lost = after < before
        failures += lost
        print(f"{session.name:20s} {len(strikes):11d} {subharmonic:9d} "
              f"{f'{before}/{total}':>8s} {f'{after}/{total}':>8s} "
              f"{rescued:9d}{'   VERLIERT' if lost else ''}")

    print()
    if failures:
        print(f"{failures} Take(s) verlieren Noten -- die Regel ist so nicht "
              f"sicher.")
        return 1
    print("Kein Take verliert Noten.")
    print("Dass nichts erfunden wird, halten check_ringing_rescue.py "
          "(gedaempfte Takes) und check_chord_credit.py (Fehlgriffe).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
