"""Expanded sounding-locator controls and export integration (T25).

The live hodograph locator remains the rendering authority.  This module adds
an explicit presentation controller, a read-only enlarged canvas, and a PNG
action around that renderer; it does not create a second selectable map.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
import weakref

from qtpy.QtCore import QObject, Qt, QTimer
from qtpy.QtGui import QAction, QPainter
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sharpmod.maps import locator_presentation
from sharpmod.viz import hodo_locator


_LOGGER = logging.getLogger(__name__)


def _current_collection(win):
    try:
        widget = win.spc_widget
        return widget.prof_collections[int(widget.pc_idx)]
    except (AttributeError, IndexError, RuntimeError, TypeError, ValueError):
        return None


def _source_widget(win):
    """Return the real hodograph carrying the focused collection and palette."""

    try:
        widget = win.spc_widget.hodo
        if hasattr(widget, "prof_collections"):
            return widget
    except (AttributeError, RuntimeError):
        pass
    try:
        from sharppy.viz.hodo import plotHodo

        matches = win.findChildren(plotHodo)
    except Exception:  # noqa: BLE001 - optional dialog must degrade safely
        matches = ()
    return matches[0] if matches else None


def _main_map_context(controller) -> tuple[
        locator_presentation.Bounds | None,
        locator_presentation.Bounds | None,
        str,
        object | None,
]:
    """Read the picker map currently in front without constructing a lazy tab."""

    getter = getattr(controller, "_active_map_widget", None)
    try:
        widget = getter() if callable(getter) else None
    except (AttributeError, RuntimeError):
        widget = None
    if widget is None:
        return None, None, "metric", None
    try:
        view = locator_presentation.bounds_from_map_view(widget.view_bounds())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        view = None
    try:
        box = locator_presentation.area_from_map_box(widget.box())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        box = None
    units = "imperial" if getattr(widget, "_scale_units", "metric") == "imperial" \
        else "metric"
    return view, box, units, widget


def _collection_context_update(collection, controller, *, clear_area=False):
    current = locator_presentation.collection_presentation(collection)
    view, area, units, _widget = _main_map_context(controller)
    updated = current.updated(
        main_bounds=view,
        # A freshly restored analysis session can open before the picker has
        # recreated its selection box.  Preserve that persisted box until the
        # live map either supplies a replacement or explicitly emits
        # ``boxCleared``.  Tab changes deliberately pass ``clear_area=True``
        # because the newly active map is then the source of truth.
        selected_area=(area if area is not None or clear_area
                       else current.selected_area),
        scale_units=units,
    )
    if updated != current:
        locator_presentation.set_collection_presentation(collection, updated)
    return updated


def _repaint_locator(win) -> None:
    try:
        from sharpmod.gui_viewer import _repaint_locator_insets

        _repaint_locator_insets(win)
    except Exception:  # noqa: BLE001 - expanded view still refreshes independently
        pass
    dialog = getattr(win, "_sharpmod_locator_dialog", None)
    if dialog is not None:
        try:
            dialog.refresh()
        except (AttributeError, RuntimeError):
            pass


class LocatorBinding(QObject):
    """Keep followed map bounds current while preserving pinned choices."""

    def __init__(self, win, controller):
        super().__init__(win)
        self._win_ref = weakref.ref(win)
        try:
            self._controller_ref = weakref.ref(controller)
        except TypeError:
            self._controller_ref = lambda: controller
        self._connected_widgets: set[int] = set()
        self._connected_tabs = False
        self._connect_tabs()
        self.sync()

    def _connect_tabs(self) -> None:
        controller = self._controller_ref()
        tabs = getattr(controller, "_tabs", None) if controller is not None else None
        if tabs is None or self._connected_tabs:
            return
        try:
            tabs.currentChanged.connect(self._tab_changed)
            self._connected_tabs = True
        except (AttributeError, RuntimeError):
            pass

    def _connect_widget(self, widget) -> None:
        if widget is None or id(widget) in self._connected_widgets:
            return
        connected = False
        for signal_name, callback in (
                ("viewSettled", self._context_changed),
                ("boxSelected", self._context_changed),
                ("boxCleared", self._box_cleared)):
            signal = getattr(widget, signal_name, None)
            if signal is None:
                continue
            try:
                signal.connect(callback)
                connected = True
            except (AttributeError, RuntimeError, TypeError):
                continue
        if connected:
            self._connected_widgets.add(id(widget))

    def _context_changed(self, *_args) -> None:
        self.sync()

    def _box_cleared(self, *_args) -> None:
        self.sync(clear_area=True)

    def _tab_changed(self, *_args) -> None:
        self.sync(clear_area=True)

    def collections(self) -> tuple:
        win = self._win_ref()
        if win is None:
            return ()
        try:
            return tuple(win.spc_widget.prof_collections)
        except (AttributeError, RuntimeError, TypeError):
            return ()

    def add_collection(self, collection) -> None:
        controller = self._controller_ref()
        if collection is not None and controller is not None:
            _collection_context_update(collection, controller)
        self.sync()

    def sync(self, *, clear_area=False) -> None:
        win = self._win_ref()
        controller = self._controller_ref()
        if win is None or controller is None:
            return
        self._connect_tabs()
        _view, _area, _units, widget = _main_map_context(controller)
        self._connect_widget(widget)
        changed = False
        for collection in self.collections():
            before = locator_presentation.collection_presentation(collection)
            after = _collection_context_update(
                collection, controller, clear_area=clear_area)
            changed = changed or before != after
        if changed:
            _repaint_locator(win)


class LocatorCanvas(QWidget):
    """Read-only enlarged view rendered by the hodograph locator path."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self._win_ref = weakref.ref(win)
        # Leave enough vertical give for 200% text scaling.  The canvas still
        # expands to consume the dialog, while its minimum no longer forces the
        # action row over the painted attribution/status footer.
        self.setMinimumSize(480, 250)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAccessibleName("Expanded sounding locator map")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        del event
        painter = QPainter(self)
        try:
            win = self._win_ref()
            source = _source_widget(win) if win is not None else None
            if source is None:
                painter.drawText(self.rect(), Qt.AlignCenter, "Locator unavailable")
                return
            pixmap = hodo_locator.render_locator_pixmap(
                source, self.width(), self.height(), expanded=True)
            painter.drawPixmap(0, 0, pixmap)
            presentation = hodo_locator.presentation_from_widget(source)
            contexts = hodo_locator.overlay_context_for_widget(source)
            overlay_text = "; ".join(
                f"{'Hazard ' if item['hazard'] else ''}{item['title']}: "
                f"{item['status']}; "
                f"{hodo_locator.overlay_timing_text(item) or 'time unavailable'}; "
                f"{item['source']}"
                for item in contexts
            ) or "no locator overlays"
            sampled = hodo_locator.point_from_widget(source)
            requested = hodo_locator.requested_point_from_widget(source)
            point_text = (
                f" sampled {sampled[0]:.3f}, {sampled[1]:.3f};"
                if sampled is not None else ""
            ) + (
                f" requested {requested[0]:.3f}, {requested[1]:.3f};"
                if requested is not None else ""
            )
            location = hodo_locator.location_name_from_widget(source)
            self.setAccessibleDescription(
                f"{location}; {presentation.summary()};{point_text} "
                f"{overlay_text}. Read-only inspection; "
                "clicking this view does not change the sounding selection."
            )
            self.setToolTip(self.accessibleDescription())
        finally:
            painter.end()


