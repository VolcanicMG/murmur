# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one-file windowed murmur.exe. The Parakeet model is NOT bundled (downloaded to the HF cache on first run)."""
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

a = Analysis(
    ["packaging/entry.py"],
    pathex=[SPECPATH],
    binaries=[],
    datas=collect_data_files("onnx_asr") + copy_metadata("onnx-asr") + [(os.path.join(SPECPATH, "packaging", f), "packaging") for f in ("murmur.ico", "icon.png")],  # model preprocessors + dist-info (onnx_asr reads its own version)
    hiddenimports=collect_submodules("murmur") + collect_submodules("onnx_asr") + ["pystray._win32", "comtypes.stream"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],  # tkinter must stay: overlay + settings
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="murmur",
    debug=False,
    strip=False,
    upx=False,  # UPX trips antivirus heuristics
    runtime_tmpdir=None,
    console=False,
    icon=os.path.join(SPECPATH, "packaging", "murmur.ico"),
)
