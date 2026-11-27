"""Tests for tools/doctor.py.

The doctor exists because a new machine fails every feature at once and
silently, so the one property it has to have is that it never stops: a
check that raises must become a LINE, not a traceback that hides the other
eleven. Every test here either drives the real `run()` or the one rule in
the file that has a number in it.

The ASCII rule these are not: `tests/test_spec.py` already walks every tool
whose docstring says `Usage:` and fails on a character cp850 cannot encode.
`test_the_spec_suite_covers_it` is what makes sure this file is in that set
rather than quietly outside it.
"""

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "doctor.py"


def _load():
    """The tool as a module, by path -- `tools/` is not a package."""
    spec = importlib.util.spec_from_file_location("doctor_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def doctor():
    return _load()


class TestTheOneRuleWithANumberInIt:
    """What a measured peak means, without an audio device to measure it.

    Exact zeros against a quiet room is the whole reason this tool exists:
    on a Mac a refused microphone permission is a dialog nobody saw, and
    the app then looks like a detector that has stopped working.
    """

    def test_exact_silence_is_a_failure_and_names_the_permission(self, doctor):
        status, *lines = doctor.mic_verdict(0.0)
        assert status == doctor.FAIL
        said = " ".join(lines)
        assert "exactly zero" in said
        assert "Microphone" in said

    def test_a_quiet_room_is_not_silence(self, doctor):
        """The control. Without it the rule above passes on any peak."""
        status, *lines = doctor.mic_verdict(10 ** (-60.0 / 20.0))
        assert status == doctor.WARN
        assert "exactly zero" not in " ".join(lines)

    def test_a_healthy_peak_says_nothing(self, doctor):
        assert doctor.mic_verdict(10 ** (-20.0 / 20.0)) == (doctor.OK,)

    def test_the_knee_is_the_one_measured_elsewhere(self, doctor):
        """-38 dB is where the pitch starts rotting, not a number chosen here."""
        assert doctor.QUIET_PEAK_DB == -38.0
        above = doctor.mic_verdict(10 ** ((doctor.QUIET_PEAK_DB + 1) / 20.0))
        below = doctor.mic_verdict(10 ** ((doctor.QUIET_PEAK_DB - 1) / 20.0))
        assert above[0] == doctor.OK
        assert below[0] == doctor.WARN

    def test_silence_reads_as_minus_infinity(self, doctor):
        assert doctor._db(0.0) == "-inf dB"
        assert "-20.0" in doctor._db(0.1)


class TestWhatItSaysWhenAubioWillNotBuild:
    """The one dependency with no macOS wheel, and the one surprising error.

    The first version of this tool said "needs a compiler", which is true and
    was not the fault: the compiler was installed and aubio still failed, on a
    const mismatch with numpy that gcc warns about and clang 16+ refuses. A
    build measured on Linux says nothing about it, which is how that advice
    came to be half an answer.
    """

    def test_it_names_the_compiler_and_the_flag(self, doctor):
        said = " ".join(doctor.AUBIO_FIX)
        assert "xcode-select --install" in said
        assert "CFLAGS" in said
        assert "incompatible-function-pointer-types" in said

    def test_the_advice_is_only_given_for_aubio(self, doctor, monkeypatch):
        """A missing pygame must not be answered with aubio's compiler flag."""
        monkeypatch.setattr(doctor, "PACKAGES",
                            (("no_such_module_at_all", "nothing", True),))
        status, _head, *lines = doctor.check_packages()
        assert status == doctor.FAIL
        assert "CFLAGS" not in " ".join(lines)

    def test_and_it_is_given_when_aubio_is_the_missing_one(self, doctor,
                                                           monkeypatch):
        monkeypatch.setattr(doctor, "PACKAGES",
                            (("aubio_not_installed_here", "aubio", True),))
        status, _head, *lines = doctor.check_packages()
        assert status == doctor.FAIL
        assert "CFLAGS" in " ".join(lines)


class TestItDoesNotOpenTkOnAMac(object):
    """The report was killed by the thing it was reporting on.

    Eight checks passed and then the clipboard check opened a Tk window,
    which aborts the process on macOS once SDL owns `NSApp`. A diagnostic
    that cannot survive the fault it exists to find is worth nothing, so on
    darwin it asks the pasteboard tools instead -- and the tkinter put into
    `sys.modules` here fails on being touched at all, the way the real one
    does.
    """

    @staticmethod
    def _exploding():
        import types

        class Boom(types.ModuleType):
            def __getattr__(self, name):
                raise AssertionError("tkinter was touched: %s" % name)

        return Boom("tkinter")

    def test_the_clipboard_check_never_touches_tkinter(self, doctor,
                                                       monkeypatch):
        import subprocess
        import types
        monkeypatch.setattr(doctor.sys, "platform", "darwin")
        monkeypatch.setitem(sys.modules, "tkinter", self._exploding())
        board = {"text": ""}

        def run(argv, **kw):
            if argv == ["pbcopy"]:
                board["text"] = kw["input"].decode("utf-8")
                return types.SimpleNamespace(returncode=0, stdout=b"",
                                             stderr=b"")
            return types.SimpleNamespace(
                returncode=0, stdout=board["text"].encode("utf-8"),
                stderr=b"")

        monkeypatch.setattr(subprocess, "run", run)
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/" + name)
        status, *lines = doctor.check_clipboard()
        assert status == doctor.OK
        assert "pbcopy" in " ".join(lines)

    def test_a_missing_pasteboard_tool_is_named(self, doctor, monkeypatch):
        monkeypatch.setattr(doctor.sys, "platform", "darwin")
        monkeypatch.setitem(sys.modules, "tkinter", self._exploding())
        monkeypatch.setattr(doctor.shutil, "which",
                            lambda name: None if name == "pbpaste" else "/bin")
        status, *lines = doctor.check_clipboard()
        assert status == doctor.WARN
        assert "pbpaste" in lines[0]


class TestNothingMayRaise:
    """The property the whole tool rests on.

    A doctor that dies on the first missing dependency cannot report the
    other eleven, which on a machine where NOTHING is installed yet is the
    only report there is.
    """

    def test_a_check_that_raises_becomes_a_line(self, doctor, capsys):
        def boom():
            raise RuntimeError("no such device")

        doctor.CHECKS = tuple((label, boom) for label, _ in doctor.CHECKS)
        code = doctor.run()
        out = capsys.readouterr().out
        assert code == 1
        assert out.count("[FAIL]") == len(doctor.CHECKS)
        assert "no such device" in out

    def test_every_check_is_still_named_when_they_all_blow_up(self, doctor,
                                                              capsys):
        labels = [label for label, _ in doctor.CHECKS]

        def boom():
            raise OSError("gone")

        doctor.CHECKS = tuple((label, boom) for label in labels)
        doctor.run()
        out = capsys.readouterr().out
        for label in labels:
            assert label in out, label

    def test_a_warning_is_not_a_failure(self, doctor, capsys):
        doctor.CHECKS = (("thing", lambda: (doctor.WARN, "no ffmpeg")),)
        assert doctor.run() == 0
        assert "[warn]" in capsys.readouterr().out

    def test_all_well_exits_zero(self, doctor, capsys):
        doctor.CHECKS = (("thing", lambda: (doctor.OK, "fine")),)
        assert doctor.run() == 0
        assert "Everything works." in capsys.readouterr().out

    def test_one_failure_outranks_any_number_of_warnings(self, doctor,
                                                         capsys):
        doctor.CHECKS = (("a", lambda: (doctor.WARN, "x")),
                         ("b", lambda: (doctor.FAIL, "y")),
                         ("c", lambda: (doctor.WARN, "z")))
        assert doctor.run() == 1


class TestItReallyRunsOnThisMachine:
    """Driven end to end, because a tool nothing calls is a tool nobody tests.

    This box has no audio device, no PortAudio and no tkinter, so several
    checks are expected to come back FAIL or WARN -- which is the point: the
    run has to finish and say so rather than stop at the first one.
    """

    def test_the_whole_thing_finishes_and_names_every_check(self, doctor,
                                                            capsys):
        code = doctor.run()
        out = capsys.readouterr().out
        assert code in (0, 1)
        for label, _ in doctor.CHECKS:
            assert label in out, label

    def test_every_check_answers_a_known_status(self, doctor):
        for label, check in doctor.CHECKS:
            result = check()
            assert isinstance(result, tuple) and len(result) >= 2, label
            assert result[0] in (doctor.OK, doctor.WARN, doctor.FAIL), label
            assert all(isinstance(line, str) for line in result[1:]), label

    def test_aubio_is_asked_with_a_sine_and_not_with_a_tick(self, doctor):
        """The check that catches an aubio built against another numpy."""
        status, head = doctor.check_aubio_reads_a_sine()[:2]
        if status == doctor.OK:
            assert "MIDI 69" in head
        else:
            assert "aubio" in head or "detector" in head

    def test_the_build_stamp_is_reported(self, doctor):
        status, head = doctor.check_build()[:2]
        assert status == doctor.OK
        assert head


class TestTheRulesTheSuiteAlreadyHas:
    def test_it_documents_how_to_run_it(self):
        """`Usage:` is what puts it under the ASCII rule in test_spec.py."""
        doc = ast.get_docstring(ast.parse(TOOL.read_text(encoding="utf-8")))
        assert "Usage:" in (doc or "")

    def test_the_spec_suite_covers_it(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_spec import _player_tools
        assert "doctor.py" in {path.name for path in _player_tools()}

    def test_it_imports_nothing_heavy_at_module_level(self):
        """A missing numpy has to be a line, so the imports live in the checks."""
        tree = ast.parse(TOOL.read_text(encoding="utf-8"))
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top.add(node.module.split(".")[0])
        assert not top & {"numpy", "aubio", "sounddevice", "pygame",
                          "guitarpro", "verovio", "resvg_py", "pickhero",
                          "tkinter"}, top


class TestTheReportSurvivesTheConsoleItIsReadOn:
    """cp850 cannot hold an em dash, and the report prints data.

    Every literal in the tool is cp850-safe and `test_spec.py` enforces
    it -- but a device name, a song title and the app's own `missing()`
    sentence are not literals in this file, and the German Windows console
    is the one this tool exists for. Twelve computed answers replaced by a
    traceback because of one character in somebody else's string is the
    fault `coach.py` already shipped once.
    """

    def test_an_unencodable_character_does_not_kill_it(self, doctor,
                                                       monkeypatch, capsys):
        import io
        narrow = io.TextIOWrapper(io.BytesIO(), encoding="cp850",
                                  errors="strict")
        monkeypatch.setattr(doctor.sys, "stdout", narrow)
        monkeypatch.setattr(doctor, "CHECKS",
                            (("ffmpeg", lambda: (doctor.WARN,
                                                 "ffmpeg — run: brew")),))
        assert doctor.run() == 0           # a warning is not a failure
        narrow.flush()

    def test_and_a_stream_that_cannot_be_reconfigured_is_not_an_error(
            self, doctor, monkeypatch):
        class Deaf:
            def reconfigure(self, **kw):
                raise OSError("not a real stream")

        monkeypatch.setattr(doctor.sys, "stdout", Deaf())
        doctor._forgive_the_console()      # must simply return


class TestTheOutputThatCanBeEntirelyAbsent:
    """MIDI was the one output the machine check never asked about.

    Windows ships "Microsoft GS Wavetable Synth" as a destination, so `B`
    has made a sound there since the first day. A clean Mac lists nothing at
    all, and the app says so to stdout and as a dash in the HUD -- which
    reads as a key that does nothing. *"Midi output B und Sh+B gehen nicht"*.
    """

    def test_no_port_is_no_longer_a_warning(self, doctor, monkeypatch):
        """Inverted deliberately: it used to warn, and warning was right.

        Before `audio/softsynth.py` there really was nothing at the other end
        of the wire, so no port meant silence. Now the question is WHICH
        synth, not whether one exists -- so this says which, and a rule
        change shows up as a failing suite rather than as a surprise later.
        """
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        monkeypatch.setattr(doctor, "_built_in_synth_ready", lambda: True)
        status, *lines = self._with_ports(doctor, monkeypatch, [])
        assert status == doctor.OK
        assert "built-in synth" in " ".join(lines)

    def test_but_no_synth_at_all_still_is_one(self, doctor, monkeypatch):
        """No port AND no mixer is real silence, which is a different report."""
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        monkeypatch.setattr(doctor, "_built_in_synth_ready", lambda: False)
        status, *lines = self._with_ports(doctor, monkeypatch, [])
        assert status == doctor.WARN
        assert "silent" in " ".join(lines)

    def test_and_on_a_mac_it_names_the_better_sound(self, doctor, monkeypatch):
        monkeypatch.setattr(doctor.sys, "platform", "darwin")
        monkeypatch.setattr(doctor, "_built_in_synth_ready", lambda: True)
        status, *lines = self._with_ports(doctor, monkeypatch, [])
        said = " ".join(lines)
        assert "fluid" in said                    # the upgrade, not the fault
        assert "IAC" in said                      # and the thing that is not

    def test_the_mac_advice_names_no_placeholder_in_brackets(self, doctor):
        """A `<soundfont>.sf2` is what zsh answers with "no such file".

        The player ran the line verbatim, as anybody would, and that is the
        tool's fault rather than his.
        """
        said = " ".join(doctor.MIDI_BETTER_FIX)
        assert "<" not in said and ">" not in said

    def test_a_port_is_named_with_the_one_the_app_would_use(self, doctor,
                                                            monkeypatch):
        status, *lines = self._with_ports(
            doctor, monkeypatch, [(0, "Some Port"), (1, "FluidSynth virtual")])
        assert status == doctor.OK
        said = " ".join(lines)
        assert "FluidSynth virtual" in said
        assert "2 port" in said

    def test_it_never_opens_the_device(self, doctor, monkeypatch):
        """Opening takes the port and prints; the app does that on the key."""
        from pickhero.audio import midi_playback
        opened = []
        monkeypatch.setattr(midi_playback, "_open_shared_output",
                            lambda *a: opened.append(1))
        self._with_ports(doctor, monkeypatch, [(0, "Port")])
        assert opened == []

    @staticmethod
    def _with_ports(doctor, monkeypatch, ports):
        from pickhero.audio import midi_playback
        monkeypatch.setattr(midi_playback, "list_midi_outputs",
                            lambda: list(ports))
        return doctor.check_midi()


class TestTheOneNetworkThisAppUses:
    """Thirteen checks and none of them asked whether the network works.

    *"Die Songsterr Suche bringt auf Mac keine Ergebnisse."* The download
    makes four network calls and the search reported every possible failure
    as "No results found" -- so the machine check could not tell "Songsterr
    has no such song" from "this Python can verify nothing".
    """

    def test_an_empty_trust_store_is_asked_first_and_fails(self, doctor,
                                                           monkeypatch):
        """It outranks reachability: a Python that can verify nothing cannot
        reach ANY https host, so a timeout would be the symptom and the
        certificate the cause. And it is the one of the two with a fix.
        """
        from pickhero.tabs import downloader
        monkeypatch.setattr(downloader, "_no_trust_store", lambda: True)
        monkeypatch.setattr(downloader.sys, "platform", "darwin")
        reached = []
        monkeypatch.setattr(downloader, "search",
                            lambda *a, **k: reached.append(1) or [])
        status, *lines = doctor.check_songsterr()
        assert status == doctor.FAIL
        assert reached == []                   # never even tried
        assert any("Install Certificates" in line for line in lines)

    def test_a_search_that_works_is_ok_and_says_how_many(self, doctor,
                                                         monkeypatch):
        from pickhero.tabs import downloader
        monkeypatch.setattr(downloader, "_no_trust_store", lambda: False)
        monkeypatch.setattr(downloader, "search",
                            lambda *a, **k: [object(), object()])
        status, *lines = doctor.check_songsterr()
        assert status == doctor.OK
        assert "2 result" in " ".join(lines)

    def test_an_unreachable_host_names_the_reason_and_what_still_works(
            self, doctor, monkeypatch):
        """A player whose songs are already on disk is not stuck: the bar
        maps are cached beside the tabs, so only DOWNLOADS are affected."""
        from pickhero.tabs import downloader
        monkeypatch.setattr(downloader, "_no_trust_store", lambda: False)
        monkeypatch.setattr(downloader, "search", lambda *a, **k: [])
        monkeypatch.setattr(downloader, "last_error", "URLError: refused")
        status, *lines = doctor.check_songsterr()
        said = " ".join(lines)
        assert status == doctor.WARN
        assert "refused" in said
        assert "already on disk" in said

    def test_and_an_empty_answer_is_not_called_a_fault(self, doctor,
                                                       monkeypatch):
        """Songsterr answering with nothing is Songsterr, not this machine."""
        from pickhero.tabs import downloader
        monkeypatch.setattr(downloader, "_no_trust_store", lambda: False)
        monkeypatch.setattr(downloader, "search", lambda *a, **k: [])
        monkeypatch.setattr(downloader, "last_error", "")
        status, *lines = doctor.check_songsterr()
        assert "Songsterr answering" in " ".join(lines)

    def test_a_search_that_raises_is_a_line_and_not_a_traceback(self, doctor,
                                                                monkeypatch):
        from pickhero.tabs import downloader
        monkeypatch.setattr(downloader, "_no_trust_store", lambda: False)

        def boom(*a, **k):
            raise RuntimeError("nope")

        monkeypatch.setattr(downloader, "search", boom)
        status, *lines = doctor.check_songsterr()
        assert status == doctor.WARN
        assert "nope" in " ".join(lines)
