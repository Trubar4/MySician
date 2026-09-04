"""Note conversion utilities.

Frequency <-> MIDI note number <-> note name, plus guitar string/fret mapping.
All internal note representation uses MIDI note numbers (0-127).
"""

import math

# A4 reference frequency
A4_FREQ = 440.0
A4_MIDI = 69

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Standard guitar tuning: string 1 (high E) to string 6 (low E)
# pyguitarpro uses 1-indexed strings: 1=high E, 6=low E
STANDARD_TUNING = {
    1: 64,  # E4
    2: 59,  # B3
    3: 55,  # G3
    4: 50,  # D3
    5: 45,  # A2
    6: 40,  # E2
}

# Guitar range: low E open (40) to high E 24th fret (88)
GUITAR_MIDI_MIN = 40
GUITAR_MIDI_MAX = 88

# Number of frets on a standard guitar
MAX_FRETS = 24


def freq_to_midi(freq: float) -> int:
    """Convert frequency in Hz to nearest MIDI note number.

    Uses: round(12 * log2(freq / 440) + 69)
    Returns -1 for invalid frequencies (<= 0).
    """
    if freq <= 0:
        return -1
    return round(12 * math.log2(freq / A4_FREQ) + A4_MIDI)


def freq_to_midi_exact(freq: float) -> float:
    """MIDI note number WITHOUT rounding, so a bend can be measured.

    Everywhere else the app rounds to the nearest semitone, because a note is
    either the written one or not. A bend is the exception: the whole question
    is how far between two semitones the pitch got, and rounding throws away
    exactly that. Returns -1.0 for an invalid frequency.
    """
    if freq <= 0:
        return -1.0
    return 12 * math.log2(freq / A4_FREQ) + A4_MIDI


def midi_to_freq(midi_note: int) -> float:
    """Convert MIDI note number to frequency in Hz."""
    return A4_FREQ * (2 ** ((midi_note - A4_MIDI) / 12))


