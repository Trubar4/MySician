"""What did the detector actually hear while the song was being played?

The 29 reference exercises are isolated notes with a rest after each, which
is the case that already works. The case that fails is a passage played
through, where strings struck earlier go on ringing under the next note --
and no take in that set contains it. `record_reference.py --play-along`
records exactly that; this reads it back.

Nothing has to be lined up while recording. The song's onsets are known
exactly (the tab is the ground truth), so the alignment is found here: the
offset that lets the most detected strikes fall near a written onset wins.
A few seconds of fumbling at either end therefore cost nothing.

The PRACTICE TEMPO is found the same way, and has to be. A take played at
80 % is stretched against the written grid, and reading it against the
written one anyway lines up the first bar and nothing after it: the same
recording read at 100 % scored 22 %, and at its real 80 % scored 96 %. Pass
--tempo when it is known; otherwise every speed the app offers is tried and
the best-fitting one is named in the output.

    python tools/analyze_play_along.py reference_recordings/<stamp>
    python tools/analyze_play_along.py <stamp> --song songs/timing_test_100bpm.gp5
    python tools/analyze_play_along.py <stamp> --tempo 80

Prints, per written onset, whether a strike arrived and whether it carried
the right pitch -- so "played but heard as the wrong note" is separated from
"never heard at all", which no summary percentage can do.
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
from pickhero.tabs.loader import load_gp_file  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
HOP = 512
# How near a strike has to be to a written onset to count as that note. Wide
# enough for real input latency, narrow enough not to reach the next eighth.
MATCH_MS = 140.0
# Alignment search. The recording is started by hand, so the song may begin
# anywhere in the first half minute -- but the recording may also START in the
# MIDDLE of the song, when the player jumps to a chorus and records that. The
# offset is then NEGATIVE and as large as the song is long, which the old
# search could not express at all: a 45-second take of the last verse was
# forced to the only offset in range, matched the opening, and was reported as
# "the intro again". A take of anything but the beginning could not be read.
ALIGN_MAX_MS = 30_000.0
ALIGN_STEP_MS = 5.0
# How many places to look, from each of the two histograms -- where strikes
# pile up on written onsets, and where they pile up on onsets whose pitch
# they carry.
ALIGN_REGIONS = 8
# How much better the pitch evidence has to be before it overrules the times.
ALIGN_PITCH_DOUBT = 3.0
# Practice speeds the app can be in when the take was played (PgDn/PgUp go
# from 50 % to 100 % in steps of 5). A take at 80 % lasts 1/0.8 as long as
# the tab says, so the written grid has to be stretched by that much before
# anything can be read off it.
TEMPO_FACTORS = tuple(round(0.50 + 0.05 * i, 2) for i in range(11))


def load_wav(path: Path):
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, rate


def strikes_of(audio, rate):
    """Every strike the app would see, in recording time."""
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


def best_offset_at(strike_ms, onsets, tempo, strike_midi=None,
                   written=None):
    """Where the song starts inside the recording, at a given practice tempo.

    Scored on TIMES alone for as long as the times can answer, and this
    was written up here for a year as a principle: fitting on pitch would
    assume the answer to the question being asked. That is right about the
    danger and wrong about the alternative, and Kid Rock's "Rock On"
    settles it. Its verse repeats one rhythmic figure, so on a 45-second
    take of it the rhythm does not merely tie -- it actively prefers the
    wrong bars. The true offset ranks NINETY-THIRD by strikes-on-the-grid,
    with 48 against the winner's 66, while its pitch agreement is 61 of 72
    against the winner's 12. Read where the times point, the take scores
    30 % of its written notes; read where the pitches point, 86 %.

    So the candidates come from BOTH: the places where strikes pile up on
    written onsets, and the places where strikes pile up on onsets whose
    pitch they carry. Among them the pitches choose, with the time hits as
    the tie-break. An alignment that puts a take on the wrong bars is not
    the conservative option -- it is simply wrong, and it looks exactly
    like a detector that has stopped working.

    The circularity is real and is handled by SAYING SO rather than by
    pretending it away: `alignment_note()` reports when the two disagree,
    so a reader knows that "heard with the right pitch" rests on an
    alignment the pitches helped choose.

    `strike_midi` runs parallel to `strike_ms` and carries the pitch a
    strike can be TRUSTED to name -- None or 0 where it cannot. A strike
    with no pitch is one such case and a SUBHARMONIC is the other, and the
    second one decides this: a subharmonic names the chord sounding in the
    room, not the note just struck, and on an arpeggio it is most of what
    the detector produces. Counted as evidence, 62 % of one take's strikes
    voted for a dense chorus 126 seconds from where the take really was.
    Counted out, the true place wins by two to one.

    `written` is the tab as (time_ms, midi) pairs, which is not the same
    list as `onsets` because a chord writes several pitches at one moment.
    Without either, this behaves exactly as it always did.
    """
    if not strike_ms or not onsets:
        return 0.0, 0, float("inf")
    grid = np.asarray(onsets, dtype=float) / tempo
    times = np.asarray(strike_ms, dtype=float)
    heard = (np.asarray([m or 0 for m in strike_midi], dtype=float)
             if strike_midi is not None else None)
    if written:
        note_at = np.asarray([t for t, _ in written], dtype=float) / tempo
        note_is = np.asarray([m for _, m in written], dtype=float)
    else:
        note_at = note_is = None

    def agreement(offset):
        """How well the strikes and the WRITTEN NOTES explain each other.

        Counting only the strikes that find a note of their own pitch is
        free in a dense passage: a chorus writing six strings a beat has
        some note of every pitch class at almost every moment, so a sparse
        45-second take scores full marks there. Measured, that is not a
        hypothetical -- it moved the arpeggio take from its true place at
        song -1 s into a chord section at 125 s, where 615 notes sit under
        its 134 strikes.

        So both directions, combined as an F-measure: what fraction of the
        strikes landed on a note of the pitch they carry, and what fraction
        of the notes WRITTEN in the stretch the take covers got such a
        strike. A dense passage wins the first and loses the second.

        Octave-equivalent, the way the matcher scores: a low string read an
        octave up is the commonest reading this detector produces and is
        green on screen, so counting it as a disagreement would prefer
        whichever bar happened to be misread less.
        """
        if heard is None or note_at is None:
            return 0.0
        song = times - offset
        lo, hi = song.min() - MATCH_MS, song.max() + MATCH_MS
        within = (note_at >= lo) & (note_at <= hi)
        if not within.any():
            return 0.0
        their_at, their_is = note_at[within], note_is[within]
        explained = np.zeros(len(their_at), dtype=bool)
        struck = 0
        for when, note in zip(song, heard):
            if note <= 0:                      # unpitched: says nothing here
                continue
            fits = (np.abs(their_at - when) <= MATCH_MS) & (
                (their_is - note) % 12 == 0)
            if fits.any():
                struck += 1
                explained |= fits
        pitched = int((heard > 0).sum())
        if not struck or not pitched:
            return 0.0
        precision = struck / pitched
        recall = float(explained.sum()) / len(their_at)
        return 2.0 * precision * recall / (precision + recall)

    def score(offset):
        """(how many strikes land on the grid, how tightly they land)."""
        errors = np.abs(grid[None, :] - (times - offset)[:, None]).min(axis=1)
        near = errors[errors <= MATCH_MS]
        return len(near), float(near.sum())

    # Counting hits alone leaves a plateau tens of milliseconds wide -- every
    # offset inside it scores the same, the first one wins, and the whole
    # report is then read against a grid sitting up to 100 ms off. Among the
    # offsets that tie on hits, the tightest fit is the real one.
    # Searching every offset from -len(song) to +30 s at 5 ms would be
    # forty thousand passes over the whole matrix. The optimum always sits
    # near some (strike - onset) difference, so those are the candidates:
    # histogram them, then search finely around the busiest regions only.
    diffs = (times[:, None] - grid[None, :]).ravel()
    diffs = diffs[(diffs >= -grid.max() - 1000.0) & (diffs <= ALIGN_MAX_MS)]
    if not diffs.size:
        return 0.0, 0, float("inf")
    edges = np.arange(diffs.min() - MATCH_MS, diffs.max() + 2 * MATCH_MS,
                      MATCH_MS)
    counts, _ = np.histogram(diffs, bins=edges)
    # A few regions, not one: the true offset can lose the raw count to a
    # dense passage that happens to line up with a different bar.
    wanted = list(np.argsort(counts)[::-1][:ALIGN_REGIONS])
    if note_at is not None and heard is not None:
        # ...and the same histogram over the pairs whose PITCH agrees, which
        # is where a take of a repetitive verse is actually found. Without
        # this the true offset is never even a candidate: on the take that
        # prompted it, it ranks 93rd by time alone.
        agreeing = []
        for when, note in zip(times, heard):
            if note <= 0:
                continue
            fits = (note_is - note) % 12 == 0
            agreeing.append(when - note_at[fits])
        if agreeing:
            pitched = np.concatenate(agreeing)
            pitched = pitched[(pitched >= -grid.max() - 1000.0)
                              & (pitched <= ALIGN_MAX_MS)]
            if pitched.size:
                by_pitch, _ = np.histogram(pitched, bins=edges)
                for region in np.argsort(by_pitch)[::-1][:ALIGN_REGIONS]:
                    if region not in wanted:
                        wanted.append(int(region))
    regions = wanted

    # One best per region first, then choose between the regions. They are
    # the distinct candidates: offsets inside a region differ by
    # milliseconds, offsets in different regions by whole bars.
    found = []
    for region in regions:
        lo, hi = edges[region] - MATCH_MS, edges[region + 1] + MATCH_MS
        best = None
        for offset in np.arange(lo, hi, ALIGN_STEP_MS):
            hits, error = score(float(offset))
            if best is None or hits > best[0] or (hits == best[0]
                                                  and error < best[1]):
                best = (hits, error, float(offset))
        if best is not None:
            found.append(best)
    if not found:
        return 0.0, 0, float("inf")

    by_time = sorted(found, key=lambda c: (-c[0], c[1]))
    if heard is None or note_at is None:
        hits, error, offset = by_time[0]
        return offset, hits, error

    # The TIMES still answer. The pitches may only OVERRULE them, and only
    # when they disagree overwhelmingly -- the same shape as the tempo check
    # further down, and for the same reason: a criterion that is usually
    # right must not be replaced by one that is occasionally better.
    #
    # Measured over the eight play-along takes there are: where the two
    # already agree the ratio is 1.00-1.05, where the pitch answer is WORSE
    # it is 1.83-1.93 (and once 0.51), and on the take the times cannot
    # place at all it is 4.51. So the window is 1.93 to 4.51 and the value
    # is 3.0 -- above every disagreement that was wrong and well below the
    # one that was right.
    by_pitch = max(found, key=lambda c: (agreement(c[2]), c[0], -c[1]))
    settled = agreement(by_time[0][2])
    if agreement(by_pitch[2]) >= max(settled, 1e-9) * ALIGN_PITCH_DOUBT:
        hits, error, offset = by_pitch
    else:
        hits, error, offset = by_time[0]
    return offset, hits, error


# A stated practice speed is checked against the audio, never believed. The
# manifest said 80 % for a take played at 100 % and the report came back at
# 13 %, which reads exactly like a detector that has stopped working. On a
# real take the true speed is a sharp peak (46 strikes explained against 40
# at the neighbouring speed); the wrong one sat at 33 against 51. So another
# speed has to explain a QUARTER more strikes before it overrules what the
# manifest says -- far more than the peak's own margin, and far less than the
# gap a wrong number opens.
TEMPO_DOUBT_RATIO = 1.25


def check_tempo(strike_ms, onsets, stated):
    """(tempo, note) -- the stated speed, or the measured one if it is beaten.

    A tool that reports a number without checking the assumption underneath it
    is measuring itself, and this one has done exactly that once already.
    """
    if stated is None:
        return None, ""
    _, stated_hits, _ = best_offset_at(strike_ms, onsets, stated)
    _, best_hits, best_tempo = best_alignment(strike_ms, onsets, None)
    if best_hits < stated_hits * TEMPO_DOUBT_RATIO:
        return stated, ""
    note = (f"ACHTUNG: angegeben sind {stated * 100:.0f} %, aber bei "
            f"{best_tempo * 100:.0f} % passen {best_hits} statt {stated_hits} "
            f"Anschlaege aufs Raster.\n"
            f"         Gelesen wird mit {best_tempo * 100:.0f} %. "
            f"Das Manifest der Aufnahme stimmt nicht.")
    return best_tempo, note


def best_alignment(strike_ms, onsets, tempo=None, strike_midi=None,
                   written=None):
    """(offset, hits, tempo) for the best fit, over one tempo or all of them.

    Reading a slowed-down take against the written grid is not a small error
    that shows up as noise: the first bar lines up, everything after it walks
    away, and the report then blames detection for notes it heard perfectly.
    So the tempo is part of the fit unless the caller states it.
    """
    if tempo is not None:
        offset, hits, _ = best_offset_at(strike_ms, onsets, tempo,
                                         strike_midi, written)
        return offset, hits, tempo
    best = (0.0, -1, float("inf"), 1.0)
    for factor in TEMPO_FACTORS:
        offset, hits, error = best_offset_at(strike_ms, onsets, factor,
                                             strike_midi, written)
        # Prefer the tempo that explains more strikes; on a tie, the tighter
        # fit. A tie broken by neither would silently prefer 50 %, whose
        # stretched grid has a written onset near almost any strike.
        if hits > best[1] or (hits == best[1] and error < best[2]):
            best = (offset, hits, error, factor)
    return best[0], best[1], best[3]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", help="reference_recordings/<stamp>")
    ap.add_argument("--song", default=None, help="the .gp5 that was played")
    ap.add_argument("--wav", default=None, help="a WAV outside a session")
    ap.add_argument("--tempo", type=float, default=None,
                    help="practice speed the take was played at, in percent "
                         "(e.g. 80). Left out, it is measured.")
    args = ap.parse_args()
    tempo = None
    if args.tempo is not None:
        tempo = args.tempo / 100.0 if args.tempo > 1.5 else args.tempo
    stated = tempo

    session = Path(args.session)
    song_name = None
    if args.wav:
        wav_path = Path(args.wav)
        rate_hint = None
    else:
        manifest = json.loads((session / "manifest.json").read_text())
        take = next((t for t in manifest["takes"] if t["id"] == "play_along"), None)
        if take is None:
            print("Kein play_along-Take in dieser Session. Aufnehmen mit:")
            print("  python tools/record_reference.py --play-along")
            return 1
        wav_path = session / take["file"]
        song_name = take.get("song")
        rate_hint = manifest.get("samplerate")
        # Recorded by the recorder straight out of the app's settings, so a
        # slowed-down take says so instead of having to be guessed at.
        if tempo is None and take.get("tempo_percent"):
            tempo = float(take["tempo_percent"]) / 100.0

    if args.song:
        song = Path(args.song)
    else:
        if not song_name:
            # No default. A take whose manifest cannot say what was played is
            # not a take, it is 45 seconds of audio -- and a guessed default
            # once made a take of a different piece read as 3 of 46 notes,
            # which looks exactly like a detector that has stopped working.
            print("Im Manifest steht nicht, welcher Song gespielt wurde. "
                  "Mit --song angeben.")
            return 1
        song = REPO_ROOT / "songs" / song_name
        if not song.exists():
            for suffix in (".gp5", ".gp", ".gpx"):
                candidate = song.with_name(song.name + suffix)
                if candidate.exists():
                    song = candidate
                    break
    if not Path(song).exists():
        print(f"Songdatei nicht gefunden: {song}")
        return 1
    timeline = load_gp_file(song)
    by_onset: dict[float, list] = {}
    for note in timeline.notes:
        by_onset.setdefault(note.timestamp_ms, []).append(note)
    onsets = sorted(by_onset)

    audio, rate = load_wav(wav_path)
    if rate_hint and rate != rate_hint:
        print(f"(WAV liegt bei {rate} Hz, Manifest sagt {rate_hint})")
    strikes = strikes_of(audio, rate)
    stated = stated if stated is not None else tempo
    strike_ms = [s.timestamp_ms for s in strikes]
    tempo, doubt = check_tempo(strike_ms, onsets, tempo)
    if doubt:
        print(doubt + "\n")
        stated = None
    offset, aligned, tempo = best_alignment(
        strike_ms, onsets, tempo,
        [None if (s.note.unpitched
                  or getattr(s.note, "subharmonic", False))
         else s.note.midi_note for s in strikes],
        [(n.timestamp_ms, n.midi_note) for n in timeline.notes])

    print(f"{wav_path.name}: {len(audio) / rate:.0f}s, {rate} Hz")
    print(f"Song: {Path(song).name} — {len(onsets)} Anschlaege geschrieben")
    print(f"Tempo der Aufnahme: {tempo * 100:.0f} % des geschriebenen"
          + (" (gemessen)" if stated is None else ""))
    print(f"Ausrichtung: Song beginnt bei {offset / 1000:.2f}s im Mitschnitt "
          f"({aligned} von {len(strikes)} Anschlaegen fallen aufs Raster)\n")

    print(f"{'Zeit':>8} {'erwartet':>18} {'gehoert':>16}  Ergebnis")
    print("-" * 66)
    heard = wrong = silent = 0
    for onset in onsets:
        wanted = sorted({n.midi_note for n in by_onset[onset]})
        played_at = onset / tempo
        near = [s for s in strikes
                if abs(s.timestamp_ms - offset - played_at) <= MATCH_MS]
        want_text = "+".join(midi_to_name(m) for m in wanted)
        if not near:
            silent += 1
            print(f"{played_at / 1000:7.2f}s {want_text:>18} {'—':>16}  nichts gehoert")
            continue
        pitched = [s for s in near if not s.note.unpitched]
        if not pitched:
            heard += 1
            print(f"{played_at / 1000:7.2f}s {want_text:>18} {'(ohne Ton)':>16}  "
                  f"Anschlag ohne Tonhoehe")
            continue
        got = pitched[0].note.midi_note
        name = midi_to_name(got)
        if got in wanted or any(abs(got - m) % 12 == 0 for m in wanted):
            heard += 1
            print(f"{played_at / 1000:7.2f}s {want_text:>18} {name:>16}  richtig")
        else:
            wrong += 1
            near_by = min(abs(got - m) for m in wanted)
            print(f"{played_at / 1000:7.2f}s {want_text:>18} {name:>16}  "
                  f"FALSCH ({near_by} Halbtoene daneben)")

    total = len(onsets)
    print("-" * 66)
    print(f"richtig oder als Anschlag erkannt: {heard}/{total} "
          f"({100 * heard / total:.0f}%)")
    print(f"falsche Tonhoehe:                  {wrong}/{total}")
    print(f"gar nichts gehoert:                {silent}/{total}")
    print(f"\nZusaetzliche Anschlaege ohne geschriebene Note: "
          f"{max(0, len(strikes) - aligned)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
