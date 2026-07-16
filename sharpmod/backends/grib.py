"""Low-allocation pressure-level GRIB point decoding with ecCodes.

The xarray/cfgrib path is useful for general gridded analysis, but a point
sounding only needs one value from each relevant pressure-level message.  This
module inventories message headers once, performs one ecCodes nearest-grid
lookup, and then reads the selected value directly from each chosen message.
"""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
import importlib
import importlib.util
import os
from pathlib import Path
import sys
import threading

import numpy as np


GRIB_COLUMN_NAMES = (
    "pres",
    "hght",
    "tmpc",
    "dwpc",
    "wdir",
    "wspd",
    "omeg",
    "u",
    "v",
)

_G0 = 9.80665
_KELVIN_OFFSET = 273.15
_MS_TO_KNOTS = 1.94384449
_EARTH_ROTATION_RATE = 7.2921159e-5

_INVENTORY_CACHE_MAX = 8
_NEAREST_CACHE_MAX = 256
_POINT_CACHE_MAX = 128


class GribDecodeError(RuntimeError):
    """A GRIB file cannot provide a supported pressure-level point sounding."""


@dataclass(frozen=True, slots=True)
class DecodedPoint:
    """Compact, immutable point sounding returned by both backends.

    ``matrix`` is C-contiguous ``float64`` in :data:`GRIB_COLUMN_NAMES` order.
    Named row properties are zero-copy views of that one allocation.
    """

    matrix: np.ndarray
    selected_lat: float
    selected_lon: float
    surface_relative_vorticity: float | None = None

    def __post_init__(self):
        # PyO3/Numpy may return a C-contiguous ndarray whose memory is owned by
        # a capsule rather than by ``ndarray`` itself. Requiring OWNDATA would
        # copy that native matrix at the language boundary for no benefit.
        matrix = np.require(self.matrix, dtype=np.float64, requirements=("C",))
        if matrix.ndim != 2 or matrix.shape[0] != len(GRIB_COLUMN_NAMES):
            raise ValueError(
                "decoded GRIB matrix must have shape "
                f"({len(GRIB_COLUMN_NAMES)}, nlevels); got {matrix.shape}"
            )
        matrix.setflags(write=False)
        object.__setattr__(self, "matrix", matrix)
        object.__setattr__(self, "selected_lat", float(self.selected_lat))
        object.__setattr__(self, "selected_lon", float(self.selected_lon))
        vorticity = self.surface_relative_vorticity
        if vorticity is not None:
            vorticity = float(vorticity)
            if not np.isfinite(vorticity):
                vorticity = None
        object.__setattr__(self, "surface_relative_vorticity", vorticity)

    @property
    def pres(self):
        return self.matrix[0]

    @property
    def hght(self):
        return self.matrix[1]

    @property
    def tmpc(self):
        return self.matrix[2]

    @property
    def dwpc(self):
        return self.matrix[3]

    @property
    def wdir(self):
        return self.matrix[4]

    @property
    def wspd(self):
        return self.matrix[5]

    @property
    def omeg(self):
        return self.matrix[6]

    @property
    def u(self):
        return self.matrix[7]

    @property
    def v(self):
        return self.matrix[8]

    def as_dict(self):
        """Return named zero-copy column views for extractor integration."""
        return {
            name: self.matrix[index]
            for index, name in enumerate(GRIB_COLUMN_NAMES)
        }


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    path: str
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class _MessageRef:
    offset: int
    field_index: int
    pressure: float
    missing_value: float | None


@dataclass(frozen=True, slots=True)
class _RoleMessages:
    role: str
    short_name: str
    messages: tuple[_MessageRef, ...]


@dataclass(frozen=True, slots=True)
class _Inventory:
    levels: tuple[float, ...]
    roles: tuple[_RoleMessages, ...]
    representative_offset: int
    representative_field_index: int


@dataclass(frozen=True, slots=True)
class _GridPoint:
    index: int
    latitude: float
    longitude: float


_CACHE_LOCK = threading.RLock()
_ECCODES_LOCK = threading.RLock()
_INVENTORY_CACHE: OrderedDict[_FileIdentity, _Inventory] = OrderedDict()
_NEAREST_CACHE: OrderedDict[tuple[_FileIdentity, float, float], _GridPoint] = \
    OrderedDict()
_POINT_CACHE: OrderedDict[tuple[_FileIdentity, int, float], DecodedPoint] = \
    OrderedDict()
_CACHE_STATS = {
    "inventory": {"hits": 0, "misses": 0},
    "nearest": {"hits": 0, "misses": 0},
    "points": {"hits": 0, "misses": 0},
}


