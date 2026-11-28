"""Songsterr's own tab format, read as a Timeline.

Most Songsterr tabs have no Guitar Pro file behind them. `_source_of` walks
eight revisions back looking for one and regularly finds nothing, because
Songsterr keeps these tabs in a format of their own -- a hash per track and
an `audioV4` mix -- and a `.gp` exists only where somebody uploaded one. A
third-party downloader can still produce a tab for those songs because it
BUILDS one from this data rather than finding a file.

So this reads the same data. Per track:

    {songId}/{revisionId}/{hash}/{partId}.json

holding `measures -> voices -> beats -> notes`, the tuning, the instrument
and the tempo automations -- a complete tab, and the only route to a
playable song for the tabs the walk comes back empty on.

Everything here was measured against the third-party `.gp` of one revision
(Nirvana, "Come As You Are", revision 10510536), which is the control
`tools/check_songsterr_tab.py` runs:

  - the note COUNT agrees exactly on all eight pitched tracks, once the
    ties are taken off: 1406 notes minus 166 ties is the .gp's 1240;
  - `tone: 100` is the .gp's 2.0 semitones, so 50 is one semitone -- the
    same unit GP5 used and GPIF kept;
  - `position: 60` is the .gp's 1.0, the end of the note;
  - and this source carries MORE than the .gp does: 1013 let-ring notes
    against none (the string `LetRing` does not appear in that file's GPIF
    at all), 48 slides against 36, 14 bends against 10, and the chord names.

Neither source is a strict superset, which has to be said rather than
implied: that GPIF carries 18 `PalmMute` the JSON does not.

Pure arithmetic -- no network, no pygame, no file reading -- so the whole
format is tested on dicts, and so the fetcher and the app cannot have two
ideas of what a Songsterr tab is.
"""

from __future__ import annotations

from typing import Any

from pickhero.tabs.timeline import (MeasureInfo, NoteEvent, SongMetadata,
                                    Timeline, distinct_reasons, extend_tied,
                                    note_or_skip)

#: `duration` is [numerator, denominator] of a WHOLE note, so [1, 8] is an
#: eighth. It already INCLUDES the dot: a dotted quarter is written
#: `type: 4, duration: [3, 8], dots: 1`, so `dots` and `type` say how to
#: ENGRAVE the beat and `duration` says how long it is. Applying the dot as
#: well would make every dotted note half a beat too long.
WHOLE_IN_QUARTERS = 4.0

#: Measured against the .gp, not guessed. See the module docstring.
BEND_PER_SEMITONE = 50.0
BEND_POSITION_SCALE = 60.0

#: General MIDI, the same window the GP loader uses -- read from there
#: rather than written down again, since "which programs are guitars" is
#: one answer and two copies of it would be two.
#: (Vocals in these tabs are written as a SAX on a six-string staff with
#: guitar tuning, so the program number is the only thing that says they
#: are not a guitar part.)
_DRUM_INSTRUMENT = 1024

#: What this reader understands. Anything else in the file is counted and
#: NAMED, because the one thing no single song can show is what a format
#: does with the structures this song does not contain -- there are no
#: repeats, no tuplets and no second voice anywhere in the revision these
#: rules were fitted on. A reader that skipped them in silence would read a
#: song with a repeat in it as a song that is simply shorter.
_KNOWN_MEASURE = frozenset({
    "voices", "signature", "keySignature", "marker", "rest", "clef",
})
_KNOWN_VOICE = frozenset({"beats", "rest"})
_KNOWN_BEAT = frozenset({
    "notes", "duration", "type", "dots", "rest", "letRing", "palmMute",
    "beamStart", "beamStop", "chord", "text", "velocity", "pickStroke",
    "brushStroke", "vibratoWithTremoloBar", "upStroke", "downStroke",
    "tremoloPicking", "slap", "pop", "tap", "fadeIn",
})
_KNOWN_NOTE = frozenset({
    "string", "fret", "tie", "dead", "bend", "slide", "ghost", "rest",
    "accentuated", "harmonic", "vibrato", "palmMute", "staccato", "muted",
    "leftHandFinger", "rightHandFinger",
})

#: `slide` as Songsterr spells it, against NoteEvent's own encoding
#: (`slide_in`/`slide_out`: +1 from below / upwards, -1 from above /
#: downwards). A LEGATO slide maps to `slide_to_next` and not to
#: `hammer_to_next`, which is what the GP loader does with GP5's own legato
#: slide -- a second reader must not score one technique two ways.
_SLIDE_TO_NEXT = frozenset({"shift", "legato"})
_SLIDE_IN = {"inFromBelow": 1, "inFromAbove": -1}
_SLIDE_OUT = {"upwards": 1, "downwards": -1, "outUpwards": 1,
              "outDownwards": -1}


