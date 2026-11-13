"""Generate a GP5 for the one power-chord case no reference take covers.

`check_chord_credit.py` holds four deliberate one-fret errors on power chords
-- 21_E5_sharp, 22_E5_flat, 25_G5_sharp, 28_E5_palm_sharp -- and every one of
them is still caught. What they all are is ISOLATED two-string chords with
long gaps, where the chord verifier always gets its 341 ms of audio.

A real metal song is neither. The player's own take of Marilyn Manson's
"Sweet Dreams" reads 53 three-string power chords, 97 % of them green -- and
110 of the 154 credited notes rest on nothing but the strum being heard,
because `_limit_pending_windows` dropped 462 of its 538 windows for want of a
gap. That is where the credit is load-bearing and it is the one place no take
has ever asked whether a WRONG chord is still convicted there.

So this file puts the same three-string shape at four densities and says of
itself which of them the verifier can reach. Play it twice: once correctly,
once with every shape one fret higher. The tab stays the CORRECT one -- the
manifest records what was PLAYED, and telling the verifier to expect the
wrong note asks it whether the error is the error it was given.

Usage:

    python tools/make_power_chord_test.py                 # 140 BPM -> songs/
    python tools/make_power_chord_test.py --tempo 120
    python tools/make_power_chord_test.py --out /tmp/x.gp5

## Why these frets and this order

Standard tuning, so nothing has to be retuned before a take, and the whole
shape slides: one fret higher moves the ROOT, which is the only string a
wrong power chord can be convicted on. The fifth and the octave are partials
of the root and can never be CONFIRMED -- that is why crediting them is safe
and why this measurement is about the root.

Nothing goes above the 7th fret. The app's fret filter is a range the player
can set, and a diagnostic whose notes the filter silently removes measures
the filter instead of the chords; the first run of the timing test lost a
sixth of its notes exactly that way.

The roots cycle E A C G D B, and that order is not decoration: a strike is
only attributable where exactly ONE tab note can explain it, so neighbouring
roots are at least three semitones apart, and a root does not come round
again inside half a second at any tempo this file is meant for.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gp_builder import (  # noqa: E402
    EIGHTH, QUARTER, REST, build_song, palm_mute, write,
)

from pickhero.audio.chord_verify import (  # noqa: E402
    MIN_WINDOW_MS, NEXT_STRIKE_GUARD_MS, SKIP_MS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# (root string, root fret). A root on string 6 puts its fifth and octave on
# 5 and 4; a root on string 5 puts them on 4 and 3. Both at root + 2 frets,
# which is the shape every guitarist already has under their hand.
ROOTS = [(6, 0), (6, 5), (5, 3), (6, 3), (5, 5), (5, 2)]

# A window needs a gap wider than this to survive `_limit_pending_windows`.
WINDOW_NEEDS_MS = MIN_WINDOW_MS + SKIP_MS + NEXT_STRIKE_GUARD_MS


def shape(root, *effects):
    """The three-string power chord on `root`, low string first."""
    string, fret = root
    return [(string, fret) + effects,
            (string - 1, fret + 2) + effects,
            (string - 2, fret + 2) + effects]


def _sections(bars_dense: int):
    """(label, bars) where a bar is a list of (duration, notes) in 4/4."""
    chords = [shape(r) for r in ROOTS]
    muted = [shape(r, palm_mute()) for r in ROOTS]

    # ONE running cycle across every block that follows, not one per block.
    # Restarting it at a boundary put the same root 428 ms either side of the
    # join -- and a strike is only counted where exactly one tab note can
    # explain it, so that is two notes nobody can measure.
    at = [0]

    def cycle(source, n):
        taken = [source[(at[0] + i) % len(source)] for i in range(n)]
        at[0] += n
        return taken

    # One chord on beat 1, then three beats of silence. The verifier always
    # reaches these, so they are the control: whatever the dense blocks do,
    # a wrong chord HAS to be convicted here.
    isolated = [[(QUARTER, c), (QUARTER, REST), (QUARTER, REST),
                 (QUARTER, REST)] for c in chords]

    quarter_bars = [[(QUARTER, c) for c in cycle(chords, 4)]
                    for _ in range(2)]
    eighth_bars = [[(EIGHTH, c) for c in cycle(chords, 8)]
                   for _ in range(bars_dense)]
    muted_bars = [[(EIGHTH, c) for c in cycle(muted, 8)]
                  for _ in range(bars_dense)]

    return [
        ("Count-in", [[(QUARTER, REST)] * 4]),
        ("Isolated power chords - the verifier always reaches these", isolated),
        ("Quarter-note power chords", quarter_bars),
        ("Eighth-note power chords - the case no take covers", eighth_bars),
        ("Palm-muted eighths - the same case, choked", muted_bars),
    ]


def _report(sections, path):
    """Say which blocks the verifier can reach, reading the FILE back.

    A file whose dense block is not actually dense measures the covered case
    twice over, and what decides it is the TEMPO rather than anything written
    here -- so the arithmetic is printed. It is read off the written tab
    through the app's own loader rather than off the plan above: the first
    version of this took each strike's own note length for the gap to the
    next one, which is true of a run of eighths and wrong by a factor of
    three for an isolated chord followed by rests.
    """
    from pickhero.tabs.loader import load_gp_file

    timeline = load_gp_file(path, track_index=0)
    onsets = sorted({n.timestamp_ms for n in timeline.notes})
    strings = {t: sum(1 for n in timeline.notes if n.timestamp_ms == t)
               for t in onsets}
    # The gap that decides a window is the one to the next strike anywhere in
    # the song, so the last chord of a block is measured against the first of
    # the one after it.
    gap = {t: (onsets[i + 1] - t if i + 1 < len(onsets) else float("inf"))
           for i, t in enumerate(onsets)}

    # Which section a bar belongs to, by where the sections end.
    ends, at = [], 0
    for label, bars in sections:
        at += len(bars)
        ends.append((at, label))

    def section_of(bar):
        for end, label in ends:
            if bar < end:
                return label
        return ends[-1][1]

    lines, strikes_total, reached_total = [], 0, 0
    for (end, label), bars in zip(ends, (b for _, b in sections)):
        times = [t for t in onsets
                 if strings[t] and section_of(_bar_of(timeline, t)) == label]
        if not times:
            lines.append(f"  {len(bars):2d} bars  {'':3s}  {'':25s}  {label}")
            continue
        worst = min(gap[t] for t in times)
        reached = sum(1 for t in times if gap[t] > WINDOW_NEEDS_MS)
        strikes_total += len(times)
        reached_total += reached
        shown = "inf" if worst == float("inf") else f"{worst:4.0f} ms"
        lines.append(
            f"  {len(bars):2d} bars  {len(times):3d} chords  "
            f"tightest gap {shown:>7s}  "
            f"{reached:2d} windows arrive, {len(times) - reached:2d} dropped"
            f"   {label}")
    return lines, (strikes_total, reached_total), timeline.duration_ms


def _bar_of(timeline, ms):
    """The bar a time falls in, from the tab's own measures."""
    for measure in timeline.measures:
        if measure.start_ms <= ms < measure.end_ms:
            return measure.index
    return timeline.measures[-1].index if timeline.measures else 0


