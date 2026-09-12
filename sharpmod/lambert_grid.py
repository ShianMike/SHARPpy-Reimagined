"""Lambert conformal conic model grids, in pure Python.

NOAA's mesoscale models -- HRRR, RAP, the NAM nests -- are computed on Lambert
conformal conic grids. A grid is a *rectangle in the projection plane*, so its
geographic boundary is curved, and the curvature is not small: HRRR's southern
edge reaches 24.36N over Kansas and only 21.14N at its own corners, a spread of
3.2 degrees. Describing such a grid with a longitude/latitude box is therefore
wrong by hundreds of kilometres at the corners, and it is wrong in a way that is
visible: drawn on a flat map the box is a rectangle while the model data inside
it is a fan, and drawn on a conic map the box bows while the data straightens.
The two never agree, which reads as though the projections had been swapped.

This module exists so the boundary can be drawn as what it is.

**Pure ``math``, no NumPy and no pyproj.** :mod:`sharpmod.tools.model_extract`
imports this to publish a model's real perimeter, and that module is reachable
from the picker's import path, which is held free of NumPy so the interface still
opens promptly. The transform is textbook spherical Lambert conformal conic;
:mod:`sharpmod.hrrr_field` keeps using pyproj for the bulk field reprojection,
where NumPy is already loaded and vectorised work is what matters.

Verified against pyproj at HRRR's four grid corners to 1e-13 degrees, and the
south-west corner reproduces the ``21.138123N 237.280472E`` that HRRR's own GRIB
header publishes as its first grid point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "LambertConformalGrid",
    "HRRR_GRID",
]

#: Latitude the transform clamps to, so a pole cannot divide by zero. The models
#: this describes are mid-latitude, so this is a guard and never a limit reached
#: in normal use.
_LAT_LIMIT = 89.9


@dataclass(frozen=True)
class LambertConformalGrid:
    """One Lambert conformal conic grid, described the way GRIB describes it.

    ``x0``/``y0`` are the projection-plane coordinates of the *first* grid point
    (the south-west corner) in metres, and ``shape`` is ``(rows, columns)``, so
    the grid spans ``(columns - 1) * spacing`` east and ``(rows - 1) * spacing``
    north of that origin -- point counts, not cell counts, because that is what
    the header gives and an off-by-one here is a 3 km error in the outline.
    """

    #: Latitude of the projection origin, degrees.
    lat_origin: float
    #: Central meridian, degrees east, normalised to -180..180 on use.
    lon_origin: float
    #: The two standard parallels. Equal values mean a tangent cone.
    lat_1: float
    lat_2: float
    #: Sphere radius in metres, as the model's own projection declares it.
    radius: float
    #: ``(rows, columns)`` grid point counts.
    shape: tuple[int, int]
    #: Grid spacing in metres.
    spacing: float
    #: Projection-plane coordinates of the first grid point, metres.
    x0: float
    y0: float

    # -- cone constants ------------------------------------------------------ #
    @property
    def _n(self) -> float:
        """The cone constant.

        For a tangent cone (equal standard parallels) the usual secant formula is
        ``0/0``, so the limit ``sin(phi_1)`` is used instead. HRRR is tangent at
        38.5, so this branch is the one that matters here, not a corner case.
        """
        phi1 = math.radians(self.lat_1)
        phi2 = math.radians(self.lat_2)
        if abs(phi1 - phi2) < 1e-12:
            return math.sin(phi1)
        return (math.log(math.cos(phi1) / math.cos(phi2))
                / math.log(math.tan(math.pi / 4.0 + phi2 / 2.0)
                           / math.tan(math.pi / 4.0 + phi1 / 2.0)))

    @property
    def _f(self) -> float:
        n = self._n
        phi1 = math.radians(self.lat_1)
        return (math.cos(phi1)
                * math.tan(math.pi / 4.0 + phi1 / 2.0) ** n) / n

    @property
    def _rho0(self) -> float:
        n = self._n
        phi0 = math.radians(self.lat_origin)
        return (self.radius * self._f
                / math.tan(math.pi / 4.0 + phi0 / 2.0) ** n)

    # -- extent -------------------------------------------------------------- #
    @property
    def x1(self) -> float:
        """Easting of the last grid column, metres."""
        return self.x0 + (self.shape[1] - 1) * self.spacing

    @property
    def y1(self) -> float:
        """Northing of the last grid row, metres."""
        return self.y0 + (self.shape[0] - 1) * self.spacing

    # -- transform ----------------------------------------------------------- #
    def forward(self, lon: float, lat: float) -> tuple[float, float]:
        """Return the projection-plane ``(x, y)`` in metres for a coordinate."""
        n, f = self._n, self._f
        phi = math.radians(max(-_LAT_LIMIT, min(_LAT_LIMIT, float(lat))))
        delta = math.radians(
            ((float(lon) - self.lon_origin + 180.0) % 360.0) - 180.0)
        tangent = math.tan(math.pi / 4.0 + phi / 2.0)
        if tangent <= 0.0:
            raise ValueError("latitude is at or beyond the far pole")
        rho = self.radius * f / tangent ** n
        theta = n * delta
        return rho * math.sin(theta), self._rho0 - rho * math.cos(theta)

    def inverse(self, x: float, y: float) -> tuple[float, float]:
        """Return the ``(lon, lat)`` in degrees for a projection-plane point."""
        n, f = self._n, self._f
        dy = self._rho0 - float(y)
        rho = math.copysign(math.hypot(float(x), dy), n)
        if rho == 0.0:
            # The cone's apex is the pole; longitude is undefined there, so the
            # central meridian is the only non-arbitrary answer.
            return self._normalise_lon(self.lon_origin), 90.0 if n > 0 else -90.0
        theta = (math.atan2(float(x), dy) if n > 0
                 else math.atan2(-float(x), -dy))
        lon = self.lon_origin + math.degrees(theta / n)
        lat = math.degrees(
            2.0 * math.atan((self.radius * f / rho) ** (1.0 / n))
            - math.pi / 2.0)
        return self._normalise_lon(lon), lat

    @staticmethod
    def _normalise_lon(lon: float) -> float:
        return ((float(lon) + 180.0) % 360.0) - 180.0

    # -- membership ---------------------------------------------------------- #
    def contains(self, lat: float, lon: float) -> bool:
        """Return whether a geographic point falls on this grid.

        Tested in the grid's own plane rather than against a longitude/latitude
        box, which is the whole point of the class: a box either accepts points
        the model does not carry or refuses points it does, and near the corners
        it does both at once.
        """
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(lat) or not math.isfinite(lon):
            return False
        if not -90.0 <= lat <= 90.0:
            return False
        delta = ((lon - self.lon_origin + 180.0) % 360.0) - 180.0
        # Past a half-turn of the cone the projection folds back on itself and a
        # point on the far side of the planet would land inside the grid.
        if abs(math.radians(delta) * self._n) >= math.pi:
            return False
        try:
            x, y = self.forward(lon, lat)
        except ValueError:
            return False
        return (self.x0 <= x <= self.x1) and (self.y0 <= y <= self.y1)

    # -- perimeter ----------------------------------------------------------- #
    def outline(self, samples_per_edge: int = 32) -> tuple[
            tuple[float, float], ...]:
        """Return the grid's real perimeter as ``(lon, lat)`` points, closed.

        Sampled rather than cornered, because every edge is a curve in
        longitude/latitude -- four corners joined by straight lines would cut
        3.2 degrees off HRRR's southern boundary at the middle.
        """
        count = max(2, int(samples_per_edge))
        x0, x1, y0, y1 = self.x0, self.x1, self.y0, self.y1

        def along(start, stop):
            return (start + (stop - start) * index / count
                    for index in range(count))

        points: list[tuple[float, float]] = []
        points.extend(self.inverse(x, y0) for x in along(x0, x1))   # south
        points.extend(self.inverse(x1, y) for y in along(y0, y1))   # east
        points.extend(self.inverse(x, y1) for x in along(x1, x0))   # north
        points.extend(self.inverse(x0, y) for y in along(y1, y0))   # west
        points.append(points[0])
        return tuple(points)

    def bounds(self, samples_per_edge: int = 32) -> tuple[
            float, float, float, float]:
        """Return the perimeter's ``(lon0, lon1, lat0, lat1)`` envelope.

        Measured off the perimeter, so it is the grid's true envelope rather than
        a rounded guess. Still an over-estimate of the grid itself -- an
        axis-aligned box around a bowed shape must be -- which is exactly why
        :meth:`contains` does not use it.
        """
        points = self.outline(samples_per_edge)
        lons = [lon for lon, _lat in points]
        lats = [lat for _lon, lat in points]
        return min(lons), max(lons), min(lats), max(lats)


#: The operational HRRR CONUS grid.
#:
#: Verified against the ``latitudes``/``longitudes`` arrays encoded in a live
#: GRIB message: agreement is exact at all 1,905,141 grid points, so these are
#: not an approximation of the grid, they are the grid.
HRRR_GRID = LambertConformalGrid(
    lat_origin=38.5,
    lon_origin=-97.5,          # 262.5 degrees east
    lat_1=38.5,
    lat_2=38.5,
    radius=6371229.0,
    shape=(1059, 1799),
    spacing=3000.0,
    x0=-2697520.1425219304,
    y0=-1587306.1525566636,
)

#: PROJ string for the same grid, for the NumPy/pyproj reprojection path.
HRRR_PROJ4 = ("+proj=lcc +lat_0=38.5 +lon_0=262.5 +lat_1=38.5 "
              "+lat_2=38.5 +R=6371229 +units=m +no_defs")
