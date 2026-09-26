"""Integration tests for chrome theming across the real widget tree.

:mod:`test_gui_theme_tokens` covers the token values and the generated style
sheet in isolation. This module checks the parts that only break once real
widgets exist:

* The theme is applied on the ``QApplication``, not per window. That matters
  because five of the picker's six source panels are materialized lazily and
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

from datetime import datetime, timezone
from pathlib import Path
import pytest

from qtpy.QtCore import QDate, QSettings, QTimer, Qt
from qtpy.QtGui import QFont
from qtpy.QtWidgets import QComboBox, QLabel, QPushButton, QStyle, QStyleOptionSlider

from sharpmod import gui_picker, gui_theme
from sharpmod import theme as T
from sharpmod.gui_settings import _build_settings

#: The picker's six source panels, by tab label.
PANEL_TITLES = (
    "Station Map",
    "Station List",
    "Forecast Model",
    "Field Panels",
    "Reanalysis (ERA5)",
    "Open File",
)


@pytest.fixture
def picker(standard_qt_app, monkeypatch, tmp_path):
    """A fully materialized picker under an isolated settings file."""
    qt_app = standard_qt_app
    monkeypatch.setattr(
        gui_picker,
        "_build_settings",
        lambda: _build_settings(path=tmp_path / "settings.ini"),
    )
    monkeypatch.setattr(
        gui_picker.PickerWindow, "_refresh_station_catalog", lambda *_args: None
    )

    window = gui_picker.PickerWindow()
    # Background probes would otherwise fire network work during the test.
    window._avail_timer.stop()
    window._catalog_timer.stop()
    window._model_availability_timer.stop()
    yield window
    window.close()
    window.deleteLater()
    from qtpy.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    if (
        gui_theme.current_theme() != gui_theme.theme_for_color_style("standard")
        or gui_theme.current_text_scale() != 100
        or gui_theme.current_density() != "comfortable"
    ):
        gui_theme.apply_theme(qt_app, color_style="standard")


@pytest.fixture(scope="module")
def rails_picker(qt_app, tmp_path_factory):
    """One fully materialized picker shared by the rail-geometry tests.

    Module scope because one PickerWindow build costs ~2s of the ~7-30s
    geometry assertions; per-test construction multiplied that across the
    four text-scale legs without adding another correctness dimension.
    """
    from unittest import mock

    from qtpy.QtCore import QCoreApplication, QEvent

    tmp_path = tmp_path_factory.mktemp("chrome-rails")
    with mock.patch.object(
        gui_picker,
        "_build_settings",
        lambda: _build_settings(path=tmp_path / "settings.ini"),
    ), mock.patch.object(
        gui_picker.PickerWindow, "_refresh_station_catalog", lambda *_args: None
    ):
        window = gui_picker.PickerWindow()
    window._avail_timer.stop()
    window._catalog_timer.stop()
    window._model_availability_timer.stop()
    _materialize_all(window)
    if hasattr(window, "_file_modes"):
        window._file_modes.setCurrentIndex(1)
    rails = [(attr, getattr(window, attr)) for attr in RAIL_ATTRS
             if getattr(window, attr, None) is not None]
    yield window, rails
    window.hide()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    if (
        gui_theme.current_theme() != gui_theme.theme_for_color_style("standard")
        or gui_theme.current_text_scale() != 100
        or gui_theme.current_density() != "comfortable"
    ):
        gui_theme.apply_theme(qt_app, color_style="standard")


# ---------------------------------------------------------------------------
# Application-level application
# ---------------------------------------------------------------------------


def test_theme_lives_on_the_application_not_the_window(picker, qt_app):
    """A per-window style sheet would miss lazily built panels and dialogs."""
    assert qt_app.styleSheet(), "no application-level chrome style sheet"
    assert not picker.styleSheet(), (
        "picker sets its own style sheet; lazily built panels and dialogs would "
        "not inherit it"
    )


def test_constructing_the_picker_applies_the_theme_by_itself(
    qt_app, monkeypatch, tmp_path
):
    """Entry points that bypass ``main`` must still get themed chrome.

    The test suite and any embedder construct ``PickerWindow`` directly. Before
    the theme moved onto the application this was covered by the window styling
    itself in ``__init__``.
    """
    monkeypatch.setattr(
        gui_picker,
        "_build_settings",
        lambda: _build_settings(path=tmp_path / "settings.ini"),
    )
    monkeypatch.setattr(
        gui_picker.PickerWindow, "_refresh_station_catalog", lambda *_args: None
    )
    monkeypatch.setattr(gui_theme, "_theme_applied", False)
    qt_app.setStyleSheet("")

    window = gui_picker.PickerWindow()
    try:
        assert qt_app.styleSheet(), (
            "constructing the picker did not install the chrome theme"
        )
        assert gui_theme.theme_is_applied()
    finally:
        window.close()
        window.deleteLater()


def test_ensure_theme_applied_is_idempotent(qt_app):
    """``main`` applies the theme first; later calls must not re-apply."""
    gui_theme.apply_theme(qt_app, color_style="standard")
    first = qt_app.styleSheet()
    gui_theme.ensure_theme_applied(qt_app)
    assert qt_app.styleSheet() == first, (
        "ensure_theme_applied overwrote an already-applied theme"
    )
    gui_theme.ensure_theme_applied(
        qt_app, color_style="standard", text_scale=100, density="comfortable")
    assert qt_app.styleSheet() == first
    try:
        gui_theme.ensure_theme_applied(qt_app, color_style="inverted")
        assert qt_app.styleSheet() != first, (
            "an explicit color_style must still take precedence"
        )
    finally:
        gui_theme.apply_theme(qt_app, color_style="standard")


def test_reapplying_the_theme_restores_a_replaced_stylesheet(qt_app):
    """The no-op shortcut must compare sheets, not mere non-emptiness."""
    gui_theme.apply_theme(qt_app, color_style="standard")
    expected = qt_app.styleSheet()
    assert expected, "no chrome style sheet to replace"
    qt_app.setStyleSheet("QWidget { color: red; }")
    try:
        restored = gui_theme.apply_theme(qt_app, color_style="standard")
    finally:
        if qt_app.styleSheet() != expected:
            gui_theme.apply_theme(qt_app, color_style="standard")
    assert restored == gui_theme.theme_for_color_style("standard")
    assert qt_app.styleSheet() == expected, (
        "reapplying the theme did not restore the replaced style sheet"
    )


def test_theme_override_with_same_name_rebuilds_the_stylesheet(qt_app):
    """``with_overrides()`` keeps the name but must not keep the old CSS."""
    base = gui_theme.theme_for_color_style("standard")
    tinted = base.with_overrides(accent="#FF00FF")
    assert tinted.name == base.name
    assert tinted != base
    gui_theme.apply_theme(qt_app, theme=base)
    gui_theme.apply_theme(qt_app, theme=tinted)
    try:
        assert gui_theme.current_theme() == tinted
        assert "#FF00FF" in qt_app.styleSheet(), (
            "the cached sheet for the theme name hid the overridden accent"
        )
        assert base.accent not in qt_app.styleSheet()
    finally:
        gui_theme.apply_theme(qt_app, theme=base)
    assert base.accent in qt_app.styleSheet()


def test_ensure_theme_applied_restores_a_replaced_stylesheet(qt_app):
    """The PickerWindow entry point must validate the sheet, not the flag."""
    gui_theme.apply_theme(qt_app, color_style="standard")
    expected = qt_app.styleSheet()
    assert expected, "no chrome style sheet to replace"
    qt_app.setStyleSheet("QWidget { color: red; }")
    try:
        gui_theme.ensure_theme_applied()
    finally:
        if qt_app.styleSheet() != expected:
            gui_theme.apply_theme(qt_app, color_style="standard")
    assert qt_app.styleSheet() == expected, (
        "ensure_theme_applied left the replacement sheet in place"
    )


def test_ensure_theme_applied_keeps_a_non_default_scale(qt_app):
    """A bare call must not downgrade the recorded scale or density."""
    gui_theme.apply_theme(
        qt_app, color_style="standard", text_scale=150, density="compact")
    try:
        sheet_150 = qt_app.styleSheet()
        assert gui_theme.current_text_scale() == 150
        assert gui_theme.current_density() == "compact"
        gui_theme.ensure_theme_applied()
        assert gui_theme.current_text_scale() == 150
        assert gui_theme.current_density() == "compact"
        assert qt_app.styleSheet() == sheet_150, (
            "ensure_theme_applied installed the 100% sheet over a 150% session"
        )
    finally:
        gui_theme.apply_theme(qt_app, color_style="standard")


def test_applying_the_theme_twice_keeps_the_stylesheet_without_repolish(qt_app):
    """The shortcut's only win is skipping the live-widget re-polish."""
    from unittest import mock

    gui_theme.apply_theme(qt_app, color_style="standard")
    expected = qt_app.styleSheet()
    with mock.patch.object(qt_app, "setStyleSheet") as setter:
        gui_theme.apply_theme(qt_app, color_style="standard")
    setter.assert_not_called()
    assert qt_app.styleSheet() == expected


