"""Public composition module for the interactive sounding viewer.

It installs viewer controls, export, locator, sidebar, editing, and lifecycle behavior
around SHARPpy widgets while preserving the established viewer import surface; focused
implementations live in adjacent ``sharpmod.ui.viewer_*`` modules."""

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



from sharpmod.ui.features.gui_common import (
    APP_NAME,
    APP_VERSION,
    TIP_LINE,
    _LOGGER,
    _compose_window,
    _install_fullscreen_action,
    _render,
    _show_controls_dialog,
    make_status_label,
    set_status_label,
)
from sharpmod.export_paths import (
    ExportDirectoryError,
    export_directory,
    export_file_path,
)
from sharpmod.ui.features.export_presentation import (
    ExportPresentation,
    available_export_path,
    export_filename,
    export_identity,
    load_presentation,
    recent_exports,
    remember_export,
    save_presentation,
)
from sharpmod.ui.features.gui_sessions import _install_analysis_actions
from sharpmod.ui.features.gui_edit_feedback import install_edit_feedback
from sharpmod.ui.features.gui_edit_history import install_edit_history
from sharpmod.ui.features.gui_interaction_mode import install_interaction_mode
from sharpmod.ui.features.gui_sounding_readout import install_linked_readout
from sharpmod.ui.features.gui_settings import (
    _ParcelDialog,
    _apply_default_parcel_to_window,
    _apply_unit_preferences_to_window,
)
from sharpmod.ui.shell import dock_title_bar
from sharpmod.ui.styles.theme import (
    CONTROL_H,
    FIELD_W,
    OBJ_CANVAS_HOST,
    OBJ_GHOST,
    OBJ_HINT,
    OBJ_NAV_RAIL,
    OBJ_NUMERIC,
    OBJ_PLAIN,
    OBJ_REPORT,
    OBJ_SECTION_LABEL,
    OBJ_SIDEBAR,
    OBJ_STATUS,
    PROP_COMPACT,
    SPACE,
    VIEWER_SIDEBAR_W,
    ZOOM_SLIDER_W,
)

_setup_done = False

from qtpy.QtCore import (
    Qt,
    QThread,
    QTimer,
    Signal,
    QDate,
    QSettings,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    QUrl,
    QEvent,
)
from qtpy.QtGui import (
    QAction,
    QActionGroup,
    QPainter,
    QColor,
    QPen,
    QBrush,
    QPolygonF,
    QFont,
    QPixmap,
    QIcon,
    QTransform,
    QDesktopServices,
    QWheelEvent,
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
    QAbstractItemView,
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
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _apply_level_order_bounds(self) -> None:
        """Constrain pressure and height so the vertical order stays valid."""
        pres = self._inputs["pres"]
        below_pres = _nearest_valid_neighbor(self._prof, "pres", self._idx, -1)
        above_pres = _nearest_valid_neighbor(self._prof, "pres", self._idx, 1)
        if above_pres is not None:
            pres.setMinimum(above_pres + 0.1)
        if below_pres is not None:
            pres.setMaximum(below_pres - 0.1)

        hght = self._inputs["hght"]
        below_hght = _nearest_valid_neighbor(self._prof, "hght", self._idx, -1)
        above_hght = _nearest_valid_neighbor(self._prof, "hght", self._idx, 1)
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
            for field, value in values.items()
        ):
            return {}
        if values.get("dwpc", -np.inf) > values.get("tmpc", np.inf):
            raise ValueError("Dewpoint cannot exceed temperature.")
        # Send the whole editable level. ProfileCollection can then retain one
        # coherent original snapshot and Reset Skew-T can restore every field.
        return values


