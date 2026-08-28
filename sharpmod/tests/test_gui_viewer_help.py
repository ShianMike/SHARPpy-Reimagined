"""The sounding window's Help menu and the interaction guide.

The guide used to be reachable from the sounding window only through the "Full
guide" button on the tips strip. That strip has a dismiss button whose preference
persists, so closing it removed the last route to the guide permanently -- the
window had no Help menu at all.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtpy.QtCore import QSettings, QTimer
from qtpy.QtWidgets import QDialog, QMainWindow, QTextBrowser

from sharpmod import gui_viewer
from sharpmod.gui_common import CONTROLS_HTML


class _Controller:
    """Just the settings object the tips strip reads and writes."""

    def __init__(self, path, hidden):
        self._settings = QSettings(str(path), QSettings.IniFormat)
        self._settings.setValue("hide_tips", hidden)


def _window(qt_app, tmp_path, tips_hidden=False):
    controller = _Controller(tmp_path / "settings.ini", tips_hidden)
    win = QMainWindow()
    gui_viewer._install_view_controls(win)
    gui_viewer._install_tip_bar(win, controller)
    gui_viewer._install_help_menu(win)
    qt_app.processEvents()
    return win, controller


@pytest.fixture
def viewer_window(qt_app, tmp_path):
    win, controller = _window(qt_app, tmp_path)
    yield win, controller
    win.close()


def test_the_sounding_window_has_a_help_menu(viewer_window):
    win, _controller = viewer_window
    titles = [action.text() for action in win.menuBar().actions()]
    assert any("Help" in title for title in titles), titles


def test_the_guide_is_on_f1(viewer_window):
    win, _controller = viewer_window
    guide = win._sharpmod_help_menu.actions()[0]
    assert "Controls" in guide.text()
    assert guide.shortcut().toString() == "F1"


def test_guide_stays_reachable_after_the_tips_strip_is_dismissed(
        qt_app, tmp_path):
    """The regression: dismissing the strip must not hide the guide forever."""
    win, _controller = _window(qt_app, tmp_path, tips_hidden=True)
    try:
        # isVisibleTo, not isVisible: these tests never show the window, and
        # isVisible() is False for every descendant of an unshown window -- so a
        # "hidden" assertion written with it passes regardless of the code.
        assert not win._sharpmod_tips.isVisibleTo(win.menuBar()), (
            "fixture did not reproduce a dismissed strip")
        guide = win._sharpmod_help_menu.actions()[0]
        assert guide.isEnabled(), (
            "the guide is unreachable once the tips strip is dismissed")
    finally:
        win.close()


def test_show_tips_restores_the_strip_and_clears_the_preference(qt_app,
                                                                tmp_path):
    win, controller = _window(qt_app, tmp_path, tips_hidden=True)
    try:
        toggle = win._sharpmod_tips_action
        assert toggle.isCheckable() and not toggle.isChecked()

        menubar = win.menuBar()
        toggle.setChecked(True)
        qt_app.processEvents()
        assert win._sharpmod_tips.isVisibleTo(menubar)
        # Persisted, so the strip is still there next time the viewer opens.
        assert controller._settings.value("hide_tips", True, bool) is False

        toggle.setChecked(False)
        qt_app.processEvents()
        assert not win._sharpmod_tips.isVisibleTo(menubar)
        assert controller._settings.value("hide_tips", False, bool) is True
    finally:
        win.close()


def test_show_tips_opens_checked_when_the_strip_is_showing(viewer_window):
    """The default case, and the one that was wrong.

    ``_install_help_menu`` latched the checkmark from ``tips.isVisible()``, which
    is False for every descendant of an unshown window -- and it runs while
    ``compose_interactive`` still has the window hidden. So with the strip on
    screen (the default, ``hide_tips`` unset) the menu opened *unchecked*: the
    first click ran the "show" branch on an already-visible strip, changing
    nothing on screen, and hiding the strip took two clicks.

    Note this cannot be written with ``isVisible()`` either -- that is exactly
    the trap. ``isVisibleTo`` reports the widget's own visibility relative to an
    ancestor, ignoring whether the top level has been shown.
    """
    win, _controller = viewer_window
    toggle = win._sharpmod_tips_action

    assert win._sharpmod_tips.isVisibleTo(win.menuBar()), (
        "fixture did not reproduce a visible strip")
    assert toggle.isChecked(), (
        "the strip is showing but its menu item is unchecked, so the first "
        "click is a no-op and hiding the strip takes two")


def test_one_click_hides_a_showing_strip(viewer_window, qt_app):
    """The user-visible consequence of the state above, end to end."""
    win, controller = viewer_window
    menubar = win.menuBar()
    toggle = win._sharpmod_tips_action
    assert win._sharpmod_tips.isVisibleTo(menubar)

    toggle.trigger()
    qt_app.processEvents()

    assert not win._sharpmod_tips.isVisibleTo(menubar), (
        "one click on a checked Show Tips item did not hide the strip")
    assert controller._settings.value("hide_tips", False, bool) is True


def _open_guide(qt_app, win):
    """Open the modal guide, capture its geometry, and close it."""
    captured = {}

    def inspect():
        for widget in qt_app.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                captured["size"] = (widget.width(), widget.height())
                bodies = widget.findChildren(QTextBrowser)
                # Scalars only, deliberately: the dialog is deleteLater'd once
                # exec unwinds, so any widget reference kept past the close()
                # below would outlive its C++ object and raise on touch.
                captured["body_count"] = len(bodies)
                if bodies:
                    captured["scrollable"] = (
                        bodies[0].verticalScrollBarPolicy())
                    captured["overflow"] = (
                        bodies[0].verticalScrollBar().maximum())
                widget.close()
                return

    QTimer.singleShot(0, inspect)
    win._sharpmod_help_menu.actions()[0].trigger()
    for _ in range(6):
        qt_app.processEvents()
    return captured


def test_guide_fits_a_1080p_screen(viewer_window, qt_app):
    """A message box laid the guide out at its full height and could not scroll.

    With the guide expanded to cover both zoom gestures it reached about
    400x1224 px -- too narrow to read comfortably and taller than a 1080p work
    area, with the overflow simply unreachable.
    """
    win, _controller = viewer_window
    captured = _open_guide(qt_app, win)

    assert captured.get("size"), "the guide did not open"
    width, height = captured["size"]
    assert height <= 900, f"guide is {height}px tall; will not fit 1080p"
    assert width >= 600, f"guide is only {width}px wide; prose needs room"


def test_guide_body_scrolls(viewer_window, qt_app):
    """Long content must be reachable, which a message box could not manage.

    Two facts, both needed. The content genuinely overflows 760x620 -- so if the
    body could not scroll, the overflow would be unreachable, which is the
    original bug -- and the scrollbar policy permits scrolling. Asserting only
    that a ``QTextBrowser`` exists, as this once did, would hold just as well
    with scrolling switched off.
    """
    from qtpy.QtCore import Qt

    win, _controller = viewer_window
    captured = _open_guide(qt_app, win)

    assert captured.get("body_count"), "the guide has no scrollable body"
    assert captured["scrollable"] != Qt.ScrollBarAlwaysOff, (
        "the guide body cannot scroll, so content past the fold is unreachable")
    assert captured.get("overflow", 0) > 0, (
        "the guide fits without scrolling, so this test is no longer measuring "
        "anything -- either the content shrank or the dialog grew")


def test_the_guide_does_not_accumulate_on_the_window(viewer_window, qt_app):
    """Every F1 press must leave the window as it found it.

    The dialog is parented to the window so it centres on it and stays in
    front, which also means Qt keeps it alive until the *window* dies. Without
    an explicit ``deleteLater`` each press left another 760x620 dialog and its
    fully populated ``QTextBrowser`` attached -- and this is a guide people open
    repeatedly while learning the zoom gestures. ``QMessageBox.information``
    had no such problem, so the leak arrived with the scrollable rewrite.

    Note the disposal is *not* ``WA_DeleteOnClose``: that attribute deletes the
    dialog from inside the close that ends the modal loop, while ``QDialog::exec``
    is still on the stack, and it segfaulted on teardown.
    """
    from qtpy.QtCore import QEvent

    win, _controller = viewer_window
    before = len(win.findChildren(QDialog))

    for _ in range(3):
        assert _open_guide(qt_app, win).get("size"), "the guide did not open"
        # DeferredDelete is not dispatched by processEvents(), so drain it
        # explicitly rather than depending on which event loop is unwinding.
        qt_app.sendPostedEvents(None, QEvent.DeferredDelete)
        qt_app.processEvents()

    assert len(win.findChildren(QDialog)) == before, (
        f"{len(win.findChildren(QDialog)) - before} guide dialog(s) still "
        f"parented to the window after opening and closing it three times")


@pytest.mark.parametrize("phrase,why", [
    ("scroll", "the per-panel zoom gesture"),
    ("Ctrl+scroll", "the whole-sounding zoom gesture"),
    ("Ctrl+0", "fit to window"),
    ("Ctrl+1", "actual size"),
    ("Ctrl+B", "the sounding panel"),
    ("F1", "this guide itself"),
])
def test_guide_documents_the_zoom_and_view_controls(phrase, why):
    """The guide's single line about the wheel was what left zoom unexplained.

    It said only "zoom the Skew-T or hodograph": no direction, no mention that
    zooming out stops at the default view, and nothing distinguishing per-panel
    zoom from whole-sounding zoom on the same gesture.
    """
    assert phrase in CONTROLS_HTML, f"guide never mentions {why} ({phrase})"


def test_guide_states_the_zoom_direction_and_its_limit():
    """Both were absent, and both are what made the zoom look broken."""
    assert "up</b> to magnify" in CONTROLS_HTML, (
        "the guide does not say which direction magnifies")
    assert "stops at the normal view" in CONTROLS_HTML, (
        "the guide does not say that zooming out stops at the default, so "
        "scrolling that way at the default reads as nothing happening")


def test_dismissing_with_the_x_button_unchecks_the_menu_item(viewer_window,
                                                             qt_app):
    """The strip's own X must leave the Help menu telling the truth.

    Fixing the initial checkmark state was not enough. ``_dismiss`` hid the
    strip without touching the action, so after clicking the X the menu still
    read "Show Interaction Tips" as checked while the strip was gone -- the same
    two-click restore, reached from the other direction, and by the likelier
    route: bringing the strip back after dismissal is the main reason that menu
    item exists at all.
    """
    win, controller = viewer_window
    menubar = win.menuBar()
    toggle = win._sharpmod_tips_action
    assert win._sharpmod_tips.isVisibleTo(menubar) and toggle.isChecked()

    # The X button is the last child button on the strip.
    from qtpy.QtWidgets import QToolButton

    buttons = win._sharpmod_tips.findChildren(QToolButton)
    close_btn = buttons[-1]
    close_btn.click()
    qt_app.processEvents()

    assert not win._sharpmod_tips.isVisibleTo(menubar), "the X did not hide it"
    assert not toggle.isChecked(), (
        "the strip is hidden but its menu item still reads checked, so the "
        "next click is a no-op and restoring it takes two")
    assert controller._settings.value("hide_tips", False, bool) is True

    # And one click genuinely brings it back.
    toggle.trigger()
    qt_app.processEvents()
    assert win._sharpmod_tips.isVisibleTo(menubar)
    assert controller._settings.value("hide_tips", True, bool) is False


def test_dismissing_writes_the_preference_exactly_once(viewer_window, qt_app):
    """Syncing the checkmark must not re-enter the toggle handler.

    ``setChecked(False)`` on the action emits ``toggled``, which runs
    ``_toggle_tips`` and rewrites ``hide_tips``. Harmless here because both
    paths write True, but it would silently undo the dismissal if either side
    changed, so the signal is blocked and this pins that.
    """
    win, controller = viewer_window
    from qtpy.QtWidgets import QToolButton

    writes = []
    original = controller._settings.setValue

    def _record(key, value):
        if key == "hide_tips":
            writes.append(value)
        original(key, value)

    controller._settings.setValue = _record
    try:
        win._sharpmod_tips.findChildren(QToolButton)[-1].click()
        qt_app.processEvents()
    finally:
        controller._settings.setValue = original

    assert writes == [True], f"hide_tips written {writes}, expected [True]"
