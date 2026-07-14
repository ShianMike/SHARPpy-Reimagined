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

"""Sounding-viewer composition, scaling, editing, and export integration."""

from sharpmod.gui_common import (
    APP_NAME,
    APP_VERSION,
    SOUNDING_LIGHT_QSS,
    TIP_LINE,
    _LOGGER,
    _compose_window,
    _render,
    _show_controls_dialog,
)
from sharpmod.gui_sessions import _install_analysis_actions
from sharpmod.gui_settings import (
    _ParcelDialog,
    _apply_default_parcel_to_window,
    _apply_unit_preferences_to_window,
)

_setup_done = False

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

_LEVEL_FIELDS = (
    ("pres", "Pressure (hPa)", 0.1, 1100.0, 0.1),
    ("hght", "Height (m MSL)", -1000.0, 60000.0, 1.0),
    ("tmpc", "Temperature (\u00b0C)", -273.1, 80.0, 0.1),
    ("dwpc", "Dewpoint (\u00b0C)", -273.1, 80.0, 0.1),
    ("wdir", "Wind direction (\u00b0)", 0.0, 359.9, 1.0),
    ("wspd", "Wind speed (kt)", 0.0, 500.0, 1.0),
)


def _finite_profile_value(prof, field: str, idx: int):
    """Return one finite, unmasked profile value or ``None``."""
    try:
        value = np.ma.asarray(getattr(prof, field), dtype=float)[idx]
    except (AttributeError, IndexError, TypeError, ValueError):
        return None
    if np.ma.is_masked(value):
        return None
    value = float(value)
    return value if np.isfinite(value) else None


def _nearest_profile_level(prof, pressure: float):
    """Return the nearest valid pressure index in *prof*, if one exists."""
    try:
        values = np.ma.asarray(prof.pres, dtype=float)
    except (AttributeError, TypeError, ValueError):
        return None
    data = np.asarray(values.filled(np.nan), dtype=float)
    valid = np.flatnonzero(np.isfinite(data))
    if not valid.size or not np.isfinite(pressure):
        return None
    return int(valid[np.argmin(np.abs(data[valid] - float(pressure)))])


def _nearest_valid_neighbor(prof, field: str, idx: int, direction: int):
    """Find the nearest finite value before or after *idx*."""
    try:
        size = len(getattr(prof, field))
    except (AttributeError, TypeError):
        return None
    pos = idx + direction
    while 0 <= pos < size:
        value = _finite_profile_value(prof, field, pos)
        if value is not None:
            return value
        pos += direction
    return None


class _SoundingLevelEditorDialog(QDialog):
    """Validated numeric editor for one physical sounding level."""

    def __init__(self, prof, idx: int, parent=None):
        super().__init__(parent)
        self._prof = prof
        self._idx = int(idx)
        self._original = {}
        self._inputs = {}
        self.setWindowTitle("Edit Sounding Level")

        form = QFormLayout(self)
        for field, label, minimum, maximum, step in _LEVEL_FIELDS:
            spin = QDoubleSpinBox(self)
            spin.setDecimals(1)
            spin.setSingleStep(step)
            spin.setRange(minimum, maximum)
            value = _finite_profile_value(prof, field, self._idx)
            if value is None:
                spin.setEnabled(False)
                spin.setToolTip("This value is missing at the selected level.")
            else:
                self._original[field] = value
                spin.setValue(value)
            self._inputs[field] = spin
            setattr(self, f"_{field}", spin)
            form.addRow(label, spin)

        self._apply_level_order_bounds()
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _apply_level_order_bounds(self) -> None:
        """Constrain pressure and height so the vertical order stays valid."""
        pres = self._inputs["pres"]
        below_pres = _nearest_valid_neighbor(
            self._prof, "pres", self._idx, -1)
        above_pres = _nearest_valid_neighbor(
            self._prof, "pres", self._idx, 1)
        if above_pres is not None:
            pres.setMinimum(above_pres + 0.1)
        if below_pres is not None:
            pres.setMaximum(below_pres - 0.1)

        hght = self._inputs["hght"]
        below_hght = _nearest_valid_neighbor(
            self._prof, "hght", self._idx, -1)
        above_hght = _nearest_valid_neighbor(
            self._prof, "hght", self._idx, 1)
        if below_hght is not None:
            hght.setMinimum(below_hght + 0.1)
        if above_hght is not None:
            hght.setMaximum(above_hght - 0.1)

    def changes(self) -> dict[str, float]:
        """Return a complete edited level, or an empty dict for a no-op."""
        values = {
            field: float(spin.value())
            for field, spin in self._inputs.items()
            if spin.isEnabled() and field in self._original
        }
        if not any(
                not np.isclose(value, self._original[field], atol=0.049)
                for field, value in values.items()):
            return {}
        if values.get("dwpc", -np.inf) > values.get("tmpc", np.inf):
            raise ValueError("Dewpoint cannot exceed temperature.")
        # Send the whole editable level. ProfileCollection can then retain one
        # coherent original snapshot and Reset Skew-T can restore every field.
        return values
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
    R.install_render_patches()
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
    _install_analysis_actions(win, controller)
    _install_units_menu(win, controller)
    try:
        _apply_unit_preferences_to_window(win, controller._config())
    except Exception:
        pass

    # Restore the legacy "Show Parcels" double-click on the parcel inset (the
    # fork replaces the vendored parcel panel with its IndexBoard, so the
    # vendored double-click is otherwise unreachable).
    _install_parcel_selector(win)
    _install_level_editor(win)
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


