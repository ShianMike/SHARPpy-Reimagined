"""The control that chooses what a sounding's locator inset carries.

The rules live in :mod:`sharpmod.locator_overlay`; what is checked here is that
the boxes agree with those rules rather than quietly claiming to show something
that will not be drawn.
"""

from __future__ import annotations

import pytest

from sharpmod import locator_overlay as lo
from sharpmod.gui_overlay_controls import (
    LOCATOR_SETTINGS_KEY, LocatorOverlaySelector,
)


class _Settings:
    """The two operations the selector needs from QSettings."""

    def __init__(self, initial=None):
        self._values = dict(initial or {})
        self.writes: list[tuple[str, str]] = []

    def value(self, key, default=None):
        return self._values.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's spelling
        self._values[key] = value
        self.writes.append((key, value))


class _BrokenSettings:
    def value(self, key, default=None):
        raise OSError("unreadable settings file")

    def setValue(self, key, value):  # noqa: N802 - Qt's spelling
        raise OSError("read-only settings file")


@pytest.fixture
def selector(qt_app):
    control = LocatorOverlaySelector()
    try:
        yield control
    finally:
        control.controls_widget().close()


def _box(control, family):
    return control._boxes[family]


def test_nothing_is_selected_by_default(selector):
    """A launch must reach for no network until asked."""
    assert selector.selection() == ()
    assert selector.spec() == "none"


def test_every_family_is_offered_and_labelled(selector):
    for family in lo.FAMILIES:
        box = _box(selector, family)
        assert box.text(), f"{family} needs a label"
        assert box.property("locator_family") == family


def test_risk_and_a_field_can_both_be_checked(selector):
    _box(selector, lo.FAMILY_RISK).setChecked(True)
    _box(selector, lo.FAMILY_HRRR).setChecked(True)

    assert {item.family for item in selector.selection()} == {
        lo.FAMILY_RISK, lo.FAMILY_HRRR}


def test_choosing_radar_clears_what_it_displaces(selector):
    """The boxes have to move, or the control claims what is not drawn."""
    _box(selector, lo.FAMILY_RISK).setChecked(True)
    _box(selector, lo.FAMILY_HRRR).setChecked(True)

    _box(selector, lo.FAMILY_RADAR_SITE).setChecked(True)

    assert selector.selection() == (lo.Selection(lo.FAMILY_RADAR_SITE),)
    assert not _box(selector, lo.FAMILY_RISK).isChecked()
    assert not _box(selector, lo.FAMILY_HRRR).isChecked()


def test_one_radar_scope_replaces_the_other(selector):
    _box(selector, lo.FAMILY_RADAR_MOSAIC).setChecked(True)

    _box(selector, lo.FAMILY_RADAR_SITE).setChecked(True)

    assert len(selector.selection()) == 1


def test_reports_are_unavailable_until_the_outlook_is_chosen(selector):
    reports = _box(selector, lo.FAMILY_REPORTS)

    assert not reports.isEnabled()
    assert "Select" in reports.toolTip(), "the reason has to be given"

    _box(selector, lo.FAMILY_RISK).setChecked(True)

    assert reports.isEnabled()
    assert reports.toolTip() == ""


def test_dropping_the_outlook_takes_the_reports_with_it(selector):
    _box(selector, lo.FAMILY_RISK).setChecked(True)
    _box(selector, lo.FAMILY_REPORTS).setChecked(True)
    assert len(selector.selection()) == 2

    _box(selector, lo.FAMILY_RISK).setChecked(False)

    assert selector.selection() == ()
    assert not _box(selector, lo.FAMILY_REPORTS).isChecked()
    assert not _box(selector, lo.FAMILY_REPORTS).isEnabled()


def test_radar_also_strands_nothing(selector):
    """Radar displaces the outlook, so the reports cannot survive it either."""
    _box(selector, lo.FAMILY_RISK).setChecked(True)
    _box(selector, lo.FAMILY_REPORTS).setChecked(True)

    _box(selector, lo.FAMILY_RADAR_MOSAIC).setChecked(True)

    assert selector.selection() == (lo.Selection(lo.FAMILY_RADAR_MOSAIC),)
    assert not _box(selector, lo.FAMILY_REPORTS).isChecked()