def test_reapplying_the_theme_restores_a_replaced_application_font(qt_app):
    """The shortcut must validate the font, not just the style sheet."""
    from qtpy.QtGui import QFont

    gui_theme.apply_theme(qt_app, color_style="standard")
    expected = gui_theme.ui_font("body")
    drifted = QFont(expected)
    drifted.setPointSizeF(42.0)
    qt_app.setFont(drifted)
    assert qt_app.font() != expected
    try:
        gui_theme.apply_theme(qt_app, color_style="standard")
    finally:
        if qt_app.font() != expected:
            gui_theme.apply_theme(qt_app, color_style="standard")
    assert qt_app.font() == expected, (
        "reapplying the theme left the replaced 42 pt application font in place"
    )


def test_chrome_stylesheet_is_memoized_per_theme_scale_density(qt_app):
    """Repeat scale changes must not regenerate the same sheet text."""
    first = gui_theme.chrome_qss(
        gui_theme.theme_for_color_style("standard"), text_scale=150)
    second = gui_theme.chrome_qss(
        gui_theme.theme_for_color_style("standard"), text_scale=150)
    assert first == second
    gui_theme.apply_theme(qt_app, color_style="standard", text_scale=150)
    assert qt_app.styleSheet() == first
    gui_theme.apply_theme(qt_app, color_style="standard")


@pytest.mark.parametrize("style", ("standard", "inverted", "protanopia"))
@pytest.mark.parametrize("text_scale", (100, 200))
def test_native_tab_scroll_arrows_are_visible_and_scroll_without_selection_loss(
    standard_qt_app, style, text_scale
):
    """Global button padding must not erase the native 16 px scroll arrow."""
    from qtpy.QtTest import QTest
    from qtpy.QtWidgets import QTabWidget, QToolButton, QWidget

    from sharpmod.colors import contrast_ratio

    app = standard_qt_app
    gui_theme.apply_theme(app, color_style=style, text_scale=text_scale)
    tabs = QTabWidget()
    try:
        for index in range(12):
            tabs.addTab(QWidget(), f"Diagnostic {index + 1}")
        tabs.resize(680, 400)
        tabs.show()
        app.processEvents()
        bar = tabs.tabBar()
        right = next(
            button for button in bar.findChildren(QToolButton)
            if button.arrowType() == Qt.RightArrow
        )
        assert right.isVisible() and right.isEnabled()
        assert right.accessibleName()
        image = right.grab().toImage()
        radius = max(1, round(4 * image.devicePixelRatio()))
        center_x, center_y = image.width() // 2, image.height() // 2
        # Inspect only the central glyph region, excluding the button border.
        # This is not a full-window pixel golden or a platform-font assertion.
        ink = sum(
            contrast_ratio(image.pixelColor(x, y).name(),
                           gui_theme.current_theme().surface_raised) > 3
            for x in range(center_x - radius, center_x + radius)
            for y in range(center_y - radius, center_y + radius)
        )
        assert ink > 0, "native tab-scroll arrow was painted as an empty button"
        previous_left = bar.tabRect(tabs.count() - 1).left()
        previous_selection = tabs.currentIndex()
        QTest.mouseClick(right, Qt.LeftButton)
        app.processEvents()
        assert bar.tabRect(tabs.count() - 1).left() < previous_left
        assert tabs.currentIndex() == previous_selection
    finally:
        tabs.close()
        tabs.deleteLater()
        gui_theme.apply_theme(app, color_style="standard")


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
            f"{titles[index]!r} is still showing its lazy placeholder"
        )


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
        assert not dialog.styleSheet(), "dialog overrides the inherited chrome theme"
        assert qt_app.styleSheet()
    finally:
        dialog.close()


