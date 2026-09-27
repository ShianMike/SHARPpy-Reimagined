"""Same-level Skew-T/hodograph readout regressions."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import numpy.ma as ma
import pytest
from qtpy.QtCore import QEvent, QPointF, Qt, Signal
from qtpy.QtWidgets import QMainWindow, QWidget

from sharpmod.gui_sounding_readout import (
    LinkedReadoutController,
    format_readout,
    install_linked_readout_hooks,
    nearest_hodograph_height,
    readout_at_pressure,
)
from sharpmod.sharptab import interp


def _profile(*, missing_dewpoint=False, missing_wind=False):
    mask = [True, True, True]
    return SimpleNamespace(
        sfc=0,
        pres=ma.array([1000.0, 900.0, 800.0]),
        logp=np.log10([1000.0, 900.0, 800.0]),
        hght=ma.array([300.0, 1300.0, 2300.0]),
        tmpc=ma.array([20.0, 10.0, 0.0]),
        dwpc=ma.array(
            [15.0, 5.0, -5.0], mask=mask if missing_dewpoint else False
        ),
        u=ma.array([10.0, 10.0, 10.0], mask=mask if missing_wind else False),
        v=ma.array([0.0, 0.0, 0.0], mask=mask if missing_wind else False),
    )


def test_one_pressure_produces_one_explicit_agl_msl_thermo_wind_sample():
    sample = readout_at_pressure(_profile(), 900.0)

    assert sample.pressure_hpa == pytest.approx(900.0)
    assert sample.height_agl_m == pytest.approx(1000.0)
    assert sample.height_msl_m == pytest.approx(1300.0)
    assert sample.temperature_c == pytest.approx(10.0)
    assert sample.dewpoint_c == pytest.approx(5.0)
    assert sample.wind_direction_deg == pytest.approx(270.0)
    assert sample.wind_speed_kt == pytest.approx(10.0)
    assert format_readout(sample, temp_units="Celsius", wind_units="knots") == (
        "900.0 hPa · 1,000 m AGL / 1,300 m MSL · "
        "T 10.0 °C · Td 5.0 °C · Wind 270°/10 kt"
    )


def test_readout_formats_existing_fahrenheit_and_metres_per_second_preferences():
    text = format_readout(
        readout_at_pressure(_profile(), 900.0),
        temp_units="Fahrenheit",
        wind_units="m/s",
    )

    assert "T 50.0 °F" in text
    assert "Td 41.0 °F" in text
    assert "Wind 270°/5.1 m/s" in text


def test_missing_and_out_of_range_fields_remain_explicitly_missing():
    missing = format_readout(
        readout_at_pressure(
            _profile(missing_dewpoint=True, missing_wind=True), 900.0
        ),
        temp_units="Celsius",
        wind_units="knots",
    )
    outside = format_readout(
        readout_at_pressure(_profile(), 700.0),
        temp_units="Celsius",
        wind_units="knots",
    )

    assert "Td not reported" in missing
    assert "Wind not reported" in missing
    assert "nan" not in missing.casefold() and "-9999" not in missing
    assert outside.startswith("700.0 hPa")
    assert "Height not reported" in outside
    assert "T not reported" in outside
    assert "Td not reported" in outside
    assert "Wind not reported" in outside


def test_readout_does_not_interpolate_across_a_reported_data_gap():
    profile = _profile()
    profile.hght = ma.array(profile.hght, mask=[False, True, False])
    profile.dwpc = ma.array(profile.dwpc, mask=[False, True, False])
    profile.u = ma.array(profile.u, mask=[False, True, False])
    profile.v = ma.array(profile.v, mask=[False, True, False])

    exact_gap = readout_at_pressure(profile, 900.0)
    adjacent_gap = readout_at_pressure(profile, 950.0)

    assert exact_gap.temperature_c == pytest.approx(10.0)
    assert exact_gap.height_msl_m is None
    assert exact_gap.height_agl_m is None
    assert exact_gap.dewpoint_c is None
    assert exact_gap.wind_direction_deg is None
    assert exact_gap.wind_speed_kt is None
    assert adjacent_gap.temperature_c is not None
    assert adjacent_gap.height_msl_m is None
    assert adjacent_gap.dewpoint_c is None
    assert adjacent_gap.wind_speed_kt is None


def test_hodograph_hover_uses_nearest_valid_segment_without_crossing_gaps():
    identity = lambda u, v: (np.asarray(u), np.asarray(v))

    height = nearest_hodograph_height(
        [0.0, 10.0], [0.0, 0.0], [300.0, 1300.0],
        x=5.0, y=2.0, to_pixels=identity, max_distance=3.0,
    )
    gap = nearest_hodograph_height(
        ma.array([0.0, 5.0, 10.0], mask=[False, True, False]),
        [0.0, 0.0, 0.0],
        [300.0, 800.0, 1300.0],
        x=5.0, y=0.0, to_pixels=identity, max_distance=10.0,
    )

    assert height == pytest.approx(800.0)
    assert gap is None


class _Sound(QWidget):
    cursor_move = Signal(float)
    cursor_toggle = Signal(bool)

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.prof = profile
        self.readout_pres = 900.0
        self.sfc_units = "Celsius"
        self.update_calls = 0

    def updateReadout(self):  # noqa: N802 - upstream API
        self.update_calls += 1
        height_msl = interp.hght(self.prof, self.readout_pres)
        self.cursor_move.emit(float(interp.to_agl(self.prof, height_msl)))


class _Hodo(QWidget):
    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.prof = profile
        self.hght = profile.hght
        self.u = profile.u
        self.v = profile.v
        self.wind_units = "knots"
        self.readout_visible = False
        self.readout_hght = -999.0

    def uv_to_pix(self, u, v):
        return np.asarray(u), np.asarray(v)

    def cursorToggle(self, enabled):  # noqa: N802 - upstream API
        self.readout_visible = enabled

    def cursorMove(self, height):  # noqa: N802 - upstream API
        self.readout_hght = height


def _window(qt_app):
    win = QMainWindow()
    profile = _profile()
    sound = _Sound(profile, win)
    hodo = _Hodo(profile, win)
    sound.cursor_toggle.connect(hodo.cursorToggle)
    sound.cursor_move.connect(hodo.cursorMove)
    win.spc_widget = SimpleNamespace(sound=sound, hodo=hodo)
    return win, sound, hodo


def test_controller_drives_both_surfaces_from_the_same_pressure(qt_app):
    win, sound, hodo = _window(qt_app)
    controller = LinkedReadoutController(win)

    sound.cursor_toggle.emit(True)
    sound.cursor_move.emit(9999.0)  # ignored: pressure is authoritative

    assert controller.sample.pressure_hpa == pytest.approx(900.0)
    assert hodo.readout_hght == pytest.approx(controller.sample.height_agl_m)
    assert "900.0 hPa" in controller.label.toolTip()
    assert "1,000 m AGL / 1,300 m MSL" in controller.label.toolTip()
    assert "T 10.0 °C" in controller.label.toolTip()
    assert "Wind 270°/10 kt" in controller.label.toolTip()
    controller.label.resize(300, 120)
    qt_app.processEvents()
    assert "\n" in controller.label.text()
    for field in ("900.0 hPa", "AGL", "MSL", "T 10.0 °C", "Td 5.0 °C", "Wind"):
        assert field in controller.label.text()

    sound.sfc_units = "Fahrenheit"
    hodo.wind_units = "m/s"
    controller.refresh()
    assert "T 50.0 °F" in controller.label.toolTip()
    assert "Wind 270°/5.1 m/s" in controller.label.toolTip()

    sound.cursor_toggle.emit(False)
    assert "paused" in controller.label.toolTip().casefold()
    win.close()


def test_hodograph_hover_updates_the_skewt_pressure_and_shared_sample(qt_app):
    win, sound, hodo = _window(qt_app)
    hodo.u = ma.array([0.0, 10.0, 20.0])
    win._sharpmod_interaction_mode = SimpleNamespace(mode="inspect")
    controller = LinkedReadoutController(win)
    sound.cursor_toggle.emit(True)
    event = SimpleNamespace(
        type=lambda: QEvent.MouseMove,
        position=lambda: QPointF(10.0, 0.0),
        buttons=lambda: Qt.NoButton,
    )

    assert controller.eventFilter(hodo, event) is False

    expected = interp.pres(sound.prof, 1300.0)
    assert sound.update_calls == 1
    assert sound.readout_pres == pytest.approx(expected)
    assert controller.sample.height_agl_m == pytest.approx(1000.0)
    assert hodo.readout_hght == pytest.approx(1000.0)
    win.close()


def test_profile_update_hook_refreshes_a_stationary_linked_readout():
    calls = []

    class _Widget:
        def __init__(self):
            self._sharpmod_linked_readout = SimpleNamespace(
                refresh=lambda: calls.append("readout")
            )

        def updateProfs(self):  # noqa: N802 - upstream API
            calls.append("profiles")
            return "updated"

    install_linked_readout_hooks(_Widget)

    assert _Widget().updateProfs() == "updated"
    assert calls == ["profiles", "readout"]
