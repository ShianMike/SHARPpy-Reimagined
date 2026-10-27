"""Requested model hazards: published addresses, units, totals, and rates."""

from datetime import datetime, timezone
import re
from dataclasses import replace

import numpy as np
import pytest

from sharpmod.providers import hrrr_field as hf, hrrr_products as hp


@pytest.mark.parametrize("key,variable,level,raw,expected,units", [
    ("apcp", "APCP", "surface", 25.4, 1, "in"),
    ("efhl", "EFHL", "surface", 250, 250, "m^2/s^2"),
    ("gust", "GUST", "surface", 10, 10 * hp.MS_TO_KT, "kt"),
    ("lftx", "LFTX", "500-1000 mb", -8, -8, "\u00b0C"),
    ("maxuvv", "MAXUVV", "100-1000 mb above ground", 20, 20, "m/s"),
    ("prate", "PRATE", "surface", 25.4 / 3600, 1, "in/h"),
    ("pwat", "PWAT", "entire atmosphere (considered as a single layer)", 25.4, 1, "in"),
    ("snod", "SNOD", "surface", 0.254, 10, "in"),
])
def test_native_hazard_fields_keep_the_published_quantity_and_missing_cells(
    key, variable, level, raw, expected, units,
):
    product = hp.PRODUCTS[key]
    spec, = product.fields
    assert (spec.variable, spec.level, spec.source) == (variable, level, "sfc")
    result = product.derive({spec.name: np.array([[raw, np.nan]], dtype=np.float32)})
    assert result[0, 0] == pytest.approx(expected)
    assert np.isnan(result[0, 1])
    assert product.palette.units == units
    assert product.palette.display == ("fixed" if key in {"maxuvv", "lftx"} else "auto")


def test_surface_lifted_index_matches_the_supplied_celsius_bands():
    product = hp.PRODUCTS["lftx"]
    palette = product.palette
    assert product.label == "Surface-Based Lifted Index"
    assert (palette.minimum, palette.maximum, palette.ceiling) == (-20, 0, 0)
    assert palette.stepped
    anchors = {-18: "#c29b9f", -14: "#b2445a", -10: "#da99e7", -8: "#8b0950",
               -6: "#e16f02", -4: "#a7c8ce", -2: "#696969", 0: "#ffffff"}
    assert {value: dict(palette.stops)[value] for value in anchors} == anchors
    values = np.array([*anchors, 0.1, np.nan], dtype=np.float32)
    rgba = hf.colourize(product.derive({"lftx": values}), palette)
    assert rgba[:, 3].tolist() == [255] * len(anchors) + [0, 0]
    assert ["#%02x%02x%02x" % tuple(colour[:3]) for colour in rgba[:len(anchors)]] == list(anchors.values())


@pytest.mark.parametrize("model,raw", [("hrrr", -8), ("rrfs-a", 265.15), ("nam-3km-conus", 265.15)])
def test_surface_lifted_index_normalizes_each_models_published_encoding(model, raw):
    product = next(product for product in hp.available_products(model) if product.key == "lftx")
    values = product.derive({"lftx": np.array([raw, np.nan], dtype=np.float32)})
    assert values[0] == pytest.approx(-8, abs=0.0001)
    assert np.isnan(values[1])
    assert hf.colourize(values, product.palette)[0, 3] == 255


def test_ten_metre_wind_uses_instantaneous_components_not_the_hourly_maximum():
    product = hp.PRODUCTS["wind-10m"]
    rows = hf.parse_inventory(
        "1:0:d=2026101600:WIND:10 m above ground:5-6 hour max fcst:\n"
        "2:100:d=2026101600:UGRD:10 m above ground:6 hour fcst:\n"
        "3:200:d=2026101600:VGRD:10 m above ground:6 hour fcst:\n"
    )
    assert [hf.locate(rows, spec).variable for spec in product.fields] == ["UGRD", "VGRD"]
    result = product.derive({"u10": np.array([3, np.nan]), "v10": np.array([4, 4])})
    assert result[0] == pytest.approx(5 * hp.MS_TO_KT)
    assert np.isnan(result[1])


