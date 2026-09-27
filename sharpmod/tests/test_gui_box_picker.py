"""Box sounding wiring inside the forecast-model picker tab."""

from types import SimpleNamespace

import numpy as np
import pytest

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QDialog

from sharpmod import gui_batch_process
from sharpmod import box_analysis as ba
from sharpmod.box_sounding import BoxRegion, plan_box_samples
from sharpmod.tests._examples import examples_dir
from sharpmod.tests.test_box_analysis import _write_variants


HRRR_NPZ = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"

pytestmark = [
    pytest.mark.skipif(not HRRR_NPZ.is_file(), reason="no HRRR .npz example sounding"),
    pytest.mark.usefixtures("qt_app"),
    # The picker owns process-global Qt state (settings, logging handlers).
    pytest.mark.xdist_group("gui_picker"),
]


@pytest.fixture
def picker(monkeypatch, tmp_path):
    """A picker window with its model tab realized and no network access."""
    monkeypatch.setenv("SHARPMOD_SETTINGS_PATH", str(tmp_path / "settings.json"))
    from sharpmod.gui_picker import PickerWindow

    # Availability probing reaches the network; the box path does not need it.
    monkeypatch.setattr(
        PickerWindow, "_queue_model_availability", lambda self, *a: None
    )
    window = PickerWindow()
    window._ensure_tab("Forecast Model")
    yield window
    window._shutdown_model_cache()
    window.close()
    window.deleteLater()


def _select_hrrr(picker):
    index = picker._model_combo.findData("hrrr")
    if index < 0:
        for position in range(picker._model_combo.count()):
            if picker._model_combo.itemText(position).startswith("HRRR"):
                index = position
                break
    assert index >= 0, "HRRR is not selectable"
    picker._model_combo.setCurrentIndex(index)


# -- controls -------------------------------------------------------------- #


def test_model_tab_exposes_a_box_button(picker):
    assert picker._model_box_btn.isCheckable()
    assert "rectangle" in picker._model_box_btn.toolTip()


def test_box_button_toggles_map_box_mode(picker):
    picker._model_box_btn.setChecked(True)
    assert picker._model_map.box_mode() is True
    picker._model_map.set_box((34.0, -99.0, 37.0, -95.0))
    picker._model_box_btn.setChecked(False)
    assert picker._model_map.box_mode() is False
    # Tool choice is not selection state: bounds survive until explicit Reset.
    assert picker._model_map.box() == (34.0, -99.0, 37.0, -95.0)


def test_map_box_signal_is_connected_to_the_picker(picker, monkeypatch):
    seen = []
    monkeypatch.setattr(
        type(picker), "_model_on_box_selected", lambda self, *args: seen.append(args)
    )
    picker._model_map.boxSelected.emit(34.0, -99.0, 37.0, -95.0)
    assert seen == [(34.0, -99.0, 37.0, -95.0)]


def test_numeric_bounds_commit_preview_without_starting_extraction(picker):
    _select_hrrr(picker)
    picker._model_box_south.setValue(34.0)
    picker._model_box_north.setValue(37.0)
    picker._model_box_west.setValue(-99.0)
    picker._model_box_east.setValue(-95.0)

    picker._model_apply_box_bounds()

    assert picker._model_map.box() == (34.0, -99.0, 37.0, -95.0)
    assert picker._box_preview_plan is not None
    assert picker._box_extract_worker is None
    assert "planned nodes" in picker._model_box_preview.text()
    assert "not guaranteed" in picker._model_box_preview.text()
    assert picker._model_box_extract_action.isVisibleTo(picker._model_selection_pane)


def test_partial_grid_coverage_distinguishes_outside_nodes(picker):
    _select_hrrr(picker)
    picker._model_map.set_box((45.0, -110.0, 55.0, -90.0))
    picker._model_on_box_selected(45.0, -110.0, 55.0, -90.0)

    plan = picker._box_preview_plan
    assert plan is not None
    assert 0 < len(plan.requestable_points) < plan.count
    assert len(picker._model_map._box_node_coverage) == plan.count
    assert False in picker._model_map._box_node_coverage
    assert plan.spacing_km >= plan.native_spacing_km
    assert f"{len(plan.requestable_points)}/{plan.count}" in (
        picker._model_box_preview.text())
    assert picker._box_extract_worker is None


def test_map_box_tool_row_and_bottom_action_stay_in_sync(picker, qt_app):
    picker._model_map.set_map_tool("box")
    qt_app.processEvents()
    assert picker._model_box_btn.isChecked()
    assert picker._model_box_editor.isVisibleTo(picker._model_controls_scroll)

    picker._model_map.set_map_tool("inspect")
    qt_app.processEvents()
    assert not picker._model_box_btn.isChecked()


def test_numeric_bounds_support_antimeridian_convention(picker):
    picker._model_box_south.setValue(45.0)
    picker._model_box_north.setValue(55.0)
    picker._model_box_west.setValue(170.0)
    picker._model_box_east.setValue(-170.0)

    picker._model_apply_box_bounds()

    assert picker._model_map.box() == (45.0, 170.0, 55.0, 190.0)
    region = picker._current_model_box_region()
    assert region.crosses_antimeridian
    assert region.lon_span == pytest.approx(20.0)


