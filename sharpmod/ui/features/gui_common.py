"""Shared runtime, identity, styling, and lazy imports for the desktop GUI."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import weakref
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import MappingProxyType

from sharpmod._version import __version__

if "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = (
        "windows" if sys.platform.startswith("win")
        else ("cocoa" if sys.platform == "darwin" else "xcb")
    )
os.environ.setdefault("QT_API", "pyside6")

from qtpy.QtCore import (
    Qt, QThread, QTimer, Signal, QDate, QEvent, QObject, QSettings, QPointF,
    QRectF, QSize, QUrl,
)
from qtpy.QtGui import (
    QAction, QPainter, QColor, QPen, QBrush, QPolygonF, QFont, QPixmap, QIcon,
    QTransform, QDesktopServices, QTextCursor,
)

# theme is deliberately Qt-free and imports nothing from sharpmod, so this
# cannot close an import cycle.
from sharpmod.ui.styles.theme import (
    CONTROL_H,
    OBJ_ERROR_TEXT,
    OBJ_GUIDE_BODY,
    OBJ_GUIDE_DIALOG,
    OBJ_STATUS,
    OBJ_TOP_BAR_MENU,
    OBJ_WARNING_TEXT,
    POPUP_ROWS,
)
from qtpy.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QMainWindow,
    QSlider,
    QWidget,
    QTextBrowser,
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
    QCalendarWidget,
    QCheckBox,
    QSizePolicy,
    QGraphicsView,
    QGraphicsScene,
    QProgressBar,
    QMenu,
    QStyledItemDelegate,
    QTableWidgetItem,
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

# Shared verbs use one spelling and one ellipsis convention. Keep this list
# deliberately small: add an entry only after a second real surface needs it.
ACTION_LABELS = MappingProxyType(
    {
        "cancel": "Cancel",
        "load_sounding": "Load sounding",
        "export_csv": "Export CSV…",
        "open": "Open…",
        "refresh": "Refresh",
        "retry_unavailable": "Retry unavailable",
        "save": "Save…",
    }
)

_STATUS_LEVELS = {
    "info": (OBJ_STATUS, "Status message", ""),
    "warn": (OBJ_WARNING_TEXT, "Warning message", "Warning: "),
    "warning": (OBJ_WARNING_TEXT, "Warning message", "Warning: "),
    "error": (OBJ_ERROR_TEXT, "Error message", "Error: "),
}


class SortableTableItem(QTableWidgetItem):
    """A table cell that sorts by a real value instead of display text."""

    SORT_ROLE = Qt.UserRole + 1

    def __init__(self, text="", sort_key=None):
        super().__init__(str(text))
        self.setData(self.SORT_ROLE, text if sort_key is None else sort_key)

    def __lt__(self, other):
        try:
            return self.data(self.SORT_ROLE) < other.data(self.SORT_ROLE)
        except TypeError:
            return super().__lt__(other)


def action_label(key: str) -> str:
    """Return the canonical reader-facing label for a shared action."""
    try:
        return ACTION_LABELS[str(key)]
    except KeyError as exc:
        raise ValueError(f"unknown shared action label {key!r}") from exc


def set_status_label(label: QLabel, text, *, level: str = "info") -> QLabel:
    """Apply one semantic, accessible state to an existing status label.

    The visible message still names the concrete state (loading, unavailable,
    cancelled, and so on); colour is supporting information rather than the
    only signal.  Keeping repolishing here prevents each workspace from
    carrying a slightly different object-name swap.
    """
    normalized = str(level or "info").strip().casefold()
    object_name, accessible_name, prefix = _STATUS_LEVELS.get(
        normalized, _STATUS_LEVELS["info"]
    )
    canonical = "warn" if normalized in {"warn", "warning"} else (
        normalized if normalized in {"info", "error"} else "info"
    )
    message = str(text)
    if prefix and not message.casefold().startswith(prefix.strip().casefold()):
        message = prefix + message
    label.setTextFormat(Qt.PlainText)
    label.setText(message)
    label.setAccessibleName(accessible_name)
    label.setAccessibleDescription(message)
    label.setProperty("statusLevel", canonical)
    if label.objectName() != object_name:
        label.setObjectName(object_name)
        style = label.style()
        style.unpolish(label)
        style.polish(label)
    return label


def make_status_label(
    text: str = "", *, level: str = "info", parent=None
) -> QLabel:
    """Create the shared word-wrapped empty/loading/error state surface."""
    label = QLabel(parent)
    label.setWordWrap(True)
    return set_status_label(label, text, level=level)


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


#: One-line interaction hints shown in the sounding-window tip bar.  Mutation
#: gestures deliberately name the explicit mode first: advertising a bare drag
#: as an edit would contradict the Inspect-by-default safety boundary.
TIP_LINE = (
    "Tips:  Ctrl+Alt+I / Ctrl+Alt+E = Inspect / Edit   \u00b7   "
    "right-click = readout / edit menu   \u00b7   drag points in Edit mode   \u00b7   "
    "wheel = zoom   \u00b7   \u2190\u2009/\u2009\u2192 = step time   \u00b7   "
    "Ctrl+Z / Ctrl+Y = undo / redo   \u00b7   Ctrl+E = export"
)

#: Full interaction guide (shared by the picker Help menu and the in-window
#: "Full guide" button).
CONTROLS_HTML = (
    "<b>Sounding window controls</b><br><br>"
    "<b>Inspect and Edit are explicit modes.</b> The viewer opens in the "
    "remembered safe mode (Inspect when no valid preference exists). Use "
    "<b>Ctrl+Alt+I</b> for Inspect mode and <b>Ctrl+Alt+E</b> for Edit mode; "
    "the checked toolbar action, cursor, and status hint always show which mode "
    "owns the pointer. Inspect mode links the Skew-T and hodograph readout at "
    "one level but blocks every profile and storm-motion mutation.<br>"
    "<b>Right-click the Skew-T</b> for the readout cursor and menu. Editing the "
    "nearest level, Modify Surface, interpolation, reset, and storm-motion "
    "changes require Edit mode.<br>"
    "In <b>Edit mode</b>, <b>click + drag</b> a temperature / dewpoint / wind "
    "point to edit the profile (indices recalculate live). The edit readout "
    "states original, proposed, and changed values; the optional dashed "
    "original overlay keeps the retained baseline visible.<br>"
    "Use <b>Ctrl+Alt+H</b> for the readable edit history. <b>Ctrl+Z / "
    "Ctrl+Y</b> move through the same bounded undo/redo history.<br>"
    "LCL, LFC, EL, MPL, and other diagnostics are recalculated results, so "
    "they are not edited directly.<br>"
    "The hodograph defaults to <b>Mean Wind</b> centering; "
    "<b>right-click it</b> to change the center; "
    "<b>double-click</b> the RM / LM markers to set the storm motion.<br>"
    "<b>Double-click the lower-left inset</b> \u2014 swap lifted parcels.<br><br>"
    # Zoom is two separate things on the same gesture, which is not guessable.
    # Spelling out the direction and the limit matters: zooming out stops at the
    # normal view, so scrolling that way at the default does nothing at all and
    # reads as broken.
    "<b>Zooming \u2014 one panel</b><br>"
    "Point at the Skew-T or the hodograph and <b>scroll</b>. Scroll <b>up</b> "
    "to magnify, <b>down</b> to come back out. Each panel zooms on its own, and "
    "the zoom centres on the pointer, so aim at what you want enlarged.<br>"
    "Zooming out stops at the normal view \u2014 it will not go wider than that, "
    "so at the default scrolling down does nothing. That is also how you reset: "
    "scroll down until it stops.<br>"
    "There is no drag-to-pan inside a magnified panel: in Edit mode a drag can "
    "change the profile, while Inspect mode reserves it for inspection. To move "
    "elsewhere, reset and magnify again with the pointer over the part you "
    "want.<br><br>"
    "<b>Zooming \u2014 the whole sounding</b><br>"
    "<b>Ctrl+scroll</b> zooms the entire image, as do the <b>View</b> toolbar's "
    "Fit to Window / Actual Size buttons and the zoom slider.<br>"
    "<b>Ctrl+0</b> fits the whole sounding to the window; <b>Ctrl+1</b> shows it "
    "at 100%, which is the sharpest view because the sounding is drawn at that "
    "size; <b>Ctrl++ / Ctrl+-</b> step. <b>Middle-button drag</b> pans when the "
    "image is larger than the window.<br>"
    "<b>F11</b> goes full screen (<b>Escape</b> leaves). Worth using: the fit is "
    "limited by height, so the title bar and taskbar it reclaims make the "
    "sounding roughly 8% larger.<br><br>"
    "<b>Keys:</b> \u2190/\u2192 step in time, \u2191/\u2193 change ensemble "
    "member, <b>Space</b> swap focus, <b>I</b> interpolate, "
    "<b>C</b> collect observed, <b>W</b> back to the picker, "
    "<b>Ctrl+Z / Ctrl+Y</b> undo / redo analysis edits, <b>F1</b> this guide. "
    "<b>Ctrl+K</b> opens Find an Action: search names, shortcuts, or aliases; "
    "unavailable actions explain their current state and cannot run."
    "<br><b>Search selectors:</b> focus a choice field and press <b>Ctrl+Space</b>, "
    "or use its Search button / right-click Search choices. Search full names, "
    "abbreviations and aliases; mark repeated choices as favorites. Use selected "
    "confirms an actual item; Cancel leaves the current value unchanged."
    "<br><b>Paste coordinates:</b> point-based sources preview pasted decimal "
    "or N/S/E/W coordinates before Use this point. Ambiguous unlabeled pairs "
    "need explicit latitude/longitude order; Cancel leaves the point unchanged."
    "<br><br>"
    "<b>Sounding panel</b> (<b>Ctrl+B</b>) \u2014 the strip on the right lists "
    "every loaded sounding and marks which one is in focus; click to switch. It "
    "also selects the ensemble member and opens the source and quality "
    "report.<br><br>"
    "<b>Forecast timeline</b> \u2014 when a sounding covers several times, a "
    "second toolbar appears with previous / next, a scrub slider, and looping "
    "playback.<br><br>"
    "<b>Sessions:</b> File \u2192 Save Analysis Session preserves every loaded "
    "sounding and its current analysis state; Open Analysis Session restores "
    "them together in one viewer.<br><br>"
    "<b>Export:</b> the <b>Export</b> menu saves HD, UHD, or lossless PNG "
    "images (<b>Ctrl+E</b> for HD), copies the current view to the clipboard "
    "(<b>Ctrl+Shift+C</b>), or writes a SHARPpy text sounding that loads back "
    "into the app "
    "(File \u2192 Save Image / Save Text also work).")



def _show_controls_dialog(parent) -> None:
    """Show the shared interaction guide in a scrollable, resizable dialog.

    Deliberately not a ``QMessageBox``: a message box lays its text out at
    whatever height the content needs and cannot scroll, so the guide grew to
    about 400x1224 px -- narrower than a paragraph wants and taller than a
    1080p screen, with the overflow simply unreachable.

    The dialog is disposed of explicitly at the end. It is parented to the
    window so it centres on it and stays in front, which also means Qt keeps it
    alive until the *window* dies -- so without this every F1 press left another
    760x620 dialog and its fully populated ``QTextBrowser`` attached to the
    window, and this is a guide people open repeatedly while learning the zoom
    gestures. ``QMessageBox.information`` had no such problem, so the leak
    arrived with the scrollable rewrite.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("Sounding Window Controls")
    dialog.setObjectName(OBJ_GUIDE_DIALOG)
    dialog.resize(760, 620)

    layout = QVBoxLayout(dialog)
    body = QTextBrowser(dialog)
    body.setObjectName(OBJ_GUIDE_BODY)
    body.setOpenExternalLinks(True)
    body.setHtml(CONTROLS_HTML)
    # Start at the top: QTextBrowser otherwise keeps whatever scroll position
    # the layout pass left behind.
    body.moveCursor(QTextCursor.Start)
    layout.addWidget(body, 1)

    buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
    # One connection, not three. A Close button carries RejectRole, so
    # ``accepted`` can never fire, and the extra ``clicked`` lambda both raced
    # ``rejected`` (Qt emits clicked first, so the dialog resolved Accepted then
    # Rejected) and captured ``dialog`` on one of its own children.
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    try:
        dialog.exec()
    finally:
        # deleteLater *after* exec returns -- not WA_DeleteOnClose. That
        # attribute deletes the dialog from inside the close that ends the modal
        # loop, while QDialog::exec is still on the stack and about to touch its
        # own members; it segfaults on teardown (0xC0000005 here). Scheduling
        # the delete once exec has unwound frees the dialog just as reliably and
        # leaves nothing for Qt to touch.
        #
        # Guarded, because the dialog is a child of the window: if the parent is
        # destroyed while the modal is up, the C++ object goes with it and this
        # wrapper is already stale, so the call would raise RuntimeError out of
        # whatever opened the guide.
        try:
            dialog.deleteLater()
        except RuntimeError:
            pass


