"""Open-Meteo Single Runs point soundings.

A Qt-free provider adapter that turns one Open-Meteo pressure-level response
into the same portable point-sounding contract the Herbie, ECCC GeoMet, IFS,
ERA5, UWyo, and WRF paths already write. It needs no GRIB runtime.

Scope: this adapter deliberately enables exactly one model, ECMWF IFS on its
native 9 km grid. Open-Meteo advertises forty-odd identifiers, but advertising a
model and a variable independently does not prove that model publishes a
complete sounding at every level, and each additional entry needs its own live
capability audit. One vetted model is worth more than a long list of maybes.

Design notes worth knowing before changing anything here:

*Single Runs, not the Forecast API.* The interface selects a run and a forecast
hour, so the request names an explicit ``run``. The operational Forecast API
stitches the newest run of each model into one series and can no longer say
which initialisation produced a value, which makes it unusable for a sounding
that claims to depict a specific cycle.

*Distinct from the built-in ECMWF route.* This application already reads ECMWF
through Herbie against the 0.25 degree open-data feed. This route is not a
replacement: it reaches the native 9 km grid and archived runs the open-data
feed no longer holds. Both are selectable, and the labels say which is which.

*Completeness is enforced per request, not merely declared.* A level missing
temperature, moisture, wind, or height is dropped; a profile that loses its
verified ground row or fails physical QC is refused rather than rendered. So an
enabled model can produce a clear failure but never a quietly wrong sounding.

*Vertical velocity is deliberately omitted.* Open-Meteo publishes
``vertical_velocity_*hPa`` as geometric velocity in m/s, while ``omeg`` in this
contract is a pressure velocity. Passing one through as the other would be
scientifically wrong, so ``omeg`` is left missing and one variable per level is
saved from the weighted request cost.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sharpmod.providers.openmeteo_access import (
    OpenMeteoAccess,
    OpenMeteoAccessError,
    OpenMeteoRateLimited,
    ParameterRangeError,
    RetrievalError,
    fetch_json,
    resolve_access,
)
from sharpmod.export_paths import export_file_path

_LOGGER = logging.getLogger(__name__)

#: Provider label used for attribution in sidecars and the interface.
PROVIDER = "Open-Meteo"

#: Attribution required by the provider licence (CC BY 4.0). The originating
#: centre is named separately per capability, because the licence covering the
#: underlying model output is theirs rather than Open-Meteo's.
ATTRIBUTION = (
    "Weather data by Open-Meteo.com (CC BY 4.0), derived from the originating "
    "centre's model output"
)

#: Transport tag recorded in cache metadata and sidecars.
TRANSPORT = "open-meteo-single-runs-point"

#: Every pressure level the Single Runs API can return, descending. Taken from
#: the published OpenAPI schema; note there is no 975 hPa level, and the ladder
#: is sparse above 100 hPa.
ALL_PRESSURE_LEVELS: tuple[int, ...] = (
    1000, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400,
    350, 300, 275, 250, 225, 200, 175, 150, 125, 100, 50, 10,
)

#: Levels worth ever asking for. Stopping at 100 hPa keeps the two
#: stratospheric levels out of the request: nothing this application computes
#: reaches them, and every level costs request width against the user's
#: allowance. This is the candidate set an audit probes, NOT what any single
#: model returns -- see ``MEASURED_LADDERS``.
PRESSURE_LEVELS: tuple[int, ...] = tuple(
    level for level in ALL_PRESSURE_LEVELS if level >= 100)

#: Pressure ladders each model actually populates, measured against the live
#: service (run 2026-08-28 00Z, F012; evidence in the capability audit).
#:
#: The published schema advertises 26 levels for every model, but no model fills
#: more than twelve of them: 950, 900, 750, 650, 550, 450, 350, 275, 225, 175,
#: and 125 hPa came back empty everywhere. Requesting a level a model never
#: populates buys nothing and costs request width, which is billable, so every
#: capability carries its measured ladder rather than the candidate set.
_L12_TO_100 = (1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100)
_L11_TO_150 = (1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150)
_L11_HRDPS = (1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 100)
_L10_ICON_D2 = (1000, 850, 800, 700, 600, 500, 400, 300, 250, 200)
_L10_TO_200 = (1000, 925, 850, 700, 600, 500, 400, 300, 250, 200)
_L9_TO_250 = (1000, 925, 850, 700, 600, 500, 400, 300, 250)
_L8_TO_300 = (1000, 925, 850, 700, 600, 500, 400, 300)

#: Every provider identifier measured as usable, with its ladder. Only the
#: enabled ones appear in :data:`CAPABILITIES`; the rest are recorded so that
#: promoting one later is a manifest entry rather than another live audit.
MEASURED_LADDERS: dict[str, tuple[int, ...]] = {
    "icon_global": _L12_TO_100,
    "ukmo_global_deterministic_10km": _L12_TO_100,
    "cma_grapes_global": _L12_TO_100,
    "meteofrance_arome_france": _L12_TO_100,
    "icon_eu": _L11_TO_150,
    "cmc_gem_hrdps": _L11_HRDPS,
    "icon_d2": _L10_ICON_D2,
    "meteofrance_arpege_world": _L10_TO_200,
    "meteofrance_arpege_europe": _L10_TO_200,
    "ukmo_uk_deterministic_2km": _L9_TO_250,
    "jma_gsm": _L8_TO_300,
    "jma_msm": _L8_TO_300,
}

#: Pressure-level families requested for every sounding. Each expands to one
#: variable per level, so this tuple's length multiplies the request width.
PRESSURE_FAMILIES: tuple[str, ...] = (
    "temperature",
    "dew_point",
    "wind_speed",
    "wind_direction",
    "geopotential_height",
)

#: Surface fields completing the verified ground row. Without all five the
#: profile's lowest isobar would be presented as if it were ground.
SURFACE_VARIABLES: tuple[str, ...] = (
    "surface_pressure",
    "temperature_2m",
    "dew_point_2m",
    "wind_speed_10m",
    "wind_direction_10m",
)

#: Fixed query parameters. ``elevation=nan`` asks the provider not to
#: terrain-downscale and to answer on its own grid cell, which is what a model
#: sounding must depict; ``cell_selection=nearest`` stops a land/sea preference
#: from silently moving the point.
BASE_QUERY: dict[str, str] = {
    "timezone": "GMT",
    "timeformat": "unixtime",
    "cell_selection": "nearest",
    "elevation": "nan",
    "temperature_unit": "celsius",
    "wind_speed_unit": "ms",
}

#: Minimum usable levels, and the pressure a profile must reach, before it is
#: worth rendering. A ladder that stops in the mid troposphere cannot support
#: the parcel and shear calculations this application exists to show.
MIN_USABLE_LEVELS = 8
REQUIRED_TOP_PRESSURE_HPA = 300.0

#: Knots per metre per second, matching the constant the other adapters use.
KNOTS_PER_MS = 1.94384449

#: Missing sentinel understood by the portable sounding loader.
MISSING = -9999.0


def _hours(*segments: tuple[int, int, int]) -> tuple[int, ...]:
    """Build a forecast-hour tuple from ``(start, stop, step)`` segments.

    Cadence changes with lead time, so the hours are expressed as segments
    rather than one range. ``stop`` is inclusive.
    """
    hours: list[int] = []
    for start, stop, step in segments:
        hours.extend(range(int(start), int(stop) + 1, int(step)))
    return tuple(sorted(set(hours)))


@dataclass(frozen=True)
class OpenMeteoCapability:
    """One vetted, selectable Open-Meteo deterministic model.

    ``pressure_levels`` has no default on purpose. The published schema
    advertises the same 26 levels for every model while no model fills more than
    twelve, so a default would let an unmeasured entry inherit a ladder the
    provider never populates. Requiring it forces each capability to carry
    audit evidence -- see :data:`MEASURED_LADDERS`.

    The fetch path still validates the requested hour against the run's actual
    series and drops levels that come back incomplete, so a stale entry yields
    an explicit error or a shorter profile rather than a wrong sounding.
    """

    model_key: str
    api_model: str
    label: str
    #: Originating centre, named separately from Open-Meteo because the
    #: underlying model licence is theirs.
    origin: str
    resolution: str
    domain: str
    domain_bounds: tuple[float, float, float, float]
    cycles: tuple[int, ...]
    forecast_hours: tuple[int, ...]
    #: Source model output cadence in hours. Where this exceeds one, Open-Meteo
    #: may still answer hourly by interpolation, which the interface must not
    #: present as native model output.
    native_cadence_hours: int
    archive_start: date
    notes: str
    #: Measured, not assumed. See the class docstring for why there is no
    #: default.
    pressure_levels: tuple[int, ...]
    #: Cycles publishing a shortened forecast, and the hour they stop at.
    short_cutoff_cycles: tuple[int, ...] = ()
    short_cutoff_max_fxx: int = 0
    domain_outline: tuple[tuple[float, float], ...] = ()
    #: Non-empty withholds the model from every selectable list.
    withheld_reason: str = ""

    @property
    def provider(self) -> str:
        return PROVIDER

    @property
    def fields(self) -> tuple[str, ...]:
        return PRESSURE_FAMILIES + SURFACE_VARIABLES

    @property
    def interpolated_hours(self) -> bool:
        """Whether hourly values between native steps are interpolated."""
        return self.native_cadence_hours > 1

    def hours_for_cycle(self, cycle_hour: int | None = None) -> tuple[int, ...]:
        """Return forecast hours available from ``cycle_hour``."""
        if cycle_hour is None or not self.short_cutoff_cycles:
            return self.forecast_hours
        if int(cycle_hour) not in self.short_cutoff_cycles:
            return self.forecast_hours
        return tuple(
            hour for hour in self.forecast_hours
            if hour <= self.short_cutoff_max_fxx)

    def variable_count(self) -> int:
        """Number of hourly variables one request asks for.

        Drives the weighted-usage estimate: Open-Meteo counts a call as more
        than one unit once it exceeds ten variables.
        """
        return len(PRESSURE_FAMILIES) * len(self.pressure_levels) \
            + len(SURFACE_VARIABLES)


#: Open-Meteo's own documentation states most models are archived from this
#: date. A run older than this is refused before a request is spent on it.
ARCHIVE_START = date(2026, 4, 2)

_GLOBAL_BOUNDS = (-180.0, 180.0, -90.0, 90.0)

#: The one enabled model. Keyed by a namespaced SHARPpy key so it can never be
#: confused with, or overwrite, a built-in Herbie route of the same name.
CAPABILITIES: dict[str, OpenMeteoCapability] = {
    "openmeteo-icon-global": OpenMeteoCapability(
        model_key="openmeteo-icon-global",
        api_model="icon_global",
        label="ICON Global 11 km (Open-Meteo)",
        origin="Deutscher Wetterdienst (DWD)",
        resolution="11 km (0.125 degree grid)",
        domain="Global",
        domain_bounds=_GLOBAL_BOUNDS,
        cycles=(0, 6, 12, 18),
        # DWD publishes ICON global hourly to F078 and three-hourly after it,
        # reaching F180 from 00Z/12Z and F120 from 06Z/18Z. Open-Meteo will
        # interpolate the gaps back to hourly beyond F078; those hours are not
        # model output, so only the native steps are offered.
        forecast_hours=_hours((0, 78, 1), (81, 180, 3)),
        native_cadence_hours=1,
        archive_start=ARCHIVE_START,
        short_cutoff_cycles=(6, 18),
        short_cutoff_max_fxx=120,
        pressure_levels=MEASURED_LADDERS["icon_global"],
        notes=(
            "DWD ICON global through Open-Meteo, which needs no GRIB runtime. "
            "Twelve pressure levels from 1000 to 100 hPa, measured against the "
            "live service. Hours past F078 are three-hourly because that is "
            "DWD's own cadence; 06Z and 18Z runs stop at F120"
        ),
    ),
}


#: Identifiers deliberately not selectable, with a reason a reader can act on.
#: Only the ones someone would plausibly go looking for are listed; the point is
#: to explain an absence, not to enumerate the provider's whole catalogue.
WITHHELD_API_MODELS: dict[str, str] = {
    "best_match": (
        "Best Match combines several models into one series, so a sounding "
        "could not name the model that produced it."
    ),
    "ecmwf_ifs": (
        "Open-Meteo serves ECMWF IFS without pressure-level fields. A live "
        "request returns the run and every surface variable, then zero levels "
        "for all five pressure families, so no sounding can be built from it."
    ),
    "ecmwf_ifs025": (
        "The same ECMWF IFS at a coarser 0.25 degree grid, and it too publishes "
        "no pressure-level fields."
    ),
    "ecmwf_aifs025_single": (
        "ECMWF AIFS is already a built-in route, and Open-Meteo offers it only "
        "at 0.25 degrees, so there is no resolution gain."
    ),
    "ncep_gfs_global": "GFS is already a built-in route through Herbie.",
    "ncep_hrrr_conus": "HRRR is already a built-in route through Herbie.",
    "ncep_nam_conus": "NAM is already a built-in route through Herbie.",
    "cmc_gem_gdps": "GDPS is already a built-in route through ECCC GeoMet.",
    "cmc_gem_rdps": "RDPS is already a built-in route through ECCC GeoMet.",
    "jma_gsm": (
        "Audited usable, but only eight levels reaching 300 hPa, which is "
        "exactly the minimum this adapter accepts. One level withdrawn "
        "upstream would disqualify it mid-season."
    ),
    "jma_msm": (
        "Audited usable, but only eight levels reaching 300 hPa, the same "
        "margin-free ladder as JMA GSM."
    ),
    "bom_access_global": (
        "The service answers this identifier with a body that is not JSON. "
        "Until that response is understood it cannot be trusted."
    ),
    "cma_grapes_global": (
        "Audited usable with twelve levels to 100 hPa. Not enabled yet only "
        "because one global model is enough to start with."
    ),
    "ukmo_global_deterministic_10km": (
        "Audited usable with twelve levels to 100 hPa, but its CC BY-SA licence "
        "carries stronger attribution obligations than the other models here."
    ),
    "ncep_hgefs025_ensemble_mean": (
        "An ensemble mean is not a physically consistent profile and needs "
        "separate member semantics."
    ),
}

#: Suffix marking every seamless identifier, which stitches a provider's models
#: together and therefore cannot name one initialisation.
_SEAMLESS_SUFFIX = "_seamless"


def namespaced_key(api_model: str) -> str:
    """Return the SHARPpy key for a provider identifier."""
    return "openmeteo-%s" % str(api_model or "").strip().lower().replace(
        "_", "-")


def _api_model_for_key(model_key: str) -> str:
    """Return the provider identifier a namespaced SHARPpy key refers to."""
    text = str(model_key or "").strip().lower()
    prefix = "openmeteo-"
    if text.startswith(prefix):
        text = text[len(prefix):]
    return text.replace("-", "_")


def unsupported_models() -> dict[str, str]:
    """Return withheld models keyed by SHARPpy key, with a usable reason.

    Namespaced rather than raw provider identifiers, because this merges into
    the facade's own unsupported-model map, which is keyed the same way as
    everything else a user can type.
    """
    reasons = {
        namespaced_key(api_model): reason
        for api_model, reason in WITHHELD_API_MODELS.items()
    }
    for capability in CAPABILITIES.values():
        if capability.withheld_reason:
            reasons[capability.model_key] = capability.withheld_reason
    return reasons


def is_selectable_api_model(api_model: str) -> bool:
    """Report whether ``api_model`` may ever be requested.

    Seamless, best-match, and ensemble identifiers are rejected by shape as well
    as by list, so a provider adding one later cannot become selectable through
    an omission here.
    """
    text = str(api_model or "").strip().lower()
    if not text or text in WITHHELD_API_MODELS:
        return False
    if text == "best_match" or text.endswith(_SEAMLESS_SUFFIX):
        return False
    if "ensemble" in text or "_member" in text:
        return False
    return any(
        capability.api_model == text and not capability.withheld_reason
        for capability in CAPABILITIES.values())


def available_capabilities() -> tuple[OpenMeteoCapability, ...]:
    """Return every selectable capability."""
    return tuple(
        capability for capability in CAPABILITIES.values()
        if not capability.withheld_reason)


def get_capability(model_key: str) -> OpenMeteoCapability:
    """Return the capability for ``model_key``.

    Raises :class:`RetrievalError` for a withheld model so the reason reaches
    the caller, and ``KeyError`` for an identifier this adapter never knew.
    """
    key = str(model_key or "").strip().lower()
    capability = CAPABILITIES.get(key)
    if capability is None:
        # A deliberately withheld model must explain itself. Falling through to
        # KeyError would present a considered decision as a typo.
        reason = WITHHELD_API_MODELS.get(_api_model_for_key(key))
        if reason:
            raise RetrievalError("%s is not available: %s" % (key, reason))
        raise KeyError(key)
    if capability.withheld_reason:
        raise RetrievalError(
            "%s is not available: %s"
            % (capability.label, capability.withheld_reason))
    return capability


def hourly_variables(capability: OpenMeteoCapability) -> tuple[str, ...]:
    """Return the exact hourly variable names for one request.

    Every level and surface field travels in a single request. Asking per level
    or per family would multiply a user's billable calls by twenty or more for
    no benefit.
    """
    names: list[str] = []
    for family in PRESSURE_FAMILIES:
        for level in capability.pressure_levels:
            names.append("%s_%dhPa" % (family, int(level)))
    names.extend(SURFACE_VARIABLES)
    return tuple(names)


def normalise_longitude(lon: float) -> float:
    """Return ``lon`` on the -180..180 convention used across the adapters."""
    return ((float(lon) + 180.0) % 360.0) - 180.0


def point_in_domain(
        capability: OpenMeteoCapability, lat: float, lon: float) -> bool:
    """Report whether a point lies inside this model's domain."""
    latitude = float(lat)
    if not -90.0 <= latitude <= 90.0:
        return False
    lon0, lon1, lat0, lat1 = capability.domain_bounds
    if not lat0 <= latitude <= lat1:
        return False
    longitude = normalise_longitude(lon)
    if lon0 <= lon1:
        return lon0 <= longitude <= lon1
    # Wrapped box across the antimeridian.
    return longitude >= lon0 or longitude <= lon1