def test_units_dialog_inherits_live_theme_and_text_scale(qt_app):
    """A dialog-local dark style would defeat light mode and text scaling."""
    from sharpmod.gui_settings import UNIT_DEFAULTS, _UnitPreferencesDialog

    try:
        gui_theme.apply_theme(
            qt_app,
            color_style="inverted",
            text_scale=150,
            density="compact",
        )
        dialog = _UnitPreferencesDialog(UNIT_DEFAULTS)
        dialog.ensurePolished()
        assert not dialog.styleSheet()
        assert dialog.font().pointSizeF() >= T.FONT_PT["body"] * 1.5
    finally:
        dialog.close()
        gui_theme.apply_theme(qt_app, color_style="standard")


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
        "object name and style it in sharpmod.theme instead"
    )


def test_each_source_panel_has_an_accent_primary(picker):
    """One primary action per panel, or nothing reads as primary."""
    _materialize_all(picker)

    for index in range(picker._tabs.count()):
        panel = picker._tabs.widget(index)
        primaries = [
            b
            for b in panel.findChildren(QPushButton)
            if b.objectName() == T.OBJ_PRIMARY
        ]
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


@pytest.mark.parametrize(
    "style,expected",
    [
        ("standard", T.GRAPHITE_DARK.name),
        ("inverted", T.PAPER_LIGHT.name),
        ("protanopia", T.PROTANOPIA_DARK.name),
    ],
)
def test_switching_palette_retheme_the_application(qt_app, style, expected):
    """Chrome follows the canvas palette, live, without a restart."""
    applied = gui_theme.apply_theme(qt_app, color_style=style)
    assert applied.name == expected
    window_bg = qt_app.palette().color(qt_app.palette().ColorRole.Window).name().lower()
    assert window_bg == applied.surface.lower()
    assert applied.surface in qt_app.styleSheet()


def test_light_palette_produces_light_chrome(qt_app):
    """The dark-picker / light-viewer split is what this replaces."""
    gui_theme.apply_theme(qt_app, color_style="inverted")
    assert gui_theme.current_theme().is_dark is False
    gui_theme.apply_theme(qt_app, color_style="standard")
    assert gui_theme.current_theme().is_dark is True


@pytest.mark.parametrize("scale", T.TEXT_SCALE_OPTIONS)
def test_application_text_scale_updates_programmatic_and_stylesheet_fonts(
    qt_app, scale
):
    """Painter fonts and ordinary widgets must consume the same live scale."""
    try:
        gui_theme.apply_theme(
            qt_app, color_style="standard", text_scale=scale
        )
        assert gui_theme.current_text_scale() == scale
        assert gui_theme.ui_font("body").pointSizeF() == pytest.approx(
            T.FONT_PT["body"] * scale / 100.0
        )
        assert (
            f"font-size: {T.FONT_PT['body'] * scale / 100.0}pt;"
            in qt_app.styleSheet()
        )
    finally:
        gui_theme.apply_theme(qt_app, color_style="standard")


def test_compact_density_reduces_real_button_height_without_shrinking_text(qt_app):
    try:
        gui_theme.apply_theme(
            qt_app, color_style="standard", density="compact"
        )
        compact = QPushButton("Evaluate")
        compact.ensurePolished()
        compact_height = compact.sizeHint().height()
        compact_font = gui_theme.ui_font("body").pointSizeF()

        gui_theme.apply_theme(
            qt_app, color_style="standard", density="comfortable"
        )
        comfortable = QPushButton("Evaluate")
        comfortable.ensurePolished()

        assert gui_theme.current_density() == "comfortable"
        assert compact_height < comfortable.sizeHint().height()
        assert gui_theme.ui_font("body").pointSizeF() == compact_font
    finally:
        compact.deleteLater()
        comfortable.deleteLater()
        gui_theme.apply_theme(qt_app, color_style="standard")


@pytest.mark.parametrize("accept", (True, False), ids=("accept", "cancel"))
def test_real_preferences_dialog_applies_interface_changes_only_on_accept(
    picker, qt_app, monkeypatch, accept
):
    """Exercise the actual native Preferences dialog and durable controller path."""
    original_builder = gui_picker._build_preferences_dialog

    def build_and_choose(config, parent=None):
        dialog = original_builder(config, parent=parent)

        def choose():
            scale = dialog.findChild(QComboBox, "interfaceTextScale")
            density = dialog.findChild(QComboBox, "interfaceDensity")
            scale.setCurrentIndex(scale.findData("200"))
            density.setCurrentIndex(density.findData("compact"))
            dialog.accept() if accept else dialog.reject()

        QTimer.singleShot(0, choose)
        return dialog

    monkeypatch.setattr(gui_picker, "_build_preferences_dialog", build_and_choose)
    try:
        picker.preferencesbox()
        qt_app.processEvents()
        expected = {"text_scale": "200", "density": "compact"} if accept else {
            "text_scale": "100", "density": "comfortable",
        }
        assert picker._interface_preferences() == expected
        reread = QSettings(picker._settings.fileName(), QSettings.IniFormat)
        assert reread.value("interface/text_scale", "100", str) == expected["text_scale"]
        assert reread.value("interface/density", "comfortable", str) == expected["density"]
        assert gui_theme.current_text_scale() == int(expected["text_scale"])
        assert gui_theme.current_density() == expected["density"]
    finally:
        picker.hide()


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
def test_availability_chip_paints_the_token_colour_for_every_state(qt_app, style):
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
                f"{theme.name}: {state!r} dot did not paint {expected}"
            )
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
                f"state {state!r} conveys itself by colour alone"
            )
    finally:
        chip.deleteLater()


def test_unknown_availability_state_falls_back_instead_of_going_unstyled(qt_app):
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

