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
# Alignment search: the recording is started by hand, so the song may begin
# anywhere in the first half minute.
ALIGN_MAX_MS = 30_000.0
ALIGN_STEP_MS = 5.0
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


def best_offset_at(strike_ms, onsets, tempo):
    """Where the song starts inside the recording, at a given practice tempo.

    Scored by how many strikes land near a written onset, which needs no
    pitch to be right -- an alignment fitted on pitch would quietly assume
    the answer to the question being asked.
    """
    if not strike_ms or not onsets:
        return 0.0, 0, float("inf")
    grid = np.asarray(onsets, dtype=float) / tempo
    times = np.asarray(strike_ms, dtype=float)

    def score(offset):
        """(how many strikes land on the grid, how tightly they land)."""
        errors = np.abs(grid[None, :] - (times - offset)[:, None]).min(axis=1)
        near = errors[errors <= MATCH_MS]
        return len(near), float(near.sum())

    # Counting hits alone leaves a plateau tens of milliseconds wide -- every
    # offset inside it scores the same, the first one wins, and the whole
    # report is then read against a grid sitting up to 100 ms off. Among the
    # offsets that tie on hits, the tightest fit is the real one.
    best_offset, best_hits, best_error = 0.0, -1, float("inf")
    for offset in np.arange(0.0, ALIGN_MAX_MS, ALIGN_STEP_MS):
        hits, error = score(float(offset))
        if hits > best_hits or (hits == best_hits and error < best_error):
            best_offset, best_hits, best_error = float(offset), hits, error
    return best_offset, best_hits, best_error


def best_alignment(strike_ms, onsets, tempo=None):
    """(offset, hits, tempo) for the best fit, over one tempo or all of them.

    Reading a slowed-down take against the written grid is not a small error
    that shows up as noise: the first bar lines up, everything after it walks
    away, and the report then blames detection for notes it heard perfectly.
    So the tempo is part of the fit unless the caller states it.
    """
    if tempo is not None:
        offset, hits, _ = best_offset_at(strike_ms, onsets, tempo)
        return offset, hits, tempo
    best = (0.0, -1, float("inf"), 1.0)
    for factor in TEMPO_FACTORS:
        offset, hits, error = best_offset_at(strike_ms, onsets, factor)
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

    song = args.song or (REPO_ROOT / "songs" / (song_name or "timing_test_100bpm.gp5"))
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
    offset, aligned, tempo = best_alignment(
        [s.timestamp_ms for s in strikes], onsets, tempo)

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
