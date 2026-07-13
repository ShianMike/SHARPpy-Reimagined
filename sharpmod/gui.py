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
selects the native windowing platform *before* the renderer is lazily imported
so the same composed window is realized on screen. All of the renderer's font
install, vendored-widget monkeypatches, layout compensation, and window-grow
passes are reused verbatim so the interactive window matches the rendered PNG
pixel-for-pixel.
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
# Respect an explicit override so power users and headless tests can still
# force e.g. ``offscreen`` or ``xcb``.
if "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = (
        "windows" if sys.platform.startswith("win")
        else ("cocoa" if sys.platform == "darwin" else "xcb")
    )
os.environ.setdefault("QT_API", "pyside6")

from qtpy.QtCore import (  # noqa: E402
    Qt, QThread, QTimer, Signal, QDate, QSettings, QPointF, QRectF, QSize,
)
from qtpy.QtGui import (  # noqa: E402
    QAction, QPainter, QColor, QPen, QBrush, QPolygonF, QFont, QPixmap, QIcon,
    QTransform,
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
)

__all__ = ["PickerWindow", "compose_interactive", "main"]

_render_mod = None
_compose_window_fn = None
_uwyo_catalog_mod = None
_uwyo_decoder_types = None

APP_NAME = "SHARPpy Reimagined"
APP_VERSION = "0.2 (20260713)"

UNIT_DEFAULTS = {
    "temp_units": "Fahrenheit",
    "wind_units": "knots",
    "pw_units": "in",
}

UNIT_OPTIONS = {
    "temp_units": ("Fahrenheit", "Celsius"),
    "wind_units": ("knots", "m/s"),
    "pw_units": ("in", "cm"),
}

PICKER_RAIL_MIN_WIDTH = 320
PICKER_RAIL_MAX_WIDTH = 380

#: One-line interaction hints shown in the sounding-window tip bar.
TIP_LINE = ("Tips:  right-click = readout / modify   \u00b7   drag points to edit"
            "   \u00b7   wheel = zoom   \u00b7   \u2190\u2009/\u2009\u2192 = step "
            "time   \u00b7   Ctrl+E = export   \u00b7   Ctrl+Shift+C = copy")

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
    R = _render()
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
    R._install_hodo_interpolation_menu()
    R._install_hodo_label_fit()
    R._install_skewt_level_labels_fit()
    R._install_stp_condense()
    R._install_stp_label_rename()
    R._install_stp_xlabel_colors()
    R._install_stp_bottom_margin()
    R._install_stp_box_shrink()
    R._install_stp_prob_box_spacing()
    R._install_conditional_prob_panel_fit()
    R._install_winter_text_fit()
    # Split the wind-speed strip's SFC-500 m layer and cap the wind-speed +
    # temp-advection strip fonts so titles/axis labels stay tidy at any strip
    # width (matches the PNG render path).
    R._install_speed_0500()
    R._install_speed_title_cap()
    R._install_advection_font_cap()
    # Size the skew-T mixing-ratio + surface-value label masks to the font so
    # background lines stop bleeding through the (wider-font) digits.
    R._install_skewt_mixratio_mask()
    R._install_skewt_sfc_label_mask()
    R._install_skewt_effective_layer_label_fit()
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


def _settle_layout_events(app, passes: int = 2) -> None:
    """Let Qt apply pending layout/resize work between manual grow passes."""
    for _ in range(max(1, passes)):
        app.processEvents()


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
    R = _render()
    _ensure_setup(app)

    _fill_metadata(prof_col, stn_id, model=model, run=run, loc=loc)

    # mount=True appends the derived-parameter family panels into the vendored
    # index band and attaches the skew-T HGZ overlay; controller=picker wires
    # the config/preferences/focus contract to the picker window.
    win, _ = _compose_window()(config, prof_col, mount=True, controller=controller)

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
    win.setStyleSheet(SOUNDING_LIGHT_QSS)
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
    _settle_layout_events(app)
    R._grow_for_family_panels(win)
    _settle_layout_events(app)
    # Grow the canvas the same way the PNG renderer does, so the interactive
    # window's skew-T / hodograph sizing matches the rendered image.
    R.enlarge_canvas(win)
    _settle_layout_events(app, 6)

    # A discoverable Export menu with sensible default filenames/locations
    # (the vendored Save Image/Text default to a hidden temp dir with no name).
    _install_export_menu(win, prof_col, controller)
    _install_units_menu(win, controller)
    try:
        _apply_unit_preferences_to_window(win, controller._config())
    except Exception:
        pass

    # Restore the legacy "Show Parcels" double-click on the parcel inset (the
    # fork replaces the vendored parcel panel with its IndexBoard, so the
    # vendored double-click is otherwise unreachable).
    _install_parcel_selector(win)
    try:
        _apply_default_parcel_to_window(win, controller._default_parcel())
    except Exception:
        pass

    # Fill the top strip with a compact interaction tip bar (also the on-screen
    # how-to). Done last so it wraps the fully composed spc_widget.
    _install_tip_bar(win, controller)

    # Keep the real sounding widget at its natural (CLI-identical) size inside
    # a non-resizing scroll host. Letting the Windows QMainWindow/graphics proxy
    # recompute the child geometry snaps the canvas back to SHARPpy's flatter
    # 1180x800-era size and squishes the Skew-T/hodograph.
    _fit_window_to_screen(app, win)

    win.showNormal()
    win.raise_()
    win.activateWindow()

    # The pre-show sizing uses an *estimated* menu-bar/chrome height, which can
    # leave the window slightly taller than the scaled sounding (a black band
    # below the index tables). Re-fit once now that the window is realized and
    # the true chrome heights are measurable, so the window wraps the sounding
    # exactly with no leftover gap.
    QTimer.singleShot(0, lambda: _finalize_scaled_fit(app, win))
    return win


