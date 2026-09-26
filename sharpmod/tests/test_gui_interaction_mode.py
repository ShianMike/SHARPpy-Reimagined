"""Explicit Inspect/Edit mode regressions for the sounding viewer."""

from __future__ import annotations

from types import SimpleNamespace

from qtpy.QtCore import QEvent, QPointF, Qt, Signal
from qtpy.QtGui import QAction
from qtpy.QtWidgets import QLabel, QMainWindow, QMenu, QToolBar, QWidget

from sharpmod.gui_interaction_mode import (
    MODE_EDIT,
    MODE_INSPECT,
    InteractionModeController,
    install_interaction_mode_hooks,
)
from sharpmod.gui_sounding_readout import install_linked_readout


class _Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None, _type=None):
        value = self.values.get(key, default)
        return _type(value) if _type is not None else value

    def setValue(self, key, value):  # noqa: N802 - QSettings compatibility
        self.values[key] = value


class _Drag:
    def __init__(self):
        self._drag_idx = 1


class _MouseSurface(QWidget):
    def __init__(self, *, skew=False, parent=None):
        super().__init__(parent)
        self.popupmenu = QMenu(self)
        self.presses = 0
        self.moves = 0
        self.releases = 0
        self.cursor_type = "none"
        self.readout = False
        self.track_cursor = False
        self.drag_tmpc = _Drag()
        self.drag_dwpc = _Drag()
        self.drag_hodo = _Drag()
        self.drag_rm = _Drag()
        self.drag_lm = _Drag()
        self._skew = skew

    def setReadoutCursor(self):  # noqa: N802 - upstream API
        self.readout = True
        self.track_cursor = True

    def setNoCursor(self):  # noqa: N802 - upstream API
        if self._skew:
            self.readout = False
            self.track_cursor = False
        else:
            self.cursor_type = "none"
            self.unsetCursor()

    def mousePressEvent(self, event):  # noqa: N802 - Qt override
        self.presses += 1
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt override
        self.moves += 1
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        self.releases += 1
        super().mouseReleaseEvent(event)


class _History:
    def __init__(self):
        self.listeners = []
        self.undoable = True
        self.redoable = False

    def add_listener(self, callback):
        self.listeners.append(callback)
        callback()

    def can_undo(self):
        return self.undoable

    def can_redo(self):
        return self.redoable


def _add_action(menu, text):
    action = QAction(text, menu)
    menu.addAction(action)
    return action


def _window(settings=None):
    win = QMainWindow()
    win.menuBar().addMenu("&Edit")
    toolbar = QToolBar("View", win)
    toolbar.addAction(QAction("Fit to Window", win))
    win.addToolBar(toolbar)
    win._sharpmod_view_toolbar = toolbar

    sound = _MouseSurface(skew=True, parent=win)
    hodo = _MouseSurface(parent=win)
    sound_actions = {
        text: _add_action(sound.popupmenu, text)
        for text in (
            "No Cursor",
            "Readout Cursor",
            "Modify Surface",
            "Edit Nearest Level\u2026",
            "Reset Skew-T",
        )
    }
    for text in ("No Cursor", "Readout Cursor"):
        sound_actions[text].setCheckable(True)
    hodo_actions = {
        text: _add_action(hodo.popupmenu, text)
        for text in (
            "No Cursor",
            "Bndy Cursor",
            "Reset Hodograph",
            "Reset Storm Motion",
        )
    }
    sw = SimpleNamespace(sound=sound, hodo=hodo)
    history = _History()
    sw._sharpmod_history = history
    win.spc_widget = sw
    win._sharpmod_history = history
    win._sharpmod_undo_action = QAction("Undo", win)
    win._sharpmod_redo_action = QAction("Redo", win)
    win.interpolate = QAction("Interpolate Focused Profile", win)
    win.resetinterp = QAction("Reset Interpolation", win)
    win.resetinterp.setVisible(False)
    return win, sound, hodo, sound_actions, hodo_actions, settings or _Settings()


def _left_event(kind, *, pressed=False):
    buttons = Qt.LeftButton if pressed else Qt.NoButton
    return SimpleNamespace(
        type=lambda: kind,
        button=lambda: Qt.LeftButton,
        buttons=lambda: buttons,
    )


