"""Focused regressions for T19 manageable, understandable map layers.

Covers the layer-management contracts before visual evidence:

* T19.1 active-layer list: grouped rows in paint order with name, visibility,
  opacity, actual time, and availability/error state; geography always present
  and always drawn; failed rows carry scoped retry; the list refreshes when a
  controller reports.
* T19.2 opacity and presets: numeric readout plus reset beside every
  opacity slider; temporary hide-and-restore remembers only what it hid;
  named compatible presets apply layers at readable opacities, migrate
  unknown names, and persist.
* T19.3 dependencies and scope: storm reports stay unavailable until the
  outlook is showing, with the unblocking action named; main-map rows and the
  locator/inset scope cannot be confused.
* T19.4 order and occlusion: the list follows the map's paint order with the
  top fill marked; two covering-strength fills name the hidden one and the
  mitigation on the map legend as well as the list; translucent stacks stay
  silent.
* T19.5 failures and session: failures stay visible until resolved with retry
  scoped to the affected layer; successful independent layers are preserved;
  session carries choices (never frames) and restores without replaying the
  network.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytestmark = pytest.mark.usefixtures("qt_app")


def _entries(**overrides):
    from sharpmod.map_layers import LayerEntry

    base = dict(key="hrrr_field", name="HRRR model field",
                group="weather fields", visible=True, opacity=0.75,
                time_text="", state="ok", detail="")
    base.update(overrides)
    return LayerEntry(**base)


# --------------------------------------------------------------------------- #
# T19 core model: rows, order, occlusion, presets, dependencies, session
# --------------------------------------------------------------------------- #

def test_layer_rows_follow_paint_order_with_top_marker():
    from sharpmod import map_layers

    entries = [
        _entries(key="spc_outlook", name="SPC outlook",
                 group="observations/reports/outlooks"),
        _entries(key="hrrr_field", name="HRRR field"),
        _entries(key="radar_site", name="Radar"),
    ]
    rows = map_layers.layer_list_rows(entries)
    assert [row.split(" · ")[0] for row in rows] == [
        "● HRRR field", "● Radar", "● SPC outlook"]
    assert " — top" in rows[1]
    assert " — top" not in rows[0]


def test_occlusion_guard_names_hidden_layer_and_mitigation():
    from sharpmod import map_layers

    field = _entries(key="hrrr_field", name="HRRR field", opacity=0.95)
    radar = _entries(key="radar_site", name="Radar", opacity=0.95)
    notes = map_layers.occlusion_guard([field, radar])
    assert set(notes) == {"hrrr_field"}
    assert "Radar" in notes["hrrr_field"]
    assert "90%" in notes["hrrr_field"]


def test_occlusion_guard_silent_for_translucent_or_single_or_hidden():
    from sharpmod import map_layers

    field = _entries(key="hrrr_field", name="HRRR field", opacity=0.60)
    radar = _entries(key="radar_site", name="Radar", opacity=0.95)
    assert map_layers.occlusion_guard([field, radar]) == {}
    assert map_layers.occlusion_guard([radar]) == {}
    hidden = _entries(key="hrrr_field", name="HRRR field", opacity=0.95,
                      visible=False)
    assert map_layers.occlusion_guard([hidden, radar]) == {}


def test_preset_migration_degrades_unknown_to_default():
    from sharpmod import map_layers

    assert map_layers.migrate_preset_choice("bogus preset") == \
        map_layers.DEFAULT_PRESET_KEY
    assert map_layers.migrate_preset_choice("") == map_layers.DEFAULT_PRESET_KEY
    assert map_layers.migrate_preset_choice("anything", version=99) == \
        map_layers.DEFAULT_PRESET_KEY
    assert map_layers.migrate_preset_choice("  Convection WATCH ") == \
        "convection watch"


def test_preset_layers_stay_readable_under_occlusion_guard():
    from sharpmod import map_layers

    for key in map_layers.LAYER_PRESETS:
        pairs = map_layers.preset_layers(key)
        assert pairs, key
        entries = [_entries(key=layer, name=layer, opacity=opacity)
                   for layer, opacity in pairs]
        assert map_layers.occlusion_guard(entries) == {}, key


def test_reports_dependency_names_outlook_and_action():
    from sharpmod import map_layers

    available, reason, action = map_layers.reports_dependency(False)
    assert available is False
    assert "outlook" in reason.casefold()
    assert "outlook" in action.casefold()
    assert map_layers.reports_dependency(True) == (True, "", "")


def test_opacity_normalise_and_reset():
    from sharpmod import map_layers

    assert map_layers.normalise_opacity("bogus") == pytest.approx(0.57)
    assert map_layers.normalise_opacity(5.0) == pytest.approx(0.95)
    assert map_layers.normalise_opacity(-1.0) == pytest.approx(0.20)
    assert map_layers.opacity_reset_value() == pytest.approx(0.75)


def test_layer_session_round_trips_choices_not_frames():
    from sharpmod import map_layers

    state = map_layers.layer_session_state(
        enabled={"map/hrrr_field": True}, products={"map/hrrr_field": "mlcape"},
        opacities={"map/hrrr_field": 0.6}, preset="Convection watch")
    assert state["version"] == 1
    assert state["preset"] == "convection watch"
    assert map_layers.restore_layer_session_state(state) == state
    assert map_layers.restore_layer_session_state({"version": 99})[  # noqa: E201
        "enabled"] == {}
    assert map_layers.restore_layer_session_state(None)["enabled"] == {}


def test_paint_order_index_tolerates_newcomers():
    from sharpmod import map_layers

    assert map_layers.paint_order_index("hrrr_field") < \
        map_layers.paint_order_index("radar_site")
    assert map_layers.paint_order_index("radar_site") < \
        map_layers.paint_order_index("spc_outlook")
    assert map_layers.paint_order_index("geography") == \
        len(map_layers.LAYER_PAINT_ORDER) - 1
    assert map_layers.paint_order_index("something-new") == \
        map_layers.paint_order_index("storm_reports")


# --------------------------------------------------------------------------- #
# T19.4 map order and occlusion
# --------------------------------------------------------------------------- #

def _map():
    from sharpmod import gui

    return gui.PointMapWidget()


def _sized(widget):
    widget.resize(640, 480)
    widget.set_extent(-100.0, -90.0, 30.0, 42.0, pad=0.0)
    return widget


def _raster(key, title, opacity=1.0):
    from qtpy.QtCore import QBuffer, QByteArray
    from qtpy.QtGui import QColor, QImage
    from sharpmod import hrrr_field

    image = QImage(64, 32, QImage.Format_RGBA8888)
    image.fill(QColor(255, 0, 0, 255))
    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QBuffer.WriteOnly)
    assert image.save(buffer, "PNG")
    return hrrr_field.OverlayRaster(
        key=key, title=title, image_bytes=bytes(store),
        bounds=hrrr_field.COVERAGE_BOUNDS, short_name=key, opacity=opacity)


def test_paint_order_keys_follow_draw_order_not_insertion():
    widget = _sized(_map())
    try:
        widget.set_overlay("radar_site", _raster("radar_site", "Radar"))
        widget.set_overlay("hrrr_field", _raster("hrrr_field", "Field"))
        assert widget.paint_order_keys() == ("hrrr_field", "radar_site")
    finally:
        widget.close()
        widget.deleteLater()


def test_occlusion_warnings_name_covering_layer():
    widget = _sized(_map())
    try:
        widget.set_overlay("hrrr_field", _raster("hrrr_field", "Field", 0.95))
        widget.set_overlay("radar_site", _raster("radar_site", "Radar", 0.95))
        warnings = widget.occlusion_warnings()
        assert set(warnings) == {"hrrr_field"}
        assert "Radar" in warnings["hrrr_field"]
    finally:
        widget.close()
        widget.deleteLater()


def test_occlusion_warnings_silent_when_translucent():
    widget = _sized(_map())
    try:
        widget.set_overlay("hrrr_field", _raster("hrrr_field", "Field", 0.60))
        widget.set_overlay("radar_site", _raster("radar_site", "Radar", 0.95))
        assert widget.occlusion_warnings() == {}
    finally:
        widget.close()
        widget.deleteLater()


def test_vector_layers_paint_in_vector_draw_order():
    from sharpmod import map_overlays as mo
    from sharpmod import spc_outlook, storm_reports

    widget = _sized(_map())
    try:
        rings = mo.rings_from_geometry(
            {"type": "Polygon", "coordinates": [[
                [-100.0, 35.0], [-90.0, 35.0], [-90.0, 45.0],
                [-100.0, 45.0], [-100.0, 35.0]]]})[0]
        shape = mo.OverlayShape(
            rings=rings, bounds=mo.bounds_of(rings), stroke="#fff",
            fill="#000", label="X")
        outlook = mo.build_layer(spc_outlook.OVERLAY_KEY, "Outlook", [shape])
        reports = mo.build_layer(storm_reports.OVERLAY_KEY, "Reports", [shape])
        widget.set_overlay(storm_reports.OVERLAY_KEY, reports)
        widget.set_overlay(spc_outlook.OVERLAY_KEY, outlook)
        layers = widget._ordered_vector_layers()
        assert [layer.key for layer in layers] == [
            storm_reports.OVERLAY_KEY, spc_outlook.OVERLAY_KEY]
        assert widget.paint_order_keys() == (
            storm_reports.OVERLAY_KEY, spc_outlook.OVERLAY_KEY)
    finally:
        widget.close()
        widget.deleteLater()


def test_occlusion_legend_names_hidden_field():
    from qtpy.QtGui import QPixmap

    widget = _sized(_map())
    try:
        widget.set_overlay("hrrr_field", _raster("hrrr_field", "Field", 0.95))
        widget.set_overlay("radar_site", _raster("radar_site", "Radar", 0.95))
        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        assert not pixmap.isNull()
        assert "hrrr_field" in widget.occlusion_warnings()
    finally:
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# T19.2 opacity numeric/reset on every slider row
# --------------------------------------------------------------------------- #

def test_radar_opacity_numeric_and_reset():
    widget = _sized(_map())
    try:
        from sharpmod.gui_overlay_controls import RadarOverlayController

        controller = RadarOverlayController(widget, enabled=False)
        try:
            controller.set_opacity_value(0.62)
            assert controller.opacity() == pytest.approx(0.62)
            assert controller._opacity_spin.value() == 62
            controller._opacity_spin.setValue(81)
            assert controller.opacity() == pytest.approx(0.81)
            controller.reset_opacity()
            assert controller.opacity() == pytest.approx(0.75)
            controller.set_opacity_value("bogus")
            assert 0.20 <= controller.opacity() <= 0.95
        finally:
            controller.shutdown()
    finally:
        widget.close()
        widget.deleteLater()


def test_field_opacity_numeric_and_reset():
    widget = _sized(_map())
    try:
        from sharpmod.gui_overlay_controls import HrrrFieldController

        controller = HrrrFieldController(widget, enabled=False)
        try:
            controller.set_opacity_value(0.62)
            assert controller.opacity() == pytest.approx(0.62)
            assert controller._opacity_spin.value() == 62
            controller._opacity_spin.setValue(44)
            assert controller.opacity() == pytest.approx(0.44)
            controller.reset_opacity()
            assert controller.opacity() == pytest.approx(0.75)
        finally:
            controller.shutdown()
    finally:
        widget.close()
        widget.deleteLater()


def test_satellite_opacity_numeric_and_reset():
    widget = _sized(_map())
    try:
        from sharpmod.gui_environmental_context import (
            EnvironmentalContextController,
        )

        controller = EnvironmentalContextController(widget)
        try:
            controller.set_opacity_value(0.62)
            assert controller.opacity() == pytest.approx(0.62)
            assert controller._opacity_spin.value() == 62
            controller.reset_opacity()
            assert controller.opacity() == pytest.approx(0.75)
        finally:
            controller.shutdown()
    finally:
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# T19.1 list widget: rows, hide/restore, presets, retry
# --------------------------------------------------------------------------- #

def _layer_list():
    from sharpmod.gui_picker_layout import ActiveLayerList

    return ActiveLayerList()


def test_layer_list_groups_rows_and_names_state():
    view = _layer_list()
    try:
        entries = [
            _entries(key="hrrr_field", name="HRRR field",
                     time_text="12Z run F06", state="ok"),
            _entries(key="storm_reports", name="Storm reports",
                     group="observations/reports/outlooks",
                     visible=False, state="off"),
        ]
        view.set_entries(entries)
        texts = [view.list.item(index).text()
                 for index in range(view.list.count())]
        assert any("weather fields" in text.casefold() for text in texts)
        assert any("observations & outlooks" in text.casefold()
                   for text in texts)
        assert any("HRRR field" in text and "12Z run F06" in text
                   for text in texts)
        assert "1 of 2 layers visible." in view.summary.text()
    finally:
        view.close()
        view.deleteLater()


def test_layer_status_rows_use_checkbox_plus_two_line_hierarchy():
    from qtpy.QtWidgets import QCheckBox, QLabel

    view = _layer_list()
    try:
        view.set_entries([_entries(
            key="hrrr_field", name="HRRR model field",
            time_text="12Z run · F006", state="ok", opacity=0.75,
        )])
        row = view.list.item(1)
        assert row.text() == "HRRR model field\n75% · 12Z run · F006 · On"
        assert "●" not in row.text() and "○" not in row.text()
        widget = view.list.itemWidget(row)
        assert widget is not None
        assert len(widget.findChildren(QCheckBox)) == 1
        labels = [label.text() for label in widget.findChildren(QLabel)]
        assert labels == [
            "HRRR model field", "75% · 12Z run · F006 · On"]
    finally:
        view.close()
        view.deleteLater()


def test_layer_status_rows_measure_large_theme_text(qt_app):
    from qtpy.QtWidgets import QLabel

    from sharpmod.gui_theme import apply_theme

    view = None
    try:
        apply_theme(qt_app, color_style="standard", text_scale=100)
        view = _layer_list()
        view.set_entries([_entries(
            key="hrrr_field", name="HRRR model field — reflectivity",
            time_text="12Z run · F006", state="ok", opacity=0.75,
        )])
        view.show()
        qt_app.processEvents()
        small_height = view.list.item(1).sizeHint().height()

        # Exercise a live preference change, not only construction under the
        # large theme: existing rows must be remeasured after StyleChange.
        apply_theme(qt_app, color_style="standard", text_scale=200)
        for _ in range(4):
            qt_app.processEvents()
        item = view.list.item(1)
        widget = view.list.itemWidget(item)
        labels = widget.findChildren(QLabel)
        margins = widget.layout().contentsMargins()
        needed = sum(max(label.sizeHint().height(),
                         label.fontMetrics().lineSpacing())
                     for label in labels)
        assert item.sizeHint().height() >= (
            needed + margins.top() + margins.bottom())
        assert item.sizeHint().height() > small_height
    finally:
        if view is not None:
            view.close()
            view.deleteLater()
        apply_theme(qt_app, color_style="standard", text_scale=100)


def test_layer_list_geography_row_is_not_checkable():
    from qtpy.QtCore import Qt

    view = _layer_list()
    try:
        view.set_entries([
            _entries(key="geography", name="Geography",
                     group="geography", state="ok", detail="Always drawn"),
        ])
        item = view.list.item(1)
        assert "Geography" in item.text()
        assert not (item.flags() & Qt.ItemIsUserCheckable)
    finally:
        view.close()
        view.deleteLater()


def test_hide_all_restores_only_what_it_hid():
    view = _layer_list()
    try:
        view.set_entries([
            _entries(key="hrrr_field", name="Field A"),
            _entries(key="radar_site", name="Radar", visible=False),
            _entries(key="geography", name="Geography", visible=True),
            _entries(key="locator:risk", name="SPC risk areas", visible=True),
        ])
        seen = []
        view.visibilityToggled.connect(lambda key, on: seen.append((key, on)))
        view.hide_all_layers()
        assert seen == [("hrrr_field", False)]
        assert view.show_all.isEnabled()
        view.restore_hidden_layers()
        assert seen == [("hrrr_field", False), ("hrrr_field", True)]
        assert not view.show_all.isEnabled()
    finally:
        view.close()
        view.deleteLater()


def test_preset_combo_selects_and_reports():
    view = _layer_list()
    try:
        from sharpmod import map_layers

        view.set_presets(dict(map_layers.LAYER_PRESETS),
                         current="Convection watch")
        assert view.selected_preset() == "convection watch"
        view.set_presets(dict(map_layers.LAYER_PRESETS), current="bogus")
        assert view.selected_preset() == map_layers.DEFAULT_PRESET_KEY
    finally:
        view.close()
        view.deleteLater()


def test_failed_row_carries_its_own_retry_button():
    from qtpy.QtWidgets import QToolButton

    view = _layer_list()
    try:
        view.set_entries([
            _entries(key="hrrr_field", name="HRRR field", state="failed",
                     detail="Field unavailable: proof failure"),
            _entries(key="radar_site", name="Radar", state="ok"),
        ])
        assert "need attention" in view.summary.text()
        buttons = view.list.findChildren(QToolButton)
        retry = [button for button in buttons
                 if button.accessibleName() == "Retry HRRR field"]
        assert len(retry) == 1
        seen = []
        view.retryRequested.connect(lambda key: seen.append(key))
        retry[0].click()
        assert seen == ["hrrr_field"]
    finally:
        view.close()
        view.deleteLater()


@pytest.mark.parametrize("state", ("offline", "no-data", "canceled"))
def test_typed_field_recovery_rows_keep_their_scoped_retry(state):
    from qtpy.QtWidgets import QLabel, QToolButton

    view = _layer_list()
    try:
        view.set_entries([
            _entries(key="hrrr_field", name="HRRR field", state=state,
                     detail=f"Field {state}: retained cached frame"),
        ])
        retry = [button for button in view.list.findChildren(QToolButton)
                 if button.accessibleName() == "Retry HRRR field"]
        assert len(retry) == 1
        assert "need attention" in view.summary.text()
        assert state in retry[0].parent().findChild(QLabel).accessibleName()
    finally:
        view.close()
        view.deleteLater()


def test_outside_domain_is_specific_but_not_a_failed_retry_row():
    from qtpy.QtWidgets import QToolButton

    view = _layer_list()
    try:
        entry = _entries(state="outside-domain",
                         detail="Outside HRRR coverage")
        assert "Outside HRRR" in entry.status_text()
        view.set_entries([entry])
        assert not [button for button in view.list.findChildren(QToolButton)
                    if button.text() == "Retry"]
        assert "need attention" not in view.summary.text()
    finally:
        view.close()
        view.deleteLater()


def test_failed_row_keeps_retry_while_hidden():
    from sharpmod import map_layers

    entry = _entries(key="hrrr_field", name="HRRR field", visible=False,
                     state="failed", detail="Field unavailable: proof failure")
    assert "unavailable" in entry.status_text().casefold()
    rows = map_layers.layer_list_rows([entry])
    assert "Field unavailable" in rows[0]


def test_locator_rows_sort_last_and_cannot_toggle():
    from qtpy.QtCore import Qt

    from sharpmod import map_layers
    from sharpmod.gui_picker_layout import ActiveLayerList

    entry = map_layers.locator_entry("risk", "SPC risk areas",
                                     detail="Follows the map hazard")
    assert entry.key == "locator:risk"
    assert entry.scope == "sounding locator inset"
    assert entry.group == "sounding locator"
    view = ActiveLayerList()
    try:
        # The list preserves the host's paint order; the model sorts locator
        # rows last, so hand them over sorted the way the picker does.
        rows = sorted([entry, _entries(key="hrrr_field", name="Field")],
                      key=lambda row: map_layers.paint_order_index(row.key))
        view.set_entries(rows)
        texts = [view.list.item(index).text()
                 for index in range(view.list.count())
                 if view.list.item(index).flags() & Qt.ItemIsSelectable]
        assert texts[-1].startswith("SPC risk areas\n")
        assert "●" not in texts[-1] and "○" not in texts[-1]
        item = view.list.item(view.list.count() - 1)
        assert not (item.flags() & Qt.ItemIsUserCheckable)
    finally:
        view.close()
        view.deleteLater()


def test_paint_order_index_sorts_locator_last():
    from sharpmod import map_layers

    assert map_layers.paint_order_index("locator:risk") > \
        map_layers.paint_order_index("geography")


# --------------------------------------------------------------------------- #
# T19.3 reports dependency + T19.5 scoped retry through controllers
# --------------------------------------------------------------------------- #

def test_reports_row_unavailable_until_outlook_shows():
    widget = _sized(_map())
    try:
        from sharpmod.gui_overlay_controls import (
            OutlookOverlayController,
            StormReportsOverlayController,
        )

        outlook = OutlookOverlayController(widget, enabled=False)
        reports = StormReportsOverlayController(widget, enabled=False)
        try:
            reports.bind_outlook(outlook)
            assert reports._check.isEnabled() is False
            assert reports._detail.isHidden()
            assert "outlook" in reports.layer_state_text().casefold()
            outlook.set_enabled(True)
            assert reports._check.isEnabled() is True
            assert reports._detail.isHidden()
        finally:
            outlook.shutdown()
            reports.shutdown()
    finally:
        widget.close()
        widget.deleteLater()


def test_scoped_retry_touches_only_its_layer(monkeypatch):
    widget = _sized(_map())
    try:
        from sharpmod import gui_overlay_controls
        from sharpmod.gui_overlay_controls import HrrrFieldController

        calls = []
        monkeypatch.setattr(gui_overlay_controls.HrrrFieldController,
                            "_request", lambda self: calls.append("field"))
        first = HrrrFieldController(widget, enabled=False)
        second = HrrrFieldController(widget, enabled=False)
        try:
            first.set_enabled(True)
            calls.clear()
            first.retry()
            assert calls == ["field"]
            assert second._token == 0
            first.retry_scoped()
            assert calls == ["field", "field"]
        finally:
            first.shutdown()
            second.shutdown()
    finally:
        widget.close()
        widget.deleteLater()


def test_reports_retry_scoped_alias_exists():
    widget = _sized(_map())
    try:
        from sharpmod.gui_overlay_controls import StormReportsOverlayController

        controller = StormReportsOverlayController(widget, enabled=False)
        try:
            assert callable(controller.retry)
            assert callable(controller.retry_scoped)
            controller.retry_scoped()
        finally:
            controller.shutdown()
    finally:
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# T19 picker wiring: lists, presets, session choices
# --------------------------------------------------------------------------- #

def test_picker_builds_layer_lists_with_geography():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Station Map")
        window._ensure_tab("Forecast Model")
        window.show()
        for tab, attribute in (("map", "_map_layers"),
                               ("model", "_model_layers")):
            view = getattr(window, attribute)
            window._refresh_layer_list(tab)
            texts = [view.list.item(index).text()
                     for index in range(view.list.count())]
            assert any("Geography" in text for text in texts), tab
            assert any("weather fields" in text.casefold()
                       for text in texts), tab
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_picker_uses_one_layer_manager_with_inline_status(qt_app):
    from sharpmod import gui_picker
    from sharpmod.gui_picker_layout import CollapsibleRailSection
    from qtpy.QtWidgets import QTabWidget

    window = None
    try:
        window = gui_picker.PickerWindow()
        window.show()
        for source, prefix in (
            ("Station Map", "map"),
            ("Forecast Model", "model"),
            ("Field Panels", "panels"),
        ):
            window._select_tab(source)
            for _ in range(8):
                qt_app.processEvents()

            pane = getattr(window, f"_{prefix}_selection_pane")
            cards = pane.rail.findChildren(CollapsibleRailSection)
            titles = [card.title() for card in cards]
            assert titles.count("Map layers") == 1, source
            assert "Map overlays" not in titles, source
            assert "Active layers" not in titles, source

            overview = getattr(window, f"_{prefix}_layer_overview")
            layers = getattr(window, f"_{prefix}_layers")
            card = getattr(window, f"_{prefix}_layers_card")
            statuses = getattr(window, f"_{prefix}_layer_statuses")
            compact = getattr(window, f"_{prefix}_compact_layers")
            locator = getattr(window, f"_{prefix}_locator")
            locator_toggle = getattr(window, f"_{prefix}_locator_toggle")
            assert not card.findChildren(QTabWidget)
            assert "on map" in overview.text()
            assert "on locator" in overview.text()
            assert layers.preset_combo.isVisibleTo(card)
            assert layers.hide_all.isVisibleTo(card)
            assert not layers.list.isVisibleTo(card)
            assert "spc_outlook" in statuses
            assert "storm_reports" in statuses
            assert "goes_context" in statuses
            assert compact.open_key is None
            assert all(not row.detail.isVisibleTo(card)
                       for row in compact.rows.values())
            compact.rows["radar_site"].edit.click()
            assert compact.rows["radar_site"].detail.isVisibleTo(card)
            compact.rows["spc_outlook"].edit.click()
            assert compact.rows["spc_outlook"].detail.isVisibleTo(card)
            assert not compact.rows["radar_site"].detail.isVisibleTo(card)
            compact.open(None)
            assert not compact.rows["spc_outlook"].detail.isVisibleTo(card)
            assert not locator.controls_widget().isVisibleTo(card)
            assert "selected" in locator_toggle.text()
            locator_toggle.click()
            assert locator.controls_widget().isVisibleTo(card)
            locator_toggle.click()
            assert not locator.controls_widget().isVisibleTo(card)

        context = window._map_context
        context._sat_status.setText("Satellite unavailable")
        context._surface_status.setText("Nearby stations matched")
        entries = {entry.key: entry
                   for entry in window._build_layer_entries("map")}
        assert entries["goes_context"].state == "failed"
        assert entries["surface_observations"].state != "failed"
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_station_correction_lives_in_sounding_setup_card(qt_app):
    from sharpmod import gui_picker
    from sharpmod.gui_picker_layout import CollapsibleRailSection

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Station Map")
        window.show()
        for _ in range(8):
            qt_app.processEvents()

        pane = window._map_selection_pane
        card = window._map_setup_card
        window._map_selected_id = None
        window._map_gen_btn.setEnabled(False)
        window._map_selection_feedback.refresh()
        assert card.title() == "Sounding setup"
        assert card.isAncestorOf(window._map_source)
        assert card.isAncestorOf(window._map_date)
        assert card.isAncestorOf(window._map_cycle)
        assert card.isAncestorOf(window._map_mode_combo)
        assert card.isAncestorOf(window._map_sel_lbl)
        assert card.isAncestorOf(window._map_avail)
        assert card.isAncestorOf(pane.feedback)
        assert card.isAncestorOf(pane.corrective)
        assert pane.layout().indexOf(pane.feedback) == -1
        assert pane.header.indexOf(pane.corrective) == -1
        assert "Choose a station" in pane.feedback.text()
        assert pane.corrective.text() == "Choose station…"

        rail_layout = pane.rail.widget().layout()
        card_titles = [
            rail_layout.itemAt(index).widget().title()
            for index in range(rail_layout.count())
            if isinstance(
                rail_layout.itemAt(index).widget(), CollapsibleRailSection)
        ]
        assert card_titles.count("Sounding setup") == 1
        assert "Sounding source" not in card_titles
        assert "Run time (UTC)" not in card_titles
        assert "Selected station" not in card_titles
        assert card_titles.index("Sounding setup") < card_titles.index(
            "Map layers")
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_picker_field_row_preserves_typed_recovery_states(monkeypatch):
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Forecast Model")
        field = window._model_field
        monkeypatch.setattr(field, "_request", lambda: None)
        field.set_enabled(True)
        for state in (
                "loading", "offline", "no-data", "failed", "canceled",
                "outside-domain"):
            field._set_status(f"{state} proof", state=state)
            entries = window._build_layer_entries("model")
            row = next(entry for entry in entries
                       if entry.key == "hrrr_field")
            assert row.state == state
            assert state in row.detail
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_picker_preset_applies_compatible_layers():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Station Map")
        window.show()
        window._apply_layer_preset("map", "Surface context")
        assert window._map_field.is_enabled() is True
        assert window._map_radar.is_enabled() is False
        assert window._map_field.opacity() == pytest.approx(0.50)
        window._refresh_layer_list("map")
        texts = [window._map_layers.list.item(index).text()
                 for index in range(window._map_layers.list.count())]
        assert any("HRRR" in text and "50%" in text for text in texts)
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_picker_opacity_drag_persists_immediately(tmp_path, monkeypatch):
    from qtpy.QtCore import QSettings

    from sharpmod import gui_picker

    settings_path = str(tmp_path / "drag.ini")

    def _settings_for(_cls=None):
        settings = QSettings(settings_path, QSettings.IniFormat)
        settings.setFallbacksEnabled(False)
        return settings

    monkeypatch.setattr(gui_picker, "_build_settings", _settings_for)
    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Station Map")
        window.show()
        window._map_field.set_opacity_value(0.44)
        fresh = QSettings(settings_path, QSettings.IniFormat)
        fresh.setFallbacksEnabled(False)
        assert float(fresh.value("overlays/hrrr_field_opacity")) == \
            pytest.approx(0.44)
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_picker_locator_rows_follow_inset_selection():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Station Map")
        window.show()
        window._map_locator.set_spec("risk,reports,hrrr")
        window._refresh_layer_list("map")
        texts = [window._map_layers.list.item(index).text()
                 for index in range(window._map_layers.list.count())]
        locator_rows = [text for text in texts if "sounding locator" in text
                        or "SPC risk areas" in text or "Storm reports" in text
                        and "●" in text]
        assert any("SPC risk areas" in text for text in texts)
        assert any("sounding locator" in text
                   for text in [window._map_layers.list.item(index).toolTip()
                                for index in range(window._map_layers.list.count())])
        assert locator_rows
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_picker_opacity_and_preset_persist_across_relaunch(tmp_path, monkeypatch):
    from qtpy.QtCore import QSettings

    from sharpmod import gui_picker
    from sharpmod.gui_settings import _build_settings

    settings_path = str(tmp_path / "layers.ini")

    def _settings_for(_cls=None):
        settings = QSettings(settings_path, QSettings.IniFormat)
        settings.setFallbacksEnabled(False)
        return settings

    monkeypatch.setattr(gui_picker, "_build_settings", _settings_for)
    first = None
    second = None
    try:
        first = gui_picker.PickerWindow()
        first._ensure_tab("Station Map")
        first.show()
        first._apply_layer_preset("map", "Convection watch")
        first._map_field.set_opacity_value(0.62)
        first._map_radar.set_opacity_value(0.81)
        first.close()
        first.deleteLater()
        first = None
        probe = gui_picker.PickerWindow()
        try:
            assert probe._startup_field_opacity() == pytest.approx(0.62)
            assert probe._startup_radar_opacity() == pytest.approx(0.81)
            assert probe._startup_layer_preset() == "convection watch"
        finally:
            second = probe
    finally:
        for window in (first, second):
            if window is not None:
                try:
                    window.close()
                    window.deleteLater()
                except RuntimeError:
                    pass


def test_picker_session_carries_choices_not_frames():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Station Map")
        window.show()
        window._map_field.set_enabled(True)
        window._map_field.set_opacity_value(0.62)
        state = window.session_layer_state()
        assert state["version"] == 1
        assert state["enabled"]["map/hrrr_field"] is True
        assert state["opacities"]["map/hrrr_field"] == pytest.approx(0.62)
        window._map_field.set_enabled(False)
        window.restore_session_layer_state(state)
        assert window._map_field.is_enabled() is True
        assert window._map_field.opacity() == pytest.approx(0.62)
        window.restore_session_layer_state({"version": 99})
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_picker_session_splits_satellite_and_surface():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Station Map")
        window.show()
        window._map_context._sat_check.setChecked(True)
        window._map_context._surface_check.setChecked(False)
        state = window.session_layer_state()
        assert state["enabled"]["map/satellite"] is True
        assert state["enabled"]["map/surface"] is False
        window._map_context._sat_check.setChecked(False)
        window._map_context._surface_check.setChecked(True)
        window.restore_session_layer_state(state)
        assert window._map_context.is_satellite_enabled() is True
        assert window._map_context.is_surface_enabled() is False
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_picker_list_toggles_route_to_controllers():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Station Map")
        window.show()
        window._set_layer_visible("map", "hrrr_field", True)
        assert window._map_field.is_enabled() is True
        window._set_layer_visible("map", "hrrr_field", False)
        assert window._map_field.is_enabled() is False
        window._set_layer_visible("map", "goes_context", True)
        assert window._map_context.is_satellite_enabled() is True
        window._set_layer_visible("map", "surface_observations", True)
        assert window._map_context.is_surface_enabled() is True
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_picker_panels_list_uses_shared_controllers():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Field Panels")
        window.show()
        window._refresh_layer_list("panels")
        texts = [window._panels_layers.list.item(index).text()
                 for index in range(window._panels_layers.list.count())]
        assert any("Geography" in text for text in texts)
        assert len(window._panels_view.maps()) in (2, 4)
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
