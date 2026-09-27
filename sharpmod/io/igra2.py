"""IGRA v2 (Integrated Global Radiosonde Archive) sounding reader.

IGRA is NOAA/NCEI's quality-assured global radiosonde archive: roughly 2,800
stations, some with a record reaching back to 1905, updated daily. It is the
only source here that offers deep history, which is the reason it exists
alongside the University of Wyoming and IEM adapters.

The archive has no per-sounding endpoint. The smallest retrievable unit is one
ZIP per station covering either the current year-to-date window (a few MB) or
the entire period of record (tens of MB -- 80 MB for a long US station). Every
request therefore goes through :class:`IGRACache`, so the cost is paid once per
station rather than once per sounding.

This module is deliberately free of NumPy-dependent profile machinery and of
Qt. It parses the published fixed-width formats into plain values in the units
the rest of the project uses, and leaves :class:`~sharpmod.io.uwyo_decoder`
``from_intermediate`` conversion to the provider adapter in
:mod:`sharpmod.providers.observations`.

Formats implemented from the published specifications:

* ``doc/igra2-list-format.txt`` -- station list.
* ``doc/igra2-data-format.txt`` -- sounding header and data records.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import io
import json
import logging
import math
import os
from pathlib import Path
import socket
import ssl
import tempfile
import time
from typing import Callable, Iterator, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

try:
    import certifi

    _CA_FILE = certifi.where()
except Exception:  # pragma: no cover - certifi is a runtime dependency
    _CA_FILE = None


_LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Published endpoints
# --------------------------------------------------------------------------- #
#: Root of the public IGRA v2 tree at NCEI.
ARCHIVE_ROOT = (
    "https://www.ncei.noaa.gov/data/integrated-global-radiosonde-archive"
)

#: Fixed-width station list (~260 kB), refreshed daily with the data.
STATION_LIST_URL = f"{ARCHIVE_ROOT}/doc/igra2-station-list.txt"

#: Full period of record, one ZIP per station. Large: tens of MB.
DATA_POR_URL = f"{ARCHIVE_ROOT}/access/data-por"

#: Current (or current and previous) year only, one ZIP per station.
DATA_Y2D_URL = f"{ARCHIVE_ROOT}/access/data-y2d"


# --------------------------------------------------------------------------- #
# Sentinels and units
# --------------------------------------------------------------------------- #
#: Per-level missing sentinel shared with the ``.npz`` intermediate contract
#: (``UWyo_Decoder.MISSING``). Distinct from the raw IGRA sentinels below.
MISSING = -9999.0

#: Raw IGRA sentinel: value was missing before quality assurance ran.
RAW_MISSING = -9999

#: Raw IGRA sentinel: quality assurance removed the value, but other fields at
#: the same level remain valid. Treated identically to :data:`RAW_MISSING` --
#: the distinction is provenance, not usability.
RAW_REMOVED = -8888

#: Station-list sentinels for a mobile platform or an unknown elevation.
MOBILE_LAT = -98.8888
MOBILE_LON = -998.8888
MISSING_ELEVATION = -999.9
MOBILE_ELEVATION = -998.8

#: IGRA reports wind speed in m/s; profiles here carry knots.
MS_TO_KNOTS = 3600.0 / 1852.0

#: ``HOUR`` value meaning the nominal observation hour is unknown.
UNKNOWN_HOUR = 99

#: ``RELTIME`` value meaning both release hour and minute are unknown.
UNKNOWN_RELTIME = 9999

#: Network code (third character of an IGRA id) whose trailing five characters
#: are the WMO station number -- the identifier the rest of this project and
#: the University of Wyoming archive both use.
WMO_NETWORK = "M"

#: Network code whose trailing four characters are an ICAO callsign.
ICAO_NETWORK = "I"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class IGRAError(Exception):
    """Base class for every IGRA reader error."""


class IGRAStationLookupError(IGRAError):
    """A station query resolved to zero or more than one station."""


class IGRAStationTimeUnavailableError(IGRAError):
    """The station has no archived sounding at the requested time."""


class IGRARetrievalError(IGRAError):
    """The archive could not be reached, or refused the request."""


class IGRASoundingParseError(IGRAError):
    """A retrieved archive is not a parseable IGRA sounding file."""


class IGRAArchiveTooLargeError(IGRARetrievalError):
    """The archive needed exceeds the configured download ceiling."""


class IGRAFullRecordRequiredError(IGRAStationTimeUnavailableError):
    """The date needs the full period-of-record archive, which is disabled.

    Raised only when ``allow_full_record`` is off. A caller that probes
    availability cheaply -- the GUI does, on every station click -- can catch
    this and report "not checked" instead of the misleading "no sounding" that
    the parent class would otherwise imply.
    """


# --------------------------------------------------------------------------- #
# Station list
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class IGRAStation:
    """One record from ``igra2-station-list.txt``."""

    id: str
    name: str
    lat: float
    lon: float
    elev_m: float
    state: str
    first_year: int
    last_year: int
    n_obs: int

    @property
    def country(self) -> str:
        """FIPS country code (first two characters of the id)."""
        return self.id[:2]

    @property
    def network(self) -> str:
        """Network code: ``I``, ``M``, ``V``, ``W``, or ``X``."""
        return self.id[2:3]

    @property
    def wmo_id(self) -> str | None:
        """WMO station number, when this id encodes one.

        This is the bridge to the identifiers already used elsewhere in the
        project: the station the University of Wyoming archive calls ``72357``
        is ``USM00072357`` here.
        """
        if self.network != WMO_NETWORK:
            return None
        tail = self.id[-5:]
        return tail if tail.isdigit() else None

    @property
    def icao_id(self) -> str | None:
        """ICAO callsign, when this id encodes one."""
        if self.network != ICAO_NETWORK:
            return None
        return self.id[-4:].strip() or None

    @property
    def is_mobile(self) -> bool:
        """True for a mobile platform, whose position varies per sounding."""
        return (
            math.isclose(self.lat, MOBILE_LAT, abs_tol=1e-4)
            or math.isclose(self.lon, MOBILE_LON, abs_tol=1e-4)
        )

    def covers_year(self, year: int) -> bool:
        """True when the published record spans ``year``.

        Checked before any download: it is the difference between refusing a
        1993-only station immediately and pulling tens of megabytes to discover
        the same thing.
        """
        return int(self.first_year) <= int(year) <= int(self.last_year)


def _float_or(value: str, default: float) -> float:
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default


def _int_or(value: str, default: int) -> int:
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def parse_station_list(text: str) -> tuple[IGRAStation, ...]:
    """Parse ``igra2-station-list.txt`` into station records.

    Column positions follow ``igra2-list-format.txt``: ID 1-11, LATITUDE
    13-20, LONGITUDE 22-30, ELEVATION 32-37, STATE 39-40, NAME 42-71, FSTYEAR
    73-76, LSTYEAR 78-81, NOBS 83-88. Unparseable lines are skipped rather
    than failing the whole catalogue, because one malformed row upstream should
    not remove every station.
    """
    if not text or not text.strip():
        raise IGRASoundingParseError("IGRA station list was empty")

    stations: list[IGRAStation] = []
    for line in text.splitlines():
        if len(line) < 41:
            continue
        station_id = line[0:11].strip().upper()
        if not station_id:
            continue
        name = line[41:71].strip()
        elevation = _float_or(line[31:37], MISSING_ELEVATION)
        stations.append(
            IGRAStation(
                id=station_id,
                name=name,
                lat=_float_or(line[12:20], float("nan")),
                lon=_float_or(line[21:30], float("nan")),
                elev_m=elevation,
                state=line[38:40].strip(),
                first_year=_int_or(line[72:76], 0),
                last_year=_int_or(line[77:81], 0),
                n_obs=_int_or(line[82:88], 0),
            )
        )
    if not stations:
        raise IGRASoundingParseError(
            "IGRA station list contained no parseable records"
        )
    return tuple(stations)


# --------------------------------------------------------------------------- #
# Sounding records
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class IGRAHeader:
    """One ``#``-prefixed sounding header record."""

    station_id: str
    year: int
    month: int
    day: int
    hour: int
    reltime: int
    n_levels: int
    pressure_source: str
    nonpressure_source: str
    lat: float
    lon: float
    line_index: int

    @property
    def nominal_valid(self) -> datetime | None:
        """Nominal valid time in UTC, or ``None`` when it cannot be formed.

        ``HOUR`` is 99 when the provider did not supply a nominal hour; the
        release time is used instead, since a sounding with no time at all
        cannot be matched to a request.
        """
        hour = self.hour
        if hour == UNKNOWN_HOUR:
            if self.reltime == UNKNOWN_RELTIME or self.reltime < 0:
                return None
            hour = self.reltime // 100
            if hour > 23:
                return None
        if not (0 <= hour <= 23):
            return None
        try:
            return datetime(
                self.year, self.month, self.day, hour, tzinfo=timezone.utc
            )
        except ValueError:
            return None

    @property
    def release_time(self) -> datetime | None:
        """Actual release time in UTC, when the archive reports one.

        A release can precede its nominal hour -- a 00Z sounding is typically
        launched around 23Z the previous day -- so the date is rolled back when
        the release hour is far ahead of the nominal hour.
        """
        if self.reltime == UNKNOWN_RELTIME or self.reltime < 0:
            return None
        hour, minute = divmod(self.reltime, 100)
        if hour > 23:
            return None
        if minute > 59:
            minute = 0
        try:
            released = datetime(
                self.year, self.month, self.day, hour, minute,
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None
        nominal = self.nominal_valid
        if nominal is not None and released - nominal > timedelta(hours=12):
            released -= timedelta(days=1)
        return released


def _decode_header(line: str, line_index: int) -> IGRAHeader:
    """Decode one header record per ``igra2-data-format.txt``.

    Column positions: HEADREC 1, ID 2-12, YEAR 14-17, MONTH 19-20, DAY 22-23,
    HOUR 25-26, RELTIME 28-31, NUMLEV 33-36, P_SRC 38-45, NP_SRC 47-54,
    LAT 56-62, LON 64-71. LAT and LON are integers in ten-thousandths of a
    degree.

    The sentinel check on position happens *before* the scaling, which matters:
    dividing the raw ``-9999`` by 10000 would yield ``-0.9999``, a perfectly
    plausible latitude near the equator, and a missing position would then be
    indistinguishable from a real one.
    """
    padded = line.ljust(71)
    raw_lat = _int_or(padded[55:62], RAW_MISSING)
    raw_lon = _int_or(padded[63:71], RAW_MISSING)
    lat = raw_lat / 10000.0 if _usable(raw_lat) else float("nan")
    lon = raw_lon / 10000.0 if _usable(raw_lon) else float("nan")
    if math.isclose(lat, MOBILE_LAT, abs_tol=1e-4):
        lat = float("nan")
    if math.isclose(lon, MOBILE_LON, abs_tol=1e-4):
        lon = float("nan")
    return IGRAHeader(
        station_id=padded[1:12].strip().upper(),
        year=_int_or(padded[13:17], 0),
        month=_int_or(padded[18:20], 0),
        day=_int_or(padded[21:23], 0),
        hour=_int_or(padded[24:26], UNKNOWN_HOUR),
        reltime=_int_or(padded[27:31], UNKNOWN_RELTIME),
        n_levels=_int_or(padded[32:36], 0),
        pressure_source=padded[37:45].strip(),
        nonpressure_source=padded[46:54].strip(),
        lat=lat,
        lon=lon,
        line_index=line_index,
    )


def _usable(raw: int) -> bool:
    """True when a raw IGRA integer carries a real measurement."""
    return raw not in (RAW_MISSING, RAW_REMOVED)


def dewpoint_from_relative_humidity(tmpc: float, rh_percent: float) -> float:
    """Return the dewpoint in C for ``tmpc`` and ``rh_percent``.

    Used only where IGRA reports relative humidity but no dewpoint depression,
    which is common in the older record. This is the exact inversion of the
    Alduchov-Eskridge (1996) saturation vapour pressure relation, not an
    interpolation between levels, and every level it fills is counted in the
    sounding metadata so the substitution stays visible.
    """
    if not math.isfinite(tmpc) or not math.isfinite(rh_percent):
        return MISSING
    humidity = min(max(float(rh_percent), 0.0), 100.0)
    if humidity <= 0.0:
        return MISSING
    ratio = (17.625 * tmpc) / (tmpc + 243.04)
    saturation = 6.1094 * math.exp(ratio)
    vapour = saturation * humidity / 100.0
    if vapour <= 0.0:
        return MISSING
    log_term = math.log(vapour / 6.1094)
    denominator = 17.625 - log_term
    if abs(denominator) < 1e-12:
        return MISSING
    return (243.04 * log_term) / denominator


@dataclass(frozen=True)
class IGRALevel:
    """One decoded data record, in the units this project uses."""

    pres: float
    hght: float
    tmpc: float
    dwpc: float
    wdir: float
    wspd: float
    is_surface: bool
    dewpoint_from_rh: bool


def _decode_level(line: str) -> IGRALevel | None:
    """Decode one data record, or ``None`` when it has no pressure.

    Column positions: LVLTYP1 1, LVLTYP2 2, ETIME 4-8, PRESS 10-15, PFLAG 16,
    GPH 17-21, ZFLAG 22, TEMP 23-27, TFLAG 28, RH 29-33, DPDP 35-39,
    WDIR 41-45, WSPD 47-51.

    Levels are dropped when they carry no pressure. IGRA includes
    non-pressure levels (``LVLTYP1 == 3``) identified only by height, and
    pressure is the vertical coordinate downstream, so such a level cannot be
    placed on a profile.
    """
    padded = line.ljust(51)
    raw_pressure = _int_or(padded[9:15], RAW_MISSING)
    if not _usable(raw_pressure) or raw_pressure <= 0:
        return None

    raw_height = _int_or(padded[16:21], RAW_MISSING)
    raw_temp = _int_or(padded[22:27], RAW_MISSING)
    raw_rh = _int_or(padded[28:33], RAW_MISSING)
    raw_dpdp = _int_or(padded[34:39], RAW_MISSING)
    raw_wdir = _int_or(padded[40:45], RAW_MISSING)
    raw_wspd = _int_or(padded[46:51], RAW_MISSING)

    tmpc = raw_temp / 10.0 if _usable(raw_temp) else MISSING
    dwpc = MISSING
    from_rh = False
    if _usable(raw_dpdp) and tmpc != MISSING:
        # DPDP is a dewpoint *depression* in tenths of a degree.
        dwpc = tmpc - (raw_dpdp / 10.0)
    elif _usable(raw_rh) and tmpc != MISSING:
        dwpc = dewpoint_from_relative_humidity(tmpc, raw_rh / 10.0)
        from_rh = dwpc != MISSING

    wspd = raw_wspd / 10.0 * MS_TO_KNOTS if _usable(raw_wspd) else MISSING
    wdir = float(raw_wdir) if _usable(raw_wdir) else MISSING
    if wdir != MISSING and not (0.0 <= wdir <= 360.0):
        wdir = MISSING

    return IGRALevel(
        pres=raw_pressure / 100.0,
        hght=float(raw_height) if _usable(raw_height) else MISSING,
        tmpc=tmpc,
        dwpc=dwpc,
        wdir=wdir,
        wspd=wspd,
        is_surface=padded[1:2] == "1",
        dewpoint_from_rh=from_rh,
    )


@dataclass(frozen=True)
class IGRASounding:
    """One IGRA sounding decoded into per-level columns."""

    station_id: str
    valid: datetime
    release_time: datetime | None
    lat: float
    lon: float
    pres: tuple[float, ...]
    hght: tuple[float, ...]
    tmpc: tuple[float, ...]
    dwpc: tuple[float, ...]
    wdir: tuple[float, ...]
    wspd: tuple[float, ...]
    pressure_source: str
    nonpressure_source: str
    dewpoint_from_rh_levels: int
    dropped_levels: int
    #: Provenance, filled in by :meth:`IGRA_Decoder.fetch`. Parsing alone does
    #: not know which archive the text came from.
    archive_url: str = ""
    archive_kind: str = ""

    @property
    def n_levels(self) -> int:
        return len(self.pres)


def iter_headers(text: str) -> Iterator[IGRAHeader]:
    """Yield every sounding header in one station archive, in file order."""
    for index, line in enumerate(text.splitlines()):
        if line.startswith("#"):
            yield _decode_header(line, index)


def parse_sounding(text: str, header: IGRAHeader) -> IGRASounding:
    """Decode the sounding introduced by ``header``.

    Levels are kept only where pressure is present and strictly decreasing.
    IGRA interleaves pressure and non-pressure levels and can repeat a
    pressure across a surface/standard pair; downstream interpolation assumes a
    monotonic vertical coordinate.
    """
    lines = text.splitlines()
    start = header.line_index + 1
    end = start + max(int(header.n_levels), 0)
    if start > len(lines):
        raise IGRASoundingParseError(
            f"IGRA header for {header.station_id} points past end of file"
        )

    pres: list[float] = []
    hght: list[float] = []
    tmpc: list[float] = []
    dwpc: list[float] = []
    wdir: list[float] = []
    wspd: list[float] = []
    from_rh = 0
    dropped = 0
    last_pressure = math.inf

    for line in lines[start:end]:
        if line.startswith("#"):
            break
        level = _decode_level(line)
        if level is None or level.pres >= last_pressure:
            dropped += 1
            continue
        last_pressure = level.pres
        pres.append(level.pres)
        hght.append(level.hght)
        tmpc.append(level.tmpc)
        dwpc.append(level.dwpc)
        wdir.append(level.wdir)
        wspd.append(level.wspd)
        if level.dewpoint_from_rh:
            from_rh += 1

    if not pres:
        raise IGRASoundingParseError(
            f"IGRA sounding for {header.station_id} at "
            f"{header.year:04d}-{header.month:02d}-{header.day:02d} "
            f"{header.hour:02d}Z has no pressure levels"
        )

    valid = header.nominal_valid
    if valid is None:
        raise IGRASoundingParseError(
            f"IGRA sounding for {header.station_id} has no usable valid time"
        )

    return IGRASounding(
        station_id=header.station_id,
        valid=valid,
        release_time=header.release_time,
        lat=header.lat,
        lon=header.lon,
        pres=tuple(pres),
        hght=tuple(hght),
        tmpc=tuple(tmpc),
        dwpc=tuple(dwpc),
        wdir=tuple(wdir),
        wspd=tuple(wspd),
        pressure_source=header.pressure_source,
        nonpressure_source=header.nonpressure_source,
        dewpoint_from_rh_levels=from_rh,
        dropped_levels=dropped,
    )


# --------------------------------------------------------------------------- #
# Per-station archive cache
# --------------------------------------------------------------------------- #
def default_igra_cache_root() -> Path:
    """Return the IGRA cache directory, honoring an explicit override.

    Kept separate from the model cache: that one is addressed by
    model/run/forecast-hour and shares a multi-gigabyte GRIB budget, neither of
    which describes a per-station text archive.
    """
    explicit = os.environ.get("SHARPMOD_IGRA_CACHE")
    if explicit:
        return Path(explicit).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "sharpmod" / "igra2-cache"
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base).expanduser() / "sharpmod" / "igra2-cache"
    return Path.home() / ".cache" / "sharpmod" / "igra2-cache"