def test_numeric_bounds_round_trip_positive_dateline_edge(picker):
    picker._model_box_south.setValue(10.0)
    picker._model_box_north.setValue(20.0)
    picker._model_box_west.setValue(170.0)
    picker._model_box_east.setValue(180.0)

    picker._model_apply_box_bounds()
    picker._sync_model_box_bounds(picker._current_model_box_region())

    assert picker._model_box_west.value() == pytest.approx(170.0)
    assert picker._model_box_east.value() == pytest.approx(180.0)


def test_reset_is_the_explicit_way_to_clear_committed_bounds(picker):
    picker._model_map.set_box((34.0, -99.0, 37.0, -95.0))
    picker._model_on_box_selected(34.0, -99.0, 37.0, -95.0)
    assert picker._model_map.box() is not None

    picker._model_reset_box()

    assert picker._model_map.box() is None
    assert picker._box_preview_plan is None
    assert not picker._model_box_extract_btn.isEnabled()
    assert picker._model_box_editor.isVisibleTo(picker._model_controls_scroll)
    assert "No area selected" in picker._model_box_preview.text()
    assert "draft" in picker._model_box_preview.text()
    assert picker._model_box_south.value() != pytest.approx(34.0)
    assert picker._model_box_west.value() != pytest.approx(-99.0)
    assert picker._model_box_saved_combo.currentData() == ""

    # Reset is not a dead end: the still-visible controls can create a new
    # selection without arming the drawing tool or starting a worker.
    picker._model_box_south.setValue(32.0)
    picker._model_box_north.setValue(33.0)
    picker._model_box_west.setValue(-101.0)
    picker._model_box_east.setValue(-99.0)
    picker._model_box_apply_btn.click()
    assert picker._model_map.box() == (32.0, -101.0, 33.0, -99.0)
    assert picker._box_preview_plan is not None
    assert picker._box_extract_worker is None


def test_forecast_hour_change_preserves_bounds_and_refreshes_preview(picker):
    _select_hrrr(picker)
    picker._model_map.set_box((34.0, -99.0, 37.0, -95.0))
    picker._model_on_box_selected(34.0, -99.0, 37.0, -95.0)
    before = picker._model_map.box()
    plan_before = picker._box_preview_plan
    assert picker._model_fxx_combo.count() > 1

    picker._model_fxx_combo.setCurrentIndex(1)

    assert picker._model_map.box() == before
    assert picker._box_preview_plan is not None
    assert picker._box_preview_plan.region == plan_before.region


def test_model_change_replans_coverage_but_keeps_selected_geography(picker):
    _select_hrrr(picker)
    bounds = (34.0, -99.0, 37.0, -95.0)
    picker._model_map.set_box(bounds)
    picker._model_on_box_selected(*bounds)
    gfs_index = picker._model_combo.findData("gfs")
    assert gfs_index >= 0

    picker._model_combo.setCurrentIndex(gfs_index)

    assert picker._model_map.box() == bounds
    assert picker._box_preview_plan.model_key == "gfs"
    assert "GFS" in picker._model_box_preview.text()


def test_box_survives_model_and_field_panel_navigation(picker):
    _select_hrrr(picker)
    bounds = (34.0, -99.0, 37.0, -95.0)
    picker._model_map.set_box(bounds)
    picker._model_on_box_selected(*bounds)
    picker._ensure_tab("Field Panels")
    picker._select_tab("Field Panels")
    picker._select_tab("Forecast Model")
    assert picker._model_map.box() == bounds
    assert picker._box_preview_plan is not None


def test_only_explicit_confirmation_starts_extraction_after_drag_and_cancel(
        picker, monkeypatch):
    from sharpmod.tests.test_gui_box_map import _MouseEvent

    _select_hrrr(picker)
    started = []
    monkeypatch.setattr(type(picker), "_start_box_extraction",
                        lambda self, *args, **kwargs: started.append(args))
    monkeypatch.setattr("sharpmod.gui_box.BoxPlanDialog.exec",
                        lambda self: QDialog.Rejected)
    view = picker._model_map
    view.resize(600, 400)
    view.set_box_mode(True)
    start, end = (250.0, 160.0), (350.0, 230.0)
    view.mousePressEvent(_MouseEvent(start))
    view.mouseMoveEvent(_MouseEvent(end))
    view.mouseReleaseEvent(_MouseEvent(end))
    assert view.box() is not None
    assert picker._box_preview_plan is not None
    assert started == []
    picker._model_extract_box()
    assert started == []
    assert view.box() is not None
    monkeypatch.setattr("sharpmod.gui_box.BoxPlanDialog.exec",
                        lambda self: QDialog.Accepted)
    picker._model_extract_box()
    assert len(started) == 1


