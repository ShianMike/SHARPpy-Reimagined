"""Full screen (F11), shared by the picker and the sounding window."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtpy.QtWidgets import QMainWindow, QMenuBar

from sharpmod.gui_common import _install_fullscreen_action

#: Label of the hidden Escape action the helper installs on the window.
LEAVE = "Leave Full Screen"


@pytest.fixture
def window(qt_app):
    """A bare window with the full-screen action installed."""
    win = QMainWindow()
    menu = win.menuBar().addMenu("View")
    action = _install_fullscreen_action(win, menu)
    win.show()
    for _ in range(6):
        qt_app.processEvents()
    escape = next(a for a in win.actions() if a.text() == LEAVE)
    yield win, menu, action, escape
    win.close()


def _settle(qt_app, times=8):
    for _ in range(times):
        qt_app.processEvents()


def test_action_is_a_checkable_f11_menu_item(window):
    _win, menu, action, _escape = window
    assert action.shortcut().toString() == "F11"
    assert action.isCheckable()
    assert action in menu.actions()


def test_toggling_enters_and_leaves_full_screen(window, qt_app):
    win, _menu, action, _escape = window
    assert not win.isFullScreen()

    action.setChecked(True)
    _settle(qt_app)
    assert win.isFullScreen()

    action.setChecked(False)
    _settle(qt_app)
    assert not win.isFullScreen()


def test_leaving_full_screen_restores_a_maximized_window(window, qt_app):
    """``showNormal`` is the obvious call here and it is wrong.

    From full screen it drops a window that was maximized back to its small
    floating size, so pressing F11 twice would not return the user where they
    started.
    """
    win, _menu, action, _escape = window
    win.showMaximized()
    _settle(qt_app, 10)
    if not win.isMaximized():
        pytest.skip("platform did not honour showMaximized")

    action.setChecked(True)
    _settle(qt_app)
    assert win.isFullScreen()

    action.setChecked(False)
    _settle(qt_app, 10)
    assert win.isMaximized(), "a maximized window came back un-maximized"
    assert not win.isFullScreen()


def test_leaving_full_screen_keeps_a_normal_window_normal(window, qt_app):
    win, _menu, action, _escape = window
    assert not win.isMaximized()

    action.setChecked(True)
    _settle(qt_app)
    action.setChecked(False)
    _settle(qt_app, 10)

    assert not win.isFullScreen()
    assert not win.isMaximized(), "a normal window came back maximized"


def test_escape_is_inert_until_full_screen(window, qt_app):
    """An always-enabled Escape shortcut would swallow the key window-wide.

    Nothing else in the sounding window binds Escape today, but claiming it
    unconditionally would quietly take it from anything added later.
    """
    win, _menu, action, escape = window
    assert not escape.isEnabled()

    action.setChecked(True)
    _settle(qt_app)
    assert escape.isEnabled()

    action.setChecked(False)
    _settle(qt_app)
    assert not escape.isEnabled()


def test_escape_leaves_full_screen_and_clears_the_checkmark(window, qt_app):
    win, _menu, action, escape = window
    action.setChecked(True)
    _settle(qt_app)

    escape.trigger()
    _settle(qt_app)

    assert not win.isFullScreen()
    assert not action.isChecked()


def test_checkmark_resyncs_when_the_menu_opens(window, qt_app):
    """The window can leave full screen without going through either action.

    A window-manager shortcut, for instance. The menu is the only place the
    checkmark is visible, so it is re-read there.
    """
    win, menu, action, _escape = window
    win.showFullScreen()          # bypass the action entirely
    _settle(qt_app)
    assert win.isFullScreen()
    assert not action.isChecked(), "expected the checkmark to be stale here"

    menu.aboutToShow.emit()
    assert action.isChecked()


def test_full_screen_reaches_the_sounding_window_view_menu(qt_app):
    """The viewer wires the shared helper into its View menu."""
    from sharpmod import gui_viewer

    win = QMainWindow()
    gui_viewer._install_view_controls(win)
    qt_app.processEvents()
    try:
        labels = [a.text() for a in win._sharpmod_view_menu.actions() if a.text()]
        assert any("Full Screen" in label for label in labels), labels
        assert getattr(win, "_sharpmod_fullscreen_action", None) is not None
    finally:
        win.close()


def test_the_action_does_not_pin_the_window(qt_app):
    """The helper must not keep the window's wrapper alive.

    Both actions are children of the window, so Qt holds their connections
    C++-side where Python's cyclic GC cannot see them. A closure capturing the
    window strongly therefore pins it for the life of the process, and every
    viewer open/close cycle retains a whole sounding window. The first version of
    this helper did exactly that, and
    ``test_gui_viewer_sidebar::test_panel_holds_the_window_weakly`` caught it --
    asserted here too, so the helper is guarded where it is defined.
    """
    import gc
    import weakref

    win = QMainWindow()
    menu = win.menuBar().addMenu("View")
    _install_fullscreen_action(win, menu)
    win.show()
    _settle(qt_app)

    ref = weakref.ref(win)
    win.close()
    del win, menu
    gc.collect()
    qt_app.processEvents()
    gc.collect()

    assert ref() is None, "the full-screen action kept the window alive"


def test_full_screen_is_documented_in_the_guide():
    from sharpmod.gui_common import CONTROLS_HTML

    assert "F11" in CONTROLS_HTML
    assert "Escape" in CONTROLS_HTML