def scrollable_page(content, *, parent=None, accessible_name="Panel content"):
    """Keep a real Qt panel usable when enlarged text exceeds the window.

    The child's layout keeps its honest minimum size; the surrounding viewport
    can become smaller and Qt scrolls/focuses controls into view. This does not
    rescale scientific canvases, or replace any panel's own state/worker model.
    """
    scroll = QScrollArea(parent)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setFocusPolicy(Qt.NoFocus)
    scroll.setAccessibleName(accessible_name)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setWidget(content)
    return scroll

#: Three-hourly UTC observation times offered for regular and special launches.
SYNOPTIC_HOURS = tuple(range(0, 24, 3))

#: How many recent files / stations to remember.
MAX_RECENTS = 8
def _install_fullscreen_action(win, menu):
    """Add a Full Screen toggle (F11) for ``win`` to ``menu``.

    Shared by the picker and the sounding window so the gesture is the same in
    both. Worth having in the sounding window in particular: the fit scale there
    is limited by *height*, so reclaiming the title bar and the taskbar makes the
    sounding meaningfully larger rather than merely tidier.

    Two details that are easy to get wrong:

    * Leaving full screen uses ``showMaximized`` when the window was maximized
      going in. ``showNormal`` is the obvious call and is wrong -- it drops a
      maximized window back to its small floating size, so F11 twice would not
      return you where you started.
    * ``Escape`` also leaves full screen, which is the near-universal
      convention, but its action is *disabled* whenever the window is not full
      screen. A permanently enabled Escape shortcut would silently swallow the
      key everywhere else in the window.
    """
    action = QAction("&Full Screen", win)
    action.setShortcut("F11")
    action.setCheckable(True)
    action.setChecked(win.isFullScreen())
    action.setToolTip(
        "Use the whole screen (F11, or Escape to leave)")

    escape = QAction("Leave Full Screen", win)
    escape.setShortcut("Esc")
    escape.setEnabled(win.isFullScreen())
    # Not in any menu: F11 is the advertised way back, and a second visible
    # entry for the same thing is noise.
    win.addAction(escape)

    # Weak, deliberately. Both actions are children of the window, so Qt holds
    # their connections C++-side where Python's cyclic GC cannot see them. A
    # closure capturing ``win`` strongly therefore pins the window's wrapper for
    # the life of the process, and every viewer open/close cycle would retain a
    # whole sounding window. See test_gui_viewer_lifecycle.
    win_ref = weakref.ref(win)

    def _apply(enable: bool) -> None:
        window = win_ref()
        if window is None:
            return
        if enable:
            window._sharpmod_pre_fullscreen_maximized = window.isMaximized()
            window.showFullScreen()
        elif getattr(window, "_sharpmod_pre_fullscreen_maximized", False):
            window.showMaximized()
        else:
            window.showNormal()
        escape.setEnabled(window.isFullScreen())

    action.toggled.connect(_apply)

    def _leave() -> None:
        window = win_ref()
        if window is not None and window.isFullScreen():
            action.setChecked(False)

    escape.triggered.connect(_leave)

    # The window can leave full screen without going through either action --
    # a window-manager shortcut, for instance -- so re-read the real state
    # whenever the menu is about to be shown. That is the only moment the
    # checkmark is visible, so it is the only moment it has to be right.
    def _sync() -> None:
        window = win_ref()
        if window is None:
            return
        was = action.blockSignals(True)
        try:
            action.setChecked(window.isFullScreen())
        finally:
            action.blockSignals(was)
        escape.setEnabled(window.isFullScreen())

    menu.aboutToShow.connect(_sync)
    menu.addAction(action)
    win._sharpmod_fullscreen_action = action
    return action


