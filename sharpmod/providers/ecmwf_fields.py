"""ECMWF's published regular grids and their supported HRRR-style map products."""

from __future__ import annotations

import json
from dataclasses import replace
from functools import lru_cache
from types import SimpleNamespace

import numpy as np

from sharpmod.providers import hrrr_products as hp

BOUNDS = (-180.0, 180.0, -85.05112878, 85.05112878)
_SURFACE = {
    "2t": ("TMP", "2 m above ground"),
    "2d": ("DPT", "2 m above ground"),
    "sp": ("PRES", "surface"),
    "10u": ("UGRD", "10 m above ground"),
    "10v": ("VGRD", "10 m above ground"),
    "10fg": ("GUST", "surface"),
    "tp": ("APCP", "surface"),
    "tprate": ("PRATE", "surface"),
    "tcwv": ("PWAT", hp.F_PWAT.level),
    "mucape": ("CAPE", hp.F_MUCAPE.level),
    "sd": ("sd", "surface"),
    "rsn": ("rsn", "surface"),
    "ptype": ("ptype", "surface"),
}
_PRESSURE = {"gh": "HGT", "t": "TMP", "u": "UGRD", "v": "VGRD"}
_PRODUCTS = frozenset({
    *(f"hgt-wind-{level}" for level in (200, 300, 500, 700, 850)),
    "tmp-2m", "dpt-2m", "thetae-2m", "rh-2m", "gust", "wind-10m",
    "apcp", "prate", "pwat", "prate-frzr", "prate-sleet", "prate-snow",
    "snod", "mucape", "lapse-700-500",
})
_PTYPE = hp.FieldSpec("ptype", hp.SOURCE_SURFACE, "ptype", "surface")
_SWE = hp.FieldSpec("swe", hp.SOURCE_SURFACE, "sd", "surface")
_DENSITY = hp.FieldSpec("snow_density", hp.SOURCE_SURFACE, "rsn", "surface")


@lru_cache(maxsize=2)
def grid(model):
    spacing = 0.1 if model == "ecmwf-ifs-9km" else 0.25
    return SimpleNamespace(spacing=spacing, shape=(round(180 / spacing) + 1, round(360 / spacing)))


def grib_url(run, hour, source, model):
    resolution = "0p1" if model == "ecmwf-ifs-9km" else "0p25"
    suffix = ("-pl" if source == hp.SOURCE_PRESSURE else "-sfc") if resolution == "0p1" else ""
    return (f"https://storage.googleapis.com/ecmwf-open-data/{run:%Y%m%d/%Hz}/ifs/"
            f"{resolution}/oper/{run:%Y%m%d%H%M%S}-{hour}h-oper-fc{suffix}.grib2")


def parse_inventory(text):
    """Translate ECMWF record addresses without changing the shared product names."""
    from sharpmod.providers.hrrr_field import HrrrFieldError, InventoryRow

    rows = []
    try:
        for index, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            item = json.loads(line)
            param, levtype = item["param"], item["levtype"]
            if levtype == "sfc" and param in _SURFACE:
                variable, level = _SURFACE[param]
            elif levtype == "pl" and param in _PRESSURE:
                variable, level = _PRESSURE[param], f"{int(item['levelist'])} mb"
            else:
                continue
            offset, length = int(item["_offset"]), int(item["_length"])
            if offset < 0 or length <= 0:
                raise ValueError("invalid record byte range")
            forecast = f"0-{int(item['step'])} hour acc fcst" if param == "tp" else ""
            rows.append(InventoryRow(index, offset, length, variable, level, forecast))
    except (KeyError, TypeError, ValueError) as error:
        raise HrrrFieldError("ECMWF inventory contains an invalid record") from error
    return tuple(rows)


def _rh(fields):
    from metpy.calc import relative_humidity_from_dewpoint
    from metpy.units import units

    temperature, dewpoint = fields["tmp2m"], fields["dpt2m"]
    valid = (temperature > 0) & (dewpoint > 0)
    result = 100 * relative_humidity_from_dewpoint(
        units.Quantity(np.where(valid, temperature, np.nan), "K"),
        units.Quantity(np.where(valid, dewpoint, np.nan), "K"),
    ).m_as("dimensionless")
    return np.clip(result, 0, 100)


def _snow_depth(fields):
    water, density = fields["swe"], fields["snow_density"]
    with np.errstate(divide="ignore", invalid="ignore"):
        depth = water * 1000 / density
    return hp.m_to_in(np.where(water == 0, 0, np.where((water > 0) & (density > 0), depth, np.nan)))


def _type_rate(codes):
    def derive(fields):
        rate, kind = fields["prate"], fields["ptype"]
        valid = np.isfinite(rate) & (rate >= 0) & np.isin(kind, range(15))
        return np.where(valid, np.where(np.isin(kind, codes), rate * 3600, 0), np.nan)
    return derive


@lru_cache(maxsize=1)
def products():
    result = []
    for product in hp.PRODUCTS.values():
        if product.key not in _PRODUCTS:
            continue
        if product.key == "rh-2m":
            product = replace(product, fields=(hp.F_TMP2M, hp.F_DPT2M), derive=_rh,
                              caveat="Calculated from ECMWF 2 m temperature and dewpoint.")
        elif product.key == "snod":
            product = replace(product, fields=(_SWE, _DENSITY), derive=_snow_depth,
                              caveat="Snow water equivalent converted to physical depth using ECMWF snow density.")
        elif product.key.startswith("prate-"):
            codes = {"prate-frzr": (3,), "prate-sleet": (8,), "prate-snow": (5, 6)}[product.key]
            product = replace(product, fields=(hp.F_PRATE, _PTYPE), derive=_type_rate(codes),
                              caveat="Total liquid-equivalent rate where ECMWF identifies this type; mixed rain/snow is excluded.")
        elif product.key == "mucape":
            product = replace(product, description="Most-unstable CAPE published by ECMWF.",
                              caveat="Uses ECMWF's native parcel definition.")
        elif product.key == "gust":
            product = replace(product, description="Maximum 10 m wind gust over ECMWF's reporting interval.",
                              caveat="An interval maximum, distinct from instantaneous 10 m wind.")
        result.append(product)
    return tuple(result)


def available_products(model, run, hour):
    """Offer a product only when this run and hour publish every ingredient."""
    from sharpmod.providers.hrrr_field import fetch_inventory, grib_url as field_url, HrrrFieldUnavailable

    inventories = {}
    for source in (hp.SOURCE_SURFACE, hp.SOURCE_PRESSURE):
        try:
            inventories[source] = fetch_inventory(field_url(run, hour, source, model))
        except HrrrFieldUnavailable:
            inventories[source] = ()
    return tuple(product for product in hp.available_products(model) if all(
        any(spec.matches(row.variable, row.level, row.forecast) for row in inventories[spec.source])
        for spec in product.all_fields
    ))
