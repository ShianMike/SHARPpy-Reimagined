"""Map product catalogues and native-model grids transported as quantized frames."""

from __future__ import annotations

import base64
from concurrent.futures import Future
from datetime import timedelta
from typing import Any
import threading

from sharpmod.bridge import cancellation, params as p
from sharpmod.bridge.errors import BridgeError
from sharpmod.bridge.map_catalog import catalog
from sharpmod.models.model_transport import DownloadCancelled

#: Fixed output dimensions; the geographic extent follows the selected model.
# ponytail: the grid rides base64 inside one 8 MiB JSON frame, which caps the
# frame at about 2.9 M cells; protocol 2's binary bodies would lift that.
FRAME = (2048, 1024)
#: The quantized value that marks a cell with no data.
NODATA = 65535
#: The outlook products the map draws as separate layers.
OUTLOOK_PRODUCTS = ("cat", "torn", "wind", "hail")
_FLIGHT_LOCK = threading.Lock()
_FLIGHTS: dict[tuple, tuple[Future, list]] = {}


def _bounds(bounds: tuple[float, float, float, float]) -> dict[str, float]:
    # Sub-centimetre precision keeps projected bounds stable across PROJ platforms.
    west, east, south, north = (round(float(value), 8) for value in bounds)
    return {"west": west, "east": east, "south": south, "north": north}


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
    hour = p.integer(params, "forecast_hour", 0, 384)
    if model != "hrrr" and run.hour % 6:
        raise BridgeError("FIELD_UNAVAILABLE", f"{model} publishes 00, 06, 12 and 18Z runs.", False)
    if model in hrrr_products.HRW_MODELS and run.hour not in (0, 12):
        raise BridgeError("FIELD_UNAVAILABLE", "HRW publishes only 00 and 12Z runs.", False)
    if model == "aigfs":
        if hour % 6:
            raise BridgeError("FIELD_UNAVAILABLE", "AIGFS publishes six-hour steps through F384.", False)
        return model, product, run, hour
    if model in hrrr_products.ECMWF_MODELS or model == "gfs":
        from sharpmod.tools.model_extract import forecast_hours
        if hour not in forecast_hours(model, cycle_hour=run.hour):
            if model == "gfs":
                raise BridgeError("FIELD_UNAVAILABLE", "GFS publishes hourly steps through F120, then three-hour steps through F384.", False)
            raise BridgeError("FIELD_UNAVAILABLE", "ECMWF publishes three-hour steps through F144, then six-hour steps; 06/18Z runs stop at F144.", False)
        return model, product, run, hour
    limit = (hrrr_field.max_forecast_hour(run.hour) if model == "hrrr"
             else 48 if model in {"hrw-wrf-arw", "hrw-wrf-nssl"}
             else 84 if model in {"rrfs-a", "nam"} else 60)
    if hour > limit:
        raise BridgeError(
            "FIELD_UNAVAILABLE", f"{model.upper()} {run:%H}Z runs stop at F{limit:03d}.", False
        )
    if model == "nam" and hour % 3:
        raise BridgeError("FIELD_UNAVAILABLE", "NAM map fields publish every three forecast hours.", False)
    return model, product, run, hour


def grid(params: dict[str, Any]) -> dict[str, Any]:
    """One selected model product/run/hour, as a lat/lon grid of values."""
    from sharpmod.bridge.frame_cache import path, read
    model, product, run, hour = _request(params)
    return read(path("grid", model, product.key, run, hour, FRAME)) or _acquire(model, product, run, hour)[0]


