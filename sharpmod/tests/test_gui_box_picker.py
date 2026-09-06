"""Box sounding wiring inside the forecast-model picker tab."""

from types import SimpleNamespace

import numpy as np
import pytest

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QDialog

from sharpmod import batch_extract
from sharpmod import box_analysis as ba
from sharpmod.box_sounding import BoxRegion, plan_box_samples
from sharpmod.tests._examples import examples_dir
from sharpmod.tests.test_box_analysis import _write_variants


HRRR_NPZ = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"

pytestmark = [
    pytest.mark.skipif(
        not HRRR_NPZ.is_file(), reason="no HRRR .npz example sounding"),
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
        PickerWindow, "_queue_model_availability", lambda self, *a: None)
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
    picker._model_box_btn.setChecked(False)
    assert picker._model_map.box_mode() is False
    # Leaving box mode also drops any drawn rectangle.
    assert picker._model_map.box() is None


def test_map_box_signal_is_connected_to_the_picker(picker, monkeypatch):
    seen = []
    monkeypatch.setattr(
        type(picker), "_model_on_box_selected",
        lambda self, *args: seen.append(args))
    picker._model_map.boxSelected.emit(34.0, -99.0, 37.0, -95.0)
    assert seen == [(34.0, -99.0, 37.0, -95.0)]


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


def test_a_box_is_refused_while_another_model_fetch_runs(picker, monkeypatch):
    _select_hrrr(picker)
    shown = []
    monkeypatch.setattr(
        "sharpmod.gui_picker.QMessageBox.information",
        lambda *args, **kwargs: shown.append(args[-1]))
    picker._model_worker = object()
    picker._model_on_box_selected(34.0, -99.0, 37.0, -95.0)
    picker._model_worker = None
    assert shown and "already in progress" in shown[0]
    assert picker._box_extract_worker is None


def test_a_second_box_is_refused_while_one_runs(picker, monkeypatch):
    _select_hrrr(picker)
    shown = []
    monkeypatch.setattr(
        "sharpmod.gui_picker.QMessageBox.information",
        lambda *args, **kwargs: shown.append(args[-1]))
    picker._box_extract_worker = object()
    picker._model_on_box_selected(34.0, -99.0, 37.0, -95.0)
    picker._box_extract_worker = None
    assert shown and "box sounding is already in progress" in shown[0]


def test_cancelling_the_plan_dialog_clears_the_rectangle(picker, monkeypatch):
    _select_hrrr(picker)
    monkeypatch.setattr(
        "sharpmod.gui_box.BoxPlanDialog.exec",
        lambda self: QDialog.Rejected)
    picker._model_map.set_box((34.0, -99.0, 37.0, -95.0))
    picker._model_on_box_selected(34.0, -99.0, 37.0, -95.0)
    assert picker._model_map.box() is None
    assert picker._box_extract_worker is None


def test_accepting_the_plan_previews_the_lattice_and_starts_a_worker(
    picker, monkeypatch
):
    _select_hrrr(picker)
    monkeypatch.setattr(
        "sharpmod.gui_box.BoxPlanDialog.exec",
        lambda self: QDialog.Accepted)
    started = []
    monkeypatch.setattr(
        type(picker), "_start_box_extraction",
        lambda self, plan, hours=None, mode=None, fxx=None: started.append(
            (plan, hours, mode, fxx)))
    picker._model_on_box_selected(34.0, -99.0, 37.0, -95.0)
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
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8),
        target_points=16)
    outputs = _write_variants(plan, tmp_path)
    extraction = BoxExtractResult(
        plan=plan, outputs=outputs, run_time=None, fxx=18,
        completed=len(outputs), output_dir=str(tmp_path))

    analyses = []
    monkeypatch.setattr(
        type(picker), "_start_box_analysis",
        lambda self, extraction, tiers: analyses.append(tiers))
    # The handler guards on the sender, so stand in as the active worker.
    sentinel = SimpleNamespace()
    monkeypatch.setattr(type(picker), "sender", lambda self: sentinel)
    picker._box_extract_worker = sentinel
    # This is the field workspace path; the default now averages instead.
    picker._box_mode = "field"
    picker._on_box_extract_result(extraction)
    picker._box_extract_worker = None

    assert analyses == [(ba.FAST_TIER,)]
    assert picker._box_window is not None
    assert picker._box_extraction is extraction
    region = plan.region
    assert picker._box_window._map.box() == pytest.approx(
        (region.lat0, region.lon0, region.lat1, region.lon1))
    assert "soundings from one" in picker._box_window._status.text()


def test_an_empty_extraction_warns_and_opens_nothing(picker, monkeypatch):
    from sharpmod.gui_box import BoxExtractResult

    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8),
        target_points=16)
    warned = []
    monkeypatch.setattr(
        "sharpmod.gui_picker.QMessageBox.warning",
        lambda *args, **kwargs: warned.append(args[-1]))
    sentinel = SimpleNamespace()
    monkeypatch.setattr(type(picker), "sender", lambda self: sentinel)
    picker._box_extract_worker = sentinel
    picker._on_box_extract_result(
        BoxExtractResult(plan=plan, outputs={}, completed=0))
    picker._box_extract_worker = None
    assert warned and "No sounding in that box" in warned[0]
    assert picker._box_window is None


