"""Whether this machine can run MySician, and which half is missing if not.

Usage:  python tools/doctor.py

Written for a SECOND computer. The app had only ever been started on one
Windows laptop, and the first run on a Mac tests every assumption in it at
once: a compiler for aubio (which has no macOS wheel at all, only a 2019
sdist), Tk for the clipboard and the file chooser, a microphone the
operating system may simply refuse to hand over, and a Retina screen whose
pixels are not the window's pixels.

Every one of those fails the SAME way without this: the app starts, draws a
frame, and then quietly does nothing. That is this project's oldest lesson,
written down a dozen times over -- a feature that cannot be seen working is
indistinguishable from one that does not work -- and on a new machine it is
every feature at once, which is why the first session there is otherwise
spent photographing a screen and typing the error back.

Three rules it follows:

- **Nothing may raise.** A doctor that dies on the first missing dependency
  cannot report the other eleven, so every check catches everything and the
  reason is REPORTED rather than swallowed. Hence no imports at module
  level either: a missing numpy must be a line, not a traceback.
- **It says what it MEASURED, not only whether it passed.** "ok" with no
  number cannot be compared against the next machine, and half the value of
  this is a report that can be pasted into a conversation.
- **It goes through the app's own code wherever the app has some** -- the
  resolver, the mixer, the engraver's self-check, the clipboard seam -- so
  the doctor can never pass something the app then fails. Two readers of one
  answer is how they come to disagree, which this project has paid for at
  the repeats, at the transpose and at the shifted keys.

The microphone is checked TWICE on purpose, and the pair is the point. The
raw read says whether the DEVICE delivers anything at all; the capture path
says what the app's own resolver then makes of it. A guitar in input 2 of a
two-in interface arrives as silence if the resolver asks for one channel --
that fault has been shipped here once -- and only the independent read can
tell it from a device that is handing over nothing.

**A denied microphone delivers exact zeros**, which is what makes that
distinguishable from a quiet room at all: a room never measures exactly
zero. The zero half is measured; that macOS specifically answers a refused
permission that way is reported rather than measured, because the machine
this was written on has no audio device.
"""

from __future__ import annotations

import math
import os
import platform
import sys
import time
from pathlib import Path

# pygame prints a two-line greeting on import, which would land in the
# middle of this report. A report that cannot be pasted whole is worth less.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK, WARN, FAIL = "ok", "warn", "fail"

#: Where the pitch starts rotting, measured by attenuating a real take and
#: reading it back through the real detector: right 91 % of the time at
#: -38 dB, 83 % at -44, 52 % at -50. See "A Weak Input Does Not Lose Notes".
QUIET_PEAK_DB = -38.0

#: How long to listen. Long enough that a block boundary cannot be the whole
#: reading, short enough that nobody waits for it.
LISTEN_S = 1.2


def _db(peak: float) -> str:
    if peak <= 0.0:
        return "-inf dB"
    return "%.1f dB" % (20.0 * math.log10(peak))


# -- the checks --------------------------------------------------------------
#
# Each returns (status, headline, *extra lines). The extra lines are for the
# fix, which is the half a player can act on: naming the missing thing
# without naming the fix is a sentence he can do nothing with, and that is
# exactly what came back the one time this project tried it.


def check_python():
    """The interpreter, and whether it is the one the wheels are built for.

    `platform.machine()` is here for one trap: an x86_64 Python on Apple
    Silicon runs under Rosetta, installs x86_64 wheels, and is slower at
    everything without saying so anywhere.
    """
    v = sys.version_info
    where = "venv" if sys.prefix != sys.base_prefix else "system Python"
    head = ("%d.%d.%d %s, %s at %s"
            % (v.major, v.minor, v.micro, platform.machine(), where,
               sys.prefix))
    if (v.major, v.minor) < (3, 10):
        return (FAIL, head, "MySician needs Python 3.10 or newer.")
    if sys.platform == "darwin" and platform.machine() != "arm64":
        return (WARN, head,
                "This Python is not arm64. On Apple Silicon it runs under",
                "Rosetta and installs the wrong wheels. Install Python from",
                "python.org (arm64) and build the venv again.")
    if sys.prefix == sys.base_prefix:
        return (WARN, head,
                "Not in a virtual environment. Fine, but a venv keeps this",
                "project's pinned versions off the system Python.")
    return (OK, head)


