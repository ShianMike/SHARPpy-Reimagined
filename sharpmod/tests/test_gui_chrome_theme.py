"""Integration tests for chrome theming across the real widget tree.

:mod:`test_gui_theme_tokens` covers the token values and the generated style
sheet in isolation. This module checks the parts that only break once real
widgets exist:

* The theme is applied on the ``QApplication``, not per window. That matters
  because four of the picker's five source panels are materialized lazily and
  every dialog is built on demand -- a window-level style sheet would miss
  everything created after construction.
* Every source panel and dialog can be constructed under the themed
  application without a style-sheet selector crashing on a widget type.
* Semantic object names are actually assigned, not merely defined. A selector
  with no widget wearing its name is dead styling.
* Chrome typography survives ``render.install_font``, which replaces
  ``QtGui.QFont`` process-wide when the first sounding window opens.
"""

from __future__ import annotations

import pytest

from qtpy.QtGui import QFont
from qtpy.QtWidgets import QLabel, QPushButton

from sharpmod import gui_picker, gui_theme
from sharpmod import theme as T
from sharpmod.gui_settings import _build_settings

#: The picker's five source panels, by tab label.
PANEL_TITLES = (
    "Station Map",
    "Station List",
    "Forecast Model",
    "Reanalysis (ERA5)",
    "Open File",
)


@pytest.fixture
def picker(qt_app, monkeypatch, tmp_path):
    """A fully materialized picker under an isolated settings file."""
    monkeypatch.setattr(
        gui_picker, "_build_settings",
        lambda: _build_settings(path=tmp_path / "settings.ini"))
    monkeypatch.setattr(
        gui_picker.PickerWindow, "_refresh_station_catalog",
        lambda *_args: None)

    gui_theme.apply_theme(qt_app, color_style="standard")

    window = gui_picker.PickerWindow()
    # Background probes would otherwise fire network work during the test.
    window._avail_timer.stop()
    window._catalog_timer.stop()
    window._model_availability_timer.stop()
    yield window
    window.close()


# ---------------------------------------------------------------------------
# Application-level application
# ---------------------------------------------------------------------------


def test_theme_lives_on_the_application_not_the_window(picker, qt_app):
    """A per-window style sheet would miss lazily built panels and dialogs."""
    assert qt_app.styleSheet(), "no application-level chrome style sheet"
    assert not picker.styleSheet(), (
        "picker sets its own style sheet; lazily built panels and dialogs would "
        "not inherit it")


def test_constructing_the_picker_applies_the_theme_by_itself(
        qt_app, monkeypatch, tmp_path):
    """Entry points that bypass ``main`` must still get themed chrome.

    The test suite and any embedder construct ``PickerWindow`` directly. Before
    the theme moved onto the application this was covered by the window styling
    itself in ``__init__``.
    """
    monkeypatch.setattr(
        gui_picker, "_build_settings",
        lambda: _build_settings(path=tmp_path / "settings.ini"))
    monkeypatch.setattr(
        gui_picker.PickerWindow, "_refresh_station_catalog",
        lambda *_args: None)
    monkeypatch.setattr(gui_theme, "_theme_applied", False)
    qt_app.setStyleSheet("")

    window = gui_picker.PickerWindow()
    try:
        assert qt_app.styleSheet(), (
            "constructing the picker did not install the chrome theme")
        assert gui_theme.theme_is_applied()
    finally:
        window.close()


def test_ensure_theme_applied_is_idempotent(qt_app):
    """``main`` applies the theme first; later calls must not re-apply."""
    gui_theme.apply_theme(qt_app, color_style="standard")
    first = qt_app.styleSheet()
    gui_theme.ensure_theme_applied(qt_app, color_style="inverted")
    assert qt_app.styleSheet() == first, (
        "ensure_theme_applied overwrote an already-applied theme")


# ---------------------------------------------------------------------------
# Panel and dialog construction
# ---------------------------------------------------------------------------


def _materialize_all(picker):
    """Build every lazily created source panel.

    ``_ensure_tab`` is keyed by tab *title*, not index; passing an index
    silently does nothing.
    """
    for title in PANEL_TITLES:
        picker._ensure_tab(title)