#: Attribute names of the scrollable control rails. All six source panels now
#: use the same [rail | content] structure; Station List was previously a single
#: full-width column, which stretched its controls across the whole window.
RAIL_ATTRS = (
    "_map_controls_scroll",
    "_uwyo_controls_scroll",
    "_model_controls_scroll",
    "_panels_controls_scroll",
    "_era5_controls_scroll",
    "_wrf_controls_scroll",
)


def test_every_source_panel_uses_the_shared_rail_structure(picker):
    """All six panels share one [control rail | content] layout."""
    _materialize_all(picker)
    if hasattr(picker, "_file_modes"):
        picker._file_modes.setCurrentIndex(1)
    missing = [attr for attr in RAIL_ATTRS if getattr(picker, attr, None) is None]
    assert not missing, f"panels without a control rail: {missing}"


@pytest.mark.parametrize("scale", (100, 200))
def test_all_selection_panes_keep_summary_and_load_visible_when_collapsed(
    picker, qt_app, monkeypatch, scale
):
    """Actual six-source workflow keeps context/actions outside scroll and collapse."""
    monkeypatch.setattr(picker, "_queue_availability", lambda *_a, **_kw: None)
    monkeypatch.setattr(picker, "_queue_model_availability", lambda *_a: None)
    _materialize_all(picker)
    picker._file_modes.setCurrentIndex(1)
    if scale != 100:
        gui_theme.apply_theme(qt_app, color_style="standard", text_scale=scale)
    picker.resize(1000, 650)
    picker.show()
    entries = (
        ("Station Map", "map", "_map_gen_btn"),
        ("Station List", "uwyo", "_fetch_btn"),
        ("Forecast Model", "model", "_model_fetch_btn"),
        ("Field Panels", "panels", "_panels_fetch_btn"),
        ("Reanalysis (ERA5)", "era5", "_era5_fetch_btn"),
        ("Open File", "wrf", "_wrf_extract_btn"),
    )
    try:
        for title, source, button_name in entries:
            picker._select_tab(title)
            for _ in range(8):
                qt_app.processEvents()
            pane = getattr(picker, f"_{source}_selection_pane")
            primary = getattr(picker, button_name)
            before = pane.summary.text()
            assert before and pane.summary.accessibleDescription() == before
            pane.set_collapsed(True)
            pane.rail.verticalScrollBar().setValue(pane.rail.verticalScrollBar().maximum())
            for _ in range(8):
                qt_app.processEvents()
            assert pane.rail.isHidden()
            # The summary is presented in the top bar beside the clock now. The
            # guarantee is unchanged, though: hiding the configuration must not
            # hide the selection context. At large text scales the menu row cannot
            # spare the width, and the pane presents it instead of it disappearing.
            if pane.summary_is_inline():
                summary = pane.summary
                assert summary.text() == before
            else:
                summary = picker._top_summary
                # The visible label elides to its budget, so compare the full text.
                assert summary.toolTip() == before
                assert summary.accessibleDescription() == before
            assert summary.isVisible()
            assert primary.isVisible() and not pane.rail.isAncestorOf(primary)
            assert picker.rect().contains(primary.mapTo(picker, primary.rect().bottomRight()))
            assert picker.rect().contains(
                summary.mapTo(picker, summary.rect().bottomRight())
            )
            # Exactly one of the two presents it, never both and never neither.
            assert pane.summary.isVisible() != picker._top_summary.isVisible()
            # And the rail toggle no longer spends a row of the window.
            assert pane.toggle.isHidden()
            pane.set_collapsed(False)
    finally:
        picker.hide()


def test_view_menu_hides_the_active_sources_controls_and_follows_the_tab(
    picker, qt_app
):
    """The rail toggle is a View menu item now, not a button in every source.

    It has to act on whichever source is showing, and it has to report that
    source's state, or one pane's collapse would appear to belong to another.
    """
    picker.resize(1800, 950)
    picker.show()
    for _ in range(8):
        qt_app.processEvents()
    action = picker._hide_controls_action
    assert action.isEnabled()

    picker._select_tab("Station Map")
    for _ in range(8):
        qt_app.processEvents()
    station_map = picker._map_selection_pane
    assert action.isChecked() is station_map.is_collapsed()

    action.setChecked(True)
    for _ in range(8):
        qt_app.processEvents()
    assert station_map.rail.isHidden()
    assert station_map.is_collapsed()

    # Switching source shows that source's own state, not the one just set.
    picker._select_tab("Forecast Model")
    for _ in range(8):
        qt_app.processEvents()
    model = picker._model_selection_pane
    assert action.isChecked() is model.is_collapsed()
    assert not model.rail.isHidden()
    assert action.isChecked() is False

    # And collapsing this one leaves the other one collapsed, as it was.
    action.setChecked(True)
    for _ in range(8):
        qt_app.processEvents()
    assert model.rail.isHidden()
    assert station_map.rail.isHidden()

    # Driving the pane directly still updates the menu item, so the two cannot
    # disagree about what the reader is looking at.
    model.set_collapsed(False)
    for _ in range(8):
        qt_app.processEvents()
    assert action.isChecked() is False

    station_map.set_collapsed(False)
    for _ in range(8):
        qt_app.processEvents()


def test_the_top_bar_shows_the_active_sources_context_beside_the_clock(
    picker, qt_app
):
    picker.resize(1800, 950)
    picker.show()
    for _ in range(8):
        qt_app.processEvents()

    picker._select_tab("Forecast Model")
    for _ in range(10):
        qt_app.processEvents()
    pane = picker._model_selection_pane
    summary = picker._top_summary
    divider = picker._top_summary_divider

    assert pane.summary_text()
    assert summary.isVisible()
    assert summary.toolTip() == pane.summary_text()
    assert "Requested point" in summary.text()
    assert "Initialization" not in summary.text()
    assert "Valid " not in summary.text()
    # Divided from the clock by the compact-metadata separator.
    assert divider.isVisible()
    assert divider.text().strip() == "\u00b7"
    # Left of the clock, inside the same corner widget.
    assert summary.x() < picker._utc_clock.x()
    assert summary.parent() is picker._utc_clock.parent()

    # It follows the source, and a source with different context replaces it.
    picker._select_tab("Station Map")
    for _ in range(10):
        qt_app.processEvents()
    assert picker._top_summary.toolTip() == picker._map_selection_pane.summary_text()
    assert "Requested observation" not in picker._top_summary.text()