def as_utc(when: datetime | None) -> datetime | None:
    """Return ``when`` as tz-aware UTC, treating a naive value as already UTC.

    Several picker tabs build their valid time from bare ``QDateEdit`` and
    ``QComboBox`` state and produce a naive ``datetime``; the values are UTC by
    construction, since every cycle and forecast hour in this application is.
    Consumers that must not guess a timezone -- resolving which SPC convective
    outlook covers a time, for instance -- reject naive input outright, so the
    assumption is made explicit here instead of being repeated at each caller.
    """
    if when is None:
        return None
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


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
        from sharpmod.rendering import cli as render_mod
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


# ---------------------------------------------------------------------------
# Wheel guard
# ---------------------------------------------------------------------------

#: Value pickers whose selection the wheel must never change.
#:
#: Every one of these sits in a control rail that scrolls, and Qt's default is to
#: treat a wheel over them as a value change. So a scroll aimed at the rail
#: silently switched the model, the region, the cycle, or the forecast hour on the
#: way past -- and because the rail did not move, the only feedback was a value
#: the user did not choose. A spin box is the same hazard with a coordinate in it.
_WHEEL_BLOCKED = (QComboBox, QAbstractSpinBox)

#: Controls that keep their wheel unless a scrolling ancestor has a better claim.
#:
#: A slider is a drag control and wheeling one is a reasonable gesture, so this is
#: not blanket-blocked: the hazard is only that a scroll meant for the rail lands
#: on it. Inside a scroll area the rail wins; anywhere else -- the sounding
#: window's zoom slider sits in a toolbar -- the slider keeps its wheel.
_WHEEL_YIELDS = (QSlider,)


