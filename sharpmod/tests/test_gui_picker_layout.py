"""Native selection arrangement, persistence, and state-preservation checks."""

from qtpy.QtCore import QPoint, QSettings, Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QLineEdit, QVBoxLayout, QWidget

from sharpmod.gui_picker_layout import (
    SUMMARY_JOIN,
    fit_summary_groups,
    scrolling_control_rail,
    selection_pane,
)
from sharpmod.theme import RAIL_W, SCROLLBAR_W


def _pane(settings, *, key="test"):
    layout = QVBoxLayout()
    field = QLineEdit("preserve this selection")
    layout.addWidget(field)
    rail = scrolling_control_rail(layout)
    content = QWidget()
    content.setMinimumWidth(240)
    pane = selection_pane(rail, content, settings=settings, key=key, title="Test")
    pane.resize(1200, 700)
    return pane, field, content


def _settle(app):
    for _ in range(8):
        app.processEvents()


def test_selection_pane_native_resize_collapse_and_reopen_preserve_state(
    standard_qt_app, tmp_path
):
    app = standard_qt_app
    settings_path = str(tmp_path / "settings.ini")
    settings = QSettings(settings_path, QSettings.IniFormat)
    pane, field, content = _pane(settings)
    restored = None
    try:
        pane.show()
        _settle(app)
        assert pane.rail.width() == RAIL_W["max"] + SCROLLBAR_W
        assert pane.rail.maximumWidth() > pane.rail.minimumWidth()
        initial_map_width = content.width()
        handle = pane.splitter.handle(1)
        center = handle.rect().center()
        QTest.mousePress(handle, Qt.LeftButton, pos=center)
        QTest.mouseMove(handle, center + QPoint(130, 0))
        QTest.mouseRelease(handle, Qt.LeftButton, pos=handle.rect().center())
        _settle(app)
        assert pane.rail.width() > RAIL_W["max"] + SCROLLBAR_W
        assert content.width() < initial_map_width
        preferred_width = pane.rail.width()
        assert int(settings.value("picker/rail/test/width")) == preferred_width
        field.setFocus()
        QTest.mouseClick(pane.toggle, Qt.LeftButton)
        _settle(app)
        assert pane.rail.isHidden()
        assert content.width() > initial_map_width
        assert pane.toggle.text() == "Show controls"
        # Focus was inside the rail that just disappeared. The toggle used to
        # catch it, but it is the View menu's Hide Controls item now and is not
        # displayed, so focus has to land on something real instead of being
        # dropped onto a hidden widget or lost entirely.
        focused = pane.focusWidget()
        assert focused is not None
        assert not pane.rail.isAncestorOf(focused)
        assert focused is content or content.isAncestorOf(focused) or focused is pane
        assert field.text() == "preserve this selection"
        pane.close()
        reread = QSettings(settings_path, QSettings.IniFormat)
        restored, restored_field, _ = _pane(reread)
        restored.show()
        _settle(app)
        assert restored.rail.isHidden()
        QTest.keyClick(restored.toggle, Qt.Key_Space)
        _settle(app)
        assert not restored.rail.isHidden()
        assert restored.rail.width() == preferred_width
        assert restored_field.text() == field.text()
    finally:
        pane.close()
        pane.deleteLater()
        if restored is not None:
            restored.close()
            restored.deleteLater()


def test_selection_pane_invalid_and_source_specific_layout_preferences(
    standard_qt_app, tmp_path
):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    settings.setValue("picker/rail/test/width", "not a width")
    settings.setValue("picker/rail/test/collapsed", "invalid")
    settings.setValue("picker/rail/other/collapsed", True)
    pane, _, _ = _pane(settings)
    other, _, _ = _pane(settings, key="other")
    try:
        pane.show()
        other.show()
        _settle(standard_qt_app)
        assert not pane.rail.isHidden()
        assert pane.rail.width() == RAIL_W["max"] + SCROLLBAR_W
        assert other.rail.isHidden()
    finally:
        pane.close()
        other.close()
        pane.deleteLater()
        other.deleteLater()


def _check_missing_lead(app, update_fetch_state):
    """The same native-control assertion can be exercised against HEAD's method."""
    from types import SimpleNamespace

    from qtpy.QtWidgets import QComboBox, QDoubleSpinBox, QLabel, QPushButton

    root = QWidget()
    try:
        cycle, lead = QComboBox(root), QComboBox(root)
        cycle.addItem("00 UTC", 0)
        latitude, longitude = QDoubleSpinBox(root), QDoubleSpinBox(root)
        latitude.setRange(-90, 90)
        longitude.setRange(-180, 180)
        latitude.setValue(35.63)
        longitude.setValue(-97.44)
        owner = SimpleNamespace(
            _model_cycle=cycle, _model_fxx_combo=lead,
            _model_lat=latitude, _model_lon=longitude,
            _model_fetch_btn=QPushButton("Load sounding", root),
            _model_point_status=QLabel(root), _model_worker=None,
            _model_config=lambda: SimpleNamespace(key="hrrr", label="HRRR", domain="CONUS"),
            _model_point_ok=lambda: True,
        )
        update_fetch_state(owner)
        app.processEvents()
        assert not owner._model_fetch_btn.isEnabled(), "missing lead silently enabled a fallback F000 load"
    finally:
        root.close()
        root.deleteLater()


def test_missing_lead_does_not_enable_a_silent_default_forecast_load(standard_qt_app):
    from sharpmod.gui_picker import PickerWindow

    _check_missing_lead(standard_qt_app, PickerWindow._model_update_fetch_state)


