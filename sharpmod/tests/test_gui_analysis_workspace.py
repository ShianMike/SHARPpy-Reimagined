"""Fast, stub-driven checks for the sounding analysis workspace."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

from qtpy.QtWidgets import QDockWidget, QMainWindow

from sharpmod.gui_analysis_workspace import install_analysis_workspace
from sharpmod.profile_metrics import (
    DEFAULT_METRIC_KEYS,
    ComparisonSample,
    EnsembleSummary,
    MetricDistribution,
    MetricSample,
    ThermodynamicBand,
)


RUN = datetime(2026, 9, 10, 0, tzinfo=timezone.utc)


class _Collection:
    def __init__(self, loc, model="HRRR", dates=None, members=("",)):
        self._dates = list(dates or (RUN, RUN + timedelta(hours=1)))
        self._prof_idx = 0
        self._highlight = members[0]
        self._meta = {"loc": loc, "model": model, "run": RUN}
        self.current_calls = []

    def getMeta(self, key):  # noqa: N802 - upstream API
        return self._meta[key]

    def getCurrentDate(self):  # noqa: N802 - upstream API
        return self._dates[self._prof_idx]

    def setCurrentDate(self, value):  # noqa: N802 - upstream API
        self.current_calls.append(value)
        self._prof_idx = self._dates.index(value)


class _Widget:
    def __init__(self, collections):
        self.prof_collections = list(collections)
        self.prof_ids = [f"profile-{index}" for index in range(len(collections))]
        self.pc_idx = 0
        self.update_calls = 0

    def setProfileCollection(self, prof_id):  # noqa: N802 - upstream API
        self.pc_idx = self.prof_ids.index(prof_id)
        self.updateProfs()

    def updateProfs(self):  # noqa: N802 - upstream API
        self.update_calls += 1


class _Window(QMainWindow):
    def __init__(self, collections):
        super().__init__()
        self.spc_widget = _Widget(collections)
        self._sharpmod_view_menu = self.menuBar().addMenu("View")


class _Engine:
    def __init__(self):
        self.calls = []
        self.timeline_result = ()
        self.compare_result = ()
        self.ensemble_result = EnsembleSummary(
            RUN, 0, 0, (), (), {}, (), False, "not an ensemble"
        )

    def timeline(self, collection, keys, member=None):
        self.calls.append(("timeline", collection, tuple(keys), member))
        return self.timeline_result

    def compare(self, collections, keys, valid_time=None):
        self.calls.append(("compare", tuple(collections), tuple(keys), valid_time))
        return self.compare_result

    def ensemble(self, collection, keys, valid_time=None, pressure_levels=None):
        self.calls.append(
            ("ensemble", collection, tuple(keys), valid_time, pressure_levels)
        )
        return self.ensemble_result


def _trend_samples():
    return (
        MetricSample(RUN, "control", {"mlcape": 900.0}, True),
        MetricSample(
            RUN + timedelta(hours=1),
            "control",
            {"mlcape": None},
            False,
            "missing hour",
        ),
        MetricSample(RUN + timedelta(hours=2), "control", {"mlcape": 1300.0}, True),
    )


def _make(qt_app, collections, engine=None):
    win = _Window(collections)
    engine = engine or _Engine()
    workspace = install_analysis_workspace(win, engine=engine, async_compute=False)
    qt_app.processEvents()
    return win, workspace, engine


def test_installer_is_idempotent_hidden_and_discoverable(qt_app):
    win, workspace, engine = _make(qt_app, [_Collection("KOUN")])
    try:
        again = install_analysis_workspace(win, engine=_Engine(), async_compute=False)
        assert again is workspace
        assert isinstance(workspace.dock, QDockWidget)
        assert workspace.dock.isHidden()
        assert [workspace.tabs.tabText(i) for i in range(workspace.tabs.count())] == [
            "Trends",
            "Compare",
            "Ensemble",
            "Notes",
        ]
        assert workspace.dock.toggleViewAction() in win._sharpmod_view_menu.actions()
        assert engine.calls == []
    finally:
        win.close()


def test_open_refreshes_only_visible_tab_and_selected_metric(qt_app):
    engine = _Engine()
    engine.timeline_result = _trend_samples()
    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")], engine)
    try:
        workspace.refresh()
        assert engine.calls == [], "a hidden dock must not do analysis"

        workspace.open("Trends")

        assert len(engine.calls) == 1
        kind, _collection, keys, member = engine.calls[0]
        assert (kind, keys, member) == ("timeline", ("mlcape",), None)
        assert "1 missing gap" in workspace.trend_status.text()
        assert len(workspace.trend_chart._prepared) == 3

        workspace.tabs.setCurrentIndex(workspace.TAB_NOTES)
        assert len(engine.calls) == 1
    finally:
        win.close()


def test_trend_point_selects_collection_time_and_focus(qt_app):
    collection = _Collection(
        "KOUN", dates=(RUN, RUN + timedelta(hours=1), RUN + timedelta(hours=2))
    )
    engine = _Engine()
    engine.timeline_result = _trend_samples()
    win, workspace, _engine = _make(qt_app, [collection], engine)
    try:
        workspace.open("Trends")
        workspace.trend_chart.pointActivated.emit(2)

        assert collection.current_calls == [RUN + timedelta(hours=2)]
        assert win.spc_widget.pc_idx == 0
        assert win.spc_widget.update_calls == 1
    finally:
        win.close()


def test_trend_csv_exports_cached_series_without_reanalysis(qt_app, tmp_path):
    engine = _Engine()
    engine.timeline_result = _trend_samples()
    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")], engine)
    try:
        workspace.open("Trends")
        destination = workspace.export_trend(tmp_path / "trend.csv")

        assert destination.read_text(encoding="utf-8").splitlines()[0] == (
            "valid_time,member,available,reason,mlcape"
        )
        assert len(engine.calls) == 1
    finally:
        win.close()


def test_trend_dialog_default_is_saved_in_dedicated_export_folder(
        qt_app, tmp_path, monkeypatch):
    from sharpmod import export_paths, gui_analysis_workspace

    application = tmp_path / "application"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    chosen = []

    def accept_default(_parent, _caption, start, _filter):
        chosen.append(Path(start))
        return start, "CSV files (*.csv)"

    monkeypatch.setattr(
        gui_analysis_workspace.QFileDialog, "getSaveFileName", accept_default
    )
    engine = _Engine()
    engine.timeline_result = _trend_samples()
    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")], engine)
    try:
        workspace.open("Trends")
        workspace._choose_trend_export()

        expected = application / "rendered_soundings" / "sounding-trend.csv"
        assert chosen == [expected]
        assert expected.is_file()
    finally:
        win.close()


def test_comparison_dialog_default_is_saved_in_dedicated_export_folder(
        qt_app, tmp_path, monkeypatch):
    from sharpmod import export_paths, gui_analysis_workspace

    application = tmp_path / "application"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    chosen = []

    def accept_default(_parent, _caption, start, _filter):
        chosen.append(Path(start))
        return start, "CSV files (*.csv)"

    monkeypatch.setattr(
        gui_analysis_workspace.QFileDialog, "getSaveFileName", accept_default
    )
    values = {key: float(index) for index, key in enumerate(DEFAULT_METRIC_KEYS)}
    engine = _Engine()
    engine.compare_result = (
        ComparisonSample(0, "HRRR", RUN, RUN, "", values, True, True),
        ComparisonSample(1, "RAP", RUN, RUN, "", values, True, True),
    )
    win, workspace, _engine = _make(
        qt_app, [_Collection("KOUN", "HRRR"), _Collection("KOUN", "RAP")], engine
    )
    try:
        workspace.open("Compare")
        workspace._choose_comparison_export()

        expected = application / "rendered_soundings" / "sounding-comparison.csv"
        assert chosen == [expected]
        assert expected.is_file()
    finally:
        win.close()


def test_compare_shows_absolute_deltas_and_explicit_mismatch(qt_app, tmp_path):
    collections = [
        _Collection("KOUN", "HRRR"),
        _Collection("KOUN", "RAP"),
        _Collection("KOUN", "NAM"),
    ]
    values_a = {key: float(index + 10) for index, key in enumerate(DEFAULT_METRIC_KEYS)}
    values_b = {key: value + 5.0 for key, value in values_a.items()}
    missing = {key: None for key in DEFAULT_METRIC_KEYS}
    engine = _Engine()
    engine.compare_result = (
        ComparisonSample(0, "HRRR", RUN, RUN, "", values_a, True, True),
        ComparisonSample(1, "RAP", RUN, RUN, "", values_b, True, True),
        ComparisonSample(
            2,
            "NAM",
            RUN,
            RUN + timedelta(hours=1),
            None,
            missing,
            False,
            False,
            "requested valid time is unavailable",
        ),
    )
    win, workspace, _engine = _make(qt_app, collections, engine)
    try:
        workspace.open("Compare")

        assert engine.calls[0][0] == "compare"
        assert workspace.compare_table.rowCount() == 3
        # First metric absolute/delta columns start at 3/4.
        assert workspace.compare_table.item(1, 3).text() == "15"
        assert workspace.compare_table.item(1, 4).text() == "5"
        assert "unavailable" in workspace.compare_table.item(2, 2).text()
        assert "1 mismatch/unavailable" in workspace.compare_status.text()

        destination = workspace.export_comparison(tmp_path / "compare.csv")
        header = destination.read_text(encoding="utf-8").splitlines()[0]
        assert header.endswith("," + ",".join(DEFAULT_METRIC_KEYS))
    finally:
        win.close()


def test_compare_empty_state_explains_existing_viewer_workflow(qt_app):
    win, workspace, engine = _make(qt_app, [_Collection("KOUN")])
    try:
        workspace.open("Compare")
        assert (
            "Load 2 or more soundings into the same viewer"
            in workspace.compare_status.text()
        )
        assert not any(call[0] == "compare" for call in engine.calls)
    finally:
        win.close()


def test_ensemble_renders_scalar_counts_and_thermodynamic_bands(qt_app):
    distribution = MetricDistribution(3, 10.0, 20.0, 30.0, 5.0, 35.0)
    scalar = {key: distribution for key in DEFAULT_METRIC_KEYS}
    engine = _Engine()
    engine.ensemble_result = EnsembleSummary(
        valid_time=RUN,
        member_count=4,
        available_member_count=3,
        members=("c00", "p01", "p02"),
        missing_members=("p03",),
        scalar=scalar,
        bands=(
            ThermodynamicBand(
                1000.0,
                MetricDistribution(3, 18.0, 20.0, 22.0, 17.0, 23.0),
                MetricDistribution(3, 14.0, 16.0, 18.0, 13.0, 19.0),
            ),
            ThermodynamicBand(
                850.0,
                MetricDistribution(3, 8.0, 10.0, 12.0, 7.0, 13.0),
                MetricDistribution(3, 3.0, 5.0, 7.0, 2.0, 8.0),
            ),
        ),
        available=True,
    )
    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")], engine)
    try:
        workspace.show_ensemble()

        assert workspace.tabs.currentIndex() == workspace.TAB_ENSEMBLE
        assert workspace.ensemble_table.rowCount() == len(DEFAULT_METRIC_KEYS)
        assert workspace.ensemble_chart._temperature_bounds == (-50.0, 50.0)
        assert workspace.ensemble_table.item(0, 1).text() == "3"
        assert "3 of 4 members available" in workspace.ensemble_status.text()
        assert "p03" in workspace.ensemble_status.text()
        assert len(workspace.ensemble_chart._temperature) == 2
        assert len(workspace.ensemble_chart._dewpoint) == 2
        surface = workspace.ensemble_chart._point(1000.0, 20.0)
        aloft = workspace.ensemble_chart._point(850.0, 20.0)
        assert surface.y() > aloft.y(), "higher pressure must plot nearer the ground"
    finally:
        win.close()


def test_workspace_state_round_trips_visibility_controls_and_notes(qt_app):
    collections = [_Collection("KOUN", "HRRR"), _Collection("KOUN", "RAP")]
    win, workspace, _engine = _make(qt_app, collections)
    try:
        state = {
            "visible": True,
            "tab": workspace.TAB_NOTES,
            "trend_collection": 1,
            "trend_metric": "shear_6km",
            "comparison_reference": 1,
            "notes": "RAP is warmer below 850 hPa.",
        }
        workspace.restore_session_state(state)

        restored = workspace.session_state()
        assert restored["visible"] is True
        assert restored["tab"] == workspace.TAB_NOTES
        assert restored["trend_collection"] == 1
        assert restored["trend_metric"] == "shear_6km"
        assert restored["comparison_reference"] == 1
        assert restored["notes"] == state["notes"]
    finally:
        win.close()


def test_stale_background_result_is_ignored(qt_app):
    engine = _Engine()
    engine.timeline_result = _trend_samples()
    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")], engine)
    try:
        workspace.open("Trends")
        original = workspace._trend_samples
        workspace._generation["trends"] = 3
        stale = (MetricSample(RUN, "", {"mlcape": 9999.0}, True),)

        workspace._accept_result(2, "trends", stale)

        assert workspace._trend_samples == original
    finally:
        win.close()


def test_bounded_async_path_delivers_without_slow_fixture_work(qt_app):
    engine = _Engine()
    engine.timeline_result = _trend_samples()
    win = _Window([_Collection("KOUN")])
    workspace = install_analysis_workspace(win, engine=engine)
    try:
        workspace.open("Trends")
        deadline = time.monotonic() + 1.0
        while not workspace._trend_samples and time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(0.001)

        assert workspace._trend_samples == engine.timeline_result
        assert workspace._pool.maxThreadCount() == 1
    finally:
        win.close()


# --------------------------------------------------------------------------- #
# every sounding on one graph
#
# The tab used to carry a sounding picker and plot one model at a time, so
# comparing runs meant switching back and forth and remembering the last shape.
# --------------------------------------------------------------------------- #
class _MultiEngine(_Engine):
    """Gives each collection its own curve, so the series are distinguishable."""

    CURVES = {
        "HRRR": (900.0, 1800.0, 2400.0),
        "RAP": (700.0, None, 2100.0),
        "NAM": (500.0, 1200.0, 1500.0),
    }

    def timeline(self, collection, keys, member=None):
        self.calls.append(("timeline", collection, tuple(keys), member))
        curve = self.CURVES[collection.getMeta("model")]
        return tuple(
            MetricSample(
                RUN + timedelta(hours=hour),
                "control",
                {keys[0]: value},
                value is not None,
                None if value is not None else "missing hour",
            )
            for hour, value in enumerate(curve)
        )


def _three():
    return [
        _Collection("KOUN", "HRRR"),
        _Collection("KOUN", "RAP"),
        _Collection("KOUN", "NAM"),
    ]


def test_there_is_no_sounding_picker_left_to_choose_between_them(qt_app):
    win, workspace, _engine = _make(qt_app, _three())
    try:
        assert not hasattr(workspace, "trend_collection"), (
            "the trends tab plots every sounding, so a single-sounding combo "
            "would contradict the chart"
        )
    finally:
        win.close()


def test_every_sounding_is_plotted_as_its_own_series(qt_app):
    win, workspace, engine = _make(qt_app, _three(), _MultiEngine())
    try:
        workspace.open("Trends")

        assert sum(call[0] == "timeline" for call in engine.calls) == 3
        series = workspace.trend_chart.series
        assert [item.short_label for item in series] == ["HRRR", "RAP", "NAM"]
        assert [item.collection_index for item in series] == [0, 1, 2]
        assert len({item.colour for item in series}) == 3, "one colour per sounding"
        # Flattened in series-then-sample order: 3 soundings x 3 hours.
        assert len(workspace.trend_chart._prepared) == 9
        assert "across 3 soundings" in workspace.trend_status.text()
    finally:
        win.close()


def test_a_point_opens_its_own_sounding_not_the_first(qt_app):
    """The gap this closes: one axis, so a point must carry its collection."""
    collections = _three()
    win, workspace, _engine = _make(qt_app, collections, _MultiEngine())
    try:
        workspace.open("Trends")
        # Series 2 (NAM), sample 1 -> flat index 3*2 + 1.
        workspace.trend_chart.pointActivated.emit(7)

        assert collections[2].current_calls == [RUN + timedelta(hours=1)]
        assert collections[0].current_calls == []
        assert win.spc_widget.pc_idx == 2
    finally:
        win.close()


def test_a_gap_belongs_to_the_series_it_came_from(qt_app):
    win, workspace, _engine = _make(qt_app, _three(), _MultiEngine())
    try:
        workspace.open("Trends")

        gaps = [
            point for point in workspace.trend_chart._prepared if point.y is None
        ]
        assert [point.series for point in gaps] == [1], "only the RAP hour is missing"
        assert "1 missing gap" in workspace.trend_status.text()
    finally:
        win.close()


def test_several_soundings_export_with_a_sounding_column(qt_app, tmp_path):
    win, workspace, engine = _make(qt_app, _three(), _MultiEngine())
    try:
        workspace.open("Trends")
        before = len(engine.calls)

        destination = workspace.export_trend(tmp_path / "trends.csv")

        lines = destination.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "sounding,valid_time,member,available,reason,mlcape"
        assert len(lines) == 1 + 9, "every plotted sample is exported"
        assert lines[1].startswith("KOUN \u00b7 HRRR")
        assert len(engine.calls) == before, "export must not re-run analysis"
    finally:
        win.close()


def test_the_focused_sounding_survives_a_session_round_trip(qt_app):
    win, workspace, _engine = _make(qt_app, _three(), _MultiEngine())
    try:
        workspace.open("Trends")
        workspace.trend_chart.pointActivated.emit(7)

        assert workspace.session_state()["trend_collection"] == 2

        workspace.restore_session_state({"trend_collection": 1, "visible": False})
        assert workspace.session_state()["trend_collection"] == 1
    finally:
        win.close()


def test_a_hovered_point_names_the_sounding_it_belongs_to(qt_app):
    """Which run a point belongs to is what it cannot say for itself."""
    win, workspace, _engine = _make(qt_app, _three(), _MultiEngine())
    try:
        workspace.open("Trends")

        # 3 hours per sounding, so flat index 3 is the RAP series' first sample
        # and 6 is the NAM series'.
        assert workspace.trend_chart._sample_tooltip(0).startswith("KOUN \u00b7 HRRR")
        assert workspace.trend_chart._sample_tooltip(3).startswith("KOUN \u00b7 RAP")
        assert workspace.trend_chart._sample_tooltip(6).startswith("KOUN \u00b7 NAM")
    finally:
        win.close()


def test_soundings_sharing_a_model_are_still_told_apart(qt_app):
    """Two runs of one model is the comparison this chart exists for."""
    collections = [_Collection("KOUN", "HRRR"), _Collection("KOUN", "HRRR")]
    win, workspace, engine = _make(qt_app, collections)
    engine.timeline_result = _trend_samples()
    try:
        workspace.open("Trends")

        labels = [item.short_label for item in workspace.trend_chart.series]
        assert len(set(labels)) == 2, f"ambiguous legend labels: {labels}"
    finally:
        win.close()
