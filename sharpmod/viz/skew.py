"""Skew-T viz: the Hail Growth Zone (HGZ) overlay pass.

This module implements the *overlay pass* described in the SHARPpy Reimagined design
("Viz: Hail Growth Zone Overlay"). It is deliberately self-contained: rather
than re-implementing the entire vendored ``sharppy.viz.skew`` SkewT widget, it
provides a small helper -- :class:`HGZOverlay` -- that is given the skew-T plot
geometry plus the widget's own coordinate-transform callable(s) and draws a
shaded/annotated band over the -10 degrees C to -30 degrees C layer.

Behavior (Requirements 19.9, 19.10, 19.11):

* **19.9** -- when ``prof.hgz_cape`` is *available* (not the ``MISSING``
  sentinel), shade and annotate the -10 degrees C to -30 degrees C layer region
  within the skew-T plot area.
* **19.10** -- the shaded band is clipped to the plot area (via
  :meth:`QPainter.setClipRect`) so it never overlaps other skew-T elements
  outside the plotting rectangle, and its vertical extent is clamped to the
  plot rectangle.
* **19.11** -- when ``prof.hgz_cape`` is *missing* (or the -10/-30 degrees C
  isotherms are not resolvable in the profile), no overlay is drawn at all.

Design principle *missing data propagates, never crashes*: every gate that
cannot be satisfied results in the overlay simply not drawing, never an
exception.

Integration
-----------
A real SkewT widget (successor to ``sharppy.viz.skew.SkewT``) already exposes
a plotting rectangle (``self.tlx/self.tly/self.brx/self.bry`` or ``self.lpad``
/ ``self.rpad`` / ``self.tpad`` / ``self.bpad``) and a pressure->pixel
transform ``self.pres_to_pix(p)``. It gains the HGZ overlay by mixing in
:class:`SkewTHGZOverlayMixin` and calling ``self.drawHGZ(qp)`` from its
``drawData`` / ``plotData`` pass, after the temperature/dewpoint traces are
drawn::

    class SkewT(SkewTHGZOverlayMixin, backgroundSkewT):
        def drawData(self, qp):
            ...  # draw isotherms, traces, parcel, etc.
            self.drawHGZ(qp)  # HGZ overlay pass (Req 19.9-19.11)

See :func:`draw_hgz_overlay` for the free-function form used by both the mixin
and by unit tests, which need only geometry + a transform callable and no live
Qt widget.
"""

from __future__ import annotations

from typing import Callable, Optional

from qtpy import QtCore, QtGui
from qtpy.QtCore import QRect, Qt
from qtpy.QtGui import QBrush, QColor, QFont, QPainter, QPen

from ..sharptab import interp
from ..sharptab.constants import is_missing

__all__ = [
    "HGZ_TOP_ISOTHERM",
    "HGZ_BOTTOM_ISOTHERM",
    "HGZ_FILL_COLOR",
    "HGZ_EDGE_COLOR",
    "HGZOverlay",
    "draw_hgz_overlay",
    "SkewTHGZOverlayMixin",
]


# ---------------------------------------------------------------------------
# HGZ layer definition and default styling
# ---------------------------------------------------------------------------

#: Warm bound of the hail-growth zone (degrees C). This is the lower-altitude /
#: higher-pressure edge of the band.
HGZ_TOP_ISOTHERM = -10.0
#: Cold bound of the hail-growth zone (degrees C). Higher-altitude /
#: lower-pressure edge of the band.
HGZ_BOTTOM_ISOTHERM = -30.0

#: Translucent cyan fill for the shaded band. The alpha keeps the underlying
#: isotherms / traces legible while marking the layer.
HGZ_FILL_COLOR = QColor(0, 200, 255, 18)
#: Slightly stronger edge / label color for the band boundaries and annotation.
HGZ_EDGE_COLOR = QColor(0, 200, 255, 100)

#: Annotation text drawn alongside the band.
_HGZ_LABEL = "HGZ"


# ---------------------------------------------------------------------------
# Core overlay helper
# ---------------------------------------------------------------------------

