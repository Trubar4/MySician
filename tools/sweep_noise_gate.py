"""What does the noise gate actually cost, measured over a real take?

The gate is the one setting that can delete a strike before anything
downstream ever sees it, and a strike that never arrives cannot be recovered
by the matcher, the chord verifier or the rescue. So the question this
answers is not "does the gate work" but "how high may it be before the notes
start disappearing", which is the number an automatic gate has to stay under.

    python tools/sweep_noise_gate.py reference_recordings/<stamp>
    python tools/sweep_noise_gate.py <stamp> --song songs/x.gp5

The alignment and the practice tempo are found ONCE, at the lowest gate in
the sweep, and reused for every step. Re-fitting them per gate would let a
gate that deletes half the strikes also choose the grid it is measured
against -- the tool would be marking its own homework, which is exactly how
sweep_chord_window.py came to print "below floor" with nothing judged.
"""

import argparse
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_play_along import (  # noqa: E402
    HOP, MATCH_MS, best_alignment, check_tempo, load_wav)
from pickhero.audio.detector import PitchDetector  # noqa: E402
from pickhero.audio.input import OnsetPitchCollector  # noqa: E402
from pickhero.tabs.loader import load_gp_file  # noqa: E402
from pickhero.ui.scrolling import (  # noqa: E402
    MAX_GATE_DB, MIN_GATE_DB, NOISE_MARGIN_DB)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Low enough to be out of the way of any real signal, so the run at this gate
# is the control every other one is compared against.
FLOOR_GATE_DB = -80.0


def strikes_at_gate(audio, rate, gate_db):
    """Every strike the app would see with the gate at this level.

    Also returns the share of hops the gate discarded, which is the number the
    run log prints and the only one the player can see.
    """
    detector = PitchDetector(buf_size=4096, hop_size=HOP, sample_rate=rate)
    detector.set_noise_gate_db(gate_db)
    collector = OnsetPitchCollector()
    strikes, hops, under = [], 0, 0
    for i in range(0, len(audio) - HOP + 1, HOP):
        detector.process(audio[i:i + HOP])
        hops += 1
        if detector.last_signal_db < gate_db:
            under += 1
        strike = collector.process_frame(
            detector.last_freq, detector.last_confidence,
            detector.last_is_onset, i * 1000.0 / rate,
            detector.confidence_threshold, sample_pos=i,
        )
        if strike is not None:
            strikes.append(strike)
    return strikes, 100.0 * under / max(1, hops)


def score(strikes, onsets, offset, tempo):
    """(picks that produced a strike, picks heard with the right pitch)."""
    if not onsets:
        return 0, 0
    times = np.array([s.timestamp_ms - offset for s in strikes]) if strikes \
        else np.zeros(0)
    heard = right = 0
    for onset_ms, midi in onsets:
        played_at = onset_ms / tempo
        near = np.abs(times - played_at) <= MATCH_MS if len(times) else []
        if not len(near) or not near.any():
            continue
        heard += 1
        # Octave equivalence, because the matcher grants it on purpose -- a
        # palm-muted low string comes back an octave up 59 times in 61.
        for index in np.flatnonzero(near):
            got = strikes[index].note.midi_note
            if got is not None and (got - midi) % 12 == 0:
                right += 1
                break
    return heard, right


def room_before(audio, rate, offset_ms):
    """The room, from the audio recorded before the song starts.

    Which is what the app measures too -- while the song is not running and
    during the count-in. A low percentile of the PLAYING is not the room: over
    one session's takes that percentile ran from -35 dB on a dense passage to
    -94 dB on a sparse one, against a recorded room of -73.
    """
    pre = audio[:int(offset_ms / 1000.0 * rate)]
    if len(pre) < HOP * 30:
        return None
    detector = PitchDetector(buf_size=4096, hop_size=HOP, sample_rate=rate)
    detector.set_noise_gate_db(MIN_GATE_DB)
    hops = []
    for i in range(0, len(pre) - HOP + 1, HOP):
        detector.process(pre[i:i + HOP])
        hops.append(detector.last_signal_db)
    return statistics.median(hops)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session")
    ap.add_argument("--song", default=None)
    ap.add_argument("--from", dest="lo", type=float, default=-80.0)
    ap.add_argument("--to", dest="hi", type=float, default=-20.0)
    ap.add_argument("--step", type=float, default=5.0)
    args = ap.parse_args()

    session = Path(args.session)
    if not session.exists():
        session = Path("reference_recordings") / args.session
    wav = session / "play_along.wav"
    if not wav.exists():
        print(f"{wav} gibt es nicht.")
        return 1

    import json
    manifest = json.loads((session / "manifest.json").read_text())
    take = next((t for t in manifest.get("takes", [])
                 if t.get("file") == "play_along.wav"), {})
    song = args.song
    if song is None and take.get("song"):
        name = take["song"]
        song = str(REPO_ROOT / "songs" /
                   (name if name.endswith(".gp5") else name + ".gp5"))
    if song is None or not Path(song).exists():
        print("Keine Song-Datei gefunden: --song angeben.")
        return 1
    timeline = load_gp_file(song)
    onsets = sorted({(n.timestamp_ms, n.midi_note) for n in timeline.notes})

    audio, rate = load_wav(wav)
    print(f"{wav}: {len(audio) / rate:.0f}s, {rate} Hz")
    print(f"Song: {Path(song).name} — {len(onsets)} Noten geschrieben")

    # The control: everything below is measured against this one alignment.
    control, control_under = strikes_at_gate(audio, rate, FLOOR_GATE_DB)
    stamps = [s.timestamp_ms for s in control]
    stated = take.get("tempo_percent")
    tempo, note = check_tempo(stamps, [o for o, _ in onsets],
                              stated / 100.0 if stated else None)
    offset, hits, tempo = best_alignment(stamps, [o for o, _ in onsets], tempo)
    if note:
        print(note)
    print(f"Tempo {tempo * 100:.0f} %, Start bei {offset / 1000:.2f}s, "
          f"{hits} Anschlaege aufs Raster\n")

    print(f"{'Gate':>6} {'verworfen':>10} {'Anschlaege':>11} "
          f"{'gehoert':>9} {'richtige Tonhoehe':>18}")
    total = len(onsets)
    best = 0
    gate = args.lo
    while gate <= args.hi + 1e-9:
        strikes, under = strikes_at_gate(audio, rate, gate)
        heard, right = score(strikes, onsets, offset, tempo)
        best = max(best, right)
        print(f"{gate:6.0f} {under:9.0f}% {len(strikes):11d} "
              f"{heard:4d}/{total:<4d} {right:9d}/{total:<4d} "
              f"({100 * right / total:.0f} %)")
        gate += args.step

    # What the automatic would choose, scored against the best gate in the
    # sweep. A rule that picks a value nobody swept is a guess, and this is
    # the only thing here that can fail: the sweep above is a description,
    # the line below is a claim.
    room = room_before(audio, rate, offset)
    if room is None:
        print("\nZu wenig Vorlauf im Mitschnitt, um den Raum zu messen.")
        return 0
    chosen = max(MIN_GATE_DB, min(room + NOISE_MARGIN_DB, MAX_GATE_DB))
    strikes, _ = strikes_at_gate(audio, rate, chosen)
    _, right = score(strikes, onsets, offset, tempo)
    print(f"\nRaum vor dem Song: {room:.1f} dB")
    print(f"Automatik waehlt:  {chosen:.0f} dB "
          f"-> {right}/{total} richtig, bestes im Sweep {best}/{total}")
    if right < best:
        print("FEHLER: die Automatik verliert Noten gegen ein Gate, "
              "das im Sweep steht.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
