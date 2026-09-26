"""Shared request types and provider/domain validation for point-model extraction.

These definitions constrain the probe, retrieval, and run stages so model capabilities
and geographic bounds are normalized consistently before data acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from sharpmod import backends as _backends
from sharpmod.providers import eccc_geomet
from sharpmod.providers import openmeteo
from sharpmod.providers import rrfs_nomads
from sharpmod.models.model_fields import NOAA_SURFACE_FIELDS
from sharpmod.models.model_sources import nomads_supported
from sharpmod.tools.era5_extract import RetrievalError
from sharpmod.tools.era5_extract import _LAT_COORDS
from sharpmod.tools.era5_extract import _LEVEL_COORDS
from sharpmod.tools.era5_extract import _LON_COORDS
from sharpmod.tools.era5_extract import _VAR_10M_U
from sharpmod.tools.era5_extract import _VAR_10M_V
from sharpmod.tools.era5_extract import _VAR_2M_DEWPOINT
from sharpmod.tools.era5_extract import _VAR_2M_TEMP
from sharpmod.tools.era5_extract import _VAR_SURFACE_HEIGHT
from sharpmod.tools.era5_extract import _VAR_SURFACE_PRESSURE
from sharpmod.tools.era5_extract import _VAR_U
from sharpmod.tools.era5_extract import _VAR_V
from sharpmod.tools.era5_extract import _coord_values
from sharpmod.tools.era5_extract import _select_time
from sharpmod.tools.era5_extract import _surface_relative_vorticity_from_wind_grid
from sharpmod.tools.era5_extract import select_nearest_grid_point
from sharpmod.upstream.upstream_warnings import xarray_new_combine_defaults
import numpy as np
import os
import threading
from sharpmod.tools import model_extract as _api


class _LocalGribDataset:
    """Cache-owned local GRIB source with a lazy compatibility dataset.

    The normal path decodes a compact point directly from ``path``. If a GRIB
    layout is not supported by that decoder, ``fallback_dataset`` opens the
    same file with cfgrib's persistent on-disk index and keeps the lazy xarray
    object under the existing model-hour lease.
    """

    def __init__(self, path):
        self.path = Path(path).expanduser().resolve(strict=True)
        self._fallback_sources = None
        self._pressure_sources = None
        self._vorticity_source = None
        self._lock = threading.RLock()

    def _open_vorticity_source(self):
        """Open only the compatible pressure group needed for a wind stencil."""
        with self._lock:
            if self._vorticity_source is not None:
                return self._vorticity_source
            try:
                import cfgrib
            except ImportError as exc:
                raise RetrievalError(
                    "forecast model wind-gradient decoding requires cfgrib"
                ) from exc

            failures = []
            filters = (
                # U/V share parameter category 2 in GRIB2. Selecting that
                # category avoids cfgrib choosing another pressure group when
                # a product (notably GEFS) stores thermodynamic and wind
                # fields in separate hypercubes with the same level type.
                {"typeOfLevel": "isobaricInhPa", "parameterCategory": 2},
                {"typeOfLevel": "isobaricInPa", "parameterCategory": 2},
                # Retain compatibility with GRIB layouts that do not expose a
                # GRIB2 parameterCategory key.
                {"typeOfLevel": "isobaricInhPa"},
                {"typeOfLevel": "isobaricInPa"},
            )
            for filter_by_keys in filters:
                source = None
                try:
                    source = cfgrib.open_dataset(
                        os.fspath(self.path),
                        filter_by_keys=filter_by_keys,
                        errors="ignore",
                    )
                    variables = set(source.data_vars)
                    if not variables.intersection(_VAR_U) \
                            or not variables.intersection(_VAR_V):
                        raise RetrievalError(
                            "pressure group has no compatible u/v wind pair"
                        )
                    _level_name, levels = _coord_values(
                        source, _LEVEL_COORDS
                    )
                    if levels is None or not np.asarray(levels).size:
                        raise RetrievalError(
                            "pressure group has no pressure coordinate"
                        )
                except Exception as exc:
                    failures.append(exc)
                    if source is not None:
                        try:
                            source.close()
                        except Exception:
                            pass
                    continue
                self._vorticity_source = source
                return source
            detail = failures[-1] if failures else "no pressure group"
            raise RetrievalError(
                "could not open a targeted pressure-wind group: %s" % detail
            )

    def surface_wind_vorticity(self, lat, lon, run_dt):
        """Decode one pressure-level u/v neighbor stencil, not every field."""
        try:
            return _backends.decode_grib_wind_vorticity(
                self.path, lat, lon
            )
        except Exception as exc:
            # Reduced/unstructured or otherwise unusual GRIB grids retain the
            # proven cfgrib compatibility path below.
            _api._LOGGER.info(
                "grib_decode.direct_wind_stencil_fallback path=%s reason=%s",
                self.path,
                exc,
            )
        with self._lock:
            source = self._open_vorticity_source()
            ds_t, _selected_time = _select_time(source, run_dt)
            _, lats = _coord_values(ds_t, _LAT_COORDS)
            _, lons = _coord_values(ds_t, _LON_COORDS)
            _, levels = _coord_values(ds_t, _LEVEL_COORDS)
            if lats is None or lons is None or levels is None:
                raise RetrievalError(
                    "targeted pressure-wind group is missing coordinates"
                )
            lon_req = float(lon)
            try:
                if np.nanmin(lons) >= 0.0 and lon_req < 0.0:
                    lon_req += 360.0
            except Exception:
                pass
            index_tuple, _selected_lat, _selected_lon = \
                select_nearest_grid_point(lats, lons, float(lat), lon_req)
            value = _surface_relative_vorticity_from_wind_grid(
                ds_t, index_tuple, levels
            )
            if value is None:
                raise RetrievalError(
                    "targeted pressure-wind group could not produce vorticity"
                )
            return float(value)

    def _open_fallback_sources(self):
        with self._lock:
            if self._pressure_sources is not None:
                return self._pressure_sources
            try:
                import cfgrib
            except ImportError as exc:
                raise RetrievalError(
                    "forecast model fallback decoding requires cfgrib"
                ) from exc

            sources = ()
            try:
                # Keep cfgrib's default ``{path}.{short_hash}.idx``. Unlike the
                # previous no-index profiling path, this inventory is reused by
                # later opens while the model-hour cache owns the directory.
                with xarray_new_combine_defaults():
                    sources = tuple(cfgrib.open_datasets(
                        os.fspath(self.path)
                    ))
                point_field_names = {
                    *_VAR_SURFACE_PRESSURE,
                    *_VAR_SURFACE_HEIGHT,
                    *_VAR_2M_TEMP,
                    *_VAR_2M_DEWPOINT,
                    *_VAR_10M_U,
                    *_VAR_10M_V,
                }
                pressure_sources = tuple(
                    source for source in sources
                    if any(name in source.coords for name in _LEVEL_COORDS)
                    or not point_field_names.isdisjoint(source.data_vars)
                    or (
                        "z" in source.data_vars
                        and not any(
                            name in source.coords for name in _LEVEL_COORDS
                        )
                    )
                )
                if not pressure_sources:
                    raise RetrievalError(
                        "cfgrib returned no pressure-level dataset"
                    )
            except BaseException:
                for source in sources:
                    try:
                        source.close()
                    except Exception:
                        pass
                raise
            self._fallback_sources = sources
            self._pressure_sources = pressure_sources
            return pressure_sources

    def fallback_point_dataset(self, lat, lon, run_dt):
        """Merge only a small neighborhood around the requested grid point."""
        return _api._merge_point_datasets(
            self._open_fallback_sources(), lat, lon, run_dt
        )

    def close(self):
        with self._lock:
            sources = self._fallback_sources or ()
            vorticity_source = self._vorticity_source
            self._fallback_sources = None
            self._pressure_sources = None
            self._vorticity_source = None
        for source in sources:
            try:
                source.close()
            except Exception:
                pass
        if vorticity_source is not None:
            try:
                vorticity_source.close()
            except Exception:
                pass


@dataclass(frozen=True)
class ModelConfig:
    """Herbie-backed forecast model configuration."""

    key: str
    label: str
    herbie_model: str
    product: str
    search: str = _api.NOAA_PRESSURE_SEARCH
    cycles: tuple[int, ...] = (0, 6, 12, 18)
    default_fxx: int = 0
    fxx_values: tuple[int, ...] = ()
    domain: str = "Global"
    domain_bounds: tuple[float, float, float, float] = (
        -180.0, 180.0, -90.0, 90.0)
    kwargs: dict[str, object] = field(default_factory=dict)
    notes: str = ""
    domain_outline: tuple[tuple[float, float], ...] = ()
    # Set when a product is still described here but cannot currently produce a
    # sounding, so it is withheld from every selectable model list.
    unavailable_reason: str = ""
    # Nominal horizontal spacing of the published grid, in kilometres. Area
    # sampling (see sharpmod.analysis.box_sounding) snaps its request spacing to a
    # multiple of this so a box never asks for two soundings out of one grid
    # cell -- that would download and decode the same column twice and then
    # draw a false gradient between the duplicates. Global products are quoted
    # at their mid-latitude spacing because that is where the sampler is used;
    # a degree-based grid narrows toward the poles, so this is the conservative
    # (largest) value. Zero means "unknown", and the sampler then falls back to
    # UNKNOWN_GRID_SPACING_KM rather than guessing per product.
    grid_spacing_km: float = 0.0


@dataclass(frozen=True)
class ProviderCapability:
    """Qt-independent description of one forecast-model provider adapter."""

    model_key: str
    provider: str
    domain: str
    domain_bounds: tuple[float, float, float, float]
    cycles: tuple[int, ...]
    forecast_hours: tuple[int, ...]
    members: tuple[str, ...]
    fields: tuple[str, ...]
    levels: str
    archive_window: str | None
    transports: tuple[str, ...]
    domain_outline: tuple[tuple[float, float], ...] = ()


def forecast_hours(model, cycle_hour=None):
    """Return selectable forecast hours for ``model``.

    Most products have one cadence. HRRR publishes longer forecasts on major
    synoptic cycles than on its off-hour cycles, and ECMWF IFS runs a short
    cut-off forecast at 06Z and 18Z that stops at F144.

    RRFS needs no such rule: it publishes pressure levels only on the synoptic
    cycles, so ``cycle_hours`` already excludes the off-hour cycles rather than
    advertising a shorter forecast for them.
    """
    cfg = _api._coerce_config(model)
    values = cfg.fxx_values or (cfg.default_fxx,)
    if cfg.key in _api.OPENMETEO_POINT_KEYS:
        # The provider adapter owns its own cadence and cut-off rules, so ask it
        # rather than restating them here.
        return openmeteo.get_capability(cfg.key).hours_for_cycle(cycle_hour)
    if cfg.key == "hrrr" and cycle_hour is not None \
            and int(cycle_hour) not in (0, 6, 12, 18):
        return tuple(v for v in values if int(v) <= 18)
    if cfg.key == "ecmwf-ifs" and cycle_hour is not None \
            and int(cycle_hour) in _api.IFS_SHORT_CUTOFF_CYCLES:
        return tuple(
            v for v in values if int(v) <= _api.IFS_SHORT_CUTOFF_MAX_FXX
        )
    return values


def provider_capability(model, cycle_hour=None):
    """Return the normalized capability contract for one model adapter."""
    cfg = _api._coerce_config(model)
    if cfg.key in _api.OPENMETEO_POINT_KEYS:
        capability = openmeteo.get_capability(cfg.key)
        return _api.ProviderCapability(
            model_key=cfg.key,
            provider=capability.provider,
            domain=capability.domain,
            domain_bounds=capability.domain_bounds,
            cycles=capability.cycles,
            forecast_hours=capability.hours_for_cycle(cycle_hour),
            members=(),
            fields=capability.fields,
            levels="%d requested pressure levels, %d hPa to %d hPa" % (
                len(capability.pressure_levels),
                max(capability.pressure_levels),
                min(capability.pressure_levels),
            ),
            archive_window="runs archived from %s"
            % capability.archive_start.isoformat(),
            transports=(openmeteo.TRANSPORT,),
            domain_outline=capability.domain_outline,
        )
    if cfg.key in _api.ECCC_POINT_KEYS:
        capability = eccc_geomet.get_capability(cfg.key)
        return _api.ProviderCapability(
            model_key=cfg.key,
            provider=capability.provider,
            domain=capability.domain,
            domain_bounds=capability.domain_bounds,
            cycles=capability.cycles,
            forecast_hours=capability.forecast_hours,
            members=(),
            fields=capability.fields,
            levels="%d published pressure levels" % len(
                capability.pressure_levels
            ),
            archive_window=capability.archive_window,
            transports=capability.transports,
            domain_outline=capability.domain_outline,
        )
    if cfg.key in _api.DIRECT_GRIB_KEYS:
        return _api.ProviderCapability(
            model_key=cfg.key,
            provider=rrfs_nomads.PROVIDER,
            domain=cfg.domain,
            domain_bounds=cfg.domain_bounds,
            cycles=_api.cycle_hours(cfg),
            forecast_hours=_api.forecast_hours(cfg, cycle_hour=cycle_hour),
            members=(),
            # No VVEL is published and DZDT is not a usable substitute, so
            # this is the one enabled GRIB product with no omega field.
            fields=tuple(dict.fromkeys((
                "HGT", "TMP", "UGRD", "VGRD", "RH", "ABSV",
                *NOAA_SURFACE_FIELDS,
            ))),
            levels="45 published pressure levels, 1000 hPa to 2 hPa",
            archive_window="recent runs only; NOMADS purges older files",
            transports=(
                rrfs_nomads.TRANSPORT, "verified-surface-companion",
            ),
            domain_outline=cfg.domain_outline,
        )
    transports = ["herbie", "indexed-ranges"]
    if nomads_supported(cfg):
        transports.insert(0, "nomads-subregion")
    if cfg.key == "hrrr":
        transports.insert(0, "hrrr-zarr-point")
    member = cfg.kwargs.get("member")
    fields = (
        "HGT", "TMP", "UGRD", "VGRD", "RH-or-SPFH",
        "VVEL-or-DZDT", "ABSV-when-published",
    )
    if cfg.herbie_model in {"ifs", "aifs"}:
        fields = (
            "gh", "t", "u", "v", "r-or-q", "w-when-published",
            "vo-when-published",
        )
    return _api.ProviderCapability(
        model_key=cfg.key,
        provider="Herbie",
        domain=cfg.domain,
        domain_bounds=cfg.domain_bounds,
        cycles=_api.cycle_hours(cfg),
        forecast_hours=_api.forecast_hours(cfg, cycle_hour=cycle_hour),
        members=(str(member),) if member is not None else (),
        fields=fields,
        levels="all published pressure levels",
        archive_window=None,
        transports=tuple(transports),
        domain_outline=cfg.domain_outline,
    )


def domain_intersects_bounds(model, bounds):
    """Return whether a model domain intersects a map extent.

    ``bounds`` is ``(lon0, lon1, lat0, lat1)`` in degrees.
    """
    cfg = _api._coerce_config(model)
    if cfg.domain_outline:
        blo0, blo1, bla0, bla1 = bounds
        lat_mid = (float(bla0) + float(bla1)) / 2.0
        samples = []
        for left, right in _api._longitude_segments(blo0, blo1):
            lon_mid = (left + right) / 2.0
            samples.extend(
                (lat, lon)
                for lat in (bla0, lat_mid, bla1)
                for lon in (left, lon_mid, right)
            )
        if any(_api.point_in_domain(cfg, lat, lon) for lat, lon in samples):
            return True
        return any(
            bla0 <= lat <= bla1
            and any(left <= lon <= right for left, right in
                    _api._longitude_segments(blo0, blo1))
            for lon, lat in cfg.domain_outline
        )
    lon0, lon1, lat0, lat1 = cfg.domain_bounds
    blo0, blo1, bla0, bla1 = bounds
    if lat1 < bla0 or lat0 > bla1:
        return False
    return any(
        not (right < other_left or left > other_right)
        for left, right in _api._longitude_segments(lon0, lon1)
        for other_left, other_right in _api._longitude_segments(blo0, blo1)
    )


def domain_contains_bounds(model, bounds):
    """Return whether a model domain fully contains a map extent."""
    cfg = _api._coerce_config(model)
    if cfg.domain_outline:
        blo0, blo1, bla0, bla1 = bounds
        return all(
            _api.point_in_domain(cfg, lat, lon)
            for left, right in _api._longitude_segments(blo0, blo1)
            for lon in (left, right)
            for lat in (bla0, bla1)
        )
    lon0, lon1, lat0, lat1 = cfg.domain_bounds
    blo0, blo1, bla0, bla1 = bounds
    if lat0 > bla0 or lat1 < bla1:
        return False
    model_segments = _api._longitude_segments(lon0, lon1)
    return all(
        any(left <= other_left and right >= other_right
            for left, right in model_segments)
        for other_left, other_right in _api._longitude_segments(blo0, blo1)
    )
