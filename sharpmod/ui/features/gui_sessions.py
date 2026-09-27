"""Qt integration for viewer sessions, recovery, and analysis actions.

It captures/restores dock, map, and locator state and connects portable session/history
operations to window menus; serialization and durable state formats live in
``sharpmod.state``."""

from __future__ import annotations

import logging
import math
import os
import re
import sys
import tempfile
import time
import weakref
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

"""Analysis-session state and menu integration for sounding viewers."""

from sharpmod.ui.features.gui_common import APP_NAME, _LOGGER
from sharpmod.export_paths import ExportDirectoryError, export_file_path

from qtpy.QtCore import (
    Qt,
    QThread,
    QTimer,
    Signal,
    QDate,
    QSettings,
    QPointF,
    QRectF,
    QSize,
    QUrl,
)
from qtpy.QtGui import (
    QAction,
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
    QInputDialog,
    QFormLayout,
    QCheckBox,
    QSizePolicy,
    QGraphicsView,
    QGraphicsScene,
    QProgressBar,
    QMenu,
    QDockWidget,
)

SESSION_SUFFIX = ".sharpmod-session"
RECOVERY_DEBOUNCE_MS = 2000

_MAP_WIDGET_ATTRIBUTES = ("_map", "_model_map", "_era5_map", "_wrf_map")
_OVERLAY_DESCRIPTOR_META_KEY = "sharpmod_locator_overlay_descriptors"


def _viewer_controller(win):
    """Return the viewer's controller when it is still alive."""
    controller = getattr(win, "_sharpmod_controller", None)
    if controller is not None:
        return controller
    try:
        return win.parent()
    except (AttributeError, RuntimeError):
        return None


def _dock_session_state(win) -> dict:
    """Capture stable dock state as portable values, never native blobs."""
    result = {}
    try:
        docks = win.findChildren(QDockWidget)
    except (AttributeError, RuntimeError):
        docks = []
    for dock in docks:
        try:
            key = str(dock.objectName() or dock.windowTitle()).strip()
            if not key:
                continue
            state = {
                # ``isHidden`` reflects an explicitly closed dock even while
                # its top-level viewer is itself temporarily hidden.
                "visible": not dock.isHidden(),
                "floating": bool(dock.isFloating()),
            }
            if state["floating"]:
                from sharpmod.ui.features.gui_workspace_layouts import (
                    capture_floating_placement,
                )

                state.update(capture_floating_placement(dock))
            content = dock.widget()
            current_index = getattr(content, "currentIndex", None)
            count = getattr(content, "count", None)
            if callable(current_index) and callable(count) and count() > 0:
                state["current_index"] = int(current_index())
            result[key] = state
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    return result


def _restore_dock_session_state(win, states) -> None:
    if not isinstance(states, dict):
        return
    try:
        docks = win.findChildren(QDockWidget)
    except (AttributeError, RuntimeError):
        return
    by_key = {}
    for dock in docks:
        try:
            key = str(dock.objectName() or dock.windowTitle()).strip()
        except (AttributeError, RuntimeError):
            continue
        if key:
            by_key[key] = dock
    for key, state in states.items():
        dock = by_key.get(str(key))
        if dock is None or not isinstance(state, dict):
            continue
        try:
            if (
                "floating" in state
                and dock.features() & QDockWidget.DockWidgetFloatable
            ):
                dock.setFloating(bool(state["floating"]))
                if bool(state["floating"]):
                    from sharpmod.ui.features.gui_workspace_layouts import (
                        restore_floating_placement,
                    )

                    restore_floating_placement(dock, state)
            if "visible" in state:
                dock.setVisible(bool(state["visible"]))
            content = dock.widget()
            setter = getattr(content, "setCurrentIndex", None)
            count = getattr(content, "count", None)
            if (
                "current_index" in state
                and callable(setter)
                and callable(count)
                and count() > 0
            ):
                index = max(0, min(int(state["current_index"]), count() - 1))
                setter(index)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue


def _finite_bounds(value) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        bounds = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    return bounds if all(math.isfinite(item) for item in bounds) else None


