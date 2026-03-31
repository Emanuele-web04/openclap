#!/bin/zsh
set -euo pipefail

# FILE: build_app.sh
# Purpose: Builds a standalone ClapTrigger.app from the current checkout.
# Depends on: python, PyInstaller, the runtime requirements, and iconutil/AppKit on macOS.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! "$PYTHON_BIN" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "PyInstaller is missing. Run: $PYTHON_BIN -m pip install pyinstaller" >&2
  exit 1
fi

"$PYTHON_BIN" "$ROOT_DIR/scripts/generate_icon.py"
rm -rf "$ROOT_DIR/build" "$ROOT_DIR/dist"
"$PYTHON_BIN" -m PyInstaller --noconfirm --clean "$ROOT_DIR/ClapTrigger.spec"

echo "Built $ROOT_DIR/dist/ClapTrigger.app"