def test_the_live_clock_does_not_repeat_the_date_or_zulu_time():
    now = datetime(2026, 9, 23, 9, 46, 59, tzinfo=timezone.utc)

    assert gui_picker.PickerWindow._format_utc_clock(now) == "09:46 UTC"


def test_picker_feedback_updates_actual_time_point_and_corrective_focus(
    picker, qt_app, monkeypatch
):
    from sharpmod.gui_picker_layout import CollapsibleRailSection

    monkeypatch.setattr(picker, "_queue_availability", lambda *_a, **_kw: None)
    monkeypatch.setattr(picker, "_queue_model_availability", lambda *_a: None)
    picker._select_tab("Forecast Model")
    picker.resize(1200, 750)
    picker.show()
    pane = picker._model_selection_pane
    picker._model_date.setDate(QDate(2024, 5, 20))
    picker._model_lat.setValue(0)
    picker._model_lon.setValue(0)
    for _ in range(8):
        qt_app.processEvents()
    assert "Initialization 2024-05-20" in pane.summary.text()
    assert "Requested point 0.0000°, 0.0000°" in pane.summary.text()
    assert "outside" in pane.feedback.text()
    assert pane.feedback.text().startswith("Warning:")
    assert picker._model_box.isAncestorOf(pane.feedback)
    assert picker._model_box.isAncestorOf(pane.corrective)
    assert pane.layout().indexOf(pane.feedback) == -1
    assert pane.header.indexOf(pane.corrective) == -1
    assert not picker._model_point_status.isVisible()
    assert not picker._model_fetch_btn.isEnabled()
    pane.set_collapsed(True)
    pane.corrective.click()
    for _ in range(8):
        qt_app.processEvents()
    assert not pane.rail.isHidden()
    assert picker._model_lat.hasFocus()
    location = next(
        section for section in pane.rail.findChildren(CollapsibleRailSection)
        if section.title() == "Map location"
    )
    assert location.is_expanded()
    assert "0.0000°" in location.summary.text()
    assert picker._model_lat.value() == 0  # correction revealed, never chose a point
    picker._model_lat.setValue(35.63)
    picker._model_lon.setValue(-97.44)
    location.set_collapsed(True)
    for _ in range(8):
        qt_app.processEvents()
    assert location.summary.isVisible()
    assert "35.6300°" in location.summary.text()
    assert "-97.4400°" in location.summary.text()
    assert picker._model_fetch_btn.isEnabled()
    assert pane.feedback.isHidden()
    assert pane.corrective.isHidden()


def test_picker_observation_summary_selection_and_source_are_truthful(
    picker, qt_app, monkeypatch
):
    monkeypatch.setattr(picker, "_queue_availability", lambda *_a, **_kw: None)
    picker.show()
    pane = picker._map_selection_pane
    picker._map_selected_id = None
    picker._map_gen_btn.setEnabled(False)
    picker._map_selection_feedback.refresh()
    assert "No station selected" in pane.summary.text()
    assert "Choose a station" in pane.feedback.text()
    station = picker._all_stations[0]
    picker._map_on_select(station["id"], check_availability=False)
    picker._map_date.setDate(QDate(2024, 6, 16))
    for _ in range(8):
        qt_app.processEvents()
    assert station["name"] in pane.summary.text()
    assert "Requested observation 2024-06-16" in pane.summary.text()
    assert picker._map_gen_btn.isEnabled()
    assert pane.feedback.isHidden()
    pane.set_collapsed(True)
    picker._map_source.setCurrentIndex((picker._map_source.currentIndex() + 1)
                                      % picker._map_source.count())
    for _ in range(8):
        qt_app.processEvents()
    assert picker._map_source.currentText() in pane.summary.text()
    assert picker._map_selected_id == station["id"]


def test_picker_missing_file_and_lead_give_inline_corrective_guidance(
    picker, qt_app, monkeypatch, tmp_path
):
    monkeypatch.setattr(picker, "_queue_model_availability", lambda *_a: None)
    picker._select_tab("Open File")
    picker.show()
    picker._file_edit.clear()
    assert not picker._file_open_btn.isEnabled()
    assert "Browse" in picker._file_selection_guidance.text()
    picker._file_edit.setText(str(tmp_path / "does-not-exist.npz"))
    assert "not found" in picker._file_selection_guidance.text()
    assert "valid time will be read" in picker._file_selection_summary.text()
    from sharpmod.tests._examples import examples_dir

    actual_file = examples_dir() / "14061619.OAX"
    assert actual_file.is_file()
    picker._file_edit.setText(f'"{actual_file}"')
    assert picker._file_open_btn.isEnabled()
    assert picker._file_selection_guidance.isHidden()
    picker._select_tab("Forecast Model")
    picker._model_fxx_combo.clear()
    for _ in range(8):
        qt_app.processEvents()
    pane = picker._model_selection_pane
    assert "No forecast lead selected" in pane.summary.text()
    assert "Choose a forecast lead" in pane.feedback.text()
    assert not picker._model_fetch_btn.isEnabled()


def test_panels_sticky_cancel_delegates_to_actual_existing_worker(
    picker, qt_app, monkeypatch
):
    monkeypatch.setattr(picker, "_queue_model_availability", lambda *_a: None)
    picker._ensure_tab("Forecast Model")
    picker._select_tab("Field Panels")
    picker.show()

    class Worker:
        interrupted = False

        def requestInterruption(self):  # noqa: N802 - Qt API
            self.interrupted = True

    worker = Worker()
    try:
        picker._model_worker = worker
        picker._set_model_busy(True)
        pane = picker._panels_selection_pane
        pane.set_collapsed(True)
        for _ in range(8):
            qt_app.processEvents()
        assert picker._panels_cancel_btn.isVisible()
        assert not pane.rail.isAncestorOf(picker._panels_cancel_btn)
        assert not picker._panels_fetch_btn.isEnabled()
        picker._panels_cancel_btn.click()
        assert worker.interrupted
        assert not picker._panels_cancel_btn.isEnabled()
        assert picker._panels_cancel_btn.text() == "Cancellation requested"
    finally:
        picker._model_worker = None
        picker._set_model_busy(False)


