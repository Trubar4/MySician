"""Does the key check find a tab in the wrong key, and stay quiet otherwise?

Usage:
    python tools/check_key_check.py
    python tools/check_key_check.py --fault TAB --control TAB --recording MP3
    python tools/check_key_check.py --quiet "songs/Thunder - Love Walked in 2.gp"

The pair is what makes this a measurement rather than a demonstration: ONE
recording read against two tabs that differ by exactly the fault. The player
transcribed the first ten bars of Carol Of The Bells a whole tone low, and
`tools/transpose_bars.py` wrote the corrected copy -- so the fault take must
report +2 over the opening and the control take must report nothing at all.

Everything else in the songs folder is a second kind of control: six songs
with a recording on this machine, none of which may produce a word.

Re-run it after touching KEY_WINDOW_S, KEY_STEP_S, KEY_LAG_S,
KEY_MIN_WINDOWS or KEY_MARGIN. It exits non-zero when the fault is missed or
a control speaks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.audio import autosync                        # noqa: E402
from pickhero.tabs import songsterr                        # noqa: E402
from pickhero.tabs.loader import list_tracks, load_gp_file  # noqa: E402
from pickhero.tabs.timeline import Timeline                # noqa: E402

SONGS = Path("songs")
FAULT = SONGS / "Trans-Siberian Orchestra - Carol Of The Bells.gp"
CONTROL = SONGS / "Trans-Siberian Orchestra - Carol Of The Bells bars 1-10 +2.gp"
RECORDING = SONGS / "Trans-Siberian Orchestra - Carol Of The Bells.mp3"
# What the fault really is, read off the file: bars 1-10 of the clean guitar,
# A3 D4 F4 E4 against the recording's E4 G4 B3 F#4.
WANT_SEMITONES = 2
WANT_BY_S = 12.0        # the run has to start inside the opening bars


def whole_song(tab: Path) -> Timeline:
    """Every pitched track as one timeline, the way the app reads it."""
    merged = []
    for info in list_tracks(tab):
        if info.get("is_percussion"):
            continue
        try:
            merged.append(load_gp_file(tab, track_index=info["index"]))
        except Exception:
            continue
    if not merged:
        raise SystemExit(f"no readable track in {tab}")
    return Timeline([n for t in merged for n in t.notes], merged[0].metadata,
                    measures=merged[0].measures)


def bar_map_for(tab: Path, mp3: Path):
    """Songsterr's candidates for this song, from the cache beside the tab."""
    cache_file = tab.with_suffix(".songsterr.json")
    if not cache_file.exists():
        cache_file = mp3.with_suffix(".songsterr.json")
    if not cache_file.exists():
        return None
    cache = json.loads(cache_file.read_text())
    entries = cache.get("entries", cache)
    video = songsterr.video_id_of(songsterr.main_entry(entries)) or ""
    return songsterr.candidates_for(entries, video)


def recording_for(tab: Path) -> Path | None:
    """The recording beside this tab, by name.

    Loosely, because the player's own folder spells them differently: "4 Non
    Blondes - Whats up.gp" sits next to "4 Non Blondes - What's Up.mp3". The
    app resolves that through its stored path; this is a tool and has to
    guess, so it compares the letters and digits alone.
    """
    exact = tab.with_suffix(".mp3")
    if exact.exists():
        return exact
    def plain(name: str) -> str:
        return "".join(c for c in name.lower() if c.isalnum())
    want = plain(tab.stem)
    for found in sorted(tab.parent.glob("*.mp3")):
        if plain(found.stem) == want:
            return found
    return None


def read(tab: Path, mp3: Path) -> dict[str, list]:
    """The key gaps each path reports for this tab against this recording."""
    out: dict[str, list] = {}
    out["listening"] = autosync.find(str(tab), str(mp3)).get("key_gaps") or []
    candidates = bar_map_for(tab, mp3)
    if candidates:
        got = autosync.align_to_bar_times(whole_song(tab), str(mp3),
                                          candidates)
        out["bar map"] = got.get("key_gaps") or []
    return out


def say(label: str, found: dict[str, list]) -> None:
    for path, gaps in found.items():
        if not gaps:
            print(f"  {label:34} {path:10} nothing")
            continue
        for gap in gaps:
            print(f"  {label:34} {path:10} "
                  f"{gap.from_s:5.1f}-{gap.to_s:5.1f}s  "
                  f"{gap.semitones:+d} semitones  "
                  f"margin {gap.margin:.3f}  {gap.windows} windows")


INJECT_S = (90.0, 130.0)
INJECT_SHIFTS = (2, -1, 5)