def _scrollable_ancestor(widget):
    """Return the nearest scrolling ancestor of ``widget``, or ``None``."""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


class _WheelValueGuard(QObject):
    """Stops the mouse wheel from changing a control's value.

    Installed on the ``QApplication`` rather than per widget, for the reason the
    chrome style sheet is: the picker builds four of its five source panels
    lazily and every dialog on demand, so anything attached per widget at
    construction reaches only what exists at the time.

    The event is re-sent to the scrolling ancestor rather than merely swallowed.
    Consuming it would fix the wrong half of the problem: the value would stop
    changing, but the rail still would not move, so the gesture would do nothing
    at all and read as a dead scroll area. Forwarding makes the wheel mean
    "scroll the rail" everywhere inside it, which is what the user was aiming at.

    Combo *popups* are unaffected. A popup's list view is a ``QListView`` inside
    its own window, not a ``QComboBox``, so it never matches here and keeps the
    wheel it needs to scroll a long list of radar sites or forecast hours.
    """

    def eventFilter(self, obj, event):  # noqa: N802 - Qt override
        if event.type() != QEvent.Type.Wheel:
            return False
        if not isinstance(obj, QWidget):
            return False

        blocked = isinstance(obj, _WHEEL_BLOCKED)
        if not blocked and not isinstance(obj, _WHEEL_YIELDS):
            return False

        area = _scrollable_ancestor(obj)
        if area is None:
            # Nothing to hand it to: swallow it for a value picker, leave it for
            # anything that only yields out of politeness.
            return blocked

        QApplication.sendEvent(area.viewport(), event)
        return True


