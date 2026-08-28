"""Shared application shell widgets.

Holds :class:`SourceSelector`, the left navigation rail that replaced the
picker's top tab bar, and :class:`PalettePreview`, the live palette swatch shown
in Preferences.

Why a rail instead of tabs
--------------------------
Five tabs across the top read as a settings dialog. A left rail reads as an
application, gives each source a full-width hit target with room for a
description, and leaves the top edge free for window-level chrome.

Why it keeps the ``QTabWidget`` surface
---------------------------------------
The picker refers to its source panels by *title* in roughly forty places --
``_ensure_tab``, ``_select_tab``, ``_on_tab_changed``, ``_sync_tab_status``, drag
and drop, the WRF sub-mode switch, and the test suite. Rewriting all of that
alongside a layout change would mix two risky edits together. So this class
exposes the subset of the ``QTabWidget`` API the picker actually uses
(:meth:`addTab`, :meth:`insertTab`, :meth:`removeTab`, :meth:`count`,
:meth:`tabText`, :meth:`widget`, :meth:`currentIndex`, :meth:`setCurrentIndex`,
and a ``currentChanged`` signal) and is a drop-in replacement.

Lazy panel construction is preserved: the picker still swaps a placeholder for
the real panel at the same index, which is why :meth:`removeTab` and
:meth:`insertTab` exist here rather than a simpler "set the panels once" API.
"""

from __future__ import annotations

import logging
import math

from qtpy.QtCore import QPointF, QRectF, Qt, Signal
from qtpy.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from qtpy.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from sharpmod.theme import (
    NAV_RAIL_W,
    OBJ_CARD_TITLE,
    OBJ_NAV_RAIL,
    OBJ_NAV_RAIL_HEADER,
    SPACE,
)

_LOGGER = logging.getLogger("sharpmod.gui")

__all__ = ["SourceSelector", "PalettePreview"]


