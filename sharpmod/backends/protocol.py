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
BACKEND_API_VERSION = 6

REQUIRED_RUST_CAPABILITIES = (
    "wind_to_components",
    "components_to_wind",
    "interpolate_1d",
    "basic_sounding_qc",
    "pressure_sort_dedup_indices",
    "parse_sounding_rows",
    "profile_kinematics",
    "profile_parcels",
    "profile_convective_parcels",
    "lift_parcel",
    "profile_dcape",
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


@dataclass(frozen=True)
class KinematicLayer:
    """One surface-to-height layer from a profile kinematics workspace.

    Missing quantities are represented by ``NaN`` inside the backend contract.
    The SharpTab adapter converts those values to its masked ``MISSING``
    sentinel when exposing existing public functions.
    """

    top_agl: float
    top_pressure: float
    pressure_shear_u: float
    pressure_shear_v: float
    height_shear_u: float
    height_shear_v: float
    mean_u: float
    mean_v: float
    mean_npw_u: float
    mean_npw_v: float
    srh_total: float
    srh_positive: float
    srh_negative: float
    storm_relative_mean_u: float
    storm_relative_mean_v: float


@dataclass(frozen=True)
class ProfileKinematics:
    """Coarse-grained surface-layer diagnostics computed in one backend call."""

    storm_motion: tuple[float, float, float, float]
    layers: tuple[KinematicLayer, ...]

    def layer(self, top_agl: float, *, tolerance: float = 1.0e-6):
        """Return the requested AGL layer or ``None`` when it was not computed."""
        target = float(top_agl)
        for layer in self.layers:
            if abs(layer.top_agl - target) <= tolerance:
                return layer
        return None


@dataclass(frozen=True)
class ParcelDiagnostics:
    """One undiluted parcel ascent from the thermodynamic workspace."""

    start_pressure: float
    start_height: float
    start_temperature: float
    start_dewpoint: float
    lcl_pressure: float
    lcl_height: float
    lfc_pressure: float
    lfc_height: float
    el_pressure: float
    el_height: float
    cape: float
    cin: float
    cape_3km: float
    cape_6km: float


@dataclass(frozen=True)
class ParcelWorkspace:
    """Surface, most-unstable, and 100-hPa mixed-layer parcel results."""

    surface: ParcelDiagnostics
    most_unstable: ParcelDiagnostics
    mixed_layer: ParcelDiagnostics

    def parcel(self, kind: str) -> ParcelDiagnostics:
        """Return a parcel by its short or descriptive name."""
        key = str(kind).strip().lower().replace("-", "_")
        mapping = {
            "sb": self.surface,
            "surface": self.surface,
            "mu": self.most_unstable,
            "most_unstable": self.most_unstable,
            "ml": self.mixed_layer,
            "mixed_layer": self.mixed_layer,
        }
        try:
            return mapping[key]
        except KeyError as exc:
            raise KeyError(f"unknown parcel kind {kind!r}") from exc


@dataclass(frozen=True)
class ParcelTrace:
    """Pressure and temperature coordinates for one parcel path."""

    pressure: tuple[float, ...]
    temperature: tuple[float, ...]


@dataclass(frozen=True)
class ParcelAscent:
    """Parcel diagnostics plus its virtual-temperature plotting trace."""

    diagnostics: ParcelDiagnostics
    trace: ParcelTrace


@dataclass(frozen=True)
class ConvectiveParcelWorkspace:
    """Standard full-profile parcels and effective-inflow-layer bounds."""

    surface: ParcelAscent
    forecast: ParcelAscent
    most_unstable: ParcelAscent
    mixed_layer: ParcelAscent
    effective: ParcelAscent
    effective_bottom_pressure: float
    effective_top_pressure: float

    def parcel(self, kind: str) -> ParcelAscent:
        """Return a parcel ascent by its short or descriptive name."""
        key = str(kind).strip().lower().replace("-", "_")
        mapping = {
            "sb": self.surface,
            "surface": self.surface,
            "fcst": self.forecast,
            "forecast": self.forecast,
            "mu": self.most_unstable,
            "most_unstable": self.most_unstable,
            "ml": self.mixed_layer,
            "mixed_layer": self.mixed_layer,
            "eff": self.effective,
            "effective": self.effective,
        }
        try:
            return mapping[key]
        except KeyError as exc:
            raise KeyError(f"unknown parcel kind {kind!r}") from exc


@dataclass(frozen=True)
class DowndraftDiagnostics:
    """DCAPE, source level, downrush temperature, and parcel trace."""

    cape: float
    source_pressure: float
    downrush_temperature: float
    trace: ParcelTrace


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

    def profile_kinematics(
        self,
        pres,
        hght,
        u,
        v,
        layer_tops_agl,
        *,
        sfc=0,
        missing=-9999.0,
    ) -> ProfileKinematics:
        """Compute shared surface-layer wind diagnostics in one backend call."""

    def profile_parcels(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        *,
        sfc=0,
        missing=-9999.0,
    ) -> ParcelWorkspace:
        """Compute cached SB/MU/ML parcel diagnostics in one backend call."""

    def profile_convective_parcels(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        *,
        sfc=0,
        missing=-9999.0,
    ) -> ConvectiveParcelWorkspace:
        """Compute standard parcel summaries, traces, and effective bounds."""

    def lift_parcel(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        parcel_pressure,
        parcel_temperature,
        parcel_dewpoint,
        *,
        sfc=0,
        missing=-9999.0,
    ) -> ParcelAscent:
        """Lift one explicitly defined parcel and return its trace."""

    def profile_dcape(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        *,
        sfc=0,
        missing=-9999.0,
    ) -> DowndraftDiagnostics:
        """Compute DCAPE and its descending parcel trace."""

    def decode_grib_point(
        self, path, lat, lon, *, missing=-9999.0,
    ) -> "DecodedPoint":
        """Decode one pressure-level GRIB column at the nearest grid point."""
