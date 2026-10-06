#!/usr/bin/env bash
# Build murmur.exe from WSL. PyInstaller must run as a Windows process (Windows venv python via interop);
# the build is staged on the Windows filesystem because the \\wsl$ share is slow and flaky for it.
# Override paths via env, e.g. INSTALL_DIR=... ./packaging/build.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIN_PY="${WIN_PY:-/mnt/c/Users/ethan/.murmur/venv/Scripts/python.exe}"
BUILD_DIR="${BUILD_DIR:-/mnt/c/Users/ethan/AppData/Local/murmur-build}"
INSTALL_DIR="${INSTALL_DIR:-/mnt/c/Users/ethan/AppData/Local/Programs/murmur}"  # outside OneDrive: it caches stale icons and would sync 60 MB per build

echo "[1/3] Staging source + spec to $BUILD_DIR ..."
rm -rf "${BUILD_DIR:?}"
mkdir -p "$BUILD_DIR/packaging"
cp -r "$REPO/murmur" "$BUILD_DIR/"
cp "$REPO/murmur.spec" "$BUILD_DIR/"
cp "$REPO/packaging/entry.py" "$BUILD_DIR/packaging/"
cp "$REPO/packaging/murmur.ico" "$REPO/packaging/icon.png" "$BUILD_DIR/packaging/"
find "$BUILD_DIR/murmur" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "[2/3] Running PyInstaller (Windows Python) ..."
start=$(date +%s)
( cd "$BUILD_DIR" && "$WIN_PY" -m PyInstaller --noconfirm --clean --distpath dist --workpath build murmur.spec )
echo "build time: $(( $(date +%s) - start ))s"

echo "[3/3] Delivering exe ..."
OUT="$BUILD_DIR/dist/murmur.exe"
[[ -f "$OUT" ]] || { echo "ERROR: build produced no exe at $OUT"; exit 1; }
mkdir -p "$INSTALL_DIR"
cp "$OUT" "$INSTALL_DIR/murmur.exe"
echo "DONE. Size: $(du -h "$OUT" | cut -f1)  ->  $INSTALL_DIR/murmur.exe"
