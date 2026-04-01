#!/bin/zsh
set -euo pipefail

# FILE: build_app.sh
# Purpose: Builds the native OpenClap.app plus the embedded Python helper runtime.
# Depends on: python, PyInstaller, Swift/Xcode, and the repo-local native shell package.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
NATIVE_PACKAGE_DIR="$ROOT_DIR/native/OpenClapDesktop"
HELPER_SPEC="$ROOT_DIR/OpenClapHelper.spec"

if ! "$PYTHON_BIN" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "PyInstaller is missing. Run: $PYTHON_BIN -m pip install pyinstaller" >&2
  exit 1
fi

if ! command -v swift >/dev/null 2>&1; then
  echo "Swift is missing. Install Xcode command line tools first." >&2
  exit 1
fi

"$PYTHON_BIN" "$ROOT_DIR/scripts/generate_icon.py"
rm -rf "$ROOT_DIR/build" "$ROOT_DIR/dist" "$NATIVE_PACKAGE_DIR/.build"
"$PYTHON_BIN" -m PyInstaller --noconfirm --clean "$HELPER_SPEC"
swift build -c release --package-path "$NATIVE_PACKAGE_DIR"

APP_NAME="$("$PYTHON_BIN" -c 'from app_paths import APP_NAME; print(APP_NAME)')"
APP_VERSION="$("$PYTHON_BIN" -c 'from app_paths import APP_VERSION; print(APP_VERSION)')"
APP_BUNDLE="$ROOT_DIR/dist/${APP_NAME}.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_MACOS="$APP_CONTENTS/MacOS"
APP_RESOURCES="$APP_CONTENTS/Resources"
HELPER_SOURCE_DIR="$ROOT_DIR/dist/OpenClapHelper"
NATIVE_BINARY="$NATIVE_PACKAGE_DIR/.build/release/OpenClapDesktop"

mkdir -p "$APP_MACOS" "$APP_RESOURCES/Helper"
cp "$NATIVE_PACKAGE_DIR/AppBundle/Info.plist" "$APP_CONTENTS/Info.plist"
cp "$NATIVE_BINARY" "$APP_MACOS/$APP_NAME"
chmod +x "$APP_MACOS/$APP_NAME"

if [[ -f "$ROOT_DIR/assets/${APP_NAME}.icns" ]]; then
  cp "$ROOT_DIR/assets/${APP_NAME}.icns" "$APP_RESOURCES/${APP_NAME}.icns"
fi

cp -R "$HELPER_SOURCE_DIR" "$APP_RESOURCES/Helper/"

OPENCLAP_ROOT_DIR="$ROOT_DIR" OPENCLAP_APP_RESOURCES="$APP_RESOURCES" OPENCLAP_APP_VERSION="$APP_VERSION" "$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path

payload = {
    "helperExecutable": "Helper/OpenClapHelper/OpenClapHelper",
    "sourceRoot": os.environ["OPENCLAP_ROOT_DIR"],
    "version": os.environ["OPENCLAP_APP_VERSION"],
}
resource_path = Path(os.environ["OPENCLAP_APP_RESOURCES"]) / "HelperConfig.json"
resource_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

echo "Built $APP_BUNDLE"
