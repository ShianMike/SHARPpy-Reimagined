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

import numpy as np
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
    "CAPE_FILL_COLOR",
    "CIN_FILL_COLOR",
    "draw_cape_fill",
    "parcel_level_markers",
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
# CAPE / CIN buoyancy-area fill styling
# ---------------------------------------------------------------------------

#: Translucent orange for positive buoyancy (CAPE) -- the area where the
#: parcel's virtual temperature exceeds the environment's. The alpha keeps the
#: temperature/parcel traces legible through the shading.
CAPE_FILL_COLOR = QColor(255, 130, 0, 80)
#: Translucent blue for negative buoyancy (CIN) -- the area where the parcel is
#: colder than the environment.
CIN_FILL_COLOR = QColor(30, 120, 255, 70)


def parcel_level_markers(pcl):
    """Return the standard parcel-level labels and pressures, including MPL."""
    if pcl is None:
        return []
    return [
        ("LCL", getattr(pcl, "lclpres", None)),
        ("LFC", getattr(pcl, "lfcpres", None)),
        ("EL", getattr(pcl, "elpres", None)),
        ("MPL", getattr(pcl, "mplpres", None)),
    ]


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
        draw_fill: bool = True,
    ) -> None:
        self.plot_rect = QRect(plot_rect)
        self.pres_to_pix = pres_to_pix
        self.fill_color = QColor(fill_color) if fill_color is not None else QColor(HGZ_FILL_COLOR)
        self.edge_color = QColor(edge_color) if edge_color is not None else QColor(HGZ_EDGE_COLOR)
        #: When ``False`` the translucent band fill is skipped and only the
        #: boundary isotherm lines + ``HGZ`` label are drawn. Used when the
        #: CAPE/CIN buoyancy fill provides the primary shading and a second
        #: translucent band over the same region would muddy it.
        self.draw_fill = bool(draw_fill)

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

            # Shaded fill across the full plot width (skipped when the CAPE/CIN
            # buoyancy fill is the primary shading -- boundaries/label stay).
            if self.draw_fill:
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
    draw_fill: bool = True,
) -> bool:
    """Convenience wrapper: build an :class:`HGZOverlay` and draw it once.

    Returns ``True`` if an overlay was drawn (see :meth:`HGZOverlay.draw`).
    """
    overlay = HGZOverlay(
        plot_rect,
        pres_to_pix,
        fill_color=fill_color,
        edge_color=edge_color,
        draw_fill=draw_fill,
    )
    return overlay.draw(qp, prof)


# ---------------------------------------------------------------------------
# CAPE / CIN buoyancy-area fill
# ---------------------------------------------------------------------------

def _finite_pair(values, pressures):
    """Return ``(values, pressures)`` as aligned finite float arrays or ``None``.

    Masked/NaN entries (and any level missing either coordinate) are dropped.
    Returns ``None`` when fewer than two usable levels remain.
    """
    if values is None or pressures is None:
        return None
    v = np.ma.filled(np.ma.masked_invalid(np.ma.asarray(values, dtype=float)), np.nan)
    p = np.ma.filled(np.ma.masked_invalid(np.ma.asarray(pressures, dtype=float)), np.nan)
    v = np.ravel(v)
    p = np.ravel(p)
    if v.shape != p.shape or v.size < 2:
        return None
    keep = np.isfinite(v) & np.isfinite(p)
    if np.count_nonzero(keep) < 2:
        return None
    return v[keep], p[keep]


def _fill_poly(qp, color, points):
    poly = QtGui.QPolygonF([QtCore.QPointF(float(px), float(py)) for px, py in points])
    qp.setBrush(QBrush(color))
    qp.drawPolygon(poly)


