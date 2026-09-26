"""Reference/visibility preserve scientific identity and native paint contracts."""

import pytest
from qtpy.QtWidgets import QWidget
from qtpy.QtCore import Qt

from sharpmod import gui_viewer
from sharpmod.gui_collection_state import install_collection_state
from sharpmod.tests.test_gui_viewer_sidebar import _make, _StubCollection


@pytest.fixture
def profiles(qt_app):
    win, widget, panel, dock = _make(qt_app, {
        "A": _StubCollection("KOUN"), "B": _StubCollection("KTLX", model="GFS"),
        "C": _StubCollection("KFWS", model="NAM"),
    })
    yield win, widget, panel, dock
    win.close()


def test_pin_survives_hide_show_focus_and_organization(profiles):
    win, widget, panel, _dock = profiles
    state = win._sharpmod_collection_state
    original_ids, original_collections = list(widget.prof_ids), list(widget.prof_collections)
    panel._pin.click()
    assert state.reference_id == "A"
    panel._visibility.click()
    assert "A" in state.hidden_ids and state.reference_id == "A"
    assert widget.prof_ids == original_ids and widget.prof_collections == original_collections
    assert state.focused_id() == "B"
    assert panel._list.currentItem().data(Qt.UserRole) == "A"
    assert "Hidden" in panel._list.currentItem().text()
    assert "Pinned reference" in panel._list.currentItem().text()
    assert panel._visibility.text() == "Show selected"
    panel._sort_actions["source"].trigger()
    panel._group_actions["location"].trigger()
    assert state.reference_id == "A" and "A" in state.hidden_ids
    panel._visibility.click()
    assert "A" not in state.hidden_ids and state.reference_id == "A"
    assert widget.prof_ids == original_ids and widget.prof_collections == original_collections


def test_hidden_focus_boundary_and_last_visible_guard(profiles):
    win, widget, panel, _dock = profiles
    state = win._sharpmod_collection_state
    feedback = []
    state.feedback.connect(lambda message, level: feedback.append((message, level)))
    assert state.set_visible("C", False)
    assert widget.setProfileCollection("C") is False
    assert widget.pc_idx == 0
    assert "Show it" in feedback[-1][0] or "Show it".lower() in feedback[-1][0].lower()
    assert state.set_visible("A", False)
    assert state.focused_id() == "B"
    assert not state.set_visible("B", False)
    assert state.hidden_ids == {"A", "C"}
    widget.pc_idx = 2  # The upstream Space path sets the index before updateProfs.
    widget.updateProfs()
    assert widget.pc_idx == 1 and state.focused_id() == "B"
    assert panel._visibility.text() in {"Hide selected", "Show selected"}


def test_removing_reference_unpins_with_visible_explanation(profiles):
    win, widget, panel, _dock = profiles
    state = win._sharpmod_collection_state
    assert state.pin("B")
    widget.setProfileCollection("B")
    panel._remove.click()
    assert state.reference_id is None
    assert "B" not in widget.prof_ids
    assert panel._collection_feedback.isVisibleTo(panel)
    assert "reference" in panel._collection_feedback.text()


def test_remove_last_visible_is_guarded_without_revealing_hidden_data(profiles):
    win, widget, panel, _dock = profiles
    state = win._sharpmod_collection_state
    state.set_visible("B", False)
    state.set_visible("C", False)
    assert win.rmProfileCollection("A") is False
    assert widget.prof_ids == ["A", "B", "C"]
    assert state.hidden_ids == {"B", "C"}
    assert "Show another" in panel._collection_feedback.text()