#: (import name, pip name, required). yt_dlp is optional: it serves the
#: audio half of the Songsterr download, which is blocked at YouTube's bot
#: check anyway, and the app says so in words when it is missing.
PACKAGES = (
    ("numpy", "numpy", True),
    ("aubio", "aubio", True),
    ("sounddevice", "sounddevice", True),
    ("guitarpro", "pyguitarpro", True),
    ("pygame", "pygame", True),
    ("verovio", "verovio", True),
    ("resvg_py", "resvg-py", True),
    ("yt_dlp", "yt-dlp", False),
)


def _version(mod, pip_name: str) -> str:
    """The version, from wherever this package keeps it.

    Four of the eight carry no `__version__` at all, and "aubio ?" is not a
    thing anybody can compare against the other machine.
    """
    ver = getattr(mod, "__version__", None)
    if ver is None:
        ver = getattr(getattr(mod, "version", None), "ver", None)
    if ver is None:
        try:
            from importlib.metadata import version
            ver = version(pip_name)
        except Exception:
            ver = None
    return str(ver) if ver else "?"


def check_packages():
    """Every dependency, with the version it answers.

    A version rather than a tick, because "verovio is installed" and
    "verovio 6 is installed where the code was written against 5" are
    different reports and only one of them is actionable.
    """
    import importlib
    found, missing, optional = [], [], []
    for name, pip_name, required in PACKAGES:
        try:
            mod = importlib.import_module(name)
        except Exception as exc:
            (missing if required else optional).append(
                "%s (%s: %s)" % (pip_name, type(exc).__name__, exc))
            continue
        found.append("%s %s" % (name, _version(mod, pip_name)))
    head = ", ".join(found) if found else "nothing imported"
    if missing:
        lines = ["Missing: " + "; ".join(missing),
                 "Fix: pip install -r requirements.txt"]
        if any(m.startswith("aubio") for m in missing):
            lines += ["aubio has no macOS wheel and must be compiled, so it",
                      "needs a compiler: xcode-select --install"]
        return (FAIL, head, *lines)
    if optional:
        return (WARN, head, "Optional, not installed: " + "; ".join(optional))
    return (OK, head)


def check_aubio_reads_a_sine():
    """A 440 Hz sine through the app's own detector, which must say MIDI 69.

    An aubio that imports and then returns nothing is the exact failure this
    project keeps writing chapters about, and it is what a module built
    against a different numpy ABI looks like from the outside. The sine is
    the same instrument `tests/test_detector.py` uses.
    """
    try:
        import numpy as np
        from pickhero.audio.detector import PitchDetector
    except Exception as exc:
        return (FAIL, "could not load the detector: %s: %s"
                % (type(exc).__name__, exc))
    try:
        det = PitchDetector(buf_size=2048, hop_size=512, sample_rate=44100,
                            confidence_threshold=0.3, onset_threshold=0.3,
                            noise_gate_db=-80.0)
        t = np.arange(int(44100 * 0.4), dtype=np.float32) / 44100.0
        signal = (0.5 * np.sin(2 * math.pi * 440.0 * t)).astype(np.float32)
        heard = []
        for i in range(0, len(signal) - 512 + 1, 512):
            note = det.process(signal[i:i + 512])
            if note is not None:
                heard.append(note.midi_note)
    except Exception as exc:
        return (FAIL, "the detector raised: %s: %s"
                % (type(exc).__name__, exc))
    if not heard:
        return (FAIL, "a clean 440 Hz sine produced no pitch at all",
                "aubio imported but is not working. Reinstall it against",
                "this numpy: pip install --no-cache-dir --force-reinstall aubio")
    common = max(set(heard), key=heard.count)
    head = "440 Hz sine -> MIDI %d over %d readings" % (common, len(heard))
    if common != 69:
        return (FAIL, head, "Expected MIDI 69. aubio is reading wrongly.")
    return (OK, head)


