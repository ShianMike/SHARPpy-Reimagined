"""Public forecast-model point-sounding extractor.

Fetches pressure-level forecast grids through Herbie, extracts the nearest
column, and writes the same portable ``.npz`` point-sounding format used by the
ERA5, IFS, HRRR, UWyo, and WRF paths.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from sharpmod import backends as _backends
from sharpmod.providers import eccc_geomet
from sharpmod.models import lambert_grid
from sharpmod.providers import openmeteo
from sharpmod.providers import rrfs_nomads
from sharpmod.export_paths import export_file_path
from sharpmod.models.model_fields import (
    CFS_SURFACE_SEARCH,
    IFS_INVARIANT_FIELDS,
    IFS_INVARIANT_SEARCH,
    IFS_SURFACE_FIELDS,
    NOAA_INVARIANT_FIELDS,
    NOAA_INVARIANT_SEARCH,
    NOAA_SURFACE_FIELDS,
    NOAA_SURFACE_SEARCH,
    build_ifs_search,
    build_noaa_search,
    choose_search,
    supports_ifs_surface_merge,
    supports_noaa_surface_merge,
)
from sharpmod.models.model_transport import (
    DownloadCancelled,
    OptimizedTransportUnavailable,
    _valid_grib,
    download_herbie_subset,
    download_herbie_subset_fallback,
    range_worker_count,
    ranges_from_inventory,
)
from sharpmod.models.model_surface import (
    SURFACE_CONTRACT_FIELDS,
    SURFACE_CONTRACT_VERSION,
)
from sharpmod.upstream.upstream_patches import apply_herbie_source_fallback
from sharpmod.upstream.upstream_warnings import (
    known_herbie_deprecations,
    xarray_new_combine_defaults,
)
from sharpmod.models.model_sources import (
    SourceRoutingUnavailable,
    download_nomads_subset,
    nomads_supported,
    select_herbie_provider,
)
from sharpmod.providers.hrrr_zarr import (
    HrrrZarrPointDataset,
    ZarrBackendUnavailable,
    fetch_hrrr_zarr_point,
)
from sharpmod.tools.era5_extract import (
    ERA5ExtractionError as ModelExtractionError,
    ParameterRangeError,
    RetrievalError,
    _as_datetime,
    _atomic_write_json,
    _atomic_write_npz,
    _build_columns,
    _coord_values,
    _horizontal_indexers,
    _merge_datasets,
    _promote_scalar_level_coordinate,
    _quiet_remove,
    _require_profile_qc,
    _select_time,
    _surface_relative_vorticity_from_wind_grid,
    select_nearest_grid_point,
    _LAT_COORDS,
    _LEVEL_COORDS,
    _LON_COORDS,
    _VAR_U,
    _VAR_V,
    _VAR_10M_U,
    _VAR_10M_V,
    _VAR_2M_DEWPOINT,
    _VAR_2M_TEMP,
    _VAR_SURFACE_HEIGHT,
    _VAR_SURFACE_PRESSURE,
)

LAT_MIN, LAT_MAX = -90.0, 90.0
LON_MIN, LON_MAX = -180.0, 360.0

NOAA_PRESSURE_SEARCH = build_noaa_search(
    ("HGT", "TMP", "RH", "SPFH", "UGRD", "VGRD", "VVEL", "DZDT", "ABSV")
)
IFS_PRESSURE_SEARCH = build_ifs_search(("gh", "t", "u", "v", "r", "q", "w", "vo"))

_LOGGER = logging.getLogger(__name__)




@dataclass(frozen=True)
class DecodedModelPointDataset:
    """One predecoded model point used by vectorized batch extraction."""

    decoded: object
    valid_time: datetime
    backend: str = "vectorized multi-point direct GRIB decoder"
    vorticity_source: str = "direct pressure-level vorticity field"

    def close(self):
        """Match the model-hour dataset protocol (there is no handle)."""






GLOBAL_DOMAIN = (-180.0, 180.0, -90.0, 90.0)

#: Coarse longitude/latitude envelope shared by the CONUS models.
#:
#: Deliberately generous, and only a fallback. Every one of these models runs on
#: a Lambert conformal grid whose boundary is curved, so no box describes one:
#: HRRR's southern edge reaches 24.36N over Kansas and 21.14N at its own corners.
#: A model that supplies a ``domain_outline`` is tested and drawn against that
#: instead -- see :data:`HRRR_OUTLINE`. The others still fall back to this, which
#: accepts a margin of points they do not actually carry.
CONUS_DOMAIN = (-130.0, -60.0, 20.0, 55.0)

#: HRRR's real grid perimeter, and the envelope measured off it.
#:
#: The envelope is computed rather than rounded, so it cannot drift from the grid.
#: It also differs from :data:`CONUS_DOMAIN` in every direction, which is the
#: measure of how wrong the box was: 4.1 degrees of longitude at the west edge,
#: 2.4 degrees of latitude at the north.
HRRR_OUTLINE = lambert_grid.HRRR_GRID.outline()
HRRR_DOMAIN = lambert_grid.HRRR_GRID.bounds()
ALASKA_DOMAIN = (160.0, -120.0, 40.0, 80.0)
HAWAII_DOMAIN = (-162.5, -152.5, 16.0, 24.0)
PUERTO_RICO_DOMAIN = (-70.0, -62.0, 15.0, 22.0)
NORTH_AMERICA_DOMAIN = (-180.0, -20.0, 5.0, 80.0)
# Only the synoptic cycles publish the RRFS pressure-level product. The
# off-hour cycles publish a sub-hourly two-dimensional product and nothing
# else, so they carry no sounding at any forecast hour; see rrfs_nomads.
RRFS_CYCLES = rrfs_nomads.PRESSURE_CYCLES

# Products that are described here but withheld from selection because no
# published file can complete the verified ground row. Confirmed against live
# inventories rather than assumed; see CHANGELOG for the audit.
AIGFS_NO_SURFACE_REASON = (
    "AIGFS splits pressure and surface products, and its sfc product "
    "publishes only 2-m temperature, 10-m winds, and mean-sea-level pressure. "
    "Surface pressure, terrain height, and 2-m moisture are absent from every "
    "AIGFS product, so no verified ground row can be built."
)


def _hours(stop, step=1):
    return tuple(range(0, int(stop) + 1, int(step)))


def _gfs_hours():
    return tuple(range(0, 121)) + tuple(range(123, 385, 3))


def _ifs_hours():
    return tuple(range(0, 145, 3)) + tuple(range(150, 361, 6))


def _aifs_hours():
    """AIFS publishes 6-hourly steps only, at every cycle, out to F360."""
    return tuple(range(0, 361, 6))


# ECMWF IFS runs a short cut-off forecast at 06Z and 18Z that stops at F144.
# Since IFS Cycle 50r1 (13 May 2026) those cycles are published under
# ``stream=oper`` rather than the retired ``scda`` stream, but they still carry
# no step beyond F144. AIFS has no such cut-off.
IFS_SHORT_CUTOFF_CYCLES = (6, 18)
IFS_SHORT_CUTOFF_MAX_FXX = 144


from sharpmod.tools.model_extract_config import ModelConfig  # noqa: E402


_CONFIGS = (
    ModelConfig(
        "hrrr", "HRRR", "hrrr", "prs", cycles=tuple(range(24)),
        fxx_values=_hours(48), domain="CONUS", domain_bounds=HRRR_DOMAIN,
        grid_spacing_km=3.0,
        domain_outline=HRRR_OUTLINE,
        notes="3-km CONUS pressure-level forecast grids"),
    ModelConfig(
        "rap", "RAP", "rap", "awp130pgrb", cycles=tuple(range(24)),
        fxx_values=_hours(51), domain="CONUS", domain_bounds=CONUS_DOMAIN,
        grid_spacing_km=13.0,
        notes="13-km RAP AWIPS pressure-level forecast grids"),
    ModelConfig(
        "nam", "NAM", "nam", "awphys",
        fxx_values=_hours(84, 3), domain="CONUS", domain_bounds=CONUS_DOMAIN,
        grid_spacing_km=12.0,
        notes="12-km NAM CONUS pressure-level forecast grids"),
    ModelConfig(
        "nam-3km-conus", "NAM 3km CONUS", "nam", "conusnest.hiresf",
        fxx_values=_hours(60), domain="CONUS", domain_bounds=CONUS_DOMAIN,
        grid_spacing_km=3.0,
        notes="NAM CONUS nest pressure-level forecast grids"),
    # HiResW CONUS nests run twice a day; 06Z and 18Z are never published.
    ModelConfig(
        "hrw-wrf-arw", "HRW WRF-ARW", "hiresw", "arw_5km",
        cycles=(0, 12),
        fxx_values=_hours(48), domain="CONUS", domain_bounds=CONUS_DOMAIN,
        grid_spacing_km=5.0,
        notes="NOAA HiResW ARW 5-km pressure-level grids, 00Z/12Z"),
    ModelConfig(
        "hrw-fv3", "HRW FV3", "hiresw", "fv3_5km",
        cycles=(0, 12),
        fxx_values=_hours(48), domain="CONUS", domain_bounds=CONUS_DOMAIN,
        grid_spacing_km=5.0,
        notes="NOAA HiResW FV3 5-km pressure-level grids, 00Z/12Z"),
    # RRFS bypasses Herbie entirely; see rrfs_nomads and DIRECT_GRIB_KEYS.
    # ``herbie_model`` and ``product`` stay truthful because they still name
    # the published product, and ``kwargs["domain"]`` is now this route's own
    # domain tag rather than a Herbie argument.
    ModelConfig(
        "rrfs-a", "RRFS A", "rrfs", "prslev",
        cycles=RRFS_CYCLES, fxx_values=_hours(84),
        domain="CONUS", domain_bounds=CONUS_DOMAIN,
        kwargs={"domain": "conus"},
        grid_spacing_km=3.0,
        notes="RRFS-A 3-km pressure-level grids; no VVEL published"),
    ModelConfig(
        "rrfs-a-alaska", "RRFS A Alaska", "rrfs", "prslev",
        cycles=RRFS_CYCLES, fxx_values=_hours(84),
        domain="Alaska", domain_bounds=ALASKA_DOMAIN,
        kwargs={"domain": "alaska"},
        grid_spacing_km=3.0,
        notes="RRFS-A 3-km Alaska pressure-level grids; no VVEL published"),
    ModelConfig(
        "rrfs-a-hawaii", "RRFS A Hawaii", "rrfs", "prslev",
        cycles=RRFS_CYCLES, fxx_values=_hours(84),
        domain="Hawaii", domain_bounds=HAWAII_DOMAIN,
        kwargs={"domain": "hawaii"},
        grid_spacing_km=2.5,
        notes="RRFS-A 2.5-km Hawaii pressure-level grids; no VVEL published"),
    ModelConfig(
        "rrfs-a-puerto-rico", "RRFS A Puerto Rico", "rrfs", "prslev",
        cycles=RRFS_CYCLES, fxx_values=_hours(84),
        domain="Puerto Rico", domain_bounds=PUERTO_RICO_DOMAIN,
        kwargs={"domain": "puerto rico"},
        grid_spacing_km=2.5,
        notes="RRFS-A 2.5-km Puerto Rico pressure-level grids; "
              "no VVEL published"),
    ModelConfig(
        "rrfs-a-north-america", "RRFS A North America", "rrfs", "prslev",
        cycles=RRFS_CYCLES, fxx_values=_hours(84),
        domain="North America", domain_bounds=NORTH_AMERICA_DOMAIN,
        kwargs={"domain": "north america"},
        grid_spacing_km=13.0,
        notes="RRFS-A 13-km North America pressure-level grids; "
              "covers CONUS for roughly half the transfer of the 3-km "
              "domain; no VVEL published"),
    ModelConfig(
        "gfs", "GFS", "gfs", "pgrb2.0p25",
        fxx_values=_gfs_hours(),
        grid_spacing_km=27.8,
        notes="0.25-degree GFS pressure-level forecast grids"),
    ModelConfig(
        "aigfs", "AIGFS", "aigfs", "pres",
        fxx_values=_hours(384, 6),
        grid_spacing_km=27.8,
        notes="AI-GFS pressure-level grids; humidity from SPFH",
        unavailable_reason=AIGFS_NO_SURFACE_REASON),
    ModelConfig(
        "cfs", "CFS", "cfs", "6_hourly",
        fxx_values=_hours(384, 6),
        kwargs={"member": 1, "kind": "pgbf"},
        grid_spacing_km=55.6,
        notes="CFS 6-hourly pressure-level grids, member 1 by default"),
    ModelConfig(
        "ecmwf-ifs", "ECMWF IFS Open Data", "ifs", "oper",
        search=IFS_PRESSURE_SEARCH, fxx_values=_ifs_hours(),
        grid_spacing_km=27.8,
        notes="ECMWF open-data deterministic IFS pressure levels"),
    ModelConfig(
        "ecmwf-aifs", "ECMWF-AIFS", "aifs", "oper",
        search=IFS_PRESSURE_SEARCH, fxx_values=_aifs_hours(),
        grid_spacing_km=27.8,
        notes="ECMWF open-data AIFS pressure levels, 6-hourly steps"),
    # Open-Meteo point provider. ``herbie_model`` and ``product`` are sentinels
    # here, as they are for the ECCC entries: this route never reaches Herbie.
    # This is the only route to ICON in the application, since the installed
    # Herbie cannot load DWD's split native-level GRIB.
    ModelConfig(
        "openmeteo-icon-global",
        openmeteo.CAPABILITIES["openmeteo-icon-global"].label,
        "openmeteo-icon-global", "open-meteo-point",
        cycles=openmeteo.CAPABILITIES["openmeteo-icon-global"].cycles,
        fxx_values=openmeteo.CAPABILITIES[
            "openmeteo-icon-global"].forecast_hours,
        # Matches the adapter's own declared resolution ("11 km, 0.125 degree
        # grid"), so area sampling cannot advertise a spacing that contradicts
        # the label shown beside it.
        grid_spacing_km=11.0,
        notes=openmeteo.CAPABILITIES["openmeteo-icon-global"].notes),
    ModelConfig(
        "gefs", "GEFS", "gefs", "atmos.5",
        fxx_values=_hours(384, 3),
        kwargs={"member": "c00"},
        grid_spacing_km=55.6,
        notes="GEFS 0.5-degree control member by default"),
    ModelConfig(
        "gdps", "Canadian GDPS 15 km", "gdps", "geomet-point",
        cycles=(0, 12),
        fxx_values=eccc_geomet.get_capability("gdps").forecast_hours,
        grid_spacing_km=15.0,
        notes="Global ECCC GeoMet pressure-level point values"),
    ModelConfig(
        "rdps", "Canadian RDPS 10 km", "rdps", "geomet-point",
        cycles=(0, 6, 12, 18),
        fxx_values=eccc_geomet.get_capability("rdps").forecast_hours,
        domain="North America and Arctic",
        domain_bounds=eccc_geomet.get_capability("rdps").domain_bounds,
        grid_spacing_km=10.0,
        notes="Regional ECCC GeoMet pressure-level point values",
        domain_outline=eccc_geomet.get_capability("rdps").domain_outline),
)

_ALIASES = {
    "hrrr": "hrrr",
    "rap": "rap",
    "nam": "nam",
    "nam3": "nam-3km-conus",
    "nam-3km": "nam-3km-conus",
    "nam-3km-conus": "nam-3km-conus",
    "hiresw-arw": "hrw-wrf-arw",
    "hrw-arw": "hrw-wrf-arw",
    "hrw-wrf-arw": "hrw-wrf-arw",
    "hiresw-fv3": "hrw-fv3",
    "hrw-fv3": "hrw-fv3",
    "rrfs": "rrfs-a",
    "rrfs-a": "rrfs-a",
    "rrfs-ak": "rrfs-a-alaska",
    "rrfs-alaska": "rrfs-a-alaska",
    "rrfs-a-alaska": "rrfs-a-alaska",
    "rrfs-hi": "rrfs-a-hawaii",
    "rrfs-hawaii": "rrfs-a-hawaii",
    "rrfs-a-hawaii": "rrfs-a-hawaii",
    "rrfs-pr": "rrfs-a-puerto-rico",
    "rrfs-puerto-rico": "rrfs-a-puerto-rico",
    "rrfs-a-puerto-rico": "rrfs-a-puerto-rico",
    "rrfs-na": "rrfs-a-north-america",
    "rrfs-north-america": "rrfs-a-north-america",
    "rrfs-a-north-america": "rrfs-a-north-america",
    "gfs": "gfs",
    "aigfs": "aigfs",
    "cfs": "cfs",
    "ecmwf": "ecmwf-ifs",
    "ifs": "ecmwf-ifs",
    "ecmwf-ifs": "ecmwf-ifs",
    "aifs": "ecmwf-aifs",
    # Bare "icon" is claimed here, unlike "ecmwf" above. Nothing resolved it
    # before -- it raised with "the installed Herbie has no ICON loader" -- so
    # no existing command or saved session changes meaning, and this is the only
    # ICON route the application has. The regional ICON variants stay
    # namespaced, so "icon-eu" and "icon-d2" remain free for later.
    "icon": "openmeteo-icon-global",
    "icon-global": "openmeteo-icon-global",
    "openmeteo-icon-global": "openmeteo-icon-global",
    "openmeteo-icon": "openmeteo-icon-global",
    "om-icon": "openmeteo-icon-global",
    "ecmwf-aifs": "ecmwf-aifs",
    "gefs": "gefs",
    "gdps": "gdps",
    "gem-global": "gdps",
    "cmc-global": "gdps",
    "rdps": "rdps",
    "gem-regional": "rdps",
    "cmc-regional": "rdps",
}

_CONFIG_BY_KEY = {cfg.key: cfg for cfg in _CONFIGS}

UNSUPPORTED_MODELS = {
    # "icon" used to sit here, explaining that the installed Herbie has no ICON
    # loader. That is still true of the GRIB route, but ICON now reaches this
    # application as an Open-Meteo point sounding, so the key resolves and must
    # not also be reported as unavailable.
    "ukmet": "UKMET global GRIB access is not public through Herbie here.",
    "eps": "ECMWF EPS products need separate ensemble handling.",
    "eps-opendata": "ECMWF EPS open data needs separate ensemble handling.",
    "cmce": "CMC ensemble members are not exposed by the installed Herbie loader.",
    "mogreps-g": "MOGREPS-G is not exposed by the installed Herbie loader.",
    "sref": "SREF is not exposed by the installed Herbie loader.",
    "hrdps": (
        "HRDPS GeoMet does not currently expose a complete pressure-level "
        "temperature/moisture/wind profile."
    ),
}


def available_models():
    """Return the forecast model configs that can produce a sounding."""
    return tuple(cfg for cfg in _CONFIGS if not cfg.unavailable_reason)


def model_aliases():
    """Return a copy of resolver aliases for discovery without exposing mutation."""
    return dict(_ALIASES)


def withheld_models():
    """Return ``{key: reason}`` for configured products that cannot be used."""
    return {
        cfg.key: cfg.unavailable_reason
        for cfg in _CONFIGS
        if cfg.unavailable_reason
    }


def model_unavailable_reason(model) -> str:
    """Return why ``model`` cannot produce a sounding, or an empty string."""
    return _coerce_config(model).unavailable_reason


def _require_selectable(config):
    """Refuse a product that cannot complete the verified ground row."""
    if config.unavailable_reason:
        raise RetrievalError(
            "%s cannot produce a sounding: %s"
            % (config.label, config.unavailable_reason)
        )
    return config


#: Model keys answered by a point provider rather than a grid subset. These need
#: no GRIB runtime and their reusable dataset is bound to one requested point.
#: Collected once so the seams below ask a single question instead of repeating a
#: literal set, which is how the second provider came to need seven edits.
ECCC_POINT_KEYS = frozenset({"gdps", "rdps"})
OPENMETEO_POINT_KEYS = frozenset(openmeteo.CAPABILITIES)
POINT_PROVIDER_KEYS = ECCC_POINT_KEYS | OPENMETEO_POINT_KEYS

#: Model keys that fetch published GRIB directly instead of through Herbie.
#: These still need the native GRIB runtime to decode and still cache a
#: grid-level dataset, so they are not point providers; they only skip Herbie's
#: file location and inventory layer. Asked as one question for the same reason
#: as the sets above.
DIRECT_GRIB_KEYS = frozenset({
    cfg.key for cfg in _CONFIGS if cfg.herbie_model == "rrfs"
})


def requires_grib_runtime(model) -> bool:
    """Whether a model route imports the native ecCodes/cfgrib stack."""
    return _coerce_config(model).key not in POINT_PROVIDER_KEYS


def point_only_provider(model) -> bool:
    """Whether a reusable source dataset is tied to one requested point."""
    return _coerce_config(model).key in POINT_PROVIDER_KEYS


def _normalize_lon180(lon):
    return ((float(lon) + 180.0) % 360.0) - 180.0


def _coerce_config(model):
    return model if isinstance(model, ModelConfig) else get_config(model)




def cycle_hours(model):
    """Return configured UTC cycle hours for one provider adapter."""
    return tuple(_coerce_config(model).cycles)




def domain_label(model):
    """Return a reader-facing model-domain label."""
    cfg = _coerce_config(model)
    return cfg.domain


#: Spacing assumed when a product does not declare one. Chosen coarse on
#: purpose: over-estimating spacing asks for fewer soundings than the grid can
#: actually resolve, which wastes nothing and cannot fabricate a gradient.
#: Under-estimating would do the opposite.
UNKNOWN_GRID_SPACING_KM = 25.0


def grid_spacing_km(model):
    """Return the nominal horizontal grid spacing for ``model``, in km.

    Falls back to :data:`UNKNOWN_GRID_SPACING_KM` for a product that does not
    declare one, so callers can always divide by this without a guard.
    """
    spacing = float(_coerce_config(model).grid_spacing_km or 0.0)
    if not np.isfinite(spacing) or spacing <= 0.0:
        return UNKNOWN_GRID_SPACING_KM
    return float(spacing)


#: Models whose acceptance is decided on their native grid, not on a box.
#:
#: A Lambert grid's boundary is curved, so a box does two wrong things at once
#: near the corners: it accepts points the model does not carry and, if tightened
#: to stop that, refuses points it does. Testing in the grid's own plane is the
#: only way to get both right, and it is also what makes the drawn outline and
#: the accept/refuse answer agree -- a map that outlines one region while
#: accepting a different one is worse than a coarse map.
_NATIVE_GRID_DOMAINS = {
    "hrrr": lambert_grid.HRRR_GRID,
}


def point_in_domain(model, lat, lon):
    """Return whether ``lat``/``lon`` is inside the model's configured domain."""
    cfg = _coerce_config(model)
    if cfg.key in ECCC_POINT_KEYS:
        return eccc_geomet.point_in_domain(cfg.key, lat, lon)
    if cfg.key in OPENMETEO_POINT_KEYS:
        return openmeteo.point_in_domain(
            openmeteo.get_capability(cfg.key), lat, lon)
    grid = _NATIVE_GRID_DOMAINS.get(cfg.key)
    if grid is not None:
        return grid.contains(lat, lon)
    lon0, lon1, lat0, lat1 = cfg.domain_bounds
    lat = float(lat)
    lon = _normalize_lon180(lon)
    longitude_ok = lon0 <= lon <= lon1 if lon0 <= lon1 \
        else lon >= lon0 or lon <= lon1
    return lat0 <= lat <= lat1 and longitude_ok


