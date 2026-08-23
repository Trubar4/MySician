"""Guided reference recorder for detection calibration.

Walks through a fixed list of exercises, shows each one as a tab, counts you
in, and records every take to its own WAV plus a manifest describing exactly
what was supposed to sound. The result is a labelled data set the detection
work can be tuned against instead of against synthetic test tones.

The list deliberately contains WRONG takes as well as right ones (a fifth one
fret off, an open string where a fretted one belongs). Telling right from
wrong is the whole job, so the calibration needs both sides.

Standalone on purpose: only sounddevice + numpy, no aubio and no pygame, so it
still runs when the detection stack is broken -- which is when it is needed
most. Device probing mirrors AudioCapture._resolve_input_settings.

    python tools/record_reference.py            # record everything
    python tools/record_reference.py --list     # just show the exercises
    python tools/record_reference.py --block 2  # only block 2
"""

import argparse
import json
import sys
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Standard tuning as MIDI notes, string 1 = high E ... string 6 = low E.
# Mirrors pickhero.audio.note_utils.STANDARD_TUNING.
STANDARD_TUNING = {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40}
STRING_LABELS = {1: "e", 2: "B", 3: "G", 4: "D", 5: "A", 6: "E"}

# A shape is {string_number: fret}; strings left out are not struck.
Shape = dict


@dataclass
class Exercise:
    """One take: what to play, how to play it, and how long to record."""
    id: str
    block: int
    title: str
    shapes: list = field(default_factory=list)
    technique: str = "normal"      # normal | palm_mute | let_ring | free
    instruction: str = ""
    seconds: float = 6.0
    # Set when the take is deliberately wrong -- shown as a warning so the
    # player does not "correct" it by accident.
    intentional_error: str = ""

    def expected_midi(self, offset: int = 0, drop: int = 0) -> list:
        """MIDI notes per shape, for the tuning the guitar is actually in.

        `offset` shifts every string (Eb, D standard, ...); `drop` lowers the
        sixth string on its own. Drop tunings used to be forbidden here, on
        the grounds that the shapes only hold for an evenly tuned guitar --
        which is true of the shapes and false of the player, who plays metal
        and lives in drop D. The block 7 takes were recorded that way and
        every expected pitch on the low string was two semitones wrong, so
        the whole set scored zero until it was worked out by hand.
        """
        return [
            sorted(STANDARD_TUNING[s] + fret + offset - (drop if s == 6 else 0)
                   for s, fret in shape.items())
            for shape in self.shapes
        ]


def render_tab(shapes: list) -> str:
    """Draw shapes as an ASCII tab, high E on top."""
    if not shapes:
        return "    (nichts spielen)"
    lines = []
    for s in range(1, 7):
        cells = []
        for shape in shapes:
            # every cell is exactly 4 chars wide so the strings line up
            cells.append(f"-{shape[s]:->2d}-" if s in shape else "----")
        lines.append(f"    {STRING_LABELS[s]}|" + "".join(cells) + "|")
    return "\n".join(lines)


# --------------------------------------------------------------- exercises

