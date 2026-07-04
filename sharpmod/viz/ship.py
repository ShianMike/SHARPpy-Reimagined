"""SHIP chart inset widget (Requirement 20).

This module renders the Significant Hail Parameter (SHIP) on a small labeled
scale, analogous to the Effective Layer STP EF-scale inset in the legacy
SHARPpy renderer. It exposes a single Qt widget, :class:`plotSHIP`, a
``QFrame`` that draws:

* a numeric read-out of the current SHIP value,
* a horizontal scale axis whose endpoints are the documented
  :data:`SHIP_SCALE_MIN` / :data:`SHIP_SCALE_MAX` bounds, with tick labels, and
* a marker positioned along the axis at the SHIP value's location.

Behavior mandated by Requirement 20:

* **20.1 / 20.2** -- the value is drawn together with a scale axis whose min and
  max bounds are the documented limits, and the marker is placed at the axis
  location corresponding to the value.
* **20.3** -- values greater than :data:`SHIP_SCALE_MAX` are *clamped*: the
  marker is drawn at the maximum-bound endpoint, never beyond the drawn scale.
* **20.4** -- when SHIP cannot be computed (``prof.ship`` is the ``MISSING``
  masked constant, ``None``, or non-finite), the inset is still drawn but a
  missing-value indicator ("--") replaces the value marker.
* **20.5** -- all content is clipped to the widget's allocated panel region so
  nothing spills into adjacent panels.

Documented scale bounds
------------------------
SHIP is a unitless SPC composite for large hail. Operationally it ranges from 0
upward; values at or above ~1 indicate significant-hail (>=2 in) potential and
values of ~5+ are extreme. The renderer's SHIP tier table (``colors._SHIP_RULES``)
places its top tier at 5. To keep the extreme tier visible on-scale with a small
margin of headroom, this inset uses a documented display maximum of **6.0**:

    SHIP_SCALE_MIN = 0.0
    SHIP_SCALE_MAX = 6.0   # documented display bound; SHIP>=5 is already extreme

Any SHIP value above 6.0 is clamped to the 6.0 endpoint per Requirement 20.3.

Headless / import safety
------------------------
The vendored viz widgets use Qt6 via ``qtpy`` (PySide6). Importing this module
must not require a running ``QApplication``; only *instantiating* the widget
does. For headless rendering, set ``QT_QPA_PLATFORM=offscreen`` before creating
a ``QApplication``.
"""

from __future__ import annotations

from qtpy import QtGui, QtCore, QtWidgets

from sharpmod import colors
from sharpmod.sharptab.constants import is_missing

__all__ = ["plotSHIP", "SHIP_SCALE_MIN", "SHIP_SCALE_MAX"]


#: Documented minimum bound of the SHIP display scale (unitless).
SHIP_SCALE_MIN = 0.0
#: Documented maximum bound of the SHIP display scale (unitless).
#:
#: SHIP >= 5 is already the extreme tier (see ``colors._SHIP_RULES``); 6.0 gives
#: a small margin of headroom while keeping the extreme tier on-scale. Values
#: above this bound are clamped to the endpoint (Requirement 20.3).
SHIP_SCALE_MAX = 6.0

#: Text drawn in place of the marker when SHIP is unavailable (Requirement 20.4).
MISSING_STR = colors.MISSING_STR


