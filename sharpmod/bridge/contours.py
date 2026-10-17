"""Isolines of a product's contour field as map line geometry.

One handler extracts the contour lines of a height/wind product's own contour
field (``providers.hrrr_products.ContourSpec``) from the reprojected frame, as
segments in lon/lat; the frontend draws them. This is the "backend contour
operation" the map notes ask for: the legacy path stamps the same lines into
its PNG (``hrrr_field_images.draw_contours``) where no client can read them.

The extraction is marching squares: for every grid edge whose endpoints
straddle a level, the crossing is linearly interpolated between the two
samples, and the crossings of each cell are paired into segments. Levels are
the multiples of the product's interval, the same level set ``draw_contours``
bands between. Cells with a non-finite corner contribute nothing, so holes and
below-ground stay empty instead of drawing a line along the domain edge.
"""

from __future__ import annotations

import math
import base64
from typing import Any

import numpy as np

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError

#: Decimal places kept for line vertices: about a metre, finer than the frame.
POINT_DECIMALS = 5
#: Ceiling on the segments of one request. A level set denser than this would
#: be a hairball on screen and would approach the protocol's frame cap; the
#: honest answer is that the product is too dense to draw at this interval.
MAX_SEGMENTS = 100_000


def wind_grid(product, decoded, mapping):
    """Packed display winds: low 9 bits are wind-from degrees, high 7 are 5 kt."""
    from sharpmod.models.lambert_grid import HRRR_GRID
    from sharpmod.providers.hrrr_products import ms_to_kt

    names = {field.variable: field.name for field in product.fields}
    if not {"UGRD", "VGRD"} <= names.keys():
        return None
    u, v = (
        np.asarray(decoded[names[key]], dtype=np.float32) for key in ("UGRD", "VGRD")
    )
    if u.shape != HRRR_GRID.shape or v.shape != u.shape:
        from sharpmod.providers.hrrr_field import HrrrFieldError

        raise HrrrFieldError("wind components do not match the HRRR grid")
    u, v = u.ravel()[mapping.flat], v.ravel()[mapping.flat]
    valid = mapping.inside & np.isfinite(u) & np.isfinite(v)
    steps = np.floor(ms_to_kt(np.hypot(u, v)) / 5 + 0.5)
    if np.any(valid & (steps > 127)):
        from sharpmod.providers.hrrr_field import HrrrFieldError

        raise HrrrFieldError("wind exceeds the display encoding range")
    # These CONUS models use grid-relative winds. Lambert convergence rotates them
    # to true north before reprojection (NCAR's uvmet convention).
    x = HRRR_GRID.x0 + mapping.flat % HRRR_GRID.shape[1] * HRRR_GRID.spacing
    y = HRRR_GRID.y0 + mapping.flat // HRRR_GRID.shape[1] * HRRR_GRID.spacing
    convergence = np.degrees(np.arctan2(x, HRRR_GRID._rho0 - y))
    direction = np.rint((270 - np.degrees(np.arctan2(v, u)) + convergence) % 360) % 360
    packed = np.full(u.shape, 65535, dtype="<u2")
    packed[valid] = (steps[valid].astype(np.uint16) << 9) | direction[valid].astype(
        np.uint16
    )
    from sharpmod.bridge.fields import _bounds

    return {
        "width": mapping.width,
        "height": mapping.height,
        "bounds": _bounds(mapping.bounds),
        "data_base64": base64.b64encode(packed.tobytes()).decode("ascii"),
    }


#: Cell edge crossings, keyed by the edge bitmask (T*1 + R*2 + B*4 + L*8).
#: The number of crossed edges in a cell is always even, so only these seven
#: codes occur; 15 is the saddle (all four corners alternate), paired T-R with
#: B-L by fixed convention so neighbouring cells still share their edge points
#: exactly and line segments meet.
_EDGE_PAIRS: dict[int, tuple[tuple[str, str], ...]] = {
    3: (("T", "R"),),
    5: (("T", "B"),),
    6: (("R", "B"),),
    9: (("T", "L"),),
    10: (("R", "L"),),
    12: (("B", "L"),),
    15: (("T", "R"), ("B", "L")),
}


def _crossings(values: np.ndarray, level: float) -> tuple[np.ndarray, np.ndarray]:
    """Return per-edge interpolation fractions (NaN where no crossing).

    ``horizontal[r, c]`` sits between samples ``(r, c)`` and ``(r, c + 1)``;
    ``vertical[r, c]`` between ``(r, c)`` and ``(r + 1, c)``. Both are NaN at
    edges whose endpoints are not both finite, so a crossing needs a real value
    on either side -- the rule ``draw_contours`` applies.
    """
    both = np.isfinite(values)
    with np.errstate(invalid="ignore", divide="ignore"):
        horizontal = (values[:, :-1] - level) / (values[:, :-1] - values[:, 1:])
        vertical = (values[:-1, :] - level) / (values[:-1, :] - values[1:, :])
    edge = (values[:, :-1] < level) != (values[:, 1:] < level)
    horizontal = np.where(edge & both[:, :-1] & both[:, 1:], horizontal, np.nan)
    edge = (values[:-1, :] < level) != (values[1:, :] < level)
    vertical = np.where(edge & both[:-1, :] & both[1:, :], vertical, np.nan)
    return horizontal, vertical