def test_defaults_to_inspect_with_visible_hint_and_locked_mutations(qt_app):
    win, sound, hodo, sound_actions, hodo_actions, settings = _window()

    controller = InteractionModeController(win, settings=settings)

    assert controller.mode == MODE_INSPECT
    assert win._sharpmod_inspect_action.isChecked()
    assert not win._sharpmod_edit_action.isChecked()
    assert sound.readout and sound.track_cursor
    assert sound.cursor().shape() == Qt.CrossCursor
    assert hodo.cursor().shape() == Qt.CrossCursor
    assert "Inspect" in win._sharpmod_mode_hint.text()
    assert isinstance(win._sharpmod_mode_hint, QLabel)
    for action in (
        sound_actions["Modify Surface"],
        sound_actions["Edit Nearest Level\u2026"],
        sound_actions["Reset Skew-T"],
        hodo_actions["Reset Hodograph"],
        hodo_actions["Reset Storm Motion"],
        win.interpolate,
        win.resetinterp,
        win._sharpmod_undo_action,
        win._sharpmod_redo_action,
    ):
        assert not action.isEnabled(), action.text()
    assert not sound_actions["No Cursor"].isEnabled()
    assert not sound_actions["Readout Cursor"].isEnabled()

    win.close()


def test_inspect_blocks_drag_events_but_keeps_skew_readout_events(qt_app):
    win, sound, hodo, _sound_actions, _hodo_actions, settings = _window()
    controller = InteractionModeController(win, settings=settings)

    press = _left_event(QEvent.MouseButtonPress, pressed=True)
    release = _left_event(QEvent.MouseButtonRelease)
    assert not controller.eventFilter(sound, press)
    assert not controller.eventFilter(sound, release)
    assert controller.eventFilter(hodo, press)
    assert controller.eventFilter(hodo, release)

    # Even if an upstream cursor action or future code drifts the Skew-T out of
    # readout state, Inspect remains a hard non-mutation boundary.
    sound.readout = False
    assert controller.eventFilter(sound, press)
    assert controller.eventFilter(sound, release)

    win.close()


def test_edit_mode_enables_real_edit_routes_and_is_remembered(qt_app):
    settings = _Settings()
    win, sound, hodo, sound_actions, hodo_actions, _ = _window(settings)
    controller = InteractionModeController(win, settings=settings)

    win._sharpmod_edit_action.trigger()

    assert controller.mode == MODE_EDIT
    assert settings.values["viewer/interaction_mode"] == MODE_EDIT
    assert not sound.readout
    assert sound.cursor().shape() == Qt.SizeHorCursor
    assert hodo.cursor().shape() == Qt.SizeAllCursor
    assert "Edit" in win._sharpmod_mode_hint.text()
    assert sound_actions["Edit Nearest Level\u2026"].isEnabled()
    assert sound_actions["Modify Surface"].isEnabled()
    assert hodo_actions["Reset Storm Motion"].isEnabled()
    assert win.interpolate.isEnabled()
    assert win._sharpmod_undo_action.isEnabled()
    assert not win._sharpmod_redo_action.isEnabled()
    assert not controller.eventFilter(
        hodo, _left_event(QEvent.MouseButtonPress, pressed=True)
    )
    win.close()

    restored, restored_sound, _hodo, _sa, _ha, _ = _window(settings)
    restored_controller = InteractionModeController(restored, settings=settings)
    assert restored_controller.mode == MODE_EDIT
    assert restored_sound.cursor().shape() == Qt.SizeHorCursor
    restored.close()


def test_invalid_remembered_mode_fails_closed_to_inspect(qt_app):
    settings = _Settings({"viewer/interaction_mode": "drag-everything"})
    win, _sound, _hodo, _sa, _ha, _ = _window(settings)

    controller = InteractionModeController(win, settings=settings)

    assert controller.mode == MODE_INSPECT
    assert settings.values["viewer/interaction_mode"] == MODE_INSPECT
    win.close()


def test_mode_hook_guards_every_existing_mutation_funnel():
    class FakeWidget:
        def __init__(self):
            self.calls = []

        def modifyProf(self, *_args):  # noqa: N802 - upstream API
            self.calls.append("modify profile")

        def modifyVector(self, *_args):  # noqa: N802 - upstream API
            self.calls.append("modify storm motion")

        def interpProf(self):  # noqa: N802 - upstream API
            self.calls.append("interpolate")

        def resetProfModifications(self, *_args):  # noqa: N802 - upstream API
            self.calls.append("reset profile")

        def resetProfInterpolation(self):  # noqa: N802 - upstream API
            self.calls.append("reset interpolation")

        def resetVector(self):  # noqa: N802 - upstream API
            self.calls.append("reset storm motion")

    install_interaction_mode_hooks(FakeWidget)
    widget = FakeWidget()
    blocked = []
    mode = SimpleNamespace(
        allows_edits=lambda: False,
        report_blocked=blocked.append,
    )
    widget._sharpmod_interaction_controller = mode

    widget.modifyProf(1, {"tmpc": 20.0})
    widget.modifyVector("right", 1.0, 2.0)
    widget.interpProf()
    widget.resetProfModifications(["tmpc"])
    widget.resetProfInterpolation()
    widget.resetVector()

    assert widget.calls == []
    assert blocked == [
        "Edit sounding level",
        "Edit storm motion",
        "Interpolate profile",
        "Reset profile edits",
        "Reset interpolation",
        "Reset storm motion",
    ]

    mode.allows_edits = lambda: True
    widget.modifyProf(1, {"tmpc": 20.0})
    widget.modifyVector("right", 1.0, 2.0)
    widget.interpProf()
    widget.resetProfModifications(["tmpc"])
    widget.resetProfInterpolation()
    widget.resetVector()
    assert widget.calls == [
        "modify profile",
        "modify storm motion",
        "interpolate",
        "reset profile",
        "reset interpolation",
        "reset storm motion",
    ]