def _longitude_segments(lon0, lon1):
    lon0 = max(-180.0, min(180.0, float(lon0)))
    lon1 = max(-180.0, min(180.0, float(lon1)))
    if lon0 <= lon1:
        return ((lon0, lon1),)
    return ((lon0, 180.0), (-180.0, lon1))






def unsupported_models():
    """Return every known model that is not selectable here, with the reason."""
    # The Open-Meteo adapter withholds identifiers it has not audited, and
    # duplicates of routes already built in. Merging them here is what turns
    # "why is ICON missing?" into an answer instead of a KeyError.
    return {
        **UNSUPPORTED_MODELS,
        **openmeteo.unsupported_models(),
        **withheld_models(),
    }


def get_config(model):
    """Resolve a model key/alias to a :class:`ModelConfig`."""
    key = str(model).strip().lower()
    canonical = _ALIASES.get(key)
    if canonical is None:
        # Consult the merged map, not just this module's dict. ``--list`` prints
        # the Open-Meteo adapter's withheld keys under "known but not enabled",
        # so typing one back must produce that same reason rather than a bare
        # KeyError contradicting the listing the user just read.
        reason = unsupported_models().get(key)
        if reason:
            raise RetrievalError("%s is not enabled: %s" % (model, reason))
        raise KeyError("unknown forecast model %r" % model)
    return _CONFIG_BY_KEY[canonical]