def draw_cape_fill(
    qp: QPainter,
    parcel_tv,
    parcel_p,
    env_p,
    env_tv,
    plot_rect: QRect,
    to_x: Callable,
    to_y: Callable,
    pos_color: Optional[QColor] = None,
    neg_color: Optional[QColor] = None,
) -> bool:
    """Shade the buoyancy area between the parcel and environment traces.

    The area between the parcel's virtual-temperature trace
    (``parcel_tv`` at ``parcel_p``) and the environment's virtual-temperature
    trace (``env_tv`` at ``env_p``) is filled per level-segment:

    * **orange** (``pos_color`` / :data:`CAPE_FILL_COLOR`) where the parcel is
      *warmer* than the environment -- positive buoyancy (CAPE), and
    * **blue** (``neg_color`` / :data:`CIN_FILL_COLOR`) where the parcel is
      *colder* -- negative buoyancy (CIN).

    ``to_x(t, p)`` maps a (temperature C, pressure hPa) pair to an x pixel and
    ``to_y(p)`` maps a pressure to a y pixel, both in ``plot_rect``'s coordinate
    space (the SkewT widget's own composed transforms). Everything is clipped to
    ``plot_rect``. Segments straddling the parcel/environment crossing (LFC, EL)
    are split at the crossing so the orange/blue boundary lands exactly there.

    Returns ``True`` when at least one area was filled, ``False`` otherwise
    (missing/degenerate traces, or no vertical overlap). Never raises on bad
    geometry beyond what the painter itself would.
    """
    pos_color = QColor(pos_color) if pos_color is not None else QColor(CAPE_FILL_COLOR)
    neg_color = QColor(neg_color) if neg_color is not None else QColor(CIN_FILL_COLOR)

    parcel = _finite_pair(parcel_tv, parcel_p)
    env = _finite_pair(env_tv, env_p)
    if parcel is None or env is None:
        return False
    p_tv, p_p = parcel
    e_tv, e_p = env

    # Interpolate the environment Tv onto the parcel's pressure levels. np.interp
    # requires ascending sample coordinates; mark out-of-range as NaN so segments
    # outside the sounding are skipped rather than clamped.
    order = np.argsort(e_p)
    env_on_parcel = np.interp(
        p_p, e_p[order], e_tv[order], left=np.nan, right=np.nan
    )
    valid = np.isfinite(env_on_parcel)
    if np.count_nonzero(valid) < 2:
        return False

    dt = p_tv - env_on_parcel  # >0 -> parcel warmer -> CAPE; <0 -> CIN
    x_par = np.asarray(to_x(p_tv, p_p), dtype=float)
    x_env = np.asarray(to_x(env_on_parcel, p_p), dtype=float)
    y = np.asarray(to_y(p_p), dtype=float)

    drew = False
    qp.save()
    try:
        qp.setClipRect(QtCore.QRectF(plot_rect))
        qp.setPen(Qt.NoPen)
        for k in range(len(p_p) - 1):
            if not (valid[k] and valid[k + 1]):
                continue
            d0 = float(dt[k])
            d1 = float(dt[k + 1])
            if d0 == 0.0 and d1 == 0.0:
                continue
            if d0 * d1 >= 0.0:
                # Whole segment has one sign (treat a lone zero endpoint with
                # its neighbour's sign).
                color = pos_color if (d0 + d1) > 0.0 else neg_color
                _fill_poly(qp, color, [
                    (x_par[k], y[k]),
                    (x_par[k + 1], y[k + 1]),
                    (x_env[k + 1], y[k + 1]),
                    (x_env[k], y[k]),
                ])
                drew = True
            else:
                # Sign change within the segment: split at the crossing, where
                # the parcel and environment traces meet (dt == 0).
                f = d0 / (d0 - d1)
                ym = y[k] + f * (y[k + 1] - y[k])
                xm = x_par[k] + f * (x_par[k + 1] - x_par[k])
                _fill_poly(qp, pos_color if d0 > 0 else neg_color, [
                    (x_par[k], y[k]),
                    (xm, ym),
                    (x_env[k], y[k]),
                ])
                _fill_poly(qp, pos_color if d1 > 0 else neg_color, [
                    (xm, ym),
                    (x_par[k + 1], y[k + 1]),
                    (x_env[k + 1], y[k + 1]),
                ])
                drew = True
    finally:
        qp.restore()
    return drew


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
