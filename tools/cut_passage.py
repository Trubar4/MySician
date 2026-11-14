"""Cut a passage out of a real tab and write it as its own GP5.

Two takes of different bars are not a comparison, so a measurement that needs
the same passage played twice needs the passage to be a FILE. "Find bar 103
again" is a confound, not a chore -- the player said so about the power-chord
test and he was right.

With `--offset N` it writes a second copy with every fret N higher. Both are
then scored against the tab they were NOT played from, which is the same
one-semitone discrepancy either way, and the difference between the two
scores is the conviction rate. See tools/check_power_chord_credit.py.

What it refuses rather than guesses:

  * a passage whose onsets are not all on the grid it writes. Milliseconds
    cannot be read back into a note value -- `duration_quarters` is what the
    tab WROTE, and where that does not line up the cut would move onsets.
  * bars of different lengths. gp_builder writes one tempo, so a passage
    crossing a tempo change would come out at the wrong speed.
  * two notes of one stroke on one string. GP5's played-strings byte has one
    bit per string, so the second is written and never read back and every
    byte after it is garbage -- the file simply does not open.

And it checks its own claim through the real loader before it exits, because
a quietly wrong cut is worse than no cut.

Usage:

    python tools/cut_passage.py "songs/Godsmack - Awake.gp" --bars 103-116
    python tools/cut_passage.py "songs/Godsmack - Awake.gp" --bars 103-116 --offset 1
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gp_builder import (  # noqa: E402
    REST, build_song, dead, palm_mute, write,
)

from pickhero.audio.chord_verify import (  # noqa: E402
    MIN_WINDOW_MS, NEXT_STRIKE_GUARD_MS, SKIP_MS,
)
from pickhero.tabs.loader import load_gp_file  # noqa: E402

WINDOW_FLOOR_MS = MIN_WINDOW_MS + SKIP_MS + NEXT_STRIKE_GUARD_MS

# A written note value, as GP spells it: (value, dotted). Nothing is inferred
# from milliseconds -- these are the quarter lengths a tab can actually hold.
VALUES = {
    4.0: (1, False), 3.0: (2, True), 2.0: (2, False), 1.5: (4, True),
    1.0: (4, False), 0.75: (8, True), 0.5: (8, False), 0.375: (16, True),
    0.25: (16, False), 0.125: (32, False),
}
# Rests are filled with the longest value that fits AND lands on its own
# boundary, so a bar of chugs does not come out as sixteen sixteenth rests.
RESTS = [(4.0, (1, False)), (2.0, (2, False)), (1.0, (4, False)),
         (0.5, (8, False)), (0.25, (16, False))]

GRID = 0.25            # the passage is written on sixteenths or it is refused
EPS = 1e-6


def strokes_of(timeline, bar):
    """{quarters from the bar's start: [note, ...]}, in string order."""
    quarter = (bar.end_ms - bar.start_ms) / (4.0 * bar.beats / bar.beat_type)
    out = {}
    for note in timeline.notes:
        if not (bar.start_ms <= note.timestamp_ms < bar.end_ms):
            continue
        at = (note.timestamp_ms - bar.start_ms) / quarter
        key = round(at / GRID) * GRID
        if abs(at - key) > 0.02:
            raise SystemExit(
                f"bar {bar.index}: a note sits {abs(at - key) * quarter:.0f} ms "
                f"off the grid this writes -- refusing to move it")
        out.setdefault(round(key, 6), []).append(note)
    for key, notes in out.items():
        notes.sort(key=lambda n: n.string)
        strings = [n.string for n in notes]
        if len(set(strings)) != len(strings):
            raise SystemExit(
                f"bar {bar.index}: two notes of one stroke on string "
                f"{strings} -- GP5 cannot write that")
    return out


def _value(quarters, where):
    key = min(VALUES, key=lambda v: abs(v - quarters))
    if abs(key - quarters) > 0.01:
        raise SystemExit(f"{where}: {quarters:.4f} quarters is not a note value")
    return VALUES[key]


def _rests(quarters):
    """Rest beats filling `quarters`, longest first and aligned."""
    out, at = [], 0.0
    while quarters > EPS:
        for length, value in RESTS:
            if length <= quarters + EPS and abs(at % length) < EPS:
                out.append((value, REST))
                at += length
                quarters -= length
                break
        else:
            length, value = RESTS[-1]
            out.append((value, REST))
            at += length
            quarters -= length
    return out


def bar_beats(timeline, bar, offset):
    """[(duration, notes)] filling the bar exactly."""
    total = 4.0 * bar.beats / bar.beat_type
    found = strokes_of(timeline, bar)
    beats, at = [], 0.0
    for key in sorted(found):
        beats.extend(_rests(key - at))
        at = key
        notes = found[key]
        # The stroke lasts until the next one, or its own written length --
        # whichever is shorter, since a rest after a chug is part of the riff.
        nxt = min((k for k in found if k > key), default=total)
        held = min(max(n.duration_quarters for n in notes), nxt - key)
        value = _value(held, f"bar {bar.index} at {key} quarters")
        spec = []
        for n in notes:
            effects = []
            if n.palm_mute:
                effects.append(palm_mute())
            if n.dead:
                effects.append(dead())
            spec.append((n.string, n.fret + offset, *effects))
        beats.append((value, spec))
        at = key + held
    beats.extend(_rests(total - at))
    return beats


def cut(path, track, first, last, offset):
    timeline = load_gp_file(str(path), track_index=track)
    bars = [m for m in timeline.measures if first <= m.index <= last]
    if not bars:
        raise SystemExit(f"no bars {first}-{last} in this track")
    lengths = {round(m.end_ms - m.start_ms, 1) for m in bars}
    if len(lengths) != 1:
        raise SystemExit(
            f"bars {first}-{last} are not all the same length ({sorted(lengths)} "
            f"ms) -- this writes one tempo, so a tempo change has to be the cut")
    signatures = {(m.beats, m.beat_type) for m in bars}
    if len(signatures) != 1:
        raise SystemExit(f"more than one time signature: {sorted(signatures)}")
    beats_per_bar, beat_type = signatures.pop()
    length = lengths.pop()
    quarters = 4.0 * beats_per_bar / beat_type
    tempo = int(round(60000.0 * quarters / length))
    count_in = [((4, False), REST)] * int(quarters)
    sections = [("Count-in", [count_in]),
                (f"bars {first}-{last}",
                 [bar_beats(timeline, m, offset) for m in bars])]
    # String 1 (high) first, as GP numbers them -- and the SOURCE tuning
    # whatever the offset. The offset moves the FRETS, so a tuning moved with
    # them would put the copy two semitones up while every fret still read one
    # higher, which `check` compares pitches to catch.
    tuning = [timeline.metadata.tuning[s] for s in range(1, 7)]
    return sections, tempo, tuning, timeline, bars


def check(written, timeline, bars, offset, track_name):
    """The cut really is the passage, read back through the app's own loader."""
    out = load_gp_file(str(written), track_index=0)
    want = [n for m in bars for n in timeline.notes
            if m.start_ms <= n.timestamp_ms < m.end_ms]
    want.sort(key=lambda n: (n.timestamp_ms, n.string))
    bar_len = bars[0].end_ms - bars[0].start_ms
    got = [n for n in out.notes if n.timestamp_ms >= bar_len - 1.0]
    got.sort(key=lambda n: (n.timestamp_ms, n.string))
    if len(got) != len(want):
        raise SystemExit(f"wrote {len(got)} notes, the passage has {len(want)}")
    base = bars[0].start_ms - bar_len
    bad = 0
    for a, b in zip(want, got):
        same = (a.string == b.string and a.fret + offset == b.fret
                and a.midi_note + offset == b.midi_note
                and abs((a.timestamp_ms - base) - b.timestamp_ms) < 2.0
                and a.palm_mute == b.palm_mute and a.dead == b.dead)
        if not same:
            bad += 1
            if bad <= 5:
                print(f"  MISMATCH  tab s{a.string}/f{a.fret}@"
                      f"{a.timestamp_ms - base:.0f} pm={a.palm_mute}  ->  "
                      f"s{b.string}/f{b.fret}@{b.timestamp_ms:.0f} "
                      f"pm={b.palm_mute}")
    if bad:
        raise SystemExit(f"{bad} of {len(want)} notes did not survive the write")
    print(f"  {len(got)} notes, every string, fret, time and mute as written"
          + (f", every pitch exactly +{offset}" if offset else ""))
    return out


def report(out, label):
    strokes = {}
    for n in out.notes:
        strokes.setdefault(round(n.timestamp_ms, 1), []).append(n)
    at = sorted(strokes)
    gaps = [b - a for a, b in zip(at, at[1:])]
    dropped = sum(1 for g in gaps if g < WINDOW_FLOOR_MS)
    chords = sum(1 for t in at if len(strokes[t]) >= 2)
    print(f"  {label}: {len(at)} strokes ({chords} of them chords), "
          f"{len(out.notes)} notes, {out.duration_ms / 1000:.1f}s")
    print(f"  gaps {min(gaps):.0f}-{max(gaps):.0f} ms, "
          f"{len(gaps) - dropped} windows arrive, {dropped} dropped "
          f"(a window needs {WINDOW_FLOOR_MS:.0f} ms)")


def main():
    ap = argparse.ArgumentParser(description="Cut a passage into its own GP5.")
    ap.add_argument("source")
    ap.add_argument("--track", type=int, default=0)
    ap.add_argument("--bars", required=True, help="first-last, inclusive")
    ap.add_argument("--offset", type=int, default=0,
                    help="semitones to move every fret, for the wrong copy")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    first, last = (int(x) for x in args.bars.split("-"))
    sections, tempo, tuning, timeline, bars = cut(
        Path(args.source), args.track, first, last, args.offset)
    stem = args.out or (f"songs/{Path(args.source).stem} bars {first}-{last}"
                        + (f" up{args.offset}" if args.offset else "") + ".gp5")
    name = f"{Path(args.source).stem} bars {first}-{last}"
    song = build_song(name + (f" +{args.offset}" if args.offset else ""),
                      tempo, sections, track_name="Play this", tuning=tuning)
    write(song, stem)
    print(f"{stem}   {tempo} BPM, tuning {tuning}")
    out = check(Path(stem), timeline, bars, args.offset, name)
    report(out, "written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