def test_every_source_panel_materializes_under_the_theme(picker):
    """No style-sheet selector may crash a panel that is built lazily."""
    _materialize_all(picker)
    titles = tuple(picker._tabs.tabText(i) for i in range(picker._tabs.count()))
    assert titles == PANEL_TITLES
    for index in range(picker._tabs.count()):
        panel = picker._tabs.widget(index)
        assert panel is not None
        labels = [w.text() for w in panel.findChildren(QLabel)]
        assert not any(text.startswith("Preparing") for text in labels), (
            f"{titles[index]!r} is still showing its lazy placeholder")


def test_saved_locations_dialog_inherits_the_theme(picker, qt_app):
    """Dialogs set no style sheet of their own, so they must inherit."""
    from sharpmod.gui_locations import SavedLocationsDialog

    dialog = SavedLocationsDialog(
        picker._saved_location_store,
        current_point=picker._current_location_point,
        use_callback=picker._apply_saved_location,
        parent=picker,
    )
    try:
        assert not dialog.styleSheet(), (
            "dialog overrides the inherited chrome theme")
        assert qt_app.styleSheet()
    finally:
        dialog.close()


# ---------------------------------------------------------------------------
# Semantic object names
# ---------------------------------------------------------------------------


def test_no_inline_stylesheets_remain_in_the_picker():
    """Inline styles cannot follow a theme change, so none may remain.

    Guards the migration of 17 hardcoded ``color: gray`` / ``color: #aeb8c8``
    declarations onto semantic object names.
    """
    from pathlib import Path

    source = Path(gui_picker.__file__).read_text(encoding="utf-8")
    assert "setStyleSheet" not in source, (
        "gui_picker.py reintroduced an inline style sheet; assign a semantic "
        "object name and style it in sharpmod.theme instead")


def test_each_source_panel_has_an_accent_primary(picker):
    """One primary action per panel, or nothing reads as primary."""
    _materialize_all(picker)

    for index in range(picker._tabs.count()):
        panel = picker._tabs.widget(index)
        primaries = [b for b in panel.findChildren(QPushButton)
                     if b.objectName() == T.OBJ_PRIMARY]
        title = picker._tabs.tabText(index)
        assert primaries, f"{title!r} has no accent primary action"


def test_progress_detail_labels_use_the_numeric_role(picker):
    """Byte counters must be monospace so digits do not jitter in place."""
    picker._ensure_tab("Forecast Model")
    assert picker._model_progress_detail.objectName() == T.OBJ_PROGRESS_DETAIL


def test_readiness_prose_is_not_given_the_numeric_role(picker):
    """Sentences stay in the UI family; only columns of figures go mono."""
    picker._ensure_tab("Reanalysis (ERA5)")
    assert picker._era5_readiness.objectName() == T.OBJ_STATUS


# ---------------------------------------------------------------------------
# Live theme switching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("style,expected", [
    ("standard", T.GRAPHITE_DARK.name),
    ("inverted", T.PAPER_LIGHT.name),
    ("protanopia", T.PROTANOPIA_DARK.name),
])
def test_switching_palette_retheme_the_application(qt_app, style, expected):
    """Chrome follows the canvas palette, live, without a restart."""
    applied = gui_theme.apply_theme(qt_app, color_style=style)
    assert applied.name == expected
    window_bg = qt_app.palette().color(
        qt_app.palette().ColorRole.Window).name().lower()
    assert window_bg == applied.surface.lower()
    assert applied.surface in qt_app.styleSheet()


def test_light_palette_produces_light_chrome(qt_app):
    """The dark-picker / light-viewer split is what this replaces."""
    gui_theme.apply_theme(qt_app, color_style="inverted")
    assert gui_theme.current_theme().is_dark is False
    gui_theme.apply_theme(qt_app, color_style="standard")
    assert gui_theme.current_theme().is_dark is True


# ---------------------------------------------------------------------------
# Font survival
# ---------------------------------------------------------------------------


def test_bundled_chrome_families_register(qt_app):
    """Space Grotesk and JetBrains Mono ship with the package."""
    families = gui_theme.install_chrome_fonts()
    assert "Space Grotesk" in families
    assert "JetBrains Mono" in families


