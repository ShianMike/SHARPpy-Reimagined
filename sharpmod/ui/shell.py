"""Shared application shell widgets.

Holds :class:`SourceSelector`, the compact source dropdown controlling the
picker's panel stack, and :class:`PalettePreview`, the live palette swatch shown
in Preferences.

Why a dropdown instead of tabs or a navigation rail
----------------------------------------------------
The source is a single mutually-exclusive choice. Keeping that choice beside
File, Locations, View, and Help makes it visible without permanently taking a
strip from every map. The panel stack remains the central widget; the picker
places :meth:`SourceSelector.navigation_widget` in its menu bar.

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

from qtpy.QtCore import QEvent, QObject, QPointF, QRectF, QTimer, Qt, Signal
from qtpy.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from qtpy.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.ui.features.gui_common import configure_combo_popup, place_combo_popup
from sharpmod.ui.styles.theme import (
    CONTROL_H,
    OBJ_CARD_TITLE,
    OBJ_DOCK_TITLE,
    OBJ_GHOST,
    OBJ_HEADER_BAR,
    OBJ_PLAIN,
    PROP_COMPACT,
    SPACE,
)

_LOGGER = logging.getLogger("sharpmod.gui")

__all__ = ["SourceSelector", "PalettePreview", "ResponsivePickerHeader", "dock_title_bar"]


class ResponsivePickerHeader(QObject):
    """Reflow the same source/clock controls when menu corners cannot fit.

    Normal windows retain the compact bar. Native menus never share their row
    with controls that would cover them; the optional clock can yield first.
    """

    def __init__(self, owner, container, source, clock, *, after_reflow=None):
        super().__init__(owner)
        self.owner, self.source, self.clock = owner, source, clock
        # Called once the layout decision is final. Anything that budgets itself
        # against the remaining row space has to be recomputed after the flip, not
        # before it, or it spends a whole resize acting on the previous layout.
        self._after_reflow = after_reflow
        self.bar = owner.menuBar()
        self.row_widget = QWidget(container)
        self.row_widget.setObjectName(OBJ_PLAIN)
        self.row = QHBoxLayout(self.row_widget)
        self.row.setContentsMargins(SPACE["sm"], 0, SPACE["sm"], 0)
        self.row.setSpacing(SPACE["sm"])
        self.row.addStretch(1)
        container.layout().insertWidget(0, self.row_widget)
        self.row_widget.hide()
        self._separate = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.reflow)
        for widget in (owner, self.bar, source, clock):
            widget.installEventFilter(self)
        self._timer.start(0)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if event.type() in {QEvent.FontChange, QEvent.StyleChange, QEvent.LayoutRequest} or (
            watched is self.owner and event.type() in {QEvent.Resize, QEvent.Show}
        ):
            self._timer.start(0)
        return super().eventFilter(watched, event)

    def reflow(self):
        left = max(self.source.sizeHint().width(), self.source.minimumSizeHint().width())
        right = max(self.clock.sizeHint().width(), self.clock.minimumSizeHint().width())
        metrics = self.bar.fontMetrics()
        menus = sum(metrics.horizontalAdvance(action.text().replace("&", ""))
                    + SPACE["lg"] for action in self.bar.actions())
        available = self.owner.width() - 2 * SPACE["sm"]
        separate = left + right + menus + SPACE["lg"] > available
        if separate != self._separate:
            self._separate = separate
            if separate:
                self.bar.setCornerWidget(None, Qt.TopLeftCorner)
                self.bar.setCornerWidget(None, Qt.TopRightCorner)
                self.row.insertWidget(0, self.source)
                self.row.addWidget(self.clock)
            else:
                self.row.removeWidget(self.source)
                self.row.removeWidget(self.clock)
                self.bar.setCornerWidget(self.source, Qt.TopLeftCorner)
                self.bar.setCornerWidget(self.clock, Qt.TopRightCorner)
            self.source.show()
            self.row_widget.setVisible(separate)
        self.clock.setVisible(not separate or left + right + SPACE["sm"] <= available)
        if self._after_reflow is not None:
            self._after_reflow()

    def is_separated(self) -> bool:
        """Whether the source and clock have been moved off the menu row.

        Exposed so an optional extra in either corner widget can stand down
        instead of competing for a row that has already run out of space.
        """
        return self._separate


def dock_title_bar(dock, title: str, *, shortcut_hint: str | None = None) -> QFrame:
    """Build a themed dock title bar with a properly sized close button.

    Replaces Qt's built-in dock title bar, which cannot be themed usefully:
    the Fusion style computes the close button's rectangle from title-bar
    metrics and ignores a QSS ``width``/``height``, leaving a roughly 16x9px
    target -- and that button is the panel's only visible affordance for
    dismissing it.

    Lives here rather than in a single dock's module because every dock needs
    the same fix. ``shortcut_hint`` is appended to the close tooltip so each
    panel can advertise its own toggle shortcut.
    """
    bar = QFrame(dock)
    bar.setObjectName(OBJ_HEADER_BAR)
    row = QHBoxLayout(bar)
    row.setContentsMargins(SPACE["md"], SPACE["xs"], SPACE["xs"], SPACE["xs"])
    row.setSpacing(SPACE["sm"])

    label = QLabel(title, bar)
    label.setObjectName(OBJ_DOCK_TITLE)
    row.addWidget(label)
    row.addStretch(1)

    close = QToolButton(bar)
    close.setObjectName(OBJ_GHOST)
    # Opts out of the shared button min-height, which would otherwise beat
    # setFixedSize and inflate this header to 50px.
    # Size comes from the style sheet, not setFixedSize: QStyleSheetStyle
    # recomputes size constraints from QSS and would override it anyway.
    close.setProperty(PROP_COMPACT, True)
    close.setText("\u2715")
    close.setAccessibleName(f"Hide {title}")
    hint = f" ({shortcut_hint})" if shortcut_hint else ""
    close.setToolTip(f"Hide the {title.lower()}{hint}")
    close.clicked.connect(dock.close)
    row.addWidget(close)
    return bar


class _MenuLikeComboBox(QComboBox):
    """A combo box whose popup drops below the field, the way a menu does.

    Every combo box in the application now behaves this way, through the
    application-wide filter :func:`sharpmod.ui.features.gui_common.install_popup_placement`.
    This subclass stays because the behaviour started here and this control cannot
    afford to depend on the filter: it *is* the top bar's source dropdown, the one
    the placement is defined against, and ``SourceSelector`` is constructed
    directly by tests that never install an application filter. Applying it twice
    is harmless -- the placement is the same arithmetic either way.

    A subclass rather than a wholesale move to ``QToolButton`` plus ``QMenu``,
    which would make the relationship identical by construction. That swap is the
    better end state, but ``SourceSelector`` exposes this widget as the tab-bar
    stand-in the picker keys forty call sites and several tests against, so it is
    a separate change from making the popup land in the right place.
    """

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        configure_combo_popup(self)
        super().showPopup()
        place_combo_popup(self)


class SourceSelector(QWidget):
    """A menu-bar dropdown controlling a stack of source panels.

    Emits :attr:`currentChanged` with the new index, matching
    ``QTabWidget.currentChanged`` so existing connections keep working.
    """

    currentChanged = Signal(int)

    def __init__(self, header: str = "Source", parent=None):
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The picker reparents this combo into a menu-bar corner widget. It is
        # intentionally not in the central layout: the stack gets every pixel
        # when the source control moves into the top bar.
        self._header = str(header)
        self._nav = _MenuLikeComboBox(self)
        configure_combo_popup(self._nav)
        self._nav.setAccessibleName(f"{self._header.title()} source")
        self._nav.setToolTip("Choose where to load a sounding from")
        self._nav.setMinimumContentsLength(18)
        self._nav.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._nav.currentIndexChanged.connect(self._on_nav_index_changed)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        #: Titles, index-aligned with the stack. Kept separately so
        #: :meth:`tabText` remains stable while a lazy placeholder is swapped.
        self._titles: list[str] = []

    # -- QTabWidget-compatible surface -------------------------------------- #

    def addTab(self, widget: QWidget, title: str) -> int:
        """Append a panel and its dropdown entry. Returns the new index."""
        return self.insertTab(self.count(), widget, title)

    def insertTab(self, index: int, widget: QWidget, title: str) -> int:
        """Insert a panel and its dropdown entry at ``index``."""
        index = max(0, min(int(index), self.count()))
        self._stack.insertWidget(index, widget)
        self._titles.insert(index, title)

        blocked = self._nav.signalsBlocked()
        self._nav.blockSignals(True)
        try:
            self._nav.insertItem(index, title)
            self._nav.setItemData(index, title, Qt.ToolTipRole)
            if self._nav.currentIndex() < 0:
                self._nav.setCurrentIndex(0)
        finally:
            self._nav.blockSignals(blocked)
        return index

    def removeTab(self, index: int) -> None:
        """Remove the panel and dropdown entry at ``index``.

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
            self._nav.removeItem(index)
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
        if index == self._stack.currentIndex() and self._nav.currentIndex() == index:
            return
        self._stack.setCurrentIndex(index)
        if self._nav.currentIndex() != index:
            blocked = self._nav.signalsBlocked()
            self._nav.blockSignals(True)
            try:
                self._nav.setCurrentIndex(index)
            finally:
                self._nav.blockSignals(blocked)
        self.currentChanged.emit(index)

    def currentWidget(self) -> QWidget | None:
        return self._stack.currentWidget()

    def setTabToolTip(self, index: int, tip: str) -> None:
        if 0 <= index < self._nav.count():
            self._nav.setItemData(index, tip, Qt.ToolTipRole)

    def navigation_widget(self) -> QComboBox:
        """Return the dropdown intended for the picker's top bar."""
        return self._nav

    # -- internals ---------------------------------------------------------- #

    def _on_nav_index_changed(self, index: int) -> None:
        """Mirror a dropdown choice onto the stack and emit a tab change."""
        if index < 0 or index >= self.count():
            return
        if self._stack.currentIndex() != index:
            self._stack.setCurrentIndex(index)
        self.currentChanged.emit(index)


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
        hodo = QRectF(pad * 2 + skew_w, pad, w - (pad * 3 + skew_w), body_h)
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
        qp.setPen(QPen(self._colour("skew_mixr_color", "#006600"), 1, Qt.DashLine))
        qp.drawLine(
            QPointF(r.left() + r.width() * 0.16, r.bottom()),
            QPointF(r.left() + r.width() * 0.30, r.top()),
        )

        def trace(colour: QColor, surface_x: float, width: float) -> None:
            """Draw a profile from ``surface_x`` at the bottom, leaning left."""
            qp.setPen(QPen(colour, width))
            points = []
            steps = 6
            for step in range(steps + 1):
                t = step / steps
                # Cools with height, with a slight kink so it reads as data
                # rather than a straight ruled line.
                x = r.left() + r.width() * (surface_x - 0.34 * t + 0.03 * (step % 2))
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
            qp.drawLine(
                QPointF(r.right() - r.width() * 0.20, y),
                QPointF(r.right() - r.width() * 0.04, y),
            )
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
                points.append(
                    QPointF(
                        centre.x() + rad * math.cos(angle),
                        centre.y() - rad * math.sin(angle),
                    )
                )
            qp.drawPolyline(QPolygonF(points))

        qp.setPen(QPen(fg, 1))
        qp.drawRect(r)

    def _paint_index_strip(self, qp: QPainter, r: QRectF, fg: QColor) -> None:
        # The alert ramp drives every derived-parameter value in the real
        # window, so showing it in order is the most informative strip.
        ramp = (
            "pcl_cin_lo_color",
            "alert_l1_color",
            "alert_l2_color",
            "alert_l4_color",
            "alert_l5_color",
            "alert_l6_color",
        )
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
