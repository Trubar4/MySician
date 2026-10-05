"""`pickhero.spec` is Python that only ever runs on the build machine.

Nothing imports it, no test used to read it, and PyInstaller executes it with
`exec` -- so a name used before it is assigned is a NameError that the whole
suite passes straight over and the Windows build then dies on. That is how
the ffmpeg bundling shipped: it appended to `binaries` six lines ABOVE
`binaries = []`, which would have wiped it even had it not raised.

So the property is asserted rather than the instance: every name the spec
reads at module level is bound before the line that reads it.
"""

import ast
import builtins
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "pickhero.spec"

# PyInstaller execs the spec with these already in its namespace.
INJECTED = {
    "Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE", "Tree", "TOC", "Splash",
    "SPEC", "SPECPATH", "DISTPATH", "workpath", "warnfile", "noconfirm",
    "HOMEPATH", "CONF", "__file__", "__name__",
}


def _bound_by(node: ast.AST) -> set[str]:
    """The names this statement binds -- assignments, imports, loop targets."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            names.add(child.id)
        elif isinstance(child, ast.alias):
            names.add((child.asname or child.name).split(".")[0])
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(child.name)
    return names


def _read_by(node: ast.AST) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


class TestEveryNameIsBoundBeforeItIsUsed:
    def test_the_spec_never_reads_a_name_it_has_not_set(self):
        tree = ast.parse(SPEC.read_text(encoding="utf-8"), filename=str(SPEC))
        known = set(dir(builtins)) | INJECTED
        for statement in tree.body:
            # A statement may bind and read the same name (`x += 1`, a loop
            # variable), so its own bindings count as known while it runs.
            for name in _read_by(statement) - _bound_by(statement):
                assert name in known, (
                    f"{SPEC.name} line {statement.lineno}: '{name}' is read "
                    f"before anything assigns it"
                )
            known |= _bound_by(statement)

    def test_it_would_have_caught_the_ffmpeg_bug(self):
        """The check is only worth having if it fails on the broken code."""
        broken = ast.parse("binaries.append(1)\nbinaries = []\n")
        known = set(dir(builtins))
        first = broken.body[0]
        assert "binaries" not in known | _bound_by(first)


class TestEveryLazyImportIsNamed:
    """A module reached only from inside a function is a module the static
    import graph can miss, and the spec already carries a list of them with
    a comment saying why. A hand-kept list is only as fresh as somebody's
    memory -- this reads the tree instead, so the next one added fails here
    rather than on the machine that only has the .exe.
    """

    def _imports(self):
        """(imported at module level, imported only inside functions)."""
        import ast
        top, lazy = set(), set()
        for path in (ROOT / "pickhero").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))

            class Walk(ast.NodeVisitor):
                def __init__(self):
                    self.depth = 0

                def visit_FunctionDef(self, node):
                    self.depth += 1
                    self.generic_visit(node)
                    self.depth -= 1

                visit_AsyncFunctionDef = visit_FunctionDef

                def _add(self, name):
                    (lazy if self.depth else top).add(name)

                def visit_Import(self, node):
                    for alias in node.names:
                        if alias.name.startswith("pickhero"):
                            self._add(alias.name)

                def visit_ImportFrom(self, node):
                    if node.module and node.module.startswith("pickhero"):
                        self._add(node.module)
                        for alias in node.names:
                            self._add(f"{node.module}.{alias.name}")

            Walk().visit(tree)
        return top, lazy

    def _is_module(self, name):
        """A module rather than a name imported FROM one."""
        parts = name.split(".")
        as_file = ROOT.joinpath(*parts).with_suffix(".py")
        as_package = ROOT.joinpath(*parts, "__init__.py")
        return as_file.exists() or as_package.exists()

    def test_nothing_is_reachable_only_at_run_time(self):
        top, lazy = self._imports()
        spec = (ROOT / "pickhero.spec").read_text(encoding="utf-8")
        missing = sorted(name for name in lazy - top
                         if self._is_module(name) and f'"{name}"' not in spec)
        assert not missing, (
            "imported only inside a function and not in pickhero.spec: "
            + ", ".join(missing))


# ── The release workflow ────────────────────────────────────────────────────
# Same class of file as the spec above: YAML that only ever runs on GitHub's
# Windows runner, so nothing in this tree ever read it -- and it was missing
# two steps `build.bat` has had for months. Every EXE it built reported
# `unknown build`, which is the one question the stamp exists to answer, and
# carried no ffmpeg, so downloaded audio silently would not play.
#
# The suite cannot run the workflow, so it asserts the ORDER its steps have
# to be in. No YAML parser: pyyaml is not in requirements.txt, and a test that
# skips itself catches nothing on the machine that matters.

WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _run_steps() -> list[str]:
    """The shell commands the workflow runs, in order.

    Both forms: `run: cmd` on one line, and the `run: |` block, whose body is
    every more-indented line after it.
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("run:"):
            rest = stripped[4:].strip()
            indent = len(line) - len(line.lstrip())
            i += 1
            if rest in ("|", ">", "|-", ">-"):
                while i < len(lines):
                    body = lines[i]
                    if body.strip() and (len(body) - len(body.lstrip())) <= indent:
                        break
                    if body.strip():
                        out.append(body.strip())
                    i += 1
            elif rest:
                out.append(rest)
            continue
        i += 1
    return out


