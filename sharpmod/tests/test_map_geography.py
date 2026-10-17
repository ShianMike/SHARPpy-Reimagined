"""T16 geography presentation: boundaries, labels, scale, and contrast."""

from __future__ import annotations

import math

import pytest

from sharpmod import map_geography as geo


def test_boundary_draw_order_is_dimmest_first_with_coastline_on_top():
    assert geo.boundary_draw_order(
        ("coastline", "states", "countries", "lakes")) == (
        "states", "countries", "lakes", "coastline")
    assert geo.boundary_draw_order(("states", "future-layer")) == (
        "states", "future-layer")


def test_boundary_widths_keep_coastline_heaviest():
    widths = {name: geo.boundary_width(name) for name in geo.BOUNDARY_ZORDER}
    assert widths["coastline"] > widths["countries"] >= widths["lakes"]
    assert widths["lakes"] >= widths["states"]
    assert geo.boundary_width("unknown-family") == pytest.approx(1.0)
    assert geo.boundary_width("coastline", density_scale="bad") == (
        pytest.approx(widths["coastline"]))


def test_label_tiers_appear_as_the_view_closes_in():
    assert geo.labels_visible_for_span("standard", 60.0) == (True, False, False)
    assert geo.labels_visible_for_span("standard", 20.0) == (True, True, False)
    assert geo.labels_visible_for_span("standard", 8.0) == (True, True, False)
    assert geo.labels_visible_for_span("dense", 20.0) == (True, True, False)
    assert geo.labels_visible_for_span("dense", 8.0) == (True, True, True)
    assert geo.labels_visible_for_span("sparse", 8.0) == (True, False, False)
    assert geo.labels_visible_for_span("off", 8.0) == (False, False, False)
    assert geo.normalise_label_density("bogus") == "standard"


def test_declutter_keeps_important_labels_and_protects_markers():
    candidates = [
        (1, 100.0, 100.0, "Town A"),
        (1, 110.0, 105.0, "Town B"),
        (2, 300.0, 300.0, "Town C"),
        (0, 500.0, 100.0, "Selected place"),
    ]
    kept = geo.declutter_labels(
        candidates, protected=[(505.0, 105.0)], min_separation_px=48.0)
    labels = [label for _importance, _x, _y, label in kept]
    assert "Town A" in labels
    assert "Town B" not in labels
    assert "Town C" in labels
    assert "Selected place" not in labels


def test_declutter_is_bounded_and_ignores_bad_entries():
    candidates = [(5, float("nan"), 0.0, "Nowhere")]
    candidates += [(9, float(i), 0.0, "") for i in range(10)]
    candidates += [(1, float(i * 100), 0.0, f"Place {i}") for i in range(60)]
    kept = geo.declutter_labels(candidates)
    assert len(kept) <= geo.LABEL_MAX_COUNT
    assert all(str(label).strip() for _, _, _, label in kept)
    assert geo.declutter_labels(candidates, limit=0) == []


def test_flat_scale_uses_cosine_squeeze_not_constant_distance():
    equatorial = geo.km_per_pixel_at_latitude(59.0, 800.0, 0.0)
    northern = geo.km_per_pixel_at_latitude(59.0, 800.0, 60.0)
    assert equatorial == pytest.approx(8.20, abs=0.05)
    assert northern == pytest.approx(equatorial / 2.0, rel=0.02)
    assert math.isnan(geo.km_per_pixel_at_latitude(59.0, 0.0, 35.0))


def test_scale_bar_snaps_to_a_nice_length_near_the_target():
    length, pixels, unit = geo.pick_scale_length(8.2)
    assert (length, unit) == (1000.0, "km")
    assert pixels == pytest.approx(1000.0 / 8.2, rel=0.01)
    imperial = geo.pick_scale_length(8.2, imperial=True)
    assert imperial[2] == "mi"
    assert imperial[0] == pytest.approx(2000.0 / geo.KM_PER_MILE, rel=0.02)
    assert geo.pick_scale_length(float("nan"))[:2] == (0.0, 0.0)
    assert geo.format_scale_length(1000.0, "km") == "1000 km"
    assert geo.format_scale_length(50.0, "km") == "50 km"
    assert geo.format_scale_length(float("nan"), "km") == "Unknown km"