class SourceSelector(QWidget):
    """A left navigation rail over a stack of source panels.

    Emits :attr:`currentChanged` with the new index, matching
    ``QTabWidget.currentChanged`` so existing connections keep working.
    """

    currentChanged = Signal(int)

    def __init__(self, header: str = "Source", parent=None):
        super().__init__(parent)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- navigation rail ---
        rail = QFrame()
        rail.setObjectName(OBJ_NAV_RAIL)
        rail.setFixedWidth(NAV_RAIL_W)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(
            SPACE["sm"], SPACE["md"], SPACE["sm"], SPACE["sm"])
        rail_layout.setSpacing(SPACE["sm"])

        caption = QLabel(header)
        caption.setObjectName(OBJ_NAV_RAIL_HEADER)
        rail_layout.addWidget(caption)

        self._nav = QListWidget()
        self._nav.setObjectName(OBJ_NAV_RAIL)
        self._nav.setFrameShape(QFrame.NoFrame)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Selection *is* navigation here, so a cleared selection would leave the
        # stack showing a panel that no rail entry claims.
        self._nav.setSelectionMode(QListWidget.SingleSelection)
        self._nav.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._nav.currentRowChanged.connect(self._on_nav_row_changed)
        rail_layout.addWidget(self._nav, 1)

        outer.addWidget(rail)

        # --- panel stack ---
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        #: Titles, index-aligned with the stack. Kept alongside the rail items
        #: so :meth:`tabText` stays correct while a placeholder is being
        #: swapped out and the rail is momentarily out of step.
        self._titles: list[str] = []

    # -- QTabWidget-compatible surface -------------------------------------- #

    def addTab(self, widget: QWidget, title: str) -> int:
        """Append a panel and its rail entry. Returns the new index."""
        return self.insertTab(self.count(), widget, title)

    def insertTab(self, index: int, widget: QWidget, title: str) -> int:
        """Insert a panel and its rail entry at ``index``."""
        index = max(0, min(int(index), self.count()))
        self._stack.insertWidget(index, widget)
        self._titles.insert(index, title)

        item = QListWidgetItem(title)
        item.setToolTip(title)
        blocked = self._nav.signalsBlocked()
        self._nav.blockSignals(True)
        try:
            self._nav.insertItem(index, item)
            if self._nav.currentRow() < 0:
                self._nav.setCurrentRow(0)
        finally:
            self._nav.blockSignals(blocked)
        return index

    def removeTab(self, index: int) -> None:
        """Remove the panel and rail entry at ``index``.

        The widget is detached from the stack but not destroyed -- the caller
        owns its lifetime, matching ``QTabWidget.removeTab``.
        """
        if not 0 <= index < self.count():
            return
        widget = self._stack.widget(index)
        if widget is not None:
            self._stack.removeWidget(widget)
        del self._titles[index]
        blocked = self._nav.signalsBlocked()
        self._nav.blockSignals(True)
        try:
            self._nav.takeItem(index)
        finally:
            self._nav.blockSignals(blocked)

    def count(self) -> int:
        return self._stack.count()

    def tabText(self, index: int) -> str:
        """Return the title at ``index``, or ``""`` when out of range.

        Out-of-range returns empty rather than raising, because the picker
        compares this against expected titles during teardown, when the current
        index can briefly be -1.
        """
        if 0 <= index < len(self._titles):
            return self._titles[index]
        return ""

    def widget(self, index: int) -> QWidget | None:
        if 0 <= index < self.count():
            return self._stack.widget(index)
        return None

    def indexOf(self, widget: QWidget) -> int:
        return self._stack.indexOf(widget)

    def currentIndex(self) -> int:
        return self._stack.currentIndex()

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < self.count():
            return
        if index == self._stack.currentIndex() and \
                self._nav.currentRow() == index:
            return
        self._stack.setCurrentIndex(index)
        if self._nav.currentRow() != index:
            blocked = self._nav.signalsBlocked()
            self._nav.blockSignals(True)
            try:
                self._nav.setCurrentRow(index)
            finally:
                self._nav.blockSignals(blocked)
        self.currentChanged.emit(index)

    def currentWidget(self) -> QWidget | None:
        return self._stack.currentWidget()

    def setTabToolTip(self, index: int, tip: str) -> None:
        item = self._nav.item(index)
        if item is not None:
            item.setToolTip(tip)

    # -- internals ---------------------------------------------------------- #

    def _on_nav_row_changed(self, row: int) -> None:
        """Mirror a rail click onto the stack and re-emit as a tab change."""
        if row < 0 or row >= self.count():
            return
        if self._stack.currentIndex() != row:
            self._stack.setCurrentIndex(row)
        self.currentChanged.emit(row)


