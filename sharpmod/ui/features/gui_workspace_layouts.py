"""Named viewer layouts with portable, monitor-safe window placement."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
import weakref

from qtpy.QtCore import QRect, Qt
from qtpy.QtGui import QAction
from qtpy.QtWidgets import (
    QApplication,
    QDockWidget,
    QInputDialog,
    QMenu,
    QMessageBox,
)


LAYOUT_FORMAT = "sharpmod-workspace-layouts"
LAYOUT_VERSION = 1
LAYOUT_SETTINGS_KEY = "workspace/layouts"
MAX_LAYOUTS = 12
MAX_LAYOUT_NAME = 64


class LayoutFormatError(ValueError):
    """A saved layout document cannot be safely rewritten by this version."""


def _clean_name(value) -> str:
    name = str(value or "").strip()
    if (
        not name
        or len(name) > MAX_LAYOUT_NAME
        or any(ord(character) < 32 for character in name)
    ):
        raise ValueError(
            f"Layout names must be 1-{MAX_LAYOUT_NAME} printable characters."
        )
    return name


def _bounded_int(value, default, minimum=1, maximum=10000):
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)
    return max(int(minimum), min(int(maximum), number))


def _rect_values(value):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    if len(value) != 4:
        return None
    numbers = []
    for item in value:
        try:
            number = float(item)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(number):
            return None
        numbers.append(int(round(number)))
    if numbers[2] <= 0 or numbers[3] <= 0:
        return None
    return numbers


def _screen_spec(value):
    if not isinstance(value, Mapping):
        return None
    available = _rect_values(value.get("available"))
    if available is None:
        return None
    try:
        ratio = float(value.get("device_pixel_ratio", 1.0))
    except (TypeError, ValueError, OverflowError):
        ratio = 1.0
    if not math.isfinite(ratio) or ratio <= 0.0:
        ratio = 1.0
    return {
        "name": str(value.get("name") or ""),
        "available": available,
        "device_pixel_ratio": ratio,
    }


def _normal_layout_state(value):
    if not isinstance(value, Mapping):
        raise ValueError("A workspace layout must be an object.")
    mode = str(value.get("mode") or "docked").casefold()
    if mode not in {"docked", "expanded", "separate"}:
        mode = "docked"
    area = str(value.get("analysis_area") or "right").casefold()
    if area not in {"left", "right"}:
        area = "right"
    state = {
        "mode": mode,
        "analysis_visible": bool(value.get("analysis_visible", True)),
        "analysis_area": area,
        "analysis_width": _bounded_int(
            value.get("analysis_width"), 640, 200, 10000
        ),
        "sidebar_visible": bool(value.get("sidebar_visible", True)),
        "sidebar_width": _bounded_int(
            value.get("sidebar_width"), 280, 160, 5000
        ),
        "tabified": bool(value.get("tabified", False)),
    }
    geometry = _rect_values(value.get("floating_geometry"))
    screen = _screen_spec(value.get("floating_screen"))
    if geometry is not None:
        state["floating_geometry"] = geometry
    if screen is not None:
        state["floating_screen"] = screen
    return state


class WorkspaceLayoutStore:
    """Versioned JSON storage over the application's existing QSettings."""

    def __init__(self, settings=None):
        self.settings = settings
        self._memory_raw = ""

    def _raw(self):
        if self.settings is None:
            return self._memory_raw
        return self.settings.value(LAYOUT_SETTINGS_KEY, "", str)

    def _read(self):
        raw = self._raw()
        if not raw:
            return [], True
        try:
            document = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return [], True
        if not isinstance(document, Mapping):
            return [], True
        if document.get("format") != LAYOUT_FORMAT:
            return [], False
        try:
            version = int(document.get("version", 0))
        except (TypeError, ValueError, OverflowError):
            return [], False
        if version != LAYOUT_VERSION:
            return [], False

        layouts = []
        seen = set()
        for item in document.get("layouts", ()):
            if not isinstance(item, Mapping):
                continue
            try:
                name = _clean_name(item.get("name"))
                state = _normal_layout_state(item.get("state"))
            except ValueError:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            layouts.append({"name": name, "state": state})
            if len(layouts) >= MAX_LAYOUTS:
                break
        return layouts, True

    def _write(self, layouts):
        document = {
            "format": LAYOUT_FORMAT,
            "version": LAYOUT_VERSION,
            "layouts": layouts,
        }
        raw = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        if self.settings is None:
            self._memory_raw = raw
        else:
            self.settings.setValue(LAYOUT_SETTINGS_KEY, raw)
            self.settings.sync()

    def names(self):
        layouts, _supported = self._read()
        return tuple(item["name"] for item in layouts)

    def get(self, name):
        try:
            key = _clean_name(name).casefold()
        except ValueError:
            return None
        layouts, _supported = self._read()
        for item in layouts:
            if item["name"].casefold() == key:
                # Normalize again to hand callers a fresh object rather than a
                # reference that can mutate this store's in-memory document.
                return _normal_layout_state(item["state"])
        return None

    def save(self, name, state):
        clean = _clean_name(name)
        normal = _normal_layout_state(state)
        layouts, supported = self._read()
        if not supported:
            raise LayoutFormatError(
                "Saved layouts use a newer version and were left unchanged."
            )
        key = clean.casefold()
        for item in layouts:
            if item["name"].casefold() == key:
                item["state"] = normal
                self._write(layouts)
                return item["name"]
        if len(layouts) >= MAX_LAYOUTS:
            raise ValueError(f"At most {MAX_LAYOUTS} named layouts can be saved.")
        layouts.append({"name": clean, "state": normal})
        self._write(layouts)
        return clean

    def delete(self, name):
        try:
            key = _clean_name(name).casefold()
        except ValueError:
            return False
        layouts, supported = self._read()
        if not supported:
            raise LayoutFormatError(
                "Saved layouts use a newer version and were left unchanged."
            )
        kept = [item for item in layouts if item["name"].casefold() != key]
        if len(kept) == len(layouts):
            return False
        self._write(kept)
        return True