def clear_grib_caches(
    *,
    inventory: bool = True,
    nearest: bool = True,
    points: bool = True,
    reset_stats: bool = True,
):
    """Clear selected decoder LRUs.

    Selective clearing lets benchmarks measure a warm inventory with a cold
    point lookup while normal application shutdown can use the no-argument
    form.  File identity is part of every key, so retaining one cache while
    clearing another cannot return data for a changed file.
    """
    with _CACHE_LOCK:
        if inventory:
            _INVENTORY_CACHE.clear()
        if nearest:
            _NEAREST_CACHE.clear()
        if points:
            _POINT_CACHE.clear()
        if reset_stats:
            for stats in _CACHE_STATS.values():
                stats["hits"] = 0
                stats["misses"] = 0


def grib_cache_info():
    """Return deterministic sizes and counters for decoder profiling/tests."""
    with _CACHE_LOCK:
        return {
            "inventory": {
                "size": len(_INVENTORY_CACHE),
                "max_size": _INVENTORY_CACHE_MAX,
                **_CACHE_STATS["inventory"],
            },
            "nearest": {
                "size": len(_NEAREST_CACHE),
                "max_size": _NEAREST_CACHE_MAX,
                **_CACHE_STATS["nearest"],
            },
            "points": {
                "size": len(_POINT_CACHE),
                "max_size": _POINT_CACHE_MAX,
                **_CACHE_STATS["points"],
            },
        }


def _cache_get(name, cache, key):
    with _CACHE_LOCK:
        value = cache.get(key)
        if value is None:
            _CACHE_STATS[name]["misses"] += 1
            return None
        cache.move_to_end(key)
        _CACHE_STATS[name]["hits"] += 1
        return value


def _cache_put(cache, key, value, max_size):
    with _CACHE_LOCK:
        existing = cache.get(key)
        if existing is not None:
            cache.move_to_end(key)
            return existing
        cache[key] = value
        while len(cache) > max_size:
            cache.popitem(last=False)
        return value


def _prepare_windows_eccodes_runtime():
    """Expose the bundled ecCodes DLL for pure-Python Windows wheels."""
    if sys.platform != "win32":
        return
    try:
        spec = importlib.util.find_spec("eccodes")
        origin = getattr(spec, "origin", None)
        if not origin:
            return
        package_dir = os.path.dirname(os.path.abspath(origin))
        package_files = os.listdir(package_dir)
    except (ImportError, OSError, ValueError):
        return
    has_helper = any(
        name.startswith("_eccodes") and name.endswith(".pyd")
        for name in package_files
    )
    if has_helper or not os.path.isfile(os.path.join(package_dir, "eccodes.dll")):
        return
    os.environ["ECCODES_PYTHON_USE_FINDLIBS"] = "1"
    entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep)
               if entry]
    normalized = {os.path.normcase(os.path.abspath(entry)) for entry in entries}
    if os.path.normcase(package_dir) not in normalized:
        os.environ["PATH"] = os.pathsep.join([package_dir, *entries])


def load_eccodes():
    """Import and validate the optional ecCodes Python binding lazily."""
    _prepare_windows_eccodes_runtime()
    try:
        eccodes = importlib.import_module("eccodes")
        eccodes.codes_get_api_version()
    except Exception as exc:
        raise GribDecodeError(
            "direct Python GRIB decoding requires a working ecCodes runtime: "
            f"{exc}"
        ) from exc
    return eccodes


def _file_identity(path) -> _FileIdentity:
    resolved = Path(path).expanduser().resolve(strict=True)
    stat = resolved.stat()
    return _FileIdentity(
        os.path.normcase(os.fspath(resolved)),
        int(stat.st_size),
        int(stat.st_mtime_ns),
    )


def _safe_get(eccodes, message, key, default=None):
    try:
        return eccodes.codes_get(message, key)
    except Exception:
        return default


def _reset_multi_file(eccodes, source):
    reset = getattr(eccodes, "codes_grib_multi_support_reset_file", None)
    if reset is not None:
        reset(source)


@contextmanager
def _multi_field_source(identity: _FileIdentity, eccodes):
    """Open one GRIB while owning ecCodes' process-global multi-field state."""
    with _ECCODES_LOCK:
        eccodes.codes_grib_multi_support_on()
        try:
            with open(identity.path, "rb") as source:
                _reset_multi_file(eccodes, source)
                try:
                    yield source
                finally:
                    # ecCodes caches the remaining fields of a multi-field
                    # message by FILE*. Clear that state before Python closes
                    # the file, which also avoids a stale Windows file handle.
                    _reset_multi_file(eccodes, source)
        finally:
            eccodes.codes_grib_multi_support_off()