def test_actual_session_ui_capture_restore_keeps_reference_visibility_and_organization(profiles):
    import json
    from sharpmod.gui_sessions import _viewer_session_ui_state, _apply_viewer_session_state

    win, widget, panel, _dock = profiles
    state = win._sharpmod_collection_state
    state.pin("B")
    state.set_visible("B", False)
    panel._group_actions["source"].trigger()
    panel._sort_actions["location"].trigger()
    panel._search.setText("KTLX")
    saved = json.loads(json.dumps(_viewer_session_ui_state(win)))
    assert saved["collections"]["reference_id"] == "B"
    assert saved["collections"]["hidden_ids"] == ["B"]
    state.pin("A")
    state.set_visible("B", True)
    panel._search.clear()
    panel._group_actions["none"].trigger()
    _apply_viewer_session_state(win, 0, saved)
    assert state.reference_id == "B" and state.hidden_ids == {"B"}
    assert widget.prof_ids == ["A", "B", "C"]
    assert panel._group_key == "source" and panel._sort_key == "location"
    assert panel._search.text() == "KTLX"
    assert "Hidden (display only)" in panel._reference.text()


def test_corrupt_future_and_all_hidden_restore_are_safe(profiles):
    win, widget, panel, _dock = profiles
    state = win._sharpmod_collection_state
    state.pin("A")
    state.set_visible("B", False)
    assert not state.restore_session_state({"version": 2, "hidden_ids": [], "reference_id": None})
    assert not state.restore_session_state({"version": True, "hidden_ids": []})
    assert not state.restore_session_state({"version": 1, "hidden_ids": "B"})
    assert state.reference_id == "A" and state.hidden_ids == {"B"}
    assert state.restore_session_state({"version": 1, "hidden_ids": ["A", "B", "C", "gone"], "reference_id": "A"})
    assert state.focused_id() == "A" and state.hidden_ids == {"B", "C"}
    assert "kept visible" in panel._collection_feedback.text()
    panel.restore_session_state({"version": 1, "sort": [], "group": {}, "query": None})
    assert panel._sort_key == "loaded" and panel._group_key == "none" and panel._search.text() == ""
    assert widget.prof_ids == ["A", "B", "C"]


def test_comparison_reference_tracks_id_after_an_earlier_collection_is_removed(qt_app):
    from sharpmod.tests.test_gui_analysis_workspace import _make as make_analysis, _Collection

    win, workspace, _engine = make_analysis(qt_app, [_Collection("A"), _Collection("B"), _Collection("C")])
    try:
        workspace.compare_reference.setCurrentIndex(1)
        gui_viewer._install_sounding_sidebar(win)
        state = win._sharpmod_collection_state
        state.pin("profile-1")
        workspace._populate_controls()
        assert workspace.compare_reference.currentData() == 1
        widget = win.spc_widget
        reference = widget.prof_collections[1]
        widget.prof_collections.pop(0)
        widget.prof_ids.pop(0)
        widget.pc_idx = 0
        widget.updateProfs()
        workspace._populate_controls()
        assert state.reference_id == "profile-1"
        assert workspace.compare_reference.currentData() == 0
        assert widget.prof_collections[workspace.compare_reference.currentData()] is reference
        state.set_visible("profile-1", False)
        workspace._populate_controls()
        assert state.reference_id == "profile-1"
        assert workspace.compare_reference.currentData() == 0
        assert reference in widget.prof_collections
    finally:
        win.close()


