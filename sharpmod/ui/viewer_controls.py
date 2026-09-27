"""Viewer Controls for the sounding viewer."""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtGui import QAction
from qtpy.QtGui import QActionGroup
from qtpy.QtWidgets import QDialog
from qtpy.QtWidgets import QHBoxLayout
from qtpy.QtWidgets import QLabel
from qtpy.QtWidgets import QMessageBox
from qtpy.QtWidgets import QSlider
from qtpy.QtWidgets import QToolBar
from qtpy.QtWidgets import QToolButton
from qtpy.QtWidgets import QWidget
from sharpmod.ui.features.gui_common import TIP_LINE
from sharpmod.ui.features.gui_common import _LOGGER
from sharpmod.ui.features.gui_common import _install_fullscreen_action
from sharpmod.ui.features.gui_common import _show_controls_dialog
from sharpmod.ui.features.gui_settings import _ParcelDialog
from sharpmod.ui.styles.theme import FIELD_W
from sharpmod.ui.styles.theme import OBJ_GHOST
from sharpmod.ui.styles.theme import OBJ_NUMERIC
from sharpmod.ui.styles.theme import ZOOM_SLIDER_W
import logging
import weakref
from sharpmod import gui_viewer as _api


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
            "the scaling."
        )
        actions["fit"] = act_fit

        act_actual = QAction("Actual Size", win)
        act_actual.setShortcut("Ctrl+1")
        act_actual.setCheckable(True)
        act_actual.setToolTip(
            "Show the sounding at 1:1 (100%).\nThe sharpest view -- the canvas "
            "is drawn at this size, so nothing is resampled. Middle-drag to "
            "reach the lower panels."
        )
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
        slider.setRange(
            int(_api._ScaledSoundingView.MIN_SCALE * 100),
            int(_api._ScaledSoundingView.MAX_SCALE * 100),
        )
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
    except Exception as exc:
        _LOGGER.exception("view_controls.install_failed")
        _api._record_install_failure(win, "View controls", exc)


def show_sounding_panel(win, key: str) -> bool:
    """Show the swappable panel named ``key``. Returns whether it changed.

    Drives the vendored swap rather than reaching into the layout, because that
    path does more than move a widget: it re-creates the outgoing panel, persists
    the choice, and turns the matching Skew-T annotation on or off -- the mixing
    height for the fire panel, the dendritic growth zone band for the winter one.
    Those pairings are the reason a separate "mode" concept is unnecessary, and
    bypassing ``swapInset`` would lose them.
    """
    sw = getattr(win, "spc_widget", None)
    if sw is None or not key:
        return False
    if getattr(sw, "right_inset", None) == key:
        return False  # already showing; nothing to do
    try:
        # Rebuilt exactly as a right-click would: the vendored swap reads its
        # target from ``menu_ag``'s checked action and ``inset_to_swap``.
        sw.makeInsetMenu(sw.left_inset, sw.right_inset)
        action = next(
            (item for item in sw.menu_ag.actions() if item.data() == key), None
        )
        if action is None:
            return False
        action.setChecked(True)
        sw.inset_to_swap = "RIGHT"
        sw.swapInset()
        return True
    except Exception:
        _LOGGER.exception("panel_menu.swap_failed key=%s", key)
        return False


def _install_panel_menu(win) -> None:
    """List the swappable sounding panels in the View menu.

    These panels were reachable only by right-clicking one particular box, and
    not by design: the composed layout detaches the left inset to make room for
    the index board, so the vendored hit test can only ever land on the right one.
    Nothing named the gesture, so the fire and winter panels -- both computed for
    every sounding whether or not anyone looks at them -- reader as absent.

    The right-click still works. This adds a named route to the same swap.
    """
    try:
        menu = getattr(win, "_sharpmod_view_menu", None)
        sw = getattr(win, "spc_widget", None)
        if menu is None or sw is None:
            return
        names = dict(getattr(type(sw), "inset_names", None) or {})
        if not names:
            return

        # The left inset is detached by the layout, so offering it would be a
        # menu entry that cannot do anything.
        detached = {getattr(sw, "left_inset", None)}
        offered = [
            (key, label)
            for key, label in sorted(names.items(), key=lambda kv: kv[1])
            if key not in detached
        ]
        if not offered:
            return

        submenu = menu.addMenu("Sounding &Panel")
        submenu.setToolTipsVisible(True)
        group = QActionGroup(submenu)
        group.setExclusive(True)
        actions = {}
        for key, label in offered:
            action = QAction(label, win)
            action.setCheckable(True)
            action.setData(key)
            if key in _api._PANEL_TOOLTIPS:
                action.setToolTip(_api._PANEL_TOOLTIPS[key])
            # See _install_export_menu: the window owns the action, so the
            # handler must not close over the window itself.
            win_ref = weakref.ref(win)

            def chosen(_checked=False, panel=key, ref=win_ref):
                live = ref()
                if live is not None:
                    _api.show_sounding_panel(live, panel)

            action.triggered.connect(chosen)
            group.addAction(action)
            submenu.addAction(action)
            actions[key] = action

        def sync():
            """Tick whichever panel is actually mounted.

            Recomputed on open rather than tracked, because the right-click menu
            and the vendored startup config can both change it without going
            through here.
            """
            current = getattr(sw, "right_inset", None)
            for key, action in actions.items():
                action.setChecked(key == current)

        submenu.aboutToShow.connect(sync)
        sync()
        win._sharpmod_panel_menu = submenu
        win._sharpmod_panel_actions = actions
    except Exception as exc:
        _LOGGER.exception("panel_menu.install_failed")
        _api._record_install_failure(win, "Panel menu", exc)


