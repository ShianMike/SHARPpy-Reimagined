"""Low-level GRIB point decoding and wind/vorticity assembly helpers.

The Python backend uses these routines to turn selected GRIB messages into
backend-neutral point data; ``backends.grib`` retains the established decoder-facing
facade."""

from __future__ import annotations

from sharpmod.models.model_surface import merge_surface_level
from sharpmod.models.wind_reference import rotate_winds
import numpy as np
from sharpmod.backends import grib as _api


def decode_grib_wind_vorticities(path, points) -> tuple[float | None, ...]:
    """Estimate above-ground stencils with two wind unpacks per chosen level.

    This preserves the legacy centered finite-difference definition without
    constructing cfgrib/xarray wind cubes. It supports structured GRIB grids
    with stable ``Ni``/``Nj`` indexing and falls back cleanly for reduced or
    alternating-row layouts. Duplicate requests resolving to one grid cell
    share the same calculation. Missing surface pressure or an unusable
    above-ground stencil leaves this optional enrichment missing.
    """
    requests = []
    for value in points:
        try:
            latitude, longitude = value
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "each GRIB point must be a (latitude, longitude) pair"
            ) from exc
        if not np.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
            raise ValueError("latitude must be finite and within [-90, 90]")
        if not np.isfinite(longitude):
            raise ValueError("longitude must be finite")
        requests.append((latitude, longitude))
    if not requests:
        return ()

    identity = _api._file_identity(path)
    eccodes = _api.load_eccodes()
    inventory = _api._inventory_for(identity, eccodes)
    requested_points = [
        _api._nearest_point(identity, inventory, lat, lon, eccodes)
        for lat, lon in requests
    ]
    unique_points = []
    seen_indexes = set()
    for point in requested_points:
        if point.index not in seen_indexes:
            seen_indexes.add(point.index)
            unique_points.append(point)
    u_role = next(
        (value for value in inventory.roles if value.role == "u"), None
    )
    v_role = next(
        (value for value in inventory.roles if value.role == "v"), None
    )
    if u_role is None or v_role is None:
        raise _api.GribDecodeError("pressure-level GRIB has no compatible u/v pair")
    common_levels = {
        value.pressure for value in u_role.messages
    }.intersection(value.pressure for value in v_role.messages)
    if not common_levels:
        raise _api.GribDecodeError(
            "pressure-level GRIB u/v fields share no pressure level"
        )
    surface = next((role for role in inventory.surface_roles
                    if role.role == "surface_pressure"), None)
    results_by_index = {point.index: None for point in unique_points}
    if surface is not None:
        reference = surface.messages[0]
        with _api._multi_field_source(identity, eccodes) as source:
            message = _api._message_at(
                eccodes, source, reference.offset, reference.field_index
            )
            try:
                ni = int(_api._safe_get(eccodes, message, "Ni") or _api._safe_get(eccodes, message, "Nx", 0))
                nj = int(_api._safe_get(eccodes, message, "Nj") or _api._safe_get(eccodes, message, "Ny", 0))
                if (ni < 2 or nj < 2
                        or ni * nj != _api._safe_get(eccodes, message, "numberOfPoints")
                        or _api._safe_get(eccodes, message, "alternativeRowScanning", 0)):
                    raise _api.GribDecodeError("surface-pressure stencil requires a structured grid")
                j_consecutive = bool(_api._safe_get(eccodes, message, "jPointsAreConsecutive", 0))
                indexes = []
                for point in unique_points:
                    if j_consecutive:
                        ix, iy = divmod(point.index, nj)
                    else:
                        iy, ix = divmod(point.index, ni)
                    x0, x1 = _api._neighbor_pair(ix, ni)
                    y0, y1 = _api._neighbor_pair(iy, nj)
                    neighbors = ((ix, iy), (x0, iy), (x1, iy), (ix, y0), (ix, y1))
                    indexes.extend(x * nj + y if j_consecutive else y * ni + x for x, y in neighbors)
                pressures = _api._read_elements(
                    eccodes, message, indexes
                ).reshape(len(unique_points), 5)
            finally:
                eccodes.codes_release(message)
        groups = {}
        for point, values in zip(unique_points, pressures):
            if not _api._valid_reference_values(values, reference) or np.any(values <= 0):
                continue
            value = float(np.min(values))
            pressure = float(value) / 100.0 if value > 2000.0 else float(value)
            eligible = [level for level in common_levels if level <= pressure]
            if eligible:
                groups.setdefault(max(eligible), []).append(point)
        for pressure, group in groups.items():
            results_by_index.update(_wind_stencils(
                identity, inventory, group, pressure, eccodes
            ))
    return tuple(results_by_index[point.index] for point in requested_points)


