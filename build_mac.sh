#!/usr/bin/env bash
# build_mac.sh
# Usage: run on a macOS machine from the project root.
# This script creates a venv, installs deps, installs Playwright browsers, runs PyInstaller (onedir),
# and copies Playwright browser cache into the app bundle directory.

set -euo pipefail

PROJECT_ROOT="$(pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"
APP_NAME="smtp_unlock"
DIST_DIR="$PROJECT_ROOT/dist/$APP_NAME"

python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Install Playwright browsers
python -m playwright install chromium

# Install pyinstaller (if not in requirements)
python -m pip install pyinstaller

# Build with pyinstaller onedir (CLI)
pyinstaller --noconfirm --clean --onedir --name "$APP_NAME" run.py

# Also build GUI entry point
pyinstaller --noconfirm --clean --onedir --name "${APP_NAME}_gui" run_gui.py

# Merge GUI bundle into main (they share same runtime)
cp -r "dist/${APP_NAME}_gui/"* "$DIST_DIR/" 2>/dev/null || true
rm -rf "dist/${APP_NAME}_gui"

# Copy Playwright cache (default cache location on macOS)
PW_CACHE="$HOME/Library/Caches/ms-playwright"
if [ -d "$PW_CACHE" ] && [ -d "$DIST_DIR" ]; then
  echo "Copying Playwright browser runtime from $PW_CACHE to $DIST_DIR/ms-playwright"
  rsync -a --delete "$PW_CACHE/" "$DIST_DIR/ms-playwright/"
  echo "Copy complete."
else
  echo "Playwright cache or dist folder missing; skip copying runtime."
fi

echo "Build complete. Check $DIST_DIR"

# Notes: For a proper macOS .app bundle and Gatekeeper-friendly distribution,
# you should codesign and notarize the app. See BUILD_MAC.md for steps.
