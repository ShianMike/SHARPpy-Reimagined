"""Valid-time-aligned model/run comparison acquisition for the picker.

The request planner is intentionally Qt-free and deterministic.  The dialog and
worker in this module are imported only when the Forecast Model tab is built so
the comparison feature does not lengthen normal picker startup.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from qtpy.QtCore import QObject, Qt, QThread, Signal
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)


MAX_COMPARISON_SOUNDINGS = 8
ENSEMBLE_MEMBERS = {
    "gefs": ("c00", *(f"p{index:02d}" for index in range(1, 31))),
    "cfs": (1, 2, 3, 4),
}


@dataclass(frozen=True)
class ComparisonRequestSpec:
    """One model cycle selected for a shared forecast valid time."""

    request_id: str
    model: str
    label: str
    run_time: datetime
    fxx: int
    member: str | int | None = None

    @property
    def valid_time(self) -> datetime:
        return self.run_time + timedelta(hours=self.fxx)


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("comparison valid time must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _request_id(
    model: str,
    run_time: datetime,
    fxx: int,
    member: str | int | None = None,
) -> str:
    safe_model = "".join(
        character if character.isalnum() else "-" for character in model
    ).strip("-")
    suffix = ""
    if member is not None:
        safe_member = "".join(
            character if character.isalnum() else "-" for character in str(member)
        ).strip("-")
        suffix = f"-{safe_member}"
    return f"{safe_model}-{run_time:%Y%m%d%H}-f{int(fxx):03d}{suffix}"


def _candidate_specs(config, valid_time: datetime, *, before=None):
    """Yield newest compatible cycles for ``valid_time``."""
    from sharpmod.tools import model_extract

    valid_time = _as_utc(valid_time)
    before = _as_utc(before) if before is not None else None
    # The longest products in the registry reach well past a week.  Derive the
    # search horizon from their actual forecast-hour table instead of using a
    # product-specific assumption.
    all_hours = tuple(model_extract.forecast_hours(config))
    max_hour = max(all_hours or (0,))
    day_count = max(2, int(max_hour // 24) + 2)
    candidates = []
    midnight = valid_time.replace(hour=0, minute=0, second=0, microsecond=0)
    for day_offset in range(day_count):
        day = midnight - timedelta(days=day_offset)
        for cycle in config.cycles:
            run_time = day.replace(hour=int(cycle))
            if run_time > valid_time or (before is not None and run_time >= before):
                continue
            delta_hours = (valid_time - run_time).total_seconds() / 3600.0
            if not delta_hours.is_integer():
                continue
            fxx = int(delta_hours)
            if fxx in model_extract.forecast_hours(config, cycle_hour=run_time.hour):
                candidates.append((run_time, fxx))
    for run_time, fxx in sorted(candidates, reverse=True):
        yield ComparisonRequestSpec(
            _request_id(config.key, run_time, fxx),
            str(config.key),
            str(config.label),
            run_time,
            fxx,
        )


def plan_model_comparison(
    model_keys,
    valid_time: datetime,
    *,
    anchor_model: str | None = None,
    anchor_run: datetime | None = None,
    anchor_fxx: int | None = None,
) -> tuple[ComparisonRequestSpec, ...]:
    """Plan newest compatible cycles for two or more distinct models."""
    from sharpmod.tools import model_extract

    valid_time = _as_utc(valid_time)
    keys = tuple(dict.fromkeys(str(key) for key in model_keys if str(key)))
    if len(keys) < 2:
        raise ValueError("choose at least two forecast models")
    if len(keys) > MAX_COMPARISON_SOUNDINGS:
        raise ValueError(f"choose no more than {MAX_COMPARISON_SOUNDINGS} soundings")

    anchor_run_utc = _as_utc(anchor_run) if anchor_run is not None else None
    specs = []
    missing = []
    for key in keys:
        config = model_extract.get_config(key)
        spec = None
        if (
            key == anchor_model
            and anchor_run_utc is not None
            and anchor_fxx is not None
        ):
            candidate_fxx = int(anchor_fxx)
            if anchor_run_utc + timedelta(hours=candidate_fxx) == valid_time:
                available = model_extract.forecast_hours(
                    config, cycle_hour=anchor_run_utc.hour
                )
                if candidate_fxx in available:
                    spec = ComparisonRequestSpec(
                        _request_id(key, anchor_run_utc, candidate_fxx),
                        key,
                        str(config.label),
                        anchor_run_utc,
                        candidate_fxx,
                    )
        if spec is None:
            spec = next(_candidate_specs(config, valid_time), None)
        if spec is None:
            missing.append(str(config.label))
        else:
            specs.append(spec)
    if missing:
        raise ValueError(
            "no cycle reaches the selected valid time for: " + ", ".join(missing)
        )
    return tuple(specs)


def plan_run_comparison(
    model_key: str,
    valid_time: datetime,
    *,
    anchor_run: datetime,
    anchor_fxx: int,
    count: int = 3,
) -> tuple[ComparisonRequestSpec, ...]:
    """Plan successive cycles of one model at the same valid time."""
    from sharpmod.tools import model_extract

    count = max(2, min(MAX_COMPARISON_SOUNDINGS, int(count)))
    valid_time = _as_utc(valid_time)
    anchor_run = _as_utc(anchor_run)
    config = model_extract.get_config(model_key)
    anchor_fxx = int(anchor_fxx)
    if anchor_run + timedelta(hours=anchor_fxx) != valid_time:
        raise ValueError("the selected run and forecast hour do not match")
    if anchor_fxx not in model_extract.forecast_hours(
        config, cycle_hour=anchor_run.hour
    ):
        raise ValueError("the selected forecast hour is unavailable for this run")
    anchor = ComparisonRequestSpec(
        _request_id(config.key, anchor_run, anchor_fxx),
        str(config.key),
        str(config.label),
        anchor_run,
        anchor_fxx,
    )
    older = tuple(_candidate_specs(config, valid_time, before=anchor_run))
    specs = (anchor, *older[: count - 1])
    if len(specs) < 2:
        raise ValueError("no earlier compatible run reaches this valid time")
    return tuple(specs)


def plan_ensemble(
    model_key: str,
    *,
    run_time: datetime,
    fxx: int,
    count: int | None = None,
) -> tuple[ComparisonRequestSpec, ...]:
    """Plan a bounded control-plus-perturbation ensemble request."""
    from sharpmod.tools import model_extract

    config = model_extract.get_config(model_key)
    members = ENSEMBLE_MEMBERS.get(config.key)
    if not members:
        raise ValueError(
            f"{config.label} is deterministic; choose GEFS or CFS for an ensemble"
        )
    if count is None:
        count = len(members)
    count = max(2, min(len(members), int(count)))
    run_time = _as_utc(run_time)
    fxx = int(fxx)
    if fxx not in model_extract.forecast_hours(config, cycle_hour=run_time.hour):
        raise ValueError("the selected forecast hour is unavailable for this run")
    return tuple(
        ComparisonRequestSpec(
            _request_id(config.key, run_time, fxx, member),
            config.key,
            str(config.label),
            run_time,
            fxx,
            member,
        )
        for member in members[:count]
    )


class ModelComparisonDialog(QDialog):
    """Choose either several models or several runs at one valid time."""

    def __init__(
        self,
        *,
        current_model,
        run_time,
        fxx,
        lat,
        lon,
        parent=None,
    ):
        super().__init__(parent)
        from sharpmod.tools import model_extract

        self._current_model = str(current_model)
        self._run_time = _as_utc(run_time)
        self._fxx = int(fxx)
        self._valid_time = self._run_time + timedelta(hours=self._fxx)
        self.setWindowTitle("Compare Models / Runs")
        self.resize(510, 470)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Fetch aligned soundings for one point and valid time into a "
            "single comparison workspace. Downloads are bounded and cached."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.valid_label = QLabel(
            f"{float(lat):.4f}, {float(lon):.4f}  •  "
            f"valid {self._valid_time:%Y-%m-%d %H:%MZ}"
        )
        layout.addWidget(self.valid_label)

        form = QFormLayout()
        self.mode = QComboBox(self)
        self.mode.addItem("Different models", "models")
        self.mode.addItem("Successive runs of selected model", "runs")
        self.mode.addItem("Ensemble members", "ensemble")
        form.addRow("Compare", self.mode)
        self.run_count = QSpinBox(self)
        self.run_count.setRange(2, MAX_COMPARISON_SOUNDINGS)
        self.run_count.setValue(3)
        form.addRow("Sounding count", self.run_count)
        layout.addLayout(form)

        self.models = QListWidget(self)
        available = []
        for config in model_extract.available_models():
            try:
                inside = model_extract.point_in_domain(config, lat, lon)
            except Exception:
                inside = False
            if inside:
                available.append(config)
        preferred = (
            self._current_model,
            "hrrr",
            "rap",
            "nam-3km-conus",
            "nam",
            "gfs",
        )
        defaults = []
        for key in preferred:
            if key not in defaults and any(cfg.key == key for cfg in available):
                defaults.append(key)
            if len(defaults) == 3:
                break
        for config in available:
            item = QListWidgetItem(f"{config.label}  —  {config.domain}")
            item.setData(Qt.UserRole, config.key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if config.key in defaults else Qt.Unchecked)
            self.models.addItem(item)
        layout.addWidget(self.models, 1)
        self.preview = QLabel(self)
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.accepted.connect(self._accept_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.run_count.valueChanged.connect(self._refresh_preview)
        self.models.itemChanged.connect(self._refresh_preview)
        self._mode_changed()

    def _checked_models(self) -> tuple[str, ...]:
        return tuple(
            str(self.models.item(index).data(Qt.UserRole))
            for index in range(self.models.count())
            if self.models.item(index).checkState() == Qt.Checked
        )

    def specs(self) -> tuple[ComparisonRequestSpec, ...]:
        if self.mode.currentData() == "runs":
            return plan_run_comparison(
                self._current_model,
                self._valid_time,
                anchor_run=self._run_time,
                anchor_fxx=self._fxx,
                count=self.run_count.value(),
            )
        if self.mode.currentData() == "ensemble":
            return plan_ensemble(
                self._current_model,
                run_time=self._run_time,
                fxx=self._fxx,
                count=self.run_count.value(),
            )
        return plan_model_comparison(
            self._checked_models(),
            self._valid_time,
            anchor_model=self._current_model,
            anchor_run=self._run_time,
            anchor_fxx=self._fxx,
        )

    def workspace_kind(self) -> str:
        return "ensemble" if self.mode.currentData() == "ensemble" else "compare"

    def _mode_changed(self, *_args) -> None:
        kind = self.mode.currentData()
        if kind == "ensemble":
            members = ENSEMBLE_MEMBERS.get(self._current_model, ())
            self.run_count.setRange(2, max(2, len(members)))
            self.run_count.setValue(max(2, len(members)))
        else:
            self.run_count.setRange(2, MAX_COMPARISON_SOUNDINGS)
            if self.run_count.value() > MAX_COMPARISON_SOUNDINGS:
                self.run_count.setValue(MAX_COMPARISON_SOUNDINGS)
        self._refresh_preview()

    def _refresh_preview(self, *_args) -> None:
        kind = self.mode.currentData()
        self.models.setEnabled(kind == "models")
        self.run_count.setEnabled(kind in {"runs", "ensemble"})
        try:
            specs = self.specs()
        except (KeyError, ValueError) as exc:
            self.preview.setText(str(exc))
            return
        self.preview.setText(
            " • ".join(
                f"{spec.label} {spec.run_time:%d/%HZ} F{spec.fxx:03d}" for spec in specs
            )
        )

    def _accept_valid(self) -> None:
        try:
            self.specs()
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, "Model comparison", str(exc))
            return
        self.accept()


class ModelComparisonWorker(QThread):
    """Run an aligned batch in a killable child process off the UI thread."""

    progress = Signal(str, int, int)
    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        specs,
        lat,
        lon,
        output_dir,
        *,
        loc=None,
        disk_cache=None,
        parent=None,
    ):
        super().__init__(parent)
        self.specs = tuple(specs)
        self.lat = float(lat)
        self.lon = float(lon)
        self.output_dir = os.fspath(output_dir)
        self.loc = str(loc) if loc else None
        self.disk_cache = disk_cache
        self._runner = None
        self._completed = 0

    def requestInterruption(self):  # noqa: N802 - Qt API override
        super().requestInterruption()
        if self._runner is not None:
            self._runner.cancel()

    def run(self):
        from sharpmod.batch_extract import BatchRequest
        from sharpmod.gui_batch_process import (
            IsolatedBatchCancelled,
            IsolatedBatchRunner,
        )

        requests = [
            BatchRequest(
                id=spec.request_id,
                model=spec.model,
                lat=self.lat,
                lon=self.lon,
                run_time=spec.run_time,
                fxx=spec.fxx,
                output=f"{spec.request_id}.npz",
                loc=self.loc,
                member=spec.member,
            )
            for spec in self.specs
        ]

        def on_progress(event):
            kind = str(event.get("event", "working"))
            if kind == "completed":
                self._completed += 1
            self.progress.emit(kind, self._completed, len(requests))

        try:
            self._runner = IsolatedBatchRunner()
            result = self._runner.run(
                requests,
                output_dir=self.output_dir,
                max_workers=min(3, len(requests)),
                progress_callback=on_progress,
                disk_cache=self.disk_cache,
            )
        except IsolatedBatchCancelled:
            return
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(f"Model comparison failed: {exc}")
            return
        finally:
            self._runner = None
        self.result_ready.emit(result)


class _ModelComparisonCoordinator(QObject):
    """Own dialog, worker, progress, and one combined viewer lifecycle."""

    def __init__(self, picker):
        super().__init__(picker)
        self.picker = picker
        self.worker = None
        self.output_dir = None
        self.kind = "compare"

    def open(self) -> None:
        picker = self.picker
        if self.worker is not None:
            QMessageBox.information(
                picker, "Model comparison", "A comparison is already running."
            )
            return
        other_workers = (
            getattr(picker, "_model_worker", None),
            getattr(picker, "_model_timeline_worker", None),
            getattr(picker, "_box_extract_worker", None),
            getattr(picker, "_box_analysis_worker", None),
            getattr(picker, "_box_mean_worker", None),
        )
        if any(worker is not None for worker in other_workers):
            QMessageBox.information(
                picker,
                "Model comparison",
                "Another model-data operation is already running.",
            )
            return
        config = picker._model_config()
        if config is None:
            QMessageBox.warning(
                picker, "Model comparison", "Choose a forecast model first."
            )
            return
        lat = float(picker._model_lat.value())
        lon = float(picker._model_lon.value())
        if not picker._model_point_ok():
            QMessageBox.warning(
                picker,
                "Model comparison",
                f"{config.label} does not cover {lat:.4f}, {lon:.4f}.",
            )
            return
        dialog = ModelComparisonDialog(
            current_model=config.key,
            run_time=picker._model_run_time(),
            fxx=picker._model_selected_fxx(),
            lat=lat,
            lon=lon,
            parent=picker,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        specs = dialog.specs()
        self.kind = dialog.workspace_kind()
        from sharpmod.tools import model_extract

        if any(model_extract.requires_grib_runtime(spec.model) for spec in specs):
            try:
                model_extract.require_runtime_dependencies()
            except model_extract.RetrievalError as exc:
                QMessageBox.critical(
                    picker,
                    "Model comparison",
                    f"Forecast model support is unavailable:\n{exc}",
                )
                return

        picker._ensure_model_cache()
        picker._cancel_model_prefetch(wait=True)
        picker._remember_point(lat, lon, picker._model_loc.text().strip() or None)
        self.output_dir = tempfile.mkdtemp(prefix="model_comparison_")
        self.worker = ModelComparisonWorker(
            specs,
            lat,
            lon,
            self.output_dir,
            loc=picker._model_loc.text().strip() or None,
            disk_cache=picker._model_disk_cache,
            parent=picker,
        )
        picker._model_compare_worker = self.worker
        self.worker.progress.connect(self._progress)
        self.worker.result_ready.connect(self._result)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        picker._set_model_busy(True)
        picker._model_progress_timer.stop()
        picker._model_progress.setRange(0, len(specs))
        picker._model_progress.setValue(0)
        picker._model_progress.setFormat(f"0 / {len(specs)} soundings")
        picker._model_progress_detail.setText(
            "Fetching aligned model cycles in parallel…"
        )
        picker.statusBar().showMessage(
            f"Fetching {len(specs)} aligned comparison soundings…"
        )
        self.worker.start()

    def cancel(self) -> None:
        if self.worker is None:
            return
        self.worker.requestInterruption()
        self.picker.statusBar().showMessage("Cancelling model comparison…")

    def _progress(self, stage: str, completed: int, total: int) -> None:
        self.picker._model_progress.setRange(0, max(1, int(total)))
        self.picker._model_progress.setValue(max(0, int(completed)))
        self.picker._model_progress.setFormat(
            f"{int(completed)} / {int(total)} soundings"
        )
        if stage not in {"running", "completed"}:
            self.picker._model_progress_detail.setText(
                stage.replace("_", " ").capitalize()
            )

    def _result(self, result) -> None:
        completed = [item for item in result.items if item.status == "completed"]
        if len(completed) < 2:
            self._failed(
                "Fewer than two aligned soundings were available; "
                "no comparison workspace was opened."
            )
            return
        try:
            from sharpmod.gui_picker import (
                compose_interactive,
                _fill_profile_metadata,
                _locator_spec_for,
                _overlay_product_for,
                _render,
                _retain_model_data_until_close,
                _start_locator_overlay_fetch,
            )

            decoded = [_render().decode(str(item.output_path)) for item in completed]
            if self.kind == "ensemble":
                member_by_id = {
                    spec.request_id: spec.member for spec in self.worker.specs
                }
                from sharpmod.profile_timeline import combine_ensemble_collections

                member_names = [str(member_by_id[item.id]) for item in completed]
                ensemble = combine_ensemble_collections(
                    [collection for collection, _station_id in decoded],
                    member_names=member_names,
                )
                first_id = decoded[0][1]
                decoded = [(ensemble, first_id)]
            first_collection, first_id = decoded[0]
            self.picker._prune_closed_viewers()
            win = compose_interactive(
                self.picker._config(),
                first_collection,
                self.picker,
                stn_id=first_id,
            )
            win.setWindowTitle(
                (
                    f"SHARPpy Reimagined — {len(completed)}-Member Ensemble"
                    if self.kind == "ensemble"
                    else f"SHARPpy Reimagined — {len(decoded)}-Sounding Comparison"
                )
            )
            self.picker._viewers.append(win)
            for collection, station_id in decoded[1:]:
                _fill_profile_metadata(collection, station_id)
                win.addProfileCollection(collection, focus=False, check_integrity=False)
                _start_locator_overlay_fetch(
                    win,
                    collection,
                    product=_overlay_product_for(self.picker),
                    controller=self.picker,
                    spec=_locator_spec_for(self.picker),
                )
            win.spc_widget.updateProfs()
            workspace = getattr(win, "_sharpmod_analysis_workspace", None)
            show_workspace = getattr(
                workspace,
                "show_ensemble" if self.kind == "ensemble" else "show_compare",
                None,
            )
            if callable(show_workspace):
                show_workspace()
            _retain_model_data_until_close(
                win, str(completed[0].output_path), self.output_dir
            )
            self.output_dir = None
            message = (
                f"Opened {len(completed)}-member ensemble summary"
                if self.kind == "ensemble"
                else f"Opened {len(decoded)} aligned comparison soundings"
            )
            if result.failed or result.cancelled:
                message += (
                    f" ({int(result.failed) + int(result.cancelled)} unavailable)"
                )
            self.picker.statusBar().showMessage(message, 7000)
        except Exception as exc:  # noqa: BLE001 - decode/viewer boundary
            self._failed(f"Comparison fetched, but could not be displayed: {exc}")

    def _failed(self, message: str) -> None:
        self.picker.statusBar().showMessage("Model comparison failed")
        QMessageBox.critical(self.picker, "Model comparison", str(message))

    def _finished(self) -> None:
        worker = self.worker
        self.worker = None
        if getattr(self.picker, "_model_compare_worker", None) is worker:
            self.picker._model_compare_worker = None
        self.picker._set_model_busy(False)
        if worker is not None:
            worker.deleteLater()
        if self.output_dir:
            from sharpmod.gui_workers import _cleanup_point_data

            _cleanup_point_data(None, self.output_dir)
            self.output_dir = None


def install_model_compare_control(picker, layout) -> QPushButton:
    """Add the picker action and return it for tests/customisation."""
    existing = getattr(picker, "_model_compare_btn", None)
    if existing is not None:
        return existing
    coordinator = _ModelComparisonCoordinator(picker)
    button = QPushButton("Workspace…", picker)
    button.setToolTip(
        "Fetch two or more models or cycles at the same valid time into one "
        "analysis workspace"
    )
    button.clicked.connect(coordinator.open)
    # Keep comparison beside Timeline and before the box/cancel controls.  The
    # installer runs after those widgets exist; the picker's existing Cancel
    # handler delegates to this coordinator while its worker is active.
    layout.insertWidget(1, button, 1)
    picker._model_compare_btn = button
    picker._model_compare_coordinator = coordinator
    picker._model_compare_worker = None
    return button


__all__ = [
    "ComparisonRequestSpec",
    "ENSEMBLE_MEMBERS",
    "MAX_COMPARISON_SOUNDINGS",
    "ModelComparisonDialog",
    "ModelComparisonWorker",
    "install_model_compare_control",
    "plan_model_comparison",
    "plan_ensemble",
    "plan_run_comparison",
]