class _FixedSoundingScrollArea(QScrollArea):
    """Host the composed sounding at its settled CLI geometry."""

    def __init__(self, widget, natural_size, parent=None):
        super().__init__(parent)
        self._widget = widget
        self._natural_size = QSize(max(1, natural_size.width()),
                                   max(1, natural_size.height()))
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("QScrollArea{background:#f3f6fa;border:0;}")
        self._lock_widget_size()
        self.setWidget(widget)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._lock_widget_size()
        super().resizeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._lock_widget_size()
        super().showEvent(event)

    def _lock_widget_size(self) -> None:
        self._widget.setFixedSize(self._natural_size)
        if self._widget.size() != self._natural_size:
            self._widget.resize(self._natural_size)


class _ScaledSoundingView(QGraphicsView):
    """Host the composed sounding, scaled uniformly to fit the viewport.

    Wraps the fixed-size ``spc_widget`` in a graphics scene and applies a view
    transform that fits the *entire* sounding into the visible area with its
    aspect ratio preserved. On displays too small to show the CLI-render size
    at 1:1 (e.g. 1920x1080), the whole Skew-T / hodograph / index layout scales
    down to fit instead of forcing the user to scroll.

    The widget itself stays pinned at its natural render geometry -- only a
    view transform scales it -- so the proportions match the PNG renderer and
    interactive editing / exports are unaffected. The transform is capped at
    1:1 so large screens never upscale (which would blur the text).
    """

    def __init__(self, widget, natural_size, parent=None):
        super().__init__(parent)
        self._natural = QSize(max(1, natural_size.width()),
                              max(1, natural_size.height()))
        self._widget = widget
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("QGraphicsView{background:#f3f6fa;border:0;}")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHints(
            QPainter.Antialiasing | QPainter.TextAntialiasing
            | QPainter.SmoothPixmapTransform)
        # Pin the sounding to the top of the view so any spare vertical space
        # (e.g. when the window is maximized or taller than the scaled canvas)
        # collects at the bottom instead of leaving a gap above the Skew-T.
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)

        widget.setFixedSize(self._natural)
        scene = QGraphicsScene(self)
        scene.addWidget(widget)
        scene.setSceneRect(0, 0, self._natural.width(),
                           self._natural.height())
        self.setScene(scene)

    def _refit(self) -> None:
        vp = self.viewport().size()
        if vp.width() <= 1 or vp.height() <= 1:
            return
        scale = min(vp.width() / self._natural.width(),
                    vp.height() / self._natural.height(), 1.0)
        self.setTransform(QTransform().scale(scale, scale))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refit()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._refit()


def _fit_window_to_screen(app, win) -> None:
    """Display the sounding at a size that fits the current screen.

    The vendored ``spc_widget`` keeps the composed natural (CLI-render)
    geometry, so the Skew-T, hodograph, and index panels keep the same
    proportions used by the PNG renderer. If that size fits the screen it is
    shown 1:1 in a scroll host; otherwise (e.g. on 1920x1080) it is embedded in
    a :class:`_ScaledSoundingView` that scales the whole sounding down to fit,
    so the entire layout is visible without scrolling.
    """
    sw = getattr(win, "spc_widget", None)
    if sw is None:
        return
    nat_w, nat_h = sw.width(), sw.height()
    if nat_w <= 1 or nat_h <= 1:
        return
    try:
        # Pin the sounding to its settled render size so no child layout can
        # squish it back to the vendored Windows geometry.
        natural = QSize(nat_w, nat_h)
        sw.setFixedSize(natural)

        # Detach the original central widget before re-parenting it into the
        # new host; otherwise QMainWindow may delete it when replacing the
        # central widget.
        old_central = win.takeCentralWidget()
        if old_central is not None and old_central is not sw:
            old_central.setParent(None)
        sw.setParent(None)

        mb = win.menuBar()
        mb_h = mb.sizeHint().height() or mb.height() or 26
        screen = app.primaryScreen().availableGeometry()

        # Reserve space for the window frame (title bar + borders) so the whole
        # window -- including its bottom edge -- stays on screen when it opens
        # non-maximized. Without this the client area can be as tall as the
        # work area and the frame pushes the bottom index tables off-screen.
        FRAME_W = 16
        FRAME_H = 48
        avail_w = max(320, screen.width() - FRAME_W)
        avail_h = max(240, screen.height() - FRAME_H)

        # Largest uniform scale (never above 1:1) that fits the sounding plus
        # its menu bar inside the available work area.
        content_max_h = max(1, avail_h - mb_h)
        scale = min(avail_w / nat_w, content_max_h / nat_h, 1.0)

        if scale >= 0.999:
            # Fits at native size: show it 1:1.
            win.setCentralWidget(_FixedSoundingScrollArea(sw, natural, win))
            client_w, client_h = nat_w, nat_h
        else:
            # Too big for this screen (e.g. 1920x1080): scale the whole sounding
            # down uniformly. Size the window to wrap the scaled sounding so the
            # entire layout is visible with no wasted letterbox and no cutoff.
            win.setCentralWidget(_ScaledSoundingView(sw, natural, win))
            client_w = int(round(nat_w * scale))
            client_h = int(round(nat_h * scale))

        win.resize(client_w, client_h + mb_h)
        try:
            # Centre the window on the work area.
            frame = win.frameGeometry()
            frame.moveCenter(screen.center())
            # Keep the top-left within the work area so the title bar/menu are
            # always reachable.
            tl = frame.topLeft()
            tl.setX(max(screen.left(), tl.x()))
            tl.setY(max(screen.top(), tl.y()))
            win.move(tl)
        except Exception:
            pass
    except Exception:
        # Never let the fit/scroll wrap block the interactive window.
        pass


