"""T27 real PNG preview, completion, and one-off-path GUI contracts."""

from __future__ import annotations

from pathlib import Path

from qtpy.QtCore import QSettings, Qt
from qtpy.QtGui import QImage, QPixmap
from qtpy.QtWidgets import QLabel, QVBoxLayout, QWidget

from sharpmod import gui_export, gui_theme, render
from sharpmod.export_presentation import ExportPresentation, recent_exports
from sharpmod.gui_export import (
    ExportCompletionPanel, ExportImageDialog, ExportSizeControls,
    RecentExportsDialog,
)


def _canvas(qt_app):
    widget = QWidget()
    widget.setStyleSheet("background: #123456; color: white;")
    layout = QVBoxLayout(widget)
    layout.addWidget(QLabel("KOUN · HRRR · valid 2026-09-22 03Z"))
    widget.resize(640, 480)
    widget.show()
    qt_app.processEvents()
    return widget


def test_preset_custom_aspect_and_pixel_safety(qt_app):
    controls = ExportSizeControls()
    try:
        controls.preset.setCurrentIndex(controls.preset.findData("presentation"))
        assert controls.presentation().size == (1600, 1200)
        assert controls.presentation().aspect_label == "4:3"
        controls.width.setValue(1200)
        assert controls.presentation().size == (1200, 900)
        assert controls.presentation().preset == "custom"
        controls.lock_aspect.setChecked(False)
        controls.height.setValue(1000)
        assert controls.presentation().size == (1200, 1000)
        assert not controls.presentation().lock_aspect
        controls.width.setValue(4096)
        controls.height.setValue(4096)
        assert "safety limit" in _render_error(controls)
    finally:
        controls.close()


def _render_error(controls):
    try:
        controls.presentation()
    except ValueError as exc:
        return str(exc)
    return ""


def test_exact_snapshot_preview_matches_saved_png_and_leaves_canvas_unchanged(
    qt_app, tmp_path
):
    canvas = _canvas(qt_app)
    before = canvas.grab().toImage()
    size = canvas.size()
    dialog = ExportImageDialog(
        lambda choices: render.compose_widget_pixmap(
            canvas, *choices.size,
            caption="KOUN · HRRR · valid 2026-09-22 03Z"
            if choices.include_caption else "",
            theme=choices.theme,
        ),
        content="Entire focused sounding · KOUN · HRRR",
        presentation=ExportPresentation(1200, 900, "custom", True, "light"),
    )
    try:
        dialog.show()
        qt_app.processEvents()
        dialog.capture_preview()
        assert "1,200 × 900" in dialog.status.text()
        snapshot = dialog.output_pixmap
        assert snapshot.size().width() == 1200
        assert snapshot.size().height() == 900
        destination = tmp_path / "one-off" / "configured.png"
        assert render.save_pixmap_png_atomic(snapshot, str(destination))
        image = QImage(str(destination))
        assert image.size() == snapshot.size()
        for point in ((20, 20), (600, 300), (20, 880), (1100, 880)):
            assert image.pixelColor(*point) == snapshot.toImage().pixelColor(*point)
        assert canvas.size() == size and canvas.grab().toImage() == before

        dialog.sizes.caption.setChecked(False)
        dialog.sizes.width.setValue(800)
        dialog.sizes.height.setValue(600)
        dialog._accept_snapshot()
        assert dialog.result() == dialog.Accepted
        assert dialog.presentation.size == (800, 600)
        assert (dialog.output_pixmap.width(), dialog.output_pixmap.height()) == (800, 600)
    finally:
        dialog.close()
        canvas.close()


def test_preview_remains_visible_and_configuration_scrolls_at_200_percent(qt_app):
    gui_theme.apply_theme(qt_app, color_style="standard", text_scale=200)
    canvas = _canvas(qt_app)
    dialog = ExportImageDialog(
        lambda choice: render.compose_widget_pixmap(canvas, *choice.size,
                                                     caption="KOUN HRRR valid 03Z"),
        content="KOUN HRRR valid 03Z",
    )
    try:
        dialog.show()
        qt_app.processEvents()
        dialog.capture_preview()
        scroll = dialog.findChild(gui_export.QScrollArea, "exportConfigurationScroll")
        assert scroll is not None
        assert scroll.horizontalScrollBar().maximum() == 0
        assert scroll.verticalScrollBar().maximum() > 0
        assert dialog.preview.height() >= 190
        assert not dialog.preview.pixmap().isNull()
        assert dialog.findChild(gui_export.QDialogButtonBox).button(
            gui_export.QDialogButtonBox.Save
        ).isVisible()
    finally:
        dialog.close()
        canvas.close()
        gui_theme.apply_theme(qt_app, color_style="standard", text_scale=100)


def test_atomic_png_failure_retains_existing_bytes(tmp_path):
    destination = tmp_path / "existing.png"
    destination.write_bytes(b"existing complete png")

    class BrokenPixmap:
        @staticmethod
        def isNull():  # noqa: N802 - QPixmap-shaped fake
            return False

        @staticmethod
        def save(candidate, _format, _quality):
            Path(candidate).write_bytes(b"partial")
            return False

    assert not render.save_pixmap_png_atomic(BrokenPixmap(), str(destination))
    assert destination.read_bytes() == b"existing complete png"
    assert list(tmp_path.iterdir()) == [destination]


def test_completion_actions_recent_exports_and_missing_file_recovery(
    qt_app, tmp_path, monkeypatch
):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    artifact = tmp_path / "one-off" / "real.png"
    artifact.parent.mkdir()
    pixmap = QPixmap(640, 480)
    pixmap.fill(Qt.darkBlue)
    assert render.save_pixmap_png_atomic(pixmap, str(artifact))
    panel = ExportCompletionPanel(lambda: settings)
    opened = []
    monkeypatch.setattr(
        gui_export.QDesktopServices, "openUrl",
        lambda url: opened.append(Path(url.toLocalFile())) or True,
    )
    try:
        assert not panel.open_button.isEnabled()
        panel.completed(
            artifact, kind="PNG", summary="KOUN HRRR", dimensions=(640, 480)
        )
        assert panel.open_button.isEnabled()
        assert str(artifact) in panel.label.text()
        panel.open_file()
        panel.open_folder()
        panel.copy_path()
        assert opened == [artifact, artifact.parent]
        assert qt_app.clipboard().text() == str(artifact)
        assert recent_exports(settings)[0].path == str(artifact)
        dialog = RecentExportsDialog(settings)
        try:
            assert dialog.items.count() == 1
            assert "KOUN HRRR" in dialog.items.item(0).text()
            dialog.copy_path()
            assert qt_app.clipboard().text() == str(artifact)
        finally:
            dialog.close()
        artifact.unlink()
        missing = RecentExportsDialog(settings)
        try:
            assert "missing or empty" in missing.items.item(0).text()
        finally:
            missing.close()
    finally:
        panel.close()