def test_future_era5_selection_is_explained_before_starting_any_request(
    picker, qt_app, monkeypatch
):
    from datetime import datetime, timedelta, timezone

    picker._select_tab("Reanalysis (ERA5)")
    picker.show()
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    monkeypatch.setattr(picker, "_era5_valid_time", lambda: future)
    picker._era5_update_state()
    assert "future analysis" in picker._era5_selection_pane.feedback.text()
    assert not picker._era5_fetch_btn.isEnabled()
    started, messages = [], []
    monkeypatch.setattr(picker, "_ensure_model_cache", lambda: started.append(True))
    from qtpy.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: messages.append(_args[-1]))
    picker._era5_fetch()
    assert not started
    assert "earlier ERA5" in messages[0]


@pytest.mark.parametrize("model,member", (("gefs", "c00"), ("cfs", 1)))
def test_ensemble_selection_summary_names_authoritative_default_member(
    picker, qt_app, monkeypatch, model, member
):
    monkeypatch.setattr(picker, "_queue_model_availability", lambda *_a: None)
    picker._select_tab("Forecast Model")
    picker.show()
    index = picker._model_combo.findData(model)
    assert index >= 0
    picker._model_combo.setCurrentIndex(index)
    picker._model_member.clear()
    for _ in range(8):
        qt_app.processEvents()
    assert picker._model_config().kwargs["member"] == member
    assert f"Member {member} (default)" in picker._model_selection_pane.summary.text()
    picker._model_member_box.set_collapsed(True)
    assert picker._model_member_box.summary.text() == f"Member {member} (default)"


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


def _scrollbar_handle_rect(bar):
    """Return where the style really paints ``bar``'s handle.

    Asked of the style rather than the style sheet text, because a declaration
    being present says nothing about Qt honouring it: margin on the handle and a
    transparent border on the handle are both accepted and both leave the rect
    at the full bar width.
    """
    option = QStyleOptionSlider()
    option.rect = bar.rect()
    option.minimum, option.maximum = bar.minimum(), bar.maximum()
    option.sliderPosition = option.sliderValue = bar.value()
    option.pageStep = bar.pageStep()
    option.orientation = bar.orientation()
    return bar.style().subControlRect(
        QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSlider, bar
    )


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


@pytest.mark.parametrize("scale", T.TEXT_SCALE_OPTIONS)
def test_live_text_scaling_keeps_expanded_rail_controls_in_view(
    rails_picker, qt_app, scale, monkeypatch
):
    """Turning up text size must not cut off the right side of a control.

    Three ``processEvents`` drains per tab, not eight: the geometry under test
    (``minimumSizeHint`` vs. viewport width) settles after a few queued
    resizes, and each extra drain repaints the station-map basemap (thousands
    of re-projected boundary segments). One drain is not enough -- the rails
    still report stale widths (model 642 vs. 539, ERA5 534 vs. 473). The tab
    order still visits every panel, so all six rails are measured. The final
    width polls rather than fixed-drains: it exits early when the restored
    theme has settled.
    """
    from sharpmod.gui_picker_layout import CollapsibleRailSection

    picker, rails = rails_picker
    try:
        monkeypatch.setattr(picker, "_queue_availability", lambda *_a, **_kw: None)
        monkeypatch.setattr(picker, "_queue_model_availability", lambda *_a: None)
        for _name, rail in rails:
            for section in rail.findChildren(CollapsibleRailSection):
                section.set_collapsed(False)
        if scale != 100:
            gui_theme.apply_theme(qt_app, color_style="standard", text_scale=scale)
        picker.resize(1800, 1000)
        picker.show()
        for _ in range(3):
            qt_app.processEvents()
        failures = []
        for (name, rail), title in zip(rails, PANEL_TITLES):
            picker._select_tab(title)
            for _ in range(3):
                qt_app.processEvents()
            needed = rail.widget().minimumSizeHint().width()
            available = rail.viewport().width()
            if needed > available:
                failures.append(f"{name}: needs {needed}px, viewport {available}px")
        assert not failures, "scaled rail clipping:\n  " + "\n  ".join(failures)
        if scale != 100:
            gui_theme.apply_theme(qt_app, color_style="standard", text_scale=100)
        # The map rail lives on a hidden tab after the loop above, so its
        # width is stale until the tab is shown and the restored theme's
        # re-polish settles. Poll rather than fixed-draining: fast when the
        # layout is already back, patient when the re-polish needs cycles.
        picker._select_tab("Station Map")
        for _ in range(8):
            qt_app.processEvents()
            if picker._map_controls_scroll.width() == (
                T.RAIL_W["max"] + T.SCROLLBAR_W
            ):
                break
        assert picker._map_controls_scroll.width() == T.RAIL_W["max"] + T.SCROLLBAR_W
    finally:
        picker.hide()


def test_every_control_rail_reserves_room_for_its_scrollbar(picker, qt_app):
    """The rail's outer width must exceed its usable content width."""
    picker.resize(1440, 900)
    picker.show()
    qt_app.processEvents()

    for attr, rail in _built_rails(picker):
        assert rail.width() >= T.RAIL_W["max"] + T.SCROLLBAR_W, (
            f"{attr} does not reserve the scrollbar width on top of the "
            f"{T.RAIL_W['max']}px content area"
        )