def mic_verdict(peak: float):
    """What a measured peak means. Separate from the device on purpose.

    This is the one rule in the file with a number in it, and the one that
    matters most on a machine nobody has run the app on -- so it is testable
    without an audio device, which is also the only way it can be tested at
    all on the machine it was written on.
    """
    if peak == 0.0:
        return (FAIL,
                "Every single sample is exactly zero. That is not a quiet",
                "room -- a room never measures zero. Either the operating",
                "system is not letting this process hear the microphone, or",
                "the input is muted, or nothing is plugged in.",
                "Fix: System Settings > Privacy & Security > Microphone,",
                "and allow the app you started this from (VS Code).")
    if 20.0 * math.log10(peak) < QUIET_PEAK_DB:
        return (WARN,
                "Quiet. The pitch starts rotting below %.0f dB, so turn the"
                % QUIET_PEAK_DB,
                "interface gain up before reading anything into a low score.",
                "(Measured while nothing was played this is normal.)")
    return (OK,)


def check_microphone():
    """Does the input device deliver anything at all.

    Independent of the app on purpose: this opens the device itself, so a
    resolver that asks for the wrong channel count cannot hide a device that
    is working, and a device handing over silence cannot look like a quiet
    guitar.

    EXACT zeros are the thing worth separating out. A room measures
    something; a refused microphone, a muted input and an empty socket
    measure nothing at all, and on macOS the first of those three is a
    dialog nobody saw.
    """
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as exc:
        return (FAIL, "sounddevice is not usable: %s: %s"
                % (type(exc).__name__, exc))
    try:
        info = sd.query_devices(None, "input")
        name = info["name"]
        rate = int(info["default_samplerate"])
    except Exception as exc:
        return (FAIL, "no default input device: %s: %s"
                % (type(exc).__name__, exc),
                "Plug the interface in, or pick a device with D in the",
                "song list once the app runs.")
    blocks = []
    try:
        with sd.InputStream(channels=1, samplerate=rate,
                            dtype="float32") as stream:
            until = time.monotonic() + LISTEN_S
            while time.monotonic() < until:
                chunk, _ = stream.read(1024)
                blocks.append(np.asarray(chunk, dtype=np.float32).copy())
    except Exception as exc:
        return (FAIL, "could not read from %s: %s: %s"
                % (name, type(exc).__name__, exc),
                "On macOS this is where a refused microphone shows up.",
                "Fix: System Settings > Privacy & Security > Microphone,",
                "and allow the app you started this from (VS Code).")
    if not blocks:
        return (FAIL, "%s returned no audio at all" % name)
    audio = np.concatenate(blocks)
    peak = float(np.max(np.abs(audio)))
    head = "%s at %d Hz, peak %s over %.1f s" % (name, rate, _db(peak),
                                                 LISTEN_S)
    verdict = mic_verdict(peak)
    return (verdict[0], head, *verdict[1:])


def check_capture_path():
    """The app's own capture: which device it resolves to, and at what rate.

    This is the line the run log carries, and on a machine listing the same
    interface under several host APIs it is the whole question -- three
    entries of one Focusrite have been measured here keeping the clock flat
    and one drifting at 6.5 ms/s.
    """
    try:
        from pickhero.audio.input import AudioCapture
        from pickhero.config import Config
    except Exception as exc:
        return (FAIL, "could not load the capture: %s: %s"
                % (type(exc).__name__, exc))
    capture = None
    try:
        capture = AudioCapture(Config.load())
        capture.start()
        time.sleep(LISTEN_S)
        loudest = -120.0
        for _ in range(20):
            loudest = max(loudest, capture.get_signal_db())
            time.sleep(0.01)
        head = "%s, loudest %.1f dB" % (capture.describe_device(), loudest)
        dropped = getattr(capture, "dropped_buffers", None)
        if dropped:
            head += ", %d dropped buffer(s)" % dropped
        return (OK, head)
    except Exception as exc:
        return (FAIL, "the app's capture could not start: %s: %s"
                % (type(exc).__name__, exc))
    finally:
        if capture is not None:
            try:
                capture.stop()
            except Exception:
                pass


