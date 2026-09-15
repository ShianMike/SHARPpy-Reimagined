"""Portable historical-case builder and exact-time replay UI."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlparse

from qtpy.QtCore import QThread, QTimer, Qt, Signal
from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from sharpmod.case_replay import (
    ALLOWED_KINDS,
    PROVIDER_CAPABILITIES,
    CaseAsset,
    CaseManifest,
    ReplayClock,
    create_case_package,
    download_case_assets,
    load_case_package,
)
from sharpmod.export_paths import ExportDirectoryError, export_file_path
from sharpmod.scenarios import write_scenario_book
from sharpmod.sessions import SESSION_VERSION, build_session, restore_collection, write_session
from sharpmod.surface_observations import write_surface_observations
from sharpmod.theme import (
    OBJ_ERROR_TEXT,
    OBJ_GHOST,
    OBJ_HINT,
    OBJ_STATUS,
    OBJ_WARNING_TEXT,
    SPACE,
)
from sharpmod.verification import write_verification_json
from sharpmod.wind_profiles import write_wind_profiles


def _utc(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _time_text(value):
    value = _utc(value)
    return value.strftime("%Y-%m-%d %H:%M:%SZ") if value else "Unknown"


def _parse_time(value, *, required=False):
    text = str(value or "").strip()
    if not text:
        if required:
            raise ValueError("an event UTC time is required")
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid UTC timestamp: {text}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _jsonable(value):
    if isinstance(value, datetime):
        return _utc(value).isoformat().replace("+00:00", "Z")
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _safe_identifier(prefix, counter):
    prefix = "".join(character if character.isalnum() else "-" for character in prefix)
    return f"{prefix.strip('-') or 'asset'}-{counter:03d}"


class _BuildWorker(QThread):
    completed = Signal(int, str, object, bool)
    failed = Signal(int, str)
    progress = Signal(int, int, str)

    def __init__(self, token, destination, manifest, files, temporary_root, parent=None):
        super().__init__(parent)
        self.token = int(token)
        self.destination = str(destination)
        self.manifest = manifest
        self.files = dict(files)
        self.temporary_root = str(temporary_root)

    def run(self):
        try:
            downloaded = download_case_assets(
                self.manifest,
                Path(self.temporary_root) / "downloads",
                cancel=self.isInterruptionRequested,
                progress=lambda done, total, asset: self.progress.emit(
                    done, total, f"{asset.asset_id}: {asset.status}"
                ),
            )
            files = {**self.files, **dict(downloaded.files)}
            saved = create_case_package(
                self.destination, downloaded.manifest, files
            )
        except Exception as exc:  # noqa: BLE001 - package boundary
            self.failed.emit(self.token, str(exc))
            return
        finally:
            shutil.rmtree(self.temporary_root, ignore_errors=True)
        self.completed.emit(
            self.token, str(saved), downloaded.manifest, downloaded.cancelled
        )


class _LoadWorker(QThread):
    completed = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, token, path, parent=None):
        super().__init__(parent)
        self.token = int(token)
        self.path = str(path)

    def run(self):
        try:
            loaded = load_case_package(self.path)
        except Exception as exc:  # noqa: BLE001 - package boundary
            self.failed.emit(self.token, str(exc))
            return
        self.completed.emit(self.token, loaded)


class CaseReplayWorkspace(QWidget):
    """Build data-only case packages and replay their exact archived times."""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.setObjectName("analysisCaseReplayWorkspace")
        self.host = host
        self._external = []
        self._asset_counter = 0
        self._loaded = None
        self._case_collections = ()
        self._clock = ReplayClock()
        self._worker = None
        self._token = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._build_ui()

    @property
    def loaded_case(self):
        return self._loaded

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE["sm"])
        intro = QLabel(
            "Portable cases contain data only, verify every packaged SHA-256 digest, "
            "and never execute or extract embedded members. Live radar is not used as "
            "a historical substitute."
        )
        intro.setObjectName(OBJ_HINT)
        intro.setWordWrap(True)
        outer.addWidget(intro)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("caseReplayModes")
        self._build_package_tab()
        self._build_replay_tab()
        outer.addWidget(self.tabs, 1)
        self.status = QLabel("Build a package from the current session or open an existing case.")
        self.status.setObjectName(OBJ_STATUS)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

    def _build_package_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        layout.setSpacing(SPACE["sm"])
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Case name"))
        self.case_name = QLineEdit("Historical sounding case")
        self.case_name.setObjectName("caseName")
        name_row.addWidget(self.case_name, 1)
        self.include_current = QCheckBox("Include current soundings, analyses, notes, and context")
        self.include_current.setChecked(True)
        name_row.addWidget(self.include_current)
        layout.addLayout(name_row)

        editor = QGridLayout()
        self.kind = QComboBox()
        for item in (
            "radar-level3",
            "radar-image",
            "outlook",
            "storm-reports",
            "satellite",
            "surface-observations",
            "wind-profile",
            "sounding",
            "notes",
        ):
            self.kind.addItem(item.replace("-", " ").title(), item)
        self.event_time = QLineEdit()
        self.event_time.setPlaceholderText("event UTC, e.g. 2024-05-20T18:00Z")
        self.available_time = QLineEdit()
        self.available_time.setPlaceholderText("available UTC; blank means unknown")
        self.remote_url = QLineEdit()
        self.remote_url.setPlaceholderText("documented HTTPS archive URL")
        editor.addWidget(QLabel("Archived product"), 0, 0)
        editor.addWidget(self.kind, 1, 0)
        editor.addWidget(QLabel("Event / valid time"), 0, 1)
        editor.addWidget(self.event_time, 1, 1)
        editor.addWidget(QLabel("Forecaster availability"), 0, 2)
        editor.addWidget(self.available_time, 1, 2)
        editor.addWidget(QLabel("Remote archive URL"), 0, 3)
        editor.addWidget(self.remote_url, 1, 3)
        self.add_local = QPushButton("Add local archived file…")
        self.add_remote = QPushButton("Add remote archive URL")
        self.remove_asset = QPushButton("Remove selected")
        for button in (self.add_local, self.add_remote, self.remove_asset):
            button.setObjectName(OBJ_GHOST)
        editor.addWidget(self.add_local, 2, 1)
        editor.addWidget(self.add_remote, 2, 2)
        editor.addWidget(self.remove_asset, 2, 3)
        layout.addLayout(editor)
        capability = QLabel(
            "Confirmed adapters: "
            + " · ".join(f"{key}: {value}" for key, value in PROVIDER_CAPABILITIES.items())
        )
        capability.setObjectName(OBJ_HINT)
        capability.setWordWrap(True)
        layout.addWidget(capability)
        self.planned = self._table("casePlannedAssets")
        self.planned.setColumnCount(7)
        self.planned.setHorizontalHeaderLabels(
            ("ID", "Kind", "Event", "Available", "Source", "Status", "Note")
        )
        self.planned.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self.planned, 1)
        actions = QHBoxLayout()
        self.build_button = QPushButton("Build portable case…")
        self.build_button.setObjectName("caseBuild")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName(OBJ_GHOST)
        self.cancel_button.setEnabled(False)
        self.open_button = QPushButton("Open case…")
        self.open_button.setObjectName(OBJ_GHOST)
        actions.addWidget(self.build_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        actions.addWidget(self.open_button)
        layout.addLayout(actions)
        self.tabs.addTab(page, "Build package")

        self.add_local.clicked.connect(self._add_local)
        self.add_remote.clicked.connect(self._add_remote)
        self.remove_asset.clicked.connect(self._remove_selected)
        self.build_button.clicked.connect(self._build_package)
        self.cancel_button.clicked.connect(self._cancel)
        self.open_button.clicked.connect(self._open_package)

    def _build_replay_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        layout.setSpacing(SPACE["sm"])
        controls = QHBoxLayout()
        self.previous = self._button("◀", "Previous exact archived time")
        self.play = self._button("Play", "Play exact archived times")
        self.next = self._button("▶", "Next exact archived time")
        self.replay_time = QComboBox()
        self.replay_time.setObjectName("caseReplayTime")
        self.replay_time.setAccessibleName("Exact archived replay time")
        self.speed = QSpinBox()
        self.speed.setRange(200, 10000)
        self.speed.setValue(1000)
        self.speed.setSingleStep(200)
        self.speed.setSuffix(" ms")
        self.training = QCheckBox("Training mode: reveal only by known availability")
        for widget in (self.previous, self.play, self.next):
            controls.addWidget(widget)
        controls.addWidget(self.replay_time, 1)
        controls.addWidget(QLabel("Step speed"))
        controls.addWidget(self.speed)
        controls.addWidget(self.training)
        layout.addLayout(controls)
        self.training_limitations = QLabel()
        self.training_limitations.setObjectName(OBJ_WARNING_TEXT)
        self.training_limitations.setWordWrap(True)
        layout.addWidget(self.training_limitations)
        actions = QHBoxLayout()
        self.open_soundings = QPushButton("Open packaged soundings in this viewer")
        self.open_soundings.setObjectName("caseOpenSoundings")
        actions.addWidget(self.open_soundings)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.assets = self._table("caseReplayAssets")
        self.assets.setColumnCount(7)
        self.assets.setHorizontalHeaderLabels(
            ("Visible", "ID", "Kind", "Event", "Available", "Status", "Note")
        )
        self.assets.setSelectionMode(QAbstractItemView.SingleSelection)
        self.preview = QLabel("Open a case package to inspect its verified assets.")
        self.preview.setObjectName("caseAssetPreview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setTextFormat(Qt.PlainText)
        self.preview.setWordWrap(True)
        self.preview.setMinimumHeight(220)
        split = QSplitter(Qt.Vertical)
        split.setObjectName("caseReplaySplitter")
        split.setChildrenCollapsible(False)
        split.addWidget(self.assets)
        split.addWidget(self.preview)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        layout.addWidget(split, 1)
        self.replay_splitter = split
        self.tabs.addTab(page, "Replay")

        self.previous.clicked.connect(lambda: self._step(-1))
        self.next.clicked.connect(lambda: self._step(1))
        self.play.clicked.connect(self._toggle_play)
        self.replay_time.currentIndexChanged.connect(self._select_time)
        self.speed.valueChanged.connect(self._update_speed)
        self.training.toggled.connect(self._render_replay)
        self.assets.itemSelectionChanged.connect(self._preview_selected)
        self.open_soundings.clicked.connect(self._open_soundings)
        self._update_replay_controls()

    @staticmethod
    def _button(text, tooltip):
        button = QPushButton(text)
        button.setObjectName(OBJ_GHOST)
        button.setToolTip(tooltip)
        return button

    @staticmethod
    def _table(name):
        table = QTableWidget()
        table.setObjectName(name)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def refresh(self):
        current = self._focused_valid_time()
        if current is not None and not self.event_time.text().strip():
            self.event_time.setText(current.isoformat().replace("+00:00", "Z"))

    def _focused_valid_time(self):
        collections = self.host._collections()
        if not collections:
            return None
        collection = collections[min(self.host._focused_index(), len(collections) - 1)]
        dates = tuple(getattr(collection, "_dates", ()) or ())
        try:
            return _utc(dates[int(getattr(collection, "_prof_idx", 0) or 0)])
        except (IndexError, TypeError, ValueError):
            return None

    def _new_asset(self, *, source_url, path=None, status="planned"):
        kind = str(self.kind.currentData())
        event = _parse_time(self.event_time.text(), required=True)
        available = _parse_time(self.available_time.text())
        self._asset_counter += 1
        asset = CaseAsset(
            _safe_identifier(kind, self._asset_counter),
            kind,
            event,
            available,
            str(source_url),
            status=status,
            note=(
                "availability time unknown; excluded from faithful training reveal"
                if available is None
                else ""
            ),
        )
        self._external.append((asset, Path(path) if path else None))
        self._render_planned()

    def _add_local(self):
        try:
            suggested = export_file_path("case-assets")
        except ExportDirectoryError as exc:
            self._set_status(str(exc), "error")
            return
        path, _filter = QFileDialog.getOpenFileName(
            self, "Add archived case asset", str(Path(suggested).parent), "All files (*)"
        )
        if not path:
            return
        try:
            self._new_asset(
                source_url=Path(path).resolve().as_uri(), path=path, status="complete"
            )
        except Exception as exc:  # noqa: BLE001 - form boundary
            self._set_status(f"Archived file was not added: {exc}", "error")
        else:
            self._set_status(f"Added local archived data: {path}")

    def _add_remote(self):
        url = self.remote_url.text().strip()
        try:
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("remote archive assets require a documented HTTPS URL")
            self._new_asset(source_url=url)
        except Exception as exc:  # noqa: BLE001 - form boundary
            self._set_status(f"Remote asset was not added: {exc}", "error")
        else:
            self.remote_url.clear()
            self._set_status("Remote archive asset queued for bounded download.")

    def _remove_selected(self):
        row = self.planned.currentRow()
        if 0 <= row < len(self._external):
            self._external.pop(row)
            self._render_planned()

    def _render_planned(self):
        self.planned.setRowCount(len(self._external))
        for row, (asset, path) in enumerate(self._external):
            values = (
                asset.asset_id,
                asset.kind,
                _time_text(asset.event_time),
                _time_text(asset.available_time),
                str(path or asset.source_url),
                asset.status,
                asset.note,
            )
            for column, value in enumerate(values):
                self.planned.setItem(row, column, QTableWidgetItem(value))

    def _environment_controller(self):
        win = self.host._window()
        try:
            controller = getattr(win, "_sharpmod_controller", None) or win.parent()
        except (AttributeError, RuntimeError):
            return None
        getter = getattr(controller, "active_environment_context", None)
        return getter() if callable(getter) else None

    @staticmethod
    def _write_bytes(path, payload):
        Path(path).write_bytes(bytes(payload))
        return Path(path)

    def _append_asset(self, assets, files, asset, path):
        existing = {item.asset_id for item in assets}
        if asset.asset_id in existing:
            base = asset.asset_id
            counter = 2
            while f"{base}-{counter}" in existing:
                counter += 1
            asset = CaseAsset(
                f"{base}-{counter}",
                asset.kind,
                asset.event_time,
                asset.available_time,
                asset.source_url,
                status=asset.status,
                note=asset.note,
            )
        assets.append(asset)
        if path is not None:
            files[asset.asset_id] = Path(path)

    def _prepare_current_assets(self, root):
        assets = []
        files = {}
        event = self._focused_valid_time()
        collections = self.host._collections()
        if collections:
            from sharpmod.gui_sessions import _viewer_session_ui_state

            win = self.host._window()
            session_path = root / "analysis.sharpmod-session"
            document = build_session(
                collections,
                active_collection=min(self.host._focused_index(), len(collections) - 1),
                ui_state=_viewer_session_ui_state(win),
            )
            write_session(session_path, document)
            self._append_asset(
                assets,
                files,
                CaseAsset(
                    "sounding-session",
                    "sounding-session",
                    event,
                    None,
                    str(getattr(win, "_sharpmod_session_path", "generated:current-viewer") or "generated:current-viewer"),
                    status="complete",
                    note=(
                        "Container includes exact sounding/model metadata; one common "
                        "forecaster-availability time cannot be asserted"
                    ),
                ),
                session_path,
            )
        notes = self.host.notes.toPlainText()
        if notes:
            note_path = root / "notes.txt"
            note_path.write_text(notes, encoding="utf-8", newline="\n")
            self._append_asset(
                assets,
                files,
                CaseAsset(
                    "notes",
                    "notes",
                    event,
                    None,
                    "generated:analysis-notes",
                    status="complete",
                    note="Analyst notes have no independently established availability time",
                ),
                note_path,
            )
        book = getattr(self.host.scenario_workspace, "book", None)
        if book is not None:
            path = write_scenario_book(root / "scenarios.json", book)
            self._append_asset(
                assets,
                files,
                CaseAsset(
                    "scenarios",
                    "sounding",
                    event,
                    None,
                    "generated:hypothetical-scenarios",
                    status="complete",
                    note="Hypothetical sensitivity scenarios; not observations",
                ),
                path,
            )
        pairs = getattr(self.host.verification_workspace, "pairs", ())
        if pairs:
            path = write_verification_json(root / "verification.json", pairs)
            self._append_asset(
                assets,
                files,
                CaseAsset(
                    "verification",
                    "sounding",
                    event,
                    None,
                    "generated:forecast-verification",
                    status="complete",
                    note="Forecast/observation matches and conservative errors",
                ),
                path,
            )
        wind_profiles = getattr(self.host.wind_workspace, "profiles", ())
        if wind_profiles:
            path = write_wind_profiles(root / "wind-profiles.json", wind_profiles)
            observed = wind_profiles[0]
            self._append_asset(
                assets,
                files,
                CaseAsset(
                    "wind-profiles",
                    "wind-profile",
                    observed.observed_at,
                    observed.retrieved_at,
                    observed.source_url,
                    status="complete",
                    note="Wind-only VAD/VWP profiles with quality metadata",
                ),
                path,
            )
        environmental = self._environment_controller()
        map_widget = getattr(environmental, "_map", None) if environmental else None
        if environmental is not None:
            satellite = environmental.satellite_overlay()
            if satellite is not None:
                path = self._write_bytes(root / "goes.png", satellite.raster.image_bytes)
                self._append_asset(
                    assets,
                    files,
                    CaseAsset(
                        "goes-context",
                        "satellite",
                        satellite.selected_time,
                        satellite.raster.retrieved_at,
                        satellite.raster.source_url,
                        status="complete",
                        note=(
                            f"{satellite.satellite} {satellite.channel}; requested "
                            f"{_time_text(satellite.requested_time)}; "
                            f"coverage {satellite.coverage_fraction:.0%}"
                        ),
                    ),
                    path,
                )
            surface = environmental.surface_observations()
            if surface is not None:
                path = write_surface_observations(root / "surface-observations.json", surface)
                self._append_asset(
                    assets,
                    files,
                    CaseAsset(
                        "surface-context",
                        "surface-observations",
                        surface.requested_time,
                        surface.retrieved_at,
                        surface.source_url,
                        status="complete",
                        note=(
                            f"{len(surface.observations)}/{surface.discovered_station_count} "
                            "nearby stations matched"
                        ),
                    ),
                    path,
                )
        if map_widget is not None:
            self._capture_map_layers(root, map_widget, assets, files)
        return assets, files

    def _capture_map_layers(self, root, map_widget, assets, files):
        known_ids = {item.asset_id for item in assets}
        for key, raster in dict(getattr(map_widget, "_rasters", {}) or {}).items():
            text = f"{key} {getattr(raster, 'title', '')}".casefold()
            kind = "satellite" if "goes" in text or "satellite" in text else "radar-image" if "radar" in text else None
            if kind is None or (kind == "satellite" and "goes-context" in known_ids):
                continue
            asset_id = _safe_identifier(str(key), len(assets) + 1)
            suffix = ".png" if bytes(raster.image_bytes).startswith(b"\x89PNG") else ".img"
            path = self._write_bytes(root / f"{asset_id}{suffix}", raster.image_bytes)
            self._append_asset(
                assets,
                files,
                CaseAsset(
                    asset_id,
                    kind,
                    getattr(raster, "valid_time", None),
                    getattr(raster, "retrieved_at", None),
                    getattr(raster, "source_url", ""),
                    status="complete",
                    note=str(getattr(raster, "attribution", "")),
                ),
                path,
            )
        for key, layer in dict(getattr(map_widget, "_overlays", {}) or {}).items():
            text = f"{key} {getattr(layer, 'title', '')}".casefold()
            kind = "storm-reports" if "report" in text else "outlook" if "outlook" in text or "spc" in text else None
            if kind is None:
                continue
            asset_id = _safe_identifier(str(key), len(assets) + 1)
            path = root / f"{asset_id}.json"
            path.write_text(
                json.dumps(
                    {
                        "format": "sharpmod-map-overlay",
                        "version": 1,
                        "layer": _jsonable(layer),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            self._append_asset(
                assets,
                files,
                CaseAsset(
                    asset_id,
                    kind,
                    getattr(layer, "valid_from", None),
                    getattr(layer, "issued", None),
                    getattr(layer, "source_url", ""),
                    status="complete",
                    note=str(getattr(layer, "attribution", "")),
                ),
                path,
            )

    def _prepare_case(self):
        root = Path(tempfile.mkdtemp(prefix="sharpmod-case-"))
        try:
            assets, files = (
                self._prepare_current_assets(root)
                if self.include_current.isChecked()
                else ([], {})
            )
            for asset, path in self._external:
                self._append_asset(assets, files, asset, path)
            if not assets:
                raise ValueError("a portable case needs at least one current or archived asset")
            manifest = CaseManifest(
                self.case_name.text().strip(),
                tuple(assets),
                notes=self.host.notes.toPlainText(),
                source_session_version=SESSION_VERSION,
            )
            return root, manifest, files
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise

    def _suggested(self):
        try:
            return export_file_path("historical-case.sharpmod-case")
        except ExportDirectoryError as exc:
            self._set_status(str(exc), "error")
            return None

    def _build_package(self):
        if self._worker is not None:
            return
        suggested = self._suggested()
        if suggested is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "Build portable historical case",
            str(suggested),
            "SHARPpy portable case (*.sharpmod-case);;All files (*)",
        )
        if not path:
            return
        try:
            root, manifest, files = self._prepare_case()
        except Exception as exc:  # noqa: BLE001 - case form/snapshot boundary
            self._set_status(f"Case package could not start: {exc}", "error")
            return
        self._token += 1
        worker = _BuildWorker(
            self._token, path, manifest, files, root, parent=self
        )
        self._worker = worker
        worker.progress.connect(
            lambda done, total, detail: self._set_status(
                f"Archived download {done}/{total}: {detail}"
            )
        )
        worker.completed.connect(self._built)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self.build_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._set_status("Hashing local assets and downloading planned archives with at most four workers…")
        worker.start()

    def _cancel(self):
        if self._worker is not None:
            self._worker.requestInterruption()
            self._set_status("Cancelling remote downloads; completed bytes will remain in a valid partial package…")

    def _built(self, token, path, manifest, cancelled):
        if token != self._token:
            return
        missing = len(manifest.missing_assets)
        qualifier = "partial cancelled" if cancelled else "complete"
        self._set_status(
            f"Saved {qualifier} integrity-checked case: {path}; "
            f"{len(manifest.assets) - missing}/{len(manifest.assets)} assets packaged."
        )

    def _open_package(self):
        if self._worker is not None:
            return
        suggested = self._suggested()
        if suggested is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Open portable historical case",
            str(Path(suggested).parent),
            "SHARPpy portable case (*.sharpmod-case *.zip);;All files (*)",
        )
        if not path:
            return
        self._token += 1
        worker = _LoadWorker(self._token, path, self)
        self._worker = worker
        worker.completed.connect(self._loaded_ready)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self.build_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._set_status("Validating package member paths, sizes, and SHA-256 digests…")
        worker.start()

    def _loaded_ready(self, token, loaded):
        if token != self._token:
            return
        self._loaded = loaded
        self._case_collections = ()
        self._clock = ReplayClock(loaded.manifest.replay_times)
        self.replay_time.blockSignals(True)
        self.replay_time.clear()
        for index, when in enumerate(self._clock.times):
            self.replay_time.addItem(_time_text(when), index)
        self.replay_time.blockSignals(False)
        if self.replay_time.count():
            self.replay_time.setCurrentIndex(0)
        limitations = loaded.manifest.training_limitations
        self.training_limitations.setText(
            "Training limitations: " + "; ".join(limitations)
            if limitations
            else "All packaged products have explicit availability timestamps."
        )
        self.tabs.setCurrentIndex(1)
        self._render_replay()
        self._set_status(
            f"Opened {loaded.manifest.name}: {len(loaded.manifest.assets)} verified manifest entries, "
            f"{len(loaded.assets)} packaged payloads."
        )

    def _failed(self, token, message):
        if token == self._token:
            self._set_status(f"Case operation failed: {message}", "error")

    def _finished(self):
        worker = self.sender()
        if worker is self._worker:
            self._worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        self.build_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _select_time(self, index):
        if self._loaded is None or not 0 <= index < len(self._clock.times):
            return
        self._clock.select(self._clock.times[index])
        self._clock.pause()
        self._timer.stop()
        self.play.setText("Play")
        self._render_replay()
        self._apply_replay_time()

    def _step(self, amount):
        if self._clock.current is None:
            return
        self._clock.pause()
        self._timer.stop()
        self._clock.step(amount)
        self.replay_time.setCurrentIndex(self._clock.index)
        self.play.setText("Play")

    def _toggle_play(self):
        if self._clock.current is None:
            return
        if self._clock.playing:
            self._clock.pause()
            self._timer.stop()
            self.play.setText("Play")
        else:
            self._clock.play()
            self._timer.start(self.speed.value())
            self.play.setText("Pause")

    def _tick(self):
        before = self._clock.index
        self._clock.tick()
        self.replay_time.setCurrentIndex(self._clock.index)
        if not self._clock.playing or self._clock.index == before:
            self._timer.stop()
            self.play.setText("Play")

    def _update_speed(self, value):
        if self._timer.isActive():
            self._timer.setInterval(int(value))

    def _visible_ids(self):
        if self._loaded is None or self._clock.current is None:
            return set()
        return {
            asset.asset_id
            for asset in self._loaded.manifest.visible_assets(
                self._clock.current, training_mode=self.training.isChecked()
            )
        }

    def _render_replay(self, *_args):
        if self._loaded is None:
            self.assets.setRowCount(0)
            self._update_replay_controls()
            return
        visible = self._visible_ids()
        entries = self._loaded.manifest.assets
        self.assets.setRowCount(len(entries))
        for row, asset in enumerate(entries):
            shown = asset.asset_id in visible
            values = (
                "Yes" if shown else "No",
                asset.asset_id,
                asset.kind,
                _time_text(asset.event_time),
                _time_text(asset.available_time),
                asset.status,
                asset.note,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, asset.asset_id)
                self.assets.setItem(row, column, item)
        self._update_replay_controls()
        self._preview_selected()

    def _preview_selected(self):
        if self._loaded is None:
            return
        row = self.assets.currentRow()
        if not 0 <= row < len(self._loaded.manifest.assets):
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Select a manifest asset to inspect its packaged bytes and provenance.")
            return
        asset = self._loaded.manifest.assets[row]
        if asset.asset_id not in self._visible_ids():
            self.preview.setPixmap(QPixmap())
            boundary = asset.available_time if self.training.isChecked() else asset.event_time
            self.preview.setText(
                f"{asset.asset_id} is hidden at {_time_text(self._clock.current)}. "
                f"Reveal boundary: {_time_text(boundary)}."
            )
            return
        payload = self._loaded.asset_bytes(asset.asset_id)
        if payload is None:
            self.preview.setPixmap(QPixmap())
            self.preview.setText(
                f"{asset.asset_id}: {asset.status}. {asset.note or 'No packaged bytes.'}"
            )
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(payload):
            self.preview.setText("")
            self.preview.setPixmap(
                pixmap.scaled(
                    max(1, self.preview.width()),
                    max(1, self.preview.height()),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
            return
        self.preview.setPixmap(QPixmap())
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            text = f"Binary data ({len(payload)} bytes); use the named product adapter after export."
        self.preview.setText(text[:8000] + ("\n…" if len(text) > 8000 else ""))

    def _session_asset(self):
        if self._loaded is None:
            return None
        return next(
            (
                asset
                for asset in self._loaded.manifest.assets
                if asset.kind == "sounding-session" and asset.status == "complete"
            ),
            None,
        )

    def _open_soundings(self):
        asset = self._session_asset()
        if asset is None or asset.asset_id not in self._visible_ids():
            self._set_status(
                "Packaged soundings are not visible at this replay/training time.",
                "warn",
            )
            return
        payload = self._loaded.asset_bytes(asset.asset_id)
        if payload is None:
            self._set_status("The case session payload is missing.", "error")
            return
        root = Path(tempfile.mkdtemp(prefix="sharpmod-case-session-"))
        path = root / "case.sharpmod-session"
        try:
            path.write_bytes(payload)
            from sharpmod.sessions import read_session

            document = read_session(path)
            collections = tuple(
                restore_collection(item) for item in document["collections"]
            )
            win = self.host._window()
            add = getattr(win, "addProfileCollection", None)
            if not callable(add):
                raise RuntimeError("this viewer cannot add packaged soundings")
            for collection in collections:
                add(collection, focus=True, check_integrity=False)
            self._case_collections = collections
            self.host._populate_controls()
            self._apply_replay_time()
        except Exception as exc:  # noqa: BLE001 - session boundary
            self._set_status(f"Packaged soundings could not be opened: {exc}", "error")
            return
        finally:
            shutil.rmtree(root, ignore_errors=True)
        self._set_status(
            f"Opened {len(collections)} packaged sounding collection(s); replay uses exact times only."
        )

    def _apply_replay_time(self):
        when = self._clock.current
        if when is None or not self._case_collections:
            return
        if self.training.isChecked():
            asset = self._session_asset()
            if asset is None or asset.asset_id not in self._visible_ids():
                return
        matched = []
        for collection in self._case_collections:
            dates = tuple(getattr(collection, "_dates", ()) or ())
            if when not in dates:
                continue
            try:
                collection.setCurrentDate(when)
            except Exception:
                collection._prof_idx = dates.index(when)
            matched.append(collection)
        win = self.host._window()
        sw = getattr(win, "spc_widget", None)
        if matched and sw is not None:
            try:
                index = tuple(sw.prof_collections).index(matched[0])
                sw.setProfileCollection(sw.prof_ids[index])
                sw.updateProfs()
            except Exception:
                pass
        if not matched:
            self._set_status(
                f"No packaged sounding exists at exact replay time {_time_text(when)}; "
                "no nearby sounding was substituted.",
                "warn",
            )

    def _update_replay_controls(self):
        has_times = self._clock.current is not None
        self.previous.setEnabled(has_times and self._clock.index > 0)
        self.next.setEnabled(has_times and self._clock.index + 1 < len(self._clock.times))
        self.play.setEnabled(has_times and len(self._clock.times) > 1)
        asset = self._session_asset()
        self.open_soundings.setEnabled(
            bool(asset is not None and asset.asset_id in self._visible_ids())
        )

    def session_state(self):
        # Ordinary analysis sessions stay lightweight: case payload bytes and
        # local archive paths are intentionally not embedded here.
        return {
            "name": self.case_name.text(),
            "include_current": self.include_current.isChecked(),
            "training": self.training.isChecked(),
            "speed_ms": self.speed.value(),
            "mode": self.tabs.currentIndex(),
            "replay_split": self.replay_splitter.sizes(),
        }

    def restore_session_state(self, state):
        if not isinstance(state, Mapping):
            return
        self.case_name.setText(str(state.get("name") or self.case_name.text()))
        self.include_current.setChecked(bool(state.get("include_current", True)))
        self.training.setChecked(bool(state.get("training", False)))
        try:
            self.speed.setValue(int(state.get("speed_ms", 1000)))
            self.tabs.setCurrentIndex(int(state.get("mode", 0)))
        except (TypeError, ValueError):
            pass
        sizes = state.get("replay_split")
        if isinstance(sizes, (list, tuple)) and len(sizes) == 2:
            try:
                self.replay_splitter.setSizes([int(item) for item in sizes])
            except (TypeError, ValueError):
                pass

    def _set_status(self, text, level="info"):
        role = {"info": OBJ_STATUS, "warn": OBJ_WARNING_TEXT, "error": OBJ_ERROR_TEXT}.get(level, OBJ_STATUS)
        self.status.setObjectName(role)
        self.status.setText(str(text))
        style = self.status.style()
        style.unpolish(self.status)
        style.polish(self.status)

    def shutdown(self):
        self._timer.stop()
        self._clock.pause()
        self._token += 1
        worker = self._worker
        if worker is None:
            return
        worker.requestInterruption()
        worker.wait(2000)
        if worker.isRunning():
            from sharpmod.gui_threading import retain_worker_until_finished

            retain_worker_until_finished(worker)
        self._worker = None


__all__ = ["CaseReplayWorkspace"]
