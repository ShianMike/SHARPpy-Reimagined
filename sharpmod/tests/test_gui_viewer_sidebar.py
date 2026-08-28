"""Tests for the viewer's sounding context sidebar.

Two layers, deliberately:

* Most tests drive a **stub** standing in for the vendored ``SPCWidget``. The
  sidebar's job is to reflect and mutate a small, well-defined slice of that
  widget's state (``prof_collections``, ``prof_ids``, ``pc_idx``,
  ``setProfileCollection``, ``updateProfs``), and a stub exercises every branch
  in milliseconds instead of composing a full sounding per test.
* A handful of tests compose the **real** sounding, because a stub cannot prove
  the assumptions the stub encodes: that those attributes exist and behave as
  assumed, and that the panel's width is genuinely free at 1080p.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtpy.QtCore import QSize, Qt
from qtpy.QtWidgets import QDockWidget, QMainWindow

from sharpmod import gui_viewer
from sharpmod.theme import VIEWER_SIDEBAR_W

#: The composed canvas size shared by the interactive viewer and the
#: ``sharpmod-render`` PNG CLI, measured after ``compose_window`` and the four
#: grow/settle passes.
#:
#: Deliberately a test constant rather than a product one. It is an *outcome* of
#: the vendored layout, not an input: publishing it next to the code would
#: invite someone to force the canvas to this size and hide a real layout
#: regression behind a resize.
CANVAS_W, CANVAS_H = 1630, 1091


# --------------------------------------------------------------------------
# Stub layer
# --------------------------------------------------------------------------
class _StubCollection:
    """The slice of ``ProfCollection`` the sidebar reads."""

    def __init__(self, loc, model="HRRR", run=None, members=("",)):
        from datetime import datetime, timezone

        self._meta = {
            "loc": loc,
            "model": model,
            "run": run or datetime(2026, 8, 27, 12, tzinfo=timezone.utc),
        }
        self._members = {name: object() for name in members}
        self._highlight = sorted(self._members)[0]

    def getMeta(self, key, index=False):  # noqa: N802 - matches upstream
        return self._meta[key]

    def isEnsemble(self):  # noqa: N802 - matches upstream
        return len(self._members) > 1

    def getCurrentProfs(self):  # noqa: N802 - matches upstream
        return dict(self._members)

    def getHighlightedProf(self):  # noqa: N802 - matches upstream
        return self._members[self._highlight]

    def setHighlightedMember(self, name):  # noqa: N802 - matches upstream
        self._highlight = name


class _StubWidget:
    """The slice of ``SPCWidget`` the sidebar reads and mutates."""

    def __init__(self, collections):
        self.prof_collections = list(collections.values())
        self.prof_ids = list(collections.keys())
        self.pc_idx = 0
        self.update_calls = 0
        self.focus_calls = []

    def setProfileCollection(self, prof_id):  # noqa: N802 - matches upstream
        self.focus_calls.append(prof_id)
        self.pc_idx = self.prof_ids.index(prof_id)
        self.updateProfs()

    def updateProfs(self):  # noqa: N802 - matches upstream
        self.update_calls += 1


class _StubWindow(QMainWindow):
    def __init__(self, widget):
        super().__init__()
        self.spc_widget = widget
        self.removed = []

    def rmProfileCollection(self, prof_id):  # noqa: N802 - matches upstream
        idx = self.spc_widget.prof_ids.index(prof_id)
        self.spc_widget.prof_ids.pop(idx)
        self.spc_widget.prof_collections.pop(idx)
        if self.spc_widget.pc_idx >= len(self.spc_widget.prof_ids):
            self.spc_widget.pc_idx = 0
        self.removed.append(prof_id)
        self.spc_widget.updateProfs()


@pytest.fixture
def themed_app(qt_app):
    """``qt_app`` with the chrome theme applied, restored afterwards.

    Control sizes in the sidebar are style-sheet driven, so an unthemed
    application is a configuration that never ships -- ``main()`` always calls
    ``ensure_theme_applied``. The style sheet and palette are snapshotted and
    restored because the application object is shared across the whole worker.

    Style sheet, palette, application font and ``gui_theme``'s two module
    globals are all restored, and that is not incidental tidiness. ``qt_app`` is
    session-scoped, so leaving any of them behind hands every later test a
    configuration that never ships. The globals matter most: ``apply_theme`` sets
    ``_theme_applied = True``, so restoring only the style sheet would leave
    later tests on an application whose chrome QSS has been stripped while
    ``ensure_theme_applied()`` believes it is already themed and short-circuits.
    The font matters because it moves widget size hints, which is what the
    composed geometry is measured from.

    The one thing deliberately *not* restored is the application style.
    ``apply_theme`` switches to Fusion, and putting the platform style back means
    re-polishing every widget alive in the process -- unbounded in a serial run
    that has built thousands of them -- to reach a state no shipping
    configuration uses and no test asserts on. Every other fixture that applies
    the theme already leaves Fusion in place, so this matches them rather than
    introducing a novel, expensive teardown.
    """
    from sharpmod import gui_theme
    from sharpmod.theme import DEFAULT_THEME_NAME, THEMES

    before_qss = qt_app.styleSheet()
    before_palette = qt_app.palette()
    before_font = qt_app.font()
    before_applied = gui_theme._theme_applied
    before_theme = gui_theme._current_theme
    gui_theme.apply_theme(qt_app, theme=THEMES[DEFAULT_THEME_NAME])
    try:
        yield qt_app
    finally:
        qt_app.setStyleSheet(before_qss)
        qt_app.setPalette(before_palette)
        qt_app.setFont(before_font)
        gui_theme._theme_applied = before_applied
        gui_theme._current_theme = before_theme


def _make(qt_app, collections):
    """A stub window with the sidebar installed, plus its panel and dock."""
    widget = _StubWidget(collections)
    win = _StubWindow(widget)
    gui_viewer._install_view_controls(win)
    gui_viewer._install_sounding_sidebar(win)
    qt_app.processEvents()
    return win, widget, win._sharpmod_sidebar, win._sharpmod_sidebar_dock


@pytest.fixture
def one_sounding(qt_app):
    win, widget, panel, dock = _make(
        qt_app, {"A (27/1200Z HRRR)": _StubCollection("KOUN")})
    yield win, widget, panel, dock
    win.close()


@pytest.fixture
def three_soundings(qt_app):
    win, widget, panel, dock = _make(qt_app, {
        "A (27/1200Z HRRR)": _StubCollection("KOUN"),
        "B (27/1200Z GFS)": _StubCollection("KTLX", model="GFS"),
        "C (27/1800Z NAM)": _StubCollection("KFWS", model="NAM"),
    })
    yield win, widget, panel, dock
    win.close()


def test_installs_docked_on_the_right(one_sounding):
    win, _widget, panel, dock = one_sounding
    assert isinstance(dock, QDockWidget)
    assert win.dockWidgetArea(dock) == Qt.RightDockWidgetArea
    assert dock.widget() is panel


def test_is_not_floatable(one_sounding):
    """A floating panel would cover the sounding, defeating its purpose."""
    _win, _widget, _panel, dock = one_sounding
    assert not dock.features() & QDockWidget.DockWidgetFloatable
    assert dock.features() & QDockWidget.DockWidgetClosable


def test_title_bar_close_button_is_a_usable_target(themed_app):
    """Qt's built-in dock close button cannot be sized via QSS.

    Fusion computes its rect from title-bar metrics and ignores a QSS
    width/height, yielding roughly a 16x9px target -- and it is the panel's only
    visible affordance for dismissing itself. Hence the custom title bar.

    Uses ``themed_app``: the replacement button's size comes from the style
    sheet, so an unthemed application measures Qt's default 44x19 instead.
    """
    from qtpy.QtWidgets import QAbstractButton

    win, _widget, _panel, dock = _make(
        themed_app, {"A (27/1200Z HRRR)": _StubCollection("KOUN")})
    win.show()
    themed_app.processEvents()

    bar = dock.titleBarWidget()
    assert bar is not None, "dock still uses Qt's unstylable title bar"
    buttons = bar.findChildren(QAbstractButton)
    assert len(buttons) == 1
    close = buttons[0]
    assert close.isVisible()
    assert close.width() >= 24 and close.height() >= 24, \
        f"close target too small: {close.width()}x{close.height()}"
    assert "Ctrl+B" in close.toolTip()
    win.close()


def test_title_bar_close_button_hides_the_panel(one_sounding, qt_app):
    from qtpy.QtWidgets import QAbstractButton

    win, _widget, _panel, dock = one_sounding
    win.show()
    qt_app.processEvents()
    assert dock.isVisible()

    dock.titleBarWidget().findChildren(QAbstractButton)[0].click()
    qt_app.processEvents()
    assert not dock.isVisible()
    # And the View-menu toggle brings it back.
    dock.toggleViewAction().trigger()
    qt_app.processEvents()
    assert dock.isVisible()


def test_lists_every_loaded_sounding(three_soundings):
    _win, _widget, panel, _dock = three_soundings
    assert panel._list.count() == 3


def test_marks_the_focused_sounding(three_soundings):
    _win, widget, panel, _dock = three_soundings
    widget.pc_idx = 2
    panel.refresh()
    assert panel._list.currentRow() == 2


def test_row_label_splits_location_from_model_and_run(three_soundings):
    """Location on its own line, so it is scannable down the list."""
    _win, _widget, panel, _dock = three_soundings
    text = panel._list.item(0).text()
    assert text.startswith("KOUN\n")
    assert "HRRR" in text and "27 Aug 1200Z" in text


def test_row_label_falls_back_to_the_upstream_id(qt_app):
    """A collection with no usable metadata still gets an identifiable row."""

    class _Bare:
        def getMeta(self, key, index=False):  # noqa: N802
            raise KeyError(key)

        def isEnsemble(self):  # noqa: N802
            return False

    win, _widget, panel, _dock = _make(qt_app, {"RAW ID": _Bare()})
    try:
        assert panel._list.item(0).text() == "RAW ID"
    finally:
        win.close()


def test_picking_a_row_focuses_that_collection(three_soundings):
    _win, widget, panel, _dock = three_soundings
    panel._list.setCurrentRow(1)
    assert widget.focus_calls == ["B (27/1200Z GFS)"]
    assert widget.pc_idx == 1


def test_external_focus_change_syncs_the_list(three_soundings):
    """The menu's Focus action and the Space key both land in updateProfs."""
    _win, widget, panel, _dock = three_soundings
    widget.pc_idx = 2
    widget.updateProfs()
    assert panel._list.currentRow() == 2