def check_output():
    """The mixer, through the app's own door, and the rate it really got.

    The rate is ASKED for and not granted: Windows and CoreAudio both run a
    shared mixer at the device's own rate, so the app lets SDL open at that
    rate and reports back what it was given. A log printing only the answer
    cannot say whether the device chose it or the app did.
    """
    try:
        from pickhero.audio import output
    except Exception as exc:
        return (FAIL, "could not load the output: %s: %s"
                % (type(exc).__name__, exc))
    try:
        if not output.ensure_mixer():
            return (WARN, "the mixer would not open",
                    "No backing track and no metronome. The notes still",
                    "scroll and the microphone still works.")
        return (OK, output.describe())
    except Exception as exc:
        return (WARN, "the mixer raised: %s: %s" % (type(exc).__name__, exc))


def check_display():
    """What a window is, in pixels, which is not what the screen is.

    Retina is the question here and this does not answer it, it MEASURES it:
    a drawing surface the same size as the window on a 2x screen means the
    operating system is scaling the picture up, and whether that reads as
    soft is for the eye in front of it. This project has spent two days on a
    sharpness complaint that turned out to be neither the frame rate nor the
    machine, so the numbers come first.
    """
    try:
        import pygame
    except Exception as exc:
        return (FAIL, "pygame is not usable: %s: %s"
                % (type(exc).__name__, exc))
    try:
        pygame.display.init()
        desktops = pygame.display.get_desktop_sizes()
        pygame.display.set_mode((640, 360), pygame.RESIZABLE)
        window = pygame.display.get_window_size()
        surface = pygame.display.get_surface().get_size()
        driver = pygame.display.get_driver()
    except Exception as exc:
        return (FAIL, "no window could be opened: %s: %s"
                % (type(exc).__name__, exc))
    finally:
        try:
            pygame.display.quit()
        except Exception:
            pass
    head = ("driver %s, desktop %s, window %dx%d, drawing surface %dx%d"
            % (driver, desktops, window[0], window[1],
               surface[0], surface[1]))
    if window != surface:
        return (OK, head, "The surface is larger than the window: high-DPI.")
    if sys.platform == "darwin":
        return (OK, head,
                "Window and surface are the same size, so the picture is",
                "drawn at window size and scaled up by the screen. Look at",
                "a two-digit fret number and say whether it is sharp.")
    return (OK, head)


def check_engraver():
    """verovio, asked the way the app asks it: on a worker thread.

    Its resource path is thread-local and set at import, in the main thread,
    so on any other thread the toolkit constructs without complaint, loads
    the score without complaint, and renders an empty page. Asked on the
    main thread this reports `ready` for a build whose every page is blank.
    """
    try:
        from pickhero.ui.scrolling import _engraver_state
    except Exception as exc:
        return (WARN, "could not ask: %s: %s" % (type(exc).__name__, exc))
    try:
        state = _engraver_state()
    except Exception as exc:
        return (WARN, "the check raised: %s: %s" % (type(exc).__name__, exc))
    if state == "ready":
        return (OK, state)
    return (WARN, state,
            "Shift+T (the engraved page) will be blank. Everything else",
            "works. Fix: pip install -U verovio resvg-py")


def check_clipboard():
    """Tk, and a round trip through the seam the two keys really use.

    Tk is the one dependency that is not a wheel: python.org's installer
    brings it, Homebrew's Python does not without `brew install python-tk`,
    and on macOS Tk wants the main thread that SDL has already taken. So
    this creates a window the way the app does rather than only importing
    the module -- importing is not where it fails.

    Whatever is on the clipboard is put back afterwards. A diagnostic that
    eats the link you were about to paste is a diagnostic you run once.
    """
    try:
        import tkinter
    except Exception as exc:
        return (WARN, "tkinter is missing: %s: %s"
                % (type(exc).__name__, exc),
                "Ctrl+C, Ctrl+V and the file chooser (Shift+U) will not",
                "work; nothing else is affected.",
                "Fix: install Python from python.org, or with Homebrew",
                "also run: brew install python-tk")
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.destroy()
    except Exception as exc:
        return (WARN, "Tk would not open a window: %s: %s"
                % (type(exc).__name__, exc),
                "The clipboard keys and the file chooser will not work.")
    try:
        from pickhero.ui import clipboard
    except Exception as exc:
        return (WARN, "could not load the clipboard seam: %s: %s"
                % (type(exc).__name__, exc))
    had = clipboard.clipboard_text()
    marker = "mysician-doctor"
    wrote = clipboard.put_clipboard(marker)
    read_back = clipboard.clipboard_text()
    if had:
        clipboard.put_clipboard(had)
    if not wrote:
        return (WARN, "could not copy: %s" % (clipboard.last_error or "?"))
    if read_back != marker:
        return (WARN, "copied, but read back %r" % read_back,
                "Ctrl+C works and Ctrl+V does not. %s"
                % (clipboard.last_error or ""))
    return (OK, "Tk opens, and a copy reads back")


