"""T16 geography presentation: boundaries, labels, scale, and contrast."""

from __future__ import annotations

import math
from types import SimpleNamespace

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


def test_gui_scale_bar_reports_true_ground_distance(qt_app):
    from sharpmod.gui_maps import PointMapWidget

    widget = PointMapWidget()
    widget.resize(800, 500)
    widget.set_extent(-125.0, -66.0, 24.0, 50.0, pad=0.0)
    try:
        info = widget.scale_bar_info()
        assert info["length_km"] > 0.0
        assert info["length_km"] == pytest.approx(1000.0, rel=0.01)
        assert info["length_px"] == pytest.approx(
            1000.0 / info["km_per_pixel"], rel=0.01)
        projection = widget._proj()
        west = projection.forward(-110.0, 35.0)
        east = projection.forward(-109.0, 35.0)
        ground_px = abs(east[0] - west[0])
        # The flat projection squeezes longitudes by cos(view centre), not by
        # cos(sampled parallel): the bar measures at the view's own latitude.
        centre_lat = (widget._lat0 + widget._lat1) / 2.0
        expected_km = math.radians(1.0) * math.cos(
            math.radians(centre_lat)) * geo.EARTH_RADIUS_KM
        assert ground_px * info["km_per_pixel"] == pytest.approx(
            expected_km, rel=0.02)
        assert "km" in info["label"]
        assert info["projection"] in ("flat", "curved")
        assert widget.accessibleDescription() != ""
    finally:
        widget.close()
        widget.deleteLater()


def test_gui_place_labels_declutter_and_protect_selection(qt_app):
    from sharpmod.gui_maps import PointMapWidget

    widget = PointMapWidget()
    widget.resize(800, 500)
    try:
        widget.set_extent(-100.0, -94.0, 33.0, 38.0, pad=0.0)
        labels = widget.place_labels()
        assert labels, "expected town labels at a regional zoom"
        assert len(labels) <= geo.LABEL_MAX_COUNT
        names = [label for _x, _y, label in labels]
        assert any("Oklahoma" in name for name in names)
        widget.set_extent(-125.0, -66.0, 24.0, 50.0, pad=0.0)
        continental = widget.place_labels()
        continental_names = [label for _x, _y, label in continental]
        assert len(continental) <= len(labels)
        assert len(continental) <= geo.LABEL_MAX_COUNT
        assert any("Chicago" in name for name in continental_names)
        widget.set_label_density("sparse")
        sparse_names = [label for _x, _y, label in widget.place_labels()]
        assert all(name in continental_names for name in sparse_names)
        widget.set_label_density("off")
        assert tuple(widget.place_labels()) == ()
        assert widget.label_density() == "off"
        widget.set_label_density("bogus-density")
        assert widget.label_density() == "standard"
    finally:
        widget.close()
        widget.deleteLater()


def test_gui_boundary_hierarchy_paints_coastline_above_states(qt_app):
    from sharpmod.gui_maps import PointMapWidget

    widget = PointMapWidget()
    try:
        widget.resize(640, 480)
        widget.set_extent(-100.0, -90.0, 30.0, 42.0, pad=0.0)
        assert widget.boundary_zorder() == (
            "states", "countries", "lakes", "coastline")
    finally:
        widget.close()
        widget.deleteLater()


def test_repeated_paints_reuse_label_and_outline_layouts(qt_app):
    """Pans and hovers must not recompute the declutter or reprojection.

    Regression for the lag report: ``place_labels`` walked the whole place
    index and the outline pass reprojected ~113k basemap vertices on every
    paint, so every mouse move and pan stuttered once labels existed.
    """
    import time

    from sharpmod.gui_maps import PointMapWidget

    widget = PointMapWidget()
    widget.resize(900, 600)
    try:
        widget.set_extent(-100.0, -90.0, 30.0, 42.0, pad=0.0)
        first = widget.place_labels()
        assert first, "expected labels to lay out at a regional zoom"
        repeats = [widget.place_labels() for _ in range(10)]
        assert all(layout is first for layout in repeats), \
            "place_labels recomputed instead of reusing the cached layout"
        start = time.perf_counter()
        for _ in range(100):
            widget.place_labels()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        assert elapsed_ms < 50.0, \
            f"100 cached label reads took {elapsed_ms:.1f} ms"
        outlines = widget._cached_boundary_outlines(widget._proj())
        assert outlines, "expected cached boundary outlines"
        assert widget._cached_boundary_outlines(widget._proj()) is outlines, \
            "outline pass reprojected instead of reusing the cached layout"
        assert len(outlines) < 3000
    finally:
        widget.close()
        widget.deleteLater()


