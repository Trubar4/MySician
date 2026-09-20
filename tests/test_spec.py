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