#: Module-level so the filter outlives the call that installed it. An event
#: filter is not owned by the object it is installed on, so a local would be
#: collected and the guard would silently stop working.
_wheel_guard: _WheelValueGuard | None = None


def install_wheel_guard(app=None) -> bool:
    """Stop the wheel changing values app-wide. Returns whether it installed.

    Idempotent, and safe to call before or after widgets exist. Called from
    ``main`` and again from ``PickerWindow.__init__``, so an embedder or a test
    that constructs the window directly gets the same behaviour as the shipped
    application -- the same arrangement ``ensure_theme_applied`` uses.
    """
    global _wheel_guard
    if _wheel_guard is not None:
        return False
    app = app or QApplication.instance()
    if app is None:
        return False
    _wheel_guard = _WheelValueGuard(app)
    app.installEventFilter(_wheel_guard)
    _LOGGER.info("wheel_guard.installed")
    return True


class MenuRowDelegate(QStyledItemDelegate):
    """Gives popup rows a menu's height.

    Has to be a delegate. A view's row height comes from its item delegate, which
    does not consult the style sheet, so neither ``::item { padding }`` nor
    ``::item { min-height }`` moved it -- both were measured leaving the rows at
    the compact list height of 25px against the menu's 33px.

    Separators keep the height they ask for. A separator is not a row anyone
    points at, and stretching it to a full row turns a divider into a gap.
    """

    def sizeHint(self, option, index):  # noqa: N802 - Qt override
        size = super().sizeHint(option, index)
        if _is_separator(index):
            return size
        size.setHeight(max(size.height(), CONTROL_H["md"]))
        return size


