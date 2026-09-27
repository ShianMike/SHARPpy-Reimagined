"""Closed data is bounded, explicitly restored, and never silently replaced."""

import gc
import weakref

import pytest
from qtpy.QtCore import QCoreApplication, QEvent, Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QAbstractItemView, QDialog

from sharpmod import gui_viewer
from sharpmod.gui_collection_state import CLOSED_LIMIT
from sharpmod.tests.test_gui_viewer_sidebar import _StubCollection, _StubWidget, _StubWindow


class _Window(_StubWindow):
    def __init__(self, collections):
        super().__init__(_StubWidget(collections))
        self.add_calls = []
        self.fail_add = False
        self.fail_after_add = False

    def addProfileCollection(self, collection, *, focus=True, check_integrity=True):  # noqa: N802
        if self.fail_add:
            raise ValueError("reopen failure fixture")
        self.add_calls.append((collection, focus, check_integrity))
        self.spc_widget.prof_ids.append(collection.getMeta("loc"))
        self.spc_widget.prof_collections.append(collection)
        if focus:
            self.spc_widget.pc_idx = len(self.spc_widget.prof_ids) - 1
        self.spc_widget.updateProfs()
        if self.fail_after_add:
            raise ValueError("partial reopen failure fixture")


def _make(qt_app, count=3):
    collections = {chr(65 + index): _StubCollection(chr(65 + index), model="GFS" if index == 1 else "HRRR")
                   for index in range(count)}
    window = _Window(collections)
    gui_viewer._install_sounding_sidebar(window)
    qt_app.processEvents()
    return window, window._sharpmod_collection_state


def test_successful_close_reopen_restores_same_data_identity_order_and_reference(qt_app):
    window, state = _make(qt_app)
    try:
        widget = window.spc_widget
        original = list(widget.prof_collections)
        state.pin("B")
        widget.setProfileCollection("B")
        assert window.rmProfileCollection("B")
        assert state.reference_id is None
        assert state.closed[0].collection is original[1]
        assert state.closed[0].closed_at.tzinfo is not None
        assert state.reopen("B")
        assert widget.prof_ids == ["A", "B", "C"]
        assert widget.prof_collections == original
        assert widget.prof_collections[1] is original[1]
        assert state.focused_id() == "B" and state.reference_id == "B"
        assert window.add_calls == [(original[1], True, False)]
        assert not state.closed and not state._closed_action.isEnabled()
    finally:
        window.close()


def test_reopen_hidden_retains_visibility_and_respects_a_new_reference(qt_app):
    window, state = _make(qt_app)
    try:
        state.pin("B")
        state.set_visible("B", False)
        assert window.rmProfileCollection("B")
        state.pin("C")
        assert state.reopen("B")
        assert state.hidden_ids == {"B"} and state.reference_id == "C"
        assert state.focused_id() == "A"
        assert window.spc_widget.prof_ids == ["A", "B", "C"]
        assert window.add_calls[0][1] is False
    finally:
        window.close()


def test_reopen_nonfocused_visible_profile_does_not_steal_focus(qt_app):
    window, state = _make(qt_app)
    try:
        assert state.focused_id() == "A"
        assert window.rmProfileCollection("B")
        assert state.closed[0].focused is False
        assert state.reopen("B")
        assert state.focused_id() == "A"
        assert window.add_calls == [(window.spc_widget.prof_collections[1], False, False)]
    finally:
        window.close()


def test_closed_history_bounds_missing_entries_and_last_profile_guard(qt_app):
    window, state = _make(qt_app, count=12)
    try:
        for profile_id in tuple(window.spc_widget.prof_ids)[:10]:
            assert window.rmProfileCollection(profile_id)
        assert len(state.closed) == CLOSED_LIMIT
        assert [record.profile_id for record in state.closed] == list("JIHGFEDC")
        assert not state.reopen("A")
        assert "retrieval may be required" in window._sharpmod_sidebar._collection_feedback.text()
        assert not state.remove("missing")
        assert state.remove("K")
        assert not state.remove("L")
    finally:
        window.close()


