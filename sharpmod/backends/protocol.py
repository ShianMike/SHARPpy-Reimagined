"""Shared types for the Rust-primary and portable Python backends.

The protocol contains isolated numerical, row-processing, and point-GRIB
operations. GUI objects, SHARPpy widgets, downloads, and profile construction
stay in Python and remain outside this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .grib import DecodedPoint


# Increment this only when the Python/native calling contract changes in an
# incompatible way.  It is deliberately separate from the package version:
# package versions keep wheels in lockstep, while this value describes the
# shape of the extension API itself.
BACKEND_API_VERSION = 2

REQUIRED_RUST_CAPABILITIES = (
    "wind_to_components",
    "components_to_wind",
    "interpolate_1d",
    "basic_sounding_qc",
    "pressure_sort_dedup_indices",
    "parse_sounding_rows",
    "decode_grib_point",
)


@dataclass(frozen=True)
class QualityControlResult:
    """Deterministic result returned by basic sounding-profile QC.

    ``valid_level_count`` counts rows with usable pressure and height.  Missing
    thermodynamic or wind values do not remove an otherwise structural level.
    """

    valid: bool
    valid_level_count: int
    issues: tuple[str, ...]


@runtime_checkable
class Backend(Protocol):
    """Operations implemented by both the Python and Rust backends."""

    name: str

    def wind_to_components(self, direction, speed, *, missing=None):
        """Convert meteorological direction/speed to unit-preserving ``u/v``."""

    def components_to_wind(self, u, v, *, missing=None):
        """Convert unit-preserving ``u/v`` to direction/speed."""

    def interpolate_1d(
        self, target, coordinate, values, *, missing=None, log=False,
    ):
        """Interpolate values at one or more targets without extrapolation."""

    def basic_sounding_qc(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        wdir,
        wspd,
        *,
        missing=-9999.0,
    ) -> QualityControlResult:
        """Run the pinned basic sounding-profile checks."""

    def pressure_sort_dedup_indices(self, pressure, *, missing=-9999.0):
        """Return stable descending indices with invalid/duplicate pressure removed."""

    def parse_sounding_rows(self, text: str, *, missing=-9999.0):
        """Parse the simple six-column sounding-row representation."""

    def decode_grib_point(
        self, path, lat, lon, *, missing=-9999.0,
    ) -> "DecodedPoint":
        """Decode one pressure-level GRIB column at the nearest grid point."""