def test_a_change_is_announced_once_settled(selector):
    seen: list[int] = []
    selector.selectionChanged.connect(lambda: seen.append(1))

    _box(selector, lo.FAMILY_RISK).setChecked(True)

    assert seen, "the sounding window has to learn the selection changed"


def test_the_specification_round_trips_through_the_command_line_form(selector):
    _box(selector, lo.FAMILY_RISK).setChecked(True)
    _box(selector, lo.FAMILY_REPORTS).setChecked(True)

    spec = selector.spec()

    assert lo.parse(spec) == selector.selection()


def test_setting_a_specification_moves_the_boxes(selector):
    selector.set_spec("risk,hrrr")

    assert _box(selector, lo.FAMILY_RISK).isChecked()
    assert _box(selector, lo.FAMILY_HRRR).isChecked()
    assert not _box(selector, lo.FAMILY_RADAR_SITE).isChecked()


def test_a_specification_naming_only_reports_selects_nothing(selector):
    """The rules drop it, so the control must not show it as chosen."""
    selector.set_spec("reports")

    assert selector.selection() == ()
    assert not _box(selector, lo.FAMILY_REPORTS).isChecked()


@pytest.mark.parametrize("text", ["", "none", "satellite", "risk,satellite",
                                 None, "   "])
def test_an_unusable_specification_is_ignored_rather_than_fatal(selector, text):
    selector.set_spec(text)

    assert isinstance(selector.selection(), tuple)


# --------------------------------------------------------------------------- #
# persistence
# --------------------------------------------------------------------------- #
def test_the_remembered_choice_is_restored(qt_app):
    settings = _Settings({LOCATOR_SETTINGS_KEY: "risk,hrrr"})

    control = LocatorOverlaySelector(settings=settings)
    try:
        assert {item.family for item in control.selection()} == {
            lo.FAMILY_RISK, lo.FAMILY_HRRR}
    finally:
        control.controls_widget().close()


def test_only_the_choice_is_written(qt_app):
    """Matching the field and radar policy: never whether it was switched on."""
    settings = _Settings()
    control = LocatorOverlaySelector(settings=settings)
    try:
        control.set_spec("risk,reports")
        control.remember()
    finally:
        control.controls_widget().close()

    assert settings.writes == [(LOCATOR_SETTINGS_KEY, "risk,reports")]


def test_a_stored_value_from_another_version_degrades_quietly(qt_app):
    settings = _Settings({LOCATOR_SETTINGS_KEY: "risk,lightning,radar-orbital"})

    control = LocatorOverlaySelector(settings=settings)
    try:
        assert control.selection() == ()
    finally:
        control.controls_widget().close()


def test_unreadable_settings_neither_restore_nor_raise(qt_app):
    control = LocatorOverlaySelector(settings=_BrokenSettings())
    try:
        assert control.selection() == ()
        control.remember()  # must not raise either
    finally:
        control.controls_widget().close()