def _intersection_area(first, second):
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    bottom = min(first[1] + first[3], second[1] + second[3])
    return max(0, right - left) * max(0, bottom - top)


def safe_floating_geometry(
    geometry,
    *,
    saved_screen=None,
    current_screens=(),
    minimum_size=(480, 320),
):
    """Map saved logical geometry completely inside a current work area.

    Qt reports screen and widget coordinates in device-independent pixels. A
    saved screen's available rectangle therefore captures both its arrangement
    and effective scale. Mapping the window by ratios into a same-named or
    fallback screen handles a removed monitor and a DPI change in one step.
    """
    screens = [spec for value in current_screens if (spec := _screen_spec(value))]
    rect = _rect_values(geometry)
    source = _screen_spec(saved_screen)
    if not screens:
        return rect or [100, 100, 900, 650]

    target = None
    if source is not None and source["name"]:
        target = next(
            (screen for screen in screens if screen["name"] == source["name"]),
            None,
        )
    if target is None and rect is not None:
        target = max(
            screens,
            key=lambda screen: _intersection_area(rect, screen["available"]),
        )
        if _intersection_area(rect, target["available"]) == 0:
            target = None
    if target is None:
        # current_screen_specs() puts the primary first.
        target = screens[0]

    tx, ty, tw, th = target["available"]
    if rect is None:
        width = min(900, tw)
        height = min(650, th)
        x = tx + max(0, (tw - width) // 2)
        y = ty + max(0, (th - height) // 2)
    else:
        x, y, width, height = rect
        if source is not None:
            sx, sy, sw, sh = source["available"]
            if sw > 0 and sh > 0:
                x = tx + round((x - sx) / sw * tw)
                y = ty + round((y - sy) / sh * th)
                width = round(width / sw * tw)
                height = round(height / sh * th)

    min_width = min(max(1, int(minimum_size[0])), tw)
    min_height = min(max(1, int(minimum_size[1])), th)
    width = max(min_width, min(tw, int(width)))
    height = max(min_height, min(th, int(height)))
    x = max(tx, min(tx + tw - width, int(x)))
    y = max(ty, min(ty + th - height, int(y)))
    return [x, y, width, height]


def _qt_screen_spec(screen):
    if screen is None:
        return None
    try:
        available = screen.availableGeometry()
        return {
            "name": str(screen.name() or ""),
            "available": [
                int(available.x()),
                int(available.y()),
                int(available.width()),
                int(available.height()),
            ],
            "device_pixel_ratio": float(screen.devicePixelRatio()),
        }
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def current_screen_specs(app=None):
    """Return current logical work areas with the primary screen first."""
    app = app or QApplication.instance()
    if app is None:
        return ()
    try:
        primary = app.primaryScreen()
        ordered = [primary]
        ordered.extend(screen for screen in app.screens() if screen is not primary)
    except (AttributeError, RuntimeError):
        return ()
    return tuple(spec for screen in ordered if (spec := _qt_screen_spec(screen)))


def capture_floating_placement(widget):
    """Capture portable logical geometry and its source work area."""
    try:
        geometry = widget.geometry()
        screen = widget.screen()
        return {
            "floating_geometry": [
                int(geometry.x()),
                int(geometry.y()),
                int(geometry.width()),
                int(geometry.height()),
            ],
            "floating_screen": _qt_screen_spec(screen),
        }
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return {}


def restore_floating_placement(widget, state, *, screens=None):
    """Restore a floating widget wholly inside the current monitor work area."""
    if not isinstance(state, Mapping):
        return None
    available = current_screen_specs() if screens is None else tuple(screens)
    geometry = safe_floating_geometry(
        state.get("floating_geometry"),
        saved_screen=state.get("floating_screen"),
        current_screens=available,
    )
    try:
        widget.setGeometry(QRect(*geometry))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    return geometry


class WorkspaceLayoutController:
    """Capture and apply presentation-only viewer layouts."""

    def __init__(self, workspace, *, settings=None, layout_menu=None, view_menu=None):
        self._workspace_ref = weakref.ref(workspace)
        self.store = WorkspaceLayoutStore(settings)
        self.layout_menu = layout_menu
        self.view_menu = view_menu
        self._default_state = self.capture_layout_state()
        win = self._window()
        sidebar = getattr(win, "_sharpmod_sidebar_dock", None) if win else None
        if isinstance(sidebar, QDockWidget):
            # install_analysis_workspace explicitly creates this tab group just
            # before installing us. Some Qt styles do not report a hidden tab
            # sibling through tabifiedDockWidgets until both docks are shown.
            self._default_state["tabified"] = True
        self._build_actions()

    def _workspace(self):
        return self._workspace_ref()

    def _window(self):
        workspace = self._workspace()
        return workspace._window() if workspace is not None else None

    def _notify(self, message):
        win = self._window()
        if win is None:
            return
        try:
            win.statusBar().showMessage(str(message), 5000)
        except (AttributeError, RuntimeError):
            pass

    def _warn(self, message):
        win = self._window()
        if win is not None:
            QMessageBox.warning(win, "Workspace layout", str(message))

    def _build_actions(self):
        workspace = self._workspace()
        dock = getattr(workspace, "dock", None) if workspace is not None else None
        parent = dock
        self.save_action = QAction("Save current layout…", parent)
        self.save_action.setToolTip(
            "Save dock visibility, placement, size, and safe floating-window geometry"
        )
        self.reset_action = QAction("Reset workspace layout", parent)
        self.reset_action.setToolTip(
            "Return to the original sounding panel and hidden analysis workspace"
        )
        self.remove_action = QAction("Remove saved layout…", parent)
        self.saved_menu = QMenu("Saved layouts", parent)
        self.saved_menu.setToolTipsVisible(True)

        controller_ref = weakref.ref(self)

        def save_requested(_checked=False):
            controller = controller_ref()
            if controller is not None:
                controller._prompt_save()

        def reset_requested(_checked=False):
            controller = controller_ref()
            if controller is not None:
                controller.reset_layout()

        def remove_requested(_checked=False):
            controller = controller_ref()
            if controller is not None:
                controller._prompt_remove()

        def rebuild():
            controller = controller_ref()
            if controller is not None:
                controller.rebuild_saved_menu()

        self.save_action.triggered.connect(save_requested)
        self.reset_action.triggered.connect(reset_requested)
        self.remove_action.triggered.connect(remove_requested)
        self.saved_menu.aboutToShow.connect(rebuild)

        if self.layout_menu is not None:
            self.layout_menu.addSeparator()
            self.layout_menu.addAction(self.save_action)
            self.layout_menu.addMenu(self.saved_menu)
            self.layout_menu.addAction(self.remove_action)
            self.layout_menu.addSeparator()
            self.layout_menu.addAction(self.reset_action)
        self.view_layout_menu = None
        if self.view_menu is not None:
            self.view_layout_menu = self.view_menu.addMenu("Workspace layouts")
            self.view_layout_menu.addAction(self.save_action)
            self.view_layout_menu.addMenu(self.saved_menu)
            self.view_layout_menu.addAction(self.remove_action)
            self.view_layout_menu.addSeparator()
            self.view_layout_menu.addAction(self.reset_action)
        self.rebuild_saved_menu()

    def layout_names(self):
        return self.store.names()

    def _sidebar_hidden_before_expand(self, sidebar):
        workspace = self._workspace()
        restore = getattr(workspace, "_expanded_restore", None)
        if not isinstance(restore, Mapping):
            return sidebar.isHidden()
        for other_ref, was_hidden in restore.get("siblings", ()):
            if other_ref() is sidebar:
                return bool(was_hidden)
        return sidebar.isHidden()

    def capture_layout_state(self):
        workspace = self._workspace()
        win = self._window()
        dock = getattr(workspace, "dock", None) if workspace is not None else None
        if workspace is None or win is None or dock is None:
            return _normal_layout_state({})
        sidebar = getattr(win, "_sharpmod_sidebar_dock", None)
        area = win.dockWidgetArea(dock)
        area_name = "left" if area == Qt.LeftDockWidgetArea else "right"
        mode = workspace.presentation_mode()
        restore = getattr(workspace, "_expanded_restore", None)
        dock_width = (
            restore.get("dock_width", dock.width())
            if isinstance(restore, Mapping)
            else dock.width()
        )
        state = {
            "mode": mode,
            "analysis_visible": not dock.isHidden(),
            "analysis_area": area_name,
            "analysis_width": dock_width,
            "sidebar_visible": bool(
                isinstance(sidebar, QDockWidget)
                and not self._sidebar_hidden_before_expand(sidebar)
            ),
            "sidebar_width": (
                sidebar.width() if isinstance(sidebar, QDockWidget) else 280
            ),
            "tabified": bool(
                isinstance(sidebar, QDockWidget)
                and sidebar in win.tabifiedDockWidgets(dock)
            ),
        }
        if dock.isFloating():
            state.update(capture_floating_placement(dock))
        return _normal_layout_state(state)

    def save_named_layout(self, name):
        saved = self.store.save(name, self.capture_layout_state())
        self.rebuild_saved_menu()
        self._notify(f"Saved workspace layout: {saved}")
        return saved

    def apply_named_layout(self, name):
        state = self.store.get(name)
        if state is None:
            return False
        self.apply_layout_state(state)
        self._notify(f"Restored workspace layout: {name}")
        return True

    def delete_named_layout(self, name):
        removed = self.store.delete(name)
        if removed:
            self.rebuild_saved_menu()
            self._notify(f"Removed workspace layout: {name}")
        return removed

    def reset_layout(self):
        self.apply_layout_state(self._default_state)
        self._notify("Reset workspace layout")

    def apply_layout_state(self, state):
        state = _normal_layout_state(state)
        workspace = self._workspace()
        win = self._window()
        dock = getattr(workspace, "dock", None) if workspace is not None else None
        if workspace is None or win is None or dock is None:
            return False
        sidebar = getattr(win, "_sharpmod_sidebar_dock", None)

        if getattr(workspace, "_expanded_restore", None) is not None:
            workspace._leave_expanded(show_dock=False)
        if dock.isFloating():
            dock.setFloating(False)

        area = (
            Qt.LeftDockWidgetArea
            if state["analysis_area"] == "left"
            else Qt.RightDockWidgetArea
        )
        win.addDockWidget(area, dock)
        if isinstance(sidebar, QDockWidget):
            sidebar.setVisible(state["sidebar_visible"])
            if state["tabified"] and win.dockWidgetArea(sidebar) == area:
                win.tabifyDockWidget(sidebar, dock)
            try:
                win.resizeDocks(
                    [sidebar], [state["sidebar_width"]], Qt.Horizontal
                )
            except (AttributeError, RuntimeError):
                pass

        try:
            win.resizeDocks(
                [dock], [state["analysis_width"]], Qt.Horizontal
            )
        except (AttributeError, RuntimeError):
            pass

        mode = state["mode"]
        was_hidden = dock.isHidden()
        if mode == "expanded":
            workspace.set_expanded(True)
        elif mode == "separate":
            if state["analysis_visible"]:
                workspace.set_separate_window(True)
            else:
                dock.setFloating(True)
                dock.hide()
                workspace._sync_presentation_actions()
            restore_floating_placement(dock, state)
        else:
            dock.setVisible(state["analysis_visible"])
            if state["analysis_visible"]:
                dock.raise_()
                if was_hidden:
                    workspace.refresh()
            workspace._sync_presentation_actions()
            workspace._apply_navigation_layout()
        return True

    def rebuild_saved_menu(self):
        self.saved_menu.clear()
        names = self.layout_names()
        if not names:
            empty = self.saved_menu.addAction("No saved layouts")
            empty.setEnabled(False)
            self.remove_action.setEnabled(False)
            return
        self.remove_action.setEnabled(True)
        controller_ref = weakref.ref(self)
        for name in names:
            action = self.saved_menu.addAction(name)
            action.setToolTip(f"Restore workspace layout: {name}")

            def apply_saved(_checked=False, *, saved_name=name):
                controller = controller_ref()
                if controller is not None:
                    controller.apply_named_layout(saved_name)

            action.triggered.connect(apply_saved)

    def _prompt_save(self):
        win = self._window()
        if win is None:
            return
        name, accepted = QInputDialog.getText(
            win,
            "Save workspace layout",
            "Layout name (saving the same name updates it):",
        )
        if not accepted:
            return
        try:
            self.save_named_layout(name)
        except (LayoutFormatError, ValueError) as exc:
            self._warn(exc)

    def _prompt_remove(self):
        win = self._window()
        names = self.layout_names()
        if win is None or not names:
            return
        name, accepted = QInputDialog.getItem(
            win,
            "Remove workspace layout",
            "Saved layout:",
            list(names),
            0,
            False,
        )
        if accepted:
            try:
                self.delete_named_layout(name)
            except LayoutFormatError as exc:
                self._warn(exc)


def install_workspace_layouts(
    workspace, *, settings=None, layout_menu=None, view_menu=None
):
    """Install the named-layout controller once for an analysis workspace."""
    existing = getattr(workspace, "layout_controller", None)
    if existing is not None:
        return existing
    controller = WorkspaceLayoutController(
        workspace,
        settings=settings,
        layout_menu=layout_menu,
        view_menu=view_menu,
    )
    workspace.layout_controller = controller
    return controller


__all__ = [
    "LAYOUT_FORMAT",
    "LAYOUT_SETTINGS_KEY",
    "LAYOUT_VERSION",
    "LayoutFormatError",
    "WorkspaceLayoutController",
    "WorkspaceLayoutStore",
    "capture_floating_placement",
    "current_screen_specs",
    "install_workspace_layouts",
    "restore_floating_placement",
    "safe_floating_geometry",
]
