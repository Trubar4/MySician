"""Is a WRONG power chord still convicted where the verifier gets no window?

The one case `check_chord_credit.py` cannot reach. Its four error takes are
ISOLATED two-string chords with long gaps, where a verification window always
arrives -- and the player's own metal song is three-string chords at 214 ms
spacing, where 86 % of the windows are dropped and the credit is doing the
work on its own.

**It needs no deliberately mis-fretted take, which is the finding that made
this cheap.** Scoring a CORRECT take against the tab a semitone higher is the
same one-semitone discrepancy as scoring a wrong take against the correct
tab: either way what was played and what the tab writes are one fret apart.
So the measurement comes out of the same recording, twice.

**Either tab may be the one that was played**, and the manifest says which,
so a real error take is read the same way round: score it against the tab it
was NOT played from and the discrepancy is again one fret. Swapped, the
played note is a semitone BELOW the written one; a take of the up1 copy puts
it ABOVE, which is the direction a mis-fretted hand really produces -- and
the partials are not provably symmetric about the written pitch, so both are
measured rather than one inferred from the other.

Usage:

    python tools/check_power_chord_credit.py
    python tools/check_power_chord_credit.py --dir reference_recordings/20261113_184013

Exits non-zero when either half of the trade moves: a correct take must stay
above MIN_CORRECT green, and the gap to the semitone-wrong reading must stay
above MIN_GAP. Loosening the credit shrinks the gap; tightening it drops the
correct score. The two together bracket it.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import make_power_chord_test as gen  # noqa: E402
import take_harness as th  # noqa: E402

from pickhero.audio.chord_verify import ChordVerifier  # noqa: E402
from pickhero.audio.take import song_ms_at  # noqa: E402
from pickhero.config import Config  # noqa: E402
from pickhero.matcher import MatchType, NoteMatcher  # noqa: E402
from pickhero.tabs.loader import load_gp_file  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SONGS = REPO / "songs"
DEFAULT_DIRS = [
    REPO / "reference_recordings" / "20261113_184013",
    REPO / "reference_recordings" / "20261113_183934",
    REPO / "reference_recordings" / "20261113_190742",
]

RIGHT = "power_chord_test_140bpm.gp5"
WRONG = "power_chord_test_140bpm_up1.gp5"

# A record of what was measured on 2026-11-13, with margin -- not a fitted
# bound. The three takes read 96.2 %, 88.9 % and 91.9 % green against the tab
# they were played from, and 34.6 %, 35.5 % and 30.3 % against the tab a
# semitone away. The last of those is the up1 copy, i.e. the error direction.
MIN_CORRECT = 85.0
MIN_GAP = 45.0

# What the app does on that material. The hit window is the player's own
# setting; at 150 and at 200 the answer here is the same to a few notes.
TIMING_WINDOW_MS = 200.0
LATE_WINDOW_MS = 300.0


def blocks():
    """(label, bar range) read off the generator, not written down twice."""
    out, at = [], 0
    for label, bars in gen._sections(4, 0):
        if not label.startswith("Count-in"):
            out.append((label.split(" - ")[0], range(at, at + len(bars))))
        at += len(bars)
    return out


def score(wav, take, rate, tab, strum=True):
    """Verdicts per bar, through the real capture path and matcher."""
    timeline = load_gp_file(str(tab), track_index=0)
    events = th.events(Path(wav), rate, Config())
    matcher = NoteMatcher(timeline, timing_window_ms=TIMING_WINDOW_MS,
                          late_window_ms=LATE_WINDOW_MS,
                          chord_verifier=ChordVerifier())
    if not strum:
        matcher._unpitched_chord_credit = lambda *a, **k: None
    for kind, item in events:
        if kind == "strike":
            sample = int(item.timestamp_ms * rate / 1000.0)
            song = song_ms_at(take, sample, rate)
            if song is None:
                return None
            # The app matches a strike at its adjusted stamp, which IS the
            # song position; nothing is left for an offset to do.
            item.timestamp_ms = song
            matcher.process_detected_notes([item], song)
        else:
            matcher.process_strike_windows([item])
    last = max((i.timestamp_ms for k, i in events if k == "strike"), default=0.0)
    matcher.process_detected_notes([], last + 1000.0)

    per_bar, strum_credit = {}, 0
    for note in timeline.notes:
        bar = next(m.index for m in timeline.measures
                   if m.start_ms <= note.timestamp_ms < m.end_ms)
        state = matcher._get_state(note)
        green, yellow, red = per_bar.get(bar, (0, 0, 0))
        per_bar[bar] = (green + (state == MatchType.HIT),
                        yellow + (state == MatchType.CLOSE),
                        red + (state == MatchType.MISS))
        if state == MatchType.HIT and matcher.unreliable(note) == "strum":
            strum_credit += 1
    return per_bar, strum_credit, len(th.windows_of(events)), len(
        th.strikes_of(events))


def totals(per_bar, bars=None):
    keys = per_bar if bars is None else [b for b in bars if b in per_bar]
    green = sum(per_bar[b][0] for b in keys)
    yellow = sum(per_bar[b][1] for b in keys)
    red = sum(per_bar[b][2] for b in keys)
    return green, yellow, red


def percent(green, yellow, red):
    total = green + yellow + red
    return 100.0 * green / total if total else 0.0


def tabs_for(take):
    """(as played, a semitone away, what that direction is) off the manifest.

    Either copy may be the one that was played. A take of the up1 copy is
    the error direction: read against the UNMOVED tab, what was played sits
    a semitone ABOVE what is written, which is what a mis-fretted hand does.
    """
    song = take.get("song")
    if song == Path(RIGHT).stem:
        return RIGHT, WRONG, "the played note a semitone BELOW the written one"
    if song == Path(WRONG).stem:
        return WRONG, RIGHT, "the played note a semitone ABOVE the written one"
    return None, None, None


def check(directory):
    """One take, against both tabs. True when the trade still holds."""
    manifest = json.loads((Path(directory) / "manifest.json").read_text())
    take = manifest["takes"][0]
    rate = int(manifest["samplerate"])
    wav = Path(directory) / take["file"]
    played, away, direction = tabs_for(take)
    if played is None:
        print(f"  {directory.name}: recorded against {take.get('song')!r}, "
              f"which is neither test tab -- skipped")
        return True

    right = score(wav, take, rate, SONGS / played)
    wrong = score(wav, take, rate, SONGS / away)
    bare = score(wav, take, rate, SONGS / away, strum=False)
    if right is None or wrong is None or bare is None:
        print(f"  {directory.name}: the manifest carries no mapping -- skipped")
        return True

    print(f"\n{directory.name}   {take['seconds']:.1f} s, "
          f"{right[3]} strikes, {right[2]} windows of them judged")
    print(f"  played {played}, so a semitone wrong is {direction}")
    print(f"  {'block':26s} {'as played':>14s}   "
          f"{'a semitone wrong':>34s}")
    for label, bars in blocks():
        rg, ry, rr = totals(right[0], bars)
        wg, wy, wr = totals(wrong[0], bars)
        print(f"  {label:26s}  green {rg:3d} {percent(rg, ry, rr):5.1f}%"
              f"   green {wg:3d} {percent(wg, wy, wr):5.1f}%"
              f"  yellow {wy:3d}  red {wr:3d}")

    r_pct = percent(*totals(right[0]))
    w_pct = percent(*totals(wrong[0]))
    b_pct = percent(*totals(bare[0]))
    gap = r_pct - w_pct
    print(f"  {'whole take':26s}  green     {r_pct:5.1f}%"
          f"   green     {w_pct:5.1f}%   gap {gap:5.1f} points")
    print(f"  of the green it earns as played, {right[1]} notes rest on a "
          f"strum being heard rather than the note")
    print(f"  with the strum credit OFF, the wrong reading keeps "
          f"{b_pct:.1f}% green and the right one "
          f"{percent(*totals(score(wav, take, rate, SONGS / played, strum=False)[0])):.1f}%")

    ok = True
    if r_pct < MIN_CORRECT:
        print(f"  FAIL: a take read against the tab it was played from is "
              f"{r_pct:.1f}% green, under the {MIN_CORRECT:.0f}% this was "
              f"measured at")
        ok = False
    if gap < MIN_GAP:
        print(f"  FAIL: the gap is {gap:.1f} points, under {MIN_GAP:.0f} -- "
              f"a semitone wrong is being credited more than it was")
        ok = False
    return ok


def main():
    ap = argparse.ArgumentParser(description="Power-chord conviction check.")
    ap.add_argument("--dir", action="append", default=None,
                    help="a take folder (repeatable)")
    args = ap.parse_args()
    dirs = [Path(d) for d in args.dir] if args.dir else DEFAULT_DIRS
    ok = True
    for directory in dirs:
        if not (directory / "manifest.json").exists():
            print(f"  {directory}: no manifest -- skipped")
            continue
        ok = check(directory) and ok
    print("\nOK" if ok else "\nFAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
