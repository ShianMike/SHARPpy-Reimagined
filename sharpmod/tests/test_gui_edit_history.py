"""Readable edit-history regressions over the existing bounded undo/redo."""

from __future__ import annotations

from types import SimpleNamespace

import numpy.ma as ma

from sharpmod.gui_edit_history import (
    ORIGINAL_ROW,
    EditHistoryDialog,
    install_edit_history,
)
from sharpmod.sessions import AnalysisHistory


class _Collection:
    """A minimal stand-in for the parts of a collection history touches."""

    def __init__(self, temperature=20.0):
        self.values = {"tmpc": ma.array([temperature, temperature - 3.0])}
        self.restored = []

    def snapshot(self):
        return {"tmpc": [float(value) for value in self.values["tmpc"]]}

    def restore(self, payload):
        self.values["tmpc"] = ma.array(list(payload["tmpc"]))
        self.restored.append(payload)


class _Widget:
    def __init__(self, *collections):
        self.prof_collections = list(collections)
        self.prof_ids = [f"profile-{index}" for index in range(len(collections))]
        self.pc_idx = 0
        self.updates = 0

    def updateProfs(self):  # noqa: N802 - upstream API
        self.updates += 1

    def setFocus(self):  # noqa: N802 - upstream API
        return None


def _history(widget, monkeypatch, limit=50):
    """Build a real AnalysisHistory over a fake collection snapshot format."""
    from sharpmod import sessions

    monkeypatch.setattr(sessions, "snapshot_collection", lambda c: c.snapshot())

    def _restore(collection, payload):
        collection.restore(payload)
        return collection

    monkeypatch.setattr(sessions, "_restore_collection_in_place", _restore)
    monkeypatch.setattr(
        sessions, "_merge_streamed_snapshot_state", lambda stored, _live: stored
    )
    return AnalysisHistory(widget, limit=limit)


def _record(history, widget, label, temperature):
    captured = history.capture()
    widget.prof_collections[widget.pc_idx].values["tmpc"][0] = temperature
    history.commit(label, captured)


def test_steps_are_numbered_oldest_first_with_applied_state(monkeypatch):
    widget = _Widget(_Collection())
    history = _history(widget, monkeypatch)

    assert history.steps() == ()
    assert history.applied_count() == 0
    assert history.limit == 50

    _record(history, widget, "Edit sounding level", 21.0)
    _record(history, widget, "Edit storm motion", 22.0)

    steps = history.steps()
    assert [step.number for step in steps] == [1, 2]
    assert [step.label for step in steps] == [
        "Edit sounding level",
        "Edit storm motion",
    ]
    assert all(step.applied for step in steps)
    assert all(step.recorded_at is not None for step in steps)
    assert all(step.recorded_text.endswith("UTC") for step in steps)
    assert history.applied_count() == 2

    history.undo()
    steps = history.steps()
    # Numbering is stable across undo, so a row keeps its identity.
    assert [step.number for step in steps] == [1, 2]
    assert [step.applied for step in steps] == [True, False]
    assert [step.state_text for step in steps] == ["Applied", "Undone"]
    assert history.applied_count() == 1


def test_move_to_uses_the_existing_undo_path_and_stays_reversible(monkeypatch):
    widget = _Widget(_Collection())
    history = _history(widget, monkeypatch)
    for index, temperature in enumerate((21.0, 22.0, 23.0), start=1):
        _record(history, widget, f"Edit {index}", temperature)

    assert float(widget.prof_collections[0].values["tmpc"][0]) == 23.0

    assert history.move_to(1) == 1
    assert float(widget.prof_collections[0].values["tmpc"][0]) == 21.0
    assert [step.applied for step in history.steps()] == [True, False, False]

    assert history.move_to(3) == 3
    assert float(widget.prof_collections[0].values["tmpc"][0]) == 23.0

    assert history.move_to(0) == 0
    assert float(widget.prof_collections[0].values["tmpc"][0]) == 20.0
    assert history.can_undo() is False
    assert history.can_redo() is True

    # Out-of-range requests clamp instead of raising or losing entries.
    assert history.move_to(99) == 3
    assert history.move_to(-5) == 0
    assert len(history.steps()) == 3


def test_bounded_history_drops_the_oldest_and_renumbers(monkeypatch):
    widget = _Widget(_Collection())
    history = _history(widget, monkeypatch, limit=2)
    for index, temperature in enumerate((21.0, 22.0, 23.0), start=1):
        _record(history, widget, f"Edit {index}", temperature)

    steps = history.steps()
    assert len(steps) == 2
    assert [step.label for step in steps] == ["Edit 2", "Edit 3"]
    assert [step.number for step in steps] == [1, 2]