def _is_separator(index) -> bool:
    """Whether a model index is one of ``QComboBox.insertSeparator``'s dividers."""
    try:
        return str(index.data(Qt.AccessibleDescriptionRole) or "") == "separator"
    except (AttributeError, TypeError):
        return False


#: Marks a combo box whose popup has already been given the menu treatment, so
#: the filter configures each one once rather than on every show.
_POPUP_READY = "sharpmodPopupReady"


def configure_combo_popup(combo) -> None:
    """Give one combo box's popup a menu's metrics and a bounded height.

    Idempotent. Three things have to be true for a dropdown to read like the
    menus in the top bar, and none of them can be reached from the style sheet
    alone:

    * the popup's view carries :data:`~sharpmod.ui.styles.theme.OBJ_TOP_BAR_MENU`, because a
      popup is its own top-level window and so is not a style-sheet descendant of
      the combo -- the descendant selector is accepted and matches nothing;
    * its rows come from :class:`MenuRowDelegate`, because row height is the item
      delegate's answer and the delegate does not read the style sheet;
    * the row count is bounded, because menu-height rows on an unbounded popup put
      a 209-entry list past the bottom of the screen.
    """
    if combo is None or combo.property(_POPUP_READY):
        return
    view = combo.view()
    if view is None:
        return
    view.setObjectName(OBJ_TOP_BAR_MENU)
    combo.setItemDelegate(MenuRowDelegate(combo))
    # A call site that asked for *fewer* rows than the cap wanted a shorter popup
    # and still gets one; Qt's own default of ten is not such a request, so it is
    # the cap that applies. Anything above the cap is what the cap is for.
    wanted = POPUP_ROWS["max"]
    existing = int(combo.maxVisibleItems())
    if existing != _QT_DEFAULT_MAX_VISIBLE and existing < wanted:
        wanted = existing
    combo.setMaxVisibleItems(wanted)
    combo.setProperty(_POPUP_READY, True)
    from sharpmod.ui.features.gui_selectors import configure_combo_search

    configure_combo_search(combo)


#: Qt's own default for ``QComboBox.maxVisibleItems``. Treated as "nobody asked",
#: so the cap applies rather than shrinking every popup in the application to ten.
_QT_DEFAULT_MAX_VISIBLE = 10


def place_combo_popup(combo) -> None:
    """Drop ``combo``'s open popup below the field, the way a menu does.

    Qt places a combo popup to sit *over* its field. For the source dropdown in
    the menu bar that put the popup's top edge 24px above the field's bottom edge,
    covering the bar it belongs to; a menu drops from the bottom of its title.
    Same gesture, same row of controls, two different relationships -- and the
    difference shows on every use.

    Called after Qt has shown the popup, so it has already been sized and clamped
    to the screen and the only thing left to correct is the origin and the height.

    The height has to be bounded here rather than through
    ``QComboBox.maxVisibleItems``, which was measured having no effect at all: a
    19-entry list still opened 19 rows tall against a cap of 14. Qt documents that
    property as ignored for styles whose ``SH_ComboBox_Popup`` is true, and an
    application style sheet turns it true -- which is also why these popups were
    covering their fields in the first place, since that is where a menu-style
    popup puts itself. So the same style sheet that gives the dropdowns a menu's
    look takes away the property that would have bounded them, and the bound has
    to be applied to the container directly.
    """
    view = combo.view() if combo is not None else None
    popup = view.window() if view is not None else None
    if popup is None:
        return
    _bound_popup_height(combo, view, popup)
    geometry = popup.geometry()
    geometry.moveTopLeft(combo.mapToGlobal(combo.rect().bottomLeft()))
    # If it will not fit below, hang it above the field instead -- which is what
    # Qt itself does for a menu with no room under its title.
    screen = getattr(combo, "screen", lambda: None)()
    available = screen.availableGeometry() if screen is not None else None
    if available is not None and geometry.bottom() > available.bottom():
        geometry.moveBottomLeft(combo.mapToGlobal(combo.rect().topLeft()))
    popup.setGeometry(geometry)


