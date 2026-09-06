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
    """The old scale put every point weaker than -25 into one colour.

    On a summer CONUS domain that was 83% of the map, most of it not capped at
    all, which made the CIN product a flat wash that said nothing.
    """
    palette = hrrr_products.get_product("mlcin").palette

    drawn = _alpha([0.0, -1.0, -10.0, -24.0], palette)

    assert not drawn.any()


def test_a_real_cap_is_drawn_and_graded():
    palette = hrrr_products.get_product("mlcin").palette
    values = [-25.0, -100.0, -300.0, -600.0, -800.0]

    assert _alpha(values, palette).all()
    rgba = hrrr_field.colourize(np.asarray(values), palette)
    assert len({tuple(row[:3]) for row in rgba}) == len(values), \
        "a deep cap must not read the same as a shallow one"


def test_the_cin_scale_reaches_an_observed_extreme():
    """A capped summer morning reaches -600 J/kg; -583 was measured."""
    palette = hrrr_products.get_product("mlcin").palette

    assert palette.minimum <= -800.0
    assert palette.ceiling == -25.0


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


def test_shallow_and_deep_helicity_do_not_share_a_scale():
    shallow = hrrr_products.get_product("srh-0-1km").palette
    deep = hrrr_products.get_product("srh-0-3km").palette

    assert shallow.floor < deep.floor
    assert shallow.maximum < deep.maximum


@pytest.mark.parametrize(
    ("level", "expected_floor"),
    ((850, 10.0), (700, 10.0), (500, 20.0), (300, 30.0), (200, 30.0)),
)
def test_each_level_gets_its_own_isotach_range(level, expected_floor):
    """One 20-180 kt scale left the 850 mb map 90% blank and used 4 classes."""
    palette = hrrr_products.get_product(f"hgt-wind-{level}").palette

    assert palette.floor == expected_floor
    assert palette.units == "kt"


def test_isotach_ranges_grow_with_height():
    tops = [hrrr_products.get_product(f"hgt-wind-{level}").palette.maximum
            for level in (850, 700, 500, 300, 200)]

    assert tops == sorted(tops)
    assert tops[0] < tops[-1], "a 200 mb jet outruns an 850 mb one"


def test_every_isotach_level_has_a_colour_for_every_class():
    for level, edges in hrrr_products._ISOTACH_EDGES.items():
        assert len(edges) == len(hrrr_products._ISOTACH_COLOURS), level


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
