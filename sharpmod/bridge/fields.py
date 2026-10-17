"""The map catalogue and model field grids for the map workspace.

Every value comes from sharpmod: the products and their palettes from
``providers.hrrr_products``, the grid from ``providers.hrrr_field`` (the same
download, derivation, and reprojection the legacy map colours), and the radar
and SPC product lists from their providers. The grid travels as numbers,
quantized to uint16 with a stated scale and offset, so the frontend colours it.
"""

from __future__ import annotations

import base64
from datetime import timedelta
from typing import Any

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError

#: All supported field routes request the common 3 km CONUS Lambert output
#: grid, including NAM and RRFS/REFS; they do not request native model grids.
#: The output frame over its coverage bounds: about 3.3 by 3.5 km a cell at
#: 35N, close to the native 3 km grid.
# ponytail: the grid rides base64 inside one 8 MiB JSON frame, which caps the
# frame at about 2.9 M cells; protocol 2's binary bodies would lift that.
FRAME = (2048, 1024)
#: The quantized value that marks a cell with no data.
NODATA = 65535
#: The outlook products the map draws as separate layers.
OUTLOOK_PRODUCTS = ("cat", "torn", "wind", "hail")


def _palette(palette: Any) -> dict[str, Any]:
    return {
        "stops": [[float(value), colour] for value, colour in palette.stops],
        "units": palette.units,
        "display": palette.display,
        "stepped": bool(palette.stepped),
        "floor": None if palette.floor is None else float(palette.floor),
        "ceiling": None if palette.ceiling is None else float(palette.ceiling),
        "ticks": [float(tick) for tick in palette.tick_values()],
    }


def _bounds(bounds: tuple[float, float, float, float]) -> dict[str, float]:
    west, east, south, north = (float(value) for value in bounds)
    return {"west": west, "east": east, "south": south, "north": north}


def catalog(params: dict[str, Any]) -> dict[str, Any]:
    """The selected model's supported field products, radar products, outlooks."""
    p.keys(params, (), ("model",))
    from sharpmod.providers import hrrr_field, hrrr_products, radar_mosaic, radar_site, spc_outlook
    model = p.optional_text(params, "model", 40) or "hrrr"
    # Other forecast models can still select runs and soundings, with map fields off.
    if model not in hrrr_products.FIELD_MODELS:
        model = "hrrr"

    return {
        "field_model": model,
        "field_bounds": _bounds(hrrr_field.COVERAGE_BOUNDS),
        "max_forecast_hours": [
            (hrrr_field.max_forecast_hour(cycle) if model == "hrrr"
             else (84 if model == "rrfs-a" else 60) if cycle % 6 == 0 else 0)
            for cycle in range(24)
        ],
        "categories": [name for name, _ in hrrr_products.products_by_category(model)],
        "default_product": hrrr_products.DEFAULT_PRODUCT,
        "products": [
            {
                "key": product.key,
                "label": product.label,
                "short_label": product.short_label,
                "category": product.category,
                "description": product.description,
                "caveat": product.caveat,
                "palette": _palette(product.palette),
                "wind": product.wind,
                "contour": (
                    None
                    if product.contour is None
                    else {
                        "interval": float(product.contour.interval),
                        "label": product.contour.label,
                        "colour": product.contour.colour,
                        "width": float(product.contour.width),
                    }
                ),
            }
            for product in hrrr_products.available_products(model)
        ],
        "radar_products": [
            {
                "key": radar.key,
                "label": radar.label,
                "units": radar.units,
                "description": radar.description,
            }
            for radar in radar_mosaic.available_products()
        ],
        "site_products": [
            {
                "key": product.key,
                "label": product.label,
                "units": product.units,
                "description": product.description,
            }
            for product in radar_site.available_products()
        ],
        "outlook_products": [
            {"key": key, "label": spc_outlook.PRODUCTS[key].label}
            for key in OUTLOOK_PRODUCTS
        ],
    }


def _field_error(code: str, what: str, exc: Exception, retryable: bool) -> BridgeError:
    detail = (
        str(exc).strip().splitlines()[0][:200]
        if str(exc).strip()
        else type(exc).__name__
    )
    return BridgeError(code, f"{what}: {detail}.", retryable)


def _quantize(values: Any) -> tuple[bytes, float, float, float]:
    """uint16 little-endian cells (value = cell * scale + low), scale, low, high."""
    import numpy as np

    finite = np.isfinite(values)
    low = float(values[finite].min()) if finite.any() else 0.0
    high = float(values[finite].max()) if finite.any() else 0.0
    scale = (high - low) / (NODATA - 1) if high > low else 1.0
    cells = np.full(values.shape, NODATA, dtype="<u2")
    cells[finite] = np.rint((values[finite] - low) / scale)
    return cells.tobytes(), scale, low, high