def _bound_popup_height(combo, view, popup) -> None:
    """Hold the popup between :data:`~sharpmod.ui.styles.theme.POPUP_ROWS`' two bounds.

    Measured in rows of the popup's own first row rather than in pixels, so a
    bound follows the row height instead of having to be retuned beside it. The
    container's chrome -- its frame, and the view's padding -- is measured the same
    way, as the part of the container the rows do not occupy, because both come
    from the style sheet and neither is knowable from here.

    The bounds are left on the container as minimum and maximum rather than as a
    fixed height. Qt reuses one container per combo across opens and resizes it to
    the item count each time, so a fixed height would be right once and then leave
    a shrinking list padded with blank space.
    """
    rows = combo.count()
    row = view.sizeHintForRow(0) if rows else CONTROL_H["md"]
    row = max(int(row), CONTROL_H["md"])
    viewport = view.viewport()
    chrome = max(0, popup.height() - (viewport.height() if viewport else 0))
    ceiling = POPUP_ROWS["max"] * row + chrome
    floor = min(POPUP_ROWS["min"] * row + chrome, ceiling)
    popup.setMinimumHeight(floor)
    popup.setMaximumHeight(ceiling)
    # The scrollbar is what "bounded" has to mean for the reader: a wheel over a
    # capped list has to move it. Menu-style popups otherwise offer only the hover
    # arrows at top and bottom, which are easy to miss on a list this long.
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    wanted = max(floor, min(ceiling, popup.height()))
    if wanted != popup.height():
        popup.resize(popup.width(), wanted)


class _ComboPopupPlacer(QObject):
    """Applies the top bar's dropdown behaviour to every combo box.

    Installed on the ``QApplication`` for the reason the wheel guard and the
    chrome style sheet are: the picker builds five of its six source panels
    lazily and every dialog on demand, so anything attached per widget at
    construction reaches only what exists at the time. There are around forty
    combo boxes across the picker, its dialogs, and the analysis workspace, and
    they are built by a dozen different modules.

    Two events, because the two halves have different deadlines. Configuration has
    to land before Qt sizes a popup, so it happens when the *combo* is polished or
    shown -- both of which precede any click on it. Placement can only happen once
    the popup exists, so it happens when the popup window is shown.
    """

    def eventFilter(self, obj, event):  # noqa: N802 - Qt override
        kind = event.type()
        if kind not in (QEvent.Type.Show, QEvent.Type.Polish):
            return False
        if isinstance(obj, QComboBox):
            configure_combo_popup(obj)
            return False
        if kind != QEvent.Type.Show or not isinstance(obj, QWidget):
            return False
        # Qt parents a combo's popup container to the combo itself, so this is
        # what a popup being opened looks like from the application's side.
        if not obj.isWindow():
            return False
        owner = obj.parentWidget()
        if isinstance(owner, QComboBox):
            place_combo_popup(owner)
        return False


#: Module-level for the reason ``_wheel_guard`` is: an event filter is not owned
#: by the object it is installed on, so a local would be collected and the
#: behaviour would silently stop working.
_popup_placer: _ComboPopupPlacer | None = None


def install_popup_placement(app=None) -> bool:
    """Make every dropdown behave like the top bar's. Returns whether it installed.

    Idempotent, and safe to call before or after widgets exist. Called from
    ``main`` and again from ``PickerWindow.__init__``, so an embedder or a test
    that constructs the window directly gets the same behaviour as the shipped
    application -- the same arrangement ``install_wheel_guard`` uses.
    """
    global _popup_placer
    if _popup_placer is not None:
        return False
    app = app or QApplication.instance()
    if app is None:
        return False
    _popup_placer = _ComboPopupPlacer(app)
    app.installEventFilter(_popup_placer)
    _LOGGER.info("popup_placement.installed")
    return True




def install_month_calendar(date_edit) -> MonthCalendar:
    """Give ``date_edit`` a popup restricted to the month it is showing.

    ``setCalendarPopup(True)`` must already have been called; Qt ignores a
    calendar widget assigned to an edit that has no popup.
    """
    calendar = MonthCalendar(date_edit)
    calendar.setGridVisible(False)
    # The two header views are drawn by Qt, not by ``paintCell``, so they are
    # still subject to the style sheet's section padding and elide exactly the
    # way the day cells used to. Removing the week-number column frees that
    # width for the day columns, and single-letter weekday names always fit
    # whatever remains. ISO week numbers are of no use when picking a sounding
    # date, so nothing is lost by dropping them.
    calendar.setVerticalHeaderFormat(
        QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    calendar.setHorizontalHeaderFormat(
        QCalendarWidget.HorizontalHeaderFormat.SingleLetterDayNames)
    date_edit.setCalendarWidget(calendar)
    return calendar


from sharpmod.ui.calendar import MonthCalendar  # noqa: E402