def _finalize_scaled_fit(app, win) -> None:
    """Snap the window to wrap the scaled sounding exactly (post-realize).

    Runs after the window is shown, when the *actual* menu-bar / window-frame
    heights are known (the pre-show pass can only estimate them). It recomputes
    the largest uniform scale that fits the sounding in the work area using the
    measured chrome, then sizes the window so its viewport matches the scaled
    sounding's aspect ratio -- eliminating the black band that an over-estimated
    chrome height would otherwise leave below the index tables.
    """
    try:
        view = win.centralWidget()
        if not isinstance(view, _ScaledSoundingView):
            return  # native 1:1 path -- nothing to correct
        vp = view.viewport().size()
        if vp.width() <= 1 or vp.height() <= 1:
            return

        nat = view._natural
        scr = app.primaryScreen().availableGeometry()

        # Measured chrome: everything in the window that is NOT the viewport
        # (menu bar, borders) and the title-bar/frame outside the client area.
        chrome_w = max(0, win.width() - vp.width())
        chrome_h = max(0, win.height() - vp.height())
        frame_w = max(0, win.frameGeometry().width() - win.width())
        frame_h = max(0, win.frameGeometry().height() - win.height())

        avail_vp_w = scr.width() - frame_w - chrome_w
        avail_vp_h = scr.height() - frame_h - chrome_h
        if avail_vp_w <= 1 or avail_vp_h <= 1:
            return

        scale = min(avail_vp_w / nat.width(), avail_vp_h / nat.height(), 1.0)
        vp_w = int(round(nat.width() * scale))
        vp_h = int(round(nat.height() * scale))
        new_w = vp_w + chrome_w
        new_h = vp_h + chrome_h

        if abs(new_w - win.width()) > 2 or abs(new_h - win.height()) > 2:
            win.resize(new_w, new_h)

        # Re-centre on the work area, keeping the title bar reachable.
        frame = win.frameGeometry()
        frame.moveCenter(scr.center())
        tl = frame.topLeft()
        tl.setX(max(scr.left(), tl.x()))
        tl.setY(max(scr.top(), tl.y()))
        win.move(tl)
        view._refit()
    except Exception:
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
        lbl.setStyleSheet("color:#47566b; font-size:11px;")
        lbl.setWordWrap(False)

        guide_btn = QToolButton()
        guide_btn.setText("Full guide")
        guide_btn.setToolTip("Show all sounding-window controls")
        guide_btn.setAutoRaise(True)
        guide_btn.setStyleSheet("QToolButton{color:#34506f;}")
        guide_btn.clicked.connect(lambda: _show_controls_dialog(win))

        close_btn = QToolButton()
        close_btn.setText("\u2715")
        close_btn.setToolTip("Hide these tips")
        close_btn.setAutoRaise(True)
        close_btn.setStyleSheet("QToolButton{color:#66758a;}")

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

#: Preserve SHARPpy's existing initial Skew-T parcel when no preference has
#: been saved yet (``SPCWidget.parcel_type`` also defaults to ``"MU"``).
_DEFAULT_SKEWT_PARCEL = "MU"


def _normalize_default_parcel(value) -> str:
    """Return a supported parcel key, falling back to SHARPpy's MU default."""
    try:
        key = str(value or "").strip().upper()
    except Exception:
        key = ""
    valid = {parcel_key for _label, parcel_key in _PARCEL_OPTIONS}
    return key if key in valid else _DEFAULT_SKEWT_PARCEL


def _build_preferences_dialog(config, parent=None):
    """Create SHARPpy's preferences dialog with its Qt6 readout fix.

    SHARPpy 1.4.0a5 passes the one-element NumPy result of ``where`` directly
    to ``QComboBox.setCurrentIndex``. Current PySide6 correctly requires a
    scalar integer, which otherwise prevents Preferences from opening at all.
    Keep the upstream dialog and override only that small widget constructor.
    """
    from sharppy.viz.preferences import PrefDialog

    class _Qt6PreferencesDialog(PrefDialog):
        def _createReadoutWidget(self):
            box = QWidget(self)
            layout = QVBoxLayout(box)
            layout.addWidget(QLabel("Top Right Readout Variable:"))
            self.combo1 = QComboBox(box)
            layout.addWidget(self.combo1)
            layout.addWidget(QLabel("Bottom Right Readout Variable:"))
            self.combo2 = QComboBox(box)
            layout.addWidget(self.combo2)

            self.variables = {
                "Temperature (C)": "tmpc",
                "Dewpoint (C)": "dwpc",
                "Equiv. Potential Temp. (K)": "thetae",
                "Wetbulb Temperature (C)": "wetbulb",
                "Potential Temperature (K)": "theta",
                "Water Vapor Mixing Ratio (g/kg)": "wvmr",
                "Vertical Velocity (mb/hr)": "omeg",
            }
            self.combo1.addItems(list(self.variables))
            self.combo2.addItems(list(self.variables))

            top = self._config["preferences", "readout_tr"]
            bottom = self._config["preferences", "readout_br"]
            labels_by_value = {
                value: label for label, value in self.variables.items()
            }
            self.combo1.setCurrentText(
                labels_by_value.get(top, "Temperature (C)"))
            self.combo2.setCurrentText(
                labels_by_value.get(bottom, "Dewpoint (C)"))
            return box

    return _Qt6PreferencesDialog(config, parent=parent)


def _add_default_parcel_tab(dialog, current_key):
    """Add the app-specific default-parcel choice to SHARPpy Preferences."""
    tabs = dialog.findChild(QTabWidget)
    if tabs is None:
        return None

    panel = QWidget(dialog)
    layout = QVBoxLayout(panel)
    layout.addWidget(QLabel(
        "Parcel visualized when a sounding window first opens."))

    parcel_box = QComboBox(panel)
    for label, key in _PARCEL_OPTIONS:
        parcel_box.addItem(label, key)
    selected = parcel_box.findData(_normalize_default_parcel(current_key))
    parcel_box.setCurrentIndex(max(0, selected))
    layout.addWidget(parcel_box)

    note = QLabel(
        "You can still click any parcel row in a sounding window to change "
        "that window's Skew-T trace.")
    note.setWordWrap(True)
    layout.addWidget(note)
    layout.addStretch(1)
    tabs.addTab(panel, "Parcel")
    return parcel_box


def _apply_default_parcel_to_window(win, parcel_key) -> bool:
    """Apply a preferred parcel through SHARPpy's normal interactive path."""
    try:
        key = _normalize_default_parcel(parcel_key)
        sw = getattr(win, "spc_widget", None)
        conv = getattr(sw, "convective", None) if sw is not None else None
        if sw is None or conv is None or not hasattr(sw, "updateParcel"):
            return False

        parcels = getattr(conv, "parcels", {}) or {}
        parcel = parcels.get(key)
        if parcel is None:
            prof = getattr(sw, "default_prof", None)
            parcel = getattr(prof, _PARCEL_ATTR[key], None)
        if parcel is None:
            return False

        pcl_types = list(getattr(conv, "pcl_types", None) or [])
        if key in pcl_types:
            conv.skewt_pcl = pcl_types.index(key)
        sw.updateParcel(parcel)
        return True
    except Exception:
        # A missing parcel (most often EFF in a stable profile) must never stop
        # a sounding from opening; SHARPpy's already-rendered parcel remains.
        return False


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


