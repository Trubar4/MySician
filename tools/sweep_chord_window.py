"""Find how short the chord-verification window may get before it starts lying.

A chord struck soon after the previous one leaves less than the full 341 ms
of clean audio, so the window has to be cut off at the next strike. The
question this answers is not "does a short window still decide" -- abstaining
is fine -- but "does a short window ever decide WRONGLY". That is the only
failure that reaches the player, as a string turning red that was played
right.

Two measurements per window length, from the same reference takes the
thresholds were fitted on:

  false alarms   expected = what the manifest says was actually played, so
                 every "wrong" verdict is a lie
  errors caught  expected = what the TAB called for on the deliberate-error
                 takes, so every miss is a real error going unnoticed

Both must hold: a floor low enough to lie is useless, and so is one so high
that it never catches anything.

    python tools/sweep_chord_window.py reference_recordings/<stamp>
    python tools/sweep_chord_window.py <stamp> --min-hz-seconds 61 90 110
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pickhero.audio.chord_verify as cv  # noqa: E402
from analyze_reference import load_wav, nm, onsets  # noqa: E402

SKIP_MS = 40.0
# Deliberately reaches well below the floor in use. The floor went stale once
# already: the sweep gated on it, printed "below floor" with nothing judged,
# and so could never show that shorter windows had become safe.
DEFAULT_LENGTHS = [341, 300, 280, 260, 240, 220, 200, 190, 180, 170, 160, 140]

# What the TAB asks for on the takes that deliberately play something else.
# The manifest records what was PLAYED, which is what makes it a false-alarm
# reference; this is the other half, and it has to be stated by hand.
TAB_EXPECTS = {
    "21_E5_sharp": [40, 47],
    "22_E5_flat": [40, 47],
    "25_G5_sharp": [43, 50],
    "28_E5_palm_sharp": [40, 47],
    "31_Emaj_G_open": [40, 47, 52, 56, 59, 64],
    "32_Emaj_D_open": [40, 47, 52, 56, 59, 64],
    "35_Amin_B_open": [45, 52, 57, 60, 64],
}


def load_takes(session: Path):
    manifest = json.load(open(session / "manifest.json", encoding="utf-8"))
    takes = [t for t in manifest["takes"]
             if len(t["expected_midi"]) == 1 and len(t["expected_midi"][0]) >= 2]
    audio = {t["id"]: load_wav(session / t["file"]) for t in takes}
    strikes = {tid: onsets(x, sr) for tid, (x, sr) in audio.items()}
    return manifest, takes, audio, strikes


def verdicts_for(verifier, take, expected, win_ms, audio, strikes):
    """Majority verdict per string over every strike in the take."""
    x, sr = audio[take["id"]]
    skip = int(SKIP_MS / 1000 * sr)
    length = int(win_ms / 1000 * sr)
    seen: dict[int, list[int]] = {}
    for t0 in strikes[take["id"]]:
        start = int(t0 * sr) + skip
        segment = x[start:start + length]
        if len(segment) < length:
            continue
        for midi, v in verifier.verify(segment, sr, expected).items():
            if v.decided:
                seen.setdefault(midi, []).append(v.played_midi)
    return {m: max(set(p), key=p.count) for m, p in seen.items() if p}


def evaluate(win_ms, takes, audio, strikes, verbose=False):
    verifier = cv.ChordVerifier()
    judged = alarms = caught = errors = 0
    for take in takes:
        played = sorted(set(take["expected_midi"][0]))
        for midi, winner in verdicts_for(
            verifier, take, played, win_ms, audio, strikes
        ).items():
            judged += 1
            if winner != midi:
                alarms += 1
                if verbose:
                    print(f"      FALSE ALARM  {take['id']:20s} "
                          f"{nm(midi)} called {nm(winner)}")
        tab = TAB_EXPECTS.get(take["id"])
        if not tab:
            continue
        got = verdicts_for(verifier, take, sorted(set(tab)), win_ms, audio, strikes)
        for midi in set(tab) - set(played):     # the strings played wrong
            if midi in got:
                errors += 1
                if got[midi] != midi:
                    caught += 1
                elif verbose:
                    print(f"      MISSED ERROR {take['id']:20s} {nm(midi)}")
    return judged, alarms, caught, errors


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", help="reference_recordings/<stamp>")
    ap.add_argument("--lengths", type=int, nargs="+", default=DEFAULT_LENGTHS,
                    help="window lengths in ms")
    ap.add_argument("--min-hz-seconds", type=float, nargs="+", default=None,
                    help="try these MIN_HZ_SECONDS values (default: the one "
                         "chord_verify actually uses)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="name every false alarm and missed error")
    args = ap.parse_args()

    session = Path(args.session)
    manifest, takes, audio, strikes = load_takes(session)
    print(f"{manifest['device']}  {manifest['samplerate']} Hz")
    floor_in_use = cv.MIN_WINDOW_MS
    print(f"{len(takes)} chord takes, floor in use = {floor_in_use:.0f} ms\n")
    # Lift the floor for the duration: verify() refuses below it, so leaving it
    # in place makes every shorter length report nothing judged and no false
    # alarms -- which reads as "no data" but looks like "nothing to gain", and
    # is how the floor came to sit 80 ms above where the takes allowed.
    cv.MIN_WINDOW_MS = 1.0

    for k in (args.min_hz_seconds or [cv.MIN_HZ_SECONDS]):
        cv.MIN_HZ_SECONDS = k
        print(f"MIN_HZ_SECONDS = {k:.1f}")
        print(f"  {'window':>8}  {'judged':>6}  {'false alarms':>12}  "
              f"{'errors caught':>13}")
        for win_ms in args.lengths:
            judged, alarms, caught, errors = evaluate(
                win_ms, takes, audio, strikes, verbose=args.verbose
            )
            usable = "" if win_ms >= floor_in_use else "   below the floor in use"
            print(f"  {win_ms:6d}ms  {judged:6d}  {alarms:12d}  "
                  f"{caught:6d} / {errors:<4d}{usable}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