def test_refresh_does_not_re_focus(three_soundings):
    """Writing the selection during refresh must not read as a user pick.

    Without the guard this recurses: refresh sets the row, the row change calls
    setProfileCollection, that calls updateProfs, which refreshes again.
    """
    _win, widget, panel, _dock = three_soundings
    widget.pc_idx = 1
    widget.focus_calls.clear()
    before = widget.update_calls
    panel.refresh()
    assert widget.focus_calls == []
    assert widget.update_calls == before


def test_single_sounding_shows_the_hint_and_disables_remove(one_sounding):
    _win, _widget, panel, _dock = one_sounding
    assert panel._list.count() == 1
    # isVisibleTo, not isVisible: these tests never show the window, and
    # isVisible() is False for every descendant of an unshown window -- so a
    # "hidden" assertion written with isVisible() passes no matter what the
    # code does.
    assert panel._empty.isVisibleTo(panel)
    assert not panel._remove.isEnabled()


def test_multiple_soundings_hide_the_hint_and_enable_remove(three_soundings):
    _win, _widget, panel, _dock = three_soundings
    assert not panel._empty.isVisibleTo(panel)
    assert panel._remove.isEnabled()


def test_remove_focused_removes_and_resyncs(three_soundings):
    win, widget, panel, _dock = three_soundings
    panel._list.setCurrentRow(1)
    panel._on_remove()
    assert win.removed == ["B (27/1200Z GFS)"]
    assert panel._list.count() == 2
    assert [panel._list.item(i).text().split("\n")[0] for i in range(2)] \
        == ["KOUN", "KFWS"]


