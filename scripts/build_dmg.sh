#!/bin/zsh
set -euo pipefail

# FILE: build_dmg.sh
# Purpose: Packages the generated .app into a drag-and-drop DMG for GitHub Releases.
# Depends on: an existing dist/OpenClap.app plus hdiutil available on macOS.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_NAME="$("$PYTHON_BIN" -c 'from app_paths import APP_NAME; print(APP_NAME)')"
APP_VERSION="$("$PYTHON_BIN" -c 'from app_paths import APP_VERSION; print(APP_VERSION)')"
APP_PATH="$ROOT_DIR/dist/${APP_NAME}.app"
DMG_PATH="$ROOT_DIR/dist/${APP_NAME}-${APP_VERSION}.dmg"
STAGE_DIR="$ROOT_DIR/build/dmg-stage"

if [[ ! -d "$APP_PATH" ]]; then
  echo "Missing $APP_PATH. Run scripts/build_app.sh first." >&2
  exit 1
fi

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
cp -R "$APP_PATH" "$STAGE_DIR/"
ln -s /Applications "$STAGE_DIR/Applications"
rm -f "$DMG_PATH"

hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$STAGE_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH" >/dev/null

echo "Built $DMG_PATH"
