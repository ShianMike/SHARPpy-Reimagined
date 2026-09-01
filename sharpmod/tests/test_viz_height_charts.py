"""Height-profile charts and the swappable slot that hosts them."""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sharpmod.viz.height_charts import (
    HeightChartInset,
    HeightSeries,
    SwappableHeightChart,
    _as_kelvin,
    _nice_step,
    plotStepwiseCape,
    plotStormRelativeWind,
    plotThetaProfile,
    storm_relative_wind_profile,
    theta_profile,
)
from sharpmod.viz.streamwiseness import plotStreamwiseness


def _wind_profile():
    """A veering, strengthening profile with a known storm motion."""
    height = np.arange(0.0, 12000.0 + 500.0, 500.0)
    return SimpleNamespace(
        hght=height + 300.0,
        u=np.linspace(5.0, 45.0, height.size),
        v=12.0 * np.sin(height / 3000.0),
        sfc=0,
        srwind=(10.0, -8.0, -10.0, 8.0),
    )


def _thermo_profile():
    pres = np.array([1000.0, 925.0, 850.0, 700.0, 500.0, 300.0, 200.0])
    hght = np.array([100.0, 800.0, 1500.0, 3100.0, 5900.0, 9600.0, 12000.0])
    tmpc = np.array([30.0, 24.0, 18.0, 6.0, -17.0, -45.0, -58.0])
    dwpc = np.array([23.0, 20.0, 14.0, -4.0, -35.0, -60.0, -72.0])
    return SimpleNamespace(
        pres=pres, hght=hght, tmpc=tmpc, dwpc=dwpc, sfc=0)


# --- scaling helpers ------------------------------------------------------- #

@pytest.mark.parametrize("span,expected_max", [
    (1.0, 1.0), (9.0, 2.5), (100.0, 25.0), (1000.0, 250.0),
])
def test_nice_step_stays_within_an_order_of_the_span(span, expected_max):
    step = _nice_step(span)

    assert 0 < step <= expected_max
    assert span / step <= 12


@pytest.mark.parametrize("span", [0.0, -5.0, float("nan")])
def test_nice_step_refuses_to_return_zero(span):
    """A zero step would divide by zero when placing ticks."""
    assert _nice_step(span) > 0


def test_as_kelvin_promotes_a_celsius_scaled_series():
    celsius = np.array([30.0, 45.0, 60.0])

    promoted = _as_kelvin(celsius)

    assert np.allclose(promoted, celsius + 273.15)


def test_as_kelvin_leaves_a_kelvin_series_alone():
    kelvin = np.array([303.0, 318.0, 333.0])

    assert np.allclose(_as_kelvin(kelvin), kelvin)


# --- storm-relative wind --------------------------------------------------- #

def test_storm_relative_wind_is_positive_and_agl_indexed():
    result = storm_relative_wind_profile(_wind_profile())

    assert result is not None
    assert result.height_km[0] == pytest.approx(0.0)
    assert result.height_km[-1] == pytest.approx(12.0)
    speeds = result.series("srw")
    finite = speeds[np.isfinite(speeds)]
    assert finite.size
    assert np.all(finite >= 0.0)


def test_storm_relative_wind_needs_a_storm_motion():
    """Without a storm motion there is no frame to be relative to."""
    prof = SimpleNamespace(
        hght=np.array([0.0, 1000.0]), u=np.array([10.0, 20.0]),
        v=np.array([0.0, 5.0]), sfc=0)

    assert storm_relative_wind_profile(prof) is None


def test_storm_relative_wind_follows_the_deviant_vector():
    """Left and right movers give different speeds, so the toggle must land."""
    prof = _wind_profile()
    right = storm_relative_wind_profile(prof, use_left=False)
    left = storm_relative_wind_profile(prof, use_left=True)

    assert right is not None and left is not None
    assert not np.allclose(right.series("srw"), left.series("srw"))


# --- theta / theta-e ------------------------------------------------------- #

def test_theta_profile_publishes_both_series_in_kelvin():
    result = theta_profile(_thermo_profile())

    assert result is not None
    assert set(result.values) == {"theta", "thetae"}
    for key in ("theta", "thetae"):
        finite = result.series(key)[np.isfinite(result.series(key))]
        assert finite.size
        assert np.all(finite > 200.0), f"{key} is not in kelvin"


def test_theta_e_is_never_below_theta():
    """Latent heat can only add to the potential temperature."""
    result = theta_profile(_thermo_profile())

    assert result is not None
    theta = result.series("theta")
    thetae = result.series("thetae")
    both = np.isfinite(theta) & np.isfinite(thetae)
    assert np.any(both)
    assert np.all(thetae[both] >= theta[both] - 0.5)


def test_theta_profile_returns_none_without_temperature():
    prof = SimpleNamespace(
        hght=np.array([0.0, 1000.0]), pres=np.array([1000.0, 900.0]), sfc=0)

    assert theta_profile(prof) is None


# --- the widgets ----------------------------------------------------------- #

@pytest.mark.parametrize("factory,profile_factory", [
    (plotStormRelativeWind, _wind_profile),
    (plotThetaProfile, _thermo_profile),
])
def test_chart_accepts_the_sharppy_widget_contract(
        qt_app, factory, profile_factory):
    widget = factory()
    widget.resize(220, 420)
    widget.setProf(profile_factory())
    widget.setPreferences(
        update_gui=True, bg_color="#000000", fg_color="#ffffff")
    widget.setDeviant("left")
    widget.plotData()
    qt_app.processEvents()

    assert widget.data is not None
    assert widget.grab().toImage().isNull() is False


@pytest.mark.parametrize("factory", [
    plotStormRelativeWind, plotThetaProfile, plotStepwiseCape])