def test_remove_is_a_no_op_with_no_selection(three_soundings):
    win, _widget, panel, _dock = three_soundings
    panel._list.setCurrentRow(-1)
    panel._on_remove()
    assert win.removed == []


def test_list_height_tracks_the_row_count(qt_app, three_soundings):
    """A single sounding must not leave a tall empty well above the members."""
    _win, _widget, three, _dock = three_soundings
    win1, _w1, one, _d1 = _make(
        qt_app, {"A (27/1200Z HRRR)": _StubCollection("KOUN")})
    try:
        assert one._list.height() < three._list.height()
    finally:
        win1.close()


def test_members_hidden_for_a_deterministic_sounding(one_sounding):
    _win, _widget, panel, _dock = one_sounding
    assert not panel._members.isVisibleTo(panel)
    assert not panel._member_label.isVisibleTo(panel)


def test_members_listed_for_an_ensemble(qt_app):
    win, _widget, panel, _dock = _make(qt_app, {
        "E (27/1200Z SREF)": _StubCollection(
            "KOUN", members=("mean", "m01", "m02")),
    })
    try:
        assert panel._members.isVisibleTo(panel)
        assert panel._member_label.isVisibleTo(panel)
        assert [panel._members.item(i).text()
                for i in range(panel._members.count())] \
            == ["m01", "m02", "mean"]
    finally:
        win.close()