def test_analysis_ready_populates_the_window(picker, monkeypatch, tmp_path):
    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8),
        target_points=16)
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


def test_composites_request_is_confirmed_before_running(
    picker, monkeypatch, tmp_path
):
    from qtpy.QtWidgets import QMessageBox

    from sharpmod.gui_box import BoxExtractResult

    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8),
        target_points=16)
    outputs = _write_variants(plan, tmp_path)
    picker._box_extraction = BoxExtractResult(
        plan=plan, outputs=outputs, completed=len(outputs))

    asked = []
    monkeypatch.setattr(
        "sharpmod.gui_picker.QMessageBox.question",
        lambda *args, **kwargs: asked.append(args[-3]) or QMessageBox.No)
    started = []
    monkeypatch.setattr(
        type(picker), "_start_box_analysis",
        lambda self, extraction, tiers: started.append(tiers))
    picker._on_box_composites_requested()
    # Declining must not start the expensive tier, and the prompt must quote a
    # duration rather than leaving the window looking hung.
    assert started == []
    assert asked and "seconds" in asked[0]

    monkeypatch.setattr(
        "sharpmod.gui_picker.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.Yes)
    picker._on_box_composites_requested()
    assert started == [(ba.FAST_TIER, ba.COMPOSITE_TIER)]


def test_sounding_request_opens_a_viewer(picker, monkeypatch, tmp_path):
    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8),
        target_points=16)
    outputs = _write_variants(plan, tmp_path)
    path = outputs[sorted(outputs)[0]]
    shown = []
    monkeypatch.setattr(
        type(picker), "_show_sounding",
        lambda self, col, stn, title=None: shown.append((stn, title)))
    picker._on_box_sounding_requested(path, "Box r000c000  37.00, -96.50")
    assert len(shown) == 1
    assert "Box r000c000" in shown[0][1]


def test_a_bad_sounding_request_warns_instead_of_raising(picker, monkeypatch):
    warned = []
    monkeypatch.setattr(
        "sharpmod.gui_picker.QMessageBox.warning",
        lambda *args, **kwargs: warned.append(args[-1]))
    picker._on_box_sounding_requested("does-not-exist.npz", "Box")
    assert warned and "could not be opened" in warned[0]


# -- lifecycle ------------------------------------------------------------- #


def test_closing_the_box_window_removes_its_temporary_soundings(
    picker, tmp_path
):
    scratch = tmp_path / "box-run"
    scratch.mkdir()
    (scratch / "r000c000.npz").write_bytes(b"placeholder")
    picker._box_output_dir = str(scratch)
    picker._ensure_box_window()
    picker._on_box_window_destroyed()
    assert not scratch.exists()
    assert picker._box_window is None
    assert picker._box_extraction is None


def test_temporary_soundings_survive_while_a_worker_still_runs(
    picker, tmp_path
):
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
        type(picker), "_cancel_box_operation",
        lambda self: cancelled.append(True))
    picker._box_extract_worker = object()
    picker._cancel_model_fetch()
    picker._box_extract_worker = None
    assert cancelled == [True]


# -- end to end through a faked extractor --------------------------------- #


def test_full_box_flow_with_a_faked_batch_extractor(
    picker, monkeypatch, tmp_path
):
    """Drive plan -> extract -> analyze without touching the network."""
    _select_hrrr(picker)
    source = dict(np.load(HRRR_NPZ, allow_pickle=False))

    class FakeExtractor:
        def __init__(self, progress_callback=None):
            self.progress_callback = progress_callback

        def cancel(self):
            pass

        def run(self, requests, **kwargs):
            # Write a real sounding for each request, into the directory the
            # worker actually chose, so the analysis stage reads genuine data
            # through the real path the picker built.
            from pathlib import Path

            root = Path(kwargs["output_dir"])
            for item in requests:
                target = root / item.output
                target.parent.mkdir(parents=True, exist_ok=True)
                np.savez(target, **source)
                self.progress_callback({
                    "event": "completed", "request_id": item.id})
            return SimpleNamespace(
                completed=len(requests), failed=0, cancelled=0)

    monkeypatch.setattr(batch_extract, "BatchExtractor", FakeExtractor)
    # This exercises the field workspace, so the dialog is answered with that
    # mode rather than the averaging default.
    monkeypatch.setattr(
        "sharpmod.gui_box.BoxPlanDialog.exec",
        lambda self: QDialog.Accepted)
    monkeypatch.setattr(
        "sharpmod.gui_box.BoxPlanDialog.mode", lambda self: "field")

    from sharpmod.gui_box import BoxAnalysisWorker, BoxExtractWorker

    # Run both stages synchronously so the test does not depend on the event
    # loop, but through the real picker handlers.
    monkeypatch.setattr(BoxExtractWorker, "start", BoxExtractWorker.run)
    monkeypatch.setattr(BoxAnalysisWorker, "start", BoxAnalysisWorker.run)

    picker._model_on_box_selected(36.0, -96.5, 37.4, -94.8)

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
        type(picker), "_show_sounding",
        lambda self, col, stn, title=None: shown.append(title))
    window._map.set_selected_cell(0, 0)
    window._open_selected()
    assert len(shown) == 1


