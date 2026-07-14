from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np

"""Analysis-session state and menu integration for sounding viewers."""

from sharpmod.gui_common import APP_NAME, _LOGGER

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

SESSION_SUFFIX = ".sharpmod-session"


def _viewer_session_ui_state(win) -> dict:
    """Capture viewer selections that are not owned by ``ProfCollection``."""
    sw = getattr(win, "spc_widget", None)
    convective = getattr(sw, "convective", None)
    return {
        "window_title": win.windowTitle(),
        "deviant": str(getattr(sw, "deviant", "right")),
        "parcel_types": list(getattr(convective, "pcl_types", None) or []),
        "parcel_index": int(getattr(convective, "skewt_pcl", 0) or 0),
    }


def _apply_viewer_session_state(win, active_collection: int,
                                ui_state: dict | None) -> None:
    """Restore the active sounding and supported viewer selections."""
    sw = getattr(win, "spc_widget", None)
    if sw is None or not getattr(sw, "prof_collections", None):
        return
    active = max(0, min(int(active_collection), len(sw.prof_collections) - 1))
    try:
        sw.setProfileCollection(sw.prof_ids[active])
    except Exception:
        sw.pc_idx = active
        sw.updateProfs()
    ui_state = dict(ui_state or {})
    deviant = str(ui_state.get("deviant", "right")).lower()
    if deviant in {"left", "right"}:
        try:
            sw.toggleVector(deviant)
        except Exception:
            pass

    convective = getattr(sw, "convective", None)
    board = getattr(sw, "index_board", None)
    parcel_types = [
        str(item).upper() for item in ui_state.get("parcel_types", [])
        if str(item).upper() in {"SFC", "ML", "FCST", "MU", "EFF", "USER"}
    ]
    if convective is not None and parcel_types:
        convective.pcl_types = parcel_types
        if board is not None:
            board.pcl_types = list(parcel_types)
        index = max(0, min(int(ui_state.get("parcel_index", 0)),
                           len(parcel_types) - 1))
        convective.skewt_pcl = index
        parcel = (getattr(convective, "parcels", None) or {}).get(
            parcel_types[index])
        if parcel is not None:
            try:
                sw.updateParcel(parcel)
            except Exception:
                pass
        if board is not None and getattr(board, "sp", None) is not None:
            try:
                board.setData(board.sp, board.dp)
            except Exception:
                pass
    title = str(ui_state.get("window_title", "")).strip()
    if title:
        win.setWindowTitle(title)


def _session_default_path() -> Path:
    desktop = Path.home() / "Desktop"
    root = desktop if desktop.is_dir() else Path.home()
    return root / f"analysis_{datetime.now():%Y%m%d_%H%M}{SESSION_SUFFIX}"


def _find_window_menu(win, title: str):
    """Return a stable C++-owned menu wrapper by its display title.

    Iterating ``menuBar().actions()`` and returning ``action.menu()`` creates a
    temporary Python-owned wrapper under PySide6. The wrapper can delete itself
    as the temporary ``QAction`` list leaves scope even though the visible menu
    remains. Finding the child ``QMenu`` directly preserves Qt ownership.
    """
    wanted = title.replace("&", "").lower()
    for menu in win.menuBar().findChildren(QMenu):
        if menu.title().replace("&", "").lower() == wanted:
            return menu
    return None


def _install_analysis_actions(win, controller) -> None:
    """Attach session save/open plus history-aware Edit actions once."""
    if getattr(win, "_sharpmod_analysis_actions_installed", False):
        return
    from sharpmod.sessions import (
        AnalysisHistory,
        build_session,
        write_session,
    )

    sw = getattr(win, "spc_widget", None)
    if sw is None:
        return
    history = AnalysisHistory(sw)
    sw._sharpmod_history = history
    win._sharpmod_history = history

    editmenu = _find_window_menu(win, "Edit")
    if editmenu is None:
        editmenu = win.menuBar().addMenu("&Edit")
    undo_action = QAction("Undo", win)
    undo_action.setShortcut("Ctrl+Z")
    redo_action = QAction("Redo", win)
    redo_action.setShortcut("Ctrl+Y")
    editmenu.addAction(undo_action)
    editmenu.addAction(redo_action)

    def _refresh_history_actions():
        undo_label = history.undo_label
        redo_label = history.redo_label
        undo_action.setEnabled(undo_label is not None)
        redo_action.setEnabled(redo_label is not None)
        undo_action.setText(f"Undo {undo_label}" if undo_label else "Undo")
        redo_action.setText(f"Redo {redo_label}" if redo_label else "Redo")

    def _undo():
        label = history.undo()
        if label:
            win.statusBar().showMessage(f"Undid {label}", 3000)

    def _redo():
        label = history.redo()
        if label:
            win.statusBar().showMessage(f"Redid {label}", 3000)

    undo_action.triggered.connect(_undo)
    redo_action.triggered.connect(_redo)
    history.add_listener(_refresh_history_actions)

    filemenu = _find_window_menu(win, "File")
    if filemenu is not None:
        filemenu.addSeparator()
        open_session = QAction("Open Analysis Session…", win)
        open_session.setShortcut("Ctrl+Shift+O")
        open_session.triggered.connect(controller._open_analysis_session)
        filemenu.addAction(open_session)
        save_session = QAction("Save Analysis Session…", win)
        save_session.setShortcut("Ctrl+Shift+E")

        def _save_session():
            suggested = str(getattr(win, "_sharpmod_session_path", "")
                            or _session_default_path())
            path, _ = QFileDialog.getSaveFileName(
                win,
                "Save Analysis Session",
                suggested,
                "SHARPpy Analysis Session (*.sharpmod-session)",
            )
            if not path:
                return
            if not path.lower().endswith(SESSION_SUFFIX):
                path += SESSION_SUFFIX
            try:
                document = build_session(
                    sw.prof_collections,
                    active_collection=int(getattr(sw, "pc_idx", 0)),
                    ui_state=_viewer_session_ui_state(win),
                )
                write_session(path, document)
            except Exception as exc:  # noqa: BLE001 - user-facing save error
                _LOGGER.exception("analysis_session.save_failed path=%s", path)
                QMessageBox.critical(
                    win, APP_NAME, f"The analysis session could not be saved:\n{exc}")
                return
            win._sharpmod_session_path = path
            win.statusBar().showMessage(f"Saved analysis session: {path}", 5000)
            _LOGGER.info(
                "analysis_session.saved path=%s soundings=%d", path,
                len(sw.prof_collections))

        save_session.triggered.connect(_save_session)
        filemenu.addAction(save_session)
        win._sharpmod_open_session_action = open_session
        win._sharpmod_save_session_action = save_session

    win._sharpmod_undo_action = undo_action
    win._sharpmod_redo_action = redo_action
    win._sharpmod_analysis_actions_installed = True