def test_members_mark_the_highlighted_one(qt_app):
    win, widget, panel, _dock = _make(qt_app, {
        "E (27/1200Z SREF)": _StubCollection(
            "KOUN", members=("mean", "m01", "m02")),
    })
    try:
        widget.prof_collections[0].setHighlightedMember("m02")
        panel.refresh()
        assert panel._members.currentItem().text() == "m02"
    finally:
        win.close()


def test_picking_a_member_highlights_it(qt_app):
    win, widget, panel, _dock = _make(qt_app, {
        "E (27/1200Z SREF)": _StubCollection(
            "KOUN", members=("mean", "m01", "m02")),
    })
    try:
        before = widget.update_calls
        panel._members.setCurrentRow(1)  # m02
        assert widget.prof_collections[0]._highlight == "m02"
        assert widget.update_calls > before
    finally:
        win.close()


def test_member_rows_are_compact(qt_app):
    """Single-line member rows must not inherit the two-line row height."""
    from sharpmod.theme import PROP_COMPACT

    win, _widget, panel, _dock = _make(qt_app, {
        "E (27/1200Z SREF)": _StubCollection(
            "KOUN", members=("mean", "m01")),
    })
    try:
        assert panel._members.property(PROP_COMPACT) is True
        assert panel._list.property(PROP_COMPACT) in (None, False)
    finally:
        win.close()


def test_toggle_action_is_in_the_view_menu(one_sounding):
    win, _widget, _panel, dock = one_sounding
    toggle = dock.toggleViewAction()
    # Same string as the dock's own title, so the menu entry and the panel
    # header cannot drift apart.
    assert toggle.text() == dock.windowTitle() == "Sounding Panel"
    assert toggle.shortcut().toString() == "Ctrl+B"
    assert toggle in win._sharpmod_view_menu.actions()


