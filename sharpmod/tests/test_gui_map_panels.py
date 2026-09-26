"""Focused regressions for T24 two/four-panel field analysis.

Covers the panel contracts without the network: one shared geographic
crosshair with per-panel values, searchable/remembered selection with stable
slots, maximise/restore with shared extent, named presets with truthful
placeholders, and request reuse through the existing derived-grid cache.

T24.1 crosshair: hovering one panel mirrors one geographic location onto
every other panel while each panel keeps sampling its own field, units,
valid time, and unavailable state. The hovered panel keeps its own hover
readout; siblings gain guides plus a line naming their own value.
T24.2 selection: category switches answer with the remembered product for
that category; swap/reorder exchange configured slots without repacking.
T24.3 maximise: one panel fills the grid while hidden panels keep every
configured choice; the shared extent survives maximise and restore, and
locking one panel locks every panel.
T24.4 presets: named combinations land positionally, keep slot meaning for
missing panels, and report loading with the field plus the pinned run/hour.
T24.5 reuse: hidden/invisible panels keep products without spending
requests; sampling reads the remembered derived grid, never a colour.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timezone

import pytest
from qtpy.QtCore import QPointF, Qt

pytestmark = pytest.mark.usefixtures("qt_app")

RUN = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)


class _MouseEvent:
    def __init__(self, pos, *, button=Qt.LeftButton, buttons=None,
                 modifiers=Qt.NoModifier):
        self._pos = QPointF(*pos) if isinstance(pos, tuple) else pos
        self._button = button
        self._buttons = buttons if buttons is not None else Qt.NoButton
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


def _panels(**kwargs):
    from sharpmod.gui_map_panels import MapPanelsView

    view = MapPanelsView(**kwargs)
    view.resize(900, 600)
    for panel in view._panels:
        panel.map.resize(420, 280)
        panel.map.set_extent(-100.0, -90.0, 30.0, 42.0, pad=0.0)
    return view


def _remember(product, run, fxx, value=1500.0):
    import numpy as np

    from sharpmod import hrrr_field

    field = np.full((1536, 3072), float(value), dtype=np.float32)
    hrrr_field.remember_derived(product, run, fxx, (3072, 1536), field)
    return hrrr_field


def _clear_frames():
    from sharpmod import hrrr_field

    hrrr_field.clear_derived_cache()


def _close(view):
    try:
        view.shutdown()
    except Exception:  # noqa: BLE001 - teardown never fails the test
        pass
    view.close()
    view.deleteLater()


# --------------------------------------------------------------------------- #
# T24 model: presets, memory, arrangement state
# --------------------------------------------------------------------------- #

def test_panel_state_round_trips_choices_not_frames():
    from sharpmod.gui_map_panels import (
        panel_state,
        restore_panel_state,
    )

    state = panel_state(count=4, products=("refc", "mlcape", "stp", "sbcape"),
                        preset="storm-environment",
                        category_memory={"Instability": "mlcape"},
                        maximised=1)
    assert state["version"] == 1
    assert state["count"] == 4
    assert state["preset"] == "storm-environment"
    assert state["maximised"] == 1
    assert restore_panel_state(state) == state
    assert restore_panel_state({"version": 99}) == panel_state()
    assert restore_panel_state(None) == panel_state()


def test_unknown_preset_and_memory_degrade_to_working_grid():
    from sharpmod.gui_map_panels import (
        migrate_panel_preset_choice,
        normalise_panel_category_memory,
        preset_panel_products,
    )

    assert migrate_panel_preset_choice("not a preset") == \
        "storm-environment"
    assert preset_panel_products("not a preset") == preset_panel_products(
        "storm-environment")
    assert normalise_panel_category_memory(
        {"Instability": "mlcape", "Wind Shear": "not-a-field"}) == \
        {"Instability": "mlcape"}


def test_all_preset_products_are_real_catalogue_keys():
    from sharpmod import hrrr_products
    from sharpmod.gui_map_panels import PANEL_PRESETS

    assert PANEL_PRESETS, "no named field presets"
    for key, entry in PANEL_PRESETS.items():
        products = tuple(entry["products"])
        assert len(products) == 4, key
        for product in products:
            assert product in hrrr_products.PRODUCTS, (key, product)
        assert "warning" not in str(entry.get("label", "")).casefold(), key


# --------------------------------------------------------------------------- #
# T24.1 shared crosshair with per-panel values
# --------------------------------------------------------------------------- #

def test_hover_mirrors_one_location_onto_sibling_panels():
    view = _panels()
    hrrr = _remember("mlcape", RUN, 6)
    try:
        for panel in view._panels:
            panel.set_forecast_reference(RUN, 6)
        source = view._panels[0].map
        target = view._panels[1].map
        source.mouseMoveEvent(_MouseEvent((210.0, 140.0)))
        assert view.crosshair() is not None
        assert target.crosshair() == view.crosshair()
        # The hovered panel keeps its own hover; it never mirrors itself.
        assert source.crosshair() is None
        assert view.crosshair_texts()
        assert "Crosshair" in view.crosshair_accessible_text()
    finally:
        _clear_frames()
        _close(view)


def test_each_panel_reports_its_own_field_units_and_time():
    view = _panels()
    _remember("mlcape", RUN, 6, value=1500.0)
    _remember("refc", RUN, 6, value=45.0)
    try:
        view._panels[0].set_product("mlcape")
        view._panels[1].set_product("refc")
        for panel in view._panels[:2]:
            panel.set_forecast_reference(RUN, 6)
        view._panels[0].map.mouseMoveEvent(_MouseEvent((210.0, 140.0)))
        texts = view.crosshair_texts()
        reflectivity = next(text for text in texts if "Panel 2" in text)
        assert "dBZ" in reflectivity
        assert "04 Sep" in reflectivity
        # Panel 1 is the hovered source and keeps its own hover readout;
        # Panel 2 mirrors the same location with its own field and units.
        assert len(texts) == 1
    finally:
        _clear_frames()
        _close(view)


def test_crosshair_missing_state_names_its_limitation():
    view = _panels()
    try:
        for panel in view._panels:
            panel.map.set_map_tool("select")
        view._panels[0].set_forecast_reference(RUN, 6)
        view._panels[1].set_forecast_reference(RUN, 6)
        # No derived grid remembered: each panel states its own honest
        # limitation (imagery-only or outside-domain), never a colour lookup.
        view._panels[0].map.mouseMoveEvent(_MouseEvent((210.0, 140.0)))
        texts = view.crosshair_texts()
        assert texts
        joined = "\n".join(texts).casefold()
        assert ("imagery only" in joined or "outside model coverage" in joined
                or "no numeric value" in joined)
        view.clear_crosshair()
        assert view.crosshair() is None
        assert view.crosshair_texts() == ()
    finally:
        _close(view)


def test_crosshair_never_moves_point_selection_or_cards():
    view = _panels()
    _remember("mlcape", RUN, 6)
    try:
        for panel in view._panels:
            panel.set_forecast_reference(RUN, 6)
        before = [panel.map.context_point() for panel in view._panels]
        view._panels[0].map.mouseMoveEvent(_MouseEvent((210.0, 140.0)))
        after = [panel.map.context_point() for panel in view._panels]
        assert before == after
        assert all(panel.map.inspection_snapshot() is None
                   for panel in view._panels)
    finally:
        _clear_frames()
        _close(view)


def test_crosshair_legend_avoidance_and_accessible_panel_text():
    view = _panels()
    _remember("mlcape", RUN, 6)
    try:
        for panel in view._panels:
            panel.set_forecast_reference(RUN, 6)
        panel = view._panels[1]
        panel.map.mouseMoveEvent(_MouseEvent((210.0, 140.0)))
        other = view._panels[0].map
        assert other.crosshair() is not None
        probe = other._proj()
        avoid = other._legend_avoid_points(probe)
        point = other._to_px(other.crosshair()[1], other.crosshair()[0])
        assert any(abs(x - point.x()) < 1.0 and abs(y - point.y()) < 1.0
                   for x, y in avoid)
        assert "Panel 1" in view._panels[0].accessible_text()
        assert "Crosshair" in view._panels[0].accessible_text()
    finally:
        _clear_frames()
        _close(view)


# --------------------------------------------------------------------------- #
# T24.2 selection memory, swap, and stable slots
# --------------------------------------------------------------------------- #

def test_category_switch_answers_with_remembered_product():
    view = _panels()
    try:
        selector = view._panels[0].selector
        selector.set_product("mlcape")
        selector._on_product(0)
        assert selector.category_memory().get("Instability") == "mlcape"
        # Switch away and back: the group returns to MLCAPE, not its first row.
        selector._category.setCurrentIndex(
            selector._category.findData("Wind Shear"))
        assert selector.product() != "mlcape"
        selector._category.setCurrentIndex(
            selector._category.findData("Instability"))
        assert selector.product() == "mlcape"
    finally:
        _close(view)


def test_search_commits_one_catalogue_choice():
    view = _panels()
    try:
        selector = view._panels[0].selector
        selector._choose_search_product("stp")
        assert selector.product() == "stp"
        assert view._panels[0].product() == "stp"
        before = selector.product()
        selector._choose_search_product("not-a-field")
        assert selector.product() == before
    finally:
        _close(view)


def test_swap_keeps_slots_stable_without_repacking():
    view = _panels()
    try:
        view.set_panel_products(("refc", "mlcape", "stp", "sbcape"))
        assert view.swap_panels(0, 2) is True
        assert view.panel_products()[:4] == (
            "stp", "mlcape", "refc", "sbcape")
        assert view.swap_panels(0, 0) is False
        assert view.swap_panels(0, 99) is False
        assert view.move_panel_product(0, 2) is True
        assert view.panel_products()[:4] == (
            "mlcape", "refc", "stp", "sbcape")
        assert view.move_panel_product(1, 1) is False
    finally:
        _close(view)


def test_category_memory_round_trips_through_view():
    view = _panels()
    try:
        view._panels[0].selector.set_product("mlcape")
        view._panels[0].selector._on_product(0)
        memory = view.category_memory()
        assert memory.get("Instability") == "mlcape"
        probe_memory = {"Instability": "sbcape"}
        view.set_category_memory(probe_memory)
        assert view.category_memory().get("Instability") == "sbcape"
    finally:
        _close(view)


# --------------------------------------------------------------------------- #
# T24.3 maximise / restore with shared state
# --------------------------------------------------------------------------- #

def test_maximise_keeps_products_extent_locks_and_crosshair():
    view = _panels()
    _remember("mlcape", RUN, 6)
    try:
        view.set_panel_products(("refc", "mlcape", "stp", "sbcape"))
        for panel in view._panels:
            panel.set_forecast_reference(RUN, 6)
        bounds = view._panels[0].map.view_bounds()
        view._panels[1].map.mouseMoveEvent(_MouseEvent((210.0, 140.0)))
        shared_before = view.crosshair()
        assert shared_before is not None
        assert view.set_maximised_panel(1) is True
        assert view.maximised_panel() == 1
        assert view.panel_products()[:4] == (
            "refc", "mlcape", "stp", "sbcape")
        assert view._panels[1].map.view_bounds() == bounds
        assert view.crosshair() == shared_before
        assert view.restore_maximised() is True
        assert view.maximised_panel() is None
        assert view._panels[0].map.view_bounds() == bounds
        assert view.panel_products()[:4] == (
            "refc", "mlcape", "stp", "sbcape")
        assert view.set_maximised_panel(99) is False
        assert view.set_maximised_panel(None) is False
    finally:
        _clear_frames()
        _close(view)


def test_panel_lock_fans_out_to_every_panel():
    view = _panels()
    try:
        view.set_point_locked(True)
        assert view.is_point_locked() is True
        for panel in view._panels:
            assert panel.map.is_point_locked() is True
        view.set_point_locked(False)
        assert view.is_point_locked() is False
    finally:
        _close(view)


def test_panel_tools_and_recent_point_are_shared_contracts():
    view = _panels()
    try:
        view.set_map_tool("inspect")
        for panel in view._panels:
            assert panel.map.map_tool() == "inspect"
        view.set_map_tool("select")
        assert view.has_recent_point() is False
        assert view.restore_recent_point() is False
    finally:
        _close(view)


# --------------------------------------------------------------------------- #
# T24.4 presets and stable placeholders
# --------------------------------------------------------------------------- #

def test_applying_a_preset_lands_positionally():
    view = _panels()
    try:
        resolved = view.apply_preset("hail-ingredients")
        assert resolved == "hail-ingredients"
        from sharpmod.gui_map_panels import preset_panel_products

        assert view.panel_products() == preset_panel_products(resolved)
        assert view.apply_preset("not-a-preset") == "storm-environment"
    finally:
        _close(view)


def test_missing_panel_keeps_its_slot_and_names_its_field():
    view = _panels()
    try:
        view.set_panel_products(("refc", "mlcape", "stp", "sbcape"))
        panel = view._panels[2]
        text = panel.placeholder_text()
        assert "Significant Tornado" in text or "stp" in text.casefold()
        assert "loading" in text.casefold()
        assert panel.productChanged is not None
        assert view.visible_panel_products() == ("refc", "mlcape")
        view.set_panel_count(4)
        assert view.panel_products()[2] == "stp"
    finally:
        _close(view)


# --------------------------------------------------------------------------- #
# T24.5 request reuse: invisible panels keep state without fetching
# --------------------------------------------------------------------------- #

def test_hidden_panels_keep_products_without_enabling_fetches():
    view = _panels(panel_count=2)
    try:
        view.set_panel_products(("refc", "mlcape", "stp", "sbcape"))
        hidden = [panel for panel in view._panels if not panel.is_active()]
        assert len(hidden) == 2
        assert [panel.product() for panel in hidden] == ["stp", "sbcape"]
        assert all(panel.field.is_enabled() is False for panel in hidden)
        view.set_panel_count(4)
        assert view.panel_products() == ("refc", "mlcape", "stp", "sbcape")
    finally:
        _close(view)


def test_layout_count_change_never_resets_configured_fields():
    view = _panels(panel_count=4)
    try:
        view.set_panel_products(("sbcape", "srh-0-1km", "stp", "refc"))
        view.set_panel_count(2)
        assert view.visible_panel_products() == ("sbcape", "srh-0-1km")
        view.set_panel_count(4)
        assert view.panel_products() == ("sbcape", "srh-0-1km", "stp", "refc")
    finally:
        _close(view)


def test_panel_request_controls_live_outside_the_map_grid():
    view = _panels(panel_count=4)
    try:
        for panel in view._panels:
            assert panel.layout().indexOf(panel.activity_row) == -1
            assert panel.activity_row.parentWidget() is panel
            panel.field._set_status("Loading test field…", state="loading")
            assert panel.cancel_btn.isVisibleTo(panel.activity_row)
            panel.field._set_status("Field failed", state="failed")
            assert panel.retry_btn.isVisibleTo(panel.activity_row)
    finally:
        _close(view)


def test_picker_hosts_panel_activity_in_field_setup(qt_app, monkeypatch):
    from sharpmod.gui_picker import PickerWindow
    from sharpmod.ui.maps.overlays.hrrr_controller import HrrrFieldController

    monkeypatch.setattr(HrrrFieldController, "_request", lambda self: None)
    window = PickerWindow()
    try:
        window._ensure_tab("Field Panels")
        window._panels_view.set_panel_count(4)
        for panel, host in window._panels_activity_rows:
            assert panel.activity_row.parentWidget() is host
            assert host.isVisibleTo(window._panels_controls_scroll)
            panel.field._set_status("Loading a field…", state="loading")
            assert panel.cancel_btn.isVisibleTo(host)
            panel.field._set_status("No field available", state="no-data")
            assert panel.retry_btn.isVisibleTo(host)
        window._panels_view.set_panel_count(2)
        assert [host.isHidden() for _, host in window._panels_activity_rows] == [
            False, False, True, True]
    finally:
        window._catalog_timer.stop()
        window._avail_timer.stop()
        window._model_availability_timer.stop()
        window.close()
        window.deleteLater()


def test_picker_shares_only_matching_visible_field_details(qt_app, monkeypatch):
    from qtpy.QtCore import QBuffer, QByteArray
    from qtpy.QtGui import QColor, QImage

    from sharpmod.gui_picker import PickerWindow
    from sharpmod.maps.map_overlays import OverlayRaster
    from sharpmod.providers.hrrr_field import COVERAGE_BOUNDS
    from sharpmod.ui.maps.overlays.hrrr_controller import HrrrFieldController

    monkeypatch.setattr(HrrrFieldController, "_request", lambda self: None)
    image = QImage(2, 2, QImage.Format_RGBA8888)
    image.fill(QColor("transparent"))
    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QBuffer.WriteOnly)
    assert image.save(buffer, "PNG")
    window = PickerWindow()
    try:
        window._ensure_tab("Field Panels")
        window._panels_view.set_panel_count(4)
        panels = window._panels_view._panels

        def attach(panel, source):
            panel.map.set_overlay("hrrr_field", OverlayRaster(
                key="hrrr_field", title=panel.panel_label(),
                image_bytes=bytes(store), bounds=COVERAGE_BOUNDS,
                short_name=panel.product(), attribution=source))

        for panel in panels:
            attach(panel, "NOAA/NCEP HRRR via AWS Open Data")
        window._update_forecast_map_details("panels")
        shared = window._panels_shared_details.text()
        assert shared.count("≈3 km") == 1
        assert shared.count("Source: NOAA/NCEP HRRR via AWS Open Data") == 1
        assert all("Color scale:" in label.text()
                   and "Source:" not in label.text()
                   and "native grid" not in label.text()
                   for _panel, label in window._panels_detail_rows)

        attach(panels[3], "Different provider")
        window._update_forecast_map_details("panels")
        assert "≈3 km" in window._panels_shared_details.text()
        assert "Source:" not in window._panels_shared_details.text()
        assert "Source: Different provider" in window._panels_detail_rows[3][1].text()

        window._panels_view.set_panel_count(2)
        assert "Source: NOAA/NCEP HRRR via AWS Open Data" in \
            window._panels_shared_details.text()
    finally:
        window._catalog_timer.stop()
        window._avail_timer.stop()
        window._model_availability_timer.stop()
        window.close()
        window.deleteLater()


def test_session_panels_state_restores_arrangement_not_frames():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Field Panels")
        window.show()
        view = window._panels_view
        view.set_panel_count(4)
        view.set_panel_products(("sbcape", "srh-0-1km", "stp", "refc"))
        state = window.session_layer_state()
        assert state["panels"]["count"] == 4
        assert tuple(state["panels"]["products"]) == (
            "sbcape", "srh-0-1km", "stp", "refc")
        view.set_panel_count(2)
        view.set_panel_products(("refc", "mlcape", "stp", "sbcape"))
        window.restore_session_layer_state(state)
        assert view.panel_count() == 4
        assert view.panel_products() == ("sbcape", "srh-0-1km", "stp", "refc")
        assert view.maximised_panel() is None
        window.restore_session_layer_state({"version": 99})
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
