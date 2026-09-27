"""The palette searches and invokes the same actions as native menus."""

import gc
import weakref

import pytest
from qtpy.QtCore import QCoreApplication, QEvent, Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QDialog, QLineEdit, QMainWindow

from sharpmod.gui_commands import CommandPalette, collect_commands, install_command_palette


@pytest.fixture
def command_window(qt_app):
    window = QMainWindow()
    window.resize(1000, 650)
    window.setCentralWidget(QLineEdit(window))
    file_menu = window.menuBar().addMenu("&File")
    export = file_menu.addMenu("&Export")
    image = export.addAction("Save &Image…")
    image.setShortcut("Ctrl+Shift+I")
    settings = file_menu.addAction("&Preferences…")
    settings.setToolTip("Choose application appearance")
    toggle = window.menuBar().addMenu("&View").addAction("Interaction tips")
    toggle.setCheckable(True)
    help_menu = window.menuBar().addMenu("&Help")
    window.show()
    window.activateWindow()
    qt_app.processEvents()
    yield window, export, image, settings, toggle, help_menu
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_app.processEvents()


def test_alias_context_shortcut_and_same_action_trigger(command_window, qt_app):
    window, _export, image, _settings, _toggle, _help = command_window
    calls = []
    image.triggered.connect(lambda checked=False: calls.append(checked))
    palette = CommandPalette(window)
    palette.show()
    palette.search.setText("export png")
    assert palette.results.count() == 1
    item = palette.results.item(0)
    assert item.data(Qt.UserRole).action is image
    assert "File › Export" in item.text()
    assert "Ctrl+Shift+I" in item.text()
    palette.search.setFocus()
    qt_app.processEvents()
    QTest.keyClick(palette.search, Qt.Key_Return)
    assert calls == [False], "Enter must run exactly the authoritative action once"
    assert not palette.isVisible()


def test_live_parent_availability_visibility_and_invocation_race(command_window, qt_app):
    window, export, image, _settings, _toggle, _help = command_window
    calls = []
    image.triggered.connect(lambda: calls.append(True))
    palette = CommandPalette(window)
    palette.search.setText("png")
    export.setEnabled(False)
    qt_app.processEvents()
    assert "unavailable" in palette.results.item(0).text()
    assert "unavailable" in palette.status.text()
    assert not palette.run_button.isEnabled()
    assert palette.results.currentItem() is not None, "disabled reason remains keyboard readable"
    palette.run_selected()
    assert calls == []
    export.setEnabled(True)
    qt_app.processEvents()
    assert palette.run_button.isEnabled()
    # Disable after a row was presented, without dispatching changed yet.
    image.setEnabled(False)
    palette.run_selected()
    assert calls == []
    image.setEnabled(True)
    export.menuAction().setVisible(False)
    qt_app.processEvents()
    assert palette.results.count() == 0
    assert not palette.run_button.isEnabled()


def test_alias_keyboard_browse_and_check_state(command_window, qt_app):
    window, _export, _image, settings, toggle, _help = command_window
    palette = CommandPalette(window)
    palette.show()
    palette.search.setText("settings")
    assert palette.results.item(0).data(Qt.UserRole).action is settings
    palette.search.setText("tips")
    assert "Off" in palette.results.item(0).text()
    palette.activateWindow()
    palette.search.setFocus()
    qt_app.processEvents()
    QTest.keyClick(palette.search, Qt.Key_Down)
    assert palette.results.hasFocus()
    QTest.keyClick(palette.results, Qt.Key_Return)
    assert toggle.isChecked()
    palette.reload_commands()
    assert "On" in palette.results.item(0).text()


def test_ctrl_k_discoverable_idempotent_reused_dialog_and_dynamic_actions(command_window, qt_app):
    window, _export, _image, _settings, _toggle, help_menu = command_window
    action = install_command_palette(window, menu=help_menu)
    assert install_command_palette(window, menu=help_menu) is action
    assert action in help_menu.actions()
    assert action.shortcut().toString() == "Ctrl+K"
    assert all(command.action is not action for command in collect_commands(window, exclude=action))
    QTest.keyClick(window.centralWidget(), Qt.Key_K, Qt.ControlModifier)
    qt_app.processEvents()
    controller = window._sharpmod_command_palette
    assert controller.dialog.isVisible()
    first = controller.dialog
    first.reject()
    fresh = help_menu.addAction("Fresh command")
    action.trigger()
    controller.dialog.search.setText("fresh")
    assert controller.dialog is first
    assert controller.dialog.results.item(0).data(Qt.UserRole).action is fresh
    assert len(window.findChildren(CommandPalette)) == 1
    first.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert controller.dialog is None
    action.trigger()
    assert controller.dialog.isVisible()
    assert len(window.findChildren(CommandPalette)) == 1


def test_deleted_dynamic_action_is_removed_without_stale_trigger(command_window, qt_app):
    window, _export, image, _settings, _toggle, _help = command_window
    palette = CommandPalette(window)
    palette.search.setText("png")
    image.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_app.processEvents()
    palette.run_selected()
    assert palette.results.count() == 0
    assert not palette.run_button.isEnabled()


def test_shortcut_conflict_does_not_create_an_ambiguous_command(command_window):
    window, _export, _image, settings, _toggle, _help = command_window
    settings.setShortcuts(["Ctrl+Q", "Ctrl+K"])
    with pytest.raises(ValueError, match="already assigned"):
        install_command_palette(window)
    assert not hasattr(window, "_sharpmod_command_palette")


def test_window_and_palette_are_released_on_delete(qt_app):
    window = QMainWindow()
    window.setAttribute(Qt.WA_DeleteOnClose)
    menu = window.menuBar().addMenu("Help")
    menu.addAction("Test")
    action = install_command_palette(window, menu=menu)
    action.trigger()
    window_ref = weakref.ref(window)
    dialog_ref = weakref.ref(window._sharpmod_command_palette.dialog)
    window.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_app.processEvents()
    del action, menu, window
    gc.collect()
    assert window_ref() is None
    assert dialog_ref() is None
    # A palette is bounded to one live dialog per window, not one per opening.
    assert not any(isinstance(widget, QDialog) and widget.objectName() == "commandPalette"
                   for widget in qt_app.topLevelWidgets())