def test_shift_drag_on_the_model_map_reaches_the_box_handler(
    picker, monkeypatch
):
    from sharpmod.tests.test_gui_box_map import _MouseEvent

    _select_hrrr(picker)
    picker._model_map.resize(600, 400)
    seen = []
    monkeypatch.setattr(
        type(picker), "_model_on_box_selected",
        lambda self, *args: seen.append(args))
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
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8),
        target_points=target_points)
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


def test_a_finished_extraction_is_averaged_by_default(picker, monkeypatch,
                                                      tmp_path):
    """The default mode collapses the box instead of opening the workspace."""
    _select_hrrr(picker)
    extraction = _mean_extraction(tmp_path)

    averaged = []
    monkeypatch.setattr(
        type(picker), "_start_box_mean",
        lambda self, extraction: averaged.append(extraction))
    sentinel = SimpleNamespace()
    monkeypatch.setattr(type(picker), "sender", lambda self: sentinel)
    picker._box_extract_worker = sentinel
    picker._on_box_extract_result(extraction)
    picker._box_extract_worker = None

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
        type(picker), "_show_sounding",
        lambda self, col, stn, title=None: shown.append((col, stn, title))
        or SimpleNamespace(destroyed=SimpleNamespace(connect=lambda *_a: None)))
    # The retention hook wants a real viewer; the sounding itself is the subject.
    monkeypatch.setattr(
        "sharpmod.gui_picker._retain_model_data_until_close",
        lambda *a, **k: None)

    # Run synchronously through the picker's own wiring, so the signal
    # connections are the real ones rather than reproduced by the test.
    monkeypatch.setattr(BoxMeanWorker, "start", BoxMeanWorker.run)
    monkeypatch.setattr(
        type(picker), "sender", lambda self: self._box_mean_worker)
    picker._start_box_mean(extraction)
    picker._box_mean_worker = None

    assert len(shown) == 1
    _col, stn, title = shown[0]
    # A coordinate label is what lets the automatic town lookup run.
    assert stn.startswith("HRRR ")
    assert "box mean" in title


def test_the_mean_sounding_carries_the_overlay_and_town_preconditions(
    picker, tmp_path
):
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


def test_accepting_a_box_releases_box_mode_but_keeps_the_preview(
    picker, monkeypatch
):
    """Leaving the mode armed turned the next pan into a second rectangle."""
    _select_hrrr(picker)
    monkeypatch.setattr(
        "sharpmod.gui_box.BoxPlanDialog.exec",
        lambda self: QDialog.Accepted)
    monkeypatch.setattr(
        type(picker), "_start_box_extraction",
        lambda self, plan, hours=None, mode=None, fxx=None: None)

    picker._model_box_btn.setChecked(True)
    assert picker._model_map.box_mode() is True

    # The map commits the rectangle and then emits, so mirror that order.
    picker._model_map.set_box((34.0, -99.0, 37.0, -95.0))
    picker._model_on_box_selected(34.0, -99.0, 37.0, -95.0)

    # The mode is released, so the next drag pans instead of drawing again.
    assert picker._model_box_btn.isChecked() is False
    assert picker._model_map.box_mode() is False
    # But the rectangle and its sampled lattice stay on screen.
    assert picker._model_map.box() is not None
    assert len(picker._model_map._box_nodes) > 0


def test_turning_box_mode_off_by_hand_still_clears_the_rectangle(picker):
    """Only the automatic release preserves the box."""
    _select_hrrr(picker)
    picker._model_box_btn.setChecked(True)
    picker._model_map.set_box((34.0, -99.0, 37.0, -95.0))
    assert picker._model_map.box() is not None

    picker._model_box_btn.setChecked(False)

    assert picker._model_map.box() is None


def test_the_mean_sounding_title_states_it_is_an_average(
    picker, monkeypatch, tmp_path
):
    """The window title has to name the average and its member count."""
    from sharpmod.gui_box import BoxMeanWorker

    _select_hrrr(picker)
    extraction = _mean_extraction(tmp_path)
    picker._box_extraction = extraction
    picker._box_mode = "mean"

    shown = []
    monkeypatch.setattr(
        type(picker), "_show_sounding",
        lambda self, col, stn, title=None: shown.append(title))
    monkeypatch.setattr(
        "sharpmod.gui_picker._retain_model_data_until_close",
        lambda *a, **k: None)
    monkeypatch.setattr(BoxMeanWorker, "start", BoxMeanWorker.run)
    monkeypatch.setattr(
        type(picker), "sender", lambda self: self._box_mean_worker)

    picker._start_box_mean(extraction)
    picker._box_mean_worker = None

    assert len(shown) == 1
    assert "box mean of" in shown[0]
    assert str(len(extraction.outputs)) in shown[0]