@pytest.mark.parametrize("key", ["wind-10m", "gust"])
def test_surface_wind_and_gusts_match_the_supplied_knot_table(key):
    palette = hp.PRODUCTS[key].palette
    assert (palette.minimum, palette.maximum, palette.units) == (9, 61, "kt")
    assert palette.stepped and palette.floor == 10
    assert palette.tick_values() == (9, *range(10, 61, 5), 61)
    swatches = {
        10: "#f0f8ff", 14.99: "#8bd0fa", 15: "#81b7f1", 20: "#9c72d3",
        25: "#d472ca", 30: "#a8229e", 35: "#c80028", 40: "#e03040",
        45: "#f49058", 50: "#e8d252", 55: "#c4962e", 60: "#a05a0a",
        80: "#a05a0a",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(hf.colourize(np.array(list(swatches)), palette), expected)
    assert hf.colourize(np.array([-1, 0, 9.99, 10, np.nan]), palette)[:, 3].tolist() == [0, 0, 0, 255, 0]
    assert hp.PRODUCTS["gust"].palette is hp.PALETTE_SURFACE_WIND


def test_snow_depth_matches_the_supplied_inch_table():
    palette = hp.PRODUCTS["snod"].palette
    assert (palette.minimum, palette.maximum, palette.units) == (0, 64, "in")
    assert palette.stepped and palette.floor == 0.1
    assert palette.tick_values() == (
        0, 0.1, 0.5, 1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 28,
        32, 36, 44, 52, 60, 64,
    )
    swatches = {
        0.1: "#e1e1e1", 0.2499: "#e1e1e1", 0.25: "#c8c8c8",
        0.4999: "#c8c8c8", 0.5: "#afafaf", 0.75: "#969696",
        1: "#a9e7f2", 2: "#3b98bb", 3: "#0046b0", 4: "#4d82c8",
        5: "#9abedf", 6: "#c9addb", 8: "#b97ec9", 10: "#a84fb8",
        12: "#870a47", 16: "#a72d6e", 20: "#c65095", 24: "#eba7b7",
        28: "#e68f8a", 32: "#e1765d", 36: "#dc8150", 44: "#e6a97f",
        52: "#f1d1af", 60: "#fbf9de", 100: "#fbf9de",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(hf.colourize(np.array(list(swatches)), palette), expected)
    assert hf.colourize(np.array([-1, 0, 0.0999, 0.1, np.nan]), palette)[:, 3].tolist() == [0, 0, 0, 255, 0]


def test_precipitable_water_matches_the_supplied_inch_table():
    palette = hp.PRODUCTS["pwat"].palette
    assert (palette.minimum, palette.maximum, palette.units) == (0.05, 3.05, "in")
    assert palette.stepped and palette.floor == 0.05
    assert palette.tick_values() == (0.05, *(step / 4 for step in range(1, 13)), 3.05)
    swatches = {
        0.05: "#433920", 0.0999: "#433920", 0.1: "#534a31", 0.25: "#837c63",
        0.4999: "#c2bea6", 0.5: "#b9e5b9", 0.75: "#579157", 0.9999: "#084e08",
        1: "#61a3af", 1.25: "#366166", 1.4999: "#132c2b", 1.5: "#66669a",
        1.75: "#463e7c", 1.9999: "#2d1e64", 2: "#724172", 2.25: "#8e5c7a",
        2.4999: "#a47281", 2.5: "#c9978c", 2.75: "#e0c4b1", 3: "#f6f0d7",
        4: "#f6f0d7",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(hf.colourize(np.array(list(swatches)), palette), expected)
    assert hf.colourize(np.array([-1, 0, 0.0499, 0.05, np.nan]), palette)[:, 3].tolist() == [0, 0, 0, 255, 0]


@pytest.mark.parametrize("key,units", [("apcp", "in"), ("prate", "in/h")])
def test_precipitation_totals_and_rates_match_the_supplied_inch_table(key, units):
    palette = hp.PRODUCTS[key].palette
    assert (palette.minimum, palette.maximum, palette.units) == (0.01, 17.5, units)
    assert palette.stepped and palette.floor == 0.01
    swatches = {
        0.01: "#dcdcdc", 0.0299: "#dcdcdc", 0.03: "#bebebe",
        0.05: "#a0a0a0", 0.075: "#828282", 0.1: "#b7f0be",
        0.2: "#87c7a7", 0.3: "#579d90", 0.4: "#277479", 0.5: "#1450b4",
        0.6: "#3f73c2", 0.7: "#6b96d0", 0.8: "#96b9dd", 0.9: "#c1dceb",
        1: "#cebce0", 1.2: "#c49ed5", 1.4: "#ba81ca", 1.6: "#af63bf",
        1.8: "#a546b4", 2: "#a53a34", 2.5: "#b5554f", 3: "#c6716b",
        3.5: "#d68c86", 4: "#f8eea2", 5: "#e5bd76", 6: "#d28c4a",
        8: "#ac632d", 10: "#814a21", 12.5: "#6b3e1b", 15: "#553115",
        100: "#553115",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(hf.colourize(np.array(list(swatches)), palette), expected)
    assert hf.colourize(np.array([-1, 0, 0.0099, 0.01, np.nan]), palette)[:, 3].tolist() == [
        0, 0, 0, 255, 0]


@pytest.mark.parametrize("key", ["apcp"])
@pytest.mark.parametrize("total", ["0-0 day acc fcst", "0-1 hour acc fcst", "0-6 hour acc fcst", "0-2 day acc fcst"])
def test_run_totals_never_select_an_hourly_accumulation_even_when_it_comes_first(key, total):
    spec, = hp.PRODUCTS[key].fields
    rows = hf.parse_inventory("\n".join(
        f"{i}:{i * 100}:d=2026101600:{spec.variable}:surface:{interval}:"
        for i, interval in enumerate(("5-6 hour acc fcst", "10-11 hour acc fcst", total), 1)
    ))
    assert hf.locate(rows, spec).forecast == total


@pytest.mark.parametrize("key,mask,variable", [
    ("prate-frzr", "cfrzr", "CFRZR"), ("prate-sleet", "cicep", "CICEP"),
    ("prate-snow", "csnow", "CSNOW"),
])
def test_winter_rates_use_instantaneous_prate_and_type_flags_with_missing_cells(key, mask, variable):
    product = hp.PRODUCTS[key]
    assert product.fields == (hp.F_PRATE, hp.FieldSpec(mask, "sfc", variable, "surface"))
    rate = np.array([0, 2, 4, np.nan, 6, np.inf, -1, 7, 8, 9, 10]) / 3600
    flag = np.array([1, 1, 0, 1, np.nan, 1, 1, 2, 255, -1, 0.5])
    values = product.derive({"prate": rate, mask: flag})
    np.testing.assert_allclose(values, [0, 2, 0, *([np.nan] * 8)], equal_nan=True)
    assert product.palette.units == "mm/h"


@pytest.mark.parametrize("key,colours", [
    ("prate-frzr", ["#ffa6af", "#df5893", "#cc2d0d", "#ff4504"]),
    ("prate-sleet", ["#ada8ff", "#c24df7", "#c02782", "#dc445b"]),
    ("prate-snow", ["#00ffff", "#009ffd", "#0e0ba3", "#ff0080"]),
])
def test_winter_rate_palettes_match_the_reference_and_hide_dry_cells(key, colours):
    palette = hp.PRODUCTS[key].palette
    assert (palette.minimum, palette.maximum, palette.floor, palette.units) == (0.1, 16, 0.1, "mm/h")
    assert palette.stepped
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255] for colour in colours]
    np.testing.assert_array_equal(hf.colourize(np.array([0.1, 1, 6, 14]), palette), expected)
    assert hf.colourize(np.array([0, 0.0999, np.nan]), palette)[:, 3].tolist() == [0, 0, 0]
    np.testing.assert_array_equal(hf.colourize(np.array([100]), palette), [expected[-1]])


def test_instantaneous_and_hourly_maximum_addressing_still_stay_separate():
    assert hp.F_MAXUVV.matches("MAXUVV", hp.F_MAXUVV.level, "47-48 hour max fcst")
    assert hp.F_MAXUVV.matches("MAXUVV", hp.F_MAXUVV.level, "0-0 day max fcst")
    assert not hp.F_MAXUVV.matches("MAXUVV", hp.F_MAXUVV.level, "48 hour fcst")
    assert hp.F_PRATE.matches("PRATE", "surface", "anl")
    assert not hp.F_PRATE.matches("PRATE", "surface", "5-6 hour ave fcst")
    assert not hp.F_APCP.matches("APCP", "surface", "6 hour fcst")


def test_removed_products_are_absent_from_every_model_catalogue():
    removed = {"snowc", "cfrzr", "csnow", "asnow", "frzr", "4lftx"}
    assert removed.isdisjoint(hp.PRODUCTS)
    for model in hp.FIELD_MODELS:
        assert removed.isdisjoint(product.key for product in hp.available_products(model))


@pytest.mark.parametrize("model", hp.FIELD_MODELS)
def test_hazard_products_follow_each_models_published_inventory(model):
    additions = {"apcp", "efhl",
                 "gust", "lftx", "maxuvv", "prate", "pwat", "snod", "wind-10m",
                 "prate-frzr", "prate-sleet", "prate-snow"}
    expected = (additions - {"efhl", "lftx", "maxuvv"} if model in hp.ECMWF_MODELS or model == "gfs"
                else {"apcp", "wind-10m"} if model == "aigfs"
                else {"apcp", "gust", "lftx", "maxuvv", "pwat", "wind-10m"} if model in hp.HRW_MODELS
                else {"apcp", "gust", "maxuvv", "pwat", "wind-10m"} if model.startswith("refs-")
                else additions - {"efhl", "maxuvv"} if model == "nam"
                else additions - {"efhl"} if model == "nam-3km-conus" else additions)
    products = {product.key: product for product in hp.available_products(model)}
    assert additions & products.keys() == expected
    for key in expected:
        product = products[key]
        assert product.palette is hp.PRODUCTS[key].palette
        assert product.category in hp.CATEGORIES
    if "maxuvv" in products:
        maximum, = products["maxuvv"].fields
        assert maximum.level == ("100-1000 mb above ground" if model == "hrrr" else "100-1000 mb")
        assert maximum.forecast == "max fcst"


@pytest.mark.parametrize("hour", [0, 1, 2, 3, 4, 6, 7, 12, 60])
@pytest.mark.parametrize("model", ["nam", "nam-3km-conus"])
def test_nam_total_precipitation_sums_disjoint_intervals_and_keeps_missing(hour, model, monkeypatch):
    product = next(p for p in hp.available_products(model) if p.key == "apcp")
    monkeypatch.setattr(hf, "field_grid", lambda _model: replace(hf.HRRR_GRID, shape=(1, 2)))
    monkeypatch.setattr(hf, "_INVENTORY_CACHE", {})
    monkeypatch.setattr(hf, "_load_eccodes", lambda: None)
    monkeypatch.setattr(hf, "decode_message", lambda data, *_args, **_kwargs: np.array([[float(data.strip()), np.nan]]))
    downloaded = []

    def opener(url, _timeout, _limit, byte_range=None):
        end = int(re.search(r"(?:hiresf|awip12)(\d+)", url)[1])
        start = (end - 1) // 3 * 3
        starts = list(dict.fromkeys((0, end - 1, start)))
        if byte_range is None:
            return "\n".join(
                f"{i + 1}:{i * 16}:d=2026101600:APCP:surface:{begin}-{end} hour acc fcst:"
                for i, begin in enumerate(starts)
            ).encode()
        downloaded.append(end)
        data = b"".join(str(end - start if begin == start else 1000).encode().ljust(16)
                        for begin in starts)
        return data[byte_range[0]:byte_range[1] + 1]

    decoded = hf._download_fields(product, datetime(2026, 10, 16, tzinfo=timezone.utc), hour,
                                  opener=opener, timeout=1, should_cancel=None, model=model)
    assert decoded["apcp"][0, 0] == hour
    assert product.derive(decoded)[0, 0] == pytest.approx(hour / 25.4)
    assert sorted(downloaded) == ([*range(3, hour, 3), hour] if hour else [])
    assert np.isnan(decoded["apcp"][0, 1]) if hour else decoded["apcp"][0, 1] == 0


def test_nam_total_precipitation_rejects_an_incomplete_run(monkeypatch):
    product = next(p for p in hp.available_products("nam-3km-conus") if p.key == "apcp")
    monkeypatch.setattr(hf, "_load_eccodes", lambda: None)
    monkeypatch.setattr(hf, "fetch_inventory", lambda *_args, **_kwargs: hf.parse_inventory(
        "1:0:d=2026101600:APCP:surface:5-6 hour acc fcst:"))
    with pytest.raises(hf.HrrrFieldUnavailable):
        hf._download_fields(product, datetime(2026, 10, 16, tzinfo=timezone.utc), 6,
                            opener=None, timeout=1, should_cancel=None, model="nam-3km-conus")


@pytest.mark.parametrize("missing", [None, b""])
def test_nam_total_precipitation_never_returns_a_partial_total(missing, monkeypatch):
    product = next(p for p in hp.available_products("nam-3km-conus") if p.key == "apcp")
    monkeypatch.setattr(hf, "_load_eccodes", lambda: None)
    monkeypatch.setattr(hf, "fetch_inventory", lambda url, **_kwargs: hf.parse_inventory(
        "1:0:d=2026101600:APCP:surface:" + ("0-3" if "hiresf03" in url else "3-6") + " hour acc fcst:"))
    monkeypatch.setattr(hf, "decode_message", lambda *_args, **_kwargs: np.ones((1, 2)))
    with pytest.raises(hf.HrrrFieldUnavailable, match="GRIB byte range"):
        hf._download_fields(product, datetime(2026, 10, 16, tzinfo=timezone.utc), 6,
                            opener=lambda url, *_args: b"GRIB" if "hiresf03" in url else missing,
                            timeout=1, should_cancel=None, model="nam-3km-conus")


@pytest.mark.parametrize("model", hp.FIELD_MODELS)
def test_initial_run_precipitation_is_zero_without_downloading(model, monkeypatch):
    product = next(p for p in hp.available_products(model) if p.key == "apcp")
    monkeypatch.setattr(hf, "field_grid", lambda _model: replace(hf.HRRR_GRID, shape=(1, 2)))
    run = datetime(2026, 10, 16, tzinfo=timezone.utc)
    np.testing.assert_array_equal(hf._download_fields(product, run, 0, opener=None, timeout=1,
                                                    should_cancel=None, model=model)["apcp"], [[0, 0]])
    assert hf._download_fields(product, run, 0, opener=None, timeout=1,
                               should_cancel=lambda: True, model=model) == {}
