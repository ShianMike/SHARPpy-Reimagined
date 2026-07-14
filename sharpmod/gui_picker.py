from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np

"""Sounding picker/controller window and desktop application entry point."""

from sharpmod.gui_common import (
    APP_NAME,
    APP_VERSION,
    CONTROLS_HTML,
    MAX_RECENTS,
    PICKER_DARK_QSS,
    PICKER_RAIL_MAX_WIDTH,
    PICKER_RAIL_MIN_WIDTH,
    SYNOPTIC_HOURS,
    _LOGGER,
    _configure_debug_logging,
    _format_progress_bytes,
    _format_progress_duration,
    _most_recent_synoptic,
    _render,
    _show_controls_dialog,
    _uwyo_catalog,
)
from sharpmod.gui_maps import MAP_AREAS, PointMapWidget, StationMapWidget
from sharpmod.model_disk_cache import ModelDiskCache
from sharpmod.model_hour_cache import ModelHourCache
from sharpmod.gui_sessions import _apply_viewer_session_state
from sharpmod.gui_settings import (
    UNIT_DEFAULTS,
    UNIT_OPTIONS,
    _DEFAULT_SKEWT_PARCEL,
    _UnitPreferencesDialog,
    _add_default_parcel_tab,
    _apply_default_parcel_to_window,
    _apply_unit_preferences_to_window,
    _build_preferences_dialog,
    _build_settings,
    _normalize_default_parcel,
    _normalize_unit_preferences,
    _read_config_preferences,
    _read_config_unit,
    _read_settings_preferences,
    _save_settings_preferences,
    _write_config_preferences,
    _write_unit_preferences_to_config,
)
from sharpmod.gui_viewer import compose_interactive
from sharpmod.gui_workers import (
    AVAIL_CHECKING,
    AVAIL_FALLBACK,
    AVAIL_UNKNOWN,
    _AVAIL_LABELS,
    _AvailabilityIndicator,
    _AvailabilityWorker,
    _FetchWorker,
    _ModelAvailabilityWorker,
    _ModelFetchWorker,
    _ModelPrefetchWorker,
    _StationListWorker,
    _cleanup_model_data,
    _retain_model_data_until_close,
    _station_label,
)

from qtpy.QtCore import (
    Qt, QThread, QTimer, Signal, QDate, QSettings, QPointF, QRectF, QSize, QUrl,
)
from qtpy.QtGui import (
    QAction, QPainter, QColor, QPen, QBrush, QPolygonF, QFont, QPixmap, QIcon,
    QTransform, QDesktopServices,
)
from qtpy.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QDateEdit,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QGroupBox,
    QStatusBar,
    QToolButton,
    QScrollArea,
    QFrame,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QCheckBox,
    QSizePolicy,
    QGraphicsView,
    QGraphicsScene,
    QProgressBar,
    QMenu,
)

_STABLE_GUI_RUNTIME_ENV = "SHARPMOD_GUI_STABLE_RUNTIME"


def _project_gui_runtime() -> tuple[Path, Path] | None:
    """Return a project Python suitable for the Windows desktop GUI.

    Release executables are frozen with the supported Python runtime.  Source
    checkouts may instead be invoked by whichever ``python`` is first on PATH,
    so prefer their local environment when it exists.
    """
    project_root = Path(__file__).resolve().parents[1]
    current = Path(sys.executable).resolve()
    for environment in (".gribenv", ".venv", "venv"):
        scripts = project_root / environment / "Scripts"
        for executable in ("pythonw.exe", "python.exe"):
            candidate = scripts / executable
            if candidate.is_file() and candidate.resolve() != current:
                return candidate, project_root
    return None


def _show_stable_gui_runtime_required() -> None:
    """Explain why an unsupported Windows source runtime cannot continue."""
    message = (
        "SHARPpy Reimagined cannot safely start its Windows desktop GUI with "
        "Python 3.14. Create this checkout's .venv with Python 3.11-3.13, or "
        "use the packaged Windows release (which includes Python 3.11)."
    )
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    except Exception:  # noqa: BLE001 - stderr is the non-GUI fallback
        print(message, file=sys.stderr)


def _relaunch_stable_windows_gui(arguments: list[str]) -> bool:
    """Relaunch an unfrozen Python 3.14 Windows GUI with project Python.

    CPython 3.14 can access-violate inside ``python314.dll`` while PySide6 is
    dispatching the visible Windows picker, leaving no catchable traceback.
    The project/release runtime is Python 3.11, so switch before QApplication
    starts rather than allowing a native crash.
    """
    if sys.platform != "win32" or sys.version_info < (3, 14):
        return False
    if getattr(sys, "frozen", False) \
            or os.environ.get(_STABLE_GUI_RUNTIME_ENV) == "1":
        return False

    runtime = _project_gui_runtime()
    if runtime is None:
        _LOGGER.error(
            "application.stable_runtime_missing python=%s", sys.executable)
        _show_stable_gui_runtime_required()
        return True

    python, project_root = runtime
    environment = os.environ.copy()
    environment[_STABLE_GUI_RUNTIME_ENV] = "1"
    command = [str(python), "-m", "sharpmod.gui", *arguments]
    try:
        subprocess.Popen(
            command,
            cwd=str(project_root),
            env=environment,
            close_fds=True,
        )
    except OSError:
        _LOGGER.exception(
            "application.stable_runtime_relaunch_failed runtime=%s", python)
        return False

    _LOGGER.info(
        "application.stable_runtime_relaunch source=%s target=%s",
        sys.executable, python)
    return True


