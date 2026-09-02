# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for PickHero.

Build with:  pyinstaller pickhero.spec --noconfirm
Output:      dist/MySician.exe  (single-file, windowed)
"""

import glob
import os
import sys

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
)

block_cipher = None

# ── Data files ──────────────────────────────────────────────────────────────
# sounddevice bundles the PortAudio shared library as package data.
# certifi provides the CA certificate bundle needed for HTTPS downloads
# (Songsterr search/download via urllib).
datas = []
datas += collect_data_files("sounddevice")
datas += collect_data_files("certifi")
# verovio ships ~20 MB of engraving data -- the music fonts and the schemas.
# Without them the toolkit imports and then renders nothing, which is the
# failure mode this project has learnt to check for rather than assume: a
# feature that only works in the development tree is a feature the player
# does not have.
datas += collect_data_files("verovio")
# resvg rasterises the engraving. SDL's own SVG loader accepts verovio's
# output and draws 20 pixels of it, which is why this is here at all -- and
# it is resvg rather than cairosvg because cairosvg needs a cairo the Windows
# build has not got. It is one self-contained extension with no data files.

# ── Native binaries / C extensions ──────────────────────────────────────────
# aubio  — C pitch/onset detection library
# pygame — SDL2 + mixer + image + font shared libraries
# numpy  — C core (multiarray, umath, etc.) — sometimes missed by analysis
binaries = []
binaries += collect_dynamic_libs("aubio")
binaries += collect_dynamic_libs("pygame")
binaries += collect_dynamic_libs("numpy")
# verovio is a 17 MB abi3 extension beside a thin Python wrapper, and every
# import of it in this app is inside a function -- which is how it came to be
# missing from the EXE while the build and its own check both passed. Both
# halves are named explicitly rather than left to the import graph.
binaries += collect_dynamic_libs("verovio")
binaries += collect_dynamic_libs("resvg_py")

# ── VC++ Runtime ────────────────────────────────────────────────────────────
# Bundle the Visual C++ runtime so the exe works on machines without it.
# Python ships these DLLs in its install directory.
_python_dir = os.path.dirname(sys.executable)
for _pattern in ("vcruntime*.dll", "msvcp*.dll"):
    for _dll in glob.glob(os.path.join(_python_dir, _pattern)):
        binaries.append((_dll, "."))

# ── Analysis ────────────────────────────────────────────────────────────────
a = Analysis(
    ["pickhero/main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        # Audio capture & detection
        "aubio",
        "numpy",
        "sounddevice",
        # UI
        "pygame",
        "pygame.midi",
        # Tab loading — the pip package is "pyguitarpro", but the import is "guitarpro"
        "guitarpro",
        # SSL certs for urllib HTTPS requests (Songsterr downloader)
        "certifi",
        # Engraving. Imported lazily everywhere it is used, so nothing in the
        # static import graph reaches it.
        "verovio",
        "resvg_py",
        "pickhero.ui.tab_view",
        "pickhero.tabs.musicxml",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MySician",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
