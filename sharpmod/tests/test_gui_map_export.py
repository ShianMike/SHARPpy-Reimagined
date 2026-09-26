"""T34 map figures: captured state, exact pixels and shared export workflow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from qtpy.QtCore import QBuffer, QByteArray
from qtpy.QtGui import QColor, QImage
from qtpy.QtWidgets import QDialog

from sharpmod.export_presentation import ExportIdentity, ExportPresentation, recent_exports
from sharpmod.gui_map_export import (
    _context_lines, capture_locator, capture_map, export_map_figure,
    render_map_figure,
)

pytestmark = pytest.mark.usefixtures("qt_app")

RUN = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)


def _raster(*, color="#ff0000", actual=None, opacity=0.65):
    from sharpmod.map_overlays import OverlayRaster

    image = QImage(40, 30, QImage.Format_ARGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.WriteOnly)
    assert image.save(buffer, "PNG")
    return OverlayRaster(
        key="hrrr_field", title="HRRR reflectivity", image_bytes=bytes(data),
        bounds=(-104, -88, 28, 47), valid_time=actual,
        short_name="refc", opacity=opacity, attribution="NOAA HRRR",
    )


def _map():
    from sharpmod.gui_maps import PointMapWidget

    widget = PointMapWidget()
    widget.setMinimumSize(1, 1)
    widget.resize(720, 540)
    widget.set_extent(-101, -91, 32, 43, pad=0.0)
    widget.set_point(36.5, -96.0)
    widget.set_box((35.0, -99.0, 39.0, -93.0))
    widget.set_forecast_reference(RUN, 6)
    widget.set_valid_time(RUN + timedelta(hours=6))
    widget.set_overlay("hrrr_field", _raster(actual=RUN + timedelta(hours=5)))
    return widget


def _prepare_hidden_panels(view, qt_app):
    """Lay out map painters without starting visibility-driven providers."""

    view.resize(1200, 800)
    view.ensurePolished()
    view.layout().activate()
    for panel in view._panels:
        panel.map.setMinimumSize(1, 1)
        panel.map.resize(560, 340)
    qt_app.processEvents()


def test_main_map_capture_preserves_layer_extent_time_selection_and_pixels():
    widget = _map()
    before = widget.grab().toImage()
    frame = capture_map(widget, title="Forecast map")
    assert frame.extent == widget.view_bounds()
    assert frame.selection.startswith("Point 36.500°") and "Box" in frame.selection
    assert frame.requested_time == RUN + timedelta(hours=6)
    assert frame.run == RUN and frame.forecast_hour == 6
    assert "requested" in frame.time_header and "showing" in frame.time_header
    assert frame.layers[0].opacity == 0.65
    assert frame.layers[0].actual_time == RUN + timedelta(hours=5)
    assert frame.layers[0].source == "refc"
    assert frame.layers[0].attribution == "NOAA HRRR"
    assert "Auto scale" in frame.legend
    assert frame.map_scale
    assert any(
        line == (
            f"Map scale/orientation: {frame.map_scale} · North is up · "
            "Equirectangular"
        )
        for line in _context_lines(frame)
    )
    choices = ExportPresentation(1600, 1200, preset="presentation")
    original = render_map_figure((frame,), choices, title="Frozen field")
    # Later updates cannot replace the picture or metadata in this frame.
    widget.set_overlay("hrrr_field", _raster(color="#00ff00", actual=RUN))
    widget.set_overlay_visible("hrrr_field", False)
    widget.set_extent(-110, -100, 35, 45, pad=0)
    assert render_map_figure((frame,), choices, title="Frozen field").toImage() == \
        original.toImage()
    assert widget.grab().toImage() != before
    assert widget.view_bounds() != frame.extent
    widget.close()


def test_detached_export_suppresses_only_the_duplicate_canvas_scale(monkeypatch):
    from sharpmod.gui_maps import PointMapWidget

    widget = _map()
    frame = capture_map(widget, title="Export style")
    observed = []
    original = PointMapWidget._draw_scale_bar

    def observe_scale_bar(self, painter, projection):
        observed.append(bool(getattr(self, "_export_suppress_scale_bar", False)))
        return original(self, painter, projection)

    monkeypatch.setattr(PointMapWidget, "_draw_scale_bar", observe_scale_bar)
    before_size = widget.size()
    output = frame.map_state.render(1200, 700)
    assert (output.width(), output.height()) == (1200, 700)
    assert observed == [True]
    assert not hasattr(widget, "_export_suppress_scale_bar")
    assert widget.size() == before_size
    widget.close()


def test_map_capture_freezes_relative_age_clock(monkeypatch):
    from sharpmod.map_overlays import OverlayRaster

    widget = _map()
    captured_at = RUN + timedelta(hours=7)
    frame = capture_map(
        widget, title="Clock-frozen field", captured_at=captured_at)
    observed = []
    original = OverlayRaster.age_seconds

    def observe_age(self, now=None):
        observed.append(now)
        return original(self, now)

    monkeypatch.setattr(OverlayRaster, "age_seconds", observe_age)
    first = frame.map_state.render(1200, 700)
    second = frame.map_state.render(1200, 700)
    assert first.toImage() == second.toImage()
    assert observed and all(value == captured_at for value in observed)
    assert frame.captured_at == captured_at
    assert not hasattr(widget, "_export_reference_time")
    widget.close()


def test_export_map_typography_is_independent_of_live_interface_scale(qt_app):
    from sharpmod.gui_theme import apply_theme

    widget = _map()
    frame = capture_map(widget, title="Explicit export typography")
    choices = ExportPresentation(1600, 1200, "presentation")
    try:
        apply_theme(qt_app, color_style="standard", text_scale=100)
        normal = render_map_figure((frame,), choices, title="Frozen map")
        apply_theme(qt_app, color_style="standard", text_scale=200)
        enlarged_ui = render_map_figure((frame,), choices, title="Frozen map")
        assert enlarged_ui.toImage() == normal.toImage()
    finally:
        apply_theme(qt_app, color_style="standard", text_scale=100)
        widget.close()


def test_map_palette_units_use_export_safe_glyphs():
    from sharpmod.hrrr_products import (
        PALETTE_CAPE, PALETTE_CIN, PALETTE_LAPSE_RATE, PALETTE_SRH,
        PALETTE_SRH_LOW, PALETTE_UPDRAFT_HELICITY,
    )

    assert PALETTE_CAPE.units == PALETTE_CIN.units == "J/kg"
    assert PALETTE_LAPSE_RATE.units == "°C/km"
    assert PALETTE_SRH.units == PALETTE_SRH_LOW.units == "m^2/s^2"
    assert PALETTE_UPDRAFT_HELICITY.units == "m^2/s^2"
    assert all("\ufffd" not in palette.units for palette in (
        PALETTE_CAPE, PALETTE_CIN, PALETTE_LAPSE_RATE, PALETTE_SRH,
        PALETTE_SRH_LOW, PALETTE_UPDRAFT_HELICITY,
    ))


def test_fixed_scale_hidden_layer_and_order_are_frozen():
    from sharpmod.map_overlays import OverlayLayer

    widget = _map()
    widget.set_overlay("spc_outlook", OverlayLayer(
        key="spc_outlook", title="SPC outlook", attribution="NOAA SPC"))
    widget.set_overlay_visible("spc_outlook", False)
    widget.set_scale_locked("refc", True)
    frame = capture_map(widget, title="Fixed field")
    assert tuple(item.key for item in frame.layers) == widget.paint_order_keys()
    assert frame.layers[-1].title == "SPC outlook"
    assert frame.layers[-1].visible is False
    assert "Fixed scale" in frame.legend
    widget.set_scale_locked("refc", False)
    assert "Fixed scale" in frame.legend
    assert "Auto scale" in capture_map(widget, title="Auto field").legend
    widget.close()


def test_two_four_panel_figure_records_slot_order_and_exact_dimensions(qt_app):
    from sharpmod.gui_map_panels import MapPanelsView

    view = MapPanelsView()
    _prepare_hidden_panels(view, qt_app)
    frames = tuple(capture_map(panel.map, title=panel.title.text())
                   for panel in view._panels)
    for count, size in ((2, (1600, 1200)), (4, (2400, 1600))):
        choices = ExportPresentation(*size, preset="custom")
        output = render_map_figure(frames[:count], choices,
                                   title=f"HRRR {count} fields")
        assert (output.width(), output.height()) == size
        assert len({frame.title for frame in frames[:count]}) == count
        assert len({frame.extent for frame in frames[:count]}) == 1
    view.shutdown()
    view.close()


def test_four_panel_print_keeps_distinct_actual_times_and_scales(qt_app):
    from sharpmod.gui_map_panels import MapPanelsView

    view = MapPanelsView(panel_count=4)
    _prepare_hidden_panels(view, qt_app)
    for index, panel in enumerate(view._panels):
        panel.map.set_forecast_reference(RUN, 6)
        panel.map.set_valid_time(RUN + timedelta(hours=6))
        panel.map.set_overlay("hrrr_field", _raster(
            actual=RUN + timedelta(hours=5 + index), opacity=0.5 + index * 0.1))
    frames = tuple(capture_map(panel.map, title=panel.title.text())
                   for panel in view._panels)
    assert len({frame.layers[0].opacity for frame in frames}) == 4
    assert len({frame.layers[0].actual_time for frame in frames}) == 4
    figure = render_map_figure(frames, ExportPresentation(2400, 1600, "print"),
                               title="Four different captured panels")
    assert (figure.width(), figure.height()) == (2400, 1600)
    view.shutdown()
    view.close()


def test_small_four_panel_figure_rejects_unreadable_map(qt_app):
    from sharpmod.gui_map_panels import MapPanelsView

    view = MapPanelsView()
    _prepare_hidden_panels(view, qt_app)
    frames = tuple(capture_map(panel.map, title=panel.title.text())
                   for panel in view._panels)
    with pytest.raises(ValueError, match="[Tt]oo little space"):
        render_map_figure(frames, ExportPresentation(320, 320, "custom"),
                          title="Four panels")
    view.shutdown()
    view.close()


def test_preview_and_saved_png_are_identical_and_use_recent_policy(
        qt_app, tmp_path, monkeypatch):
    from qtpy.QtCore import QSettings
    from qtpy.QtWidgets import QWidget
    from sharpmod import gui_map_export, render

    widget = _map()
    frame = capture_map(widget, title="Model field")
    owner = QWidget()
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.IniFormat)
    existing = tmp_path / "old.png"
    existing.write_bytes(b"old")
    expected = {}

    def accept_preview(dialog):
        dialog.sizes.set_presentation(ExportPresentation(1600, 1200,
                                                         "presentation"))
        dialog.title_edit.setText("Frozen analysis")
        dialog.capture_preview()
        expected["image"] = dialog.output_pixmap.toImage()
        return QDialog.Accepted

    monkeypatch.setattr(gui_map_export.ExportImageDialog, "exec", accept_preview)
    monkeypatch.setattr(gui_map_export, "available_export_path",
                        lambda *_args, **_kwargs: existing)
    monkeypatch.setattr(gui_map_export.QFileDialog, "getSaveFileName",
                        lambda *_args, **_kwargs: (str(existing), ""))
    completed = []
    output = export_map_figure(
        owner, (frame,), settings=settings, namespace="map-test",
        identity=ExportIdentity("Norman", "HRRR", RUN, valid_start=RUN),
        kind="map", title="Forecast field", status=completed.append,
    )
    assert output == existing and completed
    assert QImage(str(existing)) == expected["image"]
    assert (QImage(str(existing)).width(), QImage(str(existing)).height()) == \
        (1600, 1200)
    assert recent_exports(settings)[0].path == str(existing)
    assert render.save_pixmap_png_atomic is gui_map_export.save_pixmap_png_atomic
    assert widget.view_bounds() == frame.extent
    owner.close()
    widget.close()


def test_locator_snapshot_freezes_pinned_extent_requested_sampled_and_hazard(qt_app):
    from sharpmod import locator_presentation as lp
    from sharpmod.tests.test_gui_locator import _window_fixture

    win, _controller, _map, collection, source = _window_fixture(qt_app)
    lp.set_collection_presentation(collection, lp.LocatorPresentation(
        link_mode=lp.LINK_PINNED,
        extent_mode=lp.EXTENT_CUSTOM,
        custom_bounds=(-99, 33, -95, 37),
    ))
    lp.set_overlay_status(collection, "spc_outlook", "unavailable",
                          family="risk", product="torn",
                          detail="Outside SPC outlook coverage")
    frame = capture_locator(source)
    assert frame.extent == (-99, -95, 33, 37)
    assert "Requested" in frame.selection and "Sampled" in frame.selection
    assert frame.layers[0].time_match == "Outside SPC outlook coverage"
    assert frame.attribution.startswith("U.S. Census Bureau boundaries")
    assert (
        "Map scale/orientation: scale bar in kilometers · North is up · "
        "Equirectangular"
    ) in _context_lines(frame)
    choices = ExportPresentation(1600, 1200, "presentation")
    original = render_map_figure((frame,), choices, title="Pinned locator")
    collection.setMeta("lat", 44.0)
    lp.set_collection_presentation(collection, lp.LocatorPresentation())
    newer = render_map_figure((frame,), choices, title="Pinned locator")
    assert original.toImage() == newer.toImage()
    assert frame.extent == (-99, -95, 33, 37)
    win.close()


def test_locator_snapshot_freezes_followed_main_map_extent(qt_app):
    from sharpmod import locator_presentation as lp
    from sharpmod.tests.test_gui_locator import _window_fixture

    win, _controller, _map, collection, source = _window_fixture(qt_app)
    followed = (-105.0, 28.0, -88.0, 44.0)
    lp.set_collection_presentation(collection, lp.LocatorPresentation(
        link_mode=lp.LINK_FOLLOW,
        extent_mode=lp.EXTENT_CUSTOM,
        custom_bounds=(-99.0, 33.0, -95.0, 37.0),
        main_bounds=followed,
    ))
    frame = capture_locator(source)
    assert frame.extent == (-105.0, -88.0, 28.0, 44.0)
    assert frame.mode == lp.LINK_FOLLOW
    assert frame.legend == "Follow main map"
    assert (
        "Map scale/orientation: scale bar in kilometers · North is up · "
        "Equirectangular"
    ) in _context_lines(frame)
    choices = ExportPresentation(1600, 1200, "presentation")
    original = render_map_figure((frame,), choices, title="Followed locator")
    lp.set_collection_presentation(collection, lp.LocatorPresentation(
        link_mode=lp.LINK_PINNED,
        extent_mode=lp.EXTENT_REGIONAL,
    ))
    assert render_map_figure(
        (frame,), choices, title="Followed locator").toImage() == original.toImage()
    win.close()


def test_picker_map_and_panel_actions_capture_the_active_context(
        qt_app, tmp_path, monkeypatch):
    from qtpy.QtCore import QSettings
    from sharpmod import gui_map_export, gui_picker
    from sharpmod.gui_map_panels import MapPanelsView

    saved = []
    monkeypatch.setattr(gui_map_export, "export_map_figure",
                        lambda _owner, frames, **kwargs: saved.append(
                            {"frames": frames, **kwargs}))
    station = _map()
    station.show()
    qt_app.processEvents()
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.IniFormat)
    fake_tabs = SimpleNamespace(currentIndex=lambda: 0,
                                tabText=lambda _index: "Forecast Model")
    owner = SimpleNamespace(
        _tabs=fake_tabs, _settings=settings,
        _active_map_widget=lambda: station,
        statusBar=lambda: SimpleNamespace(showMessage=lambda *_args: None),
    )
    gui_picker.PickerWindow._export_active_map_figure(owner)
    assert saved[-1]["namespace"] == "map-standalone"
    assert saved[-1]["identity"].initialization == RUN
    assert saved[-1]["identity"].valid_start == RUN + timedelta(hours=6)
    assert saved[-1]["kind"] == "map"

    panels = MapPanelsView(panel_count=4)
    panels.resize(1300, 900)
    # This test needs a visible panel view to exercise the picker guard, but
    # never needs provider data; keep showEvent from enabling live controllers.
    monkeypatch.setattr(panels, "_set_running", lambda _running: None)
    panels.show()
    qt_app.processEvents()
    for panel in panels._panels:
        panel.map.set_forecast_reference(RUN, 6)
        panel.map.set_valid_time(RUN + timedelta(hours=6))
    owner._panels_view = panels
    gui_picker.PickerWindow._export_panel_map_figure(owner)
    assert saved[-1]["namespace"] == "map-panels"
    assert saved[-1]["kind"] == "map-4panel"
    assert len(saved[-1]["frames"]) == 4
    panels.shutdown()
    panels.close()
    station.close()


def test_locator_dialog_uses_shared_exact_preview_and_completion(
        qt_app, tmp_path, monkeypatch):
    from qtpy.QtCore import QSettings
    from sharpmod import gui_map_export
    from sharpmod.gui_locator import export_locator_with_dialog
    from sharpmod.tests.test_gui_locator import _window_fixture

    win, controller, _map, _collection, _source = _window_fixture(qt_app)
    controller._settings = QSettings(str(tmp_path / "prefs.ini"),
                                     QSettings.IniFormat)
    saved = tmp_path / "locator.png"
    preview = {}

    def accept_preview(dialog):
        dialog.sizes.set_presentation(ExportPresentation(1600, 1200,
                                                         "presentation"))
        dialog.capture_preview()
        preview["pixels"] = dialog.output_pixmap.toImage()
        return QDialog.Accepted

    monkeypatch.setattr(gui_map_export.ExportImageDialog, "exec", accept_preview)
    monkeypatch.setattr(gui_map_export.QFileDialog, "getSaveFileName",
                        lambda *_args, **_kwargs: (str(saved), ""))
    export_locator_with_dialog(win, controller)
    assert saved.is_file()
    assert QImage(str(saved)) == preview["pixels"]
    assert recent_exports(controller._settings)[0].path == str(saved)
    win.close()


def test_atomic_map_failure_preserves_existing_destination_and_no_history(
        qt_app, tmp_path, monkeypatch):
    from qtpy.QtCore import QSettings
    from qtpy.QtWidgets import QWidget
    from sharpmod import gui_map_export

    widget = _map()
    frame = capture_map(widget, title="Old frame")
    owner = QWidget()
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.IniFormat)
    destination = tmp_path / "existing.png"
    destination.write_bytes(b"old complete content")

    def accept_preview(dialog):
        dialog.capture_preview()
        return QDialog.Accepted

    warnings = []
    monkeypatch.setattr(gui_map_export.ExportImageDialog, "exec", accept_preview)
    monkeypatch.setattr(gui_map_export, "available_export_path",
                        lambda *_args, **_kwargs: destination)
    monkeypatch.setattr(gui_map_export.QFileDialog, "getSaveFileName",
                        lambda *_args, **_kwargs: (str(destination), ""))
    monkeypatch.setattr(gui_map_export, "save_pixmap_png_atomic",
                        lambda *_args: False)
    monkeypatch.setattr(gui_map_export.QMessageBox, "warning",
                        lambda *_args: warnings.append(_args[-1]))
    result = export_map_figure(
        owner, (frame,), settings=settings, namespace="map-test-fail",
        identity=ExportIdentity("Norman", "HRRR", RUN),
        kind="map", title="Failure test",
    )
    assert result is None and "previous destination preserved" in warnings[0]
    assert destination.read_bytes() == b"old complete content"
    assert recent_exports(settings) == ()
    assert sorted(path.name for path in Path(tmp_path).iterdir()
                  if path.suffix == ".png") == ["existing.png"]
    widget.close()
    owner.close()
