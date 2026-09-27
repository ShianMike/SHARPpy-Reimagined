"""Shared image/GIF sizing, real composition previews, and export recovery."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from qtpy.QtCore import Qt, QTimer, QUrl, Signal
from qtpy.QtGui import QDesktopServices, QPixmap
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from sharpmod.ui.features.export_presentation import (
    DIMENSION_PRESETS,
    MAX_EXPORT_DIMENSION,
    MIN_EXPORT_DIMENSION,
    PRESET_BY_KEY,
    ExportPresentation,
    recent_exports,
    remember_export,
)
from sharpmod.ui.features.gui_theme import current_theme
from sharpmod.ui.styles.theme import OBJ_HINT, OBJ_STATUS, SPACE


def resolved_export_theme(value: str) -> str:
    return ("dark" if current_theme().is_dark else "light") if value == "current" else value


def _open_target(owner, path: Path, *, folder: bool = False) -> bool:
    target = path.parent if folder else path
    if not target.exists():
        QMessageBox.warning(
            owner,
            "Export not found",
            f"The {'folder' if folder else 'file'} no longer exists:\n{target}\n"
            "Check whether it was moved or choose another recent export.",
        )
        return False
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
        QMessageBox.warning(
            owner,
            "Could not open export",
            f"Your system could not open:\n{target}\nUse Copy path to find it manually.",
        )
        return False
    return True


def copy_export_path(path: Path) -> None:
    QApplication.clipboard().setText(str(path))


class ExportSizeControls(QWidget):
    """Exact pixel/aspect choices shared by stills and animations."""

    changed = Signal()

    def __init__(self, presentation: ExportPresentation | None = None, *, caption=True, parent=None):
        super().__init__(parent)
        self.setObjectName("exportSizeControls")
        self._updating = False
        self._ratio = 1.0
        form = QVBoxLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.addWidget(QLabel("Output size / aspect", self))
        self.preset = QComboBox(self)
        for item in DIMENSION_PRESETS:
            self.preset.addItem(item.label, item.key)
        self.preset.addItem("Custom pixels", "custom")
        self.preset.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.preset.setMinimumContentsLength(1)
        self.preset.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        form.addWidget(self.preset)

        sizes = QWidget(self)
        size_row = QGridLayout(sizes)
        size_row.setContentsMargins(0, 0, 0, 0)
        self.width = QSpinBox(sizes)
        self.width.setRange(MIN_EXPORT_DIMENSION, MAX_EXPORT_DIMENSION)
        self.width.setSuffix(" px")
        self.width.setAccessibleName("Export width in pixels")
        self.height = QSpinBox(sizes)
        self.height.setRange(MIN_EXPORT_DIMENSION, MAX_EXPORT_DIMENSION)
        self.height.setSuffix(" px")
        self.height.setAccessibleName("Export height in pixels")
        self.lock_aspect = QCheckBox("Lock aspect", sizes)
        self.lock_aspect.setChecked(True)
        size_row.addWidget(self.width, 0, 0)
        size_row.addWidget(QLabel("×"), 0, 1)
        size_row.addWidget(self.height, 0, 2)
        size_row.addWidget(self.lock_aspect, 1, 0, 1, 3)
        form.addWidget(QLabel("Exact dimensions", self))
        form.addWidget(sizes)

        self.theme = QComboBox(self)
        self.theme.addItem("Current display / frame theme", "current")
        self.theme.addItem("Dark frame (canvas stays current)", "dark")
        self.theme.addItem("Light frame (canvas stays current)", "light")
        self.theme.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.theme.setMinimumContentsLength(1)
        self.theme.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        form.addWidget(QLabel("Frame theme", self))
        form.addWidget(self.theme)
        self.caption = QCheckBox("Include location, source and valid-time caption", self)
        self.caption.setVisible(bool(caption))
        if caption:
            form.addWidget(self.caption)
        self.pixel_label = QLabel(self)
        self.pixel_label.setObjectName(OBJ_HINT)
        self.pixel_label.setWordWrap(True)
        form.addWidget(self.pixel_label)

        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.width.valueChanged.connect(lambda _value: self._size_changed("width"))
        self.height.valueChanged.connect(lambda _value: self._size_changed("height"))
        self.lock_aspect.toggled.connect(self._lock_changed)
        self.theme.currentIndexChanged.connect(self.changed)
        self.caption.toggled.connect(self.changed)
        self.set_presentation(presentation or ExportPresentation())

    def _preset_changed(self, *_args):
        if self._updating:
            return
        key = self.preset.currentData()
        if key != "custom":
            preset = PRESET_BY_KEY[key]
            self._updating = True
            try:
                self.width.setValue(preset.width)
                self.height.setValue(preset.height)
                self._ratio = preset.width / preset.height
            finally:
                self._updating = False
        self._update_label()
        self.changed.emit()

    def _size_changed(self, axis):
        if self._updating:
            return
        self._updating = True
        try:
            self.preset.setCurrentIndex(self.preset.findData("custom"))
            if self.lock_aspect.isChecked():
                if axis == "width":
                    self.height.setValue(round(self.width.value() / self._ratio))
                else:
                    self.width.setValue(round(self.height.value() * self._ratio))
            else:
                self._ratio = self.width.value() / self.height.value()
        finally:
            self._updating = False
        self._update_label()
        self.changed.emit()

    def _lock_changed(self, locked):
        if self._updating:
            return
        if locked:
            self._ratio = self.width.value() / self.height.value()
        self.changed.emit()

    def _update_label(self):
        self.pixel_label.setText(
            f"{self.width.value():,} × {self.height.value():,} output pixels. "
            "The on-screen window size is not changed; letterboxing preserves "
            "the displayed canvas aspect when it differs from the output."
        )

    def set_presentation(self, value: ExportPresentation):
        self._updating = True
        controls = (
            self.preset, self.width, self.height, self.lock_aspect,
            self.caption, self.theme,
        )
        blocked = [control.blockSignals(True) for control in controls]
        try:
            self.width.setValue(value.width)
            self.height.setValue(value.height)
            self._ratio = value.width / value.height
            self.lock_aspect.setChecked(value.lock_aspect)
            preset = PRESET_BY_KEY.get(value.preset)
            index = self.preset.findData(value.preset) if (
                preset is not None and preset.size == value.size
            ) else -1
            self.preset.setCurrentIndex(
                index if index >= 0 else self.preset.findData("custom")
            )
            self.caption.setChecked(value.include_caption)
            self.theme.setCurrentIndex(max(0, self.theme.findData(value.theme)))
        finally:
            for control, previous in zip(controls, blocked):
                control.blockSignals(previous)
            self._updating = False
        self._update_label()

    def presentation(self) -> ExportPresentation:
        return ExportPresentation(
            self.width.value(),
            self.height.value(),
            self.preset.currentData() or "custom",
            self.caption.isChecked(),
            self.theme.currentData(),
            self.lock_aspect.isChecked(),
        )


class ExportImageDialog(QDialog):
    """Preview the actual full-resolution pixmap that will be saved."""

    def __init__(
        self,
        renderer: Callable[..., QPixmap],
        *,
        content: str,
        presentation: ExportPresentation | None = None,
        title: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("exportImageDialog")
        self.setWindowTitle("Configure map figure" if title is not None
                            else "Configure sounding image")
        self.resize(760, 680)
        self._renderer = renderer
        self._pixmap = None
        self._rendered_presentation = None
        self._rendered_title = None
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE["sm"])
        controls = QWidget(self)
        control_layout = QVBoxLayout(controls)
        control_layout.setContentsMargins(0, 0, 0, 0)
        self.content = QLabel(f"Selected content: {content}", controls)
        self.content.setWordWrap(True)
        control_layout.addWidget(self.content)
        self.title_edit = None
        if title is not None:
            control_layout.addWidget(QLabel("Figure title", controls))
            self.title_edit = QLineEdit(str(title), controls)
            self.title_edit.setAccessibleName("Map figure title")
            control_layout.addWidget(self.title_edit)
        self.sizes = ExportSizeControls(presentation, parent=controls)
        control_layout.addWidget(self.sizes)
        control_layout.addStretch(1)
        controls_scroll = QScrollArea(self)
        controls_scroll.setObjectName("exportConfigurationScroll")
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        controls_scroll.setMinimumHeight(190)
        controls_scroll.setWidget(controls)
        layout.addWidget(controls_scroll, 1)
        self.preview = QLabel(self)
        self.preview.setObjectName("exportImagePreview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(320, 190)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.preview.setToolTip("Actual output composition scaled down for display")
        layout.addWidget(self.preview, 1)
        self.status = QLabel(self)
        self.status.setObjectName(OBJ_STATUS)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Save).setText("Choose destination…")
        buttons.accepted.connect(self._accept_snapshot)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.capture_preview)
        self.sizes.changed.connect(lambda: self._timer.start(180))
        if self.title_edit is not None:
            self.title_edit.textChanged.connect(lambda: self._timer.start(180))
        self._timer.start(0)

    def capture_preview(self):
        try:
            presentation = self.sizes.presentation()
            pixmap = (self._renderer(presentation, self.figure_title)
                      if self.title_edit is not None
                      else self._renderer(presentation))
            if pixmap is None or pixmap.isNull() or (
                pixmap.width(), pixmap.height()
            ) != presentation.size:
                raise ValueError("renderer returned an empty or wrong-size preview")
        except Exception as exc:  # noqa: BLE001 - GUI render boundary
            self._pixmap = None
            self._rendered_presentation = None
            self._rendered_title = None
            self.preview.setText("Preview unavailable. Adjust the size or retry.")
            self.status.setText(f"Could not render output: {exc}")
            return
        self._pixmap = pixmap
        self._rendered_presentation = presentation
        self._rendered_title = self.figure_title
        self.preview.setPixmap(
            pixmap.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.status.setText(
            f"Preview and saved PNG use this same {pixmap.width():,} × "
            f"{pixmap.height():,} pixel composition. "
            f"Theme: {resolved_export_theme(presentation.theme)}."
        )

    def resizeEvent(self, event):  # noqa: N802 - Qt hook
        super().resizeEvent(event)
        if self._pixmap is not None:
            self.preview.setPixmap(
                self._pixmap.scaled(
                    self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )

    def _accept_snapshot(self):
        self._timer.stop()
        if (self._rendered_presentation != self.sizes.presentation()
                or self._rendered_title != self.figure_title):
            self.capture_preview()
        if self._pixmap is not None:
            self.accept()

    @property
    def output_pixmap(self):
        return self._pixmap

    @property
    def presentation(self) -> ExportPresentation:
        return self._rendered_presentation or self.sizes.presentation()

    @property
    def figure_title(self) -> str:
        return self.title_edit.text().strip() if self.title_edit is not None else ""

    def release(self):
        """Drop the full-resolution snapshot and parent-window render closure."""
        self._timer.stop()
        self._pixmap = None
        self._renderer = None
        self.deleteLater()


class RecentExportsDialog(QDialog):
    """Small bounded history, including one-off paths and missing-file context."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recent exports")
        self.setObjectName("recentExportsDialog")
        self.resize(720, 450)
        layout = QVBoxLayout(self)
        self.items = QListWidget(self)
        self.items.setWordWrap(True)
        layout.addWidget(self.items, 1)
        self.records = recent_exports(settings)
        for record in self.records:
            size = (
                f" · {record.width} × {record.height}"
                if record.width is not None and record.height is not None
                else ""
            )
            state = "" if record.exists else " · missing or empty"
            item = QListWidgetItem(
                f"{record.name} · {record.kind}{size}{state}\n"
                f"{record.summary}\n{record.path}"
            )
            item.setData(Qt.UserRole, record.path)
            self.items.addItem(item)
        if self.items.count():
            self.items.setCurrentRow(0)
        else:
            self.items.addItem("No completed image or GIF exports recorded yet.")
        row = QHBoxLayout()
        for label, callback in (
            ("Open file", self.open_file),
            ("Open folder", self.open_folder),
            ("Copy path", self.copy_path),
        ):
            button = QPushButton(label, self)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.Close, self)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

    def _selected(self) -> Path | None:
        item = self.items.currentItem()
        raw = item.data(Qt.UserRole) if item is not None else None
        return Path(raw) if raw else None

    def open_file(self):
        path = self._selected()
        if path is not None:
            _open_target(self, path)

    def open_folder(self):
        path = self._selected()
        if path is not None:
            _open_target(self, path, folder=True)

    def copy_path(self):
        path = self._selected()
        if path is not None:
            copy_export_path(path)