def test_source_and_quality_triggers_the_inspector(one_sounding):
    from qtpy.QtGui import QAction

    win, _widget, panel, _dock = one_sounding
    fired = []
    action = QAction("inspect", win)
    action.triggered.connect(lambda: fired.append(1))
    win._sharpmod_data_inspector_action = action
    panel._inspect.click()
    assert fired == [1]


def test_source_and_quality_is_safe_without_the_inspector(one_sounding):
    """The sidebar installs before the inspector in some orders."""
    _win, _widget, panel, _dock = one_sounding
    panel._on_inspect()  # must not raise


def test_updateprofs_is_wrapped_once(one_sounding):
    """A second install must not stack another refresh onto updateProfs."""
    win, widget, _panel, _dock = one_sounding
    wrapped = widget.updateProfs
    gui_viewer._install_sounding_sidebar(win)
    assert widget.updateProfs is wrapped


def test_reserved_width_counts_only_visible_docks(one_sounding):
    win, _widget, _panel, dock = one_sounding
    win.show()
    assert gui_viewer._reserved_dock_width(win) >= VIEWER_SIDEBAR_W
    dock.hide()
    assert gui_viewer._reserved_dock_width(win) == 0


def test_reserved_width_is_counted_before_the_window_is_shown(one_sounding):
    """The pre-show pass is the *only* caller, so it must work unshown.

    ``_reserved_dock_width`` exists for :func:`_fit_window_to_screen`, which
    runs between ``compose_interactive``'s ``win.hide()`` and its matching
    ``showNormal()``. The helper originally filtered on ``dock.isVisible()``,
    and ``isVisible()`` is False for every descendant of a window that has not
    been shown -- so it returned 0 on every production call and the reservation
    it exists to make never happened. The sibling test above passes only because
    it calls ``win.show()`` first, which is a state the caller never occupies.
    """
    win, _widget, _panel, _dock = one_sounding
    assert not win.isVisible(), "this test is meaningless on a shown window"
    assert gui_viewer._reserved_dock_width(win) >= VIEWER_SIDEBAR_W, (
        "no width reserved for the dock before the window is shown, so the "
        "fit sizes the window as if the sounding had the full screen width")


def test_reserved_width_ignores_a_dock_the_user_closed_before_show(
        one_sounding):
    """The relaxed predicate must not start counting closed docks.

    ``not isHidden()`` is deliberately weaker than ``isVisible()``; this pins
    the part that still has to hold, so the fix cannot drift into reserving
    space for a panel that is not there.
    """
    win, _widget, _panel, dock = one_sounding
    dock.hide()
    assert not win.isVisible()
    assert gui_viewer._reserved_dock_width(win) == 0


def test_refresh_survives_a_widget_that_disappears(one_sounding):
    """Teardown order can strip spc_widget before the panel is destroyed."""
    win, _widget, panel, _dock = one_sounding
    del win.spc_widget
    panel.refresh()  # must not raise


def test_panel_holds_the_window_weakly(qt_app):
    """The panel must not keep the sounding window's wrapper alive.

    Connecting a bound method to a child widget's signal makes Qt hold a
    C++-side reference to the panel that Python's cyclic GC cannot see. A strong
    reference to the window from the panel therefore pins the window for the
    process lifetime, and every open/close cycle retains a whole viewer --
    which is exactly what
    ``test_gui_viewer_lifecycle::test_composed_viewer_is_destroyed_and_released_on_close``
    caught. Asserted here too, because that test uses a stand-in ``spc_widget``
    and so never exercises the ``updateProfs`` wrap.
    """
    import gc
    import weakref

    win, _widget, panel, _dock = _make(
        qt_app, {"A (27/1200Z HRRR)": _StubCollection("KOUN")})
    ref = weakref.ref(win)
    assert panel._window() is win

    win.close()
    del win
    gc.collect()
    qt_app.processEvents()
    gc.collect()

    assert ref() is None
    assert panel._window() is None
    # refresh() is the one method still reachable after teardown -- the
    # updateProfs wrapper can fire during it -- so it must degrade to a no-op
    # rather than raise. The button handlers are not reachable: the panel's own
    # C++ object goes with the window.
    panel.refresh()