# --------------------------------------------------------------------------- #
# mounted in the picker
# --------------------------------------------------------------------------- #
@pytest.fixture
def picker(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_SETTINGS_PATH", str(tmp_path / "settings.ini"))
    from sharpmod import gui_picker

    window = gui_picker.PickerWindow()
    try:
        yield window
    finally:
        window.close()
        window.deleteLater()
        qt_app.processEvents()


def _front(picker, qt_app, title):
    for index in range(picker._tabs.count()):
        if picker._tabs.tabText(index) == title:
            picker._tabs.setCurrentIndex(index)
            qt_app.processEvents()
            return
    pytest.skip(f"no {title!r} tab in this build")


def test_both_map_tabs_offer_the_selector(picker, qt_app):
    """It was built and tested before it was mounted; this is the guard."""
    _front(picker, qt_app, "Forecast Model")

    for attribute in ("_map_locator", "_model_locator"):
        selector = getattr(picker, attribute, None)
        assert selector is not None, f"{attribute} was never built"
        assert selector.controls_widget().parent() is not None, \
            f"{attribute} was built but not added to a layout"


def test_the_tab_in_front_answers_for_the_sounding(picker, qt_app):
    """Ranking the tabs in a fixed order answers with the wrong map's choice."""
    _front(picker, qt_app, "Forecast Model")
    picker._model_locator.set_spec("risk")
    picker._map_locator.set_spec("radar-mosaic")

    _front(picker, qt_app, "Station Map")
    assert picker.selected_locator_spec() == "radar-mosaic"

    _front(picker, qt_app, "Forecast Model")
    assert picker.selected_locator_spec() == "risk"


def test_nothing_selected_reports_a_bare_inset(picker, qt_app):
    _front(picker, qt_app, "Station Map")

    assert picker.selected_locator_spec() == "none"


def test_the_choice_is_written_when_the_window_closes(picker, qt_app):
    _front(picker, qt_app, "Station Map")
    picker._map_locator.set_spec("risk,hrrr")

    picker._remember_locator_choice()

    assert picker._settings.value(LOCATOR_SETTINGS_KEY, "") == "risk,hrrr"


# --------------------------------------------------------------------------- #
# which outlook the inset draws
#
# The control used to offer families only, so the inset could never be pointed at
# a hazard: whatever it produced named "risk" and nothing more. There was no way
# to ask for the tornado, wind, or hail probability on the sounding's own locator,
# only on the picker map.
# --------------------------------------------------------------------------- #
def test_the_hazard_defaults_to_following_the_map(selector):
    """Naming nothing keeps the inset as context for the map it came from."""
    _box(selector, lo.FAMILY_RISK).setChecked(True)

    assert selector.hazard() is None
    assert selector.spec() == "risk", (
        "a bare family is what lets the viewer substitute the map's hazard"
    )


@pytest.mark.parametrize("hazard", ["torn", "wind", "hail", "prob", "cat"])
def test_a_named_hazard_reaches_the_spec(selector, hazard):
    _box(selector, lo.FAMILY_RISK).setChecked(True)
    selector._hazard.setCurrentIndex(selector._hazard.findData(hazard))

    assert selector.hazard() == hazard
    assert selector.spec() == f"risk:{hazard}"
    assert selector.selection()[0].product == hazard


def test_the_hazard_survives_a_spec_round_trip(selector):
    selector.set_spec("risk:wind,hrrr")

    assert selector.hazard() == "wind"
    assert selector.spec() == "risk:wind,hrrr"


def test_an_unknown_hazard_falls_back_to_following_the_map(selector):
    """A hand-edited INI must not pin the inset to something undrawable."""
    selector.set_spec("risk:nonsense")

    assert selector.hazard() is None
    assert selector.spec() == "risk"


def test_the_hazard_choice_is_hidden_until_the_outlook_is_selected(selector):
    """It cannot affect anything while no outlook is being drawn."""
    widget = selector.controls_widget()
    widget.show()
    try:
        assert not selector._hazard.isVisible()

        _box(selector, lo.FAMILY_RISK).setChecked(True)
        assert selector._hazard.isVisible()

        _box(selector, lo.FAMILY_RISK).setChecked(False)
        assert not selector._hazard.isVisible()
    finally:
        widget.hide()


def test_choosing_a_hazard_announces_the_change(selector):
    _box(selector, lo.FAMILY_RISK).setChecked(True)
    seen = []
    selector.selectionChanged.connect(lambda: seen.append(selector.spec()))

    selector._hazard.setCurrentIndex(selector._hazard.findData("hail"))

    assert seen == ["risk:hail"]


def test_the_hazard_is_remembered_with_the_choice():
    settings = _Settings()
    control = LocatorOverlaySelector(settings=settings)
    try:
        _box(control, lo.FAMILY_RISK).setChecked(True)
        control._hazard.setCurrentIndex(control._hazard.findData("torn"))
        control.remember()

        assert settings.value(LOCATOR_SETTINGS_KEY) == "risk:torn"

        restored = LocatorOverlaySelector(settings=settings)
        try:
            assert restored.hazard() == "torn"
        finally:
            restored.controls_widget().close()
    finally:
        control.controls_widget().close()


def test_radar_displacing_the_outlook_also_drops_the_hazard(selector):
    """The spec must never claim a hazard for an outlook that was displaced."""
    selector.set_spec("risk:torn")
    _box(selector, lo.FAMILY_RADAR_SITE).setChecked(True)

    assert selector.spec() == "radar-site"
