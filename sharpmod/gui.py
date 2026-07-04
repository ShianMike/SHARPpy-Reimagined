"""Interactive SHARPpy Reimagined desktop GUI (legacy-SHARPpy style).

This module turns the otherwise headless :mod:`sharpmod.render` pipeline into an
interactive, on-screen application -- the modern equivalent of the legacy
SHARPpy "Picker" + ``SPCWindow`` experience:

* A **picker** window (:class:`PickerWindow`) lets the user either open a local
  sounding file (``.npz``, SPC tabular, BUFKIT, PECAN, WRF-ARW text) or fetch an
  observed University of Wyoming sounding by station + UTC time.
* Each chosen sounding is composed into the *exact same* SPC-style window the
  PNG renderer produces (skew-T, hodograph, index tables, insets, and the
  SHARPpy Reimagined derived-parameter panels) and shown on screen in a
  :class:`SoundingViewer` window.

The only difference from :mod:`sharpmod.render` is the Qt platform: the renderer
runs under the ``offscreen`` platform and grabs a pixmap, whereas this module
selects the native windowing platform *before* importing the renderer so the
same composed window is realized on screen. All of the renderer's font install,
vendored-widget monkeypatches, layout compensation, and window-grow passes are
reused verbatim so the interactive window matches the rendered PNG pixel-for-pixel.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# --- Qt platform / binding setup (MUST precede the first Qt import) ---------
# The renderer defaults ``QT_QPA_PLATFORM`` to "offscreen" via ``setdefault``.
# For an interactive app we want the native windowing platform, so pin it here
# BEFORE importing :mod:`sharpmod.render` (whose ``setdefault`` then no-ops).
# Respect an explicit override so power users can still force e.g. "xcb".
if os.environ.get("QT_QPA_PLATFORM", "offscreen") == "offscreen":
    os.environ["QT_QPA_PLATFORM"] = (
        "windows" if sys.platform.startswith("win")
        else ("cocoa" if sys.platform == "darwin" else "xcb")
    )
os.environ.setdefault("QT_API", "pyside6")

from qtpy.QtCore import (  # noqa: E402
    Qt, QThread, Signal, QDate, QSettings, QPointF, QRectF, QSize,
)
from qtpy.QtGui import (  # noqa: E402
    QAction, QPainter, QColor, QPen, QBrush, QPolygonF, QFont, QPixmap, QIcon,
)
from qtpy.QtWidgets import (  # noqa: E402
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
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QGroupBox,
    QStatusBar,
    QToolButton,
    QScrollArea,
    QFrame,
    QDialog,
    QCheckBox,
)

# Importing the renderer wires up the whole vendored widget stack and exposes
# the font install + monkeypatch helpers we reuse. It is imported AFTER the Qt
# platform is pinned above so the composed window is interactive, not offscreen.
from sharpmod import render as R  # noqa: E402
from sharpmod.viz.SPCWindow import compose_window  # noqa: E402
from sharpmod.io import uwyo_catalog  # noqa: E402
from sharpmod.io.uwyo_decoder import (  # noqa: E402
    StationLookupError,
    UWyo_Decoder,
    UWyoError,
)

__all__ = ["PickerWindow", "compose_interactive", "main"]

APP_NAME = "SHARPpy Reimagined"
APP_VERSION = "0.1"

#: One-line interaction hints shown in the sounding-window tip bar.
TIP_LINE = ("Tips:  right-click = readout / modify   \u00b7   drag points to edit"
            "   \u00b7   wheel = zoom   \u00b7   \u2190\u2009/\u2009\u2192 = step "
            "time   \u00b7   Ctrl+E = export")

#: Full interaction guide (shared by the picker Help menu and the in-window
#: "Full guide" button).
CONTROLS_HTML = (
    "<b>Sounding window controls</b><br><br>"
    "<b>Right-click the Skew-T</b> \u2014 readout cursor, Modify Surface, "
    "lift a parcel, reset.<br>"
    "<b>Click + drag</b> a temperature / dewpoint / wind point to edit the "
    "profile (indices recalculate live).<br>"
    "<b>Mouse wheel</b> \u2014 zoom the Skew-T or hodograph.<br>"
    "<b>Right-click the hodograph</b> \u2014 re-center it; "
    "<b>double-click</b> the RM / LM markers to set the storm motion.<br>"
    "<b>Double-click the lower-left inset</b> \u2014 swap lifted parcels.<br><br>"
    "<b>Keys:</b> \u2190/\u2192 step in time, \u2191/\u2193 change ensemble "
    "member, <b>Space</b> swap focus, <b>I</b> interpolate, "
    "<b>C</b> collect observed, <b>W</b> back to the picker.<br><br>"
    "<b>Export:</b> the <b>Export</b> menu saves a PNG image (<b>Ctrl+E</b>) or "
    "an SPC tabular text file that loads back into the app "
    "(File \u2192 Save Image / Save Text also work).")


def _show_controls_dialog(parent) -> None:
    """Show the shared interaction guide as a message box."""
    QMessageBox.information(parent, "Sounding Window Controls", CONTROLS_HTML)

#: UWyo radiosonde launch hours offered in the cycle picker.
SYNOPTIC_HOURS = (0, 6, 12, 18)

#: How many recent files / stations to remember.
MAX_RECENTS = 8

# Guard so the one-time vendored-widget monkeypatches are installed once.
_setup_done = False


def _most_recent_synoptic() -> tuple[QDate, int]:
    """Return the most recent (00Z/12Z) sounding time likely to be available.

    Radiosondes are launched at 00Z and 12Z with a reporting lag, so this picks
    the latest of those that is safely in the past (UTC), returning the date and
    hour to pre-select in the picker. The user can still choose any date/cycle.
    """
    now = datetime.now(timezone.utc)
    if now.hour >= 13:
        d, h = now, 12
    elif now.hour >= 1:
        d, h = now, 0
    else:  # just after 00Z -- yesterday's 12Z is the safe most-recent
        d, h = now - timedelta(days=1), 12
    return QDate(d.year, d.month, d.day), h


# ===========================================================================
# Shared window composition (reuses the renderer setup, but shows on screen)
# ===========================================================================
def _ensure_setup(app) -> None:
    """Install fonts + the renderer's vendored-widget monkeypatches once.

    Mirrors the sequence :func:`sharpmod.render.render` runs before composing a
    window so the interactive window looks identical to the rendered PNG:
    bundled fonts, title/heading overrides, custom wind barbs, the 0-500 m
    hodograph band, the Effective-Layer STP tweaks, and the thermo/kinematics
    row-spacing patch. Idempotent -- safe to call before every sounding.
    """
    global _setup_done
    # Fonts must be (re)asserted on the live QApplication; cheap + idempotent.
    R.install_font(app)
    if _setup_done:
        return
    R._apply_sars_match_color()
    R._install_title_override()
    R._install_skewt_title_shrink()
    R._install_title_top()
    R._install_custom_barbs()
    R._install_hodo_0500()
    R._install_hodo_zoom()
    R._install_skewt_level_labels_fit()
    R._install_stp_condense()
    R._install_stp_label_rename()
    R._install_stp_xlabel_colors()
    R._install_stp_bottom_margin()
    R._install_stp_box_shrink()
    # Cap the wind-speed + temp-advection strip fonts so their titles/axis
    # labels stay tidy at any strip width (matches the PNG render path).
    R._install_speed_title_cap()
    R._install_advection_font_cap()
    # Size the skew-T mixing-ratio + surface-value label masks to the font so
    # background lines stop bleeding through the (wider-font) digits.
    R._install_skewt_mixratio_mask()
    R._install_skewt_sfc_label_mask()
    # Redraw the white skew-T outline on top so label masks never gap it.
    R._install_skewt_frame_ontop()
    # Keep the bottom isotherm labels inside the widget (no bottom clip).
    R._install_skewt_isotherm_label_fit()
    # Keep the Storm Slinky title inside the widget (no descender clip).
    R._install_slinky_title_fit()
    R._apply_table_spacing_patch()
    _setup_done = True


def _fill_metadata(prof_col, stn_id, model=None, run=None, loc=None) -> None:
    """Fill the metadata the title/header rendering dereferences.

    A faithful copy of the metadata block in :func:`sharpmod.render.render`, so
    the interactive window's heading matches the rendered PNG. Never clobbers a
    value the decoder already worked out.
    """
    if model is not None:
        prof_col.setMeta("model", model)
    if run is not None:
        prof_col.setMeta("run", run)
    if loc is not None:
        prof_col.setMeta("loc", loc)

    has = lambda k: k in prof_col._meta  # noqa: E731
    base = prof_col.getMeta("base_time") if has("base_time") \
        else prof_col.getCurrentDate()
    observed = prof_col.getMeta("observed") if has("observed") else True
    if not has("loc"):
        prof_col.setMeta("loc", stn_id)
    if not has("run"):
        prof_col.setMeta("run", base)
    if not has("model"):
        prof_col.setMeta("model", "Archive" if observed else "Model")


def compose_interactive(config, prof_col, controller, *, stn_id=None,
                        model=None, run=None, loc=None):
    """Compose and show a fully interactive SPC-style sounding window.

    Builds the *real* upstream :class:`sharppy.viz.SPCWindow.SPCWindow` (a
    top-level ``QMainWindow`` that ships every interactive behaviour -- readout
    cursor, mouse-wheel zoom, click-drag profile editing, storm-motion vectors,
    the boundary cursor, parcel selection, Save Image / Save Text, and the
    arrow/space/I/C/W key bindings) with ``controller`` as its Qt parent, so the
    ``W`` key refocuses the picker and Preferences routes to
    ``controller.preferencesbox``.

    The same font install, vendored-widget monkeypatches, mounted
    derived-parameter panels, layout-compensation passes and canvas grow that
    the PNG renderer applies are reused verbatim, so the on-screen window
    matches the rendered image. Returns the composed ``SPCWindow`` (already
    shown). The caller must retain both it and ``controller``.
    """
    app = QApplication.instance()
    _ensure_setup(app)

    _fill_metadata(prof_col, stn_id, model=model, run=run, loc=loc)

    # mount=True appends the derived-parameter family panels into the vendored
    # index band and attaches the skew-T HGZ overlay; controller=picker wires
    # the config/preferences/focus contract to the picker window.
    win, _ = compose_window(config, prof_col, mount=True, controller=controller)

    # The vendored SPCWindow.__initUI calls self.show() as soon as it is
    # constructed, so an empty white window flashes on screen while we still
    # have to add the profile metadata, run layout compensation, grow the
    # canvas, and embed it in the scaling graphics view. Hide it now and only
    # reveal it once fully composed + painted (see the showNormal() at the end),
    # so the user sees the finished sounding appear in one step -- no white
    # flash, no half-built window.
    win.hide()

    # Rebrand the vendored window title + top-right version label.
    try:
        loc_lbl = prof_col.getMeta("loc")
    except Exception:
        loc_lbl = stn_id
    win.setWindowTitle(f"{APP_NAME} \u2014 {loc_lbl or 'Sounding'}")
    try:
        for _lbl in win.findChildren(QLabel):
            if _lbl.text().startswith("SHARPpy"):
                _lbl.setText(f"{APP_NAME} v{APP_VERSION}")
    except Exception:
        pass

    # Level the top frame so the upper-right panel band lines up with the
    # skew-T top border (and the brand label lines up with the skew-T title) --
    # identical to the PNG render path.
    R.align_top_row(win)

    # The five legacy layout-compensation passes, then grow the canvas so the
    # family panels + barbs fit -- identical to the PNG path.
    R.apply_layout_compensation(win.spc_widget)
    R._grow_for_family_panels(win)
    # Grow the canvas the same way the PNG renderer does, so the interactive
    # window's skew-T / hodograph sizing matches the rendered image.
    R.enlarge_canvas(win)
    for _ in range(6):
        app.processEvents()

    # A discoverable Export menu with sensible default filenames/locations
    # (the vendored Save Image/Text default to a hidden temp dir with no name).
    _install_export_menu(win, prof_col, controller)

    # Restore the legacy "Show Parcels" double-click on the parcel inset (the
    # fork replaces the vendored parcel panel with its IndexBoard, so the
    # vendored double-click is otherwise unreachable).
    _install_parcel_selector(win)

    # Fill the top strip with a compact interaction tip bar (also the on-screen
    # how-to). Done last so it wraps the fully composed spc_widget.
    _install_tip_bar(win, controller)

    # Keep the sounding at its natural (CLI-identical) size inside a scroll area
    # and size the window to fit the screen. Without this, a window taller than
    # the screen work area gets clamped by the OS and the vendored panel layout
    # squishes the Skew-T (the family-panel minimum heights hold, so the plot
    # collapses vertically). The scroll area guarantees no squish.
    _fit_window_to_screen(app, win)

    win.showNormal()
    win.raise_()
    win.activateWindow()
    return win


def _fit_window_to_screen(app, win) -> None:
    """Show the sounding at its natural, PNG-identical size (crisp, no squish).

    The vendored ``spc_widget`` is pinned to its composed (natural) size and
    hosted in a non-resizing :class:`QScrollArea`. At natural size the
    interactive window is pixel-identical to the headless render -- crisp text,
    correct Skew-T / hodograph proportions. The window opens at natural size,
    clamped only to the available screen work area; on a screen large enough
    (the common case) it fills exactly with no scrollbars, and on a smaller
    screen a scrollbar appears instead of the plot being squished or blurred.
    """
    sw = getattr(win, "spc_widget", None)
    if sw is None:
        return
    nat_w, nat_h = sw.width(), sw.height()
    if nat_w <= 1 or nat_h <= 1:
        return
    try:
        # Pin the sounding to its natural size so nothing can squish it.
        sw.setMinimumSize(nat_w, nat_h)

        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignCenter)
        scroll.setStyleSheet("QScrollArea{background:#000000;border:0;}")
        scroll.setWidget(sw)
        win.setCentralWidget(scroll)

        mb_h = win.menuBar().height() or 0
        screen = app.primaryScreen().availableGeometry()
        max_w = int(screen.width() * 0.98)
        max_h = int(screen.height() * 0.96)
        # +2 for the viewport frame; scrollbars appear only when truly needed.
        win_w = min(nat_w + 2, max_w)
        win_h = min(nat_h + mb_h + 2, max_h)
        win.resize(win_w, win_h)
    except Exception:
        # Never let the fit/scroll wrap block the interactive window.
        pass


def _install_tip_bar(win, controller) -> None:
    """Show the interaction tips *inside* the menu-bar row (no extra band).

    Rather than adding a second bar under the menu bar (which stacks two strips
    and leaves the menu bar's empty area looking like wasted black space), the
    tips + "Full guide" + dismiss controls are placed as a **corner widget** on
    the right of the vendored ``SPCWindow`` menu bar. This fills the otherwise
    empty menu-bar space, keeps the top to a single row, and leaves the
    ``spc_widget`` (and therefore exports) completely untouched.

    A per-user "hide tips" preference is honored/updated via the controller's
    ``QSettings`` so the tips can be permanently dismissed.
    """
    settings = getattr(controller, "_settings", None)
    hidden = settings is not None and settings.value("hide_tips", False, bool)

    try:
        menubar = win.menuBar()

        tips = QWidget()
        h = QHBoxLayout(tips)
        h.setContentsMargins(6, 0, 8, 0)
        h.setSpacing(8)

        lbl = QLabel(TIP_LINE)
        lbl.setStyleSheet("color:#9fb6d6; font-size:11px;")
        lbl.setWordWrap(False)

        guide_btn = QToolButton()
        guide_btn.setText("Full guide")
        guide_btn.setToolTip("Show all sounding-window controls")
        guide_btn.setAutoRaise(True)
        guide_btn.setStyleSheet("QToolButton{color:#cfe0f5;}")
        guide_btn.clicked.connect(lambda: _show_controls_dialog(win))

        close_btn = QToolButton()
        close_btn.setText("\u2715")
        close_btn.setToolTip("Hide these tips")
        close_btn.setAutoRaise(True)
        close_btn.setStyleSheet("QToolButton{color:#9fb6d6;}")

        h.addWidget(lbl)
        h.addWidget(guide_btn)
        h.addWidget(close_btn)

        menubar.setCornerWidget(tips, Qt.TopRightCorner)
        tips.setVisible(not hidden)

        def _dismiss():
            tips.setVisible(False)
            if settings is not None:
                settings.setValue("hide_tips", True)

        close_btn.clicked.connect(_dismiss)
    except Exception:
        # A tip hiccup must never block the interactive window.
        pass


#: Ordered parcel options for the Show Parcels dialog: (label, key).
_PARCEL_OPTIONS = [
    ("Surface-Based Parcel", "SFC"),
    ("100 mb Mixed Layer Parcel", "ML"),
    ("Forecast Surface Parcel", "FCST"),
    ("Most Unstable Parcel", "MU"),
    ("Effective Inflow Layer Parcel", "EFF"),
    ("User Defined Parcel", "USER"),
]

#: Parcel key -> Profile attribute (matches sharpmod.viz.index_board.PCL_ATTR).
_PARCEL_ATTR = {"SFC": "sfcpcl", "ML": "mlpcl", "FCST": "fcstpcl",
                "MU": "mupcl", "EFF": "effpcl", "USER": "usrpcl"}


class _ParcelDialog(QDialog):
    """A reliable, self-contained "Show Parcels" chooser.

    Replaces the vendored ``SelectParcels`` (whose checkbox state is unreliable
    in our composed window and whose dark-theme indicators are invisible). It
    pre-checks the currently displayed parcels, enforces exactly four, and on
    OK invokes ``on_apply(list_of_keys)`` -- which updates the Skew-T parcel
    trace, the storm slinky, and the IndexBoard.
    """

    _QSS = (
        "QDialog{background-color:#1b2436;color:#e8eef7;}"
        "QLabel{color:#c8d4e6;}"
        "QCheckBox{color:#e8eef7;padding:4px;spacing:9px;}"
        "QCheckBox::indicator{width:16px;height:16px;}"
        "QCheckBox::indicator:unchecked{border:1px solid #8a97ab;"
        "background:#0d1220;border-radius:3px;}"
        "QCheckBox::indicator:checked{border:1px solid #4da3ff;"
        "background:#4da3ff;border-radius:3px;}"
        "QPushButton{background:#2c3e55;color:#ffffff;padding:6px 18px;"
        "border:1px solid #46607f;border-radius:4px;}"
        "QPushButton:hover{background:#37507a;}"
    )

    def __init__(self, current_keys, on_apply, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Show Parcels")
        self.setStyleSheet(self._QSS)
        self._on_apply = on_apply
        self._boxes = {}

        layout = QVBoxLayout(self)
        hint = QLabel("Choose exactly four parcels to display:")
        layout.addWidget(hint)
        for label, key in _PARCEL_OPTIONS:
            cb = QCheckBox(label)
            cb.setChecked(key in current_keys)
            self._boxes[key] = cb
            layout.addWidget(cb)

        self._count_lbl = QLabel("")
        layout.addWidget(self._count_lbl)
        for cb in self._boxes.values():
            cb.stateChanged.connect(self._update_count)

        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("OK")
        ok.clicked.connect(self._apply)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        layout.addLayout(row)
        self._update_count()

    def _checked_keys(self):
        return [k for label, k in _PARCEL_OPTIONS if self._boxes[k].isChecked()]

    def _update_count(self):
        n = len(self._checked_keys())
        self._count_lbl.setText(f"{n} of 4 selected")
        self._count_lbl.setStyleSheet(
            "color:#7fd08a;" if n == 4 else "color:#e0a030;")

    def _apply(self):
        keys = self._checked_keys()
        if len(keys) != 4:
            QMessageBox.information(
                self, "Show Parcels",
                "Select exactly four parcels to display "
                f"(currently {len(keys)}).")
            return
        try:
            self._on_apply(keys)
        except Exception:
            pass
        self.accept()


def _install_parcel_selector(win) -> None:
    """Restore the legacy "Show Parcels" double-click on the parcel inset.

    The fork hides the vendored ``plotText`` parcel inset (which carried the
    double-click parcel selector) and shows its own :class:`IndexBoard`
    instead. This reconnects the behaviour with a robust, self-contained dialog
    (:class:`_ParcelDialog`): double-clicking the IndexBoard's parcel column
    opens it, pre-checked to the current parcels; choosing four and pressing OK
    updates the Skew-T parcel trace, the storm slinky, and the IndexBoard rows
    (including Effective Inflow / User Defined).
    """
    sw = getattr(win, "spc_widget", None)
    if sw is None:
        return
    board = getattr(sw, "index_board", None)
    conv = getattr(sw, "convective", None)
    if board is None or conv is None:
        return
    try:
        if getattr(conv, "pcl_types", None):
            board.pcl_types = list(conv.pcl_types)

        def _apply(keys):
            # 1. Record the new selection everywhere it is read.
            conv.pcl_types = list(keys)
            try:
                conv.skewt_pcl = 0
            except Exception:
                pass
            board.pcl_types = list(keys)
            # 2. Drive the Skew-T + storm slinky to the highlighted (first)
            #    parcel via the vendored update path.
            parcels = getattr(conv, "parcels", {}) or {}
            first = parcels.get(keys[0])
            if first is not None and hasattr(sw, "updateParcel"):
                sw.updateParcel(first)
            # 3. Redraw the IndexBoard so its parcel rows match the selection.
            if board.sp is not None:
                board.setData(board.sp, board.dp)

        def _open_dialog():
            cur = list(getattr(conv, "pcl_types", None)
                       or ["SFC", "ML", "FCST", "MU"])
            dlg = _ParcelDialog(cur, _apply, parent=win)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()

        def _select_parcel(key):
            # Single-click a parcel row -> draw that parcel's trace on the
            # Skew-T (+ storm slinky), like legacy SHARPpy.
            parcels = getattr(conv, "parcels", {}) or {}
            pcl = parcels.get(key)
            if pcl is not None and hasattr(sw, "updateParcel"):
                try:
                    sw.updateParcel(pcl)
                except Exception:
                    pass

        board.parcelDialogRequested.connect(_open_dialog)
        board.parcelClicked.connect(_select_parcel)
    except Exception:
        # Parcel-selector wiring must never block the interactive window.
        pass


def _default_export_basename(prof_col) -> str:
    """Build a friendly export filename stem like ``OUN_2024052000Z``."""
    base = "sounding"
    try:
        loc = prof_col.getMeta("loc") or "sounding"
        run = prof_col.getMeta("run")
        base = f"{loc}_{run:%Y%m%d%H}Z" if run is not None else str(loc)
    except Exception:
        pass
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", base) or "sounding"


def _install_export_menu(win, prof_col, controller) -> None:
    """Add an ``Export`` menu (image / text) to a composed sounding window.

    Improves on the vendored Save Image / Save Text by pre-filling a sensible
    filename (station + cycle) and defaulting to the user's Desktop (or last
    used export folder), so exports land somewhere findable. The image grab
    captures the whole window -- including the mounted derived-parameter panels
    -- and the text export writes the focused profile as an SPC tabular file
    that loads back into the app.
    """
    settings = getattr(controller, "_settings", None)
    base = _default_export_basename(prof_col)

    def _start_dir() -> str:
        if settings is not None:
            d = settings.value("export_dir", "", str)
            if d and os.path.isdir(d):
                return d
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        return desktop if os.path.isdir(desktop) else os.path.expanduser("~")

    def _remember(path: str) -> None:
        if settings is not None:
            settings.setValue("export_dir", os.path.dirname(path))

    def export_image() -> None:
        start = os.path.join(_start_dir(), base + ".png")
        fn, _ok = QFileDialog.getSaveFileName(
            win, "Export Sounding Image", start, "PNG image (*.png)")
        if fn:
            if not fn.lower().endswith(".png"):
                fn += ".png"
            win.spc_widget.pixmapToFile(fn)
            _remember(fn)

    def export_text() -> None:
        start = os.path.join(_start_dir(), base + ".txt")
        fn, _ok = QFileDialog.getSaveFileName(
            win, "Export Sounding Text (SPC tabular)", start,
            "SPC text (*.txt)")
        if fn:
            if not fn.lower().endswith(".txt"):
                fn += ".txt"
            try:
                win.spc_widget.default_prof.toFile(fn)
                _remember(fn)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(win, APP_NAME,
                                    f"Could not export text:\n{exc}")

    try:
        menu = win.menuBar().addMenu("Export")
        act_img = QAction("Export Image (PNG)\u2026", win)
        act_img.setShortcut("Ctrl+E")
        act_img.triggered.connect(export_image)
        menu.addAction(act_img)
        act_txt = QAction("Export Text (SPC tabular)\u2026", win)
        act_txt.triggered.connect(export_text)
        menu.addAction(act_txt)
    except Exception:
        # Never let an export-menu hiccup block the interactive window.
        pass


# ===========================================================================
# UWyo fetch worker (keeps the picker UI responsive during the network call)
# ===========================================================================
class _FetchWorker(QThread):
    """Fetch a UWyo sounding off the UI thread and write a temp ``.npz``.

    Emits :attr:`finished_ok` with ``(npz_path, station_meta, when)`` on
    success or :attr:`failed` with a human-readable message on any error.
    """

    finished_ok = Signal(str, object, object)
    failed = Signal(str)

    def __init__(self, station_query: str, when_utc: datetime, parent=None):
        super().__init__(parent)
        self._query = station_query
        self._when = when_utc

    def run(self):  # noqa: D401 - QThread entry point
        try:
            decoder = UWyo_Decoder(full_catalog=True)
            meta = decoder.resolve_station(self._query)
            prof = decoder.fetch(meta.id, self._when)
        except StationLookupError as exc:
            self.failed.emit(f"Station lookup failed: {exc}")
            return
        except UWyoError as exc:
            self.failed.emit(f"UWyo fetch failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - surface any error to the UI
            self.failed.emit(f"Unexpected error: {exc}")
            return

        try:
            # Reuse the tested UWyo -> .npz writer so the interactive path and
            # the CLI/PNG path share one output format + metadata.
            from sharpmod.tools.uwyo_sounding import _write_npz

            prof_meta = dict(getattr(prof, "meta", {}) or {})
            if prof_meta.get("lat") != prof_meta.get("lat") or "lat" not in prof_meta:
                prof_meta["lat"] = meta.lat
            if prof_meta.get("lon") != prof_meta.get("lon") or "lon" not in prof_meta:
                prof_meta["lon"] = meta.lon
            prof_meta.setdefault("valid", self._when)

            loc = meta.name.split(",")[0].split()[0]
            fd, npz_path = tempfile.mkstemp(
                prefix=f"uwyo_{meta.id}_{self._when:%Y%m%d%H}_", suffix=".npz")
            os.close(fd)
            _write_npz(prof, npz_path, prof_meta, loc)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Could not save fetched sounding: {exc}")
            return

        self.finished_ok.emit(npz_path, meta, self._when)


# ===========================================================================
# Station map widget (legacy-SHARPpy style clickable picker map)
# ===========================================================================
def _load_basemap() -> dict:
    """Load the bundled HD basemap layers for the station map.

    Returns a dict ``{"coastline": [...], "countries": [...], "states": [...]}``
    where each value is a list of ``[lon, lat]`` polylines. Resolved
    package-relative via :mod:`importlib.resources`; prefers the multi-layer
    ``basemap.json`` and falls back to the older single-layer
    ``coastlines.json`` (or empty layers) so the map always renders.
    """
    import json
    try:
        from importlib.resources import files
        pkg = files("sharpmod.resources")
        res = pkg.joinpath("basemap.json")
        data = json.loads(res.read_text(encoding="utf-8"))
        return {
            "coastline": data.get("coastline", []),
            "countries": data.get("countries", []),
            "states": data.get("states", []),
        }
    except Exception:
        pass
    try:
        from importlib.resources import files
        res = files("sharpmod.resources").joinpath("coastlines.json")
        data = json.loads(res.read_text(encoding="utf-8"))
        return {"coastline": data.get("polylines", []),
                "countries": [], "states": []}
    except Exception:
        return {"coastline": [], "countries": [], "states": []}


#: Named map extents for the "Map Area" selector: (lon0, lon1, lat0, lat1).
MAP_AREAS: dict[str, tuple[float, float, float, float]] = {
    "United States (CONUS)": (-125.0, -66.0, 23.0, 50.0),
    "North America": (-170.0, -50.0, 8.0, 75.0),
    "Northern Hemisphere": (-180.0, 180.0, 0.0, 88.0),
    "Southern Hemisphere": (-180.0, 180.0, -88.0, 0.0),
    "Europe": (-15.0, 45.0, 34.0, 72.0),
    "Australia / Oceania": (110.0, 180.0, -50.0, 5.0),
    "Tropics": (-180.0, 180.0, -30.0, 30.0),
    "World": (-180.0, 180.0, -85.0, 85.0),
}


class StationMapWidget(QWidget):
    """A clickable map of sounding stations (the legacy SHARPpy picker map).

    Plots every station as a dot over an HD coastline + border basemap. The
    projection is an equirectangular with a cosine-of-latitude longitude
    correction and a single uniform scale (letterbox fit), so land shapes keep
    their real proportions and never stretch as the window is resized.

    Hovering shows the cursor lat/lon and the nearest station; clicking selects
    the nearest station; double-clicking activates it (generate). The mouse
    wheel zooms about the cursor and dragging pans. The visible extent can be
    set to a named region via :meth:`set_area`. The basemap is rasterized once
    per extent/size into a cached pixmap so hover and selection stay smooth.
    """

    stationSelected = Signal(str)   # station id (single click / hover-pick)
    stationActivated = Signal(str)  # station id (double click -> generate)

    def __init__(self, stations, parent=None):
        super().__init__(parent)
        self._stations = list(stations)
        self._layers = self._prep_layers(_load_basemap())
        self._area_name = "United States (CONUS)"
        self._lon0, self._lon1, self._lat0, self._lat1 = MAP_AREAS[
            self._area_name]
        self._selected_id: str | None = None
        self._hover_id: str | None = None
        self._hover_lonlat: tuple[float, float] | None = None
        self._drag_last: QPointF | None = None
        self._dragged = False
        self._basemap_cache = None
        self._cache_key = None
        self.setMinimumSize(QSize(520, 380))
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- data prep ----------------------------------------------------------- #
    @staticmethod
    def _prep_layers(basemap: dict) -> dict:
        """Precompute each polyline's lon/lat bounding box for fast clipping."""
        out = {}
        for name, lines in basemap.items():
            prepped = []
            for pts in lines:
                if len(pts) < 2:
                    continue
                lons = [p[0] for p in pts]
                lats = [p[1] for p in pts]
                bbox = (min(lons), max(lons), min(lats), max(lats))
                prepped.append((bbox, pts))
            out[name] = prepped
        return out

    # -- public API ---------------------------------------------------------- #
    def set_area(self, name: str) -> None:
        if name in MAP_AREAS:
            self._area_name = name
            self._lon0, self._lon1, self._lat0, self._lat1 = MAP_AREAS[name]
            self._invalidate()

    def reset_view(self) -> None:
        """Snap back to the current named region's default extent."""
        self.set_area(self._area_name)

    def zoom(self, factor: float) -> None:
        """Zoom about the map center (``factor`` < 1 zooms in)."""
        clon = (self._lon0 + self._lon1) / 2.0
        clat = (self._lat0 + self._lat1) / 2.0
        self._lon0 = clon + (self._lon0 - clon) * factor
        self._lon1 = clon + (self._lon1 - clon) * factor
        self._lat0 = clat + (self._lat0 - clat) * factor
        self._lat1 = clat + (self._lat1 - clat) * factor
        self._invalidate()

    def set_selected(self, sid: str | None) -> None:
        self._selected_id = sid
        self.update()

    def center_on(self, sid: str) -> None:
        """Pan the view so ``sid`` is centred (keeps the current zoom span)."""
        st = self._station(sid)
        if st is None:
            return
        span_lon = (self._lon1 - self._lon0) / 2.0
        span_lat = (self._lat1 - self._lat0) / 2.0
        self._lon0, self._lon1 = st["lon"] - span_lon, st["lon"] + span_lon
        self._lat0, self._lat1 = st["lat"] - span_lat, st["lat"] + span_lat
        self._invalidate()

    def _invalidate(self) -> None:
        self._basemap_cache = None
        self.update()

    # -- projection (aspect-correct, letterboxed) ---------------------------- #
    def _proj(self) -> tuple:
        """Return the projection params ``(k, scale, offx, offy, X0, Y1)``."""
        import math
        w = max(1, self.width())
        h = max(1, self.height())
        lat_ref = math.radians((self._lat0 + self._lat1) / 2.0)
        k = max(0.05, math.cos(lat_ref))
        x0 = self._lon0 * k
        x1 = self._lon1 * k
        box_w = max(1e-6, x1 - x0)
        box_h = max(1e-6, self._lat1 - self._lat0)
        scale = min(w / box_w, h / box_h)
        offx = (w - box_w * scale) / 2.0
        offy = (h - box_h * scale) / 2.0
        return k, scale, offx, offy, x0, self._lat1

    def _to_px(self, lon: float, lat: float, p=None) -> QPointF:
        k, scale, offx, offy, x0, y1 = p or self._proj()
        return QPointF(offx + (lon * k - x0) * scale,
                       offy + (y1 - lat) * scale)

    def _to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        k, scale, offx, offy, x0, y1 = self._proj()
        lon = ((x - offx) / scale + x0) / k
        lat = y1 - (y - offy) / scale
        return lon, lat

    def _station(self, sid):
        for s in self._stations:
            if s["id"] == sid:
                return s
        return None

    def _nearest(self, x: float, y: float, max_px: float = 12.0):
        p = self._proj()
        w, h = self.width(), self.height()
        best, best_d2 = None, max_px * max_px
        for s in self._stations:
            pt = self._to_px(s["lon"], s["lat"], p)
            if pt.x() < -5 or pt.y() < -5 or pt.x() > w + 5 or pt.y() > h + 5:
                continue
            d2 = (pt.x() - x) ** 2 + (pt.y() - y) ** 2
            if d2 <= best_d2:
                best, best_d2 = s, d2
        return best

    # -- basemap raster (cached per extent + size) --------------------------- #
    def _basemap_pixmap(self):
        key = (self.width(), self.height(),
               round(self._lon0, 4), round(self._lon1, 4),
               round(self._lat0, 4), round(self._lat1, 4))
        if self._basemap_cache is not None and self._cache_key == key:
            return self._basemap_cache

        pm = QPixmap(self.size())
        pm.fill(QColor("#05070d"))
        qp = QPainter(pm)
        qp.setRenderHint(QPainter.Antialiasing, True)
        p = self._proj()
        self._draw_graticule(qp, p)
        # Draw borders first (dim), coastline last (bright) so it reads on top.
        self._draw_layer(qp, self._layers.get("states", []), "#2c3e55", 1.0, p)
        self._draw_layer(qp, self._layers.get("countries", []), "#54697f", 1.0, p)
        self._draw_layer(qp, self._layers.get("coastline", []), "#a9c0dc", 1.4, p)
        qp.end()

        self._basemap_cache = pm
        self._cache_key = key
        return pm

    def _draw_graticule(self, qp, p) -> None:
        span = self._lon1 - self._lon0
        step = 5 if span <= 40 else (10 if span <= 90 else
                                     (20 if span <= 200 else 30))
        grid = QPen(QColor("#141d2e"), 1)
        label = QColor("#3a4a63")
        qp.setFont(QFont("Helvetica", 8))
        lon = int(self._lon0 // step * step)
        while lon <= self._lon1:
            qp.setPen(grid)
            qp.drawLine(self._to_px(lon, self._lat0, p),
                        self._to_px(lon, self._lat1, p))
            qp.setPen(QPen(label))
            top = self._to_px(lon, self._lat1, p)
            qp.drawText(QRectF(top.x() - 24, 2, 48, 12),
                        Qt.AlignCenter, self._fmt_lon(lon))
            lon += step
        lat = int(self._lat0 // step * step)
        while lat <= self._lat1:
            qp.setPen(grid)
            qp.drawLine(self._to_px(self._lon0, lat, p),
                        self._to_px(self._lon1, lat, p))
            qp.setPen(QPen(label))
            left = self._to_px(self._lon0, lat, p)
            qp.drawText(QRectF(3, left.y() - 7, 34, 12),
                        Qt.AlignLeft | Qt.AlignVCenter, self._fmt_lat(lat))
            lat += step

    @staticmethod
    def _fmt_lat(lat: int) -> str:
        return f"{abs(lat)}\u00b0{'N' if lat >= 0 else 'S'}"

    @staticmethod
    def _fmt_lon(lon: int) -> str:
        lon = ((lon + 180) % 360) - 180  # normalize to [-180, 180)
        return f"{abs(lon)}\u00b0{'E' if lon >= 0 else 'W'}"

    def _draw_layer(self, qp, prepped, color, width, p) -> None:
        if not prepped:
            return
        qp.setPen(QPen(QColor(color), width))
        # Pad the clip window by one extent-span so partially visible lines draw.
        lon_pad = (self._lon1 - self._lon0) * 0.15
        lat_pad = (self._lat1 - self._lat0) * 0.15
        vlon0, vlon1 = self._lon0 - lon_pad, self._lon1 + lon_pad
        vlat0, vlat1 = self._lat0 - lat_pad, self._lat1 + lat_pad
        for (blo0, blo1, bla0, bla1), pts in prepped:
            if blo1 < vlon0 or blo0 > vlon1 or bla1 < vlat0 or bla0 > vlat1:
                continue  # bbox entirely outside the view
            poly = QPolygonF()
            for lon, lat in pts:
                poly.append(self._to_px(lon, lat, p))
            qp.drawPolyline(poly)

    # -- painting ------------------------------------------------------------ #
    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        qp = QPainter(self)
        qp.drawPixmap(0, 0, self._basemap_pixmap())
        qp.setRenderHint(QPainter.Antialiasing, True)
        self._draw_stations(qp, self._proj())
        self._draw_readout(qp)
        qp.end()

    def _draw_stations(self, qp, p) -> None:
        r = 3.0
        w, h = self.width(), self.height()
        for s in self._stations:
            pt = self._to_px(s["lon"], s["lat"], p)
            if pt.x() < 0 or pt.y() < 0 or pt.x() > w or pt.y() > h:
                continue
            sid = s["id"]
            if sid == self._selected_id:
                qp.setBrush(QBrush(QColor("#ffd000")))
                qp.setPen(QPen(QColor("#ffffff"), 1.5))
                qp.drawEllipse(pt, r + 2.5, r + 2.5)
            elif sid == self._hover_id:
                qp.setBrush(QBrush(QColor("#ff8a8a")))
                qp.setPen(QPen(QColor("#ffffff"), 1))
                qp.drawEllipse(pt, r + 1.5, r + 1.5)
            else:
                qp.setBrush(QBrush(QColor("#e03030")))
                qp.setPen(QPen(QColor("#7a1414"), 1))
                qp.drawEllipse(pt, r, r)

    def _draw_readout(self, qp) -> None:
        lines = []
        if self._hover_lonlat is not None:
            lon, lat = self._hover_lonlat
            lines.append(f"{lat:.3f}, {lon:.3f}")
        if self._hover_id is not None:
            st = self._station(self._hover_id)
            if st is not None:
                lines.append(f"{st['id']}  {st['name']}")
        if not lines:
            return
        qp.setFont(QFont("Helvetica", 10))
        # Shadowed text for legibility over any basemap color.
        y = 18
        for text in lines:
            rect = QRectF(8, y - 14, self.width() - 16, 18)
            qp.setPen(QPen(QColor("#000000")))
            qp.drawText(rect.translated(1, 1),
                        Qt.AlignLeft | Qt.AlignVCenter, text)
            qp.setPen(QPen(QColor("#eef2f8")))
            qp.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
            y += 18

    # -- interaction --------------------------------------------------------- #
    @staticmethod
    def _pos(event) -> QPointF:
        return event.position() if hasattr(event, "position") \
            else QPointF(event.x(), event.y())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = self._pos(event)
        if self._drag_last is not None and (event.buttons() & Qt.LeftButton):
            # Pan: convert the pixel delta to a lon/lat shift via the transform.
            k, scale, _ox, _oy, _x0, _y1 = self._proj()
            dx = pos.x() - self._drag_last.x()
            dy = pos.y() - self._drag_last.y()
            self._dragged = self._dragged or abs(dx) + abs(dy) > 3
            dlon = -dx / scale / k
            dlat = dy / scale
            self._lon0 += dlon
            self._lon1 += dlon
            self._lat0 += dlat
            self._lat1 += dlat
            self._drag_last = pos
            self._invalidate()
            return
        self._hover_lonlat = self._to_lonlat(pos.x(), pos.y())
        near = self._nearest(pos.x(), pos.y())
        self._hover_id = near["id"] if near else None
        self.setToolTip(f"{near['id']}  {near['name']}" if near else "")
        self.update()  # cheap: basemap is cached, only overlay repaints

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_last = self._pos(event)
            self._dragged = False

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        pos = self._pos(event)
        was_drag = self._dragged
        self._drag_last = None
        self._dragged = False
        if was_drag:
            return
        near = self._nearest(pos.x(), pos.y())
        if near is not None:
            self._selected_id = near["id"]
            self.stationSelected.emit(near["id"])
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pos = self._pos(event)
        near = self._nearest(pos.x(), pos.y())
        if near is not None:
            self._selected_id = near["id"]
            self.stationSelected.emit(near["id"])
            self.stationActivated.emit(near["id"])
            self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 0.83 if delta > 0 else 1.20  # wheel up = zoom in
        pos = self._pos(event)
        clon, clat = self._to_lonlat(pos.x(), pos.y())
        self._lon0 = clon + (self._lon0 - clon) * factor
        self._lon1 = clon + (self._lon1 - clon) * factor
        self._lat0 = clat + (self._lat0 - clat) * factor
        self._lat1 = clat + (self._lat1 - clat) * factor
        self._invalidate()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._invalidate()
        super().resizeEvent(event)


# ===========================================================================
# Picker / launcher window
# ===========================================================================
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
        self.setAcceptDrops(True)  # drag a sounding file onto the window

        # Keep every opened sounding window alive. Each vendored ``SPCWindow`` is
        # a top-level window parented to this picker, but we also hold a Python
        # reference so it is never garbage-collected out from under Qt.
        self._viewers: list = []
        self._worker: _FetchWorker | None = None
        self._settings = QSettings("SHARPpyReimagined", "GUI")
        self._all_stations = uwyo_catalog.all_stations()

        # The one shared render/display config, owned by the controller (this
        # window). Built once here -- mirrors the renderer bootstrap -- and
        # mutated in place by the Preferences dialog.
        self.config = R.build_config(tempfile.gettempdir())

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_map_tab(), "Station Map")
        self._tabs.addTab(self._build_uwyo_tab(), "Station List")
        self._tabs.addTab(self._build_file_tab(), "Open File")
        self.setCentralWidget(self._tabs)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready \u2014 pick a station and press Fetch")

        self._build_menu()
        self._restore_state()

    # -- menu ---------------------------------------------------------------- #
    def _build_menu(self) -> None:
        filemenu = self.menuBar().addMenu("&File")
        open_act = QAction("&Open Sounding File\u2026", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._browse_and_open)
        filemenu.addAction(open_act)
        filemenu.addSeparator()
        pref_act = QAction("&Preferences\u2026", self)
        pref_act.setShortcut("Ctrl+,")
        pref_act.triggered.connect(self.preferencesbox)
        filemenu.addAction(pref_act)
        filemenu.addSeparator()
        quit_act = QAction("&Quit", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        filemenu.addAction(quit_act)

        helpmenu = self.menuBar().addMenu("&Help")
        controls_act = QAction("Sounding Window &Controls", self)
        controls_act.triggered.connect(self._show_controls_help)
        helpmenu.addAction(controls_act)
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

    def _show_controls_help(self) -> None:
        _show_controls_dialog(self)

    # -- controller contract (this window doubles as the SHARPpy controller) - #
    def preferencesbox(self) -> None:
        """Open the SHARPpy preferences dialog (palette + units).

        Mirrors the legacy ``Main.preferencesbox``: the dialog edits the shared
        :attr:`config` in place; on close we re-apply the fork's alert-tier
        color substitutions and broadcast :attr:`config_changed` so every open
        sounding window refreshes its profiles and palette.
        """
        try:
            from sharppy.viz.preferences import PrefDialog
        except Exception as exc:  # pragma: no cover - vendored dep always present
            QMessageBox.warning(self, APP_NAME,
                                f"Preferences are unavailable:\n{exc}")
            return
        dialog = PrefDialog(self.config, parent=self)
        dialog.exec()
        # Keep the fork's legibility substitutions after any palette change.
        try:
            from sharpmod import colors
            self.config["preferences", "alert_l1_color"] = colors.ALERT_L1_COLOR
            self.config["preferences", "alert_l2_color"] = colors.ALERT_L2_COLOR
        except Exception:
            pass
        self.config_changed.emit(self.config)

    def focusPicker(self) -> None:  # noqa: N802 - matches SPCWindow's caller
        """Bring the picker back to the front (the ``W`` key target)."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ====================================================================== #
    # Station Map tab (legacy-SHARPpy style)
    # ====================================================================== #
    def _build_map_tab(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)

        # --- left control column ---
        left = QVBoxLayout()
        left.setSpacing(8)

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
        cg.addWidget(self._map_date, 0, 1)
        cg.addWidget(QLabel("Time:"), 1, 0)
        self._map_cycle = QComboBox()
        for h in SYNOPTIC_HOURS:
            self._map_cycle.addItem(f"{h:02d}Z", h)
        self._map_cycle.setCurrentIndex(SYNOPTIC_HOURS.index(default_hour))
        cg.addWidget(self._map_cycle, 1, 1)
        recent = QToolButton()
        recent.setText("Most recent")
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
        left_w.setFixedWidth(260)
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
        self._date_edit.dateChanged.connect(self._update_valid_label)
        tg.addWidget(self._date_edit, 0, 1)

        tg.addWidget(QLabel("Cycle:"), 1, 0)
        self._cycle_combo = QComboBox()
        for h in SYNOPTIC_HOURS:
            self._cycle_combo.addItem(f"{h:02d}Z", h)
        self._cycle_combo.setCurrentIndex(SYNOPTIC_HOURS.index(default_hour))
        self._cycle_combo.currentIndexChanged.connect(self._update_valid_label)
        tg.addWidget(self._cycle_combo, 1, 1)

        recent_btn = QToolButton()
        recent_btn.setText("Most recent")
        recent_btn.clicked.connect(self._set_most_recent)
        tg.addWidget(recent_btn, 1, 2)

        self._valid_lbl = QLabel("")
        self._valid_lbl.setStyleSheet("font-weight: bold;")
        tg.addWidget(self._valid_lbl, 2, 0, 1, 3)
        layout.addWidget(time_box)

        # --- Primary action ---
        self._fetch_btn = QPushButton("Fetch && Display Sounding")
        self._fetch_btn.setDefault(True)
        self._fetch_btn.setMinimumHeight(36)
        self._fetch_btn.clicked.connect(self._fetch_selected)
        layout.addWidget(self._fetch_btn)

        # Populate the full catalogue now; live-filter narrows it.
        self._filter_stations("")
        self._update_valid_label()
        self._sync_fetch_enabled()
        return w

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
        self._settings.setValue("last_station", sid)

        self._set_busy(True)
        self.statusBar().showMessage(
            f"Fetching {sid} at {when:%Y-%m-%d %H}Z from UWyo\u2026")

        self._worker = _FetchWorker(sid, when, parent=self)
        self._worker.finished_ok.connect(self._on_fetch_ok)
        self._worker.failed.connect(self._on_fetch_failed)
        self._worker.finished.connect(lambda: self._set_busy(False))
        self._worker.start()

    def _set_busy(self, busy: bool) -> None:
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
            prof_col, stn_id = R.decode(npz_path)
            title = f"{APP_NAME} \u2014 {meta.id} {when:%Y-%m-%d %H}Z"
            self._show_sounding(prof_col, stn_id, title=title)
            self.statusBar().showMessage(f"Opened {meta.id} {when:%Y-%m-%d %H}Z")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, APP_NAME,
                                 f"Fetched, but could not display:\n{exc}")
        finally:
            try:
                os.remove(npz_path)
            except OSError:
                pass

    def _on_fetch_failed(self, message: str) -> None:
        self.statusBar().showMessage("Fetch failed")
        QMessageBox.critical(self, APP_NAME, message)

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
    def _show_sounding(self, prof_col, stn_id, title=None) -> None:
        # Compose the real, interactive SPCWindow with this picker as its Qt
        # parent/controller (so the W key refocuses the picker and Preferences
        # routes here). The window shows itself; we retain a reference.
        win = compose_interactive(self.config, prof_col, self, stn_id=stn_id)
        if title:
            win.setWindowTitle(title)
        self._prune_closed_viewers()
        self._viewers.append(win)

    def _prune_closed_viewers(self) -> None:
        """Drop references to windows the user has closed."""
        alive = []
        for w in self._viewers:
            try:
                if w.isVisible():
                    alive.append(w)
            except RuntimeError:
                continue  # already deleted by Qt
        self._viewers = alive

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
    app = QApplication.instance() or QApplication(sys.argv if argv is None
                                                  else argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)

    icon = _app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    picker = PickerWindow()
    picker.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