def test_save_duplicate_restore_bounds_do_not_change_model(picker):
    _select_hrrr(picker)
    source = picker._model_combo.currentData()
    run = picker._model_run_time()
    hour = picker._model_selected_fxx()
    bounds = (34.0, -99.0, 37.0, -95.0)
    picker._model_map.set_box(bounds)
    picker._model_on_box_selected(*bounds)
    picker._model_box_name.setText("Dryline sector")

    picker._model_save_box()
    saved_index = picker._model_box_saved_combo.findData("Dryline sector")
    assert saved_index > 0
    picker._model_duplicate_box()
    assert picker._model_box_saved_combo.findData("Dryline sector copy") > 0

    picker._model_map.set_box((30.0, -90.0, 31.0, -88.0))
    picker._model_restore_saved_box(saved_index)

    assert picker._model_map.box() == bounds
    assert picker._model_combo.currentData() == source
    assert picker._model_run_time() == run
    assert picker._model_selected_fxx() == hour


def test_saved_area_is_available_after_picker_reopens(picker):
    from sharpmod.gui_picker import PickerWindow

    _select_hrrr(picker)
    bounds = (34.0, -99.0, 37.0, -95.0)
    picker._model_map.set_box(bounds)
    picker._model_on_box_selected(*bounds)
    picker._model_box_name.setText("Reusable area")
    picker._model_save_box()

    reopened = PickerWindow()
    reopened._ensure_tab("Forecast Model")
    try:
        index = reopened._model_box_saved_combo.findData("Reusable area")
        assert index > 0
        source_before = reopened._model_combo.currentData()
        reopened._model_restore_saved_box(index)
        assert reopened._model_map.box() == bounds
        assert reopened._model_combo.currentData() == source_before
    finally:
        reopened._shutdown_model_cache()
        reopened.close()
        reopened.deleteLater()


def test_a_box_run_marks_the_model_tab_busy(picker):
    _select_hrrr(picker)
    picker._box_extract_worker = object()
    picker._model_update_fetch_state()
    assert not picker._model_fetch_btn.isEnabled()
    picker._box_extract_worker = None
    picker._box_analysis_worker = object()
    picker._model_update_fetch_state()
    assert not picker._model_fetch_btn.isEnabled()
    picker._box_analysis_worker = None


# -- plan gating ----------------------------------------------------------- #


def test_degenerate_box_is_reported_and_discarded(picker):
    _select_hrrr(picker)
    picker._model_map.set_box((35.0, -97.0, 35.0, -97.0))
    picker._model_on_box_selected(35.0, -97.0, 35.0, -97.0)
    assert picker._model_map.box() is None
    assert picker._box_extract_worker is None


def test_extraction_is_refused_while_another_model_fetch_runs(picker, monkeypatch):
    _select_hrrr(picker)
    shown = []
    monkeypatch.setattr(
        "sharpmod.gui_picker.QMessageBox.information",
        lambda *args, **kwargs: shown.append(args[-1]),
    )
    picker._model_on_box_selected(34.0, -99.0, 37.0, -95.0)
    picker._model_worker = object()
    picker._model_extract_box()
    picker._model_worker = None
    assert shown and "already in progress" in shown[0]
    assert picker._box_extract_worker is None


def test_a_second_extraction_is_refused_while_one_runs(picker, monkeypatch):
    _select_hrrr(picker)
    shown = []
    monkeypatch.setattr(
        "sharpmod.gui_picker.QMessageBox.information",
        lambda *args, **kwargs: shown.append(args[-1]),
    )
    picker._model_on_box_selected(34.0, -99.0, 37.0, -95.0)
    picker._box_extract_worker = object()
    picker._model_extract_box()
    picker._box_extract_worker = None
    assert shown and "box sounding is already in progress" in shown[0]


def test_cancelling_the_plan_dialog_keeps_the_editable_rectangle(picker, monkeypatch):
    _select_hrrr(picker)
    monkeypatch.setattr(
        "sharpmod.gui_box.BoxPlanDialog.exec", lambda self: QDialog.Rejected
    )
    picker._model_box_btn.setChecked(True)
    picker._model_map.set_box((34.0, -99.0, 37.0, -95.0))
    picker._model_on_box_selected(34.0, -99.0, 37.0, -95.0)
    picker._model_extract_box()
    assert picker._model_map.box() == (34.0, -99.0, 37.0, -95.0)
    assert picker._model_box_btn.isChecked()
    assert picker._model_map.box_mode()
    assert picker._box_extract_worker is None


def test_escape_signal_disarms_the_box_button(picker):
    """Escaping the map cannot leave the toolbar toggle visually armed."""
    picker._model_box_btn.setChecked(True)
    assert picker._model_map.box_mode()

    picker._model_map.boxCleared.emit()

    assert not picker._model_box_btn.isChecked()
    assert not picker._model_map.box_mode()