def _ensure_setup(app) -> None:
    """Install fonts + the renderer's vendored-widget monkeypatches once.

    Mirrors the sequence :func:`sharpmod.rendering.cli.render` runs before composing a
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


def _record_install_failure(win, feature: str, error: BaseException) -> None:
    """Expose an optional viewer-tool failure without blocking the sounding.

    Optional chrome should not make a usable sounding fail to open, but logging
    alone made a missing menu or workspace indistinguishable from a feature the
    application never shipped. Keep the detailed exception on the window and
    add one persistent status-bar indicator whose tooltip lists every failure.
    """
    detail = f"{str(feature).strip()}: {type(error).__name__}: {error}"
    failures = getattr(win, "_sharpmod_install_failures", None)
    if not isinstance(failures, list):
        failures = []
        win._sharpmod_install_failures = failures
    if detail not in failures:
        failures.append(detail)

    try:
        status = win.statusBar()
        label = getattr(win, "_sharpmod_install_failure_label", None)
        if label is None:
            label = QLabel("Setup issue", win)
            label.setObjectName(OBJ_STATUS)
            status.addPermanentWidget(label)
            win._sharpmod_install_failure_label = label
        label.setText(
            "Setup issue" if len(failures) == 1 else f"Setup issues ({len(failures)})"
        )
        label.setToolTip("\n".join(failures))
        status.showMessage(
            f"{feature} is unavailable; hover over Setup issue for details."
        )
    except (AttributeError, RuntimeError):
        # The exception is still retained on the window and already logged by
        # the caller; a partially destroyed status bar cannot be made visible.
        return






























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
            viewers[:] = [viewer for viewer in viewers if id(viewer) != viewer_id]
        # SPCWindow's interconnected widgets/signals form Python cycles. Qt has
        # deleted the native tree at this point, but waiting for an arbitrary
        # later cyclic-GC pass retains roughly one viewer's heap per close.
        # Run collection on the next event-loop turn, outside the destruction
        # callback itself, so repeated open/close sessions plateau promptly.
        QTimer.singleShot(0, _collect_closed_viewer_cycles)

    win.destroyed.connect(_release_reference)


def compose_interactive(
    config, prof_col, controller, *, stn_id=None, model=None, run=None, loc=None,
    activate=True,
):
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
    # Started once the window exists, and before the layout passes so the fetch
    # can land during them. The hazard follows whatever the picker has selected,
    # so opening a sounding from a tornado-probability map does not silently
    # switch the inset to the categorical outlook.
    locator_spec = _collection_locator_spec(prof_col)
    if locator_spec is None:
        locator_spec = _controller_locator_spec(controller)
    start_locator_overlay_fetch(
        win,
        prof_col,
        product=_controller_overlay_product(controller),
        controller=controller,
        spec=locator_spec,
    )

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
    try:
        from sharpmod.ui.features.gui_locator import install_locator_tools

        install_locator_tools(win, controller)
    except Exception as exc:
        _LOGGER.exception("locator_tools.install_failed")
        _record_install_failure(win, "Sounding locator", exc)
    _install_analysis_actions(win, controller)
    _install_units_menu(win, controller)
    _install_data_inspector(win, prof_col)
    try:
        from sharpmod.ui.features.gui_timeline import install_timeline_controls

        install_timeline_controls(win, prof_col)
    except Exception as exc:
        _LOGGER.exception("forecast_timeline.install_failed")
        _record_install_failure(win, "Forecast timeline", exc)
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
    install_linked_readout(win)
    # The mode buttons reuse the one existing toolbar and guard the real
    # vendored drag/numeric/history mutation funnels. Install only after those
    # actions exist so Inspect can disable every editing route coherently.
    install_interaction_mode(
        win, settings=getattr(controller, "_settings", None)
    )
    # Original/proposed edit feedback and the dashed original-profile overlay.
    # After the history actions exist, so the controller can follow undo/redo,
    # and after the mode controller, so its own toolbar/menu wiring is stable.
    try:
        install_edit_feedback(
            win, settings=getattr(controller, "_settings", None)
        )
    except Exception as exc:
        _LOGGER.exception("edit_feedback.install_failed")
        _record_install_failure(win, "Edit feedback", exc)
    # The readable view of the same bounded history the Undo/Redo actions use.
    try:
        install_edit_history(win)
    except Exception as exc:
        _LOGGER.exception("edit_history.install_failed")
        _record_install_failure(win, "Edit history", exc)
    # After the View menu exists, and before the sidebar adds its own entry, so
    # the panel list sits with the other view choices rather than below a toggle.
    _install_panel_menu(win)

    # The context sidebar, also before the fit so its width is part of the
    # chrome. It goes after _install_data_inspector because its Source &
    # Quality button triggers that action, and after _install_view_controls
    # because its show/hide toggle is added to the View menu.
    _install_sounding_sidebar(win)
    # Cross-sounding analysis stays in its own lazy dock.  Installing it after
    # the context sidebar lets Qt tabify the two right-hand tools; the analysis
    # dock starts hidden and performs no metric work until opened.
    try:
        from sharpmod.ui.analysis.workspace import install_analysis_workspace

        install_analysis_workspace(
            win, settings=getattr(controller, "_settings", None)
        )
    except Exception as exc:
        _LOGGER.exception("analysis_workspace.install_failed")
        _record_install_failure(win, "Analysis workspace", exc)

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

    win.setAttribute(Qt.WA_ShowWithoutActivating, not activate)
    win.showNormal()
    if activate:
        win.raise_()
        win.activateWindow()

    # The pre-show sizing uses an *estimated* menu-bar/chrome height, which can
    # leave the window slightly taller than the scaled sounding (a black band
    # below the index tables). Re-fit once now that the window is realized and
    # the true chrome heights are measurable, so the window wraps the sounding
    # exactly with no leftover gap.
    QTimer.singleShot(0, lambda: _finalize_scaled_fit(app, win))
    return win












#: What each swappable panel is for, keyed by the vendored inset name.
#:
#: Worth stating in the menu because the panel titles are abbreviations and two
#: of them ("Sig-Tor Stats", "EF-Scale Probs") describe the same hazard from
#: different angles. Every one of these is computed for every sounding already,
#: so the only question the menu has to answer is which one to look at.
_PANEL_TOOLTIPS = {
    "STP STATS": (
        "Significant-tornado parameter against its climatology, with the "
        "probability of a significant tornado given a supercell."
    ),
    "COND STP": (
        "Conditional EF-scale probabilities derived from the significant-tornado "
        "parameter."
    ),
    "VROT": (
        "Conditional EF-scale probabilities derived from radar rotational velocity."
    ),
    "SHIP": ("Significant-hail parameter against its climatology."),
    "FIRE": (
        "Fire weather: Fosberg index, Haines index, mixed-layer depth, transport "
        "wind, ventilation rate, and low-level moisture.\nAlso marks the mixing "
        "height on the Skew-T."
    ),
    "WINTER": (
        "Winter weather: dendritic growth zone, its moisture and omega, and the "
        "best-guess precipitation type.\nAlso marks the dendritic growth zone on "
        "the Skew-T."
    ),
    "SARS": (
        "Sounding analogues: the closest historical soundings and what they produced."
    ),
}


































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
    except Exception as exc:
        _LOGGER.exception("units_menu.install_failed")
        _record_install_failure(win, "Units menu", exc)


from sharpmod.ui.viewer_locator import (  # noqa: E402
    _fill_metadata,
    _locator_overlay_point,
    _controller_overlay_product,
    _controller_model_field,
    _controller_locator_spec,
    _collection_locator_spec,
    attach_locator_model_field,
    _repaint_locator_insets,
    _locator_selection,
    _track_locator_worker,
    _collection_meta,
    _risk_selection_for_product,
    _start_explicit_locator_fetch,
    start_locator_overlay_fetch
)


from sharpmod.ui.viewer_scaled import (  # noqa: E402
    _FixedSoundingScrollArea,
    _ScaledSoundingView,
    _fit_window_to_screen,
    _finalize_scaled_fit
)


from sharpmod.ui.viewer_controls import (  # noqa: E402
    _install_view_controls,
    show_sounding_panel,
    _install_panel_menu,
    _bind_view_controls,
    _install_tip_bar,
    _install_help_menu,
    _install_parcel_selector,
    _install_level_editor
)


from sharpmod.ui.viewer_export import (  # noqa: E402
    _default_export_basename,
    _focused_profile_collection,
    _install_export_menu
)


from sharpmod.ui.viewer_sidebar import (  # noqa: E402
    _SoundingSidebar,
    _dock_title_bar,
    _reserved_toolbar_height,
    _reserved_dock_width,
    _install_sounding_sidebar,
    _install_data_inspector
)
