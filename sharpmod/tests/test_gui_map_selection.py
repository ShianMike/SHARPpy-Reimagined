"""Focused regressions for T18 separate selection, inspection, and loading.

Covers the map-level contracts before the rail/picker wiring:

* T18.1 explicit tools (select/inspect/box) with active-state styling,
  cursors, and hints; Shift-drag always draws a box; box click still picks
  a point; tools persist by map focus and never steal typing.
* T18.1 sounding-selection lock: a locked map refuses click/drag point
  changes and keyboard recent-point moves, while inspection, pan, zoom,
  history, and box gestures keep working; unlocking restores picking.
* T18.1 reversible recent point: each committed selection remembers the
  previous one; restoring returns without pushing view history.
* T18.2 hover readout and pinned card: hover reports the numeric field
  value with units/source/time without moving the sounding point; clicking
  pins a card that survives hovers and pans; Escape/unpin clears it.
* T18.3 requested versus sampled: the card shows both coordinates plus
  distance and resolution context; copy actions emit full-precision text;
  save-location hands the sampled point to the saved-location store.
* T18.4 numeric provenance: values come from the cached derived grid in
  the product's display units with run/valid text; imagery-only or
  out-of-range states name their limitation and never present a
  colour-derived number; remembered frames share the render's run/fxx.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timezone

import pytest
from qtpy.QtCore import QEvent, QPointF, Qt
from qtpy.QtGui import QKeyEvent

pytestmark = pytest.mark.usefixtures("qt_app")

RUN = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)


class _MouseEvent:
    def __init__(self, pos, *, button=Qt.LeftButton, buttons=None,
                 modifiers=Qt.NoModifier):
        self._pos = QPointF(*pos) if isinstance(pos, tuple) else pos
        self._button = button
        self._buttons = buttons if buttons is not None else button
        self._modifiers = modifiers

    def position(self):
        return self._pos

    def button(self):
        return self._button

    def buttons(self):
        return self._buttons

    def modifiers(self):
        return self._modifiers

    def x(self):
        return self._pos.x()

    def y(self):
        return self._pos.y()


class _WheelEvent:
    def __init__(self, *, angle_y=120, pos=(320.0, 240.0)):
        self._angle_y = angle_y
        self._pos = QPointF(*pos)

    def angleDelta(self):
        from qtpy.QtCore import QPoint

        return QPoint(0, self._angle_y)

    def pixelDelta(self):
        from qtpy.QtCore import QPoint

        return QPoint(0, 0)

    def position(self):
        return self._pos

    def modifiers(self):
        return Qt.NoModifier

    def isInverted(self):
        return False

    def inverted(self):
        return False


def _map(kind="point"):
    from sharpmod import gui

    if kind == "point":
        return gui.PointMapWidget()
    return gui.StationMapWidget([])


def _sized(widget, lon0=-100.0, lon1=-90.0, lat0=30.0, lat1=42.0):
    widget.resize(640, 480)
    widget.set_extent(lon0, lon1, lat0, lat1, pad=0.0)
    try:
        widget._basemap_pixmap()
    except Exception:  # noqa: BLE001 - basemap cache is optional here
        pass
    return widget


def _drag(widget, start, end, *, modifiers=Qt.NoModifier):
    widget.mousePressEvent(_MouseEvent(start, modifiers=modifiers))
    widget.mouseMoveEvent(_MouseEvent(end, modifiers=modifiers))
    widget.mouseReleaseEvent(_MouseEvent(end, modifiers=modifiers))


def _click(widget, pos, *, modifiers=Qt.NoModifier):
    widget.mousePressEvent(_MouseEvent(pos, modifiers=modifiers))
    widget.mouseReleaseEvent(_MouseEvent(pos, modifiers=modifiers))


def _remember_frame(product="mlcape", value=1500.0):
    import numpy as np

    from sharpmod import hrrr_field

    field = np.full((1536, 3072), float(value), dtype=np.float32)
    hrrr_field.remember_derived(product, RUN, 6, (3072, 1536), field)
    return hrrr_field


def _clear_frames():
    from sharpmod import hrrr_field

    hrrr_field.clear_derived_cache()


# --------------------------------------------------------------------------- #
# T18.1 explicit tools
# --------------------------------------------------------------------------- #

def test_tool_row_tracks_widget_and_box_syncs_both_ways():
    from qtpy.QtWidgets import QToolButton

    from sharpmod.gui_picker_layout import map_tool_row

    widget = _sized(_map())
    try:
        row = map_tool_row(widget, allow_box=True)
        buttons = {}

        def _collect(layout):
            for index in range(layout.count()):
                item = layout.itemAt(index)
                child = item.widget()
                if isinstance(child, QToolButton):
                    buttons[child.accessibleName()] = child

        _collect(row)
        assert set(buttons) == {"Map select tool", "Map inspect tool",
                                "Map draw-box tool"}
        assert buttons["Map select tool"].isChecked()
        widget.set_map_tool("inspect")
        widget.repaint()
        assert buttons["Map inspect tool"].isChecked()
        buttons["Map draw-box tool"].click()
        assert widget.map_tool() == "box"
        assert widget.box_mode() is True
        widget.set_box_mode(False)
        assert widget.map_tool() == "select"
        point_row = map_tool_row(_sized(_map(kind="station")), allow_box=False)
        names = []

        def _collect_names(layout):
            for index in range(layout.count()):
                item = layout.itemAt(index)
                child = item.widget()
                if isinstance(child, QToolButton):
                    names.append(child.accessibleName())

        _collect_names(point_row)
        assert "Map draw-box tool" not in names
    finally:
        widget.close()
        widget.deleteLater()


def test_view_menu_tool_lock_recent_and_copy_actions():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window.show()
        for tool in ("inspect", "box", "select"):
            window._trigger_map_tool(tool)
            active = window._active_map_widget()
            assert active is not None
            allowed = (active._allowed_tools()
                       if hasattr(active, "_allowed_tools") else ("select",))
            assert active.map_tool() == (tool if tool in allowed else "select")
        window._trigger_map_lock(True)
        active = window._active_map_widget()
        assert active.is_point_locked() is True
        window._refresh_tool_actions()
        assert window._lock_point_action.isChecked() is True
        window._trigger_map_lock(False)
        assert active.is_point_locked() is False
        assert window._recent_point_action is not None
        assert window._copy_coords_action is not None
        assert window._copy_value_action is not None
        assert window._save_point_action is not None
        window._select_tab("Field Panels")
        window._ensure_tab("Field Panels")
        active = window._active_map_widget()
        assert active is not None
        window._trigger_map_tool("inspect")
        assert active.map_tool() == "inspect"
        window._trigger_map_tool("select")
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_forecast_point_lock_and_recent_buttons():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Forecast Model")
        window.show()
        assert window._model_lock_btn.isCheckable()
        window._model_lock_btn.setChecked(True)
        assert window._model_map.is_point_locked() is True
        assert window._model_lock_btn.text() == "Unlock point"
        window._model_lock_btn.setChecked(False)
        assert window._model_map.is_point_locked() is False
        assert window._model_recent_btn is not None
    finally:
        if window is not None:
            window.close()
            window.deleteLater()

def test_map_tool_defaults_to_select_with_hint_and_cursor():
    widget = _sized(_map())
    try:
        assert widget.map_tool() == "select"
        assert "click to place" in widget.tool_hint_text().casefold()
        assert "double-click to fetch" in widget.tool_hint_text().casefold()
        assert widget.cursor().shape() == Qt.CrossCursor
    finally:
        widget.close()
        widget.deleteLater()


def test_setting_inspect_tool_changes_cursor_hint_and_tooltip():
    widget = _sized(_map())
    try:
        widget.set_map_tool("inspect")
        assert widget.map_tool() == "inspect"
        assert widget.cursor().shape() == Qt.WhatsThisCursor
        assert "without moving" in widget.toolTip().casefold() or "inspect" in widget.toolTip().casefold()
        assert "without moving" in widget.tool_hint_text().casefold() or "inspect" in widget.tool_hint_text().casefold()
    finally:
        widget.close()
        widget.deleteLater()


def test_setting_box_tool_arms_sticky_box_gesture():
    widget = _sized(_map())
    try:
        widget.set_map_tool("box")
        assert widget.map_tool() == "box"
        assert widget.box_mode() is True
        boxes = []
        widget.boxSelected.connect(lambda *args: boxes.append(args))
        _drag(widget, (150.0, 120.0), (320.0, 260.0))
        assert len(boxes) == 1
    finally:
        widget.close()
        widget.deleteLater()


def test_leaving_box_tool_disarms_sticky_mode_but_keeps_committed_box():
    widget = _sized(_map())
    try:
        widget.set_map_tool("box")
        _drag(widget, (150.0, 120.0), (320.0, 260.0), modifiers=Qt.ShiftModifier)
        assert widget.box() is not None
        widget.set_map_tool("select")
        assert widget.box_mode() is False
        assert widget.box() is not None
    finally:
        widget.close()
        widget.deleteLater()


def test_unknown_tool_falls_back_to_select():
    widget = _sized(_map())
    try:
        widget.set_map_tool("watermelon")
        assert widget.map_tool() == "select"
    finally:
        widget.close()
        widget.deleteLater()


def test_inspect_click_never_moves_the_sounding_point():
    widget = _sized(_map())
    try:
        widget.set_point(35.0, -95.0)
        widget.set_map_tool("inspect")
        picked = []
        widget.pointSelected.connect(lambda lat, lon: picked.append((lat, lon)))
        _click(widget, (420.0, 300.0))
        assert picked == []
        assert widget._point_lonlat == (-95.0, 35.0)
        assert widget.inspection_snapshot() is not None
    finally:
        widget.close()
        widget.deleteLater()


def test_inspect_double_click_never_fetches():
    widget = _sized(_map())
    try:
        widget.set_point(35.0, -95.0)
        widget.set_map_tool("inspect")
        activated = []
        widget.pointActivated.connect(lambda lat, lon: activated.append((lat, lon)))
        widget.mouseDoubleClickEvent(_MouseEvent((420.0, 300.0)))
        assert activated == []
        assert widget._point_lonlat == (-95.0, 35.0)
    finally:
        widget.close()
        widget.deleteLater()


def test_select_click_still_places_the_point():
    widget = _sized(_map())
    try:
        widget.set_map_tool("select")
        picked = []
        widget.pointSelected.connect(lambda lat, lon: picked.append((lat, lon)))
        _click(widget, (420.0, 300.0))
        assert len(picked) == 1
    finally:
        widget.close()
        widget.deleteLater()


def test_shift_drag_draws_a_box_in_any_tool():
    widget = _sized(_map())
    try:
        for tool in ("select", "inspect"):
            widget.set_map_tool(tool)
            boxes = []
            widget.boxSelected.connect(lambda *args: boxes.append(args))
            before = len(boxes)
            _drag(widget, (140.0, 100.0), (380.0, 280.0),
                  modifiers=Qt.ShiftModifier)
            assert len(boxes) == before + 1, tool
            widget.set_box(None)
            # Disconnect this iteration's recorder before the next tool so
            # the second assertion counts only its own gesture.
            try:
                widget.boxSelected.disconnect()
            except (RuntimeError, TypeError):
                pass
    finally:
        widget.close()
        widget.deleteLater()


def test_tool_keyboard_shortcuts_switch_tools_without_typing():
    widget = _sized(_map())
    try:
        widget.set_map_tool("select")
        widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_I, Qt.NoModifier))
        assert widget.map_tool() == "inspect"
        widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_V, Qt.NoModifier))
        assert widget.map_tool() == "select"
        widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_B, Qt.NoModifier))
        assert widget.map_tool() == "box"
        widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        assert widget.map_tool() == "select"
    finally:
        widget.close()
        widget.deleteLater()


def test_tool_shortcuts_require_no_modifiers():
    widget = _sized(_map())
    try:
        widget.set_map_tool("select")
        widget.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, Qt.Key_I, Qt.ControlModifier))
        assert widget.map_tool() == "select"
    finally:
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# T18.1 lock + reversible recent point
# --------------------------------------------------------------------------- #

def test_lock_blocks_click_point_changes_but_keeps_navigation():
    widget = _sized(_map())
    try:
        widget.set_point(35.0, -95.0)
        widget.set_point_locked(True)
        assert widget.is_point_locked() is True
        picked = []
        widget.pointSelected.connect(lambda lat, lon: picked.append((lat, lon)))
        _click(widget, (420.0, 300.0))
        assert picked == []
        assert widget._point_lonlat == (-95.0, 35.0)
        before = widget.view_bounds()
        widget.mousePressEvent(_MouseEvent((300.0, 220.0)))
        widget.mouseMoveEvent(_MouseEvent((380.0, 280.0)))
        widget.mouseReleaseEvent(_MouseEvent((380.0, 280.0)))
        assert widget.view_bounds() != before
    finally:
        widget.close()
        widget.deleteLater()


def test_lock_blocks_box_mode_point_fallback_pick():
    widget = _sized(_map())
    try:
        widget.set_point(35.0, -95.0)
        widget.set_point_locked(True)
        picked = []
        widget.pointSelected.connect(lambda lat, lon: picked.append((lat, lon)))
        _click(widget, (420.0, 300.0), modifiers=Qt.ShiftModifier)
        assert picked == []
        assert widget._point_lonlat == (-95.0, 35.0)
    finally:
        widget.close()
        widget.deleteLater()


def test_unlock_restores_click_picking():
    widget = _sized(_map())
    try:
        widget.set_point_locked(True)
        widget.set_point_locked(False)
        picked = []
        widget.pointSelected.connect(lambda lat, lon: picked.append((lat, lon)))
        _click(widget, (420.0, 300.0))
        assert len(picked) == 1
    finally:
        widget.close()
        widget.deleteLater()


def test_recent_point_restores_previous_selection_without_history_push():
    widget = _sized(_map())
    try:
        widget.set_point(35.0, -95.0)
        depth_before = widget.navigation_history_depth()
        _click(widget, (420.0, 300.0))
        assert widget.has_recent_point() is True
        widget.restore_recent_point()
        assert widget._point_lonlat == (-95.0, 35.0)
        assert widget.navigation_history_depth() == depth_before
        assert widget.has_recent_point() is False
    finally:
        widget.close()
        widget.deleteLater()


def test_programmatic_set_point_does_not_rewrite_recent_point():
    widget = _sized(_map())
    try:
        widget.set_point(35.0, -95.0)
        _click(widget, (420.0, 300.0))
        first_recent = widget.recent_point()
        widget.set_point(36.0, -96.0)
        assert widget.recent_point() == first_recent
    finally:
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# T18.2 hover readout + pinned card
# --------------------------------------------------------------------------- #

def test_hover_reports_numeric_value_without_moving_point():
    widget = _sized(_map())
    hrrr = _remember_frame()
    try:
        widget.set_point(35.0, -95.0)
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        widget.mouseMoveEvent(_MouseEvent((420.0, 300.0)))
        assert widget._point_lonlat == (-95.0, 35.0)
        readout = widget.hover_inspection_text()
        assert "1500" in readout
        assert "J kg" in readout or "J" in readout
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()


def test_hover_with_no_frame_names_imagery_limitation():
    widget = _sized(_map())
    try:
        widget.set_point(35.0, -95.0)
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        widget.mouseMoveEvent(_MouseEvent((420.0, 300.0)))
        readout = widget.hover_inspection_text()
        assert "magery" in readout
        assert "no numeric value" in readout.casefold()
    finally:
        widget.close()
        widget.deleteLater()


def test_pinned_card_survives_hover_and_pan():
    widget = _sized(_map())
    hrrr = _remember_frame()
    try:
        widget.set_point(35.0, -95.0)
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        _click(widget, (420.0, 300.0))
        snapshot = widget.inspection_snapshot()
        assert snapshot is not None
        assert snapshot.get("kind") == "field"
        widget.mouseMoveEvent(_MouseEvent((100.0, 100.0)))
        assert widget.inspection_snapshot() == snapshot
        widget.mousePressEvent(_MouseEvent((300.0, 220.0)))
        widget.mouseMoveEvent(_MouseEvent((380.0, 280.0)))
        widget.mouseReleaseEvent(_MouseEvent((380.0, 280.0)))
        assert widget.inspection_snapshot() == snapshot
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()


def test_escape_clears_pinned_card_before_box_state():
    widget = _sized(_map())
    hrrr = _remember_frame()
    try:
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        _click(widget, (420.0, 300.0))
        assert widget.inspection_snapshot() is not None
        widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        assert widget.inspection_snapshot() is None
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()


def test_unpin_clears_card():
    widget = _sized(_map())
    hrrr = _remember_frame()
    try:
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        _click(widget, (420.0, 300.0))
        assert widget.inspection_snapshot() is not None
        widget.clear_inspection()
        assert widget.inspection_snapshot() is None
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()


def test_station_hover_card_pins_without_selecting():
    widget = _sized(_map(kind="station"))
    try:
        widget.set_stations([
            {"id": "KOAX", "name": "Omaha", "lat": 41.3, "lon": -96.0},
        ])
        widget.set_map_tool("inspect")
        pos = widget._to_px(-96.0, 41.3)
        _click(widget, (pos.x(), pos.y()))
        assert widget._selected_id is None
        snapshot = widget.inspection_snapshot()
        assert snapshot is not None
        assert snapshot.get("station_id") == "KOAX"
    finally:
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# T18.3 requested vs sampled, copy, save-location
# --------------------------------------------------------------------------- #

def test_card_shows_requested_and_sampled_coordinates_with_distance():
    widget = _sized(_map())
    hrrr = _remember_frame()
    try:
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        _click(widget, (420.0, 300.0))
        snapshot = widget.inspection_snapshot()
        assert snapshot.get("requested_lat") != pytest.approx(
            snapshot.get("sampled_lat"))
        lines = widget.inspection_card_lines()
        joined = "\n".join(lines)
        assert "Sampled" in joined
        assert "Requested" in joined
        assert "km apart" in joined
        assert "grid" in joined.casefold()
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()


def test_copy_coordinates_reports_sampled_at_full_precision():
    widget = _sized(_map())
    hrrr = _remember_frame()
    try:
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        _click(widget, (420.0, 300.0))
        text = widget.copy_inspection_coordinates()
        snapshot = widget.inspection_snapshot()
        assert f"{snapshot['sampled_lat']:.4f}" in text
        assert f"{snapshot['sampled_lon']:.4f}" in text
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()


def test_copy_value_reports_label_units_and_sampled_site():
    widget = _sized(_map())
    hrrr = _remember_frame()
    try:
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        _click(widget, (420.0, 300.0))
        text = widget.copy_inspection_value()
        assert "1500" in text
        assert "J kg" in text or "J" in text
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()


def test_copy_value_with_no_card_explains_itself():
    widget = _sized(_map())
    try:
        assert widget.inspection_snapshot() is None
        assert widget.copy_inspection_value() != ""
    finally:
        widget.close()
        widget.deleteLater()


def test_save_location_payload_uses_sampled_point():
    widget = _sized(_map())
    hrrr = _remember_frame()
    try:
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        _click(widget, (420.0, 300.0))
        payload = widget.inspection_save_payload("My field")
        assert payload["name"] == "My field"
        assert payload["lat"] == pytest.approx(
            widget.inspection_snapshot()["sampled_lat"])
        assert payload["lon"] == pytest.approx(
            widget.inspection_snapshot()["sampled_lon"])
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()


def test_save_payload_rejects_empty_name():
    widget = _sized(_map())
    hrrr = _remember_frame()
    try:
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        _click(widget, (420.0, 300.0))
        with pytest.raises(ValueError):
            widget.inspection_save_payload("   ")
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# T18.4 numeric provenance
# --------------------------------------------------------------------------- #

def test_sample_matches_rendered_frame_not_colour_lookup():
    import numpy as np

    widget = _sized(_map())
    hrrr = _remember_frame(value=2345.0)
    try:
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        lon, lat = widget._to_lonlat(420.0, 300.0)
        sample = widget.sample_field_at(float(lat), float(lon))
        assert sample.value == pytest.approx(2345.0)
        assert sample.sampled_lat != pytest.approx(float(lat)) or \
            sample.sampled_lon != pytest.approx(float(lon))
        assert sample.distance_km > 0.0
        assert "04 Sep" in hrrr.field_valid_label(RUN, 6)
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()


def test_unknown_product_reports_no_frame_not_a_number():
    widget = _sized(_map())
    try:
        widget.set_inspection_product("not-a-product", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        sample = widget.sample_field_at(35.0, -95.0)
        assert sample.value is None
        assert sample.imagery_only is True
        widget.mouseMoveEvent(_MouseEvent((420.0, 300.0)))
        assert "no numeric value" in widget.hover_inspection_text().casefold() \
            or "magery" in widget.hover_inspection_text()
    finally:
        widget.close()
        widget.deleteLater()


def test_point_outside_domain_reports_uncovered():
    widget = _sized(_map(), lon0=150.0, lon1=190.0, lat0=55.0, lat1=70.0)
    hrrr = _remember_frame()
    try:
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        sample = widget.sample_field_at(75.0, 0.0)
        assert sample.covered is False
        assert sample.value is None
        outside_lon, outside_lat = widget._to_lonlat(320.0, 240.0)
        outside = widget.sample_field_at(float(outside_lat), float(outside_lon))
        assert outside.covered is False
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()


def test_out_of_palette_range_is_reported_not_hidden():
    import numpy as np

    widget = _sized(_map())
    hrrr = _remember_frame(value=50.0)
    try:
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        _click(widget, (420.0, 300.0))
        snapshot = widget.inspection_snapshot()
        assert snapshot["value"] == pytest.approx(50.0)
        assert "drawn range" in (snapshot.get("display_note") or "")
        assert "drawn range" in "\n".join(widget.inspection_card_lines())
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()


def test_card_names_run_and_valid_time():
    widget = _sized(_map())
    hrrr = _remember_frame()
    try:
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        _click(widget, (420.0, 300.0))
        snapshot = widget.inspection_snapshot()
        assert snapshot.get("run_iso") == RUN.isoformat()
        assert snapshot.get("fxx") == 6
        assert "04 Sep" in snapshot.get("valid_label", "")
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()


def test_field_raster_without_numeric_cache_names_limitation():
    from sharpmod import hrrr_field

    widget = _sized(_map())
    widget.set_map_tool("inspect")
    try:
        from qtpy.QtCore import QBuffer, QByteArray
        from qtpy.QtGui import QColor, QImage

        image = QImage(64, 32, QImage.Format_RGBA8888)
        image.fill(QColor(255, 0, 0, 255))
        store = QByteArray()
        buffer = QBuffer(store)
        buffer.open(QBuffer.WriteOnly)
        assert image.save(buffer, "PNG")
        raster = hrrr_field.OverlayRaster(
            key=hrrr_field.OVERLAY_KEY, title="HRRR Mixed Layer CAPE",
            image_bytes=bytes(store), bounds=hrrr_field.COVERAGE_BOUNDS,
            short_name="mlcape", opacity=1.0)
        widget.set_overlay(hrrr_field.OVERLAY_KEY, raster, visible=True)
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.mouseMoveEvent(_MouseEvent((420.0, 300.0)))
        readout = widget.hover_inspection_text()
        assert "no numeric value" in readout.casefold()
    finally:
        widget.close()
        widget.deleteLater()


def test_session_restore_keeps_sounding_point_and_locks_nothing():
    widget = _sized(_map())
    try:
        widget.set_point(35.0, -95.0)
        widget.set_point_locked(True)
        first = widget.view_bounds()
        widget.zoom(0.5)
        widget.restore_view_bounds(first)
        assert widget._point_lonlat == (-95.0, 35.0)
        assert widget.has_recent_point() is False
    finally:
        widget.close()
        widget.deleteLater()


def test_history_steps_keep_sounding_point_stable():
    widget = _sized(_map())
    try:
        widget.set_point(35.0, -95.0)
        _click(widget, (420.0, 300.0))
        widget.zoom(0.5)
        widget.go_back()
        widget.go_forward()
        assert widget._point_lonlat != (-95.0, 35.0)
        widget.restore_recent_point()
        assert widget._point_lonlat == (-95.0, 35.0)
    finally:
        widget.close()
        widget.deleteLater()


def test_pinned_field_card_paints_without_error():
    from qtpy.QtGui import QPixmap

    widget = _sized(_map())
    hrrr = _remember_frame()
    try:
        widget.set_inspection_product("mlcape", run=RUN, fxx=6)
        widget.set_map_tool("inspect")
        _click(widget, (420.0, 300.0))
        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        assert not pixmap.isNull()
        assert widget.inspection_card_lines()
    finally:
        _clear_frames()
        widget.close()
        widget.deleteLater()
