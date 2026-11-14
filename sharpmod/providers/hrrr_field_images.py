"""Image-space transforms for HRRR field products.

This module builds color lookup tables, contours, PNG tiles, and derived-field samples;
acquisition and overlay lifecycle are coordinated by ``hrrr_field_fetch`` and
``hrrr_field``."""

from __future__ import annotations

from sharpmod.providers.hrrr_products import Palette
from sharpmod.maps.map_overlays import MAX_RASTER_BYTES
import numpy as np
import sharpmod_rs
from sharpmod.providers import hrrr_field as _api


def index_map(size: tuple[int, int],
              bounds: tuple[float, float, float, float], *,
              model: str = "hrrr") -> _api._IndexMap:
    """Return the cached output-frame to native-model-grid index map.

    This is the expensive part of a first render -- a projection of every output
    pixel -- and it depends only on the frame geometry, so it is computed once
    and then shared by every product, every run and every forecast hour.
    """
    grid = _api.field_grid(model)
    key = (model, grid.shape, int(size[0]), int(size[1]), tuple(round(value, 6) for value in bounds))
    with _api._INDEX_LOCK:
        cached = _api._INDEX_CACHE.get(key)
        if cached is not None:
            _api._INDEX_CACHE.move_to_end(key)
            return cached

        try:
            from pyproj import CRS, Transformer
        except Exception as error:  # noqa: BLE001
            raise _api.HrrrFieldError(
                "model fields need pyproj to place the model grid; install the "
                "'era5' extra to enable them") from error

        width, height = int(size[0]), int(size[1])
        lon0, lon1, lat0, lat1 = bounds
        # Pixel centres, rows running north to south so row 0 is the north edge.
        lons = lon0 + (np.arange(width, dtype=np.float64) + 0.5) * (
            lon1 - lon0) / width
        lats = lat1 - (np.arange(height, dtype=np.float64) + 0.5) * (
            lat1 - lat0) / height
        lon_grid, lat_grid = np.meshgrid(lons, lats)

        if model in _api.GLOBAL_MODELS:
            # Stabilize half-cell ties against decimal grid-spacing roundoff.
            col = np.floor(np.round((lon_grid.ravel() + 180) / grid.spacing + 0.5, 8)).astype(np.int32) % grid.shape[1]
            row = np.floor(np.round((90 - lat_grid.ravel()) / grid.spacing + 0.5, 8)).astype(np.int32)
        else:
            if model in {"rap", "rdps"}:
                projection = sharpmod_rs.regional_map_projection(model)
            else:
                projection = _api.NAM_PROJ4 if model == "nam" or model in _api.HRW_MODELS else _api.HRRR_PROJ4
            transformer = Transformer.from_crs(
                CRS.from_epsg(4326),
                CRS.from_proj4(projection),
                always_xy=True)
            xs, ys = transformer.transform(lon_grid.ravel(), lat_grid.ravel())
            col = np.rint((np.asarray(xs) - grid.x0) / grid.spacing).astype(np.int32)
            row = np.rint((np.asarray(ys) - grid.y0) / grid.spacing).astype(np.int32)
        inside = ((col >= 0) & (col < grid.shape[1])
                  & (row >= 0) & (row < grid.shape[0]))
        flat = np.where(inside, row.astype(np.int64) * grid.shape[1] + col,
                        0).astype(np.int32)

        resolved = _api._IndexMap(width, height, tuple(bounds), flat, inside)
        _api._INDEX_CACHE[key] = resolved
        while len(_api._INDEX_CACHE) > _api._INDEX_CACHE_MAX_ENTRIES:
            _api._INDEX_CACHE.popitem(last=False)
        return resolved


def build_lut(palette: Palette) -> tuple[np.ndarray, float, float]:
    """Return ``(lut, low, high)`` for a palette.

    A banded palette fills each class with its own flat colour, so the map shows
    the same discrete classes the scale is defined in. A continuous one
    interpolates in RGB between stops.
    """
    return sharpmod_rs.build_map_lut(palette, _api._LUT_SIZE)


def colourize(field: np.ndarray, palette: Palette) -> np.ndarray:
    """Turn a field in display units into an RGBA image.

    Values outside the palette's drawn range, and anything non-finite, are fully
    transparent: a field overlay has to leave the basemap and the SPC risk areas
    beneath it visible wherever it has nothing to say. ``floor`` suppresses the
    low end and ``ceiling`` the high end, which is what a negative-signed field
    like convective inhibition needs -- its uninteresting values are the ones
    near zero, at the top of its scale.
    """
    return sharpmod_rs.colorize_map_field(field, palette, _api._LUT_SIZE)


