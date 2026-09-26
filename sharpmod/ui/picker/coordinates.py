"""Preview pasted coordinates before updating the existing point controls."""

import weakref

from qtpy.QtCore import QEvent, QObject, Qt
from qtpy.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QToolButton, QVBoxLayout, QWidget,
)

from sharpmod.ui.features.coordinate_input import CoordinateInputError, interpret_coordinates
from sharpmod.ui.features.gui_common import make_status_label, scrollable_page, set_status_label
from sharpmod.ui.styles.theme import OBJ_HINT, OBJ_NUMERIC, SPACE


class CoordinateDialog(QDialog):
    def __init__(self, lat_spin, lon_spin, *, source="selected source", apply_ref=None,
                 initial_text=""):
        super().__init__(lat_spin.window())
        self._lat_ref, self._lon_ref = weakref.ref(lat_spin), weakref.ref(lon_spin)
        self._apply_ref = apply_ref
        self.selected_point = None
        self.setObjectName("coordinatePreviewDialog")
        self.setWindowTitle("Paste coordinates")
        self.resize(min(780, max(380, lat_spin.window().width() - 80)),
                    min(650, max(430, lat_spin.window().height() - 80)))
        outer = QVBoxLayout(self)
        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setSpacing(SPACE["sm"])
        hint = QLabel(
            "Paste decimal degrees or N/S/E/W coordinates, including degrees° minutes′ seconds″. "
            "Check the preview before applying."
        )
        hint.setWordWrap(True)
        hint.setObjectName(OBJ_HINT)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.input = QLineEdit(self)
        self.input.setMaxLength(1024)
        self.input.setAccessibleName("Pasted latitude and longitude")
        self.input.setPlaceholderText("e.g. lat:35.63 lon:-97.44, or 35.63N 97.44W")
        self.input.setClearButtonEnabled(True)
        row.addWidget(self.input, 1)
        paste = QToolButton(self)
        paste.setText("Paste")
        paste.setAccessibleName("Paste coordinates from clipboard")
        paste.clicked.connect(self.input.paste)
        row.addWidget(paste)
        layout.addLayout(row)
        layout.addWidget(QLabel("Unlabeled number order"))
        self.order = QComboBox(self)
        self.order.setAccessibleName("Interpret unlabeled coordinate order")
        self.order.addItem("Automatic — only unambiguous axes", "auto")
        self.order.addItem("Latitude first, longitude second", "lat_lon")
        self.order.addItem("Longitude first, latitude second", "lon_lat")
        layout.addWidget(self.order)
        self.preview = QLabel(self)
        self.preview.setObjectName(OBJ_NUMERIC)
        self.preview.setTextFormat(Qt.PlainText)
        self.preview.setAccessibleName("Interpreted requested point")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.preview.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(self.preview)
        self.status = make_status_label(parent=self)
        source_label = QLabel(f"Applies to {source}. This selects a point; it does not load a sounding.")
        source_label.setTextFormat(Qt.PlainText)
        source_label.setObjectName(OBJ_HINT)
        source_label.setWordWrap(True)
        layout.addWidget(source_label)
        layout.addStretch(1)
        self.body = scrollable_page(content, parent=self, accessible_name="Coordinate entry and preview")
        outer.addWidget(self.body, 1)
        outer.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        self.apply_button = buttons.button(QDialogButtonBox.Ok)
        self.apply_button.setText("Use this point")
        self.apply_button.setAutoDefault(False)
        self.apply_button.setDefault(False)
        buttons.accepted.connect(self.use_point)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.input.textChanged.connect(self.refresh_preview)
        self.order.currentIndexChanged.connect(self.refresh_preview)
        self.input.returnPressed.connect(self.use_point)
        self.input.setText(initial_text)
        self.refresh_preview()

    def refresh_preview(self, *_args):
        self.selected_point = None
        try:
            interpreted = interpret_coordinates(self.input.text(), order=self.order.currentData())
            lat_spin, lon_spin = self._lat_ref(), self._lon_ref()
            if lat_spin is None or lon_spin is None:
                raise CoordinateInputError("The point controls are no longer available. Cancel this preview.")
            lat_decimals, lon_decimals = lat_spin.decimals(), lon_spin.decimals()
            lat, lon = round(interpreted.lat, lat_decimals), round(interpreted.lon, lon_decimals)
            self.selected_point = (lat, lon)
            self.preview.setText(
                f"Latitude:  {abs(lat):.{lat_decimals}f}° {'S' if lat < 0 else 'N'}\n"
                f"Longitude: {abs(lon):.{lon_decimals}f}° {'W' if lon < 0 else 'E'}\n"
                "Requested point — the source's grid point may differ."
            )
            self.preview.setAccessibleDescription(self.preview.text())
            rounded = lat != interpreted.lat or lon != interpreted.lon
            message = interpreted.explanation + (
                f". Rounded to the controls' precision ({lat_decimals}/{lon_decimals} decimal places)." if rounded else "."
            )
            set_status_label(self.status, message)
            self.apply_button.setEnabled(True)
        except (CoordinateInputError, RuntimeError) as exc:
            self.preview.setText("No interpreted point yet. Current coordinates are unchanged.")
            self.preview.setAccessibleDescription(self.preview.text())
            set_status_label(self.status, str(exc), level="warning" if self.input.text() else "info")
            self.apply_button.setEnabled(False)

    def use_point(self, *_args):
        # Reinterpret at confirmation, including current control precision.
        self.refresh_preview()
        if self.selected_point is None:
            return
        if self._apply_ref is not None:
            apply = self._apply_ref()
            try:
                if apply is None:
                    raise CoordinateInputError("The source is no longer available. Cancel this preview.")
                apply(*self.selected_point)
            except (CoordinateInputError, RuntimeError) as exc:
                set_status_label(self.status, str(exc), level="warning")
                self.apply_button.setEnabled(False)
                return
        self.accept()


