"""Several parameters become separated tracks, not several lines on one axis.

The trend chart plotted exactly one parameter. Reading how CAPE and shear evolve
together meant flipping the selector back and forth and remembering the previous
shape, which is not a comparison. Putting them both on one axis is worse than
that: CAPE in J/kg and shear in knots share no scale, so the smaller quantity
becomes a flat line along the bottom and its variation is invisible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtpy.QtCore import QSettings

from sharpmod import gui_trend_tracks as tracks
from sharpmod.gui_analysis_workspace import _TrendChart, _TrendSeries
from sharpmod.profile_metrics import MetricSample

RUN = datetime(2026, 9, 17, 6, tzinfo=timezone.utc)


def _samples(count=3, *, cape=1000.0, shear=30.0, missing=()):
    rows = []
    for index in range(count):
        available = index not in set(missing)
        rows.append(
            MetricSample(
                RUN + timedelta(hours=index),
                "control",
                {
                    "mlcape": cape + index * 100.0 if available else None,
                    "shear_6km": shear + index if available else None,
                },
                available,
                None if available else "missing hour",
            )
        )
    return tuple(rows)


def _series(count=1, **kwargs):
    return tuple(
        _TrendSeries(index, f"Sounding {index}", f"S{index}", "#3399ff", _samples(**kwargs))
        for index in range(count)
    )


# --------------------------------------------------------------------------- #
# The selection model
# --------------------------------------------------------------------------- #


def test_the_default_is_one_track_so_nothing_changes_until_asked():
    assert len(tracks.default_tracks()) == 1
    assert tracks.normalize_keys(()) == tracks.default_tracks()
    assert tracks.normalize_keys(None) == tracks.default_tracks()


def test_unknown_keys_are_dropped_and_duplicates_collapse():
    chosen = tracks.normalize_keys(
        ("mlcape", "not_a_metric", "mlcape", "shear_6km")
    )

    assert chosen == ("mlcape", "shear_6km")


def test_the_track_count_is_bounded_because_a_short_band_cannot_be_read():
    many = tracks.normalize_keys(
        ("mlcape", "shear_6km", "srh_1km", "stp_cin", "mucape", "mlcin")
    )

    assert len(many) == tracks.MAX_TRACKS
    assert tracks.MAX_TRACKS < 12, "a stack is bounded far lower than a table"


def test_the_preference_round_trips_and_survives_unusable_documents(tmp_path):
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)

    assert tracks.write_preference(settings, ("mlcape", "shear_6km")) is True
    assert tracks.read_preference(settings) == ("mlcape", "shear_6km")

    for payload in ("", "not json", "[]", '{"version": 99, "tracks": ["mlcape"]}',
                    '{"version": 1}', '{"version": 1, "tracks": "mlcape"}'):
        settings.setValue(tracks.SETTINGS_KEY, payload)
        settings.sync()
        assert tracks.read_preference(settings) == tracks.default_tracks()

    assert tracks.read_preference(None) == tracks.default_tracks()
    assert tracks.write_preference(None, ("mlcape",)) is False


# --------------------------------------------------------------------------- #
# The chart
# --------------------------------------------------------------------------- #


def test_one_metric_flattens_exactly_as_it_did_before_tracks_existed(qt_app):
    """The pinned single-metric shape: three series of three samples is nine."""
    chart = _TrendChart()
    try:
        chart.set_series(_series(3), "mlcape")

        assert len(chart._prepared) == 9
        assert [point.series for point in chart._prepared] == [0, 0, 0, 1, 1, 1, 2, 2, 2]
        assert {point.track for point in chart._prepared} == {0}
        assert chart.tracks() == ("mlcape",)
    finally:
        chart.deleteLater()


def test_two_metrics_become_two_tracks_with_their_own_scales(qt_app):
    chart = _TrendChart()
    try:
        chart.resize(600, 420)
        chart.set_tracks(_series(1), ("mlcape", "shear_6km"))

        assert chart.tracks() == ("mlcape", "shear_6km")
        # Track-then-series-then-sample: three samples per track.
        assert len(chart._prepared) == 6
        assert [point.track for point in chart._prepared] == [0, 0, 0, 1, 1, 1]

        cape_bounds = chart._bounds_of(0)
        shear_bounds = chart._bounds_of(1)
        # Each track is scaled to its own quantity, not to a shared maximum. A
        # shared axis would put the 30 kt shear at the very bottom of a 1200 J/kg
        # scale and flatten it.
        assert cape_bounds[1] > 1000.0
        assert shear_bounds[1] < 100.0
        assert cape_bounds != shear_bounds
    finally:
        chart.deleteLater()


def test_the_tracks_are_stacked_and_do_not_overlap(qt_app):
    chart = _TrendChart()
    try:
        chart.resize(600, 460)
        chart.set_tracks(_series(1), ("mlcape", "shear_6km", "srh_1km"))

        bands = [chart._track_rect(index) for index in range(3)]

        for upper, lower in zip(bands, bands[1:]):
            assert upper.bottom() < lower.top(), "tracks must not overlap"
        # And they stay inside the plot area.
        area = chart._plot_area()
        assert bands[0].top() >= area.top() - 0.5
        assert bands[-1].bottom() <= area.bottom() + 0.5
    finally:
        chart.deleteLater()


def test_one_track_keeps_the_whole_plot_area(qt_app):
    """So the single-metric chart's geometry is unchanged to the pixel."""
    chart = _TrendChart()
    try:
        chart.resize(600, 420)
        chart.set_series(_series(1), "mlcape")

        assert chart._track_rect(0) == chart._plot_area()
    finally:
        chart.deleteLater()