def _capture_owner_map_state(owner) -> dict:
    if owner is None:
        return {}
    result = {}
    hook = getattr(owner, "session_map_state", None)
    if callable(hook):
        try:
            hook_state = hook()
        except Exception:  # noqa: BLE001 - optional state cannot block saving
            hook_state = None
        if isinstance(hook_state, dict):
            result["hook"] = hook_state
    extents = {}
    for attribute in _MAP_WIDGET_ATTRIBUTES:
        widget = getattr(owner, attribute, None)
        reader = getattr(widget, "view_bounds", None)
        if not callable(reader):
            continue
        try:
            bounds = _finite_bounds(reader())
        except Exception:  # noqa: BLE001 - one optional map is non-critical
            bounds = None
        if bounds is not None:
            extents[attribute] = bounds
    if extents:
        result["extents"] = extents
    return result


def _map_session_state(win) -> dict:
    result = {}
    window_state = _capture_owner_map_state(win)
    if window_state:
        result["window"] = window_state
    controller_state = _capture_owner_map_state(_viewer_controller(win))
    if controller_state:
        result["controller"] = controller_state
    return result


def _restore_owner_map_state(owner, state) -> None:
    if owner is None or not isinstance(state, dict):
        return
    hook = getattr(owner, "restore_session_map_state", None)
    if callable(hook) and isinstance(state.get("hook"), dict):
        try:
            hook(state["hook"])
        except Exception:  # noqa: BLE001 - tolerate older controller hooks
            pass
    extents = state.get("extents")
    if not isinstance(extents, dict):
        return
    for attribute, raw_bounds in extents.items():
        if attribute not in _MAP_WIDGET_ATTRIBUTES:
            continue
        bounds = _finite_bounds(raw_bounds)
        widget = getattr(owner, attribute, None)
        if bounds is None or widget is None:
            continue
        restored = False
        for method_name in ("restore_view_bounds", "set_view_bounds"):
            method = getattr(widget, method_name, None)
            if callable(method):
                try:
                    method(tuple(bounds))
                    restored = True
                except Exception:  # noqa: BLE001 - try the public fallback
                    pass
                if restored:
                    break
        if restored:
            continue
        setter = getattr(widget, "set_extent", None)
        if callable(setter):
            try:
                setter(*bounds, pad=0.0)
            except (TypeError, ValueError, RuntimeError):
                try:
                    setter(*bounds)
                except Exception:  # noqa: BLE001 - optional map state
                    pass


def _restore_locator_session_state(sw, state) -> None:
    if not isinstance(state, list):
        return
    try:
        collections = list(getattr(sw, "prof_collections", None) or [])
    except (TypeError, RuntimeError):
        return
    for group in state:
        if not isinstance(group, dict) or not isinstance(group.get("items"), list):
            continue
        try:
            index = int(group.get("collection_index", -1))
            collection = collections[index] if 0 <= index < len(collections) else None
        except (TypeError, ValueError, IndexError):
            collection = None
        if collection is None:
            continue
        descriptors = [dict(item) for item in group["items"] if isinstance(item, dict)]
        try:
            existing = collection.getMeta(_OVERLAY_DESCRIPTOR_META_KEY)
        except (AttributeError, RuntimeError, TypeError):
            existing = None
        if descriptors and not existing:
            try:
                collection.setMeta(_OVERLAY_DESCRIPTOR_META_KEY, descriptors)
            except (AttributeError, RuntimeError, TypeError):
                try:
                    collection._meta[_OVERLAY_DESCRIPTOR_META_KEY] = descriptors
                except (AttributeError, TypeError):
                    pass


