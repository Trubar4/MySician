"""Rewrite a tab for a guitar with fewer strings, keeping every pitch.

Metal is written for seven and eight strings, and this app plays six. The
usual answer is "you need a seven-string"; the honest one is that the notes
often fit a six-string in a low tuning perfectly well, because a seven-string
in B standard and a six-string in drop B share their lowest note.

Measured on the file that prompted this -- I Prevail, "Blank Space", a real
seven-string tab: the two rhythm guitars are 1179 and 1884 notes and **every
single one** of them fits drop B. The lead track loses four notes above the
neck, and it says so rather than moving them.

What this is NOT: it does not transpose, simplify, or re-voice anything. Each
note keeps its exact MIDI pitch and only its string and fret change, which is
arithmetic rather than arrangement -- and the check at the end proves it by
comparing every pitch before and after.

    python tools/retune.py song.gp5                    # to drop B
    python tools/retune.py song.gp5 --tuning drop-d
    python tools/retune.py song.gp5 --out six.gp5
"""

import argparse
import sys
from pathlib import Path

import guitarpro

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# String 1 (high) first, the order Guitar Pro stores them in.
TUNINGS = {
    "drop-b": (61, 56, 52, 47, 42, 35),      # C#4 G#3 E3 B2 F#2 B1
    "drop-a": (59, 54, 50, 45, 40, 33),      # B3 F#3 D3 A2 E2 A1
    "drop-c": (62, 57, 53, 48, 43, 36),      # D4 A3 F3 C3 G2 C2
    "drop-d": (64, 59, 55, 50, 45, 38),      # E4 B3 G3 D3 A2 D2
    "b-standard": (59, 54, 50, 45, 40, 35),  # B3 F#3 D3 A2 E2 B1
    "standard": (64, 59, 55, 50, 45, 40),
}

MAX_FRET = 24


def note_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def options(pitch: int, tuning, was_fret: int):
    """Every (cost, string, fret) this pitch can be played at, cheapest first.

    Cost is how far the fret moves from where it was. The two tunings share
    most of their intervals, so the cheapest placement keeps the SHAPE of the
    tab -- a riff that sat in one hand position stays in one hand position,
    which is the difference between a playable conversion and a correct one
    nobody can finger.
    """
    out = []
    for index, open_pitch in enumerate(tuning):
        fret = pitch - open_pitch
        if 0 <= fret <= MAX_FRET:
            out.append((abs(fret - was_fret), index + 1, fret))
    out.sort()
    return out


def place_beat(pitches, was_frets, tuning, fixed=None):
    """One string per note, or None for a note the neck cannot reach.

    The notes of a beat are placed TOGETHER, because a guitar cannot play two
    of them on the same string -- and a GP5 file cannot even describe it: the
    played-strings byte has one bit per string, so two notes sharing one are
    written but only one is read back, and every byte after that is garbage.
    That is what a beat-at-a-time conversion produced, and the file simply
    would not open.

    At most six notes over six strings, so the cheapest assignment is found by
    trying them rather than by being clever about it.
    """
    fixed = fixed or {}
    order = sorted(range(len(pitches)), key=lambda i: -pitches[i])
    choices = [[(0, *fixed[i])] if i in fixed
               else options(pitches[i], tuning, was_frets[i])
               for i in order]
    best = {"cost": None, "picks": None}

    def walk(depth, used, cost, picks):
        if best["cost"] is not None and cost >= best["cost"]:
            return
        if depth == len(order):
            best["cost"], best["picks"] = cost, list(picks)
            return
        placed = False
        for extra, string, fret in choices[depth]:
            if string in used:
                continue
            placed = True
            picks.append((string, fret))
            walk(depth + 1, used | {string}, cost + extra, picks)
            picks.pop()
        if not placed:
            # Nothing left for this note: out of reach, or every string that
            # could take it is already spoken for by a lower note.
            picks.append(None)
            walk(depth + 1, used, cost + 100, picks)
            picks.pop()

    walk(0, frozenset(), 0, [])
    out = [None] * len(pitches)
    for slot, index in enumerate(order):
        out[index] = best["picks"][slot]
    return out