class PickerWindow(QMainWindow):
    """The launcher: fetch an observed sounding or open a local sounding file.

    Designed to be immediately usable: the full UWyo station catalogue is loaded
    up front and filtered live as you type, the observation time defaults to the
    most recent sounding cycle, and a sounding opens on a double-click (or the
    single Fetch button). Local files can be dropped straight onto the window.
    """

    #: Emitted after the preferences change; every open ``SPCWindow`` subscribes
    #: to this (via its Qt parent) to refresh profiles + re-apply the palette.
    #: The picker doubles as the SHARPpy controller (mirrors the legacy Main
    #: window), so it owns the shared config and this signal.
    config_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} \u2014 Sounding Picker")
        self.resize(1000, 720)
        self.setMinimumSize(900, 620)
        self.setStyleSheet(PICKER_DARK_QSS)
        self.setAcceptDrops(True)  # drag a sounding file onto the window

        # Keep every opened sounding window alive. Each vendored ``SPCWindow`` is
        # a top-level window parented to this picker, but we also hold a Python
        # reference so it is never garbage-collected out from under Qt.
        self._viewers: list = []
        self._worker: _FetchWorker | None = None
        self._model_worker: _ModelFetchWorker | None = None
        self._model_prefetch_worker: _ModelPrefetchWorker | None = None
        self._model_disk_cache = ModelDiskCache()
        try:
            self._model_disk_cache.prune()
        except OSError:
            _LOGGER.exception("model_disk_cache.startup_prune_failed")
        self._model_hour_cache = ModelHourCache(
            max_entries=1,
            directory_factory=self._model_disk_cache.directory_for,
            directory_protector=self._model_disk_cache.protect,
            delete_download_dirs=False,
        )
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_model_cache)
        self._model_progress_stage = ""
        self._model_progress_total = 0
        self._model_progress_started = 0.0
        self._model_progress_timer = QTimer(self)
        self._model_progress_timer.setInterval(500)
        self._model_progress_timer.timeout.connect(
            self._poll_model_fetch_progress)
        self._settings = _build_settings()
        self._all_stations = _uwyo_catalog().all_stations()

        # -- availability pre-flight check state ----------------------------- #
        # A background probe grades the selected station/time as green (usable),
        # red (nothing archived / unreachable), or gray (present but too sparse
        # or corrupt). Checks are debounced and stale results are discarded via
        # a monotonically increasing token.
        self._avail_workers: list[_AvailabilityWorker] = []
        self._avail_pending: dict[int, _AvailabilityIndicator] = {}
        self._avail_latest: dict[int, int] = {}
        self._avail_token = 0
        self._avail_request: tuple | None = None
        self._avail_timer = QTimer(self)
        self._avail_timer.setSingleShot(True)
        self._avail_timer.setInterval(350)
        self._avail_timer.timeout.connect(self._run_pending_availability)

        # Forecast catalog checks are independent of the actual fetch worker.
        # They are deliberately advisory: a failed inventory request never
        # disables Fetch because catalog propagation and upstream mirrors can
        # lag behind the data itself.
        self._model_availability_workers: list[_ModelAvailabilityWorker] = []
        self._model_availability_token = 0
        self._model_availability_request: tuple | None = None
        self._model_available_run: datetime | None = None
        self._model_availability_timer = QTimer(self)
        self._model_availability_timer.setSingleShot(True)
        self._model_availability_timer.setInterval(450)
        self._model_availability_timer.timeout.connect(
            self._run_model_availability)

        # -- datetime-aware station catalogue state -------------------------- #
        # The station set shown in the map + list is refreshed from UWyo for the
        # selected observation time, so relocated / re-indexed stations appear
        # for the period they actually reported. The bundled catalogue is the
        # offline fallback used until the first live list arrives (or if the
        # network is unavailable). Refreshes are debounced and stale results are
        # discarded via a monotonically increasing token.
        self._catalog_when: datetime | None = None
        self._catalog_worker: _StationListWorker | None = None
        self._catalog_token = 0
        self._catalog_request: datetime | None = None
        self._catalog_timer = QTimer(self)
        self._catalog_timer.setSingleShot(True)
        self._catalog_timer.setInterval(300)
        self._catalog_timer.timeout.connect(self._run_pending_catalog)

        # The one shared render/display config, owned by the controller (this
        # window). Built lazily on first sounding/preference use so the picker
        # window appears before the heavy render stack is imported.
        self.config = None

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_map_tab(), "Station Map")
        self._tabs.addTab(self._build_uwyo_tab(), "Station List")
        self._tabs.addTab(self._build_model_tab(), "Forecast Model")
        self._tabs.addTab(self._build_file_tab(), "Open File")
        self._tabs.currentChanged.connect(self._sync_tab_status)
        self.setCentralWidget(self._tabs)

        self.setStatusBar(QStatusBar())
        self._sync_tab_status()

        self._build_menu()
        self._restore_state()

        # Populate the live, datetime-aware station set for the default cycle in
        # the background (the bundled catalogue shows immediately as fallback).
        self._refresh_station_catalog(self._selected_when())

    def _sync_tab_status(self, *_args) -> None:
        if not hasattr(self, "_tabs") or self.statusBar() is None:
            return
        tab = self._tabs.tabText(self._tabs.currentIndex())
        if tab == "Forecast Model":
            self.statusBar().showMessage(
                "Ready \u2014 pick a point, model, run, and forecast hour")
        elif tab == "Open File":
            self.statusBar().showMessage("Ready \u2014 open a sounding file")
        else:
            self.statusBar().showMessage(
                "Ready \u2014 pick a station and press Fetch")

    # -- menu ---------------------------------------------------------------- #
    def _build_menu(self) -> None:
        filemenu = self.menuBar().addMenu("&File")
        open_act = QAction("&Open Sounding File\u2026", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._browse_and_open)
        filemenu.addAction(open_act)
        session_act = QAction("Open Analysis &Session…", self)
        session_act.setShortcut("Ctrl+Shift+O")
        session_act.triggered.connect(self._open_analysis_session)
        filemenu.addAction(session_act)
        self._open_session_action = session_act
        filemenu.addSeparator()
        pref_act = QAction("&Preferences\u2026", self)
        pref_act.setShortcut("Ctrl+,")
        pref_act.triggered.connect(self.preferencesbox)
        filemenu.addAction(pref_act)
        units_act = QAction("&Units\u2026", self)
        units_act.setShortcut("Ctrl+U")
        units_act.triggered.connect(lambda _checked=False: self.unit_preferencesbox())
        filemenu.addAction(units_act)
        filemenu.addSeparator()
        combine_act = QAction("Add New Soundings to Active Window", self)
        combine_act.setCheckable(True)
        combine_act.setChecked(self._combine_soundings_enabled())
        combine_act.toggled.connect(self._save_combine_soundings)
        filemenu.addAction(combine_act)
        self._combine_soundings_action = combine_act
        prefetch_act = QAction("Prefetch Next Forecast Hour", self)
        prefetch_act.setCheckable(True)
        prefetch_act.setChecked(self._model_prefetch_enabled())
        prefetch_act.toggled.connect(self._save_model_prefetch)
        filemenu.addAction(prefetch_act)
        self._model_prefetch_action = prefetch_act
        clear_cache_act = QAction("Clear Downloaded Model Cache", self)
        clear_cache_act.triggered.connect(self._clear_model_cache)
        filemenu.addAction(clear_cache_act)
        self._clear_model_cache_action = clear_cache_act
        filemenu.addSeparator()
        quit_act = QAction("&Quit", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        filemenu.addAction(quit_act)

        helpmenu = self.menuBar().addMenu("&Help")
        controls_act = QAction("Sounding Window &Controls", self)
        controls_act.triggered.connect(self._show_controls_help)
        helpmenu.addAction(controls_act)
        debug_act = QAction("Open &Debug Log Folder", self)
        debug_act.triggered.connect(self._open_debug_log_folder)
        helpmenu.addAction(debug_act)
        helpmenu.addSeparator()
        about_act = QAction("&About", self)
        about_act.triggered.connect(self._about)
        helpmenu.addAction(about_act)

    def _about(self) -> None:
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> v{APP_VERSION}<br><br>"
            "A modernized, standalone fork of SHARPpy.<br>"
            "SPC-style skew-T / hodograph sounding analysis (Qt6/PySide6).<br><br>"
            "<b>Tips:</b> type to filter stations, double-click one to open it, "
            "or drag a sounding file onto the window.")

    def _open_analysis_session(self, path=None) -> None:
        """Open a validated session as one new multi-sounding viewer."""
        if isinstance(path, bool):  # QAction.triggered supplies ``checked``.
            path = None
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Open Analysis Session",
                str(Path.home()),
                "SHARPpy Analysis Session (*.sharpmod-session)",
            )
        path = str(path or "").strip()
        if not path:
            return
        from sharpmod.sessions import (
            SessionFormatError,
            read_session,
            restore_collection,
        )
        try:
            # Validate and reconstruct every sounding before composing a window;
            # a malformed file therefore cannot partially mutate the UI.
            document = read_session(path)
            collections = [
                restore_collection(payload)
                for payload in document["collections"]
            ]
        except (OSError, SessionFormatError, ValueError) as exc:
            _LOGGER.exception("analysis_session.open_failed path=%s", path)
            QMessageBox.critical(
                self, APP_NAME, f"The analysis session could not be opened:\n{exc}")
            return

        first = collections[0]
        try:
            stn_id = first.getMeta("loc")
        except Exception:
            stn_id = "Session"
        try:
            win = compose_interactive(
                self._config(), first, self, stn_id=stn_id)
            for collection in collections[1:]:
                win.addProfileCollection(
                    collection, focus=True, check_integrity=False)
            _apply_viewer_session_state(
                win,
                document.get("active_collection", 0),
                document.get("ui_state"),
            )
        except Exception as exc:  # noqa: BLE001 - user-facing restore error
            _LOGGER.exception("analysis_session.display_failed path=%s", path)
            try:
                win.close()
            except Exception:
                pass
            QMessageBox.critical(
                self, APP_NAME,
                f"The session was valid, but its viewer could not be opened:\n{exc}")
            return

        win._sharpmod_session_path = path
        history = getattr(win, "_sharpmod_history", None)
        if history is not None:
            history.clear()
        self._viewers.append(win)
        viewer_id = id(win)
        win.destroyed.connect(
            lambda *_args, viewer_id=viewer_id, path=path: _LOGGER.info(
                "viewer.closed viewer=%s session=%s", viewer_id, path))
        self.statusBar().showMessage(
            f"Opened analysis session with {len(collections)} sounding"
            f"{'s' if len(collections) != 1 else ''}",
            5000,
        )
        _LOGGER.info(
            "analysis_session.opened path=%s viewer=%s soundings=%d",
            path, viewer_id, len(collections))

    def _show_controls_help(self) -> None:
        _show_controls_dialog(self)

    def _open_debug_log_folder(self) -> None:
        log_path = _configure_debug_logging()
        opened = QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(log_path.parent.resolve())))
        if opened:
            self.statusBar().showMessage(f"Debug log: {log_path}")
            _LOGGER.info("diagnostics.folder_opened path=%s", log_path.parent)
        else:
            QMessageBox.information(
                self,
                APP_NAME,
                f"Debug log location:\n{log_path}",
            )

    # -- controller contract (this window doubles as the SHARPpy controller) - #
    def preferencesbox(self) -> None:
        """Open the SHARPpy preferences dialog (palette + units).

        Mirrors the legacy ``Main.preferencesbox``: the dialog edits the shared
        :attr:`config` in place; on close we re-apply the fork's alert-tier
        color substitutions and broadcast :attr:`config_changed` so every open
        sounding window refreshes its profiles and palette.
        """
        try:
            config = self._config()
            dialog = _build_preferences_dialog(config, parent=self)
        except Exception as exc:  # pragma: no cover - vendored dep always present
            QMessageBox.warning(self, APP_NAME,
                                f"Preferences are unavailable:\n{exc}")
            return
        parcel_box = _add_default_parcel_tab(dialog, self._default_parcel())
        accepted = dialog.exec()
        # Keep the fork's legibility substitutions after any palette change.
        try:
            from sharpmod import colors
            config["preferences", "alert_l1_color"] = colors.ALERT_L1_COLOR
            config["preferences", "alert_l2_color"] = colors.ALERT_L2_COLOR
        except Exception:
            pass
        if accepted:
            self._save_config_preferences(config)
        self.config_changed.emit(config)
        self._apply_unit_preferences_to_viewers(config)
        if accepted and parcel_box is not None:
            parcel_key = _normalize_default_parcel(parcel_box.currentData())
            self._save_default_parcel(parcel_key)
            self._apply_default_parcel_to_viewers(parcel_key)

    def unit_preferencesbox(self, parent=None) -> None:
        """Open the compact display-units popup."""
        dialog = _UnitPreferencesDialog(self._unit_preferences(), parent=parent or self)
        if dialog.exec():
            prefs = dialog.preferences()
            self._save_unit_preferences(prefs)
            config = self._config()
            _write_unit_preferences_to_config(config, prefs)
            self.config_changed.emit(config)
            self._apply_unit_preferences_to_viewers(config)

    def focusPicker(self) -> None:  # noqa: N802 - matches SPCWindow's caller
        """Bring the picker back to the front (the ``W`` key target)."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _config(self):
        """Build and cache the shared render config on first real use."""
        if self.config is None:
            saved_preferences = _read_settings_preferences(
                getattr(self, "_settings", None))
            try:
                self.statusBar().showMessage("Loading analysis engine\u2026")
                QApplication.processEvents()
            except Exception:
                pass
            self.config = _render().build_config(tempfile.gettempdir())
            _write_config_preferences(self.config, saved_preferences)
        return self.config

    def _unit_preferences(self):
        prefs = dict(UNIT_DEFAULTS)
        settings = getattr(self, "_settings", None)
        if settings is not None:
            for key in UNIT_DEFAULTS:
                value = settings.value("units/" + key, "", str)
                if value in UNIT_OPTIONS[key]:
                    prefs[key] = value
        if self.config is not None:
            prefs.update(self._unit_preferences_from_config(self.config))
        return _normalize_unit_preferences(prefs)

    def _unit_preferences_from_config(self, config):
        prefs = {}
        for key in UNIT_DEFAULTS:
            value = _read_config_unit(config, key)
            if value is not None:
                prefs[key] = value
        return _normalize_unit_preferences(prefs)

    def _save_unit_preferences(self, preferences) -> None:
        _save_settings_preferences(
            getattr(self, "_settings", None),
            _normalize_unit_preferences(preferences),
        )

    def _save_config_preferences(self, config) -> None:
        _save_settings_preferences(
            getattr(self, "_settings", None),
            _read_config_preferences(config),
        )

    def _default_parcel(self) -> str:
        """Return the persistent parcel used for newly opened Skew-Ts."""
        settings = getattr(self, "_settings", None)
        if settings is None:
            return _DEFAULT_SKEWT_PARCEL
        return _normalize_default_parcel(
            settings.value("parcel/default_skewt", _DEFAULT_SKEWT_PARCEL, str))

    def _save_default_parcel(self, parcel_key) -> None:
        settings = getattr(self, "_settings", None)
        if settings is not None:
            settings.setValue(
                "parcel/default_skewt",
                _normalize_default_parcel(parcel_key),
            )
            settings.sync()

    def _model_prefetch_enabled(self) -> bool:
        settings = getattr(self, "_settings", None)
        return False if settings is None else settings.value(
            "model/prefetch_next_hour", False, bool
        )

    def _save_model_prefetch(self, enabled) -> None:
        settings = getattr(self, "_settings", None)
        if settings is not None:
            settings.setValue("model/prefetch_next_hour", bool(enabled))
            settings.sync()
        if not enabled:
            self._cancel_model_prefetch(wait=False)

    def _combine_soundings_enabled(self) -> bool:
        """Whether future opens should join the last visible analysis window."""
        action = getattr(self, "_combine_soundings_action", None)
        if action is not None:
            return action.isChecked()
        return bool(self._settings.value(
            "viewer/combine_soundings", True, bool))

    def _save_combine_soundings(self, enabled: bool) -> None:
        self._settings.setValue("viewer/combine_soundings", bool(enabled))
        self._settings.sync()
        _LOGGER.info("viewer.combine_soundings enabled=%s", bool(enabled))

    def _apply_default_parcel_to_viewers(self, parcel_key) -> None:
        for viewer in list(getattr(self, "_viewers", [])):
            _apply_default_parcel_to_window(viewer, parcel_key)

    def _apply_unit_preferences_to_viewers(self, config) -> None:
        for viewer in list(getattr(self, "_viewers", [])):
            _apply_unit_preferences_to_window(viewer, config)

    # ====================================================================== #
    # Availability pre-flight check (green / red / gray)
    # ====================================================================== #
    def _station_label_for(self, sid: str | None) -> str:
        """Look up a station's index + city label from the loaded catalogue."""
        if not sid:
            return ""
        st = next((s for s in self._all_stations if s["id"] == sid), None)
        return _station_label(sid, st["name"] if st else "")

    def _queue_availability(self, sid: str | None, when: datetime,
                            indicator: "_AvailabilityIndicator") -> None:
        """Debounce, then probe ``sid`` at ``when`` and update ``indicator``."""
        if not sid:
            indicator.set_status(AVAIL_UNKNOWN)
            return
        indicator.set_status(
            AVAIL_CHECKING,
            _AVAIL_LABELS[AVAIL_CHECKING],
            self._station_label_for(sid))
        self._avail_request = (sid, when, indicator, self._station(sid))
        self._avail_timer.start()

    def _run_pending_availability(self) -> None:
        if not self._avail_request:
            return
        sid, when, indicator, station = self._avail_request
        self._avail_request = None
        self._avail_token += 1
        token = self._avail_token
        self._avail_pending[token] = indicator
        self._avail_latest[id(indicator)] = token

        worker = _AvailabilityWorker(sid, when, token, parent=self,
                                     station=station)
        worker.checked.connect(self._on_availability_checked)
        worker.finished.connect(worker.deleteLater)
        self._avail_workers.append(worker)
        worker.start()

    def _on_availability_checked(self, _sid: str, _when, status: str,
                                 message: str, station_label: str) -> None:
        worker = self.sender()
        token = getattr(worker, "token", None)
        indicator = self._avail_pending.pop(token, None)
        if worker in self._avail_workers:
            self._avail_workers.remove(worker)
        if indicator is None:
            return
        # Discard results superseded by a newer check for the same indicator.
        if self._avail_latest.get(id(indicator)) != token:
            return
        indicator.set_status(status, message, station_label)

    # ====================================================================== #
    # Datetime-aware station catalogue refresh
    # ====================================================================== #
    def _refresh_station_catalog(self, when: datetime) -> None:
        """Debounce, then fetch the stations UWyo reported at ``when`` (UTC).

        No-ops when the catalogue already matches ``when`` so switching between
        tabs (which share one station set) does not re-fetch needlessly.
        """
        if when == self._catalog_when:
            return
        self._catalog_request = when
        self._catalog_timer.start()

    def _run_pending_catalog(self) -> None:
        when = self._catalog_request
        self._catalog_request = None
        if when is None:
            return
        self._catalog_token += 1
        token = self._catalog_token
        try:
            self.statusBar().showMessage(
                f"Loading stations for {when:%Y-%m-%d %H}Z from UWyo\u2026")
        except Exception:
            pass
        worker = _StationListWorker(when, token, parent=self)
        worker.loaded.connect(self._on_station_list_loaded)
        worker.failed.connect(self._on_station_list_failed)
        worker.finished.connect(worker.deleteLater)
        self._catalog_worker = worker
        worker.start()

    def _on_station_list_loaded(self, when, stations) -> None:
        worker = self.sender()
        if getattr(worker, "token", None) != self._catalog_token:
            return  # a newer request superseded this one
        self._all_stations = list(stations)
        self._catalog_when = when

        # Repaint the map and re-run the live filter against the new set.
        if hasattr(self, "_map"):
            self._map.set_stations(self._all_stations)
            if self._map_selected_id and \
                    self._station(self._map_selected_id) is None:
                # The previously selected station isn't reported at this time.
                self._map_selected_id = None
                self._map_sel_lbl.setText("No station selected")
                self._map_gen_btn.setEnabled(False)
                self._map_avail.set_status(AVAIL_UNKNOWN)
        if hasattr(self, "_uwyo_search"):
            self._filter_stations(self._uwyo_search.text())

        try:
            self.statusBar().showMessage(
                f"{len(self._all_stations)} stations available for "
                f"{when:%Y-%m-%d %H}Z")
        except Exception:
            pass

    def _on_station_list_failed(self, when, message: str) -> None:
        worker = self.sender()
        if getattr(worker, "token", None) != self._catalog_token:
            return
        # Keep the current (bundled or previous) catalogue; just note it.
        try:
            self.statusBar().showMessage(
                f"Using offline station list \u2014 could not load "
                f"{when:%Y-%m-%d %H}Z ({message})")
        except Exception:
            pass

    # ====================================================================== #
    # Station Map tab (legacy-SHARPpy style)
    # ====================================================================== #
    def _build_map_tab(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(12)

        # --- left control column ---
        left = QVBoxLayout()
        left.setSpacing(8)
        left.setContentsMargins(0, 0, 0, 0)

        src_box = QGroupBox("Sounding source")
        sb = QVBoxLayout(src_box)
        src_combo = QComboBox()
        src_combo.addItem("Observed (UWyo radiosonde)")
        src_combo.setEnabled(False)  # only observed is wired today
        sb.addWidget(src_combo)
        left.addWidget(src_box)

        cycle_box = QGroupBox("Cycle (UTC)")
        cg = QGridLayout(cycle_box)
        cg.setColumnStretch(1, 1)
        default_date, default_hour = _most_recent_synoptic()
        cg.addWidget(QLabel("Date:"), 0, 0)
        self._map_date = QDateEdit()
        self._map_date.setDisplayFormat("yyyy-MM-dd")
        self._map_date.setCalendarPopup(True)
        self._map_date.setDate(default_date)
        self._map_date.setMaximumDate(QDate.currentDate().addDays(1))
        self._map_date.setMinimumWidth(118)
        cg.addWidget(self._map_date, 0, 1)
        cg.addWidget(QLabel("Time:"), 1, 0)
        self._map_cycle = QComboBox()
        for h in SYNOPTIC_HOURS:
            self._map_cycle.addItem(f"{h:02d}Z", h)
        self._map_cycle.setCurrentIndex(SYNOPTIC_HOURS.index(default_hour))
        self._map_cycle.setMinimumWidth(72)
        cg.addWidget(self._map_cycle, 1, 1)
        recent = QToolButton()
        recent.setText("Most recent")
        recent.setMinimumWidth(96)
        recent.clicked.connect(self._map_set_recent)
        cg.addWidget(recent, 1, 2)
        left.addWidget(cycle_box)

        area_box = QGroupBox("Map area")
        ab = QVBoxLayout(area_box)
        self._area_combo = QComboBox()
        for name in MAP_AREAS:
            self._area_combo.addItem(name)
        self._area_combo.currentTextChanged.connect(
            lambda name: self._map.set_area(name))
        ab.addWidget(self._area_combo)
        zoom_row = QHBoxLayout()
        zin = QToolButton(); zin.setText("\u2212")   # minus: zoom out
        zin.setToolTip("Zoom out")
        zin.clicked.connect(lambda: self._map.zoom(1.25))
        zout = QToolButton(); zout.setText("+")       # plus: zoom in
        zout.setToolTip("Zoom in")
        zout.clicked.connect(lambda: self._map.zoom(0.8))
        zreset = QToolButton(); zreset.setText("Reset view")
        zreset.clicked.connect(lambda: self._map.reset_view())
        zoom_row.addWidget(zin)
        zoom_row.addWidget(zout)
        zoom_row.addWidget(zreset)
        zoom_row.addStretch(1)
        ab.addLayout(zoom_row)
        left.addWidget(area_box)

        self._map_sel_lbl = QLabel("No station selected")
        self._map_sel_lbl.setWordWrap(True)
        self._map_sel_lbl.setStyleSheet("font-weight: bold;")
        left.addWidget(self._map_sel_lbl)

        avail_box = QGroupBox("Availability")
        avail_box.setMinimumHeight(88)
        avb = QVBoxLayout(avail_box)
        avb.setContentsMargins(10, 10, 10, 10)
        self._map_avail = _AvailabilityIndicator()
        avb.addWidget(self._map_avail)
        left.addWidget(avail_box)

        # Re-probe when the requested cycle changes for the current selection.
        self._map_date.dateChanged.connect(self._map_recheck_availability)
        self._map_cycle.currentIndexChanged.connect(
            self._map_recheck_availability)

        left.addStretch(1)

        self._map_gen_btn = QPushButton("Generate Sounding")
        self._map_gen_btn.setMinimumHeight(36)
        self._map_gen_btn.setEnabled(False)
        self._map_gen_btn.clicked.connect(self._map_generate)
        left.addWidget(self._map_gen_btn)

        hint = QLabel("Click a station dot to select \u2014 double-click to "
                      "open. Scroll to zoom, drag to pan.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        left.addWidget(hint)

        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setMinimumWidth(PICKER_RAIL_MIN_WIDTH)
        left_w.setMaximumWidth(PICKER_RAIL_MAX_WIDTH)
        left_w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        outer.addWidget(left_w)

        # --- the map itself ---
        self._map = StationMapWidget(self._all_stations)
        self._map.stationSelected.connect(self._map_on_select)
        self._map.stationActivated.connect(self._map_on_activate)
        outer.addWidget(self._map, stretch=1)

        self._map_selected_id: str | None = None
        return w

    def _map_set_recent(self) -> None:
        d, h = _most_recent_synoptic()
        self._map_date.setDate(d)
        self._map_cycle.setCurrentIndex(SYNOPTIC_HOURS.index(h))

    def _map_when(self) -> datetime:
        d = self._map_date.date()
        h = int(self._map_cycle.currentData())
        return datetime(d.year(), d.month(), d.day(), h, 0)

    def _map_on_select(self, sid: str) -> None:
        self._map_selected_id = sid
        st = next((s for s in self._all_stations if s["id"] == sid), None)
        if st is not None:
            self._map_sel_lbl.setText(
                f"{st['id']} \u2014 {st['name']}\n"
                f"({st['lat']:.2f}, {st['lon']:.2f})")
        else:
            self._map_sel_lbl.setText(sid)
        self._map_gen_btn.setEnabled(
            not (self._worker is not None and self._worker.isRunning()))
        self._queue_availability(sid, self._map_when(), self._map_avail)

    def _map_recheck_availability(self) -> None:
        # Refresh the datetime-aware station set for the newly chosen cycle,
        # then re-probe availability for the current selection.
        self._refresh_station_catalog(self._map_when())
        self._queue_availability(
            self._map_selected_id, self._map_when(), self._map_avail)

    def _map_on_activate(self, sid: str) -> None:
        self._map_on_select(sid)
        self._map_generate()

    def _map_generate(self) -> None:
        if not self._map_selected_id:
            QMessageBox.warning(self, APP_NAME,
                                "Click a station on the map first.")
            return
        self._start_fetch(self._map_selected_id, self._map_when())

    # ====================================================================== #
    # Observed (UWyo) list tab
    # ====================================================================== #
    def _build_uwyo_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        help_lbl = QLabel("Fetch an observed radiosonde sounding from the "
                          "University of Wyoming archive.")
        help_lbl.setWordWrap(True)
        layout.addWidget(help_lbl)

        # --- Station filter + list ---
        self._uwyo_search = QLineEdit()
        self._uwyo_search.setClearButtonEnabled(True)
        self._uwyo_search.setPlaceholderText(
            "Type to filter \u2014 station id or name (e.g. 72357, Norman, OUN)")
        # Live filtering: no button to press.
        self._uwyo_search.textChanged.connect(self._filter_stations)
        self._uwyo_search.returnPressed.connect(self._focus_first_station)
        layout.addWidget(self._uwyo_search)

        self._station_list = QListWidget()
        self._station_list.setAlternatingRowColors(True)
        self._station_list.itemSelectionChanged.connect(self._sync_fetch_enabled)
        self._station_list.itemDoubleClicked.connect(
            lambda _item: self._fetch_selected())
        layout.addWidget(self._station_list, stretch=1)

        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("color: gray;")
        layout.addWidget(self._count_lbl)

        # --- Observation time ---
        time_box = QGroupBox("Observation time (UTC)")
        tg = QGridLayout(time_box)
        tg.setColumnStretch(1, 1)

        default_date, default_hour = _most_recent_synoptic()

        tg.addWidget(QLabel("Date:"), 0, 0)
        self._date_edit = QDateEdit()
        self._date_edit.setDisplayFormat("yyyy-MM-dd")
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDate(default_date)
        self._date_edit.setMaximumDate(QDate.currentDate().addDays(1))
        self._date_edit.setMinimumWidth(118)
        self._date_edit.dateChanged.connect(self._update_valid_label)
        tg.addWidget(self._date_edit, 0, 1)

        tg.addWidget(QLabel("Cycle:"), 1, 0)
        self._cycle_combo = QComboBox()
        for h in SYNOPTIC_HOURS:
            self._cycle_combo.addItem(f"{h:02d}Z", h)
        self._cycle_combo.setCurrentIndex(SYNOPTIC_HOURS.index(default_hour))
        self._cycle_combo.setMinimumWidth(72)
        self._cycle_combo.currentIndexChanged.connect(self._update_valid_label)
        tg.addWidget(self._cycle_combo, 1, 1)

        recent_btn = QToolButton()
        recent_btn.setText("Most recent")
        recent_btn.setMinimumWidth(96)
        recent_btn.clicked.connect(self._set_most_recent)
        tg.addWidget(recent_btn, 1, 2)

        self._valid_lbl = QLabel("")
        self._valid_lbl.setStyleSheet("font-weight: bold;")
        tg.addWidget(self._valid_lbl, 2, 0, 1, 3)
        layout.addWidget(time_box)

        # --- Availability pre-flight ---
        avail_row = QHBoxLayout()
        self._uwyo_avail = _AvailabilityIndicator()
        avail_row.addWidget(self._uwyo_avail, 1)
        layout.addLayout(avail_row)

        # --- Primary action ---
        self._fetch_btn = QPushButton("Fetch && Display Sounding")
        self._fetch_btn.setDefault(True)
        self._fetch_btn.setMinimumHeight(36)
        self._fetch_btn.clicked.connect(self._fetch_selected)
        layout.addWidget(self._fetch_btn)

        # Re-probe availability when the selection or requested cycle changes.
        self._station_list.itemSelectionChanged.connect(
            self._uwyo_recheck_availability)
        self._date_edit.dateChanged.connect(self._uwyo_recheck_availability)
        self._cycle_combo.currentIndexChanged.connect(
            self._uwyo_recheck_availability)
        # Reload the datetime-aware station set when the cycle changes.
        self._date_edit.dateChanged.connect(self._uwyo_refresh_catalog)
        self._cycle_combo.currentIndexChanged.connect(
            self._uwyo_refresh_catalog)

        # Populate the full catalogue now; live-filter narrows it.
        self._filter_stations("")
        self._update_valid_label()
        self._sync_fetch_enabled()
        return w

    def _uwyo_recheck_availability(self) -> None:
        self._queue_availability(
            self._selected_station_id(), self._selected_when(),
            self._uwyo_avail)

    def _uwyo_refresh_catalog(self) -> None:
        """Refresh the station set when the list tab's cycle changes."""
        self._refresh_station_catalog(self._selected_when())

    # -- station list -------------------------------------------------------- #
    def _filter_stations(self, text: str) -> None:
        query = (text or "").strip().casefold()
        if query:
            rows = [r for r in self._all_stations
                    if query in r["id"].casefold()
                    or query in r["name"].casefold()]
        else:
            rows = self._all_stations

        self._station_list.clear()
        for r in rows:
            label = (f"{r['id']}   {r['name']}   "
                     f"({r['lat']:.2f}, {r['lon']:.2f})")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, r["id"])
            self._station_list.addItem(item)

        total = len(self._all_stations)
        shown = len(rows)
        if query:
            self._count_lbl.setText(f"{shown} of {total} stations match")
        else:
            self._count_lbl.setText(f"{total} stations \u2014 type above to filter")
        self._sync_fetch_enabled()

    def _focus_first_station(self) -> None:
        """Enter in the filter box selects the first match (then Fetch works)."""
        if self._station_list.count() > 0 and \
                self._station_list.currentRow() < 0:
            self._station_list.setCurrentRow(0)
        self._station_list.setFocus()

    def _selected_station_id(self) -> str | None:
        item = self._station_list.currentItem()
        if item is None:
            return None
        sid = item.data(Qt.UserRole)
        return str(sid) if sid else None

    def _sync_fetch_enabled(self) -> None:
        busy = self._worker is not None and self._worker.isRunning()
        self._fetch_btn.setEnabled(
            not busy and self._selected_station_id() is not None)

    # -- time ---------------------------------------------------------------- #
    def _set_most_recent(self) -> None:
        d, h = _most_recent_synoptic()
        self._date_edit.setDate(d)
        self._cycle_combo.setCurrentIndex(SYNOPTIC_HOURS.index(h))
        self._update_valid_label()

    def _selected_when(self) -> datetime:
        d = self._date_edit.date()
        h = int(self._cycle_combo.currentData())
        return datetime(d.year(), d.month(), d.day(), h, 0)

    def _update_valid_label(self) -> None:
        when = self._selected_when()
        self._valid_lbl.setText(f"Valid: {when:%a %Y-%m-%d}  {when.hour:02d}Z")

    # -- fetch --------------------------------------------------------------- #
    def _fetch_selected(self) -> None:
        sid = self._selected_station_id()
        if not sid:
            QMessageBox.warning(self, APP_NAME,
                                "Select a station from the list first.")
            return
        self._start_fetch(sid, self._selected_when())

    def _start_fetch(self, sid: str, when: datetime) -> None:
        """Kick off a background UWyo fetch for ``sid`` at ``when`` (UTC)."""
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, APP_NAME,
                                    "A fetch is already in progress.")
            return
        _LOGGER.info("uwyo_fetch.start station=%s valid=%s", sid, when)
        self._settings.setValue("last_station", sid)

        self._set_busy(True)
        self.statusBar().showMessage(
            f"Fetching {sid} at {when:%Y-%m-%d %H}Z from UWyo\u2026")

        self._worker = _FetchWorker(sid, when, parent=self,
                                    station=self._station(sid))
        self._worker.finished_ok.connect(self._on_fetch_ok)
        self._worker.failed.connect(self._on_fetch_failed)
        self._worker.finished.connect(lambda: self._set_busy(False))
        self._worker.start()

    def _set_busy(self, busy: bool) -> None:
        _LOGGER.debug(
            "uwyo_fetch.ui_busy busy=%s worker=%s",
            busy, id(self._worker) if self._worker else None)
        if busy:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._fetch_btn.setEnabled(False)
            self._fetch_btn.setText("Fetching\u2026")
            if hasattr(self, "_map_gen_btn"):
                self._map_gen_btn.setEnabled(False)
                self._map_gen_btn.setText("Fetching\u2026")
        else:
            QApplication.restoreOverrideCursor()
            self._fetch_btn.setText("Fetch && Display Sounding")
            self._sync_fetch_enabled()
            if hasattr(self, "_map_gen_btn"):
                self._map_gen_btn.setText("Generate Sounding")
                self._map_gen_btn.setEnabled(bool(self._map_selected_id))

    def _on_fetch_ok(self, npz_path, meta, when) -> None:
        self.statusBar().showMessage(
            f"Rendering {meta.id} sounding\u2026 (this takes a moment)")
        # Force the status message to paint before the synchronous compose
        # (which briefly blocks the UI thread while the SPC window is built).
        QApplication.processEvents()
        try:
            R = _render()
            prof_col, stn_id = R.decode(npz_path)
            title = f"{APP_NAME} \u2014 {meta.id} {when:%Y-%m-%d %H}Z"
            self._show_sounding(prof_col, stn_id, title=title)
            self.statusBar().showMessage(f"Opened {meta.id} {when:%Y-%m-%d %H}Z")
            _LOGGER.info(
                "uwyo_fetch.displayed station=%s valid=%s", meta.id, when)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception(
                "uwyo_fetch.display_failed station=%s valid=%s", meta.id, when)
            QMessageBox.critical(self, APP_NAME,
                                 f"Fetched, but could not display:\n{exc}")
        finally:
            try:
                os.remove(npz_path)
            except OSError:
                pass

    def _on_fetch_failed(self, message: str) -> None:
        _LOGGER.error("uwyo_fetch.failed message=%s", message)
        self.statusBar().showMessage("Fetch failed")
        QMessageBox.critical(self, APP_NAME, message)

    # ====================================================================== #
    # Forecast model tab
    # ====================================================================== #
    def _build_model_tab(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(12)

        self._model_syncing_point = False
        self._model_map = PointMapWidget()
        self._model_map.pointSelected.connect(self._model_on_map_point)
        self._model_map.pointActivated.connect(
            lambda _lat, _lon: self._model_fetch())

        left = QVBoxLayout()
        left.setSpacing(8)
        left.setContentsMargins(0, 0, 0, 0)

        area_box = QGroupBox("Region")
        area_layout = QVBoxLayout(area_box)
        self._model_area_combo = QComboBox()
        for name in MAP_AREAS:
            self._model_area_combo.addItem(name)
        self._model_area_combo.currentTextChanged.connect(
            self._model_area_changed)
        area_layout.addWidget(self._model_area_combo)
        zoom_row = QHBoxLayout()
        zoom_out = QToolButton(); zoom_out.setText("\u2212")
        zoom_out.setToolTip("Zoom out")
        zoom_out.clicked.connect(lambda: self._model_map.zoom(1.25))
        zoom_in = QToolButton(); zoom_in.setText("+")
        zoom_in.setToolTip("Zoom in")
        zoom_in.clicked.connect(lambda: self._model_map.zoom(0.8))
        zoom_reset = QToolButton(); zoom_reset.setText("Reset")
        zoom_reset.clicked.connect(lambda: self._model_map.reset_view())
        zoom_row.addWidget(zoom_out)
        zoom_row.addWidget(zoom_in)
        zoom_row.addWidget(zoom_reset)
        zoom_row.addStretch(1)
        area_layout.addLayout(zoom_row)
        left.addWidget(area_box)

        model_box = QGroupBox("Model")
        model_layout = QVBoxLayout(model_box)
        self._model_combo = QComboBox()
        self._model_combo.currentIndexChanged.connect(self._model_update_cycles)
        model_layout.addWidget(self._model_combo)
        self._model_notes = QLabel("")
        self._model_notes.setWordWrap(True)
        self._model_notes.setStyleSheet("color: gray;")
        model_layout.addWidget(self._model_notes)
        left.addWidget(model_box)

        time_box = QGroupBox("Run / valid time (UTC)")
        time_box.setMinimumHeight(210)
        time_grid = QGridLayout(time_box)
        time_grid.setVerticalSpacing(8)
        time_grid.setColumnStretch(1, 1)
        for row in range(3):
            time_grid.setRowMinimumHeight(row, 32)
        time_grid.addWidget(QLabel("Date:"), 0, 0)
        self._model_date = QDateEdit()
        self._model_date.setDisplayFormat("yyyy-MM-dd")
        self._model_date.setCalendarPopup(True)
        self._model_date.setDate(QDate.currentDate())
        self._model_date.setMaximumDate(QDate.currentDate().addDays(1))
        self._model_date.setMinimumWidth(132)
        self._model_date.setMinimumHeight(30)
        self._model_date.dateChanged.connect(self._model_update_valid_label)
        time_grid.addWidget(self._model_date, 0, 1)
        time_grid.addWidget(QLabel("Cycle:"), 1, 0)
        self._model_cycle = QComboBox()
        self._model_cycle.setMinimumWidth(132)
        self._model_cycle.setMinimumHeight(30)
        self._model_cycle.currentIndexChanged.connect(self._model_update_fxx)
        time_grid.addWidget(self._model_cycle, 1, 1)
        recent = QToolButton()
        recent.setText("Most recent")
        recent.setMinimumWidth(96)
        recent.setMinimumHeight(30)
        recent.clicked.connect(self._model_set_recent)
        time_grid.addWidget(recent, 1, 2)
        time_grid.addWidget(QLabel("Forecast:"), 2, 0)
        self._model_fxx_combo = QComboBox()
        self._model_fxx_combo.setMaxVisibleItems(24)
        self._model_fxx_combo.setMinimumWidth(132)
        self._model_fxx_combo.setMinimumHeight(30)
        self._model_fxx_combo.currentIndexChanged.connect(
            self._model_update_valid_label)
        time_grid.addWidget(self._model_fxx_combo, 2, 1, 1, 2)
        self._model_valid_lbl = QLabel("")
        self._model_valid_lbl.setStyleSheet("font-weight: bold;")
        time_grid.addWidget(self._model_valid_lbl, 3, 0, 1, 3)
        self._model_availability = _AvailabilityIndicator()
        self._model_availability.setToolTip(
            "Catalog check only; Fetch remains available if this is uncertain")
        time_grid.addWidget(self._model_availability, 4, 0, 1, 3)
        self._model_use_available_btn = QPushButton("Use available cycle")
        self._model_use_available_btn.clicked.connect(
            self._use_model_available_run)
        self._model_use_available_btn.hide()
        time_grid.addWidget(self._model_use_available_btn, 5, 0, 1, 3)
        left.addWidget(time_box)

        point_box = QGroupBox("Point")
        point_grid = QGridLayout(point_box)
        point_grid.setColumnStretch(1, 1)
        point_grid.addWidget(QLabel("Latitude:"), 0, 0)
        self._model_lat = QDoubleSpinBox()
        self._model_lat.setRange(-90.0, 90.0)
        self._model_lat.setDecimals(4)
        self._model_lat.setSingleStep(0.25)
        self._model_lat.setValue(35.6300)
        self._model_lat.valueChanged.connect(
            lambda _value: self._model_point_from_spins())
        point_grid.addWidget(self._model_lat, 0, 1)
        point_grid.addWidget(QLabel("Longitude:"), 1, 0)
        self._model_lon = QDoubleSpinBox()
        self._model_lon.setRange(-180.0, 180.0)
        self._model_lon.setDecimals(4)
        self._model_lon.setSingleStep(0.25)
        self._model_lon.setValue(-97.4400)
        self._model_lon.valueChanged.connect(
            lambda _value: self._model_point_from_spins())
        point_grid.addWidget(self._model_lon, 1, 1)
        center = QToolButton()
        center.setText("Center")
        center.clicked.connect(lambda: self._model_map.set_point(
            self._model_lat.value(), self._model_lon.value(), center=True))
        point_grid.addWidget(center, 0, 2, 2, 1)
        self._model_point_status = QLabel("")
        self._model_point_status.setWordWrap(True)
        self._model_point_status.setStyleSheet("color: gray;")
        point_grid.addWidget(self._model_point_status, 2, 0, 1, 3)
        left.addWidget(point_box)

        member_box = QGroupBox("Member")
        member_layout = QVBoxLayout(member_box)
        self._model_member = QLineEdit()
        self._model_member.textChanged.connect(
            self._queue_model_availability)
        member_layout.addWidget(self._model_member)
        left.addWidget(member_box)

        fetch_row = QHBoxLayout()
        self._model_fetch_btn = QPushButton("Fetch && Display Forecast Sounding")
        self._model_fetch_btn.setMinimumHeight(36)
        self._model_fetch_btn.clicked.connect(self._model_fetch)
        fetch_row.addWidget(self._model_fetch_btn, 1)
        self._model_cancel_btn = QPushButton("Cancel")
        self._model_cancel_btn.setMinimumHeight(36)
        self._model_cancel_btn.clicked.connect(self._cancel_model_fetch)
        self._model_cancel_btn.hide()
        fetch_row.addWidget(self._model_cancel_btn)
        left.addLayout(fetch_row)

        self._model_progress = QProgressBar()
        self._model_progress.setMinimumHeight(18)
        self._model_progress.setTextVisible(True)
        self._model_progress.hide()
        left.addWidget(self._model_progress)
        self._model_progress_detail = QLabel("")
        self._model_progress_detail.setWordWrap(True)
        self._model_progress_detail.setStyleSheet("color: #aeb8c8;")
        self._model_progress_detail.hide()
        left.addWidget(self._model_progress_detail)

        unsupported = QLabel(self._model_unsupported_text())
        unsupported.setWordWrap(True)
        unsupported.setStyleSheet("color: gray;")
        left.addWidget(unsupported)
        left.addStretch(1)

        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setMinimumWidth(PICKER_RAIL_MIN_WIDTH)
        left_w.setMaximumWidth(PICKER_RAIL_MAX_WIDTH)
        left_w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        outer.addWidget(left_w)
        outer.addWidget(self._model_map, stretch=1)

        self._model_area_changed(self._model_area_combo.currentText())
        self._model_set_recent()
        self._model_point_from_spins(center=True)
        return w

    def _model_unsupported_text(self) -> str:
        try:
            from sharpmod.tools import model_extract
            unsupported = model_extract.unsupported_models()
        except Exception:
            return ""
        names = ", ".join(sorted(unsupported))
        return "Known but not selectable yet: " + names

    def _model_area_changed(self, name: str) -> None:
        if hasattr(self, "_model_map"):
            self._model_map.set_area(name)
        self._model_populate_models()

    def _model_populate_models(self) -> None:
        if not hasattr(self, "_model_combo"):
            return
        previous = self._model_combo.currentData()
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        try:
            from sharpmod.tools import model_extract
            area = self._model_area_combo.currentText() \
                if hasattr(self, "_model_area_combo") else "United States (CONUS)"
            area_bounds = MAP_AREAS.get(area, MAP_AREAS["United States (CONUS)"])
            regional = area in {
                "United States (CONUS)", "North America", "Caribbean / Gulf"}
            configs = []
            for cfg in model_extract.available_models():
                if regional:
                    allowed = model_extract.domain_intersects_bounds(
                        cfg, area_bounds)
                else:
                    allowed = model_extract.domain_contains_bounds(
                        cfg, area_bounds)
                if allowed:
                    configs.append(cfg)
            for cfg in configs:
                self._model_combo.addItem(cfg.label, cfg.key)
            if not configs:
                self._model_combo.addItem("No public models for this region", "")
                self._model_combo.setEnabled(False)
            else:
                self._model_combo.setEnabled(True)
                idx = self._model_combo.findData(previous)
                self._model_combo.setCurrentIndex(idx if idx >= 0 else 0)
        except Exception:
            self._model_combo.addItem("Forecast models unavailable", "")
            self._model_combo.setEnabled(False)
        finally:
            self._model_combo.blockSignals(False)
        self._model_update_cycles()

    def _model_config(self):
        key = self._model_combo.currentData()
        if not key:
            return None
        from sharpmod.tools import model_extract
        return model_extract.get_config(key)

    def _model_update_cycles(self) -> None:
        if not hasattr(self, "_model_cycle") or not hasattr(self, "_model_notes"):
            return
        cfg = self._model_config()
        self._model_cycle.blockSignals(True)
        self._model_cycle.clear()
        if cfg is None:
            self._model_notes.setText("")
            self._model_cycle.blockSignals(False)
            self._model_update_fxx()
            self._model_update_fetch_state()
            return
        for hour in cfg.cycles:
            self._model_cycle.addItem(f"{hour:02d}Z", hour)
        now = datetime.now(timezone.utc)
        idx = 0
        for i, hour in enumerate(cfg.cycles):
            if hour <= now.hour:
                idx = i
        self._model_cycle.setCurrentIndex(idx)
        self._model_cycle.blockSignals(False)

        self._model_notes.setText(
            f"{cfg.notes}\nDomain: {cfg.domain}")
        if hasattr(self, "_model_map"):
            self._model_map.set_domain(
                cfg.domain_bounds, f"{cfg.label} domain: {cfg.domain}")
        ensemble = cfg.key in {"gefs", "cfs"}
        self._model_member.setEnabled(ensemble)
        if ensemble:
            if cfg.key == "gefs":
                self._model_member.setPlaceholderText("default c00; e.g. p01")
            else:
                self._model_member.setPlaceholderText("default 1")
        else:
            self._model_member.clear()
            self._model_member.setPlaceholderText("deterministic")
        self._model_update_fxx()
        self._model_update_fetch_state()

    def _model_update_fxx(self) -> None:
        if not hasattr(self, "_model_fxx_combo"):
            return
        cfg = self._model_config()
        current = self._model_fxx_combo.currentData()
        self._model_fxx_combo.blockSignals(True)
        self._model_fxx_combo.clear()
        if cfg is not None:
            from sharpmod.tools import model_extract
            cycle = int(self._model_cycle.currentData() or 0)
            for hour in model_extract.forecast_hours(cfg, cycle_hour=cycle):
                self._model_fxx_combo.addItem(f"F{int(hour):03d}", int(hour))
            idx = self._model_fxx_combo.findData(current)
            if idx < 0:
                idx = self._model_fxx_combo.findData(cfg.default_fxx)
            self._model_fxx_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._model_fxx_combo.blockSignals(False)
        self._model_update_valid_label()

    def _model_run_time(self) -> datetime:
        d = self._model_date.date()
        h = int(self._model_cycle.currentData() or 0)
        return datetime(d.year(), d.month(), d.day(), h, 0, tzinfo=timezone.utc)

    def _model_selected_fxx(self) -> int:
        return int(self._model_fxx_combo.currentData() or 0)

    def _model_set_recent(self) -> None:
        cfg = self._model_config()
        cycles = tuple(sorted(cfg.cycles if cfg is not None else SYNOPTIC_HOURS))
        now = datetime.now(timezone.utc)
        day = now
        eligible = [h for h in cycles if h <= now.hour]
        if eligible:
            hour = eligible[-1]
        else:
            day = now - timedelta(days=1)
            hour = cycles[-1]
        self._model_date.setDate(QDate(day.year, day.month, day.day))
        idx = self._model_cycle.findData(hour)
        if idx >= 0:
            self._model_cycle.setCurrentIndex(idx)
        self._model_update_valid_label()

    def _model_update_valid_label(self) -> None:
        if not hasattr(self, "_model_valid_lbl"):
            return
        run = self._model_run_time()
        fxx = self._model_selected_fxx()
        valid = run + timedelta(hours=fxx)
        self._model_valid_lbl.setText(
            f"Run {run:%Y-%m-%d %H}Z  \u2192  Valid {valid:%Y-%m-%d %H}Z")
        if hasattr(self, "_model_availability"):
            self._queue_model_availability()

    def _model_member_value(self) -> str | None:
        if not hasattr(self, "_model_member") \
                or not self._model_member.isEnabled():
            return None
        return self._model_member.text().strip() or None

    def _queue_model_availability(self, *_args) -> None:
        """Debounce a catalog probe for the exact current picker selection."""
        if not hasattr(self, "_model_availability_timer") \
                or not hasattr(self, "_model_availability"):
            return
        self._model_availability_token += 1
        self._model_availability_timer.stop()
        self._model_available_run = None
        self._model_use_available_btn.hide()
        cfg = self._model_config()
        if cfg is None or not hasattr(self, "_model_fxx_combo"):
            self._model_availability_request = None
            self._model_availability.set_status(AVAIL_UNKNOWN)
            return
        request = (
            cfg.key,
            self._model_run_time(),
            self._model_selected_fxx(),
            self._model_member_value(),
        )
        self._model_availability_request = request
        self._model_availability.set_status(
            AVAIL_CHECKING, "Checking selected and recent cycles\u2026")
        self._model_availability_timer.start()

    def _run_model_availability(self) -> None:
        request = self._model_availability_request
        if request is None:
            return

        # Resolve the native GRIB boundary on the GUI thread before the probe
        # worker imports Herbie/ecCodes.  On Windows, first loading an
        # incompatible native library from QThread can terminate the process
        # instead of raising an ordinary Python exception.
        from sharpmod.tools import model_extract
        try:
            model_extract.require_runtime_dependencies()
        except model_extract.RetrievalError:
            _LOGGER.exception("model_availability.runtime_unavailable")
            self._model_availability.set_status(
                AVAIL_UNKNOWN,
                "Forecast model runtime unavailable; Fetch remains available",
            )
            return

        model, run_time, fxx, member = request
        token = self._model_availability_token
        worker = _ModelAvailabilityWorker(
            model, run_time, fxx, member, token, parent=self)
        self._model_availability_workers.append(worker)
        worker.checked.connect(self._on_model_availability_checked)
        worker.finished.connect(self._on_model_availability_finished)
        worker.start()

    def _on_model_availability_checked(
            self, token: int, model: str, run_time: datetime, fxx: int,
            member: str | None, status: str, message: str,
            available_run: datetime | None) -> None:
        request = (model, run_time, int(fxx), member or None)
        if token != self._model_availability_token \
                or request != self._model_availability_request:
            _LOGGER.debug(
                "model_availability.stale token=%s current=%s request=%s",
                token, self._model_availability_token, request)
            return
        self._model_availability.set_status(status, message)
        if status == AVAIL_FALLBACK and available_run is not None:
            self._model_available_run = available_run
            self._model_use_available_btn.setText(
                f"Use available cycle ({available_run:%Y-%m-%d %H}Z)")
            self._model_use_available_btn.show()
        else:
            self._model_available_run = None
            self._model_use_available_btn.hide()

    def _on_model_availability_finished(self) -> None:
        worker = self.sender()
        try:
            self._model_availability_workers.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()

    def _use_model_available_run(self) -> None:
        """Apply the offered fallback only after the user explicitly opts in."""
        run_time = self._model_available_run
        if run_time is None:
            return
        self._model_date.setDate(QDate(
            run_time.year, run_time.month, run_time.day))
        index = self._model_cycle.findData(run_time.hour)
        if index >= 0:
            self._model_cycle.setCurrentIndex(index)

    def _model_point_from_spins(self, center: bool = False) -> None:
        if getattr(self, "_model_syncing_point", False):
            return
        if hasattr(self, "_model_map"):
            self._model_map.set_point(
                float(self._model_lat.value()), float(self._model_lon.value()),
                center=center)
        self._model_update_fetch_state()

    def _model_on_map_point(self, lat: float, lon: float) -> None:
        self._model_syncing_point = True
        try:
            self._model_lat.setValue(float(lat))
            self._model_lon.setValue(float(lon))
        finally:
            self._model_syncing_point = False
        self._model_update_fetch_state()

    def _model_point_ok(self) -> bool:
        cfg = self._model_config()
        if cfg is None:
            return False
        from sharpmod.tools import model_extract
        return model_extract.point_in_domain(
            cfg, self._model_lat.value(), self._model_lon.value())

    def _model_update_fetch_state(self) -> None:
        if not hasattr(self, "_model_fetch_btn") \
                or not hasattr(self, "_model_point_status"):
            return
        cfg = self._model_config()
        busy = self._model_worker is not None
        if cfg is None:
            self._model_point_status.setText("")
            self._model_fetch_btn.setEnabled(False)
            return
        lat = float(self._model_lat.value())
        lon = float(self._model_lon.value())
        ok = self._model_point_ok()
        _LOGGER.debug(
            "model_fetch.ui_state model=%s point_ok=%s busy=%s lat=%.4f "
            "lon=%.4f",
            cfg.key, ok, busy, lat, lon)
        if ok:
            self._model_point_status.setText(
                f"Selected {lat:.4f}, {lon:.4f} inside {cfg.domain}")
        else:
            self._model_point_status.setText(
                f"Selected {lat:.4f}, {lon:.4f} is outside {cfg.label} "
                f"{cfg.domain} coverage")
        self._model_fetch_btn.setEnabled(ok and not busy)

    def _model_fetch(self) -> None:
        cfg = self._model_config()
        if cfg is None:
            QMessageBox.warning(self, APP_NAME, "Choose a forecast model first.")
            return
        if self._model_worker is not None:
            QMessageBox.information(self, APP_NAME,
                                    "A model fetch is already in progress.")
            return
        lat = float(self._model_lat.value())
        lon = float(self._model_lon.value())
        if not self._model_point_ok():
            QMessageBox.warning(
                self, APP_NAME,
                f"{cfg.label} does not cover {lat:.4f}, {lon:.4f}.")
            return

        from sharpmod.tools import model_extract
        # Keep native GRIB imports on the main Qt thread. The HRRR Zarr path
        # itself does not need ecCodes, but its automatic fallback does, and a
        # first native import from a Windows QThread can terminate the process.
        try:
            model_extract.require_runtime_dependencies()
        except model_extract.RetrievalError as exc:
            _LOGGER.exception("model_fetch.runtime_unavailable")
            self.statusBar().showMessage(
                "Forecast model support unavailable")
            QMessageBox.critical(
                self, APP_NAME,
                f"Forecast model support is unavailable:\n{exc}")
            return

        self._cancel_model_prefetch(wait=True)
        fxx = self._model_selected_fxx()
        run_time = self._model_run_time()
        member = self._model_member_value()
        loc = f"{cfg.label} {lat:.2f}, {lon:.2f}"

        download_dir = tempfile.mkdtemp(
            prefix=f"model_{cfg.key.replace('-', '_')}_{run_time:%Y%m%d%H}_"
                   f"f{fxx:03d}_")
        npz_path = os.path.join(download_dir, "sounding.npz")

        _LOGGER.info(
            "model_fetch.start model=%s run=%s fxx=%03d lat=%.4f lon=%.4f "
            "download_dir=%s",
            cfg.key, run_time, fxx, lat, lon, download_dir)

        self._set_model_busy(True)
        self.statusBar().showMessage(
            f"Fetching {cfg.label} F{fxx:03d} at {lat:.2f}, {lon:.2f}\u2026")
        worker = _ModelFetchWorker(
            cfg.key, lat, lon, run_time, fxx, npz_path, loc=loc,
            member=member, download_dir=download_dir,
            model_hour_cache=self._model_hour_cache, parent=self)
        self._model_worker = worker
        worker.finished_ok.connect(self._on_model_fetch_ok)
        worker.failed.connect(self._on_model_fetch_failed)
        worker.cancelled.connect(self._on_model_fetch_cancelled)
        worker.progress.connect(self._on_model_fetch_progress)
        worker.finished.connect(self._on_model_fetch_finished)
        worker.start()

    def _shutdown_model_cache(self) -> None:
        """Close decoded hours, release disk leases, and enforce cache limits."""
        self._cancel_model_prefetch(wait=True)
        if self._model_worker is not None:
            self._model_worker.requestInterruption()
            self._model_worker.wait(5000)
        self._model_hour_cache.clear()
        try:
            self._model_disk_cache.prune()
        except OSError:
            _LOGGER.exception("model_disk_cache.shutdown_prune_failed")

    def _on_model_fetch_finished(self) -> None:
        """Release a completed worker before enabling the next request."""
        worker = self.sender()
        if self._model_worker is not worker:
            _LOGGER.warning(
                "model_fetch.finished_stale worker=%s current=%s",
                id(worker), id(self._model_worker))
            worker.deleteLater()
            return
        _LOGGER.info("model_fetch.finished worker=%s", id(worker))
        self._model_worker = None
        try:
            self._set_model_busy(False)
        finally:
            worker.deleteLater()

    def _set_model_busy(self, busy: bool) -> None:
        _LOGGER.debug(
            "model_fetch.ui_busy busy=%s worker=%s",
            busy, id(self._model_worker) if self._model_worker else None)
        if busy:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._model_fetch_btn.setEnabled(False)
            self._model_cancel_btn.setEnabled(True)
            self._model_cancel_btn.setText("Cancel")
            self._model_cancel_btn.show()
            self._model_fetch_btn.setText("Fetching\u2026")
            self._model_progress_stage = "locating"
            self._model_progress_total = 0
            self._model_progress_started = time.monotonic()
            self._model_progress.setRange(0, 0)
            self._model_progress.setFormat("")
            self._model_progress.show()
            self._model_progress_detail.setText("Locating model run\u2026")
            self._model_progress_detail.show()
            self._model_progress_timer.start()
        else:
            QApplication.restoreOverrideCursor()
            self._model_progress_timer.stop()
            self._model_progress.hide()
            self._model_progress_detail.hide()
            self._model_progress_stage = ""
            self._model_progress_total = 0
            self._model_cancel_btn.hide()
            self._model_fetch_btn.setText("Fetch && Display Forecast Sounding")
            self._model_update_fetch_state()

    def _on_model_fetch_progress(self, stage: str, total_bytes: int) -> None:
        """Switch the visible model-fetch progress to a real worker stage."""
        stage = str(stage)
        total_bytes = max(0, int(total_bytes or 0))
        self._model_progress_stage = stage
        if total_bytes:
            self._model_progress_total = total_bytes
        self._model_progress.show()
        self._model_progress_detail.show()
        _LOGGER.info(
            "model_fetch.progress stage=%s total_bytes=%d",
            stage, self._model_progress_total)

        if stage == "downloading":
            if self._model_progress_total:
                self._model_progress.setRange(0, 100)
                self._model_progress.setValue(0)
                self._model_progress.setFormat("0%")
            else:
                self._model_progress.setRange(0, 0)
                self._model_progress.setFormat("")
            self._poll_model_fetch_progress()
            return

        messages = {
            "locating": ("Locating model run\u2026", "Locating\u2026"),
            "decoding": ("Decoding downloaded GRIB fields\u2026", "Decoding\u2026"),
            "cached": ("Using cached model hour\u2026", "Extracting\u2026"),
            "extracting": ("Extracting the nearest grid point\u2026", "Extracting\u2026"),
            "writing": ("Writing the point sounding\u2026", "Writing\u2026"),
            "complete": ("Preparing the sounding display\u2026", "Preparing\u2026"),
            "rendering": ("Rendering the sounding window\u2026", "Rendering\u2026"),
        }
        detail, button = messages.get(
            stage, ("Processing forecast data\u2026", "Processing\u2026"))
        self._model_progress.setRange(0, 0)
        self._model_progress.setFormat("")
        self._model_progress_detail.setText(detail)
        self._model_fetch_btn.setText(button)
        self.statusBar().showMessage(detail)

    def _poll_model_fetch_progress(self) -> None:
        """Update download percentage from bytes in the isolated GRIB tree."""
        if self._model_progress_stage != "downloading":
            return
        worker = self._model_worker
        if worker is None:
            return
        downloaded = 0
        try:
            for root, _dirs, files in os.walk(worker._download_dir):
                for filename in files:
                    if filename.lower().endswith(
                            (".grib2", ".grib", ".grb2", ".grb", ".part")):
                        try:
                            downloaded += os.path.getsize(
                                os.path.join(root, filename))
                        except OSError:
                            pass
        except OSError:
            pass

        total = self._model_progress_total
        elapsed = max(0.001, time.monotonic() - self._model_progress_started)
        rate = downloaded / elapsed
        model_label = worker._model.upper()
        if total > 0:
            percent = min(100, max(0, int(downloaded * 100 / total)))
            self._model_progress.setRange(0, 100)
            self._model_progress.setValue(percent)
            self._model_progress.setFormat(f"{percent}%")
            detail = (
                f"{_format_progress_bytes(downloaded)} / "
                f"{_format_progress_bytes(total)}")
            if rate > 0 and downloaded < total:
                remaining = (total - downloaded) / rate
                detail += (
                    f" \u2022 {_format_progress_bytes(rate)}/s"
                    f" \u2022 ~{_format_progress_duration(remaining)} left")
            self._model_fetch_btn.setText(f"Downloading\u2026 {percent}%")
            status = f"Downloading {model_label}: {percent}% \u2014 {detail}"
        else:
            self._model_progress.setRange(0, 0)
            self._model_progress.setFormat("")
            detail = _format_progress_bytes(downloaded)
            if rate > 0:
                detail += f" \u2022 {_format_progress_bytes(rate)}/s"
            self._model_fetch_btn.setText("Downloading\u2026")
            status = f"Downloading {model_label}: {detail}"
        self._model_progress_detail.setText(detail)
        self.statusBar().showMessage(status)

    def _on_model_fetch_ok(self, npz_path, label, run_time, fxx) -> None:
        worker = self.sender()
        self._on_model_fetch_progress("rendering", self._model_progress_total)
        self.statusBar().showMessage(f"Rendering {label} F{int(fxx):03d}\u2026")
        QApplication.processEvents()
        try:
            R = _render()
            prof_col, stn_id = R.decode(npz_path)
            title = (
                f"{APP_NAME} \u2014 {label} "
                f"{run_time:%Y-%m-%d %H}Z F{int(fxx):03d}")
            win = self._show_sounding(prof_col, stn_id, title=title)
            _retain_model_data_until_close(
                win, npz_path, os.path.dirname(npz_path))
            self.statusBar().showMessage(
                f"Opened {label} {run_time:%Y-%m-%d %H}Z F{int(fxx):03d}")
            _LOGGER.info(
                "model_fetch.displayed label=%s run=%s fxx=%03d viewer=%s",
                label, run_time, int(fxx), id(win))
            if isinstance(worker, _ModelFetchWorker):
                request = (
                    worker._model, worker._lat, worker._lon, run_time,
                    int(fxx), worker._member,
                )
                QTimer.singleShot(
                    0, lambda request=request: self._start_model_prefetch(
                        *request
                    )
                )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception(
                "model_fetch.display_failed label=%s run=%s fxx=%03d",
                label, run_time, int(fxx))
            _cleanup_model_data(npz_path, os.path.dirname(npz_path))
            QMessageBox.critical(self, APP_NAME,
                                 f"Fetched, but could not display:\n{exc}")

    def _on_model_fetch_failed(self, message: str) -> None:
        _LOGGER.error("model_fetch.failed message=%s", message)
        self.statusBar().showMessage("Forecast model fetch failed")
        QMessageBox.critical(self, APP_NAME, message)

    def _cancel_model_fetch(self) -> None:
        worker = self._model_worker
        if worker is None:
            return
        worker.requestInterruption()
        self._model_cancel_btn.setEnabled(False)
        self._model_cancel_btn.setText("Cancelling…")
        self.statusBar().showMessage("Cancelling forecast-model fetch…")

    def _on_model_fetch_cancelled(self) -> None:
        _LOGGER.info("model_fetch.cancelled")
        self.statusBar().showMessage("Forecast-model fetch cancelled", 5000)

    def _start_model_prefetch(
        self, model, lat, lon, run_time, current_fxx, member,
    ) -> None:
        if not self._model_prefetch_enabled() or self._model_worker is not None:
            return
        if self._model_prefetch_worker is not None:
            return
        from sharpmod.tools import model_extract
        cfg = model_extract.get_config(model)
        hours = model_extract.forecast_hours(
            cfg.key, cycle_hour=run_time.hour
        )
        next_fxx = next(
            (int(value) for value in hours if int(value) > int(current_fxx)),
            None,
        )
        if next_fxx is None:
            return
        worker = _ModelPrefetchWorker(
            cfg.key, lat, lon, run_time, next_fxx, member,
            self._model_hour_cache, parent=self,
        )
        self._model_prefetch_worker = worker
        worker.ready.connect(self._on_model_prefetch_ready)
        worker.failed.connect(self._on_model_prefetch_failed)
        worker.finished.connect(self._on_model_prefetch_finished)
        worker.start()
        _LOGGER.info(
            "model_prefetch.start model=%s run=%s fxx=%03d",
            cfg.key, run_time, next_fxx,
        )

    def _cancel_model_prefetch(self, *, wait: bool) -> bool:
        worker = self._model_prefetch_worker
        if worker is None:
            return True
        worker.requestInterruption()
        if not wait:
            return False
        finished = worker.wait(5000)
        if finished and self._model_prefetch_worker is worker:
            self._model_prefetch_worker = None
            worker.deleteLater()
        return bool(finished)

    def _on_model_prefetch_ready(self, label, run_time, fxx) -> None:
        self.statusBar().showMessage(
            f"Prefetched {label} {run_time:%Y-%m-%d %H}Z F{int(fxx):03d}",
            4000,
        )

    def _on_model_prefetch_failed(self, message) -> None:
        _LOGGER.info("model_prefetch.unavailable reason=%s", message)

    def _on_model_prefetch_finished(self) -> None:
        worker = self.sender()
        if self._model_prefetch_worker is worker:
            self._model_prefetch_worker = None
        worker.deleteLater()

    def _clear_model_cache(self) -> None:
        if self._model_worker is not None:
            QMessageBox.information(
                self, APP_NAME,
                "Wait for the active model fetch to finish or cancel it first.",
            )
            return
        self._cancel_model_prefetch(wait=True)
        self._model_hour_cache.clear()
        removed = self._model_disk_cache.clear()
        noun = "entry" if len(removed) == 1 else "entries"
        self.statusBar().showMessage(
            f"Cleared {len(removed)} downloaded model cache {noun}", 5000
        )

    # ====================================================================== #
    # Open File tab
    # ====================================================================== #
    def _build_file_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        intro = QLabel(
            "Open a local sounding file \u2014 or drag one onto this window.\n"
            "Supported: .npz point soundings, SPC tabular, BUFKIT, PECAN, "
            "and WRF-ARW text.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        self._file_edit = QLineEdit()
        self._file_edit.setClearButtonEnabled(True)
        self._file_edit.setPlaceholderText("Path to a sounding file\u2026")
        self._file_edit.returnPressed.connect(self._open_from_edit)
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self._browse_file)
        row.addWidget(self._file_edit)
        row.addWidget(browse)
        layout.addLayout(row)

        open_btn = QPushButton("Open Sounding")
        open_btn.setMinimumHeight(32)
        open_btn.clicked.connect(self._open_from_edit)
        layout.addWidget(open_btn)

        recent_box = QGroupBox("Recent files")
        rv = QVBoxLayout(recent_box)
        self._recent_list = QListWidget()
        self._recent_list.itemDoubleClicked.connect(
            lambda item: self._open_file(item.data(Qt.UserRole)))
        rv.addWidget(self._recent_list)
        layout.addWidget(recent_box, stretch=1)
        return w

    def _browse_file(self) -> None:
        start = self._settings.value("last_dir", "", str)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Sounding File", start,
            "Soundings (*.npz *.txt *.buf);;All files (*.*)")
        if path:
            self._file_edit.setText(path)
            self._open_file(path)

    def _browse_and_open(self) -> None:
        self._browse_file()

    def _open_from_edit(self) -> None:
        path = self._file_edit.text().strip().strip('"')
        if not path:
            QMessageBox.warning(self, APP_NAME, "Choose a sounding file first.")
            return
        self._open_file(path)

    def _open_file(self, path: str) -> None:
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, APP_NAME, f"File not found:\n{path}")
            return
        self.statusBar().showMessage(f"Decoding {os.path.basename(path)}\u2026")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            QApplication.processEvents()
            R = _render()
            prof_col, stn_id = R.decode(path)
        except Exception as exc:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            self.statusBar().showMessage("Decode failed")
            QMessageBox.critical(
                self, APP_NAME,
                f"Could not decode this file:\n{path}\n\n{exc}")
            return
        try:
            self._show_sounding(
                prof_col, stn_id,
                title=f"{APP_NAME} \u2014 {os.path.basename(path)}")
            self._settings.setValue("last_dir", os.path.dirname(path))
            self._remember_recent_file(path)
        finally:
            QApplication.restoreOverrideCursor()
        self.statusBar().showMessage(f"Opened {os.path.basename(path)}")

    # -- recents ------------------------------------------------------------- #
    def _remember_recent_file(self, path: str) -> None:
        recents = list(self._settings.value("recent_files", [], list) or [])
        path = os.path.abspath(path)
        if path in recents:
            recents.remove(path)
        recents.insert(0, path)
        recents = recents[:MAX_RECENTS]
        self._settings.setValue("recent_files", recents)
        self._load_recent_files(recents)

    def _load_recent_files(self, recents=None) -> None:
        if recents is None:
            recents = list(self._settings.value("recent_files", [], list) or [])
        self._recent_list.clear()
        for p in recents:
            if not os.path.exists(p):
                continue
            item = QListWidgetItem(os.path.basename(p))
            item.setToolTip(p)
            item.setData(Qt.UserRole, p)
            self._recent_list.addItem(item)
        if self._recent_list.count() == 0:
            hint = QListWidgetItem("(no recent files yet)")
            hint.setFlags(Qt.NoItemFlags)
            self._recent_list.addItem(hint)

    # ====================================================================== #
    # Drag & drop (open a file dropped anywhere on the window)
    # ====================================================================== #
    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self._tabs.setCurrentIndex(1)  # show the Open File tab
                self._file_edit.setText(path)
                self._open_file(path)
                break

    # ====================================================================== #
    # Shared / lifecycle
    # ====================================================================== #
    def _show_sounding(self, prof_col, stn_id, title=None):
        self._prune_closed_viewers()
        if self._combine_soundings_enabled() and self._viewers:
            win = self._viewers[-1]
            win.addProfileCollection(
                prof_col,
                focus=True,
                check_integrity=False,
            )
            count = len(getattr(win.spc_widget, "prof_collections", []))
            win.setWindowTitle(
                f"{APP_NAME} — {count} Sounding{'s' if count != 1 else ''}")
            win.showNormal()
            win.raise_()
            win.activateWindow()
            _LOGGER.info(
                "viewer.profile_added viewer=%s title=%s soundings=%d",
                id(win), title or stn_id or "Sounding", count)
            return win

        # Compose the real, interactive SPCWindow with this picker as its Qt
        # parent/controller (so the W key refocuses the picker and Preferences
        # routes here). The window shows itself; we retain a reference.
        win = compose_interactive(self._config(), prof_col, self, stn_id=stn_id)
        if title:
            win.setWindowTitle(title)
        self._viewers.append(win)
        viewer_id = id(win)
        viewer_title = title or stn_id or "Sounding"
        win.destroyed.connect(
            lambda *_args, viewer_id=viewer_id, viewer_title=viewer_title:
            _LOGGER.info(
                "viewer.closed viewer=%s title=%s", viewer_id, viewer_title))
        _LOGGER.info(
            "viewer.opened viewer=%s title=%s active_viewers=%d",
            viewer_id, viewer_title, len(self._viewers))
        return win

    def _prune_closed_viewers(self) -> None:
        """Drop references to windows the user has closed."""
        before = len(self._viewers)
        alive = []
        for w in self._viewers:
            try:
                if w.isVisible():
                    alive.append(w)
            except RuntimeError:
                continue  # already deleted by Qt
        self._viewers = alive
        _LOGGER.debug(
            "viewer.prune before=%d after=%d", before, len(self._viewers))

    def _restore_state(self) -> None:
        self._load_recent_files()
        last = self._settings.value("last_station", "", str)
        if last:
            for i in range(self._station_list.count()):
                if self._station_list.item(i).data(Qt.UserRole) == last:
                    self._station_list.setCurrentRow(i)
                    self._station_list.scrollToItem(
                        self._station_list.item(i))
                    break
            # Mirror the selection onto the map and centre it there.
            if self._station(last) is not None:
                self._map.set_selected(last)
                self._map.center_on(last)
                self._map_on_select(last)

    def _station(self, sid):
        return next((s for s in self._all_stations if s["id"] == sid), None)


