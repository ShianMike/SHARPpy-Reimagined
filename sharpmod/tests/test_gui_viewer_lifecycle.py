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
