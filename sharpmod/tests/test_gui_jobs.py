"""Shared long-operation presentation and lifecycle regressions."""

from dataclasses import FrozenInstanceError

import pytest
from qtpy.QtWidgets import QLineEdit, QVBoxLayout, QWidget

from sharpmod import gui_theme
from sharpmod.gui_common import scrollable_page
from sharpmod.gui_jobs import JobCounts, JobStatus


def test_unknown_total_never_becomes_an_invented_percentage(qt_app):
    job = JobStatus()
    try:
        job.begin(1, "Model field", "HRRR F006 at 18Z", "Downloading")
        assert job.progress.minimum() == job.progress.maximum() == 0
        assert "HRRR F006 at 18Z" in job.input_label.text()
        assert "total unknown" in job.progress_label.text()
        assert "%" not in job.progress_label.text()

        job.update(1, stage="Decoding", done=3)
        assert job.progress.maximum() == 0
        assert "3 processed" in job.progress_label.text()
        assert "Decoding" in job.status.text()

        job.finish(1, counts=JobCounts(completed=1), message="Actual 01:46Z received")
        assert job.snapshot.state == "completed"
        assert job.progress.isHidden()
        assert "Completed 1" in job.count_label.text()
        assert "requested" not in job.count_label.text()
    finally:
        job.close()


def test_known_progress_and_terminal_counts_reconcile(qt_app):
    job = JobStatus()
    try:
        job.begin(2, "Sensitivity", "HRRR F006 at 18Z", "Calculating", total=5, unit="cells")
        job.update(2, done=2)
        assert job.progress.maximum() == 5
        assert job.progress.value() == 2
        assert "2 / 5 cells" in job.progress_label.text()

        job.finish(
            2,
            counts=JobCounts(completed=2, failed=1, cancelled=2, requested=5),
            outcome="cancelled",
            retryable=True,
        )
        assert job.snapshot.state == "cancelled"
        assert "Completed 2" in job.count_label.text()
        assert "failed 1" in job.count_label.text()
        assert "cancelled 2" in job.count_label.text()
        assert "requested 5" in job.count_label.text()
        assert job.retry_button.isEnabled()
        with pytest.raises(ValueError, match="requested"):
            JobCounts(completed=6, requested=5)
        with pytest.raises(ValueError, match="nonnegative"):
            JobCounts(failed=-1)
        with pytest.raises(FrozenInstanceError):
            job.snapshot.counts.completed = 99
    finally:
        job.close()


def test_unknown_outcomes_are_not_reported_as_unattempted_or_success(qt_app):
    counts = JobCounts(completed=1, unknown=2, requested=3)
    assert counts.unattempted == 0
    assert "outcome unknown 2" in counts.summary
    assert "not attempted" not in counts.summary
    with pytest.raises(ValueError, match="requested"):
        JobCounts(completed=1, unknown=3, requested=3)
    job = JobStatus()
    try:
        job.begin(1, "Timeline", "saved 00Z request", "Fetching", total=3)
        job.finish(1, counts=counts)
        assert job.snapshot.state == "partial"
    finally:
        job.close()


def test_cancel_is_a_request_until_acknowledged_and_late_updates_are_rejected(qt_app):
    job = JobStatus()
    try:
        job.begin(1, "Model", "HRRR 12Z F006", "Downloading", cancellable=True)
        assert job.request_cancel(1)
        assert job.snapshot.state == "cancelling"
        assert "Cancel requested" in job.status.text()
        assert not job.cancel_button.isEnabled()
        assert not job.snapshot.counts.cancelled
        job.update(1, stage="Decoding")
        assert "Cancel requested" in job.status.text()

        job.finish(1, counts=JobCounts(cancelled=1, requested=1), outcome="cancelled")
        terminal = job.snapshot
        assert not job.update(1, stage="Success arriving late")
        assert not job.finish(1, counts=JobCounts(completed=1))
        assert job.snapshot is terminal

        job.begin(2, "Model", "HRRR 18Z F006", "Downloading")
        current = job.snapshot
        assert not job.update(1, stage="Old request")
        assert not job.finish(1, counts=JobCounts(completed=1))
        assert job.snapshot is current
        assert "HRRR 18Z F006" in job.accessibleDescription()
    finally:
        job.close()


