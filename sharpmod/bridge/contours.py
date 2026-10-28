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
bands between. Edges with a non-finite endpoint contribute no crossings, so holes and
below-ground stay empty instead of drawing a line along the domain edge.
"""

from __future__ import annotations

import base64
from typing import Any

import numpy as np

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError

#: Ceiling on the segments of one request. A level set denser than this would
#: be a hairball on screen and would approach the protocol's frame cap; the
#: honest answer is that the product is too dense to draw at this interval.
MAX_SEGMENTS = 100_000


def wind_grid(product, decoded, mapping, *, model="hrrr"):
    """Packed display winds: low 9 bits are wind-from degrees, high 7 are 5 kt."""
    from sharpmod.providers.hrrr_field import field_grid
    from sharpmod.providers.hrrr_products import ms_to_kt
    from sharpmod.providers.hrrr_field import HrrrFieldError
    grid = field_grid(model)

    names = {field.variable: field.name for field in product.fields}
    if not {"UGRD", "VGRD"} <= names.keys():
        if product.wind:
            raise HrrrFieldError("wind product is missing a required component")
        return None
    if any(names[key] not in decoded for key in ("UGRD", "VGRD")):
        raise HrrrFieldError("decoded wind is missing a required component")
    u, v = (
        np.asarray(decoded[names[key]], dtype=np.float32) for key in ("UGRD", "VGRD")
    )
    if u.shape != grid.shape or v.shape != u.shape:
        from sharpmod.providers.hrrr_field import HrrrFieldError

        raise HrrrFieldError("wind components do not match the model grid")
    u, v = u.ravel()[mapping.flat], v.ravel()[mapping.flat]
    valid = mapping.inside & np.isfinite(u) & np.isfinite(v)
    steps = np.floor(ms_to_kt(np.hypot(u, v)) / 5 + 0.5)
    if np.any(valid & (steps > 127)):
        from sharpmod.providers.hrrr_field import HrrrFieldError

        raise HrrrFieldError("wind exceeds the display encoding range")
    # These CONUS models use grid-relative winds. Lambert convergence rotates them
    # to true north before reprojection (NCAR's uvmet convention).
    from sharpmod.providers.hrrr_products import GLOBAL_MODELS
    convergence = 0
    if model not in GLOBAL_MODELS:
        x = grid.x0 + mapping.flat % grid.shape[1] * grid.spacing
        y = grid.y0 + mapping.flat // grid.shape[1] * grid.spacing
        convergence = np.degrees(np.arctan2(x, grid._rho0 - y))
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


def contour_segments(field: np.ndarray, interval: float, *, bounds=None):
    """Compatibility wiring; native extraction and projection release the interpreter."""
    import sharpmod_rs

    try:
        return sharpmod_rs.contour_segments(np.asarray(field, dtype=np.float64), interval, MAX_SEGMENTS, bounds)
    except ValueError as exc:
        raise BridgeError("FIELD_FETCH_FAILED", str(exc), False) from None


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
    result = bridge_fields._acquire(model, product, run, hour)[1]
    if isinstance(result, BridgeError):
        raise result
    return result


def _from_decoded(model, product, run, hour, decoded):
    from sharpmod.bridge import fields as bridge_fields, frame_cache
    from sharpmod.providers import hrrr_field
    bounds = hrrr_field.field_bounds(model)
    native = hrrr_field.field_grid(model)
    # Oversampling a coarse native grid adds staircase segments, not contour detail.
    width, height = (min(bridge_fields.FRAME[0], native.shape[1]),
                     min(bridge_fields.FRAME[1], native.shape[0]))

    target = frame_cache.path("contours", model, product.key, run, hour, bridge_fields.FRAME)
    spec = product.contour
    convert = spec.convert if spec is not None and spec.convert is not None else (lambda values: values)
    # The contour field is a separate record from the product's own; take it
    # from one download of all the product's fields.
    try:
        if spec is not None:
            if spec.name not in decoded:
                raise hrrr_field.HrrrFieldError("decoded contour field is missing")
            raw = np.asarray(convert(decoded[spec.name]), dtype=np.float32)
            if raw.shape != hrrr_field.field_grid(model).shape:
                raise hrrr_field.HrrrFieldError(f"contour field is a {raw.shape} grid")
            mapping = hrrr_field.index_map((width, height), bounds, model=model)
            grid = hrrr_field.reproject(raw, mapping)
        # shortcut: half-size winds already exceed the 64-pixel barb density at
        # map MAX_ZOOM 9; enlarge this frame if that maximum zoom is raised.
        wind_size = tuple(max(1, side // 2) for side in bridge_fields.FRAME)
        wind = wind_grid(
            product,
            decoded,
            hrrr_field.index_map(wind_size, bounds, model=model),
            model=model,
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
    segments = contour_segments(grid, float(spec.interval), bounds=bounds) if spec is not None else []
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