def test_ui_font_survives_a_forced_qfont_family(qt_app, monkeypatch):
    """``render.install_font`` replaces ``QFont`` and rewrites the family.

    It runs when the first sounding opens, i.e. after the picker is on screen.
    ``ui_font``/``mono_font`` must therefore set the family *after*
    construction, since only the constructor is intercepted.
    """
    class _ForcedFont(QFont):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.setFamily("Forced Substitute")

    monkeypatch.setattr("sharpmod.gui_theme.QFont", _ForcedFont)

    assert gui_theme.ui_font("body").family() == "Space Grotesk"
    assert gui_theme.mono_font("small").family() == "JetBrains Mono"


def test_stylesheet_font_outranks_the_application_font(qt_app):
    """The style sheet carries the chrome family, not just the app font.

    ``app.setFont`` is overwritten by ``render.install_font`` mid-session, so
    the style-sheet declaration is what keeps the picker's typography stable.
    """
    gui_theme.apply_theme(qt_app, color_style="standard")
    qt_app.setFont(QFont("Forced Substitute", 7))
    assert "Space Grotesk" in qt_app.styleSheet()


# ---------------------------------------------------------------------------
# Availability chip renders from the cascade
# ---------------------------------------------------------------------------


def _rendered_dot_colour(chip):
    """Render the chip's dot offscreen and sample its centre pixel.

    Checks the colour Qt actually painted, not just the property that was set --
    property selectors silently do nothing if the widget is not re-polished.
    """
    from qtpy.QtGui import QImage

    dot = chip._dot
    dot.resize(14, 14)
    image = QImage(14, 14, QImage.Format_ARGB32)
    image.fill(0)
    dot.render(image)
    return image.pixelColor(7, 7).name().lower()


@pytest.mark.parametrize("style", ["standard", "inverted", "protanopia"])
def test_availability_chip_paints_the_token_colour_for_every_state(
        qt_app, style):
    """The chip must resolve its colours through the style-sheet cascade.

    It used to rewrite its own style sheet on every update, which overrode the
    active theme. Now a dynamic Qt property selects a rule from the generated
    sheet, so this asserts the rendered pixel equals the token.
    """
    from sharpmod.gui_workers import AVAIL_STATES, _AvailabilityIndicator

    theme = gui_theme.apply_theme(qt_app, color_style=style)
    chip = _AvailabilityIndicator()
    try:
        chip.resize(280, 48)
        for state in AVAIL_STATES:
            chip.set_status(state)
            qt_app.processEvents()
            expected = getattr(theme, T.AVAIL_STATUS_ROLES[state]).lower()
            assert _rendered_dot_colour(chip) == expected, (
                f"{theme.name}: {state!r} dot did not paint {expected}")
    finally:
        chip.deleteLater()


def test_availability_chip_sets_no_inline_stylesheet(qt_app):
    """Inline styles cannot follow a theme change."""
    from sharpmod.gui_workers import AVAIL_STATES, _AvailabilityIndicator

    gui_theme.apply_theme(qt_app, color_style="standard")
    chip = _AvailabilityIndicator()
    try:
        for state in AVAIL_STATES:
            chip.set_status(state, message="probe")
            assert not chip._dot.styleSheet()
            assert not chip._text.styleSheet()
            assert not chip._station.styleSheet()
    finally:
        chip.deleteLater()


def test_availability_chip_always_carries_a_text_label(qt_app):
    """Colour must never be the only signal of state."""
    from sharpmod.gui_workers import AVAIL_STATES, _AvailabilityIndicator

    gui_theme.apply_theme(qt_app, color_style="standard")
    chip = _AvailabilityIndicator()
    try:
        for state in AVAIL_STATES:
            chip.set_status(state)
            assert chip._text.text().strip(), (
                f"state {state!r} conveys itself by colour alone")
    finally:
        chip.deleteLater()