def _bind_view_controls(win) -> None:
    """Connect the View actions to the sounding host created by the fit.

    Zoom is available on :class:`_ScaledSoundingView`, which the production fit
    path uses even when the sounding initially fits at 1:1 so later resizes stay
    responsive. A fixed host supplied by another caller has no transform; there
    the controls are disabled and say why rather than being present but inert.
    """
    actions = getattr(win, "_sharpmod_view_actions", None)
    if not actions:
        return
    readout = getattr(win, "_sharpmod_zoom_readout", None)
    view = win.centralWidget()

    slider = getattr(win, "_sharpmod_zoom_slider", None)

    if not isinstance(view, _api._ScaledSoundingView):
        for action in actions.values():
            action.setEnabled(False)
            action.setToolTip("The sounding already fits this screen at actual size")
        if readout is not None:
            readout.setText("100%")
        if slider is not None:
            slider.setEnabled(False)
            slider.setToolTip("The sounding already fits this screen at actual size")
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
                f"Fit \u00b7 {scale * 100:.0f}%" if fitted else f"{scale * 100:.0f}%"
            )
        # Reflect the mode without re-entering the triggered handlers.
        for key, checked in (
            ("fit", fitted),
            ("actual", not fitted and abs(scale - 1.0) < 1e-6),
        ):
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
        actions["fit"].triggered.connect(lambda _checked=False: view.fit_to_window())
        actions["actual"].triggered.connect(lambda _checked=False: view.zoom_to(1.0))
        actions["in"].triggered.connect(lambda _checked=False: view.zoom_in())
        actions["out"].triggered.connect(lambda _checked=False: view.zoom_out())
        _show_scale(view.current_scale())
    except Exception as exc:
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

        # A full sentence of shortcuts in a menu-bar corner can cover the menus
        # themselves at enlarged text sizes. Keep the native, keyboard-reachable
        # guide action visible and put the complete hint in its tooltip/readout.
        guide_btn = QToolButton()
        guide_btn.setText("Controls (F1)")
        guide_btn.setAccessibleName("Sounding controls guide")
        guide_btn.setAccessibleDescription(TIP_LINE)
        guide_btn.setToolTip(f"Show all sounding-window controls (F1)\n{TIP_LINE}")
        guide_btn.setAutoRaise(True)
        guide_btn.setObjectName(OBJ_GHOST)

        def _open_guide() -> None:
            window = win_ref()
            if window is not None:
                _show_controls_dialog(window)

        guide_btn.clicked.connect(_open_guide)

        close_btn = QToolButton()
        close_btn.setText("\u2715")
        close_btn.setAccessibleName("Hide interaction tips")
        close_btn.setToolTip("Hide these tips")
        close_btn.setAutoRaise(True)
        close_btn.setObjectName(OBJ_GHOST)

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
    except Exception as exc:
        # A tip hiccup must never block the interactive window -- but it must
        # not vanish either. _install_help_menu keys the "Show Interaction Tips"
        # item off ``_sharpmod_tips``, which is set on the last lines above, so
        # a failure anywhere before them silently drops that menu item while
        # leaving the window looking intact.
        _LOGGER.exception("tip_bar.install_failed")
        _api._record_install_failure(win, "Interaction tips", exc)


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
                "The one-line reminder strip along the top of this window"
            )

            def _toggle_tips(checked: bool) -> None:
                tips.setVisible(checked)
                if tip_settings is not None:
                    tip_settings.setValue("hide_tips", not checked)

            show_tips.toggled.connect(_toggle_tips)
            menu.addAction(show_tips)
            win._sharpmod_tips_action = show_tips

        win._sharpmod_help_menu = menu
        from sharpmod.ui.features.gui_commands import install_command_palette

        install_command_palette(win, menu=menu)
    except Exception as exc:
        _LOGGER.exception("help_menu.install_failed")
        _api._record_install_failure(win, "Help menu", exc)


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
            cur = list(getattr(conv, "pcl_types", None) or ["SFC", "ML", "FCST", "MU"])
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
    except Exception as exc:
        # Parcel-selector wiring must never block the interactive window, but a
        # silent failure here removes the parcel selector with no trace of why.
        _LOGGER.exception("parcel_selector.install_failed")
        _api._record_install_failure(win, "Parcel selector", exc)


def _install_level_editor(win) -> None:
    """Add a validated numeric level editor to the Skew-T context menu."""
    sw = getattr(win, "spc_widget", None)
    skewt = getattr(sw, "sound", None)
    popup = getattr(skewt, "popupmenu", None)
    if (
        skewt is None
        or popup is None
        or getattr(skewt, "_sharpmod_level_editor_installed", False)
    ):
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
        idx = _api._nearest_profile_level(prof, pressure)
        if idx is None:
            QMessageBox.warning(
                win,
                "Edit Sounding Level",
                "No valid pressure levels are available in this sounding.",
            )
            return

        dialog = _api._SoundingLevelEditorDialog(prof, idx, parent=win)
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
                idx,
                pressure,
                ",".join(changes),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            QMessageBox.warning(
                win, "Edit Sounding Level", f"The sounding could not be updated:\n{exc}"
            )

    edit_action.triggered.connect(_edit_nearest_level)
    if reset_action is not None:
        popup.insertAction(reset_action, edit_action)
        try:
            reset_action.triggered.disconnect()
        except (RuntimeError, TypeError):
            pass
        reset_action.triggered.connect(
            lambda: skewt.reset.emit(["pres", "hght", "tmpc", "dwpc", "wdir", "wspd"])
        )
    else:
        popup.addAction(edit_action)

    skewt._sharpmod_level_editor_action = edit_action
    skewt._sharpmod_level_editor_installed = True