def test_selecting_a_box_only_previews_and_confirming_starts_a_worker(
    picker, monkeypatch
):
    _select_hrrr(picker)
    monkeypatch.setattr(
        "sharpmod.gui_box.BoxPlanDialog.exec", lambda self: QDialog.Accepted
    )
    started = []
    monkeypatch.setattr(
        type(picker),
        "_start_box_extraction",
        lambda self, plan, hours=None, mode=None, fxx=None: started.append(
            (plan, hours, mode, fxx)
        ),
    )
    picker._model_map.set_box((34.0, -99.0, 37.0, -95.0))
    picker._model_on_box_selected(34.0, -99.0, 37.0, -95.0)
    assert started == []
    assert "Estimate only" in picker._model_box_preview.text()
    picker._model_extract_box()
    assert len(started) == 1
    plan, hours, mode, fxx = started[0]
    assert plan.model_key == "hrrr"
    # The dialog defaults to a single hour, so no sequence is requested.
    assert hours is None
    # And it defaults to averaging the box into one sounding.
    assert mode == "mean"
    # The hour comes from the dialog, which starts on the sidebar's selection.
    assert fxx == picker._model_selected_fxx()
    # The lattice is shown before anything downloads.
    assert len(picker._model_map._box_nodes) == plan.count
    assert "km" in picker._model_map._box_note


# -- extraction and analysis ---------------------------------------------- #


def test_extraction_result_opens_the_window_and_starts_analysis(
    picker, monkeypatch, tmp_path
):
    from sharpmod.gui_box import BoxExtractResult

    _select_hrrr(picker)
    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8), target_points=16
    )
    outputs = _write_variants(plan, tmp_path)
    extraction = BoxExtractResult(
        plan=plan,
        outputs=outputs,
        run_time=None,
        fxx=18,
        completed=len(outputs),
        output_dir=str(tmp_path),
    )

    analyses = []
    monkeypatch.setattr(
        type(picker),
        "_start_box_analysis",
        lambda self, extraction, tiers: analyses.append(tiers),
    )
    # The handler guards on the sender, so stand in as the active worker.
    sentinel = SimpleNamespace(_sharpmod_box_token="box-token")
    monkeypatch.setattr(type(picker), "sender", lambda self: sentinel)
    picker._box_extract_worker = sentinel
    picker._box_token = "box-token"
    # This is the field workspace path; the default now averages instead.
    picker._box_mode = "field"
    picker._on_box_extract_result(extraction)
    picker._box_extract_worker = None
    picker._box_token = None

    assert analyses == [(ba.FAST_TIER,)]
    assert picker._box_window is not None
    assert picker._box_extraction is extraction
    region = plan.region
    assert picker._box_window._map.box() == pytest.approx(
        (region.lat0, region.lon0, region.lat1, region.lon1)
    )
    assert "soundings from one" in picker._box_window._status.text()


def test_an_empty_extraction_warns_and_opens_nothing(picker, monkeypatch):
    from sharpmod.gui_box import BoxExtractResult

    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8), target_points=16
    )
    warned = []
    monkeypatch.setattr(
        "sharpmod.gui_picker.QMessageBox.warning",
        lambda *args, **kwargs: warned.append(args[-1]),
    )
    sentinel = SimpleNamespace(_sharpmod_box_token="box-token")
    monkeypatch.setattr(type(picker), "sender", lambda self: sentinel)
    picker._box_extract_worker = sentinel
    picker._box_token = "box-token"
    picker._on_box_extract_result(BoxExtractResult(plan=plan, outputs={}, completed=0))
    picker._box_extract_worker = None
    picker._box_token = None
    assert warned and "No sounding in that box" in warned[0]
    assert picker._box_window is None


def test_analysis_ready_populates_the_window(picker, monkeypatch, tmp_path):
    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8), target_points=16
    )
    outputs = _write_variants(plan, tmp_path)
    analysis = ba.analyze_box(plan, outputs, tiers=(ba.FAST_TIER,))
    sentinel = SimpleNamespace()
    monkeypatch.setattr(type(picker), "sender", lambda self: sentinel)
    picker._box_analysis_worker = sentinel
    picker._on_box_analysis_ready(analysis)
    picker._box_analysis_worker = None
    assert picker._box_window is not None
    assert picker._box_window.analysis() is analysis
    assert picker._box_window._current_field() == "mucape"


def test_analysis_failure_is_shown_in_the_window(picker, monkeypatch):
    sentinel = SimpleNamespace()
    monkeypatch.setattr(type(picker), "sender", lambda self: sentinel)
    picker._box_analysis_worker = sentinel
    window = picker._ensure_box_window()
    picker._on_box_analysis_failed("Box analysis failed: bad tier")
    picker._box_analysis_worker = None
    assert "bad tier" in window._status.text()