def _make_bound_spin(*, latitude: bool, name: str) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(-90.0 if latitude else -540.0, 90.0 if latitude else 540.0)
    spin.setDecimals(3)
    spin.setSingleStep(0.25)
    spin.setSuffix("°")
    spin.setAccessibleName(name)
    return spin


class LocatorDialog(QDialog):
    """Explicit follow/pin and extent controls around a read-only locator."""

    def __init__(self, win, controller, parent=None):
        super().__init__(parent or win)
        self._win_ref = weakref.ref(win)
        try:
            self._controller_ref = weakref.ref(controller)
        except TypeError:
            self._controller_ref = lambda: controller
        self._loading = False
        self._collection_identity = None
        self.setWindowTitle("Sounding Locator")
        self.setModal(False)
        self.resize(920, 700)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Inspect geographic context without moving or loading a sounding. "
            "Follow mirrors the map currently in front; Pinned keeps its own extent."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        choices = QGroupBox("Locator state")
        choice_layout = QFormLayout(choices)
        self.link_combo = QComboBox()
        for key in locator_presentation.LINK_MODES:
            self.link_combo.addItem(locator_presentation.LINK_LABELS[key], key)
        self.link_combo.setAccessibleName("Locator link mode")
        self.extent_combo = QComboBox()
        for key in locator_presentation.EXTENT_MODES:
            self.extent_combo.addItem(locator_presentation.EXTENT_LABELS[key], key)
        self.extent_combo.setAccessibleName("Pinned locator extent")
        choice_layout.addRow("Relationship:", self.link_combo)
        choice_layout.addRow("Pinned extent:", self.extent_combo)
        root.addWidget(choices)

        custom = QGroupBox("Custom extent")
        custom_layout = QGridLayout(custom)
        self.west = _make_bound_spin(latitude=False, name="Custom west longitude")
        self.east = _make_bound_spin(latitude=False, name="Custom east longitude")
        self.south = _make_bound_spin(latitude=True, name="Custom south latitude")
        self.north = _make_bound_spin(latitude=True, name="Custom north latitude")
        custom_layout.addWidget(QLabel("West:"), 0, 0)
        custom_layout.addWidget(self.west, 0, 1)
        custom_layout.addWidget(QLabel("East:"), 0, 2)
        custom_layout.addWidget(self.east, 0, 3)
        custom_layout.addWidget(QLabel("South:"), 1, 0)
        custom_layout.addWidget(self.south, 1, 1)
        custom_layout.addWidget(QLabel("North:"), 1, 2)
        custom_layout.addWidget(self.north, 1, 3)
        self.apply_custom = QPushButton("Apply custom extent")
        self.use_main = QPushButton("Use current main-map view")
        custom_layout.addWidget(self.apply_custom, 2, 0, 1, 2)
        custom_layout.addWidget(self.use_main, 2, 2, 1, 2)
        root.addWidget(custom)
        self.custom_group = custom

        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        self.state_label.setAccessibleName("Locator presentation summary")
        root.addWidget(self.state_label)
        self.canvas = LocatorCanvas(win, self)
        root.addWidget(self.canvas, 1)
        self.overlay_label = QLabel()
        self.overlay_label.setWordWrap(True)
        self.overlay_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.overlay_label.setAccessibleName("Locator overlay source and status")
        root.addWidget(self.overlay_label)
        # The painted footer and canvas description/tooltip provide the visible
        # and accessible context. A second large-text paragraph below the map
        # duplicated it and, at 200% text, consumed the action row; this label
        # stays as a programmatic mirror for dialog state/tests only.
        self.overlay_label.hide()

        actions = QHBoxLayout()
        self.export_button = QPushButton("Export locator PNG…")
        self.close_button = QPushButton("Close")
        actions.addWidget(self.export_button)
        actions.addStretch(1)
        actions.addWidget(self.close_button)
        root.addLayout(actions)

        self.link_combo.currentIndexChanged.connect(self._link_changed)
        self.extent_combo.currentIndexChanged.connect(self._extent_changed)
        self.apply_custom.clicked.connect(self._apply_custom)
        self.use_main.clicked.connect(self._use_main)
        self.export_button.clicked.connect(self._export)
        self.close_button.clicked.connect(self.close)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(300)
        self._refresh_timer.timeout.connect(self._refresh_if_focus_changed)
        self._refresh_timer.start()
        self.refresh(force=True)

    def _collection(self):
        win = self._win_ref()
        return _current_collection(win) if win is not None else None

    def _presentation(self):
        collection = self._collection()
        return locator_presentation.collection_presentation(collection) \
            if collection is not None else locator_presentation.LocatorPresentation()

    @staticmethod
    def _set_combo(combo, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _effective_bounds(self, presentation):
        win = self._win_ref()
        source = _source_widget(win) if win is not None else None
        point = hodo_locator.point_from_widget(source) if source is not None else None
        area = hodo_locator.box_from_widget(source) if source is not None else None
        if point is None:
            return presentation.custom_bounds or presentation.main_bounds
        return presentation.effective_bounds(point, represented_area=area)

    def refresh(self, *, force=False) -> None:
        collection = self._collection()
        identity = id(collection) if collection is not None else None
        if force or identity != self._collection_identity:
            self._collection_identity = identity
            self._loading = True
            try:
                presentation = self._presentation()
                self._set_combo(self.link_combo, presentation.link_mode)
                self._set_combo(self.extent_combo, presentation.extent_mode)
                bounds = presentation.custom_bounds or self._effective_bounds(presentation)
                if bounds is not None:
                    west, south, east, north = bounds
                    self.west.setValue(west)
                    self.south.setValue(south)
                    self.east.setValue(east)
                    self.north.setValue(north)
            finally:
                self._loading = False
        presentation = self._presentation()
        pinned = presentation.link_mode == locator_presentation.LINK_PINNED
        self.extent_combo.setEnabled(pinned)
        custom_active = pinned and presentation.extent_mode == \
            locator_presentation.EXTENT_CUSTOM
        # The longitude/latitude editor is relevant only for Custom.  Hiding
        # it for the other modes keeps the expanded inspector usable with
        # large system text while retaining the selected custom values.
        self.custom_group.setVisible(custom_active)
        for control in (self.west, self.east, self.south, self.north,
                        self.apply_custom, self.use_main):
            control.setEnabled(custom_active)
        bounds = self._effective_bounds(presentation)
        bounds_text = (
            f"longitude {bounds[0]:.3f}° to {bounds[2]:.3f}°; "
            f"latitude {bounds[1]:.3f}° to {bounds[3]:.3f}°"
            if bounds is not None else "extent unavailable"
        )
        self.state_label.setText(
            f"{presentation.summary()} · {bounds_text}. "
            "The selected sounding remains unchanged."
        )
        win = self._win_ref()
        source = _source_widget(win) if win is not None else None
        contexts = hodo_locator.overlay_context_for_widget(source) \
            if source is not None else ()
        if contexts:
            lines = []
            for item in contexts:
                timing_text = hodo_locator.overlay_timing_text(item)
                timing = f" · {timing_text}" if timing_text else ""
                hazard = "Hazard · " if item.get("hazard") else ""
                lines.append(
                    f"{hazard}{item.get('title')} — {item.get('status')}"
                    f"{timing} · {item.get('source')}"
                )
            self.overlay_label.setText("\n".join(lines))
        else:
            self.overlay_label.setText(
                "Overlays — none selected or available · Geography — "
                "U.S. Census Bureau boundaries"
            )
        self.canvas.update()

    def _refresh_if_focus_changed(self) -> None:
        collection = self._collection()
        if id(collection) != self._collection_identity:
            self.refresh(force=True)
        else:
            self.canvas.update()

    def _save(self, presentation) -> None:
        collection = self._collection()
        if collection is None:
            return
        locator_presentation.set_collection_presentation(collection, presentation)
        win = self._win_ref()
        if win is not None:
            _repaint_locator(win)
        self.refresh(force=True)

    def _link_changed(self) -> None:
        if self._loading:
            return
        wanted = self.link_combo.currentData()
        presentation = self._presentation().updated(link_mode=wanted)
        if wanted == locator_presentation.LINK_FOLLOW:
            controller = self._controller_ref()
            collection = self._collection()
            if controller is not None and collection is not None:
                presentation = _collection_context_update(collection, controller).updated(
                    link_mode=wanted)
        self._save(presentation)

    def _extent_changed(self) -> None:
        if self._loading:
            return
        wanted = self.extent_combo.currentData()
        presentation = self._presentation()
        changes = {
            "link_mode": locator_presentation.LINK_PINNED,
            "extent_mode": wanted,
        }
        if wanted == locator_presentation.EXTENT_CUSTOM \
                and presentation.custom_bounds is None:
            changes["custom_bounds"] = self._effective_bounds(presentation)
        self._save(presentation.updated(**changes))

    def _apply_custom(self) -> None:
        bounds = locator_presentation.normalise_bounds((
            self.west.value(), self.south.value(),
            self.east.value(), self.north.value(),
        ))
        if bounds is None:
            QMessageBox.warning(
                self, "Sounding Locator",
                "Custom bounds must have increasing latitude and a usable "
                "longitude span. West may exceed east only to cross the dateline.",
            )
            return
        self._save(self._presentation().updated(
            link_mode=locator_presentation.LINK_PINNED,
            extent_mode=locator_presentation.EXTENT_CUSTOM,
            custom_bounds=bounds,
        ))

    def _use_main(self) -> None:
        controller = self._controller_ref()
        view, _area, _units, _widget = _main_map_context(controller)
        if view is None:
            QMessageBox.information(
                self, "Sounding Locator", "No main-map view is currently available."
            )
            return
        self._save(self._presentation().updated(
            link_mode=locator_presentation.LINK_PINNED,
            extent_mode=locator_presentation.EXTENT_CUSTOM,
            custom_bounds=view,
        ))

    def _export(self) -> None:
        win = self._win_ref()
        controller = self._controller_ref()
        if win is not None:
            export_locator_with_dialog(win, controller)


def _safe_location_name(source) -> str:
    label = hodo_locator.location_name_from_widget(source) if source is not None else ""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "sounding"


def save_locator_png(win, path, *, width=1400, height=900) -> bool:
    """Write the current locator presentation without changing live geometry."""

    source = _source_widget(win)
    if source is None:
        return False
    pixmap = hodo_locator.render_locator_pixmap(
        source, width=width, height=height, expanded=True)
    target = Path(path)
    if target.suffix.lower() != ".png":
        target = target.with_suffix(".png")
    from sharpmod.rendering.cli import save_pixmap_png_atomic

    return save_pixmap_png_atomic(pixmap, str(target))


def export_locator_with_dialog(win, controller) -> None:
    source = _source_widget(win)
    if source is None:
        QMessageBox.warning(win, "Sounding Locator", "No locator is available to export.")
        return
    settings = getattr(controller, "_settings", None)
    try:
        from sharpmod.ui.features.export_presentation import export_identity
        from sharpmod.ui.maps.export import capture_locator, export_map_figure

        frame = capture_locator(source)
        collection = _current_collection(win)
        identity = export_identity(collection)
        export_map_figure(
            win, (frame,), settings=settings, namespace="map-locator",
            identity=identity, kind="locator-map", title=frame.title,
            status=lambda message: win.statusBar().showMessage(message, 8000),
        )
    except Exception as exc:  # noqa: BLE001 - export UI boundary
        QMessageBox.warning(win, "Sounding Locator", f"Could not capture locator: {exc}")


def _find_menu(win, title: str):
    wanted = title.replace("&", "").casefold()
    try:
        menus = win.menuBar().findChildren(QMenu)
    except (AttributeError, RuntimeError):
        return None
    for menu in menus:
        if menu.title().replace("&", "").casefold() == wanted:
            return menu
    return None


def install_locator_tools(win, controller) -> LocatorBinding:
    """Install one locator binding, expanded action, and standalone export."""

    existing = getattr(win, "_sharpmod_locator_binding", None)
    if existing is not None:
        return existing
    binding = LocatorBinding(win, controller)
    win._sharpmod_locator_binding = binding
    win._sharpmod_add_locator_collection = binding.add_collection

    win_ref = weakref.ref(win)
    try:
        controller_ref = weakref.ref(controller)
    except TypeError:
        controller_ref = lambda: controller

    def show_dialog() -> None:
        target = win_ref()
        owner = controller_ref()
        if target is None or owner is None:
            return
        dialog = getattr(target, "_sharpmod_locator_dialog", None)
        if dialog is None:
            dialog = LocatorDialog(target, owner, parent=target)
            target._sharpmod_locator_dialog = dialog
        dialog.refresh(force=True)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def export_dialog() -> None:
        target = win_ref()
        owner = controller_ref()
        if target is not None and owner is not None:
            export_locator_with_dialog(target, owner)

    view_menu = _find_menu(win, "View")
    if view_menu is None:
        view_menu = win.menuBar().addMenu("View")
    inspect_action = QAction("Inspect Sounding Locator…", win)
    inspect_action.setToolTip(
        "Open a read-only enlarged locator; it cannot move the sounding point."
    )
    inspect_action.triggered.connect(show_dialog)
    view_menu.addAction(inspect_action)

    export_menu = _find_menu(win, "Export")
    if export_menu is None:
        export_menu = win.menuBar().addMenu("Export")
    export_action = QAction("Export Locator Image…", win)
    export_action.triggered.connect(export_dialog)
    export_menu.insertAction(export_menu.actions()[0] if export_menu.actions() else None,
                             export_action)

    win._sharpmod_locator_action = inspect_action
    win._sharpmod_locator_export_action = export_action
    win._sharpmod_show_locator = show_dialog
    return binding


__all__ = [
    "LocatorBinding",
    "LocatorCanvas",
    "LocatorDialog",
    "export_locator_with_dialog",
    "install_locator_tools",
    "save_locator_png",
]