def _acquire(model, product, run, hour):
    """Produce compact raster and auxiliary outputs from one cold acquisition."""
    from sharpmod.bridge import frame_cache

    cancellation.check()
    target = frame_cache.path("grid", model, product.key, run, hour, FRAME)
    auxiliary = frame_cache.path("contours", model, product.key, run, hour, FRAME)
    key = (str(target),)
    with _FLIGHT_LOCK:
        current = _FLIGHTS.get(key)
        owner = current is None or all(event is not None and event.is_set() for event in current[1])
        if owner:
            current = _FLIGHTS[key] = (Future(), [])
        flight, consumers = current
        consumer = cancellation.event()
        consumers.append(consumer)
    try:
        if not owner:
            while True:
                cancellation.check()
                try:
                    return flight.result(timeout=0.1)
                except TimeoutError:
                    if flight.done():
                        raise
        cached = frame_cache.read(target)
        lines = frame_cache.read(auxiliary) if product.contour is not None or product.wind else None
        if cached is None or ((product.contour is not None or product.wind) and lines is None):
            def stopped():
                with _FLIGHT_LOCK:
                    return all(event is not None and event.is_set() for event in consumers)
            cached, lines = _produce(model, product, run, hour, target, stopped)
        result = (cached, lines)
        flight.set_result(result)
        return result
    except BaseException as error:
        if owner:
            flight.set_exception(error)
        raise
    finally:
        with _FLIGHT_LOCK:
            consumers.remove(consumer)
            if owner and _FLIGHTS.get(key) is current:
                _FLIGHTS.pop(key, None)


def _produce(model, product, run, hour, target, stopped):
    import numpy as np
    from sharpmod.providers import hrrr_field
    from sharpmod.bridge import contours, frame_cache
    bounds = hrrr_field.field_bounds(model)
    what = f"{model.upper()} {product.label}, {run:%Y-%m-%d %H}Z run, F{hour:03d}"
    cache_key = product.key if model == "hrrr" else f"{model}:{product.key}"
    values = hrrr_field.cached_derived(cache_key, run, hour, FRAME)
    needs_auxiliary = product.contour is not None or product.wind
    lines = None
    if values is None or needs_auxiliary:
        timeout, _limit = hrrr_field._remote_limits()
        try:
            decoded = hrrr_field._download_fields(
                product,
                run,
                hour,
                opener=hrrr_field._default_opener,
                timeout=timeout,
                should_cancel=stopped,
                model=model,
            )
            if stopped():
                raise cancellation.Cancelled()
            missing = [item.name for item in product.fields if item.name not in decoded
                       and item.name != getattr(product.contour, "name", None)]
            if missing:
                raise hrrr_field.HrrrFieldError(f"decoded fields are missing: {', '.join(missing)}")
            field = np.asarray(product.derive(decoded), dtype=np.float32)
            if field.shape != hrrr_field.field_grid(model).shape:
                raise hrrr_field.HrrrFieldError(f"derived a {field.shape} grid")
            mapping = hrrr_field.index_map(FRAME, bounds, model=model)
            values = hrrr_field.reproject(field, mapping)
        except DownloadCancelled:
            raise cancellation.Cancelled() from None
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
    if stopped():
        raise cancellation.Cancelled()
    height, width = values.shape
    result = frame_cache.write(target, {
        "product": product.key,
        "run_time": p.iso(run),
        "forecast_hour": hour,
        "valid_time": p.iso(run + timedelta(hours=hour)),
        "width": int(width),
        "height": int(height),
        "bounds": _bounds(bounds),
        "units": product.palette.units,
        "scale": scale,
        "offset": low,
        "nodata": NODATA,
        "value_min": low,
        "value_max": high,
        "source_url": hrrr_field.grib_url(run, hour, product.sources[0], model),
        "attribution": ("ECMWF IFS via ECMWF Open Data (CC BY 4.0)" if model in hrrr_field.ECMWF_MODELS
                        else "NOAA/NCEP GFS via AWS Open Data" if model == "gfs"
                        else "NOAA/NCEP AIGFS via NOMADS" if model == "aigfs"
                        else hrrr_field.ATTRIBUTION if model == "hrrr"
                        else "NOAA/NCEP NAM via AWS Open Data" if model in {"nam", "nam-3km-conus"}
                        else "NOAA/NCEP HiResW via NOMADS" if model in hrrr_field.HRW_MODELS
                        else "NOAA/NCEP RRFS via NOMADS"),
        "data_base64": base64.b64encode(data).decode("ascii"),
    })
    if needs_auxiliary:
        try:
            lines = contours._from_decoded(model, product, run, hour, decoded)
        except BridgeError as error:
            lines = error  # Keep a usable scalar frame when its auxiliary geometry fails.
    return result, lines


HANDLERS = {
    "map.catalog": catalog,
    "fields.grid": grid,
}