def _floor_cycle(when: datetime, cycles: tuple[int, ...]) -> datetime:
    """Return the most recent cycle at or before ``when``."""
    ordered = sorted({int(cycle) for cycle in cycles})
    if not ordered:
        raise RetrievalError("model publishes no cycles")
    stamp = when.astimezone(timezone.utc) if when.tzinfo \
        else when.replace(tzinfo=timezone.utc)
    day = stamp.replace(minute=0, second=0, microsecond=0)
    for cycle in reversed(ordered):
        if cycle <= day.hour:
            return day.replace(hour=cycle)
    previous = day - timedelta(days=1)
    return previous.replace(hour=ordered[-1])


def latest_reference_time(
        capability: OpenMeteoCapability,
        now: datetime | None = None,
        *,
        publication_lag_hours: float = 6.0) -> datetime:
    """Return the newest cycle likely to have been ingested by ``now``.

    Conservative by design. A run reaches Open-Meteo only after the originating
    centre publishes it and Open-Meteo ingests it, so offering the cycle that
    has just struck would spend one of the user's requests on a certain miss.
    """
    current = datetime.now(timezone.utc) if now is None else now
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    return _floor_cycle(
        current - timedelta(hours=float(publication_lag_hours)),
        capability.cycles)


def resolve_run_time(
        capability: OpenMeteoCapability,
        run_time: datetime | None,
        *,
        now: datetime | None = None) -> datetime:
    """Return the UTC run this request should name."""
    if run_time is None:
        return latest_reference_time(capability, now)
    stamp = run_time
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    else:
        stamp = stamp.astimezone(timezone.utc)
    return _floor_cycle(stamp, capability.cycles)