def _message_at(eccodes, source, offset, field_index):
    """Return one indexed field from the raw message beginning at ``offset``."""
    _reset_multi_file(eccodes, source)
    source.seek(int(offset))
    message = None
    for current_index in range(int(field_index) + 1):
        message = eccodes.codes_grib_new_from_file(source)
        if message is None:
            raise GribDecodeError(
                f"failed to reopen GRIB field {field_index} at byte {offset}"
            )
        if current_index != int(field_index):
            eccodes.codes_release(message)
            message = None
    return message


def _grid_signature(eccodes, message):
    digest = _safe_get(eccodes, message, "md5GridSection")
    if digest:
        return ("md5", str(digest))
    return (
        "keys",
        str(_safe_get(eccodes, message, "gridType", "")),
        int(_safe_get(eccodes, message, "numberOfPoints", -1)),
        int(_safe_get(eccodes, message, "Ni", -1)),
        int(_safe_get(eccodes, message, "Nj", -1)),
        int(_safe_get(eccodes, message, "Nx", -1)),
        int(_safe_get(eccodes, message, "Ny", -1)),
    )


_ROLE_ALIASES = (
    ("hght", ("gh", "hgt", "z")),
    ("tmp", ("t", "temperature")),
    ("rh", ("r", "relative_humidity")),
    ("q", ("q", "spfh", "specific_humidity")),
    ("u", ("u", "ugrd")),
    ("v", ("v", "vgrd")),
    ("omeg", ("w", "vvel", "dzdt")),
    ("vort", ("vo", "vort", "relative_vorticity")),
    ("absv", ("absv", "absolute_vorticity")),
)
_RELEVANT_SHORT_NAMES = frozenset(
    alias for _role, aliases in _ROLE_ALIASES for alias in aliases)


def _scan_inventory(identity: _FileIdentity, eccodes) -> _Inventory:
    raw_messages = []
    grid_signatures = set()
    fields_by_offset = defaultdict(int)
    with _multi_field_source(identity, eccodes) as source:
        while True:
            message = eccodes.codes_grib_new_from_file(
                source, headers_only=True)
            if message is None:
                break
            try:
                offset = int(eccodes.codes_get_message_offset(message))
                field_index = fields_by_offset[offset]
                fields_by_offset[offset] += 1
                level_type = str(_safe_get(
                    eccodes, message, "typeOfLevel", ""))
                if level_type not in {"isobaricInhPa", "isobaricInPa"}:
                    continue
                short_name = str(_safe_get(
                    eccodes, message, "shortName", "")).lower()
                if short_name not in _RELEVANT_SHORT_NAMES:
                    continue
                pressure = float(eccodes.codes_get(message, "level"))
                if level_type == "isobaricInPa":
                    pressure /= 100.0
                if not np.isfinite(pressure) or pressure <= 0.0:
                    continue
                missing_value = _safe_get(eccodes, message, "missingValue")
                try:
                    missing_value = float(missing_value)
                except (TypeError, ValueError):
                    missing_value = None
                signature = _grid_signature(eccodes, message)
                grid_signatures.add(signature)
                raw_messages.append((
                    offset,
                    field_index,
                    short_name,
                    pressure,
                    missing_value,
                    signature,
                ))
            finally:
                eccodes.codes_release(message)

    if not raw_messages:
        raise GribDecodeError(
            f"{identity.path!r} contains no supported pressure-level fields")
    if len(grid_signatures) != 1:
        raise GribDecodeError(
            "pressure-level sounding fields use inconsistent GRIB grids")

    by_short_name = defaultdict(list)
    for (
        offset,
        field_index,
        short_name,
        pressure,
        missing_value,
        _signature,
    ) in raw_messages:
        by_short_name[short_name].append(
            _MessageRef(offset, field_index, pressure, missing_value))

    selected_roles = []
    for role, aliases in _ROLE_ALIASES:
        # Match xarray's existing field behavior: candidate precedence is
        # global, not filled per level from lower-priority alternatives.
        selected_name = next(
            (alias for alias in aliases if by_short_name[alias]), None)
        if selected_name is None:
            continue
        unique_levels = {}
        for reference in by_short_name[selected_name]:
            unique_levels.setdefault(reference.pressure, reference)
        selected_roles.append(_RoleMessages(
            role,
            selected_name,
            tuple(unique_levels.values()),
        ))

    # RH takes precedence globally; q is used only when no RH field exists.
    if any(role.role == "rh" for role in selected_roles):
        selected_roles = [role for role in selected_roles if role.role != "q"]
    # Relative vorticity likewise takes precedence over absolute vorticity.
    if any(role.role == "vort" for role in selected_roles):
        selected_roles = [role for role in selected_roles if role.role != "absv"]

    present_roles = {role.role for role in selected_roles}
    requirements = (
        ("height", {"hght"}),
        ("temperature", {"tmp"}),
        ("moisture", {"rh", "q"}),
        ("u wind", {"u"}),
        ("v wind", {"v"}),
    )
    missing_roles = [
        label for label, alternatives in requirements
        if present_roles.isdisjoint(alternatives)
    ]
    if missing_roles:
        raise GribDecodeError(
            "missing required pressure-level fields: "
            + ", ".join(missing_roles)
        )

    levels = tuple(sorted({
        reference.pressure
        for role in selected_roles
        for reference in role.messages
    }, reverse=True))
    if not levels:
        raise GribDecodeError(
            f"{identity.path!r} contains no usable pressure levels")
    representative = min(
        (reference.offset, reference.field_index)
        for role in selected_roles
        for reference in role.messages
    )
    return _Inventory(
        levels,
        tuple(selected_roles),
        representative[0],
        representative[1],
    )