class HGZOverlay:
    """Draws the hail-growth-zone band onto a skew-T plot.

    The overlay is intentionally decoupled from any concrete widget: it is
    constructed from the plot geometry and the skew-T pressure->pixel
    transform, so it can be exercised headlessly (and unit-tested) with a
    plain lambda transform and no live Qt widget.

    Parameters
    ----------
    plot_rect:
        The skew-T plotting rectangle in widget pixels (a ``QRect``). Both the
        horizontal extent of the shaded band and the clip region are derived
        from it, satisfying the "within the plot area" / "clipped to the plot
        area" requirements (19.9, 19.10).
    pres_to_pix:
        Callable mapping a pressure (hPa) to a vertical pixel coordinate ``y``
        in the same coordinate space as ``plot_rect`` -- i.e. the SkewT
        widget's own ``pres_to_pix`` method.
    fill_color, edge_color:
        Optional style overrides for the band fill and its boundary/label.
    """

    def __init__(
        self,
        plot_rect: QRect,
        pres_to_pix: Callable[[float], float],
        fill_color: Optional[QColor] = None,
        edge_color: Optional[QColor] = None,
    ) -> None:
        self.plot_rect = QRect(plot_rect)
        self.pres_to_pix = pres_to_pix
        self.fill_color = QColor(fill_color) if fill_color is not None else QColor(HGZ_FILL_COLOR)
        self.edge_color = QColor(edge_color) if edge_color is not None else QColor(HGZ_EDGE_COLOR)

    # -- geometry ---------------------------------------------------------

    def band_pixels(self, prof) -> Optional[tuple]:
        """Return the clamped ``(y_top, y_bottom)`` pixel band for ``prof``.

        Resolves the -10 degrees C and -30 degrees C isotherm pressures from
        the profile, maps them to pixels, and clamps the resulting band to the
        plot rectangle (Requirement 19.10).

        Returns ``None`` -- meaning *draw nothing* -- when the HGZ CAPE is
        missing (19.11), when either isotherm is not resolvable in the profile,
        or when the resulting band has no visible extent inside the plot area.
        """
        # 19.11: no HGZ CAPE -> no overlay.
        if prof is None or is_missing(getattr(prof, "hgz_cape", None)):
            return None

        p_warm = interp.pres_at_isotherm(prof, HGZ_TOP_ISOTHERM)
        p_cold = interp.pres_at_isotherm(prof, HGZ_BOTTOM_ISOTHERM)
        if is_missing(p_warm) or is_missing(p_cold):
            # The profile does not span the -10 / -30 degrees C layer.
            return None

        try:
            y_warm = float(self.pres_to_pix(float(p_warm)))
            y_cold = float(self.pres_to_pix(float(p_cold)))
        except (TypeError, ValueError):
            return None

        if not (y_warm == y_warm and y_cold == y_cold):  # NaN guard
            return None

        y_top = min(y_warm, y_cold)
        y_bottom = max(y_warm, y_cold)

        # 19.10: clamp the band to the plot rectangle's vertical extent.
        top = max(y_top, float(self.plot_rect.top()))
        bottom = min(y_bottom, float(self.plot_rect.bottom()))
        if bottom <= top:
            # Band lies entirely outside the plot area (or is degenerate).
            return None
        return top, bottom

    # -- drawing ----------------------------------------------------------

    def draw(self, qp: QPainter, prof) -> bool:
        """Draw the HGZ overlay for ``prof`` onto ``qp``.

        Returns ``True`` when an overlay was drawn, ``False`` when nothing was
        drawn (missing HGZ CAPE, unresolved isotherms, or an off-screen band).
        The painter's clip region and pen/brush are saved and restored so the
        overlay never leaks state into the rest of the skew-T pass.
        """
        band = self.band_pixels(prof)
        if band is None:
            return False
        top, bottom = band

        left = self.plot_rect.left()
        width = self.plot_rect.width()
        band_rect = QtCore.QRectF(
            float(left),
            top,
            float(width),
            bottom - top,
        )

        qp.save()
        try:
            # 19.10: clip everything this pass draws to the plot rectangle.
            qp.setClipRect(QtCore.QRectF(self.plot_rect))

            # Shaded fill across the full plot width.
            qp.setPen(Qt.NoPen)
            qp.setBrush(QBrush(self.fill_color))
            qp.drawRect(band_rect)

            # Boundary lines at the -10 / -30 degrees C isotherms.
            pen = QPen(self.edge_color)
            pen.setWidthF(1.0)
            pen.setStyle(Qt.DashLine)
            qp.setPen(pen)
            qp.setBrush(Qt.NoBrush)
            qp.drawLine(
                QtCore.QPointF(float(left), top),
                QtCore.QPointF(float(left + width), top),
            )
            qp.drawLine(
                QtCore.QPointF(float(left), bottom),
                QtCore.QPointF(float(left + width), bottom),
            )

            # Annotation. Kept inside the clip region (19.10) near the band top.
            self._draw_label(qp, band_rect)
        finally:
            qp.restore()
        return True

    def _draw_label(self, qp: QPainter, band_rect) -> None:
        """Annotate the band with the ``HGZ`` label, inside the clip region."""
        font = QFont("Helvetica")
        font.setBold(True)
        qp.setFont(font)
        pen = QPen(self.edge_color)
        qp.setPen(pen)
        # Anchor the label just inside the left edge at the top of the band.
        text_rect = QtCore.QRectF(
            band_rect.left() + 4.0,
            band_rect.top() + 2.0,
            max(band_rect.width() - 8.0, 1.0),
            max(band_rect.height() - 4.0, 1.0),
        )
        qp.drawText(text_rect, int(Qt.AlignLeft | Qt.AlignTop), _HGZ_LABEL)