def main():
    ap = argparse.ArgumentParser(description="Power-chord test tab.")
    ap.add_argument("--tempo", type=int, default=140,
                    help="beats per minute (default 140, where an eighth is "
                         "214 ms and the window is dropped)")
    ap.add_argument("--bars", type=int, default=4,
                    help="bars in each dense block (default 4 = 32 chords)")
    ap.add_argument("--out", default=None, help="output .gp5 path")
    args = ap.parse_args()

    sections = _sections(args.bars)
    song = build_song(f"Power Chord Test {args.tempo} BPM", args.tempo,
                      sections)
    out = Path(args.out) if args.out else (
        REPO_ROOT / "songs" / f"power_chord_test_{args.tempo}bpm.gp5"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    write(song, out)

    lines, (strikes, reached), length_ms = _report(sections, out)
    bars = len(song.tracks[0].measures)
    print(f"Written: {out}")
    print(f"  {bars} bars at {args.tempo} BPM  (~{length_ms / 1000:.0f} s)")
    print(f"  {strikes} power chords, {strikes * 3} written notes")
    print("  Track 1 'Play this'  |  Track 2 'Click' sounds only on a chord")
    print(f"\nA verification window needs a gap over {WINDOW_NEEDS_MS:.0f} ms:")
    print("\n".join(lines))
    print(f"\n  {reached} chords the verifier reaches, "
          f"{strikes - reached} it does not.")
    if not strikes - reached:
        print("  Nothing here is dense enough to be the uncovered case -- "
              "raise --tempo.")
    print("\nRecord it twice with Shift+W: once correctly, once with every")
    print("shape one fret higher. Do not change the tab.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