class ExportCompletionPanel(QWidget):
    """Actions are enabled only after a verified, complete artifact exists."""

    def __init__(self, settings_provider: Callable[[], object], parent=None):
        super().__init__(parent)
        self.setObjectName("exportCompletionPanel")
        self._settings_provider = settings_provider
        self._last_path = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel("No completed export yet.", self)
        self.label.setObjectName(OBJ_STATUS)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        row = QGridLayout()
        self.open_button = QPushButton("Open file", self)
        self.folder_button = QPushButton("Open folder", self)
        self.copy_button = QPushButton("Copy path", self)
        self.recent_button = QPushButton("Recent exports…", self)
        for index, button in enumerate(
            (self.open_button, self.folder_button, self.copy_button, self.recent_button)
        ):
            row.addWidget(button, index // 2, index % 2)
        layout.addLayout(row)
        self.open_button.clicked.connect(self.open_file)
        self.folder_button.clicked.connect(self.open_folder)
        self.copy_button.clicked.connect(self.copy_path)
        self.recent_button.clicked.connect(self.show_recent)
        self._update_buttons()

    def _update_buttons(self):
        present = self._last_path is not None
        for button in (self.open_button, self.folder_button, self.copy_button):
            button.setEnabled(present)

    def completed(self, path, *, kind, summary="", dimensions=None):
        record = remember_export(
            self._settings_provider(), path, kind=kind, summary=summary,
            dimensions=dimensions,
        )
        self._last_path = Path(record.path)
        self.label.setText(
            f"Last completed {kind}: {record.name}\n{record.path}"
        )
        self._update_buttons()
        return record

    def open_file(self):
        if self._last_path is not None:
            _open_target(self, self._last_path)

    def open_folder(self):
        if self._last_path is not None:
            _open_target(self, self._last_path, folder=True)

    def copy_path(self):
        if self._last_path is not None:
            copy_export_path(self._last_path)

    def show_recent(self):
        dialog = RecentExportsDialog(self._settings_provider(), self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()


__all__ = [
    "ExportCompletionPanel", "ExportImageDialog", "ExportSizeControls",
    "RecentExportsDialog", "copy_export_path", "resolved_export_theme",
]
