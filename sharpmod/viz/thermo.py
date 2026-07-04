"""Index-table wiring for the new SHARPpy Reimagined derived parameters.

The legacy renderer draws its index tables in ``plotText`` (``thermo.py``);
severe indices such as SHIP live in ``plotText.drawSevere``. This module is the
modernized, *decoupled* successor that adds the eleven-parameter family of new
derived quantities to the rendered index tables:

======================  =========================  ================
Label                   Profile attribute          Units / format
======================  =========================  ================
``DCP``                 ``prof.dcp``               unitless (0.1)
``LRGHAIL``             ``prof.lrghail``           unitless (0.1)
``HPI``                 ``prof.hpi``               numeric  (0.1)
``Peskov``              ``prof.peskov``            numeric  (0.1)
``MCS``                 ``prof.mcs_index``         numeric  (0.1)
``EHI 0-1km``           ``prof.ehi_0_1km``         unitless (0.1)
``EHI 0-3km``           ``prof.ehi_0_3km``         unitless (0.1)
``HGZ CAPE``            ``prof.hgz_cape``          J/kg (integer)
``6CAPE``               ``prof.cape_0_6km``        J/kg (integer)
======================  =========================  ================

Behavior mandated by the requirements this task wires
(2.6/2.7, 6.7/6.8, 16.4/16.5, 17.4/17.5, 18.7/18.8, 19.7/19.8, 21.7/21.8,
13.3/13.4):

* **The renderer reads, it does not compute (13.3).** Every value is read
  *off the Profile attribute* (``prof.dcp``, ``prof.hgz_cape`` ...). The Profile
  computes each parameter lazily/cached; this module never recomputes any of
  them. It only ever *reads* the attribute and formats it for display.
* **Values are drawn in an index table (2.6, 6.7, 16.4, 17.4, 18.7, 19.7,
  21.7).** :class:`plotDerivedIndices` lays the label/value rows out in a
  ``QFrame`` panel styled to match the other vendored insets.
* **Missing values draw the documented indicator (2.7, 6.8, 13.4, 16.5, 17.5,
  18.8, 19.8, 21.8).** When an attribute is the ``MISSING`` masked constant,
  ``None``, ``"--"``, or otherwise non-finite (``colors.is_missing``), the row
  is still drawn but the value cell shows :data:`MISSING_STR` ("--") in the
  neutral foreground color instead of a number.

Decoupling / headless safety
----------------------------
Like :mod:`sharpmod.viz.ship` and :mod:`sharpmod.viz.skew`, the drawing logic is
factored into pure helpers (:func:`format_derived_value`, :func:`derived_rows`)
that need no Qt at all, plus a thin ``QFrame`` widget that paints them. Importing
this module never requires a running ``QApplication``; only *instantiating* the
widget does. For headless rendering set ``QT_QPA_PLATFORM=offscreen`` before
creating the ``QApplication``.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from qtpy import QtCore, QtGui, QtWidgets

from sharpmod import colors
from sharpmod.sharptab.constants import is_missing

__all__ = [
    "MISSING_STR",
    "DERIVED_INDEX_ROWS",
    "DERIVED_TIER_PARAMS",
    "format_derived_value",
    "derived_rows",
    "plotDerivedIndices",
]

#: The string drawn in place of an unavailable value (Requirement 13.4 et al.).
MISSING_STR = colors.MISSING_STR


#: Per-value tier coloring for the derived-index rows (Requirement 22.5).
#:
#: Maps a Profile attribute to the documented Color-Scheme tier parameter
#: (a key of :data:`sharpmod.colors.TIER_THRESHOLDS`) whose threshold map colors
#: it. Both HGZ CAPE and the SFC-6 km CAPE are CAPE-scale J/kg quantities, so
#: they are recolored on the documented CAPE tier scale; the remaining derived
#: indices have no documented tier scale and are drawn in the neutral
#: foreground. The color is recomputed from the *current* value at draw time
#: (never a stale default) via :func:`sharpmod.colors.tier_color`.
DERIVED_TIER_PARAMS = {
    "hgz_cape": "cape",
    "cape_0_6km": "cape",
}


# ---------------------------------------------------------------------------
# Value formatters (pure; no Qt)
# ---------------------------------------------------------------------------

def _fmt_float1(value: float) -> str:
    """Format a finite float to one decimal place."""
    return f"{value:.1f}"


def _fmt_int(value: float) -> str:
    """Format a finite value as a rounded integer (J/kg CAPE readouts)."""
    return str(int(round(value)))


#: Ordered specification of the derived index-table rows.
#:
#: Each entry is ``(label, profile_attribute, formatter)``. The order is the
#: draw order in the panel and matches the Data Models table in the design. The
#: attribute names are exactly the lazily-computed ``Profile`` attributes
#: registered in :mod:`sharpmod.sharptab.profile` -- the renderer reads these,
#: it never recomputes them (Requirement 13.3).
DERIVED_INDEX_ROWS: Tuple[Tuple[str, str, Callable[[float], str]], ...] = (
    ("DCP", "dcp", _fmt_float1),              # Req 2.6 / 2.7
    ("LRGHAIL", "lrghail", _fmt_float1),       # Req 6.7 / 6.8
    ("HPI", "hpi", _fmt_float1),               # Req 6.7 / 6.8
    ("Peskov", "peskov", _fmt_float1),         # Req 16.4 / 16.5
    ("MCS", "mcs_index", _fmt_float1),         # Req 17.4 / 17.5
    ("EHI 0-1km", "ehi_0_1km", _fmt_float1),   # Req 18.7 / 18.8
    ("EHI 0-3km", "ehi_0_3km", _fmt_float1),   # Req 18.7 / 18.8
    ("HGZ CAPE", "hgz_cape", _fmt_int),        # Req 19.7 / 19.8
    ("6CAPE", "cape_0_6km", _fmt_int),         # Req 21.7 / 21.8
)


def format_derived_value(prof, attr: str, formatter: Callable[[float], str]) -> str:
    """Read ``prof.<attr>`` and return its display string.

    The value is read *off the Profile* -- never recomputed here (Requirement
    13.3). When the value is missing/masked/non-finite (``colors.is_missing``)
    the documented missing-value indicator :data:`MISSING_STR` is returned
    (Requirements 2.7, 6.8, 13.4, 16.5, 17.5, 18.8, 19.8, 21.8).
    """
    if prof is None:
        return MISSING_STR
    value = getattr(prof, attr, None)
    if is_missing(value):
        return MISSING_STR
    try:
        f = float(value)
    except (TypeError, ValueError):
        return MISSING_STR
    # Guard against a non-finite that slipped past is_missing.
    if f != f or f in (float("inf"), float("-inf")):
        return MISSING_STR
    return formatter(f)


def derived_rows(prof) -> List[Tuple[str, str]]:
    """Return ``[(label, value_string), ...]`` for every derived index row.

    Each value string is produced by :func:`format_derived_value`, so missing
    values collapse to :data:`MISSING_STR` while present values are formatted to
    their documented precision. The order matches :data:`DERIVED_INDEX_ROWS`.
    """
    return [
        (label, format_derived_value(prof, attr, formatter))
        for (label, attr, formatter) in DERIVED_INDEX_ROWS
    ]


# ---------------------------------------------------------------------------
# Index-table panel widget
# ---------------------------------------------------------------------------

class plotDerivedIndices(QtWidgets.QFrame):
    """A ``QFrame`` index table for the new SHARPpy Reimagined derived parameters.

    Call :meth:`setProf` with a Profile exposing the derived attributes
    (``dcp``, ``lrghail``, ``hpi``, ``peskov``, ``mcs_index``, ``ehi_0_1km``,
    ``ehi_0_3km``, ``hgz_cape``, ``cape_0_6km``). The panel reads each value off
    the Profile, formats it, and draws a ``label = value`` row per parameter,
    substituting the missing-value indicator when a value is unavailable.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prof = None
        #: The rows most recently laid out, as ``(label, value_string)`` pairs.
        #: Exposed for tests/inspection.
        self.rows: List[Tuple[str, str]] = []

        # Palette (documented modernized color scheme, Requirement 22).
        self.bg_color = QtGui.QColor(colors.BG_COLOR)
        self.fg_color = QtGui.QColor(colors.FG_COLOR)

        # Frame styling to match the other vendored insets/tables.
        self.setStyleSheet(
            "QFrame {"
            "  background-color: rgb(0, 0, 0);"
            "  border-width: 1px;"
            "  border-style: solid;"
            "  border-color: #3a3a3a;"
            "  margin: 0px;"
            "}"
        )

        self.title = "Derived Indices"

        # Fonts / metrics.
        self.title_font = QtGui.QFont("Helvetica")
        self.title_font.setPixelSize(11)
        self.title_font.setBold(True)
        self.label_font = QtGui.QFont("Helvetica")
        self.label_font.setPixelSize(11)

        self.lpad = 6
        self.rpad = 6
        self.tpad = 4
        self.bpad = 4

        self.setMinimumSize(140, 120)

        # Backing pixmap, sized to the current widget geometry.
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg_color)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def setProf(self, prof):
        """Read the derived attributes off ``prof`` and redraw the table.

        Every value is read from its Profile attribute (Requirement 13.3);
        missing values are rendered as the missing-value indicator.
        """
        self.prof = prof
        self.rows = derived_rows(prof)
        self.clearData()
        self.plotData()
        self.update()

    def setPreferences(self, update_gui: bool = True, **prefs):
        """Apply background/foreground preferences and (optionally) redraw.

        Mirrors the vendored ``plotText.setPreferences`` contract so this panel
        can be driven by the same config re-apply path.
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
    # Qt events
    # ------------------------------------------------------------------
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.clearData()
        self.plotData()

    def paintEvent(self, e):
        super().paintEvent(e)
        qp = QtGui.QPainter()
        qp.begin(self)
        # Clip to the widget's own region so no content leaves the panel.
        qp.setClipRect(self.rect())
        qp.drawPixmap(0, 0, self.plotBitMap)
        qp.end()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def plotData(self):
        """Draw the derived-index table onto the backing pixmap."""
        if self.prof is None:
            return
        qp = QtGui.QPainter()
        qp.begin(self.plotBitMap)
        # Clip every draw to the pixmap bounds so nothing spills into adjacent
        # panels.
        qp.setClipRect(
            QtCore.QRect(0, 0, self.plotBitMap.width(), self.plotBitMap.height())
        )
        self._drawTitle(qp)
        self._drawRows(qp)
        qp.end()

    def _drawTitle(self, qp):
        qp.setFont(self.title_font)
        qp.setPen(QtGui.QPen(self.fg_color, 1, QtCore.Qt.SolidLine))
        rect = QtCore.QRect(self.lpad, self.tpad,
                            self.width() - self.lpad - self.rpad, 14)
        qp.drawText(rect, int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
                    self.title)
        # Underline separating the title from the value rows.
        y = self.tpad + 16
        qp.drawLine(self.lpad, y, self.width() - self.rpad, y)

    def _drawRows(self, qp):
        """Draw one ``label = value`` row per derived parameter."""
        qp.setFont(self.label_font)
        metrics = QtGui.QFontMetrics(self.label_font)
        row_h = metrics.height() + 2

        n = max(len(self.rows), 1)
        top = self.tpad + 20
        avail = max(self.height() - top - self.bpad, row_h)
        # Use the natural row height, but never overflow the panel.
        step = min(row_h, avail // n) if n else row_h
        step = max(step, 1)

        label_w = int((self.width() - self.lpad - self.rpad) * 0.55)
        value_x = self.lpad + label_w
        value_w = self.width() - self.rpad - value_x

        y = top
        # Zip the laid-out rows with their spec so the tier color can be
        # recomputed from the current Profile value at draw time (Req 22.5).
        for (label, value_str), (_lbl, attr, _fmt) in zip(
            self.rows, DERIVED_INDEX_ROWS
        ):
            color = (self.fg_color if value_str == MISSING_STR
                     else self._value_color(attr))
            # Label (neutral foreground).
            qp.setPen(QtGui.QPen(self.fg_color, 1, QtCore.Qt.SolidLine))
            label_rect = QtCore.QRect(self.lpad, y, label_w, step)
            qp.drawText(label_rect,
                        int(QtCore.Qt.TextSingleLine | QtCore.Qt.AlignLeft
                            | QtCore.Qt.AlignVCenter),
                        label)
            # Value (tier color when applicable, else neutral).
            qp.setPen(QtGui.QPen(color, 1, QtCore.Qt.SolidLine))
            value_rect = QtCore.QRect(value_x, y, value_w, step)
            qp.drawText(value_rect,
                        int(QtCore.Qt.TextSingleLine | QtCore.Qt.AlignRight
                            | QtCore.Qt.AlignVCenter),
                        value_str)
            y += step

    def _value_color(self, attr: str) -> QtGui.QColor:
        """Tier color for the row ``attr``, recomputed from the current value.

        Rows carrying a documented tier scale (:data:`DERIVED_TIER_PARAMS`) are
        recolored by mapping the *current* Profile value through
        :func:`sharpmod.colors.tier_color` on every draw, so changing the value
        into a different threshold band changes the drawn color and no stale
        color is retained (Requirement 22.5). Rows with no documented tier scale
        are drawn in the neutral foreground so the value stays legible.
        """
        param = DERIVED_TIER_PARAMS.get(attr)
        if param is None or self.prof is None:
            return self.fg_color
        value = getattr(self.prof, attr, None)
        if is_missing(value):
            return self.fg_color
        try:
            hex_color = colors.tier_color(param, value)
        except Exception:
            return self.fg_color
        return QtGui.QColor(hex_color)