def _edge_xy(
    edge: str, row: int, col: int, horizontal: np.ndarray, vertical: np.ndarray
) -> tuple[float, float]:
    """The crossing point of one cell edge, in sample coordinates.

    Sample ``(row, col)`` is ``(col, row)`` and a crossing interpolates
    between the two samples of its edge, so a shared edge computes one point
    for both of its cells and segments meet exactly.
    """
    if edge == "T":
        return col + float(horizontal[row, col]), float(row)
    if edge == "B":
        return col + float(horizontal[row + 1, col]), float(row + 1)
    if edge == "L":
        return float(col), row + float(vertical[row, col])
    return float(col + 1), row + float(vertical[row, col + 1])


def contour_segments(
    field: np.ndarray, interval: float
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Return the isoline segments of ``field`` at every multiple of ``interval``."""
    values = np.asarray(field, dtype=np.float64)
    if values.ndim != 2 or not math.isfinite(interval) or interval <= 0:
        return []
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return []
    first = math.floor(float(finite.min()) / interval) + 1
    last = math.floor(float(finite.max()) / interval)
    out: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for step in range(first, last + 1):
        horizontal, vertical = _crossings(values, step * interval)
        present = {
            "T": np.isfinite(horizontal[:-1, :]),
            "R": np.isfinite(vertical[:, 1:]),
            "B": np.isfinite(horizontal[1:, :]),
            "L": np.isfinite(vertical[:, :-1]),
        }
        code = (
            present["T"].astype(np.uint8)
            + 2 * present["R"].astype(np.uint8)
            + 4 * present["B"].astype(np.uint8)
            + 8 * present["L"].astype(np.uint8)
        )
        for case, pairs in _EDGE_PAIRS.items():
            for row, col in np.argwhere(code == case):
                for first_edge, second_edge in pairs:
                    out.append(
                        (
                            _edge_xy(first_edge, row, col, horizontal, vertical),
                            _edge_xy(second_edge, row, col, horizontal, vertical),
                        )
                    )
                    if len(out) > MAX_SEGMENTS:
                        raise BridgeError(
                            "FIELD_FETCH_FAILED",
                            "The contour lines of this product are too dense to draw.",
                            False,
                        )
    return out


def contours(params: dict[str, Any]) -> dict[str, Any]:
    """The isolines of one product's contour field, as lon/lat segments."""
    from sharpmod.bridge import fields as bridge_fields
    from sharpmod.providers import hrrr_field

    model, product, run, hour = bridge_fields._request(params)
    if product.contour is None and not product.wind:
        raise p.invalid(f"product {product.key!r} carries no contour lines")
    from sharpmod.bridge import frame_cache
    target = frame_cache.path("contours", model, product.key, run, hour, bridge_fields.FRAME)
    cached = frame_cache.read(target)
    if cached is not None:
        return cached
    spec = product.contour
    convert = spec.convert if spec is not None and spec.convert is not None else (lambda values: values)
    # The contour field is a separate record from the product's own; take it
    # from one download of all the product's fields.
    try:
        decoded = hrrr_field._download_fields(
            product,
            run,
            hour,
            opener=hrrr_field._default_opener,
            timeout=hrrr_field._remote_limits()[0],
            should_cancel=None,
            model=model,
        )
        if spec is not None:
            raw = np.asarray(convert(decoded[spec.name]), dtype=np.float32)
            if raw.shape != hrrr_field.HRRR_SHAPE:
                raise hrrr_field.HrrrFieldError(f"contour field is a {raw.shape} grid")
            mapping = hrrr_field.index_map(bridge_fields.FRAME, hrrr_field.COVERAGE_BOUNDS)
            grid = hrrr_field.reproject(raw, mapping)
        # shortcut: half-size winds already exceed the 64-pixel barb density at
        # map MAX_ZOOM 9; enlarge this frame if that maximum zoom is raised.
        wind_size = tuple(max(1, side // 2) for side in bridge_fields.FRAME)
        wind = wind_grid(
            product,
            decoded,
            hrrr_field.index_map(wind_size, hrrr_field.COVERAGE_BOUNDS),
        )
    except hrrr_field.HrrrFieldUnavailable:
        raise BridgeError(
            "FIELD_UNAVAILABLE", f"{product.label} is not published.", False
        ) from None
    except hrrr_field.HrrrFieldOffline:
        raise BridgeError(
            "FIELD_FETCH_FAILED", f"{product.label} could not be downloaded.", True
        ) from None
    except hrrr_field.HrrrFieldError:
        raise BridgeError(
            "FIELD_FETCH_FAILED", f"{product.label} could not be read.", False
        ) from None
    width, height = bridge_fields.FRAME
    lon0, lon1, lat0, lat1 = hrrr_field.COVERAGE_BOUNDS

    def to_lon(x: float) -> float:
        return lon0 + (x + 0.5) * (lon1 - lon0) / width

    def to_lat(y: float) -> float:
        return lat1 - (y + 0.5) * (lat1 - lat0) / height

    segments = [
        [
            [round(to_lon(ax), POINT_DECIMALS), round(to_lat(ay), POINT_DECIMALS)],
            [round(to_lon(bx), POINT_DECIMALS), round(to_lat(by), POINT_DECIMALS)],
        ]
        for (ax, ay), (bx, by) in (contour_segments(grid, float(spec.interval)) if spec is not None else [])
    ]
    return frame_cache.write(target, {
        "product": product.key,
        "run_time": p.iso(run),
        "forecast_hour": hour,
        "unit": spec.label if spec is not None else "kt",
        "interval": float(spec.interval) if spec is not None else 5.0,
        "segments": segments,
        "wind": wind,
    })


HANDLERS = {"fields.contours": contours}