def test_the_tracks_share_one_time_axis(qt_app):
    chart = _TrendChart()
    try:
        chart.resize(600, 460)
        chart.set_tracks(_series(1), ("mlcape", "shear_6km"))

        # The same valid time is at the same x in both bands, which is the whole
        # point of stacking rather than opening two charts.
        for point in chart._prepared:
            if point.track != 0:
                continue
            top = chart._screen_point(point.x, point.y, 0)
            bottom = chart._screen_point(point.x, point.y, 1)
            assert top.x() == pytest.approx(bottom.x())
    finally:
        chart.deleteLater()


def test_each_track_names_its_own_parameter_and_units(qt_app):
    chart = _TrendChart()
    try:
        chart.set_tracks(_series(1), ("mlcape", "shear_6km"))

        first = chart._track_title(0)
        second = chart._track_title(1)
        assert "MLCAPE" in first and "J/kg" in first
        assert first != second
        assert "kt" in second
    finally:
        chart.deleteLater()


def test_the_accessible_text_says_the_tracks_do_not_share_a_scale(qt_app):
    chart = _TrendChart()
    try:
        chart.set_tracks(_series(1), ("mlcape", "shear_6km"))

        description = chart.accessibleDescription()
        assert "share the time axis only" in description
        assert "its own units and value scale" in description
        assert "MLCAPE" in description
    finally:
        chart.deleteLater()


def test_a_gap_is_still_a_gap_in_every_track(qt_app):
    chart = _TrendChart()
    try:
        chart.set_tracks(_series(1, missing=(1,)), ("mlcape", "shear_6km"))

        gaps = [(point.track, point.sample) for point in chart._prepared if point.y is None]
        assert gaps == [(0, 1), (1, 1)]
        assert "missing point(s)" in chart.accessibleDescription()
    finally:
        chart.deleteLater()


def test_the_chart_paints_every_track(qt_app):
    chart = _TrendChart()
    try:
        chart.resize(620, 480)
        chart.set_tracks(_series(2), ("mlcape", "shear_6km"))
        chart.show()
        qt_app.processEvents()

        image = chart.grab().toImage()
        assert not image.isNull()
        # Real ink in both halves, not one chart and one empty band.
        for half in (0, 1):
            top = int(half * image.height() / 2)
            colours = {
                image.pixel(x, y)
                for x in range(0, image.width(), 4)
                for y in range(top + 4, top + int(image.height() / 2) - 4, 4)
            }
            assert len(colours) > 3
    finally:
        chart.close()
        chart.deleteLater()


# --------------------------------------------------------------------------- #
# Range locking
# --------------------------------------------------------------------------- #