# --------------------------------------------------------------------------
# Real-sounding layer
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def real_viewer(qt_app, tmp_path_factory):
    """A real composed sounding with the real layout passes applied.

    The theme is applied here, before composing, rather than left to whatever
    ran earlier. ``apply_theme`` installs a universal ``QWidget`` font rule and
    switches the style to Fusion, both of which move widget size hints -- and
    the grow passes below read those hints. Composing under an indeterminate
    theme state would make the geometry this module measures depend on test
    order.
    """
    from sharpmod import gui_theme
    from sharpmod import render as render_mod
    from sharpmod.tests._examples import examples_dir
    from sharpmod.theme import DEFAULT_THEME_NAME, THEMES
    from sharpmod.viz.SPCWindow import compose_window

    example = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"
    if not example.exists():
        pytest.skip("HRRR .npz example unavailable")

    before_qss = qt_app.styleSheet()
    before_palette = qt_app.palette()
    before_font = qt_app.font()
    before_applied = gui_theme._theme_applied
    before_theme = gui_theme._current_theme
    gui_theme.apply_theme(qt_app, theme=THEMES[DEFAULT_THEME_NAME])

    render_mod.install_font(qt_app)
    render_mod.install_render_patches()
    prof_col, _stn_id = render_mod.decode(str(example))
    config = render_mod.build_config(str(tmp_path_factory.mktemp("sidebar")))
    win, controller = compose_window(config, prof_col, mount=True)

    render_mod.align_top_row(win)
    render_mod.apply_layout_compensation(win.spc_widget)
    for _ in range(4):
        qt_app.processEvents()
    render_mod._grow_for_family_panels(win)
    for _ in range(4):
        qt_app.processEvents()
    render_mod.enlarge_canvas(win)
    for _ in range(6):
        qt_app.processEvents()

    # Snapshotted here, at the point the PNG renderer measures it, and handed to
    # the tests instead of letting them re-read the live widget.
    #
    # This fixture is module-scoped, and test_installs_against_the_real_viewer
    # docks the 248px sidebar onto this very window -- which takes its width out
    # of the central widget and leaves the canvas at 1376x1034. Every later test
    # that re-read ``win.spc_widget.width()`` was therefore measuring a canvas
    # the sidebar had already shrunk, and measuring the sidebar's cost against a
    # canvas the sidebar had shrunk is circular -- it made the budget look 254px
    # roomier than it really is.
    #
    # Ordering does not save us: the lane runs pytest-xdist with four workers and
    # the default ``--dist load``, so which tests land in the same process as
    # this fixture, and therefore which of them see the shrunk canvas, varies
    # between runs.
    natural = QSize(win.spc_widget.size())

    # The Source & Quality report is part of what the sidebar surfaces, so the
    # fixture installs it: without this the report test skips silently rather
    # than checking anything.
    gui_viewer._install_data_inspector(win, prof_col)

    try:
        yield win, controller, natural
    finally:
        win.close()
        qt_app.setStyleSheet(before_qss)
        qt_app.setPalette(before_palette)
        qt_app.setFont(before_font)
        gui_theme._theme_applied = before_applied
        gui_theme._current_theme = before_theme