def test_composites_request_is_confirmed_before_running(picker, monkeypatch, tmp_path):
    from qtpy.QtWidgets import QMessageBox

    from sharpmod.gui_box import BoxExtractResult

    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8), target_points=16
    )
    outputs = _write_variants(plan, tmp_path)
    picker._box_extraction = BoxExtractResult(
        plan=plan, outputs=outputs, completed=len(outputs)
    )

    asked = []
    monkeypatch.setattr(
        "sharpmod.gui_picker.QMessageBox.question",
        lambda *args, **kwargs: asked.append(args[-3]) or QMessageBox.No,
    )
    started = []
    monkeypatch.setattr(
        type(picker),
        "_start_box_analysis",
        lambda self, extraction, tiers: started.append(tiers),
    )
    picker._on_box_composites_requested()
    # Declining must not start the expensive tier, and the prompt must quote a
    # duration rather than leaving the window looking hung.
    assert started == []
    assert asked and "seconds" in asked[0]

    monkeypatch.setattr(
        "sharpmod.gui_picker.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )
    picker._on_box_composites_requested()
    assert started == [(ba.FAST_TIER, ba.COMPOSITE_TIER)]


def test_composite_estimate_counts_successes_across_every_sequence_hour(
    picker, monkeypatch, tmp_path
):
    from qtpy.QtWidgets import QMessageBox

    from sharpmod.gui_box import BoxExtractResult

    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8), target_points=16
    )
    outputs = _write_variants(plan, tmp_path)
    picker._box_extraction = BoxExtractResult(
        plan=plan,
        outputs={},
        completed=len(outputs),
        fxx=0,
        hours=(0, 6),
        outputs_by_hour={0: {}, 6: outputs},
    )
    asked = []
    monkeypatch.setattr(
        "sharpmod.gui_picker.QMessageBox.question",
        lambda *args, **kwargs: asked.append(args[-3]) or QMessageBox.No,
    )

    picker._on_box_composites_requested()

    assert asked and f"for {len(outputs)} soundings" in asked[0]


def test_sounding_request_opens_a_viewer(picker, monkeypatch, tmp_path):
    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8), target_points=16
    )
    outputs = _write_variants(plan, tmp_path)
    path = outputs[sorted(outputs)[0]]
    shown = []
    monkeypatch.setattr(
        type(picker),
        "_show_sounding",
        lambda self, col, stn, title=None: shown.append((stn, title)),
    )
    picker._on_box_sounding_requested(path, "Box r000c000  37.00, -96.50")
    assert len(shown) == 1
    assert "Box r000c000" in shown[0][1]


def test_a_bad_sounding_request_warns_instead_of_raising(picker, monkeypatch):
    warned = []
    monkeypatch.setattr(
        "sharpmod.gui_picker.QMessageBox.warning",
        lambda *args, **kwargs: warned.append(args[-1]),
    )
    picker._on_box_sounding_requested("does-not-exist.npz", "Box")
    assert warned and "could not be opened" in warned[0]


# -- lifecycle ------------------------------------------------------------- #


def test_closing_the_box_window_removes_its_temporary_soundings(picker, tmp_path):
    scratch = tmp_path / "box-run"
    scratch.mkdir()
    (scratch / "r000c000.npz").write_bytes(b"placeholder")
    picker._box_output_dir = str(scratch)
    picker._ensure_box_window()
    picker._on_box_window_destroyed()
    assert not scratch.exists()
    assert picker._box_window is None
    assert picker._box_extraction is None


def test_temporary_soundings_survive_while_a_worker_still_runs(picker, tmp_path):
    scratch = tmp_path / "box-live"
    scratch.mkdir()
    picker._box_output_dir = str(scratch)
    picker._box_extract_worker = object()
    picker._on_box_window_destroyed()
    picker._box_extract_worker = None
    assert scratch.exists()


def test_closing_during_analysis_suppresses_result_and_defers_cleanup(
    picker, monkeypatch, tmp_path
):
    scratch = tmp_path / "box-analysis-live"
    scratch.mkdir()
    interrupted = []

    class FakeWorker:
        def requestInterruption(self):  # noqa: N802 - mirrors QThread
            interrupted.append(True)

        def deleteLater(self):  # noqa: N802 - mirrors QObject
            pass

    worker = FakeWorker()
    picker._box_output_dir = str(scratch)
    picker._box_extraction = object()
    picker._box_analysis_worker = worker
    picker._ensure_box_window()
    picker._on_box_window_destroyed()

    assert interrupted == [True]
    assert scratch.exists()
    assert picker._box_output_dir == str(scratch)

    monkeypatch.setattr(type(picker), "sender", lambda self: worker)
    picker._on_box_analysis_ready(object())
    assert picker._box_window is None

    picker._on_box_analysis_finished()
    assert not scratch.exists()
    assert picker._box_output_dir is None


def test_shutdown_clears_box_state_and_scratch(picker, tmp_path):
    scratch = tmp_path / "box-shutdown"
    scratch.mkdir()
    picker._box_output_dir = str(scratch)
    picker._shutdown_model_cache()
    assert picker._box_extract_worker is None
    assert picker._box_analysis_worker is None
    assert not scratch.exists()


def test_cancel_targets_the_box_workers(picker):
    interrupted = []

    class FakeWorker:
        def requestInterruption(self):  # noqa: N802 - mirrors QThread
            interrupted.append(self)

    picker._box_extract_worker = FakeWorker()
    picker._cancel_box_operation()
    assert len(interrupted) == 1
    picker._box_extract_worker = None


def test_cancel_button_routes_to_the_box_run(picker, monkeypatch):
    cancelled = []
    monkeypatch.setattr(
        type(picker), "_cancel_box_operation", lambda self: cancelled.append(True)
    )
    picker._box_extract_worker = object()
    picker._cancel_model_fetch()
    picker._box_extract_worker = None
    assert cancelled == [True]