def _validate_lat_lon(lat, lon):
    if not (LAT_MIN <= lat <= LAT_MAX):
        raise ParameterRangeError(
            "latitude %.4f is out of range; permitted range is "
            "[%.1f, %.1f] degrees" % (lat, LAT_MIN, LAT_MAX))
    if not (LON_MIN <= lon <= LON_MAX):
        raise ParameterRangeError(
            "longitude %.4f is out of range; permitted range is "
            "[%.1f, %.1f] degrees" % (lon, LON_MIN, LON_MAX))


def _floor_to_cycle(dt, cycles):
    dt = _as_datetime(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    cycles = tuple(sorted(int(h) for h in cycles))
    hour = max((h for h in cycles if h <= dt.hour), default=cycles[-1])
    if hour > dt.hour:
        from datetime import timedelta
        dt = dt - timedelta(days=1)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0)


def _run_datetime(run_time, config):
    if run_time is None:
        return _floor_to_cycle(datetime.now(timezone.utc), config.cycles)
    return _floor_to_cycle(run_time, config.cycles)


def _herbie_kwargs(config, member=None):
    kwargs = dict(config.kwargs)
    if member is not None:
        kwargs["member"] = member
    return kwargs






def _load_herbie_class():
    """Import Herbie behind its two pinned-upstream deprecation guards."""
    try:
        with known_herbie_deprecations():
            from herbie import Herbie
    except Exception as exc:  # pragma: no cover - optional dependency path
        raise RetrievalError(
            "forecast model support requires the optional [era5] extra "
            "(herbie-data, cfgrib, xarray): %s" % exc
        ) from exc
    apply_herbie_source_fallback(Herbie)
    return Herbie


