"""T25 expanded locator, follow/pin, and standalone export checks."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from qtpy.QtCore import QObject, Signal, Qt
from qtpy.QtGui import QColor, QImage, QPixmap
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QMainWindow

from sharpmod import locator_presentation as lp
from sharpmod.gui_locator import (
    LocatorDialog,
    install_locator_tools,
    save_locator_png,
)
from sharpmod.map_overlays import OverlayLayer, OverlayShape
from sharpmod.viz import hodo_locator


VALID = datetime(2026, 5, 20, 18, tzinfo=timezone.utc)


class _Collection:
    def __init__(self):
        self._meta = {
            "lat": 35.25,
            "lon": -97.47,
            "requested_lat": 35.22,
            "requested_lon": -97.44,
            "loc": "Norman, Oklahoma",
            "model": "HRRR",
        }

    def getMeta(self, key):  # noqa: N802
        return self._meta.get(key)

    def setMeta(self, key, value):  # noqa: N802
        self._meta[key] = value

    def getCurrentDate(self):  # noqa: N802
        return VALID


class _Map(QObject):
    viewSettled = Signal()
    boxSelected = Signal(float, float, float, float)
    boxCleared = Signal()

    def __init__(self):
        super().__init__()
        self.bounds = (-110.0, -80.0, 25.0, 48.0)
        self.area = (33.0, -101.0, 39.0, -91.0)
        self._scale_units = "metric"

    def view_bounds(self):
        return self.bounds

    def box(self):
        return self.area

    def set_scale_units(self, units):
        self._scale_units = units


class _Controller(QObject):
    def __init__(self, map_widget):
        super().__init__()
        self.map_widget = map_widget
        self._settings = None

    def _active_map_widget(self):
        return self.map_widget


def _window_fixture(qt_app):
    collection = _Collection()
    source = SimpleNamespace(
        prof_collections=[collection],
        pc_idx=0,
        prof=SimpleNamespace(latitude=35.25, longitude=-97.47, date=VALID),
        bg_color=QColor("#05090b"),
        fg_color=QColor("#ffffff"),
    )
    win = QMainWindow()
    win.spc_widget = SimpleNamespace(
        hodo=source,
        prof_collections=[collection],
        pc_idx=0,
    )
    win.menuBar().addMenu("View")
    win.menuBar().addMenu("Export")
    map_widget = _Map()
    controller = _Controller(map_widget)
    return win, controller, map_widget, collection, source


def test_binding_follows_the_live_map_but_keeps_locked_custom_extent(qt_app):
    win, controller, map_widget, collection, _source = _window_fixture(qt_app)
    custom = (-99.0, 33.0, -95.0, 37.0)
    lp.set_collection_presentation(collection, lp.LocatorPresentation(
        link_mode=lp.LINK_FOLLOW,
        extent_mode=lp.EXTENT_CUSTOM,
        custom_bounds=custom,
    ))

    binding = install_locator_tools(win, controller)

    state = lp.collection_presentation(collection)
    assert state.main_bounds == (-110.0, 25.0, -80.0, 48.0)
    assert state.selected_area == (-101.0, 33.0, -91.0, 39.0)
    assert state.effective_bounds((35.25, -97.47)) == state.main_bounds

    map_widget.bounds = (-105.0, -88.0, 28.0, 44.0)
    map_widget.viewSettled.emit()
    state = lp.collection_presentation(collection)
    assert state.main_bounds == (-105.0, 28.0, -88.0, 44.0)
    assert state.custom_bounds == custom

    pinned = state.updated(link_mode=lp.LINK_PINNED)
    lp.set_collection_presentation(collection, pinned)
    binding.sync()
    assert lp.collection_presentation(collection).effective_bounds(
        (35.25, -97.47)) == custom
    win.close()


def test_binding_preserves_restored_area_until_the_map_explicitly_clears_it(
        qt_app):
    win, controller, map_widget, collection, _source = _window_fixture(qt_app)
    restored_area = (-102.0, 32.0, -90.0, 40.0)
    lp.set_collection_presentation(collection, lp.LocatorPresentation(
        link_mode=lp.LINK_PINNED,
        extent_mode=lp.EXTENT_SELECTED,
        selected_area=restored_area,
    ))
    map_widget.area = None

    install_locator_tools(win, controller)

    assert lp.collection_presentation(collection).selected_area == restored_area

    map_widget.boxCleared.emit()
    qt_app.processEvents()
    assert lp.collection_presentation(collection).selected_area is None
    win.close()


def test_picker_scale_preference_updates_the_open_locators_immediately(qt_app):
    from sharpmod.gui_picker import PickerWindow

    win, controller, map_widget, collection, _source = _window_fixture(qt_app)
    install_locator_tools(win, controller)
    controller._viewers = [win]
    controller._map_widgets = lambda: (map_widget,)
    controller._settings = SimpleNamespace(
        setValue=lambda *_args: None,
        sync=lambda: None,
    )

    PickerWindow._on_map_presentation_chosen(
        controller, "scale_units", "imperial")

    assert lp.collection_presentation(collection).scale_units == "imperial"
    win.close()


def test_expanded_dialog_makes_follow_and_pinned_state_explicit(qt_app):
    win, controller, map_widget, collection, _source = _window_fixture(qt_app)
    custom = (-99.0, 33.0, -95.0, 37.0)
    lp.set_collection_presentation(collection, lp.LocatorPresentation(
        link_mode=lp.LINK_PINNED,
        extent_mode=lp.EXTENT_CUSTOM,
        custom_bounds=custom,
    ))
    dialog = LocatorDialog(win, controller, parent=win)
    dialog.show()
    qt_app.processEvents()

    assert dialog.link_combo.currentData() == lp.LINK_PINNED
    assert dialog.extent_combo.currentData() == lp.EXTENT_CUSTOM
    assert "Pinned locator" in dialog.state_label.text()
    assert "unchanged" in dialog.state_label.text()
    assert dialog.custom_group.isVisible()

    dialog.link_combo.setCurrentIndex(dialog.link_combo.findData(lp.LINK_FOLLOW))
    qt_app.processEvents()
    followed = lp.collection_presentation(collection)
    assert followed.link_mode == lp.LINK_FOLLOW
    assert followed.custom_bounds == custom
    assert not dialog.extent_combo.isEnabled()
    assert not dialog.custom_group.isVisible()
    assert "Follow main map" in dialog.state_label.text()

    dialog.link_combo.setCurrentIndex(dialog.link_combo.findData(lp.LINK_PINNED))
    qt_app.processEvents()
    assert lp.collection_presentation(collection).effective_bounds(
        (35.25, -97.47)) == custom
    dialog.west.setValue(-100.0)
    dialog.east.setValue(-94.0)
    dialog.apply_custom.click()
    assert lp.collection_presentation(collection).custom_bounds == (
        -100.0, 33.0, -94.0, 37.0)
    dialog.use_main.click()
    assert lp.collection_presentation(collection).custom_bounds == \
        lp.bounds_from_map_view(map_widget.bounds)
    dialog.close()
    win.close()


def test_dialog_extent_choices_select_distinct_pinned_views(qt_app):
    win, controller, _map_widget, collection, _source = _window_fixture(qt_app)
    install_locator_tools(win, controller)
    dialog = LocatorDialog(win, controller, parent=win)
    dialog.show()
    qt_app.processEvents()
    resolved = {}

    for mode in (lp.EXTENT_LOCAL, lp.EXTENT_REGIONAL, lp.EXTENT_SELECTED):
        dialog.extent_combo.setCurrentIndex(dialog.extent_combo.findData(mode))
        state = lp.collection_presentation(collection)
        assert state.link_mode == lp.LINK_PINNED
        assert state.extent_mode == mode
        resolved[mode] = state.effective_bounds((35.25, -97.47))

    assert resolved[lp.EXTENT_LOCAL] != resolved[lp.EXTENT_REGIONAL]
    area = lp.collection_presentation(collection).selected_area
    assert area is not None
    selected = resolved[lp.EXTENT_SELECTED]
    assert selected[0] < area[0] < area[2] < selected[2]
    assert selected[1] < area[1] < area[3] < selected[3]
    dialog.close()
    win.close()


def test_expanded_canvas_click_does_not_change_requested_or_sampled_point(qt_app):
    win, controller, _map_widget, collection, _source = _window_fixture(qt_app)
    dialog = LocatorDialog(win, controller, parent=win)
    dialog.show()
    qt_app.processEvents()
    before = (
        collection.getMeta("requested_lat"),
        collection.getMeta("requested_lon"),
        collection.getMeta("lat"),
        collection.getMeta("lon"),
    )

    QTest.mouseClick(dialog.canvas, Qt.LeftButton, pos=dialog.canvas.rect().center())
    qt_app.processEvents()

    after = (
        collection.getMeta("requested_lat"),
        collection.getMeta("requested_lon"),
        collection.getMeta("lat"),
        collection.getMeta("lon"),
    )
    assert after == before
    dialog.close()
    win.close()


def test_standalone_png_uses_the_same_custom_presentation_without_mutation(
        qt_app, tmp_path):
    win, _controller, _map_widget, collection, source = _window_fixture(qt_app)
    state = lp.LocatorPresentation(
        link_mode=lp.LINK_PINNED,
        extent_mode=lp.EXTENT_CUSTOM,
        custom_bounds=(-99.0, 33.0, -95.0, 37.0),
        scale_units="imperial",
    )
    lp.set_collection_presentation(collection, state)
    before = lp.collection_presentation(collection)
    path = tmp_path / "locator.png"

    assert save_locator_png(win, path, width=900, height=600)

    assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    image = QImage(str(path))
    assert not image.isNull()
    assert (image.width(), image.height()) == (900, 600)
    assert lp.collection_presentation(collection) == before
    assert hodo_locator.bounds_from_widget(source) == state.custom_bounds
    win.close()


def test_expanded_canvas_and_same_size_export_have_identical_pixels(qt_app,
                                                                     tmp_path):
    win, controller, _map_widget, collection, _source = _window_fixture(qt_app)
    lp.set_collection_presentation(collection, lp.LocatorPresentation(
        link_mode=lp.LINK_PINNED,
        extent_mode=lp.EXTENT_CUSTOM,
        custom_bounds=(-99.0, 33.0, -95.0, 37.0),
    ))
    dialog = LocatorDialog(win, controller, parent=win)
    dialog.show()
    qt_app.processEvents()
    on_screen = dialog.canvas.grab().toImage()
    target = tmp_path / "expanded-locator.png"

    assert save_locator_png(
        win, target, width=dialog.canvas.width(), height=dialog.canvas.height())

    exported = QImage(str(target))
    assert not on_screen.isNull()
    assert not exported.isNull()
    assert exported.convertToFormat(QImage.Format_RGB32) == \
        on_screen.convertToFormat(QImage.Format_RGB32)
    dialog.close()
    win.close()


def test_overlay_status_names_hazard_source_even_when_unavailable(qt_app):
    win, _controller, _map_widget, collection, source = _window_fixture(qt_app)
    lp.set_overlay_status(
        collection,
        "spc_outlook",
        "unavailable",
        family="risk",
        product="torn",
        detail="Outside SPC outlook coverage",
    )

    context = hodo_locator.overlay_context_for_widget(source)

    assert len(context) == 1
    assert context[0]["hazard"] is True
    assert context[0]["source"] == "NOAA/NWS Storm Prediction Center"
    assert context[0]["status"] == "Outside SPC outlook coverage"
    win.close()


def test_live_hazard_context_reports_actual_time_and_attribution(qt_app):
    win, _controller, _map_widget, collection, source = _window_fixture(qt_app)
    shape = OverlayShape(
        rings=(((-99.0, 34.0), (-96.0, 34.0), (-96.0, 37.0)),),
        bounds=(-99.0, -96.0, 34.0, 37.0),
        stroke="#ffffff",
        fill="#ff0000",
        label="5%",
    )
    layer = OverlayLayer(
        key="spc_outlook",
        title="SPC tornado probability",
        shapes=(shape,),
        valid_from=VALID,
        valid_to=VALID.replace(hour=23),
        issued=VALID.replace(hour=17, minute=30),
        attribution="NOAA/NWS Storm Prediction Center",
    )
    collection.setMeta("sharpmod_locator_overlays", (layer,))

    context = hodo_locator.overlay_context_for_widget(source)

    assert context[0]["status"] == "available"
    assert context[0]["actual_time"] == layer.valid_from
    assert context[0]["time_basis"] == "valid from"
    assert "actual valid from" in hodo_locator.overlay_timing_text(context[0])
    assert context[0]["source"] == "NOAA/NWS Storm Prediction Center"
    win.close()


def test_expanded_accessible_context_names_points_hazard_time_and_source(qt_app):
    win, controller, _map_widget, collection, _source = _window_fixture(qt_app)
    lp.set_overlay_status(
        collection, "spc_outlook", "unavailable",
        family="risk", product="torn", detail="Provider unavailable",
    )
    dialog = LocatorDialog(win, controller, parent=win)
    dialog.show()
    dialog.canvas.render(QPixmap(dialog.canvas.size()))

    description = dialog.canvas.accessibleDescription()
    assert "Norman, Oklahoma" in description
    assert "requested 35.220, -97.440" in description
    assert "sampled 35.250, -97.470" in description
    assert "Hazard TORN: Provider unavailable" in description
    assert "NOAA/NWS Storm Prediction Center" in description
    dialog.close()
    win.close()


def test_deselected_saved_hazard_does_not_linger_as_current_context(qt_app):
    win, _controller, _map_widget, collection, source = _window_fixture(qt_app)
    collection.setMeta("sharpmod_locator_overlay_descriptors", ({
        "key": "spc_outlook",
        "title": "SPC tornado probability",
        "attribution": "NOAA/NWS Storm Prediction Center",
        "valid_from": VALID,
    },))
    assert hodo_locator.overlay_context_for_widget(source)[0]["status"] == \
        "saved context; reload required"

    lp.set_overlay_status(collection, "spc_outlook", "not-selected")

    assert hodo_locator.overlay_context_for_widget(source) == ()
    win.close()


def test_installation_exposes_inspection_and_export_actions(qt_app):
    win, controller, _map_widget, _collection, _source = _window_fixture(qt_app)

    install_locator_tools(win, controller)

    assert win._sharpmod_locator_action.text() == "Inspect Sounding Locator…"
    assert win._sharpmod_locator_export_action.text() == "Export Locator Image…"
    menus = win.menuBar().findChildren(type(win.menuBar().actions()[0].menu()))
    by_title = {menu.title(): menu for menu in menus}
    assert win._sharpmod_locator_action in by_title["View"].actions()
    assert win._sharpmod_locator_export_action in by_title["Export"].actions()
    win.close()
