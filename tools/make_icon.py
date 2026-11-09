"""Build packaging/build/app.ico from the vector icon.

Crispness rule (docs/UI_DESIGN.md): every icon size is rendered NATIVELY from the SVG with
QSvgRenderer - never downscaled from one big bitmap - then packed as PNG entries in a multi-size
.ico (Vista+ format). Also writes a contact sheet PNG for visual QA.

    uv run python tools/make_icon.py
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "src" / "deadbymapper" / "ui" / "qml" / "icons" / "app_icon.svg"
OUT = ROOT / "packaging" / "build"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render_png(size: int) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    QSvgRenderer(str(SVG)).render(p)
    p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba.data())


def pack_ico(pngs: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, data in pngs:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication, QImage, QPainter

    _app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    pngs = [(s, render_png(s)) for s in SIZES]
    (OUT / "app.ico").write_bytes(pack_ico(pngs))
    sheet = QImage(sum(SIZES) + 10 * len(SIZES), 256, QImage.Format.Format_ARGB32)
    sheet.fill(0xFF0B0E13)
    p = QPainter(sheet)
    x = 0
    for s, data in pngs:
        img = QImage.fromData(data, "PNG")
        p.drawImage(x, 256 - s, img)
        x += s + 10
    p.end()
    sheet.save(str(OUT / "icon_sheet.png"))
    print("wrote", OUT / "app.ico", "and icon_sheet.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