def _install_level_editor(win) -> None:
    """Add a validated numeric level editor to the Skew-T context menu."""
    sw = getattr(win, "spc_widget", None)
    skewt = getattr(sw, "sound", None)
    popup = getattr(skewt, "popupmenu", None)
    if skewt is None or popup is None or getattr(
            skewt, "_sharpmod_level_editor_installed", False):
        return

    reset_action = None
    for existing in popup.actions():
        if existing.text() == "Reset Skew-T":
            reset_action = existing
            break

    edit_action = QAction("Edit Nearest Level\u2026", skewt)

    def _edit_nearest_level():
        try:
            collections = getattr(sw, "prof_collections", ())
            pc_idx = int(getattr(sw, "pc_idx", 0))
            collection = collections[pc_idx]
            if collection.isEnsemble():
                QMessageBox.warning(
                    win,
                    "Edit Sounding Level",
                    "Ensemble profiles cannot be edited. Select a single "
                    "observed or deterministic sounding first.",
                )
                return
        except (AttributeError, IndexError, TypeError, ValueError):
            collection = None

        prof = getattr(skewt, "prof", None)
        cursor = getattr(skewt, "cursor_loc", None)
        if prof is None or cursor is None:
            QMessageBox.information(
                win,
                "Edit Sounding Level",
                "Right-click near the level you want to edit, then choose "
                "Edit Nearest Level again.",
            )
            return
        try:
            pressure = float(skewt.pix_to_pres(cursor.y()))
        except (AttributeError, TypeError, ValueError):
            return
        idx = _nearest_profile_level(prof, pressure)
        if idx is None:
            QMessageBox.warning(
                win, "Edit Sounding Level",
                "No valid pressure levels are available in this sounding.")
            return

        dialog = _SoundingLevelEditorDialog(prof, idx, parent=win)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            changes = dialog.changes()
        except ValueError as exc:
            QMessageBox.warning(win, "Invalid Sounding Level", str(exc))
            return
        if not changes:
            return
        try:
            skewt.modified.emit(idx, changes)
            logging.info(
                "Edited sounding level index=%d pressure=%.1f fields=%s",
                idx, pressure, ",".join(changes),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            QMessageBox.warning(
                win, "Edit Sounding Level",
                f"The sounding could not be updated:\n{exc}")

    edit_action.triggered.connect(_edit_nearest_level)
    if reset_action is not None:
        popup.insertAction(reset_action, edit_action)
        try:
            reset_action.triggered.disconnect()
        except (RuntimeError, TypeError):
            pass
        reset_action.triggered.connect(
            lambda: skewt.reset.emit(
                ["pres", "hght", "tmpc", "dwpc", "wdir", "wspd"]))
    else:
        popup.addAction(edit_action)

    skewt._sharpmod_level_editor_action = edit_action
    skewt._sharpmod_level_editor_installed = True
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