def test_retained_identity_survives_failure_and_retry_does_not_start_itself(qt_app):
    job = JobStatus()
    retries = []
    job.retryRequested.connect(lambda: retries.append(job.snapshot.affected_input))
    try:
        job.begin(
            3, "Model field", "HRRR F006 at 18Z", "Downloading",
            retained="HRRR field 2026-09-13 01:46:22Z",
        )
        job.finish(
            3, counts=JobCounts(failed=1, requested=1), outcome="failed",
            message="HTTP 503; try the same request again", retryable=True,
        )
        assert "01:46:22Z" in job.retained_label.text()
        assert "Retained result" in job.accessibleDescription()
        job.retry_button.click()
        assert retries == ["HRRR F006 at 18Z"]
        assert job.snapshot.state == "failed"

        job.begin(4, "Model field", "HRRR F012 at 18Z", "Downloading")
        assert job.retained_label.isHidden()
        assert job.retry_button.isHidden()
        job.invalidate("Input changed; previous request is superseded")
        assert not job.accepts(4)
        assert not job.finish(4, counts=JobCounts(completed=1))
    finally:
        job.close()


def test_job_updates_preserve_focus_and_fit_a_narrow_200_percent_pane(qt_app):
    gui_theme.apply_theme(qt_app, color_style="standard", text_scale=200)
    content = QWidget()
    layout = QVBoxLayout(content)
    editor = QLineEdit("Keep editing this note")
    job = JobStatus()
    layout.addWidget(editor)
    layout.addWidget(job)
    scroll = scrollable_page(content)
    try:
        scroll.resize(420, 550)
        scroll.show()
        editor.setFocus()
        editor.setCursorPosition(5)
        qt_app.processEvents()
        job.begin(
            10, "Ensemble", "A long forecast identity with an exact valid UTC time",
            "Comparing members", cancellable=True,
            retained="Previously completed set at 2026-05-20 18:00Z",
        )
        job.update(10, stage="Calculating probabilities")
        qt_app.processEvents()

        assert editor.hasFocus()
        assert editor.cursorPosition() == 5
        assert scroll.horizontalScrollBar().maximum() == 0
        assert not job.grab().isNull()
    finally:
        scroll.close()
        gui_theme.apply_theme(qt_app, color_style="standard", text_scale=100)


def test_compact_job_details_scroll_instead_of_compressing_text(qt_app):
    gui_theme.apply_theme(qt_app, color_style="standard", text_scale=200)
    job = JobStatus()
    scroll = scrollable_page(job)
    try:
        scroll.resize(976, 104)
        scroll.show()
        job.begin(
            1, "Forecast timeline", "GFS initialization 2014-06-16 18:00Z; F000–F006 at KOUN",
            "Fetching saved unresolved hours", total=3, cancellable=True,
            retained="GFS initialization 2014-06-16 18:00Z; valid 2014-06-16 18:00Z; loaded F000",
        )
        qt_app.processEvents()
        assert scroll.verticalScrollBar().maximum() > 0
        assert scroll.horizontalScrollBar().maximum() == 0
        for label in (job.status, job.input_label, job.retained_label):
            assert label.height() >= label.heightForWidth(label.width())
        scroll.ensureWidgetVisible(job.retained_label)
        qt_app.processEvents()
        assert scroll.verticalScrollBar().value() > 0
    finally:
        scroll.close()
        gui_theme.apply_theme(qt_app, color_style="standard", text_scale=100)


def test_default_scale_keeps_context_compact_and_actions_reachable(qt_app):
    gui_theme.apply_theme(qt_app, color_style="standard", text_scale=100)
    job = JobStatus()
    scroll = scrollable_page(job)
    try:
        scroll.resize(520, 260)
        scroll.show()
        full_input = (
            "GIF export \u00b7 C:\\Users\\shian\\OneDrive\\Desktop\\sharppy reimagined\\"
            "SHARPpy Reimagined\\rendered_soundings\\improvement-goal\\t13\\"
            "standard-text100-expanded-job-export.gif"
        )
        job.begin(
            1,
            "GIF export",
            full_input,
            "Encoding fixed-scale frames",
            cancellable=True,
            retained="Prior GIF at 2014-06-16 19:00Z",
        )
        qt_app.processEvents()

        assert scroll.horizontalScrollBar().maximum() == 0
        assert job.cancel_button.isVisible()
        assert "…" in job.input_label.text()
        assert full_input in job.input_label.toolTip()
        assert job.progress_label.isVisible()
    finally:
        scroll.close()
        gui_theme.apply_theme(qt_app, color_style="standard", text_scale=100)
