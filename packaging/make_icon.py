#!/usr/bin/env python3
"""Draw the application icon with Qt and pack a multi-size Windows .ico (PNG entries, no Pillow needed).

    QT_QPA_PLATFORM=offscreen python packaging/make_icon.py
"""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parents[1]


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#1f6feb"))
    g.setColorAt(1, QColor("#0a3d91"))
    p.setBrush(QBrush(g))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 44 * s, 44 * s)
    # axes
    p.setPen(QPen(QColor(255, 255, 255, 150), max(1.0, 6 * s), Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(52 * s, 200 * s), QPointF(212 * s, 200 * s))
    p.drawLine(QPointF(52 * s, 200 * s), QPointF(52 * s, 48 * s))
    # torque-speed envelope: constant torque, then constant power
    path = QPainterPath(QPointF(52 * s, 78 * s))
    path.lineTo(QPointF(112 * s, 78 * s))
    for k in range(1, 41):
        x = 112 + k * 2.5
        path.lineTo(QPointF(x * s, (200 - 122 * 112 / x) * s + 0 * s))
    p.setPen(QPen(QColor("#ffffff"), max(1.5, 14 * s), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    # operating point
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ff9f1c"))
    r = 17 * s
    p.drawEllipse(QPointF(150 * s, 140 * s), r, r)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba.data())


def write_ico(path: Path, sizes=(16, 24, 32, 48, 64, 128, 256)) -> None:
    pngs = [(sz, png_bytes(draw(sz))) for sz in sizes]
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for sz, data in pngs:
        entries += struct.pack("<BBBBHHII", sz % 256, sz % 256, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    path.write_bytes(header + entries + blobs)


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    res = ROOT / "src" / "traction_workbench" / "desktop" / "resources"
    res.mkdir(parents=True, exist_ok=True)
    draw(256).save(str(res / "icon.png"))
    write_ico(ROOT / "packaging" / "icon.ico")
    print("icon written:", res / "icon.png", ROOT / "packaging" / "icon.ico")
