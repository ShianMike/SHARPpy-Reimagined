"""Dialog for choosing a geographic box and its sampling plan.

It owns input presentation and validation; ``BoxWorkflowMixin`` coordinates extraction
workers and sends the resulting samples into analysis."""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QCheckBox
from qtpy.QtWidgets import QComboBox
from qtpy.QtWidgets import QDialog
from qtpy.QtWidgets import QDialogButtonBox
from qtpy.QtWidgets import QFormLayout
from qtpy.QtWidgets import QLabel
from qtpy.QtWidgets import QRadioButton
from qtpy.QtWidgets import QSpinBox
from qtpy.QtWidgets import QVBoxLayout
from sharpmod.analysis.box_sounding import BoxSamplePlan
from sharpmod.analysis.box_sounding import MAX_BOX_HOURS
from sharpmod.analysis.box_sounding import MAX_BOX_POINTS
from sharpmod.analysis.box_sounding import MAX_POINT_PROVIDER_POINTS
from sharpmod.analysis.box_sounding import MAX_SEQUENCE_NODES
from sharpmod.analysis.box_sounding import describe_plan
from sharpmod.analysis.box_sounding import plan_box_samples
from sharpmod.ui.features.gui_theme import mono_font


class BoxPlanDialog(QDialog):
    """Review the planned work before the explicit extraction action.

    A drag only edits a geographic area. This dialog is reached by Review &
    extract; it shows the resolved lattice, spacing, and estimated transfers
    and starts no worker until its Average/Extract confirmation is accepted.
    """

    def __init__(self, model, region, *, parent=None, target_points=None,
                 available_hours=(), start_hour=0):
        super().__init__(parent)
        self.setWindowTitle("Review area extraction")
        self._model = model
        self._region = region
        self._plan: BoxSamplePlan | None = None
        self._available_hours = tuple(
            sorted({int(hour) for hour in available_hours or ()}))
        self._start_hour = int(start_hour)

        ceiling = MAX_BOX_POINTS
        try:
            from sharpmod.tools import model_extract

            if model_extract.point_only_provider(model):
                ceiling = MAX_POINT_PROVIDER_POINTS
        except Exception:
            pass

        layout = QVBoxLayout(self)

        # What the box should produce. Averaging is the ordinary case -- one
        # sounding describing the airmass over an area -- so it leads and is the
        # default. The field workspace answers a different and rarer question:
        # *where inside* the box something peaks.
        self._mode_mean = QRadioButton("Average the box into one sounding")
        self._mode_mean.setToolTip(
            "Every grid point in the box is averaged into a single profile and "
            "opened like any other sounding.\n"
            "Winds are averaged as components and moisture as mixing ratio, so "
            "neither is biased by averaging the wrong quantity.\n"
            "The derived parameters belong to the averaged column: CAPE of the "
            "mean sounding is not the mean of the members' CAPE."
        )
        self._mode_field = QRadioButton("Explore the box as a parameter field")
        self._mode_field.setToolTip(
            "Open the area workspace instead: one parameter at a time as a "
            "field map, ingredient screens, the spread across the box, and any "
            "cell openable as its own sounding."
        )
        self._mode_mean.setChecked(True)
        layout.addWidget(self._mode_mean)
        layout.addWidget(self._mode_field)

        form = QFormLayout()
        self._points = QSpinBox()
        self._points.setRange(4, ceiling)
        self._points.setSingleStep(4)
        self._points.setValue(min(ceiling, int(target_points or 64)))
        self._points.setToolTip(
            "Soundings to aim for. The spacing is rounded up to a whole "
            "multiple of the model's grid, so the count lands near this value "
            "rather than exactly on it."
        )
        form.addRow("Grid points", self._points)

        # Which single hour to sample. Defaults to whatever the sidebar has
        # selected, so the box follows the run the user is already looking at,
        # but it is changeable here rather than making them close the dialog,
        # change the sidebar, and draw the rectangle again.
        self._hour_combo = QComboBox()
        self._hour_combo.setToolTip(
            "The forecast hour to sample. Starts on the hour selected in the "
            "sidebar; changing it here does not disturb that selection."
        )
        for hour in self._available_hours:
            self._hour_combo.addItem(f"F{hour:03d}", hour)
        if not self._available_hours:
            # Nothing was published to choose from, so offer the sidebar's hour
            # alone rather than an empty control.
            self._hour_combo.addItem(f"F{self._start_hour:03d}",
                                     self._start_hour)
        index = self._hour_combo.findData(self._start_hour)
        self._hour_combo.setCurrentIndex(max(0, index))
        self._offers_hour_choice = self._hour_combo.count() > 1
        if self._offers_hour_choice:
            form.addRow("Forecast hour", self._hour_combo)

        # Forecast-hour sequence. Built unconditionally and shown by
        # ``_on_mode_changed``: choosing an earlier hour can make a sequence
        # possible that was not when the dialog opened, and rows that were never
        # added to the layout could not appear for it.
        later = [
            hour for hour in self._available_hours if hour >= self._start_hour
        ]
        self._sequence_check = QCheckBox(
            f"Step through forecast hours from F{self._start_hour:03d}")
        self._sequence_check.setToolTip(
            "Sample the same box at several forecast hours so the field can be "
            "watched evolving. Each hour is a separate model-hour group; the "
            "transfer estimate depends on the provider and may change with cache "
            "hits or retries."
        )
        self._hour_count = QSpinBox()
        self._hour_count.setRange(2, MAX_BOX_HOURS)
        self._hour_count.setValue(min(MAX_BOX_HOURS, max(2, len(later))))
        self._hour_step = QSpinBox()
        self._hour_step.setRange(1, 24)
        self._hour_step.setValue(1)
        self._hour_step.setSuffix(" h")
        self._offers_sequence = len(later) > 1
        layout.addWidget(self._sequence_check)
        form.addRow("Hours", self._hour_count)
        form.addRow("Every", self._hour_step)
        self._sequence_check.toggled.connect(self._on_sequence_toggled)
        self._hour_count.valueChanged.connect(self._replan)
        self._hour_step.valueChanged.connect(self._replan)
        self._hour_count.setEnabled(False)
        self._hour_step.setEnabled(False)
        # Connected only now that everything it touches exists.
        self._hour_combo.currentIndexChanged.connect(self._on_hour_changed)
        layout.addLayout(form)

        self._summary = QLabel()
        self._summary.setFont(mono_font("body"))
        self._summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.button(QDialogButtonBox.Ok).setText("Extract")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._ok_button = buttons.button(QDialogButtonBox.Ok)

        self._points.valueChanged.connect(self._replan)
        self._mode_mean.toggled.connect(self._on_mode_changed)
        self._on_hour_changed()

    def _on_mode_changed(self, *_args) -> None:
        # Stepping hours produces one field per hour, which the field workspace
        # can animate. A mean is a single rendered sounding, so the sequence
        # controls are put away rather than left offering something the result
        # cannot express.
        wanted = self.mode() == "field" and self._offers_sequence
        self._sequence_check.setVisible(wanted)
        if not wanted and self._sequence_check.isChecked():
            self._sequence_check.setChecked(False)
        for widget in (self._hour_count, self._hour_step):
            widget.setVisible(wanted)
            label = self._form_label_for(widget)
            if label is not None:
                label.setVisible(wanted)
        self._replan()

    def _form_label_for(self, widget):
        """Return the form label paired with ``widget``, when there is one."""
        layout = widget.parentWidget().layout() if widget.parentWidget() else None
        if isinstance(layout, QFormLayout):
            return layout.labelForField(widget)
        for candidate in self.findChildren(QFormLayout):
            label = candidate.labelForField(widget)
            if label is not None:
                return label
        return None

    def mode(self) -> str:
        """Return ``"mean"`` for one averaged sounding, else ``"field"``."""
        return "mean" if self._mode_mean.isChecked() else "field"

    def fxx(self) -> int:
        """Return the chosen forecast hour.

        Falls back to the sidebar's hour, which is also the initial selection, so
        a caller never has to decide what an empty control meant.
        """
        value = self._hour_combo.currentData()
        if value is None:
            return self._start_hour
        try:
            return int(value)
        except (TypeError, ValueError):
            return self._start_hour

    def _on_hour_changed(self, *_args) -> None:
        # The sequence starts at the chosen hour, so its label and its offer both
        # follow this control rather than the hour the dialog opened on.
        chosen = self.fxx()
        later = [hour for hour in self._available_hours if hour >= chosen]
        self._sequence_check.setText(
            f"Step through forecast hours from F{chosen:03d}")
        self._offers_sequence = len(later) > 1
        if not self._offers_sequence and self._sequence_check.isChecked():
            self._sequence_check.setChecked(False)
        self._hour_count.setMaximum(max(2, min(MAX_BOX_HOURS, len(later))))
        self._on_mode_changed()

    def _on_sequence_toggled(self, enabled) -> None:
        self._hour_count.setEnabled(bool(enabled))
        self._hour_step.setEnabled(bool(enabled))
        self._replan()

    def hours(self) -> tuple[int, ...] | None:
        """Return the chosen forecast hours, or ``None`` for a single hour.

        Keyed off the offer and the checkbox rather than ``isVisible()``, which
        is false for a dialog that has not been shown yet and made the answer
        depend on whether anyone had looked at it.
        """
        if self.mode() == "mean" or not self._offers_sequence:
            return None
        if not self._sequence_check.isChecked():
            return None
        step = max(1, int(self._hour_step.value()))
        first = self.fxx()
        chosen = [
            hour for hour in self._available_hours
            if hour >= first and (hour - first) % step == 0
        ]
        chosen = chosen[: int(self._hour_count.value())]
        return tuple(chosen) if len(chosen) > 1 else None

    def _replan(self) -> None:
        try:
            self._plan = plan_box_samples(
                self._model, self._region,
                target_points=int(self._points.value()))
        except Exception as exc:  # noqa: BLE001 - report, do not crash
            self._plan = None
            self._summary.setText(str(exc))
            self._ok_button.setEnabled(False)
            return
        planned_hours = self.hours() if self.mode() == "field" else None
        text = describe_plan(
            self._plan,
            hour_count=len(planned_hours) if planned_hours else 1,
        )
        if self.mode() == "mean":
            points = len(self._plan.requestable_points)
            self._ok_button.setText("Average")
            self._summary.setText(
                text
                + f"\nHour       F{self.fxx():03d}"
                + f"\nResult     one sounding averaged from {points} grid "
                  f"point(s)"
                + "\n\nThe average describes the airmass over the box. Its "
                  "derived parameters belong to the averaged column, so they "
                  "are not the mean of the individual points' values."
            )
            self._ok_button.setEnabled(True)
            return
        self._ok_button.setText("Extract")
        hours = planned_hours
        if not hours:
            text += f"\nHour       F{self.fxx():03d}"
        if hours:
            points = len(self._plan.requestable_points)
            total = points * len(hours)
            text += (
                f"\nHours      {len(hours)} "
                f"(F{hours[0]:03d} to F{hours[-1]:03d})"
            )
            if total > MAX_SEQUENCE_NODES:
                self._summary.setText(
                    text
                    + f"\n\n{total} soundings is above the "
                      f"{MAX_SEQUENCE_NODES} allowed for one sequence. "
                      "Reduce the hours or the target soundings."
                )
                self._ok_button.setEnabled(False)
                return
        self._summary.setText(text)
        self._ok_button.setEnabled(True)

    def plan(self) -> BoxSamplePlan | None:
        """Return the resolved plan, or ``None`` when it cannot be built."""
        return self._plan
