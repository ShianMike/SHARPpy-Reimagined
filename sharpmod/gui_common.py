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
    QTransform, QDesktopServices, QTextCursor,
)

# theme is deliberately Qt-free and imports nothing from sharpmod, so this
# cannot close an import cycle.
from sharpmod.theme import OBJ_GUIDE_BODY, OBJ_GUIDE_DIALOG
from qtpy.QtWidgets import (
    QApplication,
    QMainWindow,
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
    "There is no drag-to-pan inside a magnified panel, because dragging edits "
    "the profile. To move elsewhere, reset and magnify again with the pointer "
    "over the part you want.<br><br>"
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
    "<b>Ctrl+Z / Ctrl+Y</b> undo / redo analysis edits, <b>F1</b> this guide."
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


class MonthCalendar(QCalendarWidget):
    """Date-picker popup that shows only the month on display.

    The default popup has two problems under the application style sheet. The
    generic ``QTableView::item`` padding rule also matches the calendar's cells,
    because a ``QCalendarWidget`` is a ``QTableView`` internally; that leaves too
    little room for a two-digit day, and the item delegate elides the number to
    an ellipsis. Separately, the leading and trailing cells show the neighbouring
    months' days, which is noise in a control whose only job is to choose a date
    within the month on show, and invites clicking a day that silently jumps the
    view to another month.

    Painting the cells directly solves both: the number is drawn centred at the
    cell's full width with no delegate and no elision, and days outside the
    shown month are left blank. Weekend tinting and the disabled colour are read
    back from the widget's own formats and palette, so the popup still follows
    the active theme and any configured date range.
    """

    def _weekday_colour(self, date, palette) -> QColor:
        """Return the configured tint for ``date``'s weekday.

        ``weekdayTextFormat`` takes a ``Qt.DayOfWeek``, while ``QDate.dayOfWeek``
        returns a plain int, and the bindings are not consistent about coercing
        between them. A failure here must not be able to make the popup
        unpaintable, so an unusable format falls back to the ordinary text
        colour.
        """
        try:
            day = Qt.DayOfWeek(date.dayOfWeek())
            brush = self.weekdayTextFormat(day).foreground()
            if brush.style() != Qt.NoBrush:
                return brush.color()
        except (TypeError, ValueError):
            pass
        return palette.text().color()

    def paintCell(self, painter, rect, date) -> None:  # noqa: N802 (Qt override)
        palette = self.palette()
        in_month = (date.month() == self.monthShown()
                    and date.year() == self.yearShown())
        selected = in_month and date == self.selectedDate()

        painter.save()
        try:
            if selected:
                painter.fillRect(rect, palette.highlight())
            else:
                painter.fillRect(rect, palette.base())
            if not in_month:
                return  # adjacent month: background only, no number

            in_range = self.minimumDate() <= date <= self.maximumDate()
            if selected:
                colour = palette.highlightedText().color()
            elif not in_range:
                colour = palette.color(palette.ColorGroup.Disabled,
                                       palette.ColorRole.Text)
            else:
                colour = self._weekday_colour(date, palette)
            painter.setPen(QPen(colour))
            painter.setFont(self.font())
            painter.drawText(rect, Qt.AlignCenter, str(date.day()))
        finally:
            painter.restore()


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
