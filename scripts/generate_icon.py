"""
FILE: generate_icon.py
Purpose: Generates a simple clap-themed macOS .icns asset so local builds and
CI releases can package a branded menu bar app without manual design work.
Depends on: PyObjC/AppKit on macOS plus the project app metadata.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_paths import APP_NAME


ICONSET_SIZES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


def render_png(size: int, destination: Path) -> None:
    """Draws one rounded-square clap icon PNG using AppKit vector/text APIs."""

    try:
        from AppKit import (
            NSBezierPath,
            NSBitmapImageRep,
            NSColor,
            NSFont,
            NSFontAttributeName,
            NSImage,
            NSMakeRect,
            NSMutableParagraphStyle,
            NSParagraphStyleAttributeName,
            NSPNGFileType,
        )
        from Foundation import NSString
    except ImportError as exc:  # pragma: no cover - depends on macOS build environment
        raise SystemExit("generate_icon.py requires AppKit/PyObjC on macOS.") from exc

    image = NSImage.alloc().initWithSize_((size, size))
    image.lockFocus()
    try:
        inset = size * 0.06
        rect = NSMakeRect(inset, inset, size - (inset * 2), size - (inset * 2))

        shadow_rect = NSMakeRect(inset, inset * 0.75, size - (inset * 2), size - (inset * 1.7))
        shadow_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            shadow_rect,
            size * 0.20,
            size * 0.20,
        )
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.19, 0.11, 0.07, 0.14).setFill()
        shadow_path.fill()

        badge_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect,
            size * 0.22,
            size * 0.22,
        )
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.99, 0.63, 0.26, 1.0).setFill()
        badge_path.fill()

        highlight_rect = NSMakeRect(inset, size * 0.54, size - (inset * 2), size * 0.18)
        highlight_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            highlight_rect,
            size * 0.12,
            size * 0.12,
        )
        NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.86, 0.66, 0.30).setFill()
        highlight_path.fill()

        paragraph = NSMutableParagraphStyle.alloc().init()
        paragraph.setAlignment_(1)
        font = NSFont.fontWithName_size_("Apple Color Emoji", size * 0.56) or NSFont.systemFontOfSize_(size * 0.56)
        text_rect = NSMakeRect(0, size * 0.16, size, size * 0.66)
        NSString.stringWithString_("👏").drawInRect_withAttributes_(
            text_rect,
            {
                NSFontAttributeName: font,
                NSParagraphStyleAttributeName: paragraph,
            },
        )
    finally:
        image.unlockFocus()

    tiff_data = image.TIFFRepresentation()
    bitmap = NSBitmapImageRep.imageRepWithData_(tiff_data)
    png_data = bitmap.representationUsingType_properties_(NSPNGFileType, {})
    png_data.writeToFile_atomically_(str(destination), True)


def main() -> int:
    """Creates an iconset and compiles it into one .icns file."""

    if sys.platform != "darwin":
        raise SystemExit("Icon generation only runs on macOS because it uses iconutil.")

    assets_dir = PROJECT_ROOT / "assets"
    iconset_dir = assets_dir / f"{APP_NAME}.iconset"
    icns_path = assets_dir / f"{APP_NAME}.icns"

    shutil.rmtree(iconset_dir, ignore_errors=True)
    iconset_dir.mkdir(parents=True, exist_ok=True)

    for filename, size in ICONSET_SIZES.items():
        render_png(size, iconset_dir / filename)

    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
        check=True,
    )
    print(icns_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