def test_real_science_canvas_hide_show_changes_output_not_source_arrays(qt_app, tmp_path):
    import numpy as np
    from pathlib import Path
    from sharpmod import render
    from sharpmod.viz.SPCWindow import compose_window

    render.install_font(qt_app)
    render.install_render_patches()
    source = Path(gui_viewer.__file__).resolve().parents[1] / "examples/soundings/14061619.OAX"
    collection, _station = render.decode(str(source))
    gui_viewer._fill_metadata(collection, _station)
    config = render.build_config(str(tmp_path))
    win, controller = compose_window(config, collection, mount=True)
    try:
        render.align_top_row(win)
        render.apply_layout_compensation(win.spc_widget)
        for _ in range(4):
            qt_app.processEvents()
        render._grow_for_family_panels(win)
        for _ in range(4):
            qt_app.processEvents()
        render.enlarge_canvas(win)
        gui_viewer._install_sounding_sidebar(win)
        assert not getattr(win, "_sharpmod_install_failures", ())
        original = collection.getHighlightedProf()
        original_values = {key: np.ma.array(getattr(original, key), copy=True)
                           for key in ("pres", "tmpc", "dwpc", "u", "v")}
        child, child_station = render.decode(str(source))
        gui_viewer._fill_metadata(child, child_station)
        child.setMeta("loc", "OAX-ALT")
        child_profile = child.getHighlightedProf()
        child_profile.tmpc = np.ma.array(child_profile.tmpc, copy=True) + 1.0
        assert child.getCurrentDate() == collection.getCurrentDate()
        win.addProfileCollection(child, focus=False, check_integrity=False)
        widget = win.spc_widget
        state = win._sharpmod_collection_state
        child_id = widget.prof_ids[-1]
        state.pin(child_id)
        source_ids, source_objects = list(widget.prof_ids), list(widget.prof_collections)
        canvas_objects = widget.sound.prof_collections
        win.resize(1800, 1200)
        win.show()
        for _ in range(4):
            qt_app.processEvents()
        widget.updateProfs()
        assert widget.sound.height() > 200, "Use the realized scientific canvas, not a collapsed layout"
        before = widget.sound.grab().toImage()
        assert state.set_visible(child_id, False)
        qt_app.processEvents()
        hidden = widget.sound.grab().toImage()
        assert before != hidden, "The actual skew-T must no longer draw the hidden profile"
        assert widget.prof_ids == source_ids and widget.prof_collections == source_objects
        assert widget.sound.prof_collections is canvas_objects
        assert state.reference_id == child_id
        for key, values in original_values.items():
            np.testing.assert_array_equal(np.ma.getmaskarray(getattr(original, key)), np.ma.getmaskarray(values))
            np.testing.assert_array_equal(np.ma.getdata(getattr(original, key)), np.ma.getdata(values))
        assert state.set_visible(child_id, True)
        qt_app.processEvents()
        shown = widget.sound.grab().toImage()
        assert before == shown, "Showing the unchanged data must restore the same actual rendering"
        assert state.remove(child_id)
        assert child_id not in widget.prof_ids and len(state.closed) == 1
        assert state.reopen(child_id)
        qt_app.processEvents()
        assert widget.prof_ids == source_ids and widget.prof_collections == source_objects
        assert state.reference_id == child_id
        assert [menu.title() for menu in win.menu_items if menu.menuAction().isVisible()] == source_ids
        assert before == widget.sound.grab().toImage()
    finally:
        win.close()
        controller.deleteLater()


class _PaintCanvas(QWidget):
    def __init__(self, collections, parent):
        super().__init__(parent)
        self.prof_collections = list(collections)
        self.pc_idx = 0
        self.seen = []
        self.fail = False

    def plotData(self):  # noqa: N802
        self.seen.append((tuple(self.prof_collections), self.pc_idx))
        if self.fail:
            raise ValueError("paint failure fixture")


@pytest.mark.parametrize("plain_callback", [False, True])
def test_paint_filter_only_changes_canvas_view_and_always_restores(qt_app, plain_callback):
    from sharpmod.tests.test_gui_viewer_sidebar import _StubWindow, _StubWidget

    widget = _StubWidget({"A": _StubCollection("A"), "B": _StubCollection("B")})
    win = _StubWindow(widget)
    widget.sound = _PaintCanvas(widget.prof_collections, win)
    canvas = widget.sound
    if plain_callback:
        original = canvas.plotData
        canvas.plotData = lambda: original()
    try:
        state = install_collection_state(win)
        assert install_collection_state(win) is state
        assert state.set_visible("B", False)
        canvas_ids = canvas.prof_collections
        canvas.plotData()
        assert canvas.seen[-1] == ((widget.prof_collections[0],), 0)
        assert canvas.prof_collections is canvas_ids
        assert canvas.pc_idx == 0
        assert widget.prof_ids == ["A", "B"] and len(widget.prof_collections) == 2
        canvas.fail = True
        with pytest.raises(ValueError, match="paint failure fixture"):
            canvas.plotData()
        assert canvas.prof_collections is canvas_ids and canvas.pc_idx == 0
    finally:
        win.close()
