"""Move a stretch of one track up or down, in a GP6/7/8 file.

A transcriber can get a section's key wrong, and no amount of scoring can
make that right: *"Das ist der einzige Song bei dem der Anfang 7-7-6-0
komplett schraeg klingt."* On Carol Of The Bells the clean guitar's ten-bar
intro is written **a whole tone below the recording** -- A3 D4 F4 E4 where
the record plays B3 E4 G4 F#4, and A, D and F do not appear in the first
three seconds of it at all. Measured from the other side with the song's own
sync map, chroma over 0-10 s fits at +2 semitones (+0.351) and at +0 scores
-0.119; from bar 11 on, +0 wins everywhere.

So the frets move. The fret numbers are what a player reads, and this writes
them -- it does not transpose a tuning, re-voice anything, or touch another
track.

Usage:

    python tools/transpose_bars.py song.gp --track 0 --bars 1-10 --semitones 2
    python tools/transpose_bars.py song.gp --track 0 --bars 1-10 -s 2 --out fixed.gp

Bars are numbered as the tab numbers them, from 1. The copy is written beside
the original unless --out says otherwise; nothing is overwritten.

GPIF stores a note's pitch FOUR times over -- Fret, Midi, ConcertPitch and
TransposedPitch -- and the app reads only the first two. A file that moved
one and not the others would play correctly here and be wrong in Guitar Pro,
so all four are written, and the check at the end reads the result back
through the real loader and compares every pitch of every track.
"""

import argparse
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.tabs.loader import list_tracks, load_gp_file  # noqa: E402

GPIF = "Content/score.gpif"
MAX_FRET = 24
# GPIF spells a pitch with a letter, an accidental and an octave, and the
# octave is `midi // 12` -- verified on 14 pitches of the player's own file,
# naturals and sharps, across four octaves. Sharps throughout, which is what
# Guitar Pro writes.
STEPS = (("C", ""), ("C", "#"), ("D", ""), ("D", "#"), ("E", ""), ("F", ""),
         ("F", "#"), ("G", ""), ("G", "#"), ("A", ""), ("A", "#"), ("B", ""))


def _prop(note: ET.Element, name: str) -> ET.Element | None:
    for p in note.findall(".//Property"):
        if p.get("name") == name:
            return p
    return None


