"""GUI contracts for exact-frame animation export."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import threading
import time

from PIL import Image
from qtpy.QtCore import QSettings, Qt
from qtpy.QtWidgets import QMainWindow, QWidget

from sharpmod.animation_exports import write_animation_gif
from sharpmod import export_paths, gui_animation as animation_ui, gui_export
from sharpmod.export_presentation import ExportPresentation, recent_exports
from sharpmod import gui_theme
from sharpmod.gui_animation import AnimationWorkspace
from sharpmod.gui_common import scrollable_page
from sharpmod.gui_jobs import JobStatus


UTC = timezone.utc
RUN = datetime(2026, 9, 13, 0, tzinfo=UTC)


def _wait(app, predicate, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


class _Collection:
    def __init__(self, model, run, dates, available):
        self._dates = list(dates)
        self._prof_idx = 0
        self._highlight = "control"
        self._profs = {
            "control": [object() if item else None for item in available]
        }
        self._meta = {"loc": "KOUN", "model": model, "run": run}

    def getMeta(self, key):  # noqa: N802 - upstream API
        return self._meta[key]

    def setCurrentDate(self, value):  # noqa: N802 - upstream API
        self._prof_idx = self._dates.index(value)


class _Viewer(QWidget):
    def __init__(self, collections):
        super().__init__()
        self.prof_collections = list(collections)
        self.prof_ids = [f"profile-{index}" for index in range(len(collections))]
        self.pc_idx = 0
        self.resize(360, 260)
        self.setStyleSheet("background: #17324d;")

    def setProfileCollection(self, profile_id):  # noqa: N802 - upstream API
        self.pc_idx = self.prof_ids.index(profile_id)

    def updateProfs(self):  # noqa: N802 - upstream API
        self.update()


class _Host:
    def __init__(self, win, collections):
        self._win = win
        self._items = collections

    def _collections(self):
        return list(self._items)

    @staticmethod
    def _focused_index():
        return 0

    def _window(self):
        return self._win


def _workspace(qt_app, *, settings=None):
    dates = (RUN, RUN + timedelta(hours=3), RUN + timedelta(hours=6))
    collections = [
        _Collection("HRRR", RUN, dates, (True, False, True)),
        _Collection(
            "RAP",
            RUN - timedelta(hours=3),
            (RUN + timedelta(hours=1),),
            (True,),
        ),
    ]
    win = QMainWindow()
    viewer = _Viewer(collections)
    win.setCentralWidget(viewer)
    win.spc_widget = viewer
    win._settings = settings
    workspace = AnimationWorkspace(_Host(win, collections))
    win.show()
    workspace.show()
    qt_app.processEvents()
    workspace.refresh()
    return win, workspace, collections


def test_animation_workspace_exposes_animation_only_controls(qt_app):
    win, workspace, _collections = _workspace(qt_app)
    try:
        for name in (
            "title",
            "soundings",
            "build_document",
            "html_button",
            "pdf_button",
        ):
            assert not hasattr(workspace, name)
        assert workspace.objectName() == "analysisAnimationWorkspace"
        assert workspace.gif_button.isEnabled()
    finally:
        workspace.shutdown()
        workspace.close()
        win.close()


def test_forecast_gif_keeps_exact_missing_frame_fixed_scale_and_state(
    qt_app, tmp_path
):
    win, workspace, collections = _workspace(qt_app)
    try:
        win.spc_widget.pc_idx = 1
        collections[0]._prof_idx = 2
        frames = workspace.capture_animation_frames()

        assert [item.label for item in frames] == ["F000", "F003", "F006"]
        assert frames[1].png_bytes is None
        assert "absent at this exact time" in frames[1].missing_reason
        assert frames[0].png_bytes.startswith(b"\x89PNG")
        assert frames[2].png_bytes.startswith(b"\x89PNG")
        assert win.spc_widget.pc_idx == 1
        assert collections[0]._prof_idx == 2

        chosen = workspace.capture_animation_frames(
            output_size=(800, 600), theme="light"
        )
        with Image.open(io.BytesIO(chosen[0].png_bytes)) as source:
            assert source.size == (800, 561)
        assert win.spc_widget.pc_idx == 1
        assert collections[0]._prof_idx == 2

        path = write_animation_gif(
            tmp_path / "timeline.gif",
            frames,
            mode="forecast-timeline",
            frame_duration_ms=250,
        )
        with Image.open(io.BytesIO(frames[0].png_bytes)) as source:
            source_size = source.size
        with Image.open(io.BytesIO(frames[2].png_bytes)) as source:
            assert source.size == source_size
        with Image.open(path) as image:
            assert image.n_frames == 3
            assert image.size == (source_size[0], source_size[1] + 42)
    finally:
        workspace.shutdown()
        workspace.close()
        win.close()


def test_run_to_run_mode_never_substitutes_a_nearby_valid_time(qt_app):
    win, workspace, _collections = _workspace(qt_app)
    try:
        workspace.mode.setCurrentIndex(workspace.mode.findData("run-to-run"))
        frames = workspace.capture_animation_frames()

        assert len(frames) == 2
        assert frames[0].valid_time == frames[1].valid_time == RUN
        missing = [item for item in frames if item.png_bytes is None]
        assert len(missing) == 1
        assert "focused exact valid time" in missing[0].missing_reason
    finally:
        workspace.shutdown()
        workspace.close()
        win.close()


def test_gif_cancel_rejects_candidate_and_retry_uses_saved_frames(
    qt_app, tmp_path, monkeypatch
):
    destination = tmp_path / "timeline.gif"
    destination.write_bytes(b"prior complete GIF")
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def queued_writer(
        path,
        frames,
        *,
        mode,
        frame_duration_ms,
        output_size,
        gap_policy,
        expected_interval_seconds,
        theme,
        context,
        cancelled,
        progress,
    ):
        calls.append(
            (
                mode, frame_duration_ms, tuple(frame.label for frame in frames),
                output_size, gap_policy, expected_interval_seconds, theme,
                context,
            )
        )
        entered.set()
        release.wait(2.0)
        Path(path).write_bytes(b"GIF89a-ready candidate")
        progress(len(frames), len(frames))
        return Path(path)

    monkeypatch.setattr(animation_ui, "write_animation_gif", queued_writer)
    monkeypatch.setattr(
        animation_ui.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(destination), ""),
    )
    win, workspace, _collections = _workspace(qt_app)
    try:
        workspace.duration.setValue(400)
        workspace._export_gif()
        _wait(qt_app, entered.is_set)

        assert isinstance(workspace.job, JobStatus)
        assert workspace.job.snapshot.total == 3
        assert "forecast timeline" in workspace.job.snapshot.affected_input.lower()
        assert destination.name in workspace.job.retained_label.text()
        workspace.frames.selectRow(2)
        panel_size = workspace.size()

        workspace._cancel()
        release.set()
        _wait(qt_app, lambda: workspace._worker is None)

        assert destination.read_bytes() == b"prior complete GIF"
        assert workspace.job.snapshot.state == "cancelled"
        assert workspace.job.snapshot.retryable
        assert workspace.frames.currentRow() == 2
        assert workspace.size() == panel_size
        assert tuple(tmp_path.iterdir()) == (destination,)

        workspace.mode.setCurrentIndex(workspace.mode.findData("run-to-run"))
        workspace.duration.setValue(900)
        workspace.job.retry_button.click()
        _wait(qt_app, lambda: workspace._worker is None)

        assert calls == [
            ("forecast-timeline", 400, ("F000", "F003", "F006"),
             (1920, 1080), "cards", 10800.0, "dark", "KOUN · HRRR"),
            ("forecast-timeline", 400, ("F000", "F003", "F006"),
             (1920, 1080), "cards", 10800.0, "dark", "KOUN · HRRR"),
        ]
        assert destination.read_bytes() == b"GIF89a-ready candidate"
        assert workspace.job.snapshot.counts.completed == 1
    finally:
        release.set()
        workspace.shutdown()
        workspace.close()
        win.close()


def test_export_job_surface_fits_a_narrow_pane_at_200_percent(qt_app):
    gui_theme.apply_theme(qt_app, color_style="standard", text_scale=200)
    win, workspace, _collections = _workspace(qt_app)
    scroll = scrollable_page(workspace)
    try:
        scroll.resize(560, 650)
        scroll.show()
        workspace.job.begin(
            1,
            "GIF export",
            "Saved exact-time capture and destination",
            "Encoding captured frames",
            total=3,
            unit="frames",
            cancellable=True,
        )
        qt_app.processEvents()

        assert scroll.horizontalScrollBar().maximum() == 0
        assert not workspace.job.grab().isNull()
    finally:
        workspace.shutdown()
        scroll.close()
        win.close()
        gui_theme.apply_theme(qt_app, color_style="standard", text_scale=100)


def test_gif_range_gap_preview_dimensions_defaults_and_completion(
    qt_app, tmp_path, monkeypatch
):
    application = tmp_path / "application"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    one_off = tmp_path / "one-off" / "timeline.gif"
    one_off.parent.mkdir()
    starts = []

    def choose(_parent, _title, start, _filter):
        starts.append(Path(start))
        return (str(one_off), "") if len(starts) == 1 else (start, "")

    monkeypatch.setattr(animation_ui.QFileDialog, "getSaveFileName", choose)
    opened = []
    monkeypatch.setattr(
        gui_export.QDesktopServices, "openUrl",
        lambda url: opened.append(Path(url.toLocalFile())) or True,
    )
    win, workspace, collections = _workspace(qt_app, settings=settings)
    collections[0]._meta["provider"] = "NCEP"
    try:
        workspace.sizes.set_presentation(
            ExportPresentation(800, 600, "custom", True, "light")
        )
        workspace.duration.setValue(350)
        workspace.frames.item(1, 0).setCheckState(Qt.Unchecked)
        assert workspace.frame_count.text().startswith("3 output frames")
        assert "1 explicit gap card" in workspace.frame_count.text()
        workspace.preview_index.setValue(2)
        workspace._preview_frame()
        assert "gap:" in workspace.preview_note.text()
        assert "800 × 600 px" in workspace.preview_note.text()
        assert not workspace.preview.pixmap().isNull()

        workspace._export_gif()
        _wait(qt_app, lambda: workspace._worker is None, timeout=8)
        assert one_off.is_file()
        with Image.open(one_off) as image:
            assert image.size == (800, 600)
            assert image.n_frames == 3
            assert image.info["duration"] == 350
            manifest = json.loads(image.info["comment"].decode("utf-8"))
            assert manifest["gap_policy"] == "cards"
            assert manifest["theme"] == "light"
            assert manifest["context"] == "KOUN · HRRR-NCEP"
            assert "discontinuity" in manifest["missing"][1]
        assert workspace.job.snapshot.state == "completed"
        assert one_off.name in workspace.completion.label.text()
        assert workspace.completion.open_button.isEnabled()
        workspace.completion.open_file()
        workspace.completion.open_folder()
        workspace.completion.copy_path()
        assert opened == [one_off, one_off.parent]
        assert qt_app.clipboard().text() == str(one_off)
        assert recent_exports(settings)[0].path == str(one_off)

        dedicated = application / "rendered_soundings"
        starts[0].write_bytes(b"older unrelated artifact")
        workspace._export_gif()
        _wait(qt_app, lambda: workspace._worker is None, timeout=8)
        assert [path.parent for path in starts] == [dedicated, dedicated]
        assert starts[1].name.endswith("-2.gif")
        assert starts[1].is_file() and starts[1].read_bytes().startswith(b"GIF89a")
        assert starts[0].read_bytes() == b"older unrelated artifact"
        assert recent_exports(settings)[0].path == str(starts[1])
        assert recent_exports(settings)[1].path == str(one_off)

        workspace.gap_policy.setCurrentIndex(workspace.gap_policy.findData("stop"))
        assert workspace.frame_count.text().startswith("1 output frame")
        workspace.gap_policy.setCurrentIndex(workspace.gap_policy.findData("error"))
        assert not workspace.gif_button.isEnabled()
        assert "Fail export" in workspace.frame_count.text()
        workspace.gap_policy.setCurrentIndex(workspace.gap_policy.findData("cards"))

        restored = AnimationWorkspace(workspace.host)
        try:
            assert restored.sizes.presentation().size == (800, 600)
            assert restored.duration.value() == 350
            assert restored.gap_policy.currentData() == "cards"
        finally:
            restored.shutdown()
            restored.close()
    finally:
        workspace.shutdown()
        workspace.close()
        win.close()


def test_gif_failure_discards_candidate_and_never_records_partial_completion(
    qt_app, tmp_path, monkeypatch
):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    destination = tmp_path / "prior.gif"
    destination.write_bytes(b"GIF89a prior complete")

    def partial_writer(path, _frames, **_options):
        Path(path).write_bytes(b"half-encoded")
        raise OSError("disk is full")

    monkeypatch.setattr(animation_ui, "write_animation_gif", partial_writer)
    monkeypatch.setattr(
        animation_ui.QFileDialog, "getSaveFileName",
        lambda *_a, **_kw: (str(destination), ""),
    )
    win, workspace, _collections = _workspace(qt_app, settings=settings)
    try:
        workspace._export_gif()
        _wait(qt_app, lambda: workspace._worker is None)
        assert workspace.job.snapshot.state == "failed"
        assert workspace.job.snapshot.retryable
        assert "disk is full" in workspace.status.text()
        assert "No artifact was replaced" in workspace.status.text()
        assert destination.read_bytes() == b"GIF89a prior complete"
        assert list(tmp_path.glob(".*.ready.gif")) == []
        assert recent_exports(settings) == ()
        assert not workspace.completion.open_button.isEnabled()
    finally:
        workspace.shutdown()
        workspace.close()
        win.close()