def _safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_." else "_"
        for character in str(value)
    ) or "none"


class IGRACache:
    """Own the downloaded IGRA archives under an age and size budget."""

    #: Bumped when the on-disk layout changes.
    CONTRACT_VERSION = 1

    def __init__(self, root=None, *, max_bytes: int | None = None):
        self.root = Path(root or default_igra_cache_root()).expanduser()
        if max_bytes is None:
            max_bytes = int(
                float(os.environ.get("SHARPMOD_IGRA_CACHE_GB", "2"))
                * 1024 ** 3
            )
        self.max_bytes = max(0, int(max_bytes))

    @property
    def directory(self) -> Path:
        return self.root / f"v{self.CONTRACT_VERSION}"

    def path_for(self, name: str) -> Path:
        return self.directory / _safe_name(name)

    def age_seconds(self, name: str, *, now: float | None = None) -> float:
        """Return the age of one cached entry, or ``inf`` when absent."""
        path = self.path_for(name)
        try:
            modified = path.stat().st_mtime
        except OSError:
            return math.inf
        return float(time.time() if now is None else now) - modified

    def read(self, name: str) -> bytes | None:
        try:
            return self.path_for(name).read_bytes()
        except OSError:
            return None

    def write(self, name: str, payload: bytes) -> Path:
        """Write one entry atomically, so a killed download leaves no ruin."""
        path = self.path_for(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".part", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
            os.replace(temporary, path)
        except BaseException:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise
        return path

    def load(
        self,
        name: str,
        loader: Callable[[], bytes],
        *,
        max_age_seconds: float,
        allow_stale: bool = True,
    ) -> bytes:
        """Return ``name`` from cache, refreshing it through ``loader``.

        A stale copy is served when the refresh fails and ``allow_stale`` is
        set. The archive is a daily-updated public mirror, so an unreachable
        network should degrade to yesterday's copy rather than to no sounding.
        """
        age = self.age_seconds(name)
        if age <= max_age_seconds:
            cached = self.read(name)
            if cached:
                _LOGGER.debug("igra2.cache_hit name=%s age=%.0fs", name, age)
                return cached
        try:
            payload = loader()
        except IGRAError:
            stale = self.read(name) if allow_stale else None
            if stale:
                _LOGGER.warning(
                    "igra2.cache_stale_served name=%s age=%.0fs", name, age
                )
                return stale
            raise
        self.write(name, payload)
        # A successful archive fetch is the operation that grows the cache, so
        # enforce the configured budget immediately instead of relying on a
        # caller to remember a separate maintenance pass.
        self.prune()
        return payload

    def entries(self) -> list[Path]:
        try:
            return sorted(
                path for path in self.directory.iterdir() if path.is_file()
            )
        except OSError:
            return []

    def prune(self) -> list[Path]:
        """Evict least-recently-modified entries down to the size budget."""
        removed: list[Path] = []
        entries = []
        for path in self.entries():
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime, stat.st_size, path))
        total = sum(size for _mtime, size, _path in entries)
        for _mtime, size, path in sorted(entries):
            if total <= self.max_bytes:
                break
            try:
                path.unlink()
            except OSError:
                continue
            removed.append(path)
            total -= size
        return removed

    def clear(self) -> list[Path]:
        removed = []
        for path in self.entries():
            try:
                path.unlink()
            except OSError:
                continue
            removed.append(path)
        return removed


