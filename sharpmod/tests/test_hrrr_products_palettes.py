"""Colour-scale invariants for the HRRR map products.

A scale is wrong in two ways that both look like a rendering fault. If its range
does not cover the field, the extremes clip and read as a plateau. If its floor
sits above where the field lives, the map is blank and the reader concludes the
model has nothing -- which is a different claim entirely.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from sharpmod import hrrr_field, hrrr_products
from sharpmod.hrrr_products import Palette

ALL_PRODUCTS = hrrr_products.available_products()
PRODUCT_IDS = [product.key for product in ALL_PRODUCTS]


# --------------------------------------------------------------------------- #
# The contract every scale has to satisfy
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("product", ALL_PRODUCTS, ids=PRODUCT_IDS)
def test_every_palette_is_well_formed(product):
    palette = product.palette
    values = [value for value, _colour in palette.stops]

    assert len(palette.stops) >= 2
    assert values == sorted(values)
    assert len(set(values)) == len(values), "duplicate class edge"
    for _value, colour in palette.stops:
        assert len(colour) == 7 and colour.startswith("#")
        int(colour[1:], 16)


@pytest.mark.parametrize("product", ALL_PRODUCTS, ids=PRODUCT_IDS)
def test_every_tick_sits_on_the_scale(product):
    """A tick outside the bar would be drawn off the end of it."""
    palette = product.palette

    for tick in palette.tick_values():
        assert palette.minimum <= tick <= palette.maximum, tick


@pytest.mark.parametrize("product", ALL_PRODUCTS, ids=PRODUCT_IDS)
def test_the_ticks_name_both_ends_of_the_scale(product):
    """The colour bar's end labels are how a reader learns its range.

    Temperature ended its ticks at 110 F on a bar that runs to 120, so the last
    number sat short of the end and there was nothing to say where the scale
    stopped.
    """
    palette = product.palette
    ticks = palette.tick_values()

    assert ticks[0] == palette.minimum, product.key
    assert ticks[-1] == palette.maximum, product.key


@pytest.mark.parametrize("product", ALL_PRODUCTS, ids=PRODUCT_IDS)
def test_a_floor_is_never_above_the_scale(product):
    palette = product.palette

    if palette.floor is not None:
        assert palette.minimum <= palette.floor < palette.maximum
    if palette.ceiling is not None:
        assert palette.minimum < palette.ceiling <= palette.maximum


@pytest.mark.parametrize("product", ALL_PRODUCTS, ids=PRODUCT_IDS)
def test_every_class_gets_a_distinct_colour(product):
    """Two adjacent classes that share a colour are one class pretending."""
    colours = [colour for _value, colour in product.palette.stops]
    if product.palette.stepped:
        colours = colours[:-1]  # The last stop closes the axis, not another band.

    for earlier, later in zip(colours, colours[1:]):
        assert earlier != later, product.key


def test_a_ceiling_below_the_floor_is_refused():
    with pytest.raises(ValueError, match="ceiling"):
        Palette(stops=((0.0, "#000000"), (1.0, "#ffffff")), units="",
                floor=0.8, ceiling=0.2)


# --------------------------------------------------------------------------- #
# Suppression at both ends
# --------------------------------------------------------------------------- #
def _alpha(values, palette):
    array = np.asarray(values, dtype=np.float64)
    return hrrr_field.colourize(array, palette)[..., 3]


def test_a_floor_hides_the_low_end():
    palette = Palette(
        stops=((10.0, "#101010"), (20.0, "#202020"), (30.0, "#303030")),
        units="", stepped=True, floor=10.0)

    assert list(_alpha([5.0, 9.9, 10.0, 25.0], palette)) == [0, 0, 255, 255]


def test_a_ceiling_hides_the_high_end():
    """Convective inhibition needs this: its weak values are its large ones."""
    palette = Palette(
        stops=((-100.0, "#101010"), (-50.0, "#202020"), (-25.0, "#303030")),
        units="", stepped=True, ceiling=-25.0)

    assert list(_alpha([-200.0, -100.0, -25.0, -24.0, 0.0], palette)) == \
        [0, 255, 255, 0, 0]


def test_non_finite_values_are_always_transparent():
    palette = hrrr_products.get_product("mlcape").palette

    assert list(_alpha([np.nan, np.inf, -np.inf], palette)) == [0, 0, 0]


def test_temperature_matches_the_supplied_fahrenheit_table():
    palette = hrrr_products.get_product("tmp-2m").palette
    assert (palette.minimum, palette.maximum, palette.units) == (-61, 121, "\u00b0F")
    assert palette.stepped
    swatches = {
        -61: "#1f497e", -60: "#326681", -40: "#b3ebe0",
        -20: "#9380c2", -1: "#7a25ab", 0: "#9f41ad",
        14: "#d7e6ee", 30: "#1953b4", 31: "#1953b4", 32: "#0f4354",
        40: "#7a967c", 50: "#f8eea2", 60: "#aa714d", 70: "#5f0000",
        80: "#73372d", 90: "#b8968c", 99: "#e6e5e5", 100: "#dfdddb",
        110: "#959391", 120: "#474747",
    }
    values = np.asarray([value + 0.5 for value in swatches])
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(hrrr_field.colourize(values, palette), expected)


def test_dewpoint_matches_the_supplied_fahrenheit_table():
    palette = hrrr_products.get_product("dpt-2m").palette
    assert (palette.minimum, palette.maximum, palette.units) == (-41, 90, "\u00b0F")
    assert palette.stepped
    swatches = {
        -41: "#986d4d", -40: "#966c4c", -20: "#6f5641", 0: "#4d4334",
        20: "#a39d88", 39: "#f4f2d7", 40: "#e6f5e6", 50: "#30ae30",
        59: "#084e08", 60: "#61a3af", 65: "#366166", 69: "#132c2b",
        70: "#66669a", 75: "#463e7c", 79: "#2d1e64", 80: "#724071",
        85: "#8e5b7a", 89: "#a47181",
    }
    values = np.asarray([value + 0.5 for value in swatches])
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(hrrr_field.colourize(values, palette), expected)


def test_relative_humidity_uses_the_native_surface_percentage_field_and_table():
    product = hrrr_products.get_product("rh-2m")
    assert product.category == "Surface"
    assert product.short_label == "2 m RH"
    assert len(product.all_fields) == 1
    spec = product.fields[0]
    assert (spec.source, spec.variable, spec.level) == (
        "sfc", "RH", "2 m above ground")
    assert spec.matches("RH", "2 m above ground", "anl")
    assert spec.matches("RH", "2 m above ground", "1 hour fcst")
    assert not spec.matches("RH", "surface", "anl")
    values = np.asarray([0.0, 9.5, 40.5, 67.5, 100.0, np.nan], dtype=np.float32)
    np.testing.assert_array_equal(product.derive({"rh2m": values}), values)
    palette = product.palette
    assert (palette.minimum, palette.maximum, palette.units) == (0, 100, "%")
    assert palette.stepped
    assert palette.tick_values() == tuple(range(0, 101, 10))
    swatches = {
        0.0: "#9a6c49", 9.5: "#382f28", 20.5: "#746b5f",
        39.5: "#ddd1c3", 40.5: "#c8d7c0", 60.5: "#769d85",
        80.5: "#25644a", 90.5: "#004123", 99.5: "#245682", 100.0: "#28588c",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(
        hrrr_field.colourize(np.asarray(list(swatches)), palette), expected)
    assert list(_alpha([0, 100, np.nan], palette)) == [255, 255, 0]


def test_lightning_uses_native_hourly_maxima_and_the_supplied_density_table():
    product = hrrr_products.get_product("ltng")
    assert product.category == "Surface"
    assert len(product.all_fields) == 1
    spec = product.fields[0]
    assert (spec.source, spec.variable, spec.level) == (
        "sfc", "LTNG", "entire atmosphere")
    assert spec.matches("LTNG", "entire atmosphere", "anl")
    assert spec.matches("LTNG", "entire atmosphere", "1 hour fcst")
    assert not spec.matches("LTNGSD", "1 m above ground", "1 hour fcst")
    values = np.asarray([0.0, 0.015, 0.3, 1.0, 15.0, 19.28, np.nan], dtype=np.float32)
    np.testing.assert_array_equal(product.derive({"ltng": values}), values)
    palette = product.palette
    assert (palette.minimum, palette.maximum, palette.units) == (0, 15, "flashes")
    assert palette.stepped
    assert palette.tick_values() == (0, 0.1, 0.3, 1, 3, 6, 15)
    swatches = {
        0.015: "#f0f0f0", 0.055: "#a5a5a5", 0.105: "#37536a",
        0.32: "#e9dd96", 0.95: "#e16f02", 1.05: "#dc4110",
        3.05: "#73088a", 6.05: "#e9bec3", 14.5: "#b2445a",
        15.0: "#893d48", 19.28: "#893d48",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(
        hrrr_field.colourize(np.asarray(list(swatches)), palette), expected)
    assert list(_alpha([0, 0.0099, 0.01, 15, 19.28, np.nan], palette)) == [
        0, 0, 255, 255, 255, 0]


@pytest.mark.parametrize("key", ("mlcape", "mucape", "sbcape"))
def test_cape_matches_the_supplied_energy_table(key):
    palette = hrrr_products.get_product(key).palette
    assert (palette.minimum, palette.maximum, palette.units) == (0, 10500, "J/kg")
    assert palette.stepped
    assert palette.floor == 100
    swatches = {
        100: "#f0f0f0", 500: "#a5a5a5", 1000: "#37536a",
        1500: "#7594a2", 2000: "#e9dd96", 2500: "#e5a044",
        3000: "#dc4110", 3500: "#af2234", 4000: "#73088a",
        4500: "#ac59be", 5000: "#e9bec3", 5500: "#ca7a89",
        6000: "#893d48", 8500: "#a97178", 10000: "#bc9195",
    }
    values = np.asarray([value + 50 for value in swatches])
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(hrrr_field.colourize(values, palette), expected)
    assert list(_alpha([0, 99.9, 100, np.nan], palette)) == [0, 0, 255, 0]


def test_cin_matches_the_supplied_energy_table():
    palette = hrrr_products.get_product("mlcin").palette
    assert (palette.minimum, palette.maximum, palette.units) == (-1050, 0, "J/kg")
    assert palette.stepped
    swatches = {
        -1025: "#bc9195", -825: "#a2676f", -590: "#b2445a",
        -490: "#d18894", -390: "#da99e7", -290: "#a148b3",
        -195: "#8b0950", -145: "#b8282c", -97.5: "#e16f02",
        -72.5: "#e5ac54", -48.75: "#a7c8ce", -36.25: "#698796",
        -26.25: "#37536a", -23.75: "#696969", -11.25: "#c3c3c3",
        -3.75: "#f0f0f0",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(
        hrrr_field.colourize(np.asarray(list(swatches)), palette), expected)


@pytest.mark.parametrize("key", ("lapse-0-3km", "lapse-700-500"))
def test_lapse_rate_matches_the_supplied_table(key):
    palette = hrrr_products.get_product(key).palette
    assert (palette.minimum, palette.maximum, palette.units) == (0, 11, "\u00b0C/km")
    assert palette.stepped
    swatches = {
        0.3: "#ffffff", 3.3: "#a5a5a5", 5.7: "#696969",
        6.05: "#37536a", 6.55: "#7594a2", 6.95: "#a7c8ce",
        7.05: "#e9dd96", 7.55: "#e5a044", 7.95: "#e16f02",
        8.05: "#dc4110", 8.55: "#af2234", 8.95: "#8b0950",
        9.05: "#73088a", 9.55: "#ac59be", 9.95: "#da99e7",
        10.5: "#da99e7",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(
        hrrr_field.colourize(np.asarray(list(swatches)), palette), expected)
    assert list(_alpha([-1, 0, 3, np.nan], palette)) == [0, 255, 255, 0]


@pytest.mark.parametrize(
    ("key", "minimum", "maximum", "floor"),
    (("shear-0-1km", 5, 71, 5), ("shear-0-6km", 9, 100, 10)),
)
def test_bulk_shear_matches_the_supplied_knot_table(key, minimum, maximum, floor):
    palette = hrrr_products.get_product(key).palette
    assert (palette.minimum, palette.maximum, palette.units) == (minimum, maximum, "kt")
    assert palette.stepped
    assert palette.floor == floor
    swatches = {
        10.5: "#f0f8ff", 14.5: "#9cd6fb", 15.5: "#87cefa",
        20.5: "#6a5acd", 25.5: "#e696dc", 30.5: "#c85abe",
        35.5: "#a01496", 40.5: "#c80028", 45.5: "#dc283c",
        50.5: "#f05050", 55.5: "#faf064", 60.5: "#dcbe46",
        65.5: "#be8c28", 70.5: "#a05a0a", 94: "#a05a0a",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(
        hrrr_field.colourize(np.asarray(list(swatches)), palette), expected)
    assert list(_alpha([floor - 0.1, floor, np.nan], palette)) == [0, 255, 0]


@pytest.mark.parametrize(
    ("key", "floor"), (("srh-0-1km", 25), ("srh-0-3km", 50)),
)
def test_storm_relative_helicity_matches_the_supplied_table(key, floor):
    palette = hrrr_products.get_product(key).palette
    assert (palette.minimum, palette.maximum, palette.units) == (0, 1050, "m^2/s^2")
    assert palette.stepped
    assert palette.floor == floor
    assert 850 in palette.tick_values()
    swatches = {
        55: "#a5a5a5", 95: "#696969", 105: "#37536a",
        155: "#7594a2", 195: "#a7c8ce", 205: "#e9dd96",
        255: "#e5a044", 295: "#e16f02", 305: "#dc4110",
        355: "#af2234", 395: "#8b0950", 405: "#73088a",
        455: "#ac59be", 495: "#da99e7", 505: "#e9bec3",
        555: "#ca7a89", 595: "#b2445a", 625: "#893d48",
        875: "#a97178", 1025: "#bc9195", 1100: "#bc9195",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(
        hrrr_field.colourize(np.asarray(list(swatches)), palette), expected)
    assert list(_alpha([-1, 0, floor - 0.1, floor, np.nan], palette)) == [0, 0, 0, 255, 0]


def test_supercell_composite_matches_the_supplied_table():
    palette = hrrr_products.get_product("scp").palette
    assert (palette.minimum, palette.maximum, palette.units) == (0, 52, "")
    assert palette.stepped
    assert palette.floor == 1
    swatches = {
        1.25: "#e1e1e1", 4.75: "#696969", 5.25: "#37536a",
        7.75: "#7594a2", 9.75: "#a7c8ce", 10.5: "#e9dd96",
        15.5: "#e5a044", 19.5: "#e16f02", 20.5: "#dc4110",
        25.5: "#af2234", 29.5: "#8b0950", 31: "#73088a",
        41: "#ac59be", 47: "#cf89dd", 49: "#da99e7",
        50.4: "#da99e7", 64: "#da99e7",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(
        hrrr_field.colourize(np.asarray(list(swatches)), palette), expected)
    assert list(_alpha([-1, 0, 0.99, 1, np.nan], palette)) == [0, 0, 0, 255, 0]


def test_significant_tornado_parameter_matches_the_supplied_table():
    palette = hrrr_products.get_product("stp").palette
    assert (palette.minimum, palette.maximum, palette.units) == (0, 10.5, "")
    assert palette.stepped
    assert palette.floor == 0.5
    assert 8.5 in palette.tick_values()
    swatches = {
        0.55: "#a5a5a5", 0.95: "#696969", 1.05: "#37536a",
        1.55: "#7594a2", 1.95: "#a7c8ce", 2.05: "#e9dd96",
        2.55: "#e5a044", 2.95: "#e16f02", 3.05: "#dc4110",
        3.55: "#af2234", 3.95: "#8b0950", 4.05: "#73088a",
        4.55: "#ac59be", 4.95: "#da99e7", 5.05: "#e9bec3",
        5.55: "#ca7a89", 5.95: "#b2445a", 6.25: "#893d48",
        8.75: "#a97178", 10.25: "#bc9195", 12: "#bc9195",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(
        hrrr_field.colourize(np.asarray(list(swatches)), palette), expected)
    assert list(_alpha([-1, 0, 0.499, 0.5, np.nan], palette)) == [0, 0, 0, 255, 0]


def test_updraft_helicity_matches_the_supplied_table():
    palette = hrrr_products.get_product("uh-0-3km").palette
    assert (palette.minimum, palette.maximum, palette.units) == (0, 200, "m^2/s^2")
    assert palette.stepped
    assert palette.floor == 5
    assert palette.tick_values() == (0, *range(5, 101, 5), *range(110, 201, 10))
    swatches = {
        6.25: "#e1e1e1", 13.75: "#a5a5a5", 23.75: "#696969",
        26.25: "#37536a", 38.75: "#7594a2", 48.75: "#a7c8ce",
        51.25: "#e9dd96", 63.75: "#e5a044", 73.75: "#e16f02",
        76.25: "#dc4110", 88.75: "#af2234", 98.75: "#8b0950",
        102.5: "#73088a", 127.5: "#ac59be", 147.5: "#da99e7",
        152.5: "#e9bec3", 177.5: "#ca7a89", 197.5: "#b2445a",
        250: "#b2445a",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(
        hrrr_field.colourize(np.asarray(list(swatches)), palette), expected)
    assert list(_alpha([-1, 0, 4.99, 5, np.nan], palette)) == [0, 0, 0, 255, 0]


def test_height_interpolation_does_not_depend_on_a_nan_missing_sentinel(
        monkeypatch):
    """A finite interchange sentinel must not make every cell look filled."""
    monkeypatch.setattr(hrrr_products, "MISSING", -9999.0)
    heights = np.asarray([
        [[0.0, 0.0]],
        [[1000.0, 1000.0]],
        [[2000.0, 2000.0]],
    ])
    values = np.asarray([
        [[20.0, 20.0]],
        [[10.0, 10.0]],
        [[0.0, 0.0]],
    ])

    actual = hrrr_products._interpolate_to_height(
        heights, values, np.asarray([[500.0, 1500.0]])
    )

    np.testing.assert_allclose(actual, [[15.0, 5.0]])


def test_supercell_composite_shear_term_saturates_at_twenty_metres_per_second():
    product = hrrr_products.get_product("scp")
    fields = {
        "mucape": np.asarray([1000.0, 1000.0]),
        "srh03": np.asarray([50.0, 50.0]),
        "ushr06": np.asarray([20.0, 30.0]),
        "vshr06": np.asarray([0.0, 0.0]),
    }

    np.testing.assert_allclose(product.derive(fields), [1.0, 1.0])


# --------------------------------------------------------------------------- #
# Convective inhibition, the field the ceiling exists for
# --------------------------------------------------------------------------- #
def test_uncapped_ground_is_not_painted_as_capped():
    """Zero and negligible inhibition leave the basemap visible."""
    palette = hrrr_products.get_product("mlcin").palette

    drawn = _alpha([0.0, -1.0, -2.49, np.nan], palette)

    assert not drawn.any()


def test_a_real_cap_is_drawn_and_graded():
    palette = hrrr_products.get_product("mlcin").palette
    values = [-2.5, -12.5, -25.0, -100.0, -300.0, -600.0, -850.0, -1050.0]

    assert _alpha(values, palette).all()
    rgba = hrrr_field.colourize(np.asarray(values), palette)
    assert len({tuple(row[:3]) for row in rgba}) == len(values), \
        "a deep cap must not read the same as a shallow one"


def test_the_cin_scale_reaches_an_observed_extreme():
    """A capped summer morning reaches -600 J/kg; -583 was measured."""
    palette = hrrr_products.get_product("mlcin").palette

    assert palette.minimum <= -800.0
    assert palette.ceiling == -2.5


# --------------------------------------------------------------------------- #
# Scales that must differ because the fields do
# --------------------------------------------------------------------------- #
def test_shallow_and_deep_shear_do_not_share_a_scale():
    """0-1 km shear is roughly a third of 0-6 km; one scale hid 98% of it."""
    shallow = hrrr_products.get_product("shear-0-1km").palette
    deep = hrrr_products.get_product("shear-0-6km").palette

    assert shallow.floor < deep.floor
    assert shallow.maximum < deep.maximum
    # A summer median of about 5 kt has to be on scale, not under the floor.
    assert shallow.floor <= 5.0


def test_shallow_helicity_keeps_its_lower_visibility_floor():
    shallow = hrrr_products.get_product("srh-0-1km").palette
    deep = hrrr_products.get_product("srh-0-3km").palette

    assert shallow.floor < deep.floor
    assert list(_alpha([25.0], shallow)) == [255]
    assert list(_alpha([25.0], deep)) == [0]


@pytest.mark.parametrize("level", (850, 700, 500, 300, 200))
def test_height_and_wind_matches_the_supplied_knot_table(level):
    palette = hrrr_products.get_product(f"hgt-wind-{level}").palette
    assert (palette.minimum, palette.maximum, palette.units) == (49, 171, "kt")
    assert palette.stepped
    assert palette.floor == 50
    assert palette.tick_values() == (49, *range(50, 171, 5), 171)
    swatches = {
        50.5: "#f0f8ff", 51.5: "#e6f4ff", 55.5: "#bce3fd",
        60.5: "#87cefa", 65.5: "#7994e4", 70.5: "#6a5acd",
        75.5: "#a878d5", 80.5: "#e696dc", 85.5: "#d778cd",
        90.5: "#c85abe", 95.5: "#b437aa", 100.5: "#a01496",
        105.5: "#b40a5f", 110.5: "#c80028", 115.5: "#d21432",
        120.5: "#dc283c", 125.5: "#e63c46", 130.5: "#f05050",
        135.5: "#f5a05a", 140.5: "#faf064", 145.5: "#ebd755",
        150.5: "#dcbe46", 155.5: "#cda537", 160.5: "#be8c28",
        165.5: "#af7319", 170.5: "#a05a0a", 200: "#a05a0a",
    }
    expected = [[int(colour[i:i + 2], 16) for i in (1, 3, 5)] + [255]
                for colour in swatches.values()]
    np.testing.assert_array_equal(
        hrrr_field.colourize(np.asarray(list(swatches)), palette), expected)
    assert list(_alpha([-1, 49.99, 50, np.nan], palette)) == [0, 0, 255, 0]


# --------------------------------------------------------------------------- #
# Ranges measured against a real run
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("key", "observed_maximum"),
    (
        # Values measured from HRRR 2026-09-04 06Z F015 over CONUS.
        ("dpt-2m", 81.6),
        ("tmp-2m", 107.5),
        ("lapse-0-3km", 10.5),
        ("shear-0-6km", 94.0),
        ("scp", 50.4),
        ("mucape", 5280.0),
    ),
)
def test_a_measured_extreme_is_on_scale(key, observed_maximum):
    """Anything past the last class is drawn as the last class, which flattens
    the very gradient the reader is looking for."""
    palette = hrrr_products.get_product(key).palette

    assert palette.maximum >= observed_maximum, (
        f"{key} clips at {palette.maximum} but reached {observed_maximum}")


# --------------------------------------------------------------------------- #
# The compact name
#
# The sounding locator inset is about a hundred pixels wide, so it cannot show a
# product's full label. A field drawn there with nothing naming it is just a wash
# of colour: CAPE, helicity and a tornado parameter are the same reds in the same
# places, and the reader cannot tell which one they are looking at.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("product", ALL_PRODUCTS, ids=PRODUCT_IDS)
def test_every_product_has_a_compact_name(product):
    assert product.short_label, "a product with no short name cannot be labelled"
    assert product.short_label == product.short_label.strip()


@pytest.mark.parametrize("product", ALL_PRODUCTS, ids=PRODUCT_IDS)
def test_a_compact_name_stays_short_enough_to_show(product):
    """Long enough to elide is long enough to be useless in the inset."""
    assert len(product.short_label) <= 12, (
        "%r is too long for the locator chip" % product.short_label)


def test_no_two_products_share_a_compact_name():
    """An ambiguous chip is worse than a verbose one."""
    seen: dict[str, str] = {}
    for product in ALL_PRODUCTS:
        clash = seen.get(product.short_label)
        assert clash is None, (
            "%r names both %s and %s"
            % (product.short_label, clash, product.key))
        seen[product.short_label] = product.key


def test_a_product_without_an_explicit_chip_falls_back_to_its_key():
    """A new product is never unlabelled, even if nobody wrote a chip for it."""
    product = hrrr_products.PRODUCTS["stp"]
    bare = dataclasses.replace(product, chip="")

    assert bare.short_label == "STP"


@pytest.mark.parametrize("key,expected", [
    ("stp", "STP"),
    ("scp", "SCP"),
    ("refc", "REFC"),
    ("srh-0-3km", "0-3 km SRH"),
    ("shear-0-6km", "0-6 km Shr"),
    ("lapse-700-500", "700-500 LR"),
    ("uh-0-3km", "0-3 km UH"),
    ("tmp-2m", "2 m Temp"),
])
def test_a_slug_key_is_spelled_the_way_a_forecaster_writes_it(key, expected):
    """``srh-0-3km`` is a slug, not a name; the chip has to read as the latter."""
    assert hrrr_products.PRODUCTS[key].short_label == expected


@pytest.mark.parametrize("level", (200, 300, 500, 700, 850))
def test_a_height_wind_chip_names_the_filled_quantity(level):
    """The colours are isotachs; height is contour lines.

    A chip reading "500 mb Hgt" would point at the half of the product that is
    not what the colour scale measures.
    """
    chip = hrrr_products.PRODUCTS["hgt-wind-%d" % level].short_label

    assert chip == "%d mb Wind" % level
    assert "Hgt" not in chip
