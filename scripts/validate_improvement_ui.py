"""Capture real Qt improvement-goal screens without changing user preferences.

Run once per QT_SCALE_FACTOR (set before launching Python). The existing README
capture helper performs the real window grab and atomic PNG replacement; these
validation artifacts stay under rendered_soundings rather than docs/images.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import QPoint, QSettings, Qt
from qtpy.QtGui import QImage, QPixmap
from qtpy.QtWidgets import (
    QApplication,
    QInputDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

import regenerate_readme_main_gui as captures
from sharpmod import gui_picker, gui_viewer, render
from sharpmod.gui_theme import apply_theme
from sharpmod.theme import DENSITY_OPTIONS, TEXT_SCALE_OPTIONS


def _save_natural_widget(app, widget, destination):
    """Save a popup at its own measured size instead of capture-window size."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    widget.ensurePolished()
    widget.adjustSize()
    widget.show()
    widget.raise_()
    captures._settle(app)
    pixmap = widget.grab()
    if pixmap.isNull():
        raise RuntimeError(f"Qt returned an empty capture for {destination}")
    with tempfile.NamedTemporaryFile(
        suffix=".png", dir=destination.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        if not pixmap.save(str(temporary_path), "PNG"):
            raise RuntimeError(f"Qt could not save {destination}")
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(
        f"wrote {destination.relative_to(captures.ROOT)} "
        f"({pixmap.width()}x{pixmap.height()})"
    )


def _save_widget_region(app, widget, destination):
    """Save one laid-out child surface at its full geometry."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    widget.ensurePolished()
    captures._settle(app)
    # QWidget.grab() respects an ancestor scroll-area clip, which can erase
    # the off-viewport rows of a tall enlarged-text status card. Rendering the
    # same live widget paints its complete geometry without changing layout.
    pixmap = QPixmap(widget.size())
    pixmap.fill(Qt.transparent)
    widget.render(pixmap)
    if pixmap.isNull():
        raise RuntimeError(f"Qt returned an empty capture for {destination}")
    with tempfile.NamedTemporaryFile(
        suffix=".png", dir=destination.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        if not pixmap.save(str(temporary_path), "PNG"):
            raise RuntimeError(f"Qt could not save {destination}")
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(
        f"wrote {destination.relative_to(captures.ROOT)} "
        f"({pixmap.width()}x{pixmap.height()})"
    )


def _capture_chart_fixture(app, output, prefix):
    """Style-only arbitrary values, explicitly labeled as non-forecast data."""
    from sharpmod.gui_analysis_workspace import (
        _EnvelopeChart, _TrendChart, _TrendSeries, _series_colours,
    )
    from sharpmod.profile_metrics import MetricDistribution, MetricSample, ThermodynamicBand

    window = QWidget()
    layout = QVBoxLayout(window)
    heading = QLabel("UI validation fixture — arbitrary values, not a forecast")
    heading.setWordWrap(True)
    layout.addWidget(heading)
    trend, envelope = _TrendChart(window), _EnvelopeChart(window)
    start = datetime(2026, 9, 16, tzinfo=timezone.utc)
    rows = ((50, 120, 180, 150), (80, None, 150, 200), (120, 160, 130, 230))
    trend.set_series(tuple(
        _TrendSeries(index, f"Fixture {index + 1}", f"Fixture {index + 1}", colour,
                     tuple(MetricSample(start + timedelta(hours=hour), "fixture",
                                        {"srh_1km": value}, value is not None,
                                        "Fixture gap" if value is None else None)
                           for hour, value in enumerate(row)))
        for index, (row, colour) in enumerate(zip(rows, _series_colours(len(rows))))
    ), "srh_1km")
    envelope.set_band(tuple(
        ThermodynamicBand(pressure,
                          MetricDistribution(3, temp - 4, temp, temp + 4, temp - 5, temp + 5),
                          MetricDistribution(3, dew - 4, dew, dew + 4, dew - 5, dew + 5))
        for pressure, temp, dew in ((1000, 20, 15), (850, 10, 5), (700, 1, -4), (500, -15, -25))
    ))
    layout.addWidget(trend, 1)
    layout.addWidget(envelope, 1)
    try:
        window.show()
        trend.setFocus()
        trend._highlight(2)
        captures._save_window(app, window, output / f"{prefix}-chart-fixture.png")
        from qtpy.QtCore import Qt
        from qtpy.QtTest import QTest

        envelope.setFocus()
        QTest.keyClick(envelope, Qt.Key_Home)
        QTest.keyClick(envelope, Qt.Key_Up)
        captures._save_window(app, window, output / f"{prefix}-envelope-focus.png")
        print(envelope.toolTip().splitlines()[-1])
    finally:
        window.close()


def _capture_session_recovery(app, output, prefix):
    """Capture the real recovery chooser labels and restore summary."""
    from sharpmod.gui_sessions import (
        _recovery_choice_label,
        _recovery_restore_question,
    )
    from sharpmod.sessions import RecoverySnapshot

    snapshot = RecoverySnapshot(
        path=output / "recovery" / "analysis_20260919_031500_123456_a1b2c3d4.sharpmod-session",
        created="2026-09-19 03:15:00Z",
        soundings=3,
        session_created="2026-09-19T11:14:58+08:00",
        active_collection=1,
        identities=(
            "KOUN · HRRR · 2026-09-19 03:00Z",
            "KTLX observed · 2026-09-19 03:00Z",
            "Manila case study · ERA5 · 2026-09-18 12:00Z",
        ),
        external_assets=2,
    )
    older = replace(
        snapshot,
        path=output / "recovery" / "analysis_20260919_030000_000000_e5f6a7b8.sharpmod-session",
        created="2026-09-19 03:00:00Z",
        soundings=1,
        active_collection=0,
        identities=("Guam · GFS · 2026-09-19 00:00Z",),
        external_assets=0,
    )
    chooser = QInputDialog()
    chooser.setWindowTitle("Recover Analysis Snapshot")
    chooser.setLabelText("Choose a recovery snapshot:")
    chooser.setComboBoxEditable(False)
    chooser.setComboBoxItems([
        _recovery_choice_label(snapshot),
        _recovery_choice_label(older),
    ])
    try:
        _save_natural_widget(
            app, chooser, output / f"{prefix}-recovery-chooser.png"
        )
    finally:
        chooser.close()

    confirmation = QMessageBox(QMessageBox.Question, "Restore recovery snapshot?", "")
    confirmation.setTextFormat(Qt.PlainText)
    confirmation.setText(
        _recovery_restore_question(snapshot, current_dirty=True)
    )
    confirmation.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    confirmation.setDefaultButton(QMessageBox.No)
    try:
        _save_natural_widget(
            app, confirmation, output / f"{prefix}-recovery-summary.png"
        )
    finally:
        confirmation.close()


def _capture_acquisition_job_workflows(app, picker, output, prefix, width, height, *, models_only=False):
    """Actual acquisition QThreads and native extractor, bounded synthetic data."""
    from types import SimpleNamespace
    import shutil
    import numpy as np
    from qtpy.QtCore import QDate
    from qtpy.QtWidgets import QDialog
    from sharpmod.batch_extract import BatchExtractor
    from sharpmod import gui_batch_process, gui_model_compare, gui_timeline
    from sharpmod.tests.era5_synth import make_era5_dataset
    from sharpmod.tools import model_extract

    started, release = threading.Event(), threading.Event()
    mode = {"kind": "timeline", "gate": True, "fail": False}
    retrieved = []
    retrieved_requests = []
    results = []
    run_time = datetime(2014, 6, 16, 18, tzinfo=timezone.utc)

    class FixtureRunner:
        def __init__(self):
            self.cancelled = threading.Event()
            self.extractor = None

        def cancel(self):
            self.cancelled.set()
            if self.extractor is not None:
                self.extractor.cancel()

        def run(self, requests, **kwargs):
            self.extractor = BatchExtractor(progress_callback=kwargs["progress_callback"])
            result = self.extractor.run(
                requests, output_dir=kwargs["output_dir"],
                max_workers=kwargs["max_workers"], cancelled=self.cancelled.is_set,
            )
            results.append(result)
            if mode.get("terminal_gate"):
                started.set()
                if not release.wait(45.0):
                    raise RuntimeError("fixture terminal gate timed out")
            if self.cancelled.is_set():
                raise gui_batch_process.IsolatedBatchCancelled("fixture transport cancelled", result=result)
            return result

    def retrieve(config, run_dt, fxx, **kwargs):
        retrieved.append(int(fxx))
        identity = (str(config.key), kwargs.get("member"), int(fxx))
        retrieved_requests.append(identity)
        gate = fxx >= 3 if mode["kind"] == "timeline" else identity != mode["first"]
        if mode["gate"] and gate:
            started.set()
            if not release.wait(45.0):
                raise model_extract.RetrievalError("fixture provider gate timed out")
        if kwargs["cancelled"]():
            raise model_extract.DownloadCancelled("fixture transport cancelled")
        failing = fxx == 3 if mode["kind"] == "timeline" else identity == mode.get("failing")
        if mode["fail"] and failing:
            mode["fail"] = False
            raise model_extract.RetrievalError("Fixture transient transport failure")
        dataset = make_era5_dataset(
            lats=[35.22], lons=[262.56], levels=np.arange(1000, 99, -25),
            times=[run_dt + timedelta(hours=int(fxx))], seed=44,
        )
        dataset = dataset.assign(vo=(dataset["t"].dims, np.full(dataset["t"].shape, 7e-5)))
        return dataset, SimpleNamespace(
            grib="memory://timeline-ui-fixture",
            _sharpmod_source_url="memory://timeline-ui-fixture",
            _sharpmod_fields=("TMP", "HGT", "UGRD", "VGRD", "RH", "ABSV"),
            _sharpmod_transport="bounded synthetic UI fixture; no network",
        )

    class AcceptedRange:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def hours(self):
            return (0, 3, 6)

    def settle_until(predicate):
        deadline = time.monotonic() + 45.0
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        app.processEvents()
        if not predicate():
            raise RuntimeError("timeline acquisition validation did not reach its expected state")

    def save(name, target=None):
        if target is not None:
            picker._model_job_scroll.ensureWidgetVisible(target, 12, 12)
        captures._settle(app, 2)
        captures.CAPTURE_WIDTH, captures.CAPTURE_HEIGHT = width, height
        captures._save_window(app, picker, output / f"{prefix}-{name}.png")
        _save_widget_region(app, picker._model_job, output / f"{prefix}-{name}-surface.png")
        print(f"{name}: actual picker={(picker.width(), picker.height())!r} "
              f"job={picker._model_job.accessibleDescription()!r}")
        if (picker.width(), picker.height()) != (width, height):
            raise RuntimeError("the job surface enlarged the user's picker window")
        for label in (picker._model_job.status, picker._model_job.input_label,
                      picker._model_job.retained_label, picker._model_job.count_label):
            if label.isVisible() and label.height() < label.heightForWidth(label.width()):
                raise RuntimeError(f"job text is vertically clipped: {label.accessibleName()!r}")

    def model_jobs():
        plans = {"kind": "compare", "specs": gui_model_compare.plan_model_comparison(
            ("hrrr", "rap", "gfs"), run_time,
        )}

        class AcceptedComparison:
            def __init__(self, **_kwargs):
                pass

            def exec(self):
                return QDialog.Accepted

            def specs(self):
                return plans["specs"]

            def workspace_kind(self):
                return plans["kind"]

        owner = picker._model_compare_coordinator
        mode.update(kind="compare", gate=False, first=("hrrr", None, 0))
        with patch.object(gui_model_compare, "ModelComparisonDialog", AcceptedComparison):
            owner.open()
            settle_until(lambda: owner.worker is None)
            prior = owner._session
            prior_viewer = prior.viewer
            assert prior_viewer is not None and len(prior.collections) == 3
            prior_viewer.resize(width, height)
            picker.activateWindow()
            picker._model_loc.setFocus()
            captures._settle(app, 2)
            focus, geometry = app.focusWidget(), picker.geometry()
            rail = picker._model_controls_scroll.verticalScrollBar()
            rail_position = rail.value()
            prior_geometry = prior_viewer.geometry()
            prior_collections = tuple(prior_viewer.spc_widget.prof_collections)
            prior_dates = tuple(collection.getCurrentDate() for collection in prior_collections)
            started.clear()
            release.clear()
            mode["gate"] = True
            before = len(retrieved_requests)
            owner.open()
            settle_until(lambda: started.is_set() and picker._model_job.snapshot.done == 1)
            session = owner._session
            save("comparison-acquisition-running-retained")
            save("comparison-acquisition-running-context", picker._model_job.retained_label)
            picker._model_job.cancel_button.click()
            save("comparison-acquisition-cancel-requested")
            release.set()
            settle_until(lambda: owner.worker is None)
            save("comparison-acquisition-cancelled-partial")
            assert (picker._model_job.snapshot.counts.completed,
                    picker._model_job.snapshot.counts.cancelled) == (1, 2)
            assert session.viewer is None and owner._retained is prior
            mode["gate"] = False
            picker._model_lat.setValue(40.0)
            picker._model_lon.setValue(-105.0)
            picker._model_loc.setText("Edited controls, not the saved acquisition")
            picker._model_job.retry_button.click()
            settle_until(lambda: owner.worker is None)
            save("comparison-acquisition-completed")
            assert len(session.collections) == 3 and results[-1].skipped == 1
            assert retrieved_requests[before:].count(("hrrr", None, 0)) == 1
            assert (session.request.lat, session.request.lon) == (35.22, -97.44)
            assert tuple(prior_viewer.spc_widget.prof_collections) == prior_collections
            assert tuple(collection.getCurrentDate() for collection in prior_collections) == prior_dates
            assert prior_viewer.geometry() == prior_geometry
            assert app.focusWidget() is focus and picker.geometry() == geometry
            assert rail.value() == rail_position
            session.viewer.resize(width, height)
            captures._save_window(app, session.viewer, output / f"{prefix}-comparison-acquisition-viewer.png")
            shutil.copytree(session.request.output_dir, output / f"{prefix}-comparison-acquisition-fixture", dirs_exist_ok=True)
            print(f"comparison cancel/retry requests={retrieved_requests[before:]!r} skipped={results[-1].skipped}")

            picker._model_lat.setValue(35.22)
            picker._model_lon.setValue(-97.44)
            picker._model_loc.setText("UI fixture: identical synthetic profiles; transient failure; no network")
            mode.update(fail=True, failing=("rap", None, 0))
            owner.open()
            settle_until(lambda: owner.worker is None)
            partial = owner._session
            save("comparison-acquisition-failed-partial")
            assert len(partial.collections) == 2 and picker._model_job.snapshot.counts.failed == 1
            partial.viewer.resize(width, height)
            partial.viewer.spc_widget.setProfileCollection(partial.viewer.spc_widget.prof_ids[1])
            picker.activateWindow()
            picker._model_loc.setFocus()
            captures._settle(app, 2)
            partial_geometry = partial.viewer.geometry()
            active = partial.viewer.spc_widget.prof_collections[partial.viewer.spc_widget.pc_idx]
            before = len(retrieved_requests)
            picker._model_job.retry_button.click()
            settle_until(lambda: owner.worker is None)
            save("comparison-acquisition-repaired", picker._model_job.retained_label)
            assert retrieved_requests[before:] == [("rap", None, 0)] and results[-1].skipped == 2
            assert len(partial.collections) == 3
            assert partial.viewer.spc_widget.prof_collections[partial.viewer.spc_widget.pc_idx] is active
            assert partial.viewer.geometry() == partial_geometry and picker._model_loc.hasFocus()

            plans.update(kind="ensemble", specs=gui_model_compare.plan_ensemble(
                "gefs", run_time=run_time, fxx=0, count=3,
            ))
            picker._model_combo.setCurrentIndex(picker._model_combo.findData("gefs"))
            picker._model_date.setDate(QDate(run_time.year, run_time.month, run_time.day))
            picker._model_cycle.setCurrentIndex(picker._model_cycle.findData(run_time.hour))
            mode.update(kind="ensemble", gate=True, first=("gefs", "c00", 0))
            picker._model_loc.setText("UI fixture: identical synthetic members; no network")
            started.clear()
            release.clear()
            before = len(retrieved_requests)
            owner.open()
            settle_until(lambda: started.is_set() and picker._model_job.snapshot.done == 1)
            ensemble_session = owner._session
            save("ensemble-acquisition-running-retained")
            picker._model_job.cancel_button.click()
            save("ensemble-acquisition-cancel-requested")
            release.set()
            settle_until(lambda: owner.worker is None)
            save("ensemble-acquisition-cancelled-partial")
            assert (picker._model_job.snapshot.counts.completed,
                    picker._model_job.snapshot.counts.cancelled) == (1, 2)
            assert ensemble_session.viewer is None
            mode["gate"] = False
            picker._model_job.retry_button.click()
            settle_until(lambda: owner.worker is None)
            save("ensemble-acquisition-completed", picker._model_job.retained_label)
            assert len(ensemble_session.collections) == 3 and results[-1].skipped == 1
            assert retrieved_requests[before:].count(("gefs", "c00", 0)) == 1
            ensemble_session.viewer.resize(width, height)
            captures._save_window(app, ensemble_session.viewer, output / f"{prefix}-ensemble-acquisition-viewer.png")
            shutil.copytree(ensemble_session.request.output_dir, output / f"{prefix}-ensemble-acquisition-fixture", dirs_exist_ok=True)
            print(f"ensemble cancel/retry requests={retrieved_requests[before:]!r} skipped={results[-1].skipped}")

            mode.update(fail=True, failing=("gefs", "p02", 0))
            owner.open()
            settle_until(lambda: owner.worker is None)
            ensemble_partial = owner._session
            save("ensemble-acquisition-failed-partial")
            assert len(ensemble_partial.collections) == 2 and picker._model_job.snapshot.counts.failed == 1
            win = ensemble_partial.viewer
            win.resize(width, height)
            collection = next(iter(ensemble_partial.collections.values()))
            collection.setHighlightedMember("p01")
            win.spc_widget.updateProfs()
            workspace = win._sharpmod_analysis_workspace
            workspace.tabs.setCurrentIndex(workspace.TAB_TRENDS)
            picker.activateWindow()
            picker._model_loc.setFocus()
            captures._settle(app, 2)
            original = collection._profs["p01"][0]
            date, geometry, tab = collection.getCurrentDate(), win.geometry(), workspace.tabs.currentIndex()
            mode["terminal_gate"] = True
            started.clear()
            release.clear()
            assert owner.retry_ensemble(win, collection, ("p02",))
            settle_until(lambda: started.is_set() and picker._model_job.snapshot.done == 1)
            member_session = owner._session
            save("ensemble-member-acquisition-running-retained")
            save("ensemble-member-acquisition-running-context", picker._model_job.retained_label)
            picker._model_job.cancel_button.click()
            save("ensemble-member-acquisition-cancel-requested")
            release.set()
            settle_until(lambda: owner.worker is None)
            save("ensemble-member-acquisition-cancelled-artifact")
            assert set(collection._profs) == {"c00", "p01"}
            assert picker._model_job.snapshot.counts.completed == 1
            assert picker._model_job.snapshot.counts.cancelled == 0
            assert picker._model_job.snapshot.state == "cancelled"
            before = len(retrieved_requests)
            mode["terminal_gate"] = False
            picker._model_job.retry_button.click()
            settle_until(lambda: owner.worker is None)
            save("ensemble-member-acquisition-repaired", picker._model_job.retained_label)
            assert retrieved_requests[before:] == [] and results[-1].skipped == 1
            assert set(collection._profs) == {"c00", "p01", "p02"}
            assert collection._profs["p01"][0] is original and collection.getHighlightedMemberName() == "p01"
            assert collection.getCurrentDate() == date and workspace.tabs.currentIndex() == tab
            assert win.geometry() == geometry and picker._model_loc.hasFocus()
            captures._save_window(app, win, output / f"{prefix}-ensemble-member-acquisition-retained-viewer.png")
            shutil.copytree(member_session.request.output_dir, output / f"{prefix}-ensemble-member-acquisition-fixture", dirs_exist_ok=True)
            print(f"targeted member retry requests={retrieved_requests[before:]!r} skipped={results[-1].skipped} "
                  f"highlight={collection.getHighlightedMemberName()!r} tab={workspace.tabs.currentIndex()}")
            return win

    index = picker._model_combo.findData("gfs")
    if index < 0:
        raise RuntimeError("the supported GFS fixture model is missing")
    picker._model_combo.setCurrentIndex(index)
    picker._model_lat.setValue(35.22)
    picker._model_lon.setValue(-97.44)
    picker._model_date.setDate(QDate(run_time.year, run_time.month, run_time.day))
    picker._model_cycle.setCurrentIndex(picker._model_cycle.findData(run_time.hour))
    picker._model_loc.setText("UI fixture: synthetic profiles; no network")
    viewer = None
    try:
        with patch.object(gui_batch_process, "IsolatedBatchRunner", FixtureRunner), \
                patch.object(model_extract, "_retrieve_dataset", retrieve), \
                patch.object(model_extract, "requires_grib_runtime", return_value=False), \
                patch.object(gui_timeline, "ForecastTimelineDialog", AcceptedRange):
            if models_only:
                return model_jobs()
            picker._model_fetch_timeline()
            coordinator = picker._model_timeline_coordinator
            settle_until(lambda: started.is_set() and coordinator._session.completed_hours == (0,))
            session = coordinator._session
            viewer = session.viewer
            viewer.resize(width, height)
            picker.activateWindow()
            picker._model_loc.setFocus()
            captures._settle(app, 2)
            scroll = picker._model_controls_scroll.verticalScrollBar()
            scroll_value = scroll.value()
            focus = app.focusWidget()
            geometry = picker.geometry()
            viewer_geometry = viewer.geometry()
            current = session.collection.getCurrentDate()
            save("timeline-acquisition-running-retained")
            save("timeline-acquisition-running-context", picker._model_job.retained_label)
            picker._model_job_scroll.verticalScrollBar().setValue(0)
            picker._model_job.cancel_button.click()
            save("timeline-acquisition-cancel-requested")
            release.set()
            settle_until(lambda: picker._model_timeline_worker is None)
            save("timeline-acquisition-cancelled-partial")
            save("timeline-acquisition-cancelled-context", picker._model_job.count_label)
            assert picker._model_job.snapshot.counts.completed == 1
            assert picker._model_job.snapshot.counts.cancelled == 2
            assert session.completed_hours == (0,)
            assert session.collection.getCurrentDate() == current
            mode["gate"] = False
            picker._model_lat.setValue(40.0)
            picker._model_lon.setValue(-105.0)
            picker._model_loc.setText("Edited controls, not the saved request")
            picker._model_job.retry_button.click()
            settle_until(lambda: picker._model_timeline_worker is None)
            save("timeline-acquisition-completed")
            assert session.completed_hours == (0, 3, 6)
            assert session.viewer is viewer and session.collection.getCurrentDate() == current
            assert retrieved.count(0) == 1 and results[-1].skipped == 1
            assert app.focusWidget() is focus and scroll.value() == scroll_value
            assert picker.geometry() == geometry
            assert viewer.geometry() == viewer_geometry
            assert "synthetic profiles; no network" in picker._model_job.snapshot.affected_input
            captures._save_window(app, viewer, output / f"{prefix}-timeline-acquisition-retained-viewer.png")
            shutil.copytree(session.request.output_dir, output / f"{prefix}-timeline-acquisition-fixture", dirs_exist_ok=True)
            print(f"cancel/retry retrieved={retrieved!r} skipped={results[-1].skipped} "
                  f"selected={current!r} horizontal={picker._model_controls_scroll.horizontalScrollBar().maximum()}")

            picker._model_lat.setValue(35.22)
            picker._model_lon.setValue(-97.44)
            picker._model_loc.setText("UI fixture: synthetic profiles; transient failure")
            mode["fail"] = True
            picker._model_fetch_timeline()
            settle_until(lambda: picker._model_timeline_worker is None)
            failed_session = coordinator._session
            save("timeline-acquisition-failed-partial")
            assert failed_session.completed_hours == (0, 6)
            assert picker._model_job.snapshot.counts.failed == 1
            save("timeline-acquisition-failed-context", picker._model_job.retained_label)
            before = tuple(retrieved)
            picker._model_job.retry_button.click()
            settle_until(lambda: picker._model_timeline_worker is None)
            save("timeline-acquisition-repaired")
            assert failed_session.completed_hours == (0, 3, 6)
            assert retrieved[len(before):] == [3] and results[-1].skipped == 2
            print(f"failure retry retrieved={retrieved[len(before):]!r} skipped={results[-1].skipped}")
            failed_session.viewer.close()
    finally:
        release.set()
        if picker._model_timeline_worker is not None:
            picker._model_timeline_coordinator.cancel()
            settle_until(lambda: picker._model_timeline_worker is None)
        comparison = picker._model_compare_coordinator
        if comparison.worker is not None:
            comparison.cancel()
            settle_until(lambda: comparison.worker is None)
    return viewer


def _capture_analysis_job_workflows(app, picker, output, prefix, width, height):
    """Real serial-pool diagnostics on clearly labeled repeated-archive slots."""
    from sharpmod.profile_metrics import ProfileMetricsError, freeze_collection
    from sharpmod.ensemble_members import EnsembleAcquisition, MemberFailure
    from qtpy.QtTest import QTest

    collection, station_id = render.decode(str(captures.SOURCE))
    viewer = picker._show_sounding(collection, station_id)
    viewer.resize(width, height)
    workspace = viewer._sharpmod_analysis_workspace
    engine = workspace.engine
    base = freeze_collection(collection)
    member = next(iter(base._profs))
    profile = base._profs[member][0]
    start = base._dates[0]
    fixture = replace(
        base, _dates=tuple(start + timedelta(hours=index) for index in range(4)),
        _profs={member: (profile, profile, None, profile)}, current_date=start,
        _meta={**base._meta, "loc": "UI fixture: repeated 19:00Z archive; synthetic slots"},
    )
    real_values = engine.values

    class MetricGate:
        def __init__(self, *, fail_call=None):
            self.calls = 0
            self.started, self.release = threading.Event(), threading.Event()
            self.fail_call = fail_call

        def __call__(self, saved_profile, keys):
            self.calls += 1
            if self.calls == 2:
                self.started.set()
                if not self.release.wait(20.0):
                    raise ProfileMetricsError("trend validation gate timed out")
            if self.calls == self.fail_call:
                raise ProfileMetricsError("Fixture transient diagnostic failure")
            return real_values(saved_profile, keys)

    def settle_until(predicate):
        deadline = time.monotonic() + 20.0
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        app.processEvents()
        if not predicate():
            raise RuntimeError("analysis job validation did not reach its expected state")

    def save(name, target, job=None):
        scroll = workspace.tabs.currentWidget()
        scroll.ensureWidgetVisible(target, 12, 12)
        captures._settle(app, 2)
        captures._save_window(app, viewer, output / f"{prefix}-{name}.png")
        _save_widget_region(app, job or workspace.trend_job, output / f"{prefix}-{name}-surface.png")

    gates = []
    try:
        with patch.object(workspace, "_collections", return_value=(fixture,)):
            workspace.open("trends")
            settle_until(lambda: not workspace._tasks)
            workspace.trend_chart._highlight(0)
            prior = workspace._trend_series
            gate = MetricGate()
            gates.append(gate)
            with patch.object(engine, "values", gate):
                workspace._refresh_trends()
                settle_until(gate.started.is_set)
                save("trend-job-running-retained", workspace.trend_job.cancel_button)
                save("trend-job-running-context", workspace.trend_job.retained_label)
                workspace.trend_job.cancel_button.click()
                save("trend-job-cancel-requested", workspace.trend_job.status)
                gate.release.set()
                settle_until(lambda: not workspace._tasks)
                assert workspace._trend_series is prior
                counts = workspace.trend_job.snapshot.counts
                assert (counts.completed, counts.cancelled, counts.unavailable) == (2, 1, 1)
                save("trend-job-cancelled-partial", workspace.trend_job.count_label)
                workspace.trend_job.retry_button.click()
                settle_until(lambda: not workspace._tasks)
                assert gate.calls == 3, "retry must not revisit completed profile diagnostics"
                assert workspace.trend_job.snapshot.counts.completed == 3
                save("trend-job-completed", workspace.trend_job.count_label)
                save("trend-job-chart", workspace.trend_chart)
            print(
                f"trend cancellation job={workspace.trend_job.accessibleDescription()!r} "
                f"values_calls={gate.calls} "
                f"horizontal_range={workspace.tabs.currentWidget().horizontalScrollBar().maximum()}"
            )

            failure = MetricGate(fail_call=2)
            gates.append(failure)
            failure.release.set()
            with patch.object(engine, "values", failure):
                workspace._refresh_trends()
                settle_until(lambda: not workspace._tasks)
                counts = workspace.trend_job.snapshot.counts
                assert (counts.completed, counts.failed, counts.unavailable) == (2, 1, 1)
                save("trend-job-failed-partial", workspace.trend_job.retry_button)
                save("trend-job-failed-context", workspace.trend_job.status)
                workspace.trend_job.retry_button.click()
                settle_until(lambda: not workspace._tasks)
                assert failure.calls == 4, "retry must repair only the failed diagnostic"
                assert workspace.trend_job.snapshot.counts.completed == 3
                save("trend-job-repaired", workspace.trend_job.count_label)
            print(
                f"trend repair job={workspace.trend_job.accessibleDescription()!r} "
                f"values_calls={failure.calls} "
                f"horizontal_range={workspace.tabs.currentWidget().horizontalScrollBar().maximum()}"
            )
        comparison_sources = tuple(replace(
            base, _profs={member: (None if index == 2 else profile,)},
            _meta={**base._meta, "loc": f"UI comparison fixture {index + 1}: repeated 19:00Z archive"},
        ) for index in range(4))
        with patch.object(workspace, "_collections", return_value=comparison_sources):
            workspace.set_comparison_metric_keys(("mlcape",), persist=False)
            workspace.open("compare")
            settle_until(lambda: not workspace._tasks)
            workspace.compare_reference.setCurrentIndex(1)
            settle_until(lambda: not workspace._tasks)
            prior, charts = workspace._comparison_samples, workspace.visual_compare._collections
            workspace.compare_table.setCurrentCell(1, 3)
            gate = MetricGate()
            gates.append(gate)
            with patch.object(engine, "values", gate):
                workspace._refresh_compare()
                settle_until(gate.started.is_set)
                save("comparison-job-running-retained", workspace.compare_job.cancel_button, workspace.compare_job)
                save("comparison-job-running-context", workspace.compare_job.retained_label, workspace.compare_job)
                workspace.compare_job.cancel_button.click()
                save("comparison-job-cancel-requested", workspace.compare_job.status, workspace.compare_job)
                gate.release.set()
                settle_until(lambda: not workspace._tasks)
                counts = workspace.compare_job.snapshot.counts
                assert (counts.completed, counts.cancelled, counts.unavailable) == (2, 1, 1)
                assert workspace._comparison_samples is prior
                assert workspace.visual_compare._collections is charts
                save("comparison-job-cancelled-partial", workspace.compare_job.count_label, workspace.compare_job)
                comparison_sources[1]._meta["loc"] = "Edited fixture label after cancellation"
                workspace.compare_job.retry_button.click()
                settle_until(lambda: not workspace._tasks)
                assert gate.calls == 3
                assert workspace.compare_job.snapshot.counts.completed == 3
                assert workspace.visual_compare._reference == 1
                assert "Edited fixture label" not in workspace.compare_job.snapshot.affected_input
                save("comparison-job-completed", workspace.compare_job.count_label, workspace.compare_job)
                save("comparison-job-table", workspace.compare_table, workspace.compare_job)
                save("comparison-job-chart", workspace.visual_compare, workspace.compare_job)
                _save_widget_region(app, workspace.visual_compare,
                                    output / f"{prefix}-comparison-job-chart-detail.png")
            print(
                f"comparison cancellation job={workspace.compare_job.accessibleDescription()!r} "
                f"values_calls={gate.calls} reference={workspace.visual_compare._reference} "
                f"selected_row={workspace.compare_table.currentRow()} "
                f"horizontal_range={workspace.tabs.currentWidget().horizontalScrollBar().maximum()}"
            )
            failure = MetricGate(fail_call=2)
            gates.append(failure)
            failure.release.set()
            with patch.object(engine, "values", failure):
                workspace._refresh_compare()
                settle_until(lambda: not workspace._tasks)
                counts = workspace.compare_job.snapshot.counts
                assert (counts.completed, counts.failed, counts.unavailable) == (2, 1, 1)
                save("comparison-job-failed-partial", workspace.compare_job.retry_button, workspace.compare_job)
                save("comparison-job-failed-context", workspace.compare_job.status, workspace.compare_job)
                workspace.compare_job.retry_button.click()
                settle_until(lambda: not workspace._tasks)
                assert failure.calls == 4
                assert workspace.compare_job.snapshot.counts.completed == 3
                save("comparison-job-repaired", workspace.compare_job.count_label, workspace.compare_job)
            print(
                f"comparison repair job={workspace.compare_job.accessibleDescription()!r} "
                f"values_calls={failure.calls} "
                f"horizontal_range={workspace.tabs.currentWidget().horizontalScrollBar().maximum()}"
            )
        ledger = EnsembleAcquisition(("c00", "p01", "p02", "p03", "p04"),
                                     ("c00", "p01", "p02", "p03"),
                                     (MemberFailure("p04", "failed", "Fixture download unavailable"),))
        ensemble_source = replace(
            base, _profs={"c00": (profile,), "p01": (profile,), "p02": (None,), "p03": (profile,)},
            _highlight="c00", _meta={**base._meta, **ledger.to_metadata(), "ensemble": True,
                                    "loc": "UI ensemble fixture: repeated 19:00Z archive"},
        )
        with patch.object(workspace, "_collections", return_value=(ensemble_source,)), \
                patch.object(workspace.threshold_explorer, "refresh"):
            workspace.open("ensemble")
            workspace.ensemble_modes.setCurrentIndex(0)
            settle_until(lambda: not workspace._tasks)
            assert workspace.ensemble_job.snapshot.counts.completed == 3
            save("ensemble-job-native-batch-completed", workspace.ensemble_job.count_label, workspace.ensemble_job)
            workspace.ensemble_table.setCurrentCell(2, 3)
            workspace.ensemble_chart.setFocus()
            QTest.keyClick(workspace.ensemble_chart, Qt.Key_End)
            prior, envelope = workspace._ensemble_summary, workspace.ensemble_chart._temperature
            pressure = workspace.ensemble_chart._pressures[workspace.ensemble_chart._level_index]
            gate = MetricGate()
            gates.append(gate)
            # Exercise the existing per-member fallback for a controlled mid-
            # member cancel; the unmodified native batch completed just above.
            with patch.object(engine, "_can_batch", return_value=False), patch.object(engine, "values", gate):
                workspace._refresh_ensemble()
                settle_until(gate.started.is_set)
                save("ensemble-job-running-retained", workspace.ensemble_job.cancel_button, workspace.ensemble_job)
                save("ensemble-job-running-context", workspace.ensemble_job.retained_label, workspace.ensemble_job)
                workspace.ensemble_job.cancel_button.click()
                save("ensemble-job-cancel-requested", workspace.ensemble_job.status, workspace.ensemble_job)
                gate.release.set()
                settle_until(lambda: not workspace._tasks)
                counts = workspace.ensemble_job.snapshot.counts
                assert (counts.completed, counts.cancelled, counts.unavailable) == (2, 1, 2)
                assert workspace._ensemble_summary is prior
                assert workspace.ensemble_chart._temperature is envelope
                save("ensemble-job-cancelled-partial", workspace.ensemble_job.count_label, workspace.ensemble_job)
                ensemble_source._meta["loc"] = "Edited ensemble label after cancellation"
                workspace.ensemble_job.retry_button.click()
                settle_until(lambda: not workspace._tasks)
                assert gate.calls == 3
                assert workspace.ensemble_job.snapshot.counts.completed == 3
                assert "Edited ensemble label" not in workspace.ensemble_job.snapshot.affected_input
                assert workspace.ensemble_chart._pressures[workspace.ensemble_chart._level_index] == pressure
                save("ensemble-job-completed", workspace.ensemble_job.count_label, workspace.ensemble_job)
                save("ensemble-job-table-values", workspace.ensemble_table, workspace.ensemble_job)
                saved_scroll = workspace.ensemble_table.horizontalScrollBar().value()
                workspace.ensemble_table.horizontalScrollBar().setValue(0)
                save("ensemble-job-table", workspace.ensemble_table, workspace.ensemble_job)
                workspace.ensemble_table.horizontalScrollBar().setValue(saved_scroll)
                save("ensemble-job-envelope", workspace.ensemble_chart, workspace.ensemble_job)
                _save_widget_region(app, workspace.ensemble_chart, output / f"{prefix}-ensemble-job-envelope-detail.png")
            print(
                f"ensemble cancellation job={workspace.ensemble_job.accessibleDescription()!r} "
                f"values_calls={gate.calls} selected_row={workspace.ensemble_table.currentRow()} "
                f"selected_pressure={pressure} "
                f"horizontal_range={workspace.tabs.currentWidget().horizontalScrollBar().maximum()}"
            )
            failure = MetricGate(fail_call=2)
            gates.append(failure)
            failure.release.set()
            with patch.object(engine, "_can_batch", return_value=False), patch.object(engine, "values", failure):
                workspace._refresh_ensemble()
                settle_until(lambda: not workspace._tasks)
                counts = workspace.ensemble_job.snapshot.counts
                assert (counts.completed, counts.failed, counts.unavailable) == (2, 1, 2)
                save("ensemble-job-failed-partial", workspace.ensemble_job.retry_button, workspace.ensemble_job)
                save("ensemble-job-failed-context", workspace.ensemble_job.status, workspace.ensemble_job)
                workspace.ensemble_job.retry_button.click()
                settle_until(lambda: not workspace._tasks)
                assert failure.calls == 4
                assert workspace.ensemble_job.snapshot.counts.completed == 3
                save("ensemble-job-repaired", workspace.ensemble_job.count_label, workspace.ensemble_job)
            destination = output / f"{prefix}-ensemble-job-export.csv"
            with patch("sharpmod.gui_analysis_workspace.QFileDialog.getSaveFileName",
                       return_value=(str(destination), "")):
                workspace._choose_ensemble_export()
            assert destination.is_file() and destination.stat().st_size > 0
            print(
                f"ensemble repair job={workspace.ensemble_job.accessibleDescription()!r} "
                f"values_calls={failure.calls} csv_bytes={destination.stat().st_size} "
                f"horizontal_range={workspace.tabs.currentWidget().horizontalScrollBar().maximum()}"
            )
    except Exception:
        viewer.close()
        raise
    finally:
        for gate in gates:
            gate.release.set()
    return viewer


def _capture_job_workflows(app, picker, output, prefix, width, height):
    """Capture the remaining Analysis job flows."""
    return _capture_analysis_job_workflows(app, picker, output, prefix, width, height)


def capture(*, text_scale=100, density="comfortable", style="standard",
            width=1600, height=950, view="both", stage="t01", source="Station Map",
            collapse_rail=False, rail_width=None, station=None, query="") -> None:
    app = QApplication.instance() or QApplication([])
    render.install_font(app)
    factor = os.environ.get("QT_SCALE_FACTOR", "1")
    output = captures.ROOT / "rendered_soundings" / "improvement-goal" / stage
    prefix = f"{style}-text{text_scale}-{density}-display{factor}"
    if stage != "t01":
        token = source.casefold().replace(" ", "-").replace("(", "").replace(")", "")
        prefix += f"-{token}-{width}x{height}-{'collapsed' if collapse_rail else 'expanded'}"
    captures.CAPTURE_WIDTH, captures.CAPTURE_HEIGHT = width, height
    if view == "charts":
        apply_theme(app, color_style=style, text_scale=text_scale, density=density)
        _capture_chart_fixture(app, output, prefix)
        return
    previous_settings = os.environ.get("SHARPMOD_SETTINGS_PATH")
    with tempfile.TemporaryDirectory(prefix="sharpmod-improvement-ui-") as root:
        settings_path = Path(root) / "settings.ini"
        captures._seed_settings(settings_path, style)
        settings = QSettings(str(settings_path), QSettings.IniFormat)
        settings.setValue("interface/text_scale", str(text_scale))
        settings.setValue("interface/density", density)
        settings.sync()
        os.environ["SHARPMOD_SETTINGS_PATH"] = str(settings_path)
        picker = viewer = None
        field_fetch_patch = None
        try:
            apply_theme(app, color_style=style, text_scale=text_scale, density=density)
            if view == "sessions":
                _capture_session_recovery(app, output, prefix)
                return
            with patch.object(gui_picker.PickerWindow, "_refresh_station_catalog",
                              lambda *_args: None), \
                    patch.object(gui_picker.PickerWindow, "_queue_availability",
                                 lambda *_args, **_kwargs: None), \
                    patch.object(gui_picker.PickerWindow, "_queue_model_availability",
                                 lambda *_args, **_kwargs: None), \
                    patch.object(gui_viewer, "start_locator_overlay_fetch",
                                 lambda *_args, **_kwargs: None):
                picker = gui_picker.PickerWindow()
                for name in ("_avail_timer", "_catalog_timer", "_model_availability_timer"):
                    getattr(picker, name).stop()
                if stage == "t26":
                    # T26 is a deterministic offline recovery capture. Patch
                    # before the lazy Field Panels tab is constructed so even
                    # its initial debounce cannot reach a provider.
                    from sharpmod import hrrr_field as _capture_hrrr_field

                    field_fetch_patch = patch.object(
                        _capture_hrrr_field, "fetch_field",
                        lambda *_args, **_kwargs: None)
                    field_fetch_patch.start()
                picker._select_tab(source)
                if source == "Open File":
                    picker._file_modes.setCurrentIndex(1)
                pane_source = dict(zip(
                    ("Station Map", "Station List", "Forecast Model", "Field Panels",
                     "Reanalysis (ERA5)", "Open File"),
                    ("map", "uwyo", "model", "panels", "era5", "wrf"),
                ))[source]
                pane = getattr(picker, f"_{pane_source}_selection_pane")
                if station is not None:
                    if picker._station(station) is None:
                        raise ValueError(f"Station not in bundled catalog: {station}")
                    picker._map_on_select(station, check_availability=False)
                    picker._map.set_selected(station)
                picker.resize(width, height)
                picker.show()
                captures._settle(app)
                if stage in {"t17", "t18", "t19", "t20", "t21", "t22"}:
                    from sharpmod.gui_picker_layout import CollapsibleRailSection

                    rail = pane.rail.widget().layout()
                    region_section = None
                    layers_section = None
                    time_section = None
                    overlay_section = None
                    for index in range(rail.count()):
                        section = rail.itemAt(index).widget()
                        if not isinstance(section, CollapsibleRailSection):
                            continue
                        title = section.title().casefold()
                        if title == "region" or (
                                stage in {"t18", "t19", "t20", "t21", "t22"}
                                and "point" in title):
                            section.set_collapsed(False)
                            if title == "region":
                                region_section = section
                        if stage in {"t19", "t20", "t21", "t22"} \
                                and ("active layer" in title or
                                     title == "map layers"):
                            section.set_collapsed(False)
                            layers_section = section
                        if stage == "t21" and "run" in title:
                            section.set_collapsed(False)
                            time_section = section
                        if stage == "t22" and (
                                "map overlay" in title or title == "map layers"):
                            section.set_collapsed(False)
                            overlay_section = section
                    if stage == "t19":
                        layer_tabs = getattr(
                            picker, f"_{pane_source}_layer_tabs", None)
                        if layer_tabs is not None:
                            layer_tabs.setCurrentIndex(1)
                    captures._settle(app)
                    # Prefer the live pane's own Region card: the picker's
                    # ``_area_combo`` belongs to whichever tab built first, so
                    # ensuring *it* visible scrolls nothing on other tabs.
                    # For t19 the Map layers card's Status / order view is the
                    # subject, scrolled in after the Region card so both read on
                    # one capture. For t20 the Region card carries the legend
                    # controls and is the subject itself.
                    if stage == "t21" and time_section is not None:
                        pane.rail.ensureWidgetVisible(time_section)
                    elif stage == "t22" and overlay_section is not None:
                        pane.rail.ensureWidgetVisible(overlay_section)
                    elif stage == "t19" and layers_section is not None:
                        pane.rail.ensureWidgetVisible(layers_section)
                    elif region_section is not None:
                        pane.rail.ensureWidgetVisible(region_section)
                    elif hasattr(picker, "_area_combo"):
                        pane.rail.ensureWidgetVisible(picker._area_combo)
                    captures._settle(app)
                if stage == "t18" and source == "Forecast Model":
                    import numpy as np

                    from sharpmod import hrrr_field

                    # The inspection product follows the tab's field
                    # controller; pin the card to whatever run/hour it holds
                    # rather than guessing a time.
                    controller = getattr(picker, "_model_field", None)
                    reference = None
                    if controller is not None:
                        try:
                            run, fxx = controller._run, controller._fxx
                            if run is None:
                                run, fxx = hrrr_field.resolve_request(
                                    controller._valid_time)
                            reference = (controller.product(), run, int(fxx))
                        except Exception:  # noqa: BLE001 - fall back below
                            reference = None
                    if reference is not None:
                        product, run, fxx = reference
                        # A realistic proof value for whatever the tab depicts:
                        # reflectivity reads in dBZ, everything else in its
                        # own display units near mid-scale.
                        proof = 45.0 if product == "refc" else 1500.0
                        field = np.full((1536, 3072), proof, dtype=np.float32)
                        hrrr_field.remember_derived(
                            product, run, fxx, (3072, 1536), field)
                        picker._model_map.set_inspection_product(
                            product, run=run, fxx=fxx)
                    picker._model_map.set_map_tool("inspect")
                    lon, lat = picker._model_map._to_lonlat(
                        picker._model_map.width() * 0.55,
                        picker._model_map.height() * 0.55)
                    picker._model_map._pin_field_snapshot(float(lat), float(lon))
                    captures._settle(app)
                if stage == "t19" and source == "Forecast Model":
                    # T19 subject: apply the compatible preset (no network --
                    # enabling alone fetches nothing without a valid time and
                    # a settled view), then state one failure honestly.
                    picker._apply_layer_preset("model", "storm environment")
                    picker._model_field._set_status(
                        "Field unavailable: synthetic proof failure")
                    picker._refresh_all_layer_lists()
                    captures._settle(app)
                if stage in {"t20", "t21"} and source == "Forecast Model":
                    # T20 subject: a depicted field with a locked scale and a
                    # hover marker (numeric proof grid, no network), the
                    # compact-legend and corner controls visible on the Region
                    # card beside them. The field is switched on so the legend
                    # has a continuous bar to draw, not just captions.
                    import numpy as np

                    from sharpmod import hrrr_field

                    controller = getattr(picker, "_model_field", None)
                    reference = None
                    if controller is not None:
                        try:
                            run, fxx = controller._run, controller._fxx
                            if run is None:
                                run, fxx = hrrr_field.resolve_request(
                                    controller._valid_time)
                            reference = (controller.product(), run, int(fxx))
                        except Exception:  # noqa: BLE001 - fall back below
                            reference = None
                    if reference is not None:
                        product, run, fxx = reference
                        proof = 45.0 if product == "refc" else 1500.0
                        field = np.full((1536, 3072), proof, dtype=np.float32)
                        hrrr_field.remember_derived(
                            product, run, fxx, (3072, 1536), field)
                        picker._model_map.set_inspection_product(
                            product, run=run, fxx=fxx)
                    if controller is not None:
                        controller._timer.stop()
                        controller._refresh_timer.stop()
                        controller.set_enabled(True)
                        controller._timer.stop()
                        controller._refresh_timer.stop()
                    # Render the proof grid to a PNG through the real renderer
                    # (no network -- the numbers are already in hand), so the
                    # legend has a continuous bar and the map shows the field
                    # the hover value is sampled from.
                    from sharpmod.hrrr_products import get_product

                    proof_product = get_product(
                        product or "refc")
                    payload = hrrr_field.render_field(
                        proof_product, {"refc": np.full(
                            hrrr_field.HRRR_SHAPE, proof, dtype=np.float32),
                            "__run_iso": run.isoformat(),
                            "__run_fxx": int(fxx)},
                        size=hrrr_field.DEFAULT_FRAME_SIZE)
                    valid = run + __import__("datetime").timedelta(
                        hours=int(fxx))
                    from sharpmod.map_overlays import OverlayRaster

                    picker._model_map.set_overlay(
                        "hrrr_field",
                        OverlayRaster(
                            key="hrrr_field",
                            title=f"HRRR {proof_product.label}",
                            image_bytes=payload,
                            bounds=hrrr_field.COVERAGE_BOUNDS,
                            subtitle=(f"{run:%d %b %HZ} run · F{int(fxx):02d} "
                                      f"· valid {valid:%d %b %HZ}"),
                            short_name=proof_product.key,
                            valid_time=valid,
                            retrieved_at=__import__(
                                "datetime").datetime.now(__import__(
                                    "datetime").timezone.utc),
                            update_interval_s=3600.0,
                            opacity=0.75),
                        visible=True)
                    picker._model_map.set_map_tool("inspect")
                    lon, lat = picker._model_map._to_lonlat(
                        picker._model_map.width() * 0.55,
                        picker._model_map.height() * 0.55)
                    picker._model_map._hover_lonlat = (float(lon), float(lat))
                    try:
                        picker._model_map._update_inspect_hover(float(lat),
                                                               float(lon))
                    except Exception:  # noqa: BLE001 - legend still draws
                        pass
                    try:
                        picker._on_scale_lock_toggled(True)
                    except Exception:  # noqa: BLE001 - legend still draws
                        pass
                    picker._refresh_all_layer_lists()
                    captures._settle(app)
                if stage == "t21" and source == "Forecast Model" \
                        and reference is not None:
                    # Proof of all four contracts in one screenshot: a pinned
                    # historical hour, an actual field offset from the request,
                    # a second nearest-time observation raster, and the shared
                    # T09 ledger carrying ready/loading/missing/failed cells.
                    from sharpmod.timeline_frames import TimelineCoverage

                    requested = valid
                    shifted = requested + __import__("datetime").timedelta(
                        minutes=20)
                    current = picker._model_selected_fxx()
                    offered = [
                        int(frame.forecast_hour)
                        for frame in picker._model_frame_strip.coverage().frames
                    ]
                    sample = offered[:5]
                    if current not in sample:
                        sample = [current, *sample[:4]]
                    sample = sorted(set(sample))
                    while len(sample) < 5:
                        sample.append(sample[-1] + 1 if sample else current)
                    ledger = TimelineCoverage.requested_range(
                        sample, run_time=run)
                    ledger = ledger.with_state(
                        sample[0], "loaded",
                        valid_time=run + __import__("datetime").timedelta(
                            hours=sample[0]))
                    ledger = ledger.with_state(
                        sample[1], "loaded",
                        valid_time=run + __import__("datetime").timedelta(
                            hours=sample[1]))
                    ledger = ledger.with_state(sample[2], "loading")
                    ledger = ledger.with_state(
                        sample[3], "unavailable", reason="not published")
                    ledger = ledger.with_state(
                        sample[4], "failed", reason="synthetic proof failure")
                    picker._set_map_frame_coverage(ledger)
                    picker._model_frame_strip.set_coverage(
                        ledger, active_hour=int(current))
                    picker._apply_map_mode("model", "history")
                    # Attach through the real map seam after entering History;
                    # this differs from the requested valid time and therefore
                    # remains the previous, correctly labelled partial frame.
                    picker._model_map._rasters["hrrr_field"] = OverlayRaster(
                        key="hrrr_field",
                        title=f"HRRR {proof_product.label}",
                        image_bytes=payload,
                        bounds=hrrr_field.COVERAGE_BOUNDS,
                        subtitle=(f"{run:%d %b %HZ} run · F{int(fxx):02d} "
                                  f"· actual {shifted:%d %b %H:%MZ}"),
                        short_name=proof_product.key,
                        valid_time=shifted,
                        retrieved_at=requested + __import__(
                            "datetime").timedelta(minutes=22),
                        update_interval_s=3600.0,
                        opacity=0.75)
                    picker._model_map._refresh_map_accessible_description()
                    picker._refresh_map_time_header("model")
                    picker._refresh_all_layer_lists()
                    captures._settle(app)
                if stage == "t22" and source == "Station Map":
                    from sharpmod import storm_reports, surface_observations
                    from sharpmod.map_overlays import (
                        OverlayShape,
                        bounds_of,
                        build_layer,
                    )

                    requested = datetime(2026, 5, 1, 18, tzinfo=timezone.utc)
                    stations = (
                        {"id": "KOUN", "name": "Norman", "lat": 35.22,
                         "lon": -97.44},
                        {"id": "KTLX", "name": "Twin Lakes", "lat": 35.33,
                         "lon": -97.28},
                        {"id": "KOKC", "name": "Oklahoma City", "lat": 35.39,
                         "lon": -97.60},
                        {"id": "KLAW", "name": "Lawton", "lat": 34.57,
                         "lon": -98.42},
                        {"id": "KSWO", "name": "Stillwater", "lat": 36.16,
                         "lon": -97.09},
                        {"id": "KPNC", "name": "Ponca City", "lat": 36.73,
                         "lon": -97.10},
                        {"id": "KADM", "name": "Ardmore", "lat": 34.30,
                         "lon": -97.02},
                        {"id": "KCHK", "name": "Chickasha", "lat": 35.10,
                         "lon": -97.97},
                    )
                    picker._map.set_stations(stations)
                    picker._map.set_extent(-100.5, -95.0, 33.4, 37.6, pad=0)
                    picker._map.set_valid_time(requested)
                    picker._map.set_selected("KOUN")
                    picker._map._hover_id = "KTLX"
                    picker._map._pin_station_snapshot(stations[2])

                    outlook_ring = (
                        (-99.7, 34.0), (-95.6, 34.0), (-95.6, 37.2),
                        (-99.7, 37.2), (-99.7, 34.0),
                    )
                    outlook = build_layer(
                        "spc_outlook",
                        "SPC convective outlook — Day 1",
                        (
                            OverlayShape(
                                rings=(outlook_ring,),
                                bounds=bounds_of((outlook_ring,)),
                                stroke="#d99000",
                                fill="#f6c945",
                                label="SLGT",
                                description=(
                                    "Slight risk · issued 01 May 1300Z · "
                                    "Source: NOAA/NWS Storm Prediction Center"
                                ),
                                rank=2,
                            ),
                        ),
                        subtitle="Valid 01 May 1200Z – 02 May 1200Z",
                        valid_from=requested - timedelta(hours=6),
                        valid_to=requested + timedelta(hours=18),
                        short_name="Day 1",
                        attribution="NOAA/NWS SPC",
                    )
                    picker._map_outlook.set_valid_time(requested)
                    picker._map_outlook.set_enabled(True)
                    picker._map_outlook._timer.stop()
                    picker._map.set_overlay("spc_outlook", outlook, visible=True)

                    reports = (
                        storm_reports.StormReport(
                            storm_reports.HAZARDS["T"], requested,
                            35.22, -97.44, None, "Norman", "Cleveland", "OK",
                            "OUN", "Storm chaser", "Brief touchdown reported.",
                        ),
                        storm_reports.StormReport(
                            storm_reports.HAZARDS["H"],
                            requested + timedelta(minutes=6),
                            35.22, -97.44, 2.25, "Norman", "Cleveland", "OK",
                            "OUN", "Public", "Measured tennis-ball hail.",
                        ),
                        storm_reports.StormReport(
                            storm_reports.HAZARDS["G"],
                            requested + timedelta(minutes=12),
                            35.34, -97.31, 78.0, "Edmond", "Oklahoma", "OK",
                            "OUN", "Mesonet", "Measured gust.",
                        ),
                        storm_reports.StormReport(
                            storm_reports.HAZARDS["D"],
                            requested - timedelta(minutes=18),
                            34.60, -98.35, None, "Lawton", "Comanche", "OK",
                            "OUN", "Emergency manager", "Large limbs down.",
                        ),
                    )
                    report_layer = storm_reports.layer_from_reports(
                        reports,
                        span_deg=5.5,
                        valid_from=requested - timedelta(hours=3),
                        valid_to=requested + timedelta(hours=3),
                        around=requested,
                        window=timedelta(hours=6),
                        source_url="https://mesonet.agron.iastate.edu/",
                    )
                    picker._map_reports.set_valid_time(requested)
                    picker._map_reports._sync_available()
                    picker._map_reports.set_enabled(True)
                    picker._map_reports._timer.stop()
                    picker._map_reports._on_loaded(
                        picker._map_reports._token, requested, report_layer
                    )

                    surface_stations = tuple(
                        surface_observations.SurfaceStation(
                            item["id"], item["name"], item["lat"], item["lon"]
                        )
                        for item in stations
                    )
                    observations = (
                        surface_observations.SurfaceObservation(
                            surface_stations[0], requested, 24.0, 19.0, None, 0.0,
                            text_description="Calm",
                        ),
                        surface_observations.SurfaceObservation(
                            surface_stations[1], requested, 23.0, 18.0, None, None,
                            text_description="Wind missing",
                        ),
                        surface_observations.SurfaceObservation(
                            surface_stations[2], requested - timedelta(minutes=48),
                            25.0, 20.0, 190.0, 22.0, 34.0,
                            text_description="Stale thunderstorm observation",
                        ),
                        surface_observations.SurfaceObservation(
                            surface_stations[3], requested + timedelta(minutes=8),
                            27.0, 17.0, None, 14.0,
                            text_description="Direction missing",
                        ),
                    )
                    dataset = surface_observations.SurfaceObservationSet(
                        requested,
                        35.22,
                        -97.44,
                        observations,
                        len(surface_stations),
                        ("KSWO", "KPNC"),
                        requested + timedelta(minutes=15),
                        "https://api.weather.gov/",
                        ("KOUN", "KTLX", "KOKC", "KLAW", "KSWO", "KPNC"),
                        surface_stations,
                        ("KPNC",),
                    )
                    picker._map_context.set_valid_time(requested)
                    picker._map_context._surface_check.setChecked(True)
                    picker._map_context._surface_timer.stop()
                    picker._map_context._surface = dataset
                    picker._map_context._rerender_surface()

                    picker._map.set_loaded_profile_points(
                        (
                            {"id": "observed-koun", "label": "KOUN observed",
                             "lat": 35.22, "lon": -97.44,
                             "kind": "observed", "active": True,
                             "valid_time": requested, "source": "RAOB"},
                            {"id": "hrrr-koun", "label": "HRRR KOUN",
                             "lat": 35.22, "lon": -97.44, "kind": "model",
                             "valid_time": requested, "source": "HRRR"},
                            {"id": "imported-lawton", "label": "Lawton case",
                             "lat": 34.57, "lon": -98.42,
                             "kind": "imported", "valid_time": requested,
                             "source": "Imported file"},
                        )
                    )
                    point = picker._map._to_px(-97.44, 35.22,
                                               picker._map._proj())
                    choices = picker._map.marker_choices_at(point.x(), point.y())
                    if len(choices) < 5:
                        raise RuntimeError(
                            "T22 overlap fixture did not expose every marker"
                        )
                    picker._refresh_all_layer_lists()
                    captures._settle(app)
                    # Reproduce the reported chase exactly: move the logical
                    # cursor into the painted bottom-right key and repaint. A
                    # transient hover must update the coordinate readout but
                    # must not make the legend jump to another corner.
                    picker._on_legend_corner_chosen("bottom-right")
                    captures._settle(app)
                    picker._map.grab()
                    legend_before = picker._map._legend_clip_box()
                    if legend_before is None:
                        raise RuntimeError("T22 map-fix legend was not painted")
                    hover_x = legend_before[0] + legend_before[2] / 2.0
                    hover_y = legend_before[1] + legend_before[3] / 2.0
                    picker._map._hover_lonlat = picker._map._to_lonlat(
                        hover_x, hover_y)
                    picker._map.update()
                    captures._settle(app)
                    picker._map.grab()
                    legend_after = picker._map._legend_clip_box()
                    if picker._map.legend_layout_info()["corner"] != \
                            "bottom-right" or legend_after is None or any(
                                abs(float(after) - float(before)) > 0.01
                                for before, after in zip(
                                    legend_before, legend_after)):
                        raise RuntimeError(
                            "Legend moved when the pointer entered it: "
                            f"{legend_before!r} -> {legend_after!r}")
                    if view in {"picker", "both"}:
                        pane.rail.ensureWidgetVisible(
                            picker._map_reports._hazard_filter, 12, 12
                        )
                        captures._settle(app)
                        captures._save_window(
                            app,
                            picker,
                            output / f"{prefix}-reports.png",
                        )
                        pane.rail.ensureWidgetVisible(
                            picker._map_context._surface_status, 12, 12
                        )
                        captures._settle(app)
                    print(
                        f"T22 overlap choices={len(choices)} "
                        f"surface={picker._map_context._surface_status.text()!r} "
                        f"legend_hover={legend_before!r}->{legend_after!r}"
                    )
                if stage == "t26" and source == "Field Panels":
                    import numpy as np

                    from sharpmod import hrrr_field
                    from sharpmod.map_overlays import OverlayRaster

                    panels = picker._panels_view
                    # Four-up proves the representative comparison at normal
                    # text; the supported two-up layout keeps the same recovery
                    # controls legible at 200% rather than manufacturing a
                    # physically larger desktop for the evidence.
                    layout_count = 2 if int(text_scale) >= 200 else 4
                    panels.set_panel_count(layout_count)
                    picker._panels_count_combo.setCurrentIndex(
                        picker._panels_count_combo.findData(layout_count))
                    panels.set_panel_products(
                        ("refc", "mlcape", "stp", "srh-0-1km"))
                    run = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
                    panels.set_forecast_reference(run, 6)
                    panels.set_point(35.63, -97.44)
                    panels.set_point_locked(True)

                    rgba = np.zeros((180, 320, 4), dtype=np.uint8)
                    x = np.linspace(0, 1, rgba.shape[1], dtype=np.float32)
                    y = np.linspace(0, 1, rgba.shape[0], dtype=np.float32)[:, None]
                    rgba[..., 0] = np.asarray(35 + 160 * x, dtype=np.uint8)
                    rgba[..., 1] = np.asarray(55 + 130 * y, dtype=np.uint8)
                    rgba[..., 2] = np.asarray(
                        165 - 95 * x[None, :] + 35 * y, dtype=np.uint8)
                    rgba[..., 3] = 220
                    payload = hrrr_field.encode_png(rgba)
                    valid = run + timedelta(hours=6)

                    for index, panel in enumerate(panels._panels):
                        controller = panel.field
                        controller._timer.stop()
                        controller._refresh_timer.stop()
                        controller._request = lambda: None
                        product = controller.product()
                        spec = hrrr_field.get_product(product)
                        raster = OverlayRaster(
                            key="hrrr_field", title=f"HRRR {spec.label}",
                            image_bytes=payload,
                            bounds=hrrr_field.COVERAGE_BOUNDS,
                            subtitle=(f"{run:%d %b %HZ} run · F06 · "
                                      f"valid {valid:%d %b %HZ}"),
                            short_name=product, valid_time=valid,
                            retrieved_at=valid + timedelta(minutes=8),
                            update_interval_s=3600.0, opacity=0.75,
                            source_url=hrrr_field.grib_url(
                                run, 6, spec.sources[0]))
                        panel.map.set_overlay("hrrr_field", raster, visible=True)
                        if index == 0:
                            controller._set_status(
                                controller._describe(raster), state="available")
                        elif index == 1:
                            controller._set_status(
                                "Loading replacement field…", state="loading")
                        elif index == 2:
                            controller._set_status(
                                "Field offline or provider unreachable: "
                                "connection timed out", state="offline")
                        else:
                            panel.map.remove_overlay("hrrr_field")
                            controller._set_status(
                                "Field unavailable for this run/hour (no data): "
                                "not published", state="no-data")
                    panels._panels[0].map.setFocus(Qt.OtherFocusReason)
                    captures._settle(app)
                    captures._save_window(
                        app, picker,
                        output / f"{prefix}-recovery-cached.png")
                    assert [panel.field.layer_state()
                            for panel in panels._panels] == [
                                "available", "loading", "offline", "no-data"]
                    assert panels._panels[1].cancel_btn.isVisible()
                    if layout_count == 4:
                        assert panels._panels[2].retry_btn.isVisible()
                        assert panels._panels[3].retry_btn.isVisible()

                    terminal = (
                        ("Field failed: decoded grid was incomplete", "failed"),
                        ("Field request canceled; Retry to request it again",
                         "canceled"),
                    )
                    for panel, (message, state) in zip(
                            panels._panels[:2], terminal):
                        panel.field._set_status(message, state=state)
                    third_raster = panels._panels[2].map.overlay("hrrr_field")
                    panels._panels[2].field._set_status(
                        panels._panels[2].field._describe(third_raster),
                        state="available")
                    captures._settle(app)
                    captures._save_window(
                        app, picker,
                        output / f"{prefix}-recovery-terminal.png")
                    states = [panel.field.layer_state()
                              for panel in panels._panels]
                    assert states == [
                        "failed", "canceled", "available", "no-data"], states

                    # A separate real outside-domain view keeps that condition
                    # truthful instead of mixing it into a CONUS panel merely
                    # to fit every label in one screenshot.
                    panels._panels[0].map.set_extent(
                        -10.0, 30.0, 35.0, 60.0, pad=0.0)
                    outside = (
                        "HRRR covers the contiguous United States; the map is "
                        "currently outside it")
                    for panel in panels._panels:
                        panel.map.set_overlay_visible("hrrr_field", False)
                        panel.field._set_status(
                            outside, state="outside-domain")
                    captures._settle(app)
                    captures._save_window(
                        app, picker,
                        output / f"{prefix}-recovery-outside-domain.png")
                    assert all(panel.field.layer_state() == "outside-domain"
                               for panel in panels._panels)
                    assert panels.panel_count() == layout_count
                    assert panels.is_point_locked()
                    assert all(panel.field._workers.live_count() <= 2
                               for panel in panels._panels)
                    print(f"T26 panel states={states!r} "
                          f"products={panels.panel_products()!r} "
                          f"locked={panels.is_point_locked()}")
                if rail_width is not None:
                    pane.splitter.setSizes([rail_width, max(pane.splitter.width() - rail_width, 0)])
                    pane._remember_width()
                pane.set_collapsed(collapse_rail)
                if view == "palette":
                    picker._sharpmod_command_palette.action.trigger()
                    palette = picker._sharpmod_command_palette.dialog
                    palette.search.setText(query)
                    captures._settle(app)
                    # Preserve the real dialog geometry; do not stretch a dialog
                    # to the outer picker capture size.
                    captures.CAPTURE_WIDTH, captures.CAPTURE_HEIGHT = palette.width(), palette.height()
                    captures._save_window(app, palette, output / f"{prefix}-palette.png")
                    print(f"matching actions={palette.results.count()} query={query!r}")
                if view == "selector":
                    if source == "Field Panels":
                        combo = picker._panels_view._panels[0].selector._product
                    elif source == "Forecast Model":
                        combo = picker._model_combo
                    else:
                        raise ValueError("Selector capture supports Forecast Model or Field Panels")
                    combo._sharpmod_selector_search.open()
                    dialog = combo._sharpmod_selector_search.dialog
                    dialog.search.setText(query)
                    captures._settle(app)
                    captures.CAPTURE_WIDTH, captures.CAPTURE_HEIGHT = dialog.width(), dialog.height()
                    captures._save_window(app, dialog, output / f"{prefix}-selector.png")
                    print(f"matching choices={dialog.results.count()} query={query!r} "
                          f"unchanged selected identity={combo.currentData()!r}")
                if view == "coordinates":
                    if pane_source not in {"model", "panels", "era5", "wrf"}:
                        raise ValueError("Coordinate capture supports point-based sources")
                    button = getattr(picker, f"_{pane_source}_paste_coordinates")
                    button.click()
                    dialog = button._sharpmod_coordinate_controller.dialog
                    dialog.input.setText(query)
                    captures._settle(app)
                    captures.CAPTURE_WIDTH, captures.CAPTURE_HEIGHT = dialog.width(), dialog.height()
                    captures._save_window(app, dialog, output / f"{prefix}-coordinates.png")
                    print(f"preview={dialog.preview.text()} status={dialog.status.text()} "
                          f"apply enabled={dialog.apply_button.isEnabled()}")
                if view == "recents":
                    from sharpmod.recent_destinations import RecentDestinationStore, describe_collection
                    from sharpmod.sessions import build_session, write_session

                    collection, _station = render.decode(str(captures.SOURCE))
                    store = RecentDestinationStore(picker._settings)
                    store.remember("file", captures.SOURCE, details=describe_collection(collection))
                    # Deliberately unavailable UI fixture; not a failed retrieval.
                    missing = output / "moved-validation-fixture.spc"
                    store.remember("file", missing, label="Missing validation fixture",
                                   details="UI validation fixture — deliberately absent")
                    output.mkdir(parents=True, exist_ok=True)
                    session_path = output / "oax-validation.sharpmod-session"
                    write_session(session_path, build_session([collection], active_collection=0))
                    store.remember("session", session_path, details="1 sounding · " + describe_collection(collection))
                    picker._remember_point(35.63, -97.44, "UI validation point")
                    picker._show_recent_destinations(query=query)
                    dialog = picker._recent_destinations_controller.dialog
                    captures._settle(app)
                    captures.CAPTURE_WIDTH, captures.CAPTURE_HEIGHT = dialog.width(), dialog.height()
                    captures._save_window(app, dialog, output / f"{prefix}-recents.png")
                    print(f"matching recent destinations={dialog.results.count()} query={query!r}")
                    if dialog.results.verticalScrollBar().maximum():
                        dialog.results.verticalScrollBar().setValue(dialog.results.verticalScrollBar().maximum())
                        captures._save_window(app, dialog, output / f"{prefix}-recents-scrolled.png")
                if view == "data-management":
                    from sharpmod.gui_locations import SavedLocationsDialog

                    store = picker._saved_location_store
                    for name, lat, lon, group in (
                        ("Norman home", 35.2226, -97.4395, "Home"),
                        ("KOUN field office", 35.2456, -97.4721, "Field work"),
                        ("Manila case", 14.5995, 120.9842, "Case studies"),
                        ("Guam", 13.4443, 144.7937, "Case studies"),
                        ("Unsorted point", 9.5, -100.1, ""),
                    ):
                        store.upsert(name, lat, lon, group=group)
                    dialog = SavedLocationsDialog(store, parent=picker)
                    dialog.resize(width, height)
                    dialog.show()
                    dialog.table.sortItems(3)
                    dialog.table.selectRow(2)
                    dialog.favorite_button.click()
                    captures._settle(app)
                    captures._save_window(
                        app, dialog, output / f"{prefix}-saved-locations.png"
                    )
                    print(
                        f"location rows={dialog.table.rowCount()} "
                        f"groups={dialog.group_filter.count() - 2} "
                        f"selected={dialog._selected().name!r} "
                        f"table viewport={dialog.table.viewport().size().width()}x"
                        f"{dialog.table.viewport().size().height()}"
                    )
                    dialog.close()
                if view in {"picker", "both"}:
                    captures._save_window(app, picker, output / f"{prefix}-picker.png")
                    scroll = pane.rail
                    print(f"rail viewport={scroll.viewport().width()} "
                          f"content minimum={scroll.widget().minimumSizeHint().width()} "
                          f"text={picker._utc_clock.font().pointSizeF():g}pt")
                    print(f"actual window={picker.width()}x{picker.height()} "
                          f"summary={pane.summary.text()}")
                if view in {"profiles", "closed"}:
                    collection, station_id = render.decode(str(captures.SOURCE))
                    viewer = picker._show_sounding(collection, station_id)
                    for filename in ("hrrr_point_36.68N_95.66W_f018.npz", "hrrr_kbvo_20260625_06z.buf"):
                        other, place = render.decode(str(captures.SOURCE.parent / filename))
                        picker._show_sounding(other, place)
                    if getattr(viewer, "_sharpmod_install_failures", ()):
                        raise RuntimeError(viewer._sharpmod_install_failures)
                    viewer.resize(width, height)
                    viewer._sharpmod_sidebar_dock.show()
                    viewer._sharpmod_sidebar_dock.raise_()
                    panel = viewer._sharpmod_sidebar
                    state = viewer._sharpmod_collection_state
                    if view == "closed":
                        closed_id = viewer.spc_widget.prof_ids[1]
                        state.pin(closed_id)
                        if not state.remove(closed_id):
                            raise RuntimeError("Could not create retained closed-profile validation state")
                        state.show_closed()
                        dialog = state._closed_dialog
                        dialog.search.setText(query)
                        captures._settle(app)
                        captures.CAPTURE_WIDTH, captures.CAPTURE_HEIGHT = dialog.width(), dialog.height()
                        captures._save_window(app, dialog, output / f"{prefix}-closed.png")
                        if dialog.results.verticalScrollBar().maximum():
                            dialog.results.verticalScrollBar().setValue(dialog.results.verticalScrollBar().maximum())
                            captures._save_window(app, dialog, output / f"{prefix}-closed-scrolled.png")
                        print(f"retained profiles={len(state.closed)} visible matches={dialog.results.count()} "
                              f"loaded IDs={viewer.spc_widget.prof_ids!r}")
                    else:
                        panel._group_actions["source"].trigger()
                        panel._sort_actions["valid_time"].trigger()
                        panel._search.setText(query)
                        captures._settle(app)
                        captures._save_window(app, viewer, output / f"{prefix}-profiles.png")
                        print(f"actual loaded profile IDs={viewer.spc_widget.prof_ids!r}")
                        captures.CAPTURE_WIDTH, captures.CAPTURE_HEIGHT = panel.width(), panel.height()
                        captures._save_window(app, panel, output / f"{prefix}-profiles-panel.png")
                        panel._list.verticalScrollBar().setValue(0)
                        captures._save_window(app, panel, output / f"{prefix}-profiles-panel-top.png")
                        reference_id = viewer.spc_widget.prof_ids[0]
                        state.pin(reference_id)
                        state.set_visible(reference_id, False)
                        captures._settle(app)
                        panel._scroll.verticalScrollBar().setValue(panel._scroll.verticalScrollBar().maximum())
                        captures._save_window(app, panel, output / f"{prefix}-profiles-reference-hidden.png")
                if view == "interaction":
                    collection, station_id = render.decode(str(captures.SOURCE))
                    viewer = picker._show_sounding(collection, station_id)
                    failures = getattr(viewer, "_sharpmod_install_failures", ())
                    if failures:
                        raise RuntimeError("; ".join(failures))
                    viewer.resize(width, height)
                    viewer._sharpmod_inspect_action.trigger()
                    viewer.spc_widget.sound.readout_pres = 850.0
                    viewer.spc_widget.sound.updateReadout()
                    captures._settle(app)
                    captures._save_window(
                        app, viewer, output / f"{prefix}-inspect.png"
                    )
                    viewer._sharpmod_edit_action.trigger()
                    captures._settle(app)
                    captures._save_window(
                        app, viewer, output / f"{prefix}-edit.png"
                    )
                    print(
                        f"interaction mode={viewer._sharpmod_interaction_mode.mode} "
                        f"hint={viewer._sharpmod_mode_hint.text()!r} "
                        f"readout={viewer._sharpmod_linked_readout.label.toolTip()!r}"
                    )
                if view == "locator":
                    from sharpmod import locator_presentation
                    from sharpmod.gui_locator import save_locator_png
                    from sharpmod.map_overlays import (
                        OverlayLayer,
                        OverlayShape,
                        attach_locator_overlay,
                    )
                    from sharpmod.viz import hodo_locator

                    collection, station_id = render.decode(str(captures.SOURCE))
                    profile = collection.getHighlightedProf()
                    lat = float(getattr(profile, "latitude", 41.32))
                    try:
                        lon = float(collection.getMeta("lon"))
                    except Exception:
                        lon = -96.37
                    collection.setMeta("lat", lat)
                    collection.setMeta("lon", lon)
                    # Exercise the human place-title path as well as the
                    # requested/sampled coordinates.  The lookup is the bundled
                    # offline Census index used by normal point workflows.
                    from sharpmod.place_names import offline_conus_town_name

                    nearby_place = offline_conus_town_name(lat, lon)
                    if nearby_place:
                        collection.setMeta("loc", nearby_place)
                    collection.setMeta("requested_lat", lat + 0.08)
                    collection.setMeta("requested_lon", lon - 0.10)
                    collection.setMeta("sharpmod_locator_overlay_spec", "none")
                    selected = (lon - 3.0, lat - 2.0, lon + 4.5, lat + 2.8)
                    locator_presentation.set_collection_presentation(
                        collection,
                        locator_presentation.LocatorPresentation(
                            link_mode=locator_presentation.LINK_PINNED,
                            extent_mode=locator_presentation.EXTENT_SELECTED,
                            selected_area=selected,
                        ),
                    )
                    hazard = OverlayShape(
                        rings=(((lon - 2.0, lat - 1.0),
                                (lon + 2.5, lat - 0.5),
                                (lon + 1.4, lat + 1.8),
                                (lon - 2.0, lat - 1.0)),),
                        bounds=(lon - 2.0, lon + 2.5, lat - 1.0, lat + 1.8),
                        stroke="#ff7b7b",
                        fill="#8f2d56",
                        label="5%",
                    )
                    locator_valid = collection.getCurrentDate()
                    if locator_valid.tzinfo is None:
                        locator_valid = locator_valid.replace(tzinfo=timezone.utc)
                    layer = OverlayLayer(
                        key="spc_outlook",
                        title="SPC tornado probability",
                        short_name="TOR",
                        shapes=(hazard,),
                        issued=locator_valid,
                        valid_from=locator_valid,
                        attribution="NOAA/NWS Storm Prediction Center",
                    )
                    attach_locator_overlay(collection, layer)
                    locator_presentation.set_overlay_status(
                        collection,
                        layer.key,
                        "available",
                        family="risk",
                        product="torn",
                        detail="Loaded validation fixture",
                    )
                    viewer = picker._show_sounding(collection, station_id)
                    failures = getattr(viewer, "_sharpmod_install_failures", ())
                    if failures:
                        raise RuntimeError("; ".join(failures))
                    # Viewer binding has now captured the live main-map context;
                    # choose the independent Fit state afterwards so this visual
                    # fixture exercises the explicit pinned path.
                    current_locator = locator_presentation.collection_presentation(
                        collection)
                    locator_presentation.set_collection_presentation(
                        collection,
                        current_locator.updated(
                            link_mode=locator_presentation.LINK_PINNED,
                            extent_mode=locator_presentation.EXTENT_SELECTED,
                            selected_area=selected,
                        ),
                    )
                    attach_locator_overlay(collection, layer)
                    locator_presentation.set_overlay_status(
                        collection,
                        layer.key,
                        "available",
                        family="risk",
                        product="torn",
                        detail="Loaded validation fixture",
                    )
                    from sharpmod.gui_viewer import _repaint_locator_insets

                    _repaint_locator_insets(viewer)
                    viewer._sharpmod_show_locator()
                    dialog = viewer._sharpmod_locator_dialog
                    dialog.resize(min(1000, width), min(760, height))
                    dialog.refresh(force=True)
                    captures._settle(app, 3)
                    captures.CAPTURE_WIDTH = dialog.width()
                    captures.CAPTURE_HEIGHT = dialog.height()
                    captures._save_window(
                        app, dialog, output / f"{prefix}-locator-pinned-fit.png"
                    )
                    export_path = output / f"{prefix}-locator-export.png"
                    if not save_locator_png(viewer, export_path, width=1400, height=900):
                        raise RuntimeError("Locator validation export failed")
                    point_before = (
                        collection.getMeta("requested_lat"),
                        collection.getMeta("requested_lon"),
                        hodo_locator.point_from_widget(viewer.spc_widget.hodo),
                    )
                    dialog.link_combo.setCurrentIndex(
                        dialog.link_combo.findData(locator_presentation.LINK_FOLLOW)
                    )
                    captures._settle(app, 2)
                    captures._save_window(
                        app, dialog, output / f"{prefix}-locator-follow.png"
                    )
                    point_after = (
                        collection.getMeta("requested_lat"),
                        collection.getMeta("requested_lon"),
                        hodo_locator.point_from_widget(viewer.spc_widget.hodo),
                    )
                    print(
                        f"locator pinned={selected!r} follow="
                        f"{locator_presentation.collection_presentation(collection).main_bounds!r} "
                        f"selection_unchanged={point_before == point_after} "
                        f"overlay={dialog.overlay_label.text()!r} export={export_path}"
                    )
                if view == "overlay":
                    collection, station_id = render.decode(str(captures.SOURCE))
                    viewer = picker._show_sounding(collection, station_id)
                    failures = getattr(viewer, "_sharpmod_install_failures", ())
                    if failures:
                        raise RuntimeError("; ".join(failures))
                    viewer.resize(width, height)
                    viewer._sharpmod_edit_action.trigger()
                    profile = collection.getHighlightedProf()
                    level = int(profile.sfc) + 4
                    before = float(profile.tmpc[level])
                    # A real vendored edit signal, not a synthetic overlay state.
                    viewer.spc_widget.sound.modified.emit(
                        level, {"tmpc": before + 3.0}
                    )
                    captures._settle(app)
                    # A wind edit as well, so the hodograph overlay and its
                    # change ring are both exercised by the capture.
                    edited = collection.getHighlightedProf()
                    viewer.spc_widget.hodo.modified.emit(
                        level,
                        {
                            "u": float(edited.u[level]) + 11.0,
                            "v": float(edited.v[level]) - 9.0,
                        },
                    )
                    captures._settle(app)
                    action = viewer._sharpmod_original_overlay_action
                    if not action.isEnabled() or not action.isChecked():
                        raise RuntimeError(
                            "Original overlay did not become available after a real edit"
                        )
                    captures._save_window(
                        app, viewer, output / f"{prefix}-overlay-on.png"
                    )
                    sound = viewer.spc_widget.sound
                    captures.CAPTURE_WIDTH = sound.width()
                    captures.CAPTURE_HEIGHT = sound.height()
                    captures._save_window(
                        app, sound, output / f"{prefix}-overlay-skewt.png"
                    )
                    hodo = viewer.spc_widget.hodo
                    captures.CAPTURE_WIDTH, captures.CAPTURE_HEIGHT = (
                        hodo.width(), hodo.height()
                    )
                    captures._save_window(
                        app, hodo, output / f"{prefix}-overlay-hodo.png"
                    )
                    action.setChecked(False)
                    captures._settle(app)
                    captures.CAPTURE_WIDTH = sound.width()
                    captures.CAPTURE_HEIGHT = sound.height()
                    captures._save_window(
                        app, sound, output / f"{prefix}-overlay-off-skewt.png"
                    )
                    print(
                        f"edited level={level} pressure={float(profile.pres[level]):.1f} hPa "
                        f"feedback={viewer._sharpmod_edit_feedback.label.toolTip()!r}"
                    )
                    # The readable history of those same two real edits.
                    action.setChecked(True)
                    viewer._sharpmod_show_edit_history()
                    dialog = viewer._sharpmod_edit_history_dialog
                    dialog.steps.setCurrentRow(1)
                    captures._settle(app)
                    captures.CAPTURE_WIDTH, captures.CAPTURE_HEIGHT = (
                        dialog.width(), dialog.height()
                    )
                    captures._save_window(
                        app, dialog, output / f"{prefix}-edit-history.png"
                    )
                    print(
                        f"history action={viewer._sharpmod_edit_history_action.text()!r} "
                        f"rows={dialog.steps.count()} status={dialog.status.text()!r}"
                    )
                    dialog.close()
                if view == "navigation":
                    collection, station_id = render.decode(str(captures.SOURCE))
                    viewer = picker._show_sounding(collection, station_id)
                    failures = getattr(viewer, "_sharpmod_install_failures", ())
                    if failures:
                        raise RuntimeError("; ".join(failures))
                    viewer.resize(width, height)
                    captures._settle(app)
                    workspace = viewer._sharpmod_analysis_workspace
                    workspace.open("trends")
                    captures._settle(app)
                    needed = workspace.tabs.tabBar().sizeHint().width()

                    # The dock at a width that fits every label: the tab bar is
                    # the navigation.
                    viewer.resizeDocks(
                        [workspace.dock], [needed + 80], Qt.Horizontal
                    )
                    captures._settle(app)
                    workspace.refresh()
                    captures._settle(app)
                    captures._save_window(
                        app, viewer, output / f"{prefix}-analysis-tabs.png"
                    )
                    print(
                        f"wide dock={workspace.dock.width()} needed={needed} "
                        f"tab bar hidden={workspace.tabs.tabBar().isHidden()} "
                        f"navigator hidden={workspace.navigator_row.isHidden()}"
                    )

                    # A realistic side-dock width: the grouped chooser takes over.
                    viewer.resizeDocks(
                        [workspace.dock], [max(320, needed // 2)], Qt.Horizontal
                    )
                    captures._settle(app)
                    workspace.refresh()
                    captures._settle(app)
                    captures._save_window(
                        app, viewer, output / f"{prefix}-analysis-navigator.png"
                    )
                    navigator = workspace.navigator
                    captures.CAPTURE_WIDTH = max(360, navigator.width() + 160)
                    captures.CAPTURE_HEIGHT = max(120, navigator.height() * 3)
                    captures._save_window(
                        app,
                        workspace.navigator_row,
                        output / f"{prefix}-analysis-navigator-row.png",
                    )
                    entries = [
                        (
                            navigator.itemText(position),
                            navigator.itemData(position),
                        )
                        for position in range(navigator.count())
                    ]
                    print(
                        f"narrow dock={workspace.dock.width()} "
                        f"tab bar hidden={workspace.tabs.tabBar().isHidden()} "
                        f"navigator hidden={workspace.navigator_row.isHidden()} "
                        f"selected={navigator.currentText()!r}"
                    )
                    print(f"navigator entries={entries!r}")

                    # T06.4: one loaded sounding is deliberately insufficient
                    # for Compare. Capture its precise explanation and the real
                    # picker recovery action at the difficult side-dock width.
                    workspace.open("compare")
                    captures._settle(app)
                    if workspace.compare_add.isHidden():
                        raise RuntimeError("Compare recovery action was not visible")
                    captures.CAPTURE_WIDTH, captures.CAPTURE_HEIGHT = width, height
                    captures._save_window(
                        app,
                        viewer,
                        output / f"{prefix}-analysis-compare-recovery.png",
                    )
                    print(
                        f"compare recovery={workspace.compare_add.text()!r} "
                        f"status={workspace.compare_status.text()!r}"
                    )
                    workspace.open("trends")
                    captures._settle(app)

                    # T06.2 uses the same widget tree in both placements. Focus
                    # mode gives analysis the main client area; separate mode
                    # makes that dock a top-level window for another monitor.
                    workspace.set_expanded(True)
                    captures._settle(app)
                    captures.CAPTURE_WIDTH, captures.CAPTURE_HEIGHT = width, height
                    captures._save_window(
                        app,
                        viewer,
                        output / f"{prefix}-analysis-expanded.png",
                    )
                    print(
                        f"expanded mode={workspace.presentation_mode()} "
                        f"workspace={workspace.width()}x{workspace.height()} "
                        f"tab bar hidden={workspace.tabs.tabBar().isHidden()}"
                    )

                    workspace.set_separate_window(True)
                    captures._settle(app)
                    captures.CAPTURE_WIDTH = min(1100, width)
                    captures.CAPTURE_HEIGHT = min(800, height)
                    captures._save_window(
                        app,
                        workspace.dock,
                        output / f"{prefix}-analysis-separate-window.png",
                    )
                    print(
                        f"separate mode={workspace.presentation_mode()} "
                        f"top-level={workspace.dock.isWindow()}"
                    )
                    layouts = workspace.layout_controller
                    layouts.save_named_layout("Dual-monitor analysis")
                    workspace.layout_menu.popup(
                        workspace.layout_button.mapToGlobal(
                            QPoint(0, workspace.layout_button.height())
                        )
                    )
                    _save_natural_widget(
                        app,
                        workspace.layout_menu,
                        output / f"{prefix}-analysis-layout-menu.png",
                    )
                    workspace.layout_menu.hide()
                    layouts.rebuild_saved_menu()
                    layouts.saved_menu.popup(
                        workspace.layout_button.mapToGlobal(
                            QPoint(0, workspace.layout_button.height())
                        )
                    )
                    _save_natural_widget(
                        app,
                        layouts.saved_menu,
                        output / f"{prefix}-analysis-saved-layouts-menu.png",
                    )
                    layouts.saved_menu.hide()
                if view == "comparison":
                    # T07.1: three exact-time profiles plus one deliberately
                    # incompatible observed time exercise fixed slot meaning.
                    primary_path = captures.SOURCE.parent / "hrrr_point_36.68N_95.66W_f018.npz"
                    collection, station_id = render.decode(str(primary_path))
                    viewer = picker._show_sounding(collection, station_id)
                    exact_time = collection.getCurrentDate()
                    for filename in (
                        "hrrr_point_36.68N_95.66W_f018.spc",
                        "hrrr_kbvo_20260625_06z.buf",
                        "14061619.OAX",
                    ):
                        other, place = render.decode(
                            str(captures.SOURCE.parent / filename)
                        )
                        if exact_time in tuple(getattr(other, "_dates", ()) or ()):
                            other.setCurrentDate(exact_time)
                        picker._show_sounding(other, place)
                    failures = getattr(viewer, "_sharpmod_install_failures", ())
                    if failures:
                        raise RuntimeError("; ".join(failures))
                    viewer.resize(width, height)
                    captures._settle(app)
                    workspace = viewer._sharpmod_analysis_workspace
                    workspace.set_expanded(True)
                    workspace.visual_compare.set_layout_count(4)
                    workspace.open("compare")
                    deadline = time.monotonic() + 30.0
                    while workspace._tasks and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(0.01)
                    if workspace._tasks:
                        raise RuntimeError("Comparison capture did not finish in 30 seconds")
                    captures._settle(app)
                    panels = workspace.visual_compare.canvas.panels()
                    if len(panels) != 4 or panels[3].profile is not None:
                        raise RuntimeError(
                            "Comparison validation fixture did not retain its missing fourth slot"
                        )
                    captures._save_window(
                        app,
                        viewer,
                        output / f"{prefix}-analysis-comparison-slots.png",
                    )
                    print(
                        f"comparison slots={workspace.visual_compare.slot_ids()!r} "
                        f"reference={workspace.compare_reference.currentText()!r} "
                        f"fourth reason={panels[3].unavailable_reason!r}"
                    )
                    # T07.2: the canvas alone, at its real size, so the two
                    # separate unit axes and their labels can be read.
                    canvas = workspace.visual_compare.canvas
                    captures.CAPTURE_WIDTH = canvas.width()
                    captures.CAPTURE_HEIGHT = canvas.height()
                    captures._save_window(
                        app,
                        canvas,
                        output / f"{prefix}-analysis-comparison-axes.png",
                    )
                    print(f"difference spans={canvas.difference_spans()!r}")
                    print(f"canvas units={canvas.accessibleDescription()!r}")
                    print(f"status={workspace.visual_compare.status.text()!r}")

                    # T07.3: fixed ranges shared across slots, then a clamped
                    # case after a much larger difference arrives.
                    workspace.visual_compare.lock_scales.setChecked(True)
                    captures._settle(app)
                    captures.CAPTURE_WIDTH = canvas.width()
                    captures.CAPTURE_HEIGHT = canvas.height()
                    captures._save_window(
                        app,
                        canvas,
                        output / f"{prefix}-analysis-comparison-fixed.png",
                    )
                    print(
                        f"locked spans={canvas.difference_spans()!r} "
                        f"clipped={canvas.clipped_families()!r} "
                        f"status={workspace.visual_compare.status.text()!r}"
                    )
                    workspace.visual_compare.lock_scales.setChecked(False)
                    captures._settle(app)

                    # T07.4: a reordered, reduced column set in the real table.
                    workspace.set_comparison_metric_keys(
                        ("srh_1km", "mlcape", "stp_cin"), persist=False
                    )
                    deadline = time.monotonic() + 30.0
                    while workspace._tasks and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(0.01)
                    captures._settle(app)
                    table = workspace.compare_table
                    captures.CAPTURE_WIDTH = max(360, table.width())
                    captures.CAPTURE_HEIGHT = max(180, table.height())
                    captures._save_window(
                        app,
                        table,
                        output / f"{prefix}-analysis-comparison-columns.png",
                    )
                    print(
                        "columns="
                        + repr(
                            [
                                table.horizontalHeaderItem(column).text()
                                for column in range(table.columnCount())
                            ]
                        )
                    )
                if view == "scientific":
                    # T08: the same multi-sounding fixture, drawn by the vendored
                    # Skew-T and hodograph instead of the simplified traces.
                    from sharpmod import gui_comparison_charts as sci

                    primary_path = captures.SOURCE.parent / "hrrr_point_36.68N_95.66W_f018.npz"
                    collection, station_id = render.decode(str(primary_path))
                    viewer = picker._show_sounding(collection, station_id)
                    exact_time = collection.getCurrentDate()
                    for filename in (
                        "hrrr_point_36.68N_95.66W_f018.spc",
                        "hrrr_kbvo_20260625_06z.buf",
                        "14061619.OAX",
                    ):
                        other, place = render.decode(
                            str(captures.SOURCE.parent / filename)
                        )
                        if exact_time in tuple(getattr(other, "_dates", ()) or ()):
                            other.setCurrentDate(exact_time)
                        picker._show_sounding(other, place)
                    failures = getattr(viewer, "_sharpmod_install_failures", ())
                    if failures:
                        raise RuntimeError("; ".join(failures))
                    viewer.resize(width, height)
                    captures._settle(app)
                    workspace = viewer._sharpmod_analysis_workspace
                    workspace.set_expanded(True)
                    visual = workspace.visual_compare
                    visual.set_layout_count(4)
                    workspace.open("compare")
                    deadline = time.monotonic() + 30.0
                    while workspace._tasks and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(0.01)
                    if workspace._tasks:
                        raise RuntimeError("Chart capture did not finish in 30 seconds")
                    captures._settle(app)

                    def _grab(name):
                        captures.CAPTURE_WIDTH = visual.charts.width()
                        captures.CAPTURE_HEIGHT = visual.charts.height()
                        captures._save_window(
                            app, visual.charts, output / f"{prefix}-{name}.png"
                        )

                    # T08.1 side-by-side real Skew-T, with the reference behind
                    # each candidate.
                    visual.set_chart_mode(sci.CHART_SKEWT)
                    captures._settle(app, 3)
                    _grab("analysis-charts-skewt")
                    drawn = [
                        (
                            view_.title.text(),
                            type(view_.renderer()).__name__,
                            view_.unavailable_reason(),
                            view_.legend.plain_text(),
                        )
                        for view_ in visual.charts.views()
                    ]
                    print(f"skewt slots={drawn!r}")
                    print(f"charts units={visual.charts.accessibleDescription()!r}")

                    # T08.2 one cursor across every slot.
                    visual.canvas.cursorHeightChanged.emit(3000.0)
                    captures._settle(app, 2)
                    _grab("analysis-charts-cursor")
                    print(f"cursor status={visual.status.text()!r}")

                    # T08.3 overlaid arrangement, one colour per profile.
                    visual.set_arrangement(sci.ARRANGEMENT_OVERLAID)
                    captures._settle(app, 3)
                    _grab("analysis-charts-overlaid")
                    print(
                        "overlaid legend="
                        + repr(
                            [
                                view_.legend.plain_text()
                                for view_ in visual.charts.views()
                                if view_.isVisibleTo(visual.charts)
                            ]
                        )
                    )
                    print(
                        "slot colours="
                        + repr([sci.slot_colour(n) for n in (1, 2, 3, 4)])
                        + f" reference={sci.reference_colour()!r}"
                    )

                    # T08.1 the hodograph, which the simplified view never had.
                    visual.set_arrangement(sci.ARRANGEMENT_SIDE_BY_SIDE)
                    visual.set_chart_mode(sci.CHART_HODOGRAPH)
                    captures._settle(app, 3)
                    visual.canvas.cursorHeightChanged.emit(3000.0)
                    captures._settle(app, 2)
                    _grab("analysis-charts-hodograph")
                    print(
                        "hodo readouts="
                        + repr(
                            [
                                (
                                    getattr(view_.renderer(), "readout_hght", None),
                                    view_.unavailable_reason(),
                                )
                                for view_ in visual.charts.views()
                            ]
                        )
                    )

                    # T08.2 the clearly indicated independent option.
                    visual.link_axes.setChecked(False)
                    captures._settle(app, 2)
                    print(f"unlinked status={visual.status.text()!r}")
                    print(f"unlinked units={visual.charts.accessibleDescription()!r}")
                    visual.link_axes.setChecked(True)
                    captures._settle(app, 2)

                    # T08.4 an export at a size the on-screen view never had.
                    visual.set_chart_mode(sci.CHART_SKEWT)
                    captures._settle(app, 3)
                    on_screen = (visual.charts.width(), visual.charts.height())
                    for export_width, export_height in ((1400, 900), (700, 460)):
                        png = visual.export_png(export_width, export_height)
                        destination = (
                            output
                            / f"{prefix}-analysis-charts-export-{export_width}x{export_height}.png"
                        )
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(png)
                        image = QImage.fromData(png, "PNG")
                        print(
                            f"export {destination.name} requested="
                            f"{(export_width, export_height)!r} actual="
                            f"{(image.width(), image.height())!r} "
                            f"on_screen={on_screen!r} bytes={len(png)}"
                        )
                    if (visual.charts.width(), visual.charts.height()) != on_screen:
                        raise RuntimeError("exporting resized the live comparison view")

                if view in {"jobs", "analysis-jobs"}:
                    job_capture = _capture_job_workflows if view == "jobs" else _capture_analysis_job_workflows
                    viewer = job_capture(
                        app, picker, output, prefix, width, height
                    )

                if view in {"acquisition-jobs", "model-acquisition-jobs"}:
                    viewer = _capture_acquisition_job_workflows(
                        app, picker, output, prefix, width, height,
                        models_only=view == "model-acquisition-jobs",
                    )

                if view == "thresholds":
                    # Use five independently decoded profiles and retain two
                    # unavailable members in the acquisition ledger.
                    from sharpmod.ensemble_members import (
                        EnsembleAcquisition,
                        MemberFailure,
                    )
                    from sharpmod.gui_ensemble_thresholds import (
                        EnsembleThresholdExplorer,
                    )
                    from sharpmod.metric_controls import control_for
                    from sharpmod.profile_metrics import ProfileMetricsEngine
                    from sharpmod.profile_timeline import combine_ensemble_collections
                    members = [render.decode(str(captures.SOURCE))[0] for _ in range(5)]
                    labels = ["c00", "p01", "p02", "p03", "p04"]
                    ensemble = combine_ensemble_collections(members, labels)
                    EnsembleAcquisition(
                        requested_members=(*labels, "p05", "p06"),
                        loaded_members=tuple(labels),
                        failures=(
                            MemberFailure("p05", "failed", "HTTP 404 from the source"),
                            MemberFailure("p06", "cancelled", "request was cancelled"),
                        ),
                    ).attach(ensemble)

                    retry_requests = []
                    explorer = EnsembleThresholdExplorer(
                        ProfileMetricsEngine(),
                        request_retry=lambda coll, wanted: (
                            retry_requests.append(tuple(wanted)) or True
                        ),
                    )
                    explorer.resize(max(900, width - 200), max(600, height - 200))
                    explorer.show()
                    captures._settle(app, 2)
                    explorer.refresh(ensemble)
                    captures._settle(app, 2)

                    # T11.1 the controls follow the diagnostic, and switching back
                    # restores the threshold that diagnostic had.
                    _en, metric, _op, value, upper = explorer.condition_rows[0]
                    print(
                        f"mlcape control suffix={value.suffix()!r} "
                        f"decimals={value.decimals()} step={value.singleStep()} "
                        f"range={(value.minimum(), value.maximum())!r} "
                        f"value={value.value()}"
                    )
                    print(f"rule={explorer.definition_summary.text()!r}")
                    value.setValue(1500.0)
                    metric.setCurrentIndex(metric.findData("stp_cin"))
                    print(
                        f"stp control suffix={value.suffix()!r} "
                        f"decimals={value.decimals()} step={value.singleStep()} "
                        f"range={(value.minimum(), value.maximum())!r} "
                        f"value={value.value()} upper={upper.value()}"
                    )
                    print(f"rule={explorer.definition_summary.text()!r}")
                    metric.setCurrentIndex(metric.findData("mlcape"))
                    print(f"mlcape remembered={value.value()}")
                    captures.CAPTURE_WIDTH = explorer.width()
                    captures.CAPTURE_HEIGHT = explorer.height()
                    captures._save_window(
                        app, explorer, output / f"{prefix}-thresholds-controls.png"
                    )

                    def _run_threshold():
                        explorer._evaluate(False)
                        deadline = time.monotonic() + 600.0
                        while explorer._worker is not None and time.monotonic() < deadline:
                            app.processEvents()
                            time.sleep(0.02)
                        if explorer._worker is not None:
                            raise RuntimeError("threshold evaluation did not finish")
                        captures._settle(app, 3)
                        found = explorer._selected_result()
                        if found is None:
                            raise RuntimeError(explorer.status.text())
                        return found

                    # T11.2 evaluate, then set a threshold that actually splits these
                    # members, so the funnel shows a real mix rather than one group.
                    # Derived from the members' own values rather than hardcoded, so
                    # it stays meaningful if the fixture changes.
                    probe = _run_threshold()
                    reported = sorted(
                        item.values["mlcape"]
                        for item in probe.members
                        if item.values.get("mlcape") is not None
                    )
                    print(f"member MLCAPE values={[round(v) for v in reported]!r}")
                    if len(reported) > 1:
                        middle = reported[len(reported) // 2]
                        value.setValue(control_for("mlcape").clamp(middle))
                        print(f"splitting threshold={value.value()}")
                    result = _run_threshold()
                    print(
                        "funnel="
                        + repr(
                            [
                                explorer.timeline.item(0, column).text()
                                for column in range(explorer.timeline.columnCount())
                            ]
                        )
                    )
                    print(
                        f"reconciles={result.reconciles} "
                        f"requested={result.requested_member_count} "
                        f"loaded={result.loaded_member_count} "
                        f"usable={result.usable_member_count} "
                        f"qualifying={result.qualifying_member_count} "
                        f"nonqualifying={result.nonqualifying_member_count} "
                        f"not_loaded={result.not_loaded_member_count}"
                    )
                    print(f"status={explorer.status.text()!r}")
                    print(f"members spoken={explorer.members.accessibleDescription()!r}")
                    captures._save_window(
                        app, explorer, output / f"{prefix}-thresholds-coverage.png"
                    )

                    # T11.3 every count opens the members behind it, with reasons.
                    totals = {}
                    for index in range(explorer.member_filter.count()):
                        explorer.member_filter.setCurrentIndex(index)
                        captures._settle(app)
                        totals[explorer.member_filter.currentText()] = (
                            explorer.members.rowCount()
                        )
                    print(f"filter totals={totals!r}")
                    explorer.member_filter.setCurrentIndex(
                        explorer.member_filter.findText("Not loaded")
                    )
                    captures._settle(app, 2)
                    print(
                        "not-loaded reasons="
                        + repr(
                            [
                                (
                                    explorer.members.item(row, 0).text(),
                                    explorer.members.item(row, 1).text(),
                                    explorer.members.item(row, 2).text(),
                                )
                                for row in range(explorer.members.rowCount())
                            ]
                        )
                    )
                    print(f"member count text={explorer.member_count.text()!r}")
                    print(f"retry enabled={explorer.retry.isEnabled()}")
                    captures._save_window(
                        app, explorer, output / f"{prefix}-thresholds-excluded.png"
                    )
                    # T11.4 the retry asks for exactly the members this result could
                    # not use. The callback records the request rather than fetching.
                    explorer._retry_unavailable()
                    captures._settle(app, 2)
                    print(f"retry requested={retry_requests!r}")
                    print(f"retry status={explorer.status.text()!r}")
                    explorer.member_filter.setCurrentIndex(
                        explorer.member_filter.findText("Qualifying")
                    )
                    captures._settle(app, 2)
                    print(
                        "qualifying members="
                        + repr(
                            [
                                explorer.members.item(row, 0).text()
                                for row in range(explorer.members.rowCount())
                            ]
                        )
                    )
                    captures._save_window(
                        app, explorer, output / f"{prefix}-thresholds-qualifying.png"
                    )
                    explorer.shutdown()
                    explorer.close()

                if view in {"analysis", "both"}:
                    collection, station_id = render.decode(str(captures.SOURCE))
                    viewer = picker._show_sounding(collection, station_id)
                    failures = getattr(viewer, "_sharpmod_install_failures", ())
                    if failures:
                        raise RuntimeError("; ".join(failures))
                    viewer.resize(width, height)
                    captures._settle(app)
                    workspace = viewer._sharpmod_analysis_workspace
                    workspace.trend_metric.setCurrentIndex(
                        workspace.trend_metric.findData("srh_1km")
                    )
                    viewer.resize(width, height)
                    workspace.open("Trends")
                    deadline = time.monotonic() + 30.0
                    while workspace._tasks and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(0.01)
                    if workspace._tasks:
                        raise RuntimeError("Analysis capture did not finish in 30 seconds")
                    if workspace.trend_status.property("statusLevel") == "error":
                        raise RuntimeError(workspace.trend_status.text())
                    workspace.trend_chart.setFocus()
                    captures._save_window(app, viewer, output / f"{prefix}-analysis.png")
        finally:
            if field_fetch_patch is not None:
                field_fetch_patch.stop()
            if viewer is not None:
                viewer.close()
            if picker is not None:
                picker.close()
            captures._settle(app, 4)
            if previous_settings is None:
                os.environ.pop("SHARPMOD_SETTINGS_PATH", None)
            else:
                os.environ["SHARPMOD_SETTINGS_PATH"] = previous_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-scale", type=int, choices=TEXT_SCALE_OPTIONS, default=100)
    parser.add_argument("--density", choices=DENSITY_OPTIONS, default="comfortable")
    parser.add_argument("--style", choices=("standard", "inverted", "protanopia"), default="standard")
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=950)
    parser.add_argument("--view", choices=("picker", "analysis", "both", "charts", "palette", "selector", "coordinates", "recents", "data-management", "sessions", "profiles", "closed", "interaction", "locator", "overlay", "navigation", "comparison", "scientific", "thresholds", "jobs", "analysis-jobs", "acquisition-jobs", "model-acquisition-jobs"), default="both")
    parser.add_argument("--stage", choices=("t01", "t02", "t03", "t04", "t05", "t06", "t07", "t08", "t09", "t11", "t13", "t14", "t15", "t17", "t18", "t19", "t20", "t21", "t22", "t25", "t26"), default="t01")
    parser.add_argument("--query", default="")
    parser.add_argument("--source", choices=("Station Map", "Station List", "Forecast Model",
                                           "Field Panels", "Reanalysis (ERA5)", "Open File"),
                        default="Station Map")
    parser.add_argument("--collapse-rail", action="store_true")
    parser.add_argument("--rail-width", type=int)
    parser.add_argument("--station")
    capture(**vars(parser.parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