class TestTheWorkflowBuildsWhatBuildBatBuilds:
    def test_the_build_is_stamped_before_pyinstaller_runs(self):
        steps = _run_steps()
        stamp = [i for i, s in enumerate(steps) if "stamp_build.py" in s]
        build = [i for i, s in enumerate(steps) if "pyinstaller" in s and "pip" not in s]
        assert stamp, (
            "the workflow never writes a build stamp, so every EXE it "
            f"produces reports 'unknown build'. Its steps: {steps}")
        assert build, f"the workflow never runs PyInstaller? {steps}"
        assert min(stamp) < min(build), (
            "the stamp is written AFTER the build, so the bundle cannot "
            "carry it")

    def test_the_two_builds_agree_about_ffmpeg(self):
        """Whichever answer is right, it has to be the same in both files.

        An ffmpeg.exe is 37 MB of the bundle -- the week the workflow
        fetched one the EXE went from 43 to 80 MB -- so the two builds
        disagreeing here is a player downloading twice the file he needs,
        or not getting the feature he was promised, depending on which
        build he ran. Neither fetches it today: the audio download is dead
        behind YouTube's bot check, and an ffmpeg.exe dropped beside
        MySician.exe is found with no rebuild. Deliberately fetch one
        before PyInstaller and it is bundled.
        """
        workflow = "fetch_ffmpeg.py" in "\n".join(_run_steps())
        bat = (ROOT / "build.bat").read_text(encoding="utf-8", errors="replace")
        in_bat = "fetch_ffmpeg.py" in "".join(
            line for line in bat.splitlines() if not line.lstrip().startswith("::"))
        assert workflow == in_bat, (
            "one build fetches ffmpeg and the other does not: workflow="
            f"{workflow}, build.bat={in_bat}")

    def test_an_ffmpeg_that_is_there_is_still_bundled(self):
        """Not fetching one is not the same as refusing to carry one."""
        spec = (ROOT / "pickhero.spec").read_text(encoding="utf-8")
        assert 'glob.glob(os.path.join("tools", "ffmpeg*"))' in spec

    def test_build_bat_uses_the_same_stamp_writer(self):
        """One writer of the stamp format, not three.

        `build.bat` wrote it with an inline `python -c` while
        `build_info.write_stamp` sat in the package called by nothing -- the
        tested-and-unused fault this project has written up twice.
        """
        bat = (ROOT / "build.bat").read_text(encoding="utf-8", errors="replace")
        assert "stamp_build.py" in bat
        assert "_build_stamp.txt" not in bat.split("stamp_build.py")[0], (
            "build.bat still writes the stamp itself before calling the script")


# ── What the build PRINTS ───────────────────────────────────────────────────
# `fetch_ffmpeg.py` printed one arrow, on the single line that reports a
# SUCCESSFUL download. An arrow is in neither cp1252 (the Windows runner's
# console) nor cp850 (a German one), so that print raised
# UnicodeEncodeError and the script exited 1 with the 88 MB file already
# written -- which is how a green workflow came to carry a red "Process
# completed with exit code 1" annotation beside an EXE that had grown by
# 37 MB. The same runner printed an em dash from `check_verovio.py` two
# steps later without complaining, which is what says it was the arrow and
# not the encoding in general.
#
# So the rule is about the tools the BUILD runs, and they are read off the
# workflow and build.bat rather than listed here: a step added later that
# runs a new tool comes under the rule by being a build step.


def _build_tools() -> list[Path]:
    """The tools/*.py the workflow or build.bat invokes."""
    commands = list(_run_steps())
    bat = (ROOT / "build.bat").read_text(encoding="utf-8", errors="replace")
    commands += [line for line in bat.splitlines()
                 if not line.lstrip().startswith("::")]
    found = set()
    for path in (ROOT / "tools").glob("*.py"):
        if any(path.name in command for command in commands):
            found.add(path)
    return sorted(found)


def _non_ascii_prints(path: Path) -> list[tuple[int, str]]:
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
            for part in ast.walk(node):
                if (isinstance(part, ast.Constant)
                        and isinstance(part.value, str)
                        and not part.value.isascii()):
                    out.append((node.lineno, part.value))
    return out


class TestTheToolsTheBuildRunsPrintAscii:
    def test_the_build_really_does_run_some_tools(self):
        """Without this the rule below passes by finding nothing."""
        names = {path.name for path in _build_tools()}
        assert "stamp_build.py" in names, names
        assert "check_verovio.py" in names, names

    def test_nothing_the_build_prints_can_raise_on_a_windows_console(self):
        offenders = {
            path.name: _non_ascii_prints(path) for path in _build_tools()
        }
        bad = {name: found for name, found in offenders.items() if found}
        assert not bad, (
            "a build step prints a character a Windows console cannot "
            f"encode, which exits 1 after the work is done: {bad}")

    def test_the_arrow_really_was_unencodable(self):
        """The check is only worth having if it catches the shipped bug."""
        for encoding in ("cp1252", "cp850", "cp437"):
            with pytest.raises(UnicodeEncodeError):
                "ffmpeg: 88 MB \u2192 tools/ffmpeg.exe".encode(encoding)
        # ...while the em dash the same runner printed two steps later is
        # fine on cp1252, which is why only one of the two steps failed.
        assert "verovio OK \u2014".encode("cp1252")