def test_locking_freezes_each_track_at_the_range_it_needed(qt_app):
    chart = _TrendChart()
    try:
        chart.set_tracks(_series(1), ("mlcape", "shear_6km"))
        before = (chart._bounds_of(0), chart._bounds_of(1))

        chart.set_scale_lock(True)

        assert chart.scale_lock() is True
        assert (chart._bounds_of(0), chart._bounds_of(1)) == before
        assert chart.clipped_tracks() == ()
    finally:
        chart.deleteLater()


def test_a_locked_range_stays_put_when_bigger_data_arrives_and_says_so(qt_app):
    """The point of locking: the same displacement means the same quantity."""
    chart = _TrendChart()
    try:
        chart.set_tracks(_series(1), ("mlcape", "shear_6km"))
        chart.set_scale_lock(True)
        locked = chart._bounds_of(0)

        # A much larger CAPE arrives on the next update.
        chart.set_tracks(_series(1, cape=8000.0), ("mlcape", "shear_6km"))

        assert chart._bounds_of(0) == locked
        # Clamped, and named as clamped rather than silently truncated.
        assert "mlcape" in chart.clipped_tracks()
        assert "clamped to the axis edge" in chart.accessibleDescription()
    finally:
        chart.deleteLater()


def test_a_clamped_point_rests_on_the_axis_edge_not_over_a_neighbour(qt_app):
    chart = _TrendChart()
    try:
        chart.resize(600, 460)
        chart.set_tracks(_series(1), ("mlcape", "shear_6km"))
        chart.set_scale_lock(True)
        chart.set_tracks(_series(1, cape=8000.0), ("mlcape", "shear_6km"))

        band = chart._track_rect(0)
        for point in chart._prepared:
            if point.track != 0 or point.y is None:
                continue
            y = chart._screen_point(point.x, point.y, 0).y()
            assert band.top() - 0.5 <= y <= band.bottom() + 0.5
    finally:
        chart.deleteLater()


def test_refit_moves_the_fixed_ranges_to_the_data_loaded_now(qt_app):
    chart = _TrendChart()
    try:
        chart.set_tracks(_series(1), ("mlcape",))
        chart.set_scale_lock(True)
        chart.set_tracks(_series(1, cape=8000.0), ("mlcape",))
        assert chart.clipped_tracks() == ("mlcape",)

        chart.refit_scales()

        assert chart.scale_lock() is True
        assert chart.clipped_tracks() == ()
        assert chart._bounds_of(0)[1] > 8000.0
    finally:
        chart.deleteLater()


def test_unlocking_returns_every_track_to_automatic(qt_app):
    chart = _TrendChart()
    try:
        chart.set_tracks(_series(1), ("mlcape",))
        chart.set_scale_lock(True)
        chart.set_scale_lock(False)

        assert chart.scale_lock() is False
        assert chart.locked_scales() is None
        chart.set_tracks(_series(1, cape=8000.0), ("mlcape",))
        assert chart._bounds_of(0)[1] > 8000.0
        assert chart.clipped_tracks() == ()
    finally:
        chart.deleteLater()


def test_the_lock_round_trips_and_rejects_unusable_saved_state(qt_app):
    chart = _TrendChart()
    try:
        chart.set_tracks(_series(1), ("mlcape", "shear_6km"))
        chart.set_scale_lock(True)
        state = chart.scale_state()

        assert state["locked"] is True
        assert set(state["tracks"]) == {"mlcape", "shear_6km"}

        restored = _TrendChart()
        try:
            restored.set_tracks(_series(1), ("mlcape", "shear_6km"))
            restored.restore_scale_state(state)
            assert restored.scale_lock() is True
            assert restored._bounds_of(0) == chart._bounds_of(0)

            # A lock with no usable ranges must not pin the axes at nothing.
            for bad in (
                {"locked": True},
                {"locked": True, "tracks": {}},
                {"locked": True, "tracks": {"mlcape": ["low", "high"]}},
                {"locked": True, "tracks": {"mlcape": [5.0, 5.0]}},
                None,
                "nonsense",
            ):
                restored.restore_scale_state(state)
                restored.restore_scale_state(bad)
                if bad in (None, "nonsense"):
                    assert restored.scale_lock() is True
                else:
                    assert restored.scale_lock() is False
        finally:
            restored.deleteLater()
    finally:
        chart.deleteLater()
