"""Does a line across the strings survive being left to ring?

The claim in `CLAUDE.md` was measured on synthesis: a line walking across the
neck with the strings it left still sounding is polyphony, and monophonic YIN
reports one pitch for it -- 3 of 8 against 8 of 8 damped. The player's own
play-along takes disagree, going 40 for 40 across string changes, but every
one of those changes sits in a slow passage where the previous note has
decayed anyway. Neither reading settles it.

Block 5 of `record_reference.py` does: the same six-note line, twice damped
and twice ringing, slow and fast. Same guitar, same player, same session, so
the damping and the speed are the only things that differ -- and whatever
separates the takes IS the effect.

    python tools/record_reference.py --block 5
    python tools/analyze_ringing.py reference_recordings/<stamp>

Scored by SEQUENCE rather than by clock: the line is played freely with no
click, so what can be checked is whether the notes come back in the order
they were played, which is exactly the question. A strike whose pitch is not
the next expected note is reported with what it was instead, because "read as
the neighbouring string" and "read as a mixture of two" are different faults.
"""

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.audio.detector import PitchDetector  # noqa: E402
from pickhero.audio.input import OnsetPitchCollector  # noqa: E402
from pickhero.audio.note_utils import midi_to_name  # noqa: E402

HOP = 512
# The four takes, paired so each row of the output is one honest comparison.
PAIRS = [
    ("langsam", "50_across_slow_damped", "51_across_slow_ringing"),
    ("schnell", "52_across_fast_damped", "53_across_fast_ringing"),
]


def strikes_of(path: Path):
    """Every strike the app would see, with its pitch."""
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    detector = PitchDetector(buf_size=4096, hop_size=HOP, sample_rate=rate)
    collector = OnsetPitchCollector()
    out = []
    for i in range(0, len(audio) - HOP + 1, HOP):
        detector.process(audio[i:i + HOP])
        strike = collector.process_frame(
            detector.last_freq, detector.last_confidence,
            detector.last_is_onset, i * 1000.0 / rate,
            detector.confidence_threshold, sample_pos=i,
        )
        if strike is not None:
            out.append(strike)
    return out


def score_sequence(strikes, expected):
    """Walk the strikes against the expected line, in order.

    The player was asked for two passes up and down, but may have played one
    or three -- so the expected sequence is repeated as far as the strikes
    reach rather than assumed. A strike is credited when it carries the note
    the line is up to; octave equivalence applies, since the matcher grants it
    too and a wound string slipping an octave is not the fault under test.
    """
    right = []
    wrong = []
    unpitched = 0
    for index, strike in enumerate(strikes):
        want = expected[index % len(expected)]
        if strike.note.unpitched:
            unpitched += 1
            continue
        got = strike.note.midi_note
        distance = abs(got - want)
        if distance == 0 or distance % 12 == 0:
            right.append(got)
        else:
            wrong.append((index, want, got))
    return right, wrong, unpitched


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", help="reference_recordings/<stamp>")
    ap.add_argument("--detail", action="store_true",
                    help="list every strike that came back wrong")
    args = ap.parse_args()

    session = Path(args.session)
    manifest = json.loads((session / "manifest.json").read_text())
    takes = {t["id"]: t for t in manifest["takes"]}

    missing = [tid for _, a, b in PAIRS for tid in (a, b) if tid not in takes]
    if missing:
        print("Diese Aufnahmen fehlen in dieser Session:")
        for tid in missing:
            print(f"  {tid}")
        print("\nAufnehmen mit:  python tools/record_reference.py --block 5")
        return 1

    print(f"{'':>10} {'gedaempft':>22} {'klingen gelassen':>22}")
    print("-" * 58)
    verdicts = []
    for label, damped_id, ringing_id in PAIRS:
        row = []
        for take_id in (damped_id, ringing_id):
            take = takes[take_id]
            expected = [notes[0] for notes in take["expected_midi"]]
            # Down and back up, which is what the instruction asks for.
            expected = expected + expected[-2:0:-1]
            strikes = strikes_of(session / take["file"])
            right, wrong, unpitched = score_sequence(strikes, expected)
            total = len(right) + len(wrong)
            share = 100 * len(right) / total if total else 0.0
            row.append((share, len(right), total, unpitched, wrong))
        (ds, dr, dt, du, _), (rs, rr, rt, ru, rw) = row
        verdicts.append((label, ds, rs))
        print(f"{label:>10} {f'{dr}/{dt}  {ds:5.1f} %':>22} "
              f"{f'{rr}/{rt}  {rs:5.1f} %':>22}")
        if du or ru:
            print(f"{'':>10} {f'({du} ohne Tonhoehe)':>22} "
                  f"{f'({ru} ohne Tonhoehe)':>22}")
        if args.detail and rw:
            for index, want, got in rw[:10]:
                print(f"{'':>12}Anschlag {index + 1}: erwartet "
                      f"{midi_to_name(want)}, gehoert {midi_to_name(got)} "
                      f"({abs(got - want)} Halbtoene)")

    print()
    worst = min((r - d) for _, d, r in verdicts)
    if worst > -10:
        print("Klingende Saiten kosten hier nichts Messbares.")
        print("Die synthetische Messung (3/8 gegen 8/8) ueberzeichnet den Fall,")
        print("und es gibt an dieser Stelle nichts zu bauen.")
    else:
        print(f"Klingende Saiten kosten bis zu {-worst:.0f} Prozentpunkte.")
        print("Der Fall ist real und lohnt eine Behandlung -- der naechste")
        print("Schritt ist, die geschriebene Note gegen die Obertoene zu")
        print("pruefen, statt der gemeldeten Tonhoehe blind zu glauben.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