class _CoordinateController(QObject):
    def __init__(self, owner, lat_spin, lon_spin, changed, source):
        super().__init__(owner)
        self._lat_ref, self._lon_ref = weakref.ref(lat_spin), weakref.ref(lon_spin)
        self._changed_ref = weakref.WeakMethod(changed)
        self._owner_ref = weakref.ref(owner)
        self.source, self.dialog = source, None
        self._button_ref = None
        lat_spin.installEventFilter(self)
        lon_spin.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if event.type() == QEvent.EnabledChange and self._button_ref is not None:
            button, lat_spin, lon_spin = self._button_ref(), self._lat_ref(), self._lon_ref()
            if button is not None and lat_spin is not None and lon_spin is not None:
                button.setEnabled(lat_spin.isEnabled() and lon_spin.isEnabled())
        return super().eventFilter(watched, event)

    def open(self, *_args):
        lat_spin, lon_spin = self._lat_ref(), self._lon_ref()
        if lat_spin is None or lon_spin is None or not lat_spin.isEnabled() or not lon_spin.isEnabled():
            return
        if self.dialog is not None:
            try:
                self.dialog.deleteLater()
            except RuntimeError:
                pass
        self.dialog = CoordinateDialog(lat_spin, lon_spin, source=self.source,
                                       apply_ref=weakref.WeakMethod(self.apply_point))
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.dialog.input.setFocus()

    def apply_point(self, lat, lon):
        lat_spin, lon_spin, changed = self._lat_ref(), self._lon_ref(), self._changed_ref()
        if lat_spin is None or lon_spin is None or changed is None:
            raise CoordinateInputError("The point controls are no longer available. Cancel this preview.")
        if not lat_spin.isEnabled() or not lon_spin.isEnabled():
            raise CoordinateInputError("Point controls are busy or unavailable. Try again when the operation finishes.")
        if not lat_spin.minimum() <= lat <= lat_spin.maximum() or not lon_spin.minimum() <= lon <= lon_spin.maximum():
            raise CoordinateInputError("The previewed point exceeds this source's coordinate limits.")
        lat_blocked, lon_blocked = lat_spin.blockSignals(True), lon_spin.blockSignals(True)
        try:
            lat_spin.setValue(lat)
            lon_spin.setValue(lon)
        finally:
            lat_spin.blockSignals(lat_blocked)
            lon_spin.blockSignals(lon_blocked)
        changed(center=True)
        owner = self._owner_ref()
        remember = getattr(owner, "_remember_point", None)
        if callable(remember):
            remember(lat, lon)


def coordinate_paste_button(owner, lat_spin, lon_spin, changed, *, source):
    controller = _CoordinateController(owner, lat_spin, lon_spin, changed, source)
    button = QToolButton(lat_spin.parentWidget())
    button.setText("Paste coordinates…")
    button.setAccessibleName(f"Paste and preview coordinates for {source}")
    button.setToolTip("Preview pasted latitude/longitude before changing the point")
    button.clicked.connect(controller.open)
    controller._button_ref = weakref.ref(button)
    button.setEnabled(lat_spin.isEnabled() and lon_spin.isEnabled())
    button._sharpmod_coordinate_controller = controller
    return button