def run_is_archived(
        capability: OpenMeteoCapability, run_time: datetime) -> bool:
    """Report whether ``run_time`` is inside this model's archive."""
    return run_time.date() >= capability.archive_start


__all__ = [
    "ALL_PRESSURE_LEVELS",
    "ARCHIVE_START",
    "ATTRIBUTION",
    "BASE_QUERY",
    "CAPABILITIES",
    "KNOTS_PER_MS",
    "MEASURED_LADDERS",
    "MIN_USABLE_LEVELS",
    "MISSING",
    "PRESSURE_FAMILIES",
    "PRESSURE_LEVELS",
    "PROVIDER",
    "REQUIRED_TOP_PRESSURE_HPA",
    "SURFACE_VARIABLES",
    "TRANSPORT",
    "WITHHELD_API_MODELS",
    "OpenMeteoAccess",
    "OpenMeteoAccessError",
    "OpenMeteoCapability",
    "OpenMeteoRateLimited",
    "ParameterRangeError",
    "RetrievalError",
    "available_capabilities",
    "fetch_json",
    "get_capability",
    "hourly_variables",
    "is_selectable_api_model",
    "latest_reference_time",
    "namespaced_key",
    "normalise_longitude",
    "point_in_domain",
    "resolve_access",
    "resolve_run_time",
    "run_is_archived",
    "unsupported_models",
]