def midi_to_name(midi_note: int) -> str:
    """Convert MIDI note number to note name with octave (e.g. 'E2', 'C#4')."""
    if midi_note < 0 or midi_note > 127:
        return "?"
    note = NOTE_NAMES[midi_note % 12]
    octave = (midi_note // 12) - 1
    return f"{note}{octave}"


def name_to_midi(name: str) -> int:
    """Convert note name with octave to MIDI number (e.g. 'E2' -> 40, 'C#4' -> 61).

    Returns -1 if the name can't be parsed.
    """
    name = name.strip()
    if len(name) < 2:
        return -1

    # Extract note and octave
    if len(name) >= 3 and name[1] in ("#", "b"):
        note_part = name[:2]
        octave_part = name[2:]
    else:
        note_part = name[0]
        octave_part = name[1:]

    # Handle flats by converting to sharps
    flat_to_sharp = {
        "Db": "C#", "Eb": "D#", "Fb": "E", "Gb": "F#",
        "Ab": "G#", "Bb": "A#", "Cb": "B",
    }
    if note_part in flat_to_sharp:
        note_part = flat_to_sharp[note_part]

    if note_part not in NOTE_NAMES:
        return -1

    try:
        octave = int(octave_part)
    except ValueError:
        return -1

    return NOTE_NAMES.index(note_part) + (octave + 1) * 12


def fret_to_midi(string: int, fret: int, tuning: dict[int, int] | None = None) -> int:
    """Convert guitar string + fret to MIDI note number.

    Args:
        string: String number (1-6, where 1=high E).
        fret: Fret number (0=open).
        tuning: Optional custom tuning dict {string_num: midi_note}.
                Defaults to standard tuning.
    """
    if tuning is None:
        tuning = STANDARD_TUNING
    open_note = tuning.get(string)
    if open_note is None:
        return -1
    return open_note + fret


def midi_to_fret_options(midi_note: int, tuning: dict[int, int] | None = None) -> list[tuple[int, int]]:
    """Find all (string, fret) combinations that produce a given MIDI note.

    Returns list of (string, fret) tuples, sorted by string number (high to low).
    Only returns positions within 0-MAX_FRETS range.
    """
    if tuning is None:
        tuning = STANDARD_TUNING
    options = []
    for string_num, open_midi in tuning.items():
        fret = midi_note - open_midi
        if 0 <= fret <= MAX_FRETS:
            options.append((string_num, fret))
    return sorted(options, key=lambda x: x[0])


def freq_to_cents_deviation(freq: float) -> tuple[int, float]:
    """Return (nearest_midi_note, cents_off) for a frequency.

    cents_off is in range [-50, +50]. Negative = flat, positive = sharp.
    Returns (-1, 0.0) for invalid frequencies.
    """
    if freq <= 0:
        return (-1, 0.0)
    midi_exact = 12 * math.log2(freq / A4_FREQ) + A4_MIDI
    midi_note = round(midi_exact)
    cents = (midi_exact - midi_note) * 100.0
    return (midi_note, cents)


def semitone_distance(midi_a: int, midi_b: int) -> int:
    """Absolute semitone distance between two MIDI notes."""
    return abs(midi_a - midi_b)


def is_in_guitar_range(midi_note: int) -> bool:
    """Check if a MIDI note falls within standard guitar range."""
    return GUITAR_MIDI_MIN <= midi_note <= GUITAR_MIDI_MAX


# Tunings worth naming, as {string: midi} the same way STANDARD_TUNING is
# written. Named ones are what a player actually says out loud ("it's in Drop
# C"); anything else is spelled out note by note instead of guessed at.
# Ordered so the more specific shape wins where two would both match.
NAMED_TUNINGS: list[tuple[str, dict[int, int]]] = [
    ("Standard", STANDARD_TUNING),
    ("Drop D", {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 38}),
    ("Eb Standard", {s: v - 1 for s, v in STANDARD_TUNING.items()}),
    ("Drop C#", {1: 63, 2: 58, 3: 54, 4: 49, 5: 44, 6: 37}),
    ("D Standard", {s: v - 2 for s, v in STANDARD_TUNING.items()}),
    ("Drop C", {1: 62, 2: 57, 3: 53, 4: 48, 5: 43, 6: 36}),
    ("C# Standard", {s: v - 3 for s, v in STANDARD_TUNING.items()}),
    ("Drop B", {1: 61, 2: 56, 3: 52, 4: 47, 5: 42, 6: 35}),
    ("C Standard", {s: v - 4 for s, v in STANDARD_TUNING.items()}),
    ("Drop A#", {1: 60, 2: 55, 3: 51, 4: 46, 5: 41, 6: 34}),
    ("B Standard", {s: v - 5 for s, v in STANDARD_TUNING.items()}),
    ("Drop A", {1: 59, 2: 54, 3: 50, 4: 45, 5: 40, 6: 33}),
    ("Open G", {1: 62, 2: 59, 3: 55, 4: 50, 5: 43, 6: 38}),
    ("Open D", {1: 62, 2: 57, 3: 54, 4: 50, 5: 45, 6: 38}),
    ("Open E", {1: 64, 2: 59, 3: 56, 4: 52, 5: 47, 6: 40}),
    ("DADGAD", {1: 62, 2: 57, 3: 55, 4: 50, 5: 45, 6: 38}),
]


def tuning_name(tuning: dict[int, int] | None) -> str:
    """What a player would call this tuning, or "" when it has no common name.

    An unnamed tuning is not a failure -- plenty of tabs use one -- so the
    caller shows the notes themselves either way and treats this as a label,
    never as the whole answer.
    """
    if not tuning:
        return ""
    for name, shape in NAMED_TUNINGS:
        if all(tuning.get(s) == v for s, v in shape.items()):
            return name
    return ""


def tuning_notes(tuning: dict[int, int] | None) -> list[str]:
    """Open-string note names from the LOWEST string up, as tab writes them.

    Low to high because that is the order a player tunes in and the order the
    strings are named ("E A D G B E"), even though string 1 is the high E.
    """
    if not tuning:
        return []
    return [midi_to_name(tuning[s]).rstrip("0123456789")
            for s in sorted(tuning, reverse=True) if s in tuning]


def tuning_for_notes(letters: str) -> dict[int, int] | None:
    """The named tuning a song's open strings describe, or None.

    Takes what the song list already shows -- "E A D G B E", low string
    first -- and gives back the shape the tuner needs. All sixteen named
    tunings render to distinct letter strings, so this is a lookup and not a
    guess; a song in a tuning nobody named simply gets no answer, which is
    the honest one.
    """
    wanted = " ".join((letters or "").split())
    if not wanted:
        return None
    for _, shape in NAMED_TUNINGS:
        if " ".join(tuning_notes(shape)) == wanted:
            return shape
    return None


def is_standard_tuning(tuning: dict[int, int] | None) -> bool:
    """True when nothing has to be retuned before playing.

    An empty tuning counts as standard: a tab that says nothing is not asking
    for anything, and warning about it would cry wolf on most files.
    """
    if not tuning:
        return True
    return all(tuning.get(s) == v for s, v in STANDARD_TUNING.items())