def _quarters(duration: Any) -> float:
    """A beat's written length in quarter notes, 0.0 if unreadable."""
    try:
        num, den = duration
        return WHOLE_IN_QUARTERS * float(num) / float(den)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _bend(raw: Any) -> tuple[tuple[float, float], ...]:
    """A bend as ((position 0..1, semitones), ...), () if there is none.

    A curve that never leaves zero is dropped, the way the GP reader drops
    it: a bend the tab declares and does not use would draw an arc of no
    height, which is noise on a lane six notes tall.
    """
    points = (raw or {}).get("points") if isinstance(raw, dict) else None
    if not points:
        return ()
    curve = []
    for point in points:
        if not isinstance(point, dict):
            continue
        position = float(point.get("position", 0)) / BEND_POSITION_SCALE
        curve.append((min(1.0, max(0.0, position)),
                      float(point.get("tone", 0)) / BEND_PER_SEMITONE))
    return tuple(curve) if any(tone > 0 for _, tone in curve) else ()


def _signature(measure: dict, carried: tuple[int, int]) -> tuple[int, int]:
    """The bar's time signature, or the one before it.

    `signature` appears only where it CHANGES -- once, on measure 0, in the
    revision this was read against -- so a reader that defaulted per bar
    would put every bar after the first in 4/4.
    """
    raw = measure.get("signature")
    try:
        beats, beat_type = raw
        if int(beats) > 0 and int(beat_type) > 0:
            return int(beats), int(beat_type)
    except (TypeError, ValueError):
        pass
    return carried


def _voice_quarters(voice: Any) -> float:
    """How much music one voice of a bar really holds."""
    if not isinstance(voice, dict):
        return 0.0
    return sum(_quarters(beat.get("duration"))
               for beat in voice.get("beats") or []
               if isinstance(beat, dict))


def _ms_at(steps: list[tuple[float, float]], quarters: float) -> float:
    """Quarters from the start of the song, in milliseconds.

    The positions are worked out in QUARTERS first and turned into time
    here, so a tempo change inside a note is accounted for rather than
    averaged away. That is the lesson the MusicXML export paid for: four
    diagnostics in a row measured accumulated drift and blamed whatever
    they were looking at, because every one compared milliseconds against a
    clock that had already slipped.
    """
    ms = 0.0
    for i, (at, quarter_bpm) in enumerate(steps):
        nxt = steps[i + 1][0] if i + 1 < len(steps) else float("inf")
        if quarters <= at:
            break
        spent = min(quarters, nxt) - at
        ms += spent * 60000.0 / quarter_bpm
        if quarters <= nxt:
            break
    return ms


def _tempo_steps(track: dict, bar_starts: list[float],
                 bar_lengths: list[float],
                 reasons: list[str]) -> list[tuple[float, float]]:
    """(quarters, quarter BPM) for every tempo the track declares.

    `type` is the note value the BPM counts, so a quarter BPM is
    `bpm * 4 / type`; `position` is read as a fraction of the bar. Only
    `type: 4` at `position: 0` appears in the revision this was fitted on,
    so anything else is converted AND named -- a number nobody could check
    is worth less than a number beside the sentence saying so.
    """
    first = float(((track.get("automations") or {}).get("tempo") or
                   [{}])[0].get("bpm") or 0) or 120.0
    steps: list[tuple[float, float]] = []
    for entry in (track.get("automations") or {}).get("tempo") or []:
        if not isinstance(entry, dict):
            continue
        bpm = float(entry.get("bpm") or 0)
        note_type = float(entry.get("type") or 4)
        if bpm <= 0 or note_type <= 0:
            continue
        if note_type != 4:
            _note_once(reasons, f"a tempo counted in 1/{note_type:g} notes")
        bar = int(entry.get("measure") or 0)
        position = float(entry.get("position") or 0)
        if position:
            _note_once(reasons, "a tempo change inside a bar")
        if 0 <= bar < len(bar_starts):
            at = bar_starts[bar] + position * bar_lengths[bar]
        else:
            at = 0.0
        steps.append((at, bpm * WHOLE_IN_QUARTERS / note_type))
    steps.sort()
    if not steps or steps[0][0] > 0:
        steps.insert(0, (0.0, first))
    return steps