def _create_herbie(Herbie, *args, **kwargs):
    """Construct Herbie without leaking its pandas-3 deprecation warning."""
    with known_herbie_deprecations():
        return Herbie(*args, **kwargs)


def _emit_progress(callback, stage, total_bytes=0):
    """Send one optional, dependency-free extraction progress event."""
    if callback is not None:
        callback(str(stage), max(0, int(total_bytes or 0)))


def _subset_download_bytes(inventory_or_herbie, search=None):
    """Return the planned coalesced byte-range transfer size."""
    try:
        if search is None:
            inventory = inventory_or_herbie
        else:
            inventory = inventory_or_herbie.inventory(search).copy()
        if len(inventory) == 0:
            return 0
        return sum(item.size for item in ranges_from_inventory(inventory))
    except Exception:
        # Progress estimation must never make an otherwise valid fetch fail.
        return 0


_NOMADS_MIN_RANGE_BYTES = 32 * 1024 * 1024


def _prefer_nomads_subset(expected_range_bytes):
    """Use CGI subsetting only when indexed ranges would be a large transfer."""
    size = max(0, int(expected_range_bytes or 0))
    return size == 0 or size > _NOMADS_MIN_RANGE_BYTES


def _is_ecmwf_open_data(config) -> bool:
    """Return whether ``config`` uses an ECMWF open-data eccodes index."""
    return str(getattr(config, "herbie_model", "")).lower() in {"ifs", "aifs"}