def _viewer_session_ui_state(win) -> dict:
    """Capture viewer selections that are not owned by ``ProfCollection``."""
    sw = getattr(win, "spc_widget", None)
    convective = getattr(sw, "convective", None)
    state = {
        "workspace_version": 1,
        "window_title": win.windowTitle(),
        "deviant": str(getattr(sw, "deviant", "right")),
        "parcel_types": list(getattr(convective, "pcl_types", None) or []),
        "parcel_index": int(getattr(convective, "skewt_pcl", 0) or 0),
    }
    panel = getattr(sw, "right_inset", None)
    if panel:
        state["visible_panel"] = str(panel)

    view = None
    try:
        view = win.centralWidget()
    except (AttributeError, RuntimeError):
        pass
    scale = getattr(view, "current_scale", None)
    fit_mode = getattr(view, "is_fit_mode", None)
    if callable(scale) and callable(fit_mode):
        try:
            value = float(scale())
            if math.isfinite(value) and value > 0.0:
                state["zoom"] = {
                    "fit_mode": bool(fit_mode()),
                    "scale": value,
                }
        except (RuntimeError, TypeError, ValueError):
            pass

    docks = _dock_session_state(win)
    if docks:
        state["docks"] = docks
    map_state = _map_session_state(win)
    if map_state:
        state["map_state"] = map_state

    workspace = getattr(win, "_sharpmod_analysis_workspace", None)
    capture = getattr(workspace, "session_state", None)
    if callable(capture):
        try:
            analysis_state = capture()
        except Exception:  # noqa: BLE001 - optional panel cannot block saving
            analysis_state = None
        if isinstance(analysis_state, dict):
            state["analysis_workspace"] = analysis_state
    for attribute, key in (("_sharpmod_collection_state", "collections"), ("_sharpmod_sidebar", "soundings_list")):
        component = getattr(win, attribute, None)
        if component is not None:
            state[key] = component.session_state()
    return state


def _apply_viewer_session_state(
    win, active_collection: int, ui_state: dict | None
) -> None:
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
        str(item).upper()
        for item in ui_state.get("parcel_types", [])
        if str(item).upper() in {"SFC", "ML", "FCST", "MU", "EFF", "USER"}
    ]
    if convective is not None and parcel_types:
        convective.pcl_types = parcel_types
        if board is not None:
            board.pcl_types = list(parcel_types)
        index = max(0, min(int(ui_state.get("parcel_index", 0)), len(parcel_types) - 1))
        convective.skewt_pcl = index
        parcel = (getattr(convective, "parcels", None) or {}).get(parcel_types[index])
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

    panel = ui_state.get("visible_panel")
    if isinstance(panel, str) and panel:
        try:
            from sharpmod.gui_viewer import show_sounding_panel

            show_sounding_panel(win, panel)
        except Exception:  # noqa: BLE001 - tolerate older viewer builds
            pass

    zoom = ui_state.get("zoom")
    if isinstance(zoom, dict):
        try:
            view = win.centralWidget()
            if bool(zoom.get("fit_mode", True)):
                fit = getattr(view, "fit_to_window", None)
                if callable(fit):
                    fit()
            else:
                scale = float(zoom.get("scale", 1.0))
                setter = getattr(view, "zoom_to", None)
                if math.isfinite(scale) and scale > 0.0 and callable(setter):
                    setter(scale)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

    for attribute, key in (("_sharpmod_collection_state", "collections"), ("_sharpmod_sidebar", "soundings_list")):
        component = getattr(win, attribute, None)
        if component is not None and isinstance(ui_state.get(key), dict):
            component.restore_session_state(ui_state[key])
    workspace = getattr(win, "_sharpmod_analysis_workspace", None)
    restore_workspace = getattr(workspace, "restore_session_state", None)
    analysis_state = ui_state.get("analysis_workspace")
    if callable(restore_workspace) and isinstance(analysis_state, dict):
        try:
            restore_workspace(analysis_state)
        except Exception:  # noqa: BLE001 - tolerate older workspace builds
            pass

    _restore_dock_session_state(win, ui_state.get("docks"))
    map_state = ui_state.get("map_state")
    if isinstance(map_state, dict):
        _restore_owner_map_state(win, map_state.get("window"))
        _restore_owner_map_state(_viewer_controller(win), map_state.get("controller"))
    _restore_locator_session_state(sw, ui_state.get("locator_overlays"))


def _session_default_path(*, settings=None) -> Path:
    return export_file_path(
        f"analysis_{datetime.now():%Y%m%d_%H%M}{SESSION_SUFFIX}",
        settings=settings,
    )