def _note_once(reasons: list[str], reason: str) -> None:
    """Keep a reason once, in the order it first came up."""
    if reason not in reasons:
        reasons.append(reason)


def _unknown_keys(track: dict, reasons: list[str]) -> None:
    """Name anything in the file this reader does not read.

    The structures that could cost whole bars -- a repeat, an alternate
    ending, a tuplet, a second voice -- are absent from the one revision
    these rules were measured against, so nothing here can be verified
    about them. What CAN be done is refuse to be silent: a song with a
    repeat in it then says so instead of reading as a song that is shorter.
    """
    seen: dict[str, int] = {}

    def note(level: str, key: str) -> None:
        seen[f"{level} {key}"] = seen.get(f"{level} {key}", 0) + 1

    for measure in track.get("measures") or []:
        if not isinstance(measure, dict):
            continue
        for key in measure:
            if key not in _KNOWN_MEASURE:
                note("a bar carrying", key)
        for voice in measure.get("voices") or []:
            if not isinstance(voice, dict):
                continue
            for key in voice:
                if key not in _KNOWN_VOICE:
                    note("a voice carrying", key)
            for beat in voice.get("beats") or []:
                if not isinstance(beat, dict):
                    continue
                for key in beat:
                    if key not in _KNOWN_BEAT:
                        note("a beat carrying", key)
                for raw in beat.get("notes") or []:
                    if not isinstance(raw, dict):
                        continue
                    for key in raw:
                        if key not in _KNOWN_NOTE:
                            note("a note carrying", key)
    for what, count in sorted(seen.items()):
        _note_once(reasons, f"{count}x {what}, which this reader does not read")


def is_guitar(track: Any) -> bool:
    """Six strings, a guitar program, and not the drum kit."""
    from pickhero.tabs.loader import (GUITAR_INSTRUMENT_MAX,
                                      GUITAR_INSTRUMENT_MIN)
    if not isinstance(track, dict) or is_percussion(track):
        return False
    program = track.get("instrumentId")
    if not isinstance(program, int):
        return False
    return (len(track.get("tuning") or []) == 6
            and GUITAR_INSTRUMENT_MIN <= program <= GUITAR_INSTRUMENT_MAX)


def is_percussion(track: Any) -> bool:
    """The drum kit: program 1024, and no tuning at all."""
    if not isinstance(track, dict):
        return False
    return (track.get("instrumentId") == _DRUM_INSTRUMENT
            or not track.get("tuning"))


def list_tracks(tracks: list[Any]) -> list[dict]:
    """The track picker's rows, in the shape the GP loader returns.

    One shape, so the picker, the merge and the song index cannot tell a
    Songsterr tab from a Guitar Pro one.
    """
    rows = []
    for index, track in enumerate(tracks):
        track = track if isinstance(track, dict) else {}
        tuning = [int(v) for v in (track.get("tuning") or [])]
        rows.append({
            "index": index,
            "name": track.get("name") or track.get("instrument") or
                    f"Track {index + 1}",
            "num_strings": len(tuning) or int(track.get("strings") or 0),
            "is_percussion": is_percussion(track),
            "is_guitar": is_guitar(track),
            # Low string first, the order a guitarist tunes in and the order
            # the GP loader returns. Songsterr writes it HIGH first.
            "tuning": list(reversed(tuning)),
        })
    return rows