def _point_backends_enabled():
    """Return whether Zarr/NOMADS point and subregion routes are enabled."""
    mode = os.environ.get("SHARPMOD_POINT_BACKENDS", "auto").strip().lower()
    return mode not in {"0", "false", "no", "off", "grib"}


def _direct_grib_enabled():
    """Return whether local GRIB point decoding may bypass xarray."""
    mode = os.environ.get("SHARPMOD_GRIB_DECODER", "auto").strip().lower()
    if mode not in {"auto", "direct", "xarray"}:
        raise RetrievalError(
            "invalid SHARPMOD_GRIB_DECODER value %r; expected auto, direct, "
            "or xarray" % mode
        )
    return mode != "xarray"


def _direct_grib_required():
    return os.environ.get(
        "SHARPMOD_GRIB_DECODER", "auto"
    ).strip().lower() == "direct"


def _local_grib_path(value):
    """Return one complete downloaded GRIB path, if ``value`` contains one."""
    candidates = value if isinstance(value, (tuple, list)) else (value,)
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            path = Path(os.fspath(candidate)).expanduser()
        except TypeError:
            continue
        if _valid_grib(path):
            return path.resolve()
    return None


def spatial_cache_key(config, lat, lon):
    """Return a point identity when an enabled backend can subset spatially."""
    if config.key in POINT_PROVIDER_KEYS:
        lon180 = ((float(lon) + 180.0) % 360.0) - 180.0
        return f"{float(lat):.4f},{lon180:.4f}"
    if not _point_backends_enabled():
        return None
    if config.key != "hrrr" and not nomads_supported(config):
        return None
    lon180 = ((float(lon) + 180.0) % 360.0) - 180.0
    return f"{float(lat):.4f},{lon180:.4f}"


