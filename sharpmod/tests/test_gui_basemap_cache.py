"""Regression coverage for process-wide immutable basemap geometry."""

from __future__ import annotations

import pytest

from sharpmod import gui_maps


@pytest.fixture
def counted_basemap(monkeypatch):
    """Install a tiny basemap whose load and preparation calls are counted."""
    calls = {"load": 0, "prepare": 0}
    raw = {
        "coastline": [[[-100.0, 30.0], [-90.0, 40.0]]],
        "countries": [[[-95.0, 25.0], [-95.0, 45.0]]],
        "states": [],
    }
    real_prepare = gui_maps._prepare_basemap_layers

    def load():
        calls["load"] += 1
        return raw

    def prepare(basemap):
        calls["prepare"] += 1
        return real_prepare(basemap)

    monkeypatch.setattr(gui_maps, "_load_basemap", load)
    monkeypatch.setattr(gui_maps, "_prepare_basemap_layers", prepare)
    gui_maps._shared_basemap_layers.cache_clear()
    try:
        yield calls
    finally:
        gui_maps._shared_basemap_layers.cache_clear()


def test_all_picker_maps_load_and_prepare_basemap_once(qt_app, counted_basemap):
    widgets = [
        gui_maps.StationMapWidget([]),
        gui_maps.PointMapWidget(),
        gui_maps.PointMapWidget(),
        gui_maps.PointMapWidget(),
    ]
    try:
        assert counted_basemap == {"load": 1, "prepare": 1}
        assert all(widget._layers is widgets[0]._layers for widget in widgets)
        assert gui_maps._shared_basemap_layers.cache_info().misses == 1
        assert gui_maps._shared_basemap_layers.cache_info().hits == 3

        with pytest.raises(TypeError):
            widgets[0]._layers["states"] = ()
        with pytest.raises(AttributeError):
            widgets[0]._layers["coastline"].append(())
        with pytest.raises(TypeError):
            widgets[0]._layers["coastline"][0][1][0] = (0.0, 0.0)
    finally:
        for widget in widgets:
            widget.close()


def test_shared_geometry_does_not_share_mutable_widget_state(
        qt_app, counted_basemap):
    first = gui_maps.PointMapWidget()
    second = gui_maps.PointMapWidget()
    try:
        assert first._layers is second._layers
        assert first._basemap_refresh_timer is not second._basemap_refresh_timer

        first.resize(640, 480)
        second.resize(640, 480)
        first._basemap_pixmap()
        second._basemap_pixmap()
        second_raster = second._basemap_cache

        first.set_area("World")
        first.set_point(12.5, 145.7)
        first.set_domain((120.0, 160.0, 0.0, 30.0), "test domain")

        assert first._basemap_cache is None
        assert second._basemap_cache is second_raster
        assert second._area_name == "United States (CONUS)"
        assert second._point_lonlat == (-97.44, 35.63)
        assert second._domain_bounds is None
        assert second._domain_label == ""
    finally:
        first.close()
        second.close()