def _inventory_for(identity, eccodes):
    inventory = _cache_get("inventory", _INVENTORY_CACHE, identity)
    if inventory is not None:
        return inventory
    inventory = _scan_inventory(identity, eccodes)
    return _cache_put(
        _INVENTORY_CACHE, identity, inventory, _INVENTORY_CACHE_MAX)


def _normalize_longitude(longitude):
    return ((float(longitude) + 180.0) % 360.0) - 180.0


def _nearest_point(identity, inventory, latitude, longitude, eccodes):
    request_key = (identity, float(latitude), _normalize_longitude(longitude))
    point = _cache_get("nearest", _NEAREST_CACHE, request_key)
    if point is not None:
        return point
    with _multi_field_source(identity, eccodes) as source:
        message = _message_at(
            eccodes,
            source,
            inventory.representative_offset,
            inventory.representative_field_index,
        )
        try:
            nearest = eccodes.codes_grib_find_nearest(
                message, float(latitude), _normalize_longitude(longitude))
        finally:
            eccodes.codes_release(message)
    if not nearest:
        raise GribDecodeError("ecCodes returned no nearest GRIB grid point")
    nearest = nearest[0]
    point = _GridPoint(
        int(nearest["index"]),
        float(nearest["lat"]),
        _normalize_longitude(nearest["lon"]),
    )
    return _cache_put(
        _NEAREST_CACHE, request_key, point, _NEAREST_CACHE_MAX)


def _read_element(eccodes, message, index):
    getter = getattr(eccodes, "codes_get_double_element", None)
    if getter is not None:
        return float(getter(message, "values", int(index)))
    values = eccodes.codes_get_elements(message, "values", [int(index)])
    return float(values[0])


def _decode_selected_values(identity, inventory, point, missing, eccodes):
    selections = defaultdict(dict)
    for role in inventory.roles:
        for reference in role.messages:
            selections[reference.offset][reference.field_index] = (
                role, reference
            )

    decoded = defaultdict(dict)
    with _multi_field_source(identity, eccodes) as source:
        for offset in sorted(selections):
            selected_fields = selections[offset]
            _reset_multi_file(eccodes, source)
            source.seek(offset)
            last_field_index = max(selected_fields)
            for field_index in range(last_field_index + 1):
                message = eccodes.codes_grib_new_from_file(source)
                if message is None:
                    raise GribDecodeError(
                        f"failed to reopen GRIB field {field_index} "
                        f"at byte {offset}"
                    )
                try:
                    selection = selected_fields.get(field_index)
                    if selection is None:
                        continue
                    role, reference = selection
                    try:
                        value = _read_element(
                            eccodes, message, point.index
                        )
                    except Exception as exc:
                        raise GribDecodeError(
                            f"failed to read {role.short_name} at "
                            f"{reference.pressure:g} hPa: {exc}"
                        ) from exc
                    if (
                        not np.isfinite(value)
                        or (
                            reference.missing_value is not None
                            and value == reference.missing_value
                        )
                    ):
                        value = missing
                    decoded[role.role][reference.pressure] = value
                finally:
                    eccodes.codes_release(message)
    return decoded


def _valid(values, missing):
    return np.isfinite(values) & (values != missing)


def _dewpoint_from_rh(temperature_c, relative_humidity):
    a, b = 17.625, 243.04
    rh = np.clip(relative_humidity, 1e-3, 100.0)
    gamma = np.log(rh / 100.0) + (a * temperature_c) / (b + temperature_c)
    return (b * gamma) / (a - gamma)


