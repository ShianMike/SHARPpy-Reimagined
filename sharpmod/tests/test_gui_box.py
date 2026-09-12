"""Box sounding Qt layer: plan dialog, workers, and the analysis window."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from qtpy.QtWidgets import QDialogButtonBox

from sharpmod import gui_batch_process
from sharpmod import box_analysis as ba
from sharpmod.box_sounding import BoxRegion, plan_box_samples
from sharpmod.tests._examples import examples_dir
from sharpmod.tests.test_box_analysis import _write_variants
from sharpmod.tests.test_gui_box_map import _MouseEvent


HRRR_NPZ = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"
RUN = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)

pytestmark = [
    pytest.mark.skipif(
        not HRRR_NPZ.is_file(), reason="no HRRR .npz example sounding"),
    pytest.mark.usefixtures("qt_app"),
]


def _region() -> BoxRegion:
    return BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8)


def _plan(target=16):
    return plan_box_samples("hrrr", _region(), target_points=target)


@pytest.fixture(scope="module")
def extraction(tmp_path_factory):
    from sharpmod.gui_box import BoxExtractResult

    tmp_path = tmp_path_factory.mktemp("gui-box-extraction")
    plan = _plan()
    outputs = _write_variants(plan, tmp_path)
    return BoxExtractResult(
        plan=plan, outputs=outputs, run_time=RUN,
        valid_time=RUN + timedelta(hours=18), fxx=18,
        completed=len(outputs), output_dir=str(tmp_path),
    )


@pytest.fixture(scope="module")
def analysis(extraction):
    return ba.analyze_box(
        extraction.plan, extraction.outputs, tiers=(ba.FAST_TIER,),
        run_time=extraction.run_time, fxx=18)


# -- plan dialog ----------------------------------------------------------- #


def test_plan_dialog_resolves_a_plan_and_describes_it():
    from sharpmod.gui_box import BoxPlanDialog

    dialog = BoxPlanDialog("hrrr", _region(), target_points=16)
    plan = dialog.plan()
    assert plan is not None
    assert plan.count >= 4
    text = dialog._summary.text()
    assert "HRRR" in text and "Lattice" in text and "Downloads" in text
    assert dialog._ok_button.isEnabled()


def test_plan_dialog_replans_when_the_target_changes():
    from sharpmod.gui_box import BoxPlanDialog

    dialog = BoxPlanDialog("hrrr", _region(), target_points=16)
    small = dialog.plan().count
    dialog._points.setValue(100)
    assert dialog.plan().count > small


def test_plan_dialog_caps_the_target_for_a_point_only_provider():
    from sharpmod.box_sounding import MAX_POINT_PROVIDER_POINTS
    from sharpmod.gui_box import BoxPlanDialog

    dialog = BoxPlanDialog("openmeteo-icon-global", _region())
    assert dialog._points.maximum() == MAX_POINT_PROVIDER_POINTS
    assert dialog.plan().point_only_provider is True


def test_plan_dialog_reports_an_unusable_box_instead_of_raising():
    from sharpmod.gui_box import BoxPlanDialog

    # Entirely outside the HRRR domain.
    dialog = BoxPlanDialog(
        "hrrr", BoxRegion.from_corners(-40.0, 20.0, -30.0, 30.0))
    assert dialog.plan() is None
    assert not dialog._ok_button.isEnabled()
    assert "outside" in dialog._summary.text()


def test_plan_dialog_buttons_are_wired():
    from sharpmod.gui_box import BoxPlanDialog

    dialog = BoxPlanDialog("hrrr", _region())
    box = dialog.findChild(QDialogButtonBox)
    # Averaging the box into one sounding is the ordinary case, so it is the
    # default and the confirm button names what it is about to do.
    assert dialog.mode() == "mean"
    assert box.button(QDialogButtonBox.Ok).text() == "Average"
    dialog._mode_field.setChecked(True)
    assert dialog.mode() == "field"
    assert box.button(QDialogButtonBox.Ok).text() == "Extract"


def test_plan_dialog_summary_states_the_averaging_caveat():
    """A mean's parameters are not the mean of the members' parameters."""
    from sharpmod.gui_box import BoxPlanDialog

    dialog = BoxPlanDialog("hrrr", _region())
    summary = dialog._summary.text()
    assert "averaged from" in summary
    assert "not the mean of the individual" in summary


def test_plan_dialog_hides_forecast_hours_when_averaging():
    """A mean is one rendered sounding, so an hour sequence cannot apply."""
    from sharpmod.gui_box import BoxPlanDialog

    dialog = BoxPlanDialog(
        "hrrr", _region(), available_hours=[0, 1, 2, 3], start_hour=0)
    assert dialog.mode() == "mean"
    assert dialog.hours() is None

    dialog._mode_field.setChecked(True)
    dialog._sequence_check.setChecked(True)
    assert dialog.hours() == (0, 1, 2, 3)

    # Going back to the mean drops the sequence rather than silently keeping it.
    dialog._mode_mean.setChecked(True)
    assert not dialog._sequence_check.isChecked()
    assert dialog.hours() is None


# -- extraction worker ----------------------------------------------------- #


def test_extract_worker_streams_cells_and_reports_a_result(
    tmp_path, monkeypatch
):
    from sharpmod.gui_box import BoxExtractWorker

    plan = _plan()
    seen = {}

    class FakeRunner:
        def __init__(self):
            pass

        def cancel(self):
            seen["cancelled"] = True

        def run(self, requests, **kwargs):
            seen["ids"] = [item.id for item in requests]
            seen["workers"] = kwargs["max_workers"]
            progress_callback = kwargs["progress_callback"]
            for item in requests[:-1]:
                progress_callback({
                    "event": "completed", "request_id": item.id})
            progress_callback({
                "event": "failed", "request_id": requests[-1].id,
                "error": {"message": "no surface contract"},
            })
            return SimpleNamespace(
                completed=len(requests) - 1, failed=1, cancelled=0)

    monkeypatch.setattr(gui_batch_process, "IsolatedBatchRunner", FakeRunner)
    ready, failed, results, stages = [], [], [], []
    worker = BoxExtractWorker(plan, RUN, 18, tmp_path)
    worker.point_ready.connect(
        lambda path, row, col: ready.append((path, row, col)))
    worker.point_failed.connect(
        lambda row, col, message: failed.append((row, col, message)))
    worker.progress.connect(
        lambda stage, done, total: stages.append((stage, done, total)))
    worker.result_ready.connect(results.append)
    worker.run()

    nodes = plan.requestable_points
    assert seen["ids"] == [node.request_id for node in nodes]
    # A box is one model hour, so extra workers would only contend for it.
    assert seen["workers"] == 1
    assert len(ready) == len(nodes) - 1
    # Every streamed cell carries its lattice position.
    assert ready[0][1:] == (nodes[0].row, nodes[0].col)
    assert failed == [
        (nodes[-1].row, nodes[-1].col, "no surface contract")]
    result = results[0]
    assert result.completed == len(nodes) - 1
    assert result.failed == 1
    assert len(result.outputs) == len(nodes) - 1
    assert result.valid_time == RUN + timedelta(hours=18)
    assert result.ok is True
    assert stages[-1][1:] == (len(nodes) - 1, len(nodes))


def test_extract_worker_reports_a_sampling_failure(tmp_path):
    from sharpmod.gui_box import BoxExtractWorker

    messages = []
    worker = BoxExtractWorker(object(), RUN, 0, tmp_path)
    worker.failed.connect(messages.append)
    worker.run()
    assert messages and "Box sampling failed" in messages[0]


def test_extract_worker_reports_an_extraction_failure(tmp_path, monkeypatch):
    from sharpmod.gui_box import BoxExtractWorker

    class Boom:
        def __init__(self):
            pass

        def cancel(self):
            pass

        def run(self, requests, **kwargs):
            raise RuntimeError("mirror unavailable")

    monkeypatch.setattr(gui_batch_process, "IsolatedBatchRunner", Boom)
    messages = []
    worker = BoxExtractWorker(_plan(), RUN, 0, tmp_path)
    worker.failed.connect(messages.append)
    worker.run()
    assert messages and "mirror unavailable" in messages[0]


def test_extract_worker_forwards_cancellation_to_the_extractor(
    tmp_path, monkeypatch
):
    from sharpmod.gui_box import BoxExtractWorker

    state = {}

    class Slow:
        def __init__(self):
            state["runner"] = self
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

        def run(self, requests, **kwargs):
            worker.requestInterruption()
            return SimpleNamespace(completed=0, failed=0, cancelled=1)

    monkeypatch.setattr(gui_batch_process, "IsolatedBatchRunner", Slow)
    worker = BoxExtractWorker(_plan(), RUN, 0, tmp_path)
    worker.run()
    assert state["runner"].cancelled is True


# -- analysis worker ------------------------------------------------------- #


def test_analysis_worker_produces_fields(extraction):
    from sharpmod.gui_box import BoxAnalysisWorker

    ready, ticks = [], []
    worker = BoxAnalysisWorker(extraction, tiers=(ba.FAST_TIER,))
    worker.ready.connect(ready.append)
    worker.progress.connect(lambda done, total: ticks.append((done, total)))
    worker.run()
    assert len(ready) == 1
    result = ready[0]
    assert len(result.analyzed) == len(extraction.outputs)
    assert "mucape" in {i.key for i in result.available_parameters()}
    assert ticks[-1] == (len(extraction.outputs), len(extraction.outputs))


def test_analysis_worker_reports_a_failure(extraction):
    from sharpmod.gui_box import BoxAnalysisWorker

    messages = []
    worker = BoxAnalysisWorker(extraction, tiers=("nope",))
    worker.failed.connect(messages.append)
    worker.run()
    assert messages and "Box analysis failed" in messages[0]


# -- analysis window ------------------------------------------------------- #


@pytest.fixture
def window(extraction, analysis):
    from sharpmod.gui_box import BoxAnalysisWindow

    view = BoxAnalysisWindow()
    view.resize(1000, 700)
    view.set_extraction(extraction)
    view.set_analysis(analysis)
    yield view
    view.close()
    view.deleteLater()


def test_window_frames_the_box_and_shows_the_model_domain(window, extraction):
    region = extraction.plan.region
    assert window._map.box() == pytest.approx(
        (region.lat0, region.lon0, region.lat1, region.lon1))
    # The view was framed on the box rather than left on the default area.
    assert window._map._lon0 < region.lon0
    assert window._map._lon1 > region.lon1
    assert window._map._domain_bounds is not None


def test_window_offers_only_available_fields_grouped(window):
    keys = [
        window._field_combo.itemData(index)
        for index in range(window._field_combo.count())
    ]
    present = [key for key in keys if key]
    assert "mucape" in present
    # The composite tier was not computed, so it must not be offered.
    assert "stp_cin" not in present
    # Groups are separated, so at least one item is a separator with no data.
    assert len(keys) > len(present)
    labels = [
        window._field_combo.itemText(index)
        for index in range(window._field_combo.count())
    ]
    assert any(text.startswith("Instability:") for text in labels)


def test_window_defaults_to_mucape(window):
    assert window._current_field() == "mucape"
    assert window._map.field() == "mucape"


def test_window_statistics_track_the_selected_field(window, analysis):
    stats = analysis.statistics("mucape")
    assert window._stat_labels["count"].text() == str(stats.count)
    assert window._stat_labels["maximum"].text() == \
        stats.parameter.format(stats.maximum)
    window.set_field("shear_6km")
    shear = analysis.statistics("shear_6km")
    assert window._stat_labels["maximum"].text() == \
        shear.parameter.format(shear.maximum)
    assert window._map.field() == "shear_6km"


def test_window_ranked_list_matches_the_analysis_order(window, analysis):
    ranked = analysis.ranked("mucape", limit=12)
    assert window._ranked.rowCount() == len(ranked)
    assert window._ranked_keys == [(p.row, p.col) for p in ranked]
    top = ranked[0]
    assert window._ranked.item(0, 0).text() == \
        analysis.statistics("mucape").parameter.format(top.value("mucape"))


def test_window_selecting_a_ranked_row_highlights_the_cell(window):
    window._ranked.selectRow(2)
    assert window._map.selected_cell() == window._ranked_keys[2]
    assert "Grid point" in window._status.text()


def test_window_map_click_syncs_the_ranked_selection(window, analysis):
    best = analysis.ranked("mucape", limit=1)[0]
    position = window._map._to_px(
        best.lon, best.lat, window._map._proj())
    window._map.mousePressEvent(_MouseEvent(position))
    window._map.mouseReleaseEvent(_MouseEvent(position))
    assert window._map.selected_cell() == (best.row, best.col)
    rows = {index.row() for index in window._ranked.selectedIndexes()}
    assert rows == {0}


def test_window_double_click_requests_a_sounding(window, analysis):
    requested = []
    window.soundingRequested.connect(
        lambda path, label: requested.append((path, label)))
    target = analysis.point_at(0, 0)
    position = window._map._to_px(
        target.lon, target.lat, window._map._proj())
    window._map.mouseDoubleClickEvent(_MouseEvent(position))
    assert len(requested) == 1
    path, label = requested[0]
    assert path == target.npz_path
    assert target.request_id in label


def test_window_open_without_a_selection_explains_itself(window):
    requested = []
    window.soundingRequested.connect(lambda *a: requested.append(a))
    window._open_selected()
    assert requested == []
    assert "Select a grid point" in window._status.text()


def test_window_refuses_to_open_a_cell_with_no_sounding(window, analysis):
    requested = []
    window.soundingRequested.connect(lambda *a: requested.append(a))
    # Find a node the extraction never produced.
    empty = next(
        (p for p in analysis.points if not p.npz_path), None)
    if empty is None:
        pytest.skip("this box has no unextracted node")
    window._open_cell(empty.row, empty.col)
    assert requested == []
    assert "no extracted sounding" in window._status.text()


def test_window_composites_button_requests_the_expensive_tier(window):
    asked = []
    window.compositesRequested.connect(lambda: asked.append(True))
    assert window._composites_button.isEnabled()
    window._composites_button.click()
    assert asked == [True]


def test_window_disables_the_composites_button_once_included(
    extraction, window
):
    full = ba.analyze_box(
        extraction.plan, dict(list(extraction.outputs.items())[:4]),
        tiers=(ba.FAST_TIER, ba.COMPOSITE_TIER))
    window.set_analysis(full)
    assert not window._composites_button.isEnabled()
    assert "included" in window._composites_button.text()
    present = {i.key for i in full.available_parameters()}
    assert "stp_cin" in present


def test_window_keeps_the_chosen_field_across_reanalysis(extraction, window):
    window.set_field("shear_6km")
    again = ba.analyze_box(
        extraction.plan, extraction.outputs, tiers=(ba.FAST_TIER,))
    window.set_analysis(again)
    assert window._current_field() == "shear_6km"


def test_window_values_toggle_reaches_the_map(window):
    window._values_check.setChecked(False)
    assert window._map._show_values is False
    window._values_check.setChecked(True)
    assert window._map._show_values is True


def test_window_progress_bar_appears_only_when_there_is_work(window):
    # isVisibleTo reports the intended state without needing the window shown,
    # which isVisible() cannot do in an offscreen test.
    window.set_progress(0, 0)
    assert not window._progress.isVisibleTo(window)
    window.set_progress(3, 10)
    assert window._progress.isVisibleTo(window)
    assert window._progress.maximum() == 10
    assert window._progress.value() == 3
    window.clear_progress()
    assert not window._progress.isVisibleTo(window)


def test_window_renders_without_an_analysis():
    from qtpy.QtGui import QPixmap

    from sharpmod.gui_box import BoxAnalysisWindow

    view = BoxAnalysisWindow()
    view.resize(900, 600)
    assert view.analysis() is None
    pixmap = QPixmap(view.size())
    view.render(pixmap)
    assert not pixmap.isNull()


# -- ingredient screens in the workspace ----------------------------------- #


def test_window_offers_none_plus_every_evaluable_screen(window, analysis):
    keys = [
        window._screen_combo.itemData(index)
        for index in range(window._screen_combo.count())
    ]
    assert keys[0] is None
    assert set(keys[1:]) == set(analysis.screens())
    assert window.screen() is None


def test_window_does_not_offer_screens_it_cannot_evaluate(window):
    offered = {
        window._screen_combo.itemData(index)
        for index in range(window._screen_combo.count())
    }
    for name in offered - {None}:
        for item in ba.screen(name):
            assert item.parameter in {
                key
                for point in window.analysis().points
                for key in point.values
            }


def test_window_selecting_a_screen_shows_its_coverage(window):
    window.set_screen("organized convection")
    assert window.screen() == "organized convection"
    assert window._coverage_box.isVisibleTo(window)
    assert "MUCAPE" in window._coverage_criteria.text()
    text = window._coverage_value.text()
    assert "points" in text and "km" in text
    # The map and the readout must describe the same thing.
    assert window._map.screen() == "organized convection"
    assert window._map.coverage().describe() == text


def test_window_clearing_the_screen_hides_the_readout(window):
    window.set_screen("organized convection")
    window.set_screen(None)
    assert window.screen() is None
    assert not window._coverage_box.isVisibleTo(window)
    assert window._map.coverage() is None


def test_window_keeps_the_chosen_screen_across_reanalysis(
    extraction, window
):
    window.set_screen("organized convection")
    again = ba.analyze_box(
        extraction.plan, extraction.outputs, tiers=(ba.FAST_TIER,))
    window.set_analysis(again)
    assert window.screen() == "organized convection"
    assert window._coverage_box.isVisibleTo(window)


def test_window_set_screen_ignores_an_unavailable_name(window):
    window.set_screen("not-a-screen")
    assert window.screen() is None


def test_window_renders_with_a_screen_selected(window):
    from qtpy.QtGui import QPixmap

    window.set_screen("supercell")
    pixmap = QPixmap(window.size())
    window.render(pixmap)
    assert not pixmap.isNull()


def test_window_coverage_survives_a_screen_with_no_matches(window, analysis):
    # A screen that nothing satisfies must still report conclusively rather
    # than looking like a failure.
    window._map.set_screen((
        ba.Criterion("mucape", minimum=1.0e9),
    ))
    coverage = window._map.coverage()
    assert coverage.count == 0
    assert coverage.evaluated == len(analysis.analyzed)
    assert coverage.conclusive is True


# -- the workspace has no vertical panels ---------------------------------- #


def test_the_workspace_no_longer_carries_vertical_plots(window):
    """Both were linear-axis plots that read as broken beside the Skew-T.

    The field map now gets the whole height. The numbers behind them are still
    available from ``BoxAnalysis``; only the panels are gone.
    """
    for attribute in (
        "_section", "_envelope", "_lower_tabs",
        "_spaghetti_check", "_section_combo", "_orientation_combo",
    ):
        assert not hasattr(window, attribute), attribute


def test_the_analysis_still_computes_the_numbers_behind_them(analysis):
    """Removed from the window, not from the API."""
    distances, levels, grid = analysis.vertical_transect(field="tmpc")
    assert distances and levels and grid
    envelope = analysis.envelope(field="tmpc")
    assert envelope.soundings > 0


# -- forecast-hour sequences ------------------------------------------------ #


@pytest.fixture(scope="module")
def hour_sequence(tmp_path_factory):
    """A three-hour sequence whose MUCAPE peaks at the middle hour."""
    tmp_path = tmp_path_factory.mktemp("gui-box-sequence")
    plan = _plan()
    source = dict(np.load(HRRR_NPZ, allow_pickle=False))
    outputs_by_hour = {}
    for hour, bump in ((0, 0.0), (6, 2.5), (12, 1.0)):
        hour_outputs = {}
        for node in plan.requestable_points:
            arrays = {
                key: (value.copy() if hasattr(value, "copy") else value)
                for key, value in source.items()
            }
            low = arrays["pres"] > 800.0
            arrays["dwpc"] = np.where(
                low & (arrays["dwpc"] > -9000.0),
                arrays["dwpc"] + bump + 0.2 * node.col, arrays["dwpc"])
            directory = tmp_path / f"f{hour:03d}"
            directory.mkdir(exist_ok=True)
            path = directory / f"{node.request_id}.npz"
            np.savez(path, **arrays)
            hour_outputs[node.request_id] = str(path)
        outputs_by_hour[hour] = hour_outputs
    return ba.analyze_box_sequence(
        plan, outputs_by_hour, tiers=(ba.FAST_TIER,))


@pytest.fixture
def sequence_window(hour_sequence):
    from sharpmod.gui_box import BoxAnalysisWindow

    view = BoxAnalysisWindow()
    view.resize(1000, 760)
    view.set_sequence(hour_sequence)
    yield view
    view.close()
    view.deleteLater()


# -- extraction result shape ----------------------------------------------- #


def test_single_hour_result_presents_itself_as_one_hour(extraction):
    assert extraction.sequence is False
    assert extraction.hours == (18,)
    # The per-hour view is derived, so every consumer can read it.
    assert extraction.outputs_for(18) == dict(extraction.outputs)
    assert extraction.outputs_for(99) == {}
    assert extraction.outputs_for("x") == {}


def test_multi_hour_result_reports_a_sequence():
    from sharpmod.gui_box import BoxExtractResult

    plan = _plan()
    result = BoxExtractResult(
        plan=plan, run_time=RUN, completed=4,
        outputs_by_hour={0: {"r000c000": "a.npz"}, 6: {"r000c000": "b.npz"}},
    )
    assert result.sequence is True
    assert result.hours == (0, 6)
    assert result.outputs_for(6) == {"r000c000": "b.npz"}


def test_extract_worker_covers_several_hours(tmp_path, monkeypatch):
    from sharpmod.gui_box import BoxExtractWorker

    plan = _plan()
    seen = {}

    class FakeRunner:
        def __init__(self):
            pass

        def cancel(self):
            pass

        def run(self, requests, **kwargs):
            seen["ids"] = [item.id for item in requests]
            seen["hours"] = sorted({item.fxx for item in requests})
            seen["workers"] = kwargs["max_workers"]
            progress_callback = kwargs["progress_callback"]
            for item in requests:
                progress_callback({
                    "event": "completed", "request_id": item.id})
            return SimpleNamespace(
                completed=len(requests), failed=0, cancelled=0)

    monkeypatch.setattr(gui_batch_process, "IsolatedBatchRunner", FakeRunner)
    results = []
    worker = BoxExtractWorker(plan, RUN, 0, tmp_path, hours=(0, 6, 12))
    worker.result_ready.connect(results.append)
    worker.run()

    points = len(plan.requestable_points)
    assert seen["hours"] == [0, 6, 12]
    assert len(seen["ids"]) == points * 3
    # A sequence holds at most two model hours at once.
    assert seen["workers"] == 2
    result = results[0]
    assert result.sequence is True
    assert result.hours == (0, 6, 12)
    for hour in (0, 6, 12):
        # Each hour is keyed by the plain lattice id, so it can be analyzed
        # on its own.
        assert set(result.outputs_for(hour)) == {
            node.request_id for node in plan.requestable_points
        }
    assert len(result.outputs) == points


def test_extract_worker_preserves_an_hour_when_all_of_its_nodes_fail(
        tmp_path, monkeypatch):
    from sharpmod.gui_box import BoxExtractWorker

    plan = _plan()

    class PartialHourRunner:
        def __init__(self):
            pass

        def cancel(self):
            pass

        def run(self, requests, **kwargs):
            completed = 0
            failed = 0
            progress_callback = kwargs["progress_callback"]
            for item in requests:
                if item.fxx == 6:
                    completed += 1
                    progress_callback({
                        "event": "completed", "request_id": item.id})
                else:
                    failed += 1
                    progress_callback({
                        "event": "failed", "request_id": item.id,
                        "error": {"message": "hour unavailable"},
                    })
            return SimpleNamespace(
                completed=completed, failed=failed, cancelled=0)

    monkeypatch.setattr(
        gui_batch_process, "IsolatedBatchRunner", PartialHourRunner)
    results = []
    worker = BoxExtractWorker(plan, RUN, 0, tmp_path, hours=(0, 6))
    worker.result_ready.connect(results.append)

    worker.run()

    result = results[0]
    assert result.sequence is True
    assert result.hours == (0, 6)
    assert result.outputs_for(0) == {}
    assert set(result.outputs_for(6)) == {
        node.request_id for node in plan.requestable_points
    }


def test_analysis_worker_emits_a_sequence_for_a_multi_hour_run(tmp_path):
    from sharpmod.gui_box import BoxAnalysisWorker, BoxExtractResult

    plan = _plan()
    outputs = _write_variants(plan, tmp_path)
    extraction = BoxExtractResult(
        plan=plan, run_time=RUN, completed=len(outputs) * 2,
        outputs_by_hour={0: outputs, 6: outputs},
    )
    ready, ticks = [], []
    worker = BoxAnalysisWorker(extraction, tiers=(ba.FAST_TIER,))
    worker.ready.connect(ready.append)
    worker.progress.connect(lambda done, total: ticks.append((done, total)))
    worker.run()
    assert len(ready) == 1
    sequence = ready[0]
    assert isinstance(sequence, ba.BoxSequence)
    assert sequence.hours == (0, 6)
    # Progress runs once across the whole sequence rather than restarting.
    assert ticks[-1] == (len(outputs) * 2, len(outputs) * 2)
    assert [done for done, _total in ticks] == sorted(
        done for done, _total in ticks)


# -- window playback -------------------------------------------------------- #


def test_window_shows_hour_controls_only_for_a_sequence(window,
                                                        sequence_window):
    assert not window._hour_bar.isVisibleTo(window)
    assert sequence_window._hour_bar.isVisibleTo(sequence_window)
    assert sequence_window._hour_slider.maximum() == 2


def test_window_starts_at_the_first_hour(sequence_window):
    assert sequence_window.hour() == 0
    assert "F000" in sequence_window._hour_label.text()
    assert "1 of 3" in sequence_window._hour_label.text()


def test_window_slider_switches_the_displayed_analysis(sequence_window,
                                                       hour_sequence):
    sequence_window._hour_slider.setValue(1)
    assert sequence_window.hour() == 6
    assert sequence_window.analysis() is hour_sequence.at(6)
    assert "F006" in sequence_window._hour_label.text()


def test_window_step_buttons_move_and_clamp(sequence_window):
    sequence_window._step_hour(1)
    assert sequence_window.hour() == 6
    sequence_window._step_hour(5)
    assert sequence_window.hour() == 12
    sequence_window._step_hour(-99)
    assert sequence_window.hour() == 0


def test_window_playback_loops_back_to_the_start(sequence_window):
    sequence_window._hour_slider.setValue(2)
    sequence_window._advance_playback()
    assert sequence_window.hour() == 0


def test_window_play_toggle_drives_the_timer(sequence_window):
    sequence_window._hour_play.setChecked(True)
    assert sequence_window._play_timer.isActive()
    sequence_window._hour_play.setChecked(False)
    assert not sequence_window._play_timer.isActive()


def test_window_jump_to_peak_finds_the_middle_hour(sequence_window):
    sequence_window.set_field("mucape")
    sequence_window._jump_to_peak()
    assert sequence_window.hour() == 6


def test_window_jump_to_peak_follows_an_active_screen(sequence_window):
    sequence_window.set_screen("organized convection")
    sequence_window._jump_to_peak()
    # With a screen selected the peak is the largest qualifying area.
    assert sequence_window.hour() in sequence_window.sequence().hours


def test_window_field_list_is_stable_across_hours(sequence_window):
    before = [
        sequence_window._field_combo.itemData(index)
        for index in range(sequence_window._field_combo.count())
    ]
    sequence_window._hour_slider.setValue(2)
    after = [
        sequence_window._field_combo.itemData(index)
        for index in range(sequence_window._field_combo.count())
    ]
    assert before == after


def test_window_keeps_the_selected_field_while_stepping(sequence_window):
    sequence_window.set_field("shear_6km")
    sequence_window._hour_slider.setValue(1)
    assert sequence_window._current_field() == "shear_6km"
    assert sequence_window._map.field() == "shear_6km"


def test_window_keeps_the_selected_screen_while_stepping(sequence_window):
    sequence_window.set_screen("organized convection")
    sequence_window._hour_slider.setValue(2)
    assert sequence_window.screen() == "organized convection"
    assert sequence_window._map.coverage() is not None


def test_window_treats_a_one_hour_sequence_as_a_plain_box(analysis):
    from sharpmod.gui_box import BoxAnalysisWindow

    view = BoxAnalysisWindow()
    view.resize(900, 700)
    view.set_sequence(ba.BoxSequence(hours=(3,), analyses={3: analysis}))
    assert not view._hour_bar.isVisibleTo(view)
    assert view.analysis() is analysis


def test_window_renders_a_sequence(sequence_window):
    from qtpy.QtGui import QPixmap

    pixmap = QPixmap(sequence_window.size())
    sequence_window.render(pixmap)
    assert not pixmap.isNull()


# -- export ---------------------------------------------------------------- #


@pytest.fixture
def save_to(monkeypatch, tmp_path):
    """Answer every save dialog with a path under tmp_path."""
    from sharpmod import export_paths

    application = tmp_path / "application"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    chosen = {"application": application}

    def pick(_parent, caption, default_name, _filter):
        from pathlib import Path

        chosen["caption"] = caption
        chosen["default"] = default_name
        path = tmp_path / Path(default_name).name
        chosen["path"] = path
        return str(path), ""

    monkeypatch.setattr(
        "sharpmod.gui_box.QFileDialog.getSaveFileName", pick)
    return chosen


def test_export_menu_offers_three_formats(window):
    actions = [
        action.text() for action in window._export_button.menu().actions()
    ]
    assert any("PNG" in text for text in actions)
    assert any("CSV" in text for text in actions)
    assert any("GeoJSON" in text for text in actions)


def test_default_export_name_identifies_the_box(window):
    stem = window._export_stem()
    assert stem.startswith("hrrr_box_")
    assert stem.endswith("f018")


def test_export_dialog_starts_in_the_dedicated_folder(window, save_to):
    window.export_csv()

    assert Path(save_to["default"]).parent == (
        save_to["application"] / "rendered_soundings"
    )


def test_open_export_folder_matches_every_box_dialog_default(
        window, save_to, monkeypatch):
    from sharpmod import gui_box

    opened = []
    monkeypatch.setattr(
        gui_box.QDesktopServices,
        "openUrl",
        lambda url: opened.append(Path(url.toLocalFile())) or True,
    )

    window.open_export_folder()

    assert opened == [save_to["application"] / "rendered_soundings"]


def test_export_field_png_writes_an_image(window, save_to):
    window.export_field_png()
    path = save_to["path"]
    assert path.suffix == ".png"
    assert path.is_file() and path.stat().st_size > 0
    assert "Saved the field map" in window._status.text()


def test_export_csv_writes_every_point(window, save_to, analysis):
    window.export_csv()
    path = save_to["path"]
    assert path.suffix == ".csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) - 1 == len(analysis.points)
    assert "Saved" in window._status.text()


def test_export_geojson_writes_a_feature_collection(window, save_to):
    import json

    window.export_geojson()
    path = save_to["path"]
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["type"] == "FeatureCollection"
    assert "features" in window._status.text()


def test_export_carries_the_active_screen(window, save_to):
    window.set_screen("organized convection")
    window.export_csv()
    header = save_to["path"].read_text(
        encoding="utf-8").splitlines()[0].split(",")
    # The verdict travels with the data rather than being lost on the way out.
    assert header[-1] == "matches"


def test_export_of_a_sequence_covers_every_hour(sequence_window, save_to):
    sequence_window.export_csv()
    lines = save_to["path"].read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("fxx,")
    hours = {int(line.split(",")[0]) for line in lines[1:]}
    assert hours == set(sequence_window.sequence().hours)


def test_sequence_export_name_spans_its_hours(sequence_window):
    assert sequence_window._export_stem().endswith("f000-f012")


def test_cancelling_a_save_dialog_writes_nothing(window, monkeypatch):
    monkeypatch.setattr(
        "sharpmod.gui_box.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: ("", ""))
    window.set_status("unchanged")
    window.export_csv()
    window.export_geojson()
    window.export_field_png()
    assert window._status.text() == "unchanged"


def test_export_without_an_analysis_explains_itself(save_to):
    from sharpmod.gui_box import BoxAnalysisWindow

    view = BoxAnalysisWindow()
    view.resize(600, 400)
    view.export_csv()
    assert "nothing to export" in view._status.text()
    view.export_field_png()
    assert "no field to export" in view._status.text()


def test_a_failing_export_is_reported_not_raised(window, monkeypatch):
    monkeypatch.setattr(
        "sharpmod.gui_box.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: ("/nonexistent-drive/\0bad.csv", ""))
    window.export_csv()
    assert "Could not write" in window._status.text()


# -- mean worker ----------------------------------------------------------- #


def test_mean_worker_writes_one_sounding_into_the_extraction_directory(extraction):
    from pathlib import Path

    from sharpmod.gui_box import BoxMeanWorker

    seen = {}
    worker = BoxMeanWorker(extraction)
    worker.ready.connect(lambda path, prof: seen.update(path=path, prof=prof))
    worker.failed.connect(lambda message: seen.update(error=message))
    worker.run()

    assert "error" not in seen, seen.get("error")
    output_dir = Path(extraction.output_dir)
    assert seen["path"] == str(output_dir / "box-mean-sounding.npz")
    assert (output_dir / "box-mean-sounding.json").is_file()
    assert seen["prof"].members == len(extraction.outputs)


def test_mean_worker_centres_the_sounding_on_the_box(extraction):
    from sharpmod.gui_box import BoxMeanWorker

    seen = {}
    worker = BoxMeanWorker(extraction)
    worker.ready.connect(lambda path, prof: seen.update(prof=prof))
    worker.run()

    region = extraction.plan.region
    assert seen["prof"].lat == pytest.approx(region.center_lat)
    assert seen["prof"].lon == pytest.approx(region.center_lon)


def test_mean_worker_passes_an_explicit_label_through(extraction):
    from sharpmod.gui_box import BoxMeanWorker

    seen = {}
    worker = BoxMeanWorker(extraction, loc="Green Country")
    worker.ready.connect(lambda path, prof: seen.update(path=path))
    worker.run()

    with np.load(seen["path"], allow_pickle=False) as data:
        assert str(data["loc"]) == "Green Country"


def test_mean_worker_reports_failure_rather_than_raising(extraction, tmp_path):
    from sharpmod.gui_box import BoxExtractResult, BoxMeanWorker

    broken = BoxExtractResult(
        plan=extraction.plan,
        outputs={key: str(tmp_path / "absent.npz")
                 for key in extraction.outputs},
        run_time=extraction.run_time,
        valid_time=extraction.valid_time,
        fxx=extraction.fxx,
        completed=extraction.completed,
        output_dir=str(tmp_path),
    )
    failures = []
    worker = BoxMeanWorker(broken)
    worker.failed.connect(failures.append)
    worker.run()

    assert len(failures) == 1
    assert "could not be averaged" in failures[0]


# -- choosing the forecast hour -------------------------------------------- #

_HOURS = [0, 1, 2, 3, 6, 9, 12]


def _hour_dialog(start_hour=0, hours=None):
    from sharpmod.gui_box import BoxPlanDialog

    return BoxPlanDialog(
        "hrrr", _region(),
        available_hours=_HOURS if hours is None else hours,
        start_hour=start_hour)


@pytest.mark.parametrize("start_hour", [0, 3, 12])
def test_the_hour_picker_starts_on_the_sidebar_selection(start_hour):
    """The box should follow the run the user is already looking at."""
    dialog = _hour_dialog(start_hour)
    assert dialog.fxx() == start_hour
    assert dialog._hour_combo.currentText() == f"F{start_hour:03d}"


def test_every_published_hour_is_offered():
    dialog = _hour_dialog(0)
    offered = [
        dialog._hour_combo.itemData(index)
        for index in range(dialog._hour_combo.count())
    ]
    assert offered == _HOURS


def test_a_different_hour_can_be_chosen_without_touching_the_sidebar():
    dialog = _hour_dialog(0)
    dialog._hour_combo.setCurrentIndex(dialog._hour_combo.findData(9))
    assert dialog.fxx() == 9
    # And the summary says which hour will be sampled.
    assert "F009" in dialog._summary.text()


def test_the_chosen_hour_is_reported_for_a_mean():
    dialog = _hour_dialog(0)
    dialog._hour_combo.setCurrentIndex(dialog._hour_combo.findData(6))
    assert dialog.mode() == "mean"
    # A mean is one hour by definition, so no sequence comes with it.
    assert dialog.hours() is None
    assert "F006" in dialog._summary.text()


def test_a_sequence_starts_at_the_chosen_hour_not_the_sidebar_one():
    dialog = _hour_dialog(0)
    dialog._mode_field.setChecked(True)
    dialog._hour_combo.setCurrentIndex(dialog._hour_combo.findData(3))
    dialog._sequence_check.setChecked(True)
    hours = dialog.hours()
    assert hours is not None and hours[0] == 3
    assert "F003" in dialog._sequence_check.text()


def test_choosing_the_final_hour_withdraws_the_sequence_offer():
    """There is nothing after the last published hour to step through."""
    dialog = _hour_dialog(0)
    dialog._mode_field.setChecked(True)
    dialog._sequence_check.setChecked(True)
    assert dialog.hours() is not None

    dialog._hour_combo.setCurrentIndex(dialog._hour_combo.findData(_HOURS[-1]))
    assert dialog._offers_sequence is False
    assert dialog._sequence_check.isChecked() is False
    assert dialog.hours() is None


def test_choosing_an_earlier_hour_restores_the_sequence_offer():
    """The rows exist from the start, so a later choice can re-enable them."""
    dialog = _hour_dialog(0)
    dialog._mode_field.setChecked(True)
    dialog._hour_combo.setCurrentIndex(dialog._hour_combo.findData(_HOURS[-1]))
    assert dialog._offers_sequence is False

    dialog._hour_combo.setCurrentIndex(dialog._hour_combo.findData(1))
    assert dialog._offers_sequence is True
    dialog._sequence_check.setChecked(True)
    hours = dialog.hours()
    assert hours is not None and hours[0] == 1


def test_a_single_published_hour_hides_the_picker():
    """A control with one choice is furniture, not a control."""
    dialog = _hour_dialog(0, hours=[0])
    assert dialog._offers_hour_choice is False
    assert dialog.fxx() == 0


def test_no_published_hours_still_answers_the_sidebar_hour():
    dialog = _hour_dialog(18, hours=[])
    assert dialog.fxx() == 18