def test_native_shortcut_search_enter_reopens_exactly_once(qt_app):
    window, state = _make(qt_app)
    try:
        state.remove("B")
        window.resize(1100, 700)
        window.show()
        window.activateWindow()
        qt_app.processEvents()
        QTest.keyClick(window, Qt.Key_T, Qt.ControlModifier | Qt.ShiftModifier)
        dialog = state._closed_dialog
        assert dialog is not None and dialog.isVisible()
        dialog.search.setText("GFS")
        assert dialog.results.count() == 1
        assert "Closed:" in dialog.results.item(0).text() and "no retrieval" in dialog.results.item(0).text()
        QTest.keyClick(dialog.search, Qt.Key_Return)
        assert dialog.result() == QDialog.Accepted
        assert len(window.add_calls) == 1 and window.spc_widget.prof_ids == ["A", "B", "C"]
    finally:
        window.close()


def test_enlarged_long_row_has_native_pixel_scroll_to_all_context(qt_app):
    from sharpmod import gui_theme
    from sharpmod.theme import DEFAULT_THEME_NAME, THEMES

    before = (qt_app.styleSheet(), qt_app.palette(), qt_app.font(), gui_theme._theme_applied,
              gui_theme._current_theme, gui_theme._current_text_scale, gui_theme._current_density)
    window, state = _make(qt_app)
    try:
        state.pin("B")
        state.remove("B")
        window.resize(1000, 650)
        window.show()
        gui_theme.apply_theme(qt_app, theme=THEMES[DEFAULT_THEME_NAME], text_scale=200)
        state.show_closed()
        dialog = state._closed_dialog
        qt_app.processEvents()
        assert dialog.results.verticalScrollMode() == QAbstractItemView.ScrollPerPixel
        assert dialog.results.visualItemRect(dialog.results.item(0)).height() > dialog.results.viewport().height()
        bar = dialog.results.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(bar.maximum())
        qt_app.processEvents()
        assert dialog.results.visualItemRect(dialog.results.item(0)).bottom() <= dialog.results.viewport().rect().bottom()
        assert "Reference when closed" in dialog.results.item(0).text()
        assert dialog.reopen_button.isVisibleTo(dialog) and dialog.reopen_button.isEnabled()
    finally:
        window.close()
        (qss, palette, font, applied, theme, scale, density) = before
        qt_app.setStyleSheet(qss)
        qt_app.setPalette(palette)
        qt_app.setFont(font)
        gui_theme._theme_applied, gui_theme._current_theme = applied, theme
        gui_theme._current_text_scale, gui_theme._current_density = scale, density


def test_failed_or_colliding_reopen_keeps_retained_data_and_dialog(qt_app):
    window, state = _make(qt_app)
    try:
        state.remove("B")
        retained = state.closed[0].collection
        state.show_closed()
        dialog = state._closed_dialog
        window.fail_add = True
        dialog.open_selected()
        assert dialog.isVisible() and "reopen failure fixture" in dialog.status.text()
        assert state.closed[0].collection is retained
        window.fail_add = False
        fresh = _StubCollection("B", model="NAM")
        window.addProfileCollection(fresh)
        assert not state.reopen("B")
        dialog.refresh()
        assert not dialog.reopen_button.isEnabled() and "not be overwritten" in dialog.status.text()
        assert retained is state.closed[0].collection and fresh in window.spc_widget.prof_collections
    finally:
        window.close()


def test_partial_add_failure_rolls_back_every_source_and_focus_contract(qt_app):
    window, state = _make(qt_app)
    try:
        state.pin("B")
        window.spc_widget.setProfileCollection("B")
        state.remove("B")
        retained = state.closed[0]
        before_ids = list(window.spc_widget.prof_ids)
        before_collections = list(window.spc_widget.prof_collections)
        before_focus = state.focused_id()
        window.fail_after_add = True
        assert not state.reopen("B")
        assert window.spc_widget.prof_ids == before_ids
        assert window.spc_widget.prof_collections == before_collections
        assert state.focused_id() == before_focus
        assert state.reference_id is None and not state.hidden_ids
        assert state.closed == (retained,)
        assert "partial reopen failure fixture" in window._sharpmod_sidebar._collection_feedback.text()
    finally:
        window.close()


def test_closed_dialog_recreation_and_window_release_are_bounded(qt_app):
    window, state = _make(qt_app)
    window.setAttribute(Qt.WA_DeleteOnClose)
    state.remove("B")
    for _ in range(3):
        state.show_closed()
        state._closed_dialog.reject()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert len(window.findChildren(QDialog)) == 1
    window_ref = weakref.ref(window)
    window.close()
    del state, window
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_app.processEvents()
    gc.collect()
    assert window_ref() is None