def cached_source_fields_compatible(
    config,
    source_fields,
    contract_version=None,
) -> bool:
    """Return whether a cached subset satisfies the current extraction contract."""
    if contract_version is not None:
        from sharpmod.models.model_disk_cache import MODEL_CACHE_CONTRACT_VERSION
        try:
            if int(contract_version) != MODEL_CACHE_CONTRACT_VERSION:
                return False
        except (TypeError, ValueError, OverflowError):
            return False
    if _is_ecmwf_open_data(config):
        return supports_ifs_surface_merge(source_fields)
    return supports_noaa_surface_merge(source_fields)


def hrrr_zarr_candidate(config, fxx, lat=None, lon=None):
    """Whether this request may use the point-only HRRR analysis archive."""
    mode = os.environ.get("SHARPMOD_HRRR_BACKEND", "auto").strip().lower()
    point_mode = os.environ.get("SHARPMOD_POINT_BACKENDS", "auto").strip().lower()
    return (
        config.key == "hrrr"
        and int(fxx) == 0
        and lat is not None
        and lon is not None
        and mode not in {"0", "false", "no", "off", "grib"}
        and point_mode not in {"0", "false", "no", "off", "grib"}
    )


_SURFACE_CONTRACT_EXPRESSIONS = (
    ("surface_pressure", r":(?:PRES|sp):(?:surface|sfc):"),
    ("surface_height", r":(?:HGT):surface:|:(?:z):sfc:"),
    ("two_metre_temperature", r":TMP:2 m above ground:|:2t:sfc:"),
    (
        "two_metre_moisture",
        r":(?:DPT|RH|SPFH):2 m above ground:|:2d:sfc:",
    ),
    ("ten_metre_u_wind", r":UGRD:10 m above ground:|:10u:sfc:"),
    ("ten_metre_v_wind", r":VGRD:10 m above ground:|:10v:sfc:"),
)


