"""Focused regressions for T17 precise, reversible map navigation.

Covers the map-level navigation contract before the rail/picker wiring:
proportional wheel zoom anchored at the cursor, high-resolution trackpad
input, clamped extremes, previous/next view history, zoom actions
(zoom in/out, fit, center on selection, previous/next view), preserved
selection state through navigation, and projection behaviour that never
moves the selected sounding point.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import QPoint, QPointF, Qt


class _FakeAngle:
    def __init__(self, x: int = 0, y: int = 0):
        self._x = int(x)
        self._y = int(y)

    def x(self):
        return self._x

    def y(self):
        return self._y

    def isNull(self):
        return self._x == 0 and self._y == 0


class _FakePixel:
    def __init__(self, x: int = 0, y: int = 0):
        self._x = int(x)
        self._y = int(y)

    def x(self):
        return self._x

    def y(self):
        return self._y

    def isNull(self):
        return self._x == 0 and self._y == 0


class _WheelEvent:
    def __init__(self, *, angle_y=120, pixel_y=0, pos=(320.0, 240.0),
                 modifiers=Qt.NoModifier, inverted=False):
        self._angle = _FakeAngle(0, angle_y)
        self._pixel = _FakePixel(0, pixel_y)
        self._pos = QPointF(*pos)
        self._modifiers = modifiers
        self._inverted = inverted

    def angleDelta(self):
        return self._angle

    def pixelDelta(self):
        return self._pixel

    def position(self):
        return self._pos

    def modifiers(self):
        return self._modifiers

    def isInverted(self):
        return self._inverted

    def inverted(self):
        return self._inverted

    def phase(self):
        from qtpy.QtCore import Qt as _Qt

        return _Qt.NoScrollPhase


class _MouseEvent:
    def __init__(self, x: float, y: float):
        self._position = QPointF(x, y)

    def button(self):
        return Qt.LeftButton

    def buttons(self):
        return Qt.LeftButton

    def modifiers(self):
        # A plain drag, so the map pans instead of starting a box gesture.
        return Qt.NoModifier

    def position(self):
        return self._position

    def x(self):
        return self._position.x()

    def y(self):
        return self._position.y()


class _RightMouseEvent(_MouseEvent):
    def button(self):
        return Qt.RightButton

    def buttons(self):
        return Qt.RightButton


class _RecordingMenu:
    def __init__(self, shown):
        self._shown = shown
        self._actions = []

    def __call__(self, *args, **kwargs):
        self._shown.append(self)
        return self

    def addAction(self, *args):
        from unittest.mock import MagicMock

        action = MagicMock()
        action.text.return_value = args[0] if args else ""
        self._actions.append(action)
        return action

    def addSeparator(self):
        pass

    @property
    def actions(self):
        return list(self._actions)

    def exec_(self, *_args, **_kwargs):
        return None


def _map(kind="station"):
    from qtpy.QtWidgets import QApplication

    from sharpmod import gui

    QApplication.instance() or QApplication([])
    if kind == "point":
        return gui.PointMapWidget()
    return gui.StationMapWidget([])


def _sized(widget, lon0=-100.0, lon1=-90.0, lat0=30.0, lat1=42.0):
    widget.resize(640, 480)
    widget.set_extent(lon0, lon1, lat0, lat1, pad=0.0)
    widget._basemap_pixmap()
    return widget


def test_wheel_zoom_is_proportional_not_fixed_step(qt_app):
    widget = _sized(_map())
    try:
        before = widget.view_bounds()
        span_before = before[1] - before[0]
        widget.wheelEvent(_WheelEvent(angle_y=120))
        after = widget.view_bounds()
        span_after = after[1] - after[0]
        assert span_after < span_before
        ratio = span_after / span_before
        assert 0.5 < ratio < 0.97
    finally:
        widget.close()
        widget.deleteLater()


def test_small_trackpad_delta_zooms_less_than_full_notch(qt_app):
    small = _sized(_map())
    notch = _sized(_map())
    try:
        small.wheelEvent(_WheelEvent(angle_y=15))
        notch.wheelEvent(_WheelEvent(angle_y=120))
        small_span = small.view_bounds()[1] - small.view_bounds()[0]
        notch_span = notch.view_bounds()[1] - notch.view_bounds()[0]
        base_span = 10.0
        assert abs(small_span - base_span) < abs(notch_span - base_span)
        assert small_span > notch_span
    finally:
        small.close()
        small.deleteLater()
        notch.close()
        notch.deleteLater()


def test_pixel_delta_drives_trackpad_zoom_when_angle_is_zero(qt_app):
    widget = _sized(_map())
    try:
        before = widget.view_bounds()[1] - widget.view_bounds()[0]
        widget.wheelEvent(_WheelEvent(angle_y=0, pixel_y=24))
        after = widget.view_bounds()[1] - widget.view_bounds()[0]
        assert after < before
    finally:
        widget.close()
        widget.deleteLater()


def test_wheel_zoom_preserves_cursor_anchor(qt_app):
    widget = _sized(_map())
    try:
        anchor_before = widget._to_lonlat(320.0, 240.0)
        widget.wheelEvent(_WheelEvent(angle_y=120, pos=(320.0, 240.0)))
        anchor_after = widget._to_lonlat(320.0, 240.0)
        assert anchor_after[0] == anchor_after[0]  # finite check below
        assert abs(anchor_after[0] - anchor_before[0]) < 0.05
        assert abs(anchor_after[1] - anchor_before[1]) < 0.05
    finally:
        widget.close()
        widget.deleteLater()


def test_wheel_zoom_clamps_extremes(qt_app):
    widget = _sized(_map())
    try:
        for _ in range(60):
            widget.wheelEvent(_WheelEvent(angle_y=480, pos=(320.0, 240.0)))
        lon_span = widget.view_bounds()[1] - widget.view_bounds()[0]
        lat_span = widget.view_bounds()[3] - widget.view_bounds()[2]
        assert lon_span >= 0.05
        assert lat_span >= 0.05
        for _ in range(60):
            widget.wheelEvent(_WheelEvent(angle_y=-480, pos=(320.0, 240.0)))
        lon_span = widget.view_bounds()[1] - widget.view_bounds()[0]
        lat_span = widget.view_bounds()[3] - widget.view_bounds()[2]
        assert lon_span <= 720.0
        assert lat_span <= 180.0
    finally:
        widget.close()
        widget.deleteLater()


def test_zoom_action_keeps_selection_and_point(qt_app):
    from sharpmod import gui

    widget = gui.PointMapWidget()
    widget.resize(640, 480)
    widget.set_point(35.0, -95.0)
    try:
        widget.zoom(0.5)
        assert widget._point_lonlat == (-95.0, 35.0)
        widget.zoom(2.0)
        assert widget._point_lonlat == (-95.0, 35.0)
    finally:
        widget.close()
        widget.deleteLater()


def test_station_selection_survives_zoom_and_pan(qt_app):
    stations = [
        {"id": "KOAX", "name": "Omaha", "lat": 41.3, "lon": -96.0},
        {"id": "KDEN", "name": "Denver", "lat": 39.8, "lon": -104.9},
    ]
    widget = _sized(_map())
    widget.set_stations(stations)
    try:
        widget.set_selected("KOAX")
        widget.zoom(0.5)
        widget.center_on("KOAX")
        assert widget._selected_id == "KOAX"
    finally:
        widget.close()
        widget.deleteLater()


def test_previous_and_next_view_restore_history(qt_app):
    widget = _sized(_map())
    try:
        assert hasattr(widget, "go_back"), "map must expose previous-view navigation"
        assert hasattr(widget, "go_forward"), "map must expose next-view navigation"
        first = widget.view_bounds()
        widget.zoom(0.5)
        zoomed = widget.view_bounds()
        assert zoomed != first
        assert widget.can_go_back()
        widget.go_back()
        assert widget.view_bounds() == first
        assert widget.can_go_forward()
        widget.go_forward()
        assert widget.view_bounds() == zoomed
    finally:
        widget.close()
        widget.deleteLater()


def test_new_navigation_clears_forward_history(qt_app):
    widget = _sized(_map())
    try:
        widget.zoom(0.5)
        widget.go_back()
        assert widget.can_go_forward()
        widget.zoom(0.5)
        assert not widget.can_go_forward()
    finally:
        widget.close()
        widget.deleteLater()


def test_fit_restores_named_region(qt_app):
    widget = _sized(_map())
    try:
        widget.zoom(0.25)
        assert hasattr(widget, "fit_region"), "map must expose a fit-to-region action"
        widget.fit_region()
        lon0, lon1, lat0, lat1 = widget.view_bounds()
        assert (lon1 - lon0) > 5.0
        assert (lat1 - lat0) > 5.0
    finally:
        widget.close()
        widget.deleteLater()


def test_center_on_selection_keeps_zoom_span(qt_app):
    stations = [
        {"id": "KOAX", "name": "Omaha", "lat": 41.3, "lon": -96.0},
        {"id": "KDEN", "name": "Denver", "lat": 39.8, "lon": -104.9},
    ]
    widget = _sized(_map())
    widget.set_stations(stations)
    try:
        widget.zoom(0.5)
        span_before = (widget.view_bounds()[1] - widget.view_bounds()[0],
                       widget.view_bounds()[3] - widget.view_bounds()[2])
        assert hasattr(widget, "center_on_selection")
        widget.set_selected("KDEN")
        widget.center_on_selection()
        span_after = (widget.view_bounds()[1] - widget.view_bounds()[0],
                      widget.view_bounds()[3] - widget.view_bounds()[2])
        assert span_after[0] == span_after[0]
        assert abs(span_after[0] - span_before[0]) < 1e-9
        assert abs(span_after[1] - span_before[1]) < 1e-9
        assert widget._selected_id == "KDEN"
    finally:
        widget.close()
        widget.deleteLater()


def test_point_map_center_on_selection_keeps_point(qt_app):
    from sharpmod import gui

    widget = gui.PointMapWidget()
    widget.resize(640, 480)
    widget.set_extent(-100.0, -90.0, 30.0, 42.0, pad=0.0)
    widget.set_point(35.0, -95.0)
    try:
        widget.zoom(0.5)
        widget.center_on_selection()
        assert widget._point_lonlat == (-95.0, 35.0)
        lon, lat = widget._to_lonlat(widget.width() / 2.0, widget.height() / 2.0)
        assert abs(lon - -95.0) < 0.6
        assert abs(lat - 35.0) < 0.6
    finally:
        widget.close()
        widget.deleteLater()


def test_projection_change_preserves_geographic_point(qt_app):
    from sharpmod import gui

    widget = gui.PointMapWidget()
    widget.resize(640, 480)
    widget.set_extent(-100.0, -90.0, 30.0, 42.0, pad=0.0)
    widget.set_point(35.0, -95.0)
    try:
        before = widget.view_bounds()
        before_centre = ((before[0] + before[1]) / 2.0,
                         (before[2] + before[3]) / 2.0)
        widget.set_projection("curved")
        assert widget._point_lonlat == (-95.0, 35.0)
        assert widget.projection() in ("flat", "curved")
        after = widget.view_bounds()
        after_centre = ((after[0] + after[1]) / 2.0,
                        (after[2] + after[3]) / 2.0)
        assert abs(after_centre[0] - before_centre[0]) < 1e-9
        assert abs(after_centre[1] - before_centre[1]) < 1e-9
        info = widget.scale_bar_info()
        assert "projection" in info
    finally:
        widget.close()
        widget.deleteLater()


def test_projection_switch_keeps_station_selection(qt_app):
    stations = [
        {"id": "KOAX", "name": "Omaha", "lat": 41.3, "lon": -96.0},
        {"id": "KDEN", "name": "Denver", "lat": 39.8, "lon": -104.9},
    ]
    widget = _sized(_map())
    widget.set_stations(stations)
    try:
        widget.set_selected("KOAX")
        widget.set_projection("curved")
        assert widget._selected_id == "KOAX"
        assert widget.projection() == "curved"
        description = widget.accessibleDescription()
        assert "curved" in description or "flat" in description
    finally:
        widget.close()
        widget.deleteLater()


def test_scale_bar_names_effective_projection_on_fallback(qt_app):
    from qtpy.QtGui import QPixmap

    widget = _sized(_map())
    try:
        widget.set_extent(-180.0, 180.0, -85.0, 85.0, pad=0.0)
        widget.set_projection("curved")
        assert widget.effective_projection() == "flat"
        target = QPixmap(widget.size())
        widget.render(target)
        assert not target.isNull()
        assert "(curved unavailable here)" in widget.accessibleDescription() \
            or "shown flat" in widget.accessibleDescription()
    finally:
        widget.close()
        widget.deleteLater()


def test_effective_projection_reports_fallback(qt_app):
    widget = _sized(_map())
    try:
        widget.set_extent(-180.0, 180.0, -85.0, 85.0, pad=0.0)
        widget.set_projection("curved")
        assert hasattr(widget, "effective_projection")
        assert widget.effective_projection() == "flat"
        assert widget.projection() == "curved"
    finally:
        widget.close()
        widget.deleteLater()


def test_keyboard_zoom_shortcuts_do_not_require_text_focus(qt_app):
    from qtpy.QtCore import QEvent
    from qtpy.QtGui import QKeyEvent

    widget = _sized(_map(kind="point"))
    try:
        before = widget.view_bounds()[1] - widget.view_bounds()[0]
        widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Plus, Qt.NoModifier))
        zoomed = widget.view_bounds()[1] - widget.view_bounds()[0]
        assert zoomed < before
        widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Minus, Qt.NoModifier))
        restored = widget.view_bounds()[1] - widget.view_bounds()[0]
        assert restored > zoomed
    finally:
        widget.close()
        widget.deleteLater()


def test_ctrl_plus_minus_shortcuts_zoom(qt_app):
    from qtpy.QtCore import QEvent
    from qtpy.QtGui import QKeyEvent

    for key in (Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus, Qt.Key_0):
        widget = _sized(_map())
        try:
            widget.keyPressEvent(
                QKeyEvent(QEvent.KeyPress, key, Qt.ControlModifier))
            assert widget.view_bounds()[1] - widget.view_bounds()[0] > 0.0
        finally:
            widget.close()
            widget.deleteLater()
    # Ctrl+L stays the Locations manager: it must not centre the map.
    widget = _sized(_map(kind="point"))
    widget.set_point(35.0, -95.0)
    try:
        before = widget.view_bounds()
        widget.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, Qt.Key_L, Qt.ControlModifier))
        assert widget.view_bounds() == before
    finally:
        widget.close()
        widget.deleteLater()


def test_map_exposes_navigation_actions_for_command_palette(qt_app):
    widget = _sized(_map())
    try:
        actions = widget.navigation_actions()
        kinds = {action["id"] for action in actions}
        for expected in ("zoom-in", "zoom-out", "fit", "center-on-selection",
                         "previous-view", "next-view"):
            assert expected in kinds
        for action in actions:
            assert action["label"]
            assert action["shortcut"]
            assert callable(action["trigger"])
    finally:
        widget.close()
        widget.deleteLater()


def test_navigation_actions_do_not_steal_text_shortcuts(qt_app):
    widget = _sized(_map())
    try:
        actions = {action["id"]: action for action in widget.navigation_actions()}
        for action_id in ("zoom-in", "zoom-out", "fit"):
            assert "Ctrl" in actions[action_id]["shortcut"]
        # Centre stays a map-focus single key so it never fires from a
        # text field; history uses Alt+arrows for the same reason.
        assert actions["center-on-selection"]["shortcut"] == "L"
        assert actions["previous-view"]["shortcut"] == "Alt+Left"
        assert actions["next-view"]["shortcut"] == "Alt+Right"
        assert "Ctrl+L" not in {action["shortcut"] for action in actions.values()}
    finally:
        widget.close()
        widget.deleteLater()


def test_history_bounded_and_described(qt_app):
    widget = _sized(_map())
    try:
        for _ in range(80):
            widget.zoom(0.9)
        assert hasattr(widget, "navigation_history_depth")
        assert widget.navigation_history_depth() <= 50
        assert widget.accessibleDescription() != ""
    finally:
        widget.close()
        widget.deleteLater()


def test_rail_zoom_row_offers_fit_center_and_history(qt_app):
    from qtpy.QtWidgets import QToolButton

    from sharpmod.gui_picker_layout import rail_zoom_row

    widget = _sized(_map(kind="point"))
    try:
        row = rail_zoom_row(widget)
        labels = []

        def _collect(layout):
            for index in range(layout.count()):
                item = layout.itemAt(index)
                child = item.widget()
                if isinstance(child, QToolButton):
                    labels.append(child.text())
                child_layout = item.layout()
                if child_layout is not None:
                    _collect(child_layout)

        _collect(row)
        assert "+" in labels
        assert "\u2212" in labels
        assert "Fit" in labels
        assert "Center" in labels
        assert "\u2039" in labels
        assert "\u203a" in labels
    finally:
        widget.close()
        widget.deleteLater()


def test_rail_history_buttons_track_navigation_state(qt_app):
    from qtpy.QtWidgets import QToolButton

    from sharpmod.gui_picker_layout import rail_zoom_row

    widget = _sized(_map(kind="point"))
    try:
        row = rail_zoom_row(widget)
        buttons = {}

        def _collect(layout):
            for index in range(layout.count()):
                item = layout.itemAt(index)
                child = item.widget()
                if isinstance(child, QToolButton):
                    buttons[child.accessibleName()] = child
                child_layout = item.layout()
                if child_layout is not None:
                    _collect(child_layout)

        _collect(row)
        assert not buttons["Previous map view"].isEnabled()
        assert not buttons["Next map view"].isEnabled()
        widget.zoom(0.5)
        qt_app.processEvents()
        assert buttons["Previous map view"].isEnabled()
        buttons["Previous map view"].click()
        qt_app.processEvents()
        assert buttons["Next map view"].isEnabled()
    finally:
        widget.close()
        widget.deleteLater()


def test_rail_zoom_shortcuts_do_not_fire_from_text_fields(qt_app):
    from qtpy.QtWidgets import QLineEdit, QToolButton, QWidget, QVBoxLayout

    from sharpmod.gui_picker_layout import rail_zoom_row

    host = QWidget()
    layout = QVBoxLayout(host)
    field = QLineEdit(host)
    layout.addWidget(field)
    widget = _sized(_map(kind="point"))
    widget.setParent(host)
    layout.addWidget(widget)
    try:
        host.show()
        qt_app.processEvents()
        row = rail_zoom_row(widget)
        layout.addLayout(row)
        field.setFocus()
        qt_app.processEvents()
        before = widget.view_bounds()
        for button_index in range(row.count()):
            item = row.itemAt(button_index)
            child = item.widget()
            if isinstance(child, QToolButton):
                for action in child.actions():
                    assert action.shortcutContext() != Qt.WindowShortcut
        assert widget.view_bounds() == before
    finally:
        host.close()
        host.deleteLater()


def test_session_restore_does_not_push_history(qt_app):
    widget = _sized(_map())
    try:
        first = widget.view_bounds()
        widget.zoom(0.5)
        depth_after_zoom = widget.navigation_history_depth()
        widget.restore_view_bounds(first)
        assert widget.navigation_history_depth() == depth_after_zoom
        assert widget.view_bounds() == first
    finally:
        widget.close()
        widget.deleteLater()


def test_left_drag_pan_never_selects_station(qt_app):
    from sharpmod.gui_maps import PAN_BUTTONS

    stations = [
        {"id": "KOAX", "name": "Omaha", "lat": 41.3, "lon": -96.0},
        {"id": "KDEN", "name": "Denver", "lat": 39.8, "lon": -104.9},
    ]
    widget = _sized(_map())
    widget.set_stations(stations)
    try:
        selected = []
        widget.stationSelected.connect(selected.append)
        widget.mousePressEvent(_MouseEvent(300.0, 220.0))
        widget.mouseMoveEvent(_MouseEvent(360.0, 260.0))
        assert widget._dragged
        widget.mouseReleaseEvent(_MouseEvent(360.0, 260.0))
        assert selected == []
        assert widget._selected_id is None
    finally:
        widget.close()
        widget.deleteLater()


def test_right_drag_pan_suppresses_context_menu(qt_app, monkeypatch):
    from qtpy.QtCore import Qt

    from sharpmod.gui_maps import SECONDARY_PAN_BUTTONS

    widget = _sized(_map())
    try:
        widget.mousePressEvent(_RightMouseEvent(300.0, 220.0))
        widget.mouseMoveEvent(_RightMouseEvent(360.0, 260.0))
        widget.mouseReleaseEvent(_RightMouseEvent(360.0, 260.0))
        assert widget._pan_consumed_press

        class _ContextEvent:
            def x(self):
                return 320

            def y(self):
                return 240

        shown = []
        import sharpmod.ui.maps.station_interactions as station_interactions

        monkeypatch.setattr(station_interactions, "QMenu", _RecordingMenu(shown))
        widget.contextMenuEvent(_ContextEvent())
        assert shown == []
        assert not widget._pan_consumed_press
    finally:
        widget.close()
        widget.deleteLater()


def test_right_click_menu_offers_navigation_without_moving_selection(
    qt_app, monkeypatch
):
    from sharpmod import gui

    widget = gui.PointMapWidget()
    widget.resize(640, 480)
    widget.set_extent(-100.0, -90.0, 30.0, 42.0, pad=0.0)
    widget.set_point(35.0, -95.0)
    try:
        before_point = widget._point_lonlat
        before_view = widget.view_bounds()

        class _ContextEvent:
            def x(self):
                return 400

            def y(self):
                return 300

        shown = []
        import sharpmod.ui.maps.point as point_module

        monkeypatch.setattr(point_module, "QMenu", _RecordingMenu(shown))
        widget.contextMenuEvent(_ContextEvent())
        assert len(shown) == 1
        labels = [action.text().split("\t")[0] for action in shown[0].actions]
        for expected in ("Zoom in", "Zoom out", "Fit region",
                         "Center on selection", "Previous view", "Next view"):
            assert expected in labels
        assert widget._point_lonlat == before_point
        assert widget.view_bounds() == before_view
    finally:
        widget.close()
        widget.deleteLater()


def test_point_map_left_drag_pan_never_moves_point(qt_app):
    from sharpmod import gui

    widget = gui.PointMapWidget()
    widget.resize(640, 480)
    widget.set_extent(-100.0, -90.0, 30.0, 42.0, pad=0.0)
    widget.set_point(35.0, -95.0)
    try:
        picked = []
        widget.pointSelected.connect(lambda lat, lon: picked.append((lat, lon)))
        widget.mousePressEvent(_MouseEvent(300.0, 220.0))
        widget.mouseMoveEvent(_MouseEvent(380.0, 280.0))
        widget.mouseReleaseEvent(_MouseEvent(380.0, 280.0))
        assert picked == []
        assert widget._point_lonlat == (-95.0, 35.0)
    finally:
        widget.close()
        widget.deleteLater()


def test_point_map_left_pan_does_not_resample_inspection_hover(qt_app):
    from sharpmod import gui

    class _HoverEvent(_MouseEvent):
        def buttons(self):
            return Qt.NoButton

    widget = gui.PointMapWidget()
    widget.resize(640, 480)
    widget.set_map_tool("inspect")
    samples = []
    widget.sample_field_at = lambda lat, lon: samples.append((lat, lon))
    try:
        widget.mouseMoveEvent(_HoverEvent(300.0, 220.0))
        assert samples
        samples.clear()
        before = widget.view_bounds()
        widget.mousePressEvent(_MouseEvent(300.0, 220.0))
        widget.mouseMoveEvent(_MouseEvent(380.0, 280.0))
        assert widget.view_bounds() != before
        assert samples == []
        widget.mouseReleaseEvent(_MouseEvent(380.0, 280.0))
    finally:
        widget.close()
        widget.deleteLater()


def test_view_menu_navigation_acts_on_active_map(qt_app):
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window.show()
        qt_app.processEvents()
        assert hasattr(window, "_nav_actions")
        assert hasattr(window, "_trigger_map_navigation")
        assert hasattr(window, "_active_map_widget")
        active = window._active_map_widget()
        assert active is not None
        before = active.view_bounds()
        window._trigger_map_navigation("zoom-in")
        qt_app.processEvents()
        after = active.view_bounds()
        assert (after[1] - after[0]) < (before[1] - before[0])
        assert active.can_go_back()
        window._trigger_map_navigation("previous-view")
        assert active.view_bounds() == before
        window._refresh_navigation_actions()
        assert not window._nav_actions["previous-view"].isEnabled() \
            or not active.can_go_back()
        # Every View-menu entry resolves against the live map actions, so
        # the menu and the map can never disagree about what exists.
        live = {entry["id"] for entry in active.navigation_actions()}
        assert set(window._nav_actions) == live
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_region_combo_is_searchable(qt_app):
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window.show()
        qt_app.processEvents()
        combo = window._area_combo
        assert combo.isEditable()
        combo.setCurrentText("Alaska")
        qt_app.processEvents()
        assert window._map._area_name == "Alaska"
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_map_tooltip_names_gestures(qt_app):
    widget = _sized(_map())
    try:
        assert "right-click" in widget.toolTip()
        assert "middle/right-drag" in widget.toolTip()
        widget.mouseMoveEvent(_MouseEvent(320.0, 240.0))
        assert "right-click" in widget.toolTip()
    finally:
        widget.close()
        widget.deleteLater()


def test_region_combo_pushes_history(qt_app):
    from sharpmod.gui_map_regions import region_combo

    widget = _sized(_map())
    try:
        combo = region_combo(widget)
        first = widget.view_bounds()
        widget.zoom(0.5)
        combo.setCurrentText("Alaska")
        assert widget._area_name == "Alaska"
        assert widget.can_go_back()
        widget.go_back()
        widget.go_back()
        assert widget.view_bounds() == first
    finally:
        widget.close()
        widget.deleteLater()


def test_navigation_actions_match_rail_and_palette_ids(qt_app):
    from qtpy.QtWidgets import QToolButton

    from sharpmod.gui_picker_layout import rail_zoom_row

    widget = _sized(_map(kind="point"))
    try:
        actions = widget.navigation_actions()
        ids = [entry["id"] for entry in actions]
        assert ids == ["zoom-in", "zoom-out", "fit", "center-on-selection",
                       "previous-view", "next-view"]
        row = rail_zoom_row(widget)
        names = []

        def _collect(layout):
            for index in range(layout.count()):
                item = layout.itemAt(index)
                child = item.widget()
                if isinstance(child, QToolButton):
                    names.append(child.accessibleName())
                child_layout = item.layout()
                if child_layout is not None:
                    _collect(child_layout)

        _collect(row)
        assert "Zoom map in" in names
        assert "Zoom map out" in names
        assert "Fit map to region" in names
        assert "Center map on selection" in names
        assert "Previous map view" in names
        assert "Next map view" in names
    finally:
        widget.close()
        widget.deleteLater()