def _wind_stencils(identity, inventory, unique_points, pressure, eccodes):
    u_reference = _api._wind_reference(inventory, "u", pressure)
    v_reference = _api._wind_reference(inventory, "v", pressure)

    with _api._multi_field_source(identity, eccodes) as source:
        u_message = _api._message_at(
            eccodes,
            source,
            u_reference.offset,
            u_reference.field_index,
        )
        try:
            ni = _api._safe_get(eccodes, u_message, "Ni")
            nj = _api._safe_get(eccodes, u_message, "Nj")
            if ni is None:
                ni = _api._safe_get(eccodes, u_message, "Nx")
            if nj is None:
                nj = _api._safe_get(eccodes, u_message, "Ny")
            try:
                ni, nj = int(ni), int(nj)
            except (TypeError, ValueError, OverflowError) as exc:
                raise _api.GribDecodeError(
                    "wind-vorticity stencil requires a structured GRIB grid"
                ) from exc
            number_of_points = int(_api._safe_get(
                eccodes, u_message, "numberOfPoints", -1
            ))
            if (
                ni < 2 or nj < 2 or ni * nj != number_of_points
                or int(_api._safe_get(
                    eccodes, u_message, "alternativeRowScanning", 0
                ))
            ):
                raise _api.GribDecodeError(
                    "wind-vorticity stencil does not support this GRIB grid"
                )
            j_consecutive = bool(int(_api._safe_get(
                eccodes, u_message, "jPointsAreConsecutive", 0
            )))
            if j_consecutive:
                def grid_index(x_index, y_index):
                    return x_index * nj + y_index
            else:
                def grid_index(x_index, y_index):
                    return y_index * ni + x_index
            u_indexes = []
            v_indexes = []
            coordinate_indexes = []
            for point in unique_points:
                if j_consecutive:
                    ix, iy = divmod(point.index, nj)
                else:
                    iy, ix = divmod(point.index, ni)
                if not (0 <= ix < ni and 0 <= iy < nj):
                    raise _api.GribDecodeError(
                        "nearest GRIB point is outside the structured grid"
                    )
                x_pair = _api._neighbor_pair(ix, ni)
                y_pair = _api._neighbor_pair(iy, nj)
                if x_pair is None or y_pair is None:
                    raise _api.GribDecodeError(
                        "GRIB grid is too small for a wind-vorticity stencil"
                    )
                x0, x1 = x_pair
                y0, y1 = y_pair
                point_u_indexes = [
                    grid_index(ix, y0), grid_index(ix, y1)
                ]
                point_v_indexes = [
                    grid_index(x0, iy), grid_index(x1, iy)
                ]
                point_coordinate_indexes = [
                    point_v_indexes[0], point_v_indexes[1],
                    point_u_indexes[0], point_u_indexes[1], point.index,
                ]
                u_indexes.extend(point_u_indexes)
                v_indexes.extend(point_v_indexes)
                coordinate_indexes.extend(point_coordinate_indexes)
            u_values = _api._read_elements(
                eccodes, u_message, u_indexes
            ).reshape(len(unique_points), 2)
            latitudes = _api._read_key_elements(
                eccodes, u_message, "latitudes", coordinate_indexes
            ).reshape(len(unique_points), 5)
            longitudes = _api._read_key_elements(
                eccodes, u_message, "longitudes", coordinate_indexes
            ).reshape(len(unique_points), 5)
        finally:
            eccodes.codes_release(u_message)

    with _api._multi_field_source(identity, eccodes) as source:
        v_message = _api._message_at(
            eccodes,
            source,
            v_reference.offset,
            v_reference.field_index,
        )
        try:
            v_values = _api._read_elements(
                eccodes, v_message, v_indexes
            ).reshape(len(unique_points), 2)
        finally:
            eccodes.codes_release(v_message)

    results_by_index = {}
    for index, point in enumerate(unique_points):
        point_u = u_values[index]
        point_v = v_values[index]
        point_lats = latitudes[index]
        point_lons = longitudes[index]
        if (
            not _api._valid_reference_values(point_u, u_reference)
            or not _api._valid_reference_values(point_v, v_reference)
            or not np.all(np.isfinite(point_lats))
            or not np.all(np.isfinite(point_lons))
        ):
            results_by_index[point.index] = None
            continue
        lat_center = float(point_lats[4])
        dx = (
            _api._EARTH_RADIUS_M
            * np.cos(np.radians(lat_center))
            * np.radians(_api._wrapped_lon_delta(
                point_lons[0], point_lons[1]
            ))
        )
        dy = _api._EARTH_RADIUS_M * np.radians(
            float(point_lats[3]) - float(point_lats[2])
        )
        if (
            not np.isfinite(dx) or not np.isfinite(dy)
            or abs(dx) < 1.0 or abs(dy) < 1.0
        ):
            results_by_index[point.index] = None
            continue
        value = (
            (float(point_v[1]) - float(point_v[0])) / dx
            - (float(point_u[1]) - float(point_u[0])) / dy
        )
        results_by_index[point.index] = float(value) if np.isfinite(value) else None
    return results_by_index