def surface_contract_status(inventory) -> dict:
    """Return an explainable verified-ground status for one GRIB inventory."""
    try:
        rows = tuple(str(value) for value in inventory["search_this"])
    except (KeyError, TypeError):
        rows = ()
    joined = "\n".join(rows)
    present = tuple(
        name
        for name, expression in _SURFACE_CONTRACT_EXPRESSIONS
        if re.search(expression, joined, re.IGNORECASE)
    )
    required = tuple(name for name, _expression in _SURFACE_CONTRACT_EXPRESSIONS)
    missing = tuple(name for name in required if name not in present)
    return {
        "complete": not missing,
        "required": required,
        "present": present,
        "missing": missing,
    }


def _inventory_has_surface_contract(inventory) -> bool:
    """Return whether an inventory exposes every verified ground input."""
    return bool(surface_contract_status(inventory)["complete"])






def _invariant_height_plan(config):
    """Return the ``(search, fields)`` naming this product's terrain height."""
    if _is_ecmwf_open_data(config):
        return IFS_INVARIANT_SEARCH, IFS_INVARIANT_FIELDS
    return NOAA_INVARIANT_SEARCH, NOAA_INVARIANT_FIELDS


def _probe_cancel_if_requested(cancelled) -> None:
    if cancelled is not None and cancelled():
        raise DownloadCancelled("forecast-model availability probe cancelled")