EXERCISES = [
    # ---- Block 0: reference material for this specific guitar & rig -------
    Exercise("00_silence", 0, "Stille - Rauschboden messen",
             [], instruction="Gitarre nicht anfassen. Einfach still halten.",
             seconds=5),
    *[
        Exercise(f"01_open_{STRING_LABELS[s]}{s}", 0,
                 f"Leersaite {STRING_LABELS[s]} (Saite {s})",
                 [{s: 0}],
                 instruction="Einmal anschlagen und ausklingen lassen.",
                 seconds=5)
        for s in (6, 5, 4, 3, 2, 1)
    ],

    # ---- Block 1: single notes, ground truth for pitch -------------------
    Exercise("10_chrom_E", 1, "Tiefe E-Saite: Bund 0, 1, 2, 3, 5",
             [{6: 0}, {6: 1}, {6: 2}, {6: 3}, {6: 5}],
             instruction="Nacheinander, ca. 1 Sekunde Pause zwischen den Toenen.",
             seconds=10),
    Exercise("11_chrom_A", 1, "A-Saite: Bund 0, 1, 2, 3, 5",
             [{5: 0}, {5: 1}, {5: 2}, {5: 3}, {5: 5}],
             instruction="Nacheinander, ca. 1 Sekunde Pause zwischen den Toenen.",
             seconds=10),
    Exercise("12_same_pitch", 1, "Derselbe Ton (E3) an drei Stellen",
             [{4: 2}, {5: 7}, {6: 12}],
             instruction="Dreimal derselbe Ton, aber auf D-, A- und E-Saite. "
                         "Zeigt, ob wir die Saite auseinanderhalten koennen.",
             seconds=9),

    # ---- Block 2: power chords, the core case ---------------------------
    Exercise("20_E5_ok", 2, "Powerchord E5 - RICHTIG",
             [{6: 0, 5: 2}],
             instruction="Viermal anschlagen, gleichmaessig.", seconds=8),
    Exercise("21_E5_sharp", 2, "Powerchord E5 - Quinte EINEN BUND ZU HOCH",
             [{6: 0, 5: 3}],
             instruction="Viermal anschlagen.", seconds=8,
             intentional_error="A-Saite absichtlich im 3. statt 2. Bund"),
    Exercise("22_E5_flat", 2, "Powerchord E5 - Quinte EINEN BUND ZU TIEF",
             [{6: 0, 5: 1}],
             instruction="Viermal anschlagen.", seconds=8,
             intentional_error="A-Saite absichtlich im 1. statt 2. Bund"),
    Exercise("23_E5_root_only", 2, "Powerchord E5 - nur Grundton",
             [{6: 0}],
             instruction="Nur die tiefe E-Saite, A-Saite gar nicht anschlagen. "
                         "Viermal.", seconds=8,
             intentional_error="Quinte fehlt komplett"),
    Exercise("24_G5_ok", 2, "Powerchord G5 - RICHTIG",
             [{6: 3, 5: 5}],
             instruction="Viermal anschlagen.", seconds=8),
    Exercise("25_G5_sharp", 2, "Powerchord G5 - Quinte EINEN BUND ZU HOCH",
             [{6: 3, 5: 6}],
             instruction="Viermal anschlagen.", seconds=8,
             intentional_error="A-Saite absichtlich im 6. statt 5. Bund"),
    Exercise("26_A5_ok", 2, "Powerchord A5 - RICHTIG",
             [{6: 5, 5: 7}],
             instruction="Viermal anschlagen.", seconds=8),
    Exercise("27_E5_palm", 2, "Powerchord E5 - PALM MUTE",
             [{6: 0, 5: 2}], technique="palm_mute",
             instruction="Handballen auf die Saiten am Steg, acht kurze Chugs.",
             seconds=8),
    Exercise("28_E5_palm_sharp", 2, "Powerchord E5 PALM MUTE - Quinte zu hoch",
             [{6: 0, 5: 3}], technique="palm_mute",
             instruction="Acht kurze Chugs.", seconds=8,
             intentional_error="A-Saite absichtlich im 3. statt 2. Bund"),

    # ---- Block 3: full chords, the hard case ----------------------------
    Exercise("30_Emaj_ok", 3, "E-Dur - RICHTIG",
             [{6: 0, 5: 2, 4: 2, 3: 1, 2: 0, 1: 0}],
             instruction="Dreimal langsam durchschlagen, ausklingen lassen.",
             seconds=9),
    Exercise("31_Emaj_G_open", 3, "E-Dur - G-Saite OFFEN statt 1. Bund",
             [{6: 0, 5: 2, 4: 2, 3: 0, 2: 0, 1: 0}],
             instruction="Finger von der G-Saite nehmen, sonst alles gleich. "
                         "Dreimal durchschlagen.", seconds=9,
             intentional_error="Terz G statt G# - einen Bund zu tief. "
                               "Das ist DER entscheidende Testfall."),
    Exercise("32_Emaj_D_open", 3, "E-Dur - D-Saite OFFEN statt 2. Bund",
             [{6: 0, 5: 2, 4: 0, 3: 1, 2: 0, 1: 0}],
             instruction="Finger von der D-Saite nehmen. Dreimal durchschlagen.",
             seconds=9,
             intentional_error="D statt E - zwei Buende zu tief"),
    Exercise("33_Emaj_no_high_e", 3, "E-Dur - hohe E-Saite AUSGELASSEN",
             [{6: 0, 5: 2, 4: 2, 3: 1, 2: 0}],
             instruction="Normal greifen, aber die hohe E-Saite nicht "
                         "anschlagen. Dreimal.", seconds=9,
             intentional_error="Eine Saite fehlt (klingt trotzdem richtig)"),
    Exercise("34_Amin_ok", 3, "A-Moll - RICHTIG",
             [{5: 0, 4: 2, 3: 2, 2: 1, 1: 0}],
             instruction="Dreimal langsam durchschlagen.", seconds=9),
    Exercise("35_Amin_B_open", 3, "A-Moll - H-Saite OFFEN statt 1. Bund",
             [{5: 0, 4: 2, 3: 2, 2: 0, 1: 0}],
             instruction="Finger von der H-Saite nehmen. Dreimal.", seconds=9,
             intentional_error="H statt C - einen Bund zu tief"),
    Exercise("36_Dmaj_ok", 3, "D-Dur - RICHTIG",
             [{4: 0, 3: 2, 2: 3, 1: 2}],
             instruction="Dreimal durchschlagen, nur die vier hohen Saiten.",
             seconds=9),

    # ---- Block 4: how you actually play ---------------------------------
    Exercise("40_E5_fast", 4, "E5 schnelles Downpicking",
             [{6: 0, 5: 2}],
             instruction="So schnell wie du sauber kannst, durchgehend.",
             seconds=8),
    Exercise("41_riff", 4, "Freies Riff deiner Wahl",
             [], technique="free",
             instruction="Spiel irgendein Riff, das du gut kannst - am besten "
                         "eins mit Powerchords und Palm Mutes.", seconds=15),
    Exercise("42_bend", 4, "Bending auf der G-Saite (7. Bund, ganzer Ton hoch)",
             [{3: 7}], technique="free",
             instruction="Anschlagen und einen Ganzton hochziehen. Dreimal.",
             seconds=9),

    # ---- Block 5: ringing strings, the same line damped and undamped ------
    #
    # The one case no other take contains, and the reason it matters: a line
    # walking ACROSS the neck while the strings it left keep sounding is
    # polyphony, and monophonic YIN reports one pitch for it. That was
    # measured on synthesis (3 of 8 against 8 of 8 damped) and never on a real
    # guitar -- and the player's own play-along takes disagree with it, going
    # 40 for 40 across string changes. But those changes all sit in a slow
    # passage where the previous note has decayed anyway, so the takes cannot
    # settle it either way.
    #
    # These four can. Same line, same player, same session: only the damping
    # and the speed change, so whatever separates them IS the effect.
    *[
        Exercise(f"5{i}_across_{speed}_{damp}", 5,
                 f"Saitenwechsel {'schnell' if speed == 'fast' else 'langsam'}"
                 f" - {'GEDAEMPFT' if damp == 'damped' else 'KLINGEN LASSEN'}",
                 [{6: 5}, {5: 5}, {4: 5}, {3: 5}, {2: 5}, {1: 5}],
                 technique="normal" if damp == "damped" else "let_ring",
                 instruction=(
                     "Alle im 5. Bund, eine Note pro Saite, tief nach hoch "
                     "und zurueck - zwei Durchgaenge. "
                     + ("Jede Saite abdaempfen, sobald du sie verlaesst: "
                        "immer nur EIN Ton gleichzeitig."
                        if damp == "damped" else
                        "NICHTS abdaempfen - alles weiterklingen lassen, "
                        "auch wenn es matschig wird. Genau darum geht es.")
                     + (" Zuegig, etwa zwei Toene pro Sekunde."
                        if speed == "fast" else
                        " Langsam, etwa ein Ton pro Sekunde.")),
                 seconds=14 if speed == "slow" else 9)
        for i, (speed, damp) in enumerate([
            ("slow", "damped"), ("slow", "ringing"),
            ("fast", "damped"), ("fast", "ringing"),
        ])
    ],

    # ---- Block 6: bends, played right and played short --------------------
    #
    # How far a bend went is now scored, and the three thresholds that decide
    # it (a quarter tone of tolerance, half the written hold, four readings of
    # evidence) were fitted to NOTHING -- there was no recording of a bend to
    # fit them to. These takes are that recording.
    #
    # The pairs are what make it a measurement rather than a demonstration: a
    # correct bend and a short one, a held one and one let go at once. A
    # threshold has to pass every correct take AND catch every deliberate
    # error, and the gap between those two demands is the room it has.
    #
    # 65 is the one most likely to embarrass the rule: vibrato swings the
    # pitch either side of the target on purpose, and a rule that counts
    # frames on target can read that as not holding.
    Exercise("60_bend_full_ok", 6, "Ganzton-Bend, gehalten - RICHTIG",
             [{3: 7}], technique="free",
             instruction="7. Bund G-Saite anschlagen, einen GANZEN TON hoch "
                         "ziehen und etwa zwei Sekunden oben halten. Dreimal.",
             seconds=12),
    Exercise("61_bend_half_ok", 6, "Halbton-Bend, gehalten - RICHTIG",
             [{3: 7}], technique="free",
             instruction="Dasselbe, aber nur einen HALBEN Ton hoch. Oben "
                         "halten. Dreimal.",
             seconds=12),
    Exercise("62_bend_too_short", 6, "Ganzton-Bend, absichtlich ZU FLACH",
             [{3: 7}], technique="free",
             instruction="Ziel ist ein ganzer Ton, aber zieh absichtlich nur "
                         "etwa die Haelfte und halte dort. Dreimal.",
             intentional_error="nur etwa ein Halbton statt einem Ganzton",
             seconds=12),
    Exercise("63_bend_not_held", 6, "Ganzton-Bend, absichtlich NICHT GEHALTEN",
             [{3: 7}], technique="free",
             instruction="Ganzen Ton hochziehen und sofort wieder loslassen, "
                         "als waere es ein Vorschlag. Dreimal.",
             intentional_error="oben nicht gehalten",
             seconds=12),
    Exercise("64_bend_release", 6, "Bend und Release",
             [{3: 7}], technique="free",
             instruction="Ganzen Ton hoch, kurz halten, dann kontrolliert "
                         "wieder herunterlassen. Dreimal.",
             seconds=12),
    Exercise("65_bend_vibrato", 6, "Ganzton-Bend mit Vibrato",
             [{3: 7}], technique="free",
             instruction="Ganzen Ton hoch und oben VIBRATO spielen, so wie du "
                         "es normal machst. Dreimal.",
             seconds=12),

    # ---- Block 7: single-note chugs, right and wrong ----------------------
    #
    # The app now credits a palm-muted note whose strike came back with no
    # pitch at all. That is leniency, and the only rule in the app granted
    # without a recording behind it -- because no take contains a SINGLE
    # palm-muted note. Every palm mute recorded so far is a power chord, and a
    # chord is credited by a different rule that was measured.
    #
    # Two numbers settle it, and both need these takes: how often a single
    # chug really does arrive pitchless (if it is rare, the leniency buys
    # nothing and should go), and how many wrong chugs it lets through (a
    # wrong fret still sounds a PITCH, so the answer should be near zero --
    # but "should be" is what this project does not accept).
    #
    # Fast matters on its own: a chug riff runs in eighths, the verification
    # window is trimmed to the gap before the next strike, and under 200 ms it
    # is dropped -- so on exactly this passage no evidence can ever arrive.
    Exercise("70_chug_slow_ok", 7, "Chugs auf der tiefen E-Saite, LANGSAM",
             [{6: 0}], technique="palm_mute",
             instruction="Leere tiefe E-Saite, Handballen auf den Saiten, "
                         "etwa ein Anschlag pro Sekunde. Acht Stueck.",
             seconds=10),
    Exercise("71_chug_fast_ok", 7, "Chugs auf der tiefen E-Saite, SCHNELL",
             [{6: 0}], technique="palm_mute",
             instruction="Dasselbe, aber im Achtel-Tempo wie in einem Riff - "
                         "so schnell du sauber chuggst, durchgehend.",
             seconds=10),
    Exercise("72_chug_fast_sharp", 7,
             "Chugs SCHNELL - absichtlich im 1. Bund statt leer",
             [{6: 1}], technique="palm_mute",
             instruction="Wie eben, aber greif die tiefe E-Saite im 1. Bund. "
                         "Das ist der absichtliche Fehler.",
             intentional_error="1. Bund statt leerer Saite",
             seconds=10),
    Exercise("73_chug_riff", 7, "Chug-Riff mit Wechsel",
             [{6: 0}, {6: 0}, {6: 3}, {6: 0}], technique="palm_mute",
             instruction="Leer, leer, 3. Bund, leer - als Riff, mit Palm Mute, "
                         "vier Durchgaenge.",
             seconds=12),
]