class PalettePreview(QWidget):
    """A live miniature sounding drawn in a candidate colour palette.

    Replaces the upstream ``ColorPreview``, which showed nothing in the frozen
    application. That widget loads ``rc/sample_std.png`` and friends from
    ``sharppy/viz/../../rc`` -- a *top-level* directory in site-packages, not
    part of the ``sharppy`` package. ``collect_all("sharppy")`` therefore never
    collected it, so the frozen tree has no ``_internal/rc`` and the pixmap came
    back null, leaving the Colors tab blank.

    Drawing the preview instead of shipping screenshots fixes that and is more
    accurate besides:

    * It reads the *actual* palette this fork will apply, via
      ``_color_style_preferences``. The upstream PNGs are static captures of
      upstream's own layout, so they never showed this fork's amber alert
      substitutions or its added panels.
    * It costs no bundle space. The three sample PNGs are 3.7 MB each.
    * It cannot go stale when a palette value changes.

    The drawing is deliberately schematic -- a grid, two traces, level markers, a
    hodograph, and an index strip. It exists to answer "what will this palette
    look like", not to be a readable sounding.
    """

    #: Fallbacks for keys a palette may omit. Protanopia has no
    #: ``skew_el_mkr_color``, so a plain lookup would raise.
    _FALLBACKS = {
        "skew_el_mkr_color": "fg_color",
        "wetb_color": "dewp_color",
        "skew_mixr_color": "skew_adiab_color",
    }

    def __init__(self, palette: dict | None = None, parent=None):
        super().__init__(parent)
        self._palette: dict = {}
        self.setMinimumSize(320, 190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.set_palette(palette or {})

    # -- data --------------------------------------------------------------- #

    def set_palette(self, palette: dict) -> None:
        """Show ``palette``, a ``_color_style_preferences`` mapping."""
        self._palette = dict(palette or {})
        self.update()

    def _colour(self, key: str, default: str = "#808080") -> QColor:
        value = self._palette.get(key)
        if not value:
            alias = self._FALLBACKS.get(key)
            if alias:
                value = self._palette.get(alias)
        return QColor(value or default)

    # -- painting ----------------------------------------------------------- #

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        qp = QPainter(self)
        try:
            qp.setRenderHint(QPainter.Antialiasing, True)
            qp.setRenderHint(QPainter.TextAntialiasing, True)
            self._paint(qp)
        finally:
            qp.end()

    def _paint(self, qp: QPainter) -> None:
        bg = self._colour("bg_color", "#000000")
        fg = self._colour("fg_color", "#FFFFFF")
        w, h = self.width(), self.height()

        qp.fillRect(0, 0, w, h, bg)

        # Layout: skew-T on the left, hodograph top-right, index strip along the
        # bottom -- the same arrangement as the real window, so the preview reads
        # as "this is the sounding view".
        pad = SPACE["sm"]
        strip_h = max(22, int(h * 0.18))
        body_h = h - strip_h - pad * 2
        skew_w = int((w - pad * 3) * 0.60)
        skew = QRectF(pad, pad, skew_w, body_h)
        hodo = QRectF(pad * 2 + skew_w, pad,
                      w - (pad * 3 + skew_w), body_h)
        strip = QRectF(pad, h - strip_h - pad, w - pad * 2, strip_h)

        self._paint_skewt(qp, skew, fg)
        self._paint_hodograph(qp, hodo, fg)
        self._paint_index_strip(qp, strip, fg)

    def _paint_skewt(self, qp: QPainter, r: QRectF, fg: QColor) -> None:
        # Clip to the panel: the skewed isotherms and the left-leaning traces
        # both run past the edges by design, so they are cropped rather than
        # foreshortened, exactly as the real Skew-T does.
        qp.save()
        try:
            qp.setClipRect(r)
            self._paint_skewt_content(qp, r)
        finally:
            qp.restore()

        qp.setPen(QPen(fg, 1))
        qp.setBrush(Qt.NoBrush)
        qp.drawRect(r)

    def _paint_skewt_content(self, qp: QPainter, r: QRectF) -> None:
        # Construction lines first, so the traces read on top of them.
        #
        # Direction matters even in a schematic: on a skew-T the isotherms are
        # skewed up and to the *right*, while a real temperature profile cools
        # with height and so runs up and to the *left*. Drawing either backwards
        # would look wrong to anyone who reads soundings.
        skew = r.width() * 0.30
        qp.setPen(QPen(self._colour("skew_itherm_color", "#555555"), 1))
        for i in range(-1, 7):
            x = r.left() + r.width() * i / 6.0
            qp.drawLine(QPointF(x, r.bottom()), QPointF(x + skew, r.top()))

        # Isobars: horizontal, evenly spaced for a schematic.
        qp.setPen(QPen(self._colour("skew_adiab_color", "#333333"), 1))
        for i in range(1, 5):
            y = r.top() + r.height() * i / 5.0
            qp.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))

        # Mixing-ratio line: also rising to the right, but steeper.
        qp.setPen(QPen(self._colour("skew_mixr_color", "#006600"), 1,
                       Qt.DashLine))
        qp.drawLine(QPointF(r.left() + r.width() * 0.16, r.bottom()),
                    QPointF(r.left() + r.width() * 0.30, r.top()))

        def trace(colour: QColor, surface_x: float, width: float) -> None:
            """Draw a profile from ``surface_x`` at the bottom, leaning left."""
            qp.setPen(QPen(colour, width))
            points = []
            steps = 6
            for step in range(steps + 1):
                t = step / steps
                # Cools with height, with a slight kink so it reads as data
                # rather than a straight ruled line.
                x = r.left() + r.width() * (
                    surface_x - 0.34 * t + 0.03 * (step % 2))
                y = r.bottom() - r.height() * t
                points.append(QPointF(x, y))
            qp.drawPolyline(QPolygonF(points))

        # Dewpoint sits left of temperature, since it is the colder of the two.
        trace(self._colour("dewp_color", "#00FF00"), 0.52, 2.0)
        trace(self._colour("wetb_color", "#00FFFF"), 0.62, 1.0)
        trace(self._colour("temp_color", "#FF0000"), 0.74, 2.0)

        # Level markers, the palette's most semantically loaded colours.
        markers = (
            ("skew_lcl_mkr_color", 0.22, "LCL"),
            ("skew_lfc_mkr_color", 0.44, "LFC"),
            ("skew_el_mkr_color", 0.80, "EL"),
        )
        font = QFont(self.font())
        font.setPointSizeF(6.5)
        qp.setFont(font)
        for key, frac, label in markers:
            colour = self._colour(key, "#FFFFFF")
            y = r.bottom() - r.height() * frac
            qp.setPen(QPen(colour, 1.6))
            # Right-hand gutter, clear of the traces which drift left with
            # height.
            qp.drawLine(QPointF(r.right() - r.width() * 0.20, y),
                        QPointF(r.right() - r.width() * 0.04, y))
            qp.drawText(QPointF(r.right() - r.width() * 0.19, y - 2), label)

    def _paint_hodograph(self, qp: QPainter, r: QRectF, fg: QColor) -> None:
        centre = r.center()
        radius = min(r.width(), r.height()) * 0.42

        qp.setPen(QPen(self._colour("hodo_itach_color", "#555555"), 1))
        for ring in (0.35, 0.70, 1.0):
            qp.drawEllipse(centre, radius * ring, radius * ring)

        # Height-banded trace: the four band colours are what a user most needs
        # to judge, especially on the protanopia palette.
        bands = (
            ("0_3_color", 0.00, 0.30),
            ("3_6_color", 0.30, 0.55),
            ("6_9_color", 0.55, 0.78),
            ("9_12_color", 0.78, 1.00),
        )
        for key, start, end in bands:
            qp.setPen(QPen(self._colour(key, "#FFFFFF"), 2.0))
            points = []
            steps = 6
            for step in range(steps + 1):
                t = start + (end - start) * step / steps
                angle = -0.4 + t * 3.6
                rad = radius * (0.18 + 0.80 * t)
                points.append(QPointF(
                    centre.x() + rad * math.cos(angle),
                    centre.y() - rad * math.sin(angle)))
            qp.drawPolyline(QPolygonF(points))

        qp.setPen(QPen(fg, 1))
        qp.drawRect(r)

    def _paint_index_strip(self, qp: QPainter, r: QRectF, fg: QColor) -> None:
        # The alert ramp drives every derived-parameter value in the real
        # window, so showing it in order is the most informative strip.
        ramp = ("pcl_cin_lo_color", "alert_l1_color", "alert_l2_color",
                "alert_l4_color", "alert_l5_color", "alert_l6_color")
        font = QFont(self.font())
        font.setPointSizeF(6.5)
        qp.setFont(font)

        cell_w = r.width() / len(ramp)
        for i, key in enumerate(ramp):
            cell = QRectF(r.left() + cell_w * i, r.top(), cell_w, r.height())
            colour = self._colour(key, "#FFFFFF")
            qp.setBrush(QBrush(colour))
            qp.setPen(Qt.NoPen)
            inner = cell.adjusted(1.5, 1.5, -1.5, -1.5)
            qp.drawRect(inner)
            # Label in the canvas background colour so it stays legible on every
            # swatch without needing a second contrast decision here.
            qp.setPen(QPen(self._colour("bg_color", "#000000")))
            qp.drawText(inner, Qt.AlignCenter, str(i + 1))

        qp.setBrush(Qt.NoBrush)
        qp.setPen(QPen(fg, 1))
        qp.drawRect(r)
