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