# --------------------------------------------------------------------------- #
# Fetch and normalization
# --------------------------------------------------------------------------- #
def _emit(progress_callback, stage: str, total: int = 0) -> None:
    """Report a stage, matching the sequence the other point adapters use."""
    if progress_callback is None:
        return
    try:
        progress_callback(stage, total)
    except TypeError:
        try:
            progress_callback(stage)
        except Exception:  # noqa: BLE001 - progress must never fail a fetch
            pass
    except Exception:  # noqa: BLE001
        pass


def _check_cancelled(cancelled) -> None:
    """Raise :class:`DownloadCancelled` when the caller has asked to stop."""
    if cancelled is None:
        return
    try:
        stop = bool(cancelled())
    except Exception:  # noqa: BLE001 - a broken predicate must not stop work
        return
    if stop:
        from sharpmod.models.model_transport import DownloadCancelled

        raise DownloadCancelled("Open-Meteo request cancelled")


@dataclass
class OpenMeteoPointDataset:
    """One normalized Open-Meteo point sounding, ready to write.

    Mutable, and carrying a no-op :meth:`close`, so it satisfies the same
    dataset protocol the model-hour cache expects of a GRIB handle.
    """

    capability: OpenMeteoCapability
    columns: dict
    requested_lat: float
    requested_lon: float
    selected_lat: float
    selected_lon: float
    #: Ground-row height, interpolated from the model's own geopotential
    #: profile at the reported surface pressure.
    surface_height_m: float
    #: The provider's terrain elevation for the cell, recorded for diagnostics
    #: and the capability audit. Deliberately not used to build the ground row.
    provider_elevation_m: float
    run_time: datetime
    valid_time: datetime
    fxx: int
    request_count: int
    variable_count: int
    weighted_units: float
    surface_pressure_hpa: float
    levels_requested: int
    levels_retained: int
    below_ground_levels_removed: int
    access_mode: str
    generation_time_ms: float = 0.0
    surface_merged: bool = True

    def close(self) -> None:
        """Match the model-hour cache dataset protocol; there is no handle."""

    # The hour cache reads provenance off the loader's source object through
    # these attribute names, so a non-GRIB payload can participate in the same
    # single-flight machinery without the cache knowing what a GRIB is.
    @property
    def _sharpmod_source_url(self) -> str:
        return "%s single runs: %s" % (PROVIDER, self.capability.api_model)

    @property
    def _sharpmod_fields(self) -> tuple[str, ...]:
        return self.capability.fields

    @property
    def _sharpmod_transport(self) -> str:
        return TRANSPORT


