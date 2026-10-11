#!/usr/bin/env bash
# Build the murmur onedir app from WSL and install it. PyInstaller must run as a Windows process (Windows venv python via interop);
# the build is staged on the Windows filesystem because the \\wsl$ share is slow and flaky for it.
# Result: %LOCALAPPDATA%\Programs\murmur\murmur.exe (+ _internal\) and a Desktop shortcut murmur.lnk.
# Override paths via env, e.g. INSTALL_DIR=... ./packaging/build.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIN_PY="${WIN_PY:-/mnt/c/Users/ethan/.murmur/venv/Scripts/python.exe}"
BUILD_DIR="${BUILD_DIR:-/mnt/c/Users/ethan/AppData/Local/murmur-build}"
INSTALL_DIR="${INSTALL_DIR:-/mnt/c/Users/ethan/AppData/Local/Programs/murmur}"  # outside OneDrive: it caches stale icons and would sync the folder per build
VAD_DIR="${VAD_DIR:-/mnt/c/Users/ethan/AppData/Local/murmur-build-model/silero}"  # Silero VAD (2 MB), same cache
MODEL_DIR="${MODEL_DIR:-/mnt/c/Users/ethan/AppData/Local/murmur-build-model/parakeet-v2}"  # speech model (stt.DEFAULT_MODEL), fetched once and kept across builds; the folder name must change with the model

# Safety: the clean-replace below deletes everything inside these folders, so only ever do it to folders that look like ours.
[[ "$(basename "$BUILD_DIR")" == "murmur-build" ]] || { echo "ERROR: BUILD_DIR must end in /murmur-build (got $BUILD_DIR)" >&2; exit 1; }
[[ "$(basename "$INSTALL_DIR")" == "murmur" ]] || { echo "ERROR: INSTALL_DIR must end in /murmur (got $INSTALL_DIR)" >&2; exit 1; }
if [[ -d "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR")" && ! -f "$INSTALL_DIR/murmur.exe" ]]; then
  echo "ERROR: $INSTALL_DIR is not empty and has no murmur.exe: refusing to wipe it" >&2; exit 1
fi

# Refuse to touch a running install: the exe and its _internal\ DLLs are locked while murmur runs.
if tasklist.exe 2>/dev/null | tr -d '\r' | grep -qi '^murmur\.exe'; then
  echo "ERROR: murmur.exe is running. Quit it from the tray (Quit murmur) and run this again." >&2
  exit 1
fi

echo "[1/4] Staging source + spec to $BUILD_DIR ..."
rm -rf "${BUILD_DIR:?}"
mkdir -p "$BUILD_DIR/packaging"
cp -r "$REPO/murmur" "$BUILD_DIR/"
cp "$REPO/murmur.spec" "$BUILD_DIR/"
cp "$REPO/packaging/entry.py" "$BUILD_DIR/packaging/"
cp "$REPO/packaging/murmur.ico" "$REPO/packaging/icon.png" "$BUILD_DIR/packaging/"
find "$BUILD_DIR/murmur" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "[2/4] Speech model -> $MODEL_DIR (download only if missing), then PyInstaller (Windows Python) ..."
[[ -f "$MODEL_DIR/config.json" ]] || ( cd "$BUILD_DIR" && "$WIN_PY" -m murmur fetch-model "$(wslpath -w "$MODEL_DIR")" )
[[ -f "$VAD_DIR/silero_vad.onnx" ]] || ( cd "$BUILD_DIR" && "$WIN_PY" -m murmur fetch-vad "$(wslpath -w "$VAD_DIR")" )
start=$(date +%s)
( cd "$BUILD_DIR" && MURMUR_MODEL_DIR="$MODEL_DIR" MURMUR_VAD_DIR="$VAD_DIR" WSLENV="${WSLENV:+$WSLENV:}MURMUR_MODEL_DIR/p:MURMUR_VAD_DIR/p" "$WIN_PY" -m PyInstaller --noconfirm --clean --distpath dist --workpath build murmur.spec )  # WSLENV /p hands the path to Windows
echo "build time: $(( $(date +%s) - start ))s"

OUT="$BUILD_DIR/dist/murmur"
[[ -f "$OUT/murmur.exe" && -d "$OUT/_internal" ]] || { echo "ERROR: build produced no onedir app at $OUT"; exit 1; }

cp "$REPO/THIRD_PARTY_NOTICES.md" "$OUT/"  # next to murmur.exe

echo "[3/4] Installing to $INSTALL_DIR (clean replace; user data lives in ~/.murmur, never here) ..."
mkdir -p "$INSTALL_DIR"
find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
cp -r "$OUT"/. "$INSTALL_DIR"/

echo "[4/4] Desktop shortcut ..."
WIN_EXE="$(wslpath -w "$INSTALL_DIR/murmur.exe")"
powershell.exe -NoProfile -Command "
  \$exe = '$WIN_EXE'
  \$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'murmur.lnk'
  \$s = (New-Object -ComObject WScript.Shell).CreateShortcut(\$lnk)
  \$s.TargetPath = \$exe; \$s.WorkingDirectory = Split-Path \$exe; \$s.IconLocation = \$exe + ',0'; \$s.Description = 'murmur'
  \$s.Save(); Write-Output \"shortcut: \$lnk\"" | tr -d '\r'
"$INSTALL_DIR/murmur.exe" --shell-menu on && echo "Explorer menu: registered (HKCU)"  # so the right-click entry exists before the first launch
echo "DONE. Size: $(du -sh "$INSTALL_DIR" | cut -f1)  ->  $INSTALL_DIR/murmur.exe"
