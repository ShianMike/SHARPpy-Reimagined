"""Interactive sounding-viewer lifecycle regressions."""

from __future__ import annotations

import gc
from types import SimpleNamespace
import weakref

import pytest
import shiboken6
from qtpy import QtCore, QtWidgets

from sharpmod import gui_timeline, gui_viewer


def _flush_deferred_deletes(qt_app) -> None:
    QtCore.QCoreApplication.sendPostedEvents(
        None, QtCore.QEvent.DeferredDelete
    )
    qt_app.processEvents()


def test_composed_viewer_is_destroyed_and_released_on_close(
        qt_app, monkeypatch):
    """Closing a normal viewer must delete it, not leave a hidden tree."""
    window_holder = [QtWidgets.QMainWindow()]
    window_holder[0].spc_widget = QtWidgets.QWidget(window_holder[0])
    controller = QtWidgets.QWidget()
    controller._viewers = []
    controller._config = lambda: object()
    controller._default_parcel = lambda: "mu"
    profile = SimpleNamespace(getMeta=lambda _key: "KOUN")
    renderer = SimpleNamespace(
        align_top_row=lambda *_args: None,
        apply_layout_compensation=lambda *_args: None,
        _grow_for_family_panels=lambda *_args: None,
        enlarge_canvas=lambda *_args: None,
        rebrand_version_label=lambda *_args: None,
    )

    monkeypatch.setattr(gui_viewer, "_ensure_setup", lambda _app: None)
    monkeypatch.setattr(gui_viewer, "_fill_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(gui_viewer, "_render", lambda: renderer)
    monkeypatch.setattr(
        gui_viewer,
        "_compose_window",
        lambda: lambda *_a, **_k: (window_holder[0], controller),
    )
    for name in (
        "_install_export_menu",
        "_install_analysis_actions",
        "_install_units_menu",
        "_install_data_inspector",
        "_apply_unit_preferences_to_window",
        "_install_parcel_selector",
        "_install_level_editor",
        "_apply_default_parcel_to_window",
        "_install_tip_bar",
        "_fit_window_to_screen",
        "_finalize_scaled_fit",
    ):
        monkeypatch.setattr(gui_viewer, name, lambda *_a, **_k: None)
    monkeypatch.setattr(
        gui_timeline, "install_timeline_controls", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        gui_viewer,
        "QTimer",
        SimpleNamespace(singleShot=lambda _delay, callback: callback()),
    )
    cycle_collections = []
    monkeypatch.setattr(
        gui_viewer,
        "_collect_closed_viewer_cycles",
        lambda: cycle_collections.append(True),
    )

    composed = gui_viewer.compose_interactive(
        object(), profile, controller, stn_id="KOUN"
    )
    window_holder.clear()
    controller._viewers.append(composed)
    destroyed = []
    composed.destroyed.connect(lambda *_args: destroyed.append(True))
    wrapper_ref = weakref.ref(composed)

    assert composed.testAttribute(QtCore.Qt.WA_DeleteOnClose)
    assert composed.close() is True
    _flush_deferred_deletes(qt_app)

    assert destroyed == [True]
    assert controller._viewers == []
    assert cycle_collections == [True]
    assert not shiboken6.isValid(composed)
    with pytest.raises(RuntimeError, match="already deleted"):
        composed.windowTitle()

    del composed
    gc.collect()
    assert wrapper_ref() is None

    controller.deleteLater()
    _flush_deferred_deletes(qt_app)

def _closures_capturing(code, path, out, name="win"):
    """Recursively collect nested functions whose closure captures ``name``."""
    for const in code.co_consts:
        if hasattr(const, "co_freevars"):
            if name in const.co_freevars:
                out.append(f"{path} -> {const.co_name}")
            _closures_capturing(const, f"{path}/{const.co_name}", out, name)


def test_no_installer_handler_closes_over_the_window():
    """A handler capturing ``win`` crashes teardown, not just leaks memory.

    Every ``_install_*`` helper attaches actions and widgets that are children
    of the sounding window, and Qt holds their signal connections C++-side. A
    closure that captures ``win`` strongly therefore closes

        win -> action -> connection -> closure -> win

    across an edge Python's cyclic collector cannot traverse. The window's
    wrapper survives to interpreter exit, by which point Qt has torn the C++
    side down, and freeing it is an access violation: 0xC0000005 with no Python
    traceback, because it happens after the last frame is gone.

    That was not theoretical. ``_install_tip_bar``'s "Full guide" button used a
    ``lambda`` capturing ``win`` and crashed the test process on exit in 6 of 14
    runs; holding the window weakly gave 0 in 14. Intermittent, silent, and it
    would abort the app when the user quits it.

    Checked against bytecode rather than by reading the source, because the
    condition is exactly "``win`` is in ``co_freevars``" -- and the fix (rebind
    ``win = win_ref()`` inside the handler, making it a local) is invisible to a
    grep for ``weakref``.

    Discovery is deliberately wide. A first version of this test looked only at
    ``gui_viewer`` and ``gui_sessions`` for names starting with ``_install`` or
    ``_bind``, and missed ``gui_timeline.install_timeline_controls`` -- which has
    no leading underscore, lives in a third module, and runs for every forecast
    sounding. A guard narrower than the defect it guards is worse than none,
    because it reads as coverage.

    The same rule covers ``dialog``, for the same reason: a preferences dialog
    built with ``parent=None`` owns its own C++ lifetime exactly as a top-level
    window does, so a handler on one of its children that captures it strongly
    retains it just as durably.
    """
    from sharpmod import gui_common, gui_sessions, gui_settings, gui_timeline

    offenders = []
    audited = []
    modules = (gui_viewer, gui_sessions, gui_timeline, gui_common, gui_settings)
    for module in modules:
        for name in sorted(dir(module)):
            if not ("install" in name or "bind" in name):
                continue
            code = getattr(getattr(module, name), "__code__", None)
            if code is None:
                continue
            params = code.co_varnames[:code.co_argcount]
            owner = next((p for p in ("win", "dialog") if p in params), None)
            if owner is None:
                continue
            audited.append(f"{module.__name__}.{name}")
            _closures_capturing(code, name, offenders, owner)

    # Guards the guard: if discovery ever stops finding installers, this test
    # would pass by examining nothing. Named explicitly rather than counted, so
    # a rename cannot quietly drop one out of scope.
    assert len(audited) >= 12, f"only audited {audited}"
    for required in ("sharpmod.gui_timeline.install_timeline_controls",
                     "sharpmod.gui_viewer._install_tip_bar",
                     "sharpmod.gui_viewer._install_export_menu",
                     "sharpmod.gui_settings._install_palette_preview",
                     "sharpmod.gui_sessions._install_analysis_actions"):
        assert required in audited, (
            f"{required} is no longer being audited; discovery is narrower "
            f"than the defect again. Audited: {audited}")

    assert not offenders, (
        "these handlers capture their own top-level window or dialog strongly, "
        "which makes an uncollectable cycle and an intermittent segfault at "
        "exit; resolve it from a weakref into a local of the same name, or pass "
        "in only the values the handler needs:\n  " + "\n  ".join(offenders))
