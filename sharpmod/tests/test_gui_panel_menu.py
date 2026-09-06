"""The swappable sounding panels have to be reachable by name.

Every one of these panels is computed for every sounding that is opened, whether
or not anyone looks at it -- the fire and winter parameters included. But the only
route to them was a right-click on one particular box, and not by design: the
composed layout detaches the left inset to make room for the index board, so the
vendored hit test can only ever land on the right one. Nothing named the gesture,
so two working panels read as missing features.

These tests pin the named route, and that switching through it still carries the
Skew-T annotations the vendored swap attaches -- the mixing-height marker for the
fire panel and the dendritic growth zone for the winter one. Those pairings are
why a separate "mode" concept is unnecessary.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from sharpmod import gui_viewer
from sharpmod import render as render_mod

EXAMPLE = "hrrr_point_36.68N_95.66W_f018.npz"


@pytest.fixture(scope="module")
def panelled_window(qt_app, tmp_path_factory):
    """A composed sounding window with the View menu and panel list installed."""
    from sharpmod.tests._examples import examples_dir
    from sharpmod.viz.SPCWindow import compose_window

    example = examples_dir() / EXAMPLE
    if not example.exists():
        pytest.skip("bundled example sounding is not present")

    render_mod.install_render_patches()
    prof_col, _stn_id = render_mod.decode(str(example))
    config = render_mod.build_config(str(tmp_path_factory.mktemp("panelmenu")))
    win, controller = compose_window(config, prof_col, mount=True)
    win.resize(1630, 1100)
    qt_app.processEvents()
    # The two installers, in the order ``compose_interactive`` runs them.
    gui_viewer._install_view_controls(win)
    gui_viewer._install_panel_menu(win)
    try:
        yield win
    finally:
        win.close()
        del controller


def _actions(win):
    return getattr(win, "_sharpmod_panel_actions", None) or {}


# --------------------------------------------------------------------------- #
# The menu itself
# --------------------------------------------------------------------------- #
def test_the_view_menu_offers_the_swappable_panels(panelled_window):
    actions = _actions(panelled_window)

    assert getattr(panelled_window, "_sharpmod_panel_menu", None) is not None
    assert {"FIRE", "WINTER", "SHIP", "STP STATS", "COND STP", "VROT"} \
        <= set(actions)


def test_fire_and_winter_are_reachable_by_name(panelled_window):
    """The whole point: these two were the ones that read as absent."""
    actions = _actions(panelled_window)

    assert actions["FIRE"].text() == "Fire Weather"
    assert actions["WINTER"].text() == "Winter Weather"


def test_every_offered_panel_says_what_it_is_for(panelled_window):
    """The titles are abbreviations, and two of them name the same hazard."""
    for key, action in _actions(panelled_window).items():
        assert action.toolTip(), key


def test_the_detached_panel_is_not_offered(panelled_window):
    """Offering the left inset would be a menu entry that cannot do anything."""
    sw = panelled_window.spc_widget

    assert sw.left_inset not in _actions(panelled_window)


def test_the_panel_entries_are_mutually_exclusive(panelled_window):
    """One slot, so one tick."""
    actions = _actions(panelled_window)
    groups = {action.actionGroup() for action in actions.values()}

    assert len(groups) == 1
    group = groups.pop()
    assert group is not None and group.isExclusive()


# --------------------------------------------------------------------------- #
# Switching
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key,widget_name", [
    ("FIRE", "plotFire"),
    ("WINTER", "plotWinter"),
    ("SHIP", "plotSHIP"),
    ("COND STP", "plotSTPEF"),
    ("VROT", "plotVROT"),
    ("STP STATS", "plotSTP"),
])
def test_choosing_a_panel_mounts_it(panelled_window, key, widget_name):
    sw = panelled_window.spc_widget

    assert gui_viewer.show_sounding_panel(panelled_window, key) is True
    assert sw.right_inset == key
    assert type(sw.right_inset_ob).__name__ == widget_name


def test_choosing_the_panel_already_shown_leaves_it_alone(panelled_window):
    """The vendored swap destroys and rebuilds, so a no-op must not reach it."""
    sw = panelled_window.spc_widget
    gui_viewer.show_sounding_panel(panelled_window, "SHIP")
    before = sw.right_inset_ob

    assert gui_viewer.show_sounding_panel(panelled_window, "SHIP") is False
    assert sw.right_inset_ob is before


@pytest.mark.parametrize("key", ["", "NOT A PANEL", None])
def test_an_unknown_panel_is_refused(panelled_window, key):
    sw = panelled_window.spc_widget
    gui_viewer.show_sounding_panel(panelled_window, "STP STATS")

    assert gui_viewer.show_sounding_panel(panelled_window, key) is False
    assert sw.right_inset == "STP STATS"


def test_a_window_without_a_sounding_widget_is_tolerated():
    """The helper is public, so it must not assume a composed window."""
    from types import SimpleNamespace

    assert gui_viewer.show_sounding_panel(SimpleNamespace(), "FIRE") is False


# --------------------------------------------------------------------------- #
# The Skew-T annotations have to follow
# --------------------------------------------------------------------------- #
def test_the_fire_panel_marks_the_mixing_height_on_the_skewt(panelled_window):
    sw = panelled_window.spc_widget
    gui_viewer.show_sounding_panel(panelled_window, "STP STATS")

    gui_viewer.show_sounding_panel(panelled_window, "FIRE")

    assert sw.pbl is True


def test_the_winter_panel_marks_the_growth_zone_on_the_skewt(panelled_window):
    sw = panelled_window.spc_widget
    gui_viewer.show_sounding_panel(panelled_window, "STP STATS")

    gui_viewer.show_sounding_panel(panelled_window, "WINTER")

    assert sw.dgz is True


def test_leaving_a_panel_takes_its_annotation_with_it(panelled_window):
    """Otherwise the markers accumulate and the Skew-T ends up wearing both."""
    sw = panelled_window.spc_widget

    gui_viewer.show_sounding_panel(panelled_window, "FIRE")
    assert sw.pbl is True
    gui_viewer.show_sounding_panel(panelled_window, "WINTER")

    assert sw.pbl is False
    assert sw.dgz is True


# --------------------------------------------------------------------------- #
# The tick
# --------------------------------------------------------------------------- #
def test_the_tick_names_the_mounted_panel(panelled_window):
    actions = _actions(panelled_window)
    gui_viewer.show_sounding_panel(panelled_window, "SHIP")

    panelled_window._sharpmod_panel_menu.aboutToShow.emit()

    assert [key for key, act in actions.items() if act.isChecked()] == ["SHIP"]


def test_a_right_click_swap_is_reflected_when_the_menu_reopens(panelled_window):
    """Both routes remain live, so the menu cannot cache what is mounted."""
    sw = panelled_window.spc_widget
    actions = _actions(panelled_window)
    gui_viewer.show_sounding_panel(panelled_window, "SHIP")

    # Exactly what a right-click on the panel does.
    sw.makeInsetMenu(sw.left_inset, sw.right_inset)
    action = next(item for item in sw.menu_ag.actions()
                  if item.data() == "FIRE")
    action.setChecked(True)
    sw.inset_to_swap = "RIGHT"
    sw.swapInset()

    panelled_window._sharpmod_panel_menu.aboutToShow.emit()

    assert [key for key, act in actions.items() if act.isChecked()] == ["FIRE"]


def test_the_choice_is_remembered_in_the_configuration(panelled_window):
    """The vendored swap persists it, so it survives a restart."""
    sw = panelled_window.spc_widget

    gui_viewer.show_sounding_panel(panelled_window, "WINTER")

    assert sw.config["insets", "right_inset"] == "WINTER"