def _assemble_point(inventory, point, decoded, missing):
    levels = np.asarray(inventory.levels, dtype=np.float64)
    matrix = np.full(
        (len(_api.GRIB_COLUMN_NAMES), levels.size), missing, dtype=np.float64)
    matrix[0] = levels
    level_indexes = {level: index for index, level in enumerate(inventory.levels)}

    role_by_name = {role.role: role for role in inventory.roles}
    row_by_role = {"hght": 1, "tmp": 2, "omeg": 6, "u": 7, "v": 8}
    for role_name, row in row_by_role.items():
        for level, value in decoded.get(role_name, {}).items():
            matrix[row, level_indexes[level]] = value

    height_role = role_by_name.get("hght")
    if height_role is not None and height_role.short_name == "z":
        good = _api._valid(matrix[1], missing)
        matrix[1, good] /= _api._G0

    good_temperature = _api._valid(matrix[2], missing)
    matrix[2, good_temperature] -= _api._KELVIN_OFFSET

    moisture_role = "rh" if "rh" in decoded else "q" if "q" in decoded else None
    if moisture_role is not None:
        moisture = np.full(levels.size, missing, dtype=np.float64)
        for level, value in decoded[moisture_role].items():
            moisture[level_indexes[level]] = value
        good = good_temperature & _api._valid(moisture, missing)
        if moisture_role == "rh":
            matrix[3, good] = _api._dewpoint_from_rh(
                matrix[2, good], moisture[good])
        else:
            matrix[3, good] = _api._dewpoint_from_q(
                moisture[good], levels[good])

    rotations = {
        role: {ref.pressure: ref.wind_rotation for ref in role_by_name[role].messages}
        for role in ("u", "v")
    }
    for index, level in enumerate(inventory.levels):
        u_rotation, v_rotation = rotations["u"].get(level), rotations["v"].get(level)
        if level in rotations["u"] and level in rotations["v"] and u_rotation != v_rotation:
            raise _api.GribDecodeError("u/v GRIB wind-reference metadata disagree")
        matrix[7, index], matrix[8, index] = rotate_winds(
            matrix[7, index], matrix[8, index], point.longitude,
            u_rotation or v_rotation, missing=missing,
        )
    good_wind = _api._valid(matrix[7], missing) & _api._valid(matrix[8], missing)
    u = matrix[7, good_wind]
    v = matrix[8, good_wind]
    matrix[4, good_wind] = (
        270.0 - np.degrees(np.arctan2(v, u))) % 360.0
    matrix[5, good_wind] = np.hypot(u, v) * _api._MS_TO_KNOTS

    surface_pressure = _api._decoded_surface_value(
        decoded, "surface_pressure", missing)
    if surface_pressure is not None and surface_pressure > 2000.0:
        surface_pressure /= 100.0
    surface_height = _api._decoded_surface_value(
        decoded, "surface_height", missing)
    surface_geopotential = _api._decoded_surface_value(
        decoded, "surface_geopotential", missing)
    if surface_height is None and surface_geopotential is not None:
        surface_height = surface_geopotential / _api._G0
    surface_temperature = _api._decoded_surface_value(
        decoded, "surface_temperature", missing)
    surface_dewpoint = _api._decoded_surface_value(
        decoded, "surface_dewpoint", missing)
    if surface_temperature is not None:
        surface_temperature -= _api._KELVIN_OFFSET
    surface_rh = _api._decoded_surface_value(decoded, "surface_rh", missing)
    surface_q = _api._decoded_surface_value(decoded, "surface_q", missing)
    if surface_q is not None and surface_pressure is not None:
        surface_dewpoint = float(_api._dewpoint_from_q(
            np.asarray([surface_q]),
            np.asarray([surface_pressure]),
        )[0])
    elif surface_dewpoint is not None:
        surface_dewpoint -= _api._KELVIN_OFFSET
    elif surface_temperature is not None:
        if surface_rh is not None:
            surface_dewpoint = float(_api._dewpoint_from_rh(
                np.asarray([surface_temperature]),
                np.asarray([surface_rh]),
            )[0])

    surface_u = _api._decoded_surface_value(decoded, "surface_u", missing)
    surface_v = _api._decoded_surface_value(decoded, "surface_v", missing)
    if surface_u is not None and surface_v is not None:
        surface_roles = {role.role: role for role in inventory.surface_roles}
        u_rotation = surface_roles["surface_u"].messages[0].wind_rotation
        v_rotation = surface_roles["surface_v"].messages[0].wind_rotation
        if u_rotation != v_rotation:
            raise _api.GribDecodeError("surface u/v wind-reference metadata disagree")
        surface_u, surface_v = rotate_winds(
            surface_u, surface_v, point.longitude, u_rotation)
    surface_merge = merge_surface_level(
        {
            name: matrix[index]
            for index, name in enumerate(_api.GRIB_COLUMN_NAMES)
        },
        {
            "pres": surface_pressure,
            "hght": surface_height,
            "tmpc": surface_temperature,
            "dwpc": surface_dewpoint,
            "u": surface_u,
            "v": surface_v,
        },
        missing=missing,
    )
    if surface_merge is not None:
        matrix = np.vstack([
            surface_merge.columns[name] for name in _api.GRIB_COLUMN_NAMES
        ])

    surface_vorticity = None
    vorticity_role = "vort" if "vort" in decoded else \
        "absv" if "absv" in decoded else None
    if vorticity_role is not None:
        for level in inventory.levels:
            if (
                surface_merge is not None
                and level > surface_merge.surface_pressure
            ):
                continue
            value = decoded[vorticity_role].get(level, missing)
            if not np.isfinite(value) or value == missing:
                continue
            if vorticity_role == "absv":
                coriolis = (
                    2.0 * _api._EARTH_ROTATION_RATE
                    * np.sin(np.radians(point.latitude))
                )
                value -= coriolis
            surface_vorticity = float(value)
            break

    return _api.DecodedPoint(
        matrix,
        point.latitude,
        point.longitude,
        surface_vorticity,
        surface_merge is not None,
        0 if surface_merge is None else surface_merge.removed_levels,
    )