def _app_icon() -> QIcon:
    """Resolve the bundled application icon as a :class:`QIcon`.

    Prefers the multi-resolution ``app.ico`` and falls back to ``app.png``,
    resolved package-relative via :mod:`importlib.resources` so it works both
    from a source checkout and inside the frozen PyInstaller bundle. Returns an
    empty ``QIcon`` if the resource is missing (the app still runs).
    """
    try:
        from importlib.resources import files
        icons = files("sharpmod.resources").joinpath("icons")
        for name in ("app.ico", "app.png"):
            res = icons.joinpath(name)
            try:
                if res.is_file():
                    return QIcon(str(res))
            except (FileNotFoundError, OSError):
                continue
    except Exception:  # noqa: BLE001 -- icon is cosmetic; never block launch
        pass
    return QIcon()


def main(argv: list[str] | None = None) -> int:
    """Launch the interactive picker. Entry point for ``sharpmod-gui``."""
    _configure_debug_logging()
    relaunch_arguments = (
        list(sys.argv[1:]) if argv is None else list(argv[1:]))
    if _relaunch_stable_windows_gui(relaunch_arguments):
        return 0
    _LOGGER.info("application.start argv=%r", sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(sys.argv if argv is None
                                                  else argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)

    icon = _app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    picker = PickerWindow()
    picker.showMaximized()
    result = app.exec()
    _LOGGER.info("application.exit code=%s", result)
    return result
