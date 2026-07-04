"""Custom wind-barb drawing with a speed-based color table.

This is the SHARPpy Reimagined port of the user's ``barbs.py`` drawing: wind barbs are
colored by speed (a pink->white gradient at the highest speeds, through
red/orange/yellow/green/cyan down to white for calm) instead of a single flat
color. The vendored ``sharppy.viz.skew``
imports ``drawBarb`` from ``sharppy.viz.barbs``; the renderer monkeypatches that
reference to this :func:`drawBarb` so the skew-T wind profile uses the table
(see ``sharpmod.render._install_custom_barbs``).
"""

from __future__ import annotations

from qtpy import QtGui, QtCore

__all__ = ["drawBarb", "drawFlag", "drawFullBarb", "drawHalfBarb", "barb_color"]


def drawFlag(path, shemis=False):
    side = -1 if shemis else 1
    pos = path.currentPosition()
    path.lineTo(pos.x(), pos.y() + side * 10)
    path.lineTo(pos.x() - 4, pos.y())
    path.moveTo(pos.x() - 6, pos.y())


def drawFullBarb(path, shemis=False):
    side = -1 if shemis else 1
    pos = path.currentPosition()
    path.lineTo(pos.x(), pos.y() + side * 10)
    path.moveTo(pos.x() - 4, pos.y())


def drawHalfBarb(path, shemis=False):
    side = -1 if shemis else 1
    pos = path.currentPosition()
    path.lineTo(pos.x(), pos.y() + side * 5)
    path.moveTo(pos.x() - 4, pos.y())


# Speed (kt) -> color table (evaluated high-to-low so the last matching branch
# for a given speed wins, mirroring the source ``barbs.py``).
# The high-speed band (>= 80 kt) is a pink->white gradient: pinkest at ~80 kt,
# whitening as the speed climbs so the strongest winds read near-white.
_BARB_TABLE = [
    (100, "#FFE6F2"), (95, "#FFCCE5"), (90, "#FFB3D9"), (85, "#FF99CC"),
    (80, "#FF80BF"), (75, "#FF0000"), (60, "#FF4000"), (55, "#FF8000"),
    (50, "#FFBF00"), (45, "#FFFF00"), (40, "#BFFF00"), (35, "#80FF00"),
    (30, "#40FF00"), (25, "#00FF00"), (20, "#00FF40"), (15, "#00FF80"),
    (10, "#00FFBF"), (5, "#00FFFF"),
]


def barb_color(wspd):
    """Return the barb color for a wind speed (kt), per the custom table."""
    color = "#FFFFFF"          # highest speeds (> 100 kt) read white
    for threshold, hexcolor in _BARB_TABLE:
        if wspd <= threshold:
            color = hexcolor
    if wspd < 3:
        color = "#FFFFFF"
    return color


def drawBarb(qp, origin_x, origin_y, wdir, wspd, color="#FFFFFF", shemis=False):
    """Draw a wind barb colored by ``wspd`` (custom speed color table)."""
    color = barb_color(wspd)
    pen = QtGui.QPen(QtGui.QColor(color), 1, QtCore.Qt.SolidLine)
    pen.setWidthF(1.)
    qp.setPen(pen)
    qp.setBrush(QtCore.Qt.NoBrush)

    try:
        wspd = int(round(wspd / 5.) * 5)   # round to nearest 5
    except ValueError:
        return

    qp.translate(origin_x, origin_y)
    if wspd > 0:
        qp.rotate(wdir - 90)
        path = QtGui.QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(25, 0)
        while wspd >= 50:
            drawFlag(path, shemis=shemis)
            wspd -= 50
        while wspd >= 10:
            drawFullBarb(path, shemis=shemis)
            wspd -= 10
        while wspd >= 5:
            drawHalfBarb(path, shemis=shemis)
            wspd -= 5
        qp.drawPath(path)
        qp.rotate(90 - wdir)
    else:
        qp.drawEllipse(QtCore.QPoint(0, 0), 3, 3)
    qp.translate(-origin_x, -origin_y)