class _UnitPreferencesDialog(QDialog):
    """Small display-unit editor for an already-open sounding window."""

    _QSS = (
        "QDialog{background-color:#1b2436;color:#e8eef7;}"
        "QLabel{color:#c8d4e6;}"
        "QComboBox{background:#0d1220;color:#e8eef7;padding:5px;"
        "border:1px solid #596a84;border-radius:4px;}"
        "QPushButton{background:#2c3e55;color:#ffffff;padding:6px 18px;"
        "border:1px solid #46607f;border-radius:4px;}"
        "QPushButton:hover{background:#37507a;}"
    )

    def __init__(self, preferences, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Units")
        self.setStyleSheet(self._QSS)
        self._boxes = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Display units for the active sounding."))

        form = QFormLayout()
        for key, label in (
            ("temp_units", "Temperatures"),
            ("wind_units", "Wind speeds / kinematics"),
            ("pw_units", "PWAT"),
        ):
            box = QComboBox()
            box.addItems(list(UNIT_OPTIONS[key]))
            value = preferences.get(key, UNIT_DEFAULTS[key])
            if value in UNIT_OPTIONS[key]:
                box.setCurrentText(value)
            self._boxes[key] = box
            form.addRow(label, box)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def preferences(self):
        return {key: box.currentText() for key, box in self._boxes.items()}


def _normalize_unit_preferences(preferences):
    prefs = dict(UNIT_DEFAULTS)
    for key, value in (preferences or {}).items():
        if key in UNIT_OPTIONS and value in UNIT_OPTIONS[key]:
            prefs[key] = value
    return prefs


def _read_config_unit(config, key):
    for accessor in (
        lambda: config["preferences", key],
        lambda: config[("preferences", key)],
        lambda: config.get(("preferences", key)),
        lambda: config.get("preferences", key),
    ):
        try:
            value = accessor()
        except Exception:
            continue
        if value in UNIT_OPTIONS.get(key, ()):
            return value
    return None


def _write_unit_preferences_to_config(config, preferences) -> None:
    if config is None:
        return
    for key, value in _normalize_unit_preferences(preferences).items():
        try:
            config["preferences", key] = value
        except Exception:
            pass


def _apply_unit_preferences_to_window(win, config) -> None:
    """Refresh mounted SHARPpy Reimagined widgets after unit changes."""
    try:
        from sharpmod.viz.SPCWindow import reapply_color_scheme

        reapply_color_scheme(win, config)
        return
    except Exception:
        pass
    try:
        from sharpmod import colors

        prefs = colors.scheme_preferences(config)
        sw = getattr(win, "spc_widget", None) or win
        board = getattr(sw, "index_board", None)
        if board is not None and hasattr(board, "setPreferences"):
            board.setPreferences(update_gui=True, **prefs)
    except Exception:
        pass


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
    -- and the text export writes the focused profile as a SHARPpy text file
    that loads back into the app.
    """
    R = _render()
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

    def _notify(message: str) -> None:
        try:
            win.statusBar().showMessage(message, 4000)
        except Exception:
            pass

    def export_image(image_mode: str) -> None:
        labels = {
            getattr(R, "PNG_IMAGE_HD", "hd"): ("HD", "_hd"),
            getattr(R, "PNG_IMAGE_UHD", "uhd"): ("UHD", "_uhd"),
            getattr(R, "PNG_IMAGE_LOSSLESS", "lossless"):
                ("Lossless", "_lossless"),
        }
        label, suffix = labels.get(image_mode, ("HD", "_hd"))
        start = os.path.join(_start_dir(), base + suffix + ".png")
        fn, _ok = QFileDialog.getSaveFileName(
            win, f"Export Sounding {label} Image", start,
            "PNG image (*.png)")
        if fn:
            if not fn.lower().endswith(".png"):
                fn += ".png"
            if R.save_widget_png(win.spc_widget, fn, image_mode=image_mode):
                _remember(fn)
                _notify(f"Exported {label.lower()} image to {fn}")
            else:
                QMessageBox.warning(win, APP_NAME,
                                    f"Could not export image:\n{fn}")

    def copy_image() -> None:
        try:
            pixmap = R.grab_widget_pixmap(win.spc_widget)
            QApplication.clipboard().setPixmap(pixmap)
            _notify("Sounding image copied to clipboard")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(win, APP_NAME,
                                f"Could not copy image:\n{exc}")

    def export_text() -> None:
        start = os.path.join(_start_dir(), base + ".txt")
        fn, _ok = QFileDialog.getSaveFileName(
            win, "Export Sounding Text (SHARPpy)", start,
            "SHARPpy text (*.txt)")
        if fn:
            if not fn.lower().endswith(".txt"):
                fn += ".txt"
            try:
                from sharpmod.io.sharppy_export import export_profile_to_sharppy

                export_profile_to_sharppy(win.spc_widget.default_prof, fn)
                _remember(fn)
                _notify(f"Exported SHARPpy text to {fn}")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(win, APP_NAME,
                                    f"Could not export text:\n{exc}")

    try:
        menu = win.menuBar().addMenu("Export")
        act_img = QAction("Export Image (HD PNG)\u2026", win)
        act_img.setShortcut("Ctrl+E")
        act_img.triggered.connect(
            lambda _checked=False: export_image(R.PNG_IMAGE_HD))
        menu.addAction(act_img)
        act_uhd = QAction("Export Image (UHD PNG)\u2026", win)
        act_uhd.triggered.connect(
            lambda _checked=False: export_image(R.PNG_IMAGE_UHD))
        menu.addAction(act_uhd)
        act_lossless = QAction("Export Image (Lossless PNG)\u2026", win)
        act_lossless.triggered.connect(
            lambda _checked=False: export_image(R.PNG_IMAGE_LOSSLESS))
        menu.addAction(act_lossless)
        act_copy = QAction("Copy Image to Clipboard", win)
        act_copy.setShortcut("Ctrl+Shift+C")
        act_copy.triggered.connect(copy_image)
        menu.addAction(act_copy)
        act_txt = QAction("Export Text (SHARPpy)\u2026", win)
        act_txt.triggered.connect(export_text)
        menu.addAction(act_txt)
    except Exception:
        # Never let an export-menu hiccup block the interactive window.
        pass


def _install_units_menu(win, controller) -> None:
    """Add a sounding-window Settings menu for fast unit changes."""
    try:
        menu = win.menuBar().addMenu("Settings")
        act_units = QAction("Units\u2026", win)
        act_units.setShortcut("Ctrl+U")

        def _open_units():
            open_dialog = getattr(controller, "unit_preferencesbox", None)
            if callable(open_dialog):
                open_dialog(parent=win)
            else:
                prefs = getattr(controller, "preferencesbox", None)
                if callable(prefs):
                    prefs()

        act_units.triggered.connect(_open_units)
        menu.addAction(act_units)
    except Exception:
        # Settings-menu wiring must never block the sounding window.
        pass


# ===========================================================================
# Sounding availability pre-flight check (green / red / gray status)
# ===========================================================================
#: Availability states surfaced by :class:`_AvailabilityIndicator`.
AVAIL_UNKNOWN = "unknown"          # not checked yet
AVAIL_CHECKING = "checking"        # network probe in flight
AVAIL_AVAILABLE = "available"      # green  -- a usable sounding exists
AVAIL_INSUFFICIENT = "insufficient"  # gray -- present but corrupt/too sparse
AVAIL_UNAVAILABLE = "unavailable"  # red   -- nothing archived / unreachable

#: Dot color per state (green available, red unavailable, gray insufficient).
_AVAIL_COLORS = {
    AVAIL_UNKNOWN: "#6f7d8f",
    AVAIL_CHECKING: "#e0a030",
    AVAIL_AVAILABLE: "#3fbf5f",
    AVAIL_INSUFFICIENT: "#9aa4b0",
    AVAIL_UNAVAILABLE: "#e0433a",
}

#: Default label per state (overridable with a specific message).
_AVAIL_LABELS = {
    AVAIL_UNKNOWN: "Not checked",
    AVAIL_CHECKING: "Checking...",
    AVAIL_AVAILABLE: "Available",
    AVAIL_INSUFFICIENT: "Limited",
    AVAIL_UNAVAILABLE: "Unavailable",
}

#: Minimum decoded levels / moisture levels for a "best analysis" sounding.
_AVAIL_MIN_THERMO_LEVELS = 6
_AVAIL_MIN_MOISTURE_LEVELS = 3
_AVAIL_MIN_PRESSURE_SPAN_HPA = 150.0


def _station_label(station_id: str, name: str) -> str:
    """Format a station's index + city, e.g. ``"72357 \u2014 OUN Norman, OK"``.

    UWyo catalogue names are ``"<callsign> <city>, <state>"``; the id is the
    WMO station index. Both are shown so the observation is unambiguous.
    """
    sid = str(station_id or "").strip()
    city = str(name or "").strip()
    if sid and city:
        return f"{sid} \u2014 {city}"
    return sid or city


def _decoder_for_station(station: dict | None):
    """Build a UWyo decoder, seeding it with ``station`` when provided.

    When ``station`` (a ``{"id","name","lat","lon","src"}`` record from the
    live datetime-aware list) is given, the decoder resolves that id against a
    one-entry catalogue carrying its real UWyo ``src`` (``BUFR`` / ``FM35`` /
    ...). This lets the picker fetch stations that are *not* in the bundled
    catalogue -- e.g. relocated stations whose WMO index changed over time --
    and always requests the correct data source. Otherwise it falls back to the
    full bundled catalogue.

    Returns ``(decoder, resolve_query)`` where ``resolve_query`` is the string
    to hand :meth:`UWyo_Decoder.resolve_station`.
    """
    _StationLookupError, UWyo_Decoder, _UWyoError = _uwyo_decoder_classes()
    if station and station.get("id"):
        sid = str(station["id"])
        catalog = {
            sid: (
                station.get("name", ""),
                station.get("lat", float("nan")),
                station.get("lon", float("nan")),
                float("nan"),
                station.get("src") or UWyo_Decoder.DEFAULT_SRC,
            )
        }
        return UWyo_Decoder(station_catalog=catalog), sid
    return UWyo_Decoder(full_catalog=True), None


def _classify_availability(prof) -> tuple[str, str]:
    """Grade a successfully fetched profile as green vs. gray.

    A sounding that downloads and parses cleanly is still only useful for SPC
    analysis if it has a reasonable vertical extent with temperature, moisture,
    and wind. Returns ``(AVAIL_AVAILABLE | AVAIL_INSUFFICIENT, message)``.
    """
    import numpy as np

    try:
        def _valid(name):
            arr = np.ma.masked_invalid(np.ma.asarray(getattr(prof, name),
                                                      dtype=float))
            return arr, np.ma.getmaskarray(arr)

        pres, pmask = _valid("pres")
        _tmpc, tmask = _valid("tmpc")
        _dwpc, dmask = _valid("dwpc")
        _wspd, wmask = _valid("wspd")
    except Exception:  # noqa: BLE001 - malformed profile => gray
        return AVAIL_INSUFFICIENT, "Limited (data unreadable)"

    n_thermo = int(np.count_nonzero(~(pmask | tmask)))
    n_moist = int(np.count_nonzero(~(pmask | dmask)))
    n_wind = int(np.count_nonzero(~(pmask | wmask)))

    if n_thermo < _AVAIL_MIN_THERMO_LEVELS:
        return AVAIL_INSUFFICIENT, f"Limited ({n_thermo} levels)"

    valid_pres = pres.compressed()
    if valid_pres.size >= 2:
        span = float(np.nanmax(valid_pres) - np.nanmin(valid_pres))
        if span < _AVAIL_MIN_PRESSURE_SPAN_HPA:
            return AVAIL_INSUFFICIENT, "Limited (shallow profile)"

    notes = []
    if n_moist < _AVAIL_MIN_MOISTURE_LEVELS:
        notes.append("missing moisture")
    if n_wind < _AVAIL_MIN_MOISTURE_LEVELS:
        notes.append("missing wind")
    if notes:
        return AVAIL_INSUFFICIENT, "Limited (" + ", ".join(notes) + ")"

    return AVAIL_AVAILABLE, f"Available ({n_thermo} levels)"


class _AvailabilityWorker(QThread):
    """Probe UWyo for a station/time and classify the result off the UI thread.

    Emits :attr:`checked` with ``(station_id, when, status, message)`` where
    ``status`` is one of the ``AVAIL_*`` constants. The full fetch is performed
    (the availability of a *usable* sounding cannot be known without decoding
    it), so this shares the exact retrieval/decode path as a real fetch.
    """

    #: (query, when, status, message, station_label). ``station_label`` is a
    #: "index \u2014 city" string once the station resolves, else "".
    checked = Signal(str, object, str, str, str)

    def __init__(self, station_query: str, when_utc: datetime, token: int,
                 parent=None, station: dict | None = None):
        super().__init__(parent)
        self._query = station_query
        self._when = when_utc
        self.token = token
        self._station = station

    def run(self):  # noqa: D401 - QThread entry point
        try:
            StationLookupError, UWyo_Decoder, UWyoError = _uwyo_decoder_classes()
        except Exception:  # noqa: BLE001
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (decoder)", "")
            return

        # Typed errors let us distinguish "nothing archived" (red) from
        # "corrupt/unparseable" (gray). Missing imports degrade gracefully.
        try:
            from sharpmod.io.uwyo_decoder import (
                RetrievalError,
                SoundingParseError,
                StationTimeUnavailableError,
            )
        except Exception:  # noqa: BLE001 - fall back to base-class handling
            RetrievalError = SoundingParseError = StationTimeUnavailableError = ()

        # Resolve the station first so its index + city can be reported in
        # every outcome (available, unavailable, or insufficient). A live
        # station record (with its real UWyo ``src``) is used when available so
        # relocated / re-indexed stations resolve and fetch correctly.
        try:
            decoder, seeded_query = _decoder_for_station(self._station)
            meta = decoder.resolve_station(seeded_query or self._query)
        except StationLookupError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (station lookup)", "")
            return
        except Exception:  # noqa: BLE001
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (station lookup)", "")
            return

        label = _station_label(meta.id, meta.name)

        try:
            prof = decoder.fetch(meta.id, self._when)
        except SoundingParseError:
            self.checked.emit(self._query, self._when, AVAIL_INSUFFICIENT,
                              "Limited (data unreadable)", label)
            return
        except StationTimeUnavailableError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (no sounding)", label)
            return
        except RetrievalError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (service unreachable)", label)
            return
        except UWyoError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (fetch failed)", label)
            return
        except Exception:  # noqa: BLE001 - never crash the UI thread
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (unexpected error)", label)
            return

        status, message = _classify_availability(prof)
        self.checked.emit(self._query, self._when, status, message, label)


class _AvailabilityIndicator(QWidget):
    """A colored dot + text reporting a station's sounding availability.

    Shows the resolved station index and city on a header line (when known)
    above the color-coded availability status.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self.setMinimumHeight(50)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(8)
        self._dot = QLabel()
        self._dot.setFixedSize(14, 14)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        self._station = QLabel()
        self._station.setWordWrap(True)
        self._station.setMinimumHeight(18)
        self._station.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self._station.setStyleSheet("color:#d5e0ef; font-weight:bold;")
        self._station.setVisible(False)
        self._text = QLabel()
        self._text.setWordWrap(True)
        self._text.setMinimumHeight(24)
        self._text.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._text.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        text_col.addWidget(self._station)
        text_col.addWidget(self._text)
        lay.addWidget(self._dot, 0, Qt.AlignTop)
        lay.addLayout(text_col, 1)
        self.set_status(AVAIL_UNKNOWN)

    def set_status(self, status: str, message: str | None = None,
                   station_label: str | None = None) -> None:
        color = _AVAIL_COLORS.get(status, _AVAIL_COLORS[AVAIL_UNKNOWN])
        self._dot.setStyleSheet(
            f"background:{color}; border-radius:7px; border:1px solid #0c1118;")
        label = (station_label or "").strip()
        if " \u2014 " in label:
            display_label = label.split(" \u2014 ", 1)[0]
        elif len(label) > 48:
            display_label = label[:45].rstrip() + "\u2026"
        else:
            display_label = label
        self._station.setText(display_label)
        self._station.setVisible(bool(label))
        text = message or _AVAIL_LABELS.get(status, "")
        self._text.setText(text)
        self._text.setStyleSheet(f"color:{color}; font-weight:bold;")
        self.setToolTip(f"{label}\n{text}".strip() if label else text)


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

    def __init__(self, station_query: str, when_utc: datetime, parent=None,
                 station: dict | None = None):
        super().__init__(parent)
        self._query = station_query
        self._when = when_utc
        self._station = station

    def run(self):  # noqa: D401 - QThread entry point
        try:
            StationLookupError, UWyo_Decoder, UWyoError = _uwyo_decoder_classes()
        except Exception as exc:  # noqa: BLE001 - surface import/freezer issues
            self.failed.emit(f"UWyo decoder is unavailable: {exc}")
            return
        try:
            decoder, seeded_query = _decoder_for_station(self._station)
            meta = decoder.resolve_station(seeded_query or self._query)
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


def _cleanup_model_data(npz_path: str, download_dir: str) -> None:
    """Remove one isolated forecast-model fetch tree."""
    from sharpmod.tools import model_extract
    model_extract.cleanup_transient_data(npz_path, download_dir)


def _retain_model_data_until_close(viewer, npz_path: str,
                                   download_dir: str) -> None:
    """Keep model data alive until ``viewer`` is actually closed."""
    viewer.setAttribute(Qt.WA_DeleteOnClose, True)
    viewer.destroyed.connect(
        lambda *_args: _cleanup_model_data(npz_path, download_dir))


class _ModelFetchWorker(QThread):
    """Extract a forecast-model point sounding off the UI thread."""

    finished_ok = Signal(str, str, object, int)
    failed = Signal(str)

    def __init__(self, model: str, lat: float, lon: float, run_time: datetime,
                 fxx: int, out_path: str, loc: str | None = None,
                 member: str | None = None, download_dir: str | None = None,
                 parent=None):
        super().__init__(parent)
        self._model = model
        self._lat = float(lat)
        self._lon = float(lon)
        self._run_time = run_time
        self._fxx = int(fxx)
        self._out_path = out_path
        self._loc = loc
        self._member = member or None
        self._download_dir = download_dir or os.path.dirname(out_path)

    def run(self):  # noqa: D401 - QThread entry point
        try:
            from sharpmod.tools import model_extract
            cfg = model_extract.get_config(self._model)
            path = model_extract.extract(
                self._model,
                self._lat,
                self._lon,
                run_time=self._run_time,
                fxx=self._fxx,
                out_path=self._out_path,
                loc=self._loc,
                member=self._member,
                download_dir=self._download_dir,
            )
        except Exception as exc:  # noqa: BLE001 - surface any model error to UI
            _cleanup_model_data(self._out_path, self._download_dir)
            self.failed.emit(f"Forecast model fetch failed: {exc}")
            return
        self.finished_ok.emit(path, cfg.label, self._run_time, self._fxx)


# ===========================================================================
# Datetime-aware station-list worker (relocated / re-indexed station support)
# ===========================================================================
class _StationListWorker(QThread):
    """Fetch the stations UWyo reported at a given time, off the UI thread.

    The bundled catalogue is fixed in time, so it misses stations that were
    relocated (and had their WMO index change). This worker queries the live
    ``/wsgi/sounding_json`` endpoint for the requested observation time and
    emits the normalized station records so the picker can show exactly what is
    choosable for that datetime.
    """

    #: (when_utc, list_of_station_records)
    loaded = Signal(object, object)
    #: (when_utc, human-readable message)
    failed = Signal(object, str)

    def __init__(self, when_utc: datetime, token: int, parent=None):
        super().__init__(parent)
        self._when = when_utc
        self.token = token

    def run(self):  # noqa: D401 - QThread entry point
        try:
            from sharpmod.io import uwyo_catalog as catalog
            stations = catalog.fetch_stations_for_datetime(self._when)
        except Exception as exc:  # noqa: BLE001 - never crash the UI thread
            self.failed.emit(self._when, str(exc))
            return
        self.loaded.emit(self._when, stations)


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
    "Caribbean / Gulf": (-100.0, -50.0, 5.0, 35.0),
    "Western Pacific": (115.0, 170.0, -5.0, 30.0),
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

    def set_stations(self, stations) -> None:
        """Replace the plotted station set (keeps a valid selection).

        Used when the datetime-aware station list is refreshed from UWyo: the
        map redraws with exactly the stations available at that time. The
        current selection/hover is cleared if it no longer exists.
        """
        self._stations = list(stations)
        ids = {s["id"] for s in self._stations}
        if self._selected_id not in ids:
            self._selected_id = None
        if self._hover_id not in ids:
            self._hover_id = None
            self._hover_lonlat = None
        self.update()

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


class PointMapWidget(StationMapWidget):
    """Clickable lat/lon picker map for forecast-model point soundings."""

    pointSelected = Signal(float, float)   # lat, lon
    pointActivated = Signal(float, float)  # lat, lon

    def __init__(self, parent=None):
        super().__init__([], parent=parent)
        self._point_lonlat = (-97.44, 35.63)
        self._domain_bounds: tuple[float, float, float, float] | None = None
        self._domain_label = ""

    def set_point(self, lat: float, lon: float, center: bool = False) -> None:
        lon = ((float(lon) + 180.0) % 360.0) - 180.0
        lat = max(-89.99, min(89.99, float(lat)))
        self._point_lonlat = (lon, lat)
        if center:
            span_lon = (self._lon1 - self._lon0) / 2.0
            span_lat = (self._lat1 - self._lat0) / 2.0
            self._lon0, self._lon1 = lon - span_lon, lon + span_lon
            self._lat0, self._lat1 = lat - span_lat, lat + span_lat
            self._invalidate()
        else:
            self.update()

    def set_domain(self, bounds, label: str = "") -> None:
        self._domain_bounds = tuple(bounds) if bounds is not None else None
        self._domain_label = label or ""
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        qp = QPainter(self)
        qp.drawPixmap(0, 0, self._basemap_pixmap())
        qp.setRenderHint(QPainter.Antialiasing, True)
        p = self._proj()
        self._draw_domain(qp, p)
        self._draw_point(qp, p)
        self._draw_readout(qp)
        qp.end()

    def _draw_domain(self, qp, p) -> None:
        if self._domain_bounds is None:
            return
        lon0, lon1, lat0, lat1 = self._domain_bounds
        if lon0 <= -179.0 and lon1 >= 179.0 and lat0 <= -85.0 and lat1 >= 85.0:
            return
        poly = QPolygonF([
            self._to_px(lon0, lat0, p),
            self._to_px(lon1, lat0, p),
            self._to_px(lon1, lat1, p),
            self._to_px(lon0, lat1, p),
            self._to_px(lon0, lat0, p),
        ])
        fill = QColor(80, 140, 220, 34)
        edge = QColor("#79b8ff")
        qp.setBrush(QBrush(fill))
        qp.setPen(QPen(edge, 1.4, Qt.DashLine))
        qp.drawPolygon(poly)

    def _draw_point(self, qp, p) -> None:
        lon, lat = self._point_lonlat
        pt = self._to_px(lon, lat, p)
        if pt.x() < -20 or pt.y() < -20 \
                or pt.x() > self.width() + 20 or pt.y() > self.height() + 20:
            return
        qp.setBrush(QBrush(QColor("#ffd000")))
        qp.setPen(QPen(QColor("#ffffff"), 2.0))
        qp.drawEllipse(pt, 7.0, 7.0)
        qp.setPen(QPen(QColor("#0a0d13"), 1.4))
        qp.drawLine(QPointF(pt.x() - 10, pt.y()), QPointF(pt.x() + 10, pt.y()))
        qp.drawLine(QPointF(pt.x(), pt.y() - 10), QPointF(pt.x(), pt.y() + 10))

    def _draw_readout(self, qp) -> None:
        lines = []
        if self._hover_lonlat is not None:
            lon, lat = self._hover_lonlat
            lines.append(f"Cursor  {lat:.3f}, {lon:.3f}")
        lon, lat = self._point_lonlat
        lines.append(f"Point   {lat:.3f}, {lon:.3f}")
        if self._domain_label:
            lines.append(self._domain_label)
        qp.setFont(QFont("Helvetica", 10))
        y = 18
        for text in lines:
            rect = QRectF(8, y - 14, self.width() - 16, 18)
            qp.setPen(QPen(QColor("#000000")))
            qp.drawText(rect.translated(1, 1),
                        Qt.AlignLeft | Qt.AlignVCenter, text)
            qp.setPen(QPen(QColor("#eef2f8")))
            qp.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
            y += 18

    def _select_from_pos(self, pos: QPointF, activate: bool = False) -> None:
        lon, lat = self._to_lonlat(pos.x(), pos.y())
        lon = ((lon + 180.0) % 360.0) - 180.0
        lat = max(-89.99, min(89.99, lat))
        self.set_point(lat, lon)
        self.pointSelected.emit(float(lat), float(lon))
        if activate:
            self.pointActivated.emit(float(lat), float(lon))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        super().mouseMoveEvent(event)
        if self._hover_lonlat is not None:
            lon, lat = self._hover_lonlat
            self.setToolTip(f"{lat:.3f}, {lon:.3f}")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        pos = self._pos(event)
        was_drag = self._dragged
        self._drag_last = None
        self._dragged = False
        if was_drag:
            return
        self._select_from_pos(pos)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self._select_from_pos(self._pos(event), activate=True)


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
        self.setMinimumSize(900, 620)
        self.setStyleSheet(PICKER_DARK_QSS)
        self.setAcceptDrops(True)  # drag a sounding file onto the window

        # Keep every opened sounding window alive. Each vendored ``SPCWindow`` is
        # a top-level window parented to this picker, but we also hold a Python
        # reference so it is never garbage-collected out from under Qt.
        self._viewers: list = []
        self._worker: _FetchWorker | None = None
        self._model_worker: _ModelFetchWorker | None = None
        self._settings = QSettings("SHARPpyReimagined", "GUI")
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
        self._save_unit_preferences(self._unit_preferences_from_config(config))
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
        created = False
        if self.config is None:
            try:
                self.statusBar().showMessage("Loading analysis engine\u2026")
                QApplication.processEvents()
            except Exception:
                pass
            self.config = _render().build_config(tempfile.gettempdir())
            created = True
        if created:
            _write_unit_preferences_to_config(self.config, self._unit_preferences())
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
        settings = getattr(self, "_settings", None)
        if settings is None:
            return
        for key, value in _normalize_unit_preferences(preferences).items():
            settings.setValue("units/" + key, value)

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
        time_box.setMinimumHeight(150)
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
        member_layout.addWidget(self._model_member)
        left.addWidget(member_box)

        self._model_fetch_btn = QPushButton("Fetch && Display Forecast Sounding")
        self._model_fetch_btn.setMinimumHeight(36)
        self._model_fetch_btn.clicked.connect(self._model_fetch)
        left.addWidget(self._model_fetch_btn)

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
        busy = self._model_worker is not None and self._model_worker.isRunning()
        if cfg is None:
            self._model_point_status.setText("")
            self._model_fetch_btn.setEnabled(False)
            return
        lat = float(self._model_lat.value())
        lon = float(self._model_lon.value())
        ok = self._model_point_ok()
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
        if self._model_worker is not None and self._model_worker.isRunning():
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
        fxx = self._model_selected_fxx()
        run_time = self._model_run_time()
        member = self._model_member.text().strip() or None \
            if self._model_member.isEnabled() else None
        loc = f"{cfg.label} {lat:.2f}, {lon:.2f}"

        download_dir = tempfile.mkdtemp(
            prefix=f"model_{cfg.key.replace('-', '_')}_{run_time:%Y%m%d%H}_"
                   f"f{fxx:03d}_")
        npz_path = os.path.join(download_dir, "sounding.npz")

        self._set_model_busy(True)
        self.statusBar().showMessage(
            f"Fetching {cfg.label} F{fxx:03d} at {lat:.2f}, {lon:.2f}\u2026")
        self._model_worker = _ModelFetchWorker(
            cfg.key, lat, lon, run_time, fxx, npz_path, loc=loc,
            member=member, download_dir=download_dir, parent=self)
        self._model_worker.finished_ok.connect(self._on_model_fetch_ok)
        self._model_worker.failed.connect(self._on_model_fetch_failed)
        self._model_worker.finished.connect(lambda: self._set_model_busy(False))
        self._model_worker.finished.connect(self._model_worker.deleteLater)
        self._model_worker.start()

    def _set_model_busy(self, busy: bool) -> None:
        if busy:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._model_fetch_btn.setEnabled(False)
            self._model_fetch_btn.setText("Fetching\u2026")
        else:
            QApplication.restoreOverrideCursor()
            self._model_fetch_btn.setText("Fetch && Display Forecast Sounding")
            self._model_update_fetch_state()

    def _on_model_fetch_ok(self, npz_path, label, run_time, fxx) -> None:
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
        except Exception as exc:  # noqa: BLE001
            _cleanup_model_data(npz_path, os.path.dirname(npz_path))
            QMessageBox.critical(self, APP_NAME,
                                 f"Fetched, but could not display:\n{exc}")

    def _on_model_fetch_failed(self, message: str) -> None:
        self.statusBar().showMessage("Forecast model fetch failed")
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
        # Compose the real, interactive SPCWindow with this picker as its Qt
        # parent/controller (so the W key refocuses the picker and Preferences
        # routes here). The window shows itself; we retain a reference.
        win = compose_interactive(self._config(), prof_col, self, stn_id=stn_id)
        if title:
            win.setWindowTitle(title)
        self._prune_closed_viewers()
        self._viewers.append(win)
        return win

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