def timeline_from_tracks(tracks: list[Any], track_index: int = 0,
                         title: str = "", artist: str = "") -> Timeline:
    """One Songsterr track as a Timeline.

    The bar grid comes from the SIGNATURE and not from the beats a bar
    happens to hold -- a sparsely filled bar would otherwise read short and
    move every bar after it, which is the fault that cost the MusicXML
    export 42 seconds on one song. The one exception is a pickup bar, where
    `anacrusis` says in the file that the bar really is shorter: measured on
    this revision, bar 0 holds half a bar of music and the other 111 sum to
    exactly 1.0.
    """
    if not tracks:
        return Timeline([], SongMetadata(title=title, artist=artist))
    index = track_index if 0 <= track_index < len(tracks) else 0
    track = tracks[index] if isinstance(tracks[index], dict) else {}
    measures = [m for m in (track.get("measures") or []) if isinstance(m, dict)]
    tuning_high_first = [int(v) for v in (track.get("tuning") or [])]
    tuning = {i + 1: midi for i, midi in enumerate(tuning_high_first)}

    reasons: list[str] = []
    _unknown_keys(track, reasons)

    # Pass one: the bar grid, in quarters, which no tempo can distort.
    signature = (4, 4)
    bar_starts: list[float] = []
    bar_lengths: list[float] = []
    signatures: list[tuple[int, int]] = []
    at = 0.0
    short_bars = 0
    for number, measure in enumerate(measures):
        signature = _signature(measure, signature)
        written = (WHOLE_IN_QUARTERS * signature[0] / signature[1])
        held = max((_voice_quarters(v) for v in measure.get("voices") or []),
                   default=0.0)
        length = written
        if number == 0 and track.get("anacrusis") and 0 < held < written:
            length = held
        elif held and abs(held - written) > 1e-6:
            short_bars += 1
        bar_starts.append(at)
        bar_lengths.append(length)
        signatures.append(signature)
        at += length
    if short_bars:
        _note_once(reasons, f"{short_bars} bar(s) do not fill their signature")

    steps = _tempo_steps(track, bar_starts, bar_lengths, reasons)

    # Pass two: the notes.
    skipped: list[str] = []
    notes: list[NoteEvent] = []
    for number, measure in enumerate(measures):
        for voice in measure.get("voices") or []:
            if not isinstance(voice, dict):
                continue
            # Each voice starts at the bar line and keeps its own cursor, so
            # a let-ring bass note under a run of eighths lands where it is
            # written rather than after it.
            quarters = bar_starts[number]
            for beat in voice.get("beats") or []:
                if not isinstance(beat, dict):
                    continue
                length = _quarters(beat.get("duration"))
                start_ms = _ms_at(steps, quarters)
                duration_ms = _ms_at(steps, quarters + length) - start_ms
                if not beat.get("rest"):
                    _read_beat(beat, number, start_ms, duration_ms, length,
                               tuning, notes, skipped)
                quarters += length

    metadata = SongMetadata(
        title=title or track.get("title") or "",
        artist=artist,
        track_name=track.get("name") or track.get("instrument") or "",
        tempo=int(round(steps[0][1])) if steps else 120,
        tuning=tuning,
        track_index=index,
        unreadable_notes=len(skipped),
        # One rule about which reasons reach a screen, with the
        # structural ones first: a repeat this reader does not read
        # costs bars, where an unreadable note costs a note.
        unreadable_reasons=distinct_reasons(reasons + skipped),
    )
    bars = [MeasureInfo(index=i, start_ms=_ms_at(steps, bar_starts[i]),
                        end_ms=_ms_at(steps, bar_starts[i] + bar_lengths[i]),
                        beats=signatures[i][0], beat_type=signatures[i][1])
            for i in range(len(measures))]
    return Timeline(notes, metadata, bars)



def _read_beat(beat: dict, number: int, start_ms: float, duration_ms: float,
               length: float, tuning: dict[int, int],
               notes: list[NoteEvent], skipped: list[str]) -> None:
    """Every note of one beat, appended."""
    let_ring = bool(beat.get("letRing"))
    palm_mute = bool(beat.get("palmMute"))
    for raw in beat.get("notes") or []:
        if not isinstance(raw, dict) or raw.get("rest"):
            continue
        high_first = raw.get("string")
        fret = raw.get("fret")
        if not isinstance(high_first, int) or not isinstance(fret, int):
            skipped.append("a note with no string or no fret")
            continue
        # Songsterr counts strings from the HIGH one at 0; this app counts
        # from the high one at 1. The GPIF reader's array is LOW first, so
        # the two conversions are deliberately not the same.
        string = high_first + 1
        if high_first not in range(len(tuning)):
            skipped.append(f"a note on string {string} of a "
                           f"{len(tuning)}-string tab")
            continue
        if raw.get("tie"):
            # A tie is one note written twice: nothing is appended, and the
            # note it continues gets longer.
            extend_tied(notes, string, start_ms, duration_ms)
            continue
        slide = raw.get("slide")
        event = note_or_skip(
            skipped,
            timestamp_ms=start_ms,
            duration_ms=duration_ms,
            midi_note=tuning[string] + fret,
            string=string,
            fret=fret,
            measure=number,
            duration_quarters=length,
            bend=_bend(raw.get("bend")),
            slide_to_next=slide in _SLIDE_TO_NEXT,
            slide_in=_SLIDE_IN.get(slide, 0),
            slide_out=_SLIDE_OUT.get(slide, 0),
            dead=bool(raw.get("dead")),
            let_ring=let_ring,
            palm_mute=palm_mute or bool(raw.get("palmMute")),
        )
        if event is not None:
            notes.append(event)
