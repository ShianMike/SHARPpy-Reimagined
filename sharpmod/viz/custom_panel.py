"""User-configurable Custom_Panel occupying the SARS slot (Requirement 10).

This module renders a small, user-configurable panel in the display slot the
legacy renderer used for the SARS (Sounding Analog Retrieval System) inset. It
exposes a single Qt widget, :class:`CustomPanel`, a ``QFrame`` that draws an
ordered list of configured items, each as a ``label + value + unit`` row.

Behavior mandated by Requirement 10:

* **10.1** -- given an ordered configuration of up to :data:`CustomPanel.MAX_ITEMS`
  (12) parameters/insets, the panel renders those items *in the configured
  order* within the SARS slot.
* **10.2** -- a configured named Profile parameter is rendered as its current
  value together with its label and unit for the analyzed Profile.
* **10.3** -- a configured name that does not resolve to an available Profile
  parameter renders an *unresolved-parameter indicator* (:data:`UNRESOLVED_STR`)
  in that item's position; all remaining resolved items are still rendered (the
  unresolved item is never dropped silently).
* **10.4** -- with no configuration (``configure(None)`` or never configured),
  the panel defaults to the SARS inset content as its default content set.
* **10.5 / 10.7** -- all content is fit/clipped to the panel's allocated slot;
  overflowing content is clipped to the slot boundaries rather than expanding
  the slot or overlapping adjacent panels.
* **10.6** -- a resolved parameter that has no computed value for the analyzed
  Profile (its value is the ``MISSING`` masked constant / ``None`` / non-finite)
  renders a missing-value indicator (:data:`MISSING_STR`) in place of the value.
* **10.8** -- a configuration specifying more than 12 items renders only the
  first 12 items plus a "more items not shown" indicator.

Configuration model
--------------------
A configuration is a list of :class:`PanelItem` (or plain strings, which are
coerced to ``PanelItem(param=<string>)``). Each item names a Profile attribute
to display; an optional ``label``/``unit`` overrides the label/unit resolved
from the derived-parameter registry (:data:`sharpmod.sharptab.constants.PARAM_REGISTRY`).

Default (SARS) content
----------------------
The real analog-based SARS inset (``viz/analogues.py``) is wired in by a later
renderer task. Until then, an unconfigured Custom_Panel draws the SARS default
content set as a labeled SARS placeholder so the slot is never blank and the
"defaults to SARS" contract (Requirement 10.4) is observable. When the SARS
widget exists, :meth:`CustomPanel.setSarsContent` lets the renderer supply the
default content lines.

Headless / import safety
------------------------
Like the other vendored viz widgets, this module uses Qt6 via ``qtpy``
(PySide6). Importing the module must not require a running ``QApplication``;
only *instantiating* the widget does. For headless rendering, set
``QT_QPA_PLATFORM=offscreen`` before creating a ``QApplication``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Union

from qtpy import QtGui, QtCore, QtWidgets

from sharpmod import colors
from sharpmod.sharptab.constants import is_missing, PARAM_REGISTRY
from sharpmod.viz.unit_text import draw_text_with_smaller_unit

__all__ = ["CustomPanel", "PanelItem", "MISSING_STR", "UNRESOLVED_STR"]


#: Indicator drawn in place of a resolved-but-missing value (Requirement 10.6).
MISSING_STR = colors.MISSING_STR
#: Indicator drawn in place of an unresolved parameter name (Requirement 10.3).
UNRESOLVED_STR = "??"


@dataclass(frozen=True)
class PanelItem:
    """A single configured Custom_Panel item.

    Attributes
    ----------
    param:
        The ``Profile`` attribute name to display (e.g. ``"dcp"``).
    label:
        Optional display label; when omitted the label is resolved from the
        derived-parameter registry, falling back to ``param``.
    unit:
        Optional display unit; when omitted the unit is resolved from the
        derived-parameter registry.
    """

    param: str
    label: Optional[str] = None
    unit: Optional[str] = None


# A configuration entry may be a PanelItem or a bare parameter-name string.
ConfigEntry = Union[PanelItem, str]


class CustomPanel(QtWidgets.QFrame):
    """A ``QFrame`` that renders a user-configurable list of Profile items.

    Call :meth:`configure` with an ordered list of items (or ``None`` for the
    SARS default), then :meth:`setProf` with the analyzed Profile to resolve and
    draw each item's value.
    """

    #: Maximum number of configured items rendered (Requirements 10.1, 10.8).
    MAX_ITEMS = 12

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prof = None
        #: The full configured item list (may exceed MAX_ITEMS); ``None`` means
        #: "unconfigured" -> SARS default content (Requirement 10.4).
        self._items: Optional[list[PanelItem]] = None
        #: Default SARS content lines drawn when unconfigured. Populated by the
        #: renderer via :meth:`setSarsContent` once the SARS widget exists.
        self._sars_lines: list[str] = [
            "SARS",
            "Sounding Analog",
            "Retrieval System",
            "(default content)",
        ]

        # Palette (documented modernized color scheme, Requirement 22).
        self.bg_color = QtGui.QColor(colors.BG_COLOR)
        self.fg_color = QtGui.QColor(colors.FG_COLOR)
        self.unresolved_color = QtGui.QColor(colors.ALERT_L2_COLOR)

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

        self.title = "Custom"

        # Fonts / metrics.
        self.title_font = QtGui.QFont("Helvetica")
        self.title_font.setPixelSize(11)
        self.title_font.setBold(True)
        self.label_font = QtGui.QFont("Helvetica")
        self.label_font.setPixelSize(11)
        self.value_font = QtGui.QFont("Helvetica")
        self.value_font.setPixelSize(11)
        self.value_font.setBold(True)
        self.note_font = QtGui.QFont("Helvetica")
        self.note_font.setPixelSize(9)
        self.note_font.setItalic(True)

        self.setMinimumSize(120, 80)

        # Backing pixmap, sized to the current widget geometry.
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg_color)
        self._setupGeometry()
        self.plotData()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def configure(self, items: Optional[Sequence[ConfigEntry]]) -> None:
        """Set the panel configuration (Requirements 10.1, 10.4, 10.8).

        ``items`` is an ordered sequence of :class:`PanelItem` or bare parameter
        name strings. Passing ``None`` (or never calling this method) selects the
        SARS default content set. The full list is retained; rendering shows only
        the first :data:`MAX_ITEMS` plus a "more items not shown" indicator when
        the configuration is longer.
        """
        if items is None:
            self._items = None
        else:
            self._items = [self._coerce_item(entry) for entry in items]
        self.clearData()
        self.plotData()
        self.update()

    def setSarsContent(self, lines: Sequence[str]) -> None:
        """Supply the default SARS content lines drawn when unconfigured.

        The renderer calls this once the analog-based SARS inset is available so
        the unconfigured panel reflects real SARS content (Requirement 10.4).
        """
        self._sars_lines = [str(line) for line in lines]
        if self._items is None:
            self.clearData()
            self.plotData()
            self.update()

    def setProf(self, prof) -> None:
        """Attach the analyzed Profile and redraw (Requirement 10.2)."""
        self.prof = prof
        self.clearData()
        self.plotData()
        self.update()

    def setPreferences(self, update_gui: bool = True, **prefs) -> None:
        """Apply the Color Scheme and (optionally) redraw (Requirement 22.4).

        Mirrors the vendored inset ``setPreferences`` contract so the panel is
        driven by the same Color-Scheme re-apply path as the other panels. The
        modernized amber alert tier (``alert_l2_color``) is used for the
        unresolved-parameter indicator; on redraw every value is re-resolved
        from the *current* Profile (Requirement 22.5).
        """
        if "bg_color" in prefs:
            self.bg_color = QtGui.QColor(prefs["bg_color"])
        if "fg_color" in prefs:
            self.fg_color = QtGui.QColor(prefs["fg_color"])
        if "alert_l2_color" in prefs:
            self.unresolved_color = QtGui.QColor(prefs["alert_l2_color"])
        if update_gui:
            self.clearData()
            self.plotData()
            self.update()

    def clearData(self) -> None:
        """Reset the backing pixmap to the background color."""
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg_color)

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _coerce_item(entry: ConfigEntry) -> PanelItem:
        """Normalize a configuration entry to a :class:`PanelItem`."""
        if isinstance(entry, PanelItem):
            return entry
        return PanelItem(param=str(entry))

    def visible_items(self) -> list[PanelItem]:
        """Return the items actually rendered: the first :data:`MAX_ITEMS`.

        Requirement 10.8: configurations longer than 12 items render only the
        first 12 (the overflow indicator is drawn separately).
        """
        if not self._items:
            return []
        return self._items[: self.MAX_ITEMS]

    def has_overflow(self) -> bool:
        """True when the configuration exceeds :data:`MAX_ITEMS` (Req 10.8)."""
        return bool(self._items) and len(self._items) > self.MAX_ITEMS

    def _resolve_label_unit(self, item: PanelItem) -> tuple[str, str]:
        """Resolve an item's display label and unit.

        Overrides on the item win; otherwise fall back to the derived-parameter
        registry, then to the raw parameter name / empty unit.
        """
        spec = PARAM_REGISTRY.get(item.param)
        label = item.label
        if label is None:
            label = spec.label if spec is not None else item.param
        unit = item.unit
        if unit is None:
            unit = spec.output_units if spec is not None else ""
        # "unitless" (and blanks) are shown as no unit.
        if unit is None or unit.strip().lower() in ("", "unitless", "none"):
            unit = ""
        return label, unit

    def _resolve_value(self, item: PanelItem) -> tuple[str, object]:
        """Resolve an item's value against the current Profile.

        Returns a ``(status, raw)`` tuple where ``status`` is one of:

        * ``"ok"``          -- resolved to a finite value (``raw`` is that value);
        * ``"missing"``     -- resolved but the value is ``MISSING``/masked
          (Requirement 10.6);
        * ``"unresolved"``  -- the parameter name does not resolve to an available
          Profile attribute (Requirement 10.3).
        """
        prof = self.prof
        if prof is None:
            # No Profile yet: the name may be valid, but there is no value to
            # show, so treat it as missing rather than unresolved.
            return "missing", None
        sentinel = object()
        try:
            raw = getattr(prof, item.param, sentinel)
        except Exception:
            # A lazy __getattr__ that raises for unknown names -> unresolved.
            raw = sentinel
        if raw is sentinel:
            return "unresolved", None
        # ``MISSING``/masked (Requirement 10.6). ``None`` and non-finite floats
        # are also treated as "no computed value": the SharpTab MISSING sentinel
        # is masked, but guard defensively so a stray None/NaN never renders as
        # literal text.
        if raw is None or is_missing(raw) or not self._is_finite(raw):
            return "missing", None
        return "ok", raw

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    def _setupGeometry(self) -> None:
        """Compute padding / row metrics from the current widget size."""
        self.lpad = 8
        self.rpad = 8
        self.tpad = 4
        self.bpad = 4
        self.title_h = 16
        self.row_h = 15

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
        # (Requirements 10.5, 10.7).
        qp.setClipRect(self.rect())
        qp.drawPixmap(0, 0, self.plotBitMap)
        qp.end()

    def plotData(self) -> None:
        """Draw the panel onto the backing pixmap."""
        qp = QtGui.QPainter()
        qp.begin(self.plotBitMap)
        qp.setRenderHint(QtGui.QPainter.Antialiasing)
        # Clip every draw to the pixmap bounds so overflow is truncated to the
        # slot rather than spilling over (Requirements 10.5, 10.7).
        qp.setClipRect(
            QtCore.QRect(0, 0, self.plotBitMap.width(), self.plotBitMap.height())
        )
        if self._items is None:
            self._drawTitle(qp, self.title)
            self._drawSarsDefault(qp)
        else:
            self._drawTitle(qp, self.title)
            self._drawItems(qp)
        qp.end()

    def _drawTitle(self, qp, text: str) -> None:
        qp.setFont(self.title_font)
        qp.setPen(QtGui.QPen(self.fg_color, 1, QtCore.Qt.SolidLine))
        rect = QtCore.QRect(
            self.lpad, self.tpad, self.width() - self.lpad - self.rpad, self.title_h
        )
        qp.drawText(rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, text)

    def _drawSarsDefault(self, qp) -> None:
        """Draw the SARS default content set when unconfigured (Req 10.4)."""
        qp.setFont(self.label_font)
        qp.setPen(QtGui.QPen(QtGui.QColor(colors.SARS_NONTOR_MATCH), 1, QtCore.Qt.SolidLine))
        y = self.tpad + self.title_h + 2
        w = self.width() - self.lpad - self.rpad
        for line in self._sars_lines:
            rect = QtCore.QRect(self.lpad, y, w, self.row_h)
            qp.drawText(rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, line)
            y += self.row_h

    def _drawItems(self, qp) -> None:
        """Draw the configured items in order (Requirements 10.1, 10.2)."""
        items = self.visible_items()
        w = self.width()
        left = self.lpad
        right = w - self.rpad
        y = self.tpad + self.title_h + 2

        for item in items:
            label, unit = self._resolve_label_unit(item)
            status, raw = self._resolve_value(item)

            # Label on the left.
            qp.setFont(self.label_font)
            qp.setPen(QtGui.QPen(self.fg_color, 1, QtCore.Qt.SolidLine))
            label_rect = QtCore.QRect(left, y, (right - left) // 2, self.row_h)
            qp.drawText(
                label_rect,
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                self._elide(label, label_rect.width(), self.label_font),
            )

            # Value (+ unit) on the right.
            if status == "unresolved":
                value_text = UNRESOLVED_STR
                value_color = self.unresolved_color
            elif status == "missing":
                value_text = MISSING_STR
                value_color = self.fg_color
            else:
                value_text = self._fmt_value(raw, param=item.param)
                if unit:
                    value_text = f"{value_text} {unit}"
                value_color = self.fg_color

            qp.setFont(self.value_font)
            qp.setPen(QtGui.QPen(value_color, 1, QtCore.Qt.SolidLine))
            value_rect = QtCore.QRect(
                left + (right - left) // 2, y, (right - left) // 2, self.row_h
            )
            if not draw_text_with_smaller_unit(
                    qp, value_rect, value_text, QtCore.Qt.AlignRight):
                display_text = self._elide(
                    value_text, value_rect.width(), self.value_font)
                qp.drawText(
                    value_rect,
                    QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                    display_text,
                )
            y += self.row_h

        # "more items not shown" indicator (Requirement 10.8).
        if self.has_overflow():
            hidden = len(self._items) - self.MAX_ITEMS
            qp.setFont(self.note_font)
            qp.setPen(QtGui.QPen(self.unresolved_color, 1, QtCore.Qt.SolidLine))
            note_rect = QtCore.QRect(left, y, right - left, self.row_h)
            qp.drawText(
                note_rect,
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                f"+{hidden} more items not shown",
            )

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _is_finite(value) -> bool:
        """True when ``value`` coerces to a finite float.

        Non-numeric values (e.g. strings) are considered finite so they can be
        displayed as-is; only numeric NaN/inf are treated as non-finite.
        """
        try:
            f = float(value)
        except (TypeError, ValueError):
            return True
        return not (f != f or f in (float("inf"), float("-inf")))

    @staticmethod
    def _fmt_value(value, *, param: Optional[str] = None) -> str:
        """Format a resolved parameter value compactly.

        A 2-element numeric vector (e.g. the SFC-500 m mean wind ``(u, v)`` in
        knots) is rendered as its magnitude/speed so a wind quantity shows a
        single readable number rather than a raw tuple.
        """
        if isinstance(value, (tuple, list)) and len(value) == 2:
            try:
                import math
                value = math.hypot(float(value[0]), float(value[1]))
            except (TypeError, ValueError):
                return str(value)
        try:
            f = float(value)
        except (TypeError, ValueError):
            return str(value)
        if f != f or f in (float("inf"), float("-inf")):
            return MISSING_STR
        if param == "vgp":
            return f"{f:.2f}"
        if abs(f) >= 1000:
            return f"{f:.0f}"
        if f == int(f):
            return str(int(f))
        return f"{f:.1f}"

    @staticmethod
    def _elide(text: str, width: int, font: QtGui.QFont) -> str:
        """Elide ``text`` to ``width`` px so a long row stays within the slot."""
        metrics = QtGui.QFontMetrics(font)
        return metrics.elidedText(text, QtCore.Qt.ElideRight, max(0, width))
