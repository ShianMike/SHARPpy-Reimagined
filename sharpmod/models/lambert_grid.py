"""Model grid declarations backed by native Lambert conformal geometry.

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

The frozen dataclass preserves the existing provider and testing interfaces.
Rust owns projection, membership and perimeter calculations; bulk raster
reprojection continues to use the existing pyproj path.

Verified against pyproj at HRRR's four grid corners to 1e-13 degrees, and the
south-west corner reproduces the ``21.138123N 237.280472E`` that HRRR's own GRIB
header publishes as its first grid point.
"""

from __future__ import annotations

from dataclasses import dataclass
import sharpmod_rs

__all__ = [
    "LambertConformalGrid",
    "HRRR_GRID",
    "NAM_GRID",
    "HRW_GRID",
]

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
        return sharpmod_rs.lambert_constants(self)[0]

    @property
    def _f(self) -> float:
        return sharpmod_rs.lambert_constants(self)[1]

    @property
    def _rho0(self) -> float:
        return sharpmod_rs.lambert_constants(self)[2]

    # -- extent -------------------------------------------------------------- #
    @property
    def x1(self) -> float:
        """Easting of the last grid column, metres."""
        return sharpmod_rs.lambert_extent(self)[0]

    @property
    def y1(self) -> float:
        """Northing of the last grid row, metres."""
        return sharpmod_rs.lambert_extent(self)[1]

    # -- transform ----------------------------------------------------------- #
    def forward(self, lon: float, lat: float) -> tuple[float, float]:
        """Return the projection-plane ``(x, y)`` in metres for a coordinate."""
        return sharpmod_rs.lambert_forward(self, float(lon), float(lat))

    def inverse(self, x: float, y: float) -> tuple[float, float]:
        """Return the ``(lon, lat)`` in degrees for a projection-plane point."""
        return sharpmod_rs.lambert_inverse(self, float(x), float(y))

    @staticmethod
    def _normalise_lon(lon: float) -> float:
        return sharpmod_rs.lambert_normalize_longitude(float(lon))

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
        return sharpmod_rs.lambert_contains(self, lat, lon)

    # -- perimeter ----------------------------------------------------------- #
    def outline(self, samples_per_edge: int = 32) -> tuple[
            tuple[float, float], ...]:
        """Return the grid's real perimeter as ``(lon, lat)`` points, closed.

        Sampled rather than cornered, because every edge is a curve in
        longitude/latitude -- four corners joined by straight lines would cut
        3.2 degrees off HRRR's southern boundary at the middle.
        """
        return sharpmod_rs.lambert_outline(self, int(samples_per_edge))

    def bounds(self, samples_per_edge: int = 32) -> tuple[
            float, float, float, float]:
        """Return the perimeter's ``(lon0, lon1, lat0, lat1)`` envelope.

        Measured off the perimeter, so it is the grid's true envelope rather than
        a rounded guess. Still an over-estimate of the grid itself -- an
        axis-aligned box around a bowed shape must be -- which is exactly why
        :meth:`contains` does not use it.
        """
        return sharpmod_rs.lambert_bounds(self, int(samples_per_edge))


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

# NAM grid 218, verified against the awphys/awip12 GRIB headers and coordinates.
NAM_GRID = LambertConformalGrid(
    lat_origin=25.0, lon_origin=-95.0, lat_1=25.0, lat_2=25.0,
    radius=6371229.0, shape=(428, 614), spacing=12191.0,
    x0=-4226106.99691547, y0=-832698.2610175647,
)
NAM_PROJ4 = ("+proj=lcc +lat_0=25 +lon_0=265 +lat_1=25 "
             "+lat_2=25 +R=6371229 +units=m +no_defs")

# HiResW's 5 km CONUS stream shares grid 218's origin and cone, with its own cells.
HRW_GRID = LambertConformalGrid(
    lat_origin=25.0, lon_origin=-95.0, lat_1=25.0, lat_2=25.0,
    radius=6371229.0, shape=(1025, 1473), spacing=5079.0,
    x0=NAM_GRID.x0, y0=NAM_GRID.y0,
)
