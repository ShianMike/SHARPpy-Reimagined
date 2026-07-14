"""Shared runtime, identity, styling, and lazy imports for the desktop GUI."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from sharpmod._version import __version__

if "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = (
        "windows" if sys.platform.startswith("win")
        else ("cocoa" if sys.platform == "darwin" else "xcb")
    )
os.environ.setdefault("QT_API", "pyside6")

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

_render_mod = None
_compose_window_fn = None
_uwyo_catalog_mod = None
_uwyo_decoder_types = None

APP_NAME = "SHARPpy Reimagined"
APP_VERSION = __version__

_LOGGER = logging.getLogger("sharpmod.gui")
_DEBUG_LOG_PATH: Path | None = None
_ORIGINAL_EXCEPTHOOK = None


def _format_progress_bytes(value: int) -> str:
    """Format a byte count compactly for the forecast download rail."""
    value = max(0, int(value))
    if value < 1024:
        return f"{value} B"
    amount = float(value)
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        amount /= 1024.0
        if amount < 1024.0 or unit == "TiB":
            return f"{amount:.1f} {unit}"
    return f"{value} B"


def _format_progress_duration(seconds: float) -> str:
    """Format an elapsed/remaining duration without false precision."""
    seconds = max(0, int(round(seconds)))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _debug_log_path() -> Path:
    """Return the user-writable rolling GUI log location."""
    override = os.environ.get("SHARPMOD_GUI_LOG_DIR", "").strip()
    if override:
        root = Path(override).expanduser()
    elif sys.platform.startswith("win"):
        root = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) \
            / "SHARPpy Reimagined" / "Logs"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Logs" / "SHARPpy Reimagined"
    else:
        state = os.environ.get("XDG_STATE_HOME", "").strip()
        root = (Path(state).expanduser() if state
                else Path.home() / ".local" / "state") \
            / "sharpmod"
    return root / "sharpmod-gui.log"


def _gui_excepthook(exc_type, exc_value, exc_traceback) -> None:
    """Persist exceptions raised by Qt slots that a windowed app can hide."""
    _LOGGER.critical(
        "Unhandled GUI exception",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
    if _ORIGINAL_EXCEPTHOOK is not None:
        try:
            _ORIGINAL_EXCEPTHOOK(exc_type, exc_value, exc_traceback)
        except Exception:
            pass


def _configure_debug_logging() -> Path:
    """Install a small rotating log and an exception hook, once per process."""
    global _DEBUG_LOG_PATH, _ORIGINAL_EXCEPTHOOK
    if _DEBUG_LOG_PATH is not None:
        return _DEBUG_LOG_PATH

    candidates = (
        _debug_log_path(),
        Path(tempfile.gettempdir()) / "SHARPpy-Reimagined" / "sharpmod-gui.log",
    )
    handler = None
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                candidate,
                maxBytes=2_000_000,
                backupCount=2,
                encoding="utf-8",
            )
        except OSError:
            continue
        _DEBUG_LOG_PATH = candidate
        break
    if handler is None:
        _DEBUG_LOG_PATH = candidates[-1]
        return _DEBUG_LOG_PATH

    level = logging.DEBUG if os.environ.get("SHARPMOD_GUI_DEBUG", "").lower() \
        in {"1", "true", "yes", "on"} else logging.INFO
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(threadName)s %(message)s"))
    _LOGGER.setLevel(level)
    _LOGGER.propagate = False
    _LOGGER.addHandler(handler)

    if sys.excepthook is not _gui_excepthook:
        _ORIGINAL_EXCEPTHOOK = sys.excepthook
        sys.excepthook = _gui_excepthook

    _LOGGER.info(
        "GUI diagnostics started version=%s python=%s frozen=%s log=%s",
        APP_VERSION,
        sys.version.split()[0],
        bool(getattr(sys, "frozen", False)),
        _DEBUG_LOG_PATH,
    )
    return _DEBUG_LOG_PATH
PICKER_RAIL_MIN_WIDTH = 320
PICKER_RAIL_MAX_WIDTH = 380

#: One-line interaction hints shown in the sounding-window tip bar.
TIP_LINE = ("Tips:  right-click = readout / edit level   \u00b7   drag points to edit"
            "   \u00b7   wheel = zoom   \u00b7   \u2190\u2009/\u2009\u2192 = step "
            "time   \u00b7   Ctrl+Z / Ctrl+Y = undo / redo   \u00b7   Ctrl+E = export")

#: Full interaction guide (shared by the picker Help menu and the in-window
#: "Full guide" button).
CONTROLS_HTML = (
    "<b>Sounding window controls</b><br><br>"
    "<b>Right-click the Skew-T</b> \u2014 readout cursor, edit the nearest "
    "level numerically, Modify Surface, lift a parcel, reset.<br>"
    "<b>Click + drag</b> a temperature / dewpoint / wind point to edit the "
    "profile (indices recalculate live).<br>"
    "LCL, LFC, EL, MPL, and other diagnostics are recalculated results, so "
    "they are not edited directly.<br>"
    "<b>Mouse wheel</b> \u2014 zoom the Skew-T or hodograph.<br>"
    "<b>Right-click the hodograph</b> \u2014 re-center it; "
    "<b>double-click</b> the RM / LM markers to set the storm motion.<br>"
    "<b>Double-click the lower-left inset</b> \u2014 swap lifted parcels.<br><br>"
    "<b>Keys:</b> \u2190/\u2192 step in time, \u2191/\u2193 change ensemble "
    "member, <b>Space</b> swap focus, <b>I</b> interpolate, "
    "<b>C</b> collect observed, <b>W</b> back to the picker, "
    "<b>Ctrl+Z / Ctrl+Y</b> undo / redo analysis edits.<br><br>"
    "<b>Sessions:</b> File \u2192 Save Analysis Session preserves every loaded "
    "sounding and its current analysis state; Open Analysis Session restores "
    "them together in one viewer.<br><br>"
    "<b>Export:</b> the <b>Export</b> menu saves HD, UHD, or lossless PNG "
    "images (<b>Ctrl+E</b> for HD), copies the current view to the clipboard "
    "(<b>Ctrl+Shift+C</b>), or writes a SHARPpy text sounding that loads back "
    "into the app "
    "(File \u2192 Save Image / Save Text also work).")

PICKER_DARK_QSS = """
QMainWindow, QWidget {
    background: #10141c;
    color: #e7edf5;
    font-family: "Segoe UI", "Arial";
}
QMenuBar, QMenu {
    background: #151b25;
    color: #e7edf5;
    border: 1px solid #273244;
}
QMenuBar::item:selected, QMenu::item:selected {
    background: #263247;
}
QTabWidget::pane {
    border: 1px solid #263247;
    background: #10141c;
}
QTabBar::tab {
    background: #182131;
    color: #b8c5d6;
    padding: 8px 14px;
    border: 1px solid #263247;
    border-bottom: 0;
}
QTabBar::tab:selected {
    background: #223047;
    color: #ffffff;
}
QGroupBox {
    border: 1px solid #2a374b;
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    background: #151b25;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: #d5e0ef;
}
QLineEdit, QComboBox, QDateEdit, QListWidget {
    background: #0c1118;
    color: #edf3fb;
    border: 1px solid #2b3950;
    border-radius: 5px;
    padding: 6px;
    selection-background-color: #315d8f;
}
QListWidget::item {
    padding: 5px;
}
QListWidget::item:selected {
    background: #315d8f;
    color: #ffffff;
}
QPushButton, QToolButton {
    background: #24334a;
    color: #f4f8ff;
    border: 1px solid #3a4e6b;
    border-radius: 5px;
    padding: 6px 10px;
}
QPushButton:hover, QToolButton:hover {
    background: #2f4564;
}
QPushButton:pressed, QToolButton:pressed {
    background: #1b283b;
}
QPushButton:disabled, QToolButton:disabled, QLineEdit:disabled, QComboBox:disabled {
    color: #6f7d8f;
    background: #151b25;
    border-color: #253044;
}
QStatusBar {
    background: #0c1118;
    color: #aebbd0;
    border-top: 1px solid #263247;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: #10141c;
    width: 12px;
    height: 12px;
}
QScrollBar::handle {
    background: #334258;
    border-radius: 5px;
}
"""

SOUNDING_LIGHT_QSS = """
QMainWindow {
    background: #f3f6fa;
    color: #18202c;
}
QMenuBar, QMenu {
    background: #ffffff;
    color: #18202c;
    border: 1px solid #d7dde6;
}
QMenuBar::item:selected, QMenu::item:selected {
    background: #e8eef7;
}
QStatusBar {
    background: #ffffff;
    color: #39475a;
    border-top: 1px solid #d7dde6;
}
QScrollArea {
    background: #f3f6fa;
    border: 0;
}
QToolButton {
    background: transparent;
    border: 0;
    color: #34506f;
    padding: 2px 5px;
}
QToolButton:hover {
    background: #e8eef7;
    border-radius: 4px;
}
"""


def _show_controls_dialog(parent) -> None:
    """Show the shared interaction guide as a message box."""
    QMessageBox.information(parent, "Sounding Window Controls", CONTROLS_HTML)
#: Three-hourly UTC observation times offered for regular and special launches.
SYNOPTIC_HOURS = tuple(range(0, 24, 3))

#: How many recent files / stations to remember.
MAX_RECENTS = 8
def _most_recent_synoptic() -> tuple[QDate, int]:
    """Return the most recent (00Z/12Z) sounding time likely to be available.

    Radiosondes are launched at 00Z and 12Z with a reporting lag, so this picks
    the latest of those that is safely in the past (UTC), returning the date and
    hour to pre-select in the picker. The user can still choose any date and
    any three-hourly observation time, including special/asynoptic launches.
    """
    now = datetime.now(timezone.utc)
    if now.hour >= 13:
        d, h = now, 12
    elif now.hour >= 1:
        d, h = now, 0
    else:  # just after 00Z -- yesterday's 12Z is the safe most-recent
        d, h = now - timedelta(days=1), 12
    return QDate(d.year, d.month, d.day), h


def _render():
    """Import the heavy renderer stack on first use, not at picker startup."""
    global _render_mod
    if _render_mod is None:
        from sharpmod import render as render_mod
        _render_mod = render_mod
    return _render_mod


def _compose_window():
    """Return the SPCWindow composer, loading the vendored UI stack lazily."""
    global _compose_window_fn
    if _compose_window_fn is None:
        from sharpmod.viz.SPCWindow import compose_window as compose_window_fn
        _compose_window_fn = compose_window_fn
    return _compose_window_fn


def _uwyo_catalog():
    """Return the bundled station catalogue module, imported on first use."""
    global _uwyo_catalog_mod
    if _uwyo_catalog_mod is None:
        from sharpmod.io import uwyo_catalog as catalog_mod
        _uwyo_catalog_mod = catalog_mod
    return _uwyo_catalog_mod


def _uwyo_decoder_classes():
    """Return UWyo decoder classes, deferring network/decoder imports."""
    global _uwyo_decoder_types
    if _uwyo_decoder_types is None:
        from sharpmod.io.uwyo_decoder import (
            StationLookupError,
            UWyo_Decoder,
            UWyoError,
        )
        _uwyo_decoder_types = (StationLookupError, UWyo_Decoder, UWyoError)
    return _uwyo_decoder_types