# ---------------------------------------------------------------------------
# Free-function form (used by the mixin and by tests)
# ---------------------------------------------------------------------------

def draw_hgz_overlay(
    qp: QPainter,
    prof,
    plot_rect: QRect,
    pres_to_pix: Callable[[float], float],
    fill_color: Optional[QColor] = None,
    edge_color: Optional[QColor] = None,
) -> bool:
    """Convenience wrapper: build an :class:`HGZOverlay` and draw it once.

    Returns ``True`` if an overlay was drawn (see :meth:`HGZOverlay.draw`).
    """
    overlay = HGZOverlay(
        plot_rect,
        pres_to_pix,
        fill_color=fill_color,
        edge_color=edge_color,
    )
    return overlay.draw(qp, prof)


# ---------------------------------------------------------------------------
# Mixin that wires the overlay into a SkewT widget
# ---------------------------------------------------------------------------

class SkewTHGZOverlayMixin:
    """Adds the HGZ overlay pass to a SkewT-style widget.

    A SkewT widget mixes this in and calls ``self.drawHGZ(qp)`` from its data
    pass. The mixin reads the plot rectangle and pressure->pixel transform the
    widget already provides:

    * ``self.pres_to_pix(p)`` -- pressure (hPa) -> vertical pixel.
    * the plot rectangle, resolved from ``self.tlx/tly/brx/bry`` when present,
      otherwise from the ``lpad/rpad/tpad/bpad`` padding and the widget size.
    * ``self.prof`` -- the analyzed :class:`Profile` (may be ``None`` before a
      sounding is loaded, in which case nothing is drawn).
    """

    def _hgz_plot_rect(self) -> Optional[QRect]:
        """Resolve the skew-T plotting rectangle from the host widget."""
        tlx = getattr(self, "tlx", None)
        tly = getattr(self, "tly", None)
        brx = getattr(self, "brx", None)
        bry = getattr(self, "bry", None)
        if None not in (tlx, tly, brx, bry):
            return QRect(int(tlx), int(tly), int(brx - tlx), int(bry - tly))

        # Fall back to padding + widget size.
        width_fn = getattr(self, "width", None)
        height_fn = getattr(self, "height", None)
        if width_fn is None or height_fn is None:
            return None
        lpad = int(getattr(self, "lpad", 0))
        rpad = int(getattr(self, "rpad", 0))
        tpad = int(getattr(self, "tpad", 0))
        bpad = int(getattr(self, "bpad", 0))
        wid = int(width_fn())
        hgt = int(height_fn())
        return QRect(lpad, tpad, max(wid - lpad - rpad, 0), max(hgt - tpad - bpad, 0))

    def drawHGZ(self, qp: QPainter) -> bool:
        """Draw the hail-growth-zone overlay for the widget's current profile.

        No-ops (returns ``False``) when there is no profile, no plot rectangle,
        or no ``pres_to_pix`` transform, or when ``prof.hgz_cape`` is missing.
        """
        prof = getattr(self, "prof", None)
        if prof is None:
            return False
        pres_to_pix = getattr(self, "pres_to_pix", None)
        if not callable(pres_to_pix):
            return False
        plot_rect = self._hgz_plot_rect()
        if plot_rect is None or plot_rect.width() <= 0 or plot_rect.height() <= 0:
            return False
        return draw_hgz_overlay(qp, prof, plot_rect, pres_to_pix)