class _CharMetrics:
    """One unit per character, so the fitting rule is tested, not a font."""

    def horizontalAdvance(self, text):  # noqa: N802 - QFontMetrics API
        return len(text)

    def elidedText(self, text, _mode, width):  # noqa: N802 - QFontMetrics API
        width = max(0, int(width))
        return text if len(text) <= width else "\u2026" + text[-(width - 1):]


SUMMARY = (
    "HRRR · Requested point 35.6300°, -97.4400° · "
    "Initialization 2026-09-17 06:00 UTC · Lead +0 h · Valid 2026-09-17 06:00 UTC"
)


def test_a_summary_that_fits_is_returned_untouched():
    assert fit_summary_groups(SUMMARY, _CharMetrics(), len(SUMMARY)) == SUMMARY
    assert fit_summary_groups(SUMMARY, _CharMetrics(), len(SUMMARY) + 50) == SUMMARY


def test_a_shortened_summary_never_severs_a_label_from_its_value():
    """The reason this exists rather than a plain left elide.

    Eliding by character produced a bare "2026-09-17 06:00 UTC" -- the
    initialization time with its label cut off -- sitting immediately before a
    labelled valid time, so it read as a second valid time.
    """
    fitted = fit_summary_groups(SUMMARY, _CharMetrics(), 44)

    assert fitted.startswith("\u2026 ")
    body = fitted[2:]
    groups = body.split(SUMMARY_JOIN)
    original = SUMMARY.split(SUMMARY_JOIN)
    # Every group still shown is one of the originals, whole.
    assert groups
    assert all(group in original for group in groups)
    # It is a suffix of the original groups, so nothing is reordered or invented.
    assert original[-len(groups):] == groups
    assert len(fitted) <= 44


def test_only_as_many_leading_groups_as_necessary_are_dropped():
    metrics = _CharMetrics()
    original = SUMMARY.split(SUMMARY_JOIN)

    wide = fit_summary_groups(SUMMARY, metrics, len(SUMMARY) - 4)
    narrow = fit_summary_groups(SUMMARY, metrics, 30)

    wide_groups = wide.removeprefix("\u2026 ").split(SUMMARY_JOIN)
    narrow_groups = narrow.removeprefix("\u2026 ").split(SUMMARY_JOIN)
    assert len(wide_groups) > len(narrow_groups)
    assert wide_groups == original[-len(wide_groups):]


def test_a_single_group_too_wide_falls_back_to_character_eliding():
    metrics = _CharMetrics()
    fitted = fit_summary_groups("Valid 2026-09-17 06:00 UTC", metrics, 10)

    assert len(fitted) <= 10
    # The tail is what is kept, because that is where the value is.
    assert fitted.endswith("UTC")


def test_no_room_means_no_summary():
    assert fit_summary_groups(SUMMARY, _CharMetrics(), 0) == ""
    assert fit_summary_groups(SUMMARY, _CharMetrics(), -20) == ""
    assert fit_summary_groups("", _CharMetrics(), 200) == ""
    assert fit_summary_groups("   ", _CharMetrics(), 200) == ""


def test_the_pane_no_longer_draws_the_toggle_or_the_summary_itself(
    standard_qt_app, tmp_path
):
    """Both moved to the top bar: the View menu item and the clock's left side.

    The pane still owns them, because it owns the collapse memory and computes the
    summary text, but spending two full-width rows of the window on one button and
    one line of context is what this change removes.
    """
    app = standard_qt_app
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    pane, _field, _content = _pane(settings, key="moved")
    try:
        pane.show()
        _settle(app)

        assert pane.toggle.isHidden()
        assert pane.summary.isHidden()

        pane.set_feedback("KOUN · Valid 2026-09-17 06:00 UTC")
        _settle(app)
        # Still hidden: the window presents it, and has not asked otherwise.
        assert pane.summary.isHidden()
        assert pane.summary_text() == "KOUN · Valid 2026-09-17 06:00 UTC"
        assert not pane.summary_is_inline()

        # When the top bar cannot spare the width it hands the job back, and the
        # context reappears here rather than disappearing.
        pane.set_summary_inline(True)
        _settle(app)
        assert pane.summary.isVisible()
        assert pane.summary.text() == "KOUN · Valid 2026-09-17 06:00 UTC"

        pane.set_summary_inline(False)
        _settle(app)
        assert pane.summary.isHidden()
    finally:
        pane.close()
        pane.deleteLater()


def test_the_pane_reports_its_collapse_state_and_announces_changes(
    standard_qt_app, tmp_path
):
    """The View menu item is a mirror, so the pane has to be the source."""
    app = standard_qt_app
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    pane, _field, _content = _pane(settings, key="mirror")
    seen = []
    try:
        pane.show()
        _settle(app)
        pane.collapsedChanged.connect(seen.append)

        assert pane.is_collapsed() is False
        pane.set_collapsed(True)
        _settle(app)
        assert pane.is_collapsed() is True
        assert pane.rail.isHidden()
        assert seen[-1] is True

        pane.set_collapsed(False)
        _settle(app)
        assert pane.is_collapsed() is False
        assert seen[-1] is False
    finally:
        pane.close()
        pane.deleteLater()


def test_the_pane_announces_a_new_summary(standard_qt_app, tmp_path):
    app = standard_qt_app
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    pane, _field, _content = _pane(settings, key="announce")
    seen = []
    try:
        pane.summaryChanged.connect(seen.append)
        pane.show()
        _settle(app)

        pane.set_feedback("first context")
        pane.set_feedback("second context")

        assert seen == ["first context", "second context"]
        # The accessible description and tooltip travel with it.
        assert pane.summary.accessibleDescription() == "second context"
        assert pane.summary.toolTip() == "second context"
    finally:
        pane.close()
        pane.deleteLater()