def decode_grib_points(path, points, *, missing=-9999.0) -> tuple[_api.DecodedPoint, ...]:
    """Decode several points while unpacking every selected message once.

    This is a vectorized multi-point operation, not speculative decoder
    threading. Duplicate requests that resolve to one grid cell share the same
    immutable :class:`DecodedPoint` and all results populate the normal point
    cache used by later scalar calls.
    """
    requests = []
    for value in points:
        try:
            latitude, longitude = value
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "each GRIB point must be a (latitude, longitude) pair"
            ) from exc
        if not np.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
            raise ValueError("latitude must be finite and within [-90, 90]")
        if not np.isfinite(longitude):
            raise ValueError("longitude must be finite")
        requests.append((latitude, longitude))
    if not requests:
        return ()
    missing_value = float(missing)
    if not np.isfinite(missing_value):
        raise ValueError("missing must be a finite numeric sentinel")

    identity = _api._file_identity(path)
    eccodes = _api.load_eccodes()
    inventory = _api._inventory_for(identity, eccodes)
    requested_points = [
        _api._nearest_point(identity, inventory, lat, lon, eccodes)
        for lat, lon in requests
    ]
    unique_points = []
    point_by_index = {}
    for point in requested_points:
        if point.index not in point_by_index:
            point_by_index[point.index] = point
            unique_points.append(point)

    results_by_index = {}
    missing_points = []
    for point in unique_points:
        key = (identity, point.index, missing_value)
        cached = _api._cache_get("points", _api._POINT_CACHE, key)
        if cached is None:
            missing_points.append(point)
        else:
            results_by_index[point.index] = cached
    if missing_points:
        decoded_values = _api._decode_selected_values_many(
            identity, inventory, missing_points, missing_value, eccodes
        )
        for point, decoded in zip(missing_points, decoded_values):
            result = _api._assemble_point(
                inventory, point, decoded, missing_value
            )
            key = (identity, point.index, missing_value)
            results_by_index[point.index] = _api._cache_put(
                _api._POINT_CACHE, key, result, _api._POINT_CACHE_MAX
            )
    return tuple(results_by_index[point.index] for point in requested_points)