def test_source_and_quality_report_is_monospaced_and_unwrapped(real_viewer,
                                                               themed_app):
    """The report is a column-aligned table, so it needs a fixed-width face.

    It was rendering in the proportional interface font, which left every value
    column ragged, and wrapping at the widget width, which broke long GRIB URLs
    and Windows temp paths mid-token. Both come from the style sheet rather than
    a QFont, because ``render.install_font`` patches ``QFont`` process-wide when
    the first sounding opens and would replace a face chosen in Python.

    Uses ``themed_app`` for exactly that reason: with no style sheet applied the
    report falls back to the interface face and this test would report the very
    bug it is meant to catch.
    """
    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QDialog, QPlainTextEdit

    from sharpmod.theme import FAMILY_MONO_STACK, OBJ_REPORT

    win, _controller, _natural = real_viewer
    if getattr(win, "_sharpmod_data_inspector_action", None) is None:
        pytest.skip("data inspector not installed on this window")

    seen = {}

    def inspect():
        for widget in themed_app.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                edits = widget.findChildren(QPlainTextEdit)
                if edits:
                    seen["edit"] = edits[0]
                    seen["family"] = edits[0].font().family()
                    seen["wrap"] = edits[0].lineWrapMode()
                    seen["name"] = edits[0].objectName()
                widget.close()
                return

    QTimer.singleShot(0, inspect)
    win._sharpmod_data_inspector_action.trigger()
    for _ in range(6):
        themed_app.processEvents()

    assert seen, "the Source & Quality dialog did not open"
    assert seen["name"] == OBJ_REPORT
    assert seen["wrap"] == QPlainTextEdit.NoWrap
    # The resolved family must be one of the monospace stack, not the UI face.
    assert seen["family"] in FAMILY_MONO_STACK, (
        f"report is set in {seen['family']!r}, which is not a monospace face; "
        f"a column-aligned table needs one of {FAMILY_MONO_STACK}")


def test_real_widget_exposes_the_attributes_the_stub_assumes(real_viewer):
    """Guards the stub against drift in the vendored widget's API."""
    win, _controller, _natural = real_viewer
    sw = win.spc_widget
    for attr in ("prof_collections", "prof_ids", "pc_idx",
                 "setProfileCollection", "updateProfs"):
        assert hasattr(sw, attr), attr
    assert hasattr(win, "rmProfileCollection")
    collection = sw.prof_collections[sw.pc_idx]
    for attr in ("getMeta", "isEnsemble", "getCurrentProfs",
                 "getHighlightedProf", "setHighlightedMember"):
        assert hasattr(collection, attr), attr


def test_installs_against_the_real_viewer(real_viewer, qt_app):
    """Note: this docks the sidebar onto the shared module-scoped window and
    deliberately leaves it there, which shrinks the canvas. That is why the
    geometry tests below read the fixture's snapshot rather than the live widget.
    """
    win, _controller, _natural = real_viewer
    if getattr(win, "_sharpmod_sidebar", None) is None:
        gui_viewer._install_view_controls(win)
        gui_viewer._install_sounding_sidebar(win)
        qt_app.processEvents()
    panel = win._sharpmod_sidebar
    assert panel._list.count() == len(win.spc_widget.prof_ids)
    assert panel._list.currentRow() == win.spc_widget.pc_idx


def test_panel_width_is_fixed_not_merely_a_minimum(one_sounding):
    """The measured budget has to be enforced, not suggested.

    With only a minimum set, the panel's own content size hint won and pushed it
    back to about 264 px, which clipped the canvas at Actual Size -- the failure
    was invisible in the panel and showed up as a cropped sounding.
    """
    _win, _widget, panel, _dock = one_sounding
    assert panel.minimumWidth() == panel.maximumWidth() == VIEWER_SIDEBAR_W


