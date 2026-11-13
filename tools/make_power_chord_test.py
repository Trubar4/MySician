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

The roots cycle E A C G D B, and that order is what makes a shape CHANGE
attributable: consecutive distinct roots are at least three semitones apart,
so a strike landing near a bar line can only explain one of the two shapes
either side of it.

## One shape per bar

Inside a bar the shape repeats -- 8 strikes at eighths, 4 at quarters -- and
that is both what a riff is and what makes this file playable at the tempo
that makes it the uncovered case. The player said so after trying the first
version: a chord that changes every 214 ms is not a thing anyone plays, and
a real riff is about five strikes of one shape before it moves.

The first version changed shape on every strike because it carried the
TIMING test's rule across: no repeat of a pitch inside half a second, since
there is otherwise no telling "late against this one" from "early against
the next". That rule belongs where it came from and not here. This file
measures CONVICTION, and every repeat of a shape has the identical expected
pitches -- so whichever repeat a strike is credited to, the verifier reads
the same audio and asks the same question of the same three strings.

Confirmed in the code rather than assumed: the refusal is `_unambiguous`,
which is reached only from `_record_timing_sample`. The scoring search takes
the nearest pitch and refuses nothing on a tie, and the chord credit does not
look at pitch at all. The timing report could not read this material in any
case -- a strummed power chord comes back subharmonic, so nothing within a
semitone of any written note is ever found.

What is genuinely given up is per-strike attribution inside a bar. Since a
bar now holds one shape, the per-SHAPE breakdown the comparison wants is
intact, and the bar is the unit the run log already reports in.
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
#
# No root above the 4th fret, so that --offset 1 still keeps the octave at or
# under the 7th: `note_filter` reaches the MATCHER, not only the drawing, so a
# fret a player's filter hides is a note that is never scored, and the wrong
# copy has to live under the same ceiling as the right one.
ROOTS = [(6, 0), (6, 3), (5, 1), (5, 4), (5, 0), (5, 3)]

# A window needs a gap wider than this to survive `_limit_pending_windows`.
WINDOW_NEEDS_MS = MIN_WINDOW_MS + SKIP_MS + NEXT_STRIKE_GUARD_MS


def shape(root, *effects):
    """The three-string power chord on `root`, low string first."""
    string, fret = root
    return [(string, fret) + effects,
            (string - 1, fret + 2) + effects,
            (string - 2, fret + 2) + effects]


def _sections(bars_dense: int, offset: int = 0):
    """(label, bars) where a bar is a list of (duration, notes) in 4/4.

    `offset` moves every shape that many frets up and changes NOTHING else --
    same strings, same bars, same durations, same tempo. That is what lets one
    take of the moved copy be scored against the unmoved tab: song time is the
    same millisecond in both, so no re-alignment is wanted and the only
    difference reaching the verifier is the pitch.
    """
    roots = [(s, f + offset) for s, f in ROOTS]
    chords = [shape(r) for r in roots]
    muted = [shape(r, palm_mute()) for r in roots]

    # ONE running cycle across every block that follows, not one per block.
    # Restarting it at a boundary put the same root either side of the join,
    # and a shape CHANGE has to be attributable even where the repeats inside
    # a bar deliberately are not.
    at = [0]

    def bar(source, strikes):
        """One shape, struck `strikes` times -- which is what a riff is."""
        chord = source[at[0] % len(source)]
        at[0] += 1
        return [chord] * strikes

    # One chord on beat 1, then three beats of silence. The verifier always
    # reaches these, so they are the control: whatever the dense blocks do,
    # a wrong chord HAS to be convicted here.
    isolated = [[(QUARTER, c), (QUARTER, REST), (QUARTER, REST),
                 (QUARTER, REST)] for c in chords]

    quarter_bars = [[(QUARTER, c) for c in bar(chords, 4)]
                    for _ in range(2)]
    eighth_bars = [[(EIGHTH, c) for c in bar(chords, 8)]
                   for _ in range(bars_dense)]
    muted_bars = [[(EIGHTH, c) for c in bar(muted, 8)]
                  for _ in range(bars_dense)]

    return [
        ("Count-in", [[(QUARTER, REST)] * 4]),
        ("Isolated power chords - the verifier always reaches these", isolated),
        ("Quarter-note power chords - one shape a bar", quarter_bars),
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
            lines.append(f"  {len(bars):2d} bars  {'':3s}  {'':31s}  {label}")
            continue
        worst = min(gap[t] for t in times)
        reached = sum(1 for t in times if gap[t] > WINDOW_NEEDS_MS)
        strikes_total += len(times)
        reached_total += reached
        shown = "inf" if worst == float("inf") else f"{worst:4.0f} ms"
        per_bar = f"{len(times) // len(bars)}/bar"
        lines.append(
            f"  {len(bars):2d} bars  {len(times):3d} chords {per_bar:>6s}  "
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
    ap.add_argument("--offset", type=int, default=0,
                    help="move every shape this many frets up (default 0; "
                         "1 writes the deliberately wrong copy)")
    ap.add_argument("--out", default=None, help="output .gp5 path")
    args = ap.parse_args()

    if args.offset < 0:
        print("--offset cannot be negative: the lowest root is fret 0, "
              "so the wrong copy has to go UP.")
        return 2
    sections = _sections(args.bars, args.offset)
    title = f"Power Chord Test {args.tempo} BPM"
    suffix = ""
    if args.offset:
        title += f" +{args.offset} fret"
        suffix = f"_up{args.offset}"
    song = build_song(title, args.tempo, sections)
    out = Path(args.out) if args.out else (
        REPO_ROOT / "songs" / f"power_chord_test_{args.tempo}bpm{suffix}.gp5"
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
    if args.offset:
        print(f"\nThis is the WRONG copy, every shape {args.offset} fret(s)"
              " up. Play it as")
        print("written and correct nothing: it is scored against the "
              "unmoved tab,")
        print("which is what asks whether a wrong root is still convicted.")
    else:
        print("\nPlay this one as written. The wrong copy is the same file")
        print("with --offset 1, and both takes are scored against THIS tab.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
