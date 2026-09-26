"""Display-only picker context and corrective navigation.

Source controllers and their existing coverage/time methods remain authoritative.
This module neither constructs requests nor stores a second scientific selection.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from qtpy.QtCore import QEvent, QObject, QTimer
from qtpy.QtWidgets import QCheckBox, QComboBox, QLineEdit, QWidget

from sharpmod.ui.picker.layout import CollapsibleRailSection
from sharpmod.ui.features.gui_common import set_status_label


def _instant(value):
    if not isinstance(value, datetime):
        return "not reported"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _point(owner, source):
    return (f"{getattr(owner, f'_{source}_lat').value():.4f}°, "
            f"{getattr(owner, f'_{source}_lon').value():.4f}°")


def _local_file_exists(path):
    try:
        return bool(path) and Path(path).is_file()
    except (OSError, ValueError):
        return False


class PickerSelectionFeedback(QObject):
    """Coalesce native input changes; explicit controller hooks cover blocked signals."""

    def __init__(self, owner, pane, source, primary):
        super().__init__(pane)
        self.owner, self.pane, self.source, self.primary = owner, pane, source, primary
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.refresh)
        for widget in pane.rail.findChildren(QWidget):
            for name in ("valueChanged", "dateChanged", "currentIndexChanged",
                         "textChanged", "toggled", "itemSelectionChanged"):
                signal = getattr(widget, name, None)
                if signal is not None and hasattr(signal, "connect"):
                    signal.connect(self.queue_refresh)
        if source == "uwyo":
            owner._station_list.itemSelectionChanged.connect(self.queue_refresh)
        primary.installEventFilter(self)
        self.refresh()

    def queue_refresh(self, *_args):
        self._timer.start(0)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if event.type() in {QEvent.EnabledChange, QEvent.Show, QEvent.Hide}:
            self.queue_refresh()
        return super().eventFilter(watched, event)

    def _reveal(self, field):
        return lambda: self.pane.reveal_field(field)

    def _details(self):
        owner, source = self.owner, self.source
        message, level, correction, action = "", "info", "", None
        if source in {"map", "uwyo"}:
            sid = (owner._map_selected_id if source == "map"
                   else owner._selected_station_id())
            when = owner._map_when() if source == "map" else owner._selected_when()
            station = owner._station(sid) if sid else None
            location = (f"{sid} — {station['name']} "
                        f"({station['lat']:.2f}°, {station['lon']:.2f}°)"
                        if station else sid or "No station selected")
            combo = getattr(owner, "_map_source", None)
            provider = combo.currentText() if combo is not None else owner._observed_source().upper()
            parts = [f"Observed — {provider}", location,
                     f"Requested observation {_instant(when)}"]
            if not sid:
                if source == "map":
                    message, level = "Choose a station before loading a sounding.", "warn"
                    correction = "Choose station…"
                    action = lambda: owner._select_tab("Station List")
        elif source in {"model", "panels"}:
            cfg = owner._model_config() if source == "model" else owner._panels_config()
            cycle = getattr(owner, f"_{source}_cycle").currentData()
            run = (owner._model_run_time() if source == "model" else owner._panels_run_time()) if (
                cycle is not None
            ) else None
            lead = getattr(owner, f"_{source}_fxx_combo").currentData()
            valid = run + timedelta(hours=int(lead)) if lead is not None and run is not None else None
            label = cfg.label if cfg is not None else "No model selected"
            parts = [label, f"Requested point {_point(owner, source)}",
                     f"Initialization {_instant(run)}" if run is not None else "No initialization selected",
                     f"Lead +{int(lead)} h" if lead is not None else "No forecast lead selected",
                     f"Valid {_instant(valid)}"]
            if source == "model":
                member = owner._model_member_value()
                if member:
                    parts.append(f"Member {member}")
                elif cfg is not None and cfg.kwargs.get("member") is not None:
                    parts.append(f"Member {cfg.kwargs['member']} (default)")
            if cfg is None:
                message, level = "Choose a supported model and region before loading.", "warn"
                correction = "Review model"
                action = self._reveal(owner._model_combo) if source == "model" else (
                    lambda: owner._select_tab("Forecast Model")
                )
            elif run is None:
                message, level = "Choose an initialization for this model.", "warn"
                correction, action = "Choose initialization", self._reveal(getattr(owner, f"_{source}_cycle"))
            elif lead is None:
                message, level = "Choose a forecast lead for this initialization.", "warn"
                correction, action = "Choose lead", self._reveal(getattr(owner, f"_{source}_fxx_combo"))
            else:
                if source == "model":
                    inside = owner._model_point_ok()
                else:
                    from sharpmod.tools import model_extract

                    inside = model_extract.point_in_domain(
                        cfg, owner._panels_lat.value(), owner._panels_lon.value()
                    )
                if not inside:
                    message, level = (
                        f"Point outside {label} coverage."
                        if source == "model" else
                        f"The requested point is outside {label} coverage. "
                        "Choose a point inside the displayed domain or another model."
                    ), "warn"
                    correction, action = "Change point", self._reveal(getattr(owner, f"_{source}_lat"))
        elif source == "era5":
            valid = owner._era5_valid_time()
            parts = ["ERA5 reanalysis", f"Requested point {_point(owner, source)}",
                     f"Analysis / valid {_instant(valid)}"]
            if valid > datetime.now(timezone.utc):
                message, level = "ERA5 cannot supply a future analysis. Choose an earlier time.", "warn"
                correction, action = "Choose earlier time", self._reveal(owner._era5_date)
            else:
                readiness = owner._era5_readiness.text()
                if readiness.startswith(("Missing ERA5", "CDS credentials")):
                    message, level = readiness, "warn"
        elif source == "wrf":
            path = owner._wrf_path()
            parts = [f"WRF · {Path(path).name if path else 'No file selected'}"]
            parts.append(f"Point {_point(owner, source)}")
            if owner._wrf_domain is not None:
                selected = owner._wrf_selected_time()
                if selected is not None:
                    parts.append(f"Valid {_instant(selected)}")
            if owner._wrf_domain is None:
                exists = _local_file_exists(path)
                if owner._wrf_domain_status.text() == "WRF inspection failed.":
                    message = "Inspection failed. Check the file, then inspect it again."
                elif exists:
                    message = "Inspect this WRF file to see its grid and times."
                elif path:
                    message = "WRF file not found. Correct the path or browse for a file."
                else:
                    message = "Choose a WRF file and inspect it to see its grid and times."
                level = "warn"
            else:
                from sharpmod.tools import wrf_extract

                if not wrf_extract.point_in_domain(
                    owner._wrf_domain, owner._wrf_lat.value(), owner._wrf_lon.value()
                ):
                    message, level = "Choose a point inside the inspected WRF grid.", "warn"
        else:
            raise ValueError(f"Unknown picker feedback source: {source}")
        cancel = getattr(owner, f"_{source}_cancel_btn", None)
        active = cancel is not None and not cancel.isHidden()
        if active:
            message, level = ("An earlier request is running; the summary describes the next selection. "
                              "Cancel the active request below if needed."), "info"
            correction, action = "", None
        elif source in {"map", "uwyo"} and owner._worker is not None and owner._worker.isRunning():
            message, level = ("An observed archive request is running. The summary describes "
                              "the next selection; this transfer has no Cancel control yet."), "info"
            correction, action = "", None
        return parts, message, level, correction, action

    def refresh(self):
        parts, message, level, correction, action = self._details()
        self.pane.set_feedback(" · ".join(parts), message, level=level,
                               corrective_label=correction, corrective_action=action)
        for section in self.pane.rail.findChildren(CollapsibleRailSection):
            title = section.title().casefold()
            if self.source == "map" and title == "sounding setup":
                # Follow the visible source, station, runtime hierarchy when
                # the card is collapsed.
                text = " · ".join((
                    self.owner._map_source.currentText(),
                    self.owner._map_selected_id or "No station selected",
                    f"{self.owner._map_when():%d %b %HZ}",
                    self.owner._map_mode_combo.currentText().split(" — ", 1)[0],
                ))
            elif self.source == "panels" and title == "field setup":
                text = (f"{self.owner._panels_count_combo.currentText()} · "
                        f"{self.owner._panels_valid_lbl.text()}")
            elif self.source == "panels" and title == "map location":
                region = self.owner._panels_area_combo.currentText().strip()
                text = (f"{region or 'Current view'} · "
                        f"{_point(self.owner, 'panels')}")
            elif self.source == "era5" and title == "era5 setup":
                region = self.owner._era5_area_combo.currentText().strip()
                text = (f"{region or 'Current view'} · "
                        f"{self.owner._era5_valid_time():%d %b %HZ} · "
                        f"{_point(self.owner, 'era5')}")
                readiness = self.owner._era5_readiness.text()
                if readiness.startswith("Missing ERA5"):
                    text += " · ERA5 packages missing"
                elif readiness.startswith("CDS credentials"):
                    text += " · CDS setup needed"
            elif self.source == "wrf" and title == "wrf setup":
                path = self.owner._wrf_path()
                text = Path(path).name if path else "No file selected"
                if self.owner._wrf_domain is None:
                    text += " · Inspect file" if path else " · Choose file"
                else:
                    selected = self.owner._wrf_selected_time()
                    if selected is not None:
                        text += f" · {_instant(selected)}"
                    text += f" · {_point(self.owner, 'wrf')}"
                    if self.owner._wrf_point_status.text() == "Outside WRF grid":
                        text += " · Point outside grid"
            elif "point" in title:
                text = _point(self.owner, self.source)
            elif "region" in title:
                combos = section.content.findChildren(QComboBox)
                text = combos[0].currentText() if combos else "Expand to review region"
            elif "overlay" in title:
                enabled = [box.text().replace("&", "")
                           for box in section.content.findChildren(QCheckBox) if box.isChecked()]
                text = ", ".join(enabled) if enabled else "No optional overlays enabled"
            elif "map layer" in title:
                overview = getattr(self.owner, f"_{self.source}_layer_overview", None)
                text = overview.text() if overview is not None else "Expand to review layers"
            elif "run" in title or "time" in title:
                text = " · ".join(part for part in parts if part.startswith(
                    ("Initialization ", "Lead ", "Valid ", "Requested observation ", "Analysis / valid ")
                ))
            elif "station" in title:
                text = parts[1]
            elif "member" in title:
                text = next((part for part in parts if part.startswith("Member ")),
                            "Member not applicable")
            elif "model" in title or "source" in title:
                text = parts[0]
            else:
                values = [combo.currentText() for combo in section.content.findChildren(QComboBox)]
                values += [field.text() for field in section.content.findChildren(QLineEdit)]
                text = " · ".join(value for value in values if value) or "Expand to review settings"
            section.set_summary(text)


def install_selection_feedback(owner, pane, source, primary):
    feedback = PickerSelectionFeedback(owner, pane, source, primary)
    setattr(owner, f"_{source}_selection_feedback", feedback)
    return feedback


def refresh_selection_feedback(owner, source):
    feedback = getattr(owner, f"_{source}_selection_feedback", None)
    if feedback is not None:
        feedback.refresh()


class FileSelectionFeedback(QObject):
    """Truthful local-file context before decoding, with the existing Browse action."""

    def __init__(self, field, summary, guidance, primary, parent):
        super().__init__(parent)
        self.field, self.summary, self.guidance, self.primary = field, summary, guidance, primary
        field.textChanged.connect(self.refresh)
        self.refresh()

    def refresh(self, *_args):
        path = self.field.text().strip().strip('"')
        exists = _local_file_exists(path)
        text = (f"Local sounding file — {Path(path).name if path else 'No file selected'} · "
                "Location / valid time will be read from the file after opening")
        self.summary.setText(text)
        self.summary.setAccessibleDescription(text)
        self.summary.setToolTip(path or text)
        message = ("" if exists else "Enter an existing sounding file path or choose Browse." if not path
                   else "That file was not found. Correct its path or choose Browse.")
        set_status_label(self.guidance, message, level="warn")
        self.guidance.setVisible(bool(message))
        self.primary.setEnabled(exists)
