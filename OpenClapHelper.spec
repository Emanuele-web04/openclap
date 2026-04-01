# -*- mode: python ; coding: utf-8 -*-
"""
FILE: OpenClapHelper.spec
Purpose: Builds the bundled Python helper runtime used by the native SwiftUI shell.
Depends on: PyInstaller, sounddevice, and the helper entrypoint in main.py.
"""

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules


project_root = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(project_root))

icon_path = project_root / "assets" / "OpenClap.icns"
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
    name="OpenClapHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    icon=str(icon_path) if icon_path.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="OpenClapHelper",
)
