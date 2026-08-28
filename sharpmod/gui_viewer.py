from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import time
import weakref
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np

"""Sounding-viewer composition, scaling, editing, and export integration."""

from sharpmod.gui_common import (
    APP_NAME,
    APP_VERSION,
    TIP_LINE,
    _LOGGER,
    _compose_window,
    _install_fullscreen_action,
    _render,
    _show_controls_dialog,
)
from sharpmod.gui_sessions import _install_analysis_actions
from sharpmod.gui_settings import (
    _ParcelDialog,
    _apply_default_parcel_to_window,
    _apply_unit_preferences_to_window,
)
from sharpmod.theme import (
    CONTROL_H,
    FIELD_W,
    OBJ_CANVAS_HOST,
    OBJ_DOCK_TITLE,
    OBJ_GHOST,
    OBJ_HEADER_BAR,
    OBJ_HINT,
    OBJ_NAV_RAIL,
    OBJ_NUMERIC,
    OBJ_REPORT,
    OBJ_SECTION_LABEL,
    OBJ_SIDEBAR,
    PROP_COMPACT,
    SPACE,
    VIEWER_SIDEBAR_W,
    ZOOM_SLIDER_W,
)

_setup_done = False

from qtpy.QtCore import (
    Qt, QThread, QTimer, Signal, QDate, QSettings, QPoint, QPointF, QRectF,
    QSize, QUrl,
)
from qtpy.QtGui import (
    QAction, QPainter, QColor, QPen, QBrush, QPolygonF, QFont, QPixmap, QIcon,
    QTransform, QDesktopServices, QWheelEvent,
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
    QPlainTextEdit,
    QToolBar,
    QSlider,
    QDockWidget,
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
    # Match the headless renderer: generic model/coordinate station ids are
    # replaced by the cached CONUS town title before any widget paints.
    _render()._resolve_location_title(prof_col, explicit_loc=loc)


def _settle_layout_events(app, passes: int = 2) -> None:
    """Let Qt apply pending layout/resize work between manual grow passes.

    These calls look like an obvious place to save time -- they are about a third
    of the layout phase -- so the measurements are recorded here to save the next
    person the experiment. Composing the HRRR example offscreen, varying only the
    pass counts at the three call sites:

        passes      elapsed    canvas
        (2, 2, 6)    898 ms    1630x1091   <- shipped
        (2, 2, 3)   1019 ms    1630x1091
        (2, 2, 1)   1029 ms    1630x1091
        (1, 1, 1)    759 ms    1910x1291   <- wrong geometry

    Two findings. Cutting the *final* settle does not save anything: the deferred
    layout work still has to happen, and it comes back slower elsewhere. Cutting
    either of the first two is worse than slow -- the layout has not settled when
    ``_grow_for_family_panels`` and ``enlarge_canvas`` read the current sizes, so
    they grow from stale numbers and the canvas lands at 1910x1291 instead of
    1630x1091. That size is the geometry contract the PNG renderer shares, so any
    change to it is a defect regardless of the time saved.

    See ``benchmarks/benchmark_gui_startup.py`` for the harness.
    """
    for _ in range(max(1, passes)):
        app.processEvents()


def _collect_closed_viewer_cycles() -> None:
    """Collect Python cycles after a deleted viewer returns to the event loop."""
    import gc

    collected = gc.collect()
    _LOGGER.debug("viewer.gc_after_close collected=%d", collected)


def _install_viewer_lifecycle(win, controller) -> None:
    """Delete a sounding window on close and release picker ownership.

    ``QWidget.close()`` hides a window by default.  Sounding viewers are large
    object trees, so merely hiding one leaves its Qt widgets and render state
    alive for the rest of the picker session.  Deleting the native object also
    guarantees that any data-cleanup hooks attached to ``destroyed`` run.
    """
    win.setAttribute(Qt.WA_DeleteOnClose, True)

    # PickerWindow retains viewers so preferences and multi-sounding mode can
    # address them.  Remove the dead wrapper as soon as Qt destroys the native
    # window; capture only weak/id references so this hook cannot itself keep
    # either QObject alive.
    try:
        controller_ref = weakref.ref(controller)
    except TypeError:
        return
    viewer_id = id(win)

    def _release_reference(*_args) -> None:
        owner = controller_ref()
        if owner is None:
            return
        viewers = getattr(owner, "_viewers", None)
        if isinstance(viewers, list):
            viewers[:] = [viewer for viewer in viewers
                          if id(viewer) != viewer_id]
        # SPCWindow's interconnected widgets/signals form Python cycles. Qt has
        # deleted the native tree at this point, but waiting for an arbitrary
        # later cyclic-GC pass retains roughly one viewer's heap per close.
        # Run collection on the next event-loop turn, outside the destruction
        # callback itself, so repeated open/close sessions plateau promptly.
        QTimer.singleShot(0, _collect_closed_viewer_cycles)

    win.destroyed.connect(_release_reference)


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
    _install_viewer_lifecycle(win, controller)

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
    # No window-level style sheet: the chrome theme lives on the QApplication
    # so the picker and every sounding window share one visual language. This
    # used to force a hardcoded light theme here, which meant opening a sounding
    # jumped from dark chrome to light and put the default black canvas inside a
    # light-grey frame.
    R.rebrand_version_label(win, f"{APP_NAME} v{APP_VERSION}")

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
    _install_data_inspector(win, prof_col)
    try:
        from sharpmod.gui_timeline import install_timeline_controls

        install_timeline_controls(win, prof_col)
    except Exception:
        _LOGGER.exception("forecast_timeline.install_failed")
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

    # Zoom controls. Installed before the fit below so the toolbar's height is
    # included in the window chrome that the fit measures; the actions are
    # connected afterwards, once the sounding host exists.
    _install_view_controls(win)

    # The context sidebar, also before the fit so its width is part of the
    # chrome. It goes after _install_data_inspector because its Source &
    # Quality button triggers that action, and after _install_view_controls
    # because its show/hide toggle is added to the View menu.
    _install_sounding_sidebar(win)

    # Fill the top strip with a compact interaction tip bar (also the on-screen
    # how-to). Done last so it wraps the fully composed spc_widget.
    _install_tip_bar(win, controller)

    # Help menu. After the tip bar, because it offers to show the tips again.
    _install_help_menu(win)

    # Keep the real sounding widget at its natural (CLI-identical) size inside
    # a non-resizing scroll host. Letting the Windows QMainWindow/graphics proxy
    # recompute the child geometry snaps the canvas back to SHARPpy's flatter
    # 1180x800-era size and squishes the Skew-T/hodograph.
    _fit_window_to_screen(app, win)

    # The sounding host now exists, so the View actions can be pointed at it.
    _bind_view_controls(win)

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
        # Named, not inline-styled. An inline style sheet outranks the
        # application sheet and is never recomputed, so baking the colour in
        # here left the surround pinned to whichever theme happened to be
        # current at construction when the user switched colour style with a
        # sounding open. OBJ_CANVAS_HOST carries the same declarations.
        self.setObjectName(OBJ_CANVAS_HOST)
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
    """Host the composed sounding, scaled to fit or to a user-chosen zoom.

    Wraps the fixed-size ``spc_widget`` in a graphics scene and applies a view
    transform. The widget itself stays pinned at its natural render geometry --
    only a view transform scales it -- so the proportions match the PNG renderer
    and interactive editing and exports are unaffected.

    Two modes:

    ``fit``
        The default. The whole sounding is scaled to fit the viewport, capped at
        1:1 so a large screen never upscales and blurs the text. Recomputed on
        every resize.
    ``manual``
        An explicit zoom factor set by the user. Scrollbars appear as needed and
        the view can be panned, so the sounding is readable on a display too
        small to show it whole.

    Manual zoom exists because the fit cap made the sounding uncomfortably small
    on a 1920x1080 display -- the entire layout was visible but the index tables
    and parameter values were near-illegible, with no way to magnify a region.

    Interaction constraints
    -----------------------
    The scene contains a *live, interactive* widget: the Skew-T accepts
    click-and-drag profile edits, both panels accept right-click menus, and the
    vendored hodograph and Skew-T already use the **plain mouse wheel** for their
    own zoom. So:

    * plain wheel is passed straight through to the canvas and never zooms the
      view;
    * view zoom is on **Ctrl+wheel**, anchored under the cursor;
    * panning is on the **middle button**, which no canvas interaction uses.
      ``ScrollHandDrag`` is deliberately not used -- it would swallow the
      left-button drags that edit the profile.
    """

    #: Emitted with the effective scale whenever the transform changes, so a
    #: toolbar can display the current zoom without polling.
    scaleChanged = Signal(float)

    #: Manual-zoom bounds. The lower bound is below any realistic fit scale so
    #: "zoom out" still works on a small window; the upper bound is where the
    #: canvas text becomes visibly interpolated.
    MIN_SCALE = 0.20
    MAX_SCALE = 4.00

    #: Multiplier per zoom step. 1.25 gives a perceptible change without
    #: needing many presses to cross a useful range.
    ZOOM_STEP = 1.25

    #: One classic mouse-wheel notch, in ``angleDelta`` units.
    _WHEEL_NOTCH = 120

    def __init__(self, widget, natural_size, parent=None):
        super().__init__(parent)
        self._natural = QSize(max(1, natural_size.width()),
                              max(1, natural_size.height()))
        self._widget = widget
        self._fit_mode = True
        self._scale = 1.0
        self._pan_origin = None
        self._pan_scroll = None

        self.setFrameShape(QFrame.NoFrame)
        # See _FixedSoundingScrollArea: named so the surround follows a runtime
        # theme switch instead of freezing at construction.
        self.setObjectName(OBJ_CANVAS_HOST)
        # Scrollbars are off while fitting (there is nothing to scroll) and
        # switch to as-needed once a manual zoom can overflow the viewport.
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
        proxy = scene.addWidget(widget)
        # Pin the proxy to the scene origin. addWidget keeps whatever position
        # the widget had under its previous parent, and the sounding arrives here
        # having been the central widget of a QMainWindow, so it carried a y
        # offset of the menu bar's height (29 px in practice). The scene rect
        # below starts at 0, so that offset pushed the *bottom* 29 px of the
        # sounding outside the fitted area -- and fit mode turns the scrollbars
        # off, so the lower index rows were silently unreachable.
        proxy.setPos(0, 0)
        scene.setSceneRect(0, 0, self._natural.width(),
                           self._natural.height())
        self.setScene(scene)

    # -- scale state -------------------------------------------------------- #

    def current_scale(self) -> float:
        """Return the scale currently applied to the view."""
        return self._scale

    def is_fit_mode(self) -> bool:
        return self._fit_mode

    def fit_scale(self) -> float:
        """Return the scale that shows the whole sounding, capped at 1:1."""
        vp = self.viewport().size()
        if vp.width() <= 1 or vp.height() <= 1:
            return 1.0
        return min(vp.width() / self._natural.width(),
                   vp.height() / self._natural.height(), 1.0)

    def _apply_scale(self, scale: float) -> None:
        scale = max(self.MIN_SCALE, min(float(scale), self.MAX_SCALE))
        self._scale = scale
        self.setTransform(QTransform().scale(scale, scale))
        self.scaleChanged.emit(scale)

    def _set_scrollbars_for_mode(self) -> None:
        policy = (Qt.ScrollBarAlwaysOff if self._fit_mode
                  else Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(policy)
        self.setVerticalScrollBarPolicy(policy)

    # -- public actions ----------------------------------------------------- #

    def fit_to_window(self) -> None:
        """Return to fit mode and rescale to show the whole sounding."""
        self._fit_mode = True
        self._set_scrollbars_for_mode()
        self._apply_scale(self.fit_scale())

    def zoom_to(self, scale: float) -> None:
        """Switch to manual mode at an explicit ``scale`` (1.0 = actual size)."""
        self._fit_mode = False
        self._set_scrollbars_for_mode()
        self._apply_scale(scale)

    def zoom_in(self) -> None:
        self.zoom_to(self._scale * self.ZOOM_STEP)

    def zoom_out(self) -> None:
        self.zoom_to(self._scale / self.ZOOM_STEP)

    # -- Qt overrides ------------------------------------------------------- #

    def _refit(self) -> None:
        """Recompute the fit scale. A no-op once the user has set a zoom."""
        if not self._fit_mode:
            return
        vp = self.viewport().size()
        if vp.width() <= 1 or vp.height() <= 1:
            return
        self._apply_scale(self.fit_scale())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refit()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._refit()

    def _panel_under(self, viewport_pos):
        """The canvas child under a viewport point, and the point in its coords.

        The proxy sits at the scene origin at 1:1, so scene coordinates *are*
        coordinates in the composed sounding widget.
        """
        scene_pt = self.mapToScene(viewport_pos).toPoint()
        if not self._widget.rect().contains(scene_pt):
            return None, None
        target = self._widget.childAt(scene_pt) or self._widget
        return target, target.mapFrom(self._widget, scene_pt)

    def _plain_wheel_delta(self, event) -> int:
        """The scroll amount in ``angleDelta`` units, whatever the device sent.

        The vendored zoom reads ``angleDelta`` and nothing else. A classic mouse
        wheel obliges with 120 units per notch, but a Windows precision touchpad
        can report ``angleDelta`` of zero and put the scroll in ``pixelDelta``,
        which moved the zoom not at all.

        The amount is passed through proportionally rather than rounded up to
        whole notches: a touchpad sends a stream of small deltas, and forwarding
        each one keeps the zoom smooth. Quantising instead imposed a threshold
        below which a slow scroll did nothing, which is the complaint in a
        different costume.
        """
        delta = event.angleDelta().y()
        if delta:
            return delta
        pixels = event.pixelDelta().y()
        if not pixels:
            return 0
        # 50 px per notch: enough that a deliberate swipe zooms noticeably
        # without a nudge throwing the scale across the panel.
        return int(round(pixels * (self._WHEEL_NOTCH / 50.0)))

    def _forward_panel_zoom(self, event) -> bool:
        """Send a wheel event the vendored panels understand, and say so.

        Returns ``True`` when the event was handled here. Re-emitting the event
        rather than letting it pass through the graphics proxy fixes two separate
        faults: a full-notch event carrying a ``ScrollUpdate`` phase -- what a
        touchpad sends for every event after the first -- was dropped before it
        reached the panel at all, and a pixel-only event moved nothing. It also
        puts the zoom anchor exactly under the cursor, because the position is
        computed here rather than taken on trust.
        """
        position = (event.position().toPoint()
                    if hasattr(event, "position") else event.pos())
        target, local = self._panel_under(position)
        if target is None:
            return False

        delta = self._plain_wheel_delta(event)
        if not delta:
            return False

        synthetic = QWheelEvent(
            QPointF(local), QPointF(target.mapToGlobal(local)),
            QPoint(0, 0), QPoint(0, delta),
            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        QApplication.sendEvent(target, synthetic)
        return True

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Ctrl+wheel zooms the view; a plain wheel belongs to the canvas."""
        if not (event.modifiers() & Qt.ControlModifier):
            # The vendored Skew-T and hodograph use the plain wheel for their
            # own zoom. Forward it explicitly rather than relying on the
            # graphics proxy: see _forward_panel_zoom for what the proxy route
            # dropped.
            if self._forward_panel_zoom(event):
                event.accept()
                return
            super().wheelEvent(event)
            return

        # Through _plain_wheel_delta, not event.angleDelta() directly. The whole
        # reason that helper exists is that a Windows precision touchpad reports
        # angleDelta of zero and puts the scroll in pixelDelta -- and reading
        # angleDelta here meant Ctrl+scroll did nothing at all on a touchpad,
        # while the guide advertises it as the whole-sounding zoom. The plain
        # wheel was fixed for exactly this device; this branch was not.
        delta = self._plain_wheel_delta(event)
        if not delta:
            # Explicitly unhandled, so the scroll area or an ancestor still gets
            # a chance. A bare return leaves the event accepted and swallows it.
            event.ignore()
            return

        # Anchor under the cursor so Ctrl+wheel magnifies whatever the user is
        # pointing at, then restore the resize anchor used by fit mode.
        previous_anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        try:
            step = self.ZOOM_STEP if delta > 0 else 1.0 / self.ZOOM_STEP
            self.zoom_to(self._scale * step)
        finally:
            self.setTransformationAnchor(previous_anchor)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Begin a middle-button pan, or hand the event to the canvas."""
        if event.button() == Qt.MiddleButton:
            self._pan_origin = event.position().toPoint() \
                if hasattr(event, "position") else event.pos()
            self._pan_scroll = (self.horizontalScrollBar().value(),
                                self.verticalScrollBar().value())
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._pan_origin is not None and self._pan_scroll is not None:
            position = (event.position().toPoint()
                        if hasattr(event, "position") else event.pos())
            delta = position - self._pan_origin
            h_start, v_start = self._pan_scroll
            self.horizontalScrollBar().setValue(h_start - delta.x())
            self.verticalScrollBar().setValue(v_start - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MiddleButton and self._pan_origin is not None:
            self._pan_origin = None
            self._pan_scroll = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


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
        # Toolbars are stacked above the central widget, so they cost the
        # sounding height exactly as the menu bar does. This was omitted, which
        # understated the chrome by the View toolbar's ~57px (and by the timeline
        # toolbar's as well, on a forecast sounding). That mattered beyond a
        # mis-sized window: the same comparison below picks the host, so a screen
        # where the sounding fits without the toolbars but not with them landed
        # on _FixedSoundingScrollArea -- which has no transform, so
        # _bind_view_controls disabled every zoom control and explained that
        # "the sounding already fits this screen at actual size" while the lower
        # index rows were only reachable by scrolling.
        chrome_h = mb_h + _reserved_toolbar_height(win)
        screen = app.primaryScreen().availableGeometry()

        # Reserve space for the window frame (title bar + borders) so the whole
        # window -- including its bottom edge -- stays on screen when it opens
        # non-maximized. Without this the client area can be as tall as the
        # work area and the frame pushes the bottom index tables off-screen.
        FRAME_W = 16
        FRAME_H = 48
        # Docked panels sit beside the central widget, so the sounding never
        # gets their width. Subtracting it here keeps the pre-show estimate
        # honest; without it the window opens too narrow to hold both and the
        # view is briefly squeezed before _finalize_scaled_fit corrects it.
        avail_w = max(320, screen.width() - FRAME_W - _reserved_dock_width(win))
        avail_h = max(240, screen.height() - FRAME_H)

        # Largest uniform scale (never above 1:1) that fits the sounding plus
        # the menu bar and toolbars inside the available work area.
        content_max_h = max(1, avail_h - chrome_h)
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

        # client_w is the sounding's width; the window also has to hold the
        # docked panel beside it, and the menu bar plus toolbars above it.
        win.resize(client_w + _reserved_dock_width(win), client_h + chrome_h)
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
    """Snap the window to wrap the sounding exactly after native realization.

    Runs after the window is shown, when the *actual* menu-bar / window-frame
    heights are known (the pre-show pass can only estimate them). Native 1:1
    windows grow their viewport to the exact sounding size so transient scroll
    bars do not consume 12-13 pixels. Scaled windows recompute the largest
    uniform scale that fits the work area using the measured chrome.
    """
    try:
        view = win.centralWidget()
        vp = view.viewport().size()
        if vp.width() <= 1 or vp.height() <= 1:
            return

        scr = app.primaryScreen().availableGeometry()

        # Measured chrome: everything in the window that is NOT the viewport
        # (menu bar, borders) and the title-bar/frame outside the client area.
        chrome_w = max(0, win.width() - vp.width())
        chrome_h = max(0, win.height() - vp.height())
        frame_w = max(0, win.frameGeometry().width() - win.width())
        frame_h = max(0, win.frameGeometry().height() - win.height())

        if isinstance(view, _FixedSoundingScrollArea):
            nat = view._natural_size
            new_w = nat.width() + chrome_w
            new_h = nat.height() + chrome_h
            if new_w + frame_w <= scr.width() \
                    and new_h + frame_h <= scr.height():
                win.resize(new_w, new_h)
        elif isinstance(view, _ScaledSoundingView):
            nat = view._natural
            avail_vp_w = scr.width() - frame_w - chrome_w
            avail_vp_h = scr.height() - frame_h - chrome_h
            if avail_vp_w <= 1 or avail_vp_h <= 1:
                return

            scale = min(
                avail_vp_w / nat.width(),
                avail_vp_h / nat.height(),
                1.0,
            )
            vp_w = int(round(nat.width() * scale))
            vp_h = int(round(nat.height() * scale))
            new_w = vp_w + chrome_w
            new_h = vp_h + chrome_h

            if abs(new_w - win.width()) > 2 \
                    or abs(new_h - win.height()) > 2:
                win.resize(new_w, new_h)
            view._refit()
        else:
            return

        # Re-centre on the work area, keeping the title bar reachable.
        frame = win.frameGeometry()
        frame.moveCenter(scr.center())
        tl = frame.topLeft()
        tl.setX(max(scr.left(), tl.x()))
        tl.setY(max(scr.top(), tl.y()))
        win.move(tl)
    except Exception:
        pass


def _install_view_controls(win) -> None:
    """Add the View menu and zoom toolbar.

    Called *before* :func:`_fit_window_to_screen` so the toolbar's height is
    part of the window chrome the fit measures. The actions cannot be connected
    yet -- the sounding host does not exist until the fit runs -- so they are
    stored on the window and wired up afterwards by
    :func:`_bind_view_controls`.

    This is the application's only toolbar. Zoom belongs on one because it is
    used repeatedly while reading a sounding, and a menu round-trip per step is
    too slow for that.
    """
    try:
        actions = {}

        # Checkable so the toolbar shows *which* mode is active. Without it,
        # clicking "Fit to Window" while already fitted changes nothing and the
        # button reads as broken.
        act_fit = QAction("Fit to Window", win)
        act_fit.setShortcut("Ctrl+0")
        act_fit.setCheckable(True)
        act_fit.setChecked(True)
        # The tooltips name the trade-off between the two modes, because it is
        # not guessable: the canvas is drawn into a bitmap at its natural size,
        # so 100% is pixel-exact and every other scale is a resample. Fit shows
        # everything but softens the small type; actual size is sharp but needs
        # a scroll to reach the bottom panels.
        act_fit.setToolTip(
            "Scale the whole sounding to fit the window, and stay fitted as it "
            "resizes.\nShows everything, but small type is slightly softened by "
            "the scaling.")
        actions["fit"] = act_fit

        act_actual = QAction("Actual Size", win)
        act_actual.setShortcut("Ctrl+1")
        act_actual.setCheckable(True)
        act_actual.setToolTip(
            "Show the sounding at 1:1 (100%).\nThe sharpest view -- the canvas "
            "is drawn at this size, so nothing is resampled. Middle-drag to "
            "reach the lower panels.")
        actions["actual"] = act_actual

        act_in = QAction("Zoom In", win)
        # Both bindings: Ctrl+= is what an unshifted "+" key actually sends.
        act_in.setShortcuts(["Ctrl++", "Ctrl+="])
        act_in.setToolTip("Zoom in (Ctrl+wheel also works)")
        actions["in"] = act_in

        act_out = QAction("Zoom Out", win)
        act_out.setShortcut("Ctrl+-")
        act_out.setToolTip("Zoom out (Ctrl+wheel also works)")
        actions["out"] = act_out

        menu = win.menuBar().addMenu("View")
        menu.addAction(act_fit)
        menu.addAction(act_actual)
        menu.addSeparator()
        menu.addAction(act_in)
        menu.addAction(act_out)
        menu.addSeparator()
        # Full screen belongs here rather than with the zoom steps: it changes
        # how much room the sounding gets, not how it is scaled into that room.
        # It matters most in this window, where the fit is height-limited, so the
        # title bar and taskbar it reclaims turn straight into a larger sounding.
        _install_fullscreen_action(win, menu)

        bar = QToolBar("View", win)
        bar.setObjectName("viewToolBar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        bar.addAction(act_fit)
        bar.addAction(act_actual)
        bar.addSeparator()
        bar.addAction(act_out)

        # A continuous control beside the stepped buttons: dragging to a target
        # zoom is quicker than pressing "Zoom In" repeatedly, and the handle
        # position shows where the current scale sits within the usable range.
        #
        # Integer percent, because QSlider is integer-only. The view clamps to
        # its own bounds, so these are the authoritative range.
        slider = QSlider(Qt.Horizontal)
        slider.setObjectName("zoomSlider")
        slider.setRange(int(_ScaledSoundingView.MIN_SCALE * 100),
                        int(_ScaledSoundingView.MAX_SCALE * 100))
        slider.setValue(100)
        slider.setFixedWidth(ZOOM_SLIDER_W)
        slider.setToolTip("Drag to zoom")
        # Clicking the groove jumps a page rather than crawling a single
        # percent, which would make the slider feel dead.
        slider.setPageStep(25)
        slider.setSingleStep(5)
        bar.addWidget(slider)

        bar.addAction(act_in)

        readout = QLabel("100%")
        readout.setObjectName(OBJ_NUMERIC)
        readout.setToolTip("Current zoom")
        readout.setAlignment(Qt.AlignCenter)
        readout.setMinimumWidth(FIELD_W["date"])
        bar.addWidget(readout)

        win.addToolBar(Qt.TopToolBarArea, bar)
        win._sharpmod_view_actions = actions
        win._sharpmod_view_menu = menu
        win._sharpmod_zoom_readout = readout
        win._sharpmod_zoom_slider = slider
        win._sharpmod_view_toolbar = bar
    except Exception:
        _LOGGER.exception("view_controls.install_failed")


def _bind_view_controls(win) -> None:
    """Connect the View actions to the sounding host created by the fit.

    Zoom is only available on :class:`_ScaledSoundingView`. When the sounding
    fits the screen at 1:1 the fit uses :class:`_FixedSoundingScrollArea`, a
    plain scroll host with no transform -- there the controls are disabled and
    say why, rather than being present but inert.
    """
    actions = getattr(win, "_sharpmod_view_actions", None)
    if not actions:
        return
    readout = getattr(win, "_sharpmod_zoom_readout", None)
    view = win.centralWidget()

    slider = getattr(win, "_sharpmod_zoom_slider", None)

    if not isinstance(view, _ScaledSoundingView):
        for action in actions.values():
            action.setEnabled(False)
            action.setToolTip(
                "The sounding already fits this screen at actual size")
        if readout is not None:
            readout.setText("100%")
        if slider is not None:
            slider.setEnabled(False)
            slider.setToolTip(
                "The sounding already fits this screen at actual size")
        return

    def _show_scale(scale: float) -> None:
        """Update the readout, the mode checkmarks, and the slider.

        The readout distinguishes an automatic fit from a manual zoom, because
        the two can land on the same percentage and the user needs to know
        whether the scale will follow the next window resize.
        """
        fitted = view.is_fit_mode()
        if readout is not None:
            readout.setText(
                f"Fit \u00b7 {scale * 100:.0f}%" if fitted
                else f"{scale * 100:.0f}%")
        # Reflect the mode without re-entering the triggered handlers.
        for key, checked in (("fit", fitted),
                             ("actual", not fitted and abs(scale - 1.0) < 1e-6)):
            action = actions.get(key)
            if action is None:
                continue
            was = action.blockSignals(True)
            try:
                action.setChecked(checked)
            finally:
                action.blockSignals(was)
        if slider is not None:
            # Signals blocked: the slider is both an input and a display of the
            # same value, so echoing the view's scale back would re-enter
            # _on_slider and fight the user mid-drag (and round-trip the value
            # through integer percent on every frame).
            was = slider.blockSignals(True)
            try:
                slider.setValue(int(round(scale * 100)))
            finally:
                slider.blockSignals(was)

    def _on_slider(percent: int) -> None:
        view.zoom_to(percent / 100.0)

    try:
        view.scaleChanged.connect(_show_scale)
        if slider is not None:
            slider.valueChanged.connect(_on_slider)
        # ``triggered`` on a checkable action passes the new checked state. Fit
        # must stay latched: clicking it while already fitted would otherwise
        # untick it and leave the mode label lying about the actual state.
        actions["fit"].triggered.connect(
            lambda _checked=False: view.fit_to_window())
        actions["actual"].triggered.connect(
            lambda _checked=False: view.zoom_to(1.0))
        actions["in"].triggered.connect(
            lambda _checked=False: view.zoom_in())
        actions["out"].triggered.connect(
            lambda _checked=False: view.zoom_out())
        _show_scale(view.current_scale())
    except Exception:
        _LOGGER.exception("view_controls.bind_failed")


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
        # Weak, and this one is load-bearing beyond the leak. A lambda capturing
        # ``win`` strongly, connected to a button inside the menu bar's corner
        # widget, closes the cycle
        #     win -> menubar -> corner widget -> button -> connection -> lambda
        # back to win, across a C++-side edge Python's cyclic collector cannot
        # traverse. The window's wrapper then survives until interpreter exit,
        # by which point Qt has already torn the C++ side down, and freeing it
        # is an access violation: measured at 6 crashes in 14 runs with the
        # strong capture and 0 in 14 with this weakref (0xC0000005, no Python
        # traceback -- it happens after the last frame is gone). It aborted the
        # test process on exit and would abort the app when the user quits.
        win_ref = weakref.ref(win)

        tips = QWidget()
        h = QHBoxLayout(tips)
        h.setContentsMargins(6, 0, 8, 0)
        h.setSpacing(8)

        # Semantic object names instead of inline colours, so these follow a
        # theme change rather than staying pinned to one palette.
        lbl = QLabel(TIP_LINE)
        lbl.setObjectName(OBJ_HINT)
        lbl.setWordWrap(False)

        guide_btn = QToolButton()
        guide_btn.setText("Full guide")
        guide_btn.setToolTip("Show all sounding-window controls")
        guide_btn.setAutoRaise(True)
        guide_btn.setObjectName(OBJ_GHOST)

        def _open_guide() -> None:
            window = win_ref()
            if window is not None:
                _show_controls_dialog(window)

        guide_btn.clicked.connect(_open_guide)

        close_btn = QToolButton()
        close_btn.setText("\u2715")
        close_btn.setToolTip("Hide these tips")
        close_btn.setAutoRaise(True)
        close_btn.setObjectName(OBJ_GHOST)

        h.addWidget(lbl)
        h.addWidget(guide_btn)
        h.addWidget(close_btn)

        menubar.setCornerWidget(tips, Qt.TopRightCorner)
        tips.setVisible(not hidden)

        def _dismiss():
            tips.setVisible(False)
            if settings is not None:
                settings.setValue("hide_tips", True)
            # Keep the Help menu's checkmark honest. Without this the strip is
            # hidden while "Show Interaction Tips" still reads checked, so the
            # first click there is a visual no-op and bringing the strip back
            # takes two -- and dismiss-then-restore is the main reason that menu
            # item exists. The action is installed after this function runs, so
            # it is looked up at click time rather than captured.
            window = win_ref()
            action = getattr(window, "_sharpmod_tips_action", None)
            if action is not None and action.isChecked():
                # Signals blocked: _toggle_tips would re-run the hide and
                # re-write the preference that was just set.
                action.blockSignals(True)
                action.setChecked(False)
                action.blockSignals(False)

        close_btn.clicked.connect(_dismiss)
        # Published so the Help menu can bring the strip back. Dismissing it
        # persists, and it used to carry the only route to the full guide, so
        # closing it left the window with no way to look anything up.
        win._sharpmod_tips = tips
        win._sharpmod_tips_settings = settings
    except Exception:
        # A tip hiccup must never block the interactive window -- but it must
        # not vanish either. _install_help_menu keys the "Show Interaction Tips"
        # item off ``_sharpmod_tips``, which is set on the last lines above, so
        # a failure anywhere before them silently drops that menu item while
        # leaving the window looking intact.
        _LOGGER.exception("tip_bar.install_failed")


def _install_help_menu(win) -> None:
    """Add the sounding window's Help menu.

    The window previously had no Help menu at all. The interaction guide was
    reachable only from the "Full guide" button on the tips strip -- and that
    strip has a dismiss button whose preference persists, so closing it removed
    the last route to the guide permanently. The picker's Help menu still had a
    copy, but nothing in the sounding window pointed there.
    """
    try:
        menu = win.menuBar().addMenu("&Help")

        # Weak, deliberately: the action is a child of the window, so a closure
        # capturing ``win`` strongly closes a cycle through a C++-side signal
        # connection that Python's cyclic GC cannot traverse, pinning the whole
        # sounding window for the life of the process. Same reasoning as
        # _install_fullscreen_action; see test_gui_viewer_lifecycle.
        win_ref = weakref.ref(win)

        guide = QAction("&Controls and Shortcuts", win)
        guide.setShortcut("F1")
        guide.setToolTip("Every mouse and keyboard interaction in this window")

        def _open_guide() -> None:
            window = win_ref()
            if window is not None:
                _show_controls_dialog(window)

        guide.triggered.connect(_open_guide)
        menu.addAction(guide)

        menu.addSeparator()

        tips = getattr(win, "_sharpmod_tips", None)
        if tips is not None:
            # Resolved once, here, so the handler below captures the settings
            # object rather than the window -- same cycle, and this one does not
            # even need the window.
            tip_settings = getattr(win, "_sharpmod_tips_settings", None)

            show_tips = QAction("Show Interaction &Tips", win)
            show_tips.setCheckable(True)
            # isVisibleTo, not isVisible: this runs while compose_interactive
            # still has the window hidden, and isVisible() is False for every
            # descendant of an unshown window. Reading it there opened the
            # action unchecked while the strip was on screen, which made the
            # first click a visual no-op and hiding the strip take two clicks.
            show_tips.setChecked(tips.isVisibleTo(win))
            show_tips.setToolTip(
                "The one-line reminder strip along the top of this window")

            def _toggle_tips(checked: bool) -> None:
                tips.setVisible(checked)
                if tip_settings is not None:
                    tip_settings.setValue("hide_tips", not checked)

            show_tips.toggled.connect(_toggle_tips)
            menu.addAction(show_tips)
            win._sharpmod_tips_action = show_tips

        win._sharpmod_help_menu = menu
    except Exception:
        _LOGGER.exception("help_menu.install_failed")
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
        # See _install_export_menu. ``board`` is a descendant of the window, so
        # a handler capturing ``win`` strongly closes the same uncollectable
        # cycle -- and that cycle is what makes teardown crash, not just leak.
        win_ref = weakref.ref(win)

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
            win = win_ref()
            if win is None:
                return
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
        # Parcel-selector wiring must never block the interactive window, but a
        # silent failure here removes the parcel selector with no trace of why.
        _LOGGER.exception("parcel_selector.install_failed")


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
    # See _install_export_menu. ``skewt`` is a descendant of the window, so this
    # action's connection would otherwise pin the window through the same
    # uncollectable cycle that makes teardown crash.
    win_ref = weakref.ref(win)

    def _edit_nearest_level():
        win = win_ref()
        if win is None:
            return
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


def _focused_profile_collection(win, fallback):
    """Return the collection currently focused in a combined viewer."""
    try:
        widget = win.spc_widget
        return widget.prof_collections[int(widget.pc_idx)]
    except (AttributeError, IndexError, TypeError, ValueError):
        return fallback


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
    # Every handler below re-resolves the window from this weakref into a local
    # of the same name, so none of them closes over the outer ``win``. The
    # actions are children of the window, and Qt holds their connections
    # C++-side, so a strong capture would make win -> action -> connection ->
    # closure -> win an uncollectable cycle. That is not merely a leak: the
    # window's Python wrapper then survives to interpreter exit, after Qt has
    # torn the C++ side down, and freeing it is an access violation. Measured on
    # the tip bar, which had the same shape: 6 crashes in 14 runs before, 0 in 14
    # after. See _install_fullscreen_action for the original of this pattern.
    win_ref = weakref.ref(win)

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
        win = win_ref()
        if win is None:
            return
        try:
            win.statusBar().showMessage(message, 4000)
        except Exception:
            pass

    def export_image(image_mode: str) -> None:
        win = win_ref()
        if win is None:
            return
        labels = {
            getattr(R, "PNG_IMAGE_HD", "hd"): ("HD", "_hd"),
            getattr(R, "PNG_IMAGE_UHD", "uhd"): ("UHD", "_uhd"),
            getattr(R, "PNG_IMAGE_LOSSLESS", "lossless"):
                ("Lossless", "_lossless"),
        }
        label, suffix = labels.get(image_mode, ("HD", "_hd"))
        focused = _focused_profile_collection(win, prof_col)
        base = _default_export_basename(focused)
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
        win = win_ref()
        if win is None:
            return
        try:
            pixmap = R.grab_widget_pixmap(win.spc_widget)
            QApplication.clipboard().setPixmap(pixmap)
            _notify("Sounding image copied to clipboard")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(win, APP_NAME,
                                f"Could not copy image:\n{exc}")

    def export_text() -> None:
        win = win_ref()
        if win is None:
            return
        focused = _focused_profile_collection(win, prof_col)
        base = _default_export_basename(focused)
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
        _LOGGER.exception("export_menu.install_failed")


class _SoundingSidebar(QFrame):
    """Right-hand context panel for the loaded soundings.

    Surfaces the two pieces of viewer state that previously had no on-screen
    representation at all:

    * **Which sounding is focused, and how to change it.** Upstream only
      exposes this as ``Profiles`` -> a submenu per sounding -> ``Focus``: a
      three-level dive that never shows which one is currently active. ``Space``
      cycles, but blindly.
    * **Which ensemble member is highlighted.** Upstream binds this to the
      ``Up``/``Down`` arrows with no visible control and no member names.

    Deliberately *not* included: forecast-time stepping. That already has a
    dedicated toolbar (:func:`sharpmod.gui_timeline.install_timeline_controls`)
    with prev/next, a scrub slider, and looping playback, so repeating it here
    would be two controls for one piece of state.

    The panel reflects state rather than owning it -- every mutation goes
    through the vendored widget's own methods, and :meth:`refresh` re-reads
    from it. ``updateProfs`` is the single funnel every upstream state change
    passes through, so :func:`_install_sounding_sidebar` wraps that to keep the
    panel in sync no matter whether the change came from this panel, a menu, or
    a key press.
    """

    def __init__(self, win, parent=None):
        super().__init__(parent)
        # Weak, deliberately. Connecting a bound method to a child's signal
        # makes Qt hold a C++-side reference to this panel, which Python's
        # cyclic GC cannot see -- so a strong reference here would pin the whole
        # sounding window's wrapper for the process lifetime and every
        # open/close cycle would retain a viewer. See
        # test_gui_viewer_lifecycle.py.
        self._win_ref = weakref.ref(win)
        self.setObjectName(OBJ_SIDEBAR)
        # Fixed, not a minimum. The width is a measured budget -- wide enough to
        # be useful, narrow enough that the sounding still fits unclipped at
        # 100% (see VIEWER_SIDEBAR_W). A minimum alone let the panel's own
        # content size hint win, which silently pushed it back to ~264 px and
        # clipped the canvas at Actual Size.
        self.setFixedWidth(VIEWER_SIDEBAR_W)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["md"], SPACE["md"],
                                 SPACE["md"], SPACE["md"])
        outer.setSpacing(SPACE["md"])

        # --- Loaded soundings ---
        outer.addWidget(self._section_label("LOADED SOUNDINGS"))
        self._list = QListWidget(self)
        self._list.setObjectName(OBJ_NAV_RAIL)
        # Long place names ("Miles Grove Township, Illinois") exceed the panel
        # width, which is set by the sounding's needs rather than the label's.
        # Elide rather than clip, and keep the full text in the tooltip.
        self._list.setTextElideMode(Qt.ElideRight)
        self._list.setWordWrap(False)
        self._list.setToolTip(
            "Click a sounding to bring it into focus (Space also cycles)")
        self._list.currentItemChanged.connect(self._on_pick)
        outer.addWidget(self._list)

        self._empty = QLabel(
            "Open another sounding from the picker to compare it here.")
        self._empty.setObjectName(OBJ_HINT)
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)

        # Default button treatment, not OBJ_GHOST: ghost is for borderless
        # tertiary actions, and a borderless label floating under the list does
        # not read as clickable.
        self._remove = QPushButton("Remove Focused")
        self._remove.setToolTip(
            "Close the focused sounding and keep the others open")
        self._remove.clicked.connect(self._on_remove)
        outer.addWidget(self._remove)

        # --- Ensemble members ---
        self._member_label = self._section_label("ENSEMBLE MEMBER")
        outer.addWidget(self._member_label)
        self._members = QListWidget(self)
        self._members.setObjectName(OBJ_NAV_RAIL)
        # Member names are one line, so they must not inherit the two-line row
        # height the sounding list needs -- at 54 px a list of member names
        # reads as unfinished rather than spacious.
        self._members.setProperty(PROP_COMPACT, True)
        self._members.setToolTip(
            "Highlight a member (the Up/Down arrows also step through these)")
        self._members.currentItemChanged.connect(self._on_member)
        outer.addWidget(self._members)

        outer.addStretch(1)

        # --- Provenance ---
        self._inspect = QPushButton("Source && Quality\u2026")
        self._inspect.setToolTip(
            "Extractor provenance and structural checks for the focused "
            "sounding")
        self._inspect.clicked.connect(self._on_inspect)
        outer.addWidget(self._inspect)

        # Guards re-entry while refresh() is writing into the lists: setting
        # the current row emits currentItemChanged, which would otherwise be
        # read as a user pick and re-focus the collection mid-refresh.
        self._syncing = False
        self.refresh()

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(OBJ_SECTION_LABEL)
        return label

    # -- state ----------------------------------------------------------
    def _window(self):
        """The sounding window, or ``None`` once it has been destroyed."""
        return self._win_ref()

    def _widget(self):
        return getattr(self._window(), "spc_widget", None)

    def _focused_collection(self):
        sw = self._widget()
        try:
            return sw.prof_collections[int(sw.pc_idx)]
        except (AttributeError, IndexError, TypeError, ValueError):
            return None

    def refresh(self) -> None:
        """Re-read the vendored widget and repaint the panel."""
        sw = self._widget()
        if sw is None:
            return
        self._syncing = True
        try:
            self._refresh_soundings(sw)
            self._refresh_members()
        except Exception:
            _LOGGER.exception("sounding_sidebar.refresh_failed")
        finally:
            self._syncing = False

    def _refresh_soundings(self, sw) -> None:
        ids = list(getattr(sw, "prof_ids", []) or [])
        collections = list(getattr(sw, "prof_collections", []) or [])
        try:
            active = int(sw.pc_idx)
        except (AttributeError, TypeError, ValueError):
            active = 0

        self._list.clear()
        for idx, prof_id in enumerate(ids):
            collection = collections[idx] if idx < len(collections) else None
            item = QListWidgetItem(self._describe(prof_id, collection))
            item.setData(Qt.UserRole, prof_id)
            item.setToolTip(prof_id)
            self._list.addItem(item)
        if 0 <= active < self._list.count():
            self._list.setCurrentRow(active)

        multiple = len(ids) > 1
        # One sounding is not an error state, so the hint only appears when the
        # comparison feature is actually unused -- and the list still shows the
        # single sounding's identity, which is useful on its own.
        self._empty.setVisible(not multiple)
        # Mirrors upstream, which hides "Remove" while a single sounding is
        # loaded: removing the last one would leave an empty window.
        self._remove.setEnabled(multiple)
        self._remove.setToolTip(
            "Close the focused sounding and keep the others open" if multiple
            else "The only loaded sounding cannot be removed")
        # Height-to-content, so a single sounding does not leave a tall empty
        # well above the member list.
        self._list.setFixedHeight(self._list_height(self._list, len(ids)))

    def _refresh_members(self) -> None:
        collection = self._focused_collection()
        members: list[str] = []
        current = None
        if collection is not None:
            try:
                if collection.isEnsemble():
                    profs = collection.getCurrentProfs() or {}
                    members = sorted(profs.keys())
                    highlighted = collection.getHighlightedProf()
                    for name, prof in profs.items():
                        if prof is highlighted:
                            current = name
                            break
            except Exception:
                members = []

        # A deterministic sounding has no members to choose between, so the
        # whole section is hidden rather than shown empty or disabled.
        show = len(members) > 1
        self._member_label.setVisible(show)
        self._members.setVisible(show)
        if not show:
            self._members.clear()
            return

        self._members.clear()
        for name in members:
            item = QListWidgetItem(name or "(unnamed)")
            item.setData(Qt.UserRole, name)
            self._members.addItem(item)
        if current in members:
            self._members.setCurrentRow(members.index(current))
        self._members.setFixedHeight(
            self._list_height(self._members, len(members)))

    @staticmethod
    def _list_height(widget: QListWidget, rows: int) -> int:
        """Exact height for ``rows`` items, capped so long lists still scroll."""
        rows = max(1, rows)
        row_h = widget.sizeHintForRow(0) if widget.count() else 0
        if row_h <= 0:
            row_h = CONTROL_H["md"]
        frame = 2 * widget.frameWidth()
        visible = min(rows, 6)
        return visible * row_h + frame + SPACE["xs"]

    @staticmethod
    def _describe(prof_id: str, collection) -> str:
        """Two-line row: location, then the model and run that produced it.

        Upstream's own label is a single ``"KOUN (27/1200Z GFS)"`` string. Split
        across two lines the location -- the thing being compared -- is scannable
        down the list instead of being read out of a parenthesised suffix.
        """
        loc = model = run = None
        if collection is not None:
            for key, target in (("loc", "loc"), ("model", "model")):
                try:
                    value = collection.getMeta(key)
                except Exception:
                    value = None
                if value:
                    if target == "loc":
                        loc = str(value)
                    else:
                        model = str(value)
            try:
                run_dt = collection.getMeta("run")
                run = run_dt.strftime("%d %b %H%MZ")
            except Exception:
                run = None
        if not loc:
            return prof_id
        detail = " \u00b7 ".join(part for part in (model, run) if part)
        return f"{loc}\n{detail}" if detail else loc

    # -- actions --------------------------------------------------------
    def _on_pick(self, current, _previous) -> None:
        if self._syncing or current is None:
            return
        sw = self._widget()
        prof_id = current.data(Qt.UserRole)
        if sw is None or not prof_id:
            return
        try:
            sw.setProfileCollection(prof_id)
        except Exception:
            _LOGGER.exception("sounding_sidebar.focus_failed")

    def _on_remove(self) -> None:
        item = self._list.currentItem()
        prof_id = item.data(Qt.UserRole) if item is not None else None
        win = self._window()
        if not prof_id or win is None:
            return
        try:
            # Window-level, not widget-level: this also hides the matching
            # Profiles submenu entry, which the widget-level call leaves behind.
            win.rmProfileCollection(prof_id)
        except Exception:
            _LOGGER.exception("sounding_sidebar.remove_failed")
        self.refresh()

    def _on_member(self, current, _previous) -> None:
        if self._syncing or current is None:
            return
        collection = self._focused_collection()
        name = current.data(Qt.UserRole)
        sw = self._widget()
        if collection is None or sw is None or name is None:
            return
        try:
            collection.setHighlightedMember(name)
            sw.updateProfs()
        except Exception:
            _LOGGER.exception("sounding_sidebar.member_failed")

    def _on_inspect(self) -> None:
        action = getattr(self._window(), "_sharpmod_data_inspector_action",
                         None)
        if action is not None:
            action.trigger()


def _dock_title_bar(dock: QDockWidget, title: str) -> QFrame:
    """Build a themed title bar with a properly sized close button.

    Replaces Qt's built-in dock title bar. The built-in one cannot be themed
    usefully here: the Fusion style computes the close button's rectangle from
    title-bar metrics and ignores a QSS ``width``/``height``, leaving a roughly
    16x9px target -- and that button is the panel's only visible affordance for
    dismissing it.
    """
    bar = QFrame(dock)
    bar.setObjectName(OBJ_HEADER_BAR)
    row = QHBoxLayout(bar)
    row.setContentsMargins(SPACE["md"], SPACE["xs"], SPACE["xs"], SPACE["xs"])
    row.setSpacing(SPACE["sm"])

    label = QLabel(title, bar)
    label.setObjectName(OBJ_DOCK_TITLE)
    row.addWidget(label)
    row.addStretch(1)

    close = QToolButton(bar)
    close.setObjectName(OBJ_GHOST)
    # Opts out of the shared button min-height, which would otherwise beat
    # setFixedSize and inflate this header to 50px.
    # Size comes from the style sheet, not setFixedSize: QStyleSheetStyle
    # recomputes size constraints from QSS and would override it anyway.
    close.setProperty(PROP_COMPACT, True)
    close.setText("\u2715")
    close.setToolTip(f"Hide the {title.lower()} (Ctrl+B)")
    close.clicked.connect(dock.close)
    row.addWidget(close)
    return bar


def _reserved_toolbar_height(win) -> int:
    """Height the top-area toolbars take out of the central widget.

    Counterpart to :func:`_reserved_dock_width`, and needed for the same reason:
    :func:`_fit_window_to_screen` sizes the window from the *screen*, so it has
    to know the sounding does not get the full height. Only the top area counts
    -- a left or right toolbar costs width, not height, and none is used here.

    Uses ``not isHidden()`` rather than ``isVisible()``: the only caller runs
    while the window is still hidden, where ``isVisible()`` is False for every
    descendant. See :func:`_reserved_dock_width`.
    """
    total = 0
    try:
        for bar in win.findChildren(QToolBar):
            if bar.isHidden() or bar.isFloating():
                continue
            if win.toolBarArea(bar) != Qt.TopToolBarArea:
                continue
            total += max(bar.height(), bar.sizeHint().height())
    except Exception:
        return 0
    return total


def _reserved_dock_width(win) -> int:
    """Width the visible docks take out of the central widget's viewport.

    The fit math sizes the window from the *screen*, so it has to know that the
    sounding does not get the full width. After the window is realized
    :func:`_finalize_scaled_fit` measures the viewport directly; this is only
    for the pre-show pass.

    The predicate is ``not isHidden()``, not ``isVisible()``. Both callers run
    from :func:`_fit_window_to_screen`, which happens between the ``win.hide()``
    in :func:`compose_interactive` and the matching ``showNormal()`` -- and
    ``isVisible()`` is False for *every* descendant of an unshown window, so the
    visible-dock test rejected the dock unconditionally and this returned 0 on
    every production call. ``isHidden()`` reflects the widget's own explicit
    hide state rather than the ancestor chain, so it still answers False for a
    dock the user closed while remaining correct before the first show.
    ``sizeHint()`` is valid either way; only the guard was wrong.
    """
    total = 0
    try:
        for dock in win.findChildren(QDockWidget):
            if not dock.isHidden() and not dock.isFloating():
                total += max(dock.width(), dock.sizeHint().width())
    except Exception:
        return 0
    return total


def _install_sounding_sidebar(win) -> None:
    """Dock the sounding context panel on the right.

    A dock is used rather than restructuring the central widget because
    :func:`_fit_window_to_screen` and :func:`_finalize_scaled_fit` both key off
    ``win.centralWidget()``, and the tests construct the sounding hosts
    directly. Docking leaves that contract untouched, and the existing
    ``chrome_w = win.width() - viewport.width()`` measurement already accounts
    for the panel's width with no change to the fit math.

    The width is free: see :data:`sharpmod.theme.VIEWER_SIDEBAR_W`.
    """
    try:
        panel = _SoundingSidebar(win)
        # The title doubles as the View-menu entry via toggleViewAction(), so
        # naming the dock once keeps the menu item and the panel header
        # identical instead of drifting apart.
        dock = QDockWidget("Sounding Panel", win)
        dock.setObjectName("soundingSidebar")
        dock.setWidget(panel)
        # Not floatable: a floating panel would sit over the sounding, and the
        # whole point is to use space the sounding cannot.
        dock.setFeatures(QDockWidget.DockWidgetClosable)
        dock.setAllowedAreas(Qt.RightDockWidgetArea)
        dock.setTitleBarWidget(_dock_title_bar(dock, dock.windowTitle()))
        win.addDockWidget(Qt.RightDockWidgetArea, dock)

        toggle = dock.toggleViewAction()
        toggle.setShortcut("Ctrl+B")
        toggle.setToolTip(
            "Show or hide the sounding list and ensemble members")
        menu = getattr(win, "_sharpmod_view_menu", None)
        if menu is not None:
            menu.addSeparator()
            menu.addAction(toggle)

        win._sharpmod_sidebar = panel
        win._sharpmod_sidebar_dock = dock

        # Every upstream state change -- menu Focus, Space, arrow keys, the
        # timeline toolbar, adding a sounding -- funnels through updateProfs,
        # so wrapping it is what keeps the panel truthful without polling.
        sw = getattr(win, "spc_widget", None)
        original = getattr(sw, "updateProfs", None)
        if original is not None \
                and not getattr(sw, "_sharpmod_profs_wrapped", False):
            # Weak, for the same reason the panel holds the window weakly: the
            # widget is a child of the window, so a strong capture here would
            # close a reference cycle Qt keeps alive outside Python's GC.
            panel_ref = weakref.ref(panel)

            def updateProfs(*args, **kwargs):  # noqa: N802 - matches upstream
                result = original(*args, **kwargs)
                live = panel_ref()
                if live is not None:
                    try:
                        live.refresh()
                    except Exception:
                        _LOGGER.exception("sounding_sidebar.sync_failed")
                return result

            sw.updateProfs = updateProfs
            sw._sharpmod_profs_wrapped = True
    except Exception:
        _LOGGER.exception("sounding_sidebar.install_failed")


def _install_data_inspector(win, prof_col) -> None:
    """Add a copyable source-provenance and conservative QC report."""
    try:
        menu = win.menuBar().addMenu("Data")
        action = QAction("Source && Quality Inspector…", win)
        # See _install_export_menu: resolved into a local of the same name so
        # this handler does not close over the window it is parented to.
        win_ref = weakref.ref(win)

        def show_report(_checked=False):
            from sharpmod.profile_inspector import format_report

            win = win_ref()
            if win is None:
                return

            focused = prof_col
            try:
                widget = win.spc_widget
                focused = widget.prof_collections[int(widget.pc_idx)]
            except (AttributeError, IndexError, TypeError, ValueError):
                pass

            dialog = QDialog(win)
            dialog.setWindowTitle("Sounding Source & Quality")
            # Wider than the old 760x540: the report contains full GRIB URLs and
            # Windows temp paths, which at that width wrapped mid-token.
            dialog.resize(1000, 680)
            layout = QVBoxLayout(dialog)
            intro = QLabel(
                "Extractor provenance and non-mutating structural checks for "
                "the focused sounding."
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)
            report = QPlainTextEdit()
            report.setReadOnly(True)
            # The report is a column-aligned table, so it needs the monospace
            # family -- it was rendering in the proportional UI face, which left
            # every value column ragged. Set via object name so the family comes
            # from the style sheet: render.install_font patches QFont
            # process-wide, so a QFont built here does not survive.
            report.setObjectName(OBJ_REPORT)
            # No wrapping, for the same reason: wrapping a fixed-width table
            # destroys the alignment, and it broke long URLs across lines.
            # A horizontal scrollbar is the honest alternative.
            report.setLineWrapMode(QPlainTextEdit.NoWrap)
            report.setPlainText(format_report(focused))
            layout.addWidget(report, 1)
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            # One connection. Close carries RejectRole, so the extra
            # clicked->accept both raced this and could never mean anything
            # different -- nothing reads the result code.
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            try:
                dialog.exec()
            finally:
                # Parented to the window, so Qt would otherwise keep this
                # dialog -- and its full report text -- alive until the window
                # dies, one per invocation. Scheduled after exec rather than via
                # WA_DeleteOnClose, which deletes from inside the close that
                # ends the modal loop and segfaults on teardown. Guarded because
                # the parent could be destroyed while the modal is up, leaving
                # this wrapper stale. See gui_common._show_controls_dialog.
                try:
                    dialog.deleteLater()
                except RuntimeError:
                    pass

        action.triggered.connect(show_report)
        menu.addAction(action)
        win._sharpmod_data_inspector_action = action
    except Exception:
        _LOGGER.exception("data_inspector.install_failed")


def _install_units_menu(win, controller) -> None:
    """Add a sounding-window Settings menu for fast unit changes."""
    try:
        menu = win.menuBar().addMenu("Settings")
        act_units = QAction("Units\u2026", win)
        act_units.setShortcut("Ctrl+U")
        # See _install_export_menu.
        win_ref = weakref.ref(win)

        def _open_units():
            win = win_ref()
            if win is None:
                return
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
        _LOGGER.exception("units_menu.install_failed")
