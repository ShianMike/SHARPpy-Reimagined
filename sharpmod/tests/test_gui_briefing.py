"""GUI contracts for briefing capture and exact-frame animation export."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
from types import SimpleNamespace

from PIL import Image
from qtpy.QtWidgets import (
    QComboBox,
    QMainWindow,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from sharpmod.briefing_exports import (
    write_animation_gif,
    write_briefing_html,
    write_briefing_pdf,
)
from sharpmod.gui_briefing import BriefingWorkspace


UTC = timezone.utc
RUN = datetime(2026, 9, 13, 0, tzinfo=UTC)


class _Collection:
    def __init__(self, model, run, dates, available):
        self._dates = list(dates)
        self._prof_idx = 0
        self._highlight = "control"
        self._profs = {
            "control": [object() if item else None for item in available]
        }
        self._meta = {
            "loc": "KOUN",
            "model": model,
            "run": run,
            "requested_lat": 35.22,
            "requested_lon": -97.44,
            "selected_lat": 35.20,
            "selected_lon": -97.46,
            "source_provider": f"test {model}",
        }

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
        self.notes = QPlainTextEdit()
        self.notes.setPlainText("Watch the cap and disclose missing frames.")
        self.compare_table = QTableWidget(1, 2)
        self.compare_table.setHorizontalHeaderLabels(("Sounding", "MLCAPE"))
        self.compare_table.setItem(0, 0, QTableWidgetItem("HRRR · KOUN"))
        self.compare_table.setItem(0, 1, QTableWidgetItem("1532 J/kg"))
        self.trend_metric = QComboBox()
        self.trend_metric.addItem("MLCAPE", "mlcape")
        sample = SimpleNamespace(
            valid_time=RUN,
            values={"mlcape": 1532.0},
            available=True,
            reason=None,
        )
        self._trend_series = (SimpleNamespace(label="HRRR", samples=(sample,)),)

    def _collections(self):
        return list(self._items)

    @staticmethod
    def _focused_index():
        return 0

    def _window(self):
        return self._win


def _workspace(qt_app):
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
    host = _Host(win, collections)
    workspace = BriefingWorkspace(host)
    win.show()
    workspace.show()
    qt_app.processEvents()
    workspace.refresh()
    return win, workspace, collections


def test_briefing_uses_actual_pixels_displayed_rows_and_restores_viewer(
    qt_app, tmp_path
):
    win, workspace, collections = _workspace(qt_app)
    try:
        win.spc_widget = win.centralWidget()
        win.spc_widget.pc_idx = 1
        collections[0]._prof_idx = 2
        document = workspace.build_document()

        assert len(document.soundings) == 2
        assert all(
            item.image.png_bytes.startswith(b"\x89PNG")
            for item in document.soundings
        )
        assert document.comparison_rows == (
            {"Sounding": "HRRR · KOUN", "MLCAPE": "1532 J/kg"},
        )
        assert document.trend_rows[0]["Displayed value"] == 1532.0
        assert "Watch the cap" in document.notes
        assert win.spc_widget.pc_idx == 1
        assert collections[0]._prof_idx == 2

        html = write_briefing_html(tmp_path / "briefing.html", document)
        pdf = write_briefing_pdf(tmp_path / "briefing.pdf", document)
        assert "data:image/png;base64," in html.read_text(encoding="utf-8")
        assert pdf.read_bytes().startswith(b"%PDF-")
    finally:
        workspace.shutdown()
        workspace.close()
        win.close()


def test_forecast_gif_keeps_exact_missing_frame_fixed_scale_and_state(
    qt_app, tmp_path
):
    win, workspace, collections = _workspace(qt_app)
    try:
        win.spc_widget = win.centralWidget()
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
        win.spc_widget = win.centralWidget()
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
