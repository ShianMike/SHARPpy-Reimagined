"""Possible Hazard Type label widget (Requirement 9.5).

This is the in-workspace successor to SHARPpy's legacy "Possible Hazard Type"
box. It draws, *as text and verbatim*, the exact label produced by the
reformulated :func:`sharpmod.sharptab.hazard.classify` for the analyzed Profile.

Behavior mandated by Requirement 9.5:

* THE Renderer SHALL display, **as text, the exact** Possible Hazard Type label
  produced by the ``Hazard_Classifier`` in the rendered output.

The classifier owns the decision logic (Requirement 9.1-9.4); this widget owns
*only* the presentation and performs **no** transformation of the returned
string -- the label drawn is exactly the value returned by ``classify(prof)``
(one of :data:`sharpmod.sharptab.hazard.HAZARD_LABELS`). No title-casing,
mapping, or abbreviation is applied so the drawn text matches the classifier
output character-for-character; only a display *color* is chosen per label for
legibility (the text itself is unchanged).

Design principle *missing data propagates, never crashes*: when the classifier
returns ``"insufficient data"`` (any required input missing/masked, Requirement
9.4) that exact string is drawn, and any unexpected failure resolving the label
degrades to ``"insufficient data"`` rather than raising.

Headless / import safety
------------------------
Like the other SHARPpy Reimagined viz widgets, this module uses Qt6 via ``qtpy``
(PySide6). Importing the module must not require a running ``QApplication``;
only *instantiating* the widget does. The pure helper
:func:`hazard_label_text` needs no Qt at all and is what the wiring/tests use to
assert the verbatim label. For headless rendering set
``QT_QPA_PLATFORM=offscreen`` before creating the ``QApplication``.
"""

from __future__ import annotations

from qtpy import QtCore, QtGui, QtWidgets

from sharpmod import colors
from sharpmod.sharptab.hazard import HAZARD_LABELS, classify

__all__ = ["plotHazard", "hazard_label_text", "HAZARD_LABEL_COLORS", "TITLE"]


#: The box title, kept identical to the legacy "Possible Hazard Type" box this
#: widget replaces.
TITLE = "Possible Hazard Type"


#: Per-label display color (Requirement 22 palette). The *text* is always the
#: verbatim classifier label; only its color varies for legibility. Unknown
#: labels fall back to the neutral foreground.
HAZARD_LABEL_COLORS = {
    "none": colors.FG_COLOR,
    "marginal": colors.ALERT_L2_COLOR,   # amber
    "tornado": "#ff0000",                 # red
    "supercell": "#ffff00",               # yellow
    "wind": colors.ALERT_L1_COLOR,        # amber
    "hail": "#00ffff",                    # cyan
    "insufficient data": colors.FG_COLOR,
}


def hazard_label_text(prof) -> str:
    """Return the exact ``Hazard_Classifier`` label for ``prof``, verbatim.

    Delegates to :func:`sharpmod.sharptab.hazard.classify` and returns its
    result unmodified (Requirement 9.5). Never raises: any unexpected failure
    degrades to ``"insufficient data"`` (Requirement 9.4).
    """
    try:
        label = classify(prof)
    except Exception:
        return "insufficient data"
    # ``classify`` is contracted to return exactly one member of HAZARD_LABELS;
    # guard defensively so a contract violation never draws arbitrary text.
    if label not in HAZARD_LABELS:
        return "insufficient data"
    return label


class plotHazard(QtWidgets.QFrame):
    """A ``QFrame`` that draws the verbatim Possible Hazard Type label.

    Call :meth:`setProf` with the analyzed Profile; the widget resolves the
    label via :func:`hazard_label_text` (i.e. ``hazard.classify``) and draws it
    verbatim (Requirement 9.5).
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prof = None
        #: The label most recently drawn (verbatim classifier output). Exposed
        #: for wiring/inspection so the exact drawn text can be asserted.
        self.label = None

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

        self.title = TITLE

        # Fonts / metrics.
        self.title_font = QtGui.QFont("Helvetica")
        self.title_font.setPixelSize(11)
        self.title_font.setBold(True)
        self.label_font = QtGui.QFont("Helvetica")
        self.label_font.setPixelSize(18)
        self.label_font.setBold(True)

        self.lpad = 6
        self.rpad = 6
        self.tpad = 4
        self.bpad = 4

        self.setMinimumSize(160, 56)

        # Backing pixmap, sized to the current widget geometry.
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg_color)
        self.plotData()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def setProf(self, prof):
        """Resolve and draw the verbatim hazard label for ``prof`` (Req 9.5)."""
        self.prof = prof
        self.label = hazard_label_text(prof)
        self.clearData()
        self.plotData()
        self.update()

    def setPreferences(self, update_gui: bool = True, **prefs):
        """Apply bg/fg preferences and (optionally) redraw.

        Mirrors the vendored inset ``setPreferences`` contract so this widget
        can be driven by the same config re-apply path as the other panels.
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
        """Draw the title and the verbatim hazard label onto the pixmap."""
        qp = QtGui.QPainter()
        qp.begin(self.plotBitMap)
        qp.setRenderHint(QtGui.QPainter.Antialiasing)
        qp.setClipRect(
            QtCore.QRect(0, 0, self.plotBitMap.width(), self.plotBitMap.height())
        )
        self._drawTitle(qp)
        self._drawLabel(qp)
        qp.end()

    def _drawTitle(self, qp):
        qp.setFont(self.title_font)
        qp.setPen(QtGui.QPen(self.fg_color, 1, QtCore.Qt.SolidLine))
        rect = QtCore.QRect(self.lpad, self.tpad,
                            self.width() - self.lpad - self.rpad, 14)
        qp.drawText(rect, int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
                    self.title)
        y = self.tpad + 16
        qp.drawLine(self.lpad, y, self.width() - self.rpad, y)

    def _drawLabel(self, qp):
        """Draw the exact classifier label verbatim (Requirement 9.5)."""
        if self.label is None:
            return
        color = QtGui.QColor(
            HAZARD_LABEL_COLORS.get(self.label, colors.FG_COLOR)
        )
        qp.setFont(self.label_font)
        qp.setPen(QtGui.QPen(color, 1, QtCore.Qt.SolidLine))
        top = self.tpad + 18
        rect = QtCore.QRect(self.lpad, top,
                            self.width() - self.lpad - self.rpad,
                            max(self.height() - top - self.bpad, 1))
        # The drawn text is the classifier output unchanged (no case/format
        # transformation) so it matches character-for-character.
        qp.drawText(rect, int(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter),
                    self.label)
