"""Fast, stub-driven checks for the sounding analysis workspace."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from qtpy.QtCore import QSettings, Qt
from qtpy.QtGui import QResizeEvent
from qtpy.QtWidgets import QApplication, QDockWidget, QMainWindow, QWidget

from sharpmod import gui_theme
from sharpmod.gui_analysis_workspace import (
    AnalysisWorkspace,
    install_analysis_workspace,
)
from sharpmod.gui_visual_comparison import VisualComparisonWidget
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


def _visual_profile(offset=0.0):
    return SimpleNamespace(
        hght=[300.0, 1_500.0, 3_000.0],
        pres=[975.0, 850.0, 700.0],
        tmpc=[24.0 + offset, 14.0 + offset, 2.0 + offset],
        dwpc=[18.0 + offset, 8.0 + offset, -4.0 + offset],
        u=[5.0 + offset, 15.0 + offset, 25.0 + offset],
        v=[10.0 + offset, 20.0 + offset, 30.0 + offset],
    )


class _VisualCollection(_Collection):
    def __init__(self, loc, *, offset=0.0, dates=(RUN,), profiles=None):
        super().__init__(loc, dates=dates)
        if profiles is None:
            profiles = tuple(_visual_profile(offset + index) for index in range(len(dates)))
        self._profs = {"": list(profiles)}


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


class _PickerController:
    def __init__(self):
        self.selected_tabs = []
        self.focus_calls = 0

    def _select_tab(self, title):
        self.selected_tabs.append(str(title))

    def focusPicker(self):  # noqa: N802 - production controller API
        self.focus_calls += 1


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

    def compare(self, collections, keys, valid_time=None, reference_index=0):
        self.calls.append(
            (
                "compare",
                tuple(collections),
                tuple(keys),
                valid_time,
                reference_index,
            )
        )
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


def test_visual_comparison_canvas_paints_with_the_current_theme(qt_app):
    widget = VisualComparisonWidget()
    try:
        widget.resize(900, 520)
        widget.show()
        qt_app.processEvents()

        assert not widget.grab().isNull()
    finally:
        widget.close()


def test_visual_comparison_slots_do_not_move_when_reference_changes(qt_app):
    collections = tuple(
        _VisualCollection(label, offset=float(index))
        for index, label in enumerate(("KOUN", "KTLX", "KFDR", "KVNX"))
    )
    ids = ("profile-a", "profile-b", "profile-c", "profile-d")
    widget = VisualComparisonWidget()
    try:
        widget.set_layout_count(4)
        widget.set_collections(collections, 0, RUN, profile_ids=ids)
        assert widget.slot_ids() == ids

        # Selecting a profile already in another slot swaps the assignments;
        # every visible panel retains one unambiguous meaning.
        widget.slot_choices[0].setCurrentIndex(
            widget.slot_choices[0].findData("profile-c")
        )
        assert widget.slot_ids() == (
            "profile-c",
            "profile-b",
            "profile-a",
            "profile-d",
        )

        widget.set_collections(collections, 1, RUN, profile_ids=ids)

        assert widget.slot_ids() == (
            "profile-c",
            "profile-b",
            "profile-a",
            "profile-d",
        )
        assert [panel.profile_id for panel in widget.canvas.panels()] == list(
            widget.slot_ids()
        )
        assert [panel.profile_id for panel in widget.canvas.panels() if panel.reference] == [
            "profile-b"
        ]
        assert "Reference:" in widget.status.text()
        assert "KTLX" in widget.status.text()
    finally:
        widget.close()


def test_visual_comparison_missing_profile_keeps_later_slots_in_place(qt_app):
    collections = (
        _VisualCollection("KOUN"),
        _VisualCollection("KTLX", dates=(RUN + timedelta(hours=1),)),
        _VisualCollection("KFDR", offset=2.0),
    )
    widget = VisualComparisonWidget()
    try:
        widget.set_layout_count(4)
        widget.set_collections(
            collections,
            0,
            RUN,
            profile_ids=("reference", "missing", "later"),
        )

        panels = widget.canvas.panels()
        assert len(panels) == 4
        assert [panel.profile_id for panel in panels[:3]] == [
            "reference",
            "missing",
            "later",
        ]
        assert panels[1].profile is None
        assert "exact reference valid time" in panels[1].unavailable_reason
        assert panels[2].profile is not None
        assert panels[3].profile_id is None
        assert "assigned" in panels[3].unavailable_reason
    finally:
        widget.close()


def test_visual_comparison_slots_survive_reorder_removal_and_layout_change(qt_app):
    first = _VisualCollection("KOUN")
    second = _VisualCollection("KTLX", offset=1.0)
    third = _VisualCollection("KFDR", offset=2.0)
    widget = VisualComparisonWidget()
    try:
        widget.set_layout_count(4)
        widget.set_collections(
            (first, second, third),
            0,
            RUN,
            profile_ids=("first", "second", "third"),
        )
        assert widget.slot_ids()[:3] == ("first", "second", "third")

        widget.set_layout_count(2)
        widget.set_layout_count(4)
        assert widget.slot_ids()[:3] == ("first", "second", "third")

        widget.set_collections(
            (third, first, second),
            1,
            RUN,
            profile_ids=("third", "first", "second"),
        )
        assert [panel.profile_id for panel in widget.canvas.panels()[:3]] == [
            "first",
            "second",
            "third",
        ]

        widget.set_collections(
            (first, third),
            0,
            RUN,
            profile_ids=("first", "third"),
        )
        panels = widget.canvas.panels()
        assert widget.slot_ids()[:3] == ("first", "second", "third")
        assert panels[1].profile is None
        assert "no longer loaded" in panels[1].unavailable_reason
        assert panels[2].profile is not None
        assert widget.slot_choices[1].currentData() == "second"
        assert widget.slot_choices[1].currentText().startswith("Unavailable")
    finally:
        widget.close()


def test_visual_comparison_separates_units_and_recomputes_subtraction_direction(qt_app):
    reference_profile = _visual_profile()
    candidate_profile = SimpleNamespace(
        hght=list(reference_profile.hght),
        pres=list(reference_profile.pres),
        tmpc=[value + 2.0 for value in reference_profile.tmpc],
        dwpc=[value - 1.0 for value in reference_profile.dwpc],
        u=[value + 30.0 for value in reference_profile.u],
        v=[value - 25.0 for value in reference_profile.v],
    )
    collections = (
        _VisualCollection("Reference", profiles=(reference_profile,)),
        _VisualCollection("Candidate", profiles=(candidate_profile,)),
    )
    widget = VisualComparisonWidget()
    try:
        widget.resize(1_000, 540)
        widget.set_collections(
            collections,
            0,
            RUN,
            profile_ids=("reference", "candidate"),
        )
        widget.show()
        qt_app.processEvents()
        assert not widget.grab().isNull()

        spans = widget.canvas.difference_spans()
        assert spans[1]["thermodynamic_c"] == pytest.approx(5.0)
        assert spans[1]["wind_kt"] == pytest.approx(30.0)
        assert "Thermodynamic differences ΔT and ΔTd use °C" in (
            widget.canvas.accessibleDescription()
        )
        assert "wind differences Δu and Δv use kt on a separate axis" in (
            widget.canvas.accessibleDescription()
        )
        assert "Gaps mean missing" in widget.canvas.accessibleDescription()
        assert "Δ = slot − reference" in widget.status.text()

        candidate_panel = widget.canvas.panels()[1]
        assert candidate_panel.difference.reference_label.startswith("Reference")
        assert candidate_panel.difference.candidate_label.startswith("Candidate")
        assert candidate_panel.difference.levels[0].temperature_c == pytest.approx(2.0)
        assert candidate_panel.difference.levels[0].u_wind_kt == pytest.approx(30.0)

        widget.set_collections(
            collections,
            1,
            RUN,
            profile_ids=("reference", "candidate"),
        )
        assert widget.slot_ids()[:2] == ("reference", "candidate")
        reversed_panel = widget.canvas.panels()[0]
        assert reversed_panel.difference.reference_label.startswith("Candidate")
        assert reversed_panel.difference.candidate_label.startswith("Reference")
        assert reversed_panel.difference.levels[0].temperature_c == pytest.approx(-2.0)
        assert reversed_panel.difference.levels[0].u_wind_kt == pytest.approx(-30.0)
    finally:
        widget.close()


def test_installer_is_idempotent_hidden_and_discoverable(qt_app):
    win, workspace, engine = _make(qt_app, [_Collection("KOUN")])
    try:
        again = install_analysis_workspace(win, engine=_Engine(), async_compute=False)
        assert again is workspace
        assert isinstance(workspace.dock, QDockWidget)
        assert workspace.dock.isHidden()
        # Only the retained analysis and export pages are built.
        assert [workspace.tabs.tabText(i) for i in range(workspace.tabs.count())] == [
            "Trends",
            "Compare",
            "Ensemble",
            "Animation",
        ]
        removed = {"scenarios", "verification", "winds", "replay", "notes"}
        assert all(workspace.tab_index_for(key) is None for key in removed)
        assert not removed.intersection(workspace.session_state())
        assert not {"observed_winds", "case_replay", "analyst_notes", "analysis_checkpoints"}.intersection(
            workspace.session_state()
        )
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
        assert workspace.trend_metric.currentText() == "MLCAPE (J/kg)"
        assert workspace.trend_refresh.text() == "Refresh"
        assert workspace.trend_export.text() == "Export CSV…"
        assert workspace.trend_load.isHidden()
        assert workspace.trend_status.accessibleName() == "Status message"
        assert workspace.trend_status.property("statusLevel") == "info"
        kind, _collection, keys, member = engine.calls[0]
        assert (kind, keys, member) == ("timeline", ("mlcape",), None)
        assert "1 missing gap" in workspace.trend_status.text()
        assert len(workspace.trend_chart._prepared) == 3

        workspace.tabs.setCurrentIndex(workspace.TAB_ANIMATION)
        assert len(engine.calls) == 1
    finally:
        win.close()


def test_threshold_retry_forwards_the_exact_member_set(qt_app):
    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")])
    calls = []

    class _Coordinator:
        def retry_ensemble(self, target, collection, members):
            calls.append((target, collection, tuple(members)))
            return True

    win._sharpmod_controller = SimpleNamespace(
        _model_compare_coordinator=_Coordinator()
    )
    try:
        collection = win.spc_widget.prof_collections[0]
        assert workspace._retry_threshold_members(collection, ("p03", "p07"))
        assert calls == [(win, collection, ("p03", "p07"))]
    finally:
        win.close()


def test_status_severity_is_visible_text_and_accessible_not_colour_only(qt_app):
    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")])
    try:
        workspace._set_status(
            workspace.compare_status, "Comparison is unavailable.", level="error"
        )
        assert workspace.compare_status.text().startswith("Error: ")
        assert workspace.compare_status.accessibleName() == "Error message"
        assert workspace.compare_status.accessibleDescription() == (
            workspace.compare_status.text()
        )

        workspace._set_status(
            workspace.compare_status, "One row is incomplete.", level="warning"
        )
        assert workspace.compare_status.text().startswith("Warning: ")
        assert workspace.compare_status.accessibleName() == "Warning message"
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
        assert "Only one sounding is loaded" in workspace.compare_status.text()
        assert "at least two" in workspace.compare_status.text().casefold()
        assert not workspace.compare_add.isHidden()
        assert not any(call[0] == "compare" for call in engine.calls)
    finally:
        win.close()


def test_empty_analysis_pages_explain_inputs_and_reuse_authoritative_picker(qt_app):
    """Every page either works without a sounding or offers one real way out."""
    win, workspace, engine = _make(qt_app, [])
    controller = _PickerController()
    win._sharpmod_controller = controller
    try:
        workspace.open("trends")
        assert "No soundings are loaded" in workspace.trend_status.text()
        assert "forecast-model run" in workspace.trend_status.text()
        assert not workspace.trend_load.isHidden()
        workspace.trend_load.click()
        assert controller.selected_tabs == ["Forecast Model"]
        assert controller.focus_calls == 1

        workspace.open("compare")
        assert "No soundings are loaded" in workspace.compare_status.text()
        assert "at least two" in workspace.compare_status.text().casefold()
        assert not workspace.compare_add.isHidden()
        workspace.compare_add.click()
        # Compare can use any provider, so it preserves the picker's source tab.
        assert controller.selected_tabs == ["Forecast Model"]
        assert controller.focus_calls == 2

        workspace.open("ensemble")
        assert "No soundings are loaded" in workspace.ensemble_status.text()
        assert "ensemble sounding" in workspace.ensemble_status.text()
        assert not workspace.ensemble_load.isHidden()
        workspace.ensemble_load.click()
        assert controller.selected_tabs[-1] == "Forecast Model"
        assert controller.focus_calls == 3

        workspace.open("animation")
        animation = workspace.animation_workspace
        assert "No soundings are loaded" in animation.status.text()
        assert not animation.load_sounding.isHidden()
        assert not animation.gif_button.isEnabled()
        animation.load_sounding.click()
        assert controller.focus_calls == 4

        assert engine.calls == [], "empty or hidden pages must not start analysis"
    finally:
        win.close()


def test_incompatible_analysis_inputs_keep_precise_recovery_visible(qt_app):
    """Loaded is not the same as usable: incompatible data stays actionable."""
    engine = _Engine()
    engine.timeline_result = ()
    collection = _Collection("KOUN")
    win, workspace, _engine = _make(qt_app, [collection], engine)
    controller = _PickerController()
    win._sharpmod_controller = controller
    try:
        workspace.open("trends")
        assert "contain no forecast times" in workspace.trend_status.text()
        assert not workspace.trend_load.isHidden()

        workspace.open("compare")
        assert "Only one sounding is loaded" in workspace.compare_status.text()
        assert not workspace.compare_add.isHidden()
        assert not any(call[0] == "compare" for call in engine.calls)
        active = workspace.tab_key()
        workspace.compare_add.click()
        assert workspace.tab_key() == active
        assert win.spc_widget.prof_collections == [collection]
        assert win.spc_widget.pc_idx == 0

        workspace.open("ensemble")
        assert "not an ensemble" in workspace.ensemble_status.text()
        assert not workspace.ensemble_load.isHidden()
        active = workspace.tab_key()
        workspace.ensemble_load.click()
        assert workspace.tab_key() == active
        assert win.spc_widget.prof_collections == [collection]
        assert win.spc_widget.pc_idx == 0
        assert controller.selected_tabs[-1] == "Forecast Model"

        workspace.open("animation")
        assert workspace.animation_workspace.load_sounding.isHidden()

        calls = [call[0] for call in engine.calls]
        assert calls.count("timeline") == 1
        assert calls.count("ensemble") >= 1
        assert set(calls) == {"timeline", "ensemble"}
    finally:
        win.close()


def test_theme_change_recolors_cached_values_without_recomputing(qt_app):
    from sharpmod.gui_analysis_workspace import _delta_ink, _series_colours

    engine = _Engine()
    engine.compare_result = (
        ComparisonSample(0, "HRRR", RUN, RUN, "", {"mlcape": 100.0}, True, True),
        ComparisonSample(1, "RAP", RUN, RUN, "", {"mlcape": 120.0}, True, True),
    )
    engine.timeline_result = _trend_samples()
    win, workspace, _engine = _make(qt_app, [_Collection("KOUN"), _Collection("KOUN", "RAP")], engine)
    try:
        workspace.open("Compare")
        calls = tuple(engine.calls)
        delta = workspace.compare_table.item(1, 4)
        gui_theme.apply_theme(qt_app, color_style="inverted", text_scale=150)
        qt_app.processEvents()
        assert delta.text() == "20"
        assert delta.foreground().color() == _delta_ink(20.0)
        assert tuple(engine.calls) == calls

        workspace.open("Trends")
        before = workspace.trend_chart._prepared
        calls = tuple(engine.calls)
        gui_theme.apply_theme(qt_app, color_style="protanopia")
        qt_app.processEvents()
        assert not workspace.trend_chart.grab().isNull()
        assert tuple(item.colour for item in workspace.trend_chart.series) == _series_colours(2)
        assert workspace.trend_chart._prepared == before
        assert tuple(engine.calls) == calls
    finally:
        gui_theme.apply_theme(qt_app, color_style="standard")
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
        assert workspace.ensemble_load.isHidden()
        assert len(workspace.ensemble_chart._temperature) == 2
        assert len(workspace.ensemble_chart._dewpoint) == 2
        description = workspace.ensemble_chart.accessibleDescription()
        assert "10th to 90th" in description
        assert "temperature is solid" in description
        assert "dewpoint is dashed" in description
        assert "p10 18.0 \u00b0C, median 20.0 \u00b0C, p90 22.0 \u00b0C" in description
        assert "1000" in description and "850" in description
        surface = workspace.ensemble_chart._point(1000.0, 20.0)
        aloft = workspace.ensemble_chart._point(850.0, 20.0)
        assert surface.y() > aloft.y(), "higher pressure must plot nearer the ground"
        from qtpy.QtCore import Qt
        from qtpy.QtTest import QTest

        QTest.keyClick(workspace.ensemble_chart, Qt.Key_Home)
        assert "Focused level: 1000 hPa" in workspace.ensemble_chart.accessibleDescription()
        QTest.keyClick(workspace.ensemble_chart, Qt.Key_Up)
        assert "Focused level: 850 hPa" in workspace.ensemble_chart.accessibleDescription()
        assert "median 10.0 \u00b0C" in workspace.ensemble_chart.toolTip()
        workspace.ensemble_chart.set_band(())
        QTest.keyClick(workspace.ensemble_chart, Qt.Key_Up)
        assert workspace.ensemble_chart._level_index is None
    finally:
        win.close()


@pytest.mark.parametrize("scale", (100, 200))
def test_large_text_does_not_force_window_to_hidden_tabs_minimum(qt_app, scale):
    """An inactive complex form must not make a visible chart window too tall."""
    from qtpy.QtWidgets import QScrollArea

    try:
        gui_theme.apply_theme(qt_app, color_style="standard", text_scale=scale)
        win, workspace, _engine = _make(qt_app, [_Collection("KOUN")])
        workspace.open("Trends")
        win.resize(1000, 700)
        win.show()
        qt_app.processEvents()
        assert win.height() <= 700
        for index in range(workspace.tabs.count()):
            viewport = workspace.tabs.widget(index)
            assert isinstance(viewport, QScrollArea)
            assert viewport.widget() is not None
        workspace.open("Ensemble")
        qt_app.processEvents()
        viewport = workspace.tabs.currentWidget()
        needed = viewport.widget().minimumSizeHint().height()
        if needed > viewport.viewport().height():
            assert viewport.verticalScrollBar().maximum() > 0
    finally:
        gui_theme.apply_theme(qt_app, color_style="standard")
        if "win" in locals():
            win.close()


def test_trend_controls_and_numeric_points_are_keyboard_reachable(qt_app):
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest

    engine = _Engine()
    engine.timeline_result = _trend_samples()
    collection = _Collection("KOUN", dates=(RUN, RUN + timedelta(hours=2)))
    win, workspace, _engine = _make(qt_app, [collection], engine)
    try:
        workspace.open("Trends")
        win.show()
        win.activateWindow()
        workspace.trend_metric.setFocus()
        qt_app.processEvents()
        # Every enabled control on this page is a Tab stop, in the order it is
        # read in. The range lock, Refit, and the track chooser were added between
        # the parameter selector and Refresh, so they belong in this chain too.
        # Refit is skipped while it is disabled, which is correct: it does nothing
        # until the ranges are fixed.
        assert workspace.trend_refit.isEnabled() is False
        chain = (
            workspace.trend_lock,
            workspace.trend_tracks,
            workspace.trend_refresh,
            workspace.trend_export,
            workspace.trend_chart,
        )
        source = workspace.trend_metric
        for expected in chain:
            QTest.keyClick(source, Qt.Key_Tab)
            assert expected.hasFocus(), f"{expected.objectName()} is not reachable"
            source = expected

        # Once the ranges are fixed, Refit becomes reachable rather than staying
        # a control the keyboard can never get to.
        workspace.trend_lock.setChecked(True)
        qt_app.processEvents()
        assert workspace.trend_refit.isEnabled()
        workspace.trend_lock.setFocus()
        qt_app.processEvents()
        QTest.keyClick(workspace.trend_lock, Qt.Key_Tab)
        assert workspace.trend_refit.hasFocus()
        workspace.trend_lock.setChecked(False)
        qt_app.processEvents()
        workspace.trend_export.setFocus()
        QTest.keyClick(workspace.trend_export, Qt.Key_Tab)
        QTest.keyClick(workspace.trend_chart, Qt.Key_End)
        assert "1300" in workspace.trend_chart.accessibleDescription()
        QTest.keyClick(workspace.trend_chart, Qt.Key_Return)
        assert collection.current_calls == [RUN + timedelta(hours=2)]
    finally:
        win.close()


def test_workspace_state_round_trips_visibility_and_controls(qt_app):
    collections = [_Collection("KOUN", "HRRR"), _Collection("KOUN", "RAP")]
    win, workspace, _engine = _make(qt_app, collections)
    try:
        state = {
            "version": 2,
            "visible": True,
            "tab_key": "animation",
            "trend_collection": 1,
            "trend_metric": "shear_6km",
            "comparison_reference": 1,
            "comparison_layout": 4,
            "comparison_slots": [
                {"profile_id": "profile-1", "label": "KOUN · RAP"},
                {"profile_id": "profile-0", "label": "KOUN · HRRR"},
                {"profile_id": "removed-profile", "label": "Removed profile"},
                {"profile_id": None, "label": ""},
            ],
        }
        workspace.restore_session_state(state)

        restored = workspace.session_state()
        assert restored["visible"] is True
        assert restored["tab_key"] == "animation"
        assert restored["tab"] == workspace.TAB_ANIMATION
        assert restored["trend_collection"] == 1
        assert restored["trend_metric"] == "shear_6km"
        assert restored["comparison_reference"] == 1
        assert restored["comparison_layout"] == 4
        assert [entry["profile_id"] for entry in restored["comparison_slots"]] == [
            "profile-1",
            "profile-0",
            "removed-profile",
            None,
        ]
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


class _HeldAnalysisPool:
    """A bounded queue held before execution, not a replacement engine."""

    def __init__(self):
        self.tasks = []

    def start(self, task):
        self.tasks.append(task)

    def tryTake(self, task):  # noqa: N802 - Qt API
        if task not in self.tasks:
            return False
        self.tasks.remove(task)
        return True

    def run_next(self):
        self.tasks.pop(0).run()


def test_cross_kind_queue_replacement_releases_old_refresh(qt_app):
    win, workspace, _engine = _make(qt_app, [_Collection("KOUN"), _Collection("KTLX")])
    pool = _HeldAnalysisPool()
    workspace._pool = pool
    workspace._async_compute = True
    try:
        workspace._refresh_trends()
        assert not workspace.trend_refresh.isEnabled()
        workspace.tabs.setCurrentIndex(workspace.TAB_COMPARE)
        assert len(pool.tasks) == 1 and pool.tasks[0].kind == "compare"
        assert workspace.trend_refresh.isEnabled(), "removed work must release its busy latch"
        assert workspace.trend_job.snapshot.state == "superseded"
        pool.run_next()
        assert workspace.compare_refresh.isEnabled()
    finally:
        workspace.shutdown()
        win.close()


def test_trend_job_freezes_input_and_retries_only_failed_times(qt_app):
    from sharpmod.profile_metrics import ProfileMetricsEngine

    collection = _VisualCollection("Saved run", dates=(RUN, RUN + timedelta(hours=1)))
    for index, profile in enumerate(collection._profs[""]):
        profile.value = float(index + 1)
    calls = []

    def fast(profile):
        calls.append(profile.value)
        if profile.value == 2.0 and calls.count(2.0) == 1:
            raise RuntimeError("one-time failure")
        return {key: profile.value for key in DEFAULT_METRIC_KEYS}

    engine = ProfileMetricsEngine(fast_analyzer=fast)
    win, workspace, _engine = _make(qt_app, [collection], engine)
    pool = _HeldAnalysisPool()
    workspace._pool = pool
    workspace._async_compute = True
    try:
        workspace._refresh_trends()
        collection._meta["loc"] = "Unapplied replacement"
        collection._dates.append(RUN + timedelta(hours=2))
        collection._profs[""] = [_visual_profile(90.0)]
        pool.run_next()
        assert calls == [1.0, 2.0]
        assert workspace.trend_job.snapshot.counts.completed == 1
        assert workspace.trend_job.snapshot.counts.failed == 1
        assert workspace.trend_job.snapshot.counts.requested == 2
        assert "Saved run" in workspace.trend_job.snapshot.affected_input
        assert "Unapplied" not in workspace.trend_job.snapshot.affected_input
        workspace.trend_chart._highlight(0)
        workspace.trend_job.retry_button.click()
        pool.run_next()
        assert calls == [1.0, 2.0, 2.0]
        assert workspace.trend_job.snapshot.counts.completed == 2
        assert workspace.trend_chart._hover_index == 0
        assert workspace._trend_series[0].label.startswith("Saved run")
    finally:
        workspace.shutdown()
        win.close()


def test_trend_cancel_rejects_finished_queued_success_and_reuses_it(qt_app):
    from threading import Event
    from sharpmod.profile_metrics import ProfileMetricsEngine

    collection = _VisualCollection("Retained", dates=(RUN,))
    engine = ProfileMetricsEngine(fast_analyzer=lambda _profile: {"mlcape": 10.0})
    win, workspace, _engine = _make(qt_app, [collection], engine)
    workspace._refresh_trends()
    prior = workspace._trend_series
    entered, release = Event(), Event()
    calls = []

    def fast(_profile):
        calls.append(1)
        entered.set()
        assert release.wait(2.0)
        return {"mlcape": 20.0}

    engine._fast_analyzer = fast
    engine.clear()
    workspace._async_compute = True
    try:
        workspace._refresh_trends()
        assert entered.wait(2.0)
        task = workspace._tasks[("trends", workspace._generation["trends"])]
        release.set()
        assert task.done_event.wait(2.0), "finish without delivering queued Qt callbacks"
        workspace._cancel_analysis("trends")
        qt_app.processEvents()
        assert workspace._trend_series is prior
        assert workspace.trend_job.snapshot.state == "cancelled"
        assert workspace.trend_job.snapshot.counts.completed == 1
        assert "Retained" in workspace.trend_job.snapshot.retained
        assert RUN.strftime("%Y-%m-%d %H:%MZ") in workspace.trend_job.snapshot.retained
        workspace.trend_job.retry_button.click()
        assert workspace._pool.waitForDone(2000)
        qt_app.processEvents()
        assert calls == [1], "retry should apply the saved result, not recalculate it"
        assert workspace._trend_samples[0].values["mlcape"] == 20.0
        assert workspace.trend_job.snapshot.state == "completed"
    finally:
        release.set()
        workspace.shutdown()
        workspace._pool.waitForDone(2000)
        win.close()


def test_trend_partial_cancel_retains_ui_context_and_resumes_missing_times(qt_app):
    from threading import Event
    from sharpmod.profile_metrics import ProfileMetricsEngine

    dates = tuple(RUN + timedelta(hours=index) for index in range(4))
    profiles = tuple(_visual_profile(index) for index in range(4))
    for index, profile in enumerate(profiles):
        profile.value = index + 1
    collection = _VisualCollection("Exact saved times", dates=dates, profiles=profiles)
    collection._profs[""][2] = None
    engine = ProfileMetricsEngine(fast_analyzer=lambda profile: {"mlcape": profile.value * 10})
    win, workspace, _engine = _make(qt_app, [collection], engine)
    entered, release = Event(), Event()
    calls = []

    def fast(profile):
        calls.append(profile.value)
        if profile.value == 2:
            entered.set()
            assert release.wait(2.0)
        return {"mlcape": profile.value * 20}

    try:
        win.resize(1100, 800)
        win.show()
        workspace.open("trends")
        prior = workspace._trend_series
        workspace.trend_chart._highlight(0)
        workspace.trend_chart.setFocus()
        qt_app.processEvents()
        pane_size = workspace.dock.size()
        scroll = workspace.tabs.currentWidget()
        scroll.verticalScrollBar().setValue(min(10, scroll.verticalScrollBar().maximum()))
        position = scroll.verticalScrollBar().value()
        engine._fast_analyzer = fast
        engine.clear()
        workspace._async_compute = True
        workspace._refresh_trends()
        assert entered.wait(2.0)
        qt_app.processEvents()
        assert workspace.trend_job.snapshot.done == 1
        assert workspace.trend_job.snapshot.total == 4
        workspace._cancel_analysis("trends")
        release.set()
        assert workspace._pool.waitForDone(2000)
        qt_app.processEvents()
        counts = workspace.trend_job.snapshot.counts
        assert (counts.completed, counts.cancelled, counts.unavailable, counts.requested) == (2, 1, 1, 4)
        assert workspace._trend_series is prior
        assert QApplication.focusWidget() is workspace.trend_chart
        assert workspace.trend_chart._hover_index == 0
        assert workspace.dock.size() == pane_size
        assert scroll.verticalScrollBar().value() == min(position, scroll.verticalScrollBar().maximum())
        workspace.trend_job.retry_button.click()
        assert workspace._pool.waitForDone(2000)
        qt_app.processEvents()
        assert calls == [1, 2, 4], "only the one withheld loaded profile should retry"
        assert workspace.trend_job.snapshot.counts.completed == 3
        assert workspace.trend_job.snapshot.counts.unavailable == 1
        assert workspace.trend_chart._hover_index == 0
        assert QApplication.focusWidget() is workspace.trend_chart
        assert workspace.dock.size() == pane_size
    finally:
        release.set()
        workspace.shutdown()
        workspace._pool.waitForDone(2000)
        win.close()


def test_replaced_collections_reject_late_trends_and_saved_retry(qt_app):
    engine = _Engine()
    engine.timeline_result = _trend_samples()
    win, workspace, _engine = _make(qt_app, [_Collection("Original")], engine)
    pool = _HeldAnalysisPool()
    workspace._pool = pool
    workspace._async_compute = True
    try:
        workspace._refresh_trends()
        task = pool.tasks.pop()
        win.spc_widget.prof_collections = [_Collection("Replacement")]
        task.run()
        assert workspace._trend_series == ()
        assert workspace.trend_job.snapshot.state == "superseded"
        workspace._refresh_trends()
        workspace._cancel_analysis("trends")
        pool.run_next()
        assert workspace.trend_job.snapshot.retryable
        win.spc_widget.prof_collections = [_Collection("New selection")]
        workspace.trend_job.retry_button.click()
        assert pool.tasks == []
        assert workspace.trend_job.snapshot.state == "superseded"
    finally:
        workspace.shutdown()
        win.close()


def test_comparison_job_retries_saved_reference_keys_and_frozen_rows(qt_app):
    from sharpmod.profile_metrics import ProfileMetricsEngine

    collections = tuple(_VisualCollection(f"Saved {index}", dates=(RUN,)) for index in range(3))
    for index, collection in enumerate(collections):
        collection._profs[""][0].value = index + 1
        collection._mod_therm = [index == 1]
    calls = []

    def fast(profile):
        calls.append(profile.value)
        if profile.value == 2 and calls.count(2) == 1:
            raise RuntimeError("one failed row")
        return {"mlcape": profile.value}

    engine = ProfileMetricsEngine(fast_analyzer=fast)
    win, workspace, _engine = _make(qt_app, collections, engine)
    pool = _HeldAnalysisPool()
    workspace._pool, workspace._async_compute = pool, True
    workspace._comparison_metric_keys = ("mlcape",)
    try:
        workspace.compare_reference.setCurrentIndex(1)
        workspace.tabs.setCurrentIndex(workspace.TAB_COMPARE)
        collections[1]._meta["loc"] = "Unapplied replacement"
        collections[1]._mod_therm[0] = False
        collections[1]._profs[""] = [_visual_profile(99)]
        blocked = workspace.compare_reference.blockSignals(True)
        workspace.compare_reference.setCurrentIndex(0)
        workspace.compare_reference.blockSignals(blocked)
        workspace._comparison_metric_keys = ("mlcin",)
        pool.run_next()
        assert calls == [1, 2, 3]
        assert workspace.compare_job.snapshot.counts.completed == 2
        assert workspace.compare_job.snapshot.counts.failed == 1
        assert workspace._comparison_samples[1].context.profile_edited is True
        assert workspace.compare_table.horizontalHeaderItem(3).text().startswith("MLCAPE")
        workspace.compare_table.setCurrentCell(2, 3)
        workspace.compare_job.retry_button.click()
        pool.run_next()
        assert calls == [1, 2, 3, 2]
        assert workspace.compare_job.snapshot.counts.completed == 3
        assert workspace.compare_table.item(1, 2).text() == "Reference"
        assert workspace.compare_table.currentRow() == 2
        assert "Unapplied" not in workspace.compare_job.snapshot.affected_input
        assert workspace._comparison_samples[1].context.profile_edited is True
    finally:
        workspace.shutdown()
        win.close()


def test_comparison_cancel_keeps_prior_table_and_reuses_queued_rows(qt_app):
    from threading import Event
    from sharpmod.profile_metrics import ProfileMetricsEngine

    collections = (_VisualCollection("Reference", dates=(RUN,)), _VisualCollection("Other", dates=(RUN,)))
    engine = ProfileMetricsEngine(fast_analyzer=lambda _profile: {"mlcape": 10.0})
    win, workspace, _engine = _make(qt_app, collections, engine)
    workspace._comparison_metric_keys = ("mlcape",)
    workspace.tabs.setCurrentIndex(workspace.TAB_COMPARE)
    prior = workspace._comparison_samples
    entered, release = Event(), Event()
    calls = []

    def fast(_profile):
        calls.append(1)
        entered.set()
        assert release.wait(2.0)
        return {"mlcape": 20.0}

    engine._fast_analyzer = fast
    engine.clear()
    workspace._async_compute = True
    try:
        workspace._refresh_compare()
        assert entered.wait(2.0)
        task = workspace._tasks[("compare", workspace._generation["compare"])]
        release.set()
        assert task.done_event.wait(2.0)
        workspace._cancel_analysis("compare")
        qt_app.processEvents()
        assert workspace._comparison_samples is prior
        assert workspace.compare_table.item(0, 3).text() == "10"
        assert workspace.compare_job.snapshot.state == "cancelled"
        assert workspace.compare_job.snapshot.counts.completed == 2
        workspace.compare_job.retry_button.click()
        assert workspace._pool.waitForDone(2000)
        qt_app.processEvents()
        assert calls == [1, 1], "saved complete rows need no calculation on retry"
        assert workspace.compare_table.item(0, 3).text() == "20"
    finally:
        release.set()
        workspace.shutdown()
        workspace._pool.waitForDone(2000)
        win.close()


def test_custom_comparison_cancel_before_start_retries_one_saved_comparison(qt_app):
    engine = _Engine()
    engine.compare_result = (
        ComparisonSample(0, "Reference", RUN, RUN, "", {"mlcape": 10}, True, True),
        ComparisonSample(1, "Other", RUN, RUN, "", {"mlcape": 20}, True, True),
    )
    win, workspace, _engine = _make(qt_app, [_Collection("Reference"), _Collection("Other")], engine)
    pool = _HeldAnalysisPool()
    workspace._pool, workspace._async_compute = pool, True
    try:
        workspace.tabs.setCurrentIndex(workspace.TAB_COMPARE)
        workspace._cancel_analysis("compare")
        pool.run_next()
        assert engine.calls == []
        assert workspace.compare_job.snapshot.counts.cancelled == 1
        assert workspace.compare_job.snapshot.counts.requested == 1
        workspace.compare_job.retry_button.click()
        pool.run_next()
        assert len(engine.calls) == 1
        assert workspace.compare_job.snapshot.counts.completed == 1
        assert workspace.compare_job.snapshot.counts.requested == 1
        status = workspace.compare_status.text()
        workspace._accept_failure(workspace._generation["compare"], "compare", "duplicate stale failure")
        assert workspace.compare_status.text() == status
    finally:
        workspace.shutdown()
        win.close()


def test_comparison_partial_cancel_preserves_focus_selection_scroll_and_geometry(qt_app):
    from threading import Event
    from sharpmod.profile_metrics import ProfileMetricsEngine

    collections = tuple(_VisualCollection(f"Saved {index}", dates=(RUN,)) for index in range(4))
    for index, collection in enumerate(collections):
        collection._profs[""][0].value = index + 1
    collections[2]._profs[""][0] = None
    engine = ProfileMetricsEngine(fast_analyzer=lambda profile: {"mlcape": profile.value * 10})
    win, workspace, _engine = _make(qt_app, collections, engine)
    workspace._comparison_metric_keys = ("mlcape",)
    entered, release = Event(), Event()
    calls = []

    def fast(profile):
        calls.append(profile.value)
        if profile.value == 2:
            entered.set()
            assert release.wait(2.0)
        return {"mlcape": profile.value * 20}

    try:
        win.resize(1100, 800)
        win.show()
        workspace.open("compare")
        qt_app.processEvents()
        prior, charts = workspace._comparison_samples, workspace.visual_compare._collections
        workspace.compare_table.setCurrentCell(1, 3)
        workspace.compare_table.setFocus()
        qt_app.processEvents()
        pane_size = workspace.dock.size()
        splitter = workspace.compare_split.sizes()
        scroll = workspace.tabs.currentWidget().verticalScrollBar()
        scroll.setValue(min(10, scroll.maximum()))
        position = scroll.value()
        engine._fast_analyzer = fast
        engine.clear()
        workspace._async_compute = True
        workspace._refresh_compare()
        assert entered.wait(2.0)
        qt_app.processEvents()
        assert workspace.compare_job.snapshot.done == 1
        assert workspace.visual_compare._collections is charts
        workspace._cancel_analysis("compare")
        release.set()
        assert workspace._pool.waitForDone(2000)
        qt_app.processEvents()
        counts = workspace.compare_job.snapshot.counts
        assert (counts.completed, counts.cancelled, counts.unavailable, counts.requested) == (2, 1, 1, 4)
        assert workspace._comparison_samples is prior
        assert workspace.visual_compare._collections is charts
        assert workspace.compare_table.currentRow() == 1
        assert QApplication.focusWidget() is workspace.compare_table
        assert workspace.dock.size() == pane_size
        sizes = workspace.compare_split.sizes()
        assert sizes[0] / sum(sizes) == pytest.approx(
            splitter[0] / sum(splitter), abs=1 / min(sum(sizes), sum(splitter)),
        )
        assert scroll.value() == min(position, scroll.maximum())
        workspace.compare_job.retry_button.click()
        assert workspace._pool.waitForDone(2000)
        qt_app.processEvents()
        assert calls == [1, 2, 4]
        assert workspace.compare_job.snapshot.counts.completed == 3
        assert workspace.compare_table.currentRow() == 1
        assert QApplication.focusWidget() is workspace.compare_table
        assert workspace.dock.size() == pane_size
        sizes = workspace.compare_split.sizes()
        assert sizes[0] / sum(sizes) == pytest.approx(
            splitter[0] / sum(splitter), abs=1 / min(sum(sizes), sum(splitter)),
        )
    finally:
        release.set()
        workspace.shutdown()
        workspace._pool.waitForDone(2000)
        win.close()


def test_replaced_collections_reject_late_comparison_and_saved_retry(qt_app):
    engine = _Engine()
    engine.compare_result = (
        ComparisonSample(0, "Original", RUN, RUN, "", {"mlcape": 10}, True, True),
        ComparisonSample(1, "Other", RUN, RUN, "", {"mlcape": 20}, True, True),
    )
    win, workspace, _engine = _make(qt_app, [_Collection("Original"), _Collection("Other")], engine)
    pool = _HeldAnalysisPool()
    workspace._pool, workspace._async_compute = pool, True
    try:
        workspace.tabs.setCurrentIndex(workspace.TAB_COMPARE)
        task = pool.tasks.pop()
        win.spc_widget.prof_collections = [_Collection("Replacement"), _Collection("Other")]
        task.run()
        assert workspace._comparison_samples == ()
        assert workspace.compare_job.snapshot.state == "superseded"
        workspace._refresh_compare()
        workspace._cancel_analysis("compare")
        pool.run_next()
        win.spc_widget.prof_collections = [_Collection("New selection"), _Collection("Other")]
        workspace.compare_job.retry_button.click()
        assert pool.tasks == []
        assert workspace.compare_job.snapshot.state == "superseded"
    finally:
        workspace.shutdown()
        win.close()


def test_comparison_job_fits_enlarged_realized_narrow_viewport(qt_app):
    gui_theme.apply_theme(qt_app, text_scale=200)
    win, workspace, _engine = _make(qt_app, [_Collection("Saved reference"), _Collection("Saved other")])
    try:
        win.resize(1100, 800)
        win.show()
        workspace.open("compare")
        scroll = workspace.tabs.widget(workspace.TAB_COMPARE)
        scroll.setFixedSize(560, 650)
        qt_app.processEvents()
        assert scroll.viewport().width() > 500
        assert scroll.horizontalScrollBar().maximum() == 0
    finally:
        workspace.shutdown()
        win.close()
        gui_theme.apply_theme(qt_app, text_scale=100)


def test_trend_job_fits_enlarged_narrow_scroll_viewport(qt_app):
    gui_theme.apply_theme(qt_app, text_scale=200)
    engine = _Engine()
    engine.timeline_result = _trend_samples()
    win, workspace, _engine = _make(qt_app, [_Collection("Retained run")], engine)
    try:
        win.resize(1100, 800)
        win.show()
        workspace.open("trends")
        scroll = workspace.tabs.widget(workspace.TAB_TRENDS)
        scroll.setFixedSize(560, 650)
        scroll.show()
        qt_app.processEvents()
        assert scroll.viewport().width() > 500, "measure a realized viewport, not a hidden ancestor"
        assert scroll.horizontalScrollBar().maximum() == 0
    finally:
        workspace.shutdown()
        win.close()
        gui_theme.apply_theme(qt_app, text_scale=100)


def test_ensemble_job_retries_frozen_members_time_and_exports_displayed_ledger(qt_app, monkeypatch, tmp_path):
    import csv
    from sharpmod.ensemble_members import EnsembleAcquisition, MemberFailure
    from sharpmod.profile_metrics import ProfileMetricsEngine

    source = _VisualCollection("Saved ensemble", dates=(RUN, RUN + timedelta(hours=1)))
    source._profs = {"c00": [_visual_profile(1)], "p01": [_visual_profile(2)],
                     "p02": [], "p03": [_visual_profile(4)]}
    source._highlight = "c00"
    ledger = EnsembleAcquisition(("c00", "p01", "p02", "p03", "p04"),
                                 ("c00", "p01", "p02", "p03"),
                                 (MemberFailure("p04", "failed", "download failed"),))
    source._meta.update(ledger.to_metadata())
    calls, failed = [], False

    def analyze(profile):
        nonlocal failed
        value = profile.tmpc[0]
        calls.append(value)
        if value == 26 and not failed:
            failed = True
            raise RuntimeError("transient ensemble diagnostic")
        return {"mlcape": value}

    win, workspace, _engine = _make(qt_app, [source], ProfileMetricsEngine(fast_analyzer=analyze))
    monkeypatch.setattr(workspace.threshold_explorer, "refresh", lambda _source: None)
    pool = _HeldAnalysisPool()
    workspace._pool, workspace._async_compute = pool, True
    try:
        workspace.tabs.setCurrentIndex(workspace.TAB_ENSEMBLE)
        assert workspace.ensemble_job.snapshot.total == 5
        source._profs["p01"][0] = _visual_profile(20)
        source._prof_idx = 1
        source._meta["loc"] = "Edited source"
        source._meta["ensemble_requested_members"].append("p99")
        pool.run_next()
        counts = workspace.ensemble_job.snapshot.counts
        assert (counts.completed, counts.failed, counts.unavailable, counts.requested) == (2, 1, 2, 5)
        assert workspace._ensemble_summary.valid_time == RUN
        assert calls == [25, 26, 28]
        assert "Saved ensemble" in workspace.ensemble_job.snapshot.retained
        workspace.ensemble_table.setCurrentCell(2, 3)
        workspace.ensemble_job.retry_button.click()
        pool.run_next()
        assert calls == [25, 26, 28, 26], "retry must not revisit complete diagnostics or edited inputs"
        assert workspace.ensemble_job.snapshot.counts.completed == 3
        assert workspace._ensemble_summary.scalar["mlcape"].median == 26
        assert workspace.ensemble_table.currentRow() == 2
        path = tmp_path / "saved-ensemble.csv"
        monkeypatch.setattr("sharpmod.gui_analysis_workspace.QFileDialog.getSaveFileName",
                            lambda *_args: (str(path), ""))
        workspace._choose_ensemble_export()
        with path.open(encoding="utf-8", newline="") as stream:
            members = {row["member"] for row in csv.DictReader(stream) if row["record_type"] == "member"}
        assert members == set(ledger.requested_members), "CSV must describe the displayed saved summary"
    finally:
        workspace.shutdown()
        win.close()


def test_ensemble_cancel_rejects_queued_native_success_and_reuses_it(qt_app, monkeypatch):
    from threading import Event
    from sharpmod.profile_metrics import ProfileMetricsEngine

    source = _VisualCollection("Saved ensemble")
    source._profs, source._highlight = {"c00": [_visual_profile(1)], "p01": [_visual_profile(2)]}, "c00"
    calls, started, release = [], Event(), Event()
    win, workspace, _engine = _make(qt_app, [source], ProfileMetricsEngine(
        fast_analyzer=lambda profile: {"mlcape": profile.tmpc[0]},
    ))
    monkeypatch.setattr(workspace.threshold_explorer, "refresh", lambda _source: None)
    try:
        workspace.tabs.setCurrentIndex(workspace.TAB_ENSEMBLE)
        prior = workspace._ensemble_summary
        temperatures = workspace.ensemble_chart._temperature

        def analyze(profile):
            calls.append(profile.tmpc[0])
            started.set()
            assert release.wait(2.0)
            return {"mlcape": profile.tmpc[0] + 10}

        workspace.engine = ProfileMetricsEngine(fast_analyzer=analyze)
        workspace._async_compute = True
        workspace._refresh_ensemble()
        assert started.wait(2.0)
        task = workspace._tasks[("ensemble", workspace._generation["ensemble"])]
        release.set()
        assert task.done_event.wait(2.0)
        workspace._cancel_analysis("ensemble")
        qt_app.processEvents()
        assert workspace._ensemble_summary is prior
        assert workspace.ensemble_chart._temperature is temperatures
        assert workspace.ensemble_job.snapshot.state == "cancelled"
        assert workspace.ensemble_job.snapshot.counts.completed == 2
        workspace.ensemble_job.retry_button.click()
        assert workspace._pool.waitForDone(2000)
        qt_app.processEvents()
        assert calls == [25, 26]
        assert workspace._ensemble_summary.scalar["mlcape"].median == 35.5
    finally:
        release.set()
        workspace.shutdown()
        workspace._pool.waitForDone(2000)
        win.close()


def test_ensemble_chart_update_keeps_focused_pressure(qt_app):
    from qtpy.QtTest import QTest
    from sharpmod.gui_analysis_workspace import _EnvelopeChart

    chart = _EnvelopeChart()
    distribution = MetricDistribution(3, 10, 15, 20, 5, 25)
    bands = tuple(ThermodynamicBand(pressure, distribution, distribution) for pressure in (1000, 900, 800))
    try:
        chart.set_band(bands)
        chart.show()
        chart.setFocus()
        QTest.keyClick(chart, Qt.Key_End)
        chart.set_band(bands)
        assert chart._level_index == 2
        assert "Focused level: 800 hPa" in chart.accessibleDescription()
    finally:
        chart.close()


def test_ensemble_partial_cancel_keeps_prior_context_and_retries_one_member(qt_app, monkeypatch):
    from threading import Event
    from qtpy.QtTest import QTest
    from sharpmod.profile_metrics import ProfileMetricsEngine

    source = _VisualCollection("Saved ensemble")
    source._profs = {"c00": [_visual_profile(1)], "p01": [_visual_profile(2)],
                     "missing": [], "p03": [_visual_profile(4)]}
    source._highlight = "c00"
    calls, started, release = [], Event(), Event()
    win, workspace, _engine = _make(qt_app, [source], ProfileMetricsEngine(
        fast_analyzer=lambda profile: {"mlcape": profile.tmpc[0]},
    ))
    monkeypatch.setattr(workspace.threshold_explorer, "refresh", lambda _source: None)
    win.setCentralWidget(QWidget())
    win.resize(1000, 1000)
    win.show()
    try:
        workspace.open("ensemble")
        win.resizeDocks([workspace.dock], [560], Qt.Horizontal)
        qt_app.processEvents()
        workspace.ensemble_table.setCurrentCell(2, 3)
        chart = workspace.ensemble_chart
        chart.setFocus()
        QTest.keyClick(chart, Qt.Key_End)
        pressure = chart._pressures[chart._level_index]
        splitter = workspace.ensemble_split.sizes()
        prior, temperatures = workspace._ensemble_summary, chart._temperature
        dock_size = workspace.dock.size()
        scroll = workspace.tabs.currentWidget()
        scroll.verticalScrollBar().setValue(50)
        scroll_value = scroll.verticalScrollBar().value()

        def analyze(profile):
            calls.append(profile.tmpc[0])
            if len(calls) == 2:
                started.set()
                assert release.wait(2.0)
            return {"mlcape": profile.tmpc[0] + 10}

        workspace.engine = ProfileMetricsEngine(fast_analyzer=analyze)
        workspace._async_compute = True
        workspace._refresh_ensemble()
        assert started.wait(2.0)
        workspace._cancel_analysis("ensemble")
        release.set()
        assert workspace._pool.waitForDone(2000)
        qt_app.processEvents()
        counts = workspace.ensemble_job.snapshot.counts
        assert (counts.completed, counts.cancelled, counts.unavailable) == (2, 1, 1)
        assert workspace._ensemble_summary is prior and chart._temperature is temperatures
        assert chart._pressures[chart._level_index] == pressure
        assert workspace.ensemble_table.currentRow() == 2
        assert QApplication.focusWidget() is chart
        assert workspace.dock.size() == dock_size
        assert scroll.verticalScrollBar().value() == scroll_value
        workspace.ensemble_job.retry_button.click()
        assert workspace._pool.waitForDone(2000)
        qt_app.processEvents()
        assert calls == [25, 26, 28]
        assert workspace.ensemble_job.snapshot.counts.completed == 3
        assert chart._pressures[chart._level_index] == pressure
        assert workspace.ensemble_table.currentRow() == 2 and workspace.ensemble_table.currentColumn() == 3
        assert QApplication.focusWidget() is chart
        assert workspace.dock.size() == dock_size
        sizes = workspace.ensemble_split.sizes()
        assert sizes[0] / sum(sizes) == pytest.approx(
            splitter[0] / sum(splitter), abs=1 / min(sum(sizes), sum(splitter)),
        )
    finally:
        release.set()
        workspace.shutdown()
        workspace._pool.waitForDone(2000)
        win.close()


def test_ensemble_owner_rejects_replaced_focus_and_saved_retry(qt_app, monkeypatch):
    from sharpmod.profile_metrics import ProfileMetricsEngine

    sources = [_VisualCollection("First"), _VisualCollection("Second")]
    for source in sources:
        source._profs, source._highlight = {"c00": [_visual_profile()]}, "c00"
    win, workspace, _engine = _make(qt_app, sources, ProfileMetricsEngine(
        fast_analyzer=lambda _profile: {"mlcape": 10},
    ))
    monkeypatch.setattr(workspace.threshold_explorer, "refresh", lambda _source: None)
    pool = _HeldAnalysisPool()
    workspace._pool, workspace._async_compute = pool, True
    try:
        workspace.tabs.setCurrentIndex(workspace.TAB_ENSEMBLE)
        win.spc_widget.pc_idx = 1
        pool.run_next()
        assert workspace._ensemble_summary is None
        assert workspace.ensemble_job.snapshot.state == "superseded"
        workspace._refresh_ensemble()
        workspace._cancel_analysis("ensemble")
        pool.run_next()
        win.spc_widget.pc_idx = 0
        workspace.ensemble_job.retry_button.click()
        assert not pool.tasks
        assert workspace.ensemble_job.snapshot.state == "superseded"
        workspace._refresh_ensemble()
        late_task = pool.tasks[0]
        win.spc_widget.prof_collections = []
        workspace._refresh_ensemble()
        assert not pool.tasks, "empty input removes queued work without waiting"
        late_task.run()
        assert workspace._ensemble_summary is None
        assert workspace._ensemble_request is None
    finally:
        workspace.shutdown()
        win.close()


def test_custom_ensemble_cancel_before_start_retries_one_summary(qt_app, monkeypatch):
    engine = _Engine()
    engine.ensemble_result = EnsembleSummary(RUN, 31, 24, ("c00",), (), {}, (), True)
    win, workspace, _engine = _make(qt_app, [_Collection("Custom ensemble")], engine)
    monkeypatch.setattr(workspace.threshold_explorer, "refresh", lambda _source: None)
    pool = _HeldAnalysisPool()
    workspace._pool, workspace._async_compute = pool, True
    try:
        workspace.tabs.setCurrentIndex(workspace.TAB_ENSEMBLE)
        workspace._cancel_analysis("ensemble")
        pool.run_next()
        assert engine.calls == []
        assert workspace.ensemble_job.snapshot.counts.cancelled == 1
        assert workspace.ensemble_job.snapshot.counts.requested == 1
        workspace.ensemble_job.retry_button.click()
        pool.run_next()
        assert len(engine.calls) == 1
        assert workspace.ensemble_job.snapshot.counts.completed == 1
        assert workspace.ensemble_job.snapshot.counts.requested == 1
        status = workspace.ensemble_status.text()
        workspace._accept_failure(workspace._generation["ensemble"], "ensemble", "duplicate terminal failure")
        assert workspace.ensemble_status.text() == status
    finally:
        workspace.shutdown()
        win.close()


def test_ensemble_job_fits_enlarged_realized_narrow_viewport(qt_app, monkeypatch):
    gui_theme.apply_theme(qt_app, text_scale=200)
    engine = _Engine()
    engine.ensemble_result = EnsembleSummary(RUN, 31, 24, ("c00",), ("p04",), {}, (), True)
    win, workspace, _engine = _make(qt_app, [_Collection("Long ensemble source " * 4)], engine)
    monkeypatch.setattr(workspace.threshold_explorer, "refresh", lambda _source: None)
    win.setCentralWidget(QWidget())
    win.resize(1000, 1000)
    win.show()
    try:
        workspace.open("ensemble")
        win.resizeDocks([workspace.dock], [560], Qt.Horizontal)
        qt_app.processEvents()
        scroll = workspace.tabs.currentWidget()
        assert scroll.viewport().width() > 500
        assert scroll.horizontalScrollBar().maximum() == 0
        table = workspace.ensemble_table
        assert table.columnWidth(0) >= table.fontMetrics().horizontalAdvance(table.item(0, 0).text())
    finally:
        workspace.shutdown()
        win.close()
        gui_theme.apply_theme(qt_app, text_scale=100)


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
        assert len(
            {
                workspace.trend_chart._series_style(index)
                for index in range(len(series))
            }
        ) == 3, "series identity must also have a non-colour line pattern"
        # Flattened in series-then-sample order: 3 soundings x 3 hours.
        assert len(workspace.trend_chart._prepared) == 9
        assert "across 3 soundings" in workspace.trend_status.text()
        description = workspace.trend_chart.accessibleDescription()
        assert "8 available point(s)" in description
        assert "1 missing point(s)" in description
        assert "distinct line patterns" in description
    finally:
        win.close()


def test_chart_text_scale_changes_annotations_without_changing_chart_zoom(qt_app):
    engine = _Engine()
    engine.timeline_result = _trend_samples()
    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")], engine)
    try:
        workspace.open("Trends")
        chart = workspace.trend_chart
        chart.resize(640, 320)
        original_bounds = chart._x_bounds, chart._y_bounds
        original_points = chart._prepared

        gui_theme.apply_theme(
            qt_app, color_style="standard", text_scale=200
        )
        qt_app.processEvents()
        assert not chart.grab().isNull()
        assert chart._label_font.pointSizeF() == pytest.approx(17.0)
        assert (chart._x_bounds, chart._y_bounds) == original_bounds
        assert chart._prepared == original_points
    finally:
        gui_theme.apply_theme(qt_app, color_style="standard")
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
        workspace.trend_chart._highlight(3)
        assert "Focused point: KOUN \u00b7 RAP" in (
            workspace.trend_chart.accessibleDescription()
        )
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


# --- T06.1 grouped, space-adaptive analysis navigation --------------------- #


def test_tab_registry_agrees_with_the_index_constants(qt_app):
    """The registry, the TAB_* constants, and the built order are one thing."""
    from sharpmod.gui_analysis_workspace import TAB_GROUPS, TAB_SPECS

    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")])
    try:
        assert workspace.tabs.count() == len(TAB_SPECS)
        constants = {
            "trends": workspace.TAB_TRENDS,
            "compare": workspace.TAB_COMPARE,
            "ensemble": workspace.TAB_ENSEMBLE,
            "animation": workspace.TAB_ANIMATION,
        }
        assert set(constants) == {spec.key for spec in TAB_SPECS}
        for key, index in constants.items():
            assert TAB_SPECS[index].key == key
            assert workspace.tabs.tabText(index) == TAB_SPECS[index].label
            assert workspace.tab_key(index) == key

        # Every page is grouped, labelled, and explained; nothing is left blank.
        assert len({spec.key for spec in TAB_SPECS}) == len(TAB_SPECS)
        assert len({spec.label for spec in TAB_SPECS}) == len(TAB_SPECS)
        for index, spec in enumerate(TAB_SPECS):
            assert spec.group in TAB_GROUPS
            assert spec.label.strip() and spec.tooltip.strip()
            tip = workspace.tabs.tabToolTip(index)
            assert spec.group in tip and spec.tooltip in tip

        # Groups are contiguous in tab order, so the bar reads as groups too.
        order = [spec.group for spec in TAB_SPECS]
        assert order == sorted(order, key=TAB_GROUPS.index)
    finally:
        win.close()


def test_navigator_groups_pages_and_headings_are_not_destinations(qt_app):
    from sharpmod.gui_analysis_workspace import TAB_GROUPS, TAB_SPECS

    win, workspace, engine = _make(qt_app, [_Collection("KOUN")])
    try:
        navigator = workspace.navigator
        headings = [
            position
            for position in range(navigator.count())
            if navigator.itemData(position) is None
        ]
        assert [navigator.itemText(position) for position in headings] == list(TAB_GROUPS)
        model = navigator.model()
        for position in headings:
            # A group name says what the pages below have in common; it is not
            # somewhere the reader can navigate to.
            assert not model.item(position).flags() & Qt.ItemIsSelectable
            assert not model.item(position).flags() & Qt.ItemIsEnabled

        entries = [
            position
            for position in range(navigator.count())
            if navigator.itemData(position) is not None
        ]
        assert len(entries) == len(TAB_SPECS)
        assert [navigator.itemData(position) for position in entries] == list(
            range(len(TAB_SPECS))
        )
        for position in entries:
            index = navigator.itemData(position)
            # The chooser reads exactly like the tab it selects.
            assert navigator.itemText(position) == TAB_SPECS[index].label
            assert TAB_SPECS[index].tooltip in model.item(position).toolTip()

        # Choosing a section moves the workspace and computes only that page.
        engine.calls.clear()
        target = next(
            position
            for position in entries
            if navigator.itemData(position) == workspace.TAB_COMPARE
        )
        navigator.setCurrentIndex(target)
        navigator.activated.emit(target)
        qt_app.processEvents()
        assert workspace.tabs.currentIndex() == workspace.TAB_COMPARE
        assert "timeline" not in engine.calls

        # Selecting a tab directly keeps the chooser in step.
        workspace.tabs.setCurrentIndex(workspace.TAB_ANIMATION)
        qt_app.processEvents()
        assert navigator.itemData(navigator.currentIndex()) == workspace.TAB_ANIMATION
    finally:
        win.close()


def test_open_resolves_a_stable_key_or_a_label_and_ignores_unknown(qt_app):
    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")])
    try:
        assert workspace.tab_index_for("  TRENDS ") == workspace.TAB_TRENDS
        assert workspace.tab_index_for("animation") == workspace.TAB_ANIMATION
        assert workspace.tab_index_for("Animation") == workspace.TAB_ANIMATION
        assert workspace.tab_index_for("share") == workspace.TAB_ANIMATION
        assert workspace.tab_index_for("Report") is None
        assert workspace.tab_index_for("nonexistent") is None

        state = workspace.session_state()
        assert "animation" in state
        assert "briefing" not in state

        workspace.open("Animation")
        assert workspace.tabs.currentIndex() == workspace.TAB_ANIMATION

        # An unknown name must not silently move the reader somewhere else.
        workspace.open("no such section")
        assert workspace.tabs.currentIndex() == workspace.TAB_ANIMATION
    finally:
        win.close()


def test_narrow_dock_replaces_the_tab_bar_with_the_grouped_chooser(qt_app):
    """The page chooser appears when labels cannot fit a side dock."""
    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")])
    try:
        workspace.show()
        needed = workspace.tabs.tabBar().sizeHint().width()
        assert needed > 0

        def _resize(width):
            """Resize and deliver the event Qt would deliver when shown."""
            before = workspace.size()
            workspace.resize(width, 600)
            QApplication.sendEvent(
                workspace, QResizeEvent(workspace.size(), before)
            )
            qt_app.processEvents()

        # The dock starts hidden, so the explicit hidden flag is the honest
        # signal here rather than isVisible().
        _resize(max(240, needed // 3))
        assert not workspace._tab_bar_fits()
        assert workspace.tabs.tabBar().isHidden()
        assert not workspace.navigator_row.isHidden()
        # Every page is still reachable in one step, not behind scroll arrows.
        assert workspace.navigator.count() >= workspace.tabs.count()

        _resize(needed + 200)
        assert workspace._tab_bar_fits()
        assert not workspace.tabs.tabBar().isHidden()
        assert workspace.navigator_row.isHidden()

        # Labels are never abbreviated when the bar is the navigation.
        assert workspace.tabs.tabBar().elideMode() == Qt.ElideNone

        # The tab bar must never pin the dock open at the width of all
        # labels; a side dock has to be narrowable to a usable reading width.
        assert workspace.minimumSizeHint().width() < needed
        assert workspace.tabs.minimumSizeHint().width() < needed

        # The decision is taken from the width actually available each time, so
        # showing the dock at its own width re-decides rather than reusing a
        # value cached at construction.
        assert not workspace._tab_bar_fits(max(240, needed // 3))
        assert workspace._tab_bar_fits(needed + 200)
        workspace._apply_navigation_layout(max(240, needed // 3))
        assert workspace.tabs.tabBar().isHidden()
        assert not workspace.navigator_row.isHidden()
        workspace._apply_navigation_layout(needed + 200)
        assert not workspace.tabs.tabBar().isHidden()
        assert workspace.navigator_row.isHidden()
    finally:
        win.close()


def test_narrow_enlarged_ensemble_page_has_no_outer_horizontal_scroll(qt_app):
    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")])
    try:
        gui_theme.apply_theme(qt_app, color_style="standard", text_scale=200)
        win.resize(1000, 650)
        win.show()
        workspace.resize(560, 600)
        workspace.open("ensemble")
        qt_app.processEvents()

        page = workspace.tabs.currentWidget()
        assert page.horizontalScrollBar().maximum() == 0
        assert workspace.ensemble_refresh.isVisible()
        assert workspace.ensemble_retry.isVisible()
        assert workspace.ensemble_export.isVisible()
    finally:
        gui_theme.apply_theme(qt_app, color_style="standard")
        win.close()


def test_legacy_session_tab_index_restores_the_page_it_named(qt_app):
    """Old session indexes map retained pages and skip removed pages."""

    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")])
    try:
        for legacy_index, key in {0: "trends", 1: "compare", 2: "ensemble", 7: "animation"}.items():
            workspace.restore_session_state({"version": 1, "tab": legacy_index})
            assert workspace.tab_key() == key

        workspace.restore_session_state({"tab": 7})
        assert workspace.tab_key() == "animation"

        # Version 2 files persisted the former page key, not only the index.
        workspace.restore_session_state({"version": 2, "tab_key": "share"})
        assert workspace.tab_key() == "animation"
        assert workspace.session_state()["tab_key"] == "animation"

        # A removed page opens the first remaining page; a valid key wins.
        workspace.restore_session_state({"version": 2, "tab": 7})
        assert workspace.tab_key() == "trends"
        workspace.restore_session_state(
            {"version": 2, "tab": 7, "tab_key": "ensemble"}
        )
        assert workspace.tab_key() == "ensemble"
        workspace.restore_session_state({"version": 2, "tab": 8})
        assert workspace.tab_key() == "animation"

        # Malformed values fall back to the first page rather than raising.
        for broken in ({"version": 1, "tab": 99}, {"version": 1, "tab": "invalid"},
                       {"version": 2, "tab_key": "gone"}):
            workspace.restore_session_state({"tab_key": "trends"})
            workspace.restore_session_state(broken)
            assert 0 <= workspace.tabs.currentIndex() < workspace.tabs.count()
    finally:
        win.close()


def test_dock_toggle_tooltip_names_every_group_and_page(qt_app):
    from sharpmod.gui_analysis_workspace import TAB_GROUPS, TAB_SPECS

    win, workspace, _engine = _make(qt_app, [_Collection("KOUN")])
    try:
        tooltip = workspace.dock.toggleViewAction().toolTip()
        for group in TAB_GROUPS:
            assert group in tooltip
        for spec in TAB_SPECS:
            assert spec.label in tooltip
    finally:
        win.close()


# --- T06.2 reversible analysis presentation ------------------------------- #


def _make_composed_window(qt_app, engine, *, settings=None):
    """Give the analysis dock the same central/sibling shape as a viewer."""
    win = _Window([_Collection("KOUN"), _Collection("KTLX")])
    central = QWidget(win)
    central.setObjectName("soundingCanvas")
    central.setMinimumSize(620, 420)
    win.setCentralWidget(central)

    sidebar = QDockWidget("Soundings", win)
    sidebar.setObjectName("soundingSidebarDock")
    sidebar.setWidget(QWidget(sidebar))
    win.addDockWidget(Qt.RightDockWidgetArea, sidebar)
    win._sharpmod_sidebar_dock = sidebar

    workspace = install_analysis_workspace(
        win, engine=engine, async_compute=False, settings=settings
    )
    win.resize(1500, 850)
    win.show()
    qt_app.processEvents()
    return win, workspace, central, sidebar


def test_focus_mode_uses_main_workspace_and_preserves_analysis_context(qt_app):
    """Expanding changes presentation, never the sounding or analysis model."""
    engine = _Engine()
    win, workspace, central, sidebar = _make_composed_window(qt_app, engine)
    collections = win.spc_widget.prof_collections
    history = object()
    interaction = object()
    win.spc_widget._sharpmod_history = history
    win.spc_widget._sharpmod_interaction_mode = interaction
    win._sharpmod_collection_state = SimpleNamespace(reference_id="profile-1")
    try:
        workspace.open("Animation")
        qt_app.processEvents()
        calls_before = tuple(engine.calls)
        needed = workspace.tabs.tabBar().sizeHint().width()

        workspace.set_expanded(True)
        qt_app.processEvents()

        assert workspace.presentation_mode() == "expanded"
        assert workspace.expand_action.isChecked()
        assert not workspace.separate_window_action.isChecked()
        assert not workspace.dock.isFloating()
        assert central.isHidden()
        assert sidebar.isHidden()
        assert not workspace.dock.isHidden()
        assert workspace.width() >= needed
        assert not workspace.tabs.tabBar().isHidden()
        assert workspace.navigator_row.isHidden()
        assert win.spc_widget.prof_collections is collections
        assert win._sharpmod_collection_state.reference_id == "profile-1"
        assert win.spc_widget._sharpmod_history is history
        assert win.spc_widget._sharpmod_interaction_mode is interaction
        assert workspace.tab_key() == "animation"
        assert tuple(engine.calls) == calls_before

        workspace.set_expanded(False)
        qt_app.processEvents()

        assert workspace.presentation_mode() == "docked"
        assert not central.isHidden()
        assert not sidebar.isHidden()
        assert workspace.tab_key() == "animation"
        assert win.spc_widget.prof_collections is collections
        assert tuple(engine.calls) == calls_before
    finally:
        win.close()


def test_separate_window_is_explicit_reversible_and_lazy(qt_app):
    """The same dock becomes a top-level window suitable for another screen."""
    engine = _Engine()
    win, workspace, central, sidebar = _make_composed_window(qt_app, engine)
    collections = win.spc_widget.prof_collections
    win._sharpmod_collection_state = SimpleNamespace(reference_id="profile-0")
    try:
        workspace.open("Animation")
        qt_app.processEvents()
        calls_before = tuple(engine.calls)

        assert workspace.dock.features() & QDockWidget.DockWidgetFloatable
        assert workspace.dock.features() & QDockWidget.DockWidgetMovable
        assert workspace.expand_action in win._sharpmod_view_menu.actions()
        assert workspace.separate_window_action in win._sharpmod_view_menu.actions()
        assert workspace.layout_button.text() == "Layout"
        assert workspace.layout_button.accessibleName() == "Analysis layout"
        assert workspace.layout_button.width() >= (
            workspace.layout_button.fontMetrics().horizontalAdvance("Layout")
        )
        assert workspace.layout_button.menu().actions()[:2] == [
            workspace.expand_action,
            workspace.separate_window_action,
        ]

        workspace.set_separate_window(True)
        qt_app.processEvents()

        assert workspace.presentation_mode() == "separate"
        assert workspace.dock.isFloating()
        assert workspace.dock.isWindow()
        assert workspace.separate_window_action.isChecked()
        assert not workspace.expand_action.isChecked()
        assert not central.isHidden()
        assert not sidebar.isHidden()
        assert workspace.tab_key() == "animation"
        assert win.spc_widget.prof_collections is collections
        assert win._sharpmod_collection_state.reference_id == "profile-0"
        assert tuple(engine.calls) == calls_before

        workspace.set_separate_window(False)
        qt_app.processEvents()

        assert workspace.presentation_mode() == "docked"
        assert not workspace.dock.isFloating()
        assert not workspace.separate_window_action.isChecked()
        assert workspace.tab_key() == "animation"
        assert tuple(engine.calls) == calls_before
    finally:
        win.close()


def test_native_dock_changes_and_close_keep_presentation_actions_truthful(qt_app):
    """Dragging or closing the dock cannot strand the hidden sounding UI."""
    engine = _Engine()
    win, workspace, central, sidebar = _make_composed_window(qt_app, engine)
    try:
        workspace.open("Animation")
        workspace.dock.setFloating(True)
        qt_app.processEvents()
        assert workspace.presentation_mode() == "separate"
        assert workspace.separate_window_action.isChecked()

        workspace.dock.setFloating(False)
        qt_app.processEvents()
        assert workspace.presentation_mode() == "docked"
        assert not workspace.separate_window_action.isChecked()

        workspace.set_expanded(True)
        qt_app.processEvents()
        assert central.isHidden() and sidebar.isHidden()
        workspace.dock.close()
        qt_app.processEvents()

        assert workspace.presentation_mode() == "docked"
        assert workspace.dock.isHidden()
        assert not workspace.expand_action.isChecked()
        assert not central.isHidden()
        assert not sidebar.isHidden()
    finally:
        win.close()


def test_named_layout_save_restore_and_reset_preserve_context(qt_app, tmp_path):
    """A layout changes chrome only and the reset returns to the install state."""
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    engine = _Engine()
    win, workspace, central, sidebar = _make_composed_window(
        qt_app, engine, settings=settings
    )
    collections = win.spc_widget.prof_collections
    history = object()
    interaction = object()
    win.spc_widget._sharpmod_history = history
    win.spc_widget._sharpmod_interaction_mode = interaction
    win._sharpmod_collection_state = SimpleNamespace(reference_id="profile-1")
    try:
        controller = workspace.layout_controller
        assert controller is not None
        workspace.open("Animation")
        sidebar.hide()
        workspace.set_separate_window(True)
        workspace.dock.setGeometry(120, 90, 900, 650)
        qt_app.processEvents()
        calls_before = tuple(engine.calls)

        assert controller.save_named_layout("Dual monitor") == "Dual monitor"
        assert controller.layout_names() == ("Dual monitor",)

        workspace.set_separate_window(False)
        sidebar.show()
        win.resizeDocks([workspace.dock], [360], Qt.Horizontal)
        qt_app.processEvents()
        assert workspace.presentation_mode() == "docked"
        assert not sidebar.isHidden()

        assert controller.apply_named_layout("dual monitor")
        qt_app.processEvents()

        assert workspace.presentation_mode() == "separate"
        assert sidebar.isHidden()
        assert workspace.tab_key() == "animation"
        assert win.spc_widget.prof_collections is collections
        assert win._sharpmod_collection_state.reference_id == "profile-1"
        assert win.spc_widget._sharpmod_history is history
        assert win.spc_widget._sharpmod_interaction_mode is interaction
        assert tuple(engine.calls) == calls_before

        controller.rebuild_saved_menu()
        assert "Dual monitor" in [
            action.text() for action in controller.saved_menu.actions()
        ]
        assert controller.save_action in workspace.layout_menu.actions()
        assert controller.reset_action in workspace.layout_menu.actions()

        controller.reset_layout()
        qt_app.processEvents()

        assert workspace.presentation_mode() == "docked"
        assert workspace.dock.isHidden()
        assert not sidebar.isHidden()
        assert not central.isHidden()
        # Some Qt styles omit a hidden sibling from tabifiedDockWidgets; make
        # it realizable before checking the relationship, then return it to
        # the reset layout's hidden state.
        workspace.dock.show()
        qt_app.processEvents()
        assert sidebar in win.tabifiedDockWidgets(workspace.dock)
        workspace.dock.hide()
        assert workspace.tab_key() == "animation"
        assert controller.layout_names() == ("Dual monitor",)
        assert tuple(engine.calls) == calls_before

        # Saving focus mode records the visibility it will restore, not the
        # temporary all-hidden chrome that makes room for analysis.
        workspace.set_expanded(True)
        assert controller.save_named_layout("Analysis focus") == "Analysis focus"
        workspace.set_expanded(False)
        sidebar.hide()

        assert controller.apply_named_layout("Analysis focus")
        qt_app.processEvents()
        assert workspace.presentation_mode() == "expanded"
        assert central.isHidden() and sidebar.isHidden()
        workspace.set_expanded(False)
        qt_app.processEvents()
        assert not central.isHidden()
        assert not sidebar.isHidden()
        assert workspace.tab_key() == "animation"
        assert win.spc_widget.prof_collections is collections
        assert tuple(engine.calls) == calls_before
    finally:
        win.close()


# --- T07.2 separated difference axes -------------------------------------- #


def _visual_pair(qt_app, candidate_profile, *, reference_profile=None):
    """Build a two-slot comparison widget around one candidate profile."""
    reference_profile = reference_profile or _visual_profile()
    collections = (
        _VisualCollection("Reference", profiles=(reference_profile,)),
        _VisualCollection("Candidate", profiles=(candidate_profile,)),
    )
    widget = VisualComparisonWidget()
    widget.resize(1_000, 540)
    widget.set_collections(
        collections, 0, RUN, profile_ids=("reference", "candidate")
    )
    widget.show()
    qt_app.processEvents()
    return widget


def test_difference_scales_are_independent_in_both_directions(qt_app):
    """A large change in one unit family must not compress the other."""
    reference_profile = _visual_profile()
    # Small wind change, large thermodynamic change: the mirror image of the
    # case the shared span used to flatten.
    candidate_profile = SimpleNamespace(
        hght=list(reference_profile.hght),
        pres=list(reference_profile.pres),
        tmpc=[value + 12.0 for value in reference_profile.tmpc],
        dwpc=[value + 3.0 for value in reference_profile.dwpc],
        u=[value + 2.0 for value in reference_profile.u],
        v=[value - 1.0 for value in reference_profile.v],
    )
    widget = _visual_pair(qt_app, candidate_profile)
    try:
        spans = widget.canvas.difference_spans()
        assert spans[1]["thermodynamic_c"] == pytest.approx(15.0)
        assert spans[1]["wind_kt"] == pytest.approx(5.0)

        # Both magnitudes are named, so neither axis is an unlabelled span.
        description = widget.canvas.accessibleDescription()
        assert "ΔT and ΔTd ±15 °C" in description
        assert "Δu and Δv ±5 kt" in description
    finally:
        widget.close()


def test_missing_differences_do_not_enter_a_scale_or_join_across_a_gap(qt_app):
    """A gap wider than the interpolation limit stays missing on both axes."""
    reference_profile = SimpleNamespace(
        hght=[300.0, 1500.0, 3000.0, 6000.0],
        pres=[975.0, 850.0, 700.0, 500.0],
        tmpc=[24.0, 14.0, 2.0, -14.0],
        dwpc=[18.0, 8.0, -4.0, -24.0],
        u=[5.0, 15.0, 25.0, 35.0],
        v=[10.0, 20.0, 30.0, 40.0],
    )
    # The candidate reports nothing between 1500 m and 6000 m, a 4500 m gap that
    # the bounded interpolator refuses to bridge.
    candidate_profile = SimpleNamespace(
        hght=[300.0, 1500.0, 6000.0],
        pres=[975.0, 850.0, 500.0],
        tmpc=[25.0, 15.0, -13.0],
        dwpc=[19.0, 9.0, -23.0],
        u=[7.0, 17.0, 37.0],
        v=[11.0, 21.0, 41.0],
    )
    widget = _visual_pair(
        qt_app, candidate_profile, reference_profile=reference_profile
    )
    try:
        levels = widget.canvas.panels()[1].difference.levels
        missing = [
            level
            for level in levels
            if level.temperature_c is None or level.u_wind_kt is None
        ]
        assert missing, "the 3000 m level has no bridgeable candidate sample"

        # Only reported differences set the scale; a missing level cannot widen
        # or narrow it, and the floor still applies.
        spans = widget.canvas.difference_spans()
        assert spans[1]["thermodynamic_c"] == pytest.approx(5.0)
        assert spans[1]["wind_kt"] == pytest.approx(5.0)

        assert "Gaps mean missing values and are never bridged" in (
            widget.canvas.accessibleDescription()
        )
        # Painting with missing levels must not raise or silently skip the panel.
        assert not widget.grab().isNull()
    finally:
        widget.close()


def test_no_common_coverage_states_the_reason_instead_of_empty_axes(qt_app):
    """An empty difference must say why rather than draw two blank scales."""
    reference_profile = _visual_profile()
    candidate_profile = SimpleNamespace(
        hght=[14_000.0, 15_000.0, 16_000.0],
        pres=[140.0, 120.0, 100.0],
        tmpc=[-60.0, -62.0, -64.0],
        dwpc=[-70.0, -72.0, -74.0],
        u=[40.0, 45.0, 50.0],
        v=[5.0, 6.0, 7.0],
    )
    widget = _visual_pair(qt_app, candidate_profile)
    try:
        difference = widget.canvas.panels()[1].difference
        assert difference.levels == ()
        assert difference.reason == "profiles have no common vertical coverage"
        # The panel still paints, and the scales fall back to the floor rather
        # than to zero-width axes.
        spans = widget.canvas.difference_spans()
        assert spans[1]["thermodynamic_c"] == pytest.approx(5.0)
        assert spans[1]["wind_kt"] == pytest.approx(5.0)
        assert not widget.grab().isNull()
    finally:
        widget.close()


def test_separate_axes_keep_their_own_geometry_and_the_shared_height_cursor(qt_app):
    """Two difference boxes, no overlap, and one unchanged height hit-test."""
    from qtpy.QtCore import QPointF

    reference_profile = _visual_profile()
    candidate_profile = SimpleNamespace(
        hght=list(reference_profile.hght),
        pres=list(reference_profile.pres),
        tmpc=[value + 2.0 for value in reference_profile.tmpc],
        dwpc=[value - 1.0 for value in reference_profile.dwpc],
        u=[value + 30.0 for value in reference_profile.u],
        v=[value - 25.0 for value in reference_profile.v],
    )
    widget = _visual_pair(qt_app, candidate_profile)
    try:
        canvas = widget.canvas
        canvas.resize(1_000, 460)
        qt_app.processEvents()
        assert not widget.grab().isNull()
        outer = canvas._panel_rects[0]
        plot, thermo, wind = canvas._panel_boxes(outer)

        # Three side-by-side boxes that never overlap and stay inside the slot.
        assert plot.right() < thermo.left()
        assert thermo.right() < wind.left()
        assert wind.right() <= outer.right()
        assert thermo.width() >= 25.0 and wind.width() >= 25.0
        # The two difference axes get equal room, so neither reads as primary.
        assert thermo.width() == pytest.approx(wind.width())
        # All three share the vertical coordinate.
        assert plot.top() == thermo.top() == wind.top()
        assert plot.height() == thermo.height() == wind.height()

        # Zero sits at each box's centre, and equal magnitudes map symmetrically
        # within a box while the two boxes keep their own scales.
        spans = canvas.difference_spans()[1]
        thermo_span, wind_span = spans["thermodynamic_c"], spans["wind_kt"]
        assert canvas._difference_x(0.0, thermo, thermo_span) == pytest.approx(
            thermo.center().x()
        )
        assert canvas._difference_x(0.0, wind, wind_span) == pytest.approx(
            wind.center().x()
        )
        assert canvas._difference_x(thermo_span, thermo, thermo_span) == pytest.approx(
            thermo.right()
        )
        assert canvas._difference_x(wind_span, wind, wind_span) == pytest.approx(
            wind.right()
        )

        # Hovering still resolves one height from the shared vertical axis.
        heights = []
        canvas.cursorHeightChanged.connect(heights.append)
        middle = QPointF(plot.center().x(), plot.center().y())
        # The canvas reads only the local position, and PySide6 deprecates the
        # device-less QMouseEvent constructor, so pass what it actually uses.
        canvas.mouseMoveEvent(SimpleNamespace(position=lambda: middle))
        low, high = canvas._height_range
        assert heights and heights[-1] == pytest.approx((low + high) / 2.0, abs=1.0)
        assert f"{heights[-1]:,.0f} m MSL" in widget.status.text()
        assert "Δ = slot − reference" in widget.status.text()
    finally:
        widget.close()


def test_family_span_rounds_up_and_ignores_missing_values():
    """The scale rule is per family, floored, and missing-value tolerant."""
    from sharpmod.gui_visual_comparison import _ComparisonCanvas

    def _levels(*values):
        return tuple(SimpleNamespace(a=value, b=None) for value in values)

    span = _ComparisonCanvas._family_span
    assert span(_levels(0.0), ("a",)) == pytest.approx(5.0)
    assert span(_levels(0.2, -1.0), ("a",)) == pytest.approx(5.0)
    assert span(_levels(5.0), ("a",)) == pytest.approx(5.0)
    assert span(_levels(5.1), ("a",)) == pytest.approx(10.0)
    assert span(_levels(-30.0, 2.0), ("a",)) == pytest.approx(30.0)
    assert span(_levels(None, float("nan"), 12.0), ("a",)) == pytest.approx(15.0)
    # A field that is absent or entirely missing falls back to the floor.
    assert span(_levels(40.0), ("b",)) == pytest.approx(5.0)
    assert span((), ("a",)) == pytest.approx(5.0)


def test_height_ticks_thin_out_instead_of_colliding(qt_app):
    """A fixed 2 km interval crowded into an unreadable column at 200% text."""
    from qtpy.QtGui import QFont, QFontMetricsF

    from sharpmod.gui_visual_comparison import _ComparisonCanvas

    step = _ComparisonCanvas._height_step
    small = QFontMetricsF(QFont("Arial", 8))
    large = QFontMetricsF(QFont("Arial", 22))

    # Plenty of room: the finest useful interval is kept.
    assert step(0.0, 18_000.0, 700.0, small) == pytest.approx(2000.0)
    # The same range in a short panel at large text has to thin out.
    coarse = step(0.0, 18_000.0, 150.0, large)
    assert coarse > 2000.0
    # Whatever interval is chosen, consecutive labels clear each other.
    for low, high, pixels, metrics in (
        (0.0, 18_000.0, 700.0, small),
        (0.0, 18_000.0, 150.0, large),
        (500.0, 3_000.0, 90.0, large),
        (0.0, 12_000.0, 40.0, large),
    ):
        chosen = step(low, high, pixels, metrics)
        spacing = chosen / (high - low) * pixels
        assert spacing >= metrics.height() or chosen >= 20_000.0

    # Degenerate inputs fall back rather than dividing by zero.
    assert step(1_000.0, 1_000.0, 400.0, small) == pytest.approx(2000.0)
    assert step(0.0, 12_000.0, 0.0, small) == pytest.approx(2000.0)


def test_comparison_canvas_stays_paintable_when_every_label_is_too_large(qt_app):
    """A cramped canvas must degrade its labels, not clip or fail to paint."""
    reference_profile = _visual_profile()
    candidate_profile = SimpleNamespace(
        hght=list(reference_profile.hght),
        pres=list(reference_profile.pres),
        tmpc=[value + 18.0 for value in reference_profile.tmpc],
        dwpc=[value - 9.0 for value in reference_profile.dwpc],
        u=[value + 120.0 for value in reference_profile.u],
        v=[value - 95.0 for value in reference_profile.v],
    )
    widget = _visual_pair(qt_app, candidate_profile)
    try:
        canvas = widget.canvas
        # The canvas minimum is the smallest real size a user can reach.
        canvas.resize(canvas.minimumWidth(), canvas.minimumHeight())
        qt_app.processEvents()
        assert not canvas.grab().isNull()

        # Even where the numeric ends cannot be drawn, the magnitudes stay
        # available in the accessible text rather than being lost.
        description = canvas.accessibleDescription()
        assert "ΔT and ΔTd ±20 °C" in description
        assert "Δu and Δv ±120 kt" in description
        assert "Δ = slot − reference" in description
    finally:
        widget.close()


@pytest.mark.parametrize("slots", (2, 4))
def test_narrow_enlarged_comparison_labels_clear_plots_and_footer(qt_app, slots):
    from qtpy.QtGui import QFontMetricsF

    gui_theme.apply_theme(qt_app, text_scale=200)
    widget = _visual_pair(qt_app, _visual_profile(1.0))
    try:
        canvas = widget.canvas
        canvas._layout_count = slots
        canvas.resize(546, 520)
        title_font = gui_theme.ui_font("small")
        title_font.setBold(True)
        title = QFontMetricsF(title_font)
        labels = QFontMetricsF(gui_theme.mono_font("small"))
        for outer in canvas._layout():
            plot, thermo, wind = canvas._panel_boxes(outer)
            # The top height tick must not share the bold slot title's row.
            assert plot.top() - labels.height() / 2 >= outer.top() + 4 + title.height() + 3
            # Two readable caption lines stay inside their slot, above the footer.
            assert plot.bottom() + 4 + labels.height() * 2 <= outer.bottom()
            assert outer.bottom() < canvas.height() - labels.height() - 3
            for box, full, short in (
                (plot, "T / Td (°C)", "T/Td °C"),
                (thermo, "ΔT ΔTd (°C)", "Δ °C"),
                (wind, "Δu Δv (kt)", "Δ kt"),
            ):
                caption = canvas._caption(labels, box.width(), full, short)
                assert all(labels.horizontalAdvance(line) <= box.width() for line in caption.splitlines())
        assert not canvas.grab().isNull()
    finally:
        widget.close()
        gui_theme.apply_theme(qt_app, text_scale=100)


def test_comparison_traces_stay_inside_their_axis(qt_app):
    from qtpy.QtCore import QPointF, QRectF
    from qtpy.QtGui import QColor, QPainter, QPixmap
    from sharpmod.gui_visual_comparison import _ComparisonCanvas

    image = QPixmap(80, 100)
    image.fill(QColor("white"))
    painter = QPainter(image)
    try:
        _ComparisonCanvas._trace(painter, (QPointF(20, 0), QPointF(20, 90)),
                                 QColor("red"), clip=QRectF(10, 30, 60, 50))
    finally:
        painter.end()
    pixels = image.toImage()
    assert pixels.pixelColor(20, 10) == QColor("white")
    assert pixels.pixelColor(20, 40) == QColor("red")
    assert pixels.pixelColor(20, 85) == QColor("white")


# --- T07.3 deliberate axis and scale locking ------------------------------- #


def _candidate(reference_profile, *, temperature=2.0, dewpoint=-1.0, u=30.0, v=-25.0):
    return SimpleNamespace(
        hght=list(reference_profile.hght),
        pres=list(reference_profile.pres),
        tmpc=[value + temperature for value in reference_profile.tmpc],
        dwpc=[value + dewpoint for value in reference_profile.dwpc],
        u=[value + u for value in reference_profile.u],
        v=[value + v for value in reference_profile.v],
    )


def test_locking_fixes_one_shared_scale_per_family_across_slots(qt_app):
    """Automatic fitting per slot makes two slots incomparable by eye."""
    reference_profile = _visual_profile()
    small = _candidate(reference_profile, temperature=2.0, u=4.0)
    large = _candidate(reference_profile, temperature=11.0, u=40.0)
    collections = (
        _VisualCollection("Reference", profiles=(reference_profile,)),
        _VisualCollection("Small", profiles=(small,)),
        _VisualCollection("Large", profiles=(large,)),
    )
    widget = VisualComparisonWidget()
    try:
        widget.resize(1_100, 620)
        widget.set_layout_count(4)
        widget.set_collections(
            collections, 0, RUN, profile_ids=("reference", "small", "large")
        )
        widget.show()
        qt_app.processEvents()

        # Automatic: each slot fits itself, so equal pixel offsets mean
        # different quantities in different slots.
        assert not widget.canvas.scale_lock()
        automatic = widget.canvas.difference_spans()
        assert automatic[1]["thermodynamic_c"] != automatic[2]["thermodynamic_c"]
        assert automatic[1]["wind_kt"] != automatic[2]["wind_kt"]
        assert "automatic ranges" in widget.status.text()
        assert "fit each slot automatically" in widget.canvas.accessibleDescription()

        widget.lock_scales.setChecked(True)
        qt_app.processEvents()
        assert widget.canvas.scale_lock()
        assert widget.refit_scales.isEnabled()

        # Locked: one scale per family, wide enough for the widest slot, so
        # nothing is clipped at the moment of locking.
        locked = widget.canvas.difference_spans()
        # Slot 4 has no profile, so it contributes no scale; the three assigned
        # slots all share one.
        assert set(locked) == {0, 1, 2}
        assert locked[0] == locked[1] == locked[2]
        assert locked[1]["thermodynamic_c"] == pytest.approx(
            max(
                automatic[index]["thermodynamic_c"] for index in automatic
            )
        )
        assert locked[1]["wind_kt"] == pytest.approx(
            max(automatic[index]["wind_kt"] for index in automatic)
        )
        assert widget.canvas.clipped_families() == ()
        assert "fixed ranges" in widget.status.text()
        assert "ranges are fixed for comparison" in (
            widget.canvas.accessibleDescription()
        )
        assert not widget.grab().isNull()

        widget.lock_scales.setChecked(False)
        qt_app.processEvents()
        assert not widget.canvas.scale_lock()
        assert widget.canvas.locked_scales() is None
        assert not widget.refit_scales.isEnabled()
        assert widget.canvas.difference_spans() == automatic
    finally:
        widget.close()


def test_a_fixed_range_survives_a_profile_change_and_clamps_honestly(qt_app):
    """A locked axis must not refit itself, and must admit when it clamps."""
    reference_profile = _visual_profile()
    modest = _candidate(reference_profile, temperature=2.0, u=4.0)
    collections = (
        _VisualCollection("Reference", profiles=(reference_profile,)),
        _VisualCollection("Modest", profiles=(modest,)),
    )
    widget = VisualComparisonWidget()
    try:
        widget.resize(1_000, 540)
        widget.set_collections(
            collections, 0, RUN, profile_ids=("reference", "modest")
        )
        widget.show()
        qt_app.processEvents()
        widget.lock_scales.setChecked(True)
        qt_app.processEvents()
        frozen = widget.canvas.locked_scales()
        frozen_height = widget.canvas._height_range
        assert frozen["thermodynamic_c"] == pytest.approx(5.0)

        # A far larger difference arrives at the same slot: the same profile ID
        # now carries much bigger departures. The fixed range must stay fixed.
        extreme = _candidate(reference_profile, temperature=30.0, u=90.0)
        widget.set_collections(
            (
                collections[0],
                _VisualCollection("Modest", profiles=(extreme,)),
            ),
            0,
            RUN,
            profile_ids=("reference", "modest"),
        )
        qt_app.processEvents()
        assert widget.canvas.locked_scales() == frozen
        assert widget.canvas.difference_spans()[1]["thermodynamic_c"] == pytest.approx(
            frozen["thermodynamic_c"]
        )
        assert widget.canvas._height_range == frozen_height

        # Clamping is stated, never silent, in both the readout and the canvas.
        assert widget.canvas.clipped_families() == ("thermodynamic_c", "wind_kt")
        assert "clamped at the axis edge" in widget.status.text()
        assert "clamped to the axis edge" in widget.canvas.accessibleDescription()
        assert "Refit" in widget.status.text()

        # A clamped value rests on the axis edge instead of drawing outside it.
        outer = widget.canvas._panel_rects[1]
        _plot, thermo, _wind = widget.canvas._panel_boxes(outer)
        span = widget.canvas.difference_spans()[1]["thermodynamic_c"]
        assert widget.canvas._difference_x(span * 6.0, thermo, span) == pytest.approx(
            thermo.right()
        )
        assert widget.canvas._difference_x(-span * 6.0, thermo, span) == pytest.approx(
            thermo.left()
        )
        assert not widget.grab().isNull()

        # Refit adopts the data now loaded while staying fixed.
        widget.refit_scales.click()
        qt_app.processEvents()
        assert widget.canvas.scale_lock()
        assert widget.canvas.clipped_families() == ()
        assert widget.canvas.locked_scales()["thermodynamic_c"] > frozen[
            "thermodynamic_c"
        ]
        assert "clamped" not in widget.status.text()
    finally:
        widget.close()


def test_axis_lock_round_trips_and_refuses_a_broken_saved_range(qt_app):
    reference_profile = _visual_profile()
    collections = (
        _VisualCollection("Reference", profiles=(reference_profile,)),
        _VisualCollection("Candidate", profiles=(_candidate(reference_profile),)),
    )
    widget = VisualComparisonWidget()
    try:
        widget.resize(1_000, 540)
        widget.set_collections(
            collections, 0, RUN, profile_ids=("reference", "candidate")
        )
        widget.lock_scales.setChecked(True)
        qt_app.processEvents()
        saved = widget.scale_state()
        assert saved["locked"] is True
        assert saved["scales"]["wind_kt"] == pytest.approx(30.0)
        # JSON-safe: no tuples survive a session file.
        assert isinstance(saved["scales"]["height_m"], list)

        widget.lock_scales.setChecked(False)
        qt_app.processEvents()
        assert not widget.canvas.scale_lock()

        widget.restore_scale_state(saved)
        qt_app.processEvents()
        assert widget.canvas.scale_lock()
        assert widget.lock_scales.isChecked()
        assert widget.refit_scales.isEnabled()
        assert widget.canvas.locked_scales()["wind_kt"] == pytest.approx(30.0)

        # A saved lock whose ranges are unusable falls back to automatic rather
        # than fixing the axes at nothing.
        for broken in (
            {"locked": True},
            {"locked": True, "scales": {}},
            {"locked": True, "scales": {"thermodynamic_c": 0.0, "wind_kt": 5.0,
                                        "height_m": [0, 1], "temperature_c": [0, 1]}},
            {"locked": True, "scales": {"thermodynamic_c": 5.0, "wind_kt": 5.0,
                                        "height_m": [0], "temperature_c": [0, 1]}},
            {"locked": True, "scales": {"thermodynamic_c": "x", "wind_kt": 5.0,
                                        "height_m": [0, 1], "temperature_c": [0, 1]}},
            "not a mapping",
            None,
        ):
            widget.restore_scale_state(saved)
            widget.restore_scale_state(broken)
            if broken in ("not a mapping", None):
                # Unreadable state changes nothing at all.
                assert widget.canvas.scale_lock()
            else:
                assert not widget.canvas.scale_lock()
                assert not widget.lock_scales.isChecked()
    finally:
        widget.close()


def test_workspace_session_round_trips_the_axis_lock(qt_app):
    collections = [_Collection("KOUN", "HRRR"), _Collection("KOUN", "RAP")]
    win, workspace, _engine = _make(qt_app, collections)
    try:
        workspace.visual_compare.lock_scales.setChecked(True)
        qt_app.processEvents()
        state = workspace.session_state()
        assert state["comparison_scales"]["locked"] is True

        workspace.visual_compare.lock_scales.setChecked(False)
        qt_app.processEvents()
        workspace.restore_session_state(state)
        assert workspace.visual_compare.canvas.scale_lock()
        assert workspace.visual_compare.lock_scales.isChecked()

        # An older session with no lock key restores as automatic.
        legacy = dict(state)
        legacy.pop("comparison_scales")
        workspace.restore_session_state(legacy)
        assert not workspace.visual_compare.canvas.scale_lock()
    finally:
        win.close()


# --- T07.4 comparison column selection and ordering ------------------------ #


def test_metric_column_keys_are_validated_bounded_and_defaulted():
    from sharpmod.gui_metric_columns import (
        MAX_COLUMNS,
        available_keys,
        metric_label,
        normalize_keys,
    )
    from sharpmod.profile_metrics import DEFAULT_METRIC_KEYS

    keys = available_keys()
    assert "mlcape" in keys and "srh_1km" in keys
    assert len(set(keys)) == len(keys)

    # Order is honoured exactly as given.
    assert normalize_keys(("srh_1km", "mlcape")) == ("srh_1km", "mlcape")
    # Unknown keys are dropped rather than requested from the engine.
    assert normalize_keys(("mlcape", "not_a_metric")) == ("mlcape",)
    # Duplicates collapse, keeping first position.
    assert normalize_keys(("mlcape", "mlcape", "mlcin")) == ("mlcape", "mlcin")
    # An empty or entirely invalid choice falls back to the documented default.
    assert normalize_keys(()) == tuple(DEFAULT_METRIC_KEYS)
    assert normalize_keys(("nope", "also_nope")) == tuple(DEFAULT_METRIC_KEYS)
    assert normalize_keys(None) == tuple(DEFAULT_METRIC_KEYS)
    # The count is bounded so the table stays readable.
    assert len(normalize_keys(keys)) == MAX_COLUMNS

    # Labels and units come from the registry, not from this module.
    assert metric_label("mlcape") == "MLCAPE (J/kg)"
    assert metric_label("not_a_metric") == "NOT A METRIC"


def test_metric_column_preference_survives_a_restart_and_bad_data(qt_app, tmp_path):
    from qtpy.QtCore import QSettings

    from sharpmod.gui_metric_columns import (
        SETTINGS_KEY,
        read_preference,
        write_preference,
    )
    from sharpmod.profile_metrics import DEFAULT_METRIC_KEYS

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)

    # Nothing stored yet: the documented default.
    assert read_preference(settings) == tuple(DEFAULT_METRIC_KEYS)
    assert read_preference(None) == tuple(DEFAULT_METRIC_KEYS)

    assert write_preference(settings, ("srh_1km", "mlcape"))
    settings.sync()
    reopened = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    assert read_preference(reopened) == ("srh_1km", "mlcape")

    # Unreadable, wrong-shaped, or newer documents fall back instead of raising.
    for broken in ("not json", "[]", '{"version": 1}', '{"version": 1, "keys": "x"}',
                   '{"version": 99, "keys": ["mlcape"]}', '{"keys": ["nope"]}'):
        settings.setValue(SETTINGS_KEY, broken)
        assert read_preference(settings) == tuple(DEFAULT_METRIC_KEYS)
    assert not write_preference(None, ("mlcape",))


def test_comparison_columns_follow_the_chosen_order_and_are_requested(qt_app):
    """A chosen column has to be computed, not read from a stale value set."""
    collections = [_Collection("KOUN", "HRRR"), _Collection("KOUN", "RAP")]
    engine = _Engine()
    win, workspace, engine = _make(qt_app, collections, engine)
    try:
        workspace.open("compare")
        qt_app.processEvents()

        chosen = ("srh_1km", "mlcape", "mlcin")
        engine.calls.clear()
        workspace.set_comparison_metric_keys(chosen, persist=False)
        qt_app.processEvents()

        assert workspace.comparison_metric_keys() == chosen
        # The engine is asked for exactly the chosen keys, in order, so a newly
        # added column is computed rather than read from a stale value set.
        compare_calls = [call for call in engine.calls if call[0] == "compare"]
        assert compare_calls, "new columns must be computed"
        assert compare_calls[-1][2] == chosen

        # Three prose columns, then one value and one Δ column per diagnostic,
        # in exactly the chosen order.
        table = workspace.compare_table
        assert table.columnCount() == 3 + len(chosen) * 2
        headers = [
            table.horizontalHeaderItem(column).text()
            for column in range(table.columnCount())
        ]
        assert headers[:3] == ["Sounding", "Valid time", "Alignment"]
        assert "SRH" in headers[3]
        assert headers[4] == "\u0394"
        assert headers[5].startswith("MLCAPE")
        assert headers[7].startswith("MLCIN")
        # Units and their glyphs ride along with the label, from the registry.
        assert "(J/kg)" in headers[5]
        assert "m\u00b2/s\u00b2" in headers[3]

        # An invalid choice cannot empty the table.
        workspace.set_comparison_metric_keys((), persist=False)
        from sharpmod.profile_metrics import DEFAULT_METRIC_KEYS

        assert workspace.comparison_metric_keys() == tuple(DEFAULT_METRIC_KEYS)
    finally:
        win.close()


def test_comparison_columns_round_trip_through_the_session(qt_app):
    collections = [_Collection("KOUN", "HRRR"), _Collection("KOUN", "RAP")]
    win, workspace, _engine = _make(qt_app, collections)
    try:
        workspace.set_comparison_metric_keys(("stp_cin", "shear_6km"), persist=False)
        state = workspace.session_state()
        assert state["comparison_metrics"] == ["stp_cin", "shear_6km"]

        workspace.set_comparison_metric_keys(("mlcape",), persist=False)
        workspace.restore_session_state(state)
        assert workspace.comparison_metric_keys() == ("stp_cin", "shear_6km")

        # A session predating the feature keeps the current preference rather
        # than silently reverting the reader's columns.
        legacy = dict(state)
        legacy.pop("comparison_metrics")
        workspace.restore_session_state(legacy)
        assert workspace.comparison_metric_keys() == ("stp_cin", "shear_6km")

        # A malformed value is ignored for the same reason.
        broken = dict(state)
        broken["comparison_metrics"] = "srh_1km"
        workspace.restore_session_state(broken)
        assert workspace.comparison_metric_keys() == ("stp_cin", "shear_6km")
    finally:
        win.close()


def test_metric_columns_dialog_checks_orders_and_refuses_an_empty_choice(qt_app):
    from qtpy.QtWidgets import QDialog, QDialogButtonBox

    from sharpmod.gui_metric_columns import MetricColumnsDialog
    from sharpmod.profile_metrics import DEFAULT_METRIC_KEYS

    dialog = MetricColumnsDialog(("srh_1km", "mlcape"))
    try:
        # Chosen columns are listed first, in order, and checked.
        assert dialog.selected_keys() == ("srh_1km", "mlcape")
        assert dialog.columns.item(0).data(Qt.UserRole) == "srh_1km"
        assert dialog.columns.item(0).checkState() == Qt.Checked
        assert dialog.columns.item(2).checkState() == Qt.Unchecked
        assert dialog.columns.count() > 2

        # Moving a row reorders the columns.
        dialog.columns.setCurrentRow(1)
        dialog.move_down.setEnabled(True)
        dialog._move(-1)
        assert dialog.selected_keys() == ("mlcape", "srh_1km")

        # Unchecking everything is refused: the table needs a column.
        for row in range(dialog.columns.count()):
            dialog.columns.item(row).setCheckState(Qt.Unchecked)
        assert dialog.selected_keys() == ()
        ok = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
        assert not ok.isEnabled()
        assert "at least one" in dialog.status.text()
        dialog.accept()
        assert dialog.result() != QDialog.Accepted

        # Reset restores the documented defaults in their documented order.
        dialog._reset()
        assert dialog.selected_keys() == tuple(DEFAULT_METRIC_KEYS)
        assert ok.isEnabled()
    finally:
        dialog.close()