# --------------------------------------------------------------------------- #
# Decoder
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _ArchiveChoice:
    """Which per-station archive to retrieve, and what it costs."""

    url: str
    kind: str
    cache_name: str
    max_age_seconds: float




def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("observation time must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _describe(delta: timedelta) -> str:
    total = int(abs(delta).total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h{minutes:02d}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


__all__ = [
    "ARCHIVE_ROOT",
    "DATA_POR_URL",
    "DATA_Y2D_URL",
    "ICAO_NETWORK",
    "IGRAArchiveTooLargeError",
    "IGRACache",
    "IGRAError",
    "IGRAFullRecordRequiredError",
    "IGRAHeader",
    "IGRALevel",
    "IGRARetrievalError",
    "IGRASounding",
    "IGRASoundingParseError",
    "IGRAStation",
    "IGRAStationLookupError",
    "IGRAStationTimeUnavailableError",
    "IGRA_Decoder",
    "MISSING",
    "MOBILE_ELEVATION",
    "MOBILE_LAT",
    "MOBILE_LON",
    "MISSING_ELEVATION",
    "MS_TO_KNOTS",
    "RAW_MISSING",
    "RAW_REMOVED",
    "STATION_LIST_URL",
    "UNKNOWN_HOUR",
    "UNKNOWN_RELTIME",
    "WMO_NETWORK",
    "default_igra_cache_root",
    "dewpoint_from_relative_humidity",
    "iter_headers",
    "parse_sounding",
    "parse_station_list",
]


from sharpmod.io.igra2_decoder import (  # noqa: E402
    IGRA_Decoder
)