def test_deep_zoom_spreads_labels_across_the_whole_view(qt_app):
    """A close zoom must name towns across the frame, not one half of it.

    Regression for the "only the south part shows town names" report: the
    place resource is ordered alphabetically by state/county, so a
    first-come declutter let southern towns win every adjacency contest.
    """
    from sharpmod.gui_maps import PointMapWidget

    widget = PointMapWidget()
    widget.resize(900, 600)
    try:
        widget.set_extent(-101.0, -93.0, 30.0, 38.0, pad=0.0)
        labels = widget.place_labels()
        assert len(labels) >= 6, \
            f"expected several towns at this zoom, got {len(labels)}"
        mid_y = widget.height() / 2.0
        north = [label for x, y, label in labels if y < mid_y]
        south = [label for x, y, label in labels if y >= mid_y]
        assert north, "no labels in the northern half of a deep zoom"
        assert south, "no labels in the southern half of a deep zoom"
        assert min(len(north), len(south)) >= max(len(labels) // 4, 2), (
            f"labels cluster in one half: {len(north)} north vs "
            f"{len(south)} south of {len(labels)}")
        middle = [
            label for _x, y, label in labels
            if widget.height() * 0.25 <= y <= widget.height() * 0.75
        ]
        assert len(middle) >= max(len(labels) // 3, 4), (
            f"labels collapsed into top/bottom bands: only {len(middle)} of "
            f"{len(labels)} occur in the middle half")
    finally:
        widget.close()
        widget.deleteLater()


def test_place_layout_reads_only_visible_cells_and_survives_station_hover(
        qt_app, monkeypatch):
    """Zoom/hover must not rescan all cells or invalidate stable towns."""
    from qtpy.QtCore import QPointF, Qt

    from sharpmod import place_names
    from sharpmod.gui_maps import StationMapWidget

    class VisibleCells(dict):
        def items(self):
            raise AssertionError("place layout scanned the full cell dictionary")

    cells = VisibleCells({
        (35, -98): (
            ("Norman, Oklahoma", 35.22, -97.44, "place", 12.0),
            ("Moore, Oklahoma", 35.34, -97.49, "place", 9.0),
        ),
    })
    monkeypatch.setattr(place_names, "_conus_place_cells", lambda: cells)
    widget = StationMapWidget([
        {"id": "KOUN", "name": "Norman", "lat": 35.22, "lon": -97.44},
    ])
    widget.resize(900, 600)
    try:
        widget.set_extent(-99.0, -96.0, 34.0, 37.0, pad=0.0)
        first = widget.place_labels()
        assert first
        point = widget._to_px(-97.44, 35.22, widget._proj())
        event = SimpleNamespace(
            position=lambda: QPointF(point),
            buttons=lambda: Qt.NoButton,
        )
        widget.mouseMoveEvent(event)
        assert widget._hover_id == "KOUN"
        assert widget.place_labels() is first
    finally:
        widget.close()
        widget.deleteLater()