class plotSHIP(QtWidgets.QFrame):
    """A ``QFrame`` inset that plots the SHIP value on a labeled scale.

    Call :meth:`setProf` with a Profile exposing a ``ship`` attribute to update
    the drawn value; the widget reads ``prof.ship``, clamps it to the documented
    scale, and repaints.
    """

    SCALE_MIN = SHIP_SCALE_MIN
    SCALE_MAX = SHIP_SCALE_MAX

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prof = None
        self.ship = None

        # Palette (documented modernized color scheme, Requirement 22).
        self.bg_color = QtGui.QColor(colors.BG_COLOR)
        self.fg_color = QtGui.QColor(colors.FG_COLOR)

        # Frame styling to match the other vendored insets.
        self.setStyleSheet(
            "QFrame {"
            "  background-color: rgb(0, 0, 0);"
            "  border-width: 1px;"
            "  border-style: solid;"
            "  border-color: #3a3a3a;"
            "  margin: 0px;"
            "}"
        )

        self.title = "Sig. Hail (SHIP)"

        # Fonts / metrics.
        self.title_font = QtGui.QFont("Helvetica")
        self.title_font.setPixelSize(11)
        self.label_font = QtGui.QFont("Helvetica")
        self.label_font.setPixelSize(10)
        self.value_font = QtGui.QFont("Helvetica")
        self.value_font.setPixelSize(16)
        self.value_font.setBold(True)

        self.setMinimumSize(120, 60)

        # Backing pixmap, sized to the current widget geometry.
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg_color)
        self._setupGeometry()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def setProf(self, prof):
        """Read ``prof.ship`` and redraw the inset (Requirement 20.1).

        Missing/masked/non-finite values leave :attr:`ship` as ``None`` so the
        missing-value indicator is drawn (Requirement 20.4).
        """
        self.prof = prof
        self.ship = self._extract_ship(prof)
        self.clearData()
        self.plotData()
        self.update()

    def setPreferences(self, update_gui: bool = True, **prefs):
        """Apply bg/fg preferences and (optionally) redraw (Requirement 22.4).

        Mirrors the vendored inset ``setPreferences`` contract so this inset is
        driven by the same Color-Scheme re-apply path as the other panels. The
        redraw recomputes the SHIP tier color from the *current* value via
        :func:`sharpmod.colors.ship_color` (Requirement 22.5) -- no stale color
        is retained across a re-apply.
        """
        if "bg_color" in prefs:
            self.bg_color = QtGui.QColor(prefs["bg_color"])
        if "fg_color" in prefs:
            self.fg_color = QtGui.QColor(prefs["fg_color"])
        if update_gui:
            self.clearData()
            self.plotData()
            self.update()

    def clearData(self):
        """Reset the backing pixmap to the background color."""
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg_color)

    # ------------------------------------------------------------------
    # Value handling
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_ship(prof):
        """Return the SHIP value as a finite float, or ``None`` when missing."""
        if prof is None:
            return None
        value = getattr(prof, "ship", None)
        if is_missing(value):
            return None
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        # Guard against non-finite that slipped past is_missing.
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f

    @classmethod
    def scale_fraction(cls, value):
        """Map a SHIP ``value`` to a clamped fraction in ``[0, 1]``.

        Implements the clamped monotonic mapping (design Property 26):
        ``SCALE_MIN`` -> 0.0, ``SCALE_MAX`` -> 1.0, values above ``SCALE_MAX``
        clamp to 1.0 (Requirement 20.3), values below ``SCALE_MIN`` clamp to 0.0.
        """
        span = cls.SCALE_MAX - cls.SCALE_MIN
        if span <= 0:
            return 0.0
        frac = (value - cls.SCALE_MIN) / span
        if frac < 0.0:
            return 0.0
        if frac > 1.0:
            return 1.0
        return frac

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    def _setupGeometry(self):
        """Compute the axis rectangle from the current widget size."""
        w = max(1, self.width())
        h = max(1, self.height())
        self.lpad = 12
        self.rpad = 12
        self.tpad = 4
        self.bpad = 4
        # Axis baseline sits in the lower third; value read-out above it.
        self.axis_x0 = self.lpad
        self.axis_x1 = w - self.rpad
        self.axis_y = int(h * 0.68)
        self.title_y = self.tpad + 12
        self.value_y = int(h * 0.44)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._setupGeometry()
        self.clearData()
        self.plotData()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, e):
        super().paintEvent(e)
        qp = QtGui.QPainter()
        qp.begin(self)
        # Clip to the widget's own region so no content leaves the panel
        # (Requirement 20.5).
        qp.setClipRect(self.rect())
        qp.drawPixmap(0, 0, self.plotBitMap)
        qp.end()

    def plotData(self):
        """Draw the inset onto the backing pixmap."""
        qp = QtGui.QPainter()
        qp.begin(self.plotBitMap)
        qp.setRenderHint(QtGui.QPainter.Antialiasing)
        # Clip every draw to the pixmap bounds (Requirement 20.5).
        qp.setClipRect(QtCore.QRect(0, 0, self.plotBitMap.width(), self.plotBitMap.height()))
        self._drawTitle(qp)
        self._drawScale(qp)
        if self.ship is None:
            self._drawMissing(qp)
        else:
            self._drawValue(qp)
            self._drawMarker(qp)
        qp.end()

    def _drawTitle(self, qp):
        qp.setFont(self.title_font)
        qp.setPen(QtGui.QPen(self.fg_color, 1, QtCore.Qt.SolidLine))
        rect = QtCore.QRect(self.lpad, self.tpad, self.width() - self.lpad - self.rpad, 14)
        qp.drawText(rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self.title)

    def _drawScale(self, qp):
        """Draw the axis line, endpoints, and min/max/mid tick labels."""
        pen = QtGui.QPen(self.fg_color, 1, QtCore.Qt.SolidLine)
        qp.setPen(pen)
        y = self.axis_y
        qp.drawLine(self.axis_x0, y, self.axis_x1, y)

        qp.setFont(self.label_font)
        # Tick marks + labels at min, midpoint, and max of the documented scale.
        ticks = (
            (self.SCALE_MIN, self.axis_x0, QtCore.Qt.AlignLeft),
            ((self.SCALE_MIN + self.SCALE_MAX) / 2.0,
             (self.axis_x0 + self.axis_x1) // 2, QtCore.Qt.AlignHCenter),
            (self.SCALE_MAX, self.axis_x1, QtCore.Qt.AlignRight),
        )
        for value, x, _align in ticks:
            qp.drawLine(int(x), y - 3, int(x), y + 3)
            label = self._fmt(value)
            tw = 30
            rect = QtCore.QRect(int(x) - tw // 2, y + 4, tw, 12)
            qp.drawText(rect, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, label)

    def _drawValue(self, qp):
        """Draw the numeric SHIP read-out, colored by its tier."""
        qp.setFont(self.value_font)
        color = QtGui.QColor(colors.ship_color(self.ship))
        qp.setPen(QtGui.QPen(color, 1, QtCore.Qt.SolidLine))
        rect = QtCore.QRect(self.lpad, self.value_y - 12,
                            self.width() - self.lpad - self.rpad, 20)
        qp.drawText(rect, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                    self._fmt(self.ship))

    def _drawMarker(self, qp):
        """Position the marker along the axis (Requirements 20.2, 20.3)."""
        frac = self.scale_fraction(self.ship)
        x = self.axis_x0 + frac * (self.axis_x1 - self.axis_x0)
        x = int(round(x))
        color = QtGui.QColor(colors.ship_color(self.ship))
        qp.setPen(QtGui.QPen(color, 2, QtCore.Qt.SolidLine))
        qp.setBrush(QtGui.QBrush(color))
        y = self.axis_y
        # Downward-pointing triangle marker sitting on the axis.
        tri = QtGui.QPolygon([
            QtCore.QPoint(x, y - 1),
            QtCore.QPoint(x - 5, y - 10),
            QtCore.QPoint(x + 5, y - 10),
        ])
        qp.drawPolygon(tri)

    def _drawMissing(self, qp):
        """Draw the missing-value indicator instead of a marker (Req 20.4)."""
        qp.setFont(self.value_font)
        qp.setPen(QtGui.QPen(self.fg_color, 1, QtCore.Qt.SolidLine))
        rect = QtCore.QRect(self.lpad, self.value_y - 12,
                            self.width() - self.lpad - self.rpad, 20)
        qp.drawText(rect, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, MISSING_STR)

    @staticmethod
    def _fmt(value):
        """Format a scale value/read-out compactly."""
        try:
            f = float(value)
        except (TypeError, ValueError):
            return MISSING_STR
        if f == int(f):
            return str(int(f))
        return f"{f:.1f}"