def retune_track(track, tuning) -> tuple[int, int]:
    """Rewrite one track in place. Returns (moved, out of reach)."""
    old = [s.value for s in track.strings]
    moved = lost = 0
    # Which NEW string each OLD string's notes went to, last time round.
    # A tie has to follow its predecessor there. See below.
    carry: dict[int, int] = {}
    for measure in track.measures:
        for voice in measure.voices:
            for beat in voice.beats:
                if not beat.notes:
                    continue
                pitches = [old[n.string - 1] + n.value for n in beat.notes]
                was = [n.value for n in beat.notes]

                # A TIE continues the note on the SAME string, and GP5 stores
                # it that way -- the fret is reconstructed on reading from
                # whatever that string was last playing. So a tie whose
                # predecessor moved has to move with it, or the file reads
                # back a different pitch: one note came out an octave low
                # before this, and the placement itself was innocent.
                fixed: dict[int, tuple[int, int]] = {}
                for i, note in enumerate(beat.notes):
                    if note.type is not guitarpro.NoteType.tie:
                        continue
                    string = carry.get(note.string)
                    if string is None:
                        continue
                    fret = pitches[i] - tuning[string - 1]
                    if 0 <= fret <= MAX_FRET:
                        fixed[i] = (string, fret)

                placed = place_beat(pitches, was, tuning, fixed)
                for i, note in enumerate(beat.notes):
                    if placed[i] is not None:
                        carry[note.string] = placed[i][0]
                keep = []
                for note, spot in zip(beat.notes, placed):
                    if spot is None:
                        lost += 1
                        continue
                    note.string, note.value = spot
                    moved += 1
                    keep.append(note)
                # The reader walks the strings in order and expects the notes
                # in that order too.
                keep.sort(key=lambda n: n.string)
                if beat.notes and not keep:
                    # Every note of this beat was out of reach. An empty beat
                    # that still calls itself NORMAL is not a valid GP5 beat
                    # and the file will not read back -- which is how this was
                    # found. A rest is what a beat with nothing in it is.
                    beat.status = guitarpro.BeatStatus.rest
                beat.notes = keep
    # The strings themselves, last: the notes above were read against the old
    # tuning and would be read against the new one otherwise.
    while len(track.strings) > len(tuning):
        track.strings.pop()
    for i, value in enumerate(tuning):
        track.strings[i].number = i + 1
        track.strings[i].value = value
    return moved, lost


def pitches_of(song) -> list[tuple[int, int]]:
    """(track index, pitch) for every note, to compare before with after."""
    out = []
    for index, track in enumerate(song.tracks):
        tuning = [s.value for s in track.strings]
        for measure in track.measures:
            for voice in measure.voices:
                for beat in voice.beats:
                    for note in beat.notes:
                        out.append((index, tuning[note.string - 1] + note.value))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("song", help="the .gp3/.gp4/.gp5 to convert")
    ap.add_argument("--tuning", default="drop-b", choices=sorted(TUNINGS),
                    help="what the six-string is tuned to (default: drop-b)")
    ap.add_argument("--out", default=None, help="where to write it")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would happen and write nothing")
    args = ap.parse_args()

    path = Path(args.song)
    tuning = TUNINGS[args.tuning]
    song = guitarpro.parse(str(path))
    before = pitches_of(song)

    print(f"{path.name}")
    print(f"Ziel: {args.tuning} — "
          f"{' '.join(note_name(v) for v in reversed(tuning))}")
    print()
    total_lost = 0
    for index, track in enumerate(song.tracks):
        strings = len(track.strings)
        if strings <= 6 or track.channel.isPercussionChannel:
            print(f"  Track {index + 1} '{track.name}': {strings} Saiten — "
                  f"unveraendert")
            continue
        moved, lost = retune_track(track, tuning)
        total_lost += lost
        note = f"{moved} Noten umgelegt"
        if lost:
            note += f", {lost} ausserhalb des Griffbretts WEGGELASSEN"
        print(f"  Track {index + 1} '{track.name}': {strings} -> 6 Saiten, {note}")

    # The whole claim of this tool, checked rather than asserted: no note was
    # transposed. Compared as a multiset of pitches, so a note that merely
    # moved to another string is invisible here -- which is the point.
    # Anything MISSING must be a note that was reported as out of reach; a
    # pitch that appears where it did not before is a bug, and the run says
    # so and fails rather than handing over a quietly wrong tab.
    from collections import Counter
    a, b = Counter(p for _, p in before), Counter(p for _, p in pitches_of(song))
    dropped = a - b
    gained = b - a
    print()
    print(f"Noten vorher {sum(a.values())}, nachher {sum(b.values())}")
    if dropped:
        print("Weggelassen (ueber dem 24. Bund der neuen Stimmung):")
        for pitch, count in sorted(dropped.items()):
            print(f"   {note_name(pitch)} x{count}")
    if gained:
        print("FEHLER: diese Tonhoehen sind neu und duerften es nicht sein:")
        for pitch, count in sorted(gained.items()):
            print(f"   {note_name(pitch)} x{count}")
        return 1
    if not dropped:
        print("Jede Note behaelt ihre Tonhoehe. Nichts verloren.")
    elif sum(dropped.values()) != total_lost:
        print(f"FEHLER: {sum(dropped.values())} Tonhoehen fehlen, aber nur "
              f"{total_lost} wurden als unerreichbar gemeldet.")
        return 1

    if args.dry_run:
        print("\n--dry-run: nichts geschrieben.")
        return 0
    out = Path(args.out) if args.out else path.with_name(
        f"{path.stem} ({args.tuning}){path.suffix}")
    guitarpro.write(song, str(out))
    print(f"\nGeschrieben: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