def test_gui_presentation_preferences_round_trip(qt_app):
    from sharpmod.gui_maps import PointMapWidget, presentation_preferences

    widget = PointMapWidget()
    widget.resize(640, 480)
    try:
        widget.set_label_density("dense")
        widget.set_scale_units("imperial")
        prefs = presentation_preferences(widget)
        assert prefs == {
            "label_density": "dense", "scale_units": "imperial",
            "legend": {"version": 1, "corner": "", "collapsed": False,
                       "locks": {}},
            "mode": "live"}
        assert widget.presentation_state() == prefs

        fresh = PointMapWidget()
        fresh.resize(640, 480)
        try:
            fresh.restore_presentation_preferences(
                {"label_density": "sparse", "scale_units": "metric"})
            assert fresh.label_density() == "sparse"
            assert fresh.scale_units() == "metric"
            fresh.restore_presentation_preferences({"label_density": "bogus"})
            assert fresh.label_density() == "sparse"
            fresh.restore_presentation_preferences(None)
            assert fresh.label_density() == "sparse"
            fresh.restore_presentation_preferences({"mode": "history"})
            assert fresh.map_mode() == "history"
            fresh.restore_presentation_preferences({"mode": "bogus"})
            assert fresh.map_mode() == "live"
        finally:
            fresh.close()
            fresh.deleteLater()
    finally:
        widget.close()
        widget.deleteLater()


def test_picker_rail_changes_label_density_and_scale_units(qt_app, tmp_path):
    from qtpy.QtCore import QSettings

    from sharpmod.gui_picker import PickerWindow

    settings = QSettings(
        str(tmp_path / "t16-presentation.ini"), QSettings.IniFormat)
    settings.clear()
    window = None
    try:
        window = PickerWindow()
        window._settings = settings
        tab = window._build_map_tab()
        tab.setParent(window)
        assert window._map.label_density() == "standard"
        combos = (window._map_label_density_combo, window._map_scale_units_combo)
        for combo in combos:
            combo.setParent(window)
        window._map_label_density_combo.setCurrentIndex(
            window._map_label_density_combo.findData("sparse"))
        qt_app.processEvents()
        assert window._map.label_density() == "sparse"
        assert window._startup_map_presentation()["label_density"] == "sparse"
        window._map_scale_units_combo.setCurrentIndex(
            window._map_scale_units_combo.findData("imperial"))
        qt_app.processEvents()
        assert window._map.scale_bar_info()["label"].endswith("mi")
        assert window._startup_map_presentation()["scale_units"] == "imperial"
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
        settings.clear()


def test_gui_keyboard_pan_and_zoom_keep_selection(qt_app):
    from qtpy.QtCore import QEvent, Qt
    from qtpy.QtGui import QKeyEvent

    from sharpmod.gui_maps import PointMapWidget

    widget = PointMapWidget()
    widget.resize(640, 480)
    widget.set_extent(-100.0, -90.0, 30.0, 42.0, pad=0.0)
    try:
        widget.set_point(35.0, -95.0)
        before = (widget._lon0, widget._lon1, widget._lat0, widget._lat1)
        widget.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
        assert (widget._lon0, widget._lon1) != (before[0], before[1])
        assert widget._point_lonlat == (-95.0, 35.0)
        widget.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, Qt.Key_Plus, Qt.NoModifier))
        assert (widget._lon1 - widget._lon0) < (before[1] - before[0])
        assert widget.focusPolicy() == Qt.StrongFocus
        assert widget.accessibleDescription() != ""
    finally:
        widget.close()
        widget.deleteLater()


def test_basemap_lines_clear_their_backgrounds():
    from sharpmod.theme import (
        COLOR_STYLE_THEMES, THEME_MAP_PALETTES, THEMES, map_palette,
    )

    for style, theme_name in COLOR_STYLE_THEMES.items():
        palette = map_palette(THEMES[theme_name])
        assert THEME_MAP_PALETTES[theme_name] is palette
        assert geo.contrast_ratio(
            palette.coastline, palette.background) >= 3.0, style
        assert geo.contrast_ratio(
            palette.countries, palette.background) >= 3.0, style
        assert geo.contrast_ratio(
            palette.graticule_label, palette.background) >= 3.0, style
        assert geo.contrast_ratio(
            palette.readout_text, palette.background) >= 4.5, style