def test_the_scrollbar_handle_is_inset_inside_its_reserved_column(picker, qt_app):
    """The handle needs a gutter, or it reads as sitting on the rail content.

    The bar reserves ``SCROLLBAR_W`` and the handle used to fill every pixel of
    it, so a solid slab of handle butted straight against the card border beside
    it -- which is what "the scrollbar overlaps the space" describes. The style
    sheet pads the bar so the handle floats inside the reservation instead.

    Both halves matter. Only checking the inset would pass if the reservation
    shrank to match, which would clip the widest card; only checking the
    reservation is what let the flush handle ship.
    """
    picker.resize(1440, 900)
    picker.show()
    qt_app.processEvents()

    rails = _built_rails(picker)
    assert rails, "no control rail was built, so nothing was checked"

    inset_checked = []
    for attr, rail in rails:
        bar = rail.verticalScrollBar()
        # The size hint, not width(): a rail that does not currently overflow
        # keeps its bar hidden, and a hidden widget's width is meaningless. The
        # hint is what QAbstractScrollArea reserves either way.
        assert bar.sizeHint().width() == T.SCROLLBAR_W, (
            f"{attr}: the bar asks for {bar.sizeHint().width()}px but the "
            f"layout reserves {T.SCROLLBAR_W}px; the two must agree"
        )

        if not bar.isVisible():
            continue
        handle = _scrollbar_handle_rect(bar)
        assert handle.width() < bar.width(), (
            f"{attr}: the handle fills the whole {bar.width()}px bar, so it "
            "touches the content beside it"
        )
        left = handle.x()
        right = bar.width() - (handle.x() + handle.width())
        assert left == right == T.SPACE["xxs"], (
            f"{attr}: handle gutters are {left}px/{right}px, expected "
            f"{T.SPACE['xxs']}px on each side"
        )
        inset_checked.append(attr)

    assert inset_checked, (
        "no rail was scrolling, so the handle inset went unchecked; the rails "
        "are meant to overflow at 1440x900"
    )


def test_control_rails_share_one_width(picker, qt_app):
    """A rail that resizes per panel moves the map divider when switching.

    The three panels previously ran at 324, 380, and 412 px.
    """
    picker.resize(1440, 900)
    picker.show()
    qt_app.processEvents()

    widths = {attr: rail.width() for attr, rail in _built_rails(picker)}
    assert len(set(widths.values())) == 1, f"control rails disagree on width: {widths}"


# ---------------------------------------------------------------------------
# Source dropdown
# ---------------------------------------------------------------------------


def test_source_selector_replaces_the_tab_bar(picker):
    """The picker uses one mutually-exclusive source control, not tabs."""
    from qtpy.QtWidgets import QTabWidget

    from sharpmod.gui_shell import SourceSelector

    assert isinstance(picker._tabs, SourceSelector)
    assert not isinstance(picker._tabs, QTabWidget)


def test_source_selector_keeps_the_tab_widget_surface(picker):
    """Roughly forty title-keyed call sites depend on this API."""
    selector = picker._tabs
    for method in (
        "addTab",
        "insertTab",
        "removeTab",
        "count",
        "tabText",
        "widget",
        "currentIndex",
        "setCurrentIndex",
        "currentWidget",
        "indexOf",
    ):
        assert callable(getattr(selector, method, None)), (
            f"SourceSelector is missing {method}()"
        )
    assert hasattr(selector, "currentChanged")


def test_source_dropdown_lives_in_the_menu_bar(picker):
    """Load From must reclaim the former navigation rail for the maps."""
    selector = picker._tabs
    dropdown = selector.navigation_widget()

    assert isinstance(dropdown, QComboBox)
    assert picker.menuBar().cornerWidget(Qt.TopLeftCorner) is picker._source_picker
    assert dropdown.parent() is picker._source_picker
    assert picker._source_picker_label.text() == "Load From"


def test_export_menu_exposes_standalone_panel_and_recent_map_actions(picker):
    export_menu = next(
        action.menu() for action in picker.menuBar().actions()
        if action.text().replace("&", "") == "Export"
    )
    labels = [action.text() for action in export_menu.actions()
              if not action.isSeparator()]
    assert labels == [
        "Active Map Figure (PNG)…",
        "Two/Four-Panel Map Figure (PNG)…",
        "Recent Exports…",
    ]
    assert picker._map_export_action is export_menu.actions()[0]
    assert picker._panels_export_action is export_menu.actions()[1]


def test_small_large_text_picker_keeps_every_menu_and_source_reachable(picker, qt_app):
    try:
        gui_theme.apply_theme(qt_app, color_style="standard", text_scale=200)
        picker.resize(1000, 650)
        picker.show()
        for _ in range(8):
            qt_app.processEvents()
        bar = picker.menuBar()
        for action in bar.actions():
            rect = bar.actionGeometry(action)
            assert not rect.isEmpty(), f"{action.text()} disappeared behind menu corners"
            assert bar.rect().contains(rect), f"{action.text()} is clipped by menu corners"
            for corner in (Qt.TopLeftCorner, Qt.TopRightCorner):
                widget = bar.cornerWidget(corner)
                assert widget is None or not rect.intersects(widget.geometry())
        assert picker._tabs.navigation_widget().isVisible()
        assert picker.width() == 1000
        before = picker._tabs.currentIndex()
        picker.resize(1800, 950)
        for _ in range(8):
            qt_app.processEvents()
        assert bar.cornerWidget(Qt.TopLeftCorner) is picker._source_picker
        assert picker._tabs.currentIndex() == before
    finally:
        picker.hide()


def test_source_dropdown_selection_and_panel_stay_in_step(picker, qt_app):
    """Choosing a source must show the matching panel, and vice versa."""
    _materialize_all(picker)
    selector = picker._tabs

    for index in range(selector.count()):
        selector.setCurrentIndex(index)
        qt_app.processEvents()
        assert selector.currentIndex() == index
        assert selector._nav.currentIndex() == index, (
            "dropdown choice drifted from the visible panel"
        )

    # And in the other direction: a dropdown choice drives the stack.
    for index in reversed(range(selector.count())):
        selector._nav.setCurrentIndex(index)
        qt_app.processEvents()
        assert selector.currentIndex() == index


def test_tab_text_survives_an_out_of_range_index(picker):
    """The picker compares tabText during teardown, when the index can be -1."""
    selector = picker._tabs
    assert selector.tabText(-1) == ""
    assert selector.tabText(selector.count() + 5) == ""


def test_lazy_placeholder_swap_preserves_order(picker, qt_app):
    """Materializing a panel must not reorder the dropdown.

    ``_ensure_tab`` removes the placeholder and inserts the real panel at the
    same index, so the dropdown entry has to follow.
    """
    selector = picker._tabs
    before = [selector.tabText(i) for i in range(selector.count())]
    _materialize_all(picker)
    qt_app.processEvents()
    after = [selector.tabText(i) for i in range(selector.count())]
    assert before == after == list(PANEL_TITLES)
    assert [selector._nav.itemText(i) for i in range(selector.count())] == list(
        PANEL_TITLES
    ), "dropdown labels drifted from the panel order"