def check_ffmpeg():
    """The app's own answer about the audio half of a download."""
    try:
        from pickhero.tabs import youtube
    except Exception as exc:
        return (WARN, "could not ask: %s: %s" % (type(exc).__name__, exc))
    try:
        reason = youtube.missing()
    except Exception as exc:
        return (WARN, "the check raised: %s: %s" % (type(exc).__name__, exc))
    if not reason:
        return (OK, "ffmpeg and yt-dlp are both there")
    return (WARN, reason,
            "Tabs and bar maps still download; only the audio does not.",
            "On a Mac: brew install ffmpeg")


def check_songs():
    """Where the tabs are, and whether their settings travelled with them."""
    try:
        from pickhero.config import Config
        folder = Config.load().songs_path()
    except Exception as exc:
        return (FAIL, "could not resolve the songs folder: %s: %s"
                % (type(exc).__name__, exc))
    if not folder.is_dir():
        return (WARN, "%s does not exist" % folder,
                "Copy your songs folder there, or start the app once with",
                "--songs /path/to/your/songs")
    tabs = [p for p in folder.iterdir()
            if p.suffix.lower() in (".gp", ".gp3", ".gp4", ".gp5", ".gpx")]
    cards = list(folder.glob("*.mysician.json"))
    runs = list(folder.glob("*.runs.json"))
    head = ("%s: %d tab(s), %d settings sidecar(s), %d run histor(y/ies)"
            % (folder.resolve(), len(tabs), len(cards), len(runs)))
    if not tabs:
        return (WARN, head, "No tabs. The song list will be empty.")
    return (OK, head)


def check_build():
    """Which version this is, so a report can be compared with the next one.

    Three fixes in a row came back as "does nothing" here and all three were
    an older build. Without this line, "is it fixed" and "did it reach the
    machine" are the same question with no way to tell them apart.
    """
    try:
        from pickhero import build_info
        return (OK, build_info.build_stamp() or "unknown build")
    except Exception as exc:
        return (WARN, "could not read the build stamp: %s: %s"
                % (type(exc).__name__, exc))


CHECKS = (
    ("python", check_python),
    ("packages", check_packages),
    ("aubio", check_aubio_reads_a_sine),
    ("microphone", check_microphone),
    ("capture", check_capture_path),
    ("output", check_output),
    ("display", check_display),
    ("engraver", check_engraver),
    ("clipboard", check_clipboard),
    ("ffmpeg", check_ffmpeg),
    ("songs", check_songs),
    ("build", check_build),
)

MARK = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]"}


def run() -> int:
    print("MySician doctor -- %s" % platform.platform())
    print("")
    worst = OK
    for label, check in CHECKS:
        try:
            result = check()
        except Exception as exc:                      # never raise: the rule
            result = (FAIL, "the check itself raised: %s: %s"
                      % (type(exc).__name__, exc))
        status, head = result[0], result[1]
        print("%s %-11s %s" % (MARK[status], label, head))
        for line in result[2:]:
            print("%s %-11s %s" % (" " * 6, "", line))
        if status == FAIL:
            worst = FAIL
        elif status == WARN and worst == OK:
            worst = WARN
    print("")
    if worst == FAIL:
        print("Something the app needs is missing or broken -- the lines")
        print("marked FAIL say what and how to fix it.")
        return 1
    if worst == WARN:
        print("Everything the app needs works. The warnings are features")
        print("that will not, and each one says which.")
        return 0
    print("Everything works.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