def test_chart_draws_its_own_axis_labels(qt_app, factory):
    """Every chart must name its quantity and its height axis."""
    widget = factory()
    widget.resize(220, 420)
    labels = []
    original = widget._draw_text

    def capture(qp, rect, text, *args, **kwargs):
        labels.append(str(text))
        return original(qp, rect, text, *args, **kwargs)

    widget._draw_text = capture
    widget._redraw()
    qt_app.processEvents()

    assert widget.TITLE in labels
    assert widget.X_LABEL in labels
    assert "Height AGL (km)" in labels


@pytest.mark.parametrize("factory", [
    plotStormRelativeWind, plotThetaProfile, plotStepwiseCape])
def test_chart_without_data_shows_the_missing_indicator(qt_app, factory):
    widget = factory()
    widget.resize(220, 420)
    labels = []
    original = widget._draw_text

    def capture(qp, rect, text, *args, **kwargs):
        labels.append(str(text))
        return original(qp, rect, text, *args, **kwargs)

    widget._draw_text = capture
    widget.setProf(None)
    widget._redraw()
    qt_app.processEvents()

    assert widget.data is None
    assert "--" in labels


def test_a_chart_that_raises_degrades_to_no_data(qt_app):
    """An inset must never take the sounding window down with it."""
    class _Exploding(HeightChartInset):
        TITLE = "Exploding"
        X_LABEL = "x"

        def _compute(self, prof):
            raise RuntimeError("boom")

    widget = _Exploding()
    widget.resize(200, 300)
    widget.setProf(_wind_profile())
    qt_app.processEvents()

    assert widget.data is None
    assert widget.grab().toImage().isNull() is False


def test_autoscaled_bounds_contain_every_finite_sample(qt_app):
    widget = plotStormRelativeWind()
    widget.resize(220, 420)
    widget.setProf(_wind_profile())

    low, high = widget._bounds
    values = widget.data.series("srw")
    finite = values[np.isfinite(values)]
    assert low <= finite.min()
    assert high >= finite.max()


def test_series_span_ignores_non_finite_samples():
    series = HeightSeries(
        height_km=np.array([0.0, 1.0, 2.0]),
        values={"a": np.array([np.nan, 5.0, 10.0])},
    )

    assert series.finite_span == (5.0, 10.0)


def test_series_span_is_none_when_nothing_is_finite():
    series = HeightSeries(
        height_km=np.array([0.0, 1.0]),
        values={"a": np.array([np.nan, np.nan])},
    )

    assert series.finite_span is None


# --- the swappable slot ---------------------------------------------------- #

def test_slot_offers_every_chart_and_defaults_to_streamwiseness(qt_app):
    """The default must not change for anyone who never opens the menu."""
    slot = SwappableHeightChart()

    keys = [key for key, _label in slot.availableCharts()]
    assert keys == ["streamwiseness", "srw", "theta", "cape"]
    assert slot.currentChart() == "streamwiseness"
    assert isinstance(slot.chart, plotStreamwiseness)


def test_slot_switches_charts_and_reports_the_change(qt_app):
    slot = SwappableHeightChart()
    slot.resize(220, 420)
    slot.setProf(_wind_profile())
    seen = []
    slot.chartChanged.connect(seen.append)

    assert slot.setChart("srw")
    qt_app.processEvents()

    assert slot.currentChart() == "srw"
    assert isinstance(slot.chart, plotStormRelativeWind)
    assert seen == ["srw"]


def test_slot_ignores_an_unknown_chart(qt_app):
    slot = SwappableHeightChart()

    assert slot.setChart("does-not-exist") is False
    assert slot.currentChart() == "streamwiseness"


def test_slot_reselecting_the_same_chart_reports_nothing(qt_app):
    slot = SwappableHeightChart()
    seen = []
    slot.chartChanged.connect(seen.append)

    slot.setChart("streamwiseness")

    assert seen == []


def test_slot_populates_a_chart_only_once_it_is_shown(qt_app):
    """The stepwise CAPE chart lifts a parcel per level, so it must be lazy."""
    slot = SwappableHeightChart()
    slot.resize(220, 420)
    computed = []

    for key, (_label, widget) in slot._charts.items():
        original = widget._compute if hasattr(widget, "_compute") else None
        if original is None:
            continue

        def spy(prof, _key=key, _original=original):
            computed.append(_key)
            return _original(prof)

        widget._compute = spy

    slot.setProf(_wind_profile())
    qt_app.processEvents()
    assert "cape" not in computed, "an unshown chart must not compute"

    slot.setChart("cape")
    qt_app.processEvents()
    assert "cape" in computed


def test_slot_forwards_the_inset_contract(qt_app):
    slot = SwappableHeightChart()
    slot.resize(220, 420)
    slot.setProf(_wind_profile())
    slot.setPreferences(
        update_gui=True, bg_color="#000000", fg_color="#ffffff")

    slot.setDeviant("left")
    assert slot.use_left is True
    slot.setDeviant("right")
    assert slot.use_left is False

    # data reads through to the visible chart, so callers that held the chart
    # directly keep working.
    assert slot.data is slot.chart.data
    slot.clearData()
    slot.plotData()
    qt_app.processEvents()
    assert slot.grab().toImage().isNull() is False


def test_slot_renders_without_a_profile(qt_app):
    slot = SwappableHeightChart()
    slot.resize(220, 420)
    qt_app.processEvents()

    assert slot.data is None
    assert slot.grab().toImage().isNull() is False


def test_slot_honors_a_requested_starting_chart(qt_app):
    slot = SwappableHeightChart(chart="theta")

    assert slot.currentChart() == "theta"
    assert isinstance(slot.chart, plotThetaProfile)