def _dewpoint_from_q(specific_humidity, pressure_hpa):
    vapor_pressure = (
        specific_humidity * pressure_hpa
        / (0.622 + 0.378 * specific_humidity)
    )
    vapor_pressure = np.clip(vapor_pressure, 1e-6, None)
    a, b = 17.625, 243.04
    logarithm = np.log(vapor_pressure / 6.112)
    return (b * logarithm) / (a - logarithm)


def _assemble_point(inventory, point, decoded, missing):
    levels = np.asarray(inventory.levels, dtype=np.float64)
    matrix = np.full(
        (len(GRIB_COLUMN_NAMES), levels.size), missing, dtype=np.float64)
    matrix[0] = levels
    level_indexes = {level: index for index, level in enumerate(inventory.levels)}

    role_by_name = {role.role: role for role in inventory.roles}
    row_by_role = {"hght": 1, "tmp": 2, "omeg": 6, "u": 7, "v": 8}
    for role_name, row in row_by_role.items():
        for level, value in decoded.get(role_name, {}).items():
            matrix[row, level_indexes[level]] = value

    height_role = role_by_name.get("hght")
    if height_role is not None and height_role.short_name == "z":
        good = _valid(matrix[1], missing)
        matrix[1, good] /= _G0

    good_temperature = _valid(matrix[2], missing)
    matrix[2, good_temperature] -= _KELVIN_OFFSET

    moisture_role = "rh" if "rh" in decoded else "q" if "q" in decoded else None
    if moisture_role is not None:
        moisture = np.full(levels.size, missing, dtype=np.float64)
        for level, value in decoded[moisture_role].items():
            moisture[level_indexes[level]] = value
        good = good_temperature & _valid(moisture, missing)
        if moisture_role == "rh":
            matrix[3, good] = _dewpoint_from_rh(
                matrix[2, good], moisture[good])
        else:
            matrix[3, good] = _dewpoint_from_q(
                moisture[good], levels[good])

    good_wind = _valid(matrix[7], missing) & _valid(matrix[8], missing)
    u = matrix[7, good_wind]
    v = matrix[8, good_wind]
    matrix[4, good_wind] = (
        270.0 - np.degrees(np.arctan2(v, u))) % 360.0
    matrix[5, good_wind] = np.hypot(u, v) * _MS_TO_KNOTS

    surface_vorticity = None
    vorticity_role = "vort" if "vort" in decoded else \
        "absv" if "absv" in decoded else None
    if vorticity_role is not None:
        for level in inventory.levels:
            value = decoded[vorticity_role].get(level, missing)
            if not np.isfinite(value) or value == missing:
                continue
            if vorticity_role == "absv":
                coriolis = (
                    2.0 * _EARTH_ROTATION_RATE
                    * np.sin(np.radians(point.latitude))
                )
                value -= coriolis
            surface_vorticity = float(value)
            break

    return DecodedPoint(
        matrix,
        point.latitude,
        point.longitude,
        surface_vorticity,
    )


def decode_grib_point(path, lat, lon, *, missing=-9999.0) -> DecodedPoint:
    """Decode one pressure-level GRIB column without constructing xarray data.

    The first request for a file scans only message headers.  Each point then
    performs one nearest-grid lookup and requests one scalar element from every
    selected field/level message.  Exact or same-grid-cell requests reuse the
    immutable decoded point result.
    """
    latitude = float(lat)
    longitude = float(lon)
    missing_value = float(missing)
    if not np.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
        raise ValueError("latitude must be finite and within [-90, 90]")
    if not np.isfinite(longitude):
        raise ValueError("longitude must be finite")
    if not np.isfinite(missing_value):
        raise ValueError("missing must be a finite numeric sentinel")

    identity = _file_identity(path)
    eccodes = load_eccodes()
    inventory = _inventory_for(identity, eccodes)
    point = _nearest_point(
        identity, inventory, latitude, longitude, eccodes)
    point_key = (identity, point.index, missing_value)
    cached = _cache_get("points", _POINT_CACHE, point_key)
    if cached is not None:
        return cached
    decoded = _decode_selected_values(
        identity, inventory, point, missing_value, eccodes)
    result = _assemble_point(
        inventory, point, decoded, missing_value)
    return _cache_put(_POINT_CACHE, point_key, result, _POINT_CACHE_MAX)


__all__ = [
    "DecodedPoint",
    "GRIB_COLUMN_NAMES",
    "GribDecodeError",
    "clear_grib_caches",
    "decode_grib_point",
    "grib_cache_info",
    "load_eccodes",
]