def test_unknown_availability_state_falls_back_instead_of_going_unstyled(
        qt_app):
    """An unrecognised state must not leave the chip in a default colour."""
    from sharpmod.gui_workers import _AvailabilityIndicator

    theme = gui_theme.apply_theme(qt_app, color_style="standard")
    chip = _AvailabilityIndicator()
    try:
        chip.resize(280, 48)
        chip.set_status("not-a-real-state")
        qt_app.processEvents()
        expected = getattr(theme, T.AVAIL_STATUS_ROLES["unknown"]).lower()
        assert _rendered_dot_colour(chip) == expected
    finally:
        chip.deleteLater()


# ---------------------------------------------------------------------------
# Control-rail geometry
# ---------------------------------------------------------------------------

#: Attribute names of the scrollable control rails. All five source panels now
#: use the same [rail | content] structure; Station List was previously a single
#: full-width column, which stretched its controls across the whole window.
RAIL_ATTRS = (
    "_map_controls_scroll",
    "_uwyo_controls_scroll",
    "_model_controls_scroll",
    "_era5_controls_scroll",
    "_wrf_controls_scroll",
)


def test_every_source_panel_uses_the_shared_rail_structure(picker):
    """All five panels share one [control rail | content] layout."""
    _materialize_all(picker)
    if hasattr(picker, "_file_modes"):
        picker._file_modes.setCurrentIndex(1)
    missing = [attr for attr in RAIL_ATTRS if getattr(picker, attr, None) is None]
    assert not missing, f"panels without a control rail: {missing}"


def _built_rails(picker):
    _materialize_all(picker)
    # The WRF rail lives inside the Open File panel's second sub-mode.
    if hasattr(picker, "_file_modes"):
        picker._file_modes.setCurrentIndex(1)
    rails = []
    for attr in RAIL_ATTRS:
        rail = getattr(picker, attr, None)
        if rail is not None:
            rails.append((attr, rail))
    return rails


def test_no_control_rail_clips_its_widest_card(picker, qt_app):
    """Horizontal scrolling is disabled, so content must fit the viewport.

    The forecast panel's "Point" card is 372 px wide. With the old 380 px rail
    cap the scrollbar left a 368 px viewport, so the card's right border was cut
    off as soon as the rail grew tall enough to scroll.
    """
    picker.resize(1440, 900)
    picker.show()
    qt_app.processEvents()

    failures = []
    for attr, rail in _built_rails(picker):
        qt_app.processEvents()
        needed = rail.widget().minimumSizeHint().width()
        available = rail.viewport().width()
        if needed > available:
            failures.append(f"{attr}: needs {needed}px, viewport {available}px")
    assert not failures, "control rail clipping:\n  " + "\n  ".join(failures)


def test_every_control_rail_reserves_room_for_its_scrollbar(picker, qt_app):
    """The rail's outer width must exceed its usable content width."""
    picker.resize(1440, 900)
    picker.show()
    qt_app.processEvents()

    for attr, rail in _built_rails(picker):
        assert rail.width() >= T.RAIL_W["max"] + T.SCROLLBAR_W, (
            f"{attr} does not reserve the scrollbar width on top of the "
            f"{T.RAIL_W['max']}px content area")


def test_control_rails_share_one_width(picker, qt_app):
    """A rail that resizes per panel moves the map divider when switching.

    The three panels previously ran at 324, 380, and 412 px.
    """
    picker.resize(1440, 900)
    picker.show()
    qt_app.processEvents()

    widths = {attr: rail.width() for attr, rail in _built_rails(picker)}
    assert len(set(widths.values())) == 1, (
        f"control rails disagree on width: {widths}")


# ---------------------------------------------------------------------------
# Navigation rail
# ---------------------------------------------------------------------------


def test_source_selector_replaces_the_tab_bar(picker):
    """The picker navigates by rail, not by a top tab bar."""
    from qtpy.QtWidgets import QTabWidget

    from sharpmod.gui_shell import SourceSelector

    assert isinstance(picker._tabs, SourceSelector)
    assert not isinstance(picker._tabs, QTabWidget)


def test_source_selector_keeps_the_tab_widget_surface(picker):
    """Roughly forty title-keyed call sites depend on this API."""
    selector = picker._tabs
    for method in ("addTab", "insertTab", "removeTab", "count", "tabText",
                   "widget", "currentIndex", "setCurrentIndex",
                   "currentWidget", "indexOf"):
        assert callable(getattr(selector, method, None)), (
            f"SourceSelector is missing {method}()")
    assert hasattr(selector, "currentChanged")


