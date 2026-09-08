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
they were played, which is exactly the question.

The alignment is Needleman-Wunsch and has to be. The first version of this
tool walked the two lists by index, which means one strike carrying no pitch
shifts every comparison after it -- and it duly reported 16 % for the DAMPED
takes, the control case known to work. A tool whose control comes back broken
is measuring itself.
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


# Alignment scores. Walking the two sequences by index does NOT work and the
# first version of this tool proved it: a single strike that carries no pitch
# shifts every comparison after it, and the damped takes -- the control, the
# case known to work -- came back at 16 %. Alignment has to tolerate an extra
# strike and a missing one, or it measures its own bookkeeping.
EXACT, OCTAVE, MISMATCH, GAP = 3, 2, -2, -2


def align(detected, expected):
    """Best correspondence between what was played and what came back.

    Needleman-Wunsch: the standard way to line up two sequences when either
    may have extra or missing entries, which is exactly the situation -- the
    player may play the turning note twice, and the detector may drop one.
    """
    n, m = len(detected), len(expected)
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = i * GAP
    for j in range(1, m + 1):
        score[0][j] = j * GAP
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            got, want = detected[i - 1], expected[j - 1]
            if got is None:
                pair = MISMATCH          # a strike with no pitch matches nothing
            elif got == want:
                pair = EXACT
            elif abs(got - want) % 12 == 0:
                pair = OCTAVE
            else:
                pair = MISMATCH
            score[i][j] = max(score[i - 1][j - 1] + pair,
                              score[i - 1][j] + GAP,
                              score[i][j - 1] + GAP)
    pairs = []
    i, j = n, m
    while i > 0 and j > 0:
        got, want = detected[i - 1], expected[j - 1]
        if got is None:
            pair = MISMATCH
        elif got == want:
            pair = EXACT
        elif abs(got - want) % 12 == 0:
            pair = OCTAVE
        else:
            pair = MISMATCH
        if score[i][j] == score[i - 1][j - 1] + pair:
            # Indices rather than values: the other tool needs the strike that
            # sits at i, not only the pitch it carried.
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif score[i][j] == score[i - 1][j] + GAP:
            i -= 1
        else:
            j -= 1
    return list(reversed(pairs))


def score_sequence(strikes, expected):
    """How the line came back: exactly, an octave out, or as another note.

    The octave column is kept apart rather than folded in, because the matcher
    grants octave equivalence on purpose (the player ruled that an octave slip
    stays green) -- so an octave error costs nothing on screen while still
    being the detector losing its grip, and the two facts belong side by side.
    """
    detected = [None if s.note.unpitched else s.note.midi_note for s in strikes]
    unpitched = sum(1 for d in detected if d is None)
    pairs = [(detected[i], expected[j]) for i, j in align(detected, expected)]
    exact = sum(1 for got, want in pairs if got is not None and got == want)
    octave = sum(1 for got, want in pairs
                 if got is not None and got != want and abs(got - want) % 12 == 0)
    wrong = [(i, want, got) for i, (got, want) in enumerate(pairs)
             if got is not None and abs(got - want) % 12 != 0]
    return exact, octave, wrong, unpitched, len(pairs)


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

    print(f"{'Linie':>10} {'':>26} {'genau':>7} {'Oktave':>7} "
          f"{'falsch':>7} {'ohne Ton':>9}")
    print("-" * 72)
    verdicts = []
    for label, damped_id, ringing_id in PAIRS:
        row = []
        for take_id, how in ((damped_id, "gedaempft"),
                             (ringing_id, "klingen gelassen")):
            take = takes[take_id]
            line = [notes[0] for notes in take["expected_midi"]]
            # Up and back down. The turning note may be struck once or twice;
            # the alignment tolerates either, so the sequence is written the
            # way it is most often played and left to sort itself out.
            expected = (line + line[::-1]) * 3
            strikes = strikes_of(session / take["file"])
            exact, octave, wrong, unpitched, total = score_sequence(
                strikes, expected)
            share = 100 * exact / total if total else 0.0
            row.append((share, exact, octave, wrong, unpitched, total))
            print(f"{label:>10} {how:>26} {f'{exact}/{total}':>7} "
                  f"{octave:>7} {len(wrong):>7} {unpitched:>9}")
            if args.detail and wrong:
                for index, want, got in wrong[:10]:
                    print(f"{'':>14}Note {index + 1}: geschrieben "
                          f"{midi_to_name(want)}, gehoert {midi_to_name(got)} "
                          f"({abs(got - want)} Halbtoene)")
        verdicts.append((label, row[0], row[1]))

    print()
    # The share of strikes that came back with a pitch the app can use. Exact
    # and an octave out both count, because the matcher grants octave
    # equivalence on purpose -- an octave slip stays green on screen. What
    # does NOT count is a strike carrying no pitch at all, which for a single
    # written note is simply a miss.
    def usable(entry):
        share, exact, octave, wrong, unpitched, total = entry
        strikes = exact + octave + len(wrong) + unpitched
        return 100 * (exact + octave) / strikes if strikes else 0.0

    worst = 0.0
    for label, damped, ringing in verdicts:
        gap = usable(ringing) - usable(damped)
        worst = min(worst, gap)
        print(f"{label:>10}: gedaempft {usable(damped):5.1f} % der Anschlaege "
              f"brauchbar, klingend {usable(ringing):5.1f} %  ({gap:+.0f} Punkte)")

    print()
    if worst > -10:
        print("Klingende Saiten kosten hier nichts Messbares.")
        print("An dieser Stelle gibt es nichts zu bauen.")
    else:
        print(f"Klingende Saiten kosten bis zu {-worst:.0f} Prozentpunkte.")
        print("Der Fall ist real. Der naechste Schritt ist, die GESCHRIEBENE")
        print("Note gegen die Obertoene zu pruefen, statt der gemeldeten")
        print("Tonhoehe blind zu glauben.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
