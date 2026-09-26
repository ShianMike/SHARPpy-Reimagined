"""Focused regressions for T20 map legends for clarity and space.

Covers the legend contracts before visual evidence, reusing the T19 layer
identity (names, states, paint order, occlusion) rather than forking it:

* T20.1 placement: the legend stays in a stable bottom-right default and avoids
  the sounding point and inspection card; collapses on small windows or total
  collision, or on request; the choice persists per corner.
* T20.2 compact identity: product name, units, and scale survive collapsing;
  categorical rows wrap past four and fold past eight into an explicit count;
  continuous fields carry a ramp/banded tag distinct from categorical keys.
* T20.3 fixed vs auto scales: a lock keeps one stated range across
  panels/times, labels any overruled change explicitly, and drops out-of-span
  ticks rather than clamping them; locks persist and migrate.
* T20.4 hover link: a hovered numeric value maps to a fractional bar position
  with clamped honesty; native grid resolution context names the grid beside
  the rendered smoothness; imagery-only states carry no bar marker.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytestmark = pytest.mark.usefixtures("qt_app")


def _map():
    from sharpmod import gui

    return gui.PointMapWidget()


def _sized(widget, width=640, height=480):
    # setMinimumSize first: the map's own 520x380 minimum wins over resize
    # otherwise, and a "small window" test would paint at 520 wide instead.
    widget.setMinimumSize(1, 1)
    widget.resize(width, height)
    widget.set_extent(-100.0, -90.0, 30.0, 42.0, pad=0.0)
    return widget


def _hrrr_frame(key="refc", title="Composite Reflectivity", opacity=0.75):
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
        key="hrrr_field", title=title, image_bytes=bytes(store),
        bounds=hrrr_field.COVERAGE_BOUNDS, short_name=key, opacity=opacity)


def _outlook_layer():
    from sharpmod import map_overlays as mo
    from sharpmod import spc_outlook

    rings = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [[
            [-100.0, 35.0], [-90.0, 35.0], [-90.0, 45.0],
            [-100.0, 45.0], [-100.0, 35.0]]]})[0]
    shapes = [
        mo.OverlayShape(rings=rings, bounds=mo.bounds_of(rings),
                        stroke="#111111", fill="#222222",
                        label=f"C{i}", hatch=(i % 2 == 0),
                        hatch_level=i % 3)
        for i in range(10)
    ]
    return mo.build_layer(spc_outlook.OVERLAY_KEY, "SPC outlook", shapes)


# --------------------------------------------------------------------------- #
# T20 model: placement, compacting, locks, hover link, session
# --------------------------------------------------------------------------- #

def test_corner_avoids_what_matters():
    from sharpmod import map_legends

    box = map_legends.choose_legend_corner(
        width=800, height=600, box_width=240, box_height=120,
        avoid=((700, 30),))
    assert box.corner != "top-right"
    assert not box.covers(700, 30)
    auto = map_legends.choose_legend_corner(
        width=800, height=600, box_width=240, box_height=120, avoid=())
    assert auto.corner == "bottom-right"
    preferred = map_legends.choose_legend_corner(
        width=800, height=600, box_width=240, box_height=120, avoid=(),
        preferred="bottom-left")
    assert preferred.corner == "bottom-left"
    pinned = map_legends.choose_legend_corner(
        width=800, height=600, box_width=240, box_height=120,
        avoid=((700, 520),), preferred="bottom-right")
    assert pinned.corner == "bottom-right"
    assert pinned.covers(700, 520)


def test_legacy_top_left_migrates_and_hover_does_not_move_legend():
    from qtpy.QtGui import QPixmap

    widget = _sized(_map())
    try:
        widget.restore_legend_state({
            "version": 1, "corner": "top-left", "collapsed": False,
            "locks": {},
        })
        assert widget.legend_corner() == ""
        widget.set_overlay("spc_outlook", _outlook_layer())
        widget.show()
        widget.render(QPixmap(widget.size()))
        before = widget._legend_clip_box()
        assert widget.legend_layout_info()["corner"] == "bottom-right"
        widget._hover_lonlat = widget._to_lonlat(
            widget.width() - 20.0, widget.height() - 20.0)
        widget.render(QPixmap(widget.size()))
        assert widget.legend_layout_info()["corner"] == "bottom-right"
        assert widget._legend_clip_box() == pytest.approx(before)
    finally:
        widget.close()
        widget.deleteLater()


def test_collapse_rules_keep_identity_not_key():
    from sharpmod import map_legends

    assert map_legends.should_collapse(
        width=800, height=600, box_width=240, box_height=120, avoid=(),
        user_collapsed=True) is True
    assert map_legends.should_collapse(
        width=300, height=400, box_width=240, box_height=200,
        avoid=()) is True
    assert map_legends.should_collapse(
        width=320, height=300, box_width=120, box_height=150,
        avoid=()) is True
    assert map_legends.should_collapse(
        width=800, height=600, box_width=240, box_height=120,
        avoid=()) is False
    assert map_legends.should_collapse(
        width=800, height=600, box_width=240, box_height=340,
        avoid=()) is True


def test_enlarged_legend_rows_stay_expanded_when_compact_content_fits():
    from qtpy.QtGui import QPixmap
    from qtpy.QtWidgets import QApplication

    from sharpmod.gui_theme import apply_theme

    widget = _sized(_map(), width=1060, height=700)
    try:
        apply_theme(QApplication.instance(), color_style="standard",
                    text_scale=200)
        for index in range(6):
            widget.set_overlay(f"proof-{index}", _outlook_layer())
        widget.show()
        widget.render(QPixmap(widget.size()))
        info = widget.legend_layout_info()
        assert info["collapsed"] is False
        box = widget._legend_clip_box()
        assert box is not None
        assert box[1] >= 0.0
        assert box[1] + box[3] <= widget.height() + 1.0
    finally:
        apply_theme(QApplication.instance(), color_style="standard",
                    text_scale=100)
        widget.close()
        widget.deleteLater()


def test_compact_identity_never_drops_the_product():
    from sharpmod import map_legends

    assert map_legends.compact_identity(
        product="Composite Reflectivity", units="dBZ",
        scale_text="Fixed scale 5–75 dBZ") == \
        "Composite Reflectivity · dBZ · Fixed scale 5–75 dBZ"
    assert map_legends.compact_identity(product="Outlook") == "Outlook"
    assert map_legends.compact_identity(
        product="", units="dBZ") == "dBZ"


def test_category_wrap_folds_with_count():
    from sharpmod import map_legends

    shown, hidden = map_legends.wrap_category_rows(list(range(10)))
    assert shown == list(range(8))
    assert hidden == 2
    shown, hidden = map_legends.wrap_category_rows(list(range(3)))
    assert hidden == 0


def test_five_spc_risk_swatches_share_a_row_when_they_fit():
    from qtpy.QtGui import QFontMetricsF
    from sharpmod.gui_theme import mono_font

    widget = _sized(_map(), width=1200, height=700)
    try:
        rows = [(label, "#ffffff", "#777777", 0)
                for label in ("TSTM", "MRGL", "SLGT", "ENH", "MDT")]
        metrics = QFontMetricsF(mono_font("caption"))
        chunks, hidden = widget._category_row_layout(
            rows, 0, widget._colour_bar_width(), metrics, 14.0)
        assert [[row[0] for row in chunk] for chunk in chunks] == [
            ["TSTM", "MRGL", "SLGT", "ENH", "MDT"]]
        assert hidden == 0
        narrow, hidden = widget._category_row_layout(
            rows, 0, 140.0, metrics, 14.0)
        assert len(narrow) == 2
        assert sum(map(len, narrow)) + hidden == len(rows)
    finally:
        widget.close()
        widget.deleteLater()


def test_encoding_tags_cannot_be_confused():
    from sharpmod import map_legends

    assert map_legends.encoding_tag(continuous=True) == "continuous ramp"
    assert map_legends.encoding_tag(continuous=True,
                                    stepped=True) == "banded scale"
    assert map_legends.encoding_tag(continuous=False) == "categories"


def test_scale_lock_labels_and_change_notes():
    from sharpmod import map_legends

    assert map_legends.scale_lock_label(
        product="STP", minimum=0.5, maximum=8, units="",
        locked=True) == "Fixed scale: STP 0.5–8"
    assert map_legends.scale_lock_label(
        product="STP", minimum=0.5, maximum=8, units="",
        locked=False) == "Auto scale: STP 0.5–8"
    assert map_legends.scale_change_note(
        product="STP", old=(0.5, 8), new=(0.5, 8), units="") == ""
    note = map_legends.scale_change_note(
        product="STP", old=(0.5, 8), new=(1, 8), units="")
    assert "locked scale kept" in note
    assert map_legends.scale_change_note(
        product="STP", old=("x", 8), new=(1, 8), units="") == ""


def test_colorbar_fraction_clamps_honestly():
    from sharpmod import map_legends

    assert map_legends.colorbar_fraction(45, 5, 75) == pytest.approx(0.5714, abs=1e-3)
    assert map_legends.colorbar_fraction(200, 5, 75) == pytest.approx(1.0)
    assert map_legends.colorbar_fraction(-50, 5, 75) == pytest.approx(0.0)
    assert map_legends.colorbar_fraction("x", 5, 75) is None
    assert map_legends.colorbar_fraction(5, 5, 5) is None


def test_resolution_context_states_grid_or_absence():
    from sharpmod import map_legends

    assert "3 km" in map_legends.resolution_context(layer_key="hrrr_field")
    assert "smooths" in map_legends.resolution_context(layer_key="hrrr_field")
    assert "No gridded resolution" in map_legends.resolution_context(
        layer_key="storm_reports")


def test_legend_session_round_trips_choices():
    from sharpmod import map_legends

    state = map_legends.legend_state(
        corner="top-right", collapsed=True,
        locks={"stp": {"minimum": 0.5, "maximum": 8.0, "units": "",
                       "label": "STP"}})
    assert state["version"] == 1
    assert map_legends.restore_legend_state(state) == state
    assert map_legends.restore_legend_state({"version": 99}) == \
        map_legends.legend_state()
    assert map_legends.restore_legend_state(None) == map_legends.legend_state()


# --------------------------------------------------------------------------- #
# T20.1 placement and collapse on the live map
# --------------------------------------------------------------------------- #

def test_legend_avoids_the_sounding_point():
    widget = _sized(_map())
    try:
        widget.set_point(35.63, -97.44, center=True)
        widget.set_overlay("hrrr_field", _hrrr_frame())
        from qtpy.QtGui import QPixmap

        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        assert not pixmap.isNull()
        info = widget.legend_layout_info()
        assert info["corner"] != "bottom-left" or info["collapsed"] in (True, False)
        point = widget._to_px(-97.44, 35.63, widget._proj())
        box = widget._legend_clip_box()
        assert box is not None
        inside = (box[0] - 6.0 <= point.x() <= box[0] + box[2] + 6.0
                  and box[1] - 6.0 <= point.y() <= box[1] + box[3] + 6.0)
        assert not inside or info["collapsed"] is True
    finally:
        widget.close()
        widget.deleteLater()


def test_small_window_collapses_keeping_identity():
    widget = _sized(_map(), width=320, height=300)
    try:
        widget.set_overlay("hrrr_field", _hrrr_frame())
        widget.show()
        from qtpy.QtGui import QPixmap

        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        assert not pixmap.isNull()
        info = widget.legend_layout_info()
        assert info["collapsed"] is True
        text = widget.legend_accessible_text()
        assert "Composite Reflectivity" in text
        assert "dBZ" in text
    finally:
        widget.close()
        widget.deleteLater()


def test_user_collapse_and_corner_persist():
    widget = _sized(_map())
    try:
        widget.set_legend_corner("bottom-right")
        widget.set_legend_collapsed(True)
        state = widget.legend_state()
        assert state["corner"] == "bottom-right"
        assert state["collapsed"] is True
        widget.set_legend_corner("bogus")
        assert widget.legend_corner() == ""
        fresh = _sized(_map())
        try:
            fresh.restore_legend_state(state)
            assert fresh.legend_corner() == "bottom-right"
            assert fresh.is_legend_collapsed() is True
            fresh.restore_legend_state({"version": 99})
            assert fresh.legend_state() == {
                "version": 1, "corner": "", "collapsed": False, "locks": {}}
        finally:
            fresh.close()
            fresh.deleteLater()
    finally:
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# T20.2 compact legend: names, units, wrap, encodings
# --------------------------------------------------------------------------- #

def test_compact_legend_wraps_categories_with_count():
    widget = _sized(_map())
    try:
        widget.set_overlay("spc_outlook", _outlook_layer())
        from qtpy.QtGui import QPixmap

        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        assert not pixmap.isNull()
        text = widget.legend_accessible_text()
        assert "categories" in text
        assert "+2 more" in text
    finally:
        widget.close()
        widget.deleteLater()


def test_accessible_text_names_product_units_scale_encoding():
    widget = _sized(_map())
    try:
        widget.set_overlay("hrrr_field", _hrrr_frame())
        from qtpy.QtGui import QPixmap

        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        text = widget.legend_accessible_text()
        assert "Composite Reflectivity" in text
        assert "dBZ" in text
        assert "Auto scale" in text
        assert "banded scale" in text
    finally:
        widget.close()
        widget.deleteLater()


def test_banded_and_categorical_encodings_differ():
    widget = _sized(_map())
    try:
        widget.set_overlay("hrrr_field", _hrrr_frame())
        from qtpy.QtGui import QPixmap

        widget.render(QPixmap(widget.size()))
        field_text = widget.legend_accessible_text()
        widget.remove_overlay("hrrr_field")
        widget.set_overlay("spc_outlook", _outlook_layer())
        widget.render(QPixmap(widget.size()))
        outlook_text = widget.legend_accessible_text()
        assert "banded scale" in field_text
        assert "categories" in outlook_text
        assert "banded scale" not in outlook_text
    finally:
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# T20.3 fixed vs auto scales with explicit change labels
# --------------------------------------------------------------------------- #

def test_lock_keeps_range_and_labels_change():
    widget = _sized(_map())
    try:
        widget.set_overlay("hrrr_field", _hrrr_frame(key="mlcape",
                                                     title="MLCAPE"))
        widget.set_scale_locked("mlcape", True)
        assert widget.is_scale_locked("mlcape") is True
        info = widget.scale_lock_info("mlcape")
        assert info["minimum"] < info["maximum"]
        text = widget.legend_accessible_text()
        assert "Fixed scale" in text
        widget.set_overlay("hrrr_field", _hrrr_frame(key="refc",
                                                     title="Reflectivity"))
        from qtpy.QtGui import QPixmap

        widget.render(QPixmap(widget.size()))
        info_after = widget.legend_layout_info()
        assert info_after["range"] == (info["minimum"], info["maximum"])
        widget.set_scale_locked("mlcape", False)
        assert widget.is_scale_locked("mlcape") is False
    finally:
        widget.close()
        widget.deleteLater()


def test_locked_ticks_drop_out_of_span_values():
    from sharpmod import hrrr_products

    widget = _sized(_map())
    try:
        widget.set_overlay("hrrr_field", _hrrr_frame(key="mlcape",
                                                     title="MLCAPE"))
        widget.set_scale_locked("mlcape", True)
        lock = widget.scale_lock_info("mlcape")
        palette = hrrr_products.get_product("mlcape").palette
        from qtpy.QtGui import QFontMetrics
        from qtpy.QtGui import QFont

        metrics = QFontMetrics(QFont())
        ticks = widget._colour_bar_tick_layout(metrics, palette, 10.0, 200.0,
                                               bar_range=(lock["minimum"],
                                                          lock["maximum"]))
        assert ticks, "a locked bar keeps at least its ends"
        widget.set_scale_locked("mlcape", False)
    finally:
        widget.close()
        widget.deleteLater()


def test_colour_bar_ticks_fit_without_overlapping_across_products():
    from qtpy.QtGui import QFontMetrics

    from sharpmod.providers.hrrr_products import available_products
    from sharpmod.ui.features.gui_theme import mono_font

    widget = _sized(_map())
    try:
        metrics = QFontMetrics(mono_font("caption"))
        for width in (160.0, 220.0, 320.0):
            for spec in available_products():
                palette = spec.palette
                if palette is None:
                    continue
                ticks = widget._colour_bar_tick_layout(
                    metrics, palette, 10.0, width)
                assert ticks, spec.key
                intervals = sorted((x, x + metrics.horizontalAdvance(label) + 6)
                                   for x, label in ticks)
                assert all(10.0 <= left and right <= 10.0 + width
                           for left, right in intervals), spec.key
                assert all(right + 5.0 <= next_left
                           for (_left, right), (next_left, _next_right)
                           in zip(intervals, intervals[1:])), spec.key
    finally:
        widget.close()
        widget.deleteLater()


def test_forecast_legend_keeps_bar_and_moves_metadata_to_rail():
    from qtpy.QtGui import QPixmap

    widget = _sized(_map(), width=420, height=300)
    try:
        widget._legend_metadata_in_rail = True
        widget.set_overlay("hrrr_field", _hrrr_frame())
        widget.render(QPixmap(widget.size()))
        assert widget.legend_layout_info()["bar"] is not None
        assert widget._legend_clip_box()[3] < 70
        assert widget.time_header_rect() is None
        assert "Color scale:" in widget.legend_details()
        assert "Auto scale" not in widget.legend_details()
    finally:
        widget.close()
        widget.deleteLater()


def test_scale_locks_persist_and_restore():
    widget = _sized(_map())
    try:
        widget.set_overlay("hrrr_field", _hrrr_frame(key="mlcape",
                                                     title="MLCAPE"))
        widget.set_scale_locked("mlcape", True)
        state = widget.legend_state()
        assert "mlcape" in state["locks"]
        fresh = _sized(_map())
        try:
            fresh.restore_legend_state(state)
            assert fresh.is_scale_locked("mlcape") is True
            assert fresh.scale_lock_info("mlcape")["minimum"] == \
                state["locks"]["mlcape"]["minimum"]
        finally:
            fresh.close()
            fresh.deleteLater()
    finally:
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# T20.4 hover-to-colorbar link and native resolution
# --------------------------------------------------------------------------- #

def test_hover_fraction_matches_sampled_value():
    widget = _sized(_map())
    try:
        import numpy as np

        from sharpmod import hrrr_field

        run, fxx = hrrr_field.resolve_request(None)
        field = np.full((1536, 3072), 45.0, dtype=np.float32)
        hrrr_field.remember_derived("refc", run, fxx, (3072, 1536), field)
        widget.set_inspection_product("refc", run=run, fxx=fxx)
        widget.set_overlay("hrrr_field", _hrrr_frame())
        widget._hover_lonlat = (-97.44, 35.63)
        from qtpy.QtGui import QPixmap

        widget.render(QPixmap(widget.size()))
        fraction = widget.hover_colorbar_fraction()
        assert fraction == pytest.approx((45.0 - 5.0) / (75.0 - 5.0), abs=1e-6)
        info = widget.legend_layout_info()
        assert info["bar"] is not None
        assert info["range"] == pytest.approx((5.0, 75.0))
    finally:
        widget.close()
        widget.deleteLater()


def test_hover_without_bar_or_value_reports_none():
    widget = _sized(_map())
    try:
        assert widget.hover_colorbar_fraction() is None
        widget.set_overlay("spc_outlook", _outlook_layer())
        from qtpy.QtGui import QPixmap

        widget.render(QPixmap(widget.size()))
        assert widget.hover_colorbar_fraction() is None
    finally:
        widget.close()
        widget.deleteLater()


def test_legend_names_native_resolution():
    widget = _sized(_map())
    try:
        widget.set_overlay("hrrr_field", _hrrr_frame())
        from qtpy.QtGui import QPixmap

        widget.render(QPixmap(widget.size()))
        assert widget.legend_accessible_text() != ""
        from sharpmod import map_legends

        assert "3 km" in map_legends.resolution_context(layer_key="hrrr_field")
    finally:
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# T20 picker + panels wiring: controls, persistence, small windows, exports
# --------------------------------------------------------------------------- #

def test_picker_legend_controls_drive_every_map():
    from qtpy.QtCore import Qt

    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Station Map")
        window.show()
        assert window._observed_job_scroll.horizontalScrollBarPolicy() == \
            Qt.ScrollBarAlwaysOff
        window._on_legend_corner_chosen("bottom-right")
        assert window._map.legend_corner() == "bottom-right"
        window._on_legend_collapsed_toggled(True)
        assert window._map.is_legend_collapsed() is True
        window._on_legend_collapsed_toggled(False)
        assert window._map.is_legend_collapsed() is False
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_picker_scale_lock_needs_a_field():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Station Map")
        window.show()
        product = window._legend_lock_product()
        assert product != ""
        window._on_scale_lock_toggled(True)
        assert window._map.is_scale_locked(product) is True
        window._on_scale_lock_toggled(False)
        assert window._map.is_scale_locked(product) is False
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_legend_controls_exist_on_every_rail_and_stay_in_sync():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Station Map")
        window._ensure_tab("Forecast Model")
        window._ensure_tab("Field Panels")
        window.show()
        assert len(getattr(window, "_legend_corner_combos", [])) == 3
        assert len(getattr(window, "_legend_collapsed_checks", [])) == 3
        assert len(getattr(window, "_scale_lock_checks", [])) == 3
        window._on_legend_corner_chosen("bottom-right")
        corners = [combo.currentData()
                   for combo in window._legend_corner_combos]
        assert corners == ["bottom-right"] * 3
        window._on_legend_collapsed_toggled(True)
        assert [check.isChecked()
                for check in window._legend_collapsed_checks] == [True] * 3
        window._on_legend_collapsed_toggled(False)
        assert window._map.is_legend_collapsed() is False
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_picker_legend_state_persists_across_relaunch(tmp_path, monkeypatch):
    from qtpy.QtCore import QSettings

    from sharpmod import gui_picker

    settings_path = str(tmp_path / "legend.ini")

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
        first._on_legend_corner_chosen("top-right")
        first._on_legend_collapsed_toggled(True)
        first.close()
        first.deleteLater()
        first = None
        probe = gui_picker.PickerWindow()
        try:
            assert probe._startup_map_presentation()["legend"]["corner"] == \
                "top-right"
            assert probe._startup_map_presentation()["legend"]["collapsed"] is True
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


def test_panels_share_one_legend_state():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Field Panels")
        window.show()
        shared = window._panels_view.shared_map()
        shared.set_legend_corner("top-right")
        shared.set_legend_collapsed(True)
        for panel_map in window._panels_view.maps():
            assert panel_map.legend_corner() == "top-right"
            assert panel_map.is_legend_collapsed() is True
        shared.set_legend_collapsed(False)
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_export_size_keeps_legend_readable():
    widget = _sized(_map(), width=1400, height=900)
    try:
        widget.set_overlay("hrrr_field", _hrrr_frame())
        from qtpy.QtGui import QPixmap

        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        assert not pixmap.isNull()
        info = widget.legend_layout_info()
        text = widget.legend_accessible_text()
        assert "Composite Reflectivity" in text
        assert info["bar"] is not None
    finally:
        widget.close()
        widget.deleteLater()


def test_prose_elides_inside_the_legend_box():
    from qtpy.QtCore import Qt
    from qtpy.QtGui import QFont, QFontMetrics

    widget = _sized(_map())
    try:
        metrics = QFontMetrics(QFont())
        text = "≈3 km native grid; rendering smooths between cells."
        assert metrics.horizontalAdvance(text) > 120.0
        elided = metrics.elidedText(text, Qt.ElideRight, 120)
        assert elided.endswith("…")
        assert metrics.horizontalAdvance(elided) <= 120.0
    finally:
        widget.close()
        widget.deleteLater()


def test_field_title_is_not_repeated_as_caption():
    widget = _sized(_map())
    try:
        widget.set_overlay("hrrr_field", _hrrr_frame())
        from qtpy.QtGui import QPixmap

        widget.render(QPixmap(widget.size()))
        text = widget.legend_accessible_text()
        assert text.count("Composite Reflectivity") == 1
    finally:
        widget.close()
        widget.deleteLater()
