"""Flat and Lambert conformal map projection math."""

from __future__ import annotations

import math

#: Widest latitude span a cone can still represent sensibly. Past this a conic
#: either wraps past the pole or degenerates toward a cylinder, so the curved
#: view falls back to flat rather than drawing something wrong.
_CONIC_MAX_LAT_SPAN = 75.0

#: A cone standing on the equator is a cylinder, and its apex runs to infinity.
#: Views centred inside this band fall back to flat.
_CONIC_MIN_ABS_MID_LAT = 4.0

#: Latitudes are clamped inside the poles before projecting: the conic's radius
#: term is undefined at exactly +/-90.
_CONIC_LAT_LIMIT = 89.0


def _conic_constants(lat0: float, lat1: float, lon_ref: float = 0.0):
    """Return ``(n, F, rho0, lon_ref)`` for a Lambert conformal conic.

    ``lon_ref`` is the central meridian the cone is cut along -- the view's own
    centre longitude -- and every longitude is measured as an offset from it.

    Standard parallels are placed a sixth of the way in from each edge of the
    view, which is the usual choice: it splits the scale error across the map
    instead of piling it at one edge.

    Returns ``None`` when the view is one a cone cannot represent -- too tall, or
    centred too near the equator -- so the caller can fall back to a flat view
    rather than draw a distorted one.
    """
    south = max(-_CONIC_LAT_LIMIT, min(_CONIC_LAT_LIMIT, float(lat0)))
    north = max(-_CONIC_LAT_LIMIT, min(_CONIC_LAT_LIMIT, float(lat1)))
    if north <= south:
        return None
    span = north - south
    mid = (south + north) / 2.0
    if span > _CONIC_MAX_LAT_SPAN or abs(mid) < _CONIC_MIN_ABS_MID_LAT:
        return None
    phi1 = math.radians(south + span / 6.0)
    phi2 = math.radians(north - span / 6.0)
    try:
        t1 = math.tan(math.pi / 4.0 + phi1 / 2.0)
        t2 = math.tan(math.pi / 4.0 + phi2 / 2.0)
        if t1 <= 0.0 or t2 <= 0.0:
            return None
        if abs(phi1 - phi2) < 1e-9:
            n = math.sin(phi1)
        else:
            n = math.log(math.cos(phi1) / math.cos(phi2)) / math.log(t2 / t1)
        if not math.isfinite(n) or abs(n) < 1e-6:
            return None
        f = math.cos(phi1) * (t1**n) / n
        if not math.isfinite(f):
            return None
        rho0 = f / (math.tan(math.pi / 4.0 + math.radians(mid) / 2.0) ** n)
        if not math.isfinite(rho0):
            return None
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return n, f, rho0, float(lon_ref)


class _Projection:
    """One resolved map transform: geographic degrees to widget pixels.

    Built once per paint and handed to every draw helper, so a widget's geometry
    is converted through exactly one object and a new projection cannot reach
    half the layers.

    ``affine`` says whether the transform is a pure scale-and-translate from
    geographic degrees.

    ``cone`` is the resolved conic constants, or ``None`` for a flat view. It
    exists so two projections can be compared: the plane-to-pixel step is a
    scale-and-translate for *both* views, so the basemap's gesture preview can
    re-blit a cached raster whenever two transforms share the same cone --
    ``None == None`` for flat, identical constants for a conic. Only the
    degrees-to-plane step differs, and that is what the cone pins down.
    """

    __slots__ = (
        "kind",
        "affine",
        "cone",
        "scale",
        "offx",
        "offy",
        "k",
        "x0",
        "y1",
        "_n",
        "_f",
        "_rho0",
        "_lon_ref",
    )

    def __init__(self, kind, *, scale, offx, offy, k=1.0, x0=0.0, y1=0.0, conic=None):
        self.kind = kind
        self.affine = conic is None
        self.cone = None if conic is None else tuple(conic)
        self.scale = scale
        self.offx = offx
        self.offy = offy
        self.k = k
        self.x0 = x0
        self.y1 = y1
        if conic is None:
            self._n = self._f = self._rho0 = self._lon_ref = 0.0
        else:
            self._n, self._f, self._rho0, self._lon_ref = conic

    # -- flat ------------------------------------------------------------- #
    def _flat_forward(self, lon, lat):
        return (
            self.offx + (lon * self.k - self.x0) * self.scale,
            self.offy + (self.y1 - lat) * self.scale,
        )

    def _flat_inverse(self, x, y):
        lon = ((x - self.offx) / self.scale + self.x0) / self.k
        lat = self.y1 - (y - self.offy) / self.scale
        return lon, lat

    # -- conic ------------------------------------------------------------ #
    def _conic_plane(self, lon, lat):
        n, f = self._n, self._f
        phi = math.radians(max(-_CONIC_LAT_LIMIT, min(_CONIC_LAT_LIMIT, lat)))
        # Longitudes are unwrapped onto the shortest way round from the
        # reference meridian, so a view crossing the antimeridian does not fling
        # its geometry the long way about the cone.
        delta = math.radians(((lon - self._lon_ref + 180.0) % 360.0) - 180.0)
        t = math.tan(math.pi / 4.0 + phi / 2.0)
        if t <= 0.0:
            return 0.0, 0.0
        rho = f / (t**n)
        theta = n * delta
        return rho * math.sin(theta), self._rho0 - rho * math.cos(theta)

    def _conic_forward(self, lon, lat):
        px, py = self._conic_plane(lon, lat)
        return (
            self.offx + (px - self.x0) * self.scale,
            self.offy + (self.y1 - py) * self.scale,
        )

    def _conic_inverse(self, x, y):
        n, f = self._n, self._f
        px = (x - self.offx) / self.scale + self.x0
        py = self.y1 - (y - self.offy) / self.scale
        dy = self._rho0 - py
        rho = math.hypot(px, dy)
        if n < 0.0:
            rho = -rho
        if abs(rho) < 1e-12:
            return self._lon_ref, math.copysign(_CONIC_LAT_LIMIT, n)
        theta = math.atan2(px, dy) if n > 0.0 else math.atan2(-px, -dy)
        lon = self._lon_ref + math.degrees(theta / n)
        try:
            phi = 2.0 * math.atan((f / rho) ** (1.0 / n)) - math.pi / 2.0
        except (ValueError, ZeroDivisionError, OverflowError):
            return lon, 0.0
        return lon, math.degrees(phi)

    # -- public ----------------------------------------------------------- #
    def forward(self, lon, lat):
        if self.affine:
            return self._flat_forward(lon, lat)
        return self._conic_forward(lon, lat)

    def inverse(self, x, y):
        if self.affine:
            return self._flat_inverse(x, y)
        return self._conic_inverse(x, y)