def injected(tab: Path, mp3: Path) -> list[tuple[int, object]]:
    """Move a well-aligned stretch of a good tab and see it found.

    The pair above is one fault on one song, and it is the HARD case: the
    player's intro is a thin clean guitar whose own ten bars run 7.6 % fast
    against the recording, so a ten-second window of it is internally
    distorted and only the bar map's warping makes it readable at all. That
    says nothing about what the check can do on ordinary material, and the
    answer matters -- so a stretch of a tab that IS in the recording's key
    and IS lined up there is rolled by a known number of semitones, and the
    check has to name it.

    Same shape as `tools/simulate_timing.py`: an injected fault with a known
    answer, on real audio, through the real code.
    """
    samples, rate = autosync.decode(mp3)
    rec, fps = autosync.chroma_of_audio(samples, rate)
    tab_chroma = autosync.chroma_of_tab_file(tab, fps)
    rows = autosync.drift_curve(tab_chroma, rec, fps)
    band = slice(int(INJECT_S[0] * fps), int(INJECT_S[1] * fps))
    out = []
    # The WHOLE tab first, which is the commonest real shape of this fault:
    # a tab in the wrong tuning, or read with a capo nobody wrote down. It
    # is also the hardest for the measurement around it -- the lag search
    # itself collapses from 32-42 usable windows to 3-10, so nothing is
    # stored and the panel reports a failure. The rotation test reads the
    # interval off what is left, which is the whole reason that branch now
    # says so before it says it could not read anything.
    whole = np.roll(tab_chroma, 1, axis=1)
    whole_rows = autosync.drift_curve(whole, rec, fps)
    whole_gaps = autosync.key_check(whole, rec, fps, whole_rows)
    # An ABSTENTION is not a failure here, and a wrong number would be.
    # Where the readings disagree about the interval the panel names none
    # (see `_key_gap_lines`), so a disagreement counts as a pass and is
    # printed as one -- it is the honest answer to a tab whose whole key is
    # wrong and whose lag map is therefore built on almost nothing.
    spread = {g.semitones for g in whole_gaps}
    if len(spread) > 1:
        out.append(("whole", [], []))
    else:
        out.append(("whole", [g for g in whole_gaps if g.semitones == -1],
                    [g for g in whole_gaps if g.semitones != -1]))
    for shift in INJECT_SHIFTS:
        moved = tab_chroma.copy()
        moved[band] = np.roll(tab_chroma[band], shift, axis=1)
        gaps = autosync.key_check(moved, rec, fps, rows)
        # Rolling the tab's pitch classes UP makes the tab sound HIGHER, so
        # the check has to report the tab as sitting that far OVER.
        want = -shift
        lo = INJECT_S[0] - autosync.KEY_WINDOW_S
        hi = INJECT_S[1] + autosync.KEY_WINDOW_S
        inside = [g for g in gaps if lo <= g.from_s and g.to_s <= hi]
        hit = [g for g in inside if g.semitones == want]
        # SPLIT is not missed, and the difference matters: one dissenting
        # window breaks a run, so a forty-second error can come back as two
        # stretches of it. Measured on Kid Rock, where the window at 99.9 s
        # reads a fourth while the eight around it read the injected tone.
        stray = [g for g in gaps if g not in inside or g.semitones != want]
        out.append((shift, hit, stray))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fault", type=Path, default=FAULT)
    ap.add_argument("--control", type=Path, default=CONTROL)
    ap.add_argument("--recording", type=Path, default=RECORDING)
    ap.add_argument("--inject", action="store_true",
                    help="also roll a well-aligned stretch of every quiet "
                         "song by a known interval and require it found")
    ap.add_argument("--quiet", type=Path, action="append", default=[],
                    help="a tab that must report nothing (repeatable); "
                         "by default every other song in songs/")
    args = ap.parse_args()

    print(f"window {autosync.KEY_WINDOW_S:.0f}s  step "
          f"{autosync.KEY_STEP_S:.0f}s  lag band "
          f"{autosync.KEY_LAG_S:.0f}s  run >= {autosync.KEY_MIN_WINDOWS}  "
          f"margin >= {autosync.KEY_MARGIN}")

    bad = 0
    if not args.recording.exists():
        print(f"\n{args.recording} is not here -- the pair cannot be read")
        bad += 1
    else:
        print("\nTHE PAIR (one recording, two tabs a tone apart)")
        if args.fault.exists():
            found = read(args.fault, args.recording)
            say("fault: as transcribed", found)
            hit = [g for gaps in found.values() for g in gaps
                   if g.semitones == WANT_SEMITONES and g.from_s <= WANT_BY_S]
            if not hit:
                print("    FAIL: neither path found the opening a tone low")
                bad += 1
        else:
            print(f"  {args.fault} is not here")
            bad += 1
        if args.control.exists():
            found = read(args.control, args.recording)
            say("control: corrected copy", found)
            spoke = [g for gaps in found.values() for g in gaps]
            if spoke:
                print("    FAIL: the corrected tab is in the recording's key")
                bad += 1
        else:
            print(f"  {args.control} is not here -- run tools/"
                  f"transpose_bars.py to write it")
            bad += 1

    quiet = args.quiet
    if not quiet and SONGS.is_dir():
        pair = {args.fault.name, args.control.name}
        quiet = sorted(t for t in SONGS.glob("*.gp*")
                       if t.name not in pair and recording_for(t) is not None)
    if quiet:
        print("\nEVERY OTHER SONG WITH A RECORDING (must say nothing)")
    for tab in quiet:
        mp3 = recording_for(tab)
        if mp3 is None:
            # SAID, not skipped. A control that quietly does not run is a
            # control that passes on anything -- which is how two tools in
            # this project came to measure themselves.
            print(f"  {tab.name[:34]:34} no recording found beside it, "
                  f"so it was not read")
            bad += 1
            continue
        try:
            found = read(tab, mp3)
        except Exception as exc:
            print(f"  {tab.name[:34]:34} could not be read: "
                  f"{type(exc).__name__}: {exc}")
            continue
        say(tab.name[:34], found)
        if [g for gaps in found.values() for g in gaps]:
            print("    FAIL: a song nobody has complained about")
            bad += 1
        if not args.inject:
            continue
        for shift, hit, stray in injected(tab, mp3):
            where = ", ".join(f"{g.from_s:.0f}-{g.to_s:.0f}s/{g.margin:.3f}"
                              for g in hit)
            shift = f"{shift:+d}" if isinstance(shift, int) else shift
            print(f"  {tab.name[:34]:34} rolled {shift:5}  "
                  + (f"found over {where}" if hit
                     else "readings disagree, no number named"
                     if shift == "whole" else "NOT FOUND")
                  + (f"   STRAY {len(stray)}" if stray else ""))
            if stray or (not hit and shift != "whole"):
                bad += 1

    print("\nFAIL" if bad else "\nOK")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