def _cleanup_window_recovery_snapshots(win) -> None:
    """Remove snapshots created by one now-saved live window."""
    paths = tuple(getattr(win, "_sharpmod_recovery_paths", ()) or ())
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            _LOGGER.warning("analysis_recovery.cleanup_failed path=%s", path)
    win._sharpmod_recovery_paths = []


def _recovery_choice_label(snapshot) -> str:
    context = snapshot.identities[0] if snapshot.identities else "Context unavailable"
    if len(context) > 56:
        context = context[:53].rstrip() + "..."
    token = snapshot.path.stem.rsplit("_", 1)[-1]
    return f"{snapshot.label} · {context} [{token}]"


def _recovery_restore_question(snapshot, *, current_dirty: bool) -> str:
    current_state = (
        "The current workspace has unsaved changes. A fresh recovery copy "
        "will be written before it closes."
        if current_dirty
        else "The current workspace matches its last explicit save."
    )
    return (
        f"{snapshot.summary()}\n\n{current_state}\n\n"
        "Restore this snapshot and replace the current workspace? "
        "The current window closes only after the recovered workspace "
        "opens successfully."
    )


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
    from sharpmod.state.sessions import (
        AnalysisHistory,
        build_session,
        list_recovery_snapshots,
        write_recovery_snapshot,
        write_session,
    )

    sw = getattr(win, "spc_widget", None)
    if sw is None:
        return
    history = AnalysisHistory(sw)
    sw._sharpmod_history = history
    win._sharpmod_history = history

    session_label = QLabel(win)
    session_label.setAccessibleName("Analysis session save state")
    win._sharpmod_session_label = session_label
    win._sharpmod_session_revision = int(
        getattr(win, "_sharpmod_session_revision", 0) or 0
    )
    win._sharpmod_session_saved_revision = int(
        getattr(win, "_sharpmod_session_saved_revision", 0) or 0
    )
    win._sharpmod_recovery_paths = list(
        getattr(win, "_sharpmod_recovery_paths", ()) or ()
    )

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

    # Resolved into a local of the same name inside each handler, so none of
    # them closes over the outer ``win``. These actions are children of the
    # window and Qt holds their connections C++-side, so a strong capture makes
    # win -> action -> connection -> closure -> win a cycle Python's cyclic
    # collector cannot break. The window's wrapper then survives to interpreter
    # exit, after Qt has torn the C++ side down, and freeing it is an access
    # violation -- measured at 6 crashes in 14 runs on an equivalent handler in
    # gui_viewer._install_tip_bar, 0 in 14 once held weakly.
    win_ref = weakref.ref(win)

    def _undo():
        win = win_ref()
        label = history.undo()
        if label and win is not None:
            win.statusBar().showMessage(f"Undid {label}", 3000)

    def _redo():
        win = win_ref()
        label = history.redo()
        if label and win is not None:
            win.statusBar().showMessage(f"Redid {label}", 3000)

    undo_action.triggered.connect(_undo)
    redo_action.triggered.connect(_redo)
    history.add_listener(_refresh_history_actions)

    def _session_dirty(target) -> bool:
        path = str(getattr(target, "_sharpmod_session_path", "") or "")
        if not path:
            return True
        saved_depth = int(
            getattr(target, "_sharpmod_session_saved_depth", 0) or 0
        )
        saved_revision = int(
            getattr(target, "_sharpmod_session_saved_revision", 0) or 0
        )
        revision = int(getattr(target, "_sharpmod_session_revision", 0) or 0)
        return history.undo_depth != saved_depth or revision != saved_revision

    def _refresh_session_label():
        target = win_ref()
        if target is None:
            return
        path = str(getattr(target, "_sharpmod_session_path", "") or "")
        try:
            dirty = _session_dirty(target)
        except (AttributeError, RuntimeError):
            return
        try:
            target.statusBar().addPermanentWidget(session_label)
        except (AttributeError, RuntimeError):
            pass
        if not path:
            session_label.setText("Session: Unsaved")
        elif dirty:
            session_label.setText(f"Session: {Path(path).name} · Unsaved changes")
        else:
            stamp = getattr(target, "_sharpmod_session_saved_at", "") or ""
            tail = f" · Saved {stamp}" if stamp else ""
            session_label.setText(f"Session: {Path(path).name}{tail}")

    history.add_listener(_refresh_session_label)
    _refresh_session_label()

    recovery_timer = QTimer(win)
    recovery_timer.setSingleShot(True)
    recovery_timer.setInterval(RECOVERY_DEBOUNCE_MS)
    win._sharpmod_recovery_timer = recovery_timer

    def _write_recovery(*, force=False, quiet=True):
        target = win_ref()
        if target is None:
            return None
        try:
            dirty = _session_dirty(target)
        except (AttributeError, RuntimeError):
            return None
        if not force and not dirty:
            return None
        try:
            document = build_session(
                sw.prof_collections,
                active_collection=int(getattr(sw, "pc_idx", 0)),
                ui_state=_viewer_session_ui_state(target),
            )
            path = write_recovery_snapshot(
                document, settings=getattr(controller, "_settings", None)
            )
        except Exception as exc:  # noqa: BLE001 - recovery must not crash UI
            _LOGGER.exception("analysis_recovery.write_failed")
            session_label.setToolTip(f"Recovery snapshot failed: {exc}")
            try:
                target.statusBar().showMessage(
                    f"Recovery snapshot failed: {exc}", 8000
                )
            except (AttributeError, RuntimeError):
                pass
            if not quiet:
                QMessageBox.critical(
                    target, APP_NAME,
                    f"The current workspace could not be protected before "
                    f"restore:\n{exc}",
                )
            return False
        target._sharpmod_recovery_paths.append(path)
        target._sharpmod_last_recovery_path = str(path)
        target._sharpmod_last_recovery_at = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%MZ"
        )
        session_label.setToolTip(
            f"Latest recovery snapshot: {target._sharpmod_last_recovery_at}\n{path}"
        )
        _LOGGER.info(
            "analysis_recovery.saved path=%s soundings=%d",
            path, len(sw.prof_collections),
        )
        return path

    def _schedule_recovery():
        target = win_ref()
        if target is None:
            return
        try:
            if _session_dirty(target):
                target._sharpmod_recovery_timer.start()
            else:
                target._sharpmod_recovery_timer.stop()
        except (AttributeError, RuntimeError):
            return

    recovery_timer.timeout.connect(_write_recovery)
    win._sharpmod_write_recovery = _write_recovery
    win._sharpmod_schedule_recovery = _schedule_recovery

    history_state = [(history.undo_depth, history.redo_depth)]

    def _history_recovery_changed():
        target = win_ref()
        current = (history.undo_depth, history.redo_depth)
        if target is not None and current != history_state[0]:
            target._sharpmod_session_revision += 1
        history_state[0] = current
        _schedule_recovery()

    history.add_listener(_history_recovery_changed)

    original_update = getattr(sw, "updateProfs", None)
    if callable(original_update) and not getattr(
        sw, "_sharpmod_recovery_update_wrapped", False
    ):
        def updateProfs(*args, **kwargs):  # noqa: N802 - upstream Qt API
            result = original_update(*args, **kwargs)
            target = win_ref()
            if target is not None:
                target._sharpmod_session_revision += 1
                _refresh_session_label()
                _schedule_recovery()
            return result

        sw.updateProfs = updateProfs
        sw._sharpmod_recovery_update_wrapped = True

    filemenu = _find_window_menu(win, "File")
    if filemenu is not None:
        filemenu.addSeparator()
        open_session = QAction("Open Analysis Session…", win)
        open_session.setShortcut("Ctrl+Shift+O")
        open_session.triggered.connect(controller._open_analysis_session)
        filemenu.addAction(open_session)
        recent_action = getattr(controller, "_recent_destinations_action", None)
        if recent_action is not None:
            filemenu.addAction(recent_action)
        recover_session = QAction("Recover Analysis Snapshot…", win)

        def _recover_session():
            win = win_ref()
            if win is None:
                return
            try:
                snapshots = list_recovery_snapshots(
                    settings=getattr(controller, "_settings", None)
                )
            except Exception as exc:  # noqa: BLE001 - user-facing inventory
                _LOGGER.exception("analysis_recovery.list_failed")
                QMessageBox.critical(
                    win, APP_NAME,
                    f"Recovery snapshots could not be listed:\n{exc}",
                )
                return
            if not snapshots:
                QMessageBox.information(
                    win, APP_NAME,
                    "No readable recovery snapshots are available.",
                )
                return
            choices = [_recovery_choice_label(item) for item in snapshots]
            selected, accepted = QInputDialog.getItem(
                win,
                "Recover Analysis Snapshot",
                "Choose a recovery snapshot:",
                choices,
                0,
                False,
            )
            if not accepted:
                return
            try:
                snapshot = snapshots[choices.index(selected)]
            except (ValueError, IndexError):
                return
            question = _recovery_restore_question(
                snapshot, current_dirty=_session_dirty(win)
            )
            answer = QMessageBox.question(
                win,
                "Restore recovery snapshot?",
                question,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            if _session_dirty(win) and _write_recovery(quiet=False) is False:
                return
            opener = getattr(controller, "_open_analysis_session", None)
            if not callable(opener):
                QMessageBox.critical(
                    win, APP_NAME,
                    "This workspace cannot open analysis sessions.",
                )
                return
            try:
                restored = opener(str(snapshot.path), recovered=True)
            except Exception as exc:  # noqa: BLE001 - protect current window
                _LOGGER.exception(
                    "analysis_recovery.restore_failed path=%s", snapshot.path
                )
                QMessageBox.critical(
                    win, APP_NAME,
                    f"The recovery snapshot could not be restored:\n{exc}",
                )
                return
            if restored:
                win.close()

        recover_session.triggered.connect(_recover_session)
        filemenu.addAction(recover_session)
        save_session = QAction("Save Analysis Session…", win)
        save_session.setShortcut("Ctrl+Shift+E")

        def _save_session():
            win = win_ref()
            if win is None:
                return
            previous = str(getattr(win, "_sharpmod_session_path", "") or "")
            try:
                suggested = export_file_path(
                    Path(previous).name
                    if previous
                    else f"analysis_{datetime.now():%Y%m%d_%H%M}{SESSION_SUFFIX}",
                    settings=getattr(controller, "_settings", None),
                )
            except ExportDirectoryError as exc:
                QMessageBox.critical(win, APP_NAME, str(exc))
                return
            path, _ = QFileDialog.getSaveFileName(
                win,
                "Save Analysis Session",
                str(suggested),
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
                    win, APP_NAME, f"The analysis session could not be saved:\n{exc}"
                )
                return
            win._sharpmod_session_path = path
            win._sharpmod_session_saved_depth = history.undo_depth
            win._sharpmod_session_saved_revision = win._sharpmod_session_revision
            win._sharpmod_session_saved_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
            recovery_timer.stop()
            _cleanup_window_recovery_snapshots(win)
            _refresh_session_label()
            record = getattr(controller, "_record_recent_destination", None)
            if callable(record):
                from sharpmod.state.recent_destinations import describe_collection

                noun = "sounding" if len(sw.prof_collections) == 1 else "soundings"
                details = f"{len(sw.prof_collections)} {noun} · " + " / ".join(
                    describe_collection(collection) for collection in sw.prof_collections[:3]
                )
                record("session", path, details=details)
            win.statusBar().showMessage(f"Saved analysis session: {path}", 5000)
            _LOGGER.info(
                "analysis_session.saved path=%s soundings=%d",
                path,
                len(sw.prof_collections),
            )

        save_session.triggered.connect(_save_session)
        filemenu.addAction(save_session)
        win._sharpmod_open_session_action = open_session
        win._sharpmod_recover_session_action = recover_session
        win._sharpmod_save_session_action = save_session

    win._sharpmod_undo_action = undo_action
    win._sharpmod_redo_action = redo_action
    win._sharpmod_analysis_actions_installed = True