def _respell(pitch: ET.Element, midi: int) -> None:
    step, accidental = STEPS[midi % 12]
    pitch.find("Step").text = step
    pitch.find("Accidental").text = accidental or None
    pitch.find("Octave").text = str(midi // 12)


def notes_in(root: ET.Element, track: int, bars: range) -> list[ET.Element]:
    """Every <Note> of one track inside a bar range, from 1."""
    by_id = {n.get("id"): n for n in root.find("Notes").findall("Note")}
    beats = {b.get("id"): b for b in root.find("Beats").findall("Beat")}
    voices = {v.get("id"): v for v in root.find("Voices").findall("Voice")}
    bar_el = {b.get("id"): b for b in root.find("Bars").findall("Bar")}
    master = root.find("MasterBars").findall("MasterBar")
    out = []
    for number in bars:
        if not 1 <= number <= len(master):
            raise SystemExit(f"bar {number} is not in this file "
                             f"({len(master)} bars)")
        ids = (master[number - 1].findtext("Bars") or "").split()
        if track >= len(ids):
            raise SystemExit(f"bar {number} has no track {track}")
        bar = bar_el.get(ids[track])
        if bar is None:
            continue
        for vid in (bar.findtext("Voices") or "").split():
            if vid == "-1":
                continue
            for bid in (voices[vid].findtext("Beats") or "").split():
                for nid in (beats[bid].findtext("Notes") or "").split():
                    note = by_id.get(nid)
                    if note is not None:
                        out.append(note)
    return out


def move(note: ET.Element, semitones: int) -> tuple[int, int]:
    """Shift one note. Returns (old fret, new fret)."""
    fret_p = _prop(note, "Fret")
    if fret_p is None:
        raise SystemExit("a note in that range carries no fret -- "
                         "it is not a fretted note and this cannot move it")
    was_fret = int(fret_p.findtext("Fret"))
    fret = was_fret + semitones
    if not 0 <= fret <= MAX_FRET:
        raise SystemExit(f"fret {was_fret} would become {fret}, "
                         "which is not on the neck")
    fret_p.find("Fret").text = str(fret)
    # The other three are whatever the file happens to carry. A real GP7 or
    # GP8 note has all of them; the app reads the fret and the string and
    # works the pitch out itself, so a file without them is still valid and
    # must not be refused.
    midi_p = _prop(note, "Midi")
    was_midi = midi = None
    if midi_p is not None and midi_p.find("Number") is not None:
        was_midi = int(midi_p.findtext("Number"))
        midi = was_midi + semitones
        midi_p.find("Number").text = str(midi)
    for name in ("ConcertPitch", "TransposedPitch"):
        prop = _prop(note, name)
        pitch = None if prop is None else prop.find("Pitch")
        if pitch is None:
            continue
        if midi is None:
            raise SystemExit(f"{name} is written down but Midi is not, so "
                             "there is nothing to respell it from")
        _respell(pitch, midi)
    return was_fret, fret


def rewrite(src: Path, out: Path, track: int, bars: range,
            semitones: int) -> int:
    data = zipfile.ZipFile(src).read(GPIF)
    root = ET.fromstring(data)
    moved = [move(n, semitones) for n in notes_in(root, track, bars)]
    body = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(
            out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, body if item.filename == GPIF
                          else zin.read(item.filename))
    return len(moved)


def check(src: Path, out: Path, track: int, bars: range,
          semitones: int) -> bool:
    """Every pitch of every track, before and after.

    A quietly transposed tab is worse than no tab, so this is the claim the
    tool makes rather than a nicety.
    """
    ok = True
    for info in list_tracks(str(src)):
        i = info["index"]
        was = load_gp_file(str(src), track_index=i).notes
        now = load_gp_file(str(out), track_index=i).notes
        if len(was) != len(now):
            print(f"  track {i}: {len(was)} notes became {len(now)}")
            ok = False
            continue
        for a, b in zip(was, now):
            want = semitones if (i == track and a.measure + 1 in bars) else 0
            same = (a.timestamp_ms == b.timestamp_ms
                    and a.string == b.string
                    and a.duration_ms == b.duration_ms
                    and a.dead == b.dead and a.palm_mute == b.palm_mute)
            if not same or b.midi_note - a.midi_note != want:
                print(f"  track {i} bar {a.measure + 1}: "
                      f"midi {a.midi_note} -> {b.midi_note}, wanted {want:+d}")
                ok = False
                break
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("song")
    ap.add_argument("--track", type=int, required=True)
    ap.add_argument("--bars", required=True, help="e.g. 1-10 or 4")
    ap.add_argument("--semitones", "-s", type=int, required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    src = Path(args.song)
    first, _, last = args.bars.partition("-")
    bars = range(int(first), int(last or first) + 1)
    out = Path(args.out) if args.out else src.with_name(
        f"{src.stem} bars {bars.start}-{bars.stop - 1} "
        f"{args.semitones:+d}{src.suffix}")
    if out.exists():
        raise SystemExit(f"{out.name} is already there; nothing overwritten")

    moved = rewrite(src, out, args.track, bars, args.semitones)
    print(f"{out.name}: {moved} notes in bars {bars.start}-{bars.stop - 1} "
          f"of track {args.track} moved {args.semitones:+d} semitones")
    if not check(src, out, args.track, bars, args.semitones):
        print("FAIL: the copy does not say what it should -- removing it")
        out.unlink()
        return 1
    print("every pitch of every other bar and track is unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
