"""Cmd is how a Mac holds down the modifier the shortcuts are written with.

*"2. du meinst a+c oder? geht beides nicht aktuell"* -- Ctrl+C and Ctrl+V
on the MacBook. The doctor had already reported `clipboard ok -- pbcopy and
pbpaste, and a copy reads back`, so the clipboard works through the app's
own seam and the failure is the KEY.

**And `CLAUDE.md` had argued against this mapping on reasoning rather than
on a measurement** -- *"no Cmd-for-Ctrl mapping: the physical Ctrl key
exists"* -- which the first report from that machine overturns.

It is a repair rather than a new binding, which is the half worth asserting:
today a Cmd press falls THROUGH to the unshifted twin, so Cmd+C raises the
noise gate, Cmd+Z toggles vsync and Cmd+S opens the sync panel. That is the
`Shift+C`-falls-through-to-the-gate fault with a different modifier.

`keys.py`'s own docstring says a helper only closes a class of fault where
it can be reached -- and `ctrl_held` was reached from ONE of the fourteen
places that read Ctrl. `TestNobodyReadsCtrlByHand` is what makes the Cmd
answer arrive at all fourteen.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pygame
import pytest

from pickhero.ui import keys

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "pickhero" / "ui"


def _press(key, mod):
    return pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod, unicode="")


def _keys_on(platform, monkeypatch):
    """`keys` as it imports on that platform -- the mask is set at import."""
    monkeypatch.setattr(sys, "platform", platform)
    return importlib.reload(keys)


@pytest.fixture(autouse=True)
def _restore():
    yield
    importlib.reload(keys)          # leave the module as this machine has it


class TestOnAMacCmdCounts:

    def test_cmd_alone_is_the_modifier(self, monkeypatch):
        mac = _keys_on("darwin", monkeypatch)
        assert mac.ctrl_held(_press(pygame.K_c, pygame.KMOD_META)) is True

    def test_and_so_is_the_physical_ctrl(self, monkeypatch):
        mac = _keys_on("darwin", monkeypatch)
        assert mac.ctrl_held(_press(pygame.K_c, pygame.KMOD_CTRL)) is True

    def test_a_bare_letter_is_still_a_bare_letter(self, monkeypatch):
        """Or every plain key on the screen would read as a shortcut."""
        mac = _keys_on("darwin", monkeypatch)
        assert mac.ctrl_held(_press(pygame.K_c, 0)) is False


class TestEverywhereElseIsUntouched:
    """The Meta key is the Windows/Super key there and means something else."""

    @pytest.mark.parametrize("platform", ["win32", "linux"])
    def test_meta_is_not_ctrl(self, platform, monkeypatch):
        other = _keys_on(platform, monkeypatch)
        assert other.ctrl_held(_press(pygame.K_c, pygame.KMOD_META)) is False

    @pytest.mark.parametrize("platform", ["win32", "linux"])
    def test_and_ctrl_still_is(self, platform, monkeypatch):
        other = _keys_on(platform, monkeypatch)
        assert other.ctrl_held(_press(pygame.K_c, pygame.KMOD_CTRL)) is True


class TestNobodyReadsCtrlByHand:
    """The property, not the fourteen instances.

    Asserted over the SOURCE rather than by calling each handler, because
    what has to hold is that a site added tomorrow comes under the helper
    too. A hand-written `event.mod & KMOD_CTRL` works on Windows and is
    silently dead on a Mac, which is the whole fault.
    """

    @staticmethod
    def _hand_written(path: Path) -> list[int]:
        """Lines reading the Ctrl bit straight off an event's modifiers."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.BinOp)
                    and isinstance(node.op, ast.BitAnd)):
                continue
            left, right = ast.unparse(node.left), ast.unparse(node.right)
            if "event.mod" in left and "KMOD_CTRL" in right:
                found.append(node.lineno)
        return found

    def test_no_screen_asks_the_bit_itself(self):
        guilty = {}
        for path in sorted(UI.glob("*.py")):
            lines = self._hand_written(path)
            if lines:
                guilty[path.name] = lines
        assert guilty == {}, (
            "these read the Ctrl bit off the event instead of asking "
            "ctrl_held(), so Cmd will not reach them on a Mac: %s" % guilty)

    def test_the_check_can_fail(self, tmp_path):
        """Or it passes on anything, which is what it exists to catch."""
        sample = tmp_path / "x.py"
        sample.write_text(
            "import pygame\n"
            "def f(event):\n"
            "    return event.mod & pygame.KMOD_CTRL\n", encoding="utf-8")
        assert self._hand_written(sample) == [3]


class TestTheLiveModifiersToo:
    """One caller reads `get_mods()` rather than an event, and needs the mask."""

    def test_the_mask_is_public_and_carries_cmd_on_a_mac(self, monkeypatch):
        mac = _keys_on("darwin", monkeypatch)
        assert mac.CTRL_MODS & pygame.KMOD_META
        assert mac.CTRL_MODS & pygame.KMOD_CTRL

    def test_and_not_elsewhere(self, monkeypatch):
        other = _keys_on("win32", monkeypatch)
        assert not other.CTRL_MODS & pygame.KMOD_META

    def test_the_backing_ladder_reads_it(self):
        """`_nudge_backing` is handed live mods, so it cannot use ctrl_held."""
        source = (UI / "scrolling.py").read_text(encoding="utf-8")
        start = source.index("def _nudge_backing")
        body = source[start:start + 1200]
        assert "mods & CTRL_MODS" in body
        assert "mods & pygame.KMOD_CTRL" not in body
