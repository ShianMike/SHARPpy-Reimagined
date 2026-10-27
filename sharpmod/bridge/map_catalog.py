"""Map product metadata and selected-run ingredient availability."""

from __future__ import annotations

from typing import Any
from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError


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


def catalog(params: dict[str, Any]) -> dict[str, Any]:
    """The selected model's supported field products, radar products, outlooks."""
    from sharpmod.bridge.fields import _bounds, _field_error, _request, OUTLOOK_PRODUCTS
    p.keys(params, (), ("model", "run_time", "forecast_hour"))
    from sharpmod.providers import hrrr_field, hrrr_products, radar_mosaic, radar_site, spc_outlook
    model = p.optional_text(params, "model", 40) or "hrrr"
    # Other forecast models can still select runs and soundings, with map fields off.
    if model not in hrrr_products.FIELD_MODELS:
        model = "hrrr"

    products = hrrr_products.available_products(model)
    is_ecmwf = model in hrrr_products.ECMWF_MODELS
    is_global = model in hrrr_products.GLOBAL_MODELS
    is_hrw = model in hrrr_products.HRW_MODELS
    model_label = "ECMWF 9km" if model == "ecmwf-ifs-9km" else "ECMWF" if is_ecmwf else model.upper()
    if (is_global or is_hrw) and ("run_time" in params or "forecast_hour" in params):
        run, hour = _request({**params, "product": "tmp-2m"})[-2:]
        try:
            if is_global:
                from sharpmod.providers.ecmwf_fields import available_products
                products = available_products(model, run, hour)
            else:
                rows = hrrr_field.fetch_inventory(hrrr_field.grib_url(run, hour, "sfc", model))
                products = tuple(product for product in products if all(
                    any(spec.matches(row.variable, row.level, row.forecast) for row in rows)
                    for spec in product.all_fields))
        except hrrr_field.HrrrFieldUnavailable as exc:
            raise _field_error("FIELD_UNAVAILABLE", f"{model_label} map fields are not published for this run and hour", exc, False) from None
        except hrrr_field.HrrrFieldError as exc:
            raise _field_error("FIELD_FETCH_FAILED", f"{model_label} field availability could not be checked", exc, True) from None
        if not products:
            raise BridgeError("FIELD_UNAVAILABLE", f"No supported {model_label} fields are published for this run and hour. Choose an earlier run or retry later.", False)
    if is_global or is_hrw:
        from sharpmod.tools.model_extract import forecast_hours
        cycles = (0, 12) if is_hrw else (0, 6, 12, 18)
        limits = [max(forecast_hours(model, cycle_hour=cycle)) if cycle in cycles else 0 for cycle in range(24)]
    else:
        limits = [(hrrr_field.max_forecast_hour(cycle) if model == "hrrr"
                   else (84 if model in {"rrfs-a", "nam"} else 60) if cycle % 6 == 0 else 0)
                  for cycle in range(24)]
    default = "tmp-2m" if is_global else hrrr_products.DEFAULT_PRODUCT

    return {
        "field_model": model,
        "field_bounds": _bounds(hrrr_field.field_bounds(model)),
        "max_forecast_hours": limits,
        "categories": [name for name in hrrr_products.CATEGORIES if any(product.category == name for product in products)],
        "default_product": default if any(product.key == default for product in products) else products[0].key,
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
            for product in products
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