def test_nav_rail_selection_and_panel_stay_in_step(picker, qt_app):
    """Clicking a rail entry must show the matching panel, and vice versa."""
    _materialize_all(picker)
    selector = picker._tabs

    for index in range(selector.count()):
        selector.setCurrentIndex(index)
        qt_app.processEvents()
        assert selector.currentIndex() == index
        assert selector._nav.currentRow() == index, (
            "rail highlight drifted from the visible panel")

    # And in the other direction: a rail click drives the stack.
    for row in reversed(range(selector.count())):
        selector._nav.setCurrentRow(row)
        qt_app.processEvents()
        assert selector.currentIndex() == row


def test_tab_text_survives_an_out_of_range_index(picker):
    """The picker compares tabText during teardown, when the index can be -1."""
    selector = picker._tabs
    assert selector.tabText(-1) == ""
    assert selector.tabText(selector.count() + 5) == ""


def test_lazy_placeholder_swap_preserves_order(picker, qt_app):
    """Materializing a panel must not reorder the rail.

    ``_ensure_tab`` removes the placeholder and inserts the real panel at the
    same index, so the rail entry has to follow.
    """
    selector = picker._tabs
    before = [selector.tabText(i) for i in range(selector.count())]
    _materialize_all(picker)
    qt_app.processEvents()
    after = [selector.tabText(i) for i in range(selector.count())]
    assert before == after == list(PANEL_TITLES)
    assert [selector._nav.item(i).text() for i in range(selector.count())] \
        == list(PANEL_TITLES), "rail labels drifted from the panel order"


# ---------------------------------------------------------------------------
# Busy labels
# ---------------------------------------------------------------------------

#: Primary actions whose label a busy handler temporarily replaces.
BUSY_BUTTONS = (
    ("_fetch_btn", "Station List"),
    ("_map_gen_btn", "Station Map"),
    ("_model_fetch_btn", "Forecast Model"),
    ("_era5_fetch_btn", "Reanalysis (ERA5)"),
    ("_wrf_extract_btn", "Open File"),
)


def test_busy_label_is_restored_from_the_widget_not_a_literal(picker, qt_app):
    """The restore path must not carry its own copy of the idle label.

    Each busy handler used to re-type the label, so the same string appeared in
    the panel builder and again in the handler. Renaming a button in the builder
    silently reverted it after the first fetch. The helper stashes the live text
    instead, so this test renames each button and checks the rename survives a
    busy/idle cycle.
    """
    from sharpmod.gui_picker import _set_button_busy

    _materialize_all(picker)
    if hasattr(picker, "_file_modes"):
        picker._file_modes.setCurrentIndex(1)

    for attr, panel in BUSY_BUTTONS:
        button = getattr(picker, attr, None)
        if button is None:
            continue
        renamed = f"Renamed {panel} Action"
        button.setText(renamed)

        _set_button_busy(button, True, "Working\u2026")
        assert button.text() == "Working\u2026"
        assert not button.isEnabled(), "a busy button must not stay clickable"

        _set_button_busy(button, False, "")
        assert button.text() == renamed, (
            f"{attr} was restored to a hardcoded label instead of its own text")


def test_repeated_busy_cycles_do_not_lose_the_idle_label(picker):
    """A second busy pass must not stash the busy text as the idle label."""
    from sharpmod.gui_picker import _set_button_busy

    button = picker._map_gen_btn
    original = button.text()

    for _ in range(3):
        _set_button_busy(button, True, "Fetching\u2026")
        # Progress updates overwrite the label mid-flight, as the model panel
        # does with "Downloading... 47%". That must not become the idle label.
        button.setText("Downloading\u2026 47%")
        _set_button_busy(button, False, "")

    assert button.text() == original


def test_busy_helper_tolerates_a_missing_button(picker):
    """Panels are built lazily, so a handler can fire before its button exists."""
    from sharpmod.gui_picker import _set_button_busy

    _set_button_busy(None, True, "Working\u2026")  # must not raise
    _set_button_busy(None, False, "")