# ------------------------------------------------------------------ audio

def resolve_input_settings(device, preferred_rate=48000):
    """Find a (samplerate, channels) pair the device accepts.

    Same probing order as the app: preferred rate, then the device default,
    then common rates; mono before stereo. USB interfaces often reject 44100
    in Windows shared mode.
    """
    candidates = [int(preferred_rate)]
    try:
        info = sd.query_devices(device, "input")
        default_sr = int(info["default_samplerate"])
        if default_sr not in candidates:
            candidates.append(default_sr)
    except Exception:
        pass
    for sr in (48000, 44100, 96000, 88200, 32000, 22050):
        if sr not in candidates:
            candidates.append(sr)

    for sr in candidates:
        for ch in (1, 2):
            try:
                sd.check_input_settings(device=device, channels=ch,
                                        samplerate=sr, dtype="float32")
                return sr, ch
            except Exception:
                continue
    raise RuntimeError(
        "Kein passendes Format gefunden. Ist das Interface angeschlossen und "
        "nicht von einem anderen Programm belegt?"
    )


def dbfs(x):
    """Peak level of a buffer in dBFS."""
    if x.size == 0:
        return -120.0
    peak = float(np.abs(x).max())
    return 20 * np.log10(peak) if peak > 0 else -120.0


def meter_bar(db, width=34):
    """Render a -60..0 dBFS level bar with a target zone marked."""
    frac = max(0.0, min(1.0, (db + 60.0) / 60.0))
    filled = int(frac * width)
    bar = "".join("#" if i < filled else "." for i in range(width))
    if db > -1.0:
        tag = "UEBERSTEUERT - Gain runter!"
    elif db > -6.0:
        tag = "etwas heiss"
    elif db > -20.0:
        tag = "gut"
    elif db > -35.0:
        tag = "leise"
    else:
        tag = "zu leise - Gain hoch"
    return f"[{bar}] {db:6.1f} dBFS  {tag}"


