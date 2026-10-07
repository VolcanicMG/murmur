# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one-FOLDER windowed build (dist/murmur/murmur.exe + _internal/): no per-launch unpack, so it starts fast. The Parakeet model
(~640 MB) is bundled from MURMUR_MODEL_DIR (default packaging/models/parakeet, filled by `python -m murmur fetch-model`) so nothing downloads at launch."""
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

MODEL_DIR = os.environ.get("MURMUR_MODEL_DIR") or os.path.join(SPECPATH, "packaging", "models", "parakeet")
if not os.path.isfile(os.path.join(MODEL_DIR, "config.json")):
    raise SystemExit(f"speech model missing at {MODEL_DIR}: run `python -m murmur fetch-model` first (or set MURMUR_MODEL_DIR)")

a = Analysis(
    ["packaging/entry.py"],
    pathex=[SPECPATH],
    binaries=[],
    datas=collect_data_files("onnx_asr") + copy_metadata("onnx-asr") + [(os.path.join(SPECPATH, "packaging", f), "packaging") for f in ("murmur.ico", "icon.png")] + [(MODEL_DIR, os.path.join("packaging", "models", "parakeet"))],  # preprocessors + dist-info (onnx_asr reads its own version) + the speech model
    hiddenimports=collect_submodules("murmur") + collect_submodules("onnx_asr") + ["pystray._win32", "comtypes.stream", "win32com.client", "PIL.ImageTk", "PIL._tkinter_finder", "huggingface_hub", "tqdm"],  # ImageTk: settings/setup render images through it
    hookspath=[],
    runtime_hooks=[],
    excludes=[],  # tkinter must stay: overlay + settings
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="murmur",
    debug=False,
    strip=False,
    upx=False,  # UPX trips antivirus heuristics
    console=False,
    icon=os.path.join(SPECPATH, "packaging", "murmur.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="murmur")