# -- shared job ownership -------------------------------------------------- #


def test_box_extract_reports_saved_request_cancel_and_counts(
    picker, monkeypatch, qt_app
):
    from types import SimpleNamespace

    _select_hrrr(picker)
    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8), target_points=4
    )
    picker._box_mode = "field"
    monkeypatch.setattr(
        "sharpmod.gui_box.BoxExtractWorker.start", lambda self: None
    )
    picker._start_box_extraction(plan)
    worker = picker._box_extract_worker
    token = picker._box_token
    assert picker._model_job.snapshot.state == "running"
    assert picker._model_job.snapshot.token == token
    assert worker._sharpmod_box_token == token

    monkeypatch.setattr(type(picker), "sender", lambda self: worker)
    picker._on_box_progress("working", 1, 4)
    qt_app.processEvents()
    assert picker._model_job.snapshot.done == 1
    assert picker._model_job.snapshot.total == 4

    picker._model_job.cancel_button.click()
    qt_app.processEvents()
    assert picker._model_job.snapshot.state == "cancelling"

    stale = SimpleNamespace(_sharpmod_box_token="other-box")
    monkeypatch.setattr(type(picker), "sender", lambda self: stale)
    picker._on_box_progress("working", 4, 4)
    assert picker._model_job.snapshot.done == 1

    monkeypatch.setattr(type(picker), "sender", lambda self: worker)
    picker._on_box_extract_finished()
    qt_app.processEvents()
    assert picker._box_extract_worker is None


# -- end to end through a faked extractor --------------------------------- #


def test_full_box_flow_with_a_faked_batch_extractor(picker, monkeypatch, tmp_path):
    """Drive plan -> extract -> analyze without touching the network."""
    _select_hrrr(picker)
    source = dict(np.load(HRRR_NPZ, allow_pickle=False))

    class FakeRunner:
        def __init__(self):
            pass

        def cancel(self):
            pass

        def run(self, requests, **kwargs):
            # Write a real sounding for each request, into the directory the
            # worker actually chose, so the analysis stage reads genuine data
            # through the real path the picker built.
            from pathlib import Path

            root = Path(kwargs["output_dir"])
            progress_callback = kwargs["progress_callback"]
            for item in requests:
                target = root / item.output
                target.parent.mkdir(parents=True, exist_ok=True)
                np.savez(target, **source)
                progress_callback({"event": "completed", "request_id": item.id})
            return SimpleNamespace(completed=len(requests), failed=0, cancelled=0)

    monkeypatch.setattr(gui_batch_process, "IsolatedBatchRunner", FakeRunner)

    # This exercises the field workspace, so the dialog is answered with that
    # mode rather than the averaging default.
    def accept_minimal_field_plan(dialog):
        # This test covers picker orchestration, not lattice resolution. Four
        # real soundings exercise the same plan -> extract -> analyze path while
        # the dialog/planner modules separately cover larger grids.
        dialog._points.setValue(4)
        dialog._replan()
        return QDialog.Accepted

    monkeypatch.setattr(
        "sharpmod.gui_box.BoxPlanDialog.exec", accept_minimal_field_plan
    )
    monkeypatch.setattr("sharpmod.gui_box.BoxPlanDialog.mode", lambda self: "field")

    from sharpmod.gui_box import BoxAnalysisWorker, BoxExtractWorker

    # Run both stages synchronously so the test does not depend on the event
    # loop, but through the real picker handlers.
    monkeypatch.setattr(BoxExtractWorker, "start", BoxExtractWorker.run)
    monkeypatch.setattr(BoxAnalysisWorker, "start", BoxAnalysisWorker.run)

    picker._model_map.set_box((36.0, -96.5, 37.4, -94.8))
    picker._model_on_box_selected(36.0, -96.5, 37.4, -94.8)
    picker._model_extract_box()

    window = picker._box_window
    assert window is not None
    analysis = window.analysis()
    assert analysis is not None
    assert len(analysis.analyzed) == len(analysis.plan.requestable_points)
    assert window._current_field() == "mucape"
    assert window._ranked.rowCount() > 0
    # And a cell can be opened as an ordinary sounding.
    shown = []
    monkeypatch.setattr(
        type(picker),
        "_show_sounding",
        lambda self, col, stn, title=None: shown.append(title),
    )
    window._map.set_selected_cell(0, 0)
    window._open_selected()
    assert len(shown) == 1


def test_shift_drag_on_the_model_map_reaches_the_box_handler(picker, monkeypatch):
    from sharpmod.tests.test_gui_box_map import _MouseEvent

    _select_hrrr(picker)
    picker._model_map.resize(600, 400)
    seen = []
    monkeypatch.setattr(
        type(picker), "_model_on_box_selected", lambda self, *args: seen.append(args)
    )
    view = picker._model_map
    view.mousePressEvent(_MouseEvent((150, 120), modifiers=Qt.ShiftModifier))
    view.mouseMoveEvent(_MouseEvent((420, 320), modifiers=Qt.ShiftModifier))
    view.mouseReleaseEvent(_MouseEvent((420, 320), modifiers=Qt.ShiftModifier))
    assert len(seen) == 1
    lat0, lon0, lat1, lon1 = seen[0]
    assert lat0 < lat1 and lon0 < lon1