def test_real_vendored_signal_is_blocked_in_inspect_and_undoable_in_edit(
    qt_app, tmp_path
):
    """Exercise the actual signal -> mode -> history -> collection route."""
    import numpy as np

    from sharpmod import gui_sessions, gui_viewer, render
    from sharpmod.sharptab import interp
    from sharpmod.viz.SPCWindow import compose_window

    render.install_font(qt_app)
    render.install_render_patches()
    source = (
        gui_viewer.Path(gui_viewer.__file__).resolve().parents[1]
        / "examples/soundings/14061619.OAX"
    )
    collection, station = render.decode(str(source))
    gui_viewer._fill_metadata(collection, station)
    config = render.build_config(str(tmp_path))
    win, owner = compose_window(config, collection, mount=True)
    session_controller = SimpleNamespace(
        _open_analysis_session=lambda: None,
        _settings=None,
    )
    try:
        gui_sessions._install_analysis_actions(win, session_controller)
        gui_viewer._install_level_editor(win)
        gui_viewer._install_view_controls(win)
        readout = install_linked_readout(win)
        mode = InteractionModeController(win, settings=_Settings())
        profile = collection.getHighlightedProf()
        idx = int(profile.sfc)
        original = float(profile.tmpc[idx])

        win.spc_widget.sound.readout_pres = 850.0
        win.spc_widget.sound.updateReadout()
        assert readout.sample.pressure_hpa == 850.0
        assert readout.sample.height_agl_m is not None
        assert win.spc_widget.hodo.readout_hght == readout.sample.height_agl_m
        assert "hPa" in readout.label.toolTip()
        assert "m AGL" in readout.label.toolTip()
        assert "m MSL" in readout.label.toolTip()
        assert "T " in readout.label.toolTip()
        assert "Td " in readout.label.toolTip()
        assert "Wind " in readout.label.toolTip()
        assert "nan" not in readout.label.toolTip().casefold()
        assert "-9999" not in readout.label.toolTip()

        hodo = win.spc_widget.hodo
        hodo_mask = (
            np.ma.getmaskarray(hodo.u)
            | np.ma.getmaskarray(hodo.v)
            | np.ma.getmaskarray(hodo.hght)
        )
        pair = next(
            point
            for point in range(len(hodo.u) - 1)
            if not hodo_mask[point] and not hodo_mask[point + 1]
        )
        x_pixels, y_pixels = hodo.uv_to_pix(hodo.u, hodo.v)
        hover_x = float((x_pixels[pair] + x_pixels[pair + 1]) / 2.0)
        hover_y = float((y_pixels[pair] + y_pixels[pair + 1]) / 2.0)
        expected_msl = float((hodo.hght[pair] + hodo.hght[pair + 1]) / 2.0)
        readout.eventFilter(
            hodo,
            SimpleNamespace(
                type=lambda: QEvent.MouseMove,
                position=lambda: QPointF(hover_x, hover_y),
                buttons=lambda: Qt.NoButton,
            ),
        )
        np.testing.assert_allclose(
            win.spc_widget.sound.readout_pres,
            float(interp.pres(profile, expected_msl)),
        )
        np.testing.assert_allclose(readout.sample.height_msl_m, expected_msl)

        win.spc_widget.sound.modified.emit(idx, {"tmpc": original + 3.0})
        assert float(collection.getHighlightedProf().tmpc[idx]) == original
        assert win._sharpmod_history.undo_depth == 0

        mode.set_mode(MODE_EDIT, announce=False)
        win.spc_widget.sound.modified.emit(idx, {"tmpc": original + 3.0})
        assert float(collection.getHighlightedProf().tmpc[idx]) == original + 3.0
        assert win._sharpmod_history.undo_depth == 1

        mode.set_mode(MODE_INSPECT, announce=False)
        assert not win._sharpmod_undo_action.isEnabled()
        mode.set_mode(MODE_EDIT, announce=False)
        assert win._sharpmod_undo_action.isEnabled()
        win._sharpmod_undo_action.trigger()
        np.testing.assert_allclose(
            collection.getHighlightedProf().tmpc[idx], original
        )
    finally:
        win.close()
        owner.deleteLater()