def record(seconds, device, samplerate, channels, live_meter=True):
    """Record for `seconds` and return a float32 array (frames, channels)."""
    blocks = []
    level = {"db": -120.0}

    def callback(indata, frames, time_info, status):
        blocks.append(indata.copy())
        level["db"] = dbfs(indata)

    with sd.InputStream(device=device, channels=channels,
                        samplerate=samplerate, dtype="float32",
                        blocksize=512, callback=callback):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            remaining = end - time.monotonic()
            if live_meter:
                print(f"\r  REC {remaining:4.1f}s  {meter_bar(level['db'])}   ",
                      end="", flush=True)
            time.sleep(0.05)
    if live_meter:
        print("\r" + " " * 88 + "\r", end="")

    if not blocks:
        return np.zeros((0, channels), dtype=np.float32)
    return np.concatenate(blocks, axis=0)


def write_wav(path, data, samplerate):
    """Write float32 audio as 16-bit PCM WAV."""
    clipped = np.clip(data, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(data.shape[1] if data.ndim > 1 else 1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm.tobytes())


# --------------------------------------------------------------- interface

def choose_device():
    """Pick an input device, defaulting to the one the app is configured for."""
    configured = None
    try:
        from pickhero.config import Config
        configured = Config.load().audio.device_index
    except Exception:
        pass

    print("\nVerfuegbare Eingaenge:")
    inputs = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            inputs.append(idx)
            mark = "  <- in der App eingestellt" if idx == configured else ""
            print(f"  [{idx}] {dev['name']}{mark}")

    if not inputs:
        raise RuntimeError("Kein Eingabegeraet gefunden.")

    default = configured if configured in inputs else inputs[0]
    raw = input(f"\nGeraet-Nummer [{default}]: ").strip()
    return int(raw) if raw else default


def ask_tuning_offset():
    """How the guitar is tuned: a uniform shift, plus a dropped sixth string.

    Both are needed. Asking only for the uniform shift is what invalidated the
    first block 7 recording: the player answered "standard" because five of
    their strings are, and the sixth was in drop D -- so every chug was scored
    against a pitch two semitones above the one that sounded.
    """
    print("\nStimmung der Gitarre:")
    print("  [0] Standard E")
    print("  [1] Eb / einen Halbton runter")
    print("  [2] D standard / einen Ganzton runter")
    print("  [3] C# standard / anderthalb Toene runter")
    raw = input("\nAuswahl [0]: ").strip()
    offset = -{"": 0, "0": 0, "1": 1, "2": 2, "3": 3}.get(raw, 0)

    print("\nIst die tiefste Saite zusaetzlich heruntergestimmt (Drop)?")
    print("  [0] nein")
    print("  [1] Drop: einen Ganzton runter (z.B. Drop D)")
    print("  [2] Drop: anderthalb Toene runter")
    raw = input("\nAuswahl [0]: ").strip()
    drop = {"": 0, "0": 0, "1": 2, "2": 3}.get(raw, 0)
    return offset, drop


def level_check(device, samplerate, channels):
    """Let the player set the interface gain before anything is recorded."""
    print("\n" + "=" * 72)
    print("PEGEL EINSTELLEN")
    print("=" * 72)
    print("Schlag ein paar Mal krachend einen Powerchord an und dreh den")
    print("Gain-Regler am Focusrite so, dass der Balken im gruenen Bereich")
    print("landet ('gut'). Uebersteuerung macht die Aufnahme unbrauchbar.")
    print("\nEnter druecken, wenn der Pegel passt.\n")

    level = {"db": -120.0, "peak": -120.0}

    def callback(indata, frames, time_info, status):
        level["db"] = dbfs(indata)
        level["peak"] = max(level["peak"], level["db"])

    stop = False
    with sd.InputStream(device=device, channels=channels,
                        samplerate=samplerate, dtype="float32",
                        blocksize=512, callback=callback):
        import threading

        def wait_enter():
            nonlocal stop
            input()
            stop = True

        threading.Thread(target=wait_enter, daemon=True).start()
        while not stop:
            print(f"\r  {meter_bar(level['db'])}   ", end="", flush=True)
            time.sleep(0.05)
    print("\r" + " " * 88 + "\r", end="")
    print(f"  Lautester Anschlag: {level['peak']:.1f} dBFS\n")


def show_exercise(ex, index, total, offset, drop=0):
    print("\n" + "=" * 72)
    print(f"[{index}/{total}]  Block {ex.block}  -  {ex.title}")
    print("=" * 72)
    if ex.intentional_error:
        print(f"  ABSICHTLICH FALSCH: {ex.intentional_error}")
        print("  (Das ist kein Fehler von dir - genau so spielen!)\n")
    if ex.shapes:
        print(render_tab(ex.shapes))
        names = " | ".join(
            " ".join(midi_name(m) for m in shape)
            for shape in ex.expected_midi(offset, drop)
        )
        print(f"\n    erwartete Toene: {names}")
    if ex.technique == "palm_mute":
        print("    Technik: PALM MUTE")
    print(f"\n  {ex.instruction}")
    print(f"  Aufnahmedauer: {ex.seconds:.0f}s")


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_name(m):
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def _git(*args, timeout=20):
    """Run a git command in the repo and return its stdout, or None."""
    try:
        import subprocess
        out = subprocess.run(
            ["git", *args], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=timeout,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def current_branch(default="<dein-branch>"):
    """Whatever is checked out right now."""
    name = _git("rev-parse", "--abbrev-ref", "HEAD")
    return name if name and name != "HEAD" else default


def upload_branch():
    """The branch these recordings belong on, which is not always this one.

    The checkout drifts. A recording pushed to a branch nobody is reading is
    a recording that does not exist, and it has happened twice: the hint used
    to name whatever was checked out, which is exactly the thing that was
    wrong. So the branch is read from the repo itself -- UPLOAD_BRANCH, kept
    up to date by whoever is working on it -- and only falls back to the
    checkout when the file is missing.

    Returns (branch, switch_needed).
    """
    _git("fetch", "--quiet", "origin", timeout=60)
    named = None
    marker = REPO_ROOT / "UPLOAD_BRANCH"
    if marker.exists():
        for line in marker.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                named = line
                break
    if not named:
        # No marker (an old checkout, which is the case this has to survive):
        # the most recently updated work branch on the remote is the best
        # guess available, and still better than the local one.
        listing = _git("for-each-ref", "--sort=-committerdate", "--count=1",
                       "--format=%(refname:strip=3)", "refs/remotes/origin/claude")
        named = listing or None
    here = current_branch(default="")
    if not named:
        return here or "<dein-branch>", False
    return named, named != here


def countdown(n=3):
    for i in range(n, 0, -1):
        print(f"\r  {i} ...   ", end="", flush=True)
        time.sleep(1.0)
    print("\r  SPIELEN!   ", end="", flush=True)
    time.sleep(0.25)
    print("\r" + " " * 20 + "\r", end="")


# -------------------------------------------------------------------- main

def practice_tempo():
    """The speed the app is set to play at, as a fraction, or None.

    Read straight out of the app's settings rather than asked for: a take
    played at 80 % is stretched against the written tab, and an analysis that
    does not know that reports the detector hearing a quarter of what it
    actually heard. The recorder is deliberately standalone, so this reads the
    JSON rather than importing the app.
    """
    try:
        path = Path.home() / ".pickhero" / "settings.json"
        value = float(json.loads(path.read_text(encoding="utf-8"))["tempo_factor"])
        return value if 0.4 < value <= 1.0 else None
    except Exception:
        return None


def play_along(device, samplerate, channels, out_dir, seconds, song):
    """Record while the player plays a song in the app, and save the raw take.

    The 29 exercises are isolated notes with a rest after each -- which is the
    case that already works. What no take in that set contains is the case
    that fails: a passage played through, where the strings already struck go
    on ringing under the next note. So this records exactly that, and leaves
    the analysis to be aligned afterwards against the song's known onsets.

    Nothing needs to be lined up while recording. Start this, then start the
    song; tools/analyze_play_along.py finds the offset that best explains the
    onsets it hears, so a few seconds of fumbling at either end cost nothing.
    """
    print("=" * 72)
    print("MITSCHNITT beim Durchspielen")
    print("=" * 72)
    tempo = practice_tempo()
    print(f"  Song:      {song}")
    print(f"  Dauer:     {seconds:.0f}s")
    if tempo is not None:
        print(f"  Tempo:     {tempo * 100:.0f} % (aus den App-Einstellungen)")
    print()
    print("  1. Diese Aufnahme mit Enter starten")
    print("  2. In MySician den Song starten und normal durchspielen")
    print("  3. Spiel wie immer - NICHT extra sauber daempfen.")
    print("     Genau das, was schiefgeht, soll drauf sein.")
    input("\n  [Enter] startet die Aufnahme: ")
    countdown(3)
    audio = record(seconds, device, samplerate, channels)
    peak = dbfs(audio)
    rms_val = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0
    rms_db = 20 * np.log10(rms_val) if rms_val > 0 else -120.0
    print(f"  aufgenommen: Peak {peak:.1f} dBFS, RMS {rms_db:.1f} dBFS")
    write_wav(out_dir / "play_along.wav", audio, samplerate)
    return {
        "id": "play_along",
        "block": 99,
        "title": f"Durchgespielt: {song}",
        "file": "play_along.wav",
        "technique": "free",
        "intentional_error": "",
        "shapes": [],
        "expected_midi": [],
        "song": song,
        "tempo_percent": None if tempo is None else round(tempo * 100),
        "seconds": seconds,
        "peak_dbfs": round(peak, 1),
        "rms_dbfs": round(rms_db, 1),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true",
                    help="Uebungen nur anzeigen, nichts aufnehmen")
    ap.add_argument("--block", type=int, action="append",
                    help="nur diese Bloecke aufnehmen (mehrfach moeglich)")
    ap.add_argument("--out", default=None, help="Zielordner")
    ap.add_argument("--play-along", metavar="SONG", nargs="?",
                    const="timing_test_100bpm.gp5", default=None,
                    help="statt der Uebungen einen Mitschnitt beim "
                         "Durchspielen aufnehmen (Standard: der Timing-Test)")
    ap.add_argument("--seconds", type=float, default=45.0,
                    help="Dauer des Mitschnitts (Standard 45)")
    args = ap.parse_args()

    todo = [e for e in EXERCISES if not args.block or e.block in args.block]

    if args.list:
        for ex in todo:
            print(f"\n[{ex.id}] Block {ex.block} - {ex.title}")
            if ex.intentional_error:
                print(f"    absichtlich falsch: {ex.intentional_error}")
            if ex.shapes:
                print(render_tab(ex.shapes))
        print(f"\n{len(todo)} Uebungen, "
              f"ca. {sum(e.seconds + 6 for e in todo) / 60:.0f} Minuten.")
        return 0

    print("=" * 72)
    print("MySician - Referenzaufnahmen")
    print("=" * 72)
    if args.play_along:
        print("Ein einzelner Mitschnitt beim Durchspielen - kein Uebungssatz.")
    else:
        print("Wir nehmen ein paar kurze Uebungen auf, richtige und absichtlich")
        print("falsche. Damit kalibrieren wir die Akkorderkennung an deiner")
        print("echten Gitarre statt an simulierten Toenen.")
    print("\nWICHTIG: Das Signal muss CLEAN reinkommen - keine Verzerrung,")
    print("kein Amp-Sim vor der Aufnahme. Verzerrung zum Mithoeren ist egal.")

    device = choose_device()
    samplerate, channels = resolve_input_settings(device)
    print(f"\n  Geraet {device}: {samplerate} Hz, "
          f"{'mono' if channels == 1 else 'stereo'}")

    offset, drop = ask_tuning_offset()
    if offset:
        print(f"  Stimmung: {abs(offset)} Halbtoene runter")
    if drop:
        print(f"  Tiefste Saite: {drop} Halbtoene tiefer (Drop)")

    level_check(device, samplerate, channels)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else REPO_ROOT / "reference_recordings" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "samplerate": samplerate,
        "channels": channels,
        "device": str(sd.query_devices(device)["name"]),
        "tuning_offset_semitones": offset,
        "drop_semitones": drop,
        "takes": [],
    }

    print(f"\n  Aufnahmen landen in: {out_dir}")

    if args.play_along:
        manifest["takes"].append(
            play_along(device, samplerate, channels, out_dir,
                       args.seconds, args.play_along)
        )
        with open(out_dir / "manifest.json", "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, ensure_ascii=False)
        todo = []

    if todo:
        print("\nEnter startet jede Aufnahme. 's' ueberspringt, 'q' beendet.")

    idx = 0
    while idx < len(todo):
        ex = todo[idx]
        show_exercise(ex, idx + 1, len(todo), offset, drop)

        key = input("\n  [Enter] aufnehmen  /  s = ueberspringen  /  q = Ende: ")
        key = key.strip().lower()
        if key == "q":
            break
        if key == "s":
            idx += 1
            continue

        countdown(3)
        audio = record(ex.seconds, device, samplerate, channels)

        peak = dbfs(audio)
        rms_val = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0
        rms_db = 20 * np.log10(rms_val) if rms_val > 0 else -120.0

        print(f"  aufgenommen: Peak {peak:.1f} dBFS, RMS {rms_db:.1f} dBFS")
        if ex.shapes and peak < -40:
            print("  WARNUNG: fast kein Signal. Kabel? Gain? Richtiger Eingang?")
        elif peak > -0.5:
            print("  WARNUNG: uebersteuert. Gain am Interface runterdrehen.")

        again = input("  [Enter] weiter  /  w = Wiederholen: ").strip().lower()
        if again == "w":
            continue

        wav_name = f"{ex.id}.wav"
        write_wav(out_dir / wav_name, audio, samplerate)
        manifest["takes"].append({
            "id": ex.id,
            "block": ex.block,
            "title": ex.title,
            "file": wav_name,
            "technique": ex.technique,
            "intentional_error": ex.intentional_error,
            "shapes": [{str(s): f for s, f in shape.items()} for shape in ex.shapes],
            "expected_midi": ex.expected_midi(offset, drop),
            "seconds": ex.seconds,
            "peak_dbfs": round(peak, 1),
            "rms_dbfs": round(rms_db, 1),
        })
        with open(out_dir / "manifest.json", "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, ensure_ascii=False)
        idx += 1

    print("\n" + "=" * 72)
    print(f"Fertig - {len(manifest['takes'])} Aufnahmen in {out_dir}")
    print("=" * 72)
    if manifest["takes"]:
        rel = out_dir.relative_to(REPO_ROOT) if out_dir.is_relative_to(REPO_ROOT) else out_dir
        branch, switch_needed = upload_branch()
        print("\nZum Hochladen, damit ich sie analysieren kann:\n")
        if switch_needed:
            print(f"  ! Du bist gerade auf '{current_branch()}',")
            print(f"    die Aufnahmen gehoeren aber auf '{branch}'.")
            print("    Der Wechsel unten nimmt die neuen Dateien mit.\n")
        print("  git fetch origin")
        print(f"  git switch {branch}")
        print(f'  git add "{rel}"')
        print('  git commit -m "Add reference recordings for detection calibration"')
        print(f"  git push -u origin {branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