def _window(qt_app, widget):
    from qtpy.QtWidgets import QMainWindow

    win = QMainWindow()
    win.menuBar().addMenu("&Edit")
    win.spc_widget = widget
    return win


def test_dialog_lists_profiles_marks_the_current_state_and_moves(qt_app, monkeypatch):
    widget = _Widget(_Collection(), _Collection(30.0))
    history = _history(widget, monkeypatch)
    win = _window(qt_app, widget)
    win._sharpmod_history = history

    for index, temperature in enumerate((21.0, 22.0), start=1):
        _record(history, widget, f"Edit {index}", temperature)

    dialog = EditHistoryDialog(win, history)
    try:
        assert dialog.steps.count() == 3
        assert dialog.steps.item(0).text().startswith(ORIGINAL_ROW)
        assert "Current profile state" in dialog.steps.item(2).text()
        assert "2 recorded, 2 currently applied" in dialog.hint.text()
        assert "last 50 recorded changes" in dialog.hint.text()

        # The current state is selected, so there is nowhere to move.
        assert not dialog.move_button.isEnabled()
        assert "current profile state" in dialog.status.text().casefold()

        dialog.steps.setCurrentRow(0)
        assert dialog.move_button.isEnabled()
        assert "undoes 2 changes" in dialog.status.text()
        dialog.move_to_selected()
        assert history.applied_count() == 0
        assert float(widget.prof_collections[0].values["tmpc"][0]) == 20.0
        # The listener refreshed the panel, and the original row is now current.
        assert "Current profile state" in dialog.steps.item(0).text()
        assert "Undone" in dialog.steps.item(1).text()

        dialog.steps.setCurrentRow(2)
        assert "redoes 2 changes" in dialog.status.text()
        dialog.move_to_selected()
        assert history.applied_count() == 2

        # Undo/Redo in the panel drive the same history as the menu actions.
        dialog._undo()
        assert history.applied_count() == 1
        dialog._redo()
        assert history.applied_count() == 2
    finally:
        dialog.close()
        win.close()


def test_dialog_names_a_closed_profile_and_locks_in_inspect_mode(qt_app, monkeypatch):
    widget = _Widget(_Collection())
    history = _history(widget, monkeypatch)
    win = _window(qt_app, widget)
    win._sharpmod_history = history
    _record(history, widget, "Edit sounding level", 21.0)

    # A change recorded against a collection index that no longer exists must
    # not be attributed to whichever profile now occupies that slot.
    history._undo[0].collection_index = 4
    history._undo[0].recorded_at = None

    mode = SimpleNamespace(allows_edits=lambda: False)
    win._sharpmod_interaction_mode = mode

    dialog = EditHistoryDialog(win, history)
    try:
        text = dialog.steps.item(1).text()
        assert "no longer loaded" in text
        assert "Time not recorded" in text

        dialog.steps.setCurrentRow(0)
        assert not dialog.move_button.isEnabled()
        assert not dialog.undo_button.isEnabled()
        assert "Inspect mode" in dialog.status.text()

        mode.allows_edits = lambda: True
        dialog._mode_changed()
        assert dialog.move_button.isEnabled()
        assert dialog.undo_button.isEnabled()
        assert "undoes 1 change" in dialog.status.text()
    finally:
        dialog.close()
        win.close()


def test_install_action_tracks_recorded_count_and_is_idempotent(qt_app, monkeypatch):
    widget = _Widget(_Collection())
    history = _history(widget, monkeypatch)
    win = _window(qt_app, widget)
    win._sharpmod_history = history

    action = install_edit_history(win)
    try:
        assert action is not None
        assert install_edit_history(win) is action
        assert not action.isEnabled()
        assert action.text() == "Edit history…"

        _record(history, widget, "Edit sounding level", 21.0)
        assert action.isEnabled()
        assert action.text() == "Edit history (1)…"

        history.clear()
        assert not action.isEnabled()
        assert action.text() == "Edit history…"

        edit_menu = next(
            menu
            for menu in win.menuBar().findChildren(type(win.menuBar().addMenu("tmp")))
            if menu.title().replace("&", "").casefold() == "edit"
        )
        assert action in edit_menu.actions()
    finally:
        win.close()


def test_install_is_skipped_without_a_history(qt_app):
    widget = _Widget(_Collection())
    win = _window(qt_app, widget)
    try:
        assert install_edit_history(win) is None
    finally:
        win.close()


