"""Search does not change scientific selection until an actual choice is confirmed."""

import gc
import weakref

from qtpy.QtCore import QCoreApplication, QEvent, QSettings, Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QComboBox, QMainWindow

from sharpmod.gui_selectors import SelectorSearch, combo_choices, configure_combo_search


def _window(tmp_path):
    window = QMainWindow()
    window.resize(1000, 650)
    window._settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    window._settings.setValue("meta/native_settings_migrated", True)
    combo = QComboBox(window)
    combo.setAccessibleName("model")
    window.setCentralWidget(combo)
    return window, combo


def _dispose(window, qt_app):
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_app.processEvents()


def test_full_names_resolver_aliases_and_no_typing_side_effect(qt_app, tmp_path):
    window, combo = _window(tmp_path)
    combo.addItem("HRRR", "hrrr")
    combo.addItem("NAM 3km CONUS", "nam-3km-conus")
    combo.addItem("ECMWF IFS Open Data", "ecmwf-ifs")
    calls = []
    combo.currentIndexChanged.connect(calls.append)
    try:
        search = SelectorSearch(combo)
        search.search.setText("high resolution rapid refresh")
        assert search.results.count() == 1
        assert search.results.item(0).data(Qt.UserRole).key == "hrrr"
        search.search.setText("nam3")
        assert search.results.count() == 1
        assert combo.currentData() == "hrrr" and calls == []
        search.use_selected()
        assert combo.currentData() == "nam-3km-conus" and calls == [1]
        search.search.setText("ifs")
        search.reject()
        assert combo.currentData() == "nam-3km-conus"
    finally:
        _dispose(window, qt_app)


def test_parameter_full_names_and_favorites_survive_reordering_and_reopen(qt_app, tmp_path):
    from sharpmod.box_analysis import PARAMETERS

    window, combo = _window(tmp_path)
    for parameter in PARAMETERS:
        combo.addItem(parameter.display_label, parameter.key)
    try:
        choices, bucket = combo_choices(combo)
        assert bucket == "parameters", "shared product keys must not steal parameter favorites"
        assert any(choice.matches("mixed layer convective available potential energy")
                   and choice.key == "mlcape" for choice in choices)
        search = SelectorSearch(combo)
        search.search.setText("storm relative helicity 1km")
        assert search.results.count() == 1
        assert search.results.item(0).data(Qt.UserRole).key == "srh_1km"
        search.toggle_favorite()
        assert combo.currentData() == PARAMETERS[0].key
        search.reject()
        combo.clear()
        for parameter in reversed(PARAMETERS):
            combo.addItem(parameter.display_label, parameter.key)
        reopened = SelectorSearch(combo)
        reopened.only_favorites.setChecked(True)
        assert reopened.results.count() == 1
        assert "★" in reopened.results.item(0).text()
        assert reopened.results.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
        reopened.use_selected()
        assert combo.currentData() == "srh_1km"
        reopened.toggle_favorite()
        assert reopened.results.count() == 0
        assert not reopened.apply_button.isEnabled()
    finally:
        _dispose(window, qt_app)


def test_persistent_index_tracks_insertions_and_rejects_reset_or_disabled_field(qt_app, tmp_path):
    window, combo = _window(tmp_path)
    combo.addItem("First", "first")
    combo.addItem("Target", "target")
    try:
        search = SelectorSearch(combo)
        search.search.setText("target")
        combo.insertItem(0, "Inserted", "inserted")
        search.use_selected()
        assert combo.currentData() == "target"
        stale = SelectorSearch(combo)
        stale.search.setText("target")
        combo.clear()
        combo.addItem("Replacement", "replacement")
        stale.use_selected()
        assert combo.currentData() == "replacement"
        assert "Choices changed" in stale.status.text()
        assert not stale.apply_button.isEnabled()
        disabled = SelectorSearch(combo)
        combo.setEnabled(False)
        disabled.use_selected()
        assert "unavailable" in disabled.status.text()
    finally:
        _dispose(window, qt_app)


def test_ctrl_space_and_right_click_action_are_native_and_bounded(qt_app, tmp_path):
    window, combo = _window(tmp_path)
    combo.addItems(["One", "Two", "Three"])
    controller = configure_combo_search(combo)
    assert configure_combo_search(combo) is controller
    assert combo.contextMenuPolicy() == Qt.ActionsContextMenu
    assert "Ctrl+Space" in combo.accessibleDescription()
    try:
        window.show()
        window.activateWindow()
        combo.setFocus()
        qt_app.processEvents()
        QTest.keyClick(combo, Qt.Key_Space, Qt.ControlModifier)
        qt_app.processEvents()
        assert controller.dialog.isVisible()
        controller.dialog.search.setText("two")
        QTest.keyClick(controller.dialog.search, Qt.Key_Return)
        assert combo.currentText() == "Two"
        controller.dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        controller.open()
        assert controller.dialog.isVisible()
        controller.open()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert len(window.findChildren(SelectorSearch)) == 1
    finally:
        _dispose(window, qt_app)


def test_category_filtered_panel_search_covers_full_catalogue_and_emits_once(qt_app, tmp_path):
    from sharpmod.gui_map_panels import _FieldSelector
    from sharpmod.hrrr_products import available_products

    window, unused = _window(tmp_path)
    unused.deleteLater()
    selector = _FieldSelector("refc", window)
    window.setCentralWidget(selector)
    calls = []
    selector.productChanged.connect(calls.append)
    try:
        search = SelectorSearch(selector._product)
        assert len(search.choices) == len(available_products())
        search.search.setText("significant tornado")
        assert search.results.count() == 1
        assert calls == [] and selector.product() != "stp"
        search.use_selected()
        assert selector.product() == "stp"
        assert calls == ["stp"]
        assert selector._category.currentData() == "Composite Parameters"
    finally:
        _dispose(window, qt_app)


def test_search_window_releases_all_wrappers_on_close(qt_app, tmp_path):
    window, combo = _window(tmp_path)
    combo.addItems(["One", "Two"])
    controller = configure_combo_search(combo)
    controller.open()
    window_ref, dialog_ref = weakref.ref(window), weakref.ref(controller.dialog)
    _dispose(window, qt_app)
    del controller, combo, window
    gc.collect()
    assert window_ref() is None and dialog_ref() is None
