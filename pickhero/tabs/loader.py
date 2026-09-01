"""Guitar Pro file loader.

Parses GP3/GP4/GP5 files via pyguitarpro, and GP7/GP8 files (ZIP+XML) via
stdlib xml.etree. Builds note timelines for both formats.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import guitarpro

from pickhero.audio.midi_playback import (
    NOTE_ON, NOTE_OFF, PROGRAM_CHANGE, BackingTrack, MidiEvent,
)
from pickhero.tabs import gpx
from pickhero.tabs.timeline import MeasureInfo, NoteEvent, SongMetadata, Timeline

# GP tick resolution: 960 ticks = 1 quarter note
TICKS_PER_QUARTER = 960

# MIDI program numbers for guitar instruments (General MIDI)
GUITAR_INSTRUMENT_MIN = 24  # Nylon String Guitar
GUITAR_INSTRUMENT_MAX = 31  # Guitar Harmonics


class TempoMap:
    """Maps absolute tick positions to milliseconds, handling tempo changes."""

    def __init__(self, initial_tempo: int):
        self._changes: list[tuple[int, int]] = [(0, initial_tempo)]

    def add_change(self, tick: int, tempo: int) -> None:
        self._changes.append((tick, tempo))
        self._changes.sort(key=lambda x: x[0])

    def tick_to_ms(self, tick: int) -> float:
        """Convert absolute tick position to milliseconds.

        Accumulates time through all tempo changes up to the target tick.
        A tempo change at the target tick does NOT affect the time up to it.
        """
        ms = 0.0
        prev_tick = 0
        prev_bpm = self._changes[0][1]

        for change_tick, change_bpm in self._changes[1:]:
            if change_tick >= tick:
                break
            delta = change_tick - prev_tick
            ms += (delta / TICKS_PER_QUARTER) * (60_000 / prev_bpm)
            prev_tick = change_tick
            prev_bpm = change_bpm

        delta = tick - prev_tick
        ms += (delta / TICKS_PER_QUARTER) * (60_000 / prev_bpm)
        return ms

    def tempo_at_tick(self, tick: int) -> int:
        """Return the active tempo (BPM) at a given tick position."""
        bpm = self._changes[0][1]
        for change_tick, change_bpm in self._changes:
            if change_tick > tick:
                break
            bpm = change_bpm
        return bpm

    def duration_ticks_to_ms(self, ticks: int, at_tick: int) -> float:
        """Convert a relative duration (in ticks) to ms using the tempo at at_tick."""
        bpm = self.tempo_at_tick(at_tick)
        return (ticks / TICKS_PER_QUARTER) * (60_000 / bpm)


@dataclass(frozen=True)
class BarRepeat:
    """What one bar says about repeats, in the terms both formats share.

    GP3-5 and GPIF spell this differently -- `isRepeatOpen` / `repeatClose` /
    `repeatAlternative` against `<Repeat start= end= count=>` and
    `<AlternateEndings>` -- but they mean the same three things, so the order
    is worked out once and every reader of a file is handed the same answer.
    """

    open: bool = False        # the bar carries |:
    close_count: int = 0      # the bar carries :| and the section plays N times
    alternatives: int = 0     # bitmask, bit k set = this bar belongs to ending k+1


# A malformed file must not hang the app. No real song is longer than this in
# bars, and a tab that claims to be is a tab nobody can play anyway.
MAX_PLAYED_BARS = 20_000

# pyguitarpro counts a quarter note as this many ticks.
_GP_QUARTER_TICKS = 960.0


def repeat_order(bars: Sequence[BarRepeat]) -> list[int]:
    """The order the bars are actually PLAYED in, as written-bar indices.

    The loader used to walk the bars once each and ignore repeat signs
    entirely. A tab that syncs to a recording only because it repeats is then
    shorter in the app than the music is: the picture and the backing start
    together and drift apart by the length of every repeat that was skipped.
    Independent of practice speed, because that stretches both alike -- which
    is exactly how the player described it.

    Alternate endings are a bitmask per bar: bit k means the bar is played on
    the (k+1)-th pass. A bar whose mask excludes the current pass is skipped,
    which is what makes "[1. ... :|] [2. ...]" come out right.
    """
    order: list[int] = []
    index = 0
    start = 0            # where the current repeated section begins
    pass_no = 1          # which time through that section this is
    while index < len(bars) and len(order) < MAX_PLAYED_BARS:
        bar = bars[index]
        # Re-entering the SAME section must not reset the pass counter; only
        # arriving at a new |: does.
        if bar.open and index != start:
            start, pass_no = index, 1
        if bar.alternatives and not bar.alternatives & (1 << (pass_no - 1)):
            index += 1                      # an ending meant for another pass
            continue
        order.append(index)
        if bar.close_count > 1 and pass_no < bar.close_count:
            pass_no += 1
            index = start
            continue
        if bar.close_count:
            # A later :| with no |: of its own repeats from here, which is what
            # GP means by a close without an open.
            start, pass_no = index + 1, 1
        index += 1
    return order


@dataclass(frozen=True)
class PlayedBar:
    """One pass through one written bar.

    Repeats mean a written bar can be played more than once, at different
    times. Everything that walks a song -- the notes, the measure ranges, the
    backing track -- needs the same answer to "when is this bar played", so
    the answer is produced once and handed to all of them. Four call sites
    each doing their own arithmetic is how the notes and the backing would
    come to disagree, which is the same drift one level down.
    """

    play_index: int          # position in the played song
    written_index: int       # which bar of the FILE this is
    start_ms: float          # when it is played
    written_start_ms: float  # where it sits in written time


def played_bars(repeats: Sequence[BarRepeat],
                written_starts: Sequence[float],
                written_ends: Sequence[float]) -> list[PlayedBar]:
    """Lay the written bars out in playing order, with real times."""
    out: list[PlayedBar] = []
    now = 0.0
    for written in repeat_order(repeats):
        out.append(PlayedBar(len(out), written, now, written_starts[written]))
        now += max(0.0, written_ends[written] - written_starts[written])
    return out


def _bar_repeat(header) -> BarRepeat:
    """Read one pyguitarpro measure header's repeat signs.

    GP3-5 counts REPEATS, not passes: `repeatClose == 1` means "go back once",
    so the section is played twice. The distinction is not cosmetic -- read as
    a pass count it means "play once", and a first/second-time ending then
    never reaches its second bar. The reference demo file lost exactly one bar
    that way, which is how this was caught.
    """
    close = getattr(header, "repeatClose", -1)
    close = 0 if close is None or close < 1 else close + 1
    return BarRepeat(
        open=bool(getattr(header, "isRepeatOpen", False)),
        close_count=close,
        alternatives=int(getattr(header, "repeatAlternative", 0) or 0),
    )


def is_guitar_track(track: guitarpro.Track) -> bool:
    """Check if a track is a guitar track.

    Criteria: 6 strings, MIDI instrument 24-31, not percussion.
    """
    return (
        len(track.strings) == 6
        and GUITAR_INSTRUMENT_MIN <= track.channel.instrument <= GUITAR_INSTRUMENT_MAX
        and not track.channel.isPercussionChannel
    )


def _build_tempo_map(song: guitarpro.Song) -> TempoMap:
    """Scan first track for tempo changes and build a TempoMap."""
    tempo_map = TempoMap(song.tempo)
    if not song.tracks:
        return tempo_map
    track = song.tracks[0]
    for measure in track.measures:
        for voice in measure.voices:
            for beat in voice.beats:
                mtc = beat.effect.mixTableChange
                if mtc and mtc.tempo:
                    tempo_map.add_change(beat.start, mtc.tempo.value)
    return tempo_map


def _extract_tuning(track: guitarpro.Track) -> dict[int, int]:
    """Extract string tuning as {string_number: midi_note}."""
    return {i + 1: s.value for i, s in enumerate(track.strings)}


# pyguitarpro normalises bend points to 0..12 across the note and its values
# to semitones, so both only need scaling into the shapes NoteEvent wants.
_BEND_MAX_POSITION = 12.0

# guitarpro.models.SlideType, spelled out so a missing enum member cannot
# silently turn a slide into no slide.
_SLIDE_INTO_FROM_ABOVE = -2
_SLIDE_INTO_FROM_BELOW = -1
_SLIDE_SHIFT_TO = 1
_SLIDE_LEGATO_TO = 2
_SLIDE_OUT_DOWNWARDS = 3
_SLIDE_OUT_UPWARDS = 4


def _extract_bend(note) -> tuple[tuple[float, float], ...]:
    """The bend curve as ((position 0..1, semitones), ...), () if unbent."""
    effect = getattr(note, "effect", None)
    bend = getattr(effect, "bend", None) if effect is not None else None
    points = getattr(bend, "points", None) if bend is not None else None
    if not points:
        return ()
    curve = tuple(
        (min(1.0, max(0.0, p.position / _BEND_MAX_POSITION)), float(p.value))
        for p in points
    )
    # A curve that never leaves zero is a bend the tab declares and does not
    # use; drawing an arc of no height would only add noise.
    return curve if any(v > 0 for _, v in curve) else ()


def _extract_slides(note) -> tuple[bool, int, int]:
    """(slide_to_next, slide_in, slide_out) for one note."""
    effect = getattr(note, "effect", None)
    slides = getattr(effect, "slides", None) if effect is not None else None
    if not slides:
        return False, 0, 0
    values = {getattr(s, "value", s) for s in slides}
    to_next = bool(values & {_SLIDE_SHIFT_TO, _SLIDE_LEGATO_TO})
    slide_in = (1 if _SLIDE_INTO_FROM_BELOW in values
                else -1 if _SLIDE_INTO_FROM_ABOVE in values else 0)
    slide_out = (1 if _SLIDE_OUT_UPWARDS in values
                 else -1 if _SLIDE_OUT_DOWNWARDS in values else 0)
    return to_next, slide_in, slide_out


def _song_plan(song: guitarpro.Song, tempo_map: TempoMap) -> list[PlayedBar]:
    """When each written bar of this song is played.

    The repeat signs live on the measure HEADERS, which every track shares, so
    one plan serves the notes, the measure ranges and the backing track alike.
    """
    headers = list(getattr(song, "measureHeaders", []) or [])
    if not headers:
        return []
    starts = [tempo_map.tick_to_ms(h.start) for h in headers]
    ends = [tempo_map.tick_to_ms(h.start + h.length) for h in headers]
    return played_bars([_bar_repeat(h) for h in headers], starts, ends)


def _extract_notes(track: guitarpro.Track, tempo_map: TempoMap,
                   plan: Sequence[PlayedBar]) -> list[NoteEvent]:
    """Extract all playable notes from a track, in playing order."""
    notes = []
    for bar in plan:
        if bar.written_index >= len(track.measures):
            continue        # a track shorter than the song's bar count
        measure = track.measures[bar.written_index]
        # A repeated bar is the same written music at a different time.
        shift = bar.start_ms - bar.written_start_ms
        for voice in measure.voices:
            for beat in voice.beats:
                timestamp_ms = tempo_map.tick_to_ms(beat.start) + shift
                duration_ms = tempo_map.duration_ticks_to_ms(
                    beat.duration.time, beat.start
                )
                for note in beat.notes:
                    # Keep normal (1) and dead (3), skip rest (0).
                    # A tie (2) is not struck, but it is not nothing either:
                    # it lengthens the note it continues.
                    if note.type.value == 2:
                        _extend_tied(notes, note.string,
                                     timestamp_ms, duration_ms)
                        continue
                    if note.type.value not in (1, 3):
                        continue
                    to_next, slide_in, slide_out = _extract_slides(note)
                    effect = getattr(note, "effect", None)
                    notes.append(
                        NoteEvent(
                            timestamp_ms=timestamp_ms,
                            duration_ms=duration_ms,
                            midi_note=note.realValue,
                            string=note.string,
                            fret=note.value,
                            measure=bar.play_index,
                            duration_quarters=(beat.duration.time
                                               / _GP_QUARTER_TICKS),
                            bend=_extract_bend(note),
                            slide_to_next=to_next,
                            slide_in=slide_in,
                            slide_out=slide_out,
                            # GP flags the note the legato STARTS from, the
                            # same way it flags a shift slide.
                            hammer_to_next=bool(
                                getattr(effect, "hammer", False)
                            ),
                            # Type 3 is a dead note: kept as an event because
                            # it is played, flagged because its written fret
                            # names a hand position, not a pitch.
                            let_ring=bool(getattr(effect, "letRing", False)),
                            dead=note.type.value == 3,
                            palm_mute=bool(
                                getattr(effect, "palmMute", False)
                            ),
                        )
                    )
    return notes


def _extract_measures(track: guitarpro.Track, tempo_map: TempoMap,
                     plan: Sequence[PlayedBar]) -> list[MeasureInfo]:
    """Measure time ranges, in playing order.

    Numbered by where they are PLAYED rather than by where they are written,
    because that is the bar the player is looking at: a repeated section is
    two passes on screen and saying "bar 12" twice would make the weakest-
    section report name a place nobody can find.
    """
    measures = []
    for bar in plan:
        beats_in_measure = []
        beats = beat_type = 4
        if bar.written_index < len(track.measures):
            signature = getattr(track.measures[bar.written_index].header,
                                "timeSignature", None)
            if signature is not None:
                beats = int(getattr(signature, "numerator", 4) or 4)
                denominator = getattr(signature, "denominator", None)
                beat_type = int(getattr(denominator, "value", 4) or 4)
            for voice in track.measures[bar.written_index].voices:
                for beat in voice.beats:
                    beats_in_measure.append(beat.start)
                    beats_in_measure.append(beat.start + beat.duration.time)

        if beats_in_measure:
            shift = bar.start_ms - bar.written_start_ms
            start_ms = tempo_map.tick_to_ms(min(beats_in_measure)) + shift
            end_ms = tempo_map.tick_to_ms(max(beats_in_measure)) + shift
        else:
            # Empty measure — use previous end or 0
            start_ms = measures[-1].end_ms if measures else bar.start_ms
            end_ms = start_ms

        measures.append(MeasureInfo(index=bar.play_index,
                                    start_ms=start_ms, end_ms=end_ms,
                                    beats=beats, beat_type=beat_type))
    return measures


def list_tracks(path: str | Path) -> list[dict]:
    """List all tracks in a GP file with metadata.

    Returns a list of dicts with keys: index, name, strings, instrument,
    is_percussion, is_guitar, tuning.

    `tuning` is {string number: MIDI note} with string 1 the HIGH e, the same
    shape as `note_utils.STANDARD_TUNING` -- so `tuning_notes` names it low to
    high the way a player says it, "E A D G B E".
    """
    if _is_gpif_file(path):
        return _list_gpif_tracks(path)

    song = guitarpro.parse(str(path))
    tracks = []
    for i, track in enumerate(song.tracks):
        tracks.append(
            {
                "index": i,
                "name": track.name,
                "strings": len(track.strings),
                "instrument": track.channel.instrument,
                "is_percussion": track.channel.isPercussionChannel,
                "is_guitar": is_guitar_track(track),
                "tuning": _extract_tuning(track),
            }
        )
    return tracks


def _list_gpif_tracks(path: str | Path) -> list[dict]:
    """The same listing for a GPIF score, so TAB offers its tracks too.

    Without this the track picker was empty for every GP6/GP7/GP8 file: the
    caller asks for the list, the plain parser refuses the format, and the
    exception is swallowed into "no tracks" -- which looks like a song with
    one track rather than like a format that was never read.
    """
    root = ET.fromstring(_read_gpif(path))
    tracks = []
    tracks_el = root.find("Tracks")
    for index, t in enumerate(tracks_el.findall("Track") if tracks_el is not None
                              else []):
        pitches: list[int] = []
        for prop in t.findall(".//Property"):
            if prop.get("name") == "Tuning":
                pitches = [int(x) for x in prop.findtext("Pitches", "").split()
                           if x.lstrip("-").isdigit()]
        try:
            program = int(t.findtext(".//MIDI/Program", "-1"))
        except ValueError:
            program = -1
        is_drum = (program == 1024
                   or t.findtext(".//InstrumentSet", "") == "drums")
        tracks.append({
            "index": index,
            "name": t.findtext("Name", "").strip(),
            "strings": len(pitches),
            "instrument": program,
            "is_percussion": is_drum,
            "is_guitar": (len(pitches) == 6 and not is_drum
                          and GUITAR_INSTRUMENT_MIN <= program
                          <= GUITAR_INSTRUMENT_MAX),
            # GPIF writes the pitches low string first; string 1 is the high e.
            "tuning": {len(pitches) - i: v for i, v in enumerate(pitches)},
        })
    return tracks


def _is_gp7_file(path: str | Path) -> bool:
    """Check if a file is GP7/GP8 format (ZIP with Content/score.gpif)."""
    try:
        return zipfile.is_zipfile(str(path))
    except OSError:
        return False


def _is_gpif_file(path: str | Path) -> bool:
    """Whether the score inside is GPIF XML — GP6, GP7 or GP8.

    Three Guitar Pro generations write the same XML and differ only in what
    they wrap it in: GP7 and GP8 use a zip, GP6 uses a container of its own.
    Everything past this point is the same parser.
    """
    return _is_gp7_file(path) or gpx.is_gpx_file(path)


def _read_gpif(path: str | Path) -> str:
    """The score XML, whichever wrapper it arrived in."""
    if gpx.is_gpx_file(path):
        return gpx.read_gpif(path)
    with zipfile.ZipFile(str(path)) as zf:
        return zf.read("Content/score.gpif").decode("utf-8")


# ── GP7/GP8 loader ──────────────────────────────────────────────────────────

# Rhythm note value → duration in quarter notes
_GP7_NOTE_VALUES = {
    "Whole": 4.0, "Half": 2.0, "Quarter": 1.0, "Eighth": 0.5,
    "16th": 0.25, "32nd": 0.125, "64th": 0.0625,
}


# GPIF writes a bend value of 50 for one semitone -- the unit GP5 used, kept
# through GP6, GP7 and GP8 -- and a bend position as a percentage of the note.
# Read out of alphaTab, which converts by 1/25 into a unit that is itself half
# a semitone, and confirmed against its GP6 test files.
_GPIF_BEND_PER_SEMITONE = 50.0
_GPIF_BEND_MAX_OFFSET = 100.0

# The bits of a GPIF <Slide><Flags>. Only the ones that change a pitch matter
# here; a pick slide (64, 128) is a noise effect and carries no target.
_GPIF_SLIDE_SHIFT_TO_NEXT = 1
_GPIF_SLIDE_LEGATO_TO_NEXT = 2
_GPIF_SLIDE_OUT_DOWN = 4
_GPIF_SLIDE_OUT_UP = 8
_GPIF_SLIDE_IN_BELOW = 16
_GPIF_SLIDE_IN_ABOVE = 32


@dataclass
class _GpifNote:
    """One <Note> of a GPIF score, with everything the app scores it by."""
    fret: int
    string: int                        # 0-indexed, 0 = low E
    dead: bool = False
    palm_mute: bool = False
    bend: tuple[tuple[float, float], ...] = ()
    slide_to_next: bool = False
    slide_in: int = 0
    slide_out: int = 0
    hammer_to_next: bool = False
    let_ring: bool = False
    # A note that CONTINUES the one before it on the same string. It is not
    # struck -- the string is already ringing -- so it must extend that note
    # rather than become one of its own, which is what "held long and not
    # played twice" means on paper and is what the player reported seeing.
    tie_to_previous: bool = False


def _gpif_float(prop: ET.Element) -> float:
    try:
        return float(prop.findtext("Float", "0"))
    except ValueError:
        return 0.0


def _gpif_bend(values: dict[str, float]) -> tuple[tuple[float, float], ...]:
    """The bend curve as ((position 0..1, semitones), ...), () if unbent.

    GPIF states a bend as an origin, up to two middle points and a
    destination, each a value and a position along the note. Most of them are
    left out of the file when they are what you would assume, and the
    assumptions are Guitar Pro's own: the origin sits at the start, the
    destination at the end, and a middle value with no position of its own
    sits halfway. Reading a missing position as zero instead -- which is what
    this did first -- puts the top of the bend at the moment the string is
    struck, and the scoring then asks the player to hold a pitch they have not
    reached yet.
    """
    def semitones(key: str) -> float:
        return values.get(key, 0.0) / _GPIF_BEND_PER_SEMITONE

    def position(key: str) -> float:
        return min(1.0, max(0.0, values.get(key, 0.0) / _GPIF_BEND_MAX_OFFSET))

    points = [(position("BendOriginOffset"), semitones("BendOriginValue"))]
    middle = semitones("BendMiddleValue")
    if "BendMiddleValue" in values:
        # A zero offset counts as absent, the way Guitar Pro writes it.
        offsets = [position(key) for key in ("BendMiddleOffset1",
                                             "BendMiddleOffset2")
                   if values.get(key)]
        for offset in (offsets or [0.5]):
            points.append((offset, middle))
    points.append((position("BendDestinationOffset")
                   if values.get("BendDestinationOffset") else 1.0,
                   semitones("BendDestinationValue")))
    # Points written twice collapse into one; a curve with a duplicate draws
    # as a kink that is not in the music.
    curve = tuple(dict.fromkeys(points))
    return curve if any(value > 0 for _, value in curve) else ()


def _extend_tied(events: list[NoteEvent], string: int,
                 start_ms: float, duration_ms: float) -> None:
    """Make the note this one continues last through it.

    A tie is one note written twice, and both formats had only the first
    half of that right: the continuation was skipped (GP3-5) or played again
    (GPIF), and in neither case did the note it belongs to get any longer.
    So a note held across two beats was drawn for one -- or, on a GP6/7/8
    file, drawn twice, which is what the player reported.

    Nothing is appended: a tied note is not struck, and giving it an event of
    its own would ask for a pick the music does not contain.
    """
    for i in range(len(events) - 1, -1, -1):
        event = events[i]
        if event.string == string:
            # NoteEvent is frozen on purpose -- a note that can be edited in
            # place is a note some later pass can quietly move.
            events[i] = replace(event, duration_ms=max(
                event.duration_ms,
                start_ms + duration_ms - event.timestamp_ms))
            return


def _parse_gpif_notes(root: ET.Element) -> dict[str, _GpifNote]:
    """Every <Note> in a GPIF score, by id.

    Techniques included. They were left out for a whole GP7 cycle on the
    grounds that "a bend needs its whole curve read out of the Properties
    block" -- which is true, and is eight lines. The cost of leaving them out
    was that a GP6 or GP7 tab scored its bends and slides as wrong notes,
    because the matcher only widens a note's accepted pitch range when the tab
    says a technique is there.
    """
    notes: dict[str, _GpifNote] = {}
    notes_el = root.find("Notes")
    if notes_el is None:
        return notes
    for n in notes_el.findall("Note"):
        nid = n.get("id", "")
        fret = string_val = None
        dead = palm_mute = bended = let_ring = False
        # GPIF writes <Tie origin=".." destination=".."/> on the Note itself,
        # not as a Property. "destination" is the half that is not played.
        tie_el = n.find("Tie")
        tie_to_previous = (tie_el is not None
                           and tie_el.get("destination", "false") == "true")
        slide_flags = 0
        hammer = False
        bend_values: dict[str, float] = {}
        for prop in n.findall(".//Property"):
            pname = prop.get("name", "")
            # Flags rather than values: GPIF writes them as an empty <Enable/>
            # child, so the property being present IS the answer.
            if pname == "Muted":
                dead = prop.find("Enable") is not None
            elif pname == "PalmMuted":
                palm_mute = prop.find("Enable") is not None
            elif pname == "LetRing":
                let_ring = prop.find("Enable") is not None
            elif pname == "Bended":
                bended = prop.find("Enable") is not None
            elif pname == "HopoOrigin":
                # The note the hammer-on or pull-off starts FROM, which is the
                # same end GP3-5 flag it at.
                hammer = prop.find("Enable") is not None
            elif pname == "Slide":
                try:
                    slide_flags = int(prop.findtext("Flags", "0"))
                except ValueError:
                    slide_flags = 0
            elif pname.startswith("Bend"):
                bend_values[pname] = _gpif_float(prop)
            elif pname == "Fret":
                try:
                    fret = int(prop.findtext("Fret", "0"))
                except ValueError:
                    pass
            elif pname == "String":
                try:
                    raw = float(prop.findtext("String", "0"))
                    if raw != int(raw):
                        continue          # fractional = drum/percussion, skip
                    string_val = int(raw)
                except ValueError:
                    pass
        if fret is None or string_val is None:
            continue
        notes[nid] = _GpifNote(
            fret=fret,
            string=string_val,
            tie_to_previous=tie_to_previous,
            let_ring=let_ring,
            dead=dead,
            palm_mute=palm_mute,
            bend=_gpif_bend(bend_values) if bended else (),
            slide_to_next=bool(slide_flags & (_GPIF_SLIDE_SHIFT_TO_NEXT
                                              | _GPIF_SLIDE_LEGATO_TO_NEXT)),
            slide_in=(1 if slide_flags & _GPIF_SLIDE_IN_BELOW
                      else -1 if slide_flags & _GPIF_SLIDE_IN_ABOVE else 0),
            slide_out=(1 if slide_flags & _GPIF_SLIDE_OUT_UP
                       else -1 if slide_flags & _GPIF_SLIDE_OUT_DOWN else 0),
            hammer_to_next=hammer,
        )
    return notes


def _gpif_bar_times(master_bars: Sequence[ET.Element],
                    tempos: dict[int, float],
                    initial_bpm: float) -> tuple[list[float], list[float],
                                                 list[float],
                                                 list[tuple[int, int]]]:
    """(bpm, written start, length, time signature) for every written bar."""
    bpm = initial_bpm
    starts: list[float] = []
    lengths: list[float] = []
    bpms: list[float] = []
    signatures: list[tuple[int, int]] = []
    now = 0.0
    for idx, mb in enumerate(master_bars):
        if idx in tempos:
            bpm = tempos[idx]
        parts = mb.findtext("Time", "4/4").split("/")
        ts_num = int(parts[0]) if len(parts) == 2 else 4
        ts_den = int(parts[1]) if len(parts) == 2 else 4
        length = ts_num * (4.0 / ts_den) * (60_000.0 / bpm)
        bpms.append(bpm)
        starts.append(now)
        lengths.append(length)
        signatures.append((ts_num, ts_den))
        now += length
    return bpms, starts, lengths, signatures


def _gpif_bar_repeat(mb: ET.Element) -> BarRepeat:
    """Read one GPIF MasterBar's repeat signs.

    GPIF counts PASSES where GP3-5 counts repeats: `<Repeat end="true"
    count="2">` is played twice, while GP5's `repeatClose == 1` also means
    twice. The two conventions are off by one from each other, which is why
    each format converts in its own reader and `BarRepeat.close_count` is
    documented as the number of times the section is PLAYED. A count below 2
    is not a repeat at all, and is read as none.
    """
    open_ = False
    count = 0
    rep = mb.find("Repeat")
    if rep is not None:
        open_ = str(rep.get("start", "false")).lower() == "true"
        if str(rep.get("end", "false")).lower() == "true":
            try:
                count = int(rep.get("count", "2") or 2)
            except ValueError:
                count = 2
            count = count if count >= 2 else 0
    alternatives = 0
    for part in (mb.findtext("AlternateEndings") or "").replace(",", " ").split():
        try:
            alternatives |= 1 << (int(part) - 1)
        except ValueError:
            continue
    return BarRepeat(open=open_, close_count=count, alternatives=alternatives)


def _load_gp7_file(path: str | Path, track_index: int | None = None) -> Timeline:
    """Load a GPIF score — GP6 (.gpx), GP7 or GP8 (.gp)."""
    root = ET.fromstring(_read_gpif(path))

    # ── Parse lookup tables ──────────────────────────────────────────────

    # Rhythms: id → duration in quarter notes
    rhythms: dict[str, float] = {}
    rhythms_el = root.find("Rhythms")
    if rhythms_el is not None:
        for r in rhythms_el.findall("Rhythm"):
            rid = r.get("id", "")
            nv = r.findtext("NoteValue", "Quarter")
            base = _GP7_NOTE_VALUES.get(nv, 1.0)
            dot = r.find("AugmentationDot")
            if dot is not None:
                dot_count = int(dot.get("count", "1"))
                if dot_count == 1:
                    base *= 1.5
                elif dot_count >= 2:
                    base *= 1.75
            tuplet = r.find("PrimaryTuplet")
            if tuplet is not None:
                num = int(tuplet.get("num", "1"))
                den = int(tuplet.get("den", "1"))
                if num > 0:
                    base = base * den / num
            rhythms[rid] = base

    notes_map = _parse_gpif_notes(root)

    # Beats: id → (rhythm_ref, [note_ids])
    beats_map: dict[str, tuple[str, list[str]]] = {}
    beats_el = root.find("Beats")
    if beats_el is not None:
        for b in beats_el.findall("Beat"):
            bid = b.get("id", "")
            rhythm_ref_el = b.find("Rhythm")
            rhythm_ref = rhythm_ref_el.get("ref", "") if rhythm_ref_el is not None else ""
            notes_text = b.findtext("Notes", "").strip()
            note_ids = notes_text.split() if notes_text else []
            beats_map[bid] = (rhythm_ref, note_ids)

    # Voices: id → [beat_ids]
    voices_map: dict[str, list[str]] = {}
    voices_el = root.find("Voices")
    if voices_el is not None:
        for v in voices_el.findall("Voice"):
            vid = v.get("id", "")
            beats_text = v.findtext("Beats", "").strip()
            voices_map[vid] = beats_text.split() if beats_text else []

    # Bars: id → [voice_ids]
    bars_map: dict[str, list[str]] = {}
    bars_el = root.find("Bars")
    if bars_el is not None:
        for bar in bars_el.findall("Bar"):
            bid = bar.get("id", "")
            voices_text = bar.findtext("Voices", "").strip()
            bars_map[bid] = voices_text.split() if voices_text else []

    # ── Parse tracks ─────────────────────────────────────────────────────

    track_list: list[dict] = []
    tracks_el = root.find("Tracks")
    if tracks_el is not None:
        for t in tracks_el.findall("Track"):
            tuning_pitches: list[int] = []
            for prop in t.findall(".//Property"):
                if prop.get("name") == "Tuning":
                    raw = prop.findtext("Pitches", "")
                    tuning_pitches = [int(x) for x in raw.split() if x]
            midi_prog = -1
            try:
                midi_prog = int(t.findtext(".//MIDI/Program", "-1"))
            except ValueError:
                pass
            is_drum = (midi_prog == 1024 or
                       t.findtext(".//InstrumentSet", "") == "drums")
            # Heuristic: drums have instrumentId=1024 in the search API;
            # in GPIF the program is the GM number. Drum kits use channel 10.
            track_list.append({
                "id": t.get("id", ""),
                "name": t.findtext("Name", "").strip(),
                "tuning": tuning_pitches,
                "midi_program": midi_prog,
                "num_strings": len(tuning_pitches),
                "is_guitar": (
                    len(tuning_pitches) == 6
                    and GUITAR_INSTRUMENT_MIN <= midi_prog <= GUITAR_INSTRUMENT_MAX
                ),
                "is_percussion": is_drum,
            })

    # ── Select track ─────────────────────────────────────────────────────

    if track_index is not None:
        selected_index = track_index
    else:
        selected_index = 0
        for i, ti in enumerate(track_list):
            if ti["is_guitar"]:
                selected_index = i
                break

    sel = track_list[selected_index]
    tuning = sel["tuning"]  # [low_E, A, D, G, B, high_E] (0-indexed)
    num_strings = sel["num_strings"] or 6

    # Build our tuning dict: {1: high_E_midi, ..., 6: low_E_midi}
    tuning_dict: dict[int, int] = {}
    for gp7_idx, midi_val in enumerate(tuning):
        our_string = num_strings - gp7_idx  # 0→6, 1→5, ..., 5→1
        tuning_dict[our_string] = midi_val

    # ── Parse tempos ─────────────────────────────────────────────────────

    tempos: dict[int, float] = {}  # master_bar_index → BPM
    for auto in root.findall(".//MasterTrack/Automations/Automation"):
        if auto.findtext("Type") == "Tempo":
            bar_idx = int(auto.findtext("Bar", "0"))
            val = auto.findtext("Value", "120 2")
            bpm = float(val.split()[0])
            tempos[bar_idx] = bpm

    initial_bpm = tempos.get(0, 120.0)

    # ── Walk MasterBars → extract notes and measures ─────────────────────

    master_bars = root.findall(".//MasterBar")
    # Written time first: a bar's tempo and length are properties of where it
    # is WRITTEN, and the repeat signs only decide the order it is played in.
    bar_bpm, bar_start, bar_length, bar_sig = _gpif_bar_times(
        master_bars, tempos, initial_bpm)
    plan = played_bars([_gpif_bar_repeat(mb) for mb in master_bars],
                       bar_start,
                       [a + b for a, b in zip(bar_start, bar_length)])

    note_events: list[NoteEvent] = []
    measure_infos: list[MeasureInfo] = []

    for played in plan:
        mb_idx = played.written_index
        mb = master_bars[mb_idx]
        current_bpm = bar_bpm[mb_idx]
        current_ms = played.start_ms
        measure_start_ms = current_ms
        measure_duration_ms = bar_length[mb_idx]

        # Get bar IDs for this master bar (one per track)
        bar_ids_text = mb.findtext("Bars", "").strip()
        bar_ids = bar_ids_text.split()

        if selected_index < len(bar_ids):
            bar_id = bar_ids[selected_index]
            voice_ids = bars_map.get(bar_id, [])

            # Walk voices → beats → notes
            for vid in voice_ids:
                if vid == "-1":
                    continue
                beat_ids = voices_map.get(vid, [])
                beat_pos_ms = current_ms

                for bid in beat_ids:
                    rhythm_ref, note_ids = beats_map.get(bid, ("", []))
                    dur_quarters = rhythms.get(rhythm_ref, 1.0)
                    dur_ms = dur_quarters * (60_000.0 / current_bpm)

                    for nid in note_ids:
                        if nid not in notes_map:
                            continue
                        gpif_note = notes_map[nid]
                        fret, gp7_string = gpif_note.fret, gpif_note.string
                        our_string = num_strings - gp7_string
                        if not 1 <= our_string <= 6:
                            continue
                        if gp7_string < len(tuning):
                            midi_note = tuning[gp7_string] + fret
                        else:
                            midi_note = fret  # fallback

                        if not 0 <= midi_note <= 127:
                            continue

                        if gpif_note.tie_to_previous:
                            _extend_tied(note_events, our_string,
                                         beat_pos_ms, dur_ms)
                            continue

                        note_events.append(NoteEvent(
                            timestamp_ms=beat_pos_ms,
                            duration_ms=dur_ms,
                            midi_note=midi_note,
                            string=our_string,
                            fret=fret,
                            measure=played.play_index,
                            duration_quarters=dur_quarters,
                            bend=gpif_note.bend,
                            slide_to_next=gpif_note.slide_to_next,
                            slide_in=gpif_note.slide_in,
                            slide_out=gpif_note.slide_out,
                            hammer_to_next=gpif_note.hammer_to_next,
                            let_ring=gpif_note.let_ring,
                            dead=gpif_note.dead,
                            palm_mute=gpif_note.palm_mute,
                        ))

                    beat_pos_ms += dur_ms

        measure_infos.append(MeasureInfo(
            index=played.play_index,
            start_ms=measure_start_ms,
            end_ms=measure_start_ms + measure_duration_ms,
            beats=bar_sig[mb_idx][0],
            beat_type=bar_sig[mb_idx][1],
        ))

    # ── Build metadata and timeline ──────────────────────────────────────

    title = root.findtext(".//Score/Title", "").strip()
    artist = root.findtext(".//Score/Artist", "").strip()
    album = root.findtext(".//Score/Album", "").strip()

    metadata = SongMetadata(
        title=title,
        artist=artist,
        album=album,
        track_name=sel["name"],
        tempo=int(initial_bpm),
        tuning=tuning_dict,
        track_index=selected_index,
    )

    return Timeline(note_events, metadata, measures=measure_infos)


def _extract_gp7_backing_track(
    path: str | Path,
    exclude_track_indices: set[int] | None = None,
) -> BackingTrack:
    """Extract non-guitar tracks from a GPIF score as MIDI events."""
    root = ET.fromstring(_read_gpif(path))

    # Reuse the same lookup tables as _load_gp7_file
    # Rhythms
    rhythms: dict[str, float] = {}
    rhythms_el = root.find("Rhythms")
    if rhythms_el is not None:
        for r in rhythms_el.findall("Rhythm"):
            rid = r.get("id", "")
            nv = r.findtext("NoteValue", "Quarter")
            base = _GP7_NOTE_VALUES.get(nv, 1.0)
            dot = r.find("AugmentationDot")
            if dot is not None:
                dot_count = int(dot.get("count", "1"))
                if dot_count == 1:
                    base *= 1.5
                elif dot_count >= 2:
                    base *= 1.75
            tuplet = r.find("PrimaryTuplet")
            if tuplet is not None:
                num = int(tuplet.get("num", "1"))
                den = int(tuplet.get("den", "1"))
                if num > 0:
                    base = base * den / num
            rhythms[rid] = base

    # Notes: id → (fret, gp7_string)
    notes_map = _parse_gpif_notes(root)

    # Beats
    beats_map: dict[str, tuple[str, list[str]]] = {}
    beats_el = root.find("Beats")
    if beats_el is not None:
        for b in beats_el.findall("Beat"):
            bid = b.get("id", "")
            rhythm_ref_el = b.find("Rhythm")
            rhythm_ref = rhythm_ref_el.get("ref", "") if rhythm_ref_el is not None else ""
            notes_text = b.findtext("Notes", "").strip()
            beats_map[bid] = (rhythm_ref, notes_text.split() if notes_text else [])

    # Voices
    voices_map: dict[str, list[str]] = {}
    voices_el = root.find("Voices")
    if voices_el is not None:
        for v in voices_el.findall("Voice"):
            vid = v.get("id", "")
            beats_text = v.findtext("Beats", "").strip()
            voices_map[vid] = beats_text.split() if beats_text else []

    # Bars
    bars_map: dict[str, list[str]] = {}
    bars_el = root.find("Bars")
    if bars_el is not None:
        for bar in bars_el.findall("Bar"):
            bid = bar.get("id", "")
            voices_text = bar.findtext("Voices", "").strip()
            bars_map[bid] = voices_text.split() if voices_text else []

    # Tracks
    track_list: list[dict] = []
    tracks_el = root.find("Tracks")
    if tracks_el is not None:
        for t in tracks_el.findall("Track"):
            tuning_pitches: list[int] = []
            for prop in t.findall(".//Property"):
                if prop.get("name") == "Tuning":
                    raw = prop.findtext("Pitches", "")
                    tuning_pitches = [int(x) for x in raw.split() if x]
            midi_prog = -1
            try:
                midi_prog = int(t.findtext(".//MIDI/Program", "-1"))
            except ValueError:
                pass
            track_list.append({
                "tuning": tuning_pitches,
                "midi_program": midi_prog,
                "num_strings": len(tuning_pitches),
                "is_guitar": (
                    len(tuning_pitches) == 6
                    and GUITAR_INSTRUMENT_MIN <= midi_prog <= GUITAR_INSTRUMENT_MAX
                ),
            })

    if exclude_track_indices is None:
        exclude_track_indices = {
            i for i, ti in enumerate(track_list) if ti["is_guitar"]
        }

    # Tempos
    tempos: dict[int, float] = {}
    for auto in root.findall(".//MasterTrack/Automations/Automation"):
        if auto.findtext("Type") == "Tempo":
            bar_idx = int(auto.findtext("Bar", "0"))
            val = auto.findtext("Value", "120 2")
            tempos[bar_idx] = float(val.split()[0])

    initial_bpm = tempos.get(0, 120.0)

    # Walk master bars and extract MIDI events for non-excluded tracks
    master_bars = root.findall(".//MasterBar")
    events: list[MidiEvent] = []

    # Program changes at t=0
    for i, ti in enumerate(track_list):
        if i in exclude_track_indices:
            continue
        if ti["midi_program"] >= 0:
            channel = i % 16
            events.append(MidiEvent(
                timestamp_ms=0.0,
                channel=channel,
                event_type=PROGRAM_CHANGE,
                data1=ti["midi_program"],
                data2=0,
            ))

    current_bpm = initial_bpm
    current_ms = 0.0

    # The SAME plan _load_gp7_file lays the notes out on. A second walk of its
    # own is how the picture and the backing would come to disagree.
    bar_bpm, bar_start, bar_length, bar_sig = _gpif_bar_times(
        master_bars, tempos, initial_bpm)
    plan = played_bars([_gpif_bar_repeat(mb) for mb in master_bars],
                       bar_start,
                       [a + b for a, b in zip(bar_start, bar_length)])

    for played in plan:
        mb = master_bars[played.written_index]
        current_bpm = bar_bpm[played.written_index]
        current_ms = played.start_ms

        bar_ids_text = mb.findtext("Bars", "").strip()
        bar_ids = bar_ids_text.split()

        for track_idx, bar_id in enumerate(bar_ids):
            if track_idx in exclude_track_indices:
                continue
            if track_idx >= len(track_list):
                continue

            ti = track_list[track_idx]
            tuning = ti["tuning"]
            channel = track_idx % 16

            voice_ids = bars_map.get(bar_id, [])
            for vid in voice_ids:
                if vid == "-1":
                    continue
                beat_ids = voices_map.get(vid, [])
                beat_pos_ms = current_ms

                for bid in beat_ids:
                    rhythm_ref, note_ids = beats_map.get(bid, ("", []))
                    dur_quarters = rhythms.get(rhythm_ref, 1.0)
                    dur_ms = dur_quarters * (60_000.0 / current_bpm)

                    for nid in note_ids:
                        if nid not in notes_map:
                            continue
                        fret = notes_map[nid].fret
                        gp7_string = notes_map[nid].string
                        if gp7_string < len(tuning):
                            midi_note = tuning[gp7_string] + fret
                        else:
                            midi_note = fret
                        midi_note = max(0, min(127, midi_note))

                        events.append(MidiEvent(
                            timestamp_ms=beat_pos_ms,
                            channel=channel,
                            event_type=NOTE_ON,
                            data1=midi_note,
                            data2=80,
                        ))
                        events.append(MidiEvent(
                            timestamp_ms=beat_pos_ms + dur_ms,
                            channel=channel,
                            event_type=NOTE_OFF,
                            data1=midi_note,
                            data2=0,
                        ))

                    beat_pos_ms += dur_ms


    return BackingTrack(events)


def load_gp_file(path: str | Path, track_index: int | None = None) -> Timeline:
    """Load a GP file and return a Timeline.

    Args:
        path: Path to GP3/GP4/GP5/GP7/GP8 file.
        track_index: Explicit track index to load. If None, auto-selects
                     the first guitar track (or track 0 as fallback).
    """
    if _is_gpif_file(path):
        return _load_gp7_file(path, track_index)

    song = guitarpro.parse(str(path))
    tempo_map = _build_tempo_map(song)

    # Select track
    if track_index is not None:
        selected_index = track_index
        track = song.tracks[track_index]
    else:
        selected_index = 0
        track = song.tracks[0]  # default fallback
        for i, t in enumerate(song.tracks):
            if is_guitar_track(t):
                selected_index = i
                track = t
                break

    plan = _song_plan(song, tempo_map)
    notes = _extract_notes(track, tempo_map, plan)
    measures = _extract_measures(track, tempo_map, plan)

    metadata = SongMetadata(
        title=song.title or "",
        artist=song.artist or "",
        album=song.album or "",
        track_name=track.name or "",
        tempo=song.tempo,
        tuning=_extract_tuning(track),
        track_index=selected_index,
    )

    return Timeline(notes, metadata, measures=measures)


def extract_backing_track(
    path: str | Path,
    exclude_track_indices: set[int] | None = None,
) -> BackingTrack:
    """Extract non-guitar tracks from a GP file as MIDI events.

    Args:
        path: Path to GP3/GP4/GP5/GP7/GP8 file.
        exclude_track_indices: Track indices to exclude. If None, auto-excludes
                               all guitar tracks.

    Returns:
        BackingTrack with note_on, note_off, and program_change events.
    """
    if _is_gpif_file(path):
        return _extract_gp7_backing_track(path, exclude_track_indices)

    song = guitarpro.parse(str(path))
    tempo_map = _build_tempo_map(song)
    # The SAME plan the notes are laid out on. Two walks of their own is how
    # the picture and the backing would come to disagree -- the very drift
    # this expansion exists to remove, reintroduced one level down.
    plan = _song_plan(song, tempo_map)

    if exclude_track_indices is None:
        exclude_track_indices = {
            i for i, t in enumerate(song.tracks) if is_guitar_track(t)
        }

    events: list[MidiEvent] = []

    for i, track in enumerate(song.tracks):
        if i in exclude_track_indices:
            continue

        # Determine MIDI channel: percussion on channel 9, others use GP channel
        if track.channel.isPercussionChannel:
            channel = 9
        else:
            channel = (track.channel.channel - 1) % 16  # GP is 1-indexed

        # Program change at t=0 for non-percussion tracks
        if not track.channel.isPercussionChannel:
            events.append(MidiEvent(
                timestamp_ms=0.0,
                channel=channel,
                event_type=PROGRAM_CHANGE,
                data1=track.channel.instrument,
                data2=0,
            ))

        # Extract note events
        for bar in plan:
            if bar.written_index >= len(track.measures):
                continue
            shift = bar.start_ms - bar.written_start_ms
            for voice in track.measures[bar.written_index].voices:
                for beat in voice.beats:
                    timestamp_ms = tempo_map.tick_to_ms(beat.start) + shift
                    duration_ms = tempo_map.duration_ticks_to_ms(
                        beat.duration.time, beat.start,
                    )

                    for note in beat.notes:
                        if note.type.value not in (1, 3):
                            continue

                        velocity = note.velocity if note.velocity > 0 else 80
                        midi_note = note.realValue

                        events.append(MidiEvent(
                            timestamp_ms=timestamp_ms,
                            channel=channel,
                            event_type=NOTE_ON,
                            data1=midi_note,
                            data2=velocity,
                        ))
                        events.append(MidiEvent(
                            timestamp_ms=timestamp_ms + duration_ms,
                            channel=channel,
                            event_type=NOTE_OFF,
                            data1=midi_note,
                            data2=0,
                        ))

    return BackingTrack(events)
