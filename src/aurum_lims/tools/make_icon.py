"""Write the Windows icon file (aurum.ico) from the app's 'Au' logo.

Usage (from the project folder)::

    .venv\\Scripts\\python -m aurum_lims.tools.make_icon

The .ico holds PNG images at the standard Windows sizes and is used by the
desktop shortcut and the stand-alone .exe.
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice

ICON_PATH = Path(__file__).resolve().parent.parent / "ui" / "aurum.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _png(size: int) -> bytes:
    from aurum_lims.ui.common import logo_pixmap

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    logo_pixmap(size).save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def build_ico(sizes=SIZES) -> bytes:
    """An .ico file made of PNG images (supported since Windows Vista)."""
    images = [(size, _png(size)) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO directory
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    return header + entries + blobs


def main() -> int:
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - needed to draw the logo
    ICON_PATH.write_bytes(build_ico())
    print(f"Wrote {ICON_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