def _request(params: dict[str, Any]):
    """Validate the same model/product/run selection for fields and contours."""
    p.keys(params, ("product", "run_time", "forecast_hour"), ("model",))
    from sharpmod.providers import hrrr_field, hrrr_products
    model = p.optional_text(params, "model", 40) or "hrrr"
    if model not in hrrr_products.FIELD_MODELS:
        raise p.invalid(f"model {model!r} has no map fields")
    key = p.text(params, "product", 1, 40)
    product = next((item for item in hrrr_products.available_products(model) if item.key == key), None)
    if product is None:
        raise p.invalid(f"product {key!r} is not a {model} field product")
    run = p.time(params, "run_time")
    if run.minute or run.second or run.microsecond:
        raise p.invalid("run_time must be on a whole hour")
    hour = p.integer(params, "forecast_hour", 0, 84)
    if model != "hrrr" and run.hour % 6:
        raise BridgeError("FIELD_UNAVAILABLE", f"{model} publishes 00, 06, 12 and 18Z runs.", False)
    limit = (hrrr_field.max_forecast_hour(run.hour) if model == "hrrr"
             else 84 if model == "rrfs-a" else 60)
    if hour > limit:
        raise BridgeError(
            "FIELD_UNAVAILABLE", f"{model.upper()} {run:%H}Z runs stop at F{limit:03d}.", False
        )
    return model, product, run, hour


def grid(params: dict[str, Any]) -> dict[str, Any]:
    """One selected model product/run/hour, as a lat/lon grid of values."""
    import numpy as np
    from sharpmod.providers import hrrr_field

    model, product, run, hour = _request(params)
    from sharpmod.bridge import frame_cache
    target = frame_cache.path("grid", model, product.key, run, hour, FRAME)
    cached = frame_cache.read(target)
    if cached is not None:
        return cached
    what = f"{model.upper()} {product.label}, {run:%Y-%m-%d %H}Z run, F{hour:03d}"
    cache_key = product.key if model == "hrrr" else f"{model}:{product.key}"
    values = hrrr_field.cached_derived(cache_key, run, hour, FRAME)
    if values is None:
        timeout, _limit = hrrr_field._remote_limits()
        try:
            decoded = hrrr_field._download_fields(
                product,
                run,
                hour,
                opener=hrrr_field._default_opener,
                timeout=timeout,
                should_cancel=None,
                model=model,
            )
            field = np.asarray(product.derive(decoded), dtype=np.float32)
            if field.shape != hrrr_field.HRRR_SHAPE:
                raise hrrr_field.HrrrFieldError(f"derived a {field.shape} grid")
            mapping = hrrr_field.index_map(FRAME, hrrr_field.COVERAGE_BOUNDS)
            values = hrrr_field.reproject(field, mapping)
        except hrrr_field.HrrrFieldUnavailable as exc:
            raise _field_error(
                "FIELD_UNAVAILABLE", f"{what} is not published", exc, False
            ) from None
        except hrrr_field.HrrrFieldOffline as exc:
            raise _field_error(
                "FIELD_FETCH_FAILED", f"{what} could not be downloaded", exc, True
            ) from None
        except hrrr_field.HrrrFieldError as exc:
            raise _field_error(
                "FIELD_FETCH_FAILED", f"{what} could not be read", exc, False
            ) from None
        hrrr_field.remember_derived(cache_key, run, hour, FRAME, values)
    data, scale, low, high = _quantize(np.asarray(values, dtype=np.float32))
    height, width = values.shape
    return frame_cache.write(target, {
        "product": product.key,
        "run_time": p.iso(run),
        "forecast_hour": hour,
        "valid_time": p.iso(run + timedelta(hours=hour)),
        "width": int(width),
        "height": int(height),
        "bounds": _bounds(hrrr_field.COVERAGE_BOUNDS),
        "units": product.palette.units,
        "scale": scale,
        "offset": low,
        "nodata": NODATA,
        "value_min": low,
        "value_max": high,
        "source_url": hrrr_field.grib_url(run, hour, product.sources[0], model),
        "attribution": hrrr_field.ATTRIBUTION if model == "hrrr" else (
            "NOAA/NCEP NAM via AWS Open Data" if model == "nam-3km-conus" else "NOAA/NCEP RRFS via NOMADS"
        ),
        "data_base64": base64.b64encode(data).decode("ascii"),
    })


HANDLERS = {
    "map.catalog": catalog,
    "fields.grid": grid,
}