def _require_float(value, name: str) -> float:
    """Return ``value`` as a finite float or raise a contract error."""
    import math

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise RetrievalError(
            "Open-Meteo response field %s was %r, expected a number"
            % (name, value)) from None
    if not math.isfinite(number):
        raise RetrievalError(
            "Open-Meteo response field %s was not finite" % name)
    return number


def _select_time_index(times, valid_time: datetime,
                       capability: OpenMeteoCapability,
                       run_time: datetime) -> int:
    """Return the index of ``valid_time`` in the returned series.

    Matched by timestamp rather than by array position. Trusting a position
    would silently return a different hour whenever the provider changed its
    cadence, shifted a window, or trimmed a run.
    """
    if not isinstance(times, (list, tuple)) or not times:
        raise RetrievalError(
            "Open-Meteo returned no time axis for %s run %s"
            % (capability.label, run_time.strftime("%Y-%m-%d %H:%MZ")))
    target = int(valid_time.timestamp())
    matches = [index for index, stamp in enumerate(times)
               if _as_epoch(stamp) == target]
    if not matches:
        raise RetrievalError(
            "%s run %s does not contain %s. The run may not be ingested yet, "
            "or that forecast hour may lie past its horizon."
            % (capability.label,
               run_time.strftime("%Y-%m-%d %H:%MZ"),
               valid_time.strftime("%Y-%m-%d %H:%MZ")))
    if len(matches) > 1:
        raise RetrievalError(
            "Open-Meteo returned %d values for %s; refusing an ambiguous "
            "timestamp" % (len(matches),
                           valid_time.strftime("%Y-%m-%d %H:%MZ")))
    return matches[0]