# -- the box mean: one sounding for the whole area ------------------------ #


def _mean_extraction(tmp_path, *, target_points=16):
    """A completed single-hour extraction, ready to be averaged."""
    from datetime import datetime, timezone

    from sharpmod.gui_box import BoxExtractResult

    plan = plan_box_samples(
        "hrrr",
        BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8),
        target_points=target_points,
    )
    source = dict(np.load(HRRR_NPZ, allow_pickle=False))
    outputs = {}
    for node in plan.requestable_points:
        arrays = {
            key: (value.copy() if hasattr(value, "copy") else value)
            for key, value in source.items()
        }
        arrays["lat"] = float(node.lat)
        arrays["lon"] = float(node.lon)
        path = tmp_path / f"{node.request_id}.npz"
        np.savez(path, **arrays)
        outputs[node.request_id] = str(path)
    return BoxExtractResult(
        plan=plan,
        outputs=outputs,
        run_time=datetime(2026, 9, 3, 0, tzinfo=timezone.utc),
        valid_time=datetime(2026, 9, 3, 18, tzinfo=timezone.utc),
        fxx=18,
        completed=len(outputs),
        output_dir=str(tmp_path),
    )



# -- cancel race ------------------------------------------------------------- #


def test_box_result_published_after_cancel_is_rejected(
        picker, monkeypatch, qt_app, tmp_path):
    """A result that arrives while cancelling must not publish over Cancel."""
    _select_hrrr(picker)
    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8), target_points=4
    )
    picker._box_mode = "field"
    monkeypatch.setattr(
        "sharpmod.gui_box.BoxExtractWorker.start", lambda self: None
    )
    picker._start_box_extraction(plan)
    worker = picker._box_extract_worker
    published = []
    monkeypatch.setattr(
        type(picker), "_ensure_box_window",
        lambda self: published.append("window"))
    monkeypatch.setattr(
        type(picker), "_start_box_mean",
        lambda self, extraction: published.append("mean"),
    )
    monkeypatch.setattr(
        type(picker), "_start_box_analysis",
        lambda self, *args: published.append("analysis"),
    )
    picker._model_job.cancel_button.click()
    qt_app.processEvents()
    assert picker._model_job.snapshot.state == "cancelling"
    extraction = _mean_extraction(tmp_path)
    monkeypatch.setattr(type(picker), "sender", lambda self: worker)
    picker._on_box_extract_result(extraction)
    qt_app.processEvents()
    assert published == []
    assert picker._box_extraction is None
    assert picker._model_job.snapshot.state == "cancelling"
    assert picker._model_job.snapshot.counts.completed == 0
    worker.deleteLater()

def test_a_finished_extraction_is_averaged_by_default(picker, monkeypatch, tmp_path):
    """The default mode collapses the box instead of opening the workspace."""
    _select_hrrr(picker)
    extraction = _mean_extraction(tmp_path)

    averaged = []
    monkeypatch.setattr(
        type(picker),
        "_start_box_mean",
        lambda self, extraction: averaged.append(extraction),
    )
    sentinel = SimpleNamespace(_sharpmod_box_token="box-token")
    monkeypatch.setattr(type(picker), "sender", lambda self: sentinel)
    picker._box_extract_worker = sentinel
    picker._box_token = "box-token"
    picker._on_box_extract_result(extraction)
    picker._box_extract_worker = None
    picker._box_token = None

    assert averaged == [extraction]
    # No workspace window is created for a mean.
    assert picker._box_window is None


def test_the_mean_worker_produces_a_sounding_the_picker_can_open(
    picker, monkeypatch, tmp_path
):
    """Drive the real worker and the real handler, then check what opened."""
    from sharpmod.gui_box import BoxMeanWorker

    _select_hrrr(picker)
    extraction = _mean_extraction(tmp_path)
    picker._box_extraction = extraction
    picker._box_mode = "mean"

    shown = []
    monkeypatch.setattr(
        type(picker),
        "_show_sounding",
        lambda self, col, stn, title=None: (
            shown.append((col, stn, title))
            or SimpleNamespace(destroyed=SimpleNamespace(connect=lambda *_a: None))
        ),
    )
    # The retention hook wants a real viewer; the sounding itself is the subject.
    monkeypatch.setattr(
        "sharpmod.gui_picker._retain_model_data_until_close", lambda *a, **k: None
    )

    # Run synchronously through the picker's own wiring, so the signal
    # connections are the real ones rather than reproduced by the test.
    monkeypatch.setattr(BoxMeanWorker, "start", BoxMeanWorker.run)
    monkeypatch.setattr(type(picker), "sender", lambda self: self._box_mean_worker)
    picker._start_box_mean(extraction)
    picker._box_mean_worker = None

    assert len(shown) == 1
    _col, stn, title = shown[0]
    # A coordinate label is what lets the automatic town lookup run.
    assert stn.startswith("HRRR ")
    assert "box mean" in title