def test_real_history_panel_names_the_profile_and_restores_its_values(
    qt_app, tmp_path
):
    """Drive the panel against the real vendored edit and history route."""
    from sharpmod import gui_sessions, gui_viewer, render
    from sharpmod.gui_interaction_mode import MODE_EDIT, install_interaction_mode
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
    try:
        gui_sessions._install_analysis_actions(
            win,
            SimpleNamespace(_open_analysis_session=lambda: None, _settings=None),
        )
        gui_viewer._install_view_controls(win)
        mode = install_interaction_mode(win)
        action = install_edit_history(win)
        assert action is not None
        assert not action.isEnabled()

        history = win._sharpmod_history
        profile = collection.getHighlightedProf()
        level = int(profile.sfc) + 4
        original = float(profile.tmpc[level])

        mode.set_mode(MODE_EDIT, announce=False)
        win.spc_widget.sound.modified.emit(level, {"tmpc": original + 2.0})
        win.spc_widget.sound.modified.emit(level, {"tmpc": original + 4.0})

        assert history.applied_count() == 2
        assert action.isEnabled()
        assert action.text() == "Edit history (2)…"

        dialog = EditHistoryDialog(win, history)
        try:
            rows = [dialog.steps.item(row).text() for row in range(dialog.steps.count())]
            assert len(rows) == 3
            assert rows[0].startswith(ORIGINAL_ROW)
            # The real identity model supplies the location for both changes.
            assert all("OAX" in row for row in rows[1:])
            # A level edit names the quantity that moved, so a wind drag and a
            # temperature drag are not both labelled "Edit sounding level".
            assert all("Edit level temperature" in row for row in rows[1:])
            assert "Current profile state" in rows[2]

            dialog.steps.setCurrentRow(1)
            dialog.move_to_selected()
            assert float(collection.getHighlightedProf().tmpc[level]) == original + 2.0

            dialog.steps.setCurrentRow(0)
            dialog.move_to_selected()
            assert float(collection.getHighlightedProf().tmpc[level]) == original
            assert history.applied_count() == 0

            dialog.steps.setCurrentRow(2)
            dialog.move_to_selected()
            assert float(collection.getHighlightedProf().tmpc[level]) == original + 4.0
        finally:
            dialog.close()
    finally:
        win.close()
        owner.deleteLater()


def test_level_edit_labels_name_the_quantity_that_moved():
    from sharpmod.sessions import _history_label

    assert (
        _history_label("modifyProf", "Edit sounding level", (5, {"tmpc": 21.0}))
        == "Edit level temperature"
    )
    assert (
        _history_label("modifyProf", "Edit sounding level", (5, {"dwpc": 12.0}))
        == "Edit level dewpoint"
    )
    assert (
        _history_label("modifyProf", "Edit sounding level", (5, {"u": 1.0, "v": 2.0}))
        == "Edit level wind"
    )
    assert (
        _history_label(
            "modifyProf", "Edit sounding level", (5, {"tmpc": 21.0, "dwpc": 12.0})
        )
        == "Edit level temperature and dewpoint"
    )
    # Unknown or absent fields keep the existing generic label rather than
    # inventing a description of what changed.
    assert (
        _history_label("modifyProf", "Edit sounding level", (5, {"omeg": 1.0}))
        == "Edit sounding level"
    )
    assert (
        _history_label("modifyProf", "Edit sounding level", (5,))
        == "Edit sounding level"
    )
    assert (
        _history_label("modifyVector", "Edit storm motion", ("right", 1.0, 2.0))
        == "Edit storm motion"
    )


def test_enlarged_rows_stay_whole_and_scrollable(qt_app, monkeypatch):
    """A wrapped row the buttons act on must not be clipped by the viewport."""
    from qtpy.QtGui import QFont

    widget = _Widget(_Collection())
    history = _history(widget, monkeypatch)
    win = _window(qt_app, widget)
    win._sharpmod_history = history
    for index, temperature in enumerate((21.0, 22.0, 23.0, 24.0, 25.0), start=1):
        _record(history, widget, f"Edit {index}", temperature)

    dialog = EditHistoryDialog(win, history)
    try:
        # Force a large interface text size and a short list, the combination
        # that produced a half-rendered first row in the real 200% capture.
        large = QFont(dialog.steps.font())
        large.setPointSizeF(max(22.0, large.pointSizeF() * 2))
        dialog.steps.setFont(large)
        dialog.steps.setFixedHeight(150)
        dialog.show()
        qt_app.processEvents()
        dialog.refresh()
        qt_app.processEvents()

        bar = dialog.steps.verticalScrollBar()
        assert bar.maximum() > 0, "a short list of wrapped rows must scroll"

        # Selecting a row is the only action taken here; the panel itself must
        # bring the selected row fully into view.
        for row in range(dialog.steps.count()):
            dialog.steps.setCurrentRow(row)
            qt_app.processEvents()
            rect = dialog.steps.visualItemRect(dialog.steps.item(row))
            viewport = dialog.steps.viewport().height()
            assert rect.top() >= 0
            assert rect.bottom() <= viewport or rect.height() >= viewport
    finally:
        dialog.close()
        win.close()
