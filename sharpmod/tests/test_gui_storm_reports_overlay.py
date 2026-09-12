"""Storm reports as a picker-map overlay, read against the outlook."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from qtpy.QtWidgets import QCheckBox

from sharpmod import gui_maps, storm_reports
from sharpmod.gui_overlay_controls import StormReportsOverlayController

UTC = timezone.utc
WHEN = datetime(2026, 5, 1, 18, tzinfo=UTC)


class _Outlook:
    """The one thing the reports controller reads from the outlook."""

    def __init__(self, enabled=False):
        self._enabled = enabled

    def is_enabled(self):
        return self._enabled

    def set_enabled(self, value):
        self._enabled = bool(value)


@pytest.fixture
def widget(qt_app):
    w = gui_maps.StationMapWidget([])
    w.resize(640, 480)
    w._lon0, w._lon1 = -125.0, -66.0
    w._lat0, w._lat1 = 24.0, 50.0
    try:
        yield w
    finally:
        w.close()


@pytest.fixture
def controller(widget):
    control = StormReportsOverlayController(widget)
    try:
        yield control
    finally:
        control.shutdown()


def _box(controller):
    return controller.controls_widget().findChildren(QCheckBox)[0]


def test_it_is_off_and_fetches_nothing_by_default(controller):
    assert not controller.is_enabled()
    assert controller.attached_layer() is None


def test_the_switch_waits_for_the_outlook(controller):
    outlook = _Outlook(enabled=False)
    controller.bind_outlook(outlook)

    box = _box(controller)
    assert not box.isEnabled()
    assert "SPC convective outlook" in box.toolTip(), \
        "the reason has to be given, not just the control greyed out"


def test_the_switch_becomes_available_with_the_outlook(controller):
    outlook = _Outlook(enabled=True)
    controller.bind_outlook(outlook)

    assert _box(controller).isEnabled()
    assert _box(controller).toolTip() == ""


def test_losing_the_outlook_switches_the_reports_off(controller):
    """Reports with no risk areas behind them cannot answer their question."""
    outlook = _Outlook(enabled=True)
    controller.bind_outlook(outlook)
    controller.set_enabled(True)
    assert controller.is_enabled()

    outlook.set_enabled(False)
    controller._sync_available()

    assert not controller.is_enabled(), "it must not draw on regardless"
    assert not _box(controller).isEnabled()


def test_an_unbound_controller_is_not_blocked(controller):
    """Nothing to depend on means nothing to wait for."""
    assert _box(controller).isEnabled()


# --------------------------------------------------------------------------- #
# drawing
# --------------------------------------------------------------------------- #
def _layer():
    reports = storm_reports.parse_reports(
        "VALID,VALID2,LAT,LON,MAG,WFO,TYPECODE,TYPETEXT,CITY,COUNTY,STATE,"
        "SOURCE,REMARK,UGC,UGCNAME,QUALIFIER\n"
        "202605011800,x,35.22,-97.44,1.75,OUN,H,HAIL,Norman,Cleveland,OK,"
        "Public,Golf ball hail.,OKC027,Cleveland,M\n"
    )
    return storm_reports.layer_from_reports(reports, span_deg=59.0)


def test_a_loaded_layer_reaches_the_map(controller, widget):
    controller.set_enabled(True)
    controller._on_loaded(controller._token, WHEN, _layer())

    assert widget.overlay(storm_reports.OVERLAY_KEY) is not None
    assert controller.attached_layer() is not None


def test_a_stale_result_is_discarded(controller, widget):
    """The user moved on; a late answer must not overwrite the current view."""
    controller.set_enabled(True)
    controller._on_loaded(controller._token - 1, WHEN, _layer())

    assert widget.overlay(storm_reports.OVERLAY_KEY) is None


def test_a_quiet_window_says_so_without_drawing(controller, widget):
    controller.set_enabled(True)
    controller._on_loaded(controller._token, WHEN, None)

    assert widget.overlay(storm_reports.OVERLAY_KEY) is None
    assert "No storm reports" in controller._status.text()


def test_switching_off_removes_the_reports(controller, widget):
    controller.set_enabled(True)
    controller._on_loaded(controller._token, WHEN, _layer())

    controller.set_enabled(False)

    assert widget.overlay(storm_reports.OVERLAY_KEY) is None
    assert controller.attached_layer() is None


def test_a_failure_is_reported_rather_than_left_blank(controller, widget):
    controller.set_enabled(True)
    controller._on_failed(controller._token, WHEN, "connection reset")

    assert widget.overlay(storm_reports.OVERLAY_KEY) is None
    assert "unavailable" in controller._status.text()


def test_the_status_names_how_many_were_found(controller):
    controller.set_enabled(True)
    controller._on_loaded(controller._token, WHEN, _layer())

    assert "1 storm report" in controller._status.text()


# --------------------------------------------------------------------------- #
# the request
# --------------------------------------------------------------------------- #
def test_the_map_extent_narrows_the_request(controller, widget, monkeypatch):
    """The feed is national and reports are points; send only what is visible."""
    seen: list[dict] = []

    class _Worker:
        def __init__(self, valid, token, *, parent=None, view=None,
                     window=None, span_deg=None):
            seen.append({"view": view, "span": span_deg, "valid": valid})
            self.loaded = SimpleNamespace(connect=lambda slot: None)
            self.failed = SimpleNamespace(connect=lambda slot: None)
            self.finished = SimpleNamespace(connect=lambda slot: None)

        def start(self):
            pass

        def isRunning(self):  # noqa: N802 - Qt's spelling
            return False

        def deleteLater(self):  # noqa: N802 - Qt's spelling
            pass

    monkeypatch.setattr("sharpmod.gui_overlay_controls._StormReportsWorker",
                        _Worker)
    controller.set_valid_time(WHEN)
    controller.set_enabled(True)
    controller._start_fetch()

    assert seen[-1]["view"] == widget.view_bounds()
    assert seen[-1]["span"] == pytest.approx(59.0)
    assert seen[-1]["valid"] == WHEN