def test_control_rail_sections_use_accessible_chevron_disclosure(picker, qt_app):
    """Dense map controls can collapse while preserving their field state."""
    from sharpmod.gui_picker_layout import CollapsibleRailSection

    panel = picker._ensure_tab("Forecast Model")
    picker.show()
    qt_app.processEvents()
    sections = panel.findChildren(CollapsibleRailSection)
    assert sections, "forecast controls did not use collapsible sections"

    section = sections[0]
    assert section.toggle.accessibleName().endswith(" controls")
    assert section.toggle.arrowType() == Qt.DownArrow
    assert not section.content.isHidden()

    section.toggle.setChecked(False)
    qt_app.processEvents()
    assert section.toggle.arrowType() == Qt.RightArrow
    assert section.content.isHidden()


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
            f"{attr} was restored to a hardcoded label instead of its own text"
        )


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


def test_pasted_points_apply_to_four_actual_sources_without_loading(
        picker, qt_app, monkeypatch):
    for method in ("_queue_model_availability", "_queue_availability"):
        monkeypatch.setattr(picker, method, lambda *_args, **_kwargs: None)
    for source, token in (("Forecast Model", "model"), ("Field Panels", "panels"),
                          ("Reanalysis (ERA5)", "era5"), ("Open File", "wrf")):
        picker._select_tab(source)
        if token == "wrf":
            picker._file_modes.setCurrentIndex(1)
        button = getattr(picker, f"_{token}_paste_coordinates")
        button.click()
        dialog = button._sharpmod_coordinate_controller.dialog
        assert dialog.isVisible()
        lat_spin, lon_spin = getattr(picker, f"_{token}_lat"), getattr(picker, f"_{token}_lon")
        previous = (lat_spin.value(), lon_spin.value())
        dialog.input.setText("lat:35.123456 lon:-97.654321")
        assert (lat_spin.value(), lon_spin.value()) == previous
        dialog.use_point()
        assert (lat_spin.value(), lon_spin.value()) == (35.1235, -97.6543)
        maps = ([panel.map for panel in picker._panels_view._panels] if token == "panels"
                else [getattr(picker, f"_{token}_map")])
        for point_map in maps:
            assert point_map.context_point() == pytest.approx((35.1235, -97.6543))
        qt_app.processEvents()
        assert "35.1235°, -97.6543°" in getattr(picker, f"_{token}_selection_pane").summary.text()
    assert picker._model_worker is None and picker._era5_worker is None
    assert picker._wrf_extract_worker is None


def test_busy_helper_tolerates_a_missing_button(picker):
    """Panels are built lazily, so a handler can fire before its button exists."""
    from sharpmod.gui_picker import _set_button_busy

    _set_button_busy(None, True, "Working\u2026")  # must not raise
    _set_button_busy(None, False, "")


def test_actual_file_open_and_session_restore_record_recent_metadata(
        picker, qt_app, tmp_path, monkeypatch):
    from sharpmod.recent_destinations import RecentDestinationStore
    from sharpmod.sessions import build_session, write_session

    source = str(Path(gui_picker.__file__).resolve().parents[1]
                 / "examples" / "soundings" / "14061619.OAX")
    displayed = []
    monkeypatch.setattr(picker, "_show_sounding", lambda collection, *_a, **_k: displayed.append(collection))
    assert picker._open_file(source) is True
    assert len(displayed) == 1
    file_entry = next(entry for entry in RecentDestinationStore(picker._settings).load() if entry.kind == "file")
    assert file_entry.target == source
    assert file_entry.last_used is not None and "Location: OAX" in file_entry.details
    assert "Valid: 2014-06-16 19:00 UTC" in file_entry.details
    picker._select_tab("Open File")
    assert "Last used:" in picker._recent_list.item(0).text()
    assert source in picker._recent_list.item(0).text()
    missing = str(tmp_path / "moved.spc")
    picker._settings.setValue("recent_files", [missing])
    picker._load_recent_files()
    assert picker._recent_list.count() == 1 and "missing" in picker._recent_list.item(0).text()
    picker._open_recent_file(picker._recent_list.item(0))
    assert picker._recent_destinations_controller.dialog.recover.isVisibleTo(
        picker._recent_destinations_controller.dialog)

    session = tmp_path / "analysis.sharpmod-session"
    write_session(session, build_session(displayed, active_collection=0))
    from qtpy.QtWidgets import QMainWindow

    viewer = QMainWindow()
    monkeypatch.setattr(gui_picker, "compose_interactive", lambda *_a, **_k: viewer)
    monkeypatch.setattr(gui_picker, "_apply_viewer_session_state", lambda *_a, **_k: None)
    try:
        assert picker._open_analysis_session(str(session)) is True
        restored = next(entry for entry in RecentDestinationStore(picker._settings).load() if entry.kind == "session")
        assert restored.last_used is not None and "1 sounding ·" in restored.details
        assert "Location: OAX" in restored.details
    finally:
        viewer.close()
        viewer.deleteLater()


def test_recent_point_uses_actual_field_panels_without_switching_source(
        picker, qt_app, monkeypatch):
    from sharpmod.saved_locations import SavedLocation
    from sharpmod.recent_destinations import RecentDestinationStore

    picker._select_tab("Field Panels")
    monkeypatch.setattr(picker, "_queue_model_availability", lambda *_a, **_k: None)
    location = SavedLocation.create("Test point", 35.1234, -97.5678)
    picker._apply_recent_location(location)
    assert picker._tabs.tabText(picker._tabs.currentIndex()) == "Field Panels"
    for panel in picker._panels_view._panels:
        assert panel.map.context_point() == pytest.approx((location.lat, location.lon))
    entries = RecentDestinationStore(picker._settings).load()
    recent = next(entry for entry in entries if entry.kind == "location")
    assert recent.last_used is not None and recent.details == "Requested point from Field Panels"
    assert recent.label == "Test point"
    assert "Last used:" in picker._recent_locations_menu.actions()[0].toolTip()
    assert picker._model_worker is None