def _as_epoch(stamp) -> int | None:
    """Return ``stamp`` as epoch seconds, or ``None`` when unusable."""
    try:
        return int(stamp)
    except (TypeError, ValueError):
        return None


def _hourly_value(hourly: dict, name: str, index: int,
                  api_model: str) -> float | None:
    """Return one hourly value, or ``None`` when absent or null.

    A single-model request returns plain variable names, but the provider
    suffixes them when several models are asked for at once. The suffixed form
    is accepted too so a future multi-model request cannot silently read
    nothing.
    """
    series = hourly.get(name)
    if series is None:
        series = hourly.get("%s_%s" % (name, api_model))
    if not isinstance(series, (list, tuple)) or index >= len(series):
        return None
    value = series[index]
    if value is None:
        return None
    try:
        import math

        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _derive_surface_height(
        pairs: list[tuple[float, float]],
        surface_pressure: float) -> float:
    """Return the model's surface geopotential height, in metres.

    Derived from the model's own geopotential-height profile by interpolating in
    log pressure to the reported surface pressure, rather than taken from the
    provider's ``elevation`` field.

    That choice is deliberate and load-bearing. ``elevation`` is a terrain value
    for the grid cell and comes from a different dataset than the mass field, so
    the two need not agree -- and when they disagree the wrong way round, the
    ground row lands above the lowest isobar, height stops increasing, and the
    whole sounding fails quality control. Interpolating the model's own profile
    is self-consistent by construction: it returns the height at which the
    model's pressure equals the surface pressure the model reported.

    ``pairs`` must be ``(pressure, height)`` sorted by descending pressure and
    must not have been filtered to levels above ground yet, so a bracketing
    interpolation is available for high terrain instead of a long extrapolation.
    """
    import math

    if len(pairs) < 2:
        raise RetrievalError(
            "Open-Meteo returned too few pressure levels to place the surface")

    # Prefer the pair straddling the surface; fall back to the nearest edge pair
    # and extrapolate, which is a short step when the ground sits just below the
    # lowest isobar.
    lower = pairs[0]
    upper = pairs[1]
    for first, second in zip(pairs, pairs[1:]):
        if first[0] >= surface_pressure >= second[0]:
            lower, upper = first, second
            break
    else:
        if surface_pressure > pairs[0][0]:
            lower, upper = pairs[0], pairs[1]
        else:
            lower, upper = pairs[-2], pairs[-1]

    p_low, h_low = lower
    p_high, h_high = upper
    if p_low <= 0.0 or p_high <= 0.0 or p_low == p_high:
        raise RetrievalError(
            "Open-Meteo pressure levels are unusable for a surface height")
    slope = (h_high - h_low) / (math.log(p_high) - math.log(p_low))
    derived = h_low + slope * (math.log(surface_pressure) - math.log(p_low))
    if not math.isfinite(derived):
        raise RetrievalError("derived Open-Meteo surface height is not finite")
    return derived