def _selected_valid(ds_t, selected_time, run_dt, fxx):
    _, vt = _coord_values(ds_t, ("valid_time",))
    if vt is not None and vt.size:
        return _as_datetime(vt.reshape(-1)[0])
    if selected_time is not None:
        return _as_datetime(selected_time)
    return run_dt + timedelta(hours=int(fxx))












def cleanup_transient_data(npz_path=None, download_dir=None):
    """Remove fetched model artifacts while preserving a rendered PNG.

    ``npz_path`` and its JSON sidecar may live outside ``download_dir`` (the
    CLI permits an explicit output path), so both are removed before the
    isolated Herbie download tree. Missing paths are intentionally harmless so
    this helper is safe in render/fetch failure ``finally`` blocks.
    """
    if npz_path:
        npz_path = os.fspath(npz_path)
        _quiet_remove(npz_path)
        _quiet_remove(os.path.splitext(npz_path)[0] + ".json")
    if download_dir:
        shutil.rmtree(os.fspath(download_dir), ignore_errors=True)


_HERBIE_PROBE_REQUEST_LOCK = threading.RLock()










def _parse_time(value):
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")




from sharpmod.tools.model_extract_retrieval import (  # noqa: E402
    _combine_grib_payloads,
    _cfs_surface_companion,
    _f000_publishes_surface_height,
    _surface_height_companion,
    _retrieve_rrfs_dataset,
    _retrieve_dataset
)


from sharpmod.tools.model_extract_run import (  # noqa: E402
    _point_neighborhood,
    _merge_point_datasets,
    _decode_local_point,
    _xarray_point_columns,
    extract
)


from sharpmod.tools.model_extract_probe import (  # noqa: E402
    _BoundedHerbieRequests,
    _bounded_herbie_probe_requests,
    probe,
    probe_recent_surface_contract
)


from sharpmod.tools.model_extract_config import (  # noqa: E402
    _LocalGribDataset,
    ModelConfig,
    ProviderCapability,
    forecast_hours,
    provider_capability,
    domain_intersects_bounds,
    domain_contains_bounds
)


from sharpmod.tools.model_extract_cli import (  # noqa: E402
    _prepare_windows_eccodes_runtime,
    require_runtime_dependencies,
    _planned_model_search,
    main
)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