def draw_contours(rgba: np.ndarray, field: np.ndarray, interval: float,
                  colour: str, width: float = 1.2) -> None:
    """Stamp isopleths of ``field`` into ``rgba`` in place.

    Marching-squares would give smoother lines, but a contour *band* is what
    reads correctly here and it is far cheaper: a pixel is on a contour when the
    field's value divided by the interval crosses an integer between it and its
    neighbour. That yields closed, connected lines one to two pixels wide with
    two array comparisons and no polygon assembly.
    """
    sharpmod_rs.draw_map_contours(rgba, field, interval, colour, width)


def encode_png(rgba: np.ndarray) -> bytes:
    """Encode RGBA through the shared native PNG runtime.

    The existing overlay byte cap is enforced before the PNG is returned.
    """
    return sharpmod_rs.encode_map_png(rgba, MAX_RASTER_BYTES)


def sample_derived(product_key: str, run, fxx: int, lat: float, lon: float,
                   *, size=_api.DEFAULT_FRAME_SIZE,
                   bounds=_api.COVERAGE_BOUNDS):
    """Sample the remembered derived grid at ``lat``/``lon`` (T18.4).

    Returns ``(value, sampled_lat, sampled_lon, distance_km, covered, note)``
    where the value is in the product's display units and the sampled
    coordinate is the centre of the nearest frame cell. ``covered`` is
    ``False`` outside the model domain; ``value`` is ``None`` (with
    ``covered`` still ``True``) where no derived grid is remembered or the
    frame holds no data there. ``note`` names a palette-range limit when the
    read value lies outside what the map draws. Never raises for bad input:
    that path just reports "not covered".
    """
    from sharpmod.maps.map_field_inspection import haversine_km

    try:
        from sharpmod.providers import hrrr_products

        product = hrrr_products.get_product(product_key)
    except Exception:  # noqa: BLE001 - unknown product reads as uncovered
        return None, float(lat), float(lon), 0.0, False, ""
    if str(getattr(product, "key", "")) != str(product_key):
        return None, float(lat), float(lon), 0.0, False, ""
    grid = _api.cached_derived(product.key, run, fxx, size)
    if grid is None:
        return None, float(lat), float(lon), 0.0, True, ""
    try:
        import numpy as _np

        # The remembered frame is already numeric.  Casting every 3072x1536
        # float32 field to float64 here allocated 36 MiB per hover (and per
        # sibling crosshair) just to read a single cell.  Convert only that
        # cell to Python float below; keep the array as a zero-copy view.
        field = _np.asarray(grid)
        height, width = int(field.shape[0]), int(field.shape[1])
    except Exception:  # noqa: BLE001 - an unusable cache reads as no value
        return None, float(lat), float(lon), 0.0, True, ""
    lon0, lon1, lat0, lat1 = (float(value) for value in bounds)
    try:
        latitude = max(-90.0, min(90.0, float(lat)))
        longitude = float(lon)
    except (TypeError, ValueError, OverflowError):
        return None, float(lat), float(lon), 0.0, False, ""
    if not (lon0 <= longitude <= lon1 and lat0 <= latitude <= lat1):
        try:
            from sharpmod.models.lambert_grid import HRRR_GRID

            if not HRRR_GRID.contains(latitude, longitude):
                return None, latitude, longitude, 0.0, False, ""
        except Exception:  # noqa: BLE001 - fall back to the frame envelope
            return None, latitude, longitude, 0.0, False, ""
    column = int((longitude - lon0) / (lon1 - lon0) * width)
    row = int((lat1 - latitude) / (lat1 - lat0) * height)
    column = max(0, min(width - 1, column))
    row = max(0, min(height - 1, row))
    try:
        raw = float(field[row, column])
    except (IndexError, TypeError, ValueError):
        return None, latitude, longitude, 0.0, True, ""
    sampled_lon = lon0 + (column + 0.5) * (lon1 - lon0) / width
    sampled_lat = lat1 - (row + 0.5) * (lat1 - lat0) / height
    try:
        distance = float(haversine_km(latitude, longitude, sampled_lat,
                                      sampled_lon))
    except Exception:  # noqa: BLE001 - distance never breaks a readout
        distance = 0.0
    import math as _math

    if not _math.isfinite(raw):
        return None, sampled_lat, sampled_lon, distance, True, ""
    display_note = ""
    try:
        palette = product.palette
        floor = getattr(palette, "floor", None)
        ceiling = getattr(palette, "ceiling", None)
        if floor is not None and raw < float(floor):
            display_note = f"below drawn range (from {float(floor):g})"
        elif ceiling is not None and raw > float(ceiling):
            display_note = f"above drawn range (to {float(ceiling):g})"
    except Exception:  # noqa: BLE001 - range notes are advisory
        display_note = ""
    return raw, sampled_lat, sampled_lon, distance, True, display_note