def _provider_elevation(response: dict) -> float:
    """Return the provider's reported grid elevation, or ``MISSING``.

    Recorded for diagnostics and the capability audit only. It is not used to
    build the ground row; see :func:`_derive_surface_height`.
    """
    import math

    try:
        value = float(response.get("elevation"))
    except (TypeError, ValueError):
        return MISSING
    return value if math.isfinite(value) else MISSING


def build_query(
        capability: OpenMeteoCapability,
        lat: float,
        lon: float,
        run_time: datetime,
        valid_time: datetime) -> dict:
    """Return the Single Runs query for one model, run, point, and hour.

    The span is bounded with ``forecast_hours``, not a calendar window. The
    Single Runs endpoint rejects ``start_date``/``end_date`` and ``start_hour``/
    ``end_hour`` outright -- it answers "Parameter 'start_date' must not be set"
    -- because a run already fixes where the series begins. The series therefore
    always starts at the run, and asking for ``fxx + 1`` hours is the smallest
    request that can contain the wanted hour.
    """
    hours = int(
        (valid_time - run_time).total_seconds() // 3600) + 1
    query = dict(BASE_QUERY)
    query.update({
        "latitude": "%.6f" % float(lat),
        "longitude": "%.6f" % float(lon),
        "models": capability.api_model,
        "run": run_time.strftime("%Y-%m-%dT%H:%M"),
        "forecast_hours": str(max(1, hours)),
        "hourly": ",".join(hourly_variables(capability)),
    })
    return query




def weighted_units(variable_count: int) -> float:
    """Estimate the provider's weighted cost of one request.

    Open-Meteo counts a call as more than one unit once it exceeds ten
    variables. A sounding asks for well over a hundred, so treating each request
    as a single unit would under-count a user's consumption by an order of
    magnitude and let a local budget sail past the real limit.
    """
    return max(1.0, float(variable_count) / 10.0)


# --------------------------------------------------------------------------- #
# Writing, extraction, and availability
# --------------------------------------------------------------------------- #
def default_out_path(
        capability: OpenMeteoCapability,
        lat: float,
        lon: float,
        run_time: datetime,
        fxx: int) -> str:
    """Return the conventional output filename for one point sounding."""
    return "%s_point_%.2fN_%.2fE_%s_f%03d.npz" % (
        capability.model_key, float(lat), float(lon),
        run_time.strftime("%Y%m%d%H"), int(fxx))








def _validate_reused(
        capability: OpenMeteoCapability,
        dataset: OpenMeteoPointDataset,
        lat: float,
        lon: float,
        run_time: datetime | None,
        fxx: int,
        *,
        now: datetime | None = None) -> None:
    """Raise unless ``dataset`` really answers this request."""
    if dataset.capability.model_key != capability.model_key:
        raise RetrievalError(
            "cached dataset is %s, not %s"
            % (dataset.capability.label, capability.label))
    if int(dataset.fxx) != int(fxx):
        raise RetrievalError(
            "cached dataset is F%03d, not F%03d" % (dataset.fxx, int(fxx)))
    expected_run = resolve_run_time(capability, run_time, now=now)
    if dataset.run_time != expected_run:
        raise RetrievalError(
            "cached dataset is run %s, not %s"
            % (dataset.run_time.strftime("%Y-%m-%d %H:%MZ"),
               expected_run.strftime("%Y-%m-%d %H:%MZ")))
    if abs(dataset.requested_lat - float(lat)) > 1e-6 \
            or abs(dataset.requested_lon - normalise_longitude(lon)) > 1e-6:
        raise RetrievalError(
            "cached dataset is for %.6f, %.6f, not %.6f, %.6f"
            % (dataset.requested_lat, dataset.requested_lon,
               float(lat), normalise_longitude(lon)))


from sharpmod.providers.openmeteo_runtime import (  # noqa: E402
    fetch_point,
    build_metadata,
    write_point_dataset,
    extract,
    probe
)