def test_the_mean_sounding_carries_the_overlay_and_town_preconditions(picker, tmp_path):
    """The gates that decide the outlook overlay and the town name."""
    from datetime import datetime

    from sharpmod import render
    from sharpmod.gui_box import BoxMeanWorker
    from sharpmod.gui_viewer import _locator_overlay_point
    from sharpmod.place_names import needs_town_name
    from sharpmod.spc_outlook import covers_location

    _select_hrrr(picker)
    extraction = _mean_extraction(tmp_path)
    written = {}
    worker = BoxMeanWorker(extraction, parent=picker)
    worker.ready.connect(lambda path, prof: written.update(path=path))
    worker.run()

    prof_col, stn_id = render.decode(written["path"])
    # 1. a real valid time, or the overlay fetch returns immediately
    assert isinstance(prof_col.getCurrentDate(), datetime)
    # 2. a resolvable point inside the outlook's coverage
    point = _locator_overlay_point(prof_col)
    assert point is not None
    assert covers_location(*point) is True
    # 3. a label generic enough for the town lookup to replace
    assert needs_town_name(stn_id, "HRRR") is True


def test_a_box_that_cannot_be_averaged_reports_instead_of_crashing(
    picker, monkeypatch, tmp_path
):
    from sharpmod.gui_box import BoxMeanWorker

    _select_hrrr(picker)
    extraction = _mean_extraction(tmp_path)
    # Point every output at a file that is not there.
    broken = type(extraction)(
        plan=extraction.plan,
        outputs={key: str(tmp_path / "gone.npz") for key in extraction.outputs},
        run_time=extraction.run_time,
        valid_time=extraction.valid_time,
        fxx=extraction.fxx,
        completed=extraction.completed,
        output_dir=str(tmp_path),
    )
    failures = []
    worker = BoxMeanWorker(broken, parent=picker)
    worker.failed.connect(failures.append)
    worker.run()
    assert len(failures) == 1
    assert "could not be averaged" in failures[0]


def test_the_mean_worker_is_tracked_as_a_busy_box_operation(picker):
    """A second box must not start while an average is still running."""
    sentinel = SimpleNamespace()
    picker._box_mean_worker = sentinel
    try:
        picker._model_update_fetch_state()
        assert not picker._model_fetch_btn.isEnabled()
    finally:
        picker._box_mean_worker = None
    picker._model_update_fetch_state()


def test_confirming_extraction_releases_box_mode_but_keeps_the_preview(
        picker, monkeypatch):
    """The explicit fetch gate returns left-drag to pan while work starts."""
    _select_hrrr(picker)
    monkeypatch.setattr(
        "sharpmod.gui_box.BoxPlanDialog.exec", lambda self: QDialog.Accepted
    )
    monkeypatch.setattr(
        type(picker),
        "_start_box_extraction",
        lambda self, plan, hours=None, mode=None, fxx=None: None,
    )

    picker._model_box_btn.setChecked(True)
    assert picker._model_map.box_mode() is True

    # The map commits the rectangle and then emits, so mirror that order.
    picker._model_map.set_box((34.0, -99.0, 37.0, -95.0))
    picker._model_on_box_selected(34.0, -99.0, 37.0, -95.0)
    assert picker._model_box_btn.isChecked() is True
    picker._model_extract_box()

    # The mode is released, so the next drag pans instead of drawing again.
    assert picker._model_box_btn.isChecked() is False
    assert picker._model_map.box_mode() is False
    # But the rectangle and its sampled lattice stay on screen.
    assert picker._model_map.box() is not None
    assert len(picker._model_map._box_nodes) > 0


def test_turning_box_mode_off_by_hand_preserves_the_rectangle(picker):
    """Tool state and selected-area state are independent (T23.1)."""
    _select_hrrr(picker)
    picker._model_box_btn.setChecked(True)
    picker._model_map.set_box((34.0, -99.0, 37.0, -95.0))
    assert picker._model_map.box() is not None

    picker._model_box_btn.setChecked(False)

    assert picker._model_map.box() == (34.0, -99.0, 37.0, -95.0)


def test_the_mean_sounding_title_states_it_is_an_average(picker, monkeypatch, tmp_path):
    """The window title has to name the average and its member count."""
    from sharpmod.gui_box import BoxMeanWorker

    _select_hrrr(picker)
    extraction = _mean_extraction(tmp_path)
    picker._box_extraction = extraction
    picker._box_mode = "mean"

    shown = []
    monkeypatch.setattr(
        type(picker),
        "_show_sounding",
        lambda self, col, stn, title=None: shown.append(title),
    )
    monkeypatch.setattr(
        "sharpmod.gui_picker._retain_model_data_until_close", lambda *a, **k: None
    )
    monkeypatch.setattr(BoxMeanWorker, "start", BoxMeanWorker.run)
    monkeypatch.setattr(type(picker), "sender", lambda self: self._box_mean_worker)

    picker._start_box_mean(extraction)
    picker._box_mean_worker = None

    assert len(shown) == 1
    assert "box mean of" in shown[0]
    assert str(len(extraction.outputs)) in shown[0]