def test_composed_canvas_matches_the_documented_geometry(real_viewer):
    """Pin the canvas size the GUI and the PNG CLI share.

    ``compose_window`` plus the four grow/settle passes is the code path both
    the interactive viewer and ``sharpmod-render`` run, so this size is a
    contract: the CLI's PNG output is required to stay byte-identical, and the
    sidebar budget and fit math above are measured against these numbers.

    It was asserted nowhere. The number lived in a docstring, in a constant fed
    *into* a stand-in widget in the zoom tests, and in a benchmark that records
    only timings -- while the lifecycle test stubs ``enlarge_canvas`` out
    entirely. That matters more than usual for this change, which installs a
    toolbar and a dock before the fit and adds a universal ``QWidget`` font rule
    reaching every vendored panel; all three can move the size hints the grow
    passes read.

    Exact equality, not a tolerance. A byte-identical PNG has no tolerance, and
    a drift of even a pixel here means the settle passes have started resolving
    differently -- which is the thing worth being told about.

    Reads the fixture's snapshot, taken right after the render passes, because a
    sibling test docks the sidebar onto this same window afterwards.
    """
    _win, _controller, size = real_viewer
    assert (size.width(), size.height()) == (CANVAS_W, CANVAS_H), (
        f"composed canvas is {size.width()}x{size.height()}, expected "
        f"{CANVAS_W}x{CANVAS_H}; the PNG CLI shares this geometry, so a change "
        f"here means sharpmod-render output moved too")


def test_sidebar_leaves_the_canvas_unclipped_at_actual_size(real_viewer):
    """Actual Size is the only unresampled view, so it must not be clipped.

    The vendored canvas paints into a bitmap at its natural size, so 100% is the
    only scale that is not resampled; every other scale is a bitmap downscale
    and looks soft. Clipping the one crisp view is therefore the worst trade the
    sidebar could make, and the first version of the panel did exactly that --
    at 320 px it cut 48 px off a 1630 px canvas on a 1920 px screen.

    The budget has to include the vertical scrollbar. The canvas is roughly
    150 px taller than the viewport at 100%, so that scrollbar is always present
    and always costs ``SCROLLBAR_W``; a measurement taken before it appeared is
    what made 272 px look safe when the real ceiling is nearer 255 px.
    """
    from sharpmod.theme import SCROLLBAR_W

    _win, _controller, natural = real_viewer
    nat_w = natural.width()
    screen_w = 1920
    # Dock separator plus the view frame, measured on Windows/Fusion.
    chrome = 12

    needed = VIEWER_SIDEBAR_W + nat_w + SCROLLBAR_W + chrome
    assert needed <= screen_w, (
        f"at 100% the sounding is clipped on a {screen_w}px screen: "
        f"sidebar {VIEWER_SIDEBAR_W} + canvas {nat_w} + scrollbar "
        f"{SCROLLBAR_W} + chrome {chrome} = {needed}")


def test_sidebar_width_is_free_at_1080p(real_viewer):
    """The panel must not shrink the sounding on a maximized 1080p window.

    The composed sounding is 1630x1091 (pinned by
    :func:`test_composed_canvas_matches_the_documented_geometry`), so on a
    1920x1080 screen the fit is limited by *height*, leaving several hundred
    pixels of horizontal slack that would otherwise be empty letterbox. This is
    the measurement that
    justifies :data:`sharpmod.theme.VIEWER_SIDEBAR_W`; if the canvas geometry
    ever changes enough to make width the binding constraint, this fails rather
    than silently shrinking the sounding.
    """
    win, _controller, natural = real_viewer
    nat_w, nat_h = natural.width(), natural.height()
    assert nat_w > 1 and nat_h > 1

    # A maximized 1920x1080 window: work area less the menu bar and toolbar.
    screen_w, screen_h = 1920, 1040
    vp_h = screen_h - win.menuBar().sizeHint().height() - 34

    def fit(viewport_w):
        return min(viewport_w / nat_w, vp_h / nat_h, 1.0)

    without = fit(screen_w)
    with_panel = fit(screen_w - VIEWER_SIDEBAR_W)
    assert with_panel == pytest.approx(without), (
        f"sidebar costs scale: {without:.4f} -> {with_panel:.4f}; "
        f"natural={nat_w}x{nat_h}")

    # And confirm the slack really is what makes it free, so this test fails
    # loudly if the geometry drifts rather than passing for the wrong reason.
    slack = screen_w - round(nat_w * without)
    assert slack >= VIEWER_SIDEBAR_W, (
        f"only {slack}px of horizontal slack for a {VIEWER_SIDEBAR_W}px panel")
