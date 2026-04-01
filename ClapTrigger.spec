# -*- mode: python ; coding: utf-8 -*-
"""
FILE: ClapTrigger.spec
Purpose: Builds a standalone LSUIElement macOS app bundle for OpenClap with
the same executable reused for no-arg app launch, daemon mode, and menu mode.
Depends on: PyInstaller plus app_paths.py for shared bundle metadata.
"""

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules


project_root = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(project_root))

from app_paths import APP_BUNDLE_ID, APP_NAME, APP_VERSION


icon_path = project_root / "assets" / f"{APP_NAME}.icns"
hiddenimports = sorted(set(collect_submodules("rumps") + ["AppKit", "Foundation", "objc", "rumps"]))
binaries = collect_dynamic_libs("sounddevice")

a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=str(icon_path) if icon_path.exists() else None,
    bundle_identifier=APP_BUNDLE_ID,
    info_plist={
        "CFBundleDisplayName": APP_NAME,
        "CFBundleName": APP_NAME,
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "LSUIElement": True,
        "NSMicrophoneUsageDescription": "OpenClap needs microphone access to detect your double clap shortcut.",
    },
)
